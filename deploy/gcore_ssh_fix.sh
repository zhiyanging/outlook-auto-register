#!/bin/bash
KEY="/home/workspace/zo-mesh/config/keys/gcore.pem"
HOST="ubuntu@31.184.244.145"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no"

$SSH $HOST bash -s <<'REMOTE'
set -e
echo "=== stop old ngrok ==="
sudo systemctl stop outlook-ngrok 2>/dev/null || true
sudo systemctl disable outlook-ngrok 2>/dev/null || true
sudo pkill -9 ngrok 2>/dev/null || true
sleep 2

echo "=== create ngrok service ==="
sudo tee /etc/systemd/system/outlook-ngrok.service > /dev/null <<EOF
[Unit]
Description=Outlook Dashboard ngrok tunnel
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ngrok http 8765 --log=stdout --log-format=logfmt
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable outlook-ngrok
sudo systemctl start outlook-ngrok
sleep 8

echo "=== ngrok status ==="
curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print([t.get('public_url') for t in d.get('tunnels',[])])" 2>/dev/null || echo "ngrok not ready"
sudo systemctl status outlook-ngrok --no-pager | head -5

echo "=== daemon status ==="
sudo systemctl status outlook-daemon --no-pager | head -5

echo "=== dashboard check ==="
curl -s http://127.0.0.1:8765/api/status 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('phase'), d.get('phase_message','')[:60])" 2>/dev/null || echo "dashboard not ready"
REMOTE
