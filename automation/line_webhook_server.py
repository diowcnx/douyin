#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE OA Webhook Listener
Receives incoming messages from user in LINE chat.
Replies immediately with confirmation, then processes video in background.
"""

import os
import re
import json
import asyncio
import threading
import subprocess
from pathlib import Path
from aiohttp import web
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
VENV_PYTHON = str(BASE_DIR / "venv/bin/python3")

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
        requests.post(url, headers=headers, json=payload, timeout=5)
    except Exception as e:
        print(f"[-] Error replying to LINE: {e}")

def process_product_in_background(url: str):
    """Background worker: scrapes, downloads, analyzes, edits, pushes video to LINE"""
    print(f"[*] Starting background processing for: {url}")
    try:
        cmd = [VENV_PYTHON, str(BASE_DIR / "auto_pipeline.py"), "--url", url]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
        print(f"[+] Pipeline finished with code {res.returncode}")
    except Exception as e:
        print(f"[-] Background error: {e}")

async def webhook_handler(request):
    try:
        body = await request.text()
        data = json.loads(body)
        events = data.get("events", [])

        for event in events:
            if event.get("type") == "message":
                msg = event.get("message", {})
                reply_token = event.get("replyToken", "")
                user_id = event.get("source", {}).get("userId", "")

                if msg.get("type") == "text":
                    text = msg.get("text", "").strip()
                    urls = re.findall(r'https?://[^\s]+', text)

                    if urls:
                        target_url = urls[0]
                        # 1. Reply immediately to LINE chat
                        confirm_text = (
                            "🤖 ได้รับลิงก์สินค้าแล้วค่ะ!\n"
                            "ระบบกำลังวิเคราะห์ ค้นหาคลิป Douyin และตัดต่อวิดีโอ...\n"
                            "(ใช้เวลาประมาณ 1-2 นาที เมื่อเสร็จแล้วจะส่งคลิปวิดีโอพร้อมแคปชั่นและแฮชแท็กมาให้นะคะ 🎬✨)"
                        )
                        reply_line_message(reply_token, confirm_text)

                        # 2. Launch background worker
                        t = threading.Thread(target=process_product_in_background, args=(target_url,), daemon=True)
                        t.start()
                    else:
                        reply_line_message(
                            reply_token,
                            "สวัสดีค่ะ! กรุณาส่งลิงก์สินค้าจาก TikTok หรือ Douyin เพื่อให้ระบบเริ่มค้นหาและตัดต่อวิดีโอให้อัตโนมัติค่ะ 🛒"
                        )

        return web.Response(text="OK", status=200)
    except Exception as e:
        print(f"[-] Webhook Exception: {e}")
        return web.Response(text="Error", status=500)

async def init_app():
    app = web.Application()
    app.router.add_post("/line-webhook", webhook_handler)
    app.router.add_get("/line-webhook", lambda r: web.Response(text="OK")); app.router.add_get("/", lambda r: web.Response(text="Douyin-LINE Automation Server OK"))
    return app

if __name__ == "__main__":
    print("Starting LINE OA Webhook Server on port 8088...")
    web.run_app(init_app(), host="0.0.0.0", port=8088)
