"""应用配置读取（`app_configs` 表）。

## 为什么单独一个文件

单写一个 `_get_config()` 塞进第一个用到它的 service 里，是很容易做出的选择 ——
然后第二个模块需要读配置时，就会**再抄一份**，两份逐渐漂移（判断默认值、
判断类型转换、判断空值各有各的写法）。

配置是**跨模块**的（exam / practice / security / learning 都在用这张表），
所以它该有一个自己的落点。这里只做"读"，写入留给将来的配置管理后台。

## 为什么不做进程内缓存

一次 `SELECT ... WHERE config_key = :k` 走的是 `uq_app_configs_key` 唯一索引，
代价可以忽略；而这些配置只出现在**写操作的守卫**里（不是每题一查的热路径）。
加缓存换来的收益很小，却引入"改了配置多久生效"的新问题 ——
运维改完阈值发现不生效，是最没必要的排障。

真到了热路径要用的那天再加缓存，**并且一定要带 TTL 与显式失效**
（参照权限缓存 `rbac:priv:{uid}` 的做法），不要写成永久进程内缓存。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 卷面题数净减少的兜底阈值（坑 42）。运营口径，放 app_configs 便于调整。
MASS_QUESTION_LOSS_THRESHOLD_KEY = "exam.mass_question_loss_threshold"

#: 读不到配置时的兜底。10 是"手工操作几乎不会一次删掉的量级"，
#: 一旦超过它，基本就是"重建卷面"这类结构性动作，值得拦一道。
MASS_QUESTION_LOSS_THRESHOLD_DEFAULT = 10


async def get_raw(db: AsyncSession, key: str) -> Any | None:
    """读一个配置项的原始值（JSONB 已由驱动解码）。不存在返回 None。"""
    value = await db.scalar(
        text("SELECT config_value FROM app_configs WHERE config_key = :k"), {"k": key}
    )
    if value is None:
        return None
    # 不同驱动对 JSONB 的处理不一致：asyncpg 直接给 Python 对象，
    # 但走 `text()` 时偶尔会以字符串形式返回 —— 两种都接住。
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


async def get_int(db: AsyncSession, key: str, default: int) -> int:
    """读一个整数配置项。

    **任何解析不了的情况都回退到默认值，绝不抛异常。**
    理由：这个函数只用在"守卫"上 —— 配置写坏了应该让业务照常跑
    （用兜底阈值继续保护），而不是把整条写路径打断。
    """
    raw = await get_raw(db, key)
    if raw is None:
        return default
    if isinstance(raw, bool):  # bool 是 int 的子类，单独挑出来免得 True 变成 1
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            return default
    return default


async def mass_question_loss_threshold(db: AsyncSession) -> int:
    """单次写操作允许净减少的卷面题数上限。"""
    return await get_int(
        db, MASS_QUESTION_LOSS_THRESHOLD_KEY, MASS_QUESTION_LOSS_THRESHOLD_DEFAULT
    )
