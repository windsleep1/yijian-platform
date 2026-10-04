"""C 端 · 收藏 / 标记（P2c-4）。

## 契约（每条固定一件事）

    ① 两个写端点都要登录 ⇒ 未登录 **401**。
    ② ★ **幂等**：重复 `PUT` / 连着两次 `DELETE` ⇒ 状态不变、不报错；
       重复 `PUT` 是**净零变更**（连 `created_at` 都不动 —— 用 `ON CONFLICT DO NOTHING` 换来的）。
    ③ ★ 题目必须**可见**（`published` 且未软删），否则 `40401`（**不是 403**）——
       否则会留下指向"看不到的题"的脏标记，而它在列表里表现为**空题干**。
    ④ ★ **归属**：A 的收藏 / 标记**不出现在 B 的列表里**（所有查询都带 `user_id`）。
    ⑤ ★★ **跨会话一致**（本批最要紧的一条）：在练习 A 标记 ⇒ 打开练习 B 的**同一道题**，
       它**也**带 `marked`。这一条正是"**用 `question_marks` 而不是 `practice_items.marked`**"
       的**可证伪锚** —— 会话级实现（旧列）在 ①②③④ 上全绿，只有这条会红。
    ⑥ 取消收藏 ⇒ **列表里消失**（不是"标记一下还留着"）。
    ⑦ ★ 分面**恒为全量**（不随 `subject_id` 收缩）—— 与错题本同一条判据（那条是拿事故换的）。
    ⑧ 空态：`total=0` 且 `items=[]`，但**分面仍在**（否则筛空之后切不回去）。
    ⑨ ★★ `kind=mark` 能列出**从没错过**的题 —— 这正是"标记也要有列表"的理由
       （只做"错题本筛已标记"的话，这种题**无处可看**）。
    ⑩ session 出参**每个 item** 带 `marked` / `favorited`（答题页两个按钮要显示当前态）。
    ⑪ 错题本 `marked_only=true`：只留标记过的；**分面仍为全量**。
    ⑫ 题目下架后：**标记保留**（不是删掉），只是列表里不出现 ⇒ 恢复后它回来。
"""

from __future__ import annotations

import json

import httpx

from .conftest import API, auth, body, fresh_user, sql_exec, sql_fetch

SUBJECT_ECONOMY = 1001
CHAPTER_ECONOMY_1 = 1101


def _session(client: httpx.Client, token: str, *, count: int = 3) -> dict:
    b = body(
        client.post(
            f"{API}/practice/sessions",
            json={
                "subject_id": str(SUBJECT_ECONOMY),
                "chapter_id": str(CHAPTER_ECONOMY_1),
                "count": count,
            },
            headers=auth(token),
        )
    )
    assert b["code"] == 0, f"建 session 失败：{b}"
    s = body(client.get(f"{API}/practice/sessions/{b['data']['id']}", headers=auth(token)))
    assert s["code"] == 0, s
    return s["data"]


def _answer_wrong(client: httpx.Client, token: str, sess: dict, nth: int) -> dict:
    """把第 `nth` 道题**故意答错**（错题本要的就是错）。"""
    it = sess["items"][nth]
    rows = sql_fetch("SELECT answer FROM questions WHERE id = $1", int(it["question_id"]))
    assert rows, f"题目 {it['question_id']} 不见了"
    correct = json.loads(rows[0]["answer"]).get("value") or []
    if it["type"] == "judge":
        value = [not bool(correct[0])]
    else:
        labels = [o["label"] for o in it["options"]] or ["A", "B", "C", "D"]
        cset = {str(c).upper() for c in correct}
        value = [next(lb for lb in labels if str(lb).upper() not in cset)]
    b = body(
        client.post(
            f"{API}/practice/sessions/{sess['id']}/answer",
            json={"item_id": it["item_id"], "value": value},
            headers=auth(token),
        )
    )
    assert b["code"] == 0, b
    return b["data"]


def _uid(u: dict) -> int:
    """查这个刚注册用户的库内 id。

    ★ 为什么不直接用注册响应里的字段：`fresh_user` 返的是 `data`，用户 id 嵌在 `user` 里；
      按**手机号**查一次更稳（响应形状改了这条也不会红），而 `u["phone"]` 是 conftest 保证有的。
    ★ 为什么非要它：pytest 共用同一个库 ⇒ 只按 `question_id` 查会**撞到别的用测试户的行**。
    """
    rows = sql_fetch("SELECT id FROM users WHERE phone = $1", u["phone"])
    assert rows, f"找不到刚注册的用户：{u['phone']}"
    return int(rows[0]["id"])


