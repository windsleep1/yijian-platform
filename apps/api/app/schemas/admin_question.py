"""管理端 · 题库请求/响应模型（Batch 4）。

## 三个刻意的设计决定

1. **答案由选项推导，不接受单独传入。**
   `single` / `multiple` 题的 `answer.value` 是**从 `options[].is_correct` 推出来的**，
   调用方没有第二个入口去单独指定答案。这样"多选题答案必须都在选项里"就变成
   **结构上不可能违反**，而不是靠运行时比对去兜。运行时那句断言仍然保留，
   当作廉价的不变量检查（见 `question_service.derive_answer`）。

2. **本批只开放 `single` / `multiple` / `judge` 三种题型。**
   案例题（`case` / `case_sub`）与主观题（`fill` / `essay`）的编辑涉及
   背景材料、子问切分、评分点，是另一个量级的工作量（见 docs/10「本批不做」）。
   种子题库里这四类题**能看能筛**，只是不能改 —— 所以 `QType` 保留了全部取值，
   而 `EditableQType` 才是写接口的白名单。

3. **`is_deleted` 与 `status='archived'` 是两个正交概念。**
   - `is_deleted=true`  = 软删除（列表默认过滤掉，靠「显示已归档」开关带出来）
   - `status='archived'` = 业务状态"已归档"，记录仍然正常可见
   列表接口的开关叫 `include_deleted`，不是 `include_archived` —— 名字容易混，
   所以这里写清楚。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.types import BigIntStr, BigIntStrOpt

# 全部题型（读接口用，含本批不可编辑的案例/主观题）
QType = Literal["single", "multiple", "judge", "case", "case_sub", "fill", "essay"]
# 写接口白名单：本批只支持这三种
EditableQType = Literal["single", "multiple", "judge"]
QStatus = Literal["draft", "reviewing", "published", "rejected", "archived"]
SourceType = Literal["self", "authorized", "public", "user_import", "ai_assisted"]

# 客观题的选项数量上下限。4 是应试常态，给到 8 是为了兼容多选。
MIN_OPTIONS = 2
MAX_OPTIONS = 8
# 多选题至少要有 2 个正确答案 —— 只有 1 个正确的那是单选题
MIN_MULTIPLE_CORRECT = 2


# --------------------------------------------------------------------- 入参


class QuestionOptionIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=8, description="选项标号，如 A/B/C/D")
    content: str = Field(..., min_length=1, max_length=2000, description="选项正文（纯文本）")
    content_html: str | None = Field(None, max_length=4000)
    is_correct: bool = Field(False, description="是否正确答案。答案由本字段推导，不单独传")


def _validate_options(qtype: str, options: list[QuestionOptionIn]) -> None:
    """题型 ↔ 选项 ↔ 正确答案 的一致性校验。

    抛 `ValueError`，由 service 层转成 `40001`（带可读中文）。
    集中在 schema 模块，是为了让 create 与 update 走**同一套**规则 ——
    两处各写一遍校验，早晚会漂移。
    """
    if qtype == "judge":
        # 判断题的对错由 judge_answer 布尔字段表达，不需要选项
        if options:
            raise ValueError("判断题不需要传 options，请用 judge_answer 表示对错")
        return

    n = len(options)
    if not (MIN_OPTIONS <= n <= MAX_OPTIONS):
        raise ValueError(f"选项数量必须在 {MIN_OPTIONS}~{MAX_OPTIONS} 之间，当前 {n} 个")

    labels = [o.label.strip().upper() for o in options]
    if len(set(labels)) != len(labels):
        dup = next(l for l in labels if labels.count(l) > 1)
        raise ValueError(f"选项标号重复：{dup}")

    correct = [l for l, o in zip(labels, options) if o.is_correct]
    if qtype == "single":
        # 这就是 B 端特征①：单选题不能标两个正确答案
        if len(correct) != 1:
            raise ValueError(
                f"单选题必须恰好有 1 个正确答案，当前标了 {len(correct)} 个"
                + (f"（{('、'.join(correct))}）" if correct else "（一个都没标）")
            )
    elif qtype == "multiple":
        if len(correct) < MIN_MULTIPLE_CORRECT:
            raise ValueError(
                f"多选题至少要有 {MIN_MULTIPLE_CORRECT} 个正确答案，当前只有 {len(correct)} 个"
                "（正确答案只有一个的题目请用单选题）"
            )


class QuestionCreateIn(BaseModel):
    subject_id: int = Field(..., description="科目 ID")
    chapter_id: int | None = Field(None, description="章节 ID，可空")
    type: EditableQType = Field(..., description="题型：single / multiple / judge")
    stem: str = Field(..., min_length=1, max_length=8000, description="题干纯文本")
    stem_html: str | None = Field(None, description="题干富文本/HTML（可空，前端由 markdown 渲染）")
    judge_answer: bool | None = Field(
        None, description="判断题答案。type=judge 时必填，其余题型必须为空"
    )
    analysis: str | None = Field(None, max_length=8000, description="解析")
    analysis_html: str | None = None
    difficulty: int = Field(3, ge=1, le=5, description="难度 1~5")
    score_default: float = Field(1, gt=0, le=100, description="默认分值")
    status: QStatus = Field("draft", description="业务状态")
    exam_year: int | None = Field(None, ge=2000, le=2100)
    keywords: str | None = Field(None, max_length=255)
    tags: list[str] = Field(default_factory=list, max_length=20)
    # 合规三件套（见 docs/07 与项目铁律：source_type 必填）
    source_type: SourceType = Field("self", description="来源类型，题库合规必填")
    source_name: str | None = Field(None, max_length=160, description="非 self 时必填")
    source_license: str | None = Field(None, max_length=160, description="authorized 时必填")
    copyright_holder: str | None = Field(None, max_length=160)
    options: list[QuestionOptionIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "QuestionCreateIn":
        _validate_options(self.type, self.options)
        if self.type == "judge" and self.judge_answer is None:
            raise ValueError("判断题必须给出 judge_answer（true=正确 / false=错误）")
        if self.type != "judge" and self.judge_answer is not None:
            raise ValueError("只有判断题才能传 judge_answer")
        # 合规红线：非自有题必须说清来源；授权题必须带许可说明
        if self.source_type != "self" and not self.source_name:
            raise ValueError("source_type 非 self 时必须提供 source_name")
        if self.source_type == "authorized" and not self.source_license:
            raise ValueError("authorized 来源必须提供 source_license（授权凭证号/许可说明）")
        return self


class QuestionUpdateIn(BaseModel):
    """编辑入参。

    **不带 `id` / `version` 之外的身份字段**：`version` 用于乐观锁 ——
    调用方必须回传读到的版本号，与库中不一致说明有人先改了，返回 `40901`。
    """

    version: int = Field(..., ge=1, description="调用方持有的版本号，用于乐观锁")
    subject_id: int | None = None
    chapter_id: int | None = None
    type: EditableQType | None = None
    stem: str | None = Field(None, min_length=1, max_length=8000)
    stem_html: str | None = None
    judge_answer: bool | None = None
    analysis: str | None = None
    analysis_html: str | None = None
    difficulty: int | None = Field(None, ge=1, le=5)
    score_default: float | None = Field(None, gt=0, le=100)
    status: QStatus | None = None
    exam_year: int | None = None
    keywords: str | None = None
    tags: list[str] | None = None
    source_type: SourceType | None = None
    source_name: str | None = None
    source_license: str | None = None
    copyright_holder: str | None = None
    options: list[QuestionOptionIn] | None = None

    # type 缺失时，用库里的当前值做校验；这里只做"字段自身可判定"的部分
    @model_validator(mode="after")
    def _check(self) -> "QuestionUpdateIn":
        # options 与 type 的交叉校验需要库中现状，放到 service 层（拿到 row 之后）
        if self.options is not None:
            labels = [o.label.strip().upper() for o in self.options]
            if len(set(labels)) != len(labels):
                raise ValueError("选项标号重复")
            if not (MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS):
                raise ValueError(
                    f"选项数量必须在 {MIN_OPTIONS}~{MAX_OPTIONS} 之间，当前 {len(self.options)} 个"
                )
        return self


class QuestionBatchDeleteIn(BaseModel):
    ids: list[int] = Field(
        ..., min_length=1, max_length=200, description="要软删除的题目 ID 列表，最多 200 条"
    )
    reason: str | None = Field(None, max_length=200, description="删除原因，写入变更日志")


# --------------------------------------------------------------------- 出参


class QuestionOptionOut(BaseModel):
    id: BigIntStr
    label: str
    content: str
    content_html: str | None = None
    is_correct: bool
    sort_no: int


class QuestionListItem(BaseModel):
    """列表项。题干做截断，避免一次拉回几十 KB。"""

    id: BigIntStr
    subject_id: BigIntStr
    subject_name: str | None = None
    chapter_id: BigIntStrOpt = None
    chapter_name: str | None = None
    #: 知识点（Batch 7 Pass 2 补）。组卷"加题"要按知识点挑题，
    #: 列表里也得能看出这道题挂在哪个知识点上，否则筛完也不知道筛的是什么。
    knowledge_point_id: BigIntStrOpt = None
    knowledge_point_name: str | None = None
    type: str
    stem: str = Field(..., description="题干纯文本（已按 stem_preview_len 截断）")
    difficulty: int
    score_default: float
    status: str
    version: int
    is_deleted: bool
    option_count: int = 0
    correct_labels: list[str] = Field(
        default_factory=list, description="正确答案标号。教研在列表里就要能一眼看到答案"
    )
    keywords: str | None = None
    created_by_name: str | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None


class QuestionVersionItem(BaseModel):
    """历史版本快照（只读，本批不做回滚）。"""

    id: BigIntStr
    version: int
    change_log: str | None = None
    operator_id: BigIntStrOpt = None
    operator_name: str | None = None
    is_current: bool = Field(False, description="是否与当前 version 相同")
    snapshot: dict[str, Any] = Field(
        default_factory=dict, description="该版本的完整题目快照，供只读展示"
    )
    created_at: datetime | None = None


class QuestionDetail(BaseModel):
    """详情。含选项全文、答案、解析、版本号与历史版本。

    历史版本**内联在详情里**（而不是单开一个 `/versions` 接口）——
    与本项目 `GET /admin/users/{id}` 内联 `recent_audits` 的做法保持一致：
    详情页本来就要展示它，单独拆接口只会多一次往返。
    """

    id: BigIntStr
    subject_id: BigIntStr
    subject_name: str | None = None
    chapter_id: BigIntStrOpt = None
    chapter_name: str | None = None
    knowledge_point_id: BigIntStrOpt = None

    type: str
    stem: str
    stem_html: str | None = None
    analysis: str | None = None
    analysis_html: str | None = None
    answer: dict[str, Any] = Field(default_factory=dict, description="原始 answer JSONB")
    options: list[QuestionOptionOut] = Field(default_factory=list)
    correct_labels: list[str] = Field(default_factory=list)

    difficulty: int
    score_default: float
    status: str
    version: int
    is_deleted: bool

    exam_year: int | None = None
    keywords: str | None = None
    tags: list[str] = Field(default_factory=list)

    source_type: str = "self"
    source_name: str | None = None
    source_license: str | None = None
    copyright_holder: str | None = None
    content_hash: str | None = None

    created_by: BigIntStrOpt = None
    created_by_name: str | None = None
    updated_by: BigIntStrOpt = None
    updated_by_name: str | None = None
    published_at: datetime | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    can_edit: bool = Field(
        False, description="调用者是否拥有 question:update。前端据此决定「编辑」按钮可用性"
    )
    can_delete: bool = Field(False, description="调用者是否拥有 question:delete")
    editable: bool = Field(
        True,
        description="该题型本批是否可编辑。案例题/主观题为 false，前端应禁用编辑入口",
    )
    versions: list[QuestionVersionItem] = Field(
        default_factory=list, description="历史版本，最新在前，最多 10 条，只读"
    )


class QuestionDeleteOut(BaseModel):
    """单题软删除结果。

    返回 `version` 是为了让前端**就地把行更新掉**（版本号 +1、状态变已删除），
    不必为了一次删除再拉一遍列表 —— 列表页在批量操作下最怕整页刷新丢筛选条件。
    """

    id: BigIntStr
    is_deleted: bool = True
    version: int = Field(..., description="软删除后的版本号（原 version + 1）")


class QuestionRestoreOut(BaseModel):
    """单题恢复结果（Batch 7 补，与软删除对称）。

    **幂等**：题目本来就没被删除时，`already_active=true` 且 `version` 不变 ——
    接口返回 `code=0` 而不是报错。批量/重试场景下，"目标状态已达成"不该算失败。
    """

    id: BigIntStr
    is_deleted: bool = False
    version: int = Field(..., description="恢复后的版本号（原 version + 1）")
    #: 本来就是未删除状态（走了幂等分支，**没有产生任何写入**）
    already_active: bool = False
    message: str = "已恢复"


class QuestionBatchDeleteOut(BaseModel):
    deleted: int = Field(..., description="实际软删除的条数")
    skipped: list[BigIntStr] = Field(
        default_factory=list, description="被跳过的 ID（不存在 / 已是删除态）"
    )
    batch_id: BigIntStr = Field(..., description="本次批量操作的批次号，写入变更日志便于追溯")


# --------------------------------------------------------------------- 章节树


class SubjectBrief(BaseModel):
    id: BigIntStr
    code: str
    name: str
    short_name: str | None = None
    professional: str | None = None


class ChapterNode(BaseModel):
    id: BigIntStr
    subject_id: BigIntStr
    parent_id: BigIntStrOpt = None
    code: str
    name: str
    level: int
    sort_no: int
    question_count: int = Field(0, description="该章节下的题目数（实时统计，不含软删除）")
    children: list["ChapterNode"] = Field(default_factory=list)


ChapterNode.model_rebuild()


class SubjectChapterGroup(BaseModel):
    subject: SubjectBrief
    chapters: list[ChapterNode] = Field(default_factory=list)


class ChapterTreeOut(BaseModel):
    """章节树。

    不传 `subject_id` → 返回全部科目分组（供"科目"下拉 + 联动默认值）。
    传 `subject_id`   → 只返回该科目一组（供选中科目后刷新章节下拉）。
    """

    subject_id: BigIntStrOpt = None
    total: int = Field(0, description="返回的章节总数")
    items: list[SubjectChapterGroup] = Field(default_factory=list)


__all__ = [
    "QType",
    "EditableQType",
    "QStatus",
    "SourceType",
    "MIN_OPTIONS",
    "MAX_OPTIONS",
    "MIN_MULTIPLE_CORRECT",
    "QuestionOptionIn",
    "QuestionCreateIn",
    "QuestionUpdateIn",
    "QuestionBatchDeleteIn",
    "QuestionOptionOut",
    "QuestionListItem",
    "QuestionVersionItem",
    "QuestionDetail",
    "QuestionDeleteOut",
    "QuestionBatchDeleteOut",
    "SubjectBrief",
    "ChapterNode",
    "SubjectChapterGroup",
    "ChapterTreeOut",
]
