#!/bin/bash
# gcore 完整部署：git pull + ngrok 保活 + daemon 保活 + systemd 开机自启
set -euo pipefail
ROOT="/home/ubuntu/Email-Register"
cd "$ROOT"

export DEBIAN_FRONTEND=noninteractive
export SUB_PROXY_FAST_START=1
export PYTHONUNBUFFERED=1

echo "=== [1/5] 安装依赖 ==="
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq xvfb python3-toml 2>/dev/null || true

echo "=== [2/5] 创建 systemd 服务 ==="

# ngrok 隧道服务
sudo tee /etc/systemd/system/outlook-ngrok.service >/dev/null <<'EOF'
[Unit]
Description=Outlook Dashboard ngrok tunnel
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/local/bin/ngrok http 8765 --log=stdout --log-format=logfmt
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 主守护进程服务
sudo tee /etc/systemd/system/outlook-daemon.service >/dev/null <<EOF
[Unit]
Description=Outlook auto register daemon (4h cycle)
After=network.target outlook-ngrok.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$ROOT
Environment=DISPLAY=:98
Environment=SUB_PROXY_FAST_START=1
Environment=PYTHONUNBUFFERED=1
Environment=OUTLOOK_DASHBOARD_PORT=8765
Environment=OUTLOOK_REGISTRAR_NODE=gcore
Environment=EMAIL_REGISTER_ROOT=$ROOT
ExecStart=/bin/bash $ROOT/outlook_daemon_no_ngrok.sh
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

echo "=== [3/5] 启用并启动服务 ==="
sudo systemctl daemon-reload
sudo systemctl enable outlook-ngrok.service outlook-daemon.service
sudo systemctl restart outlook-ngrok.service
sleep 3
sudo systemctl restart outlook-daemon.service

echo "=== [4/5] 等待 ngrok 隧道就绪 ==="
for i in $(seq 1 20); do
  URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['tunnels'][0]['public_url'])" 2>/dev/null || true)
  if [[ -n "$URL" && "$URL" != "None" ]]; then
    echo "NGROK_URL=$URL"
    break
  fi
  sleep 1
done

echo "=== [5/5] 验证 ==="
sudo systemctl status outlook-ngrok.service --no-pager | head -8
echo "---"
sudo systemctl status outlook-daemon.service --no-pager | head -8
echo "---"
curl -s http://127.0.0.1:8765/api/status | python3 -c "import sys,json;d=json.load(sys.stdin);print('phase:', d.get('phase'), '|', d.get('phase_message','')[:60])" 2>/dev/null || echo "dashboard not ready"
echo "=== DONE ==="
