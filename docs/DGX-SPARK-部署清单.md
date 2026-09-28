# SparkPath-DGX-SPARK 迁移部署清单

> ⚠️ **历史迁移记录（2026-09-21）**：本文记录从 `acc-helper-vm` 迁移到 DGX Spark 的**过程与当时状态**，
> 其中「§1 现状」「§7 进度」是时点快照。**当前部署状态以 `deploy/dgx/README.md`、
> `deploy/gpu-services/README.md`、[`DGX-SPARK-模型部署方案.md`](DGX-SPARK-模型部署方案.md)
> 与各子仓库 README 为准**；文内命令已按此做过校正。

> 把 `acc-helper-vm`（<ACC_HELPER_VM_IP>, 用户 `<DEPLOY_USER>`）上运行的整套后端，迁移部署到
> DGX Spark（`<DGX_HOST>`, 用户 `<DGX_USER>`, 代码位于 `/home/<DGX_USER>/SparkPath-DGX-SPARK`）。
>
> 调研时间：2026-09-21。本文所有数据均来自对两台机器的实际探测，未做任何修改。

---

## 0. 一句话结论

代码结构本身**不需要改**（三个子项目原样合并后克隆即可），需要改的是 **6 类"环境耦合"**：

| # | 变更类别 | acc-helper-vm | DGX Spark |
|---|---|---|---|
| 1 | 运行用户 | `<DEPLOY_USER>` | `<DGX_USER>` |
| 2 | 代码根路径 | `/home/<DEPLOY_USER>/H-agent/agent4som` | `/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som` |
| 3 | Hermes 家目录 | `~/.hermes` → `agent4som-hermesagent` | 同左（换用户/路径） |
| 4 | GPU 推理服务 | 外部 `<GPU_HOST_IP>:8001/8002/8005/8006`（qwen3-embedding / rerank / MinerU / qwen3-vl） | **本机** `127.0.0.1`，模型名不同（bge-m3 / nemotron-rerank-1b / mineru-kit / MinerU2.5-Pro） |
| 5 | 公网入口 | `<CAMPUS_PORTAL>/accapi/` → acc-svr nginx → `<ACC_HELPER_VM_IP>:8000` | 需**反向隧道**接入 acc-svr（见第 5 节） |
| 6 | SELinux / 系统 | openEuler，SELinux Enforcing → 配置放 `/etc/*.env` | Ubuntu 24.04，无 SELinux → 配置可直接放仓库内 `.env` |

另外有两个**硬前置条件**（见 §4 步骤 0）：
- **dgx 上 `sudo` 需要密码** —— 安装 systemd 服务、改 nginx 都必须解决。
- **dgx 上没有 `uv` / `nginx` / `node`**；Python 是 3.12.3（满足要求，无需 3.11）。

---

## 1. 现状：acc-helper-vm 完整部署清单

### 1.1 主机与账号

| 项 | 值 |
|---|---|
| 公网跳板 | `acc-helper` = `acc-svr` = `<ACC_SVR_IP>`（`ProxyJump`） |
| 业务机 | `acc-helper-vm` = `<ACC_HELPER_VM_IP>`，主机名 `acc-helper`，用户 `<DEPLOY_USER>` |
| 系统 | openEuler 24.03，x86_64，SELinux **Enforcing**，firewalld 运行中 |
| 代码根 | `/home/<DEPLOY_USER>/H-agent/agent4som`、`/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent` |
| Hermes 家 | `/home/<DEPLOY_USER>/.hermes` → 软链 → `/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent` |
| Hermes 版本 | **Hermes Agent v0.18.2**，用 `pip` 安装（**不是 uv**），Python 3.11.6 |
| 两个 venv | `agent4som/venv`、`agent4som-hermesagent/hermes-agent/venv`（均 3.11.6，`chromadb==1.5.9`） |

### 1.2 常驻服务（4 个，全部 `systemctl enable`）

| # | unit | 监听 | 入口命令 | venv / cwd | 配置文件 |
|---|---|---|---|---|---|
| 1 | `chroma-server.service` | `127.0.0.1:8007` | `uvicorn chromadb.app:app` | `agent4som/venv` | `/etc/chroma-auth.conf` |
| 2 | `hermes-gateway@jwc-assistant.service` | `127.0.0.1:8010`（miniapp 适配器） | `hermes gateway run --replace` | `hermes-agent/venv` | `agent4som-hermesagent/.env` |
| 3 | `miniapp-proxy.service` | `0.0.0.0:8000` | `uvicorn app:app` | `hermes-agent/venv` | 无（读 cwd） |
| 4 | `academic-warning-api.service` | `0.0.0.0:8008` | `uvicorn academicwarning.api:app` | `agent4som/venv` | `/etc/academic-warning-api.env` |

关键依赖关系：`miniapp-proxy(8000)` → `hermes miniapp 适配器(8010)`；`hermes-gateway` 与
`academic-warning-api` 都依赖 `chroma-server(8007)`。

### 1.3 定时任务（systemd timers + 1 个 hermes cron）

| unit | 频率 | 脚本 |
|---|---|---|
| `watchdog.timer` | 每分钟 | `scripts/watchdog.sh`（心跳过期 → 告警） |
| `health-check.timer` | 每 5 分钟 | `scripts/health_check.sh`（12 项：网关/Chroma/磁盘/SQLite/GPU 四服务/jxtz 新鲜度） |
| `audit-cleanup.timer` | 每天 00:00 | `scripts/cleanup_audit.sh`（审计日志 90 天保留） |
| `nas-backup@ai-helper-test.timer` | 每天 01:00 | `scripts/nas_backup.sh`（异地 Synology `<NAS_IP>`） |
| `backup-kb.timer` | 每天 02:00 | `scripts/backup_kb.sh`（本地 KB 备份，7 天滚动） |
| `temp-cleanup.timer` | 周六 03:00 | `scripts/cleanup_temp_files.sh` |
| `jxtz-sync.timer` | 每天 04:00 | **已 disable**，改用 hermes cron 任务 `a75cd2bad6f3`（`0 4 * * *`, `jxtz_sync.py`） |

