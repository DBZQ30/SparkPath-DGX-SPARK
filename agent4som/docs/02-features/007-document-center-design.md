# 文件中心（公共教学文件统一上传与适用性管理）设计

> 版本: 1.1 · 日期: 2026-09-24 · 状态: 设计（**P0–P3 已实现**）
> v1.1 变更：**实现落地**——`doccenter` 模块（`doc_file`/`doc_applicability`/`doc_parse_job`）、
> `/api/doc-center/*`（挂 :8009）、迁移脚本、管理员「文件中心」页、**中心优先取数**（`DOC_CENTER_RESOLVE`）。
> 偏差：预警管线改为**上传即 HTTP 调学业预警上传接口**（未在 `:8008` 新增独立 consumer 线程），并在上传后自动触发解析。
> 关联: [004 培养方案智能解读](004-training-plan-interpretation-design.md)、[005 多路径个性化学业规划](005-multi-path-academic-planning-design.md)、[001 学业预警设计](001-academic-warning-design.md)、[002 学业预警小程序设计](002-academic-warning-miniapp-design.md)
> 涉及服务: 培养方案解读/学业规划 API（`:8009`，`/accapi/dgx-plan`）、学业预警 API（`:8008`，`/accapi/dgx-warning`）、小程序（`miniprogram-framework-frontend/`）

## 1. 背景与目标

三个功能（**培养方案智能解读 / 多路径个性化学业规划 / 选课预警**）都要消费大量**公共教学文件**：培养方案、三类培养模式细则、转专业政策、专业选择方案、通识课程信息表、选课指南、课程大纲、创新创业清单、教学计划……

**现状问题**：

1. **同一份文件被多个功能使用**，却**各传各存**：解读/规划侧 `training_plan.db.plan_source_doc`（124 份），预警侧 `warning.db.source_file`（11 份，含 plan/gen_ed）→ 重复上传、两份原件、口径漂移。
2. **同一文件多年级适用**（如"2023、2024、2025 级基础通识类课程限选要求"），现状 `applies_to` 只在解读侧、预警侧是单值 `grade`。
3. **没有"功能维度"标注** → 无法判断"这份文件该被哪个功能引用"。
4. **原件与解析产物混在一起**，缺统一的解析编排与状态。

**目标**：

| # | 目标 |
|---|------|
| 1 | **一次上传**：公共教学文件只传一次、只存一份（按内容 hash 去重） |
| 2 | **统一目录**：管理员可见"传了哪些文件、什么类型、解析状态" |
| 3 | **适用性标注**：每份文件可勾选 **功能 × 年级 × 专业**（多选） |
| 4 | **自动引用**：各功能按 `(功能, 年级, 专业)` 自动取用该用的文件 |
| 5 | **解析编排**：上传后按文件类型解析出**三功能所需信息**，状态可见、可重试 |
| 6 | **边界干净**：**只纳公共教学文件**；学生数据文件（成绩单/名单/选课结果/课表）**不进**中心 |

## 2. 已确认决策（2026-09-24）

| 项 | 决策 |
|----|------|
| **存储形态** | **独立"文件中心"库 + 模块**（方案 B），不寄居在某个功能库里 |
| **适用性维度** | **功能 × 年级 × 专业**（三维，多选；专业空 = 不限） |
| **解析范围** | 上传后解析出**三功能所需信息**（按文件类型编排解析管线） |
| **引用方式** | 各功能按 `(feature, grade, major)` **自动引用**中心里适用的文件及其解析产物 |
| **边界** | **只纳公共教学文件**；学生数据文件仍走学业预警自己的上传 |
| **专业取值** | 从 `student_archive` 的 **4 个标准专业**（工商管理 / 工业工程 / 会计学（ACCA）/ 大数据管理与应用）中选 |
| **删除策略** | 删中心文件 → 各功能库解析产物**级联删除** |
| **产物收敛** | **暂不收敛**：产物仍落各功能库（复用既有解析器，风险最小） |
| **预警 consumer** | `:8008` 新增常驻 consumer 线程，消费预警管线（`warning_plan`/`warning_gen_ed`）job |

