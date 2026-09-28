# 小程序视觉系统（「学籍台账」）

> 适用范围：`miniprogram-framework-frontend` 全部 17 个页面。
> 本文是**视觉与样式的唯一约定**：令牌清单、共享组件用法、页面骨架、迁移硬约束。
> 架构原理见 ARCHITECTURE.md；本文只讲**怎么把界面做对**。
>
> 落版时间：2026-09-24。相关提交：`98310586`（三个 tab 页 + 设计系统）、`30e2ada3`（其余 14 页）。

---

## 1. 设计原则

把小程序当成一本**精密的学籍台账**来做：墨色为骨、纸面为底、朱砂为印。

| # | 原则 | 含义 |
|---|---|---|
| 1 | **记录像记录** | 数字（学分、人数、比例）用**宋体**、右对齐、等宽；标签用黑体。数字是内容，不是装饰。 |
| 2 | **单一强调色** | 朱砂 `--seal` **只用于需要注意 / 破坏性**（预警、删除、错误）。其余一律墨/纸/灰。 |
| 3 | **纸面，不是卡片墙** | 卡片用**发丝边框**，不用阴影；圆角只保留 3 档。识别度来自版头与字体，不来自装饰。 |
| 4 | **说人话** | 界面文案用用户语言，不暴露内部术语（"孤儿扫描""平替""新鲜"）。 |
| 5 | **四态齐全** | 加载 / 空 / 失败 / 内容，每页都要设计。失败态要给出下一步。 |
| 6 | **令牌优先** | 颜色、字号、间距、圆角只从令牌取值，页面不再各写各的。 |

---

## 2. 设计令牌

全部定义在 `app.wxss` 的 `page { }` 里（CSS 变量）。**页面样式只允许用 `var(--…)`**。

### 2.1 颜色

| 令牌 | 值 | 用途 |
|---|---|---|
| `--ink` | `#17324D` | 主品牌 / 标题 / 主按钮 / 用户气泡 / 选中态 |
| `--ink-2` | `#41556B` | 次级正文 |
| `--ink-3` | `#5C6B7A` | 元信息（**全站唯一浅灰，白底 ≥4.5:1**） |
| `--paper` | `#FFFFFF` | 卡片 / 表面 |
| `--canvas` | `#F3F5F8` | 页面底 |
| `--line` | `#E2E6EC` | 发丝分隔线 / 卡片边框 |
| `--seal` | `#B3261E` | 朱砂：**仅**需要注意 / 破坏性 |
| `--seal-soft` | `#FBEDEB` | 朱砂浅底（标签 / 横幅） |
| `--jade` | `#1F7A55` | 成功 |
| `--jade-soft` | `#E8F5EF` | 成功浅底 |
| `--amber` | `#9A6400` | 提示 |
| `--amber-soft` | `#FBF3E2` | 提示浅底 |

> **禁止**再出现旧色值：`#2563eb`、`#1f2937`、`#111827`、`#8895a7`、`#8a94a6`、`#9ca3af`、`#9aa3b2`、`#6b7280`、`#f5f6fa`、`#f4f6f8` 等。
> 语义映射：原来的红（`#dc2626/#b91c1c/#c2410c`）→ `--seal`；绿（`#059669/#047857/#10b981`）→ `--jade`；琥珀（`#b45309/#d97706/#f59e0b`）→ `--amber`。

### 2.2 字号

| 令牌 | 值 | 用途 |
|---|---|---|
| `--fs-display` | `64rpx` | 超大数字（如毕业总学分；页面可再放大） |
| `--fs-title` | `44rpx` | 版头大字（仅"对话"页用） |
| `--fs-head` | `34rpx` | 卡片内标题 / 弹层标题 / 统计数字 |
| `--fs-body-lg` | `30rpx` | 列表主标题 / 按钮 / 对话气泡 |
| `--fs-body` | `28rpx` | 默认正文 |
| `--fs-meta` | `24rpx` | 次要信息 / 说明 |
| `--fs-micro` | `22rpx` | **仅**标签 / 角标，不用于句子 |

