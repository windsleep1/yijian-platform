"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { BankStructureChart } from "@/components/stats/BankStructureChart";
import { MetricCards } from "@/components/stats/MetricCards";
import { StatsWarnings } from "@/components/stats/StatsMetaBadges";
import { TrendChart } from "@/components/stats/TrendChart";
import { useDistributions, useOverview, useTrends } from "@/hooks/useStats";
import { useAuth } from "@/lib/auth-context";
import { P } from "@/lib/permission";
import type { StatsGranularity, StatsTrendMetric } from "@/lib/types";

/**
 * 统计看板（Batch 8 / S2）。
 *
 * ## 页面结构
 *
 * ```
 * 指标卡 ×5          ← 今日 / 昨日 / 环比（数据源：practice_items + exam_attempts + users）
 * 趋势              ← 可切指标与粒度（同上）
 * 题库结构 · 科目分布 ← ★ 唯一来自**真实数据**（questions / subjects）
 * ```
 *
 * ## ★ 这一屏里"真数据"和"造数据"是**混在一起**的
 *
 * C 端还没落地，所以 `practice_items` / `exam_attempts` 里是造数；
 * 而题库（`questions` / `subjects`）是真的。⇒ **同一屏既有真数又有造数。**
 *
 * 处理办法（`docs/20` §4.1 定的两条）：
 * 1. **每个区块标题旁带标记**（`OriginBadge`），文案取自后端的 `meta.origin_label`；
 * 2. **页面顶部再放一条总说明**（就是下面那段）。
 *
 * 为什么不"干脆都标成假的"或"都标成真的"：两者都会让看的人得出**错误的结论**
 * —— 前者让人以为题库也是造的（可能就不敢用了），后者更糟（把造数当真实经营数据）。
 *
 * ## 权限门控为什么放在父组件
 *
 * `DashboardContent` 里的三个查询都会打后端。如果把"无权限"的判断写在同一个组件里、
 * 放在 hooks 之后，那三个请求**照样会发出去**（然后被后端 403）。
 * 拆一层之后，无权限时子组件**根本不挂载** → 一个请求都不发。
 */
export default function DashboardPage() {
  const { hasPermission, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-20 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载权限…
      </div>
    );
  }

  if (!hasPermission(P.statsRead)) {
    return <ForbiddenState need="stats:read" />;
  }

  return <DashboardContent />;
}

function DashboardContent() {
  const [metric, setMetric] = useState<StatsTrendMetric>("answers");
  const [granularity, setGranularity] = useState<StatsGranularity>("day");

  // 时间窗口**不传**：交给后端用它自己的默认窗口（`DEFAULT_WINDOW_DAYS`）。
  // 前端自己算日期只会多一处时区/边界 bug（"筛今天却查出昨天"那类），
  // 而这里并没有"必须由前端指定窗口"的需求。
  const overview = useOverview();
  const trends = useTrends({ metric, granularity });
  const bank = useDistributions({ dim: "subject", view: "bank" });

  return (
    <div className="space-y-4">
      <PageHeader title="统计看板" description="学习数据概况 · 日界按 Asia/Shanghai" />

      <div className="rounded-md border border-dashed bg-muted/30 px-3 py-2 text-[11px] leading-snug text-muted-foreground">
        本页同时展示两类数据，每个区块标题旁都有标记：
        <span className="mx-1 font-medium text-emerald-700">真实数据</span>
        来自已灌好的题库（科目分布）；
        <span className="mx-1 font-medium text-amber-700">演示数据</span>
        来自 C 端尚未落地的作答记录，**当前是造数**，不要据此做经营判断。
      </div>

      {overview.data ? <StatsWarnings meta={overview.data.meta} /> : null}

      <MetricCards data={overview.data} isLoading={overview.isLoading} />

      <TrendChart
        data={trends.data}
        isLoading={trends.isLoading}
        metric={metric}
        granularity={granularity}
        onMetricChange={setMetric}
        onGranularityChange={setGranularity}
      />

      <BankStructureChart data={bank.data} isLoading={bank.isLoading} />
    </div>
  );
}
