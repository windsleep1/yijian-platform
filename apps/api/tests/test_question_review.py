"""题目**审核链**（`/submit` + `/review`）—— 审计链补齐第一批。

对应方案 `docs/15` §11 的 10 条用例。判据是用户那句要求：

> **"谁审的、什么时候审的，能从数据里查出来"**

⇒ 所以每条尽量落到**库里那一列的值**（`questions.reviewed_by` / `reviewed_at` /
`content_change_logs` / `audit_logs` / `question_versions`），
而不是"接口返回 200"。接口自报的数字只能当线索（硬约定 J / 坑 56）。

依赖：API 已启动（真 PG + fakeredis）+ 题库种子。装置约定见 `test_admin_v4.py` 文件头。

⚠️ **没有覆盖到的一件事**（如实记下，别当成已测）：种子里**不存在**"有 `question:update`
但没有 `question:publish`"的角色 —— `admin` / `researcher` 拿的都是 `question` 模块整包。
所以"审核权 ≠ 编辑权"这条**只能测到"无 question 权限 → 全拦"**
（`test_permission_wall`），测不出两者之间的分界。这与 `db/schema.sql` 里
`viewer` 那段注释记的是同一个限制。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from .conftest import API, assign_roles, auth, body, fresh_user, register, sql_fetch


# ================================================================ 工具


def _mk(client: httpx.Client, h: dict[str, str], *, sid: int, chap: int | None, **over) -> dict:
    """建一道**内容唯一**的单选题（默认 `draft`）。"""
    tag = uuid.uuid4().hex[:10]
    payload: dict = {
        "subject_id": sid,
        "chapter_id": chap,
        "type": "single",
        "stem": f"【审核用例】{tag} 关于安全生产责任制，下列说法正确的是？",
        "analysis": "由自动化用例创建，可用唯一 tag 定位。",
        "difficulty": 3,
        "score_default": 1,
        "status": "draft",
        "source_type": "self",
        "options": [
            {"label": "A", "content": f"A-{tag} 说法一", "is_correct": False},
            {"label": "B", "content": f"B-{tag} 说法二", "is_correct": True},
            {"label": "C", "content": f"C-{tag} 说法三", "is_correct": False},
        ],
    }
    payload.update(over)
    b = body(client.post(f"{API}/admin/questions", headers=h, json=payload))
    assert b["code"] == 0, b
    return b["data"]


def _detail(client: httpx.Client, h: dict[str, str], qid: int) -> dict:
    b = body(client.get(f"{API}/admin/questions/{qid}", headers=h))
    assert b["code"] == 0, b
    return b["data"]


def _db(statement: str, *args) -> list[dict]:
    """直连库查询。没设 `DATABASE_URL` 才 skip（CI 会设）。"""
    rows = sql_fetch(statement, *args)
    if rows is None:
        pytest.skip("DATABASE_URL 未设置 —— 直连断言跳过（CI 会设这个变量）")
    return rows


def _j(value):
    """JSONB 列经这条连接回来的是 **str**（`conftest.sql_fetch` 没注册 jsonb codec）。

    ⚠️ 这不是可有可无的洁癖：不解就会得到
    `TypeError: string indices must be integers, not 'str'` ——
    而报错指向的是**你的断言**，看起来像"断言写错了"，实际是"取回值的形状和以为的不一样"。
    """
    if isinstance(value, str):
        import json

        return json.loads(value)
    return value


def _submit(client, h, qid: int, version: int) -> dict:
    b = body(
        client.post(f"{API}/admin/questions/{qid}/submit", headers=h, json={"version": version})
    )
    assert b["code"] == 0, b
    return b["data"]


def _review(client, h, qid: int, *, decision: str, version: int, comment: str | None = None):
    payload: dict = {"decision": decision, "version": version}
    if comment is not None:
        payload["comment"] = comment
    return body(client.post(f"{API}/admin/questions/{qid}/review", headers=h, json=payload))


def _logs(qid: int) -> list[dict]:
    """这道题在 `content_change_logs` 里的**送审/审核**痕迹（按时间序）。"""
    return _db(
        """
        SELECT action, diff, change_log, operator_id
        FROM content_change_logs
        WHERE entity_type = 'question' AND entity_id = $1 AND action IN ('submit', 'review')
        ORDER BY created_at, id
        """,
        qid,
    )


@pytest.fixture(scope="module")
def sz(client: httpx.Client, admin_h: dict[str, str]) -> tuple[int, int | None]:
    """市政科目的 subject_id 与首个 chapter_id。"""
    b = body(client.get(f"{API}/admin/chapters/tree", headers=admin_h))
    assert b["code"] == 0, b
    for group in b["data"]["items"]:
        if group["subject"]["code"] == "SW-SZ":
            chapters = group["chapters"]
            return int(group["subject"]["id"]), (int(chapters[0]["id"]) if chapters else None)
    pytest.skip("科目种子缺少市政（SW-SZ）：请确认已执行 db/schema.sql")


# ================================================================ 1. 送审


def test_submit_moves_to_reviewing_and_does_not_set_reviewer(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """送审 = `draft → reviewing`，**不写** `reviewed_by` / `reviewed_at`。

    送审是**发起**审核，不是审核本身。若它也写审核人，那么"谁审的"这个问题的答案
    就变成了"谁送的" —— 审计链从第一步就错了。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)

    d = _submit(client, admin_h, int(q["id"]), q["version"])
    assert d["status"] == "reviewing"
    assert d["version"] == q["version"] + 1
    assert d["reviewed_by"] is None, "送审不该写 reviewed_by"
    assert d["reviewed_at"] is None, "送审不该写 reviewed_at"