> `audit-cleanup` 等 unit 文件在 `agent4som/infra/` 内有模板；`backup-audit` 仅有脚本 `agent4som/scripts/backup_audit_db.sh`，无对应 unit 模板。

### 1.4 外部依赖（生产 `.env` 实测值）

| 依赖 | 地址 | 说明 |
|---|---|---|
| Embedding | `http://<GPU_HOST_IP>:8001/v1` `qwen3-embedding` (1024 维) | **必须与 Chroma 内向量同源** |
| Reranker | `http://<GPU_HOST_IP>:8002/v1/rerank` | |
| MinerU 解析 | `http://<GPU_HOST_IP>:8005/file_parse` | |
| VL / Step-Back | `http://<GPU_HOST_IP>:8006` `qwen3-vl` | |
| LLM 问答 | DeepSeek 云 `https://api.deepseek.com/v1` `deepseek-v4-flash` | 本地无问答 LLM |
| 异地备份 | Synology NAS `<NAS_IP>:22` (SFTP) | `data/nas_creds.json` |

### 1.5 数据目录（迁移内容）

`/home/<DEPLOY_USER>/H-agent/agent4som/data/` 共 **1.4 GB**：

| 路径 | 大小 | 是否必须迁移 |
|---|---|---|
| `data/chroma/`（向量库） | 208 MB | **必须**（知识库正文） |
| `data/backups/` | 1.2 GB | 可只挑最新一份 |
| `data/warning.db` | 9.3 MB | **必须**（学业预警） |
| `data/warning.db.bak-*` ×6 | 各 9.3 MB | 否 |
| `data/sqlite/miniapp.db` | 192 KB | **必须**（miniapp 会话/用户） |
| `data/quota.db` | 772 KB | **必须** |
| `data/jxtz_notices.jsonl` + `_ingest_results.jsonl` | 688 KB | 建议迁移（增量同步基线） |
| `data/warning_uploads/` | 3.3 MB | 建议 |
| `data/logs/` | 524 KB | 否 |

Hermes 家目录 `/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/` 运行时数据：

| 路径 | 大小 | 是否必须迁移 |
|---|---|---|
| `state.db` | 21 MB | **必须**（会话/记忆/审批状态） |
| `sessions/`、`memories/` | — | 建议 |
| `roles.json` | 183 B | **必须**（`XiongWei=owner`, `admin/jojo=admin`, `ApplicantUser=student`, miniapp `osMZS...=admin`） |
| `auth.json`、`channel_directory.json` | — | 建议 |
| `cron/` | — | **必须**（含 jxtz-sync 任务定义） |
| `config.yaml` | 3.2 KB | **必须**（**注意：仓库内的是旧版，以线上为准**） |
| `.env` | 2.3 KB | **必须**（含所有密钥，注意保密） |
| `plugins/miniapp-platform/` | — | 已在仓库内 |

### 1.6 公网入口链路（问题 4 的现状）

```
微信小程序 (AppID <MINIAPP_APPID>)
   │  API_BASE_URL = https://<CAMPUS_PORTAL>/accapi/
   ▼
acc-svr <ACC_SVR_IP> :443  nginx  (/etc/nginx/conf.d/accapi-https.conf)
   │    server_name accapi.xjtu.edu.cn <CAMPUS_PORTAL>;
   │    location /accapi/  →  proxy_pass http://<ACC_HELPER_VM_IP>:8000/
   │    location /accapi/downloads/ → alias /export/home/hermes/downloads/
   │    include /etc/nginx/conf.d/hermes-agents/*.conf   ← 其它 agent 前缀路由
   ▼
acc-helper-vm :8000  miniapp-proxy  (剥掉 /accapi 前缀)
   ▼
acc-helper-vm :8010  hermes miniapp 原生适配器
```

---

## 2. DGX Spark 环境现状

| 项 | 值 |
|---|---|
| 主机 | `<DGX_HOST>`（DGX Spark GB10），Ubuntu 24.04.5 LTS，**aarch64**，kernel 7.0.0-1019-nvidia |
| GPU | NVIDIA GB10，driver `580.178.04`（`nvidia-smi` 报 `memory.total=N/A`，属正常） |
| 访问 | `ssh dgx` = `<DGX_PUBLIC_IP>:7746`，用户 `<DGX_USER>`（已配好本机免密） |
| 资源 | 20 vCPU / 121 GiB RAM（可用 44 GiB）/ `/` 3.7 T（可用 3.4 T）/ Docker 已装 |
| 代码 | `/home/<DGX_USER>/SparkPath-DGX-SPARK`（444 MB，git HEAD `b4248a25`，与本地合并仓库一致） |
| 网络 | 内网 `<DGX_LAN_IP>/24`（**动态**），`docker0 172.17.0.1`；本机有 mihomo 代理 `127.0.0.1:7890/7891/9090` |
| 出网 | `api.deepseek.com:443` / `github.com:443` / `ilinkai.weixin.qq.com:443` / `pypi.org:443` **均可达** |
| 工具链 | ✅ python3.12.3 / pip3 24.0 / git 2.43 / sqlite3 / curl / tmux / autossh　❌ uv / node / npm / nginx / redis-cli |
| sudo | ❌ **需要密码** |
| 已有 GPU 服务（非 systemd，普通用户进程） | `:8001` vLLM **bge-m3**（embedding，dim=1024，实测通过）<br>`:8002` vLLM **nemotron-rerank-1b**（`/v1/rerank` 实测 200）<br>`:8005` **mineru-kit**<br>`:8006` vLLM **MinerU2.5-Pro-2605-1.2B**（VL/OCR） |
| 无 | `:8000` 无问答 LLM（问答继续走 DeepSeek 云） |
| venv / `~/.hermes` | 都还没有，需从零创建 |

网络连通性实测：

