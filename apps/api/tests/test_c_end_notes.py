"""C 端 · 笔记（P2c-5）。

## 契约（每条固定一件事）

    ① 两个读 + 三个写都要求登录 ⇒ 未登录 **401**。
    ② ★ **可证伪锚**：写之前这道题必须是 **0 条** —— 否则"一开始就有"与"真的写了"长得一样。
    ③ ★★ **一题多条**：连写两条 ⇒ **两条都在**（`notes` 表本来就没有唯一索引）
       —— 「编辑」作用在**单条**上，所以不存在"我改的是不是我刚写的那条"。
    ④ ★ **编辑改的是同一条**：`id` 不变、**条数不变**、`content` 变。
       ⇒ 这条专门拦"编辑其实插入了一条新的"。
    ⑤ ★ `DELETE` 是**软删**：库里 `is_deleted=true`（**行还在**），但读不到了。
    ⑥ ★ `DELETE` **幂等**：连删两次都成功（判据 =「目标状态已达成」）。
    ⑦ ★ **归属**：别人的 `note_id` ⇒ 编辑 / 删除都 **40401**（不是 403），且**零副作用**。
    ⑧ ★ `content` `strip()` 后非空、≤ 2000 字 ⇒ 否则 **422**（空笔记会渲染成**空白卡片**）。
    ⑨ ★★ **约定 T**：题目下架后，**已经写下**的笔记**照样可读 / 可改 / 可删**
       （`question_available=false`、列表里**仍然列出**）；而**新写**必须 `40401`。
    ⑩ ★ 分面**恒为全量**（与错题本 / 收藏同一条判据）。
    ⑪ ★ session 出参每个 item 带 `note_count`，且**软删的笔记不算**（一次查询出，非 N+1）。
    ⑫ ★ `updated_at` **由触发器维护** ⇒ 改完它必须变；`created_at` **不该**变。
"""

from __future__ import annotations

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


def _uid(u: dict) -> int:
    """查这个刚注册用户的库内 id。

    ★ 按**手机号**查而不是读注册响应：pytest 共用一个库 ⇒ 只按 `note_id` 查会撞到别人的行。
    """
    rows = sql_fetch("SELECT id FROM users WHERE phone = $1", u["phone"])
    assert rows, f"找不到刚注册的用户：{u['phone']}"
    return int(rows[0]["id"])


def _add(client: httpx.Client, token: str, qid: str, content: str) -> dict:
    return body(
        client.post(
            f"{API}/practice/questions/{qid}/notes",
            json={"content": content},
            headers=auth(token),
        )
    )


def _notes_of(client: httpx.Client, token: str, qid: str) -> list[dict]:
    b = body(client.get(f"{API}/practice/questions/{qid}/notes", headers=auth(token)))
    assert b["code"] == 0, b
    return b["data"]["items"]


def _mylist(client: httpx.Client, token: str, **q: object) -> dict:
    return body(client.get(f"{API}/practice/notes", params=dict(q), headers=auth(token)))


def _edit(client: httpx.Client, token: str, note_id: str, content: str) -> dict:
    return body(
        client.put(
            f"{API}/practice/notes/{note_id}", json={"content": content}, headers=auth(token)
        )
    )


def _del(client: httpx.Client, token: str, note_id: str) -> dict:
    return body(client.delete(f"{API}/practice/notes/{note_id}", headers=auth(token)))


# ============================================================ ① 登录


def test_notes_require_login(client: httpx.Client) -> None:
    """★ 契约①：三个写 + 两个读，全都不许匿名。"""
    calls: list[tuple[str, str, dict | None]] = [
        ("get", "/practice/notes", None),
        ("get", "/practice/questions/1/notes", None),
        ("post", "/practice/questions/1/notes", {"content": "x"}),
        ("put", "/practice/notes/1", {"content": "x"}),
        ("delete", "/practice/notes/1", None),
    ]
    for method, path, payload in calls:
        fn = getattr(client, method)
        r = fn(f"{API}{path}", json=payload) if payload is not None else fn(f"{API}{path}")
        assert r.status_code == 401, f"{method.upper()} {path} 应 401，实际 {r.status_code}"
        assert body(r)["code"] == 40100, body(r)


# ============================================================ ②③④⑤⑥⑫ 往返


