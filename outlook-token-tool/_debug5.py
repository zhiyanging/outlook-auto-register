# -*- coding: utf-8 -*-
"""debug5: inline exact same code as _debug4 but as a function"""
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

def process_one(email, password, client_id, port, output_dir, tenant, scopes, timeout_sec=60):
    from dataclasses import dataclass
    @dataclass
    class R:
        email: str; status: str = "pending"; error: str = ""; elapsed: float = 0.0; rt_len: int = 0
    result = R(email=email)
    t0 = time.time()

    redirect_uri = f"http://localhost:{port}"
    cv, cc = make_pkce_pair()
    state = os.urandom(18).hex()
    auth_url = "https://login.microsoftonline.com/{}/oauth2/v2.0/authorize?{}".format(
        tenant, urllib.parse.urlencode({
            "client_id": client_id, "response_type": "code",
            "redirect_uri": redirect_uri, "response_mode": "query",
            "scope": " ".join(scopes), "state": state,
            "code_challenge": cc, "code_challenge_method": "S256",
            "prompt": "login", "login_hint": email,
        }))

    httpd = HTTPServer(("127.0.0.1", port), CB)
    httpd.code = None; httpd.err = None
    print(f"  httpd on {httpd.server_address}")
    threading.Thread(target=httpd.handle_request, daemon=True).start()

    pw = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        print(f"  navigating...")
        page.goto(auth_url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(2)

        pwd = page.query_selector('#passwordEntry, input[name="passwd"], input[type="password"]')
        print(f"  pwd: {pwd is not None}")
        if not pwd:
            ei = page.query_selector('#usernameEntry, input[name="loginfmt"]')
            if ei:
                ei.fill(email); time.sleep(0.5)
                page.click('button[type="submit"]', timeout=5000); time.sleep(3)
                pwd = page.query_selector('#passwordEntry, input[name="passwd"]')
                print(f"  pwd after email: {pwd is not None}")

        if not pwd:
            result.status = "error"; result.error = "no pwd"
            result.elapsed = time.time() - t0; return result

        body = page.inner_text("body").lower()
        if "doesn't exist" in body or "找不到" in body:
            result.status = "not_exist"; result.error = "not exist"
            result.elapsed = time.time() - t0; return result

        pwd.fill(password); time.sleep(0.5)
        page.click('button[type="submit"], #idSIButton9', timeout=5000)
        print(f"  clicked submit")
        time.sleep(4)
        print(f"  url: {page.url[:80]}")
        print(f"  code: {httpd.code is not None}")

        # Wait for code
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if httpd.code: break
            if httpd.err:
                result.status = "error"; result.error = f"oauth: {httpd.err}"
                result.elapsed = time.time() - t0; return result
            cur_url = page.url
            if "localhost" not in cur_url:
                try:
                    body = page.inner_text("body").lower()
                    if "incorrect" in body:
                        result.status = "wrong_password"; result.error = "wrong pwd"
                        result.elapsed = time.time() - t0; return result
                except: pass
            time.sleep(1)

        if not httpd.code:
            result.status = "timeout"; result.error = "timeout"
            result.elapsed = time.time() - t0; return result

        client = NetworkClient(timeout=30)
        tokens = exchange_authorization_code(client, tenant, client_id, httpd.code, redirect_uri, scopes, cv)
        rt = tokens.get("refresh_token", "")
        if not rt:
            result.status = "error"; result.error = "no rt"
        else:
            result.status = "success"; result.rt_len = len(rt)
            out = os.path.join(output_dir, f"{safe_filename(email)}.txt")
            save_combo_line(tokens, out, email, password, client_id)
    except Exception as e:
        result.status = "error"; result.error = str(e)[:150]
    finally:
        try:
            if pw: pw.stop()
        except: pass
        try: httpd.server_close()
        except: pass

    result.elapsed = time.time() - t0
    return result


email = "henry251z7wlwgcxb754@outlook.com"
password = "hExAZ$GsL-VdqVL+7&"
client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
scopes = ensure_scopes(DEFAULT_SCOPES)

r = process_one(email, password, client_id, 20900,
    r'E:\API获取工具\邮箱自动批量注册\ninjemail - 副本\批量注册邮箱',
    'consumers', scopes, 60)
print(f"{r.status} | RT={r.rt_len} | {r.error} | {r.elapsed:.1f}s")
