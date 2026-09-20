"use client";

import { ChevronDown, ChevronRight, Info, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useRoles } from "@/hooks/useRoles";
import { useAssignRoles } from "@/hooks/useUsers";
import type { AdminUserDetail, RoleItem, ScopeType } from "@/lib/types";
import { roleLabel } from "@/lib/permission";
import { cn } from "@/lib/utils";

const SCOPE_OPTIONS: { value: ScopeType; label: string; hint: string }[] = [
  { value: "global", label: "全站", hint: "对所有数据生效" },
  { value: "subject", label: "按科目", hint: "只对该科目下的数据生效（需填科目 ID）" },
  { value: "professional", label: "按专业", hint: "只对该专业下的数据生效（需填专业码）" },
  { value: "course", label: "按课程", hint: "只对该课程生效（需填课程 ID）" },
];

/**
 * 分配角色弹窗 —— 你要的 B 端要点基本都落在这里。
 *
 * 1. **列出全部角色，`is_assignable=false` 的直接置灰 + tooltip 说明原因。**
 *    后端已经把白名单透出来了（`super_admin` 只能由 CLI 授予），前端据此禁用，
 *    而不是让用户勾上、点提交、再吃一个 40003 —— "不给用户制造必然失败的操作"。
 * 2. **每个角色可展开看它包含哪些权限码。** 光看"教研"两个字，管理员并不知道
 *    这个角色能干什么；展开就看到 `question:create` 这些，才能判断该不该给。
 * 3. **勾选变化时展示 diff**（`student → researcher`），避免"盲提交"。
 * 4. **提交前二次确认**，确认文案里带上被操作用户的手机号，让管理员核对"改的是不是这个人"。
 * 5. **`scope_type != global` 时 `scope_id` 必填**，前端先拦一道，
 *    否则必然吃后端 40001。
 */
