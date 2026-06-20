#!/bin/bash
KEY="/home/workspace/zo-mesh/config/keys/gcore.pem"
H="ubuntu@31.184.244.145"

# 停止 systemd ngrok 服务
ssh -i "$KEY" "$H" "sudo systemctl stop outlook-ngrok 2>/dev/null; sudo pkill -9 ngrok 2>/dev/null; true"

# 等 ngrok 服务器端释放域名
sleep 15

# 创建带 pooling 的 ngrok 服务
ssh -i "$KEY" "$H" 'sudo tee /etc/systemd/system/outlook-ngrok.service >/dev/null <<EOF
[Unit]
Description=Outlook Dashboard ngrok tunnel
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStartPre=/bin/sleep 5
ExecStart=/usr/local/bin/ngrok http 8765 --pooling-enabled --log=stdout --log-format=logfmt
Restart=always
RestartSec=60
Environment=HOME=/home/ubuntu

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl restart outlook-ngrok'

sleep 20

# 检查
URL=$(ssh -i "$KEY" "$H" "curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get(\"tunnels\",[{}])[0].get(\"public_url\",\"FAIL\"))' 2>/dev/null")
echo "Gcore ngrok: $URL"

# 验证面板
ssh -i "$KEY" "$H" "curl -s http://127.0.0.1:8765/api/status | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get(\"phase\"), d.get(\"phase_message\",\"\")[:80])' 2>/dev/null"
