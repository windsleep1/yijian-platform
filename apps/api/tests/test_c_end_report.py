"""C 端 · 练习报告（P2c-1）：交卷（#7）+ 报告（#2）。

## 契约（每条固定一件事）

    ① 两个接口都要登录；按 id 寻址的都要拦归属（别人的 session ⇒ **404**，且**零副作用**）。
    ② ★ **用时由服务端算**（`now() - started_at`），不吃前端传的值。
       做法：直连库把 `started_at` 往回挪 125 秒，再看接口返的 `duration_sec` ——
       "服务端算的"这件事只有这样才**可证伪**（否则 0 秒和 125 秒在响应里长得一样）。
    ③ ★ **交卷幂等 = "目标状态已达成"**（硬约定 C）：第二次交卷 `finished_at` / `duration_sec`
       **逐字节不变**，且返回同一份报告。
    ④ ★★ **零分母返 `null`**（红线）：一道题都没答 ⇒ `accuracy is None`。
       ⚠️ 这条要按"它该报相反结果"来验：**答过题之后同一个字段必须变成数**（不是 None）。
    ⑤ ★ **报告不要求 `status='finished'`**（硬约定 A：读路径不夹带更严的准入）。
    ⑥ 交卷之后不能再改答案（`40901`）—— 交卷真正让那个"状态"**可达**了
       （在此之前 `practice_sessions.status` 永远停在 `doing`，见 `docs/24` §21）。
    ⑦ `by_kp`：按正确率**升序**（最弱在前）；`sum(total) == answered`、`sum(correct) == correct`。
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
    """答第 `nth` 道题。`right=True` 用**库里揭示的**正确答案；否则挑一个必定错的。"""
    it = sess["items"][nth]
    rows = sql_fetch("SELECT answer FROM questions WHERE id = $1", int(it["question_id"]))
    assert rows, f"题目 {it['question_id']} 不见了"
    doc = json.loads(rows[0]["answer"])
    correct = doc.get("value") or []
    if right:
        value = correct
    elif it["type"] == "judge":
        value = [not bool(correct[0])]
    else:
        labels = [o["label"] for o in it["options"]] or ["A", "B", "C", "D"]
        value = [
            next(lb for lb in labels if str(lb).upper() not in {str(c).upper() for c in correct})
        ]
    b = body(
        client.post(
            f"{API}/practice/sessions/{sess['id']}/answer",
            json={"item_id": it["item_id"], "value": value},
            headers=auth(token),
        )
    )
    assert b["code"] == 0, b
    return b["data"]


def _finish(client: httpx.Client, token: str, sid: str) -> dict:
    return body(client.post(f"{API}/practice/sessions/{sid}/finish", headers=auth(token)))


def _report(client: httpx.Client, token: str, sid: str) -> dict:
    return body(client.get(f"{API}/practice/sessions/{sid}/report", headers=auth(token)))


def _row(sid: str) -> dict:
    rows = sql_fetch(
        "SELECT status, finished_at, duration_sec, answered, correct, score "
        "FROM practice_sessions WHERE id = $1",
        int(sid),
    )
    assert rows, f"session {sid} 不见了"
    return rows[0]


# ============================================================ ① 登录


def test_report_and_finish_require_login(client: httpx.Client) -> None:
    """两个新接口都不许匿名（C 端没有公开特例）。"""
    for method, path in (
        ("post", "/practice/sessions/1/finish"),
        ("get", "/practice/sessions/1/report"),
    ):
        r = getattr(client, method)(f"{API}{path}")
        assert r.status_code == 401, f"{path} 应 401，实际 {r.status_code}"
        assert body(r)["code"] == 40100, body(r)


# ============================================================ ② 用时是服务端算的


def test_finish_writes_status_and_server_side_duration(client: httpx.Client) -> None:
    """★ 契约②：把 `started_at` 挪回 125 秒前 ⇒ `duration_sec` 必须跟着变成 ≈125。

    ⚠️ 为什么必须这么验：如果用例只断言"`duration_sec` 是个非负整数"，
       那么**一个永远写 0 的实现也能过**（0 确实是非负整数）。
       把时间挪走之后，"服务端真的用 `now() - started_at` 算"才**可证伪**。
    """
    u = fresh_user(client, nickname="交卷用时")
    sess = _session(client, u["access_token"], count=2)
    sql_exec(
        "UPDATE practice_sessions SET started_at = now() - interval '125 seconds' WHERE id = $1",
        int(sess["id"]),
    )

    b = _finish(client, u["access_token"], sess["id"])
    assert b["code"] == 0, b
    d = b["data"]
    assert d["status"] == "finished", d
    assert d["finished_at"] is not None, d
    assert 120 <= d["duration_sec"] <= 135, (
        f"用时应由服务端算出来 ≈125 秒，实际 {d['duration_sec']}"
    )

    row = _row(sess["id"])
    assert row["status"] == "finished", row
    assert row["finished_at"] is not None, row
    assert 120 <= int(row["duration_sec"]) <= 135, row


# ============================================================ ③④⑥ 幂等 / 白卷 / 交卷后禁写


def test_finish_is_idempotent_and_blocks_later_answers(client: httpx.Client) -> None:
    """★ 契约③⑥：第二次交卷零写入；交卷后再答 ⇒ `40901`。"""
    u = fresh_user(client, nickname="交卷幂等")
    sess = _session(client, u["access_token"], count=2)
    _answer(client, u["access_token"], sess, 0, right=True)

    first = _finish(client, u["access_token"], sess["id"])
    assert first["code"] == 0, first
    before = _row(sess["id"])

    second = _finish(client, u["access_token"], sess["id"])
    assert second["code"] == 0, second
    after = _row(sess["id"])
    # ★ "目标状态已达成" ⇒ 两次读回来的三个字段必须**一模一样**（不是"差不多"）
    assert str(after["finished_at"]) == str(before["finished_at"]), (before, after)
    assert int(after["duration_sec"]) == int(before["duration_sec"]), (before, after)
    assert second["data"]["score"] == first["data"]["score"], (first, second)

    # 交卷之后不许再改答案：这条同时证明 `status` 真的被写成了 `finished`
    late = body(
        client.post(
            f"{API}/practice/sessions/{sess['id']}/answer",
            json={"item_id": sess["items"][1]["item_id"], "value": ["A"]},
            headers=auth(u["access_token"]),
        )
    )
    assert late["code"] == 40901, late
    assert int(_row(sess["id"])["answered"]) == 1, "被拒的提交不该有任何写入"


def test_report_on_blank_session_has_null_accuracy(client: httpx.Client) -> None:
    """★★ 契约④：**零分母返 `null`**（红线）。

    ⚠️ 这条用例要按"它该报相反结果"来读：`accuracy is None` 单独看只是"没崩"；
       真正把它钉死的是**同一字段在答过题之后必须变成数**（见上面那条用例与下面这条）——
       否则一个永远返回 `None` 的实现也能过。
    """
    u = fresh_user(client, nickname="白卷")
    sess = _session(client, u["access_token"], count=2)

    r = _report(client, u["access_token"], sess["id"])
    assert r["code"] == 0, r
    d = r["data"]
    assert d["answered"] == 0, d
    assert d["accuracy"] is None, f"0/0 必须返 null（不是 0.0），实际 {d['accuracy']!r}"
    assert d["by_kp"] == [], "一道没答 ⇒ 知识点分布必须是空的（不是一堆 0/0 的条目）"
    assert d["duration_sec"] >= 0, d
    assert d["finished_at"] is None, "还没交卷就不该有 finished_at"

    # ★ 同一个字段，答过之后必须变成**数**（这才叫"零分母"而不是"永远为 null"）
    _answer(client, u["access_token"], sess, 0, right=True)
    d2 = _report(client, u["access_token"], sess["id"])["data"]
    assert d2["accuracy"] == 1.0, f"答对 1/1 ⇒ 正确率必须是 1.0，实际 {d2['accuracy']!r}"


# ============================================================ ⑤ 报告在 doing 时也能看


def test_report_is_readable_while_doing(client: httpx.Client) -> None:
    """★ 契约⑤：**读路径不夹带比读接口更严的准入**（硬约定 A）。"""
    u = fresh_user(client, nickname="未完也能看")
    sess = _session(client, u["access_token"], count=3)
    _answer(client, u["access_token"], sess, 0, right=False)

    r = _report(client, u["access_token"], sess["id"])
    assert r["code"] == 0, r
    d = r["data"]
    assert d["status"] == "doing", "报告不该顺手把练习「收尾」（那是 finish 的事）"
    assert d["answered"] == 1 and d["correct"] == 0, d
    assert d["accuracy"] == 0.0, (
        f"答了 1 道错的 ⇒ 正确率是 0.0（**不是 None**），实际 {d['accuracy']!r}"
    )


# ============================================================ ⑦ 知识点分布


def test_report_by_kp_aggregates_and_sorts_weakest_first(client: httpx.Client) -> None:
    """★ 契约⑦：`by_kp` 的合计要对得上，且**最弱的排第一**。"""
    u = fresh_user(client, nickname="知识点分布")
    sess = _session(client, u["access_token"], count=6)
    # 交替答对 / 答错，让不同的知识点拿到不同的正确率
    for i in range(6):
        _answer(client, u["access_token"], sess, i, right=(i % 2 == 0))
    r = _finish(client, u["access_token"], sess["id"])
    assert r["code"] == 0, r
    d = r["data"]

    kps = d["by_kp"]
    assert kps, "6 道题全都答了，知识点分布不该是空的（空了说明 join 断了）"
    assert sum(k["total"] for k in kps) == d["answered"], (kps, d["answered"])
    assert sum(k["correct"] for k in kps) == d["correct"], (kps, d["correct"])
    for k in kps:
        assert k["total"] >= 1, k
        assert k["name"], f"知识点必须有名字（没归类的给「未归类」）：{k}"
        assert k["accuracy"] == round(k["correct"] / k["total"], 4), k
    accs = [k["accuracy"] for k in kps]
    assert accs == sorted(accs), f"必须按正确率**升序**（最弱在前）：{accs}"
    assert d["accuracy"] == round(d["correct"] / d["answered"], 4), d

    # ★ 报告**不返逐题解析**（那是 `GET /practice/sessions/{id}` 的事）——
    #   100 题的 session 把解析塞进报告只会让首屏多等几百 KB。
    assert "items" not in d, f"报告不该带 items：{[k for k in d]}"


# ============================================================ ① 越权：404 且零副作用


def test_foreign_session_finish_is_404_and_does_not_touch_it(client: httpx.Client) -> None:
    """★ 契约①：别人的 session ⇒ **404**（不是 403），且**一个字都不许改**。"""
    owner = fresh_user(client, nickname="归属主人")
    other = fresh_user(client, nickname="越权者")
    sess = _session(client, owner["access_token"], count=2)

    r = _finish(client, other["access_token"], sess["id"])
    assert r["code"] == 40401, f"越权交卷必须 40401，实际 {r}"
    g = _report(client, other["access_token"], sess["id"])
    assert g["code"] == 40401, f"越权看报告必须 40401，实际 {g}"
    n = _finish(client, other["access_token"], "999999999999")
    assert n["code"] == 40401, n

    row = _row(sess["id"])
    assert row["status"] == "doing", f"被拒的请求不该改动别人的 session：{row}"
    assert row["finished_at"] is None, row
