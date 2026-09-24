"""管理端 · 统计看板接口（Batch 8 / S1-b）。

    GET /admin/stats/overview        ① 5 张指标卡（今日 / 昨日 / 环比）
    GET /admin/stats/trends          ② 趋势（按天 / 周 / 月）
    GET /admin/stats/distributions   ③ 分布（题库结构 = 真数据 / 作答分布 = 演示数据）
    GET /admin/stats/funnel          ④ 漏斗（S3 才上前端）
    GET /admin/stats/weak-points     ⑤ 薄弱知识点

全部需要权限 `stats:read`（已种进 RBAC，`permissions.id = 701`）。

## 三条本批的硬要求（用户 2026-09-24 定）

1. **缓存**：TTL 分级 / key 含**筛选参数全集** / Redis 挂了**降级直连**
   —— 见 `services/stats_cache.py` 与本文件的 `CACHE_PARAMS`。
2. **超时 → 503（不是 500）**：由 `stats_service._run` 抛 `50004`（HTTP 503）实现。
   二者对前端是**不同的语义**：500 = "这个请求有 bug"；503 = "服务暂时不可用，可重试"。
3. **真 / 造数据是接口属性**：`meta.data_origin` 由后端给（`bank`=real / `practice`=demo），
   前端只渲染标记，**不许自己判断真假**。

## 为什么路由层**不**重复范围校验（`Query(ge=…)`）

`stats_service` 已对每个参数做了语义与范围校验并抛 `40001`（S1-a 有测试盯着）。
路由层再来一遍会让同一个非法值**经 HTTP 得到 422、经 service 得到 40001** ——
两套错误码，前端要多写一个分支。**契约只留一套**：范围写在 `description` 里，
校验交给 service。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, RedisClient, require_permission
from app.core.response import Envelope, ok
from app.schemas.admin_stats import (
    StatsDistributionsPayload,
    StatsFunnelPayload,
    StatsOverviewPayload,
    StatsTrendsPayload,
    StatsWeakPointsPayload,
)
from app.services import stats_cache, stats_service

router = APIRouter(prefix="/admin/stats", tags=["管理端 · 统计看板"])

#: 每个端点参与缓存 key 的参数名。
#:
#: ★ **必须与 `stats_service` 对应函数签名里的全部筛选参数一一对应** ——
#: `test_stats_cache.py::test_cache_params_match_service_signature` 会逐个对账，
#: 漏一个（或写错名字）就变红。
#:
#: 为什么做成测试：漏参数的后果是**两种筛选共享同一条缓存 → 返回错数据且不报错**。
#: 那是最难发现的一类缺陷（用户看到一张"长得正常"的图，数字却来自别的筛选条件），
#: review 抓不住，只能靠机械对账。
CACHE_PARAMS: dict[str, tuple[str, ...]] = {
    "overview": ("day",),
    "trends": ("metric", "granularity", "date_from", "date_to", "subject_id"),
    "distributions": ("dim", "view", "date_from", "date_to", "subject_id"),
    "funnel": ("cohort", "date_to"),
    "weak_points": ("limit", "min_sample", "date_from", "date_to", "subject_id"),
}


def _cache_params(endpoint: str, scope: Mapping[str, Any]) -> dict[str, Any]:
    """按 `CACHE_PARAMS` 的白名单从函数局部变量取参数（白名单之外的一律不进 key）。

    用 `locals()` 而不是手写字面量字典：**参数名就是关键字参数名**，于是
    "路由加了新参数但忘了进 key" 会被上面那条对账测试抓住；手写字典则会安静地漏掉。
    """
    return {name: scope.get(name) for name in CACHE_PARAMS[endpoint]}


# ------------------------------------------------------------------ ① 指标卡


@router.get(
    "/overview",
    response_model=Envelope[StatsOverviewPayload],
    summary="指标卡（5 张）",
    description=(
        "DAU / 新增用户 / 答题量（带「人均」副行）/ 正确率 / 模考提交量。需要权限 `stats:read`。\n\n"
        "- 口径与分子分母见 `docs/20` §2.1；日界统一 `Asia/Shanghai`。\n"
        "- **零分母返回 `null` 而不是 `0`** —— `0.00%` 的意思是「全错」，正好相反。\n"
        "- `accuracy` 的环比单位是**百分点 pp**（`62%→65%` 是 **+3pp**），故只给 `delta_pp`。\n"
        "- ③ 的分子 == ④ 的分母（故意同分母），因此可交叉验算：答对数 == 答题量 × 正确率。"
    ),
    dependencies=[Depends(require_permission("stats:read"))],
)
async def stats_overview(
    db: DbSession,
    redis: RedisClient,
    day: Annotated[date | None, Query(description="统计日（YYYY-MM-DD），缺省 = 今天")] = None,
) -> dict:
    return ok(
        await stats_cache.get_or_load(
            redis,
            endpoint="overview",
            params=_cache_params("overview", locals()),
            loader=lambda: stats_service.overview(db, day=day),
        )
    )


# ------------------------------------------------------------------ ② 趋势


@router.get(
    "/trends",
    response_model=Envelope[StatsTrendsPayload],
    summary="趋势（折线）",
    description=(
        "按 `granularity` 聚合的时间序列。需要权限 `stats:read`。\n\n"
        "- 轴按粒度对齐：`week` → 周一、`month` → 每月 1 号。\n"
        "- **空桶会补 `null`（断点，不连线）**；`meta.filled_buckets` 告诉补了几个 —— "
        "`0` 才说明数据是连续的。\n"
        "- `optional_series` 含 `avg_duration_ms_median`（**中位数**，且口径是「客户端自报」）。"
    ),
    dependencies=[Depends(require_permission("stats:read"))],
)
async def stats_trends(
    db: DbSession,
    redis: RedisClient,
    metric: Annotated[
        str,
        Query(
            description=(
                "answers（答题量）| new_users（新增用户）| active_users（DAU）| "
                "accuracy（正确率）| exam_submits（模考提交量）"
            )
        ),
    ],
    granularity: Annotated[str, Query(description="day | week | month")] = "day",
    date_from: Annotated[date | None, Query(description="起始日（含）")] = None,
    date_to: Annotated[date | None, Query(description="结束日（含）")] = None,
    subject_id: Annotated[int | None, Query(description="按科目筛选")] = None,
) -> dict:
    return ok(
        await stats_cache.get_or_load(
            redis,
            endpoint="trends",
            params=_cache_params("trends", locals()),
            loader=lambda: stats_service.trends(
                db,
                metric=metric,
                granularity=granularity,
                date_from=date_from,
                date_to=date_to,
                subject_id=subject_id,
            ),
        )
    )


# ------------------------------------------------------------------ ③ 分布


@router.get(
    "/distributions",
    response_model=Envelope[StatsDistributionsPayload],
    summary="分布（柱 / 环）",
    description=(
        "按 `dim` 分组的分布。需要权限 `stats:read`。\n\n"
        "★ **`view` 决定 `meta.data_origin`，调用方不能传**：\n"
        "- `view=bank`（题库结构，取 `questions` / `subjects`）→ 恒为 `real`（**真实数据**）\n"
        "- `view=practice`（作答分布，取 `practice_items`）→ 恒为 `demo`（**演示数据**）\n\n"
        "后果：S2 的页面上会**同时出现真数据与造数据**。所以真假必须是**接口的属性**，"
        "由 `meta.origin_label` 给界面直接显示 —— **前端不许自己判断**（`docs/20` §7 判据 13）。"
    ),
    dependencies=[Depends(require_permission("stats:read"))],
)
async def stats_distributions(
    db: DbSession,
    redis: RedisClient,
    dim: Annotated[
        str, Query(description="subject | professional | difficulty | type | knowledge_point")
    ],
    view: Annotated[str, Query(description="bank（题库结构）| practice（作答分布）")],
    date_from: Annotated[date | None, Query(description="起始日（含）；bank 视图忽略")] = None,
    date_to: Annotated[date | None, Query(description="结束日（含）；bank 视图忽略")] = None,
    subject_id: Annotated[int | None, Query(description="按科目筛选")] = None,
) -> dict:
    return ok(
        await stats_cache.get_or_load(
            redis,
            endpoint="distributions",
            params=_cache_params("distributions", locals()),
            loader=lambda: stats_service.distributions(
                db,
                dim=dim,
                view=view,
                date_from=date_from,
                date_to=date_to,
                subject_id=subject_id,
            ),
        )
    )


# ------------------------------------------------------------------ ④ 漏斗


@router.get(
    "/funnel",
    response_model=Envelope[StatsFunnelPayload],
    summary="漏斗（注册 → 首次答题 → 付费）",
    description=(
        "用户级累积漏斗。需要权限 `stats:read`。"
        "**S3 才上前端**（需要真实用户数据才有意义，现在是造数）。\n\n"
        "- 三段**用户级去重**，因此必然单调；不单调时 `meta.warnings` 会**自己说出来**。\n"
        "- 零分母返回 `null`，不是 `0`。"
    ),
    dependencies=[Depends(require_permission("stats:read"))],
)
async def stats_funnel(
    db: DbSession,
    redis: RedisClient,
    cohort: Annotated[str, Query(description="all | 7d | 30d（按注册时间分群）")] = "30d",
    date_to: Annotated[date | None, Query(description="截止日（含），缺省 = 今天")] = None,
) -> dict:
    return ok(
        await stats_cache.get_or_load(
            redis,
            endpoint="funnel",
            params=_cache_params("funnel", locals()),
            loader=lambda: stats_service.funnel(db, cohort=cohort, date_to=date_to),
        )
    )


# ------------------------------------------------------------------ ⑤ 薄弱知识点


@router.get(
    "/weak-points",
    response_model=Envelope[StatsWeakPointsPayload],
    summary="薄弱知识点",
    description=(
        "按知识点聚合，按正确率**升序**（最薄弱在前）。需要权限 `stats:read`。\n\n"
        "- `min_sample` 是**样本门槛**：样本太少的知识点不进榜 —— "
        "否则「1 题答错 = 0% 正确率」会霸榜，那是噪声不是结论。\n"
        "- `kp_id` 序列化成**字符串**（雪花 ID 超过 JS `Number.MAX_SAFE_INTEGER`）。"
    ),
    dependencies=[Depends(require_permission("stats:read"))],
)
async def stats_weak_points(
    db: DbSession,
    redis: RedisClient,
    limit: Annotated[int, Query(description="返回条数，1–100")] = 10,
    min_sample: Annotated[int, Query(description="最小样本量（答题条数），≥1")] = 20,
    date_from: Annotated[date | None, Query(description="起始日（含）")] = None,
    date_to: Annotated[date | None, Query(description="结束日（含）")] = None,
    subject_id: Annotated[int | None, Query(description="按科目筛选")] = None,
) -> dict:
    return ok(
        await stats_cache.get_or_load(
            redis,
            endpoint="weak_points",
            params=_cache_params("weak_points", locals()),
            loader=lambda: stats_service.weak_points(
                db,
                limit=limit,
                min_sample=min_sample,
                date_from=date_from,
                date_to=date_to,
                subject_id=subject_id,
            ),
        )
    )
