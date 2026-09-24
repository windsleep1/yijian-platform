#!/usr/bin/env python
"""统计看板造数（Batch 8 · S1-a）。**没有它，看板全是 0。**

    python tools/local-verify/seed-stats.py                      # 造数（默认 40 用户 / 30 天）
    python tools/local-verify/seed-stats.py --clean              # **只打印**将要删的行数（dry-run）
    python tools/local-verify/seed-stats.py --clean --yes        # 真的删（只删本脚本造的）
    python tools/local-verify/seed-stats.py --seed 7 --users 20  # 可复现

## ★★ `--clean` 的语义（2026-09-24 用户定的硬约束）

| 规则 | 实现 |
|---|---|
| **只删本脚本造的数据** | 每张表都靠**文本标记**定位：`users.remark` / `practice_sessions.title`+`config.__seed_batch` / `exams.title` / `orders.order_no`，统一前缀 `stats-seed-` |
| **每条造的数据带可识别标记** | 同上；另外所有 id 取自**保留段** `SEED_ID_BASE` 以上（真实雪花 ID ≈ 1.7e18，保留段 7e18，不会撞） |
| **绝不"全部清空"** | ① 删除模式**必须**以 `stats-seed-` 开头（不是则直接拒绝执行）；② 每张表删除前先算 `匹配数 / 表总行数`，**相等就拒绝**并让人来看；③ 不带 `--yes` 只打印计划 |
| **按批次回滚，不按全清** | `--batch <id>` 只回滚那一次；不带则回滚所有 `stats-seed-*` 批次 |

这与导入管道的回滚是**同一类问题**：按批次回滚，不按全清。
造数脚本误删真实数据是**灾难性**的，所以宁可多三道守卫、多打一遍字。

## 刻意造出的"可验证形状"（供 §7 / §10 用）

- **故意留空日**：`--gap-days 2` 个日期**不造任何数据** → 趋势图必须靠
  `generate_series` 补出空桶（`meta.filled_buckets > 0` 才说明补空逻辑真的在工作）。
- **正确率有分布**：每题的 `is_correct` 按 0.6 概率，所以"正确率"不会是 0 或 100
  （两端值会让"算错了"和"算对了"长得一样）。
- **付费是少数**：只有约 5% 的用户有 `paid` 订单 → 漏斗第三段非 0 但小于第二段，
  这样"漏斗单调"这条断言**真的在验东西**（都是 0 或都相等的话，单调性恒真）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

# 上海时区（与 app/services/stats_service.py::TZ 一致）
TZ = timezone(timedelta(hours=8))

#: 标记前缀。**`--clean` 的一切都建立在它之上** —— 见文件头。
BATCH_PREFIX = "stats-seed-"

#: 保留 ID 段：真实雪花 ID ≈ 1.7e18，这里从 7e18 起，永不碰撞。
SEED_ID_BASE = 7_000_000_000_000_000_000

#: `subjects.professional` 的实际取值（实测自本机库；没有对应的字典表，
#: 所以造数只能用这组真码，让"人的专业"分布与库里的码空间一致）。
PROFESSIONALS = ("jz", "mhjc", "sz", "gl", "gkhd", "ky", "jd", "shsd", "tl", "txyg")

#: (表名, 删除条件, 人话说明) —— 表名是**代码常量**，不来自输入。
CLEAN_PLAN: tuple[tuple[str, str, str], ...] = (
    (
        "practice_items",
        "session_id IN (SELECT id FROM practice_sessions WHERE title LIKE $1)",
        "练习明细（按会话级联，先删明细再删会话）",
    ),
    ("practice_sessions", "title LIKE $1", "练习会话"),
    (
        "exam_attempts",
        "exam_id IN (SELECT id FROM exams WHERE title LIKE $1)",
        "模考作答（先删作答再删卷子）",
    ),
    ("exams", "title LIKE $1", "模考卷（本脚本自建）"),
    ("orders", "order_no LIKE $1", "订单"),
    (
        "user_profiles",
        "user_id IN (SELECT id FROM users WHERE remark LIKE $1)",
        "造数用户的档案",
    ),
    ("users", "remark LIKE $1", "造数用户（最后删，前面都靠它级联定位）"),
)

#: 每张表的"总行数"（做"匹配数 == 总行数 → 拒绝"的对照用）。
TABLE_TOTALS = (
    "practice_items", "practice_sessions", "exam_attempts", "exams",
    "orders", "user_profiles", "users",
)


# ------------------------------------------------------------------ 安全守卫


def marker_pattern(batch: str | None) -> str:
    """把 `--batch` 变成 `LIKE` 模式。**绝不允许返回一个能匹配全表的模式。**

    这是"绝不全部清空"的第一道闸：返回的字符串**必须**以 `BATCH_PREFIX` 开头，
    否则直接拒绝执行 —— 一个空模式（`%`）会把整张表删掉，而它看起来完全合法。
    """
    pattern = f"{batch}%" if batch else f"{BATCH_PREFIX}%"
    if not pattern.startswith(BATCH_PREFIX):
        raise SystemExit(
            f"拒绝执行：删除模式 {pattern!r} 不以 {BATCH_PREFIX!r} 开头。\n"
            "这会导致 DELETE 命中本脚本没造过的数据。"
        )
    if len(pattern) <= len(BATCH_PREFIX):
        raise SystemExit(f"拒绝执行：删除模式 {pattern!r} 太短，可能匹配到非本脚本数据。")
    return pattern


async def guard_not_everything(
    conn: asyncpg.Connection, table: str, where: str, pattern: str, label: str
) -> int:
    """删之前先算一次：**匹配数 == 表总行数 → 拒绝**。

    这一条防的是"标记写错 / 全表恰好都带标记"这种极端情况 ——
    它不会报错，只会安静地把表删空。宁可在这里失败。
    """
    matched = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}", pattern)
    total = await conn.fetchval(f"SELECT count(*) FROM {table}")
    if total and matched == total:
        raise SystemExit(
            f"拒绝执行：{label} 匹配 {matched} 行 == {table} 总行数 {total}。\n"
            "这不像\"只删本脚本造的数据\"。请人工确认后再处理。"
        )
    return int(matched)


# ------------------------------------------------------------------ 造数


#: 带 `id` 列、且本脚本会写入的表（`user_profiles` 没有 id 列 —— 它的主键是 user_id）。
ID_TABLES = ("users", "practice_sessions", "practice_items", "exams", "exam_attempts", "orders")


async def next_free_id(conn: asyncpg.Connection) -> int:
    """保留段内的下一个可用 id。

    ⚠️ 不能每次从 `SEED_ID_BASE + 1` 开始 —— 只要上一次造数留下过任何一行
    （比如上一次在**中途失败**），第二次就会撞 `users_pkey`。
    实测踩到过：第一次失败留下的 40 个用户，让第二次直接 duplicate key。
    所以这里扫一遍保留段里的最大值，从它之后接着发。
    """
    base = SEED_ID_BASE
    for table in ID_TABLES:
        got = await conn.fetchval(f"SELECT max(id) FROM {table} WHERE id >= $1", SEED_ID_BASE)
        if got is not None and got > base:
            base = int(got)
    return base + 1


def _id_factory(start: int) -> Any:
    counter = start - 1

    def make() -> int:
        nonlocal counter
        counter += 1
        return counter

    return make


def _day_utc(d: date, hour: int, minute: int = 0) -> datetime:
    """构造"上海时间 d 日 hour:minute"对应的**绝对时刻**（存 TIMESTAMPTZ）。"""
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ).astimezone(timezone.utc)


async def seed(args: argparse.Namespace, conn: asyncpg.Connection) -> dict[str, Any]:
    rng = random.Random(args.seed)
    batch = args.batch or f"{BATCH_PREFIX}{rng.getrandbits(32):08x}"

    # ★ 残留检查：标记行还在就**拒绝**，而不是往上叠。
    #   往上叠的后果不是报错，而是**看板数字被重复计数** ——
    #   "看板数字与真实数不一致"是本批最难发现的缺陷（docs/20 §10）。
    leftovers = 0
    for table, where in (
        ("users", "remark LIKE $1"),
        ("practice_sessions", "title LIKE $1"),
    ):
        leftovers += int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}",
                                             f"{BATCH_PREFIX}%"))
    if leftovers and not args.force:
        raise SystemExit(
            f"库里已有 {leftovers} 行本脚本造的数据（users/practice_sessions 标记命中）。\n"
            "先回滚再重造：\n"
            "    python tools/local-verify/seed-stats.py --clean --yes\n"
            "（确认要叠加：加 --force）"
        )

    nid = _id_factory(await next_free_id(conn))

    # ---- 真实数据前提：必须有已发布的题、科目、知识点、商品 ----
    subjects = await conn.fetch(
        "SELECT id, name FROM subjects WHERE status = 'on' ORDER BY sort_no, id"
    )
    if not subjects:
        raise SystemExit("库里没有启用中的科目 —— 先跑 python -m app.cli seed-rbac / 迁移与种子")
    questions = await conn.fetch(
        """
        SELECT id, subject_id, knowledge_point_id
        FROM questions
        WHERE is_deleted = false AND status = 'published'
        ORDER BY id
        """
    )
    if not questions:
        raise SystemExit("库里没有已发布的题 —— 先跑 python -m app.cli seed-questions")
    product = await conn.fetchrow(
        "SELECT id FROM products WHERE NOT is_deleted ORDER BY id LIMIT 1"
    )
    if not product:
        raise SystemExit("库里没有可下单的商品（orders.product_id 是 NOT NULL 外键）")

    today = datetime.now(TZ).date()
    days = [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]
    # 故意留空：从最后往前取 gap_days 天，**不造任何数据**
    gap = set(days[: max(0, args.gap_days)])
    active_days = [d for d in days if d not in gap]

    summary: dict[str, Any] = {
        "batch": batch,
        "users": 0, "profiles": 0, "sessions": 0, "items": 0,
        "exams": 0, "attempts": 0, "orders": 0,
        "active_days": len(active_days), "empty_days": len(gap),
    }

    # ---------------- users + user_profiles ----------------
    user_ids: list[int] = []
    for i in range(args.users):
        uid = nid()
        reg_day = active_days[0] + timedelta(days=rng.randint(0, max(0, len(active_days) - 1)))
        phone = f"1{rng.randint(3, 9)}" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        created = _day_utc(reg_day, rng.randint(8, 23), rng.randint(0, 59))
        await conn.execute(
            """
            INSERT INTO users (id, phone, nickname, status, register_source,
                               is_deleted, remark, created_at, updated_at)
            VALUES ($1, $2, $3, 'active', 'h5', false, $4, $5, $5)
            """,
            uid, phone, f"[demo] 统计造数 {i + 1:03d}", batch, created,
        )
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, professional, exam_level, created_at, updated_at)
            VALUES ($1, $2, 'yijian', $3, $3)
            """,
            uid, rng.choice(PROFESSIONALS), created,
        )
        user_ids.append(uid)
        summary["users"] += 1
        summary["profiles"] += 1

    # 只有一小部分人付费 → 漏斗第三段非 0 但远小于第二段（这样"单调"才在验东西）
    paid_users = set(rng.sample(user_ids, max(1, len(user_ids) // 20)))

    # ---------------- 练习会话 + 明细 ----------------
    #: 每天每人的答题量（合成一条"日总量"曲线，让趋势图有形状）
    questions_by_subject: dict[int, list[asyncpg.Record]] = {}
    for q in questions:
        questions_by_subject.setdefault(q["subject_id"], []).append(q)

    for uid in user_ids:
        for d in active_days:
            if rng.random() > args.active_rate:
                continue
            subject_id = rng.choice(subjects)["id"]
            pool = questions_by_subject.get(subject_id) or questions
            n_items = rng.randint(5, 25)
            picked = rng.sample(pool, min(n_items, len(pool)))

            sid = nid()
            started = _day_utc(d, rng.randint(7, 22), rng.randint(0, 59))
            total_sec = 0
            answered = 0
            correct = 0
            item_rows = []
            for seq, q in enumerate(picked, start=1):
                ok = rng.random() < args.accuracy
                ms = rng.randint(8_000, 180_000)
                total_sec += ms // 1000
                answered += 1
                correct += 1 if ok else 0
                item_rows.append((nid(), q["id"], seq, ok, ms, started + timedelta(seconds=total_sec)))

            # ★ 会话上的汇总必须与明细一致 —— 否则"两个数会打起来"（docs/20 §2.1）。
            await conn.execute(
                """
                INSERT INTO practice_sessions
                    (id, user_id, mode, subject_id, title, config, total, answered, correct,
                     score, duration_sec, status, started_at, finished_at, created_at, updated_at)
                VALUES ($1,$2,'chapter',$3,$4,$5,$6,$7,$8,$9,$10,'finished',$11,$12,$11,$11)
                """,
                sid, uid, subject_id, batch, '{"__seed_batch": "' + batch + '"}',
                answered, answered, correct, float(correct), total_sec, started,
                started + timedelta(seconds=total_sec),
            )
            # ⚠️ 占位符 $1..$10 与元组元素**必须一一对应**：
            #   id, session_id, user_id, question_id, seq, user_answer,
            #   is_correct, score, time_ms, answered_at
            # （少给一个不会报"参数不够"，而是**位置整体错位** —— 那种错很难看出来。）
            await conn.executemany(
                """
                INSERT INTO practice_items
                    (id, session_id, user_id, question_id, seq, user_answer, is_correct,
                     score, time_ms, marked, show_analysis, answered_at, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,false,false,$10,$10)
                """,
                [
                    (iid, sid, uid, qid, seq, '{"value": "A"}', ok, float(ok), ms, at)
                    for (iid, qid, seq, ok, ms, at) in item_rows
                ],
            )
            summary["sessions"] += 1
            summary["items"] += len(item_rows)

    # ---------------- 模考卷 + 作答 ----------------
    exam_ids: list[int] = []
    for idx, s in enumerate(subjects[: args.exams], start=1):
        eid = nid()
        await conn.execute(
            """
            INSERT INTO exams (id, subject_id, title, type, total_score, pass_score,
                               question_count, duration_min, status, published_at,
                               created_at, updated_at)
            VALUES ($1,$2,$3,'mock',160,96,80,180,'published',now(),now(),now())
            """,
            eid, s["id"], f"{batch} #{idx}",
        )
        exam_ids.append(eid)
        summary["exams"] += 1

    for uid in user_ids:
        for eid in exam_ids:
            if rng.random() > args.exam_rate:
                continue
            sub = rng.choice(subjects)["id"]
            started = _day_utc(rng.choice(active_days), rng.randint(8, 20))
            correct = rng.randint(30, 70)
            wrong = 80 - correct
            score = correct * 2
            await conn.execute(
                """
                INSERT INTO exam_attempts
                    (id, exam_id, user_id, subject_id, attempt_no, objective_score,
                     subjective_score, total_score, full_score, correct_count, wrong_count,
                     unanswered_count, is_pass, duration_sec, status, started_at,
                     submitted_at, scored_at, created_at, updated_at)
                VALUES ($1,$2,$3,$4,1,$5,0,$5,160,$6,$7,0,$8,$9,'scored',$10,$11,$11,$10,$10)
                """,
                nid(), eid, uid, sub, float(score), correct, wrong,
                score >= 96, rng.randint(3600, 10800), started,
                started + timedelta(minutes=rng.randint(60, 170)),
            )
            summary["attempts"] += 1

    # ---------------- 订单（漏斗第三段）----------------
    for n, uid in enumerate(sorted(paid_users), start=1):
        paid_at = _day_utc(rng.choice(active_days), rng.randint(8, 23))
        await conn.execute(
            """
            INSERT INTO orders (id, order_no, user_id, product_id, product_snapshot, quantity,
                                original_cents, discount_cents, amount_cents, pay_channel,
                                status, paid_at, is_deleted, created_at, updated_at)
            VALUES ($1,$2,$3,$4,'{}'::jsonb,1,19900,0,19900,'wechat','paid',$5,false,$5,$5)
            """,
            nid(), f"{batch}-{n:04d}", uid, product["id"], paid_at,
        )
        summary["orders"] += 1

    return summary


# ------------------------------------------------------------------ 清场


async def clean(conn: asyncpg.Connection, *, batch: str | None, yes: bool) -> int:
    pattern = marker_pattern(batch)
    print(f"标记模式: {pattern!r}")
    print()

    planned: list[tuple[str, int]] = []
    for table, where, label in CLEAN_PLAN:
        matched = await guard_not_everything(conn, table, where, pattern, label)
        planned.append((f"{table}  ({label})", matched))

    print("将要删除：")
    for name, n in planned:
        print(f"  {name:<60} {n:>8} 行")
    total = sum(n for _, n in planned)
    print(f"  {'合计':<60} {total:>8} 行")
    print()

    if total == 0:
        print("没有本脚本造的数据（无需清理）。")
        return 0

    if not yes:
        print("== dry-run 结束。确认无误后加 --yes 真正执行。==")
        return 0

    # 用事务包住：要么全删干净，要么一行不动（避免删一半留下孤儿明细）
    async with conn.transaction():
        for table, where, label in CLEAN_PLAN:
            await conn.execute(f"DELETE FROM {table} WHERE {where}", pattern)
    print("已删除（仅限标记行）。")
    return total


# ------------------------------------------------------------------ CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="统计看板造数 / 按批次回滚")
    p.add_argument("--clean", action="store_true", help="删除本脚本造的数据（默认只打印计划）")
    p.add_argument("--yes", action="store_true", help="配合 --clean：确认执行删除")
    p.add_argument("--batch", default=None, help=f"批次号（默认随机 {BATCH_PREFIX}xxxxxxxx）")
    p.add_argument("--seed", type=int, default=20260924, help="随机种子（同种子 → 同数据）")
    p.add_argument("--users", type=int, default=40, help="造多少用户")
    p.add_argument("--days", type=int, default=30, help="覆盖最近多少天")
    p.add_argument("--gap-days", type=int, default=2, help="故意留几天完全没有数据（验补空桶）")
    p.add_argument("--active-rate", type=float, default=0.45, help="每人每天「有答题」的概率")
    p.add_argument("--accuracy", type=float, default=0.6, help="每题答对概率")
    p.add_argument("--exam-rate", type=float, default=0.12, help="每人每卷的提交概率")
    p.add_argument("--exams", type=int, default=3, help="造几张模考卷")
    p.add_argument("--dsn", default=None, help="覆盖 DSN（默认取 DATABASE_URL）")
    p.add_argument("--force", action="store_true", help="已有标记行时仍然继续（会叠加计数，慎用）")
    return p.parse_args(argv)


def dsn_from_env(explicit: str | None) -> str:
    raw = explicit or os.environ.get("DATABASE_URL")
    if not raw:
        raise SystemExit(
            "没有数据库连接串：请设置 DATABASE_URL，或用 --dsn 指定。\n"
            "  本地示例: DATABASE_URL=postgresql+asyncpg://yijian@127.0.0.1:55432/yijian"
        )
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = await asyncpg.connect(dsn_from_env(args.dsn), timeout=10, command_timeout=120)
    try:
        if args.clean:
            await clean(conn, batch=args.batch, yes=args.yes)
            return 0
        summary = await seed(args, conn)
        print("造数完成：")
        for k, v in summary.items():
            print(f"  {k:<14} {v}")
        print()
        print(f"回滚： python tools/local-verify/seed-stats.py --clean --batch {summary['batch']} --yes")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
