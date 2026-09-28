# 测试数据分层：合成单测 + 开发机真实样本集成测试

> 2026-09-26 · 批次三十四
> 背景：用户提供了真实数据（`~/trans/acc/`：成绩单 docx／培养方案解读书包／选课结果 xlsx），
> 并确定策略——**简单 Excel 构造合成数据做单元测试；其余复杂文档放开发者测试环境做
> 集成测试；完全避免隐私与校内机密数据泄露。**

## 1. 分层模型

| 层 | 数据 | 运行环境 | 入库 | 标识 |
|---|---|---|---|---|
| 合成单测 | xlwt/openpyxl 自造 xls/xlsx、stub docx 表 | 任何检出（含 CI） | ✅ 提交 | 无（默认） |
| 真实样本集成测试 | 成绩单／培养方案／大纲／选课结果原文件 | 仅开发机 | ❌ gitignored | `samples` marker |

真实样本目录（在仓库工作树内但被 `.gitignore` 排除）：

- `agent4som/academicwarning/docs/`：`2023级工商管理成绩单.docx`（40 份成绩单表）、
  `2023级26-27学年第一学期的选课结果.xlsx`、`2023版工商管理专业培养方案.docx`、
  成绩单金标准 `grade_golden.json`
- `agent4som/data/SmartGuide/`：4 份 2023 版培养方案 docx、大纲 docx、通识课程信息表
  xlsx、选课结果 xlsx 等

样本缺失时，依赖样本的用例自动 skip（沿用既有 `skipif("缺少样本")` 约定）；
政策类用例（转专业实施细则／专业选择实施方案 PDF）按文件名关键词细粒度跳过，
培养方案驱动的用例不受影响。

## 2. 金标准外置（grade_golden.json）

成绩单解析的**真值断言**（课程名、原始成绩、学期分布、块结构）从测试代码外置到
`academicwarning/docs/grade_golden.json`：

- 文件含真实课程名与成绩 → 所在目录整体 gitignored，`tests/security/test_sample_isolation.py`
  断言其被 check-ignore 覆盖；
- 测试侧只保留结构断言名称（`count`/`sem_seq`/`blocks`…），期望值全部读 JSON；
  样本或 JSON 缺失 → skip；
- 样本版本变化（如 2026-09-26 新导出 #18 表新增军训成绩 72→73 门）只改 JSON，
  不动测试代码。

## 3. PII 卫生（committed 文件零真实标识）

历史测试代码内嵌了真实学号/姓名（7 学号 + 7 姓名 + 拼音函数名），本轮全部清除。
**本设计文档与安全门测试自身也不落任何真实标识**（防"清查清单变泄露清单"）——
历史清单只存在开发机 gitignored 的 `academicwarning/docs/pii_blacklist.json`，

- **学号合成约定**：`9` 开头 10 位数字（`9000000001`…），与真实学号形状
  （`2` 开头）区分；
- **姓名合成约定**：`学生甲/学生乙/…`、`学生一六`（表 #16）等；
- 涉及真实学生案例的注释/文档改为语义化称呼（"金标准学生"）；拼音标识符
  （kunting/wulinchen 函数名）重构为表号语义命名；
- 设计文档/脚本中的示例值同步替换（docs/005、docs/006、docs/018、
  `split_skills` 的 deid 示例、`scripts/deidentify_warning_db.py`）。

安全门 `tests/security/test_sample_isolation.py`（OWASP 类别：数据最小化）：

- S10 `git ls-files`（`core.quotepath=false`，防非 ASCII 路径引号包裹漏检）零条
  `academicwarning/docs/`、`data/` 路径——唯一白名单例外是学分结构 JSON
  （`service.py` 运行时资产，`-f` 强制入库记录在案，无学生个人信息）；
- S11 `grade_golden.json` 被 .gitignore 覆盖；
- S12 跟踪文本文件无 `(?<!\d)2\d{9}(?!\d)` 学号形状字面量（无样本依赖，恒跑）；
- S13 历史清查确认的真实姓名不回流（黑名单在开发机 gitignored 的
  `academicwarning/docs/pii_blacklist.json`，缺省时 skip——干净检出不落名单本体）。

## 4. `samples` marker

`pyproject.toml` 注册 `samples`；含义=依赖 gitignored 真实样本、仅开发机可跑：

- 模块级：`tests/trainingplan/{test_trainingplan,test_route,test_simulate,test_policy}`、
  `tests/doccenter/test_doccenter.py`、`tests/academicwarning/test_parsers_selection.py`
- 单用例级（合成与真实混排）：`test_parsers_grade.py`、`test_service_upload.py`、
  `test_api.py`、`test_grade_partition.py`、`test_roster.py` 的对应用例

干净检出离线全跑：`pytest tests/ -m "not integration and not network and not samples" -q`。

## 5. 本轮发现并修复的问题

- **`_record_upload_failed`（`academicwarning/service.py`）占位收尾丢 meta**：v1.10
  上传队列占位行的 `in_file_meta` 已含 `major`，解析失败收尾时整体替换为
  `{"error": …}` → `major` 丢失 → `/status` 无法按专业归组 → 前端显示"未上传"
  而非"解析失败"。修复：占位路径**合并**写入（读回原 meta 仅补 `error`）。
  首次暴露于开发机真实样本跑 `test_upload_failed_finishes_queued_as_failed`
  （此前该用例因样本缺失一直 skip）。
- 修复该 bug 时引入过的次生错误一并修正：连接未设 `row_factory`，
  `row["in_file_meta"]` 须用元组下标 `row[0]`。

## 6. 开发机样本配置（从 `~/trans/acc/`）

```bash
# 成绩单（重命名为测试约定名）
cp ~/trans/acc/工商管理2301成绩单.docx agent4som/academicwarning/docs/2023级工商管理成绩单.docx
# 培养方案解读书包（解压后取所需文件）
unzip ~/trans/acc/培养方案智能解读.zip -d ~/trans/acc/_sg
cp ~/trans/acc/_sg/培养方案智能解读/2023版*专业培养方案.docx agent4som/data/SmartGuide/
cp ~/trans/acc/_sg/培养方案智能解读/27、28、29-管理学大纲-王磊.docx agent4som/data/SmartGuide/
cp ~/trans/acc/_sg/培养方案智能解读/通识课程信息表\(1\).xlsx agent4som/data/SmartGuide/
# 选课结果
cp ~/trans/acc/选课结果.xlsx agent4som/academicwarning/docs/2023级26-27学年第一学期的选课结果.xlsx
```

仍缺（相关用例保持 skip）：`2023级学籍信息.xls`、`2023级学籍异动详情.xls`、
工业工程成绩单 docx、转专业实施细则/专业选择实施方案 PDF。

> ⚠️ 注意：zip 内的通识课程信息表(1).xlsx **无学分列**，与代码资产
> `academicwarning/docs/通识课程信息表.xlsx`（内置默认表，用户决定不入库）结构不同，
> 不能作为其替代品放置（`_parse_gen_ed_xlsx` 解析得 0 行）。
