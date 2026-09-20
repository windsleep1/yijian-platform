"use client";

import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export type WizardStep = {
  key: string;
  title: string;
  /** 一句话说明这一步要做什么，避免用户猜"下一步会发生什么" */
  description?: string;
};

type Props = {
  steps: WizardStep[];
  /** 当前步骤下标（0 基） */
  current: number;
  /**
   * **已到达过的最大下标**。
   *
   * 这是"不可跳步"的实现依据：只有 `index <= maxReached` 的步骤可点。
   * 用 `current` 单独判是不够的 —— 从第 3 步回退到第 2 步之后，
   * 第 3 步仍然是"合法的已到达步骤"，用户应该能再点回去；
   * 而第 4 步（没到过）必须不可点。
   */
  maxReached: number;
  onStepClick?: (index: number) => void;
  className?: string;
};

/**
 * 步骤条（向导骨架）。
 *
 * 三条硬要求，逐条对应实现：
 *
 *  1. **当前步骤高亮** —— `aria-current="step"` + 主色描边（也照顾读屏）。
 *  2. **已完成打勾** —— `index < current` 渲染 Check 图标，而不是只把数字变灰；
 *     "打勾"和"变灰"在扫一眼时的信息量差很多。
 *  3. **不可跳步** —— 未到达的步骤是 `disabled`，连 hover 反馈都不给，
 *     避免用户以为"点了没反应"是页面卡了。
 *
 * 刻意不做"横向滚动的窄屏适配"：本向导只有 3 步，窄屏换成竖排即可，
 * 引一套 carousel 反而更难读。
 */
export function StepWizard({ steps, current, maxReached, onStepClick, className }: Props) {
  return (
    <ol
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-card p-4 sm:flex-row sm:items-stretch sm:gap-0",
        className,
      )}
    >
      {steps.map((step, i) => {
        const isDone = i < current;
        const isCurrent = i === current;
        const reachable = i <= maxReached;
        const clickable = !!onStepClick && reachable && !isCurrent;

        const inner = (
          <>
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold transition-colors",
                isDone && "border-primary bg-primary text-primary-foreground",
                isCurrent && "border-primary bg-background text-primary ring-2 ring-primary/25",
                !isDone && !isCurrent && "border-muted-foreground/30 text-muted-foreground",
              )}
            >
              {isDone ? <Check className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className="min-w-0 text-left">
              <span
                className={cn(
                  "block text-sm font-medium",
                  isCurrent
                    ? "text-foreground"
                    : isDone
                      ? "text-foreground"
                      : "text-muted-foreground",
                )}
              >
                {step.title}
              </span>
              {step.description ? (
                <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">
                  {step.description}
                </span>
              ) : null}
            </span>
          </>
        );

        return (
          <li
            key={step.key}
            className={cn("flex min-w-0 flex-1 items-start gap-2.5", "sm:items-center")}
            aria-current={isCurrent ? "step" : undefined}
          >
            {clickable ? (
              <button
                type="button"
                onClick={() => onStepClick?.(i)}
                title={`返回「${step.title}」`}
                className="flex min-w-0 items-start gap-2.5 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-accent/60 sm:items-center"
              >
                {inner}
              </button>
            ) : (
              <span
                className={cn(
                  "flex min-w-0 items-start gap-2.5 px-1 py-0.5 sm:items-center",
                  !reachable && "opacity-60",
                )}
                // 未到达的步骤给一句明确的话，而不是让用户对着一个死按钮猜
                title={!reachable ? "请先完成前面的步骤" : undefined}
              >
                {inner}
              </span>
            )}

            {/* 连接线：最后一步不画 */}
            {i < steps.length - 1 ? (
              <span
                aria-hidden
                className={cn(
                  "mx-2 hidden h-px flex-1 sm:block",
                  isDone ? "bg-primary/50" : "bg-border",
                )}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
