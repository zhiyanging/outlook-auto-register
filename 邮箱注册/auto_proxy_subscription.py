#!/usr/bin/env python3
"""自动注册代理服务新账号 → 获取新订阅链接 → 更新 mihomo subscriptions.json。

默认使用 hidexx 免费试用流程：注册随机新账号、领取试用、提取订阅链接。
不保存账号密码到仓库；运行态只写入 mihomo_runtime/subscriptions.json。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

from hidexx_client import register_and_get_subscription, register_sso_with_outlook
from outlook_mail_reader import load_four_credentials
from subscription_proxy import SUBS_FILE, get_manager


def log(msg: str) -> None:
    print(msg, flush=True)


def update_subscription(url: str, name: str = "") -> None:
    SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data: list[dict] = []
    if SUBS_FILE.exists():
        try:
            data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = []
    now = int(time.time())
    stale_words = ("已过期", "无法访问", "搜索", "备用", "原clash", "free-proxy")
    cleaned: list[dict] = []
    seen: set[str] = {url}
    for s in data:
        old_url = str(s.get("url", "")).strip()
        old_name = str(s.get("name", "")).strip()
        if not old_url or old_url in seen:
            continue
        if old_name == name:
            continue
        if any(w in old_name or w in old_url for w in stale_words):
            continue
        created_at = int(s.get("created_at") or 0)
        if created_at and now - created_at > 3 * 24 * 3600:
            continue
        seen.add(old_url)
        cleaned.append(s)
    data = [{"name": name, "url": url, "created_at": now}] + cleaned[:9]
    SUBS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"已写入新订阅并清理旧订阅: kept={len(data)} file={SUBS_FILE}")


def has_working_proxy() -> bool:
    try:
        m = get_manager()
        if not m.subscriptions:
            return False
        ok, msg = m.start()
        if not ok:
            log(f"已有订阅启动失败: {msg}")
            return False
        t = m.test_proxy()
        log(f"已有订阅测试: {t}")
        return bool(t.get("ok"))
    except Exception as exc:
        log(f"已有订阅测试异常: {exc}")
        return False


def register_via_hidexx_cli(log_fn: Callable[[str], None]) -> str | None:
    """Fallback: call hidexx-linux daily --line 1 and parse a fresh subscription URL."""
    import re
    import subprocess

    root = Path(__file__).resolve().parents[1]
    candidates = [
        os.getenv("HIDEXX_BIN", ""),
        str(root.parent / "hidexx" / "hidexx-linux"),
        str(Path.home() / "hidexx" / "hidexx-linux"),
        str(Path.home() / "hidexx" / "hidexx-linux"),
    ]
    bin_path = next((Path(x) for x in candidates if x and Path(x).exists()), None)
    if not bin_path:
        log_fn("hidexx CLI 不存在，已检查: " + ", ".join(x for x in candidates if x))
        return None
    for attempt in range(3):
        log_fn(f"hidexx CLI 注册新账号 attempt {attempt + 1}/3")
        try:
            proc = subprocess.run(
                [str(bin_path), "daily", "--line", "1"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            urls = re.findall(r"https?://[^\s<>'\"\)]+", out)
            yaml_urls = [
                u.strip().rstrip(",.;")
                for u in urls
                if ".yaml" in u and "hidexx" not in u.lower()
            ]
            seen: set[str] = set()
            unique = []
            for url in yaml_urls:
                if url not in seen:
                    seen.add(url)
                    unique.append(url)
            if unique:
                log_fn(f"hidexx CLI 获取到 {len(unique)} 个订阅链接")
                return unique[0]
            log_fn(f"hidexx CLI 未输出可用订阅，exit={proc.returncode}")
        except Exception as exc:
            log_fn(f"hidexx CLI 异常: {exc}")
        time.sleep(10)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="自动注册代理新账号并更新 mihomo 订阅")
    parser.add_argument("--force", action="store_true", help="不检测现有订阅，强制注册新账号并更新订阅")
    parser.add_argument("--name", default="hidexx-auto", help="写入 subscriptions.json 的订阅名称")
    parser.add_argument("--proxy", default=os.getenv("HIDEXX_REGISTER_PROXY", ""), help="注册代理服务账号时使用的可选代理")
    args = parser.parse_args()

    if not args.force and has_working_proxy():
        log("现有代理订阅可用，无需注册新代理账号")
        return 0

    url = None
    root = Path(__file__).resolve().parents[1]
    creds = load_four_credentials(root)
    if creds:
        max_sso = int(os.getenv("HIDEXX_SSO_MAX_CREDS", "5"))
        for email, password, client_id, refresh_token in creds[:max_sso]:
            log(f"尝试 SSO 邮箱验证码注册: {email}")
            url = register_sso_with_outlook(email, password, client_id, refresh_token, log=log)
            if url:
                break
    if not url:
        log("未找到可用于收验证码的 Outlook 四凭证")

    if not url:
        url = register_and_get_subscription(log=log, proxy_url=args.proxy or None)
    if not url:
        log("API 注册未获取到订阅，尝试 hidexx-linux CLI fallback")
        url = register_via_hidexx_cli(log)
    if not url:
        log("未获取到新订阅链接")
        return 1
    update_subscription(url, args.name)
    log(f"已更新订阅: {args.name}")

    m = get_manager()
    ok, msg = m.start()
    log(f"mihomo start: {ok} {msg}")
    if not ok:
        return 2
    test = m.test_proxy()
    log(f"mihomo test: {test}")
    return 0 if test.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