| 方向 | 目标 | 结果 |
|---|---|---|
| acc-svr → acc-helper-vm | `<ACC_HELPER_VM_IP>:8000 / :8008` | ✅ 可达 |
| acc-svr → dgx 内网 | `<DGX_LAN_IP>:22 / :8000` | ❌ 不可达 |
| acc-svr → dgx 公网口 | `<DGX_PUBLIC_IP>:7746` | ✅ 可达（仅 SSH） |
| acc-svr → dgx 公网 HTTP | `<DGX_PUBLIC_IP>:443 / :8000` | ❌ 不可达 |
| dgx → acc-svr | `<ACC_SVR_IP>:22 / :443` | ✅ 可达 |
| dgx → acc-helper-vm | `<ACC_HELPER_VM_IP>:22` | ❌ 不可达 |
| dgx → acc-svr SSH 登录 | `<SSH_USER>` | ❌ 无授权（需先装公钥） |

> dgx 当前公钥：`ssh-ed25519 <DGX_SSH_PUBKEY> <DGX_USER>@<DGX_HOST>`

---

## 3. 差异清单（必须逐项处理）

### 3.1 路径 / 用户替换表

| 原值 | 新值 |
|---|---|
| `<DEPLOY_USER>` | `<DGX_USER>` |
| `/home/<DEPLOY_USER>/.hermes` | `/home/<DGX_USER>/.hermes` |
| `/home/<DEPLOY_USER>/H-agent/agent4som` | `/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som` |
| `/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent` | `/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som-hermesagent` |

`config.yaml` 中要改的字段：`terminal.cwd`、`knowledge_base.chroma_path`、
`skills.external_dirs[0]`、`platforms.miniapp.extra.sqlite_path`。

### 3.2 GPU 服务地址与**模型名**（最易踩坑）

> ⚠️ 本表是迁移当时的「建议值」，**最终配置见 §7.8 与
> [`DGX-SPARK-模型部署方案.md`](DGX-SPARK-模型部署方案.md)**：Embedding/Rerank 用
> `qwen3-embedding` / `qwen3-reranker`（均 0.6B），VL / Step-Back 走本地 `qwen3.8-27b`。

| 变量 | acc-helper-vm | DGX 建议值 | 风险 |
|---|---|---|---|
| `GPU_HOST` | `<GPU_HOST_IP>` | `127.0.0.1` | — |
| `QWEN_EMBEDDING_URL` | `http://<GPU_HOST_IP>:8001/v1` | `http://127.0.0.1:8001/v1` | — |
| `QWEN_EMBEDDING_MODEL` | `qwen3-embedding` | `bge-m3` | ⚠️ **不同模型 → 与现有 Chroma 向量不兼容**，必须重建/重灌知识库（见 §4 步骤 4） |
| `BGE_RERANKER_URL` | `http://<GPU_HOST_IP>:8002/v1/rerank` | `http://127.0.0.1:8002/v1/rerank` | reranker 模型不同（nemotron-rerank-1b），接口兼容，效果需回归 |
| `STEP_BACK_MODEL_URL` | `http://<GPU_HOST_IP>:8006/v1` | `http://127.0.0.1:8006/v1` | — |
| `STEP_BACK_MODEL_NAME` | `qwen3-vl` | `/home/<DGX_USER>/models/models/MinerU2.5-Pro-2605-1.2B` | ⚠️ dgx 的 `:8006` 是 MinerU 的 VLM，不是通用 VL，需评估或另部署 Qwen3-VL |
| `VL_MODEL_URL` / `VL_MODEL_NAME` | `...:8006` / `qwen3-vl` | 同上 | 同上 |
| `MINERU_URL` | `http://<GPU_HOST_IP>:8005/file_parse` | `http://127.0.0.1:8005/file_parse` | 需确认 mineru-kit 的 `/file_parse` 路径与协议一致 |
| `NO_PROXY` | `192.168.0.0/16,127.0.0.1,localhost` | `127.0.0.1,localhost` | dgx 本机有 mihomo 代理，务必把 `127.0.0.1` 排除 |

### 3.3 其它

| 项 | 处理方式 |
|---|---|
| SELinux | dgx 无 SELinux，`/etc/*.env` 可以不放，直接用仓库内 `agent4som/.env` 与 `agent4som-hermesagent/.env`；若坚持放 `/etc` 也行 |
| Python | 3.12.3 满足 `hermes>=3.11,<3.14` 与 `agent4som>=3.11`，**不需要装 3.11** |
| `uv` | 未安装；可用 `curl -LsSf https://astral.sh/uv/install.sh | sh`（免 sudo，装到 `~/.local/bin`）来创建 venv，或 `python3 -m venv`（需 `python3.12-venv` 包，要 sudo） |
| `hooks` 软链 | `~/.hermes/hooks` → `agent4som/gateway/hooks`（`.gitignore` 忽略，需手工建） |
| nginx | dgx 未装；**不建议在 dgx 装**，见第 5 节 |
| node/npm | 仅前端 `npm run switch` 用（在 Windows 上跑），后端不需要 |

---

## 4. DGX 部署步骤

### 步骤 0 — 前置条件（阻塞项）

1. **解决 sudo**：dgx 上 `sudo` 需要密码。安装 systemd 服务、写 `/etc`、`loginctl enable-linger` 都要 root。
   请提供密码或先配置 `<DGX_USER>` 的免密 sudo。
2. 确认 dgx 的 `:8001/:8002/:8005/:8006` 服务**已开机自启**（当前是普通用户进程，重启会丢，
   应做成 systemd 服务或开机脚本）。
3. 决定知识库策略（`bge-m3` 重建 vs. 在 dgx 部署 `qwen3-embedding` 复用旧向量）。
   **已定：部署 `qwen3-embedding` 复用旧向量，见 §7.8。**

### 步骤 1 — 代码

```bash
# 已在 /home/<DGX_USER>/SparkPath-DGX-SPARK，确认干净并更新
cd /home/<DGX_USER>/SparkPath-DGX-SPARK
git status && git log --oneline -1
```

### 步骤 2 — 两个 venv

