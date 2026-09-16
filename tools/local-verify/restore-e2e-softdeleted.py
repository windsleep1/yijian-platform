# -*- coding: utf-8 -*-
"""查看 Batch 4 E2E 造成的软删除，并（可选 --restore）恢复为未删除。

E2E 只能通过后台批量删除来验证软删逻辑，跑完会留下若干 is_deleted=true 的题。
本脚本把它们列出来；加 --restore 时统一恢复，保持本地数据集干净。

用法：
    python tools/local-verify/restore-e2e-softdeleted.py            # 只查看
    python tools/local-verify/restore-e2e-softdeleted.py --restore  # 恢复
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

PORT = os.environ.get("YJ_PG_PORT", "55432")
DSN = f"postgresql://yijian@127.0.0.1:{PORT}/yijian"


async def main(restore: bool) -> int:
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT id, version, stem, updated_at
              FROM questions
             WHERE is_deleted = true
             ORDER BY updated_at DESC
            """
        )
        print(f"当前软删除题目：{len(rows)} 条")
        for r in rows:
            print(f"  {r['id']}  v{r['version']}  {r['stem'][:44]}")

        if not rows:
            return 0

        if not restore:
            print()
            print("（未加 --restore，仅查看。加 --restore 可全部恢复为未删除。）")
            return 0

        ids = [r["id"] for r in rows]
        await conn.execute(
            """
            UPDATE questions
               SET is_deleted = false, updated_at = now()
             WHERE id = ANY($1::bigint[])
            """,
            ids,
        )
        left = await conn.fetchval(
            "SELECT count(*) FROM questions WHERE is_deleted = true"
        )
        print()
        print(f"已恢复 {len(ids)} 条；剩余软删除 {left} 条")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--restore" in sys.argv)))
