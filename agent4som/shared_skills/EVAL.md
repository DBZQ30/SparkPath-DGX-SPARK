# shared_skills —— 评测操作手册（EVAL.md）

> 面向「**跑一次 skill 评测并拿到官方 PASS 报告**」的操作手册。
> 开发规范见 [`README.md`](README.md)；会话记录见 [`HANDOFF.md`](HANDOFF.md)。
> 最后更新：**2026-09-25**。

---

## 0. TL;DR（最短路径）

```bash
cd /home/<DGX_USER>/SparkPath-DGX-SPARK

# 跑全部（或指定）skill；每个自动带「进度条 + 看门狗」
bash agent4som/scripts/run_all_skill_evals.sh                      # 三个都跑
bash agent4som/scripts/run_all_skill_evals.sh multi-path-academic-planning   # 只跑一个

# 进度（另开一个终端）
tail -f ~/work/skilleval/rerun-<时间戳>/<skill>/progress.log
```

目标：**Tier 1 通过（`PASSED WITH OBSERVATIONS` 属正常，11 validators + 2–4 findings）· Tier 2 PASS · Tier 3 `verdict=pass`（两侧 trial 全出分）** → 由 `skillevaluator` 生成 `BENCHMARK.md`。

**最容易翻车的四件事**：① 判官用推理模型（§2）② agent 用慢模型（§2）③ 长跑挂在会话/后台 shell 上（§8）④ 产物放 skill 目录（README §6-D）。

---

## 1. 评测体系与判定规则

| Tier | 内容 | 是否门禁 |
|---|---|---|
| **Tier 1** | 静态/安全：schema · version · security(SkillSpector) · pii · license · code-integrity · unicode · quality · lint · secrets(Gitleaks) | **blocking**（失败 exit=1） |
| **Tier 2** | 语义去重：embedding 聚类 + LLM 判定（`INTENTIONAL_DETAIL` vs 真重复） | **blocking**（`--no-block-on-dedup` 可关） |
| **Tier 3** | 实机评测：11 用例 × with/without skill → 六评估器 → 五维度 | `--agent-eval` 下为 **advisory**；但要拿 `verdict=pass` 需完整 |

**判定规则（官方）**：
- **维度 PASS ≥ 50%**（NEUTRAL 40–50%，FAIL <40%）
- **overall lift PASS ≥ +5 点**（FAIL ≤ −10 点）
- **`verdict = pass` 仅当每个维度在 ≥1 个 agent 上过 50%**；lift 是诊断证据、不覆盖该门槛
- ⚠️ **`verdict` 实际由 `execution_status` 决定**：只要任一侧有 trial errored/未出分 → `failed` → verdict `neutral`、BENCHMARK `INCOMPLETE`，**即使五维全 PASS**。
  → 要拿 `pass`，**必须双侧全出分**（见 §6 看门狗、§2 provider）。

**当前基线（2026-09-28，`claude-code` + `codex` 双 agent；提交比赛作品版）**：

| skill | Tier 1 | Tier 2 | Tier 3 | scored | lift (`claude-code` / `codex`) |
|---|:--:|:--:|:--:|:--:|--:|
| `academic-warning` | 11/11 | PASS | pass | 78/78 | **+19.8 / +22.0 点** |
| `training-plan-interpretation` | 11/11 | PASS | pass | 46/46 | **+24.3 / +15.9 点** |
| `multi-path-academic-planning` | 11/11 | PASS | pass | 48/48 | **+33.1 / +14.7 点** |

报告：`~/work/skilleval/rerun-20260927-2246/`（revision `73fa2372`）；
三者 BENCHMARK 均为官方生成版 **"✅ Overall verdict: PASS — Recommended for publication"**。
**本轮零看门狗误杀**（阈值已固化为 `--idle-min 8 --max-trial-min 45`）。

