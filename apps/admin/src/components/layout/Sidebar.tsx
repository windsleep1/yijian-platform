"use client";

import { BookOpen, ClipboardList, ListChecks, ScrollText, ShieldCheck, Upload, Users, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/lib/auth-context";
import { MODULE_ENTRIES } from "@/lib/permission";
import { cn } from "@/lib/utils";

/**
 * 菜单项来自 `MODULE_ENTRIES`（**唯一真相**）。
 *
 * 刻意不再单独维护一份 NAV：菜单顺序、入口权限、登录落地页三者必须一致，
 * 分开写迟早会出现"菜单里有的模块，登录后跳不过去"这类不一致。
 *
 * 图标留在视图层 —— `lib/permission.ts` 是纯逻辑，不该 import 一个图标库。
 */
const ICONS: Record<string, LucideIcon> = {
  "/questions": BookOpen,
  "/exams": ClipboardList,
  "/paper-rules": ListChecks,
  "/imports": Upload,
  "/users": Users,
  "/audit-logs": ScrollText,
};

/**
 * 侧边栏。
 *
 * **菜单按权限隐藏**（`mode="hide"` 的场景）：如果 `viewer` 看得到"审计日志"菜单
 * 但点进去是 403，那是纯粹的误导。能看到 = 能进，这是 B 端导航的基本契约。
 *
 * 注意：这里只是**导航层**的过滤，真正的门在 `(console)/layout.tsx` 的
 * `RequireAuth` 与各页面的权限判断，以及后端 RBAC。隐藏菜单不是安全措施。
 */
export function Sidebar() {
  const pathname = usePathname();
  const { hasPermission, isLoading, roles } = useAuth();

  const items = MODULE_ENTRIES.filter((n) => hasPermission(n.perm));

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r bg-card">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <ShieldCheck className="h-5 w-5 text-primary" />
        <span className="text-sm font-semibold">一建通 · 后台</span>
      </div>

      <nav className="flex-1 space-y-1 p-2">
        {isLoading ? (
          <div className="space-y-1 p-1">
            <div className="h-9 animate-pulse rounded-md bg-muted" />
            <div className="h-9 animate-pulse rounded-md bg-muted" />
          </div>
        ) : items.length ? (
          items.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            const Icon = ICONS[item.href] ?? ShieldCheck;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })
        ) : (
          <div className="rounded-md border border-dashed p-3 text-xs leading-relaxed text-muted-foreground">
            当前角色没有任何后台菜单可访问。
            <span className="mt-1 block">
              你的角色：{roles.length ? roles.join("、") : "无"}
            </span>
            <span className="mt-1 block">请联系系统管理员分配权限。</span>
          </div>
        )}
      </nav>

      <div className="border-t p-3">
        <div className="flex items-center gap-1.5">
          <Badge variant="outline" className="text-[10px]">
            v0.1 · Batch 6
          </Badge>
        </div>
      </div>
    </aside>
  );
}
