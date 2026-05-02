#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${1:-main}"

cd "$ROOT_DIR"

echo "==> 拉取最新代码：origin/${BRANCH}"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> 检查 .htpasswd"
if [[ ! -f ".htpasswd" ]]; then
  echo "错误：未找到 .htpasswd，请先创建 Nginx Basic Auth 密码文件。"
  exit 1
fi

echo "==> 安装并构建前端"
cd "$ROOT_DIR/frontend"
npm install
npm run build

echo "==> 重建并启动 Docker 服务"
cd "$ROOT_DIR"
docker compose down
docker compose up -d --build

echo "==> 当前服务状态"
docker compose ps

echo "==> 部署完成"
