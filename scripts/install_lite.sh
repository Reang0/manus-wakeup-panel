#!/bin/bash
# ============================================================
#   Manus 沙箱自动唤醒 - 通用交互式一键安装脚本
#   支持任意 API Key + Task ID，可复用给任何沙箱
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

clear
echo -e "${CYAN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║     Manus 沙箱自动唤醒 - 一键安装工具       ║"
echo "║     每1分钟自动唤醒沙箱 + 重启服务           ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 交互式输入参数 ──────────────────────────────────────
echo -e "${YELLOW}请输入以下配置信息：${NC}"
echo ""

# API Key
while true; do
    read -p "$(echo -e ${BOLD})Manus API Key: $(echo -e ${NC})" MANUS_API_KEY
    if [[ -n "$MANUS_API_KEY" ]]; then
        break
    fi
    echo -e "${RED}API Key 不能为空，请重新输入${NC}"
done

# Task ID
while true; do
    read -p "$(echo -e ${BOLD})Task ID (对话ID): $(echo -e ${NC})" TASK_ID
    if [[ -n "$TASK_ID" ]]; then
        break
    fi
    echo -e "${RED}Task ID 不能为空，请重新输入${NC}"
done

# 唤醒消息（可选，有默认值）
echo ""
echo -e "${YELLOW}唤醒后执行的指令（直接回车使用默认值）：${NC}"
DEFAULT_MSG="[自动唤醒] 请执行 bash /home/ubuntu/service_restart.sh 重启所有服务"
read -p "唤醒指令 [默认: 自动重启服务]: " WAKEUP_MSG
if [[ -z "$WAKEUP_MSG" ]]; then
    WAKEUP_MSG="$DEFAULT_MSG"
fi