```bash
# 2.1 agent4som venv（Python 3.12 即可）
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cd /home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som
uv venv --python 3.12 venv
uv pip install --python venv/bin/python -r requirements.txt

# 2.2 hermes venv（Hermes v0.18.2）
#   注意：上游 Hermes 框架已移出本仓库 git 跟踪（.gitignore），克隆后需单独安装。
#   本仓库对框架的自研改动在 agent4som/hermes_overlay/，装好后执行：
#     bash agent4som/scripts/deploy_tools.sh
cd /home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som-hermesagent
git clone https://github.com/NousResearch/hermes-agent hermes-agent   # 对应版本 0.18.2（tag v2026.7.7.2）；若磁盘上已存在则可跳过
cd hermes-agent
uv venv --python 3.12 venv
uv pip install --python venv/bin/python -e ".[dev]"
venv/bin/hermes --version     # 期望 0.18.2
```

> ⚠️ aarch64 上重点验证 `chromadb==1.5.9`、`llama-index-core`、`pdfminer.six` 是否有 wheel；
> 若失败改用 `agent4som` 的 pyproject（`uv pip install -e .`）或指定可用的 chromadb 版本。

### 步骤 3 — 配置

```bash
cd /home/<DGX_USER>/SparkPath-DGX-SPARK

# 3.1 Hermes 家目录软链 + hooks 软链
ln -sfn "$PWD/agent4som-hermesagent" "$HOME/.hermes"
ln -sfn "$PWD/agent4som/gateway/hooks" "$HOME/.hermes/hooks"

# 3.2 agent4som/.env  —— 从 .env.example 生成，按 §3.2 替换 GPU_HOST/模型名
cp agent4som/.env.example agent4som/.env
#   GPU_HOST=127.0.0.1
#   QWEN_EMBEDDING_URL=http://127.0.0.1:8001/v1
#   QWEN_EMBEDDING_MODEL=bge-m3
#   BGE_RERANKER_URL=http://127.0.0.1:8002/v1/rerank
#   STEP_BACK_MODEL_URL=http://127.0.0.1:8006/v1
#   STEP_BACK_MODEL_NAME=<dgx 上 :8006 的模型 id>
#   VL_MODEL_URL=http://127.0.0.1:8006 ; VL_MODEL_NAME=<同上>
#   MINERU_URL=http://127.0.0.1:8005/file_parse
#   NO_PROXY=127.0.0.1,localhost
#   CHROMA_* / *_API_KEY / WARNING_API_KEY  ← 从 acc-helper-vm 取（见 3.3）
#   建议 WARNING_API_KEY 重新生成一串随机值

# 3.3 agent4som-hermesagent/.env  —— 从线上直接拷贝（含模型/小程序密钥）
scp acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/.env \
    agent4som-hermesagent/.env
#   然后修改：AGENT4SOM_REPO / AGENT4SOM_HOME / QWEN_*/STEP_BACK_*/VL_*/MINERU_URL
#   保留：DEEPSEEK_API_KEY / QWEN_API_KEY /
#         WECHAT_MINIAPP_APPID / WECHAT_MINIAPP_APPSECRET / MINIAPP_SESSION_SECRET /
#         CHROMA_SERVER_AUTHN_CREDENTIALS / CHROMA_AUTH_TOKEN

# 3.4 config.yaml —— 用线上版本（仓库内是旧版）
scp acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/config.yaml \
    agent4som-hermesagent/config.yaml
#   逐项替换 §3.1 的 4 个路径字段
```

### 步骤 4 — 数据迁移

```bash
# 4.1 向量库：取决于 §3.2 的选择
#   (a) 若在 dgx 部署 qwen3-embedding（与线上同模型）→ 直接拷 chroma 目录
#   (b) 若用 dgx 现成的 bge-m3 → 拷 chroma 无效，必须在 dgx 重灌：
#       迁移 data/backups 或原始语料后执行 batch_ingest_*
rsync -avz acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som/data/chroma/ \
      agent4som/data/chroma/

# 4.2 业务库
mkdir -p agent4som/data/sqlite agent4som/data/logs
rsync -avz acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som/data/warning.db \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som/data/quota.db \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som/data/jxtz_notices.jsonl \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som/data/jxtz_ingest_results.jsonl \
      agent4som/data/
rsync -avz acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som/data/sqlite/miniapp.db \
      agent4som/data/sqlite/

# 4.3 Hermes 运行时
rsync -avz acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/state.db \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/roles.json \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/auth.json \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/channel_directory.json \
      agent4som-hermesagent/
rsync -avz acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/cron/ \
              acc-helper-vm:/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/sessions/ \
      agent4som-hermesagent/
```

> 注意：`rsync` 直连不通（dgx 无法直连 <ACC_HELPER_VM_IP>）。走本机中转，或在 dgx 上用
> `ssh -J <本机可达的 acc-svr 账号>` 的方式；也可先 `scp` 到 Windows 再推到 dgx。

### 步骤 5 — systemd 常驻服务

把 `agent4som/infra/*.service` 与 `agent4som/miniapp_proxy/miniapp-proxy.service`
复制到 `/etc/systemd/system/`，再做路径替换（`<DEPLOY_USER>`→`<DGX_USER>`、`H-agent/agent4som`→`SparkPath-DGX-SPARK/agent4som`）：

> 注：本仓库内模板**已包含 dgx 值**（`<DGX_USER>` + `SparkPath-DGX-SPARK`）。下述 sed 主要用于从源机（acc-helper-vm）拉取**旧版**模板的迁移场景；直接从仓库复制时，只需把 `<DGX_USER>` 替换为本机用户名即可。

```bash
cd /home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som
# 仓库内实际存在的单元（health-check / watchdog / nas-backup 不在本仓库，见下）
for f in infra/chroma-server.service infra/hermes-gateway@.service \
         infra/backup-kb.service infra/temp-cleanup.service \
         infra/audit-cleanup.service \
         miniapp_proxy/miniapp-proxy.service; do
  sed -e 's#/home/<DEPLOY_USER>/H-agent#/home/<DGX_USER>/SparkPath-DGX-SPARK#g' \
      -e 's#/home/<DEPLOY_USER>#/home/<DGX_USER>#g' \
      -e 's#^User=<DEPLOY_USER>#User=<DGX_USER>#' \
      -e 's#^EnvironmentFile=.*chroma-auth.conf#EnvironmentFile=/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som/.env#' \
      -e 's#^EnvironmentFile=.*academic-warning-api.env#EnvironmentFile=/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som/.env#' \
      "$f" | sudo tee "/etc/systemd/system/$(basename $f)" >/dev/null
done
# academic-warning-api.service 需手工新建（线上不在仓库内），照 1.2 表格改写路径与 EnvironmentFile
sudo systemctl daemon-reload
sudo systemctl enable --now chroma-server
sudo systemctl enable --now hermes-gateway@jwc-assistant
sudo systemctl enable --now miniapp-proxy
sudo systemctl enable --now academic-warning-api
```

