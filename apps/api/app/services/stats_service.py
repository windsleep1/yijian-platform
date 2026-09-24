"""
统计看板数据层（Batch 8 · S1-a）。

只做**聚合查询**：不感知 HTTP、不碰路由、不接缓存（缓存是 S2 的事）。

## 为什么用 raw SQL 而不是 ORM

本批的查询要用到 `FILTER (WHERE …)`、`generate_series` 补空桶、`percentile_cont`、
`AT TIME ZONE` 日界 —— ORM 表达这些要么很绕，要么最后仍退化成 `text()`。
**"能被 review"比"用了 ORM"重要**，而本项目 Batch 4 起就是这条路
（`app/db/models.py` 开头写着：题库/导入服务一律用 `text()` 原生 SQL）。

## ★★ 四条硬约束（2026-09-24 用户定，**少一条就要回退到 ORM**）

| # | 约束 | 在本文件里的落点 |
|---|---|---|
| ① | **参数化绑定，不拼字符串**。任何用户输入走 `:param` | 全部 SQL 文本是**静态文件**；唯一的动态值是 `set_config('statement_timeout', :timeout, true)`，它**本身也是绑定参数** |
| ② | **返回 dict，不返回 tuple**。tuple 的字段顺序是**隐式契约** —— 加字段会静默错位且不报错 | `_run()` 统一走 `result.mappings()` |
| ③ | **每个查询带 `statement_timeout`**（硬约定 N：无界等待必须加界） | `_run()` 每次查询前 `set_config(..., is_local := true)` |
| ④ | **SQL 单独存放** `app/sql/stats/*.sql`，Python 侧只读文件 | `load_sql()` + `SQL_DIR` |

## ★ 比值只有一处实现

`_ratio()` 是全仓**唯一**做除法的地方（docs/20 §7-9「口径单一」）。
SQL 负责给**原始计数**，Python 负责给**比值** —— 这样"正确率 = 对/已答"这件事
不可能出现两个版本。

## ★ 零分母返回 `None` 而不是 `0.0`

`0.00%` 的意思是"**全错**"，与"**没人答题**"正好相反（docs/20 §7-3）。
所以零分母必须让 JSON 里是 `null`，前端显示 `—`。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, query_timeout

logger = logging.getLogger("app.stats")

#: 全站统一日界（**不是** UTC —— 见 docs/20 §2.1 易错点 3）。
#:
#: 用**固定 +08** 而不是 `ZoneInfo("Asia/Shanghai")`，两个理由：
#: ① 中国自 1992 年起**没有夏令时**，UTC+8 是精确定义，不是近似；
#: ② `ZoneInfo` 在 Windows 上要先装 `tzdata` 包才能用 —— 本机实测没装，
#:    而 CI 是 Linux（系统自带时区库）→ 那会变成"**CI 绿、本地红**"，
#:    正是本项目反复吃亏的那类环境差异。
#: 这也与 `sms_service.CN_TZ` 的既有写法一致（不另发明第二套）。
#:
#: ⚠️ SQL 那一侧仍然写 IANA 名 `AT TIME ZONE 'Asia/Shanghai'` ——
#: Postgres 自带时区库，且对 1992 年后的任何日期两者完全一致。
#: 若将来需要给 1986–1991 的历史数据分桶（中国当时有夏令时），再换回 ZoneInfo + tzdata。
TZ = timezone(timedelta(hours=8))

#: 聚合查询的硬上界（ms）。超时 → `50004`（HTTP 503，可重试）。
#: 3s 是 docs/20 §7 判据 7 的数字：10 万行 + 无界时间范围也必须在此内返回。
STATS_TIMEOUT_MS = 3000

SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "stats"

TREND_METRICS = ("answers", "new_users", "active_users", "accuracy", "exam_submits")
#: 只做**白名单校验**；轴步长在 SQL 里按 gran CASE 出来（见 trends.sql 的注释）
GRANULARITIES = ("day", "week", "month")
DIST_DIMS = ("subject", "professional", "difficulty", "type")
DIST_VIEWS = ("bank", "practice")
FUNNEL_COHORTS = ("30d", "all")

#: `view` → (SQL 文件, data_origin)。**真假是接口的属性，不是前端的判断**（docs/20 §3.2）。
#: bank 走 `questions`/`subjects` = 真实数据；practice 走 `practice_items` = 造数（C 端未落地）。
VIEW_SOURCE = {
    "bank": ("distributions_bank", "real"),
    "practice": ("distributions_practice", "demo"),
}

DEFAULT_WINDOW_DAYS = 30


# ------------------------------------------------------------------ 基础设施


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    """读取一份 SQL 文件（硬约束 ④）。

    `lru_cache` 是有意的：SQL 是随镜像走的**静态资源**，同一进程只读一次盘。
    同时它也说明"这里没有运行时拼 SQL" —— 名字只是文件名，内容来自文件。
    """
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


async def _run(db: AsyncSession, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """执行一份 SQL，返回 `list[dict]`（硬约束 ②③）。

    ## 超时是怎么生效的（这段不能只当注释，有测试盯着）
    `set_config('statement_timeout', …, is_local := true)` 的作用域是**当前事务**。
    SQLAlchemy 的 `AsyncSession` 在第一次 `execute` 时自动开启事务，并在
    `commit()` / `rollback()` / 关闭前保持打开 —— 所以紧随其后的查询在**同一个事务**里，
    超时对它**确实生效**。
    （`test_stats_service.py::test_statement_timeout_is_enforced` 会真的把它打出来。）

    ⚠️ 用 `set_config(...)` 而不是 `SET LOCAL statement_timeout = '3s'`：
    后者**不接受绑定参数**，只能拼字符串。前者是函数调用，值是绑定的（硬约束 ①）。
    """
    await db.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{STATS_TIMEOUT_MS}ms"},
    )
    try:
        result = await db.execute(text(load_sql(name)), params)
    except DBAPIError as exc:
        cause = str(getattr(exc, "orig", exc))
        if "statement timeout" in cause or "QueryCanceledError" in cause:
            logger.warning("统计查询超时 sql=%s params=%s", name, params)
            raise query_timeout() from exc
        raise
    # 硬约束 ②：**不用位置元组**。tuple 的字段顺序是隐式契约，
    # 以后 SQL 里多一列 / 换个顺序，取值处会**静默取错**而不报错。
    return [dict(row) for row in result.mappings().all()]


def _ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    """**全仓唯一**计算比值的地方（docs/20 §7-9）。

    零分母 → `None`（不是 `0.0`）：`0.00%` 表示"全错"，与"没人答题"**意思相反**。
    """
    if not denominator:
        return None
    return round(float(numerator or 0) / float(denominator), 6)


def _today() -> date:
    return datetime.now(TZ).date()


def _align(d: date, granularity: str) -> date:
    """把日期对齐到桶起点：week → 本周一，month → 本月 1 号。

    轴与 `date_trunc()` **必须按同一规则对齐**，否则 LEFT JOIN 会对不上，
    补空桶就变成了"两个错位的轴"，图会更难看（而且不报错）。
    """
    if granularity == "week":
        return d - timedelta(days=d.weekday())  # Monday
    if granularity == "month":
        return d.replace(day=1)
    return d


def _window(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    end = date_to or _today()
    start = date_from or (end - timedelta(days=DEFAULT_WINDOW_DAYS - 1))
    if start > end:
        raise bad_request("date_from 不能晚于 date_to")
    return start, end


def _delta_abs(today: int, prev: int) -> int:
    return today - prev


# ------------------------------------------------------------------ 1. 指标卡


async def overview(db: AsyncSession, *, day: date | None = None) -> dict[str, Any]:
    """5 张指标卡（docs/20 §2.1 定稿）：DAU / 新增用户 / 答题量（带人均）/ 正确率 / 模考提交量。

    `data_origin = 'demo'` —— 5 张卡的数据源（`practice_items` / `exam_attempts`）
    当前**全是造数**（C 端未落地）。这一点必须随接口给出去，不能靠前端猜。
    """
    target = day or _today()
    # ⚠️ 传 **date 对象**而不是 isoformat 字符串：`CAST(:day AS date)` 会让 SQLAlchemy
    #    把绑定参数推断成 Date 类型，asyncpg 拿到字符串会报
    #    `invalid input for query argument: 'str' object has no attribute 'toordinal'`。
    #    （这不是"字符串也能用"的地方 —— 类型是接口契约的一部分。）
    row = (await _run(db, "overview", {"day": target}))[0]

    answers = int(row["answers_today"] or 0)
    correct = int(row["correct_today"] or 0)
    dau = int(row["dau_today"] or 0)
    new_users = int(row["new_users_today"] or 0)
    submits = int(row["exam_submits_today"] or 0)

    y_answers = int(row["answers_yday"] or 0)
    y_correct = int(row["correct_yday"] or 0)
    y_dau = int(row["dau_yday"] or 0)
    y_new = int(row["new_users_yday"] or 0)
    y_submits = int(row["exam_submits_yday"] or 0)
    prev7_new = int(row["new_users_prev7"] or 0)
    ungraded = int(row["ungraded_rows"] or 0)

    def card(today: int, prev: int) -> dict[str, Any]:
        return {
            "value": today,
            "prev": prev,
            "delta_abs": _delta_abs(today, prev),
            "delta_ratio": _ratio(_delta_abs(today, prev), prev),
        }

    accuracy = _ratio(correct, answers)
    y_accuracy = _ratio(y_correct, y_answers)

    cards = {
        "dau": card(dau, y_dau),
        "new_users": {
            **card(new_users, y_new),
            "prev7_avg": round(prev7_new / 7, 4),
        },
        "answers": {
            **card(answers, y_answers),
            # 原 ⑤「人均答题」并入本卡副行（docs/20 §2.1）：它是 ③ ÷ ①，
            # 单独占一张卡等于用 1/5 的注意力买一个心算能得的信息。
            "per_capita": _ratio(answers, dau),
        },
        "accuracy": {
            "value": accuracy,
            "prev": y_accuracy,
            # ★ 正确率的环比单位是**百分点 pp**，不是 %。
            #   "62%→65%" 是 **+3pp**；报成 +4.8% 会让人以为变化更大。
            #   这里**故意不提供** delta_ratio —— 免得前端顺手拿它当同比。
            "unit": "ratio",
            "delta_pp": (
                None
                if accuracy is None or y_accuracy is None
                else round((accuracy - y_accuracy) * 100, 4)
            ),
        },
        "exam_submits": card(submits, y_submits),
    }

    warnings: list[str] = []
    if ungraded:
        warnings.append(
            f"窗口内有 {ungraded} 行「已作答但未判分」(answered_at IS NOT NULL 且 is_correct IS NULL)："
            "它们进正确率的分母、不进分子 —— 正确率会静默偏低。见 integrity_check()。"
        )

    return {
        "day": target.isoformat(),
        "cards": cards,
        "meta": {
            "timezone": "Asia/Shanghai",
            "window_start": str(row["window_start"]),
            "window_end": str(row["window_end"]),
            "data_source": "practice_items + exam_attempts + users",
            "data_origin": "demo",
            "warnings": warnings,
        },
    }


# ------------------------------------------------------------------ 2. 趋势


async def trends(
    db: AsyncSession,
    *,
    metric: str,
    granularity: str = "day",
    date_from: date | None = None,
    date_to: date | None = None,
    subject_id: int | None = None,
) -> dict[str, Any]:
    """按天/周/月的曲线。**空桶由 SQL 补齐**，比值型空桶保持 `null`（前端断线）。"""
    if metric not in TREND_METRICS:
        raise bad_request(f"metric 必须是 {'/'.join(TREND_METRICS)} 之一")
    if granularity not in GRANULARITIES:
        raise bad_request(f"granularity 必须是 {'/'.join(GRANULARITIES)} 之一")

    start, end = _window(date_from, date_to)
    rows = await _run(
        db,
        "trends",
        {
            "gran": granularity,
            "date_from": start,
            "date_to": end,
            "axis_from": _align(start, granularity),
            "axis_to": _align(end, granularity),
            "subject_id": subject_id,
        },
    )

    axis = [str(r["bucket"]) for r in rows]

    if metric == "accuracy":
        points: list[Any] = [_ratio(int(r["correct"] or 0), int(r["answers"] or 0)) for r in rows]
    else:
        points = [int(r[metric] or 0) for r in rows]

    # 「补了空桶」的计数。⚠️ 近似：LEFT JOIN 之后，"**本来没有行**"与"**行加总为 0**"
    # 不可区分（计数型聚合），所以判据取"三项活动全为 0"。
    filled = sum(
        1
        for r in rows
        if int(r["answers"] or 0) == 0
        and int(r["new_users"] or 0) == 0
        and int(r["exam_submits"] or 0) == 0
    )

    return {
        "metric": metric,
        "granularity": granularity,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "subject_id": subject_id,
        "axis": axis,
        "series": [{"name": metric, "points": points}],
        # 可选曲线（原指标卡 ⑥ 降级而来）：**默认不勾**，且是中位数。
        "optional_series": {
            "avg_duration_ms_median": [
                None if r["median_time_ms"] is None else float(r["median_time_ms"]) for r in rows
            ]
        },
        "meta": {
            "timezone": "Asia/Shanghai",
            "filled_buckets": filled,
            "total_buckets": len(rows),
            "data_source": (
                "users"
                if metric == "new_users"
                else "exam_attempts"
                if metric == "exam_submits"
                else "practice_items"
            ),
            "data_origin": "demo",
            "warnings": (
                ["subject_id 对 new_users 无效（用户表没有科目属性），已忽略"]
                if metric == "new_users" and subject_id is not None
                else []
            ),
        },
    }


# ------------------------------------------------------------------ 3. 分布


async def distributions(
    db: AsyncSession,
    *,
    dim: str,
    view: str,
    date_from: date | None = None,
    date_to: date | None = None,
    subject_id: int | None = None,
) -> dict[str, Any]:
    """分布图。`view` 决定**真数据还是造数**，并把这件事实写进 `meta.data_origin`。

    - `bank`（题库结构）→ `questions` / `subjects` → **real**
    - `practice`（作答分布）→ `practice_items` → **demo**

    ★ 两个视角不可互相替代：题库里难的题占 20%，不代表作答里难的占 20%。
    """
    if dim not in DIST_DIMS:
        raise bad_request(f"dim 必须是 {'/'.join(DIST_DIMS)} 之一")
    if view not in DIST_VIEWS:
        raise bad_request(f"view 必须是 {'/'.join(DIST_VIEWS)} 之一")

    sql_name, origin = VIEW_SOURCE[view]
    start, end = _window(date_from, date_to)
    rows = await _run(
        db,
        sql_name,
        {
            "dim": dim,
            "date_from": start,
            "date_to": end,
            "subject_id": subject_id,
        },
    )

    items = []
    for r in rows:
        value = int(r["value"] or 0)
        item: dict[str, Any] = {
            "key": str(r["key"]),
            "label": str(r["label"]),
            "value": value,
        }
        # 只有 practice 视图能算正确率：bank 视图数的是**题**，没有"答没答对"。
        item["accuracy"] = (
            _ratio(int(r["correct"] or 0), value) if view == "practice" and "correct" in r else None
        )
        items.append(item)

    return {
        "dim": dim,
        "view": view,
        "subject_id": subject_id,
        "items": items,
        "meta": {
            "timezone": "Asia/Shanghai",
            "data_origin": origin,
            # 让前端**不判断**真假，只渲染（docs/20 §7 判据 13）
            "origin_label": "真实" if origin == "real" else "演示数据",
            "data_source": "questions+subjects" if view == "bank" else "practice_items",
            "warnings": [],
        },
    }


# ------------------------------------------------------------------ 4. 漏斗


async def funnel(
    db: AsyncSession,
    *,
    cohort: str = "30d",
    date_to: date | None = None,
) -> dict[str, Any]:
    """注册 → 首次答题 → 付费，**同一批人**的留存式漏斗。

    第三段在真实环境里大概率是 0（没有支付环境）——造数脚本会造，图上标"演示数据"。
    """
    if cohort not in FUNNEL_COHORTS:
        raise bad_request(f"cohort 必须是 {'/'.join(FUNNEL_COHORTS)} 之一")

    end = date_to or _today()
    start = (end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)) if cohort == "30d" else None
    row = (
        await _run(
            db,
            "funnel",
            {
                "cohort_from": start,
                "cohort_to": end,
            },
        )
    )[0]

    registered = int(row["registered"] or 0)
    first_answered = int(row["first_answered"] or 0)
    paid = int(row["paid"] or 0)

    warnings: list[str] = []
    # §7-5：漏斗必须单调（用户级去重，段不会变多）。
    # 违反只有一种可能：SQL 被改坏了。让它**当场说出来**，不要等看图的人发现。
    if not (registered >= first_answered >= paid):
        warnings.append(
            f"漏斗不单调（注册 {registered} / 首次答题 {first_answered} / 付费 {paid}）——"
            "三段不是同一批人去重后的结果，SQL 有问题"
        )

    return {
        "cohort": cohort,
        "stages": [
            {"key": "registered", "label": "注册", "count": registered},
            {"key": "first_answered", "label": "首次答题", "count": first_answered},
            {"key": "paid", "label": "付费", "count": paid},
        ],
        "rates": {
            # 相对上一段
            "step": [_ratio(first_answered, registered), _ratio(paid, first_answered)],
            # 相对第一段
            "cumulative": [_ratio(first_answered, registered), _ratio(paid, registered)],
        },
        "meta": {
            "timezone": "Asia/Shanghai",
            "cohort_from": start.isoformat() if start else None,
            "cohort_to": end.isoformat(),
            "data_origin": "demo",
            "warnings": warnings,
        },
    }


# ------------------------------------------------------------------ 5. 薄弱点


async def weak_points(
    db: AsyncSession,
    *,
    limit: int = 10,
    min_sample: int = 20,
    date_from: date | None = None,
    date_to: date | None = None,
    subject_id: int | None = None,
) -> dict[str, Any]:
    """正确率最低的 N 个知识点。`min_sample` 是**必须**的门槛 ——
    没有它，"错了 1 题"的知识点会以 0% 霸榜。"""
    if limit < 1 or limit > 100:
        raise bad_request("limit 必须在 1–100 之间")
    if min_sample < 1:
        raise bad_request("min_sample 必须 >= 1")

    start, end = _window(date_from, date_to)
    rows = await _run(
        db,
        "weak_points",
        {
            "date_from": start,
            "date_to": end,
            "subject_id": subject_id,
            "min_sample": min_sample,
            "limit": limit,
        },
    )

    return {
        "min_sample": min_sample,
        "limit": limit,
        "items": [
            {
                "kp_id": str(r["kp_id"]),
                "name": str(r["name"]),
                "subject": str(r["subject"]),
                "sample": int(r["sample"] or 0),
                "accuracy": _ratio(int(r["correct"] or 0), int(r["sample"] or 0)),
            }
            for r in rows
        ],
        "meta": {
            "timezone": "Asia/Shanghai",
            "data_origin": "demo",
            "warnings": [],
        },
    }


# ------------------------------------------------------------------ 体检


async def integrity_check(db: AsyncSession) -> dict[str, Any]:
    """全表体检（docs/20 §7 判据 12）。两个计数**都应为 0**。

    为什么单列一个函数而不是塞进某个端点：
    "已作答但未判分"是**数据缺陷**，它的影响面是"所有含正确率的数字"，
    不属于某一张图。做成独立入口，才能被测试直接断言、也能将来挂到健康检查上。
    """
    row = (await _run(db, "integrity", {}))[0]
    ungraded = int(row["ungraded_rows"] or 0)
    graded_no_time = int(row["graded_no_time_rows"] or 0)
    return {
        "ok": ungraded == 0 and graded_no_time == 0,
        "ungraded_rows": ungraded,
        "graded_no_time_rows": graded_no_time,
        "meta": {
            "data_source": "practice_items",
            "warnings": ([f"{ungraded} 行已作答但未判分：正确率会静默偏低"] if ungraded else [])
            + ([f"{graded_no_time} 行有判分结果却没有作答时间"] if graded_no_time else []),
        },
    }
