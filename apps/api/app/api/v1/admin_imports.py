"""管理端 · 题库批量导入管道（Batch 5 Pass 1）。

六步流程（严格对齐 `docs/07-题库合规与导入规范.md` §4.3）：

    POST   /admin/imports/upload             ① 上传建批次（不写任何题）   —— question:import
    POST   /admin/imports/{id}/validate      ② 逐行校验（dry-run，不写题） —— question:import
    GET    /admin/imports/{id}               ③ 查状态 + 统计 + 错误报告    —— question:read
    POST   /admin/imports/{id}/execute       ④ 执行导入（整批事务）        —— question:import
    POST   /admin/imports/{id}/publish       ⑤ draft → published          —— question:publish
    POST   /admin/imports/{id}/rollback      ⑥ 整批回滚                   —— question:rollback
    GET    /admin/imports                    ⑦ 批次列表                    —— question:read

## 为什么是一条七步的"管道"而不是一个大接口

批量导入是**不可逆写操作**，把"上传 / 试算 / 确认 / 落库 / 发布 / 撤销"拆成独立接口，
教研才有机会在第 ③ 步（`GET /{id}`）看到"第 57 行 answer 不在选项里"之后**先改文件再重传**，
而不是一头撞进写库。每一道闸都对应一个能被审计的动作（见 `audit_service`）。

## 权限为什么这么分

- 上传/校验/执行 = `question:import`（"批量导入"这一件事的三个阶段，同一个权限）
- 发布 = `question:publish`（把草稿推向线上题库，权责不同）
- 回滚 = `question:rollback`（docs/07 §4.3 ⑥ 明确要求；`admin` 角色刻意没有这个权限，见 schema §12）
- 查看 = `question:read`

路由层**只做**「参数解析 + 权限门 + 调 service + 套信封」，不写任何业务规则 ——
规则全在 `app/services/import_service.py`（可单测、不依赖 FastAPI）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from app.core.deps import (
    CurrentUserDep,
    DbSession,
    client_ip,
    pagination,
    require_permission,
)
from app.core.errors import bad_request
from app.core.response import Envelope, Page, ok, paginate
from app.schemas.admin_import import (
    ImportBatchDetail,
    ImportBatchOut,
    ImportExecuteIn,
    ImportExecuteOut,
    ImportMode,
    ImportPublishIn,
    ImportRollbackIn,
    ImportRollbackOut,
    ImportUploadOut,
)
from app.services import import_service

router = APIRouter(prefix="/admin/imports", tags=["管理端 · 题库导入"])

# 文件扩展名 -> 内部 file_type。**刻意不含 xlsx**：本批不做 xlsx 解析
# （docs/07 §4.1 把 xlsx 列为"推荐"，但需要 openpyxl + 富文本受限的额外规则，
#  留到下一批；这里显式拒绝并给出可执行的替代建议，而不是静默当 csv 读）。
_EXT_TO_TYPE = {".csv": "csv", ".json": "json"}


def _resolve_file_type(file_name: str) -> str:
    lower = (file_name or "").lower()
    for ext, ftype in _EXT_TO_TYPE.items():
        if lower.endswith(ext):
            return ftype
    if lower.endswith((".xlsx", ".xls")):
        raise bad_request(
            "本批暂不支持 Excel（xlsx/xls）。请在 Excel 里「另存为 → CSV UTF-8（逗号分隔）」"
            "后重新上传，或导出为 .json。",
            40001,
        )
    raise bad_request("只支持 .csv 与 .json 两种文件", 40001)


# =====================================================================
# ⑦ 批次列表  —— 放在 /{batch_id} 之前注册，避免路径参数把 "upload" 吃掉
# =====================================================================


@router.get(
    "",
    response_model=Envelope[Page[ImportBatchOut]],
    summary="导入批次列表",
    description=(
        "需要权限 `question:read`。按创建时间倒序。\n\n"
        "- `status` 可选过滤：`pending` / `validating` / `importing` / `done` / `failed` / `rolled_back`。\n"
        "- 每条都带 `can_execute` / `can_publish` / `can_rollback`，**与路由上的权限门严格一致**，"
        "前端直接据此置灰按钮即可，不必自己再推一遍权限。\n"
        "- `can_execute` 还会在**该批已执行过**时变 false —— 同一批不允许执行两次。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def list_imports(
    db: DbSession,
    me: CurrentUserDep,
    page_info: Annotated[tuple[int, int], Depends(pagination)],
    status: Annotated[str | None, Query(description="按批次状态过滤")] = None,
) -> dict:
    page, page_size = page_info
    items, total = await import_service.list_batches(
        db, viewer=me, page=page, page_size=page_size, status=status
    )
    return ok(
        paginate(
            [i.model_dump() for i in items], page=page, page_size=page_size, total=total
        )
    )


# =====================================================================
# ① 上传
# =====================================================================


@router.post(
    "/upload",
    response_model=Envelope[ImportUploadOut],
    summary="上传题库文件（建批次）",
    description=(
        "需要权限 `question:import`。**multipart/form-data**，字段：\n\n"
        "- `file`：必传，`.csv`（UTF-8 with BOM 亦可）或 `.json`（数组 / `{\"questions\":[...]}`）。"
        "**不支持 xlsx** —— 上传前请另存为 CSV。\n"
        "- `subject_id`：可选，批次归属科目；会与调用者数据范围求交集。\n"
        "- `source_type`：批次默认来源类型（行内未填时兜底），默认 `self`。\n"
        "- `license_note`：授权/来源说明，写入批次留痕。\n"
        "- `mode`：`insert`（默认，命中同内容题则跳过）或 `upsert`（命中则更新）。\n"
        "- `auto_publish`：默认 false。true 表示 execute 成功后自动发布。\n\n"
        "**本接口不写任何题目**，只创建 `import_batches(status=pending)` 并返回 `batch_no`。\n"
        "单文件上限 20MB、单批上限 20000 行。"
    ),
    dependencies=[Depends(require_permission("question:import"))],
)
async def upload_import(
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
    file: Annotated[UploadFile, File(description="题库文件（csv / json）")],
    subject_id: Annotated[int | None, Form(description="批次科目 ID")] = None,
    source_type: Annotated[str, Form(description="批次默认来源类型")] = "self",
    license_note: Annotated[str | None, Form(max_length=500, description="授权/来源说明")] = None,
    mode: Annotated[ImportMode, Form(description="insert=跳过重复；upsert=更新已存在")] = "insert",
    auto_publish: Annotated[bool, Form(description="导入后是否自动发布")] = False,
) -> dict:
    file_name = file.filename or "upload"
    file_type = _resolve_file_type(file_name)

    if source_type not in import_service.SOURCE_TYPES:
        raise bad_request(
            f"source_type 非法：{source_type}（可选：{'/'.join(import_service.SOURCE_TYPES)}）",
            40001,
        )

    # 先按声明的体积拦一道，避免把超大文件整个读进内存
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > import_service.MAX_FILE_BYTES:
        raise bad_request(
            f"文件过大：{declared / 1048576:.1f}MB，上限 "
            f"{import_service.MAX_FILE_BYTES // 1048576}MB",
            40001,
        )
    content = await file.read()
    if not content:
        raise bad_request("文件是空的", 40001)

    out = await import_service.create_batch(
        db,
        actor=me,
        actor_name=me.display_name,
        file_name=file_name,
        content=content,
        file_type=file_type,
        subject_id=subject_id,
        source_type=source_type,
        license_note=license_note,
        mode=mode,
        auto_publish=auto_publish,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(out.model_dump(), message=out.message or "上传成功")


# =====================================================================
# ② 校验
# =====================================================================


@router.post(
    "/{batch_id}/validate",
    response_model=Envelope[ImportBatchDetail],
    summary="逐行校验（试算）",
    description=(
        "需要权限 `question:import`。**dry-run：一行都不会写进题库**，"
        "只把每行的判定与错误落进 `import_items`。\n\n"
        "每行的 `action` 取值：\n\n"
        "- `insert` 将通过的内容记为待插入；`update` upsert 模式命中库内同内容题；\n"
        "- `duplicate` 命中重复（insert 模式跳过，或文件内自身重复）；\n"
        "- `error` 该行有问题，原因见 `error_report.errors[]`。\n\n"
        "`error_report.errors[]` 逐条给出 `{row_no, field, message}`：\n"
        "`row_no` 是**文件里的数据行号（从 1 起，不含表头）**，教研据此在 Excel 里跳行定位；"
        "`field` 精确到列名（如 `answer` / `chapter_code`）。\n\n"
        "错误最多回传 200 条（`truncated=true` 表示被截断），但 `total_errors` 是完整计数。\n\n"
        "> 与 docs/07 §4.3 的差异：文档写的是异步（pending→parsing→validating），"
        "本实现是**同步**返回 —— 6000 行实测 < 1s，异步化的复杂度（轮询/SSE）当前不划算。"
    ),
    dependencies=[Depends(require_permission("question:import"))],
)
async def validate_import(
    batch_id: int,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    detail = await import_service.validate_batch(
        db,
        batch_id=batch_id,
        actor=me,
        actor_name=me.display_name,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(detail.model_dump(), message="校验完成，未写入任何题目")


# =====================================================================
# ④ 执行
# =====================================================================


@router.post(
    "/{batch_id}/execute",
    response_model=Envelope[ImportExecuteOut],
    summary="执行导入",
    description=(
        "需要权限 `question:import`。**整批一个事务**：要么全部进入，要么一条不进。\n\n"
        "- 前置状态必须是 `done`（先调 validate）；否则 `40901`。\n"
        "- **同一批只能执行一次**：重复执行在 insert 模式下会静默 0 写入（统计被打回 0），"
        "在 upsert 模式下会把整批题的 `version` 再顶一轮。要重导请新建批次。\n"
        "- **默认严格**：本批只要有 1 行校验失败就整体拒绝（不写一行），这是"
        "「整批成功或整批失败」的含义，也是验收标准 ① 的要求。"
        "确实只想导入通过的行时，显式传 `allow_partial=true`（跳过错误行）。\n"
        "- 插入的题一律 `status='draft'`；upsert 命中老题时**不覆盖**其 `status`"
        "（把已发布的题因「内容被重导」打回草稿是运维事故）。\n"
        "- `publish=true` 等价于执行成功后再调一次 `/publish`。\n"
        "- 中途任何异常 → 整批回滚，`status='failed'`，库里不留半截数据。"
    ),
    dependencies=[Depends(require_permission("question:import"))],
)
async def execute_import(
    batch_id: int,
    payload: ImportExecuteIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await import_service.execute_batch(
        db,
        batch_id=batch_id,
        actor=me,
        actor_name=me.display_name,
        publish=payload.publish,
        allow_partial=payload.allow_partial,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(out.model_dump(), message=f"导入完成：新增/更新 {out.success_rows} 题")


# =====================================================================
# ⑤ 发布
# =====================================================================


@router.post(
    "/{batch_id}/publish",
    response_model=Envelope[ImportBatchOut],
    summary="发布本批题目",
    description=(
        "需要权限 `question:publish`。把**本批写入**的题从 `draft` 批量置为 `published`。\n\n"
        "- `include_duplicates=true` 时，连本批命中的重复题（已存在的老题）一并发布。\n"
        "- 推荐节奏：先入库草稿 → 教研抽样验收 → 再整批发布，避免直接污染线上题库。\n"
        "- 批次状态必须为 `done`；本批没有可发布的题 → `40001`。"
    ),
    dependencies=[Depends(require_permission("question:publish"))],
)
async def publish_import(
    batch_id: int,
    payload: ImportPublishIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await import_service.publish_batch(
        db,
        batch_id=batch_id,
        actor=me,
        actor_name=me.display_name,
        include_duplicates=payload.include_duplicates,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(out.model_dump(), message="已发布")


# =====================================================================
# ⑥ 回滚
# =====================================================================


@router.post(
    "/{batch_id}/rollback",
    response_model=Envelope[ImportRollbackOut],
    summary="整批回滚",
    description=(
        "需要权限 `question:rollback`。**不物理删除**，全程留痕：\n\n"
        "- 本批 `insert` 的题 → 软删除（`is_deleted=true`，`version+1`），数据仍在库里可追溯；\n"
        "- 本批 `update` 的题 → 从 `question_versions` 还原到**导入前那一版**；\n"
        "- 每道题写一条 `content_change_logs(action='rollback')`，并写一条审计日志。\n\n"
        "`missing[]` 列出「批次记录里有、但库里已找不到」的题目 ID（用于发现被人工清过的数据）。\n"
        "批次状态必须为 `done` / `failed`；已回滚过 → `40901`。"
    ),
    dependencies=[Depends(require_permission("question:rollback"))],
)
async def rollback_import(
    batch_id: int,
    payload: ImportRollbackIn,
    request: Request,
    db: DbSession,
    me: CurrentUserDep,
) -> dict:
    out = await import_service.rollback_batch(
        db,
        batch_id=batch_id,
        actor=me,
        actor_name=me.display_name,
        reason=payload.reason,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return ok(out.model_dump(), message="已整批回滚")


# =====================================================================
# ③ 查看详情  —— 放最后：通配路径参数不与其他固定段抢
# =====================================================================


@router.get(
    "/{batch_id}",
    response_model=Envelope[ImportBatchDetail],
    summary="批次详情（状态 + 统计 + 错误报告 + 逐行结果）",
    description=(
        "需要权限 `question:read`。前端轮询这个接口即可，"
        "列表/校验/执行/发布共用同一套字段形状，不必按状态切换解析逻辑。\n\n"
        "- `error_report`：错误总数 + 截断标记 + 前 200 条 `{row_no, field, message}`；\n"
        "- `rows`：逐行结果分页（`row_page` / `row_page_size`，默认 1 / 50），"
        "每条含 `row_no / action / message / question_id`。"
    ),
    dependencies=[Depends(require_permission("question:read"))],
)
async def get_import(
    batch_id: int,
    db: DbSession,
    me: CurrentUserDep,
    row_page: Annotated[int, Query(ge=1, description="逐行结果页码")] = 1,
    row_page_size: Annotated[int, Query(ge=1, le=200, description="逐行结果每页条数")] = 50,
) -> dict:
    detail = await import_service.get_batch(
        db, batch_id=batch_id, viewer=me, row_page=row_page, row_page_size=row_page_size
    )
    return ok(detail.model_dump())
