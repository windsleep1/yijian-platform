"""
统计看板数据层的契约测试（Batch 8 · S1-a）。

覆盖 docs/20 §7 的判据 **1–4 / 9 / 11 / 12**，外加用户定的**四条硬约束**（① 参数绑定 /
② 返回 dict / ③ statement_timeout / ④ SQL 在文件里）。

## 设计原则：**不依赖造数脚本**

本文件所有断言都建立在**自建的探针数据**上，而那些探针被放在**远过去的日期**
（`_probe_base()` = 200 天前）。这样：
- 跑过 `seed-stats.py` 与否，结果都一样（造数只覆盖最近 30 天，够不着 200 天前）；
- 想验"某一天有/没有数据"时，那个窗口里**只有探针**，不需要做减法。

依赖造数脚本的测试会在"别人没跑 seed"时变红 —— 那种红**指向的是环境，不是代码**，
而人第一时间会去查代码。

## 探针的标记与清理

探针用 `users.remark = 'stats-probe'`、id 取自另一个保留段（6e18）。
与 seed 脚本的 `stats-seed-*` **刻意不重叠** —— 两个工具的清理互不影响。
每个用例 `finally` 里 `DELETE FROM users WHERE id = …`（会话与明细靠外键级联删掉）。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.errors import BizError
from app.db import base
from app.services import stats_service

TZ = timezone(timedelta(hours=8))

PROBE_MARK = "stats-probe"
PROBE_ID_BASE = 6_000_000_000_000_000_000
ID_TABLES = ("users", "practice_sessions", "practice_items", "exams", "exam_attempts", "orders")

#: 探针数据所在的"无菌窗口"起点（够远，造数够不着）
PROBE_DAYS_AGO = 200


def _probe_base() -> date:
    return datetime.now(TZ).date() - timedelta(days=PROBE_DAYS_AGO)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """构造"上海时间 day 日 hour:minute"的**绝对时刻**（TIMESTAMPTZ 存的就是它）。"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).astimezone(timezone.utc)


def _dsn_ready() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


pytestmark = pytest.mark.skipif(not _dsn_ready(), reason="未设置 DATABASE_URL —— 这些用例需要真库")


