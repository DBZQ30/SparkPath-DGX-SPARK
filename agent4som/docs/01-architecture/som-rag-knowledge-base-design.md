# SOM RAG 知识库 — 实现详解

> 版本: 2.1 · 日期: 2026-09-26 · 状态: 已完成
> 仓库: `SparkPath-DGX-SPARK`（多子项目合并仓库；本文档位于 `agent4som/docs/01-architecture/`）
> 代码引用以文件/函数为单位，不标注行号（避免随代码演进漂移）。

## 目录

1. [概述](#1-概述)
2. [架构总览](#2-架构总览)
3. [数据模型](#3-数据模型)
4. [存储层](#4-存储层)
5. [入库管线](#5-入库管线)
6. [检索管线](#6-检索管线)
7. [权限控制](#7-权限控制)
8. [外部服务依赖](#8-外部服务依赖)
9. [运维操作](#9-运维操作)
10. [集成指南](#10-集成指南)
11. [配置参考](#11-配置参考)
12. [模块索引](#12-模块索引)

---

## 1. 概述

SOM RAG 知识库是本科新生学业规划智能助手的核心数据面，提供统一的文档入库、向量检索、权限控制能力，通过 scope 机制实现数据隔离。

### 1.1 核心能力

| 能力 | 说明 |
|------|------|
| 多格式文档解析 | PDF、DOCX、DOC、PPTX、XLSX、CSV、TXT、JPG、PNG 等 |
| 结构感知切分 | 按标题层级/段落边界/表格块切分，禁止固定 token 切片 |
| 向量检索 | Qwen3-Embedding-0.6B (1024维) + ChromaDB HTTP 服务 |
| 混合检索 | 向量搜索 + BM25 关键词搜索 (0.3 权重) + Qwen3 Reranker 精排 |
| 三层权限隔离 | global（公共）/ teachers（教师）/ users/{id}（个人） |
| 内容去重 | SHA-256 内容哈希 + 文件字节哈希双重去重 |
| 配额管理 | 文件大小(≤100MB) + 每日上传 + 总文件数 + 每分钟速率限制 |
| 审计追踪 | 读写操作均记录审计日志，SHA-256 链式哈希防篡改 |

### 1.2 关键设计决策

- **ChromaDB HTTP 模式**：所有读写统一走 `chroma-server` HTTP API（`127.0.0.1:8007`），禁止 `PersistentClient` 直接操作 SQLite（否则 HNSW 向量索引与元数据索引不一致，`where` 查询报 `Error finding id`）。
  > 依据：`knowledge_base/repository/chroma_repository.py`（`get_chroma_client()`），`CLAUDE.md` 前置规则 1。
- **单集合多 scope**：本科新生学业规划智能助手共享同一个 `raw_nodes` 集合，scope 作为 metadata 字段进行过滤。也支持 scope 级集合隔离（`_USE_SCOPE_COLLECTIONS`），当前默认关闭。
  > 依据：`knowledge_base/repository/chroma_repository.py`（`NODES_COLLECTION = "raw_nodes"`、`_USE_SCOPE_COLLECTIONS = False`）。
- **确定性 chunk ID**：`chk_{hash_prefix}_{index:04d}` 格式，同一文档始终生成相同 ID，使 `col.upsert()` 天然支持幂等写入。
  > 依据：`knowledge_base/models/schemas.py`（`make_chunk_id()`）。
- **ACL 单一真相源**：所有权限逻辑集中于 `knowledge_base/retrieval/acl_filter.py`，Gateway hooks 和 tools 均委托给该模块，禁止内联权限检查。
  > 依据：`knowledge_base/retrieval/acl_filter.py`（模块文档字符串）。

---

## 2. 架构总览

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│  Hermes Agent 层                                         │
│  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │ knowledge_search  │  │ knowledge_ingest             │  │
│  │ (query_kb.py)     │  │ (knowledge_ingest.py)        │  │
│  └────────┬─────────┘  └────────────┬─────────────────┘  │
│           │                         │                     │
├───────────┼─────────────────────────┼─────────────────────┤
│  检索层   │                         │  入库层              │
│  ┌────────┴─────────┐  ┌────────────┴─────────────────┐  │
│  │ ACLFilter        │  │ IngestionOrchestrator        │  │
│  │ Qwen3Reranker    │  │ ExtractorRouter              │  │
│  │ BM25Index        │  │ QuotaManager                 │  │
│  │ ContextStitcher  │  │ VersionManager               │  │
│  │ ResponseVerifier │  │ SemanticSplitter             │  │
│  └────────┬─────────┘  └────────────┬─────────────────┘  │
│           │                         │                     │
├───────────┼─────────────────────────┼─────────────────────┤
│  存储层   │                         │                     │
│  ┌────────┴─────────────────────────┴─────────────────┐  │
│  │              ChromaRepository (单例)                │  │
│  │  ┌──────────────────┐  ┌─────────────────────────┐ │  │
│  │  │ raw_nodes (向量)  │  │ SqliteStore (配额/版本)  │ │  │
│  │  └──────────────────┘  └─────────────────────────┘ │  │
│  └──────────────────────────┬─────────────────────────┘  │
│                             │                             │
├─────────────────────────────┼─────────────────────────────┤
│  外部服务                    │                             │
│  ┌──────────┐ ┌──────────┐ │ ┌──────────┐ ┌──────────┐   │
│  │ ChromaDB │ │ Embedding│ │ │ Reranker │ │ LLM+VL   │   │
│  │ :8007    │ │ :8001    │ │ │ :8002    │ │ :8000    │   │
│  └──────────┘ └──────────┘ │ └──────────┘ └──────────┘   │
└─────────────────────────────┴─────────────────────────────┘
```

### 2.2 模块组织

```
knowledge_base/
├── auth/              # 角色解析 (role_store.py, guards.py)
├── core/              # 基础组件 (quota, version, audit, sqlite_store, query_context, display_names)
├── ingestion/         # 入库管线 (orchestrator, parsers, semantic_splitter)
├── llamaindex/        # LlamaIndex 适配 (readers, pipeline, metadata, vector_store)
├── models/            # Pydantic 数据模型 (schemas.py)
├── pipeline/          # 结构化抽取 (router, extractor, state_machine)
├── repository/        # 存储层 (chroma_repository, embedding_providers, interfaces, sqlite_metadata, chroma_metadata_dao)
├── retrieval/         # 检索层 (acl_filter, bm25_search, qwen3_reranker, bge_reranker, reranker, context_stitcher, response_verifier, conflict_resolver, normalize_for_conflict_resolver)
├── scripts/           # 运维脚本 (purge_pipeline, sync_kb, role_batch)
├── utils/             # 工具 (hashing, service_manager)
└── bootstrap.py       # 组件工厂
```

> 依据：`knowledge_base/` 目录结构，`knowledge_base/__init__.py`。

---

## 3. 数据模型

### 3.1 RawIndexNode（文档节点）

每个入库文档被切分为多个 `RawIndexNode`，存储在 ChromaDB `raw_nodes` 集合中。节点包含原文内容和完整的溯源元数据。

> 依据：`knowledge_base/models/schemas.py`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `node_id` | `str` | 确定性 chunk ID，格式 `chk_{hash}_{idx}`（见 `make_chunk_id()`） |
| `scope` | `str` | ACL 作用域（`global` / `teachers` / `users/{id}`） |
| `source_tier` | `str` | 冲突裁决层级（`global` → `"global"`，`teachers` → `"assistant"`，`users/*` → `"user"`） |
| `source` | `str` | 数据来源 — `"file"`（本科管理文件库）或 `"jxtz"`（教学通知） |
| `source_file` | `str` | 原始文件名（含类别标签，如 `[学籍管理] 文件名`） |
| `source_path` | `str` | 原始文件完整路径 |
| `doc_version` | `str \| None` | 文档年份（从文件名提取，如 `2025`） |
| `visibility_tag` | `str` | 可见性标签（默认 `"public"`），**注意：当前未接入 ACL 过滤** |
| `content` | `str` | 节点正文内容 |
| `section_title` | `str \| None` | 所在章节标题 |
| `section_path` | `str \| None` | 章节路径（如 `第3章>3.2 先修要求`） |
| `page_start` / `page_end` | `int \| None` | 页码范围（PDF/DOCX） |
| `sheet_name` | `str \| None` | 工作表名（Excel） |
| `row_start` / `row_end` | `int \| None` | 行范围（Excel） |
| `col_start` / `col_end` | `int \| None` | 列范围（Excel） |
| `anchor_text` | `str` | 定位锚文本（用于核验） |
| `anchor_locator` | `str` | 结构化定位器（页码/段落路径/单元格地址） |
| `source_hash` | `str` | 源文档 SHA-256 内容哈希 |
| `parser_version` | `str` | 解析器版本（`"llamaindex-v1"` 或 `"llamaindex-v1+semantic-rechunk"`） |
| `prev_node_id` / `next_node_id` | `str \| None` | 前后相邻节点 ID（用于上下文拼接） |
| `parse_confidence` | `float` | 解析置信度（默认 1.0） |
| `distance` | `float` | 余弦距离（0=完全相同, 2=完全相反），**不持久化，仅查询时填充** |

**ChromaDB metadata 序列化**：`to_chroma_metadata()` 方法排除 `content` 和 `distance` 字段，其余字段的值转为基本类型（str/int/float/bool）以确保 ChromaDB 兼容。

> 依据：`knowledge_base/models/schemas.py`。

**确定性 chunk ID 生成规则**：

```python
# schemas.py
structural_key = f"{content_hash}:{total_chunks}" if not scope else f"{content_hash}:{scope}:{total_chunks}"
hash_prefix = hashlib.sha256(structural_key.encode()).hexdigest()[:16]
chunk_id = f"chk_{hash_prefix}_{chunk_index:04d}"
```

scope **仅在非空时**纳入哈希。当 scope 为空时（向后兼容），哈希输入为 `content_hash:total_chunks`。非空 scope 纳入哈希确保同一文档入库到不同 scope 时产生不同的 chunk ID，避免 `col.upsert()` 静默覆盖。

> 依据：`knowledge_base/models/schemas.py`。

### 3.2 BaseFact（结构化事实）

从文档中提取的结构化知识事实，存储在 ChromaDB `normalized_facts` 集合中。当前主要用于课程要求事实。

> 依据：`knowledge_base/models/schemas.py`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `fact_id` | `str` | 事实唯一标识 |
| `fact_type` | `str` | 事实类型（如 `"course_requirement"`） |
| `assistant_id` | `str` | 所属助手 |
| `program` | `str` | 专业 |
| `cohort_year` | `str` | 年级/入学年度 |
| `payload` | `dict[str, Any]` | 结构化规则内容 |
| `payload_schema_version` | `str` | 事实结构版本 |
| `fact_key` | `str` | 规范化冲突键 |
| `needs_admin_confirmation` | `bool` | 是否需要管理员确认（默认 `False`） |
| `confidence_score` | `float` | 置信度（默认 1.0） |
| `source_node_ids` | `list[str]` | 可追溯回 raw_nodes 的源节点 ID |
| `status` | `FactStatus` | 状态（`PENDING_REVIEW` / `APPROVED` / `REJECTED` / `EXPIRED`） |
| `has_user_conflict_warning` | `bool` | 系统事实覆盖用户事实时置 `True`（默认 `False`，由 `ConflictResolver` 设置） |

**FactStatus 状态机**：

```
PENDING_REVIEW → APPROVED  (管理员确认通过)
PENDING_REVIEW → REJECTED  (管理员拒绝)
PENDING_REVIEW → EXPIRED   (卡片过期)
EXPIRED        → APPROVED  (重新确认)
EXPIRED        → REJECTED  (重新拒绝)
```

**注意**：`APPROVED` 和 `REJECTED` 是终态，无法再从这两个状态转换到其他状态。

> 依据：`knowledge_base/models/schemas.py`（`FactStatus`），`knowledge_base/pipeline/state_machine.py`（`AdminReviewStateMachine.transitions` 字典）。

**CourseRequirementFact** 是 `BaseFact` 的具类型化子类，`payload` 固定为 `CourseRequirementPayload`（包含 `course_code`、`requirement_group`、`term_scope`）。

> 依据：`knowledge_base/models/schemas.py`。

**Schema 校验失败降级**：当 `CourseRequirementFact` 构造失败时（`ValidationError`），`process_extraction()` 自动降级为通用 `BaseFact`，标记 `needs_admin_confirmation=True` 且状态为 `PENDING_REVIEW`。不会丢弃数据。

> 依据：`knowledge_base/pipeline/extractor.py`。

---

## 4. 存储层

### 4.1 ChromaRepository（核心存储）

`ChromaRepository` 是向量存储和检索的统一入口，实现 `KnowledgeBaseRepository` 接口。采用单例模式（`init_instance()` / `instance()`）。

> 依据：`knowledge_base/repository/chroma_repository.py`，`knowledge_base/repository/interfaces.py`。

**两种运行模式**：

| 模式 | 触发条件 | 客户端类型 | 适用场景 |
|------|---------|-----------|---------|
| HTTP 模式 | 设置了 `CHROMA_HOST` 和 `CHROMA_PORT` | `HttpClient` | 生产环境 |
| 嵌入式模式 | 未设置 HTTP 环境变量 | `PersistentClient` | 本地开发 |

- **HTTP 模式（生产）**：`get_chroma_client()` 优先连接 chroma-server HTTP 服务。若不可达，自动调用 `ensure_chroma_server()` 启动服务并重试。若 HTTP 已明确配置但连接失败，**绝不**回退到嵌入式模式，而是抛出 `RuntimeError`。
  > 依据：`knowledge_base/repository/chroma_repository.py`。
- **Token 认证**：通过 `CHROMA_AUTH_TOKEN` 环境变量启用。客户端传递 `Settings(chroma_client_auth_provider=..., chroma_client_auth_credentials=...)`。
  > 依据：`knowledge_base/repository/chroma_repository.py`。

**集合**：

| 集合名 | 用途 | 索引方式 |
|--------|------|---------|
| `raw_nodes` | 文档节点（向量检索 + 元数据过滤） | `ids` + `documents` + `embeddings` + `metadatas` |
| `normalized_facts` | 结构化事实（键值查找） | `ids` + `documents`(JSON) + `metadatas` |

> 依据：`knowledge_base/repository/chroma_repository.py`。

**核心方法**：

| 方法 | 说明 |
|------|------|
| `store_nodes(scope, nodes)` | 批量 upsert，每批 50 个节点（`CHROMA_BATCH_SIZE` 控制） |
| `search_nodes(scopes, query_embedding, top_k)` | 向量搜索 + scope 过滤（`where={"scope": {"$in": scopes}}`） |
| `delete_nodes(scope, source_file)` | 按 scope + 可选 source_file 删除 |
| `count_nodes(scope, source_file)` | 计数（上限 5000），**非接口方法，为 ChromaRepository 特有** |
| `store_facts(scope, facts)` | 存储结构化事实（JSON 格式） |
| `get_facts_by_keys(scopes, fact_keys)` | 按 fact_key 批量查询 |
| `get_all_user_scopes()` | 枚举所有 `users/*` scope（用于 admin 跨用户审计） |
| `purge_scope(scope)` | 清除 scope 下所有节点和事实 |

**注意**：`KnowledgeBaseRepository` 接口（`interfaces.py`）定义 12 个抽象方法，不含 `count_nodes`。

> 依据：`knowledge_base/repository/chroma_repository.py`。

**Scope 级集合隔离**（可选特性）：

`_USE_SCOPE_COLLECTIONS` 默认为 `False`。当启用时，每个 scope 获得独立的 ChromaDB 集合（`scope_global`、`scope_teachers`、`scope_users_X`），提供存储级别的隔离。当前生产环境使用单集合 + metadata 过滤模式。

> 依据：`knowledge_base/repository/chroma_repository.py`（`_USE_SCOPE_COLLECTIONS = False`、`_scope_collection()`）。

### 4.2 SqliteStore（配额与版本元数据）

SQLite 数据库（`data/quota.db`）存储文件版本元数据、上传计数和速率限制日志。WAL 模式，支持并发读写。

> 依据：`knowledge_base/core/sqlite_store.py`。

**数据表**：

```sql
-- 文件版本元数据（用于去重和版本管理）
CREATE TABLE file_metadata (
    user_id     TEXT NOT NULL,
    filename    TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',   -- SHA-256 文本哈希
    file_hash   TEXT NOT NULL DEFAULT '',    -- SHA-256 原始字节哈希
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, filename, scope)
);

-- 每日上传统计
CREATE TABLE daily_uploads (
    user_id     TEXT NOT NULL,
    upload_date TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, upload_date)
);

-- 上传日志（用于每分钟速率限制，自动清理1小时前数据）
CREATE TABLE upload_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_upload_log_user_time ON upload_log(user_id, uploaded_at);

-- 文件字节哈希索引
CREATE INDEX idx_file_metadata_file_hash ON file_metadata(file_hash);
```

> 依据：`knowledge_base/core/sqlite_store.py`。

**关键行为**：

- `set_file_metadata()`：对新文件自动递增每日上传计数；替换已有文件（同 user+filename+scope）不重复计数。
- `record_upload()`：每次上传记录到 `upload_log`，同时清理 1 小时前的旧记录。
- `get_file_metadata_by_hash()`：按文件字节哈希查询元数据，用于早期去重检查（避免 MinerU 不稳定的情况下重复入库）。对应的 `ChromaRepository.file_hash_exists()`（`chroma_repository.py`）是对此方法的封装。

> 依据：`knowledge_base/core/sqlite_store.py`（`get_file_metadata_by_hash()`），`knowledge_base/repository/chroma_repository.py`（`file_hash_exists()`）。

### 4.3 嵌入函数

三层嵌入函数链：

```
OpenAICompatibleEmbeddingFunction (Qwen3-Embedding-0.6B, 1024维)
    ↓ 失败时
BuiltinEmbeddingFunction (ONNX all-MiniLM-L6-v2, 384维)
```

`FallbackEmbeddingFunction` 包装主备两个嵌入函数，每次调用验证输出维度一致性，防止主备模型维度不同导致静默数据损坏。

> 依据：`knowledge_base/repository/embedding_providers.py`。

**注意**：`build_embedding_function()`（`bootstrap.py`）**不包含** Fallback 包装——仅返回 `OpenAICompatibleEmbeddingFunction`。Fallback 包装仅在 Gateway 的 `kb_init/handler.py` 中使用。

> 依据：`knowledge_base/bootstrap.py`（`build_embedding_function()`），`gateway/hooks/kb_init/handler.py`（Gateway 侧的 `build_embedding_function()`）。

---

## 5. 入库管线

### 5.1 流程概览

```
用户上传文件
    │
    ▼
ExtractorRouter.route()          ← 文件类型检测（扩展名 + magic bytes）
    │
    ▼
QuotaManager.check_quota()       ← 大小/速率/日限额检查（admin/owner 免限）
    │
    ▼
SHA-256 文件字节哈希              ← 早期去重（绕过 MinerU 不稳定）
    │
    ▼
read_file()                      ← 内容提取
    │                               PDF → MinerU → pdfminer
    │                               图片 → MinerU → VL 模型
    │                               DOCX → python-docx
    │                               PPTX → python-pptx
    │                               DOC → antiword CLI
    │                               TXT/MD → 纯文本读取
    ▼
SHA-256 内容哈希                  ← 文本内容哈希
    │
    ▼
VersionManager.determine_ingest_action()  ← 去重决策 (NEW/SKIP/REPLACE)
    │
    ▼
LlamaIndex 解析 / 语义切分         ← 结构化解析 → 节点生成
    │
    ▼
_extract_document_metadata()      ← 从文件名提取年份/类别/doc_type
    │
    ▼
_track_metadata()                 ← 先写 SQLite 版本记录（失败时按孤儿元数据清理）
    │
    ▼
store_nodes()                     ← ChromaDB upsert (批量 50)
```

> 依据：`knowledge_base/ingestion/orchestrator.py`（`ingest_file()` 方法）。

### 5.2 IngestionOrchestrator

`IngestionOrchestrator` 是入库管线的编排器。构造函数注入四个依赖：

```python
IngestionOrchestrator(
    repo,              # KnowledgeBaseRepository (ChromaRepository)
    quota_manager,     # QuotaManager
    version_manager,   # VersionManager
    router,            # ExtractorRouter
    chunk_size=512,    # 默认分块大小
)
```

> 依据：`knowledge_base/ingestion/orchestrator.py`。

**核心方法 `ingest_file()`** 返回 `IngestResult`（dataclass），**绝不抛异常**——错误均编码在 `result.status` 中。

> 依据：`knowledge_base/ingestion/orchestrator.py`（`IngestResult`；`ingest_file()` 文档注释 "Never raises"）。

**IngestionStatus 枚举**：

| 状态 | 含义 |
|------|------|
| `INGESTED` | 新文件入库成功 |
| `SKIPPED` | 内容未变化，跳过 |
| `REPLACED` | 旧版本被替换 |
| `QUOTA_EXCEEDED` | 超出配额限制 |
| `UNSUPPORTED_TYPE` | 不支持的文件类型 |
| `FILE_NOT_FOUND` | 文件不存在 |
| `ERROR` | 入库过程出错 |

> 依据：`knowledge_base/ingestion/orchestrator.py`。

### 5.3 文件类型路由

`ExtractorRouter` 基于文件扩展名检测类型，并支持 magic bytes 检测（处理缓存将图片重命名为 `.bin` 的情况）。

> 依据：`knowledge_base/pipeline/router.py`。

**检测逻辑**：

- `.pdf` → `DOCUMENT`
- `.docx`, `.doc` → `DOCUMENT`
- `.pptx` → `DOCUMENT`
- `.txt`, `.md` → `DOCUMENT`
- `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.tif` → `DOCUMENT`（`doc_extensions` 直接映射）
- `.xlsx`, `.xls`, `.csv` → `STRUCTURED_TABLE`（`table_extensions` 直接映射）
- `.gif`, `.webp` → 不在直接映射中，通过 magic bytes 检测识别（`detect_image_extension()`）
- 其他 → 尝试 magic bytes 检测图片格式 → 否则 `UNKNOWN`
- `.bin` 文件 → 通过 magic bytes 检测真实图片格式（PNG/JPEG/GIF/WebP），复制为正确扩展名的文件

> 依据：`knowledge_base/pipeline/router.py`（`route()`），`knowledge_base/ingestion/orchestrator.py`（`.bin` → 正确扩展名的处理）。

**支持的图片 magic bytes**：

| 扩展名 | magic bytes | 字节数 |
|--------|------------|--------|
| `.png` | `\x89PNG\r\n\x1a\n` | 8 |
| `.jpg` | `\xff\xd8\xff` | 3 |
| `.gif` | `GIF87a` 或 `GIF89a` | 6 |
| `.webp` | `RIFF`（需进一步检查偏移 8 处的 `WEBP` 子格式） | 4+ |

> 依据：`knowledge_base/pipeline/router.py`（`_IMAGE_MAGIC` 字典）。

### 5.4 文档内容提取（read_file）

`read_file()` 是多格式文档解析的入口，位于 `knowledge_base/ingestion/parsers.py`；`orchestrator.py` 通过 `from knowledge_base.ingestion.parsers import read_file` 调用它。PDF 和图片采用不同的回退策略：

```
PDF
  ├── 1. MinerU API (主路径) → Markdown 输出
  │      ├── 同步 API: 直接返回 results.md_content
  │      └── 异步 API: submit → poll → fetch result
  │            409 冲突: 指数退避重试 (最多 MINERU_RETRIES 次, 最大 MINERU_RETRY_MAX_BACKOFF 秒)
  ├── 2. pdfminer 回退 → 纯文本提取
  └── 3. VL 逐页回退 (_parse_pdf_with_vl) → 每页转图后交 VL 模型，受 PDF_VL_MAX_PAGES 限制

图片 (JPG/PNG/BMP/TIFF)
  ├── 1. MinerU API (主路径) → Markdown 输出
  └── 2. VL 模型回退 → qwen3.8-27b 图片描述
       └── >5MB 图片自动缩放到 4096px

DOCX
  ├── 1. python-docx → 段落 + 表格
  └── 2. zipfile 直接读 XML (回退)

PPTX
  └── 1. python-pptx → 幻灯片文本 + 表格

DOC
  ├── 1. antiword CLI → 文本提取
  └── 2. 纯文本读取 (errors=replace)

TXT/MD
  └── 1. UTF-8 读取 (errors=replace)
```

> 依据：`knowledge_base/ingestion/parsers.py`（`read_file()`、`_parse_with_mineru()`、`_parse_with_vl()`、`_parse_pdf_with_vl()`）。

**MinerU / VL 配置**（环境变量，`parsers.py` 模块级读取）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINERU_URL` | 空（未配置则跳过） | MinerU 服务地址 |
| `MINERU_POLL_INTERVAL` | 2 | 轮询间隔（秒） |
| `MINERU_POLL_TIMEOUT` | 300 | 轮询超时（秒） |
| `MINERU_RETRIES` | 10 | 409 冲突重试次数 |
| `MINERU_RETRY_MAX_BACKOFF` | 120 | 重试最大退避（秒） |
| `MINERU_CONNECT_RETRIES` | 3 | 连接失败/超时重试次数 |
| `MINERU_API_KEY` | 同 `QWEN_API_KEY` | API 认证 |
| `PDF_VL_MAX_PAGES` | 10 | 扫描版 PDF 走 VL 逐页回退时的最大页数（0=全部） |
| `VL_ENABLE_THINKING` | 0（关闭） | 是否保留 VL 推理链；OCR 场景关闭以避免推理吃满 `VL_MAX_TOKENS` 导致 `content=None` |

> 依据：`knowledge_base/ingestion/parsers.py`。

### 5.5 文档切分

**LlamaIndex 管线**（默认，`KB_USE_LEGACY_PARSER != "true"`）：

1. 根据文件类型选择合适的 Reader：
   - XLSX/XLS → `StructuredExcelReader`（合并单元格展开、多级表头层级化）
   - DOC → 跳过 LlamaIndex（使用 antiword 预提取的内容 + legacy splitter）
   - 其他 → `SimpleDirectoryReader`（LlamaIndex 自动检测）
2. `IngestionPipeline`：`SentenceSplitter`(chunk_size=512, chunk_overlap=32) → `RawIndexNodeMetadataExtractor`
3. `node_to_raw_index_node()` 转换为 `RawIndexNode`
4. 分配确定性 chunk ID（`make_chunk_id()`）
5. 链接相邻节点（`prev_node_id` / `next_node_id`）
6. 质量门控：当 LlamaIndex 和 `read_file()` 内容长度差异 >3× 时，选择内容更丰富的一方
7. 重新切分门控：当 legacy splitter 产生的节点数 > LlamaIndex 的 1.2× 时，采用 legacy splitter 结果（保留 LlamaIndex 元数据）

> 依据：`knowledge_base/ingestion/orchestrator.py`（`_parse_with_llamaindex()`），`knowledge_base/llamaindex/ingestion_pipeline.py`，`knowledge_base/llamaindex/readers.py`。

**Legacy 管线**（`KB_USE_LEGACY_PARSER=true` 或 LlamaIndex 失败回退）：

`chunk_document()` 实现结构感知切分：

1. 检测标题（Markdown 标题、中文编号"一、二、"、"第X章"、"第X条"、英文 Chapter/Section/Article）
2. 按标题边界切分为 section
3. 维护层级路径（如 `第3章>3.2 先修要求`）
4. 超长 section 回退到行级切分（`max_chunk_lines=200`, `max_chunk_chars=1500`）
5. 每个 chunk 生成 Contextual Retrieval 前缀（文档标题 + 章节路径）
6. 确定 `source_tier`（`global` → `"global"`，`teachers` → `"assistant"`，`users/*` → `"user"`）

> 依据：`knowledge_base/ingestion/semantic_splitter.py`（`_tier_from_scope()`）。

### 5.6 去重机制

三层去重：

| 层级 | 机制 | 触发时机 |
|------|------|---------|
| 1. 文件字节哈希 | `SHA-256(raw bytes)` 在 scope 内查重 | 文件内容解析前（`ingest_file()` 第 3b 步） |
| 2. 内容哈希 | `SHA-256(extracted text)` 比对 | 文本提取后（`VersionManager.determine_ingest_action()`） |
| 3. ChromaDB upsert | 确定性 chunk ID 自然覆盖 | `store_nodes()` 写入时 |

> 依据：`knowledge_base/ingestion/orchestrator.py`（步骤 3b-3c），`knowledge_base/core/version_manager.py`（`VersionManager`），`knowledge_base/models/schemas.py`（确定性 chunk ID）。

**共享 scope 跨用户去重**：`global` 和 `teachers` 属于共享 scope（`_SHARED_SCOPES`），`VersionManager` 检查时不仅查当前用户的元数据，还查 `get_file_metadata_any_user()`——防止同一文件被不同用户重复入库。

> 依据：`knowledge_base/core/version_manager.py`（`_SHARED_SCOPES = frozenset({"global", "teachers"})`、跨用户查询逻辑）。

### 5.7 配额管理

`QuotaManager` 实施四层限制：

| 限制项 | 默认值 | 说明 |
|--------|--------|------|
| 文件大小 | 100 MB | 所有用户均受限（含 admin/owner） |
| 每分钟上传 | 3 次 | 普通用户（`RateLimitExceededError`） |
| 每日上传 | 30 次 | 普通用户（`DailyLimitExceededError`） |
| 总文件数 | 50 个 | 普通用户（`MaxFilesExceededError`） |

**admin/owner 特权**：`resolve_role()` 返回 `owner` 或 `admin` 的用户**仅受文件大小限制**，每日/总文件数/速率限制均豁免。

> 依据：`knowledge_base/core/quota_manager.py`，`knowledge_base/ingestion/orchestrator.py`（`is_privileged` 检查）。

### 5.8 文档元数据提取

`_extract_document_metadata()` 从文件名中提取以下信息：

| 字段 | 提取规则 | 示例 |
|------|---------|------|
| `doc_year` | 匹配 4 位年份数字（`20\d{2}`） | `2025` |
| `category` | 匹配中文方括号标签（`【...】`） | `"学籍管理"` |
| `doc_type` | 关键词匹配 | `"form"`（表/申请/审批）、`"notice"`（通知/公告）、`"regulation"`（办法/规定/细则/条例）、`"plan"`（方案/计划） |

提取的 `doc_year` 写入 `node.doc_version`，`category` 写入 `node.source_file` 前缀（格式 `[类别] 文件名`）。

> 依据：`knowledge_base/ingestion/orchestrator.py`。

---

## 6. 检索管线

### 6.1 完整流程

所有检索逻辑由 `~/.hermes/hermes-agent/tools/query_kb.py` 中的 `_retrieve()` 函数实现。评估脚本 `eval_ragas.py` 调用相同的 `_retrieve()`，保证生产和评估使用完全一致的代码路径。

```
用户问题
    │
    ▼
① ACL 过滤 ─────────────────────────────────────────────
    │  resolve_role() → ACLFilter.get_allowed_scopes()
    │  返回允许检索的 scope 列表
    ▼
② 查询扩展 (QE) ────────────────────────────────────────
    │  领域词典映射（13 条），如 "保研" → "推免 推免加分 B类 A类"
    ▼
③ Step-Back 改写 ───────────────────────────────────────
    │  LLM (qwen3.8-27b) 生成更宽泛的回退查询
    │  失败时静默回退（不影响主流程）
    ▼
④ 双路向量搜索 ─────────────────────────────────────────
    │  原查询 + Step-Back 查询各搜 top_k（默认 20，最小 20）
    │  Qwen3-Embedding-0.6B 向量化
    │  ChromaDB search_nodes(scopes, embedding, top_k=max(top_k, 20))
    ▼
⑤ 结果合并去重 ─────────────────────────────────────────
    │  按 node_id 去重
    ▼
⑥ BM25 混合搜索 ────────────────────────────────────────
    │  关键词 BM25 分数 × 0.3 + 向量相似度 × 0.7
    │  支持 DAT (Dynamic Alpha Tuning，默认关闭)
    ▼
⑦ Reranker 精排 ────────────────────────────────────────
    │  Qwen3Reranker (cross-encoder) 重排 top-8
    │  精排先于任何去重/截断，保证跨文档比较最优
    ▼
⑧ 截断 top-5 ───────────────────────────────────────────
    │  无 MMR——Reranker 已做跨编码比较，不需要按源文件去重
    ▼
⑨ 最终 ACL 校验 ────────────────────────────────────────
    │  防御性检查：移除 scope 不在 allowed_scopes 中的节点
    │  _search_pool 同步过滤，防止拼接引入越权节点
    ▼
⑩ build_passages (上下文拼接) ──────────────────────────
    │  每个命中 ±1 邻居扩展，合并同一文档的重叠窗口
    │  返回完整段落（2-5 个连贯段落给 LLM）
    ▼
⑪ 格式化输出 ───────────────────────────────────────────
    │  handle_knowledge_search() 格式化为编号引用块
    │  每条引用含文件名 + 章节 + 页码
    ▼
返回给 LLM
```

> 依据：`~/.hermes/hermes-agent/tools/query_kb.py`（`_retrieve()`）。

### 6.2 Step-Back 查询改写

使用 LLM（本地 `qwen3.8-27b` @ `127.0.0.1:8000`，配置为 `STEP_BACK_MODEL_URL`/`STEP_BACK_MODEL_NAME`）将具体问题改写为更宽泛的陈述式查询。

**系统提示词**要求：
- 回退查询比原问题更宽泛
- 保留关键实体（课程名、文件名、文号）
- 用陈述句或短语输出
- 只输出回退查询本身

**安全门控**：
- 若改写结果 < 3 字符或与原问题完全相同 → 丢弃
- 若 LLM 调用失败 → 静默回退（不影响主流程）

> 依据：`~/.hermes/hermes-agent/tools/query_kb.py`（配置变量、`_STEP_BACK_SYSTEM_PROMPT`、`_step_back_rewrite()`）。

**环境变量控制**：`STEP_BACK_ENABLED=true`（默认开启）。

### 6.3 领域词典查询扩展 (QE)

在 Step-Back 改写之前，对用户查询做领域词典映射：

| 用户用词 | 扩展词 |
|---------|--------|
| 分流 | 专业选择 志愿填报 |
| 大类招生 | 专业选择 专业分流 |
| 调课 | 课程调整 调停课 |
| 停课 | 调停课 课程调整 |
| 审批流程 | 申请表 签字 审批 |
| 导师制 | 本科生导师 导师管理办法 |
| 补考 | 补考 重修 结业换毕业 |
| 选修课 | 通识选修课 通识课 |
| 预科班 | 少数民族预科班 内地班 |
| 保研 | 推免 推免加分 B类 A类 竞赛加分 |
| 竞赛 | 推免加分 B类 A类 |
| 大赛 | 竞赛 B类 A类 推免加分 |
| 国际学生 | 汉语 中国概况 必修课 思政替代 军事课程 国防教育 |

共 13 条映射。注意 `竞赛` 和 `大赛` 是两个独立条目，扩展词不完全相同。

> 依据：`~/.hermes/hermes-agent/tools/query_kb.py`（`_QE_MAP`）。

### 6.4 Qwen3Reranker（精排）

`Qwen3Reranker` 调用 Qwen3 Reranker 的 `/v1/rerank` API 进行 cross-encoder 精排。

- **输入**：query + 候选文档列表（在检索管线中通过 `pool_size = min(8, len(nodes))` 截断为最多 8 个）
- **输出**：按 `relevance_score` 降序排列
- **默认 top_k**：3（`Qwen3Reranker.__init__` 参数默认值）
- **失败回退**：返回未排序的 top-k 结果
- **配置**：检索工具 `query_kb.py` 以 `BGE_RERANKER_URL` 构造 Reranker（未设置时按 `GPU_HOST` 推导为 `:8002/v1/rerank`）；`Qwen3Reranker` 类仅在未显式传入 `base_url` 时才会优先 `QWEN3_RERANKER_URL`。两者指向同一服务，`.env.example` 同时列出。

> 依据：`knowledge_base/retrieval/qwen3_reranker.py`（`top_k: int = 3`），`~/.hermes/hermes-agent/tools/query_kb.py`（管线中 `pool_size = min(8, len(nodes))`）。

### 6.5 BM25 混合搜索

`BM25Index` 是内存中的 BM25 关键词搜索引擎，作为向量搜索的补充。

**特性**：
- 线程安全（`threading.Lock`）
- 加载：两条路径——Gateway 启动时 `kb_init` hook 调用 `preload_bm25_index()` 在后台线程预热；若未预热，首次使用时 `get_bm25_index()` 懒加载（每进程一次）。加载上限由 `BM25_INDEX_LIMIT` 控制（默认 10000 条）
- 中文感知分词：保留中文连续字符，过滤纯标点符号 token
- BM25 超参数：k1=1.2, b=0.75（标准默认值）

> 依据：`knowledge_base/retrieval/bm25_search.py`。

**混合搜索 `hybrid_search()`**：

```
final_score = dense_score × (1 - bm25_weight) + normalised_bm25_score × bm25_weight
```

`bm25_weight` 默认为 0.3（即 70% 向量 + 30% BM25）。

**DAT (Dynamic Alpha Tuning)**：当 `DAT_ENABLED=true` 时（默认关闭），使用 step-back 模型（`STEP_BACK_MODEL_URL` / `STEP_BACK_MODEL_NAME`；未配置时自动关闭并回退固定 0.3 权重）评估每个检索器 top-1 结果的相关性，动态计算 α 权重。回退到固定 0.3 权重。

> 依据：`knowledge_base/retrieval/bm25_search.py`。

### 6.6 build_passages（上下文拼接）

`build_passages()` 实现业界标准的 "Sentence Window / Parent Document Retrieval" 模式：以 chunk 粒度检索，以段落粒度返回。

**流程**：

1. 对每个命中节点，沿 `prev_node_id`/`next_node_id` 链扩展 ±1 邻居
2. 若 prev/next 链接不存在，回退到按 `source_file` 中的位置查找邻居
3. 合并同一文档内的重叠窗口
4. 拼接邻居内容，元数据继承最佳命中节点
5. 按 Reranker 排序输出

> 依据：`knowledge_base/retrieval/context_stitcher.py`（`build_passages()`，辅助函数 `_expand()`、`_index_in_chain()`、`_merge_windows()`）。

### 6.7 检索失败处理

`_retrieve()` 返回字符串（而非节点列表）表示失败：

| 场景 | 返回内容 |
|------|---------|
| ChromaDB 单例未就绪 | `"知识库暂时不可用，请稍后再试。如持续异常请联系 IT 支持。"` |
| 向量化失败 | `"知识库暂时不可用（无法向量化查询），请稍后再试。"` |
| 检索异常 | `"知识库检索出错，请稍后再试。"` |
| 无结果 | `"暂未收录该内容（已检索 {scope列表}），请联系教务办公室。"` |

> 依据：`~/.hermes/hermes-agent/tools/query_kb.py`。

### 6.8 审计日志

`handle_knowledge_search()` 在每次检索后记录审计事件：

- **事件类型**：`"search"`
- **记录字段**：`user_id`、`role`、`query`、`scopes_allowed`、`scopes_hit`、`top_k`、`result_count`、`result_sources`、`latency_ms`

审计记录失败不影响检索结果（静默捕获异常）。

> 依据：`~/.hermes/hermes-agent/tools/query_kb.py`（`handle_knowledge_search()` 中的审计日志块）。

---

## 7. 权限控制

### 7.1 角色体系

五种角色，按优先级解析：

```
1. 环境变量匹配 → owner（HERMES_OWNER，兼容 WECOM_HOME_CHANNEL；企业微信已弃用，二者均为历史平台变量）
2. ~/.hermes/roles.json 显式映射 → 对应角色
3. 平台默认角色（`default_role_for()`）：miniapp 未登记用户 → guest；其它平台 → student
```

> 依据：`knowledge_base/auth/role_store.py`（`resolve_role()`）。

| 角色 | 典型用户 | 默认写入 scope |
|------|---------|---------------|
| `owner` | 系统所有者（`XiongWei`） | `global` |
| `admin` | 管理员 | `global` |
| `teacher` | 教师 | `teachers` |
| `student` | 学生 | `users/{user_id}` |
| `guest` | 未登记/未认证用户（miniapp 默认） | `users/{user_id}`（`derive_default_scope()` fallback；但 guest 无任何写权限） |

> 依据：`knowledge_base/retrieval/acl_filter.py`（`DEFAULT_SCOPE_BY_ROLE` 仅含 admin/owner/teacher，student/guest 由 `derive_default_scope()` 的 fallback 逻辑处理：`return f"users/{user_id}"`）。

### 7.2 三层防线

所有权限逻辑集中于 `knowledge_base/retrieval/acl_filter.py`（单一真相源）。

**读取权限矩阵**：

| 角色 | global | teachers | users/{self} | users/{他人} |
|------|--------|----------|--------------|-------------|
| admin/owner | ✅ | ✅ | ✅ | ✅（审计记录） |
| teacher | ✅ | ✅ | ✅ | ❌ |
| student | ✅ | ❌ | ✅ | ❌ |
| guest | ✅ | ❌ | ❌ | ❌ |

> 依据：`knowledge_base/retrieval/acl_filter.py`（文档注释中的权限矩阵、`get_allowed_scopes()`）。

**`get_allowed_scopes()` 实现**：

```python
def get_allowed_scopes(self, all_user_scopes=None):
    scopes = ["global"]
    if role in {"admin", "owner"}:
        scopes.append("teachers")
        scopes.append(f"users/{user_id}")
        scopes.extend(all_user_scopes or [])   # 所有 users/* scope
    elif role == "teacher":
        scopes.append("teachers")
        scopes.append(f"users/{user_id}")
    elif role == "student":
        scopes.append(f"users/{user_id}")
    # guest: global only
    return scopes
```

admin/owner 通过 `ChromaRepository.get_all_user_scopes()` 获取所有 `users/*` scope，实现跨用户审计。

> 依据：`knowledge_base/retrieval/acl_filter.py`，`knowledge_base/repository/chroma_repository.py`（`get_all_user_scopes()`）。

**写入权限矩阵**：

| 角色 | global | teachers | users/{self} |
|------|--------|----------|-------------|
| owner/admin | ✅ | ✅ | ✅（仅本人） |
| teacher | ❌ | ✅ | ✅（仅本人） |
| student | ❌ | ❌ | ✅（仅本人） |
| guest | ❌ | ❌ | ❌ |

**关键规则**：任何角色都不能写入他人的 `users/{id}` ——那是私有空间。admin/owner 可以读但不能写他人的个人知识库。

> 依据：`knowledge_base/retrieval/acl_filter.py`（`WRITE_PERMISSIONS`、`check_write_permission()`）。

**三层防线架构**：

| 层级 | 位置 | 功能 |
|------|------|------|
| 入口层（写入） | `knowledge_ingest` 工具 → `check_write_permission()` | 阻止越权写入 |
| 检索层（读取） | `_retrieve()` → `ACLFilter.get_allowed_scopes()` | ChromaDB `where={"scope": {"$in": allowed}}` |
| 输出层（防御） | `_retrieve()` 第 9 步 | 最终 ACL 校验，移除越权节点 |
| 输出层（可选审计） | `verify()`（`response_verifier.py` 模块级函数） | `RESPONSE_VERIFY_ENABLED=true` 时检测并脱敏输出中的裸 `users/*` scope 泄漏，默认关闭 |

> 依据：`knowledge_base/retrieval/response_verifier.py`（`verify()` 为独立的模块级函数，非类方法），`~/.hermes/hermes-agent/tools/query_kb.py`（检索后 ACL 校验）。

**注意**：生产 `_retrieve()` **不调用** `verify()`——它在检索后直接写审计事件；输出层的纵深防御由「第 9 步最终 ACL 校验」提供，`verify()` 仅在显式开启 `RESPONSE_VERIFY_ENABLED=true` 时作为可选补充（scope 泄漏检测/脱敏）。

**重要安全说明**：Hermes 传入的 role 不可信（永远是 `student`），tools 自行调用 `resolve_role()` 重新解析。

> 依据：`CLAUDE.md` "权限控制" 节，"关键设计决策" 第 4 条。

### 7.3 角色管理

**roles.json 格式**：

```json
{"miniapp": {"openid-xxx": "owner", "openid-yyy": "admin"}}
```

存储位置：`~/.hermes/roles.json`。

> 依据：`knowledge_base/auth/role_store.py`（`ROLES_PATH`）。

**读写特性**：

- 每次 `resolve_role()` 调用都重新读取文件——无需缓存失效机制
- 写入使用原子替换（写 `.tmp` → `replace`）
- role 变更在下一个 tool call 生效（当前单 VM 部署可接受）

> 依据：`knowledge_base/auth/role_store.py`。

**CLI 管理**：

```bash
python scripts/admin_cli.py roles set --platform miniapp --user-id <id> --role <role>
python scripts/admin_cli.py roles list --platform miniapp
python scripts/admin_cli.py roles import --platform miniapp --file roles.csv
```

只有 `<DEPLOY_USER>` 或 `root` 用户可以修改角色。所有角色变更均记录审计日志。

> 依据：`scripts/admin_cli.py`（`cmd_roles_set()`、`cmd_roles_list()`、`cmd_roles_import()` 实现）。

---

## 8. 外部服务依赖

所有服务 IP 通过 `.env` 管理，代码中无硬编码。认证统一：`Authorization: Bearer <QWEN_API_KEY>`。

| 服务 | 地址 | 用途 | 模型 |
|------|------|------|------|
| LLM+VL | `127.0.0.1:8000`（dgx） | 对话生成 + 图片理解回退 + Step-Back 改写 | `qwen3.8-27b`（vLLM） |
| Embedding | `<GPU_IP>:8001` | 文本向量化 (1024维) | `qwen3-embedding`（底层权重 Qwen3-Embedding-0.6B） |
| Reranker | `<GPU_IP>:8002` | 检索结果精排 (/v1/rerank) | `qwen3-reranker`（底层权重 Qwen3-Reranker-0.6B） |
| ChromaDB | `127.0.0.1:8007` | 向量存储 (本地 HTTP) | — |
| MinerU | `127.0.0.1:8005`（经 `accsvr-mineru-tunnel`） | PDF/图片解析 | dgx `:8005` 为 mineru-kit v4，`/file_parse` 协议不兼容（404）；隧道转发到 acc-svr 的旧版 `/file_parse` |

> 依据：`.env` / `.env.example`，`deploy/dgx/` 部署说明。

**主问答模型**：`agent4som-hermesagent/config.yaml` 默认使用**本地 `qwen3.8-27b`**（`provider: local`，vLLM `:8000`）；VL / Step-Back / 上下文压缩同样走本地 `:8000`。框架支持按需切换至云端 OpenAI 兼容模型（可选）。

> 依据：`agent4som-hermesagent/config.yaml`（`model`、`custom_providers`、`auxiliary`）。

**ChromaDB 连接策略**：

1. 检查 `CHROMA_HOST` / `CHROMA_PORT` 环境变量
2. 若已设置 → HTTP 模式：连接 chroma-server，不可用时自动启动
3. 若未设置 → 嵌入式模式：`PersistentClient`（仅本地开发）

> 依据：`knowledge_base/repository/chroma_repository.py`。

---

## 9. 运维操作

### 9.1 服务管理

```bash
# ChromaDB
sudo systemctl status chroma-server
sudo systemctl restart chroma-server
sudo journalctl -u chroma-server -f

# Gateway（多 worker 模板）
sudo systemctl status hermes-gateway@jwc-assistant
sudo systemctl restart hermes-gateway@jwc-assistant
sudo journalctl -u hermes-gateway@jwc-assistant -f
```

> 依据：`CLAUDE.md` "关键命令" 节，`infra/chroma-server.service`，`infra/hermes-gateway@.service`。

### 9.2 定时任务

| 服务 | 频率 | 功能 |
|------|------|------|
| hermes cron 任务 `a75cd2bad6f3`（原 `jxtz-sync.timer`，**已 disabled**） | 每日 04:00 | 增量同步 XJTU 教务处通知（`jxtz_sync.py`，`--no-agent`） |
| `backup-kb.timer` | 每日 02:00 | 备份 ChromaDB + SQLite + roles |
| `health-check.timer` ※ | 每 5 分钟 | Gateway 进程 + ChromaDB 心跳 + 磁盘/嵌入/Reranker/LLM |
| `audit-cleanup.timer` | 每日 00:00 | 清理 >90 天审计日志 |
| `temp-cleanup.timer` | 每周六 03:00 | 清理 data/ 中的临时文件 |

> 依据：`docs/05-notes/jxtz-sync-management/README.md`（cron 任务 `a75cd2bad6f3`，`jxtz-sync.timer` 已 `disable --now`），`infra/*.timer` 文件。

> ※ `health-check.service`/`.timer` 与 `scripts/health_check.sh` **不在本仓库**，位于部署机 `~/work/systemd-app/`；`backup-kb` / `audit-cleanup` / `temp-cleanup` / `jxtz-sync` 单元见 `infra/`。

### 9.3 批量入库

**本科管理文件库**（`source="file"`）：

```bash
source venv/bin/activate
python scripts/batch_ingest_benke.py          # 增量
python scripts/batch_ingest_benke.py --reset  # 清空重建
```

遍历 `global/本科管理文件库` 目录，支持的扩展名：`.pdf`, `.docx`, `.doc`, `.txt`, `.md`, `.xlsx`, `.xls`, `.csv`, `.pptx`, `.jpg`, `.jpeg`, `.png`。

> 依据：`scripts/batch_ingest_benke.py`。

**教学通知**（`source="jxtz"`）：

```bash
source venv/bin/activate
python scripts/batch_ingest_jxtz.py          # 增量（支持断点续传）
python scripts/batch_ingest_jxtz.py --reset  # 清空重建
```

从 `data/jxtz_notices.jsonl`（5512 条记录）批量抓取详情页并入库。每 50 条保存进度到 `data/jxtz_ingest_results.jsonl`。

> 依据：`scripts/batch_ingest_jxtz.py`。

**单文件入库**：

```bash
python scripts/admin_cli.py ingest /path/to/file.docx --scope global
python scripts/admin_cli.py ingest /path/to/file.docx --scope teachers
python scripts/admin_cli.py ingest /path/to/file.docx --user-id <id>
```

> 依据：`scripts/admin_cli.py`（`cmd_ingest()`）。

### 9.4 知识库同步

```bash
python scripts/admin_cli.py sync --kb-root .
```

扫描 `teachers/` 和 `global/` 目录，使用 `SyncKbRunner` 批量入库。

> 依据：`knowledge_base/scripts/sync_kb.py`。

### 9.5 清空与重建

**按 source 精准删除**（不停服务，HTTP 模式）：

```bash
# 清空文件通知 (source='file')
python3 -c "
import chromadb
from knowledge_base.bootstrap import load_dotenv
load_dotenv()
c = chromadb.HttpClient(host='127.0.0.1', port=8007)
col = c.get_collection('raw_nodes')
col.delete(where={'source': 'file'})
print('文件通知已清空')
"

# 清空教学通知 (source='jxtz')
# 同上，where={'source': 'jxtz'}
```

> 依据：`CLAUDE.md` "方式 A2：只清空不重建"。

**全量清空重建**：

```bash
sudo systemctl stop chroma-server
rm -rf data/chroma/
rm -f data/quota.db data/quota.db-shm data/quota.db-wal
rm -f data/jxtz_ingest_results.jsonl
sudo systemctl start chroma-server
source venv/bin/activate
python scripts/batch_ingest_benke.py
python scripts/batch_ingest_jxtz.py
sudo systemctl restart hermes-gateway@jwc-assistant
```

> ⚠ **重要**：`data/chroma/` 和 `data/quota.db` 必须一起删除才算真正清空。只删除向量数据而保留版本元数据，会导致旧文件再次入库时被 `VersionManager` 判定为 `SKIP`。

> 依据：`CLAUDE.md` "方式 B：全量清空重建"。

### 9.6 用户数据清理

```bash
python scripts/admin_cli.py purge <user_id>           # 清除用户所有数据
python scripts/admin_cli.py purge <user_id> --dry-run  # 预览
```

`PurgeUserKBPipeline` 清除范围：
- ChromaDB 向量（`purge_scope(f"users/{user_id}")`）
- SqliteStore 元数据（`delete_user_data(user_id)`）
- 所有操作写入审计日志

user_id 经过路径遍历防护。

> 依据：`knowledge_base/scripts/purge_pipeline.py`。

### 9.7 备份

`infra/backup-kb.service` + `backup-kb.timer` 每日备份：
- ChromaDB 数据目录
- SQLite 数据库（`quota.db`, `audit.db`）
- roles.json

> 依据：`infra/backup-kb.service`，`infra/backup-kb.timer`。

---

## 10. 集成指南

### 10.1 在其他脚本中使用知识库

`knowledge_base.bootstrap` 提供三个工厂函数，一行代码即可获取完整组件：

```python
from knowledge_base.bootstrap import (
    load_dotenv,
    build_embedding_function,
    create_chroma_repository,
    create_ingestion_orchestrator,
)

# 加载 .env（幂等，多次调用安全）
load_dotenv()

# 方式 1：完整入库编排器（自动注入 QuotaManager + VersionManager + ExtractorRouter）
orch = create_ingestion_orchestrator()
result = orch.ingest_file("admin", "/path/to/file.pdf", scope="global")

# 方式 2：仅存储层（用于搜索/评估脚本）
repo = create_chroma_repository()
nodes = repo.search_nodes(scopes=["global"], query_embedding=emb, top_k=5)

# 方式 3：仅嵌入函数
embed_fn = build_embedding_function()
```

> 依据：`knowledge_base/bootstrap.py`（模块文档字符串、`create_ingestion_orchestrator()`、`create_chroma_repository()`、`build_embedding_function()`）。

**Gateway 中使用单例**（如 Hermes tools）：

```python
from knowledge_base.bootstrap import init_chroma_repository_singleton
from knowledge_base.repository.chroma_repository import ChromaRepository

# 启动时初始化（kb_init hook 中调用）
init_chroma_repository_singleton()

# 运行时使用
repo = ChromaRepository.instance()
```

> 依据：`knowledge_base/bootstrap.py`（`init_chroma_repository_singleton()`），`gateway/hooks/kb_init/handler.py`（`handle()`）。

### 10.2 调用检索管线

生产检索管线在 `~/.hermes/hermes-agent/tools/query_kb.py` 的 `_retrieve()` 中。如果要在其他脚本中复用相同的检索逻辑：

```python
# eval_ragas.py 的做法：调用同一个 _retrieve() 函数
from tools.query_kb import _retrieve

# 需要先注入 QueryContext
from knowledge_base.core.query_context import inject_context, QueryContext
inject_context(QueryContext(platform="miniapp", user_id="XiongWei", role="owner"))  # 企业微信已弃用，平台标识统一为 miniapp

nodes = _retrieve("转专业需要什么条件", top_k=20)
```

> 依据：`scripts/eval_ragas.py`（`retrieve_for_question()` 调用 `_retrieve()`），`~/.hermes/hermes-agent/tools/query_kb.py`（`current_context()` 用法）。

### 10.3 ACL 集成

```python
from knowledge_base.auth.role_store import resolve_role
from knowledge_base.retrieval.acl_filter import ACLFilter, check_write_permission, derive_default_scope

# 解析角色（Hermes 传入的 role 不可信，必须自行重新解析）
role = resolve_role("miniapp", user_id)  # 企业微信已弃用，平台标识统一为 miniapp

# 读取：获取允许的 scope 列表
acl = ACLFilter(current_user_id=user_id, current_role=role)
allowed_scopes = acl.get_allowed_scopes()

# 写入：检查权限
check_write_permission(role, scope, user_id)  # 失败抛 UnauthorizedAccessError

# 默认 scope
default_scope = derive_default_scope(role, user_id)
# admin/owner → "global"
# teacher → "teachers"
# student → "users/{user_id}"
```

> 依据：`knowledge_base/retrieval/acl_filter.py`，`knowledge_base/auth/role_store.py`。

### 10.4 添加新的文件格式支持

1. 在 `ExtractorRouter` 中注册扩展名 → 参考 `knowledge_base/pipeline/router.py`
2. 在 `read_file()` 中添加解析逻辑 → 参考 `knowledge_base/ingestion/parsers.py`
3. 如果是结构化表格 → 设置 `FileType.STRUCTURED_TABLE`，可选实现 LlamaIndex Reader

### 10.5 在新的 VM 上部署

参考 `infra/vm-templates/deploy.sh`：

```bash
# 1. 获取代码（当前为合并仓库 SparkPath-DGX-SPARK，agent4som 是其子目录）
git clone git@github.com:DBZQ30/SparkPath-DGX-SPARK.git
cd SparkPath-DGX-SPARK/agent4som

# 2. 安装依赖
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入实际值

# 4. 创建数据目录
mkdir -p data/{chroma,logs}

# 5. 部署 systemd 服务
sudo cp infra/chroma-server.service /etc/systemd/system/
sudo cp infra/hermes-gateway@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chroma-server
sudo systemctl enable --now hermes-gateway@<assistant-name>

# 6. 入库知识文档
python scripts/batch_ingest_benke.py
python scripts/batch_ingest_jxtz.py

# 7. 重启 Gateway
sudo systemctl restart hermes-gateway@<assistant-name>
```

> 依据：`infra/vm-templates/deploy.sh`，`infra/deploy-concurrent.sh`，`CLAUDE.md` "部署流程"。

### 10.6 注意事项

1. **修改 `~/.hermes/hermes-agent/` 或 `gateway/hooks/` 下的文件后**，必须重启 Gateway 才能生效：`sudo systemctl restart hermes-gateway@jwc-assistant`。提交 commit ≠ 部署。
   > 依据：`CLAUDE.md` 反模式第 9 条。
2. **chromadb 版本锁定为 1.5.9**，agent4som 和 Hermes 两个 venv 必须使用相同版本。
   > 依据：`requirements.txt`（`chromadb==1.5.9`），`CLAUDE.md` "关键依赖"。
3. **所有写入操作必须走 chroma-server HTTP API**，禁止 `PersistentClient` 直接写 SQLite。
   > 依据：`CLAUDE.md` 前置规则 1。
4. **Gateway 传入的 role 不可信**，tools 必须自行调用 `resolve_role()`。
   > 依据：`CLAUDE.md` "权限控制" 节。
5. **`.env` 文件不入 git**，包含 API Key、Token 等敏感信息。
6. **`data/` 目录不入 git**，`.gitignore` 中排除。

---

## 11. 配置参考

### 11.1 环境变量完整列表

所有变量定义在 `.env` 文件中（位于项目根目录，不入 git）。

**主机 / 网络**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GPU_HOST` | `127.0.0.1` | GPU 服务器 IP；`query_kb.py` 据此推导模型服务默认地址（端口 8000/8001/8002/8005） |
| `NO_PROXY` | — | 代理排除列表（如 `192.168.0.0/16,127.0.0.1,localhost`） |

**ChromaDB**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHROMA_HOST` | `127.0.0.1` | ChromaDB HTTP 服务地址 |
| `CHROMA_PORT` | `8007` | ChromaDB HTTP 服务端口 |
| `CHROMA_DB_PATH` | `data/chroma` | ChromaDB 持久化目录 |
| `CHROMA_AUTH_TOKEN` | — | 客户端 Token 认证凭证（v1.5.9 起支持） |
| `CHROMA_SERVER_AUTHN_PROVIDER` | `chromadb.auth.token_authn.TokenAuthenticationServerProvider` | 服务端认证 provider |
| `CHROMA_SERVER_AUTHN_CREDENTIALS` | — | 服务端认证 Token（与 `CHROMA_AUTH_TOKEN` 相同） |
| `CHROMA_BATCH_SIZE` | `50` | store_nodes 批量写入大小 |

**嵌入服务**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QWEN_EMBEDDING_URL` | `http://<GPU_IP>:8001/v1` | 嵌入 API 端点 |
| `QWEN_EMBEDDING_MODEL` | `qwen3-embedding` | 嵌入模型名（1024 维，底层权重 Qwen3-Embedding-0.6B） |
| `QWEN_API_KEY` | — | API 认证密钥（嵌入+Reranker+LLM 共用） |
| `EMBEDDING_TIMEOUT` | `120` | 嵌入请求超时（秒） |

**Reranker**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `QWEN3_RERANKER_URL` | — | Qwen3Reranker 类的首选端点（仅在未显式传 `base_url` 时生效；当前检索工具显式传入，实际以 `BGE_RERANKER_URL` 为准） |
| `BGE_RERANKER_URL` | `http://<GPU_IP>:8002/v1/rerank` | 检索工具实际使用的 Reranker 端点 |

**Step-Back 改写**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STEP_BACK_ENABLED` | `true` | 是否启用 Step-Back 查询改写 |
| `STEP_BACK_MODEL_URL` | `http://<GPU_IP>:8000/v1` | LLM 端点 |
| `STEP_BACK_MODEL_NAME` | `qwen3-vl`（旧默认/模板值；dgx 部署为本地 `qwen3.8-27b`） | LLM 模型名 |
| `STEP_BACK_API_KEY` | 同 `QWEN_API_KEY` | LLM API 密钥 |

**MinerU（PDF/图片解析）**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINERU_URL` | — | MinerU 服务地址（空=跳过） |
| `MINERU_POLL_INTERVAL` | `2` | 异步轮询间隔（秒） |
| `MINERU_POLL_TIMEOUT` | `300` | 异步轮询超时（秒） |
| `MINERU_RETRIES` | `10` | 409 冲突重试次数 |
| `MINERU_RETRY_MAX_BACKOFF` | `120` | 重试最大退避（秒） |
| `MINERU_CONNECT_RETRIES` | `3` | 连接失败/超时重试次数 |

**VL 模型（图片回退）**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VL_MODEL_URL` | `http://<GPU_IP>:8000` | VL 服务地址 |
| `VL_MODEL_NAME` | `qwen3-vl`（旧默认/模板值；dgx 部署为本地 `qwen3.8-27b`） | VL 模型名 |
| `VL_MAX_TOKENS` | `1024` | VL 最大输出 token |
| `VL_ENABLE_THINKING` | `0`（关闭） | 是否保留 VL 推理链 |
| `PDF_VL_MAX_PAGES` | `10` | 扫描版 PDF 走 VL 逐页回退时的最大页数（0=全部） |

**解析器 / 入库**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KB_USE_LEGACY_PARSER` | `false` | 强制使用 legacy 切分（跳过 LlamaIndex） |
| `INGEST_ALLOWED_DIRS` | — | 入库路径白名单（冒号分隔；设置后仅允许这些目录下的文件入库） |

**BM25 / DAT / 输出校验**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DAT_ENABLED` | `false` | 启用 Dynamic Alpha Tuning（**代码读取，`.env.example` 模板未列**） |
| `BM25_INDEX_LIMIT` | `10000` | BM25 索引节点上限 |
| `RESPONSE_VERIFY_ENABLED` | 关闭 | 输出层响应校验开关（scope 泄漏检测/脱敏） |

**告警**：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WECOM_ALERT_WEBHOOK` | — | 企业微信机器人 webhook（健康检查告警）；**历史兼容，企业微信已弃用** |

> 依据：`.env.example`（变量与模板默认值）、`requirements.txt`（核心依赖：`chromadb==1.5.9`、`pydantic`、`requests`、`llama-index-core`、`pdfminer.six`、`openpyxl`、`python-docx`、`python-pptx`、`pypdf`、`xlrd`、`Pillow`、`fastapi`、`uvicorn`、`python-multipart`），`knowledge_base/ingestion/parsers.py`（MinerU/VL 配置），`knowledge_base/bootstrap.py`（嵌入配置），`~/.hermes/hermes-agent/tools/query_kb.py`（检索配置），`knowledge_base/retrieval/bm25_search.py`（BM25/DAT 配置）。

### 11.2 角色管理环境变量

| 变量 | 说明 |
|------|------|
| `HERMES_OWNER` | 指定 owner 用户 ID（最高优先级） |
| `WECOM_HOME_CHANNEL` | home channel 用户 ID（历史平台 owner 检测）；**历史兼容，企业微信已弃用** |
| `HERMES_HOME` | Hermes 主目录（默认 `~/.hermes`），`roles.json` 位于此目录下 |

> 依据：`knowledge_base/auth/role_store.py`（`ROLES_PATH`、`_detect_owner()`）。

### 11.3 .gitignore 关键规则

实际 `.gitignore` 内容：

```
.worktrees/
__pycache__/
*.pyc
/venv
data/
data_copy/
academicwarning/*.docx
academicwarning/*.xlsx
academicwarning/*.xls
global/
.env
CLAUDE.md
docs/weekly-reports/
docs/others/
.coverage
coverage.json
benchmark.md
shared_skills/*/reports/
shared_skills/*/evals/environment/
academicwarning/*.png
academicwarning/pic/*.png
.env.bak-*
.superpowers/
academicwarning/docs/
academicwarning/result/
AGENTS.md
skilleval-reports/
```

关键排除项：`data/`、`data_copy/`（运行时数据，含 ChromaDB、SQLite、日志）、`.env`（环境变量含密钥）、`.env.bak-*`（备份）、`AGENTS.md`/`CLAUDE.md`（Agent 说明）。**注意**：`.env.*` 整体与 `.hermes/` 当前**不在** `.gitignore` 中（仅 `.env.bak-*` 被排除）。

> 依据：项目根目录 `.gitignore` 文件。

---

## 12. 模块索引

### 12.1 knowledge_base 子包

| 子包 | 文件 | 核心功能 |
|------|------|---------|
| `auth/` | `role_store.py` | 角色解析 (5 角色体系，JSON 文件持久化) |
| | `guards.py` | 管理操作的代码级授权守卫（纯函数，按角色判定，无 I/O） |
| `core/` | `audit_logger.py` | 审计日志（SQLite，SHA-256 链式哈希） |
| | `display_names.py` | 文件展示名解析（miniapp 适配器与 `query_kb.py` 共用，避免两处漂移） |
| | `exceptions.py` | 异常层次（`KnowledgeBaseError` → 6 个子类） |
| | `query_context.py` | 请求上下文（`contextvars`，线程安全） |
| | `quota_manager.py` | 配额检查（大小/速率/日限/总数） |
| | `sqlite_store.py` | SQLite 持久化（3 表：元数据+日计数+上传日志） |
| | `version_manager.py` | 去重决策（`NEW/SKIP/REPLACE`） |
| `ingestion/` | `orchestrator.py` | 入库编排器（路由→配额→版本→解析→存储） |
| | `parsers.py` | 多格式解析（`read_file()`、MinerU、VL、pdfminer、docx/pptx/antiword） |
| | `semantic_splitter.py` | 语义切分（标题检测+层级路径+回退行切分） |
| `llamaindex/` | `readers.py` | 格式 Reader（含自定义 `StructuredExcelReader`） |
| | `ingestion_pipeline.py` | LlamaIndex 管线工厂 |
| | `metadata_extractor.py` | 元数据规范化（Reader→RawIndexNode） |
| | `chroma_vector_store.py` | LlamaIndex 节点→RawIndexNode 转换 |
| `models/` | `schemas.py` | Pydantic 模型（`RawIndexNode` 26 字段, `BaseFact`, `make_chunk_id`） |
| `pipeline/` | `router.py` | 文件类型路由（扩展名+magic bytes） |
| | `extractor.py` | 事实提取（带 Schema 校验失败降级） |
| | `state_machine.py` | 审核状态机（PENDING→APPROVED/REJECTED/EXPIRED） |
| `repository/` | `chroma_repository.py` | 向量存储（HTTP/嵌入式，单例，2 集合） |
| | `embedding_providers.py` | 嵌入函数（Qwen3/内置/回退，维度验证） |
| | `interfaces.py` | 存储抽象接口（ABC，12 个抽象方法） |
| | `sqlite_metadata.py` | miniapp 状态的 SQLite 持久化（`miniapp.db`） |
| | `chroma_metadata_dao.py` | `file_metadata` 表 DAO（去重/版本元数据） |
| `retrieval/` | `acl_filter.py` | 权限控制（三层防线，读/写/默认 scope） |
| | `bm25_search.py` | BM25 关键词索引（内存，启动预热+懒加载，DAT） |
| | `reranker.py` | Reranker 抽象基类（`rerank()`） |
| | `qwen3_reranker.py` | Qwen3 精排（`/v1/rerank` API，失败回退） |
| | `bge_reranker.py` | BGE 精排（同 `/v1/rerank` 协议，5xx 自适应减批重试） |
| | `context_stitcher.py` | 上下文拼接（±1 邻居，窗口合并） |
| | `response_verifier.py` | 响应校验（`RESPONSE_VERIFY_ENABLED` 开启时检测/脱敏 scope 泄漏，默认关闭） |
| | `conflict_resolver.py` | 事实冲突解决（系统组 `system`/`global`/`assistant` 优先于 `user`；同组按 `effective_from` 降序取最新，并列则报冲突） |
| | `normalize_for_conflict_resolver.py` | 将 `RawIndexNode`/`BaseFact` 归一化为 ConflictResolver 输入格式 |
| `scripts/` | `purge_pipeline.py` | 用户数据清理管线 |
| | `sync_kb.py` | 目录同步入库（`/sync_kb` 后端） |
| | `role_batch.py` | 角色批量导入/导出 |
| `utils/` | `hashing.py` | SHA-256 哈希工具 |
| | `service_manager.py` | 服务生命周期管理（chroma-server 启停） |

### 12.2 关键脚本

| 脚本 | 路径 | 功能 |
|------|------|------|
| `admin_cli.py` | `scripts/admin_cli.py` | 管理 CLI（ingest/sync/roles/purge/audit/health） |
| `batch_ingest_benke.py` | `scripts/batch_ingest_benke.py` | 本科文件批量入库 |
| `batch_ingest_jxtz.py` | `scripts/batch_ingest_jxtz.py` | 教学通知批量入库（含 WAF 反爬） |
| `sync_jxtz.py` | `scripts/sync_jxtz.py` | 教学通知增量同步 |
| `eval_ragas.py` | `scripts/eval_ragas.py` | RAGAS 检索质量评估 |
| `health_check.sh` ※ | 部署机 `~/work/systemd-app/`（**不在本仓库**） | 健康检查（7 项，告警） |

### 12.3 Hermes 集成点

| 文件 | 路径 | 功能 |
|------|------|------|
| `query_kb.py` | `~/.hermes/hermes-agent/tools/query_kb.py` | `knowledge_search` 工具（检索+格式化） |
| `knowledge_ingest.py` | `~/.hermes/hermes-agent/tools/knowledge_ingest.py` | `knowledge_ingest` 工具（入库+ACL） |
| `kb_init/handler.py` | `gateway/hooks/kb_init/handler.py` | Gateway 启动初始化（单例+BM25 索引） |
| `kb_init/HOOK.yaml` | `gateway/hooks/kb_init/HOOK.yaml` | 钩子配置（`gateway:startup` 事件） |
| `SOUL.md` | `~/.hermes/SOUL.md` | 系统提示词（知识库优先等 6 条规则） |

### 12.4 基础设施文件

| 文件 | 路径 | 功能 |
|------|------|------|
| `chroma-server.service` | `infra/chroma-server.service` | ChromaDB systemd 服务 |
| `hermes-gateway@.service` | `infra/hermes-gateway@.service` | Gateway 模板服务（多 worker） |
| `health-check.service`/`.timer` ※ | 部署机 `~/work/systemd-app/`（**不在本仓库**） | 健康检查服务 |
| `backup-kb.service` | `infra/backup-kb.service` | 知识库备份服务 |
| `audit-cleanup.service` | `infra/audit-cleanup.service` | 审计日志清理服务 |
| `temp-cleanup.service` | `infra/temp-cleanup.service` | 临时文件清理服务 |
| `jxtz-sync.service` | `infra/jxtz-sync.service` | 教学通知同步服务 |
| `deploy.sh` | `infra/vm-templates/deploy.sh` | 新 VM 部署脚本 |
| `deploy-concurrent.sh` | `infra/deploy-concurrent.sh` | ChromaDB + Gateway 部署脚本 |
| `agent4som.logrotate` | `infra/agent4som.logrotate` | 日志轮转配置 |
| `journald.conf.snippet` | `infra/journald.conf.snippet` | systemd journal 限制 |

> ※ 标 ※ 的单元/脚本位于部署机的 `~/work/systemd-app/`，**不在本仓库**；本仓库 `infra/` 仅含上表其余单元。

---

## 附录 A：关键常量速查

| 常量 | 值 | 位置 |
|------|----|------|
| `NODES_COLLECTION` | `"raw_nodes"` | `chroma_repository.py` |
| `FACTS_COLLECTION` | `"normalized_facts"` | `chroma_repository.py` |
| `_VALID_SCOPE_RE` | `r"^(global\|teachers\|users/[a-zA-Z0-9_.-]+)$"` | `chroma_repository.py` |
| `_DEFAULT_CHUNK_SIZE` | `512` | `orchestrator.py` |
| `_CHUNK_OVERLAP` | `32` | `orchestrator.py` |
| `max_file_size_mb` | `100` | `quota_manager.py` |
| `max_user_files` | `50` | `quota_manager.py` |
| `max_daily_uploads` | `30` | `quota_manager.py` |
| `max_uploads_per_minute` | `3` | `quota_manager.py` |
| `_SHARED_SCOPES` | `frozenset({"global", "teachers"})` | `version_manager.py` |
| `CHROMA_BATCH_SIZE` | `50` | `chroma_repository.py` |
| BM25 `_k1` | `1.2` | `bm25_search.py` |
| BM25 `_b` | `0.75` | `bm25_search.py` |
| `bm25_weight` (hybrid_search) | `0.3` | `bm25_search.py` |
| `ROLES_PATH` | `~/.hermes/roles.json` | `role_store.py` |
| `DEFAULT_ROLE` | `"student"` | `role_store.py` |

## 附录 B：上下文检索前缀格式

每个 chunk 在语义切分时自动添加 Contextual Retrieval 前缀。前缀是**单行**（不是多行），后接正文：

```
[文档: {clean_name} | {section_path}]
{内容}
```

当 `section_path` 为空时，前缀退化为 `[文档: {clean_name}]`。`clean_name` 为去掉内部 ID 前缀与扩展名的文件名。

示例：
```
[文档: 西安交通大学本科生学籍管理规定 | 第3章 转专业>3.2 申请条件]
一、申请转专业的学生应同时具备以下条件：
1. ...
```

> 依据：`knowledge_base/ingestion/semantic_splitter.py`（`_make_chunk()` 函数中生成 Contextual Retrieval 前缀）。

## 附录 C：检索结果引用格式

`handle_knowledge_search()` 逐条拼接引用头 `参考{i}: {ref}`，条目之间以 `\n\n---\n\n` 分隔。`ref` 的构造规则：`[{source_file}]`，若 `section_title` 存在则追加 ` - {section_title}`；若 `page_start` 存在则追加 ` - 第{page_start}页`，否则若有 `sheet_name` 则追加 ` - Sheet={sheet_name}`（并在有 `row_start` 时追加 ` 行{row_start}-{row_end}`）；若正文中出现 `来源: https://...` 则追加该 URL（**不加** `来源:` 标签，也不生成日期/列号字段）。

示例：

```
参考1: [西安交通大学本科生学籍管理规定] - 第3章 转专业 - 第12页
{content}

---

参考2: [培养方案2025] - Sheet=课程结构 行42-57
{content}

---

参考3: [教务处通知] - https://jwc.xjtu.edu.cn/info/1234/5678.htm
{content}
```

> 依据：`~/.hermes/hermes-agent/tools/query_kb.py`（`handle_knowledge_search()` 格式化输出段）。

## 附录 D：与 som-rag-data-plane-design.md 的关系

本文档（`som-rag-knowledge-base-design.md`）是**实现文档**，描述当前代码的实际行为。`som-rag-data-plane-design.md`（2026-05-14）是**设计文档**，描述当时的设计意图和规划方向。二者有以下关键差异：

1. 设计文档提出 `assistants/{id}` scope，当前实现使用 `teachers` scope。
2. 设计文档提出 `global/_derived/` 和 `users/{id}/_derived/` 派生目录，当前未实现。
3. 设计文档中的管理员确认卡片流程（第 9 节）、CAS 回写（第 9 节）等高级特性在当前实现中处于规划阶段（`BaseFact.needs_admin_confirmation` 已预留字段，状态机已实现，但管理员确认卡片为**企业微信时代设计，未落地**）。
4. 设计文档中的 `visibility_tag` 概念已存在于 `RawIndexNode` Schema，但注释明确标注 "NOT wired into ACL filtering"（`schemas.py`）。
5. 设计文档中的 `fact_key` 冲突裁决已实现（`conflict_resolver.py`），但仅用于事实层面，不用于 chunk 检索。

集成时请以本文档为准——本文档的每一个陈述均有对应的代码依据（文件/函数）可验证。
