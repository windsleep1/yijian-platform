#!/bin/sh
# 一建通 API 启动引导
# 顺序：等依赖就绪 → 建表（幂等）→ 初始化超管（幂等）→ 启动应用
set -e

echo "[entrypoint] 环境: APP_ENV=${APP_ENV:-local}"

echo "[entrypoint] 等待 PostgreSQL ..."
python -m app.cli wait-db --timeout "${DB_WAIT_TIMEOUT:-120}"

echo "[entrypoint] 等待 Redis ..."
python -m app.cli wait-redis --timeout "${REDIS_WAIT_TIMEOUT:-60}"

echo "[entrypoint] 确保数据库结构已就绪 ..."
python -m app.cli init-db

# 幂等重放角色/权限种子。init-db 只在**空库**执行 schema.sql，
# 于是「老数据卷 + 新增角色」拿不到新角色。这行补上这个缺口（对空库是 no-op 的重复执行）。
echo "[entrypoint] 重放 RBAC 种子（补后加角色）..."
python -m app.cli seed-rbac

if [ -n "${ADMIN_INIT_PHONE}" ]; then
  echo "[entrypoint] 初始化超级管理员 ..."
  python -m app.cli seed-admin
else
  echo "[entrypoint] 未配置 ADMIN_INIT_PHONE，跳过超管初始化"
fi

echo "[entrypoint] 启动: $*"
exec "$@"
