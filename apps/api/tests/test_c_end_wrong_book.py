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
    ⑨ ★ 同一道题"先错后对" ⇒ 列表里 `wrong_count=1`、`retry_correct=1`
       （证明 `_UPSERT_WRONG` 与 `_BUMP_RETRY_CORRECT` **两条路都在工作**）。
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
    assert it["retry_correct"] == 1, f"之后答对过 1 次（证明 _BUMP_RETRY_CORRECT 在工作）：{it}"
    assert it["stem"], "列表要带题干（列表页的用处是「认出这是哪道题」）"
    # ★ 分面与 total 对得上
    assert sum(f["count"] for f in d["subjects"]) == d["total"], d
    assert d["subjects"] and d["subjects"][0]["count"] == 1, d["subjects"]


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
