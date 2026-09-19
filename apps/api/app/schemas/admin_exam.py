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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """编辑试卷的**元数据**。未传的字段沿用现值。

    ## ⚠️ 这里**不接受 `sections`** —— 结构防护，不是靠约定

    原先 `sections` 是这个模型上的一个可选字段，而 `update_exam` 收到它就会
    `DELETE FROM exam_questions WHERE exam_id = ...`（**整卷题目清空**）。
    一个和 `title` / `duration_min` 并列的字段带着这种副作用，签名上完全看不出来 ——
    前端"把详情对象原样回传"就中招了（坑 42）。

    "前端别乱传"是**约定**，防不住；所以改成**结构上不可能**：

    - `extra="forbid"`：任何未知字段一律拒绝（顺带防住将来新加的错字段）；
    - `sections` 由 `_reject_sections` 单独给一条**可执行的错误信息** ——
      不是笼统的 "Extra inputs are not permitted"，而是直接告诉你该用哪个接口。

    改卷面结构请用 `PUT /admin/exams/{id}/sections`（需要 `expected_question_count`）。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)
    type: ExamType | None = None
    professional: str | None = Field(None, max_length=24)
    exam_year: int | None = Field(None, ge=2000, le=2100)
    paper_no: str | None = Field(None, max_length=32)
    duration_min: int | None = Field(None, ge=1, le=600)
    pass_score: float | None = Field(None, ge=0, le=1000)
    intro_html: str | None = None
    is_free: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_sections(cls, data: Any) -> Any:
        """把 `sections` 拦下来，并给出**下一步该干什么**。

        比通用的 "extra inputs not permitted" 多一句可执行的指引 ——
        这正是本坑里最缺的东西（调用方知道错在哪，却不知道该往哪走）。
        """
        if isinstance(data, dict) and "sections" in data:
            raise ValueError(
                "sections 已从本接口剥离（它会整卷清空题目，必须显式确认）。"
                "改卷面结构请用 PUT /admin/exams/{id}/sections，"
                "并传 expected_question_count=<你读到的当前卷面题数>。"
            )
        return data


class ExamSectionsReplaceIn(BaseModel):
    """**重建卷面结构**（唯一允许的入口）。

    这是全项目**破坏性最强**的写操作之一：它会删掉这张卷现有的全部
    `exam_questions` 行再按新分段重建（分段是卷面的骨架，换骨架必然要重排题目）。
    题目本身在题库里不受影响。

    所以它要求**两重确认**：

    1. `expected_question_count` —— 调用方读到的**当前卷面题数**。
       与库里不一致就拒绝（"卷面已变化，请刷新后重试"）。
       这既是防误操作，也是**乐观并发**：两个人同时改一张卷时，
       后提交的那个一定对不上，不会把前一个人的题默默清掉。
    2. 卷面题数净减少超过 `exam.mass_question_loss_threshold`（默认 10）时，
       其他写路径会被兜底拦下并把调用方指到这里 —— 这里是**唯一的出口**，
       所以不再二次拦截（否则提示会变成循环）。
    """

    sections: list[ExamSectionIn] = Field(..., min_length=1, max_length=30)
    expected_question_count: int = Field(
        ...,
        ge=0,
        description=(
            "调用方读到的**当前卷面题数**（GET 详情里的 `question_count`）。"
            "与库里不一致则 40901 拒绝，不写库"
        ),
    )

    @model_validator(mode="after")
    def _check_sections(self) -> "ExamSectionsReplaceIn":
        _ensure_unique_sections(self.sections)
        return self


class PaperRulePreviewIn(BaseModel):
    """**试算**一组抽题规则能抽到多少题（dry-run，不写库）。

    存在的理由：规则编辑页要在**保存之前**告诉用户"这条规则现在能抽到什么程度"。
    没有它，用户只能"保存 → 建卷 → 组卷 → 发现题库不足 → 回来改"，
    一轮下来要好几次往返，而且每次都会真实落库。

    ⚠️ 判据必须和真正组卷**完全一致**（共用 `_draw_rule`）——
    否则试算说"能抽满"、真组卷却报缺口，用户会彻底不信这个预览。
    """

    subject_id: int = Field(..., description="在哪个科目里抽题")
    rules: list[RuleItem] = Field(..., min_length=1, max_length=30)
    strategy: RuleStrategy = "random"
    seed: int | None = Field(
        None, ge=0, le=2**31 - 1,
        description="传了就固定抽样结果（同一 seed + 同一题库 → 同一结果，方便对比调参）",
    )
    include_sample: bool = Field(True, description="是否回传抽到的题（前端展示抽题明细用）")
    sample_limit: int = Field(12, ge=0, le=50, description="最多回传几道样例题")

    @model_validator(mode="after")
    def _check_rules(self) -> "PaperRulePreviewIn":
        # 与建 / 改规则**同一条约束**：case_sub 不能单独抽题。
        # 试算要能提前拦住它，否则用户在预览页看到"能抽满"，
        # 保存时才被拒 —— 又白跑一趟，而这正是预览要消灭的往返。
        _ensure_composable(self.rules)
        return self

    # 刻意**不**约束规则之间唯一：两条同题型的规则带不同筛选条件是很正常的用法
    # （"单选（基础）20 道" + "单选（拔高）10 道"），去重反而挡住了合理配置。
    # 题数 >0、题型合法这些由 `RuleItem` 自身约束。


class PreviewQuestionItem(BaseModel):
    """试算抽到的一道题（**不落库**，仅供预览）。"""

    question_id: BigIntStr
    question_type: QType
    stem_preview: str = ""
    difficulty: int | None = None
    chapter_id: BigIntStrOpt = None
    score: float
    #: 这条题来自第几条规则（从 0 起）
    rule_index: int
    rule_label: str


class RulePreviewItem(BaseModel):
    """单条规则的试算结果。"""

    rule_index: int
    rule_label: str
    question_type: QType
    need: int
    got: int
    missing: int
    score: float
    #: 放宽阶梯每一档的候选数。前端可据此解释**为什么抽不到**
    #: （例如"限定题型+难度+知识点时只有 3 道，放宽到只限题型也只有 12 道"）
    stage_counts: dict[str, int] = Field(default_factory=dict)


class PaperRulePreviewOut(BaseModel):
    ok: bool = Field(..., description="所有规则都凑够了才是 true")
    subject_id: BigIntStr
    total_need: int
    total_got: int
    total_missing: int
    #: 按**实际抽到**的题算
    total_score: float
    #: 按**理论抽满**算（题库充足时的满分），用来对比"差了多少分"
    planned_score: float
    items: list[RulePreviewItem] = Field(default_factory=list)
    shortfalls: list[Shortfall] = Field(default_factory=list)
    sample: list[PreviewQuestionItem] = Field(default_factory=list)
    duration_ms: int = 0
    message: str


class ExamSectionsReplaceOut(BaseModel):
    exam_id: BigIntStr
    #: 重建前**卷面**有多少道题（只统计卷面行，题目本身不受影响）
    removed_questions: int
    question_count: int
    total_score: float
    sections: list[ExamSectionOut] = Field(default_factory=list)
    message: str


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


class ExamAddQuestionsIn(BaseModel):
    """往试卷里加题（手动选题）。

    一次最多 200 道。`question_ids` 会**去重保序**（重复传同一个 id 只算一次）。
    """

    question_ids: list[int] = Field(..., min_length=1, max_length=200)
    section_id: int | None = Field(
        None,
        description="挂到哪个分段。不传则按**题目题型**自动匹配同题型的分段",
    )


class SkippedQuestion(BaseModel):
    """被跳过的题及原因。

    与导入管道同一个哲学：**批量操作不因为其中一条有问题就整批失败**，
    但**必须逐条说清为什么没加进去**，绝不静默丢弃。
    """

    question_id: BigIntStr
    reason: str


class ExamAddQuestionsOut(BaseModel):
    exam_id: BigIntStr
    added: int
    skipped: list[SkippedQuestion] = Field(default_factory=list)
    question_count: int
    total_score: float
    sections: list[ExamSectionOut] = Field(default_factory=list)
    message: str


class ExamRemoveQuestionOut(BaseModel):
    exam_id: BigIntStr
    #: 被移除的卷面行 id（`exam_questions.id`）
    exam_question_id: BigIntStr
    question_id: BigIntStr
    question_count: int
    total_score: float
    sections: list[ExamSectionOut] = Field(default_factory=list)
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
    "ExamAddQuestionsIn",
    "ExamAddQuestionsOut",
    "ExamComposeIn",
    "ExamComposeOut",
    "ExamCreateIn",
    "ExamDetail",
    "ExamListItem",
    "ExamPublishIn",
    "ExamPublishOut",
    "ExamQuestionItem",
    "ExamRemoveQuestionOut",
    "ExamRestoreOut",
    "ExamSectionDetail",
    "ExamSectionIn",
    "ExamSectionOut",
    "ExamSectionsReplaceIn",
    "ExamSectionsReplaceOut",
    "ExamSoftDeleteOut",
    "ExamStatus",
    "ExamType",
    "ExamUpdateIn",
    "ExamValidateIssue",
    "ExamValidateOut",
    "PaperRuleCreateIn",
    "PaperRuleDeleteOut",
    "PaperRuleOut",
    "PaperRulePreviewIn",
    "PaperRulePreviewOut",
    "PaperRuleUpdateIn",
    "PreviewQuestionItem",
    "RuleItem",
    "RulePreviewItem",
    "RuleStatus",
    "RuleStrategy",
    "Shortfall",
    "SkippedQuestion",
]
