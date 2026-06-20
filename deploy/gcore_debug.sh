#!/bin/bash
KEY="/home/workspace/zo-mesh/config/keys/gcore.pem"
H="ubuntu@31.184.244.145"

echo "=== ps ngrok ==="
ssh -i "$KEY" "$H" "ps aux | grep ngrok | grep -v grep"
echo "=== journalctl ==="
ssh -i "$KEY" "$H" "journalctl -u outlook-ngrok -n 5 --no-pager"
