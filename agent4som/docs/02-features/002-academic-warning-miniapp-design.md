# 学业预警小程序模块设计

> 版本: 1.8 · 日期: 2026-08-31 · 状态: 方向重构——**选课合理性检查**（根据当前选课结果判断选课是否符合培养计划，识别"回避专业选修只攻必修"的钻漏洞行为）；v1.7 上传专业分区；v1.6 上传异步化
>
> **⚠ 2026-09-24 变更：去掉「有效期 / 新鲜度」概念。** 「已上传文件」不再显示有效期标签（原"新鲜（N 天内有效）/ 已过期"）；`/api/warning/status` 不再返回 `fresh` / `fresh_days`。下文 §4.3 示例 JSON 与 §5.2 行描述中的 `fresh` / `fresh_days` 已作废。
>
> 相关：`006-miniapp-profile-and-phone-whitelist-refactor-design.md`（「我的档案」字段重构、电话白名单分角色上传——与本模块同属小程序 methods 接口）

## 1. 背景与目标

管理学院教务管理员通过学业规划助手触发学业预警（已实现，见 001）。本期新增**小程序入口**：管理员在微信小程序中上传五类数据文件（培养方案/选课结果/成绩单/课表查询/学籍异动），触发学业预警计算，查看预警摘要与学生名单，并下载 Excel 报告。

**涉及两个仓库**：
- `agent4som/`：新增 HTTP API 服务（FastAPI，复用 `academicwarning` 包）
- `miniprogram-framework-frontend/`：新增"学业预警"模块（功能 tab 入口）

## 2. 已确认决策（2026-08-28 访谈）

| 项 | 决策 |
|----|------|
| 后端方案 | agent4som 侧加 HTTP 服务（FastAPI + uvicorn，**新增 python-multipart 一个依赖**——M2 评审核实：fastapi/uvicorn 已装但 python-multipart 缺失，FastAPI File() 上传必需），小程序直接调用 |
| 鉴权 | 固定 API Key（`.env` 配置 `WARNING_API_KEY`，小程序端内置携带），配合内网部署 |
| 小程序入口 | "功能"tab 页（methods）加"学业预警"入口，`FEATURES.warning` 开关 + `role === 'admin' \|\| role === 'owner'` 显隐（与 role_store `is_admin` 口径一致） |
| 结果展示 | 消息摘要 + 预警学生名单（滚动列表）+ Excel 报告下载（`wx.downloadFile`） |

## 3. 架构

```
小程序 (miniprogram-framework-frontend)
  pages/warning/warning   ← 新页面（上传/触发/结果）
      │  wx.uploadFile / wx.request（X-API-Key header）
      ▼
agent4som HTTP 服务 (uvicorn :8008)
  academicwarning/api.py  ← 新文件（FastAPI 应用）
      │  鉴权（X-API-Key）→ 调内部实现（_upload_impl/_trigger_impl，带并发锁）
      ▼
academicwarning 包（已有：service/db/parsers/rules/report）
  data/warning.db + data/warning_reports/
```

**复用**：上传/触发/计算/报告全部复用现有 `academicwarning` 包；HTTP 层只做鉴权、文件接收、**类型强校验**、参数透传、结果序列化。**最小侵入**：`service._upload_impl` 增加 `force_type: str | None = None` 参数（非空时跳过内部类型识别，按该类型解析入库；None 保持原三级识别逻辑，原有路径不受影响）；api.py 独立文件；调 service 的带锁内部实现（`_upload_impl`/`_trigger_impl`），跳过角色校验——API Key 即管理员凭证，角色校验由 HTTP 层承担。

## 4. HTTP API 规范

服务地址：`http://<agent4som-host>:8008`（生产经反代或直接内网访问，小程序域名白名单需加）。

**鉴权**：所有端点要求 `X-API-Key: <WARNING_API_KEY>` header；缺失/不匹配返回 401。

### 4.1 POST /api/warning/upload — 上传数据文件（单文件，类型 + 专业双强校验）

