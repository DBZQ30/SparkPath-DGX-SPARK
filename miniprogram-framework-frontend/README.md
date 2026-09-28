# miniprogram-framework-frontend —— 微信小程序前端

> 「本科新生学业规划智能助手」微信小程序前端（AppID `<MINIAPP_APPID>`），
> 面向本科生、研究生、教师与教务管理员，提供对话问答、选课预警、培养方案解读、
> 学业规划、文件中心与教务通知同步等能力。

前端采用**原生小程序**开发，通过「**单套共享代码 + 实例配置 + 构建脚本生成**」组织：
`config/instances/jwc.js` 是唯一事实来源，`npm run switch` 生成构建产物。

---

## 目录

1. [页面清单](#1-页面清单)
2. [目录结构](#2-目录结构)
3. [配置与构建产物机制](#3-配置与构建产物机制)
4. [后端对接](#4-后端对接)
5. [本地开发](#5-本地开发)
6. [测试与校验](#6-测试与校验)
7. [发布与 CI](#7-发布与-ci)
8. [文档索引](#8-文档索引)
9. [注意事项](#9-注意事项)

---

## 1. 页面清单

小程序共 **17 个页面**，底部 tabBar 三项：**对话 / 服务 / 我的**。

| # | 页面 | 导航标题 | 功能 |
|---|------|----------|------|
| 1 | `pages/chatbot/chatbot` | 对话 | **tabBar**：微信登录、AI 对话、历史记录、等待期进度、回复内「执行过程」下钻到全屏轨迹 |
| 2 | `pages/methods/methods` | 服务 | **tabBar**：功能中心，按角色与功能开关生成入口；知识文件上传；申请认证 |
| 3 | `pages/user/user` | 我的 | **tabBar**：登录、身份角色、用户名、关于 |
| 4 | `pages/list/list` | 认证与身份 | 教师 / 管理员认证审批，已认证用户角色变更 |
| 5 | `pages/profile/profile` | 我的档案 | 学籍档案只读展示、手机号绑定、学号兜底匹配 |
| 6 | `pages/phone-whitelist/phone-whitelist` | 电话白名单 | 白名单增删、按角色分批导入、批次删除 |
| 7 | `pages/student-archive/student-archive` | 学生档案管理 | 学籍信息检索与编辑（管理员） |
| 8 | `pages/knowledge/knowledge` | 知识库管理 | 按角色范围列文件、删除 / 批量删除、孤儿扫描、操作历史 |
| 9 | `pages/knowledge-preview/knowledge-preview` | 原文预览 | 从知识片段按序拼回原文，只读 |
| 10 | `pages/warning/warning` | 选课预警 | 管理员上传五类文件 → 异步解析轮询 → 选课检查名单 → 导出 / 豁免 / 成绩确认 |
| 11 | `pages/warning-detail/warning-detail` | 选课预警详情 | 单生全量：触发摘要、缺修逐门、未及格、类别差额、已修分组、豁免记录 |
| 12 | `pages/plan/plan` | 培养方案解读 | 学生只读：学分结构 / 学期地图 / 先修关系 / 毕业授学位条件 |
| 13 | `pages/plan-route/plan-route` | 学业规划 | 四方向四年路线图、专业选择、转专业模拟与对比 |
| 14 | `pages/plan-manage/plan-manage` | 培养方案管理 | 管理员上传、批量解析队列进度、先修关系原图校对 |
| 15 | `pages/doc-center/doc-center` | 文件中心 | 管理员：公共教学文件统一上传 + 适用性矩阵 + 解析状态 |
| 16 | `pages/jxtz-sync/jxtz-sync` | 教务通知同步 | 开关、每日时间、立即同步、最近运行 |
| 17 | `pages/trace/trace` | 执行轨迹 | 观测台：**有工具调用**时展示后端真实轨迹，**无工具调用**时回放内置样例（页面标注 `demo`），支持 `?live=1` 实时轮询 |

> `pages/plan-files`（入库文件与适用年级）已合并进「文件中心」并下线（见 `agent4som/docs/02-features/007-document-center-design.md`）。

角色模型：`guest`（访客）/ `student`（学生）/ `teacher`（教师）/ `admin`（管理员）/ `owner`（所有者）。
页面入口由角色与功能开关（`FEATURES`：`badge` / `warning` / `plan` / `planRoute` / `docCenter` / `jxtzSync`）共同决定。

---

## 2. 目录结构

```
miniprogram-framework-frontend/
├── app.js                       # 入口：注册 App，拉未读角标
├── app.wxss                     # 全局设计令牌（颜色 / 字号 / 间距）+ ui-* 组件 + 内联 SVG 图标
├── app.json                     # 【构建产物·gitignored】页面列表 + tabBar + window
├── project.config.json          # 【构建产物·gitignored】开发者工具配置（appid 由此写入）
├── sitemap.json                 # 微信 sitemap
├── package.json                 # 无运行时依赖；devDependency: miniprogram-ci
│
├── config/
│   ├── instances/
│   │   ├── jwc.js               # ★ 唯一事实来源：线上教务实例配置
│   │   └── _default.js          # 开发默认实例（= jwc）
│   ├── instance.js              # 【构建产物·gitignored】运行时配置
│   └── api.js                   # 全部后端接口 URL（由三个 BASE 派生）
│
├── pages/                       # 17 个页面（见上表）
├── utils/
│   ├── instance-keys.js         # 本地存储键按 INSTANCE_ID 派生（多实例缓存隔离）
│   ├── trace.js                 # 执行轨迹事件模型与格式化
│   └── trace-demo.js            # 比赛演示用录播轨迹
│
├── scripts/
│   ├── build.js                 # 多实例组装引擎（生成 4 类产物）
│   ├── switch-instance.js       # 开发切换 CLI（npm run switch）
│   ├── release.js               # 发布 CLI（miniprogram-ci 上传 + CHANGELOG + tag）
│   ├── new-instance.js          # 新实例骨架生成器（`node scripts/new-instance.js <id>`，无 npm 别名）
│   ├── ci-restore-keys.js       # CI 内从变量恢复上传密钥
│   ├── validate.js              # 架构核验（npm test 之一）
│   └── validate-ui.js           # 界面 / 样式核验（npm test 之二）
│
├── docs/                        # 3 份开发文档（见 §8）
├── images/                      # tabBar 图标（页面图标走内联 SVG）
├── assets/instances/            # 实例差异化静态资源镜像目录
└── .gitlab-ci.yml               # GitLab CI：架构核验 + 手动发布
```

---

## 3. 配置与构建产物机制

这是理解本仓库的**第一关键**：**构建产物不入库**。

`config/instances/jwc.js` 是唯一配置来源；`scripts/build.js` 根据它生成/改写四类产物：

1. `config/instance.js` —— 运行时配置（页面与接口统一读取）；
2. `project.config.json` —— 仅改写 `appid`；
3. `app.json` —— 共享页面列表（17 个）+ 实例专属页面（`FEATURES.customPages`）+ 标题 + tabBar；
4. `assets/instances/<id>/**` —— 按相对路径镜像复制实例差异化静态资源。

因此**克隆后必须先生成产物**：

```bash
npm run switch
```

> ⚠️ `app.json`、`project.config.json`、`config/instance.js` 均在 `.gitignore` 中。
> 不执行 `npm run switch` 会因 `require` 失败或页面空白而无法编译。
>
> ⚠️ 修改 tabBar 或新增共享页面，必须改 `scripts/build.js`（`SHARED_PAGES` / `DEFAULT_TAB_BAR`），
> 再执行 `npm run switch`；直接改 `app.json` 会被构建覆盖。

---

## 4. 后端对接

### 4.1 三个 BASE

配置在 `config/instances/jwc.js`，经构建透传到 `config/instance.js`，再由 `config/api.js` 派生全部接口 URL：

| 配置字段 | 对应服务 |
|----------|----------|
| `API_BASE_URL` | Hermes 网关（对话 / 登录 / methods 系列） |
| `WARNING_API_BASE` | 学业预警服务 |
| `PLAN_API_BASE` | 培养方案 / 规划 / 文件中心 / GPU 指标服务 |

> 三个 BASE 均通过校园网关的 `/accapi/` 灰度路由，分别转发到 DGX Spark 上的
> miniapp-proxy（→ Hermes 网关）、academic-warning-api、training-plan-api。

### 4.2 两种鉴权

| 接口 | 鉴权方式 |
|------|----------|
| 网关接口（`API_BASE_URL`） | `Authorization: Bearer <session_token>` |
| 预警 / 培养方案接口 | `X-API-Key: <WARNING_API_KEY / PLAN_API_KEY>` |

> Session Token 由**后端**用 `MINIAPP_SESSION_SECRET` 做 HMAC-SHA256 签发，
> 前端只负责存储与附带，**不参与计算**。

### 4.3 微信登录流程

```
小程序 wx.login() → code
  → POST {API_BASE_URL}/api/miniapp/login  { code, instance_id }
  → 后端调微信 jscode2session 换 openid
  → 返回 { openid, user_id, session_id, session_token }
  → 本地存储 session_token
  → 后续请求带 Authorization: Bearer <token>
```

登录失败时降级为临时身份，保证基础功能可用。

### 4.4 接口清单（部分）

- **对话**：`/api/chat`、`/api/miniapp/history`、`/api/miniapp/run-progress`、`/api/miniapp/run-trace`；
- **上传 / 知识库**：`/api/uploads`、`/api/methods/knowledge*`；
- **身份与认证**：`/api/methods/identity|profile|admission-profile|apply-auth|change-role`；
  认证审批 `/api/methods/{teacher,admin}-auths`、白名单 `/api/methods/phone-whitelist*`；
- **学业预警**：`/api/warning/{upload,status,selection-check,selection-check/student,grade,waiver,export}`；
- **培养方案 / 规划**：`/api/plan/{overview,semester-map,prereq,upload,route,select/simulate,simulate/transfer,...}`；
- **文件中心**：`/api/doc-center/{files,upload,status,file,retry}`；
- **教务通知同步**：`/api/methods/jxtz-sync`。

完整 URL 定义见 `config/api.js`。

---

## 5. 本地开发

```bash
npm install          # 仅发布需要 miniprogram-ci；switch 不需要
npm run switch       # 生成构建产物（必做）
```

然后用**微信开发者工具**导入本目录 → 点击「编译」即可预览。

- 若请求失败，在「详情 → 本地设置」勾选「不校验合法域名」（本地调试用）；
- 切换实例后若提示项目配置变更，点确定重载；
- 基础库版本：`3.16.2`。

常用命令：

```bash
npm run switch       # 生成构建产物
npm test             # 架构核验 + 界面核验 + jest 单元测试
npm run release -- jwc --version 1.1.0 --desc "说明"
```

---

## 6. 测试与校验

`npm test` 运行两个核验脚本 + jest 单元测试：

| 工具 | 核验内容 |
|------|----------|
| `scripts/validate.js` | 实例清单、接口 URL 是否均由对应 BASE 派生、共享代码零硬编码、构建产物已 gitignore、页面文件齐全、存储键按实例隔离 |
| `scripts/validate-ui.js` | WXSS 括号配平、WXML 事件与数据绑定在 JS 中存在、图标定义完整、每页有导航标题、禁用旧色值与 `box-shadow`、禁止 emoji |
| jest（`tests/unit/`） | 共享逻辑单元测试：`utils/trace.js`（格式化/摘要/分拍）、`utils/instance-keys.js`（实例键派生）、`utils/trace-demo.js`（录播数据契约）、`config/api.js`（URL 派生）、`scripts/build.js`（实例组装合并） |

```bash
npm run switch && npm test      # 三者全跑（validate 需要 switch 生成的构建产物）
npm run test:unit               # 只跑 jest（纯逻辑，无需构建产物）
```

jest 工作机制（配置在 `package.json` 的 `jest` 字段）：

- `tests/setup.js` 注入 `wx` 全局桩（内存存储 / 请求记录），纯 Node 环境无需小程序运行时；
- `config/instance.js` 是构建产物（gitignored），测试经 `moduleNameMapper` 映射到
  确定性夹具 `tests/fixtures/instance.js`（尾斜杠、空 PLAN_BASE 等分支都由夹具钉死），
  不会动开发者当前 switch 的实例；
- `transform: {}` 关闭 babel 转换——被测代码全是 CommonJS，且 node_modules 里
  miniprogram-ci 的旧版 @babel/core 会与 jest 内置插件版本冲突；
- 新增用例放 `tests/unit/*.test.js`，保持纯逻辑（需要 `wx.request` 的交互测试
  待引入更完整的小程序 mock 后再加）。

---

## 7. 发布与 CI

### 发布

```bash
npm run release -- jwc --version 1.1.0 --desc "本次更新说明" --robot 1
```

- 需要上传密钥 `keys/<AppID>.key`（`keys/` 已 gitignore）；
- 发布成功后自动追加 `CHANGELOG.md` 并打本地 tag。

### CI（`.gitlab-ci.yml`）

- **`architecture-check`**（MR / 默认分支）：`npm run switch && npm test`；
- **`release:jwc`**（默认分支，手动触发）：`npm ci` → 恢复上传密钥 → 执行发布。

路径所有权见 `.gitlab/CODEOWNERS`。

---

## 8. 文档索引

| 文档 | 内容 |
|------|------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 开发者全景手册：架构、实例配置、构建引擎、后端协议 |
| [`docs/UI-DESIGN-SYSTEM.md`](docs/UI-DESIGN-SYSTEM.md) | 视觉系统「学籍台账」：设计令牌、组件、图标 |
| [`docs/HANDOFF_MINIAPP.md`](docs/HANDOFF_MINIAPP.md) | 交接文档：系统地图、端口拓扑、轨迹数据契约、踩坑记录 |

---

## 9. 注意事项

- **构建产物不入库**，克隆后先 `npm run switch`；
- **多实例机制仍保留**，但当前只启用 `jwc` 一个实例；
- **密钥不入库**：`WARNING_API_KEY` / `PLAN_API_KEY` 从 gitignored 的
  `config/instances/jwc.local.js` 读取（模板见 `jwc.local.js.example`），未提供对应功能入口自动隐藏；
  注意该 Key 会随小程序包分发到客户端，应仅作限流用途并定期轮换；
- **「执行轨迹」数据来源（明确约定）**：
  - `?live=1` → **实时**（轮询后端当前 run 的事件缓冲，始终为真实执行）；
  - **有工具调用**的回答 → 后端在回复体带 `trace`，展示**真实轨迹**（思考 / 命中 Skill / 工具命令与耗时）；
  - **无工具调用**的回答 → 后端不下发 `trace`，前端回放**内置样例** `utils/trace-demo.js`，并在页面明确标注
    「演示回放 · 除结论外均为内置样例（真实结论取自本次回答）」。
  详见 `docs/HANDOFF_MINIAPP.md` §3.3 与 `docs/UI-DESIGN-SYSTEM.md` §9.6。

---

## 许可证

本项目基于 MIT License 开源。
