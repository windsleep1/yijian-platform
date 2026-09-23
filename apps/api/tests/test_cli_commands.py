"""⑤b：`app/cli.py` 六个子命令 —— 每个测「退出码 + 关键副作用」。

## ★ 为什么**在进程内**调 `cli.main([...])`，而不是 `subprocess` 起 `python -m app.cli`

不是风格选择，是**测量边界**（硬约定 L）：`run-smoke.ps1` 与 CI 都用
`coverage run --data-file .coverage.tests -m pytest tests` 包住 pytest，
而**子进程不在这个插桩范围内**（没配 `COVERAGE_PROCESS_START` / `multiprocessing`）。

所以用 `subprocess` 写这套用例的后果是：**用例全绿，cli.py 的覆盖率一条都不涨** ——
一个"看起来补了、其实没补"的假象。（反过来，`run-smoke.ps1` 里那两次
`coverage run ... -m app.cli seed-rbac/seed-admin` 就是**在 CLI 自己的进程里**插桩的，
这才是 `.coverage.cli` 存在的理由。）

进程内调用的代价是要自己管好环境（下节的替身），收益是**测量和验证同源**。

## 覆盖面：六个子命令 + 三类"运维必须正确"的分支

    wait-db / wait-redis   依赖不可达时必须**超时后失败**，不能永远挂着
    init-db                幂等（已建表则跳过）；缺 schema.sql 时**跳过而不是崩**
    seed-rbac              切片标记被改坏时必须**拒绝执行**，不能静默跑空 SQL
    seed-admin             缺手机号跳过 / 密码太短拒绝 / 超管不存在时**能建出来**
    seed-questions         幂等（题库非空则跳过）；导入失败必须返回 1
    入口                   非法子命令 → 退出码 2（argparse 的契约）

## 三处替身，各自的理由（不是为了"好写"，是真实环境到不了）

    `_PongServer`              最小 Redis 协议桩。本机与 CI **都没有真 Redis**
                               （`serve_fake_redis.py` 把 fakeredis 起在 **API 进程内部**），
                               所以 `_wait_redis` 的"就绪 → 0"这条分支在真实环境里到不了。
                               没有它，"不可达 → 1"就没有**正向对照** —— 那条红可能
                               只是"函数坏了"（⑤a 的教训）。
    `_MissingFile`             替代 `pathlib.Path`：所有路径都"不存在"。
                               用来走 `_resolve_*_file` 的**真实循环**（而不是把整个函数
                               换掉），模拟"镜像里没挂 db/schema.sql / data/seed"的容器环境。
    `_StubConn`                `raw_asyncpg_connection()` 的替身，回放 `fetchval` 结果。
                               只用在"空 subjects / 空 questions / 导入报错"这三条分支 ——
                               它们要**先破坏数据**才能构造，成本远高于价值。
                               真实路径（题库非空 → 跳过）走的是**真 PG**。

⚠️ `_seed_admin` 的"超管不存在 → 建号"那条会**真的建一个用户**，
所以用例用随机手机号 + `finally` 里按 FK 顺序删干净 —— 不做的话，
排在后面的 `test_admin_*`（文件名序在我后面）会在用户列表里看到一个多余的超管。

依赖 API 与 PG 已就绪（`tools/local-verify/run-smoke.ps1`）。
"""

from __future__ import annotations

import asyncio
import socket
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import cli
from app.core.config import settings
from app.db import base

from .conftest import ADMIN_PASSWORD, ADMIN_PHONE, rand_phone, sql_exec, sql_fetch

_CFG_FIELDS = ("dsn", "redis_url", "schema_file", "admin_init_phone", "admin_init_password")


