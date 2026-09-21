"""限流契约验收（补测批 · B 类处置 ②）。

本文件两条用例各固定一个**契约**，而不是"覆盖某一行"。

① `POST /admin/imports/upload` 的**用户维度**限流真的接上了。
   依据：`docs/04-API接口清单.md` §1.4「用户维度 120 次/分钟 ｜ Redis 令牌桶」。
   判据：同一 token 一分钟内第 121 次 → `42901`。
   ⚠️ **没有这条用例的接线 = 装饰** —— 代码在，但没人能证明它生效。

② `rate_limit` 在 **identity 为空**时**不建桶**，而不是"所有空 identity 共用一个桶"。
   这条的价值不是覆盖 `if not identity: return`，而是**把契约钉住**：
   以后谁想"简化掉这个 if"，用例会红 —— 因为空 identity 会让 key 退化成
   `rl:<name>::<window>`，**一个人打满 = 全站被封**。

依赖 API 已启动（真 PG + fakeredis）：`tools/local-verify/run-smoke.ps1`。

⚠️ 本文件**必须排在 test_admin_v5 / v7 之后**（拼音序正好满足）：
   用例①会在 Redis 里把"该 token 的上传额度"打满一分钟，
   若之后还有别的用例打 `/admin/imports/upload`，它会吃到 429。
"""

from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis
import httpx
from fastapi import Request

from app.core.deps import rate_limit
from app.core.errors import BizError
from app.core.response import now_ms

from .conftest import API, body

UPLOAD = f"{API}/admin/imports/upload"
LIMIT = 120  # docs/04 §1.4：用户维度 120 次/分钟
WINDOW_S = 60


def _align_to_window_start() -> None:
    """把起点对齐到分钟窗口开头。

    限流 key 里含 `now_ms() // (window*1000)` —— 若这 121 次请求跨了窗口边界，
    计数会归零，用例就会**偶发**变红。所以窗口只剩不到 15 秒时，先等下一个窗口。
    """
    span = WINDOW_S * 1000
    left = span - (now_ms() % span)
    if left < 15_000:
        time.sleep(left / 1000 + 0.5)


def test_upload_is_limited_per_user(client: httpx.Client, admin_h) -> None:
    """用户维度限流：同一 token 第 121 次上传 → 42901。"""
    _align_to_window_start()
    # 用**不支持的文件类型**：业务侧 40001 早早返回，不建批次、不写盘。
    # 限流是路由依赖，在进 handler **之前**就计数 —— 所以 400 的请求照样消耗额度，
    # 这条用例压的是"限流接上了"，不是"上传能不能成功"（后者由 test_admin_v5 覆盖）。
    files = {"file": ("probe.txt", b"not a csv", "text/plain")}

    codes = [body(client.post(UPLOAD, headers=admin_h, files=files))["code"] for _ in range(LIMIT)]
    assert 42901 not in codes, f"第 {LIMIT + 1} 次之前就被限流了：{sorted(set(codes))}"

    r = client.post(UPLOAD, headers=admin_h, files=files)
    assert r.status_code == 429, r.text
    assert body(r)["code"] == 42901, r.text


def test_empty_identity_does_not_share_a_bucket() -> None:
    """契约：identity 为空 → **不建桶**；有 identity → 正常建桶且限流生效。"""

    def scope(client: tuple | None = None) -> dict:
        s = {
            "type": "http",
            "method": "POST",
            "path": "/probe",
            "headers": [],
            "query_string": b"",
        }
        if client is not None:
            s["client"] = client
        return s

    limiter = rate_limit("probe_empty_identity", limit=1, window_seconds=WINDOW_S, by="ip")

    async def scenario() -> tuple[list, list, BizError | None]:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # ① 无 client、也无 X-Forwarded-For → identity 为空
        no_identity = Request(scope())
        for _ in range(3):
            await limiter(no_identity, redis)  # 必须**静默放行**，不抛错
        keys_after_empty = await redis.keys("rl:*")

        # ② 正向对照：有 client → 真的建桶、真的限流
        #    （没有这一步，"空 identity 不建桶"可能是因为限流压根没生效 = 假绿）
        has_identity = Request(scope(client=("203.0.113.7", 4242)))
        await limiter(has_identity, redis)
        denied: BizError | None = None
        try:
            await limiter(has_identity, redis)
        except BizError as exc:
            denied = exc
        return keys_after_empty, await redis.keys("rl:*"), denied

    keys_empty, keys_real, denied = asyncio.run(scenario())

    assert keys_empty == [], (
        "identity 为空时不应该建桶 —— 否则 key 退化成 rl:<name>::<window>，"
        "**一个人打满就是全站被封**"
    )
    assert len(keys_real) == 1, f"有 identity 时应正好建 1 个桶，实际 {keys_real}"
    assert denied is not None and denied.code == 42901, (
        f"limit=1 时第二次必须被拒（正向对照），实际 denied={denied!r}"
    )
