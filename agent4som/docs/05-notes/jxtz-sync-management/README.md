# JXTZ 同步管理 — 任务笔记

> 任务：按 `docs/01-architecture/som-rag-jxtz-sync-management-design.md`（v1.2）实施教务通知同步的小程序端管理功能。
> **阶段 A–E 已实现并部署**（阶段 E 于 2026-09-11 完成；原任务计划文件已移除）。

## 状态总览

| 阶段 | 内容 | 仓 | 状态 | 提交 |
|---|---|---|---|---|
| A | 同步脚本：运行记录 + flock + 参数收口 | agent4som | ✅ 完成 | `fe8fe89a` |
| B | cron 包装脚本（`os.execv`） | agent4som-hermesagent | ✅ 完成 | `f0fa7d0` |
| C | adapter 4 条路由 + 手动同步 | agent4som | ✅ 完成 | `940253e9` |
| D | 前端管理页 + 入口卡（仅学业规划助手） | miniprogram-framework-frontend | ✅ 完成 | `b17d454` |
| E | 部署（job / 停 timer / 重启网关） | 服务器手工 | ✅ 完成 | `bb57a4f`(deploy copy) |

图例：⬜ 未开始 · 🟦 进行中 · ✅ 完成 · ⏸️ 阻塞

> **计划已确认**（2026-09-11）：① 卡片仅学业规划助手 `FEATURES.jxtzSync=true`；② 文案由我拟定并登记 `DEFAULT_UI_TEXT`；③ 五段/4 次代码提交粒度认可。

## 提交记录

| 日期 | 提交 | 说明 |
|---|---|---|
| 2026-09-11 | `09b2f3f7` | chore: 忽略 AGENTS.md |
| 2026-09-11 | `d3d7c553` | docs: 设计 v1.2 完备性与可执行性核查（023） |
| 2026-09-11 | `dc04c96c` | docs: 建立 JXTZ 同步管理任务笔记目录（plan + 进度） |
| 2026-09-11 | `a0dfa745` | docs: 确认「仅学业规划助手」gating 与提交粒度，更新 plan |
| 2026-09-11 | `fe8fe89a` | feat: 阶段A 同步脚本支持运行记录、互斥锁与退出码收口 |
| 2026-09-11 | `f0fa7d0` | feat: 阶段B cron 包装脚本（os.execv，agent4som-hermesagent 仓） |
| 2026-09-11 | `940253e9` | feat: 阶段C adapter 4 条路由（开关/时间/手动/结果） |
| 2026-09-11 | `b17d454` | feat: 阶段D 前端同步管理页（前端仓，仅学业规划助手入口） |
| 2026-09-11 | `bb57a4f` | chore: 阶段E 同步 adapter 部署副本（agent4som-hermesagent 仓） |
| 2026-09-11 | `3210e01` | fix: switch 关闭 DevTools 无依赖文件过滤（前端仓） |

> 每次提交代码后，在此追加一行，并更新上方「状态总览」。

## 实现说明与偏差

- **阶段 D 文案未走 `UI_TEXT`（有意为之）**：核对前端仓实际约定——`validate.js:114-129` 仅拦截
  `招生老师` / `mba_admission` / `INSTANCE_ID` 分支，**不拦截通用中文**；`phone-whitelist`、`warning` 等
  共享页的通用文案均为字面量，`UI_TEXT` 只承载**角色相关**文案。本页（管理员专用）无角色文案，
  故采用字面量（与既有页面一致、避免给 7 个实例的全局默认塞无用键）。已过 `npm test`（零硬编码校验）。
  如需严格改为 `UI_TEXT`，为小改动。
- **手动 `/run` 不依赖 job 注册**（plan V4）：即便 `jxtz-sync` job 被误删，手动同步仍可执行；仅
  GET/toggle/schedule 会 404。
