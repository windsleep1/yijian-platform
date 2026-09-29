"""C 端 · 刷题（P2b-1）：选章节 → 建 session → 取 session → 提交判分。

## 契约（每条固定一件事，不写"覆盖某一行"）

    ① 三个接口**都要登录**；按 id 寻址的**都要拦归属** —— 别人的 session 一律 **404**
       （不是 403：403 会变成"这个 id 存在但你没权限"的探测口，硬约定 G）。
    ② ★ **未作答的题不返 `answer` / `analysis`**；**答过的题才返**。
       而且"答了第 1 题"不能让第 2 题也可见 —— 可见性判断必须**逐题**生效。
    ③ ★ **幂等 = "目标状态已达成"**（硬约定 C）：同一题提交两次 ⇒ 第二次
       `idempotent=true`、分数不变、`answered` 与 `user_question_state` **都不重复记账**。
    ④ 判分：单选/判断全对全分；多选全对 1.0、真子集且无错选（题目允许部分分）⇒ 0.5。
       ★ 部分分时 `is_correct` 仍是 **false** —— "算对"与"给分"是两个维度，
       混起来会让"答对率"被部分分污染。
    ⑤ 会话已结束 ⇒ 拒绝再写（`40901`）。
    ⑥ ★ `chapters.question_count` 那个列**是死的**（写着"定时刷新"、没有任务在刷）。
       `GET /subjects/{id}/chapters` 的 `question_count` 必须是**实时算**的，
       否则选章节页会显示"每章 0 题" —— 看起来像没有题库，实际有 6000 道。

## 判分部分是**纯函数**单测

`grade()` / `normalize_user_value()` / `_rollup()` 不碰数据库，所以它们的分支
（尤其是"部分分允许 / 不允许""重复标号""子章节汇总"）用直调覆盖 ——
比灌一堆数据再走 HTTP 更准，也更快（硬约定 J：判据要能**构造出它该报相反结果的场景**）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.practice_service import (
    _judge_bool,
    _rollup,
    grade,
    normalize_user_value,
    public_answer,
)

from .conftest import API, auth, body, fresh_user, sql_exec, sql_fetch

#: 种子里的一门公共课及其章节（`db/schema.sql`）：建设工程经济 / 第1章 工程经济。
SUBJECT_ECONOMY = 1001
CHAPTER_ECONOMY_1 = 1101


# ============================================================ 纯函数（判分）


def test_grade_single_and_judge() -> None:
    """单选 / 判断：集合相等 ⇒ 全对；否则 0 分（**没有部分分这回事**）。"""
    assert grade("single", ["B"], ["B"], False) == (True, 1.0)
    assert grade("single", ["B"], ["b"], False) == (True, 1.0), "标号必须大小写无关"
    assert grade("single", ["B"], ["C"], False) == (False, 0.0)
    assert grade("single", ["B"], ["B", "C"], False) == (False, 0.0), "单选给两个标号 = 错"
    assert grade("judge", [True], [True], False) == (True, 1.0)
    assert grade("judge", [True], [False], False) == (False, 0.0)


def test_grade_multiple_partial_credit_is_gated_by_the_question() -> None:
    """★ 部分分**由题目自己的 `partial_credit` 决定**，不是我们替他做主。

    这条同时是"判据能被证伪"的例子：把 `partial_credit` 从 True 改成 False，
    同一组输入必须给出**两个不同的结果** —— 否则这个参数根本没被读到。
    """
    assert grade("multiple", ["A", "C"], ["A", "C"], True) == (True, 1.0)
    assert grade("multiple", ["A", "C"], ["C", "a"], True) == (True, 1.0), "顺序/大小写无关"
    # 真子集且无错选：允许部分分 ⇒ 给分但**不算对**
    assert grade("multiple", ["A", "C"], ["A"], True) == (False, 0.5)
    # 同一组输入，题目不允许部分分 ⇒ 0 分（证明那个开关真的在起作用）
    assert grade("multiple", ["A", "C"], ["A"], False) == (False, 0.0)
    # 有错选：任何口径下都是 0（部分分只认"真子集"）
    assert grade("multiple", ["A", "C"], ["A", "D"], True) == (False, 0.0)
    assert grade("multiple", ["A", "C"], ["A", "C", "D"], True) == (False, 0.0)
    # 空答案 = 错
    assert grade("multiple", ["A", "C"], [], True) == (False, 0.0)


def test_grade_no_answer_is_wrong_not_right() -> None:
    """没有正确答案的题 ⇒ **判错**。

    ⚠️ 方向不能反：算对等于把"数据缺陷"藏进成绩里（用户看到满分，没人知道有问题）。
    """
    assert grade("single", [], ["B"], False) == (False, 0.0)


def test_judge_has_two_stored_conventions_and_both_are_understood() -> None:
    """★★ 判断题在库里有**两套写法**（实测，不是猜的），**两套都必须判对**。

    - 管理端新建（`question_service.derive_answer`）→ `{"value": [true]}`
    - 种子生成器（`db/seed/gen_seed_questions.py::gen_judge`）→ `{"value": ["A"]}`（正确）/ `["B"]`（错误）

    ⚠️ **这条测试是为了钉住一个"会静默给满分"的 bug**：
      如果把 `["A"]` 直接 `bool()` 读，`bool("A") == True` ⇒ 无论用户选什么，
      "正确答案是 A"的题都会判对。而反向（正确答案是 `["B"]`）则会把**对答判成错答**。
      两边都在下面钉住了 —— **只要有一条方向反了，这条测试必红**。
    """
    # 第一套：布尔
    assert grade("judge", [True], [True], False) == (True, 1.0)
    assert grade("judge", [True], [False], False) == (False, 0.0)
    # 第二套：种子用标号（A = 表述正确，B = 表述错误）
    assert grade("judge", ["A"], [True], False) == (True, 1.0)
    assert grade("judge", ["A"], [False], False) == (False, 0.0)
    assert grade("judge", ["B"], [False], False) == (True, 1.0)
    assert grade("judge", ["B"], [True], False) == (False, 0.0)
    # 认不出来的写法 ⇒ 判错（**绝不猜**：猜错的方向是"把错答判成对"）
    assert grade("judge", ["嗯"], [True], False) == (False, 0.0)


def test_public_answer_normalizes_judge_for_the_client() -> None:
    """出参归一：判断题**一律给布尔**，前端不需要知道库里有两套写法。

    认不出来时**原样透出** —— 宁可显示一个怪字符串，也不要显示一个假的"错误"。
    """
    assert public_answer("judge", ["A"]) == {"value": [True]}
    assert public_answer("judge", ["B"]) == {"value": [False]}
    assert public_answer("judge", [True]) == {"value": [True]}
    assert public_answer("judge", ["嗯"]) == {"value": ["嗯"]}, "认不出来就别假装"
    # 非判断题原样
    assert public_answer("single", ["B"]) == {"value": ["B"]}


def test_seeded_judge_answers_are_all_understood() -> None:
    """★ **数据级钉住**：种子库里每一条判断题的答案，`_judge_bool` 都必须认得。

    为什么要单开一条：上面那两条用**构造的输入**验归一；这一条验的是
    "**现实里的数据**长什么样" —— 生成器哪天换了写法（比如改成 `["对"]` 之外的第三种），
    这条会红，而构造输入的那两条**不会**。
    （判据同硬约定 H：验收脚本要问"它依赖哪些不在我控制里的东西"。）
    """
    rows = sql_fetch(
        "SELECT DISTINCT answer->>'value' AS v FROM questions "
        "WHERE type = 'judge' AND is_deleted = false LIMIT 20"
    )
    assert rows is not None, "这些用例需要数据库（DATABASE_URL）"
    assert rows, "种子里应该有判断题；一条都没有说明种子没灌上"
    for r in rows:
        tokens = json.loads(r["v"]) if r["v"] else []
        assert tokens, f"判断题的 answer.value 是空的：{r}"
        for t in tokens:
            assert _judge_bool(t) is not None, (
                f"种子里出现了我们**不认识**的判断题答案写法：{t!r}。"
                "要么把它加进 _judge_bool 的 token 表，要么改生成器统一口径（BL-20）"
            )


def test_normalize_user_value_contract() -> None:
    """按题型归一化：**拒绝时说清是哪一种题型**，而不是笼统的"参数错误"。"""
    assert normalize_user_value("single", ["b"]) == ["B"]
    assert normalize_user_value("multiple", ["c", "a"]) == ["A", "C"], "多选按标号排序后再存"
    assert normalize_user_value("judge", [True]) == [True]
    # 判断题**容忍**库里那套标号写法（前端不会用，但测试/脚本很可能照库里的样子发）
    assert normalize_user_value("judge", ["A"]) == [True]
    assert normalize_user_value("judge", ["B"]) == [False]
    assert normalize_user_value("judge", ["错"]) == [False]
    with pytest.raises(Exception) as e1:
        normalize_user_value("judge", ["嗯"])
    assert "判断" in str(e1.value), "错误信息要点名题型"
    with pytest.raises(Exception) as e2:
        normalize_user_value("single", ["A", "B"])
    assert "单选" in str(e2.value)
    with pytest.raises(Exception) as e3:
        normalize_user_value("multiple", ["A", "A"])
    assert "重复" in str(e3.value)
    with pytest.raises(Exception) as e4:
        normalize_user_value("case", ["随便"])
    assert "暂不支持" in str(e4.value)


def test_rollup_sums_the_subtree() -> None:
    """★ 一级章节的数字必须**含子章节** —— 题挂在叶子上，只报直接数会让用户看到"什么都没做"。"""
    # 1001(top) → 1002(mid) → 1003(leaf)
    parent_of = {1001: None, 1002: 1001, 1003: 1002}
    direct = {1003: 5}
    got = _rollup([1001, 1002, 1003], parent_of, direct)
    assert got == {1001: 5, 1002: 5, 1003: 5}, got
    # 兄弟节点互不串味
    parent_of = {1: None, 2: 1, 3: 1}
    got = _rollup([1, 2, 3], parent_of, {2: 2, 3: 7})
    assert got == {1: 9, 2: 2, 3: 7}, got


# ============================================================ HTTP 契约


def _session(client: httpx.Client, token: str, *, count: int = 4) -> dict:
    r = client.post(
        f"{API}/practice/sessions",
        json={
            "subject_id": str(SUBJECT_ECONOMY),
            "chapter_id": str(CHAPTER_ECONOMY_1),
            "count": count,
        },
        headers=auth(token),
    )
    b = body(r)
    assert b["code"] == 0, f"建 session 失败：{b}"
    sid = b["data"]["id"]
    assert isinstance(sid, str) and sid.isdigit(), (
        f"id 必须是数字字符串（雪花 ID 超 JS 安全整数）：{sid!r}"
    )
    s = body(client.get(f"{API}/practice/sessions/{sid}", headers=auth(token)))
    assert s["code"] == 0, s
    return s["data"]


def _correct_answer(qid: str) -> dict:
    rows = sql_fetch("SELECT answer FROM questions WHERE id = $1", int(qid))
    assert rows, f"库里的题目 {qid} 不见了"
    return json.loads(rows[0]["answer"])


def _uid(client: httpx.Client, u: dict) -> int:
    """拿用户 id。**走 `/auth/me`**，不猜注册响应的字段名 ——
    猜错的话症状是 `KeyError`（看起来像用例写错），而实际只是返回值形状不同。"""
    b = body(client.get(f"{API}/auth/me", headers=auth(u["access_token"])))
    assert b["code"] == 0, b
    return int(b["data"]["id"])


def test_practice_requires_login(client: httpx.Client) -> None:
    """三个接口都不许匿名（C 端没有公开特例）。"""
    for method, path, payload in (
        ("post", "/practice/sessions", {"subject_id": "1001", "chapter_id": "1101"}),
        ("get", "/practice/sessions/1", None),
        ("post", "/practice/sessions/1/answer", {"item_id": "1", "value": ["A"]}),
    ):
        r = getattr(client, method)(f"{API}{path}", **({"json": payload} if payload else {}))
        assert r.status_code == 401, f"{path} 应 401，实际 {r.status_code}"
        assert body(r)["code"] == 40100, body(r)


def test_chapters_live_count_beats_the_dead_column(client: httpx.Client) -> None:
    """★ 契约⑥：`question_count` 是**实时算**的，不是那张表里的冗余列。

    这条的"能证伪"形式：**同时**断言"表里的列是 0"与"接口给的是正数"。
    只断言后者不够 —— 万一哪天有人把冗余列填上了，这条测试就会变成
    "接口读的是列"的假证据。
    """
    u = fresh_user(client, nickname="选章节用例")
    r = client.get(f"{API}/subjects/{SUBJECT_ECONOMY}/chapters", headers=auth(u["access_token"]))
    b = body(r)
    assert b["code"] == 0, b
    items = b["data"]
    assert items, "科目 1001 应该有章节（db/schema.sql 的种子）"

    by_id = {int(c["id"]): c for c in items}
    assert CHAPTER_ECONOMY_1 in by_id, f"缺少种子章节 {CHAPTER_ECONOMY_1}"
    # 冗余列：读出来应该还是**建表默认值 0**（没有任务在刷它）
    rows = sql_fetch("SELECT question_count FROM chapters WHERE id = $1", CHAPTER_ECONOMY_1)
    assert rows is not None, "这些用例需要数据库（DATABASE_URL）"
    assert int(rows[0]["question_count"]) == 0, (
        "本章节那个冗余列居然不是 0 —— 请先确认是谁在刷新它，再决定这条断言怎么写"
    )
    assert by_id[CHAPTER_ECONOMY_1]["question_count"] > 0, (
        "接口返回的 question_count 必须是**实时算**的（>0）；"
        "如果是 0，说明它去读了那个没人刷的冗余列"
    )
    # id 是字符串 + 按 sort_no 升序（前端要靠这个顺序排）
    assert all(isinstance(c["id"], str) for c in items)
    assert [c["sort_no"] for c in items] == sorted(c["sort_no"] for c in items)
    # 我的进度字段先都在（新账号 = 0）
    assert all(c["my_answered"] == 0 and c["my_correct"] == 0 for c in items)


def test_chapters_of_unknown_subject_is_empty_list(client: httpx.Client) -> None:
    u = fresh_user(client, nickname="坏科目用例")
    r = client.get(f"{API}/subjects/999999/chapters", headers=auth(u["access_token"]))
    assert r.status_code == 200 and body(r)["code"] == 0, "未知科目返回空列表即可（不是错误）"
    assert body(r)["data"] == []


def test_create_session_hides_answers_until_answered(client: httpx.Client) -> None:
    """★ 契约②：`doing` 状态下**未作答的题不返 `answer` / `analysis`**，但题干和选项要给全。"""
    u = fresh_user(client, nickname="可见性用例")
    s = _session(client, u["access_token"], count=3)
    assert s["status"] == "doing" and s["mode"] == "chapter"
    assert s["total"] == 3 and len(s["items"]) == 3
    assert s["answered"] == 0
    assert s["current_item_id"] == s["items"][0]["item_id"], "断点恢复的落点 = 第一道没答的题"
    for it in s["items"]:
        assert it["answered"] is False
        assert it["answer"] is None, f"未作答就漏答案了：seq={it['seq']}"
        assert it["analysis"] is None
        assert it["my_value"] is None
        assert it["stem"], "题干要给的"
        assert it["options"], "选项要给的（不然答不了）"
        assert it["type"] in ("single", "multiple", "judge")
        # ★ 选项里**不许**出现 is_correct —— 契约是"不给答案"，不是"给一个空字段"
        assert all("is_correct" not in o for o in it["options"])


def test_session_detail_is_owner_scoped(client: httpx.Client) -> None:
    """别人的 session ⇒ **404**（不是 403：不暴露"这个 id 存在"）。"""
    owner = fresh_user(client, nickname="会话属主")
    other = fresh_user(client, nickname="旁观者")
    s = _session(client, owner["access_token"], count=2)
    r = client.get(f"{API}/practice/sessions/{s['id']}", headers=auth(other["access_token"]))
    assert r.status_code == 404, f"应 404，实际 {r.status_code}: {r.text[:160]}"
    assert body(r)["code"] == 40401, body(r)
    # 属主自己看得到（**可证伪锚**：先证明"同一条路径本来能用"）
    ok = client.get(f"{API}/practice/sessions/{s['id']}", headers=auth(owner["access_token"]))
    assert ok.status_code == 200 and body(ok)["code"] == 0


def test_answer_correct_then_reveal_only_that_item(client: httpx.Client) -> None:
    """★ 契约② + ④：答对第 1 题 ⇒ 它可见答案与解析，**第 2 题仍然不可见**。"""
    u = fresh_user(client, nickname="判分用例")
    s = _session(client, u["access_token"], count=3)
    first = s["items"][0]
    ans = _correct_answer(first["question_id"])

    r = client.post(
        f"{API}/practice/sessions/{s['id']}/answer",
        json={"item_id": first["item_id"], "value": ans["value"]},
        headers=auth(u["access_token"]),
    )
    b = body(r)
    assert b["code"] == 0, b
    d = b["data"]
    assert d["is_correct"] is True, f"提交的就是库里的正确答案，必须判对：{d}"
    assert d["score"] > 0 and d["idempotent"] is False
    assert d["analysis"] not in (None, ""), "答完要看得到解析"
    assert d["session"]["answered"] == 1 and d["session"]["correct"] == 1

    s2 = body(client.get(f"{API}/practice/sessions/{s['id']}", headers=auth(u["access_token"])))[
        "data"
    ]
    assert s2["items"][0]["answered"] is True and s2["items"][0]["answer"] is not None
    assert s2["current_item_id"] == s2["items"][1]["item_id"], "断点应该前进到第 2 题"
    assert s2["items"][1]["answer"] is None, "答了第 1 题不能让第 2 题也可见"


def test_answer_wrong_is_recorded_and_idempotent(client: httpx.Client) -> None:
    """★ 契约③ + ④：答错 ⇒ 计错题；**再提交一次零写入**（`idempotent=true`，不重复记账）。"""
    u = fresh_user(client, nickname="幂等用例")
    s = _session(client, u["access_token"], count=2)
    first = s["items"][0]
    right = _correct_answer(first["question_id"])

    # 构造一个**必定错**的答案：按题型取一个不同的值
    if first["type"] == "judge":
        # ⚠️ 必须走 `_judge_bool` 再取反：库里可能是 `["B"]`（B = 表述错误），
        #   而 `not bool("B")` 是 **False** —— 那正好是正确答案，用例会变成"提交对的却断言错"。
        wrong = [not _judge_bool(right["value"][0])]
    else:
        labels = [o["label"] for o in first["options"]]
        wrong = [next(lb for lb in labels if lb not in set(right["value"]))]

    r1 = body(
        client.post(
            f"{API}/practice/sessions/{s['id']}/answer",
            json={"item_id": first["item_id"], "value": wrong},
            headers=auth(u["access_token"]),
        )
    )
    assert r1["code"] == 0, r1
    assert r1["data"]["is_correct"] is False and r1["data"]["score"] == 0
    assert r1["data"]["idempotent"] is False

    # ---- 第二次提交：幂等分支 ----
    r2 = body(
        client.post(
            f"{API}/practice/sessions/{s['id']}/answer",
            json={"item_id": first["item_id"], "value": right["value"]},  # 甚至换个答案
            headers=auth(u["access_token"]),
        )
    )
    assert r2["code"] == 0, r2
    assert r2["data"]["idempotent"] is True, "已作答 ⇒ 必须走幂等分支"
    assert r2["data"]["my_value"] == wrong, "幂等分支返回的是**既有**结果，不是这次提交的"
    assert r2["data"]["session"]["answered"] == 1, "幂等分支**不重复记账**"

    rows = sql_fetch(
        "SELECT wrong_count, retry_correct FROM wrong_questions WHERE user_id = $1 AND question_id = $2",
        _uid(client, u),
        int(first["question_id"]),
    )
    assert rows and int(rows[0]["wrong_count"]) == 1, f"错题本应记 1 次：{rows}"
    assert int(rows[0]["retry_correct"]) == 0, "幂等分支不该动 retry_correct"

    uqs = sql_fetch(
        "SELECT wrong_count, correct_count FROM user_question_state "
        "WHERE user_id = $1 AND question_id = $2",
        _uid(client, u),
        int(first["question_id"]),
    )
    assert uqs and int(uqs[0]["wrong_count"]) == 1 and int(uqs[0]["correct_count"]) == 0, uqs


def test_answer_repeated_across_sessions_accumulates(client: httpx.Client) -> None:
    """跨 session 再答同一题 ⇒ 计数**累加**（证明 upsert 的 `+1` 真的在加）。

    ⚠️ 两个坑，都是实测踩到的：
      ① 同一个 session 里再答是**幂等分支**（零写入）—— 用同一 session 测"累加"
         会得到一个**永远绿**的假断言；
      ② 抽题是"**未做过的优先**"，所以"先答完 A 再建 B"会让 B 抽到**别的题** ——
         第一版就是这么写的，断言 `correct_count == 2` 红了（实际 1）。
      ⇒ 正解：**先把两个 session 都建好**（那时都还没答过 ⇒ 抽到的是同一道题），
        再分别提交。这样"同一道题"是构造出来的，不依赖运气。
    """
    u = fresh_user(client, nickname="累加用例")
    sa = _session(client, u["access_token"], count=1)
    sb = _session(client, u["access_token"], count=1)
    qa = sa["items"][0]["question_id"]
    qb = sb["items"][0]["question_id"]
    assert qa == qb, "两个 session 都还没答过任何题，应该抽到同一道（确定性抽题）"

    right = _correct_answer(qa)
    for s in (sa, sb):
        b = body(
            client.post(
                f"{API}/practice/sessions/{s['id']}/answer",
                json={"item_id": s["items"][0]["item_id"], "value": right["value"]},
                headers=auth(u["access_token"]),
            )
        )
        assert b["code"] == 0, b
        assert b["data"]["is_correct"] is True, b
        assert b["data"]["idempotent"] is False, "不同 session ⇒ **不是**幂等分支"
    rows = sql_fetch(
        "SELECT correct_count, streak FROM user_question_state WHERE user_id = $1 AND question_id = $2",
        _uid(client, u),
        int(qa),
    )
    assert rows and int(rows[0]["correct_count"]) == 2, f"两次正确的题应累计 2：{rows}"
    assert int(rows[0]["streak"]) == 2, "连续答对应累计 streak"


def test_answer_rejects_on_finished_session(client: httpx.Client) -> None:
    """★ 契约⑤：会话已结束 ⇒ 拒绝再写（`40901`）。已结束的卷面不能再改答案。"""
    u = fresh_user(client, nickname="已结束用例")
    s = _session(client, u["access_token"], count=2)
    sql_exec(
        "UPDATE practice_sessions SET status = 'finished', finished_at = now() WHERE id = $1",
        int(s["id"]),
    )
    it = s["items"][0]
    r = client.post(
        f"{API}/practice/sessions/{s['id']}/answer",
        json={"item_id": it["item_id"], "value": ["A"]},
        headers=auth(u["access_token"]),
    )
    assert r.status_code == 409, f"应 409，实际 {r.status_code}: {r.text[:160]}"
    assert body(r)["code"] == 40901, body(r)


def test_answer_rejects_foreign_item(client: httpx.Client) -> None:
    """item 不属于本次 session ⇒ 404（**按 id 寻址的入口都要自己再拦一次**）。"""
    u = fresh_user(client, nickname="越界 item 用例")
    a = _session(client, u["access_token"], count=1)
    b2 = _session(client, u["access_token"], count=1)
    r = client.post(
        f"{API}/practice/sessions/{a['id']}/answer",
        json={"item_id": b2["items"][0]["item_id"], "value": ["A"]},
        headers=auth(u["access_token"]),
    )
    assert r.status_code == 404, f"应 404，实际 {r.status_code}: {r.text[:160]}"


def test_answer_value_shape_is_validated(client: httpx.Client) -> None:
    """空数组被 schema 拦（422）；题型不符的值被 service 拦（400 + **说清题型**）。"""
    u = fresh_user(client, nickname="取值校验用例")
    s = _session(client, u["access_token"], count=1)
    it = s["items"][0]
    r = client.post(
        f"{API}/practice/sessions/{s['id']}/answer",
        json={"item_id": it["item_id"], "value": []},
        headers=auth(u["access_token"]),
    )
    assert r.status_code == 422, f"空数组应被 schema 拒：{r.status_code}"
    # 题型不符：判断题给标号 / 选择题给 true
    bad = [12345] if it["type"] != "judge" else ["不是布尔"]
    r2 = client.post(
        f"{API}/practice/sessions/{s['id']}/answer",
        json={"item_id": it["item_id"], "value": bad},
        headers=auth(u["access_token"]),
    )
    assert r2.status_code in (400, 422), f"应被拒，实际 {r2.status_code}: {r2.text[:160]}"


def test_create_session_rejects_bad_scope(client: httpx.Client) -> None:
    """未知科目 ⇒ 404；**章节不属于该科目** ⇒ 400 且说清是哪一条（硬约定 C 的同族）。"""
    u = fresh_user(client, nickname="范围校验用例")
    r = client.post(
        f"{API}/practice/sessions",
        json={"subject_id": "999999", "chapter_id": "1"},
        headers=auth(u["access_token"]),
    )
    assert r.status_code == 404 and body(r)["code"] == 40401, body(r)

    # 1101 属于 1001（工程经济）；把它挂到 2001（建筑实务）下 ⇒ 必须拒
    r2 = client.post(
        f"{API}/practice/sessions",
        json={"subject_id": "2001", "chapter_id": str(CHAPTER_ECONOMY_1)},
        headers=auth(u["access_token"]),
    )
    assert r2.status_code == 400, f"应 400，实际 {r2.status_code}: {r2.text[:200]}"
    assert str(CHAPTER_ECONOMY_1) in body(r2)["message"], "拒绝要说清是哪个章节"


def test_create_session_rejects_unknown_chapter(client: httpx.Client) -> None:
    u = fresh_user(client, nickname="坏章节用例")
    r = client.post(
        f"{API}/practice/sessions",
        json={"subject_id": str(SUBJECT_ECONOMY), "chapter_id": "999999"},
        headers=auth(u["access_token"]),
    )
    assert r.status_code == 404 and body(r)["code"] == 40401, body(r)


def test_subject_level_session_without_chapter(client: httpx.Client) -> None:
    """不传 `chapter_id` ⇒ 整科抽题（`config` 里记着 chapter_id=None）。"""
    u = fresh_user(client, nickname="整科抽题用例")
    r = client.post(
        f"{API}/practice/sessions",
        json={"subject_id": str(SUBJECT_ECONOMY), "count": 5},
        headers=auth(u["access_token"]),
    )
    b = body(r)
    assert b["code"] == 0, b
    s = body(
        client.get(f"{API}/practice/sessions/{b['data']['id']}", headers=auth(u["access_token"]))
    )["data"]
    assert s["total"] == 5 and s["chapter_id"] is None
    assert s["title"].endswith("练习") and s["chapter_name"] is None
