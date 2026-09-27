#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE Official Account (Messaging API) Notifier
Sends notifications, summaries, and Flex Cards directly to user's personal LINE.
"""

import os
import requests
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.getenv("LINE_USER_ID", "")

def send_line_text(message: str) -> bool:
    """Send plain text or markdown-style notification to LINE OA user"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID or LINE_CHANNEL_ACCESS_TOKEN.startswith("your_"):
        print("[!] LINE token or User ID not configured in .env. Skipping notification.")
        return False

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            print("[+] Successfully sent message to LINE OA!")
            return True
        else:
            print(f"[-] LINE API Error ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[-] LINE Request Exception: {e}")
        return False

def send_line_flex_card(product_title: str, script_summary: str, tiktok_link: str = "") -> bool:
    """Send an attractive LINE Flex Message bubble with action buttons"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID or LINE_CHANNEL_ACCESS_TOKEN.startswith("your_"):
        return False

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    footer_contents = []
    if tiktok_link:
        footer_contents.append({
            "type": "button",
            "style": "primary",
            "color": "#00B900",
            "action": {
                "type": "uri",
                "label": "🛒 ดูสินค้าใน TikTok Shop",
                "uri": tiktok_link
            }
        })

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1A1A2E",
            "contents": [
                {
                    "type": "text",
                    "text": "🎬 วิดีโอ Douyin ตัดต่อเสร็จแล้ว!",
                    "weight": "bold",
                    "color": "#00FF88",
                    "size": "md"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": product_title,
                    "weight": "bold",
                    "size": "lg",
                    "wrap": True
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F7F7F7",
                    "cornerRadius": "8px",
                    "paddingAll": "12px",
                    "contents": [
                        {
                            "type": "text",
                            "text": "🎙️ บทพากย์ไทย:",
                            "weight": "bold",
                            "size": "xs",
                            "color": "#888888"
                        },
                        {
                            "type": "text",
                            "text": script_summary,
                            "size": "sm",
                            "color": "#333333",
                            "wrap": True
                        }
                    ]
                }
            ]
        }
    }

    if footer_contents:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": footer_contents
        }

    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "flex",
                "altText": f"วิดีโอใหม่พร้อมใช้งาน: {product_title}",
                "contents": bubble
            }
        ]
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[-] Error sending flex card: {e}")
        return False

if __name__ == "__main__":
    print("Testing LINE Notifier module...")
    send_line_text("ทดสอบการแจ้งเตือนจากระบบ fms-delta Douyin Automation ค่ะ")