def _put(client: httpx.Client, token: str, kind: str, qid: str) -> dict:
    return body(client.put(f"{API}/practice/{kind}/{qid}", headers=auth(token)))


def _delete(client: httpx.Client, token: str, kind: str, qid: str) -> dict:
    return body(client.delete(f"{API}/practice/{kind}/{qid}", headers=auth(token)))


def _list(client: httpx.Client, token: str, kind: str = "favorite", **q: object) -> dict:
    return body(
        client.get(f"{API}/practice/favorites", params={"kind": kind, **q}, headers=auth(token))
    )


# ============================================================ ① 登录


def test_flags_require_login(client: httpx.Client) -> None:
    """★ 契约①：四个写端点 + 一个读端点，全都不许匿名。"""
    calls = [
        ("put", "/practice/favorites/1"),
        ("delete", "/practice/favorites/1"),
        ("put", "/practice/marks/1"),
        ("delete", "/practice/marks/1"),
        ("get", "/practice/favorites"),
    ]
    for method, path in calls:
        r = getattr(client, method)(f"{API}{path}")
        assert r.status_code == 401, f"{method.upper()} {path} 应 401，实际 {r.status_code}"
        assert body(r)["code"] == 40100, body(r)


# ============================================================ ② 幂等 + ⑩ session 出参


def test_put_is_idempotent_and_session_items_carry_flags(client: httpx.Client) -> None:
    """★ 契约②⑩：重复 PUT 净零变更（`created_at` 不动）；session 出参每题带两个状态。"""
    u = fresh_user(client, nickname="幂等标记")
    s = _session(client, u["access_token"])
    qid = s["items"][0]["question_id"]

    # ★★ 可证伪锚：**先断言未标记 / 未收藏**
    assert s["items"][0]["marked"] is False, f"还没操作就 marked：{s['items'][0]}"
    assert s["items"][0]["favorited"] is False, s["items"][0]

    first = _put(client, u["access_token"], "marks", qid)
    assert first["code"] == 0 and first["data"]["marked"] is True, first
    assert first["data"]["favorited"] is False, f"标记不该顺手收藏：{first}"
    created = sql_fetch(
        "SELECT created_at FROM question_marks WHERE user_id = $1 AND question_id = $2",
        _uid(u),
        int(qid),
    )
    assert len(created) == 1, created

    again = _put(client, u["access_token"], "marks", qid)
    assert again["code"] == 0 and again["data"]["marked"] is True, again
    rows = sql_fetch(
        "SELECT count(*) AS n, min(created_at) AS first_seen FROM question_marks "
        "WHERE user_id = $1 AND question_id = $2",
        _uid(u),
        int(qid),
    )
    assert int(rows[0]["n"]) == 1, f"重复 PUT 不该多出一行：{rows}"
    assert rows[0]["first_seen"] == created[0]["created_at"], (
        f"重复 PUT 改了 created_at ⇒ 不是净零变更：{rows[0]['first_seen']} != {created[0]['created_at']}"
    )

    # 收藏走的是另一行数据 ⇒ 两个状态可以同时为真
    fav = _put(client, u["access_token"], "favorites", qid)
    assert fav["data"] == {
        "question_id": qid,
        "marked": True,
        "favorited": True,
    }, f"收藏后应两个都为 true：{fav}"

    # ⑩ 重新取 session：每道题都要带上状态
    s2 = body(client.get(f"{API}/practice/sessions/{s['id']}", headers=auth(u["access_token"])))
    assert s2["code"] == 0, s2
    it0 = s2["data"]["items"][0]
    assert it0["marked"] is True and it0["favorited"] is True, f"session 出参没带状态：{it0}"
    for other in s2["data"]["items"][1:]:
        assert other["marked"] is False and other["favorited"] is False, other


def test_delete_is_idempotent(client: httpx.Client) -> None:
    """★ 契约②⑥：连着两次 `DELETE` 不报错；取消后**列表里消失**。"""
    u = fresh_user(client, nickname="幂等取消")
    s = _session(client, u["access_token"])
    qid = s["items"][0]["question_id"]

    _put(client, u["access_token"], "favorites", qid)
    assert _list(client, u["access_token"])["data"]["total"] == 1
    first = _delete(client, u["access_token"], "favorites", qid)
    assert first["code"] == 0 and first["data"]["favorited"] is False, first
    assert _list(client, u["access_token"])["data"]["total"] == 0, "取消后列表该空"
    again = _delete(client, u["access_token"], "favorites", qid)
    assert again["code"] == 0 and again["data"]["favorited"] is False, (
        f"第二次 DELETE 应成功（目标状态已达成）：{again}"
    )


