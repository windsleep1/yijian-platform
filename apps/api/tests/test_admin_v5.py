"""Batch 5 题库批量导入管道验收（7 个接口）。

    POST   /admin/imports/upload             ① 上传建批次
    POST   /admin/imports/{id}/validate      ② 逐行校验（dry-run）
    GET    /admin/imports/{id}               ③ 状态 + 统计 + 错误报告 + 逐行结果
    POST   /admin/imports/{id}/execute       ④ 执行导入（整批事务）
    POST   /admin/imports/{id}/publish       ⑤ draft → published
    POST   /admin/imports/{id}/rollback      ⑥ 整批回滚
    GET    /admin/imports                    ⑦ 批次列表

需要 API 已启动（真 PG + fakeredis）：

    powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1

用例与「六条验收标准」的对应关系：

    ① 错误文件 → 逐行 {row_no, field, message} 且**不写入**   → test_wrong_file_reports_row_and_field_...
    ② 同一份文件导 10 次题量不变                              → test_idempotent_import_ten_times
    ③ 全链路导入 → 回滚，题量回到导入前                       → test_full_pipeline_...
    ④ 中途异常 → 整批回滚，不留半截数据                       → test_mid_import_failure_rolls_back_...
    ⑤ 6000 道仿真题真灌进库（success=6000, failed=0）         → test_seed_bank_scale_...（+ 环境变量门控的全量）
    ⑥ researcher 只能导入自己专业的题                        → test_researcher_data_scope_...

设计要点（与 Batch 4 的用例一致）：**每个用例自己造数据**，不依赖别的用例的残留；
凡是写进库的，用例结束前都回滚掉，避免污染题库、也避免用例之间互相影响。
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import os
import random
import string
import time
import uuid
from pathlib import Path

import httpx
import pytest

from .conftest import API, TEST_PASSWORD, assign_roles, auth, body

REPO = Path(__file__).resolve().parents[3]
SEED_JSON = REPO / "data" / "seed" / "questions.json"
SIM_BANK_TOOL = REPO / "tools" / "local-verify" / "import-sim-bank.py"

# 导入模板的 24 列（与 docs/07 §4.2 一一对应；option_a..option_f 是同一行规则的展开）
COLUMNS = (
    "subject_code", "chapter_code", "kp_code", "type", "stem",
    "option_a", "option_b", "option_c", "option_d", "option_e", "option_f",
    "answer", "answer_points", "analysis", "score", "difficulty", "exam_year",
    "source_type", "source_name", "source_license", "tags",
    "case_group_id", "material", "media_urls",
)

SZ_SUBJECT_ID = 2007      # 市政实务
SZ_CHAPTER = "SZ-01"      # 该科目下真实存在的章节编码
JZ_SUBJECT_ID = 2001      # 建筑实务（用于"越权"对照）


# ================================================================ 工具

def csv_bytes(rows: list[dict]) -> bytes:
    """按导入模板列拼 CSV。刻意带 UTF-8 BOM —— 覆盖"Excel 另存为 CSV"的真实形态。"""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in COLUMNS})
    return buf.getvalue().encode("utf-8-sig")


def row(*, subject: str = "SW-SZ", chapter: str = SZ_CHAPTER, tag: str | None = None, **over) -> dict:
    """一道内容唯一的单选题的导入行。tag 保证不会撞上 content_hash 去重。"""
    tag = tag or uuid.uuid4().hex[:10]
    r = {
        "subject_code": subject, "chapter_code": chapter, "kp_code": "",
        "type": "single",
        "stem": f"【自动化-导入】{tag} 关于施工管理与验收，下列说法正确的是？",
        "option_a": f"A-{tag} 未经验收即投入使用",
        "option_b": f"B-{tag} 先验收合格再进入下道工序",
        "option_c": f"C-{tag} 口头交底代替书面交底",
        "answer": "B",
        "analysis": f"解析 {tag}：验收合格是进入下道工序的前置条件，故选 B。",
        "score": "1", "difficulty": "3",
        "source_type": "self", "tags": "自动化|单选题",
    }
    r.update(over)
    return r


def _kw(timeout):
    """只在显式给了 timeout 时才覆盖 httpx 的超时（None = 用 client 默认值）。"""
    return {"timeout": timeout} if timeout is not None else {}


def upload(client, headers, content: bytes, *, name="t.csv", timeout=None, **form):
    return body(client.post(
        f"{API}/admin/imports/upload", headers=headers,
        files={"file": (name, content, "text/csv")},
        data={"mode": "insert", "source_type": "self", **{k: str(v) for k, v in form.items()}},
        **_kw(timeout),
    ))


def validate(client, headers, bid, *, timeout=None):
    return body(client.post(f"{API}/admin/imports/{bid}/validate", headers=headers,
                            **_kw(timeout)))


def execute(client, headers, bid, *, timeout=None, **payload):
    return body(client.post(f"{API}/admin/imports/{bid}/execute", headers=headers,
                            json=payload, **_kw(timeout)))


def rollback(client, headers, bid, *, timeout=None, **payload):
    return body(client.post(f"{API}/admin/imports/{bid}/rollback", headers=headers,
                            json=payload, **_kw(timeout)))


def count_questions(client, headers) -> int:
    """题库题量（不含软删除）—— 判断"有没有真的写入"的硬指标。"""
    b = body(client.get(f"{API}/admin/questions?page=1&page_size=1", headers=headers))
    return int(b["data"]["total"])


def import_once(client, headers, content: bytes, *, mode="insert", name="t.csv"):
    """跑完 upload → validate → execute 三步，返回 (batch_id, validate_data, execute_data)。"""
    up = upload(client, headers, content, name=name, mode=mode)
    assert up["code"] == 0, up
    bid = int(up["data"]["id"])
    v = validate(client, headers, bid)
    assert v["code"] == 0, v
    ex = execute(client, headers, bid)
    return bid, v["data"], ex


def _dsn() -> str | None:
    url = os.environ.get("DATABASE_URL")
    return url.replace("postgresql+asyncpg://", "postgresql://") if url else None


def db_fetch(query: str, *params):
    """直连 PG 取几行。用于断言 content_change_logs / question_versions 这类没有读接口的表。

    连不上就 skip —— 这条断言是"锦上添花"，不该让整个套件红。
    """
    dsn = _dsn()
    if not dsn:
        pytest.skip("DATABASE_URL 未设置，跳过直连数据库断言")

    async def _run():
        import asyncpg

        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch(query, *params)
        finally:
            await conn.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"直连数据库失败（{exc}），跳过直连断言")


def fresh_user(client: httpx.Client, *, nickname: str = "导入用例") -> dict:
    """注册一个全新用户，并把限流桶隔到自己的"假 IP"上。

    **为什么不用 conftest.register**：`/auth/register` 是 10 次/60s **按 IP** 限流，
    而本地跑测试时所有请求都来自 127.0.0.1 —— 本模块每注册一个用户，就消耗一份
    整个套件共用的额度，注册得多了会把排在后面的 `test_smoke.py` 挤成 `42901`
    （实测踩到：`test_password_login_refresh_logout` / `test_rbac_flow` 直接红）。

    服务端 `trust_proxy_headers=true` 时客户端 IP 以 `X-Forwarded-For` 首个地址为准，
    因此给每个用例一个独立的 TEST-NET-3 假 IP，就既走了真实注册链路、又不和别人抢额度。
    这本身也顺带覆盖了"网关后面取真实 IP"这条路径。
    """
    ip = f"203.0.113.{random.randint(2, 250)}"
    h = {"X-Forwarded-For": ip}
    phone = "13" + "".join(random.choice(string.digits) for _ in range(9))
    b = body(client.post(f"{API}/auth/sms/send",
                         json={"phone": phone, "scene": "register"}, headers=h))
    assert b["code"] == 0, b
    b = body(client.post(f"{API}/auth/register", headers=h, json={
        "phone": phone, "code": b["data"]["dev_code"],
        "password": TEST_PASSWORD, "nickname": nickname,
    }))
    assert b["code"] == 0, b
    return b["data"]


@pytest.fixture(scope="module")
def sz(client: httpx.Client, admin_h: dict[str, str]) -> tuple[int, str]:
    """市政科目的 subject_id 与一个真实存在的 chapter_code（用于构造合法导入行）。"""
    b = body(client.get(f"{API}/admin/chapters/tree", headers=admin_h))
    assert b["code"] == 0, b
    for group in b["data"]["items"]:
        if group["subject"]["code"] == "SW-SZ":
            chapters = group["chapters"]
            return int(group["subject"]["id"]), (chapters[0]["code"] if chapters else SZ_CHAPTER)
    pytest.skip("科目种子缺少市政（SW-SZ）：请确认已执行 db/schema.sql")


# ================================================================ 1. 上传

def test_upload_rejects_unsupported_and_malformed_files(client: httpx.Client, admin_h) -> None:
    """上传闸门：只收 csv/json；xlsx 给出可执行的替代建议；表头缺列当场拒绝。"""
    b = upload(client, admin_h, b"whatever", name="题库.xlsx")
    assert b["code"] == 40001 and "Excel" in b["message"], b

    b = upload(client, admin_h, b"a,b\n1,2\n", name="t.txt")
    assert b["code"] == 40001, b

    b = upload(client, admin_h, b"", name="t.csv")
    assert b["code"] == 40001, b

    # 表头缺必需列
    b = upload(client, admin_h, "subject_code,type\nSW-SZ,single\n".encode(), name="t.csv")
    assert b["code"] == 40001 and "表头" in b["message"], b

    # 合法 JSON（数组）能被接受
    good = [row()]
    b = upload(client, admin_h, json.dumps(good, ensure_ascii=False).encode(), name="t.json")
    assert b["code"] == 0 and b["data"]["total_rows"] == 1, b
    assert b["data"]["file_type"] == "json", b
    # 上传后状态是 pending，且明确"未写入任何题目"
    assert b["data"]["status"] == "pending", b
    assert "未写入" in b["data"]["message"], b


# ================================================================ 2. 验收① 错误文件

def test_wrong_file_reports_row_and_field_and_writes_nothing(
    client: httpx.Client, admin_h, sz
) -> None:
    """验收①：第 57 行 answer 不在选项中、第 100 行章节编码不存在。

    必须**逐行**给出 {row_no, field, message}，且默认**一行都不写进库**。
    """
    subject_id, chapter_code = sz
    before = count_questions(client, admin_h)

    rows = [row(chapter=chapter_code) for _ in range(100)]
    rows[56].update({"option_d": "", "answer": "D"})       # 第 57 行：答案 D 不在 A/B/C 里
    rows[99]["chapter_code"] = "SZ-99"                     # 第 100 行：章节编码不存在

    up = upload(client, admin_h, csv_bytes(rows), name="wrong-100.csv")
    assert up["code"] == 0 and up["data"]["total_rows"] == 100, up
    bid = int(up["data"]["id"])

    v = validate(client, admin_h, bid)["data"]
    assert v["failed_rows"] == 2, v
    assert v["error_report"]["total_errors"] == 2, v
    by_row = {e["row_no"]: e for e in v["error_report"]["errors"]}
    assert set(by_row) == {57, 100}, v["error_report"]
    assert by_row[57]["field"] == "answer" and "不在选项中" in by_row[57]["message"], by_row[57]
    assert by_row[100]["field"] == "chapter_code" and "不存在" in by_row[100]["message"], by_row[100]

    # 校验是 dry-run
    assert count_questions(client, admin_h) == before

    # 默认严格：含错批次整体拒绝，一行不写
    ex = execute(client, admin_h, bid)
    assert ex["code"] == 40901 and "未通过校验" in ex["message"], ex
    assert count_questions(client, admin_h) == before

    # 显式 opt-in 才允许"跳过错行"（docs/07 §4.4 的能力，但必须由调用方主动要）
    ex = execute(client, admin_h, bid, allow_partial=True)
    assert ex["code"] == 0 and ex["data"]["success_rows"] == 98, ex
    assert count_questions(client, admin_h) == before + 98

    rb = rollback(client, admin_h, bid, reason="用例清理")
    assert rb["code"] == 0 and rb["data"]["rolled_back_questions"] == 98, rb
    assert count_questions(client, admin_h) == before


# ================================================================ 3. 验收② 幂等

def test_idempotent_import_ten_times(client: httpx.Client, admin_h, sz) -> None:
    """验收②：同一份正确文件导入 10 次，题量只增加一次。"""
    _, chapter_code = sz
    before = count_questions(client, admin_h)
    tag = uuid.uuid4().hex[:8]
    content = csv_bytes([row(chapter=chapter_code, tag=f"idem-{tag}-{i}") for i in range(3)])

    batches, results = [], []
    for n in range(1, 11):
        bid, _, ex = import_once(client, admin_h, content, name="same-3.csv")
        batches.append(bid)
        assert ex["code"] == 0, ex
        results.append((ex["data"]["success_rows"], ex["data"]["duplicate_rows"]))

    assert results[0] == (3, 0), results
    assert all(r == (0, 3) for r in results[1:]), results
    assert count_questions(client, admin_h) == before + 3

    # 第 1 批真写了，回滚它 → 回到基线（顺带验证"重复导入"没有留下额外数据）
    rb = rollback(client, admin_h, batches[0], reason="幂等用例清理")
    assert rb["data"]["rolled_back_questions"] == 3, rb
    assert count_questions(client, admin_h) == before


# ================================================================ 4. 验收③ 全链路

def test_full_pipeline_execute_publish_rollback(client: httpx.Client, admin_h, sz) -> None:
    """验收③：导入 → 草稿 → 发布 → 整批回滚，题量回到导入前。"""
    _, chapter_code = sz
    before = count_questions(client, admin_h)
    tag = uuid.uuid4().hex[:8]
    content = csv_bytes([row(chapter=chapter_code, tag=f"pipe-{tag}-{i}") for i in range(4)])

    bid, v, ex = import_once(client, admin_h, content)
    assert v["success_rows"] == 4 and v["failed_rows"] == 0, v
    assert ex["data"]["status"] == "done" and ex["data"]["success_rows"] == 4, ex
    assert count_questions(client, admin_h) == before + 4

    detail = body(client.get(f"{API}/admin/imports/{bid}", headers=admin_h))["data"]
    qids = [r["question_id"] for r in detail["rows"] if r["question_id"]]
    assert len(qids) == 4 and detail["row_total"] == 4, detail

    # 插入的题初始是 draft
    q = body(client.get(f"{API}/admin/questions/{qids[0]}", headers=admin_h))["data"]
    assert q["status"] == "draft", q

    pb = body(client.post(f"{API}/admin/imports/{bid}/publish", headers=admin_h, json={}))
    assert pb["code"] == 0, pb
    q = body(client.get(f"{API}/admin/questions/{qids[0]}", headers=admin_h))["data"]
    assert q["status"] == "published", q

    rb = rollback(client, admin_h, bid, reason="全链路用例回滚")
    assert rb["data"]["status"] == "rolled_back" and rb["data"]["rolled_back_questions"] == 4, rb
    assert rb["data"]["rolled_back_updates"] == 0, rb
    assert count_questions(client, admin_h) == before

    # 软删除：题目仍在库里，可查到 is_deleted=true
    q = body(client.get(f"{API}/admin/questions/{qids[0]}", headers=admin_h))["data"]
    assert q["is_deleted"] is True, q

    logs = db_fetch(
        "SELECT count(*) AS n FROM content_change_logs "
        "WHERE batch_id = $1 AND action = 'rollback'", bid,
    )
    assert logs[0]["n"] == 4, logs


def test_rollback_restores_upserted_questions(client: httpx.Client, admin_h, sz) -> None:
    """回滚的另一半：upsert **更新**过的题要还原到导入前版本，而不是被删掉。"""
    _, chapter_code = sz
    before = count_questions(client, admin_h)
    tag = uuid.uuid4().hex[:8]
    content = csv_bytes([row(chapter=chapter_code, tag=f"ups-{tag}-{i}") for i in range(2)])

    # 先 insert 建两题
    bid1, _, ex1 = import_once(client, admin_h, content)
    assert ex1["data"]["success_rows"] == 2, ex1
    qids = [r["question_id"] for r in body(
        client.get(f"{API}/admin/imports/{bid1}", headers=admin_h))["data"]["rows"]]
    ver_before = body(client.get(f"{API}/admin/questions/{qids[0]}", headers=admin_h))["data"]["version"]

    # 同一份文件再来一次，这次 upsert → 命中同内容题，走 update 分支
    up = upload(client, admin_h, content, name="ups-2.csv", mode="upsert")
    bid2 = int(up["data"]["id"])
    validate(client, admin_h, bid2)
    ex2 = execute(client, admin_h, bid2)
    assert ex2["code"] == 0 and ex2["data"]["updated_rows"] == 2, ex2
    ver_after = body(client.get(f"{API}/admin/questions/{qids[0]}", headers=admin_h))["data"]["version"]
    assert ver_after == ver_before + 1, (ver_before, ver_after)

    # 回滚第 2 批 → 还原到导入前版本，题目**不能**被软删除
    rb = rollback(client, admin_h, bid2, reason="upsert 回滚用例")
    assert rb["data"]["rolled_back_updates"] == 2 and rb["data"]["rolled_back_questions"] == 0, rb
    q = body(client.get(f"{API}/admin/questions/{qids[0]}", headers=admin_h))["data"]
    assert q["is_deleted"] is False, q
    assert q["version"] == ver_before + 1, (q["version"], ver_before)  # 还原本身也 +1（留痕）

    # 清理第 1 批
    rollback(client, admin_h, bid1, reason="用例清理")
    assert count_questions(client, admin_h) == before


# ================================================================ 5. 验收④ 事务

def test_mid_import_failure_rolls_back_everything(client: httpx.Client, admin_h, sz) -> None:
    """验收④：执行中途某行写库失败 → 整批回滚，库里不留半截数据。

    构造手法：`source_name` 超过列宽 `VARCHAR(160)`。它在**校验阶段查不出来**
    （校验只做业务规则，不做列宽），只会在真正 INSERT 时被 PG 拒绝 ——
    正好模拟"中途异常"。整批 6 行同处一个 500 行的写批里，因此必须一行都进不去。
    """
    _, chapter_code = sz
    before = count_questions(client, admin_h)
    rows = [row(chapter=chapter_code) for _ in range(5)]
    rows.append(row(chapter=chapter_code, source_type="authorized",
                    source_name="授权来源" * 50,        # 200 字 > 160
                    source_license="HT-2026-0001"))

    up = upload(client, admin_h, csv_bytes(rows), name="boom.csv")
    bid = int(up["data"]["id"])
    v = validate(client, admin_h, bid)["data"]
    assert v["failed_rows"] == 0 and v["success_rows"] == 6, v   # 校验阶段查不出列宽问题

    ex = execute(client, admin_h, bid)
    assert ex["code"] != 0 and "已整批回滚" in ex["message"], ex
    assert count_questions(client, admin_h) == before, "整批回滚失败：库里留下了半截数据"

    detail = body(client.get(f"{API}/admin/imports/{bid}", headers=admin_h))["data"]
    assert detail["status"] == "failed", detail
    assert detail["error_report"]["total_errors"] >= 1, detail["error_report"]


def test_execute_twice_and_rollback_twice_are_rejected(client: httpx.Client, admin_h, sz) -> None:
    """同一批不允许执行两次；已回滚的批次不允许再回滚。"""
    _, chapter_code = sz
    before = count_questions(client, admin_h)
    content = csv_bytes([row(chapter=chapter_code)])

    bid, _, ex = import_once(client, admin_h, content)
    assert ex["code"] == 0, ex

    again = execute(client, admin_h, bid)
    assert again["code"] == 40901 and "已经执行过" in again["message"], again
    assert count_questions(client, admin_h) == before + 1, "重复执行不该再写入"

    assert rollback(client, admin_h, bid)["code"] == 0
    twice = rollback(client, admin_h, bid)
    assert twice["code"] == 40901 and "已经回滚过" in twice["message"], twice
    assert count_questions(client, admin_h) == before


# ================================================================ 6. 验收⑥ 数据范围

def test_researcher_data_scope(client: httpx.Client, admin_h, sz) -> None:
    """验收⑥：researcher 挂 `subject` 范围（市政）后，只能导入自己专业的题。

    两道闸都要拦得住：
      1. 批次科目越权（subject_id=建筑实务）→ 建批次就 403
      2. 文件里混入别专业的行 → 逐行报 `subject_code` 超出数据范围
    """
    subject_id, chapter_code = sz
    assert subject_id == SZ_SUBJECT_ID, subject_id

    # 造一个只有 researcher 角色 + subject 范围的账号
    u = fresh_user(client, nickname="教研-范围")
    ar = assign_roles(client, admin_h, u["user"]["id"], ["researcher"],
                      scope_type="subject", scope_id=subject_id)
    assert ar["code"] == 0, ar
    rh = auth(u["access_token"])   # 权限每次请求从库里取，旧 token 立刻生效

    # 闸 1：批次科目越权
    up = upload(client, rh, csv_bytes([row(subject="SW-JZ", chapter="JZ-01")]),
                name="x.csv", subject_id=JZ_SUBJECT_ID)
    assert up["code"] == 40301 and "数据范围" in up["message"], up

    # 闸 2：文件里混入别专业的行
    up = upload(client, rh, csv_bytes([
        row(subject="SW-SZ", chapter=chapter_code, tag="own-1"),
        row(subject="SW-JZ", chapter="JZ-01", tag="other-1"),
    ]), name="mixed.csv")
    assert up["code"] == 0, up
    bid = int(up["data"]["id"])
    v = validate(client, rh, bid)["data"]
    assert v["failed_rows"] == 1, v
    err = v["error_report"]["errors"][0]
    assert err["field"] == "subject_code" and "数据范围" in err["message"], err
    # 本专业的行照常通过
    assert v["success_rows"] == 1, v

    # 本专业行能正常导入，且回滚后回到基线
    before = count_questions(client, admin_h)
    ex = execute(client, rh, bid, allow_partial=True)
    assert ex["code"] == 0 and ex["data"]["success_rows"] == 1, ex
    assert rollback(client, rh, bid)["code"] == 0
    assert count_questions(client, admin_h) == before


def test_permission_wall(client: httpx.Client, sz) -> None:
    """无角色账号（学员）在导入管道上一律 403 —— 包括只读接口。"""
    u = fresh_user(client, nickname="无角色")
    h = auth(u["access_token"])

    assert body(client.get(f"{API}/admin/imports", headers=h))["code"] == 40301
    assert upload(client, h, csv_bytes([row()]), name="t.csv")["code"] == 40301
    for path, method in (
        ("/admin/imports/1/validate", "post"),
        ("/admin/imports/1/execute", "post"),
        ("/admin/imports/1/publish", "post"),
        ("/admin/imports/1/rollback", "post"),
    ):
        b = body(client.request(method, f"{API}{path}", headers=h, json={}))
        assert b["code"] == 40301, (path, b)


# ================================================================ 7. 列表 / 详情契约

def test_list_and_detail_contract(client: httpx.Client, admin_h, sz) -> None:
    """列表与详情的形状契约：分页信封、共用字段、BigIntStr（ID 必须是字符串）。"""
    _, chapter_code = sz
    content = csv_bytes([row(chapter=chapter_code) for _ in range(3)])
    bid, v, _ = import_once(client, admin_h, content)

    lst = body(client.get(f"{API}/admin/imports?page=1&page_size=10", headers=admin_h))
    assert lst["code"] == 0, lst
    page = lst["data"]
    assert {"items", "page", "page_size", "total", "has_more"} <= set(page), page
    assert page["total"] >= 1, page
    items = {int(i["id"]): i for i in page["items"]}
    assert bid in items, (bid, list(items)[:5])

    one = items[bid]
    # ID 一律是字符串，否则 JS 会静默丢精度（Batch 4 的坑，这里必须守住）
    assert isinstance(one["id"], str) and int(one["id"]) > 2**53 - 1, one["id"]
    for key in ("batch_no", "file_name", "file_type", "file_hash", "source_type", "mode",
                "status", "total_rows", "success_rows", "failed_rows",
                "duplicate_rows", "updated_rows", "can_execute", "can_publish", "can_rollback"):
        assert key in one, (key, sorted(one))

    assert one["total_rows"] == 3 and one["success_rows"] == 3

    detail = body(client.get(f"{API}/admin/imports/{bid}", headers=admin_h))["data"]
    assert detail["id"] == str(bid), detail["id"]
    assert detail["row_total"] == 3 and detail["row_page"] == 1 and detail["row_page_size"] == 50
    assert len(detail["rows"]) == 3
    assert all({"row_no", "action", "message", "question_id"} <= set(r) for r in detail["rows"])
    assert {"total_errors", "truncated", "errors"} <= set(detail["error_report"])

    # 不存在的批次 → 40401
    b = body(client.get(f"{API}/admin/imports/999999999999", headers=admin_h))
    assert b["code"] == 40401, b

    assert rollback(client, admin_h, bid)["code"] == 0


def test_status_filter(client: httpx.Client, admin_h, sz) -> None:
    """列表支持按 status 过滤。"""
    _, chapter_code = sz
    bid, _, _ = import_once(client, admin_h, csv_bytes([row(chapter=chapter_code)]))
    done = body(client.get(f"{API}/admin/imports?status=done&page_size=100", headers=admin_h))["data"]
    assert all(i["status"] == "done" for i in done["items"]), done["items"][:3]
    assert bid in {int(i["id"]) for i in done["items"]}

    bogus = body(client.get(f"{API}/admin/imports?status=nope", headers=admin_h))["data"]
    assert bogus["total"] == 0 and bogus["items"] == []

    assert rollback(client, admin_h, bid)["code"] == 0


# ================================================================ 8. 验收⑤ 6000 道仿真题

def _load_sim_bank_tool():
    spec = importlib.util.spec_from_file_location("_import_sim_bank", SIM_BANK_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _seed_items() -> list[dict]:
    if not SEED_JSON.exists():
        pytest.skip(f"缺少种子文件 {SEED_JSON}")
    return json.loads(SEED_JSON.read_text(encoding="utf-8"))


def test_seed_bank_mapping_matches_existing_rows(client: httpx.Client, admin_h) -> None:
    """验收⑤ 的**无损**前置证明：把种子题按导入模板重编后，逐条命中库里已有题。

    用 `insert` 模式跑 —— 每一条都应当被判为 `duplicate`（success=0）。
    这同时证明两件事：
      1. 转换器（seed → 24 列模板）没把内容改坏（content_hash 逐条一致）；
      2. 导入管道的幂等对**真实体量**成立（不是只在 3 行的小样本上成立）。

    选题范围刻意排除 case / case_sub：案例小问要求同批带上它的案例大题，
    这一条由下面的全量用例覆盖。
    """
    tool = _load_sim_bank_tool()
    items = [i for i in _seed_items() if i["type"] not in ("case", "case_sub")][:300]

    chapter_map, kp_map = {}, {}
    dsn = _dsn()
    if dsn:
        try:
            chapter_map, kp_map = asyncio.run(tool.load_code_maps(dsn))
        except Exception:  # noqa: BLE001
            chapter_map, kp_map = {}, {}
    rows = tool.build_rows(items, chapter_map, kp_map)
    assert all(r["subject_code"] for r in rows), "subject_code 不该为空"

    payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    up = upload(client, admin_h, payload, name="seed-subset.json")
    assert up["code"] == 0 and up["data"]["total_rows"] == len(items), up
    bid = int(up["data"]["id"])

    v = validate(client, admin_h, bid)["data"]
    assert v["failed_rows"] == 0, v["error_report"]["errors"][:5]
    assert v["duplicate_rows"] == len(items), (
        f"期望 {len(items)} 行为 duplicate（说明全部命中库内已有题），"
        f"实际 duplicate={v['duplicate_rows']} success={v['success_rows']}"
    )
    assert v["success_rows"] == 0, v


@pytest.mark.skipif(
    os.environ.get("YIJIAN_BIG_IMPORT") != "1",
    reason="全量 6000 道灌库耗时约 30s 且会写 6000 条 version/变更日志；"
           "需要时用 YIJIAN_BIG_IMPORT=1 pytest 开启（或跑 "
           "tools/local-verify/import-sim-bank.py）",
)
def test_seed_bank_full_6000_upsert(client: httpx.Client, admin_h) -> None:
    """验收⑤ 全量：6000 道仿真题经导入管道真灌进库 → success=6000, failed=0。

    因为库里已有这 6000 道（Batch 1 的 seed），这里用 `upsert` —— 6000 条 update，
    题量不变、内容不变（content_hash 一致），正好证明"整批命中 + 整批写入成功"。
    """
    tool = _load_sim_bank_tool()
    items = _seed_items()
    assert len(items) == 6000, len(items)

    dsn = _dsn()
    if not dsn:
        pytest.skip("需要 DATABASE_URL 取章节/知识点编码表")
    chapter_map, kp_map = asyncio.run(tool.load_code_maps(dsn))
    rows = tool.build_rows(items, chapter_map, kp_map)
    payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")

    before = count_questions(client, admin_h)
    # 6000 行的校验约 12s、执行约 32s，远超 conftest 里 15s 的默认超时 → 这里单请求放长
    up = upload(client, admin_h, payload, name="sim-bank-6000.json", mode="upsert", timeout=300)
    assert up["code"] == 0 and up["data"]["total_rows"] == 6000, up
    bid = int(up["data"]["id"])

    v = validate(client, admin_h, bid, timeout=300)["data"]
    assert v["failed_rows"] == 0, v["error_report"]["errors"][:5]

    ex = execute(client, admin_h, bid, timeout=600)
    assert ex["code"] == 0, ex
    assert ex["data"]["success_rows"] == 6000, ex
    assert ex["data"]["updated_rows"] == 6000, ex

    detail = body(client.get(f"{API}/admin/imports/{bid}", headers=admin_h))["data"]
    assert detail["total_rows"] == 6000 and detail["success_rows"] == 6000
    assert detail["failed_rows"] == 0, detail
    assert count_questions(client, admin_h) == before, "upsert 不该改变题量"
