"use client";

import { formatNumber } from "@/lib/format";
import type { MetricCardKey, MetricCardValue, StatsOverview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * 5 张指标卡（`docs/20` §2.1 定稿）。
 *
 * ## 三处刻意的展示决定
 *
 * 1. **零分母显示 `—`，不显示 `0`。** 后端对"当日 0 人答题"返回的是 `null`，
 *    因为 `0.00%` 的意思是"**全错**"，与"没人答"**正好相反**。
 *    `formatNumber(null)` 已经给了 `—`，这里**不额外兜底成 0** —— 一兜就把语义弄反了。
 * 2. **正确率的环比单位是「百分点 pp」**，不是 %：`62% → 65%` 是 **+3pp**；
 *    报成 +4.8% 会让人以为变化更大。后端为此**故意不给** `delta_ratio`。
 * 3. **③答题量带一行「人均」副行。** 人均 = ③ ÷ ①，原本单独占一张卡，
 *    但它是心算就能得到的数 —— 占一整张卡等于用 1/5 的注意力买一个除法。
 */

/** 0–1 的比值 → 百分比。**这是展示换算，不是业务算术**：比值一律由后端算好。 */
function formatRatio(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

type Kind = "count" | "ratio";

/** 环比文案。比值型只给百分点（见文件头第 2 条）。 */
function deltaText(card: MetricCardValue, kind: Kind): string {
  if (kind === "ratio") {
    if (card.delta_pp === null || card.delta_pp === undefined) return "环比 —";
    return `环比 ${card.delta_pp > 0 ? "+" : ""}${card.delta_pp.toFixed(1)}pp`;
  }
  if (card.delta_ratio === null || card.delta_ratio === undefined) return "环比 —";
  return `环比 ${card.delta_ratio > 0 ? "+" : ""}${(card.delta_ratio * 100).toFixed(1)}%`;
}

const CARDS: { key: MetricCardKey; label: string; kind: Kind; hint: string }[] = [
  { key: "dau", label: "DAU", kind: "count", hint: "当日有学习行为的去重用户（答题 ∪ 交卷）" },
  { key: "new_users", label: "新增用户", kind: "count", hint: "当日注册、且未注销" },
  { key: "answers", label: "答题量", kind: "count", hint: "当日已作答的题目条数" },
  {
    key: "accuracy",
    label: "正确率",
    kind: "ratio",
    hint: "答对 ÷ 已作答（分母是「已答」，不是总题数）",
  },
  { key: "exam_submits", label: "模考提交量", kind: "count", hint: "当日交卷次数" },
];

/** 副行：③ 的「人均」与 ② 的「近 7 日均值」—— 都是原卡片降级并入的信息。 */
function SubLine({ card }: { card: MetricCardValue }) {
  if (card.per_capita !== undefined) {
    return <span>人均 {card.per_capita === null ? "—" : card.per_capita.toFixed(1)} 题</span>;
  }
  if (card.prev7_avg !== undefined) {
    return <span>近 7 日均值 {card.prev7_avg === null ? "—" : card.prev7_avg.toFixed(1)}</span>;
  }
  return null;
}

export function MetricCards({
  data,
  isLoading,
  className,
}: {
  data?: StatsOverview;
  isLoading: boolean;
  className?: string;
}) {
  if (isLoading || !data) {
    return (
      <div className={cn("grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5", className)}>
        {CARDS.map((c) => (
          <div key={c.key} className="rounded-lg border bg-card p-3">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="mt-2 h-6 w-20" />
            <Skeleton className="mt-2 h-3 w-24" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className={cn("grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5", className)}>
      {CARDS.map((c) => {
        const card = data.cards[c.key];
        const display = c.kind === "ratio" ? formatRatio(card?.value) : formatNumber(card?.value);
        return (
          <div key={c.key} className="rounded-lg border bg-card p-3" title={c.hint}>
            <p className="text-[11px] text-muted-foreground">{c.label}</p>
            <p className="yj-json mt-1 text-xl font-semibold tabular-nums">{display}</p>
            <p className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
              {card ? deltaText(card, c.kind) : "环比 —"}
            </p>
            {card ? (
              <p className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
                <SubLine card={card} />
              </p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
