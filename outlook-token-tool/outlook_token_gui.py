# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.parse
import webbrowser

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:
    PlaywrightTimeoutError = Exception
    sync_playwright = None

from network import HttpResponseError, NetworkClient, NetworkConnectionError
from oauth_core import (
    AccountMismatchError,
    BUILTIN_CLIENT_ID,
    BUILTIN_CLIENT_NAME,
    DEFAULT_SCOPES,
    DeviceAuthorizationError,
    OAuthCallbackHandler,
    ReusableTCPServer,
    account_from_tokens,
    ensure_account_matches,
    ensure_scopes,
    exchange_authorization_code,
    find_available_port,
    is_port_available,
    make_pkce_pair,
    mask_token,
    poll_device_code,
    refresh_access_token,
    request_device_code,
    save_combo_line,
    token_output_path,
)


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
DEFAULT_OUTPUT_DIR = os.path.join(APP_DIR, "邮箱")
MICROSOFT_PERSONAL_DOMAINS = {
    "outlook.com",
    "outlook.com.cn",
    "hotmail.com",
    "hotmail.co.uk",
    "hotmail.de",
    "hotmail.es",
    "hotmail.fr",
    "hotmail.it",
    "live.com",
    "live.cn",
    "msn.com",
}

TENANT_LABELS = {
    "消费者个人账户": "consumers",
    "通用（个人/组织）": "common",
    "仅组织账户": "organizations",
}
TENANT_VALUES = {value: key for key, value in TENANT_LABELS.items()}

BROWSER_MODE_LABELS = {
    "默认浏览器": "default",
    "私密窗口": "private",
    "独立会话": "isolated",
}
BROWSER_MODE_VALUES = {value: key for key, value in BROWSER_MODE_LABELS.items()}

THREE_INFO_SPLIT_RE = re.compile(r"\s*----\s*|\r?\n|\t")


def load_config() -> dict:
    default = {
        "account_email": "",
        "account_password": "",
        "tenant": "consumers",
        "client_id": BUILTIN_CLIENT_ID,
        "refresh_token": "",
        "output_dir": DEFAULT_OUTPUT_DIR,
        "auto_name": True,
        "port": 8765,
        "timeout": 180,
        "scopes": list(DEFAULT_SCOPES),
        "browser_mode": "default",
        "concurrency": 3,
    }
    if not os.path.exists(CONFIG_PATH):
        return default
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default

    if isinstance(data, dict):
        default.update(data)
    output_dir = str(default.get("output_dir") or "").strip()
    if not output_dir:
        default["output_dir"] = DEFAULT_OUTPUT_DIR
    elif "鑾峰彇" in output_dir or "閭" in output_dir or "锛" in output_dir:
        # Older runs wrote mojibake into config; prefer a valid local folder.
        default["output_dir"] = DEFAULT_OUTPUT_DIR

    scopes = default.get("scopes")
    if isinstance(scopes, str):
        default["scopes"] = [item for item in scopes.split() if item]
    elif isinstance(scopes, (list, tuple)):
        default["scopes"] = [str(item).strip() for item in scopes if str(item).strip()]
    else:
        default["scopes"] = list(DEFAULT_SCOPES)
    return default


