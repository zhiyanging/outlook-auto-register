#!/bin/bash
KEY="/home/workspace/zo-mesh/config/keys/gcore.pem"
H="ubuntu@31.184.244.145"

echo "=== check ngrok ==="
ssh -i "$KEY" "$H" "sleep 15; curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d.get('tunnels',[{}])[0].get('public_url','not_ready'))\" 2>/dev/null || echo not_ready_yet"
