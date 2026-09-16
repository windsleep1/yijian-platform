"""直接验证 `_roles_map` 那条 SQL 的数组绑定。

    python probe_roles_map.py [--dsn postgresql+asyncpg://yijian@127.0.0.1:55432/yijian]

被测 SQL（与 app/services/user_service.py::_roles_map 完全一致）：

    WHERE ur.user_id = ANY(CAST(:uids AS bigint[]))

这是 Batch 2 里唯一一个「只在真 PostgreSQL + 真 asyncpg 下才可能暴露」的隐患点：
SQLite 没有数组类型，mock session 又不会真的走驱动编解码，两边都测不到它。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

SQL = text(
    "SELECT ur.user_id, r.code "
    "FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
    "WHERE ur.user_id = ANY(CAST(:uids AS bigint[]))"
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dsn", default="postgresql+asyncpg://yijian@127.0.0.1:55432/yijian"
    )
    args = ap.parse_args()

    eng = create_async_engine(args.dsn)
    ok = True
    try:
        async with eng.connect() as conn:
            rows = (
                await conn.execute(text("SELECT id FROM users ORDER BY id LIMIT 3"))
            ).fetchall()
            all_ids = [r[0] for r in rows]
            print(f"[probe] users 表取到 {len(all_ids)} 个 id: {all_ids}")

            cases = [
                ("3 个 id", all_ids),
                ("1 个 id", all_ids[:1]),
                ("0 个 id（空列表）", []),
            ]
            for label, uids in cases:
                try:
                    res = (await conn.execute(SQL, {"uids": uids})).fetchall()
                    print(f"[probe] {label:<18} -> OK   {len(res)} 行  {res}")
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    print(f"[probe] {label:<18} -> FAIL {type(exc).__name__}: {exc}")
    finally:
        await eng.dispose()

    print("[probe] RESULT =", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
