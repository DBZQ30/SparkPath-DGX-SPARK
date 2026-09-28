# 2026-05-14 SOM 主干集成：规范流程 RAG 数据层设计（解耦计算引擎）

> **⚠ 历史设计文档**：本文为 2026-05-14 的设计稿。其中 `assistants/{assistant_id}` scope 已被现行 `teachers` 取代；`/sync_kb` 已由现行摄入/同步链路取代。最新实现以 [`som-rag-knowledge-base-design.md`](./som-rag-knowledge-base-design.md) 为准。

## 1. 背景与目标

SOM 需要在主干集成可长期演进的 RAG 能力，支持：

1. 管理员上传培养计划与规则文档（不同年度、不同专业、格式不统一）。
2. 学生上传个人材料并进入个人知识库（严格私有，仅本人可用）。
3. 问答必须可追溯到精确出处（文件、章节/页码或表格范围）。
4. 后续由管理员驱动 Hermes 技能定义不同助手、不同阶段的计算规则。

本设计只交付 **Data Plane（数据读取、抽取、索引、检索、溯源）**，不在主干固化选课计算策略。

## 2. 范围与非目标

### 2.1 本次范围（In Scope）

- 多格式文档读取：`xlsx/xls/csv/docx/pdf`。
- 结构感知解析与切分（禁止固定 token 切片）。
- 强溯源元数据入库并用于回答引用。
- 三层可见性下的索引与检索：`global`、`assistants/{assistant_id}`、`users/{user_id}`。
- 冲突标注与优先级透传（`assistant/global` 高于 `user`）。
- 输出标准化中间数据供 Hermes 技能编排。
- 低置信度规则触发管理员确认卡片。

### 2.2 非目标（Out of Scope）

- 不在主干实现具体“能否选课/下学期必修”算法。
- 不在主干绑定某一助手的长期固定规则引擎。
- 不开放管理员读取学生私有知识库。

## 3. 关键约束（硬规则）

1. **可见性隔离必须严格执行**：
   - 管理员仅可见 `global` 与对应 `assistant`。
   - 学生仅可见 `global`、当前 `assistant` 与自己的 `user`。
   - 任意用户不得读取他人 `users/{user_id}`。

2. **冲突裁决固定**：
   - 学生问题中，若 `assistant/global` 与 `user` 冲突，必须以 `assistant/global` 为准。
   - `user` 仅作为补充参考，回答中必须显式标注。

3. **回答必须可定位溯源**：
   - 仅文件名不够，必须定位到章节/页码/表格区域。

4. **切分必须结构感知**：
   - 禁止固定窗口切片导致语义丢失。

### 3.1 ACL 强制执行矩阵（新增）

为避免“文档有规则、实现无落点”，ACL 必须在四层同时执行：

1. **入口层（Gateway）**：从会话上下文绑定身份，不信任外部传入 `user_id`。
2. **索引写入层（Ingestion）**：按 tier 写入对应目录，禁止跨 tier 落盘。
3. **检索层（Retrieval）**：按 actor+assistant_id 过滤可见 tier。
4. **响应组装层（Answer Composer）**：最终引用与事实再做一次可见性校验。

最小矩阵：

- `student`：可读 `global + assistant + own user`，不可读他人 `user`。
- `admin`：可读 `global + assistant`，不可读任意 `user`。
- `system`（后台任务）：仅按任务绑定 scope 访问，默认最小权限。

## 4. 技术选型：LlamaIndex 采用策略

### 4.1 选型结论

采用 **混合方案**：保留 Hermes 现有三层可见性与 `/sync_kb` 主链路，在文档解析与节点构建层引入 LlamaIndex。

### 4.2 采用理由

- 复杂 Excel（多级表头、合并单元格）与异构文档解析鲁棒性更高。
- 节点与元数据体系便于携带章节、页码、表格范围等溯源信息。
- 与主干现有检索路径兼容改造成本可控，不必全量重写。

### 4.3 采用边界

