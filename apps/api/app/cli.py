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
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.idgen import next_id, to_inet
from app.core.security import hash_password
from app.db.base import asyncpg_connect_args, raw_asyncpg_connection

# ---------------------------------------------------------------- 工具


def _log(msg: str) -> None:
    print(f"[cli] {msg}", flush=True)


def _db_target() -> str:
    """给日志用的**连接目标**描述（host / port / db / ssl）—— **绝不含凭据**。

    ★★ 为什么要打这一行（2026-10-01 Render 第二次真故障）：

      那次日志里，前两行说 `PostgreSQL ready`，几秒后引擎却报
      `socket.gaierror: Name or service not known`。而**日志里没有任何一处**
      能看出这两次连接的目标是不是同一个 —— 排查只能靠猜，于是我先猜错了两次
      （先猜密码里的 `@`，再猜 asyncpg 版本差异）。

      真因是：**同一个连接串被两个解析器读**（`urllib` 一族 vs SQLAlchemy 的正则），
      折行粘贴留下的 `\\n` 只污染了后者解出的主机名。

      ⇒ 判据：**凡是"同一个配置被两条路读"的地方，日志里就要能把目标打出来。**
        这样"两边是不是同一个目标"从"推理"变成"看见"——
        而这件事本来只需要一行日志。
    """
    u = urlsplit(settings.dsn)
    return (
        f"host={u.hostname or '?'} port={u.port or 5432} "
        f"db={(u.path or '/').lstrip('/') or '?'} ssl={settings.db_ssl_require}"
    )


def _ancestor(level: int) -> Path | None:
    """`__file__` 的第 `level` 级父目录；**不够深就返回 None**（不抛异常）。

    ★★ 为什么必须这样写（2026-10-01 在 Render 上真炸过）：

      容器里 `__file__` 是 **`/app/app/cli.py`** ⇒ `parents` 只有 **3** 项
      （`/app/app`、`/app`、`/`）⇒ `parents[3]` 抛 **`IndexError`**。
      而本机是 `…/yijian-platform/apps/api/app/cli.py`（**8 层**）⇒ **本机永远复现不出来**。

    ★ 更隐蔽的一点：**候选列表是"饿着构造"的** —— 越界发生在"逐个 `is_file()` 试探"
      **之前**，所以连"退回到别的候选"这条兜底路都走不到，直接崩。
    ⇒ 判据：**凡是按深度索引的路径推断，都要能容忍"实际路径比预期浅"**；
      并且**别在"列候选"阶段做可能失败的事**（列候选应当是纯的）。

    ★ 实现上用 **`try / except IndexError`**，而不是先 `len(parents)` 再判断：
      `len()` 当然也能用，但那样等于把"**必须支持 `len`**"变成调用方的新前置条件 ——
      而 `tests/test_cli_commands.py` 里有一个 `_MissingFile` **替身**
      （只实现 `resolve / is_file / __truediv__ / __getitem__ / parents / name`），
      正是靠"**下标越界抛 `IndexError`**"这一条**真实行为**工作的。
      ⇒ 优先依赖**文档化的行为**（越界抛 `IndexError`），而不是"顺手可用的方法"：
        这样"替身必须实现的最小表面"不会被动扩大。
      （第一版写的就是 `len()`，**是全量跑把它抓出来的** —— 392 passed / 4 failed。）
    """
    try:
        return Path(__file__).resolve().parents[level]
    except IndexError:
        return None


def _repo_relative(depth: int, *parts: str) -> Path | None:
    """`__file__` 往上 `depth` 级的目录 + 相对片段；不够深就 `None`。

    ★★ 这里**刻意用 `/` 运算符**（而不是 `Path.joinpath`），并且**不用** `len()` 探深度 ——
      因为 `tests/test_cli_commands.py` 有一个 `_MissingFile` **替身**，
      它实现的是**接口表面**：`/`（`__truediv__`）、`resolve`、`is_file`、`parents[i]`、`cwd`、`name`。

      ⚠️ 我第一次重写时用了 `len(parents)` 和 `joinpath()` —— 两次都被**全量跑**抓出来
      （392 passed / 4 failed）。它们**语义上完全等价**，却**扩大了替身必须实现的最小表面**。

      ⇒ 一般化的判据：**当一段代码被测试替身包着时，"语义等价的重写"仍可能破坏它** ——
        因为替身实现的是**表面**，不是**语义**。
        （同族：「休眠的契约」——改动前先问"谁在依赖这段代码的**形状**"，不只是"谁在调用它"。）
    """
    base = _ancestor(depth)
    if base is None:
        return None
    p = base
    for part in parts:
        p = p / part
    return p


def _resolve_schema_file() -> Path | None:
    candidates: list[Path] = [Path(settings.schema_file)]
    # 顺序与原实现一致（先仓库根、再 apps/api），只是**越界的那一级会被跳过**。
    for depth in (3, 2):
        p = _repo_relative(depth, "db", "schema.sql")
        if p is not None:
            candidates.append(p)
    candidates += [Path.cwd() / "db" / "schema.sql", Path("/db/schema.sql")]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _resolve_seed_file() -> Path | None:
    candidates: list[Path] = [Path("/data/seed/questions.sql")]
    p = _repo_relative(3, "data", "seed", "questions.sql")
    if p is not None:
        candidates.append(p)
    candidates.append(Path.cwd() / "data" / "seed" / "questions.sql")
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
            conn = await asyncpg.connect(settings.dsn, timeout=5, **asyncpg_connect_args())
            await conn.close()
            _log(f"PostgreSQL ready ({_db_target()})")
            return 0
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            await asyncio.sleep(1.5)
    _log(f"timed out waiting for PostgreSQL after {timeout}s: {last_err}")
    return 1


