# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 仓库概览

SparkPath：「**本科新生学业规划智能助手**」—— 面向高校教务的 AI 智能助手（RAG 知识库 + 业务 Skill），部署在 DGX Spark 单机上，通过微信小程序交付。本仓库是 monorepo，由四部分组成：

| 目录 | 角色 |
|------|------|
| `agent4som/` | 后端核心：RAG 数据层（`knowledge_base/`）+ 四个业务模块 + 三个自研 Skill。**大部分开发发生在这里** |
| `agent4som-hermesagent/` | Hermes Agent 运行时家目录（`HERMES_HOME`，约定 `~/.hermes` 软链指向它）。入库的只有 `config.yaml` / `SOUL.md` / `plugins/` / `scripts/`；上游框架 `hermes-agent/`（约 940MB）单独安装、gitignored |
| `miniprogram-framework-frontend/` | 微信小程序前端（原生小程序，无运行时 npm 依赖） |
| `deploy/` + `docs/` | DGX 部署配置（隧道、nginx、systemd）与部署清单 |

各子项目有更详细的 README：`agent4som/README.md`、`agent4som-hermesagent/README.md`、`miniprogram-framework-frontend/README.md`、`agent4som/shared_skills/README.md`（Skill 开发评测指南）。

## 常用命令

### 后端（`agent4som/`，Python ≥3.11，用 `agent4som/venv`）

```bash
cd agent4som
source venv/bin/activate            # 或使用系统 Python；两套 venv 的 chromadb 必须同版本 (1.5.9)

python -m pytest tests/ -v                          # 全部测试（内存 MockRepo，无需真实数据库）
python -m pytest tests/academicwarning/ -v          # 按模块
python -m pytest tests/trainingplan/test_x.py::test_y -v   # 单个测试

bash scripts/smoke_test.sh          # 冒烟：网关 / ChromaDB / GPU 服务 / SQLite / 磁盘
bash scripts/cve_check.sh           # 依赖漏洞扫描（pip-audit）

# 业务 CLI（Skill 的主命令也走这些入口）
python -m academicwarning.cli precheck --grade <年级>   # 数据齐全性预检（只读）
python -m academicwarning.cli check --grade <年级>      # 执行选课检查并导出 xlsx
python -m trainingplan.cli interpret --major <专业> --entry-year <年级>
python -m trainingplan.cli {list,status,modes,route,select,simulate,...}

# 知识库运维
python scripts/admin_cli.py ingest <file> --scope global   # 单文件入库
python scripts/admin_cli.py {health,audit,purge,roles}     # 健康检查 / 审计 / 清理 / 角色管理
python scripts/sync_jxtz.py                                # 手动触发教务通知同步
```

### 前端（`miniprogram-framework-frontend/`，Node ≥16.7）

```bash
cd miniprogram-framework-frontend
npm run switch      # 生成构建产物（克隆后必须先执行，否则编译失败）
npm test            # 架构核验 + 界面核验（validate.js + validate-ui.js）
npm run release     # miniprogram-ci 上传（CI 中手动触发）
```

### 服务运维（DGX Spark 上，需 sudo）

```bash
sudo systemctl status chroma-server hermes-gateway@jwc-assistant
sudo journalctl -u hermes-gateway@jwc-assistant -f
```

## 架构要点（跨多文件才能理解的部分）

### 双平面解耦

- **Data Plane**（`agent4som/knowledge_base/`）：文档摄入（解析→切分→向量化→入库）、分层可见性检索、审计。按端口-适配器模式组织（`repository/interfaces.py` 定义抽象，`chroma_repository.py` 实现）。
- **Policy Plane**（`academicwarning/`、`trainingplan/`、`doccenter/`、`management_flow/` + `shared_skills/`）：基于 Data Plane 输出做业务计算，不重复解析文档。各业务模块有自己的 SQLite（`data/warning.db`、`data/training_plan.db`、`data/doc_center.db`）。

### 请求链路（端到端）

```
微信小程序 → 校园网关 /accapi/ → miniapp-proxy(:8020/8000, 路径归一化)
  → Hermes miniapp 适配器(:8010, HMAC session token) → Hermes Agent (LLM qwen3.8-27b @ :8000)
  → 自研 Skill（shared_skills/, 经 config.yaml skills.external_dirs 注册）
  → 业务 CLI → 业务 SQLite / knowledge_base → ChromaDB(:8007, token 认证)
```

预警/规划 API 不走 Hermes：小程序直接调 `academic-warning-api(:8008)` 与 `training-plan-api(:8009)`，用 `X-API-Key` 鉴权。端口与鉴权总表见 `agent4som/README.md` §5。

### Hermes 运行时与自研改动的固化方式

