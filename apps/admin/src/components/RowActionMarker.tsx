"use client";

import { AlertCircle, Check, Loader2, RotateCw } from "lucide-react";

import type { RowFeedback } from "@/hooks/useRowActionFeedback";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * 行内状态标记：**进行中 / 已完成 / 失败**三态。
 *
 * 与 `useRowActionFeedback` 配套 —— hook 管状态与流程，这个组件管"长什么样"。
 * 放在表格的操作列或状态列里，宽度变化要小（行高不能跳）。
 *
 * 三态的视觉约定：
 * - **pending**：转圈的 loader + 灰字。同时把该行的操作按钮禁掉（由调用方做）。
 * - **done**：绿色对勾 + 文案，整行**淡出**（`fadeClassFor`）。给的是"成了"的正反馈，
 *   延时之后这一行才真的消失 —— 顺序不能反。
 * - **error**：红色感叹号 + 一行原因（放不下就 Tooltip 展开全文）+ **重试**。
 *   ⚠️ 重试键只在操作**幂等**时才该出现，由 hook 的 `retryable` 决定。
 */
export function RowActionMarker({
  feedback,
  className,
}: {
  feedback: RowFeedback | undefined;
  className?: string;
}) {
  if (!feedback) return null;

  if (feedback.phase === "pending") {
    return (
      <span
        className={cn("inline-flex items-center gap-1 text-xs text-muted-foreground", className)}
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {feedback.label}
      </span>
    );
  }

  if (feedback.phase === "done") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1 text-xs font-medium text-emerald-700",
          className,
        )}
      >
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-600 text-white">
          <Check className="h-3 w-3" />
        </span>
        {feedback.label}
      </span>
    );
  }

  // ---- error ----
  return (
    <div className={cn("flex max-w-[260px] flex-col gap-0.5", className)}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-start gap-1 text-xs text-destructive">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="line-clamp-2 cursor-help text-left">{feedback.error}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm">
          {/* 后端的 40901 消息里本来就带了"下一步怎么做"，直接全文展示，不再自己拼 */}
          <p className="whitespace-pre-wrap text-xs">{feedback.error}</p>
        </TooltipContent>
      </Tooltip>
      {feedback.retry ? (
        <button
          type="button"
          onClick={(e) => {
            // 行本身可能有 onClick（进详情），别让重试顺带跳走
            e.stopPropagation();
            feedback.retry?.();
          }}
          className="inline-flex w-fit items-center gap-1 text-[11px] text-primary hover:underline"
        >
          <RotateCw className="h-3 w-3" />
          重试
        </button>
      ) : null}
    </div>
  );
}

/**
 * 整行的附加样式。
 *
 * - **done** → 淡出（配合延时移除，视觉上"完成并退场"）
 * - **pending** → 轻微变暗，提示"这一行正在忙"
 * - **error** → 淡红底，让失败的那一行在长表格里能被一眼找到
 *
 * 单独导出，是因为 `DataTable` 需要 `rowClassName` 回调 ——
 * 表格组件不该知道"反馈"这个概念，它只需要一个 className。
 */
export function rowClassFor(feedback: RowFeedback | undefined): string {
  if (!feedback) return "";
  if (feedback.phase === "done") {
    return "opacity-45 transition-opacity duration-500";
  }
  if (feedback.phase === "pending") {
    return "opacity-70";
  }
  return "bg-destructive/5";
}
