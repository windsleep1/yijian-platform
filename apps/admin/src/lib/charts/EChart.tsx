"use client";

import dynamic from "next/dynamic";

import type { EChartBaseProps } from "./EChartBase";

/**
 * 图表封装的**唯一业务入口**：`import { EChart } from "@/lib/charts/EChart"`。
 *
 * 业务侧**只传 `option`**。将来换库只改 `lib/charts/` 这一层，业务组件一行不动 ——
 * "只传 option"是**可逆性的实现方式**，不是风格偏好（`docs/20` §5）。
 *
 * ## 为什么内部要 `ssr: false`
 *
 * ECharts 初始化需要真实 DOM 与 canvas。App Router 默认会在**服务端**渲染客户端组件，
 * 那时 `document` 不存在 —— 直接 `echarts.init` 会在 SSR 阶段炸。
 * 用 `next/dynamic(..., { ssr: false })` 把它推迟到浏览器执行，
 * 并且**由这一层替业务侧处理掉**（业务侧不需要知道这件事，也不需要自己写 dynamic）。
 */
const Impl = dynamic(() => import("./EChartBase").then((m) => m.EChartBase), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse rounded-md bg-muted" />,
});

export type EChartProps = EChartBaseProps;

export function EChart(props: EChartProps) {
  return <Impl {...props} />;
}
