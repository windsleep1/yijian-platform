"""管理端 · 统计看板响应模型（Batch 8 / S1-b）。

## 为什么 `meta` 强类型，而 `data` 的其余部分宽松

`meta` 承载的是**"这批数据的性质"**：`data_origin`（真数据 / 造数）、`cached`（这次是缓存
还是真算的）、`warnings`（口径告警）。这些**必须由后端给**，前端只渲染、不判断 ——
所以它们有明确类型与枚举，改坏了会被测试看见（`docs/20` §7 判据 13）。

`cached` / `ttl_sec` **声明成必填**是有意的：它们**只能**由 `stats_cache.get_or_load()` 注入。
哪个路由绕过缓存层直接返回 service 的结果，这里就会校验失败 —— 等于给"必须走缓存"加了一道
结构性防线，而不是靠 review。

其余部分是**分析型结构**（`series` / `items` / `stages` / `rates` …），形状随
`metric` / `dim` / `view` 变化，字段名与语义已由 `test_stats_service.py` 的返回键断言钉住。
这里用 `extra="allow"` 透传 —— **不为了让 Swagger 好看而写一堆会漂移的模型**。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: `real` = 真实数据（题库结构：`questions` / `subjects`）；
#: `demo` = 演示数据（C 端未落地，`practice_items` / `exam_attempts` 全是造数）。
DataOrigin = Literal["real", "demo"]


class StatsMeta(BaseModel):
    """所有 stats 端点共有的 meta（各端点自己的字段——如 `filled_buckets`——一并透传）。"""

    model_config = ConfigDict(extra="allow")

    cached: bool = Field(..., description="true = 本次响应来自缓存（没有查库）")
    ttl_sec: int = Field(..., description="该端点的缓存 TTL（秒）")
    timezone: str = Field("Asia/Shanghai", description="日界时区，全站统一（不是 UTC）")
    data_origin: DataOrigin = Field(
        ..., description="real = 真实数据；demo = 演示数据（前端只按它渲染标记，不自己判断）"
    )
    warnings: list[str] = Field(
        default_factory=list, description="口径告警（如「答了没判分」非 0）；空数组 = 无"
    )


class StatsPayload(BaseModel):
    """stats 各端点 `data` 的公共外形：一个 `meta` + 该端点自己的分析结果。"""

    model_config = ConfigDict(extra="allow")

    meta: StatsMeta


class StatsOverviewPayload(StatsPayload):
    """`GET /admin/stats/overview`：`day` + `cards{5 张}` + `meta`。

    `cards` 的键固定为 `dau` / `new_users` / `answers` / `accuracy` / `exam_submits`；
    每张卡含 `value` / `prev` / `delta_abs` / `delta_ratio`（`accuracy` 例外，
    它给的是 **`delta_pp`（百分点）**，且**故意不给 `delta_ratio`** ——
    免得前端顺手拿它当同比：`62%→65%` 是 **+3pp**，说成 +4.8% 会误导）。
    `answers` 卡额外带 `per_capita`（= ③ ÷ ①，原「人均答题」并入的副行）。
    """

    day: str = Field(..., description="统计日（Asia/Shanghai）")
    cards: dict[str, Any]


class StatsTrendsPayload(StatsPayload):
    """`GET /admin/stats/trends`：`axis` + `series` + `optional_series` + `meta`。

    - `axis`：横轴标签（按 `granularity` 对齐：`week` → 周一，`month` → 1 号）。
    - `series`：主序列（当前 `metric` 一条）。
    - `optional_series`：可选曲线 —— **形状是 dict 而不是 list**
      （`{"avg_duration_ms_median": [值或 null, …]}`，与 `axis` 等长），
      因为它按曲线名索引、且默认不勾。含 `avg_duration_ms_median`（**中位数**，不是均值）——
      它原本是第 ⑥ 张指标卡，因口径不诚实降级到这里（见 `docs/20` §2.2）。
    - `meta.filled_buckets`：**补了几个空桶**（= 0 才说明数据连续）。
    """

    metric: str
    granularity: str
    axis: list[str]
    series: list[dict[str, Any]]
    optional_series: dict[str, list[float | None]] = Field(default_factory=dict)


class StatsDistributionsPayload(StatsPayload):
    """`GET /admin/stats/distributions`：`dim` / `view` / `items` + `meta`。

    ★ **`meta.data_origin` 由 `view` 决定，不由调用方传**：
    `view=bank`（题库结构）恒为 `real`，`view=practice`（作答分布）恒为 `demo`。
    `meta.origin_label` 是给界面直接显示的文案（"真实" / "演示数据"）。
    """

    dim: str
    view: str
    subject_id: int | None = None
    items: list[dict[str, Any]]


class StatsFunnelPayload(StatsPayload):
    """`GET /admin/stats/funnel`：`stages`（3 段）+ `rates` + `meta`。

    `rates.step` 是相对上一段、`rates.cumulative` 相对第一段。
    不单调时 `meta.warnings` 会带上说明 —— 接口**自己说出来**，不让人凭肉眼觉得"有点怪"。
    """

    cohort: str
    stages: list[dict[str, Any]]
    rates: dict[str, list[float | None]]


class StatsWeakPointsPayload(StatsPayload):
    """`GET /admin/stats/weak-points`：`items`（按正确率升序）+ `meta`。

    每项：`kp_id`（**字符串** —— 雪花 ID 超过 JS `Number.MAX_SAFE_INTEGER`）、
    `name` / `subject` / `sample` / `accuracy`。
    """

    min_sample: int
    limit: int
    items: list[dict[str, Any]]
