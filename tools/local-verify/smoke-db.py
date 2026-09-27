"""一次性临时库（BL-17）：让**本地**跑测试时的数据状态与 CI 可比。

为什么需要它
------------
CI 每次都是全新容器 + 全新库（`db/schema.sql` + `db/migrations/*.sql` 现建），
而本机开发库 `yijian` **跨会话累积**。2026-09-27 那次对账就被这个差异咬到：
本地覆盖率 `4672/214`、CI `4672/216` —— 差的 2 行是
`import_service.py:1427-1428`（`_stored_file_size` 的 `except OSError: return None`）：
本地库里存着 1541 条历史 `import_batches` 行，而它们的原始文件早被系统临时目录清掉，
列表视图一旦翻到这些"孤儿批次"就走 `except` ⇒ **本地多覆盖 2 行**。
⇒ **方向是反的**：不是 CI 漏测，是本地被历史数据抬高（`docs/21` §4.12、坑 70）。

所以本地要做与 CI 同样的事：**每次跑在一个一次性库里，跑完销毁**。
开发主库 `yijian` 继续累积 —— 那才是它的用途（开发调试）。

设计约束
--------
- **安全闸**：`assert_smoke_db()` 只接受 `yijian_smoke_<...>`。**传 `yijian` 必被拒绝**
  （硬约定 O：清场只清"自己造的那批"，绝不"全部清空"）。
- **有界**：`CREATE/DROP DATABASE` 是**集群级 DDL**，一律带 `timeout`（硬约定 N）。
- **幂等**：`provision` 先收掉同名残留；`drop` 对不存在的库返回成功。
- 只做「建库 + 建表 + 迁移」。**灌种子不在这里** —— 那是 `seed-questions.py` 的职责，
  `run-smoke.ps1` 分别调用；职责单一才好被单测（硬约定 H）。

CLI
---
    python tools/local-verify/smoke-db.py name                # 打印一个新的一次性库名
    python tools/local-verify/smoke-db.py provision --db X    # 建库 + schema + migrations
    python tools/local-verify/smoke-db.py drop --db X         # 断连接 + DROP（幂等）
    python tools/local-verify/smoke-db.py url --db X          # 打印 DATABASE_URL
    python tools/local-verify/smoke-db.py --self-test         # 纯函数自检（不需要 PG）

进度信息走 **stderr**，stdout 只出结果（`name` / `url` 要能被 `$(...)` 干净捕获）。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path

SMOKE_PREFIX = "yijian_smoke_"
# 名字必须是「前缀 + [a-z0-9_]+」，一次堵死引号注入（名字会拼进 SQL 字面量）。
SMOKE_NAME_RE = re.compile(rf"{SMOKE_PREFIX}[a-z0-9_]+")
DDL_TIMEOUT = 60  # CREATE/DROP DATABASE 的上界（集群级 DDL，硬约定 N）
SCRIPT_TIMEOUT = 300  # schema / migrations 的上界
REPO = Path(__file__).resolve().parents[2]


def say(msg: str) -> None:
    print(f"[smoke-db] {msg}", file=sys.stderr, flush=True)


def smoke_db_name() -> str:
    """一次性库名：带前缀（可 grep）+ pid（并发/残留可区分）。"""
    return f"{SMOKE_PREFIX}{os.getpid()}"


def db_url(port: int, user: str, db: str) -> str:
    return f"postgresql+asyncpg://{user}@127.0.0.1:{port}/{db}"


def assert_smoke_db(name: str, *, action: str) -> None:
    """★ 本文件的**唯一安全闸** —— 只允许操作一次性库。

    为什么必须是硬闸而不是"注释里提醒一句"：`drop` 是真删库。开发主库 `yijian`
    攒着开发数据，一旦被这里碰掉不可恢复。判据要落成**可执行**的，不是文档
    （硬约定 O / 「物理约束 > 文字规则」）。

    变异验证（打掉这个判断，自检必须变红）见 `--self-test`。
    """
    if not SMOKE_NAME_RE.fullmatch(name):
        sys.exit(
            f"[smoke-db] 拒绝 {action}：{name!r} 不是一次性库\n"
            f"           合法形状：{SMOKE_PREFIX}<letters/digits/_>\n"
            f"           开发主库 yijian 绝不能被本脚本碰 —— 硬约定 O。"
        )


def _env() -> dict[str, str]:
    e = dict(os.environ)
    e["PGCLIENTENCODING"] = "UTF8"
    return e


def _psql(
    exe: str, port: int, user: str, db: str, *args: str, timeout: int
) -> subprocess.CompletedProcess[str]:
    cmd = [exe, "-h", "127.0.0.1", "-p", str(port), "-U", user, "-d", db, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
        timeout=timeout,
    )


def _must(r: subprocess.CompletedProcess[str], what: str) -> str:
    if r.returncode != 0:
        sys.exit(f"[smoke-db] {what} 失败（rc={r.returncode}）\n{r.stdout}\n{r.stderr}")
    return r.stdout


def exists(exe: str, port: int, user: str, db: str) -> bool:
    r = _psql(
        exe,
        port,
        user,
        "postgres",
        "-tAc",
        f"SELECT 1 FROM pg_database WHERE datname='{db}'",
        timeout=DDL_TIMEOUT,
    )
    _must(r, f"查库 {db} 是否存在")
    return r.stdout.strip() == "1"


def ping(exe: str, port: int, user: str) -> bool:
    """端口上是否**真**是一个可用的 PostgreSQL。

    ⚠️ 判据是**真跑一次 `SELECT 1`**，不是"端口开着"。端口开着只说明**有个东西**在监听：
    本机已踩过两次同族事故 —— 旧 API 占着 8123 导致 13 个变异**全部假存活**；
    多起一个 PG 导致全量管道成片 50001（坑 68）。"能连上"和"是我要的那个"是两件事。
    """
    try:
        r = _psql(exe, port, user, "postgres", "-tAc", "SELECT 1", timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "1"


def drop_db(exe: str, port: int, user: str, db: str) -> None:
    """先断连接，再 DROP。幂等（库不在也算成功）。"""
    assert_smoke_db(db, action="drop")
    # DROP DATABASE 要求"没有别的连接"—— 测试跑完后连接池可能还没散干净。
    # 先 terminate（排除自己这条后端），再 DROP。
    _psql(
        exe,
        port,
        user,
        "postgres",
        "-q",
        "-c",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{db}' AND pid <> pg_backend_pid()",
        timeout=DDL_TIMEOUT,
    )
    r = _psql(
        exe,
        port,
        user,
        "postgres",
        "-q",
        "-c",
        f'DROP DATABASE IF EXISTS "{db}"',
        timeout=DDL_TIMEOUT,
    )
    _must(r, f"DROP DATABASE {db}")


def provision(exe: str, port: int, user: str, db: str, repo: Path = REPO) -> None:
    """建库 + 载入 schema.sql + 按文件名顺序跑 migrations。**全新基线**。"""
    assert_smoke_db(db, action="provision")

    leftovers = exists(exe, port, user, db)
    if leftovers:
        say(f"同名残留库 {db} 先收掉（上一次没清干净）")
        drop_db(exe, port, user, db)

    say(f"创建数据库 {db}")
    _must(
        _psql(
            exe, port, user, "postgres", "-q", "-c", f'CREATE DATABASE "{db}"', timeout=DDL_TIMEOUT
        ),
        f"CREATE DATABASE {db}",
    )

    schema = repo / "db" / "schema.sql"
    if not schema.is_file():
        sys.exit(f"[smoke-db] 找不到 {schema}")
    say("载入 db/schema.sql")
    _must(
        _psql(
            exe,
            port,
            user,
            db,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(schema),
            timeout=SCRIPT_TIMEOUT,
        ),
        "schema.sql",
    )

    migs = sorted((repo / "db" / "migrations").glob("*.sql"))
    if migs:
        say(f"应用 {len(migs)} 个迁移脚本")
        for m in migs:
            _must(
                _psql(
                    exe,
                    port,
                    user,
                    db,
                    "-q",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-f",
                    str(m),
                    timeout=SCRIPT_TIMEOUT,
                ),
                f"迁移 {m.name}",
            )


# ---------------------------------------------------------------- self-test
def _self_test() -> int:
    """纯函数自检 —— **不需要 PostgreSQL、不需要网络**（硬约定 H）。

    重点验的是**安全闸可证伪**：合法/非法名字各来一遍，且必须有一条"打掉闸门就变红"
    的断言（硬约定 J）。
    """
    fails: list[str] = []

    def ok(cond: bool, label: str) -> None:
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")
        if not cond:
            fails.append(label)

    def rejected(name: str) -> bool:
        try:
            assert_smoke_db(name, action="drop")
            return False
        except SystemExit:
            return True

    print("[self-test] 安全闸：拒绝非一次性库")
    ok(rejected("yijian"), "开发主库 'yijian' 被拒绝")
    ok(rejected("postgres"), "'postgres' 被拒绝")
    ok(rejected("yijian_smoke"), "少了尾下划线/后缀被拒绝")
    ok(rejected("yijian_smoke_"), "只有前缀被拒绝")
    ok(rejected("yijian_smoke_1'; DROP DATABASE yijian; --"), "引号注入被拒绝")
    ok(rejected("yijian_smoke_1; SELECT 1"), "分号注入被拒绝")
    ok(rejected("YIJIAN_SMOKE_1"), "大写前缀被拒绝（名字是大小写敏感的）")

    print("[self-test] 安全闸：放行一次性库")
    for good in ("yijian_smoke_1", "yijian_smoke_4242", f"{SMOKE_PREFIX}abc_123"):
        try:
            assert_smoke_db(good, action="drop")
            ok(True, f"{good!r} 放行")
        except SystemExit:
            ok(False, f"{good!r} 应放行却被拒")

    print("[self-test] 名字与 URL 形状")
    n = smoke_db_name()
    ok(n.startswith(SMOKE_PREFIX), f"smoke_db_name() 带前缀：{n}")
    ok(SMOKE_NAME_RE.fullmatch(n) is not None, "smoke_db_name() 输出自身合法")
    ok(
        db_url(55432, "yijian", n) == f"postgresql+asyncpg://yijian@127.0.0.1:55432/{n}",
        "db_url() 形状正确（driver/method 与 CI 一致）",
    )

    # ---- CLI 形状：参数位置写错会**静默**给默认值（2026-09-27 实测踩到）----
    print("[self-test] CLI 形状（参数位置）")
    ap = build_parser()
    ns = ap.parse_args(
        [
            "provision",
            "--psql",
            "/P",
            "--port",
            "1234",
            "--user",
            "u",
            "--db",
            "yijian_smoke_1",
            "--repo",
            "/R",
        ]
    )
    ok(ns.psql == "/P", "子命令后的 --psql 被正确解析（不是默认值 'psql'）")
    ok(ns.port == 1234, "子命令后的 --port 被正确解析（默认值恰好也接近 ⇒ 不专门断查看不出来）")
    ok(ns.user == "u", "子命令后的 --user 被正确解析")
    ok(ns.repo == "/R", "子命令后的 --repo 被正确解析")
    ok(ns.db == "yijian_smoke_1", "子命令后的 --db 被正确解析")
    ok(ap.parse_args(["name"]).cmd == "name", "name 子命令可独立解析（不需要任何公共参数）")

    # 反向断言：公共参数**不能**写在子命令之前。这里若**不报错**，说明它们被挂到了主解析器上
    # —— 那就会引入"子解析器默认值静默覆盖主解析器"的隐患（实测过，见 `_add_common`）。
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            ap.parse_args(["--psql", "/P", "provision", "--db", "yijian_smoke_1"])
        ok(False, "公共参数写在子命令之前本应被拒绝 ⇒ 说明它们被挂到了主解析器上")
    except SystemExit:
        ok(True, "公共参数写在子命令之前会被拒绝（⇒ 确认只挂在子命令上）")

    print(f"[self-test] {'全部通过' if not fails else f'失败 {len(fails)} 项'}")
    return 1 if fails else 0


# ---------------------------------------------------------------- CLI
def _add_common(p: argparse.ArgumentParser) -> None:
    """公共参数 —— **只挂在子命令上，不挂主解析器**。

    ⚠️ 为什么不能两边都挂（2026-09-27 实测）：argparse 里"主解析器与子解析器定义同名参数"时，
    **子解析器会用它的默认值覆盖主解析器已解析到的值**，而且**不报错**：

        prog --repo /GLOBAL provision --db X   ->  repo='/DEFAULT'   ← 静默丢失！
        prog provision --repo /SUB --db X      ->  repo='/SUB'       ✓

    本脚本 `--port` 的默认值恰好也是 55432 ⇒ 这个 bug **完全看不出来**（最危险的那种）。
    另一条相关约束：**主解析器的可选参数不能写在子命令之后** —— 写了会报
    `unrecognized arguments`（这就是 2026-09-27 第一次跑管道时报的错）。
    ⇒ 两条约束一起满足的唯一简单写法：公共参数**只挂子命令**，调用方一律写成
    `子命令 --psql … --port … --user …`。
    """
    p.add_argument("--psql", default=os.environ.get("YIJIAN_PSQL", "psql"))
    p.add_argument("--port", type=int, default=55432)
    p.add_argument("--user", default="yijian")


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器。

    **抽成独立函数**是为了让 `--self-test` 能断言"参数写在不同位置时拿到的值对不对" ——
    argparse 的两种错法（见 `_add_common`）一个**报错**、一个**静默给默认值**，
    而 `--port` 的默认值恰好等于实际要用的值 ⇒ **不专门断言就永远发现不了**。
    """
    ap = argparse.ArgumentParser(
        prog="smoke-db.py",
        description="一次性临时库的建/销毁（BL-17）—— 让本地与 CI 的数据状态可比",
        epilog="例：smoke-db.py provision --psql <psql.exe> --port 55432 --user yijian --db yijian_smoke_1",
    )
    ap.add_argument("--self-test", action="store_true", help="跑纯函数自检后退出")
    sub = ap.add_subparsers(dest="cmd")

    p_name = sub.add_parser("name", help="打印一个新的一次性库名")
    p_ping = sub.add_parser("ping", help="端口上是否真是可用 PG（真跑一次 SELECT 1）；rc=0/1")
    p_prov = sub.add_parser("provision", help="建库 + schema + migrations")
    p_drop = sub.add_parser("drop", help="断连接 + DROP（幂等）")
    p_url = sub.add_parser("url", help="打印 DATABASE_URL")
    for p in (p_name, p_ping, p_prov, p_drop, p_url):
        _add_common(p)
    # `--repo` 只属于 provision（其余子命令不看仓库路径）。
    p_prov.add_argument("--repo", default=str(REPO))
    for p in (p_prov, p_drop, p_url):
        p.add_argument("--db", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.cmd:
        ap.print_help()
        return 2

    if args.cmd == "name":
        print(smoke_db_name())  # stdout 只出结果
        return 0
    if args.cmd == "ping":
        return 0 if ping(args.psql, args.port, args.user) else 1
    if args.cmd == "url":
        print(db_url(args.port, args.user, args.db))
        return 0
    if args.cmd == "provision":
        provision(args.psql, args.port, args.user, args.db, Path(args.repo))
        say(f"就绪：{args.db}")
        return 0
    if args.cmd == "drop":
        drop_db(args.psql, args.port, args.user, args.db)
        say(f"已销毁：{args.db}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
