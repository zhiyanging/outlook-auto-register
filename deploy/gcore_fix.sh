#!/bin/bash
# 修复 gcore ngrok + daemon
set -e

echo "=== kill old ngrok ==="
sudo pkill -9 ngrok 2>/dev/null || true
sleep 2

echo "=== restart ngrok ==="
sudo systemctl restart outlook-ngrok
sleep 5

echo "=== check ngrok ==="
curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
for t in d.get('tunnels',[]):
    if t.get('public_url'):
        print('Gcore ngrok:', t['public_url'])
        break
else:
    print('Gcore ngrok: not ready')
"

echo "=== kill old daemon ==="
sudo pkill -9 -f "outlook_daemon_no_ngrok" 2>/dev/null || true
sleep 1

echo "=== restart daemon ==="
sudo systemctl restart outlook-daemon
sleep 3

echo "=== check daemon ==="
sudo systemctl status outlook-daemon --no-pager | head -8

echo "=== check dashboard ==="
curl -s http://127.0.0.1:8765/ | head -c 200
