#!/usr/bin/env python3
"""
RT 获取脚本 — 用 outlook-token-tool 的 device-code 流程获取 refresh_token。
用法:
  python3 fetch_rt_device_code.py --email xxx@outlook.com [--password xxx] [--proxy http://127.0.0.1:28888]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# 添加 outlook-token-tool 到路径
ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = ROOT / "outlook-token-tool"
sys.path.insert(0, str(TOOL_DIR))

from network import NetworkClient, HttpResponseError, NetworkConnectionError
from oauth_core import (
    BUILTIN_CLIENT_ID,
    DEFAULT_SCOPES,
    DeviceAuthorizationError,
    account_from_tokens,
    ensure_scopes,
    device_code_authorize,
    mask_token,
    refresh_access_token,
    save_combo_line,
    token_output_path,
)

RT_DIR = ROOT / "runtime_outlook" / "rt_tokens"
RT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_rt(email: str, password: str = "", proxy: str = "", timeout: int = 300) -> dict:
    """用 device-code 流程获取 RT。返回 tokens dict 或 raise。"""
    client = NetworkClient(timeout=30)
    # 如果有代理，注入到环境变量
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        client.refresh_routes()

    scopes = ensure_scopes(DEFAULT_SCOPES)
    client_id = BUILTIN_CLIENT_ID

    print(f"[RT] 开始 device-code 流程获取 {email} 的 refresh_token...")

    def on_device_code(info):
        print(f"[RT] 打开: {info.verification_uri}")
        print(f"[RT] 验证码: {info.user_code}")
        print(f"[RT] 完整链接: {info.verification_uri_complete}")
        print(f"[RT] 等待用户完成授权 (最长 {timeout}s)...")

    last_msg = {"v": ""}

    def on_status(msg):
        if msg != last_msg["v"]:
            print(f"[RT] {msg}")
            last_msg["v"] = msg

    try:
        tokens = device_code_authorize(
            client, "consumers", scopes,
            on_device_code=on_device_code,
            on_status=on_status,
            client_id=client_id,
        )
    except DeviceAuthorizationError as e:
        print(f"[RT] 授权失败: {e}")
        raise
    except (HttpResponseError, NetworkConnectionError) as e:
        print(f"[RT] 网络错误: {e}")
        raise

    # 保存四凭证
    output = str(RT_DIR / f"{email}.txt")
    save_combo_line(tokens, output, email, password, client_id)

    rt = tokens.get("refresh_token", "")
    print(f"[RT] ✅ 获取成功: {email}")
    print(f"[RT] refresh_token: {mask_token(rt)}")
    print(f"[RT] 已保存到: {output}")

    return {
        "ok": True,
        "email": email,
        "refresh_token": rt,
        "client_id": client_id,
        "output": output,
        "network_route": client.last_route_name,
    }


def main():
    parser = argparse.ArgumentParser(description="获取 Outlook RT (device-code 流程)")
    parser.add_argument("--email", required=True, help="Outlook 邮箱")
    parser.add_argument("--password", default="", help="邮箱密码")
    parser.add_argument("--proxy", default="", help="代理地址 (如 http://127.0.0.1:28888)")
    parser.add_argument("--timeout", type=int, default=300, help="等待授权超时秒数")
    args = parser.parse_args()

    try:
        result = fetch_rt(args.email, args.password, args.proxy, args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[RT] ❌ 失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
