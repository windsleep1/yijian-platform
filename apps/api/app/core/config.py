"""
应用配置。

所有配置项从环境变量（或 .env）读取，字段名大小写不敏感：
    APP_ENV      -> app_env
    DATABASE_URL -> database_url

生产环境校验：APP_ENV=prod 时禁止使用默认密钥。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-change-me-please-32chars-min"


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
