# HANDOFF — 本科新生学业规划智能助手小程序（交接文档）

> 写给接手的新 agent。本文件记录 2026-09-24 ~ 09-25 这一轮工作的**全部事实、结论、坑与待办**，
> 目标是让你不需要重新踩一遍坑。先读 §1 建立地图，再看 §6 待办决定从哪继续。
>
> 协作规范见仓库 `docs/`：`ARCHITECTURE.md`（架构）、
> `UI-DESIGN-SYSTEM.md`（**界面与样式规范，改 UI 前必读**）。

---

## 1. 系统地图（先建立这个认知）

### 1.1 目录即角色

| 路径 | 是什么 | 说明 |
|---|---|---|
| `miniprogram-framework-frontend/` | **微信小程序**（本科新生学业规划智能助手 jwc 实例） | 17 个页面；`app.json`/`project.config.json`/`config/instance.js` 是**构建产物（gitignored）** |
| `agent4som/` | **业务后端（FastAPI）** | `academicwarning`(预警) / `trainingplan`(方案解读+规划) / `doccenter`(文件中心) / `knowledge_base` / `gpu_metrics`(本会话新增) |
| `agent4som-hermesagent/` | **HERMES_HOME**（不是普通目录！） | `~/.hermes` 是它的**软链**（inode 相同）。内含 `hermes-agent/`（hermes 源码）+ `plugins/miniapp-platform/adapter.py`（小程序适配器）+ `config.yaml`/`.env`/`state.db` |
| `deploy/dgx/` | 部署配置 | systemd 单元、nginx 片段、隧道脚本 |

### 1.2 运行时拓扑（真实端口）

```
微信小程序
  └─ https://<CAMPUS_PORTAL>/accapi/...   （校园网关 → acc-svr nginx → SSH 隧道 → DGX）
       ├─ /accapi/dgx-agentapi/*  → miniapp-proxy :8020 → hermes gateway :8010   （对话/登录/各类 methods）
       ├─ /accapi/dgx-plan/*      → training-plan-api :8009                      （方案解读/规划/文件中心/GPU 指标）
       └─ /accapi/dgx-warning/*   → 预警服务                                     （选课预警）
  DGX 本地：
       :8000 vLLM (Qwen3.8-27B-FP8，served-model-name=qwen3.8-27b，max-model-len 262144)
       :8007 chroma-server
       :8009 training-plan-api   （systemd **user** 单元）
       :8010 hermes gateway      （systemd **system** 单元 hermes-gateway@jwc-assistant.service）
       :8020 miniapp-proxy
```

**关键**：`~/.hermes/hermes-agent` 与 `agent4som-hermesagent/hermes-agent` 是**同一个目录**（软链 + 同 inode）。
线上网关进程用的就是这棵树里的 venv —— 它就是**活的运行时**，不是未使用的代码。

---

## 2. 本会话做了什么（按提交，由旧到新）

### 2.1 视觉改版「学籍台账」（全部 17 页）

| 提交 | 内容 |
|---|---|
| `98310586` | 三个 tab 页（对话/服务/我的）视觉改版 + 设计系统诞生（**注意：这条是用户自己提交的**，信息写在 skill 修复下） |
| `30e2ada3` | 其余 14 页迁移（学生页 + 管理页） |
| `725a1174` | `docs/UI-DESIGN-SYSTEM.md`（令牌/组件/骨架/硬约束） |
| `13b3edc9` | `scripts/validate-ui.js` + 并入 `npm test` |

**设计系统要点**（改 UI 必须遵守，详见 UI-DESIGN-SYSTEM.md）：
- 颜色/字号/间距/圆角**只用 `app.wxss` 里的 CSS 变量**与 `ui-*` 共享组件
- **导航栏承载页面名**（`navigationBarTitleText`），页面内不再重复大标题
- 禁止 emoji 当图标、禁止 `box-shadow`（**`pages/trace/trace` 是唯一豁免**）
- 旧色值（`#2563eb` 等 88 项）会**被 CI 拦住**
- `npm test` = `validate.js`（架构）`&&` `validate-ui.js`（界面）

### 2.2 Bug 修复

