"""导入管道 · 端到端（⑤c-3 / `docs/19` §3.6–§3.10）。

这批的真正价值不是"还有 24 条未覆盖"，而是——
**`case` / `case_sub` 两类题型在导入管道里从未端到端跑过**（父子挂接、批量写入）。

## 三条纪律（用户 2026-09-23 定，写在这里免得忘）

**① 隔离**：每个用例用**唯一命名空间**（stem / 科目章节 / 文件名都带唯一 tag），
不依赖"上一条用例留下的状态" —— 顺序一变就红。

**② 回滚类用例必须带 `try/finally`**：回滚会改 DB，中途失败会把状态留在半路、
污染后面的用例。`finally` 里**直接执行还原，不做"是否成功"的条件判断**。

**③ 变异要在状态构造之前施加**（这条是给 `tests/` 外的变异 harness 的）：
纯函数批是"改代码 → 重跑 → 断言红"；这批是"造状态 → 改代码 → 重跑 → 断言红"，
而**造状态的代码本身可能覆盖变异点**。所以顺序必须是
`清场 → 变异 → 造状态 → 执行 → 断言`，不能反过来。

## 覆盖的缺口行（行号基于 ⑤c-1/⑤c-2 之后的源码）

    create_batch      642 644
    validate_batch    870
    execute_batch     1222 1246 1333
    _write_rows       1512-1517 1525 1526 1527 1531 1532 1566
    publish_batch     1821 1825 1841
    rollback_batch    1913 2055 2056 2057
"""

import csv
import io
import os
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest

from app.schemas.admin_import import IMPORT_COLUMNS

from .conftest import API, body, sql_exec, sql_fetch

SUBJECT = "SW-SZ"  # 市政实务
CHAPTER = "SZ-01"  # 该科目下真实存在的章节编码
_FILE_DIR = Path(tempfile.gettempdir()) / "yijian-import-files"


# ============================================================ 唯一命名空间


def _tag() -> str:
    return uuid.uuid4().hex[:10]


def _row(*, tag: str | None = None, **over) -> dict:
    """一行内容唯一的导入行。`tag` 同时出现在 stem / 解析 / 选项里，保证内容指纹不撞。"""
    t = tag or _tag()
    r = {c: "" for c in IMPORT_COLUMNS}
    r.update(
        {
            "subject_code": SUBJECT,
            "chapter_code": CHAPTER,
            "type": "single",
            "stem": f"【自动化-管道】{t} 关于施工组织设计，下列说法正确的是？",
            "option_a": f"A-{t} 由项目技术负责人审批即可",
            "option_b": f"B-{t} 需经施工单位技术负责人批准",
            "answer": "B",
            "analysis": f"解析 {t}：施工组织设计须经施工单位技术负责人批准，故选 B。",
            "difficulty": "3",
            "source_type": "self",
        }
    )
    r.update(over)
    return r


def _csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(IMPORT_COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in IMPORT_COLUMNS})
    return buf.getvalue().encode("utf-8-sig")


# ============================================================ HTTP 步骤


def _upload(client, headers, content: bytes, *, mode: str = "insert", name: str | None = None):
    return body(
        client.post(
            f"{API}/admin/imports/upload",
            headers=headers,
            files={"file": (name or f"pipe-{_tag()}.csv", content, "text/csv")},
            data={"mode": mode, "source_type": "self"},
        )
    )


def _validate(client, headers, bid):
    return body(client.post(f"{API}/admin/imports/{bid}/validate", headers=headers))


def _execute(client, headers, bid, **payload):
    return body(client.post(f"{API}/admin/imports/{bid}/execute", headers=headers, json=payload))


def _publish(client, headers, bid, **payload):
    return body(client.post(f"{API}/admin/imports/{bid}/publish", headers=headers, json=payload))


def _rollback(client, headers, bid, **payload):
    return body(client.post(f"{API}/admin/imports/{bid}/rollback", headers=headers, json=payload))