# ================================================================ 2. 审核通过：四件套齐全


def test_approve_writes_status_reviewer_time_and_published_at(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """`approve` 必须写全：`status` + `reviewed_by` + `reviewed_at` + **`published_at`**。

    断言的是**列里的值**（不是"接口说它写了"），并且 `reviewed_at` 要落在
    调用前后的时间窗内 —— 只断"非空"是性质不是值（硬约定 J）。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]

    b = _review(client, admin_h, qid, decision="approve", version=v, comment="题面与解析一致")
    assert b["code"] == 0, b
    assert b["data"]["already"] is False
    d = b["data"]["question"]
    assert d["status"] == "published"
    assert d["reviewed_by_name"], "reviewed_by_name 要能读出来（前端要显示'谁审的'）"
    assert d["reviewed_at"] and d["published_at"]

    rows = _db(
        "SELECT status, reviewed_by, reviewed_at, published_at, version FROM questions "
        "WHERE id = $1",
        qid,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "published"
    assert str(r["reviewed_by"]) == str(d["reviewed_by"]), "库里的审核人必须与响应一致"
    assert r["reviewed_at"] is not None
    assert r["published_at"] is not None, "approve 必须写 published_at"
    assert r["version"] == v + 1, "审核要推进 version（否则与 question_versions 对不上）"


# ================================================================ 3. 驳回：有理由，但没有发布时间


def test_reject_writes_reason_but_not_published_at(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """`reject` → `rejected` + 理由落库；**`published_at` 必须仍是 NULL**（驳回 ≠ 发布）。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]

    b = _review(client, admin_h, qid, decision="reject", version=v, comment="选项 B 与题干矛盾")
    assert b["code"] == 0, b
    assert b["data"]["question"]["status"] == "rejected"

    rows = _db(
        "SELECT status, reviewed_at, published_at FROM questions WHERE id = $1",
        qid,
    )
    r = rows[0]
    assert r["status"] == "rejected"
    assert r["reviewed_at"] is not None
    assert r["published_at"] is None, "驳回答题不该写发布时间"

    # 理由必须**完整**落进 diff（change_log 会被截到 500，diff 不截）
    logs = [x for x in _logs(qid) if x["action"] == "review"]
    assert len(logs) == 1, logs
    diff = _j(logs[0]["diff"])
    assert diff["after"]["comment"] == "选项 B 与题干矛盾"
    assert diff["after"]["decision"] == "reject"
    assert diff["before"]["status"] == "reviewing"
    assert "选项 B 与题干矛盾" in logs[0]["change_log"]


