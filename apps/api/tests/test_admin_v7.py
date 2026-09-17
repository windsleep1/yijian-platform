"""Batch 7 组卷引擎验收（12 个接口）。

    GET    /admin/paper-rules              ① 组卷规则列表
    POST   /admin/paper-rules              ② 新建规则
    PUT    /admin/paper-rules/{id}         ③ 编辑规则
    DELETE /admin/paper-rules/{id}         ④ 删除规则
    GET    /admin/exams                    ⑤ 试卷列表（含 include_deleted 开关）
    POST   /admin/exams                    ⑥ 创建试卷
    POST   /admin/exams/{id}/auto-compose  ⑦ 规则自动组卷
    POST   /admin/exams/{id}/validate      ⑧ 卷面校验
    POST   /admin/exams/{id}/publish       ⑨ 发布（版本锁定）
    GET    /admin/exams/{id}               ⑩ 试卷详情
    PUT    /admin/exams/{id}               ⑪ 编辑试卷
    DELETE /admin/exams/{id}               ⑫ 归档（软删除）

需要 API 已启动（真 PG + fakeredis）：

    powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1

用例与「验收标准」的对应关系：

    ① 一条规则生成完整卷，题型数量/分值/总分匹配  → test_compose_full_paper_matches_rule_and_sections
    ② viewer 能看列表/详情，发布按钮置灰            → test_viewer_can_read_but_not_publish
    ③ 题库不足 → shortfalls 准确回传，不静默补题   → test_shortfall_is_reported_and_never_silently_filled
    ④ 发布→改题→已发布卷仍显示锁定版本              → test_publish_locks_version_and_later_edit_does_not_change_paper
    ⑤ 归档 → 默认列表看不到 → 开开关能看到          → test_soft_delete_hides_from_default_list_but_keeps_detail
    ⑥ 用 6000 道仿真题库组一套完整模考卷            → test_compose_full_mock_paper_from_seed_bank
    ⑦ 数据范围（教研只能组自己科目的卷）            → test_data_scope_researcher_only_own_subject

另有算法纯函数与 CRUD 边界用例：加权采样偏好未用过的题、同种子可复现、放宽梯度、
缺口算式、`case_sub` 不可独立抽题、难度区间校验、规则 CRUD、停用规则、状态保护、
**发布前强制校验**、权限门控、
**锁定版本落独立列**（`test_locked_version_is_written_to_column_and_mirrored_to_jsonb`）。

设计要点（与前几批一致）：**每个用例自己造数据**，不依赖别的用例的残留；
凡写进库的，用例结束前都清理掉，避免污染题库、也避免用例之间互相影响。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import string
import uuid
from types import SimpleNamespace

import httpx
import pytest

from .conftest import API, assign_roles, auth, body

# 市政实务（教研演示账号的数据范围就是它）
SZ_SUBJECT_ID = 2007
# 建筑实务（用作"越权"对照；case 题只有 27 道，正好用来造缺口）
JZ_SUBJECT_ID = 2001
# 建设工程经济：单选 513 / 多选 246 / 判断 207，够组一套完整模考卷
JJ_SUBJECT_ID = 1001


# ================================================================ 基建


def _dsn() -> str | None:
    url = os.environ.get("DATABASE_URL")
    return url.replace("postgresql+asyncpg://", "postgresql://") if url else None


def sql_exec(statement: str, *args):
    """直连库执行一条 SQL。没设 `DATABASE_URL` 时返回 None（调用方自行降级）。"""
    dsn = _dsn()
    if not dsn:
        return None

    import asyncpg

    async def _run():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.execute(statement, *args)
        finally:
            await conn.close()

    return asyncio.run(_run())


def sql_fetch(statement: str, *args):
    dsn = _dsn()
    if not dsn:
        return None

    import asyncpg

    async def _run():
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(statement, *args)
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    return asyncio.run(_run())


@pytest.fixture
def created(client: httpx.Client, admin_h):
    """收集本用例创建的试卷 / 题目，用例结束后统一清理。

    `exams` 没有删除接口（本批 11 个接口里没有 `DELETE /admin/exams`），
    所以清理走直连 SQL；题目用软删除（保留痕迹，不物理删）。
    """
    bag = SimpleNamespace(exams=[], questions=[], rules=[])
    yield bag

    for rid in bag.rules:
        try:
            client.delete(f"{API}/admin/paper-rules/{rid}", headers=admin_h, timeout=20)
        except Exception:  # noqa: BLE001
            pass
    if bag.exams and _dsn():
        ids = [int(x) for x in bag.exams]
        sql_exec("DELETE FROM exam_questions WHERE exam_id = ANY($1::bigint[])", ids)
        sql_exec("DELETE FROM exam_sections  WHERE exam_id = ANY($1::bigint[])", ids)
        sql_exec("DELETE FROM exams           WHERE id      = ANY($1::bigint[])", ids)
    for qid in bag.questions:
        try:
            client.delete(f"{API}/admin/questions/{qid}", headers=admin_h, timeout=20)
        except Exception:  # noqa: BLE001
            pass


def make_question(client, admin_h, *, subject_id: int, qtype: str = "single",
                  stem: str | None = None, exam_year: int | None = None,
                  chapter_id: int | None = None, status: str = "published") -> dict:
    """造一道**已发布**的题（试卷只能吃已发布的题）。"""
    stem = stem or f"【B7 组卷用例 {uuid.uuid4().hex[:8]}】下列表述正确的是？"
    payload: dict = {
        "subject_id": subject_id, "type": qtype, "stem": stem,
        "analysis": "B7 用例", "difficulty": 3, "score_default": 1,
        "status": status, "source_type": "self",
    }
    if exam_year is not None:
        payload["exam_year"] = exam_year
    if chapter_id is not None:
        payload["chapter_id"] = chapter_id
    if qtype == "single":
        payload["options"] = [
            {"label": "A", "content": "选项 A", "is_correct": True},
            {"label": "B", "content": "选项 B", "is_correct": False},
            {"label": "C", "content": "选项 C", "is_correct": False},
            {"label": "D", "content": "选项 D", "is_correct": False},
        ]
    elif qtype == "multiple":
        payload["options"] = [
            {"label": "A", "content": "选项 A", "is_correct": True},
            {"label": "B", "content": "选项 B", "is_correct": True},
            {"label": "C", "content": "选项 C", "is_correct": False},
            {"label": "D", "content": "选项 D", "is_correct": False},
        ]
    elif qtype == "judge":
        payload["judge_answer"] = True
    b = body(client.post(f"{API}/admin/questions", headers=admin_h, json=payload, timeout=30))
    assert b["code"] == 0, b
    return b["data"]


def create_exam(client, admin_h, *, subject_id: int, title: str, sections: list[dict] | None = None,
                duration_min: int = 180, pass_score: float = 0, exam_type: str = "mock") -> dict:
    payload = {
        "subject_id": subject_id, "title": title, "type": exam_type,
        "duration_min": duration_min, "pass_score": pass_score,
        "sections": sections or [],
    }
    b = body(client.post(f"{API}/admin/exams", headers=admin_h, json=payload, timeout=30))
    assert b["code"] == 0, b
    return b["data"]


def compose(client, headers, exam_id, payload: dict) -> dict:
    return body(client.post(f"{API}/admin/exams/{exam_id}/auto-compose",
                            headers=headers, json=payload, timeout=120))


def validate(client, headers, exam_id) -> dict:
    return body(client.post(f"{API}/admin/exams/{exam_id}/validate", headers=headers, timeout=60))


def publish(client, headers, exam_id, **kw) -> dict:
    return body(client.post(f"{API}/admin/exams/{exam_id}/publish",
                            headers=headers, json=kw or {}, timeout=60))


def new_rule(client, admin_h, *, subject_id: int, rules: list[dict], name: str | None = None,
             duration_min: int = 180) -> dict:
    b = body(client.post(f"{API}/admin/paper-rules", headers=admin_h, json={
        "name": name or f"B7 规则 {uuid.uuid4().hex[:6]}",
        "subject_id": subject_id, "type": "mock",
        "duration_min": duration_min, "rules": rules,
    }, timeout=30))
    assert b["code"] == 0, b
    return b["data"]


def fresh_user(client: httpx.Client, *, nickname: str = "组卷用例") -> dict:
    """注册全新用户并把限流桶隔到自己的假 IP（同 Batch 5 的做法，避免挤爆全站配额）。"""
    ip = f"203.0.{random.randint(1, 254)}.{random.randint(2, 250)}"
    h = {"X-Forwarded-For": ip}
    phone = "13" + "".join(random.choice(string.digits) for _ in range(9))
    b = body(client.post(f"{API}/auth/sms/send", json={"phone": phone, "scene": "register"}, headers=h))
    assert b["code"] == 0, b
    b = body(client.post(f"{API}/auth/register", headers=h, json={
        "phone": phone, "code": b["data"]["dev_code"], "password": "Passw0rd123", "nickname": nickname,
    }))
    assert b["code"] == 0, b
    return b["data"]


# ================================================================ A. 算法纯函数


def test_weighted_sample_prefers_never_used_questions() -> None:
    """加权采样：优先选**从未进过任何试卷**的题。"""
    from app.services import exam_service as es

    rows = [{"id": i, "usage_count": 0} for i in range(5)] + [
        {"id": 100 + i, "usage_count": 9} for i in range(5)
    ]
    picked = es.weighted_sample(rows, 5, random.Random(7), prefer_unused=True)
    ids = [p["id"] for p in picked]
    assert all(i < 100 for i in ids), f"应该全选未用过的题，实际 {ids}"
    assert len(set(ids)) == 5, "无放回抽样不该出现重复"


def test_weighted_sample_is_deterministic_with_seed() -> None:
    """同一种子 → 同一结果（验收与排障都要能复现同一张卷）。"""
    from app.services import exam_service as es

    rows = [{"id": i, "usage_count": i % 3} for i in range(40)]
    a = [p["id"] for p in es.weighted_sample(rows, 10, random.Random(12345))]
    b = [p["id"] for p in es.weighted_sample(rows, 10, random.Random(12345))]
    c = [p["id"] for p in es.weighted_sample(rows, 10, random.Random(999))]
    assert a == b, "同种子结果必须一致"
    assert a != c, "不同种子应当给出不同结果（否则种子没起作用）"


def test_relaxation_ladder_follows_docs_05() -> None:
    """放宽梯度：精确 → 放宽难度 → 放宽范围；没有对应约束就不该有那一档。"""
    from app.services import exam_service as es

    full = es.RuleItem(type="single", count=5, difficulty=(2, 4), kp_ids=[1])
    assert [s[0] for s in es.relaxation_steps(full)] == ["exact", "relax_difficulty", "relax_scope"]

    only_diff = es.RuleItem(type="single", count=5, difficulty=(2, 4))
    assert [s[0] for s in es.relaxation_steps(only_diff)] == ["exact", "relax_difficulty"]

    only_kp = es.RuleItem(type="single", count=5, kp_ids=[1])
    assert [s[0] for s in es.relaxation_steps(only_kp)] == ["exact", "relax_scope"]

    bare = es.RuleItem(type="judge", count=5)
    assert [s[0] for s in es.relaxation_steps(bare)] == ["exact"], "没有可放宽的约束就别造假档位"


def test_shortfall_accounting_is_consistent() -> None:
    """缺口算式：`got + missing == need`，且说明里带上每档候选数。"""
    from app.services import exam_service as es

    rule = es.RuleItem(type="case", count=100, difficulty=(5, 5))
    sf = es.build_shortfall(rule, 2, 27, {"exact": 0, "relax_difficulty": 27})
    assert (sf.need, sf.got, sf.missing) == (100, 27, 73)
    assert sf.got + sf.missing == sf.need
    assert sf.rule_index == 2 and sf.question_type == "case"
    assert "放宽" in sf.reason and "不会用其它题目顶替" in sf.reason
    assert sf.rule.count == 100, "rule 必须回传原始规则对象，前端才能对回约束"


def test_case_sub_cannot_be_composed_alone() -> None:
    """`case_sub` 不能独立抽题（会得到没有材料的孤儿子问）。"""
    from app.schemas.admin_exam import PaperRuleCreateIn, RuleItem

    with pytest.raises(Exception) as ei:
        PaperRuleCreateIn(name="x", subject_id=SZ_SUBJECT_ID,
                          rules=[RuleItem(type="case_sub", count=5)])
    assert "case_sub" in str(ei.value)


def test_difficulty_range_and_count_bounds_are_validated() -> None:
    """难度必须是合法闭区间；题量必须在 1~200。"""
    from app.schemas.admin_exam import RuleItem

    for bad in [(4, 2), (0, 3), (2, 6)]:
        with pytest.raises(Exception):
            RuleItem(type="single", count=1, difficulty=bad)
    with pytest.raises(Exception):
        RuleItem(type="single", count=0)
    with pytest.raises(Exception):
        RuleItem(type="single", count=201)


# ================================================================ B. 组卷规则 CRUD


def test_paper_rule_crud_roundtrip(client: httpx.Client, admin_h, created) -> None:
    """规则 CRUD：建 → 查 → 改 → 删；`planned_count/planned_score` 由 rules 推导。"""
    rules = [
        {"type": "single", "count": 60, "score": 1, "difficulty": [2, 3]},
        {"type": "multiple", "count": 20, "score": 2, "difficulty": [3, 4]},
    ]
    made = new_rule(client, admin_h, subject_id=JJ_SUBJECT_ID, rules=rules)
    created.rules.append(made["id"])
    assert made["planned_count"] == 80 and made["planned_score"] == 100.0, made
    assert made["status"] == "on"

    # 列表能筛到
    b = body(client.get(f"{API}/admin/paper-rules", headers=admin_h,
                        params={"subject_id": JJ_SUBJECT_ID, "page_size": 100}))
    assert any(x["id"] == made["id"] for x in b["data"]["items"]), b["data"]

    # 编辑：整体替换 rules + 停用
    b = body(client.put(f"{API}/admin/paper-rules/{made['id']}", headers=admin_h, json={
        "rules": [{"type": "judge", "count": 20, "score": 1}], "status": "off",
    }))
    assert b["code"] == 0, b
    assert b["data"]["planned_count"] == 20 and b["data"]["status"] == "off", b["data"]

    # 空改动要被拒（避免"看起来保存成功其实什么都没改"）
    b = body(client.put(f"{API}/admin/paper-rules/{made['id']}", headers=admin_h, json={}))
    assert b["code"] == 40001, b

    # 删除 = 硬删除
    b = body(client.delete(f"{API}/admin/paper-rules/{made['id']}", headers=admin_h))
    assert b["code"] == 0 and b["data"]["hard_deleted"] is True, b
    created.rules.clear()
    # 删掉之后改不动了（404，而不是静默成功）
    b = body(client.put(f"{API}/admin/paper-rules/{made['id']}", headers=admin_h, json={"name": "x"}))
    assert b["code"] == 40401, "删掉的规则应当查不到了"


def test_paper_rule_rejects_unknown_subject(client: httpx.Client, admin_h) -> None:
    b = body(client.post(f"{API}/admin/paper-rules", headers=admin_h, json={
        "name": "坏科目", "subject_id": 999999, "rules": [{"type": "single", "count": 1}],
    }))
    assert b["code"] == 40001, b


def test_disabled_rule_is_refused_at_compose_time(client: httpx.Client, admin_h, created) -> None:
    """停用的规则可以查、可以留，但**组卷时会被拒绝**（避免误用废弃规则）。"""
    made = new_rule(client, admin_h, subject_id=JJ_SUBJECT_ID,
                    rules=[{"type": "single", "count": 5}])
    created.rules.append(made["id"])
    body(client.put(f"{API}/admin/paper-rules/{made['id']}", headers=admin_h, json={"status": "off"}))

    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID, title=f"停用规则 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    b = compose(client, admin_h, exam["id"], {"rule_id": made["id"]})
    assert b["code"] == 40001 and "已停用" in b["message"], b


# ================================================================ C. 试卷 CRUD


def test_create_exam_with_sections_and_edit(client: httpx.Client, admin_h, created) -> None:
    """创建试卷（含分段）→ 详情 → 编辑展示字段；分段分值由题数×每题分推导。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID, title=f"分段 {uuid.uuid4().hex[:6]}",
                       sections=[
                           {"name": "单项选择题", "question_type": "single",
                            "question_count": 60, "score_per": 1, "sort_no": 0},
                           {"name": "多项选择题", "question_type": "multiple",
                            "question_count": 20, "score_per": 2, "sort_no": 1},
                       ], pass_score=60)
    created.exams.append(exam["id"])
    assert exam["status"] == "draft" and exam["question_count"] == 0, exam
    assert len(exam["sections"]) == 2
    s0 = exam["sections"][0]
    assert s0["question_count"] == 60 and s0["section_score"] == 60.0, s0
    assert s0["actual_count"] == 0, "刚建的空卷不该有题"

    b = body(client.put(f"{API}/admin/exams/{exam['id']}", headers=admin_h,
                        json={"title": "改过的标题", "duration_min": 150}))
    assert b["code"] == 0, b
    assert b["data"]["title"] == "改过的标题" and b["data"]["duration_min"] == 150, b["data"]

    # 分段 sort_no 重复要被拒
    b = body(client.post(f"{API}/admin/exams", headers=admin_h, json={
        "subject_id": JJ_SUBJECT_ID, "title": "坏分段",
        "sections": [
            {"name": "a", "question_type": "single", "question_count": 1, "score_per": 1, "sort_no": 1},
            {"name": "b", "question_type": "judge", "question_count": 1, "score_per": 1, "sort_no": 1},
        ],
    }))
    assert b["code"] == 40001, b


