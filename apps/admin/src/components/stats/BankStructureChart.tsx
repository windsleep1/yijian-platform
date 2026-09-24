"use client";

import type { EChartsOption } from "echarts";

import { CachedBadge, OriginBadge } from "@/components/stats/StatsMetaBadges";
import { EmptyState } from "@/components/EmptyState";
import { EChart } from "@/lib/charts/EChart";
import type { StatsDistributions } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 题库结构（科目分布）—— **S2 里唯一的"真数据"图**。
 *
 * ## 为什么它在 S2，而"作答分布"要等 S3
 *
 * 这两张图的数据来源不同，**真假也不同**：
 *
 * - **题库结构** 数的是 `questions` / `subjects` —— 题库已经灌好了，
 *   所以这一半**现在就是真数据**（`meta.data_origin = "real"`）；
 * - **作答分布** 数的是 `practice_items` —— C 端还没落地，那些行是**造数**
 *   （`meta.data_origin = "demo"`）。所以它跟着 S3（"等有真实数据再说"）。
 *
 * ⇒ 结果就是 S2 的页面上**真数与造数同屏**。这不是"将就"，而是**必须标清楚**的理由：
 * 同一屏里既有真数据又有造数据、却不标注，就等于看板在说谎 —— 而且**不会报错**。
 * 每个区块都带 `OriginBadge`（文案来自后端的 `meta.origin_label`），就是为此。
 *
 * ## 为什么用横向柱状而不是饼图
 *
 * 科目数量在十几个量级、名称是中文长词。饼图在这种情况下标签会互相压叠，
 * 而横向柱状能直接把名称排在左边、按数量排序，**一眼就能比较**。
 */

export function BankStructureChart({
  data,
  isLoading,
  className,
}: {
  data?: StatsDistributions;
  isLoading: boolean;
  className?: string;
}) {
  // 按数量降序 —— "哪个科目的题最多"是这张图要回答的问题，排序本身就是答案的一部分。
  const rows = [...(data?.items ?? [])]
    .filter((it) => it.value > 0)
    .sort((a, b) => a.value - b.value); // ECharts 的 category 轴自下而上，所以升序传进去

  const option: EChartsOption = {
    grid: { left: 4, right: 32, top: 8, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "value",
      axisLabel: { fontSize: 10 },
      splitLine: { lineStyle: { type: "dashed" } },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.label),
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        name: "题目数",
        type: "bar",
        data: rows.map((r) => r.value),
        barMaxWidth: 18,
        itemStyle: { borderRadius: [0, 3, 3, 0] },
        label: { show: true, position: "right", fontSize: 10 },
      },
    ],
  };

  return (
    <section className={cn("rounded-lg border bg-card p-4", className)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">题库结构 · 科目分布</h2>
          {data ? <OriginBadge meta={data.meta} /> : null}
          {data ? <CachedBadge meta={data.meta} /> : null}
        </div>
        {data ? (
          <p className="text-[11px] text-muted-foreground">
            数据源：{data.meta.data_source ?? "questions + subjects"}
          </p>
        ) : null}
      </div>

      {isLoading && !data ? (
        <div className="h-[320px] w-full animate-pulse rounded-md bg-muted" />
      ) : rows.length === 0 ? (
        <EmptyState
          title="题库里还没有可统计的题目"
          description="导入题库后这里会显示各科目的题量分布。"
        />
      ) : (
        <EChart option={option} height={320} ariaLabel="题库科目分布" />
      )}
    </section>
  );
}
