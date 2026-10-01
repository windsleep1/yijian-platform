"""
应用配置。

所有配置项从环境变量（或 .env）读取，字段名大小写不敏感：
    APP_ENV      -> app_env
    DATABASE_URL -> database_url

生产环境校验：APP_ENV=prod 时禁止使用默认密钥。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote, unquote, urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-change-me-please-32chars-min"

#: 托管商连接串里**只有 libpq 认、asyncpg 不认**的参数 ⇒ 一律摘掉。
#: asyncpg 没有对应能力（通道绑定 / 目标会话属性都是 libpq 客户端侧的行为）。
_LIBPQ_ONLY_PARAMS = frozenset({"channel_binding", "target_session_attrs", "gssencmode"})

#: 能出现"托管商复制按钮"里的各种方言前缀。SQLAlchemy 认 `postgres://`，
#: 但会去找 **psycopg2** 驱动（本项目没装）⇒ 必须统一换成 `+asyncpg`。
_SCHEME_PREFIXES = (
    "postgresql+asyncpg://",
    "postgresql+psycopg2://",
    "postgresql+psycopg://",
    "postgres://",
    "postgresql://",
)

#: URL 里**不可能合法、可又完全看不出来**的字符：连接串被**折行粘贴**时留下的。
#: 一律剔除（见 `normalize_database_url` 里那段长注释）。
_INVISIBLE = str.maketrans("", "", "\r\n\t")


# ---------------------------------------------------------------- 归一：两个解析器
#
# ★★ 这一节存在的理由（2026-10-01 Render 第二次真故障，**同一个字符串被两个解析器读**）：
#
#   本项目有**两条**读同一条 `DATABASE_URL` 的路，而它们**用的不是同一个解析器**：
#
#     | 路径 | 用谁 | 谁在读 |
#     |---|---|---|
#     | `settings.dsn`（`postgresql://…`） | `urllib` 那一族 | `_wait_db` / `_init_db`（裸 asyncpg） |
#     | `settings.database_url`（`postgresql+asyncpg://…`） | SQLAlchemy 的 URL 正则 | 引擎 / 业务请求 |
#
#   两条路**是从同一个字符串派生的**（`dsn` = `database_url.replace("+asyncpg","")`），
#   所以只要两个解析器解出的**主机名一致**，就永远不会出问题 ——
#   而它们**不一致**的时候，症状是**最不像"解析问题"的那一种**：
#
#       `[cli] PostgreSQL ready`（asyncpg 解对了，连得上）
#         → 几秒后 → `socket.gaierror: [Errno -2] Name or service not known`（引擎解错了）
#
#   看起来像 DNS 抽风，其实是**同一个串被解成了两个主机**。实测能造出这种分岔的输入有两类：
#
#     ① **看不见的空白**：`urllib` 会**剔除** `\r \n \t`，SQLAlchemy 的 host 正则
#        （`[^:@/]+`）却把它们**吃进主机名**。⇒ 折行粘贴一次就中。
#        实测：`…neon.tech\n/neondb` ⇒ asyncpg 得 `…neon.tech`，SQLAlchemy 得 `…neon.tech\n`。
#     ② **userinfo 里的字面量 `@`（以及 `?` `#`）**：两族都会在**第一个** `@` 处截断，
#        于是 `…:pa@ss@host` 的主机名变成 `ss@host`（**两边一起错**，且报的是 DNS）。
#
#   ⇒ 判据（一般化，可复用）：
#     **同一个配置被两个解析器读时，就把它归一成"两边都会解成一样"的规范形式；
#       并且用一条断言证明它确实一样 —— 别靠"我记得它一样"。**
#
#   ⚠️ 边界（它**不**保证对，只保证**一致**）：密码里的字面量 `/` 会把 authority 提前截断，
#      而 URL 语法里这就是不可表示的（必须写成 `%2F`）⇒ 归一只做能做到的，不假装全能。


def _split_authority(url: str) -> tuple[str, str, str]:
    """拆成 `(前缀, authority, 余下)`。

    ★ authority **只被 `/` 终止** —— 刻意**不看** `?` / `#`：
      密码里可能有它们，而那种串一旦交给 `urlsplit` 就会在 `?` 处把 authority 截断。
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url, "", ""
    authority, slash, tail = rest.partition("/")
    return f"{scheme}://", authority, slash + tail