# ------------------------------------------------------------------ 装置


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """一个**独立**的连接池（NullPool）的会话。

    用 `base.engine.url`（带 `+asyncpg` 的完整 URL）而不是 `settings.dsn`：
    后者是给 asyncpg 用的无驱动后缀形式，直接喂 `create_async_engine` 会报
    "不能加载驱动 postgresql"（这个坑在 S1 之前已经踩过一次）。
    NullPool：本文件的用例各自 `asyncio.run` 一个新事件循环，
    复用池会把"属于旧循环的连接"带进新循环（表现为随机的 NoneType.send）。
    """
    engine = create_async_engine(base.engine.url, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _next_ids(db: AsyncSession, count: int) -> list[int]:
    """探针保留段里的 `count` 个连续 id（避开真实雪花 id，也避开 seed 脚本的 7e18 段）。"""
    start = PROBE_ID_BASE
    for table in ID_TABLES:
        got = (
            await db.execute(text(f"SELECT max(id) FROM {table} WHERE id >= :b"), {"b": start})
        ).scalar()
        if got is not None and int(got) > start:
            start = int(got)
    return [start + 1 + i for i in range(count)]


async def _published_questions(db: AsyncSession, n: int, *, same_kp: bool = False) -> list[dict]:
    """取 n 道已发布题。

    `same_kp=True` → 取**同一个知识点**下的 n 道题。为什么需要它：
    "薄弱知识点"是按知识点聚合的，而随便取 3 道题**很可能落在 3 个不同知识点上** ——
    那样每个知识点只有 1 个样本，`min_sample=3` 自然全被排除，
    用例就会红得让人以为是服务错了（第一版就是这样错的）。
    """
    sql = """
        SELECT id, subject_id, knowledge_point_id
        FROM questions
        WHERE is_deleted = false AND status = 'published'
    """
    params: dict[str, Any] = {"n": n}
    if same_kp:
        sql += """
          AND knowledge_point_id = (
              SELECT knowledge_point_id FROM questions
              WHERE is_deleted = false AND status = 'published'
                AND knowledge_point_id IS NOT NULL
              GROUP BY 1 HAVING count(*) >= :n ORDER BY 1 LIMIT 1
          )
        """
    sql += " ORDER BY id LIMIT :n"
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def _mk_probe(
    db: AsyncSession,
    *,
    day: date,
    hour: int = 10,
    answers: int = 4,
    correct: int = 2,
    ungraded: int = 0,
    same_kp: bool = False,
) -> dict[str, Any]:
    """造一个探针用户 + 一个会话 + `answers` 条答题（可外加 `ungraded` 条"答了没判分"）。

    返回 `{"user_id", "session_id", "answers", "correct"}`，供断言与清理用。
    """
    qs = await _published_questions(db, max(answers + ungraded, 1), same_kp=same_kp)
    if len(qs) < answers + ungraded:
        pytest.skip("库里没有足够的已发布题（需先跑迁移 + python -m app.cli seed-questions）")

    uid, sid = await _next_ids(db, 2)
    created = _at(day, hour)
    await db.execute(
        text(
            """
            INSERT INTO users (id, phone, nickname, status, register_source,
                               is_deleted, remark, created_at, updated_at)
            VALUES (:id, :phone, :nick, 'active', 'h5', false, :mark, :ts, :ts)
            """
        ),
        {
            "id": uid,
            "phone": f"199{uid % 100000000:08d}",
            "nick": "probe",
            "mark": PROBE_MARK,
            "ts": created,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO practice_sessions
                (id, user_id, mode, subject_id, title, config, total, answered, correct,
                 score, duration_sec, status, started_at, finished_at, created_at, updated_at)
            VALUES (:id, :uid, 'chapter', :sub, :title, '{}'::jsonb, :n, :n, :c,
                    :score, 60, 'finished', :ts, :ts, :ts, :ts)
            """
        ),
        {
            "id": sid,
            "uid": uid,
            "sub": qs[0]["subject_id"],
            "title": PROBE_MARK,
            "n": answers,
            "c": correct,
            # ⚠️ `correct` 是 SMALLINT、`score` 是 NUMERIC —— **必须两个占位符**。
            #    共用一个会让 PG 报 `inconsistent types deduced for parameter`：
            #    同一个位置被推断出两种类型。（造数脚本里踩过同一个坑。）
            "score": float(correct),
            "ts": created,
        },
    )

    rows = []
    for i, q in enumerate(qs[: answers + ungraded]):
        is_graded = i < answers
        rows.append(
            {
                "sid": sid,
                "uid": uid,
                "qid": q["id"],
                "seq": i + 1,
                # ★ 未判分的行**必须照样有 answered_at** —— 那才叫"答了没判分"；
                #   连 answered_at 都不给，造出来的是"翻过没答"，是完全另一回事，
                #   那样 §7-12 的用例就在验一个不存在的场景（而且它照样会"通过"）。
                "ok": (i < correct) if is_graded else None,
                "at": _at(day, hour, i + 1),
            }
        )
    item_ids = await _next_ids(db, len(rows))
    await db.execute(
        text(
            """
            INSERT INTO practice_items
                (id, session_id, user_id, question_id, seq, user_answer, is_correct,
                 score, time_ms, marked, show_analysis, answered_at, created_at)
            VALUES (:id, :sid, :uid, :qid, :seq, '{}'::jsonb, :ok, 0, 30000, false, false, :at, :ts)
            """
        ),
        [
            {
                "id": item_ids[i],
                "sid": r["sid"],
                "uid": r["uid"],
                "qid": r["qid"],
                "seq": r["seq"],
                "ok": r["ok"],
                "at": r["at"],
                "ts": created,
            }
            for i, r in enumerate(rows)
        ],
    )
    return {"user_id": uid, "session_id": sid, "answers": answers, "correct": correct}


async def _drop_probe(db: AsyncSession, user_id: int) -> None:
    """按用户删 —— 会话与明细靠外键 `ON DELETE CASCADE` 一起走。"""
    await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


# ------------------------------------------------------------------ §7-1 确定性


def test_overview_is_deterministic() -> None:
    """§7-1：同一时点连续两次调用**逐字段一致**（没有"每次都不一样"的字段混进来）。"""

    async def body() -> None:
        async with _session() as db:
            a = await stats_service.overview(db)
            b = await stats_service.overview(db)
        assert a == b, "同一时点的两次调用不一致：说明结果里混进了时间/随机因素"

    run(body())


# ------------------------------------------------------------------ §7-3 零分母


def test_zero_denominator_returns_null_not_zero() -> None:
    """§7-3：**没人答题的窗口**里，比值型字段必须是 `None`，不是 `0` / `0.00` / NaN。

    `0.00%` 的意思是"全错"，与"没人答题"**正好相反** —— 这是本批最容易骗过眼睛的错，
    因为它**不会报错**，只是把"没有数据"渲染成"表现很差"。
    """
    empty_day = _probe_base() - timedelta(days=1)  # 再往前一天，确保连探针都没有

    async def body() -> None:
        async with _session() as db:
            ov = await stats_service.overview(db, day=empty_day)
            cards = ov["cards"]
            assert cards["answers"]["value"] == 0
            assert cards["dau"]["value"] == 0
            assert cards["accuracy"]["value"] is None, "零分母返回了非 None —— 会被显示成 0.00%"
            assert cards["answers"]["per_capita"] is None
            assert cards["accuracy"]["delta_pp"] is None
            assert cards["dau"]["delta_ratio"] is None

            tr = await stats_service.trends(
                db, metric="accuracy", date_from=empty_day, date_to=empty_day
            )
            assert tr["series"][0]["points"] == [None], "空桶的比值型曲线补成了 0"

            wp = await stats_service.weak_points(db, date_from=empty_day, date_to=empty_day)
            assert wp["items"] == []

    run(body())


# ------------------------------------------------------------------ §7-4 时区


def test_timezone_day_boundary_shanghai() -> None:
    """§7-4：`00:30 (+08)` 的答题算**当天**，不算前一天。

    这条如果写错（用 UTC 日界），会**静默**地把 00:00–08:00 归到昨天 ——
    数字全都在，只是全错位。所以两个方向都要断言：
    "计入了当天" **且** "没被算进前一天"。
    """
    day = _probe_base()

    async def body() -> None:
        async with _session() as db:
            p = await _mk_probe(db, day=day, hour=0, answers=3, correct=1)  # 00:0x
            try:
                today = await stats_service.overview(db, day=day)
                prev = await stats_service.overview(db, day=day - timedelta(days=1))
                assert today["cards"]["answers"]["value"] == p["answers"]
                assert today["cards"]["dau"]["value"] == 1
                assert prev["cards"]["answers"]["value"] == 0, (
                    "00:30 的答题被算进了前一天（UTC 日界）"
                )
            finally:
                await _drop_probe(db, p["user_id"])

    run(body())


# ------------------------------------------------------------------ §7-11 同分母


def test_answered_and_accuracy_share_the_same_denominator() -> None:
    """§7-11：③ 的分子 == ④ 的分母，且能**交叉验算**。

    靠"两个数碰巧相等"不可靠；靠"共用同一个分母"才可靠。
    这里把交叉验算显式写出来：`答对数 == 答题量 × 正确率`。
    """
    day = _probe_base() + timedelta(days=1)
    n, c = 5, 3

    async def body() -> None:
        async with _session() as db:
            p = await _mk_probe(db, day=day, answers=n, correct=c)
            try:
                ov = await stats_service.overview(db, day=day)
                cards = ov["cards"]
                assert cards["answers"]["value"] == n
                assert cards["accuracy"]["value"] == pytest.approx(c / n, abs=1e-6)
                # ★ 交叉验算：不能只验"两个数各自对"，要验"它们互相解释得通"
                assert round(cards["accuracy"]["value"] * cards["answers"]["value"]) == c
            finally:
                await _drop_probe(db, p["user_id"])

    run(body())


# ------------------------------------------------------------------ §7-2 补空桶


def test_trends_fills_empty_buckets() -> None:
    """§7-2：7 天里有 1 天无数据 → `axis` 仍是 7 项，`filled_buckets == 1`。

    缺那一天的后果不是"少个点"，而是折线**把两天连成直线** ——
    看起来是"平稳"，实际是"断档"。所以必须补空桶，且**要能证明补了**。
    """
    base_day = _probe_base() + timedelta(days=10)
    days = [base_day + timedelta(days=i) for i in range(7)]
    hole = days[3]  # 故意空掉的一天

    async def body() -> None:
        async with _session() as db:
            probes = [await _mk_probe(db, day=d, answers=2, correct=1) for d in days if d != hole]
            try:
                tr = await stats_service.trends(
                    db, metric="answers", date_from=days[0], date_to=days[-1]
                )
                assert len(tr["axis"]) == 7, f"轴被压缩成 {len(tr['axis'])} 项 —— 空日没补上"
                assert tr["meta"]["total_buckets"] == 7
                assert tr["meta"]["filled_buckets"] == 1, "漏算/多算了补出来的空桶"
                # 第 4 桶（hole）必须是 0，其余 6 桶各 2
                pts = tr["series"][0]["points"]
                assert pts[3] == 0
                assert [p for i, p in enumerate(pts) if i != 3] == [2] * 6
                # 空桶的"正确率"是 None（不补 0）
                acc = await stats_service.trends(
                    db, metric="accuracy", date_from=days[0], date_to=days[-1]
                )
                assert acc["series"][0]["points"][3] is None
            finally:
                for p in probes:
                    await _drop_probe(db, p["user_id"])

    run(body())


def test_trends_week_granularity_keeps_half_open_window() -> None:
    """周粒度也要有轴、且轴点是**周一**（轴与 `date_trunc('week')` 必须对齐）。"""

    async def body() -> None:
        async with _session() as db:
            end = _probe_base() + timedelta(days=20)
            start = end - timedelta(days=27)
            tr = await stats_service.trends(
                db, metric="answers", granularity="week", date_from=start, date_to=end
            )
        assert tr["axis"], "周粒度轴为空"
        for label in tr["axis"]:
            d = date.fromisoformat(label)
            assert d.weekday() == 0, f"周桶 {label} 不是周一 —— 轴与 date_trunc 没对齐"

    run(body())


def test_trends_rejects_unknown_metric() -> None:
    """非法入参必须是**明确的 40001**，而不是悄悄返回一条空曲线。"""

    async def body() -> None:
        async with _session() as db:
            with pytest.raises(BizError) as ei:
                await stats_service.trends(db, metric="nope")
            assert ei.value.code == 40001

    run(body())


# ------------------------------------------------------------------ 分布：真/造 分离


def test_distributions_data_origin_separates_real_and_demo() -> None:
    """docs/20 §7-13 / §3.2：**真假是接口的属性**，不是前端的判断。

    `bank`（题库结构）→ `real`（源表 `questions` 现在就有真数据）；
    `practice`（作答分布）→ `demo`（`practice_items` 是造出来的）。
    前端只渲染 `meta.origin_label`，**不允许自己判断**。
    """
    day = _probe_base() + timedelta(days=20)

    async def body() -> None:
        async with _session() as db:
            bank = await stats_service.distributions(db, dim="subject", view="bank")
            assert bank["meta"]["data_origin"] == "real"
            assert bank["meta"]["origin_label"] == "真实"
            assert bank["items"], "bank 视图为空 —— 题库里明明有已发布的题"
            assert all(i["accuracy"] is None for i in bank["items"]), "题库结构不该有正确率"

            p = await _mk_probe(db, day=day, answers=2, correct=1)
            try:
                prac = await stats_service.distributions(
                    db, dim="subject", view="practice", date_from=day, date_to=day
                )
                assert prac["meta"]["data_origin"] == "demo"
                assert prac["meta"]["origin_label"] == "演示数据"
                assert prac["items"], "practice 视图没读到探针数据"
                assert all(i["accuracy"] is not None for i in prac["items"])
            finally:
                await _drop_probe(db, p["user_id"])

    run(body())


def test_distributions_bank_matches_direct_count() -> None:
    """口径校验：`bank` 视图的合计 == 直接用 COUNT 数出来的已发布题数。

    ★ 这条防的正是我在实现时踩到的那个坑：只按 `status='published'` 过滤，
    会把 **135 条"已发布但已删除"** 的题也算进去（实测 6213 vs 6078）——
    两个数都"看着正常"，只有对账才能发现。
    """

    async def body() -> None:
        async with _session() as db:
            bank = await stats_service.distributions(db, dim="subject", view="bank")
            total = sum(i["value"] for i in bank["items"])
            expect = (
                await db.execute(
                    text(
                        """
                        SELECT count(*) FROM questions
                        WHERE is_deleted = false AND status = 'published'
                        """
                    )
                )
            ).scalar()
            assert total == int(expect), f"分布合计 {total} != 直查 {expect}"

    run(body())


# ------------------------------------------------------------------ §7-5 漏斗


def test_funnel_is_monotonic_and_warns_when_not() -> None:
    """§7-5：三段必须单调（同一批人去重，段数不会变多）。违反时接口要**自己说出来**。"""

    async def body() -> None:
        async with _session() as db:
            f = await stats_service.funnel(db, cohort="all")
            counts = [s["count"] for s in f["stages"]]
            assert counts == sorted(counts, reverse=True), f"漏斗不单调：{f['stages']}"

            # ★★ 只断言"单调"是**不够的** —— 实测：把 funnel.sql 的
            #    `o.status = 'paid'` 改成 `o.status <> 'paid'`，三段依然单调，
            #    用例全绿（那个变异"存活"了）。所以每一段都要与**独立直查**对账。
            expect_registered = (
                await db.execute(text("SELECT count(*) FROM users WHERE NOT is_deleted"))
            ).scalar()
            expect_answered = (
                await db.execute(
                    text(
                        """
                        SELECT count(DISTINCT pi.user_id)
                        FROM practice_items pi JOIN users u ON u.id = pi.user_id
                        WHERE pi.answered_at IS NOT NULL AND NOT u.is_deleted
                        """
                    )
                )
            ).scalar()
            expect_paid = (
                await db.execute(
                    text(
                        """
                        SELECT count(DISTINCT o.user_id)
                        FROM orders o JOIN users u ON u.id = o.user_id
                        WHERE o.status = 'paid' AND NOT o.is_deleted AND NOT u.is_deleted
                        """
                    )
                )
            ).scalar()
            assert counts == [int(expect_registered), int(expect_answered), int(expect_paid)], (
                f"漏斗 {counts} != 直查 "
                f"{[int(expect_registered), int(expect_answered), int(expect_paid)]}"
            )
            assert f["meta"]["warnings"] == []
            # 转化率只在有分母时是数，否则 None（零分母不补 0）
            assert len(f["rates"]["step"]) == 2
            assert len(f["rates"]["cumulative"]) == 2

    run(body())


# ------------------------------------------------------------------ §7-12 数据体检


def test_integrity_check_flags_answered_but_ungraded() -> None:
    """§7-12：'答了没判分'必须被**显式发现**，而不是静默把正确率拉低。

    这类行的后果：进正确率的分母、不进分子 → 正确率**偏低**，而且**一条错都不报**。
    所以它必须能被断言为 0，并且能让 `overview` 的 `meta.warnings` 说话。
    """
    day = _probe_base() + timedelta(days=30)

    async def body() -> None:
        async with _session() as db:
            assert (await stats_service.integrity_check(db))["ok"] is True, "库里本来就有脏数据"

            p = await _mk_probe(db, day=day, answers=2, correct=1, ungraded=1)
            try:
                chk = await stats_service.integrity_check(db)
                assert chk["ok"] is False
                assert chk["ungraded_rows"] >= 1
                assert chk["meta"]["warnings"], "发现了缺陷却没给告警文案"

                ov = await stats_service.overview(db, day=day)
                assert ov["meta"]["warnings"], "overview 没有把'答了没判分'说出来"
                # 正确率确实被它拉低了：分子 1 / 分母 3
                assert ov["cards"]["accuracy"]["value"] == pytest.approx(1 / 3, abs=1e-6)
            finally:
                await _drop_probe(db, p["user_id"])

            assert (await stats_service.integrity_check(db))["ok"] is True, "清理没干净"

    run(body())


# ------------------------------------------------------------------ 弱项：样本门槛


def test_weak_points_honours_min_sample() -> None:
    """§2.5：样本不够的知识点**不能上榜** —— 否则"错 1 题 = 0%"会霸榜。"""

    day = _probe_base() + timedelta(days=40)

    async def body() -> None:
        async with _session() as db:
            p = await _mk_probe(db, day=day, answers=3, correct=0, same_kp=True)
            try:
                loose = await stats_service.weak_points(
                    db, min_sample=3, date_from=day, date_to=day
                )
                assert loose["items"], "样本刚好达标却没上榜"
                assert loose["items"][0]["accuracy"] == 0.0
                assert loose["items"][0]["sample"] == 3

                strict = await stats_service.weak_points(
                    db, min_sample=4, date_from=day, date_to=day
                )
                assert strict["items"] == [], "样本不足还是上榜了 —— 门槛没生效"
            finally:
                await _drop_probe(db, p["user_id"])

    run(body())


# ------------------------------------------------------------------ 四条硬约束


def test_constraint3_statement_timeout_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """硬约束 ③ + §7-7：超时**真的会生效**，且变成 `50004` / HTTP 503（可重试）。

    怎么做到确定性：把 SQL 换成一条**必然慢**的（`pg_sleep(1)`），
    再把超时调到 200ms —— 不是"赌某个查询刚好够慢"。
    同时验反向：超时给足时同一条 SQL **必须成功** —— 否则可能只是"SQL 本身写错了"，
    那样这条用例就变成了在验一个假象（硬约定 J）。
    """

    def slow(_name: str) -> str:
        # ⚠️ `load_sql` 是**同步**函数（`_run` 里直接 `text(load_sql(name))`）。
        #    这里写成 `async def` 会返回协程 → `text(<coroutine>)` 报错，
        #    而且只有在别的用例上才会冒出 "coroutine was never awaited" 的警告。
        return "SELECT pg_sleep(1)"

    async def body() -> None:
        async with _session() as db:
            monkeypatch.setattr(stats_service, "load_sql", slow)

            # 反向：给足时间 → 成功（证明 SQL 与连接都是好的）
            monkeypatch.setattr(stats_service, "STATS_TIMEOUT_MS", 5000)
            rows = await stats_service._run(db, "_probe_slow", {})
            assert rows and rows[0], "慢 SQL 在充裕超时下也没跑通 —— 这条用例的前提不成立"

            # 正向：超时 200ms → 必须抛 50004，而不是挂住
            monkeypatch.setattr(stats_service, "STATS_TIMEOUT_MS", 200)
            with pytest.raises(BizError) as ei:
                await stats_service._run(db, "_probe_slow", {})
            assert ei.value.code == 50004
            assert ei.value.http_status == 503

    run(body())


def test_constraint1_params_are_bound_not_interpolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """硬约束 ①：用户输入**必须**出现在参数里、**不能**出现在 SQL 文本里。

    做法是拦截 `AsyncSession.execute`，把"实际发给数据库的 SQL 文本"抓下来，
    再断言那个值**不在文本中**。这条能抓住"有人为了省事把值拼进 SQL"。
    """
    magic = 424242
    seen: list[tuple[str, dict | None]] = []
    real = AsyncSession.execute

    async def spy(self: AsyncSession, stmt: Any, params: Any = None, **kw: Any) -> Any:
        seen.append((str(stmt), params))
        return await real(self, stmt, params or {}, **kw)

    async def body() -> None:
        async with _session() as db:
            monkeypatch.setattr(AsyncSession, "execute", spy)
            await stats_service.trends(db, metric="answers", subject_id=magic)

    run(body())

    queries = [s for s, _ in seen if "practice_items" in s]
    assert queries, "没抓到业务查询（拦截可能没生效）"
    for sql in queries:
        assert str(magic) not in sql, f"用户输入被拼进了 SQL：{sql[:120]}"
    assert any(p and p.get("subject_id") == magic for _, p in seen), (
        "subject_id 没有作为绑定参数传下去"
    )


def test_constraint2_run_returns_dicts_not_tuples() -> None:
    """硬约束 ②：一律返回 `dict`。

    tuple 的字段顺序是**隐式契约** —— 以后 SQL 里多一列或换个顺序，
    取值处会**静默取错**（比如把 `correct` 读成 `value`），而**不会报错**。
    """

    async def body() -> None:
        async with _session() as db:
            rows = await stats_service._run(db, "integrity", {})
            assert rows and isinstance(rows[0], dict), f"拿到的不是 dict：{type(rows[0])}"
            assert "ungraded_rows" in rows[0], "按列名取不到 —— 说明不是按名字返回的"

    run(body())


def test_constraint4_sql_lives_in_files_not_in_python() -> None:
    """硬约束 ④：SQL 只在 `app/sql/stats/*.sql` 里，Python 侧只读文件。

    判据：`stats_service.py` 的源码里**不出现 `FROM `** ——
    一旦有人图省事把查询内联进 Python，这条会红。
    """
    src = Path(stats_service.__file__).read_text(encoding="utf-8")
    # 去掉 docstring 与注释，避免"文档里举例说明"被误判
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(line.split("#")[0] for line in code.splitlines())
    # ⚠️ 判据是"SQL 只从文件进来"，**不是**"源码里没有 FROM" ——
    #    后者会被 `from __future__ import annotations` 命中（这条一开始就写错过一次）。
    assert "text(load_sql(" in code, "查询没有走 load_sql()"
    # ⚠️ 用 `(?<![\w.])` 排除 `read_text(` —— 朴素的 `"text(" in code` 会把它算进去
    #    （第一版就是这么错的：数出 3 处，实际只有 2 处）。
    calls = re.findall(r"(?<![\w.])text\(", code)
    assert len(calls) == 2, (
        f"stats_service 里有 {len(calls)} 处 text( 调用；只允许两处："
        "set_config 的超时设置 + load_sql(name)。多出来的就是把 SQL 内联进来了。"
    )

    sql_files = sorted(p.name for p in stats_service.SQL_DIR.glob("*.sql"))
    expected = {
        "overview.sql",
        "trends.sql",
        "distributions_bank.sql",
        "distributions_practice.sql",
        "funnel.sql",
        "weak_points.sql",
        "integrity.sql",
    }
    assert expected.issubset(set(sql_files)), f"缺少 SQL 文件：{expected - set(sql_files)}"

    # 每份 SQL 都能被读出来，且非空
    for name in expected:
        assert stats_service.load_sql(name[:-4]).strip(), f"{name} 是空的"


def test_every_query_has_a_timeout_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """硬约束 ③ 的**防回退**：`_run()` 每次都会先设一次 `statement_timeout`。

    如果没有这条，将来有人新写一个"直接 `db.execute`"的查询就绕过了超时，
    而**没有任何东西会提醒**。
    """
    seen: list[str] = []
    real = AsyncSession.execute

    async def spy(self: AsyncSession, stmt: Any, params: Any = None, **kw: Any) -> Any:
        seen.append(str(stmt))
        return await real(self, stmt, params or {}, **kw)

    async def body() -> None:
        async with _session() as db:
            monkeypatch.setattr(AsyncSession, "execute", spy)
            await stats_service.integrity_check(db)

    run(body())
    assert any("set_config('statement_timeout'" in s for s in seen), "没有设置 statement_timeout"
    assert any("practice_items" in s for s in seen), "业务查询没被执行"


def test_parameter_validation_contracts() -> None:
    """非法入参必须是**明确的 40001**，而不是悄悄返回空结果 / 500。

    这些分支是接口的**准入契约**。写错的后果不是炸，而是调用方拿到一个
    "看着正常但没意义"的响应 —— 例如 `dim` 拼错 → 返回空数组，
    前端画一张空图，谁也不知道是参数错了还是真没数据。
    """

    async def body() -> None:
        async with _session() as db:
            cases = [
                (
                    "trends 非法 granularity",
                    lambda: stats_service.trends(db, metric="answers", granularity="hour"),
                ),
                (
                    "trends 日期倒置",
                    lambda: stats_service.trends(
                        db, metric="answers", date_from=date(2026, 2, 1), date_to=date(2026, 1, 1)
                    ),
                ),
                (
                    "distributions 非法 dim",
                    lambda: stats_service.distributions(db, dim="nope", view="bank"),
                ),
                (
                    "distributions 非法 view",
                    lambda: stats_service.distributions(db, dim="subject", view="nope"),
                ),
                ("funnel 非法 cohort", lambda: stats_service.funnel(db, cohort="7d")),
                ("weak_points limit=0", lambda: stats_service.weak_points(db, limit=0)),
                ("weak_points limit=101", lambda: stats_service.weak_points(db, limit=101)),
                ("weak_points min_sample=0", lambda: stats_service.weak_points(db, min_sample=0)),
            ]
            for label, call in cases:
                with pytest.raises(BizError) as ei:
                    await call()
                assert ei.value.code == 40001, f"{label} → {ei.value.code}"
                assert ei.value.http_status == 400, label

    run(body())


def test_trends_month_granularity_aligns_to_first_day() -> None:
    """月粒度的桶必须落在**每月 1 号**（轴与 `date_trunc('month')` 对齐）。

    不对齐的后果不是报错，而是轴与数据**错位一格** —— 图上看得出"怪"，
    但说不出哪里怪。
    """

    async def body() -> None:
        async with _session() as db:
            end = _probe_base() + timedelta(days=60)
            tr = await stats_service.trends(
                db,
                metric="answers",
                granularity="month",
                date_from=end - timedelta(days=89),
                date_to=end,
            )
        assert tr["axis"], "月粒度轴为空"
        for label in tr["axis"]:
            assert date.fromisoformat(label).day == 1, f"月桶 {label} 不是 1 号"

    run(body())


def test_funnel_warns_when_not_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    """漏斗不单调时接口要**自己说出来**，而不是让看的人觉得"有点怪"。

    这里把 SQL 换成一条**必然不单调**的结果 —— 验的是"守卫生效"，
    而不是"当前数据恰好单调"。
    """

    def fake(_name: str) -> str:
        return "SELECT 1 AS registered, 5 AS first_answered, 9 AS paid"

    async def body() -> None:
        async with _session() as db:
            monkeypatch.setattr(stats_service, "load_sql", fake)
            f = await stats_service.funnel(db, cohort="all")
            assert f["meta"]["warnings"], "不单调却没给出告警"
            assert f["stages"][0]["count"] == 1

    run(body())


def test_non_timeout_db_error_is_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """**只有超时**才转成 `50004`；其它数据库错误必须原样抛出。

    否则"表不存在"会被伪装成"查询超时"，排查方向直接跑偏 ——
    正是"失败信息指向的地方 ≠ 真正的失败点"那一类。
    """

    def fake(_name: str) -> str:
        return "SELECT * FROM __no_such_table__"

    async def body() -> None:
        async with _session() as db:
            monkeypatch.setattr(stats_service, "load_sql", fake)
            with pytest.raises(Exception) as ei:
                await stats_service._run(db, "_probe_bad", {})
            assert not isinstance(ei.value, BizError), "数据库错误被伪装成了业务错误"

    run(body())


def test_every_param_is_explicitly_cast() -> None:
    """SQL 约定（`app/sql/stats/README.md`）：每个 `:param` 用法都要**显式 CAST**。

    **为什么做成测试而不是只写文档**：约定的价值在于"能被自动检查"。
    只写在 README 里的约定靠的是"下次还记得" —— 而这一批我自己就**把同一个类型错犯了两次**
    （`:c` 同时当 smallint 与 numeric）。文档是给人看的，测试是给门禁看的。
    """

    offenders: list[str] = []
    for path in sorted(stats_service.SQL_DIR.glob("*.sql")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not re.search(r"(?<![:\w]):[a-z_]+", line):
                continue  # 这行没有绑定参数
            if line.lstrip().startswith("--"):
                offenders.append(f"{path.name}:{i} 注释里出现参数名 —— {line.strip()[:70]}")
            elif "CAST(" not in line:
                offenders.append(f"{path.name}:{i} 有参数但没有 CAST —— {line.strip()[:70]}")
    assert not offenders, (
        "有参数没有显式 CAST（类型会靠 Postgres 猜，见 app/sql/stats/README.md）：\n"
        + "\n".join(offenders)
    )


def test_unreachable_db_surfaces_as_error_not_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    """硬约定 N 的体检：连接层坏掉时应当**报错**，而不是安静地一直等。

    这里把 engine 指到一个**立即拒绝**（RST）的地址，而不是"丢包地址" ——
    后者的耗时由宿主机 TCP 栈的 SYN 重试预算决定，那是"把网络超时当被测对象"（坑 57）。
    判据两条：① 抛异常；② **很快**抛（< 5s）。第 ② 条才是这条用例的重点 ——
    只断言"抛了异常"的话，一个"等 128 秒后再抛"的实现也能过。
    """
    bad = create_async_engine("postgresql+asyncpg://yijian@127.0.0.1:1/yijian", poolclass=NullPool)
    factory = async_sessionmaker(bind=bad, class_=AsyncSession, expire_on_commit=False)

    async def body() -> float:
        started = time.monotonic()
        try:
            async with factory() as db:
                # 异常类型随驱动版本变（OSError / InterfaceError / OperationalError），
                # 所以断言的是**行为**（抛错 + 快），不是某个具体类名。
                with pytest.raises(Exception):
                    await stats_service.overview(db)
            return time.monotonic() - started
        finally:
            await bad.dispose()

    elapsed = run(body())
    assert elapsed < 5, f"不可达地址花了 {elapsed:.2f}s 才失败 —— 有东西在无界等待"


def test_every_endpoint_meta_carries_origin_label() -> None:
    """★ 每个业务端点的 `meta` 都要有 `origin_label`，且与 `data_origin` **一致**。

    为什么要有这条：前端的"真 / 造"标记**只渲染后端给的文案**，不自己映射
    （`docs/20` §7 判据 13：真假是**接口属性**，不是渲染者的判断）。
    只要有一个端点漏了 `origin_label`，前端就得为它写一条兜底分支 ——
    而那条分支一旦写下来，就等于"前端开始判断真假"，判据 13 当场只落实了一半。

    ⚠️ 这条是 **S1-c（前端）顺手改后端**时一起补的：前端要渲染什么，后端就得给全。
       "顺手改后端"**必须带测试** —— 否则新增的行没人覆盖，覆盖率会往下走
       （`.coveragerc` 里那段"k=2 还能容多少条未测代码"的算式说的就是这件事）。
    """
    expected = {"real": "真实", "demo": "演示数据"}

    async def body() -> None:
        async with _session() as db:
            results = {
                "overview": await stats_service.overview(db),
                "trends": await stats_service.trends(db, metric="answers"),
                "distributions(bank)": await stats_service.distributions(
                    db, dim="subject", view="bank"
                ),
                "distributions(practice)": await stats_service.distributions(
                    db, dim="subject", view="practice"
                ),
                "funnel": await stats_service.funnel(db, cohort="all"),
                "weak_points": await stats_service.weak_points(db, limit=1),
            }

            problems: list[str] = []
            for name, payload in results.items():
                meta = payload.get("meta") or {}
                origin = meta.get("data_origin")
                label = meta.get("origin_label")
                if origin not in expected:
                    problems.append(f"{name}: data_origin={origin!r} 不在 {sorted(expected)}")
                elif label != expected[origin]:
                    problems.append(f"{name}: data_origin={origin} 但 origin_label={label!r}")
            assert not problems, "端点 meta 的 origin_label 不齐 / 不一致：\n" + "\n".join(problems)

            # 顺带把 bank / practice 的真假钉死：防止有人"顺手"改掉 view 的作用
            # （那会让整屏的真假标注一起反过来，而界面上看不出来）。
            assert results["distributions(bank)"]["meta"]["data_origin"] == "real"
            assert results["distributions(practice)"]["meta"]["data_origin"] == "demo"

    run(body())