- LlamaIndex 负责：解析、节点化、元数据增强。
- Hermes 主干负责：权限隔离、可见性控制、命令编排、回答组装。
- 计算策略由后续技能负责，不被 LlamaIndex 绑定。

## 5. 架构：Data Plane / Policy Plane 解耦

### 5.1 Data Plane（本次交付）

1. 文档摄入
2. 结构化抽取
3. 可追溯索引
4. 分层可见性检索
5. 标准化事实输出

### 5.2 Policy Plane（后续技能交付）

1. 不同助手/阶段的规则计算
2. 选课可行性推导
3. 学业规划建议

Policy Plane 只消费 Data Plane 输出的标准化数据与锚点，不重复做文档解析。

## 6. 数据模型与落盘规范

标准化结果按层隔离存储，建议位于 `KN_DATA_BASE` 下新增派生目录（示意）：

- `global/_derived/`
- `assistants/{assistant_id}/_derived/`
- `users/{user_id}/_derived/`

每份文档生成两类资产：

1. `raw_index_nodes`（可检索节点）
2. `normalized_facts`（结构化事实）

### 6.1 raw_index_nodes（示意字段）

- `node_id`
- `source_tier` (`system|assistant|user`)
- `source_file`
- `source_path`
- `doc_version`
- `content`
- `section_title`
- `section_path`（如 `第3章>3.2 先修要求`）
- `page_start` / `page_end`（pdf/docx）
- `sheet_name`（excel/csv）
- `row_start` / `row_end`
- `col_start` / `col_end`
- `anchor_text`（用于核验定位）
- `anchor_locator`（结构化定位：页码/段落路径/cell 地址）
- `source_hash`（源文档哈希）
- `parser_version`（解析器版本）
- `prev_node_id` / `next_node_id`
- `parse_confidence`

### 6.2 normalized_facts（示意字段）

- `fact_id`
- `fact_type`（如 `course_requirement`, `credit_rule`, `prerequisite`）
- `assistant_id`
- `program`（专业）
- `cohort_year`（年级/入学年度）
- `payload`（结构化规则内容）
- `payload_schema_version`（事实结构版本）
- `fact_key`（规范化冲突键）
- `needs_admin_confirmation`（低置信度标记）
- `confidence_score`
- `source_node_ids`（可追溯回 raw nodes）

### 6.3 事实契约（新增，供后续技能稳定消费）

`payload` 不能是自由 JSON。按 `fact_type` 固定 schema（JSON Schema 管理）：

- `course_requirement`：`course_id/course_code`, `program`, `cohort_year`, `term_scope`, `requirement_group`
- `credit_rule`：`min_credits`, `category`, `scope`, `effective_from`, `effective_to`
- `prerequisite`：`prerequisite_expr`, `co_requisite_expr`, `exclusion_rules`

演进规则：

1. 新增字段仅追加，不破坏旧字段语义。
2. 删除字段走双版本窗口（至少一个发布周期）。
3. 每条事实必须带 `payload_schema_version`。

Schema 校验失败降级（新增）：

1. 若结构化抽取结果不满足当前 schema：不得直接丢弃。
2. 该事实转 `needs_admin_confirmation=true` + `pending_review`。
3. 同时保留 `raw_index_nodes`，确保可检索与可追溯不丢失。

## 7. 结构感知切分策略（替代固定切片）

### 7.1 PDF/DOCX

- 按标题层级、段落边界、表格块切分。
- 单节点保留结构路径与页码区间。

### 7.2 XLSX/XLS/CSV

- 先识别逻辑表块（标题区、表头区、数据区、备注区）。
- 展开多级表头与合并单元格语义，生成层级化列名。
- 以“规则单元”或“表内语义组”为节点，而非固定行数切段。

### 7.3 召回后上下文拼接

- 检索命中节点后自动补充必要相邻节点（同节/同表头范围）避免断章。
- 引用锚点仍使用原命中节点，确保可核验。

## 8. 检索、冲突与回答生成

### 8.1 分层检索