### 2.3 间距

8rpx 基数：`--sp-1:8` `--sp-2:16` `--sp-3:24` `--sp-4:32` `--sp-5:40` `--sp-6:48`。

### 2.4 圆角

| 值 | 用途 |
|---|---|
| `8rpx`（`--r-sm`） | 小标记 |
| `12rpx` | 输入框 / 小控件 |
| `14rpx` | 主按钮 |
| `16rpx`（`--r-md`） | 卡片 / 列表 |
| `32rpx` | 底部弹层顶角 |
| `999rpx`（`--r-pill`） | 药丸 / 标签 / 分段 |

### 2.5 字体角色

| 类 | 字族 | 用于 |
|---|---|---|
| 默认（`page` 继承） | `-apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif` | 全部界面文字 |
| `.ui-num` | `"Songti SC", "STSong", "Noto Serif CJK SC", "Noto Serif SC", serif` | **数字 / 台账数值**（含 `tabular-nums`） |
| `.ui-serif` / `.ui-display` / `.ui-masthead-title` | 同上宋体 | 标题类 |

---

## 3. 图标系统

图标是**内联 SVG 的 base64**（`background-image`），不占额外资源文件，颜色已烘焙进 SVG。

- 基类 `.ic`（40rpx）/ 小号 `.ic-sm`（32rpx）
- 用法：`<view class="ic ic-sm ic-search"></view>`

**现有图标**（20 个）：

```
ic-plan      ic-route    ic-addfile   ic-archive   ic-shield    ic-warn
ic-folder    ic-phone    ic-person    ic-sync      ic-file      ic-info
ic-chev      ic-chevdown ic-search    ic-close     ic-more      ic-pencil
ic-refresh   ic-send
```

颜色约定：正文图标 `#17324D`；次要/关闭/更多/铅笔/刷新 `#5C6B7A`；箭头 `#B7C0CB`；发送 `#FFFFFF`。

**新增图标**（无需图片文件，直接生成 base64）：

```bash
node -e '
const body = `<circle cx="12" cy="12" r="9"/><path d="M12 8v8"/>`;   // ← 换成你的路径
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#17324D" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
console.log(".ic-yourname { background-image: url(\"data:image/svg+xml;base64," + Buffer.from(svg).toString("base64") + "\"); }");
'
```

把输出贴进 `app.wxss` 的图标区。约定：`viewBox="0 0 24 24"`、`stroke-width="1.7"`、圆头圆角、不填充。

> **不要用 emoji 当图标。** 唯一例外是勾选框里的 `✓`（它是文字符号，且在 `ui-check` 内）。

---

## 4. 共享组件速查

共享类一律 **`ui-` 前缀**（避免与旧页面局部类冲突）。全部定义在 `app.wxss`。

### 4.1 骨架

| 类 | 用途 |
|---|---|
| `.ui-page` | 子页容器：`min-height:100vh` + `24rpx 32rpx` 内边距 + 底部安全区 |
| `.ui-page-col` | 整屏滚动页：`height:100vh` + flex 列 |
| `.ui-masthead` / `.ui-masthead-title` | 版头大字（**仅"对话"页**，见 §6.2） |
| `.ui-utilbar` / `.ui-utilbar-btn` | 工具行（身份药丸 + 刷新） |
| `.ui-icon-btn` | 版头图标按钮（64rpx 方） |
| `.ui-role` | 身份药丸 |
| `.ui-section` / `.ui-title` / `.ui-note` | 分节标题 / 卡片标题 / 说明文字（`.ui-note-warn` `.ui-note-danger`） |
| `.ui-between` / `.ui-actions` | 两端对齐行 / 右侧操作组 |
| `.ui-empty` | 空态 |

### 4.2 卡片与列表

