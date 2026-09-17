"""管理端 · 试卷与组卷接口（Batch 7 Pass 1）。

    GET    /admin/paper-rules              组卷规则列表          —— exam:read
    POST   /admin/paper-rules              新建组卷规则          —— exam:create
    PUT    /admin/paper-rules/{id}         编辑组卷规则          —— exam:create
    DELETE /admin/paper-rules/{id}         删除组卷规则（硬删）   —— exam:create
    GET    /admin/exams                    试卷列表              —— exam:read
    POST   /admin/exams                    创建试卷              —— exam:create
    POST   /admin/exams/{id}/auto-compose  规则自动组卷          —— exam:create
    POST   /admin/exams/{id}/validate      卷面校验              —— exam:read
    POST   /admin/exams/{id}/publish       发布（版本锁定）       —— exam:publish
    GET    /admin/exams/{id}               试卷详情              —— exam:read
    PUT    /admin/exams/{id}               编辑试卷              —— exam:create

## 两个刻意的决定

**1. 不新增权限码。** 库里 `exam` 模块只有 `exam:read` / `exam:create` / `exam:publish` / `exam:grade`
四条（`db/schema.sql` 权限种子 id 201~204）。组卷规则是"试卷的模板"，
归到 `exam:create` 名下比新增 `paper_rule:*` 更省事，也不必改种子。
`researcher`（教研）与 `teacher` 的模块范围里都含 `exam`，所以都能用。

**2. `/{exam_id}` 放在最后。** 通配路径参数会与固定段抢匹配，把它排在所有固定路径之后，
是 Batch 5 就定下的规矩（见 `admin_imports.py` 的同款注释）。
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
from app.schemas.admin_exam import (
    ExamComposeIn,
    ExamComposeOut,
    ExamCreateIn,
    ExamDetail,
    ExamListItem,
    ExamPublishIn,
    ExamPublishOut,
    ExamUpdateIn,
    ExamValidateOut,
    PaperRuleCreateIn,
    PaperRuleDeleteOut,
    PaperRuleOut,
    PaperRuleUpdateIn,
)
from app.services import exam_service

router = APIRouter(tags=["管理端 · 试卷与组卷"])


# =====================================================================
# 组卷规则 CRUD
# =====================================================================


@router.get(
    "/admin/paper-rules",
    response_model=Envelope[Page[PaperRuleOut]],
    summary="组卷规则列表",
    description=(
        "需要权限 `exam:read`。\n\n"
        "- 数据范围收口：教研只看得到自己科目范围内的规则（`user_roles.scope_type/scope_id`）。\n"
        "- `planned_count` / `planned_score` 由 `rules` 推导，列表页直接展示，不用前端再算。\n"
        "- `can_edit` / `can_delete` 告诉前端按钮可用性（范围外的规则是只读的）。"
    ),
    dependencies=[Depends(require_permission("exam:read"))],
)
async def list_paper_rules(
    db: DbSession,
    me: CurrentUserDep,
    page_info: Annotated[tuple[int, int], Depends(pagination)],
    subject_id: Annotated[int | None, Query(description="科目 ID")] = None,
    status: Annotated[Literal["on", "off"] | None, Query(description="启用状态")] = None,
    rule_type: Annotated[str | None, Query(alias="type", max_length=24, description="试卷类型")] = None,
) -> dict:
    page, page_size = page_info
    items, total = await exam_service.list_paper_rules(
        db, viewer=me, page=page, page_size=page_size,
        subject_id=subject_id, status=status, rule_type=rule_type,
    )
    return ok(
        paginate([i.model_dump() for i in items], page=page, page_size=page_size, total=total)
    )


@router.post(
    "/admin/paper-rules",
    response_model=Envelope[PaperRuleOut],
    summary="新建组卷规则",
    description=(
        "需要权限 `exam:create`。\n\n"
        "`rules` 是**约束数组**，每条描述一类题的抽取条件：\n"
        "```json\n"
        "{\"type\":\"single\",\"count\":60,\"score\":1,\"difficulty\":[2,4],\"kp_ids\":[123],\"year\":null}\n"
        "```\n"
        "- `difficulty` 是**闭区间** `[min,max]`，取值 1~5；不传 = 不限。\n"
        "- `kp_ids` / `chapter_ids` 任一命中即可；空数组 = 不限。\n"
        "- ⚠️ `case_sub`（案例小问）**不能单独抽题** —— 它必须跟随父题（案例背景）一起进卷，"
        "传了会被 `40001` 拒绝。抽案例题请用 `case`。\n"
        "- 规则只描述**要什么**，能不能凑够由组卷时决定，凑不够会回传 `shortfalls`。"
    ),
    dependencies=[Depends(require_permission("exam:create"))],
)
async def create_paper_rule(
    payload: PaperRuleCreateIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await exam_service.create_paper_rule(
        db, actor=me, actor_name=me.display_name, payload=payload, ip=client_ip(request)
    )
    return ok(out.model_dump(), message="组卷规则已创建")


@router.put(
    "/admin/paper-rules/{rule_id}",
    response_model=Envelope[PaperRuleOut],
    summary="编辑组卷规则",
    description=(
        "需要权限 `exam:create`。未传的字段沿用现值（部分更新）。\n\n"
        "- 改 `rules` 是**整体替换**，不是合并。\n"
        "- `status=off` 可停用规则（组卷时会拒绝使用停用规则，但已组好的卷不受影响）。"
    ),
    dependencies=[Depends(require_permission("exam:create"))],
)
async def update_paper_rule(
    rule_id: int,
    payload: PaperRuleUpdateIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await exam_service.update_paper_rule(
        db, actor=me, actor_name=me.display_name, rule_id=rule_id,
        payload=payload, ip=client_ip(request),
    )
    return ok(out.model_dump(), message="已保存")


@router.delete(
    "/admin/paper-rules/{rule_id}",
    response_model=Envelope[PaperRuleDeleteOut],
    summary="删除组卷规则",
    description=(
        "需要权限 `exam:create`。\n\n"
        "⚠️ 这是**硬删除** —— `paper_rules` 表没有 `is_deleted` 列。\n"
        "- 已用它组过的试卷**不受影响**（试卷各自持有自己的题目与分段，没有外键依赖）。\n"
        "- 想保留规则但不再使用，请改 `status=off`，不要删。"
    ),
    dependencies=[Depends(require_permission("exam:create"))],
)
async def delete_paper_rule(
    rule_id: int, request: Request, db: DbSession, me: CurrentUserDep
) -> dict:
    out = await exam_service.delete_paper_rule(
        db, actor=me, rule_id=rule_id, ip=client_ip(request)
    )
    return ok(out.model_dump(), message="规则已删除")


# =====================================================================
# 试卷
# =====================================================================


@router.get(
    "/admin/exams",
    response_model=Envelope[Page[ExamListItem]],
    summary="试卷列表",
    description=(
        "需要权限 `exam:read`。\n\n"
        "- 筛选：`subject_id` / `type` / `status` / `keyword`（标题或卷号模糊）。\n"
        "- 默认只返回未删除的卷；`include_deleted=true` 带出已删除的。\n"
        "- 数据范围收口：教研只看得到自己科目范围内的卷。"
    ),
    dependencies=[Depends(require_permission("exam:read"))],
)
async def list_exams(
    db: DbSession,
    me: CurrentUserDep,
    page_info: Annotated[tuple[int, int], Depends(pagination)],
    subject_id: Annotated[int | None, Query(description="科目 ID")] = None,
    exam_type: Annotated[str | None, Query(alias="type", max_length=24, description="试卷类型")] = None,
    status: Annotated[str | None, Query(max_length=16, description="状态")] = None,
    keyword: Annotated[str | None, Query(max_length=100, description="标题/卷号模糊搜索")] = None,
    include_deleted: Annotated[bool, Query(description="是否带出已删除的卷")] = False,
) -> dict:
    page, page_size = page_info
    items, total = await exam_service.list_exams(
        db, viewer=me, page=page, page_size=page_size, subject_id=subject_id,
        exam_type=exam_type, status=status, keyword=keyword, include_deleted=include_deleted,
    )
    return ok(
        paginate([i.model_dump() for i in items], page=page, page_size=page_size, total=total)
    )


@router.post(
    "/admin/exams",
    response_model=Envelope[ExamDetail],
    summary="创建试卷",
    description=(
        "需要权限 `exam:create`。新建的卷子是 `draft` 草稿态，题库里**一道题都还没挂**。\n\n"
        "`sections` 是卷面分段（题型 / 计划题数 / 每题分值）。允许先建空卷再加题，"
        "所以 `sections` 可以为空 —— 之后的 `auto-compose` 会按规则重写分段。\n\n"
        "分段里的 `question_count` 是**计划题数**，同时也是校验契约："
        "如果实际只填了 N 道而计划是 M 道，`validate` 会报 `SECTION_NOT_FILLED` 错误、"
        "`publish` 会被挡住。要发这种卷，就把分段题数改成实际值 —— 那等于明确认可卷面构成。"
    ),
    dependencies=[Depends(require_permission("exam:create"))],
)
async def create_exam(
    payload: ExamCreateIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    detail = await exam_service.create_exam(
        db, actor=me, actor_name=me.display_name, payload=payload, ip=client_ip(request)
    )
    return ok(detail.model_dump(), message="试卷已创建")


@router.post(
    "/admin/exams/{exam_id}/auto-compose",
    response_model=Envelope[ExamComposeOut],
    summary="规则自动组卷",
    description=(
        "需要权限 `exam:create`。按规则从**已发布**题库抽题填卷。\n\n"
        "**规则来源优先级**：`rules`（内联） > `rule_id`（规则表） > 试卷已有分段。\n\n"
        "**抽题算法**（`docs/05 §3.2`）——每条规则逐级放宽，每步的候选数都记下来：\n"
        "1. `exact` 精确匹配（题型 + 难度 + 知识点/章节 + 年份）\n"
        "2. `relax_difficulty` 放宽难度\n"
        "3. `relax_scope` 只留题型\n\n"
        "放宽到底仍不够 → 回传 `shortfalls: [{rule, need, got, missing, reason}]`，"
        "**绝不用其它题目顶替**。这是本接口最重要的性质：一个静默凑数的组卷器会让教研"
        "失去对卷面质量的掌控。\n\n"
        "**加权采样**：优先选从未进过任何试卷的题（权重 4.0），其次按 `1/(1+使用次数)` 递减。\n\n"
        "- `seed`：传同一个种子 + 同一份库 → 同一张卷，便于复现与验收。\n"
        "- `replace=true`（默认）会清空卷面已有题目再组；`false` 表示追加（重复题自动跳过）。\n"
        "- `apply_sections=false` 时把题塞进**已有**分段（按题型找余量），不重写分段。\n"
        "- 已发布的卷子不能重新组卷 → `40901`。"
    ),
    dependencies=[Depends(require_permission("exam:create"))],
)
async def auto_compose_exam(
    exam_id: int,
    payload: ExamComposeIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await exam_service.compose_exam(
        db, actor=me, actor_name=me.display_name, exam_id=exam_id,
        payload=payload, ip=client_ip(request),
    )
    # 有缺口时 message 里已经写明，这里不再把 code 改成非 0 ——
    # "组卷成功但有缺口"是**成功**，不是失败：缺口是正常业务结果，靠数据表达而非错误码。
    return ok(out.model_dump(), message=out.message)


@router.post(
    "/admin/exams/{exam_id}/validate",
    response_model=Envelope[ExamValidateOut],
    summary="卷面校验",
    description=(
        "需要权限 `exam:read`。**只读**，不改任何数据；返回 `ok` 与两级问题清单。\n\n"
        "**error（阻止发布）**：\n"
        "- `NO_QUESTIONS` 卷面一道题都没有\n"
        "- `SECTION_NOT_FILLED` 分段计划题数与实际不符\n"
        "- `SECTION_SCORE_MISMATCH` 分段分值 ≠ 计划题数 × 每题分\n"
        "- `TOTAL_SCORE_MISMATCH` / `TOTAL_COUNT_MISMATCH` 试卷登记的总分/题量与卷面实际不符\n"
        "- `DUPLICATE_QUESTION` 同一题重复出现\n"
        "- `QUESTION_NOT_PUBLISHED` 卷面含草稿等非已发布题目\n"
        "- `QUESTION_DELETED` 卷面含已归档题目\n\n"
        "**warning（允许发布，仅提示）**：\n"
        "- `COMPOSE_SHORTFALL` 上次组卷有缺口\n"
        "- `NO_PASS_SCORE` 未设及格线\n"
        "- `VERSION_DRIFT` 有题目在发布后被改过（试卷仍按锁定版本展示与判分）"
    ),
    dependencies=[Depends(require_permission("exam:read"))],
)
async def validate_exam(exam_id: int, db: DbSession, me: CurrentUserDep) -> dict:
    out = await exam_service.validate_exam(db, exam_id=exam_id, viewer=me)
    return ok(out.model_dump(), message="卷面校验通过" if out.ok else "卷面校验未通过")


@router.post(
    "/admin/exams/{exam_id}/publish",
    response_model=Envelope[ExamPublishOut],
    summary="发布试卷（版本锁定）",
    description=(
        "需要权限 `exam:publish`。\n\n"
        "**发布前强制走 `validate`**：只要有一个 error 级问题就 `40901` 拒绝，"
        "整个操作不落库。`warning` 不阻止发布。\n\n"
        "**版本锁定**（`docs/07 §6.4`）：发布时把每道题的当前 `version` 快照进"
        "`rule_config.question_locks`。此后：\n"
        "- 题目被改动 → 试卷详情仍展示**锁定版本**的题干，并标 `version_drift=true`；\n"
        "- 校验会给出 `VERSION_DRIFT` warning；\n"
        "- 重新组卷会清空锁定（卷面都换了，旧锁定没意义）。\n\n"
        "> 这是考试类产品的严肃性要求：考生昨天考了 80 分，今天改了题，成绩就成了悬案。"
    ),
    dependencies=[Depends(require_permission("exam:publish"))],
)
async def publish_exam(
    exam_id: int,
    payload: ExamPublishIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await exam_service.publish_exam(
        db, actor=me, actor_name=me.display_name, exam_id=exam_id,
        payload=payload, ip=client_ip(request),
    )
    return ok(out.model_dump(), message=out.message)


# ---- 通配路径参数放最后（不与其他固定段抢匹配） ----


@router.get(
    "/admin/exams/{exam_id}",
    response_model=Envelope[ExamDetail],
    summary="试卷详情",
    description=(
        "需要权限 `exam:read`。\n\n"
        "返回：卷面分段（含每段的题）+ 逐题信息 + 上次组卷缺口 + 校验结果。\n\n"
        "- 每道题带 `locked_version`（发布时锁定的版本）与 `current_version`（当前版本）；"
        "不一致时 `version_drift=true`，且 `stem_preview` 用的是**锁定版本**的题干。\n"
        "- `shortfalls` 非空 = 上次组卷有缺口，前端据此给警告。\n"
        "- `validation` 是即时的校验结果，详情页可直接展示。\n"
        "- 不存在的 id → `40401`；范围外的卷 → `40301`。"
    ),
    dependencies=[Depends(require_permission("exam:read"))],
)
async def get_exam(exam_id: int, db: DbSession, me: CurrentUserDep) -> dict:
    detail = await exam_service.get_exam_detail(db, exam_id=exam_id, viewer=me)
    return ok(detail.model_dump())


@router.put(
    "/admin/exams/{exam_id}",
    response_model=Envelope[ExamDetail],
    summary="编辑试卷",
    description=(
        "需要权限 `exam:create`。未传的字段沿用现值。\n\n"
        "- 传 `sections` 会**整体替换**分段，并清空卷面题目（分段变了，题目归属就失效了）。\n"
        "- 已发布的卷子不允许改卷面结构 → `40901`（除非发布时传了 "
        "`allow_edit_after_publish=true`）。\n"
        "- 展示性字段（标题 / 简介 / 时长）在任何状态下都可改。"
    ),
    dependencies=[Depends(require_permission("exam:create"))],
)
async def update_exam(
    exam_id: int,
    payload: ExamUpdateIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    detail = await exam_service.update_exam(
        db, actor=me, actor_name=me.display_name, exam_id=exam_id,
        payload=payload, ip=client_ip(request),
    )
    return ok(detail.model_dump(), message="已保存")
