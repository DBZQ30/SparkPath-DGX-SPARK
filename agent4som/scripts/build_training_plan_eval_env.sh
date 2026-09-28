#!/bin/bash
# 构建 training-plan-interpretation skill 的 SkillEvaluator Tier 3 评测运行时。
#
# 背景（同 academic-warning）：`--env-mode local` 下 agent 的 cwd 是 trial 临时
# workspace，环境里**没有**包 / 依赖 / 数据库 → `python -m trainingplan.cli ...` 必然失败。
# 做法：把「仓库根」按 flat 布局放进 `evals/environment/repo-linked-root/`，
# local 模式会整棵树复制到 workspace 根，`python -m` 的 sys.path[0]=cwd → 零配置可导入。
#
# 依赖：trainingplan 复用了 academicwarning（models/parsers）与 knowledge_base
# （VL，仅先修图解析时才用），因此夹具需一并带上这两个包的相关部分。
#
# 产物落在 skill 内 `evals/environment/`（已 gitignore，含方案公开数据）。
# 用法：bash scripts/build_training_plan_eval_env.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC=$(dirname "$SCRIPT_DIR")                       # = agent4som/
SKILL="$SRC/shared_skills/training-plan-interpretation"
DEST="$SKILL/evals/environment/repo-linked-root"
BUILD_DIR="${BUILD_DIR:-/tmp/skillevaluator-eval-deps-tp}"

PY313="${PY313:-}"
if [ -z "$PY313" ]; then
  for cand in "$HOME"/.local/share/uv/python/cpython-3.13*/bin/python3 "$(command -v python3.13 || true)"; do
    [ -x "$cand" ] && PY313="$cand" && break
  done
fi
[ -n "$PY313" ] || { echo "!! 找不到 python 3.13（可用 PY313=... 覆盖）" >&2; exit 1; }
"$PY313" -c 'import sys; assert sys.version_info[:2] == (3, 13), sys.version' || {
  echo "!! 需要 python 3.13，实际：$("$PY313" -V)" >&2; exit 1; }

echo "=== 0) cp313 运行依赖 ==="
# 查询/解读期只需要：包 + SQLite 库 + pydantic 栈；解析期（upload/reparse）才需要 docx/openpyxl/lxml。
if [ "${FULL:-0}" = "1" ]; then
  DEPS="openpyxl pydantic et-xmlfile defusedxml annotated-types typing-extensions \
typing-inspection python-docx lxml requests"
  DEPS_TAG="full"
else
  DEPS="pydantic annotated-types typing-extensions typing-inspection"
  DEPS_TAG="slim"
fi
if [ ! -d "$BUILD_DIR/pydantic" ] || [ ! -f "$BUILD_DIR/.deps-$DEPS_TAG.build" ]; then
  rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
  "$PY313" -m pip install --quiet --target "$BUILD_DIR" $DEPS
  touch "$BUILD_DIR/.deps-$DEPS_TAG.build"
fi
echo "  deps($DEPS_TAG): $(du -sh "$BUILD_DIR" | cut -f1)"

echo "=== 1) 重置产物目录 ==="
rm -rf "$DEST"
mkdir -p "$DEST"

copy_pkg() {   # copy_pkg <pkg_dir> <dest_rel>
  local pkg="$1" rel="$2"
  mkdir -p "$DEST/$rel"
  while IFS= read -r f; do
    mkdir -p "$DEST/$rel/$(dirname "$f")"
    cp "$SRC/$pkg/$f" "$DEST/$rel/$f"
  done < <( cd "$SRC/$pkg" && find . \( -name __pycache__ -o -path './result/*' \) -prune -o -type f ! -name 'Untitled' -print )
}

echo "=== 2) trainingplan 包 ==="
copy_pkg trainingplan trainingplan

echo "=== 3) academicwarning（复用其 models/parsers/service/db/export） ==="
copy_pkg academicwarning academicwarning

echo "=== 4) knowledge_base（VL 解析所需的最小集） ==="
mkdir -p "$DEST/knowledge_base/ingestion" "$DEST/knowledge_base/auth"
cp "$SRC/knowledge_base/__init__.py" "$DEST/knowledge_base/"
cp "$SRC/knowledge_base/ingestion/__init__.py" "$SRC/knowledge_base/ingestion/parsers.py" \
   "$DEST/knowledge_base/ingestion/"
cp "$SRC/knowledge_base/auth/__init__.py" "$SRC/knowledge_base/auth/role_store.py" \
   "$DEST/knowledge_base/auth/" 2>/dev/null || true

echo "=== 5) 业务库（本功能自建库，公开数据） ==="
mkdir -p "$DEST/data"
cp "$SRC/data/training_plan.db" "$DEST/data/training_plan.db"
# 清掉可能由本机测试产生的失败/排队行，保证用例在单一环境可同时满足
"$PY313" - "$DEST/data/training_plan.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("DELETE FROM plan_document WHERE parsed_status NOT IN ('done')")
c.execute("DELETE FROM plan_source_doc")
c.commit()
print("  plan_document:", c.execute("SELECT COUNT(*) FROM plan_document").fetchone()[0])
c.close()
PY

echo "=== 6) cp313 依赖平铺到 workspace 根 ==="
cp -r "$BUILD_DIR"/* "$DEST/"
rm -rf "$DEST/__pycache__" "$DEST/bin"

echo
echo "=== 产物 ==="
du -sh "$DEST"
echo "自检（期望：list 列出 4 专业；interpret 工商管理 2023 出四段；缺年级 → need_year）："
cd "$DEST"
"$PY313" -m trainingplan.cli list | head -3
echo "  ---"
"$PY313" -m trainingplan.cli interpret --major 工商管理 --entry-year 2023 | head -2
echo "  ---"
"$PY313" -m trainingplan.cli interpret --major 工商管理 | head -1