> **历史基线**：
> - 2026-09-27（业务代码大改后重评）：`academic-warning` +28.8/+14.2 · `training-plan` +20.2/+10.5 · `multi-path` +42.1/+14.0
> - 2026-09-26（双 agent 首轮）：`academic-warning` +22.7/+13.9 · `training-plan` +14.5/+12.7 · `multi-path` +33.0/+8.3
> - 2026-09-25（单 agent `claude-code`）：`academic-warning` +28.5 · `training-plan` +34.8 · `multi-path` +32.1
>
> ⚠️ **看门狗阈值必须用 `--idle-min 8 --max-trial-min 45`**（2026-09-27 实测）：
> baseline 在「纯文档题」上无文档可读时，会主动 `ScheduleWakeup(1800s)` 空转 ——
> 期间**持续有心跳写入**，idle 判据不触发，最终被 `max-trial-min` 兜底杀 → trial 未出分 →
> `execution_status=failed` → **`verdict=neutral`（即使五维全 PASS）**。
> `--max-trial-min 30` 会误杀；45 + idle 8 实测零击杀（9-27 补跑 / 9-28 全量均验证）。


## 2. 模型选型（**硬性**）

| 角色 | 模型 | 说明 |
|---|---|---|
| **判官**（Tier 3 打分 / Tier 2 LLM 判定） | **`deepseek-chat`** | **必须非推理模型**（`reasoning=0`、无空 content）。见下方实测 |
| **被测 agent ①** `claude-code` | **`step-3.7-flash`** | StepFun 的 Anthropic 协议（快；否则 Claude Code 的 auto 安全分类器会持续超时） |
| **被测 agent ②** `codex` | **`deepseek-flash`** | DeepSeek 的 **Responses** 协议（`/v1/responses`）。⚠️ **不是偏好，是变量冲突下唯一可行解**，见 §3 与 README §6-J |
| Tier 1 SkillSpector 语义分析 | `step-3.7-flash` | `SKILLSPECTOR_PROVIDER=openai` |
| Tier 2 embedding | 本机 **`qwen3-embedding`** | `http://127.0.0.1:8001/v1`（1024 维） |

> **为什么是双 agent**：官方 **289/366** 个 skill 用 `claude-code` + `codex`（`benchmarks.json`：
> `claude-code+codex` 289 / 空 76 / 仅 `codex` 1），`BENCHMARK.md` 的 Results 表**按 agent 分列**。
> 判定门槛是「**每个维度在 ≥1 个 agent 上过 50%**」——双 agent 正是让弱 agent 暴露短板、另一侧兜底。

> ⚠️ **StepFun 全系都不能当判官**（实测 `step-3.7-flash`/`step-3.5-flash`/`step-3.5-flash-2603`/`step-router-v1`/`step-5-preview`
> 在同一个短请求下都返回 `content=""` + `reasoning_content` 填满 + `finish_reason=length`）——
> `reasoning` 吃光 `max_tokens` → 判官 JSON `extract_json('')=None` → **trial `Unscoreable`**（加重试也治不好）。
> StepFun 只用来当**被测 agent**。

---

## 3. 环境变量（可直接复制）

