#!/usr/bin/env bash
# ==============================================================================
# Douyin Downloader Launcher
# ==============================================================================

set -e

# นำทางไปยัง directory ที่แท้จริงของโปรเจกต์เสมอ (รองรับการเรียกผ่าน symlink)
TARGET_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(python3 -c "import os, sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))" "$TARGET_SOURCE" 2>/dev/null)"
if [ -z "$SCRIPT_DIR" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$TARGET_SOURCE")" && pwd)"
fi
cd "$SCRIPT_DIR"

# ตรวจสอบและตั้งค่า Python Virtual Environment
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

# สีสำหรับแสดงผลใน Terminal
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ฟังก์ชันตรวจสอบความพร้อมของ Environment
ensure_environment() {
    if [ ! -f "$PYTHON" ]; then
        echo -e "${YELLOW}⚙️  ยังไม่พบ Virtual Environment กำลังสร้างและติดตั้งแพ็กเกจที่จำเป็น...${NC}"
        python3 -m venv "$VENV_DIR"
        "$VENV_DIR/bin/pip" install --upgrade pip
        "$VENV_DIR/bin/pip" install -r requirements.txt
        "$VENV_DIR/bin/pip" install playwright
        "$VENV_DIR/bin/python" -m playwright install chromium
        echo -e "${GREEN}✓ ติดตั้งสำเร็จเรียบร้อย!${NC}\n"
    fi

    if [ ! -f "$SCRIPT_DIR/config.yml" ]; then
        if [ -f "$SCRIPT_DIR/config.example.yml" ]; then
            cp "$SCRIPT_DIR/config.example.yml" "$SCRIPT_DIR/config.yml"
        fi
    fi

    # ตรวจสอบว่ามีคุกกี้พื้นฐาน (ttwid) แล้วหรือยัง หากยังไม่มีให้ดึงอัตโนมัติ
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
    print('🔄 กำลังดึง Token / Cookies เริ่มต้นจาก Douyin อัตโนมัติ...')
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
" 2>/dev/null || true
}

# สกัดเอา URL ออกจากข้อความ (กรณีผู้ใช้ก็อปปี้มาทั้งข้อความแชร์จากแอป Douyin)
extract_url() {
    local raw_input="$1"
    # ใช้ python สกัด url https?://... อย่างแม่นยำ
    "$PYTHON" -c "
import sys, re
text = sys.argv[1] if len(sys.argv) > 1 else ''
match = re.search(r'https?://[^\s\"\'<>]+', text)
if match:
    print(match.group(0))
else:
    print(text.strip())
" "$raw_input"
}

# ฟังก์ชันดาวน์โหลดจาก URL
do_download() {
    local raw_link="$1"
    if [ -z "$raw_link" ]; then
        echo -e "${RED}⚠️ ไม่พบลิงก์ กรุณาลองใหม่อีกครั้ง${NC}"
        return 1
    fi

    local clean_link
    clean_link=$(extract_url "$raw_link")

    echo -e "\n${CYAN}🚀 กำลังเริ่มดาวน์โหลด:${NC} ${BOLD}$clean_link${NC}\n"
    "$PYTHON" run.py -u "$clean_link"
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        echo -e "\n${GREEN}✓ ดำเนินการเสร็จสิ้น!${NC}"
    else
        echo -e "\n${RED}✗ เกิดข้อผิดพลาดในการดาวน์โหลด (รหัสข้อผิดพลาด: $exit_code)${NC}"
    fi

    return $exit_code
}

# ฟังก์ชันเข้าสู่ระบบเพื่อดึง Cookies
do_login() {
    echo -e "\n${CYAN}======================================================${NC}"
    echo -e "${BOLD}🔑 กำลังเปิดเบราว์เซอร์เพื่อเข้าสู่ระบบ Douyin...${NC}"
    echo -e "1. เบราว์เซอร์จะเปิดหน้าเว็บ Douyin ขึ้นมา"
    echo -e "2. ให้สแกน QR Code หรือล็อกอินบัญชี Douyin ในหน้าต่างเบราว์เซอร์ให้เรียบร้อย"
    echo -e "3. เมื่อล็อกอินเสร็จแล้ว ให้กลับมากด ${BOLD}[Enter]${NC} ที่หน้าจอนี้"
    echo -e "${CYAN}======================================================${NC}\n"
    
    "$PYTHON" -m tools.cookie_fetcher --config config.yml
    echo -e "\n${GREEN}✓ บันทึก Cookies เรียบร้อยแล้ว!${NC}\n"
}

