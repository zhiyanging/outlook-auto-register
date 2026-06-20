# Outlook 邮箱自动注册 — 完整部署与运维实战指南

> **本文档是所有实战部署、运行、排障经验的结晶。部署新节点或排障时优先查阅此文档。**

---

## 目录

1. [系统架构总览](#一系统架构总览)
2. [从零部署新节点](#二从零部署新节点)
3. [前端面板与内网穿透](#三前端面板与内网穿透)
4. [凭证管理与同步推送](#四凭证管理与同步推送)
5. [代理与网络配置](#五代理与网络配置)
6. [浏览器实例管理](#六浏览器实例管理)
7. [运维经验与踩坑总结](#七运维经验与踩坑总结)
8. [监控、告警与自动修复](#八监控告警与自动修复)
9. [多节点协同](#九多节点协同)
10. [FAQ 快速排障表](#十faq-快速排障表)

---

## 一、系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        Zo User Service Supervisor                │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │ Xvfb :98     │  │ WARP/usque proxy │  │ mihomo proxy     │   │
│  │ (虚拟显示)    │  │ (SOCKS5 :1080)   │  │ (HTTP :7890)     │   │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘   │
│         │                   │                      │             │
│  ┌──────▼───────────────────▼──────────────────────▼─────────┐   │
│  │              outlook_daemon_hub_primary.sh (Hub)           │   │
│  │  1. 启动 Xvfb    2. 启动代理    3. 启动前端面板            │   │
│  │  4. 启动 ngrok   5. exec outlook_daemon.py                 │   │
│  └──────────────────────┬────────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────▼────────────────────────────────────┐   │
│  │                outlook_daemon.py (核心守护进程)             │   │
│  │  ┌─────────────┐ ┌──────────────┐ ┌────────────────────┐  │   │
│  │  │ 注册循环     │ │ RT 提取       │ │ 凭证同步 (cron)   │  │   │
│  │  │ (4h/5账号)  │ │ (device code) │ │ (每天 00:00 push) │  │   │
│  │  └─────────────┘ └──────────────┘ └────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │        outlook_dashboard_server.py (前端面板 :8765)        │   │
│  │  Web UI + REST API + 状态展示 + 手动操作按钮               │   │
│  └──────────────────────────┬────────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼────────────────────────────────┐   │
│  │        Zo Space Route (前端反向代理)                        │   │
│  │  /outlook → iframe     /api/outlook/* → proxy :8765       │   │
│  │  域名: https://ts8.zocomputer.io                           │   │
│  └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 核心组件

| 组件 | 文件 | 作用 |
|------|------|------|
| **守护进程** | `outlook_daemon.py` | 每 4h 注册 5 个账号 + 获取 RT + 定时同步凭证 |
| **Hub 脚本** | `outlook_daemon_hub_primary.sh` | 启动所有依赖服务 + exec 守护进程 |
| **前端面板** | `outlook_dashboard_server.py` | Web UI（HTML/JS/CSS 单页面） + REST API |
| **注册核心** | `邮箱注册/cdp_outlook.py` | CDP 协议操控 Chrome 完成 Outlook 注册 |
| **RT 提取** | `post_register_fetch_rt.py` | 注册后通过 device code flow 获取 refresh_token |
| **凭证同步** | `sync_credentials.py` | 合并本地凭证 → git fetch+rebase → push 到私有仓库 |
| **浏览器清理** | `cleanup.py` | 清理幽灵 Chrome 进程防止 OOM |

---

## 二、从零部署新节点

### 2.1 前置要求

| 要求 | 最低配置 | 推荐配置 |
|------|----------|----------|
| OS | Linux (Ubuntu 20+) | Debian 12 / Ubuntu 22 |
| RAM | 2 GB | 4 GB+ |
| CPU | 1 core | 2 cores |
| Disk | 10 GB | 20 GB |
| Python | 3.10+ | 3.11+ |
| Chrome/Chromium | 系统安装 | `chromium` 包 |
| Xvfb | `xvfb` 包 | 必须 |

### 2.2 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/xingluoyuankong/outlook-auto-register.git Email-Register
cd Email-Register

# 2. 安装 Python 依赖
pip install -r requirements.txt
pip install playwright
playwright install chromium
playwright install-deps chromium

# 3. 安装系统依赖
apt-get install -y xvfb chromium-browser git

# 4. 初始化 runtime 目录
mkdir -p runtime_outlook/logs runtime_outlook/rt_tokens
mkdir -p 云端注册邮箱/三凭证 云端注册邮箱/四凭证

# 5. 配置 Git SSH (用于凭证推送)
# 将 SSH 私钥放到 ~/.ssh/ 并配置 config
cat > ~/.ssh/config <<'EOF'
Host github.com-cloud-register
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_cloud_register
    StrictHostKeyChecking no
EOF

# 6. 初始化凭证仓库
cd 云端注册邮箱
git init
git remote add origin git@github.com:<你的私有凭证仓库>.git
git fetch origin
git branch --track main origin/main 2>/dev/null || git checkout -b main
cd ..

# 7. 配置环境变量
export DISPLAY=:98
export OUTLOOK_DASHBOARD_PORT=8765
# (可选) 代理配置
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
```

### 2.3 Zo 环境部署

如果在 Zo 工作空间中部署，使用 User Service 注册：

```python
# 注册 Hub 脚本为 process 类型服务
register_user_service(
    label="outlook-register-daemon",
    mode="process",
    entrypoint="bash /home/workspace/Email-Register/outlook_daemon_hub_primary.sh",
    workdir="/home/workspace/Email-Register",
    env_vars={
        "DISPLAY": ":98",
        "OUTLOOK_DASHBOARD_PORT": "8765",
    }
)
```

### 2.4 验证部署

```bash
# 检查进程
ps aux | grep -E "(outlook_daemon|dashboard|Xvfb)" | grep -v grep

# 检查前端
curl -s http://localhost:8765/api/status | python3 -m json.tool

# 手动触发一轮注册
curl -s http://localhost:8765/api/trigger_round -X POST | python3 -m json.tool
```

---

## 三、前端面板与内网穿透

### 3.1 前端面板架构

```
用户浏览器
    │
    ▼
┌─────────────────────────────┐
│  Zo Space Route (/outlook)  │  ← HTTPS, 公网可达
│  ┌────────────────────────┐ │
│  │ iframe src=/api/outlook│ │
│  └───────────┬────────────┘ │
└──────────────┼──────────────┘
               ▼
┌─────────────────────────────┐
│  /api/outlook/:path{.*}     │  ← API Route (Hono)
│  反向代理 → localhost:8765  │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│  dashboard_server.py :8765  │  ← Python HTTP Server
│  纯 HTML/JS/CSS 单页面      │
│  REST API (JSON)            │
└─────────────────────────────┘
```

### 3.2 Zo Space Route 配置

**页面路由** `/outlook`:
```tsx
export default function OutlookDashboard() {
  return (
    <div style={{ width: "100%", height: "100vh", overflow: "hidden" }}>
      <iframe src="/api/outlook/" style={{ width: "100%", height: "100%", border: "none" }} />
    </div>
  );
}
```

**API 路由** `/api/outlook` 和 `/api/outlook/:path{.*}`:
```typescript
// 统一反向代理到 localhost:8765
// 支持 GET/POST/PUT/DELETE 所有方法
// 支持 JSON 和 multipart/form-data body
// 转发所有 headers（除 Host/Connection）
```

### 3.3 内网穿透方案对比

| 方案 | 固定域名 | 免费 | 稳定性 | 推荐度 |
|------|----------|------|--------|--------|
| **Zo Space Route** | ✅ 固定 | ✅ | ⭐⭐⭐⭐⭐ | 🏆 首选 |
| **ngrok 付费版** | ✅ 固定 | ❌ | ⭐⭐⭐⭐ | 备选 |
| **ngrok 免费版** | ❌ 每次变 | ✅ | ⭐⭐⭐ | 临时用 |
| **Cloudflare Tunnel** | ✅ 固定 | ✅ | ⭐⭐⭐⭐ | 独立部署时推荐 |
| **SSH 反向隧道** | ❌ | ✅ | ⭐⭐ | 应急 |

### 3.4 独立部署时的 Cloudflare Tunnel 方案

```bash
# 安装 cloudflared
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

# 登录
cloudflared tunnel login

# 创建隧道
cloudflared tunnel create outlook-register

# 配置
cat > ~/.cloudflared/config.yml <<'EOF'
tunnel: outlook-register
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: outlook.yourdomain.com
    service: http://localhost:8765
  - service: http_status:404
EOF

# DNS
cloudflared tunnel route dns outlook-register outlook.yourdomain.com

# 运行
cloudflared tunnel run outlook-register
```

### 3.5 安全注意事项

- **Zo Space Route** 默认有 Zo 认证保护
- **Cloudflare Tunnel** 建议配合 Cloudflare Access (Zero Trust) 添加认证
- **ngrok** 建议使用 `--basic-auth` 参数
- 面板 API 的敏感操作（注册、同步）已有日志记录
- **绝对不要**将面板暴露在公网且无认证

---

## 四、凭证管理与同步推送

### 4.1 凭证类型

| 类型 | 文件名格式 | 内容格式 | 说明 |
|------|-----------|----------|------|
| **三凭证** | `email@outlook.com.txt` | `email----password----client_id` | 注册即有 |
| **四凭证** | `email@outlook.com.txt` | `email----password----client_id----refresh_token` | RT 获取成功后 |

### 4.2 存储结构

```
云端注册邮箱/
├── 三凭证/
│   ├── 2026-06-07/
│   │   ├── user1@outlook.com.txt
│   │   └── user2@outlook.com.txt
│   ├── 2026-06-08/
│   └── ...
├── 四凭证/
│   ├── 2026-06-06/
│   ├── 2026-06-07/
│   └── ...
├── all_success.jsonl          ← 唯一真实来源
├── README.md
└── .git/
```

### 4.3 sync_credentials.py 核心逻辑

```
每次执行 sync_credentials.py --push:

1. 扫描 sources:
   ├── runtime_outlook/results.jsonl (主 daemon 注册结果)
   ├── 自动化定时注册Outlook邮箱/runtime_outlook/results.jsonl (备用)
   ├── runtime_outlook/rt_tokens/ (RT token 文件)
   └── EXTRA_CRED_DIRS (额外凭证目录, 如 /home/workspace/6/)

2. 合并规则:
   ├── 新邮箱 + 有 RT → 只写四凭证
   ├── 新邮箱 + 无 RT → 写三凭证 (除非已有四凭证)
   ├── 已有 + 三→四升级 → 写四凭证 + 删除三凭证
   ├── 已有 + 三→三 → 跳过 (不覆盖)
   └── 已有 + 四→四 → 跳过 (不覆盖)

3. Git 同步:
   ├── _ensure_clean_git_state() → 清理卡住的 rebase/merge
   ├── _normalize_at_filenames() → _at_ → @ 文件名修复
   ├── git add 三凭证/ 四凭证/ all_success.jsonl
   ├── git commit
   ├── git fetch origin
   ├── git rebase origin/main (失败则 merge -X theirs)
   └── git push (最多重试 3 次)

4. 成功后:
   └── _archive_old_local_credentials() → 移到 已推送凭证/
```

### 4.4 ⚠️ 凭证推送踩坑经验

**坑 1: Git Rebase 冲突导致推送永久卡死**

```
症状: sync_credentials.py 每次执行都 exit=1
原因: 一次 rebase 冲突未解决，repo 卡在 "interactive rebase in progress"
解决: 
  git rebase --abort          # 或 git rebase --continue
  # 然后手动解决冲突
改进: _ensure_clean_git_state() 会在每次推送前自动检测并 abort 卡住的状态
```

**坑 2: `@` vs `_at_` 文件名不一致**

```
症状: 同一个邮箱出现两个文件 (user@outlook.com.txt 和 user_at_outlook.com.txt)
原因: 不同节点/工具使用了不同的文件名格式
解决: _normalize_at_filenames() 自动将 _at_ 重命名为 @
规则: 统一使用 @ 作为文件名中的分隔符
```

**坑 3: push rejected (non-fast-forward)**

```
症状: git push 被拒绝
原因: 远程有其他节点推送的新 commit
解决: 脚本已内置 3 次重试:
  1. fetch → rebase → push
  2. 如果 rebase 失败 → merge -X theirs → push
  3. 如果还失败 → 放弃本次推送，等下次 cron
```

**坑 4: 四凭证覆盖三凭证的反向操作**

```
规则: 
  ✅ 四凭证可以覆盖三凭证 (升级)
  ❌ 三凭证不能覆盖四凭证 (降级)
  ❌ 四凭证不能覆盖四凭证 (已有 RT 不覆盖)
实现: rt_status dict 追踪每个邮箱的 RT 状态
```

---

## 五、代理与网络配置

### 5.1 代理架构

```
注册 Chrome 浏览器
    │
    ├── 优先: WARP (usque SOCKS5 :1080)
    │   └── Cloudflare WARP 出口，IP 信誉好
    │
    ├── 备选: mihomo (HTTP :7890)
    │   └── 订阅代理，支持多节点切换
    │
    └── 兜底: 直连 (不推荐，容易被封)
```

### 5.2 WARP 代理 (usque)

```bash
# 安装
apt install -y ca-certificates curl
mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.arano.id/apt/gpg.key | gpg --dearmor > /etc/apt/keyrings/arano-id.gpg
echo "deb [signed-by=/etc/apt/keyrings/arano-id.gpg] https://repo.arano.id/apt /" > /etc/apt/sources.list.d/arano-id.list
apt update && apt install usque

# 配置
usque generate --output /etc/usque/config.json

# 启动
usque socks -b 0.0.0.0 -p 1080 -c /etc/usque/config.json

# 验证
curl -x socks5h://127.0.0.1:1080 https://ipinfo.io
```

### 5.3 Mihomo 代理

```bash
# 配置文件路径
/etc/mihomo/config.yaml

# 订阅更新
python3 邮箱注册/subscription_proxy.py --update

# 启动
mihomo -d /etc/mihomo

# 验证
curl -x http://127.0.0.1:7890 https://ipinfo.io
```

### 5.4 代理选择策略

```python
# outlook_daemon.py 中的代理选择逻辑:
def get_proxy():
    # 1. 优先 WARP (IP 信誉最好)
    if warp_available():
        return "socks5://127.0.0.1:1080"
    # 2. 备选 mihomo
    if mihomo_available():
        return "http://127.0.0.1:7890"
    # 3. 兜底直连
    return None
```

### 5.5 ⚠️ 代理踩坑经验

| 问题 | 原因 | 解决 |
|------|------|------|
| IP 被封 | 同一 IP 注册过多 | 换代理 / 用 WARP (自动换 IP) |
| 注册页面加载超时 | 代理延迟高 | 增加超时时间 / 换节点 |
| "Sorry, something went wrong" | IP 信誉差 | 用 WARP 或高质量代理 |
| DNS 解析失败 | SOCKS5 DNS 泄漏 | 使用 `socks5h://` (远程 DNS) |
| 代理订阅过期 | 未自动更新 | 配置 subscription_proxy.py cron |

---

## 六、浏览器实例管理

### 6.1 浏览器生命周期

```
启动 Chrome (--remote-debugging-port)
    │
    ├── CDP 连接
    ├── 注册流程 (navigate → fill → submit → verify)
    ├── 注册完成
    │
    ├── RT 提取 (启动新 Chromium via Playwright)
    │   ├── 登录新注册邮箱
    │   ├── 获取 device code
    │   └── 提取 refresh_token
    │
    └── 清理
        ├── kill_orphan_chrome_processes()
        ├── CDPBrowser.close() → os.killpg()
        └── pkill 兜底
```

### 6.2 ⚠️ 幽灵浏览器问题 (最常见的 OOM 原因!)

```
症状: 
  - 内存逐渐升高直到 OOM
  - "Killed" 出现在日志中
  - ps aux 显示大量 chrome/chromium 进程

原因:
  - 注册异常中断 (超时/OOM/外部 kill)
  - Chrome 崩溃导致 _process 引用丢失
  - Playwright 启动的浏览器未被回收
  - 子进程存活但主进程已退出

解决:
  - daemon 每轮结束自动调用 kill_orphan_chrome_processes()
  - 不要手动 pkill -9 chrome (会误杀 Zo agent-browser!)
  - 使用 cleanup.py 的安全清理函数

安全检查:
  python3 -c "from 邮箱注册.cdp_outlook import kill_orphan_chrome_processes; kill_orphan_chrome_processes()"
```

### 6.3 Xvfb 虚拟显示

```bash
# 启动
Xvfb :98 -screen 0 1366x768x24 -ac -nolisten tcp &

# 环境变量
export DISPLAY=:98

# 验证
xdpyinfo -display :98 | head -5

# 注意:
# - 必须 -ac (disable access control)
# - 必须 -nolisten tcp (安全)
# - 分辨率 1366x768 模拟真实桌面
# - 如果 Xvfb 挂了，所有 Chrome 都会失败
```

---

## 七、运维经验与踩坑总结

### 7.1 注册成功率优化

| 优化项 | 效果 | 实现方式 |
|--------|------|----------|
| WARP 代理 | 成功率提升 30%+ | IP 信誉好，不被封 |
| 随机 User-Agent | 减少指纹检测 | 每次启动随机生成 |
| 随机延迟 | 减少行为检测 | 每步操作间随机 1-3s |
| 真实姓名生成 | 减少审核拒绝 | 使用 faker 生成真实姓名 |
| 密码复杂度 | 满足微软要求 | 大小写+数字+特殊字符, 12位+ |
| 清理 cookies | 避免关联 | 每次新 user-data-dir |

### 7.2 RT (Refresh Token) 获取经验

```
成功率: 约 93% (30/32)

获取方式: Device Code Flow
  1. POST /common/oauth2/v2.0/devicecode → 获取 device_code
  2. 用 Playwright Chromium 打开登录页面
  3. 自动填入邮箱 + 密码
  4. 输入 device code
  5. 等待授权完成
  6. POST /common/oauth2/v2.0/token → 获取 refresh_token

常见问题:
  - "Need too many tries" → 等 30 分钟再试
  - "Account locked" → 等 24 小时
  - 验证码出现 → 当前无自动解决方案，标记为失败
  - Playwright Chromium 启动慢 → 增加超时时间到 60s
```

### 7.3 Daemon 重启机制

```python
# Zo User Service Supervisor 自动重启
# 进程退出后 supervisor 会在几秒内重启
# 48 次重启 = 13 天内平均每天 3-4 次

常见重启原因:
  1. OOM (幽灵浏览器堆积) → 定期清理
  2. 代理断开 → 自动重连后恢复
  3. Chrome 崩溃 → 自动重启后恢复
  4. Python 异常 → 已加 try-except 保护
  5. Git 推送失败 → 不影响注册循环
```

### 7.4 关键经验总结

```
1. 【绝对不要手动 kill daemon】
   → 让 supervisor 管理，手动 kill 可能导致状态不一致

2. 【绝对不要用 pkill -9 chrome】
   → 会杀掉 Zo 的 agent-browser，导致整个工作空间不可用
   → 用 kill_orphan_chrome_processes() 安全清理

3. 【Xvfb 必须先于 Chrome 启动】
   → Chrome 没有 DISPLAY 会直接退出
   → Hub 脚本保证了启动顺序

4. 【runtime_outlook/ 目录不能清理】
   → 包含 logs、results.jsonl、rt_tokens
   → 这些是 daemon 的状态文件，清理后注册统计会丢失

5. 【凭证仓库必须用 rebase 而非 merge】
   → 多节点推送时 merge 会产生大量冲突
   → rebase + -X theirs 策略最安全

6. 【文件名统一用 @ 不用 _at_】
   → 不同工具生成的文件名格式不一致会导致重复
   → _normalize_at_filenames() 自动修复

7. 【代理质量决定注册成功率】
   → 免费代理成功率 < 30%
   → WARP 代理成功率 > 60%
   → 住宅代理成功率 > 80%

8. 【每轮注册后必须清理浏览器】
   → 4GB 内存的节点最多容忍 3-5 个幽灵 Chrome
   → daemon 内置清理，但手动触发注册时要手动清理

9. 【同步推送失败不影响注册】
   → 凭证先写到本地，下次 cron 再推
   → 但如果 git 卡在 rebase，所有后续推送都会失败！
   → _ensure_clean_git_state() 解决了这个问题

10. 【前端面板不影响注册】
    → 面板挂了只影响监控和手动操作
    → daemon 是完全独立的进程
```

---

## 八、监控、告警与自动修复

### 8.1 健康检查

```bash
# 1. 检查所有进程
ps aux | grep -E "(outlook_daemon|dashboard|Xvfb|usque|mihomo)" | grep -v grep

# 2. 检查前端可达
curl -s http://localhost:8765/api/status | python3 -m json.tool

# 3. 检查 daemon 状态
curl -s http://localhost:8765/api/status | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'阶段: {d[\"phase\"]} - {d[\"phase_message\"]}')
print(f'总注册: {d[\"total_registrations\"]} | 今日: {d[\"today_registrations\"]}')
print(f'运行时长: {d[\"total_runtime\"]} | 重启次数: {d[\"daemon_restarts\"]}')
"

# 4. 检查 git 仓库状态
cd 云端注册邮箱 && git status && git log --oneline -3

# 5. 检查幽灵浏览器
ps aux | grep -c "[c]hrom"  # 应该 < 10
```

### 8.2 告警条件

| 指标 | 告警阈值 | 动作 |
|------|----------|------|
| 连续失败 | ≥ 3 轮 | 检查代理 + 浏览器 |
| 幽灵 Chrome | ≥ 10 进程 | 执行清理 |
| 内存使用 | ≥ 80% | 清理浏览器 + 检查泄漏 |
| Git push 失败 | 连续 2 天 | 检查 SSH + 仓库冲突 |
| Daemon 重启 | ≥ 5 次/小时 | 检查 OOM / 异常日志 |
| 面板不可达 | > 5 分钟 | 重启面板服务 |

### 8.3 自动修复脚本

```bash
#!/bin/bash
# auto_fix.sh - 自动检测并修复常见问题

# 1. 如果 Xvfb 不在运行
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :98 -screen 0 1366x768x24 -ac -nolisten tcp &
    sleep 2
fi

# 2. 如果幽灵浏览器过多 (>15)
CHROME_COUNT=$(ps aux | grep -c "[c]hrom")
if [ "$CHROME_COUNT" -gt 15 ]; then
    python3 -c "from 邮箱注册.cdp_outlook import kill_orphan_chrome_processes; kill_orphan_chrome_processes()"
fi

# 3. 如果 git 仓库卡住
cd 云端注册邮箱
if git status 2>&1 | grep -q "rebase in progress\|merge in progress"; then
    git rebase --abort 2>/dev/null
    git merge --abort 2>/dev/null
fi
cd ..

# 4. 如果内存不足 (<500MB free)
FREE_MEM=$(free -m | awk '/Mem:/ {print $7}')
if [ "$FREE_MEM" -lt 500 ]; then
    python3 cleanup.py  # 紧急清理
fi
```

---

## 九、多节点协同

### 9.1 节点架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  主节点 zo   │     │  zo2 节点   │     │  gcore 节点 │
│  xzxyuan    │     │  user7fuda2 │     │  reedemma   │
│             │     │             │     │             │
│  daemon     │     │  daemon     │     │  daemon     │
│  dashboard  │     │  dashboard  │     │  dashboard  │
│  sync+push  │     │  只注册     │     │  只注册     │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────▼──────┐
                    │ GitHub 私有  │
                    │ 凭证仓库     │
                    └─────────────┘
```

### 9.2 分工

| 角色 | 节点 | 职责 |
|------|------|------|
| **主节点** | zo (xzxyuan) | 注册 + 凭证汇总 + git push |
| **工作节点** | zo2, gcore | 只注册，凭证由主节点收集 |

### 9.3 远程部署

```bash
# 部署到远程节点
bash deploy/remote_deploy.sh zo2
bash deploy/remote_deploy.sh gcore

# 收集远程节点凭证
python3 deploy/collect_all_nodes.py

# 触发远程节点注册
bash deploy/trigger_all_nodes_round.sh
```

---

## 十、FAQ 快速排障表

| 症状 | 可能原因 | 快速修复 |
|------|----------|----------|
| Daemon 不注册了 | 代理挂了 | `curl -x socks5h://127.0.0.1:1080 https://ipinfo.io` 检查 |
| "Killed" in log | OOM | `kill_orphan_chrome_processes()` + 增加 swap |
| 注册全部失败 | IP 被封 | 换 WARP / 等 1h |
| 面板打不开 | 8765 端口被占 | `lsof -i :8765` + kill 占用进程 |
| Git push 失败 | rebase 卡住 | `cd 云端注册邮箱 && git rebase --abort` |
| RT 获取失败 | 验证码 | 等 30min 手动重试 |
| Chrome 启动失败 | Xvfb 没运行 | `Xvfb :98 -screen 0 1366x768x24 -ac -nolisten tcp &` |
| 内存不足 | 幽灵浏览器 | `python3 cleanup.py` |
| 凭证重复 | 文件名不一致 | `sync_credentials.py --push` (自动修复) |
| 前端 iframe 空白 | API 路由挂了 | 检查 `/api/outlook` 路由配置 |
| Daemon 反复重启 | 未知异常 | 查 `runtime_outlook/logs/outlook_daemon.log` |
| sync exit=1 | git 状态异常 | `_ensure_clean_git_state()` (已内置) |

---

## 附录 A: 关键文件清单

```
Email-Register/
├── outlook_daemon.py                    # 核心守护进程
├── outlook_daemon_hub_primary.sh        # Hub 启动脚本
├── outlook_dashboard_server.py          # 前端面板服务
├── sync_credentials.py                  # 凭证同步推送
├── post_register_fetch_rt.py           # RT 获取
├── cleanup.py                           # 幽灵浏览器清理
├── integrated_server.py                 # 集成服务器
├── full_pipeline.py                     # 完整流水线
├── requirements.txt                     # Python 依赖
├── OPERATIONS.md                        # 运维规范
├── DEPLOY_GUIDE.md                      # 本文档
├── README.md                            # 项目说明
├── .gitignore                           # Git 忽略规则
├── 邮箱注册/                             # 核心注册库
│   ├── cdp_outlook.py                   # CDP Outlook 注册
│   ├── subscription_proxy.py            # 代理订阅管理
│   └── ...
├── deploy/                              # 部署脚本
│   ├── remote_deploy.sh
│   ├── collect_all_nodes.py
│   └── ...
├── outlook-token-tool/                  # RT 获取工具
│   ├── batch_rt.py
│   ├── get_outlook_token.py
│   └── ...
├── runtime_outlook/                     # 运行时数据 (gitignored)
│   ├── logs/
│   ├── rt_tokens/
│   └── results.jsonl
└── 云端注册邮箱/                         # 凭证仓库 (gitignored from main)
    ├── 三凭证/
    ├── 四凭证/
    ├── all_success.jsonl
    └── .git/
```

## 附录 B: 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DISPLAY` | `:98` | Xvfb 虚拟显示 |
| `OUTLOOK_DASHBOARD_PORT` | `8765` | 面板端口 |
| `HTTP_PROXY` | - | HTTP 代理 |
| `HTTPS_PROXY` | - | HTTPS 代理 |
| `NO_PROXY` | `localhost,127.0.0.1` | 不走代理的地址 |
| `CLOUD_REGISTER_EMAIL_REMOTE` | `git@github.com:...` | 凭证仓库远程地址 |
| `NGROK_AUTHTOKEN` | - | ngrok 认证 token |

## 附录 C: Cron 任务

```bash
# 每天 00:00 同步推送凭证 (由 daemon 内置 cron 处理，无需系统 crontab)
# 每天 06:00 更新代理订阅 (由 daemon 内置处理)

# 如果需要独立 cron:
0 0 * * * cd /home/workspace/Email-Register && python3 sync_credentials.py --push >> runtime_outlook/logs/sync.log 2>&1
0 6 * * * cd /home/workspace/Email-Register && python3 邮箱注册/subscription_proxy.py --update >> runtime_outlook/logs/proxy.log 2>&1
```

---

*文档版本: v2.0 | 最后更新: 2026-06-20 | 维护者: Zo 工作空间*