- 请求：`multipart/form-data`，字段 `file`（文件二进制）+ **`type_hint`（必填，五类之一）** + **`major_hint`（可选，v1.7：专业分区入口必传——培养方案/成绩单的 4 个专业子按钮各传对应标准专业名；选课/课表/学籍等单文件类型不传）**
- **类型校验（以模块为准 + 防传错，2026-08-28 用户确认）**：
  1. 服务端先用**文件内信息 + 文件名**识别实际类型（`detect_file_type(path, chat_hint="")`，不含 hint 级）
  2. 识别结果明确且 **≠ type_hint** → **拒绝**（200 + message："文件类型与所选模块不匹配：模块=选课结果，文件识别=成绩单，请确认是否传错"）
  3. 无法识别 → **信任模块类型**（type_hint 为准）。**实现要点（L1）：`detect_file_type` 无法识别时抛 `ValueError`，HTTP 层须 `try/except ValueError` 捕获后走信任分支**，否则 500
  4. 校验通过 → **以 type_hint 强制入库**（`service._upload_impl(..., force_type=type_hint)`）
- **专业校验（v1.7，防传错升级：类型+专业双校验）**：`major_hint` 非空且 `detect_major(path, force_type)`（plan：docx 正文标题正则"X专业培养方案" + 文件名兜底；grade：文件名关键词，与解析同源）**明确识别到且 ≠ major_hint** → **拒绝**（200 + message："文件识别=工商管理，与所选专业不匹配"）；**无法识别（canonical_major 未命中）→ 信任 major_hint，不拒绝**
- 处理（**v1.6 异步化**）：保存到 `data/warning_uploads/` → **M2 判重前置**（`service.dedup_check`）→ 未命中则**立即返回**，解析入库由后台 daemon 线程执行（`_OP_LOCK` 内；成功/失败后入库提示文本写回记录 `in_file_meta.note`）
- **入库 major 兜底（v1.7 修正）**：`_upload_impl` 增加 `major_hint` 参数——plan/grade 分支 major = `canonical_major(识别值) or major_hint or 识别值`（改名文件如"成绩单.docx"无专业词 → 归入所选专业行，状态区一致，不落"其他"区）
- 响应 200：`{"message": "<提示文本>", "file_type": "...", "major": "<标准专业名或空>", "parsed_status": "rejected|done|parsing", "upload_time": "..."}`
  - `rejected`：校验拒绝（未知类型/超 10MB/类型不匹配/专业不匹配），message 说明原因
  - `done`：判重命中（文件已在库，message 含"已上传过"/"已刷新时间戳"）
  - `parsing`：已接收，解析入库中——小程序端轮询 `/status` 直至该文件 `parsed_status` 为 done/failed（按 file_type+major+file_name 三元组匹配；OCR 单份实测 40-155s，轮询 3s 间隔 / 240s 上限）
- 解析失败：落库记录 `parsed_status=failed` + `in_file_meta.error`（/status 展示）；鉴权失败 401
- **多文件上传由小程序端串行循环调用本端点**（每个文件独立请求、独立状态机：上传进度 → 解析轮询 → 结果；HTTP 层保持单文件端点）

### 4.2 POST /api/warning/trigger — 触发计算

- 请求：`{"scope": "全部"}`（本期仅支持全部；专业/学号范围参数预留）
- 处理：`service._trigger_impl(scope="全部", uploader="api-admin", platform="api")`
- 响应 200：`{"message": "<消息摘要>", "report_file": "<报告文件名，不含路径>", "run_id": <id>}`（**L2：不暴露服务器绝对路径**，下载走 /report/latest）
- 数据不满足（缺文件/过期/学期不一致）→ 200 + message 为拒绝说明（与 CLI 行为一致）

### 4.3 GET /api/warning/status — 文件与数据状态（v1.7 分组结构）

- 响应（**v1.7 嵌套结构**）：
  ```json
  {"files": [
    {"file_type": "plan", "type_name": "培养方案",
     "majors": ["工商管理", "工业工程", "会计学（ACCA）", "大数据管理与应用"],
     "items": [
       {"major": "工商管理", "file_name": "...", "upload_time": "...",
        "parsed_status": "done", "note": "...", "error": null,
        "fresh": true, "fresh_days": 365, "id": 12},
       {"major": "工业工程", "file_name": null, ...},          // 未上传占位（major+nulls）
       ...
     ]},
    {"file_type": "selection", "type_name": "选课结果", "majors": [],
     "items": [{"major": null, "file_name": "...", ...}]}      // 单文件类型 1 行
  ]}
  ```
  - `majors`：标准专业名单（4 个，占位行渲染依据）；plan/grade 有，其余类型 `[]`
  - `items`：**标准专业占位行（每专业 1 行，无记录时 major+其余 null）+ 非标准 major 记录（历史脏数据如旧版"会计学"/文件名残片，原样展示，归前端"其他"区）**；单文件类型 1 行（major=null）
  - `note`：入库提示文本（解析线程写回）；`error`：解析失败原因
