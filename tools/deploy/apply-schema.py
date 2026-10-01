#!/usr/bin/env python
"""把 `db/schema.sql` + `db/migrations/*.sql` 灌进一个**空库**（托管 PG 初始化用）。

为什么需要它
------------
托管 PG（Neon / Supabase / Render PG）建出来是**空库**：没有表、没有 RBAC 种子。
本项目把这些全部放在 `db/schema.sql` + `db/migrations/*.sql` 里，而**应用自己不会建表**
（`app.cli init-db` 只在"库确实是空的"时候执行 schema.sql，而且它要求容器里能读到
那个文件 —— 托管平台上没有 compose 的卷挂载，见 `docs/27`）。

CI 用的是一条 `psql -f` 的循环，但**不能指望部署的人本机装了 psql**（Windows 默认没有）。
⇒ 这里用 **asyncpg 直连**（项目本来就依赖它），只需要 Python。

安全闸（硬约定 O 的同族：清场/建库必须**先确认目标**）
----------------------------------------------------
- 必须显式 `--yes` 才真写；不带就是**预演**（只打印将要做的事）。
- 目标库里**已经有 `public.users`** ⇒ **拒绝**。理由：本脚本只服务于"空库初始化"，
  而 `db/migrations/` 里几条**不是幂等**的（重复应用会报错或改错数据）。
  已经有表说明要么已经建过、要么那是**别的库** —— 两种都不该继续。
  确实要在一个非空库上补跑迁移，用 `--force-migrations`（只跑迁移、不跑 schema）。

为什么要加 `--questions`（2026-10-01）
-------------------------------------
`docs/27` §1.5 原来给的两条路**都走不通**（用户真走到那一步才发现）：

- **Render Shell 里跑 `python -m app.cli seed-questions`**：容器里**根本没有种子文件** ——
  `data/` 被 `.gitignore` 排除（`.gitignore:23`）⇒ 它**连仓库里都没有**，
  Render 从 git clone ⇒ `/app` 下不存在。日志里那句
  `data/seed/questions.sql not found; skipping` 就是它。
- **本机跑 `python -m app.cli seed-questions`**：要装**全量** `apps/api` 依赖才能起
  `app.cli`（它 import fastapi）。而本脚本只依赖 `asyncpg`。

⇒ 判据：**"文档里那一步"必须能被人照做**。本脚本的连接路径已经在同一台机器上
  被真跑通过（`docs/27` §1.3 就是它），所以把题库接进来是最短的一条路。
  ★ 顺带：`--url` 是**参数**而不是环境变量 ⇒ 绕开 Windows 上
  `set` / `$env:` 对含 `&` `?` `@` 的连接串的转义坑。

不做什么
--------
- **不**建数据库本身（Neon 控制台已经建好 `neondb`）。
- **不**动任何凭据以外的配置。
- **不**改 `db/migrations/` 的既有行为。

CLI
---
    python tools/deploy/apply-schema.py --url "postgresql://…"            # 预演
    python tools/deploy/apply-schema.py --url "postgresql://…" --yes      # 真跑
    python tools/deploy/apply-schema.py --url "postgresql://…" --yes --force-migrations

    # 灌题库种子（13MB，约 1~3 分钟）。**表已经建好之后**再跑；库里已有题就跳过
    python tools/deploy/apply-schema.py --url "postgresql://…" --questions --yes

依赖：只有 `asyncpg`（`pip install asyncpg`）。
"""

from __future__ import annotations

import argparse
import asyncio

import sys
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "db" / "schema.sql"
MIGRATIONS_DIR = REPO / "db" / "migrations"
QUESTIONS = REPO / "data" / "seed" / "questions.sql"

#: 单条语句（整个 schema.sql）的执行上界。79KB 的建表脚本在托管 PG 上要几秒，
#: 但**无界等待必须加界**（硬约定 N）—— 网络卡住时观众只能看到"没反应"。
SCHEMA_TIMEOUT = 300
MIGRATION_TIMEOUT = 120
#: 13MB 的题库种子。给足，但仍然**有上界**（同上）。
QUESTIONS_TIMEOUT = 900


def say(msg: str) -> None:
    print(msg, flush=True)


