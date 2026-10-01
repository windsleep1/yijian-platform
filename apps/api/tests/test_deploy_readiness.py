"""部署就绪度：把"只有真部署才会暴露"的几条契约**钉成用例**。

为什么值得单独一个文件
----------------------
这些性质**本地怎么跑都碰不到**：连接串少个驱动前缀、Dockerfile 写死端口、
入口脚本因为一个**可选**依赖自杀 —— 全都要等到真部署那一刻才出现，
而那时排查成本最高（平台日志、冷启动、域名/证书混在一起）。

⇒ 判据（同族：把判据写成脚本，而不是写成"记得检查"）：
  **凡是能在本地断言的"部署契约"，就不要留到部署现场去发现。**

这个文件**不碰数据库**，全是纯函数 + 读文件 + 读 `app.routes`，所以很快。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote as urllib_unquote
from urllib.parse import urlsplit

import pytest
from sqlalchemy.engine import make_url

from app.core.config import (
    Settings,
    _host_by_lenient_parser,
    _host_by_strict_parser,
    assert_url_parses_the_same,
    normalize_database_url,
)

# 给断言用的**短别名**：让"哪个解析器"在断言里一眼可读。
_host_by_lenient = _host_by_lenient_parser
_host_by_strict = _host_by_strict_parser

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO / "apps" / "api" / "Dockerfile"
ENTRYPOINT = REPO / "apps" / "api" / "docker-entrypoint.sh"

#: Neon 控制台"复制连接串"按钮给出来的样子（实测格式）。
NEON_URL = (
    "postgresql://alice:secret@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def _settings(database_url: str, **over: object) -> Settings:
    """构造一个**只看入参**的 Settings（显式关掉 .env，避免被本机配置污染）。

    `app_env="local"` 是必须的：prod 下 `_check_secret` 会因为默认密钥直接抛错，
    那会把"连接串归一"这条用例变成"密钥校验"的用例（测错对象）。
    """
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        app_env="local",
        database_url=database_url,
        **over,
    )


# ---------------------------------------------------------------- ① 连接串归一


def test_neon_url_is_normalized() -> None:
    """Neon 的串：补 `+asyncpg` + 摘掉 libpq 参数 + 判定要 SSL。

    三件事**必须一起成立**：缺第一件报 `No module named 'psycopg2'`（去加载 psycopg2），
    缺第二件 asyncpg 收到它不认识的关键字，缺第三件连不上（Neon 强制 TLS）。
    """
    url, wants_ssl = normalize_database_url(NEON_URL)
    assert url == (
        "postgresql+asyncpg://alice:secret@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb"
    )
    assert wants_ssl is True
    assert "sslmode" not in url
    assert "channel_binding" not in url


def test_local_url_is_untouched() -> None:
    """本地串不能被"顺手改一改" —— 这是最常跑的那条路径。"""
    raw = "postgresql+asyncpg://yijian:yijian@localhost:55432/yijian"
    assert normalize_database_url(raw) == (raw, False)


@pytest.mark.parametrize("scheme", ["postgres://", "postgresql://", "postgresql+psycopg2://"])
def test_any_scheme_gets_asyncpg_driver(scheme: str) -> None:
    """各家给的方言前缀都要归一到 `+asyncpg`。

    ★ 判据：**不补驱动 = 现场一条 `No module named 'psycopg2'`** ——
      而依赖清单里根本没有 psycopg2（它只是 SQLAlchemy 对 `postgresql://` 的默认选择）。
    """
    url, _ = normalize_database_url(f"{scheme}u:p@h:5432/db")
    assert url == "postgresql+asyncpg://u:p@h:5432/db"


@pytest.mark.parametrize("val", ["disable", "allow"])
def test_sslmode_optout_is_respected(val: str) -> None:
    """`disable` / `allow` 是**显式不要**加密 ⇒ 不能被判成要 SSL。

    ★ 反例面（成对验）：只测"`require` ⇒ True"会漏掉"`disable` 被误判成 True"
      的实现。两头都要有场景。
    """
    _, wants_ssl = normalize_database_url(f"postgresql://u:p@h/db?sslmode={val}")
    assert wants_ssl is False


def test_password_with_special_chars_survives() -> None:
    """只处理**第一个 `?` 之后** —— 密码里的特殊字符不能被当连接参数切掉。

    （真实密码里出现 `?` `&` 的概率不低，尤其是随机生成的。）
    """
    url, _ = normalize_database_url("postgresql://u:p%3Fw%26d@h/db?sslmode=require")
    assert "u:p%3Fw%26d@h/db" in url


def test_unknown_query_params_are_kept() -> None:
    """只摘**名单内**的 libpq 参数，其余原样保留（不替用户做它没要求的决定）。"""
    url, _ = normalize_database_url("postgresql://u:p@h/db?application_name=yijian&sslmode=require")
    assert url.endswith("/db?application_name=yijian")


def test_settings_flips_ssl_flag_from_url() -> None:
    """URL 里写了 `sslmode=require` ⇒ `db_ssl_require` 自动为 True（不用人再填一次）。"""
    s = _settings(NEON_URL)
    assert s.database_url == (
        "postgresql+asyncpg://alice:secret@ep-cool-name-123456.us-east-2.aws.neon.tech/neondb"
    )
    assert s.db_ssl_require is True


def test_settings_ssl_defaults_off_for_local() -> None:
    s = _settings("postgresql+asyncpg://yijian:yijian@localhost:55432/yijian")
    assert s.db_ssl_require is False


def test_settings_ssl_can_be_forced_on_without_sslmode() -> None:
    """托管商没给 `sslmode` 时，`DB_SSL_REQUIRE=true` 仍要能生效（环境变量入口）。"""
    s = _settings("postgresql://u:p@h/db", db_ssl_require=True)
    assert s.db_ssl_require is True


def test_asyncpg_connect_args_follow_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """裸 asyncpg 的 `ssl` 参数**必须跟着 flag 走**。

    ★★ 这条是"摘掉 `sslmode`"的**配套断言**：只摘不传 ⇒ 托管 PG 连不上。
      两者是同一个决定的两半，所以必须成对出现在用例里（成对验）。
    """
    from app.core.config import settings
    from app.db import base

    monkeypatch.setattr(settings, "db_ssl_require", True)
    assert base.asyncpg_connect_args() == {"ssl": True}
    monkeypatch.setattr(settings, "db_ssl_require", False)
    assert base.asyncpg_connect_args() == {}


# ---------------------------------------------------------------- ①b 两个解析器必须一致
#
# ★★ 这一组是 2026-10-01 Render **第二次**部署真故障的回归。
#
#   缘由：同一条 `DATABASE_URL` 被**两个解析器**读 —— `settings.dsn`（裸 asyncpg 用）
#   走 `urllib`，`settings.database_url`（SQLAlchemy 引擎用）走 SQLAlchemy 的 URL 正则。
#   两者**从同一个字符串派生**，所以只要解出的主机名一致就不会出事；
#   一旦不一致，症状是**最不像解析问题的那一种**：
#
#       [cli] PostgreSQL ready      ← asyncpg 解对了，连得上
#         …几秒后…
#       socket.gaierror: [Errno -2] Name or service not known   ← 引擎解错了
#
#   ⇒ 判据：**别只测"归一后的字符串等于什么"，要测"两个解析器解出的主机名相同"。**
#     前者是"我抄对了没有"，后者才是真性质（同族：探活路径要连着 app 一起验）。

_HOST = "ep-cool-name-123456-pooler.us-east-2.aws.neon.tech"


@pytest.mark.parametrize("invisible", ["\n", "\t", "\r"])
def test_invisible_whitespace_never_reaches_the_hostname(invisible: str) -> None:
    """★★ 折行粘贴留下的不可见字符，**不许活着走到主机名里**。

    实测（asyncpg 0.30 + SQLAlchemy 2.0.54，都是容器里那一版）：

        `…neon.tech\\n/neondb`  →  urllib 得 `…neon.tech`   ← wait-db 连得上
                                →  SQLAlchemy 得 `…neon.tech\\n`  ← 引擎报 DNS 失败

    只测"归一后的串长什么样"抓不到它（那个串看起来完全正常）；
    必须**分别用两个真解析器解一遍**。
    """
    raw = f"postgresql://alice:secret@{_HOST}{invisible}/neondb?sslmode=require"
    url, wants_ssl = normalize_database_url(raw)

    assert invisible not in url, "不可见字符必须被剔除，否则它会成为主机名的一部分"
    assert url == f"postgresql+asyncpg://alice:secret@{_HOST}/neondb"
    assert wants_ssl is True

    # 成对验：两个解析器必须解出**同一个**主机名
    assert make_url(url).host == _HOST, "SQLAlchemy 那一族（引擎走这条）"
    assert urlsplit(url).hostname == _HOST, "urllib 那一族（wait-db / init-db 走这条）"


def test_leading_invisible_whitespace_does_not_break_scheme_detection() -> None:
    """行首的不可见字符不能把"补驱动前缀"这步顶掉。

    ⚠️ 顺序有讲究：**剔除空白必须在判断 scheme 之前** ——
      否则 `"\\npostgresql://…"` 不以任何已知前缀开头 ⇒ 不补 `+asyncpg`
      ⇒ 现场变成 `No module named 'psycopg2'`（一个**完全指不到原因**的错）。
    """
    url, _ = normalize_database_url(f"\n  postgresql://alice:secret@{_HOST}/neondb\n")
    assert url.startswith("postgresql+asyncpg://"), "前缀没被补上 ⇒ 会去找 psycopg2"


@pytest.mark.parametrize("special", ["@", "?", "#"])
def test_password_specials_do_not_move_the_hostname(special: str) -> None:
    """密码里的字面量 `@` `?` `#` 不许把主机名切歪。

    这类串原本会让**两族一起**解错主机名（`…:pa@ss@host` ⇒ 主机名变成 `ss@host`），
    报出来同样是 `Name or service not known` —— 和 ⓿ 那类是**同一个症状、不同的成因**。

    归一把 userinfo 重新百分号编码之后，两边都会解出**同一个正确的主机名**，
    且密码原文不变（两个解析器都会 unquote）。
    """
    password = f"pa{special}ss"
    raw = f"postgresql://alice:{password}@{_HOST}/neondb?sslmode=require"
    url, _ = normalize_database_url(raw)

    assert make_url(url).host == _HOST
    assert urlsplit(url).hostname == _HOST
    # 编码是"形式"变了，"内容"不能变 —— 两个解析器解出来的密码都必须是原文
    assert make_url(url).password == password
    assert urllib_unquote(urlsplit(url).password or "") == password


def test_url_parser_tripwire_can_actually_fire() -> None:
    """★ 硬约定 J：**测量工具本身要被验证** —— 这条判据必须能报红。

    否则"两个解析器一致"可能只是一句**永远不会失败的常数**，
    而它给人的安全感反而是负的（这次就是：我先信了"同一个字符串 ⇒ 同一个主机"）。

    造一个**已知分岔**的串（就是真事故那一串），它必须抛。
    """
    divergent = f"postgresql+asyncpg://alice:secret@{_HOST}\n/neondb"
    assert _host_by_strict(divergent) != _host_by_lenient(divergent), "前提变了：这条用例要重看"
    with pytest.raises(ValueError, match="两个不同的主机名"):
        assert_url_parses_the_same(divergent)


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://alice:secret@h/db?sslmode=require",
        "postgresql://alice:secret@h:5432/db",
        "postgresql+asyncpg://alice:secret@[::1]:5432/db",
        "postgresql://alice@h/db",
        "postgresql://h/db",
        "postgresql://alice:pa%40ss@h/db",
        "postgresql://alice:pa@ss@h/db",
        "postgresql://alice:secret@h/db?application_name=yijian&sslmode=require",
        "sqlite:///tmp/x.db",
    ],
)
def test_normalization_output_is_always_parser_agnostic(raw: str) -> None:
    """归一化的**出口**必须永远是"两族一致"的 —— 上面那些都是真实出现过的形状。"""
    url, _ = normalize_database_url(raw)
    assert_url_parses_the_same(url)  # 不抛即通过
    assert _host_by_lenient(url) == _host_by_strict(url)


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql+asyncpg://alice:secret@{_HOST}/neondb",
        f"postgresql+asyncpg://alice:secret@{_HOST}:5432/neondb",
        "postgresql+asyncpg://alice:secret@[::1]:5432/db",
        "postgresql+asyncpg://alice:pa%40ss@h/db",
        "postgresql+asyncpg://h/db",
        "postgresql+asyncpg://alice@h/db",
    ],
)
def test_the_two_models_match_the_real_parsers(url: str) -> None:
    """★ 硬约定 J 的第二半：**校验器自己的模型，必须对着真解析器校准**。

    上面那条 tripwire 是拿"我写的两个模型"互相比 —— 如果模型本身写错了，
    它就会变成一个**新的错误信念**（写这条用例时真发生过：IPv6 那格
    `urlsplit` 去方括号、我的模型没去 ⇒ 把一条本来正常的串判成"分岔"）。

    ⇒ 判据：**凡是我用代码"模拟"别人的行为，就必须有一格是拿真货对照的。**
    """
    assert _host_by_lenient(url) == (urlsplit(url).hostname or "").lower()
    assert _host_by_strict(url) == (make_url(url).host or "").lower()


# ---------------------------------------------------------------- ② 容器契约


def test_dockerfile_reads_injected_port() -> None:
    """托管平台注入 `PORT` ⇒ 启动命令必须**真的**读它。

    ★ 为什么这条值得写：`CMD ["uvicorn", …, "--port", "${PORT:-8000}"]`（JSON 数组）
      和 shell 形式**看起来一样**，但 **JSON 数组形式不做变量展开** ——
      uvicorn 会收到字面量 `${PORT:-8000}` 然后报 `Invalid value for '--port'`。
      这类"看着对、跑起来才发现"的错误，本地 `docker run` 不带 PORT 时**不会出现**。
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "${PORT:-8000}" in text, "启动命令与探活地址都必须读注入的 PORT"
    assert 'CMD ["sh", "-c",' in text, "启动命令必须是 shell 形式（JSON 数组不做变量展开）"


