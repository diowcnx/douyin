#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Douyin Viral Video Automation Pipeline (with LINE OA Integration)
Runs on Ubuntu Server alongside FileMaker Server.
Engineered to be lightweight, thread-limited (-threads 2), and memory-safe (<100MB RAM).
"""

import os
import sys
import json
import time
import glob
import shutil
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Load env configuration
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")
FONT_DIR = os.getenv("FONT_DIR", "/home/diowcnx/font")
FFMPEG_BIN = os.getenv("FFMPEG_PATH", "/home/diowcnx/bin/ffmpeg")
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"

OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

def run_ffmpeg(cmd, timeout=120):
    """Run ffmpeg command with priority nice 10 and max 2 threads"""
    base_cmd = ["nice", "-n", "10", FFMPEG_BIN, "-threads", "2", "-y"]
    full_cmd = base_cmd + cmd
    res = subprocess.run(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if res.returncode != 0:
        print(f"[FFmpeg Error] {res.stderr[-400:]}")
    return res.returncode == 0

def generate_voiceover(text, output_mp3):
    """Generate Thai speech using gTTS"""
    from gtts import gTTS
    print(f"[*] Generating voiceover: '{text[:40]}...' ")
    tts = gTTS(text=text, lang='th')
    tts.save(str(output_mp3))
    return output_mp3.exists()

def build_ass_subtitles(dialogues, output_ass_file):
    """Build ASS subtitle file with Prompt-Bold font styling"""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Prompt,62,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for d in dialogues:
        start_fmt = format_timestamp(d["start"])
        end_fmt = format_timestamp(d["end"])
        txt = d["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start_fmt},{end_fmt},Default,,0,0,0,,{txt}")
    
    with open(output_ass_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def format_timestamp(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def notify_line(product_name, script_text, tiktok_link=""):
    """Send notification via LINE Official Account"""
    try:
        from line_notifier import send_line_flex_card, send_line_text
        print("[*] Sending notification to LINE OA...")
        sent = send_line_flex_card(product_name, script_text, tiktok_link)
        if not sent:
            send_line_text(f"🎬 ตัดต่อวิดีโอเสร็จแล้วค่ะ!\nสินค้า: {product_name}\nบทพากย์: {script_text}\nTikTok: {tiktok_link}")
        return True
    except Exception as e:
        print(f"[-] LINE notification error: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Douyin Viral Video Automation Pipeline (with LINE OA)")
    parser.add_argument("-u", "--url", help="Douyin share URL to process")
    parser.add_argument("-v", "--video", help="Existing local video MP4 to process")
    parser.add_argument("--test", action="store_true", help="Run verification test")
    args = parser.parse_args()

    print("==================================================")
    print("   Douyin Viral Automation Engine (LINE OA)       ")
    print("==================================================")
    print(f"FFmpeg Binary: {FFMPEG_BIN}")
    print(f"Font Dir:      {FONT_DIR}")
    print(f"Output Dir:    {OUTPUT_DIR}")

    if args.test:
        print("[*] Running self-test...")
        test_audio = TEMP_DIR / "test_audio.mp3"
        generate_voiceover("ระบบพร้อมทำงานอัตโนมัติร่วมกับ LINE OA แล้วค่ะ", test_audio)
        test_ass = TEMP_DIR / "test.ass"
        build_ass_subtitles([{"start": 0.0, "end": 3.0, "text": "ระบบพร้อมทำงานอัตโนมัติแล้วค่ะ"}], test_ass)
        test_video = TEMP_DIR / "test_out.mp4"
        success = run_ffmpeg([
            "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=3",
            "-i", str(test_audio),
            "-vf", f"subtitles={test_ass}:fontsdir={FONT_DIR}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest",
            str(test_video)
        ])
        if success:
            print("[+] SELF-TEST PASSED: Video & Audio & Subtitles generated successfully!")
            print(f"    File: {test_video} ({test_video.stat().st_size} bytes)")
            notify_line("ที่ซาวข้าวอเนกประสงค์", "ระบบทดสอบเสร็จสมบูรณ์ พร้อมเชื่อมต่อ LINE OA ค่ะ")
        else:
            print("[-] SELF-TEST FAILED")
        return

    print("[*] Ready. Use --url <douyin_url> or --test to run.")

if __name__ == "__main__":
    main()