> ⚠️ `miniapp-proxy` 与 `hermes-gateway` 的 `ExecStart` 用的是 hermes venv 里的
> `uvicorn`/`hermes`，替换后必须是 `/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som-hermesagent/hermes-agent/venv/bin/...`。

### 步骤 6 — 定时任务

```bash
cd /home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som
for f in infra/backup-kb.timer infra/temp-cleanup.timer infra/audit-cleanup.timer ; do
  sudo cp "$f" /etc/systemd/system/
done
sudo systemctl daemon-reload
sudo systemctl enable --now backup-kb.timer temp-cleanup.timer audit-cleanup.timer
# 不要启用 jxtz-sync.timer（已迁移到 hermes cron 任务 a75cd2bad6f3）
# 注意：health-check.timer / watchdog.timer / nas-backup@.timer 不在本仓库，
#       其脚本与单元位于部署机 ~/work/systemd-app/，按需单独安装。
```

### 步骤 7 — 小程序侧

- 若沿用 `https://<CAMPUS_PORTAL>/accapi/`（推荐）：**前端一行不用改、微信后台合法域名不用改、不用重新发布**。
- 若改用新域名：需 ① 在微信公众平台加 `request` 合法域名；② 改
  `miniprogram-framework-frontend/config/instances/*.js` 的 `API_BASE_URL`；
  ③ 重新 `npm run release` 上传并发布体验版/正式版。

### 步骤 8 — 验证

```bash
curl -s http://127.0.0.1:8007/api/v2/heartbeat                 # Chroma
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/  # miniapp-proxy
curl -s http://127.0.0.1:8010/                                   # hermes miniapp 适配器
curl -s -H "X-API-Key: $WARNING_API_KEY" http://127.0.0.1:8008/api/warning/status
bash agent4som/scripts/smoke_test.sh                             # 仓库自带 16 项冒烟（在仓库根执行）
systemctl status hermes-gateway@jwc-assistant
cat ~/.hermes/gateway_state.json   # 期望 gateway_state=running, miniapp=connected
hermes cron list                   # 期望 a75cd2bad6f3 active
```

最后从微信开发者工具跑通小程序 → 对话 → 知识库问答 → 学业预警全链路。

### 步骤 9 — 回滚

改回 acc-svr nginx 的 `proxy_pass` 指向 `<ACC_HELPER_VM_IP>:8000`，其余不动即可（见 §5）。

---

## 5. 公网入口调整方案（问题 4 的答案）

**结论：小程序域名保持 `<CAMPUS_PORTAL>/accapi/` 不变，在 acc-svr 上把上游从
acc-helper-vm 换成 dgx。因为两台机器私网不通、dgx 也没有 HTTPS 公网口，
唯一低风险做法是 dgx 主动向 acc-svr 建 SSH 反向隧道。**

### 方案 A（推荐）：SSH 反向隧道 + acc-svr nginx 换上游

```
微信小程序 ─https─► <CAMPUS_PORTAL>:443 (acc-svr nginx)
                        │ location /accapi/ → proxy_pass http://127.0.0.1:18000/
                        ▼
                    acc-svr 127.0.0.1:18000  ◄── SSH -R 反向隧道 ──  dgx:22
                                                                    dgx 127.0.0.1:8000 miniapp-proxy
```

实施步骤：

1. **授权隧道**（只需一次；本机可同时 ssh 两台机器，可直接操作）
   ```bash
   # 把 dgx 公钥追加到 acc-svr 的 root authorized_keys
   ssh dgx "cat ~/.ssh/id_ed25519.pub" | ssh acc-helper "cat >> /root/.ssh/authorized_keys"
   ```
   acc-svr 的 sshd 已满足条件：`AllowTcpForwarding yes`、`GatewayPorts no`
   （只能绑 `127.0.0.1`，正好够用）。
2. **dgx 侧常驻隧道**（dgx 已装 `autossh`）
   ```ini
   # ~/.config/systemd/user/accapi-tunnel.service
   [Unit]
   Description=Reverse SSH tunnel to acc-svr for /accapi
   After=network-online.target

   [Service]
   Type=simple
   ExecStart=/usr/bin/autossh -M 0 -N \
     -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
     -o ExitOnForwardFailure=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
     -R 127.0.0.1:18000:127.0.0.1:8000 root@<ACC_SVR_IP>
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=default.target
   ```
   ```bash
   systemctl --user daemon-reload && systemctl --user enable --now accapi-tunnel
   sudo loginctl enable-linger <DGX_USER>   # 开机自启（需要 sudo）
   ```
