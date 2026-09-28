# shared_skills —— 自研 Skill 开发与评测指南

> **来源**：2026-09-22 ~ 09-26 开发与打磨**三个**自研 skill 的完整过程。
> **当前状态（2026-09-28，提交比赛作品版）**：`academic-warning` · `training-plan-interpretation` · `multi-path-academic-planning`
> —— **Tier 1 PASSED WITH OBSERVATIONS（各 skill 2–4 findings）、Tier 2 PASS、Tier 3 `verdict=pass` 且双侧全出分**（双 agent：`claude-code` + `codex`，其中 `codex` 走 DeepSeek，非 NVIDIA catalog），
> Tier 3 lift 分别 **+19.8/+22.0 · +24.3/+15.9 · +33.1/+14.7 点**（`claude-code`/`codex`，见各自 `BENCHMARK.md`）。
> **目的**：下一次开发新 skill 时照着走，**不重复踩坑**。
> 会话记录见同目录 [`HANDOFF.md`](HANDOFF.md)；**评测操作手册见 [`EVAL.md`](EVAL.md)**
> （模型选型 / 进度条 / 看门狗 / 夹具 / 长跑存活 / 踩坑清单 / checklist）。

---

## 0. TL;DR（新建一个 skill 的最短路径）

```bash
# 1) 建目录 + 必需件（SKILL.md / skill-card.md / evals/evals.json；BENCHMARK.md 由评测生成）
#    写法见 §3、§4；照抄任一现有 skill 的结构最省事
# 2) 需要运行时/数据 → 放 evals/environment/（§7），并脱敏（scripts/deidentify_warning_db.py）
# 3) 评测（必须用包装脚本；产物放仓库外；环境变量见 §5.3 / EVAL.md）
#    多 skill 一起跑：bash agent4som/scripts/run_all_skill_evals.sh [skill1 skill2 ...]
bash agent4som/scripts/run_skill_eval.sh agent4som/shared_skills/<name> \
  --agent-eval --tiers 1,2,3 -r cli,json,html,markdown \
  --output-dir ~/work/skilleval/<name>/validate-$(date +%Y%m%d) \
  --results-dir ~/work/skilleval/<name>/validate-$(date +%Y%m%d)/harbor \
  --agents claude-code,codex \
  --agent-model claude-code=step-3.7-flash --agent-model codex=deepseek-flash \
  --env-mode local --n-attempts 2 --stop-on-pass --n-concurrent 4 \
  --pass-threshold 0.5 --timeout-multiplier 10 \
  --evaluated-source-repository <owner/repo> \
  --evaluated-source-revision "$(git rev-parse HEAD)"
# 4) 目标：Tier 1 通过（PASSED WITH OBSERVATIONS 属正常，见各 skill 的 BENCHMARK）+ Tier 2 PASS + Tier 3 verdict=pass（scored 全出分）
# 5) 发布：bash agent4som/scripts/export_skill_release.sh <skill> [输出目录]
```

**最容易翻车的四件事**（细节见 §6 与 EVAL.md）：判官别用推理模型（§6-A）· agent 别用慢模型（§6-B）·
产物别放 skill 目录（§6-D/§6-H）· **长跑别挂在会话/后台 shell 上**（会被 server 重启清掉 → 用 `systemd-run --user`，见 EVAL.md §8）。

---

## 1. 目录约定

```
shared_skills/
├── README.md                    ← 本文件（开发指南）
├── EVAL.md                      ← 评测操作手册（模型/进度/看门狗/夹具/长跑存活/踩坑）
├── HANDOFF.md                   ← 会话记录（发生了什么）
└── <skill-name>/                ← 一个 skill 一个目录
    ├── SKILL.md                 ← 必需
    ├── skill-card.md            ← 必需（治理卡）
    ├── BENCHMARK.md             ← 必需（**官方工具生成**，别手写）
    ├── CHANGELOG.md             ← 我们额外保留（官方 366 个里只有 3 个有；会报 LOW 非阻断）
    ├── evals/
    │   ├── evals.json           ← 必需（Tier 3 数据集）
    │   └── environment/         ← 可选：本机评测运行时夹具（已 gitignore，不入库/不进签名）
    ├── references/              ← 可选：口径/细则文档
    └── （⚠️ 源码目录**不放** skill.oms.sig —— 发布时由 `export_skill_release.sh` 生成，见 §6-D）
```

> ⚠️ **评测产物（`reports/`、`validate-*`）不要放 skill 目录** —— 官方 Tier-1 的扫描排除表只含
> `evals/` `results/` `versions/` `__pycache__/` `.git/` `.venv/` `node_modules/`，**不含 `reports/`**，
> 几百 MB 会被整体扫描。放仓库外最省事（`~/work/skilleval/<skill>/`）；放仓库树内也行，只是体积大。详见 §6-H。

---

## 2. 一个合规 skill 必须带的产物（官方 README 明文）

| 文件 | 官方覆盖 | 说明 |
|---|---|---|
| `SKILL.md` | 366/366 | 触发条件 + 执行编排 |
| `skill-card.md` | 366/366 | 治理卡（章节见 §3） |
| `BENCHMARK.md` | 366/366 | **由 `validate --agent-eval` 生成** |
| Tier-3 数据集 | 363/366 | `evals/evals.json`（也接受 `evals/*.json`、`eval/*.json`、`benchmark/evals.json`） |
| `skill.oms.sig` | 366/366 | **发布包**里的 OMS 签名。**源码目录不放**（官方流程：跑评测 → 评审 → 再签名）；发布时由 `scripts/export_skill_release.sh` 生成，见 §6-D/§8 |

