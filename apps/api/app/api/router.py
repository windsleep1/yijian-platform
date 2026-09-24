"""路由汇总。新增模块只需在这里注册一行。"""

from __future__ import annotations

from fastapi import APIRouter

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

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_rbac.router)  # Batch 3：/admin/roles, /admin/permissions
api_router.include_router(admin_audit.router)  # Batch 3：/admin/audit-logs
api_router.include_router(admin_questions.router)  # Batch 4：/admin/questions CRUD + 批量删除
api_router.include_router(admin_chapters.router)  # Batch 4：/admin/chapters/tree
api_router.include_router(
    admin_imports.router
)  # Batch 5：/admin/imports 题库批量导入管道（7 接口）
# Batch 6：+ /admin/imports/{id}/changes 批次变更日志
api_router.include_router(admin_exams.router)  # Batch 7：/admin/paper-rules 组卷规则 + /admin/exams
#   试卷 CRUD / auto-compose / validate / publish（11 接口）

# Batch 8：/admin/stats 统计看板（5 接口：overview / trends /
#   distributions / funnel / weak-points）
api_router.include_router(admin_stats.router)

# 后续批次在此追加：
#   api_router.include_router(subjects.router)      # 科目 / 章节 / 知识点
#   api_router.include_router(practice.router)      # 刷题

__all__ = ["api_router"]