3. **acc-svr nginx 改上游**（`/etc/nginx/conf.d/accapi-https.conf`
   与 `/etc/nginx/default.d/accapi.conf` 两处都要改）
   ```nginx
   location /accapi/ {
       proxy_pass http://127.0.0.1:18000/;   # 原 http://<ACC_HELPER_VM_IP>:8000/
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_connect_timeout 60s;
       proxy_read_timeout 300s;              # LLM 长回答；可按需加到 600s
   }
   ```
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```
4. **`/accapi/downloads/`**：现在 alias 到 acc-svr 本地的
   `/export/home/hermes/downloads/`。若 dgx 侧也生成下载文件，需要 ① 把该 alias 改指到
   隧道暴露的路径，或 ② 把 dgx 的下载目录顺向同步到 acc-svr。**迁移前需确认小程序/工具是否用到该路径。**
5. **灰度 / 回滚**：nginx 里保留两个 upstream，用 `location /accapi-spark/` 先给 dgx 灰度
   （`API_BASE_URL` 改成 `.../accapi-spark/` 只需重新发一版前端，域名白名单不变），
   验证通过后再把 `/accapi/` 切过去。回滚 = 把 `proxy_pass` 改回 `<ACC_HELPER_VM_IP>:8000/`。

### 方案 B（不推荐）：给 dgx 直接开公网 HTTPS

需要 ① 在 `<DGX_PUBLIC_IP>` 那台 NAT 主机上加端口转发（当前只有 7746→SSH，443/8000 不通）；
② 申请证书；③ 域名 ICP 备案；④ 微信后台加合法域名；⑤ 改前端 `API_BASE_URL` 并重新发布。
链长、风险多，仅在无法使用 acc-svr 时才考虑。

### 方案 C（可选）：小程序 API 直接指向 dgx 内网（仅开发/内测）

通过 SSH 隧道把 dgx:8000 映射到本机，配合开发者工具关闭合法域名校验。
只能用于开发联调，不能上生产。

---

## 6. 风险与待确认项

| # | 事项 | 影响 | 建议 |
|---|---|---|---|
| 1 | dgx `sudo` 需要密码 | 无法装 systemd/nginx/linger，**一级阻塞** | 先解决 |
| 2 | dgx `:8001/8002/8005/8006` 非 systemd 托管 | 重启即丢，整套后端不可用 | 改造成 systemd 或开机脚本 |
| 3 | embedding 模型不一致（qwen3-embedding ↔ bge-m3） | 复用旧 Chroma 向量会**检索结果错乱** | 二选一：dgx 部署 qwen3-embedding；或 dgx 重灌知识库 |
| 4 | VL / Step-Back 模型从 `qwen3-vl` 变 `MinerU2.5-Pro` | 图片理解、查询改写质量变化 | 做一轮 RAG 回归（`scripts/eval_ragas.py`） |
| 5 | aarch64 wheel 可用性（chromadb/llama-index） | 可能装不上 | 步骤 2 先试装并记录版本 |
| 6 | `data/backups` 1.2 GB、`warning.db.bak-*` ×6 | 迁移耗时/占空间 | 只迁必要文件 |
| 7 | acc-svr nginx 上有其它 agent（`lizhen-adm` 等）共用 443 | 误改会影响他人 | 只改 `/accapi/` 两处，改前 `nginx -t`、备份配置 |
| 8 | 反向隧道断开会直接导致线上 502 | 可用性 | autossh + `Restart=always` + 健康检查 |
| 9 | 同一入口被两台机器同时服务 | 消息重复/抢占 | 切换前先停 acc-helper-vm 的 `hermes-gateway@jwc-assistant` |
| 10 | 仓库内 `config.yaml` 落后于线上（线上 `_config_version: 22`） | 部署出错配置 | 以 §4 步骤 3.4 从线上拉取为准 |
| 11 | 密钥文件（`.env`、`auth.json`、`nas_creds.json`） | 泄露风险 | 传输走加密通道，不入库（`.gitignore` 已覆盖） |

---

## 附录 A：需要从 acc-helper-vm 取出的配置值清单

`/home/<DEPLOY_USER>/H-agent/agent4som/.env` 与 `agent4som-hermesagent/.env` 中需要保留的键：

```
# 通用/GPU
GPU_HOST  QWEN_EMBEDDING_URL  QWEN_EMBEDDING_MODEL  QWEN_API_KEY  EMBEDDING_TIMEOUT  NO_PROXY
CHROMA_HOST  CHROMA_PORT  CHROMA_DB_PATH  CHROMA_SERVER_AUTHN_PROVIDER
CHROMA_SERVER_AUTHN_CREDENTIALS  CHROMA_AUTH_TOKEN
BGE_RERANKER_URL  STEP_BACK_ENABLED  STEP_BACK_MODEL_URL  STEP_BACK_MODEL_NAME  STEP_BACK_API_KEY
MINERU_URL  MINERU_API_KEY  MINERU_POLL_INTERVAL  MINERU_POLL_TIMEOUT  MINERU_RETRIES  MINERU_RETRY_MAX_BACKOFF
VL_MODEL_URL  VL_MODEL_NAME  VL_MAX_TOKENS  KB_USE_LEGACY_PARSER
WARNING_API_KEY