def save_config(data: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_refresh_token_from_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        raise RuntimeError("凭证文件为空")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        parts = raw.split("----")
        refresh = parts[3].strip() if len(parts) >= 4 else ""
        if not refresh:
            raise RuntimeError("凭证文件里没有 refresh_token")
        return refresh
    refresh = str(data.get("refresh_token", "")).strip()
    if not refresh:
        raise RuntimeError("凭证文件里没有 refresh_token")
    return refresh


def clean_scanned_outlook_email(value: str) -> str:
    email = (value or "").strip().strip(".,;:()[]{}<>\"'")
    if email.count("@") != 1:
        return ""
    local, domain = email.split("@", 1)
    local = local.strip()
    domain = domain.strip().lower()
    if not local or not domain:
        return ""
    if domain not in MICROSOFT_PERSONAL_DOMAINS:
        return ""
    return f"{local}@{domain}"


def text_from_registry_value(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        chunks: list[str] = []
        for encoding in ("utf-16le", "utf-8", "latin1"):
            try:
                chunks.append(value.decode(encoding, errors="ignore"))
            except Exception:
                continue
        return "\n".join(chunks)
    try:
        return str(value)
    except Exception:
        return ""


def find_local_outlook_emails(max_nodes_per_root: int = 2500) -> list[str]:
    import winreg

    found: set[str] = set()

    def add_from_text(text: str) -> None:
        for match in EMAIL_RE.findall(text or ""):
            email = clean_scanned_outlook_email(match)
            if email:
                found.add(email)

    def scan_root(path: str, depth: int) -> None:
        scanned = 0

        def scan_key(root, key_path: str, current_depth: int) -> None:
            nonlocal scanned
            if current_depth < 0 or scanned >= max_nodes_per_root:
                return
            try:
                key = winreg.OpenKey(root, key_path)
            except OSError:
                return
            with key:
                scanned += 1
                try:
                    subkey_count, value_count, _ = winreg.QueryInfoKey(key)
                except OSError:
                    return

                for index in range(value_count):
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        continue
                    add_from_text(name)
                    add_from_text(text_from_registry_value(value))

                for index in range(subkey_count):
                    try:
                        child = winreg.EnumKey(key, index)
                    except OSError:
                        continue
                    add_from_text(child)
                    scan_key(root, key_path + "\\" + child, current_depth - 1)

        scan_key(winreg.HKEY_CURRENT_USER, path, depth)

    roots = [
        (r"Software\Microsoft\IdentityCRL\UserExtendedProperties", 8),
        (r"Software\Microsoft\OneAuth\Accounts", 8),
        (r"Software\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles", 8),
        (r"Software\Microsoft\Office", 6),
    ]
    for root_path, depth in roots:
        scan_root(root_path, depth)
    return sorted(found)


def browser_candidates() -> list[tuple[str, str, str]]:
    env = os.environ
    return [
        ("Edge", os.path.join(env.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"), "--inprivate"),
        ("Edge", os.path.join(env.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"), "--inprivate"),
        ("Chrome", os.path.join(env.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"), "--incognito"),
        ("Chrome", os.path.join(env.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"), "--incognito"),
        ("Chrome", os.path.join(env.get("LocalAppData", ""), "Google", "Chrome", "Application", "chrome.exe"), "--incognito"),
        ("Firefox", os.path.join(env.get("ProgramFiles", ""), "Mozilla Firefox", "firefox.exe"), "-private-window"),
        ("Firefox", os.path.join(env.get("ProgramFiles(x86)", ""), "Mozilla Firefox", "firefox.exe"), "-private-window"),
    ]


def open_private_browser(url: str) -> str:
    for name, path, private_arg in browser_candidates():
        if not os.path.exists(path):
            continue
        subprocess.Popen(
            [path, private_arg, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return f"{name} 私密窗口"
    webbrowser.open(url)
    return "默认浏览器"


def open_isolated_browser(url: str) -> str:
    for name, path, private_arg in browser_candidates():
        if not os.path.exists(path):
            continue
        profile_dir = tempfile.mkdtemp(prefix="outlook-token-tool-browser-")
        lower = path.lower()
        if "firefox" in lower:
            args = [path, "-profile", profile_dir, "-private-window", url]
        else:
            args = [path, f"--user-data-dir={profile_dir}", "--no-first-run", private_arg, url]
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return f"{name} 独立会话"
    webbrowser.open(url)
    return "默认浏览器"


def launch_browser(url: str, mode: str) -> str:
    if mode == "private":
        return open_private_browser(url)
    if mode == "isolated":
        return open_isolated_browser(url)
    webbrowser.open(url)
    return "默认浏览器"


def _log_browser(on_log, message: str) -> None:
    if on_log:
        try:
            on_log(message)
        except Exception:
            pass


def _first_visible_locator(page, selectors: list[str]):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=400):
                return locator
        except Exception:
            continue
    return None


def _page_text_contains(page, keywords: list[str]) -> bool:
    try:
        text = (page.locator("body").inner_text(timeout=800) or "").lower()
    except Exception:
        return False
    return any(keyword.lower() in text for keyword in keywords)


def _page_text(page) -> str:
    try:
        return (page.locator("body").inner_text(timeout=800) or "").lower()
    except Exception:
        return ""


def _has_visible_selector(page, selectors: list[str]) -> bool:
    return _first_visible_locator(page, selectors) is not None


def classify_microsoft_login_page(page) -> str:
    """返回: login_email | password | recovery_email | two_factor | stay_signed_in | logged_in | consent | error | unknown"""
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""
    text = _page_text(page)

    login_email_selectors = [
        "input#i0116",
        "input[name='loginfmt']",
        "input[autocomplete='username']",
    ]
    password_selectors = [
        "input#i0118",
        "input[name='passwd']",
        "input#passwordEntry",
        "input[autocomplete='current-password']",
    ]
    recovery_selectors = [
        "input[name='ProofConfirmation']",
        "input[name='proof']",
        "input[data-testid='proof-input']",
        "input[name='email']",
    ]
    two_factor_selectors = [
        "input[name='otc']",
        "input[name='code']",
        "input[inputmode='numeric']",
        "div[data-bind*='Type.Authenticator']",
    ]

    # 错误页面检测（账号不存在、密码错误、账号被锁等）
    error_keywords = [
        "找不到 microsoft 帐户", "找不到该账户", "找不到此账户",
        "that microsoft account doesn't exist", "account doesn't exist",
        "we couldn't find an account", "no account found",
        "that account doesn't exist", "this account doesn't exist",
        "请尝试重新输入", "please try again",
        "your account or password is incorrect", "密码不正确",
        "incorrect password", "wrong password",
        "account has been locked", "帐户已被锁定",
        "your account has been blocked", "帐户已被阻止",
        "sign-in is blocked", "登录被阻止",
        "too many attempts", "尝试次数过多",
    ]
    if any(k in text for k in error_keywords):
        return "error"
    # 也检查页面上是否有错误提示元素
    try:
        err_el = page.locator("#usernameError, #passwordError, .alert-error, [data-testid='error'], #errorText").first
        if err_el.is_visible(timeout=300):
            err_text = (err_el.text_content(timeout=300) or "").lower()
            if err_text and len(err_text) > 3:
                return "error"
    except Exception:
        pass

    if any(domain in current_url for domain in [
        "outlook.live.com", "outlook.office.com", "outlook.office365.com",
    ]):
        return "logged_in"
    # 检测 "保持登录？" 页面
    if any(k in text for k in [
        "保持登录", "保持登入", "do this to reduce", "让登录保持", "让应用保持登录",
        "stay signed in", "keep you signed in", "stay signed in?",
    ]):
        return "stay_signed_in"

    if any(key in current_url for key in ["proofup", "addproofs", "security-info", "recover", "identity/confirm", "abuse"]):
        return "recovery_email"

    if _has_visible_selector(page, recovery_selectors):
        return "recovery_email"
    if any(k in text for k in [
        "辅助邮箱", "恢复邮箱", "安全邮箱", "备用邮箱", "备用电子邮件", "辅助电子邮件", "恢复电子邮件",
        "验证你的电子邮件", "验证你的邮箱", "输入备用电子邮件", "输入你的电子邮件地址", "输入电子邮件地址",
        "我们将向此电子邮件发送验证码", "发送代码", "发送验证码", "确认你的身份", "验证你的身份",
        "保护你的帐户", "帮助我们保护你的帐户", "enter a recovery email", "recovery email",
        "alternate email", "security email", "verify your email", "enter your email address",
        "verify your identity", "help us protect your account", "protect your account", "add security info", "security info"
    ]):
        return "recovery_email"

    if _has_visible_selector(page, two_factor_selectors) or any(k in text for k in [
        "microsoft authenticator", "批准请求", "approve sign in request", "双重验证", "两步验证", "二步验证", "验证码", "authenticator"
    ]):
        return "two_factor"

    # Consent/权限同意页面（account.live.com/Consent/Update 等）
    if any(k in current_url for k in ["consent", "/Consent/"]) or any(k in text for k in [
        "允许此应用", "权限请求", "will be able to", "review the requested",
        "read your email", "access your data", "查看你的电子邮件", "访问你的数据",
        "requested permissions", "请求的权限", "应用权限",
    ]):
        return "consent"

    if _has_visible_selector(page, password_selectors):
        return "password"

    if _has_visible_selector(page, login_email_selectors):
        return "login_email"

    return "unknown"


def detect_microsoft_login_blocker(page) -> str:
    state = classify_microsoft_login_page(page)
    if state == "recovery_email":
        return "recovery_email"
    if state == "two_factor":
        return "two_factor"
    return ""


def _click_first_visible(page, selectors: list[str], timeout: int = 2000) -> bool:
    """尝试点击第一个可见元素，成功返回True"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=timeout):
                loc.click()
                return True
        except Exception:
            continue
    return False


class AutofillSkipError(Exception):
    """autofill遇到不可恢复的错误，应跳过此账号"""
    pass


def autofill_microsoft_login(page, email: str = "", password: str = "", device_code: str = "", on_log=None, stop_event: threading.Event | None = None, skip_event: threading.Event | None = None, browser_context=None) -> None:
    """微软登录自动填写：按步骤处理，浏览器保持打开直到回调URL到达。
    流程：邮箱 -> 密码 -> 保持登录 -> Consent -> 回调URL
    遇到不可恢复错误（账号不存在、密码错误等）抛出 AutofillSkipError。
    """
    email = (email or "").strip()
    password = password or ""
    device_code = (device_code or "").strip()
    TIMEOUT = 180
    start_time = time.time()

    def elapsed():
        return int(time.time() - start_time)

    def timed_out():
        return time.time() - start_time > TIMEOUT

    def check_stop():
        return (stop_event and stop_event.is_set()) or timed_out()

    def _safe_fill(locator, value, field_name):
        """安全填写：等可见 -> 清空 -> 逐字符输入 -> 验证"""
        try:
            locator.wait_for(state="visible", timeout=5000)
        except Exception:
            raise AutofillSkipError(f"{field_name}输入框不可见")
        try:
            locator.click()
            time.sleep(0.2)
            locator.fill("")
            time.sleep(0.1)
            locator.type(value, delay=30)
            time.sleep(0.3)
            # 验证填写结果
            actual = locator.input_value(timeout=1000)
            if actual and actual.strip() == value.strip():
                return True
            # type 可能失败，回退到 fill
            locator.fill(value)
            time.sleep(0.3)
            actual2 = locator.input_value(timeout=1000)
            if actual2 and value.strip() in actual2.strip():
                return True
            _log_browser(on_log, f"[警告] {field_name}填写可能不完整: 期望'{value}' 实际'{actual2}'")
            return True
        except AutofillSkipError:
            raise
        except Exception as exc:
            raise AutofillSkipError(f"{field_name}填写失败: {exc}")

    def _get_error_text():
        """获取页面上的错误提示文本"""
        try:
            for sel in ["#usernameError", "#passwordError", ".alert-error", "[data-testid='error']", "#errorText", "#idTd_ErrorMsg_Error"]:
                el = page.locator(sel).first
                if el.is_visible(timeout=500):
                    t = (el.text_content(timeout=500) or "").strip()
                    if t and len(t) > 2:
                        return t
        except Exception:
            pass
        return ""

    # ====== 步骤1：填写邮箱 ======
    # 等页面加载完成
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    time.sleep(0.5)

    state = classify_microsoft_login_page(page)
    if state == "error":
        err = _get_error_text() or "页面错误"
        raise AutofillSkipError(f"邮箱页面错误: {err}")

    if state == "login_email" and email:
        _log_browser(on_log, f"[步骤1] 填写邮箱: {email}")
        email_input = _first_visible_locator(page, [
            "input#i0116", "input[name='loginfmt']", "input[autocomplete='username']"
        ])
        if email_input is None:
            raise AutofillSkipError("找不到邮箱输入框")
        _safe_fill(email_input, email, "邮箱")
        time.sleep(0.3)
        _click_first_visible(page, ["button[type='submit']", "input[type='submit']", "#idSIButton9"], timeout=3000)
        _log_browser(on_log, "[步骤1] 邮箱已提交")
        # 等待页面跳转
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        time.sleep(1.0)
        # 检查错误
        state = classify_microsoft_login_page(page)
        if state == "error":
            err = _get_error_text() or "账号不存在或被锁定"
            raise AutofillSkipError(f"邮箱验证失败: {err}")

    # ====== 步骤2：填写密码 ======
    if check_stop():
        return
    state = classify_microsoft_login_page(page)
    if state == "error":
        err = _get_error_text() or "页面错误"
        raise AutofillSkipError(f"密码页面错误: {err}")
    if state == "password" and password:
        _log_browser(on_log, "[步骤2] 填写密码")
        pwd_input = _first_visible_locator(page, [
            "input#i0118", "input[name='passwd']", "input#passwordEntry", "input[autocomplete='current-password']"
        ])
        if pwd_input is None:
            raise AutofillSkipError("找不到密码输入框")
        _safe_fill(pwd_input, password, "密码")
        time.sleep(0.3)
        _click_first_visible(page, ["button[type='submit']", "input[type='submit']", "#idSIButton9"], timeout=3000)
        _log_browser(on_log, "[步骤2] 密码已提交，等待页面跳转...")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        time.sleep(1.5)
        # 检查错误
        state = classify_microsoft_login_page(page)
        if state == "error":
            err = _get_error_text() or "密码错误或账号被锁"
            raise AutofillSkipError(f"密码验证失败: {err}")

    # ====== 步骤3：处理登录后页面（保持登录/Consent/二次验证），直到回调URL ======
    _log_browser(on_log, "[步骤3] 等待授权完成...")
    last_state = ""
    consent_attempts = 0

    while not check_stop():
        # 检查是否有新标签页
        if browser_context is not None:
            try:
                all_pages = browser_context.pages
                if len(all_pages) > 1:
                    for p in reversed(all_pages):
                        try:
                            purl = p.url
                            if purl and "about:blank" not in purl:
                                if p != page:
                                    _log_browser(on_log, f"[步骤3] 发现新标签页: {purl[:80]}")
                                    page = p
                                break
                        except Exception:
                            continue
            except Exception:
                pass

        try:
            current_url = page.url
        except Exception:
            if browser_context is not None:
                try:
                    all_pages = browser_context.pages
                    if all_pages:
                        page = all_pages[-1]
                        current_url = page.url
                    else:
                        break
                except Exception:
                    break
            else:
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                continue

        # 已到达回调URL
        if "localhost" in current_url and "code=" in current_url:
            _log_browser(on_log, "[完成] 已到达回调URL")
            return

        state = classify_microsoft_login_page(page)

        # 错误检测
        if state == "error":
            err = _get_error_text() or "登录过程出错"
            raise AutofillSkipError(f"登录错误: {err}")

        if state != last_state:
            _log_browser(on_log, f"[步骤3] state={state} url={current_url[:80]}")
            last_state = state

        # Passkey弹窗取消
        _click_first_visible(page, [
            "input#idBtn_Back",
            "button:has-text('取消')",
            "button:has-text('Cancel')",
        ], timeout=200)

        # 保持登录
        if state == "stay_signed_in":
            _log_browser(on_log, "[步骤3] 检测到'保持登录'，点击'是'")
            if _click_first_visible(page, [
                "#idSIButton9",
                "button:has-text('是')",
                "button:has-text('Yes')",
                "button:has-text('下一步')",
                "button:has-text('Next')",
            ], timeout=3000):
                _log_browser(on_log, "[步骤3] 已点击保持登录")
                try:
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                continue

        # Consent/权限同意
        if state == "consent":
            consent_attempts += 1
            if consent_attempts <= 3:
                _log_browser(on_log, f"[步骤3] 权限同意页面，点击'接受' (第{consent_attempts}次)")
                try:
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                clicked = _click_first_visible(page, [
                    "#idSIButton9",
                    "button:has-text('接受')",
                    "button:has-text('Accept')",
                    "button:has-text('允许')",
                    "button:has-text('Allow')",
                    "button:has-text('同意')",
                    "button:has-text('Approve')",
                    "input[type='submit'][value*='Accept']",
                    "input[type='submit'][value*='Allow']",
                    "[data-testid='primaryButton']",
                    "button[type='submit']",
                ], timeout=3000)
                if clicked:
                    _log_browser(on_log, "[步骤3] 已点击同意按钮")
                    try:
                        page.wait_for_timeout(3000)
                    except Exception:
                        pass
                    continue
            # 3次失败后等人工
            try:
                page.wait_for_timeout(3000)
            except Exception:
                pass
            continue

        # 辅助邮箱验证
        if state == "recovery_email":
            _log_browser(on_log, "[步骤3] 辅助邮箱验证，尝试跳过")
            if _click_first_visible(page, [
                "button:has-text('跳过')",
                "a:has-text('跳过')",
                "button:has-text('Skip')",
                "a:has-text('Skip')",
                "#CancelLink",
            ], timeout=2000):
                _log_browser(on_log, "[步骤3] 已点击跳过")
                try:
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                continue
            else:
                _log_browser(on_log, "[步骤3] 无法跳过，等待人工处理")
                if skip_event:
                    skip_event.set()
                try:
                    page.wait_for_timeout(3000)
                except Exception:
                    pass
                continue

        # 二次验证
        if state == "two_factor":
            if device_code:
                _log_browser(on_log, "[步骤3] 二次验证，自动填写验证码")
                try:
                    code_input = _first_visible_locator(page, [
                        "input[name='otc']", "input[name='code']", "input[type='tel']", "input[inputmode='numeric']"
                    ])
                    if code_input:
                        _safe_fill(code_input, device_code, "验证码")
                        _click_first_visible(page, ["button[type='submit']", "#idSIButton9"], timeout=2000)
                        try:
                            page.wait_for_load_state("domcontentloaded")
                            page.wait_for_timeout(1000)
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass
            _log_browser(on_log, "[步骤3] 二次验证，等待人工处理...")
            try:
                page.wait_for_timeout(3000)
            except Exception:
                pass
            continue

        # 已登录
        if state == "logged_in":
            _log_browser(on_log, "[步骤3] 已登录，等待回调...")
            try:
                page.wait_for_timeout(2000)
            except Exception:
                pass
            continue

        # 未知状态
        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass

    _log_browser(on_log, "[超时] 等待授权完成超时 (%d秒)" % elapsed())


# 并发安全的CDP端口分配
_CDP_PORT_LOCK = threading.Lock()
_CDP_PORT_COUNTER = [9222]  # 可变容器，线程间共享

def _alloc_cdp_port() -> int:
    """分配一个可用的CDP端口（线程安全）"""
    with _CDP_PORT_LOCK:
        port = _CDP_PORT_COUNTER[0]
        _CDP_PORT_COUNTER[0] += 1
        return port

def open_automated_browser(url: str, email: str = "", password: str = "", device_code: str = "", browser_mode: str = "default", on_log=None, stop_event: threading.Event | None = None, skip_event: threading.Event | None = None):
    if sync_playwright is None:
        return None, launch_browser(url, browser_mode)

    try:
        playwright = sync_playwright().start()
    except Exception as exc:
        _log_browser(on_log, f"Playwright启动失败: {exc}")
        return None, launch_browser(url, browser_mode)

    # 并发安全：每个调用用唯一的端口和用户目录
    _tid = threading.get_ident()
    _ts = int(time.time() * 1000) % 100000
    _user_data_dir = os.path.join(tempfile.gettempdir(), f"otb_{_tid}_{_ts}")
    _cdp_port = _alloc_cdp_port()
    edge_proc = None
    channel_name = "Edge"

    try:
        # 查找Edge路径
        edge_path = None
        for candidate in [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]:
            if os.path.exists(candidate):
                edge_path = candidate
                break

        if edge_path:
            _log_browser(on_log, f"启动Edge(CDP:{_cdp_port}): {email}")
            edge_proc = subprocess.Popen([
                edge_path,
                f"--remote-debugging-port={_cdp_port}",
                f"--user-data-dir={_user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                url,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # 等待CDP端口就绪（最多5秒）
            for _ in range(25):
                time.sleep(0.2)
                if edge_proc.poll() is not None:
                    break
                try:
                    import socket
                    s = socket.socket(); s.settimeout(0.5)
                    s.connect(("127.0.0.1", _cdp_port)); s.close()
                    break
                except Exception:
                    continue
            if edge_proc.poll() is not None:
                _log_browser(on_log, f"Edge启动失败(码={edge_proc.returncode})，尝试Chrome")
                edge_proc = None

        if edge_proc is None:
            chrome_path = None
            for candidate in [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]:
                if os.path.exists(candidate):
                    chrome_path = candidate
                    break
            if chrome_path:
                _log_browser(on_log, f"启动Chrome(CDP:{_cdp_port}): {email}")
                edge_proc = subprocess.Popen([
                    chrome_path,
                    f"--remote-debugging-port={_cdp_port}",
                    f"--user-data-dir={_user_data_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    url,
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                channel_name = "Chrome"
                for _ in range(25):
                    time.sleep(0.2)
                    if edge_proc.poll() is not None:
                        break
                    try:
                        import socket
                        s = socket.socket(); s.settimeout(0.5)
                        s.connect(("127.0.0.1", _cdp_port)); s.close()
                        break
                    except Exception:
                        continue

        if edge_proc is None:
            _log_browser(on_log, f"未找到Edge/Chrome，用Playwright内置Chromium: {email}")
            browser = playwright.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context()
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); delete navigator.__proto__.webdriver;")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                autofill_microsoft_login(page, email=email, password=password, device_code=device_code, on_log=on_log, stop_event=stop_event, skip_event=skip_event, browser_context=context)
            except Exception as exc:
                _log_browser(on_log, f"autofill异常: {exc}")
            return {"playwright": playwright, "browser": browser, "context": context, "page": page, "edge_proc": None}, f"Chromium 自动浏览器"

        # CDP连接
        _log_browser(on_log, f"CDP连接 localhost:{_cdp_port}...")
        browser = playwright.chromium.connect_over_cdp(f"http://localhost:{_cdp_port}")
        _log_browser(on_log, f"CDP连接成功: {email}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        time.sleep(1)
        # 找到登录页面
        page = None
        for p in context.pages:
            try:
                purl = p.url or ""
                if "login" in purl.lower() or "microsoft" in purl.lower() or "oauth" in purl.lower() or "account" in purl.lower():
                    page = p
                    break
            except Exception:
                continue
        if page is None:
            page = context.pages[0] if context.pages else context.new_page()
        _log_browser(on_log, f"页面URL: {page.url[:80]}")

    except Exception as exc:
        _log_browser(on_log, f"浏览器启动/CDP连接失败({email}): {exc}")
        if edge_proc:
            try: edge_proc.terminate()
            except: pass
        try: playwright.stop()
        except: pass
        return None, launch_browser(url, browser_mode)

    # autofill 单独 try，AutofillSkipError需穿透，其他异常保持浏览器打开
    try:
        autofill_microsoft_login(page, email=email, password=password, device_code=device_code, on_log=on_log, stop_event=stop_event, skip_event=skip_event, browser_context=context)
    except AutofillSkipError:
        raise  # 穿透到调用方，由调用方处理跳过逻辑
    except Exception as exc:
        _log_browser(on_log, f"autofill异常（浏览器保持打开）: {exc}")
    return {"playwright": playwright, "browser": browser, "context": context, "page": page, "edge_proc": edge_proc}, f"{channel_name} 自动浏览器(CDP:{_cdp_port})"

class OutlookTokenToolApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Outlook Token Tool")
        self.geometry("980x720")
        self.minsize(860, 620)

        self.network = NetworkClient(timeout=30)
        self.busy = False
        self.cancel_event = threading.Event()
        self.current_oauth_server = None
        self.current_browser_session = None
        self.latest_tokens: dict = {}
        self.imported_credentials: list[dict] = []

        cfg = load_config()
        self.account_email = tk.StringVar(value=str(cfg.get("account_email", "")))
        self.account_password = tk.StringVar(value=str(cfg.get("account_password", "")))
        self.import_three_info = tk.StringVar(value="")
        self.import_four_info = tk.StringVar(value="")
        tenant_value = str(cfg.get("tenant", "consumers"))
        self.tenant = tk.StringVar(value=tenant_value)
        self.tenant_label = tk.StringVar(value=TENANT_VALUES.get(tenant_value, "消费者个人账户"))
        self.client_id = tk.StringVar(value=str(cfg.get("client_id", BUILTIN_CLIENT_ID)))
        self.refresh_token = tk.StringVar(value=str(cfg.get("refresh_token", "")))
        self.output_dir = tk.StringVar(value=str(cfg.get("output_dir", DEFAULT_OUTPUT_DIR)))
        self.auto_name = tk.BooleanVar(value=bool(cfg.get("auto_name", True)))
        self.port = tk.StringVar(value=str(cfg.get("port", 8765)))
        self.timeout = tk.StringVar(value=str(cfg.get("timeout", 180)))
        self.scopes = tk.StringVar(value=" ".join(ensure_scopes(cfg.get("scopes"))))
        browser_mode_value = str(cfg.get("browser_mode", "default"))
        self.browser_mode = tk.StringVar(value=browser_mode_value)
        self.browser_mode_label = tk.StringVar(value=BROWSER_MODE_VALUES.get(browser_mode_value, "默认浏览器"))
        self.concurrency = tk.StringVar(value=str(cfg.get("concurrency", 3)))
        self.status_var = tk.StringVar(value="就绪")

        self._action_buttons: list[ttk.Button] = []
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(18, weight=1)

        ttk.Label(root, text="邮箱").grid(row=0, column=0, sticky="w", pady=(6, 2))
        mail_row = ttk.Frame(root)
        mail_row.grid(row=0, column=1, sticky="ew", pady=(6, 2))
        mail_row.columnconfigure(0, weight=1)
        self.email_box = ttk.Combobox(mail_row, textvariable=self.account_email)
        self.email_box.grid(row=0, column=0, sticky="ew")
        mail_actions = ttk.Frame(root)
        mail_actions.grid(row=1, column=1, sticky="w", pady=(0, 6))
        self._add_action_button(mail_actions, "粘贴邮箱", self.paste_email, width=10).pack(side="left")
        self._add_action_button(mail_actions, "复制邮箱", self.copy_email, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(mail_actions, "扫描本机邮箱", self.scan_local_emails, width=14).pack(side="left", padx=(8, 0))

        ttk.Label(root, text="密码").grid(row=2, column=0, sticky="w", pady=(6, 2))
        pass_row = ttk.Frame(root)
        pass_row.grid(row=2, column=1, sticky="ew", pady=(6, 2))
        pass_row.columnconfigure(0, weight=1)
        ttk.Entry(pass_row, textvariable=self.account_password, show="*").grid(row=0, column=0, sticky="ew")
        self._password_entry = pass_row.winfo_children()[0]
        pass_actions = ttk.Frame(root)
        pass_actions.grid(row=3, column=1, sticky="w", pady=(0, 6))
        self._add_action_button(pass_actions, "粘贴密码", self.paste_password, width=10).pack(side="left")
        self._add_action_button(pass_actions, "复制密码", self.copy_password, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(pass_actions, "显示/隐藏", self.toggle_password_visibility, width=10).pack(side="left", padx=(8, 0))

        ttk.Label(root, text="id").grid(row=4, column=0, sticky="w", pady=(6, 2))
        client_row = ttk.Frame(root)
        client_row.grid(row=4, column=1, sticky="ew", pady=(6, 2))
        client_row.columnconfigure(0, weight=1)
        ttk.Entry(client_row, textvariable=self.client_id).grid(row=0, column=0, sticky="ew")
        client_actions = ttk.Frame(root)
        client_actions.grid(row=5, column=1, sticky="w", pady=(0, 6))
        self._add_action_button(client_actions, "粘贴 ID", self.paste_client_id, width=10).pack(side="left")
        self._add_action_button(client_actions, "复制 ID", self.copy_client_id, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(client_actions, "恢复内置 Client ID", self.reset_builtin_client_id, width=16).pack(side="left", padx=(8, 0))

        ttk.Label(root, text="rt").grid(row=6, column=0, sticky="w", pady=(6, 2))
        rt_row = ttk.Frame(root)
        rt_row.grid(row=6, column=1, sticky="ew", pady=(6, 2))
        rt_row.columnconfigure(0, weight=1)
        ttk.Entry(rt_row, textvariable=self.refresh_token).grid(row=0, column=0, sticky="ew")
        rt_actions = ttk.Frame(root)
        rt_actions.grid(row=7, column=1, sticky="w", pady=(0, 6))
        self._add_action_button(rt_actions, "粘贴Token", self.paste_refresh_token, width=10).pack(side="left")
        self._add_action_button(rt_actions, "复制Token", self.copy_refresh_token, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(rt_actions, "从文件导入Token", self.import_refresh_token_from_file, width=16).pack(side="left", padx=(8, 0))

        ttk.Label(root, text="三凭证").grid(row=8, column=0, sticky="w", pady=(6, 2))
        import_three_row = ttk.Frame(root)
        import_three_row.grid(row=8, column=1, sticky="ew", pady=(6, 2))
        import_three_row.columnconfigure(0, weight=1)
        ttk.Entry(import_three_row, textvariable=self.import_three_info).grid(row=0, column=0, sticky="ew")
        import_three_actions = ttk.Frame(root)
        import_three_actions.grid(row=9, column=1, sticky="w", pady=(0, 6))
        self._add_action_button(import_three_actions, "复制三凭证", self.copy_import_three_info, width=10).pack(side="left")
        self._add_action_button(import_three_actions, "粘贴三凭证", self.paste_import_three_info, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(import_three_actions, "导入三凭证", self.apply_import_three_info, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(import_three_actions, "批量导入三凭证", self.batch_import_three_credentials, width=16).pack(side="left", padx=(8, 0))

        ttk.Label(root, text="四凭证").grid(row=10, column=0, sticky="w", pady=(6, 2))
        import_four_row = ttk.Frame(root)
        import_four_row.grid(row=10, column=1, sticky="ew", pady=(6, 2))
        import_four_row.columnconfigure(0, weight=1)
        ttk.Entry(import_four_row, textvariable=self.import_four_info).grid(row=0, column=0, sticky="ew")
        import_four_actions = ttk.Frame(root)
        import_four_actions.grid(row=11, column=1, sticky="w", pady=(0, 6))
        self._add_action_button(import_four_actions, "复制四凭证", self.copy_import_four_info, width=10).pack(side="left")
        self._add_action_button(import_four_actions, "粘贴四凭证", self.paste_import_four_info, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(import_four_actions, "导入四凭证", self.apply_import_four_info, width=10).pack(side="left", padx=(8, 0))
        self._add_action_button(import_four_actions, "批量导入四凭证", self.batch_import_four_credentials, width=16).pack(side="left", padx=(8, 0))

        ttk.Label(root, text="Tenant").grid(row=12, column=0, sticky="w", pady=6)
        tenant_row = ttk.Frame(root)
        tenant_row.grid(row=12, column=1, sticky="ew", pady=6)
        ttk.Combobox(
            tenant_row,
            textvariable=self.tenant_label,
            values=tuple(TENANT_LABELS.keys()),
            state="readonly",
            width=18,
        ).pack(side="left")
        ttk.Label(tenant_row, text="浏览器模式").pack(side="left", padx=(16, 6))
        ttk.Combobox(
            tenant_row,
            textvariable=self.browser_mode_label,
            values=tuple(BROWSER_MODE_LABELS.keys()),
            state="readonly",
            width=14,
        ).pack(side="left")

        ttk.Label(root, text="Scopes").grid(row=13, column=0, sticky="w", pady=6)
        ttk.Entry(root, textvariable=self.scopes).grid(row=13, column=1, sticky="ew", pady=6)

        ttk.Label(root, text="保存目录").grid(row=14, column=0, sticky="w", pady=6)
        out_row = ttk.Frame(root)
        out_row.grid(row=14, column=1, sticky="ew", pady=6)
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.output_dir).grid(row=0, column=0, sticky="ew")
        self._add_action_button(out_row, "选择目录", self.pick_output_dir).grid(row=0, column=1, padx=(8, 0))
        self._add_action_button(out_row, "打开目录", self.open_output_dir).grid(row=0, column=2, padx=(8, 0))

        extra = ttk.Frame(root)
        extra.grid(row=15, column=1, sticky="w", pady=6)
        ttk.Checkbutton(extra, text="按邮箱命名", variable=self.auto_name).grid(row=0, column=0, padx=(0, 12))
        ttk.Label(extra, text="回调端口").grid(row=0, column=1, padx=(0, 6))
        ttk.Entry(extra, textvariable=self.port, width=8).grid(row=0, column=2, padx=(0, 12))
        ttk.Label(extra, text="超时秒数").grid(row=0, column=3, padx=(0, 6))
        ttk.Entry(extra, textvariable=self.timeout, width=8).grid(row=0, column=4, padx=(0, 12))
        ttk.Label(extra, text="并发数").grid(row=0, column=5, padx=(0, 6))
        ttk.Entry(extra, textvariable=self.concurrency, width=5).grid(row=0, column=6)

        buttons = ttk.Frame(root)
        buttons.grid(row=16, column=0, columnspan=2, sticky="w", pady=(8, 10))
        self._add_action_button(buttons, "网页登录并获取四凭证", self.start_auth_code).pack(side="left")
        self._add_action_button(buttons, "Device Code 获取四凭证", self.start_device_code).pack(side="left", padx=(8, 0))
        self._add_action_button(buttons, "刷新已有四凭证", self.start_refresh).pack(side="left", padx=(8, 0))
        self._add_action_button(buttons, "批量登录本地Outlook", self.start_batch_login).pack(side="left", padx=(8, 0))
        self._add_action_button(buttons, "清理已成功", self.cleanup_successful_credentials).pack(side="left", padx=(8, 0))
        self._add_action_button(buttons, "保存配置", self.persist_config).pack(side="left", padx=(8, 0))
        self._add_action_button(buttons, "取消当前任务", self.cancel_current_task).pack(side="left", padx=(8, 0))

        status_row = ttk.Frame(root)
        status_row.grid(row=17, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

        self.log_box = tk.Text(root, height=18, wrap="word")
        self.log_box.grid(row=18, column=0, columnspan=2, sticky="nsew")
        scroll = ttk.Scrollbar(root, orient="vertical", command=self.log_box.yview)
        scroll.grid(row=18, column=2, sticky="ns")
        self.log_box.configure(yscrollcommand=scroll.set)

    def _add_action_button(self, parent, text: str, command, width: int | None = None):
        kwargs = {"text": text, "command": command}
        if width is not None:
            kwargs["width"] = width
        button = ttk.Button(parent, **kwargs)
        self._action_buttons.append(button)
        return button

    def log(self, message: str) -> None:
        text = str(message).rstrip()
        if not text:
            return
        def update():
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
        self.after(0, update)

    def set_status(self, text: str) -> None:
        self.after(0, lambda: self.status_var.set(text))

    def set_busy(self, busy: bool, status: str) -> None:
        def update():
            self.busy = busy
            self.status_var.set(status)
        self.after(0, update)

    def paste_password(self) -> None:
        self._paste_var(self.account_password)
        self.set_status("已粘贴密码")

    def copy_password(self) -> None:
        self._copy_text(self.account_password.get())
        self.set_status("已复制密码")

    def toggle_password_visibility(self) -> None:
        current = self._password_entry.cget("show")
        if current == "*":
            self._password_entry.configure(show="")
            self.set_status("密码已显示")
        else:
            self._password_entry.configure(show="*")
            self.set_status("密码已隐藏")

    def paste_email(self) -> None:
        self._paste_var(self.account_email)
        self.set_status("已粘贴邮箱")

    def copy_email(self) -> None:
        self._copy_text(self.account_email.get())
        self.set_status("已复制邮箱")

    def paste_client_id(self) -> None:
        self._paste_var(self.client_id)
        self.set_status("已粘贴 Client ID")

    def copy_client_id(self) -> None:
        self._copy_text(self.client_id.get())
        self.set_status("已复制 Client ID")

    def paste_refresh_token(self) -> None:
        self._paste_var(self.refresh_token)
        self.set_status("已粘贴 Refresh Token")

    def copy_refresh_token(self) -> None:
        self._copy_text(self.refresh_token.get())
        self.set_status("已复制 Refresh Token")

    def import_refresh_token_from_file(self) -> None:
        """从已有的四凭证文件中导入 refresh_token 字段"""
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            return
        path = filedialog.askopenfilename(
            title="选择四凭证文件",
            initialdir=config["output_dir"],
            filetypes=[("凭证文件", "*.txt *.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            refresh = load_refresh_token_from_file(path)
            self.refresh_token.set(refresh)
            self.set_status("已从文件导入 Refresh Token")
            self.log(f"已从文件导入 Refresh Token: {mask_token(refresh)}")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    def paste_import_three_info(self) -> None:
        self._paste_var(self.import_three_info)
        self.set_status("已粘贴三凭证")

    def copy_import_three_info(self) -> None:
        self.import_three_info.set(self.build_three_credential_text())
        self._copy_text(self.import_three_info.get())
        self.set_status("已复制三凭证")

    def apply_import_three_info(self) -> None:
        raw = self.import_three_info.get().strip()
        parts = [part.strip() for part in THREE_INFO_SPLIT_RE.split(raw) if part.strip()]
        if len(parts) < 3:
            messagebox.showerror("导入失败", "请按 邮箱----密码----id 的格式提供三凭证。")
            return
        self.account_email.set(parts[0])
        self.account_password.set(parts[1])
        self.client_id.set(parts[2])
        status_msg = "已导入三凭证（邮箱、密码、id）"
        self.set_status(status_msg)
        self.log(status_msg)

    def paste_import_four_info(self) -> None:
        self._paste_var(self.import_four_info)
        self.set_status("已粘贴四凭证")

    def copy_import_four_info(self) -> None:
        self.import_four_info.set(self.build_four_credential_text())
        self._copy_text(self.import_four_info.get())
        self.set_status("已复制四凭证")

    def apply_import_four_info(self) -> None:
        raw = self.import_four_info.get().strip()
        parts = [part.strip() for part in THREE_INFO_SPLIT_RE.split(raw) if part.strip()]
        if len(parts) < 4:
            messagebox.showerror("导入失败", "请按 邮箱----密码----id----rt 的格式提供四凭证。")
            return
        self.account_email.set(parts[0])
        self.account_password.set(parts[1])
        self.client_id.set(parts[2])
        self.refresh_token.set(parts[3])
        status_msg = "已导入四凭证（邮箱、密码、id、rt）"
        self.set_status(status_msg)
        self.log(status_msg)

    def build_three_credential_text(self) -> str:
        return "----".join([
            self.account_email.get().strip(),
            self.account_password.get(),
            self.client_id.get().strip() or BUILTIN_CLIENT_ID,
        ])

    def build_four_credential_text(self) -> str:
        return "----".join([
            self.account_email.get().strip(),
            self.account_password.get(),
            self.client_id.get().strip() or BUILTIN_CLIENT_ID,
            self.refresh_token.get().strip(),
        ])

    def batch_import_three_credentials(self) -> None:
        self._batch_import_by_type(3)

    def batch_import_four_credentials(self) -> None:
        self._batch_import_by_type(4)

    def _batch_import_by_type(self, expected_fields: int) -> None:
        """批量导入凭证到内存：3=只导三凭证，4=只导四凭证，不写文件"""
        type_name = "三凭证" if expected_fields == 3 else "四凭证"

        # 选择文件夹
        folder = filedialog.askdirectory(
            title=f"选择文件夹（导入{type_name}）",
        )
        if not folder:
            return

        # 递归扫描
        paths = []
        for root, dirs, files in os.walk(folder):
            for fname in files:
                if fname.lower().endswith((".txt", ".csv", ".dat", ".log")):
                    paths.append(os.path.join(root, fname))
        if not paths:
            messagebox.showinfo("提示", f"文件夹中没有找到凭证文件: {folder}")
            return

        # 解析全部行 → 存到 self.imported_credentials
        imported = []
        skipped_other = 0
        skipped_error = 0

        for path in paths:
            content = ""
            for encoding in ["utf-8-sig", "utf-8", "gbk", "latin1"]:
                try:
                    with open(path, "r", encoding=encoding) as f:
                        content = f.read().strip()
                    if content:
                        break
                except (UnicodeDecodeError, UnicodeError):
                    continue
                except Exception:
                    break
            if not content:
                continue

            for line_no, raw_line in enumerate(content.splitlines(), 1):
                raw_line = raw_line.strip()
                if not raw_line or raw_line.startswith("#") or raw_line.startswith("//"):
                    continue
                if raw_line.startswith("\ufeff"):
                    raw_line = raw_line[1:]

                parts = self._parse_line(raw_line)
                if not parts:
                    skipped_error += 1
                    continue

                source = f"{os.path.basename(path)}:{line_no}"

                if len(parts) >= 4 and parts[3].strip():
                    # 四字段（有rt）
                    if expected_fields == 4:
                        email, password, client_id, rt = parts[0], parts[1], parts[2], parts[3].strip()
                        if not email:
                            skipped_error += 1
                            continue
                        imported.append({
                            "email": email, "password": password,
                            "client_id": client_id or BUILTIN_CLIENT_ID,
                            "refresh_token": rt, "mode": "four",
                            "source_path": path,
                        })
                        self.log(f"[四凭证] {email} <- {source}")
                    else:
                        skipped_other += 1
                        self.log(f"[跳过-四凭证] {parts[0]} <- {source}")
                elif len(parts) >= 3:
                    # 三字段（无rt）
                    if expected_fields == 3:
                        email, password, client_id = parts[0], parts[1], parts[2]
                        if not email:
                            skipped_error += 1
                            continue
                        imported.append({
                            "email": email, "password": password,
                            "client_id": client_id or BUILTIN_CLIENT_ID,
                            "refresh_token": "", "mode": "three",
                            "source_path": path,
                        })
                        self.log(f"[三凭证] {email} <- {source}")
                    else:
                        skipped_other += 1
                        self.log(f"[跳过-三凭证] {parts[0]} <- {source}")
                else:
                    skipped_error += 1

        # 存到实例变量，供批量登录使用
        self.imported_credentials = imported

        summary = f"导入{type_name}完成: 扫描 {len(paths)} 个文件, 导入 {len(imported)} 条"
        if skipped_other:
            summary += f", 跳过其他类型 {skipped_other} 条"
        if skipped_error:
            summary += f", 跳过错误行 {skipped_error} 条"
        self.log(f"\n{summary}")
        messagebox.showinfo("完成", summary)

    def _parse_line(self, line: str) -> list[str]:
        """解析一行凭证，返回字段列表"""
        for sep in ["----", "|", "\t", ",", ";", "---", "  "]:
            parts = [p.strip() for p in line.split(sep)]
            while parts and not parts[-1]:
                parts.pop()
            if len(parts) >= 3:
                return parts[:4] if len(parts) >= 4 else parts
        return []

        if not paths:
            return
        paths = list(dict.fromkeys(paths))  # 去重保持顺序

        # 自动识别分隔符的解析函数
        def parse_credential_line(line: str) -> list[str]:
            """尝试多种分隔符解析一行凭证"""
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                return []
            if line.startswith("\ufeff"):
                line = line[1:]
            # 按优先级尝试不同分隔符
            for sep in ["----", "|", "\t", ",", ";", "---", "  "]:
                parts = [p.strip() for p in line.split(sep)]
                # 去掉末尾空字段，但保留中间的空字段（如空rt）
                while parts and not parts[-1]:
                    parts.pop()
                if len(parts) >= 3:
                    return parts[:4] if len(parts) >= 4 else parts
            return []

        all_lines: list[tuple[str, int, str, list[str]]] = []
        unreadable_files: list[str] = []
        skipped_lines: list[str] = []
        
        for path in paths:
            # 尝试多种编码读取
            content = ""
            for encoding in ["utf-8-sig", "utf-8", "gbk", "latin1"]:
                try:
                    with open(path, "r", encoding=encoding) as f:
                        content = f.read().strip()
                    if content:
                        break
                except (UnicodeDecodeError, UnicodeError):
                    continue
                except Exception as exc:
                    unreadable_files.append(f"{os.path.basename(path)}: {exc}")
                    break
            else:
                if not content:
                    unreadable_files.append(f"{os.path.basename(path)}: 无法解码或文件为空")
                    continue
            
            if not content:
                unreadable_files.append(f"{os.path.basename(path)}: 文件为空")
                continue
            
            for line_no, line in enumerate(content.splitlines(), 1):
                raw_line = line.strip()
                if not raw_line:
                    continue
                parts = parse_credential_line(raw_line)
                if parts:
                    all_lines.append((path, line_no, raw_line, parts))
                else:
                    skipped_lines.append(f"{os.path.basename(path)} 第{line_no}行: {raw_line[:60]}")

        if not all_lines:
            detail = ""
            if unreadable_files:
                detail += "无法读取:\n" + "\n".join(unreadable_files[:5])
            if skipped_lines:
                detail += "\n\n无法识别的行:\n" + "\n".join(skipped_lines[:5])
            if not detail:
                detail = "没有读取到任何有效内容"
            messagebox.showerror("导入失败", detail)
            return

        three_count = 0
        four_count = 0
        error_lines = []
        output_dir = os.path.abspath(self.output_dir.get().strip() or DEFAULT_OUTPUT_DIR)
        os.makedirs(output_dir, exist_ok=True)

        for path, line_no, raw_line, parts in all_lines:
            source = f"{os.path.basename(path)} 第{line_no}行"
            
            # 自动识别凭证类型，不强制丢弃数据
            if len(parts) >= 4:
                email, password, client_id, refresh_token = parts[0], parts[1], parts[2], parts[3].strip()
                if not email:
                    error_lines.append(f"{source}: 邮箱为空")
                    continue
                if refresh_token:
                    four_count += 1
                    self.log(f"[四凭证] {email} <- {source}")
                else:
                    three_count += 1
                    self.log(f"[三凭证] {email} <- {source} (缺少rt)")
                save_combo_line({"refresh_token": refresh_token}, os.path.join(output_dir, f"{safe_filename(email)}.txt"), email, password, client_id)
            elif len(parts) == 3:
                email, password, client_id = parts[0], parts[1], parts[2]
                if not email:
                    error_lines.append(f"{source}: 邮箱为空")
                    continue
                three_count += 1
                self.log(f"[三凭证] {email} <- {source} (缺少rt)")
                save_combo_line({"refresh_token": ""}, os.path.join(output_dir, f"{safe_filename(email)}.txt"), email, password, client_id)
            else:
                error_lines.append(f"{source}: 字段数不足 (识别到{len(parts)}个，需要至少3个)")

        # 构建汇总
        summary = f"批量导入完成: 扫描文件 {len(paths)} 个, 导入 {four_count} 条四凭证, {three_count} 条三凭证"
        if unreadable_files:
            summary += f", {len(unreadable_files)} 个文件未读入"
            for item in unreadable_files[:10]:
                self.log(f"  未读入: {item}")
        if error_lines:
            summary += f", {len(error_lines)} 行跳过"
            for err in error_lines[:10]:
                self.log(f"  跳过: {err}")
        if skipped_lines and not error_lines:
            summary += f", {len(skipped_lines)} 行无法识别"
            for item in skipped_lines[:5]:
                self.log(f"  无法识别: {item}")
        self.set_status(summary)
        self.log(summary)
        messagebox.showinfo("批量导入", summary)

    def start_batch_login(self) -> None:
        """批量登录：用导入的凭证登录"""
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            return

        if not getattr(self, "imported_credentials", None):
            messagebox.showinfo("提示", "没有可登录的凭证。请先批量导入。")
            return

        credential_entries = self.imported_credentials
        source_desc = f"导入的 {len(credential_entries)} 个账号"

        three_count = sum(1 for e in credential_entries if e.get("mode") == "three")
        four_count = sum(1 for e in credential_entries if e.get("mode") == "four")
        concurrency = config.get("concurrency", 3)

        preview_lines = []
        for e in credential_entries[:30]:
            tag = "[三]" if e.get("mode") == "three" else "[四]"
            preview_lines.append(f"{tag} {e['email']}")
        preview = "\n".join(preview_lines)
        if len(credential_entries) > 30:
            preview += f"\n...还有 {len(credential_entries) - 30} 个"

        confirm = messagebox.askyesno(
            "确认批量登录",
            f"{source_desc}，共 {len(credential_entries)} 个账号:\n"
            f"三凭证: {three_count}（网页登录获取rt）\n"
            f"四凭证: {four_count}（先刷新rt再登录）\n"
            f"并发数: {concurrency}\n\n"
            f"{preview}\n\n是否开始？",
        )
        if not confirm or self.busy:
            return

        self.cancel_event.clear()
        self.set_busy(True, f"批量登录 (0/{len(credential_entries)})")
        threading.Thread(target=self.batch_login_worker, args=(config, credential_entries), daemon=True).start()

    def cleanup_successful_credentials(self) -> None:
        """从导入列表中移除已成功获取rt的账号（通过检查输出目录中是否已有对应文件）"""
        if not getattr(self, "imported_credentials", None):
            messagebox.showinfo("提示", "没有导入的凭证。")
            return

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            messagebox.showinfo("提示", "请先设置输出目录。")
            return
        output_dir = os.path.abspath(output_dir)

        before = len(self.imported_credentials)
        remaining = []
        removed_emails = []
        source_files_to_delete = []

        for entry in self.imported_credentials:
            email = entry["email"]
            safe_name = re.sub(r"[^a-z0-9@._-]+", "_", email.strip().lower()).strip("._-") or "outlook"
            out_path = os.path.join(output_dir, f"{safe_name}.txt")
            # 检查输出文件中是否已有该账号的rt
            has_rt = False
            if os.path.exists(out_path):
                try:
                    with open(out_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # 文件中有refresh_token且不是空的
                    if "refresh_token" in content.lower():
                        for line in content.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            parts = [p.strip() for p in re.split(r"[|\t,;]|----", line) if p.strip()]
                            if len(parts) >= 4 and parts[3]:
                                has_rt = True
                                break
                except Exception:
                    pass
            if has_rt:
                removed_emails.append(email)
                # 记录源文件路径用于删除
                source_path = entry.get("source_path", "")
                if source_path and os.path.exists(source_path):
                    source_files_to_delete.append(source_path)
            else:
                remaining.append(entry)

        self.imported_credentials = remaining
        removed = before - len(remaining)

        # 删除已成功账号的源文件
        if source_files_to_delete:
            deleted = 0
            for sp in set(source_files_to_delete):
                try:
                    os.remove(sp)
                    deleted += 1
                    self.log(f"[清理] 已删除源文件: {os.path.basename(sp)}")
                except Exception as exc:
                    self.log(f"[清理] 删除失败: {os.path.basename(sp)} - {exc}")
            self.log(f"[清理] 共删除 {deleted} 个源文件")

        if removed:
            self.log(f"[清理] 移除了 {removed} 个已有rt的账号: {', '.join(removed_emails[:5])}{'...' if len(removed_emails) > 5 else ''}")
            self.log(f"[清理] 剩余 {len(remaining)} 个账号待处理")
            self.set_status(f"已清理 {removed} 个成功账号，剩余 {len(remaining)} 个")
            messagebox.showinfo("清理完成", f"已移除 {removed} 个已有rt的账号\n剩余 {len(remaining)} 个待处理")
        else:
            messagebox.showinfo("提示", f"没有找到已成功的账号\n当前列表共 {len(remaining)} 个账号")

    def _scan_credential_folder(self, folder: str) -> list[dict]:
        """递归扫描文件夹，解析所有凭证文件"""
        entries = []
        for root, dirs, files in os.walk(folder):
            for fname in files:
                if fname.lower().endswith((".txt", ".csv", ".dat", ".log")):
                    entries.extend(self._parse_credential_file(os.path.join(root, fname)))
        return entries

    def _parse_credential_file(self, path: str) -> list[dict]:
        """解析凭证文件，支持三凭证和四凭证，自动识别分隔符和编码"""
        entries = []
        content = ""
        for encoding in ["utf-8-sig", "utf-8", "gbk", "latin1"]:
            try:
                with open(path, "r", encoding=encoding) as f:
                    content = f.read().strip()
                if content:
                    break
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception:
                return entries
        if not content:
            return entries
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if line.startswith("\ufeff"):
                line = line[1:]
            parts = []
            for sep in ["----", "|", "\t", ",", ";"]:
                parts = [p.strip() for p in line.split(sep) if p.strip()]
                if len(parts) >= 3:
                    break
            if len(parts) < 3:
                continue
            email, password, client_id = parts[0], parts[1], parts[2]
            refresh_token = parts[3] if len(parts) >= 4 else ""
            if not email:
                continue
            entries.append({
                "email": email, "password": password,
                "client_id": client_id or BUILTIN_CLIENT_ID,
                "refresh_token": refresh_token,
                "mode": "four" if refresh_token else "three",
            })
        return entries

    def batch_login_worker(self, config: dict, entries: list[dict]) -> None:
        """并发批量登录：三凭证走网页授权获取rt，四凭证先刷新再登录"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(entries)
        concurrency = max(1, min(config.get("concurrency", 3), total, 20))
        success = 0
        fail = 0
        completed = 0
        lock = threading.Lock()
        success_emails: list[str] = []  # 记录成功的email
        source_files_to_delete: list[str] = []  # 记录成功的源文件路径

        def process_one(idx: int, entry: dict) -> tuple[bool, str]:
            if self.cancel_event.is_set():
                return False, "已取消"
            email = entry["email"]
            password = entry["password"]
            client_id = entry["client_id"]
            refresh_token = entry["refresh_token"]
            mode = entry.get("mode", "four")
            source_path = entry.get("source_path", "")
            tag = "[三]" if mode == "three" else "[四]"
            self.log(f"  {tag} [{idx}/{total}] 开始: {email}")
            try:
                if mode == "four" and refresh_token:
                    # 四凭证：先尝试rt刷新
                    try:
                        client = NetworkClient(timeout=30)
                        tokens = refresh_access_token(client, config["tenant"], client_id, refresh_token, config["scopes"])
                        if tokens.get("access_token"):
                            self.log(f"  {tag} [{idx}/{total}] rt刷新成功: {email}")
                            output_dir = os.path.abspath(config["output_dir"])
                            safe_name = re.sub(r"[^a-z0-9@._-]+", "_", email.strip().lower()).strip("._-") or "outlook"
                            out_path = os.path.join(output_dir, f"{safe_name}.txt")
                            save_combo_line(tokens, out_path, email, password, client_id)
                            with lock:
                                success_emails.append(email)
                                if source_path and os.path.exists(source_path):
                                    source_files_to_delete.append(source_path)
                            return True, f"{email} rt刷新成功"
                    except Exception as exc:
                        self.log(f"  {tag} [{idx}/{total}] rt刷新失败({email}): {exc}，改网页登录")
                # 三凭证 或 四凭证刷新失败：走网页授权
                result = self._auth_code_login_single(config, email, password, client_id, idx, total)
                if result[0]:
                    with lock:
                        success_emails.append(email)
                        if source_path and os.path.exists(source_path):
                            source_files_to_delete.append(source_path)
                return result
            except Exception as exc:
                self.log(f"  {tag} [{idx}/{total}] ✗ 失败({email}): {exc}")
                return False, f"{email} 失败: {exc}"

        self.log(f"\n=== 开始批量登录: {total} 个账号, 并发数 {concurrency} ===")
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(process_one, idx, entry): idx for idx, entry in enumerate(entries, 1)}
            for future in as_completed(futures):
                if self.cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    ok, msg = future.result()
                    with lock:
                        completed += 1
                        if ok:
                            success += 1
                        else:
                            fail += 1
                        self.set_status(f"批量登录 ({completed}/{total}) 成功:{success} 失败:{fail}")
                except Exception as exc:
                    with lock:
                        completed += 1
                        fail += 1
                        self.log(f"  ✗ 线程异常: {exc}")

        summary = f"批量登录完成: 成功 {success}/{total}, 失败 {fail}/{total}"
        def update_ui():
            self.set_busy(False, "批量登录完成")
            self.log(f"\n=== {summary} ===")
            # 从导入列表中移除已成功获取rt的账号
            if success_emails:
                before = len(self.imported_credentials)
                self.imported_credentials = [
                    e for e in self.imported_credentials
                    if e["email"] not in success_emails
                ]
                removed = before - len(self.imported_credentials)
                self.log(f"[清理] 已从导入列表移除 {removed} 个成功账号，剩余 {len(self.imported_credentials)} 个")
            # 删除已成功账号的源文件
            if source_files_to_delete:
                deleted = 0
                for sp in set(source_files_to_delete):
                    try:
                        os.remove(sp)
                        deleted += 1
                        self.log(f"[清理] 已删除源文件: {os.path.basename(sp)}")
                    except Exception as exc:
                        self.log(f"[清理] 删除失败: {os.path.basename(sp)} - {exc}")
                self.log(f"[清理] 共删除 {deleted} 个源文件")
            messagebox.showinfo("完成", summary + (f"\n已自动清理 {len(success_emails)} 个成功账号" if success_emails else ""))
        self.after(0, update_ui)

    def _auth_code_login_single(self, config: dict, email: str, password: str, client_id: str, idx: int, total: int) -> tuple[bool, str]:
        """单个账号的网页授权登录，返回 (成功, 消息)"""
        tag = "[三]"
        code_verifier, code_challenge = make_pkce_pair()
        state = os.urandom(18).hex()
        # 每个并发用不同端口（idx直接偏移，不取模避免冲突）
        port = config["port"] + (idx - 1)
        for _ in range(20):
            if is_port_available(port):
                break
            port += 1
        else:
            return False, f"{email} 找不到可用端口"
        redirect_uri = f"http://localhost:{port}"
        if client_id != BUILTIN_CLIENT_ID:
            redirect_uri += "/callback"
        query = {
            "client_id": client_id, "response_type": "code",
            "redirect_uri": redirect_uri, "response_mode": "query",
            "scope": " ".join(config["scopes"]), "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256",
            "prompt": "login", "login_hint": email,
        }
        auth_url = f"https://login.microsoftonline.com/{config['tenant']}/oauth2/v2.0/authorize?{urllib.parse.urlencode(query)}"
        self.log(f"  {tag} [{idx}/{total}] 打开授权页: {email}")
        with ReusableTCPServer(("localhost", port), OAuthCallbackHandler) as httpd:
            httpd.oauth_code = None
            httpd.oauth_state = None
            httpd.oauth_error = None
            threading.Thread(target=httpd.handle_request, daemon=True).start()
            try:
                session, browser_name = open_automated_browser(
                    auth_url, email=email, password=password,
                    browser_mode=config["browser_mode"], on_log=self.log,
                    stop_event=self.cancel_event,
                )
            except AutofillSkipError as exc:
                self.log(f"  {tag} [{idx}/{total}] ✗ 跳过: {email} — {exc}")
                return False, f"{email} 跳过: {exc}"
            self.log(f"  {tag} [{idx}/{total}] {browser_name} 已打开: {email}")
            deadline = time.time() + config["timeout"]
            while time.time() < deadline and not (httpd.oauth_code or httpd.oauth_error):
                if self.cancel_event.is_set():
                    raise RuntimeError("已取消")
                time.sleep(0.5)
                # 回退：如果autofill已返回但handler还没拿到code，从URL解析
                if not httpd.oauth_code and session and session.get("page"):
                    try:
                        page_url = session["page"].url
                        if "localhost" in page_url and "code=" in page_url:
                            parsed = urllib.parse.urlparse(page_url)
                            params = urllib.parse.parse_qs(parsed.query)
                            url_code = params.get("code", [None])[0]
                            url_state = params.get("state", [None])[0]
                            if url_code:
                                httpd.oauth_code = url_code
                                httpd.oauth_state = url_state
                                self.log(f"  {tag} [{idx}/{total}] 从URL解析到code")
                    except Exception:
                        pass
            if httpd.oauth_error:
                raise RuntimeError(f"授权失败: {httpd.oauth_error}")
            if not httpd.oauth_code:
                raise RuntimeError("授权回调超时")
            if httpd.oauth_state != state:
                raise RuntimeError("state校验失败")
        client = NetworkClient(timeout=30)
        tokens = exchange_authorization_code(client, config["tenant"], client_id, httpd.oauth_code, redirect_uri, config["scopes"], code_verifier)
        # 检查是否拿到rt
        refresh_token = tokens.get("refresh_token", "")
        if not refresh_token:
            self.log(f"  {tag} [{idx}/{total}] ⚠ 未获取到rt，跳过保存: {email}")
            return False, f"{email} 未获取到rt"
        # 有rt才保存
        output_dir = os.path.abspath(config["output_dir"])
        safe_name = re.sub(r"[^a-z0-9@._-]+", "_", email.strip().lower()).strip("._-") or "outlook"
        out_path = os.path.join(output_dir, f"{safe_name}.txt")
        save_combo_line(tokens, out_path, email, password, client_id)
        self.log(f"  {tag} [{idx}/{total}] ✓ 四凭证已保存: {email}")
        # 关闭浏览器
        self._close_session(session)
        return True, f"{email} 登录成功"

    def _close_session(self, session):
        """安全关闭浏览器会话"""
        if not session:
            return
        try: session["context"].close()
        except Exception: pass
        try: session["browser"].close()
        except Exception: pass
        try: session["playwright"].stop()
        except Exception: pass
        # 关闭Edge子进程
        edge_proc = session.get("edge_proc")
        if edge_proc:
            try: edge_proc.terminate()
            except Exception: pass

    def _paste_var(self, variable: tk.StringVar) -> None:
        try:
            value = self.clipboard_get()
        except tk.TclError:
            value = ""
        variable.set(value)

    def _copy_text(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)

    def reset_builtin_client_id(self) -> None:
        self.client_id.set(BUILTIN_CLIENT_ID)
        self.set_status(f"已恢复内置 Client ID: {BUILTIN_CLIENT_NAME}")

    def pick_output_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_dir.get().strip() or APP_DIR)
        if folder:
            self.output_dir.set(folder)
            self.set_status("已更新保存目录")

    def open_output_dir(self) -> None:
        folder = os.path.abspath(self.output_dir.get().strip() or DEFAULT_OUTPUT_DIR)
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def scan_local_emails(self) -> None:
        if self.busy:
            self.set_status("后台任务进行中，仍可扫描邮箱")
        self.set_status("正在扫描本机邮箱...")
        self.log("正在扫描本机 Outlook/Office 配置中的邮箱地址...")

        def worker():
            try:
                emails = find_local_outlook_emails()
                self.after(0, lambda: self.finish_scan_local_emails(emails))
            except Exception as exc:
                self.after(0, lambda: self.finish_scan_local_emails_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def finish_scan_local_emails(self, emails: list[str]) -> None:
        self.email_box["values"] = emails
        self.set_status(f"扫描到 {len(emails)} 个本机 Outlook 邮箱")
        if emails:
            self.log("本机邮箱扫描结果:")
            for email in emails:
                self.log(f"  {email}")
            if len(emails) == 1:
                self.account_email.set(emails[0])
                self.set_status("已填入扫描到的邮箱")
            else:
                self.choose_scanned_email(emails)
        else:
            self.log("未扫描到本机 Outlook 邮箱")
            messagebox.showinfo("没有找到", "没有在本机 Outlook/Office 配置里扫描到 Outlook/Hotmail 邮箱。")

    def finish_scan_local_emails_error(self, exc: Exception) -> None:
        detail = str(exc) or repr(exc)
        self.set_status("扫描失败")
        self.log(f"扫描本机邮箱失败: {detail}")
        messagebox.showerror("扫描失败", detail)

    def choose_scanned_email(self, emails: list[str]) -> None:
        win = tk.Toplevel(self)
        win.title("选择 Outlook 邮箱")
        win.geometry("520x360")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text="扫描到多个邮箱，选择一个：").pack(anchor="w", padx=14, pady=(14, 8))
        box = tk.Listbox(win, height=10)
        box.pack(fill="both", expand=True, padx=14)
        for email in emails:
            box.insert("end", email)
        if emails:
            box.selection_set(0)

        def use_selected():
            selection = box.curselection()
            if not selection:
                return
            email = emails[selection[0]]
            self.account_email.set(email)
            self.set_status("已选择邮箱")
            self.log(f"已选择邮箱: {email}")
            win.destroy()

        actions = ttk.Frame(win, padding=14)
        actions.pack(fill="x")
        ttk.Button(actions, text="使用选中邮箱", command=use_selected).pack(side="right")
        ttk.Button(actions, text="取消", command=win.destroy).pack(side="right", padx=(0, 8))
        box.bind("<Double-Button-1>", lambda _event: use_selected())

    def persist_config(self) -> None:
        try:
            data = self.collect_config()
            save_config(data)
        except Exception as exc:
            detail = str(exc) or repr(exc)
            self.set_status("保存配置失败")
            self.log(f"保存配置失败: {detail}")
            messagebox.showerror("保存配置失败", detail)
            return
        self.set_status("配置已保存")
        self.log("配置已保存")
        messagebox.showinfo("配置已保存", "当前配置已经写入 config.json")

    def collect_config(self) -> dict:
        output_dir = os.path.abspath(self.output_dir.get().strip() or DEFAULT_OUTPUT_DIR)
        os.makedirs(output_dir, exist_ok=True)
        scopes = [item for item in self.scopes.get().strip().split() if item]
        port_text = self.port.get().strip() or "8765"
        timeout_text = self.timeout.get().strip() or "180"
        try:
            port_value = int(port_text)
        except ValueError as exc:
            raise ValueError(f"回调端口不是有效整数: {port_text}") from exc
        try:
            timeout_value = int(timeout_text)
        except ValueError as exc:
            raise ValueError(f"超时秒数不是有效整数: {timeout_text}") from exc
        if port_value <= 0 or port_value > 65535:
            raise ValueError(f"回调端口超出范围: {port_value}")
        if timeout_value <= 0:
            raise ValueError(f"超时秒数必须大于 0: {timeout_value}")
        concurrency_text = self.concurrency.get().strip() or "3"
        try:
            concurrency_value = int(concurrency_text)
        except ValueError as exc:
            raise ValueError(f"并发数不是有效整数: {concurrency_text}") from exc
        if concurrency_value < 1 or concurrency_value > 20:
            raise ValueError(f"并发数超出范围(1-20): {concurrency_value}")
        tenant_value = TENANT_LABELS.get(self.tenant_label.get(), self.tenant.get().strip() or "consumers")
        browser_mode_value = BROWSER_MODE_LABELS.get(self.browser_mode_label.get(), self.browser_mode.get().strip() or "default")
        self.tenant.set(tenant_value)
        self.browser_mode.set(browser_mode_value)
        return {
            "account_email": self.account_email.get().strip(),
            "account_password": self.account_password.get(),
            "tenant": tenant_value,
            "client_id": self.client_id.get().strip() or BUILTIN_CLIENT_ID,
            "refresh_token": self.refresh_token.get().strip(),
            "output_dir": output_dir,
            "auto_name": bool(self.auto_name.get()),
            "port": port_value,
            "timeout": timeout_value,
            "concurrency": concurrency_value,
            "scopes": ensure_scopes(scopes),
            "browser_mode": browser_mode_value,
        }

    def cancel_current_task(self) -> None:
        if not self.busy:
            self.set_status("当前没有进行中的任务")
            return
        self.cancel_event.set()
        server = self.current_oauth_server
        if server is not None:
            try:
                server.server_close()
            except Exception:
                pass
        self.close_browser_session()
        self.set_status("已请求取消，正在中断当前任务")
        self.log("已请求取消当前任务")

    def close_browser_session(self) -> None:
        session = self.current_browser_session
        self.current_browser_session = None
        if not session:
            return
        try:
            session["context"].close()
        except Exception:
            pass
        try:
            session["browser"].close()
        except Exception:
            pass
        try:
            session["playwright"].stop()
        except Exception:
            pass

    def start_auth_code(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            return
        if not config["account_email"]:
            messagebox.showerror("错误", "请先填写邮箱")
            return
        if not config["account_password"]:
            messagebox.showerror("错误", "请先填写密码")
            return
        if self.busy:
            return
        self.cancel_event.clear()
        self.set_busy(True, "正在进行网页登录授权")
        threading.Thread(target=self.auth_code_worker, args=(config,), daemon=True).start()

    def auth_code_worker(self, config: dict) -> None:
        client = NetworkClient(timeout=30)
        self.network = client
        client_id = config["client_id"] or BUILTIN_CLIENT_ID
        code_verifier, code_challenge = make_pkce_pair()
        state = os.urandom(18).hex()
        redirect_uri = f"http://localhost:{config['port']}" if client_id == BUILTIN_CLIENT_ID else f"http://localhost:{config['port']}/callback"
        query = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(config["scopes"]),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "login" if config["account_email"] else "select_account",
            "login_hint": config["account_email"],
        }
        auth_url = f"https://login.microsoftonline.com/{config['tenant']}/oauth2/v2.0/authorize?{urllib.parse.urlencode(query)}"
        try:
            # 检查端口是否可用，自动寻找可用端口
            requested_port = config["port"]
            if not is_port_available(requested_port):
                self.log(f"端口 {requested_port} 被占用，正在寻找可用端口...")
                try:
                    actual_port = find_available_port(requested_port)
                    self.log(f"使用端口 {actual_port}")
                    # 更新 redirect_uri 和 auth_url 使用实际端口
                    redirect_uri = f"http://localhost:{actual_port}" if client_id == BUILTIN_CLIENT_ID else f"http://localhost:{actual_port}/callback"
                    query["redirect_uri"] = redirect_uri
                    auth_url = f"https://login.microsoftonline.com/{config['tenant']}/oauth2/v2.0/authorize?{urllib.parse.urlencode(query)}"
                except RuntimeError as exc:
                    raise RuntimeError(str(exc)) from exc
            else:
                actual_port = requested_port
            
            self.log(f"打开授权页: {auth_url}")
            with ReusableTCPServer(("localhost", actual_port), OAuthCallbackHandler) as httpd:
                self.current_oauth_server = httpd
                httpd.oauth_code = None
                httpd.oauth_state = None
                httpd.oauth_error = None
                httpd.oauth_error_description = None
                threading.Thread(target=httpd.handle_request, daemon=True).start()
                try:
                    session, browser_name = open_automated_browser(
                        auth_url,
                        email=config["account_email"],
                        password=config["account_password"],
                        browser_mode=config["browser_mode"],
                        on_log=self.log,
                        stop_event=self.cancel_event,
                    )
                except AutofillSkipError as exc:
                    self.log(f"✗ 跳过: {config['account_email']} — {exc}")
                    raise RuntimeError(f"自动登录失败: {exc}") from exc
                self.current_browser_session = session
                self.log(f"已使用 {browser_name} 打开授权页")
                deadline = time.time() + config["timeout"]
                while time.time() < deadline and not (httpd.oauth_code or httpd.oauth_error):
                    if self.cancel_event.is_set():
                        raise RuntimeError("已取消")
                    time.sleep(0.3)
                    # 回退：从URL解析code
                    if not httpd.oauth_code and session and session.get("page"):
                        try:
                            page_url = session["page"].url
                            if "localhost" in page_url and "code=" in page_url:
                                parsed = urllib.parse.urlparse(page_url)
                                params = urllib.parse.parse_qs(parsed.query)
                                url_code = params.get("code", [None])[0]
                                url_state = params.get("state", [None])[0]
                                if url_code:
                                    httpd.oauth_code = url_code
                                    httpd.oauth_state = url_state
                                    self.log(f"从URL解析到code")
                        except Exception:
                            pass
                if httpd.oauth_error:
                    detail = f"{httpd.oauth_error} {httpd.oauth_error_description or ''}".strip()
                    raise RuntimeError(f"授权失败: {detail}")
                if not httpd.oauth_code:
                    raise RuntimeError("等待授权回调超时")
                if httpd.oauth_state != state:
                    raise RuntimeError("授权 state 校验失败")
                tokens = exchange_authorization_code(
                    client,
                    config["tenant"],
                    client_id,
                    httpd.oauth_code,
                    redirect_uri,
                    config["scopes"],
                    code_verifier,
                )
            ensure_account_matches(tokens, config["account_email"])
            if not tokens.get("refresh_token"):
                raise RuntimeError("授权成功但未获取到refresh_token，请检查scope是否包含offline_access")
            self.finish_success(tokens, config)
        except Exception as exc:
            self.finish_error(exc)
        finally:
            self.current_oauth_server = None
            self.close_browser_session()

    def start_device_code(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            return
        if not config["account_email"]:
            messagebox.showerror("错误", "请先填写邮箱")
            return
        if not config["account_password"]:
            messagebox.showerror("错误", "请先填写密码")
            return
        if self.busy:
            return
        self.cancel_event.clear()
        self.set_busy(True, "正在进行 Device Code 授权")
        threading.Thread(target=self.device_code_worker, args=(config,), daemon=True).start()

    def device_code_worker(self, config: dict) -> None:
        client = NetworkClient(timeout=30)
        self.network = client
        client_id = config["client_id"] or BUILTIN_CLIENT_ID

        try:
            info = request_device_code(client, config["tenant"], config["scopes"], client_id)
            url = info.verification_uri_complete or info.verification_uri
            self.log(f"Device Code 客户端: {BUILTIN_CLIENT_NAME if client_id == BUILTIN_CLIENT_ID else client_id}")
            self.log(f"打开: {info.verification_uri}")
            self.log(f"验证码: {info.user_code}")
            try:
                session, browser_name = open_automated_browser(
                    url,
                    email=config["account_email"],
                    password=config["account_password"],
                    device_code=info.user_code,
                    browser_mode=config["browser_mode"],
                    on_log=self.log,
                    stop_event=self.cancel_event,
                )
            except AutofillSkipError as exc:
                self.log(f"✗ 跳过: {config['account_email']} — {exc}")
                raise RuntimeError(f"自动登录失败: {exc}") from exc
            self.current_browser_session = session
            self.log(f"已使用 {browser_name} 打开验证页")
            self.set_status("等待网页登录完成")

            deadline = time.time() + info.expires_in
            interval = max(1, info.interval)
            tokens = None
            while time.time() < deadline:
                if self.cancel_event.wait(interval):
                    raise RuntimeError("已取消")
                try:
                    tokens = poll_device_code(client, config["tenant"], info.device_code, client_id)
                except DeviceAuthorizationError as exc:
                    if str(exc) == "slow_down":
                        interval += 5
                        self.set_status(f"微软要求放慢轮询，当前间隔 {interval} 秒")
                        self.log(f"微软要求放慢轮询，当前间隔 {interval} 秒")
                        continue
                    raise RuntimeError(str(exc)) from exc
                if tokens:
                    break
                self.set_status("等待网页登录完成")
                self.log("等待网页登录完成...")
            if not tokens:
                raise RuntimeError("网页登录验证码已过期，请重新获取")
            ensure_account_matches(tokens, config["account_email"])
            self.finish_success(tokens, config)
        except Exception as exc:
            self.finish_error(exc)
        finally:
            self.close_browser_session()

    def start_refresh(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            return
        # 优先使用界面上的 refresh_token，如果为空则从文件选择
        ui_refresh = config.get("refresh_token", "").strip()
        if ui_refresh:
            if not config["account_email"]:
                messagebox.showerror("错误", "界面有 Refresh Token 但邮箱为空，请先填写邮箱")
                return
            if self.busy:
                return
            self.cancel_event.clear()
            self.set_busy(True, "正在刷新已有凭证")
            threading.Thread(target=self.refresh_worker_direct, args=(config, ui_refresh), daemon=True).start()
        else:
            path = filedialog.askopenfilename(
                title="选择已有四凭证文件",
                initialdir=config["output_dir"],
                filetypes=[("Credential files", "*.txt *.json"), ("All files", "*.*")],
            )
            if not path or self.busy:
                return
            self.cancel_event.clear()
            self.set_busy(True, "正在刷新已有凭证")
            threading.Thread(target=self.refresh_worker, args=(config, path), daemon=True).start()

    def refresh_worker(self, config: dict, path: str) -> None:
        client = NetworkClient(timeout=30)
        self.network = client
        try:
            refresh = load_refresh_token_from_file(path)
            tokens = refresh_access_token(
                client,
                config["tenant"],
                config["client_id"] or BUILTIN_CLIENT_ID,
                refresh,
                config["scopes"],
            )
            ensure_account_matches(tokens, config["account_email"])
            self.finish_success(tokens, config)
        except Exception as exc:
            self.finish_error(exc)

    def refresh_worker_direct(self, config: dict, refresh: str) -> None:
        """直接使用界面上的 refresh_token 刷新"""
        client = NetworkClient(timeout=30)
        self.network = client
        try:
            tokens = refresh_access_token(
                client,
                config["tenant"],
                config["client_id"] or BUILTIN_CLIENT_ID,
                refresh,
                config["scopes"],
            )
            ensure_account_matches(tokens, config["account_email"])
            self.finish_success(tokens, config)
        except Exception as exc:
            self.finish_error(exc)

    def finish_success(self, tokens: dict, config: dict) -> None:
        self.latest_tokens = dict(tokens or {})
        output = token_output_path(
            config["output_dir"],
            tokens,
            config["account_email"],
            config["auto_name"],
        )
        path = save_combo_line(
            tokens,
            output,
            config["account_email"],
            config["account_password"],
            config["client_id"] or BUILTIN_CLIENT_ID,
        )
        account = config["account_email"] or account_from_tokens(tokens)

        def update_ui():
            new_rt = tokens.get('refresh_token', '')
            if new_rt:
                self.refresh_token.set(new_rt)
            self.log("获取成功")
            self.log(f"账号: {account}")
            self.log(f"网络通道: {self.network.last_route_name}")
            self.log(f"四凭证文件: {path}")
            self.log(f"access_token: {mask_token(tokens.get('access_token', ''))}")
            self.log(f"refresh_token: {mask_token(tokens.get('refresh_token', ''))}")
            # 从导入列表中移除已成功的账号，并删除源文件
            email_lower = (account or "").strip().lower()
            if email_lower and getattr(self, "imported_credentials", None):
                before = len(self.imported_credentials)
                matched = [e for e in self.imported_credentials if (e.get("email", "").strip().lower() == email_lower)]
                self.imported_credentials = [e for e in self.imported_credentials if e.get("email", "").strip().lower() != email_lower]
                removed = before - len(self.imported_credentials)
                if removed:
                    self.log(f"[清理] 已从导入列表移除 {removed} 个账号，剩余 {len(self.imported_credentials)} 个")
                    for e in matched:
                        sp = e.get("source_path", "")
                        if sp and os.path.exists(sp):
                            try:
                                os.remove(sp)
                                self.log(f"[清理] 已删除源文件: {os.path.basename(sp)}")
                            except Exception as exc:
                                self.log(f"[清理] 删除失败: {os.path.basename(sp)} - {exc}")
            self.set_busy(False, "成功")
            messagebox.showinfo("成功", "四凭证已保存")

        self.after(0, update_ui)

    def finish_error(self, exc: Exception) -> None:
        if isinstance(exc, HttpResponseError):
            detail = json.dumps(exc.payload, ensure_ascii=False, indent=2)
        elif isinstance(exc, (AccountMismatchError, NetworkConnectionError, RuntimeError)):
            detail = str(exc)
        else:
            detail = repr(exc)

        def update_ui():
            self.log("错误:")
            self.log(detail)
            self.set_busy(False, "失败")
            messagebox.showerror("失败", detail)

        self.after(0, update_ui)

    def on_close(self) -> None:
        self.destroy()


def main() -> None:
    app = OutlookTokenToolApp()
    app.mainloop()


if __name__ == "__main__":
    main()
