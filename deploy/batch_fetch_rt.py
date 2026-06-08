#!/usr/bin/env python3
"""
批量获取 RT — 逐个启动 device-code 流程，等待用户完成授权。
用法:
  python3 batch_fetch_rt.py [--proxy http://127.0.0.1:28888] [--limit 5]
  python3 batch_fetch_rt.py --single ievanstusdwjsf4ek9iub@outlook.com
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = ROOT / "outlook-token-tool"
sys.path.insert(0, str(TOOL_DIR))

from network import NetworkClient, HttpResponseError, NetworkConnectionError
from oauth_core import (
    BUILTIN_CLIENT_ID,
    DEFAULT_SCOPES,
    DeviceAuthorizationError,
    device_code_authorize,
    ensure_scopes,
    mask_token,
    save_combo_line,
)

RESULTS = ROOT / "runtime_outlook" / "results.jsonl"
RT_DIR = ROOT / "runtime_outlook" / "rt_tokens"
RT_DIR.mkdir(parents=True, exist_ok=True)


def find_emails_without_rt(limit: int = 5) -> list[dict]:
    if not RESULTS.exists():
        return []
    rows = []
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not d.get("success") or not d.get("email"):
            continue
        if d.get("refresh_token"):
            continue
        email = d["email"]
        rt_file = RT_DIR / f"{email}.txt"
        if rt_file.exists():
            content = rt_file.read_text(encoding="utf-8").strip()
            if "----" in content:
                parts = content.split("----")
                if len(parts) >= 4 and parts[3].strip():
                    continue
        rows.append(d)
    return rows[:limit]


def fetch_rt_for_email(email: str, password: str = "", proxy: str = "", timeout: int = 300) -> dict:
    """用 device-code 流程获取单个邮箱的 RT。"""
    client = NetworkClient(timeout=30)
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        client.refresh_routes()

    scopes = ensure_scopes(DEFAULT_SCOPES)
    client_id = BUILTIN_CLIENT_ID

    print(f"\n{'='*60}")
    print(f"获取 RT: {email}")
    print(f"{'='*60}")

    def on_device_code(info):
        print(f"\n📱 请在浏览器中打开: {info.verification_uri}")
        print(f"🔑 验证码: {info.user_code}")
        print(f"🔗 完整链接: {info.verification_uri_complete}")
        print(f"⏳ 等待授权完成 (最长 {timeout}s)...\n")

    last_msg = {"v": ""}

    def on_status(msg):
        if msg != last_msg["v"]:
            print(f"  {msg}")
            last_msg["v"] = msg

    try:
        tokens = device_code_authorize(
            client, "consumers", scopes,
            on_device_code=on_device_code,
            on_status=on_status,
            client_id=client_id,
        )
    except DeviceAuthorizationError as e:
        print(f"❌ 授权失败: {e}")
        raise
    except (HttpResponseError, NetworkConnectionError) as e:
        print(f"❌ 网络错误: {e}")
        raise

    output = str(RT_DIR / f"{email}.txt")
    save_combo_line(tokens, output, email, password, client_id)

    rt = tokens.get("refresh_token", "")
    print(f"\n✅ 获取成功: {email}")
    print(f"   refresh_token: {mask_token(rt)}")
    print(f"   已保存到: {output}")

    return {
        "ok": True,
        "email": email,
        "refresh_token": rt,
        "client_id": client_id,
        "output": output,
    }


def write_back_results_jsonl(email: str, rt: str):
    """把 RT 回写到 results.jsonl。"""
    if not RESULTS.exists() or not rt:
        return
    lines = RESULTS.read_text(encoding="utf-8").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        try:
            d = json.loads(lines[i])
        except json.JSONDecodeError:
            continue
        if d.get("email") == email and not d.get("refresh_token"):
            d["refresh_token"] = rt
            lines[i] = json.dumps(d, ensure_ascii=False)
            break
    RESULTS.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="批量获取 Outlook RT")
    parser.add_argument("--proxy", default="", help="代理地址")
    parser.add_argument("--limit", type=int, default=5, help="最多获取几个")
    parser.add_argument("--single", default="", help="只获取单个邮箱的 RT")
    parser.add_argument("--timeout", type=int, default=300, help="每个邮箱等待授权的超时秒数")
    args = parser.parse_args()

    if args.single:
        emails = [{"email": args.single, "password": ""}]
    else:
        emails = find_emails_without_rt(args.limit)
        if not emails:
            print("没有需要获取 RT 的邮箱（全部已有 RT）。")
            return

    print(f"找到 {len(emails)} 个需要获取 RT 的邮箱")
    for i, d in enumerate(emails, 1):
        print(f"  {i}. {d['email']}")

    success = 0
    fail = 0
    for i, d in enumerate(emails, 1):
        email = d["email"]
        password = d.get("password", "")
        try:
            result = fetch_rt_for_email(email, password, proxy=args.proxy, timeout=args.timeout)
            write_back_results_jsonl(email, result.get("refresh_token", ""))
            success += 1
        except Exception as e:
            print(f"❌ {email} 失败: {e}")
            fail += 1

    print(f"\n{'='*60}")
    print(f"批量 RT 获取完成: {success} 成功, {fail} 失败")
    print(f"结果保存在: {RT_DIR}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
