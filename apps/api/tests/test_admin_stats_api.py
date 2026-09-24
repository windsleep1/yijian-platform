"""统计看板 HTTP 层验收（Batch 8 / S1-b 的 5 个接口）。

    GET /admin/stats/overview        ① 指标卡
    GET /admin/stats/trends          ② 趋势
    GET /admin/stats/distributions   ③ 分布
    GET /admin/stats/funnel          ④ 漏斗（S3 才上前端）
    GET /admin/stats/weak-points     ⑤ 薄弱知识点

需要 API 已启动（真 PG + fakeredis）：

    powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1

## 本文件回答的是 S1-b 的三条硬要求

1. **缓存真的在工作** —— `test_cache_miss_then_hit_visible_in_meta`：
   同一个筛选请求两次，`meta.cached` 必须 `false → true`，且两次的 `cards` 一致。
2. **超时是 503 不是 500** —— `test_timeout_maps_to_503_not_500`：
   走 ASGI 直驱（本进程替换 `load_sql` 为 `pg_sleep`），验的是**整条链路**，
   包括 FastAPI 的异常处理器。
3. **真 / 造数据由接口给** —— `test_viewer_can_read_all_five_endpoints` 里断言
   `view=bank → data_origin=real`、`view=practice → demo`。S2 的页面会**同屏混真假**，
   所以真假必须是接口属性，不能靠前端猜。

## 权限矩阵（对照 `db/schema.sql` 的 RBAC 种子）

`stats:read`（permissions id=701）已授给 **super_admin(1) / researcher(3) / operator(5) / viewer(7)**；
`student(6)` **没有**任何权限 —— 正好用作 403 的对照。
"""

from __future__ import annotations

import asyncio
import random
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import httpx

from app.core import deps

from .conftest import API, BASE, assign_roles, auth, body, fresh_user


def _all_endpoints() -> list[tuple[str, dict[str, Any]]]:
    """5 个端点的最小合法请求（必填参数都给上）。"""
    return [
        ("/admin/stats/overview", {}),
        ("/admin/stats/trends", {"metric": "answers"}),
        ("/admin/stats/distributions", {"dim": "subject", "view": "bank"}),
        ("/admin/stats/funnel", {}),
        ("/admin/stats/weak-points", {}),
    ]


def _viewer(client: httpx.Client, admin_h: dict[str, str]) -> dict[str, str]:
    """注册一个全新用户 → 授 `viewer`（有 `stats:read`、但没有写权限）→ 返回其请求头。"""
    u = fresh_user(client, nickname="看板只读")
    r = assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    assert r["code"] == 0, r
    return auth(u["access_token"])


# ---------------------------------------------------------------- 权限


def test_stats_endpoints_require_auth(client: httpx.Client) -> None:
    """没带 token → **401**（不是 403）。前端据此跳登录页，而不是显示"无权限"。"""
    for path, params in _all_endpoints():
        r = client.get(f"{API}{path}", params=params)
        assert r.status_code == 401, f"{path} 无 token 应 401，实际 {r.status_code}"
        assert body(r)["code"] == 40100


def test_student_has_no_stats_permission(client: httpx.Client) -> None:
    """`student` 没有任何权限 → **403 + 40301**。

    这与 401 的区别是**语义**：401 = 没登录；403 = 登录了但没这个权限。
    前端对两者的处理完全不同（跳登录 vs 提示无权限），所以必须分开断言。
    """
    u = fresh_user(client, nickname="无权限的学员")
    h = auth(u["access_token"])
    r = client.get(f"{API}/admin/stats/overview", headers=h)
    assert r.status_code == 403, f"应 403，实际 {r.status_code}: {r.text[:160]}"
    b = body(r)
    assert b["code"] == 40301
    assert "stats:read" in b["message"], f"403 消息里应说明缺哪个权限：{b['message']}"