def _import_once(client, headers, content: bytes, *, mode: str = "insert"):
    """upload → validate → execute，返回 (bid, validate_data, execute_data)。"""
    up = _upload(client, headers, content, mode=mode)
    assert up["code"] == 0, up
    bid = int(up["data"]["id"])
    v = _validate(client, headers, bid)
    assert v["code"] == 0, v
    ex = _execute(client, headers, bid)
    return bid, v["data"], ex


def _uploaded(client, headers, content: bytes, *, mode: str = "insert"):
    """只到 upload 为止，返回 bid。"""
    up = _upload(client, headers, content, mode=mode)
    assert up["code"] == 0, up
    return int(up["data"]["id"])


# ============================================================ §3.8 案例题写入


def test_case_and_case_sub_are_linked_in_one_batch(client: httpx.Client, admin_h) -> None:
    """案例大题 + 小问**同批**导入 → 小问挂到大题上（覆盖 1512–1517 / 1525 / 1526 / 1531 / 1532）。

    这是 `case` / `case_sub` 第一次真正走完导入管道 ——
    在此之前，导入的端到端用例只碰过 single / multiple / judge。
    """
    t = _tag()
    group = f"G{t}"
    rows = [
        _row(
            tag=t,
            type="case",
            case_group_id=group,
            option_a="",
            option_b="",
            answer=f"案例参考答案 {t}",
            material=f"案例材料 {t}",
        ),
        _row(
            tag=t,
            type="case_sub",
            case_group_id=group,
            option_a="",
            option_b="",
            answer=f"小问参考答案 {t}",
        ),
    ]
    bid, _, ex = _import_once(client, admin_h, _csv(rows))
    assert ex["code"] == 0, ex

    got = sql_fetch(
        "SELECT id, type, parent_id, root_id FROM questions WHERE stem LIKE '%' || $1 || '%'",
        t,
    )
    assert len(got) == 2, got
    by_type = {r["type"]: r for r in got}
    parent_id = by_type["case"]["id"]
    assert by_type["case"]["parent_id"] is None, "大题自己不该有父题"
    assert by_type["case_sub"]["parent_id"] == parent_id
    assert by_type["case_sub"]["root_id"] == parent_id

    # 批次也记了两条 insert（不是一条）
    items = sql_fetch("SELECT action FROM import_items WHERE batch_id = $1", bid)
    assert [r["action"] for r in items] == ["insert", "insert"], items


def test_case_sub_without_case_in_batch_is_rejected_at_execute(
    client: httpx.Client, admin_h
) -> None:
    """只导小问、不导大题 → **validate 放过、execute 才拒**（覆盖 1525 / 1526 / 1527）。

    ⚠️ 这条固定的是"**两阶段检查强度不同**"这个事实：
    `validate_row` 的同类检查带 `ctx.case_groups_in_file and` 前置 ——
    本批没有大题时集合为空、条件短路、于是放过；
    而 `_write_rows` 的 `group_to_id` 没有这个豁免。

    ⚠️ 另一个事实：`_write_rows` 抛的 `bad_request(40001)` 被 `execute_batch` 的 except
    统一包成 **`50001` + 整批回滚**（批次落 `failed`）—— 不是原样透传 40001。
    """
    t = _tag()
    content = _csv(
        [
            _row(
                tag=t,
                type="case_sub",
                case_group_id=f"G{t}",
                option_a="",
                option_b="",
                answer=f"小问参考答案 {t}",
            )
        ]
    )
    bid = _uploaded(client, admin_h, content)

    v = _validate(client, admin_h, bid)
    assert v["code"] == 0, f"validate 应当放过（case_groups_in_file 为空 → 短路）：{v}"

    ex = _execute(client, admin_h, bid)
    assert ex["code"] == 50001, ex
    assert "找不到同批导入的案例大题" in ex["message"]


