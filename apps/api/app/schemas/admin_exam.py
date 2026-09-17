"""管理端 · 试卷 / 组卷规则 请求响应模型（Batch 7 Pass 1）。

## 三个必须说清的设计决定

### 1. 组卷规则的 `rules` 是一个**约束数组**，不是"数量表"

每条 rule 描述一类题的抽取条件：
`{"type":"single","count":60,"score":1,"difficulty":[2,4],"kp_ids":[...],"year":null}`
抽题时**逐条独立执行**，每条都可能在"精确匹配 → 放宽难度 → 放宽知识点"三步后仍有缺口
（见 `docs/05 §3.2`）。缺口通过 `Shortfall` 回传，**绝不静默补别的题**。

`count` 上限 200：一份卷子单个题型的题量不该超过这个数，
超过基本是把"套卷"和"整库导出"搞混了。

### 2. 版本锁定为什么存在 `rule_config` 里（而不是 `exam_questions` 列）

`docs/07 §6.4` 的示例代码用了 `eq.locked_version`，但 **`exam_questions` 表里没有这一列**，
本批又约定"不改 schema"。所以锁定信息落在 `exams.rule_config.question_locks`
（`{"<question_id>": <version>}`），发布时写入、回滚/重编排时清除。

这是个**明知的将就**：JSONB 里存映射，查询/索引都不如独立列，
而且 `rule_config` 原本是描述"答题行为"（随机顺序 / 单题限时）的字段，语义被撑宽了。
正确的修法是 `ALTER TABLE exam_questions ADD COLUMN locked_version INTEGER`，
建议下一批补上 —— 届时只需改 `_read_question_locks` / `_write_question_locks` 两个内部函数。

### 3. `shortfalls` 的 `rule` 字段放**原始规则对象**，不是字符串

`docs/05 §3.2` 的示例是 `shortfalls.append({"rule": rule, ...})`（rule 是 dict）。
保留结构化形态，前端才能把缺口对回具体是哪条约束；
展示用的中文标签由前端拼（或读 `rule_label`，服务端也顺带给了）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.admin_question import QType
from app.schemas.types import BigIntStr, BigIntStrOpt

# ---------------------------------------------------------------- 枚举

# exams.type 的 CHECK 约束取值
ExamType = Literal["real", "mock", "chapter_test", "sprint", "daily", "custom"]
# exams.status 的 CHECK 约束取值
ExamStatus = Literal["draft", "reviewing", "published", "off", "archived"]
# paper_rules.strategy 的 CHECK 约束取值
RuleStrategy = Literal["random", "weak_first", "coverage", "history_similar"]
# paper_rules.status 的 CHECK 约束取值
RuleStatus = Literal["on", "off"]

#: 组卷可用的题型。案例小问（`case_sub`）**不作为独立抽题单位** ——
#: 它必须跟着父题（案例背景）一起进卷，否则考生看到的是没有材料的孤儿子问。
COMPOSABLE_TYPES: tuple[str, ...] = ("single", "multiple", "judge", "case", "fill", "essay")

#: 客观题（有确定答案、可自动判分）。试卷统计 `has_subjective` 靠它推导。
OBJECTIVE_TYPES: tuple[str, ...] = ("single", "multiple", "judge")


# ---------------------------------------------------------------- 组卷规则


class RuleItem(BaseModel):
    """一条抽题约束。"""

    type: QType = Field(..., description="题型")
    count: int = Field(..., ge=1, le=200, description="需要多少道")
    score: float = Field(1, gt=0, le=100, description="每题分值")
    difficulty: tuple[int, int] | None = Field(
        None,
        description="难度**闭区间** `[min,max]`，取值 1~5。不传 = 不限难度。",
    )
    kp_ids: list[int] = Field(
        default_factory=list, description="限定知识点 ID（任一命中即可）。空 = 不限"
    )
    chapter_ids: list[int] = Field(
        default_factory=list, description="限定章节 ID（任一命中即可）。空 = 不限"
    )
    year: int | None = Field(None, ge=2000, le=2100, description="限定考试年份。不传 = 不限")
    prefer_unused: bool = Field(
        True, description="加权采样时是否优先选**从未被任何试卷用过**的题"
    )

    @model_validator(mode="after")
    def _check_difficulty(self) -> "RuleItem":
        if self.difficulty is not None:
            lo, hi = self.difficulty
            if not (1 <= lo <= hi <= 5):
                raise ValueError("difficulty 必须是 [min,max]，且 1 <= min <= max <= 5")
        return self

    @property
    def label(self) -> str:
        """人类可读描述，用于错误提示与缺口说明。"""
        parts = [self.type, f"{self.count} 题"]
        if self.difficulty:
            parts.append(f"难度 {self.difficulty[0]}~{self.difficulty[1]}")
        if self.kp_ids:
            parts.append(f"{len(self.kp_ids)} 个知识点")
        if self.chapter_ids:
            parts.append(f"{len(self.chapter_ids)} 个章节")
        if self.year:
            parts.append(f"{self.year} 年")
        return " · ".join(parts)


class PaperRuleCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    subject_id: int = Field(..., description="科目 ID")
    type: ExamType = "mock"
    duration_min: int = Field(180, ge=1, le=600)
    rules: list[RuleItem] = Field(..., min_length=1, description="至少一条抽题约束")
    strategy: RuleStrategy = "random"

    @model_validator(mode="after")
    def _check_rules(self) -> "PaperRuleCreateIn":
        _ensure_composable(self.rules)
        return self


class PaperRuleUpdateIn(BaseModel):
    """编辑规则。未传的字段沿用现值（`None` = 不改）。"""

    name: str | None = Field(None, min_length=1, max_length=160)
    subject_id: int | None = None
    type: ExamType | None = None
    duration_min: int | None = Field(None, ge=1, le=600)
    rules: list[RuleItem] | None = Field(None, min_length=1)
    strategy: RuleStrategy | None = None
    status: RuleStatus | None = None

    @model_validator(mode="after")
    def _check_rules(self) -> "PaperRuleUpdateIn":
        if self.rules is not None:
            _ensure_composable(self.rules)
        return self


class PaperRuleOut(BaseModel):
    id: BigIntStr
    name: str
    subject_id: BigIntStr
    subject_name: str | None = None
    type: ExamType
    duration_min: int
    rules: list[RuleItem]
    strategy: RuleStrategy
    status: RuleStatus
    #: 规则要求的总题量与总分（由 rules 推导，便于列表页直接展示）
    planned_count: int = 0
    planned_score: float = 0
    created_by: BigIntStrOpt = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    can_edit: bool = False
    can_delete: bool = False


class PaperRuleDeleteOut(BaseModel):
    id: BigIntStr
    name: str
    #: 硬删除（`paper_rules` 没有 `is_deleted` 列）。说清楚，避免前端以为是软删除。
    hard_deleted: bool = True
    message: str


# ---------------------------------------------------------------- 缺口


class Shortfall(BaseModel):
    """某条规则**没能凑够**的题量。

    `rule` 是原始规则对象（结构化），前端可据此把缺口对回具体约束。
    `need` / `got` / `missing` 的关系恒为 `got + missing == need`。
    """

    rule: RuleItem
    rule_label: str
    rule_index: int = Field(..., description="该规则在 rules 数组里的下标（从 0 起）")
    question_type: QType
    need: int
    got: int
    missing: int
    #: 逐级放宽后每步的候选数，便于解释"为什么还是不够"
    reason: str


# ---------------------------------------------------------------- 试卷


class ExamSectionIn(BaseModel):
    """卷面分段（如"单项选择题 60 题 × 1 分"）。"""

    name: str = Field(..., min_length=1, max_length=96)
    question_type: QType
    question_count: int = Field(..., ge=0, le=500)
    score_per: float = Field(1, gt=0, le=100)
    sort_no: int = Field(0, ge=0, le=999)


class ExamCreateIn(BaseModel):
    subject_id: int
    title: str = Field(..., min_length=1, max_length=200)
    type: ExamType = "mock"
    professional: str | None = Field(None, max_length=24)
    exam_year: int | None = Field(None, ge=2000, le=2100)
    paper_no: str | None = Field(None, max_length=32)
    duration_min: int = Field(180, ge=1, le=600)
    pass_score: float = Field(0, ge=0, le=1000)
    intro_html: str | None = None
    is_free: bool = False
    sections: list[ExamSectionIn] = Field(
        default_factory=list, description="卷面分段；允许先建空卷再加题"
    )

    @model_validator(mode="after")
    def _check_sections(self) -> "ExamCreateIn":
        _ensure_unique_sections(self.sections)
        return self


class ExamUpdateIn(BaseModel):
    """编辑试卷。未传的字段沿用现值。**已发布**的卷只允许改展示性字段。"""

    title: str | None = Field(None, min_length=1, max_length=200)
    type: ExamType | None = None
    professional: str | None = Field(None, max_length=24)
    exam_year: int | None = Field(None, ge=2000, le=2100)
    paper_no: str | None = Field(None, max_length=32)
    duration_min: int | None = Field(None, ge=1, le=600)
    pass_score: float | None = Field(None, ge=0, le=1000)
    intro_html: str | None = None
    is_free: bool | None = None
    sections: list[ExamSectionIn] | None = None

    @model_validator(mode="after")
    def _check_sections(self) -> "ExamUpdateIn":
        if self.sections is not None:
            _ensure_unique_sections(self.sections)
        return self


class ExamSectionOut(BaseModel):
    id: BigIntStr
    seq: int
    name: str
    question_type: QType
    question_count: int = Field(..., description="计划题数")
    score_per: float
    section_score: float
    sort_no: int
    #: 库里**实际**挂在这一段的题数。与 `question_count` 不等 = 卷面没填满。
    actual_count: int = 0
    actual_score: float = 0


class ExamQuestionItem(BaseModel):
    id: BigIntStr = Field(..., description="`exam_questions.id`（卷面行 id，不是题目 id）")
    question_id: BigIntStr
    section_id: BigIntStrOpt = None
    seq: int
    score: float
    question_type: QType
    stem_preview: str = ""
    difficulty: int | None = None
    chapter_id: BigIntStrOpt = None
    #: 发布时锁定的题目版本；未发布为 None
    locked_version: int | None = None
    #: 题目**当前**版本
    current_version: int | None = None
    #: 锁定版本与当前版本不一致 = 题目在发布后被改过（卷面仍用锁定版）
    version_drift: bool = False


class ExamSectionDetail(ExamSectionOut):
    questions: list[ExamQuestionItem] = Field(default_factory=list)


class ExamListItem(BaseModel):
    id: BigIntStr
    subject_id: BigIntStr
    subject_name: str | None = None
    title: str
    type: ExamType
    status: ExamStatus
    exam_year: int | None = None
    paper_no: str | None = None
    question_count: int
    total_score: float
    pass_score: float
    duration_min: int
    difficulty: float
    has_subjective: bool
    is_free: bool
    #: 软删除标记。**已归档的卷仍可查看详情**（对称 Batch 4 题目软删除），
    #: 列表默认过滤掉、`include_deleted=true` 时带出。
    is_deleted: bool = False
    published_at: datetime | None = None
    updated_at: datetime
    created_by: BigIntStrOpt = None
    created_by_name: str | None = None


class ExamDetail(ExamListItem):
    professional: str | None = None
    intro_html: str | None = None
    sections: list[ExamSectionDetail] = Field(default_factory=list)
    #: 上次组卷留下的缺口（存在 rule_config 里）。空列表 = 没有缺口或还没组过卷。
    shortfalls: list[Shortfall] = Field(default_factory=list)
    #: 卷面校验结果（详情页只读展示；`ok=false` 时不允许发布）
    validation: "ExamValidateOut | None" = None
    created_at: datetime
    can_edit: bool = False
    can_compose: bool = False
    can_publish: bool = False
    can_unpublish: bool = False
    can_delete: bool = False


# ---------------------------------------------------------------- 组卷 / 校验 / 发布


class ExamComposeIn(BaseModel):
    """自动组卷入参。

    两种用法：
    - `rule_id`：按库里的组卷规则抽题（推荐，规则可复用）
    - `rules`：直接内联约束（临时组卷，不落规则表）

    两者都不传 = 用试卷创建时存的分段（`exam_sections`）自动推导约束。
    均传时 **`rules` 优先**（内联更明确）。
    """

    rule_id: int | None = Field(None, description="组卷规则 ID")
    rules: list[RuleItem] | None = Field(None, min_length=1, description="内联抽题约束")
    subject_id: int | None = Field(None, description="内联组卷时的科目（默认取试卷的科目）")
    #: 复现用的随机种子。同一份库 + 同一种子 → 同一张卷，便于验收与排障。
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    replace: bool = Field(
        True, description="是否清空已有题目再组卷（false = 追加，重复题自动跳过）"
    )
    apply_sections: bool = Field(
        True, description="是否按规则重写卷面分段（题型/题量/分值）"
    )

    @model_validator(mode="after")
    def _check(self) -> "ExamComposeIn":
        if self.rules is not None:
            _ensure_composable(self.rules)
        return self


class ExamComposeOut(BaseModel):
    exam_id: BigIntStr
    status: ExamStatus
    question_count: int
    total_score: float
    duration_min: int
    sections: list[ExamSectionOut] = Field(default_factory=list)
    #: ★ 缺口。非空 = 有规则没凑够题。**不会用别的题顶上**。
    shortfalls: list[Shortfall] = Field(default_factory=list)
    #: 语义化结论，前端直接显示，不必自己拼
    message: str
    elapsed_ms: int = 0


class ExamValidateIssue(BaseModel):
    level: Literal["error", "warning"]
    code: str
    message: str


class ExamValidateOut(BaseModel):
    exam_id: BigIntStr
    ok: bool = Field(..., description="无 error 级问题才为 true；发布接口要求 true")
    errors: list[ExamValidateIssue] = Field(default_factory=list)
    warnings: list[ExamValidateIssue] = Field(default_factory=list)
    question_count: int
    total_score: float
    checked_at: datetime


class ExamPublishIn(BaseModel):
    #: 发布后是否允许继续编辑题目（默认 false：发布即冻结卷面）
    allow_edit_after_publish: bool = False


class ExamPublishOut(BaseModel):
    exam_id: BigIntStr
    status: ExamStatus
    published_at: datetime
    question_count: int
    total_score: float
    #: 本次锁定的题目版本数（即卷面题数）
    locked_versions: int
    message: str


class ExamSoftDeleteOut(BaseModel):
    """软删除结果。字段与 Batch 4 的 `QuestionDeleteOut` 对齐。"""

    id: BigIntStr
    title: str
    #: 删除前的状态（已发布的卷被归档时，调用方需要知道它原来是什么状态）
    previous_status: ExamStatus
    is_deleted: bool = True
    #: 仍被软删除的卷面题数（题目本身不动，归档的只是"这张卷"）
    question_count: int = 0
    message: str


class ExamRestoreOut(BaseModel):
    """试卷恢复结果（与归档对称）。

    **幂等**：试卷本来就没归档时，`already_active=true`，接口返回 `code=0`。
    """

    id: BigIntStr
    title: str
    is_deleted: bool = False
    status: ExamStatus
    #: 本来就是未归档状态（走了幂等分支，**没有产生任何写入**）
    already_active: bool = False
    message: str


# ---------------------------------------------------------------- 内部校验


def _ensure_composable(rules: list[RuleItem]) -> None:
    """`case_sub` 不能作为独立抽题单位（会得到没有材料的孤儿子问）。"""
    bad = [r.type for r in rules if r.type == "case_sub"]
    if bad:
        raise ValueError(
            "`case_sub`（案例小问）不能单独抽题：它必须跟随父题（案例背景）一起进卷。"
            "请改为抽 `case` 题。"
        )


def _ensure_unique_sections(sections: list[ExamSectionIn]) -> None:
    """同一题型在同一份卷里可能出现多段（如"单选（基础）"+"单选（拔高）"），
    所以只约束 `sort_no` 不重复。"""
    seen: set[int] = set()
    for s in sections:
        if s.sort_no in seen:
            raise ValueError(f"分段 sort_no 重复：{s.sort_no}")
        seen.add(s.sort_no)


# `ExamDetail.validation` 是自引用（先有 ExamValidateOut 的定义顺序问题），此处解析前向引用。
ExamDetail.model_rebuild()

__all__ = [
    "COMPOSABLE_TYPES",
    "OBJECTIVE_TYPES",
    "ExamComposeIn",
    "ExamComposeOut",
    "ExamCreateIn",
    "ExamDetail",
    "ExamListItem",
    "ExamPublishIn",
    "ExamPublishOut",
    "ExamQuestionItem",
    "ExamRestoreOut",
    "ExamSectionDetail",
    "ExamSectionIn",
    "ExamSectionOut",
    "ExamSoftDeleteOut",
    "ExamStatus",
    "ExamType",
    "ExamUpdateIn",
    "ExamValidateIssue",
    "ExamValidateOut",
    "PaperRuleCreateIn",
    "PaperRuleDeleteOut",
    "PaperRuleOut",
    "PaperRuleUpdateIn",
    "RuleItem",
    "RuleStatus",
    "RuleStrategy",
    "Shortfall",
]
