"""⑤a（进程内半）：短信服务层 + 账号状态分支 —— **HTTP 打不到的那一半**。

## 为什么必须进程内（三条硬约束，都是实测确认的）

1. **同一手机号 60 秒冷却**（`sms_service._send_code` 的 `set(..., ex=..., nx=True)`）：
   "同一手机号发两次码"在单次运行里做不到 → 「已注册用户再次登录」「锁定/注销后登录」
   这些**需要第二次发码**的场景，HTTP 覆盖不了。
2. **每日上限**（`sms_service.py:48`）：手机号维度上限 10 次 + 60 秒冷却 → 不可能；
   IP 维度上限 20 次，而**路由层本身就是 20 次/60s** → 先撞路由限流（`42901`），
   永远走不到服务层的日上限。
3. **`_dispatch_real_sms`**（`sms_service.py:100`）需要 `SMS_PROVIDER != mock` 的 **API 进程**，
   而 API 由 `run-smoke` / CI 固定用 mock 起，运行中改不了。

## 边界（用户 2026-09-22 定，照抄判据）

> 判据：这个测试会在"**重构实现但不改契约**"时红吗？会 → 测得太细；不会 → 恰恰好。
> 该测：输入→输出 / 输入→异常类型 / 输入→状态变化 / 边界条件；
> 不该测：中间变量值 / 调用几个 SQL / 私有函数名 / if 的层数。

所以本文件只用**公开服务函数**（`sms_service.send_code` / `verify_code`、
`auth_service.login_by_sms` / `get_user_by_phone`）并按上面四类断言。
连"清掉冷却"都用 `redis.flushdb()`（拿我自己持有的 redis），**不引用任何 key 常量**。

## ⚠️ 为什么这些用例"算数"

它们跑在 **pytest 进程**里。补测批 step ③ 之前，覆盖率门禁只采 API 进程 ——
这类用例在账上**等于不存在**（硬约定 L：跨进程执行 = 跨进程测量）。
step ③ 给 pytest 进程也插了桩，这一半才第一次被记上账。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import fakeredis.aioredis
import pytest
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.errors import BizError
from app.db.models import User
from app.schemas.auth import RegisterIn, SmsLoginIn, SmsSendIn
from app.services import auth_service, sms_service

from .conftest import rand_phone

_DSN = os.environ.get("DATABASE_URL")


def _require_db() -> None:
    """缺 DATABASE_URL 就**硬失败**，不 skip（硬约定 M）。

    判据「换个干净环境跑，这条 skip 应该还是 skip 吗」—— run-smoke 与 CI 都导出
    DATABASE_URL，干净环境里它本该有值；skip 只会让"这条从没跑过"变成一个绿对勾。
    """
    if not _DSN:
        pytest.fail(
            "缺少 DATABASE_URL：本文件的用例需要真 PostgreSQL（与 run-smoke / CI 一致）。\n"
            "  怎么产生：跑 tools/local-verify/run-smoke.ps1（它会导出 DATABASE_URL 并起库）；\n"
            "  或手动 export DATABASE_URL=postgresql+asyncpg://yijian@127.0.0.1:55432/yijian"
        )


class _BrokenRedis:
    """只在被**调用时**抛异常的 redis 替身 —— 模拟"Redis 不可用"，不是"值不存在"。

    两者必须区分：值不存在是 40010（业务错），服务不可用是 50003/503（依赖错）。
    """

    def __init__(self, fail_on: str) -> None:
        self._fail_on = fail_on

    async def set(self, *a: Any, **k: Any) -> Any:
        if self._fail_on == "set":
            raise ConnectionError("redis down (stub)")
        return True

    async def get(self, *a: Any, **k: Any) -> Any:
        if self._fail_on == "get":
            raise ConnectionError("redis down (stub)")
        return None

    async def delete(self, *a: Any, **k: Any) -> int:
        return 1

    async def incr(self, *a: Any, **k: Any) -> int:
        return 1

    async def expire(self, *a: Any, **k: Any) -> bool:
        return True

    async def ttl(self, *a: Any, **k: Any) -> int:
        return 60

    async def aclose(self) -> None:
        return None


@asynccontextmanager
async def _env(redis: Any = None):
    """一次用例的干净环境：NullPool 引擎 + 会话 + fakeredis。

    ⚠️ `NullPool` 是必需的：每个用例都 `asyncio.run()` 起一个**新的事件循环**，
    而带连接池的全局 engine 会把连接绑在**旧循环**上 → "attached to a different loop"。
    （踩过：`tools/local-verify/_probe_greenlet.py` 的第一版就是这么炸的。）
    """
    engine = create_async_engine(_DSN, poolclass=NullPool)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    r = redis if redis is not None else fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        async with maker() as db:
            yield db, r
    finally:
        await r.aclose()
        await engine.dispose()


async def _sms_login(db: AsyncSession, r: Any, phone: str) -> tuple[Any, User | None]:
    """走**公开服务函数**完成"发码 → 短信登录"，返回 (令牌, 该手机号对应的用户)。"""
    _, code = await sms_service.send_code(r, phone=phone, scene="login", ip=None)
    tokens = await auth_service.login_by_sms(
        db, r, SmsLoginIn(phone=phone, code=code), ip=None, user_agent=None
    )
    return tokens, await auth_service.get_user_by_phone(db, phone)


async def _set_user(db: AsyncSession, uid: int, **values: Any) -> None:
    """直接改库里的账号状态。

    ⚠️ 改完必须 `expire_all()`：会话的 identity map 里可能还持有旧对象，
    而 SQLAlchemy 默认**不会**用新查出的行覆盖身份映射里已有的属性 ——
    不 expire 的话服务层会读到"改之前"的状态，测试就变成假绿。
    """
    await db.execute(update(User).where(User.id == uid).values(**values))
    await db.commit()
    db.expire_all()


# ---------------------------------------------------------------- sms_service


def test_send_code_reports_503_when_redis_is_down() -> None:
    """Redis 写不进去 → **50003 / HTTP 503**（依赖不可用），不是 500、也不是"码不对"。"""
    _require_db()

    async def flow() -> BizError:
        async with _env(redis=_BrokenRedis("set")) as (_db, r):
            with pytest.raises(BizError) as ei:
                await sms_service.send_code(r, phone=rand_phone(), scene="login", ip=None)
            return ei.value

    e = asyncio.run(flow())
    assert e.code == 50003 and e.http_status == 503, (e.code, e.message)


def test_verify_code_reports_503_when_redis_is_down() -> None:
    """Redis 读不出来 → **50003 / HTTP 503**。

    与「验证码已过期（40010）」必须分开：一个是依赖故障（可重试），
    一个是业务结果（重试没用）。
    """
    _require_db()

    async def flow() -> BizError:
        async with _env(redis=_BrokenRedis("get")) as (_db, r):
            with pytest.raises(BizError) as ei:
                await sms_service.verify_code(r, phone=rand_phone(), scene="login", code="123456")
            return ei.value

    e = asyncio.run(flow())
    assert e.code == 50003 and e.http_status == 503, (e.code, e.message)


def test_send_code_enforces_daily_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """**日上限**（不是冷却）必须生效，且与冷却**报不同的文案**。

    ⚠️ 两条错误码都是 `42902` —— 区分它们的**只有消息**。所以这里断言的是消息含"上限"，
    否则"冷却生效"会被误当成"日上限生效"（假绿）。
    """
    _require_db()
    monkeypatch.setattr(settings, "sms_daily_limit_per_phone", 1)
    monkeypatch.setattr(settings, "sms_interval_seconds", 1)
    phone = rand_phone()

    async def flow() -> BizError:
        async with _env() as (_db, r):
            await sms_service.send_code(r, phone=phone, scene="login", ip=None)
            await asyncio.sleep(1.05)  # 让 1 秒冷却过期，但**不动**日计数
            with pytest.raises(BizError) as ei:
                await sms_service.send_code(r, phone=phone, scene="login", ip=None)
            return ei.value

    e = asyncio.run(flow())
    assert e.code == 42902, (e.code, e.message)
    assert "上限" in e.message, f"报的是冷却不是日上限：{e.message}"


def test_send_code_with_unconfigured_real_provider_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SMS_PROVIDER` 配成真实服务商（尚未接入）→ **50003 / 503**，不是崩成 500。

    这条覆盖 `_dispatch_real_sms`：它 `raise NotImplementedError`，
    必须被 `send_code` 兜成"依赖不可用"，而不是漏成未捕获异常。
    """
    _require_db()
    monkeypatch.setattr(settings, "sms_provider", "aliyun")

    async def flow() -> BizError:
        async with _env() as (_db, r):
            with pytest.raises(BizError) as ei:
                await sms_service.send_code(r, phone=rand_phone(), scene="login", ip=None)
            return ei.value

    e = asyncio.run(flow())
    assert e.code == 50003 and e.http_status == 503, (e.code, e.message)