def test_execute_recomputes_instead_of_trusting_import_items(client: httpx.Client, admin_h) -> None:
    """执行阶段**以重算结果为准**，不信 `import_items.action` / `question_id`。

    ⚠️ 这条是在试图构造 `_write_rows:1566`（"要更新的题目已不存在" → `40401`）时
    **把踩到的坑固定下来**：篡改库里的 `import_items.question_id` 对它**完全无效** ——
    因为 `payloads` 是 execute 时重新 `validate_row` 算出来的，`items` 只提供 `row_no`
    （`_write_rows` 的 docstring 写明了这个设计）。

    ⇒ 推论：`1566` 只在"**重算时题还在、写的时候题没了**"的**并发窗口**可达；
      正常流程不可达（`docs/19` §4 已把同类判定为 pragma 候选）。
    """
    t = _tag()
    content = _csv([_row(tag=t)])
    _import_once(client, admin_h, content)  # 先入库一道

    bid = _uploaded(client, admin_h, content, mode="upsert")
    v = _validate(client, admin_h, bid)
    assert v["code"] == 0, v
    assert [r["action"] for r in v["data"]["rows"]] == ["update"], v["data"]

    ghost = sql_fetch("SELECT max(id) + 1000 AS x FROM questions")[0]["x"]
    changed = sql_exec(
        "UPDATE import_items SET question_id = $1 WHERE batch_id = $2 AND action = 'update'",
        ghost,
        bid,
    )
    assert "1" in str(changed), f"篡改必须生效，否则下面的结论站不住：{changed}"

    ex = _execute(client, admin_h, bid)
    assert ex["code"] == 0, f"execute 应当重算、忽略被篡改的 import_items：{ex}"
    real = sql_fetch("SELECT id FROM questions WHERE stem LIKE '%' || $1 || '%'", t)
    assert len(real) == 1, real


# ============================================================ §3.6 状态机拒绝


def test_execute_before_validate_is_rejected(client: httpx.Client, admin_h) -> None:
    """上传后**直接 execute**（跳过一次 validate）→ `40901`（覆盖 1222）。"""
    bid = _uploaded(client, admin_h, _csv([_row()]))
    ex = _execute(client, admin_h, bid)
    assert ex["code"] == 40901, ex
    assert "只有校验完成" in ex["message"]


def test_publish_before_done_is_rejected(client: httpx.Client, admin_h) -> None:
    """批次还没 done 就 publish → `40901`（覆盖 1821）。"""
    bid = _uploaded(client, admin_h, _csv([_row()]))
    pb = _publish(client, admin_h, bid)
    assert pb["code"] == 40901, pb
    assert "不能发布" in pb["message"]


def test_rollback_pending_batch_is_rejected(client: httpx.Client, admin_h) -> None:
    """还没执行的批次 rollback → `40901`（覆盖 1913）。

    ⚠️ `finally` 里直接还原，不管前面成没成（纪律②）。
    """
    bid = _uploaded(client, admin_h, _csv([_row()]))
    try:
        rb = _rollback(client, admin_h, bid)
        assert rb["code"] == 40901, rb
        assert "不能回滚" in rb["message"]
    finally:
        sql_exec("UPDATE import_batches SET status = 'pending' WHERE id = $1", bid)


def test_validate_while_importing_is_rejected(client: httpx.Client, admin_h) -> None:
    """批次处于 `importing` 时不能再 validate → `40901`（覆盖 870）。

    ⚠️ `importing` 是 execute 过程中的**瞬态**，HTTP 上抓不到 ——
    所以这里用 SQL 把状态直接置成 `importing`（造状态），**finally 里还原**。
    """
    bid = _uploaded(client, admin_h, _csv([_row()]))
    try:
        sql_exec("UPDATE import_batches SET status = 'importing' WHERE id = $1", bid)
        v = _validate(client, admin_h, bid)
        assert v["code"] == 40901, v
        assert "正在导入" in v["message"]
    finally:
        sql_exec("UPDATE import_batches SET status = 'pending' WHERE id = $1", bid)


