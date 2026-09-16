"""
短信验证码服务。

生产：对接阿里云/腾讯云（Batch 5 补真实 SDK 调用）
开发：SMS_PROVIDER=mock，验证码写 Redis 并在响应里回显，保证 docker compose 起来就能联调

风控（Redis 计数）：
    sms:cd:{phone}                 60 秒内只允许发一次
    sms:cnt:phone:{phone}:{date}   单手机号每日上限
    sms:cnt:ip:{ip}:{date}         单 IP 每日上限
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.errors import BizError, bad_request, too_many, unavailable

logger = logging.getLogger("app.sms")

CODE_KEY = "sms:code:{scene}:{phone}"
COOLDOWN_KEY = "sms:cd:{phone}"
PHONE_DAILY_KEY = "sms:cnt:phone:{phone}:{date}"
IP_DAILY_KEY = "sms:cnt:ip:{ip}:{date}"

DAILY_KEY_TTL = 25 * 3600          # 覆盖当日 + 跨时区余量
CN_TZ = timezone(timedelta(hours=8))


def _today() -> str:
    return datetime.now(CN_TZ).strftime("%Y%m%d")


def generate_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


async def _incr_daily(redis: aioredis.Redis, key: str, limit: int) -> None:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, DAILY_KEY_TTL)
    if count > limit:
        raise too_many("今日验证码发送次数已达上限，请明天再试", 42902)


async def send_code(
    redis: aioredis.Redis, *, phone: str, scene: str, ip: str | None
) -> tuple[int, str | None]:
    """
    发送验证码。返回 (有效期秒数, dev_code)。
    dev_code 仅在 SMS_PROVIDER=mock 且非生产环境有值，便于本地联调。

    验证码必须落 Redis，无法降级；Redis 不可用时返回 503 而不是 500。
    """
    try:
        return await _send_code(redis, phone=phone, scene=scene, ip=ip)
    except BizError:
        raise  # 限流等业务异常原样抛出
    except Exception as exc:  # noqa: BLE001
        logger.error("短信服务不可用（Redis 异常）: %s", exc)
        raise unavailable("短信服务暂时不可用，请稍后重试") from exc


async def _send_code(
    redis: aioredis.Redis, *, phone: str, scene: str, ip: str | None
) -> tuple[int, str | None]:
    cooldown_key = COOLDOWN_KEY.format(phone=phone)
    if not await redis.set(cooldown_key, "1", ex=settings.sms_interval_seconds, nx=True):
        ttl = await redis.ttl(cooldown_key)
        raise too_many(f"发送过于频繁，请 {max(ttl, 1)} 秒后再试", 42902)

    await _incr_daily(
        redis,
        PHONE_DAILY_KEY.format(phone=phone, date=_today()),
        settings.sms_daily_limit_per_phone,
    )
    if ip:
        await _incr_daily(
            redis,
            IP_DAILY_KEY.format(ip=ip, date=_today()),
            settings.sms_daily_limit_per_ip,
        )

    code = generate_code()
    await redis.set(
        CODE_KEY.format(scene=scene, phone=phone), code, ex=settings.sms_code_ttl_seconds
    )

    dev_code: str | None = None
    if settings.sms_provider == "mock":
        logger.info("[MOCK SMS] phone=%s scene=%s code=%s", phone, scene, code)
        if not settings.is_prod:
            dev_code = code
    else:
        await _dispatch_real_sms(phone=phone, code=code, scene=scene)

    return settings.sms_code_ttl_seconds, dev_code


async def _dispatch_real_sms(*, phone: str, code: str, scene: str) -> None:
    """真实短信通道。Batch 5 接入阿里云/腾讯云 SDK。"""
    raise NotImplementedError(
        f"SMS_PROVIDER={settings.sms_provider} 尚未接入，请先使用 SMS_PROVIDER=mock"
    )


async def verify_code(redis: aioredis.Redis, *, phone: str, scene: str, code: str) -> None:
    """校验验证码。失败抛 BizError；成功后立即删除（一次性消费）。"""
    key = CODE_KEY.format(scene=scene, phone=phone)
    try:
        expected = await redis.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.error("验证码校验不可用（Redis 异常）: %s", exc)
        raise unavailable("验证码服务暂时不可用，请稍后重试") from exc
    if not expected:
        raise bad_request("验证码已过期，请重新获取", 40010)
    if expected != (code or "").strip():
        raise bad_request("验证码不正确", 40011)
    await redis.delete(key)
