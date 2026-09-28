#!/bin/bash
# 带着 skill.oms.sig 也能正常评测的包装脚本。
#
# 为什么需要
# ----------
# skillspector 2.11.x 会把 `skill.oms.sig` **计入 `components`** 却**排除在 `total_components` 之外**，
# 而 SkillEvaluator 要求两者相等（`validators/security.py:1621`）→ 判
#   `skillspector JSON component inventory contradicts analysis completeness`
# → `security` INCOMPLETE → `exit=1` → BENCHMARK 整体判 INCOMPLETE。
# （实测 v2.11.1 / v2.11.2 都有；**凡带签名的 skill 都会踩**。）
#
# 而 SkillEvaluator 自己就把签名列为「过滤发现」的产物：
#   `SCAN_EXCLUDED_FILES = {"skill-card.md", "benchmark.md", "skill.oms.sig"}`
# 所以**评测期间把它临时移开，与官方声明的扫描范围一致**；评测结束后自动还原。
#
# 用法
# ----
#   bash scripts/run_skill_eval.sh <skill_dir> [skillevaluator validate 的其余参数...]
#
# 例：
#   bash scripts/run_skill_eval.sh agent4som/shared_skills/academic-warning \
#     --agent-eval --tiers 1,2,3 -r cli,json,html,markdown \
#     --output-dir /tmp/eval-out --results-dir /tmp/eval-out/harbor \
#     --agents claude-code,codex \
#     --agent-model claude-code=step-3.7-flash --agent-model codex=step-3.7-flash \
#     --env-mode local --n-attempts 2 --stop-on-pass --n-concurrent 4 \
#     --pass-threshold 0.5 --timeout-multiplier 6
#
# codex 的 agent-model 用**裸模型名**（不带 `<provider>/` 前缀）。
#   - evaluator 生成的 codex `config.toml` 已指定 `model_provider = "openai_compatible"`，
#     codex 会用该 provider 的 base_url 发在 `--model` 里给的名字；
#   - 若写成 `openai/xxx`（官方文档里的 `openai/gpt-5.1-codex-mini` 风格），Harbor 会把整个
#     带斜杠的名字原样传给 codex → codex 当模型名发给 provider → **404 model does not exist**。
set -uo pipefail

SKILL=${1:?用法: run_skill_eval.sh <skill_dir> [skillevaluator validate 的其余参数...]}
shift || true

[ -d "$SKILL" ] || { echo "!! 不是目录: $SKILL" >&2; exit 1; }
[ -f "$SKILL/SKILL.md" ] || { echo "!! 不是 skill 目录（缺 SKILL.md）: $SKILL" >&2; exit 1; }

SIG="$SKILL/skill.oms.sig"
BAK=""
PROG_PID=""

restore() {
  if [ -n "$PROG_PID" ] && kill -0 "$PROG_PID" 2>/dev/null; then
    kill "$PROG_PID" 2>/dev/null || true
  fi
  if [ -n "$BAK" ] && [ -f "$BAK" ]; then
    mv -f "$BAK" "$SIG"
    echo "[wrapper] skill.oms.sig 已还原"
  fi
  BAK=""
}
trap restore EXIT INT TERM

# ── 进度展示（默认开；EVAL_PROGRESS=0 关闭）──────────────────────────────
# 解析输出目录（支持 "-o X" / "--output-dir X" / "-o=X" / "--output-dir=X"）
OUT_DIR=""
_prev=""
for _a in "$@"; do
  case "$_prev" in -o|--output-dir) OUT_DIR="$_a";; esac
  case "$_a" in -o=*|--output-dir=*) OUT_DIR="${_a#*=}";; esac
  _prev="$_a"
done
[ -n "$OUT_DIR" ] || OUT_DIR="reports"
if [ "${EVAL_PROGRESS:-1}" != "0" ] && command -v python3 >/dev/null 2>&1; then
  mkdir -p "$OUT_DIR" 2>/dev/null || true
  _SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  python3 "$_SCRIPT_DIR/eval_progress.py" "$OUT_DIR" --interval 20 \
    --log "$OUT_DIR/progress.log" >/dev/null 2>&1 &
  PROG_PID=$!
  echo "[wrapper] 进度展示：tail -f $OUT_DIR/progress.log  （或 python3 $_SCRIPT_DIR/eval_progress.py $OUT_DIR --once）"
fi

if [ -f "$SIG" ]; then
  BAK=$(mktemp -u /tmp/skill.oms.sig.XXXXXXXX)
  mv "$SIG" "$BAK"
  echo "[wrapper] 评测期间临时移开 skill.oms.sig（官方 SCAN_EXCLUDED_FILES 本就排除它）"
else
  echo "[wrapper] $SKILL 无 skill.oms.sig，直接评测"
fi

skillevaluator validate "$SKILL" "$@"
rc=$?
echo "[wrapper] skillevaluator exit=$rc"
exit $rc
