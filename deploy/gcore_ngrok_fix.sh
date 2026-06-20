#!/bin/bash
KEY="/home/workspace/zo-mesh/config/keys/gcore.pem"
H="ubuntu@31.184.244.145"

# 停掉旧 ngrok
ssh -i "$KEY" "$H" "sudo systemctl stop outlook-ngrok; sudo pkill -9 ngrok; sleep 2"

# 创建新的 ngrok 服务（不指定 domain，让它随机分配）
ssh -i "$KEY" "$H" "sudo tee /etc/systemd/system/outlook-ngrok.service" <<'EOF'
[Unit]
Description=Outlook Dashboard ngrok tunnel
After=network.target outlook-daemon.service

[Service]
Type=simple
User=ubuntu
ExecStartPre=/bin/sleep 10
ExecStart=/usr/local/bin/ngrok http 8765 --log=stdout --log-format=logfmt
Restart=always
RestartSec=30
Environment=HOME=/home/ubuntu

[Install]
WantedBy=multi-user.target
EOF

ssh -i "$KEY" "$H" "sudo systemctl daemon-reload && sudo systemctl restart outlook-ngrok"
sleep 15
ssh -i "$KEY" "$H" "curl -s http://127.0.0.1:4040/api/tunnels | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get(\"tunnels\",[{}])[0].get(\"public_url\",\"not ready\"))'"