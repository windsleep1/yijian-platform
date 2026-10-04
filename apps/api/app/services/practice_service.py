"""刷题（P2b-1）：**选章节 → 建 session → 取 session → 提交判分**（`docs/24` §3.2 的 #2 / #4 / #5 / #6）。

本批的边界（有意划的，不是没做完）：
- **切题 / 答题卡 / 长按标记 / 会话结束（#7）** → P2b-2；
- **主观题**（`case` / `fill` / `essay`）不进来：判分需要评分点或人工，
  抽进来只会得到"永远判错"。所以**抽题时就按 `GRADABLE_TYPES` 排除** ——
  而不是等判分时才发现（那时已经写进了 `practice_items`）。

分层：本文件**不 import `core.deps`**，只认普通参数（与 `question_service` 同规矩）——
这样它才能被单测直接驱动，不必造一个 `CurrentUser`。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import BigInteger, Boolean, Integer, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, not_found
from app.core.idgen import next_id
from app.schemas.answer import judge_bool
from app.schemas.c_end import (
    AnswerResultOut,
    CollectionItemOut,
    CollectionListOut,
    ChapterOut,
    KpStatOut,
    QuestionOptionOut,
    SessionItemOut,
    WrongDetailOut,
    WrongItemOut,
    WrongListOut,
    WrongSubjectOut,
    SessionOut,
    SessionProgressOut,
    SessionReportOut,
    parse_answer_doc,
    parse_answer_value,
)

#: P2b-1 只判**客观题**。见模块抬头：抽题时就排除，不是判分时才拒。
GRADABLE_TYPES: tuple[str, ...] = ("single", "multiple", "judge")

#: ⚠️ 直接拼进 SQL 的**模块常量**（不是用户输入）—— `GRADABLE_TYPES` 是唯一来源，
#:    加一个新题型只需要改上面那一行。用 `= ANY(:arr)` 要额外声明数组类型，
#:    而那条路在 `asyncpg` 上正是坑 73 那一族（裸参数推不出类型）。
_TYPES_SQL = ", ".join(f"'{t}'" for t in GRADABLE_TYPES)


def _marked_exists(qref: str, uref: str = ":uid") -> str:
    """「我标记过这道题吗」——**全站唯一一处**写法。

    ★ **必须在文件顶部**：它被下面那些**模块级 SQL 常量**在 f-string 里立即求值，
      定义在末尾会在 **import 时** NameError（实测 ruff 先报 6 条 F821）。
    ★ 为什么写成函数：这个谓词在**查询列**与**筛选条件**里都要用（错题本的 `marked`
      与 `marked_only`、两个列表的 `marked`）⇒ 手抄两遍必然漂（同族：坑 93）。
    ★ 为什么用 `:uid` 而不是外层表的 `w.user_id`：外层已经 `WHERE user_id = :uid`，
      两者等价，但用 `:uid` 让这个片段**能贴到任何"以我为口径"的查询里**。
    """
    return (
        "EXISTS (SELECT 1 FROM question_marks mk"
        f" WHERE mk.user_id = {uref} AND mk.question_id = {qref})"
    )


def _favorited_exists(qref: str, uref: str = ":uid") -> str:
    """「我收藏过这道题吗」——同上，唯一一处。"""
    return (
        "EXISTS (SELECT 1 FROM favorites fv"
        f" WHERE fv.user_id = {uref} AND fv.target_type = 'question'"
        f" AND fv.target_id = {qref})"
    )


#: 多选题"选对一半"的得分系数。全对 = 1.0，**真子集且无错选** = 0.5，其他 = 0。
#: 是否允许部分分由**题目自己的** `answer.partial_credit` 决定（下面 `grade` 的注释）。
PARTIAL_CREDIT_RATIO = 0.5


# ============================================================ 判断题：库里有两套写法
#
# ★★ 这不是"防御性编程"，是**既有的两个事实**（2026-09-29 实测，都在本仓）：
#
#   · 管理端新建的判断题（`question_service.derive_answer`）→ `{"value": [true]}`
#   · **种子生成器**的判断题（`db/seed/gen_seed_questions.py::gen_judge`）→
#     `{"value": ["A"]}`（表述正确）/ `{"value": ["B"]}`（表述错误），
#     而且**不写 `question_options`**（所以那类题在前端只有"正确 / 错误"两个按钮可点）。
#
# ⚠️ 为什么必须显式归一，而不是"顺手 `bool()` 一下"：
#    `bool("A")` 是 **True** —— 也就是说，把 `["A"]` 当布尔读，
#    会把"**答错的判断题判成对**"。那是本批最危险的一类 bug：**它不报错、还给人满分**。
#    （判据同硬约定 J：先构造"它该报相反结果"的场景 —— 这里的场景就是 `["A"]` 与 `[false]`。）
#
# ⇒ 判分与出参前**都**走 `_judge_bool`；**这是唯一一处**理解这两套写法的地方。
# ⚠️ 待办 **BL-20**：统一库内口径（编号/触发条件/检查点见 `docs/21`）。
def public_answer(qtype: str, correct: list[Any]) -> dict[str, Any]:
    """**出参**用的答案：判断题一律归一到 `[true]` / `[false]`。

    前端因此**不需要知道库里有两套写法** —— 它只看到布尔，把 `true` 显示成"正确"。
    （认不出来时**原样透出**，不做假：宁可显示一个怪字符串，也不要显示一个假的"错误"。）
    """
    if qtype != "judge":
        return {"value": correct}
    as_bool = [judge_bool(x) for x in correct]
    if as_bool and all(b is not None for b in as_bool):
        return {"value": [bool(b) for b in as_bool]}
    return {"value": correct}


# ============================================================ 判分（纯函数，可单测）


def grade(
    qtype: str, correct: list[Any], user: list[Any], partial_credit: bool
) -> tuple[bool, float]:
    """判分。返回 `(是否算全对, 得分系数 0~1)`。

    - `single` / `judge`：集合相等 ⇒ `(True, 1.0)`；否则 `(False, 0.0)`
    - `multiple`：全对 ⇒ `(True, 1.0)`；**真子集且选了的都对**且题目允许部分分
      ⇒ `(False, 0.5)`（**算错但给分** —— 这两个维度必须分开，否则"答对率"会被部分分污染）
    - 其他：`(False, 0.0)`

    ★ **为什么部分分要问题目**：`answer` JSONB 里的 `partial_credit` 就是题目自己的口径 ——
      有些多选题的评分规则是"错一个全扣"。**不替题目做主**（判据同硬约定 A：别在更外层改语义）。

    ★ 归一化（大写 / 去空格 / 去重）在这里做：调用方因此只需要保证"传进来的是一组标号"，
      判分逻辑才可能被单测覆盖。
    """
    if qtype == "judge":
        # ⚠️ 两边都过 `_judge_bool`：库里可能是 `[true]`，也可能是种子的 `["A"]` / `["B"]`。
        # 认不出来的 token 被丢掉 ⇒ 下面那条共用的 `if not c` 会把它判成"错"。
        # **不能猜** —— 猜错的方向是"把错答判成对"（本批最危险的一类 bug）。
        c = {b for b in (judge_bool(x) for x in correct) if b is not None}
        u = {b for b in (judge_bool(x) for x in user) if b is not None}
    else:
        c = {str(x).strip().upper() for x in correct}
        u = {str(x).strip().upper() for x in user}
    if not c:
        # 没有正确答案的题（客观题里不该出现）—— 判错比"判对"安全，
        # 而且这种情况必须能被看见：算对等于把数据缺陷藏进成绩里。
        return False, 0.0
    if u == c:
        return True, 1.0
    if qtype == "multiple" and partial_credit and u and u < c:
        return False, PARTIAL_CREDIT_RATIO
    return False, 0.0


def normalize_user_value(qtype: str, raw: list[Any]) -> list[Any]:
    """按题型校验并归一化用户答案。**拒绝时说清是什么题型、收到了什么**。

    ★ 判断题的**出参一律是布尔**（前端只发 `true` / `false`）；
      但这里也**容忍标号写法**（`"A"` / `"B"` / `"对"` / `"错"`）——
      因为库里有种子那一套写法（见 `_judge_bool` 抬头的说明），
      测试与他人脚本很可能照着库里的样子发。容忍是**归一**，不是"两套都收下"。
    """
    if qtype == "judge":
        if len(raw) != 1:
            raise bad_request("判断题请提交 [true] 或 [false]", 40001)
        b = judge_bool(raw[0])
        if b is None:
            raise bad_request("判断题请提交 [true] 或 [false]", 40001)
        return [b]
    if qtype == "single":
        if len(raw) != 1 or not isinstance(raw[0], str):
            raise bad_request('单选题请提交一个选项标号，例如 ["B"]', 40001)
        return [str(raw[0]).strip().upper()]
    if qtype == "multiple":
        if not all(isinstance(x, str) for x in raw):
            raise bad_request('多选题请提交选项标号数组，例如 ["A","C"]', 40001)
        labels = [str(x).strip().upper() for x in raw]
        if len(set(labels)) != len(labels):
            raise bad_request("多选题不要重复提交同一个标号", 40001)
        return sorted(labels)
    raise bad_request(f"这类题（{qtype}）暂不支持在线判分", 40001)


# ============================================================ 章节（选章节页）


_SELECT_CHAPTERS = text(
    """
    SELECT id, parent_id, code, name, level, outline_ref, weight, sort_no
      FROM chapters
     WHERE subject_id = :sid AND is_deleted = false
     ORDER BY sort_no, id
    """
)


def _live_count_sql() -> str:
    """章节下**真的能练**的题数（按章节直接计数，子树在 Python 里汇总）。

    ⚠️ 为什么不用 `chapters.question_count`：`db/schema.sql` 说它"定时刷新"，
      但**没有任何定时任务在刷它**（2026-09-29 核实：那一列还是建表默认值 0）。
      读它 = 选章节页上每章都显示 0 题 ⇒ **看起来像没有题库，实际有 6000 道**。
      这是硬约定 F 的同族：「列存在」≠「有人写它」。
    """
    return f"""
    SELECT q.chapter_id AS cid, count(*) AS n
      FROM questions q
     WHERE q.subject_id = :sid
       AND q.status = 'published'
       AND q.is_deleted = false
       AND q.chapter_id IS NOT NULL
       AND q.type IN ({_TYPES_SQL})
     GROUP BY q.chapter_id
    """


_SELECT_MY_STATE = text(
    """
    SELECT q.chapter_id AS cid,
           count(*)                                  AS answered,
           count(*) FILTER (WHERE uqs.last_result)    AS correct
      FROM user_question_state uqs
      JOIN questions q ON q.id = uqs.question_id
     WHERE uqs.user_id = :uid
       AND q.subject_id = :sid
       AND q.is_deleted = false
     GROUP BY q.chapter_id
    """
).bindparams(bindparam("uid", type_=BigInteger), bindparam("sid", type_=BigInteger))


def _rollup(
    ids: list[int], parent_of: dict[int, int | None], direct: dict[int, int]
) -> dict[int, int]:
    """把"章节自己的数"汇总成"含子章节的数"（自底向上，一次递归 + 记忆化）。

    ⚠️ 为什么要汇总：选章节页展示的是"这一章我做了多少"，而题挂在**叶子**章节上。
      只报直接数 ⇒ 一级章节永远是 0，用户看到的是"我什么都没做"。
    """
    children: dict[int, list[int]] = {}
    for i in ids:
        p = parent_of.get(i)
        if p is not None and p in parent_of:
            children.setdefault(p, []).append(i)
    out: dict[int, int] = {}

    def walk(node: int) -> int:
        if node in out:
            return out[node]
        out[node] = direct.get(node, 0)  # 先占位，防脏数据造成的环导致无限递归
        total = direct.get(node, 0) + sum(walk(c) for c in children.get(node, []))
        out[node] = total
        return total

    for i in ids:
        walk(i)
    return out


async def list_chapters(db: AsyncSession, *, subject_id: int, user_id: int) -> list[ChapterOut]:
    rows = (await db.execute(_SELECT_CHAPTERS, {"sid": subject_id})).mappings().all()
    ids = [int(r["id"]) for r in rows]
    parent_of = {
        int(r["id"]): (int(r["parent_id"]) if r["parent_id"] is not None else None) for r in rows
    }
    counts = {
        int(r["cid"]): int(r["n"])
        for r in (await db.execute(text(_live_count_sql()), {"sid": subject_id})).mappings().all()
    }
    mine = {
        int(r["cid"]): (int(r["answered"]), int(r["correct"]))
        for r in (await db.execute(_SELECT_MY_STATE, {"uid": user_id, "sid": subject_id}))
        .mappings()
        .all()
    }
    q_roll = _rollup(ids, parent_of, counts)
    a_roll = _rollup(ids, parent_of, {k: v[0] for k, v in mine.items()})
    c_roll = _rollup(ids, parent_of, {k: v[1] for k, v in mine.items()})
    return [
        ChapterOut(
            id=r["id"],
            parent_id=r["parent_id"],
            code=r["code"],
            name=r["name"],
            level=int(r["level"]),
            outline_ref=r["outline_ref"],
            weight=float(r["weight"]),
            sort_no=int(r["sort_no"]),
            question_count=q_roll.get(int(r["id"]), 0),
            my_answered=a_roll.get(int(r["id"]), 0),
            my_correct=c_roll.get(int(r["id"]), 0),
        )
        for r in rows
    ]


# ============================================================ 建 session（#4）


async def _assert_subject_usable(db: AsyncSession, subject_id: int) -> dict[str, Any]:
    row = (
        (
            await db.execute(
                text("SELECT id, name FROM subjects WHERE id = :sid AND status = 'on'"),
                {"sid": subject_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise not_found("科目不存在或已下线", 40401)
    return dict(row)


async def _chapter_prefix(db: AsyncSession, *, chapter_id: int, subject_id: int) -> str:
    """章节的**子树前缀**（`chapters.path` 是物化路径，如 `/1101/`）。"""
    row = (
        (
            await db.execute(
                text(
                    "SELECT path, subject_id FROM chapters WHERE id = :cid AND is_deleted = false"
                ),
                {"cid": chapter_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise not_found("章节不存在", 40401)
    if int(row["subject_id"]) != subject_id:
        # ★ 拒绝时**说清是哪一条**（硬约定 C 的同族）：只说"参数不对"，
        #   调用方得自己猜是科目错了还是章节错了。
        raise bad_request(f"章节 {chapter_id} 不属于科目 {subject_id}", 40001)
    return str(row["path"])


#: 抽题。★ `:chapter_id` / `:cprefix` **必须声明类型** —— 裸参数在
#: `(:chapter_id IS NULL OR q.chapter_id IN (...))` 里推不出类型，
#: 会得到 `AmbiguousParameterError`（坑 73：症状是稳定的 50001，SQL 看着完全正常）。
_PICK_SQL = text(
    f"""
    SELECT q.id
      FROM questions q
      LEFT JOIN user_question_state uqs
             ON uqs.user_id = :uid AND uqs.question_id = q.id
     WHERE q.subject_id = :sid
       AND q.status = 'published'
       AND q.is_deleted = false
       AND q.type IN ({_TYPES_SQL})
       AND (
             :chapter_id IS NULL
             OR q.chapter_id IN (SELECT c.id FROM chapters c WHERE c.path LIKE :cprefix)
           )
     ORDER BY (uqs.id IS NOT NULL), q.id
     LIMIT :cnt
    """
).bindparams(
    bindparam("uid", type_=BigInteger),
    bindparam("sid", type_=BigInteger),
    bindparam("chapter_id", type_=BigInteger),
    bindparam("cprefix", type_=String),
    bindparam("cnt", type_=Integer),
)

#: 错题重练（P2c-3）的抽题。★ 三条硬约束**都写在 SQL 里**，不靠调用方记得：
#:   ① `w.user_id = :uid` —— 别人的错题**永远抽不到**（硬约定 G 的原文：
#:      "按 id 寻址的入口都要自己再拦一次"；`question_ids` 是调用方给的，更不能省）；
#:   ② 只抽未软删、题目仍 `published` 且未软删、且是**可判分题型** ——
#:      否则会建出一个"点进去题没了 / 判不了分"的会话；
#:   ③ 顺序 `last_wrong_at DESC` 与**错题本列表同口径** ⇒ 前端把当前页的 id 原样传进来时，
#:      得到的顺序与列表一致（前端不必再排一次）。
_WRONG_PICK_HEAD = f"""
    SELECT w.question_id AS id
      FROM wrong_questions w
      JOIN questions q ON q.id = w.question_id
     WHERE w.user_id = :uid
       AND w.is_removed = false
       AND q.status = 'published'
       AND q.is_deleted = false
       AND q.type IN ({_TYPES_SQL})
       AND (:sid IS NULL OR w.subject_id = :sid)
