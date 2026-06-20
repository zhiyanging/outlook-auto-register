# -*- coding: utf-8 -*-
"""
批量获取 Outlook refresh_token
每次全新 headless Playwright 浏览器，手动填写。
用法: py -3 batch_rt.py [--workers 1] [--timeout 120]
"""
import argparse, os, re, sys, time, threading, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from oauth_core import (
    BUILTIN_CLIENT_ID, DEFAULT_SCOPES, ensure_scopes,
    exchange_authorization_code, make_pkce_pair, save_combo_line,
    safe_filename,
)
from network import NetworkClient


@dataclass
class Result:
    email: str
    status: str = "pending"
    error: str = ""
    elapsed: float = 0.0
    rt_len: int = 0


class CB(BaseHTTPRequestHandler):
    def do_GET(self):
        p = parse_qs(urlparse(self.path).query)
        self.server.code = p.get("code", [None])[0]
        self.server.err = p.get("error", [None])[0]
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass


def process_one(email, password, client_id, port, output_dir, tenant, scopes, timeout_sec=120):
    result = Result(email=email)
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
    threading.Thread(target=httpd.handle_request, daemon=True).start()

    pw = None
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(auth_url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(2)

        # 有 login_hint 时直接在密码页
        pwd = page.query_selector('#passwordEntry, input[name="passwd"], input[type="password"]')
        if not pwd:
            ei = page.query_selector('#usernameEntry, input[name="loginfmt"]')
            if ei:
                ei.fill(email); time.sleep(0.5)
                page.click('button[type="submit"]', timeout=5000); time.sleep(3)
                pwd = page.query_selector('#passwordEntry, input[name="passwd"]')

        if not pwd:
            result.status = "error"; result.error = "no pwd input"
            result.elapsed = time.time() - t0; return result

        body = page.inner_text("body").lower()
        if "doesn't exist" in body or "找不到" in body:
            result.status = "not_exist"; result.error = "not exist"
            result.elapsed = time.time() - t0; return result

        pwd.fill(password); time.sleep(0.5)
        page.click('button[type="submit"], #idSIButton9', timeout=5000)
        time.sleep(4)

        # ── 处理 consent 授权同意页面 ──
        for _ in range(5):
            cur_url = page.url
            try:
                body_text = page.inner_text("body").lower()
            except:
                body_text = ""
            # consent 页面特征: URL 含 Consent/Update 或 body 含 "access your info" / "permission"
            is_consent = ("consent" in cur_url.lower() or
                         "access your info" in body_text or
                         ("permission" in body_text and "accept" in body_text))
            if is_consent:
                # 找 Accept / Yes / Allow 按钮并点击
                try:
                    btn = page.query_selector('button:has-text("Accept"), input[type="submit"][value*="Accept"], button:has-text("Yes"), button:has-text("Allow")')
                    if btn:
                        btn.click()
                        time.sleep(3)
                        continue
                except:
                    pass
                # fallback: 找所有 button 看哪个是 Accept
                for btn in page.query_selector_all("button"):
                    txt = (btn.inner_text() or "").strip().lower()
                    if txt in ("accept", "yes", "allow", "同意", "允许"):
                        btn.click()
                        time.sleep(3)
                        break
                time.sleep(2)
                continue
            break

        # 等回调
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
            if browser: browser.close()
        except: pass
        try:
            if pw: pw.stop()
        except: pass
        try: httpd.server_close()
        except: pass
        # 清理 Playwright 残留的 Chrome 孤儿进程
        try:
            import subprocess
            subprocess.run(["pkill", "-9", "-f", "chromium.*playwright"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass

    result.elapsed = time.time() - t0
    return result


def load_credentials(input_dir):
    creds = []
    for f in sorted(os.listdir(input_dir)):
        if not f.endswith(".txt"): continue
        if f.startswith("[") and "]" in f: continue
        if f.startswith("_"): continue
        try:
            content = open(os.path.join(input_dir, f), "r", encoding="utf-8").read().strip()
        except: continue
        if not content: continue
        parts = re.split(r"\s*----\s*", content)
        if len(parts) < 2: continue
        email = parts[0].strip()
        pwd = parts[1].strip()
        cid = parts[2].strip() if len(parts) >= 3 else BUILTIN_CLIENT_ID
        rt = parts[3].strip() if len(parts) >= 4 else ""
        if rt and len(rt) > 20: continue
        if "@" not in email: continue
        creds.append((email, pwd, cid))
    return creds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=r"E:\API获取工具\邮箱自动批量注册\ninjemail - 副本\批量注册邮箱")
    parser.add_argument("--output-dir", default=r"E:\API获取工具\邮箱自动批量注册\ninjemail - 副本\批量注册邮箱")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--start-port", type=int, default=20000)
    parser.add_argument("--tenant", default="consumers")
    args = parser.parse_args()

    scopes = ensure_scopes(DEFAULT_SCOPES)
    creds = load_credentials(args.input_dir)
    print(f"Loaded {len(creds)} accounts")
    if not creds:
        print("Nothing to do"); return

    stats = {}

    def worker(item):
        idx, (email, pwd, cid) = item
        return process_one(email, pwd, cid, args.start_port + idx,
                           args.output_dir, args.tenant, scopes, args.timeout)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, (i, c)): c for i, c in enumerate(creds)}
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            stats[r.status] = stats.get(r.status, 0) + 1
            tag = {"success": "OK", "not_exist": "NE", "wrong_password": "WP",
                   "timeout": "TO", "error": "ER"}.get(r.status, "??")
            info = f"RT={r.rt_len}" if r.status == "success" else r.error[:50]
            print(f"[{done}/{len(creds)}] {tag} {r.email} - {r.status} ({r.elapsed:.1f}s) {info}")
            if r.status == "not_exist":
                src = os.path.join(args.output_dir, f"{safe_filename(r.email)}.txt")
                if os.path.exists(src):
                    dst = os.path.join(args.output_dir, f"[不存在] {safe_filename(r.email)}.txt")
                    if not os.path.exists(dst): os.rename(src, dst)

    print(f"\nTotal: {len(creds)}")
    for k, v in sorted(stats.items()):
        if v: print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
