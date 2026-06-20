# -*- coding: utf-8 -*-
"""批量检测所有账号是否存在"""
import sys, os, time, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

INPUT_DIR = r"E:\API获取工具\邮箱自动批量注册\ninjemail - 副本\批量注册邮箱"

# Load all emails
emails = []
for f in sorted(os.listdir(INPUT_DIR)):
    if not f.endswith(".txt") or (f.startswith("[") and "]" in f):
        continue
    content = open(os.path.join(INPUT_DIR, f), "r", encoding="utf-8").read().strip()
    parts = re.split(r"\s*----\s*", content)
    email = parts[0].strip() if parts else ""
    rt = parts[3].strip() if len(parts) >= 4 else ""
    if rt and len(rt) > 20:
        continue  # skip has-RT
    if "@" in email:
        emails.append((email, f))

print(f"Checking {len(emails)} accounts...")

pw = sync_playwright().start()
browser = pw.chromium.launch(headless=True)
ctx = browser.new_context()

exists_list = []
not_exist_list = []
error_list = []

for i, (email, fname) in enumerate(emails):
    page = ctx.new_page()
    try:
        page.goto("https://login.live.com/", timeout=15000, wait_until="domcontentloaded")
        time.sleep(1.5)
        ei = page.wait_for_selector("#usernameEntry", timeout=6000)
        if ei:
            ei.fill(email)
            time.sleep(0.3)
            btn = page.wait_for_selector('button[type="submit"]', timeout=3000)
            btn.click()
            time.sleep(2.5)
            body = page.inner_text("body").lower()
            if "doesn't exist" in body or "找不到" in body or "couldn't find" in body:
                not_exist_list.append((email, fname))
                print(f"[{i+1}/{len(emails)}] NOT EXIST: {email}")
            elif "password" in body or "密码" in body:
                exists_list.append((email, fname))
                print(f"[{i+1}/{len(emails)}] EXISTS:    {email}")
            else:
                error_list.append((email, fname, body[:60]))
                print(f"[{i+1}/{len(emails)}] UNKNOWN:   {email}")
    except Exception as e:
        error_list.append((email, fname, str(e)[:60]))
        print(f"[{i+1}/{len(emails)}] ERROR:     {email}")
    finally:
        page.close()

browser.close()
pw.stop()

# Mark not-exist files
for email, fname in not_exist_list:
    path = os.path.join(INPUT_DIR, fname)
    new_name = f"[账号不存在] {fname}"
    new_path = os.path.join(INPUT_DIR, new_name)
    if os.path.exists(path) and not os.path.exists(new_path):
        os.rename(path, new_path)

print(f"\n{'='*60}")
print(f"EXISTS:     {len(exists_list)}")
print(f"NOT EXIST:  {len(not_exist_list)} (已标记)")
print(f"ERROR:      {len(error_list)}")

# Save results
with open(os.path.join(INPUT_DIR, "_检测结果.txt"), "w", encoding="utf-8") as f:
    f.write(f"检测时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"存在: {len(exists_list)}, 不存在: {len(not_exist_list)}, 异常: {len(error_list)}\n\n")
    f.write("=== 存在的账号 ===\n")
    for e, _ in exists_list:
        f.write(f"  {e}\n")
    f.write("\n=== 不存在的账号 ===\n")
    for e, _ in not_exist_list:
        f.write(f"  {e}\n")
    if error_list:
        f.write("\n=== 异常 ===\n")
        for e, _, err in error_list:
            f.write(f"  {e} | {err}\n")

print(f"\n结果已保存到 _检测结果.txt")