def test_viewer_can_read_all_five_endpoints(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """`viewer`（只读岗）能读全部 5 个端点 —— 且顺带验"真/造数据由接口给"。"""
    h = _viewer(client, admin_h)
    for path, params in _all_endpoints():
        r = client.get(f"{API}{path}", headers=h, params=params)
        assert r.status_code == 200, f"{path} 应 200，实际 {r.status_code}: {r.text[:200]}"
        b = body(r)
        assert b["code"] == 0
        meta = b["data"]["meta"]
        # 缓存层必须注入这两个字段（schema 里声明成必填 —— 绕过缓存层会在这里红）
        assert isinstance(meta["cached"], bool)
        assert meta["ttl_sec"] > 0
        assert meta["timezone"] == "Asia/Shanghai"
        assert meta["data_origin"] in ("real", "demo")


def test_distributions_data_origin_follows_view(
    client: httpx.Client, admin_h: dict[str, str]
) -> None:
    """★ `view` 决定 `data_origin`，**调用方不能传**。

    `bank`（题库结构，`questions`/`subjects`）是**真实数据**（题库已灌）；
    `practice`（作答分布，`practice_items`）是**演示数据**（C 端未落地）。
    S2 的同一屏里两者会**同时出现** —— 所以真假必须是接口属性。
    """
    h = _viewer(client, admin_h)
    for view, expected in (("bank", "real"), ("practice", "demo")):
        r = client.get(
            f"{API}/admin/stats/distributions",
            headers=h,
            params={"dim": "subject", "view": view},
        )
        assert r.status_code == 200, r.text[:200]
        meta = body(r)["data"]["meta"]
        assert meta["data_origin"] == expected, (
            f"view={view} 应 {expected}，实际 {meta['data_origin']}"
        )
        assert meta["origin_label"], "origin_label 应给界面可直接显示的文案"


# ---------------------------------------------------------------- 缓存（端到端）


def test_cache_miss_then_hit_visible_in_meta(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """★ **缓存命中 + 未命中**（S1-b 硬要求 ① 的端到端验证）。

    同一个筛选请求两次：`meta.cached` 必须 `false → true`，且两次的 `cards` 一致
    （否则"命中"命中的是别人的缓存 —— 那正是 key 漏参数的后果）。

    用**随机久远日期**做 `day`，保证这个 key 之前没被写过（fakeredis 在 API 进程里是常驻的，
    用固定日期会受"上一次跑留下的缓存"影响，断言就不可复现了）。
    """
    h = _viewer(client, admin_h)
    day = (date.today() - timedelta(days=random.randint(3000, 9000))).isoformat()
    params = {"day": day}

    first = body(client.get(f"{API}/admin/stats/overview", headers=h, params=params))
    assert first["code"] == 0, first
    assert first["data"]["meta"]["cached"] is False, "首次请求不该命中缓存"

    second = body(client.get(f"{API}/admin/stats/overview", headers=h, params=params))
    assert second["data"]["meta"]["cached"] is True, "第二次请求应当命中缓存"
    assert second["data"]["cards"] == first["data"]["cards"], "命中的内容与首次不一致"

    # 换一个参数 → 必须重新算（说明 key 真的把参数算进去了）
    other = body(
        client.get(
            f"{API}/admin/stats/overview",
            headers=h,
            params={"day": (date.today() - timedelta(days=random.randint(9100, 9900))).isoformat()},
        )
    )
    assert other["data"]["meta"]["cached"] is False, "换了 day 却命中了 —— key 没把 day 算进去"


# ---------------------------------------------------------------- 参数校验


def test_invalid_params_give_40001(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """非法入参 → **40001（400）**，而不是 500、也不是静默的空结果。

    路由层**故意不重复** `Query(ge=…)` 校验（见 `admin_stats.py` 顶部说明）：
    同一套契约只留一种错误码，前端不必同时处理 422 与 40001。
    """
    h = _viewer(client, admin_h)
    cases = [
        ("/admin/stats/trends", {"metric": "nope"}),
        ("/admin/stats/trends", {"metric": "answers", "granularity": "hour"}),
        (
            "/admin/stats/trends",
            {"metric": "answers", "date_from": "2026-02-01", "date_to": "2026-01-01"},
        ),
        ("/admin/stats/distributions", {"dim": "nope", "view": "bank"}),
        ("/admin/stats/distributions", {"dim": "subject", "view": "nope"}),
        ("/admin/stats/funnel", {"cohort": "7d"}),
        ("/admin/stats/weak-points", {"limit": 0}),
        ("/admin/stats/weak-points", {"limit": 101}),
        ("/admin/stats/weak-points", {"min_sample": 0}),
    ]
    bad: list[str] = []
    for path, params in cases:
        r = client.get(f"{API}{path}", headers=h, params=params)
        if r.status_code != 400:
            bad.append(f"{path} {params} → HTTP {r.status_code}")
            continue
        b = body(r)
        if b["code"] != 40001:
            bad.append(f"{path} {params} → code {b['code']}")
    assert not bad, "这些非法入参没有被 40001 拦下：\n" + "\n".join(bad)


# ---------------------------------------------------------------- 超时 → 503


def test_timeout_maps_to_503_not_500(client: httpx.Client) -> None:
    """★ 查询超时 → **HTTP 503**（不是 500）。

    （参数里的 `client` 只用来确保"API / DB / fakeredis 环境已就绪"这一前置成立 ——
    本用例自己走 ASGI 直驱，不经过它发请求。）

    500 与 503 对前端是**不同的语义**：500 = "这个请求有 bug"（不该重试）；
    503 = "服务暂时不可用"（可重试、可熔断）。看板在数据量大时超时是**正常现象**，
    报 500 会让前端把它当缺陷，还丢掉了重试的依据。

    怎么造出超时：把该端点的 SQL 换成 `pg_sleep(2)` 并把 `STATS_TIMEOUT_MS` 降到 200ms ——
    走的是**产品代码那条路径**（`_run` 的 `set_config` + `DBAPIError` 转换），
    不是伪造一个异常。所以它验的是整条链路，**包括 FastAPI 的异常处理器**。

    ⚠️ 必须 **ASGI 直驱**（而不是打真 API）：`load_sql` 要在**本进程**被替换。
    认证用 `dependency_overrides` 造一个超级用户 —— 这样不必依赖"两个进程的 JWT 密钥一致"
    这种脆弱前提。
    """
    from app.core import deps
    from app.core.config import settings
    from app.db import base
    from app.main import app
    from app.services import stats_service

    class _Priv:
        role_codes = ["super_admin"]
        permission_set = {"stats:read"}
        scopes: list = []

        def has(self, *_codes: str) -> bool:
            return True

        def has_all(self, *_codes: str) -> bool:
            return True

    fake = SimpleNamespace(
        user=SimpleNamespace(id=1, nickname="t", status="active"),
        privileges=_Priv(),
        session_id=None,
        jti=None,
        id=1,
    )

    import fakeredis.aioredis

    saved = (stats_service.load_sql, stats_service.STATS_TIMEOUT_MS, base._redis)

    def slow(_name: str) -> str:
        return "SELECT pg_sleep(2)"

    stats_service.load_sql = slow
    stats_service.STATS_TIMEOUT_MS = 200
    base._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.dependency_overrides[deps.current_user] = lambda: fake
    try:
        # ⚠️ `ASGITransport` 是 **async-only**（没有 `__enter__`）—— 必须配 `AsyncClient`。
        #    用同步 `httpx.Client` 会直接 `AttributeError: '__enter__'`（第一版就是这么错的）。
        async def _call() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://asgi") as c:
                return await c.get(
                    f"{settings.api_prefix}/admin/stats/overview",
                    params={"day": "2000-01-01"},
                )

        r = asyncio.run(_call())
    finally:
        app.dependency_overrides.pop(deps.current_user, None)
        stats_service.load_sql, stats_service.STATS_TIMEOUT_MS, base._redis = saved

    assert r.status_code == 503, f"超时应是 503，实际 {r.status_code}：{r.text[:200]}"
    b = r.json()
    assert b["code"] == 50004, f"超时的业务码应是 50004，实际 {b['code']}"
    assert b["message"], "超时响应要给人看得懂的话"
    # 统一信封：503 也必须带 trace_id（否则线上对不上日志）
    assert b["trace_id"] and b["server_time"]


def _declared_permissions(route: Any) -> set[str]:
    """从一条路由的依赖里挖出 `require_permission(...)` **实际声明的权限码**。

    ⚠️ 这里有个"判据写糙了会给假红"的实例（值得留着）：第一版写的是
    `"stats:read" in repr(dep)` —— 结果**每条路由都报缺权限**。
    原因：`require_permission(...)` 返回的是**闭包** `_checker`，
    它的 `repr` 里既没有参数、也没有函数名，字符串里根本找不到权限码。

    权限码在闭包的**自由变量**里（`codes` 是一个 tuple），只能从 `__closure__` 读。
    这条判据才真的能回答"这个端点声明的到底是不是 `stats:read`"——
    也才抓得住"把权限码写错成别的"这种改动。
    """
    found: set[str] = set()
    for dep in getattr(route, "dependencies", None) or []:
        fn = getattr(dep, "dependency", None)
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # 尚未绑定的 cell
                continue
            if isinstance(value, tuple):
                found |= {v for v in value if isinstance(v, str)}
    return found


def test_every_stats_route_declares_the_permission() -> None:
    """**每个** stats 路由都必须声明 `stats:read` 依赖。

    进程内结构断言（不起 HTTP），与 `CACHE_PARAMS` 对账同类：
    漏一个端点加权限，那条链路就变成**谁都能读**，而它不会让任何既有用例变红
    （既有用例只打那几个"有权限"的端点）。

    覆盖面比 E2E 广：E2E 只能证明"被请求的那几个端点"权限是对的。
    """
    from app.api.v1 import admin_stats

    routes = list(admin_stats.router.routes)
    assert len(routes) == 5, f"stats 路由数不是 5：{len(routes)}"

    problems: list[str] = []
    for route in routes:
        codes = _declared_permissions(route)
        if "stats:read" not in codes:
            problems.append(f"{route.path} 声明的是 {sorted(codes) or '（空）'}，缺 stats:read")
    assert not problems, "有 stats 路由没声明 stats:read：\n" + "\n".join(problems)

    # 判据本身要有效：随便挑一个路由，把它依赖换成"别的权限码"，`_declared_permissions`
    # 必须能读出来（否则这条用例就是永远绿的摆设）。
    probe = SimpleNamespace(
        dependencies=[SimpleNamespace(dependency=deps.require_permission("other:code"))]
    )
    assert _declared_permissions(probe) == {"other:code"}, _declared_permissions(probe)


def test_endpoint_set_is_exactly_five(client: httpx.Client) -> None:
    """结构对账：**恰好 5 个** stats 端点（写多 / 写少 / 写错路径都要被看见）。

    为什么要对账"端点数"而不是逐个测：漏注册一个路由，`pytest` 不会红
    （没有用例引用它）；而前端那边是 404。这条把"注册表"本身钉住。
    """
    spec = client.get(f"{BASE}/openapi.json").json()
    stats_paths = sorted(p for p in spec["paths"] if "/admin/stats/" in p)
    assert stats_paths == [
        "/api/v1/admin/stats/distributions",
        "/api/v1/admin/stats/funnel",
        "/api/v1/admin/stats/overview",
        "/api/v1/admin/stats/trends",
        "/api/v1/admin/stats/weak-points",
    ], f"端点集合与预期不一致：{stats_paths}"

    # 每个端点的 200 响应都声明了 schema（契约在 Swagger 里可见，不是裸 dict）
    for path in stats_paths:
        responses = spec["paths"][path]["get"]["responses"]
        assert "200" in responses, f"{path} 没有声明 200 响应"
        schema = responses["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema, f"{path} 的 200 响应没有引用具体模型（契约不可见）"