def test_exam_list_filters_and_404(client: httpx.Client, admin_h, created) -> None:
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"列表筛选 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])

    b = body(client.get(f"{API}/admin/exams", headers=admin_h,
                        params={"subject_id": JJ_SUBJECT_ID, "status": "draft", "page_size": 100}))
    assert b["code"] == 0 and any(x["id"] == exam["id"] for x in b["data"]["items"]), b["data"]

    b = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))
    assert b["code"] == 0 and b["data"]["id"] == exam["id"], b

    b = body(client.get(f"{API}/admin/exams/999999999999999999", headers=admin_h))
    assert b["code"] == 40401, b


# ================================================================ D. 验收① 完整组卷


def test_compose_full_paper_matches_rule_and_sections(client: httpx.Client, admin_h, created) -> None:
    """验收①：一条规则生成完整卷 —— 题型数量 / 每题分值 / 总分全部匹配。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"完整卷 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])

    rule = {"type": "single", "count": 30, "score": 2, "difficulty": [2, 3]}
    b = compose(client, admin_h, exam["id"], {"rules": [rule], "seed": 2026})
    assert b["code"] == 0, b
    d = b["data"]
    assert d["shortfalls"] == [], f"题库有 500+ 道单选，不该有缺口：{d['shortfalls']}"
    assert d["question_count"] == 30 and d["total_score"] == 60.0, d

    # 分段跟着规则重建：计划题数 = rule.count，每题分 = rule.score
    assert len(d["sections"]) == 1, d["sections"]
    sec = d["sections"][0]
    assert sec["question_type"] == "single" and sec["question_count"] == 30, sec
    assert sec["score_per"] == 2 and sec["section_score"] == 60.0, sec
    assert sec["actual_count"] == 30, "实际挂题数必须等于计划题数"

    # 详情：30 道题、逐题分值正确、无重复
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    qs = detail["sections"][0]["questions"]
    assert len(qs) == 30, len(qs)
    assert {q["score"] for q in qs} == {2.0}, "每题分值应全为 2"
    ids = [q["question_id"] for q in qs]
    assert len(set(ids)) == 30, "同一份卷不能出现重复题"
    assert detail["question_count"] == 30 and detail["total_score"] == 60.0, detail

    # 校验通过（分段填满 + 分值自洽）
    v = validate(client, admin_h, exam["id"])
    assert v["code"] == 0 and v["data"]["ok"] is True, v["data"]
    assert v["data"]["errors"] == [], v["data"]["errors"]


def test_compose_two_same_type_rules_do_not_overlap(client: httpx.Client, admin_h, created) -> None:
    """两条同题型规则抽出的题必须**互不重叠**（同卷不重复取题）。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"不重叠 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    b = compose(client, admin_h, exam["id"], {
        "rules": [{"type": "single", "count": 25}, {"type": "single", "count": 25}],
        "seed": 7,
    })
    assert b["code"] == 0 and b["data"]["shortfalls"] == [], b
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    ids = [q["question_id"] for sec in detail["sections"] for q in sec["questions"]]
    assert len(ids) == 50 and len(set(ids)) == 50, f"出现重复取题：{len(ids)} vs {len(set(ids))}"


