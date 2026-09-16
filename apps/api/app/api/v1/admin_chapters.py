"""管理端 · 章节树接口（Batch 4）。

    GET /admin/chapters/tree    章节树（下拉 / 联动用）  —— question:read

**权限为什么用 `question:read` 而不是 `question:create`**
    这份数据是「建题/选题页」的下拉数据源，读题的人（`viewer` 之外的教研、审核）
    都可能打开列表页。若收到 `question:create`，一个只有 `question:read` 的审核岗
    能看列表却拉不到章节下拉，页面直接残废 —— 与 Batch 3 `admin_rbac` 里
    「角色下拉用 user:read 而不是 system:role」是同一个取舍。

**联动语义**：不传 `subject_id` → 返回全部科目各一组（供「科目」下拉 + 默认联动）；
传了就只返回那一组（选中科目后刷新章节下拉）。

TODO(权限归属，Batch 4 之后)：本接口现在挂在 `question:read` 下，理由是**当下它只服务题库页**。
    但"章节树"本身是**科目域**的公共数据，不是题目的附属物。一旦课程模块（课次挂章节）
    或知识点管理也来用同一棵树，就会出现"想读章节但不想给题库权限"的岗位 ——
    那时 `question:read` 就是错的授权粒度，应改为 `subject:read` 或独立权限
    （如 `chapter:read`），并同步调整种子里的角色映射。
    现在**刻意不改**：没有第二个调用方之前，多一个权限码只会让角色矩阵提前变复杂，
    而收益是零。等真有第二个消费方时再改，改动面只有这里 + 种子的一行。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, require_permission
from app.core.response import Envelope, ok
from app.schemas.admin_question import ChapterTreeOut
from app.services import question_service

router = APIRouter(prefix="/admin/chapters", tags=["管理端 · 章节"])


@router.get(
    "/tree",
    response_model=Envelope[ChapterTreeOut],
    summary="章节树",
    description=(
        "需要权限 `question:read`。题库页的科目 → 章节联动下拉数据源。\n\n"
        "- 不传 `subject_id` → 返回全部科目分组（每组带 `subject` 基本信息与 `chapters`）。\n"
        "- 传 `subject_id` → 只返回该科目一组。\n"
        "- `question_count` 是**实时统计**（不含软删除），不是读 `chapters.question_count` "
        "那个冗余列 —— 冗余列的刷新任务还没做，读了会全是 0，反而误导人。\n"
        "- 当前种子的章节都是 `level=1` 的扁平结构，但本接口按 `parent_id` 递归成树；"
        "将来加了二级章节，返回形状不变。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def chapter_tree(
    db: DbSession,
    subject_id: Annotated[
        int | None, Query(description="科目 ID；不传返回全部科目分组")
    ] = None,
) -> dict:
    tree = await question_service.list_chapter_tree(db, subject_id=subject_id)
    return ok(tree.model_dump())