- **前端开关**用小程序内置 `<switch>`（设计 §5 原文），时间用 `picker mode="time"`。
- **DevTools「无依赖文件过滤」误判（已修，`3210e01`）**：微信开发者工具（1.05.2201210+）的增量依赖分析会把
  新加入 `app.json` 的页面误判为无依赖，报 `已被代码依赖分析忽略，无法被其他模块引用`。页面本身已正确登记
  在 `app.json`（实测 12 条 pages 含 `pages/jxtz-sync/jxtz-sync`），属工具行为。已在 `scripts/build.js` 生成
  `project.config.json` 时注入 `setting.ignoreDevUnusedFiles=false` / `ignoreUploadUnusedFiles=false`（官方规避开关），
  每次 `npm run switch` 自动生效。

## 关键依据速查

- 设计：`docs/01-architecture/som-rag-jxtz-sync-management-design.md`（v1.2）
- 评审：原设计评审文档（021 / 022 / 023）已移除
- 复用清单 / 陷阱清单（V1–V7）：原任务计划文件已移除

## 部署记录（阶段 E，2026-09-11 15:03 CST）

- cron job：`hermes cron create "0 4 * * *" --name jxtz-sync --script jxtz_sync.py --no-agent --deliver local` → **id `a75cd2bad6f3`**，next run `2026-09-12T04:00:00+08:00`。
- systemd：`jxtz-sync.timer` 已 `disable --now`（`disabled` / `inactive`）。
- 网关：adapter 部署副本已更新（md5 与源码一致 `a61bf3a5…`），`systemctl restart hermes-gateway@jwc-assistant`；`ActiveEnterTimestamp=2026-09-11 15:03:25 CST`，`/health` 200，新路由无 token 401。
- 冒烟：手工跑一次 cron 包装脚本 → `no_new`、退出码 0；`data/jxtz_sync_runs.jsonl` 落一条（`trigger=schedule`, `run_at=2026-09-11T15:03:50+08:00`）。
- **回滚**：`sudo systemctl enable --now jxtz-sync.timer` + `hermes cron pause a75cd2bad6f3`（或 remove）。

## LLM 问答模型（2026-09-11）

- **背景（历史排障记录）**：一次对话无回复，网关日志为 `model=deepseek-v4-flash` → **HTTP 402 Insufficient Balance**（DeepSeek 余额不足）。
- **经过**：曾临时切到 SCNet GLM-5.3（`d7056ff`），后按用户要求**改回 deepseek**（`67b94b6`，仅改 `~/.hermes/config.yaml` 的 model/custom_providers/auxiliary）。
- **现状**：主模型现为本地 `qwen3.8-27b`（`provider: local`）；「主问答与辅助模型均为 deepseek」是本条历史排障时的情况（当时 DeepSeek 账号返回 402，需充值后才能正常回答）。
- **miniapp 提示**：`gateway/run.py` 的「No home channel is set」对 miniapp 平台跳过——**保留**（`d7056ff` 中，未随模型回滚）。
- 密钥：临时加入的 `ANTHROPIC_AUTH_TOKEN` 已从 gitignored 的 `.env` 移除。
- 回滚/再切换：`git -C agent4som-hermesagent log --oneline` 参考 `d7056ff`（GLM）/ `67b94b6`（deepseek）。

## 其他改动（2026-09-11）

- **认证入口合并 + 待办角标**（与同步功能无关，随本次会话一并交付）：
  - 管理员区「查看老师认证申请 / 查看管理员认证申请 / 查看老师·管理员」三卡合并为 **1 张「认证与身份」**；
    `pages/list/list` 内用 tab 切换 `老师审核 / 管理员审核 / 已认证`（复用既有 handoff tab 机制）。
  - **待办角标**：`/api/methods/unread` 新增 `auth_teacher_pending` / `auth_admin_pending`（仅 admin/owner，
    复用 `TeacherAuthRequestDAO` / `AdminAuthRequestDAO`），前端合计显示在卡片上。
  - 提交：agent4som `c00a9a09`、hermes 部署副本 `631e6a5`、前端 `749254d`。已部署（网关重启 15:43:01，健康）。

## 未决

1. ✅ 新页文案字符串（阶段 D 时拟定并登记）。
2. ✅ 前端卡片按实例显隐：仅学业规划助手（`FEATURES.jxtzSync`）。
3. ✅ 提交粒度确认。
4. ✅ 阶段 A–E 全部完成并部署；**端到端（带真实 token）待用户在微信开发者工具手动验证**。
