#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE OA Webhook Listener
Receives incoming messages from user in LINE chat.
Replies immediately with confirmation, then processes video in background.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

try:
    from .auto_pipeline import is_douyin_video_url
except ImportError:  # Deployed automation scripts also run as standalone files.
    from auto_pipeline import is_douyin_video_url

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
VENV_PYTHON = str(BASE_DIR / "venv/bin/python3")
OUTPUT_DIR = BASE_DIR / "output"
BATCH_IDLE_SECONDS = 30
MAX_BATCH_LINKS = 10

_pending_lock = threading.Lock()
_pending_urls = []
_pending_timer = None
_pending_generation = 0


def reply_line_message(reply_token: str, text: str):
    """Reply immediately to user's message using replyToken"""
    import requests
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[-] LINE reply rejected ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[-] Error replying to LINE: {e}")


def process_video_in_background(urls):
    """Background worker: download every link and send one montage to LINE."""
    print(f"[*] Starting background processing for {len(urls)} Douyin links")
    try:
        cmd = [VENV_PYTHON, str(BASE_DIR / "auto_pipeline.py")]
        for url in urls:
            cmd.extend(["--url", url])
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=600 + 180 * len(urls),
        )
        print(f"[*] Pipeline stdout:\n{res.stdout[-6000:]}")
        if res.stderr:
            print(f"[*] Pipeline stderr:\n{res.stderr[-6000:]}")
        print(f"[*] Pipeline finished with code {res.returncode}")
    except subprocess.TimeoutExpired:
        print("[-] Background processing timed out")
        from line_notifier import send_line_text

        send_line_text("❌ งานรวมคลิปใช้เวลานานเกินกำหนด กรุณาแบ่งลิงก์ส่งเป็นชุดเล็กลง")
    except Exception as e:
        print(f"[-] Background error: {e}")
        from line_notifier import send_line_text

        send_line_text("❌ ระบบรวมคลิปเกิดข้อผิดพลาด กรุณาลองส่งลิงก์อีกครั้ง")


def dispatch_pending_videos(generation=None):
    """Start one job containing all links received before the idle deadline."""
    global _pending_timer
    with _pending_lock:
        if generation is not None and generation != _pending_generation:
            return 0
        urls = list(_pending_urls)
        _pending_urls.clear()
        if _pending_timer is not None:
            _pending_timer.cancel()
            _pending_timer = None
    if not urls:
        return 0
    worker = threading.Thread(target=process_video_in_background, args=(urls,), daemon=True)
    worker.start()
    return len(urls)


def queue_video_urls(urls):
    """Collect links from one or more LINE messages into a single edit job."""
    global _pending_generation, _pending_timer
    with _pending_lock:
        if len(_pending_urls) + len(urls) > MAX_BATCH_LINKS:
            return None
        _pending_urls.extend(urls)
        _pending_generation += 1
        if _pending_timer is not None:
            _pending_timer.cancel()
        timer = threading.Timer(
            BATCH_IDLE_SECONDS, dispatch_pending_videos, args=(_pending_generation,)
        )
        timer.daemon = True
        _pending_timer = timer
        timer.start()
        return len(_pending_urls)


