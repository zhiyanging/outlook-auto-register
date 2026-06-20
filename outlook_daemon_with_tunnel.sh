#!/bin/bash
# Outlook 守护 + 实况面板 + ngrok（复用原 cpa-proxy-ngrok 的 ngrok 账号/域名）
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${EMAIL_REGISTER_ROOT:-$SCRIPT_DIR}"
cd "$ROOT"

DASH_PID=""
NGROK_PID=""

cleanup() {
  [[ -n "$DASH_PID" ]] && kill "$DASH_PID" 2>/dev/null || true
  [[ -n "$NGROK_PID" ]] && kill "$NGROK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export OUTLOOK_DASHBOARD_PORT="${OUTLOOK_DASHBOARD_PORT:-8765}"
export PYTHONUNBUFFERED=1

if ! ss -tlnp 2>/dev/null | grep -q ":${OUTLOOK_DASHBOARD_PORT} "; then
  python3 "$ROOT/outlook_dashboard_server.py" &
  DASH_PID=$!
  sleep 1
else
  echo "dashboard already on port $OUTLOOK_DASHBOARD_PORT" >&2
fi

if [[ -n "${NGROK_AUTHTOKEN:-}" ]] && ! pgrep -f "ngrok http ${OUTLOOK_DASHBOARD_PORT}" >/dev/null 2>&1; then
  NGROK_CONFIG="$ROOT/runtime_outlook/ngrok.yml"
  mkdir -p "$ROOT/runtime_outlook"
  cat > "$NGROK_CONFIG" <<EOF
version: "3"
agent:
  authtoken: ${NGROK_AUTHTOKEN}
EOF
  NGROK_ARGS=("http" "$OUTLOOK_DASHBOARD_PORT" "--authtoken" "$NGROK_AUTHTOKEN" "--log=stdout" "--log-format=logfmt")
  if [[ -n "${NGROK_DOMAIN:-}" ]]; then
    NGROK_ARGS+=("--url=https://${NGROK_DOMAIN}")
  fi
  ngrok "${NGROK_ARGS[@]}" &
  NGROK_PID=$!
  sleep 3
else
  echo "WARN: NGROK_AUTHTOKEN not set; dashboard only on local port $OUTLOOK_DASHBOARD_PORT" >&2
fi

exec python3 "$ROOT/outlook_daemon.py"