```xml
<view class="ui-card ui-card-pad">
  <view class="ui-title">四年学分结构</view>
  ...
</view>

<view class="ui-list">
  <view class="ui-item" bindtap="...">
    <view class="ui-ico"><view class="ic ic-plan"></view></view>
    <view class="ui-item-main">
      <view class="ui-item-row">
        <text class="ui-item-title">培养方案解读</text>
        <text class="ui-tag ui-tag-info">按学号匹配</text>
      </view>
      <text class="ui-item-sub">学分结构 · 先修关系 · 毕业条件</text>
    </view>
    <view class="ic ic-sm ic-chev ui-chev"></view>
  </view>
</view>
```

| 类 | 用途 |
|---|---|
| `.ui-card` / `.ui-card-pad` / `.ui-card-pad-sm` | 卡片（发丝边框，无阴影）/ 内边距 |
| `.ui-list` / `.ui-item` / `.ui-item-main` / `.ui-item-row` / `.ui-item-title` / `.ui-item-sub` | 列表与行 |
| `.ui-row` / `.ui-ico` / `.ui-txt` / `.ui-lbl` / `.ui-desc` / `.ui-chev` / `.ui-badge` | 功能入口行（图标 + 标题 + 描述 + 箭头/角标） |
| `.ui-group` / `.ui-group-label` | 功能分组（带右侧发丝线） |
| `.ui-tag` + `-info` `-warn` `-danger` `-ok` `-muted` | 状态标签 |
| `.ui-banner` + `-info` `-warn` `-danger` `-ok` | 提示横幅 |

### 4.3 表单与选择

| 类 | 用途 |
|---|---|
| `.ui-field` / `.ui-field-label` / `.ui-input` | 表单字段 |
| `.ui-pickers` / `.ui-picker-wrap` / `.ui-picker` / `.ui-picker-k` / `.ui-picker-v` | picker（`wrap` 必须包在 `<picker>` 上，内层用 `.ui-picker`） |
| `.ui-seg` / `.ui-seg-item` / `.ui-seg-on` | 分段控件（等宽，选中墨底） |
| `.ui-chips` / `.ui-chip` / `.ui-chip-on` | 筛选药丸 |
| `.ui-check` / `.ui-check-on` | 勾选框（视觉 44rpx，**整行 ≥64rpx 可点**） |
| `.ui-search` / `.ui-search-input` / `.ui-search-clear` | 搜索框（配 `.ic-search` / `.ic-close`） |

### 4.4 状态与反馈

| 类 | 用途 |
|---|---|
| `.ui-loading` / `.ui-spinner` | 加载态（`<view class="ui-spinner"></view>` + 文字） |
| `.ui-empty` | 空态 |
| `.ui-progress` / `.ui-progress-fill` | 进度条 |
| `.ui-stats` / `.ui-stat` / `.ui-stat-num` / `.ui-stat-label` / `.ui-stat-danger` | 统计（数字宋体） |
| `.ui-fold` / `.ui-fold-title` / `.ui-fold-arrow` | 折叠头 |
| `.ui-link` / `.ui-link-danger` | 文本按钮（**需自行保证 ≥64rpx 触控高**） |

### 4.5 弹层与按钮

```xml
<view wx:if="{{show}}" class="ui-sheet-mask" bindtap="close" catchtouchmove="noop">
  <view class="ui-sheet" catchtap="noop">
    <view class="ui-sheet-title">标题</view>
    <view class="ui-sheet-text">正文</view>
    <view class="ui-sheet-actions">
      <view class="ui-btn ui-btn-ghost" bindtap="close">取消</view>
      <view class="ui-btn ui-btn-primary" bindtap="ok">确认</view>
    </view>
  </view>
</view>
```

| 类 | 用途 |
|---|---|
| `.ui-sheet-mask` / `.ui-sheet` / `.ui-sheet-title` / `.ui-sheet-text` / `.ui-sheet-close` / `.ui-sheet-actions` | 底部弹层 |
| `.ui-btn` + `.ui-btn-primary` `-ghost` | 弹层/表单主次按钮（`<view>` 实现） |
| `.ui-pill` + `-primary` `-warn` `-ok` `-danger` | **行内**药丸操作（≥64rpx） |
| `.ui-num` | 宋体数字 |