# ฟังก์ชันเปิดโฟลเดอร์ Downloaded
do_open_folder() {
    mkdir -p "$SCRIPT_DIR/Downloaded"
    echo -e "${GREEN}📂 กำลังเปิดโฟลเดอร์ Downloaded...${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "$SCRIPT_DIR/Downloaded"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        xdg-open "$SCRIPT_DIR/Downloaded" 2>/dev/null || echo "ที่อยู่โฟลเดอร์: $SCRIPT_DIR/Downloaded"
    fi
}

# จัดการเมื่อส่ง argument มาโดยตรง
handle_args() {
    ensure_environment

    case "$1" in
        login)
            do_login
            exit 0
            ;;
        open)
            do_open_folder
            exit 0
            ;;
        server)
            echo -e "${CYAN}🚀 กำลังเริ่ม REST API Server...${NC}"
            "$PYTHON" run.py --serve --serve-port 8000
            exit 0
            ;;
        help|--help|-h)
            echo -e "${BOLD}การใช้งาน:${NC}"
            echo -e "  ./douyin.sh                    เปิดเมนูใช้งานแบบโต้ตอบ"
            echo -e "  ./douyin.sh <URL>              ดาวน์โหลดลิงก์ที่ระบุทันที"
            echo -e "  ./douyin.sh login              เปิดเบราว์เซอร์เพื่อเข้าสู่ระบบ Douyin รับ Cookies"
            echo -e "  ./douyin.sh open               เปิดโฟลเดอร์ไฟล์ที่ดาวน์โหลด"
            echo -e "  ./douyin.sh server             เปิด REST API Server (พอร์ต 8000)"
            exit 0
            ;;
        *)
            # ถือว่าเป็น URL สำหรับดาวน์โหลด
            do_download "$1"
            exit $?
            ;;
    esac
}

# เมนูหลักแบบ Interactive
interactive_menu() {
    ensure_environment

    while true; do
        echo -e "\n${CYAN}╔════════════════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}║${NC}       ${BOLD}🎵 Douyin Downloader (TikTok จีน)${NC}           ${CYAN}║${NC}"
        echo -e "${CYAN}╚════════════════════════════════════════════════════╝${NC}"
        echo -e "  ${BOLD}1)${NC} 📥 วางลิงก์เพื่อดาวน์โหลด (วิดีโอ / รูปภาพ / ผู้ใช้)"
        echo -e "  ${BOLD}2)${NC} 🔑 เข้าสู่ระบบเพื่อดึง Cookies (เปิดเบราว์เซอร์)"
        echo -e "  ${BOLD}3)${NC} 📁 ดาวน์โหลดทุกลิงก์ที่ระบุไว้ใน config.yml"
        echo -e "  ${BOLD}4)${NC} 📂 เปิดโฟลเดอร์ที่ดาวน์โหลดไว้ (Downloaded/)"
        echo -e "  ${BOLD}5)${NC} ⚙️  เปิดดูหรือแก้ไข config.yml"
        echo -e "  ${BOLD}0)${NC} 🚪 ออกจากโปรแกรม"
        echo -e "${CYAN}──────────────────────────────────────────────────────${NC}"
        read -rp "👉 เลือกเมนู [1-5, 0]: " choice

        case "$choice" in
            1)
                echo -e "\n${YELLOW}วางลิงก์ Douyin ได้เลย (รองรับทั้ง short link, video, note หรือข้อความแชร์):${NC}"
                read -rp "🔗 ลิงก์: " user_link
                if [ -n "$user_link" ]; then
                    do_download "$user_link"
                    read -rp "ต้องการเปิดโฟลเดอร์ไฟล์ที่ดาวน์โหลดเลยไหม? (y/N): " open_ans
                    if [[ "$open_ans" =~ ^[Yy]$ ]]; then
                        do_open_folder
                    fi
                fi
                ;;
            2)
                do_login
                ;;
            3)
                echo -e "\n${CYAN}🚀 กำลังดาวน์โหลดตามรายการใน config.yml...${NC}\n"
                "$PYTHON" run.py
                ;;
            4)
                do_open_folder
                ;;
            5)
                if [[ "$OSTYPE" == "darwin"* ]]; then
                    open -t "$SCRIPT_DIR/config.yml" 2>/dev/null || nano "$SCRIPT_DIR/config.yml"
                else
                    nano "$SCRIPT_DIR/config.yml"
                fi
                ;;
            0|q|exit)
                echo -e "\n${GREEN}👋 บ๊ายบายครับ!${NC}\n"
                exit 0
                ;;
            *)
                echo -e "${RED}⚠️ ตัวเลือกไม่ถูกต้อง กรุณาเลือกใหม่${NC}"
                ;;
        esac
    done
}

# จุดเริ่มต้นของสคริปต์
if [ $# -gt 0 ]; then
    handle_args "$@"
else
    interactive_menu
fi
