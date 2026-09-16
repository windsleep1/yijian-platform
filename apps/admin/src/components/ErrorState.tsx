"use client";

import { AlertTriangle, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 出错态。
 *
 * 比空态的差别：**必须给出可执行的下一步**（重试 / 复制 trace_id），
 * 而不是丢一句"加载失败"。B 端用户是内部同事，他们能自己判断要不要找后端，
 * 前提是你把 trace_id 给他。
 */
export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const isApi = error instanceof ApiError;
  const message = error instanceof Error ? error.message : String(error ?? "未知错误");
  const traceId = isApi ? error.traceId : "";

  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-14 text-center", className)}>
      <div className="mb-3 rounded-full bg-destructive/10 p-3">
        <AlertTriangle className="h-6 w-6 text-destructive" />
      </div>
      <p className="text-sm font-medium text-foreground">加载失败</p>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">{message}</p>

      {isApi && error.httpStatus === 403 ? (
        <p className="mt-2 max-w-md text-xs text-muted-foreground">
          这是权限不足。请确认当前账号的角色是否包含该页面所需权限，或联系系统管理员。
        </p>
      ) : null}

      {traceId ? (
        <button
          type="button"
          onClick={() => void navigator.clipboard?.writeText(traceId)}
          className="yj-json mt-3 rounded border bg-muted px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted/70"
          title="点击复制 trace_id"
        >
          trace_id: {traceId}
        </button>
      ) : null}

      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          <RotateCw className="h-3.5 w-3.5" />
          重试
        </Button>
      ) : null}
    </div>
  );
}
