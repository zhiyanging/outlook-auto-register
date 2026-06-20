#!/bin/bash
set -e
# 停掉旧 ngrok 服务，重新创建带 pooling 的 ngrok 服务
sudo systemctl stop outlook-ngrok 2>/dev/null || true
sudo systemctl disable outlook-ngrok 2>/dev/null || true
sudo pkill -9 ngrok 2>/dev/null || true
sleep 2

# 重新创建 ngrok 服务
sudo tee /etc/systemd/system/outlook-ngrok.service > /dev/null <<EOF
[Unit]
Description=Outlook Dashboard ngrok tunnel (pooling)
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ngrok http 8765 --pooling-enabled --log=stdout --log-format=logfmt
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable outlook-ngrok
sudo systemctl start outlook-ngrok
sleep 8
curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; d=json.load(sys.stdin); print([t.get('public_url') for t in d.get('tunnels',[])])"
sudo systemctl status outlook-ngrok --no-pager | head -5
