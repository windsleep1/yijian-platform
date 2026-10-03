"""C 端 · 错题本（P2c-2）：列表（按科目筛选）+ 详情（含正确答案）。

## 契约（每条固定一件事）

    ① 两个接口都要登录；所有查询都带 `user_id` ⇒ 别人的错题一律 **404**。
    ② ★ 列表按 `last_wrong_at` **倒序**（最近错的在前）。
    ③ 只列 `is_removed = false` 的（软删除的行不出现）。
    ④ ★ 按科目筛选：传了 `subject_id` ⇒ 列表只剩该科目，**且分面只含该科目**
       （否则会出现"点了经济、chip 写着 3、列表只有 1 条"）。
    ⑤ 分页：`page_size > 50` ⇒ 422；`page` 越界 ⇒ items 空但 `total` 不变。
    ⑥ ★★ **详情有"真的错过"这道门**：没错的题 ⇒ `40401`，**且响应里不含 `answer`**。
       这是本批最要紧的一条 —— 少了它，这个接口就是"用 `question_id` 遍历题库拿答案的后门"。
    ⑦ 详情含**归一形态**的正确答案（判断题是 `[true]`/`[false]`）+ 解析 + 选项。
    ⑧ ★ 可证伪锚：**答对的那道题不该进错题本**（否则"所有题都进错题本"的实现也能过 ②）。
    ⑨ ★ 同一道题"先错后对" ⇒ 列表里 `wrong_count=1`；**`retry_correct` 仍是 0**
       （2026-10-03 收紧：只有 `mode='wrong'` 里答对才算"重练答对" —— 见 ⑩）。
    ⑩ ★ **错题重练**（P2c-3）：`mode='wrong'` 建的会话只含**我的**错题（按 id 或整批）；
       在**重练会话里**答对 ⇒ `retry_correct` +1；错题本空 / 筛选后空 ⇒ `40401`
       （**不建空 session**）；重练会话走同一套交卷 / 报告链路。
    ⑪ ★★ **归属**：`question_ids` 是**调用方给的** ⇒ 传别人的错题 / 我没错过的题，
       就算显式指定也 **40401 且不带答案**（硬约定 G：按 id 寻址的入口都要自己再拦一次）。
       混合传入 ⇒ **逐条按归属过滤**，只留我自己的。
"""

from __future__ import annotations

import json

import httpx

from .conftest import API, auth, body, fresh_user, sql_exec, sql_fetch

SUBJECT_ECONOMY = 1001
CHAPTER_ECONOMY_1 = 1101


def _session(client: httpx.Client, token: str, *, count: int = 4) -> dict:
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


def _answer(client: httpx.Client, token: str, sess: dict, nth: int, *, right: bool) -> dict:
    """答第 `nth` 道题：`right=True` 用库里揭示的正确答案，否则挑一个必定错的。"""
    it = sess["items"][nth]
    rows = sql_fetch("SELECT answer FROM questions WHERE id = $1", int(it["question_id"]))
    assert rows, f"题目 {it['question_id']} 不见了"
    correct = json.loads(rows[0]["answer"]).get("value") or []
    if right:
        value = correct
    elif it["type"] == "judge":
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


def _list(client: httpx.Client, token: str, **q: object) -> dict:
    return body(client.get(f"{API}/practice/wrong-questions", params=q, headers=auth(token)))


# ============================================================ ① 登录


def test_wrong_book_requires_login(client: httpx.Client) -> None:
    """两个接口都不许匿名。"""
    for path in ("/practice/wrong-questions", "/practice/wrong-questions/1"):
        r = client.get(f"{API}{path}")
        assert r.status_code == 401, f"{path} 应 401，实际 {r.status_code}"
        assert body(r)["code"] == 40100, body(r)


# ============================================================ ②⑧⑨ 列表内容


