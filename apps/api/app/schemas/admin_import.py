"""管理端 · 题库批量导入 请求/响应模型（Batch 5 Pass 1）。

## 字段规范来自哪里

`docs/07-题库合规与导入规范.md` §4.2「字段规范（导入模板列定义）」。
下面是那 19 条规则**逐一展开**后的结果（`option_a`…`option_f` 展开为 6 列），
**不做任何增删** —— 增一个字段就意味着导入模板要重新发给教研，
删一个字段就意味着那份模板导不进来。

    subject_code / chapter_code / kp_code / type / stem
    option_a..option_f / answer / answer_points / analysis
    score / difficulty / exam_year
    source_type / source_name / source_license
    tags / case_group_id / material / media_urls

## 为什么"必填"跟"条件必填"分开表达

`answer` 是必填，但它的**合法取值**随 `type` 变化（单选一个字母、多选多个字母、
判断题 A/B、案例小题是文本）。把它塞进 Pydantic 的字段校验只会得到
"answer 格式错误"这种没法定位的报错 —— 而验收标准要求**逐行返回
`{row_no, field, message}`**，精确到字段。

所以本模块只做「字段类型」层面的约束，**所有跨字段规则都在
`import_service.validate_row()` 里逐条实现**，每条都带 `field` 名字。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.types import BigIntStr, BigIntStrOpt

# 导入模板列（顺序即 CSV 表头顺序）
IMPORT_COLUMNS: tuple[str, ...] = (
    "subject_code",
    "chapter_code",
    "kp_code",
    "type",
    "stem",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "option_e",
    "option_f",
    "answer",
    "answer_points",
    "analysis",
    "score",
    "difficulty",
    "exam_year",
    "source_type",
    "source_name",
    "source_license",
    "tags",
    "case_group_id",
    "material",
    "media_urls",
)
# 选项列（供"至少 2 项 / 判断题固定 A、B"等规则使用）
OPTION_COLUMNS: tuple[str, ...] = tuple(c for c in IMPORT_COLUMNS if c.startswith("option_"))

ImportFileType = Literal["csv", "json"]
ImportMode = Literal["insert", "upsert"]
ImportStatus = Literal[
    "pending", "parsing", "validating", "importing", "done", "failed", "rolled_back"
]


# --------------------------------------------------------------------- 错误报告


class RowError(BaseModel):
    """单行单字段的错误。验收要求：前端能据此**精确定位**。

    `row_no` 是**文件里的行号**（数据行从 1 开始，不含表头），
    不是数据库自增、也不是 0-based 下标 —— 教研拿着它去 Excel 里跳行就能找到。
    """

    row_no: int = Field(..., description="文件中的行号（数据行，从 1 开始，不含表头）")
    field: str = Field(..., description="出错的字段名，如 answer / chapter_code")
    message: str = Field(..., description="人话描述的原因")


class ErrorReport(BaseModel):
    """错误报告。

    `errors` **截断**保存（默认前 200 条），但 `total_errors` 是完整计数 ——
    一个 6000 行的文件如果全错，回传 6000 条错误既撑爆响应也没人看，
    但计数必须真实。
    """

    total_errors: int = 0
    truncated: bool = False
    errors: list[RowError] = Field(default_factory=list)


# --------------------------------------------------------------------- 入参


class ImportUploadMeta(BaseModel):
    """上传接口除文件外的元信息（multipart 的 Form 字段）。"""

    subject_id: int | None = Field(None, description="批次所属科目 ID（可与文件内 subject_code 并存）")
    source_type: str = Field("self", description="批次默认来源类型，行内未填时兜底")
    license_note: str | None = Field(None, max_length=500, description="授权/来源说明，写入批次")
    mode: ImportMode = Field("insert", description="insert=跳过重复；upsert=更新已存在")
    auto_publish: bool = Field(False, description="导入后是否自动发布（本批默认 false）")


class ImportExecuteIn(BaseModel):
    """执行入参。

    **刻意没有 `mode` 字段**：mode（insert / upsert）在上传时就定死，
    校验阶段据此把每一行判成 insert / update / duplicate 并落进 `import_items`。
    执行阶段若允许覆盖 mode，就会出现"校验说是 update、执行却按 insert 写"
    的分裂 —— 那种 bug 只在数据里看得出来。要换 mode 就重新上传一次。
    """

    publish: bool = Field(False, description="执行后紧接着发布（等价于再调一次 /publish）")
    allow_partial: bool = Field(
        False,
        description=(
            "本批存在校验失败行时，是否仍导入通过的行（跳过错误行）。"
            "默认 false = 整体拒绝，符合「整批成功或整批失败」"
        ),
    )


class ImportPublishIn(BaseModel):
    include_duplicates: bool = Field(
        False, description="是否把本批次命中的重复题（已存在的老题）一并发布"
    )


class ImportRollbackIn(BaseModel):
    reason: str | None = Field(None, max_length=200, description="回滚原因，写入变更日志")


# --------------------------------------------------------------------- 出参


class ImportRowPreview(BaseModel):
    row_no: int
    action: str = Field(..., description="insert / update / skip / error / duplicate")
    message: str | None = None
    question_id: BigIntStrOpt = None


class ImportBatchOut(BaseModel):
    """批次状态 + 统计。列表/详情/校验/执行/发布/回滚**共用**这个形状。

    共用是有意的：前端轮询 `/imports/{id}` 时不需要按状态切换解析逻辑，
    执行完再取一次就能就地刷新。
    """

    id: BigIntStr
    batch_no: str
    file_name: str
    file_type: str
    file_hash: str
    file_size: int | None = Field(None, description="文件字节数（便于前端展示与排查）")
    subject_id: BigIntStrOpt = None
    subject_code: str | None = None
    subject_name: str | None = None
    source_type: str
    license_note: str | None = None
    mode: str
    status: str
    auto_publish: bool = False

    total_rows: int = 0
    success_rows: int = 0
    failed_rows: int = 0
    duplicate_rows: int = 0
    updated_rows: int = 0

    error_report: ErrorReport = Field(default_factory=ErrorReport)

    operator_id: BigIntStrOpt = None
    operator_name: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rollback_at: datetime | None = None
    rollback_by: BigIntStrOpt = None
    created_at: datetime | None = None

    can_execute: bool = Field(False, description="当前状态 + 调用者权限是否允许执行")
    can_publish: bool = Field(False)
    can_rollback: bool = Field(False)


class ImportBatchDetail(ImportBatchOut):
    """批次详情 = 批次 + 逐行结果（分页）。"""

    rows: list[ImportRowPreview] = Field(default_factory=list)
    row_page: int = 1
    row_page_size: int = 50
    row_total: int = 0


class ImportUploadOut(BaseModel):
    id: BigIntStr
    batch_no: str
    file_name: str
    file_type: str
    total_rows: int = Field(0, description="解析出的数据行数（校验前）")
    status: str
    message: str = ""


class ImportExecuteOut(BaseModel):
    id: BigIntStr
    status: str
    total_rows: int = 0
    success_rows: int = 0
    failed_rows: int = 0
    duplicate_rows: int = 0
    updated_rows: int = 0
    duration_ms: int = 0


class ImportRollbackOut(BaseModel):
    id: BigIntStr
    status: str
    rolled_back_questions: int = Field(0, description="被软删除（或还原）的题目数")
    rolled_back_updates: int = Field(0, description="被还原到导入前版本的题目数")
    missing: list[BigIntStr] = Field(
        default_factory=list, description="批次记录里存在、但库里已找不到的题目 ID"
    )


__all__ = [
    "IMPORT_COLUMNS",
    "OPTION_COLUMNS",
    "ImportFileType",
    "ImportMode",
    "ImportStatus",
    "RowError",
    "ErrorReport",
    "ImportUploadMeta",
    "ImportExecuteIn",
    "ImportPublishIn",
    "ImportRollbackIn",
    "ImportRowPreview",
    "ImportBatchOut",
    "ImportBatchDetail",
    "ImportUploadOut",
    "ImportExecuteOut",
    "ImportRollbackOut",
]
