#!/bin/bash
# setup-tunnel.sh —— 在 dgx 上安装「到 acc-svr 的 SSH 反向隧道」systemd 用户服务
#
# 作用：把 dgx 的 miniapp-proxy(127.0.0.1:8020) 暴露为 acc-svr 的 127.0.0.1:18000
#       之后 acc-svr nginx 只需把 /accapi/ 的 proxy_pass 改为 http://127.0.0.1:18000/
# 注意：安装后不影响线上（nginx 未改动前无人访问 18000）
#       dgx 上 :8000 被 vLLM 占用，故 miniapp-proxy 用 8020（见 deploy/dgx/README.md）
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

if [ ! -f "$SCRIPT_DIR/accapi-tunnel.service" ]; then
  echo "缺少 $SCRIPT_DIR/accapi-tunnel.service"; exit 1
fi
cp "$SCRIPT_DIR/accapi-tunnel.service" "$UNIT_DIR/accapi-tunnel.service"

echo "########## 1. 单元文件 ##########"
cat "$UNIT_DIR/accapi-tunnel.service"

echo
echo "########## 2. 启用并启动 ##########"
systemctl --user daemon-reload
systemctl --user enable accapi-tunnel.service
systemctl --user restart accapi-tunnel.service
sleep 8
systemctl --user --no-pager --no-legend status accapi-tunnel.service | head -10

echo
echo "########## 3. acc-svr 侧验证端口 ##########"
ssh -n -o BatchMode=yes acc-svr "ss -tlnp 2>/dev/null | grep 18000 || echo '  18000 未监听'"

echo
echo "########## 4. 通过隧道访问 dgx:8020 ##########"
ssh -n -o BatchMode=yes acc-svr "curl -s -m 8 -o /dev/null -w '  acc-svr -> 127.0.0.1:18000 -> HTTP %{http_code}\n' http://127.0.0.1:18000/ || echo '  隧道暂不可用（miniapp-proxy 未启动时返回 502 属正常）'"

echo
echo "########## 5. 开机常驻（linger）##########"
if loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  echo "  linger 已开启"
else
  echo "  需要 root 开启 linger："
  echo "    sudo loginctl enable-linger $USER"
fi

echo
echo "===== DONE tunnel ====="