- 用途：小程序端展示"已上传哪些文件、是否过期、解析成功/失败及原因"；**缺专业汇总提示**（前端按"标准 majors 中无 parsed_status=done 记录"自算，与 freshness_check 口径一致）；解析阶段轮询复用本端点

### 4.4 POST /api/warning/delete — 删除上传文件（v1.7 按专业分区）

- 请求：`{"file_type": "plan|selection|grade|roster|status_change", "major": "<专业名，plan/grade 必传>"}`
- 语义（**与上传分区一一对应**）：plan/grade 删除**该专业全部记录（含历史版本，物理删除不可恢复）** + plan 级联删 `training_plan`（按 major_name 精确匹配，含 is_active=0 历史版）；其他类型忽略 major，删除该类型全部记录
- 处理：`_OP_LOCK` 内执行；无记录 → 200 + message "暂无XX文件可删除"
- 响应 200：`{"message": "已删除培养方案文件：<专业> <文件名>（上传于 …）"}`
- 用途：替换/清理传错文件——删除后该专业回"未上传"占位 + 顶部缺专业提示出现；重新上传同一文件（判重记录已删）可正常入库

### 4.5 GET /api/warning/results?run_id=<可选> — 预警学生列表

- `run_id` 可选，缺省返回**最新 run**（L3）；从未触发或无 run 时返回 `{"run_id": null, "summary": null, "students": [], "triggered_at": null}`（空数组，避免前端"最新"语义悬空）
- 响应：`{"run_id": N, "triggered_at": "...", "summary": "<summary_json.message 文本——M4：直接透传现库消息摘要文本，不新造结构化统计>", "students": [{student_id, name, major, class_name, level, rules: [1,2,3], data_incomplete: 0|1}]}`
- level 取该生最高预警级别；rules 为命中规则号列表（按学生去重）；**data_incomplete 标记（L5）：成绩数据缺失学生单独呈现（前端加"数据缺失"角标），与 005 §6.3 N1 口径一致**

### 4.6 GET /api/warning/report/latest — 下载 Excel 报告

- 响应：最新 run 的报告文件（`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`，`Content-Disposition: attachment`）
- 小程序端：`wx.downloadFile` → `wx.openDocument` 预览/转发，或 `wx.saveFile` 保存

### 4.7 GET /api/warning/selection-check — 选课检查名单（v1.8 方向重构）

- 响应：
  ```json
  {"source_file_id": 51, "semester_label": "2026-2027学年 第一学期",
   "checked_at": "2026-08-31 22:00:00",
   "summary": "选课检查：专业选修不足 11 人（工商管理 9 人、大数据管理与应用 2 人）",
   "students": [{"student_id": "...", "name": "...", "major": "...",
                 "class_name": "...", "expected_credit": 16.0,
                 "gained_credit": 10.0, "selected_credit": 0.0, "gap": 6.0,
                 "message": "专业选修不足：应累计 16.0 学分，已修 10.0 + 本学期已选 0.0，差 6.0 学分…"}]}
  ```
- 取数：最新选课文件（`MAX(id) FROM source_file WHERE file_type='selection' AND parsed_status='done'`）对应的检查结果；无选课文件 → `source_file_id=null、students=[]`
- 用途：小程序结果区名单 + 原因弹层

### 4.8 POST /api/warning/selection-check/run — 手动重跑选课检查（v1.8）

- 响应：`{"message": "<摘要/提示文本>", "source_file_id": 51, "checked_at": "...", "count": 11}`
- 未上传选课/成绩单未上传/缺方案 → message 为友好降级提示（不产生假阳性提醒）

## 5. 小程序模块设计

### 5.1 入口（methods 页）

- `config/instance.js` 的 `FEATURES` 增加 `"warning": true`（实例开关）
- `methods.wxml` 增加"学业预警"入口（`wx:if="{{features.warning && (role === 'admin' || role === 'owner')}}"`，与 §8 及 role_store 口径一致）→ `wx.navigateTo` 到 `pages/warning/warning`

### 5.2 新页面 pages/warning/warning（上传分区 + 状态分组 + 触发 + 结果）

