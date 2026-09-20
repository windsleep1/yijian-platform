"""
命令行工具。容器启动时由 docker-entrypoint.sh 调用。

    python -m app.cli wait-db       等待 PostgreSQL 可连接
    python -m app.cli wait-redis    等待 Redis 可连接
    python -m app.cli init-db       执行 db/schema.sql（幂等：已建表则跳过）
    python -m app.cli seed-rbac     重放 db/schema.sql 的角色/权限种子（幂等，用于补新角色）
    python -m app.cli seed-admin    创建/修复超级管理员（幂等）
    python -m app.cli seed-questions 导入种子题库（幂等，依赖 db/seed/questions.sql）
"""

# ---------------------------------------------------------------- 关于输出语言
#
# ⚠️ **本文件所有控制台输出一律用英文（纯 ASCII），这是刻意的，别翻译回中文。**
#
# 原因（硬约定 I）：这是运维命令，输出会穿过
#     Python stdout → PowerShell → 工具调用宿主 / CI runner
# 多层边界。链路上各方对编码的假设**不一致**（本机系统 ACP 是 UTF-8、
# PS 5.1 按 936 读写、再外层的宿主按 936 解码），而**最外层改不了** ——
# 结果日志里中文全变成 `[cli] RBAC 绉嶅瓙宸查噸鏀`，让"核对验收日志"这一步失效。
#
# 判据：**最外层那一端的编码你改不了，就让它成为唯一假设，其余各方向它对齐。**
# 中文注释 / docstring 不受影响（按 UTF-8 读源码，与 console 编码无关），
# 所以**只有 `_log()` 与 argparse 文案需要英文**；写进库的数据（如超管昵称）也不需要改。

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from app.core.config import settings
from app.core.idgen import next_id, to_inet
from app.core.security import hash_password

# ---------------------------------------------------------------- 工具


def _log(msg: str) -> None:
    print(f"[cli] {msg}", flush=True)


async def _raw_conn():
    import asyncpg

    return await asyncpg.connect(settings.dsn)