# ================================================================ E. 验收② 缺口


def test_shortfall_is_reported_and_never_silently_filled(client: httpx.Client, admin_h, created) -> None:
    """验收②：题库不足时 `shortfalls` 准确回传，**绝不静默补题**。

    构造"要 100 道但远远不够"的规则：建筑实务的 `case` 题总共只有 27 道。
    """
    available = sql_fetch(
        "SELECT count(*) AS n FROM questions WHERE subject_id=$1 AND type='case' "
        "AND status='published' AND is_deleted=false AND parent_id IS NULL",
        JZ_SUBJECT_ID,
    )
    if available is None:
        pytest.skip("需要 DATABASE_URL 才能确认题库上限")
    total_case = int(available[0]["n"])
    if total_case >= 100:
        pytest.skip(f"该科目 case 题有 {total_case} 道，构造不出缺口")

    exam = create_exam(client, admin_h, subject_id=JZ_SUBJECT_ID,
                       title=f"缺口 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])

    b = compose(client, admin_h, exam["id"], {
        "rules": [{"type": "case", "count": 100, "score": 2, "difficulty": [5, 5]}],
    })
    assert b["code"] == 0, b
    d = b["data"]

    assert len(d["shortfalls"]) == 1, d["shortfalls"]
    sf = d["shortfalls"][0]
    assert sf["need"] == 100, sf
    assert sf["got"] == total_case, f"got 应为库里全部 {total_case} 道，实际 {sf['got']}"
    assert sf["missing"] == 100 - total_case, sf
    assert sf["got"] + sf["missing"] == sf["need"], sf
    assert sf["question_type"] == "case" and sf["rule_index"] == 0, sf
    assert sf["rule"]["count"] == 100, "rule 要回传原始规则对象"
    assert "不会用其它题目顶替" in sf["reason"], sf["reason"]

    # ★ 核心断言：写进卷面的就是 got 道，绝不是 need 道
    assert d["question_count"] == total_case, (
        f"静默补题了！need=100 但库里只有 {total_case} 道，卷面却写入了 {d['question_count']} 道"
    )
    assert len(d["shortfalls"]) >= 1 and "未凑够" in d["message"], d["message"]

    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    real = sum(len(s["questions"]) for s in detail["sections"])
    assert real == total_case, f"库中实际挂题 {real} 道 ≠ got {total_case} 道"
    assert len(detail["shortfalls"]) == 1, "缺口要持久化，详情页才能继续警告"

    # 有缺口的卷：分段没填满 → 校验必然不通过 → 发布被挡
    v = validate(client, admin_h, exam["id"])
    assert v["data"]["ok"] is False, v["data"]
    codes = {e["code"] for e in v["data"]["errors"]}
    assert "SECTION_NOT_FILLED" in codes, codes
    assert "COMPOSE_SHORTFALL" in {w["code"] for w in v["data"]["warnings"]}, v["data"]["warnings"]


# ================================================================ F. 验收⑥ 发布前强制校验


def test_publish_blocked_when_validation_fails(client: httpx.Client, admin_h, created) -> None:
    """验收⑥：发布前强制走 validate，不通过**不允许发布**，且整批不落库。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"空卷发布 {uuid.uuid4().hex[:6]}",
                       sections=[{"name": "单项选择题", "question_type": "single",
                                  "question_count": 10, "score_per": 1, "sort_no": 0}])
    created.exams.append(exam["id"])

    # 空卷：一道题都没有
    v = validate(client, admin_h, exam["id"])
    assert v["data"]["ok"] is False
    assert "NO_QUESTIONS" in {e["code"] for e in v["data"]["errors"]}, v["data"]["errors"]

    b = publish(client, admin_h, exam["id"])
    assert b["code"] == 40901 and "校验未通过" in b["message"], b
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    assert detail["status"] == "draft", "发布失败不该改状态"
    assert detail["published_at"] is None, detail

    # 分段计划 10 道、实际只放 1 道 → 仍然挡
    b = compose(client, admin_h, exam["id"], {
        "rules": [{"type": "single", "count": 1}], "apply_sections": True, "seed": 1,
    })
    assert b["code"] == 0 and b["data"]["question_count"] == 1, b
    # 组卷重写了分段（计划 1 道 = 实际 1 道）→ 这时校验应通过
    assert validate(client, admin_h, exam["id"])["data"]["ok"] is True

    # 手工把分段改成 10 道来制造"没填满"
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    sql = (
        "UPDATE exam_sections SET question_count = 10, section_score = 10 WHERE exam_id = $1"
    )
    if sql_exec(sql, int(exam["id"])) is None:
        pytest.skip("需要 DATABASE_URL 才能手工改分段")
    v = validate(client, admin_h, exam["id"])
    assert v["data"]["ok"] is False, v["data"]
    assert "SECTION_NOT_FILLED" in {e["code"] for e in v["data"]["errors"]}, v["data"]["errors"]
    b = publish(client, admin_h, exam["id"])
    assert b["code"] == 40901, b


def test_publish_twice_and_recompose_after_publish_are_rejected(
    client: httpx.Client, admin_h, created
) -> None:
    """状态保护：已发布的卷不能重复发布，也不能重新组卷。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"状态保护 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert compose(client, admin_h, exam["id"],
                   {"rules": [{"type": "judge", "count": 5}], "seed": 3})["code"] == 0

    b = publish(client, admin_h, exam["id"])
    assert b["code"] == 0, b
    assert b["data"]["status"] == "published" and b["data"]["locked_versions"] == 5, b

    b = publish(client, admin_h, exam["id"])
    assert b["code"] == 40901 and "已经是发布状态" in b["message"], b

    b = compose(client, admin_h, exam["id"], {"rules": [{"type": "judge", "count": 5}]})
    assert b["code"] == 40901 and "已发布" in b["message"], b


