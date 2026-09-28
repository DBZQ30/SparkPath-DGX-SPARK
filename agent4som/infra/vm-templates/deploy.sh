#!/bin/bash

# Agent for SOM - 自动化部署脚本
# 用法: ./deploy.sh <assistant-name> <branch-name>
# 示例: ./deploy.sh jwc-assistant <branch-name>

set -euo pipefail

ASSISTANT_NAME=$1
BRANCH_NAME=$2
INSTALL_DIR="/opt/agent4som"

if [ -z "$ASSISTANT_NAME" ] || [ -z "$BRANCH_NAME" ]; then
    echo "Usage: $0 <assistant-name> <branch-name>"
    exit 1
fi

# Validate parameters against safe patterns (P0-2 fix)
if ! [[ "$ASSISTANT_NAME" =~ ^[a-zA-Z0-9_/.-]+$ ]]; then
    echo "错误: assistant-name 包含非法字符 (仅允许字母、数字、_ / . -)"
    exit 1
fi
if ! [[ "$BRANCH_NAME" =~ ^[a-zA-Z0-9_/.-]+$ ]]; then
    echo "错误: branch-name 包含非法字符 (仅允许字母、数字、_ / . -)"
    exit 1
fi

echo "开始部署助手: $ASSISTANT_NAME (分支: $BRANCH_NAME)..."

# 1. 基础依赖检查
if ! command -v git &> /dev/null; then
    echo "错误: 未安装 git"
    exit 1
fi

# 2. 代码克隆/更新
if [ ! -d "$INSTALL_DIR" ]; then
    echo "首次部署，克隆仓库..."
    # 历史模板：请替换为实际仓库地址（或设置 REPO_URL 环境变量）
    git clone "${REPO_URL:-https://github.com/OurOrg/agent4som.git}" $INSTALL_DIR
    cd $INSTALL_DIR
else
    echo "更新现有代码..."
    cd $INSTALL_DIR
    git fetch --all
fi

# 3. 切换分支并同步子模块
echo "切换到分支 $BRANCH_NAME..."
git checkout -- "$BRANCH_NAME"
git pull origin $BRANCH_NAME
git submodule update --init --recursive

# 4. 初始化数据目录
echo "初始化数据目录..."
mkdir -p data/sqlite data/chroma data/knowledge data/logs

# 5. 检查环境变量文件
if [ ! -f ".env" ]; then
    echo "警告: .env 文件不存在！请手动创建并配置企微密钥。"
    touch .env
fi

# 6. 安装/更新依赖
echo "安装核心引擎与插件..."
if command -v uv &> /dev/null; then
    uv pip install -e ./hermes
    uv pip install -e ./plugins/aiagent
else
    pip install -e ./hermes
    pip install -e ./plugins/aiagent
fi

# 7. 配置 systemd 服务 (需要 sudo)
echo "配置 systemd 服务..."
SERVICE_FILE="infra/hermes-gateway@.service"
if [ -f "$SERVICE_FILE" ]; then
    sudo cp $SERVICE_FILE /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable hermes-gateway@$ASSISTANT_NAME
    echo "配置完成。请使用 'sudo systemctl start hermes-gateway@$ASSISTANT_NAME' 启动服务。"
else
    echo "错误: 未找到 systemd 服务模板文件"
fi

echo "部署脚本执行完毕。"
