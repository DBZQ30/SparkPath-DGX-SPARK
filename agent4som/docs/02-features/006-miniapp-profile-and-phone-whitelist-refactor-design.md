# 小程序「我的档案」重构与电话白名单分角色上传 设计

> 版本: 1.0 · 日期: 2026-09-24 · 状态: 已实现
> 关联: `002-academic-warning-miniapp-design.md`（小程序模块设计总纲）、`003-phone-sms-verification-design.md`（手机号验证/绑定）
> 涉及仓库: `miniprogram-framework-frontend/`（小程序前端）、`agent4som-hermesagent/plugins/miniapp-platform/`（methods API 后端）

## 1. 背景与目标

当前小程序存在两处需要重构：

1. **「我的档案」入口与内容**：
   - 「功能」tab →「我的功能」区有一张「我的档案」卡片，与「我的」tab 的档案入口重复，需删除；
     档案只在「我的」tab 展示。
   - 现有档案字段是 MBA 招生场景的自填信息（姓名/电话/是否在校学生职工/学号/性别/年龄/毕业院校/项目经历），
     与教务场景不符。需按**学籍信息**（参考 `data/熊伟-2023级学籍信息.xls`）重新设计字段。
   - **学生本人不可修改**档案；**管理员可修改已上传的学生信息**。

2. **电话白名单上传入口**：
   - 现为单一「批量导入 Excel」入口，文件内「角色」列决定身份，易传错。
   - 改为**学生 / 教师 / 管理员三个独立上传入口**，各自接口固定角色，文件内不再需要角色列。

## 2. 已确认决策（2026-09-24 访谈）

| 项 | 决策 |
|----|------|
| 档案数据来源 | 管理员上传的**学籍信息**；学生只读，管理员可改 |
| 档案匹配键 | **手机号优先，学号兜底**（手机号来自已验证绑定；学号来自用户自助匹配或历史自填） |
| 学籍信息上传入口 | **与白名单「学生信息」上传合并**：一次上传同时写学籍档案与白名单 |
| 白名单上传接口 | **三个**：学生接口上传的人全部是学生；教师接口全部是教师；管理员接口全部是管理员（角色由入口固定） |
| 前端入口 | 「功能」tab 删除「我的档案」卡片；档案只在「我的」tab；管理员在「功能」tab 新增「学生档案管理」 |

## 3. 数据模型

### 3.1 新表 `student_archive`（学籍档案，methods DB）

主键 `student_id`（学号）。字段与 Excel 表头映射（按表头名定位，顺序/多余列忽略）：

| Excel 表头（含别名） | 列名 | 说明 |
|------|------|------|
| 学号 / 工号 | `student_id` | 主键 |
| 姓名 | `name` | |
| 性别 | `gender` | |
| 出生日期 | `birth_date` | 归一为 `YYYY-MM-DD` |
| 民族 | `ethnicity` | |
| 政治面貌 | `political_status` | |
| 所属书院 / 书院 | `residence_college` | |
| 年级 | `grade` | |
| 学院 | `school` | |
| 系 | `department` | |
| 托管院系 | `host_department` | |
| 专业 | `major` | |
| 专业方向 | `major_direction` | |
| 学制 | `study_years` | |
| 班级 | `class_name` | |
| 是否在籍 | `enrolled` | |
| 入学日期 | `enroll_date` | 归一为 `YYYY-MM-DD` |
| 入学年级 | `enroll_grade` | |
| 入学专业 | `enroll_major` | |
| 个人手机 / 手机号 / 手机号码 / 电话 | `phone` | 白名单/匹配依据 |
| 个人邮箱 / 邮箱 | `email` | |
| 紧急联系人 | `emergency_contact` | |
| 紧急联系方式 | `emergency_phone` | |

另含 `source_batch_id`、`updated_at`、`created_at`。手机号建索引（`idx_student_archive_phone`）。

### 3.2 白名单角色

复用现有 `phone_whitelist` 表，`role` 由上传接口固定（`student` / `teacher` / `admin`），
文件内「角色」列在三个新接口中被忽略（旧 `/import` 兼容入口仍读角色列）。

## 4. 后端 API

