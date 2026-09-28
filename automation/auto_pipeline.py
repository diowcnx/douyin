#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Edit a real Douyin clip and deliver the resulting video to LINE."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
PHAYA_API_KEY = os.getenv("PHAYA_API_KEY", "")
FONT_DIR = os.getenv("FONT_DIR", "/home/diowcnx/font")
FFMPEG_BIN = os.getenv("FFMPEG_PATH", "/home/diowcnx/bin/ffmpeg")
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "https://fms-a.threegeneration.org/line-webhook"
).rstrip("/")
DOWNLOADER_DIR = Path(os.getenv("DOUYIN_DOWNLOADER_DIR", str(BASE_DIR / "douyin-dl")))
DOWNLOADER_CONFIG = Path(
    os.getenv("DOUYIN_CONFIG", str(DOWNLOADER_DIR / "config.yml"))
)
DOWNLOADER_PYTHON = Path(
    os.getenv("DOUYIN_PYTHON", str(BASE_DIR / "venv/bin/python3"))
)
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)


class PipelineError(RuntimeError):
    """An expected pipeline failure that is safe to report to the LINE user."""


class DouyinLoginRequired(PipelineError):
    """The downloader cannot continue because its Douyin session has expired."""


def run_ffmpeg(cmd: List[str], timeout: int = 180) -> bool:
    """Run ffmpeg at low priority and with a small thread budget."""
    full_cmd = ["nice", "-n", "10", FFMPEG_BIN, "-threads", "2", "-y"] + cmd
    result = subprocess.run(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        print("[FFmpeg Error] {}".format(result.stderr[-1200:]))
    return result.returncode == 0


def generate_voiceover(text: str, output_mp3: Path) -> bool:
    """Generate Thai speech using gTTS."""
    from gtts import gTTS

    print("[*] Generating voiceover: {!r}".format(text[:60]))
    gTTS(text=text, lang="th").save(str(output_mp3))
    return output_mp3.exists() and output_mp3.stat().st_size > 0


def escape_ass_text(text: str) -> str:
    """Escape user/generated text for an ASS dialogue field."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def build_ass_subtitles(dialogues: List[Dict[str, Any]], output_ass_file: Path) -> None:
    """Build large yellow captions with an opaque black background sized to the text."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Prompt,96,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,2,60,60,270,1
Style: Mask,Prompt,82,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for dialogue in dialogues:
        region = dialogue.get("mask_region")
        style = "Mask" if region else "Default"
        content = escape_ass_text(str(dialogue["text"]))
        if region:
            x, y, width, height = region
            size, fitted_text = _fit_caption_layout(str(dialogue["text"]), width, height)
            content = "{{\\an5\\pos({},{})\\fs{}}}{}".format(
                x + width // 2, y + height // 2, size, escape_ass_text(fitted_text)
            )
        lines.append(
            "Dialogue: 0,{},{},{},,0,0,0,,{}".format(
                format_timestamp(float(dialogue["start"])),
                format_timestamp(float(dialogue["end"])),
                style, content,
            )
        )
    output_ass_file.write_text("\n".join(lines), encoding="utf-8")


def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60
    return "{}:{:02d}:{:05.2f}".format(hours, minutes, remaining)


def _open_without_redirect(url: str, timeout: int = 20):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            return exc
        raise


def extract_tiktok_product(url: str) -> Dict[str, str]:
    """Read the product title/image carried in TikTok Shop's first redirect."""
    try:
        response = _open_without_redirect(url)
        location = response.headers.get("Location", "")
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        raw_info = params.get("og_info", [""])[0]
        info = json.loads(raw_info) if raw_info else {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError("อ่านข้อมูลสินค้าจาก TikTok Shop ไม่สำเร็จ") from exc

    title = str(info.get("title") or "").strip()
    if not title:
        raise PipelineError("ลิงก์นี้ไม่มีชื่อสินค้าให้ระบบนำไปค้นหาคลิป")
    return {"title": title, "image": str(info.get("image") or "")}


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise PipelineError("Gemini ไม่ได้ส่งผลวิเคราะห์กลับมาในรูปแบบที่ใช้ได้")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PipelineError("อ่านผลวิเคราะห์จาก Gemini ไม่สำเร็จ") from exc
    if not isinstance(value, dict):
        raise PipelineError("ผลวิเคราะห์จาก Gemini ไม่ถูกต้อง")
    return value


def _analysis_prompt(product_title: str) -> str:
    return """คุณเป็นผู้ทำวิดีโอโฆษณาสินค้า TikTok ภาษาไทย
วิเคราะห์ชื่อสินค้าด้านล่าง แล้วตอบเป็น JSON object เท่านั้น โดยมี key:
search_keyword_zh = คำค้นภาษาจีนสั้น ๆ สำหรับหาคลิปสินค้าชนิดเดียวกันบน Douyin
product_name_th = ชื่อสินค้าไทยสั้น อ่านง่าย ไม่เกิน 45 ตัวอักษร
voiceover_th = บทพากย์ขายสินค้าไทยธรรมชาติ ความยาว 35-55 คำ ห้ามอ้างราคา
caption_th = แคปชั่นไทยสั้น 1 ประโยค
hashtags = แฮชแท็กไทย 3-5 คั่นด้วยช่องว่าง

ชื่อสินค้า: {}""".format(product_title)


def _analyze_with_phaya(prompt: str) -> Dict[str, Any]:
    import requests

    response = requests.post(
        "https://api.phaya.io/api/v1/phaya-gpt/chat/completions",
        headers={
            "Authorization": "Bearer {}".format(PHAYA_API_KEY),
            "Content-Type": "application/json",
        },
        json={
            "messages": [
                {"role": "system", "content": "ตอบเป็น JSON object ที่ถูกต้องเท่านั้น"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "stream": False,
        },
        timeout=60,
    )
    response.raise_for_status()
    return _extract_json_object(response.json()["message"]["content"])


def _analyze_with_gemini(prompt: str) -> Dict[str, Any]:
    import requests

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + urllib.parse.quote(GEMINI_MODEL, safe="")
        + ":generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    response = requests.post(
        endpoint,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    body = response.json()
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    return _extract_json_object(text)


def analyze_product(product_title: str) -> Dict[str, str]:
    """Generate a Chinese search phrase and Thai copy, preferring the configured Phaya API."""
    import requests

    prompt = _analysis_prompt(product_title)
    providers = []
    if PHAYA_API_KEY and not PHAYA_API_KEY.startswith("your_"):
        providers.append(("Phaya", lambda: _analyze_with_phaya(prompt)))
    if GEMINI_API_KEY and not GEMINI_API_KEY.startswith("your_"):
        providers.append(("Gemini", lambda: _analyze_with_gemini(prompt)))
    if not providers:
        raise PipelineError("ยังไม่ได้ตั้งค่า API สำหรับวิเคราะห์สินค้าบนเซิร์ฟเวอร์")

    result = None
    failures = []
    for provider_name, analyze in providers:
        try:
            result = analyze()
            print("[+] Product analysis completed with {}".format(provider_name))
            break
        except (requests.RequestException, KeyError, IndexError, ValueError, PipelineError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            failures.append("{} HTTP {}".format(provider_name, status) if status else provider_name)
    if result is None:
        raise PipelineError("วิเคราะห์สินค้าไม่สำเร็จ ({})".format(", ".join(failures)))

    required = ["search_keyword_zh", "product_name_th", "voiceover_th"]
    if any(not str(result.get(key) or "").strip() for key in required):
        raise PipelineError("Gemini ส่งข้อมูลสินค้าไม่ครบ")
    return {key: str(result.get(key) or "").strip() for key in result}


def _run_downloader(arguments: List[str], timeout: int = 240) -> subprocess.CompletedProcess:
    command = [str(DOWNLOADER_PYTHON), str(DOWNLOADER_DIR / "run.py")] + arguments
    print("[*] Running downloader: {}".format(" ".join(arguments[:4])))
    result = subprocess.run(
        command,
        cwd=str(DOWNLOADER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    combined = "{}\n{}".format(result.stdout, result.stderr)
    if result.returncode != 0:
        print("[-] Downloader output: {}".format(combined[-2000:]))
        if "LoginRequiredError" in combined or "status 2483" in combined:
            raise DouyinLoginRequired(
                "เซสชัน Douyin หมดอายุ จึงค้นหา/ดาวน์โหลดคลิปไม่ได้ กรุณาล็อกอิน Douyin ใหม่"
            )
        raise PipelineError("โปรแกรมดาวน์โหลด Douyin ทำงานไม่สำเร็จ")
    return result


def _latest_search_file(search_dir: Path, started_at: float) -> Path:
    candidates = [
        path
        for path in search_dir.glob("search/*.jsonl")
        if path.stat().st_mtime >= started_at - 2
    ]
    if not candidates:
        raise PipelineError("ค้นหา Douyin แล้วไม่พบรายการวิดีโอ")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def search_douyin_video(keyword: str, job_dir: Path) -> str:
    started_at = time.time()
    _run_downloader(
        [
            "--search",
            keyword,
            "--search-max",
            "5",
            "-p",
            str(job_dir),
            "-c",
            str(DOWNLOADER_CONFIG),
        ]
    )
    result_file = _latest_search_file(job_dir, started_at)
    with result_file.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            aweme_id = str(item.get("aweme_id") or "").strip()
            if aweme_id:
                return "https://www.douyin.com/video/{}".format(aweme_id)
    raise PipelineError("ผลค้นหา Douyin ไม่มีวิดีโอที่ดาวน์โหลดได้")


def download_douyin_video(url: str, job_dir: Path) -> Path:
    source_dir = job_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    before = set(source_dir.rglob("*.mp4"))
    _run_downloader(
        ["-u", url, "-p", str(source_dir), "-c", str(DOWNLOADER_CONFIG)],
        timeout=360,
    )
    videos = [path for path in source_dir.rglob("*.mp4") if path not in before]
    if not videos:
        videos = list(source_dir.rglob("*.mp4"))
    if not videos:
        raise PipelineError("ดาวน์โหลดเสร็จแต่ไม่พบไฟล์วิดีโอ")
    return max(videos, key=lambda path: path.stat().st_mtime)


def make_dialogues(text: str, duration: float = 18.0) -> List[Dict[str, Any]]:
    """Split Thai copy into readable caption cards distributed over the ad."""
    words = text.split()
    if not words:
        return []
    chunks = []
    current = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and len(candidate) > 26:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        chunks.append(" ".join(current))
    step = duration / len(chunks)
    return [
        {"start": index * step, "end": min(duration, (index + 1) * step), "text": chunk}
        for index, chunk in enumerate(chunks)
    ]


def _fit_caption_layout(text: str, width: int, height: int) -> Optional[Tuple[int, str]]:
    """Fit Thai text to the width and height of an OCR-derived black strip."""
    from PIL import ImageFont

    font_file = Path(FONT_DIR) / "Prompt-Bold.ttf"
    for size in range(146, 29, -2):
        font = ImageFont.truetype(str(font_file), size)
        if height >= 220 and len(text.split()) >= 2:
            words = text.split()
            options = []
            for split in range(1, len(words)):
                first, second = " ".join(words[:split]), " ".join(words[split:])
                first_box, second_box = font.getbbox(first), font.getbbox(second)
                first_width = first_box[2] - first_box[0]
                second_width = second_box[2] - second_box[0]
                line_height = first_box[3] - first_box[1] + second_box[3] - second_box[1] + 8
                if max(first_width, second_width) <= width - 36 and line_height <= height - 24:
                    options.append((abs(first_width - second_width), first, second))
            if options:
                _, first, second = min(options)
                return size, first + "\n" + second
        bounds = font.getbbox(text)
        if bounds[2] - bounds[0] <= width - 36 and bounds[3] - bounds[1] <= height - 24:
            return size, text
    return None


def _chinese_regions(video: Path, duration: float, job_dir: Path, engine) -> List[Dict[str, Any]]:
    """Sample a normalized clip and locate visible Chinese text with RapidOCR."""
    regions = []
    region_start = 0.0
    while region_start < duration:
        region_end = min(duration, region_start + 2.0)
        sample_time = min(duration - 0.1, (region_start + region_end) / 2)
        frame = job_dir / "ocr_{:03d}.jpg".format(len(regions))
        if not run_ffmpeg([
            "-ss", "{:.2f}".format(sample_time), "-i", str(video),
            "-vf", "scale=540:960", "-frames:v", "1", str(frame),
        ]):
            raise PipelineError("อ่านภาพจากคลิปเพื่อตรวจข้อความจีนไม่สำเร็จ")
        result = engine(str(frame))
        boxes = []
        for box, value, score in zip(
            result.boxes if result.boxes is not None else [],
            result.txts if result.txts is not None else [],
            result.scores if result.scores is not None else [],
        ):
            if score < 0.7 or not re.search(r"[\u3400-\u9fff]", value):
                continue
            left = max(0, int(min(point[0] for point in box) * 2) - 24)
            top = max(0, int(min(point[1] for point in box) * 2) - 20)
            right = min(1080, int(max(point[0] for point in box) * 2) + 24)
            bottom = min(1920, int(max(point[1] for point in box) * 2) + 20)
            boxes.append((left, top, right - left, bottom - top))
        regions.append({"start": region_start, "end": region_end,
                        "boxes": _merge_text_boxes(boxes)})
        region_start = region_end
    return regions


def _merge_text_boxes(boxes: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    """Use one black strip for adjacent lines of the same Chinese caption."""
    merged = []
    for x, y, width, height in sorted(boxes, key=lambda box: box[1]):
        if merged:
            old_x, old_y, old_width, old_height = merged[-1]
            horizontal_overlap = min(x + width, old_x + old_width) - max(x, old_x)
            vertical_gap = y - (old_y + old_height)
            if horizontal_overlap > 0 and vertical_gap <= 40:
                left = min(x, old_x)
                top = min(y, old_y)
                right = max(x + width, old_x + old_width)
                bottom = max(y + height, old_y + old_height)
                merged[-1] = (left, top, right - left, bottom - top)
                continue
        merged.append((x, y, width, height))
    return merged


def _mask_filters(regions: List[Dict[str, Any]]) -> str:
    filters = []
    for region in regions:
        for x, y, width, height in region["boxes"]:
            filters.append(
                "drawbox=x={}:y={}:w={}:h={}:color=black:t=fill:enable='between(t,{:.2f},{:.2f})'"
                .format(x, y, width, height, region["start"], region["end"])
            )
    return ",".join(filters)


def _place_dialogues_in_mask(dialogues: List[Dict[str, Any]], regions: List[Dict[str, Any]]) -> None:
    """Move Thai caption into a matching Chinese strip, when one is large enough."""
    for dialogue in dialogues:
        midpoint = (dialogue["start"] + dialogue["end"]) / 2
        region = next((item for item in regions if item["start"] <= midpoint < item["end"]), None)
        if not region or not region["boxes"]:
            continue
        x, y, width, height = max(region["boxes"], key=lambda box: box[2] * box[3])
        if width >= 480 and height >= 100 and _fit_caption_layout(dialogue["text"], width, height):
            dialogue["mask_region"] = (x, y, width, height)


def _prepare_montage_sources(source_videos: List[Path], job_dir: Path):
    """Normalize each clip to a short, equal length segment in input order."""
    segment_seconds = max(3.0, 18.0 / len(source_videos))
    prepared = []
    for index, source in enumerate(source_videos, start=1):
        segment = job_dir / "segment_{:02d}.mp4".format(index)
        normalized = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30,format=yuv420p"
        )
        if not run_ffmpeg(
            [
                "-stream_loop", "-1", "-i", str(source),
                "-t", "{:.3f}".format(segment_seconds), "-vf", normalized,
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                str(segment),
            ],
            timeout=240,
        ):
            raise PipelineError("ตัดช่วงจากคลิป Douyin ลำดับที่ {} ไม่สำเร็จ".format(index))
        prepared.append(segment)

    concat_list = job_dir / "montage_sources.txt"
    concat_list.write_text(
        "".join("file '{}'\n".format(path) for path in prepared),
        encoding="utf-8",
    )
    return concat_list, segment_seconds * len(source_videos), prepared


def compose_video(source_videos: List[Path], voiceover_text: str, job_dir: Path, output: Path) -> Path:
    if not source_videos:
        raise PipelineError("ไม่มีคลิปต้นฉบับสำหรับตัดต่อ")
    voice_path = job_dir / "voiceover.mp3"
    subtitle_path = job_dir / "captions.ass"
    preview_path = output.with_suffix(".jpg")
    if not generate_voiceover(voiceover_text, voice_path):
        raise PipelineError("สร้างเสียงพากย์ไทยไม่สำเร็จ")
    video_source, duration, segments = _prepare_montage_sources(source_videos, job_dir)
    segment_duration = duration / len(segments)
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise PipelineError("เซิร์ฟเวอร์ยังไม่มี OCR สำหรับตรวจข้อความจีนในคลิป") from exc
    engine = RapidOCR()
    all_regions = []
    for index, segment in enumerate(segments):
        for region in _chinese_regions(segment, segment_duration, job_dir, engine):
            all_regions.append({
                "start": region["start"] + index * segment_duration,
                "end": region["end"] + index * segment_duration,
                "boxes": region["boxes"],
            })
    dialogues = make_dialogues(voiceover_text, duration)
    _place_dialogues_in_mask(dialogues, all_regions)
    build_ass_subtitles(dialogues, subtitle_path)

    escaped_ass = str(subtitle_path).replace("\\", "\\\\").replace(":", "\\:")
    escaped_fonts = str(FONT_DIR).replace("\\", "\\\\").replace(":", "\\:")
    masks = _mask_filters(all_regions)
    video_filter = "{}subtitles='{}':fontsdir='{}'".format(
        masks + "," if masks else "", escaped_ass, escaped_fonts
    )
    video_input = ["-f", "concat", "-safe", "0", "-i", str(video_source)]
    success = run_ffmpeg(
        video_input + [
            "-i", str(voice_path), "-t", "{:.3f}".format(duration),
            "-vf", video_filter,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
            "-af", "apad", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(output),
        ],
        timeout=360,
    )
    if not success or not output.exists():
        raise PipelineError("ตัดต่อวิดีโอด้วย FFmpeg ไม่สำเร็จ")
    if not run_ffmpeg(["-i", str(output), "-ss", "00:00:01", "-frames:v", "1", str(preview_path)]):
        raise PipelineError("สร้างภาพตัวอย่างสำหรับ LINE ไม่สำเร็จ")
    return preview_path


def notify_success(
    output: Path,
    preview: Path,
    product_name: str,
    script_text: str,
    source_link: str,
    caption: str,
    hashtags: str,
    note: str = "",
) -> None:
    from line_notifier import send_line_flex_card, send_line_text, send_line_video

    video_url = "{}/media/{}".format(PUBLIC_BASE_URL, urllib.parse.quote(output.name))
    preview_url = "{}/media/{}".format(PUBLIC_BASE_URL, urllib.parse.quote(preview.name))
    if not send_line_video(video_url, preview_url):
        raise PipelineError("ตัดต่อเสร็จแล้ว แต่ LINE ปฏิเสธไฟล์วิดีโอ")
    send_line_flex_card(product_name, script_text, source_link)
    details = "{}\n\n{}\n\n{}".format(caption, hashtags, note).strip()
    if details:
        send_line_text(details)


def notify_failure(message: str) -> None:
    from line_notifier import send_line_text

    send_line_text("❌ งานวิดีโอนี้ไม่สำเร็จ\n{}".format(message))


def is_douyin_video_url(url: str) -> bool:
    """Accept direct Douyin video pages and Douyin's short share links only."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if host in {"v.douyin.com", "v.iesdouyin.com"}:
        return bool(parsed.path.strip("/"))
    if host in {"douyin.com", "www.douyin.com", "m.douyin.com"}:
        return bool(re.fullmatch(r"/video/\d{15,20}/?", parsed.path))
    return False


def process_urls(urls: List[str]) -> Path:
    if not urls:
        raise PipelineError("กรุณาส่งลิงก์คลิป Douyin อย่างน้อย 1 ลิงก์")
    if len(urls) > 10:
        raise PipelineError("ส่งคลิปได้สูงสุด 10 ลิงก์ต่อหนึ่งงาน กรุณาแบ่งส่งเป็นหลายข้อความ")
    for index, url in enumerate(urls, start=1):
        if not is_douyin_video_url(url):
            raise PipelineError(
                "ลิงก์ลำดับที่ {} ไม่ใช่คลิป Douyin กรุณาส่งลิงก์วิดีโอโดยตรง "
                "ระบบไม่สร้างวิดีโอจากภาพสินค้า".format(index)
            )

    job_id = "{}_{}".format(int(time.time()), os.getpid())
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    analysis = {
        "product_name_th": "วิดีโอจาก Douyin",
        "voiceover_th": "รวมคลิปสินค้า Douyin ดูสินค้าจริง หลายมุมมอง พร้อมใช้งานค่ะ",
        "caption_th": "คลิปพร้อมใช้งานแล้วค่ะ ✨",
        "hashtags": "#Douyin #รีวิวสินค้า",
    }
    source_videos = []
    for index, url in enumerate(urls, start=1):
        source_job_dir = job_dir / "clip_{:02d}".format(index)
        source_job_dir.mkdir(parents=True, exist_ok=True)
        try:
            source_videos.append(download_douyin_video(url, source_job_dir))
        except PipelineError as exc:
            raise PipelineError("คลิปลำดับที่ {}: {}".format(index, exc)) from exc
    output = OUTPUT_DIR / "douyin_ad_{}.mp4".format(job_id)
    preview = compose_video(source_videos, analysis["voiceover_th"], job_dir, output)
    notify_success(
        output, preview, analysis["product_name_th"], analysis["voiceover_th"], urls[0],
        analysis["caption_th"], analysis["hashtags"],
        "รวมคลิป Douyin {} คลิป ตามลำดับที่ส่งมา".format(len(urls)),
    )
    return output


def process_url(url: str) -> Path:
    """Keep the single link entry point for callers outside the LINE webhook."""
    return process_urls([url])


def run_self_test() -> bool:
    test_audio = TEMP_DIR / "test_audio.mp3"
    generate_voiceover("ระบบพร้อมทำงานอัตโนมัติร่วมกับไลน์แล้วค่ะ", test_audio)
    test_ass = TEMP_DIR / "test.ass"
    build_ass_subtitles(
        [{"start": 0.0, "end": 3.0, "text": "ระบบพร้อมทำงานอัตโนมัติแล้วค่ะ"}],
        test_ass,
    )
    test_video = TEMP_DIR / "test_out.mp4"
    success = run_ffmpeg(
        [
            "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=3",
            "-i", str(test_audio), "-vf",
            "subtitles={}:fontsdir={}".format(test_ass, FONT_DIR),
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(test_video),
        ]
    )
    print("[+] SELF-TEST PASSED: {}".format(test_video) if success else "[-] SELF-TEST FAILED")
    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="Douyin viral video automation pipeline")
    parser.add_argument("-u", "--url", action="append", help="Direct Douyin video URL (repeatable)")
    parser.add_argument("--test", action="store_true", help="Run FFmpeg/voice verification test")
    args = parser.parse_args()

    if args.test:
        return 0 if run_self_test() else 1
    if not args.url:
        parser.error("--url is required unless --test is used")

    try:
        output = process_urls(args.url)
        print("[+] Pipeline completed: {}".format(output))
        return 0
    except PipelineError as exc:
        print("[-] Pipeline failed: {}".format(exc))
        notify_failure(str(exc))
        return 1
    except subprocess.TimeoutExpired:
        message = "การประมวลผลใช้เวลานานเกินกำหนด กรุณาลองส่งลิงก์อีกครั้ง"
        print("[-] {}".format(message))
        notify_failure(message)
        return 1
    except Exception as exc:
        print("[-] Unexpected pipeline error: {}: {}".format(type(exc).__name__, exc))
        notify_failure("ระบบเกิดข้อผิดพลาดภายใน กรุณาลองใหม่อีกครั้ง")
        return 1


if __name__ == "__main__":
    sys.exit(main())
