"use client";

/**
 * ECharts 的**薄封装**（内层实现）。
 *
 * 业务侧**不要**直接 import 这个文件 —— 用 `@/lib/charts/EChart`（外层做了 `ssr: false`）。
 *
 * ## 为什么是"只传 option"
 *
 * 这一层存在的唯一理由是**可逆性**：将来换图表库（Recharts / Chart.js）时，
 * 只改这一个文件 + option 映射，业务组件一行不动。
 * 所以它**只暴露 `option` 与尺寸**，**不暴露 ref、不暴露 echarts 实例** ——
 * 一旦业务侧能拿到实例，就会有人绕过这一层直接调 API，可逆性立刻消失。
 * ⇒ "只传 option"是**可逆性的实现方式**，不是风格偏好（`docs/20` §5）。
 *
 * ## 按需引入（**不是** `import * as echarts from "echarts"`）
 *
 * 只 `use()` 注册真正用到的：折线 / 柱状 / 环形 + 网格 / 提示 / 图例 + Canvas 渲染器。
 * 全量引入会把整个 echarts（地图、3D、所有图表类型）打进 bundle。
 * `docs/20` §5 把这条写成了硬约束，验收时量真实的 gzip 增量。
 */

import type { EChartsOption } from "echarts";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

export type EChartBaseProps = {
  /** **唯一的业务入参**：一份完整的 ECharts option。 */
  option: EChartsOption;
  /** 高度（px）。宽度永远撑满父容器。 */
  height?: number;
  className?: string;
  /** 无障碍标签（会写到 canvas 的 aria-label 上）。 */
  ariaLabel?: string;
};

export function EChartBase({ option, height = 280, className, ariaLabel }: EChartBaseProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const chart = echarts.init(host);
    chartRef.current = chart;

    // 容器尺寸变化时 ECharts **不会自己跟**（canvas 是固定像素）—— 必须显式 resize。
    // 用 ResizeObserver 而不是 `window.resize`：侧边栏折叠时窗口尺寸没变、容器变了，
    // 只监听 window 的话图会一直按旧宽度画。
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host);

    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  // `notMerge = true`：换 metric / 粒度时旧 series 必须被**替换**而不是**合并**，
  // 否则上一份数据会残留在图上 —— 看着像"数据没刷新"，很难查。
  useEffect(() => {
    chartRef.current?.setOption(option, true);
  }, [option]);

  return (
    <div ref={hostRef} className={className} style={{ height }} role="img" aria-label={ariaLabel} />
  );
}