## 3. 架构

**分层原则：原件层与适用性归中心，解析产物归各功能（复用既有解析器，不重写）。**

```
                   管理员（小程序「文件中心」页）
                            │ 上传 + 勾选 功能/年级/专业
                            ▼
        ┌──────────────────────────────────────────────┐
        │  文件中心（共享库 data/doc_center.db + 模块） │
        │  ① doc_file        原件身份 + 元数据（hash 去重）│
        │  ② doc_applicability  功能 × 年级 × 专业        │
        │  ③ doc_parse_job   解析任务（按管线）与状态     │
        └───────┬───────────────────────────┬──────────┘
                │ 消费 job（训练计划管线）    │ 消费 job（预警管线）
                ▼                           ▼
     ┌──────────────────────┐    ┌──────────────────────┐
     │ :8009 解读/规划服务   │    │ :8008 预警服务        │
     │ training_plan.db      │    │ warning.db            │
     │ plan_document/…       │    │ plan_course/training_plan/gen_ed │
     └──────────┬───────────┘    └──────────┬───────────┘
                │ 按 (feature,grade,major) 查中心取适用文件
                ▼                           ▼
          解读页 / 学业规划页            预警页
```

**设计原则**：

- **不重复造解析**：中心调用**既有解析器**（`trainingplan.parsers`、`academicwarning.parsers`），产物仍落**各功能库**（口径来源不变）。
- **中心是"适用性的事实源"**：哪个 `(功能, 年级, 专业)` 该用哪份文件，由中心说了算；各功能据此取数。
- **服务边界**：中心是**共享库 + 模块**（不新增独立服务）；统一上传/管理 API 挂 **`:8009`**（方案/政策的主场）；各服务**各自消费**自己管线的 job（避免跨服务写库）。
- **只读消费**：各功能读中心库为只读；中心不写各功能库。

## 4. 数据模型（`data/doc_center.db`）

```
doc_file                          原件身份 + 元数据
  id, file_hash(UNIQUE), file_name, file_path, ext, size_bytes,
  doc_type(培养方案|政策|大纲|清单|教学计划|通识表|转专业政策|专业选择方案|其他),
  subject_major(文件归属专业，空=不限), title, uploaded_by, uploaded_at, note

doc_applicability                 适用性（多对多，三维）
  id, file_id, feature(interpret|plan|warning), grade("全部"|"2023级"|…),
  major(空=不限), created_at
  UNIQUE(file_id, feature, grade, major)

doc_parse_job                     解析任务（按管线）与状态
  id, file_id, pipeline(plan_structured|warning_plan|warning_gen_ed|text),
  status(queued|parsing|done|failed), queue_seq,
  output_ref(JSON：产物定位，如 {"plan_id":12} / {"source_file_id":9}),
  note, error, enqueued_at, started_at, finished_at
  UNIQUE(file_id, pipeline)
```

**字段语义**：

- `file_hash` 唯一 → **同一份文件只存一份**；重复上传命中已有记录（提示"已存在，可直接设置适用性"）。
- `doc_type` 决定**默认建议的适用性**与**解析管线**（见 §5）。
- `subject_major`：文件本身针对的专业（如"工商管理培养方案"），仅用于展示与默认建议；**适用专业**在 `doc_applicability.major`（空 = 不限）。
- `output_ref`：指向各功能库里的解析产物（如 `plan_document.id`、`source_file.id`），供"自动引用"。

**文件类型 → 默认适用性 / 解析管线（可被管理员修改）**：