> `<button>` 仅在必须用微信能力时保留（`open-type` / `loading` / `disabled`），并务必加 `::after { border: none; }`。

---

## 5. 页面骨架范式

### 5.1 内容页（多数子页）

```css
page { background: var(--canvas); min-height: 100vh; }
.page {
  min-height: 100vh; box-sizing: border-box;
  padding: 24rpx 32rpx calc(48rpx + env(safe-area-inset-bottom));
  display: flex; flex-direction: column; gap: 24rpx;
}
```

参考实现：`pages/plan/plan.*`、`pages/profile/profile.*`。

### 5.2 整屏滚动页（预览类）

```css
page { background: var(--canvas); min-height: 100vh; }
.page { display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; padding: 24rpx 32rpx 0; }
.body { flex: 1; min-height: 0; }
```

参考实现：`pages/knowledge-preview/knowledge-preview.*`、`pages/chatbot/chatbot.*`。

### 5.3 列表页

`ui-list` + `ui-item`，行高 ≥96rpx；行内操作放右侧 `.ui-pill`。

---

## 6. 迁移硬约束

新写或改造页面时，**必须**满足：

### 6.1 类名与颜色
1. 共享类只用 `ui-` 前缀；页面私有类不要与既有页面同名。
2. 颜色只用 `var(--…)`，不得出现旧色值（§2.1）。
3. **禁止 `box-shadow`**（卡片用 `1rpx solid var(--line)`）。唯一例外是**演示模式页** `pages/trace/trace`（暗色观测台用发光表达"激活"，见 §9）。
4. 圆角只用 8/12/14/16/32/999rpx。
5. 字号只用 `var(--fs-*)`；`22rpx` 只做标签。

### 6.2 标题
6. **导航栏承载页面名**（页面 `.json` 的 `navigationBarTitleText`），页面内**不再重复大标题**。
   例外：内容型标题保留 —— 如 `knowledge-preview` 的文档名、`warning-detail` 的学生姓名。
7. 唯一保留版头大字的是「对话」页（标题是产品名"本科新生学业规划智能助手"，与导航栏"对话"不重复）。

### 6.3 交互与可达性
8. 行内可点元素 **≥64rpx**；主按钮 88rpx；列表行 ≥96rpx。
9. 正文/元信息文字最浅 `--ink-3`（白底 ≥4.5:1），不得更浅。
10. 破坏性操作（删除/清空/撤销）**必须二次确认**。
11. 页面底部处理安全区 `env(safe-area-inset-bottom)`。
12. 状态四态齐全：加载 / 空 / 失败 / 内容。

### 6.4 文案
13. 不用 emoji 当图标或标题装饰。
14. 用用户语言：`入库知识`→`添加知识文件`、`孤儿`→`可清理记录`、`新鲜`→`数据有效`、`平替`→`课程替代`。

### 6.5 不改逻辑
15. 改造样式时，事件处理函数名、`{{}}` 绑定、`wx:if/for/key`、`data-*`、`catchtouchmove="noop"` 一律**原样保留**。
16. 只改 `.wxml` / `.wxss`；`.js` / `.json`（除导航标题）不动。

---

## 7. 校验

`npm test` 依次执行两个脚本：

```bash
npm test
# → scripts/validate.js     架构核验（实例构建 / 硬编码守卫 / 模块加载链）
# → scripts/validate-ui.js  界面与样式核验（本规范）
```

`scripts/validate-ui.js` 自动拦截以下问题（任一失败 → `npm test` 失败 → CI 红灯）：

| 核验项 | 拦截的问题 |
|---|---|
| WXSS 括号配平 | 样式写坏 |
| 事件处理函数 | WXML 引用了 `.js` 里不存在的 handler（**"误删 `bindtap`"的兜底**） |
| 数据绑定 | WXML 引用了 `.js` 里不存在的变量 |
| 图标类 | 用了未在 `app.wxss` 定义的 `.ic-*` |
| 旧色值回流 | 出现 88 项禁用色值之一（如 `#2563eb`、`#9ca3af`） |
| `box-shadow` | 卡片又用回阴影（**演示模式页 `pages/trace/trace` 豁免**） |
| emoji | 界面里出现 emoji（`✓ ✕` 等文字符号允许） |
| 导航标题 | 页面缺 `navigationBarTitleText` |

