"""`questions.answer` 的**规范形式** —— 契约 + 数据一致性（用户 2026-09-29 要求）。

## 这一组用例要挡的是什么

2026-09-29 之前，**判断题在库里有两种写法**（管理端 `[true]` / 种子与导入 `["A"]`），
读取方只能靠类型强制去猜 —— `bool("A") == True` ⇒ **答错的判断题被判成对**（不报错，还给满分）。
规范形式的定义在 `app/schemas/answer.py`。

## 四条判据（互相不可替代）

| # | 判据 | 少了它会怎样 |
|---|---|---|
| 1 | **构造入口**只产出规范形式（纯函数） | 写路径各拼一份 dict ⇒ 又长回两种写法 |
| 2 | **读路径宽容**（旧写法 → 规范 token，只在内存里） | 遇到旧数据直接判错/抛异常 |
| 3 | ★ 检查器**真的会报**旧写法（构造一个"该红"的场景） | 一个恒绿的检查器 = 没有检查器（硬约定 J） |
| 4 | ★ **扫全库**：每一道题的 `answer` 都符合规范 | 前三条全绿，库里仍然躺着第三种写法 |

★ 判据 4 是**唯一能覆盖"种子生成器"这条写路径**的地方 —— 生成器是独立进程，
CI / 本地都是"先灌种子、再跑 pytest"，所以扫库就等于扫了它写出来的东西。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.schemas.answer import (
    canonical_doc,
    check_doc,
    judge_bool,
    normalize_tokens,
)
from app.services.import_service import _build_answer

# ⚠️ `admin_h` 是 conftest 里的 **fixture**，用参数名注入即可 —— **不要 import 它**
#   （import 会把它变成一个普通函数，把同名 fixture 遮掉：ruff 报 F811/F401，
#   而 pytest 的报错会变成"fixture 找不到"，两边都指向一个不存在的问题）。
from .conftest import API, body, sql_fetch

#: 扫库用例的**下限**：扫到的行数少于它 ⇒ 认为"查询坏了"，而不是"库很干净"。
#: （"零发现"被读成"通过"是假绿最经典的形状，硬约定 H。）
MIN_QUESTIONS = 1000


# ============================================================ 1/2/3 纯函数


def test_canonical_doc_contract() -> None:
    """构造入口：按题型产出规范形式；**判断题一律布尔**（这就是本次的核心决定）。"""
    assert canonical_doc("judge", [True]) == {"value": [True]}
    assert canonical_doc("judge", ["A"]) == {"value": [True]}, "旧写法进、规范形式出"
    assert canonical_doc("judge", ["B"]) == {"value": [False]}
    assert canonical_doc("judge", True) == {"value": [True]}, "标量也收"
    assert canonical_doc("single", ["b"]) == {"value": ["B"]}
    # 多选：**排序 + 去重**；`partial_credit` 只在**显式为真**时出现
    # （规范形式里"没这个键"就是"不允许部分分" —— 别用 `null` 表达两件事）
    assert canonical_doc("multiple", ["C", "A", "A"]) == {"value": ["A", "C"]}
    assert canonical_doc("multiple", ["C", "A"], partial_credit=True) == {
        "value": ["A", "C"],
        "partial_credit": True,
    }
    assert "partial_credit" not in canonical_doc("multiple", ["A", "C"])
    assert canonical_doc("case", "参考答案") == {"value": "参考答案"}
    assert canonical_doc("fill", ["答案一", "答案二"]) == {"value": ["答案一", "答案二"]}


def test_canonical_doc_refuses_what_it_cannot_express() -> None:
    """**认不出来就报错**，不静默挑一个形状。

    ⚠️ 判断题传 `None`：旧实现是 `bool(None)` ⇒ `{"value": [False]}` ——
      "调用方忘了传"变成一条**看起来完全正常的答案「错误」**。
      静默默认值是这个项目反复踩的坑（硬约定 H 的同族）。
    """
    with pytest.raises(ValueError):
        canonical_doc("judge", [None])
    with pytest.raises(ValueError):
        canonical_doc("judge", [])
    with pytest.raises(ValueError):
        canonical_doc("judge", ["嗯"])
    with pytest.raises(ValueError):
        canonical_doc("newtype", ["x"])  # 新题型不许悄悄塞进旧形状


def test_judge_bool_is_wide_in() -> None:
    """读宽容：旧的标号写法、中文写法、布尔都认；**认不出来返回 None**（不猜）。"""
    for t in (True, "A", "a", "T", "对", "正确", "√", "1"):
        assert judge_bool(t) is True, t
    for f in (False, "B", "b", "F", "错", "错误", "×", "0"):
        assert judge_bool(f) is False, f
    assert judge_bool("嗯") is None
    assert judge_bool(None) is None
    assert judge_bool(3) is None


def test_normalize_tokens_reads_all_three_shapes() -> None:
    """读路径要吃得下历史形态：dict / JSON 文本 / 裸 list。"""
    assert normalize_tokens("judge", {"value": ["A"]}) == [True]
    assert normalize_tokens("judge", json.dumps({"value": ["B"]})) == [False]
    assert normalize_tokens("judge", [True]) == [True]
    assert normalize_tokens("single", {"value": ["b"]}) == ["B"]
    assert normalize_tokens("judge", {"value": ["嗯"]}) == [], "认不出来 ⇒ 空（= 没有答案）"


def test_check_doc_really_flags_the_legacy_shape() -> None:
    """★★ **判据自己能证伪**：构造"它该报相反结果"的场景。

    没有这一条，`check_doc` 完全可以是个恒返回 `None` 的函数，
    而用例 4（扫库）照样全绿 —— 那正是"检查器假绿"。
    """
    assert check_doc("judge", {"value": [True]}) is None
    assert check_doc("judge", {"value": ["A"]}) is not None, "旧写法必须被报出来"
    assert check_doc("judge", {"value": "A"}) is not None, "标量不是数组"
    assert check_doc("judge", {"value": [True, False]}) is not None, "判断题只能有一个 token"
    assert check_doc("single", {"value": ["A", "B"]}) is not None
    assert check_doc("multiple", {"value": ["A"]}) is not None, "多选至少两个标号"
    assert check_doc("multiple", {"value": ["A", "A"]}) is not None, "标号不许重复"
    assert check_doc("single", {"A": "B"}) is not None, "缺 value"
    assert check_doc("case", {"value": ["文本"]}) is not None, "案例题是字符串不是数组"


# ============================================================ 4 扫库（数据一致性）


def _scan() -> list[dict]:
    rows = sql_fetch("SELECT id, type, answer FROM questions ORDER BY id")
    assert rows is not None, "这些用例需要数据库（DATABASE_URL）"
    return rows


def test_db_all_answers_are_canonical() -> None:
    """★ 扫**全库**：每一道题的 `answer` 都是规范形式。

    ★★ 报出**逐题型**的计数，并且**有下限** —— "扫到 0 行、全部通过"是这个用例
    最可能的假绿形态（查询写错、表名打错、库是空的）。
    """
    rows = _scan()
    assert len(rows) >= MIN_QUESTIONS, (
        f"只扫到 {len(rows)} 道题（下限 {MIN_QUESTIONS}）—— 先确认库不是空的、查询没写错，"
        "再把这条下限调下来"
    )
    by_type: dict[str, int] = {}
    bad: list[str] = []
    for r in rows:
        qtype = str(r["type"])
        by_type[qtype] = by_type.get(qtype, 0) + 1
        problem = check_doc(qtype, r["answer"])
        if problem:
            bad.append(f"题目 {r['id']}（{qtype}）：{problem}")
        if len(bad) >= 5:  # 只留前 5 条，避免刷屏
            break
    assert not bad, "有题目不符合答案规范形式：\n" + "\n".join(bad)
    # 失败时这行会一起打出来 —— 它同时证明了"扫的确实是真实的分布"
    assert by_type.get("judge", 0) > 0, f"库里没有判断题，用例失去了意义：{by_type}"


def test_db_has_no_legacy_judge_answer() -> None:
    """★ 迁移（`db/migrations/20260929-01`）真的生效了：**旧写法一道都不剩**。

    与上一条的区别：上一条验"全库都合规"，这一条**单独钉住那个具体的历史形状**
    （`answer->'value'->>0` 是 `A`/`B`）。两个都留着，因为它们的失败信息不一样：
    上一条说"哪里不合规"，这一条说"迁移没跑"。
    """
    rows = sql_fetch(
        "SELECT count(*) AS n FROM questions "
        "WHERE type = 'judge' AND upper(answer -> 'value' ->> 0) IN ('A', 'B')"
    )
    assert rows is not None
    assert int(rows[0]["n"]) == 0, (
        f"库里还有 {rows[0]['n']} 道判断题在用旧的 ['A']/['B'] 写法 "
        "⇒ 迁移没生效或没跑（db/migrations/20260929-01-judge-answer-canonical.sql）"
    )


# ============================================================ 写路径（两条入口）


def test_admin_writer_stores_canonical_form(client: httpx.Client, admin_h: dict) -> None:
    """**管理端这条写路径**落库的必须是布尔（它是本次事故的"两种写法"之一）。"""
    payload = {
        "subject_id": "1001",
        "type": "judge",
        "stem": f"规范形式用例：判断题的答案落库必须是布尔（{__name__}）",
        "judge_answer": True,
        "analysis": "答案由 judge_answer 推导，不接受单独传入。",
        "source_type": "self",
    }
    b = body(client.post(f"{API}/admin/questions", headers=admin_h, json=payload, timeout=30))
    assert b["code"] == 0, b
    qid = int(b["data"]["id"])

    rows = sql_fetch("SELECT type, answer FROM questions WHERE id = $1", qid)
    assert rows, "刚建的题读不到（先查 commit，再查 SQL）"
    assert str(rows[0]["type"]) == "judge"
    doc = json.loads(rows[0]["answer"]) if isinstance(rows[0]["answer"], str) else rows[0]["answer"]
    assert doc == {"value": [True]}, f"管理端落库的不是规范形式：{doc!r}"


def test_import_writer_stores_canonical_form() -> None:
    """**导入管道这条写路径**：题干列仍然填 `A`/`B`（人写的），**落库必须是布尔**。

    （种子生成器那条写路径由上面两条扫库用例覆盖 —— 它产出的 SQL 就是库里那些行。）
    """
    assert _build_answer("judge", "A", []) == {"value": [True]}
    assert _build_answer("judge", "b", []) == {"value": [False]}
    assert _build_answer("single", "B", []) == {"value": ["B"]}
    assert _build_answer("multiple", "C|A", []) == {"value": ["A", "C"], "partial_credit": True}
    assert _build_answer("case", "参考答案", []) == {"value": "参考答案"}
    assert _build_answer("fill", "答案一", []) == {"value": ["答案一"]}