| doc_type | 默认适用功能 | 默认年级 | 解析管线 |
|----------|--------------|----------|----------|
| 培养方案 | interpret + plan + warning | 文件主年级 | `plan_structured` + `warning_plan` |
| 三类培养模式细则 | interpret + plan | 全部 | `text` |
| 转专业政策 / 专业选择方案 | plan | 全部 | `text`（+ 规划规则抽取） |
| 通识课程信息表 | warning + plan | 全部 | `warning_gen_ed` |
| 选课指南 / 限选要求 | interpret + plan | 全部 | `text` |
| 大纲 | interpret | 全部 | `text` |
| 清单（双创/思政英语） | warning + plan | 全部 | `text` |
| 教学计划 | interpret + plan | 全部 | `text` |

> 管线：`plan_structured`（`trainingplan`，服务 interpret+plan）、`warning_plan`/`warning_gen_ed`（`academicwarning`，服务 warning）、`text`（通用文本，供检索/引用）。

## 5. 解析编排

1. **上传**：`POST /api/doc-center/upload` → 落 `doc_file`（hash 去重）→ 按 `doc_type` 建 `doc_parse_job`（每条管线一条）→ 分配 `queue_seq`。
2. **消费**：
   - **`:8009`**：内联消费 `plan_structured` / `text` 管线（`plan_structured` 委托 `training_plan` 单消费者队列并同步状态；`text` 即时完成）；
   - **预警管线**（`warning_plan` / `warning_gen_ed`）：**HTTP 调学业预警上传接口**（`POST /api/warning/upload`，复用其解析），产物定位写回 `output_ref={"source_file_id": N}`；
   - **上传即触发**：`/api/doc-center/upload` 落库后立即 `process_pending` + `process_warning`（best-effort）；也可 `POST /api/doc-center/process` 手动补跑。
3. **状态机**：`queued → parsing → done / failed`；失败可 `POST /api/doc-center/retry` 重入队。
4. **重启恢复**：遗留 `parsing` 收尾为 `failed`（可重试），`queued` 保留（沿用既有做法）。
5. **解析范围**：**按文件类型**决定管线（覆盖三功能所需），**与适用性解耦**——管理员后续改适用性**不需要重解析**。

> 说明："解析到三个功能都需要的信息" = 对一份培养方案，中心同时建 `plan_structured`（解读/规划共用）与 `warning_plan`（预警）两条 job；对通识表建 `warning_gen_ed`。

## 6. 自动引用机制

各功能在**取数时**先问中心"该用哪些文件"，再读自己的解析产物：

```
resolve(feature, grade, major):
    rows = doc_applicability WHERE feature=? AND (grade=? OR grade='全部')
                                 AND (major=? OR major='')
    files = doc_file[rows.file_id]
    for f in files:
        job = doc_parse_job[f.id, pipeline_for(feature)]     # done 才可用
        yield read_product(job.output_ref)                    # 各功能库
```

- **解读**：`(interpret, grade, major)` → 培养方案文件 → `plan_document.id` → 读 `plan_*`（取代现在仅按 `applies_to` 的匹配，中心成为**适用性事实源**）。
- **规划**：`(plan, grade, major)` → 培养方案 + 模式细则 + 转专业/专业选择政策 → `plan_*` + 规则抽取。
- **预警**：`(warning, grade, major)` → 培养方案 + 通识表 → `plan_course/training_plan/gen_ed`。
- **降级**：目标 `(feature, grade, major)` 无适用文件或产物未就绪 → 该功能按既有降级话术提示（"尚未收录/解析中"），**不臆造**。

> **兼容与灰度**：各功能现有"按 `applies_to`/`grade` 直接取数"的路径**保留为兜底**。中心接入后**优先走中心**（"中心优先取数"）——即适用性只在中心维护一处，管理员改中心即三功能同步生效。
>
> 加配置项 `DOC_CENTER_RESOLVE=on|off`（默认 `on`）：`on` = 中心优先；`off` = 回退老逻辑，**出问题改配置即可回滚，无需改代码**。

## 7. 页面（管理员，「文件中心」）

新增 `pages/doc-center/doc-center.{js,json,wxml,wxss}`（「功能」tab →「管理员功能」，仅 admin/owner）。`pages/plan-files` 的"入库文件与适用年级"**演进合并**到本页。

