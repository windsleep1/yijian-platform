"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ForbiddenState } from "@/components/ForbiddenState";
import { useAuth } from "@/lib/auth-context";
import { landingPath } from "@/lib/permission";

/**
 * 控制台首页 —— **按权限**挑一个真正进得去的模块落地。
 *
 * 这里过去是 `redirect("/users")`，在只有"用户 / 审计"两个模块时没问题。
 * Batch 4 加了题库之后它就成了 bug：`researcher`（教研）只有 `question:read`，
 * 进控制台被硬塞到 `/users`，随即吃一个 403 ——
 * **明明有权限的模块就在旁边，用户却只看到"没有访问权限"。**
 *
 * 现在改成：能从权限推导出落地页就跳过去；一个模块都无权限才渲染 403。
 * 顺序由 `MODULE_ENTRIES` 决定（题库在前）。
 */
export default function ConsoleIndex() {
  const { permissions, isLoading } = useAuth();
  const router = useRouter();
  const target = landingPath(permissions);

  useEffect(() => {
    if (!isLoading && target) router.replace(target);
  }, [isLoading, target, router]);

  if (!isLoading && !target) return <ForbiddenState />;

  return (
    <div className="flex items-center gap-2 py-20 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      正在进入控制台…
    </div>
  );
}