官方同步管道**会丢弃缺任一必需件的 skill**。

---

## 3. SKILL.md 写作要求

### 3.1 frontmatter（官方高频字段与占比）

```yaml
---
name: <skill-name>              # 366/366
description: "Use when ..."     # 366/366：写清触发条件 + 不适用边界
version: 1.0.0                  # 96/366
license: Apache-2.0             # 363/366
compatibility: |                # 158/366：声明目标 agent + 运行依赖
  Designed for Claude Code, OpenCode, Codex, and Agent Skills-compatible tools. ...
allowed-tools: Read Bash        # 83/366：用到什么就声明什么
metadata:
  author: "团队名 <邮箱>"        # 258/366
  domain: ...                   # 60/366
  kind: tool                    # library | tool | service | knowledge
  tags: [...]
---
```

### 3.2 章节

官方常见：`## Purpose`、`## Prerequisites`、`## Instructions`/`## Workflow`、
**`## Examples`**、`## Limitations`、`## Troubleshooting`、`## References`。

> **`## Examples` 是 RECOMMENDED** —— 缺了会报 `SCHEMA/body_recommended_section`（MEDIUM）。

### 3.3 ❌ 不要做（都会触发安全/schema 告警）

| 反模式 | 触发的告警 |
|---|---|
| 自创「Files in this skill」文件清单段（其中的**自我引用**是元凶） | **HIGH `analysis-evasion`** → 直接让 `security` 失败（官方 366 个里只有 1 个有类似段落） |
| 把**未打包进 skill 的路径**写成反引号引用，如 `` `data/warning.db` ``、`` `xxx/docs/` `` | `reference_resolution/reference_missing` |
| 在 skill 根放 `CHANGELOG.md` | `SCHEMA/unexpected_file`（LOW，非阻断） |
| skill 不在 `skills/` / `team-skills/` 下 | `SCHEMA/folder_hierarchy`（MEDIUM，非阻断；可用 `SKILLEVALUATOR_SCHEMA_ALLOWED_DIRS` 放行） |

---

## 4. `evals/evals.json` 写法

### 4.1 用 **agentskills.io 格式**（官方首选；旧扁平数组会触发 CI deprecation warning）

```json
{
  "skill_name": "<skill-name>",
  "evals": [
    {
      "id": "<skill>-pos-xxx",
      "prompt": "用户会怎么说",
      "expected_output": "期望的最终答案",
      "assertions": ["逐条期望行为"],
      "expected_skill": "<skill-name>",
      "expected_script": "xxx.cli ..."          // 见下方说明：不要带 "python -m " 前缀
    }
  ]
}
```

字段映射（`tier3/dataset_utils.py::_normalize_entry`）：
`prompt`→`question`、`expected_output`→`ground_truth`、`assertions`→`expected_behavior`；
`skill_name`→`expected_skill`（**仅当条目未显式给出时**）。

> ⚠️ **negative 用例必须显式写 `"expected_skill": null`** ——
> 否则会被顶层 `skill_name` 自动填成 skill 名，负例直接失效。

`expected_script` 只用于**字符串匹配**「agent 是否调了这条命令」；不跑脚本的用例（纯文档题）填 `null`。

> ⚠️ **别写 `python -m xxx.cli ...`** —— agent 若用 `python3 -m` 就匹配不上 → 目标命令被漏判、
> `goal_accuracy`/`skill_execution` 误判 0。**只写命令主体**（如 `trainingplan.cli route`、
> `academicwarning.cli check --grade 2023级`），或稳定子串（如 `from academicwarning.db import WarningDB`）。
> 三个现有 skill 已统一按此放宽。

### 4.2 三条硬规矩（血泪换来的）

1. **用例必须在"同一个环境"里可同时满足。**
   `evals/environment/` 与 `harbor.runtime_env` 都是**全局一份，不支持 per-case**。
   > 反例：3 个用例都问「2023级」却分别期望 `READY` / `PENDING` / `INCOMPLETE` → **不可能同时通过**。
   > 改法：按年级/参数拆开（我们改成 2023级=READY、2024级=INCOMPLETE、2025级=PENDING）。
2. **别让两个用例题目一模一样却要求不同行为。**
3. **加"纯文档复述题"能显著拉高 lift。** prompt 里明确
   「**只读 skill 文档回答，不要执行任何命令/脚本/网络请求**」，考 skill 专有知识
   （精确话术、分支分派、降级契约）。官方高 uplift skill（**+70%**）全是这个模式。

---

## 5. 评测怎么跑

### 5.1 前置安装（一次性）

```bash
export GIT_CONFIG_GLOBAL=/dev/null   # dgx 的 git 全局代理是残留的，会挡住 GitHub，必须绕过
uv tool install --python 3.13 "skillevaluator[all] @ git+https://github.com/NVIDIA/SkillEvaluator.git"
uv tool install "git+https://github.com/NVIDIA/SkillSpector.git"        # Tier 1 security
# gitleaks（Tier 1 secrets）：从 GitHub release 下 linux_arm64 二进制到 ~/.local/bin/
python3 -m venv ~/.local/share/model-signing-venv
~/.local/share/model-signing-venv/bin/pip install model-signing         # 签名
```