1. **上传模块（v1.7 专业分区）**：
   - **培养方案 / 成绩单**：每类卡片内 **4 个专业子按钮**（工商管理/工业工程/会计学ACCA/大数据管理与应用，与状态区 4 行一一对应），每按钮 `wx.chooseMessageFile({count: 1, type: 'file', extension: ['docx']})` → 上传时携带 `type_hint` + `major_hint`（标准专业名）→ 后端专业强校验（§4.1，文件识别 ≠ 所选专业 → 拒绝）
   - **选课结果 / 课表查询 / 学籍异动**：保持单按钮（每类 1 份全校文件），`type_hint` 不带 major_hint
   - 多选来源：`wx.chooseMessageFile` 从聊天记录（含**微信传输助手**）选择文件
   - **上传策略（M1 评审修订 + v1.6 异步化）**：**串行上传**（每次 1 个），`wx.uploadFile` 显式 `timeout: 120000`（仅覆盖网络传输阶段）；**每文件状态机**：上传中（`onProgressUpdate` 进度条）→ 解析入库中（轮询 `/status`，3s 间隔 / 240s 上限，按 file_type+major+file_name 三元组匹配）→ 成功/失败（逐文件展示入库提示 note）；失败文件自动重试 1 次（仅网络类失败；校验拒绝不重试），仍失败标红并提示
   - 原因：服务端 `_OP_LOCK` 为串行锁 + 单份成绩单 OCR 实测 40-155s——v1.6 起解析移出请求（后台线程），同步等待不再必要
   - 统一错误处理（L8）：401（Key 失效）/网络错误/超时统一文案"请检查网络或联系管理员"；`wx.uploadFile` fail 回调 err.errMsg 透出（可区分 timeout / 域名未配置）
2. **文件状态区（v1.7 分组）**：`GET /api/warning/status` 渲染（§4.3 嵌套结构）：
   - **类型分组**：培养方案/成绩单 → 标准专业 4 行（`majors` 名单）+ **"其他"区**（非标准 major 历史记录，原样展示可删）；选课/课表/学籍 → 1 行
   - 每行：专业名 / 文件名 / 上传时间 / 解析状态（已解析·解析失败+原因）/ 新鲜度（fresh_days 窗口）/ **删除**（confirm 后按 file_type+major 调 §4.4）
   - **缺专业汇总提示**：顶部显示"XX缺少：专业名"（标准 majors 中无 done 记录的专业，前端自算）
   - 未上传专业显示"未上传"占位行（无删除键）
3. **选课检查区（v1.8 方向重构）**："运行选课检查"按钮（`POST /api/warning/selection-check/run`，手动重跑——上传选课后自动检查，重跑用于先传选课、后补传成绩单/方案的修正）→ 显示检查摘要
4. **结果区（v1.8）**：`GET /api/warning/selection-check` 渲染选课检查名单（学号/姓名/专业/应累计/已修/已选/差额 + "查看原因"弹层显示提醒文案）；空态"暂无提醒，当前选课符合培养计划"；无选课文件时引导先上传

### 5.3 API 配置

- `config/api.js` 增加：`WARNING_UPLOAD_URL / WARNING_TRIGGER_URL / WARNING_STATUS_URL / WARNING_RESULTS_URL / WARNING_REPORT_URL / WARNING_DELETE_URL`（指向 agent4som 服务）
- `config/instance.js` 增加：`WARNING_API_BASE`（服务地址）、`WARNING_API_KEY`（内置，见 §6 风险）
- `config/instances/jwc.js`（源配置）同步

### 5.4 页面文件

`pages/warning/warning.{js,json,wxml,wxss}` + `app.json` pages 注册（不进 tabBar）。

## 6. 安全与风险

| 风险 | 处理 |
|------|------|
| API Key 内置小程序端暴露 | 小程序代码可被反编译——**风险已确认接受**（用户选 A 方案）；缓解：①内网部署/生产域名限制 ②Key 仅具有上传/触发权限，无系统管理权限 ③后续可升级为服务端代理签名 |
| 上传文件恶意内容 | 复用 service 层解析兜底（M5 友好失败，不入库）；文件扩展名限制（docx/xlsx/xls）；**文件大小上限校验（L6）：>10MB 直接拒绝** |
| 上传落盘文件清理（L4） | `data/warning_uploads/` 解析入库后不再被读取，量级小（≤10 份/次），**暂不清理**（留档便于排查）；如后续磁盘增长再补定期清理 |
| **文件传错（模块与文件不匹配）** | 类型强校验：文件内信息+文件名识别 ≠ type_hint → 拒绝并提示（§4.1）；无法识别（含 ValueError）时信任模块类型 |
| **跨进程写库边界（M3 评审修订）** | `_OP_LOCK` 为**进程内锁**（threading.Lock）；部署形态下对话助手进程与 uvicorn 进程为独立进程，并发写库依赖 SQLite WAL——各写事务均为短事务（解析期间不持事务），冲突窗口极小且失败方向为**拒绝计算**（如 roster 两事务窗口读到空名单 → "基准名单含多届"拒绝），不会静默错算；db 连接显式 `timeout=30`；**uvicorn 部署注明单 worker**（多 worker 时锁失效，同此边界） |
| 并发触发 | 复用 `_OP_LOCK`（进程内，见上） |
| 域名白名单 | agent4som 服务域名需加入小程序 **request/uploadFile/downloadFile 合法域名**（L7：微信按 request/uploadFile/downloadFile/socket 四类独立配置，漏配 downloadFile 则"下载 Excel"静默失败）——部署侧操作 |

