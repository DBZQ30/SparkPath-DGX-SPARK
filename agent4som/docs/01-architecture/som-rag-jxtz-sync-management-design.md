# 教务通知同步 — 小程序端管理（开关 / 定时时间 / 手动同步 / 结果展示）设计

> 版本: 1.2 · 日期: 2026-09-11 · 状态: **已实现并部署**（2026-09-11，按 §10；cron job `a75cd2bad6f3`，systemd timer 已停用）
> 分支: main（前端改动在另一个仓，见 §5 / §11）
> 关联: `som-rag-knowledge-base-delete-design.md`（同批功能）、`som-rag-knowledge-base-design.md`（§5 入库管线）
> 已拍板前提: ① 调度迁到 **Hermes 内置 cron（单轨）**，停用 systemd timer；② 小程序点「立即同步」**同步等待结果**返回。
> 变更: 1.1 按设计评审修订（**M1–M5 + L1–L14 全采纳**）：
> **M1** 失败路径必须写运行记录——`sys.exit(1)`（`sync_jxtz.py:316-319`）与未捕获的初始化异常（`:345-349`）都要收进 `try/except BaseException`（只写 `except Exception` **抓不住 `SystemExit`**）；`sys.exit(1)` 改 `return 1`，`__main__` 统一 `sys.exit(main())`（§4.1）；
> **M2** 部分失败不得显示成成功——运行记录改**四态** `no_new` / `ok` / `partial` / `failed`（`skip` 计入成功），端点与前端主数字改用**实际入库数**（09-10 现网实证：2 条新增 / 1 成功 1 失败 / **退出码 0**，按原口径会显示「新增 2 条」）（§4.1、§4.2、§5）；
> **M3** 「被 `flock` 拒绝」与「跑完但无新增」必须可区分——采纳其目标，实现取**字节偏移法**：spawn 前记文件大小，返回后只读新增部分，**无新记录即「本次未执行」**（其覆盖范围大于 `exit 75`，且同时挡住「进程异常死亡导致读到上一轮记录」）（§4.2）；
> **M4** 运行记录路径由 `$AGENT4SOM_REPO` 锚定（`adapter.py:2514-2519` 先例），**不得用 `__file__`**——适配器跑的是部署副本，其目录下没有 `data/`（§4.2、§6.6）；
> **M5** `pause` **不清空** `next_run_at`（冻结旧值）、paused 时 `update_job` **不重算** → 端点统一规范化为 `null`（§4.2、§7、§9）；
> **L1–L14** 见 §9「评审采纳」；外加 3 处评审之外的事实修正：L8 的 `finally` 清理对 SIGKILL **无效**（`subprocess.run(timeout=)` 走 `Popen.kill()`）、`.tick.lock` 的真实持有点是 `cron/scheduler.py:3528-3536`（`:554-558` 只是路径函数）、grace 窗口只折叠**累积槽位**（越界仍执行一次）。全文行号按**符号名**重同步（附录 A）。
> 变更: 1.2 按设计复核修订（**R1–R9 全采纳**，另含 1 处评审外修正）：
> **R1** 弃用 v1.1 的**字节偏移法**（`size0` 快照与「属于本次运行」之间没有绑定，会读到别的运行的记录），改 **`--run-id` 令牌精确匹配**：端点生成 UUID 随参数传入 → 脚本写进记录 → 端点只认 `run_id` 相符的那条；同时把「`flock` 拒绝」写成**唯一不写运行记录**的终止路径，并把「锁必须在读账本之前获取、持有到记录落盘之后」写死（§4.1、§4.2 第 3 点、§4.3、§6.4）；
> **R2** 600s 超时走 SIGKILL，`BaseException`/`finally` 都拦不住 → 该路径原本**完全不落记录**。改为由**端点补写一条合成记录**（`status="failed"`、`error="手动同步超时（600s，结果未知）"`），§4.1 的「任何终止路径」同时收窄为「脚本自身能执行到的终止路径」（§4.1、§4.2 第 3 点、§8 风险 4）；
> **R3** `_find_jxtz_job()` 不再调 `resolve_job_ref`（对同名**直接抛 `AmbiguousJobReference`**，`cron/jobs.py:1252`，根本走不到「取最早」）：就地过滤出 `id`，`pause`/`resume`/`update` 一律传 **id**（§4.2 第 1 点）；
> **R4** `error` 摘要不写整段 `str(exc)`（Chroma/嵌入异常常带 token 连接串）：只留**异常类型 + 截断 200 字**，初始化类异常给固定文案（§4.1、§4.3）；
> **R5 + R9** `next_run_at` 是带时区的 `isoformat()`（`2026-09-10T04:00:00+08:00`）→ 示例、端点契约与前端一律按 **ISO 解析 + 标注时区**；`schedule.kind != "cron"` 时 `schedule_time` **容错为 `null`** 而非 500（§4.2 第 2 点、§5、§7）；
> **R6** `hermes cron create` 仍是手工步骤（`jobs.json` 不在码里），机器重建后定时任务消失 → §10 固化命令并显式声明该限制，**不引入 bootstrap 框架**（§10、§11）；
> **R7** 定时路径 `partial`（退出码 0）在 cron 侧记 `last_status="ok"` → 显式声明「**cron 状态不反映 `partial`**」（§6.5、§9）；
> **R8** 包装脚本改 **`os.execv`** 替换自身进程（cron 3600s SIGKILL 只杀单个 pid，`subprocess.run` 会把真脚本遗留成持锁孤儿）——无孤儿、退出码自然透传（§3 D3、§4.3、§8）；
> 评审外修正：§6.5 原称「cron 侧包装脚本不捕获 stdout」**有误**——`_run_job_script` 用 `capture_output=True`（`cron/scheduler.py:2099-2107`）且经 `redact_sensitive_text` 脱敏（`:2113-2115`）后写 cron output；真实差异是「cron 侧脱敏、手动侧原样进网关 journal」（§6.5）。

## 目录