```bash
cd /home/<DGX_USER>/SparkPath-DGX-SPARK
set -a && . /home/<DGX_USER>/.hermes/.env && set +a      # STEPFUN_API_KEY / DEEPSEEK_API_KEY
export PATH="$HOME/.local/bin:$PATH"
export SKILLEVALUATOR_LOCAL_SANDBOX=off

# —— 判官：非推理模型 ——
export SKILL_EVAL_LLM_PROVIDER=openai
export SKILL_EVAL_LLM_MODEL=deepseek-chat
export SKILL_EVAL_LLM_BASE_URL=https://api.deepseek.com
export SKILL_EVAL_LLM_API_KEY="$DEEPSEEK_API_KEY"     # 用 *_API_KEY，别用 OPENAI_API_KEY（留给 codex）

# —— 双 agent 的变量分配（本环境实测唯一可行组合，原因见 README §6-J）——
#    判官与 codex 共用 OPENAI_*（都指向 DeepSeek）；claude-code 独占 ANTHROPIC_*（StepFun）。
#    不能把 codex 指向 StepFun：OPENAI_BASE_URL 会被判官所在的 openai provider 覆盖成 DeepSeek。
export OPENAI_API_KEY="$DEEPSEEK_API_KEY"             # codex 用（DeepSeek /v1/responses）
export OPENAI_BASE_URL=https://api.deepseek.com       # codex 用

# —— 被测 agent claude-code（Anthropic 协议，独占 ANTHROPIC_*）——
export ANTHROPIC_BASE_URL=https://api.stepfun.com/step_plan
export ANTHROPIC_AUTH_TOKEN="$STEPFUN_API_KEY"
export ANTHROPIC_API_KEY="$STEPFUN_API_KEY"
export ANTHROPIC_MODEL=step-3.7-flash

# —— Tier 1 skillspector ——
export SKILLSPECTOR_PROVIDER=openai
export SKILLSPECTOR_MODEL=step-3.7-flash

# —— Tier 2 去重 embedding（本机；用 openai-compatible 避免真实密钥外流）——
export SKILL_EVAL_EMBEDDING_PROVIDER=openai-compatible
export SKILL_EVAL_EMBEDDING_API_KEY=local-embed
export SKILL_EVAL_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export SKILL_EVAL_EMBEDDING_MODEL=qwen3-embedding
```

> `run_all_skill_evals.sh` / `run_skill_eval.sh` 不替你设这些——请先 export（或把它放进 `~/.hermes/.env` 之上的一份 `eval.env`）。

**双 agent 前置：codex 的 verifier wrapper（一次性）**

`local_environment` 会把 verifier 切到「runtime bin 里的 python3」；**每个 agent 各有一份 runtime bin**，
只给 `claude-code` 放 wrapper 的话，`codex` 侧会报 `ModuleNotFoundError: No module named 'idna'`
→ `RewardFileNotFoundError`（README §6-J / §5bis-①）。

```bash
for RT in claude-code codex; do
  mkdir -p ~/.local/share/skillevaluator/runtimes/$RT/bin
  cat > ~/.local/share/skillevaluator/runtimes/$RT/bin/python3 <<'EOF'
#!/bin/bash
exec /home/<DGX_USER>/.local/share/uv/tools/skillevaluator/bin/python3 "$@"
EOF
  chmod +x ~/.local/share/skillevaluator/runtimes/$RT/bin/python3
done
```

**跑前探活（可选但推荐）**：

```bash
curl -s -o /dev/null -w "agent  %{http_code}\n" https://api.stepfun.com/step_plan/v1/messages \
  -H "x-api-key: $STEPFUN_API_KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -d '{"model":"step-3.7-flash","max_tokens":8,"messages":[{"role":"user","content":"hi"}]}'
curl -s -o /dev/null -w "codex  %{http_code}\n" https://api.deepseek.com/v1/responses \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" -H "content-type: application/json" \
  -d '{"model":"deepseek-flash","input":"hi"}'
curl -s -o /dev/null -w "judge  %{http_code}\n" https://api.deepseek.com/chat/completions \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" -H "content-type: application/json" \
  -d '{"model":"deepseek-chat","max_tokens":8,"messages":[{"role":"user","content":"hi"}]}'
curl -s -o /dev/null -w "embed  %{http_code}\n" http://127.0.0.1:8001/v1/embeddings \
  -H "content-type: application/json" -d '{"model":"qwen3-embedding","input":"hi"}'
```

---

## 4. 怎么跑

### 4.1 多 skill（推荐）

```bash
bash agent4som/scripts/run_all_skill_evals.sh [skill1 skill2 ...]   # 缺省=三个都跑
```
- 串行跑（避免 provider 争用）；每个 skill 自动起 **进度展示** + **看门狗**
- 报告根：`~/work/skilleval/rerun-<时间戳>/<skill>/`
- 跑完打印每个 skill 的 `verdict / scored / with / base / lift / 五维`

