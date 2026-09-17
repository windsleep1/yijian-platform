"use client";

import { validateStats, type StatCard } from "@/lib/import";
import type { ImportBatch } from "@/lib/types";
import { cn } from "@/lib/utils";

const TONE: Record<StatCard["tone"], string> = {
  default: "text-foreground",
  success: "text-emerald-700",
  warning: "text-amber-700",
  danger: "text-destructive",
  muted: "text-muted-foreground",
};

type Props = {
  batch: Pick<
    ImportBatch,
    "total_rows" | "success_rows" | "failed_rows" | "duplicate_rows" | "updated_rows"
  >;
  className?: string;
};

/**
 * 校验 / 执行结果的统计卡片。
 *
 * 需求里那句"**预览页给具体数字，不能只写「通过」**"就是落在这里：
 * 六个数各有各的含义，而且**必须分开显示**——
 * "将新增 3 / 将更新 0 / 将跳过 597"和"通过 600" 对教研是两件完全不同的事：
 * 前者告诉他"只有 3 道是新题，其余 597 道库里已经有了"，
 * 后者只给了个总数，等于什么都没说。
 *
 * 每张卡片都带 `hint`，解释这个数字**意味着执行时会发生什么**。
 */
export function ImportStatCards({ batch, className }: Props) {
  const cards = validateStats(batch);
  return (
    <div className={cn("grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5", className)}>
      {cards.map((c) => (
        <div key={c.key} className="rounded-lg border bg-card p-3">
          <p className="text-[11px] text-muted-foreground">{c.label}</p>
          <p className={cn("yj-json mt-1 text-xl font-semibold tabular-nums", TONE[c.tone])}>
            {c.value}
          </p>
          {c.hint ? (
            <p className="mt-0.5 text-[10px] leading-snug text-muted-foreground">{c.hint}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}
