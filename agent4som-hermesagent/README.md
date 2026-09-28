# agent4som-hermesagent —— Hermes 运行时家目录

> 本目录是 Hermes Agent 的 **`HERMES_HOME`（运行家目录）**，不是普通代码目录。
> 项目约定：`~/.hermes` 是指向本目录的**软链接**。

```
ln -sfn "$PWD/agent4som-hermesagent" "$HOME/.hermes"
```

它承载「本科新生学业规划智能助手」在运行时所需的一切：网关配置、Agent 身份、角色映射、
微信小程序平台适配器、知识库启动钩子，以及单独安装的上游 Hermes Agent 框架。

---

## 目录

1. [目录结构](#1-目录结构)
2. [config.yaml](#2-configyaml)
3. [Agent 身份与角色](#3-agent-身份与角色)
4. [平台适配器（miniapp）](#4-平台适配器miniapp)
5. [启动钩子（kb_init）](#5-启动钩子kb_init)
6. [hermes-agent 上游框架](#6-hermes-agent-上游框架)
7. [技能目录：skills 与 shared_skills](#7-技能目录skills-与-shared_skills)
8. [部署与运维](#8-部署与运维)
9. [安全约定](#9-安全约定)

---

## 1. 目录结构

```
agent4som-hermesagent/                # == $HERMES_HOME == ~/.hermes
├── config.yaml                      # 网关运行配置（可入库）
├── SOUL.md                          # Agent 身份 / 系统提示词（可入库）
├── plugins/
│   └── miniapp-platform/            # 微信小程序平台适配器（自研插件，可入库）
│       ├── plugin.yaml
│       └── adapter.py
├── hooks/
│   └── kb_init/                     # 网关启动钩子：初始化知识库单例
│       ├── HOOK.yaml
│       └── handler.py
├── scripts/
│   └── jxtz_sync.py                 # Hermes cron 包装：切 venv 跑 agent4som 的同步脚本
│
├── hermes-agent/                    # 上游 Hermes Agent 框架（v0.18.2，单独安装、gitignored，约 940MB）
│   ├── venv/                        # 框架自带 venv（Python 3.12，editable 安装）
│   └── ...
│
├── skills/                          # Hermes 托管技能库（bundled / hub，运行时产物）
├── cron/                            # Hermes 定时任务（jobs.json 等，运行时产物）
├── sessions/                        # 会话存储
├── memories/                        # 记忆插件存储
├── logs/                            # 日志：agent.log / errors.log / gateway.log
├── cache/ image_cache/ audio_cache/ # 运行期缓存
├── sandboxes/                       # 沙箱运行时
├── bin/                             # 本地二进制（uv / uvx / tirith；按需安装，不入库）
│
├── state.db                         # Hermes SessionDB（会话 / 消息 / 记忆，运行时）
├── kanban.db                        # 多 Agent 看板（运行时）
├── roles.json                       # 角色映射（运行时，gitignored）
├── gateway_state.json               # 网关运行状态（运行时）
├── auth.json / channel_directory.json / models_dev_cache.json
├── .env                             # 密钥与环境变量（gitignored，切勿提交）
└── .gitignore
```

> `config.yaml`、`SOUL.md`、`plugins/`、`scripts/` 属于**可入库的项目配置与代码**；
> `state.db*`、`roles.json`、`gateway_state.json`、`.env`、`logs/`、`sessions/`、`cron/`、
> `skills/`、缓存等均为**运行时产物**，已在 `.gitignore` 中排除。

---

## 2. config.yaml

网关的核心配置文件，主要配置项如下（密钥一律通过 `.env` 注入，不写在配置里）：

### 模型

| 配置 | 值 | 说明 |
|------|-----|------|
| `model.default` | `qwen3.8-27b` | 主对话模型（**本地 vLLM `:8000`，默认；数据不出校**） |
| `model.provider` | `local` | 框架支持按需切换至云端 OpenAI 兼容模型（可选） |
| `custom_providers.local` | `http://127.0.0.1:8000/v1` | 本地 vLLM `qwen3.8-27b` |
| `auxiliary.compression` / `auxiliary.vision` | 本地 `qwen3.8-27b` | 上下文压缩、视觉理解 |
| `model.context_length` | `131072` | Hermes 侧上下文（vLLM 实际支持 262144，取 131072 控制首包） |

> **默认全本地推理**：主对话、VL、Step-Back、上下文压缩均走本地 `:8000` 的 `qwen3.8-27b`；
> 框架亦支持接入云端 OpenAI 兼容模型（可选）。

### 技能与工具

| 配置 | 值 | 说明 |
|------|-----|------|
| `skills.external_dirs` | `.../agent4som/shared_skills` | **三个自研 Skill 的注册入口** |
| `platform_toolsets.miniapp` | `[web, terminal, file, skills, todo, rag]` | 小程序平台可用工具集 |
| `role_toolsets` | 按 `admin` / `teacher` / `student` / `guest` 分级 | 角色级工具集 |

### 平台与网关

| 配置 | 值 | 说明 |
|------|-----|------|
| `gateway.platform` | `miniapp` | 当前唯一入口 |
| `platforms.miniapp.port` | `8010` | miniapp 适配器监听端口 |
| `platforms.miniapp.sqlite_path` | `.../agent4som/data/sqlite/miniapp.db` | 会话库 |
| `platforms.miniapp.allowed_origins` | `https://<CAMPUS_PORTAL>` | 允许来源 |
| `plugins.enabled` | `[miniapp-platform]` | 启用小程序平台插件 |

### 其它

- `knowledge_base.chroma_path`：ChromaDB 数据目录；
- `agent.max_turns` / `gateway_timeout`：Agent 轮次与网关超时；
- `display.interim_assistant_messages: false`：避免工具前旁白抢先返回给前端；
- `sessions.retention_days` / `compression` / `memory` / `logging`：会话保留、压缩、记忆、日志轮转。

> **企业微信入口已弃用并移除（2026-09）**，`config.yaml` 中不再有 `wecom` 配置，
> 也不再需要 `WEIXIN_*` / `WECOM_*` 凭据。

---

## 3. Agent 身份与角色

- **`SOUL.md`**：定义 Agent 的身份与系统提示词（「西安交通大学管理学院本科新生学业规划智能助手」），
  作为 system prompt 的主身份注入。
- **`roles.json`**：RBAC 角色映射（platform → user_id → role），运行时由业务侧读写，**不入库**。
  角色解析优先级：环境变量所有者 → `roles.json` → 平台默认（未登记的 miniapp 用户默认 `guest`）。

---

## 4. 平台适配器（miniapp）

`plugins/miniapp-platform/` 是本项目自研的微信小程序平台适配器（`plugin.yaml` + `adapter.py`）。

- 注册平台名 `miniapp`，监听 `127.0.0.1:8010`；
- **登录**：小程序 `wx.login()` 拿 code → `/api/miniapp/login` → 后端用
  `WECHAT_MINIAPP_APPID` / `WECHAT_MINIAPP_APPSECRET` 调微信接口换 openid；
- **会话令牌**：后端用 `MINIAPP_SESSION_SECRET` 做 HMAC-SHA256 签发不透明 token，
  有效期 24 小时，前端只存储与附带，不参与计算；
- **主要路由**：`/api/chat`（对话）、`/api/miniapp/login|history|run-progress|run-trace`、
  `/api/uploads`（文件上传入库）、`/api/methods/*`（身份、档案、认证、知识库管理、通知同步等）。

`scripts/deploy_tools.sh` 会把该插件与 RAG 工具部署到 Hermes 运行时，并为 `toolsets.py`
补上 `rag` 工具集。

---

## 5. 启动钩子（kb_init）

`hooks/kb_init/` 在网关 `gateway:startup` 事件时初始化知识库：

1. 依赖 `AGENT4SOM_HOME` 环境变量把 `agent4som` 仓库加入 `sys.path`；
2. 构建嵌入函数（Qwen3 / 内置回退）并初始化 `ChromaRepository` 单例；
3. 初始化配额存储、审计日志与角色审计；
4. 在工作线程中预加载 BM25 索引（避免阻塞事件循环）；
5. **不做自动入库**——入库由 `knowledge_ingest` 工具驱动。

源码位置为 `agent4som/gateway/hooks/kb_init/`，部署时链接为 `~/.hermes/hooks/`。

---

## 6. hermes-agent 上游框架（单独安装，不入库）

`hermes-agent/` 是 **上游第三方框架 Hermes Agent**（Nous Research，MIT License，当前 **v0.18.2**），
以 editable 源码安装在本目录下（`$HERMES_HOME/hermes-agent/`，含自带 venv，Python 3.12）。

**该目录约 940MB，不纳入本仓库版本控制**（已在 `.gitignore` 中排除）。克隆后需自行安装：

```bash
cd agent4som-hermesagent
git clone https://github.com/NousResearch/hermes-agent hermes-agent
cd hermes-agent
git checkout v2026.7.7.2   # 对应 hermes-agent 0.18.2（本项目验证版本）
uv venv --python 3.12
uv pip install -e ".[dev]"
```

**本项目对框架的定制不放在框架目录内**，而是保存在
[`../agent4som/hermes_overlay/`](../agent4som/hermes_overlay/)：

- `tools/query_kb.py`、`tools/knowledge_ingest.py`：RAG 检索与知识入库工具；
- `patches/hermes-local.patch`：对 `gateway/run.py`（Agent 活动事件旁路）、
  `gateway/platforms/base.py`、`tui_gateway/server.py`（`skill.activate` 事件）的本地改动。

安装好框架后，执行以下命令应用定制：

```bash
bash agent4som/scripts/deploy_tools.sh
```

> ⚠️ 升级 Hermes 后需重新执行 `deploy_tools.sh`（并在必要时重打 `hermes-local.patch`），
> 确保本项目定制被重新应用。

---

## 7. 技能目录：skills 与 shared_skills

| 目录 | 内容 | 是否自研 |
|------|------|----------|
| `agent4som-hermesagent/skills/` | Hermes 官方 bundled / hub 技能库（由 curator 管理） | 否（上游） |
| `agent4som/shared_skills/` | 三个自研 Skill | **是** |

三个自研 Skill **不在**家目录的 `skills/` 下，而是通过 `config.yaml` 的
`skills.external_dirs` 外部注册：

- `academic-warning`（选课合理性检查）
- `training-plan-interpretation`（培养方案智能解读）
- `multi-path-academic-planning`（多路径个性化学业规划）

---

## 8. 部署与运维

### 8.1 systemd 服务

| 单元 | 说明 |
|------|------|
| `hermes-gateway@.service` | 网关模板服务，实际实例 `hermes-gateway@jwc-assistant` |
| `chroma-server.service` | ChromaDB（网关的依赖服务） |

网关单元的关键环境：

```
WorkingDirectory=/home/<DGX_USER>/SparkPath-DGX-SPARK/agent4som
EnvironmentFile=.../agent4som-hermesagent/.env
HERMES_HOME=/home/<DGX_USER>/.hermes
ExecStart=.../hermes-agent/venv/bin/hermes gateway run --replace
```

### 8.2 端口

| 端口 | 服务 |
|------|------|
| 8010 | Hermes miniapp 适配器 |
| 8020 | miniapp-proxy（dgx 版，生产为 8000） |
| 8007 | ChromaDB |
| 8000 / 8001 / 8002 / 8005 | LLM / Embedding / Reranker / MinerU |

> 模型栈的选型、性能与部署见
> [`../docs/DGX-SPARK-模型部署方案.md`](../docs/DGX-SPARK-模型部署方案.md)；
> GPU 服务单元见 `deploy/gpu-services/systemd/dgx-*.service`。

### 8.3 常用命令

```bash
# 启动 / 重启网关
hermes gateway run --replace
sudo systemctl restart hermes-gateway@jwc-assistant

# 查看状态与日志
sudo systemctl status hermes-gateway@jwc-assistant
tail -f logs/gateway.log
journalctl -u hermes-gateway@jwc-assistant -f

# 定时任务（Hermes cron）
ls cron/                          # 任务定义
```

### 8.4 教务通知同步

`scripts/jxtz_sync.py` 是 Hermes cron 的包装脚本：用 `os.execv` 切换到 `agent4som/venv`
并执行 `agent4som/scripts/sync_jxtz.py`，实现每日自动同步学校官网教务通知。

---

## 9. 安全约定

- **`.env` 含全部密钥**（模型 Key、小程序凭据、ChromaDB Token 等），已在 `.gitignore` 中排除，
  **严禁提交**；README 与文档中只列变量名，不写明文；
- `redact_secrets` / `redact_pii` 默认开启，日志中对敏感信息脱敏；
- `roles.json`、`state.db*`、`gateway_state.json` 等运行时文件不入库；
- 会话保留 90 天后自动清理。

---

## 许可证

- 本项目自研部分（`config.yaml`、`SOUL.md`、`plugins/`、`hooks/`、`scripts/` 等）基于 MIT License；
- `hermes-agent/` 子目录版权归上游 Nous Research，遵循其 MIT License。
