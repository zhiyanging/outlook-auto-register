#!/bin/bash
# 在 zo2/zo3 上启用 Email-Register 守护（替换旧 outlook-auto-register 路径）
set -euo pipefail
ROOT="${EMAIL_REGISTER_ROOT:-/home/workspace/Email-Register}"
NODE_ID="${OUTLOOK_REGISTRAR_NODE:-unknown}"
USE_HUB="${USE_NGROK_HUB:-0}"
if [[ "$USE_HUB" == "1" ]]; then
  DAEMON_SCRIPT="${ROOT}/outlook_daemon_with_tunnel.sh"
else
  DAEMON_SCRIPT="${ROOT}/outlook_daemon_no_ngrok.sh"
fi
CONF="/etc/zo/supervisord-user.conf"
PROG="outlook-auto-register-daemon"

BLOCK="
[program:${PROG}]
command=/bin/bash ${DAEMON_SCRIPT}
directory=${ROOT}
environment=DISPLAY=\":98\",SUB_PROXY_FAST_START=\"1\",PYTHONUNBUFFERED=\"1\",OUTLOOK_DASHBOARD_PORT=\"8765\",OUTLOOK_REGISTRAR_NODE=\"${NODE_ID}\",EMAIL_REGISTER_ROOT=\"${ROOT}\",USE_NGROK_HUB=\"${USE_HUB}\",NGROK_AUTHTOKEN=\"${NGROK_AUTHTOKEN:-}\",NGROK_DOMAIN=\"${NGROK_DOMAIN:-}\",CLOUD_REGISTER_EMAIL_REMOTE=\"${CLOUD_REGISTER_EMAIL_REMOTE:-}\"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=8
stdout_logfile=/dev/shm/${PROG}.log
stderr_logfile=/dev/shm/${PROG}_err.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
"

# 停旧服务名
supervisorctl -c /etc/zo/supervisord-user.conf stop outlook-register-daemon 2>/dev/null || true
supervisorctl -c /etc/zo/supervisord-user.conf stop "${PROG}" 2>/dev/null || true

TMP="$(mktemp)"
awk -v prog="${PROG}" '
  $0 == "[program:" prog "]" {skip=1; next}
  skip && $0 ~ /^\[program:/ {skip=0}
  !skip {print}
' "$CONF" > "$TMP"
cat "$TMP" > "$CONF"
rm -f "$TMP"
echo "$BLOCK" >> "$CONF"
echo "installed refreshed ${PROG} supervisor block"

supervisorctl -c /etc/zo/supervisord-user.conf reread
supervisorctl -c /etc/zo/supervisord-user.conf update
supervisorctl -c /etc/zo/supervisord-user.conf restart "${PROG}" 2>/dev/null || supervisorctl -c /etc/zo/supervisord-user.conf start "${PROG}"
supervisorctl -c /etc/zo/supervisord-user.conf status "${PROG}" outlook-register-daemon 2>/dev/null | head -5