async def _wait_redis(timeout: int) -> int:
    from redis.asyncio import from_url

    # ⚠️ **必须给单次连接/单次命令加上界**（硬约定 N：无界等待必须加界）。
    #    下面那个 `deadline` 只守得住**重试循环**，守不住**单次 `ping()`**：
    #    没有 `socket_timeout` 时，一次 ping 可以**永远**等不到回包 ——
    #    于是 `while time.monotonic() < deadline` 再也回不到判断，`--timeout` 形同虚设。
    #
    #    这就是 CI **连红 12 次**的真因（pytest 12m12s / 121s / 135s / 136s 全是被整步超时打断，
    #    而单条用例的堆栈指到 `test_wait_redis_returns_0_when_it_answers` → `await client.ping()`）。
    #    本机复现：同一段 `from_url(...).ping()` 连跑 40 次，**6 次永久卡住**（15%）。
    #
    #    对照：隔壁 `_wait_db` 是 `asyncpg.connect(..., timeout=5)` —— **有界**；
    #    这里当初漏了，而某个测试**在自己的替身里绕过了它**（mock 掉 `from_url`），
    #    于是"症状在测试里消失了，缺陷还在产品代码里"（这正是坑 66 的同族）。
    client = from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,  # 单次 TCP 连接的上界
        socket_timeout=2,  # 单次命令读写（含 ping 等回包）的上界 ← 就是它缺了才让 deadline 失效
    )
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
    """确保库里有表。**镜像里没有 schema 时，也要能分清"已就绪"与"这个部署是坏的"。**

    ★★ 为什么这条分支必须存在（2026-10-01 在 Render 上的真实故障）：
      Docker 构建上下文是 `apps/api`，而 `db/schema.sql` 在**仓库根** ⇒ **镜像里没有它**
      （本地 compose 靠卷挂载 `../db:/db:ro` 才有）。于是托管部署时
      `_resolve_schema_file()` 必然返回 `None` —— **这是常态，不是错误**。

      但"常态"与"这个库根本没初始化"在日志里长得**一模一样**：
      老的写法是直接 `return 0` 跳过 ⇒ 一个**没有任何表**的 API 会正常启动。
      它 `/api/v1/health` 还能返 200（`degraded`），而每个真实请求都 500 ——
      **最难查的一种"活着但没用"**。⇒ 判据（兜底要出声）：
      **别让"没找到前置条件"和"前置条件已满足"走同一条路**。
    """
    schema_file = _resolve_schema_file()

    conn = await raw_asyncpg_connection()
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.users')")

        if schema_file is None:
            if exists is not None:
                _log(f"镜像内无 schema.sql，但目标库已就绪（{exists}）⇒ 跳过 init-db（**预期**）")
                return 0
            _log("✗ 目标库里没有 public.users，而镜像里也没有 db/schema.sql ⇒ 这个库还没初始化。")
            _log("  修法（在**本机**跑一次，见 docs/27 §1.3）：")
            _log('      python tools/deploy/apply-schema.py --url "<Neon 连接串>" --yes')
            _log("  然后再重新部署。")
            return 1

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
        # 镜像里没有 schema.sql（Docker 构建上下文是 apps/api；托管部署的常态）。
        # 角色/权限**是随 schema.sql 一起灌进去的**（`tools/deploy/apply-schema.py` 跑的就是它）
        # ⇒ 这里跳过是对的。
        # ⚠️ 但"跳过"必须**说清为什么**，否则下一个人会把它读成"角色没灌进去"。
        _log("镜像内无 schema.sql ⇒ 跳过 seed-rbac（角色/权限已随 apply-schema 灌入）")
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

    conn = await raw_asyncpg_connection()
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

    # ⚠️ 这里**刻意不加重试**（别顺手加）：
    #    ① "依赖还没起来"这一种情况已经由**前面的 `wait-db`** 吸收掉了 ——
    #       能走到这里就说明几秒前它是通的；
    #    ② 加重试只会让**确定性**的失败**晚**出现，而不会让它更好查
    #       （本次真故障就是确定性的：重试三次还是同一行）。
    #    ⇒ 该做的是**把目标打进日志**，不是把失败往后拖。
    try:
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
    except Exception as exc:  # noqa: BLE001
        # 运维命令**保留全栈**（对着 "xxx failed" 猜是最贵的一种排障），
        # 但在全栈**之前**先给一行可读的：**目标是什么** + 一句话原因。
        # 全栈里看不出它连的是哪台机器 —— 而那正是这次事故卡住的地方。
        _log(f"seed-admin failed :: {_db_target()} :: {type(exc).__name__}: {exc}")
        raise
    return 0


# ---------------------------------------------------------------- 题库


async def _seed_questions() -> int:
    seed_file = _resolve_seed_file()
    if seed_file is None:
        _log(
            "data/seed/questions.sql not found; skipping (run db/seed/gen_seed_questions.py first)"
        )
        return 0

    conn = await raw_asyncpg_connection()
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
    # argparse 的 `choices` 已枚举全部取值，非法命令在 parse_args 阶段就以 2 退出 ——
    # 这一行**不可达**，留着是"以后加子命令忘了接分派"时的兜底（那时它会变成 2 而不是 None）。
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