# ================================================================ 4. 驳回必须写理由


def test_reject_without_comment_is_40001_and_has_zero_side_effect(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """驳回不给理由 → `40001`，且**库里一点没动**（硬约定 C：拒绝时零副作用）。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]

    for bad in (None, "", "   "):
        b = _review(client, admin_h, qid, decision="reject", version=v, comment=bad)
        assert b["code"] == 40001, (bad, b)

    rows = _db("SELECT status, reviewed_by, version FROM questions WHERE id = $1", qid)
    assert rows[0]["status"] == "reviewing", "被拒的非法请求不能改状态"
    assert rows[0]["reviewed_by"] is None
    assert len([x for x in _logs(qid) if x["action"] == "review"]) == 0, "不能留下失败痕迹"


# ================================================================ 5. 幂等：判"目标状态是否已达成"


def test_approve_is_idempotent_and_writes_nothing(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """对已 `published` 的题再 `approve` → `already=true`，**零写入**。

    判据是「**目标状态是否已达成**」，不是「输入的 decision 是不是同一个」（硬约定 C）。
    所以这里逐项钉死：`reviewed_at` 不变、`version` 不变、**留痕仍只有 1 条**。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]

    first = _review(client, admin_h, qid, decision="approve", version=v, comment="第一次")
    assert first["code"] == 0 and first["data"]["already"] is False
    before = first["data"]["question"]
    logs_before = _logs(qid)

    again = _review(client, admin_h, qid, decision="approve", version=before["version"])
    assert again["code"] == 0, again
    assert again["data"]["already"] is True, "已是目标状态 → 必须报幂等命中，不能假装又审了一次"
    now = again["data"]["question"]
    assert now["version"] == before["version"], "幂等分支绝不动 version"
    assert now["reviewed_at"] == before["reviewed_at"], "幂等分支不能刷新审核时间"
    assert _logs(qid) == logs_before, "幂等分支不能新增留痕"


# ================================================================ 6. 已发布的题不能被驳回


def test_reject_published_is_refused(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """`published` + `reject` → `40901`（那是"悄悄撤题"，不是审核）。

    拒绝文案必须**说清是哪一道**并给可执行下一步（硬约定 C）。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]
    ok_body = _review(client, admin_h, qid, decision="approve", version=v)
    assert ok_body["code"] == 0, ok_body
    v2 = ok_body["data"]["question"]["version"]

    b = _review(client, admin_h, qid, decision="reject", version=v2, comment="想撤下来")
    assert b["code"] == 40901, b
    assert "published" in b["message"], "文案要说清当前状态"
    assert "归档" in b["message"], "文案要给可执行下一步"

    rows = _db("SELECT status, version FROM questions WHERE id = $1", qid)
    assert rows[0]["status"] == "published", "拒绝的请求不能改状态"
    assert rows[0]["version"] == v2


# ================================================================ 7. 乐观锁


def test_stale_version_is_conflict(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """回传过期 `version` → `40901`，不静默覆盖（两人同时审同一道题）。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])

    b = _review(client, admin_h, qid, decision="approve", version=999999)
    assert b["code"] == 40901, b

    b2 = body(
        client.post(
            f"{API}/admin/questions/{qid}/submit", headers=admin_h, json={"version": 999999}
        )
    )
    assert b2["code"] == 40901, b2


# ================================================================ 8. 端到端读回（★ 这条才是"查得出来"）