调色板外的色值只**告警不阻断**，提示你确认是否应并入令牌。

仍建议人工核对：
- `git diff` 逐页确认 **没有整块丢失 `data-*` / `wx:if` / 绑定**（脚本只能查出"引用了不存在的东西"，查不出"整段被删"）。
- 真机（iOS + Android）确认 base64 SVG 图标显示正常——**这是唯一无法在 CI 覆盖的点**。
- 低端安卓确认宋体回退（`.ui-num`）不会导致数字挤压。

---

## 8. 已知边界与待办

| 项 | 说明 |
|---|---|
| 多实例 | `config/instances/` 当前**只有 `jwc`**（多实例占位已在 2026-09 移除）。`scripts/build.js` 的 `DEFAULT_TAB_BAR` 等共享默认改动**只影响学业规划助手本身**。 |
| 图标 | 目前用 base64 SVG；若真机兼容性有问题，回退方案是导出 PNG 放 `images/`（生成脚本思路见 §3）。 |
| 未迁移的旧类 | 各页仍可能残留页面私有的旧类（已全部改用令牌），后续可按需合并进 `ui-` 组件。 |
| 无障碍 | 已满足对比度与触控尺寸；未做系统字体缩放适配。 |

---

## 9. 附录：演示模式（执行轨迹页）

> 比赛演示 / 录屏专用。产品页仍是亮色「学籍台账」；只有这一页是**暗色观测台**，
> 两者并存，用 `page` 级局部变量隔离，不污染产品调色板。

### 9.1 目标与入口

把 agent 的执行过程**可见化**：思考 → 命中 Skill → 走 Skill 流程 → 思考执行 → 得到结果。

| 入口 | 位置 | 说明 |
|---|---|---|
| 对话内联（主入口） | 助手回复下方「执行过程」折叠条，**默认收起** | 点开**就地展开**时间轴（亮色、克制） |
| 全屏「执行轨迹」 | 折叠条里的「全屏查看 ↗」→ `pages/trace/trace` | 暗色观测台（本附录） |

不做「服务」页入口；不分角色，所有人可见完整过程。

### 9.2 暗色调色板（`pages/trace/trace.wxss` 的 `page {}`）

| 变量 | 值 | 用途 |
|---|---|---|
| `--dbg` | `#0D1826` | 页底（深墨蓝，不是纯黑） |
| `--dpanel` / `--dpanel2` | `#14202F` / `#1B2A3B` | 面板 / 次级表面 |
| `--dline` | `#243447` | 发丝线 |
| `--dtext` / `--dmuted` / `--dfaint` | `#E6EDF5` / `#8B9BAC` / `#5C6E80` | 文字三级 |
| **`--nv`** | **`#76B900`** | **只表示「算力 / 激活」**：激活的 skill 节点 + 连线（原 GPU 表已随算力三项移除） |
| `--dseal` | `#E2554A` | 朱砂（预警数字 / 印章） |
| `--djade` | `#3FBF87` | 成功 / 完成 |

> NVIDIA 绿是全页唯一的高饱和色，且只有一个语义 —— 它一亮就是焦点。新增元素
> 不要再用它表示别的东西。

### 9.3 事件契约（录播与实时同一份）

`utils/trace.js` 定义契约，`utils/trace-demo.js` 是内置录播数据：

