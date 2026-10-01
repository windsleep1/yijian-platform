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

import pytest

from app.core.config import Settings, normalize_database_url

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