> ⚠️ **每次安装/升级 `skillevaluator` 后，都要重打判官重试补丁**（否则一次瞬时抖动就能废掉整轮）：
> ```bash
> ~/.local/share/uv/tools/skillevaluator/bin/python3 \
>   agent4som/scripts/patch_skillevaluator_judge_retries.py
> ```
> 详见 §6-G。

### 5.2 verifier python wrapper（否则 verifier 报 `ModuleNotFoundError: idna`）

```bash
mkdir -p ~/.local/share/skillevaluator/runtimes/claude-code/bin
cat > ~/.local/share/skillevaluator/runtimes/claude-code/bin/python3 <<'EOF'
#!/bin/bash
exec /home/<DGX_USER>/.local/share/uv/tools/skillevaluator/bin/python3 "$@"
EOF
chmod +x ~/.local/share/skillevaluator/runtimes/claude-code/bin/python3
```

> ⚠️ **必须用 wrapper 脚本** —— `ln -s` 无效（CPython 按 **argv0 所在目录**找 `pyvenv.cfg`，
> 软链目录没有该文件，`sys.prefix` 会落回裸解释器）。

### 5.3 正式评测命令（已验证可跑出 PASS）

```bash
export PATH="$HOME/.local/bin:$PATH"
export SKILLEVALUATOR_LOCAL_SANDBOX=off

# —— 判官 ——
#   ⚠️ 双 agent 时判官与 codex 共用 OPENAI_*（都指向 DeepSeek），原因见 §6-J
export SKILL_EVAL_LLM_PROVIDER=openai
export SKILL_EVAL_LLM_MODEL=deepseek-chat           # reasoning=0、无空 content、约 1.5s
export SKILL_EVAL_LLM_BASE_URL=https://api.deepseek.com
export SKILL_EVAL_LLM_API_KEY="$DEEPSEEK_API_KEY"   # 用 *_API_KEY 而非 OPENAI_API_KEY（该变量留给 codex）
export OPENAI_API_KEY="$DEEPSEEK_API_KEY"           # codex 用
export OPENAI_BASE_URL=https://api.deepseek.com     # codex 用（judge 会覆盖成同值）

# —— 被测 agent claude-code（Anthropic 协议，独占 ANTHROPIC_*）——
export ANTHROPIC_BASE_URL=https://api.stepfun.com/step_plan
export ANTHROPIC_AUTH_TOKEN="$STEPFUN_KEY"
export ANTHROPIC_API_KEY="$STEPFUN_KEY"
export ANTHROPIC_MODEL=step-3.7-flash             # ⚠️ 必须够快，见 §6-B

# —— Tier 1 的 skillspector 语义分析单独配 ——
export SKILLSPECTOR_PROVIDER=openai
export SKILLSPECTOR_MODEL=step-3.7-flash

# —— Tier 2 语义去重：用本机 embedding 服务（只发 dummy key，真实密钥不外流）——
export SKILL_EVAL_EMBEDDING_PROVIDER=openai-compatible
export SKILL_EVAL_EMBEDDING_API_KEY=local-embed
export SKILL_EVAL_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export SKILL_EVAL_EMBEDDING_MODEL=qwen3-embedding

# —— 用包装脚本（skill 目录里若还有 skill.oms.sig 也能正常评测）——
# 它会在评测期间临时移开签名、结束自动还原；详见 §6-D
bash agent4som/scripts/run_skill_eval.sh <skill_dir> \
  --agent-eval --tiers 1,2,3 \
  -r cli,json,html,markdown \
  --output-dir <报告目录> --results-dir <报告目录>/harbor \
  --agents claude-code,codex \
  --agent-model claude-code=step-3.7-flash --agent-model codex=deepseek-flash \
  --env-mode local --n-attempts 2 --stop-on-pass --n-concurrent 4 \
  --pass-threshold 0.5 --timeout-multiplier 10 \
  --evaluated-source-repository <owner/repo> \
  --evaluated-source-revision "$(git rev-parse HEAD)" \
  --harbor-keep-jobs
```

> **一次跑多个 skill**：`bash agent4som/scripts/run_all_skill_evals.sh [skill1 skill2 ...]`（串行、逐个起进度条与看门狗）。
> **进度/看门狗/长跑存活（`systemd-run`）**详见 `EVAL.md`。

**怎么读结果**：`skillevaluator-output-*.json` → `tier3.summary`
（`verdict` / `execution_status` / `scored_attempts` vs `expected_attempts` / `overall_score` / `overall_lift`）。

**官方判定规则**：
- 维度 **PASS ≥ 50%**（NEUTRAL 40–50%，FAIL <40%）
- overall lift **PASS ≥ +5 点**、FAIL ≤ −10 点
- **Overall verdict = PASS 仅当每个维度在 ≥1 个 agent 上过 50%**；**lift 只是诊断证据，不覆盖该门槛**
- 官方常见配置：tasks 4（范围 1–8）、attempts 1、pass_threshold 50%、**双 agent（`claude-code` + `codex`）**

---

## 6. 踩过的坑（按类型）

### A. 判官 / LLM

