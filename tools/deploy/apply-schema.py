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

不做什么
--------
- **不**建数据库本身（Neon 控制台已经建好 `neondb`）。
- **不**灌题库种子（那是 `app.cli seed-questions`；13MB，按需再跑，见 `docs/27` §1.5）。
- **不**动任何凭据以外的配置。

CLI
---
    python tools/deploy/apply-schema.py --url "postgresql://…"            # 预演
    python tools/deploy/apply-schema.py --url "postgresql://…" --yes      # 真跑
    python tools/deploy/apply-schema.py --url "postgresql://…" --yes --force-migrations

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

#: 单条语句（整个 schema.sql）的执行上界。79KB 的建表脚本在托管 PG 上要几秒，
#: 但**无界等待必须加界**（硬约定 N）—— 网络卡住时观众只能看到"没反应"。
SCHEMA_TIMEOUT = 300
MIGRATION_TIMEOUT = 120


def say(msg: str) -> None:
    print(msg, flush=True)


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
    import asyncpg

    conn = await asyncpg.connect(
        url,
        timeout=15,
        command_timeout=SCHEMA_TIMEOUT,
        ssl=True if "localhost" not in url and "127.0.0.1" not in url else None,
    )
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
    import asyncpg

    try:
        conn = await asyncpg.connect(
            url,
            timeout=15,
            ssl=True if "localhost" not in url and "127.0.0.1" not in url else None,
        )
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apply-schema", description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True, help="目标库连接串（Neon 控制台复制的那条）")
    ap.add_argument("--yes", action="store_true", help="真的执行；不带就是**预演**")
    ap.add_argument(
        "--force-migrations",
        action="store_true",
        help="库里已有表时，仍然只补跑 db/migrations/（**明知可能不幂等**才用）",
    )
    args = ap.parse_args(argv)

    if not SCHEMA.is_file():
        say(f"[apply] ✗ 找不到 {SCHEMA}")
        return 2
    url = normalize_url(args.url)
    say(f"[apply] 目标库：{describe(url)}")
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
