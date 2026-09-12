#!/usr/bin/env bash
# ==============================================================================
# Douyin Downloader - Setup Script
# ตรวจสอบและติดตั้งสภาพแวดล้อมทั้งหมดแบบอัตโนมัติ
# ==============================================================================

set -e

TARGET_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(python3 -c "import os, sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))" "$TARGET_SOURCE" 2>/dev/null)"
if [ -z "$SCRIPT_DIR" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$TARGET_SOURCE")" && pwd)"
fi
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "\n${CYAN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}       ${BOLD}🚀 เริ่มต้นการติดตั้ง Douyin Downloader${NC}       ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════╝${NC}\n"

# 1. ตรวจสอบ Python 3
echo -e "${BLUE}[1/6] ตรวจสอบ Python 3...${NC}"
if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo -e "${RED}❌ ไม่พบ Python 3 ในเครื่อง กรุณาติดตั้ง Python 3 ก่อนใช้งาน${NC}"
    exit 1
fi

PY_VER=$($PY_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "${GREEN}✓ พบ Python เวอร์ชัน $PY_VER${NC}"

# 2. ตรวจสอบ/สร้าง Virtual Environment (.venv)
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

echo -e "\n${BLUE}[2/6] ตรวจสอบ Virtual Environment (.venv)...${NC}"
if [ ! -f "$PYTHON" ]; then
    echo -e "${YELLOW}⚙️  กำลังสร้าง Virtual Environment ที่ $VENV_DIR ...${NC}"
    $PY_CMD -m venv "$VENV_DIR"
    echo -e "${GREEN}✓ สร้าง Virtual Environment เรียบร้อย${NC}"
else
    echo -e "${GREEN}✓ พบ Virtual Environment แล้ว${NC}"
fi

# 3. ติดตั้ง Dependencies
echo -e "\n${BLUE}[3/6] ติดตั้งแพ็กเกจ Python ที่จำเป็น...${NC}"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

echo -e "📦 กำลังติดตั้ง requirements.txt..."
"$VENV_DIR/bin/pip" install -r requirements.txt --quiet

echo -e "📦 กำลังติดตั้ง playwright, fastapi, uvicorn..."
"$VENV_DIR/bin/pip" install playwright "fastapi>=0.100" "uvicorn>=0.23" "pydantic>=2.0" --quiet

echo -e "🌐 กำลังตรวจสอบเบราว์เซอร์ Chromium สำหรับดึง Cookies..."
"$PYTHON" -m playwright install chromium

echo -e "${GREEN}✓ ติดตั้งแพ็กเกจทั้งหมดสำเร็จ!${NC}"

# 4. เตรียมไฟล์ config.yml
echo -e "\n${BLUE}[4/6] ตรวจสอบไฟล์การตั้งค่า (config.yml)...${NC}"
if [ ! -f "$SCRIPT_DIR/config.yml" ]; then
    if [ -f "$SCRIPT_DIR/config.example.yml" ]; then
        cp "$SCRIPT_DIR/config.example.yml" "$SCRIPT_DIR/config.yml"
    fi
fi

# ทำความสะอาด config.yml ให้พร้อมใช้งาน
"$PYTHON" -c "
import yaml
from pathlib import Path
cfg_path = Path('config.yml')
if cfg_path.exists():
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg.get('link'), list) or (cfg.get('link') and 'MS4wLjABAAAA' in str(cfg.get('link'))):
        cfg['link'] = []
    if cfg.get('cookies') and any(str(v).startswith('YOUR_') for v in cfg['cookies'].values()):
        cfg['cookies'] = {}
    with open(cfg_path, 'w', encoding='utf-8') as f:
        yaml.dump(cfg, f, allow_unicode=True)
" 2>/dev/null || true
echo -e "${GREEN}✓ ไฟล์ config.yml พร้อมใช้งาน${NC}"

# 5. ดึง Token / Cookies พื้นฐาน (ttwid) แบบอัตโนมัติ
echo -e "\n${BLUE}[5/6] ตรวจสอบ Token และ Cookies พื้นฐาน (ttwid)...${NC}"
"$PYTHON" -c "
import asyncio, yaml
from pathlib import Path

cfg_path = Path('config.yml')
need_cookies = True
if cfg_path.exists():
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    cookies = cfg.get('cookies') or {}
    if 'ttwid' in cookies and cookies['ttwid']:
        need_cookies = False

if need_cookies:
    print('🔄 กำลังดึง Token / Cookies เริ่มต้นจาก Douyin อัตโนมัติ (ใช้เวลา 3-5 วินาที)...')
    from playwright.async_api import async_playwright
    async def fetch():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            )
            page = await context.new_page()
            await page.goto('https://www.douyin.com/', wait_until='commit')
            await asyncio.sleep(4)
            cookies = await context.cookies()
            c_dict = {c['name']: c['value'] for c in cookies if 'douyin.com' in c['domain']}
            await browser.close()
            with open('config.yml', 'r', encoding='utf-8') as f:
                c = yaml.safe_load(f) or {}
            c['cookies'] = c_dict
            with open('config.yml', 'w', encoding='utf-8') as f:
                yaml.dump(c, f, allow_unicode=True)
            print('✓ ดึง Cookies เริ่มต้นเรียบร้อยแล้ว!')
    asyncio.run(fetch())
else:
    print('✓ พบคุกกี้ ttwid พร้อมใช้งานแล้ว')
" 2>/dev/null || true

# 6. ตั้งค่าสิทธิ์และ Command ลัด
echo -e "\n${BLUE}[6/6] ตั้งค่าสิทธิ์และคำสั่งลัด...${NC}"
chmod +x "$SCRIPT_DIR/douyin.sh" "$SCRIPT_DIR/run.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/setup.sh" 2>/dev/null || true

# สร้าง symlink เข้า Homebrew bin หากเขียนได้
if [ -w "/opt/homebrew/bin" ]; then
    ln -sf "$SCRIPT_DIR/douyin.sh" "/opt/homebrew/bin/douyin" 2>/dev/null || true
elif [ -d "$HOME/.local/bin" ] && [ -w "$HOME/.local/bin" ]; then
    ln -sf "$SCRIPT_DIR/douyin.sh" "$HOME/.local/bin/douyin" 2>/dev/null || true
fi

# บันทึก alias ลง shell rc ถ้ายังไม่มี
SHELL_RC=""
if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ] && [ -f "$SHELL_RC" ]; then
    if ! grep -q "alias douyin=" "$SHELL_RC" 2>/dev/null; then
        echo "alias douyin=\"$SCRIPT_DIR/douyin.sh\"" >> "$SHELL_RC"
    fi
fi

echo -e "\n${GREEN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}       ${BOLD}🎉 ติดตั้งสมบูรณ์ 100% พร้อมใช้งาน!${NC}          ${GREEN}║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════╝${NC}\n"
echo -e "คุณสามารถเริ่มใช้งานได้ทันทีด้วยวิธีใดวิธีหนึ่งต่อไปนี้:"
echo -e "  1. พิมพ์คำสั่ง: ${BOLD}./douyin.sh${NC} (เปิดเมนูโต้ตอบ)"
echo -e "  2. พิมพ์คำสั่ง: ${BOLD}./douyin.sh <URL>${NC} (ดาวน์โหลดทันที)"
echo -e "  3. หรือพิมพ์:   ${BOLD}douyin${NC} (เมื่อเปิด terminal ใหม่)"
echo -e ""