- ⚠️ **判官绝对不要用推理模型**（`step-5-preview`、`step-3.7-flash`、`deepseek-flash` 等）。
  **根因（2026-09-23 定量定位）**：推理模型的 **`reasoning_content` 也计入 `max_tokens`**。
  复杂判官题（如 `accuracy` 的 5 条 rubric + 轨迹证据）思考量在 **12k–20k 字符间浮动**，
  一旦超出预算 → `finish_reason=length` 且 **`content=""`** → `extract_json('')=None`
  → 报 `Judge response was not a valid JSON object` → **`eval.py` 写残缺 reward + `exit 1`
  → 整个 trial `Unscoreable`**。
  同一个真实判官 prompt 的实测：

  | 模型 | max_tokens | finish | content | reasoning | 合法 JSON |
  |---|---|---:|---:|---:|---|
  | `step-5-preview` | 4096 | `length` | **0** | 19568 | **1/4** ❌ |
  | `step-5-preview` | 8192 | `stop` | ~300 | ~12000 | 4/4 ✅ |
  | `step-3.7-flash` | 4096 | `length` | **0** | 18115 | **0/1** ❌ 必然失败 |
  | **`deepseek-chat`** | 4096 | `stop` | 631–887 | **0** | **8/8** ✅（1.2–1.6s） |
  | 本机 `qwen3.8-27b` | 4096 | `stop` | 327 | **0** | ✅（14 tok/s 偏慢） |

  - ❌ **加"重试次数"治不了它**（每次都在同一预算里把思考烧完）；加 `max_tokens` 只是把赌注往后推。
  - ✅ **正解：判官用非推理模型**（`deepseek-chat` 最省心：reasoning=0、无空 content、1.5 秒）。
  - 保险：本地补丁已把 `STRUCTURED_JUDGE_MAX_TOKENS` **4096 → 8192**（可用 `SKILL_EVAL_JUDGE_MAX_TOKENS` 覆盖），
    并把判官重试从 1 次提到 3 次（见 §6-G）。
- `SKILL_EVAL_LLM_MODEL` 只影响**判官**；被测 agent 由 `--agent-model` 控制。

### B. Agent 运行时（**最容易让整轮评测作废**）

- **Claude Code 的 `auto` 模式安全分类器**：SkillEvaluator 会把
  `--permission-mode=bypassPermissions` **改写为 `auto`**（`local_agents.py:130`）
  → 每条**非只读**命令都要过一次**安全分类器**（一个走 `ANTHROPIC_BASE_URL` 的模型调用）
  → **模型慢就持续超时**（实测单 trial 出现 **90 次**
  `temporarily unavailable (timed out), so auto mode cannot determine the safety`）
  → 命令被挡、agent 只能跑 `find`/`env`/`ls` 等只读命令、空转
  → **`AgentTimeoutError 1800s`** → 该 trial `Unscoreable`
  → **整个 Tier 3 判 `failed`，with-skill 侧完全没有聚合分**。
  - ❌ 把 `ANTHROPIC_DEFAULT_HAIKU_MODEL` / `ANTHROPIC_SMALL_FAST_MODEL` 指向快模型 **无效**（分类器用的是主模型）
  - ✅ **解法：agent 用快的模型**（`step-3.7-flash`）。实测 `temporarily unavailable` **90 → 0**，34/34 全部出分
- **eval workspace 里默认没有 skill 的运行时**：agent 的 cwd 是 trial 临时目录、不在仓库根，
  环境里也没有包/依赖/数据库 → 需要 `evals/environment/`，见 §7。
- `PYTHONPATH` **不可注入**（属 evaluator 托管 loader 变量，`harbor.runtime_env` 设了会直接 `raise`）；
  `--env-mode local` **完全不读 Dockerfile**。

### C. 评测集设计

- 见 §4.2 三条硬规矩。
- 官方高 uplift 用例是**纯文档复述题**；若把可运行 CLI + 源码铺进 workspace，
  baseline 自己就能摸索出来 → lift ≈ 0。
- **agent 模型越弱，lift 越明显**：同一套用例，`step-5-preview` 时 baseline 0.86（lift≈0）；
  `step-3.7-flash` 时 baseline 0.73（**lift +18 点**）。官方用双 agent 正是这个原因。

### D. 产物 / 合规

- **源码目录不要放 `skill.oms.sig`**。官方 release checklist 顺序是
  「跑评测 → 完成 skill card → **签名刚通过评审的目录** → 发布」→ **签名在评测之后**。
  另外 SkillSpector 2.11.x 会把 `skill.oms.sig` 计入 `components` 却从 `total_components` 排除，
  而 SkillEvaluator 要求两者相等 → 判 `component inventory contradicts analysis completeness`
  → **Tier 1 `security` INCOMPLETE**（`exit 1`）。
- `reports/` 不要放 skill 目录（见 §1）。
- **`evals/` 里的真实数据不会被任何扫描拦住**（Tier-1 的 SkillSpector/PII 扫描**按设计跳过 `evals/`**）
  → 夹具里放真实数据必须**自己脱敏**，见 §8。