- 查询视角为学生时：`global + assistant + own user`。
- 查询视角为管理员时：`global + assistant`。

### 8.2 冲突处理

- 对同一事实键（课程要求/学分规则/先修条件）执行来源优先排序：
  `system/assistant > user`。
- 同一事实键以 `fact_key` 为准，不以自然语言相似度判定。
- 若 `user` 与权威层冲突：
  - 采用权威层结果。
  - 在响应中加“个人材料存在冲突，已按官方规则判定”的提示。

`fact_key` 规范（v1 建议）：

- `assistant_id + program + cohort_year + rule_type + course_id(or group_id)`

冲突矩阵（v1）：

1. 权威层与个人层同 `fact_key` 冲突：权威层覆盖。
2. 权威层之间冲突：按 `doc_version` + `effective_from` 选最新有效版本，并标记告警。
3. 不同 `fact_key`：允许并存，不视为冲突。

权威层内不可自动仲裁冲突（新增）：

当 `doc_version/effective_from` 均无法稳定判定优先级时：

1. 系统不得盲猜。
2. 向用户返回“发现规则冲突”并列出两条冲突来源锚点。
3. 触发后台审计事件，进入管理员确认队列。

### 8.3 回答引用格式

统一支持精确定位：

- `[来源: 2026培养方案.pdf | 第4章 选课要求 | p12-13]`
- `[来源: 培养计划.xlsx | Sheet=课程结构 | 行42-57 | 列B-H]`

## 9. 管理员确认闭环（管理员确认卡片）

当抽取置信度不足或结构歧义明显时：

1. 标记 `needs_admin_confirmation=true`。
2. 发送确认卡片给管理员，展示：
   - 待确认规则摘要
   - 原始定位锚点
   - 推荐解析结果
3. 管理员在卡片上确认/修改。
4. 回写 `normalized_facts` 并触发增量刷新。

确认状态机（新增）：

- `pending_review`：待审核，默认不进入“可用于计算”的事实集合。
- `approved`：审核通过，进入可用集合。
- `rejected`：审核拒绝，不进入可用集合。
- `expired`：卡片过期，回退为 `pending_review` 并进入重发队列。

默认策略（防阻塞）：

1. 在管理员确认前，`pending_review` 事实对外不可见（尤其不可参与策略计算）。
2. 问答可继续进行，但必须显式提示“该文档部分规则待审核”。
3. 批量低置信度场景采用聚合摘要卡片，避免卡片风暴。

确认闭环新增约束：

- 每次确认必须带 `review_id`（幂等键）与版本号。
- 回写采用 CAS（compare-and-set），防止并发覆盖。
- 必须记录审计日志（append-only）：`who/when/before_hash/after_hash/reason/channel_msg_id`。

卡片并发与降噪（新增）：

1. 同一次 `/sync_kb` 仅发送 1 张摘要卡片 + N 条待审条目链接。
2. 对同 `fact_key` 的重复待审项做去重合并。
3. 卡片操作需防重放（一次性 token + 过期时间）。

## 10. Hermes 对接接口（供技能编排）

对技能开放统一读取接口（名称示意）：

- `get_program_facts(session_ctx, assistant_id, filters...)`
- `get_my_student_facts(session_ctx, assistant_id)`
- `get_fact_sources(session_ctx, fact_id)`
- `search_nodes(session_ctx, query, scope...)`

规则技能可在不解析原文档的情况下直接消费事实数据并输出“结论 + 计算摘要 + 引用”。

接口安全约束：

1. 所有读取接口必须接收 `session_ctx`，由服务端绑定身份。
2. 禁止技能侧直接传任意 `user_id` 读取学生事实。
3. 任何越权请求必须返回显式拒绝码并记录审计。

## 11. 兼容当前主干的落地策略

1. 保持 `/sync_kb` 作为管理员主入口。
2. 在 `/sync_kb` 扫描流程中增加“结构化解析与派生资产刷新”。
3. 学生私聊上传文件继续自动入 `users/{user_id}/`，并增量更新 `users/{user_id}/_derived/`。
4. 不破坏已有 `session_search_tool` 的分层检索逻辑，在其输出中增强定位元数据。