1. [概述](#1-概述)
2. [现状（代码级事实）](#2-现状代码级事实)
3. [关键设计决策](#3-关键设计决策)
4. [后端设计](#4-后端设计)
5. [前端设计](#5-前端设计)
6. [一致性与边界](#6-一致性与边界)
7. [验证方案](#7-验证方案)
8. [风险与回滚](#8-风险与回滚)
9. [待拍板](#9-待拍板)
10. [实施顺序](#10-实施顺序)
11. [模块索引](#11-模块索引)
12. [附录 A：引用速查（符号名优先）](#附录-a引用速查符号名优先)

---

## 1. 概述

### 1.1 现状

教务处「教学通知」的每日同步目前**完全在服务端、由 systemd 驱动**，管理员看不到也管不了：

- `jxtz-sync.timer` 每天 04:00 触发 `jxtz-sync.service`，后者以 `oneshot` 方式执行
  `/home/<DEPLOY_USER>/H-agent/agent4som/venv/bin/python scripts/sync_jxtz.py`（WorkingDirectory = agent4som）。
  > 依据：`/etc/systemd/system/jxtz-sync.timer`（`OnCalendar=04:00:00`、`Persistent=true`）、`/etc/systemd/system/jxtz-sync.service`。实测（2026-09-11）：`enabled` + `active`，下次 2026-09-12 04:00。
- 运行结果只落两处：`data/jxtz_sync.log`（人类可读文本，>5MB 轮转）和 systemd journal。小程序端没有任何入口。
  > 依据：`scripts/sync_jxtz.py:24-27`（`SYNC_LOG` 常量）、`:40-48`（`log()` 同时 print 与追加写）。
- 开关、时间、手动触发都不存在——要改时间只能编辑 `/etc/systemd/system/jxtz-sync.timer` 再 `daemon-reload`。
- **失败对管理员不可见**，且**部分失败会被当成成功**：2026-09-10 那一轮「2 条新增 → 1 条入库、1 条 404」，日志写「同步完成: 1 成功, 1 失败, 0 跳过 (共 2 条新增)」而退出码为 0；404 那条被写入账本防重试（`sync_jxtz.py:369-373`），09-11 起日志只剩「无新通知，跳过」——**这次失败再也不会被提起**。
  > 依据：`data/jxtz_sync.log`（09-10、09-11 两轮）+ `sync_jxtz.py:369-373`、`:424`；退出码事实见 §2.1 最后一行。

> **现状更新**：现行由 hermes cron 任务承担（`a75cd2bad6f3`），systemd `jxtz-sync.timer` 已停用；本节其余内容为迁移前的事实记录。

### 1.2 目标

小程序端新增「同步管理」页（**仅 admin / owner**），提供四件事：

1. **开关**：关闭后到点不再抓取；重新开启后按原时间恢复。
2. **每日时间**：管理员设置 `HH:MM`，下一次按新时间运行。
3. **立即同步**：手动触发一次并**同步等待**结果返回（入库 N 条 / 无新增 / 失败原因）。
4. **结果展示**：显示最近若干次运行的时间、**实际入库条数**、未入库条数与错误摘要；**失败（含部分失败）必须作为失败呈现**，这是本功能存在的首要理由。

### 1.3 非目标

- 不做首次全量补录（那是 `scripts/batch_ingest_jxtz.py` 的职责，运行时长以小时计）。
- 不改抓取逻辑本身（WAF 挑战、正文解析、入库路径全部沿用 `scripts/sync_jxtz.py`）。
- 不把抓取搬到小程序端：小程序不是常驻进程，定时任务只能在服务端执行；前端只是管理入口。
- 不做多套调度并存（已拍板单轨，见 D1）。
- 不做失败推送（§9 #5）、不做 cron 原始日志入口（§9 #3）。

---

## 2. 现状（代码级事实）

### 2.1 同步脚本（`scripts/sync_jxtz.py`，428 行）

| 事实 | 依据 |
|---|---|
| 入口 `main()`；抓取失败 `sys.exit(1)`；`__main__` 直接调用、**不传退出码** | `:282`、`:316-319`、`:427-428` |
| 抓取首页（WAF 挑战 + 退避重试 3 次） | `:304-315` |
| 与本地 URL 账本比对，仅处理新增；无新增即 `return` | `:322-326` |
| 入库管线初始化（`get_chroma_client()` / `build_embedding_function()` / `create_ingestion_orchestrator()`）**无 try/except** | `:345-349` |
| 正文写临时文件 → 入库 → `unlink`（**无持久副本**）；异常路径各自 `unlink` | `:386-391`、`:406` |
| 逐条状态归类：`ingested`/`replaced` → `ok`；`skipped` → `skip`；其余 → `fail` | `:393-404` |
| 404 等**永久失败**会被写进账本「防无限重试」→ 次日不再提起 | `:369-373` |
| 账本 `data/jxtz_notices.jsonl` 用「临时文件 + 原子替换」写在开头 | `:411-421` |
| 脚本**自己加载 `.env`**，且按文件位置解析（与 cwd 无关） | `:338-339`、`knowledge_base/bootstrap.py:35-49` |
| 入库走 `orch.ingest_file(USER_ID, …, scope=SCOPE, source="jxtz")`（`SCOPE="global"`、`USER_ID="admin"` 在 `:32-33`） | `:32-33`、`:390` |
| 抓取窗口：`fetch_latest_notices(max_pages=3)`，每页约 10 条 → **约 30 条** | `:171-172` |
| **退出码语义**：除 `sys.exit(1)` 外没有任何失败出口 → **「有新增但部分失败」= 退出码 0**（09-10 实证） | `:282-428`（无失败出口）、§1.1 |

**实测耗时**（`data/jxtz_sync.log`）：无新通知 ~1s（09-06/09-07 04:00:04 结束）；有新通知 3–5s（09-08、09-09 均 04:00:04 → 04:00:08）。→ 手动同步可以「同步等待」。

### 2.2 Hermes 内置 cron 子系统（本次复用的机制）

| 能力 | 依据 |
|---|---|
| job 存 `~/.hermes/cron/jobs.json`，输出存 `~/.hermes/cron/output/{job_id}/{时间戳}.md` | `cron/jobs.py:1-6` |
| job 记录含 `next_run_at` / `last_run_at` / `last_status` / `last_error` / `deliver` | `cron/jobs.py:1186-1196` |
| `no_agent=True` + `script` → **脚本即 job，不调用 LLM**，stdout 直接投递；LLM 路径在其后 | `cron/scheduler.py`（`_run_job_script` :2013；`if job.get("no_agent")` :2524，分支体到 :2607，`from run_agent import AIAgent` :2615） |
| 脚本必须是 `~/.hermes/scripts/` 内的**相对路径**（绝对路径 / `~` 在 API 边界直接拒绝，`:546`），目录自动创建 | `tools/cronjob_tools.py:528`（`_validate_cron_script_path`）、`:557`；执行侧 `cron/scheduler.py:2045` |
| 脚本解释器固定为 `sys.executable`，cwd = 脚本目录，env 经 `_sanitize_subprocess_env` 清洗 | `cron/scheduler.py:2093`、`:2104`、`:2105`；清洗实现 `tools/environments/local.py:354`（`:366-368` 剥离内部密钥、`:385` 可能重写 `HOME`、`:391-392` pop `VIRTUAL_ENV`/`CONDA_PREFIX`） |
| 脚本超时默认 3600s | `cron/scheduler.py:1975`（`_DEFAULT_SCRIPT_TIMEOUT`） |
| 错过的周期：grace 窗口内直接补跑；**越过 grace 只折叠累积槽位、仍会立即执行一次**（`next_run_at` 由完成时刻重锚） | `cron/jobs.py:1942-1976`（fall-through 到 `due.append`）；grace = 周期一半、钳在 120s–7200s（`:630-650`）→ **日任务 = 2h** |
| 暂停 / 恢复 / 手动立即执行（手动路径带 at-most-once claim） | `tools/cronjob_tools.py:828-834`（pause/resume）、`:604-644`（`_execute_job_now`，claim 在 `:625`） |
| **ticker 在网关进程内**（60s 一轮）：真实实现是 `interval=60` 的循环，由网关以 `daemon=True` 线程启动 | `cron/scheduler_provider.py:166-194`（`while not stop_event.is_set()` / `stop_event.wait(interval)`）、`gateway/run.py:20791-20801`（`threading.Thread(..., daemon=True, name="cron-scheduler")`）。**注**：`cron/scheduler.py:294-327` 是并行线程池状态 + `get_running_job_ids()`、`hermes_cli/cron.py` 是一次性 `cron tick` CLI，二者**都不是** ticker |
| 任务失败判定：`no_agent` 脚本模式下 `last_status` **完全由退出码决定** | `cron/scheduler.py:2121-2129`（`if result.returncode != 0: … return False`）→ `cron/jobs.py:1465`（`job["last_status"] = "ok" if success else "error"`） |
| `deliver` 默认 `local`（条件：无 origin）——CLI 创建无 session origin ⇒ `local` | `cron/jobs.py:1089-1091` |
| 所需 API 签名：`list_jobs(include_disabled=False)` `:1258`、`pause_job(job_id, reason=None)` `:1367`、`resume_job(job_id)` `:1383`（三者均经 `resolve_job_ref` `:1233` **接受 id 或 name**）、`update_job(job_id, updates)` `:1266`（**只吃 id**） | `cron/jobs.py` |

### 2.3 运行环境与沙箱

| 事实 | 依据 |
|---|---|
| 网关 WorkingDirectory = `agent4som`，`EnvironmentFile` = `agent4som-hermesagent/.env` | `/etc/systemd/system/hermes-gateway@.service` |
| 该 unit 是**模板 unit**（`%i`），注释明写「通过 systemd 模板 + nginx 实现水平扩展」——**将来可能多实例** | 同上（`Description=Hermes Gateway Worker %i`） |
| `HERMES_HOME` = `~/.hermes` → 软链到 `agent4som-hermesagent`（**那是一个 git 仓**） | `ls -ld /home/<DEPLOY_USER>/.hermes`；`git -C /home/<DEPLOY_USER>/H-agent/agent4som-hermesagent rev-parse --is-inside-work-tree` |
| 网关可写范围仅 `agent4som/data` 与 `~/.hermes`；`ProtectHome=read-only`、`PrivateTmp=yes`、`RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX` | `/etc/systemd/system/hermes-gateway@.service`（两条 `ReadWritePaths`） |
| 两份 `.env` 的 Chroma 配置一致（`127.0.0.1:8007`）；且 25 个共有键**逐键相同**（含密钥，以哈希前缀比对） | `agent4som/.env:17-18`、`agent4som-hermesagent/.env`（021 评审 §0 实测） |
| 网关用 HTTP 模式访问 Chroma，**显式配置时永不回退嵌入式** | `knowledge_base/repository/chroma_repository.py:30-99`（`get_chroma_client`） |
| 适配器**跑的是部署副本**：`/home/<DEPLOY_USER>/H-agent/agent4som-hermesagent/plugins/miniapp-platform/adapter.py`（与源码 md5 一致），**该目录下只有 `adapter.py` / `__init__.py` / `plugin.yaml` / `__pycache__`，没有 `data/`** | 实测 2026-09-11 |

### 2.4 可复用的既有模式

- 管理员门禁：`self._require_role(openid, {"admin", "owner"})`（共 **14 处**，代表 `adapter.py:885`、`:1041`、`:1077`；其余 `:911`/`:1057`/`:1098`/`:1114`/`:1134`/`:1155`/`:1894`/`:1919`/`:1952`/`:1976`/`:2010`）。
- 离开事件循环的先例：`await asyncio.to_thread(...)`（`adapter.py:377`；另有 `:242`、`:1739` 等）。
- 管理页模板：`miniprogram-framework-frontend/pages/phone-whitelist/`（列表 + 二次确认 + 批量操作）。
- 共享页清单与构建校验：`scripts/build.js` 的 `SHARED_PAGES`（`:21-33`，最近新增的条目**带行尾注释**）、`validate.js` 的存在性校验（`:162-168`）与零硬编码校验（`:114-129`）。**`app.json` 是 `build.js:178-203` 的生成产物且被 `.gitignore:11` 忽略，不要手改**。
- 小程序入口卡：`pages/methods/methods.wxml:39-46`（「入库知识」卡）与 `:48-55`（「知识库管理」卡）为样式先例；管理员段本身已在 `:112` 做了**段级** `wx:if="{{role === 'admin' || role === 'owner'}}"`，新卡加进段内即可、不需要卡级守卫。方法体先例：`pages/methods/methods.js:152`（`tapKnowledgeManage`）、`:154-224`（`tapIngestKnowledge`）。
- 共享页文案硬约束：文本必须走 `UI_TEXT`（`build.js:61-67` 定义 `DEFAULT_UI_TEXT`、`:101` 合并进生成的 `config/instance.js`），违反会被 `validate.js:114-129` 拦下。

---

## 3. 关键设计决策

| # | 决策 | 理由（依据） |
|---|---|---|
| D1 | 调度 = **Hermes cron 单轨**；一次性 `sudo systemctl disable --now jxtz-sync.timer` | 四项需求全部有现成机制（§2.2）；不自己写调度器、不改 systemd 排期 |
| D2 | job 形态：`no_agent=True` + `script="jxtz_sync.py"` + `deliver="local"` + `name="jxtz-sync"` | 脚本即 job，**不烧 token**；`deliver` 默认即 `local`（无 session origin，`cron/jobs.py:1089-1091`），不推送到任何聊天 |
| D3 | 新增包装脚本 `~/.hermes/scripts/jxtz_sync.py`（约 8 行）：用 agent4som venv 解释器拉起真脚本（`cwd=agent4som`），**用 `os.execv` 替换自身进程**而非 `subprocess.run`——退出码天然透传，且不留孤儿 | cron 只允许脚本在 `~/.hermes/scripts/` 且解释器固定为网关 venv（§2.2），真脚本依赖 agent4som 的包与 venv。**`subprocess.run` 必须弃用**：cron 的 3600s 超时只对**包装脚本这单个 pid** 发 SIGKILL（`cron/scheduler.py:2099-2107`，无 `start_new_session`/`killpg`），真脚本会变孤儿继续持 `flock`，此后定时运行全部静默 `exit 0` 直到孤儿结束（022 R8） |
| D4 | **手动同步不走 `cron run`**，由 adapter 直接子进程执行真脚本并同步等待结果 | ① 关闭定时后仍能手动同步（`claim_job_for_fire` 对 paused job 直接拒绝，`cron/jobs.py:1678-1679`）；② 手动结果需要「实际入库条数」，`cron run` 的返回值只有 `success`，拿不到明细 |
| D5 | 开关 = job 的 `enabled` / `state=paused`（`pause`/`resume`） | 现成语义；`resume_job` 会按「现在」重算 `next_run_at`（`:1389`）。**注意 `pause_job` 不重算也不清空 `next_run_at`（冻结旧值，`:1367-1379`）**，展示层必须规范化（§4.2） |
| D6 | 每日时间 = job 的 cron 表达式；**前端只传 `HH:MM`**，服务端校验后拼装 `M H * * *` | `update_job(schedule=…)` 现成（`:1266`）；服务端拼装 + 锚定正则 = 无注入面（前端不得传表达式） |
| D7 | 结果 = 脚本追加写 `data/jxtz_sync_runs.jsonl`（每行一次运行，结构化，**不截断**），cron output 原文作为原始日志保留 | 前端解析 Markdown 不稳；JSONL 同时服务「最近一次结果」与「最近 N 次历史」；不截断避免「读全量 → 截断 → 写回」的非原子窗口（50 行 ≈ 15 KB/年，增长可忽略） |
| D8 | 权限 = admin / owner，复用 `_require_role`；前端仅对管理员显示入口 | 与白名单、角色管理一致（§2.4） |
| D9 | 定时与手动互斥 = 脚本内 `flock`（`data/jxtz_sync.lock`，非阻塞）。**理由（勿删）**：① D4 绕开了 cron 的 claim，账本读-改-写非原子（`sync_jxtz.py:411-421`），必须由脚本自身保证不并发；② cron 的 at-most-once **本身也不硬**——ticker 派发只查进程内 `_running_job_ids`（`cron/scheduler.py:3627-3632`），手动 `cron run` 走 `claim_job_for_fire` 取 `.jobs.lock`（`cron/jobs.py:1673`、`:195-197`），而 tick 期间持的是 `.tick.lock`（`cron/scheduler.py:3528-3536`）——**两把锁互不检查**，手动与 ticker 交叠会 double fire；③ 网关 unit 是模板 unit，一旦水平扩到多实例，flock 是「同一时刻只有一次同步」的唯一保证。锁放 `data/`（非 `/tmp`）是因为网关 `PrivateTmp=yes`，`/tmp` 是 service 私有的 | 内核锁随进程退出自动释放（含 SIGKILL），**无常驻 stale 锁** |

---

## 4. 后端设计

### 4.1 运行记录文件（新增，脚本侧）

`data/jxtz_sync_runs.jsonl`，每次运行**追加一行，不截断**：

```json
{"run_id": "5f3c1a7e-…", "run_at": "2026-09-10T04:00:08+08:00", "trigger": "schedule", "by": "",
 "status": "partial", "new": 2, "ok": 1, "fail": 1, "skip": 0,
 "error": "页面不存在 (HTTP 404) https://jwc.xjtu.edu.cn/info/1102/10444.htm",
 "items": [{"title": "关于开展…通知", "url": "https://jwc.xjtu.edu.cn/…",
            "status": "ingested", "nodes": 5}]}
```

- `run_id`：**手动路径的唯一认领凭据**（022 R1）。端点 spawn 前生成 UUID 并传 `--run-id <uuid>`，脚本原样写进记录；端点返回后只在新增字节里找 `run_id` 相符的那条。定时路径由脚本自行生成（包装脚本不传）。
- `run_at` 用 `datetime.now().astimezone().isoformat()`（本地墙钟 + 时区偏移），与 cron 的 `next_run_at` 格式一致（R5）。**两种时间都在同一次运行的同一台机器上产出，前端按 ISO 解析即可**。
- `trigger` 由命令行参数决定：手动同步传 `--trigger manual`，cron 包装脚本不传（默认 `schedule`）。
- `by`：手动路径传 `--by <openid>`（端点已知调用者，`adapter.py:2177-2183`）；定时路径留空。仅用于事后追查「谁点的立即同步」。
- `items[].status` 用 **`result.status.value` 原值**（`IngestionStatus`：`ingested` / `skipped` / `replaced` / `quota_exceeded` / `unsupported_type` / `file_not_found` / `error`，`knowledge_base/ingestion/orchestrator.py:58-67`），不要自造 `"ok"` 之类的值。
- **`status` 四态**（`skip` 计入成功，否则「全部已在库」既落不进 `ok` 也落不进 `failed`）：

  | 取值 | 判定 | 退出码 |
  |---|---|---|
  | `no_new` | `new == 0` | 0 |
  | `ok` | `fail == 0` 且 `new > 0` | 0 |
  | `partial` | `fail > 0` 且 `ok + skip > 0` | 0 |
  | `failed` | `fail > 0` 且 `ok + skip == 0`（含抓取失败、初始化异常、全部入库失败） | 1 |

- **写入时机：脚本自身能执行到的终止路径都要写**（成功 / 无新增 / 部分失败 / 整轮失败）。**且只有一个例外**：**持锁失败（`flock` 拿不到）不写运行记录**——这条必须写死，否则每次 409 都会新增一条记录，端点的「找不到本次 `run_id` ⇒ 未执行」判定会永久失效（022 R1 问题二）。另外，进程被**外部 SIGKILL**（端点 600s 超时、cron 3600s 超时）时脚本没有任何执行机会，本就不在「能执行到的路径」范围内——超时那条由**端点补写合成记录**（§4.2 第 3 点），不是脚本写。
  实现要求：
  1. `main()` 主体包 `try/except BaseException`，在 `except` 里写 `status=failed` + `error` 摘要后 `return 1`；正常路径在末尾写记录后 `return 0`；
  2. 去掉 `sys.exit(1)`（`:319`）改为 `return 1`，`__main__` 改为 `sys.exit(main())`；
  3. **只写 `except Exception` 抓不住 `SystemExit`**——要么清掉所有 `sys.exit`，要么显式用 `BaseException`；二者必须选一个，否则修复看起来做了、实际没生效；
  4. **锁的生命周期**：`flock` 必须在**读取账本之前**获取，并持有到**运行记录写盘之后**——这既是账本读-改-写的互斥前提（D9），也是端点「找不到本次 `run_id` ⇒ 本次未执行」这个判定成立的前提（022 R1 附注：读记录可能读到别人正在写的行）；
  5. `failed` 时 `error` 取异常/失败原因摘要（如「未获取到任何通知（WAF / 页面结构 / 网络）」），但**不得直写整段 `str(exc)`**（022 R4）：Chroma / 嵌入服务的异常常带 token 连接串或完整 URL，会落进 JSONL 并回显到管理页。取 **`type(exc).__name__` + 截断到 200 字**的消息；初始化/连接类异常优先给**固定文案**（如「入库管线初始化失败」），不拼原始异常文本。
- 结尾摘要行（`:424`）保持原样，便于与日志对照。

### 4.2 端点（adapter 新增 4 条路由，均 admin/owner）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/methods/jxtz-sync` | — | `{"success":true,"enabled":true,"schedule_time":"04:00","next_run_at":"2026-09-10T04:00:00+08:00","last_run":{…}, "recent_runs":[…最近 5 条…]}` |
| POST | `/api/methods/jxtz-sync/toggle` | `{"enabled":false}` | `{"success":true,"enabled":false,"next_run_at":null}` |
| POST | `/api/methods/jxtz-sync/schedule` | `{"time":"05:30"}` | `{"success":true,"schedule_time":"05:30","next_run_at":"2026-09-10T05:30:00+08:00"}` |
| POST | `/api/methods/jxtz-sync/run` | — | `{"success":true,"result":{…本次运行记录…}}` |

实现要点：

1. **找 job**：按 `name == "jxtz-sync"` 从 `list_jobs(include_disabled=True)` 中取；**多于一条**时记 WARNING 并取 `next_run_at` 最早的一条，**直接从该 job 取 `id`**；一条都没有 → 404 `{"error":"job_not_found"}`（job 由部署时一次性 CLI 创建，见 §10；不在请求路径里自动创建）。**`_find_jxtz_job()` 不得调用 `resolve_job_ref`**（022 R3）：它对同名**直接抛 `AmbiguousJobReference`**（`cron/jobs.py:1252`），根本走不到「取最早」；拿到 `id` 后，`pause_job` / `resume_job` / `update_job` **一律传 `id`**（`update_job` 本就只吃 id，另两个虽接受 name 但传 name 会二次解析、再次踩到歧义）。
2. **开关 / 时间**：分别调用 `pause_job` / `resume_job` / `update_job(schedule=…)`（`cron/jobs.py`，均传 id）；`HH:MM` 用 `^([01]\d|2[0-3]):[0-5]\d$` 校验后**由服务端**拼成 `M H * * *`。两个契约细节：
   - `next_run_at` **统一规范化**：`next_run_at = None if job.get("state") == "paused" else job.get("next_run_at")`——`pause` 不会清空该字段（冻结旧值），且 paused 时 `update_job` 不重算（`:1316`），不规范化就会出现「已关闭 + 下次运行 2026-09-12 04:00」的自相矛盾。
   - 该字段是 **`datetime.isoformat()` 原样字符串**（`cron/jobs.py:664`，形如 `2026-09-10T04:00:00+08:00`），**不是**设计早期示例里的空格格式（022 R5）：端点原样透传、**不重格式化**，前端按 ISO 解析（§5）。
   - `schedule_time` 由 `job["schedule"]["expr"]` 反解 `HH:MM`；**必须容错 `schedule.kind != "cron"`**（人工改过或历史 job）：反解失败时返回 `null`（页面显示「未知」），**不返回 500**（022 R9）。
3. **手动同步**：`asyncio.to_thread` 中执行
   `subprocess.run([agent4som/venv/bin/python, scripts/sync_jxtz.py, "--trigger", "manual", "--by", openid, "--run-id", run_id], cwd=agent4som, timeout=600)`（先例 `adapter.py:377`）。参数全部由服务端拼装，不经 shell，无注入面。
   - **本次结果按 `run_id` 认领**（022 R1）：`run_id = uuid4()` 在 spawn 前生成，随参数传入；返回后**从文件末尾向前扫描**，找 `run_id` 相符的那条记录当本次结果，逐行解析、**跳过坏行**（只扫末尾若干行即可，记录是追加写的）。这比 v1.1 的「字节偏移法」（`size0` 快照）多一层绑定——`size0` 只界定了「可能的新增区间」，区间里仍可能是**别人**（持锁中的定时运行）刚写的记录；`run_id` 才是「这条属于本次调用」的判据：

     | 情形 | 退出码 | `run_id` 记录 | 端点行为 |
     |---|---|---|---|
     | 跑完（`ok` / `partial` / `no_new`） | 0 | 有 | 按记录 `status` 出结果 |
     | 跑完但整轮失败（抓取 / 初始化 / 全失败） | 1 | 有（`status=failed`） | 500 `{"error":"sync_failed","detail":…}` |
     | 被 `flock` 拒绝（已有同步在运行） | 0 | **无**（这是唯一不写记录的终止路径） | 409 `{"error":"already_running"}` |
     | 子进程异常死亡（未写记录、非 0） | ≠0 | 无 | 500 `sync_failed`（**不读旧记录**） |
     | 端点 600s 超时（SIGKILL） | — | 无 | **504** `{"error":"sync_timeout"}` + **端点补写合成记录**（下条） |

   - 超时用 `TimeoutExpired` 判定；不要对超时后再去读末行。
   - **超时补写合成记录**（022 R2）：SIGKILL 下脚本没机会落库，若端点也不写，「失败必须可见」就在唯一强制中断路径上失效。端点在 `TimeoutExpired` 分支自行 `append` 一行 `{"run_id": run_id, "run_at": now, "trigger": "manual", "by": openid, "status": "failed", "new": 0, "ok": 0, "fail": 0, "skip": 0, "error": "手动同步超时（600s，结果未知）", "items": []}`（约 5 行，复用 `_jxtz_runs_path()`）。**`error` 明写「结果未知」**——进程可能在补写后仍在跑，不能谎报「失败」以外的结论。cron 定时路径的 3600s 超时**不做**同样处理（R8 的 `os.execv` 已消除孤儿问题，且 cron output 本身有记录）。
   - 注：600s 可能短于脚本最坏重试链（`MINERU_RETRIES=10 × MINERU_POLL_TIMEOUT=300`），即「慢但合法」的运行也会走这条路径——这正是必须补写记录的原因（§8 风险 4）。
4. **读取结果（GET）**：路径由 **`$AGENT4SOM_REPO` 锚定**，照抄 `_db_path` 先例（`adapter.py:2514-2519`）：
   ```python
   repo = os.getenv("AGENT4SOM_REPO", "").strip()
   return Path(repo or Path.home()) / "data/jxtz_sync_runs.jsonl"
   ```
   **不得用 `__file__`**（部署副本目录下没有 `data/`，会永远读到空且静默）；`AGENT4SOM_REPO=/home/<DEPLOY_USER>/H-agent/agent4som` 已在 `agent4som-hermesagent/.env` 设置。`last_run` 取最后一条可解析记录，`recent_runs` 取末尾 N 条；全部坏行/文件不存在 → 空（页面显示「暂无运行记录」，不抛错）。
5. **鉴权**：`_authorize`（`:2177-2183`，无 token 401）→ `_require_role(openid, {"admin","owner"})`（`:420`，非管理员 403），与白名单端点同形。
6. **两套响应约定并存**：框架级错误抛 `web.HTTPxxx(text=json.dumps({"error":…}))`（401 `:2182`、403 `:426-430`、400 `:855`），业务级走 `{"success": true/false}`（`:922`、`:1038`）。**前端必须按 `statusCode` 判定**：401/403/404/409/500/504 的 body 里没有 `success` 字段。

### 4.3 改动清单（最小）

| 文件 | 仓 | 改动 |
|---|---|---|
| `scripts/sync_jxtz.py` | agent4som | + `--trigger {schedule,manual}`（默认 `schedule`）、`--by`、`--run-id`（手动路径由端点传入，定时路径自生成）；+ `flock` 非阻塞（**读账本前获取、写记录后释放**；拿不到锁 → 打印「已有同步在运行」并 `exit 0`，**且不写运行记录**）；+ 运行记录 JSONL（四态、失败路径也写、`error` 截断 200 字、不截断）；+ `main()` 收口为 `return 0/1` |
| `~/.hermes/scripts/jxtz_sync.py` | **agent4som-hermesagent**（`~/.hermes` 软链，该目录未被其 `.gitignore` 覆盖） | **新建**（部署产物，目录由 cron 自动创建）。包装脚本：`os.execv(agent4som/venv/bin/python, [python, agent4som/scripts/sync_jxtz.py])`（**先 `os.chdir(agent4som)`**）——不用 `subprocess.run`，避免 cron 超时后真脚本变孤儿持锁（022 R8）。建议纳入该仓提交，避免「机器重建后脚本丢失」（job 本身仍需重建，见 §10） |
| `~/.hermes/plugins/miniapp-platform/adapter.py` | agent4som | + 4 条路由（**插在 `/api/methods/*` 群末尾、`:162` 之后**）+ `_method_jxtz_sync_get` / `_toggle` / `_schedule` / `_run` + `_find_jxtz_job()` + `_jxtz_runs_path()` |

---

## 5. 前端设计

**仓归属：前端在独立仓 `/home/<DEPLOY_USER>/H-agent/miniprogram-framework-frontend`（分支 `master`）**——本节与 §10/§11 的文件路径均相对该仓，裸写 `scripts/build.js` 指的是前端仓的，与 `agent4som/scripts/`（含 `sync_jxtz.py`）**无关**。

新建 `pages/jxtz-sync/`（`jxtz-sync.{js,json,wxml,wxss}`，以 `pages/phone-whitelist/` 为模板；命名取后端同名 `jxtz`，不引入 `kb-sync` 这套新前缀）。

- **入口**：`pages/methods/` 的「管理员功能」节（`:112` 段级守卫内，`:113` 标题之后）新增「同步管理」卡，样式照 `:48-55` 的知识库管理卡。
- **页面结构**：
  - 状态卡：`定时同步：已开启 / 已关闭`、`每日时间：04:00`（**`null` 时显示「未知」**，见 §4.2 第 2 点 R9）、`下次运行：09-10 04:00（北京时间）`（**已关闭时不显示「下次运行」**——后端已把它规范化为 `null`）。`next_run_at` 是带时区的 ISO 串（`2026-09-10T04:00:00+08:00`），**必须 `new Date()` 解析后再格式化**，不得字符串截取/拼接（022 R5）；页面标注时区，避免日后改服务器时区时误读
  - 开关：`switch` 组件 → 调 `/toggle`，失败回滚 UI 状态
  - 时间：点击弹出时间选择（`picker mode="time"`）→ 调 `/schedule`，成功 toast + 显示新的「下次运行」
  - 手动：「立即同步」按钮 → 调 `/run`，按钮置 loading 文案「正在同步…」，最长等 60s；**文案按 `result.status` 与 `ok`/`fail` 出**：`no_new` →「无新增」；`ok` →「新增 N 条」（N = `ok`，`skip>0` 时附「M 条已在库」）；`partial` →「M 条未入库」红字；`failed` →「同步失败：<error>」。409 →「已有同步正在进行」；504 →「同步超时（结果未知）」——端点已补写一条超时记录，刷新后可在「最近运行」看到（§4.2 第 3 点 R2）
  - 最近运行：列表（时间（`run_at`，同样按 ISO 解析）/ 结果 / **实际入库条数** / 未入库条数），失败行显示错误摘要（`error` 已由脚本侧截断，前端不再二次加工）
- **文案约束**：① 不出现 `cron`、`job`、`systemd`、`scope` 等术语；② 文本一律走 `UI_TEXT`（`validate.js:114-129` 强制共享代码零硬编码，不得出现「招生老师」「mba_admission」「INSTANCE_ID」分支），违反会被 `npm test` 拦下。
- **要动的文件**（均在**前端仓**）：`pages/jxtz-sync/jxtz-sync.{js,json,wxml,wxss}`（新建）、`config/api.js`（+ `METHODS_JXTZ_SYNC*` 3–4 条）、`scripts/build.js:21-33` 的 `SHARED_PAGES`（**新增行带一行注释**）、`pages/methods/methods.{wxml,js}`（入口卡 + 跳转）。**`app.json` 不要手改**（`build.js:178-203` 生成、`.gitignore:11` 忽略）。

---

## 6. 一致性与边界

1. **网关宕机期间不调度**：ticker 在网关进程内（§2.2）；恢复后 `get_due_jobs` 会补跑一次（`cron/jobs.py:1942-1976`），不会永久跳过。**限定词**：grace 窗口（日任务 2h）只决定**累积的错过槽位是否折叠**——越过 grace 仍会立即执行一次，只是 `next_run_at` 被重锚；真正的漏抓边界是**抓取窗口**（下条）。这是相对 systemd timer 的**唯一能力差异**（systemd 在网关宕机时仍会跑）。
2. **抓取窗口量化**：`fetch_latest_notices(max_pages=3)` ≈ 30 条（`:171-172`），超出即永久漏抓（账本无法回翻）。实测 `data/jxtz_notices.jsonl`（1214 条 / 729 天）：**最大单日 12 条、中位 1 条、p90 3 条** → 可容忍网关连续不可用约 **2.5 天（最坏）/ 约 1 个月（中位）**。运维说明：长期不可用恢复后应人工核对当天通知。
3. **停用 systemd timer 之前**存在双跑窗口：双跑本身被 URL 账本兜住（第二次「无新通知，跳过」），但账本是读-改-写（`sync_jxtz.py:411-421`），因此 D9 的 `flock` 必须与停用动作同批上线。
4. **`flock` 的拒绝语义**：定时与手动重叠时，**后到者**放弃**且不写运行记录**（stdout 打印「已有同步在运行」）；手动路径由端点的「找不到本次 `run_id` 的记录」判为 409，定时路径静默跳过（`exit 0`，cron 侧记为成功）。「持锁失败不写记录」是**唯一**的不写记录路径（§4.1），必须与端点的 409 判定保持一致——否则每次 409 都会新增记录、409 分支永久不可达（022 R1）。
5. **脚本 stdout 即 cron 输出（但两处口径不同）**：cron 侧 `_run_job_script` 用 `capture_output=True` 捕获 stdout/stderr（`cron/scheduler.py:2099-2107`），**经 `redact_sensitive_text` 脱敏后**才写进 cron output（`:2113-2115`，脱敏失败则整段替换为 `[REDACTED - redaction failed]`；`no_agent` 分支 `:2524-2607`）；**手动路径是 adapter 直调真脚本（D4），不做捕获**，`print` 原样进网关 journal。**差异是「cron 侧脱敏、手动侧不脱敏」**（v1.1 曾误写成「两处都不捕获」）。运行时输出主要是通知标题与 URL，本设计不接受新的敏感输入，故不加额外处理；但排查日志时须知手动侧原文可在 gateway journal 看到。
   **注（022 R7）**：定时路径的 `partial` 退出码为 0，cron 侧因此记 `last_status="ok"`（`cron/scheduler.py:2121-2129` → `cron/jobs.py:1465`）——**cron 状态不反映 `partial`**，只有小程序页面与 JSONL 能看见「N 条未入库」。这是「只靠页面呈现」的已拍板取舍，**不要**为它改退出码（`partial` 改成 1 会把「部分失败」误记成整轮失败）。
6. **配置只落两处**：新文件（`jxtz_sync_runs.jsonl`、`jxtz_sync.lock`）放 `agent4som/data/`；包装脚本放 `~/.hermes/scripts/`。二者都在网关沙箱的可写范围内（§2.3）。**端点读运行记录的路径由 `$AGENT4SOM_REPO` 锚定**（§4.2 第 4 点），不依赖 cwd、不依赖 `__file__`。
7. **`.env` 解析不受 cwd 影响，但两份路径的环境来源不同**：真脚本按文件位置加载 `agent4som/.env`（§2.1），且 `bootstrap.load_dotenv` 用 `os.environ.setdefault`（`bootstrap.py:76`，**不覆盖已有变量**）→ 脚本实际读到的配置取决于「进程环境里已有什么」。定时路径经 `_sanitize_subprocess_env` 清洗（§2.2），手动路径继承网关原始环境（`EnvironmentFile` = `agent4som-hermesagent/.env`）。当前两份 `.env` 的 25 个共有键**完全一致** → 无实际影响；但**任一份单独轮换凭证时会出现「定时对、手动错」**，轮换时两份都要改。
8. **手动同步不改变 `next_run_at`**：它不经 cron 的 claim/fire 路径（D4），因此不会打乱定时节奏——管理员手动跑一次后，当天 04:00 仍会照常运行（届时通常「无新通知」）。

---

## 7. 验证方案

| 层级 | 内容 | 状态 |
|---|---|---|
| 脚本层 | `--trigger manual` / `--by` / `--run-id` 落库正确（`run_id` 原样回写）；连续两次运行只追加两行；`flock` 第二次被拒、`exit 0` **且不新增记录**；**JSONL 只追加、不截断** | 待实施 |
| 脚本层（**R4**） | 构造一个初始化异常（如 `CHROMA_PORT` 指向空端口）→ 记录里的 `error` 为**固定文案/截断文本**，**不含 token、不含完整连接串**（`grep` 一下 `CHROMA_AUTH_TOKEN` 的值确认未出现） | 待实施 |
| 脚本层（**失败路径**，M1/M2 新增） | ① `LIST_URL` 指不可达地址跑一次 → 记录出现 `status=failed` + `error`；② `CHROMA_PORT` 指空端口跑一次 → 同上；③ 构造「1 条成功 + 1 条 404」→ 记录 `status=partial`、`ok=1`、`fail=1`（**退出码仍为 0**），端点按 `partial` 呈现 | 待实施 |
| job 层 | `hermes cron list` 能看到 `jxtz-sync`（`no_agent`）；`pause` 后 **cron 层 `next_run_at` 保留旧值（不置空）、端点层规范化为 `null`**，`resume` 后按新 schedule 重算；`edit --schedule "30 5 * * *"` 生效（paused 状态下改时间不回显新值属预期，端点同样只回 `null`）；返回的 `next_run_at` 是**带时区 ISO 串**（`+08:00`） | 待实施 |
| 端点层 | 4 条路由无 token → 401；student/teacher token → 403；admin → 200；`job_not_found` 分支（临时改名 job 验证）；`HH:MM` 非法值 → 400；同名 job 多于一条 → WARNING + 取最早一条（**不经 `resolve_job_ref`、不抛 `AmbiguousJobReference`**）；把 job 的 `schedule.kind` 伪造成非 `cron` → `schedule_time` 回 `null` 且仍 200（R9） | 待实施 |
| 手动同步 | 点「立即同步」→ 等待返回，结果与 `jxtz_sync_runs.jsonl` 里 `run_id` 相符的那条一致（**多跑几轮确认不会认领到相邻记录**）；定时运行与手动同时触发 → 手动侧拿到 409（**且不返回上一轮结果**）；人为把 `timeout` 调到 1s → 端点返回 **504**，**且 JSONL 新增一条 `status=failed` / `error` 含「结果未知」的合成记录**（R2；022 复核 §4 已列为实施时必须复现的用例） | 待实施 |
| 前端 | `npm test`（共享页清单 + 零硬编码校验）+ 开发者工具过一遍开关 / 改时间 / 立即同步 / 最近运行列表（含 409/504 文案） | 待实施 |
| **端到端（未验证项）** | 带真实 token 的端到端需用户在开发者工具手动走一次（签发 token 需读 `MINIAPP_SESSION_SECRET`，权限策略拦截，不绕过） | 由用户执行 |
| 部署 | 包装脚本落地（用 `os.execv`，**并纳入 `agent4som-hermesagent` 仓提交**）→ `hermes cron create`（命令固化在 §10）→ `sudo systemctl disable --now jxtz-sync.timer` → `cp` adapter 到实盘 + `sudo systemctl restart hermes-gateway@jwc-assistant` → 记录 `ActiveEnterTimestamp`（**没重启不算验证过**，原交接文档已移除）。**此行的前两步不落在任何 git 仓里**（job 存于 `~/.hermes/cron/jobs.json`），机器重建后必须重跑（R6） | 待实施 |

---

## 8. 风险与回滚

| # | 风险 | 缓解 |
|---|---|---|
| 1 | 迁移后网关长期宕机 → 期间不同步 | 恢复后补跑一次（§6.1，grace 只折叠累积槽位、仍执行一次）；漏抓边界 = 抓取窗口约 30 条 ≈ 2.5 天（最坏），长期不可用后人工核对（§6.2）；如需强保障，保留 timer 双轨（本设计已拍板单轨，不做） |
| 2 | `flock` 未生效导致账本并发写 | 与停用 timer 同批上线并验证（§7 脚本层）；账本用原子替换写入（`sync_jxtz.py:411-421`） |
| 3 | cron job 被误删 / **机器重建后 job 消失**（R6：`jobs.json` 不在任何 git 仓里） | 页面显示「同步任务未注册，请联系运维」；恢复只需按 §10 固化的命令重跑一次 `hermes cron create`（不引入幂等 bootstrap，见 §10 注） |
| 4 | 手动同步超时（WAF 重试 / 入库重试链） | 脚本内列表抓取最多重试 3 次、退避 15s×(n−1)（`:304-315`）；端点 600s 超时 → **504**（与 500 区分，前端文案不同）**+ 端点补写合成记录**（`failed` / 「结果未知」，R2），失败不会在历史里消失；前端 60s 后提示「仍在运行，稍后刷新查看结果」。**注**：`subprocess.run(timeout=)` 用 **SIGKILL**，`finally` 不会执行 → 超时后 `data/jxtz_tmp/` 可能残留临时文件（文件名由 URL 派生，重试会覆盖；属可接受残留，不为此加清理机制）；入库重试链本身可远超 600s（`MINERU_RETRIES=10` × `MINERU_POLL_TIMEOUT=300`，`agent4som/.env:38-39`），但正常路径实测仅 3–5s |
| 5 | 时间格式错误导致 job 不再触发 | 服务端强校验 `HH:MM` 并**由服务端拼装**表达式（D6）；写入后用返回值回显规范化后的 `next_run_at`，页面立即显示 |
| 6 | **孤儿同步持锁**：cron 侧 3600s 超时只 SIGKILL 包装脚本，真脚本若仍在跑会一直持有 `flock`，此后每次定时运行都静默 `exit 0` 直到孤儿结束 | D3 改 `os.execv`——包装脚本进程**就是**真脚本，不存在「父子两个 pid」；退出码自然透传。手动侧不适用（`subprocess.run` 的 600s 硬超时由端点自己控制，且 `flock` 在真脚本进程内） |
| 7 | **运行记录里出现凭证** | R4：`error` 只写「异常类型 + 截断 200 字」，初始化/连接类异常用固定文案；§7 增用例 `grep` 确认 token 未落盘 |
| 8 | **回滚** | `sudo systemctl enable --now jxtz-sync.timer` + `hermes cron pause <job_id>`（或 `remove`）；脚本新增参数与 JSONL 向后兼容（`--trigger` 默认 `schedule`，timer 直接拉起真脚本仍可用），无需回退代码 |

---

## 9. 待拍板

| # | 问题 | 建议 |
|---|---|---|
| 1 | 关闭定时后是否仍允许「立即同步」 | **允许**（D4）——关闭的是「自动」，不是「能力」 |
| 2 | 运行记录保留多少条 | **不截断**（约 15 KB/年，增长可忽略；截断会引入读-改-写窗口） |
| 3 | 页面是否提供 cron 原始日志入口 | **暂不做**——JSONL 已覆盖管理员关心的信息；原始日志留在服务器 |
| 4 | 改时间是否二次确认 | **不确认**，但成功后显示新的「下次运行」时间；**已关闭时不显示「下次运行」**（后端已规范化） |
| 5 | 是否把「同步失败」通过微信小程序推送给管理员 | **本次不做**——页面可见即可，避免扩大改动面 |
| 6 | cron 侧 `last_status` 不反映 `partial`（定时路径退出码 0） | **接受**（R7）——只在页面呈现「N 条未入库」；**不改退出码**（改成 1 会把部分失败误记成整轮失败，反而更差）；向运维口头说明时以此为准 |

**评审采纳（2026-09-11，v1.1）**：设计评审的 **M1–M5 + L1–L14 全部采纳**，落点如下。

| 项 | 处置 | 落点 |
|---|---|---|
| M1 失败路径不留痕 | `main()` 收口 `try/except BaseException`，去 `sys.exit(1)` 改 `return 1`，任何终止路径都写记录；§7 补两条失败路径用例（不可达 `LIST_URL` / 空 `CHROMA_PORT`） | §4.1、§4.3、§7 |
| M2 部分失败显示成成功 | 四态 `no_new`/`ok`/`partial`/`failed`（`skip` 计入成功）；端点按记录 `status` 出结果；前端主数字改用 `ok`，`fail>0` 红字「N 条未入库」 | §4.1、§4.2、§5、§7 |
| M3 flock 拒绝无信号 + 读到上一轮 | 采纳目标，实现取**字节偏移法**（spawn 前记 `size0`，返回后只读新增、无新记录即未执行）——覆盖范围大于 `exit 75`，且同时挡住「进程异常死亡读到旧记录」；`--trigger manual` 不再需要魔法退出码。**⚠️ 该实现已被 v1.2 的 R1 取代**（`size0` 无归属绑定，会认领到别人的记录），改为 `--run-id` 令牌匹配，见下表 | §4.2 第 3 点、§6.4、§7 |
| M4 运行记录路径口径 | `$AGENT4SOM_REPO` 锚定（`_db_path` 先例），**禁止 `__file__`**；§6.6 写明 | §4.2 第 4 点、§4.3、§6.6 |
| M5 `pause` 冻结 `next_run_at` | 端点统一规范化 `next_run_at = None if state == "paused" else …`；§7 断言与 §9 #4 同步改写 | §4.2 第 2 点、§7、§9 #4 |
| L1 D6 与 §4.2 冲突 | D6 改为「前端只传 `HH:MM`，服务端校验并拼装」 | §3 D6 |
| L2 行号/位置漂移 | 全文按**符号名**重同步（含 `adapter.py:885/:1041/:1077`、`to_thread:377`、路由插入点 `:162` 之后、cron ticker 真实位置、`build.js:21-33`）；附录 A 改为符号名优先 | 全文、附录 A |
| L3 前端是另一个仓 | §5 / §11 标注仓归属，§10 单列前端仓提交 | §5、§10、§11 |
| L4 包装脚本落点在另一个仓 | §4.3 标明 `agent4som-hermesagent`（目录由 cron 自动创建），建议纳入该仓提交 | §4.3、§10 |
| L5 `items[].status` 示例值不存在 | 改用 `result.status.value` 原值并列出 `IngestionStatus` 全集 | §4.1 |
| L6 运行记录不必截断 | 改为**不截断**（D7 / §4.1 / §9 #2） | §3 D7、§4.1、§9 #2 |
| L7 读到半行 | 逐行解析、跳过坏行、取最后一条可解析记录 | §4.2 第 3 点 |
| L8 600s 超时 SIGKILL | 超时返回 **504**（与 500 区分）+ 前端文案；**修正**：`finally` 对 SIGKILL 无效，故明确「临时文件残留可接受、不加清理机制」 | §4.2 第 3 点、§8 风险 4、§5 |
| L9 补跑窗口未量化 | 补 grace 语义限定 + 抓取窗口量化（约 30 条 ≈ 2.5 天最坏 / 1 个月中位） | §6.1、§6.2、§8 风险 1 |
| L10 记录缺「谁触发的」 | 运行记录加 `by` 字段，手动路径传 `--by <openid>` | §4.1、§4.3 |
| L11 `app.json` 是生成产物 + 落地顺序 | §5 明确改 `SHARED_PAGES`、不要手改 `app.json`；§10 写明「先落 `pages/*` 四个文件 → 再加 `SHARED_PAGES` → 再 build」 | §5、§10 |
| L12 两条路径环境来源不同 | §6.7 改写：`load_dotenv` 用 `setdefault` 不覆盖 → 实际配置取决于进程环境；两份 `.env` 须同步轮换 | §6.7、§2.3 |
| L13 前端命名与文案硬约束 | 页面改名 `pages/jxtz-sync/`；文案必须走 `UI_TEXT`（`validate.js:114-129`） | §5 |
| L14 两套响应约定 + 同名 job | §5 写明前端按 `statusCode` 判定；`_find_jxtz_job()` 多条时 WARNING + 取 `next_run_at` 最早 | §5、§4.2 第 1 点 |
| 补充（评审外） | ① `cron/scheduler.py:554-558` 只是锁路径函数，`tick()` 的真实持锁点是 `:3528-3536`；② `no_agent` 分支是 `:2524-2607`（非 `:2507-2532`）——均按符号名重写；③ grace 窗口越界**仍执行一次**（只折叠累积槽位） | §2.2、§6.1 |

**评审采纳（2026-09-11，v1.2）**：设计复核的 **R1–R9 全部采纳**（含 1 处评审外修正），落点如下。本轮全部是 v1.1 新增机制（字节偏移法 / 600s 超时）自带的边界，**不是架构返工**。

| 项 | 处置 | 落点 |
|---|---|---|
| R1 字节偏移法的跨运行误配竞态 + 与「任何终止路径都写」自相矛盾 | 取**修复方案 A**：`--run-id <uuid>` 令牌精确匹配（`size0` 只用于界定扫描区间，`run_id` 才是认领判据）；并在 §4.1 / §4.3 / §6.4 三处写死「**持锁失败是唯一不写记录**的终止路径」+「锁须在读账本前获取、持有到记录落盘后」（评审给出 A/B 两案，**取 A**——B 只堵住矛盾、不消除竞态） | §4.1、§4.2 第 3 点、§4.3、§6.4、§7 |
| R2 600s 超时（SIGKILL）不留记录 | 取**方案 A**：端点在 `TimeoutExpired` 分支补写合成记录（`status="failed"`、`error="手动同步超时（600s，结果未知）"`）；§4.1 的「任何终止路径」收窄为「脚本自身能执行到的路径」，超时另注 | §4.1、§4.2 第 3 点、§7、§8 风险 4 |
| R3 `_find_jxtz_job` 与 `resolve_job_ref` 矛盾 | `_find_jxtz_job` 就地过滤 `name`、自行取最早、**直接取 `id`**；**不调 `resolve_job_ref`**；`pause`/`resume`/`update` 一律传 id | §4.2 第 1 点、§7 |
| R4 `error` 可能泄露凭证 | `error` = `type(exc).__name__` + 截断 200 字；初始化/连接类异常给固定文案；§7 加 `grep` 校验用例 | §4.1、§4.3、§7、§8 风险 7 |
| R5 `next_run_at` 是带时区 ISO / 时区标注 | 端点原样透传 ISO（不重格式化），示例与前端改为 ISO 解析 + 「（北京时间）」标注；`run_at` 同步改为 `isoformat()` 口径 | §4.1、§4.2 第 2 点、§5、§7 |
| R6 cron job 不可复现（机器重建即消失） | §10 固化 `hermes cron create` 命令并显式声明「job 不落在任何 git 仓、重建后必须重跑」；**按评审建议不引入幂等 bootstrap** | §7、§8 风险 3、§10 |
| R7 定时 `partial` 在 cron 侧记 `ok` | 采纳「显式声明」：§6.5 加「**cron 状态不反映 `partial`**」+ 明说不改退出码的理由；§9 列为已拍板取舍 | §6.5、§9 #6 |
| R8 包装脚本可能留孤儿持锁 | 取评审**建议 ①**：`os.execv` 替换自身进程（不用 `subprocess.run`）；§8 新增「孤儿同步持锁」风险行 | §3 D3、§4.3、§8 风险 6 |
| R9 `schedule_time` 解析需容错 | `schedule.kind != "cron"` → 返回 `null`（页面显示「未知」），**不 500**；§7 加用例 | §4.2 第 2 点、§5、§7 |
| 补充（评审外，我方发现） | §6.5 原写「cron 侧包装脚本不捕获 stdout」**有误**：`_run_job_script` 用 `capture_output=True` 且经 `redact_sensitive_text` 脱敏后写 cron output；真实差异是「cron 侧脱敏、手动侧原样进 journal」，已按事实改写 | §6.5 |

---

## 10. 实施顺序（批准后）

1. **`scripts/sync_jxtz.py`**（agent4som 仓）：`--trigger` / `--by` / `--run-id` 参数 + `flock`（读账本前获取、记录落盘后释放）+ 运行记录 JSONL（四态、失败路径也写、`error` 截断）+ `main()` 返回值收口；本地跑两次验证（含 §7 的三条失败路径用例与 R4 的 token `grep`）。
2. **部署 job（手工步骤，命令固化于此——R6）**：写 `~/.hermes/scripts/jxtz_sync.py`（`os.execv` 版）→
   ```bash
   hermes cron create "0 4 * * *" --name jxtz-sync --script jxtz_sync.py --no-agent --deliver local
   hermes cron list          # 确认存在、no_agent、next_run_at 正确
   ```
   > **注（R6）**：这一步（以及 `~/.hermes/cron/jobs.json`）**不落在任何 git 仓里**——包装脚本进 `agent4som-hermesagent` 仓只解决「脚本丢失」。机器重建 / 换机后必须**重跑上面这条命令**。按评审建议**不**为此加幂等 bootstrap 片段（一次性操作的代价低于维护一个自动创建 job 的路径；且失败时页面已显示「同步任务未注册」，可发现）。
3. **adapter**（agent4som 仓）：4 条路由（插在 `/api/methods/*` 群末尾）+ `_find_jxtz_job()` + `_jxtz_runs_path()`，隔离 harness 验证（含 401/403/404/409/504）；`schedule_time` 的 `kind != "cron"` 容错与超时合成记录一并验（R2/R9）。
4. `sudo systemctl disable --now jxtz-sync.timer` → 手动触发一次确认无冲突。
5. **前端**（**前端仓，独立提交**）：先落 `pages/jxtz-sync/*` 四个文件 → 再加 `config/api.js` 接口 → **再**改 `scripts/build.js` 的 `SHARED_PAGES`（带注释）→ 改 `pages/methods/` 入口卡 → `npm test` → 微信开发者工具过一遍。（顺序不可颠倒：`SHARED_PAGES` 先加、页面文件后建会让 7 个实例的构建与 `npm test` 全挂。）
6. **部署**：adapter 副本 `cp` + `sudo systemctl restart hermes-gateway@jwc-assistant` → 记录 `ActiveEnterTimestamp`；包装脚本（`os.execv` 版）纳入 `agent4som-hermesagent` 仓提交；文档状态改为「已实现」。

---

## 11. 模块索引

| 模块 | 文件（**仓**） | 本文相关职责 |
|---|---|---|
| 同步脚本 | `scripts/sync_jxtz.py`（agent4som） | 新增 `--trigger` / `--by` / `--run-id`、`flock`（读账本前获取）、运行记录 JSONL |
| cron 包装脚本 | `~/.hermes/scripts/jxtz_sync.py`（新建，落点属 **agent4som-hermesagent** 仓） | 用 `os.execv` 以 agent4som venv 拉起真脚本（无孤儿进程），供 Hermes cron 调用 |
| cron job 本体 | `~/.hermes/cron/jobs.json`（**不在任何 git 仓**，`hermes cron create` 创建） | job 注册；机器重建后须按 §10 重跑命令（R6） |
| Hermes cron | `~/.hermes/hermes-agent/cron/jobs.py`、`cron/scheduler.py`、`cron/scheduler_provider.py`、`tools/cronjob_tools.py`（agent4som-hermesagent） | job 存储与调度、暂停/恢复、脚本执行与路径校验、ticker |
| 小程序适配器 | `~/.hermes/plugins/miniapp-platform/adapter.py`（agent4som） | 4 条管理路由、手动同步子进程、权限门禁 |
| 运行记录 | `data/jxtz_sync_runs.jsonl`（新增）、`data/jxtz_sync.log`（既有） | 结构化结果 / 原始文本日志 |
| 前端 | `pages/jxtz-sync/`、`pages/methods/`、`config/api.js`、`scripts/build.js`（**前端仓**） | 管理页与入口 |
| systemd（退役） | `/etc/systemd/system/jxtz-sync.{service,timer}` | 迁移后停用，保留可回滚 |

---

## 附录 A：引用速查（符号名优先）

> 行号为 2026-09-11 实测；**实施时以符号名为准**。

| 引用 | 位置 |
|---|---|
| 同步脚本：路径常量 / 日志 / 入口 / `__main__` | `sync_jxtz.py:24-27`、`:40-48`、`:282`、`:427-428` |
| 同步脚本：`SCOPE`/`USER_ID` / 抓取窗口 / 退避重试 | `:32-33`、`:171-172`、`:304-315` |
| 同步脚本：抓取失败 `sys.exit(1)` / 新增判定 / 永久失败入账本 | `:316-319`、`:322-326`、`:369-373` |
| 同步脚本：`.env` 自加载 / 入库调用 / 状态归类 | `:338-339`、`:390`、`:393-404` |
| 同步脚本：初始化（无保护）/ 临时文件与 unlink / 账本原子写 / 摘要 | `:345-349`、`:386-391`（`:406`）、`:411-421`、`:424` |
| `.env` 加载实现（`setdefault` 不覆盖） | `knowledge_base/bootstrap.py:35-49`、`:76` |
| `IngestionStatus` 取值 | `knowledge_base/ingestion/orchestrator.py:58-67` |
| Chroma HTTP 模式（显式配置不回退） | `knowledge_base/repository/chroma_repository.py:30-99` |
| 两份 `.env` 的 Chroma 配置 | `agent4som/.env:17-18`、`agent4som-hermesagent/.env`（`AGENT4SOM_REPO` 在 `:62`） |
| cron：存储与输出目录 / job 记录字段 / `deliver` 默认 | `cron/jobs.py:1-6`、`:1186-1196`、`:1089-1091` |
| cron：`list_jobs` / `resolve_job_ref`（**本设计不用**） / `update_job` / `pause_job` / `resume_job` / `mark_job_run` | `:1258`、`:1233`（raise `:1252`）、`:1266`（重算 `:1304-1339`、paused 跳过 `:1316`）、`:1367`、`:1383`（重算 `:1389`）、`:1448`（状态 `:1465`） |
| cron：`compute_next_run`（产出带时区 `isoformat()`） | `cron/jobs.py:664` |
| cron：`claim_job_for_fire`（paused 拒绝 `:1678-1679`）/ `.jobs.lock` | `:1652`、`:1673`、`:195-197`、`:201` |
| cron：错过补跑 + grace 窗口 | `:1942-1976`、`:630-650` |
| cron：`no_agent` 脚本分支 / 退出码判定 / 默认超时 / 目录自动建 | `cron/scheduler.py:2524-2607`、`:2121-2129`、`:1975`、`:2045` |
| cron：解释器 / cwd / env 清洗 | `cron/scheduler.py:2093`、`:2104`、`:2105`；`tools/environments/local.py:354`（`:366-368`、`:385`、`:391-392`） |
| cron：`capture_output` 捕获 + `redact_sensitive_text` 脱敏（§6.5） | `cron/scheduler.py:2099-2107`、`:2113-2115` |
| cron：脚本路径校验（拒绝 `:546`、建目录 `:557`）/ 手动 run claim | `tools/cronjob_tools.py:528`、`:604-644`、`:625` |
| cron：pause/resume 动作 | `tools/cronjob_tools.py:828-834` |
| cron：ticker 真实实现 / 启动点 / 两把锁 | `cron/scheduler_provider.py:166-194`、`gateway/run.py:20791-20801`、`cron/scheduler.py:3528-3536`（tick）、`cron/jobs.py:195-197`（claim）、`cron/scheduler.py:3627-3632`（`_running_job_ids`） |
| cron：插件直调 `cron.*` 的先例 | `plugins/cron_providers/chronos/__init__.py:33`、`:184` |
| adapter：管理员门禁 / `_require_role` / `_authorize` / `_json_body` | `adapter.py:885`（同类 13 处）、`:420`、`:2177-2183`、`:2474` |
| adapter：响应风格（403 `:426-430`、401 `:2182`、400 `:855`、成功 `:922`/`:1038`） | 同上 |
| adapter：`asyncio.to_thread` 先例 / 路由群 / 运行记录路径先例 | `:377`、`:114-162`、`:2514-2519` |
| 前端：`SHARED_PAGES` / `DEFAULT_FEATURES` / `DEFAULT_UI_TEXT` / `app.json` 生成 | `scripts/build.js:21-33`、`:49-57`、`:61-67`、`:178-203` |
| 前端：硬编码校验 / 共享页存在性校验 / `.gitignore` | `scripts/validate.js:114-129`、`:162-168`、`.gitignore:10-12` |
| 前端：管理员段 / 入口卡先例 / 方法体先例 | `pages/methods/methods.wxml:112`（段）、`:39-46`、`:48-55`；`pages/methods/methods.js:152`、`:154-224` |
| 批量补录的断点续跑（非本次） | `batch_ingest_jxtz.py:195-213` |