| 提交 | 修了什么 |
|---|---|
| `ea78e511` | doc-center 上传类型 picker **错位一格**（range 用了带「全部类型」前缀的数组，索引却按无前缀取）；功能筛选**显示 key 而非 label** + `=== 0` 严格相等导致越界崩溃 |
| `b09c97dd` | 对话回答**不折行撑出屏幕**（rich-text 节点不继承气泡样式 → 改成内联注入 + `table-layout:fixed`）；轨迹页加**暂停/点步骤展开**；样例数据**明确标注** |

### 2.3 「执行轨迹」演示功能（本会话重点，比赛用）

| 提交 | 内容 |
|---|---|
| `45eaf727` | 新增 `pages/trace/trace`（暗色观测台）+ 对话内联「执行过程」折叠条 |
| `af720b64` | `tui_gateway` 新增 `skill.activate` 事件 —— ⚠️ **对小程序无效，见 §5.1** |
| `9fc2fdea` | **网关侧**接入真实 agent 活动：`gateway/run.py` 的 opt-in 旁路 + 适配器 `on_agent_activity` |
| `e6848631` | 前端接真实 trace；指标缺失显示 `—`（不编数字） |
| `6e60fe7a` | `GET /api/metrics/gpu`（真实 GPU/内存/吞吐）+ 前端轮询 |
| `95117005` | ⚠️ **指标从网关插件移到 `agent4som`**（网关 PrivateDevices 拿不到 GPU，见 §5.2） |
| `617a6984` | 文档 §9.6 更新为真实数据来源 |

---

## 3. 执行轨迹：架构与数据契约

### 3.1 数据流

```
小程序提问
  → /api/chat（miniapp 适配器 _chat）
      · activity_begin(openid)          ← 开缓冲
      → handle_message → hermes 网关
          → agent 跑起来
          → gateway/run.py: progress_callback
              · 若适配器声明 wants_agent_activity → adapter.on_agent_activity(...) 旁路
                  （位置在进度队列门控**之前**：轨迹与「工具展示开关」无关）
      · activity_take(openid, answer)   ← 组装 trace
  ← 响应体多一个 "trace" 字段
前端把完整 trace 存本地缓存，消息上只挂 {id, summary} 给折叠条
```

### 3.2 事件契约（`utils/trace.js` 顶部注释是权威）

```
{ t, type: 'user' }                                    提问
{ t, type: 'reasoning', text }                         思考/说明（上游按 500 字/块截断）
{ t, type: 'skill', name, source }                     命中 Skill（由 skill_view 工具调用推导）
{ t, type: 'tool.start', tool, title }                 工具开始（title 取真实命令）
{ t, type: 'tool.done',  title, duration_s }           工具完成（真实耗时）
{ t, type: 'files', files: [] }                        数据文件流入（预留）
{ t, type: 'result', answer, artifacts?, metrics? }    结果
trace.telemetry: [{ t, gpu, vram, vram_total, tps }]   算力采样（保留字段，当前不展示）
```

### 3.3 真实 vs 样例（务必分清，用户很在意这个）

| 展示项 | 现在的真实来源 |
|---|---|
| 提问 / 结论 / 总耗时 | ✅ 真实（本次请求与回复） |
| 思考 | ✅ 真实（`reasoning.available`，**上游截断 500 字**） |
| 命中 Skill | ✅ 真实（`skill_view` 工具调用） |
| 工具/命令/耗时 | ✅ 真实（`tool.started` / `tool.completed`） |
| ~~GPU / 内存 / 吞吐~~ | ⛔ **已按要求从轨迹页移除展示**；采集接口仍在（`GET /api/metrics/gpu`，见 §4），只显示「总耗时」 |
| **产出清单 artifacts** | ❌ **仍是样例**（网关不产生，真实轨迹里没有）；真实轨迹会**隐藏该块** |
| 录播样例 | 有 `demo: true` 标记，页面显示「演示回放 · 除结论外均为内置样例」 |

**数据来源判定（一句话）**：后端仅在**有真实活动事件（工具调用）**时才在回复体下发 `trace`；
因此 **有工具调用的回答 → 展示真实轨迹**，**无工具调用的回答 → 回放内置样例轨迹（页面标注 `demo: true`）**；
`?live=1` 走实时轮询，始终为真实执行。