### 4.2 单 skill

```bash
bash agent4som/scripts/run_skill_eval.sh agent4som/shared_skills/<skill> \
  --agent-eval --tiers 1,2,3 -r cli,json,html,markdown \
  --output-dir "$OUT" --results-dir "$OUT/harbor" \
  --agents claude-code,codex \
  --agent-model claude-code=step-3.7-flash --agent-model codex=deepseek-flash \
  --env-mode local --n-attempts 2 --stop-on-pass --n-concurrent 4 \
  --pass-threshold 0.5 --timeout-multiplier 10 \
  --evaluated-source-repository DBZQ30/SparkPath-DGX-SPARK \
  --evaluated-source-revision "$(git rev-parse HEAD)" \
  --harbor-keep-jobs
```

| 参数 | 为什么这么设 |
|---|---|
| `--n-attempts 2 --stop-on-pass` | 单 trial 抖动可自动补跑；通过的用例不重复跑 |
| `--timeout-multiplier 10` | 给"慢但没挂"的 baseline 更多时间（默认 300s × 10 = 50min 上限） |
| `--env-mode local` | 本机模式（**不读 Dockerfile**）；需要 `evals/environment/` 夹具（§7） |
| `--agents claude-code,codex` | 采用双 agent 配置（`claude-code` + `codex`；官方基线亦为双 agent，但本环境 `codex` 走 DeepSeek 而非 NVIDIA catalog）。**`codex` 的 agent-model 必须是裸模型名**（`codex=deepseek-flash`），带 `openai/` 前缀会 404，见 README §6-J |
| `--evaluated-source-*` | 写进 `BENCHMARK.md` 的溯源信息 |
| `--harbor-keep-jobs` | 保留逐 trial 目录，便于事后诊断 |

> **为什么要用 `run_skill_eval.sh` 包装**：`skillspector 2.11.x` 会把 `skill.oms.sig` 计入 `components` 却从
> `total_components` 排除 → Tier 1 `security` INCOMPLETE。包装脚本在评测期间临时移开签名（官方 `SCAN_EXCLUDED_FILES`
> 本就排除它），结束自动还原。**若源码目录已不放签名（现行做法），它也照常工作。**

---

## 5. 进度展示 —— `eval_progress.py`

```bash
# 单次快照
python3 agent4som/scripts/eval_progress.py <eval_output_dir> --once
# 持续（每 20s 一行，可同时写 progress.log）
python3 agent4som/scripts/eval_progress.py <eval_output_dir> --interval 20 --log <out>/progress.log
```

输出样例：

```
[12:34:56] 已跑 23.1min | attempts 27 | 用例 22（with 11 · without 11） | 活跃 2
  ▸   4.2min  with    plan-pos-compare  (pid=123)
  ▸   1.1min  without plan-doc-boundary  (pid=456)
```

- `attempts` = 实际已经出分的 trial 数（含补跑）；`用例` = 按 (侧, 用例) 去重后的完成数
- `活跃` = 当前仍在跑、且有 `claude` 进程的 trial（含已跑时长）
- `run_skill_eval.sh` 与 `run_all_skill_evals.sh` 会**自动**在后台启停它（`EVAL_PROGRESS=0` 可关）

---

## 6. 看门狗 —— `eval_watchdog.py`

```bash
python3 agent4som/scripts/eval_watchdog.py <harbor_dir> \
  --interval 30 --idle-min 8 --max-trial-min 45 --kill \
  --log <out>/watchdog.log --pidfile <out>/watchdog.pid

# 精确停止（按 PID，绝不扫全表杀）
python3 agent4som/scripts/eval_watchdog.py <harbor_dir> --stop --pidfile <out>/watchdog.pid
```

**判据用 `idle`（无文件写入）而不是 `duration`**：

