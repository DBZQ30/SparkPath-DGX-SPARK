#!/bin/bash
# Scan ~/.hermes/skills/ for known anti-patterns that can cause tool-calling failures.
# Run periodically or after any LLM creates/modifies a skill.
#
# Exit code 0 = clean, 1 = warnings found.

set -euo pipefail

SKILLS_DIR="${HOME}/.hermes/skills"
ISSUES=0

red() { echo -e "\033[31m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

echo "=== Skill Anti-Pattern Scanner ==="
echo "Scanning: $SKILLS_DIR"
echo ""

# 1. Anti-tool instructions: "don't call X tool" / "X tool is broken"
echo "[1/4] Checking for anti-tool instructions..."
while IFS=: read -r file line; do
    red "  ANTI-TOOL: $file:$line"
    ISSUES=$((ISSUES + 1))
done < <(grep -rIn '不要.*调用\|禁止.*使用.*工具\|不要.*重试工具\|tool.*broken\|schema.*为空\|schema.*empty\|parameters.*properties.*为空' "$SKILLS_DIR" --include="*.md" 2>/dev/null)

# 2. CLI fallback prescription (should only be in admin docs, not in LLM skills)
echo "[2/4] Checking for CLI fallback prescriptions..."
while IFS=: read -r file line; do
    yellow "  CLI-FALLBACK: $file:$line"
    ISSUES=$((ISSUES + 1))
done < <(grep -rIn 'admin_cli.py\|走代码执行路径\|走 CLI' "$SKILLS_DIR" --include="*.md" 2>/dev/null)

# 3. LLM-generated skills without review
echo "[3/4] Checking for unreviewed LLM-generated skills..."
while IFS=: read -r file line; do
    yellow "  LLM-GENERATED: $file (author: agent, unreviewed)"
    ISSUES=$((ISSUES + 1))
done < <(grep -rln 'author:\s*agent' "$SKILLS_DIR" --include="*.md" 2>/dev/null)

# 4. Skills referencing deleted/archived knowledge-ingest skill
echo "[4/4] Checking for dangling references..."
while IFS=: read -r file line; do
    yellow "  DANGLING-REF: $file:$line"
    ISSUES=$((ISSUES + 1))
done < <(grep -rIn 'knowledge-ingest\|knowledge_ingest.*skill' "$SKILLS_DIR" --include="*.md" 2>/dev/null | grep -v 'hermes-agent' | grep -v 'SOUL.md')

echo ""
if [ $ISSUES -eq 0 ]; then
    echo "✓ No anti-patterns detected."
    exit 0
else
    red "✗ $ISSUES potential issue(s) found. Review before publishing any skill."
    exit 1
fi
