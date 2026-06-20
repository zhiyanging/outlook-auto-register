# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
browser = pw.chromium.launch(headless=False)
ctx = browser.new_context()
page = ctx.new_page()

page.goto("https://login.live.com/", timeout=30000, wait_until="domcontentloaded")
time.sleep(3)

# Dump all input elements
inputs = page.evaluate("""() => {
    return Array.from(document.querySelectorAll('input')).map(el => ({
        name: el.name, id: el.id, type: el.type,
        placeholder: el.placeholder, aria: el.getAttribute('aria-label')
    }));
}""")
print("INPUTS:", inputs)

# Dump all buttons
buttons = page.evaluate("""() => {
    return Array.from(document.querySelectorAll('button, input[type="submit"]')).map(el => ({
        tag: el.tagName, id: el.id, type: el.type,
        text: el.innerText || el.value
    }));
}""")
print("BUTTONS:", buttons)

# Check for iframes
frames = page.frames
print(f"FRAMES: {len(frames)}")
for f in frames:
    print(f"  frame: {f.url[:100]}")

browser.close()
pw.stop()