@pytest.fixture(autouse=True)
def _no_global_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `_seed_admin` 用的 `SessionLocal` 换成 **NullPool** 版本。

    ⚠️ 为什么必须换：产品代码里 `app.db.base.SessionLocal` 绑的是**全局 engine**（带连接池），
    而本文件每个用例都从 `cli.main` 里 `asyncio.run(...)` 起一个**新事件循环** ——
    池里属于旧循环的连接会被下一个用例复用，症状不是断言失败，而是**随机在别的用例上炸**：

        RuntimeWarning: coroutine 'Connection._cancel' was never awaited
        AttributeError: 'NoneType' object has no attribute 'send'

    `test_sms_inproc.py` 用同样手法（NullPool）绕开同一问题；照抄它，不另发明。
    也试过"用例结束后 `asyncio.run(engine.dispose())` 丢池" —— 那个**会挂住**
    （连接绑在已关闭的循环上，dispose 等不到它们关闭），所以换成这条。

    ⚠️ URL 直接取 `base.engine.url`，**不用 `settings.dsn`**：后者是给 asyncpg 用的
    无驱动后缀形式（`postgresql://`），`create_async_engine` 需要 `+asyncpg`，
    传错会退到 psycopg2 并报 `No module named 'psycopg2'`。
    """
    engine = create_async_engine(base.engine.url, poolclass=NullPool)
    monkeypatch.setattr(
        base,
        "SessionLocal",
        async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False),
    )


def _cfg(**overrides: Any) -> SimpleNamespace:
    """`app.cli.settings` 的替身：只覆盖要改的字段，其余沿用真实值。

    用替身而不是 `monkeypatch.setattr(settings, "dsn", ...)`：后者依赖配置模型
    允许赋值（未冻结），而替身对配置模型的内部约定零依赖。
    """
    base = {name: getattr(settings, name) for name in _CFG_FIELDS}
    base.update(overrides)
    return SimpleNamespace(**base)


# 自建临时目录：**刻意不用 pytest 的 `tmp_path`**。
#
# 本机（WorkBuddy 沙箱）在 `shutil.rmtree` 上装了"批量删除守卫"，而 pytest 会在会话
# 前后清理它自己的临时目录树 —— 命中守卫的表现是 `SystemExit(1)`，且**测试本身全绿**：
# 只看汇总行会以为一切正常，实际进程异常退出（更糟的一次是卡在那里等批准）。
# CI（ubuntu）没有这个 shim，但本地必须能跑，所以自己建目录、只写单文件、不删目录。
_TMP_DIR = Path(tempfile.gettempdir()) / "yijian-cli-tests"


def _sql_file(name: str, body: str) -> Path:
    """在自建临时目录里写一个文本文件并返回路径（同名覆盖，不删目录）。"""
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    p = _TMP_DIR / name
    p.write_text(body, encoding="utf-8")
    return p


def _out(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out


# ---------------------------------------------------------------- 替身


class _PongServer:
    """最小 Redis 协议桩：收到含 `PING` 的报文回 `+PONG`，其余回 `+OK`。

    `_wait_redis` 只要 `await client.ping()` 不抛异常就算就绪，不校验返回值，
    所以不需要实现完整协议。
    """

    def __init__(self) -> None:
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.port: int = self._srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _handle(conn: socket.socket) -> None:
        with conn:
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                conn.sendall(b"+PONG\r\n" if b"PING" in data.upper() else b"+OK\r\n")

    def close(self) -> None:
        self._srv.close()


class _MissingFile:
    """替代 `pathlib.Path`：**所有**路径都不存在（`is_file()` 恒为 False）。

    只需要 `_resolve_*_file` 用到的那点表面：构造 / `resolve()` / `parents[i]` /
    `/` / `is_file()` / `cwd()` / `name`。
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def resolve(self) -> _MissingFile:
        return self

    def is_file(self) -> bool:
        return False

    def __truediv__(self, _other: Any) -> _MissingFile:
        return self

    def __getitem__(self, _index: Any) -> _MissingFile:
        return self

    @property
    def parents(self) -> _MissingFile:
        return self

    @property
    def name(self) -> str:
        return "schema.sql"

    @staticmethod
    def cwd() -> _MissingFile:
        return _MissingFile()