## 7. 部署

1. `agent4som/.env` 加 `WARNING_API_KEY=<随机长密钥>`（gitignored；`.env` 为 shell 格式 `KEY=VAL`，systemd EnvironmentFile 直接支持）
2. **requirements.txt 增补 `fastapi`、`uvicorn`、`python-multipart`**（M2：python-multipart 为 FastAPI File() 上传必需，当前 venv 缺失；fastapi/uvicorn 已装但未锁定，生产重装需可复现）：`source venv/bin/activate && pip install -r requirements.txt`
3. **启动（开发/临时）**：
   ```bash
   source venv/bin/activate
   set -a; source .env; set +a   # 加载 WARNING_API_KEY 等环境变量
   nohup uvicorn academicwarning.api:app --host 0.0.0.0 --port 8008 --workers 1 \
     > data/logs/warning-api.log 2>&1 &
   ```
   **单 worker（M3：多 worker 时进程内锁失效）**；反代 `proxy_read_timeout ≥ 180s`（M1：成绩单上传 OCR 最长 ~155s）
4. **生产（systemd，仿现有 watchdog 模式）**——`/etc/systemd/system/academic-warning-api.service`：
   ```ini
   [Unit]
   Description=Academic Warning HTTP API (miniapp entry)
   After=network.target

   [Service]
   Type=simple
   User=<DEPLOY_USER>
   WorkingDirectory=/home/<DEPLOY_USER>/H-agent/agent4som
   EnvironmentFile=/etc/academic-warning-api.env   # SELinux 要求：home 下 .env 被拒读（§6 踩坑 1）
   ExecStart=/home/<DEPLOY_USER>/H-agent/agent4som/venv/bin/uvicorn academicwarning.api:app \
       --host 0.0.0.0 --port 8008 --workers 1
   Restart=on-failure
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now academic-warning-api
   sudo systemctl status academic-warning-api
   ```
   > 注：`--host 0.0.0.0` 直接暴露内网（firewalld 需放行 8008/tcp，§6 踩坑 2）；如走反代可改 `127.0.0.1`。反向代理需配置：
   > ```nginx
   > location /api/warning/ {
   >     proxy_pass http://127.0.0.1:8008;
   >     proxy_read_timeout 180s;   # v1.6 异步化后请求不再等待 OCR（≤10MB 传输绰绰有余）
   >     client_max_body_size 20m;  # 单文件 ≤10MB（L6），留余量
   > }
   > ```
5. 小程序：`config/instances/jwc.js` 配 `WARNING_API_BASE`（如 `https://<CAMPUS_PORTAL>/accapi/warning-api/` 或独立域名）与 `WARNING_API_KEY` → `npm run switch` → `npm run release` 上传
6. 域名白名单配置（微信公众平台：request/uploadFile/downloadFile 三类，L7）

### v1.6 升级部署（上传异步化，2026-08-30）

代码在 `/home/<DEPLOY_USER>/H-agent/agent4som`（WorkingDirectory 即仓库），无新增 pip 依赖（后台线程用标准库 `threading`）：

```bash
cd /home/<DEPLOY_USER>/H-agent/agent4som && git pull        # 拉取 v1.6 代码（分支 Academic-Assistant）
sudo systemctl restart academic-warning-api
journalctl -u academic-warning-api -n 20             # 确认启动无报错
```

冒烟验证（本机，KEY 从 `/etc/academic-warning-api.env` 取；上传文件后等待数秒再查 status）：