`/sync_kb` 兼容契约（v1）新增返回字段：

- `job_id`
- `files_scanned`
- `parsed_nodes`
- `facts_upserted`
- `conflicts_detected`
- `failed_files`
- `partial_failure`（bool）
- `visibility_refresh`

兼容要求：

1. 保留旧字段，不破坏现有调用方。
2. 新字段以追加方式返回。
3. 支持 feature flag 灰度：旧解析链与新解析链并行比对后再切流。

增量键策略（新增）：

为避免“文档未变但解析器升级后无法重建”，增量比较键定义为：

- `ingest_fingerprint = hash(source_hash + parser_version)`

当 `parser_version` 升级时，允许触发存量文档再解析（re-ingest）。

## 12. 风险与缓解

1. **复杂 Excel 误解读**
   - 缓解：低置信度卡片确认 + 可回滚版本。

2. **文档模板频繁变化**
   - 缓解：结构感知解析 + 提取失败自动降级并提示人工确认。

3. **引用定位不稳定**
   - 缓解：固定 `anchor_text + path + range` 三元定位，避免仅靠页码。

4. **权限越界风险**
   - 缓解：读取层统一走 tier ACL 检查，严禁跨 `user_id`。

5. **性能与回归风险**
   - 缓解：定义 SLO 与基准集，解析器升级必须跑回归。

6. **身份生命周期风险**
   - 缓解：提供 `PurgeUserKBPipeline`，处理注销/毕业用户的数据清理与解绑。

建议 SLO（初版）：

- `/sync_kb` 增量刷新 P95 耗时 <= 60s（典型批量）
- 检索响应 P95 <= 2s（缓存命中场景）
- 引用回放命中率 >= 99%
- 复杂 Excel 解析字段级准确率 >= 95%（基准集）

## 12.1 用户身份生命周期与清理（新增）

为防止 `user_id` 注销后遗留隐私数据：

1. 提供后台清理管线 `PurgeUserKBPipeline(user_id)`。
2. 清理范围：`users/{user_id}/` 与 `users/{user_id}/_derived/` 及对应向量集合。
3. 清理动作必须写审计日志并支持 dry-run。
4. 若平台存在 `user_id` 复用风险，默认启用“历史ID不可复用绑定”。

## 13. 验收标准（Data Plane）

1. 可接收并解析 `xlsx/xls/csv/docx/pdf`。
2. 任一回答均能附带可定位出处（章节/页码或表格范围）。
3. 学生私有文档仅本人可检索；管理员不可见。
4. 冲突时权威层优先，且有冲突提示。
5. 低置信度抽取可通过管理员确认卡片修正后生效。
6. 技能可直接读取标准化事实，无需二次文档解析。
7. 越权访问负向测试通过（管理员读学生私库、学生读他人私库均拒绝）。
8. `/sync_kb` 新旧兼容字段回归通过，且支持灰度回滚。
9. `pending_review` 事实在未审核前不参与策略计算。
10. 权威层内不可仲裁冲突能够被显式抛出并触发审计。

## 14. 后续计划（非本次）

- 由管理员驱动技能生成：
  - “能否选择某课程”
  - “下学期必须选哪些课程”
  - “学分缺口与风险提醒”

上述能力基于本次 Data Plane 输出构建，按助手/阶段独立演化。

## 15. 修订记录（2026-05-14，专家检视后）

本次根据专家检视补充：

1. 新增确认状态机与默认策略，明确 `pending_review` 不参与计算。
2. 新增卡片并发降噪与过期回退策略，降低卡片风暴风险。
3. 新增 schema 校验失败降级路径：转人工确认，不直接丢弃。
4. 新增权威层内不可自动仲裁冲突的抛出与审计策略。
5. 新增增量键 `hash(source_hash + parser_version)`，支持解析器升级重建。
6. 新增用户身份生命周期清理管线 `PurgeUserKBPipeline`。