```
{ t, type: 'user' }                                    提问
{ t, type: 'reasoning', text }                         思考增量（流式）
{ t, type: 'skill', name, description? }               命中 Skill ← 后端 skill.activate
{ t, type: 'tool.start', tool, title, cmd? }           工具开始
{ t, type: 'tool.done',  title, duration_s, result? }  工具完成
{ t, type: 'files', files: [name] }                    数据文件流入
{ t, type: 'result', answer, artifacts, metrics }      结果
{ t, type: 'end' }                                     结束
trace.telemetry: [{ t, gpu, vram, tps }]               算力采样（保留字段，轨迹页当前不展示）
```

- **真实（有工具调用）**：后端在回复体下发 `trace` 时，对话页把 `trace` 挂到消息（`chatbot.js`），
  折叠条与全屏页展示**后端真实事件**（思考 / 命中 Skill / 工具命令与耗时）。
- **内置样例（无工具调用）**：后端仅在**有真实活动事件**时下发 `trace`；无工具调用的简单回答不下发，
  前端回放 `utils/trace-demo.js`（`demo: true`），页面明确标注「演示回放 · 除结论外均为内置样例
  （真实结论取自本次回答，`answerSource: 'reply'`）」。
- **实时**：`?live=1` 轮询后端当前 run 的事件缓冲，始终为真实执行（不播样例）。
- 开关 `DEMO_TRACE_ENABLED`（`chatbot.js`）控制「无真实 trace 时是否用样例兜底」，当前为 `true`。
- 后端的 `skill.activate` 事件在 `tui_gateway/server.py`（`_maybe_emit_skill_activate`），
  每个 session 每个 skill 只发一次，随 `tool.complete` 之后发出。

### 9.4 动效红线（低端安卓也能录屏）

1. **只动画 `transform` / `opacity`**，不碰 `width` / `height` / `top` / `left`。
2. **无常驻动画循环**：拓扑脉冲由 `skill` 事件触发，约 1.8s 后 `cancelAnimationFrame`
   自行停止；脑波条用 CSS 动画（`animation-duration` 固定，不占 JS）。
3. `setData` 节流：计时每 2 拍更新一次、sparkline 每 4 拍推一次；思考文本只在事件到达时追加。
4. 不用 `filter: blur()` / `backdrop-filter`；发光用 `box-shadow`（本页豁免）。
5. 图标不依赖生僻字形：skill 卡的菱形用纯 CSS 画（`::after` + `rotate(45deg)`），
   避免缺字变方块。

### 9.5 交互

- **暂停 / 继续**：现场可停在任意一刻讲解；暂停后计时条显示「已暂停」。
- **点任意步骤展开 / 收起**：完成态默认收成一行摘要，点开可细看该拍内容
  （`manualOpen`，不受播放进度影响）。
- **下一拍**：跳到下一个拍点（命中 skill / 工具 / 结果），用于现场控场。
- 录播态会在计时条下方显示标注，避免把样例误认为真实回放。

### 9.6 真实数据来源

前端是数据驱动的：事件是真的，展示就是真的。当前真实链路 ——

```
小程序 → /api/chat（miniapp 适配器）→ 网关 progress_callback → agent
                    ↑                                    ↓
       trace（随回复返回）              on_agent_activity（opt-in 旁路）
```

| 展示项 | 真实来源 |
|---|---|
| 提问 / 结论 / 总耗时 | ✅ 本次真实请求与回复 |
| 思考 | ✅ `reasoning.available`（上游按 **500 字/块**截断，这是框架限制） |
| 命中 Skill | ✅ `skill_view` 工具调用（含 skill 名） |
| 走 Skill 流程 | ✅ `tool.started` / `tool.completed`（工具名 + 真实命令 + 真实耗时） |
| **无工具调用的回答** | ⚠️ 后端不下发 `trace`，前端回放**内置样例**（`utils/trace-demo.js`），页面标注 `demo` |

> **算力指标（GPU / 统一内存 / 吞吐）当前不在轨迹页展示** —— 页面只显示「总耗时」。
> 采集接口 `GET /api/metrics/gpu` 仍部署在 `agent4som/gpu_metrics.py`（见 §4），
> 前端 URL 常量 `PLAN_METRICS_URL` 保留备用；若要重新展示，接回轮询即可。