# ============================================================ ③ 准入


def test_flag_on_invisible_question_is_40401(client: httpx.Client) -> None:
    """★ 契约③⑫：题不存在 / 已软删 ⇒ `40401`（**不是 403**，也不是 50001）。"""
    u = fresh_user(client, nickname="脏标记的门")
    s = _session(client, u["access_token"])
    qid = s["items"][0]["question_id"]
    assert _put(client, u["access_token"], "marks", qid)["code"] == 0

    # 题目软删 ⇒ 再操作被拒
    sql_exec("UPDATE questions SET is_deleted = true WHERE id = $1", int(qid))
    bad = _put(client, u["access_token"], "marks", qid)
    assert bad["code"] == 40401, f"软删的题不该还能标记：{bad}"
    # ⑫ 标记**保留**（不是删掉），只是列表里不出现
    left = sql_fetch(
        "SELECT count(*) AS n FROM question_marks WHERE user_id = $1 AND question_id = $2",
        _uid(u),
        int(qid),
    )
    assert int(left[0]["n"]) == 1, f"软删不该清掉标记（恢复后它该回来）：{left}"
    assert _list(client, u["access_token"], "mark")["data"]["total"] == 0, (
        "软删的题不该出现在列表里（过滤在查询侧）"
    )
    sql_exec("UPDATE questions SET is_deleted = false WHERE id = $1", int(qid))
    assert _list(client, u["access_token"], "mark")["data"]["total"] == 1, "恢复后该回来"

    # 不存在的题号
    for kind in ("marks", "favorites"):
        nope = _put(client, u["access_token"], kind, "999999999")
        assert nope["code"] == 40401, f"不存在的题应 40401：{nope}"


# ============================================================ ④ 归属


def test_foreign_flags_are_invisible(client: httpx.Client) -> None:
    """★ 契约④：A 的收藏 / 标记**不出现在 B 的列表里**。"""
    a = fresh_user(client, nickname="收藏主人")
    b = fresh_user(client, nickname="旁观者")
    s = _session(client, a["access_token"])
    qid = s["items"][0]["question_id"]
    _put(client, a["access_token"], "favorites", qid)
    _put(client, a["access_token"], "marks", qid)

    assert _list(client, a["access_token"])["data"]["total"] == 1
    for kind in ("favorite", "mark"):
        other = _list(client, b["access_token"], kind)["data"]
        assert other["total"] == 0 and other["items"] == [], (
            f"别人的 {kind} 不该出现在我的列表里：{other}"
        )
    # B 也"取消"不掉 A 的（那是一条 DELETE 影响 0 行 —— 幂等成功，但什么都没改）
    assert _delete(client, b["access_token"], "favorites", qid)["code"] == 0
    assert _list(client, a["access_token"])["data"]["total"] == 1, "B 的操作不该影响 A"


# ============================================================ ⑤ 跨会话一致（可证伪锚）


def test_mark_is_cross_session_on_the_same_question(client: httpx.Client) -> None:
    """★★ 契约⑤ —— 本批最要紧的一条（也是"为什么用 `question_marks`"的可证伪锚）。

    会话级实现（`practice_items.marked`）在 ①②③④ 上**全绿**，只有这一条会红：
    在练习 A 标记之后，练习 B 的**同一道题**必须**也**带 `marked`。
    """
    u = fresh_user(client, nickname="跨会话标记")
    a = _session(client, u["access_token"], count=2)
    b = _session(client, u["access_token"], count=2)
    qid_a0 = a["items"][0]["question_id"]
    assert b["items"][0]["question_id"] == qid_a0, (
        "两个 session 的第一题应相同（未做过的优先）—— 不同的话这条判据构造不起来"
    )
    assert b["items"][0]["marked"] is False, "还没标记就带 marked"

    _put(client, u["access_token"], "marks", qid_a0)

    fresh_b = body(
        client.get(f"{API}/practice/sessions/{b['id']}", headers=auth(u["access_token"]))
    )
    assert fresh_b["code"] == 0, fresh_b
    got = fresh_b["data"]["items"][0]
    assert got["question_id"] == qid_a0, got
    assert got["marked"] is True, (
        "★ 在练习 A 标的，练习 B 的**同一道题**必须也显示已标记 —— "
        "否则就是「错题本说已标记、答题卡说未标记」（同一个事实两种说法）"
        f"：{got}"
    )


# ============================================================ ⑦⑧ 列表与分面


