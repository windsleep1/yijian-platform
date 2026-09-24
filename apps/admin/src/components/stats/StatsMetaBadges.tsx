"use client";

import { Database, FlaskConical, RefreshCw } from "lucide-react";

import type { StatsMeta } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 数据来源标记：**真实** / **演示数据**。
 *
 * ## 为什么必须显式标出来
 *
 * S2 的页面上**真数据与造数据同屏**：题库结构（`questions` / `subjects`）是真的，
 * 作答分布（`practice_items`）是造数。不标注的话，看的人会把造出来的数当成真的读 ——
 * 而且**不会报错**（数字看着完全正常）。
 *
 * ## 文案由**后端**给，前端不映射
 *
 * 优先用 `meta.origin_label`（后端给的"真实" / "演示数据"）。
 * 真假是**接口的属性**，不是渲染者的判断（`docs/20` §7 判据 13）——
 * 前端一旦开始"按 `view` 反推真假"，就会在加了第三个视图时悄悄判错。
 * 后面的兜底只是防御性的（老接口没这个字段时别渲染成空白），**不是**主路径。
 */
export function OriginBadge({ meta, className }: { meta: StatsMeta; className?: string }) {
  const real = meta.data_origin === "real";
  const label = meta.origin_label ?? (real ? "真实数据" : "演示数据");
  const Icon = real ? Database : FlaskConical;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium",
        real ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800",
        className,
      )}
      title={
        real
          ? "真实数据：来自题库（questions / subjects）"
          : "演示数据：C 端未落地，practice_items / exam_attempts 当前是造数"
      }
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/**
 * 缓存状态：**只在命中缓存时**显示。
 *
 * ## 为什么要给用户看
 *
 * 教研核对数据时会问："我刚改了一道题，统计怎么没变？" —— 十有八九是缓存
 * （TTL 60s ～ 1h）。把这个标出来能省掉一整轮"是不是算错了"的排查。
 *
 * ## 为什么"未命中"不显示
 *
 * 未命中 = 这次是实时算的，那是**默认状态**。默认状态占位置会稀释注意力，
 * 而且用户会开始怀疑"没标的是不是也有问题"。所以只在**偏离默认**时提示。
 */
export function CachedBadge({ meta, className }: { meta: StatsMeta; className?: string }) {
  if (!meta.cached) return null;
  const seconds = meta.ttl_sec;
  const human =
    seconds >= 3600 ? `${Math.round(seconds / 3600)} 小时` : `${Math.round(seconds / 60)} 分钟`;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600",
        className,
      )}
      title={`本页数字来自缓存，最多 ${human} 内的数据（后端没有重新查库）`}
    >
      <RefreshCw className="h-3 w-3" />
      缓存（{human}内）
    </span>
  );
}

/** 口径告警条：`meta.warnings` 非空时才出现。后端**自己说出来**，不让人凭肉眼觉得"有点怪"。 */
export function StatsWarnings({ meta, className }: { meta: StatsMeta; className?: string }) {
  if (!meta.warnings?.length) return null;
  return (
    <ul
      className={cn(
        "space-y-1 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-900",
        className,
      )}
    >
      {meta.warnings.map((w) => (
        <li key={w}>· {w}</li>
      ))}
    </ul>
  );
}
