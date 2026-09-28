# dgx 专属部署增量（相对仓库内生产模板的差异）

本目录存放 **dgx 与生产（acc-helper-vm / acc-svr）不一致、需要版本化**的部署产物。

> 模型层（主模型 / Embedding / Reranker / MinerU）的选型、性能与部署见
> [`../../docs/DGX-SPARK-模型部署方案.md`](../../docs/DGX-SPARK-模型部署方案.md)。

---

## 1. `miniapp-proxy.service` —— 端口必须与生产不同

| | 生产 acc-helper-vm | dgx |
|---|---|---|
| 监听 | `0.0.0.0:8000` | **`127.0.0.1:8020`** |
| 原因 | `:8000` 空闲（问答走云端） | dgx 本地 vLLM **常驻 `:8000`**（VL / Step-Back / 本地 LLM；主对话可切本地），端口需避让 |
| 绑定范围 | `0.0.0.0`（nginx 在另一台主机上） | `127.0.0.1`（只被本机 SSH 反向隧道访问，更安全） |

> ⚠️ 这是切换前必须处理的冲突：若 miniapp-proxy 仍配 `8000`，会因端口被 vLLM 占用而**绑不上**，
> 且反向隧道会把 `<CAMPUS_PORTAL>/accapi/` 的请求打到 LLM 而不是 miniapp-proxy。

---

## 2. `accapi-tunnel.service` —— 反向隧道端点同步改到 8020

```
-R 127.0.0.1:18000:127.0.0.1:8020     # 原为 ...:127.0.0.1:8000
```

链路：`<CAMPUS_PORTAL>/accapi/` → acc-svr nginx → `127.0.0.1:18000` →（SSH -R）→ dgx `127.0.0.1:8020` → dgx `127.0.0.1:8010`（hermes miniapp 适配器）

---

## 3. dgx 端口总表（与生产的差异部分）

| 端口 | dgx 用途 | 生产对应 |
|---|---|---|
| 8000 | **vLLM Qwen3.8-27B**（chat + VL + Step-Back） | 空闲（问答走 DeepSeek 云） |
| 8001 | qwen3-embedding（GPU FP32 包装器） | 同（CPU FP32） |
| 8002 | qwen3-reranker（GPU FP32 包装器） | 同（CPU FP32） |
| 8005 | **acc-svr MinerU 隧道**（正向隧道到 acc-svr 的旧版 `/file_parse`） | 旧 `mineru.cli.fast_api`（有 `/file_parse`） |
| 8006 | （未启用；dgx 的 VL 统一走 :8000 的 Qwen3.8-27B） | vLLM Qwen3-VL-8B-AWQ-4bit |
| 8007 | ChromaDB | 同 |
| 8008 | academic-warning-api | 同 |
| 8009 | training-plan-api（培养方案解读，004 设计） | — |
| 8010 | hermes miniapp 适配器 | 同 |
| **8020** | **miniapp-proxy** | 8000 |

> 注：dgx 统一内存紧张，**本地不起 MinerU**；PDF 通道走 `accsvr-mineru-tunnel.service`
> （`autossh -L 127.0.0.1:8005:<GPU_HOST_IP>:8005`），`MINERU_URL` 保持默认不变。

---

## 3.1 灰度路由 `/accapi/dgx-agentapi`（把小程序流量单独引到 dgx）

**链路**（`deploy/dgx/dgx-agentapi.conf`，装在 **acc-svr**）：

```
微信小程序  https://<CAMPUS_PORTAL>/accapi/dgx-agentapi/...
   │
   ▼  校园网关 <CAMPUS_GATEWAY>（只放行 /accapi/ 开头的路径，其它一律 403）
acc-svr nginx  location /accapi/dgx-agentapi { proxy_pass http://127.0.0.1:18000; }
   │  ← proxy_pass 不带 URI，原样透传请求路径
   ▼  SSH 反向隧道 127.0.0.1:18000
dgx  miniapp-proxy(127.0.0.1:8020)   归一化路径 + 剥离前缀
   ▼
dgx  hermes miniapp 适配器(127.0.0.1:8010)
```

**为什么这么设计（踩过的坑）**：

1. **校园网关只放行 `/accapi/`**：放在 `/` 或 `/dgx-agentapi` 下的 location 根本不会被转发
   （实测返回网关自己的 403，且不落 acc-svr 的任何 access log）。所以路由必须挂在 `/accapi/` 下。
