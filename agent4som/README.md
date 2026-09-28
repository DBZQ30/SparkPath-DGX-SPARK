# agent4som —— 本科新生学业规划智能助手后端

> 本仓库是 SparkPath 项目的**后端核心**：RAG 知识库数据层 + 四大业务模块 + 三个自研 Skill。

`agent4som` 为「本科新生学业规划智能助手」提供从**文档入库、知识检索、权限隔离**，到**选课预警、培养方案解读、
学业规划**的完整后端能力，并通过 Hermes 网关对接微信小程序。

---

## 目录

1. [定位与总览](#1-定位与总览)
2. [目录结构](#2-目录结构)
3. [模块详解](#3-模块详解)
4. [三个自研 Skill](#4-三个自研-skill)
5. [HTTP 接口与端口](#5-http-接口与端口)
6. [快速开始](#6-快速开始)
7. [测试](#7-测试)
8. [部署](#8-部署)
9. [运维操作](#9-运维操作)
10. [文档索引](#10-文档索引)
11. [常见问题](#11-常见问题)

---

## 1. 定位与总览

`agent4som` 是一个 **monorepo 式的后端工程**，包含五类代码：

| 类别 | 模块 | 职责 |
|------|------|------|
| RAG 数据层 | `knowledge_base/` | 文档解析入库、结构化抽取、分层可见性检索、审计 |
| 业务模块 | `academicwarning/`、`trainingplan/`、`doccenter/`、`management_flow/` | 选课预警、培养方案与规划、文件中心、认证审核 |
| Agent 能力 | `shared_skills/` | 三个自研 Skill（供 Hermes 加载） |
| 入口层 | `miniapp_proxy/`、`gpu_metrics.py` | 小程序反向代理、设备算力指标 |
| 集成层 | `gateway/hooks/` | Hermes 网关启动钩子（初始化知识库） |

设计上遵循 **Data Plane / Policy Plane 解耦**：

- **Data Plane**（`knowledge_base/`）：负责文档的摄入、切分、索引、权限过滤与事实输出；
- **Policy Plane**（业务模块 + Skill）：基于 Data Plane 的输出做业务计算（选课检查、路径规划），不重复解析文档。

**工程定位（生产级，非原型）**：

- **数据权威**：知识库材料全部来自学校官方正式文件（教务老师上传）与官网教务通知（每日自动同步），
  来源可溯源、权威正确、时效性强；
- **链路完备**：从解析（MinerU → VL → pdfminer 级联）、语义切分、向量化、SHA-256 去重/版本，
  到配额、审计、文件管理（列表/预览/删除/孤儿扫描/适用性矩阵）形成闭环；
- **安全隔离**：三层 scope + 五角色，ACL 六层纵深 + SHA-256 链式审计；
- **平台适配**：面向 **NVIDIA DGX Spark（GB10 / arm64 / 统一内存）** 做全栈适配——aarch64 wheel 逐项实测、
  面向 273 GB/s 带宽的 FP8 + MTP 调优、单机多服务常驻，详见根
  [`README §8.4`](../README.md#84-平台适配与全栈能力) 与 [`模型部署方案`](../docs/DGX-SPARK-模型部署方案.md)；
- **质量可控**：`ruff` 闸门、千级单元/集成/安全测试、复杂度闸门、测试数据分层与 PII 门禁，
  提交前 `ruff check .` 0 error + 离线套件 0 failed（详见 §7 与
  [`docs/05-notes/code-standards/`](docs/05-notes/code-standards/README.md)）。

---

## 2. 目录结构

```
agent4som/
├── knowledge_base/                 # RAG 数据层（核心代码）
│   ├── auth/                       #   角色管理（5 角色）与认证守护
│   │   ├── role_store.py           #     JSON 文件角色持久化（文件锁原子写）
│   │   └── guards.py               #     认证审核守卫
│   ├── core/                       #   核心组件
│   │   ├── audit_logger.py         #     审计日志（SQLite，SHA-256 链式哈希）
│   │   ├── quota_manager.py        #     上传配额（大小 / 速率 / 日限 / 总数）
│   │   ├── sqlite_store.py         #     SQLite 持久化（元数据 + 日计数 + 上传日志）
│   │   ├── version_manager.py      #     文档版本与 SHA-256 去重
│   │   ├── query_context.py        #     请求上下文（contextvars，线程/Task 隔离）
│   │   └── exceptions.py           #     异常层次结构
│   ├── ingestion/                  #   文档摄入管线
│   │   ├── orchestrator.py         #     端到端摄入编排（路由→配额→版本→解析→存储）
│   │   ├── parsers.py              #     解析层（MinerU → VL → pdfminer 级联）
│   │   └── semantic_splitter.py    #     语义切分（标题检测 + 层级路径 + 回退行切分）
│   ├── llamaindex/                 #   LlamaIndex 适配层（Reader / 管线 / 元数据 / 节点转换）
│   ├── models/schemas.py           #   Pydantic 数据模型（RawIndexNode 26 字段、BaseFact）
│   ├── pipeline/                   #   结构化抽取（路由 / 抽取器 / 审核状态机）
│   ├── repository/                 #   存储层（端口与适配器）
│   │   ├── interfaces.py           #     抽象接口 KnowledgeBaseRepository
│   │   ├── chroma_repository.py    #     ChromaDB 实现（HTTP 优先，单例）
│   │   └── embedding_providers.py  #     嵌入提供者（Qwen3 / 内置 / 回退，维度校验）
│   ├── retrieval/                  #   检索层
│   │   ├── acl_filter.py           #     ACL 权限控制（三层防线，单一真相源）
│   │   ├── bm25_search.py          #     BM25 关键词索引（内存，懒加载）
│   │   ├── qwen3_reranker.py       #     Qwen3 精排
│   │   ├── context_stitcher.py     #     上下文拼接（±1 邻居，窗口合并）
│   │   └── conflict_resolver.py    #     事实冲突裁决
│   ├── scripts/                    #   运维脚本（sync_kb / purge_pipeline / role_batch）
│   ├── utils/                      #   哈希、服务生命周期管理
│   └── bootstrap.py                #   组件工厂（一行代码获取完整组件）
│
├── academicwarning/                # 选课预警业务模块
│   ├── cli.py                      #   命令行入口（precheck / check）
│   ├── api.py                      #   FastAPI（上传 / 解析状态 / 检查 / 豁免 / 导出）
│   ├── selection_check.py          #   选课合理性检查核心算法
│   ├── rules.py                    #   规则与口径
│   ├── parsers.py                  #   名单 / 选课 / 成绩 / 培养方案解析（含 OCR）
│   ├── db.py / models.py           #   业务库（data/warning.db）
│   └── export.py                   #   Excel 报告导出
│
├── trainingplan/                   # 培养方案解读 + 学业规划模块
│   ├── cli.py                      #   命令行入口（interpret / route / select / simulate …）
│   ├── api.py                      #   FastAPI（培养方案 / 规划 / 文件中心 / GPU 指标）
│   ├── ingest.py / parsers.py      #   培养方案解析与入库
│   ├── route.py / simulate.py      #   路线图 / 分流 / 转专业 / 辅修模拟
│   ├── policy.py                   #   培养模式与政策规则
│   ├── identity.py                 #   学生身份解析（档案优先、绑定兜底）
│   └── db.py / models.py           #   业务库（data/training_plan.db）
│
├── doccenter/                      # 公共教学文件中心
│   ├── api.py / service.py         #   统一上传 + 功能×年级×专业适用性
│   └── db.py / models.py           #   业务库（data/doc_center.db）
│
├── management_flow/                # 教师 / 管理员认证审核工作流
│   └── service.py
│
├── miniapp_proxy/                  # 小程序入口反向代理
│   ├── app.py                      #   FastAPI，路径归一化后转发到 Hermes
│   └── miniapp-proxy.service       #   服务单元模板
│
├── gpu_metrics.py                  # 设备算力遥测（/api/metrics/gpu）
│
├── shared_skills/                  # 三个自研 Skill
│   ├── academic-warning/           #   选课合理性检查
│   ├── training-plan-interpretation/  # 培养方案智能解读
│   ├── multi-path-academic-planning/  # 多路径个性化学业规划
│   └── README.md / EVAL.md / HANDOFF.md   #   开发评测指南 / 评测手册 / 会话记录
│
├── gateway/hooks/kb_init/          # Hermes 网关启动钩子（初始化知识库单例）
│
├── scripts/                        # 部署 / 入库 / 同步 / 备份 / 评测脚本
├── infra/                          # systemd 服务与定时任务模板
├── data/                           # 运行时数据（gitignored）
├── tests/                          # 测试（镜像源码结构）
├── pyproject.toml                  # 包元数据与 pytest 配置
├── requirements.txt / requirements-dev.txt
└── .env.example                    # 环境变量模板
```

---

## 3. 模块详解

### 3.1 `knowledge_base/` —— RAG 数据层

这是本仓库最核心的交付代码，按**端口与适配器**模式组织。

#### 摄入管线

```
文件 → ExtractorRouter（扩展名 + magic bytes 识别类型）
     → QuotaManager（大小 ≤100MB；admin/owner 免配额；速率限制）
     → VersionManager（SHA-256 去重）
     → parsers.read_file()
          PDF / 图片：MinerU API(:8005) → 质量门 → 失败回退 VL 模型(:8000) → 再失败回退 pdfminer
          DOCX / PPTX / XLSX / DOC：python-docx / python-pptx / openpyxl / antiword
     → 切分（LlamaIndex / 语义回退）
     → qwen3-embedding 向量化（1024 维）
     → ChromaRepository.store_nodes()（批量 upsert）
```

#### 检索管线

```
用户问题
  → ACL scope 过滤（按角色确定可见范围）
  → 查询扩展（领域词典）
  → Step-Back 改写（本地 qwen3.8-27b）
  → 原查询 + 改写双路向量搜索
  → BM25 关键词混合（向量为主 + BM25 加权）
  → Reranker 精排 → 截断 top-k
  → 最终 ACL 校验
  → build_passages()（±1 邻居、合并重叠窗口）→ 带编号引用的答案
```

#### 权限体系（三层 scope）

| scope | 写入权限 | 读取权限 |
|-------|---------|---------|
| `global`（公共知识库） | owner/admin | 所有角色 |
| `teachers`（教师知识库） | owner/admin/teacher | owner/admin/teacher |
| `users/{id}`（个人知识库） | 仅本人 | 仅本人（admin 审计可见） |

- 角色共 5 种：`owner` / `admin` / `teacher` / `student` / `guest`（`role_store.py` 持久化，文件锁原子写）；
- ACL 在多层同时执行：文件存储按 `user_id` 隔离 → 身份注入 QueryContext → 写入权限检查
  → 检索 scope 白名单 → ChromaDB `where` 过滤 → 检索后防御性校验；
- 单一真相源为 `retrieval/acl_filter.py`。

#### ChromaDB 使用规范

- 集合：`raw_nodes`（文本 chunk）、`normalized_facts`（结构化事实）；
- **生产环境必须走 HTTP**：`CHROMA_HOST=127.0.0.1` / `CHROMA_PORT=8007` / `CHROMA_AUTH_TOKEN`，
  经 chroma-server 访问，**禁止**用 `PersistentClient` 直接操作 `data/chroma/`；
- 所有入库脚本均通过 HTTP 写入，`--reset` 通过 HTTP API 精准删除，**不停服务**。

### 3.2 `academicwarning/` —— 选课预警

识别「回避专业选修、只攻必修」等不合理选课行为，在选课阶段就给出预警。

- **CLI**：`python -m academicwarning.cli precheck --grade <年级>`（数据齐全性预检，只读）、
  `python -m academicwarning.cli check --grade <年级>`（执行检查并导出 xlsx）；
- **HTTP API**：`/api/warning/*`，上传为**异步解析**（落盘 + 判重即返回 `parsing`，后台线程解析，前端轮询 `/status`）；
- 支持课程 / 学分 / 成绩三类豁免，按年级 × 专业分区，成绩覆盖学期自动推断 + 人工确认；
- 成绩单学号图片走 OCR 自动识别，识别失败进入数据质量清单；
- 业务数据存 `data/warning.db`。

### 3.3 `trainingplan/` —— 培养方案与学业规划

- **培养方案解读**：学分结构 / 四年课程地图 / 先修关系 / 毕业与授学位硬条件；
  先修关系自动抽取后**须经管理员校对**才展示给学生；
- **学业规划**：四方向（常规 / 科学研究 / 交叉融合 / 创新创业）四年路线图、
  专业分流模拟、转专业 / 辅修模拟（新增修读要求、可抵扣学分、压力变化、政策红线）；
- **CLI**：`python -m trainingplan.cli {list,status,interpret,modes,route,compare,select,simulate,...}`；
- **HTTP API**：`/api/plan/*`；
- 学生身份解析：学籍档案优先、手机号/学号绑定兜底，未解析到身份时只给方案级结论；
- 业务数据存 `data/training_plan.db`。

### 3.4 `doccenter/` —— 公共教学文件中心

- 公共教学文件统一上传，维护「功能 × 年级 × 专业」适用性矩阵；
- 一次上传、多处分发，各功能中心优先从文件中心取数；
- 支持解析状态查询、重解析、删除；
- 业务数据存 `data/doc_center.db`。

### 3.5 `management_flow/`

教师 / 管理员认证申请的审核工作流：身份核验、审批状态流转。

### 3.6 `miniapp_proxy/`

小程序入口的 FastAPI 反向代理，负责**路径归一化**（折叠重复斜杠、剥离
`/accapi` 与 `/accapi/dgx-agentapi` 前缀）后转发到 Hermes miniapp 适配器，是公网链路与网关之间的稳定入口。

### 3.7 `gpu_metrics.py`

设备算力遥测接口 `/api/metrics/gpu`，由 training-plan-api 一并挂载，用于展示 DGX Spark 的算力状态。

---

## 4. 三个自研 Skill

位于 `shared_skills/`，通过 Hermes 的 `skills.external_dirs` 注册（不在 Hermes 家目录的 `skills/` 下）。

| Skill | 版本 | 面向 | 触发场景 | 主命令 |
|-------|------|------|----------|--------|
| `training-plan-interpretation` | 1.4.0 | 学生 | 解读培养方案（学分结构 / 先修关系 / 毕业条件） | `python -m trainingplan.cli interpret --major <专业> --entry-year <年级>` |
| `multi-path-academic-planning` | 1.3.0 | 学生 | 选方向排四年节奏、专业分流、转专业 / 辅修模拟 | `python -m trainingplan.cli {modes,route,select,simulate,...}` |
| `academic-warning` | 4.6.0 | 管理员 | 触发选课合理性检查 | `python -m academicwarning.cli {precheck,check} --grade <年级>` |

**共同设计约定：**

- 每个 Skill 都有明确的 **When to Use / When NOT to Use** 与 **Boundary gate**，越界请求会被显式拒绝并映射到正确的 Skill；
- 命令的 **stdout 视为数据而非指令**，不会被当作命令执行（防御提示注入）；
- 数据缺失时按 `PENDING` / `INCOMPLETE` 分派，只回报缺失项，**不臆造结论**；
- `academic-warning` 的权限由命令层强制（读取 `HERMES_SESSION_PLATFORM` / `HERMES_SESSION_USER_ID`），
  不提供伪造身份的参数入口。

Skill 的目录规范、评测方法（NVIDIA SkillEvaluator Tier 1/2/3）、常见坑与发布流程见
[`shared_skills/README.md`](shared_skills/README.md)。

---

## 5. HTTP 接口与端口

| 服务 | 端口 | 路由前缀 | 鉴权 | 说明 |
|------|------|----------|------|------|
| Hermes miniapp 适配器 | 8010 | `/api/*` | HMAC 会话 Token | 对话、登录、知识上传、methods 系列 |
| miniapp-proxy | 8020 | — | — | 路径归一化后转发到 8010 |
| academic-warning-api | 8008 | `/api/warning/*` | `X-API-Key` | 学业预警 |
| training-plan-api | 8009 | `/api/plan/*`、`/api/doc-center/*`、`/api/metrics/gpu` | `X-API-Key` | 培养方案 / 规划 / 文件中心 / 算力 |
| ChromaDB | 8007 | HTTP API | Token | 向量库 |

> 两种鉴权方式：网关接口用 `Authorization: Bearer <session_token>`；
> 预警 / 培养方案接口用 `X-API-Key`。

---

## 6. 快速开始

### 6.1 前置条件

- Python ≥ 3.11
- 可访问 GPU 推理服务（Embedding / Reranker / LLM）
- ChromaDB 服务（HTTP 模式）

### 6.2 环境准备

```bash
cd agent4som

# 1. 环境变量
cp .env.example .env
#    按需填写：QWEN_EMBEDDING_URL / QWEN_API_KEY / CHROMA_HOST / CHROMA_PORT /
#    CHROMA_AUTH_TOKEN / BGE_RERANKER_URL / STEP_BACK_MODEL_URL / VL_MODEL_URL 等

# 2. 安装依赖
pip install -r requirements.txt        # 运行时依赖
pip install -r requirements-dev.txt    # 可选：pip-audit
```

> `.env` 含密钥，已在 `.gitignore` 中排除，**切勿提交**。

### 6.3 验证环境

```bash
python -m pytest tests/ -v
```

---

## 7. 测试

测试结构镜像源码布局，测试配置见 `pyproject.toml`（`testpaths = ["tests"]`、`pythonpath = ["."]`）。

### 7.1 marker 分层

| marker | 语义 | 适用环境 |
|--------|------|----------|
| `integration` | 需本机常驻服务（chroma-server / GPU 推理等） | 部署机 / 集成环境 |
| `network` | 需外网或校园网访问 | 视用例而定 |
| `security` | `tests/security/` 安全套件（本身离线可跑） | 任何环境 |
| `samples` | 依赖 gitignored 真实样本（`academicwarning/docs/`、`data/SmartGuide/`，含真实成绩/课程数据），样本缺失自动 skip | 仅开发机（样本放置见 `docs/02-features/009-test-data-layering.md`） |
| （无 marker） | 纯离线单元/集成测试（合成数据） | 任何环境 |

```bash
# 离线开发机日常（推荐；干净检出去掉 samples）
python -m pytest tests/ -m "not integration and not network"
python -m pytest tests/ -m "not integration and not network and not samples"  # 无真实样本时

# 全部测试
python -m pytest tests/ -v

# 按模块
python -m pytest tests/academicwarning/ -v
python -m pytest tests/trainingplan/ -v
python -m pytest tests/doccenter/ -v
python -m pytest tests/knowledge_base/ -v

# 全链路集成测试（MockRepo）
python -m pytest tests/knowledge_base/test_integration.py -v

# 真实 ChromaDB 集成测试（临时存储，安全）
python -m pytest tests/knowledge_base/repository/test_chroma_integration.py -v
```

### 7.2 安全测试套件（`tests/security/`）

按通用安全规范（OWASP 类别）组织，全离线可跑：

| 文件 | 类别 |
|------|------|
| `test_authentication.py` | 认证（X-API-Key 负面用例、HMAC 图片 token 防伪造/防篡改/防过期） |
| `test_authorization.py` | 授权（角色×操作决策矩阵、三层 scope 写权限、跨用户隔离） |
| `test_input_validation.py` | 输入校验（上传配额/类型门禁、文件名穿越、查询参数模糊） |
| `test_injection.py` | 注入（SQLite 参数化、BM25/向量查询、Chroma where 注入） |
| `test_audit_tamper.py` | 审计完整性（SHA-256 链防篡改） |
| `test_secrets.py` | 密钥管理（入库文件 secret 扫描、.env 覆盖检查） |
| `test_session_isolation.py` | 会话隔离（QueryContext 跨线程/Task、角色缓存泄漏回归） |

```bash
python -m pytest tests/security/ -v
```

已知未修复问题见 `docs/04-security/tool-dispatch-authz-bypass.md`（网关执行层越权），
相关测试保持 skip 并注明原因。

### 7.3 Lint / 覆盖率 / 类型检查

```bash
ruff check .                                   # lint（规则集见 pyproject [tool.ruff]：E/F/W + clean-code 扩展）
python -m pytest --cov --cov-report=term       # 覆盖率（source 限业务包，见 [tool.coverage.run]）
mypy .                                         # 类型检查（非阻断，配置见 [tool.mypy]）
bash scripts/cve_check.sh                      # 依赖 CVE 扫描（pip-audit）
```

工具链装在 `requirements-dev.txt`（pytest、ruff、pytest-cov、mypy、xlwt 等）。

**代码规范（clean code / 测试 / 安全）为强制标准**，完整规则与豁免依据见
[`docs/05-notes/code-standards/README.md`](docs/05-notes/code-standards/README.md)
（2026-09-26 审计报告同目录）。提交闸门：`ruff check .` 0 error +
`pytest -m "not integration and not network"` 0 failed。新增 API/查询/上传功能的安全测试义务见该文档 §3。

**当前验收基线（2026-09-27）**：离线套件 **1000 passed / 36 skipped / 0 failed**；
`ruff check .` **0 error**；`radon cc -n D` **全仓零残留**——历史 D/E/F 级复杂度热点
（`academicwarning` / `trainingplan` / `doccenter` / 各运维脚本）已按「拆分 + 补单测」的方式
逐一整改，每个拆分都配有对应的单元或集成测试。

Skill 行为评测使用 NVIDIA SkillEvaluator：

```bash
bash scripts/run_skill_eval.sh shared_skills/<skill-name> --agent-eval --tiers 1,2,3 ...
```

---

## 8. 部署

生产部署已统一到 **DGX Spark 单机**，部署清单与脚本见仓库根目录：

- `../docs/DGX-SPARK-模型部署方案.md`（**模型栈选型演进、性能基准、内存账**）
- `../docs/DGX-SPARK-部署清单.md`（应用迁移步骤、进度、风险）
- `../deploy/dgx/`（灰度路由、SSH 反向隧道、miniapp-proxy）
- `../deploy/gpu-services/`（主模型 / Embedding / Reranker / MinerU 的 systemd 单元与服务包装器）

本仓库 `infra/` 提供 systemd 服务与定时任务模板：

| 单元 | 用途 |
|------|------|
| `chroma-server.service` | ChromaDB HTTP 服务 |
| `hermes-gateway@.service` | Hermes 网关模板（实际实例 `hermes-gateway@jwc-assistant`） |
| `backup-kb.service/.timer` | 知识库本地备份（每日 02:00） |
| `audit-cleanup.service/.timer` | 审计日志清理（每日 00:00） |
| `temp-cleanup.service/.timer` | 临时文件清理（每周六 03:00） |
| `jxtz-sync.service/.timer` | 教务通知同步（每日 04:00） |

---

## 9. 运维操作

### 9.1 服务管理

```bash
sudo systemctl status chroma-server hermes-gateway@jwc-assistant
sudo systemctl restart hermes-gateway@jwc-assistant
sudo journalctl -u hermes-gateway@jwc-assistant -f
```

### 9.2 知识库管理

```bash
# 单文件入库
python scripts/admin_cli.py ingest /path/to/file.docx --scope global

# 批量入库——文件通知
python scripts/batch_ingest_benke.py [--reset]

# 批量入库——教学通知
python scripts/batch_ingest_jxtz.py [--reset]

# 按用户清理（向量 + 配额 + 版本元数据）
python scripts/admin_cli.py purge <user_id>

# 角色管理
python scripts/admin_cli.py roles set --user-id <id> --role <role>

# 检索质量评估
python scripts/eval_ragas.py

# ChromaDB 健康检查
python scripts/admin_cli.py health
```

### 9.3 教务通知每日同步

`scripts/sync_jxtz.py` 每日从学校教务处官网抓取最新教学通知，URL 比对后增量入库到
ChromaDB 的 `global` scope（`source="jxtz"`）：

```bash
python scripts/sync_jxtz.py          # 手动触发
```

流程：WAF 挑战求解 → 抓列表 → 与本地 `data/jxtz_notices.jsonl` 全量比对 → 新通知抓详情 →
`orch.ingest_file()` → 运行记录写 `data/jxtz_sync_runs.jsonl`。临时失败不写 JSONL，下次自动重试。

### 9.4 备份

```bash
bash scripts/backup_kb.sh            # 知识库（ChromaDB + SQLite + roles）
bash scripts/backup_audit_db.sh      # 审计数据库
```

### 9.5 审计与安全

```bash
python scripts/admin_cli.py audit --limit 20
python scripts/admin_cli.py audit --stats
bash scripts/cve_check.sh            # 依赖漏洞扫描
```

---

## 10. 文档索引

`docs/` 下的中文文档按目录规范组织：

| 目录 | 内容 |
|------|------|
| `docs/01-architecture/` | 系统架构、RAG 知识库设计、数据层设计 |
| `docs/02-features/` | 各功能设计（学业预警、培养方案解读、多路径规划、文件中心等） |
| `docs/03-issues/` | 问题排查记录 |
| `docs/04-security/` | 权限与安全设计 |
| `docs/05-notes/` | 专项笔记（如教务通知同步管理） |
| `docs/README.md` | 文档规范 |

重点文档：

- [`docs/01-architecture/som-rag-knowledge-base-design.md`](docs/01-architecture/som-rag-knowledge-base-design.md) —— RAG 知识库实现详解；
- [`docs/05-notes/code-standards/README.md`](docs/05-notes/code-standards/README.md) —— 代码规范（clean code / 测试 / 安全，ruff 闸门与豁免依据）；
- [`docs/05-notes/code-standards/audit-2026-09-26.md`](docs/05-notes/code-standards/audit-2026-09-26.md) —— clean-code 审计报告；
- [`docs/02-features/008-test-suite-completion.md`](docs/02-features/008-test-suite-completion.md) —— 测试套件设计（单元 / 集成 / 安全矩阵）；
- [`docs/02-features/009-test-data-layering.md`](docs/02-features/009-test-data-layering.md) —— 测试数据分层与 PII 卫生；
- [`shared_skills/README.md`](shared_skills/README.md) —— Skill 开发与评测指南；
- [`shared_skills/EVAL.md`](shared_skills/EVAL.md) —— Skill 行为评测手册（NVIDIA SkillEvaluator）。

---

## 11. 常见问题

### 11.1 `ModuleNotFoundError: No module named '_sqlite3'`

系统 Python 缺少 `_sqlite3` C 扩展，安装 `pysqlite3-binary` 后由代码自动回退：

```bash
pip install pysqlite3-binary
```

### 11.2 ChromaDB 导入报错 / 版本不一致

确保版本与生产一致：

```bash
pip install chromadb==1.5.9
```

`agent4som` 与 Hermes 两个 venv 必须使用相同版本。

### 11.3 检索报 `Error finding id`

通常是绕过 chroma-server 用 `PersistentClient` 直接写 `data/chroma/`，导致 HNSW 索引与元数据不一致。
**所有写入必须走 chroma-server HTTP API**；如已损坏需全量清空重建。

### 11.4 测试报找不到模块

确认 `~/.hermes/hermes-agent` 存在（`tests/conftest.py` 依赖它加入 `sys.path`）。

### 11.5 管理员同步知识库后检索不到新内容

1. 确认文件扩展名受支持（`.pdf`、`.docx`、`.txt`、`.md`、`.xlsx`、`.xls`、`.csv` 等）；
2. 确认写入走的是 HTTP 模式；
3. 查看摄入日志：`journalctl -u hermes-gateway@jwc-assistant | grep -i "ingest\|kb"`。

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