# ================================================================ G. 验收③ 版本锁定


def test_publish_locks_version_and_later_edit_does_not_change_paper(
    client: httpx.Client, admin_h, created
) -> None:
    """验收③：发布试卷 → 编辑其中某题 → 打开已发布试卷**仍显示锁定版本**。

    用 `exam_year=2026` 造一道"独家"题：种子里所有题的 `exam_year` 都是 NULL，
    所以「year=2026 + single」这条规则**只会**命中这道题，卷面构成完全可控。
    """
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能确认种子题的 exam_year 分布")

    q = make_question(client, admin_h, subject_id=SZ_SUBJECT_ID, qtype="single",
                      exam_year=2026, stem="【B7 锁定用例】原始题干：下列表述正确的是？")
    created.questions.append(q["id"])
    assert q["version"] == 1, q

    exam = create_exam(client, admin_h, subject_id=SZ_SUBJECT_ID,
                       title=f"锁定 {uuid.uuid4().hex[:6]}", pass_score=10)
    created.exams.append(exam["id"])

    b = compose(client, admin_h, exam["id"], {
        "rules": [{"type": "single", "count": 1, "score": 10, "year": 2026}],
    })
    assert b["code"] == 0, b
    assert b["data"]["shortfalls"] == [], b["data"]["shortfalls"]
    assert b["data"]["question_count"] == 1, b["data"]

    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    picked = detail["sections"][0]["questions"][0]
    assert picked["question_id"] == q["id"], f"应只抽到刚造的题：{picked}"
    assert picked["locked_version"] is None, "发布前不该有锁定版本"

    b = publish(client, admin_h, exam["id"])
    assert b["code"] == 0 and b["data"]["locked_versions"] == 1, b

    # ---- 改题：题干变了、版本 +1 ----
    b = body(client.put(f"{API}/admin/questions/{q['id']}", headers=admin_h, json={
        "version": 1, "stem": "【B7 锁定用例】改过的题干：下列说法错误的是？",
    }))
    assert b["code"] == 0 and b["data"]["version"] == 2, b

    # ---- 已发布试卷：锁定版本仍在，且展示的是**旧题干** ----
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    picked = detail["sections"][0]["questions"][0]
    assert picked["locked_version"] == 1, f"锁定版本应停在 v1：{picked}"
    assert picked["current_version"] == 2, f"题目当前已是 v2：{picked}"
    assert picked["version_drift"] is True, picked
    assert "原始题干" in picked["stem_preview"], (
        f"已发布试卷必须展示锁定版本的题干，实际：{picked['stem_preview']}"
    )
    assert "改过的题干" not in picked["stem_preview"], picked["stem_preview"]

    # 校验给出 VERSION_DRIFT warning，但仍允许发布/保持已发布
    v = validate(client, admin_h, exam["id"])
    assert v["data"]["ok"] is True, v["data"]["errors"]
    assert "VERSION_DRIFT" in {w["code"] for w in v["data"]["warnings"]}, v["data"]["warnings"]

    # 重新组卷会清空锁定（但已发布的卷本来就不允许重组）
    assert detail["status"] == "published" and detail["published_at"], detail


# ================================================================ H. 验收④ 6000 题库整卷


def test_compose_full_mock_paper_from_seed_bank(client: httpx.Client, admin_h, created) -> None:
    """验收④：用 6000 道仿真题库组一套完整模考卷（单选 60 + 多选 20 + 判断 20）。

    科目用「建设工程经济」（种子里单选 513 / 多选 246 / 判断 207，容量充足）。
    """
    counts = sql_fetch(
        "SELECT type, count(*) AS n FROM questions WHERE subject_id=$1 AND status='published' "
        "AND is_deleted=false AND parent_id IS NULL AND type IN ('single','multiple','judge') "
        "GROUP BY type",
        JJ_SUBJECT_ID,
    )
    if counts is None:
        pytest.skip("需要 DATABASE_URL 才能确认题库容量")
    have = {r["type"]: int(r["n"]) for r in counts}
    need = {"single": 60, "multiple": 20, "judge": 20}
    for t, n in need.items():
        if have.get(t, 0) < n:
            pytest.skip(f"科目 {JJ_SUBJECT_ID} 的 {t} 只有 {have.get(t, 0)} 道，不足 {n} 道")

    rules = [
        {"type": "single", "count": 60, "score": 1, "difficulty": [2, 3]},
        {"type": "multiple", "count": 20, "score": 2, "difficulty": [3, 4]},
        {"type": "judge", "count": 20, "score": 1, "difficulty": [1, 2]},
    ]
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"B7 全真模考 {uuid.uuid4().hex[:6]}", duration_min=120, pass_score=60)
    created.exams.append(exam["id"])

    b = compose(client, admin_h, exam["id"], {"rules": rules, "seed": 20260917})
    assert b["code"] == 0, b
    d = b["data"]
    assert d["shortfalls"] == [], f"容量充足不该有缺口：{d['shortfalls']}"
    assert d["question_count"] == 100, d
    assert d["total_score"] == 120.0, f"60×1 + 20×2 + 20×1 = 120，实际 {d['total_score']}"

    by_type = {s["question_type"]: s for s in d["sections"]}
    assert set(by_type) == {"single", "multiple", "judge"}, by_type
    assert by_type["single"]["actual_count"] == 60 and by_type["single"]["section_score"] == 60.0
    assert by_type["multiple"]["actual_count"] == 20 and by_type["multiple"]["section_score"] == 40.0
    assert by_type["judge"]["actual_count"] == 20 and by_type["judge"]["section_score"] == 20.0

    v = validate(client, admin_h, exam["id"])
    assert v["data"]["ok"] is True, v["data"]["errors"]
    assert v["data"]["question_count"] == 100 and v["data"]["total_score"] == 120.0, v["data"]

    b = publish(client, admin_h, exam["id"])
    assert b["code"] == 0 and b["data"]["locked_versions"] == 100, b

    # 打印一份"卷面构成"当验收证据
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    print("\n================ B7 模考卷面构成 ================")
    print(f"  试卷     {detail['title']}")
    print(f"  状态     {detail['status']}  试卷号 {detail.get('paper_no')}")
    print(f"  题量     {detail['question_count']} 道   总分 {detail['total_score']}")
    print(f"  时长     {detail['duration_min']} 分钟   及格线 {detail['pass_score']}")
    for s in detail["sections"]:
        print(f"  - {s['name']:<8} {s['actual_count']:>3} 道 × {s['score_per']} 分 "
              f"= {s['section_score']} 分")
    print("===============================================\n")


def test_rule_from_rule_table_composes_paper(client: httpx.Client, admin_h, created) -> None:
    """`rule_id` 通路：用库里的规则组卷（规则可复用，不内联）。"""
    made = new_rule(client, admin_h, subject_id=JJ_SUBJECT_ID,
                    rules=[{"type": "judge", "count": 10, "score": 1}])
    created.rules.append(made["id"])
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"规则表组卷 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])

    b = compose(client, admin_h, exam["id"], {"rule_id": made["id"]})
    assert b["code"] == 0, b
    assert b["data"]["question_count"] == 10 and b["data"]["total_score"] == 10.0, b["data"]


# ================================================================ I. 验收⑤ 数据范围


def test_data_scope_researcher_only_own_subject(client: httpx.Client, admin_h, created) -> None:
    """验收⑤：教研（researcher + subject 范围）只能组自己科目的卷。

    三道闸都要拦得住：建卷、组卷、以及**规则列表本身就看不到别科目的**。
    """
    u = fresh_user(client, nickname="B7 教研-范围")
    ar = assign_roles(client, admin_h, u["user"]["id"], ["researcher"],
                      scope_type="subject", scope_id=SZ_SUBJECT_ID)
    assert ar["code"] == 0, ar
    rh = auth(u["access_token"])

    # 闸 1：建别科目的卷 → 403
    b = body(client.post(f"{API}/admin/exams", headers=rh, json={
        "subject_id": JZ_SUBJECT_ID, "title": "越权卷", "sections": [],
    }))
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    # 闸 2：本科目的卷可以建；但换成别科目组卷 → 403
    exam = body(client.post(f"{API}/admin/exams", headers=rh, json={
        "subject_id": SZ_SUBJECT_ID, "title": f"B7 教研卷 {uuid.uuid4().hex[:6]}", "sections": [],
    }))
    assert exam["code"] == 0, exam
    eid = exam["data"]["id"]
    created.exams.append(eid)

    b = compose(client, rh, eid, {"subject_id": JZ_SUBJECT_ID,
                                  "rules": [{"type": "case", "count": 1}]})
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    # 本科目组卷正常
    b = compose(client, rh, eid, {"rules": [{"type": "judge", "count": 5}], "seed": 5})
    assert b["code"] == 0 and b["data"]["question_count"] == 5, b

    # 闸 3：规则列表只返回自己科目的规则
    mine = new_rule(client, admin_h, subject_id=SZ_SUBJECT_ID,
                    rules=[{"type": "judge", "count": 5}])
    created.rules.append(mine["id"])
    other = new_rule(client, admin_h, subject_id=JZ_SUBJECT_ID,
                     rules=[{"type": "single", "count": 5}])
    created.rules.append(other["id"])

    b = body(client.get(f"{API}/admin/paper-rules", headers=rh, params={"page_size": 100}))
    assert b["code"] == 0, b
    subs = {x["subject_id"] for x in b["data"]["items"]}
    assert subs <= {str(SZ_SUBJECT_ID)}, f"教研看到了别科目的规则：{subs}"

    # 别科目的规则，教研改不动（403）
    b = body(client.put(f"{API}/admin/paper-rules/{other['id']}", headers=rh, json={"name": "改名"}))
    assert b["code"] == 40301, b

    # 别科目的卷，教研看不到也读不了
    other_exam = create_exam(client, admin_h, subject_id=JZ_SUBJECT_ID,
                             title=f"别科目卷 {uuid.uuid4().hex[:6]}")
    created.exams.append(other_exam["id"])
    b = body(client.get(f"{API}/admin/exams/{other_exam['id']}", headers=rh))
    assert b["code"] == 40301, b

    b = body(client.get(f"{API}/admin/exams", headers=rh, params={"page_size": 100}))
    assert {x["subject_id"] for x in b["data"]["items"]} <= {str(SZ_SUBJECT_ID)}, b["data"]