2. **多层反代会把路径变成 `////api/...`**：acc-svr nginx 用 `proxy_pass http://...:8010/`（带 URI）时，
   实测上游收到的是 `////api/methods/x`，hermes 适配器匹配不上 → 405（`allow: OPTIONS`，是 aiohttp 的兜底路由）。
   → 改为 `proxy_pass http://127.0.0.1:18000;`（**不带 URI**）透传，把归一化交给 miniapp-proxy。
3. **miniapp-proxy 做路径归一化**（`agent4som/miniapp_proxy/app.py` 的 `_target_path`）：
   先折叠重复斜杠，再按 `("/accapi/dgx-agentapi", "/accapi")` 顺序剥离最长匹配前缀。
   实测 `/api/methods/unread`、`/accapi/api/...`、`/accapi/dgx-agentapi/api/...`、`////api/...`、
   `/accapi/dgx-agentapi////api/...` 五种写法全部正确归一化为 `/api/methods/unread`。

**验收（新老路由行为一致）**：

| 请求 | `/accapi/dgx-agentapi/…` | `/accapi/…`（老，acc-helper-vm） |
|---|---|---|
| `GET /api/methods/unread` | 401 `invalid or expired session token` | 401 |
| `GET /api/methods/identity` | 401 | 401 |
| `POST /api/miniapp/login` | 400（参数错误） | 400 |

**⚠️ 企业微信已弃用并移除**（2026-09）：dgx 上的 wecom 插件与配置已删除，`gateway.platform` 改为
`miniapp`，gateway 只连 miniapp。因此当前是 **miniapp 走 dgx、企微仍走 acc-helper-vm** 的分流状态
—— 两个后端的会话/记忆库（`state.db`、`miniapp.db`）会各自独立演进。
正式全量切换时把 `/accapi/` 整体切到 dgx 即可。

---

## 3.2 学业预警路由 `/accapi/dgx-warning`

链路与 §3.1 同构，只是出口换成 dgx 的学业预警 API（`:8008`）：

```
https://<CAMPUS_PORTAL>/accapi/dgx-warning/api/warning/status
   │  校园网关（只放行 /accapi/）
acc-svr nginx  location /accapi/dgx-warning {
                   rewrite ^/accapi/dgx-warning/?(.*)$ /$1 break;
                   proxy_pass http://127.0.0.1:18008;      # 不带 URI
                   client_max_body_size 20m;               # 上传五类数据文件
               }
   ▼  SSH 反向隧道 127.0.0.1:18008
dgx  academic-warning-api (0.0.0.0:8008)
```

**为什么用 `rewrite` 而不是带 URI 的 `proxy_pass`**：预警 API 的路由是 `/api/warning/*`（无前缀），
必须剥掉 `/accapi/dgx-warning`；而带 URI 的 `proxy_pass` 在本机多层级联下实测会产出 `////api/...`。
显式 `rewrite ... break` + 不带 URI 的 `proxy_pass` 是确定性写法（已在 dgx-agentapi 之外独立验证通过）。

**小程序侧**：`WARNING_API_BASE = https://<CAMPUS_PORTAL>/accapi/dgx-warning`
（前端拼出 `/api/warning/status`、`/api/warning/upload` 等；`WARNING_API_KEY` 不变）。

**验收**：

| 请求 | 结果 |
|---|---|
| 经隧道 `acc-svr:18008/api/warning/status`（带 key） | 200 |
| `https://…/accapi/dgx-warning/api/warning/status` 无 key | 401 |
| 同上带 `X-API-Key` | **200 + 真实预警数据** |

---

## 3.3 培养方案解读路由 `/accapi/dgx-plan`（004 设计）

链路与 §3.2 同构，出口换成 dgx 的培养方案解读 API（`:8009`）：

```
https://<CAMPUS_PORTAL>/accapi/dgx-plan/api/plan/status
   │  校园网关（只放行 /accapi/）
acc-svr nginx  location /accapi/dgx-plan {
                   rewrite ^/accapi/dgx-plan/?(.*)$ /$1 break;
                   proxy_pass http://127.0.0.1:18009;      # 不带 URI
                   client_max_body_size 20m;               # 上传培养方案 docx
               }
   ▼  SSH 反向隧道 127.0.0.1:18009
dgx  training-plan-api (127.0.0.1:8009)
```

**小程序侧**：`PLAN_API_BASE = https://<CAMPUS_PORTAL>/accapi/dgx-plan`，
`FEATURES.plan = true`（关闭时前端隐藏「培养方案解读 / 培养方案管理」入口）。

**必需的额外配置（易漏）**：解读页/校对页要加载 `/api/plan/prereq-image`（先修关系原图），
须在微信公众平台把 `<CAMPUS_PORTAL>` 加入 **downloadFile 合法域名**，
否则图片静默不显示（request / uploadFile / downloadFile 三类独立配置）。