- `idle`：该 trial 的日志/会话文件超过 N 分钟没更新 → **真卡住**（卡在某次模型调用）→ `SIGTERM` 让它尽快失败、评测继续
- `max-trial-min`：兜底上限（脚本内置默认 10min；本轮评测实跑建议 **45**），防止极端"一直写但永不收敛"

> ⚠️ **别把 `max-trial-min` 设太小（如 10min）**：baseline（无 skill）**慢但仍在推进**，会被误杀 →
> 该用例未出分 → 整轮 `neutral`/`INCOMPLETE`（**实测踩过**）。建议 `--idle-min 8 --max-trial-min 45`。

---

## 7. 评测运行时夹具（`evals/environment/repo-linked-root/`）

**问题**：`--env-mode local` 下 agent 的 cwd 是 trial 临时 workspace，**没有你的包/依赖/数据库** → `python -m xxx.cli` 必然失败。

**方案**：把"仓库根"按 **flat 布局**放进 `evals/environment/repo-linked-root/` —— `local_environment._copy_environment_bundle`
会把它整棵树复制到 workspace 根，`python -m` 的 `sys.path[0]` 即 cwd → 零配置可导入。
（`evals/environment/` 下还支持 `skills/`→`workspace/skills`、`input/`→`workspace/input`、`repo/`→`workspace/repo`、`codex-config/`；只有 `repo-linked-root/` 铺到**根**。）

**三个 skill 的构建脚本与大小**：

| skill | 脚本 | 大小 |
|---|---|---|
| `academic-warning` | `build_skill_eval_env.sh` | 23MB（含业务库；带脱敏 + 年级自检） |
| `training-plan-interpretation` | `build_training_plan_eval_env.sh` | 14MB |
| `multi-path-academic-planning` | `build_multi_path_eval_env.sh` | 13MB |

**瘦身原则（默认瘦身，`FULL=1` 装全量）**：
- 查询/解读期只需 `pydantic` 栈 + 包 + SQLite 库；
- `python-docx / lxml / openpyxl / pypdf / requests` 只在**解析/上传类**用例才需要 → `FULL=1` 才装。
- **实测**：13MB 夹具（只含 pydantic 栈 + 包 + 库）即可正常跑 `list/route/compare/select/simulate/interpret`。
- **收益**：夹具越瘦，baseline 越不会在大工作区里 `grep/find` 乱翻。

> **解释器要对齐**：仓库 venv 是 3.12，而评测里的 `python` 是 **3.13** —— 二进制轮子不通用，依赖按评测解释器（uv cpython-3.13）装。
> **依赖闭包要实测**，别凭猜（多数包是函数内延迟导入）。

---

## 8. 长跑存活：`systemd-run --user`（**本次踩坑的解法**）

**问题**：评测要跑 1–4 小时。挂在 **后台 shell / `setsid nohup`** 上都会被 **OpenCode server 重启**清掉
（实测：`bash run_all_skill_evals.sh &` 与 `setsid nohup … &` 都被杀，`training-plan` 跑到一半中断）。

**解法**：挂到**用户 systemd 的瞬时服务**（与登录会话解耦）：

```bash
systemd-run --user --unit=skill-eval-3 --collect \
  --working-directory=/home/<DGX_USER>/SparkPath-DGX-SPARK \
  --setenv=HOME=/home/<DGX_USER> \
  bash -lc 'bash agent4som/scripts/run_all_skill_evals.sh \
    > /tmp/opencode/all_evals.log 2>&1'

# 查看
systemctl --user is-active skill-eval-3.service
systemctl --user status  skill-eval-3.service
# 结束（如需）
systemctl --user stop skill-eval-3.service
```

> `--collect` 让服务退出后自动清理；服务名可自取（如 `skill-eval-<skill>`）。

---

## 9. 安全杀进程 —— `safe_pkill.py`