- 发布用 `export_skill_release.sh`，**不要直接 `zip -r`** skill 目录。
- **`skill.oms.sig` 放在 skill 目录时，直接跑 `validate` 会让 Tier 1 失败**（skillspector 2.11.x 把它计入
  `components` 却排除在 `total_components` 之外 → `security` INCOMPLETE → `exit=1`；v2.11.1/2.11.2 都有）。
  → **用 `scripts/run_skill_eval.sh <skill>` 评测**：它在评测期间临时移开签名、结束自动还原
  （官方 `SCAN_EXCLUDED_FILES` 本就声明排除签名，所以这是与官方扫描范围一致的跑法）。

### E. 签名工具（`model_signing`）

- `--ignore-paths` 是**相对 CWD** 解析的（不是相对模型目录）；从别处调用会失效。
- 被忽略目录里若有符号链接，仍会报 `Cannot use '...' because it is a symlink` → 需同时加 `--allow_symlinks`。
- 签名证书**必须有 `extendedKeyUsage = codeSigning`**，否则校验器报
  `Certificate does not specify 'ExtendedKeyUsage'`。
- `verify` 的选项是 **`--certificate_chain`（下划线）**，不是官方文档写的 `--certificate-chain`。
- **改 skill 目录里任何被签文件后必须重签** → 正确顺序：改文件 → 跑评测 → **最后签名**。

### F. Tier 2 语义去重

- Tier 2 = **embedding 相似度聚类 + LLM 判定**（判定 `INTENTIONAL_DETAIL` / 真重复）。
- **embedding provider 默认继承 `SKILL_EVAL_LLM_PROVIDER`** —— 如果那是 `anthropic` / `bedrock`，
  会直接报 `... does not provide embeddings`。**必须显式设**：
  ```bash
  export SKILL_EVAL_EMBEDDING_PROVIDER=openai-compatible
  export SKILL_EVAL_EMBEDDING_API_KEY=<任意非空串即可>
  export SKILL_EVAL_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1   # 本机 qwen3-embedding
  export SKILL_EVAL_EMBEDDING_MODEL=qwen3-embedding
  ```
  > 注意 provider 分支：选 `openai` 时会用 `OPENAI_API_KEY`（我们的 StepFun key）去打这个端点；
  > 选 `openai-compatible` 才只看 `SKILL_EVAL_EMBEDDING_API_KEY` —— **本地服务用后者，避免真实密钥外流**。
- Tier 2 **默认是 blocking**（`--block-on-dedup` 是默认值）→ 有真重复会拉低 exit code；要调就 `--no-block-on-dedup`。
- 快速自检（不必跑全量）：`skillevaluator tier2 dedup-scan <skill_dir>`。

### G. 判官重试次数（已打本地补丁）

上游 `tier3/harbor/templates/eval.py` 的判官**只重试 1 次**，而 `--n-attempts 1` 时：

> **任一 trial 判官失败 → 该 trial `Unscoreable` → `execution_status=failed` → 整个 Tier 3 无聚合分**（`lift=None`）

34 个 trial × 3 个必答判官 ≈ **100+ 次调用**，provider 一次瞬时抖动就废掉整轮（实测两次，每轮 45 分钟）。
上游**没有暴露「重试次数」的环境变量**，所以打了本地补丁：

```bash
# 用 skillevaluator 自己的 venv 跑；幂等，可重复执行
~/.local/share/uv/tools/skillevaluator/bin/python3 \
  agent4som/scripts/patch_skillevaluator_judge_retries.py
#   --check   只检查是否已打
#   --revert  从备份还原
```

补丁把**两处**判官都改成最多 `JUDGE_MAX_ATTEMPTS` 次（默认 **3**）、线性退避（默认 **2s**）：
`_call_validated_json_judge`（结构化判官：accuracy / goal_accuracy 等）与 `_judge_behavior`（behavior_check）。

- 覆盖项：`SKILL_EVAL_JUDGE_ATTEMPTS`、`SKILL_EVAL_JUDGE_RETRY_BACKOFF`
  > ⚠️ 这两个变量**未必能传进 verifier**（verifier 的环境是白名单制的）——
  > 实测生效的是**补丁里的默认值 3**；要改的话得走 `harbor.runtime_env`（见 §6-B）。
- 功能验证（`/tmp/verify_judge_retry.py` 口径）：不可解析 → 调 3 次；502 → 调 3 次；
  首次成功 → 调 1 次；第 2 次成功 → 调 2 次。
- ⚠️ **`uv tool install` 升级/重装 SkillEvaluator 后补丁会被覆盖 → 重新执行本脚本**（脚本会自检）。
- 原文件备份：同目录 `eval.py.pre-judge-retry-patch`；`--revert` 可还原。

### H. 评测产物的落盘位置（**修正过的一节**）

Claude Code 跑 **subagent** 时，会在自己的 `claude-tmp` 里建**符号链接**指向子会话的 `.jsonl`：

```
<trial>/agent/claude-tmp/.../tasks/<hex>.output  ->  <trial>/agent/sessions/.../subagents/agent-<hex>.jsonl
```

这些软链会**逐轮累积**（实测 1→1→3→5→9→7→3→6 = **35 个**）。

> ⚠️ **勘误**：最初（HANDOFF §6-38）把「Tier 3 被跳过」归因为"报告目录在仓库树内、软链毒化后续轮次"。
> **该归因是错的** —— 把产物移到仓库外后（v6）**仍然失败**。真正原因是 §6-I 的
> `--n-attempts ≥ 2` 触发的 attempt-merge snapshot（拷贝的是**本轮**产物目录，与位置无关）。
> **§6-I 才是根因**；本节只讲"放哪儿"的取舍。

