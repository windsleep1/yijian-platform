"""统计看板缓存层 `stats_cache` 的契约（S1-b 硬要求 ①）。

## 这里验的是"缓存真的在工作"，不是覆盖率

用户对 S1-b 的第三条要求原文：**端到端至少覆盖一条"缓存命中 + 未命中"路径 ——
这不是覆盖率，是验证缓存真的在工作**。

所以本文件里最核心的两条是：

- `test_miss_then_hit_calls_loader_once` —— 第二次**根本没调 loader**。
  注意它断言的是 **loader 的调用次数**，不是 `meta.cached`：后者是接口自己报的，
  一个"每次都查库、然后谎报 `cached=True`"的实现也能让它变绿。
  调用次数是**独立来源**，骗不了。
- `test_cache_params_match_service_signature` —— **该进 key 的参数一个不漏**。
  漏一个参数的后果是**两种筛选共享同一条缓存 → 返回错数据且不报错**：
  界面上完全看不出来（图长得正常，数字来自别的筛选条件），review 也抓不住，
  只能靠机械对账。

进程内直测（不起 HTTP）：`stats_cache` 不依赖 FastAPI，用 `fakeredis` + 计数 loader
就能把"命中 / 未命中 / 降级"三条路径全部钉住。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import fakeredis.aioredis
import pytest

from app.api.v1 import admin_stats
from app.services import stats_cache, stats_service


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


def _fake_redis() -> Any:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _counter(box: dict[str, int], payload: dict[str, Any] | None = None) -> Callable[[], Any]:
    """造一个"会记账"的 loader。`box["calls"]` 就是它被调了几次。"""

    async def loader() -> dict[str, Any]:
        box["calls"] = box.get("calls", 0) + 1
        return (
            payload
            if payload is not None
            else {
                "cards": {"dau": {"value": 1}},
                "meta": {"data_origin": "demo"},
            }
        )

    return loader


# ---------------------------------------------------------------- 命中 / 未命中


def test_miss_then_hit_calls_loader_once() -> None:
    """★ 未命中调 loader、命中**一次都不调** —— 缓存真的在工作（用调用次数断言）。"""

    async def body() -> None:
        redis = _fake_redis()
        box: dict[str, int] = {}
        args = {"endpoint": "overview", "params": {"day": date(2026, 5, 1)}}

        first = await stats_cache.get_or_load(redis, **args, loader=_counter(box))
        second = await stats_cache.get_or_load(redis, **args, loader=_counter(box))

        assert box["calls"] == 1, f"loader 被调了 {box['calls']} 次（应恰好 1 次）—— 缓存没生效"
        assert first["meta"]["cached"] is False
        assert second["meta"]["cached"] is True
        assert second["cards"] == first["cards"], "命中返回的内容与首次不一致"
        assert second["meta"]["ttl_sec"] == stats_cache.TTL_SECONDS["overview"]
        await redis.aclose()

    run(body())


def test_different_params_do_not_share_cache() -> None:
    """不同筛选 → 不同 key（**不许共享**）。

    反过来读：如果 key 漏了 `subject_id`，第二步会命中第一步的缓存、`calls` 停在 1 ——
    这条用例就是让那种情况变红。
    """

    async def body() -> None:
        redis = _fake_redis()
        box: dict[str, int] = {}
        base = {
            "metric": "answers",
            "granularity": "day",
            "date_from": None,
            "date_to": None,
            "subject_id": None,
        }
        empty = {"series": [], "meta": {"data_origin": "demo"}}
        await stats_cache.get_or_load(
            redis, endpoint="trends", params=base, loader=_counter(box, empty)
        )
        await stats_cache.get_or_load(
            redis,
            endpoint="trends",
            params={**base, "subject_id": 2007},
            loader=_counter(box, empty),
        )
        assert box["calls"] == 2, f"换了 subject_id 却只有 {box['calls']} 次 loader —— key 漏参数"
        await redis.aclose()

    run(body())


def test_cache_key_covers_every_param() -> None:
    """逐个参数验证"改它 → key 变"。

    这是上一条的加强版：**逐个**换掉每个参数，key 都必须变。
    比"整组换一次"更能定位是哪个参数没进 key。
    """
    base = {
        "metric": "answers",
        "granularity": "day",
        "date_from": date(2026, 1, 1),
        "date_to": date(2026, 1, 31),
        "subject_id": 2007,
    }
    original = stats_cache.cache_key("trends", base)
    changes = {
        "metric": "new_users",
        "granularity": "week",
        "date_from": date(2025, 1, 1),
        "date_to": date(2025, 1, 31),
        "subject_id": 1001,
    }
    same = [
        p for p, v in changes.items() if stats_cache.cache_key("trends", {**base, p: v}) == original
    ]
    assert not same, f"这些参数改了但 key 没变（等于没进 key）：{same}"


def test_equivalent_param_forms_share_cache() -> None:
    """同义值必须同形：`date(2026,1,1)` == `"2026-01-01"`、`2007` == `2007.0`、参数顺序无关。"""
    assert stats_cache.cache_key("overview", {"day": date(2026, 1, 1)}) == stats_cache.cache_key(
        "overview", {"day": "2026-01-01"}
    )
    assert stats_cache.cache_key("t", {"subject_id": 2007}) == stats_cache.cache_key(
        "t", {"subject_id": 2007.0}
    )
    assert stats_cache.cache_key("t", {"a": 1, "b": 2}) == stats_cache.cache_key(
        "t", {"b": 2, "a": 1}
    )
    # 但 2007 与 None 必须**不同**（"全站"和"某科目"是两种筛选）
    assert stats_cache.cache_key("t", {"subject_id": None}) != stats_cache.cache_key(
        "t", {"subject_id": 2007}
    )
    # `bool` 要在 `int` **之前**判掉：Python 里 `True == 1`，但 JSON 里 `true` 与 `1` 不同 ——
    # 若不特判，`True` 会被当作 `1` 折叠，两种筛选共用一条缓存。
    assert stats_cache.cache_key("t", {"flag": True}) != stats_cache.cache_key("t", {"flag": 1})


# ---------------------------------------------------------------- 对账守卫


def test_cache_params_match_service_signature() -> None:
    """★ **对账守卫**：`CACHE_PARAMS` 必须与 service 函数签名的筛选参数**完全一致**。

    这是"能机械检查的约定"的又一个例子（第一个是 SQL 的 `:param` 必须显式 `CAST`）：
    漏一个参数不会报错，只会让两种筛选共享缓存、**返回错数据**。
    所以它不是"顺手加的守卫"，而是本批防止"看板说谎"的关键一道闸。
    """
    problems: list[str] = []
    for endpoint, declared in admin_stats.CACHE_PARAMS.items():
        fn = getattr(stats_service, endpoint)
        actual = tuple(p.name for p in inspect.signature(fn).parameters.values() if p.name != "db")
        if set(actual) != set(declared):
            problems.append(
                f"{endpoint}: service 签名有 {sorted(actual)}，CACHE_PARAMS 声明 {sorted(declared)}"
            )
        if len(actual) != len(declared):
            problems.append(f"{endpoint}: 数量不一致（有重名或漏写）")
    assert not problems, (
        "缓存 key 的参数与 service 签名不一致（漏参数 = 两种筛选共享缓存）：\n"
        + "\n".join(problems)
    )

    # 反向两条：端点集合要对齐；多声明的参数在 locals() 里取不到 → 静默变 None，也是坑
    assert set(admin_stats.CACHE_PARAMS) == set(stats_cache.TTL_SECONDS), (
        "CACHE_PARAMS 与 TTL_SECONDS 的端点集合不一致"
    )


def test_ttl_is_graded() -> None:
    """每个端点都有 TTL，且 TTL **真的分级**（不是所有端点同一个数）。"""
    assert set(admin_stats.CACHE_PARAMS) <= set(stats_cache.TTL_SECONDS)
    assert len(set(stats_cache.TTL_SECONDS.values())) > 1, "TTL 没有分级"
    assert min(stats_cache.TTL_SECONDS.values()) >= 30, "TTL 太短会让缓存形同虚设"
    assert max(stats_cache.TTL_SECONDS.values()) <= 24 * 3600, (
        "TTL 超过一天会让看板长期显示陈旧数据"
    )


def test_all_five_endpoints_are_cacheable() -> None:
    """5 个业务端点**全部**在缓存表里 —— 新增端点忘了登记会在这里红。"""
    assert set(admin_stats.CACHE_PARAMS) == {
        "overview",
        "trends",
        "distributions",
        "funnel",
        "weak_points",
    }


# ---------------------------------------------------------------- 降级


def test_redis_down_degrades_to_direct_call() -> None:
    """★ Redis 挂了 → **直连**，不抛错、不返回 500，也**不许谎报 `cached=True`**。"""

    class _Broken:
        async def get(self, *_a: Any, **_k: Any) -> Any:
            raise ConnectionError("redis down (stub)")

        async def set(self, *_a: Any, **_k: Any) -> Any:
            raise ConnectionError("redis down (stub)")

    async def body() -> None:
        box: dict[str, int] = {}
        first = await stats_cache.get_or_load(
            _Broken(), endpoint="overview", params={"day": None}, loader=_counter(box)
        )
        second = await stats_cache.get_or_load(
            _Broken(), endpoint="overview", params={"day": None}, loader=_counter(box)
        )
        assert box["calls"] == 2, "降级后应当每次都直连"
        assert first["meta"]["cached"] is False and second["meta"]["cached"] is False

    run(body())


def test_none_redis_is_allowed() -> None:
    """`redis=None`（未注入 / 未配置）也走降级路径，不炸。"""

    async def body() -> None:
        box: dict[str, int] = {}
        r = await stats_cache.get_or_load(
            None, endpoint="overview", params={"day": None}, loader=_counter(box)
        )
        assert box["calls"] == 1 and r["meta"]["cached"] is False

    run(body())


def test_write_failure_does_not_break_response() -> None:
    """**写缓存失败**不该影响本次响应（值已经算出来了）。"""

    class _ReadOkWriteBroken:
        async def get(self, *_a: Any, **_k: Any) -> Any:
            return None

        async def set(self, *_a: Any, **_k: Any) -> Any:
            raise ConnectionError("set failed (stub)")

    async def body() -> None:
        box: dict[str, int] = {}
        r = await stats_cache.get_or_load(
            _ReadOkWriteBroken(), endpoint="overview", params={"day": None}, loader=_counter(box)
        )
        assert r["meta"]["cached"] is False and "cards" in r

    run(body())


def test_corrupt_cache_is_treated_as_miss() -> None:
    """缓存里是坏数据（旧结构 / 被手改）→ 当未命中，**不许把接口打崩**。"""

    async def body() -> None:
        redis = _fake_redis()
        await redis.set(stats_cache.cache_key("overview", {"day": None}), "{ not json")
        box: dict[str, int] = {}
        r = await stats_cache.get_or_load(
            redis, endpoint="overview", params={"day": None}, loader=_counter(box)
        )
        assert box["calls"] == 1 and r["meta"]["cached"] is False
        await redis.aclose()

    run(body())


def test_cached_payload_is_not_polluted_by_hit() -> None:
    """命中时改 `meta.cached` **不能污染缓存本身**。

    防的是"把带 `cached: True` 的副本写回缓存" —— 那样缓存里的内容就与"真实计算结果"
    不再一致了（这一次看不出来，但下一次读到的是被改写过的对象）。

    ⚠️ **两个时点都要查**（这条是变异验证逼出来的）：第一版只在**第一次（未命中）之后**
    读了一次缓存 —— 那时缓存里当然是 `False`，于是"命中路径把自己写回去"这个变异
    **存活了**（14 个变异里唯一一个存活）。
    ⇒ 只验一个时点 = 验的是"性质"而不是"值"，抓不住另一半路径。
    """

    async def body() -> None:
        redis = _fake_redis()
        key = stats_cache.cache_key("overview", {"day": None})

        # 时点 1：未命中 → 写入
        await stats_cache.get_or_load(
            redis, endpoint="overview", params={"day": None}, loader=_counter({})
        )
        stored = json.loads(await redis.get(key))
        assert stored["meta"]["cached"] is False, "首次写入就带上了 cached=True"

        # 时点 2：**命中**（返回给调用方的会是 cached=True）→ 缓存里**仍必须是 False**
        hit = await stats_cache.get_or_load(
            redis, endpoint="overview", params={"day": None}, loader=_counter({})
        )
        assert hit["meta"]["cached"] is True, "第二次请求没命中（说明缓存没写进去）"
        after_hit = json.loads(await redis.get(key))
        assert after_hit["meta"]["cached"] is False, (
            "命中路径把 cached=True 写回了缓存 —— 缓存内容从此与真实计算结果不一致"
        )
        await redis.aclose()

    run(body())


def test_loader_return_value_is_not_mutated() -> None:
    """缓存层不该**原地改** service 的返回值（否则 service 的对象会被悄悄改掉）。"""

    async def body() -> None:
        redis = _fake_redis()
        original = {"cards": {}, "meta": {"data_origin": "demo"}}
        await stats_cache.get_or_load(
            redis, endpoint="overview", params={"day": None}, loader=_counter({}, original)
        )
        assert "cached" not in original["meta"], "service 的返回值被就地改了"
        await redis.aclose()

    run(body())


# ---------------------------------------------------------------- 清理


def test_invalidate_by_endpoint() -> None:
    """按端点清缓存：清 A 不动 B。"""

    async def body() -> None:
        redis = _fake_redis()
        await stats_cache.get_or_load(
            redis, endpoint="overview", params={"day": None}, loader=_counter({})
        )
        await stats_cache.get_or_load(
            redis, endpoint="trends", params={"metric": "answers"}, loader=_counter({})
        )
        assert await stats_cache.invalidate(redis, "overview") == 1

        box: dict[str, int] = {}
        await stats_cache.get_or_load(
            redis, endpoint="trends", params={"metric": "answers"}, loader=_counter(box)
        )
        # `box` 里**没有** `calls` 才是对的：loader 一次都没被调 = trends 命中了缓存。
        # （第一版这里写成 box["calls"] == 0，直接 KeyError —— 断言写糙了一样是缺陷。）
        assert box.get("calls", 0) == 0, "清了 overview 却把 trends 也清了"
        await stats_cache.get_or_load(
            redis, endpoint="overview", params={"day": None}, loader=_counter(box)
        )
        assert box["calls"] == 1, "overview 被清了却没重算"
        await redis.aclose()

    run(body())


def test_invalidate_all() -> None:
    """不带端点 → 清全部 stats 缓存。"""

    async def body() -> None:
        redis = _fake_redis()
        for ep, params in (("overview", {"day": None}), ("funnel", {"cohort": "all"})):
            await stats_cache.get_or_load(redis, endpoint=ep, params=params, loader=_counter({}))
        assert await stats_cache.invalidate(redis) == 2
        await redis.aclose()

    run(body())


def test_invalidate_tolerates_redis_failure() -> None:
    """清理失败只记日志、返回 0（运维动作不该把调用方打崩）。"""

    class _Broken:
        def scan_iter(self, *_a: Any, **_k: Any) -> Any:
            async def _gen() -> Any:
                raise ConnectionError("stub")
                yield  # pragma: no cover —— 不可达；写它只为让 _gen 成为 async generator

            return _gen()

    assert run(stats_cache.invalidate(_Broken())) == 0
    # 连 redis 都没有（未配置 / 未注入）→ 也是 0，不该炸
    assert run(stats_cache.invalidate(None)) == 0


def test_unknown_endpoint_fails_fast() -> None:
    """未登记的端点 → `KeyError`（**早失败**好过安静地不缓存）。"""

    async def body() -> None:
        with pytest.raises(KeyError):
            await stats_cache.get_or_load(
                _fake_redis(), endpoint="nope", params={}, loader=_counter({})
            )

    run(body())