**开关**：`pages/chatbot/chatbot.js` 的 `DEMO_TRACE_ENABLED` 控制「无真实 trace 时是否用内置样例兜底」；
当前为 `true`（无工具调用的回答回放样例并明确标注）。置 `false` 后，只有后端回传真实 `trace` 时才显示轨迹。

---

## 4. GPU 指标接口（`GET /api/metrics/gpu`）

**位置**：`agent4som/gpu_metrics.py`（APIRouter，挂在 `trainingplan.api:app`，端口 8009）
**公网**：`https://<CAMPUS_PORTAL>/accapi/dgx-plan/api/metrics/gpu`
**鉴权**：`X-API-Key`（`WARNING_API_KEY` / `TRAINING_PLAN_API_KEY`）

**数据来源（全部真实，取不到一律 `null`，绝不造数）**：

| 字段 | 来源 | 备注 |
|---|---|---|
| `gpu.util/temp_c/power_w/clock_mhz` | `nvidia-smi --query-gpu` | GB10 支持这四项 |
| `mem.used_mb/total_mb` | `/proc/meminfo` | **GB10 是统一内存**，`nvidia-smi` 的 Memory-Usage 为 `Not Supported` |
| `engine.running/waiting/kv_cache` | vLLM `/metrics` | |
| `tps` | `vllm:generation_tokens_total` **两次采样增量** | 计数器；**首次调用返回 null**；0.8s TTL 缓存 |

**实测样例**：`gpu.util 5-6% / 43-44C / 12.6W / 2431MHz`、`mem 119k/124608 MB`、`tps 首次 null → 二次 0.0`。

---

## 5. 坑（都是实测踩出来的，务必看）

### 5.1 ⚠️ `skill.activate` 在小程序链路上不生效

`af720b64` 把 `skill.activate` 加在了 **`tui_gateway/server.py`**（TUI/仪表盘那条事件线）。
但小程序走的是 **gateway 平台适配器**那条线，**根本不会触发它**。
→ 小程序侧的「命中 Skill」是靠 `gateway/run.py` 的 `progress_callback` →
`on_agent_activity` 里**检测 `skill_view` 工具调用**推导的。
（`skill.activate` 本身对 TUI/桌面端有效，不是废码，只是对本项目的小程序无贡献。）

### 5.2 ⚠️ 网关看不到 GPU（PrivateDevices）

`hermes-gateway@jwc-assistant.service` 设了 **`PrivateDevices=yes`**：
- 网关进程 `/proc/<pid>/root/dev` 里 **nvidia 设备数 = 0**（宿主机为 6）
- 所以在网关进程里 `nvidia-smi` **必然失败** → 这就是 GPU 指标一度全 null 的根因
- **不要**为了让网关读 GPU 而去掉这个沙箱（agent 的 `terminal` 工具同处该沙箱，是有意加固）
- 正确做法就是现在这样：**指标放在能看见设备的服务（agent4som :8009）**

### 5.3 重启网关

- 服务名：`hermes-gateway@jwc-assistant.service`，**system 级**（`systemctl --user` 看不到）
- `systemctl restart` 需要**交互式认证**（无免密 sudo 会失败）
- **可用的替代**（已验证）：`kill -TERM <MainPID>` → 单元里 `Restart=always` + `RestartSec=5` 会拉起新实例，
  并且新进程归入服务 cgroup（干净）。**不要用 `setsid ... gateway run --replace` 手动起**，容易和 systemd 抢。

### 5.4 构建产物 & 配置

- `app.json` / `project.config.json` / `config/instance.js` **是构建产物（gitignored）**
  → 改 tabBar、加页面**必须改 `scripts/build.js`**（`SHARED_PAGES` / `DEFAULT_TAB_BAR`），再 `npm run switch`
- `config/instance.js` 由 `npm run switch` 生成（源配置在 `config/instances/`）
- 本仓库当前**只有 jwc 一个实例**（多实例占位已移除）

### 5.5 校验脚本会拦你（这是好事）

