#!/bin/bash
# Outlook 自动化注册 - 一键部署脚本
# 用法: bash deploy_full.sh [node_name]
# 适用于全新服务器初始化

set -e
NODE_NAME="${1:-unknown}"
DEST="/home/workspace/Email-Register"
PYTHON=python3

echo "=========================================="
echo "  Outlook 自动化注册部署 - ${NODE_NAME}"
echo "=========================================="

# 1. 系统依赖
echo "[1/8] 安装系统依赖..."
if command -v apt-get &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-venv git curl wget xvfb \
        chromium chromium-driver libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
        libgbm1 libasound2 libxshmfence1 libxcomposite1 libxrandr2 libxdamage1 \
        libpango-1.0-0 libcairo2 libatspi2.0-0 2>/dev/null || true
elif command -v yum &>/dev/null; then
    yum install -y python3 python3-pip git curl wget xorg-x11-server-Xvfb \
        chromium chromium-libs nss atk at-spi2-atk libdrm libxkbcommon \
        mesa-libgbm alsa-lib libXcomposite libXrandr libXdamage \
        pango cairo at-spi2-core 2>/dev/null || true
fi

# 2. Python 依赖
echo "[2/8] 安装 Python 依赖..."
pip3 install --quiet playwright requests aiohttp 2>/dev/null || \
pip install --quiet playwright requests aiohttp 2>/dev/null || true

# 3. 克隆/更新仓库
echo "[3/8] 同步代码..."
if [ -d "$DEST/.git" ]; then
    cd "$DEST"
    git fetch origin main
    git reset --hard origin/main
else
    git clone https://github.com/xingluoyuankong/outlook-auto-register.git "$DEST"
    cd "$DEST"
    git checkout main
fi

# 4. 初始化云端凭证仓库
echo "[4/8] 初始化云端凭证仓库..."
CLOUD_DIR="$DEST/云端注册邮箱"
if [ ! -d "$CLOUD_DIR/.git" ]; then
    rm -rf "$CLOUD_DIR"
    git clone https://github.com/xingluoyuankong/cloud-register-email.git "$CLOUD_DIR"
fi
cd "$CLOUD_DIR"
git pull --rebase 2>/dev/null || true
cd "$DEST"

# 5. 安装 Playwright 浏览器
echo "[5/8] 安装 Playwright Chromium..."
python3 -m playwright install chromium 2>/dev/null || true
python3 -m playwright install-deps chromium 2>/dev/null || true

# 6. 配置 Xvfb
echo "[6/8] 配置 Xvfb 显示..."
if ! pgrep -f "Xvfb :98" >/dev/null; then
    Xvfb :98 -screen 0 1366x768x24 -ac -nolisten tcp &
    sleep 2
fi
export DISPLAY=:98

# 7. 配置 systemd 服务
echo "[7/8] 配置 systemd 服务..."
cat > /etc/systemd/system/outlook-auto-register.service << 'EOF'
[Unit]
Description=Outlook Auto Register Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/workspace/Email-Register
Environment=DISPLAY=:98
Environment=PYTHONUNBUFFERED=1
Environment=SUB_PROXY_FAST_START=1
ExecStart=/usr/bin/python3 /home/workspace/Email-Register/outlook_daemon.py
Restart=always
RestartSec=30
StandardOutput=append:/home/workspace/Email-Register/runtime_outlook/logs/outlook_daemon.log
StandardError=append:/home/workspace/Email-Register/runtime_outlook/logs/outlook_daemon_err.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable outlook-auto-register
systemctl restart outlook-auto-register

# 8. 配置 ngrok (如果 token 可用)
echo "[8/8] 配置 ngrok 隧道..."
if command -v ngrok &>/dev/null && [ -n "$NGROK_AUTHTOKEN" ]; then
    ngrok config add-authtoken "$NGROK_AUTHTOKEN" 2>/dev/null || true
    pkill -f "ngrok http 8765" 2>/dev/null || true
    nohup ngrok http 8765 --log=stdout --log-format=logfmt > /tmp/ngrok.log 2>&1 &
    sleep 5
    NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])" 2>/dev/null || echo "未获取到")
    echo "ngrok 隧道: $NGROK_URL"
else
    echo "ngrok 未安装或未配置 NGROK_AUTHTOKEN"
fi

# 完成
echo ""
echo "=========================================="
echo "  部署完成!"
echo "=========================================="
echo "服务状态:"
systemctl status outlook-auto-register --no-pager | head -5
echo ""
echo "日志: tail -f $DEST/runtime_outlook/logs/outlook_daemon.log"
echo "=========================================="