def _connect(url: str, *, command_timeout: int):
    """**唯一**的一处建连 —— SSL 口径只写一遍。

    ⚠️ 三个调用点（schema / 探活 / 题库）必须一致：之前是各写一份，
      而"三份里改了两份"这种漂移不会报错，只会在某一条路上悄悄连不上。
    """
    import asyncpg

    return asyncpg.connect(
        url,
        timeout=15,
        command_timeout=command_timeout,
        ssl=True if "localhost" not in url and "127.0.0.1" not in url else None,
    )


def normalize_url(raw: str) -> str:
    """托管商连接串 → asyncpg 能吃的 DSN。

    ⚠️ 这里**故意只做最小处理**（补默认库名、摘 libpq 参数），
      与 `apps/api/app/core/config.py::normalize_database_url` 是**两处**实现 ——
      因为本脚本要能在**没装项目依赖**的环境里裸跑（只依赖 asyncpg）。
      ⇒ 判据：两处都要能被"Neon 串"喂通；不一致时以 config.py 为准（那是运行时的那条路）。
    """
    s = (raw or "").strip()
    if not s:
        return s
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if s.startswith(prefix):
            s = "postgresql://" + s[len(prefix) :]
            break
    base, sep, query = s.partition("?")
    if sep:
        kept = [
            p
            for p in query.split("&")
            if p.strip()
            and p.split("=", 1)[0].strip().lower()
            not in {"sslmode", "channel_binding", "target_session_attrs", "gssencmode"}
        ]
        base = base + ("?" + "&".join(kept) if kept else "")
    return base


def describe(url: str) -> str:
    """只打印**主机 + 库名**，绝不回显口令（日志/截图会带出去）。"""
    u = urlsplit(url)
    host = u.hostname or "(?)"
    db = (u.path or "/").lstrip("/") or "(默认)"
    port = f":{u.port}" if u.port else ""
    return f"{host}{port}/{db}"


async def apply(url: str, *, do_schema: bool, do_migrations: bool) -> int:
    conn = await _connect(url, command_timeout=SCHEMA_TIMEOUT)
    try:
        if do_schema:
            say(f"[apply] 执行 {SCHEMA.name} …")
            sql = SCHEMA.read_text(encoding="utf-8")
            await conn.execute(sql)
            n = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            )
            say(f"[apply] schema 完成：public 下 {n} 张表/视图")

        if do_migrations:
            files = sorted(MIGRATIONS_DIR.glob("*.sql"))
            if not files:
                say("[apply] db/migrations/ 里没有 .sql（跳过）")
            for f in files:
                say(f"[apply] 迁移 {f.name} …")
                await conn.execute(f.read_text(encoding="utf-8"), timeout=MIGRATION_TIMEOUT)

        users = await conn.fetchval("SELECT to_regclass('public.users')")
        say(f"[apply] 自检：public.users = {users}")
        return 0
    finally:
        await conn.close()


async def has_users(url: str) -> bool | None:
    """`True/False`；连不上返回 `None`（让调用方给一条**能看懂**的错）。"""
    try:
        conn = await _connect(url, command_timeout=60)
    except Exception as exc:  # noqa: BLE001
        say(f"[apply] ✗ 连不上数据库：{type(exc).__name__}: {exc}")
        say(
            "[apply]   常见原因：连接串抄漏了、Neon 项目在休眠（首连要几秒）、口令里有需转义的字符。"
        )
        return None
    try:
        return await conn.fetchval("SELECT to_regclass('public.users')") is not None
    finally:
        await conn.close()


