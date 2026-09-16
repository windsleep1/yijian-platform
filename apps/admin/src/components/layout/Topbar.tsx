"use client";

import { LogOut, RefreshCw, UserCog } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth-context";
import { roleLabel } from "@/lib/permission";

/** 顶栏：当前用户 + 角色徽章 + 刷新权限 + 退出。 */
export function Topbar() {
  const { me, roles, isLoading, logout, refreshMe } = useAuth();

  const reload = async () => {
    try {
      await refreshMe();
      toast.success("权限已刷新");
    } catch {
      // 统一错误 toast 已在 QueryCache 里处理（这里走的是直接 request，手动补一条）
      toast.error("刷新失败", { description: "请稍后重试，或重新登录。" });
    }
  };

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b bg-card px-5">
      <div className="text-sm text-muted-foreground">
        {isLoading ? (
          <span className="inline-block h-4 w-32 animate-pulse rounded bg-muted" />
        ) : (
          <>管理员控制台</>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void reload()}
          // 权限可能被别的管理员改掉了，给一个显式"重新拉取"的入口。
          // 后端改了角色会清 Redis 权限缓存，但浏览器里这份还得主动刷。
          title="重新拉取当前账号的角色与权限"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          刷新权限
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="gap-2">
              <UserCog className="h-3.5 w-3.5" />
              {me?.nickname || me?.phone || "未登录"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuLabel>
              <div className="space-y-1">
                <div className="text-sm font-medium">{me?.nickname || "—"}</div>
                <div className="yj-json text-[11px] font-normal text-muted-foreground">
                  {me?.phone ?? "—"}
                </div>
                <div className="yj-json text-[11px] font-normal text-muted-foreground">
                  ID {me?.id ?? "—"}
                </div>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5">
              <div className="mb-1 text-[11px] text-muted-foreground">当前角色</div>
              <div className="flex flex-wrap gap-1">
                {roles.length ? (
                  roles.map((r) => (
                    <Badge key={r} variant="secondary" className="text-[10px]">
                      {roleLabel(r)}
                    </Badge>
                  ))
                ) : (
                  <span className="text-[11px] text-muted-foreground">无</span>
                )}
              </div>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => void logout()}
              className="text-destructive focus:text-destructive"
            >
              <LogOut className="h-4 w-4" />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