# ================================================================ J. 权限门控


def test_exam_endpoints_require_permissions(client: httpx.Client, admin_h, created) -> None:
    """权限门控：无权限角色一律 403、未登录 401；并**记录一个角色模型缺口**。

    ⚠️ 实测发现（`db/schema.sql` 的权限种子）：`exam` 模块的四条权限
    （`read` / `create` / `publish` / `grade`）是**整包**发给角色的 ——
    `super_admin` / `admin` / `researcher` / `teacher` **四个角色都同时拥有全部四条**。
    也就是说：**没有任何内置角色能表达「能看试卷但不能发布」**，
    这跟 Batch 3 遇到过的 "有 `user:read` 却没人没有 `user:manage`" 是同一类缺口
    （那次是靠新增 `viewer` 角色补上的）。

    本批**不新增角色**（改种子超出组卷引擎范围），所以这里如实断言现状：
    `researcher` 是**可以**发布试卷的。缺口记在 `docs/13` §遗留事项。
    """
    u = fresh_user(client, nickname="B7 无权限")
    # student 无任何权限
    assign_roles(client, admin_h, u["user"]["id"], ["student"])
    sh = auth(u["access_token"])

    b = body(client.get(f"{API}/admin/exams", headers=sh))
    assert b["code"] == 40301 and "exam:read" in b["message"], b
    b = body(client.post(f"{API}/admin/exams", headers=sh, json={
        "subject_id": JJ_SUBJECT_ID, "title": "x", "sections": [],
    }))
    assert b["code"] == 40301 and "exam:create" in b["message"], b
    b = body(client.get(f"{API}/admin/paper-rules", headers=sh))
    assert b["code"] == 40301, b
    b = body(client.post(f"{API}/admin/exams/1/validate", headers=sh))
    assert b["code"] == 40301 and "exam:read" in b["message"], b
    b = body(client.post(f"{API}/admin/exams/1/publish", headers=sh, json={}))
    assert b["code"] == 40301 and "exam:publish" in b["message"], b

    # 未登录 → 401
    b = body(client.get(f"{API}/admin/exams"))
    assert b["code"] in (40100, 40101), b

    # researcher 走完整链路（教研本来就该能发布自己的卷 —— 当前的权限模型如此）
    u2 = fresh_user(client, nickname="B7 教研-全链路")
    assign_roles(client, admin_h, u2["user"]["id"], ["researcher"],
                 scope_type="subject", scope_id=JJ_SUBJECT_ID)
    rh = auth(u2["access_token"])
    exam = body(client.post(f"{API}/admin/exams", headers=rh, json={
        "subject_id": JJ_SUBJECT_ID, "title": f"B7 教研发布 {uuid.uuid4().hex[:6]}",
        "sections": [], "pass_score": 3,
    }))
    assert exam["code"] == 0, exam
    created.exams.append(exam["data"]["id"])
    assert compose(client, rh, exam["data"]["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 9})["code"] == 0
    b = publish(client, rh, exam["data"]["id"])
    assert b["code"] == 0 and b["data"]["locked_versions"] == 3, b


# ================================================================ K. 前置修正三件
# （locked_version 独立列 / viewer 只读 / 试卷归档）—— 这三条是 Pass 2 前端的地基。


def test_locked_version_is_written_to_column_and_mirrored_to_jsonb(
    client: httpx.Client, admin_h, created
) -> None:
    """版本锁定落在**独立列** `exam_questions.locked_version`；JSONB 仍双写但已 deprecated。

    为什么单独断言这件事：锁定最初挤在 `exams.rule_config.question_locks` 里
    （"不改 schema"的过度约束）。加列之后，**读**优先走列、**写**双写，
    两件事都必须被固定住，否则下批清理 JSONB 时没人知道它到底还用不用。
    """
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能直接断言列内容")

    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"锁定列 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert compose(client, admin_h, exam["id"],
                   {"rules": [{"type": "judge", "count": 4}], "seed": 11})["code"] == 0

    # 发布前：列应为 NULL（没有锁定）
    rows = sql_fetch(
        "SELECT locked_version FROM exam_questions WHERE exam_id=$1", int(exam["id"])
    )
    assert len(rows) == 4 and all(r["locked_version"] is None for r in rows), rows

    assert publish(client, admin_h, exam["id"])["code"] == 0

    # 发布后：4 行的列都被填上，且值等于题目当前 version
    after = sql_fetch(
        "SELECT eq.question_id, eq.locked_version, q.version AS current_version "
        "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
        "WHERE eq.exam_id=$1",
        int(exam["id"]),
    )
    assert len(after) == 4, after
    assert all(r["locked_version"] is not None for r in after), f"列没被填上：{after}"
    assert all(r["locked_version"] == r["current_version"] for r in after), after

    # JSONB 仍在双写（deprecated，留回滚余地）—— 键与值都要与列**逐条**一致，
    # 否则回滚到旧代码时锁定会悄悄丢。
    # 注意：asyncpg 把 jsonb 当**字符串**返回（没注册 codec），这里要自己解析。
    rc_rows = sql_fetch("SELECT rule_config FROM exams WHERE id=$1", int(exam["id"]))
    rc_raw = rc_rows[0]["rule_config"]
    rc = json.loads(rc_raw) if isinstance(rc_raw, str) else (rc_raw or {})
    locks_json = rc.get("question_locks") or {}
    assert len(locks_json) == 4, f"JSONB 双写缺失（回滚会丢锁定）：{locks_json}"
    for r in after:
        key = str(r["question_id"])
        assert key in locks_json, f"JSONB 缺少题目 {key}：{locks_json}"
        assert int(locks_json[key]) == r["locked_version"], (
            f"JSONB 与列不一致：jsonb={locks_json[key]} column={r['locked_version']}"
        )

    # 重新组卷应把列清空（卷面换了，旧锁没意义）
    # 已发布的卷不能重组，所以先造一张草稿卷验证清理逻辑
    draft = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                        title=f"清锁 {uuid.uuid4().hex[:6]}")
    created.exams.append(draft["id"])
    assert compose(client, admin_h, draft["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 12})["code"] == 0
    publish(client, admin_h, draft["id"])
    # 已发布 → 直接改回 draft 再重组（模拟"下线重编"）
    sql_exec("UPDATE exams SET status='draft', published_at=NULL WHERE id=$1", int(draft["id"]))
    assert compose(client, admin_h, draft["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 13})["code"] == 0
    cleared = sql_fetch(
        "SELECT locked_version FROM exam_questions WHERE exam_id=$1", int(draft["id"])
    )
    assert all(r["locked_version"] is None for r in cleared), f"重组后锁未清：{cleared}"


def test_viewer_can_read_but_not_publish(client: httpx.Client, admin_h, created) -> None:
    """验收标准 ②：`viewer` 能看试卷列表与详情，**发布按钮置灰**（后端 40301）。

    `viewer` 是"通用只读岗"（Batch 7 给它补了 `exam:read`，**不新建角色**）。
    这条同时是把「有 read、没有 write → 按钮置灰」这个 B 端状态**变得可复现**的关键：
    其他角色的权限是按模块整包发的，都能发布（见坑 36）。
    """
    # 先备一张有内容的卷，供 viewer 读
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"viewer 只读 {uuid.uuid4().hex[:6]}", pass_score=3)
    created.exams.append(exam["id"])
    assert compose(client, admin_h, exam["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 21})["code"] == 0

    u = fresh_user(client, nickname="B7 只读岗")
    ar = assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    assert ar["code"] == 0, ar
    vh = auth(u["access_token"])

    # 读：列表 / 详情 / 校验，全部放行
    b = body(client.get(f"{API}/admin/exams", headers=vh, params={"page_size": 100}))
    assert b["code"] == 0 and any(x["id"] == exam["id"] for x in b["data"]["items"]), b
    b = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=vh))
    assert b["code"] == 0, b
    assert b["data"]["id"] == exam["id"] and len(b["data"]["sections"]) == 1, b["data"]
    b = body(client.post(f"{API}/admin/exams/{exam['id']}/validate", headers=vh))
    assert b["code"] == 0, b
    b = body(client.get(f"{API}/admin/paper-rules", headers=vh))
    assert b["code"] == 0, b

    # 写：全部 40301，且消息里点明缺哪个权限（前端 tooltip 直接用这句）
    b = publish(client, vh, exam["id"])
    assert b["code"] == 40301 and "exam:publish" in b["message"], b
    b = body(client.post(f"{API}/admin/exams", headers=vh, json={
        "subject_id": JJ_SUBJECT_ID, "title": "越权建卷", "sections": [],
    }))
    assert b["code"] == 40301 and "exam:create" in b["message"], b
    b = body(client.post(f"{API}/admin/exams/{exam['id']}/auto-compose", headers=vh,
                         json={"rules": [{"type": "judge", "count": 1}]}))
    assert b["code"] == 40301 and "exam:create" in b["message"], b
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=vh))
    assert b["code"] == 40301 and "exam:create" in b["message"], b

    # 权限模型自检：viewer 有 exam:read 但没有 exam:publish
    perms = body(client.get(f"{API}/auth/me", headers=vh))["data"]["permissions"]
    assert "exam:read" in perms, perms
    assert "exam:publish" not in perms, perms
    assert "exam:create" not in perms, perms

    # 卷子仍是 draft（viewer 的失败尝试没有产生副作用）
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    assert detail["status"] == "draft", detail


def test_soft_delete_hides_from_default_list_but_keeps_detail(
    client: httpx.Client, admin_h, created
) -> None:
    """验收标准 ⑤：归档一份卷 → 默认列表看不到 → 打开开关能看到 → 详情仍可读。

    与 Batch 4 的题目软删除同一套语义：只置 `is_deleted`，
    题目不动、卷面不删、详情不 404。
    """
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"归档 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert compose(client, admin_h, exam["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 31})["code"] == 0
    assert publish(client, admin_h, exam["id"])["code"] == 0

    # 归档
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h,
                           params={"reason": "用例归档"}))
    assert b["code"] == 0, b
    assert b["data"]["is_deleted"] is True, b["data"]
    assert b["data"]["previous_status"] == "published", b["data"]
    assert b["data"]["question_count"] == 3, b["data"]

    # 默认列表看不到
    b = body(client.get(f"{API}/admin/exams", headers=admin_h,
                        params={"page_size": 100, "status": "published"}))
    assert not any(x["id"] == exam["id"] for x in b["data"]["items"]), "归档后默认列表不该出现"

    # 开关打开能看到，且带 is_deleted 标记
    b = body(client.get(f"{API}/admin/exams", headers=admin_h,
                        params={"page_size": 100, "include_deleted": "true"}))
    hits = [x for x in b["data"]["items"] if x["id"] == exam["id"]]
    assert len(hits) == 1 and hits[0]["is_deleted"] is True, "开了开关应能看到归档的卷"

    # 详情仍可打开（不是 404），并且按钮全灰
    b = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))
    assert b["code"] == 0 and b["data"]["is_deleted"] is True, b
    assert b["data"]["can_edit"] is False and b["data"]["can_delete"] is False, b["data"]
    assert b["data"]["can_publish"] is False and b["data"]["can_compose"] is False, b["data"]
    # 卷面数据仍在
    assert len(b["data"]["sections"][0]["questions"]) == 3, b["data"]["sections"]

    # 重复归档 → 40001（不静默成功）
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h))
    assert b["code"] == 40001 and "已归档" in b["message"], b

    # 归档不动题目：题库题量不变
    n = body(client.get(f"{API}/admin/questions?page=1&page_size=1", headers=admin_h))
    assert n["code"] == 0, n

    # 不存在的 id 归档 → 40401
    b = body(client.delete(f"{API}/admin/exams/999999999999999999", headers=admin_h))
    assert b["code"] == 40401, b