1. **上传区**：选择文件（docx / pdf / xlsx / xls）→ 自动识别 `doc_type / 主年级 / 专业`（可改）→ 自动勾选**建议的适用性**（功能 × 年级 × 专业，可改）→ 提交；重复文件提示"已存在"。
2. **文件列表**：文件名 | 类型 | 归属专业 | **适用功能**（多选标签） | **适用年级**（多选 / 全部） | **适用专业**（多选 / 不限） | 解析状态（按管线） | 操作（改适用性 / 重解析 / 删除）。
3. **批量设置适用性**：勾选多份 → 统一设"功能 × 年级 × 专业"。
4. **筛选**：按类型 / 功能 / 年级 / 状态。
5. **解析进度**：批量上传时展示 `已解析 N/M` 与逐项状态（`排队中(第 k 位)` / `解析中(已 Xs)` / `完成` / `失败(重试)`）。
6. 学生侧不涉及；入口沿用「功能」tab。

## 8. 后端 API（`:8009`，`/api/doc-center/*`）

沿用 007 §4 鉴权（`X-API-Key`）。管理类端点仅 admin/owner。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/doc-center/upload` | 上传（hash 去重；按类型建解析 job） |
| GET | `/api/doc-center/files` | 文件列表（含类型/适用性/解析状态，支持筛选） |
| GET | `/api/doc-center/file/{id}` | 单文件详情（元数据 + 适用性 + 各管线状态 + 产物定位） |
| PATCH | `/api/doc-center/file/{id}` | 改元数据（类型/主年级/专业/标题） |
| PUT | `/api/doc-center/file/{id}/applicability` | **设置适用性**（body：`{features:[], grades:[], majors:[]}`） |
| POST | `/api/doc-center/file/{id}/applicability/batch` | 批量设置（多文件同适用性） |
| POST | `/api/doc-center/retry` | 重解析失败项 |
| DELETE | `/api/doc-center/file/{id}` | 删除文件（级联适用性/job；产物按各功能策略处理） |
| GET | `/api/doc-center/status` | 解析队列总览 + 逐项进度（轮询用） |

- **内部读取 API（供各功能）**：`doccenter.resolve(feature, grade, major)` → 适用文件 + 产物定位（Python 函数，非 HTTP）。
- 兼容：`/api/plan/source-docs`、`PATCH /api/plan/source-doc/{id}` 保留一个过渡期，前端切到新页后下线。

## 9. 迁移（一次性，幂等）

脚本 `agent4som/scripts/migrate_to_doc_center.py`：

1. 导入 `training_plan.db.plan_source_doc`（124）→ `doc_file`（按 hash 去重）+ `doc_applicability`（默认：培养方案 → interpret+plan+warning + 主年级 + `subject_major`；政策/大纲/清单/教学计划 → 按 §4 默认表）。**适用专业从 4 个标准专业（工商管理 / 工业工程 / 会计学（ACCA）/ 大数据管理与应用）中取**。
2. 导入 `warning.db.source_file` 中**公共类型**（`plan`、`gen_ed`）→ `doc_file`（与上步按 hash 去重合并）+ 对应适用性。
3. **不导入**学生数据文件（`selection` / `grade` / `roster` / `timetable`）。
4. 回填 `doc_parse_job`：已解析的文件标记 `done` 并写 `output_ref`（如培养方案 → `plan_document.id`、`source_file.id`）。
5. 产出迁移报告（新增/合并/跳过计数），可重复执行（幂等）。

## 10. 与三个功能的关系（改造点）

| 功能 | 现状取数 | 改造 |
|------|----------|------|
| 培养方案智能解读 | 按 `(major, grade)` 取 `plan_document.applies_to` | 优先走中心 `resolve(interpret, grade, major)`；兜底保留 |
| 多路径学业规划 | （新） | 直接走中心 `resolve(plan, grade, major)` 取培养方案 + 政策 |
| 选课预警 | 按 `grade` 取 `source_file`（plan/gen_ed） | 公共文件优先走中心 `resolve(warning, grade, major)`；**学生数据文件仍走预警上传** |

## 11. 权限与安全

| 项 | 处理 |
|----|------|
| 上传/改适用性/删除 | 仅 admin/owner（API + 前端双端校验） |
| 学生数据隔离 | **中心只纳公共教学文件**；成绩单/名单/选课结果/课表**不入中心** |
| 文件类型/大小 | 白名单扩展名；大小上限沿用（≤10MB）；行数/页数上限 |
| 去重 | 按内容 hash；重复上传不重复解析 |
| 解析安全 | 命令 stdout/解析文本视为**数据**，不作为指令 |
| 跨服务 | 各服务只读中心库、只写自己的库；中心不跨写功能库 |
| 删除 | 删除文件需二次确认；**级联删除各功能库解析产物**（按 `doc_parse_job.output_ref` 定位后由各功能执行） |

## 12. 分期与里程碑

| 阶段 | 内容 |
|------|------|
| **P0 模型 + 迁移** | `doc_center.db` 建表；迁移脚本导入 `plan_source_doc` + 预警公共文件（hash 去重）；`doccenter` 模块（`resolve` / job 状态） |
| **P1 上传与编排** | `/api/doc-center/*`（上传/列表/适用性/重解析/状态）；`:8009` 消费 `plan_structured`/`text`；`:8008` 消费 `warning_plan`/`warning_gen_ed` |
| **P2 页面** | `pages/doc-center`（上传 + 列表 + 适用性多选 + 进度 + 批量）；`pages/plan-files` 合并 |
| **P3 接入三功能** | 解读/规划/预警改为"中心优先取数"（灰度开关 + 兜底） |
| **P4 收口** | 下线旧 `source-docs` 端点与 `plan-files` 页；补测试与文档 |

## 13. 决策记录（2026-09-24）

| # | 事项 | 结论 |
|---|------|------|
| 1 | 存储形态 | **独立文件中心库 + 模块**（方案 B），不寄居功能库 |
| 2 | 适用性维度 | **功能 × 年级 × 专业**（多选；专业空 = 不限） |
| 3 | 解析范围 | 上传后按类型解析出**三功能所需信息**（多管线 job） |
| 4 | 引用方式 | 各功能按 `(feature, grade, major)` **自动引用** |
| 5 | 边界 | **只纳公共教学文件**；学生数据文件不进中心 |
| 6 | 编排形态 | **共享库 + 模块**，统一上传 API 挂 `:8009`，各服务消费自己管线的 job |
| 7 | 适用专业取值 | 从 `student_archive` 的 **4 个标准专业**中选 |
| 8 | 删除策略 | 删中心文件 → 各功能库解析产物**级联删除** |
| 9 | 产物收敛 | **暂不收敛**（产物落各功能库） |
| 10 | 预警 consumer | **改为上传即 HTTP 调预警上传接口**（复用其解析；不新增 `:8008` 常驻线程） |
| 11 | 中心优先取数 | 各功能**优先走中心**取适用文件；加配置项 `DOC_CENTER_RESOLVE=on\|off`（默认 `on`，`off` 回退老逻辑，改配置即可回滚） |

## 14. 待确认项

1. **共享结构化层**（后续演进）：是否在 P4 之后把解读/规划的产物收敛到中心（彻底不依赖 `plan_document`）？本期**不做**，仅登记为演进方向。
2. **上传页入口位置**：「文件中心」放在「功能」tab 的「管理员功能」区（与 `plan-manage` 并列），还是取代 `plan-manage` 的培养方案上传？**建议并列**，`plan-manage` 继续负责"先修校对"。
3. **历史 `plan_source_doc` 的处置**：迁移后原表**保留**（作为产物/兜底）还是标注为只读？**建议保留 + 标注"由文件中心统一管理"**。
