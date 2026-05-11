#!/bin/bash
# ============================================================
#   Manus 沙箱唤醒管理面板 - 一键安装脚本 v2
#   自动检测系统环境，安装所有依赖，开机自启
# ============================================================

set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

PANEL_DIR="/opt/manus-panel"
SERVICE_FILE="/etc/systemd/system/manus-panel.service"
AGENT_SCRIPT="/usr/local/bin/manus_wakeup_agent.py"
LOG_FILE="/var/log/manus_wakeup.log"
PANEL_PORT=7788
GITHUB_RAW="https://raw.githubusercontent.com/Reang0/manus-wakeup-panel/main"

info()    { echo -e "${CYAN}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()    { echo -e "\n${BOLD}${YELLOW}[$1/7] $2${NC}"; }

clear
echo -e "${CYAN}${BOLD}"
echo "╔══════════════════════════════════════════════════╗"
echo "║    Manus 沙箱唤醒管理面板 - 一键安装 v2         ║"
echo "║    自动检测环境 · 安装依赖 · 开机自启            ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 检查 root ─────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "请使用 root 权限运行此脚本（sudo bash install.sh）"

# ── 检测系统类型 ──────────────────────────────────────────
step 1 "检测系统环境"
OS=""; PKG_MGR=""
if command -v apt-get &>/dev/null; then
    OS="debian"; PKG_MGR="apt-get"; info "检测到 Debian/Ubuntu 系统"
elif command -v dnf &>/dev/null; then
    OS="rhel"; PKG_MGR="dnf"; info "检测到 Fedora/RHEL 系统"
elif command -v yum &>/dev/null; then
    OS="rhel"; PKG_MGR="yum"; info "检测到 CentOS/RHEL 系统"
else
    error "不支持的系统，需要 apt-get 或 yum/dnf"
fi
success "系统检测完成：$OS ($PKG_MGR)"

# ── 安装 Python3 ──────────────────────────────────────────
step 2 "检查并安装 Python3"
PYTHON=""
for cmd in python3.11 python3.10 python3.9 python3.8 python3; do
    if command -v $cmd &>/dev/null; then
        VER=$($cmd --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        MAJOR=$(echo $VER | cut -d. -f1)
        MINOR=$(echo $VER | cut -d. -f2)
        if [[ $MAJOR -ge 3 && $MINOR -ge 8 ]]; then
            PYTHON=$cmd; info "找到 Python $VER：$cmd"; break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "未找到 Python 3.8+，正在安装..."
    if [[ "$OS" == "debian" ]]; then
        apt-get update -qq && apt-get install -y python3 python3-pip python3-venv -qq
    else
        $PKG_MGR install -y python3 python3-pip -q
    fi
    PYTHON=$(command -v python3)
fi
[[ -z "$PYTHON" ]] && error "Python3 安装失败"
success "Python 就绪：$($PYTHON --version)"

# ── 安装 pip ──────────────────────────────────────────────
step 3 "检查并安装 pip"
if ! $PYTHON -m pip --version &>/dev/null 2>&1; then
    warn "pip 未安装，正在安装..."
    if [[ "$OS" == "debian" ]]; then
        apt-get install -y python3-pip -qq 2>/dev/null || true
    else
        $PKG_MGR install -y python3-pip -q 2>/dev/null || true
    fi
    if ! $PYTHON -m pip --version &>/dev/null 2>&1; then
        warn "通过 get-pip.py 安装 pip..."
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        $PYTHON /tmp/get-pip.py --quiet
        rm -f /tmp/get-pip.py
    fi
fi
$PYTHON -m pip --version &>/dev/null || error "pip 安装失败，请手动执行：curl -sS https://bootstrap.pypa.io/get-pip.py | $PYTHON"
success "pip 就绪"

# ── 安装 Python 依赖 ──────────────────────────────────────
step 4 "安装 Python 依赖（flask, requests）"
install_pkg() {
    local pkg=$1
    local import_name=${2:-$1}
    if $PYTHON -c "import $import_name" &>/dev/null 2>&1; then
        info "$pkg 已安装，跳过"; return 0
    fi
    warn "$pkg 未安装，正在安装..."
    $PYTHON -m pip install $pkg --quiet --timeout 60 2>/dev/null || \
    $PYTHON -m pip install $pkg --quiet --timeout 60 --break-system-packages 2>/dev/null || \
    $PYTHON -m pip install $pkg --quiet --timeout 60 --user 2>/dev/null || \
    error "$pkg 安装失败"
    success "$pkg 安装完成"
}
install_pkg flask
install_pkg requests
success "所有依赖安装完成"

# ── 创建目录并下载文件 ────────────────────────────────────
step 5 "下载项目文件（来自 GitHub）"
mkdir -p "$PANEL_DIR/templates" /etc/manus_wakeup
touch "$LOG_FILE" && chmod 666 "$LOG_FILE"

download() {
    local url=$1; local dest=$2
    info "下载 $(basename $dest)..."
    if command -v curl &>/dev/null; then
        curl -fsSL "$url" -o "$dest" || error "下载失败：$url"
    elif command -v wget &>/dev/null; then
        wget -q "$url" -O "$dest" || error "下载失败：$url"
    else
        error "未找到 curl 或 wget"
    fi
}

download "$GITHUB_RAW/panel/app.py"               "$PANEL_DIR/app.py"
download "$GITHUB_RAW/panel/templates/index.html"  "$PANEL_DIR/templates/index.html"
download "$GITHUB_RAW/scripts/wakeup_agent.py"    "$AGENT_SCRIPT"
chmod +x "$AGENT_SCRIPT"
success "文件下载完成"

# ── 配置 systemd 服务 ─────────────────────────────────────
step 6 "配置系统服务（开机自启）"
PYTHON_PATH=$(command -v $PYTHON)

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Manus 沙箱唤醒管理面板
After=network.target

[Service]
Type=simple
WorkingDirectory=$PANEL_DIR
ExecStart=$PYTHON_PATH $PANEL_DIR/app.py
Restart=always
RestartSec=5
Environment=PORT=$PANEL_PORT
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
WantedBy=multi-user.target
EOF

if command -v systemctl &>/dev/null; then
    systemctl daemon-reload
    systemctl enable manus-panel --quiet 2>/dev/null || true
    systemctl restart manus-panel
    sleep 3
    if systemctl is-active --quiet manus-panel; then
        success "systemd 服务启动成功"
    else
        warn "systemd 启动异常，尝试直接运行..."
        nohup $PYTHON_PATH $PANEL_DIR/app.py >> $LOG_FILE 2>&1 &
        sleep 3
    fi
else
    warn "不支持 systemd，使用 nohup 后台运行"
    nohup $PYTHON_PATH $PANEL_DIR/app.py >> $LOG_FILE 2>&1 &
    sleep 3
fi

# ── 配置 cron ─────────────────────────────────────────────
step 7 "配置 cron 定时唤醒（每分钟）"
(crontab -l 2>/dev/null | grep -v "manus_wakeup") | crontab - 2>/dev/null || true
(crontab -l 2>/dev/null; echo "* * * * * $PYTHON_PATH $AGENT_SCRIPT run >> $LOG_FILE 2>&1") | crontab -
success "cron 配置完成（每分钟执行）"

# ── 验证 ──────────────────────────────────────────────────
sleep 2
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PANEL_PORT/" 2>/dev/null || echo "000")
[[ "$HTTP_CODE" == "200" ]] && success "服务验证通过" || warn "服务可能未完全启动（HTTP $HTTP_CODE），请查看日志：tail -f $LOG_FILE"

# ── 完成 ─────────────────────────────────────────────────
HOST_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}${BOLD}"
echo "╔══════════════════════════════════════════════════╗"
echo "║                   安装完成！                     ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${CYAN}管理面板地址：${NC}  ${BOLD}http://${HOST_IP}:${PANEL_PORT}${NC}"
echo ""
echo -e "  ${CYAN}常用命令：${NC}"
echo -e "    查看服务状态：  systemctl status manus-panel"
echo -e "    查看实时日志：  tail -f $LOG_FILE"
echo -e "    重启面板：      systemctl restart manus-panel"
echo -e "    停止面板：      systemctl stop manus-panel"
echo ""
echo -e "  ${YELLOW}注意：请确保服务器防火墙/安全组已放行 ${PANEL_PORT} 端口${NC}"
echo ""