def _resolve_schema_file() -> Path | None:
    candidates = [
        Path(settings.schema_file),
        Path(__file__).resolve().parents[3] / "db" / "schema.sql",  # apps/api/app -> repo/db
        Path(__file__).resolve().parents[2] / "db" / "schema.sql",
        Path.cwd() / "db" / "schema.sql",
        Path("/db/schema.sql"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _resolve_seed_file() -> Path | None:
    candidates = [
        Path("/data/seed/questions.sql"),
        Path(__file__).resolve().parents[3] / "data" / "seed" / "questions.sql",
        Path.cwd() / "data" / "seed" / "questions.sql",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


# ---------------------------------------------------------------- 等待依赖


async def _wait_db(timeout: int) -> int:
    import asyncpg

    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = await asyncpg.connect(settings.dsn, timeout=5)
            await conn.close()
            _log("PostgreSQL ready")
            return 0
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            await asyncio.sleep(1.5)
    _log(f"timed out waiting for PostgreSQL after {timeout}s: {last_err}")
    return 1


async def _wait_redis(timeout: int) -> int:
    from redis.asyncio import from_url

    client = from_url(settings.redis_url, decode_responses=True)
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    try:
        while time.monotonic() < deadline:
            try:
                await client.ping()
                _log("Redis ready")
                return 0
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                await asyncio.sleep(1.5)
        _log(f"timed out waiting for Redis after {timeout}s: {last_err}")
        return 1
    finally:
        await client.aclose()


# ---------------------------------------------------------------- 建表


async def _init_db() -> int:
    schema_file = _resolve_schema_file()
    if schema_file is None:
        _log("db/schema.sql not found; skipping init-db")
        return 0

    conn = await _raw_conn()
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.users')")
        if exists is not None:
            _log(f"schema already present ({exists}); skipping {schema_file.name}")
            return 0

        _log(f"applying {schema_file} ...")
        sql = schema_file.read_text(encoding="utf-8")
        await conn.execute(sql)

        tables = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        )
        _log(f"schema applied; {tables} tables/views in public schema")
        return 0
    except Exception as exc:  # noqa: BLE001
        _log(f"init-db failed: {exc}")
        return 1
    finally:
        await conn.close()


# ---------------------------------------------------------------- RBAC 种子

# 切片标记：必须是**独一无二**的字面量。
# 踩过的坑：最初用 "-- 12.1 角色" / "-- 12.3 科目" 当标记，结果解释性注释里
# 又引用了这两个短语，`find()` 命中最前面的那处注释 → 切出 101 个字符的纯注释，
# 交给 asyncpg 执行后报 `'NoneType' object has no attribute 'decode'`（无语句可执行），
# 报错信息完全指不到真正的原因。所以改用专用哨兵，并在 schema.sql 里注明不要复用。
_RBAC_START = "RBAC-SEED:START"
_RBAC_END = "RBAC-SEED:END"


async def _seed_rbac() -> int:
    """重放角色/权限种子（schema.sql 中 RBAC-SEED 标记之间的一段）。

    为什么需要它：`init-db` 只在**库不存在**时执行 schema.sql（`to_regclass('users')`
    为 NULL 才跑）。于是**已建好的库永远拿不到后加的角色**——这次加 `viewer` 就撞上了：
    老库里 schema.sql 不会重跑，viewer 角色不存在，前端「按钮禁用态」的演示直接落空。

    种子里每条都是 `ON CONFLICT DO NOTHING`，重复执行安全；唯一副作用是**新增**
    角色/权限行，不会覆盖已有数据。

    实现上刻意**从 schema.sql 切片**而不是复制一份 SQL：两处维护必然漂移，
    而漂移的表现是"线上有人悄悄少了权限"，这种 bug 查起来很痛。
    """
    schema_file = _resolve_schema_file()
    if schema_file is None:
        _log("db/schema.sql not found; skipping seed-rbac")
        return 0

    sql = schema_file.read_text(encoding="utf-8")
    start = sql.find(_RBAC_START)
    end = sql.find(_RBAC_END)
    if start < 0 or end < 0 or end <= start:
        _log(f"slice markers ({_RBAC_START} / {_RBAC_END}) not found; skipping seed-rbac")
        return 1
    # 从标记**行尾**开始切，避免把标记行本身带进待执行的 SQL（它已经是个完整注释，
    # 但注释一多容易出意外；明确跳过更稳）。
    block = sql[sql.index("\n", start) + 1 : end].strip()

    if ";" not in block:
        _log(
            f"slice has no executable statement (len={len(block)}); markers look edited -- refusing"
        )
        return 1

    conn = await _raw_conn()
    try:
        before = await conn.fetchval("SELECT count(*) FROM roles")
        before_perm = await conn.fetchval("SELECT count(*) FROM role_permissions")
        await conn.execute(block)
        after = await conn.fetchval("SELECT count(*) FROM roles")
        after_perm = await conn.fetchval("SELECT count(*) FROM role_permissions")
        _log(
            f"RBAC seed replayed: roles {before} -> {after}, "
            f"role_permissions {before_perm} -> {after_perm}"
        )
        rows = await conn.fetch(
            "SELECT r.code, count(rp.permission_id) AS n "
            "FROM roles r LEFT JOIN role_permissions rp ON rp.role_id = r.id "
            "GROUP BY r.code ORDER BY min(r.sort_no)"
        )
        for row in rows:
            _log(f"  - {row['code']}: {row['n']} permissions")
        return 0
    except Exception as exc:  # noqa: BLE001
        # 这是运维命令，失败时必须给全栈信息，否则只能对着 "xxx failed" 猜。
        import traceback

        _log(f"RBAC seed replay failed: {exc}")
        _log(traceback.format_exc())
        return 1
    finally:
        await conn.close()


# ---------------------------------------------------------------- 超管


async def _seed_admin() -> int:
    phone = (settings.admin_init_phone or "").strip()
    password = settings.admin_init_password or ""
    if not phone:
        _log("ADMIN_INIT_PHONE not set; skipping seed-admin")
        return 0
    if len(password) < 8:
        _log("ADMIN_INIT_PASSWORD must be >= 8 chars; skipping (fix it in .env)")
        return 1

    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import User, UserProfile
    from app.services import rbac_service

    async with SessionLocal() as db:
        role = await rbac_service.get_role_by_code(db, "super_admin")
        if role is None:
            _log("no super_admin role in table; run init-db first")
            return 1

        user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()

        if user is None:
            user = User(
                id=next_id(),
                phone=phone,
                nickname="超级管理员",
                password_hash=hash_password(password),
                status="active",
                register_source="cli",
                register_ip=to_inet("127.0.0.1"),
                is_deleted=False,
            )
            db.add(user)
            await db.flush()
            db.add(UserProfile(user_id=user.id, exam_level="yijian"))
            await db.flush()
            _log(f"created super admin {phone}")
        else:
            user.password_hash = hash_password(password)
            user.status = "active"
            user.is_deleted = False
            _log(f"super admin {phone} exists; password reset and account activated")

        await rbac_service.ensure_role_assigned(db, user_id=user.id, role_code="super_admin")
        await db.commit()
        _log("super_admin role is ready")
    return 0


# ---------------------------------------------------------------- 题库


async def _seed_questions() -> int:
    seed_file = _resolve_seed_file()
    if seed_file is None:
        _log(
            "data/seed/questions.sql not found; skipping (run db/seed/gen_seed_questions.py first)"
        )
        return 0

    conn = await _raw_conn()
    try:
        has_subjects = await conn.fetchval("SELECT count(*) FROM subjects")
        if not has_subjects:
            _log("subjects table is empty; run init-db first")
            return 1

        existing = await conn.fetchval("SELECT count(*) FROM questions")
        if existing:
            _log(
                f"question bank already has {existing} rows; skipping import "
                "(truncate questions to re-import)"
            )
            return 0

        _log(f"importing {seed_file.name} ...")
        await conn.execute(seed_file.read_text(encoding="utf-8"))
        total = await conn.fetchval("SELECT count(*) FROM questions")
        _log(f"question bank imported; {total} questions total")
        return 0
    except Exception as exc:  # noqa: BLE001
        _log(f"question import failed: {exc}")
        return 1
    finally:
        await conn.close()


# ---------------------------------------------------------------- 入口


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Yijian backend operations CLI")
    parser.add_argument(
        "command",
        choices=["wait-db", "wait-redis", "init-db", "seed-rbac", "seed-admin", "seed-questions"],
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="timeout in seconds for wait-* commands"
    )
    args = parser.parse_args(argv)

    if args.command == "wait-db":
        return asyncio.run(_wait_db(args.timeout))
    if args.command == "wait-redis":
        return asyncio.run(_wait_redis(args.timeout))
    if args.command == "init-db":
        return asyncio.run(_init_db())
    if args.command == "seed-rbac":
        return asyncio.run(_seed_rbac())
    if args.command == "seed-admin":
        return asyncio.run(_seed_admin())
    if args.command == "seed-questions":
        return asyncio.run(_seed_questions())
    return 2


if __name__ == "__main__":
    sys.exit(main())
