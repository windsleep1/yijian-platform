"""C 端（学员端）接口的请求 / 响应模型。

为什么单独一个文件：这一批是**普通学员**用的接口（**没有权限码，靠会话**），
与管理端 `admin*.py` 的形状、可见性规则都不同（管理端接口会返答案，C 端不能）。
放在一起迟早有人把管理端字段抄过来 —— 而 `docs/22` §5.3 已经把
"C 端**一个都不许**复用 `/admin/*`"写成红线。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.types import BigIntStr

#: 考试年份的合理区间。**不收 1900 也不是洁癖**：`exam_year` 直接参与
#: "还有几天考试"的倒计时计算，离谱的值（0 / 负数 / 2999）会让前端算出
#: 几十年或负数的倒计时 —— 当场拒掉比事后排查便宜。
EXAM_YEAR_MIN = 2000
EXAM_YEAR_MAX = 2100


class SubjectOut(BaseModel):
    """科目（公共课 + 专业课）。引导页的"选专业"就是从 `category='professional'` 里挑。"""

    id: BigIntStr
    code: str
    name: str
    short_name: str | None = None
    exam_level: str
    category: str
    #: 专业课的所属专业码（`jz` / `sz` …）；公共课为 `None`。
    professional: str | None = None
    full_score: int
    pass_score: int
    duration_min: int
    color: str | None = None
    sort_no: int

    model_config = {"from_attributes": True}


class ProfileUpdateIn(BaseModel):
    """引导 / 资料更新。**全部字段可选** —— 引导页分两步提交，不该要求一次给全。

    ⚠️ 语义（刻意的）：**只写"这次真的传了"的字段**。
      - 没传的字段 → **保持原值**（不是清空）；
      - 显式传 `null` → 也**当成没传**（见 `profile_service` 的注释与用例）。
      为什么不做"传 null 就清空"：C 端表单直接 `JSON.stringify(state)` 时，
      未填的字段天然是 `null` —— 那种语义会把用户没动过的资料**误清空**。
    """

    exam_level: Literal["yijian", "erjian"] | None = None
    professional: str | None = Field(None, max_length=24)
    exam_year: int | None = Field(None, ge=EXAM_YEAR_MIN, le=EXAM_YEAR_MAX)
    #: ⚠️ 科目 ID 用**字符串**：雪花 ID 超出 JS 安全整数，用数字会被静默舍入
    #:   （`schemas/types.py` 抬头有实测）。这里当场校验，不指望前端记得。
    target_subjects: list[str] | None = None
    target_score: int | None = Field(None, ge=0, le=1000)
    daily_goal_min: int | None = Field(None, ge=5, le=600)

    @field_validator("target_subjects")
    @classmethod
    def _ids_are_digit_strings(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if len(v) > 20:
            raise ValueError("目标科目最多 20 个")
        for item in v:
            if not item.isdigit():
                raise ValueError(f"目标科目 ID 必须是数字字符串（收到 {item!r}）")
        return v


# ============================================================ P2b-1 · 刷题（选章节 + 答题）
#
# 这组模型里有两条**刻意的**设计，都不是顺手写的：
#
# ① **答案 / 解析在 `SessionItemOut` 上是可选字段**（`answer` / `analysis`），
#    而不是"另开一个 results 接口"。理由：`docs/24` §3.2 #5 把"一个 resource、两种状态"
#    定成了这个接口的**形状**（`doing` 不返答案、`finished` 返全）——
#    可见性判断因此**只留最外层一处**（硬约定 A：读路径只留一处可见性判断）。
#
# ② `chapter.question_count` **不用表里的冗余列**。`db/schema.sql` 写着它"定时刷新"，
#    但**没有任何定时任务在刷它**（2026-09-29 核实：那张表里这一列还是建表默认值 0）。
#    ⇒ 选章节页要是读它，就会"每个章节都是 0 题"——**看起来像没有题库，实际有 6000 道**。
#    所以这里一律**实时从 `questions` 算**（见 practice_service.list_chapters）。
#    判据同硬约定 F：**"读路径判断了某状态" ≠ "该状态可达"** —— 一个没人写的冗余列，
#    与"这个字段存在"是两件事。


class QuestionOptionOut(BaseModel):
    """题目的一个选项。**不含 `is_correct`** —— C 端拿不到答案。"""

    label: str
    content: str
    content_html: str | None = None


class ChapterOut(BaseModel):
    """章节（带"我做过多少"）。选章节页的数据源。"""

    id: BigIntStr
    parent_id: BigIntStr | None = None
    code: str
    name: str
    level: int
    outline_ref: str | None = None
    weight: float
    sort_no: int
    #: 这个章节（**含子章节**）下**真的能练**的题数 —— 实时算，不是表里的冗余列。
    question_count: int = 0
    #: 我在这儿做过的题数 / 答对的题数（同样含子章节）。
    my_answered: int = 0
    my_correct: int = 0


class SessionCreateIn(BaseModel):
    """创建一次练习。P2b-1 只支持 `mode='chapter'`（`chapter_id` 必填）。"""

    subject_id: BigIntStr
    chapter_id: BigIntStr | None = None
    count: int = Field(10, ge=1, le=100)


class SessionItemOut(BaseModel):
    item_id: BigIntStr
    seq: int
    question_id: BigIntStr
    type: str
    stem: str
    stem_html: str | None = None
    options: list[QuestionOptionOut] = []
    #: 我提交过的答案（没答过 = None）。
    my_value: list[Any] | None = None
    is_correct: bool | None = None
    score: float | None = None
    answered: bool = False
    #: ⚠️ **只有"已作答"或"会话已结束"时才非空** —— 见 practice_service 的 `_may_reveal`。
    answer: dict[str, Any] | None = None
    analysis: str | None = None
    analysis_html: str | None = None


class SessionProgressOut(BaseModel):
    total: int
    answered: int
    correct: int
    score: float


class SessionOut(BaseModel):
    """一次练习（`doing` = 断点恢复；`finished` = 报告）。"""

    id: BigIntStr
    mode: str
    status: str
    subject_id: BigIntStr | None = None
    subject_name: str | None = None
    chapter_id: BigIntStr | None = None
    chapter_name: str | None = None
    title: str
    #: 第一道**还没作答**的 item（全答完 = None）—— "刷新后还在"就是靠它回到原处。
    current_item_id: BigIntStr | None = None
    total: int
    answered: int
    correct: int
    score: float
    items: list[SessionItemOut] = []


class KpStatOut(BaseModel):
    """**结果页的一个知识点条**（按已作答的题聚合）。

    ⚠️ `accuracy` 是可以为 `None` 的 —— 虽然这里 `total` 恒 ≥ 1（只统计有作答的题），
       但"零分母返 null"是本项目**红线**，宁可留一个永远不会为 null 的可空类型，
       也不要留一个"看起来不会 null 所以没人防"的类型。
    """

    knowledge_point_id: BigIntStr | None = None
    #: `questions.knowledge_point_id` 是**可空**的 ⇒ 没归类的题聚成一条，名字给「未归类」
    #: （留空字符串会让前端显示成一条没有标题的条 —— 那比"未归类"更让人困惑）。
    name: str
    total: int
    correct: int
    accuracy: float | None = None


class SessionReportOut(BaseModel):
    """一次练习的**报告**（P2c-1 结果页）。

    ★ 与 `SessionOut` 的区别：**不返 `items`**。结果页要的是"这次练得怎么样"（聚合），
      不是"每道题的解析"（那是 `GET /practice/sessions/{id}` 的事）——
      一个 100 题的 session 把逐题解析塞进报告，只会让首屏多等几百 KB。
    """

    id: BigIntStr
    mode: str
    status: str
    title: str
    subject_id: BigIntStr | None = None
    subject_name: str | None = None
    chapter_id: BigIntStr | None = None
    chapter_name: str | None = None

    total: int
    answered: int
    correct: int
    score: float
    #: ★★ **零分母返 `null`**（红线）：一道题都没答时 `0/0` 不是 0%，是"没有数据"。
    #:    前端因此必须显示「—」而不是「0%」——后者会让用户以为"我全错了"。
    accuracy: float | None = None
    duration_sec: int
    started_at: datetime | None = None
    finished_at: datetime | None = None

    #: 按正确率**升序**（最弱的在前）—— 结果页的用处是"知道该补哪儿"，
    #: 而不是"看我多强"。所以最差的排第一。
    by_kp: list[KpStatOut] = []


class AnswerIn(BaseModel):
    """提交一道题。

    ⚠️ `value` **故意放宽成 `list[Any]`**：判分要看题目类型，而类型得查库才知道 ——
      在这里按"选择题"校验会把判断题一起拒掉。类型化的校验放在 service 里做，
      好处是能给出"**是哪一道题、它是什么类型**"的错误（硬约定：拒绝要说清是哪一条）。
    """

    item_id: BigIntStr
    #: 选择题 = 标号数组（`["B"]` / `["A","C"]`）；判断题 = `[true]` / `[false]`。
    value: list[Any] = Field(min_length=1, max_length=8)


class AnswerResultOut(BaseModel):
    item_id: BigIntStr
    is_correct: bool
    score: float
    correct_answer: dict[str, Any]
    my_value: list[Any]
    analysis: str | None = None
    analysis_html: str | None = None
    #: 这道题**之前已经答过**（"目标状态已达成" ⇒ **零写入**，硬约定 C）。
    idempotent: bool = False
    session: SessionProgressOut


def parse_answer_doc(raw: Any) -> dict[str, Any]:
    """把库里的 `answer` / `user_answer` JSONB 解成 **dict**。

    ⚠️ 为什么需要它：这两列是 `JSONB`，而驱动交回来的可能是 `str`（未解析的 JSON 文本）
      也可能是已经解析好的 `dict` —— **取决于列类型与驱动版本**。
      写死一种形态会在另一种上直接炸，而报错会指向"数据坏了"（实际是取值方式错了）。
      `dict(str)` 更糟：那会把字符串当"可迭代的键值对"去拆，得到一个**看似正常**的错值。
    """
    import json as _json

    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def parse_answer_value(raw: Any) -> list[Any]:
    """取"答案标号数组"：`{"value": ["B"]}` → `["B"]`。

    兼容三个形态（都实测过会出现在返回值里）：`dict`（`{"value": [...]}`）、
    `str`（JSON 文本）、以及**裸 `list`**（测试里直接构造的行）。
    """
    doc = parse_answer_doc(raw)
    if doc:
        v = doc.get("value")
        return v if isinstance(v, list) else []
    return raw if isinstance(raw, list) else []
