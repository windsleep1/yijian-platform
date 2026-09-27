"""路由汇总。新增模块只需在这里注册一行。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1 import (
    admin_audit,
    admin_chapters,
    admin_exams,
    admin_imports,
    admin_questions,
    admin_rbac,
    admin_stats,
    admin_users,
    auth,
    health,
)
from app.core.deps import require_admin_session

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)

# ---- ★★ 管理端会话墙（`docs/22` §6.5）：**一处挂**，不散在几十个路由上 ----
# `current_user` 已保证"**会话有效**"（BL-13，登出即时生效）；
# 这一层再加"**这是管理端会话**"（`user_sessions.platform == 'pc'`）——
# **权限码是"能力"，这里要的是"身份"**：把一个纯 C 端账号授上 `question:read`，
# 在加这道墙之前它是**能读后台的**。
#
# ⚠️ 为什么挂在 **router 级**而不是逐个路由写：这里 8 个 admin router、几十个路由，
#    逐个写**一定会漏**；而"漏一个"的症状是"那个接口不需要管理端会话也能读"（**静默**）。
# ⚠️ **顺序**（权限墙 vs 会话墙）：两者**都是拒绝**，谁先谁后对安全性零影响；
#    实际顺序由 FastAPI 的依赖解析决定 —— 既有那几条"无权限账号"的用例**跑一遍看拿到哪个码**，
#    结果记在 `docs/24`（**不在这里猜**）。
api_router.include_router(admin_users.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_rbac.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_audit.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_questions.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_chapters.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_imports.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_exams.router, dependencies=[Depends(require_admin_session)])
api_router.include_router(admin_stats.router, dependencies=[Depends(require_admin_session)])

# 后续批次在此追加：
#   api_router.include_router(subjects.router)      # 科目 / 章节 / 知识点（C 端）
#   api_router.include_router(practice.router)      # 刷题

__all__ = ["api_router"]
