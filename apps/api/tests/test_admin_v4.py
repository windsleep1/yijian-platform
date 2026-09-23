"""Batch 4 题库 CRUD 接口验收。

需要 API 已启动（真 PG + fakeredis），且题库里有种子题：

    powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1

覆盖 Batch 4 的 7 个接口：

    GET    /admin/questions                 列表（筛选 + 分页 + 排序 + 归档开关）
    GET    /admin/questions/{id}            详情（选项 / 答案 / 版本历史）
    POST   /admin/questions                 新建
    PUT    /admin/questions/{id}            编辑（乐观锁 + 版本 +1）
    DELETE /admin/questions/{id}            软删除
    POST   /admin/questions/batch-delete     批量软删除
    GET    /admin/chapters/tree             章节树（下拉 / 联动）

外加 B 端最该被验的三条硬规则（对应 docs/10 的「必须体现的 B 端特征」）：

    ① 单选题不能标两个正确答案；多选题的答案必须都在选项里 —— 后端拒绝 + 前端拦截
    ② 保存后 version +1，写 question_versions 与 content_change_logs；详情能看历史只读
    ③ 列表默认隐藏软删除；「显示已归档」能带出来；批量删除共用 batch_id

设计要点：**每个用例自己造数据**，不依赖别的用例的残留 —— 测试之间不该有隐式顺序依赖。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx
import pytest

from .conftest import API, assign_roles, auth, body, fresh_user, register

JS_MAX_SAFE_INT = 2**53 - 1


# ================================================================ 工具


def _q(*, subject_id: int, chapter_id: int | None = None, **over) -> dict:
    """构造一道**内容唯一**的单选题 payload。唯一的 tag 保证不会撞上 content_hash 去重。"""
    tag = uuid.uuid4().hex[:10]
    payload: dict = {
        "subject_id": subject_id,
        "chapter_id": chapter_id,
        "type": "single",
        "stem": f"【自动化】{tag} 关于施工现场安全管理，下列做法正确的是？",
        "analysis": "由自动化用例创建，可用 JSON 唯一 tag 定位后删除。",
        "difficulty": 3,
        "score_default": 1,
        "status": "draft",
        "source_type": "self",
        "options": [
            {"label": "A", "content": f"A-{tag} 未办理审批即开工", "is_correct": False},
            {"label": "B", "content": f"B-{tag} 先审批后施工", "is_correct": True},
            {"label": "C", "content": f"C-{tag} 口头交底替代书面交底", "is_correct": False},
            {"label": "D", "content": f"D-{tag} 省略验收环节", "is_correct": False},
        ],
    }
    payload.update(over)
    return payload


def _dsn() -> str | None:
    """从 DATABASE_URL 推出 asyncpg 直连串（run-smoke.ps1 会设这个环境变量）。"""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgresql+asyncpg://", "postgresql://")


def db_fetch(query: str, *params):
    """直连 PG 取几行。用于断言 content_change_logs / question_versions 这类没有读接口的表。

    没有 DATABASE_URL 或连不上就 skip —— 这条断言是「锦上添花」，不该让整个套件红。
    """
    dsn = _dsn()
    if not dsn:
        pytest.skip("DATABASE_URL 未设置，跳过直连数据库断言")

    async def _run():
        import asyncpg

        # 同 conftest.sql_fetch：command_timeout 必须给（硬约定 N），否则查询可无限等
        conn = await asyncpg.connect(dsn, timeout=10, command_timeout=30)
        try:
            return await conn.fetch(query, *params)
        finally:
            await conn.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"直连数据库失败（{exc}），跳过直连断言")


@pytest.fixture(scope="module")
def sz(client: httpx.Client, admin_h: dict[str, str]) -> tuple[int, int | None]:
    """市政科目的 subject_id 与首个 chapter_id（用于建题时把章节也填上）。"""
    b = body(client.get(f"{API}/admin/chapters/tree", headers=admin_h))
    assert b["code"] == 0, b
    for group in b["data"]["items"]:
        if group["subject"]["code"] == "SW-SZ":
            chapters = group["chapters"]
            return int(group["subject"]["id"]), (int(chapters[0]["id"]) if chapters else None)
    pytest.skip("科目种子缺少市政（SW-SZ）：请确认已执行 db/schema.sql")


# ================================================================ 0. 纯函数（不依赖 API）


def test_content_hash_and_answer_derivation() -> None:
    """去重指纹的归一化 + 答案由选项推导 —— 这是「多选答案必在选项里」的结构保证。"""
    from app.schemas.admin_question import QuestionOptionIn, _validate_options
    from app.services.question_service import content_hash, derive_answer

    # 归一化：大小写 / 空白 / 标点 / 全角括号都不影响指纹
    h1 = content_hash("建设工程  经济（）", [("A", "选项 一。"), ("B", "选项二")], "single")
    h2 = content_hash("建设工程经济", [("A", "选项一"), ("B", "选项二")], "single")
    assert h1 == h2, "同一道题的不同排版应得到相同指纹（否则去重失效）"
    assert content_hash("甲", [("A", "a")], "single") != content_hash("乙", [("A", "a")], "single")

    # 答案只可能来自选项
    single = [
        QuestionOptionIn(label="A", content="甲", is_correct=False),
        QuestionOptionIn(label="B", content="乙", is_correct=True),
    ]
    assert derive_answer("single", single) == {"value": ["B"]}
    assert derive_answer(
        "multiple",
        [
            QuestionOptionIn(label="A", content="甲", is_correct=True),
            QuestionOptionIn(label="C", content="丙", is_correct=True),
        ],
    ) == {"value": ["A", "C"], "partial_credit": True}
    assert derive_answer("judge", [], True) == {"value": [True]}

    # B 端特征①：规则本体
    with pytest.raises(ValueError):
        _validate_options(
            "single",
            [
                QuestionOptionIn(label="A", content="甲", is_correct=True),
                QuestionOptionIn(label="B", content="乙", is_correct=True),
            ],
        )
    with pytest.raises(ValueError):
        _validate_options(
            "multiple",
            [
                QuestionOptionIn(label="A", content="甲", is_correct=True),
                QuestionOptionIn(label="B", content="乙", is_correct=False),
            ],
        )


def test_new_modules_have_no_undefined_globals() -> None:
    """静态守卫：扫描 Batch 4 两个新模块，揪出「引用了但没导入」的全局名。

    为什么值得单独写一条（2026-09-15 实测踩到）：
        `question_service.soft_delete_question` 里用了 `QuestionDeleteOut`，
        但**忘了加进 import 列表**。结果：
          - `python -m compileall` 通过（只看语法，不看名字）；
          - `from app.main import app` 通过（`from __future__ import annotations`
            把注解变成字符串，且该名字只在函数**体内**用到，import 期不触发）；
          - 直到真的调 `DELETE /admin/questions/{id}` 才 `NameError` → 50001。
        也就是说「编译 + 起服务 + OpenAPI 生成」三关全过，代码仍然是坏的。
        这类名字错只有跑到了那一行才炸，而没被测试覆盖的分支会一直潜伏。
        所以这里做一次静态扫描，把「跑不到就发现不了」变成「一开始就发现」。
    """
    import builtins
    import dis
    import inspect
    import os
    import types

    from app.schemas import admin_question
    from app.services import question_service

    def undefined_globals(mod) -> set[str]:
        available = set(dir(builtins)) | set(vars(mod))
        missing: set[str] = set()
        # 只看**由本模块源码编译出来**的代码对象。
        # 必须排除生成代码：@dataclass 的 __init__ / __repr__ 是 exec 出来的，
        # co_filename == "<string>"，它们的全局名来自 dataclasses 模块，
        # 拿本模块的命名空间去判会凭空多出一堆「未定义」（实测踩到 get_ident）。
        src = os.path.basename(getattr(mod, "__file__", "") or "")

        def walk(code: types.CodeType) -> None:
            if src and os.path.basename(code.co_filename) != src:
                return
            for ins in dis.get_instructions(code):
                if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                    name = ins.argval
                    if isinstance(name, str) and name not in available:
                        missing.add(name)
            for const in code.co_consts:
                if isinstance(const, types.CodeType):
                    walk(const)

        for obj in vars(mod).values():
            if inspect.isfunction(obj) and getattr(obj, "__module__", None) == mod.__name__:
                walk(obj.__code__)
            elif inspect.isclass(obj) and getattr(obj, "__module__", None) == mod.__name__:
                for _, meth in inspect.getmembers(obj, inspect.isfunction):
                    if getattr(meth, "__module__", None) == mod.__name__:
                        walk(meth.__code__)
        return missing

    for mod in (question_service, admin_question):
        missing = undefined_globals(mod)
        assert not missing, (
            f"{mod.__name__} 引用了未定义的全局名：{sorted(missing)}（多半是忘了 import）"
        )


# ================================================================ 1. 章节树


def test_chapter_tree_contract(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    sid, _ = sz

    b = body(client.get(f"{API}/admin/chapters/tree", headers=admin_h))
    assert b["code"] == 0, b
    d = b["data"]
    assert d["total"] >= 27, f"章节总数异常：{d['total']}"
    assert d["subject_id"] is None
    codes = {g["subject"]["code"] for g in d["items"]}
    assert "SW-SZ" in codes, codes
    for g in d["items"]:
        assert isinstance(g["subject"]["id"], str), "subject.id 必须是字符串"
        for c in g["chapters"]:
            assert isinstance(c["id"], str)
            assert c["level"] >= 1 and isinstance(c["children"], list)

    # 传 subject_id → 只返回该科目一组
    b2 = body(client.get(f"{API}/admin/chapters/tree", headers=admin_h, params={"subject_id": sid}))
    assert b2["code"] == 0, b2
    assert b2["data"]["subject_id"] == str(sid)
    assert len(b2["data"]["items"]) == 1, b2["data"]["items"]

    in_tree = sum(c["question_count"] for c in b2["data"]["items"][0]["chapters"])
    # question_count 是实时统计：种子里 chapters.question_count 冗余列是 0，
    # 若这里 > 0 就证明接口真的去 count 了 questions，而不是读那个没刷新的列。
    assert in_tree > 0, "章节题目数全为 0：疑似读到了未刷新的冗余列"

    lst = body(
        client.get(
            f"{API}/admin/questions", headers=admin_h, params={"subject_id": sid, "page_size": 1}
        )
    )
    assert lst["code"] == 0
    assert in_tree <= lst["data"]["total"], (
        f"章节题目数({in_tree}) 不应超过该科目题目总数({lst['data']['total']})"
    )


# ================================================================ 2. 列表筛选 / 分页 / 排序


def test_question_list_filter_and_sort(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    sid, _ = sz
    params = {
        "subject_id": sid,
        "type": "single",
        "status": "published",
        "page": 1,
        "page_size": 20,
    }

    b = body(client.get(f"{API}/admin/questions", headers=admin_h, params=params))
    assert b["code"] == 0, b
    d = b["data"]
    assert d["total"] > 0, "市政 + 单选 + 已发布 筛不出题：请先 python -m app.cli seed-questions"
    assert {"items", "page", "page_size", "total", "has_more"} <= set(d)
    assert d["page"] == 1 and d["page_size"] == 20
    assert d["has_more"] is (d["total"] > 20)

    for it in d["items"]:
        assert it["type"] == "single" and it["status"] == "published"
        assert str(it["subject_id"]) == str(sid)
        assert isinstance(it["id"], str)
        assert it["option_count"] >= 2
        assert len(it["correct_labels"]) == 1, "单选题必须恰好 1 个正确答案"
        assert it["is_deleted"] is False, "默认列表不该出现软删除的题"
        assert it["version"] >= 1

    # 翻页不重叠（排序末尾用 id 兜底才做得到）
    b2 = body(client.get(f"{API}/admin/questions", headers=admin_h, params={**params, "page": 2}))
    ids1 = {i["id"] for i in d["items"]}
    ids2 = {i["id"] for i in b2["data"]["items"]}
    assert not (ids1 & ids2), "翻页出现重复项：排序缺少稳定兜底键"

    # 排序白名单生效
    asc = body(
        client.get(
            f"{API}/admin/questions",
            headers=admin_h,
            params={**params, "order_by": "difficulty", "order": "asc"},
        )
    )
    diffs = [i["difficulty"] for i in asc["data"]["items"]]
    assert diffs == sorted(diffs), diffs

    # 非白名单排序字段会被 FastAPI 拦成 40001（422）
    bad = client.get(
        f"{API}/admin/questions", headers=admin_h, params={**params, "order_by": "stem; DROP TABLE"}
    )
    assert body(bad)["code"] == 40001, body(bad)

    # 关键词命中为 0
    kw = body(
        client.get(
            f"{API}/admin/questions",
            headers=admin_h,
            params={"keyword": "绝不可能命中的中文串zzz"},
        )
    )
    assert kw["data"]["total"] == 0


# ================================================================ 3. 新建 + 回读


def test_create_single_question_and_read_back(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    sid, cid = sz
    payload = _q(subject_id=sid, chapter_id=cid)

    b = body(client.post(f"{API}/admin/questions", headers=admin_h, json=payload))
    assert b["code"] == 0, b
    d = b["data"]
    qid = d["id"]

    # ID 字符串契约
    assert isinstance(qid, str) and int(qid) > JS_MAX_SAFE_INT
    assert isinstance(d["subject_id"], str)
    assert all(isinstance(o["id"], str) for o in d["options"])

    assert d["version"] == 1, "新建的题应从 v1 起"
    assert d["type"] == "single" and d["stem"] == payload["stem"]
    assert d["subject_name"]
    assert d["chapter_id"] == str(cid) if cid else True
    assert d["is_deleted"] is False
    assert d["editable"] is True and d["can_edit"] is True and d["can_delete"] is True

    # 答案由选项推导（B=正确）
    assert d["answer"]["value"] == ["B"]
    assert d["correct_labels"] == ["B"]
    assert [o["label"] for o in d["options"]] == ["A", "B", "C", "D"]
    assert [o["is_correct"] for o in d["options"]] == [False, True, False, False]

    # 版本历史：v1 就是当前版本，快照含原文
    assert len(d["versions"]) == 1
    v1 = d["versions"][0]
    assert v1["version"] == 1 and v1["is_current"] is True
    assert v1["snapshot"]["stem"] == payload["stem"]
    assert isinstance(v1["id"], str)

    # 列表里能立刻看到
    lst = body(
        client.get(
            f"{API}/admin/questions", headers=admin_h, params={"keyword": payload["stem"][:20]}
        )
    )
    assert lst["code"] == 0
    assert lst["data"]["total"] >= 1
    hit = next(i for i in lst["data"]["items"] if i["id"] == qid)
    assert hit["correct_labels"] == ["B"]


# ================================================================ 4. 新建校验 + 去重


def test_create_validation_and_compliance_rules(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    sid, _ = sz

    def post(payload: dict) -> dict:
        r = client.post(f"{API}/admin/questions", headers=admin_h, json=payload)
        assert r.status_code in (400, 422), f"{r.status_code}: {r.text[:200]}"
        return body(r)

    # ① 单选题标两个正确答案 → 拒绝
    b = post(
        _q(
            subject_id=sid,
            options=[
                {"label": "A", "content": f"a-{uuid.uuid4().hex[:6]}", "is_correct": True},
                {"label": "B", "content": f"b-{uuid.uuid4().hex[:6]}", "is_correct": True},
                {"label": "C", "content": f"c-{uuid.uuid4().hex[:6]}", "is_correct": False},
            ],
        )
    )
    assert b["code"] == 40001, b
    assert "单选题" in b["message"], b["message"]

    # 单选题一个都没标 → 拒绝
    b = post(
        _q(
            subject_id=sid,
            options=[
                {"label": "A", "content": f"a-{uuid.uuid4().hex[:6]}", "is_correct": False},
                {"label": "B", "content": f"b-{uuid.uuid4().hex[:6]}", "is_correct": False},
            ],
        )
    )
    assert b["code"] == 40001, b

    # 多选题只标 1 个正确答案 → 拒绝（只有一个正确的请用单选）
    b = post(
        _q(
            subject_id=sid,
            type="multiple",
            options=[
                {"label": "A", "content": f"a-{uuid.uuid4().hex[:6]}", "is_correct": True},
                {"label": "B", "content": f"b-{uuid.uuid4().hex[:6]}", "is_correct": False},
                {"label": "C", "content": f"c-{uuid.uuid4().hex[:6]}", "is_correct": False},
            ],
        )
    )
    assert b["code"] == 40001, b

    # 判断题必须给 judge_answer
    b = post(_q(subject_id=sid, type="judge", options=[]))
    assert b["code"] == 40001, b

    # 合规：public 来源必须给 source_name
    b = post(_q(subject_id=sid, source_type="public"))
    assert b["code"] == 40001, b
    # 合规：authorized 还必须给 source_license
    b = post(_q(subject_id=sid, source_type="authorized", source_name="某出版社"))
    assert b["code"] == 40001, b

    # 科目不存在 → 40001
    b = post(_q(subject_id=999999999))
    assert b["code"] == 40001, b


def test_duplicate_content_conflict(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """同一题干 + 同一选项 → 归一化指纹相同 → 40901（复用 uq_questions_hash）。"""
    sid, cid = sz
    payload = _q(subject_id=sid, chapter_id=cid)

    first = body(client.post(f"{API}/admin/questions", headers=admin_h, json=payload))
    assert first["code"] == 0, first

    again = body(client.post(f"{API}/admin/questions", headers=admin_h, json=payload))
    assert again["code"] == 40901, again
    assert str(first["data"]["id"]) in again["message"] or "重复" in again["message"]


# ================================================================ 5. 编辑：乐观锁 + 版本历史


def test_edit_optimistic_lock_and_history(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    sid, cid = sz
    created = body(
        client.post(
            f"{API}/admin/questions", headers=admin_h, json=_q(subject_id=sid, chapter_id=cid)
        )
    )["data"]
    qid, v1_stem = created["id"], created["stem"]

    # 过期 version 提交 → 40901（不静默覆盖）
    stale = body(
        client.put(
            f"{API}/admin/questions/{qid}", headers=admin_h, json={"version": 99, "stem": "占位"}
        )
    )
    assert stale["code"] == 40901, stale

    # 正确 version=1：改题干、难度，并把正确答案从 B 换成 C
    new_stem = f"{v1_stem}（已修订）"
    tag = uuid.uuid4().hex[:8]
    edited = body(
        client.put(
            f"{API}/admin/questions/{qid}",
            headers=admin_h,
            json={
                "version": 1,
                "stem": new_stem,
                "difficulty": 5,
                "options": [
                    {"label": "A", "content": f"A-{tag}", "is_correct": False},
                    {"label": "B", "content": f"B-{tag}", "is_correct": False},
                    {"label": "C", "content": f"C-{tag}", "is_correct": True},
                    {"label": "D", "content": f"D-{tag}", "is_correct": False},
                ],
            },
        )
    )
    assert edited["code"] == 0, edited
    d2 = edited["data"]
    assert d2["version"] == 2, "保存后版本号必须 +1"
    assert d2["stem"] == new_stem and d2["difficulty"] == 5
    assert d2["correct_labels"] == ["C"], "答案随选项推导更新"
    assert d2["answer"]["value"] == ["C"]

    # 历史保留 v1 原文（只读），当前版本是 v2
    vers = {v["version"]: v for v in d2["versions"]}
    assert set(vers) == {1, 2}, vers.keys()
    assert vers[1]["is_current"] is False
    assert vers[1]["snapshot"]["stem"] == v1_stem, "v1 快照应保留修订前的原文"
    assert vers[2]["is_current"] is True
    assert vers[2]["snapshot"]["stem"] == new_stem
    assert vers[1]["operator_name"]

    # 再用过期 version=1 提交 → 40901
    stale2 = body(
        client.put(
            f"{API}/admin/questions/{qid}", headers=admin_h, json={"version": 1, "stem": "过期提交"}
        )
    )
    assert stale2["code"] == 40901, stale2

    # 不存在的题 → 40401
    nf = client.put(
        f"{API}/admin/questions/999999999999999999", headers=admin_h, json={"version": 1}
    )
    assert nf.status_code == 404 and body(nf)["code"] == 40401


# ================================================================ 6. 软删除


def test_soft_delete_single(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    sid, cid = sz
    created = body(
        client.post(
            f"{API}/admin/questions", headers=admin_h, json=_q(subject_id=sid, chapter_id=cid)
        )
    )["data"]
    qid = created["id"]
    kw = {"keyword": created["stem"][:20]}

    assert (
        body(client.get(f"{API}/admin/questions", headers=admin_h, params=kw))["data"]["total"] >= 1
    )

    dele = body(
        client.delete(
            f"{API}/admin/questions/{qid}", headers=admin_h, params={"reason": "自动化用例软删除"}
        )
    )
    assert dele["code"] == 0, dele
    assert dele["data"]["is_deleted"] is True
    assert dele["data"]["version"] == 2, "软删除也推进版本号"

    # 默认列表隐藏
    assert (
        body(client.get(f"{API}/admin/questions", headers=admin_h, params=kw))["data"]["total"] == 0
    )

    # 「显示已归档」带出来
    shown = body(
        client.get(
            f"{API}/admin/questions", headers=admin_h, params={**kw, "include_deleted": True}
        )
    )
    assert shown["data"]["total"] >= 1
    row = next(i for i in shown["data"]["items"] if i["id"] == qid)
    assert row["is_deleted"] is True

    # 详情仍可查看（详情页要展示「这条已归档」）
    detail = body(client.get(f"{API}/admin/questions/{qid}", headers=admin_h))
    assert detail["code"] == 0 and detail["data"]["is_deleted"] is True

    # 已归档不可编辑
    assert (
        body(
            client.put(
                f"{API}/admin/questions/{qid}", headers=admin_h, json={"version": 2, "stem": "想改"}
            )
        )["code"]
        == 40001
    )

    # 重复删除 → 40001；不存在 → 40401
    assert body(client.delete(f"{API}/admin/questions/{qid}", headers=admin_h))["code"] == 40001
    nf = client.delete(f"{API}/admin/questions/999999999999999999", headers=admin_h)
    assert nf.status_code == 404 and body(nf)["code"] == 40401


# ================================================================ 7. 批量删除


def test_batch_delete(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    sid, cid = sz
    ids = [
        body(
            client.post(
                f"{API}/admin/questions", headers=admin_h, json=_q(subject_id=sid, chapter_id=cid)
            )
        )["data"]["id"]
        for _ in range(3)
    ]
    ghost = "999999999999999998"

    # 先单独删掉一条，制造「已是删除态」的跳过场景
    assert body(client.delete(f"{API}/admin/questions/{ids[0]}", headers=admin_h))["code"] == 0

    # ids[1] 故意重复一次，验证去重
    payload_ids = [int(ids[0]), int(ids[1]), int(ids[1]), int(ids[2]), int(ghost)]
    r = body(
        client.post(
            f"{API}/admin/questions/batch-delete",
            headers=admin_h,
            json={"ids": payload_ids, "reason": "自动化批量删除"},
        )
    )
    assert r["code"] == 0, r
    d = r["data"]
    assert d["deleted"] == 2, f"应删除 2 条（去重 + 跳过已删除/不存在），实际 {d}"
    assert set(d["skipped"]) == {ids[0], ghost}, d["skipped"]
    assert isinstance(d["batch_id"], str)

    # 两条确实已软删除
    for qid in ids[1:]:
        detail = body(client.get(f"{API}/admin/questions/{qid}", headers=admin_h))
        assert detail["data"]["is_deleted"] is True

    # 整批共用 batch_id（直接查 content_change_logs 才能验到）
    rows = db_fetch(
        "SELECT entity_id, batch_id FROM content_change_logs "
        "WHERE entity_type = 'question' AND action = 'delete' "
        "AND entity_id = ANY(CAST($1 AS bigint[]))",
        [int(x) for x in ids[1:]],
    )
    batch_ids = {str(r["batch_id"]) for r in rows}
    assert len(rows) == 2, rows
    assert batch_ids == {d["batch_id"]}, (batch_ids, d["batch_id"])

    # 空 ids → 40001
    empty = client.post(f"{API}/admin/questions/batch-delete", headers=admin_h, json={"ids": []})
    assert body(empty)["code"] == 40001


# ================================================================ 8. 变更日志 / 版本快照（直连核对）


def test_change_logs_and_version_snapshots_in_db(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    sid, cid = sz
    created = body(
        client.post(
            f"{API}/admin/questions", headers=admin_h, json=_q(subject_id=sid, chapter_id=cid)
        )
    )["data"]
    qid = int(created["id"])

    # 编辑一次，制造 update 日志
    body(
        client.put(
            f"{API}/admin/questions/{created['id']}",
            headers=admin_h,
            json={"version": 1, "analysis": "自动化用例补写解析"},
        )
    )

    logs = db_fetch(
        "SELECT action, diff, change_log FROM content_change_logs "
        "WHERE entity_type = 'question' AND entity_id = $1 ORDER BY created_at",
        qid,
    )
    actions = [r["action"] for r in logs]
    assert "create" in actions and "update" in actions, actions

    upd = next(r for r in logs if r["action"] == "update")
    diff = upd["diff"]
    if isinstance(diff, str):
        diff = json.loads(diff)
    assert set(diff) >= {"before", "after"}, diff
    assert "analysis" in diff["after"] or "analysis" in diff["before"], diff

    vers = db_fetch(
        "SELECT version, snapshot FROM question_versions WHERE question_id = $1 ORDER BY version",
        qid,
    )
    assert [r["version"] for r in vers] == [1, 2]
    snap1 = vers[0]["snapshot"]
    if isinstance(snap1, str):
        snap1 = json.loads(snap1)
    assert snap1["stem"] == created["stem"], "v1 快照必须保留创建时的题干"


# ================================================================ 9. 权限墙


def test_question_permission_wall(client: httpx.Client, sz) -> None:
    """student 角色没有任何 question:* 权限 → 读写全被拦（前端按钮 disabled 对应的后端那道墙）。"""
    sid, _ = sz
    stu = register(client, nickname="题库越权用例")
    h = auth(stu["access_token"])

    assert body(client.get(f"{API}/admin/questions", headers=h))["code"] == 40301
    assert body(client.get(f"{API}/admin/questions/1", headers=h))["code"] == 40301
    assert body(client.get(f"{API}/admin/chapters/tree", headers=h))["code"] == 40301
    assert (
        body(client.post(f"{API}/admin/questions", headers=h, json=_q(subject_id=sid)))["code"]
        == 40301
    )
    assert (
        body(client.put(f"{API}/admin/questions/1", headers=h, json={"version": 1}))["code"]
        == 40301
    )
    assert body(client.delete(f"{API}/admin/questions/1", headers=h))["code"] == 40301
    assert (
        body(client.post(f"{API}/admin/questions/batch-delete", headers=h, json={"ids": [1]}))[
            "code"
        ]
        == 40301
    )

    # 无 Token → 401（40100 = 未携带凭据）
    assert body(client.get(f"{API}/admin/questions"))["code"] in (40100, 40101)


# ================================================================ 8. 数据范围收口（题目全部入口）
#
# 收口前只有 `list_questions` 套了数据范围过滤 —— **列表看不见，但照着 id 直接请求
# 就能读到、改到、删掉别的科目的题**。"列表过滤"防的是"翻到"，防不了"猜到"。
#
# 本组把八条入口逐个钉住。用 `researcher` 而不是 `viewer`：种子里 researcher 拿满了
# `question` 模块的 read/create/update/delete，八条入口它**全部打得通**，
# 正是"越权"要测的那个面（viewer 连 `question:read` 都没有，测不出范围这件事）。

SZ_SUBJECT_ID = 2007  # 市政实务
JZ_SUBJECT_ID = 2001  # 建筑实务（越权对照）


def _scoped_headers(
    client: httpx.Client,
    admin_h: dict[str, str],
    *,
    nickname: str,
    scope_type: str = "subject",
    scope_id: int | None = SZ_SUBJECT_ID,
) -> dict[str, str]:
    """造一个只挂单个科目范围的 researcher，返回认证头。"""
    u = fresh_user(client, nickname=nickname)
    ar = assign_roles(
        client,
        admin_h,
        u["user"]["id"],
        ["researcher"],
        scope_type=scope_type,
        scope_id=scope_id,
    )
    assert ar["code"] == 0, ar
    return auth(u["access_token"])


def _exists(client: httpx.Client, admin_h: dict[str, str], tag: str) -> bool:
    """按内容里的唯一 tag 查这道题在不在（admin 视角，不受范围影响）。"""
    b = body(
        client.get(
            f"{API}/admin/questions",
            headers=admin_h,
            params={"keyword": tag, "page_size": 5},
        )
    )
    assert b["code"] == 0, b
    return b["data"]["total"] > 0


def _tag_of(payload: dict) -> str:
    """取出 `_q` 放在题干开头的那个唯一 tag（`【自动化】<tag> 关于施工…`）。

    ⚠️ 不能写 `stem.split()[1]` —— 那是**题干后半句**（所有题都一样），
    拿它去 `keyword` 查会把全库的题都命中，于是"越权新建没落库"这个断言永远为假。
    """
    return payload["stem"].split(" ", 1)[0].replace("【自动化】", "")


def _make(
    client: httpx.Client,
    admin_h: dict[str, str],
    *,
    subject_id: int,
    chapter_id: int | None = None,
) -> tuple[str, str]:
    """建一道题，返回 `(id, tag)`。tag 用来按内容定位，不依赖 id 之外的线索。"""
    payload = _q(subject_id=subject_id, chapter_id=chapter_id)
    b = body(client.post(f"{API}/admin/questions", headers=admin_h, json=payload))
    assert b["code"] == 0, b
    return b["data"]["id"], _tag_of(payload)


def _drop(client: httpx.Client, admin_h: dict[str, str], qid: str) -> None:
    """软删清场（已删的再删返回 40001，忽略即可）。"""
    body(client.delete(f"{API}/admin/questions/{qid}", headers=admin_h))


def test_scope_detail_wall(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """**详情**：按 id 直接请求别的科目的题 → `40301`。

    报 `40301` 而不是 `40401`：题确实存在，只是不归你 —— 报 404 会让排查的人
    以为 id 写错了，去找一个并不存在的问题。
    """
    sz_id, sz_chapter = sz
    jz_qid, _ = _make(client, admin_h, subject_id=JZ_SUBJECT_ID)
    my_qid, _ = _make(client, admin_h, subject_id=sz_id, chapter_id=sz_chapter)
    h = _scoped_headers(client, admin_h, nickname="范围-详情")

    b = body(client.get(f"{API}/admin/questions/{my_qid}", headers=h))
    assert b["code"] == 0, b
    assert b["data"]["subject_id"] == str(sz_id), b["data"]["subject_id"]

    b = body(client.get(f"{API}/admin/questions/{jz_qid}", headers=h))
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    _drop(client, admin_h, my_qid)
    _drop(client, admin_h, jz_qid)


def test_scope_create_wall(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """**新建**：往别科目建题 → `40301`，且**库里一道都没多**（零副作用）。"""
    sz_id, sz_chapter = sz
    h = _scoped_headers(client, admin_h, nickname="范围-新建")

    payload = _q(subject_id=JZ_SUBJECT_ID)
    tag = _tag_of(payload)
    b = body(client.post(f"{API}/admin/questions", headers=h, json=payload))
    assert b["code"] == 40301 and "数据范围" in b["message"], b
    # 关键：不是"先写进去再报错"
    assert not _exists(client, admin_h, tag), "越权新建居然落了库"

    ok_payload = _q(subject_id=sz_id, chapter_id=sz_chapter)
    b = body(client.post(f"{API}/admin/questions", headers=h, json=ok_payload))
    assert b["code"] == 0, b
    _drop(client, admin_h, b["data"]["id"])


def test_scope_update_wall_including_moving_subject(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """**编辑**：改别科目的题 → 40301；把自己科目题**搬到**别科目 → 也 40301。

    后者不能漏 —— 否则可以把题"搬进"一个自己没被授权的科目，
    等于绕过新建时的那道闸。
    """
    sz_id, sz_chapter = sz
    jz_qid, _ = _make(client, admin_h, subject_id=JZ_SUBJECT_ID)
    my_qid, _ = _make(client, admin_h, subject_id=sz_id, chapter_id=sz_chapter)
    h = _scoped_headers(client, admin_h, nickname="范围-编辑")

    # ---- 改别科目的题 ----
    detail = body(client.get(f"{API}/admin/questions/{jz_qid}", headers=admin_h))["data"]
    b = body(
        client.put(
            f"{API}/admin/questions/{jz_qid}",
            headers=h,
            json={"version": detail["version"], "stem": "越权改的题干"},
        )
    )
    assert b["code"] == 40301 and "数据范围" in b["message"], b
    after = body(client.get(f"{API}/admin/questions/{jz_qid}", headers=admin_h))["data"]
    assert after["stem"] == detail["stem"], "越权编辑居然改了内容"

    # ---- 把自己的题搬去别的科目 ----
    mine = body(client.get(f"{API}/admin/questions/{my_qid}", headers=admin_h))["data"]
    b = body(
        client.put(
            f"{API}/admin/questions/{my_qid}",
            headers=h,
            json={"version": mine["version"], "subject_id": JZ_SUBJECT_ID},
        )
    )
    assert b["code"] == 40301 and "目标科目" in b["message"], b
    after = body(client.get(f"{API}/admin/questions/{my_qid}", headers=admin_h))["data"]
    assert after["subject_id"] == str(sz_id), "题被搬走了"

    # 本科目内改动照常
    b = body(
        client.put(
            f"{API}/admin/questions/{my_qid}",
            headers=h,
            json={"version": mine["version"], "stem": mine["stem"] + "（教研改）"},
        )
    )
    assert b["code"] == 0, b

    _drop(client, admin_h, my_qid)
    _drop(client, admin_h, jz_qid)


def test_scope_delete_and_restore_wall(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """**删除 / 恢复**：两条都要拦，且恢复的**幂等分支也拦得住**。

    幂等分支（"本来就没删"→ 200 + already_active）必须排在范围闸**之后** ——
    否则越权者能靠"这道题没被删"拿到一个 200，等于用返回值确认了对象存在。
    """
    sz_id, sz_chapter = sz
    jz_qid, _ = _make(client, admin_h, subject_id=JZ_SUBJECT_ID)
    h = _scoped_headers(client, admin_h, nickname="范围-删恢复")

    b = body(client.delete(f"{API}/admin/questions/{jz_qid}", headers=h))
    assert b["code"] == 40301 and "数据范围" in b["message"], b
    assert body(client.get(f"{API}/admin/questions/{jz_qid}", headers=admin_h))["code"] == 0, (
        "越权删除居然成功了"
    )

    # 未删态的幂等分支：也不许提前返回 200
    b = body(client.post(f"{API}/admin/questions/{jz_qid}/restore", headers=h, json={}))
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    # 已删态
    _drop(client, admin_h, jz_qid)
    b = body(client.post(f"{API}/admin/questions/{jz_qid}/restore", headers=h, json={}))
    assert b["code"] == 40301 and "数据范围" in b["message"], b
    detail = body(client.get(f"{API}/admin/questions/{jz_qid}", headers=admin_h))["data"]
    assert detail["is_deleted"] is True, "越权恢复居然成功了"

    # 自己科目的题，删/恢复都正常
    my_qid, _ = _make(client, admin_h, subject_id=sz_id, chapter_id=sz_chapter)
    assert body(client.delete(f"{API}/admin/questions/{my_qid}", headers=h))["code"] == 0
    assert (
        body(client.post(f"{API}/admin/questions/{my_qid}/restore", headers=h, json={}))["code"]
        == 0
    )
    _drop(client, admin_h, my_qid)


def test_scope_batch_delete_is_all_or_nothing(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """**批量删除**：混入一道越权的 → **整批 40301**，不是"把那条塞进 skipped"。

    这是本组最容易被做成"看起来更友好、实际更弱"的一处：若把越权降级成 `skipped`，
    批量入口就比单条入口（40301）**更容易绕过**，而且越权尝试会被照常写进审计日志
    当作正常操作。判据（硬约定 C）：要不要"放过"看目标状态是否已达成 —— 越权不满足。
    """
    sz_id, sz_chapter = sz
    my_qid, _ = _make(client, admin_h, subject_id=sz_id, chapter_id=sz_chapter)
    jz_qid, _ = _make(client, admin_h, subject_id=JZ_SUBJECT_ID)
    h = _scoped_headers(client, admin_h, nickname="范围-批量删")

    b = body(
        client.post(
            f"{API}/admin/questions/batch-delete", headers=h, json={"ids": [my_qid, jz_qid]}
        )
    )
    assert b["code"] == 40301, b
    assert "整批未执行" in b["message"] and jz_qid in b["message"], b["message"]
    for qid in (my_qid, jz_qid):
        d = body(client.get(f"{API}/admin/questions/{qid}", headers=admin_h))["data"]
        assert d["is_deleted"] is False, f"{qid} 被误删了 —— 整批拒绝必须零副作用"

    # 只传自己的 → 正常
    b = body(client.post(f"{API}/admin/questions/batch-delete", headers=h, json={"ids": [my_qid]}))
    assert b["code"] == 0 and b["data"]["deleted"] == 1, b

    # 只传越权的 → 也是 40301（哪怕整批只有一条越权）
    b = body(client.post(f"{API}/admin/questions/batch-delete", headers=h, json={"ids": [jz_qid]}))
    assert b["code"] == 40301, b

    _drop(client, admin_h, jz_qid)


def test_scope_dropdowns_are_filtered(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """**下拉数据源**（章节树 / 知识点）：只列有权限的科目；显式要范围外的 → 40301。

    下拉是"能选什么"的清单 —— 把没权限的科目摆上去，等于让用户点一个注定 403 的选项。
    """
    h = _scoped_headers(client, admin_h, nickname="范围-下拉")

    b = body(client.get(f"{API}/admin/chapters/tree", headers=h))
    assert b["code"] == 0, b
    codes = {g["subject"]["code"] for g in b["data"]["items"]}
    assert codes == {"SW-SZ"}, f"教研看到了别科目的章节树分组：{codes}"

    b = body(
        client.get(f"{API}/admin/chapters/tree", headers=h, params={"subject_id": JZ_SUBJECT_ID})
    )
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    assert (
        body(
            client.get(
                f"{API}/admin/chapters/tree", headers=h, params={"subject_id": SZ_SUBJECT_ID}
            )
        )["code"]
        == 0
    )

    # ---- 知识点：同理 ----
    b = body(client.get(f"{API}/admin/chapters/knowledge-points", headers=h))
    assert b["code"] == 0, b
    subs = {x["subject_id"] for x in b["data"]["items"]}
    assert subs <= {str(SZ_SUBJECT_ID)}, f"教研看到了别科目的知识点：{subs}"

    b = body(
        client.get(
            f"{API}/admin/chapters/knowledge-points",
            headers=h,
            params={"subject_id": JZ_SUBJECT_ID},
        )
    )
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    # 对照组：超管不受影响（收口不能把全局岗也一起收掉）
    b = body(client.get(f"{API}/admin/chapters/tree", headers=admin_h))
    assert b["code"] == 0 and len(b["data"]["items"]) >= 2, (
        f"超管应当看到全部科目分组，实际 {len(b['data']['items'])} 组"
    )


def test_scope_fails_closed_for_unmapped_scope_type(
    client: httpx.Client, admin_h: dict[str, str], sz
) -> None:
    """只挂**未映射**的 `professional` 范围 → 可见集合是**空集**，全拦。

    这是"失败关闭"最容易被误实现成"失败开启"的点：`scope_subject_ids` 对
    professional/course 返回**空集合**（不是 `None`）。`None` 的语义是"不限制"，
    两者搞混就等于给未映射的范围**放开全部**。
    """
    sz_id, sz_chapter = sz
    my_qid, _ = _make(client, admin_h, subject_id=sz_id, chapter_id=sz_chapter)
    h = _scoped_headers(
        client, admin_h, nickname="范围-未映射", scope_type="professional", scope_id=SZ_SUBJECT_ID
    )

    b = body(client.get(f"{API}/admin/questions", headers=h, params={"page_size": 5}))
    assert b["code"] == 0 and b["data"]["total"] == 0, (
        f"未映射范围应当收敛到空集（失败关闭），实际看到 {b['data'].get('total')} 条"
    )

    b = body(client.get(f"{API}/admin/questions/{my_qid}", headers=h))
    assert b["code"] == 40301 and "数据范围" in b["message"], b

    b = body(client.get(f"{API}/admin/chapters/tree", headers=h))
    assert b["code"] == 0 and b["data"]["items"] == [], b["data"]

    _drop(client, admin_h, my_qid)


def test_scope_global_role_unrestricted(client: httpx.Client, admin_h: dict[str, str], sz) -> None:
    """对照组：`global` 范围**不受任何限制**。"""
    jz_qid, _ = _make(client, admin_h, subject_id=JZ_SUBJECT_ID)
    b = body(client.get(f"{API}/admin/questions/{jz_qid}", headers=admin_h))
    assert b["code"] == 0, b
    _drop(client, admin_h, jz_qid)
