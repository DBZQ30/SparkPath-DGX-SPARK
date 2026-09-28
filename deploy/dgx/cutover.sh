#!/bin/bash
# cutover.sh —— 把服务从 acc-helper-vm 正式切换到 dgx
#
# 用法（在 dgx 上执行）：
#   bash ~/work/cutover.sh --check      # 只做就绪预检，不动任何服务
#   bash ~/work/cutover.sh              # 正式切换（会短暂停掉线上服务做最后一次增量同步）
#   bash ~/work/cutover.sh --rollback   # 把 dgx 服务停掉、线上服务恢复
#
# 注意：nginx 上游的切换不在此脚本内（见 switch-nginx.sh），需单独执行以便留出观察窗口。
#
# dgx 端口差异（见 deploy/dgx/README.md）：
#   :8000 = vLLM Qwen3.8-27B（chat+VL+Step-Back）
#   :8020 = miniapp-proxy（生产是 8000）
set -u
REPO="${AGENT4SOM_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
A4S="$REPO/agent4som"
HERMES="$REPO/agent4som-hermesagent"
# 迁移源（老 VM 路径；如脚本不随仓库分发，可用环境变量覆盖）
SRC_A4S="${SRC_A4S:-<OLD_VM_HOME>/H-agent/agent4som}"
SRC_HERMES="${SRC_HERMES:-<OLD_VM_HOME>/H-agent/agent4som-hermesagent}"
SRC_UNITS="chroma-server hermes-gateway@jwc-assistant miniapp-proxy academic-warning-api"
SRC_TIMERS="backup-kb.timer temp-cleanup.timer audit-cleanup.timer jxtz-sync.timer"
DST_UNITS="hermes-gateway@jwc-assistant miniapp-proxy"
DST_TIMERS="backup-kb.timer temp-cleanup.timer audit-cleanup.timer"
LLM_PORT=8000
PROXY_PORT=8020
MODE="${1:-cutover}"

hr() { echo "────────────────────────────────────────────────────────────"; }

check_models() {
  hr; echo "[预检] 模型服务"
  local ok=1
  for spec in "$LLM_PORT:qwen3.8-27b" "8001:qwen3-embedding" "8002:qwen3-reranker"; do
    p="${spec%%:*}"; m="${spec##*:}"
    printf '  :%-5s %-18s ' "$p" "$m"
    if curl -s -m 8 "http://127.0.0.1:$p/v1/models" | grep -q "$m"; then echo "OK"; else echo "缺失"; ok=0; fi
  done
  printf '  :%-5s %-18s ' 8005 /v1/health
  curl -sf -m 8 -o /dev/null http://127.0.0.1:8005/v1/health && echo "OK" || { echo "缺失"; ok=0; }
  printf '  :%-5s %-18s ' 8007 chroma-heartbeat
  TOKEN=$(grep -E '^CHROMA_AUTH_TOKEN=' "$A4S/.env" | cut -d= -f2-)
  curl -sf -m 8 -o /dev/null -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8007/api/v2/heartbeat && echo "OK" || { echo "缺失"; ok=0; }
  return $((1-ok))
}

check_src() {
  hr; echo "[预检] 线上服务可达"
  ssh -n -o BatchMode=yes acc-helper-vm "hostname; sudo -n true && echo 'sudo OK'" 2>&1 \
    | grep -vE 'Authorized users|^$' | tail -2 | sed 's/^/  /'
}

do_stop_src() {
  hr; echo "[1/6] 停止线上服务与定时器"
  ssh -n -o BatchMode=yes acc-helper-vm "
    sudo -n systemctl stop $SRC_TIMERS 2>/dev/null
    sudo -n systemctl disable $SRC_TIMERS 2>/dev/null
    sudo -n systemctl stop $SRC_UNITS
    sudo -n systemctl disable $SRC_UNITS 2>/dev/null
    sleep 3
    echo '  --- 残留监听 ---'
    ss -tlnp 2>/dev/null | grep -E ':(8000|8007|8008|8010)\b' || echo '  (已全部释放)'
  " 2>&1 | grep -vE 'Authorized users|^$' | sed 's/^/  /'
}

do_sync() {
  hr; echo "[2/6] 最后一次增量同步"
  R="rsync -az --partial --info=stats2"
  $R acc-helper-vm:"$SRC_A4S/data/chroma/" "$A4S/data/chroma/" 2>&1 | grep -E 'Total file size|Total transferred'
  for f in warning.db quota.db audit.db sqlite/miniapp.db jxtz_notices.jsonl; do
    mkdir -p "$(dirname "$A4S/data/$f")"
    $R acc-helper-vm:"$SRC_A4S/data/$f" "$A4S/data/$f" >/dev/null 2>&1 && echo "  synced $f"
  done
  $R acc-helper-vm:"$SRC_HERMES/state.db" "$HERMES/state.db" >/dev/null 2>&1 && echo "  synced state.db"
  $R acc-helper-vm:"$SRC_HERMES/roles.json" "$HERMES/roles.json" >/dev/null 2>&1 && echo "  synced roles.json"
}

