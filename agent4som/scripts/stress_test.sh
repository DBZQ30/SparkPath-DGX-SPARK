#!/bin/bash
# Agent4SOM 压力测试 — 验证系统在高并发下的表现
# 用法: bash scripts/stress_test.sh [并发数] [轮次]
# 默认: 10 并发, 3 轮
set -euo pipefail

CONCURRENCY="${1:-10}"
ROUNDS="${2:-3}"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi

GPU_HOST="${GPU_HOST:-}"
if [ -z "$GPU_HOST" ]; then
    echo "ERROR: GPU_HOST not set — source .env or export GPU_HOST=<GPU server IP>"
    exit 1
fi
CHROMA_AUTH="${CHROMA_AUTH_TOKEN:-}"

_now_ms() { echo $(($(date +%s%N) / 1000000)); }

# ── 并发请求工具 ────────────────────────────────────────────────────
_concurrent_curl() {
    local url="$1" timeout="$2" rounds="$3" auth_header="$4"
    local total_ok=0 total_fail=0 times=()
    for r in $(seq 1 "$rounds"); do
        local round_ok=0 round_fail=0
        local start_ms end_ms
        start_ms=$(_now_ms)
        for i in $(seq 1 "$CONCURRENCY"); do
            if [ -n "$auth_header" ]; then
                curl -sf --max-time "$timeout" -H "$auth_header" "$url" > /dev/null 2>&1 && round_ok=$((round_ok+1)) || round_fail=$((round_fail+1))
            else
                curl -sf --max-time "$timeout" "$url" > /dev/null 2>&1 && round_ok=$((round_ok+1)) || round_fail=$((round_fail+1))
            fi
        done
        end_ms=$(_now_ms)
        times+=($((end_ms - start_ms)))
        total_ok=$((total_ok + round_ok))
        total_fail=$((total_fail + round_fail))
    done
    # 统计
    local total=$((total_ok + total_fail))
    local avg=0
    if [ ${#times[@]} -gt 0 ]; then
        local sum=0; for v in "${times[@]}"; do sum=$((sum + v)); done
        avg=$((sum / ${#times[@]}))
    fi
    echo "  $total_ok/$total 成功, 平均耗时: ${avg}ms"
    [ "$total_fail" -gt 0 ] && echo "    ⚠ $total_fail 次失败"
}

echo "========================================="
echo "  Agent4SOM 压力测试"
echo "  并发: $CONCURRENCY, 轮次: $ROUNDS"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="
echo ""

echo "── ChromaDB 心跳 ──"
_concurrent_curl "http://127.0.0.1:8007/api/v2/heartbeat" 5 "$ROUNDS" ""

echo "── ChromaDB (Auth) ──"
_concurrent_curl "http://127.0.0.1:8007/api/v1/collections" 5 "$ROUNDS" "Authorization: Bearer ${CHROMA_AUTH}"

_url_base() {
    # http://host:port/path → http://host:port
    echo "$1" | sed -E 's|(https?://[^/]+).*|\1|'
}

# 服务地址从 .env URL 提取，修改端口只需改 .env
QWEN_EMBEDDING_URL="${QWEN_EMBEDDING_URL:-http://${GPU_HOST}:8001/v1}"
BGE_RERANKER_URL="${BGE_RERANKER_URL:-http://${GPU_HOST}:8002/v1/rerank}"
STEP_BACK_MODEL_URL="${STEP_BACK_MODEL_URL:-http://${GPU_HOST}:8006/v1}"
MINERU_URL="${MINERU_URL:-http://${GPU_HOST}:8005/file_parse}"

VLLM_BASE="$(_url_base "$STEP_BACK_MODEL_URL")"
EMBEDDING_BASE="$(_url_base "$QWEN_EMBEDDING_URL")"
RERANKER_BASE="$(_url_base "$BGE_RERANKER_URL")"
MINERU_BASE="$(_url_base "$MINERU_URL")"

echo "── vLLM ${VLLM_BASE} ──"
_concurrent_curl "${VLLM_BASE}/health" 10 "$ROUNDS" ""

echo "── Embedding ${EMBEDDING_BASE} ──"
_concurrent_curl "${EMBEDDING_BASE}/health" 10 "$ROUNDS" ""

echo "── Reranker ${RERANKER_BASE} ──"
_concurrent_curl "${RERANKER_BASE}/health" 10 "$ROUNDS" ""

echo "── MinerU ${MINERU_BASE} ──"
_concurrent_curl "${MINERU_BASE}/health" 10 "$ROUNDS" ""

echo ""
echo "========================================="
echo "  压力测试完成"
echo "========================================="
