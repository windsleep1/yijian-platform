"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { useState, type ReactNode } from "react";
import { Toaster, toast } from "sonner";

import { ApiError } from "@/lib/api";
import { AuthProvider } from "@/lib/auth-context";

/**
 * 统一的错误提示出口。
 *
 * 全站**只有这一个地方**弹错误 toast —— 页面里不写 try/catch + toast，
 * 否则同一个 403 会被三处各弹一次。做法是把 toast 挂到 QueryCache/MutationCache 的
 * onError 上，所有 TanStack Query 的失败的请求自动汇聚到这里。
 *
 * 三件必须做对的事：
 *   1. 提示里带 **trace_id**，并且可以一键复制 —— 用户报障时能直接给后端定位。
 *   2. **已跳登录页的错误不再弹**（`isAuthRedirect`），否则"登录过期"会连弹三条。
 *   3. 403 与 5xx 用不同语气，别把所有错误都说成"网络错误"。
 */
function buildQueryClient() {
  const onError = (error: unknown) => {
    if (error instanceof ApiError) {
      // 已经走了跳登录链路，弹窗没有意义
      if (error.isAuthRedirect) return;

      const title =
        error.httpStatus === 403
          ? "没有操作权限"
          : error.httpStatus >= 500
            ? "服务端出错了"
            : error.message;

      toast.error(title, {
        description:
          error.httpStatus === 403
            ? `${error.message}。需要的权限请联系系统管理员开通。`
            : error.message,
        duration: error.httpStatus === 403 ? 6000 : 8000,
        action: error.traceId
          ? {
              label: "复制 trace_id",
              onClick: () => {
                void navigator.clipboard?.writeText(error.traceId);
                toast.success("trace_id 已复制");
              },
            }
          : undefined,
      });
      return;
    }

    // 非 ApiError：fetch 自身失败（断网、DNS、被 CORS 拦）
    const message = error instanceof Error ? error.message : String(error);
    toast.error("请求发送失败", {
      description: `${message}。请检查后端是否已启动，以及 NEXT_PUBLIC_API_BASE 是否配置正确。`,
      duration: 10000,
    });
  };

  return new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        // 业务错误重试没有意义；网络/网关抖动值得再试两次
        retry: (count, error) => (error instanceof ApiError ? false : count < 2),
        refetchOnWindowFocus: false,
        staleTime: 15_000,
      },
      mutations: { retry: false },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  // useState 而不是模块级变量：避免多个请求/多次渲染共享同一个 client
  const [client] = useState(buildQueryClient);

  return (
    <QueryClientProvider client={client}>
      <AuthProvider>
        <TooltipPrimitive.Provider delayDuration={150} skipDelayDuration={300}>
          {children}
          <Toaster position="top-center" richColors closeButton />
        </TooltipPrimitive.Provider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
