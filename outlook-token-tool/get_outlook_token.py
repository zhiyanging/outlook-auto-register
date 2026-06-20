# -*- coding: utf-8 -*-
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.parse
import webbrowser

from network import HttpResponseError, NetworkClient, NetworkConnectionError
from oauth_core import (
    AccountMismatchError,
    BUILTIN_CLIENT_ID,
    BUILTIN_CLIENT_NAME,
    DEFAULT_SCOPES,
    OAuthCallbackHandler,
    ReusableTCPServer,
    account_from_tokens,
    device_code_authorize,
    ensure_account_matches,
    ensure_scopes,
    exchange_authorization_code,
    make_pkce_pair,
    mask_token,
    refresh_access_token,
    save_combo_line,
    token_output_path,
)


APP_DIR = os.path.dirname(os.path.abspath(__file__))


def redirect_uri_for_args(args, client_id):
    if client_id == BUILTIN_CLIENT_ID and not args.client_id:
        return f"http://localhost:{args.port}"
    return f"http://localhost:{args.port}/callback"


def authorize_with_callback(args, client, scopes, client_id):
    code_verifier, code_challenge = make_pkce_pair()
    redirect_uri = redirect_uri_for_args(args, client_id)
    state = os.urandom(18).hex()
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login" if args.account_email else "select_account",
    }
    if args.account_email:
        query["login_hint"] = args.account_email
    auth_url = f"https://login.microsoftonline.com/{args.tenant}/oauth2/v2.0/authorize?{urllib.parse.urlencode(query)}"

    with ReusableTCPServer(("localhost", args.port), OAuthCallbackHandler) as httpd:
        httpd.oauth_code = None
        httpd.oauth_state = None
        httpd.oauth_error = None
        httpd.oauth_error_description = None
        threading.Thread(target=httpd.handle_request, daemon=True).start()

        print("正在打开浏览器授权...")
        print(auth_url)
        if not args.no_open:
            webbrowser.open(auth_url)

        deadline = time.time() + args.timeout
        while time.time() < deadline and not (httpd.oauth_code or httpd.oauth_error):
            time.sleep(0.2)

        if httpd.oauth_error:
            raise SystemExit(f"授权失败: {httpd.oauth_error} {httpd.oauth_error_description or ''}".strip())
        if not httpd.oauth_code:
            raise SystemExit("等待授权超时")
        if httpd.oauth_state != state:
            raise SystemExit("授权 state 校验失败")

        return exchange_authorization_code(
            client,
            args.tenant,
            client_id,
            httpd.oauth_code,
            redirect_uri,
            scopes,
            code_verifier,
        )


def authorize_with_device_code(args, client, scopes, client_id):
    def on_device_code(info):
        print(f"客户端: {BUILTIN_CLIENT_NAME if client_id == BUILTIN_CLIENT_ID else client_id}")
        print(f"打开: {info.verification_uri}")
        print(f"验证码: {info.user_code}")
        print("网页登录完成后，命令行会自动继续。")
        if not args.no_open:
            webbrowser.open(info.verification_uri)

    last_status = {"value": ""}

    def on_status(message):
        if message != last_status["value"]:
            print(message)
            last_status["value"] = message

    return device_code_authorize(client, args.tenant, scopes, on_device_code, on_status=on_status, client_id=client_id)


def output_path_for(args, tokens):
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        return args.output
    return token_output_path(args.output_dir, tokens, args.account_email, args.auto_name)


def load_refresh_token_from_file(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        raise SystemExit(f"{path} 是空文件")
    try:
        previous = json.loads(raw)
    except json.JSONDecodeError:
        parts = raw.split("----")
        refresh = parts[3].strip() if len(parts) >= 4 else ""
        if not refresh:
            raise SystemExit(f"{path} 里没有 refresh_token")
        return refresh
    refresh = previous.get("refresh_token")
    if not refresh:
        raise SystemExit(f"{path} 里没有 refresh_token")
    return refresh


def print_success(tokens, output, route):
    account = account_from_tokens(tokens)
    print("\n获取成功")
    print(f"账号: {account}")
    print(f"网络通道: {route}")
    print(f"四凭证: {os.path.abspath(output)}")
    print(f"access_token: {mask_token(tokens.get('access_token', ''))}")
    print(f"refresh_token: {mask_token(tokens.get('refresh_token', ''))}")


def parse_args():
    parser = argparse.ArgumentParser(description="获取 Outlook / Microsoft Graph refresh_token，并只保存四凭证文本文件。")
    parser.add_argument("--account-email", default="", help="已有 Outlook 邮箱，用于登录提示和自动命名")
    parser.add_argument("--account-password", default="", help="仅用于导出 邮箱----密码----client_id----refresh_token")
    parser.add_argument("--tenant", default="consumers", help="个人 Outlook/Hotmail 使用 consumers")
    parser.add_argument("--client-id", default="", help="自定义 Application/Client ID")
    parser.add_argument("--builtin-client", action="store_true", help="使用内置 Microsoft Graph Command Line Tools client id")
    parser.add_argument("--device-code", action="store_true", help="使用 device-code 登录流程")
    parser.add_argument("--auth-code", action="store_true", help="使用 localhost 回调授权码流程")
    parser.add_argument("--refresh", action="store_true", help="使用已有 refresh_token 刷新 access_token")
    parser.add_argument("--input", default="tokens.json", help="刷新时读取的旧 JSON 或四凭证文本文件")
    parser.add_argument("--output", default="", help="保存的具体文本文件路径")
    parser.add_argument("--output-dir", default=APP_DIR, help="自动命名时的保存目录")
    parser.add_argument("--no-auto-name", dest="auto_name", action="store_false", help="不要按邮箱自动命名")
    parser.set_defaults(auto_name=True)
    parser.add_argument("--port", type=int, default=8765, help="auth-code 模式的本地回调端口")
    parser.add_argument("--timeout", type=int, default=180, help="auth-code 模式等待秒数")
    parser.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    parser.add_argument("--scopes", nargs="+", default=DEFAULT_SCOPES, help="OAuth scopes")
    return parser.parse_args()


def main():
    args = parse_args()
    scopes = ensure_scopes(args.scopes)
    client_id = BUILTIN_CLIENT_ID if args.builtin_client or not args.client_id else args.client_id
    client = NetworkClient(timeout=30)

    try:
        if args.refresh:
            refresh = load_refresh_token_from_file(args.input)
            tokens = refresh_access_token(client, args.tenant, client_id, refresh, scopes)
        elif args.auth_code or args.account_email:
            tokens = authorize_with_callback(args, client, scopes, client_id)
        else:
            tokens = authorize_with_device_code(args, client, scopes, client_id)

        ensure_account_matches(tokens, args.account_email)
        output = output_path_for(args, tokens)
        save_combo_line(tokens, output, args.account_email, args.account_password, client_id)
        print_success(tokens, output, client.last_route_name)
    except HttpResponseError as exc:
        raise SystemExit(json.dumps(exc.payload, ensure_ascii=False, indent=2))
    except AccountMismatchError as exc:
        raise SystemExit(str(exc))
    except NetworkConnectionError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
