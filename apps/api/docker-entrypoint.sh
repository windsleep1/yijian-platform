#!/bin/sh
# 一建通 API 启动引导
# 顺序：等依赖就绪 → 建表（幂等）→ 初始化超管（幂等）→ 启动应用
set -e

echo "[entrypoint] 环境: APP_ENV=${APP_ENV:-local}  PORT=${PORT:-8000}"

# ---- PostgreSQL：**致命** ----
# 理由：库不可用 ⇒ 每个请求都会 500，起一个"能探活但什么都做不了"的进程
# 只会让排障更难。宁可快速失败，让平台把这次部署标记为失败。
echo "[entrypoint] 等待 PostgreSQL ..."
python -m app.cli wait-db --timeout "${DB_WAIT_TIMEOUT:-120}"

# ---- Redis：**非致命**（降级启动）----
# ★★ 这里以前是裸调用，而脚本开头是 `set -e` ⇒ wait-redis 返回 1 会让**整个
#    入口脚本退出**，容器根本起不来 —— 这与 `app/db/base.py` 模块文档里承诺的
#    "Redis 不可用时不阻塞启动（降级：限流关闭、验证码走内存 + 日志）"**直接矛盾**。
#    也就是说：**代码层的设计意图被入口脚本推翻了**。
#    ⇒ 现在把承诺落到实处：等不到就**大声警告后继续**。
#    （触发场景：托管平台的免费档没有 Redis —— Render 默认不提供，
#      需要另配 Upstash / Render Key Value；没有它时应用仍可跑通主流程。）
if python -m app.cli wait-redis --timeout "${REDIS_WAIT_TIMEOUT:-60}"; then
  echo "[entrypoint] Redis 已就绪"
else
  echo "[entrypoint] ⚠️ Redis 不可用 —— **继续启动**（降级：限流关闭、验证码走内存+日志）。"
  echo "[entrypoint]    /api/v1/health 会报 degraded（dependencies.redis=down），这是预期。"
  echo "[entrypoint]    想消除它：给 REDIS_URL 配一个可用的 Redis（如 Upstash 免费档）。"
fi

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