def test_wrong_list_keeps_only_the_ones_actually_missed(client: httpx.Client) -> None:
    """★ 契约②⑧⑨：一错一对 ⇒ 列表里**只有错的那道**，且两条写入路都留下痕迹。"""
    u = fresh_user(client, nickname="错题本内容")
    # ★ 先**连建两个** session：此刻都还没答过 ⇒ 抽到同一道题（P2b-1 用过的办法）。
    a = _session(client, u["access_token"], count=2)
    b = _session(client, u["access_token"], count=2)
    qid_a0 = a["items"][0]["question_id"]
    assert b["items"][0]["question_id"] == qid_a0, "两个 session 的第一题应该相同（未做过的优先）"

    _answer(client, u["access_token"], a, 0, right=False)  # 错
    _answer(client, u["access_token"], b, 0, right=True)  # 对（同一道题）⇒ retry_correct +1
    _answer(client, u["access_token"], a, 1, right=True)  # 另一道题答对 ⇒ 不该进错题本

    r = _list(client, u["access_token"])
    assert r["code"] == 0, r
    d = r["data"]
    assert d["total"] == 1, f"只错了一道题，列表却 {d['total']} 条：{d}"
    assert len(d["items"]) == 1, d
    it = d["items"][0]
    assert it["question_id"] == qid_a0, f"进错题本的应是错过的那道：{it}"
    assert it["wrong_count"] == 1, it
    # ★★ 2026-10-03 **语义收紧**：章节练习里答对 **不算**重练。
    #    改造前这里是 `== 1`（"任何答对都 +1"）⇒ 错题本上会出现
    #    **从没重练过、却标着"已重练"**的题。重练那条路由 ⑩ 单独钉住。
    assert it["retry_correct"] == 0, f"章节练习里答对**不算重练**：{it}"
    assert it["stem"], "列表要带题干（列表页的用处是「认出这是哪道题」）"
    # ★ 分面与 total 对得上
    assert sum(f["count"] for f in d["subjects"]) == d["total"], d
    assert d["subjects"] and d["subjects"][0]["count"] == 1, d["subjects"]


# ============================================================ ⑩⑪ 错题重练（P2c-3）


def _retry(
    client: httpx.Client,
    token: str,
    *,
    question_ids: list[str] | None = None,
    subject_id: str | None = None,
    count: int = 10,
) -> httpx.Response:
    """建一次错题重练。`question_ids` 不给 = "重练这一组（当前筛选）"。"""
    payload: dict[str, object] = {"mode": "wrong", "count": count}
    if question_ids:
        payload["question_ids"] = question_ids
    if subject_id is not None:
        payload["subject_id"] = subject_id
    return client.post(f"{API}/practice/sessions", json=payload, headers=auth(token))


def _session_of(client: httpx.Client, token: str, sid: str) -> dict:
    b = body(client.get(f"{API}/practice/sessions/{sid}", headers=auth(token)))
    assert b["code"] == 0, b
    return b["data"]


def _miss_one(client: httpx.Client, token: str) -> tuple[dict, str]:
    """造一道错题：建章节 session → 把第 0 题答错。返回 (session, 错题 qid)。"""
    s = _session(client, token, count=2)
    _answer(client, token, s, 0, right=False)
    return s, s["items"][0]["question_id"]


def test_wrong_retry_creates_wrong_mode_session_with_only_my_wrong_question(
    client: httpx.Client,
) -> None:
    """★ 契约⑩：重练建的会话 `mode='wrong'`、只含指定的那道错题、**答对后 `retry_correct` +1**。

    ★ 顺带钉住"题目顺序与错题本同口径"：只传一道题时它是唯一元素，顺序无歧义。
    """
    u = fresh_user(client, nickname="重练这道题")
    _s, qid = _miss_one(client, u["access_token"])

    r = _retry(client, u["access_token"], question_ids=[qid])
    b = body(r)
    assert b["code"] == 0, f"建重练会话失败：{b}"
    sess = _session_of(client, u["access_token"], b["data"]["id"])

    assert sess["mode"] == "wrong", f"会话必须是 wrong 模式（前端据此显示重练）：{sess}"
    assert sess["title"] == "错题重练", sess
    assert [i["question_id"] for i in sess["items"]] == [qid], (
        f"重练会话应**只含**这道错题（不许多带别的题）：{sess['items']}"
    )
    assert sess["status"] == "doing" and sess["total"] == 1, sess

    # 重练里答对 ⇒ 记账
    _answer(client, u["access_token"], sess, 0, right=True)
    d = _list(client, u["access_token"])["data"]
    it = next(x for x in d["items"] if x["question_id"] == qid)
    assert it["retry_correct"] == 1, f"重练答对后 retry_correct 应为 1：{it}"
    assert it["wrong_count"] == 1, f"重练答对不该再记一次错：{it}"