**产物放哪儿的结论**：

| 位置 | 可否 | 理由 |
|---|---|---|
| **skill 目录内（`<skill>/reports/`）** | ❌ | 官方 Tier-1 的 SkillScanner 排除表**不含 `reports/`** → 几百 MB 会被整体扫描 |
| **仓库树内（如 `agent4som/skilleval-reports/`）** | ⚠️ 可以但体积大 | 功能上没问题；但会让仓库级工具（备份/索引/`git status`）变慢 |
| **仓库外（如 `~/work/skilleval/`）** | ✅ **推荐** | 体积大、含本机路径、gitignore 也管不到；放外面最省事 |

- ✅ **跑前自检**（防"上一轮残留的软链"混进来，虽非根因但仍是好习惯）：
  ```bash
  n=$(find <repo>/agent4som -type l -not -path '*/venv/*' -not -path '*/.venv/*' | wc -l)
  [ "$n" = 0 ] || { echo "仓库内有 $n 个符号链接，先清理"; exit 1; }
  ```
- **别把 `reports/` 放进 skill 目录**（见 §1）——这条是硬要求。

### I. `--n-attempts ≥ 2` 会撞上 Claude 的 subagent 软链，**整个 Tier 3 被丢弃**

`tier3/harbor/runner.py` 合并多个 attempt 时，会把每个 attempt job 目录
`copytree_secure` 成快照。而 Claude Code 跑 **subagent** 时会在 `<trial>/agent/claude-tmp` 下建软链
（`tasks/<hex>.output -> .../subagents/agent-<hex>.jsonl`）→ `copytree_secure` **拒绝软链**
（`secure_copy.py:274`，`role="source"`）→ 抛错 → `cli.py` 把 **Tier 3 报成 `skipped`、整轮结果丢弃**，
而 **Tier 1/2 照常 PASS、`exit=0`**（极易误判成"一切正常"）。

| `--n-attempts` | Tier 3 |
|---|---|
| **1** | ✅ 正常 |
| **≥2** | ❌ 只要 agent 用过 subagent 就**必然触发** |

- ✅ **修法**：补丁**阶段3** 给那个 `copytree_secure` 传 `ignore=` 跳过 `claude-tmp`
  （`copytree_secure` 支持 `ignore` 回调，且**在校验前**生效）——见 §6-G 的同一个补丁脚本。
- ⚠️ 把产物移出仓库树（§6-H）**并不能**解决这条；两件事都要做。

### J. 加第二个 agent（`codex`）—— 三个新坑（2026-09-26 实测）

官方 **289/366** 个 skill 是 **`claude-code` + `codex` 双 agent**（`benchmarks.json`：
`claude-code + codex` 289 / 空 76 / 仅 `codex` 1；`BENCHMARK.md` 的 Results 表就是**按 agent 分列**）。
本机跑 codex 时踩到下面三条，**前两条各自的报错都极具误导性**：

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| J-1 | `codex runtime preflight failed: 401 Unauthorized … Your api key: ****xxxx is invalid` | 判官用 `SKILL_EVAL_LLM_PROVIDER=openai` 时，`runner.py:949` 对 codex **不给**独立 `OPENAI_*`，只在 **os.environ** 里找；而 verifier 的 `OPENAI_*` 来自判官配置 | 见下方「变量分配」 |
| J-2 | `404 Not Found: The model "openai/step-3.7-flash" does not exist` | 官方文档的 `openai/gpt-5.1-codex-mini` 里 **`openai/` 是 codex 的 profile 名**（`codex -p` = `Layer $CODEX_HOME/<name>.config.toml`）。**但 Harbor 把 `--model` 原样传给 codex、且不加 `-p`** → codex 把整个带斜杠的名字当模型名发给 provider → 404 | **codex 的 `--agent-model` 用裸模型名**（`codex=deepseek-flash`），**不要带 `<profile>/` 前缀**；evaluator 生成的 `config.toml` 已指定 `model_provider`，裸名即可正确路由 |
| J-3 | `codex` 侧 trial 报 `RewardFileNotFoundError`，verifier 日志 `ModuleNotFoundError: No module named 'idna'` | 与 §5bis-① 同一个坑：verifier 用裸 python。差别是 **wrapper 只放在 `runtimes/claude-code/bin/`**，codex 用的是 `runtimes/codex/bin/` | 给**每个** agent 的 runtime 各放一份 wrapper（见下方命令） |

**`codex` 的 runtime wrapper（J-3，每个 agent 都要一份）**：

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

**变量分配（J-1；本环境实测唯一可行组合）**：

判官、`codex`、`claude-code` 三者争抢同一组 `*_BASE_URL`，而本环境**判官（DeepSeek）与
`claude-code`（StepFun）是不同厂商** → 穷举三种组合，**每种都必有一个 agent 被判官的 base_url 覆盖**
（`_provider_environment()` 会把 `config.base_url` 写进 `OPENAI_BASE_URL`）：

| 组合 | 判官 | `claude-code` | `codex` |
|---|---|---|---|
| A ✅ | DeepSeek | StepFun | **DeepSeek**（正好可用） |
| B | DeepSeek | **DeepSeek**（污染） | StepFun |