- `~/.hermes` 是指向 `agent4som-hermesagent/` 的软链；上游框架在 `hermes-agent/venv`（独立 venv）。
- 对上游框架的改动**不在框架目录里改**，而是固化在 `agent4som/hermes_overlay/`（`tools/` 的 RAG 工具 + `patches/hermes-local.patch`），部署时用 `scripts/deploy_tools.sh` + `git apply` 覆盖上去。
- 网关启动钩子 `agent4som/gateway/hooks/kb_init/` 负责初始化知识库单例。

### 前端构建产物机制（关键约定）

`config/instances/jwc.js` 是唯一配置事实来源；`scripts/build.js` 生成 `app.json`、`project.config.json`、`config/instance.js`（**均 gitignored**）。改 tabBar / 新增共享页面必须改 `scripts/build.js`，再 `npm run switch`——直接改 `app.json` 会被覆盖。

### 权限体系

5 角色（owner/admin/teacher/student/guest），知识库三层 scope（`global` / `teachers` / `users/{id}`），ACL 单一真相源在 `agent4som/knowledge_base/retrieval/acl_filter.py`，多层同时执行。Skill 权限由命令层读取 `HERMES_SESSION_PLATFORM` / `HERMES_SESSION_USER_ID` 强制，不提供伪造身份的参数入口。

## 硬性约束

- **ChromaDB 生产必须走 HTTP**（`CHROMA_HOST=127.0.0.1:8007` + token），禁止 `PersistentClient` 直接写 `data/chroma/`（会导致 HNSW 索引损坏）；`--reset` 也走 HTTP API，不停服务。
- `.env` 含密钥，gitignored，切勿提交；密钥一律经 `.env` 注入，不写入 `config.yaml` 或代码。
- 两个 venv（`agent4som/venv` 与 `hermes-agent/venv`）的 `chromadb` 版本必须一致（1.5.9）。
- `tests/conftest.py` 依赖 `~/.hermes/hermes-agent` 存在（加入 sys.path）；测试报找不到模块先确认软链。
- Skill 评测产物（reports/validate-*）放仓库外（如 `~/work/skilleval/`），不要放 skill 目录；评测必须用 `agent4som/scripts/run_skill_eval.sh` 包装脚本，不要直接跑 SkillEvaluator。
- 不重装/重建运行环境（不重跑 install、不重建数据库/ChromaDB 数据目录）；环境坏了先报告，由用户决定恢复方式。

## 代码规范（强制，完整版见 `agent4som/docs/05-notes/code-standards/README.md`）

规则已固化为 ruff 闸门（`agent4som/pyproject.toml`），提交前必须双绿：

```bash
cd agent4som && ruff check .                                   # 0 error
python -m pytest tests/ -m "not integration and not network" -q  # 0 failed
```

要点（违反会被 lint/测试挡下，不要绕过豁免）：

- **clean code**：规则集 `E/F/W/B/SIM/C4/RET/RUF/PLW1510`；except 内再 raise 必须 `from exc`；`subprocess.run` 必须显式 `check=`；有返回类型注解的函数全分支显式 return；`ContextVar` 禁止可变共享默认值；类属性可变容器必须 `ClassVar`；**`ruff --unsafe-fixes` 禁止批量跑**（SIM118 曾把 sqlite3.Row 当 dict 改坏 9 处 DAO），批量 autofix 后必须跑全量离线套件。
- **单元/集成测试**：测试目录镜像源码结构；新源文件/大函数（>50 行）须伴随测试；`tmp_path` 隔离 DB、`monkeypatch` 打模块级 `_API_KEY`、FastAPI TestClient 不触发 lifespan、被 `~/.hermes` 遮蔽的模块用 `importlib.util.spec_from_file_location` 加载；真实服务依赖打 `integration`/`network` marker，skip 必须带 reason。
- **安全测试**（`agent4som/tests/security/`，OWASP 7 类）：新增 API 端点必须补认证负面用例 + 角色×操作矩阵更新；新增查询入口必须补注入参数化用例；新增上传功能必须补文件名穿越/类型门/配额用例；已知未修复问题的测试保持 skip 并指向 `docs/04-security/` 文档。

## 测试与文档约定

- 后端测试镜像源码结构（`tests/knowledge_base/`、`tests/academicwarning/`…）；真实 ChromaDB 集成测试：`tests/knowledge_base/repository/test_chroma_integration.py`（临时存储，安全）。
- 设计文档全中文，按 `agent4som/docs/README.md` 的规范分目录：`01-architecture`（架构）、`02-features`（`{nnn}-{slug}.md`）、`03-issues`（排查）、`04-security`、`05-notes`。新设计/问题排查文档遵循该编号与目录约定。
- 顶层 `docs/` 是 DGX 部署文档：`DGX-SPARK-模型部署方案.md`（模型栈）与 `DGX-SPARK-部署清单.md`（应用迁移记录）。