def _canonical_authority(authority: str) -> str:
    """把 authority 里的 userinfo 重新做百分号编码，返回规范形式。

    `quote(unquote(x))` 是**幂等**的：`pa` → `pa`，`pa%40ss` → `pa%40ss`，`pa@ss` → `pa%40ss`。
    所以它对"用户本来就写对了"的串没有任何影响，只把"字面量分隔符"变成编码形式。

    两边都**会**解码（实测）：asyncpg 解 `postgresql://u:pa%40ss@h/db` 得
    `host=h`、`password=pa@ss`；SQLAlchemy 同样。⇒ 编码后**主机名与密码两边都对**。
    """
    if "@" not in authority:
        return authority
    userinfo, _, hostport = authority.rpartition("@")
    user, colon, password = userinfo.partition(":")
    user_q = quote(unquote(user), safe="")
    if not colon:
        return f"{user_q}@{hostport}"
    return f"{user_q}:{quote(unquote(password), safe='')}@{hostport}"


def _host_by_lenient_parser(url: str) -> str:
    """`urllib` 那一族（**裸 asyncpg** 走这条）解出的主机名。

    这一族会**剔除** `\\r \\n \\t`，authority 止于 `/` `?` `#`，userinfo 取**最后一个** `@`。
    """
    return (urlsplit(url).hostname or "").lower()


def _strip_port(hostport: str) -> str:
    """去掉端口，并把 IPv6 字面量的方括号去掉。

    ⚠️ 方括号**必须去** —— `urlsplit(...).hostname` 与 SQLAlchemy 的 `make_url(...).host`
    都给 `::1`（不带括号）。这里如果保留 `[::1]`，两条**本来一致**的串会被误判成
    "分岔"（`tests/test_deploy_readiness.py` 里那条 IPv6 用例当场抓到了这个误报）。
    ⇒ 判据：**校验器的模型必须对着真解析器校准**，否则它自己就是个新的"错误信念"。
    """
    if hostport.startswith("["):
        return hostport.partition("]")[0][1:]
    return hostport.partition(":")[0]


def _host_by_strict_parser(url: str) -> str:
    """**SQLAlchemy 的 URL 正则**那一族解出的主机名。

    这一族：authority 止于 `/`（**不认** `?` `#`）、userinfo 止于**第一个** `@`、
    且**不剔除**任何空白。上面那两类分岔正是靠这三条差异造出来的。
    """
    authority = url.partition("://")[2].partition("/")[0]
    hostport = authority.partition("@")[2] if "@" in authority else authority
    return _strip_port(hostport).lower()


def assert_url_parses_the_same(url: str) -> None:
    """★ 物理约束：同一个连接串**不许被两个解析器解出两个主机名**。

    写在归一化函数的**出口**上 —— 保证"发出去的串"永远是安全的，
    而不是"记得把它写安全"。（同族：把判据写成脚本，而不是写成注意事项。）

    ⚠️ 它**只**保证两族一致，**不**保证解出来是对的：两边一起错（如密码里的字面量 `/`）
      它不会响。别把这条约束当成"连接串正确性"的证明。
    """
    lenient = _host_by_lenient_parser(url)
    strict = _host_by_strict_parser(url)
    if lenient != strict:
        raise ValueError(
            "DATABASE_URL 会被两个解析器解出**两个不同的主机名**，而两条连接路径各用一个：\n"
            f"  宽松解析（裸 asyncpg / wait-db）: {lenient!r}\n"
            f"  严格解析（SQLAlchemy 引擎）    : {strict!r}\n"
            "这类串的症状是「wait-db 说 ready，几秒后引擎报 "
            "socket.gaierror: Name or service not known」，很容易被误判成 DNS 故障。\n"
            "修法：把连接串里的**不可见空白（换行/制表/回车）**去掉，"
            "并把密码里的 `@` `?` `#` `%` 写成 `%40` `%3F` `%23` `%25`。"
        )