def test_collection_list_facets_are_always_full(client: httpx.Client) -> None:
    """★ 契约⑦⑧：分面**恒为全量**（筛空之后 chip 仍在 ⇒ 能切回去）。"""
    u = fresh_user(client, nickname="分面全量")
    s = _session(client, u["access_token"])
    qids = [it["question_id"] for it in s["items"]]
    for q in qids:
        _put(client, u["access_token"], "favorites", q)

    all_d = _list(client, u["access_token"])["data"]
    assert all_d["total"] == len(qids), all_d
    assert sum(f["count"] for f in all_d["subjects"]) == all_d["total"], all_d["subjects"]
    subs_before = {f["subject_id"] for f in all_d["subjects"]}
    assert subs_before, all_d

    # 筛一个没有收藏的科目 ⇒ 空，但**分面还在**
    empty = _list(client, u["access_token"], subject_id=999999)["data"]
    assert empty["total"] == 0 and empty["items"] == [], empty
    assert {f["subject_id"] for f in empty["subjects"]} == subs_before, (
        f"筛空之后 chip 必须还在（否则切不回去）：{empty['subjects']}"
    )


# ============================================================ ⑨ 标记也要有列表


def test_mark_list_shows_questions_never_answered_wrong(client: httpx.Client) -> None:
    """★★ 契约⑨：`kind=mark` 能列出**从没错过**的题 —— 这就是"标记也要有列表"的理由。

    只做"错题本筛已标记"的话，这道题**哪儿都看不到**（它不在错题本里）。
    """
    u = fresh_user(client, nickname="标记没错过的题")
    s = _session(client, u["access_token"])
    untouched = s["items"][0]["question_id"]

    wrong_book = body(
        client.get(f"{API}/practice/wrong-questions", headers=auth(u["access_token"]))
    )
    assert wrong_book["data"]["total"] == 0, f"先确认这道题不在错题本里：{wrong_book['data']}"

    _put(client, u["access_token"], "marks", untouched)
    marks = _list(client, u["access_token"], "mark")["data"]
    assert marks["kind"] == "mark", marks
    assert marks["total"] == 1 and len(marks["items"]) == 1, marks
    assert marks["items"][0]["question_id"] == untouched, marks["items"]
    assert marks["items"][0]["marked"] is True, marks["items"][0]
    # 收藏列表**不该**被标记污染（它们是两行数据）
    assert _list(client, u["access_token"], "favorite")["data"]["total"] == 0
    # 未登录 / 参数错的 kind ⇒ 422（Literal 卡住）
    assert (
        client.get(
            f"{API}/practice/favorites", params={"kind": "wat"}, headers=auth(u["access_token"])
        ).status_code
        == 422
    )


# ============================================================ ⑪ 错题本筛"已标记"


def test_wrong_book_marked_only_filter(client: httpx.Client) -> None:
    """★ 契约⑪：`marked_only=true` 只留标记过的，**分面仍为全量**。"""
    u = fresh_user(client, nickname="错题本筛已标记")
    s = _session(client, u["access_token"], count=3)
    for i in range(3):
        _answer_wrong(client, u["access_token"], s, i)
    qids = [it["question_id"] for it in s["items"]]

    all_d = body(client.get(f"{API}/practice/wrong-questions", headers=auth(u["access_token"])))[
        "data"
    ]
    assert all_d["total"] == 3, all_d
    assert all(it["marked"] is False for it in all_d["items"]), all_d["items"]

    _put(client, u["access_token"], "marks", qids[1])
    only = body(
        client.get(
            f"{API}/practice/wrong-questions",
            params={"marked_only": "true"},
            headers=auth(u["access_token"]),
        )
    )["data"]
    assert only["total"] == 1, f"筛已标记应只剩 1 条：{only}"
    assert [it["question_id"] for it in only["items"]] == [qids[1]], only["items"]
    assert only["items"][0]["marked"] is True, only["items"][0]
    # 分面**恒为全量**（按 marked_only 筛也不收缩）—— 与按科目筛同一条判据
    assert {f["subject_id"] for f in only["subjects"]} == {
        f["subject_id"] for f in all_d["subjects"]
    }, f"分面不该随 marked_only 收缩：{only['subjects']}"

    # 组合：科目 + 已标记
    sub = all_d["items"][0]["subject_id"]
    both = body(
        client.get(
            f"{API}/practice/wrong-questions",
            params={"marked_only": "true", "subject_id": sub},
            headers=auth(u["access_token"]),
        )
    )["data"]
    assert both["total"] == 1, both