# 小程序
GATEWAY_ALLOW_ALL_USERS
DEEPSEEK_API_KEY  QWEN_API_KEY
WECHAT_MINIAPP_APPID  WECHAT_MINIAPP_APPSECRET  MINIAPP_SESSION_SECRET
MINIAPP_ALLOW_ALL_USERS  MINIAPP_ALLOWED_ORIGINS  MINIAPP_HOST  MINIAPP_PORT  PHONE_VERIFY_MODE
AGENT4SOM_REPO  AGENT4SOM_HOME  CHROMA_TELEMETRY_ENABLED
```

## 附录 B：端口分配总表（dgx 部署后）

| 端口 | 服务 | 绑定 |
|---|---|---|
| 8000 | vLLM `qwen3.8-27b`（chat / VL / Step-Back） | 0.0.0.0 |
| 8001 | `qwen3-embedding`（1024 维） | 127.0.0.1 |
| 8002 | `qwen3-reranker` | 127.0.0.1 |
| 8005 | MinerU（经 accsvr-mineru-tunnel 转发到 acc-svr） | 127.0.0.1 |
| 8006 | 未启用（VL 统一走 `:8000`） | — |
| 8007 | ChromaDB | 127.0.0.1 |
| 8008 | academic-warning-api | 0.0.0.0 |
| 8009 | training-plan-api | 127.0.0.1 |
| 8010 | hermes miniapp 适配器 | 127.0.0.1 |
| 8020 | miniapp-proxy（dgx 版；生产为 8000） | 127.0.0.1 |
| 18000 / 18008 / 18009 | （acc-svr 侧）反向隧道入口 | 127.0.0.1（在 acc-svr 上） |

---

## 7. 执行进度与已定决策（2026-09-21 16:10 更新）

### 7.1 已定决策

| 议题 | 结论 |
|---|---|
| Embedding / Rerank | **替换为与线上一模一样的模型**：dgx `:8001` bge-m3 → `qwen3-embedding`，`:8002` nemotron-rerank-1b → `qwen3-reranker`。故可直接沿用已迁移的向量库，**无需重灌** |
| 问答主模型 | **切到本地 `qwen3.8-27b`（:8000）**，`config.yaml` 为 `provider: local`（默认全本地，问答内容不出校）。框架支持按需接入云端 OpenAI 兼容模型（可选）；见模型部署方案 §9 |
| VL / Step-Back | 线上走 `:8006 qwen3-vl`；dgx 上 `:8006` 是 MinerU 内部 VLM，改为走本地多模态 `:8000 qwen3.8-27b` |
| 数据迁移 | **全量** |
| dgx sudo | 已获密码，systemd/nginx 操作已就绪 |
| 模型服务 | **由模型组负责**，应用侧不碰；待其就绪后直接切换 |

### 7.2 已完成（实测通过）

| # | 项 | 证据 |
|---|---|---|
| 1 | dgx → acc-svr → acc-helper-vm SSH 通路 | dgx 直连 acc-svr OK；ProxyJump 到 acc-helper-vm `LOGIN_OK`；三台均有 rsync |
| 2 | 反向隧道（切换前置） | `~/.config/systemd/user/accapi-tunnel.service` 运行中；acc-svr `127.0.0.1:18000` 已监听；`loginctl` Linger=yes |
| 3 | 两个 venv | agent4som (py3.12.3, chromadb 1.5.9) / hermes (v0.18.2, chromadb 1.5.9)；**已与线上 freeze 逐包比对并补齐全部缺失依赖**（含 chromadb 的 dev extra `opentelemetry-instrumentation-fastapi`） |
| 4 | 配置 | `~/.hermes` → `agent4som-hermesagent` 软链正确；`~/.hermes/hooks` 软链；`.env` 已改为本机 GPU；`config.yaml` 主模型 qwen3.8-27b（YAML 校验 + `hermes config show` 通过） |
| 5 | 数据全量迁移 | chroma 208M / warning.db / quota.db / audit.db / miniapp.db / state.db(21M) / cron / sessions / memories / platforms / skills(30) / roles.json / auth.json / nas_creds.json；**5 个 SQLite `integrity_check` 全 ok**；**chroma embeddings=8373 与线上一致** |
| 6 | systemd 单元 | 已生成并安装 12 个 unit + 7 个 timer 到 `/etc/systemd/system/`（源文件在 `~/work/systemd-app/`；含从源机带来、**装了未启用**的 health-check/watchdog/nas-backup 等。dgx **常驻启用**为 9 个服务 unit + 5 个 timer，见 `模型部署方案` §3.2） |
| 7 | 已启动服务 | `chroma-server`（heartbeat 200，集合 `raw_nodes` count=8373）、`academic-warning-api`（返回真实预警数据） |
| 8 | 切换/回滚脚本 | `~/work/cutover.sh`（--check / 切换 / --rollback）、`~/work/switch-nginx.sh`（--to-dgx / --to-vm / --status） |

### 7.3 有意"装了但没启用"（防误伤线上）

| 项 | 原因 |
|---|---|
| `hermes-gateway@jwc-assistant` | miniapp 适配器，需确保只由一台机器对外服务 |
| `miniapp-proxy` | 依赖 gateway |
| 7 个 timer（health-check / watchdog / nas-backup 等，来自源机、**装了未启用**） | gateway 未起时 health-check 会持续告警 |

以上由 `cutover.sh` 在切换时统一启用。

### 7.4 阻塞项 / 待办

| 优先级 | 事项 | 责任方 |
|---|---|---|
| ~~P0~~ | ~~dgx `:8001` 换 `qwen3-embedding`、`:8002` 换 `qwen3-reranker`~~ **✅ 已完成（2026-09-21 17:20，见 §7.8）** | —— |
| **P1** | `:8005` MinerU 接口不兼容（v4 `/v1/parse/jobs` vs 应用侧 `/file_parse`） | 模型组决策（方案 A 补兼容端点 / 方案 B 应用侧改造）。**不阻塞切换**（有 pdfminer + VL 降级链） |
| P1 | **VL 模型不一致**：生产 VL 实测为 `Qwen3-VL-8B-Instruct-AWQ-4bit`，dgx 现配 `qwen3.8-27b`。学术预警的成绩单学号 OCR（生产实测 98.6%→100%）是在 Qwen3-VL-8B 上验证的 | 需对 dgx 的 VL 做一轮成绩单 OCR 回归；若精度不足则在 dgx 另起 Qwen3-VL-8B |
| P2 | `:8000`(Qwen3.8-27B) / `:8005` / `:8006` 仍为 nohup 进程（非 systemd） | 模型组 |
| P2 | 切换后把 acc-helper-vm 的 timer 全部 disable，避免 NAS 备份/告警重复 | 切换时由 `cutover.sh` 处理 |

### 7.5 切换顺序（三步）

```bash
# ① 预检（模型就绪再继续）
ssh dgx 'bash ~/work/cutover.sh --check'

# ② 切换：停线上 → 最后一次增量同步 → 起 dgx 服务
ssh dgx 'bash ~/work/cutover.sh'