**问题**：`pkill -f <pattern>`（或 `ps|grep|kill` 管道）会**匹配到执行该命令的 shell 自己**（命令行里就含 pattern）→ **自杀**。

```bash
python3 agent4som/scripts/safe_pkill.py eval_watchdog.py          # 干跑，列出会杀谁
python3 agent4som/scripts/safe_pkill.py --kill eval_watchdog.py   # 真杀
```
- 匹配 `/proc/<pid>/cmdline`（多个正则需**同时**命中）；**排除自身 PID、父进程链（到 init）、任何含 `safe_pkill` 的进程**
- 停止看门狗优先用 `eval_watchdog.py --stop --pidfile …`（**按 PID 精确停，不扫全表**）

---

## 10. 结果解读与落库

**读 JSON**：`<out>/skillevaluator-output-*.json`
- `overall_status`（passed/failed）、`results[].tier3`：
  - `summary.verdict`（pass/neutral/fail）· `execution_status`（succeeded/failed）
  - `agents.claude-code.{with_skill, baseline, lift}` · `dimensions[]`（五维：security/correctness/discoverability/effectiveness/efficiency）
  - `conditions.{with_skill,without_skill}.execution_errors`（未出分原因）
- **落库**：把生成的 `BENCHMARK.md` 拷到 skill 根（官方必需产物，别手写）
- **同步** `skill-card.md`：`Evaluation Results`（Tier 1/2/3 + per-evaluator 表 + 五维）与 `Skill Version(s)`
- 升 `SKILL.md` 的 `version` 并记 `CHANGELOG.md`

**发布**：`bash agent4som/scripts/export_skill_release.sh <skill> [输出目录]`
→ 产出可发布树（排除夹具/产物/旧签名）+ PII 自检 + **OMS 签名与验证**。

---

## 11. 踩坑清单（精简）

| 坑 | 症状 | 处置 |
|---|---|---|
| 判官用推理模型 | `content=""`、`Judge response was not a valid JSON object`、trial Unscoreable | 判官换 **`deepseek-chat`**（§2） |
| agent 用慢模型 | Claude Code auto 安全分类器持续超时、`AgentTimeoutError`、整轮 failed | agent 用 **`step-3.7-flash`**（§2） |
| provider 中途坏 | `403 real-name verification` / `451 content blocked` | **跑前探活**（§3）；完成实名/换 key |
| 看门狗阈值太紧 | 慢但没挂的 baseline 被 `SIGTERM` → 未出分 → `neutral` | 用 **idle 判据** + `--idle-min 8 --max-trial-min 45`（§6） |
| `expected_script` 太窄 | 写了 `python -m xxx.cli`，agent 用 `python3 -m` → 匹配不上、`goal_accuracy=0` | 只写命令主体（`xxx.cli route`） |
| 产物放 skill 目录 | Tier-1 扫描几百 MB、变慢 | 放仓库外（`~/work/skilleval/`） |
| 长跑被清 | 会话/后台 shell 被 server 重启杀掉 | **`systemd-run --user`**（§8） |
| `pkill` 自杀 | 杀看门狗时把自己的 shell 也杀了 | **`safe_pkill.py`** / `--stop`（§9） |
| negative 用例结构性扣分 | `expected_script=null` 的用例 `skill_execution` 记 0 | **无法修**，别在这上面耗 |
| **codex 凭据 401** | `codex runtime preflight failed: 401 … Your api key: ****xxxx is invalid` | 判官与 codex 共用 `OPENAI_*`（都指 DeepSeek），见 README §6-J |
| **codex 模型 404** | `404 The model "openai/xxx" does not exist` | codex 的 agent-model **用裸模型名**（不带 `openai/` 前缀） |
| **codex 侧 `idna` 缺失** | codex trial `RewardFileNotFoundError` + verifier `No module named 'idna'` | 给 **`runtimes/codex/bin/`** 也放 python3 wrapper（§3） |

---

## 12. 与官方标准对齐 & 夹具迁移评估