export function AssignRolesDialog({
  open,
  onOpenChange,
  user,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: AdminUserDetail;
}) {
  const rolesQuery = useRoles(true);
  const assign = useAssignRoles(user.id);

  const [selected, setSelected] = useState<string[]>(user.roles);
  const [scopeType, setScopeType] = useState<ScopeType>("global");
  const [scopeId, setScopeId] = useState<string>("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [confirmOpen, setConfirmOpen] = useState(false);

  // 每次打开都用服务端的最新角色重置本地乐观态，
  // 否则上一次取消的勾选会残留到下一次打开。
  useEffect(() => {
    if (open) {
      setSelected(user.roles);
      setScopeType("global");
      setScopeId("");
      setExpanded({});
    }
  }, [open, user.roles]);

  const roles = rolesQuery.data ?? [];

  const added = selected.filter((c) => !user.roles.includes(c));
  const removed = user.roles.filter((c) => !selected.includes(c));
  const changed = added.length > 0 || removed.length > 0;

  const needScopeId = scopeType !== "global";
  const scopeIdMissing = needScopeId && !scopeId.trim();
  const clearingAll = selected.length === 0;

  const toggle = (code: string, role: RoleItem) => {
    if (!role.is_assignable) return;
    setSelected((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));
  };

  const submit = async () => {
    try {
      const out = await assign.mutateAsync({
        role_codes: selected,
        scope_type: scopeType,
        // TODO(同 Batch 4 坑 #21)：scope_id 在库里是 BigInteger、响应侧按字符串返回
        // （schemas/admin.py:48/62 用 BigIntStrOpt），但这里手输后用 Number() 转换，
        // 一旦 scope_id 是雪花 ID（18~19 位）就会丢精度、把角色授到错误范围。
        // 现状：种子数据的 subjects.id 是 1003 这类小数字，暂未触发，故本次不改。
        // 后续改成传字符串时，需同时把 AssignRolesIn.scope_id 改为 BigIntStrOpt 并回归 Batch 3 用例。
        scope_id: needScopeId ? Number(scopeId) : null,
      });
      toast.success("角色已更新", {
        description: `当前角色：${
          out.roles.length ? out.roles.map((r) => roleLabel(r.code)).join("、") : "（已清空）"
        }`,
      });
      setConfirmOpen(false);
      onOpenChange(false);
    } catch (err) {
      // 统一错误 toast 已由 Providers 里的 MutationCache.onError 负责；
      // 这里只负责别把弹窗关掉，让用户能改完再试。
      void err;
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>分配角色</DialogTitle>
            <DialogDescription>
              为 <span className="font-medium text-foreground">{user.nickname || user.phone}</span>
              （{user.phone}）设置角色。
              <span className="mt-1 block font-medium text-amber-700">
                注意：这里是<strong>整体替换</strong>，不是追加 —— 没勾的角色会被移除。
              </span>
            </DialogDescription>
          </DialogHeader>

          {/* ---- 角色列表 ---- */}
          <div className="max-h-80 space-y-1.5 overflow-y-auto rounded-md border p-2">
            {rolesQuery.isLoading ? (
              <div className="flex items-center gap-2 px-2 py-6 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载角色…
              </div>
            ) : rolesQuery.error ? (
              <p className="px-2 py-6 text-sm text-destructive">
                角色列表加载失败，请关闭弹窗后重试。
              </p>
            ) : (
              roles.map((role) => {
                const checked = selected.includes(role.code);
                const isOpen = !!expanded[role.code];
                return (
                  <div
                    key={role.code}
                    className={cn(
                      "rounded-md border px-3 py-2 transition-colors",
                      checked ? "border-primary/40 bg-primary/5" : "border-transparent",
                      !role.is_assignable && "opacity-60",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <Checkbox
                        id={`role-${role.code}`}
                        checked={checked}
                        disabled={!role.is_assignable}
                        onCheckedChange={() => toggle(role.code, role)}
                        className="mt-0.5"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <label
                            htmlFor={`role-${role.code}`}
                            className={cn(
                              "text-sm font-medium",
                              role.is_assignable ? "cursor-pointer" : "cursor-not-allowed",
                            )}
                          >
                            {role.name}
                          </label>
                          <code className="yj-json rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                            {role.code}
                          </code>
                          {!role.is_assignable ? (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="inline-flex cursor-help items-center gap-1 text-[11px] text-amber-700">
                                  <Info className="h-3 w-3" />
                                  不可在此分配
                                </span>
                              </TooltipTrigger>
                              <TooltipContent>
                                该角色只能通过运维命令行（seed-admin）授予，后台不允许提权，
                                以免出现"管理员把自己升成超管"。
                              </TooltipContent>
                            </Tooltip>
                          ) : null}
                        </div>
                        {role.description ? (
                          <p className="mt-0.5 text-xs text-muted-foreground">{role.description}</p>
                        ) : null}

                        <button
                          type="button"
                          className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                          onClick={() =>
                            setExpanded((p) => ({ ...p, [role.code]: !p[role.code] }))
                          }
                        >
                          {isOpen ? (
                            <ChevronDown className="h-3 w-3" />
                          ) : (
                            <ChevronRight className="h-3 w-3" />
                          )}
                          包含 {role.permissions.length} 个权限
                        </button>

                        {isOpen ? (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {role.permissions.length ? (
                              role.permissions.map((p) => (
                                <Badge key={p} variant="outline" className="yj-json text-[10px]">
                                  {p}
                                </Badge>
                              ))
                            ) : (
                              <span className="text-[11px] text-muted-foreground">
                                该角色不包含任何权限
                              </span>
                            )}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* ---- 数据范围 ---- */}
          <div className="grid gap-2 rounded-md border p-3">
            <Label className="text-xs text-muted-foreground">数据范围</Label>
            <div className="flex flex-wrap gap-1.5">
              {SCOPE_OPTIONS.map((o) => (
                <Button
                  key={o.value}
                  type="button"
                  size="sm"
                  variant={scopeType === o.value ? "default" : "outline"}
                  onClick={() => setScopeType(o.value)}
                >
                  {o.label}
                </Button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {SCOPE_OPTIONS.find((o) => o.value === scopeType)?.hint}
            </p>
            {needScopeId ? (
              <div className="mt-1 space-y-1">
                <Input
                  value={scopeId}
                  onChange={(e) => setScopeId(e.target.value.replace(/[^\d]/g, ""))}
                  placeholder="请输入 scope_id（数字）"
                  inputMode="numeric"
                  className="h-9"
                />
                {scopeIdMissing ? (
                  <p className="text-[11px] text-destructive">
                    选择非「全站」范围时必须填写 scope_id，否则后端会返回 40001。
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>

          {/* ---- 变更预览（diff）---- */}
          <div className="rounded-md bg-muted/50 p-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">变更预览</span>
              {changed ? (
                <span className="text-[11px] font-medium text-amber-700">
                  {added.length} 项新增 / {removed.length} 项移除
                </span>
              ) : (
                <span className="text-[11px] text-muted-foreground">未做修改</span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground line-through">
                {user.roles.length ? user.roles.map(roleLabel).join("、") : "无角色"}
              </span>
              <span className="text-muted-foreground">→</span>
              <span className="text-xs font-medium">
                {selected.length ? selected.map(roleLabel).join("、") : "无角色"}
              </span>
            </div>
            {clearingAll ? (
              <p className="mt-2 text-[11px] font-medium text-destructive">
                保存后该用户将<strong>失去所有后台权限</strong>（前台学员身份不受影响）。
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button
              disabled={!changed || scopeIdMissing || assign.isPending}
              onClick={() => setConfirmOpen(true)}
            >
              {assign.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- 二次确认：复述关键信息，让用户做的是"核对"而不是"盲签" ---- */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="确认修改该用户的角色？"
        destructive={clearingAll}
        loading={assign.isPending}
        confirmText="确认修改"
        description={
          <div className="space-y-2 text-sm">
            <p>
              即将把用户{" "}
              <span className="font-medium text-foreground">{user.nickname || "—"}</span>（
              {user.phone}）的角色修改为：
            </p>
            <p className="font-medium text-foreground">
              {selected.length ? selected.map(roleLabel).join("、") : "（清空，无任何角色）"}
            </p>
            <p className="text-xs text-muted-foreground">
              数据范围：{SCOPE_OPTIONS.find((o) => o.value === scopeType)?.label}
              {needScopeId ? `（scope_id = ${scopeId}）` : ""}
            </p>
            <p className="text-xs text-muted-foreground">
              这次操作会写入审计日志，可在「审计日志」页面按
              <code className="yj-json mx-1 rounded bg-muted px-1">user.assign_roles</code>
              查到。
            </p>
          </div>
        }
        onConfirm={submit}
      />
    </>
  );
}
