"""
安全原语：密码哈希、JWT 签发与校验。

密码：bcrypt cost=12（约 250ms/次，足够抵抗离线爆破，又不至于拖垮登录接口）
JWT ：access token 2 小时（内存持有），refresh token 30 天（入库，可撤销）
       refresh token 走 Rotation：每次刷新作废旧 token，被盗用时可及时发现。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import unauthorized

TokenType = Literal["access", "refresh"]

BCRYPT_ROUNDS = 12
_MAX_BCRYPT_BYTES = 72          # bcrypt 只取前 72 字节，超出必须先截断否则报错


def hash_password(plain: str) -> str:
    raw = plain.encode("utf-8")[:_MAX_BCRYPT_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        raw = plain.encode("utf-8")[:_MAX_BCRYPT_BYTES]
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _encode(payload: dict[str, Any], ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    body = {
        **payload,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(body, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(*, user_id: int, session_id: int, roles: list[str]) -> tuple[str, int]:
    """返回 (token, 有效期秒数)。"""
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    token = _encode(
        {"sub": str(user_id), "typ": "access", "sid": str(session_id), "roles": roles},
        ttl,
    )
    return token, int(ttl.total_seconds())


def create_refresh_token(*, user_id: int, session_id: int) -> tuple[str, datetime]:
    ttl = timedelta(days=settings.refresh_token_ttl_days)
    token = _encode(
        {"sub": str(user_id), "typ": "refresh", "sid": str(session_id)},
        ttl,
    )
    return token, datetime.now(timezone.utc) + ttl


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("登录已过期，请重新登录", 40101) from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("登录凭证无效", 40102) from exc

    if payload.get("typ") != expected_type:
        raise unauthorized("登录凭证类型不匹配", 40102)
    return payload
