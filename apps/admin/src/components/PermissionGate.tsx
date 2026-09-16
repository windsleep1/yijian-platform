"use client";

import { cloneElement, type ReactElement } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

type GateChild = ReactElement<{ disabled?: boolean; className?: string }>;

type Props = {
  /** 需要的权限码；命中任一即通过 */
  code: string | string[];
  /**
   * 无权限时：
   *   - `disable`（默认，B 端主用）渲染但置灰 + tooltip 说明原因
   *   - `hide` 直接不渲染（用于"整块功能区"这种没有禁用语义的场景）
   */
  mode?: "hide" | "disable";
  /** disable 时的解释文案。**必须写清"为什么不能点"和"该找谁"**，这是 B 端素养。 */
  reason?: string;
  children: GateChild;
};

/**
 * 权限门控原语。
 *
 * ## 为什么必须套一层 `<span>`
 *
 * 这是本文件存在的全部理由。`disabled` 的按钮**不派发 pointer 事件**
 * （shadcn 的 Button 还额外加了 `disabled:pointer-events-none`，
 * 连 `pointerenter` 都不会冒泡到它身上）。所以如果把 `<TooltipTrigger asChild>`
 * 直接挂在按钮上，悬停时 tooltip **永远不会出现** —— 表现为"加了 tooltip 但没反应"，
 * 排查起来很容易怀疑到 radix 版本上去。
 *
 * 正确做法：让外层 `<span className="inline-block">` 承接鼠标事件，按钮只负责"灰着"。
 *
 * ## 为什么还是"禁用"而不是"隐藏"
 *
 * 隐藏会让用户根本不知道有这个功能（也无法自我判断该不该申请权限）；
 * 禁用 + 说清原因，才是"不给用户制造必然失败的操作"。
 */
export function PermissionGate({ code, mode = "disable", reason, children }: Props) {
  const { hasAnyPermission, isLoading } = useAuth();
  const codes = Array.isArray(code) ? code : [code];

  // 权限尚未确定时一律按"无权限"处理。
  // 宁可先灰一会儿，也不能先亮起来让用户点 —— 点下去必然 403，体验更差。
  const allowed = !isLoading && hasAnyPermission(codes);

  if (allowed) return children;
  if (mode === "hide") return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("inline-block cursor-not-allowed")} aria-disabled="true">
          {cloneElement(children, { disabled: true })}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        <p className="font-medium">你没有执行此操作的权限</p>
        <p className="mt-1 text-muted-foreground">
          需要「{codes.join(" / ")}」，请联系系统管理员为你的账号开通
        </p>
        {reason ? <p className="mt-1 text-muted-foreground">{reason}</p> : null}
      </TooltipContent>
    </Tooltip>
  );
}
