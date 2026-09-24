"use client";

import type { EChartsOption } from "echarts";

import { CachedBadge, OriginBadge } from "@/components/stats/StatsMetaBadges";
import { EmptyState } from "@/components/EmptyState";
import { EChart } from "@/lib/charts/EChart";
import type { StatsGranularity, StatsTrendMetric, StatsTrends } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 趋势图（答题量 / 新增用户 / DAU / 正确率，按天 / 周 / 月）。
 *
 * ## 三个要点
 *
 * 1. **空桶必须断线，不能连成一条直直的斜线。** 后端对没有数据的桶返回 `null`
 *    （`meta.filled_buckets` 会告诉你补了几个），ECharts 的 `connectNulls` 保持默认的
 *    `false` —— 一开就连出一条"看着很平滑但纯属虚构"的趋势。
 *    造数脚本刻意留了 **2 个空日**，就是给这个状态用的。
 * 2. **正确率的 y 轴是百分比**，不是原始 0–1 比值。（`axisLabel.formatter` 做展示换算，
 *    数据本身仍是后端给的。）
 * 3. **切换 metric / 粒度时保留旧图**（`placeholderData: keepPreviousData` 在 hook 层），
 *    避免每次切换整块闪成骨架屏。
 */

const METRICS: { key: StatsTrendMetric; label: string }[] = [
  { key: "answers", label: "答题量" },
  { key: "new_users", label: "新增用户" },
  { key: "active_users", label: "DAU" },
  { key: "accuracy", label: "正确率" },
];

const GRANULARITIES: { key: StatsGranularity; label: string }[] = [
  { key: "day", label: "按天" },
  { key: "week", label: "按周" },
  { key: "month", label: "按月" },
];

function TabGroup<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
}: {
  items: { key: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      className="inline-flex rounded-md border bg-muted/40 p-0.5"
      role="group"
      aria-label={ariaLabel}
    >
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          onClick={() => onChange(it.key)}
          aria-pressed={value === it.key}
          className={cn(
            "rounded px-2 py-1 text-xs transition-colors",
            value === it.key
              ? "bg-background font-medium text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function TrendChart({
  data,
  isLoading,
  metric,
  granularity,
  onMetricChange,
  onGranularityChange,
  className,
}: {
  data?: StatsTrends;
  isLoading: boolean;
  metric: StatsTrendMetric;
  granularity: StatsGranularity;
  onMetricChange: (m: StatsTrendMetric) => void;
  onGranularityChange: (g: StatsGranularity) => void;
  className?: string;
}) {
  const isRatio = metric === "accuracy";
  const label = METRICS.find((m) => m.key === metric)?.label ?? metric;
  const hasData = !!data?.axis?.length;

  const option: EChartsOption = {
    grid: { left: 4, right: 12, top: 28, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v) =>
        v === null || v === undefined
          ? "—"
          : isRatio
            ? `${(Number(v) * 100).toFixed(1)}%`
            : String(v),
    },
    xAxis: {
      type: "category",
      data: data?.axis ?? [],
      boundaryGap: false,
      axisLabel: { fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      // 正确率按百分比显示；数据本身仍是后端给的 0–1 比值
      axisLabel: isRatio
        ? { fontSize: 10, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }
        : { fontSize: 10 },
      splitLine: { lineStyle: { type: "dashed" } },
    },
    series: (data?.series ?? []).map((s) => ({
      name: label,
      type: "line" as const,
      data: s.points,
      // ⚠️ 保持 false：空桶要**断线**，不能连成虚构的斜线（见文件头第 1 条）
      connectNulls: false,
      symbolSize: 5,
      lineStyle: { width: 2 },
    })),
  };

  return (
    <section className={cn("rounded-lg border bg-card p-4", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">趋势</h2>
          {data ? <OriginBadge meta={data.meta} /> : null}
          {data ? <CachedBadge meta={data.meta} /> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TabGroup items={METRICS} value={metric} onChange={onMetricChange} ariaLabel="选择指标" />
          <TabGroup
            items={GRANULARITIES}
            value={granularity}
            onChange={onGranularityChange}
            ariaLabel="选择粒度"
          />
        </div>
      </div>

      {isLoading && !data ? (
        <div className="h-[280px] w-full animate-pulse rounded-md bg-muted" />
      ) : !hasData ? (
        <EmptyState
          title="这段时间没有数据"
          description="换一个时间范围，或确认所选科目下已有作答记录。"
        />
      ) : (
        <>
          <EChart option={option} height={280} ariaLabel={`${label}趋势图`} />
          {/* 补了几个空桶要**说出来**：`0` 才说明数据是连续的 */}
          {typeof data?.meta.filled_buckets === "number" && data.meta.filled_buckets > 0 ? (
            <p className="mt-2 text-[11px] text-muted-foreground">
              共 {data.meta.total_buckets ?? data.axis.length} 个时间点，其中{" "}
              <span className="font-medium">{data.meta.filled_buckets}</span>{" "}
              个**没有数据**（图上为断点）。
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
