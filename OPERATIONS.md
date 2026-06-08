# Outlook 邮箱注册 — 运维与部署要求

> 本文档定义了注册程序的**永久运行规范**，所有节点必须遵守。

---

## 一、永久保活 · 不间断运行

### 1.1 守护进程 `outlook_daemon.py`

| 项目 | 要求 |
|------|------|
| 运行方式 | Zo user service (`mode=process`)，系统级 supervisor 管理 |
| 重启策略 | 崩溃自动重启，**永不自行退出** |
| 注册周期 | 每 **4 小时**串行注册 5 个账号 |
| RT 获取 | 每轮注册后自动调用 `post_register_fetch_rt.py` 获取 refresh_token |
| 凭证同步 | 每天 **00:00** 自动执行 `sync_credentials.py --push` |
| 日志 | `/home/workspace/Email-Register/runtime_outlook/logs/outlook_daemon.log` |

### 1.2 前端面板 `outlook_dashboard_server.py`

| 项目 | 要求 |
|------|------|
| 运行方式 | Zo user service (`mode=http`) 或由 hub 脚本拉起 |
| 端口 | `8765`（可通过 `OUTLOOK_DASHBOARD_PORT` 环境变量覆盖） |
| 保活 | 与守护进程同生命周期，hub 脚本负责拉起 |
| 功能按钮 | 刷新代理 / 手动注册 / 获取RT / 同步推送 / 查看状态 |

### 1.3 Hub 脚本 `outlook_daemon_hub_primary.sh`

Hub 脚本是主入口，负责：
1. 拉起 Xvfb 虚拟显示 (`:98`)
2. 拉起 mihomo 代理
3. 拉起前端面板
4. 拉起 ngrok 内网穿透
5. `exec` 守护进程（永不退出）

### 1.4 禁止事项

- ❌ 不得手动 kill 守护进程
- ❌ 不得清理 `runtime_outlook/` 目录（日志、凭证、状态文件）
- ❌ 不得清理 `云端注册邮箱/` 仓库
- ❌ 不得修改守护进程的退出条件（应永久循环）

---

## 二、前端面板优化要求

### 2.1 当前状态

- 纯 HTML + JS 单页面，通过 `http.server.ThreadingHTTPServer` 提供
- 5 个手动按钮 + 实时状态轮询
- 无认证，仅限内网访问

### 2.2 优化目标

| 优先级 | 优化项 | 说明 |
|--------|--------|------|
| P0 | **实时状态面板** | 显示：当前阶段、下次注册时间、下次同步时间、已注册总数、四凭证比例 |
| P0 | **日志流** | 实时 tail 守护进程日志（WebSocket 或 SSE） |
| P1 | **凭证统计** | 按日期展示三凭证/四凭证分布，支持搜索 |
| P1 | **节点状态** | 多节点部署时显示各节点健康状态 |
| P2 | **操作历史** | 记录每次手动操作的结果和时间戳 |
| P2 | **告警通知** | 注册失败、RT 获取失败、同步失败时推送通知 |

### 2.3 技术要求

- 保持轻量，不引入前端框架（纯 HTML/JS/CSS）
- 所有 API 返回 JSON，前端通过 fetch 轮询
- 面板必须在守护进程启动时自动拉起

---

## 三、内网穿透 · 外网访问

### 3.1 当前方案

- 使用 **ngrok** 穿透 `8765` 端口
- 需要 `NGROK_AUTHTOKEN` 环境变量
- 每次重启域名会变（免费版）

### 3.2 稳定方案要求

| 方案 | 优先级 | 说明 |
|------|--------|------|
| **固定域名 ngrok** | P0 | 使用 ngrok 付费版固定域名，确保域名不变 |
| **Cloudflare Tunnel** | P1 | 备选方案，免费固定域名，无需暴露端口 |
| **Zo 反向代理** | P1 | 利用 Zo 的 `proxy_local_service` 或 user service 暴露面板 |
| **SSH 隧道** | P2 | 作为最后备选，通过 SSH 反向隧道暴露 |

### 3.3 安全要求

- 外网访问必须有认证（Bearer token 或 Basic Auth）
- 面板 API 敏感操作（注册、同步）需要二次确认
- 日志中不得暴露密码明文

---

## 四、凭证管理规范

### 4.1 三凭证 vs 四凭证

| 类型 | 格式 | 说明 |
|------|------|------|
| 三凭证 | `email----password----client_id` | 注册成功即有 |
| 四凭证 | `email----password----client_id----refresh_token` | 注册后自动获取 RT |

### 4.2 同步规则

```
三凭证 → 三凭证：跳过（不覆盖）
四凭证 → 四凭证：跳过（不覆盖）
四凭证 → 三凭证：升级（覆盖，删除三凭证文件）
```

### 4.3 存储规范

- 四凭证优先：有 RT 的账号只保留四凭证文件，不保留三凭证
- 按日期分目录：`三凭证/YYYY-MM-DD/`、`四凭证/YYYY-MM-DD/`
- `all_success.jsonl`：唯一真实来源，`has_refresh_token` 字段必须准确

---

## 五、多节点部署

### 5.1 节点清单

| 节点 | Handle | 部署方式 |
|------|--------|----------|
| zo (主) | xzxyuan | 本地 Zo user service |
| zo2 | user7fuda2 | SSH 远程部署 |
| gcore | reedemma | SSH 远程部署 |

### 5.2 部署命令

```bash
# 远程部署
bash deploy/remote_deploy.sh zo2
bash deploy/remote_deploy.sh gcore

# 收集所有节点结果
python3 deploy/collect_all_nodes.py
```

### 5.3 凭证汇总

所有节点的凭证最终汇总到 `云端注册邮箱/` 仓库，由主节点统一 `sync_credentials.py --push`。

---

## 六、监控与告警

### 6.1 健康检查

- 守护进程状态：`outlook_daemon_status.py` 输出 JSON
- 面板可达性：HTTP GET `http://localhost:8765/`
- 代理状态：检查 mihomo 进程和出口 IP

### 6.2 告警条件

| 条件 | 动作 |
|------|------|
| 守护进程崩溃 | supervisor 自动重启 + 日志记录 |
| 连续 3 轮注册失败 | 检查代理 + 检查浏览器 |
| RT 获取成功率 < 50% | 检查 token tool 版本 |
| 同步推送失败 | 检查 git 远程仓库连通性 |

---

*最后更新: 2026-06-08*