def normalize_database_url(raw: str) -> tuple[str, bool]:
    """把托管商（Neon / Supabase / Render …）复制的连接串归一成能直接用的形式。

    返回 `(归一后的 URL, 是否要求 SSL)`。**归一后的串保证"两个解析器解出同一个主机名"**
    （见本节开头那段，以及出口处的 `assert_url_parses_the_same`）。

    三处**只有在这一步才修得好**的坑，都不该让部署的人去背：

      ⓿ **不可见的空白**：连接串太长，粘贴时折行就会在 authority 里留下 `\\n`。
         `urllib` 剔除它、SQLAlchemy 的 host 正则不剔除 ⇒
         **同一串解出 `…neon.tech` 与 `…neon.tech\\n` 两个主机**，后者解析失败。
         症状是「`wait-db` 说 ready、几秒后引擎报 `Name or service not known`」——
         看着像 DNS 抽风，其实是**解析分岔**（2026-10-01 真故障，见本节开头）。

      ① **方言前缀**：托管商给的是 `postgresql://…`。SQLAlchemy 认它，但会去加载
         **psycopg2**，而本项目跑的是 asyncpg ⇒ 现场报 `No module named 'psycopg2'`。
         （这个坑本项目在 `tests/test_stats_service.py` 的注释里已经踩过一次。）

      ② **`sslmode` 是 libpq 的参数，asyncpg 不认它**。它不能被塞进 URL：
         SQLAlchemy 的 asyncpg 方言会把 URL 上的查询参数**当连接参数**转发，
         于是变成"传给 `asyncpg.connect` 一个它没有的关键字"。
         ⇒ 这里的做法是**从 URL 里摘掉**，把它翻译成布尔量由调用方显式传
         `ssl=`（见 `Settings.db_ssl_require` 与 `db/base.py::asyncpg_connect_args`）。
         ⚠️ **为什么不就地改写成 `ssl=require`**：`ssl` 在 **DSN 字符串**里认不认，
         取决于 asyncpg 的 DSN 解析器（而不是 `connect()` 的关键字）—— 那是一件
         **本机没有 Neon 就验证不了的事**。摘掉 + 显式传参**两种假设下都对**，
         所以选它（判据：不赌"某个我验不了的默认行为"）。

    ⚠️ 只动"连接参数"与"userinfo 的编码形式"，**不动** host / port / dbname
    （密码里的 `?` `&` `@` 由 `_canonical_authority` 编码掉，所以查询串只需切**第一个** `?`）。
    """
    # ⓿ 先剔不可见空白：**必须在判断 scheme 之前**（否则行首的 `\n` 会让前缀匹配失败），
    #    也必须在 `partition("?")` 之前（否则空白会跟着主机名一路走下去）。
    s = (raw or "").translate(_INVISIBLE).strip()
    if not s:
        return s, False

    for prefix in _SCHEME_PREFIXES:
        if s.startswith(prefix):
            s = "postgresql+asyncpg://" + s[len(prefix) :]
            break

    # ① userinfo 编码化：把密码里的字面量 `@` `?` `#` 变成 `%40` `%3F` `%23`。
    #    **必须早于摘查询串** —— 否则密码里的 `?` 会被当成查询起点，把主机名切进"参数"里。
    head, authority, rest = _split_authority(s)
    if authority:
        s = head + _canonical_authority(authority) + rest

    base, sep, query = s.partition("?")
    if not sep:
        assert_url_parses_the_same(base)
        return base, False

    wants_ssl = False
    kept: list[str] = []
    for pair in query.split("&"):
        pair = pair.strip()
        if not pair:
            continue
        key, _, val = pair.partition("=")
        k = key.strip().lower()
        if k == "sslmode":
            # `disable` / `allow` 是**显式不要**加密；其余（require / verify-ca / verify-full）都要。
            if val.strip().lower() not in {"", "disable", "allow"}:
                wants_ssl = True
            continue
        if k in _LIBPQ_ONLY_PARAMS:
            continue
        kept.append(pair)

    out = base + ("?" + "&".join(kept) if kept else "")
    assert_url_parses_the_same(out)
    return out, wants_ssl


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "一建通"
    app_version: str = "0.2.0"
    app_env: str = "local"  # local | dev | staging | prod
    app_debug: bool = True
    app_port: int = 8000
    app_timezone: str = "Asia/Shanghai"
    api_prefix: str = "/api/v1"

    # ---------------- 数据库 ----------------
    database_url: str = "postgresql+asyncpg://yijian:yijian@localhost:5432/yijian"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_echo: bool = False
    #: 是否要求 SSL/TLS 连数据库。**一般不用手填**：`DATABASE_URL` 里带
    #: `sslmode=require` 时会由 `_normalize_database` 自动置 True
    #: （托管 PG 如 Neon / Supabase 的连接串默认就带）。
    #: 想强制开启（例如托管商没给 sslmode）就设 `DB_SSL_REQUIRE=true`。
    db_ssl_require: bool = False
    schema_file: str = "/db/schema.sql"

    # ---------------- Redis ----------------
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 50

    # ---------------- JWT ----------------
    jwt_secret: str = DEV_SECRET
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "yijian"
    access_token_ttl_minutes: int = 120
    refresh_token_ttl_days: int = 30

    # ---------------- 短信 ----------------
    sms_provider: str = "mock"  # mock | aliyun | tencent
    sms_sign_name: str = "一建通"
    sms_code_ttl_seconds: int = 300
    sms_daily_limit_per_phone: int = 10
    sms_daily_limit_per_ip: int = 20
    sms_interval_seconds: int = 60

    # ---------------- 安全 / 限流 ----------------
    login_max_failures: int = 5
    login_lock_seconds: int = 900
    rate_limit_per_minute: int = 120
    trust_proxy_headers: bool = True

    # ---------------- 初始化 ----------------
    admin_init_phone: str = ""
    admin_init_password: str = ""
    snowflake_worker_id: int = 1

    # ---------------- 派生属性 ----------------
    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() == "prod"

    @property
    def dsn(self) -> str:
        """给 asyncpg 用的裸 DSN（去掉 SQLAlchemy 方言前缀）。"""
        return self.database_url.replace("+asyncpg", "")

    @field_validator("jwt_secret")
    @classmethod
    def _check_secret(cls, v: str, info) -> str:
        # 允许在非生产环境使用默认值，方便 docker compose 一把跑通
        env = (info.data or {}).get("app_env", "local")
        if env == "prod" and (v == DEV_SECRET or len(v) < 32):
            raise ValueError(
                "生产环境必须设置长度 >= 32 的 JWT_SECRET，不能使用默认值。"
                '生成方式：python -c "import secrets;print(secrets.token_urlsafe(48))"'
            )
        return v

    @field_validator("sms_provider")
    @classmethod
    def _check_sms(cls, v: str) -> str:
        allowed = {"mock", "aliyun", "tencent"}
        if v not in allowed:
            raise ValueError(f"SMS_PROVIDER 必须是 {allowed} 之一，当前为 {v}")
        return v

    @model_validator(mode="after")
    def _normalize_database(self) -> "Settings":
        """归一连接串，并把 `sslmode=require` 翻译成 `db_ssl_require=True`。

        ★ 用 `model_validator(mode="after")` 而**不是** `field_validator`：
          `field_validator` 只能改自己那一个字段，拿不到"原始 URL 里有没有 sslmode"
          —— 而归一之后那个信息就没了。只有在这里才**同时**看得到原值与目标字段。

        ★ 合并口径：**URL 里写了的算数**（`sslmode=require` ⇒ True，即使 `DB_SSL_REQUIRE`
          没设或设了 false）。反过来，URL 里没写时 `DB_SSL_REQUIRE` 才起作用。
          即"URL 是更具体的那一个来源"。这条要能被说出来，所以写在注释里。
        """
        url, wants_ssl = normalize_database_url(self.database_url)
        self.database_url = url
        self.db_ssl_require = self.db_ssl_require or wants_ssl
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