均在 methods API（`agent4som-hermesagent/plugins/miniapp-platform/adapter.py`），
鉴权 `Authorization: Bearer <session_token>`；白名单与档案维护端点要求 `admin`/`owner`。

### 4.1 电话白名单导入（三个入口）

| 端点 | 角色 | 模板 | 说明 |
|------|------|------|------|
| `POST /api/methods/phone-whitelist/import/student` | 固定 `student` | **学籍信息表**（学号/姓名/个人手机 + 档案字段） | ① upsert `student_archive`；② 有合法手机号的行写 `phone_whitelist`（staff_id=学号，name=姓名）。无手机号的行只入档案 |
| `POST /api/methods/phone-whitelist/import/teacher` | 固定 `teacher` | 简版：姓名 / 电话 / 工号 | upsert 白名单 |
| `POST /api/methods/phone-whitelist/import/admin` | 固定 `admin` | 简版：姓名 / 电话 / 工号 | upsert 白名单 |

- 三个入口共用实现 `_import_whitelist(request, fixed_role=..., student_mode=...)`；
  旧 `POST /api/methods/phone-whitelist/import`（文件内角色列）保留兼容，前端不再使用。
- 支持 `.xlsx` / `.xls`（`.xls` 需 `xlrd`）；`multipart/form-data` 字段：`file`、`label`（名单名）、`dry_run`（`1` 预览）。
- 语义：库中已存在或文件内重复 → 后到覆盖；坏行跳过并逐条返回原因（`errors` 截前 100）。
- 响应新增 `archive_imported` / `archive_updated`（学生接口）；`role_sync` 逻辑不变。
- `dry_run=1` 仅解析与计算，不写库，返回 `preview.downgrades` 供前端二次确认（沿用既有机制）。

### 4.2 学籍档案查询 / 编辑

| 端点 | 角色 | 说明 |
|------|------|------|
| `GET /api/methods/student-archive?keyword=&limit=` | admin/owner | 按 姓名/学号/手机号 模糊检索；`limit` 默认 200、上限 500 |
| `PUT /api/methods/student-archive` | admin/owner | body `{student_id, ...可编辑字段}`；仅更新传入字段，学号不可改 |
| `POST /api/methods/student-archive/link` | 任意登录用户 | body `{student_id}`；命中档案则写入 `admission_profiles.student_staff_id`，作为手机号之外的兜底匹配键 |

### 4.3 档案读取（自助）

`GET /api/methods/admission-profile` 响应新增：

```json
{"has_profile": true, "phone": "...", ...,
 "archive": { "student_id": "...", "name": "...", ... },
 "archive_match": "phone" }
```

- `archive`：匹配到的学籍档案（无则 `null`）；`archive_match`：`phone` / `student_id` / `null`。
- 匹配顺序：`phone_verified=1` 的手机号 → `student_archive.phone`；否则 `student_staff_id` → `student_archive.student_id`。

## 5. 小程序前端

### 5.1 「功能」tab（`pages/methods`）

- **删除**「我的功能」区「我的档案」卡片（`methods.wxml`）及 `tapProfile` 处理函数。
- 「管理员功能」区**新增**「学生档案管理」卡片（仅 `role === 'admin' || 'owner'`）→ `pages/student-archive/student-archive`。

### 5.2 「我的」tab 档案页（`pages/profile`，重写）

- **只读**展示：档案字段来自 `GET admission-profile` 的 `archive`，按学籍字段顺序渲染，空值不显示。
- **手机号卡片**：保留绑定入口（`phoneAuthMode`：manual 手动绑定 / wechat 授权），绑定成功后自动刷新档案。
- **未匹配空态**：提示档案由管理员上传；提供「输入学号匹配」输入框（调 `/student-archive/link`）。
- **管理员**：显示「学生档案管理」入口。
- 移除原自填「创建/编辑档案」表单（学生不可修改）。

### 5.3 学生档案管理页（`pages/student-archive`，新增，仅 admin/owner）

- 搜索框（姓名/学号/手机号）+ 列表（`姓名（学号）` + `专业 · 班级 · 手机`）。
- 点击条目 → 底部弹层编辑 22 个可编辑字段（学号为主键只读）→ `PUT /student-archive`。
- 学籍信息由「电话白名单 → 导入学生信息」上传，本页只做检索与修改。