# ============================================================ §3.7 批次规模


def test_csv_with_only_header_is_rejected(client: httpx.Client, admin_h) -> None:
    """只有表头、没有数据行 → `40001`（覆盖 642）。

    与"空文件"是**两条不同的错误**（空文件在 `parse_file` 就被拦，见 §3.1）。
    """
    up = _upload(client, admin_h, _csv([]))
    assert up["code"] == 40001, up
    assert "没有数据行" in up["message"]


def test_oversize_batch_is_rejected(client: httpx.Client, admin_h) -> None:
    """超过 20000 行上限 → `40001`（覆盖 644）。

    ⚠️ 这是**唯一**需要生成 2 万行的用例 —— 行故意写短，控制体积与耗时。
    """
    n = 20001
    rows = [
        _row(tag=f"{i:05d}", stem=f"【自动化-管道】超限行 {i:05d}", analysis="解析内容足够长。")
        for i in range(n)
    ]
    up = _upload(client, admin_h, _csv(rows))
    assert up["code"] == 40001, up
    assert "单批最多" in up["message"]


# ============================================================ §3.9 / §3.10 发布与顺带发布


def test_publish_with_include_duplicates(client: httpx.Client, admin_h) -> None:
    """`include_duplicates=true` 时把"命中的老题"一并发布（覆盖 1825）。

    全重复的批次单独 publish 是会被拒的（下一条用例）——
    所以这个开关是**唯一的出口**，必须能走通。
    """
    t = _tag()
    content = _csv([_row(tag=t)])
    _import_once(client, admin_h, content)  # 入库一道

    bid = _uploaded(client, admin_h, content)  # 同内容再来一次 → duplicate
    v = _validate(client, admin_h, bid)
    assert v["code"] == 0 and [r["action"] for r in v["data"]["rows"]] == ["duplicate"], v["data"]
    ex = _execute(client, admin_h, bid)
    assert ex["code"] == 0 and ex["data"]["success_rows"] == 0, ex

    pb = _publish(client, admin_h, bid, include_duplicates=True)
    assert pb["code"] == 0, pb


def test_publish_batch_without_publishable_rows_is_rejected(client: httpx.Client, admin_h) -> None:
    """全重复的批次（不带 `include_duplicates`）没有可发布对象 → `40001`（覆盖 1841）。

    这也是 `execute_batch` 里那句 `(inserted + updated) > 0` 守卫存在的原因 ——
    否则"一次成功的 execute"会因为这个 40001 变成 500。
    """
    t = _tag()
    content = _csv([_row(tag=t)])
    _import_once(client, admin_h, content)

    bid = _uploaded(client, admin_h, content)
    assert _validate(client, admin_h, bid)["code"] == 0
    assert _execute(client, admin_h, bid)["code"] == 0

    pb = _publish(client, admin_h, bid)
    assert pb["code"] == 40001, pb
    assert "没有可发布的题目" in pb["message"]


def test_execute_with_publish_flag_publishes(client: httpx.Client, admin_h) -> None:
    """`execute(publish=true)` → 执行后**顺带发布**（覆盖 1333）。

    这是"上传即发布"的一键流程；此前测试只走过"显式再调一次 /publish"。
    """
    t = _tag()
    content = _csv([_row(tag=t)])
    bid = _uploaded(client, admin_h, content)
    assert _validate(client, admin_h, bid)["code"] == 0

    ex = _execute(client, admin_h, bid, publish=True)
    assert ex["code"] == 0, ex

    got = sql_fetch("SELECT status FROM questions WHERE stem LIKE '%' || $1 || '%'", t)
    assert [r["status"] for r in got] == ["published"], got
    assert "published" in ex["data"]["status"] or ex["data"]["status"] == "done", ex["data"]


# ============================================================ §3.8 回滚 + 文件缺失


