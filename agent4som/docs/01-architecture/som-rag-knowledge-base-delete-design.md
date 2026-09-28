# SOM RAG 知识库 — 出库与管理面可观测性设计

> 版本: 1.6.8 · 日期: 2026-09-09 · 状态: 前后端已实现并已部署（步骤 0–8 完成；步骤 9 跨仓改动待单独授权；开发者工具视觉确认与真实 token 端到端待用户）
> 变更: 1.6.8 §11 步骤 7-8 实施回写（v1.5 前后端）：① 后端——`role_store.default_role_for()` + `resolve_role` 去常量比较（M2，已落代码）、`ChromaRepository.get_file_documents()`、列表顶层 `quota` + 每文件 `status`、`GET /knowledge/content`、`GET /knowledge/audit`；② 前端——`pages/knowledge/*` 状态标签 / 配额 / 操作历史页签 + 新建 `pages/knowledge-preview/*` 预览页 + `config/api.js` **2 条**接口 + `build.js` 注册；③ 实现口径细化（补文档未写的边界，非偏差）：预览缺 `filename` → 400 `invalid_filename`、`limit` 非正整数 → 400 `invalid_limit`、审计器不可用 → 503 `audit_unavailable`、`quota` 键**恒存在**（admin / owner 为 `null`）；④ §8 五行状态回写 + 路由探活 / 部署行补记，附录 A 行号按实施后重同步（`adapter.py` / `chroma_repository.py` / `role_store.py`）
> 变更: 1.6.7 §11 步骤 5 实施回写：前端 `pages/knowledge/*`（范围切换 / 显示名搜索 / 来源筛选 / 单删 / 批量 / 孤儿扫描 / 访客隐藏）+ 功能页入口卡 + `build.js` 注册 + `config/api.js` **3 条**接口（v1.5 的 `METHODS_KNOWLEDGE_CONTENT` / `METHODS_KNOWLEDGE_AUDIT` 随步骤 7-8 与端点一起加，避免先声明不存在的地址）；§8 前端相关行状态更新
> 变更: 1.6.6 部署探活发现并修复一处鉴权顺序偏差：DELETE 端点原先「先解析 body 再鉴权」，无 token 的空 body 返回 400 而非 401（实测 20:27 部署探活），与白名单 DELETE 端点的 401 口径及 §8 路由探活断言不一致。改法：`_knowledge_identity()`（401）→ `_json_body`（400）→ `_knowledge_scope_guard()`（403），矩阵仍只有 `_knowledge_scopes()` 一处（§4.2）
> 变更: 1.6.5 §8 验证状态回写（步骤 0–3 已完成项）：单元 harness / 用例 / BM25 同步 / 来源标记 / 显示名 / 批量删除 / 孤儿扫描 / 历史数据清理 / 严格复核 → 已实施；「来源派生与筛选」「访客」→ 后端已实施、前端待做；§2.2 补步骤 0 执行后复测值（8339 节点 / 1301 行）
> 变更: 1.6.4 步骤 3 实施回写（4 处与文档的偏差，均已落到代码）：① `list_source_files(scope)` 返回 `dict[str, int]`（文件名 → 节点数）而非 `set[str]`——§4.5 响应里的 `scanned_nodes` 用去重集合**无法**算出，返回计数即可 `sum()` 得到，且仍是**一次** `col.get`；② 新增 `count_collection_nodes()`（集合级计数）供 §4.5「整集合为空 → 503」护栏使用；③ 新增 `get_audit_logger()`（`response_verifier.py`，此前只有 `init_audit_logger`，§4.2 的审计写入无公开入口）；④ `_knowledge_delete_one(scope, filename)` 增补 keyword-only 的 `openid` / `role` / `op`——§4.2 的审计事件需要 `user_id` / `role` / `detail.op`，这两个值只在 handler 里
> 变更: 1.6.3 按用户复审的 4 处残余缺口修订：① §10 M3 行 / §11 步骤 3 的旧口径（`delenv`）同步为 §8 N1 的「注入 fake repo / `PersistentClient(tmp_path)`」；② §8 操作历史用例写明「批量删 1 个文件」前提，并改为按 `detail.op` 分组断言（批量每成功 1 个文件写 1 条）；③ §4.2 / §4.6 补明 `metadata_rows` 与第 4 步逐行删的来源 = `list_file_metadata_by_scope(scope)` 按 `filename` 过滤（现有 `sqlite_store` 无「按文件列全部上传者」的查询）；④ §4.7.1 `quota` 由每文件字段改为响应顶层（每用户一个值）
> 变更: 1.6.2 全文逐条对照代码复核（只保留有代码依据的结论，逐项复测数据面）：修正行号漂移 10 处（§2.4 `count_nodes:397`、§2.6 `:245-247`、§2.8 `sync_jxtz.py:32-33`、§4.6 `orchestrator.py:124`、§4.7.2 `unlink:391`、§7.1 `query_kb.py:276-279`、§7.4 `:338`/`:346`、§9 #6 与附录 A `nas_backup.sh:57-67`）与口径 5 处（§2.3 metadata key 的 sqlite/API 双口径、§2.6 文件数、§2.8 jxtz 文件数与 `admin:fix` 8 条、§4.5 实测口径、§6.14 setData 1024 kB 推导、§4.7.4 import 处数 14）
> 变更: 1.6.1 按评审 020 的 §7 复审（N1–N3）修订：**N1** §8 harness 隔离主推方案改为「注入 fake repo / 直接 `ChromaRepository(PersistentClient(tmp_path))`」，`delenv` 降为辅助——`create_chroma_repository()` 会调 `bootstrap.load_dotenv()`（`bootstrap.py:125`）用 `os.environ.setdefault` 把变量从 `.env` 重新注入，实测仍连生产 `8007`；**N2** 经复核**不成立**（`query()` 自身已 `json.loads` `detail`，`audit_logger.py:263-268`），§4.7.3 只加说明不改实现；**N3** 措辞「全量冷备」→「在线全量备份（不停服务）」（§1.3 / §9 #6 / §10 / §12）
> 变更: 1.6 按删除设计评审修订（M1–M4 + E1 + L1–L12 全采纳）：**M1** 审计统一为单一事件类型 `kb_delete`（§4.2 / §4.7.3，原 `delete`/`batch_delete` 与 `query()` 单值等值签名冲突 → 操作历史会恒空）；**M2** 修正 D15 改法（`resolve_role` 去常量比较，否则**已登记的小程序 student 被静默降级为 guest**，§4.7.5）；**M3** harness 隔离必须同时清 `CHROMA_HOST/PORT`，否则删的是生产向量（§8）；**M4** 列表规模阈值与预案入档（§6.11）；**E1** 修正 §9 风险 #6——NAS 每日 01:00 在线全量备份**正常**（本地 `backup-kb` 自 2026-08-25 起 SELinux 拒 exec 静默失败，已修）；**L1–L12** 见 §10「评审采纳」
> 变更: 1.5 按 2026-09-09 追加拍板（**范围从「删除」扩到管理面可观测性**）：⑧ 小程序未登记用户默认角色改为 **guest**（其它平台仍保持 student，D15、§4.7.5）；⑨ 对话回答的引用改用**显示名**（跨仓 `query_kb.py`，D16、§4.7.4）；⑩ 个人库列表标出**入库状态**（D17、§4.1 / §4.7.1）；⑪ **个人配额**可见（D18、§4.7.1）；⑫ **原文只读预览**（D19、§4.7.2）；⑬ 管理员**操作历史**可见（D20、§4.7.3）
> 变更: 1.4 按 2026-09-09 追加拍板：① 访客（guest）无知识库、不可上传也不可管理（D11，§4.6 矩阵 / §5）；② 列表**显示来源并支持一键筛选（仅 admin / owner 可见）**（改写 D8，§4.1 / §5）；③ 教务通知显示**完整原始标题**（取自抓取账本，D10，§4.1）；④ 新增**批量删除**（D12，§4.4）；⑤ **不做回收站 / 撤销**，删除确认文案强制「删除后不可撤回」（D13，§5）；⑥ 清理 `users/*` 5 行历史数据 + 10 行 0 节点脏元数据（§11 步骤 0）；⑦ 新增**孤儿扫描**端点（只读候选 + 勾选删除，不做一键全删，D14、§4.5）
> 变更: 1.3 §7 全部改为**单文件 / 单条通知**口径（删除单位 `(scope, source_file)`），补「同 scope 其他文件不受影响、BM25 其他文件打分自洽、相邻文件上下文拼接不报错、删除后可重新上传同一文件、抓取通知无持久副本」等结论
> 变更: 1.2 全篇去除推测性表述、逐条补代码依据；新增 §7「删除后的查询可用性与数据完整性」（7.1 查询 / 7.2 损坏风险 / 7.3 数据到位 / 7.4 并发），并据 §4.2 严格复核补上「Chroma 不可用 → 503、不写任何东西」的失败路径
> 变更: 1.1 补充两类入库来源（教务通知自动抓取 / 人工上传）及其与删除的交互（§2.8、D8/D9）
> 分支: main
> 关联: `som-rag-knowledge-base-design.md`（§4 存储层 / §5 入库管线 / §7 权限控制 / §9 运维操作）
> 已拍板前提: ① 共享库（`teachers` / `global`）**有写权限即可删**，不按上传者区分；② 本次交付范围 = **后端接口 + 前端管理页**；③ 访客（guest）不参与知识库；④ 不做回收站 / 撤销，删除不可逆。

## 目录