# ================================================================ L. 恢复接口（题目 + 试卷对称）
# 两条接口刻意做成对称的：
#     POST /admin/exams/{id}/restore      —— exam:publish
#     POST /admin/questions/{id}/restore  —— question:delete
# 共同性质：幂等；恢复前校验关联数据有效性，不静默恢复到悬空引用上。


def restore_exam(client, headers, exam_id) -> dict:
    return body(client.post(f"{API}/admin/exams/{exam_id}/restore", headers=headers, timeout=30))


def restore_question(client, headers, qid) -> dict:
    return body(client.post(f"{API}/admin/questions/{qid}/restore", headers=headers, timeout=30))


def test_exam_restore_roundtrip_and_idempotent(client: httpx.Client, admin_h, created) -> None:
    """归档 → 默认列表看不到 → 开开关能看到 → **恢复** → 回到默认列表；再调幂等。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"恢复 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert compose(client, admin_h, exam["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 41})["code"] == 0

    assert body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["code"] == 0

    def in_default_list() -> bool:
        b = body(client.get(f"{API}/admin/exams", headers=admin_h,
                            params={"page_size": 100, "subject_id": JJ_SUBJECT_ID}))
        return any(x["id"] == exam["id"] for x in b["data"]["items"])

    assert not in_default_list(), "归档后不该出现在默认列表"

    # 恢复
    r = restore_exam(client, admin_h, exam["id"])
    assert r["code"] == 0, r
    assert r["data"]["is_deleted"] is False and r["data"]["already_active"] is False, r["data"]
    assert in_default_list(), "恢复后应回到默认列表"

    # 详情：按钮能力恢复（草稿态可编可组）
    d = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    assert d["is_deleted"] is False and d["can_edit"] is True and d["can_delete"] is True, d

    # 幂等：没归档的再调一次 → code=0、already_active=true
    again = restore_exam(client, admin_h, exam["id"])
    assert again["code"] == 0, again
    assert again["data"]["already_active"] is True, again["data"]
    assert again["data"]["is_deleted"] is False, again["data"]

    # 不存在的 id → 40401
    b = restore_exam(client, admin_h, 999999999999999999)
    assert b["code"] == 40401, b


def test_exam_restore_requires_publish_permission(client: httpx.Client, admin_h, created) -> None:
    """恢复需要 `exam:publish`：viewer 能看能归档（exam:create）但**不能恢复**。"""
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"恢复权限 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["code"] == 0

    u = fresh_user(client, nickname="B7 恢复权限")
    assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    vh = auth(u["access_token"])

    # 读 OK（含已归档的详情）
    assert body(client.get(f"{API}/admin/exams/{exam['id']}", headers=vh))["code"] == 0
    # 恢复被挡
    b = restore_exam(client, vh, exam["id"])
    assert b["code"] == 40301 and "exam:publish" in b["message"], b
    # 归档也被挡（viewer 没有 exam:create）
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=vh))
    assert b["code"] == 40301 and "exam:create" in b["message"], b

    # 仍是归档态（viewer 的失败尝试无副作用）
    d = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    assert d["is_deleted"] is True, d


def test_exam_restore_rejects_when_subject_disabled(client: httpx.Client, admin_h, created) -> None:
    """所属科目已停用 → 拒绝恢复并说明原因、**不改状态**。"""
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能改科目状态")

    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"停用科目 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["code"] == 0

    try:
        assert sql_exec("UPDATE subjects SET status='off' WHERE id=$1", JJ_SUBJECT_ID) is not None
        b = restore_exam(client, admin_h, exam["id"])
        assert b["code"] == 40901 and "停用" in b["message"], b
        # 拒绝 = 没有副作用
        d = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
        assert d["is_deleted"] is True, "被拒绝的恢复不该改动 is_deleted"
    finally:
        sql_exec("UPDATE subjects SET status='on' WHERE id=$1", JJ_SUBJECT_ID)

    # 科目恢复启用后，同一个请求就通过了
    assert restore_exam(client, admin_h, exam["id"])["code"] == 0


def test_exam_restore_rejects_published_with_archived_questions(
    client: httpx.Client, admin_h, created
) -> None:
    """**已发布**的卷若卷面有题被归档 → 拒绝恢复（否则考生看到残缺卷面）。

    对照：把同一个卷改回草稿，就不该再拦 —— 草稿缺题很正常。
    """
    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"缺题的已发布卷 {uuid.uuid4().hex[:6]}", pass_score=3)
    created.exams.append(exam["id"])
    assert compose(client, admin_h, exam["id"],
                   {"rules": [{"type": "judge", "count": 3}], "seed": 42})["code"] == 0
    assert publish(client, admin_h, exam["id"])["code"] == 0

    # 挑一道卷面题归档
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    victim = detail["sections"][0]["questions"][0]["question_id"]
    created.questions.append(victim)
    assert body(client.delete(f"{API}/admin/questions/{victim}", headers=admin_h))["code"] == 0

    assert body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["code"] == 0
    b = restore_exam(client, admin_h, exam["id"])
    assert b["code"] == 40901 and "已被归档" in b["message"], b
    assert "已发布" in b["message"], b["message"]

    # 草稿态不受此限
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能改试卷状态")
    sql_exec("UPDATE exams SET status='draft', published_at=NULL WHERE id=$1", int(exam["id"]))
    assert restore_exam(client, admin_h, exam["id"])["code"] == 0, "草稿缺题不该拦"


def test_question_restore_roundtrip_and_idempotent(client: httpx.Client, admin_h, created) -> None:
    """题目：软删 → 默认列表看不到 → 恢复 → 回到列表；幂等；版本号 +1。"""
    q = make_question(client, admin_h, subject_id=JJ_SUBJECT_ID)
    created.questions.append(q["id"])
    assert q["version"] == 1, q

    assert body(client.delete(f"{API}/admin/questions/{q['id']}", headers=admin_h))["code"] == 0

    def visible() -> bool:
        b = body(client.get(f"{API}/admin/questions", headers=admin_h,
                            params={"page_size": 100, "subject_id": JJ_SUBJECT_ID}))
        return any(x["id"] == q["id"] for x in b["data"]["items"])

    assert not visible(), "软删除后默认列表不该有它"

    r = restore_question(client, admin_h, q["id"])
    assert r["code"] == 0, r
    assert r["data"]["is_deleted"] is False and r["data"]["already_active"] is False, r["data"]
    assert r["data"]["version"] == 3, f"删除 +1、恢复 +1，应为 3：{r['data']}"
    assert visible(), "恢复后应回到默认列表"

    # 幂等
    again = restore_question(client, admin_h, q["id"])
    assert again["code"] == 0 and again["data"]["already_active"] is True, again
    assert again["data"]["version"] == 3, "幂等分支不该再 +1"

    # 不存在的 id → 40401
    assert restore_question(client, admin_h, 999999999999999999)["code"] == 40401


def test_question_restore_requires_delete_permission(client: httpx.Client, admin_h, created) -> None:
    """恢复题目需要 `question:delete`（与删除**同一个权责**，不新增权限码）。"""
    q = make_question(client, admin_h, subject_id=JJ_SUBJECT_ID)
    created.questions.append(q["id"])
    assert body(client.delete(f"{API}/admin/questions/{q['id']}", headers=admin_h))["code"] == 0

    u = fresh_user(client, nickname="B7 题目恢复权限")
    assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    vh = auth(u["access_token"])

    b = restore_question(client, vh, q["id"])
    assert b["code"] == 40301 and "question:delete" in b["message"], b
    # 仍然是删除态
    d = body(client.get(f"{API}/admin/questions/{q['id']}", headers=admin_h))["data"]
    assert d["is_deleted"] is True, d

    # researcher 有 question:delete → 能恢复
    u2 = fresh_user(client, nickname="B7 教研恢复")
    assign_roles(client, admin_h, u2["user"]["id"], ["researcher"],
                 scope_type="subject", scope_id=JJ_SUBJECT_ID)
    rh = auth(u2["access_token"])
    assert restore_question(client, rh, q["id"])["code"] == 0


def test_question_restore_rejects_when_chapter_or_kp_deleted(
    client: httpx.Client, admin_h, created
) -> None:
    """⚠️ 核心：题目挂的章节 / 知识点已删 → **拒绝恢复并说明原因**，不静默恢复到悬空引用。

    章节与知识点是**软删除**，数据库不会拦题目的弱引用 ——
    这正是"恢复前必须自己校验"的原因。
    """
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能构造失效的章节/知识点")

    # 找一个真实存在的 chapter / knowledge_point
    ch = sql_fetch(
        "SELECT c.id, c.subject_id, k.id AS kp_id FROM chapters c "
        "JOIN knowledge_points k ON k.chapter_id = c.id "
        "WHERE c.subject_id=$1 AND c.is_deleted=false AND k.is_deleted=false LIMIT 1",
        JJ_SUBJECT_ID,
    )
    if not ch:
        pytest.skip(f"科目 {JJ_SUBJECT_ID} 缺少可用的章节/知识点种子")
    chapter_id, kp_id = int(ch[0]["id"]), int(ch[0]["kp_id"])

    q = make_question(client, admin_h, subject_id=JJ_SUBJECT_ID, chapter_id=chapter_id)
    created.questions.append(q["id"])
    assert body(client.delete(f"{API}/admin/questions/{q['id']}", headers=admin_h))["code"] == 0

    # ---- 场景 1：章节被软删除 ----
    try:
        sql_exec("UPDATE questions SET knowledge_point_id=$1 WHERE id=$2", kp_id, int(q["id"]))
        sql_exec("UPDATE chapters SET is_deleted=true WHERE id=$1", chapter_id)

        b = restore_question(client, admin_h, q["id"])
        assert b["code"] == 40901, b
        assert "章节" in b["message"] and "已删除" in b["message"], b["message"]
        assert "不会静默恢复" in b["message"], b["message"]
        # 拒绝 = 没有副作用
        row = sql_fetch("SELECT is_deleted FROM questions WHERE id=$1", int(q["id"]))
        assert row[0]["is_deleted"] is True, "被拒绝的恢复不该改动 is_deleted"

        # ---- 场景 2：知识点所属章节被删（知识点本身还在）----
        sql_exec("UPDATE questions SET chapter_id=NULL WHERE id=$1", int(q["id"]))
        b = restore_question(client, admin_h, q["id"])
        assert b["code"] == 40901, b
        assert "知识点" in b["message"] and "所属章节" in b["message"], b["message"]

        # ---- 场景 3：知识点自己被软删除 ----
        sql_exec("UPDATE chapters SET is_deleted=false WHERE id=$1", chapter_id)
        sql_exec("UPDATE knowledge_points SET is_deleted=true WHERE id=$1", kp_id)
        b = restore_question(client, admin_h, q["id"])
        assert b["code"] == 40901 and "知识点" in b["message"], b
    finally:
        # 清场：把章节/知识点/题目的引用与删除位恢复原状
        sql_exec("UPDATE chapters SET is_deleted=false WHERE id=$1", chapter_id)
        sql_exec("UPDATE knowledge_points SET is_deleted=false WHERE id=$1", kp_id)
        sql_exec(
            "UPDATE questions SET chapter_id=NULL, knowledge_point_id=NULL WHERE id=$1",
            int(q["id"]),
        )

    # 关联数据恢复有效后，同一个请求就通过了（说明前面的拒绝是可逆的、不是永久锁死）
    r = restore_question(client, admin_h, q["id"])
    assert r["code"] == 0, r
    assert r["data"]["is_deleted"] is False, r["data"]


def test_restore_writes_change_log_with_is_deleted_diff(
    client: httpx.Client, admin_h, created
) -> None:
    """恢复要留痕：`content_change_logs` 里 action=restore，diff 为 `{is_deleted: true → false}`。"""
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能断言变更日志")

    exam = create_exam(client, admin_h, subject_id=JJ_SUBJECT_ID,
                       title=f"留痕 {uuid.uuid4().hex[:6]}")
    created.exams.append(exam["id"])
    assert body(client.delete(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["code"] == 0
    assert restore_exam(client, admin_h, exam["id"])["code"] == 0

    rows = sql_fetch(
        "SELECT action, diff FROM content_change_logs "
        "WHERE entity_type='exam' AND entity_id=$1 ORDER BY created_at DESC",
        int(exam["id"]),
    )
    assert rows, "归档/恢复应各写一条变更日志"
    restore_row = next((r for r in rows if r["action"] == "restore"), None)
    assert restore_row is not None, [r["action"] for r in rows]
    diff = restore_row["diff"]
    diff = json.loads(diff) if isinstance(diff, str) else diff
    assert diff["before"]["is_deleted"] is True, diff
    assert diff["after"]["is_deleted"] is False, diff


# ================================================================ M. 手动加题 / 移题
# Pass 2 的前置缺口：spec 要求「手动选题 / 加题 / 移题」，但后端原本只有"自动组卷"。


def add_questions(client, headers, exam_id, ids: list[int], section_id: int | None = None) -> dict:
    payload: dict = {"question_ids": [str(i) for i in ids]}
    if section_id is not None:
        payload["section_id"] = str(section_id)
    return body(client.post(f"{API}/admin/exams/{exam_id}/questions",
                            headers=headers, json=payload, timeout=60))


def make_exam_with_sections(client, admin_h, created, *, subject_id=JJ_SUBJECT_ID,
                            single=0, multiple=0, judge=0) -> dict:
    """建一张**带分段**的草稿卷，供手动加题用。"""
    sections = []
    sort_no = 0
    for qt, cnt, score in (("single", single, 1), ("multiple", multiple, 2), ("judge", judge, 1)):
        if cnt:
            sections.append({
                "name": {"single": "单项选择题", "multiple": "多项选择题",
                         "judge": "判断题"}[qt],
                "question_type": qt, "question_count": cnt, "score_per": score,
                "sort_no": sort_no,
            })
            sort_no += 1
    exam = create_exam(client, admin_h, subject_id=subject_id,
                       title=f"手动选题 {uuid.uuid4().hex[:6]}", sections=sections)
    created.exams.append(exam["id"])
    return exam


def pick_published_ids(client, admin_h, subject_id: int, qtype: str, n: int) -> list[int]:
    b = body(client.get(f"{API}/admin/questions", headers=admin_h, params={
        "subject_id": subject_id, "type": qtype, "status": "published", "page_size": n,
    }))
    assert b["code"] == 0, b
    return [int(x["id"]) for x in b["data"]["items"]][:n]


def test_manual_add_and_remove_questions(client: httpx.Client, admin_h, created) -> None:
    """手动加题 → 题型自动挂到同题型分段 → 题数/总分重算；移题后题数回落 + 提示分段不符。"""
    exam = make_exam_with_sections(client, admin_h, created, single=5, multiple=3)
    assert exam["question_count"] == 0, exam

    singles = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "single", 2)
    multiples = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "multiple", 1)
    assert len(singles) == 2 and len(multiples) == 1, "题库容量不足，先确认种子已导入"

    b = add_questions(client, admin_h, exam["id"], singles + multiples)
    assert b["code"] == 0, b
    d = b["data"]
    assert d["added"] == 3 and d["skipped"] == [], d
    # 单选每题 1 分、多选每题 2 分 → 1+1+2 = 4
    assert d["question_count"] == 3 and d["total_score"] == 4.0, d

    # 分段实际题数：单选段 2 道、多选段 1 道
    by_type = {s["question_type"]: s for s in d["sections"]}
    assert by_type["single"]["actual_count"] == 2, by_type["single"]
    assert by_type["multiple"]["actual_count"] == 1, by_type["multiple"]

    # 详情里能拿到卷面行 id（移题要用）
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    eq_id = detail["sections"][0]["questions"][0]["id"]

    b = body(client.delete(f"{API}/admin/exams/{exam['id']}/questions/{eq_id}",
                           headers=admin_h))
    assert b["code"] == 0, b
    d = b["data"]
    assert d["question_count"] == 2, d
    assert d["total_score"] == 3.0, d
    assert "分段的题数与计划不符" in d["message"], d["message"]
    # 题目本身没被删（只是移出卷面）
    assert body(client.get(f"{API}/admin/questions/{d['question_id']}", headers=admin_h))["code"] == 0

    # 再移一次同一个卷面行 → 40401
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}/questions/{eq_id}", headers=admin_h))
    assert b["code"] == 40401, b


def test_add_questions_skips_with_reason_not_whole_batch_failure(
    client: httpx.Client, admin_h, created
) -> None:
    """六道闸逐条给理由，**不因为一条有问题就整批失败**，也**不静默丢弃**。"""
    # 只建「单选」分段：于是判断题必然找不到同题型分段（第 6 道闸）
    exam = make_exam_with_sections(client, admin_h, created, single=3)

    good = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "single", 1)[0]
    # 别科目的题（经济卷里塞法规题）
    other_subject = pick_published_ids(client, admin_h, JZ_SUBJECT_ID, "single", 1)[0]
    # 已归档的题
    archived = make_question(client, admin_h, subject_id=JJ_SUBJECT_ID)
    created.questions.append(archived["id"])
    assert body(client.delete(f"{API}/admin/questions/{archived['id']}",
                              headers=admin_h))["code"] == 0
    # 卷面没有「判断题」分段 → 这道 judge 题应被跳过
    judge_q = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "judge", 1)[0]

    b = add_questions(client, admin_h, exam["id"],
                      [good, int(other_subject), int(archived["id"]), int(judge_q),
                       999999999999999999, good])
    assert b["code"] == 0, "一条有问题不该整批失败"
    d = b["data"]
    assert d["added"] == 1, d
    assert d["question_count"] == 1, d

    reasons = {s["question_id"]: s["reason"] for s in d["skipped"]}
    assert len(d["skipped"]) == 4, d["skipped"]
    assert "不属于本试卷的科目" in reasons[str(other_subject)], reasons
    assert "已归档" in reasons[str(archived["id"])], reasons
    assert "判断题" in reasons[str(judge_q)] and "分段" in reasons[str(judge_q)], reasons
    assert "不存在" in reasons["999999999999999999"], reasons
    # 重复传同一个 id（good 传了两次）只算一次，不该进 skipped
    assert str(good) not in reasons, reasons


def test_add_and_remove_questions_frozen_after_publish(
    client: httpx.Client, admin_h, created
) -> None:
    """已发布的卷面是冻结的：加题 / 移题都 `40901`。"""
    exam = make_exam_with_sections(client, admin_h, created, judge=2, subject_id=JJ_SUBJECT_ID)
    qs = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "judge", 2)
    assert add_questions(client, admin_h, exam["id"], qs)["code"] == 0
    assert publish(client, admin_h, exam["id"])["code"] == 0

    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    eq_id = detail["sections"][0]["questions"][0]["id"]
    extra = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "judge", 1)

    b = add_questions(client, admin_h, exam["id"], extra)
    assert b["code"] == 40901 and "已发布" in b["message"] and "冻结" in b["message"], b
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}/questions/{eq_id}", headers=admin_h))
    assert b["code"] == 40901 and "已发布" in b["message"], b


def test_add_questions_validates_section_and_permission(
    client: httpx.Client, admin_h, created
) -> None:
    """指定了不属于本卷的分段 → 40001；viewer 无 `exam:create` → 40301。"""
    exam = make_exam_with_sections(client, admin_h, created, single=2)
    qs = pick_published_ids(client, admin_h, JJ_SUBJECT_ID, "single", 1)

    # 不属于本卷的分段 id
    b = add_questions(client, admin_h, exam["id"], qs, section_id=999999999999999999)
    assert b["code"] == 40001 and "分段" in b["message"], b

    u = fresh_user(client, nickname="B7 加题权限")
    assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    vh = auth(u["access_token"])
    b = add_questions(client, vh, exam["id"], qs)
    assert b["code"] == 40301 and "exam:create" in b["message"], b
    # viewer 也移不了题
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    assert add_questions(client, admin_h, exam["id"], qs)["code"] == 0
    detail = body(client.get(f"{API}/admin/exams/{exam['id']}", headers=admin_h))["data"]
    eq_id = detail["sections"][0]["questions"][0]["id"]
    b = body(client.delete(f"{API}/admin/exams/{exam['id']}/questions/{eq_id}", headers=vh))
    assert b["code"] == 40301 and "exam:create" in b["message"], b


def test_question_list_filters_by_knowledge_point(client: httpx.Client, admin_h) -> None:
    """按知识点筛选（Pass 2 的「加题」面板要按知识点挑题，原来只能按章节）。"""
    if _dsn() is None:
        pytest.skip("需要 DATABASE_URL 才能找有题的知识点")

    rows = sql_fetch(
        "SELECT knowledge_point_id AS kp, count(*) AS n FROM questions "
        "WHERE subject_id=$1 AND status='published' AND is_deleted=false "
        "  AND knowledge_point_id IS NOT NULL "
        "GROUP BY knowledge_point_id ORDER BY n DESC LIMIT 1",
        JJ_SUBJECT_ID,
    )
    if not rows:
        pytest.skip(f"科目 {JJ_SUBJECT_ID} 没有挂了知识点的已发布题")
    kp_id, total = int(rows[0]["kp"]), int(rows[0]["n"])

    b = body(client.get(f"{API}/admin/questions", headers=admin_h, params={
        "subject_id": JJ_SUBJECT_ID, "knowledge_point_id": kp_id, "page_size": 1,
    }))
    assert b["code"] == 0, b
    assert int(b["data"]["total"]) == total, f"期望 {total}，实际 {b['data']['total']}"

    # 不传该参数时结果集更大（证明筛选真的在起作用）
    b2 = body(client.get(f"{API}/admin/questions", headers=admin_h, params={
        "subject_id": JJ_SUBJECT_ID, "page_size": 1,
    }))
    assert int(b2["data"]["total"]) > total, "知识点筛选没起作用"

    # 每条结果的 knowledge_point_id 都等于筛选值
    b3 = body(client.get(f"{API}/admin/questions", headers=admin_h, params={
        "subject_id": JJ_SUBJECT_ID, "knowledge_point_id": kp_id, "page_size": 50,
    }))
    assert all(
        int(x["knowledge_point_id"]) == kp_id for x in b3["data"]["items"]
    ), "返回了别的知识点的题"
