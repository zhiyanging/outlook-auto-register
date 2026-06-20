# -*- coding: utf-8 -*-
"""直接复制 debug3 的逻辑测试"""
import sys, os, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oauth_core import make_pkce_pair, ensure_scopes, DEFAULT_SCOPES, exchange_authorization_code, save_combo_line, safe_filename
from network import NetworkClient
import urllib.parse

class CB(BaseHTTPRequestHandler):
    def do_GET(self):
        p = parse_qs(urlparse(self.path).query)
        self.server.code = p.get("code", [None])[0]
        self.server.err = p.get("error", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

port = 20700
email = "henry251z7wlwgcxb754@outlook.com"
password = "hExAZ$GsL-VdqVL+7&"
client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
cv, cc = make_pkce_pair()
scopes = ensure_scopes(DEFAULT_SCOPES)
redirect_uri = f"http://localhost:{port}"

auth_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
    "client_id": client_id, "response_type": "code",
    "redirect_uri": redirect_uri, "response_mode": "query",
    "scope": " ".join(scopes), "code_challenge": cc, "code_challenge_method": "S256",
    "prompt": "login", "login_hint": email,
})

httpd = HTTPServer(("127.0.0.1", port), CB)
httpd.code = None; httpd.err = None
print(f"httpd listening on {httpd.server_address}")
threading.Thread(target=httpd.handle_request, daemon=True).start()

from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
ctx = browser.new_context()
page = ctx.new_page()

print("1. goto")
page.goto(auth_url, timeout=25000, wait_until="domcontentloaded")
time.sleep(2)

pwd = page.query_selector('#passwordEntry, input[name="passwd"], input[type="password"]')
print(f"2. pwd: {pwd is not None}")
if pwd:
    pwd.fill(password)
    time.sleep(0.5)
    page.click('button[type="submit"], #idSIButton9', timeout=5000)
    print("3. clicked")
    time.sleep(4)
    print(f"4. url: {page.url[:100]}")
    print(f"   code: {httpd.code is not None}")

    if httpd.code:
        client = NetworkClient(timeout=30)
        tokens = exchange_authorization_code(client, "consumers", client_id, httpd.code, redirect_uri, scopes, cv)
        rt = tokens.get("refresh_token", "")
        print(f"5. RT len: {len(rt)}")
        out = os.path.join(r"E:\API获取工具\邮箱自动批量注册\ninjemail - 副本\批量注册邮箱", f"{safe_filename(email)}.txt")
        save_combo_line(tokens, out, email, password, client_id)
    else:
        print("5. no code, waiting more...")
        time.sleep(10)
        print(f"   code after 10s: {httpd.code is not None}")
        print(f"   url: {page.url[:200]}")

browser.close()
pw.stop()