1. [概述](#1-概述)
2. [数据面现状（代码级事实）](#2-数据面现状代码级事实)
3. [关键设计决策](#3-关键设计决策)
4. [后端设计](#4-后端设计)
5. [前端设计](#5-前端设计)
6. [一致性与边界](#6-一致性与边界)
7. [删除单个文件后的查询可用性与数据完整性](#7-删除单个文件--单条通知后的查询可用性与数据完整性)
8. [验证方案](#8-验证方案)
9. [风险与回滚](#9-风险与回滚)
10. [待拍板](#10-待拍板)
11. [实施顺序](#11-实施顺序)
12. [模块索引](#12-模块索引)
13. [附录 A：引用行号速查](#附录-a引用行号速查)

---

## 1. 概述

### 1.1 问题

知识库只有「入库」没有「出库」：

- 小程序端 adapter 的 5 条白名单路由是当前唯一带 `DELETE` 的方法端点，知识库没有任何删除端点
  > 依据：`~/.hermes/plugins/miniapp-platform/adapter.py:124-128`。
- 用户传错文件（传错 scope、传错版本、教师误传公共库）后无法撤回，文件继续被检索到，且一直占用 `max_user_files=50` 的配额
  > 依据：`knowledge_base/core/quota_manager.py:11`（`max_user_files=50`）、`:45`（计数判定）。
- 数据面其实**已有删除原语**（`delete_nodes` / `delete_file_metadata` / `purge_scope`），但只暴露给运维脚本 `scripts/purge_pipeline.py`（整 scope 清空），小程序端无法使用
  > 依据：`knowledge_base/repository/chroma_repository.py:365`、`:546`、`knowledge_base/core/sqlite_store.py:200`、`knowledge_base/scripts/purge_pipeline.py`。

### 1.2 目标

小程序端提供「列出已入库文件 + 删除单个文件 + 批量删除」，删除后 **向量节点、元数据行、配额计数、BM25 内存索引**四者同步收敛，被删文件不再出现在检索结果里。

- 列表区分两类来源（**文件入库** / **教务通知入库**），**管理员**可一键筛选（默认全部）；来源相关的 UI（筛选条 + 来源标签）**只对 admin / owner 显示**（D8）
- 教务通知显示**完整原始标题**，不是内部文件名（D10）
- 删除确认必须明示「删除后不可撤回」；本次**不做**回收站 / 撤销（D13、§1.3）
- 访客（guest）不可上传、不可进入知识库管理（D11）
- 管理员可**扫描「孤儿」元数据行**（有元数据行、Chroma 0 节点）并勾选清理；只读扫描 + 人工确认，不做一键全删（D14、§4.5）
- **可观测性**（v1.5）：个人库列表标出**入库状态**（有内容 / 未完成）与**配额**（已用 / 上限）；文件可**只读预览原文**（从节点内容拼回，截断 2 万字）；管理员可查**删除操作历史**（D17–D20、§4.7）
- **对话回答的引用用显示名**，不再是内部文件名（D16、§4.7.4，跨仓）
- **小程序未登记用户默认角色 = 访客**：不能上传、看不到知识库管理入口，但仍可在对话里查公共库（D15、§4.7.5）

### 1.3 非目标（本次不做）

| 项 | 原因 |
|---|---|
| 回收站 / 撤销 / 软删除 | **已拍板不做**（2026-09-09）；`delete_nodes` 无版本快照可回滚，且抓取通知本地无副本（§7.3）→ 替代措施 = 删除确认文案强制「删除后不可撤回」（D13）+ **每日 01:00 NAS 在线全量备份兜底（T-1，§9 风险 #6）**。误删的恢复单位是「整份 `data/` 回滚到昨日」，会一并回退当日其它变更——这是不做回收站的代价，已如实写进风险 |
| 单条 chunk 删除 | 删除单位由数据模型决定（§2.3），chunk 级粒度没有使用场景 |
| 整 scope 清空 | 已有运维工具 `purge_pipeline.py` |
| 按上传者删除 | 与「共享库有写权限即可删」的前提冲突 |
| 清理 `miniapp_uploads` 临时副本 | 24h 自动过期，见 §6.5 |
| 改动抓取账本（`data/jxtz_notices.jsonl`、`data/jxtz_ingest_results.jsonl`） | 删除只作用于知识库；动账本会让已删通知被重新抓取（§2.8） |
| 修复「新入库文件对 BM25 不可见」 | 既有一致性缺口，非本次引入，见 §2.6 |

---

## 2. 数据面现状（代码级事实）

### 2.1 一次入库写了哪三处

| 位置 | 内容 | 关键点 |
|---|---|---|
| `data/chroma` → collection `raw_nodes` | 向量节点（现网 8384 个） | 节点 metadata 含 `scope` / `source_file` / `source_path`，**不含 `user_id`**（§2.3） |
| `data/quota.db` → `file_metadata` | PK `(user_id, filename, scope)` + `content_hash` / `file_hash` | 配额计数与去重的权威来源 |
| `data/quota.db` → `daily_uploads` / `upload_log` | 日 / 分钟上传计数 | 只增不减，本次不动 |

> 依据：DDL `knowledge_base/core/sqlite_store.py:39-47`；DB 路径 `knowledge_base/bootstrap.py:133`（`sqlite_path = os.path.join(os.path.dirname(chroma_path), "quota.db")`）。
> 入库入口：`_upload` → `_auto_ingest`（`adapter.py:377`、`:385`）→ `IngestionOrchestrator.ingest_file(user_id=openid, scope=…)`。

### 2.2 现网规模（2026-09-09 实测）

| 数据面 | 数值 |
|---|---|
| Chroma `raw_nodes` 节点总数 | 8384（`global` 8295 / `teachers` 44 / `users/XiongWei` 44 / `users/openid-1` 1） |
| Chroma `source_file` 去重数 | 1298（`global` 1297） |
| `file_metadata` 行数 / 文件数 | 1316 行；`global` 1308、`teachers` 3、`users/XiongWei` 3、`users/zhangsan` 1、`users/openid-1` 1 |
| `global` 的 user_id 分布 | `admin` 1307、`XiongWei` 1 |

⚠️ **公共库是真实教务资料（1308 个文件）**，删除不可逆 → §4.2 的预览与二次确认不是可选项。

> 注（2026-09-09 §11 步骤 0 执行后复测）：Chroma `raw_nodes` = **8339**、`file_metadata` = **1301** 行（`global` 1298 / `teachers` 3）。上表为清理前基线；差值来自步骤 0 删掉的 15 行（`users/*` 5 行及其 45 个节点 + `global` 10 行 0 节点脏行）。

补充实测（2026-09-09）：

- `users/*` 共 **5 行**历史数据、涉及 3 个 user_id：`XiongWei` 3 行、`zhangsan` 1 行、`openid-1` 1 行 —— 本次**全部清理**（§11 步骤 0）
- 现网 `roles.json` 共 5 个用户（owner 1 / admin 2 / student 1 / 小程序 openid 1），**当前没有 guest 用户** —— D11 的访客限制是防未来（白名单可授予 guest，`adapter.py:918`）
- `global` 另有 **10 行 0 节点脏元数据**（`user_id=admin`）：8 行乱码文件名与各自的「正常文件名」兄弟行共享 record_id → 列表会显示同名两行；2 行空壳。Chroma 里无对应 `source_file`（§2.8）→ 随 §11 步骤 0 清理

### 2.3 节点 metadata 没有 `user_id` → 删除单位只能是 `(scope, source_file)`

Chroma 节点 metadata 的完整 key 集合（实测）：

```
anchor_locator, anchor_text, chroma:document, col_end, col_start, doc_version,
next_node_id, node_id, page_start, parse_confidence, parser_version, prev_node_id,
row_end, row_start, scope, section_path, section_title, sheet_name, source,
source_file, source_hash, source_path, source_tier, visibility_tag
```

> 口径：上述 24 个 key 取自 `data/chroma/chroma.sqlite3` 的 `embedding_metadata` 表（含存储层正文键 `chroma:document`）；HTTP API `col.get(include=["metadatas"])` 返回其中 23 个（不含 `chroma:document`，正文走 `documents`；23 为全库并集，单节点 12–21 个，可选字段随解析器类型而异）。两种口径均**无 `user_id`**。

**没有 `user_id`**：向量节点不知道自己是谁传的。因此删除的最小可寻址单位是 `(scope, source_file)`，对应 `delete_nodes(scope, source_file)` 的 `where` 过滤
> 依据：`chroma_repository.py:208-210`（`{"$and": [{"scope": scope}, {"source_file": source_file}]}`）、`:365-395`。
> 另注：`source_path` 存的是 scope 而非路径（`knowledge_base/ingestion/semantic_splitter.py:266`），BM25 的 `remove_scope()` 正是按它做前缀匹配（`bm25_search.py:124-131`）。

### 2.4 可复用原语

| 原语 | 位置 | 行为 |
|---|---|---|
| `delete_nodes(scope, source_file=None)` | `chroma_repository.py:365` | 删该 scope 下该 `source_file` 的全部节点；**best-effort**：异常只记日志、恒返回 `True` |
| `count_nodes(scope, source_file=None)` | `chroma_repository.py:397` | 按同一 `where` 计数（`limit=5000`，出错返回 0）→ 可用来复核删除结果 |
| `delete_file_metadata(user_id, filename, scope)` | `sqlite_store.py:200` | 删一行元数据，配额随之释放 |
| `get_file_metadata_any_user(filename, scope)` | `sqlite_store.py:108` | 判断共享库中该文件名是否还有其它上传者 |
| `get_user_file_count(user_id)` | `sqlite_store.py:66` | 配额计数（`max_user_files` 判定用） |
| `get_file_metadata_by_hash(file_hash, scope)` | `sqlite_store.py:137` | 按内容哈希反查同名内容（§6.2 的重复内容场景） |
| `delete_user_data` / `purge_scope` | `sqlite_store.py:214` / `chroma_repository.py:608` | 粒度太大（整用户 / 整 scope），本次不用 |
| `AuditLogger.log_event(...)` | `knowledge_base/core/audit_logger.py:133` | 已在 Gateway 启动时初始化到 `audit.db`，schema 自带 `filename` / `scope` / `node_count` / `detail` 字段 |
| `BM25Index.remove_scope(scope_pattern)` | `bm25_search.py:124` | 按 scope 前缀删内存索引节点（**scope 级，不是文件级**） |

### 2.5 缺口 A：没有「按 scope 列文件」的查询

`SqliteStore` 只有按 `(user_id, filename, scope)` 的单点查询（`:94`、`:108`），没有「列出某 scope 下所有文件」的只读方法 → 需新增（§4.6）。
Chroma 侧同样没有按 scope 列举的封装（`search_nodes` 需要 query embedding，`chroma_repository.py:261`）。

### 2.6 缺口 B：BM25 内存索引是**启动快照**（本次必须处理）

检索管线里 BM25 与向量结果是**融合**的，BM25 命中的节点可以不出现在向量结果里、却进入最终 top-k：

```python
id_to_node = {n.node_id: n for n in vector_results}
for n, _ in bm25_results:
    if n.node_id not in id_to_node:
        id_to_node[n.node_id] = n        # ← BM25 独有的节点也会进入结果
```

> 依据：`knowledge_base/retrieval/bm25_search.py:259-326`（`hybrid_search`，融合与并集在 `:303-315`）；调用点 `~/.hermes/hermes-agent/tools/query_kb.py:251-254`。

而 `BM25Index` 只在 **Gateway 启动时一次性** 从 Chroma 灌入，之后入库 / 删除都不更新：

- 启动灌入：`gateway/hooks/kb_init/handler.py:82-127`（`bm25.index(nodes)`，`BM25_INDEX_LIMIT=10000`）
- 懒加载兜底：`bm25_search.py:153-169`（`get_bm25_index()` 每进程只灌一次）
- 入库侧**没有**任何 `bm25.index()` 调用（全仓 grep 只有上述两处 `get_bm25_index`）

推论：

1. **删除后不处理 BM25，被删内容仍会被 BM25 命中并送进最终结果**（前提是该 scope 仍在调用者可见范围内且向量结果非空 —— 实测 `global` 尚有 1308 条元数据行 / 1297 个 Chroma `source_file`，`teachers` 尚有 3 个，故 `allowed_scopes` 过滤挡不住这些命中）。
   末端的 ACL 复核只校验 `scope`、不校验节点是否还存在（`query_kb.py:276-279`），挡不住这种情况。
2. 反向缺口：**启动后新入库的文件对 BM25 完全不可见**（向量检索仍能找到，只是少了一路召回）。这是既有问题，非本次引入。

`remove_scope()` 是 scope 级粒度，不能用于单文件删除，因此需要新增文件级方法（§4.3）。

**同进程证据**（决定「删除端点能否直接改这份内存索引」）：`MiniappAdapter` 继承 `BasePlatformAdapter`，`connect()` 在自身进程内起 aiohttp 服务（`adapter.py:70`、`:107-120`）；`_chat` 在事件循环里 `loop.create_future()` 并挂到 `self._pending`（`:265-267`），由 `send()` 在同一事件循环 `future.set_result(...)` 兑现（`:182-192`）。`asyncio.Future` 只能被同进程、同事件循环的代码兑现 → Agent 与小程序适配器在同一进程，`get_bm25_index()` 的模块级单例对两者是同一份。

### 2.7 实测到的数据不一致（影响删除语义）

| 现象 | 实测 | 对删除的影响 |
|---|---|---|
| 有元数据行、Chroma 无对应 `source_file` | `global` 11 个 = 10 个 jxtz 乱码行（如 `jxtz_10392_unzv285n.txt`，随 §11 步骤 0 清理）+ `西安交通大学课程调整申请表.docx`（保留） | 删除时 `node_count` 为 0（`source_file` 无节点）；必须允许「只回收元数据行」，否则这些文件永远删不掉 |
| 同一内容换名重传 → 节点挂在后一次的名字下 | 实测 `西安交通大学课程调整申请表.docx` 的 `content_hash` 在 Chroma 命中 1 个节点，但该节点的 `source_file` 是另一个文件名 | 删「没有节点的那一份」只回收配额，内容仍可通过有节点的文件名检索到（§6.2） |
| 同名文件、内容不同、同 scope | 节点按 `(scope, source_file)` 过滤；内容不同 → chunk ID 不同 → 两份节点共存于同一 `source_file` | 删除会一并移除（§6.1）。当前数据中 `global` 无同名重复行（1308 行 / 1308 文件名，实测） |

机制（代码依据，非推断）：chunk ID = `chk_{SHA-256(content_hash:scope:total_chunks)[:16]}_{idx:04d}`，**不含文件名**（`knowledge_base/models/schemas.py:7-26`）；`store_nodes` 用 `col.upsert()` 写入（`chroma_repository.py:213-252`）。因此当**字节不同、但抽取文本与分块数相同**（`content_hash` 相同）时，后一次入库会覆盖同一批节点，节点 metadata（含 `source_file`）被后一个文件名替换 —— `schemas.py:17-19` 的文档字符串正是为此说明「不加 scope 会让 upsert 静默覆盖前一个 scope 的节点」。

> 依据：`orchestrator.py:453`（`make_chunk_id(..., scope=scope)`）、`:223-246`（字节哈希早退去重）、`:262-295`（内容哈希版本去重按 `(user_id, filename, scope)`，换名即为 NEW）、`:465-470`（`source_file` 归一化为 basename）、`:644-647`（`_track_metadata`）。

### 2.8 两类入库来源：教务通知自动抓取 vs 人工上传

现网 `global` 库由两类来源构成，**两者都写 `scope=global` + `user_id=admin`**，在 `file_metadata` 层面完全同形（无 `source` 列）：

| 类别 | 写入方 | Chroma `source` | 节点数 | `global` 文件数 |
|---|---|---|---|---|
| 自动抓取 | `scripts/sync_jxtz.py`（systemd `jxtz-sync.timer`，每日 04:00）+ `scripts/batch_ingest_jxtz.py`（一次性批量） | `jxtz` | 5666（68%） | **1213（93%）** |
| 人工 / 后端 | `scripts/admin_cli.py:79`、`scripts/batch_ingest_benke.py:105`（`source="file"`）、其它平台（`wecom:XiongWei`）、`admin:fix` | `file` 2550 / `admin:fix` 41 / `wecom:XiongWei` 38 | 2629 | 95 |

> `source` 一列为 **`global` scope 内**的节点数（合计 8295 = 5666 + 2629）。全库口径另含 `teachers` 44 + `users/XiongWei` 44 + `users/openid-1` 1，其中 `wecom:*` 全库 126 = global 38 + teachers 44 + XiongWei 44、`""` 1 属 `users/openid-1`——与删除无关，仅说明两者不可混用（评审 L3）。「文件数」一列为 `file_metadata` 口径：`jxtz` 行 = 1213 条以 `^jxtz_\d+_` 开头的文件名，其中 8 条（record_id 10392、10394–10400）的节点由 `admin:fix` 重新入库，故 Chroma 侧 `source="jxtz"` 覆盖 1195 个 `source_file` / 5666 节点；人工 95 条为 `file_metadata` 口径（Chroma 侧 94 个 `source_file`，少的 1 个是 §2.7 的无节点 docx）。

> 依据：`scripts/sync_jxtz.py:32-33`（`SCOPE="global"` / `USER_ID="admin"`）、`:390`（`source="jxtz"`）；`scripts/batch_ingest_jxtz.py:27-28`、`:263`；`/etc/systemd/system/jxtz-sync.timer`（`OnCalendar=04:00:00`，`Persistent=true`）；`scripts/admin_cli.py:79`、`scripts/batch_ingest_benke.py:105`。
> 数值为 2026-09-09 实测（Chroma `embedding_metadata` 与 `file_metadata` 分组统计）。

来源标记**只存在于 Chroma 节点 metadata**（`source`），`file_metadata` 无此列（DDL 见 §2.1）。因此列表的「来源」**不查 Chroma**，而是由文件名前缀派生（D8）：

- 实测 `global` 的 1213 条通知文件名 **100% 匹配** `^jxtz_\d+_`：`sync_jxtz.py:386` 以 `jxtz_{record_id}_{safe_title}.txt` 命名临时文件入库；`orchestrator.py:533-535` 的注释明确「保留 jxtz 前缀 + 扩展名，使 `source_file` 与 VersionManager 的 filename 键一致」—— 前缀是入库侧刻意保留的稳定键
- 人工文件（95 条）实测 **0 条**命中该模式
- 通知的**完整原始标题**不在库里：文件名里的标题被 `re.sub(r'[\\/:*?"<>|]', "_", title)[:80]` 清洗过（`sync_jxtz.py:385`），但抓取账本 `data/jxtz_notices.jsonl` 保留原始 `title`（1212 条）→ D10
  - **账本没有 `record_id` 字段**（实测 key 只有 `title` / `category` / `date` / `url`）→ 关联键必须由 `url` 末段派生：`url.rstrip("/").split("/")[-1].replace(".htm", "")`，与入库侧同一规则（`sync_jxtz.py:360`）。**不得按标题模糊匹配**（评审 L12）
- ⚠️ **账本关联会暴露 10 行历史脏数据**（2026-09-09 逐行比对实测）：1213 行通知里 **1203 行的文件名 stem 与账本标题完全一致**，10 行不一致，全是乱码 stem（`jxtz_10392_unzv285n.txt` 这类）。这 10 行里 **8 行（record_id 10392、10394–10400）各有一个「正常文件名」兄弟行**共享同一 record_id → 两行解析出同一个显示名（D10），列表出现同名两行；另 2 行（10401、10402）无兄弟行。10 行在 Chroma 里**均无对应 `source_file`（0 节点）**→ 随 §11 步骤 0 清理元数据行即可

**与删除的交互（本次设计必须写清）**：

1. **抓取通知删除后不会被每日同步抓回**：`sync_jxtz.py` 以 `data/jxtz_notices.jsonl` 的 URL 为去重账本，删除不动该账本（`:311-325`、`:390`）→ 删除是永久的。
2. **但批量脚本可以恢复**：`batch_ingest_jxtz.py` 依赖 `data/jxtz_ingest_results.jsonl` 断点续跑（`:195-213`）；该结果文件丢失或重置后重跑，已删条目会重新入库。
3. **配额对 admin 无实际意义**：admin / owner 豁免计数与速率限制（`knowledge_base/core/quota_manager.py:26-28`），删抓取通知只回收账目，不释放可用配额。
4. **既有先例说明「按来源区分」是真实需求**：`batch_ingest_benke.py:71-86` 的 `--reset` 按 `source="file"` 清 Chroma 节点，但元数据用的是 `delete_user_data("admin")` —— 会连带清掉抓取通知的元数据行。本设计用 `(scope, filename)` 粒度，不重蹈这个 user_id 粒度的覆辙。

---

## 3. 关键设计决策

| # | 决策 | 理由与依据 |
|---|---|---|
| **D1** | 删除单位 = `(scope, filename)` | 节点无 `user_id`（§2.3），无法按上传者精确寻址 |
| **D2** | 列表数据源 = `file_metadata`（不是 Chroma） | 配额与去重的权威来源；Chroma 无「列文件」封装（§2.5）。今日实测「Chroma 有、元数据无」为 0 条 |
| **D3** | 删除顺序 fail-fast：`count → delete_nodes → recount → delete metadata` | `delete_nodes` 恒返回 `True`（§2.4），不复核就会出现「元数据已删、向量还在」的假成功 |
| **D4** | `dry_run` 预览 + 前端二次确认 | 公共库 1308 个真实文件，误删不可逆（§2.2）；与白名单端点风格一致 |
| **D5** | 权限矩阵与**写侧同源**，抽 `_knowledge_scopes(role, openid)` | 矩阵目前内联在 `_upload`（`adapter.py:312-320`），新端点再抄一份必然漂移 |
| **D6** | 删除成功后同步 BM25 内存索引（新增 `BM25Index.remove_file`） | 否则被删内容仍会经 BM25 进入回答（§2.6）；不处理等于功能未完成 |
| **D7** | 个人库 `users/{openid}` 只能本人操作，任何角色无例外 | 写侧矩阵注释已明确「No role can write to another user's personal KB」（`acl_filter.py:35-37`） |
| **D8** | 来源 = **文件名前缀派生**（`^jxtz_\d+_` → 教务通知，否则文件入库），**仅 admin / owner** 可见可筛 | 改写自 v1.3 的「列表不显示来源」。可见性限定理由：教务通知只存在于公共库，而公共库只有 admin / owner 能管理（§4.6 矩阵）；其它角色/库里来源恒为「文件」，展示只会是噪音。`file_metadata` 无 `source` 列（§2.8），加列需迁移 + 回填 1308 行；而前缀是入库侧刻意保留的稳定键（`orchestrator.py:533-535`），实测 1213/1213 命中、95 个人工文件 0 误命中 → 零 schema 改动 |
| **D9** | 抓取通知与人工文件**同等可删**（已拍板 2026-09-09） | 删除后每日同步不会抓回（URL 账本去重，§2.8-1），但批量脚本可恢复（§2.8-2）—— 这个前提必须写进预览文案与本文档，不能假装是绝对永久 |
| **D10** | 教务通知的显示名 = **抓取账本里的原始标题** | `data/jxtz_notices.jsonl` 每条含 `title`（原始、未截断）；用文件名里的 record_id 关联。实测 1212 条账本覆盖 `file_metadata` 全部 1213 条通知（0 缺失），并修正 10 条文件名乱码（如 `jxtz_10392_unzv285n.txt` → `[学籍管理]2026年电子与信息学部接收本科生转专业考试安排`）。**已知副作用**：8 行乱码 stem 与兄弟行共享 record_id → 两行显示名相同（历史数据问题，随 §11 步骤 0 清理；另 2 行为空壳）。不加列、不回填、不改入库链路 |
| **D11** | 访客（guest）**不参与知识库**：不可上传、不可列、不可删 | 知识库自身的写权限矩阵早已是 `"guest": set()`（`acl_filter.py:43`），读侧 guest 仅 `global`（`:177-178`）；而小程序 `_upload` 另起炉灶、把所有角色都放进个人库（`adapter.py:312-320`）—— 现状是「访客能上传、却检索不到自己的上传」（死数据）。本次把两端对齐到 `WRITE_PERMISSIONS` |
| **D12** | 批量删除 = 单文件删除的**循环**，上限 50，逐条返回结果 | 复用同一个 `_knowledge_delete_one()`（§4.2），避免两套语义；上限防止单请求串行几十次 Chroma 往返导致超时 |
| **D13** | 删除确认文案**强制**含「删除后不可撤回」 | 不做回收站（§1.3），唯一的防误删手段就是 `dry_run` 预览 + 确认文案 |
| **D14** | 孤儿清理 = **只读扫描 + 人工勾选 + 复用批量删除**，不做一键全删 | 唯一安全的「脏数据」判据是「元数据行在、Chroma 0 节点」（§4.5）；`count_nodes` 出错静默返回 0（`chroma_repository.py:397-415`）→ 自动删在 chroma-server 抖动时会把整库判成孤儿。且 0 节点 ≠ 内容丢失（内容可能挂在另一个文件名下，§2.7 第 2 行），删它只回收配额账目 |
| **D15** | 小程序未登记用户默认角色 = **guest**；其它平台仍保持 student | 现状 `DEFAULT_ROLE = student`（`role_store.py:51`）是两平台共用兜底值，且 `_upload` 不校验认证状态 → 新用户能上传。全局改会连带改变（历史：招生助手时代）未知联系人的认证状态分支（`management_flow/service.py:475`）→ 按平台给默认值（§4.7.5） |
| **D16** | 对话回答的引用改用**显示名**（复用 D8 / D10 规则） | 用户最常接触知识库的地方是对话，而检索工具拼的是内部文件名（`query_kb.py:359` 用 `node.source_file`）→ 乱码文件在对话里就是 `[jxtz_10392_unzv285n.txt]`。跨仓改动（另一个 git 仓库），单独授权 + 单独提交 + 部署（§4.7.4） |
| **D17** | 个人库列表返回**入库状态**（`ok` / `empty`），共享库不返回 | 个人库文件数少，逐文件计数可接受；共享库沿用「不返回节点数」（§4.1）的结论。上传失败的行从此在列表可见——现在只在那一次上传响应里能看到（`adapter.py:380-385`）。查询失败时**省略状态字段**，不把「查不到」显示成「空」 |
| **D18** | 列表返回**个人配额**（`used` / `limit`） | 后端已有 `get_user_file_count`（`sqlite_store.py:66`）与 `QuotaManager.max_user_files`（默认 50，`quota_manager.py:11`），UI 从未暴露。admin / owner 豁免计数（`quota_manager.py:26-28`）→ 返回 `null` |
| **D19** | 原文预览 = **从节点内容拼回文本**（只读、截断 2 万字），不恢复原文存储 | 行业标配；但原文件入库后不保留（`sync_jxtz.py:391` 入库后 `unlink`、上传件 24h 清理 `adapter.py:2334-2346`）→ 恢复存储成本高且无必要，正文存在 Chroma 的 document 列（存储层键 `chroma:document`）|
| **D20** | 管理员可查**删除操作历史**（只读） | 审计日志已经在写（§10 #5），但只有 DBA 能看；`AuditLogger.query`（`audit_logger.py:242`）现成，成本低 |

---

## 4. 后端设计

### 4.1 `GET /api/methods/knowledge?scope=…` —— 列出已入库文件

- 鉴权：`Bearer` token（同其它 methods 端点，`adapter.py:2075 _authorize`）
- 权限：`scope` 必须落在调用者的 §4.6 矩阵内，否则 403 `{error:"permission denied", role, required:[…]}`；**访客（guest）一律 403**（D11）
- 响应：

  ```json
  {
    "success": true,
    "scope": "global",
    "total": 1308,
    "files": [
      {
        "filename": "jxtz_10443_[专业建设]关于2026-2027学年第一学期本科课程研究生助教聘任工作通知.txt",
        "display_name": "[专业建设]关于2026-2027学年第一学期本科课程研究生助教聘任工作通知",
        "source": "jxtz",
        "node_count": null,
        "uploaders": [{"user_id": "admin", "ingested_at": "2026-09-08 04:00:12"}]
      }
    ]
  }
  ```

- **按 `filename` 聚合**：共享库中同一文件名可对应多行（主键 `(user_id, filename, scope)`，`sqlite_store.py:39-47`），`uploaders` 列出全部上传者与入库时间；个人库只有一行
- **`source`（D8）**：文件名匹配 `^jxtz_\d+_` → `"jxtz"`，否则 `"file"`。纯派生，不查 Chroma。接口对所有有权限的角色一致返回；**可见性由前端按角色控制**（仅 admin / owner 渲染筛选条与来源标签，§5）
- **`display_name`（D10）**：`source == "jxtz"` → 用文件名里的 record_id 查抓取账本（账本无 `record_id` 列，索引在加载时由 `url` 末段派生，§2.8）（`os.path.dirname(CHROMA_DB_PATH)/jxtz_notices.jsonl`，与 `quota.db` 同一套解析，`bootstrap.py:133`）的原始 `title`；账本缺该条时回退为「剥掉 `jxtz_<id>_` 前缀与扩展名的文件名」。非 jxtz → 直接等于 `filename`。账本按 mtime 缓存，只在端点进程内读一次
  - **不要用 `__file__` 相对路径定位账本**：账本在 `data/` 且被 `.gitignore` 忽略（`.gitignore:6`），而部署副本 `agent4som-hermesagent/` 下**没有 `data/` 目录**（插件代码从副本加载，进程 CWD 是源仓库 `agent4som/`）→ 只有按 `CHROMA_DB_PATH` 同级解析才稳
- **不返回节点数**：每文件一次 Chroma 查询即 N+1，节点数只在删除预览里算一次（`node_count` 恒为 `null`，前端不显示）
- **筛选在前端本地做**（列表已全量返回），接口不加 `source` 参数
- **v1.5 扩展**：个人库 scope 的响应顶层额外返回 `quota`，`files[]` 每个文件额外返回 `status`（§4.7.1）
- 空库 / 无权限 scope 均返回 200 + 空列表（不区分，避免探测）

### 4.2 `DELETE /api/methods/knowledge` —— 删除文件（出库）

- body：`{"scope": "teachers", "filename": "聘任协议.pdf", "dry_run": false}`
- 权限：同 §4.1
- **删除顺序（fail-fast）**：

  1. 参数与权限校验；`node_count = count_nodes(scope, filename, raise_on_error=True)`
     - **边界校验（L5）**：`scope` / `filename` 必须是非空 `str`，否则 400 `{"error": "invalid_scope"|"invalid_filename"}`——非字符串进 Chroma `where` 会变成 500（CLAUDE.md §7：校验只放系统边界）
     - **必须严格计数（本次新增参数）**：现有 `count_nodes` 在 Chroma 出错时返回 0（`chroma_repository.py:397-415`），`delete_nodes` 出错也恒返回 `True`（`:365-395`）。沿用静默语义会在 chroma-server 不可用时产生**不可逆不一致**：删除其实失败 → 复核读数 0 → 误判成功 → 元数据行被删、节点仍在库里，而列表再也看不到它们。改为出错即抛 → 503 `{"error": "chroma_unavailable"}`，此时不写任何东西，用户可重试
     - 注意 `count_nodes` 的 collection 获取在 `try` **之外**（`:404`）→ 客户端获取失败时它本就会抛 `RuntimeError`，而 `delete_nodes` 仍返回 `True`（`:386` 在 `try` 内）——**不能**依赖 `delete_nodes` 的返回值判断成功（评审 §6.1）
  2. `dry_run=true` → 返回预览，**不写任何东西**：

     ```json
     {"success": true, "dry_run": true,
      "preview": {"scope": "global", "filename": "jxtz_10443_[专业建设]关于….txt",
                  "node_count": 12, "source": "jxtz",
                  "metadata_rows": [{"user_id": "admin", "ingested_at": "…"}]}}
     ```

     来源判定：`source = get_file_source(scope, filename)`（单次 `col.get(where=…, limit=1, include=["metadatas"])`，新增方法见 §4.6）。`jxtz` → 前端提示「系统自动抓取」；`file` / `wecom:*` / `admin:fix` / 空 → 「人工上传」（§2.8、§5）。预览里的 `filename` 同时返回 `display_name`（D10），弹窗文案用显示名。`metadata_rows`（以及第 4 步逐行要删的行）**取自 `list_file_metadata_by_scope(scope)` 按 `filename` 过滤**——`sqlite_store` 现有方法只有按 `user_id` 取单行的 `get_file_metadata`（`:94`）与 `get_file_metadata_any_user`（`:108`），没有「按文件列全部上传者」的查询；global 一次 SELECT 1308 行、过滤开销可忽略，不为此再加方法

  3. 真删：`delete_nodes(scope, source_file=filename)`
  4. **复核** `count_nodes(scope, filename, raise_on_error=True)`：
     - `== 0` → 删除该 `(scope, filename)` 的**全部** `file_metadata` 行（`delete_file_metadata` 逐行）→ `{"deleted": true, "nodes_removed": N, "metadata_rows_removed": M}`
     - `> 0` → 500 `{"error": "partial_delete", "nodes_remaining": N}`，**保留元数据行**（列表里仍可见、可重试），同时 WARNING 日志
  5. 前置 404：`node_count == 0` **且** 无任何元数据行 → `{"error": "file_not_found"}`（前端提示刷新列表）
  6. 前置 200（无需删除）：`node_count == 0` 但存在元数据行 → 直接走第 4 步的元数据清理，返回 `nodes_removed: 0`（§2.7 的 11 个孤儿行属于此类，是**清理路径**而非错误）

- 幂等性：重复调用返回 404 `file_not_found`，不报 500
- 共享库语义：删除作用于整个 `(scope, filename)`，`metadata_rows` 会列出受影响的上传者，预览弹窗必须写清楚（§5）
- **单文件逻辑抽成 `_knowledge_delete_one(scope, filename, *, openid, role, op) -> dict`**，单删端点与 §4.4 批量端点共用，避免两套语义（D12）。后三个 keyword-only 参数是审计事件所需（`user_id` / `role` / `detail.op` 只在 handler 里，1.6.4）
- 前端确认文案必须含「**删除后不可撤回**」（D13、§5）
- **执行线程**：删除含 Chroma HTTP 调用与 SQLite 写，与入库同类，必须走 `asyncio.to_thread`，否则阻塞整个网关（既有做法与注释：`adapter.py:376-377`「入库含 MinerU/嵌入等长耗时同步调用，必须离开事件循环」）
- **鉴权顺序（1.6.6）**：`_authorize` **先于** body 解析。DELETE 的 `scope` 在 body 里，若先解析 body，无 token 的空 body 会先撞 400 `invalid JSON body`——与白名单 DELETE 端点（`_authorize` 在 `_json_body` 之前，`adapter.py:1951-1953`）及 §8「无 token → 401」的口径不一致。现序：`_knowledge_identity()` 401 → `_json_body()` 400 → `_knowledge_scope_guard()` 403；矩阵仍只由 `_knowledge_scopes()` 定义
- 删除成功后写审计事件（§10 待拍板 #5）；**单一事件类型 `kb_delete`**，单删 / 批量差异放 `detail.op`（M1）：

  ```python
  audit.log_event(event_type="kb_delete", user_id=openid, role=role,
                  filename=filename, scope=scope, node_count=node_count,
                  detail={"op": "single", "metadata_rows_removed": M})
  ```

  - **写入失败不得让「已成功的删除」变成 500**（L4）：`log_event` 包 `try/except` + `logger.warning`（先例 `role_store.py:128-136`），节点与元数据行此时已删净
  - `log_event` 的 `event_type` 之后全是 **keyword-only**（`audit_logger.py:133-157` 的 `*`），实现时别传位置参数

### 4.3 BM25 内存索引同步（D6）

在 `knowledge_base/retrieval/bm25_search.py` 新增文件级删除（与 `remove_scope` 并列，约 10 行）：

```python
def remove_file(self, scope: str, source_file: str) -> int:
    """Remove all nodes of *source_file* in *scope*. Returns the count removed."""
    with self._lock:
        to_remove = [nid for nid, n in self._nodes.items()
                     if n.scope == scope and n.source_file == source_file]
        for nid in to_remove:
            self._remove_node(nid)
        return len(to_remove)
```

调用点放在 adapter 复核通过之后：

```python
try:
    from knowledge_base.retrieval.bm25_search import get_bm25_index
    bm25_removed = get_bm25_index().remove_file(scope, filename)
except Exception:
    logger.warning("BM25 index sync failed after delete (scope=%s file=%s)", scope, filename, exc_info=True)
```

要点：

- 用 `scope` + `source_file` **精确匹配**（`remove_scope` 是前缀匹配 scope，不能复用）
- best-effort：BM25 同步失败不影响删除结果（Chroma 节点已删是既成事实），仅记 WARNING；此时该进程的 BM25 索引仍持有旧节点，直到 Gateway 重启 —— 重启时 `kb_init` 按当时的 Chroma 状态重新灌入（`gateway/hooks/kb_init/handler.py:82-127`）
- 若 `get_bm25_index()` 尚未灌入，懒加载会用**删除后**的 Chroma 状态重建索引（`bm25_search.py:153-169`），结果同样正确
- 已知限制：本次**不修**入库侧（新文件对 BM25 不可见）。理由：删除侧的漏洞是「已移除的内容仍被引用」（隐私 / 正确性），入库侧只是「少一路召回」（向量检索仍可达）——只补一侧是刻意取舍，且避免扩大改动面

### 4.4 `DELETE /api/methods/knowledge/batch` —— 批量删除（D12）

- body：`{"scope": "global", "filenames": ["a.txt", "b.txt"], "dry_run": false}`
- 权限：同 §4.1（guest 一律 403）
- **上限 50**：超出返回 400 `{"error": "too_many_files", "max": 50}`。理由：逐条串行执行，每个文件 2–3 次 Chroma 往返（§4.2），再高会撞上小程序端请求超时（实测 50 文件约 1.5 s，评审 §6.5）
- **边界校验（L5）**：`filenames` 必须是 `list[str]`，逐元素非空、**去重后**再计数与执行；含非字符串元素 → 400 `{"error": "invalid_filename"}`
- `dry_run=true` → 对每个文件执行 §4.2 第 2 步的预览，返回汇总：

  ```json
  {"success": true, "dry_run": true, "scope": "global",
   "total_nodes": 87, "by_source": {"jxtz": 80, "file": 7},
   "items": [{"filename": "…", "display_name": "…", "source": "jxtz",
              "node_count": 12, "metadata_rows": [{"user_id": "admin", "ingested_at": "…"}]}]}
  ```

- 真删：**逐条**调用 `_knowledge_delete_one(scope, filename)`，**部分失败不回滚整批**：

  ```json
  {"success": true, "scope": "global", "deleted": 48, "failed": 2,
   "results": [{"filename": "…", "ok": true, "nodes_removed": 12, "metadata_rows_removed": 1},
               {"filename": "…", "ok": false, "error": "partial_delete", "nodes_remaining": 3}]}
  ```

  - 成功项：与单删完全一致（节点删净 → 元数据行删净 → BM25 同步 → 审计一条 `kb_delete`，`detail.op="batch"`）
  - 失败项：**保留元数据行**（列表里仍在、可重试），错误码与单删一致（`partial_delete` / `chroma_unavailable` / `file_not_found`）
  - 整体 HTTP 200（请求本身合法）；前端按 `results` 逐条展示，失败项标红并给出原因
  - 不因单个文件失败而中断整批 —— 否则一个孤儿行会让整批回滚
- 幂等：重复提交同一批 → 已删文件返回 `file_not_found`，不影响其它文件
- 执行线程：同 §4.2，`asyncio.to_thread`

### 4.5 `GET /api/methods/knowledge/orphans?scope=…` —— 扫描孤儿元数据（D14）

- 目的：找出「`file_metadata` 有行、Chroma 里 0 节点」的残留行（§2.7 第 1 行、§2.8 的 10 行）。**只读，不写任何东西**；删除仍走 §4.2 / §4.4
- 权限：同 §4.1（guest 403）
- 扫描方式：**一次** `col.get(where={"scope": scope}, include=["metadatas"])` 统计该 scope 每个 `source_file` 的节点数（`list_source_files` 返回 `dict[str, int]`；文件名集合 = 键，`scanned_nodes` = 值求和，1.6.4）。**不用**逐文件 `count_nodes`：global 1308 个文件会变成 1308 次 Chroma 往返，且出错静默返回 0（`chroma_repository.py:397-415`）。实测全库 8384 节点、`col.get(include=["metadatas"])` 单次返回 130,784 个键值（`global` 单 scope 129,384；完整 metadatas JSON 约 5.9 MiB，只投影 `source_file`/`source` 约 0.65 MiB），耗时 **0.54–0.76 s**（2026-09-09 本机 4 次实测）→ 单次调用可接受
- 判定：`orphans = 该 scope 的 file_metadata 文件名集合 − Chroma 的 source_file 集合`
- **护栏（必须）**：
  - `col.get` 出错 → 503 `{"error": "chroma_unavailable"}`，**不返回任何候选**（沿用 §4.2 第 1 步的严格语义）
  - **`collection.count() == 0`（整集合为空）→ 同样 503**（防止「Chroma 空/坏被当成整库孤儿」）。判据用**集合级**而非 scope 级：`list_source_files` 本身「出错即抛」，某 scope 合法地只剩元数据行（节点被删光）时应当照常返回候选，否则该端点永远 503、无法用它清理（评审 L9）
- 响应：

  ```json
  {"success": true, "scope": "global", "scanned_nodes": 8295, "orphans": [
    {"filename": "jxtz_10392_unzv285n.txt", "display_name": "…",
     "uploaders": [{"user_id": "admin", "ingested_at": "2026-08-12 04:00:07"}]}
  ]}
  ```

- **候选是快照**：正在入库中的文件会短暂像孤儿（§7.4 无文件锁）→ 只列候选、由管理员勾选；删除前批量 `dry_run` 会**当场重新计数**，被重新入库的文件会显示真实节点数（§4.4）
- **不提供「一键全删」**：判据依赖 Chroma 的返回，自动删在 chroma-server 抖动时会把整库判成孤儿（D14）
- 反向孤儿（Chroma 有节点、元数据无行）现网 0 条（D2），本次不处理

### 4.6 代码改动清单（最小）

| 文件 | 改动 |
|---|---|
| `knowledge_base/core/sqlite_store.py` | + `list_file_metadata_by_scope(scope) -> list[dict]`（只读 SELECT，按 `filename` 排序）。列表、单删预览的 `metadata_rows`、单删第 4 步的逐行删**都从这份结果按 `filename` 过滤**（PK 是 `(user_id, filename, scope)`，按文件查同样全表扫；global 1308 行，无需再加按文件查询） |
| `knowledge_base/repository/chroma_repository.py` | + `list_file_metadata_by_scope(scope)` 透传给 `SqliteStore`（沿用现有封装风格，`:641` 附近）+ `get_file_source(scope, source_file) -> str`（单次 `col.get(limit=1, include=["metadatas"])`，供 dry_run 预览判来源）+ `count_nodes(..., raise_on_error: bool = False)` 可选参数（默认保持现有静默语义，仅删除端点传 `True`）+ `list_source_files(scope) -> dict[str, int]`（孤儿扫描用，单次 `col.get(where=…, include=["metadatas"])` 统计每个 `source_file` 的节点数，**出错即抛**，§4.5）+ `count_collection_nodes() -> int`（集合级 `count()`，供 §4.5「整集合为空 → 503」护栏；**出错即抛**，不静默返回 0） |
| `knowledge_base/retrieval/bm25_search.py` | + `BM25Index.remove_file(scope, source_file) -> int`（§4.3） |
| `knowledge_base/retrieval/response_verifier.py` | + `get_audit_logger() -> AuditLogger \| None`（此前只有 `init_audit_logger`，§4.2 的审计写入无公开入口；1.6.4） |
| `knowledge_base/core/display_names.py`（**新增**） | `resolve_display_name(scope, source_file) -> str`：D8 来源派生（`^jxtz_\d+_`）+ D10 账本标题（读 `os.path.dirname(CHROMA_DB_PATH)/jxtz_notices.jsonl`，与 `quota.db` 同源解析；索引在加载时由 `url` 末段派生 record_id，按 mtime 缓存）。**放 `knowledge_base/` 而不是 adapter**：`query_kb.py` 在另一个 git 仓、无法 import adapter，两处各写一份必然漂移（评审 L7） |
| `~/.hermes/plugins/miniapp-platform/adapter.py` | + 4 条路由（`:128` 之后：GET / DELETE / DELETE batch / GET orphans）+ `_method_knowledge_list` / `_method_knowledge_delete` / `_method_knowledge_batch_delete` / `_method_knowledge_orphans` + `_knowledge_delete_one()` + `_knowledge_scopes(role, openid)`；显示名统一调 `knowledge_base.core.display_names` |
| 同上 `_upload`（`:312-320`） | 改为调用 `_knowledge_scopes()`（矩阵只留一处），**并把 guest 排除在外**（D11） |
| 同上 `_auto_ingest`（`:385-400`） | 补 `source="file"`（`ingest_file` 当前未传，落库为空字符串，`orchestrator.py:124`）—— 让 Chroma 侧来源语义与列表派生一致；**不影响**列表筛选（筛选只读文件名前缀） |
| `miniprogram-framework-frontend/pages/knowledge/*` | 新建管理页（§5） |
| `miniprogram-framework-frontend/pages/methods/methods.{wxml,js}` | 入口卡（访客隐藏）+ `tapKnowledgeManage` |
| `miniprogram-framework-frontend/config/api.js`、`scripts/build.js` | 路由常量 / `SHARED_PAGES`（`app.json` 是 `build.js` 的**生成产物**且被 `.gitignore:11` 忽略，**不要手改**，评审 §6.4） |

v1.5 补充（§4.7）：

| 模块 | 改动 |
|---|---|
| `knowledge_base/auth/role_store.py` | + `DEFAULT_ROLE_BY_PLATFORM` / `default_role_for(platform)`；`get_role`（def `:93`，回退在 `:101`）改用它、`resolve_role` 去掉常量比较（§4.7.5，M2） |
| `knowledge_base/repository/chroma_repository.py` | + `get_file_documents(scope, source_file) -> list[str]`（原文预览，单次 `col.get(include=["documents"])`，按 `node_id` 排序，出错即抛） |
| `~/.hermes/plugins/miniapp-platform/adapter.py` | + 2 条路由（`GET /knowledge/content`、`GET /knowledge/audit`）+ 列表响应补顶层 `quota` + 每文件 `status`（§4.7.1–§4.7.3） |
| `agent4som-hermesagent/hermes-agent/tools/query_kb.py`（**另一个 git 仓库**） | 引用改用显示名：`from knowledge_base.core.display_names import resolve_display_name`（D16、§4.7.4）；需单独授权 + 单独提交 + 重启网关 |

权限矩阵（`_knowledge_scopes`，与写侧同源）：

| 角色 | `users/{self}` | `teachers` | `global` |
|---|---|---|---|
| **guest** | **—（不可列 / 不可删 / 不可传）** | — | — |
| student | 列 + 删（仅本人） | — | — |
| teacher | 列 + 删（仅本人） | 列 + 删 | — |
| admin / owner | 列 + 删（仅本人） | 列 + 删 | 列 + 删 |

> 依据：写侧矩阵 `adapter.py:312-320`、`knowledge_base/retrieval/acl_filter.py:38-44`（`WRITE_PERMISSIONS`，guest 为空集在 `:43`）、`:177-178`（guest 读侧仅 `global`）、`:147`（`get_allowed_scopes`）。

---

### 4.7 v1.5 补充：可观测性、原文预览与默认角色（D15–D20）

#### 4.7.1 列表响应扩展（§4.1）

`GET /api/methods/knowledge` 的响应增加两个字段，**只对个人库 scope（`users/{self}`）填充**：

| 字段 | 位置 | 含义 | 计算方式 |
|---|---|---|---|
| `quota` | **响应顶层** | `{"used": 12, "limit": 50}` | `get_user_file_count(user_id)`（`sqlite_store.py:66`）+ `QuotaManager.max_user_files`（默认 50，`quota_manager.py:11`）；admin / owner 豁免 → `null`。**每用户一个值，挂顶层**，不在每个文件对象里重复 |
| `status` | `files[]` 每项 | `"ok"` / `"empty"` | `count_nodes(scope, filename, raise_on_error=True)`：`> 0` → `ok`；`== 0` → `empty`（该文件没有内容，可删）。**查询失败时省略整个字段**——不能把「查不到」显示成「空」 |

只在个人库算的理由：个人库文件数少（个位数），逐文件计数可接受；共享库 1308 个文件沿用 §4.1「不返回节点数」的结论，0 节点行由管理员的孤儿扫描（§4.5）覆盖。

**实现（1.6.8 回写）**：`_knowledge_personal_extras(openid, role, scope, filenames)`（adapter，`asyncio.to_thread` 内执行）只在 `scope == f"users/{openid}"` 时被调用；`quota` 键**恒存在**于响应顶层（admin / owner → `null`）；逐文件 `count_nodes(..., raise_on_error=True)` 各自包 `try/except`，失败即**省略该文件的 `status` 键**（列表仍 200）。

#### 4.7.2 `GET /api/methods/knowledge/content?scope=…&filename=…` —— 原文只读预览（D19）

- 权限：同 §4.2（scope 在调用者矩阵内；guest 403）
- 取内容：`col.get(where={"scope": scope, "source_file": filename}, include=["documents", "metadatas"])`，按 `node_id` 顺序拼接 `chroma:document`
- 响应：`{"success": true, "filename": …, "display_name": …, "text": "…", "truncated": true, "node_count": 37}`
- **截断**：只返回前 **20,000 字**；`truncated=true` 时前端提示「仅显示前 2 万字」
- 文件不存在（0 节点）→ 404 `{"error": "file_not_found"}`
- 只读，不写任何东西。**说明**：入库后原始文件不保留（`sync_jxtz.py:391` 入库即 `unlink`；上传件 `expires_at = +1 day`，`adapter.py:2304-2305`、到期清理 `:2334-2346`），所以预览 = 从节点内容拼回文本，不提供下载

**实现（1.6.8 回写）**：取内容走新增的 `ChromaRepository.get_file_documents(scope, source_file)`（`chroma_repository.py:463-477`，单次 `col.get(include=["documents"])` + 按 `node_id` 排序；**出错即抛**，静默空列表会被误判成「文件不存在」）。端点 `_method_knowledge_content`（`adapter.py:1748`）：缺 `filename` → 400 `invalid_filename`；`get_file_documents` 抛错 → 503 `chroma_unavailable`；0 节点 → 404 `file_not_found`；`node_count` = 拼回的节点数。

#### 4.7.3 `GET /api/methods/knowledge/audit?limit=…` —— 删除操作历史（D20）

- 权限：**仅 admin / owner**（`_knowledge_scopes` 之外单独判定；guest / student / teacher 403）
- 数据源：`AuditLogger.query(event_type="kb_delete", limit=…)`（`audit_logger.py:242-247`）——**单一事件类型**，单删 / 批量由 `detail.op` 区分（M1）
  - 原 v1.5 写「过滤 `event_type in ("delete","batch_delete")`」**照字面无法实现**：`query()` 只支持单值等值（`:250-252`）、无 IN 列表、排序硬编码 `ORDER BY id DESC`（`:256`），且 §4.2 写的是 `kb_delete` → 三个口径不一致会让该端点**恒返回空且不报错**
  - **不设 `?scope=` 参数**（`query()` 无该参数）；确需按 scope 过滤时在前端对返回结果筛
- 响应：`{"success": true, "events": [{"time": …, "user_id": …, "role": …, "scope": …, "filename": …, "node_count": …, "detail": {…}}]}`，默认 50 条、`id` 倒序（即时间倒序）
  - `detail` **无需端点再解析**：`query()` 已把 TEXT 列 `json.loads` 成 dict（`audit_logger.py:263-268`）——评审 N2 经复核**不成立**，此处照实记录以免实施时重复处理
- 只读；不改审计表结构

**实现（1.6.8 回写）**：`_method_knowledge_audit`（`adapter.py:1774`）——`limit` 缺省 50、非正整数（`isdigit` 判定）→ 400 `invalid_limit`；`get_audit_logger()` 返回 `None` → 503 `audit_unavailable`；`query()` 在 `asyncio.to_thread` 里执行；事件字段映射为 `timestamp → time`，`user_id` / `role` / `scope` / `filename` / `node_count` / `detail` 同名透传。

#### 4.7.4 对话引用改用显示名（D16，跨仓）

- 现状：检索工具拼给模型的上下文是 `参考{i}: [{node.source_file}]`（`query_kb.py:359`）→ 用户看到内部文件名；乱码文件则是 `[jxtz_10392_unzv285n.txt]`
- 改法：**import 同一个函数**，不复制逻辑：`from knowledge_base.core.display_names import resolve_display_name`（函数随 §4.6 下沉到 `knowledge_base/`），替换 `node.source_file`。实测 `query_kb.py` 已有 14 处函数内 `import knowledge_base.*`（`:130`/`:163-164`/`:172`/`:251`/`:288` 等）、无 `sys.path` 特殊处理 → 两仓共用同一份实现（评审 L7）
- **跨仓**：`~/.hermes/hermes-agent` 与 `/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/hermes-agent` 是同一份（inode 相同），属**另一个 git 仓库** → 需单独授权、单独提交、按部署流程重启网关
- 账本路径同样按 `os.path.dirname(CHROMA_DB_PATH)/jxtz_notices.jsonl` 解析（§4.1）；账本缺失时回退「剥 `jxtz_<id>_` 前缀与扩展名的文件名」

#### 4.7.5 小程序默认角色 = 访客（D15）

- 现状：`DEFAULT_ROLE = ROLE_STUDENT`（`role_store.py:51`），两个平台的未登记用户默认学生 → 小程序新用户能上传到个人库（`_upload` 也不校验认证状态）
- 改法：新增按平台默认值

  ```python
  DEFAULT_ROLE_BY_PLATFORM = {"miniapp": ROLE_GUEST}
  def default_role_for(platform: str) -> str:
      return DEFAULT_ROLE_BY_PLATFORM.get(platform, DEFAULT_ROLE)
  ```

  **改法（M2，勿按字面「`:101` 与 `:184` 都换成 `default_role_for`」）**：

  ```python
  # get_role:110  →  platform_roles.get(user_id, default_role_for(platform))
  # resolve_role:189-193  →  去掉与常量的比较，直接返回 get_role 的结果
      stored = get_role(platform, user_id)
      return stored          # 原为：if stored != DEFAULT_ROLE: return stored; return DEFAULT_ROLE
  ```

  - **为什么必须去掉常量比较**：`resolve_role` 原逻辑用 `stored != DEFAULT_ROLE`（模块常量 `"student"`）判断「是否显式登记」。miniapp 默认改成 guest 后，**已登记为 student 的小程序用户** `stored == "student" == DEFAULT_ROLE` → 判定为「未登记」→ 落到 `:193` 返回 `default_role_for("miniapp")` = **guest** → **静默降级**（表现为学生不能上传、看不到个人库，且不报错）。今天 `~/.hermes/roles.json` 的 miniapp 只有 1 个 admin，所以这是潜伏缺陷；但 miniapp 的 student 正是管理员经电话白名单 / 变更身份授予的常规角色（`adapter.py:654-661`）
  - `DEFAULT_ROLE` 常量保留（其它平台 / 未知平台仍是 student，既有测试与调用不受影响）
- 效果：小程序未登记用户 = 访客 → 不能上传、知识库入口不可见；仍可在对话里查公共库（读侧 guest = `global`，`acl_filter.py:177-178`）
- **不影响**其它平台：（历史：招生助手时代）未知联系人仍是 student，认证状态分支不变（`management_flow/service.py:475`）
- **不影响入职路径**（本次补验）：`_method_identity`（`adapter.py:544`）走 `resolve_role("miniapp", …)` → 前端角色自动贯通；`_method_phone_bind` 只有 `_authorize`、**无角色门** → 新访客仍能绑手机号并经电话白名单拿到角色
- 一致性：`ingest_file` 内部用 `resolve_role(platform, user_id)` 判定 admin/owner 配额豁免（`orchestrator.py:205`），平台参数已是 `"miniapp"`，语义一致
- **顺带**：`unset_role` 的审计把 `role` / `detail.new_role` 写死成 `DEFAULT_ROLE`（`role_store.py:155-165`）→ 改为写 `default_role_for(platform)`
- **已实施（1.6.8）**：`DEFAULT_ROLE_BY_PLATFORM` / `default_role_for`（`role_store.py:54-59`）、`get_role` 回退（`:110`）、`resolve_role` 去常量比较（`:189-193`）、`unset_role` 审计（`:157-162`）。回归网 = `tests/knowledge_base/auth/test_role_store.py::TestPlatformDefaults`（6 项，含 M2 的「已登记 student 不降级」）；隔离 harness 另覆盖未登记 miniapp = guest 的端到端角色判定

---

## 5. 前端设计

新建 `pages/knowledge/`（`knowledge.{js,json,wxml,wxss}`，以 `pages/phone-whitelist/` 为模板）。

- **入口**：`pages/methods/` 的「我的功能」节，「入库知识」卡（`methods.wxml:39-46`）下方新增「知识库管理」卡
  - **两张卡都只对非访客显示**：`wx:if="{{role !== 'guest'}}"`（D11）；`role` 已由 `_method_identity` 下发（`methods.js:47-51`），且 `tapIngestKnowledge` 自身也要加 guard（现在对所有角色都给出「个人知识库」选项，`methods.js:154-174`）
  - 不进「管理员功能」节 —— 教师 / 学生也有个人库
- **页面结构**：
  - 顶部 scope 切换（按角色显示：个人 / 教师 / 公共；切换即重新拉列表，默认个人）
  - **来源筛选（D8）：只对 admin / owner 显示** —— `wx:if="{{role === 'admin' || role === 'owner'}}"`。三个选项：全部 / 文件入库 / 教务通知，**默认全部**，本地过滤（列表已全量返回，不新增接口参数）。理由：教务通知只存在于公共库，而公共库只有 admin / owner 能管理（§4.6 矩阵）
  - 列表行：**显示名**（教务通知 = 原始标题；文件 = 文件名）+ **来源标签**（文件 / 教务通知，**同样只对 admin / owner 显示**）+ 上传者 + 入库时间
  - 页面需拉 `_method_identity` 拿 `role`（与 methods 页同源，`methods.js:47-51`）
  - 本地按**显示名**搜索
  - 每行「删除」→ 先 `dry_run` → 弹窗（多条可叠加）：
    - 常规：「将删除《X》的 N 个知识片段，**删除后不可撤回**。」
    - `metadata_rows` 多于一行时追加：「该文件由 A、B 上传，删除将同时移除两人的记录。」
    - `source == "jxtz"` 时追加：「该文件由系统从教务处网站自动抓取，删除后不会自动恢复。」
    - `node_count == 0` 时改为：「《X》未占用知识片段，删除仅清理记录。若该内容曾以其它文件名入库，需单独删除。**删除后不可撤回**。」（L10：入库管线的 REPLACE 会连带清理「剥扩展名」变体，`orchestrator.py:318-322`）
  - **批量删除（§4.4）**：行首勾选 + 顶部「全选」+「删除选中（N）」→ 一次 `dry_run` → 汇总弹窗：「将删除 N 个文件、共 M 个知识片段（教务通知 X 个 / 文件 Y 个），**删除后不可撤回**。」→ 确认后逐条执行，结果里失败项标红并给出原因，列表刷新
  - **孤儿扫描（D14、§4.5）：只对 admin / owner 显示的「扫描孤儿」按钮** → 调 GET orphans → 列出候选（显示名 + 上传者 + 入库时间）→ 勾选 → 走**批量删除**的汇总确认（同样强制「删除后不可撤回」）；扫不出结果时提示「未发现可清理的记录」；503 时提示「知识库服务暂时不可用，请稍后重试」（不展示空列表，避免被误读为「没有孤儿」）
  - **入库状态与配额（v1.5、§4.7.1）**：个人库每行显示状态标签——`status == "empty"` → 「未完成」（附提示「该文件没有内容，可以删除」）；`status` 缺失 → 不显示标签。页头显示「已用 12 / 50」（`quota` 为 `null` 时不显示）
  - **原文预览（v1.5、§4.7.2）**：点文件名 → 只读预览页显示 `text`；`truncated` 时底部提示「仅显示前 2 万字」；404 → 「文件已不存在，已为你刷新」+ 自动返回。**实现为独立页面** `pages/knowledge-preview/`（`navigateTo` + `encodeURIComponent(scope/filename)`）
  - **操作历史（v1.5、§4.7.3，仅 admin / owner）**：知识库页顶部「操作历史」入口 → 只读列表（时间 / 操作者 / 文件 / 片段数），默认 50 条。**实现为页内页签**（`mode: 'files' | 'audit'`，返回按钮切回文件列表；403 → 失败态提示）
  - 确认后真删 → 成功刷新列表 + toast；404 → 「文件已不存在，已为你刷新」
  - 空态 / 加载态 / 失败态
- **文案约束**（沿用白名单页教训）：不得出现 `scope`、`Chroma`、`元数据`、`roles.json` 等后端术语，统一用「文件 / 通知 / 知识片段 / 删除」；**任何删除确认弹窗都必须出现「删除后不可撤回」**（D13）
- **要动的文件**：`pages/knowledge/knowledge.{js,json,wxml,wxss}`（新建）、`pages/knowledge-preview/knowledge-preview.{js,json,wxml,wxss}`（新建，v1.5 预览页）、`config/api.js`（+ `METHODS_KNOWLEDGE`、`METHODS_KNOWLEDGE_BATCH`、`METHODS_KNOWLEDGE_ORPHANS`（步骤 5 已加）、`METHODS_KNOWLEDGE_CONTENT`、`METHODS_KNOWLEDGE_AUDIT`（步骤 8 已加），**必须沿用 `API_BASE_URL` 派生写法**，否则 `npm test` 失败）、`scripts/build.js:21-32` 的 `SHARED_PAGES`（+ `pages/knowledge/knowledge`、`pages/knowledge-preview/knowledge-preview`，`npm test` 会校验）、`pages/methods/methods.{wxml,js}`（入口卡 + 访客隐藏 + `tapKnowledgeManage` + 既有「入库知识」卡补角色守卫）
  - **不要手改 `app.json`**：它是 `build.js` 的生成产物且被 `.gitignore:11` 忽略（评审 §6.4）

---

## 6. 一致性与边界

1. **共享库同名文件**：Chroma 按 `(scope, source_file)` 删、元数据按 `(scope, filename)` 全删 → 会连带移除其他上传者的配额记录。这是刻意的（否则向量已删、元数据还在，状态更乱），但必须在预览里显式列出上传者。
2. **同一内容的重复上传**：chunk ID 只由 `content_hash` / `scope` / 分块数决定，不含文件名（`schemas.py:7-26`），`store_nodes` 用 `upsert`（`chroma_repository.py:213-252`）→ 抽取文本相同的重传会覆盖同一批节点，节点 `source_file` 归**后一次**的名字（§2.7 机制段）。因此删除「没有节点的那一份」只回收配额（`node_count == 0`），内容仍可能通过有节点的文件名检索到 —— 预览文案需如实说明（§5 的第三种弹窗）。
3. **Chroma 删除是 best-effort**：`delete_nodes` 恒返回 `True`，所以第 4 步复核不可省；复核失败时保留元数据行，操作可重试（§4.2）。
4. **BM25 窗口**：删除同步后不再命中；若同步失败（进程异常 / 导入失败），在 Gateway 重启前被删内容仍可能被 BM25 召回（§2.6、§4.3）。
5. **`miniapp_uploads` 的临时副本不动**：由 `_cleanup_expired_uploads`（`adapter.py:2334-2346`）按 `expires_at` 自动删行 + 删物理文件；与知识库删除混在一起会误删用户还在查看的附件。
6. **配额释放**：删掉 `file_metadata` 行后 `get_user_file_count` 立即下降（`sqlite_store.py:66`），用户可继续上传；`daily_uploads` **不回退**（当天次数不重置，符合直觉）。
7. **列表与节点的一致性**：列表源自 `file_metadata`，若某文件的节点被外部工具单独删除，列表仍会显示它 —— 此时「删除」正好把它清掉（§4.2 第 6 步）。
8. **抓取通知（`source="jxtz"`）的删除是永久的**：每日同步按 `data/jxtz_notices.jsonl` 的 URL 账本去重，不会抓回（§2.8-1）；但 `batch_ingest_jxtz.py` 在结果文件丢失/重置后重跑会恢复（§2.8-2）。因此预览文案用「不会自动恢复」，不写「永久删除」。
9. **来源是派生值，不是存储字段**：列表的「文件 / 教务通知」由文件名前缀判定（D8），不是 `file_metadata` 的真实列 —— 若将来有入库路径产出的文件名以 `jxtz_` 开头，会被误标为教务通知（现网 0 例）。该 UI 只对 admin / owner 渲染，接口仍一致返回 `source`。删除抓取通知也不释放 admin 的实际配额（§2.8-3）。
10. **显示名的数据源是抓取账本**：`data/jxtz_notices.jsonl` 缺失 / 落后时，该通知回退显示「剥掉前缀的文件名」，不影响删除与检索；账本**只读、不写**（§1.3 非目标）。
11. **批量删除是部分成功语义**：成功项已删、失败项保留元数据行，列表刷新后仍在 —— 前端必须逐条展示，不能只报「删除完成」（§4.4）。
12. **访客被禁用后 `users/*` 的历史数据无人能管理** → 本次一并清理（§11 步骤 0）；清理**不能用 `admin_cli purge`**，原因见 §11。
13. **CORS 不声明 DELETE**（L6）：`Access-Control-Allow-Methods` 只有 `GET, POST, OPTIONS`（`adapter.py:2253`）。小程序 `wx.request` 不受 CORS 约束，且白名单的 2 条 DELETE 已在生产跑（先例）；仅当将来出现**浏览器**管理页时才需要补 `DELETE`，本次不改。
14. **列表规模阈值与预案（M4）**：列表**全量返回、无分页**。实测 `global` 1308 行按 §4.1 响应结构序列化 = **443 KiB**（`json.dumps` 默认分隔符、`ensure_ascii=false`，含 `uploaders` / `ingested_at`；紧凑分隔符 426 KiB，2026-09-09 复算）；精简为 `filename` / `display_name` / `source` 后 **314 KiB**。jxtz 每日新增；小程序 `setData` 单次上限 **1024 kB**（官方限制，超限报 `vdSyncBatch 数据传输长度…已经超过最大长度 1048576`），按 443 KiB / 1308 行线性外推约 **3000 行**触顶，留余量按 **约 2500 行**启动预案，届时表现为页面加载失败而非变慢。预案（本次不做，触发条件出现时按此执行）：接口加 `limit` / `offset` + `total`，前端触底加载，筛选与搜索改服务端 `?q=`；次选为列表只返回 `filename` / `display_name` / `source`，`uploaders` 移到删除预览里查（§4.2 本来就要查一次）。

---

## 7. 删除单个文件 / 单条通知后的查询可用性与数据完整性

> 删除单位 = `(scope, source_file)`，即**一份具体文件或一条具体通知**（§4.2）。整 scope 清空只存在于运维脚本 `scripts/purge_pipeline.py`，不在本设计范围。以下结论均指单文件删除。

### 7.1 删除后用户能否正常查询

| 检查点 | 结论 | 依据 |
|---|---|---|
| 同 scope 的其他文件 | **不受影响**。删除条件是 `$and[{scope},{source_file}]` 精确匹配，只命中这一份文件的节点 | `chroma_repository.py:208-210`、`:365-395` |
| 被删文件本身 | 不再被向量检索命中（节点已删）；BM25 侧同步移除（D6） | `chroma_repository.py:365-395`、`bm25_search.py:82-95` |
| BM25 里其他文件的打分 | **保持自洽**。`_remove_node` 逐 token 摘倒排与 `_df`（倒排清空的 token 连 `_df` 一起删）、重算 `_total_docs` / `_avgdl`；检索还有 `nid in self._nodes` 兜底 | `bm25_search.py:82-95`、`:122` |
| 相邻文件的上下文拼接 | **不受影响、不报错**。`_expand` 对不在结果池里的邻居回退到链位相邻节点，构建 passage 时再以 `if nid in node_map` 过滤 | `context_stitcher.py:101-110`、`:81` |
| 权限 | **不变**。`get_allowed_scopes` 只由 role + user_id 推导，与库里有无数据无关 | `acl_filter.py:147-186` |
| 该 scope 被删到空（极端情况） | **不报错**。`search_nodes` 返回空 → `_retrieve` 走「暂未收录该内容」 | `chroma_repository.py:323-326`、`query_kb.py:241-248` |
| 检索缓存 | 应用层无检索缓存；唯一缓存是「`users/*` scope 列表」，`delete_nodes` 主动失效 | `chroma_repository.py:372`、`:504-542` |
| 精排 / 上下文拼接 | 只作用于本次检索结果集，不回读 Chroma | `query_kb.py:259`、`:280-296` |
| Chroma 不可用 | 查询侧回复「知识库检索出错」；删除侧 503 且不写任何东西（§4.2 第 1 步） | `query_kb.py:218-224` |
| 历史会话里出现过的已删内容 | **不会消失**：历史消息从 `~/.hermes/state.db` 只读回放，删除不触碰该库 | `adapter.py:2192-2200` |
| 删除后能否重新上传同一文件 | **能**。字节级去重查的是 `file_metadata`（非 Chroma），行删掉后 `file_hash_exists` 返回 False → 正常重新入库 | `orchestrator.py:223-246`、`chroma_repository.py:678`、`sqlite_store.py:137-150` |

### 7.2 是否会损坏数据库（单文件删除）

| 存储 | 删这一个文件做了什么 | 损坏风险 | 依据 |
|---|---|---|---|
| Chroma（`data/chroma`） | 一次 `col.delete(where=…)`，经 HTTP 交给 chroma-server | **无**。生产 `.env` 配 `CHROMA_HOST/PORT`（`127.0.0.1:8007`）→ HTTP 模式下 `get_chroma_client` **绝不回退**嵌入式 `PersistentClient`，客户端不直接打开 `chroma.sqlite3` / HNSW 文件 | `.env:17-18`、`chroma_repository.py:30-98`、`:365-395` |
| `data/quota.db` | 逐个上传者执行一行 `DELETE FROM file_metadata WHERE user_id=? AND filename=? AND scope=?` | **无**。主键精确匹配（无 `LIKE`、无级联、无外键），单行删除 + `commit`；WAL 模式；`sqlite3.threadsafety=3`（本机 venv 实测，连接可跨线程串行使用） | `sqlite_store.py:200-208`、`:39-47`、`:16-18` |
| BM25（进程内存） | 持 `self._lock` 摘除该文件的节点 | **无持久化**，不涉及文件 | `bm25_search.py:82-95`、`:124-131` |
| `data/audit.db` | 追加一条事件 | 只增不改 | `audit_logger.py:133-175` |

### 7.3 这一份文件 / 这一条通知的数据是否处理到位

| 数据 | 删除后状态 | 依据 / 处理方式 |
|---|---|---|
| 该文件的 Chroma 节点 | 按 `(scope, source_file)` 删除 + 严格复核 | §4.2 第 1 / 4 步；复核失败即 503 / 500 且**不删元数据行** |
| 该文件的 `file_metadata` 行 | 复核通过后逐行按主键删除（含共享库其他上传者的行） | `sqlite_store.py:200-208` |
| 配额计数 | `get_user_file_count` 立即下降，可继续上传 | `sqlite_store.py:66-92`；`daily_uploads` / `upload_log` 不回退 |
| 小程序上传的临时副本 | 不动，24h 过期自清 | `adapter.py:2334-2346`（§6.5） |
| 抓取通知的正文文件 | **无持久副本**：每日同步写的是临时文件、入库后立即 `unlink`；批量脚本同样 `os.remove` | `sync_jxtz.py:386-391`、`batch_ingest_jxtz.py:263-264` |
| 抓取账本 | 不动 → 每日同步按 URL 账本跳过，**不会抓回**；只有 `batch_ingest_jxtz.py` 按其结果文件重跑才会恢复 | `sync_jxtz.py:324`、`:415-420`、`batch_ingest_jxtz.py:195-213` |
| 同内容被 upsert 覆盖的另一份 | 删「持有节点的那一份」会连内容一起删，另一份只剩孤儿元数据行，可再经 §4.2 第 6 步清理 | `schemas.py:7-26`、`chroma_repository.py:213-252`（§2.7） |
| `miniapp_uploads` / `normalized_facts` / 历史会话 | 不动 | `adapter.py:2334-2346`、`chroma_repository.py:18`、`adapter.py:2192-2200` |
| 列表的显示名 / 来源 | **只读派生**（账本 / 文件名前缀），不写任何存储；删除后该行消失，账本不动 | D8、D10、`sync_jxtz.py:415-420` |

### 7.4 并发与竞态（如实记录，本次不加额外兜底）

- **同一文件「边入库边删除」**：入库在工作线程执行（`adapter.py:377`），删除同样走工作线程；两者按写 / 删的先后决定最终状态 —— 入库顺序为解析（`orchestrator.py:311`）→ `_track_metadata`（`:338`）→ `store_nodes`（`:346`），删除若落在入库完成之前，元数据行与节点会被入库重新写回，文件重新出现在列表且可被检索。本设计**不加文件级锁**（需求未要求，触发需同一文件名同时被两个操作命中）。
- **查询与删除并发**：单次查询与删除的可见性由 chroma-server 的并发语义决定，本设计不作保证，列为实施阶段的验证项（§8）。

---

## 8. 验证方案

| 层级 | 内容 | 状态 |
|---|---|---|
| 单元（进程内 harness） | **隔离口径（M3，必须）**：只隔离 `CHROMA_DB_PATH` 是**无效**的——`.env` 配了 `CHROMA_HOST=127.0.0.1` / `CHROMA_PORT=8007`（`.env:17-18`），`get_chroma_client` 在 HTTP 已配置时**忽略 path 且绝不回退嵌入式**（`chroma_repository.py:45-47`、`:63-94`），`ensure_chroma_server()` 还可能顺带拉起服务 → 测试会**删生产向量**。做法（按可靠性排序，评审 N1 修正）：① **直接 `ChromaRepository(chromadb.PersistentClient(tmp_path))`** 或注入 fake repo——既有测试正是因为直接用 `PersistentClient`（`tests/knowledge_base/repository/test_chroma_integration.py:14`）才安全，最稳；② 需真实实例时用 `monkeypatch.setenv("CHROMA_HOST"/"CHROMA_PORT")` 指向一次性 chroma-server。**不要只靠 `delenv`**：`create_chroma_repository()` 内部会调 `bootstrap.load_dotenv()`（`bootstrap.py:125`），它用 `os.environ.setdefault` 把 `delenv` 掉的变量**从 `.env` 重新注入**（实测仍连生产 `8007`）→ `delenv` 只能作为辅助 | 已实施（步骤 3 按此口径执行） |
| 单元（用例） | 造 3 个 scope 的文件 → 列表聚合正确 → student 访问 `teachers` / `users/other` 一律 403 → 本人删 `users/{self}` 成功 → 复核 Chroma 节点数、`file_metadata` 行数、`get_user_file_count` 同步下降 → 重复删除返回 404 → `dry_run` 不产生任何写入 → `filename` 传非字符串 → 400（L5） | 已实施（隔离 harness 39 项 PASS） |
| BM25 同步 | 灌入索引 → 删除文件 → `remove_file` 后 `search()` 不再命中该文件；`remove_file` 对不存在文件返回 0 | 已实施（单元 + harness） |
| 来源标记 | `dry_run` 对抓取文件返回 `source="jxtz"`、对人工文件返回 `source="file"`；`get_file_source` 对不存在文件返回 `""` | 已实施（单元 + harness） |
| 来源派生与筛选 | 列表里 1 个 jxtz + 1 个 file → `source` 各自正确；admin 下筛选「教务通知」只剩 1 条、「文件入库」只剩 1 条、「全部」2 条；**student / teacher 角色下筛选条与来源标签不渲染**（D8、§5） | 已实施（后端 harness + 前端页面逻辑 harness；视觉确认待用户） |
| 显示名 | `jxtz_10392_unzv285n.txt` 显示为 `[学籍管理]2026年电子与信息学部接收本科生转专业考试安排`（账本修正）；账本删掉该行后回退为「剥前缀文件名」；**账本文件整体缺失时不报错**、全部回退为剥前缀文件名（D10、§6.10、§4.1） | 已实施（单元 test_display_names） |
| 批量删除 | 3 个文件（1 正常 / 1 只有元数据行 / 1 不存在的名字）→ `deleted=2`、`failed=1`，失败项元数据行仍在；51 个文件 → 400 `too_many_files`；`dry_run` 不产生任何写入（§4.4） | 已实施（harness） |
| 孤儿扫描 | 造「有元数据行、0 节点」的文件 → 扫描恰好命中它，正常文件不命中；`col.get` 抛错 → 503 且**不返回候选**；**集合为空 → 503**，而「某 scope 合法地只剩元数据行」→ 正常返回候选（L9）；扫描后把该文件重新入库 → 批量 `dry_run` 显示真实节点数（候选过期保护）（§4.5、D14） | 已实施（harness，含 3 条护栏） |
| 默认角色（v1.5） | 未登记的 miniapp 用户 → `resolve_role("miniapp", uid) == "guest"`、`_upload` 403、`GET knowledge` 403；未登记的其它平台用户仍为 `student`，`query_auth_status` 走学生分支（D15、§4.7.5） | 已实施（单元 `test_role_store.py::TestPlatformDefaults` + 隔离 harness） |
| **默认角色回归（v1.5，M2）** | **已登记为 student 的 miniapp 用户 → `resolve_role` 仍为 `student`**（不是 guest）、能上传到个人库；已登记 teacher / admin 不受影响；`unset_role` 后审计里的 `new_role` = `default_role_for(platform)`。这是 §4.7.5 改法的回归网 | 已实施（`test_registered_miniapp_student_not_downgraded` 等 6 项） |
| 列表状态与配额（v1.5） | 个人库造 1 个 0 节点行 + 1 个正常文件 → `status` 分别为 `empty` / `ok`；顶层 `quota` 返回 `used` / `limit`，admin 为 `null`；Chroma 不可用 → `status` 字段省略且列表仍 200（§4.7.1） | 已实施（隔离 harness 30 项覆盖） |
| 原文预览（v1.5） | 正常文件返回拼接文本、`node_count` 与 `count_nodes` 一致；超长文件 `truncated=true` 且 `len(text) == 20000`；0 节点 → 404；越权 scope → 403（§4.7.2） | 已实施（单元 + harness；缺 `filename` → 400 亦覆盖） |
| 操作历史（v1.5） | 单删 1 个文件 + 批量删 1 个文件各执行一次 → admin 能看到 **2 条** `kb_delete`（`detail.op` = `single` / `batch`，M1）；批量每成功 1 个文件写 1 条（§4.4）→ **断言按 `detail.op` 分组计数，不写死总数**；student / teacher 403；默认 50 条倒序（§4.7.3） | 已实施（harness：2 条按 `detail.op` 分组 + 403 + `limit` 校验 + 503） |
| 对话引用（v1.5，跨仓） | `query_kb.py` 的引用行用显示名（乱码文件显示账本标题）；账本缺失回退文件名。**需在另一个仓库单独验证**（§4.7.4） | 待实施 |
| 访客 | guest token 调 GET / DELETE / DELETE batch / 上传 → 一律 403；前端 `role === 'guest'` 时两张卡不渲染（D11） | 已实施（后端 403 + 前端非访客才渲染；视觉确认待用户） |
| 历史数据清理 | dry_run 输出恰好 **15 行**：`users/*` 5 行（`XiongWei` 3 / `zhangsan` 1 / `openid-1` 1）+ `global` 10 行 0 节点脏元数据（8 行乱码 + 10401/10402）；执行后 `file_metadata` 无 `users/%` 行、无这 10 个乱码文件名，`teachers` 的 3 行与 95 个 `global` 人工文件**保持不变**（§11 步骤 0） | 已实施（步骤 0，2026-09-09） |
| 严格复核（失败路径） | 让 `col.get` 抛错（不可达的 `CHROMA_HOST/PORT` 或 mock）→ 删除端点返回 503 且 `file_metadata` 行数不变，验证「假成功」已被堵住（§4.2 第 1 步） | 已实施（harness） |
| 单文件删除回归 | 删某 scope 中 1 个文件后：同 scope 其他文件仍能检索到；被删文件向量 + BM25 均不再命中；相邻文件的多片段拼接不报错；重新上传同一文件可正常入库（§7.1） | 待实施 |
| 查询可用性回归 | 删空某 scope 的最后一个文件后，该 scope 检索返回「暂未收录」而非报错；其它 scope 检索不受影响（§7.1） | 待实施 |
| 并发可见性 | 删除与查询并发时的可见性（§7.4）需在隔离环境实测；本次不作保证 | 待实施 |
| 路由探活 | `GET` / `DELETE /api/methods/knowledge` 无 token → 401。**注意口径**：实施前这些路径返回的是 **405** 而非 404（适配器注册了 `add_options("/{tail:.*}")` 通配，aiohttp 认为路径存在但方法不允许，实测确认）→ 断言写 404 会误判；正确写法是「实施前 405 / 实施后无 token 401」 | 已实施（步骤 4 / 步骤 7 两次探活：6 条知识库路由——列表 / 删除 / 批量 / 孤儿 / 预览 / 操作历史——无 token 一律 401，`/health` 200） |
| 前端 | `npm test`（共享页清单校验）+ 微信开发者工具过一遍列表 / 预览弹窗 / 空态 / 404 | 已实施（npm test + 页面逻辑 harness 32 项）/ 开发者工具过一眼待用户 |
| **端到端（未验证项）** | 带真实 token 的端到端删除需用户在开发者工具手动走一次（签发 token 需读 `MINIAPP_SESSION_SECRET`，权限策略拦截，不绕过） | 由用户执行 |
| 部署 | `cp` 到 `agent4som-hermesagent`（实盘，仅两份副本）+ `sudo systemctl restart hermes-gateway@jwc-assistant` + 记录 `ActiveEnterTimestamp`（原交接文档已移除） | 已实施（步骤 4 / 步骤 7 两次部署；当前 `ActiveEnterTimestamp` = 2026-09-09 20:35:50，两份副本 md5 一致） |

---

## 9. 风险与回滚

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **公共库真实数据误删**（1308 个文件，不可逆） | `dry_run` 预览 + 前端二次确认 + 不做整库清空；上线前先用测试文件在开发者工具走一遍 |
| 2 | 共享库误伤他人 | 预览显式列出全部上传者（§6.1） |
| 3 | 部分删除（Chroma best-effort） | 复核 + 500 + 保留元数据行，可重试（§4.2 第 4 步） |
| 4 | BM25 同步失败导致残留召回 | best-effort + WARNING；重启 Gateway 即彻底清除（§4.3） |
| 5 | 权限矩阵漂移 | `_upload` 与新端点共用 `_knowledge_scopes()`（D5） |
| 6 | **误删只能整份回滚到 T-1**（**已按评审 E1 更正**：不是「无备份」） | 事实：**NAS 每日 01:00 在线全量备份正常工作（不停服务）**（`nas-backup@ai-helper-test.timer`，2026-09-09 01:01:10 `status=0/SUCCESS`、132 MB、sha256 `e59c0b5e…`），范围含 `data/` 全量、排除 `data/backups`（`scripts/nas_backup.sh:57-67` 先对 `quota.db` / `audit.db` / `chroma.sqlite3` 做 `PRAGMA wal_checkpoint(TRUNCATE)`，再 `tar -czf … -C "$SCRIPT_DIR" data/`）。**恢复单位是整份 `data/` 回滚到昨日**，会一并回退当日全部变更；`quota.db` 恢复精确，Chroma tar 的跨服务一致性脚本内未强校验（`wal_checkpoint` 失败被 `|| true` 吞掉）→ **恢复演练需实测**。缓解：`dry_run` 预览 + 二次确认 + 不做整库清空；审计事件（§10 #5）提供事后追溯。本地 `backup-kb`（02:00，`data/backups/`）自 2026-08-25 起因 SELinux 拒 exec 静默失败（`status=203/EXEC`，`data/backups/` 不存在）→ **本次已修复并验证**（`ExecStart` 改为 `/bin/bash <script>`，与 `nas-backup@.service` 同法）。2026-09-09 18:46 手动跑通：`data/backups/` 产出 chroma 127 MB / quota 782 KB / audit 155 KB / roles.json；`quota` 副本可读且 `file_metadata` 1316 行与现网一致（§11 步骤 -1） |
| 7 | **批量误删**（一次勾选删掉公共库几十个真实文件） | 上限 50 + 汇总确认弹窗（条数 + 来源构成）+ 强制「删除后不可撤回」；不提供跨 scope 的「全选」 |
| 8 | 访客被前端隐藏后仍直接调接口 | 后端 `_knowledge_scopes()` 对 guest 返回空集 → 所有知识库端点 403（D11）；前端隐藏只是体验层 |
| 9 | 账本定位错 → 通知标题**静默**回退成文件名 | 账本被 `.gitignore` 忽略、部署副本无 `data/` 目录；按 `CHROMA_DB_PATH` 同级解析（与 `quota.db` 一致，§4.1），并在验证方案里覆盖「账本缺失」用例（§8） |
| 10 | 孤儿候选过期（扫描后该文件又被重新入库） | 删除前批量 `dry_run` **当场重新计数**，预览里显示真实节点数；管理员据此决定（§4.5、§4.4） |
| 11 | 孤儿扫描被误读为「没有孤儿」 | Chroma 不可用时返回 503 而**不是空列表**（§4.5 护栏）；前端对 503 单独提示（§5） |
| 12 | **默认角色改动**影响小程序未登记用户（student → guest） | 预期效果是「不能上传、看不到管理入口」；需回归小程序其它流程（认证申请 / handoff / 个人资料）确认不依赖 student 默认值（D15、§8） |
| 13 | 原文预览拉取大文件节点 | 单文件节点数通常 < 100；截断 2 万字；`col.get` 出错即抛 → 503（§4.7.2） |
| 14 | 操作历史暴露操作者身份 | 仅 admin / owner 可见，只读，只返回删除类事件（§4.7.3） |
| 15 | **列表规模增长**（M4） | 当前 1308 行 = 443 KiB，可用；约 2500 行逼近小程序 `setData` 上限 → 预案见 §6.14（分页 / 精简字段）。**不在本次实施**，但触发时必须处理，否则表现为页面加载失败 |
| 16 | **审计写入失败** | 删除已成功后 `log_event` 抛异常 → `try/except` + WARNING，不让成功变 500（L4、§4.2） |

---

## 10. 待拍板

| # | 问题 | 建议 |
|---|---|---|
| 1 | `dry_run` 预览要不要做 | **做** —— 与白名单一致，代价一个分支，挡住公共库误删 |
| 2 | 列表是否显示上传者 | **显示** —— 共享库删别人的文件时必须可见 |
| 3 | 入口位置 | 「我的功能」节新增「知识库管理」卡（所有角色） |
| 4 | 是否顺带清理 `miniapp_uploads` | **不做** —— 24h 自动过期（§6.5） |
| 5 | 删除是否写审计日志 | **写** —— 复用启动时已初始化的 `AuditLogger`（schema 自带 `filename`/`scope`/`node_count`/`detail`），约 10 行；取用沿用既有写法 `from knowledge_base.retrieval.response_verifier import _audit` + `if _audit:`（`~/.hermes/hermes-agent/tools/knowledge_ingest.py:145-147`、`query_kb.py:322` 即此写法），无需新增访问器 |
| 6 | 是否分阶段（先个人库、后共享库） | **一次性开放** —— 共享库权限已按角色收口 |
| 7 | 重复内容（§6.2）要不要在预览里额外提示 | 建议**暂不做** —— 需按 `content_hash` 反查（`get_file_metadata_by_hash`），价值有限；如评审要求可加一行提示 |
| 8 | BM25 同步（D6）是否纳入本次 | **纳入** —— 不纳入则「删除后不再被检索到」不成立（§2.6） |

**已拍板（2026-09-09 追加）**：

| # | 问题 | 结论 |
|---|---|---|
| 9 | 两类来源（抓取 / 人工）是否在界面区分 | ~~仅删除预览显示来源~~ → **已被 #11 取代**：列表显示来源并支持筛选（D8） |
| 10 | 抓取通知是否允许从管理页删除 | **允许，与人工文件一致**（D9）—— 每日同步不会抓回（URL 账本），但批量脚本可恢复，文案如实 |
| 11 | 列表是否显示来源 / 是否支持筛选 | **显示 + 筛选，且仅 admin / owner 可见**（教学通知只在公共库，之后也只有管理员能管理）；全部 / 文件入库 / 教务通知，默认全部；来源由文件名前缀派生（D8），不加 schema 列 |
| 12 | 通知如何显示 | **显示完整原始标题**，取自抓取账本（D10）；不加列、不回填、不改入库链路 |
| 13 | 回收站 / 撤销 | **不做**；删除确认文案强制「删除后不可撤回」（D13、§1.3） |
| 14 | 访客（guest） | **不参与知识库**：不可上传 / 不可列 / 不可删（D11）；`users/*` 5 行历史数据全部清理（§11 步骤 0） |
| 15 | 批量删除 | **做**，上限 50，逐条返回结果（D12、§4.4） |
| 16 | 10 行 0 节点脏元数据（8 行同名两行 + 2 行空壳） | **全清**（2026-09-09 拍板）—— 只删 `file_metadata` 行（Chroma 无节点），清完列表不再出现同名两行（§11 步骤 0） |
| 17 | 要不要给管理员一个清理脏数据的按钮 | **要，但只做「只读扫描 + 勾选删除」**（D14、§4.5）：唯一安全判据是「元数据行在、Chroma 0 节点」；不做一键全删（Chroma 抖动会全库误判）。孤儿会因「同内容换名重传」持续产生（§2.7 第 2 行），一次性清理治不了本 |

**待拍板（v1.5）**：

| # | 问题 | 建议 |
|---|---|---|
| v1.5-1 | 跨仓的 `query_kb.py` 引用显示名（D16）是否与本次一起做 | **一起做，但走独立授权 + 独立提交 + 独立部署**（§4.7.4、§11 步骤 8）—— 不改则对话里的乱码文件名依旧存在，可观测性只做了一半 |

**已拍板（2026-09-09 v1.5）**：

| # | 问题 | 结论 |
|---|---|---|
| 18 | 小程序新用户默认角色 | **改为 guest**（D15、§4.7.5）—— 「访客不能上传」只靠前端隐藏不够，后端默认值才是根因 |
| 19 | 默认角色改动范围 | **只改小程序**，其它平台仍保持 student —— 全局改会连带改变（历史：招生助手时代）未知联系人的认证状态分支（`management_flow/service.py:475`） |
| 20 | 对话回答的引用显示什么 | **显示名**（教务通知 = 原始标题，文件 = 文件名）—— 复用 D8 / D10 规则（D16） |
| 21 | 列表是否显示入库状态与个人配额 | **显示**，只对个人库（D17 / D18、§4.7.1）—— 上传失败的行从此在列表可见 |
| 22 | 是否做原文预览 / 管理员删除历史 | **都做**（D19 / D20、§4.7.2–§4.7.3）—— 只读，预览截断 2 万字 |

**评审采纳（2026-09-09，v1.6）**：删除设计评审的 M1–M4 + E1 + L1–L12 **全部采纳**，落点如下。

| 项 | 处置 | 落点 |
|---|---|---|
| M1 审计事件类型 / 查询契约不一致 | 统一为单一 `kb_delete`，单删 / 批量由 `detail.op` 区分；§4.7.3 去掉 `?scope=` | §4.2、§4.7.3、§8 |
| M2 D15 改法会把已登记 student 静默降级 | `resolve_role` 去掉与常量的比较、直接返回 `get_role`；补回归用例；`unset_role` 审计写平台默认值 | §4.7.5、§8 |
| M3 harness 隔离不足会打生产 Chroma | §8 写明：优先直接 `ChromaRepository(PersistentClient(tmp_path))` / 注入 fake repo；`delenv` **不可靠**（`load_dotenv()` 会把变量从 `.env` 重新注入，1.6.1 N1） | §8 |
| M4 列表无分页 | **本次不加**，写阈值 + 预案（约 2500 行逼近上限） | §6.14、§9 风险 15 |
| E1 备份事实 | §9 风险 #6 重写为「NAS 每日 01:00 在线全量备份可用（不停服务），恢复单位 = 整份回滚到 T-1」；`backup-kb` 的 `ExecStart` 已改并跑通验证（`infra/` 副本 + 系统单元，步骤 -1） | §1.3、§9 风险 6、§11 步骤 -1 |
| L1 | 白名单路由 DELETE 数 3 → **2** | 附录 A |
| L2 | 行号漂移逐条修正；附录 A 加「以符号名为准」说明 | 全文、附录 A |
| L3 | §2.8 `source` 计数标注为 `global` scope 口径 | §2.8 |
| L4 | 审计写入失败 `try/except` + WARNING | §4.2、§9 风险 16 |
| L5 | `filename` / `filenames` 边界校验（400） | §4.2、§4.4、§8 |
| L6 | CORS 无 DELETE 说明（小程序不受影响） | §6.13 |
| L7 | 显示名解析下沉 `knowledge_base/core/display_names.py`，两仓共用 | §4.6、§4.7.4、§12 |
| L8 | 端点统一 `/api/methods/knowledge/audit` | §4.7.3、§5 |
| L9 | 孤儿护栏由 scope 级改为**集合级** count == 0 | §4.5、§8 |
| L10 | `node_count == 0` 文案补「可能以其它文件名入库」 | §5 |
| L11 | DDL 行号统一为 `:39-47` | §2.1、§4.1、§7.2、附录 A |
| L12 | 账本无 `record_id` 列，关联键由 `url` 末段派生 | §2.8、§4.1 |

---

## 11. 实施顺序（批准后）

0. **数据清理（可与代码解耦，先跑 `dry_run`）**：共 15 行。
   - `users/*` 5 行历史数据（3 个 user_id）。用一次性脚本按 `(scope, filename)` 逐条删节点 + 删元数据行，跑完即删脚本（不新增常驻工具）
   - `global` 10 行 0 节点脏元数据（`user_id=admin`）：8 行乱码文件名（`jxtz_10392_unzv285n.txt` 等，与兄弟行共享 record_id → 列表同名两行）+ 2 行空壳（`jxtz_10401_iuet41pr.txt`、`jxtz_10402_4tw3if_8.txt`）。Chroma 侧无节点 → 只调 `delete_file_metadata("admin", filename, "global")`，不碰 Chroma
   **禁止 `admin_cli purge XiongWei`**：`delete_user_data` 会删该 user_id 在**所有 scope** 的元数据行（`sqlite_store.py:214-219`），而 `purge_scope` 只清 `users/XiongWei` 的节点（`chroma_repository.py:608`）→ XiongWei 在 `teachers` 的 3 行元数据会被误删、节点成孤儿。先 dry_run 核对恰好 5 行，执行后复核 `teachers` 3 行不变
-1. **修备份（评审 E1）——已完成（2026-09-09 18:46）**：`ExecStart` 改为 `/bin/bash /home/<DEPLOY_USER>/H-agent/agent4som/scripts/backup_kb.sh`（绕开 SELinux 对 `user_home_t` 的 exec 检查），`daemon-reload` 后手动跑通：`data/backups/` 出 chroma 127 MB / quota 782 KB / audit 155 KB / roles.json，`quota` 副本 1316 行可读。每日 02:00 由 `backup-kb.timer` 继续跑。**先有可验证的本地备份，再开放不可逆删除 —— 此前提已满足**
1. `knowledge_base/core/display_names.py`（L7，两仓共用）+ `SqliteStore.list_file_metadata_by_scope` + `ChromaRepository` 透传 + `ChromaRepository.list_source_files`（孤儿扫描用，出错即抛）+ 单元验证
2. `BM25Index.remove_file` + 单元验证
3. adapter：`_knowledge_scopes()` 抽取（含 guest 排除）+ 4 条端点（含 `_method_knowledge_orphans`，§4.5 护栏）+ `_knowledge_delete_one()` + `_auto_ingest` 补 `source="file"` + 隔离 harness 验证（**按 §8：直接 `PersistentClient(tmp_path)` / 注入 fake repo，勿只靠 `delenv`**）
4. 部署 + 重启 + 路由探活 + 记录 `ActiveEnterTimestamp`
5. 前端：`pages/knowledge/*`（筛选 / 来源 / 标题 / 批量 / 孤儿扫描 / 访客隐藏）+ 入口卡 + `config/api.js` + `build.js` + `npm test` + 开发者工具过一眼
6. 文档同步：本文件状态改为「已实现」（原交接文档已移除，风险清单不再补链接）
7. **v1.5 后端**：`role_store.default_role_for()` + `get_role` 改用它 + `resolve_role` 去掉常量比较（M2）→ 回归「未登记小程序用户 = guest、**已登记小程序 student 仍为 student**、未登记其它平台用户 = student」；`ChromaRepository.get_file_documents()`；列表补 `quota` / `status`（§4.7.1）；`GET /knowledge/content`（§4.7.2）、`GET /knowledge/audit`（§4.7.3）→ 隔离 harness + 路由探活 + 重启 —— **已完成（2026-09-09）**：单元 47 / 301 / 159 项全绿、隔离 harness 30 项 PASS、部署 + 重启（20:35:50）+ 6 条路由探活
8. **v1.5 前端**：知识库页加状态标签 / 配额、预览页、操作历史页签（§5） —— **已完成（2026-09-09）**：`build.js jwc` + `npm test` 全绿、Node 页面逻辑 harness 23 项 PASS；开发者工具视觉确认待用户
9. **v1.5 跨仓（需单独授权）**：`agent4som-hermesagent/hermes-agent/tools/query_kb.py` 引用改用显示名（§4.7.4）—— 另一个 git 仓库，单独提交、按部署流程重启网关、单独验证

---

## 12. 模块索引

| 模块 | 文件 | 本文相关职责 |
|---|---|---|
| 小程序 HTTP 适配器 | `~/.hermes/plugins/miniapp-platform/adapter.py` | 新增 6 条路由（列表 / 删除 / 批量 / 孤儿扫描 + v1.5 的预览 / 操作历史）、`_knowledge_scopes()`、删除编排与 BM25 同步 |
| 显示名解析（**两仓共用**） | `knowledge_base/core/display_names.py`（新增） | `resolve_display_name(scope, source_file)`：D8 前缀派生 + D10 账本标题（L7） |
| 元数据 / 配额存储 | `knowledge_base/core/sqlite_store.py` | `list_file_metadata_by_scope`（新增）、`delete_file_metadata` |
| 孤儿扫描 | `knowledge_base/repository/chroma_repository.py` | `list_source_files(scope)`（新增，单次 `col.get` 统计每个 `source_file` 的节点数 → `dict[str, int]`，出错即抛）+ `count_collection_nodes()`（空集合护栏） |
| 向量仓储 | `knowledge_base/repository/chroma_repository.py` | `delete_nodes` / `count_nodes` / 元数据查询透传 / `get_file_documents`（v1.5 原文预览，§4.7.2） |
| BM25 关键词索引 | `knowledge_base/retrieval/bm25_search.py` | `remove_file`（新增）、`remove_scope`、`hybrid_search` |
| 入库管线 | `knowledge_base/ingestion/orchestrator.py` | 去重与 `source_file` 归一化（决定删除语义） |
| 教务通知抓取 | `scripts/sync_jxtz.py`（每日 04:00 定时）、`scripts/batch_ingest_jxtz.py`（批量） | 以 `user_id=admin` + `scope=global` + `source="jxtz"` 入库；URL 账本决定删除后是否会被抓回（§2.8） |
| 抓取账本（显示名数据源） | `data/jxtz_notices.jsonl` | **只读**：`record_id → 原始标题`（D10），按 mtime 缓存 |
| 权限 | `knowledge_base/retrieval/acl_filter.py`、`knowledge_base/auth/role_store.py` | 读 / 写矩阵、角色解析、`default_role_for(platform)`（v1.5、§4.7.5） |
| 审计 | `knowledge_base/core/audit_logger.py` | `kb_delete` 事件写入；`query()` 供 v1.5 操作历史读取（§4.7.3） |
| 跨仓检索工具 | `agent4som-hermesagent/hermes-agent/tools/query_kb.py`（**另一个 git 仓库**） | 对话引用显示名（v1.5、D16、§4.7.4） |
| 前端 | `miniprogram-framework-frontend/pages/knowledge/`、`pages/knowledge-preview/`、`pages/methods/`、`config/api.js`、`scripts/build.js` | 管理页与入口、v1.5 状态 / 配额 / 预览页 / 操作历史页签（§5） |

---

## 附录 A：引用行号速查

> **行号基于 `Academic-Assistant` 分支（v1.6.8 实施后重同步：`adapter.py` / `chroma_repository.py` / `role_store.py`；其余文件沿用 `9d67c311` 的 v1.6 复核值），实施时以符号名为准**（函数名 / 常量名比行号稳定，评审 L2）。

| 引用 | 位置 |
|---|---|
| 白名单 5 条路由（含 **2 条 DELETE**：`:129` 单条、`:131` 批量） | `adapter.py:127-131` |
| `_upload` 写侧权限矩阵 | `adapter.py:312-320` |
| `_auto_ingest` / 入库调用 | `adapter.py:390`（def）、`:377`（`to_thread` 调用）、`:385`（`ingest_detail`） |
| 自助申请角色门（身份只能由管理员授予） | `adapter.py:654-661` |
| `_authorize` | `adapter.py:2177` |
| `miniapp_uploads` DDL / 清理 | `adapter.py:2169`、`:2232-2242` |
| `file_metadata` DDL | `sqlite_store.py:39-47` |
| 配额 / 元数据查询 | `sqlite_store.py:66`、`:94`、`:108`、`:119`、`:135` |
| 元数据删除 | `sqlite_store.py:200`、`:196` |
| `quota.db` 路径推导 | `bootstrap.py:133` |
| `raw_nodes` / 单集合模式 | `chroma_repository.py:17`、`:26` |
| scope 过滤 | `chroma_repository.py:204`、`:208` |
| `delete_nodes` / `count_nodes` | `chroma_repository.py:365`、`:397` |
| `get_chroma_client`（HTTP 已配置则绝不回退嵌入式） | `chroma_repository.py:45-47`、`:63-94` |
| `AuditLogger.query`（单值等值、`ORDER BY id DESC`） | `audit_logger.py:242-256` |
| `log_event`（`event_type` 之后 keyword-only） | `audit_logger.py:133-157` |
| `default_role_for` / `get_role` / `resolve_role` | `role_store.py:54-59`、`:102-110`、`:169-193` |
| 抓取账本（无 `record_id` 列，由 url 派生） | `sync_jxtz.py:360`、`data/jxtz_notices.jsonl` |
| NAS 在线全量备份（WAL checkpoint + `data/` 全量、排除 `data/backups`，不停服务） | `scripts/nas_backup.sh:57-67`、`nas-backup@ai-helper-test.timer` |
| 本地备份（SELinux 拒 exec，v1.6 已修） | `infra/backup-kb.service`、`scripts/backup_kb.sh` |
| `purge_scope` | `chroma_repository.py:608` |
| BM25 `search` / `remove_scope` / 单例 / 灌入 | `bm25_search.py:97`、`:124`、`:142`、`:160` |
| `hybrid_search` 融合（BM25 独有节点进入结果） | `bm25_search.py:259`、`:277`、`:303-315` |
| BM25 启动灌入 | `gateway/hooks/kb_init/handler.py:82-127` |
| 检索侧 BM25 调用 / 末端 ACL 复核 | `~/.hermes/hermes-agent/tools/query_kb.py:251-254`、`:276-279` |
| 入库去重 / `source_file` 归一化 / 元数据落库 | `orchestrator.py:223-246`、`:262-295`、`:465-470`、`:644-647` |
| 权限矩阵 / 访客写侧空集 / 访客读侧仅 global | `acl_filter.py:38-44`、`:43`、`:147`、`:177-178` |
| `delete_user_data`（按 user_id 跨 scope 删元数据行） | `sqlite_store.py:214-219` |
| 通知临时文件名构造 / 账本原子写 | `sync_jxtz.py:385-386`、`:415-420` |
| `source_file` 保留 jxtz 前缀的注释 | `orchestrator.py:533-535` |
| 小程序入库传 `source="file"`（步骤 3 已补，原为空串） | `adapter.py:396-399`、`orchestrator.py:124` |
| jxtz 前缀剥离先例 | `chroma_vector_store.py:39` |
| 前端入口卡 / 角色分支 | `pages/methods/methods.wxml:39-46`、`pages/methods/methods.js:154-174` |
| 教务通知抓取入库 | `sync_jxtz.py:32-33`、`:311-325`、`:324`、`:386-391`、`:415-420`；`batch_ingest_jxtz.py:27-28`、`:195-213`、`:263-264` |
| 上下文拼接的邻居查找 | `context_stitcher.py:49`、`:81`、`:101-110` |
| 抓取定时器 | `/etc/systemd/system/jxtz-sync.timer`（`OnCalendar=04:00:00`、`Persistent=true`） |
| 人工 / 后端入库入口 | `scripts/admin_cli.py:79`、`scripts/batch_ingest_benke.py:71-86`、`:105` |
| 配额豁免（admin / owner） | `quota_manager.py:26-28` |
| 审计 | `knowledge_base/core/audit_logger.py:133` |
| 前端入口卡 / 上传交互 | `methods.wxml:39-46`、`methods.js:154-224` |
| 前端共享页清单 | `scripts/build.js:21-32` |
| 节点 ID 确定性（同文本 + 同分块数覆盖同名节点） | `schemas.py:7-26`、`chroma_repository.py:213-252` |
| 同进程证明（`_pending` Future 回填） | `adapter.py:182-192`、`:265-267` |
| 历史记录只读回放 | `adapter.py:2192-2200` |
| v1.5 列表 `quota` / 每文件 `status`（`_knowledge_personal_extras`） | `adapter.py:1722`（`_method_knowledge_list`）、`_knowledge_personal_extras` |
| v1.5 原文预览（`get_file_documents` + 端点） | `chroma_repository.py:463-477`、`adapter.py:1748` |
| v1.5 操作历史端点（`_method_knowledge_audit`） | `adapter.py:1774` |
| v1.5 上传件 `expires_at = +1 day` | `adapter.py:2304-2305` |
| SQLite 线程安全配置 | `sqlite_store.py:16-18` |
| Chroma HTTP 模式（`.env` 端口） | `.env:17-18`、`chroma_repository.py:30-98` |
| 审计取用写法 | `~/.hermes/hermes-agent/tools/knowledge_ingest.py:145-147`、`query_kb.py:322` |