→ **采用 A**：判官与 `codex` **共用 DeepSeek**（`codex` 走 DeepSeek 的 `/v1/responses`，实测 200/可用），
`claude-code` 独占 `ANTHROPIC_*` 指向 StepFun。

```bash
# 判官 + codex（同一厂商，共用 OPENAI_*；判官另用 SKILL_EVAL_LLM_BASE_URL 显式指定）
export SKILL_EVAL_LLM_PROVIDER=openai
export SKILL_EVAL_LLM_MODEL=deepseek-chat
export SKILL_EVAL_LLM_BASE_URL=https://api.deepseek.com
export SKILL_EVAL_LLM_API_KEY="$DEEPSEEK_API_KEY"
export OPENAI_API_KEY="$DEEPSEEK_API_KEY"          # codex 用
export OPENAI_BASE_URL=https://api.deepseek.com    # codex 用（judge 也会把它覆盖成同值）
# claude-code（独占 ANTHROPIC_*）
export ANTHROPIC_BASE_URL=https://api.stepfun.com/step_plan
export ANTHROPIC_API_KEY="$STEPFUN_API_KEY"
export ANTHROPIC_MODEL=step-3.7-flash
# 命令行
#   --agents claude-code,codex \
#   --agent-model claude-code=step-3.7-flash --agent-model codex=deepseek-flash
```

> ⚠️ **`codex` 走 DeepSeek 的三点取舍**：① 与判官同厂商同 base（非官方所述"独立 Responses 凭据"）；
> ② DeepSeek Responses 是**推理模型**（响应含 `reasoning`），codex 侧会更慢；
> ③ 官方双 agent 用 **NVIDIA catalog 模型**（`nv_build` provider），本配置是第三方端点 →
> **报告里的 `codex` 列不对等官方基线**，只是本环境下的第二个 agent。

---

## 7. 让 Tier 3 真的能跑：评测运行时夹具

**问题**：`--env-mode local` 下 agent 的 cwd 是 trial 临时 workspace，
环境里没有你的代码 / 依赖 / 数据库 → 命令必然失败、skill 永远拿不到分。

**方案（已验证）**：把"仓库根"按 **flat 布局**放进 `evals/environment/repo-linked-root/` ——
`local_environment._copy_environment_bundle` 会把它**整棵树复制到 workspace 根**，
而 `python -m` 的 `sys.path[0]` 就是 cwd → **包 / 依赖 / 数据零配置可导入**。

三个 skill 各有构建脚本（默认"瘦身版"，只装查询期依赖；`FULL=1` 才装解析期依赖）：

| skill | 构建脚本 | 夹具大小 |
|---|---|---|
| academic-warning | `build_skill_eval_env.sh` | **23MB**（含业务库；带**自动脱敏 + 年级状态自检**） |
| training-plan-interpretation | `build_training_plan_eval_env.sh` | **14MB** |
| multi-path-academic-planning | `build_multi_path_eval_env.sh` | **13MB** |

> **瘦身原则**：查询/解读期只需 `pydantic` 栈 + 包 + SQLite 库；`python-docx/lxml/openpyxl/pypdf/requests`
> 只在**解析/上传类**用例才需要 → 用 `FULL=1 bash scripts/build_*_eval_env.sh` 重建。
> **夹具越瘦，baseline 越不会在大工作区里乱翻**（baseline 中位 14–16 次工具调用、最多 103；with_skill 仅 2–3 次 —— 见 EVAL.md §7/§11）。

> ⚠️ **解释器版本要对齐**：仓库 venv 是 **3.12**，而评测里的 `python` 是 **3.13** ——
> 二进制轮子不通用，依赖必须按评测那个解释器（uv cpython-3.13）装。
>
> ⚠️ 依赖闭包要**实测**，别凭猜：`academicwarning` 的 `numpy`/`lxml`/`PIL` 都是**函数内延迟导入**，
> 只有 `openpyxl`/`pydantic`/`et_xmlfile`/`defusedxml` 等 8 个包是必需的（13MB vs 116MB）。

---

## 8. 可复用脚本（`agent4som/scripts/`）

| 脚本 | 用途 |
|---|---|
| `build_skill_eval_env.sh` | 生成 `academic-warning` 的 Tier-3 运行时夹具（静态资源 + cp313 依赖 + 业务库 + **自动脱敏** + 年级状态自检） |
| `build_training_plan_eval_env.sh` / `build_multi_path_eval_env.sh` | 另两个 skill 的 Tier-3 夹具（默认**瘦身**；`FULL=1` 装全量解析依赖） |
| `deidentify_warning_db.py` | 把 `warning.db` 里真实姓名/学号换成合成值（`9000000001→S0001`、`学生甲→学生0001`）。⚠️ **必须先改姓名再改学号**，否则后续 `WHERE student_id=?` 匹配不到（实测漏 443 处） |
| `export_skill_release.sh` | 产出**可发布树**（自动排除本机夹具/产物/旧签名）+ PII 自检 + **OMS 签名与验证**。用法：`export_skill_release.sh <skill> [输出目录]` |
| **`patch_skillevaluator_judge_retries.py`** | **给已安装的 SkillEvaluator 判官补上「可配多次重试」**（上游只重试 1 次）。⚠️ `uv tool install` 升级后会被覆盖 → **重新执行**。见 §6-G |
| **`run_skill_eval.sh`** | **单 skill 评测包装脚本**：skill 目录里若还有 `skill.oms.sig`，评测期间自动临时移开、结束还原（否则 Tier 1 `security` 会被 skillspector 计数 bug 判失败，见 §6-D）；并自动起**进度展示** |
| **`run_all_skill_evals.sh`** | **多 skill 串行评测**（每个自动起进度条 + 看门狗；判官/agent/embedding 环境变量内置） |
| **`eval_progress.py`** | 评测**进度展示**：已用时长 / attempts / 用例双侧计数 / 活跃 trial+时长；`--once` 看单次快照 |
| **`eval_watchdog.py`** | 评测**看门狗**：按 **idle**（非 duration）判"真挂"并可 `SIGTERM`；`--pidfile/--stop` 精确停止 |
| **`safe_pkill.py`** | **安全版 pkill**：排除自身/父进程链，杜绝"`pkill -f` 误杀自己" |

