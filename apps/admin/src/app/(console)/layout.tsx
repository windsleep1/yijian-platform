"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type ReactNode } from "react";

import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth-context";
import { hasToken } from "@/lib/auth-store";

/**
 * 第二层守卫：**真正的登录判定**。
 *
 * middleware 那层只看 cookie 提示位（体验），这层看 `/auth/me` 的结果（事实）。
 * 三种状态必须分开处理，混在一起就会出现"白屏"：
 *   1. 还没确定 → 骨架屏（**不要**渲染 children，否则会闪出无权访问的内容）
 *   2. 没登录 → 跳登录页，并带上 `?next=` 回跳
 *   3. 加载失败 → 错误态 + 重试（**不要**当成"未登录"直接踢走，
 *      否则后端一抖，管理员就被登出了）
 */
function RequireAuth({ children }: { children: ReactNode }) {
  const { me, isLoading, error, refreshMe } = useAuth();
  const router = useRouter();
  const sp = useSearchParams();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!mounted || isLoading) return;
    if (!me && !hasToken()) {
      // 回跳目标默认给 `/`（控制台首页）而不是写死某个模块 ——
      // 首页会按登录后的权限挑落地页，写死会让"只有题库权限"的角色被弹到 403。
      const next = sp.get("next") ?? "/";
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [mounted, isLoading, me, router, sp]);

  if (!mounted || isLoading) {
    return (
      <div className="flex min-h-screen">
        <div className="hidden w-56 shrink-0 border-r bg-card p-3 md:block">
          <Skeleton className="mb-4 h-8 w-full" />
          <Skeleton className="mb-2 h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
        <div className="flex-1 p-6">
          <Skeleton className="mb-4 h-8 w-48" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (!me) {
    if (error) {
      return (
        <div className="flex min-h-screen items-center justify-center p-6">
          <div className="w-full max-w-lg rounded-lg border bg-card">
            <ErrorState error={error} onRetry={() => void refreshMe()} />
          </div>
        </div>
      );
    }
    // 已触发跳转，避免闪一帧空白
    return null;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="min-w-0 flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

export default function ConsoleLayout({ children }: { children: ReactNode }) {
  // useSearchParams 需要 Suspense 边界，否则 next build 会报
  // "useSearchParams() should be wrapped in a suspense boundary"。
  return (
    <Suspense fallback={null}>
      <RequireAuth>{children}</RequireAuth>
    </Suspense>
  );
}