# ---------------------------------------------------------------- auth_service


def test_existing_phone_logs_into_the_same_account() -> None:
    """**已注册手机号再次短信登录 = 登录**，不是新建账号（`user_id` 必须不变）。

    ⚠️ 这条**不是异常路径**，别和"被锁定 / 已注销"混在一起 ——
    它是正常业务：同一个手机号第二次进来应当回到同一个账号。
    """
    _require_db()
    phone = rand_phone()

    async def flow() -> tuple[Any, int, Any, int]:
        async with _env() as (db, r):
            t1, u1 = await _sms_login(db, r, phone)
            assert u1 is not None
            await r.flushdb()  # 清掉冷却与旧码（不引用任何 key 常量）
            t2, u2 = await _sms_login(db, r, phone)
            assert u2 is not None
            return t1, u1.id, t2, u2.id

    t1, uid1, t2, uid2 = asyncio.run(flow())
    assert t1.access_token and t2.access_token
    assert uid1 == uid2, "已注册手机号再次短信登录必须回到**同一个**账号"


def test_locked_account_is_refused_on_sms_login() -> None:
    """账号 `status='locked'` → **40305**（账号已被锁定）。

    这是"验证码对了也不能进"的场景 —— 正是 HTTP 覆盖不到的那类
    （它需要同一手机号的第二次发码）。
    """
    _require_db()
    phone = rand_phone()

    async def flow() -> tuple[int, BizError]:
        async with _env() as (db, r):
            _, u = await _sms_login(db, r, phone)
            assert u is not None
            uid = u.id
            await _set_user(db, uid, status="locked")
            await r.flushdb()
            _, code = await sms_service.send_code(r, phone=phone, scene="login", ip=None)
            with pytest.raises(BizError) as ei:
                await auth_service.login_by_sms(
                    db, r, SmsLoginIn(phone=phone, code=code), ip=None, user_agent=None
                )
            return uid, ei.value

    _uid, e = asyncio.run(flow())
    assert e.code == 40305, (e.code, e.message)