# 间隔时间
read -p "检查间隔（秒，默认60）: " INTERVAL
if [[ -z "$INTERVAL" ]] || ! [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then
    INTERVAL=60
fi

echo ""
echo -e "${CYAN}── 配置确认 ─────────────────────────────────${NC}"
echo -e "  API Key : ${BOLD}${MANUS_API_KEY:0:20}...${NC}"
echo -e "  Task ID : ${BOLD}${TASK_ID}${NC}"
echo -e "  间隔    : ${BOLD}每 ${INTERVAL} 秒${NC}"
echo -e "  唤醒指令: ${BOLD}${WAKEUP_MSG:0:50}...${NC}"
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
echo ""
read -p "确认安装？[Y/n]: " CONFIRM
if [[ "$CONFIRM" =~ ^[Nn]$ ]]; then
    echo "已取消安装"
    exit 0
fi

echo ""

# ── Step 1: 检查 Python3 ─────────────────────────────────
echo -e "${YELLOW}[1/4] 检查 Python3...${NC}"
PYTHON=""
for cmd in python3 python3.11 python3.10 python3.9 python; do
    if command -v $cmd &>/dev/null; then
        VER=$($cmd --version 2>&1)
        if echo "$VER" | grep -q "Python 3"; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "未找到 Python3，正在安装..."
    if command -v apt-get &>/dev/null; then
        apt-get install -y python3 python3-pip -qq
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip -q
    else
        echo -e "${RED}无法自动安装 Python3，请手动安装后重试${NC}"
        exit 1
    fi
    PYTHON=python3
fi
echo -e "${GREEN}✓ $($PYTHON --version)${NC}"

# ── Step 2: 安装 requests（静默，带超时）────────────────
echo -e "${YELLOW}[2/4] 安装 Python 依赖...${NC}"

# 先检查是否已安装
if $PYTHON -c "import requests" 2>/dev/null; then
    echo -e "${GREEN}✓ requests 已安装${NC}"
else
    # 尝试多种方式安装
    INSTALLED=false
    for PIP in pip3 pip "$PYTHON -m pip"; do
        if $PIP install requests --quiet --timeout 30 2>/dev/null; then
            INSTALLED=true
            break
        fi
    done

    if ! $INSTALLED; then
        # 最后尝试：直接下载 requests 源码
        echo "pip 安装失败，尝试备用方式..."
        $PYTHON -c "
import urllib.request, zipfile, os, sys
url = 'https://files.pythonhosted.org/packages/source/r/requests/requests-2.31.0.tar.gz'
try:
    urllib.request.urlretrieve(url, '/tmp/requests.tar.gz')
    import tarfile
    with tarfile.open('/tmp/requests.tar.gz') as t:
        t.extractall('/tmp/')
    sys.path.insert(0, '/tmp/requests-2.31.0/src' if os.path.exists('/tmp/requests-2.31.0/src') else '/tmp/requests-2.31.0')
    import requests
    print('requests 加载成功')
except Exception as e:
    print(f'备用安装失败: {e}')
" 2>&1 || true
    fi

    if $PYTHON -c "import requests" 2>/dev/null; then
        echo -e "${GREEN}✓ requests 安装成功${NC}"
    else
        echo -e "${RED}✗ requests 安装失败，将使用内置 urllib 替代${NC}"
    fi
fi

# ── Step 3: 写入唤醒脚本 ────────────────────────────────
echo -e "${YELLOW}[3/4] 安装唤醒脚本...${NC}"

SCRIPT_PATH="/usr/local/bin/manus_wakeup.py"

cat > "$SCRIPT_PATH" << PYEOF
#!/usr/bin/env python3
# Manus 沙箱自动唤醒脚本 - 自动生成于 $(date)
import time, sys, logging, json
from datetime import datetime
try:
    import requests as _req
    USE_REQUESTS = True
except ImportError:
    import urllib.request, urllib.error
    USE_REQUESTS = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

MANUS_API_KEY = "${MANUS_API_KEY}"
TASK_ID = "${TASK_ID}"
BASE = "https://api.manus.ai"
INTERVAL = ${INTERVAL}
WAKEUP_MSG = """${WAKEUP_MSG}"""

def http_get(url, headers, params):
    if USE_REQUESTS:
        r = _req.get(url, headers=headers, params=params, timeout=15)
        return r.json()
    else:
        from urllib.parse import urlencode
        full_url = url + "?" + urlencode(params)
        req = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

def http_post(url, headers, data):
    if USE_REQUESTS:
        r = _req.post(url, headers=headers, json=data, timeout=30)
        return r.json()
    else:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

def get_status():
    try:
        data = http_get(f"{BASE}/v2/task.listMessages",
            {"x-manus-api-key": MANUS_API_KEY},
            {"task_id": TASK_ID, "order": "desc", "limit": 5})
        for m in data.get("messages", []):
            if m.get("type") == "status_update":
                return m["status_update"]["agent_status"]
        return "stopped"
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return None

def send_wakeup():
    try:
        data = http_post(f"{BASE}/v2/task.sendMessage",
            {"x-manus-api-key": MANUS_API_KEY, "Content-Type": "application/json"},
            {"task_id": TASK_ID, "message": {"role": "user", "content": WAKEUP_MSG}})
        if data.get("ok"):
            logger.info("唤醒消息发送成功")
            return True
        else:
            logger.error(f"发送失败: {data.get('error', {}).get('message')}")
            return False
    except Exception as e:
        logger.error(f"发送异常: {e}")
        return False

def run_once():
    logger.info(f"--- 唤醒检查 @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    status = get_status()
    logger.info(f"任务状态: {status}")
    if status != "running":
        send_wakeup()
    else:
        logger.info("任务运行中，跳过唤醒")

def run_loop():
    logger.info(f"启动持续循环模式，每 {INTERVAL} 秒检查一次")
    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"执行异常: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    if "--loop" in sys.argv:
        run_loop()
    else:
        run_once()
PYEOF

chmod +x "$SCRIPT_PATH"
echo -e "${GREEN}✓ 脚本已安装到 ${SCRIPT_PATH}${NC}"

# ── Step 4: 配置 cron ────────────────────────────────────
echo -e "${YELLOW}[4/4] 配置 cron 定时任务（每 ${INTERVAL} 秒 → 每分钟执行）...${NC}"

# 删除旧任务
(crontab -l 2>/dev/null | grep -v "manus_wakeup") | crontab - 2>/dev/null || true

# 添加新任务（cron 最小粒度1分钟）
(crontab -l 2>/dev/null; echo "* * * * * $PYTHON $SCRIPT_PATH >> /var/log/manus_wakeup.log 2>&1") | crontab -

echo -e "${GREEN}✓ cron 已配置${NC}"

# ── 立即测试 ─────────────────────────────────────────────
echo ""
echo -e "${YELLOW}立即测试运行...${NC}"
$PYTHON "$SCRIPT_PATH"

# ── 完成 ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "╔══════════════════════════════════════════════╗"
echo "║              安装完成！                      ║"
echo "╠══════════════════════════════════════════════╣"
echo "║  每分钟自动检查并唤醒 Manus 沙箱             ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${CYAN}查看日志：${NC}  tail -f /var/log/manus_wakeup.log"
echo -e "  ${CYAN}手动执行：${NC}  $PYTHON $SCRIPT_PATH"
echo -e "  ${CYAN}停止任务：${NC}  crontab -e  (删除 manus_wakeup 那行)"
echo ""