- `npm test` = `scripts/validate.js` + `scripts/validate-ui.js`
- `validate.js` 有一条：**api.js 里每个 URL 必须派生自对应 BASE**，
  且 **键名决定用哪个 BASE** —— `PLAN_*` → `PLAN_API_BASE`，`WARNING_*` → `WARNING_API_BASE`，其余 → `API_BASE_URL`。
  （我为此把 `MINIAPP_METRICS_URL` 改名为 `PLAN_METRICS_URL`。）
- `hermes-agent` 全量测试有 **36 项环境性既有失败**（gateway 目录，环境缺浏览器等）。
  **判断方法**：`git stash` 掉改动再跑一遍对照，两次一致才说明与你无关。

### 5.6 环境

- 服务器会**不定时重启**；`/tmp/opencode` 会被清空（playwright 的 npm 包没了，但浏览器缓存在 `~/.cache/ms-playwright`）
- 本机是 **aarch64**（DGX Spark / GB10）：x86 的 Chromium 跑不了，渲染截图要用
  `npx playwright install chromium`（会下 arm64 headless shell），或直接跳过视觉验证
- `agent4som-hermesagent/` 下的 `.restart_*` / `feishu_seen_message_ids.json` 是**运行时产物，不要提交**

---

## 6. 待办（按优先级，含已验证的证据）

### 6.1 🔴 agent 慢 —— 已定位到根因（强烈建议先做）

**实测账（会话 `20260925_131222_9485d41f`，一次提问 → 206.6s）**：
- LLM 9 次调用合计 **137.4s**；工具（知识库检索 ×5）约 **67s**
- **system prompt = 17,129 字符，其中 `## Skills (mandatory)` 占 10,104 字符（59%）** ← 主因
  （72 个 skill 目录被注入；只有 3 个与交付相关）
- 每次调用 prompt：**10,020 → 21,262 tokens**（基础 10k + 累积工具结果）
- vLLM 启动参数：`--max-num-batched-tokens 4096` → 21k prompt 要**分 6 批 prefill**
- `--reasoning-parser qwen3` → **推理 token 也生成**；全局 `generation_tokens_total` 远大于可见 `out`
- 实测生成吞吐 **11–13 tok/s**
- 知识库检索单次 **10.7–16.7s**，且 `Step-back rewrite failed` **24/24 全失败**

**建议动作（按性价比）**：
1. **裁 skills 目录**：网关的 `config.yaml`（`agent4som-hermesagent/config.yaml`）里有 `skills:` 段
   （目前形如 `skills:\n  catalog: all`）→ 大概率能配成只暴露需要的 3 个。
   **省 10k 字符 × 每次调用**，收益最大。改完 `kill -TERM` 重启网关。
2. **`--max-num-batched-tokens` 4096 → 8192/16384**（vLLM 启动参数）→ 减少 prefill 批数。
3. **适配器 `response_timeout` 120s → 300s**：目前 >120s 的正常 run 会被客户端判成
   「当前处理时间较长」（假超时）。配置在 `config.yaml` 的 platform `extra.response_timeout_seconds`。
   实测那次 206.6s 的 run **服务端其实跑完了**（日志：`response ready: time=206.6s`）。
4. **修 `Step-back rewrite failed: 'NoneType' object has no attribute 'strip'`**
   （`tools/query_kb.py` 的查询改写路径；24/24 失败，很可能拖慢检索）。**未定位，请先复现再改。**
5. 演示前**预热一次**（把 prefix cache 与模型打热）。

**怎么量**：`~/.hermes/state.db` 的 `sessions` 表有
`system_prompt / input_tokens / output_tokens / reasoning_tokens / cache_read_tokens / api_call_count`，
`~/.hermes/logs/agent.log` 有每次 `API call #n ... in=... out=... latency=...` 与
`tool <name> completed (Xs, N chars)`。

### 6.2 🟠 真机验收（本会话无法完成）

只能在微信开发者工具 + 真机验证：
- `pages/trace/trace` 的 **`<canvas type="2d">` 拓扑**与**脉冲动画**
- **`wx.vibrateShort()` 振动**（命中 Skill 时）
- 暂停/点步骤展开、内联折叠条、**对话长回答折行**（b09c97dd 的修复）
- **base64 SVG 图标**在 iOS/Android 的显示（`app.wxss` 底部，早期就留的风险点）