def test_dockerfile_healthcheck_probes_a_route_that_exists() -> None:
    """探活路径**必须是真的存在的路由** —— 两边连着验，不各写各的。

    只查 Dockerfile 里的字符串，等于在验"我抄对了没有"；
    真正的判据是"**这个路径在 app 上真的存在**"。
    """
    from app.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/health" in paths, "Dockerfile 探活的路径必须真的注册在 app 上"
    assert "/api/v1/health" in DOCKERFILE.read_text(encoding="utf-8")


def test_entrypoint_survives_a_missing_redis() -> None:
    """★ 入口脚本不能在 Redis 缺失时自杀 —— 它和 `db/base.py` 的承诺必须一致。

    背景：脚本开头是 `set -e`，而 `wait-redis` 等不到时返回 1
    ⇒ **裸调用会让整个入口脚本退出、容器起不来**。
    这与 `app/db/base.py` 模块文档写的"Redis 不可用时不阻塞启动"**直接矛盾** ——
    代码层的设计意图被入口脚本推翻了。托管平台免费档**默认没有 Redis**，
    所以这条矛盾在部署当天一定会撞上。
    """
    sh = ENTRYPOINT.read_text(encoding="utf-8")
    assert "set -e" in sh, "前提变了，这条用例要重看"
    assert "if python -m app.cli wait-redis" in sh, "wait-redis 必须被包在条件里（非致命）"
    # 反例面：裸调用**不能**还在（成对验 —— 否则"又加了一处裸调用"照样绿）
    assert "\npython -m app.cli wait-redis" not in sh


