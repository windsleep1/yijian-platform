"""C 端（学员端）接口的请求 / 响应模型。

为什么单独一个文件：这一批是**普通学员**用的接口（**没有权限码，靠会话**），
与管理端 `admin*.py` 的形状、可见性规则都不同（管理端接口会返答案，C 端不能）。
放在一起迟早有人把管理端字段抄过来 —— 而 `docs/22` §5.3 已经把
"C 端**一个都不许**复用 `/admin/*`"写成红线。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

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
    """创建一次练习。**两种题源共用一个接口**。

    - `mode='chapter'`（默认，P2b-1）：从科目 / 章节抽题 ⇒ **`subject_id` 必填**；
    - `mode='wrong'`（**P2c-3 · 错题重练**）：从**我自己的错题本**抽题 ⇒ `subject_id`
      **只当筛选**（不给 = 全部科目 —— 跨科目重练是合法场景，所以
      `practice_sessions.subject_id` 允许为 NULL）。`question_ids` 给具体题
      （重练这一题 / 这一组）；不给则按「最近错的在前」抽 `count` 道。

    ★ 为什么**不新开** `POST /practice/wrong-sessions`：两者产出的都是
      `practice_sessions` 行、走**同一套**答题 / 交卷 / 报告链路，差别只有"题从哪来"。
      新开接口会把 `submit_answer` / `get_session` / `report` / `finish` 全复制一遍
      —— 而它们的语义**完全一样**，复制出来的第二份迟早分叉。
    """

    mode: Literal["chapter", "wrong"] = "chapter"
    subject_id: BigIntStr | None = None
    chapter_id: BigIntStr | None = None
    #: 只对 `mode='wrong'` 有意义：重练**指定的这些题**（按调用方给的这一组）。
    question_ids: list[BigIntStr] | None = None
    count: int = Field(10, ge=1, le=100)

    @model_validator(mode="after")
    def _check(self) -> "SessionCreateIn":
        """★ 必填项**按 mode 分叉**。

        写成 `model_validator` 而不是 service 里的 if，是为了让"缺字段"仍然是 **422**
        —— 与改造前（`subject_id: BigIntStr` 必填）**对外表现一致**，
        免得多出一种"以前 422、现在 50001"的新形态（那种变化只会让调用方困惑）。
        """
        if self.mode == "chapter" and not self.subject_id:
            raise ValueError("mode='chapter' 需要 subject_id")
        if self.mode != "wrong" and self.question_ids:
            raise ValueError("question_ids 只在 mode='wrong' 下有意义")
        return self


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
    #: ★ 用户级（**题目级**）状态 —— 与 `question_marks` / `favorites` 对账，
    #:   **不是** `practice_items.marked`（那是「这次练习的卷面标记」，粒度不对、未接线）。
    marked: bool = False
    favorited: bool = False
    #: ⚠️ **只有"已作答"或"会话已结束"时才非空** —— 见 practice_service 的 `_may_reveal`。
    answer: dict[str, Any] | None = None
    analysis: str | None = None
    analysis_html: str | None = None
    #: ★ 这道题下我写了几条笔记（`notes` 未软删）。**一次查询出**（见 `_SELECT_ITEMS`），
    #:   不是每题一次 —— 20 题就是 20 次往返（N+1）。
    note_count: int = 0


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


class WrongSubjectOut(BaseModel):
    """错题本筛选用的科目分面（**带条数**）。

    ★ 为什么把它放进列表响应、而不是让前端去调 `/subjects`：
      `/subjects` 返的是**全部**科目（6 个），而错题本很可能只涉及其中 1~2 个 ——
      界面上会出现 4 个点了没反应的 chip。分面（facet）是"这批数据的分布"，
      它只有和列表一起算才准。
    """

    subject_id: BigIntStr
    name: str
    count: int


class WrongItemOut(BaseModel):
    """错题本的一行。**不含选项与正确答案** —— 那是详情的事。

    ⚠️ 列表刻意**只给题干摘要**：一页 20 题把选项也带上，首屏就白等几百 KB，
      而列表页的用处是"认出这是哪道题"。
    """

    question_id: BigIntStr
    subject_id: BigIntStr | None = None
    subject_name: str | None = None
    chapter_name: str | None = None
    type: str
    stem: str
    #: 错过几次（`_UPSERT_WRONG` 每次答错 +1）。
    wrong_count: int
    #: 之后又答对过几次（`_BUMP_RETRY_CORRECT`）。
    retry_correct: int
    mastered_level: int
    last_wrong_at: datetime | None = None
    #: ★ 我标记过它吗（`question_marks`）—— 错题本的「已标记」筛选用它。
    marked: bool = False
    #: ★★ 约定 T：题目下架后这一行**仍然在**，这里给 false（前端标「题目已下架」、禁掉「重练」）。
    question_available: bool = True


class WrongListOut(BaseModel):
    total: int
    page: int
    page_size: int
    subjects: list[WrongSubjectOut] = []
    items: list[WrongItemOut] = []


class CollectionItemOut(BaseModel):
    """收藏 / 标记 列表的一行 —— 形状与 `WrongItemOut` **刻意保持一致**。

    ★ 为什么一致：两个列表页的分面 / 空态 / 分页判据全都一样（那三条是 P2c-2 拿事故换来的），
      形状一致才能**直接复用**前端那一套（各写一套迟早漂）。
    """

    question_id: BigIntStr
    subject_id: BigIntStr | None = None
    subject_name: str | None = None
    chapter_name: str | None = None
    type: str
    stem: str
    stem_html: str | None = None
    #: 进这个列表的时间（收藏时间 / 标记时间）—— 列表按它倒序。
    collected_at: datetime
    #: ★ 两个状态**都给**（一道题可以既收藏又标记）—— 前端两个页签共用一次响应。
    marked: bool = False
    favorited: bool = False
    #: ★★ 约定 T：题目下架后这一行**仍然在**，这里给 false（前端标「题目已下架」、取消按钮仍可用）。
    #: ★ 这道题在我的**错题本**里吗 —— 决定列表那一行**能不能链到** `/practice/wrong/{qid}`。
    #:   实测缺陷（批次 2）：那个页面要求错题本里有这道题（无 ⇒ 404），而**收藏了但从没错过**
    #:   的题很常见 ⇒ 原来每行都渲染成链接，等于**一半的点开是报错页**。
    in_wrong_book: bool = False
    question_available: bool = True


class CollectionListOut(BaseModel):
    """收藏 / 标记 列表。`kind` 回显请求的那一种（前端两个页签据此高亮）。"""

    kind: str
    total: int
    page: int
    page_size: int
    #: 分面**恒为全量**（不随 `subject_id` 收缩）—— 与错题本同一条判据，故复用同一个模型。
    subjects: list[WrongSubjectOut] = []
    items: list[CollectionItemOut] = []


class FlagIn(BaseModel):
    """标记 / 收藏的写入体。`on=true` 置上、`false` 取消。"""

    on: bool = True


class FlagOut(BaseModel):
    """写入后的**两个**状态（前端两个按钮共用一次响应，省一次往返）。"""

    question_id: BigIntStr
    marked: bool = False
    favorited: bool = False


# ============================================================ 笔记（P2c-5）

#: 笔记正文的**产品**上限（`strip()` 之后）。★ 与 `notes.content TEXT` 无关 —— 那是 DB 上限；
#: 不设这条的话前端能存进"一条只有空格的卡片"，而它在列表里**看起来像加载失败**。
NOTE_MAX_LEN = 2000

#: ★ `strip_whitespace=True` + `min_length=1` ⇒ **只有空格 = 空** ⇒ 由 pydantic 返 **422**，
#:   不用业务代码去判空（同一个规则只写一遍：这里）。
NoteContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_MAX_LEN)
]


class NoteCreateIn(BaseModel):
    content: NoteContent


class NoteUpdateIn(BaseModel):
    content: NoteContent


class NoteOut(BaseModel):
    """一条笔记（写操作的返回、以及「本题的笔记」用）。"""

    id: BigIntStr
    question_id: BigIntStr
    content: str
    #: ★★ 约定 T：题目下架后这条笔记**照样给**，这里为 `false`（前端标「题目已下架」）。
    question_available: bool = True
    created_at: datetime
    updated_at: datetime
    #: ★ `updated_at` 由触发器 `trg_notes_updated` 维护 —— 应用侧**不写**（不变量 8）。


class NoteListItemOut(NoteOut):
    """列表里的一行：比 `NoteOut` 多"这道题长什么样"—— 列表的用处是**认出那是哪道题**。

    ★ 与 `WrongItemOut` / `CollectionItemOut` 一样**刻意不带选项与答案**：
      一页 20 条把选项也带上，首屏就白等几百 KB。
    """

    subject_id: BigIntStr | None = None
    subject_name: str | None = None
    chapter_name: str | None = None
    type: str | None = None
    stem: str | None = None


class NoteListOfQuestionOut(BaseModel):
    """「这道题下的笔记」—— 不分页，所以没有 total / page。"""

    items: list[NoteOut] = []


class NoteListOut(BaseModel):
    total: int
    page: int
    page_size: int
    subjects: list[WrongSubjectOut] = []
    items: list[NoteListItemOut] = []


class WrongDetailOut(BaseModel):
    """错题详情 —— **含正确答案与解析**。

    ★★ 与 P2b-1 那条"未作答的题不返 `answer`"**不矛盾**，理由必须写清：
      · P2b-1 防的是"**没答就看到答案**"；
      · 错题本的**前提就是"你已经答过了、而且答错了"**（`wrong_questions` 有行才给看）。
      ⇒ 两条规则的目标一致：**答案只在「你已经和这道题交过手」之后才给**。

    ⚠️ 因此这个接口**必须有"真的错过"这道门**（`wrong_questions` 里有行）——
      否则它就变成一个**用 `question_id` 遍历题库拿答案的后门**（比不返答案更糟）。
      拒绝用 `40401`（**不是 403**：403 会告诉对方"这道题存在、你没权限"）。
    """

    question_id: BigIntStr
    subject_id: BigIntStr | None = None
    subject_name: str | None = None
    chapter_name: str | None = None
    type: str
    stem: str
    stem_html: str | None = None
    options: list[QuestionOptionOut] = []
    #: 归一形态的正确答案（判断题统一成 `[true]` / `[false]`）。
    answer: dict[str, Any]
    analysis: str | None = None
    analysis_html: str | None = None
    wrong_count: int
    retry_correct: int
    mastered_level: int
    reason_tag: str | None = None
    last_wrong_at: datetime | None = None


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
