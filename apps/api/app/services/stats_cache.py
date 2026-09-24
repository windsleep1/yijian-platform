"""统计看板的缓存层：TTL 分级 + key 规范化 + Redis 不可用时降级直连。

## 为什么要有这一层（而不是在路由里直接 `redis.get` / `redis.set`）

**① key 必须含"筛选参数全集"。** 手写 key（`f"stats:trends:{metric}"`）只要漏一个参数，
两种不同的筛选就会共享同一条缓存 —— **返回错的数据，而且不会报错**。
那是看板最危险的一类缺陷：用户看到一张"长得完全正常"的图，数字却是别的筛选条件下的。
所以 key 由**参数**算出来（`cache_key`），并由 `admin_stats.CACHE_PARAMS` 对账表
保证"该进 key 的一个不漏"（`test_stats_cache.py` 里有一条用例专门盯它）。

**② TTL 要分级。** `overview` 是当日实时口径、`funnel` 是小时级累积 —— 一个统一 TTL
会让慢变的图缓存太短（白算）、快变的图缓存太长（看到陈旧数字）。

**③ Redis 是加速器，不是单点。** 任何 redis 异常都降级直连（**读和写都降级**），
与 `core.deps.rate_limit` 的处理保持一致 —— 缓存坏掉不该让看板打不开。

## `meta.cached` 由这一层**唯一**决定

service 层不设置它。未命中 → `False`（这次真的算了）；命中 → `True`（这次是缓存里的）。
前端的"数据截至 xx:xx（缓存）"就靠它。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("app.stats.cache")

#: 缓存 key 前缀。与 `rbac:priv:` 并列，便于在 Redis 里按前缀查看 / 清理。
KEY_PREFIX = "stats"

#: TTL 分级（秒）—— **按"数据变化速度"分**，不是按端点个数分。
#:
#: | 端点 | TTL | 依据 |
#: |---|---|---|
#: | `overview` | 60 | 当日实时口径，也是打开看板第一眼看到的东西 |
#: | `trends` | 300 | 按天聚合；5 分钟内的变化只可能来自"刚刚答题"，不必更实时 |
#: | `weak_points` | 600 | 按知识点聚合，样本要攒够才有意义（`min_sample` 默认 20） |
#: | `distributions` | 3600 | `bank` 视图（真数据）几乎不变；`practice` 视图以天为单位变 |
#: | `funnel` | 3600 | 用户级累积漏斗，分钟级刷新没有意义 |
#:
#: ⚠️ **一处已知取舍**：`distributions` 的两个视图变化速度并不相同（`bank` 更慢），
#: 这里**故意用同一个 TTL**。要按 `view` 再分级，就得让 TTL 也依赖参数 ——
#: 本批不做，等 S2 上线后有实际反馈再改（见 `docs/20` §10 风险表）。
TTL_SECONDS: dict[str, int] = {
    "overview": 60,
    "trends": 300,
    "weak_points": 600,
    "distributions": 3600,
    "funnel": 3600,
}


def _norm(value: Any) -> Any:
    """把参数规范化成"同义值同形"，避免同一组筛选算出两个 key。

    - `date` / `datetime` → ISO 字符串（`date(2026, 1, 1)` 与 `"2026-01-01"` 同形）
    - `None` 与缺省同形（都落成 `None`）
    - 整数型 `float` → `int`（`2001` 与 `2001.0` 同形，避免调用方传 float 时白多一份缓存）
    """
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def cache_key(endpoint: str, params: Mapping[str, Any]) -> str:
    """`stats:<endpoint>:<sha1(参数全集)>`。

    ★ **参数全集**是硬要求：`params` 的**键集合**必须等于该端点 service 函数签名里的
    全部筛选参数 —— 由 `admin_stats.CACHE_PARAMS` 声明、由测试对账。
    漏一个参数不会报错，只会让两种筛选共享缓存（**返回错数据**）。
    """
    canonical = json.dumps(
        {k: _norm(v) for k, v in sorted(params.items())},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{KEY_PREFIX}:{endpoint}:{digest}"


async def _get(redis: aioredis.Redis | None, key: str) -> dict[str, Any] | None:
    """读缓存。**任何异常都当未命中**（降级直连），不向上抛。"""
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception as exc:  # noqa: BLE001 —— 缓存故障不该影响可用性
        logger.warning("stats 缓存读取失败，降级直连: key=%s err=%s", key, exc)
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        # 缓存里是坏数据（例如上个版本写进去的旧结构）→ 当未命中，别让它把接口打崩。
        logger.warning("stats 缓存内容无法解析，按未命中处理: key=%s err=%s", key, exc)
        return None
    return parsed if isinstance(parsed, dict) else None


async def _set(redis: aioredis.Redis | None, key: str, payload: dict[str, Any], ttl: int) -> None:
    """写缓存。**写失败只记日志**（本次响应已经算出来了，不该因为回写失败而报错）。"""
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(payload, ensure_ascii=False, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats 缓存写入失败（不影响本次响应）: key=%s err=%s", key, exc)


def _with_meta(data: dict[str, Any], *, cached: bool, ttl: int) -> dict[str, Any]:
    """给响应补上 `meta.cached` / `meta.ttl_sec`。

    **不原地改** `data`（service 的返回值不该被缓存层改写）；浅拷贝一层，
    `meta` 单独拷贝 —— 这两个 dict 里装的是标量与列表，浅拷贝足够。
    """
    out = dict(data)
    meta = dict(out.get("meta") or {})
    meta["cached"] = cached
    meta["ttl_sec"] = ttl
    out["meta"] = meta
    return out


async def get_or_load(
    redis: aioredis.Redis | None,
    *,
    endpoint: str,
    params: Mapping[str, Any],
    loader: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """读缓存；未命中则执行 `loader` 并回写。

    `endpoint` 必须是 `TTL_SECONDS` 里的键（写错会 `KeyError` —— 早失败好过安静地不缓存）。
    """
    ttl = TTL_SECONDS[endpoint]
    key = cache_key(endpoint, params)

    hit = await _get(redis, key)
    if hit is not None:
        hit.setdefault("meta", {})["cached"] = True
        hit["meta"]["ttl_sec"] = ttl
        return hit

    payload = _with_meta(await loader(), cached=False, ttl=ttl)
    await _set(redis, key, payload, ttl)
    return payload


async def invalidate(redis: aioredis.Redis | None, endpoint: str | None = None) -> int:
    """按前缀删缓存（测试与运维用）。返回删除条数。

    用 `SCAN` 而不是 `KEYS`：`KEYS` 在大 key 空间上会**阻塞 Redis 单线程**。
    """
    if redis is None:
        return 0
    pattern = f"{KEY_PREFIX}:{endpoint}:*" if endpoint else f"{KEY_PREFIX}:*"
    removed = 0
    try:
        async for key in redis.scan_iter(match=pattern, count=200):
            removed += int(await redis.delete(key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats 缓存清理失败: pattern=%s err=%s", pattern, exc)
    return removed