def test_entrypoint_keeps_postgres_fatal_and_still_execs() -> None:
    """数据库仍然**致命**（起一个什么都做不了的进程只会让排障更难），且仍以 exec 收尾。

    `exec "$@"` 不能丢：少了它，`sh` 会变成 1 号进程的子进程，
    容器收不到 SIGTERM ⇒ 平台只能强杀（优雅关闭失效）。
    """
    sh = ENTRYPOINT.read_text(encoding="utf-8")
    assert "\npython -m app.cli wait-db" in sh
    assert 'exec "$@"' in sh


# ---------------------------------------------------------------- ③ 容器里的路径更深/更浅


def test_schema_resolution_survives_the_container_path_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★★ 回归：**容器里的 `__file__` 比本机浅得多**，路径推断不能因此抛异常。

    真故障（2026-10-01，Render 第一次部署）：
      容器里 `__file__` = `/app/app/cli.py` ⇒ `parents` 只有 **3** 项 ⇒
      老代码的 `Path(__file__).resolve().parents[3]` 抛 **`IndexError`**，
      而本机是 8 层 ⇒ **本机永远复现不出来**。

    ★ 更隐蔽的是**"饿着构造候选列表"**：越界发生在逐个 `is_file()` 试探**之前**，
      所以连"退回到别的候选"这条兜底路都走不到 —— 它不是"找不到文件"，
      是**"连找的动作都没做成"**。

    ⇒ 判据：**按深度索引的路径推断必须容忍"路径比预期浅"**。
      （Windows 上 `Path("/app/app/cli.py").resolve()` → `C:\\app\\app\\cli.py`，
       父目录同样只有 3 级 ⇒ 这条用例在**本机**就能复现容器的条件。）
    """
    from app import cli

    monkeypatch.setattr(cli, "__file__", "/app/app/cli.py")
    assert len(Path("/app/app/cli.py").resolve().parents) == 3, "前提变了：这条用例要重看"

    schema = cli._resolve_schema_file()  # 不能抛
    seed = cli._resolve_seed_file()  # 不能抛
    assert schema is None or schema.is_file()
    assert seed is None or seed.is_file()


def _fake_conn(users: object):
    """最小 asyncpg 连接替身：只回答 `to_regclass('public.users')` 那一次查询。"""

    class _Conn:
        async def fetchval(self, sql: str, *args: object) -> object:
            assert "to_regclass" in sql, sql
            return users

        async def close(self) -> None:
            return None

    async def _make() -> _Conn:
        return _Conn()

    return _make


def test_init_db_is_loud_when_the_schema_is_missing_and_the_db_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """镜像里没有 schema、**库里也没有表** ⇒ 必须**明确失败**（返回 1）。

    ★ 这是"兜底要出声"的部署形态：老写法在这里 `return 0` 跳过 ⇒
      一个**没有任何表**的 API 会正常启动，`/health` 还返 200（degraded），
      而每个真实请求都 500 —— 最难查的一种"活着但没用"。
    """
    import asyncio

    from app import cli

    monkeypatch.setattr(cli, "_resolve_schema_file", lambda: None)
    monkeypatch.setattr(cli, "raw_asyncpg_connection", _fake_conn(None))
    assert asyncio.run(cli._init_db()) == 1


def test_init_db_quietly_skips_when_the_schema_is_missing_but_the_db_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**成对验的另一头**：镜像里没有 schema、但库里已有表 ⇒ 跳过（返回 0）。

    ★ 只测"空库要失败"是不够的：一条**恒返 1** 的实现在那条用例下也是绿的，
      而它会让托管部署**永远起不来**（schema 本来就该由 apply-schema 提前灌好）。
      两头都有场景，才说明这条分支真的在**区分**两种情形。
    """
    import asyncio

    from app import cli

    monkeypatch.setattr(cli, "_resolve_schema_file", lambda: None)
    monkeypatch.setattr(cli, "raw_asyncpg_connection", _fake_conn("users"))
    assert asyncio.run(cli._init_db()) == 0
