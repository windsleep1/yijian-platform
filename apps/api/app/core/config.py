"""
应用配置。

所有配置项从环境变量（或 .env）读取，字段名大小写不敏感：
    APP_ENV      -> app_env
    DATABASE_URL -> database_url

生产环境校验：APP_ENV=prod 时禁止使用默认密钥。
"""

from __future__ import annotations

from functools import lru_cache

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


def normalize_database_url(raw: str) -> tuple[str, bool]:
    """把托管商（Neon / Supabase / Render …）复制的连接串归一成能直接用的形式。

    返回 `(归一后的 URL, 是否要求 SSL)`。

    ★ 两处**只有在这一步才修得好**的坑，都不该让部署的人去背：

      ① **方言前缀**：托管商给的是 `postgresql://…`。SQLAlchemy 认它，但会去加载
         **psycopg2**，而本项目跑的是 asyncpg ⇒ 现场报 `No module named 'psycopg2'`。
         （这个坑本项目在 `tests/test_stats_service.py` 的注释里已经踩过一次。）

      ② **`sslmode` 是 libpq 的参数，asyncpg 不认它**。它不能被塞进 URL：
         SQLAlchemy 的 asyncpg 方言会把 URL 上的查询参数**当连接参数**转发，
         于是变成"传给 `asyncpg.connect` 一个它没有的关键字"。
         ⇒ 这里的做法是**从 URL 里摘掉**，把它翻译成布尔量由调用方显式传
         `ssl=`（见 `Settings.db_ssl_require` 与 `db/base.py::_asyncpg_connect_args`）。
         ⚠️ **为什么不就地改写成 `ssl=require`**：`ssl` 在 **DSN 字符串**里认不认，
         取决于 asyncpg 的 DSN 解析器（而不是 `connect()` 的关键字）—— 那是一件
         **本机没有 Neon 就验证不了的事**。摘掉 + 显式传参**两种假设下都对**，
         所以选它（判据：不赌"某个我验不了的默认行为"）。

    ⚠️ 只动"连接参数"这一层，**不动** user / password / host / dbname
    （密码里可能有 `?` `&` `@`，所以只对**第一个 `?` 之后**做处理）。
    """
    s = (raw or "").strip()
    if not s:
        return s, False

    for prefix in _SCHEME_PREFIXES:
        if s.startswith(prefix):
            s = "postgresql+asyncpg://" + s[len(prefix) :]
            break

    base, sep, query = s.partition("?")
    if not sep:
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

    return base + ("?" + "&".join(kept) if kept else ""), wants_ssl


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
