#!/bin/bash
# 构建 academic-warning skill 的 SkillEvaluator Tier 3 评测环境运行时。
#
# 背景：`--env-mode local` 下 agent 的 cwd 是 trial 的临时 workspace，但评测环境
# 里**没有任何运行时**（没包、没依赖、没数据库、没静态资源），于是
# `python -m academicwarning.cli ...` 必然失败，skill 永远拿不到分。
#
# 做法：把「仓库根」按 flat 布局放进任务 environment 的 `repo-linked-root/` sidecar。
# local 模式会把它整棵树复制到 **workspace 根**；而 `python -m` 的 sys.path[0] 就是
# cwd，因此 `academicwarning` / `knowledge_base` / `data` / 第三方依赖全部零配置可导入。
#
# 注意：`PYTHONPATH` 属于 SkillEvaluator 托管的 loader 变量，无法通过
# `harbor.runtime_env` 注入，所以 flat 布局是唯一可行方案。
#
# 产物落在 skill 内 `evals/environment/`（已在 agent4som/.gitignore 中忽略，含业务数据）。
# 用法：bash scripts/build_skill_eval_env.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC=$(dirname "$SCRIPT_DIR")                       # = agent4som/
SKILL="$SRC/shared_skills/academic-warning"
DEST="$SKILL/evals/environment/repo-linked-root"
BUILD_DIR="${BUILD_DIR:-/tmp/skillevaluator-eval-deps}"

# 评测里的 `python` 是 uv 的 cpython 3.13，二进制依赖必须匹配该 ABI。
PY313="${PY313:-}"
if [ -z "$PY313" ]; then
  for cand in "$HOME"/.local/share/uv/python/cpython-3.13*/bin/python3 "$(command -v python3.13 || true)"; do
    [ -x "$cand" ] && PY313="$cand" && break
  done
fi
[ -n "$PY313" ] || { echo "!! 找不到 python 3.13（可用 PY313=... 覆盖）" >&2; exit 1; }
"$PY313" -c 'import sys; assert sys.version_info[:2] == (3, 13), sys.version' || {
  echo "!! 需要 python 3.13，实际：$("$PY313" -V)" >&2; exit 1; }

echo "=== 0) 准备 cp313 运行依赖（13MB；numpy/lxml/PIL 是延迟导入，不需要）==="
if [ ! -d "$BUILD_DIR/pydantic" ]; then
  rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
  "$PY313" -m pip install --quiet --target "$BUILD_DIR" \
    openpyxl pydantic et-xmlfile defusedxml annotated-types typing-extensions typing-inspection
fi
echo "  deps: $(du -sh "$BUILD_DIR" | cut -f1)  ($PY313)"

echo "=== 1) 重置产物目录 ==="
rm -rf "$DEST"
mkdir -p "$DEST/academicwarning/result" "$DEST/knowledge_base/auth" "$DEST/data"

echo "=== 2) academicwarning 包源码（排除 __pycache__ / result 内容 / 临时垃圾）==="
cd "$SRC/academicwarning"
find . \( -name __pycache__ -o -path './result/*' \) -prune -o -type f ! -name 'Untitled' -print |
  while read -r f; do
    mkdir -p "$DEST/academicwarning/$(dirname "$f")"
    cp "$f" "$DEST/academicwarning/$f"
  done

echo "=== 3) knowledge_base.auth（权限判定所需，纯标准库）==="
cp "$SRC/knowledge_base/__init__.py" "$DEST/knowledge_base/"
cp "$SRC/knowledge_base/auth/__init__.py" "$SRC/knowledge_base/auth/role_store.py" "$DEST/knowledge_base/auth/"

echo "=== 4) 业务库副本 + 构造 PENDING 夹具（2025级一条 parsing 的选课结果）==="
cp "$SRC/data/warning.db" "$DEST/data/warning.db"
"$PY313" - "$DEST/data/warning.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("DELETE FROM source_file WHERE uploader='eval-fixture'")
c.execute("""INSERT INTO source_file
  (file_type, file_name, file_hash, file_path, upload_time, uploader, parsed_status, in_file_meta, grade)
  VALUES ('selection','2025级选课结果(解析中).xlsx','evalfixture2025',
          'data/warning_uploads/eval-fixture-2025','2026-09-22 12:00:00',
          'eval-fixture','parsing','{}','2025级')""")
c.commit()
print("  已注入 2025级 parsing 行 id =",
      c.execute("SELECT id FROM source_file WHERE uploader='eval-fixture'").fetchone()[0])
c.close()
PY

echo "=== 4b) 脱敏：真实学生姓名/学号 → 合成值（防止夹具被误打包时泄露 PII）==="
"$PY313" "$SCRIPT_DIR/deidentify_warning_db.py" "$DEST/data/warning.db"

echo "=== 5) cp313 依赖平铺到 workspace 根 ==="
cp -r "$BUILD_DIR"/* "$DEST/"
rm -rf "$DEST/__pycache__" "$DEST/bin"

echo
echo "=== 产物 ==="
du -sh "$DEST"
echo "年级状态自检（期望 2023级=READY / 2024级=INCOMPLETE / 2025级=PENDING）："
cd "$DEST"
for g in 2023级 2024级 2025级; do
  printf '  %-8s %s\n' "$g" "$("$PY313" -m academicwarning.cli precheck --grade "$g" 2>/dev/null | tail -1)"
done
