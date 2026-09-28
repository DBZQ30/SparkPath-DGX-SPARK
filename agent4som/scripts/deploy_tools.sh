#!/usr/bin/env bash
# deploy_tools.sh — Deploy agent4som tools to Hermes agent runtime.
#
# Usage:
#   ./scripts/deploy_tools.sh [--dry-run]
#
# This script copies tool files from agent4som/hermes_overlay/tools/ to
# the Hermes tools directory and patches Hermes toolsets.py to include
# the "rag" toolset definition.
# 对上游文件的本地改动（gateway / tui_gateway）见 hermes_overlay/patches/。
#
# Run after Hermes upgrades to restore custom tools.

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_AGENT="$HERMES_HOME/hermes-agent"
TOOLS_SRC="$(cd "$(dirname "$0")/../hermes_overlay/tools" && pwd)"
TOOLS_DST="$HERMES_AGENT/tools"
TOOLSETS_FILE="$HERMES_AGENT/toolsets.py"
BACKUP_SUFFIX=".bak.$(date +%Y%m%d%H%M%S)"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

echo "=== Deploy agent4som tools ==="
echo "  Source:      $TOOLS_SRC"
echo "  Hermes:      $HERMES_AGENT"
echo "  Dry-run:     $DRY_RUN"
echo ""

# ── 1. Copy tool files ────────────────────────────────────────────
if [ -d "$TOOLS_SRC" ]; then
    for tool_file in "$TOOLS_SRC"/*.py; do
        [ -f "$tool_file" ] || continue
        fname=$(basename "$tool_file")
        dst="$TOOLS_DST/$fname"
        echo "  [tool] $fname → $dst"
        if [ "$DRY_RUN" = false ]; then
            cp "$tool_file" "$dst"
        fi
    done
else
    echo "  [SKIP] tools source directory not found: $TOOLS_SRC"
fi

# ── 2. Patch toolsets.py ──────────────────────────────────────────
PATCH_MARKER="# --- agent4som rag toolset ---"

if grep -q "$PATCH_MARKER" "$TOOLSETS_FILE" 2>/dev/null; then
    echo "  [PATCH] rag toolset already patched in toolsets.py — skipping"
else
    echo "  [PATCH] adding rag toolset to toolsets.py"
    if [ "$DRY_RUN" = false ]; then
        cp "$TOOLSETS_FILE" "${TOOLSETS_FILE}${BACKUP_SUFFIX}"
        echo "  [BACKUP] created ${TOOLSETS_FILE}${BACKUP_SUFFIX}"

        HERMES_PYTHON="${HERMES_AGENT}/venv/bin/python3"
        if [ ! -x "$HERMES_PYTHON" ]; then
            HERMES_PYTHON="python3"
        fi

        "$HERMES_PYTHON" - "$TOOLSETS_FILE" << 'PYEOF'
import re
import sys

path = sys.argv[1]
rag_block = '''# --- agent4som rag toolset ---
    "rag": {
        "description": "RAG knowledge base search tools (agent4som)",
        "tools": ["knowledge_search"],
        "includes": []
    },'''

with open(path) as f:
    lines = f.read().split('\n')

# Find TOOLSETS = { and track brace depth to find its closing }
ts_start = None
for i, line in enumerate(lines):
    if re.search(r'TOOLSETS\s*=', line):
        ts_start = i
        break

if ts_start is not None:
    depth = 0
    found_start = False
    for i in range(ts_start, len(lines)):
        for ch in lines[i]:
            if ch == '{':
                depth += 1
                found_start = True
            elif ch == '}':
                depth -= 1
                if found_start and depth == 0:
                    prev = i - 1
                    while prev >= 0 and lines[prev].strip() == '':
                        prev -= 1
                    prev_line = lines[prev].rstrip()
                    if not prev_line.endswith(','):
                        lines[prev] = prev_line + ','
                    lines.insert(i, '')
                    lines.insert(i, rag_block)
                    break
        if found_start and depth == 0:
            break

    with open(path, 'w') as f:
        f.write('\n'.join(lines))
    print("  [PATCH] rag toolset inserted into TOOLSETS dict")
else:
    print("  [ERROR] Could not find TOOLSETS dict")
PYEOF
    fi
fi

# ── 2.5 Apply local patches to upstream Gateway/TUI (optional) ─────
PATCH_FILE="$(cd "$(dirname "$0")/../hermes_overlay/patches" && pwd)/hermes-local.patch"
if [ -f "$PATCH_FILE" ] && command -v git >/dev/null 2>&1; then
    if git -C "$HERMES_AGENT" apply --check "$PATCH_FILE" 2>/dev/null; then
        echo "  [patch] applying hermes-local.patch"
        [ "$DRY_RUN" = false ] && git -C "$HERMES_AGENT" apply "$PATCH_FILE"
    else
        echo "  [patch] hermes-local.patch 不适用（可能已应用或上游已变更），跳过"
    fi
fi

HOOKS_SRC="$(cd "$(dirname "$0")/../gateway/hooks" && pwd 2>/dev/null || echo '')"
HOOKS_DST="$HERMES_HOME/hooks"

# ── 3. Deploy hooks (optional) ─────────────────────────────────────
if [ -d "$HOOKS_SRC" ]; then
    for hook_dir in "$HOOKS_SRC"/*/; do
        [ -d "$hook_dir" ] || continue
        hook_name=$(basename "$hook_dir")
        dst="$HOOKS_DST/$hook_name"
        echo "  [hook] $hook_name → $dst"
        if [ "$DRY_RUN" = false ]; then
            rm -rf "$dst"
            mkdir -p "$dst"
            cp -r "$hook_dir"/* "$dst/"
        fi
    done
else
    echo "  [SKIP] hooks source directory not found: $HOOKS_SRC"
fi

echo ""
if [ "$DRY_RUN" = true ]; then
    echo "=== Dry-run complete. No changes made. ==="
else
    echo "=== Deploy complete. Restart Hermes gateway to activate. ==="
    echo "  Run: hermes gateway run --replace"
fi