class _StubConn:
    """`raw_asyncpg_connection()` 的替身：按 SQL 里的关键字回放结果。

    刻意**不认识就断言失败**（而不是返回 None）—— 否则函数换了查询而桩没跟上时，
    测试会静默地"验了个假的"。
    """

    def __init__(
        self, *, subjects: int = 1, questions: int = 0, fail_execute: bool = False
    ) -> None:
        self._subjects = subjects
        self._questions = questions
        self._fail = fail_execute
        self.closed = False
        self.executed: list[str] = []

    async def fetchval(self, sql: str) -> int:
        if "subjects" in sql:
            return self._subjects
        if "questions" in sql:
            return self._questions
        raise AssertionError(f"桩不认识这条 SQL：{sql}")

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self._fail:
            raise RuntimeError("boom: 种子脚本里有语法错误")
        self._questions = 7  # 桩的设定：导入后变成 7 条

    async def close(self) -> None:
        self.closed = True


def _stub_factory(stub: _StubConn):
    async def _make() -> _StubConn:
        return stub

    return _make


# ---------------------------------------------------------------- wait-db / wait-redis


def test_wait_db_returns_0_when_postgres_is_up(capsys: pytest.CaptureFixture[str]) -> None:
    """PG 可连 → 0，且打印 "PostgreSQL ready"（等待类命令的可观测副作用就是这行日志）。"""
    assert cli.main(["wait-db", "--timeout", "20"]) == 0
    assert "PostgreSQL ready" in _out(capsys)


def test_wait_db_times_out_with_1_when_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """库不可达 → **超时后返回 1**，不能永远挂着（否则容器启动流程会静默卡死）。"""
    monkeypatch.setattr(cli, "settings", _cfg(dsn="postgresql://yijian@127.0.0.1:1/yijian"))
    t0 = time.monotonic()
    assert cli.main(["wait-db", "--timeout", "1"]) == 1
    elapsed = time.monotonic() - t0
    assert "timed out waiting for PostgreSQL" in _out(capsys)
    # 正向对照的替代判据：**确实重试过**（一次失败后至少 sleep 1.5s 才判超时）。
    # 少了它，"返回 1"可能只是"函数一进来就 return 1"。
    assert elapsed >= 1.3, f"没等过就返回了（{elapsed:.2f}s）—— 重试循环可能没跑"


def test_wait_redis_returns_0_when_it_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Redis 能 PING 通 → 0（用协议桩造出这条真实环境到不了的分支）。"""
    server = _PongServer()
    try:
        monkeypatch.setattr(cli, "settings", _cfg(redis_url=f"redis://127.0.0.1:{server.port}/0"))
        assert cli.main(["wait-redis", "--timeout", "10"]) == 0
        assert "Redis ready" in _out(capsys)
    finally:
        server.close()


def test_wait_redis_times_out_with_1_when_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Redis 不可达 → 超时后返回 1。"""
    monkeypatch.setattr(cli, "settings", _cfg(redis_url="redis://127.0.0.1:1/0"))
    t0 = time.monotonic()
    assert cli.main(["wait-redis", "--timeout", "1"]) == 1
    elapsed = time.monotonic() - t0
    assert "timed out waiting for Redis" in _out(capsys)
    assert elapsed >= 1.3, f"没等过就返回了（{elapsed:.2f}s）—— 重试循环可能没跑"


# ---------------------------------------------------------------- init-db


def test_resolvers_return_none_when_no_candidate_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有候选路径都不存在 → `None`（容器里没挂 schema/seed 时的真实处境）。"""
    monkeypatch.setattr(cli, "Path", _MissingFile)
    assert cli._resolve_schema_file() is None
    assert cli._resolve_seed_file() is None


def test_init_db_skips_when_schema_file_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺 schema.sql → **跳过并返回 0**：这是"没挂脚本"的正常情形，不该让容器启动失败。"""
    monkeypatch.setattr(cli, "Path", _MissingFile)
    assert cli.main(["init-db"]) == 0
    assert "not found; skipping init-db" in _out(capsys)