def test_account_marked_deleted_is_refused_on_sms_login() -> None:
    """`status='deleted'`（但行还在）→ **40104**（账号不存在或已注销）。

    与下一条的区别：**这条是"拒绝"，下一条是"释放"** ——
    释放走 `is_deleted=true`，于是 `get_user_by_phone` 查不到 → 自动建新号。
    两种语义不能混。
    """
    _require_db()
    phone = rand_phone()

    async def flow() -> BizError:
        async with _env() as (db, r):
            _, u = await _sms_login(db, r, phone)
            assert u is not None
            await _set_user(db, u.id, status="deleted")
            await r.flushdb()
            _, code = await sms_service.send_code(r, phone=phone, scene="login", ip=None)
            with pytest.raises(BizError) as ei:
                await auth_service.login_by_sms(
                    db, r, SmsLoginIn(phone=phone, code=code), ip=None, user_agent=None
                )
            return ei.value

    e = asyncio.run(flow())
    assert e.code == 40104, (e.code, e.message)


def test_deleted_account_releases_the_phone() -> None:
    """**注销（`is_deleted=true`）后手机号被释放**：再次短信登录会**建一个新号**。

    语义（用户 2026-09-22 裁定）：**现在就释放** —— 还没到支付/试用阶段，"防白嫖"不存在；
    释放符合用户预期、也符合合规精神，而且代码更简单（`get_user_by_phone` 只看
    `is_deleted=false`，不用给"幽灵手机号"特判）。见 `docs/04` §2.1 的待办。

    ⚠️ 这条同时是一个**约束检查**：如果 `users.phone` 上有**整表**唯一索引，
    新号的 INSERT 会撞唯一键 —— 那"释放"就是做不到的，必须立刻暴露。
    """
    _require_db()
    phone = rand_phone()

    async def flow() -> tuple[int, int]:
        async with _env() as (db, r):
            _, u1 = await _sms_login(db, r, phone)
            assert u1 is not None
            uid1 = u1.id
            await _set_user(db, uid1, is_deleted=True, status="deleted")
            await r.flushdb()
            _, u2 = await _sms_login(db, r, phone)
            assert u2 is not None
            return uid1, u2.id

    uid1, uid2 = asyncio.run(flow())
    assert uid1 != uid2, "注销后的手机号应当释放（建新号），而不是复用到旧号"


# ---------------------------------------------------------------- schemas


def test_auth_schemas_reject_bad_phone_and_weak_password() -> None:
    """入参校验：手机号格式、验证码长度、密码强度 —— 三条都是**拒绝**契约。

    每条都断言**报错原因**（而不只是"抛了 ValidationError"）：
    否则"字段名写错"之类的失败也会被当成通过。
    """
    with pytest.raises(ValidationError) as e1:
        SmsSendIn(phone="12345", scene="login")
    assert "手机号" in str(e1.value), e1.value

    with pytest.raises(ValidationError) as e2:
        SmsLoginIn(phone="13800000000", code="1")
    assert "code" in str(e2.value).lower(), e2.value

    with pytest.raises(ValidationError) as e3:
        RegisterIn(phone="13800000000", code="1234", password="abcdefgh")
    assert "密码" in str(e3.value), e3.value

    # 合法输入必须**通过** —— 否则上面三条可能只是"模型本来就坏"
    assert SmsSendIn(phone="13800000000", scene="login").phone == "13800000000"
    assert SmsLoginIn(phone="13800000000", code="1234").code == "1234"
