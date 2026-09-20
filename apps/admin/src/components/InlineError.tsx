"use client";

import { AlertTriangle, RotateCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  /** 这一步在做什么，用"上传"而不是"操作" —— 用户才知道重试的是哪一步 */
  title: string;
  error: unknown;
  onRetry?: () => void;
  retrying?: boolean;
  /** 补充一句"你可以怎么办" */
  hint?: ReactNode;
  className?: string;
};

/**
 * 行内失败提示（带重试）。
 *
 * 为什么不能只靠全局的 error toast（`providers.tsx` 的 MutationCache）：
 *
 *  1. **toast 会消失。** 8 秒后它就没了，而用户可能正好在切窗口看 Excel。
 *     回来只看到"什么都没发生"，不知道刚才到底成没成。
 *  2. **toast 不区分是哪一步。** 上传失败与执行失败都弹"服务端出错了"，
 *     而这两件事的下一步动作完全不同。
 *  3. **toast 上放不下 trace_id + 重试按钮 + 排查建议**这三件 B 端用户真正需要的东西。
 *
 * 所以：**toast 负责"提醒"，这个组件负责"解释与恢复"**，两者都在，不冲突。
 *
 * 这里刻意区分了三类失败，因为它们对应三种不同的处理方式：
 *   - **断网 / 服务不可达**（非 ApiError，fetch 自己抛）：先去看后端起没起、网通不通；
 *   - **网关错误**（HTTP 有响应但不是本站信封，如 502 HTML）：多半是中间层挂了；
 *   - **业务错误**（`ApiError`，有 code 与 trace_id）：拿 trace_id 找后端。
 */
export function InlineError({ title, error, onRetry, retrying, hint, className }: Props) {
  // 显式取一次而不是靠 `isApi` 别名收窄：嵌套三元里 TS 的别名收窄容易失效，
  // 直接判一次最稳（`api === null` 就一定是"请求根本没发出去"）。
  const api = error instanceof ApiError ? error : null;
  const message = error instanceof Error ? error.message : String(error ?? "未知错误");

  const headline = !api
    ? "请求没有发出去（网络或后端不可达）"
    : api.httpStatus >= 500
      ? "服务端出错了"
      : api.httpStatus === 403
        ? "没有这一步的操作权限"
        : message;

  return (
    <div
      role="alert"
      className={cn("rounded-lg border border-destructive/40 bg-destructive/5 p-4", className)}
    >
      <div className="flex items-start gap-2.5">
        <div className="mt-0.5 rounded-full bg-destructive/10 p-1.5">
          {!api ? (
            <WifiOff className="h-4 w-4 text-destructive" />
          ) : (
            <AlertTriangle className="h-4 w-4 text-destructive" />
          )}
        </div>
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-sm font-medium">
            {title}失败：{headline}
          </p>

          {!api ? (
            <p className="text-xs leading-relaxed text-muted-foreground">
              {message}。请检查网络连接、后端是否已启动，以及 `NEXT_PUBLIC_API_BASE`
              是否指向正确的地址。网络恢复后可以直接重试，这次操作没有对题库产生任何影响。
            </p>
          ) : (
            <p className="text-xs leading-relaxed text-muted-foreground">{message}</p>
          )}

          {hint ? (
            <div className="text-xs leading-relaxed text-muted-foreground">{hint}</div>
          ) : null}

          {api?.traceId ? (
            <p className="yj-json text-[11px] text-muted-foreground">trace_id: {api.traceId}</p>
          ) : null}

          {onRetry ? (
            <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
              <RotateCw className={cn("h-3.5 w-3.5", retrying && "animate-spin")} />
              {retrying ? "重试中…" : "重试"}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