async def webhook_handler(request):
    try:
        body_bytes = await request.read()
        if LINE_CHANNEL_SECRET:
            supplied = request.headers.get("X-Line-Signature", "")
            expected = base64.b64encode(
                hmac.new(LINE_CHANNEL_SECRET.encode(), body_bytes, hashlib.sha256).digest()
            ).decode()
            if not hmac.compare_digest(supplied, expected):
                return web.Response(text="Invalid signature", status=401)
        else:
            print("[!] LINE_CHANNEL_SECRET is not configured; signature validation is disabled")
        body = body_bytes.decode("utf-8")
        data = json.loads(body)
        events = data.get("events", [])

        for event in events:
            if event.get("type") == "message":
                msg = event.get("message", {})
                reply_token = event.get("replyToken", "")
                user_id = event.get("source", {}).get("userId", "")
                if LINE_USER_ID and user_id != LINE_USER_ID:
                    print("[!] Ignoring event from an unexpected LINE user")
                    continue

                if msg.get("type") == "text":
                    text = msg.get("text", "").strip()
                    if text.lower() in {"เริ่ม", "start", "done"}:
                        count = dispatch_pending_videos()
                        message = (
                            "🎬 เริ่มดาวน์โหลดและรวมคลิป Douyin {} ลิงก์แล้วค่ะ".format(count)
                            if count else "ยังไม่มีลิงก์คลิปค้างอยู่ค่ะ กรุณาส่งลิงก์ Douyin ก่อน"
                        )
                        reply_line_message(reply_token, message)
                        continue
                    urls = re.findall(r'https?://[^\s]+', text)

                    if urls:
                        target_urls = [url.rstrip(".,)]}>\"'") for url in urls]
                        if len(target_urls) > MAX_BATCH_LINKS:
                            reply_line_message(
                                reply_token,
                                "ส่งได้สูงสุด 10 ลิงก์ต่อหนึ่งงานค่ะ กรุณาแบ่งเป็นหลายชุด",
                            )
                            continue
                        invalid = [
                            index for index, url in enumerate(target_urls, start=1)
                            if not is_douyin_video_url(url)
                        ]
                        if invalid:
                            reply_line_message(
                                reply_token,
                                "ลิงก์ลำดับที่ {} ไม่ใช่คลิป Douyin ค่ะ กรุณาส่งลิงก์วิดีโอโดยตรง "
                                "ระบบไม่สร้างวิดีโอจากภาพสินค้า".format(
                                    ", ".join(str(index) for index in invalid)
                                ),
                            )
                            continue
                        count = queue_video_urls(target_urls)
                        if count is None:
                            reply_line_message(
                                reply_token,
                                "ชุดนี้ครบ 10 ลิงก์แล้วค่ะ พิมพ์ “เริ่ม” เพื่อรวมคลิปชุดนี้ "
                                "แล้วส่งชุดถัดไป",
                            )
                            continue
                        reply_line_message(
                            reply_token,
                            f"🎬 รับคลิป Douyin แล้ว ตอนนี้มี {count} ลิงก์ในชุดนี้ค่ะ\n"
                            "ส่งเพิ่มได้ภายใน 30 วินาที หรือพิมพ์ “เริ่ม” เพื่อดาวน์โหลดทุกคลิป "
                            "และรวมเป็นวิดีโอเดียวทันที",
                        )
                    else:
                        reply_line_message(
                            reply_token,
                            "สวัสดีค่ะ กรุณาส่งลิงก์คลิป Douyin โดยตรง เช่น "
                            "https://www.douyin.com/video/... หรือ v.douyin.com ค่ะ 🎬"
                        )

        return web.Response(text="OK", status=200)
    except Exception as e:
        print(f"[-] Webhook Exception: {e}")
        return web.Response(text="Error", status=500)


async def media_handler(request):
    """Serve only completed MP4/JPEG files from the pipeline output directory."""
    filename = Path(request.match_info["filename"]).name
    if filename != request.match_info["filename"]:
        raise web.HTTPNotFound()
    path = OUTPUT_DIR / filename
    if path.suffix.lower() not in {".mp4", ".jpg", ".jpeg"} or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def init_app():
    app = web.Application()
    app.router.add_post("/line-webhook", webhook_handler)
    app.router.add_get("/line-webhook", lambda r: web.Response(text="OK"))
    app.router.add_get("/line-webhook/media/{filename}", media_handler)
    app.router.add_get("/", lambda r: web.Response(text="Douyin-LINE Automation Server OK"))
    return app

if __name__ == "__main__":
    print("Starting LINE OA Webhook Server on port 8088...")
    web.run_app(init_app(), host="0.0.0.0", port=8088)
