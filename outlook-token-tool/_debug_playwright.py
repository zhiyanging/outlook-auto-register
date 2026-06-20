# -*- coding: utf-8 -*-
import sys, os, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oauth_core import make_pkce_pair, ensure_scopes, DEFAULT_SCOPES
import urllib.parse

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = parse_qs(urlparse(self.path).query)
        self.server.code = p.get("code", [None])[0]
        self.server.err = p.get("error", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

port = 19200
email = "apittman85cvayjqk4levf@outlook.com"
password = "Reg2026Secure!"
client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
cv, cc = make_pkce_pair()
scopes = ensure_scopes(DEFAULT_SCOPES)

auth_url = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?" + urllib.parse.urlencode({
    "client_id": client_id, "response_type": "code",
    "redirect_uri": f"http://localhost:{port}",
    "response_mode": "query", "scope": " ".join(scopes),
    "code_challenge": cc, "code_challenge_method": "S256",
    "prompt": "login", "login_hint": email, "domain_hint": "consumers",
})

httpd = HTTPServer(("127.0.0.1", port), H)
httpd.code = None
httpd.err = None
threading.Thread(target=httpd.handle_request, daemon=True).start()

from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.launch(headless=False)
ctx = browser.new_context()
page = ctx.new_page()

print(f"Navigating...")
page.goto(auth_url, timeout=30000, wait_until="domcontentloaded")
time.sleep(3)

page.screenshot(path=r"C:\Users\XZXyuan\_dbg1.png")
print(f"URL: {page.url[:200]}")
print(f"Title: {page.title()}")

body_text = page.inner_text("body")
print(f"Body preview: {body_text[:300]}")

# Fill email
try:
    ei = page.wait_for_selector('input[name="loginfmt"]', timeout=8000)
    if ei:
        ei.fill(email)
        print("Filled email")
        btn = page.wait_for_selector("#idSIButton9", timeout=5000)
        if btn:
            btn.click()
            print("Clicked next")
            time.sleep(4)
            page.screenshot(path=r"C:\Users\XZXyuan\_dbg2.png")
            print(f"After next URL: {page.url[:200]}")
            body_text = page.inner_text("body")
            print(f"Body after next: {body_text[:300]}")
except Exception as e:
    print(f"Email error: {e}")

# Fill password
try:
    pi = page.wait_for_selector('input[name="passwd"]', timeout=8000)
    if pi:
        pi.fill(password)
        print("Filled password")
        btn = page.wait_for_selector("#idSIButton9", timeout=5000)
        if btn:
            btn.click()
            print("Clicked sign in")
            time.sleep(5)
            page.screenshot(path=r"C:\Users\XZXyuan\_dbg3.png")
            print(f"After signin URL: {page.url[:200]}")
            body_text = page.inner_text("body")
            print(f"Body after signin: {body_text[:300]}")
except Exception as e:
    print(f"Password error: {e}")

# Stay signed in
try:
    stay = page.wait_for_selector("#idSIButton9", timeout=5000)
    if stay:
        stay.click()
        print("Clicked stay signed in")
        time.sleep(5)
except:
    pass

print(f"Final URL: {page.url[:200]}")
print(f"Callback code: {httpd.code}")
print(f"Callback err: {httpd.err}")

page.screenshot(path=r"C:\Users\XZXyuan\_dbg4.png")
time.sleep(2)
browser.close()
pw.stop()