def test_chapter_correct_is_not_a_retry(client: httpx.Client) -> None:
    """★★ 契约⑨⑪（**语义收紧的"可证伪锚"**）：章节练习里答对 **不算**重练。

    没有这一条，"任何答对都 +1"的旧实现也能过 ⑩（因为它照样会让重练那条 +1）
    —— 这正是硬约定 J 说的：**判据要能"打掉被测量的对象就变红"**。
    """
    u = fresh_user(client, nickname="章节答对不算重练")
    a = _session(client, u["access_token"], count=2)
    b = _session(client, u["access_token"], count=2)
    qid = a["items"][0]["question_id"]
    assert b["items"][0]["question_id"] == qid, "两个 session 第一题应相同（未做过的优先）"

    _answer(client, u["access_token"], a, 0, right=False)  # 错
    _answer(client, u["access_token"], b, 0, right=True)  # 用**章节**练习答对同一道题

    it = next(
        x for x in _list(client, u["access_token"])["data"]["items"] if x["question_id"] == qid
    )
    assert it["wrong_count"] == 1, it
    assert it["retry_correct"] == 0, (
        f"★ 章节练习里答对**不算重练** —— 否则错题本会出现「从没重练过却标着已重练」的题：{it}"
    )


def test_wrong_retry_all_uses_whole_book_and_respects_subject_filter(
    client: httpx.Client,
) -> None:
    """★ 契约⑩：不传 `question_ids` = 重练**整本**（按「最近错的在前」）；
    传 `subject_id` 只当**筛选**（会话的 subject_id 就是它）。"""
    u = fresh_user(client, nickname="重练整批")
    s = _session(client, u["access_token"], count=3)
    for i in range(3):
        _answer(client, u["access_token"], s, i, right=False)
    missed = {x["question_id"] for x in _list(client, u["access_token"])["data"]["items"]}
    assert len(missed) == 3, missed
    sub = SUBJECT_ECONOMY  # ★ `SessionItemOut` **没有** subject_id（那是列表项才有的字段）

    r = _retry(client, u["access_token"], subject_id=str(sub), count=10)
    sess = _session_of(client, u["access_token"], body(r)["data"]["id"])
    assert {i["question_id"] for i in sess["items"]} == missed, sess["items"]
    assert sess["subject_id"] == str(sub), f"传了 subject_id 就该记在会话上：{sess}"

    # 不含错题的科目 ⇒ 空 ⇒ 40401（**不建空 session**）
    empty = _retry(client, u["access_token"], subject_id="999999")
    assert body(empty)["code"] == 40401, body(empty)


def test_wrong_retry_on_empty_book_is_40401(client: httpx.Client) -> None:
    """★ 契约⑩：错题本是空的 ⇒ `40401`（而不是建一个 0 题的会话）。"""
    u = fresh_user(client, nickname="空错题本")
    r = _retry(client, u["access_token"])
    b = body(r)
    assert b["code"] == 40401, f"空错题本重练应 40401，实际 {b}"
    assert b.get("data") in (None, {}), f"拒绝时不该建出会话：{b}"


def test_wrong_retry_cannot_touch_someone_elses_wrong_question(client: httpx.Client) -> None:
    """★★ 契约⑪（**本批最要紧的一条**）：`question_ids` 是调用方给的 ⇒
    就算显式传别人的错题 id，也必须 **40401 且不带答案**。

    没有它，`{"mode":"wrong","question_ids":[任意题号]}` 就是一个
    "**用任意题号建会话、再顺手拿到答案**"的后门 —— 比"详情接口漏判"更隐蔽，
    因为它走的是**建会话**这条路（看起来与内容无关）。
    """
    owner = fresh_user(client, nickname="重练主人")
    other = fresh_user(client, nickname="重练旁观者")
    _s, qid = _miss_one(client, owner["access_token"])

    # ① 别人的错题 id
    b1 = body(_retry(client, other["access_token"], question_ids=[qid]))
    assert b1["code"] == 40401, f"别人的错题不该能拿来建会话：{b1}"
    assert "answer" not in json.dumps(b1, ensure_ascii=False), b1

    # ② 我**没错过**的题（自己建一个 session 拿一个真实题号，但不去答它）
    s2 = _session(client, other["access_token"], count=2)
    untouched = s2["items"][0]["question_id"]
    b2 = body(_retry(client, other["access_token"], question_ids=[untouched]))
    assert b2["code"] == 40401, f"没错过的题不该能重练：{b2}"

    # ③ 混合：一个合法 + 一个非法 ⇒ 只给合法的（**逐条按归属过滤，不是整批拒绝**）
    _s3, mine = _miss_one(client, other["access_token"])
    ok = body(_retry(client, other["access_token"], question_ids=[mine, qid]))
    assert ok["code"] == 0, ok
    sess = _session_of(client, other["access_token"], ok["data"]["id"])
    assert [i["question_id"] for i in sess["items"]] == [mine], (
        f"非法的那道必须被丢掉、只留我自己的：{sess['items']}"
    )