do_verify_data() {
  hr; echo "[3/6] 数据校验"
  for db in "$A4S/data/warning.db" "$A4S/data/quota.db" "$A4S/data/audit.db" "$A4S/data/sqlite/miniapp.db" "$HERMES/state.db"; do
    [ -f "$db" ] || continue
    printf '  %-22s ' "$(basename "$db")"; sqlite3 "$db" 'PRAGMA integrity_check;' | head -1
  done
  printf '  chroma embeddings     '; sqlite3 "$A4S/data/chroma/chroma.sqlite3" "select count(*) from embeddings;" 2>&1
}

do_start_dst() {
  hr; echo "[4/6] 启动 dgx 服务"
  for u in $DST_UNITS; do
    sudo systemctl enable "$u.service" 2>/dev/null
    echo "  -> restart $u"
    sudo systemctl restart "$u.service"
    sleep 2
  done
  for t in $DST_TIMERS; do
    sudo systemctl enable --now "$t" 2>/dev/null && echo "  enabled $t"
  done
  echo "  等待 hermes miniapp 适配器 (127.0.0.1:8010) 就绪 ..."
  for i in $(seq 1 40); do
    curl -sf -m 3 -o /dev/null http://127.0.0.1:8010/ && break
    sleep 3
  done
}

do_verify_svc() {
  hr; echo "[5/6] 服务验证"
  for spec in "8007:Chroma" "8008:warning-api" "8010:hermes-miniapp" "$PROXY_PORT:miniapp-proxy"; do
    p="${spec%%:*}"; n="${spec##*:}"
    printf '  :%-5s %-18s ' "$p" "$n"
    curl -s -m 5 -o /dev/null -w 'HTTP %{http_code}\n' "http://127.0.0.1:$p/" 2>/dev/null || echo "N/A"
  done
  echo "  --- gateway state ---"
  grep -o '"gateway_state":"[^"]*"' "$HOME/.hermes/gateway_state.json" 2>/dev/null | sed 's/^/  /'
  echo "  --- 隧道 acc-svr:18000 -> dgx:$PROXY_PORT ---"
  ssh -n -o BatchMode=yes acc-svr "curl -s -m 8 -o /dev/null -w '  HTTP %{http_code}\n' http://127.0.0.1:18000/" 2>/dev/null | tail -1
  echo "  --- 端到端（nginx 未切前仍是旧后端）---"
  curl -s -m 15 -o /dev/null -w '  https://isom.xjtu.edu.cn/accapi/  -> HTTP %{http_code}\n' https://isom.xjtu.edu.cn/accapi/ 2>/dev/null
}

do_report() {
  hr; echo "[6/6] 下一步"
  cat <<'TIP'
  dgx 侧已就绪。现在按顺序做：
   1) 在 acc-svr 上切 nginx 上游：
        bash switch-nginx.sh --to-dgx
      （或手工把 /accapi/ 的 proxy_pass 从 http://<ACC_HELPER_VM_IP>:8000/
        改为 http://127.0.0.1:18000/）
   2) 微信开发者工具/真机验证：登录 → 对话 → 知识库问答 → 学业预警
   3) 观察 30 分钟后，在 acc-helper-vm 上确认可下线

  回滚：bash cutover.sh --rollback
TIP
}

rollback() {
  hr; echo "[回滚] 停止 dgx 服务"
  for u in $DST_UNITS; do sudo systemctl stop "$u.service" 2>/dev/null; echo "  stopped $u"; done
  for t in $DST_TIMERS; do sudo systemctl stop "$t" 2>/dev/null && echo "  stopped $t"; done
  hr; echo "[回滚] 恢复线上服务"
  ssh -n -o BatchMode=yes acc-helper-vm "
    sudo -n systemctl enable --now chroma-server academic-warning-api hermes-gateway@jwc-assistant miniapp-proxy
    sudo -n systemctl enable --now $SRC_TIMERS
    ss -tlnp 2>/dev/null | grep -E ':(8000|8007|8008|8010)\b'
  " 2>&1 | grep -vE 'Authorized users|^$' | sed 's/^/  /'
  hr; echo "  回滚完成（若已切 nginx，请把 proxy_pass 改回 http://<ACC_HELPER_VM_IP>:8000/）"
}

case "$MODE" in
  --check)
    check_models || { echo; echo "❌ 模型服务未就绪"; exit 1; }
    check_src
    echo; echo "✅ 预检通过，可以执行切换"
    ;;
  --rollback)
    rollback
    ;;
  *)
    check_models || { echo; echo "❌ 模型服务未就绪，终止切换"; exit 1; }
    check_src || exit 1
    do_stop_src
    do_sync
    do_verify_data
    do_start_dst
    do_verify_svc
    do_report
    ;;
esac