### 5.4 电话白名单页（`pages/phone-whitelist`）

- 原「批量导入 Excel」按钮拆为三个：「导入学生信息」「导入教师」「导入管理员」，
  分别调用 §4.1 三个接口；`wx.chooseMessageFile` 扩展名 `['xlsx','xls']`。
- Excel 格式说明更新为两套模板（学籍信息表 / 简版名单）。
- 导入结果卡新增「学生档案：新增 N · 更新 M」一行（仅学生导入出现）。

### 5.5 配置与页面注册

- `config/api.js` 新增：
  `METHODS_PHONE_WHITELIST_IMPORT_STUDENT / _TEACHER / _ADMIN`、
  `METHODS_STUDENT_ARCHIVE`、`METHODS_STUDENT_ARCHIVE_LINK`。
- `scripts/build.js` 的 `SHARED_PAGES` 新增 `pages/student-archive/student-archive`。

## 6. 权限与安全

| 项 | 处理 |
|----|------|
| 学生改档案 | 无写接口：`PUT /student-archive` 仅 admin/owner；`link` 只写自己的 `student_staff_id` |
| 管理员改档案 | `PUT /student-archive` 需 admin/owner；仅更新传入字段，学号不可改 |
| 学号绑定 | `link` 需登录；命中档案才写，避免脏数据 |
| 上传文件 | 仅 admin/owner；`multipart`、大小上限沿用 `upload_max_bytes`、行数上限 5000 |
| 手机号匹配 | 仅用 `phone_verified=1` 的手机号匹配，避免误关联 |

## 7. 部署

1. **新增 Python 依赖 `xlrd`**（解析 `.xls`；`.xlsx` 用已装的 `openpyxl`）。
   网关 venv 安装：
   ```bash
   cd agent4som-hermesagent/hermes-agent
   uv pip install --python ./venv/bin/python xlrd
   ```
   未安装时上传 `.xls` 会返回 400「请另存为 .xlsx 后重试」，不影响 `.xlsx`。
2. 重启网关进程（miniapp 适配器）使新路由生效。
3. 前端：`npm run switch`（重新生成 `app.json`）→ `npm test` → `npm run release`。

## 8. 测试与验证

- **后端**（本次已用真实样本冒烟）：`熊伟-2023级学籍信息.xls` 学生导入 →
  `student_archive=176`、白名单学生 `=159`、无手机号入档 `=17`；`dry_run` 预览、检索、管理员更新、
  教师导入（固定角色、坏行拒绝）、学号 `link` 全部通过。
- **前端**：`npm test`（validate.js）全部通过（含新页面模块加载链、SHARED_PAGES 齐全、api.js 派生）。
- **建议补充**：为 `_import_whitelist` 增补单测（学生/教师/管理员三种角色固定、无手机号入档、dry-run 不写库）。

## 9. 代码改动清单

**后端（`agent4som-hermesagent/plugins/miniapp-platform/adapter.py`）**
- 新增常量 `_STUDENT_ARCHIVE_FIELDS` / `_STUDENT_ARCHIVE_COLUMNS` / `_STUDENT_ARCHIVE_EDITABLE` / `_STUDENT_ARCHIVE_DISPLAY`
- 新增 `_ensure_student_archive_table`、`_parse_archive_sheet`、`_archive_row_to_dict`、`_lookup_archive_for_openid`
- `_method_admission_profile` 返回 `archive` / `archive_match`
- 新增 `_method_student_archive_list` / `_method_student_archive_update` / `_method_student_archive_link`
- 白名单导入重构为 `_import_whitelist` + 三个固定角色处理器（保留旧 `/import`）
- `connect()` 注册新路由；`_initialize_storage` 建 `student_archive` 表

**前端（`miniprogram-framework-frontend/`）**
- `pages/methods/methods.{wxml,js}`：删档案卡、加「学生档案管理」卡
- `pages/profile/profile.{js,wxml,wxss}`：重写为只读档案 + 手机号绑定 + 学号匹配
- `pages/student-archive/student-archive.{js,json,wxml,wxss}`：新增
- `pages/phone-whitelist/phone-whitelist.{js,wxml,wxss}`：三个导入入口 + 结果展示
- `config/api.js`、`scripts/build.js`、`scripts/validate.js`