**官方必需 5 件**（`/home/<DGX_USER>/nvidia-skill/skills.zip`，367 个 skill 统计）：
`SKILL.md`(366) · `skill-card.md`(366) · `BENCHMARK.md`(366) · `skill.oms.sig`(366，**发布包**里) · `evals/`(363)。
`references/` 198/366（可选）；`CHANGELOG.md` 仅 3/366（我们保留，会报 LOW）。

- **frontmatter**：官方多数用 `metadata.author`（258）而非顶层 `author`（15）；我们三个统一 `metadata.author`。
- **签名时机**：官方流程「跑评测 → 评审 → **再签名**」→ **源码目录不放 `skill.oms.sig`**；发布时由 `export_skill_release.sh` 生成。
- **夹具位置**：官方主流是 `evals/files/`（14 个），`evals/environment/` 仅 **4/366**（受支持但非主流）。

**夹具形态评估**：

| 方案 | 内容 | 必要性 | 成本 | 结论 |
|---|---|---|---|---|
| A. 瘦身 | 只装查询期依赖 | **高** | 低 | ✅ **已做**（40M→13M / 36M→14M） |
| B. 迁移官方形态 | skill **自包含**（`scripts/` + `requirements.txt`）+ **Docker 模式**（默认 `--env-mode docker`） | 低（仅发布到 NVIDIA catalog 才需要） | 中高 | 暂缓，记为演进方向 |
| C. 保留 `repo-linked-root` | local 模式 sidecar（官方支持） | — | — | 保留（仅内部评测；已 gitignore + 发布排除） |

---

## 13. 可复用脚本（评测相关，`agent4som/scripts/`）

| 脚本 | 用途 |
|---|---|
| `run_all_skill_evals.sh` | 多 skill 串行评测（自动进度 + 看门狗） |
| `run_skill_eval.sh` | 单 skill 包装（临时移开签名；自动进度） |
| `eval_progress.py` | 进度展示（`--once` / 持续 / 写日志） |
| `eval_watchdog.py` | 看门狗（idle 判挂、`--pidfile/--stop`） |
| `safe_pkill.py` | 安全版 pkill |
| `build_skill_eval_env.sh` / `build_training_plan_eval_env.sh` / `build_multi_path_eval_env.sh` | 三个 skill 的夹具（默认瘦身，`FULL=1` 全量） |
| `patch_skillevaluator_judge_retries.py` | 判官重试补丁（⚠️ 每次 `uv tool install` 升级后**重打**） |
| `export_skill_release.sh` | 发布树 + PII 自检 + OMS 签名（`<skill> [out]`） |

---

## 14. 跑一次完整评测的 checklist

- [ ] 三个 provider **探活**（§3）：StepFun / DeepSeek / 本机 embedding 均 200
- [ ] 判官 = `deepseek-chat`、`claude-code` = `step-3.7-flash`、`codex` = `deepseek-flash`、embedding = 本机 `qwen3-embedding`
- [ ] **codex 侧 runtime wrapper 已放**（`runtimes/codex/bin/python3`，见 §3）
- [ ] **codex 的 agent-model 是裸模型名**（不带 `openai/` 前缀，见 §6-J）
- [ ] 夹具已按当前代码重建（`FULL=1` 视用例是否需要解析）
- [ ] `expected_script` 不带 `python -m ` 前缀
- [ ] 用 **`systemd-run --user`** 启动（§8），不要挂后台 shell
- [ ] 看门狗 `--idle-min 8 --max-trial-min 45`（§6）
- [ ] 进度：`tail -f <out>/progress.log`
- [ ] 跑完核对：`overall_status=passed` · Tier3 `verdict=pass` · `scored == expected`（双侧全出分）
- [ ] 落 `BENCHMARK.md` + 刷新 `skill-card.md` + 升 version/CHANGELOG
- [ ] 发布：`export_skill_release.sh <skill>`（签名 + 验证）
