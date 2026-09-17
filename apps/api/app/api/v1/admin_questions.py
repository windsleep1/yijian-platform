"""管理端 · 题库 CRUD 接口（Batch 4）。

    GET    /admin/questions                  题目列表（筛选 + 分页 + 排序）  —— question:read
    GET    /admin/questions/{id}             题目详情（选项/答案/解析/版本）  —— question:read
    POST   /admin/questions                  新建题目                        —— question:create
    PUT    /admin/questions/{id}             编辑题目（乐观锁 version+1）     —— question:update
    DELETE /admin/questions/{id}             软删除单题                      —— question:delete
    POST   /admin/questions/batch-delete      批量软删除                      —— question:delete

**路由顺序**：`/batch-delete` 必须注册在 `/{question_id}` **之前**吗？
不用 —— 前者是 `POST`、后者是 `GET`/`PUT`/`DELETE`，方法不同不会串。
真正要小心的是 `POST /admin/questions`（新建）与 `POST /admin/questions/batch-delete`，
路径一个是 `/admin/questions`、一个是 `/admin/questions/batch-delete`，前缀不同也不会串。
FastAPI 按「方法 + 路径」精确匹配，这里没有歧义。

**为什么这些接口都返回完整详情/结果而不是 204**：
B 端页面在写操作后要**立即就地更新**（不然得整页刷新、丢筛选条件）。
新建/编辑直接返回新的 `QuestionDetail`；删除返回被删的 id 与新 version。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import (
    CurrentUserDep,
    DbSession,
    client_ip,
    pagination,
    require_permission,
)
from app.core.response import Envelope, Page, ok, paginate
from app.schemas.admin_question import (
    QStatus,
    QType,
    QuestionBatchDeleteIn,
    QuestionBatchDeleteOut,
    QuestionCreateIn,
    QuestionDeleteOut,
    QuestionDetail,
    QuestionListItem,
    QuestionRestoreOut,
    QuestionUpdateIn,
)
from app.services import question_service

router = APIRouter(prefix="/admin/questions", tags=["管理端 · 题库"])


@router.get(
    "",
    response_model=Envelope[Page[QuestionListItem]],
    summary="题目列表",
    description=(
        "需要权限 `question:read`。\n\n"
        "- **默认只返回未删除的题**（`is_deleted=false`）。要连软删除的一起看，"
        "传 `include_deleted=true`（对应列表页顶部的「显示已归档」开关）。\n"
        "- 筛选支持：`subject_id` / `chapter_id` / `knowledge_point_id` / `type` / "
        "`difficulty` / `status` / `keyword`。`keyword` 同时模糊匹配题干、关键词与富文本题干。\n"
        "- `order_by` 走白名单（`updated_at` / `created_at` / `difficulty` / `id`），"
        "**不接受列名直传**，避免变成注入面。\n"
        "- 排序末尾恒定追加 `q.id`，否则同一时间戳的行在翻页时会重复或漏项。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def list_questions(
    db: DbSession,
    me: CurrentUserDep,
    page_info: Annotated[tuple[int, int], Depends(pagination)],
    subject_id: Annotated[int | None, Query(description="科目 ID")] = None,
    chapter_id: Annotated[int | None, Query(description="章节 ID")] = None,
    knowledge_point_id: Annotated[
        int | None, Query(description="知识点 ID（组卷加题时按知识点挑题用）")
    ] = None,
    qtype: Annotated[QType | None, Query(alias="type", description="题型")] = None,
    difficulty: Annotated[int | None, Query(ge=1, le=5, description="难度 1~5")] = None,
    status: Annotated[QStatus | None, Query(description="业务状态")] = None,
    keyword: Annotated[str | None, Query(max_length=100, description="题干/关键词模糊搜索")] = None,
    include_deleted: Annotated[
        bool, Query(description="是否带出已归档（软删除）的题，默认 false")
    ] = False,
    order_by: Annotated[
        Literal["updated_at", "created_at", "difficulty", "id"], Query(description="排序字段")
    ] = "updated_at",
    order: Annotated[Literal["asc", "desc"], Query(description="排序方向")] = "desc",
) -> dict:
    page, page_size = page_info
    items, total = await question_service.list_questions(
        db,
        current_user=me,  # 数据的可见范围由服务层的 apply_data_scope 钩子决定
        page=page,
        page_size=page_size,
        subject_id=subject_id,
        chapter_id=chapter_id,
        knowledge_point_id=knowledge_point_id,
        qtype=qtype,
        difficulty=difficulty,
        status=status,
        keyword=keyword,
        include_deleted=include_deleted,
        order_by=order_by,
        order=order,
    )
    return ok(
        paginate(
            [i.model_dump() for i in items],
            page=page,
            page_size=page_size,
            total=total,
        )
    )


@router.post(
    "",
    response_model=Envelope[QuestionDetail],
    summary="新建题目",
    description=(
        "需要权限 `question:create`。\n\n"
        "- **答案由选项推导**：`single` / `multiple` 题把正确选项的 `is_correct` 置 true 即可，"
        "**不单独传答案**。这样「多选题答案必须都在选项里」在结构上不可能违反。\n"
        "- `single` 必须恰好 1 个正确答案；`multiple` 至少 2 个；`judge` 用 `judge_answer` 表示对错。\n"
        "- 选项数量 2~8，标号不可重复。\n"
        "- 内容重复（归一化后题干 + 选项完全一致）→ `40901`。\n"
        "- 合规：`source_type` 非 `self` 时必须给 `source_name`；`authorized` 必须给 `source_license`。\n"
        "- 成功后版本号为 **v1**，并写入一条 `content_change_logs`（action=create）。"
    ),
    dependencies=[Depends(require_permission("question:create"))],
)
async def create_question(
    payload: QuestionCreateIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    detail = await question_service.create_question(
        db,
        actor=me,
        actor_name=me.display_name,
        payload=payload,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(detail.model_dump(), message="题目已创建")


@router.post(
    "/batch-delete",
    response_model=Envelope[QuestionBatchDeleteOut],
    summary="批量软删除题目",
    description=(
        "需要权限 `question:delete`。单次最多 200 条。\n\n"
        "- 去重后逐条判断：**不存在 / 已是删除态** 的进 `skipped`，不报错。"
        "批量操作不该因为其中一条有问题就整批失败。\n"
        "- 整批共用一个 `batch_id`（写入变更日志），便于追溯「谁在什么时候批量删了什么」。"
    ),
    dependencies=[Depends(require_permission("question:delete"))],
)
async def batch_delete_questions(
    payload: QuestionBatchDeleteIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    result = await question_service.batch_soft_delete(
        db,
        actor=me,
        actor_name=me.display_name,
        ids=payload.ids,
        reason=payload.reason,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(result.model_dump(), message=f"已删除 {result.deleted} 道题")


@router.get(
    "/{question_id}",
    response_model=Envelope[QuestionDetail],
    summary="题目详情",
    description=(
        "需要权限 `question:read`。\n\n"
        "- 不存在的 id → `40401`（**不是** 200 + 空对象）。\n"
        "- 软删除的题**仍可查看**（详情页要能展示「这条已归档」），`is_deleted=true` 会带出去。\n"
        "- **历史版本内联在详情里**（最新在前，最多 10 条，只读；本批不做回滚）。\n"
        "- `can_edit` / `can_delete` 告诉前端按钮可用性；`editable=false` 表示该题型本批不可编辑"
        "（案例题/主观题）。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def get_question(question_id: int, db: DbSession, me: CurrentUserDep) -> dict:
    detail = await question_service.get_question_detail(
        db, question_id=question_id, viewer=me
    )
    return ok(detail.model_dump())


@router.put(
    "/{question_id}",
    response_model=Envelope[QuestionDetail],
    summary="编辑题目",
    description=(
        "需要权限 `question:update`。**乐观锁**：必须回传 `version`，"
        "与库中不一致 → `40901`（说明有人先改过，请刷新后重试）。不做静默覆盖。\n\n"
        "- 保存成功后 `version + 1`，写 `question_versions` 快照与 `content_change_logs`"
        "（action=update，含字段级 `{before, after}` diff）。\n"
        "- 未传的字段沿用库中现值；传 `options` 则整体替换选项。\n"
        "- 校验针对**合并后的最终态**：因为改 `type`（单选↔多选）会整体改变选项合法性规则。\n"
        "- 已归档（软删除）的题不可编辑 → `40001`。"
    ),
    dependencies=[Depends(require_permission("question:update"))],
)
async def update_question(
    question_id: int,
    payload: QuestionUpdateIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    detail = await question_service.update_question(
        db,
        actor=me,
        actor_name=me.display_name,
        question_id=question_id,
        payload=payload,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(detail.model_dump(), message="已保存")


@router.delete(
    "/{question_id}",
    response_model=Envelope[QuestionDeleteOut],
    summary="删除题目（软删除）",
    description=(
        "需要权限 `question:delete`。\n\n"
        "- 软删除 `is_deleted=true`，版本号 +1，写 `content_change_logs`（action=delete）。"
        "记录仍在库里，列表页「显示已归档」开关能看到。\n"
        "- 已删除的再删 → `40001`；不存在的 id → `40401`。"
    ),
    dependencies=[Depends(require_permission("question:delete"))],
)
async def delete_question(
    question_id: int,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
    reason: Annotated[str | None, Query(max_length=200, description="删除原因")] = None,
) -> dict:
    result = await question_service.soft_delete_question(
        db,
        actor=me,
        actor_name=me.display_name,
        question_id=question_id,
        reason=reason,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(result.model_dump(), message="已删除")


@router.post(
    "/{question_id}/restore",
    response_model=Envelope[QuestionRestoreOut],
    summary="恢复题目",
    description=(
        "需要权限 `question:delete`（恢复与删除是**同一个权责**：能归档的人才能解除归档，\n"
        "所以复用权限码，不新增 `question:restore`）。\n\n"
        "**幂等**：题目本来就没被删除 → `code=0` + `already_active=true`，"
        "**不报错也不产生写入**。\n\n"
        "**恢复前会校验关联数据有效性**，任何一条不满足就拒绝（`40901`）并说明原因：\n"
        "- 题目挂的 `chapter_id` 指向的章节已被删除 / 不存在\n"
        "- `knowledge_point_id` 指向的知识点已被删除 / 不存在\n"
        "- 知识点**所属的章节**已被删除 / 不存在\n\n"
        "> 题目的章节/知识点是**弱引用**，章节被软删除时数据库不会拦，题目仍「挂」在\n"
        "> 已删除的节点上。如果直接恢复，这道题就会出现在不存在的章节里 ——\n"
        "> 按章节筛选时既不属于任何章节、又占着列表位置，是最难查的一类脏数据。\n"
        "> 所以**宁可拒绝并说清楚，也不静默恢复到悬空引用上**。\n\n"
        "恢复后 `version + 1` 并写 `content_change_logs`（action=restore，"
        "diff 为 `{is_deleted: true → false}`）。"
    ),
    dependencies=[Depends(require_permission("question:delete"))],
)
async def restore_question(
    question_id: int,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    result = await question_service.restore_question(
        db,
        actor=me,
        actor_name=me.display_name,
        question_id=question_id,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(result.model_dump(), message=result.message)