def test_note_roundtrip_create_edit_delete(client: httpx.Client) -> None:
    """★ 契约②③④⑤⑥⑫：写 → 多条 → 编辑（**同一条**）→ 软删 → 幂等。"""
    u = fresh_user(client, nickname="笔记往返")
    s = _session(client, u["access_token"])
    qid = s["items"][0]["question_id"]

    # ② 可证伪锚
    assert _notes_of(client, u["access_token"], qid) == [], "还没写就有笔记了"

    first = _add(client, u["access_token"], qid, "  第一条：先排除明显错的  ")
    assert first["code"] == 0, first
    n1 = first["data"]
    assert n1["content"] == "第一条：先排除明显错的", f"首尾空白该被 strip 掉：{n1}"
    assert n1["question_available"] is True, n1
    assert n1["question_id"] == qid and n1["id"], n1
    created_at, updated_at = n1["created_at"], n1["updated_at"]

    # ③ 一题多条：第二条是**新增**，不是覆盖
    second = _add(client, u["access_token"], qid, "第二条")
    assert second["code"] == 0, second
    got = _notes_of(client, u["access_token"], qid)
    assert len(got) == 2, f"一题应能有多条（表里没有唯一索引）：{got}"
    assert {x["id"] for x in got} == {n1["id"], second["data"]["id"]}, got

    # ④ 编辑改的是**同一条**
    edited = _edit(client, u["access_token"], n1["id"], "第一条（改过）")
    assert edited["code"] == 0, edited
    assert edited["data"]["id"] == n1["id"], f"编辑不该换 id：{edited}"
    assert edited["data"]["content"] == "第一条（改过）", edited
    after = _notes_of(client, u["access_token"], qid)
    assert len(after) == 2, (
        f"★★ 编辑**不是新增** ⇒ 条数必须不变（这条就是拦「编辑其实插了一条新的」）：{after}"
    )
    assert after[0]["id"] == n1["id"], after

    # ⑫ updated_at 由触发器维护 ⇒ 必须变；created_at **不该**变
    assert after[0]["updated_at"] != updated_at, (
        f"改完 `updated_at` 该变（`trg_notes_updated` 触发器）：{after[0]}"
    )
    assert after[0]["created_at"] == created_at, f"`created_at` 不该被改：{after[0]}"

    # ⑤ 软删：**行还在**，只是 `is_deleted=true`
    assert _del(client, u["access_token"], n1["id"])["code"] == 0
    rows = sql_fetch(
        "SELECT is_deleted FROM notes WHERE id = $1 AND user_id = $2", int(n1["id"]), _uid(u)
    )
    assert rows, f"软删不该把行删掉（那样「恢复」就无从谈起）：{rows}"
    assert rows[0]["is_deleted"] is True, f"删除必须是**软删**：{rows}"
    assert len(_notes_of(client, u["access_token"], qid)) == 1, "删掉的那条不该再读得到"

    # ⑥ 幂等：再删一次仍成功
    again = _del(client, u["access_token"], n1["id"])
    assert again["code"] == 0, f"第二次 DELETE 应成功（目标状态已达成）：{again}"
    # 但**改一条已删的**不行（那是"不存在的东西"）
    assert _edit(client, u["access_token"], n1["id"], "改已删的")["code"] == 40401


# ============================================================ ⑧ 内容校验


def test_note_content_validation(client: httpx.Client) -> None:
    """★ 契约⑧：空 / 纯空白 / 超长 ⇒ **422**，且**一个字节都不该落库**。"""
    u = fresh_user(client, nickname="笔记校验")
    s = _session(client, u["access_token"])
    qid = s["items"][0]["question_id"]
    url = f"{API}/practice/questions/{qid}/notes"
    for bad in ("", "   ", "\n\t  "):
        r = client.post(url, json={"content": bad}, headers=auth(u["access_token"]))
        assert r.status_code == 422, (
            f"空 / 纯空白内容应 422（否则界面上一张**空白卡片**），实际 {r.status_code}：{body(r)}"
        )
    too_long = client.post(url, json={"content": "字" * 2001}, headers=auth(u["access_token"]))
    assert too_long.status_code == 422, f"超过 2000 字应 422：{body(too_long)}"
    assert _notes_of(client, u["access_token"], qid) == [], "被拒的写入不该留下任何东西"


# ============================================================ ⑦ 归属


def test_foreign_note_is_40401_with_no_side_effect(client: httpx.Client) -> None:
    """★ 契约⑦：别人的笔记 ⇒ 改 / 删都 `40401`，且**零副作用**。"""
    a = fresh_user(client, nickname="笔记主人")
    b = fresh_user(client, nickname="笔记旁人")
    s = _session(client, a["access_token"])
    qid = s["items"][0]["question_id"]
    n = _add(client, a["access_token"], qid, "A 的笔记")["data"]

    bad_edit = _edit(client, b["access_token"], n["id"], "B 想改")
    assert bad_edit["code"] == 40401, (
        f"别人的笔记不该能改（也不该是 403 —— 403 会泄露「它存在」）：{bad_edit}"
    )
    bad_del = _del(client, b["access_token"], n["id"])
    assert bad_del["code"] == 40401, f"别人的笔记不该能删：{bad_del}"

    still = _notes_of(client, a["access_token"], qid)
    assert len(still) == 1 and still[0]["content"] == "A 的笔记", f"零副作用：{still}"
    assert _mylist(client, b["access_token"])["data"]["total"] == 0, "B 的列表里不该有 A 的笔记"


# ============================================================ ⑨ 约定 T