# ③ 切 nginx 上游（在 acc-svr 上，留观察窗口）
ssh acc-helper 'bash /root/switch-nginx.sh --to-dgx'   # 需先把 switch-nginx.sh 放到 acc-svr
```

回滚：`ssh dgx 'bash ~/work/cutover.sh --rollback'` + `switch-nginx.sh --to-vm`

### 7.6 一处已知隐患（已修复，记录备查）

`home/.hermes` 首次被 hermes CLI 自动创建成**真目录**，导致后续 `ln -sfn` 只在其内部建立子链接；
表现为 `hermes` 读不到配置（`hermes-agent/venv/bin/hermes` 路径解析失败）。已删除该目录并重建为
指向 `SparkPath-DGX-SPARK/agent4som-hermesagent` 的软链，`hermes config show` 现已正确解析。

### 7.7 审计发现的两处公共代码硬编码（已修正）

对 dgx 全部模型调用出口做了穷举审计，发现两处把"某一台机器的地址/模型"写死在代码里，已改为
按环境变量推导，使同一份代码在 acc-helper-vm 与 dgx 上都正确：

| 文件 | 原状 | 修正 |
|---|---|---|
| `agent4som-hermesagent/hermes-agent/tools/query_kb.py:17-25` | 兜底默认值写死 `http://<GPU_HOST_IP>:8001/v1`、`:8002/v1/rerank`、`:8000/v1` | 改为由 `GPU_HOST` 推导（未设置回落 `127.0.0.1`）；`os.getenv("X", d)` → `os.getenv("X") or d`，避免"设为空串时兜底失效" |
| `agent4som/knowledge_base/retrieval/bm25_search.py:241`（`_dat_alpha`） | URL 取 `STEP_BACK_MODEL_URL`（env），但 **模型名写死 `deepseek-v4-flash`**；key 只认 `DEEPSEEK_API_KEY`/`QWEN_API_KEY` | URL 与模型名均取 `STEP_BACK_MODEL_URL`/`STEP_BACK_MODEL_NAME`，key 优先 `STEP_BACK_API_KEY`；两者未配置时**自动关闭 DAT**（不再回落到云端写死模型） |

验证（在 dgx 实测）：

```
GPU_HOST=127.0.0.1      → embed/rerank/stepback 全部 http://127.0.0.1:...
GPU_HOST=<GPU_HOST_IP>  → embed/rerank/stepback 全部 http://<GPU_HOST_IP>:...
GPU_HOST 未设置          → 回落 http://127.0.0.1:...
```
两个文件 `py_compile` 通过，`_dat_alpha` 已无 deepseek 残留。

配套：已向两台的 hermes `.env` 补充 `GPU_HOST`（dgx=`127.0.0.1`，acc-helper-vm=`<GPU_HOST_IP>`）。
`query_kb.py` 的**线上副本需下次同步代码 + 重启 gateway 才生效**（该改动在 acc-helper-vm 上行为等价，风险为零）。

其余审计结论：dgx 侧 8 条模型链路（主问答/辅助模型/VL/Step-Back/Embedding/Rerank/MinerU/Chroma）
的 URL **全部指向 `127.0.0.1`**，无任何链路指向云端 LLM 或 acc-helper-vm 的 GPU。
`DAT_ENABLED` 默认 `false` 且未设置，DAT 路径不活跃。

### 7.8 Embedding / Rerank 替换完成（2026-09-21 17:20）✅

**背景**：dgx 原为 bge-m3 + nemotron-rerank-1b，与线上不一致，会导致已迁移的 8373 条向量不可用。
线上这两个服务在 **GPU 主机 acc-svr（<GPU_HOST_IP>）** 上，是**未纳入任何仓库**的自定义包装器，
因此先把它们取回仓库：`deploy/gpu-services/`（含 README、wrapper、systemd 单元、参考启动脚本）。

**线上真实实现（读代码确认，非猜测）**：

| 端口 | 实现 | 模型 |
|---|---|---|
| `:8001` | `embedding_server.py`（transformers + FastAPI，FP32/**CPU**） | `Qwen3-Embedding-0.6B` |
| `:8002` | `reranker_server.py`（同上） | `Qwen3-Reranker-0.6B` |
| `:8006` | `start-vllm.sh` → vLLM | `Qwen3-VL-8B-Instruct-AWQ-4bit`（服务名 `qwen3-vl`） |
| `:8005` | `start-mineru.sh` → `mineru.cli.fast_api`（**旧 API，有 `/file_parse`**） | MinerU |

**关键坑（此前探针差 0.88–0.91 的原因）**：`embedding_server.py` 在**服务端统一加 instruction 前缀**
（查询与文档都加）：
```python
task_desc = "Given a web search query, retrieve relevant passages that answer the query"
texts = [f"Instruct: {task_desc}\nQuery: {t}" for t in texts]   # 再 last-token pooling + L2 归一化
```
少了这个前缀，即使权重完全相同，向量余弦也只有 ~0.88。

**实施**：
1. 从 ModelScope 下载 `Qwen3-Embedding-0.6B` / `Qwen3-Reranker-0.6B`（hf-mirror 的 API 被 403，Xet 也不可用 → 改用 ModelScope API）。权重 sha256 与生产 **逐字节一致**（`0437e45c…`）。
2. 仓库版 wrapper 仅做**最小改动**：新增 `HOST` 环境变量（默认仍 `<GPU_HOST_IP>`，不改生产语义）、`/health` 的 device 改为真实值。
3. dgx 上改用 **GPU + FP32** 运行（生产是 CPU），权重指向本地，监听 `127.0.0.1`，做成 systemd：
   `dgx-embedding.service` / `dgx-reranker.service`（已 enable）。
4. `pip install accelerate`（wrapper 用了 `device_map="auto"`）到 `~/venvs/vllm`。

**验收（与生产逐项比对）**：

| 项 | 结果 |
|---|---|
| Embedding dim / norm | 1024 / 1.000000 ✅ |
| Embedding 余弦（5 条文本） | **1.0000000000**（max\|Δ\| ≈ 2e-7，纯 fp32 噪声）✅ |
| Rerank 排序 | prod `[0,2,3,1]` = dgx `[0,2,3,1]` ✅ |
| Rerank 分数最大差 | 1.75e-05 ✅ |

→ **现有 8373 条向量可 100% 复用，无需重灌知识库。**

**注意**：`dgx-*.service` **不能加 `PrivateDevices=yes`**（会屏蔽 `/dev/nvidia*` 导致 CUDA 初始化失败）。

**回滚**：模型组原命令已存于 `/home/<DGX_USER>/work/modelteam-services-backup.txt`；
`systemctl disable --now dgx-embedding dgx-reranker` 后照该文件重新 nohup 启动即可
（bge-m3 / nemotron 权重仍保留在 `~/models/`）。
