"use client";

import { ShieldX } from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import { roleLabel } from "@/lib/permission";

/**
 * 整页 403。
 *
 * 关键点：**必须说清"你没有哪个权限"和"你当前是什么角色"。**
 * 只写"无权访问"，用户只能来问开发；写清楚了，他自己就知道该找谁开通。
 * 这也是 `researcher` / `teacher` / `student` 登录后会看到的页面 ——
 * 他们能登录，但后台没有一处对他们开放。
 */
export function ForbiddenState({ need }: { need?: string }) {
  const { roles, isLoading } = useAuth();

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
      <div className="mb-4 rounded-full bg-amber-100 p-4">
        <ShieldX className="h-8 w-8 text-amber-700" />
      </div>
      <h2 className="text-lg font-semibold">没有访问权限</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        {need ? (
          <>
            访问这个页面需要 <code className="yj-json rounded bg-muted px-1.5 py-0.5">{need}</code> 权限。
          </>
        ) : (
          "当前账号没有被授权访问这个页面。"
        )}
      </p>
      <p className="mt-3 text-sm text-muted-foreground">
        当前角色：
        {isLoading ? (
          "加载中…"
        ) : roles.length ? (
          <span className="ml-1 font-medium text-foreground">
            {roles.map(roleLabel).join("、")}
          </span>
        ) : (
          <span className="ml-1">无</span>
        )}
      </p>
      <p className="mt-4 max-w-md text-xs leading-relaxed text-muted-foreground">
        如果你确实需要这个权限，请联系系统管理员（超级管理员）为你的账号分配包含该权限的角色。
      </p>
    </div>
  );
}
