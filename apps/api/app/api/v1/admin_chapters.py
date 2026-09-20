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

from app.core.deps import CurrentUserDep, DbSession, require_permission
from app.core.response import Envelope, ok
from app.schemas.admin_question import ChapterTreeOut, KnowledgePointListOut
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
        "将来加了二级章节，返回形状不变。\n"
        "- **数据范围**：不传 `subject_id` 只返回**你有权限的科目**分组；"
        "显式传一个范围外的科目 → `40301`。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def chapter_tree(
    db: DbSession,
    me: CurrentUserDep,
    subject_id: Annotated[
        int | None, Query(description="科目 ID；不传返回全部科目分组")
    ] = None,
) -> dict:
    tree = await question_service.list_chapter_tree(db, viewer=me, subject_id=subject_id)
    return ok(tree.model_dump())


@router.get(
    "/knowledge-points",
    response_model=Envelope[KnowledgePointListOut],
    summary="知识点列表（下拉数据源）",
    description=(
        "需要权限 `question:read`。**组卷「加题」面板按知识点筛题**用的数据源。\n\n"
        "为什么放在 `/admin/chapters` 下：它与章节树是**同一角色** —— 科目域的"
        "「下拉数据源」，服务于题库挑选场景，不是题目的附属物。\n\n"
        "- `subject_id` / `chapter_id` 可叠加筛选；`keyword` 模糊匹配名称与编码。\n"
        "- `question_count` 是**实时统计**（不含软删除），不是读"
        "`knowledge_points.question_count` 冗余列 —— 那个列还没刷新任务，读了全是 0。\n"
        "- **已删除的章节/知识点会被硬过滤**：列出一个已删除的知识点等于"
        "给用户一个必然空手而归的筛选项。\n\n"
        "> 关于权限粒度：本文件顶部的 TODO 说「等出现第二个消费方就该考虑改粒度」。\n"
        "> 组卷加题确实算第二个消费方，但它**仍然是在挑题**（消费者还是题库域的），\n"
        "> 所以 `question:read` 依然是对的。真正的触发条件是「想读章节但**没有**题库权限」的岗位。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def knowledge_points(
    db: DbSession,
    me: CurrentUserDep,
    subject_id: Annotated[int | None, Query(description="科目 ID")] = None,
    chapter_id: Annotated[int | None, Query(description="章节 ID")] = None,
    keyword: Annotated[str | None, Query(max_length=80, description="名称 / 编码模糊匹配")] = None,
) -> dict:
    out = await question_service.list_knowledge_points(
        db, viewer=me, subject_id=subject_id, chapter_id=chapter_id, keyword=keyword
    )
    return ok(out.model_dump())
