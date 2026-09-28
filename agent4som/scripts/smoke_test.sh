#!/bin/bash
# Agent4SOM 冒烟测试 — 部署后验证核心功能是否正常
# 用法: bash scripts/smoke_test.sh [--quick]
#
# --quick: 只做快速检查，跳过耗时测试(如 MinerU 文件解析)
set -euo pipefail

PASS=0
FAIL=0

green() { echo -e "\033[32m$1\033[0m"; }
red()   { echo -e "\033[31m$1\033[0m"; }
yellow(){ echo -e "\033[33m$1\033[0m"; }

pass() { green "  ✅ PASS: $1"; PASS=$((PASS+1)); }
fail() { red   "  ❌ FAIL: $1 — $2"; FAIL=$((FAIL+1)); }
warn() { yellow "  ⚠ WARN: $1"; }

QUICK="${1:-}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 加载 .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi

GPU_HOST="${GPU_HOST:-}"
if [ -z "$GPU_HOST" ]; then
    echo "ERROR: GPU_HOST not set — source .env or export GPU_HOST=<GPU server IP>"
    exit 1
fi
QWEN_API_KEY="${QWEN_API_KEY:-}"
CHROMA_AUTH_TOKEN="${CHROMA_AUTH_TOKEN:-}"
TIMEOUT=10

echo "========================================="
echo "  Agent4SOM 冒烟测试"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="
echo ""

# ════════════════════════════════════════════════════════════════
# 1. 本地服务
# ════════════════════════════════════════════════════════════════
echo "── 1. 本地服务 ──"

# 1.1 Gateway
if pgrep -f "hermes gateway run" > /dev/null 2>&1; then
    pass "Gateway 进程运行中 (PID: $(pgrep -f 'hermes gateway run' | head -1))"
else
    fail "Gateway 进程" "无 hermes gateway run 进程"
fi

# 1.2 ChromaDB
if curl -sf --max-time 5 http://127.0.0.1:8007/api/v2/heartbeat > /dev/null 2>&1; then
    pass "ChromaDB 心跳正常"
else
    fail "ChromaDB 心跳" "http://127.0.0.1:8007/api/v2/heartbeat 无响应"
fi

