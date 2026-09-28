# 小程序架构说明文档

> **当前范围：本仓库只维护 1 个微信小程序 —— 本科新生学业规划智能助手（jwc）。**
> 多实例机制（`config/instances/<id>.js` + 构建脚本）作为**可扩展的架构能力**保留，
> 如需再开小程序，按第 11 节新增实例即可。
>
> 本文档是**开发者全景手册**：新成员照第 6 节上手，日常开发看第 6.2 节全景表，疑问查第 10 节 FAQ。
> 界面与样式规范见 [`UI-DESIGN-SYSTEM.md`](UI-DESIGN-SYSTEM.md)；系统地图与踩坑见
> [`HANDOFF_MINIAPP.md`](HANDOFF_MINIAPP.md)。

---

## 1. 背景与目标

| 问题 | 说明 |
|---|---|
| 现状 | 小程序代码按「实例配置 + 构建脚本」组装；当前只有本科新生学业规划智能助手一个实例 |
| 传统做法 | 每个小程序复制一份代码各自维护 → 改一个 bug 要改 N 处，必然漂移 |
| 本方案 | **单仓库单套代码**，每个助手一份实例配置，构建时按配置组装出该助手专属的代码包 |

**核心原则：**
1. **配置即唯一事实来源** —— `config/instances/<id>.js` 定义了一个助手的一切
2. **生成物不手工改** —— `config/instance.js`、`app.json`、`project.config.json` 由构建脚本生成
3. **共享代码零侵入** —— 页面、api.js、登录逻辑不感知"多实例"的存在，只读配置

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    config/instances/                          │
│  1 个实例配置文件（jwc.js，另有 _default.js 指向它）            │
└──────────────────────────┬──────────────────────────────────┘
                           │ node scripts/switch-instance.js <id>
                           │ node scripts/release.js <id> / --all
                           │ GitLab CI（.gitlab-ci.yml，MR/推送触发核验）
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    scripts/build.js（组装引擎）                 │
│  ① 生成 config/instance.js    ← 运行时配置（页面只读这个）        │
│  ② 改写 project.config.json   ← appid                          │
│  ③ 生成 app.json             ← 页面/标题/tabBar/专属页           │
│  ④ 镜像复制 assets/instances/<id>/** → 项目根（差异化静态资源）    │
│  安全写入：内容未变不落盘 + 临时文件原子替换                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐
              ▼                ▼  ▼                ▼  ▼                  ▼
   微信开发者工具（开发）      miniprogram-ci（发布）  GitLab CI（核验）
   打开本仓库目录             上传到对应 AppID 的小程序  npm test：架构核验
   AppID 已按实例切换         版本号/描述/批量            + UI 规约 + jest
```

**运行时的数据流（小程序内）：**

```
config/instance.js（构建产物）
   ├── INSTANCE_ID ──→ 登录请求 body.instance_id ──→ 后端区分用户体系
   ├── ASSISTANT_TITLE ──→ app.json 全局标题 / 对话页导航标题
   ├── WELCOME_TEXT ──→ 对话页首条欢迎消息
   ├── CHAT_PLACEHOLDER ──→ 对话页输入框占位符
   ├── API_BASE_URL / WARNING_API_BASE / PLAN_API_BASE ──→ config/api.js 派生全部 66 个接口地址
   ├── WARNING_API_KEY / PLAN_API_KEY ──→ 预警 / 培养方案服务的 X-API-Key（来自本地密钥文件）
   └── FEATURES ──→ 功能开关（构建期决定 app.json，运行期控制行为）
```

---

## 3. 目录结构

```
miniprogram-framework-frontend/
├── app.js / app.wxss            # 小程序入口（业务代码零侵入）
├── app.json                     # 【生成产物·不入库】由 build.js 按实例生成（gitignore）
├── project.config.json          # 【生成·不入库】appid 由 build.js 改写，缺失时按模板生成（gitignore）
├── project.private.config.json  # 开发者工具私有状态（gitignore，不入库）
├── config/
│   ├── instances/               # ★ 实例配置目录（唯一事实来源，全部入库）
│   │   ├── jwc.js               #   本科新生学业规划智能助手（当前唯一实例，真实 AppID）
│   │   ├── jwc.local.js.example #   本地密钥模板（复制为 jwc.local.js 填入 KEY，后者 gitignore）
│   │   └── _default.js          #   开发默认实例（= 教务 jwc）
│   ├── instance.js              # 【生成产物】当前生效实例配置（gitignore）
│   └── api.js                   # 66 个接口地址，从 instance.js 的三个 BASE 派生
├── utils/
│   ├── instance-keys.js         # 实例命名的本地存储键（按 INSTANCE_ID 派生）
│   ├── trace.js                 # 执行轨迹：事件模型 / 摘要 / 格式化
│   └── trace-demo.js            # 内置录播轨迹（无真实 trace 时回放，页面标注 demo）
├── assets/instances/            # 【可选】差异化静态资源
│   └── <id>/                    #   按相对路径镜像到项目根，如 <id>/images/tab-*.png
├── pages/                       # 17 个共享页面（对话/服务/我的 三个 tab + 各功能页）
│   └── _ext/                    # 【约定】各实例专属页面目录，如 _ext/jwc/xxx
├── scripts/
│   ├── build.js                 # 组装引擎（switch/release 共用）
│   ├── switch-instance.js       # 开发切换 CLI
│   ├── release.js               # 发布 CLI（成功后自动打 tag + 追加 CHANGELOG）
│   ├── ci-restore-keys.js       # CI 内从 CI 变量恢复上传密钥（KEY_<AppID>，不入库）
│   ├── new-instance.js          # 新实例骨架生成 CLI
│   ├── validate.js              # 架构核验脚本（npm test 之一）
│   └── validate-ui.js           # 界面/样式核验脚本（npm test 之二）
├── tests/unit/                  # jest 单元测试（trace / instance-keys / api / trace-demo / build）
├── keys/                        # 【不入库】上传密钥 keys/<AppID>.key（CI 发布用变量 KEY_<AppID> 替代）
├── docs/                        # 3 份开发文档（ARCHITECTURE / UI-DESIGN-SYSTEM / HANDOFF_MINIAPP）
├── CHANGELOG.md                 # 发布记录（release.js 成功发布后自动追加，幂等）
├── .gitlab-ci.yml               # GitLab CI 流水线（MR/推送自动核验 + 手动发布 job）
├── .gitlab/CODEOWNERS           # 路径级所有权：实例专属归个人，共享区归全员
└── .gitattributes               # 统一 LF 行尾
```

---

## 4. 实例配置文件规范

### 4.1 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `APPID` | string | ✅ | 该小程序在微信公众平台的 AppID，切换/发布时写入 project.config.json |
| `INSTANCE_ID` | string | ✅ | 实例唯一标识（后端协议用），随登录请求发送，如 `jwc-assistant` |
| `ASSISTANT_TITLE` | string | ✅ | 助手名称，用于全局导航标题与对话页标题 |
| `WELCOME_TEXT` | string | ✅ | 对话页首条欢迎消息（助手人设） |
| `CHAT_PLACEHOLDER` | string | ✅ | 对话页输入框占位符 |
| `API_BASE_URL` | string | ✅ | 网关（对话/登录/methods）API 基址，api.js 大部分接口由此派生 |
| `WARNING_API_BASE` | string | 否 | 学业预警服务基址（独立 `X-API-Key` 鉴权；空值 = 功能未启用） |
| `PLAN_API_BASE` | string | 否 | 培养方案/规划/文件中心服务基址（空值 = 功能未启用） |
| `FEATURES.*` | 见下 | 否 | 功能开关（构建期 + 运行期） |
| `UI_TEXT` | object | 否 | 界面文案覆盖（角色名、认证措辞等），与默认文案合并（实例字段优先） |
| `TAB_BAR` | object | 否 | 整体覆盖默认 tabBar（文案/图标），不填用默认三 tab |

> ⚠️ `WARNING_API_KEY` / `PLAN_API_KEY` **不写进实例配置**，而是从同目录 gitignored 的
> `<id>.local.js` 读取（模板 `<id>.local.js.example`）；缺失时构建告警、对应功能入口隐藏。
> 这些 Key 会随小程序包分发到客户端，应仅作限流用途并定期轮换。

`FEATURES` 可用键（默认值见 `scripts/build.js` 的 `DEFAULT_FEATURES`）：

| 键 | 默认 | 生效 | 作用 |
|---|---|---|---|
| `badge` | true | 运行期 | 未读角标（app.js 按此决定是否请求/显示） |
| `customPages` | [] | 构建期 | 本实例专属页面路径，追加进 app.json pages（拼写错误构建时报错） |
| `phoneAuthMode` | `'manual'` | 运行期 | 手机号绑定方式：`manual`（手输）/ `wechat`（getPhoneNumber 授权） |
| `warning` | 由实例定 | 运行期 | 学业预警模块入口（需 `WARNING_API_BASE`） |
| `plan` | 由实例定 | 运行期 | 培养方案解读（需 `PLAN_API_BASE`） |
| `planRoute` | 由实例定 | 运行期 | 多路径学业规划入口 |
| `docCenter` | 由实例定 | 运行期 | 文件中心入口（管理员） |
| `jxtzSync` | false | 运行期 | 教务通知同步管理入口（仅学业规划助手） |

`UI_TEXT` 可用键（默认值见 `scripts/build.js` 的 `DEFAULT_UI_TEXT`，当前默认即教务"教师"语境）：

| 键 | 默认值 | 作用位置 |
|---|---|---|
| `ROLE_TEACHER` | 教师 | 角色标签（methods/user/list 页）、变更身份弹窗 |
| `APPLY_TEACHER_DESC` | 申请成为教师 | 功能中心"申请认证"卡片描述 |
| `PENDING_TEACHER_AUTH` | 待审核的教师申请列表 | 管理员"查看认证申请"卡片描述 |
| `AUTH_TEACHER_LABEL` | 教师 | 认证弹窗标题"申请{{}}认证"中的角色名 |
| `ALREADY_TEACHER` | 已是教师 | 已是教师角色时的 toast |

### 4.2 完整示例（jwc.js 节选）

```js
// config/instances/jwc.js
module.exports = {
  APPID: '<MINIAPP_APPID>',
  INSTANCE_ID: 'jwc-assistant',
  ASSISTANT_TITLE: '本科新生学业规划智能助手',
  WELCOME_TEXT: '你好，我是西安交通大学管理学院本科新生学业规划智能助手，……',
  CHAT_PLACEHOLDER: '输入您的问题',
  API_BASE_URL: 'https://<CAMPUS_PORTAL>/accapi/dgx-agentapi/',
  WARNING_API_BASE: 'https://<CAMPUS_PORTAL>/accapi/dgx-warning',
  WARNING_API_KEY: SECRETS.WARNING_API_KEY || '',   // 来自 gitignored 的 jwc.local.js
  PLAN_API_BASE: 'https://<CAMPUS_PORTAL>/accapi/dgx-plan',
  PLAN_API_KEY: SECRETS.PLAN_API_KEY || '',
  FEATURES: {
    badge: true, warning: true, plan: true, planRoute: true,
    docCenter: true, jxtzSync: true, customPages: [], phoneAuthMode: 'manual',
  },
  UI_TEXT: { ROLE_TEACHER: '教师', /* … */ },
}
```

### 4.3 功能开关与界面文案如何生效

- **构建期**：`FEATURES.customPages` 决定 app.json 的 pages 列表（专属页只在对应实例的包里）
- **运行期**：页面从 `config/instance.js` 读取 `FEATURES` / `UI_TEXT`，用 `wx:if` 控制模块显隐、绑定文案
- 页面统一通过 `require('../../config/instance')` 读取配置，**实例差异全部走配置，共享代码零硬编码**

### 4.4 在共享页面里消费实例差异（写新页面的模式）

```js
// pages/xxx/xxx.js —— 页面顶部
const { FEATURES, UI_TEXT } = require('../../config/instance')
Page({
  data: { features: FEATURES, uiText: UI_TEXT },
  // ...
})
```

```xml
<!-- pages/xxx/xxx.wxml -->
<view wx:if="{{features.plan}}">…培养方案模块…</view>
<text>{{uiText.ROLE_TEACHER}}</text>
```

约定：**显隐用 `features`，文案用 `uiText`**；新键先加进 `scripts/build.js` 的 `DEFAULT_FEATURES` / `DEFAULT_UI_TEXT`，实例配置按需覆盖。

---

## 5. 构建引擎（scripts/build.js）

### 5.1 四类产物

| 产物 | 作用 | 来源 |
|---|---|---|
| `config/instance.js` | 运行时配置，页面/接口统一读取 | 实例配置序列化（含透传的 WARNING/PLAN BASE 与 KEY、FEATURES） |
| `project.config.json` | DevTools 识别的项目配置 | 读取现有文件 → 仅改写 `appid` → 写回；文件缺失时按模板生成 |
| `app.json` | 页面注册表 | 共享页（17 个）+ `FEATURES.customPages` + 标题 = `ASSISTANT_TITLE` + 默认/覆盖 tabBar |
| `assets/instances/<id>/**` | 差异化静态资源 | 镜像复制到项目根对应相对路径（如 tabBar 图标） |

> ⚠️ assets 目录按相对路径**覆盖**项目根文件。`build.js` 已强制拒绝镜像中出现与构建产物同名的
> 文件（`app.json` / `project.config.json` / `config/instance.js`），误放会直接构建失败并给出提示。

### 5.2 安全写入机制（重要）

- **内容未变化 → 跳过写入**：连续执行两次切换，第二次全部跳过，不产生 mtime 变更
- **原子写入**：先写 `<file>.tmp` 再 rename 替换，DevTools 文件监听永远不会读到半写文件
- 原因：DevTools 在文件被批量改写时可能读到半写状态，导致其内部配置解析失败
  （表现为 `Cannot read property 'subPackages' of undefined` + 模拟器空白，重启工具恢复）
- **密钥告警**：`jwc.local.js` 缺失导致 `PLAN/WARNING` KEY 为空时，`switch` 会显式告警

---

## 6. 开发工作流

### 6.1 新开发者上手（5 分钟）

**环境要求（一次性）：**

| 项 | 要求 |
|---|---|
| Node.js | ≥ 16.7（build.js 用到 `fs.cpSync`），建议 18 LTS |
| 微信开发者工具 | 任意近期版本（当前项目 base lib 3.16.2） |
| 小程序权限 | 需要目标小程序的开发者权限（AppID 有效即可开发；上传密钥只在发布时需要） |
| npm | 随 Node 安装 |

**步骤：**

```bash
# ① 克隆仓库
git clone https://github.com/DBZQ30/SparkPath-DGX-SPARK
cd miniprogram-framework-frontend

# ② 安装依赖（发布需要 miniprogram-ci；测试需要 jest）
npm install

# ③ ★ 必须先切换一次实例！
#    config/instance.js、app.json、project.config.json 都是构建产物、不入库，克隆后不存在。
#    不执行这一步直接打开 DevTools，页面会因 require 失败而空白。
npm run switch
```

④ 用微信开发者工具「导入项目」选择仓库目录
⑤ 点「编译」→ 模拟器应显示"本科新生学业规划智能助手"对话页（标题、欢迎语、输入框齐全）
⑥ 验证清单：标题正确 / 欢迎语正确 / 底部输入框可输入 / 控制台无红色报错

### 6.2 日常开发全景表

| 我想…… | 做法 | 影响 |
|---|---|---|
| 切换实例开发 | `npm run switch <id>` + DevTools 点「编译」 | appid、标题、欢迎语、占位符、API、tabBar、专属页全部切换 |
| 改实例的文案/API | 改 `config/instances/<id>.js` → switch 该实例 | 只影响该实例 |
| 加一个所有实例共有的页面 | 建页面 → 加进 `scripts/build.js` 的 `SHARED_PAGES` → switch | 全部实例生效 |
| 加某实例的专属页面 | 页面放 `pages/_ext/<id>/` → 配置 `FEATURES.customPages` → switch | 只进该实例的代码包 |
| 换某实例的 tabBar 图标 | 建 `assets/instances/<id>/images/xxx.png` → switch | 只覆盖该实例 |
| 给某实例关一个功能 | 配置 `FEATURES.xxx = false` → switch | 构建期/运行期同时生效 |
| 改角色文案 | 配置 `UI_TEXT` 对应键 → switch | 只影响该实例 |
| 新增一个实例 | `node scripts/new-instance.js <id> --title "名称"` | 生成配置 + 专属目录骨架，见第 11 节 |
| 发布一个实例 | 本地 `npm run release -- jwc --version 1.1.0`；CI 运行 `release:jwc` 手动 job | 成功后自动打 tag + CHANGELOG，见第 7 节 |
| 跑架构核验 | `npm test` | validate + validate-ui + jest；提交前自检，MR/推送后 CI 自动跑 |
| 只跑单元测试 | `npm run test:unit` | jest（纯逻辑，无需构建产物） |
| 预览切换产生什么 | `node scripts/build.js jwc --dry-run` | 不落盘 |

### 6.3 使用提示

- 切换后若工具弹出"项目配置已变更"→ 点确定重载；没弹就手动点一次「编译」
- **不要在工具编译进行中连续快速切换多个实例**（脚本已原子写入，但给工具一个呼吸时间更稳妥）
- 若模拟器空白且控制台出现 `Cannot read property 'subPackages' of undefined`：
  完全退出工具 → 重开；仍不行则在项目列表删除记录后重新导入（磁盘文件不受影响）
- 切换实例不会影响你的登录态：微信 storage 按 AppID 天然隔离

---

## 7. 发布流程（scripts/release.js + GitLab CI）

### 7.1 前置准备（一次性）

1. `npm install`（安装 miniprogram-ci）
2. 准备**代码上传密钥**：微信公众平台 → 开发管理 → 开发设置 → 小程序代码上传密钥 → 下载
3. 密钥存放（二选一）：
   - **本地发布**：按 AppID 命名放入 `keys/`：`keys/<MINIAPP_APPID>.key`（已 gitignore，绝不入库）
   - **CI 发布**：把密钥 base64 配到 CI 变量 `KEY_<AppID>`（勾选 Protected），
     CI 内由 `scripts/ci-restore-keys.js` 恢复，job 结束即消失

### 7.2 发布命令（本地）

```bash
npm run release -- jwc --version 1.1.0 --desc "新增 XX 功能" --robot 1
npm run release -- --all --version 1.0.0        # 批量（当前只有 jwc）
```

发布流程：按该实例组装（同 switch）→ 校验密钥存在 → miniprogram-ci 上传代码包 → 输出结果。
密钥缺失/上传失败时给出明确错误信息。工作区有未提交修改时先打印警告。

### 7.3 发布命令（GitLab CI）

默认分支 pipeline 上提供 **1 个手动 job** `release:jwc`（另有自动的 `architecture-check`）：

1. Pipeline 页面点 ▶ 运行 `release:jwc`
2. Run pipeline 时填写变量：`VERSION=1.1.0`、`DESC=新增 XX 功能`
3. job 内自动恢复密钥 → 上传 → 输出结果

> 前提：管理员已在 GitLab 配好 `KEY_<AppID>` 变量。

### 7.4 发布留痕（自动，本地与 CI 均生效）

- **追加 `CHANGELOG.md`**：`日期 | 实例 | 版本 | commit | 说明`，幂等去重
- **打本地 tag** `<实例>@<版本>`（如 `jwc@1.1.0`），`git push --tags` 后可用于追溯（CI 环境跳过）

---

## 8. 运行时集成点与后端协议

### 8.1 三个 BASE 与两种鉴权

| BASE | 服务 | 鉴权 |
|---|---|---|
| `API_BASE_URL` | Hermes 网关（对话 / 登录 / methods） | `Authorization: Bearer <session_token>` |
| `WARNING_API_BASE` | 学业预警服务 | `X-API-Key: <WARNING_API_KEY>` |
| `PLAN_API_BASE` | 培养方案 / 规划 / 文件中心 / GPU 指标 | `X-API-Key: <PLAN_API_KEY>` |

登录请求：

```
POST {API_BASE_URL}/api/miniapp/login
{ "code": "<wx.login 返回的 code>", "instance_id": "jwc-assistant" }
```

- 会话 token 由**后端**用 `MINIAPP_SESSION_SECRET` 做 HMAC-SHA256 签发，前端只存储与附带；
- 后端按 `instance_id` 隔离用户、会话与知识库。

### 8.2 隔离模型（无需代码处理的部分）

| 维度 | 隔离机制 |
|---|---|
| 微信登录态 | 每个 AppID 独立，`wx.login` 的 code 只对各自 AppID 有效 |
| 本地缓存 | storage 键前缀由 `INSTANCE_ID` 派生（`utils/instance-keys.js`），实例间物理隔离 |
| 代码包 | 每个 AppID 上传独立代码包、独立版本号、独立审核 |
| 请求域名 | 各小程序后台独立配置 request 合法域名（同域名可复用） |

---

## 9. 已知边界与限制

| # | 边界 | 影响 | 状态 |
|---|---|---|---|
| 1 | 单实例（jwc） | 多实例机制保留为可扩展能力，新增按第 11 节 | 当前范围 |
| 2 | 预警/培养方案 Key 随包分发到客户端 | 仅作限流用途，需定期轮换 | 已知取舍 |
| 3 | 包体积 | 已按实例裁剪；若继续膨胀需拆分包 | 监控中 |
| 4 | DevTools 会话状态 | 编译中连切实例偶发空白（见 6.3 恢复步骤） | 已缓解（原子写入） |
| 5 | GitLab 服务端配置 | 保护分支 / MR 门禁 / CODEOWNERS / CI 密钥变量需人工配置一次 | ⏳ 待配置 |

---

## 10. FAQ

**Q1：克隆后打开 DevTools，对话页空白 / 报 require 找不到模块？**
A：`config/instance.js`、`app.json`、`project.config.json` 都是构建产物、不入库，克隆后不存在。
先执行 `npm run switch` 生成（`project.config.json` 缺失时构建脚本按模板自动生成），再打开工具。

**Q2：切实例后模拟器还是显示上一个实例的内容？**
A：切完没点「编译」或工具未重载。手动点一次「编译」；仍不行则完全退出工具重开。

**Q3：切换后聊天发不出去（请求失败）？**
A：先看「详情 → 本地设置」的「不校验合法域名」是否勾选；其次确认该 AppID 在后台配了 request 合法域名。

**Q4：为什么 release 提示缺少密钥？**
A：`keys/<AppID>.key` 不存在。从微信公众平台下载代码上传密钥，按 AppID 命名放入 `keys/`（gitignore 保证不入库）。

**Q5：为什么功能入口不见了？**
A：该功能由 `FEATURES.<键>` 控制（如 `warning`/`plan`/`docCenter`/`jxtzSync`），且需要对应的
`WARNING_API_BASE`/`PLAN_API_BASE` 与本地 Key；任一缺失时入口隐藏。

**Q6：角色文案怎么改？**
A：在实例配置加 `UI_TEXT` 覆盖对应键（见 4.1），→ switch 该实例。

**Q7：GitLab CI 里怎么发布？**
A：默认分支 pipeline → 运行 `release:jwc` 手动 job → 填 `VERSION`/`DESC`。前提是已配好 `KEY_<AppID>`。

---

## 11. 扩展指南：新增一个实例

1. **生成骨架**：`node scripts/new-instance.js <new-id> --title "助手名称" [--appid wx...]`
   自动生成实例配置 + `pages/_ext/<new-id>/` + `assets/instances/<new-id>/` 目录
2. 编辑 `config/instances/<new-id>.js`：补全 `APPID`、欢迎语、占位符，按需调整 `FEATURES` 与 `UI_TEXT`；
   如需独立服务，填 `WARNING_API_BASE` / `PLAN_API_BASE`
3. （可选）往 `assets/instances/<new-id>/` 放差异图标；往 `pages/_ext/<new-id>/` 写专属页并在 `customPages` 声明
4. `npm run switch <new-id>` 验证（DevTools 重载后确认 AppID/标题）
5. 微信公众平台注册小程序 → 配 request 合法域名 → 下载上传密钥到 `keys/<AppID>.key`（本地）
   或配置 CI 变量 `KEY_<AppID>`（CI 发布）
6. 发布：本地 `npm run release -- <new-id> --version 1.0.0`，或 CI 运行 `release:<new-id>` 手动 job
7. 提交配置文件入库（实例文件是团队的配置资产）

> 无需改动任何脚本与共享代码——脚本自动扫描 `config/instances/` 目录。

---

## 12. 命令速查

```bash
npm run switch                  # 切到默认实例（教务）
npm run switch jwc              # 切到指定实例
node scripts/build.js <id> --dry-run   # 预览变更（不落盘）
node scripts/new-instance.js <id> --title "名称"   # 新增实例骨架
npm test                              # 架构核验 + UI 规约 + jest 单元测试
npm run test:unit                     # 只跑 jest 单元测试
npm run release -- jwc --version 1.0.0 --desc "说明"   # 发布单个（成功后自动打 tag + CHANGELOG）
npm run release -- --all --version 1.0.0               # 发布全部
git push --tags                       # 推送发布 tag（用于版本追溯）
```

---

## 13. 附：架构核验（自动化）

本表已由 `scripts/validate.js` / `scripts/validate-ui.js` + jest 自动化，运行方式：`npm test`
（提交/发布前跑一遍，CI 也会跑）。

| # | 核验项 | 方式 | 当前结果 |
|---|---|---|---|
| 1 | 实例清单（1 个）+ 全实例构建冒烟（必填字段/页面存在性） | validate.js 逐实例 `--dry-run` | 1/1 通过 ✓ |
| 2 | api.js 接口全部由对应 BASE（API / WARNING / PLAN）派生 | 逐键校验前缀 | 66 个 ✓ |
| 3 | UI_TEXT 合并正确性 | jwc → 教师 | ✓ |
| 4 | `_default` = 教务 | require 对比 | ✓ |
| 5 | 共享代码零硬编码 | "招生老师" 0 处；无 `mba_admission`/`mba_username`；无 `INSTANCE_ID` 分支 | ✓ |
| 6 | 模块加载链 | Node stub 运行时，require 全部共享页面 + app.js | 17 页面 + app.js ✓ |
| 7 | 构建产物不入库 | .gitignore 含 app.json / project.config.json / config/instance.js | ✓ |
| 8 | SHARED_PAGES 页面文件齐全 | 逐页检查 `.js` 存在 | 17 个 ✓ |
| 9 | 存储键按实例派生 | `utils/instance-keys.js` `PREFIX === INSTANCE_ID` | ✓ |
| 10 | 界面/样式规约 | validate-ui.js（括号配平 / 事件绑定 / 图标 / 禁用色值 / emoji） | ✓ |
| 11 | 单元测试 | jest（trace / instance-keys / api / trace-demo / build） | ✓ |