### 6.3 🟡 其它未完成

- **选课预警页「上传数据文件 / 已上传文件」合并**：用户早前提过（把「已上传」标在上传模块上，取消独立列表）。
  我给了方案与关切（其他专业历史行、删除/重传位置），**用户未最终确认，未动代码**。
- **真实 trace 的产出清单（artifacts）**：网关不产生 → 目前真实轨迹隐藏该块。
  要显示需后端在 `result` 里补 `artifacts`。
- **`tps` 首次为 null**：前端显示 `—`（诚实），如需首屏就有值，可在适配器启动时先采样一次。
- **`utils/trace-demo.js` 的样例数值**（128 人 / 12 人缺口）与真实结果不同 —— 已用 `demo` 标注，
  且结论取自真实回复；若要彻底消除违和感，可考虑只保留结构、把数值也换成占位文案。

---

## 7. 怎么验证（照抄即可）

```bash
# 前端（架构 + 界面规范）
cd miniprogram-framework-frontend && npm test

# 后端（指标接口）
cd agent4som && venv/bin/python -m pytest tests/test_gpu_metrics.py -q

# 后端（轨迹活动采集）
cd agent4som-hermesagent/hermes-agent
scripts/run_tests.sh tests/test_miniapp_activity.py -q

# 指标接口实测（本机）
KEY=$(grep -E '^WARNING_API_KEY=' ../agent4som/.env | cut -d= -f2-)
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8009/api/metrics/gpu | python3 -m json.tool

# 指标接口实测（公网，小程序实际路径）
curl -s -H "X-API-Key: $KEY" https://<CAMPUS_PORTAL>/accapi/dgx-plan/api/metrics/gpu

# 网关健康
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/health   # 期望 200

# 端到端（真实 agent，会消耗一次推理；需自签会话令牌）
#   适配器 _issue_token(openid, ttl) + MINIAPP_SESSION_SECRET（在 ~/.hermes/.env）
#   注意：简单问题可能没有 trace（没有非结果事件时后端返回 None，属设计如此）
```

---

## 8. 关键文件索引

| 关注点 | 文件 |
|---|---|
| 设计令牌 / `ui-*` 组件 / 图标 | `app.wxss` |
| 界面规范（含 §9 演示模式附录） | `docs/UI-DESIGN-SYSTEM.md` |
| 界面自动核验 | `scripts/validate-ui.js` |
| 页面注册 / tabBar / 构建 | `scripts/build.js` |
| 轨迹事件契约与摘要 | `utils/trace.js` |
| 录播样例数据 | `utils/trace-demo.js` |
| 轨迹页（暗色观测台） | `pages/trace/trace.{js,wxml,wxss,json}` |
| 对话页（内联折叠条 + 开关） | `pages/chatbot/chatbot.{js,wxml,wxss}`，找 `DEMO_TRACE_ENABLED` |
| 网关侧事件旁路（opt-in） | `agent4som-hermesagent/hermes-agent/gateway/run.py`，搜 `_activity_adapter` / `on_agent_activity` |
| 小程序适配器（轨迹采集 + 业务接口） | `agent4som-hermesagent/plugins/miniapp-platform/adapter.py`，搜 `wants_agent_activity` |
| GPU 指标接口 | `agent4som/gpu_metrics.py`（挂在 `agent4som/trainingplan/api.py`） |
| 接口 URL 汇总 | `miniprogram-framework-frontend/config/api.js` |

---

## 9. 上下文：这是给谁做的

用户在准备**第三届 NVIDIA DGX Spark 黑客松**，交付物是 `agent4som/shared_skills/` 下的三个 skill：
`academic-warning`（选课预警/选课检查）、`multi-path-academic-planning`（多路径学业规划）、
`training-plan-interpretation`（培养方案解读）。

「执行轨迹」页的定位是**演示/录屏**：把 agent 的「思考 → 命中 Skill → 走 Skill 流程 → 思考执行 → 得到结果」
可见化。**用户非常在意"展示的数据是不是真的"** —— 请保持 §3.3 的诚实约定：
**取不到就显示 `—`，样例就必须标注为样例。**
