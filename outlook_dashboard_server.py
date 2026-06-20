#!/usr/bin/env python3
"""Outlook 注册仪表盘 v2 — 状态面板 + 代理管理 + 手动操作"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from outlook_daemon_status import build_snapshot, save_status

PORT = int(os.environ.get("OUTLOOK_DASHBOARD_PORT", "8765"))
ROOT = Path(__file__).resolve().parent

MIHOMO_API = "http://127.0.0.1:29090"
MIHOMO_PROXY_PORT = 28888
MIHOMO_DIR = ROOT / "邮箱注册" / "mihomo_runtime"
SUBS_FILE = MIHOMO_DIR / "subscriptions.json"
RESIDENTIAL_FILE = MIHOMO_DIR / "residential_proxies.json"


# ─── 通用工具 ────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        return r.returncode, r.stdout[-3000:], r.stderr[-1000:]
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)[:300]


def _mihomo_get(path: str, timeout: float = 5) -> dict | None:
    try:
        req = urllib.request.Request(f"{MIHOMO_API}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _mihomo_put(path: str, data: dict, timeout: float = 5) -> bool:
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(f"{MIHOMO_API}{path}", data=body, method="PUT",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


# ─── 代理管理 ────────────────────────────────────────────────

def proxy_status() -> dict:
    """获取代理总状态"""
    version = _mihomo_get("/version")
    proxies_data = _mihomo_get("/proxies")
    if not proxies_data:
        return {"running": False, "nodes": 0, "current": "", "subscriptions": _load_subs()}

    auto = proxies_data.get("proxies", {}).get("AUTO", {})
    nodes = auto.get("all", [])
    # 去重
    seen = set()
    unique = []
    for n in nodes:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    # Add node count to each subscription
    subs = _load_subs()
    # Get all proxy details to check provider
    all_proxies = proxies_data.get("proxies", {})
    for sub in subs:
        # Count nodes from this subscription's provider
        provider_name = sub.get("name", "")
        count = 0
        for node_name in unique:
            proxy_info = all_proxies.get(node_name, {})
            # Check if this node belongs to this subscription
            # (simplified: count all nodes, as mihomo doesn't expose provider mapping directly)
            pass
        sub["node_count"] = len(unique)  # Total nodes for now

    return {
        "running": True,
        "version": version.get("version", "") if version else "",
        "nodes": len(unique),
        "current": auto.get("now", ""),
        "mode": proxies_data.get("mode", auto.get("type", "")),
        "subscriptions": subs,
        "residential": _load_residential(),
    }


def proxy_nodes() -> list[dict]:
    """获取所有节点详情（含延迟），按延迟排序"""
    data = _mihomo_get("/proxies")
    if not data:
        return []
    proxies = data.get("proxies", {})
    auto = proxies.get("AUTO", {})
    all_names = auto.get("all", [])
    current = auto.get("now", "")
    seen = set()
    result = []
    skip = {"COMPATIBLE", "DIRECT", "PASS", "PASS-RULE", "REJECT", "REJECT-DROP", "AUTO"}
    
    for name in all_names:
        if name in seen or name in skip:
            continue
        seen.add(name)
        info = proxies.get(name, {})
        history = info.get("history", [])
        delay = history[-1].get("delay", 0) if history else 0
        alive = info.get("alive", False)
        
        # Extract server/port if available in proxy config
        server = info.get("server", "")
        port = info.get("port", 0)
        
        result.append({
            "name": name,
            "type": info.get("type", ""),
            "delay": delay,
            "alive": alive,
            "current": name == current,
            "server": server,
            "port": port,
        })
    
    # Sort: alive nodes with lowest delay first, then dead nodes
    result.sort(key=lambda x: (
        0 if x["alive"] else 1,  # alive first
        x["delay"] if x["alive"] and x["delay"] > 0 else 99999,  # by delay
        x["name"]  # by name as tiebreaker
    ))
    
    return result


def proxy_switch_node(node_name: str) -> dict:
    """切换到指定节点"""
    if _mihomo_put("/proxies/AUTO", {"name": node_name}):
        return {"ok": True, "msg": f"已切换到 {node_name}"}
    return {"ok": False, "msg": f"切换失败: {node_name}"}


def proxy_test_node(node_name: str) -> dict:
    """测试单个节点延迟"""
    url = f"/group/AUTO/delay?url=http://connect.rom.miui.com/generate_204&timeout=5000"
    # 先触发所有节点测速
    try:
        req = urllib.request.Request(f"{MIHOMO_API}{url}")
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass
    time.sleep(1)
    # 读取结果
    data = _mihomo_get("/proxies")
    if data:
        info = data.get("proxies", {}).get(node_name, {})
        history = info.get("history", [])
        delay = history[-1].get("delay", 0) if history else 0
        return {"ok": True, "delay": delay, "alive": info.get("alive", False)}
    return {"ok": False, "delay": 0}


def proxy_test_all() -> dict:
    """触发所有节点测速并返回结果"""
    url = f"/group/AUTO/delay?url=http://connect.rom.miui.com/generate_204&timeout=5000"
    try:
        req = urllib.request.Request(f"{MIHOMO_API}{url}")
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass
    time.sleep(3)
    nodes = proxy_nodes()
    alive = [n for n in nodes if n.get("alive") and n.get("delay", 0) > 0]
    return {
        "ok": True,
        "tested": len(nodes),
        "alive": len(alive),
        "nodes": nodes,
    }


def proxy_delete_node(node_name: str) -> dict:
    """从 mihomo 配置中删除节点"""
    config_path = MIHOMO_DIR / "config.yaml"
    if not config_path.exists():
        return {"ok": False, "msg": "配置文件不存在"}
    
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        # Check if node is WARP (preserve it)
        if "WARP" in node_name.upper():
            return {"ok": False, "msg": "WARP 节点不允许删除"}
        
        # Remove from proxies list
        proxies = config.get("proxies", [])
        config["proxies"] = [p for p in proxies if p.get("name") != node_name]
        
        # Remove from proxy groups
        for group in config.get("proxy-groups", []):
            if "proxies" in group:
                group["proxies"] = [p for p in group["proxies"] if p != node_name]
        
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        
        # Reload mihomo config
        _mihomo_put("/configs", {"path": str(config_path)})
        
        return {"ok": True, "msg": f"已删除节点: {node_name}"}
    except Exception as e:
        return {"ok": False, "msg": f"删除失败: {str(e)}"}


def proxy_rename_node(old_name: str, new_name: str) -> dict:
    """重命名节点"""
    if not new_name or new_name == old_name:
        return {"ok": False, "msg": "新名称无效"}
    
    config_path = MIHOMO_DIR / "config.yaml"
    if not config_path.exists():
        return {"ok": False, "msg": "配置文件不存在"}
    
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        # Rename in proxies list
        for proxy in config.get("proxies", []):
            if proxy.get("name") == old_name:
                proxy["name"] = new_name
                break
        
        # Rename in proxy groups
        for group in config.get("proxy-groups", []):
            if "proxies" in group:
                group["proxies"] = [new_name if p == old_name else p for p in group["proxies"]]
        
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
        
        # Reload mihomo config
        _mihomo_put("/configs", {"path": str(config_path)})
        
        return {"ok": True, "msg": f"已重命名: {old_name} → {new_name}"}
    except Exception as e:
        return {"ok": False, "msg": f"重命名失败: {str(e)}"}


def proxy_get_exit_ip() -> dict:
    """获取当前出口 IP"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", MIHOMO_PROXY_PORT))
        s.close()
        
        proxy_url = f"http://127.0.0.1:{MIHOMO_PROXY_PORT}"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        
        req = urllib.request.Request("https://ipinfo.io/json")
        proxy_handler = urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        
        with opener.open(req, timeout=10) as r:
            d = json.loads(r.read())
        
        return {
            "ok": True,
            "ip": d.get("ip", ""),
            "country": d.get("country", ""),
            "region": d.get("region", ""),
            "city": d.get("city", ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def proxy_auto_rotate() -> dict:
    """自动切换到延迟最低的存活节点"""
    nodes = proxy_nodes()
    alive_nodes = [n for n in nodes if n.get("alive") and n.get("delay", 0) > 0]
    
    if not alive_nodes:
        return {"ok": False, "msg": "没有可用的存活节点"}
    
    # Pick the one with lowest delay
    best = min(alive_nodes, key=lambda x: x["delay"])
    
    if _mihomo_put("/proxies/AUTO", {"name": best["name"]}):
        return {
            "ok": True,
            "msg": f"已切换到最优节点: {best['name']} ({best['delay']}ms)",
            "node": best
        }
    return {"ok": False, "msg": "切换失败"}


def _load_subs() -> list[dict]:
    try:
        if SUBS_FILE.exists():
            return json.loads(SUBS_FILE.read_text("utf-8"))
    except Exception:
        pass
    return []


def _save_subs(subs: list[dict]):
    MIHOMO_DIR.mkdir(parents=True, exist_ok=True)
    SUBS_FILE.write_text(json.dumps(subs, ensure_ascii=False, indent=2), "utf-8")


def proxy_add_subscription(url: str, name: str = "") -> dict:
    """添加订阅链接"""
    url = url.strip()
    if not url:
        return {"ok": False, "msg": "URL 为空"}
    subs = _load_subs()
    for s in subs:
        if s["url"] == url:
            return {"ok": False, "msg": "该订阅已存在"}
    name = name or url.split("/")[-1][:32] or "sub"
    subs.append({"url": url, "name": name, "added": time.strftime("%Y-%m-%d %H:%M")})
    _save_subs(subs)
    return {"ok": True, "msg": f"已添加订阅: {name}"}


def proxy_remove_subscription(url: str) -> dict:
    """移除订阅链接"""
    subs = _load_subs()
    before = len(subs)
    subs = [s for s in subs if s["url"] != url]
    if len(subs) < before:
        _save_subs(subs)
        return {"ok": True, "msg": "已移除订阅"}
    return {"ok": False, "msg": "未找到该订阅"}


def proxy_refresh_subscriptions() -> dict:
    """刷新订阅：重新拉取所有订阅并重启 mihomo"""
    save_status({"phase": "proxy_refresh", "phase_message": "正在刷新订阅代理..."})
    code, out, err = _run(["bash", str(ROOT / "deploy" / "ensure_mihomo_proxy.sh")], timeout=120)
    ok = code == 0 and "PROXY_OK" in out
    ip = ""
    for line in out.splitlines():
        if "PROXY_OK" in line:
            parts = line.split()
            if len(parts) >= 2:
                ip = parts[1]
    msg = f"订阅刷新{'成功' if ok else '失败'}"
    if ip:
        msg += f" → 出口 {ip}"
    
    # Update node count per subscription
    if ok:
        time.sleep(3)  # Wait for mihomo to start
        subs = _load_subs()
        proxies_data = _mihomo_get("/proxies")
        if proxies_data:
            auto = proxies_data.get("proxies", {}).get("AUTO", {})
            all_nodes = auto.get("all", [])
            # Get all proxy details
            all_proxies = proxies_data.get("proxies", {})
            
            # Try to count nodes per subscription by checking provider info
            for sub in subs:
                # Count nodes that might belong to this subscription
                # (This is a best-effort approach since mihomo doesn't expose provider mapping directly)
                sub["node_count"] = len([n for n in all_nodes if n not in ("COMPATIBLE", "DIRECT", "PASS", "REJECT", "AUTO")])
            
            _save_subs(subs)
    
    save_status({"phase": "idle", "phase_message": msg})
    return {"ok": ok, "msg": msg, "output": out[-800:]}


# ─── 住宅代理 ────────────────────────────────────────────────

def _load_residential() -> list[dict]:
    try:
        if RESIDENTIAL_FILE.exists():
            return json.loads(RESIDENTIAL_FILE.read_text("utf-8"))
    except Exception:
        pass
    return []


def _save_residential(proxies: list[dict]):
    MIHOMO_DIR.mkdir(parents=True, exist_ok=True)
    RESIDENTIAL_FILE.write_text(json.dumps(proxies, ensure_ascii=False, indent=2), "utf-8")


def residential_add(proxy_str: str, name: str = "") -> dict:
    """添加住宅代理 (格式: http://user:pass@host:port 或 socks5://...)"""
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return {"ok": False, "msg": "代理地址为空"}
    proxies = _load_residential()
    for p in proxies:
        if p["proxy"] == proxy_str:
            return {"ok": False, "msg": "该代理已存在"}
    name = name or proxy_str.split("@")[-1][:20] if "@" in proxy_str else proxy_str[:20]
    proxies.append({"proxy": proxy_str, "name": name, "added": time.strftime("%Y-%m-%d %H:%M"), "active": False})
    _save_residential(proxies)
    return {"ok": True, "msg": f"已添加住宅代理: {name}"}


def residential_remove(proxy_str: str) -> dict:
    """移除住宅代理"""
    proxies = _load_residential()
    before = len(proxies)
    proxies = [p for p in proxies if p["proxy"] != proxy_str]
    if len(proxies) < before:
        _save_residential(proxies)
        return {"ok": True, "msg": "已移除住宅代理"}
    return {"ok": False, "msg": "未找到该代理"}


def residential_activate(proxy_str: str) -> dict:
    """激活住宅代理（写入 mihomo 配置并切换）"""
    proxies = _load_residential()
    target = None
    for p in proxies:
        p["active"] = False
        if p["proxy"] == proxy_str:
            p["active"] = True
            target = p
    if not target:
        return {"ok": False, "msg": "未找到该代理"}
    _save_residential(proxies)
    return {"ok": True, "msg": f"已激活住宅代理: {target['name']}（需刷新代理生效）"}


def residential_deactivate() -> dict:
    """停用所有住宅代理"""
    proxies = _load_residential()
    for p in proxies:
        p["active"] = False
    _save_residential(proxies)
    return {"ok": True, "msg": "已停用所有住宅代理"}


def residential_bulk_add(text: str) -> dict:
    """批量导入住宅代理（每行一个）"""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    added = 0
    for line in lines:
        r = residential_add(line)
        if r["ok"]:
            added += 1
    return {"ok": True, "msg": f"批量导入完成: {added}/{len(lines)} 成功"}


# ─── 手动操作按钮 ────────────────────────────────────────────

def action_register_1() -> dict:
    """注册 1 个 Outlook"""
    save_status({"phase": "registering", "phase_message": "正在注册 1 个 Outlook 账号..."})
    code, out, err = _run(
        [sys.executable, "outlook_launcher.py", "run", "--count", "1", "--shuffle", "--max-proxy-attempts", "12"],
        timeout=300,
    )
    ok_count = out.count("成功:")
    fail_count = 1 - ok_count
    save_status({
        "phase": "idle",
        "phase_message": f"注册完成：{ok_count} 成功 / {fail_count} 失败",
        "last_register_ok": ok_count,
        "last_register_fail": fail_count,
    })
    return {"ok": code == 0, "success": ok_count, "fail": fail_count, "output": out[-1200:]}


def action_register_5() -> dict:
    """注册 5 个 Outlook"""
    save_status({"phase": "registering", "phase_message": "正在注册 5 个 Outlook 账号..."})
    code, out, err = _run(
        [sys.executable, "outlook_launcher.py", "run", "--count", "5", "--shuffle", "--max-proxy-attempts", "12"],
        timeout=600,
    )
    ok_count = out.count("成功:")
    fail_count = 5 - ok_count
    save_status({
        "phase": "idle",
        "phase_message": f"注册完成：{ok_count} 成功 / {fail_count} 失败",
        "last_register_ok": ok_count,
        "last_register_fail": fail_count,
    })
    return {"ok": code == 0, "success": ok_count, "fail": fail_count, "output": out[-1200:]}


def action_fetch_rt() -> dict:
    """为三凭证账号（无RT）获取 refresh_token"""
    save_status({"phase": "rt_fetch", "phase_message": "正在获取三凭证账号的 refresh_token..."})
    code, out, err = _run(
        [sys.executable, str(ROOT / "post_register_fetch_rt.py")],
        timeout=300,
    )
    ok = code == 0 and ("RT_OK" in out or "refresh_token" in out.lower() or "成功" in out)
    msg = f"RT 获取{'完成' if ok else '失败'}"
    save_status({"phase": "idle", "phase_message": msg})
    return {"ok": ok, "msg": msg, "output": out[-800:]}


def action_push_github() -> dict:
    """同步凭证 + 推送到 cloud-register-email"""
    save_status({"phase": "syncing", "phase_message": "正在同步凭证到 GitHub..."})
    code, out, err = _run([sys.executable, "sync_credentials.py", "--push"], timeout=300)
    save_status({"phase": "idle", "phase_message": f"凭证推送{'成功' if code == 0 else '失败'}"})
    return {"ok": code == 0, "msg": f"推送{'成功' if code == 0 else '失败'}", "output": out[-500:]}


ACTIONS = {
    "register_1": action_register_1,
    "register_5": action_register_5,
    "fetch_rt": action_fetch_rt,
    "push_github": action_push_github,
}


# ─── HTML ────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Outlook 自动注册 · xzxyuan</title>
<style>
:root{--bg:#0f1419;--card:#1a2332;--text:#e7ecf3;--muted:#8b9cb3;--ok:#3dd68c;--bad:#f07178;--accent:#6cb6ff;--btn:#2563eb;--btn-hover:#1d4ed8;--border:#2a3548}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:1rem;line-height:1.5}
h1{font-size:1.4rem;margin:.2rem 0 .4rem}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:.8rem}
.grid{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{background:var(--card);border-radius:14px;padding:1rem 1.15rem;border:1px solid var(--border)}
.card h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 .6rem}
.pill{display:inline-block;padding:.15rem .5rem;border-radius:999px;font-size:.78rem;font-weight:600}
.pill.run{background:#1e3a2f;color:var(--ok)}
.pill.idle{background:#2a3040;color:var(--muted)}
.pill.work{background:#2a2840;color:#c4b5fd}
.pill.proxy{background:#1a2a40;color:var(--accent)}
dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:.3rem .8rem;font-size:.88rem}
dt{color:var(--muted)}
dd{margin:0}
a{color:var(--accent);text-decoration:none}
pre{margin:.5rem 0 0;font-size:.7rem;max-height:180px;overflow:auto;background:#0d1117;padding:.6rem;border-radius:8px;white-space:pre-wrap;word-break:break-all}
.btn{display:inline-block;padding:.5rem .9rem;border-radius:10px;font-size:.82rem;font-weight:600;cursor:pointer;border:none;color:#fff;background:var(--btn);transition:all .15s}
.btn:hover{background:var(--btn-hover);transform:translateY(-1px)}
.btn.ok{background:#059669}.btn.warn{background:#d97706}.btn.err{background:#dc2626}
.btn.sm{padding:.3rem .6rem;font-size:.75rem;border-radius:6px}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
.btn-row{display:flex;gap:.5rem;flex-wrap:wrap;margin:.6rem 0}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th,td{text-align:left;padding:.35rem .3rem;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500}
.ok-c{color:var(--ok)}.bad-c{color:var(--bad)}.warn-c{color:#d97706}

/* Tabs */
.tabs{display:flex;gap:0;margin-bottom:0;border-bottom:2px solid var(--border)}
.tab{padding:.5rem 1rem;cursor:pointer;font-size:.85rem;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none;padding-top:.8rem}
.tab-content.active{display:block}

/* Proxy */
.node-list{max-height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:8px}
.node-item{display:flex;align-items:center;justify-content:space-between;padding:.4rem .6rem;border-bottom:1px solid #1a2230;font-size:.82rem;cursor:pointer;transition:background .1s}
.node-item:hover{background:#1e2a3a}
.node-item.current{background:#1a2a40;border-left:3px solid var(--accent)}
.node-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.node-meta{display:flex;align-items:center;gap:.5rem;font-size:.75rem}
.node-delay{padding:.1rem .4rem;border-radius:4px;font-weight:600}
.delay-good{background:#0d3320;color:var(--ok)}
.delay-med{background:#332a0d;color:#d97706}
.delay-bad{background:#331010;color:var(--bad)}
.delay-none{background:#2a3040;color:var(--muted)}
.alive-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.alive-dot.on{background:var(--ok)}
.alive-dot.off{background:var(--bad)}

/* Input */
.input-group{display:flex;gap:.5rem;margin:.5rem 0}
.input-group input,.input-group textarea{flex:1;background:#0d1117;border:1px solid var(--border);color:var(--text);padding:.5rem;border-radius:8px;font-size:.85rem;outline:none}
.input-group input:focus,.input-group textarea:focus{border-color:var(--accent)}
.input-group textarea{min-height:80px;resize:vertical;font-family:monospace}
.input-group button{white-space:nowrap}

/* Toast */
#toast{position:fixed;top:1rem;right:1rem;background:#1a2332;border:1px solid var(--border);padding:.6rem 1rem;border-radius:10px;font-size:.85rem;display:none;z-index:99;max-width:400px}
.stat-big{font-size:2rem;font-weight:700;text-align:center}
.stat-label{font-size:.75rem;color:var(--muted);text-align:center}
</style>
</head>
<body>
<h1>📬 Outlook 自动注册</h1>
<p class="sub">xzxyuan · 每 4 小时 5 个 · <span id="clock"></span></p>

<!-- 统计卡片 -->
<div class="grid">
  <div class="card">
    <h2>📊 注册统计</h2>
    <div style="display:flex;justify-content:space-around;text-align:center;padding:.4rem 0">
      <div><div class="stat-big" style="color:var(--ok)">{{total_registrations}}</div><div class="stat-label">总注册数</div></div>
      <div><div class="stat-big" style="color:var(--accent)">{{today_registrations}}</div><div class="stat-label">今日注册</div></div>
      <div><div class="stat-big" style="color:#c4b5fd">{{total_runtime}}</div><div class="stat-label">运行总时长</div></div>
    </div>
  </div>
  <div class="card">
    <h2>运行状态</h2>
    <p><span class="pill {{phase_class}}">{{phase_label}}</span></p>
    <dl>
      <dt>当前任务</dt><dd>{{phase_detail}}</dd>
      <dt>下一轮注册</dt><dd>{{next_register}}</dd>
      <dt>上次成功</dt><dd>{{last_success}}</dd>
      <dt>距上次成功</dt><dd>{{hours_since}}</dd>
      <dt>守护重启</dt><dd>{{restart_count}}</dd>
      <dt>状态更新</dt><dd>{{updated}}</dd>
    </dl>
  </div>
</div>

<!-- 手动操作 -->
<div class="card" style="margin-top:.8rem">
  <h2>手动操作</h2>
  <div class="btn-row">
    <button class="btn" onclick="doAction('register_1')">📝 注册 1 个</button>
    <button class="btn" onclick="doAction('register_5')">📝 注册 5 个</button>
    <button class="btn warn" onclick="doAction('fetch_rt')">🔑 获取 RT</button>
    <button class="btn" onclick="doAction('push_github')">📤 推送 GitHub</button>
  </div>
  <p style="font-size:.75rem;color:var(--muted);margin:0">🔑 获取 RT = 仅为本地三凭证（无RT）账号获取 refresh_token</p>
  <pre id="action_log" style="display:none"></pre>
</div>

<!-- 代理管理（Tab） -->
<div class="card" style="margin-top:.8rem">
  <div class="tabs">
    <div class="tab active" onclick="switchTab('proxy-status')">📡 代理状态</div>
    <div class="tab" onclick="switchTab('proxy-nodes')">🌐 节点管理</div>
    <div class="tab" onclick="switchTab('proxy-subs')">📋 订阅管理</div>
    <div class="tab" onclick="switchTab('proxy-residential')">🏠 住宅代理</div>
  </div>

  <!-- 代理状态 -->
  <div id="tab-proxy-status" class="tab-content active">
    <dl id="proxy-info">
      <dt>mihomo</dt><dd id="p-running">检测中...</dd>
      <dt>出口代理</dt><dd id="p-proxy-url">—</dd>
      <dt>节点数</dt><dd id="p-nodes">—</dd>
      <dt>当前节点</dt><dd id="p-current">—</dd>
      <dt>出口 IP</dt><dd id="p-ip">—</dd>
    </dl>
    <div class="btn-row" style="margin-top:.6rem">
      <button class="btn sm" onclick="refreshProxy()">🔄 刷新代理</button>
      <button class="btn sm" onclick="testProxy()">🏓 测试出口</button>
    </div>
  </div>

  <!-- 节点管理 -->
  <div id="tab-proxy-nodes" class="tab-content">
    <div class="btn-row">
      <button class="btn sm" onclick="testAllNodes()">⚡ 全部测速</button>
      <button class="btn sm ok" onclick="autoRotate()">🎯 自动选择</button>
      <button class="btn sm" onclick="verifyExitIP()">🔍 验证出口</button>
      <button class="btn sm" onclick="loadNodes()">🔄 刷新列表</button>
      <span id="node-summary" style="font-size:.8rem;color:var(--muted);line-height:2"></span>
    </div>
    <div class="node-list" id="node-list">
      <div style="padding:1rem;text-align:center;color:var(--muted)">加载中...</div>
    </div>
  </div>

  <!-- 订阅管理 -->
  <div id="tab-proxy-subs" class="tab-content">
    <p style="font-size:.82rem;color:var(--muted);margin:0 0 .5rem">
      导入订阅链接后点击"刷新代理"即可生效。支持 V2Ray/Clash 订阅格式。
      <br>推荐注册 <a href="https://vostuo.com/#/register?code=6lbWUCoU" target="_blank">vostuo.com</a> 获取订阅。
    </p>
    <div class="input-group">
      <input type="text" id="sub-url" placeholder="粘贴订阅链接..."/>
      <input type="text" id="sub-name" placeholder="名称(可选)" style="max-width:120px"/>
      <button class="btn sm" onclick="addSub()">➕ 添加</button>
    </div>
    <div id="sub-list" style="margin-top:.5rem"></div>
    <div class="btn-row">
      <button class="btn sm" onclick="refreshProxy()">🔄 刷新代理（重新拉取订阅）</button>
    </div>
  </div>

  <!-- 住宅代理 -->
  <div id="tab-proxy-residential" class="tab-content">
    <p style="font-size:.82rem;color:var(--muted);margin:0 0 .5rem">
      住宅代理格式：每行一个 <code>http://user:pass@host:port</code> 或 <code>socks5://host:port</code>
    </p>
    <div class="input-group">
      <input type="text" id="res-proxy" placeholder="单个代理: http://user:pass@host:port"/>
      <button class="btn sm" onclick="addResidential()">➕ 添加</button>
    </div>
    <div class="input-group">
      <textarea id="res-bulk" placeholder="批量导入（每行一个代理）..."></textarea>
      <button class="btn sm" onclick="bulkAddResidential()">📥 批量导入</button>
    </div>
    <div class="btn-row">
      <button class="btn sm warn" onclick="deactivateResidential()">⏹ 停用住宅代理</button>
    </div>
    <div id="res-list" style="margin-top:.5rem"></div>
  </div>
</div>

<!-- 最近注册结果 -->
<div class="card" style="margin-top:.8rem">
  <h2>最近注册结果（{{results_count}} 条）</h2>
  {{results_table}}
</div>

<!-- 日志 -->
<div class="card" style="margin-top:.8rem">
  <h2>守护日志（尾部）</h2>
  <pre>{{log_tail}}</pre>
</div>

<div id="toast"></div>

<script>
const $ = id => document.getElementById(id);
setInterval(() => { $('clock').textContent = new Date().toLocaleString('zh-CN') }, 1000);

// ─── Tab 切换 ───
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelector(`.tab[onclick="switchTab('${name}')"]`).classList.add('active');
  $('tab-' + name).classList.add('active');
  if (name === 'proxy-nodes') loadNodes();
  if (name === 'proxy-subs') loadSubs();
  if (name === 'proxy-residential') loadResidential();
  if (name === 'proxy-status') loadProxyStatus();
}

// ─── Toast ───
let toastTimer;
function toast(msg, type='ok') {
  const t = $('toast');
  t.textContent = msg; t.style.display = 'block';
  t.style.borderColor = type==='ok' ? '#059669' : type==='err' ? '#dc2626' : '#d97706';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.style.display = 'none', 4000);
}

// ─── API 调用 ───
const BASE_PATH = window.location.pathname.startsWith('/api/outlook') ? '/api/outlook' : '';

async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  try {
    const r = await fetch(BASE_PATH + path, opts);
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('json')) {
      const text = await r.text();
      console.error('Non-JSON response from', BASE_PATH + path, ':', text.substring(0, 200));
      throw new Error('服务器返回非JSON响应 (HTTP ' + r.status + ')');
    }
    return await r.json();
  } catch(e) {
    if (e.message && e.message.includes('Non-JSON')) throw e;
    throw new Error('网络请求失败: ' + e.message);
  }
}

// ─── 手动操作 ───
async function doAction(action) {
  const btn = event.target;
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = '⏳ 执行中...';
  const logEl = $('action_log');
  logEl.style.display = 'block';
  logEl.textContent = '正在执行 ' + action + ' ...';
  try {
    const d = await api('/api/action/' + action, 'POST');
    if (d.ok) { toast('✅ ' + d.msg); } else { toast('❌ ' + d.msg, 'err'); }
    logEl.textContent = d.output || d.msg;
  } catch(e) { toast('❌ 请求失败: ' + e, 'err'); }
  btn.disabled = false;
  btn.textContent = orig;
  setTimeout(() => location.reload(), 2000);
}

// ─── 代理状态 ───
async function loadProxyStatus() {
  try {
    const d = await api('/api/proxy/status');
    $('p-running').textContent = d.running ? '✅ 运行中 ' + (d.version||'') : '❌ 未运行';
    $('p-proxy-url').textContent = d.running ? 'http://127.0.0.1:28888' : '—';
    $('p-nodes').textContent = d.nodes || '—';
    $('p-current').textContent = d.current || '—';
    renderSubList(d.subscriptions || []);
    renderResList(d.residential || []);
  } catch(e) { $('p-running').textContent = '❌ 连接失败'; }
}

async function testProxy() {
  toast('⏳ 测试中...');
  try {
    const d = await api('/api/proxy/test', 'POST');
    if (d.ok) {
      $('p-ip').textContent = d.ip + ' (' + (d.country||'') + ')';
      toast('✅ 出口: ' + d.ip);
    } else {
      $('p-ip').textContent = '❌ ' + (d.error||'失败');
      toast('❌ ' + (d.error||'测试失败'), 'err');
    }
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function refreshProxy() {
  toast('⏳ 正在刷新代理...');
  try {
    const d = await api('/api/proxy/refresh', 'POST');
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    loadProxyStatus();
  } catch(e) { toast('❌ ' + e, 'err'); }
}

// ─── 节点管理 ───
async function loadNodes() {
  $('node-list').innerHTML = '<div style="padding:1rem;text-align:center;color:var(--muted)">加载中...</div>';
  try {
    const d = await api('/api/proxy/nodes');
    const nodes = d.nodes || [];
    const alive = nodes.filter(n => n.alive).length;
    $('node-summary').textContent = `共 ${nodes.length} 个节点，${alive} 个存活`;
    renderNodeList(nodes);
  } catch(e) {
    $('node-list').innerHTML = '<div style="padding:1rem;color:var(--bad)">加载失败</div>';
  }
}

function renderNodeList(nodes) {
  if (!nodes.length) {
    $('node-list').innerHTML = '<div style="padding:1rem;text-align:center;color:var(--muted)">暂无节点</div>';
    return;
  }
  
  // Sort: alive first, then by delay
  const sorted = [...nodes].sort((a, b) => {
    if (a.alive !== b.alive) return a.alive ? -1 : 1;
    if (a.delay === 0 && b.delay === 0) return a.name.localeCompare(b.name);
    if (a.delay === 0) return 1;
    if (b.delay === 0) return -1;
    return a.delay - b.delay;
  });
  
  $('node-list').innerHTML = sorted.map(n => {
    const delay = n.delay || 0;
    let delayClass = 'delay-none', delayText = '—';
    if (delay > 0) {
      delayText = delay + 'ms';
      delayClass = delay < 200 ? 'delay-good' : delay < 500 ? 'delay-med' : 'delay-bad';
    }
    const isWarp = n.name.toUpperCase().includes('WARP');
    const deleteBtn = isWarp ? '' : `<button class="btn sm err" onclick="deleteNode('${n.name.replace(/'/g,"\\\\'")}')">🗑</button>`;
    return `<div class="node-item ${n.current?'current':''}">
      <span class="alive-dot ${n.alive?'on':'off'}" onclick="switchNode('${n.name.replace(/'/g,"\\\\'")}')" style="cursor:pointer"></span>
      <span class="node-name" onclick="switchNode('${n.name.replace(/'/g,"\\\\'")}')" style="cursor:pointer" title="${n.name}">${n.name}</span>
      <span class="node-meta">
        <span style="color:var(--muted)">${n.type}</span>
        <span class="node-delay ${delayClass}">${delayText}</span>
        ${n.current ? '<span style="color:var(--accent)">◆ 当前</span>' : ''}
      </span>
      <span class="node-actions">
        <button class="btn sm" onclick="renameNode('${n.name.replace(/'/g,"\\\\'")}')">✏️</button>
        ${deleteBtn}
      </span>
    </div>`;
  }).join('');
}

async function switchNode(name) {
  try {
    const d = await api('/api/proxy/nodes/' + encodeURIComponent(name), 'PUT');
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    setTimeout(loadNodes, 500);
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function deleteNode(name) {
  if (!confirm(`确定删除节点 "${name}"？`)) return;
  try {
    const d = await api('/api/proxy/delete-node', 'POST', {node_name: name});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) setTimeout(loadNodes, 500);
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function renameNode(oldName) {
  const newName = prompt(`重命名节点:\n当前: ${oldName}\n新名称:`, oldName);
  if (!newName || newName === oldName) return;
  try {
    const d = await api('/api/proxy/rename-node', 'PUT', {old_name: oldName, new_name: newName});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) setTimeout(loadNodes, 500);
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function verifyExitIP() {
  toast('⏳ 检测出口 IP...');
  try {
    const d = await api('/api/proxy/exit-ip', 'POST');
    if (d.ok) {
      $('p-ip').textContent = d.ip + ' (' + (d.country||'') + ')';
      toast('✅ 出口: ' + d.ip, 'ok');
    } else {
      $('p-ip').textContent = '❌ ' + (d.error||'失败');
      toast('❌ ' + (d.error||'检测失败'), 'err');
    }
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function autoRotate() {
  toast('⏳ 自动选择最佳节点...');
  try {
    const d = await api('/api/proxy/auto-rotate', 'POST');
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) setTimeout(loadNodes, 1000);
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function testAllNodes() {
  toast('⏳ 正在测速所有节点...');
  try {
    const d = await api('/api/proxy/test-all', 'POST');
    toast(`✅ 测速完成: ${d.alive}/${d.tested} 存活`);
    renderNodeList(d.nodes || []);
  } catch(e) { toast('❌ ' + e, 'err'); }
}

// ─── 订阅管理 ───
async function loadSubs() {
  try {
    const d = await api('/api/proxy/status');
    renderSubList(d.subscriptions || []);
  } catch(e) {}
}

function renderSubList(subs) {
  const el = $('sub-list');
  if (!subs.length) { el.innerHTML = '<p style="color:var(--muted);font-size:.82rem">暂无订阅</p>'; return; }
  el.innerHTML = '<table><tr><th>名称</th><th>链接</th><th>节点数</th><th>添加时间</th><th></th></tr>' +
    subs.map(s => {
      // Mask URL: show domain + *** + last 8 chars
      const url = s.url || '';
      let maskedUrl = url;
      try {
        const u = new URL(url);
        const domain = u.hostname;
        const last8 = url.slice(-8);
        maskedUrl = domain + '/***' + last8;
      } catch(e) {
        if (url.length > 20) {
          maskedUrl = url.substring(0, 15) + '***' + url.slice(-8);
        }
      }
      return `<tr>
      <td>${s.name||'—'}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${url}">${maskedUrl}</td>
      <td>${s.node_count||'—'}</td>
      <td>${s.added||'—'}</td>
      <td>
        <button class="btn sm" onclick="renameSub('${encodeURIComponent(url)}','${s.name||''}')">✏️</button>
        <button class="btn sm err" onclick="removeSub('${encodeURIComponent(url)}')">🗑</button>
      </td>
    </tr>`;
    }).join('') + '</table>';
}

async function addSub() {
  const url = $('sub-url').value.trim();
  const name = $('sub-name').value.trim();
  if (!url) { toast('请输入订阅链接', 'warn'); return; }
  try {
    const d = await api('/api/proxy/subscriptions', 'POST', {url, name});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) { $('sub-url').value = ''; $('sub-name').value = ''; loadSubs(); }
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function removeSub(url) {
  try {
    const d = await api('/api/proxy/subscriptions?url=' + url, 'DELETE');
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) loadSubs();
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function renameSub(url, oldName) {
  const newName = prompt(`重命名订阅:\n当前: ${oldName || '(未命名)'}\n新名称:`, oldName);
  if (!newName || newName === oldName) return;
  try {
    const d = await api('/api/proxy/rename-subscription', 'PUT', {url: decodeURIComponent(url), new_name: newName});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) loadSubs();
  } catch(e) { toast('❌ ' + e, 'err'); }
}

// ─── 住宅代理 ───
async function loadResidential() {
  try {
    const d = await api('/api/proxy/status');
    renderResList(d.residential || []);
  } catch(e) {}
}

function renderResList(proxies) {
  const el = $('res-list');
  if (!proxies.length) { el.innerHTML = '<p style="color:var(--muted);font-size:.82rem">暂无住宅代理</p>'; return; }
  el.innerHTML = '<table><tr><th>名称</th><th>代理</th><th>状态</th><th></th></tr>' +
    proxies.map(p => `<tr>
      <td>${p.name||'—'}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${p.proxy}">${p.proxy}</td>
      <td>${p.active ? '<span class="ok-c">● 活跃</span>' : '<span style="color:var(--muted)">○ 未激活</span>'}</td>
      <td>
        <button class="btn sm ok" onclick="activateRes('${encodeURIComponent(p.proxy)}')">✓ 激活</button>
        <button class="btn sm err" onclick="removeRes('${encodeURIComponent(p.proxy)}')">🗑</button>
      </td>
    </tr>`).join('') + '</table>';
}

async function addResidential() {
  const proxy = $('res-proxy').value.trim();
  if (!proxy) { toast('请输入代理地址', 'warn'); return; }
  try {
    const d = await api('/api/proxy/residential', 'POST', {proxy});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) { $('res-proxy').value = ''; loadResidential(); }
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function bulkAddResidential() {
  const text = $('res-bulk').value.trim();
  if (!text) { toast('请输入代理列表', 'warn'); return; }
  try {
    const d = await api('/api/proxy/residential/bulk', 'POST', {text});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) { $('res-bulk').value = ''; loadResidential(); }
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function activateRes(proxy) {
  try {
    const d = await api('/api/proxy/residential/activate', 'POST', {proxy: decodeURIComponent(proxy)});
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) loadResidential();
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function removeRes(proxy) {
  try {
    const d = await api('/api/proxy/residential?proxy=' + proxy, 'DELETE');
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) loadResidential();
  } catch(e) { toast('❌ ' + e, 'err'); }
}

async function deactivateResidential() {
  try {
    const d = await api('/api/proxy/residential/deactivate', 'POST');
    toast(d.ok ? '✅ ' + d.msg : '❌ ' + d.msg, d.ok ? 'ok' : 'err');
    if (d.ok) loadResidential();
  } catch(e) { toast('❌ ' + e, 'err'); }
}

// ─── 初始化 ───
loadProxyStatus();
</script>
</body>
</html>
"""


# ─── 渲染 ────────────────────────────────────────────────────

def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    if isinstance(ts, (int, float)):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    return str(ts)


def _render_results(rows: list) -> str:
    if not rows:
        return "<p style='color:var(--muted)'>暂无记录</p>"
    lines = ["<table><tr><th>时间</th><th>结果</th><th>邮箱</th><th>RT</th><th>说明</th></tr>"]
    for r in reversed(rows):
        ok = r.get("success")
        cls = "ok-c" if ok else "bad-c"
        label = "✅ 成功" if ok else "❌ 失败"
        email = (r.get("email") or "—")[:40]
        has_rt = "🔑" if r.get("refresh_token") else "—"
        err = (r.get("error") or "")[:45]
        ts = (r.get("ts") or "")[:19]
        lines.append(f"<tr><td>{ts}</td><td class='{cls}'>{label}</td><td>{email}</td><td>{has_rt}</td><td>{err}</td></tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _render_page(snap: dict) -> str:
    phase = snap.get("phase") or "unknown"
    labels = {
        "registering": ("work", "注册中", "正在执行注册"),
        "syncing": ("work", "同步中", "正在同步凭证到 Git"),
        "proxy_refresh": ("proxy", "代理刷新中", "正在刷新订阅代理"),
        "rt_fetch": ("work", "RT 获取中", "正在获取三凭证账号的 refresh_token"),
        "waiting": ("run", "等待中", "守护进程正常循环，等待下一轮"),
        "starting": ("run", "启动中", "刚启动或刚完成一轮"),
        "idle": ("idle", "空闲", "等待指令"),
        "unknown": ("idle", "未知", "等待守护进程写入状态"),
    }
    phase_class, phase_label, phase_detail = labels.get(phase, labels["unknown"])
    if snap.get("phase_message"):
        phase_detail = str(snap["phase_message"])

    log_tail = "\n".join(snap.get("log_tail") or [])[-6000:]
    recent = snap.get("recent_results") or []

    return (HTML
        .replace("{{phase_class}}", phase_class)
        .replace("{{phase_label}}", phase_label)
        .replace("{{phase_detail}}", phase_detail)
        .replace("{{next_register}}", _fmt_ts(snap.get("next_register_at")))
        .replace("{{last_success}}", snap.get("last_successful_batch_iso") or "—")
        .replace("{{hours_since}}", (
            f"{snap.get('hours_since_last_success'):.1f} 小时"
            if snap.get("hours_since_last_success") is not None else "—"
        ))
        .replace("{{restart_count}}", str(snap.get("daemon_restart_count") or 0))
        .replace("{{updated}}", snap.get("updated_at_iso") or "—")
        .replace("{{results_count}}", str(len(recent)))
        .replace("{{results_table}}", _render_results(recent))
        .replace("{{log_tail}}", log_tail or "(暂无日志)")
        .replace("{{total_registrations}}", str(snap.get("total_registrations", 0)))
        .replace("{{today_registrations}}", str(snap.get("today_registrations", 0)))
        .replace("{{total_runtime}}", str(snap.get("total_runtime", "—")))
    )


# ─── HTTP Handler ────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            try:
                return json.loads(self.rfile.read(length))
            except Exception:
                pass
        return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            snap = build_snapshot({})
            self._html(_render_page(snap))
            return

        if path == "/api/status":
            snap = build_snapshot({})
            self._json(snap)
            return

        # ─── Proxy API ───
        if path == "/api/proxy/status":
            self._json(proxy_status())
            return
        if path == "/api/proxy/nodes":
            self._json({"nodes": proxy_nodes()})
            return
        if path == "/api/proxy/test-all":
            self._json(proxy_test_all())
            return
        if path == "/api/proxy/subscriptions":
            self._json({"subscriptions": _load_subs()})
            return
        if path == "/api/proxy/residential":
            self._json({"residential": _load_residential()})
            return

        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        # ─── Actions ───
        if path.startswith("/api/action/"):
            action_name = path.rsplit("/", 1)[-1]
            fn = ACTIONS.get(action_name)
            if fn:
                threading.Thread(target=fn, daemon=True).start()
                self._json({"ok": True, "msg": f"已启动 {action_name}", "accepted": True})
                return

        # ─── Proxy API ───
        if path == "/api/proxy/refresh":
            threading.Thread(target=lambda: self._json(proxy_refresh_subscriptions()), daemon=True).start()
            self._json({"ok": True, "msg": "正在刷新...", "accepted": True})
            return

        if path == "/api/proxy/test":
            # Test current proxy
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect(("127.0.0.1", MIHOMO_PROXY_PORT))
                s.close()
                proxy_url = f"http://127.0.0.1:{MIHOMO_PROXY_PORT}"
                os.environ["NO_PROXY"] = "127.0.0.1,localhost"
                req = urllib.request.Request("https://ipinfo.io/json")
                proxy_handler = urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
                opener = urllib.request.build_opener(proxy_handler)
                with opener.open(req, timeout=15) as r:
                    d = json.loads(r.read())
                self._json({"ok": True, "ip": d.get("ip",""), "country": d.get("country","")})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200]})
            return

        if path == "/api/proxy/test-all":
            self._json(proxy_test_all())
            return

        if path == "/api/proxy/subscriptions":
            url = body.get("url", "")
            name = body.get("name", "")
            self._json(proxy_add_subscription(url, name))
            return

        if path == "/api/proxy/residential":
            proxy_str = body.get("proxy", "")
            self._json(residential_add(proxy_str))
            return

        if path == "/api/proxy/residential/bulk":
            text = body.get("text", "")
            self._json(residential_bulk_add(text))
            return

        if path == "/api/proxy/residential/activate":
            proxy_str = body.get("proxy", "")
            self._json(residential_activate(proxy_str))
            return

        if path == "/api/proxy/residential/deactivate":
            self._json(residential_deactivate())
            return

        if path == "/api/proxy/delete-node":
            node_name = body.get("node_name", "")
            self._json(proxy_delete_node(node_name))
            return

        if path == "/api/proxy/exit-ip":
            self._json(proxy_get_exit_ip())
            return

        if path == "/api/proxy/auto-rotate":
            self._json(proxy_auto_rotate())
            return

        self.send_error(404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        # PUT /api/proxy/nodes/:name - switch node
        if path.startswith("/api/proxy/nodes/"):
            node_name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            self._json(proxy_switch_node(node_name))
            return

        if path == "/api/proxy/rename-node":
            old_name = body.get("old_name", "")
            new_name = body.get("new_name", "")
            self._json(proxy_rename_node(old_name, new_name))
            return

        self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        params = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}

        if path == "/api/proxy/subscriptions":
            url = params.get("url", [""])[0]
            self._json(proxy_remove_subscription(urllib.parse.unquote(url)))
            return

        if path == "/api/proxy/residential":
            proxy_str = params.get("proxy", [""])[0]
            self._json(residential_remove(urllib.parse.unquote(proxy_str)))
            return

        self.send_error(404)


def main():
    save_status({"phase": "starting", "phase_message": "仪表盘 v2 已启动"})
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Outlook dashboard v2 http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