def test_init_db_is_idempotent_when_schema_present(capsys: pytest.CaptureFixture[str]) -> None:
    """库已建（真 PG）→ 不重复执行 schema.sql，返回 0。"""
    assert cli.main(["init-db"]) == 0
    assert "schema already present" in _out(capsys)


@contextmanager
def _scratch_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], int]]:
    """建一个**临时空库**，把 `cli.raw_asyncpg_connection` 指过去；用完删掉。

    为什么需要它：`init-db` 的"从零建库"路径要求库里**没有 `users` 表**，
    而 `run-smoke.ps1` 与 CI 都是**先用 psql 建好表**再调 CLI —— 两个环境都走不到那条路。
    真在主库上走，代价是先 `DROP SCHEMA public CASCADE`（整库重建，测试之间还会互相踩）。

    产出 `table_count()`，让调用方能断言"schema 真的建出表了"。
    """
    import asyncpg

    name = f"cli_probe_{uuid4().hex[:8]}"
    admin_dsn = settings.dsn
    scratch = f"{admin_dsn.rsplit('/', 1)[0]}/{name}"

    async def _admin(sql: str) -> None:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    async def _connect() -> Any:
        return await asyncpg.connect(scratch)

    async def _table_count() -> int:
        conn = await asyncpg.connect(scratch)
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            )
        finally:
            await conn.close()

    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    monkeypatch.setattr(cli, "raw_asyncpg_connection", _connect)
    try:
        yield lambda: asyncio.run(_table_count())
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def test_init_db_fails_loudly_when_schema_sql_is_broken(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """schema.sql 执行失败 → **返回 1 并说明**，不能假装建好了。

    契约意义：`init-db` 在容器启动链路的最前面，它谎报成功 =
    "应用起来了、但所有查询都报表不存在"，比直接失败难查十倍。
    """
    f = _sql_file("broken_schema.sql", "THIS IS NOT VALID SQL;\n")
    monkeypatch.setattr(cli, "_resolve_schema_file", lambda: f)

    with _scratch_database(monkeypatch):
        assert cli.main(["init-db"]) == 1
        assert "init-db failed" in _out(capsys)


def test_init_db_applies_schema_on_empty_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**空库 → 真的把 schema.sql 应用上去**，回报表数；再跑一次走"已存在"分支。

    这条路是 `init-db` 存在的理由（容器第一次起来时靠它建表），
    而 run-smoke 与 CI **都没走过**（它们先用 psql 建表）。

    ⚠️ 顺带验证一件产品行为：`_init_db` 是用 **asyncpg 一次性执行整份 schema.sql**
    （不是 psql），所以 schema.sql 里若有反斜杠开头的 psql 元命令，这里会直接红 ——
    那正是"首次部署根本起不来"的缺陷，应当被发现而不是被绕开。
    """
    with _scratch_database(monkeypatch) as table_count:
        assert cli.main(["init-db"]) == 0
        out = _out(capsys)
        assert "applying" in out and "schema applied" in out, out
        assert table_count() > 10, "schema.sql 应当建出十几张表/视图"

        # 幂等：同一个库再跑一次 → 走"已存在"分支，且不再执行 SQL
        assert cli.main(["init-db"]) == 0
        assert "schema already present" in _out(capsys)


# ---------------------------------------------------------------- seed-rbac


def _schema_file(name: str, body: str) -> Path:
    return _sql_file(name, body)


def test_seed_rbac_skips_when_schema_file_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺 schema.sql → 0（跳过）。"""
    monkeypatch.setattr(cli, "Path", _MissingFile)
    assert cli.main(["seed-rbac"]) == 0
    assert "not found; skipping seed-rbac" in _out(capsys)


def test_seed_rbac_rejects_schema_without_slice_markers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """标记缺失 → **返回 1**，不静默当"跑过了"。"""
    f = _schema_file("no_markers.sql", "-- 这个 schema 里没有 RBAC-SEED 标记\nSELECT 1;\n")
    monkeypatch.setattr(cli, "_resolve_schema_file", lambda: f)
    assert cli.main(["seed-rbac"]) == 1
    out = _out(capsys)  # ⚠️ 只读一次：readouterr() 会**清空**缓冲，读两次第二次是空串
    assert "slice markers" in out and "not found" in out


def test_seed_rbac_refuses_slice_without_statement(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """标记在、但切出来的块里**没有 `;`** → 返回 1。

    这是**回归测试**：当初用 `-- 12.1 角色` 当切片标记，解释性注释里又引用了同一短语，
    `find()` 命中最前面的注释 → 切出 101 个字符的纯注释，交给 asyncpg 执行后报
    `'NoneType' object has no attribute 'decode'` —— 报错完全指不到真正的原因。
    现在这一层显式拒绝，就是为了让"标记被改坏"表现为**一句能读懂的话**。
    """
    f = _schema_file(
        "empty_slice.sql",
        "-- RBAC-SEED:START\n只有注释，没有一条可执行语句\n-- RBAC-SEED:END\n",
    )
    monkeypatch.setattr(cli, "_resolve_schema_file", lambda: f)
    assert cli.main(["seed-rbac"]) == 1
    out = _out(capsys)
    assert "no executable statement" in out and "refusing" in out


def test_seed_rbac_reports_failure_when_sql_is_invalid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """切片合法但 SQL 执行失败 → 返回 1 **并打印完整栈**（运维命令失败时必须给全栈，否则只能猜）。"""
    f = _schema_file(
        "bad_sql.sql",
        "-- RBAC-SEED:START\nTHIS IS NOT VALID SQL;\n-- RBAC-SEED:END\n",
    )
    monkeypatch.setattr(cli, "_resolve_schema_file", lambda: f)
    assert cli.main(["seed-rbac"]) == 1
    out = _out(capsys)
    assert "RBAC seed replay failed" in out
    assert "Traceback (most recent call last)" in out, "失败时必须打全栈"


def test_seed_rbac_replays_on_real_schema_and_is_idempotent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """真 schema 上重放 → 0，且**角色/权限条数不变**（种子里每条都是 `ON CONFLICT DO NOTHING`）。

    这是"可以反复跑"的契约：容器每次重启都会执行它，一旦不幂等就会重复插数据。
    它同时也是**切片逻辑的正常路径**（对真实 `schema.sql` 切出可执行块）。
    """
    before = sql_fetch(
        "SELECT (SELECT count(*) FROM roles) AS r, (SELECT count(*) FROM role_permissions) AS p"
    )[0]
    assert cli.main(["seed-rbac"]) == 0
    out = _out(capsys)
    assert "RBAC seed replayed" in out, out
    assert "permissions" in out, "应当逐角色打印权限数（运维要能一眼看出种子生效了）"
    after = sql_fetch(
        "SELECT (SELECT count(*) FROM roles) AS r, (SELECT count(*) FROM role_permissions) AS p"
    )[0]
    assert after == before, f"重放必须幂等：{before} -> {after}"


# ---------------------------------------------------------------- seed-admin


def test_seed_admin_skips_without_phone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """未配 `ADMIN_INIT_PHONE` → 0（跳过），不是报错。"""
    monkeypatch.setattr(cli, "settings", _cfg(admin_init_phone="   "))
    assert cli.main(["seed-admin"]) == 0
    assert "ADMIN_INIT_PHONE not set" in _out(capsys)


def test_seed_admin_rejects_short_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """密码短于 8 位 → **返回 1**：宁可启动失败，也不能建出一个弱口令超管。

    ⚠️ 手机号必须同时给上（`_seed_admin` 先查手机号、再查口令长度）——
    否则测到的是"没配手机号就跳过"那条分支，而不是"口令太短被拒"。
    """
    monkeypatch.setattr(
        cli,
        "settings",
        _cfg(admin_init_phone=rand_phone(), admin_init_password="short"),
    )
    assert cli.main(["seed-admin"]) == 1
    assert "ADMIN_INIT_PASSWORD must be >= 8 chars" in _out(capsys)


def test_seed_admin_reports_1_when_super_admin_role_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """角色表里没有 `super_admin` → 返回 1（提示先跑 init-db），而不是建一个没有角色的管理员。"""
    from app.services import rbac_service

    async def _none(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(
        cli,
        "settings",
        _cfg(admin_init_phone=rand_phone(), admin_init_password=ADMIN_PASSWORD),
    )
    monkeypatch.setattr(rbac_service, "get_role_by_code", _none)
    assert cli.main(["seed-admin"]) == 1
    assert "no super_admin role in table" in _out(capsys)


def test_seed_admin_creates_admin_when_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """超管不存在 → **建出来**（含 profile 与角色），这是"首次部署"的路径。

    每次 run-smoke 都是在**已有超管**的库上跑（走的是 `exists` 分支），
    所以这条"从无到有"的分支本地一直没被走过；CI 因为是全新库才走到。
    用例用随机手机号，`finally` 里按外键顺序删干净。
    """
    phone = rand_phone()
    uid: int | None = None
    try:
        monkeypatch.setattr(
            cli,
            "settings",
            _cfg(admin_init_phone=phone, admin_init_password=ADMIN_PASSWORD),
        )
        assert cli.main(["seed-admin"]) == 0
        assert f"created super admin {phone}" in _out(capsys)

        rows = sql_fetch("SELECT id, is_deleted, status FROM users WHERE phone = $1", phone)
        assert rows, "应当建出这个超管"
        uid = int(rows[0]["id"])
        assert rows[0]["is_deleted"] is False and rows[0]["status"] == "active"

        # 关键副作用：profile 行 + 角色绑定，缺一个这个超管都是"半成品"
        assert sql_fetch("SELECT 1 FROM user_profiles WHERE user_id = $1", uid)
        roles = sql_fetch(
            "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = $1",
            uid,
        )
        assert [r["code"] for r in roles] == ["super_admin"], roles
    finally:
        if uid is not None:
            for sql in (
                "DELETE FROM user_roles WHERE user_id = $1",
                "DELETE FROM user_profiles WHERE user_id = $1",
                "DELETE FROM user_sessions WHERE user_id = $1",
                "DELETE FROM users WHERE id = $1",
            ):
                sql_exec(sql, uid)
            assert not sql_fetch("SELECT 1 FROM users WHERE id = $1", uid), "清理必须彻底"


def test_seed_admin_resets_password_for_existing_admin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """超管已存在 → 不新建、**重置口令并激活**（幂等：可以反复跑）。"""
    monkeypatch.setattr(
        cli,
        "settings",
        _cfg(admin_init_phone=ADMIN_PHONE, admin_init_password=ADMIN_PASSWORD),
    )
    before = sql_fetch("SELECT count(*) AS n FROM users WHERE phone = $1", ADMIN_PHONE)[0]["n"]
    assert cli.main(["seed-admin"]) == 0
    assert f"super admin {ADMIN_PHONE} exists" in _out(capsys)
    after = sql_fetch("SELECT count(*) AS n FROM users WHERE phone = $1", ADMIN_PHONE)[0]["n"]
    assert after == before, "幂等：不应重复建号"


# ---------------------------------------------------------------- seed-questions


def test_seed_questions_skips_when_seed_file_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺 `data/seed/questions.sql` → 0（跳过，并提示先跑生成器）。"""
    monkeypatch.setattr(cli, "Path", _MissingFile)
    assert cli.main(["seed-questions"]) == 0
    assert "questions.sql not found; skipping" in _out(capsys)


def test_seed_questions_skips_when_bank_already_imported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """题库非空 → **0 且不重复导入**（幂等；用真 PG 数条数）。

    ⚠️ 种子文件指向**临时文件**（内容是无害的 `SELECT 1;`），不指向真实
    `data/seed/questions.sql`。理由：这条用例的意义是"题库非空时不导入"，
    而万一哪天那个判断被改坏（变异验证时就会），拿真文件去跑会**真的把 6000 道题
    再灌一遍** —— 污染库、还可能卡在唯一约束上。指向临时文件则最坏也只是白跑一条 SELECT。
    """
    f = _sql_file("seed_dup.sql", "SELECT 1;\n")
    monkeypatch.setattr(cli, "_resolve_seed_file", lambda: f)

    existing = sql_fetch("SELECT count(*) AS n FROM questions")[0]["n"]
    assert existing > 0, "这条用例的前提是库里已经有题"
    assert cli.main(["seed-questions"]) == 0
    out = _out(capsys)
    assert "already has" in out and "skipping import" in out
    assert sql_fetch("SELECT count(*) AS n FROM questions")[0]["n"] == existing


def test_seed_questions_fails_when_subjects_table_is_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """科目表为空 → **返回 1**，而不是"导入 0 条也算成功"。"""
    stub = _StubConn(subjects=0)
    monkeypatch.setattr(cli, "raw_asyncpg_connection", _stub_factory(stub))
    assert cli.main(["seed-questions"]) == 1
    assert "subjects table is empty" in _out(capsys)
    assert stub.closed, "无论走哪条分支都必须关连接"


def test_seed_questions_imports_when_bank_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """空题库 → 执行种子脚本、回报条数、返回 0。"""
    f = _sql_file("seed_ok.sql", "SELECT 1;\n")
    stub = _StubConn(subjects=6, questions=0)
    monkeypatch.setattr(cli, "_resolve_seed_file", lambda: f)
    monkeypatch.setattr(cli, "raw_asyncpg_connection", _stub_factory(stub))
    assert cli.main(["seed-questions"]) == 0
    assert stub.executed, "应当真的执行了种子脚本"
    assert "question bank imported; 7 questions total" in _out(capsys)
    assert stub.closed


def test_seed_questions_reports_1_when_import_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """导入报错 → 返回 1 + 日志（不能静默当成功）。"""
    f = _sql_file("seed_broken.sql", "BROKEN;\n")
    stub = _StubConn(subjects=6, questions=0, fail_execute=True)
    monkeypatch.setattr(cli, "_resolve_seed_file", lambda: f)
    monkeypatch.setattr(cli, "raw_asyncpg_connection", _stub_factory(stub))
    assert cli.main(["seed-questions"]) == 1
    assert "question import failed" in _out(capsys)
    assert stub.closed


# ---------------------------------------------------------------- 入口


def test_unknown_command_exits_with_2() -> None:
    """非法子命令 → argparse 退出码 **2**（不是 0、也不是 1）。

    契约意义：编排脚本靠退出码判断成败，2 = "用法错"（人改参数）与 1 = "执行失败"
    （环境/数据问题）必须可分，否则排障方向会被带偏。
    """
    with pytest.raises(SystemExit) as ei:
        cli.main(["definitely-not-a-command"])
    assert ei.value.code == 2


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("wait-db", "_wait_db"),
        ("wait-redis", "_wait_redis"),
        ("init-db", "_init_db"),
        ("seed-rbac", "_seed_rbac"),
        ("seed-admin", "_seed_admin"),
        ("seed-questions", "_seed_questions"),
    ],
)
def test_main_dispatches_each_command_to_its_own_handler(
    command: str, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """六个子命令各自**路由到自己的处理函数**，并把它的返回码原样带出来。

    比"读源码找字符串"强的地方：它验证的是**真的调用了谁** —— 少一个分支、
    或者两个子命令接到同一个处理函数，这里都会红。用 7 当哨兵值是为了证明
    "返回码是**带出来**的"，不是恰好也返回了 0。
    """
    hit: list[str] = []

    async def _stub(*_a: Any, **_k: Any) -> int:
        hit.append(target)
        return 7

    monkeypatch.setattr(cli, target, _stub)
    assert cli.main([command]) == 7, f"{command} 没有把处理函数的返回码带出来"
    assert hit == [target], f"{command} 应当只调用 {target}"