"""
_WRONG_PICK_TAIL = """
     ORDER BY w.last_wrong_at DESC, w.question_id
     LIMIT :cnt
"""

_PICK_WRONG_SQL = text(_WRONG_PICK_HEAD + _WRONG_PICK_TAIL).bindparams(
    bindparam("uid", type_=BigInteger),
    bindparam("sid", type_=BigInteger),
    bindparam("cnt", type_=Integer),
)

#: 指定题时用 `IN :qids` + `expanding=True`（**不是** `= ANY(:qids)`）：
#:   `ANY(array)` 在 asyncpg 上要额外声明数组类型 —— 那是坑 73 那一族
#:   （裸参数推不出类型，症状是稳定的 50001 而 SQL 看着完全正常）。
_PICK_WRONG_IDS_SQL = text(
    _WRONG_PICK_HEAD + "       AND w.question_id IN :qids\n" + _WRONG_PICK_TAIL
).bindparams(
    bindparam("uid", type_=BigInteger),
    bindparam("sid", type_=BigInteger),
    bindparam("cnt", type_=Integer),
    bindparam("qids", type_=BigInteger, expanding=True),
)


async def _pick_wrong(
    db: AsyncSession,
    *,
    user_id: int,
    subject_id: int | None,
    question_ids: list[int] | None,
    count: int,
) -> list[int]:
    """从**我自己的错题本**抽题（`mode='wrong'` 的题源）。

    ★ 归属拦截在 SQL 里（`w.user_id = :uid`），**不是**在 Python 里过滤 ——
      这样"查不到就是查不到"，不存在一段"先查出来再判断要不要给"的窗口。
    """
    params: dict[str, Any] = {"uid": user_id, "sid": subject_id, "cnt": count}
    if question_ids:
        params["qids"] = list(question_ids)
        stmt = _PICK_WRONG_IDS_SQL
    else:
        stmt = _PICK_WRONG_SQL
    rows = (await db.execute(stmt, params)).mappings().all()
    return [int(r["id"]) for r in rows]


#: ⚠️ `mode` 与 `subject_id` 都是**参数**（`mode` 以前写死成 'chapter'）：
#:   错题重练共用这条插入；且当一个会话跨科目时 `subject_id` 为 NULL。
_INSERT_SESSION = text(
    """
    INSERT INTO practice_sessions
        (id, user_id, mode, subject_id, chapter_id, title, config, total, status)
    VALUES
        (:id, :uid, :mode, :sid, :cid, :title, CAST(:cfg AS jsonb), :total, 'doing')
    """
).bindparams(
    bindparam("id", type_=BigInteger),
    bindparam("uid", type_=BigInteger),
    bindparam("mode", type_=String),
    bindparam("sid", type_=BigInteger),
    bindparam("cid", type_=BigInteger),
    bindparam("title", type_=String),
    bindparam("total", type_=Integer),
)

_INSERT_ITEM = text(
    """
    INSERT INTO practice_items (id, session_id, user_id, question_id, seq)
    VALUES (:id, :sid, :uid, :qid, :seq)
    """
)


async def create_session(
    db: AsyncSession,
    *,
    user_id: int,
    mode: str = "chapter",
    subject_id: int | None = None,
    chapter_id: int | None = None,
    question_ids: list[int] | None = None,
    count: int = 10,
) -> int:
    """建一次练习，返回 session id。**两种题源共用同一条插入路径**。

    - `mode='chapter'`：★ **抽题顺序是确定性的**（未做过的优先，其次按 id）——
      为了让"数据流跑通"可复现、可断言；随机 / 按掌握度抽题属后续批次。
    - `mode='wrong'`（**错题重练**，P2c-3）：题源 = **我自己的错题本**（`wrong_questions`）；
      `question_ids` 给具体题、不给则按「最近错的在前」抽；`subject_id` 只当筛选
      （跨科目重练 ⇒ 会话的 `subject_id` 允许为 NULL）。

    ⚠️ 抽不到题时**不建空 session**：空 session 会在前端表现为"进去了但没题"，
      而这与"后端炸了"在前端看起来一模一样。宁可当场报 404（带原因）。
      ★ 错题重练的空态尤其要说清 —— **"错题本还没题"是正常状态，不是故障**，
      所以文案要带上"怎么才能有题"（去答题、答错才会进来）。
    """
    picked: list[int]
    title: str

    if mode == "wrong":
        picked = await _pick_wrong(
            db, user_id=user_id, subject_id=subject_id, question_ids=question_ids, count=count
        )
        if not picked:
            raise not_found(
                "错题本里还没有可重练的题 —— 这里只放**你自己答错过**的题，先去练习里做几道。",
                40401,
            )
        title = "错题重练"
        chapter_id = None
    else:
        if subject_id is None:
            # `SessionCreateIn` 已保证；这里再拦一次是因为 service 也能被直接调用（单测就是）。
            raise bad_request("章节练习需要 subject_id", 40001)
        subject = await _assert_subject_usable(db, subject_id)
        cprefix: str | None = None
        chapter_name: str | None = None
        if chapter_id is not None:
            cprefix = await _chapter_prefix(db, chapter_id=chapter_id, subject_id=subject_id)
            cprefix += "%"
            ch = (
                await db.execute(
                    text("SELECT name FROM chapters WHERE id = :cid"), {"cid": chapter_id}
                )
            ).scalar()
            chapter_name = str(ch) if ch is not None else None

        picked = [
            int(r["id"])
            for r in (
                await db.execute(
                    _PICK_SQL,
                    {
                        "uid": user_id,
                        "sid": subject_id,
                        "chapter_id": chapter_id,
                        "cprefix": cprefix,
                        "cnt": count,
                    },
                )
            )
            .mappings()
            .all()
        ]
        if not picked:
            scope = f"章节「{chapter_name}」" if chapter_name else f"科目「{subject['name']}」"
            raise not_found(f"{scope}下还没有可练习的题目", 40401)
        title = f"{chapter_name} 练习" if chapter_name else f"{subject['name']} 练习"

    sid = next_id()
    await db.execute(
        _INSERT_SESSION,
        {
            "id": sid,
            "uid": user_id,
            "mode": mode,
            "sid": subject_id,
            "cid": chapter_id,
            "title": title,
            "cfg": json.dumps(
                {
                    "mode": mode,
                    "subject_id": subject_id,
                    "chapter_id": chapter_id,
                    "question_ids": [str(x) for x in picked],
                    "count": count,
                },
                ensure_ascii=False,
            ),
            "total": len(picked),
        },
    )
    await db.execute(
        _INSERT_ITEM,
        [
            {"id": next_id(), "sid": sid, "uid": user_id, "qid": qid, "seq": i + 1}
            for i, qid in enumerate(picked)
        ],
    )
    # ★★ **必须显式提交**：`app/db/base.py::get_db` **不自动 commit**（它只在异常时 rollback）。
    #   漏了这一句的症状非常有欺骗性：**接口 200 + `code=0` + 返回了一个合法 id**，
    #   但会话关闭时整个事务被丢弃 ⇒ 紧接着 `GET /practice/sessions/<id>` 就是 404。
    #   第一版就是漏了它，而 9 条用例**全红在同一种表现上**（"刚建的练习不存在"），
    #   看上去像"按 id 查的那条 SQL 写错了"或"主从延迟"。—— 记住这条：
    #   **写路径的"成功了但查不到"，先查 commit，再查 SQL。**
    await db.commit()
    return sid


# ============================================================ 取 session（#5）


_SELECT_SESSION = text(
    """
    SELECT s.id, s.mode, s.status, s.subject_id, s.chapter_id, s.title,
           s.total, s.answered, s.correct, s.score,
           s.duration_sec, s.started_at, s.finished_at,
           sub.name AS subject_name, ch.name AS chapter_name
      FROM practice_sessions s
      LEFT JOIN subjects sub ON sub.id = s.subject_id
      LEFT JOIN chapters ch  ON ch.id  = s.chapter_id
     WHERE s.id = :sid AND s.user_id = :uid
    """
).bindparams(bindparam("sid", type_=BigInteger), bindparam("uid", type_=BigInteger))

_SELECT_ITEMS = text(
    f"""
    SELECT i.id AS item_id, i.seq, i.question_id, i.user_answer, i.is_correct, i.score,
           i.answered_at, q.type, q.stem, q.stem_html, q.answer, q.analysis, q.analysis_html,
           {_marked_exists("q.id", "i.user_id")} AS marked,
           {_favorited_exists("q.id", "i.user_id")} AS favorited
      FROM practice_items i
      JOIN questions q ON q.id = i.question_id
     WHERE i.session_id = :sid
     ORDER BY i.seq
    """
)

#: `expanding=True` ⇒ SQLAlchemy 把列表展开成 `(:q_1, :q_2, …)`。
#: （另一条路 `= ANY(:arr)` 要额外声明数组类型 —— 坑 73 那一族，能免则免。）
_SELECT_OPTIONS = text(
    "SELECT question_id, label, content, content_html FROM question_options "
    "WHERE question_id IN :qids ORDER BY question_id, sort_no, label"
).bindparams(bindparam("qids", expanding=True))


def _may_reveal(*, session_status: str, answered: bool) -> bool:
    """★★ **本文件里唯一的答案可见性判断**（硬约定 A：读路径只留一处）。

    - 已作答 ⇒ 可以看答案与解析（"提交 → 显示解析"就是这条）；
    - 会话已结束 ⇒ 未作答的题也可以看（报告页要有完整卷面）。

    ⚠️ 别把它拆到两个 mapper 里去 —— 一旦有两处，早晚会出现
      "列表接口不漏、详情接口漏"这种**只在一条路径上泄题**的 bug。
    """
    return answered or session_status == "finished"


async def get_session(db: AsyncSession, *, user_id: int, session_id: int) -> SessionOut:
    head = (
        (await db.execute(_SELECT_SESSION, {"sid": session_id, "uid": user_id})).mappings().first()
    )
    if head is None:
        # 用 404（不是 403）：403 会变成一个"这个 id 存在但你没权限"的探测口
        # （硬约定 G：按 id 寻址的入口都要自己再拦一次，且不要泄漏存在性）。
        raise not_found("练习不存在", 40401)

    status = str(head["status"])
    rows = (await db.execute(_SELECT_ITEMS, {"sid": session_id})).mappings().all()
    qids = [int(r["question_id"]) for r in rows]
    opt_map: dict[int, list[QuestionOptionOut]] = {}
    if qids:
        for r in (await db.execute(_SELECT_OPTIONS, {"qids": qids})).mappings().all():
            opt_map.setdefault(int(r["question_id"]), []).append(
                QuestionOptionOut(
                    label=r["label"], content=r["content"], content_html=r["content_html"]
                )
            )

    items: list[SessionItemOut] = []
    current: int | None = None
    for r in rows:
        answered = r["answered_at"] is not None
        reveal = _may_reveal(session_status=status, answered=answered)
        if not answered and current is None:
            current = int(r["item_id"])
        answer_doc = parse_answer_doc(r["answer"])
        items.append(
            SessionItemOut(
                item_id=r["item_id"],
                seq=int(r["seq"]),
                question_id=r["question_id"],
                type=str(r["type"]),
                stem=str(r["stem"]),
                stem_html=r["stem_html"],
                options=opt_map.get(int(r["question_id"]), []),
                my_value=(parse_answer_value(r["user_answer"]) if answered else None),
                is_correct=(None if r["is_correct"] is None else bool(r["is_correct"])),
                score=(None if r["score"] is None else float(r["score"])),
                answered=answered,
                # ★ 出参前归一：判断题在库里有两套写法，这里统一成布尔给前端
                #   （前端因此只认识 `true`/`false`，把它显示成"正确"/"错误"）。
                answer=(
                    public_answer(str(r["type"]), parse_answer_value(r["answer"]))
                    if reveal and answer_doc
                    else None
                ),
                analysis=(r["analysis"] if reveal else None),
                analysis_html=(r["analysis_html"] if reveal else None),
                # ★ 用户级（题目级）状态 —— 与 `question_marks` / `favorites` 对账，
                #   **不是** `practice_items.marked`（那是卷面内标记，未接线）。
                marked=bool(r["marked"]),
                favorited=bool(r["favorited"]),
            )
        )

    return SessionOut(
        id=head["id"],
        mode=str(head["mode"]),
        status=status,
        subject_id=head["subject_id"],
        subject_name=head["subject_name"],
        chapter_id=head["chapter_id"],
        chapter_name=head["chapter_name"],
        title=str(head["title"]),
        current_item_id=current,
        total=int(head["total"]),
        answered=int(head["answered"]),
        correct=int(head["correct"]),
        score=float(head["score"]),
        items=items,
    )


# ============================================================ 错题本（P2c-2）


#: ⚠️ 为什么**写两条** SQL 而不是 `(:sub IS NULL OR w.subject_id = :sub)`：
#:   后者让参数在"NULL / 数字"之间摇摆，而 `asyncpg` 要靠类型推参数 ——
#:   这正是坑 73 那一族（裸参数推不出类型）。**能免则免**：两条常量，各自绑定明确的类型。
#: ★ 两条的差异**只有** `AND w.subject_id = :sub` 一行，其余逐字相同。
_WRONG_BASE_SQL = f"""
    SELECT w.question_id, w.subject_id, w.chapter_id, w.wrong_count, w.retry_correct,
           w.mastered_level, w.last_wrong_at,
           s.name AS subject_name, ch.name AS chapter_name,
           q.type, q.stem,
           {_marked_exists("w.question_id")} AS marked
      FROM wrong_questions w
      JOIN questions q  ON q.id = w.question_id
      LEFT JOIN subjects s ON s.id = w.subject_id
      LEFT JOIN chapters ch ON ch.id = w.chapter_id
     WHERE w.user_id = :uid AND w.is_removed = false
       -- ★ `:mo` 为 NULL / false ⇒ 不过滤（`:mo IS NOT TRUE` 与 `NOT :mo` 的差别在这里：
       --   后者在 NULL 时是 NULL ⇒ 整个 WHERE 变 NULL ⇒ **一行都不返**）
       AND (:mo IS NOT TRUE OR {_marked_exists("w.question_id")})
