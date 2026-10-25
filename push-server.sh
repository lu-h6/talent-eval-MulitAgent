#!/usr/bin/env bash
# push folder to remote server

# ====================== 你只需要改这里 2 个配置 ======================
# SSH 别名（从 ~/.ssh/config 里来）及 远程项目路径（docker-compose.yml 所在目录）
# SERVER_ALIAS="yq-ai"
# REMOTE_DIR="/root/will/talent-eval-agents"

SERVER_ALIAS="tengxun-claw"
REMOTE_DIR="/root/talent-eval-agents"

# ==================================================================

echo "====================================="
echo "  正在同步代码到 $SERVER_ALIAS ..."
echo "====================================="

# rsync L: 所有软链接都变成真实文件
rsync -avzL --delete \
  --exclude=".venv/" \
  --exclude=".vscode/" \
  --exclude=".github/" \
  --exclude="dist/" \
  --exclude="node_modules/" \
  --exclude="logs/" \
  --exclude="output/" \
  --exclude="sample-data/" \
  --exclude=".env" \
  --exclude="uv.lock" \
  ./ $SERVER_ALIAS:$REMOTE_DIR/


# 检查同步是否成功
if [ $? -ne 0 ]; then
  echo "❌ 同步失败！"
  exit 1
fi
echo "✅ 同步成功！"