#!/bin/bash
# 依次评测 shared_skills 下三个 skill（Tier 1/2/3），每个都带：
#   - 进度展示（eval_progress.py，写 <out>/progress.log）
#   - 看门狗（eval_watchdog.py，--idle-min 8 --max-trial-min 45 --kill）
#     阈值 2026-09-27 放宽：baseline 在纯文档题上可能 ScheduleWakeup 空转，30min 会误杀
# 并规避已知坑：判官=非推理模型(deepseek-chat)、agent=step-3.7-flash、n-attempts 2 + stop-on-pass、
# 评测期间临时移开 skill.oms.sig（run_skill_eval.sh 负责）、夹具已按当前代码重建。
#
# 用法：bash scripts/run_all_skill_evals.sh [skill1 skill2 ...]
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
set -a && . "${HERMES_HOME:-$HOME/.hermes}/.env" && set +a

export PATH="$HOME/.local/bin:$PATH"
export SKILLEVALUATOR_LOCAL_SANDBOX=off

# 判官：必须非推理模型（StepFun 全部是推理模型 → content 为空、判官非法 JSON）
export SKILL_EVAL_LLM_PROVIDER=openai
export SKILL_EVAL_LLM_BASE_URL=https://api.deepseek.com
export SKILL_EVAL_LLM_MODEL=deepseek-chat
# ⚠️ 双 agent 的变量分配（实测唯一可行组合，原因见 shared_skills/README.md §6-J）：
#   判官(openai) 与 codex 共用 OPENAI_*，两者都指向 DeepSeek（codex 走 DeepSeek /v1/responses）；
#   claude-code 独占 ANTHROPIC_*，指向 StepFun。
#   不能给 codex 用 StepFun：OPENAI_BASE_URL 会被判官所在的 openai provider 覆盖成 DeepSeek。
export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
export OPENAI_BASE_URL=https://api.deepseek.com

# 被测 agent claude-code：StepFun 快模型（Anthropic 协议）
export ANTHROPIC_BASE_URL=https://api.stepfun.com/step_plan
export ANTHROPIC_AUTH_TOKEN="$STEPFUN_API_KEY"
export ANTHROPIC_API_KEY="$STEPFUN_API_KEY"
export ANTHROPIC_MODEL=step-3.7-flash

# Tier 1 skillspector
export SKILLSPECTOR_PROVIDER=openai
export SKILLSPECTOR_MODEL=step-3.7-flash

# Tier 2 去重 embedding（本机）
export SKILL_EVAL_EMBEDDING_PROVIDER=openai-compatible
export SKILL_EVAL_EMBEDDING_API_KEY=local-embed
export SKILL_EVAL_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export SKILL_EVAL_EMBEDDING_MODEL=qwen3-embedding

SKILLS=("$@")
[ ${#SKILLS[@]} -gt 0 ] || SKILLS=(academic-warning training-plan-interpretation multi-path-academic-planning)

BASE="$HOME/work/skilleval/rerun-$(date +%Y%m%d-%H%M)"
mkdir -p "$BASE"
echo "[driver] BASE=$BASE"
echo "[driver] skills: ${SKILLS[*]}"

for S in "${SKILLS[@]}"; do
  OUT="$BASE/$S"; mkdir -p "$OUT"
  echo; echo "==================== $S ===================="
  # 进度
  python3 agent4som/scripts/eval_progress.py "$OUT" --interval 20 --log "$OUT/progress.log" >/dev/null 2>&1 &
  PROG=$!
  # 看门狗（等 harbor/ 出现；按 idle 判真挂）
  # ⚠️ 阈值（2026-09-27 实测放宽，勿收紧）：
  #   baseline（无 skill）在「纯文档题」上无文档可读时，可能主动 ScheduleWakeup(1800s) 空转，
  #   期间**持续有心跳写入** → idle 判据不触发，最终被 max-trial-min 兜底杀掉
  #   → 该 trial 未出分 → execution_status=failed → verdict=neutral（即使五维全 PASS）。
  #   实测 --max-trial-min 30 会误杀；放到 45 + idle 8 后零击杀、一次通过。
  python3 agent4som/scripts/eval_watchdog.py "$OUT/harbor" --interval 30 --idle-min 8 \
    --max-trial-min 45 --kill --log "$OUT/watchdog.log" --pidfile "$OUT/watchdog.pid" >/dev/null 2>&1 &
  WD=$!
  sleep 3

  bash agent4som/scripts/run_skill_eval.sh "agent4som/shared_skills/$S" \
    --agent-eval --tiers 1,2,3 -r cli,json,html,markdown \
    --output-dir "$OUT" --results-dir "$OUT/harbor" \
    --agents claude-code,codex \
    --agent-model claude-code=step-3.7-flash --agent-model codex=deepseek-flash \
    --env-mode local --n-attempts 2 --stop-on-pass --n-concurrent 4 \
    --pass-threshold 0.5 --timeout-multiplier 10 \
    --evaluated-source-repository DBZQ30/SparkPath-DGX-SPARK \
    --evaluated-source-revision "$(git rev-parse HEAD)" \
    --harbor-keep-jobs
  echo "[driver] $S exit=$?"

  kill "$PROG" "$WD" 2>/dev/null || true
  sleep 3
  # 汇总本 skill 的 verdict / 维度 / lift
  python3 - "$OUT" "$S" <<'PY'
import json, glob, sys
out, skill = sys.argv[1], sys.argv[2]
fs = sorted(glob.glob(f"{out}/skillevaluator-output-*.json"))
if not fs:
    print(f"[sum] {skill}: 无 JSON 输出"); raise SystemExit
d = json.load(open(fs[-1]))
t3 = None
for r in d.get("results", []):
    if r.get("tier3"):
        t3 = r["tier3"]; break
agents = (t3.get("agents") or {}) if t3 else {}
line = f"[sum] {skill}: overall={d.get('overall_status')} "
if t3:
    line += (f"| tier3={t3.get('verdict')} "
             f"| scored={t3.get('scored_attempts')}/{t3.get('expected_attempts')}")
print(line)
for ag, v in agents.items():
    print(f"    [{ag}] with={v.get('with_skill')} base={v.get('baseline')} lift={v.get('lift')} "
          f"verdict={v.get('verdict')}")
    for dim in (v.get("dimensions") or []):
        print(f"        {dim.get('id'):16} {dim.get('score')} {dim.get('verdict')} lift={dim.get('lift')}")
PY
done

echo; echo "[driver] 全部完成；报告根目录：$BASE"
