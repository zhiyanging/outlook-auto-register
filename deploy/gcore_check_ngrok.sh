#!/bin/bash
sleep 20
curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tunnels',[{}])[0].get('public_url','not ready'))"