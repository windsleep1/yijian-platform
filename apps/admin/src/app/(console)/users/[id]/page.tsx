"use client";

import { ArrowLeft, Copy, Info, KeyRound, Loader2, Pencil, UserX } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { AssignRolesDialog } from "@/components/AssignRolesDialog";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { PermissionGate } from "@/components/PermissionGate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useUser } from "@/hooks/useUsers";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";
import { P, roleLabel, statusLabel } from "@/lib/permission";
import type { AdminUserDetail } from "@/lib/types";

/**
 * 用户详情 + 分配角色。
 *
 * 这个页面是"权限门控"的主战场，三件事必须同时成立：
 *
 *  1. **没有 `user:manage` → 「分配角色」按钮置灰 + tooltip**（`PermissionGate`）。
 *     viewer 账号进这个页面看到的就是这个状态。注意：置灰必须是"前端可点性"层面，
 *     后端那道墙（403）永远都在 —— 前端门控是体验，不是安全。
 *  2. **手机号明文只随 `user:export` 下发**。没有该权限时 `phone_full` 是 `null`，
 *     页面显示脱敏值 + 一行说明；而不是"前端把明文盖住"（那样 F12 里还是明文）。
 *  3. **不存在的用户必须给出明确的"用户不存在"**，而不是画一屏空白 ——
 *     后端为此专门返回 40401 而不是空对象。
 */