"""

_SELECT_WRONG_PAGE = text(
    _WRONG_BASE_SQL + " ORDER BY w.last_wrong_at DESC, w.question_id DESC LIMIT :lim OFFSET :off"
).bindparams(
    bindparam("uid", type_=BigInteger),
    bindparam("mo", type_=Boolean),
    bindparam("lim", type_=Integer),
    bindparam("off", type_=Integer),
)

_SELECT_WRONG_PAGE_BY_SUBJECT = text(
    _WRONG_BASE_SQL
    + "   AND w.subject_id = :sub"
    + " ORDER BY w.last_wrong_at DESC, w.question_id DESC LIMIT :lim OFFSET :off"
).bindparams(
    bindparam("uid", type_=BigInteger),
    bindparam("sub", type_=BigInteger),
    bindparam("mo", type_=Boolean),
    bindparam("lim", type_=Integer),
    bindparam("off", type_=Integer),
)

_COUNT_WRONG = text(
    f"SELECT count(*) AS n FROM wrong_questions w "
    "WHERE w.user_id = :uid AND w.is_removed = false "
    f"AND (:mo IS NOT TRUE OR {_marked_exists('w.question_id')})"
).bindparams(bindparam("uid", type_=BigInteger), bindparam("mo", type_=Boolean))

_COUNT_WRONG_BY_SUBJECT = text(
    f"SELECT count(*) AS n FROM wrong_questions w "
    "WHERE w.user_id = :uid AND w.is_removed = false AND w.subject_id = :sub "
    f"AND (:mo IS NOT TRUE OR {_marked_exists('w.question_id')})"
).bindparams(
    bindparam("uid", type_=BigInteger),
    bindparam("sub", type_=BigInteger),
    bindparam("mo", type_=Boolean),
)

#: 分面：科目分布。★★ **恒为全量**，不随 `subject_id` 收缩 —— 见 `list_wrong` 里的注释：
#:   分面的作用正是「**让你看见还能切到哪**」，收缩之后就切不过去了（p2c2 走查抓到的真缺陷）。
_SELECT_WRONG_FACETS = text(
    """
    SELECT w.subject_id, COALESCE(s.name, '未归类') AS name, count(*) AS n
      FROM wrong_questions w
      LEFT JOIN subjects s ON s.id = w.subject_id
     WHERE w.user_id = :uid AND w.is_removed = false
     GROUP BY 1, 2
     ORDER BY 3 DESC, 1
    """
).bindparams(bindparam("uid", type_=BigInteger))

#: ★★ 详情有**一道门**：`wrong_questions` 里必须有这个用户 × 这道题的行。
#:    没有 ⇒ 404（**不返答案**）—— 否则这个接口就是"用 question_id 遍历题库拿答案的后门"。
_SELECT_WRONG_DETAIL = text(
    """
    SELECT w.question_id, w.subject_id, w.chapter_id, w.wrong_count, w.retry_correct,
           w.mastered_level, w.reason_tag, w.last_wrong_at,
           s.name AS subject_name, ch.name AS chapter_name,
           q.type, q.stem, q.stem_html, q.answer, q.analysis, q.analysis_html
      FROM wrong_questions w
      JOIN questions q  ON q.id = w.question_id
      LEFT JOIN subjects s ON s.id = w.subject_id
      LEFT JOIN chapters ch ON ch.id = w.chapter_id
     WHERE w.user_id = :uid AND w.question_id = :qid AND w.is_removed = false
    """
).bindparams(bindparam("uid", type_=BigInteger), bindparam("qid", type_=BigInteger))

#: 一次能取多少（上限刻意小）：错题本是"回看"的界面，不是批量导出的通道。
WRONG_PAGE_MAX = 50
WRONG_PAGE_DEFAULT = 20


async def list_wrong(
    db: AsyncSession,
    *,
    user_id: int,
    subject_id: int | None = None,
    marked_only: bool = False,
    page: int = 1,
    page_size: int = WRONG_PAGE_DEFAULT,
) -> WrongListOut:
    """错题列表（按 `last_wrong_at` 倒序 = **最近错的在前**）。

    ★ 倒序的理由：错题本的入口是"我刚错的那些题"，不是"我最老的账"。
      掌握度排序属下一批（那要有 `mastery`，而现在它是 0）。

    ★★ `subjects`（分面）**恒为全量分布**，即使传了 `subject_id`。
      我第一版写成了"只返选中科目"，理由是怕"chip 写着 3、列表 1 条"——
      **那个理由不成立**：一条错题只属于**一个**科目，按科目筛选不会改变该科目自己的条数。
      而它的代价是实的：**切一次科目之后，其他 chip 从 DOM 里消失 ⇒ 切不过去**
      （p2c2 走查在 `[data-facet="1002"]` 上等超时，第一次跑就抓到了）。

    ⚠️ 分页参数在这里**夹了一次**（不是靠调用方）：`page >= 1`、`page_size ∈ [1, 50]`。
      夹在 service 里而不是只在 schema 上，是因为**别的地方（将来的 worker）也会调这个函数**。
    """
    page = max(1, int(page))
    page_size = max(1, min(WRONG_PAGE_MAX, int(page_size)))
    off = (page - 1) * page_size

    if subject_id is None:
        total = int(
            (await db.execute(_COUNT_WRONG, {"uid": user_id, "mo": marked_only})).scalar() or 0
        )
        rows = (
            (
                await db.execute(
                    _SELECT_WRONG_PAGE,
                    {"uid": user_id, "mo": marked_only, "lim": page_size, "off": off},
                )
            )
            .mappings()
            .all()
        )
        facets = (await db.execute(_SELECT_WRONG_FACETS, {"uid": user_id})).mappings().all()
    else:
        total = int(
            (
                await db.execute(
                    _COUNT_WRONG_BY_SUBJECT,
                    {"uid": user_id, "sub": subject_id, "mo": marked_only},
                )
            ).scalar()
            or 0
        )
        rows = (
            (
                await db.execute(
                    _SELECT_WRONG_PAGE_BY_SUBJECT,
                    {
                        "uid": user_id,
                        "sub": subject_id,
                        "mo": marked_only,
                        "lim": page_size,
                        "off": off,
                    },
                )
            )
            .mappings()
            .all()
        )
        facets = (await db.execute(_SELECT_WRONG_FACETS, {"uid": user_id})).mappings().all()

    return WrongListOut(
        total=total,
        page=page,
        page_size=page_size,
        subjects=[
            WrongSubjectOut(subject_id=f["subject_id"], name=str(f["name"]), count=int(f["n"]))
            for f in facets
        ],
        items=[
            WrongItemOut(
                question_id=r["question_id"],
                subject_id=r["subject_id"],
                subject_name=r["subject_name"],
                chapter_name=r["chapter_name"],
                type=str(r["type"]),
                stem=str(r["stem"]),
                marked=bool(r["marked"]),
                wrong_count=int(r["wrong_count"]),
                retry_correct=int(r["retry_correct"]),
                mastered_level=int(r["mastered_level"]),
                last_wrong_at=r["last_wrong_at"],
            )
            for r in rows
        ],
    )


async def get_wrong_detail(db: AsyncSession, *, user_id: int, question_id: int) -> WrongDetailOut:
    """错题详情（含正确答案与解析）。**没真的错过 ⇒ 40401、不返答案**。

    判据见 `WrongDetailOut` 的注释：答案是给"已经和这道题交过手"的人的。
    """
    r = (
        (await db.execute(_SELECT_WRONG_DETAIL, {"uid": user_id, "qid": question_id}))
        .mappings()
        .first()
    )
    if r is None:
        raise not_found("这道题不在你的错题本里", 40401)

    qid = int(r["question_id"])
    opt_rows = (await db.execute(_SELECT_OPTIONS, {"qids": [qid]})).mappings().all()
    qtype = str(r["type"])
    answer_doc = parse_answer_doc(r["answer"])
    return WrongDetailOut(
        question_id=r["question_id"],
        subject_id=r["subject_id"],
        subject_name=r["subject_name"],
        chapter_name=r["chapter_name"],
        type=qtype,
        stem=str(r["stem"]),
        stem_html=r["stem_html"],
        options=[
            QuestionOptionOut(
                label=str(o["label"]), content=str(o["content"]), content_html=o["content_html"]
            )
            for o in opt_rows
        ],
        # ★ 出参前**归一**（判断题在库里有 `[true]` / `["A"]` 两套写法，坑 76）。
        answer=public_answer(qtype, parse_answer_value(r["answer"])) if answer_doc else {},
        analysis=r["analysis"],
        analysis_html=r["analysis_html"],
        wrong_count=int(r["wrong_count"]),
        retry_correct=int(r["retry_correct"]),
        mastered_level=int(r["mastered_level"]),
        reason_tag=r["reason_tag"],
        last_wrong_at=r["last_wrong_at"],
    )


# ============================================================ 交卷（#7）+ 报告（#2）


#: ★ 交卷：**一次性写三个字段**，且**只在 `doing` 上生效**。
#:   `duration_sec` 用**服务端时间**算（`now() - started_at`）—— 不让前端传：
#:   前端传 = 可伪造 + 要处理时区 + 客户端时钟可能不准（三条里任何一条都够否掉它）。
#:   `GREATEST(0, …)`：时钟回拨 / 造数时 `finished_at < started_at` ⇒ 别写负数进去。
_FINISH_SESSION = text(
    """
    UPDATE practice_sessions
       SET status       = 'finished',
           finished_at  = COALESCE(finished_at, now()),
           duration_sec = GREATEST(
                            0,
                            EXTRACT(EPOCH FROM (now() - started_at))::int
                          ),
           updated_at   = now()
     WHERE id = :sid AND user_id = :uid AND status = 'doing'
    """
).bindparams(bindparam("sid", type_=BigInteger), bindparam("uid", type_=BigInteger))

#: 知识点分布：**按已作答的题聚**。
#: ⚠️ 为什么只聚已作答的：没答的题不构成"表现" —— 把它们算进去，
#:    "知识点分布"就退化成"这次抽了多少题"，与结果页的目的（找弱项）相反。
_SELECT_KP_STATS = text(
    """
    SELECT q.knowledge_point_id                       AS kp_id,
           COALESCE(kp.name, '未归类')                 AS name,
           count(*)                                   AS total,
           count(*) FILTER (WHERE i.is_correct)        AS correct
      FROM practice_items i
      JOIN questions q       ON q.id  = i.question_id
      LEFT JOIN knowledge_points kp ON kp.id = q.knowledge_point_id
     WHERE i.session_id = :sid AND i.answered_at IS NOT NULL
     GROUP BY 1, 2
     ORDER BY 1
    """
).bindparams(bindparam("sid", type_=BigInteger))


def _report_of(head: Any, by_kp: list[KpStatOut]) -> SessionReportOut:
    answered = int(head["answered"])
    correct = int(head["correct"])
    return SessionReportOut(
        id=head["id"],
        mode=str(head["mode"]),
        status=str(head["status"]),
        title=str(head["title"]),
        subject_id=head["subject_id"],
        subject_name=head["subject_name"],
        chapter_id=head["chapter_id"],
        chapter_name=head["chapter_name"],
        total=int(head["total"]),
        answered=answered,
        correct=correct,
        score=float(head["score"]),
        # ★★ 红线：**零分母返 `null`**。
        #    写成 `correct / answered if answered else 0` 是错的 ——
        #    "一道没答"和"全答错了"在界面上会长得一样（都是 0%），而那两件事完全不同。
        accuracy=(round(correct / answered, 4) if answered else None),
        duration_sec=int(head["duration_sec"] or 0),
        started_at=head["started_at"],
        finished_at=head["finished_at"],
        by_kp=by_kp,
    )


async def _kp_stats(db: AsyncSession, session_id: int) -> list[KpStatOut]:
    rows = (await db.execute(_SELECT_KP_STATS, {"sid": session_id})).mappings().all()
    out: list[KpStatOut] = []
    for r in rows:
        total = int(r["total"])
        correct = int(r["correct"])
        out.append(
            KpStatOut(
                knowledge_point_id=r["kp_id"],
                name=str(r["name"]),
                total=total,
                correct=correct,
                accuracy=(round(correct / total, 4) if total else None),
            )
        )
    # ★ 按**正确率升序**（最弱在前）。`accuracy is None` 的排最后
    #   （理论上到不了这里，但排序里不能有"未定义行为"）。
    out.sort(key=lambda k: (k.accuracy is None, k.accuracy if k.accuracy is not None else 0.0))
    return out


async def get_report(db: AsyncSession, *, user_id: int, session_id: int) -> SessionReportOut:
    """取练习报告（P2c-1 结果页）。

    ⚠️ **不要求 `status='finished'`** —— 判据 = 硬约定 **A**（读路径不得夹带比读接口更严的准入）。
       "能不能看统计"与"练没练完"是两件事；前端在 `doing` 时不放入口，
       但接口不设这道门（否则将来"中途看统计"会撞一堵没必要的墙）。

    ⚠️ 归属判断只有一处：所有 SQL 都带 `user_id` —— 查不到 ⇒ `40401`
       （**不是 403**：403 等于告诉对方"这个 id 存在但你没权限"）。
    """
    head = (
        (await db.execute(_SELECT_SESSION, {"sid": session_id, "uid": user_id})).mappings().first()
    )
    if head is None:
        raise not_found("练习不存在", 40401)
    return _report_of(head, await _kp_stats(db, session_id))


async def finish_session(db: AsyncSession, *, user_id: int, session_id: int) -> SessionReportOut:
    """交卷（#7）：把这次练习收尾，并返回报告。

    ★★ **幂等判据 = "目标状态已达成"**（硬约定 C）：已经 `finished` ⇒
       **零写入**、直接返回同一份报告。守卫**不写成**"这次请求带没带某个参数"——
       用户连点两次「交卷」是**同一个**目标状态。

    ⚠️ **允许交白卷**（一道都没答）：`accuracy` 会是 `null`。
       拦下它反而是错的 —— "我不想做了"也是一个正当的结束方式，
       而 `abandoned` 状态留给**系统**判定（超时未继续），不是用户手动选。
    """
    head = (
        (await db.execute(_SELECT_SESSION, {"sid": session_id, "uid": user_id})).mappings().first()
    )
    if head is None:
        raise not_found("练习不存在", 40401)

    if str(head["status"]) == "doing":
        res = await db.execute(_FINISH_SESSION, {"sid": session_id, "uid": user_id})
        if res.rowcount == 0:
            # 并发下被另一个请求先收了尾 ⇒ 回到幂等分支（和 `submit_answer` 同一形状）。
            pass
        # 写路径必须自己 commit（`get_db` 不自动提交）——
        # 症状极易误判：接口 200 + code=0，紧接着按 id 查就是 40401（坑 75）。
        await db.commit()
        head = (
            (await db.execute(_SELECT_SESSION, {"sid": session_id, "uid": user_id}))
            .mappings()
            .first()
        )

    return _report_of(head, await _kp_stats(db, session_id))


# ============================================================ 提交判分（#6）


_SELECT_ITEM_FOR_ANSWER = text(
    """
    SELECT i.id AS item_id, i.answered_at, i.user_answer, i.is_correct, i.score,
           q.id AS question_id, q.subject_id, q.chapter_id, q.type, q.answer,
           q.analysis, q.analysis_html, q.score_default
      FROM practice_items i
      JOIN questions q ON q.id = i.question_id
     WHERE i.id = :iid AND i.session_id = :sid
    """
)

#: ★ `AND answered_at IS NULL` 是**并发下的真守卫**：两个请求同时读到"未作答"时，
#:   这条 UPDATE 只有一个能写进去（另一个 rowcount=0 ⇒ 走幂等分支）。
_UPDATE_ITEM_ANSWER = text(
    """
    UPDATE practice_items
       SET user_answer = CAST(:ua AS jsonb),
           is_correct  = :ok,
           score       = :score,
           answered_at = now(),
           show_analysis = true
     WHERE id = :iid AND answered_at IS NULL
    """
)

_UPSERT_UQS = text(
    """
    INSERT INTO user_question_state
        (id, user_id, question_id, subject_id, status,
         correct_count, wrong_count, streak, last_answer, last_result, last_at)
    VALUES
        (:id, :uid, :qid, :sub, :st, :cc, :wc, :streak, CAST(:la AS jsonb), :lr, now())
    ON CONFLICT (user_id, question_id) DO UPDATE SET
        status        = EXCLUDED.status,
        correct_count = user_question_state.correct_count + :cc,
        wrong_count   = user_question_state.wrong_count + :wc,
        streak        = CASE WHEN EXCLUDED.last_result
                        THEN user_question_state.streak + 1 ELSE 0 END,
        last_answer   = EXCLUDED.last_answer,
        last_result   = EXCLUDED.last_result,
        last_at       = now(),
        updated_at    = now()
    """
)
#: ⚠️ **刻意不动** `mastery` / `ease_factor` / `interval_days` / `next_review_at`：
#:   那是 SM-2 记忆曲线的地盘，`docs/24` §2 已把掌握度算法划出首批。
#:   在这里塞一个"看起来像遗忘曲线"的公式，比不写更糟 —— 它会被当成实现。

_UPSERT_WRONG = text(
    """
    INSERT INTO wrong_questions (id, user_id, question_id, subject_id, chapter_id, wrong_count)
    VALUES (:id, :uid, :qid, :sub, :ch, 1)
    ON CONFLICT (user_id, question_id) DO UPDATE SET
        wrong_count   = wrong_questions.wrong_count + 1,
        last_wrong_at = now(),
        is_removed    = false,
        updated_at    = now()
    """
)

#: ★★ **只在 `mode='wrong'` 的会话里调用**（P2c-3 收紧语义，见 `submit_answer`）。
#:   这个字段回答的问题是"**这道错题我攻克了吗**" ⇒ 只有"重练 **并且** 答对"才该 +1。
_BUMP_RETRY_CORRECT = text(
    "UPDATE wrong_questions SET retry_correct = retry_correct + 1, updated_at = now() "
    "WHERE user_id = :uid AND question_id = :qid"
)

#: ★ 会话计数的**唯一实现**（`total/answered/correct/score`）。
#:   #7 `finish` 也要用它 —— 两处各写一遍必然出现"报告页与进度条不一致"。
_REFRESH_COUNTERS = text(
    """
    UPDATE practice_sessions s
       SET answered = t.n, correct = t.c, score = t.s, updated_at = now()
      FROM (
            SELECT count(*) FILTER (WHERE answered_at IS NOT NULL) AS n,
                   count(*) FILTER (WHERE is_correct)              AS c,
                   COALESCE(sum(score), 0)                         AS s
              FROM practice_items
             WHERE session_id = :sid
           ) t
     WHERE s.id = :sid
    """
)


def _progress(row: Any) -> SessionProgressOut:
    return SessionProgressOut(
        total=int(row["total"]),
        answered=int(row["answered"]),
        correct=int(row["correct"]),
        score=float(row["score"]),
    )


async def submit_answer(
    db: AsyncSession, *, user_id: int, session_id: int, item_id: int, value: list[Any]
) -> AnswerResultOut:
    """提交一道题并判分。

    ★★ **幂等判据 = "目标状态已达成"**（硬约定 C）：这道题**已经答过** ⇒
       **零写入**、返回既有结果、且**不动**任何 `version` / 计数。
       守卫不写成"请求体是不是同一个答案"—— 用户改主意再提交一次**也是**幂等分支，
       因为"这道题已作答"这个目标状态已经达成了。

    ⚠️ 会话 `status != 'doing'` 时**拒绝写入**（而不是默默允许）：
       已结束的练习再改答案，会让"报告页"与"错题本"两处对不上，且没有回退入口。
    """
    sess = (
        (await db.execute(_SELECT_SESSION, {"sid": session_id, "uid": user_id})).mappings().first()
    )
    if sess is None:
        raise not_found("练习不存在", 40401)
    if str(sess["status"]) != "doing":
        raise conflict("这次练习已经结束了，不能再提交答案", 40901)

    item = (
        (await db.execute(_SELECT_ITEM_FOR_ANSWER, {"iid": item_id, "sid": session_id}))
        .mappings()
        .first()
    )
    if item is None:
        raise not_found("这道题不在本次练习里", 40401)

    qtype = str(item["type"])
    answer_doc = parse_answer_doc(item["answer"])
    correct = parse_answer_value(item["answer"])
    partial = bool(answer_doc.get("partial_credit"))

    # ---- 幂等分支：已作答 ⇒ 零写入 ----
    if item["answered_at"] is not None:
        return AnswerResultOut(
            item_id=item["item_id"],
            is_correct=bool(item["is_correct"]),
            score=float(item["score"] or 0),
            correct_answer=public_answer(qtype, correct),
            my_value=parse_answer_value(item["user_answer"]),
            analysis=item["analysis"],
            analysis_html=item["analysis_html"],
            idempotent=True,
            session=_progress(sess),
        )

    user_value = normalize_user_value(qtype, value)
    is_correct, ratio = grade(qtype, correct, user_value, partial)
    score = round(float(item["score_default"]) * ratio, 2)

    res = await db.execute(
        _UPDATE_ITEM_ANSWER,
        {
            "iid": item_id,
            "ua": json.dumps(user_value, ensure_ascii=False),
            "ok": is_correct,
            "score": score,
        },
    )
    if res.rowcount == 0:
        # 并发下被另一个请求先写进去了 ⇒ 回到幂等分支（**不重复记账**）。
        # ⚠️ 用**重读**而不是递归调用自己：递归在"rowcount 一直是 0"的脏状态下会打转，
        #    而这里要的只是"**再确认一次目标状态**"。
        again = (
            (await db.execute(_SELECT_ITEM_FOR_ANSWER, {"iid": item_id, "sid": session_id}))
            .mappings()
            .first()
        )
        if again is not None and again["answered_at"] is not None:
            return AnswerResultOut(
                item_id=again["item_id"],
                is_correct=bool(again["is_correct"]),
                score=float(again["score"] or 0),
                correct_answer=public_answer(
                    str(again["type"]), parse_answer_value(again["answer"])
                ),
                my_value=parse_answer_value(again["user_answer"]),
                analysis=again["analysis"],
                analysis_html=again["analysis_html"],
                idempotent=True,
                session=_progress(sess),
            )
        raise conflict("这道题刚刚被改动了，请刷新后重试", 40901)

    qid = int(item["question_id"])
    await db.execute(
        _UPSERT_UQS,
        {
            "id": next_id(),
            "uid": user_id,
            "qid": qid,
            "sub": int(item["subject_id"]),
            "st": "done" if is_correct else "wrong",
            "cc": 1 if is_correct else 0,
            "wc": 0 if is_correct else 1,
            "streak": 1 if is_correct else 0,
            "la": json.dumps(user_value, ensure_ascii=False),
            "lr": is_correct,
        },
    )
    if is_correct:
        # ★★ **只有"重练答对"才算重练答对**（P2c-3 的语义收紧）。
        #    改造前这里对**任何**答对都 +1（只要该题在错题本里）⇒ `retry_correct`
        #    实际含义是"答对次数"。后果不报错，但错题本上会出现**从没重练过、
        #    却标着"已重练"**的题 —— 字段名与它回答的问题必须一致，
        #    否则前端只能靠"猜"来用这个数（而猜错的方向是**给用户一个假的成就感**）。
        if str(sess["mode"]) == "wrong":
            await db.execute(_BUMP_RETRY_CORRECT, {"uid": user_id, "qid": qid})
    else:
        await db.execute(
            _UPSERT_WRONG,
            {
                "id": next_id(),
                "uid": user_id,
                "qid": qid,
                "sub": int(item["subject_id"]),
                "ch": item["chapter_id"],
            },
        )
    await db.execute(_REFRESH_COUNTERS, {"sid": session_id})
    # 同上：`get_db` 不自动提交。**先提交再回读** —— 回读是为了把"提交后的真值"给前端；
    # 顺序反了的话，前端拿到的进度可能比库里少一步（看起来像"进度没更新"）。
    await db.commit()
    fresh = (
        (await db.execute(_SELECT_SESSION, {"sid": session_id, "uid": user_id})).mappings().first()
    )

    return AnswerResultOut(
        item_id=item["item_id"],
        is_correct=is_correct,
        score=score,
        correct_answer=public_answer(qtype, correct),
        my_value=user_value,
        analysis=item["analysis"],
        analysis_html=item["analysis_html"],
        idempotent=False,
        session=_progress(fresh if fresh is not None else sess),
    )


# ============================================================ 收藏 / 标记（P2c-4）


#: 题目必须**可见**才允许标记 / 收藏 —— 否则会留下指向"用户看不到的题"的脏标记，
#: 而那种脏标记在列表里**表现成一个空题干**（比报错更难查）。
_SELECT_QUESTION_USABLE = text(
    "SELECT q.id, q.subject_id FROM questions q "
    "WHERE q.id = :qid AND q.status = 'published' AND q.is_deleted = false"
).bindparams(bindparam("qid", type_=BigInteger))

#: ★ 幂等：`ON CONFLICT DO NOTHING` ⇒ 重复 PUT **净零变更**（连 `created_at` 都不动）。
#:   判据 = 硬约定 C 的"**目标状态已达成**"。
_INSERT_FAVORITE = text(
    "INSERT INTO favorites (id, user_id, target_type, target_id, subject_id) "
    "VALUES (:id, :uid, 'question', :qid, :sub) "
    "ON CONFLICT (user_id, target_type, target_id) DO NOTHING"
).bindparams(
    bindparam("id", type_=BigInteger),
    bindparam("uid", type_=BigInteger),
    bindparam("qid", type_=BigInteger),
    bindparam("sub", type_=BigInteger),
)

_DELETE_FAVORITE = text(
    "DELETE FROM favorites WHERE user_id = :uid AND target_type = 'question' AND target_id = :qid"
).bindparams(bindparam("uid", type_=BigInteger), bindparam("qid", type_=BigInteger))

_INSERT_MARK = text(
    "INSERT INTO question_marks (id, user_id, question_id, subject_id) "
    "VALUES (:id, :uid, :qid, :sub) "
    "ON CONFLICT (user_id, question_id) DO NOTHING"
).bindparams(
    bindparam("id", type_=BigInteger),
    bindparam("uid", type_=BigInteger),
    bindparam("qid", type_=BigInteger),
    bindparam("sub", type_=BigInteger),
)

_DELETE_MARK = text(
    "DELETE FROM question_marks WHERE user_id = :uid AND question_id = :qid"
).bindparams(bindparam("uid", type_=BigInteger), bindparam("qid", type_=BigInteger))

_SELECT_FLAGS = text(
    f"SELECT {_marked_exists('q.id')} AS marked, {_favorited_exists('q.id')} AS favorited "
    "FROM questions q WHERE q.id = :qid"
).bindparams(bindparam("qid", type_=BigInteger), bindparam("uid", type_=BigInteger))


async def _assert_question_usable(db: AsyncSession, *, question_id: int) -> int:
    """返 `subject_id`。题目不存在 / 未发布 / 已软删 ⇒ **`40401`**（不是 403）。"""
    row = (await db.execute(_SELECT_QUESTION_USABLE, {"qid": question_id})).mappings().first()
    if row is None:
        raise not_found("这道题不存在或已下架", 40401)
    return int(row["subject_id"])


async def get_flags(db: AsyncSession, *, user_id: int, question_id: int) -> dict[str, bool]:
    """读一道题的两个状态。**题目不存在 ⇒ 两个都 false**（读路径不报错，硬约定 A）。"""
    row = (await db.execute(_SELECT_FLAGS, {"qid": question_id, "uid": user_id})).mappings().first()
    if row is None:
        return {"marked": False, "favorited": False}
    return {"marked": bool(row["marked"]), "favorited": bool(row["favorited"])}


async def set_flag(
    db: AsyncSession, *, user_id: int, question_id: int, kind: str, on: bool
) -> dict[str, bool]:
    """置 / 清「收藏」或「标记」。**幂等**（判据 = 目标状态已达成），返回**两个**状态。

    ★ 为什么返回两个：前端两个按钮共用一次响应，省一次往返；
      而且"我点了收藏，标记还在吗"这个问题**只有后端知道**（它们是两行数据）。
    """
    subject_id = await _assert_question_usable(db, question_id=question_id)
    new_id = next_id()
    if kind == "favorite":
        stmt = _INSERT_FAVORITE if on else _DELETE_FAVORITE
        params = (
            {"id": new_id, "uid": user_id, "qid": question_id, "sub": subject_id}
            if on
            else {"uid": user_id, "qid": question_id}
        )
    elif kind == "mark":
        stmt = _INSERT_MARK if on else _DELETE_MARK
        params = (
            {"id": new_id, "uid": user_id, "qid": question_id, "sub": subject_id}
            if on
            else {"uid": user_id, "qid": question_id}
        )
    else:  # pragma: no cover —— 路由层的 Literal 已经卡住，这里是 service 被直接调用时的兜底
        raise bad_request(f"未知的标记类型：{kind}", 40001)
    await db.execute(stmt, params)
    # 同 `create_session`：`get_db` **不自动 commit** ⇒ 漏了这一句会"接口成功但查不到"。
    await db.commit()
    return await get_flags(db, user_id=user_id, question_id=question_id)


#: 两个列表的**题源**（其余完全一样 ⇒ 只有 FROM 不同，别的都共用）。
_COLLECTION_SRC = {
    "favorite": (
        "(SELECT target_id AS qid, created_at FROM favorites"
        " WHERE user_id = :uid AND target_type = 'question')"
    ),
    "mark": "(SELECT question_id AS qid, created_at FROM question_marks WHERE user_id = :uid)",
}

_COLLECTION_COLS = (
    "SELECT q.id AS question_id, q.subject_id, q.chapter_id, q.type, q.stem, q.stem_html,"
    "       s.name AS subject_name, ch.name AS chapter_name,"
    "       src.created_at AS collected_at,"
    f"       {_marked_exists('q.id')} AS marked,"
    f"       {_favorited_exists('q.id')} AS favorited"
)

_COLLECTION_TAIL = (
    "  JOIN questions q  ON q.id = src.qid"
    "  LEFT JOIN subjects s  ON s.id = q.subject_id"
    "  LEFT JOIN chapters ch ON ch.id = q.chapter_id"
    " WHERE q.status = 'published' AND q.is_deleted = false"
    "   AND (:sid IS NULL OR q.subject_id = :sid)"
)


def _collection_sql(kind: str, tail: str) -> str:
    return f"{_COLLECTION_COLS} FROM {_COLLECTION_SRC[kind]} src {_COLLECTION_TAIL}{tail}"


_COLLECT_PAGE = {
    k: text(
        _collection_sql(k, " ORDER BY src.created_at DESC, q.id DESC LIMIT :lim OFFSET :off")
    ).bindparams(
        bindparam("uid", type_=BigInteger),
        bindparam("sid", type_=BigInteger),
        bindparam("lim", type_=Integer),
        bindparam("off", type_=Integer),
    )
    for k in _COLLECTION_SRC
}

_COLLECT_COUNT = {
    k: text(f"SELECT count(*) AS n FROM {_COLLECTION_SRC[k]} src {_COLLECTION_TAIL}").bindparams(
        bindparam("uid", type_=BigInteger), bindparam("sid", type_=BigInteger)
    )
    for k in _COLLECTION_SRC
}

#: 分面：**恒为全量**（不随 `subject_id` 收缩）—— 与错题本**同一条判据**
#: （`list_wrong` 里那段：分面的作用正是"让你看见还能切到哪"，收缩之后就切不过去了）。
_COLLECT_FACETS = {
    k: text(
        "SELECT q.subject_id, COALESCE(s.name, '未归类') AS name, count(*) AS n"
        f" FROM {_COLLECTION_SRC[k]} src"
        "  JOIN questions q ON q.id = src.qid"
        "  LEFT JOIN subjects s ON s.id = q.subject_id"
        " WHERE q.status = 'published' AND q.is_deleted = false"
        " GROUP BY 1, 2 ORDER BY 3 DESC, 1"
    ).bindparams(bindparam("uid", type_=BigInteger))
    for k in _COLLECTION_SRC
}


async def list_collections(
    db: AsyncSession,
    *,
    user_id: int,
    kind: str,
    subject_id: int | None = None,
    page: int = 1,
    page_size: int = WRONG_PAGE_DEFAULT,
) -> CollectionListOut:
    """收藏 / 标记列表（两个列表**同形状**，只有题源不同）。

    ★ `kind`：`favorite` = 我收藏的；`mark` = 我标记的。
      ★★ **为什么"标记"也要有列表**：标记可以打在**从没错过**的题上，那种题**不在错题本里**
         ⇒ 只做"错题本筛已标记"的话，用户会问"**我标的题去哪看**"。
    ★ 分面**恒为全量**（与错题本同一条判据 —— 见 `list_wrong` 的注释里那段教训）。
    ★ 只列**可见**的题（`published` 且未软删）：题目下架后**标记保留**、但列表里不出现；
      过滤放在查询侧 ⇒ 题目恢复后它自然回来（不是"删掉标记"）。
    """
    if kind not in _COLLECTION_SRC:
        raise bad_request(f"未知的列表类型：{kind}", 40001)
    page = max(1, int(page))
    page_size = max(1, min(WRONG_PAGE_MAX, int(page_size)))
    off = (page - 1) * page_size
    params = {"uid": user_id, "sid": subject_id, "lim": page_size, "off": off}
    total = int((await db.execute(_COLLECT_COUNT[kind], params)).scalar() or 0)
    rows = (await db.execute(_COLLECT_PAGE[kind], params)).mappings().all()
    facets = (await db.execute(_COLLECT_FACETS[kind], params)).mappings().all()
    return CollectionListOut(
        kind=kind,
        total=total,
        page=page,
        page_size=page_size,
        subjects=[
            WrongSubjectOut(subject_id=f["subject_id"], name=str(f["name"]), count=int(f["n"]))
            for f in facets
        ],
        items=[
            CollectionItemOut(
                question_id=r["question_id"],
                subject_id=r["subject_id"],
                subject_name=r["subject_name"],
                chapter_name=r["chapter_name"],
                type=str(r["type"]),
                stem=str(r["stem"]),
                stem_html=r["stem_html"],
                collected_at=r["collected_at"],
                marked=bool(r["marked"]),
                favorited=bool(r["favorited"]),
            )
            for r in rows
        ],
    )


__all__ = [
    "GRADABLE_TYPES",
    "get_flags",
    "list_collections",
    "set_flag",
    "PARTIAL_CREDIT_RATIO",
    "create_session",
    "finish_session",
    "get_report",
    "get_wrong_detail",
    "list_wrong",
    "get_session",
    "grade",
    "list_chapters",
    "normalize_user_value",
    "public_answer",
    "submit_answer",
]