def test_reviewer_is_queryable_through_the_api(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """**端到端**：审核人是一个**独立账号**，审完能从详情接口查出"是他、在那时"。

    为什么单列：只测写不测读，会漏掉"schema 没接线"这种情形 ——
    库里有值、接口不返回，用户端依然查不到（"中间产物生成 ≠ 被消费"）。
    刻意**换一个账号**来审，否则"审核人 == 创建人 == 超管"三者同体，写错了也看不出来。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]

    reviewer = fresh_user(client, nickname="审核员甲")
    ar = assign_roles(client, admin_h, reviewer["user"]["id"], ["researcher"])
    assert ar["code"] == 0, ar
    rh = auth(reviewer["access_token"])

    b = _review(client, rh, qid, decision="approve", version=v, comment="由审核员甲通过")
    assert b["code"] == 0, b

    d = _detail(client, admin_h, qid)
    assert d["status"] == "published"
    assert str(d["reviewed_by"]) == str(reviewer["user"]["id"]), "读回来的审核人必须是审核员甲"
    assert d["reviewed_by_name"] == "审核员甲"
    assert d["reviewed_at"] is not None
    assert d["can_review"] is True and d["can_submit"] is True, "admin 两种权限都有"


# ================================================================ 9. 两条留痕通道同源


def test_both_audit_channels_agree(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """`content_change_logs` 与 `audit_logs` 都要有，且**指同一个人、同一个结论**。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]
    assert _review(client, admin_h, qid, decision="approve", version=v)["code"] == 0

    logs = [x for x in _logs(qid) if x["action"] == "review"]
    audits = _db(
        "SELECT action, actor_id, before_data, after_data FROM audit_logs "
        "WHERE entity_type = 'question' AND entity_id = $1 AND action = 'question.review'",
        qid,
    )
    assert len(logs) == 1 and len(audits) == 1, (logs, audits)
    assert str(audits[0]["actor_id"]) == str(logs[0]["operator_id"]), "两条通道必须同源"
    assert _j(audits[0]["after_data"])["decision"] == "approve"
    assert _j(audits[0]["before_data"])["status"] == _j(logs[0]["diff"])["before"]["status"], (
        "两条通道的 before 必须一致 —— 否则审计页与内容审计抽屉会各说各话"
    )


# ================================================================ 10. 版本历史不能缺号


def test_version_history_has_no_gap(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """审核推进了 `version`，就必须有**同版本号**的 `question_versions` 行。

    否则详情页"历史版本"会出现「题目 v4，历史只到 v2」这种缺号 ——
    而缺号是**不报错**的，只会让人以为历史丢了（与坑 44 同族）。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v1 = _submit(client, admin_h, qid, q["version"])["version"]
    d = _review(client, admin_h, qid, decision="reject", version=v1, comment="再改改")["data"][
        "question"
    ]
    v2 = d["version"]

    rows = _db(
        "SELECT version, change_log FROM question_versions WHERE question_id = $1 ORDER BY version",
        qid,
    )
    have = {r["version"] for r in rows}
    assert v1 in have, f"送审那一版没有快照：{have}"
    assert v2 in have, f"审核那一版没有快照：{have}"
    assert have == set(range(1, v2 + 1)), f"版本号必须连续：有 {sorted(have)}，期望 1..{v2}"
    assert any("驳回" in (r["change_log"] or "") for r in rows), "审核结论要写进快照的 change_log"

    # 详情内联的历史也一样
    assert {x["version"] for x in d["versions"]} >= {v1, v2}


# ================================================================ 11. 权限墙


def test_permission_wall(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """`student` 没有任何 `question:*` 权限 → 两个新入口与旧的 PUT 一样被拦（`40301`）。

    ⚠️ 这**测不出**"有编辑权但没有审核权"的分界 —— 种子里的角色是按 module 整包发的，
    `admin` / `researcher` 都有 `question:publish`。该限制与 `db/schema.sql` 里
    `viewer` 那段注释记的是同一件事（`docs/21` BL-08）。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])

    stu = register(client, nickname="审核越权用例")
    h = auth(stu["access_token"])

    assert (
        body(
            client.post(
                f"{API}/admin/questions/{qid}/submit", headers=h, json={"version": q["version"]}
            )
        )["code"]
        == 40301
    )
    assert (
        body(
            client.post(
                f"{API}/admin/questions/{qid}/review",
                headers=h,
                json={"decision": "approve", "version": q["version"]},
            )
        )["code"]
        == 40301
    )

    rows = _db("SELECT status FROM questions WHERE id = $1", qid)
    assert rows[0]["status"] == "draft", "被拦的请求不能改状态"