export default function UserDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const { hasPermission, isLoading: authLoading } = useAuth();
  const query = useUser(id);
  const [dialogOpen, setDialogOpen] = useState(false);

  if (!authLoading && !hasPermission(P.userRead)) {
    return <ForbiddenState need={P.userRead} />;
  }

  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 py-20 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载用户详情…
      </div>
    );
  }

  if (query.error) {
    const err = query.error;
    const notFound = err instanceof ApiError && err.code === 40401;
    return (
      <div className="mx-auto max-w-lg py-20 text-center">
        <div className="mb-4 inline-flex rounded-full bg-muted p-4">
          <UserX className="h-7 w-7 text-muted-foreground" />
        </div>
        <h2 className="text-base font-semibold">{notFound ? "用户不存在" : "加载用户详情失败"}</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {notFound
            ? `ID「${id}」对应的用户不存在，或已被注销。请确认链接是否正确。`
            : (err as Error).message}
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/users">
              <ArrowLeft className="h-3.5 w-3.5" />
              返回用户列表
            </Link>
          </Button>
          {!notFound ? (
            <Button size="sm" onClick={() => void query.refetch()}>
              重试
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  const user = query.data as AdminUserDetail;

  return (
    <>
      <PageHeader
        title={user.nickname || `用户 ${user.id}`}
        description={
          <span className="yj-json text-xs">
            ID {user.id}
            <button
              type="button"
              className="ml-1 align-middle text-muted-foreground hover:text-foreground"
              onClick={() => {
                void navigator.clipboard?.writeText(user.id);
                toast.success("用户 ID 已复制");
              }}
              title="复制用户 ID"
            >
              <Copy className="inline h-3 w-3" />
            </button>
          </span>
        }
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link href="/users">
                <ArrowLeft className="h-3.5 w-3.5" />
                返回列表
              </Link>
            </Button>

            {/* ★ 权限门控：没有 user:manage 时按钮置灰 + tooltip 说明原因 */}
            <PermissionGate
              code={P.userManage}
              reason="该操作会把用户的角色整体替换，属于高权限动作。"
            >
              <Button size="sm" onClick={() => setDialogOpen(true)}>
                <Pencil className="h-3.5 w-3.5" />
                分配角色
              </Button>
            </PermissionGate>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        {/* ---------------- 基本信息 ---------------- */}
        <section className="rounded-lg border bg-card p-5 lg:col-span-2">
          <h3 className="mb-4 text-sm font-semibold">基本信息</h3>
          <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
            <Field label="手机号">
              <PhoneView user={user} />
            </Field>
            <Field label="状态">
              <Badge variant={user.status === "active" ? "success" : "warning"}>
                {statusLabel(user.status)}
              </Badge>
            </Field>
            <Field label="昵称">{user.nickname || "—"}</Field>
            <Field label="真实姓名">{user.real_name || "—"}</Field>
            <Field label="邮箱">{user.email || "—"}</Field>
            <Field label="用户名">{user.username || "—"}</Field>
            <Field label="注册来源">{user.register_source || "—"}</Field>
            <Field label="登录次数">{user.login_count}</Field>
            <Field label="注册 IP">{user.register_ip || "—"}</Field>
            <Field label="最近登录 IP">{user.last_login_ip || "—"}</Field>
            <Field label="最近登录时间">
              {user.last_login_at ? formatDateTime(user.last_login_at) : "从未登录"}
            </Field>
            <Field label="注册时间">{formatDateTime(user.created_at)}</Field>
            {user.remark ? (
              <Field label="备注" className="sm:col-span-2">
                {user.remark}
              </Field>
            ) : null}
          </dl>
        </section>

        {/* ---------------- 角色与数据范围 ---------------- */}
        <section className="rounded-lg border bg-card p-5">
          <h3 className="mb-4 text-sm font-semibold">角色与数据范围</h3>

          <div className="mb-3 flex flex-wrap gap-1.5">
            {user.roles.length ? (
              user.roles.map((r) => (
                <Badge key={r} variant="secondary">
                  {roleLabel(r)}
                </Badge>
              ))
            ) : (
              <span className="text-xs text-muted-foreground">该用户没有任何角色</span>
            )}
          </div>

          <Separator className="my-3" />

          {user.scopes.length ? (
            <ul className="space-y-1.5">
              {user.scopes.map((s) => (
                <li key={`${s.role_code}-${s.scope_type}-${s.scope_id ?? 0}`} className="text-xs">
                  <span className="font-medium">{roleLabel(s.role_code)}</span>
                  <span className="ml-2 text-muted-foreground">
                    {s.scope_type === "global" ? "全站" : `${s.scope_type} #${s.scope_id ?? "—"}`}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">无数据范围记录</p>
          )}

          <Separator className="my-3" />

          {!user.can_manage_roles ? (
            <p className="flex gap-2 rounded-md bg-muted/60 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                当前账号没有 <code className="yj-json">user:manage</code> 权限，
                因此「分配角色」不可用（按钮置灰，悬停可看原因）。
              </span>
            </p>
          ) : (
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              你拥有 <code className="yj-json">user:manage</code>，
              可以修改该用户的角色。每次修改都会写入审计日志。
            </p>
          )}
        </section>

        {/* ---------------- 学习档案 ---------------- */}
        <section className="rounded-lg border bg-card p-5 lg:col-span-3">
          <h3 className="mb-4 text-sm font-semibold">学习档案（user_profiles）</h3>
          {user.profile ? (
            <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-3 lg:grid-cols-4">
              {Object.entries(user.profile).map(([k, v]) => (
                <Field key={k} label={k}>
                  <span className="yj-json text-xs">{renderProfileValue(v)}</span>
                </Field>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">该用户还没有学习档案记录。</p>
          )}
        </section>

        {/* ---------------- 最近审计 ---------------- */}
        <section className="rounded-lg border bg-card p-5 lg:col-span-3">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              最近审计记录
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                作为操作人 或 被操作对象，最近 10 条
              </span>
            </h3>
            {hasPermission(P.systemAudit) ? (
              <Button variant="ghost" size="sm" asChild>
                <Link href={`/audit-logs?entity_type=user&entity_id=${user.id}`}>
                  在审计日志中查看
                </Link>
              </Button>
            ) : null}
          </div>

          {user.recent_audits.length ? (
            <ul className="divide-y">
              {user.recent_audits.map((a) => (
                <li
                  key={a.id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-sm"
                >
                  <code className="yj-json rounded bg-muted px-1.5 py-0.5 text-[11px]">
                    {a.action}
                  </code>
                  <Badge variant={a.success ? "success" : "destructive"} className="text-[10px]">
                    {a.success ? "成功" : "失败"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {a.actor_id
                      ? `操作人 ${a.actor_name || ""} ${a.actor_id}`
                      : "（无操作人，可能是系统任务）"}
                  </span>
                  <span className="ml-auto yj-json text-[11px] text-muted-foreground">
                    {formatDateTime(a.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">
              该用户还没有相关审计记录。给 TA 分配一次角色后回到这里刷新即可看到。
            </p>
          )}
        </section>
      </div>

      <AssignRolesDialog open={dialogOpen} onOpenChange={setDialogOpen} user={user} />
    </>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="mb-0.5 text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all text-sm">{children}</dd>
    </div>
  );
}

/**
 * 手机号展示：脱敏值永远在；明文只在后端下发了 `phone_full` 时才有。
 *
 * 这里**没有**任何"把中间几位盖住"的前端脱敏逻辑 —— 那是掩耳盗铃。
 * 有权限就显示明文，没权限后端压根不下发。
 */
function PhoneView({ user }: { user: AdminUserDetail }) {
  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="yj-json">{user.phone ?? "—"}</span>
      {user.phone_full ? (
        <>
          <Badge variant="outline" className="gap-1 text-[10px]">
            <KeyRound className="h-3 w-3" />
            明文
          </Badge>
          <span className="yj-json text-xs text-muted-foreground">{user.phone_full}</span>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => {
              void navigator.clipboard?.writeText(user.phone_full!);
              toast.success("手机号已复制");
            }}
            title="复制明文手机号"
          >
            <Copy className="inline h-3 w-3" />
          </button>
        </>
      ) : (
        <span className="text-[11px] text-muted-foreground">
          （需 <code className="yj-json">user:export</code> 权限才能查看明文）
        </span>
      )}
    </span>
  );
}

function renderProfileValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}