**落地步骤（2026-09-23 已在 dgx 与 acc-svr 上执行并验证）**：

```bash
# 1) dgx：装并启动解读 API
#    ⚠️ dgx 的 sudo 需要密码 → 实际使用 **user 级**单元（linger=yes，注销后仍运行）。
#    生产机若有 sudo，改用 system 级 training-plan-api.service。
cp deploy/dgx/training-plan-api.user.service ~/.config/systemd/user/training-plan-api.service
systemctl --user daemon-reload
systemctl --user enable --now training-plan-api
systemctl --user status training-plan-api --no-pager

# 2) dgx：反向隧道已含 18009（accapi-tunnel.service 已更新为四条隧道）
systemctl --user restart accapi-tunnel
ssh acc-svr "ss -tln | grep 18009"     # 应看到 127.0.0.1:18009

# 3) acc-svr：装 nginx 路由（scp 会被登录横幅干扰，用 stdin 传输）
ssh acc-svr 'cat > /etc/nginx/conf.d/hermes-agents/dgx-plan.conf' < dgx-plan.conf
ssh acc-svr "nginx -t && systemctl reload nginx"

# 4) 验证（公网全链路）
curl -s -o /dev/null -w '%{http_code}\n' https://<CAMPUS_PORTAL>/accapi/dgx-plan/api/plan/majors
#   → 401
curl -s -H "X-API-Key: $TRAINING_PLAN_API_KEY" \
     https://<CAMPUS_PORTAL>/accapi/dgx-plan/api/plan/majors
#   → 200 + {"majors":[...],"plans":[...]}
```

> **实测结果（2026-09-23）**：`majors` 无 key 401 / 带 key 200；`status` 200；
> `prereq-image` 200 `image/png`；`overview` 返回 `state=done, total=148+8, 课程 67`；
> `upload` 判重正常返回「该文件已上传过」。

> 注意：`training-plan-api.service` 必须 **单 worker** —— 解析队列是进程内单消费者，
> 多 worker 会出现多个消费者竞争同一队列（队列位次/进度失真）。

---


## 4. 目录内容

| 文件 | 说明 |
|---|---|
| `miniapp-proxy.service` | dgx 版（127.0.0.1:8020） |
| `dgx-agentapi.conf` | **acc-svr** 上的灰度路由（`/accapi/dgx-agentapi` → 隧道 18000） |
| `dgx-warning.conf` | **acc-svr** 上的学业预警路由（`/accapi/dgx-warning` → 隧道 18008） |
| `dgx-plan.conf` | **acc-svr** 上的培养方案解读路由（`/accapi/dgx-plan` → 隧道 18009，004 设计） |
| `accapi-tunnel.service` | dgx 的 systemd **用户**单元，四条反向隧道（18000→8020、8010→8010、18008→8008、18009→8009） |
| `accsvr-mineru-tunnel.service` | dgx 的 systemd **用户**单元，正向隧道到 acc-svr MinerU（`127.0.0.1:8005`） |
| `training-plan-api.service` | 培养方案解读 HTTP API（`:8009`，单 worker，内嵌解析队列）——**system** 级，生产机有 sudo 时用 |
| `training-plan-api.user.service` | 同上，**user** 级 —— dgx 无 sudo，实际用的是这个（`systemctl --user`） |
| `setup-tunnel.sh` | 安装上面的隧道单元（含 linger 提示） |
| `cutover.sh` | 一键切换：预检 → 停线上 → 增量同步 → 起 dgx 服务 → 验证 / 回滚 |
| `switch-nginx.sh` | 在 **acc-svr** 上切 `/accapi/` 上游（`--to-dgx` / `--to-vm` / `--status`） |

> 注：`training-plan-api.service`（system 级）绑定 `0.0.0.0:8009`，`training-plan-api.user.service`（dgx 实际所用）绑定 `127.0.0.1:8009`——仅本机可达，经反向隧道对外。

> 依赖：`chroma-server` / `hermes-gateway@` 等由 `agent4som/infra/` 模板用 sed 生成
> （5 个 timer：backup-kb / audit-cleanup / temp-cleanup / jxtz-sync / dgx-gpu-services-oom-mitigation；
> 其中 `jxtz-sync.timer` **已停用**，教务通知同步改由 hermes cron 承担）；
> `academic-warning-api` **不在** `agent4som/infra/`，需手工新建。见
> `docs/DGX-SPARK-部署清单.md` §4 步骤 5。
