#!/bin/bash
# switch-nginx.sh —— 在 acc-svr 上把 /accapi/ 的上游从 acc-helper-vm 切到 dgx（经反向隧道）
#
# 用法（在 acc-svr 上以 root 执行）：
#   bash switch-nginx.sh --to-dgx       # 切到 dgx (http://127.0.0.1:18000/)
#   bash switch-nginx.sh --to-vm        # 回滚到 acc-helper-vm (http://<ACC_HELPER_VM_IP>:8000/)
#   bash switch-nginx.sh --status       # 查看当前上游
set -eu

CONF1=/etc/nginx/conf.d/accapi-https.conf
CONF2=/etc/nginx/default.d/accapi.conf
OLD_VM='http://<ACC_HELPER_VM_IP>:8000/'
OLD_TUNNEL='http://127.0.0.1:18000/'
BACKUP_DIR=/etc/nginx/accapi-backup

status() {
  echo "当前 /accapi/ 上游："
  grep -n 'proxy_pass' "$CONF1" | sed 's/^/  conf.d: /'
  grep -n 'proxy_pass' "$CONF2" 2>/dev/null | sed 's/^/  default.d: /' || true
}

switch_to() {
  local new="$1" label="$2"
  mkdir -p "$BACKUP_DIR"
  ts=$(date +%Y%m%d_%H%M%S)
  cp "$CONF1" "$BACKUP_DIR/accapi-https.conf.$ts"
  [ -f "$CONF2" ] && cp "$CONF2" "$BACKUP_DIR/accapi.conf.$ts"
  echo "  已备份到 $BACKUP_DIR/*.$ts"

  # 两个文件里的 /accapi/ location 上游都改（default.d 里的也一并改，保持一致）
  sed -i "s#proxy_pass ${OLD_VM}#proxy_pass ${new}#g; s#proxy_pass ${OLD_TUNNEL}#proxy_pass ${new}#g" "$CONF1"
  [ -f "$CONF2" ] && sed -i "s#proxy_pass ${OLD_VM}#proxy_pass ${new}#g; s#proxy_pass ${OLD_TUNNEL}#proxy_pass ${new}#g" "$CONF2"

  echo "  已改为 $label ($new)"
  nginx -t
  systemctl reload nginx
  echo "  nginx reloaded"
  status
}

case "${1:-}" in
  --to-dgx)
    echo "== 切到 dgx =="
    curl -sf -m 8 -o /dev/null http://127.0.0.1:18000/ && echo "  隧道 18000 可达" || { echo "  !! 隧道 18000 不可达，中止"; exit 1; }
    switch_to "$OLD_TUNNEL" "dgx(隧道)"
    echo
    echo "验证：curl -s -o /dev/null -w '%{http_code}\n' https://isom.xjtu.edu.cn/accapi/"
    ;;
  --to-vm)
    echo "== 回滚到 acc-helper-vm =="
    switch_to "$OLD_VM" "acc-helper-vm"
    ;;
  --status|"")
    status
    ;;
  *)
    echo "用法: $0 [--to-dgx|--to-vm|--status]"; exit 1
    ;;
esac
