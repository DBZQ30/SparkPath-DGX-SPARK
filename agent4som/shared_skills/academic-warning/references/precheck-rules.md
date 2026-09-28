# 数据齐全性预检口径（precheck）

`python -m academicwarning.cli precheck --grade <grade>` 在 `check` 之前运行，
**只读、无副作用**（不写库、不导出报告），用于一次性判断该年级能否执行选课检查。

## 检查的五类数据

| 序 | 类别 | 必需 | 齐备判定 |
|---|---|---|---|
| 1 | 选课结果（selection） | 是 | 该年级存在 1 份 `parsed_status='done'` 的最新文件 |
| 2 | 学籍名单（roster） | 是 | 同上（检查对象名单的权威基准） |
| 3 | 培养方案（plan） | 是 | 4 个标准专业**各**有 `done` 的最新文件 |
| 4 | 成绩单（grade） | 是 | 4 个标准专业**各**有 `done` 的最新文件 |
| 5 | 通识课程信息表（gen_ed） | 否 | 全局单份；缺失回落内置默认表/众数口径，不阻断 |

标准 4 专业：工商管理、大数据管理与应用、工业工程、会计学（ACCA）。

> 成绩单/培养方案按专业逐个判定——缺哪个专业就点名到专业级（如「缺 工业工程」），
> 而非笼统报「成绩单缺失」。

## 三种结果标志（末行机器可读）

命令除人读清单外，**末行**输出 `[precheck] <FLAG>`，供 skill 分派措辞：

| FLAG | 条件 | skill 应答 |
|---|---|---|
| `READY` | 全部必需项齐备 | 继续执行 `check` |
| `PENDING` | 不齐备，且不齐备项中**至少一项为 `queued`/`parsing`** | “<文件>正在解析，约需几分钟；解析完成后再对我说一句『跑选课检查』即可执行。”**不执行 check、不让重传** |
| `INCOMPLETE` | 不齐备，且不齐备项均为**真缺失或 `failed`** | 列出**全部**不齐备项，指引在小程序“学业预警”页补齐；**不执行 check** |

判定优先级：`READY` > `PENDING` > `INCOMPLETE`。
`failed`（解析失败）归入 `INCOMPLETE`——它对管理员的动作是“补传”，与“等解析”不同。

## 与 run_selection_check 的关系

预检口径与 `service.run_selection_check` 的守卫**同源**（同一套 `latest_source_file` /
`latest_source_files_by_major` 取数 + `STANDARD_MAJORS`），但：

- 预检**只读**，不产生检查批次、不写 meta、不导出报告；
- `run_selection_check` 缺数据时返回**首条**降级文本（如“成绩单未上传”），
  而预检**一次性列全**——所以对话路径应先 precheck 再 check，避免管理员反复试。

## 权限

`precheck` 与 `check` 共用权限校验：命令从会话身份
（`HERMES_SESSION_PLATFORM` / `HERMES_SESSION_USER_ID`）判定 admin/owner，
非管理员返回“无权限：仅管理员可触发选课检查”（退出码 1）。
预检缺数据为**只读提示**，退出码恒为 0。