async def load_questions(url: str, *, yes: bool) -> int:
    """灌题库种子（`data/seed/questions.sql`）。

    ### 三条安全闸（同族：硬约定 O"清场必须按批次"）
    1. 表必须已经在位（`public.questions`）——否则先回 §1.3 灌 schema，别在这瞎试；
    2. `subjects` 必须非空（种子里的题要挂到科目/章节上）；
    3. **库里已经有题 ⇒ 跳过**，连那 13MB 都不读。

    ⚠️ 第 3 条是"幂等"的**真实形态**，与 `docs/27` 早先写的不一样：
      种子文件里**没有任何 `DELETE FROM` / `TRUNCATE`**（实测 `grep -c` = 0），
      它自己的头注释写的是 `ON CONFLICT (content_hash) DO NOTHING`。
      所以"重复执行安全"的原因是**它只增不改**，不是"先清后灌"。
    """
    if not QUESTIONS.is_file():
        say(f"[apply] ✗ 找不到 {QUESTIONS}")
        say("[apply]   生成它：python db/seed/gen_seed_questions.py --format sql")
        return 2
    size_mb = QUESTIONS.stat().st_size / 1e6

    conn = await _connect(url, command_timeout=QUESTIONS_TIMEOUT)
    try:
        if await conn.fetchval("SELECT to_regclass('public.questions')") is None:
            say("[apply] ✗ 目标库里没有 public.questions ⇒ **先把 schema 灌好**（§1.3）。")
            return 1
        subjects = await conn.fetchval("SELECT count(*) FROM subjects")
        if not subjects:
            say("[apply] ✗ subjects 表是空的 ⇒ schema 没灌全，先回 §1.3。")
            return 1

        existing = await conn.fetchval("SELECT count(*) FROM questions")
        if existing:
            say(f"[apply] 题库已有 {existing} 题 ⇒ **跳过**（不读那 {size_mb:.1f} MB）。")
            say("[apply]   确实要重灌：先按批次清（`DELETE FROM questions`），再来一次。")
            return 0

        if not yes:
            say(f"[apply] —— 预演：将导入 {QUESTIONS.name}（{size_mb:.1f} MB）。加 --yes 才真跑。")
            return 0

        say(f"[apply] 导入 {QUESTIONS.name}（{size_mb:.1f} MB）… 首次约 1~3 分钟")
        await conn.execute(QUESTIONS.read_text(encoding="utf-8"))

        q = await conn.fetchval("SELECT count(*) FROM questions")
        o = await conn.fetchval("SELECT count(*) FROM question_options")
        kp = await conn.fetchval("SELECT count(*) FROM knowledge_points")
        say(f"[apply] 题库就绪：questions={q} question_options={o} knowledge_points={kp}")
        # ⚠️ 自检**必须断言"填成了正确值"**，不是"非空"（硬约定 B 的同族）：
        #    灌了一半也是"非空"，而前端会把它当完整的题库用。
        if (q, o) != (6000, 20156):
            say(
                f"[apply] ⚠️ 题数不是预期的 6000/20156（实际 {q}/{o}）—— 种子文件换过？还是灌了一半？"
            )
            return 1
        return 0
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apply-schema", description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True, help="目标库连接串（Neon 控制台复制的那条）")
    ap.add_argument("--yes", action="store_true", help="真的执行；不带就是**预演**")
    ap.add_argument(
        "--force-migrations",
        action="store_true",
        help="库里已有表时，仍然只补跑 db/migrations/（**明知可能不幂等**才用）",
    )
    ap.add_argument(
        "--questions",
        action="store_true",
        help="灌题库种子 data/seed/questions.sql（13MB）。**只做这件事**，不碰 schema",
    )
    args = ap.parse_args(argv)

    url = normalize_url(args.url)
    say(f"[apply] 目标库：{describe(url)}")

    # ---- 只灌题库：**不碰 schema** ⇒ 刻意**不**走下面那道"已有 users 就拒绝"的闸 ----
    #      那道闸是给"空库初始化"用的；而灌题库天生发生在 schema 已就位之后。
    if args.questions:
        say(f"[apply] 计划：题库种子 {QUESTIONS.name}（只有这一步）")
        return asyncio.run(load_questions(url, yes=args.yes))

    if not SCHEMA.is_file():
        say(f"[apply] ✗ 找不到 {SCHEMA}")
        return 2
    say(f"[apply] 计划：schema.sql + {len(sorted(MIGRATIONS_DIR.glob('*.sql')))} 个迁移")

    if not args.yes:
        say("[apply] —— 预演结束（加 --yes 才真的执行）。")
        return 0

    exists = asyncio.run(has_users(url))
    if exists is None:
        return 1
    if exists and not args.force_migrations:
        say("[apply] ✗ 目标库里**已经有 public.users** ⇒ 拒绝执行。")
        say("[apply]   本脚本只用于**空库**初始化；重复建库不该发生，")
        say("[apply]   而重复应用 db/migrations/ 里几条非幂等的迁移会改错数据。")
        say("[apply]   确认目标没错、且你只想补跑迁移 ⇒ 加 --force-migrations。")
        return 1

    do_schema = not exists
    say(f"[apply] schema：{'执行' if do_schema else '跳过（已有表）'}｜迁移：执行")
    return asyncio.run(apply(url, do_schema=do_schema, do_migrations=True))


if __name__ == "__main__":
    sys.exit(main())