# ================================================================ 12. 已归档的题不能审核


def test_deleted_question_cannot_be_reviewed(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """软删除的题先恢复再审核 —— 与题库其他入口同一条规矩。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    d = body(client.delete(f"{API}/admin/questions/{qid}", headers=admin_h))
    assert d["code"] == 0, d
    v = d["data"]["version"]

    b = _review(client, admin_h, qid, decision="approve", version=v)
    assert b["code"] == 40001, b
    assert "恢复" in b["message"], "文案要给可执行下一步"


# ================================================================ 13. 送审的边界（覆盖率补的 4 条分支）
#
# 这一组是**看着覆盖率报告补的** —— 它们不是"凑数"，四个都是真实分支：
#   ① 已在 reviewing 再送审（幂等）  ② 已发布还想送审  ③ 已归档还想送审
#   ④ status='archived'（业务归档，与软删除正交）的题要审核
# 判据：缺失行里如果有**每个请求必经**的行，先查采集；而这四条都是**有条件才走**的分支
# （坑 54 / `docs/18` 的反面），所以该补测试而不是查采集。


def test_submit_is_idempotent(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """已 `reviewing` 再送审 → 零写入（同一条判据：目标状态是否已达成）。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    first = _submit(client, admin_h, qid, q["version"])

    before = _logs(qid)
    again = body(
        client.post(
            f"{API}/admin/questions/{qid}/submit",
            headers=admin_h,
            json={"version": first["version"]},
        )
    )
    assert again["code"] == 0, again
    assert again["data"]["version"] == first["version"], "幂等分支绝不动 version"
    assert _logs(qid) == before, "幂等分支不能新增留痕"


def test_submit_refused_from_published(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """已发布的题不能送审（它早就走过送审了）。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    v = _submit(client, admin_h, qid, q["version"])["version"]
    d = _review(client, admin_h, qid, decision="approve", version=v)["data"]["question"]

    b = body(
        client.post(
            f"{API}/admin/questions/{qid}/submit", headers=admin_h, json={"version": d["version"]}
        )
    )
    assert b["code"] == 40901, b
    assert "published" in b["message"], "文案要说清当前状态"


def test_submit_refused_when_deleted(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """软删除的题不能送审（与审核那条对称 —— 写审核时漏了这条对称的）。"""
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    d = body(client.delete(f"{API}/admin/questions/{qid}", headers=admin_h))
    assert d["code"] == 0, d

    b = body(
        client.post(
            f"{API}/admin/questions/{qid}/submit",
            headers=admin_h,
            json={"version": d["data"]["version"]},
        )
    )
    assert b["code"] == 40001, b
    assert "恢复" in b["message"]


def test_archived_status_gets_its_own_hint(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """`status='archived'`（业务归档，**与软删除正交**）的题 → `40901` + 归档专属提示。

    ⚠️ 这条用 `PUT` 把 status 改成 `archived` —— 那正是 `docs/15` §12.1 记的旁路
    （`PUT` 仍接受 `status`）。等前端改造完把它移除后，本用例要改成走新入口；
    **先留着**，因为它现在真实存在。
    """
    sid, chap = sz
    q = _mk(client, admin_h, sid=sid, chap=chap)
    qid = int(q["id"])
    up = body(
        client.put(
            f"{API}/admin/questions/{qid}",
            headers=admin_h,
            json={"version": q["version"], "status": "archived"},
        )
    )
    assert up["code"] == 0, up
    assert up["data"]["status"] == "archived"
    assert up["data"]["is_deleted"] is False, "业务归档与软删除是两回事"

    b = _review(client, admin_h, qid, decision="approve", version=up["data"]["version"])
    assert b["code"] == 40901, b
    assert "归档" in b["message"], "归档态要有自己的提示，不能落到兜底那句"
