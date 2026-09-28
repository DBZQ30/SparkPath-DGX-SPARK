#!/bin/bash
# 安装/更新 ChromaDB 服务 + Hermes Gateway 多 Worker
# 用法: sudo bash infra/deploy-concurrent.sh

set -euo pipefail

echo "=== 1. 安装 ChromaDB 服务 ==="
cp infra/chroma-server.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable chroma-server
systemctl restart chroma-server
echo "ChromaDB: http://127.0.0.1:8007"

echo ""
echo "=== 2. 启动 ChromaDB 后验证 ==="
sleep 2
curl -s http://127.0.0.1:8007/api/v2/heartbeat && echo " OK" || echo " FAIL"

echo ""
echo "=== 3. 安装 Hermes Gateway Workers (4 实例) ==="
cp infra/hermes-gateway@.service /etc/systemd/system/
systemctl daemon-reload

ROLLBACK=()
FAILED=0
for i in 1 2 3 4; do
    echo -n "Worker $i: "
    if systemctl restart hermes-gateway@$i 2>/dev/null; then
        systemctl enable hermes-gateway@$i 2>/dev/null
        echo "OK — http://127.0.0.1:801$i"
        ROLLBACK+=($i)
    else
        echo "FAIL"
        FAILED=1
        break
    fi
done

if [ "$FAILED" -eq 1 ]; then
    echo ""
    echo "⚠️  Worker $i 启动失败，回滚已启动的 worker..."
    for j in "${ROLLBACK[@]}"; do
        systemctl stop hermes-gateway@$j 2>/dev/null || true
        systemctl disable hermes-gateway@$j 2>/dev/null || true
        echo "  已回滚 worker $j"
    done
    echo "❌ 部署失败，已回滚。请检查日志: journalctl -u hermes-gateway@$i"
    exit 1
fi

echo ""
echo "=== 4. 检查所有服务状态 ==="
systemctl status chroma-server --no-pager -l | head -5
for i in 1 2 3 4; do
    systemctl status hermes-gateway@$i --no-pager -l | head -3
done

echo ""
echo "=== 部署完成 ==="
echo "ChromaDB:  localhost:8007"
echo "Workers:   localhost:8011-8014"
echo ""
echo "生产环境建议在前面加 nginx 做负载均衡:"
echo "  upstream gateway {"
echo "    server 127.0.0.1:8011;"
echo "    server 127.0.0.1:8012;"
echo "    server 127.0.0.1:8013;"
echo "    server 127.0.0.1:8014;"
echo "  }"
