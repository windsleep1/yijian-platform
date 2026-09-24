"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { request } from "@/lib/api";
import type {
  StatsDistributions,
  StatsDistributionsQuery,
  StatsOverview,
  StatsOverviewQuery,
  StatsTrends,
  StatsTrendsQuery,
  StatsWeakPoints,
} from "@/lib/types";

/**
 * stats 的 query key。
 *
 * 与 `qk`（`useUsers.ts`）同风格 —— **集中定义**，避免散落的字符串写错：
 * 键不一致的后果是"改了筛选没反应"，而且**不报错**（TanStack Query 只是当成新 key）。
 * 单独建一个 `statsQk` 而不是塞进 `qk`：`qk` 定义在 `useUsers.ts`（用户模块）里，
 * 往里加看板的键会让"用户模块的 hook 文件"长出别的模块的东西。
 */
export const statsQk = {
  overview: (q: StatsOverviewQuery) => ["stats", "overview", q] as const,
  trends: (q: StatsTrendsQuery) => ["stats", "trends", q] as const,
  distributions: (q: StatsDistributionsQuery) => ["stats", "distributions", q] as const,
  weakPoints: (q: unknown) => ["stats", "weak-points", q] as const,
};

/**
 * 指标卡（5 张）。需要 `stats:read`。
 *
 * ★ `staleTime` 与**后端的缓存 TTL** 对齐（overview = 60s）。两者管的是不同层：
 * 后端缓存是**多用户共享**的，前端这个是**本标签页**的。不对齐的话，
 * 用户切走再切回来会立刻重新请求 —— 后端虽然命中缓存（不查库），
 * 但**网络往返仍在**，白等一次。
 */
export function useOverview(q: StatsOverviewQuery = {}) {
  return useQuery({
    queryKey: statsQk.overview(q),
    queryFn: () => request<StatsOverview>("/admin/stats/overview", { query: q }),
    staleTime: 60_000,
  });
}

/** 趋势。切 metric / 粒度时用 `placeholderData` 保留旧图，避免整块闪成骨架屏。 */
export function useTrends(q: StatsTrendsQuery) {
  return useQuery({
    queryKey: statsQk.trends(q),
    queryFn: () => request<StatsTrends>("/admin/stats/trends", { query: q }),
    placeholderData: keepPreviousData,
    staleTime: 300_000,
  });
}

/**
 * 分布。`view=bank` 取**题库结构**（真实数据）、`view=practice` 取**作答分布**（演示数据）。
 *
 * ⚠️ `view` 会影响后端的 `meta.data_origin`，但**真假是后端判的**：
 * 前端只渲染 `meta.origin_label`，不许自己按 `view` 反推。
 */
export function useDistributions(q: StatsDistributionsQuery) {
  return useQuery({
    queryKey: statsQk.distributions(q),
    queryFn: () => request<StatsDistributions>("/admin/stats/distributions", { query: q }),
    placeholderData: keepPreviousData,
    staleTime: 3_600_000,
  });
}

/** 薄弱知识点（S3 才上前端；这里先备好，页面暂不渲染）。 */
export function useWeakPoints(q: { min_sample?: number; limit?: number } = {}) {
  return useQuery({
    queryKey: statsQk.weakPoints(q),
    queryFn: () => request<StatsWeakPoints>("/admin/stats/weak-points", { query: q }),
    staleTime: 600_000,
  });
}
