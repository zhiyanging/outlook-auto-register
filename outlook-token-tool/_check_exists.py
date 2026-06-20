# -*- coding: utf-8 -*-
"""快速检测哪些账号存在"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

emails = [
    "apittman85cvayjqk4levf@outlook.com",
    "aprilwang04qex52dxy5e@outlook.com",
    "ataylor69wmy5qikih5zm@outlook.com",
    "avery0408hu26b8on6yw1zw@outlook.com",
    "brandonwhite664as5pfi0ir5@outlook.com",
    "henry251z7wlwgcxb754@outlook.com",
    "mxgregbrow94xyuv2v54vmxe@outlook.com",
    "reedhanymo2y2iqx7njaoj9@outlook.com",
    "smartinez831fh9xcnkphg@outlook.com",
    "eleancolemajxv1kgaq2jhlw@outlook.com",
]

pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
ctx = browser.new_context()

for email in emails:
    page = ctx.new_page()
    try:
        page.goto("https://login.live.com/", timeout=20000, wait_until="domcontentloaded")
        time.sleep(2)
        ei = page.wait_for_selector("#usernameEntry", timeout=8000)
        if ei:
            ei.fill(email)
            time.sleep(0.5)
            btn = page.wait_for_selector('button[type="submit"]', timeout=3000)
            btn.click()
            time.sleep(3)
            body = page.inner_text("body").lower()
            if "doesn't exist" in body or "找不到" in body or "couldn't find" in body:
                print(f"NOT EXIST: {email}")
            elif "password" in body or "密码" in body:
                print(f"EXISTS:    {email}")
            else:
                print(f"UNKNOWN:   {email} | {body[:80]}")
    except Exception as e:
        print(f"ERROR:     {email} | {str(e)[:60]}")
    finally:
        page.close()

browser.close()
pw.stop()