# 1.3 ChromaDB 查询
CHROMA_AUTH="${CHROMA_AUTH_TOKEN:-}"
COL_COUNT=$(curl -sf -H "Authorization: Bearer ${CHROMA_AUTH}" http://127.0.0.1:8007/api/v1/collections 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>/dev/null || echo "0")
if [ "$COL_COUNT" -gt 0 ]; then
    pass "ChromaDB 集合数: $COL_COUNT"
else
    fail "ChromaDB 集合" "无法列出集合"
fi

echo ""

# ════════════════════════════════════════════════════════════════
# 2. GPU 服务健康
# ════════════════════════════════════════════════════════════════
echo "── 2. GPU 服务健康 ──"

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

for entry in "vLLM:$VLLM_BASE" "Embedding:$EMBEDDING_BASE" "Reranker:$RERANKER_BASE" "MinerU:$MINERU_BASE"; do
    name="${entry%%:*}"
    base="${entry#*:}"
    if curl -sf --max-time "$TIMEOUT" "${base}/health" > /dev/null 2>&1; then
        pass "$name 健康检查 ($base)"
    else
        fail "$name 健康检查" "${base}/health 不可达"
    fi
done

echo ""

# ════════════════════════════════════════════════════════════════
# 3. GPU 服务功能
# ════════════════════════════════════════════════════════════════
echo "── 3. GPU 服务功能 ──"

if [ -z "$QWEN_API_KEY" ]; then
    warn "QWEN_API_KEY 未设置, 跳过 GPU 功能测试"
else
    # 3.1 Embedding
    EMBED_RESP=$(curl -sf --max-time "$TIMEOUT" -X POST "${EMBEDDING_BASE}/v1/embeddings" \
        -H "Authorization: Bearer ${QWEN_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"qwen3-embedding","input":"冒烟测试文本"}' 2>&1 || true)
    if echo "$EMBED_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))" 2>/dev/null | grep -q .; then
        DIM=$(echo "$EMBED_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']))")
        pass "Embedding API — 向量维度: $DIM"
    else
        fail "Embedding API" "无法生成向量"
    fi

    # 3.2 Reranker
    RERANK_RESP=$(curl -sf --max-time "$TIMEOUT" -X POST "${RERANKER_BASE}/v1/rerank" \
        -H "Authorization: Bearer ${QWEN_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"qwen3-reranker","query":"测试","documents":["文档A","文档B"],"top_n":1}' 2>&1 || true)
    if echo "$RERANK_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['results'][0]['relevance_score'])" 2>/dev/null | grep -q .; then
        SCORE=$(echo "$RERANK_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['results'][0]['relevance_score'])")
        pass "Reranker API — 相关性分数: $SCORE"
    else
        fail "Reranker API" "无法获取重排序结果"
    fi

    # 3.3 LLM
    LLM_RESP=$(curl -sf --max-time "$TIMEOUT" -X POST "${VLLM_BASE}/v1/chat/completions" \
        -H "Authorization: Bearer ${QWEN_API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"model":"qwen3-vl","messages":[{"role":"user","content":"回复 冒烟测试通过"}],"max_tokens":10}' 2>&1 || true)
    if echo "$LLM_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])" 2>/dev/null | grep -q .; then
        REPLY=$(echo "$LLM_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])" 2>/dev/null)
        pass "LLM API — 回复: $REPLY"
    else
        fail "LLM API" "无法获取对话回复"
    fi
fi

echo ""

# ════════════════════════════════════════════════════════════════
# 4. 存储层
# ════════════════════════════════════════════════════════════════
echo "── 4. 存储层 ──"

for db in "$PROJECT_ROOT/data/quota.db" "$PROJECT_ROOT/data/audit.db"; do
    if [ -f "$db" ]; then
        if sqlite3 "$db" "PRAGMA integrity_check;" 2>&1 | grep -q "ok"; then
            pass "SQLite $(basename "$db") — 完整性正常"
        else
            fail "SQLite $(basename "$db")" "完整性检查未通过"
        fi
    else
        warn "SQLite $(basename "$db") — 文件不存在"
    fi
done

echo ""

# ════════════════════════════════════════════════════════════════
# 5. 数据统计
# ════════════════════════════════════════════════════════════════
echo "── 5. 数据统计 ──"

DISK_PCT=$(df "$PROJECT_ROOT/data" --output=pcent 2>/dev/null | tail -1 | tr -d ' %')
DISK_AVAIL=$(df -h "$PROJECT_ROOT/data" --output=avail 2>/dev/null | tail -1 | tr -d ' ')
echo "  磁盘使用: ${DISK_PCT}% (可用: $DISK_AVAIL)"
echo "  ChromaDB 节点: $(curl -sf http://127.0.0.1:9091/metrics 2>/dev/null | grep chroma_nodes_total | cut -d' ' -f2 || echo 'N/A')"

echo ""

# ════════════════════════════════════════════════════════════════
# 6. 耗时统计
# ════════════════════════════════════════════════════════════════
echo "── 6. API 耗时 (ms) ──"

_test_latency() {
    local name="$1" url="$2"
    local start end elapsed
    start=$(date +%s%3N)
    curl -sf --max-time 5 -o /dev/null "$url" 2>/dev/null || true
    end=$(date +%s%3N)
    elapsed=$((end - start))
    if [ "$elapsed" -lt 1000 ]; then
        green "  $name: ${elapsed}ms"
    elif [ "$elapsed" -lt 3000 ]; then
        yellow "  $name: ${elapsed}ms"
    else
        red "  $name: ${elapsed}ms"
    fi
}

_test_latency "ChromaDB /heartbeat" "http://127.0.0.1:8007/api/v2/heartbeat"
_test_latency "vLLM /health" "${VLLM_BASE}/health"
_test_latency "Embedding /health" "${EMBEDDING_BASE}/health"

echo ""
echo "========================================="
echo "  结果: $PASS 通过, $FAIL 失败"
echo "========================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
