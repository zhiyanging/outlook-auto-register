# -*- coding: utf-8 -*-
"""测试 batch_rt 的 _fill_and_wait 函数"""
import sys, os, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oauth_core import make_pkce_pair, ensure_scopes, DEFAULT_SCOPES
import urllib.parse

class CB(BaseHTTPRequestHandler):
    def do_GET(self):
        p = parse_qs(urlparse(self.path).query)
        self.server.code = p.get("code", [None])[0]
        self.server.err = p.get("error", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

port = 20500
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
threading.Thread(target=httpd.handle_request, daemon=True).start()

from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
ctx = browser.new_context()
page = ctx.new_page()

page.goto(auth_url, timeout=25000, wait_until="domcontentloaded")
time.sleep(2)

# 复制 batch_rt 的逻辑
pwd_input = page.query_selector('#passwordEntry, input[name="passwd"], input[type="password"]')
print(f"Step 1 - pwd_input: {pwd_input is not None}")

if pwd_input:
    pwd_input.fill(password)
    time.sleep(0.5)

    # 检查 body（submit前）
    body_before = page.inner_text("body").lower()
    print(f"Step 2 - body before click: {body_before[:100]}")
    print(f"  'incorrect' in body: {'incorrect' in body_before}")
    print(f"  'password' in body: {'password' in body_before}")

    page.click('button[type="submit"], #idSIButton9', timeout=5000)
    print("Step 3 - clicked submit")

    # 立即检查
    time.sleep(0.5)
    body_05 = page.inner_text("body").lower()
    print(f"Step 4 - body 0.5s after: {body_05[:100]}")
    print(f"  'incorrect' in body: {'incorrect' in body_05}")
    print(f"  URL: {page.url[:100]}")

    time.sleep(1.5)
    body_2 = page.inner_text("body").lower()
    print(f"Step 5 - body 2s after: {body_2[:100]}")
    print(f"  'incorrect' in body: {'incorrect' in body_2}")
    print(f"  URL: {page.url[:100]}")

    time.sleep(2)
    body_4 = page.inner_text("body").lower()
    print(f"Step 6 - body 4s after: {body_4[:100]}")
    print(f"  'incorrect' in body: {'incorrect' in body_4}")
    print(f"  URL: {page.url[:100]}")
    print(f"  code: {httpd.code is not None}")

browser.close()
pw.stop()
