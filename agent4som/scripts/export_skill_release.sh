#!/bin/bash
# 产出**可发布**的 skill 目录树，并（可选）重新签名。
#
# 为什么需要：`evals/environment/` 是 Tier-3 的运行时夹具（官方 hook 路径，必须留在 skill 目录里），
# 但它体积 23MB、且是**本机可重建**的产物 —— 发布/提交时应排除。靠"记得排除"不可靠，
# 所以用本脚本做机械保证。
#
# 用法：
#   bash scripts/export_skill_release.sh <skill-name> [输出目录]
#   SKIP_SIGN=1 bash scripts/export_skill_release.sh <skill-name> ...   # 跳过签名
# 例：
#   bash scripts/export_skill_release.sh academic-warning
#   bash scripts/export_skill_release.sh multi-path-academic-planning ~/work/skill-release/mp
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SKILL=${1:?用法: export_skill_release.sh <skill-name> [输出目录]}
SRC=$(dirname "$SCRIPT_DIR")/shared_skills/$SKILL
OUT=${2:-$HOME/work/skill-release/$SKILL}
PKI=${PKI:-$HOME/.local/share/skill-signing}
MS=${MODEL_SIGNING_BIN:-$HOME/.local/share/model-signing-venv/bin/model_signing}

[ -d "$SRC" ] || { echo "!! 找不到 skill 目录: $SRC" >&2; exit 1; }
[ -f "$SRC/SKILL.md" ] || { echo "!! $SRC 不是 skill 目录（缺 SKILL.md）" >&2; exit 1; }

echo "=== 1) 复制发布树（排除本机夹具与本地产物）==="
rm -rf "$OUT"; mkdir -p "$OUT"
rsync -a \
  --exclude 'evals/environment/' \
  --exclude 'reports/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'skill.oms.sig' \
  "$SRC/" "$OUT/"

echo "=== 2) 发布树清单 ==="
find "$OUT" -type f | sed "s|$OUT/||" | sort
echo -n "  体积: "; du -sh "$OUT" | cut -f1

echo
echo "=== 3) PII 自检（发布树里不该有 warning.db / 学生数据）==="
if find "$OUT" -name 'warning.db' | grep -q .; then
  echo "  !! 发布树里仍有 warning.db —— 中止"; exit 1
fi
if grep -rIl --exclude='*.sig' -e 'student_id' -e '学号' "$OUT" 2>/dev/null | grep -q .; then
  echo "  ⚠ 发布树里有文本提到 student_id/学号（若只是文档描述可忽略）："
  grep -rIl --exclude='*.sig' -e 'student_id' -e '学号' "$OUT" 2>/dev/null | sed "s|$OUT/||" | sed 's/^/    /'
else
  echo "  OK 未发现学生数据文件"
fi

echo
echo "=== 4) 签名（OMS 格式；官方签名需 NVIDIA 证书，这里用自建根证书）==="
if [ "${SKIP_SIGN:-0}" = "1" ]; then
  echo "  (SKIP_SIGN=1，跳过)"
elif [ -x "$MS" ] && [ -f "$PKI/signing.key" ] && [ -f "$PKI/signing.crt" ]; then
  ( cd "$OUT" && "$MS" sign certificate . --signature skill.oms.sig \
      --private_key "$PKI/signing.key" \
      --signing_certificate "$PKI/signing.crt" \
      --certificate_chain "$PKI/root.crt" )
  ( cd "$OUT" && "$MS" verify certificate . --signature skill.oms.sig \
      --certificate_chain "$PKI/root.crt" )
else
  echo "  ⚠ 未找到 model_signing 或签名密钥（$PKI），跳过签名"
fi

echo
echo "=== 完成 ==="
echo "发布树: $OUT"
echo "重新生成评测夹具（若需在本机重跑 Tier 3）: 见 agent4som/scripts/build_*_eval_env.sh"