```bash
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8008/api/warning/status
#   → 每行含 id/parsed_status/note/error 字段
curl -s -X POST -H "X-API-Key: $KEY" -F "file=@<方案文件>.docx" -F "type_hint=培养方案" \
     http://127.0.0.1:8008/api/warning/upload
#   → parsed_status=parsing（立即返回，不等待解析）
curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"file_type":"plan"}' http://127.0.0.1:8008/api/warning/delete
#   → "已删除培养方案文件：…"（P2 删除端点，验证后清理冒烟数据）
```

> 过渡说明：旧版小程序代码对 `parsing` 响应走"未知响应→重试"路径，**后端与小程序建议同时发布**。

## 8. 代码改动清单（评审 M4 修订）

**agent4som 侧**：
- 新文件 `academicwarning/api.py`：FastAPI 应用（8 端点 + API Key 鉴权；**v1.6 上传异步化**；**v1.7 类型+专业双强校验**；**v1.8 选课检查两端点** `GET /selection-check` + `POST /selection-check/run`）
- **新文件 `academicwarning/selection_check.py`（v1.8 方向重构）**：选课合理性检查——应累计（方案专业选修 ≤ 当前学期，剔除全员无成绩课程）− 已修（成绩单有记录即算，含挂科；方案名匹配 + ◆标记归并）− 已选（本轮选课专业选修）≥ 2 学分 → 提醒；复用 Prep 零改动 rules.py
- 修改 `academicwarning/service.py`：**抽取 `_assemble_check_prep`**（触发与选课检查共用装配）；新增 `run_selection_check`；`_upload_impl` selection 分支自动检查（摘要进回复 note）
- 修改 `academicwarning/db.py`：**新增 `selection_check` 表** + `insert_selection_check_rows`/`latest_selection_check`
- 修改 `academicwarning/models.py`：**新增 `SelectionCheckRow`**
- 新测试 `tests/academicwarning/test_selection_check.py`：口径单测（差额边界/学期/豁免/已修含挂科/◆归并/跨选排除/非在籍）+ 服务层/API（自动检查、空态、重跑）
- 旧六规则/`/results`/`/report/latest`/`/trigger` 保留在库（决策 2：代码保留，界面换新）

**结果聚合口径（与 005 §6.3 N1 一致）**：level 取该生最高级别；rules 按学生去重；`data_incomplete=1` 学生**不计入汇总统计、单列**（小程序端加"数据缺失"角标）；`summary` 直接透传 `warning_run.summary_json.message` 文本。

**小程序侧**：新页面 `pages/warning/warning.{js,json,wxml,wxss}` + app.json 注册 + methods 页入口（`FEATURES.warning` + admin/owner 角色）+ config/api.js 与 instance.js 配置。

## 9. 测试与验证

1. **API 单测**（agent4som/tests/academicwarning/test_api.py）：鉴权 401、上传（真实样本）、类型强校验（传错拒绝）、触发、结果、报告下载，用 TestClient
   - **v1.7 专业分区用例**：①专业强校验（工商方案传会计学ACCA入口 → rejected + "专业不匹配"）②无法识别专业信任 hint（改名文件按所选专业入库）③status 嵌套结构（majors 名单/标准 4 行占位/非标准记录入 items）④delete 按专业（删 ACCA 保留工商 + plan 级联）⑤缺专业提示口径（无 done 记录 → missing）
   - **N1 口径回归断言（2026-08-28 用户建议）**：构造含 `data_incomplete=1` 学生的 run → `GET /results` 断言 ①该生出现在 students 列表且 `data_incomplete: 1`（前端角标依据）②summary 文本不含该生统计（N1：数据缺失学生不计入汇总；若 summary 未来演进为结构化统计，同样断言其不计入红黄蓝/规则命中数）——锁住 `get_results_by_run` 聚合与 005 §6.3 N1 口径一致
2. **小程序端**：`npm test`（validate.js）+ 真机预览手工验证（管理员角色登录 → 入口可见 → 上传 → 触发 → 查看名单 → 下载 Excel）
3. **端到端**：小程序上传真实样本五类文件 → 触发 → 结果与 CLI 触发一致（run 数字可对比）

## 10. 未决项（可评审时调整）

1. ✅ 已确认（2026-08-28）：**五个独立上传模块 + 一次多选**（`wx.chooseMessageFile` count 多选，从微信传输助手选文件，逐文件上传并显示结果）
2. 触发范围：本期仅"全部"，专业/学号范围预留
3. 预警名单分页：129 人一次性返回可接受；数据量大时加分页参数
4. 报告下载保存方式：`wx.openDocument`（预览+转发）vs `wx.saveFile`（保存到本地）——默认 openDocument，用户可转发/保存
