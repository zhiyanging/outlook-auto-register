# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import socketserver
import time
from typing import Callable
import urllib.parse

from network import HttpResponseError, NetworkClient


DEFAULT_SCOPES = [
    "offline_access",
    "openid",
    "profile",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read",
]

BUILTIN_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
BUILTIN_CLIENT_NAME = "Microsoft Graph Command Line Tools"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@dataclass
class DeviceCodeInfo:
    user_code: str
    verification_uri: str
    device_code: str
    expires_in: int
    interval: int
    verification_uri_complete: str = ""


class DeviceAuthorizationError(RuntimeError):
    pass


class AccountMismatchError(RuntimeError):
    pass


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "OutlookTokenTool/2.0"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path not in ("", "/", "/callback"):
            self.send_response(404)
            self.end_headers()
            return

        self.server.oauth_code = params.get("code", [None])[0]
        self.server.oauth_state = params.get("state", [None])[0]
        self.server.oauth_error = params.get("error", [None])[0]
        self.server.oauth_error_description = params.get("error_description", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if self.server.oauth_code:
            message = "授权成功，可以关闭这个页面，回到 Outlook Token Tool。"
        else:
            message = "授权失败，请回到 Outlook Token Tool 查看错误。"

        self.wfile.write(
            f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Outlook Token Tool</title></head>
<body style="font-family: system-ui, sans-serif; padding: 32px;">
  <h1>{message}</h1>
</body>
</html>""".encode("utf-8")
        )

    def log_message(self, format, *args):
        return


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def is_port_available(port: int, host: str = "localhost") -> bool:
    """检查端口是否可用"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(start_port: int, host: str = "localhost", max_tries: int = 10) -> int:
    """从 start_port 开始寻找可用端口"""
    for offset in range(max_tries):
        port = start_port + offset
        if is_port_available(port, host):
            return port
    raise RuntimeError(f"端口 {start_port}-{start_port + max_tries - 1} 均被占用，请关闭占用端口的程序或手动指定其他端口")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_pkce_pair() -> tuple[str, str]:
    verifier = b64url(secrets.token_bytes(64))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def jwt_payload(token: str) -> dict:
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return {}


def safe_filename(value: str | None) -> str:
    value = (value or "outlook").strip().lower()
    value = re.sub(r"[^a-z0-9@._-]+", "_", value)
    return value.strip("._-") or "outlook"


def normalize_account(value: str | None) -> str:
    return (value or "").strip().lower()


def account_claim_from_tokens(tokens: dict) -> str:
    claims = jwt_payload(tokens.get("id_token", ""))
    for key in ("preferred_username", "email", "upn", "unique_name"):
        value = claims.get(key)
        if value:
            return str(value).strip()
    return ""


def account_from_tokens(tokens: dict, fallback: str = "") -> str:
    account = account_claim_from_tokens(tokens)
    if account:
        return account
    claims = jwt_payload(tokens.get("id_token", ""))
    value = claims.get("name")
    if value:
        return str(value)
    return fallback.strip() or "outlook_account"


def ensure_account_matches(tokens: dict, expected_account: str = "") -> str:
    expected = normalize_account(expected_account)
    actual = account_claim_from_tokens(tokens)
    if not expected:
        return actual or account_from_tokens(tokens)
    if not actual:
        raise AccountMismatchError(
            f"Token 里没有可识别的邮箱，已取消保存。你填写的是 {expected_account}，请重新登录并确认 scope 包含 openid profile。"
        )
    if normalize_account(actual) != expected:
        raise AccountMismatchError(
            f"账号不匹配，已取消保存。你填写的是 {expected_account}，但微软返回的是 {actual}。"
            "请在网页登录页切换到正确邮箱，或先退出浏览器里的错误 Microsoft 账号后重试。"
        )
    return actual


def mask_token(value: str, head: int = 14, tail: int = 8) -> str:
    if not value:
        return ""
    if len(value) <= head + tail + 3:
        return value[:head] + "..."
    return f"{value[:head]}...{value[-tail:]}"


def ensure_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    result = list(scopes or DEFAULT_SCOPES)
    if "offline_access" not in result:
        result.insert(0, "offline_access")
    return result


def token_output_path(output_dir: str, tokens: dict, fallback_account: str = "", auto_name: bool = True) -> str:
    os.makedirs(output_dir, exist_ok=True)
    account = account_from_tokens(tokens, fallback_account)
    if not auto_name and not fallback_account:
        account = "outlook_account"
    return os.path.join(output_dir, f"{safe_filename(account)}.txt")


def save_combo_line(
    tokens: dict,
    output: str,
    email: str = "",
    password: str = "",
    client_id: str = BUILTIN_CLIENT_ID,
) -> str:
    account = email.strip() or account_from_tokens(tokens)
    refresh = tokens.get("refresh_token", "")
    line = f"{account}----{password}----{client_id}----{refresh}"
    # 读取已有内容，检查是否已存在该账号
    existing_lines = []
    if os.path.exists(output):
        try:
            with open(output, "r", encoding="utf-8") as f:
                existing_lines = [l.rstrip("\n\r") for l in f.readlines() if l.strip()]
        except Exception:
            existing_lines = []
    # 查找并更新已有账号的行，或追加新行
    account_lower = account.strip().lower()
    found = False
    for i, existing in enumerate(existing_lines):
        parts = existing.split("----")
        if parts and parts[0].strip().lower() == account_lower:
            existing_lines[i] = line
            found = True
            break
    if not found:
        existing_lines.append(line)
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(existing_lines) + "\n")
    return output


def request_device_code(
    client: NetworkClient,
    tenant: str,
    scopes: list[str],
    client_id: str = BUILTIN_CLIENT_ID,
) -> DeviceCodeInfo:
    device_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
    data = client.post_form(device_url, {"client_id": client_id, "scope": " ".join(ensure_scopes(scopes))})

    user_code = data.get("user_code", "")
    verification_uri = data.get("verification_uri") or data.get("verification_url") or "https://www.microsoft.com/link"
    verification_uri_complete = data.get("verification_uri_complete", "")
    device_code = data.get("device_code", "")
    if not user_code or not device_code:
        raise DeviceAuthorizationError("微软没有返回 device_code/user_code，无法继续。")

    return DeviceCodeInfo(
        user_code=user_code,
        verification_uri=verification_uri,
        device_code=device_code,
        expires_in=int(data.get("expires_in", 900)),
        interval=int(data.get("interval", 5)),
        verification_uri_complete=verification_uri_complete,
    )


def poll_device_code(
    client: NetworkClient,
    tenant: str,
    device_code: str,
    client_id: str = BUILTIN_CLIENT_ID,
) -> dict | None:
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    try:
        return client.post_form(
            token_url,
            {
                "client_id": client_id,
                "grant_type": DEVICE_CODE_GRANT,
                "device_code": device_code,
            },
        )
    except HttpResponseError as exc:
        payload = exc.payload
        code = payload.get("error", "")
        desc = payload.get("error_description", "")
        if code == "authorization_pending":
            return None
        if code == "slow_down":
            raise DeviceAuthorizationError("slow_down")
        if code == "expired_token":
            raise DeviceAuthorizationError("验证码已过期，请重新点击获取 Token。") from exc
        if code == "authorization_declined":
            raise DeviceAuthorizationError("你在网页登录页取消了授权。") from exc
        if code == "bad_verification_code":
            raise DeviceAuthorizationError("验证码不正确，请重新点击获取 Token。") from exc
        raise DeviceAuthorizationError(desc or code or str(exc)) from exc


def device_code_authorize(
    client: NetworkClient,
    tenant: str,
    scopes: list[str],
    on_device_code: Callable[[DeviceCodeInfo], None],
    on_status: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    client_id: str = BUILTIN_CLIENT_ID,
) -> dict:
    info = request_device_code(client, tenant, scopes, client_id)
    on_device_code(info)

    deadline = time.time() + info.expires_in
    interval = max(1, info.interval)
    while time.time() < deadline:
        if should_cancel and should_cancel():
            raise DeviceAuthorizationError("已取消。")
        time.sleep(interval)
        try:
            tokens = poll_device_code(client, tenant, info.device_code, client_id)
        except DeviceAuthorizationError as exc:
            if str(exc) == "slow_down":
                interval += 5
                if on_status:
                    on_status(f"微软要求放慢轮询，当前间隔 {interval} 秒。")
                continue
            raise
        if tokens:
            return tokens
        if on_status:
            on_status("等待网页授权完成...")

    raise DeviceAuthorizationError("网页登录验证码已过期，请重新点击获取 Token。")


def exchange_authorization_code(
    client: NetworkClient,
    tenant: str,
    client_id: str,
    code: str,
    redirect_uri: str,
    scopes: list[str],
    code_verifier: str,
) -> dict:
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    return client.post_form(
        token_url,
        {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": " ".join(ensure_scopes(scopes)),
            "code_verifier": code_verifier,
        },
    )


def refresh_access_token(
    client: NetworkClient,
    tenant: str,
    client_id: str,
    refresh_token: str,
    scopes: list[str],
) -> dict:
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    tokens = client.post_form(
        token_url,
        {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(ensure_scopes(scopes)),
        },
    )
    if not tokens.get("refresh_token"):
        tokens["refresh_token"] = refresh_token
    return tokens
