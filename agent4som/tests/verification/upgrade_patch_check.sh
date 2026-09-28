#!/bin/bash
# Hermes 上游框架升级后，本项目定制（hermes_overlay）完整性检查脚本。
# 运行: bash tests/verification/upgrade_patch_check.sh [hermes-agent目录]
#
# 检查对象是 deploy_tools.sh 部署到 hermes-agent 的全部定制：
#   1. tools/query_kb.py、knowledge_ingest.py（复制部署）
#   2. toolsets.py 的 rag 工具集（幂等补丁）
#   3. hermes-local.patch 对 gateway/run.py、gateway/platforms/base.py、
#      tui_gateway/server.py 的改动（执行轨迹旁路 / 最终答案标记 / skill.activate）
#   4. hooks/kb_init（复制到 $HERMES_HOME/hooks）
#
# 升级流程（见仓库根 README「开发全生命周期」）：
#   升级 hermes-agent → bash scripts/deploy_tools.sh → 运行本脚本确认零 FAIL。
#
# 历史注记：本脚本原检查 WeCom 时代补丁（issue #001-#014），企业微信入口
# 2026-09 弃用时相关补丁已随之移除，检查项已重写为当前 overlay 内容。

PASS=0
FAIL=0

# hermes-agent 目录解析：参数 > 环境变量 > 仓库约定（~/.hermes 软链）> 仓库内路径
HERMES_AGENT="${1:-${HERMES_AGENT:-$HOME/.hermes/hermes-agent}}"
if [ ! -d "$HERMES_AGENT" ]; then
    REPO_FALLBACK="$(cd "$(dirname "$0")/../../.." && pwd)/agent4som-hermesagent/hermes-agent"
    if [ -d "$REPO_FALLBACK" ]; then
        HERMES_AGENT="$REPO_FALLBACK"
    fi
fi
HERMES_HOME="$(dirname "$HERMES_AGENT")"

echo "=== hermes 升级补丁完整性检查 ==="
echo "  hermes-agent: $HERMES_AGENT"
echo ""

if [ ! -d "$HERMES_AGENT" ]; then
    echo "  ✗ 未找到 hermes-agent 目录（可用参数或 HERMES_AGENT 环境变量指定）"
    exit 1
fi

check_grep() {
    local label="$1" file="$2" pattern="$3" min="${4:-1}"
    local count
    count=$(grep -c "$pattern" "$HERMES_AGENT/$file" 2>/dev/null || echo 0)
    if [ "$count" -ge "$min" ]; then
        echo "  PASS: $label ($count matches)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (expected >= $min, got $count)"
        FAIL=$((FAIL + 1))
    fi
}

check_file() {
    local label="$1" file="$2"
    if [ -f "$HERMES_AGENT/$file" ]; then
        echo "  PASS: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label ($HERMES_AGENT/$file 不存在)"
        FAIL=$((FAIL + 1))
    fi
}

# ── 1. overlay 工具已部署 ──────────────────────────────────────────
check_file "tools/query_kb.py 已部署" "tools/query_kb.py"
check_file "tools/knowledge_ingest.py 已部署" "tools/knowledge_ingest.py"

# ── 2. toolsets.py rag 工具集（deploy_tools.sh 的幂等补丁标记）─────
check_grep "rag toolset 补丁标记" "toolsets.py" "agent4som rag toolset" 1

# ── 3. hermes-local.patch 关键改动 ────────────────────────────────
# run.py：Agent 活动事件旁路（opt-in，wants_agent_activity 门控）
check_grep "run.py 活动旁路 wants_agent_activity" "gateway/run.py" "wants_agent_activity" 1
# run.py：推理增量转发（轨迹「思考/思考执行」两拍的数据来源）
check_grep "run.py 推理增量转发" "gateway/run.py" "_fire_reasoning_delta" 1
# base.py：最终答案标记 hermes_final（HTTP 适配器判定本轮结束）
check_grep "base.py hermes_final 标记" "gateway/platforms/base.py" "hermes_final" 1
# base.py：适配器活动回调
check_grep "base.py on_agent_activity 回调" "gateway/platforms/base.py" "on_agent_activity" 1
# server.py：skill.activate 事件（技能激活可视化）
check_grep "tui skill.activate 事件" "tui_gateway/server.py" "skill.activate" 1

# ── 4. 启动钩子已部署到 $HERMES_HOME/hooks ────────────────────────
if [ -f "$HERMES_HOME/hooks/kb_init/handler.py" ]; then
    echo "  PASS: hooks/kb_init 已部署"
    PASS=$((PASS + 1))
else
    echo "  FAIL: hooks/kb_init 未部署（期望 $HERMES_HOME/hooks/kb_init/handler.py）"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "=== 结果: $PASS pass, $FAIL fail ==="
echo "  如有 FAIL：重新执行 bash scripts/deploy_tools.sh 后再跑本脚本。"
exit $FAIL