---

## 9. 本机可复用的服务

| 服务 | 地址 | systemd | 用途 |
|---|---|---|---|
| vLLM `qwen3.8-27b` | `http://127.0.0.1:8000/v1` | `dgx-vllm` | 主 LLM / VL |
| **Embeddings `qwen3-embedding`** | **`http://127.0.0.1:8001/v1`** | `dgx-embedding` | **1024 维**；可给 **Tier 2 去重** 用 |
| Reranker `qwen3-reranker` | `http://127.0.0.1:8002/v1` | `dgx-reranker` | 重排 |
| ChromaDB | `http://127.0.0.1:8007` | `chroma-server` | 向量库 |
| MinerU | `http://127.0.0.1:8005/file_parse` | — | 文档解析 |
| StepFun（外网） | `https://api.stepfun.com/step_plan` | — | **被测 agent**：`step-3.7-flash`（Anthropic 协议）；⚠️ **不能当判官**（全系推理模型，见 §6-A）。key 在 `~/.hermes/.env` 的 `STEPFUN_API_KEY`（**勿入库**） |
| DeepSeek（外网） | `https://api.deepseek.com` | — | **判官**：`deepseek-chat`（非推理、reasoning=0）。key 在 `~/.hermes/.env` 的 `DEEPSEEK_API_KEY` |

---

## 10. 开发新 skill 的 checklist

- [ ] 目录放 `shared_skills/<name>/`，含 `SKILL.md` + `skill-card.md` + `evals/evals.json`
- [ ] `SKILL.md` frontmatter 补 `license` / `compatibility` / `allowed-tools` / `metadata`；正文含 `## Examples`
- [ ] `SKILL.md` 里**没有**自创文件清单段；**没有**指向未打包路径的反引号引用
- [ ] `evals.json` 用 agentskills.io 格式；negative 显式 `expected_skill: null`；
      用例在**单一环境可同时满足**；含若干**纯文档复述题**；`expected_script` **不带 `python -m ` 前缀**（§4.1）
- [ ] 需要运行时的话，`evals/environment/` 夹具就位（**默认瘦身**，`FULL=1` 装解析依赖）并**脱敏**
- [ ] 跑评测（照 `EVAL.md`：进度条 + 看门狗 + `systemd-run` 长跑存活）→ **Tier 1 通过（PASSED WITH OBSERVATIONS 属正常）** + **Tier 2 PASS** + **Tier 3 `verdict=pass`（scored 全出分）**
- [ ] `BENCHMARK.md` 用**生成版**替换手写版
- [ ] **源码目录不放 `skill.oms.sig`**；发布用 `export_skill_release.sh <skill> [out]`
- [ ] 更新 `CHANGELOG.md` 与本目录的 `HANDOFF.md`
- [ ] `git status` 确认只有预期改动（报告/夹具/发布包均已 gitignore）

---

## 11. 待办（下一步）

1. ~~加第二个 agent（`codex`）~~ **✅ 已接入（2026-09-26）** —— 官方 **289/366** 是 `claude-code` + `codex` 双 agent；
   命令加 `--agents claude-code,codex --agent-model codex=deepseek-flash`。**踩坑与变量分配见 §6-J**
   （J-1 凭据 401 / J-2 模型名带斜杠 → 404 / J-3 codex 侧 verifier 缺 `idna`）。
2. ~~接入 Tier 2 去重~~ **✅ 已接入（2026-09-23）** —— 用本机 embedding 服务，配置见 §5.3：
   `SKILL_EVAL_EMBEDDING_PROVIDER=openai-compatible` +
   `SKILL_EVAL_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1` + `SKILL_EVAL_EMBEDDING_MODEL=qwen3-embedding`。
   实测：4 文件 → 33 chunks → 嵌入 → 1 个相似簇（max 0.892）→ LLM 判为 `INTENTIONAL_DETAIL` → **PASS**。
   也可单独跑 `skillevaluator tier2 dedup-scan <skill>` 快速自检。
3. *（暂不考虑）* 把 `skill.oms.sig` 换成 NVIDIA 签发的证书（需向 NVIDIA 申请）。
4. **夹具形态演进**：当前用 `evals/environment/repo-linked-root`（官方**支持**，但官方 367 个 skill 里仅 4 个用；主流是 `evals/files/`）。
   目标形态是「skill 自包含（`scripts/` + `requirements.txt`）+ Docker 模式」——评估见 `EVAL.md` §12。
