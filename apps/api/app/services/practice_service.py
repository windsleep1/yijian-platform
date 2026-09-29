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

from sqlalchemy import BigInteger, Integer, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, not_found
from app.core.idgen import next_id
from app.schemas.c_end import (
    AnswerResultOut,
    ChapterOut,
    QuestionOptionOut,
    SessionItemOut,
    SessionOut,
    SessionProgressOut,
    parse_answer_doc,
    parse_answer_value,
)

#: P2b-1 只判**客观题**。见模块抬头：抽题时就排除，不是判分时才拒。
GRADABLE_TYPES: tuple[str, ...] = ("single", "multiple", "judge")

#: ⚠️ 直接拼进 SQL 的**模块常量**（不是用户输入）—— `GRADABLE_TYPES` 是唯一来源，
#:    加一个新题型只需要改上面那一行。用 `= ANY(:arr)` 要额外声明数组类型，
#:    而那条路在 `asyncpg` 上正是坑 73 那一族（裸参数推不出类型）。
_TYPES_SQL = ", ".join(f"'{t}'" for t in GRADABLE_TYPES)

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
_JUDGE_TRUE_TOKENS = frozenset({"A", "T", "TRUE", "Y", "YES", "对", "正确", "√"})
_JUDGE_FALSE_TOKENS = frozenset({"B", "F", "FALSE", "N", "NO", "错", "错误", "×"})


def _judge_bool(x: Any) -> bool | None:
    """把一个"判断题的答案token"解成布尔；**认不出来返回 `None`**（不猜）。

    ⚠️ 返回 `None` 而不是 `False`：`False` 会把"这题的数据我不认识"伪装成"答案是错"。
    """
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        s = x.strip().upper()
        if s in _JUDGE_TRUE_TOKENS:
            return True
        if s in _JUDGE_FALSE_TOKENS:
            return False
    return None


def public_answer(qtype: str, correct: list[Any]) -> dict[str, Any]:
    """**出参**用的答案：判断题一律归一到 `[true]` / `[false]`。

    前端因此**不需要知道库里有两套写法** —— 它只看到布尔，把 `true` 显示成"正确"。
    （认不出来时**原样透出**，不做假：宁可显示一个怪字符串，也不要显示一个假的"错误"。）
    """
    if qtype != "judge":
        return {"value": correct}
    as_bool = [_judge_bool(x) for x in correct]
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
        c = {b for b in (_judge_bool(x) for x in correct) if b is not None}
        u = {b for b in (_judge_bool(x) for x in user) if b is not None}
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
        b = _judge_bool(raw[0])
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

_INSERT_SESSION = text(
    """
    INSERT INTO practice_sessions
        (id, user_id, mode, subject_id, chapter_id, title, config, total, status)
    VALUES
        (:id, :uid, 'chapter', :sid, :cid, :title, CAST(:cfg AS jsonb), :total, 'doing')
    """
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
    subject_id: int,
    chapter_id: int | None = None,
    count: int = 10,
) -> int:
    """建一次章节练习，返回 session id。

    ★ **抽题顺序是确定性的**（未做过的优先，其次按 id）—— P2b-1 要的是
      "后端到前端**往返跑通**"，确定性让这件事**可复现、可断言**。
      "随机抽题 / 按掌握度抽题"属 P2b-2（`config` 列已经留好了位置）。
    ⚠️ 抽不到题时**不建空 session**：空 session 会在前端表现为"进去了但没题"，
      而这与"后端炸了"在前端看起来一模一样。宁可当场报 404（带原因）。
    """
    subject = await _assert_subject_usable(db, subject_id)
    cprefix: str | None = None
    chapter_name: str | None = None
    if chapter_id is not None:
        cprefix = await _chapter_prefix(db, chapter_id=chapter_id, subject_id=subject_id)
        cprefix += "%"
        ch = (
            await db.execute(text("SELECT name FROM chapters WHERE id = :cid"), {"cid": chapter_id})
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

    sid = next_id()
    title = f"{chapter_name} 练习" if chapter_name else f"{subject['name']} 练习"
    await db.execute(
        _INSERT_SESSION,
        {
            "id": sid,
            "uid": user_id,
            "sid": subject_id,
            "cid": chapter_id,
            "title": title,
            "cfg": json.dumps({"chapter_id": chapter_id, "count": count}, ensure_ascii=False),
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
           sub.name AS subject_name, ch.name AS chapter_name
      FROM practice_sessions s
      LEFT JOIN subjects sub ON sub.id = s.subject_id
      LEFT JOIN chapters ch  ON ch.id  = s.chapter_id
     WHERE s.id = :sid AND s.user_id = :uid
    """
).bindparams(bindparam("sid", type_=BigInteger), bindparam("uid", type_=BigInteger))

_SELECT_ITEMS = text(
    """
    SELECT i.id AS item_id, i.seq, i.question_id, i.user_answer, i.is_correct, i.score,
           i.answered_at, q.type, q.stem, q.stem_html, q.answer, q.analysis, q.analysis_html
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


__all__ = [
    "GRADABLE_TYPES",
    "PARTIAL_CREDIT_RATIO",
    "create_session",
    "get_session",
    "grade",
    "list_chapters",
    "normalize_user_value",
    "public_answer",
    "submit_answer",
]