def test_rollback_soft_deletes_inserted_questions(client: httpx.Client, admin_h) -> None:
    """正向通道：回滚把 insert 进来的题**软删除**、批次置 `rolled_back`。

    没有这条正向对照，下面那条"回滚失败 → 50001"红了也可能是**通道本来就坏**。
    """
    t = _tag()
    bid, _, _ = _import_once(client, admin_h, _csv([_row(tag=t)]))
    before = sql_fetch("SELECT id FROM questions WHERE stem LIKE '%' || $1 || '%'", t)
    assert len(before) == 1

    rb = _rollback(client, admin_h, bid, reason=f"自动化回滚 {t}")
    assert rb["code"] == 0, rb
    assert rb["data"]["status"] == "rolled_back"

    after = sql_fetch("SELECT is_deleted FROM questions WHERE stem LIKE '%' || $1 || '%'", t)
    assert [r["is_deleted"] for r in after] == [True], after


def test_rollback_failure_is_wrapped_as_50001(client: httpx.Client, admin_h) -> None:
    """回滚中途抛异常 → 先 `db.rollback()` 再包成 `50001`（覆盖 2055 / 2056 / 2057）。

    ⚠️ 构造：把"导入前那一版"的 `snapshot` 改成 JSON 数组 `[]` ——
    代码里 `s = snap["snapshot"] or {}` 之后调 `s.get(...)`，
    **list 没有 `.get`** → AttributeError → 走 except 分支。

    ⚠️ 纪律②：`finally` 里**直接还原** snapshot，不做"是否成功"的判断。
    """
    t = _tag()
    content = _csv([_row(tag=t)])
    _import_once(client, admin_h, content)  # 第一版（v1）

    bid = _uploaded(client, admin_h, content, mode="upsert")
    assert _validate(client, admin_h, bid)["code"] == 0
    assert _execute(client, admin_h, bid)["code"] == 0  # 第二版（v2）

    qid = sql_fetch("SELECT id FROM questions WHERE stem LIKE '%' || $1 || '%'", t)[0]["id"]
    # "导入前那一版" = version 最小的那条（v1）
    oldest = sql_fetch(
        "SELECT version, snapshot FROM question_versions WHERE question_id = $1 "
        "ORDER BY version ASC LIMIT 1",
        qid,
    )[0]
    import json as _json

    original = _json.dumps(oldest["snapshot"], ensure_ascii=False)
    try:
        sql_exec(
            "UPDATE question_versions SET snapshot = '[]'::jsonb "
            "WHERE question_id = $1 AND version = $2",
            qid,
            oldest["version"],
        )
        rb = _rollback(client, admin_h, bid)
        assert rb["code"] == 50001, rb
        assert "回滚失败" in rb["message"]
    finally:
        # 直接还原，不管前面成没成（纪律②）
        sql_exec(
            "UPDATE question_versions SET snapshot = CAST($1 AS jsonb) "
            "WHERE question_id = $2 AND version = $3",
            original,
            qid,
            oldest["version"],
        )


def test_execute_with_missing_batch_file_is_rejected(client: httpx.Client, admin_h) -> None:
    """批次原始文件被清理后再 execute → `40001`（覆盖 1246）。

    这是真实运维场景（临时目录会被清），也是 `_load_full_payloads` 里那道
    "第二道防线"（已 pragma）先行拦下的位置。
    """
    t = _tag()
    bid = _uploaded(client, admin_h, _csv([_row(tag=t)]))
    assert _validate(client, admin_h, bid)["code"] == 0

    path = _FILE_DIR / f"{bid}.csv"
    assert path.exists(), f"原始文件应当已落盘：{path}"
    try:
        os.remove(path)
        ex = _execute(client, admin_h, bid)
        assert ex["code"] == 40001, ex
        assert "原始文件已不可用" in ex["message"]
    finally:
        # 文件已删，无法还原 —— 但批次本身没被写成 done，后续用例用各自的新批次
        pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