def test_chapter_mode_still_requires_subject_id(client: httpx.Client) -> None:
    """★ `mode='chapter'` 缺 `subject_id` ⇒ **422**（与改造前一致，不许变成 50001）。"""
    u = fresh_user(client, nickname="chapter必填")
    for payload in (
        {"chapter_id": "1101"},
        {"mode": "chapter"},
        {"subject_id": "1001", "question_ids": ["1"]},
    ):
        r = client.post(f"{API}/practice/sessions", json=payload, headers=auth(u["access_token"]))
        assert r.status_code == 422, f"应 422（入参问题，不是业务错）：{payload} -> {r.status_code}"


def test_wrong_retry_session_reports_mode_through_finish_and_report(client: httpx.Client) -> None:
    """★ 契约⑩：重练会话走**同一套**交卷 / 报告链路（`mode='wrong'` 一路透传）。"""
    u = fresh_user(client, nickname="重练也能交卷")
    _s, qid = _miss_one(client, u["access_token"])
    sid = body(_retry(client, u["access_token"], question_ids=[qid]))["data"]["id"]
    sess = _session_of(client, u["access_token"], sid)
    _answer(client, u["access_token"], sess, 0, right=True)

    fin = body(
        client.post(f"{API}/practice/sessions/{sid}/finish", headers=auth(u["access_token"]))
    )
    assert fin["code"] == 0, fin
    assert fin["data"]["mode"] == "wrong", fin["data"]
    assert fin["data"]["status"] == "finished", fin["data"]
    rep = body(client.get(f"{API}/practice/sessions/{sid}/report", headers=auth(u["access_token"])))
    assert rep["data"]["mode"] == "wrong", rep["data"]
    assert rep["data"]["correct"] == 1, rep["data"]

    # 收尾之后不许再改答案（与章节练习同一条规则）
    again = body(
        client.post(
            f"{API}/practice/sessions/{sid}/answer",
            json={"item_id": sess["items"][0]["item_id"], "value": ["A"]},
            headers=auth(u["access_token"]),
        )
    )
    assert again["code"] == 40901, again


# ============================================================ ③ 软删除


def test_wrong_list_hides_soft_removed_rows(client: httpx.Client) -> None:
    """★ 契约③：`is_removed = true` 的行不出现（软删除是"我们不再提它"，不是"删掉"）。"""
    u = fresh_user(client, nickname="软删除")
    s = _session(client, u["access_token"], count=2)
    _answer(client, u["access_token"], s, 0, right=False)
    assert _list(client, u["access_token"])["data"]["total"] == 1

    sql_exec(
        "UPDATE wrong_questions SET is_removed = true WHERE question_id = $1",
        int(s["items"][0]["question_id"]),
    )
    d = _list(client, u["access_token"])["data"]
    assert d["total"] == 0, f"软删除后不该再出现：{d}"
    assert d["items"] == [], d
    assert d["subjects"] == [], f"分面也要跟着空（否则 chip 上写着 1、列表空）：{d['subjects']}"


# ============================================================ ④⑤ 筛选与分页


