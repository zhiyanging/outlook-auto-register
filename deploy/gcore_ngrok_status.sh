#!/bin/bash
KEY="/home/workspace/zo-mesh/config/keys/gcore.pem"
H="ubuntu@31.184.244.145"
ssh -i "$KEY" "$H" 'sudo systemctl status outlook-ngrok --no-pager | head -15; echo "---"; sudo journalctl -u outlook-ngrok -n 5 --no-pager | grep -i error'