def test_notes_survive_offline_question(client: httpx.Client) -> None:
    """★★ 约定 T：题目下架后，**已写下**的笔记照样可读 / 可改 / 可删；**新写**必须被拒。

    ★ 这个不对称是**有意的**：门管的是"将来"（不能对看不到的题写新东西），
      不是对"已经写下的东西"的追溯（平台下架题目，不能让用户写下的东西消失）。
    """
    u = fresh_user(client, nickname="下架后的笔记")
    s = _session(client, u["access_token"])
    qid = s["items"][0]["question_id"]
    n = _add(client, u["access_token"], qid, "下架前写的")["data"]

    sql_exec("UPDATE questions SET is_deleted = true WHERE id = $1", int(qid))
    try:
        # 新写 ⇒ 40401
        bad = _add(client, u["access_token"], qid, "下架后想写")
        assert bad["code"] == 40401, f"下架后不该还能新增（与标记 / 收藏同一道门）：{bad}"

        # 已写的**照样可读**，且标着不可用
        got = _notes_of(client, u["access_token"], qid)
        assert len(got) == 1 and got[0]["id"] == n["id"], got
        assert got[0]["content"] == "下架前写的", got[0]
        assert got[0]["question_available"] is False, (
            f"下架要体现在 `question_available=false` 上：{got[0]}"
        )

        # 列表里**仍然列出**
        d = _mylist(client, u["access_token"])["data"]
        assert d["total"] == 1 and d["items"][0]["id"] == n["id"], (
            f"下架后笔记**不该**从列表里消失（约定 T）：{d}"
        )
        assert d["items"][0]["question_available"] is False, d["items"][0]

        # ★ 改和删**都还能做**（我的东西我要能管）
        assert _edit(client, u["access_token"], n["id"], "下架后改的")["code"] == 0
        assert _del(client, u["access_token"], n["id"])["code"] == 0
    finally:
        sql_exec("UPDATE questions SET is_deleted = false WHERE id = $1", int(qid))


# ============================================================ ⑩ 列表 / 分面


def test_notes_list_facets_and_paging(client: httpx.Client) -> None:
    """★ 契约⑩：分页不重不漏；分面**恒为全量**；每行带 `stem`。"""
    u = fresh_user(client, nickname="笔记列表")
    s = _session(client, u["access_token"], count=3)
    for i, it in enumerate(s["items"]):
        assert _add(client, u["access_token"], it["question_id"], f"第 {i} 题的笔记")["code"] == 0

    d = _mylist(client, u["access_token"])["data"]
    assert d["total"] == 3, d
    assert sum(f["count"] for f in d["subjects"]) == 3, d["subjects"]
    subs = {f["subject_id"] for f in d["subjects"]}
    assert subs, d
    assert all(x["stem"] for x in d["items"]), f"列表每行要带 stem（认出那是哪道题）：{d['items']}"

    p1 = _mylist(client, u["access_token"], page=1, page_size=2)["data"]
    p2 = _mylist(client, u["access_token"], page=2, page_size=2)["data"]
    assert len(p1["items"]) == 2 and len(p2["items"]) == 1, (p1, p2)
    assert {x["id"] for x in p1["items"]} & {x["id"] for x in p2["items"]} == set(), "两页不该重复"

    # 分面恒为全量：筛一个没有笔记的科目 ⇒ 空，但**分面还在**（否则切不回去）
    empty = _mylist(client, u["access_token"], subject_id=999999)["data"]
    assert empty["total"] == 0 and empty["items"] == [], empty
    assert {f["subject_id"] for f in empty["subjects"]} == subs, (
        f"筛空之后 chip 必须还在：{empty['subjects']}"
    )


# ============================================================ ⑪ note_count


def test_session_items_carry_note_count(client: httpx.Client) -> None:
    """★ 契约⑪：session 每题带 `note_count`，且**软删的不算**（否则徽标永远比列表多）。"""
    u = fresh_user(client, nickname="笔记计数")
    s = _session(client, u["access_token"], count=2)
    q0 = s["items"][0]["question_id"]
    assert s["items"][0]["note_count"] == 0, f"可证伪锚：还没写就非 0：{s['items'][0]}"

    _add(client, u["access_token"], q0, "一")
    _add(client, u["access_token"], q0, "二")
    s2 = body(client.get(f"{API}/practice/sessions/{s['id']}", headers=auth(u["access_token"])))[
        "data"
    ]
    assert s2["items"][0]["note_count"] == 2, s2["items"][0]
    assert s2["items"][1]["note_count"] == 0, f"别的题不该被算进来：{s2['items'][1]}"

    ids = [x["id"] for x in _notes_of(client, u["access_token"], q0)]
    assert _del(client, u["access_token"], ids[0])["code"] == 0
    s3 = body(client.get(f"{API}/practice/sessions/{s['id']}", headers=auth(u["access_token"])))[
        "data"
    ]
    assert s3["items"][0]["note_count"] == 1, (
        f"软删的那条**不该**还算在计数里（否则答题页徽标与列表对不上）：{s3['items'][0]}"
    )