def test_wrong_list_filters_by_subject_and_pages(client: httpx.Client) -> None:
    """★ 契约④⑤：筛选真的生效；分页上限与越界行为明确。"""
    u = fresh_user(client, nickname="筛选分页")
    s = _session(client, u["access_token"], count=3)
    for i in range(3):
        _answer(client, u["access_token"], s, i, right=False)

    all_d = _list(client, u["access_token"])["data"]
    assert all_d["total"] == 3, all_d
    sub = int(all_d["items"][0]["subject_id"])

    # ④ 按科目筛：命中
    hit = _list(client, u["access_token"], subject_id=sub)["data"]
    assert hit["total"] == 3, hit
    assert {int(x["subject_id"]) for x in hit["items"]} == {sub}, hit
    # ★★ 分面**恒为全量**：切了科目之后，其他 chip 必须还在（否则**切不过去**）。
    # 这条是走查抓出来的真缺陷的钉子（第一版写的是"只返选中科目"）。
    all_subs = {int(x["subject_id"]) for x in all_d["subjects"]}
    hit_subs = {int(x["subject_id"]) for x in hit["subjects"]}
    assert hit_subs == all_subs, (
        f"分面不该随筛选收缩（否则切不回别的科目）：{hit_subs} vs {all_subs}"
    )

    # ④ 按科目筛：不命中（换一个没有错题的科目）
    miss = _list(client, u["access_token"], subject_id=999999)["data"]
    assert miss["total"] == 0 and miss["items"] == [], miss
    # ★★ 筛空之后分面**仍然在** —— 这是"能切回去"的**唯一入口**：
    #    如果分面也跟着空，用户筛到一个空科目就再也点不出别的 chip 了。
    assert {int(f["subject_id"]) for f in miss["subjects"]} == {
        int(f["subject_id"]) for f in all_d["subjects"]
    }, f"筛空时 chip 也该还在（否则切不回去）：{miss['subjects']}"

    # ⑤ 分页
    p1 = _list(client, u["access_token"], page=1, page_size=2)["data"]
    assert len(p1["items"]) == 2 and p1["total"] == 3, p1
    p2 = _list(client, u["access_token"], page=2, page_size=2)["data"]
    assert len(p2["items"]) == 1, p2
    assert {x["question_id"] for x in p1["items"]} & {
        x["question_id"] for x in p2["items"]
    } == set(), "两页不该有重复"
    far = _list(client, u["access_token"], page=99, page_size=2)["data"]
    assert far["items"] == [] and far["total"] == 3, f"越界页：items 空但 total 不变：{far}"

    over = client.get(
        f"{API}/practice/wrong-questions",
        params={"page_size": 500},
        headers=auth(u["access_token"]),
    )
    assert over.status_code == 422, f"page_size 上限 50 ⇒ 超了应被拒，实际 {over.status_code}"


# ============================================================ ⑥⑦ 详情那一道门


def test_wrong_detail_requires_actually_having_missed_it(client: httpx.Client) -> None:
    """★★ 契约⑥⑦ —— 本批最要紧的一条。

    「含正确答案」这个能力**必须**被"真的错过"这道门限住：
    否则 `GET /practice/wrong-questions/{任意题号}` 就是一个**遍历题库拿答案的后门**。
    """
    u = fresh_user(client, nickname="详情那道门")
    s = _session(client, u["access_token"], count=2)
    missed = s["items"][0]["question_id"]
    untouched = s["items"][1]["question_id"]
    _answer(client, u["access_token"], s, 0, right=False)

    # ⑥ 没错的题 ⇒ 40401，**且回包里没有答案**
    bad = body(
        client.get(f"{API}/practice/wrong-questions/{untouched}", headers=auth(u["access_token"]))
    )
    assert bad["code"] == 40401, f"没进错题本的题必须 40401，实际 {bad}"
    assert "answer" not in json.dumps(bad, ensure_ascii=False), f"拒绝时不许带答案：{bad}"

    # ⑦ 错过的题 ⇒ 有答案、有解析、题型对得上
    ok = body(
        client.get(f"{API}/practice/wrong-questions/{missed}", headers=auth(u["access_token"]))
    )
    assert ok["code"] == 0, ok
    d = ok["data"]
    assert d["question_id"] == missed, d
    assert isinstance(d["answer"], dict) and d["answer"].get("value"), f"详情必须给答案：{d}"
    if d["type"] in ("single", "multiple"):
        assert d["options"], f"选择题必须带选项：{d}"
        labels = {o["label"] for o in d["options"]}
        assert set(d["answer"]["value"]) <= labels, f"答案标号必须落在选项里：{d}"
    if d["type"] == "judge":
        assert all(isinstance(v, bool) for v in d["answer"]["value"]), (
            f'判断题答案必须是**布尔**（库里有 [true] / ["A"] 两套写法，出参要归一）：{d}'
        )
    assert d["stem"], d
    assert d["wrong_count"] == 1, d


# ============================================================ ① 越权


def test_foreign_wrong_book_is_404(client: httpx.Client) -> None:
    """★ 契约①：别人的错题 ⇒ 列表空、详情 404 —— 都**不泄**。"""
    owner = fresh_user(client, nickname="错题主人")
    other = fresh_user(client, nickname="旁观者")
    s = _session(client, owner["access_token"], count=2)
    _answer(client, owner["access_token"], s, 0, right=False)
    qid = s["items"][0]["question_id"]

    d = _list(client, other["access_token"])["data"]
    assert d["total"] == 0 and d["items"] == [], f"别人的错题不该出现在我的列表里：{d}"
    r = body(
        client.get(f"{API}/practice/wrong-questions/{qid}", headers=auth(other["access_token"]))
    )
    assert r["code"] == 40401, f"别人的错题详情必须 40401，实际 {r}"
    assert "answer" not in json.dumps(r, ensure_ascii=False), f"拒绝时不许带答案：{r}"
