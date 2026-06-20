#!/bin/bash
# Gcore: systemd + no ngrok
set -euo pipefail
ROOT="${EMAIL_REGISTER_ROOT:-/home/ubuntu/Email-Register}"
NODE_ID="${1:-gcore}"
cd "$ROOT"
chmod +x deploy/*.sh outlook_daemon_no_ngrok.sh 2>/dev/null || true
mkdir -p runtime_outlook/logs
echo "{\"node_id\":\"$NODE_ID\",\"label\":\"Gcore VPS\"}" > runtime_outlook/node_identity.json
bash deploy/remote_install.sh "$NODE_ID" || true

DAEMON_SCRIPT="$ROOT/outlook_daemon_no_ngrok.sh"
if [[ "${USE_NGROK_HUB:-0}" == "1" ]]; then
  DAEMON_SCRIPT="$ROOT/outlook_daemon_with_tunnel.sh"
fi

UNIT=/etc/systemd/system/outlook-auto-register.service
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Outlook auto register daemon
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$ROOT
Environment=DISPLAY=:98
Environment=SUB_PROXY_FAST_START=1
Environment=PYTHONUNBUFFERED=1
Environment=OUTLOOK_DASHBOARD_PORT=8765
Environment=OUTLOOK_REGISTRAR_NODE=$NODE_ID
Environment=EMAIL_REGISTER_ROOT=$ROOT
Environment=USE_NGROK_HUB=${USE_NGROK_HUB:-0}
Environment=NGROK_AUTHTOKEN=${NGROK_AUTHTOKEN:-}
Environment=NGROK_DOMAIN=${NGROK_DOMAIN:-}
Environment=CLOUD_REGISTER_EMAIL_REMOTE=${CLOUD_REGISTER_EMAIL_REMOTE:-}
ExecStart=/bin/bash $DAEMON_SCRIPT
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now outlook-auto-register.service
sudo systemctl status outlook-auto-register.service --no-pager | head -15