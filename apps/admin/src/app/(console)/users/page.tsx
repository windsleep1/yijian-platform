"use client";

import { Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { RowActionMarker, rowClassFor } from "@/components/RowActionMarker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRowActionFeedback } from "@/hooks/useRowActionFeedback";
import { useTableState } from "@/hooks/useTableState";
import { useUpdateUserStatus, useUsers } from "@/hooks/useUsers";
import { useAuth } from "@/lib/auth-context";
import { formatDateMinute } from "@/lib/format";
import { P, roleLabel, statusLabel } from "@/lib/permission";
import type { AdminUserItem } from "@/lib/types";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * 用户列表。
 *
 * B 端特征落点：
 *  - **筛选/分页状态进 URL**（`useTableState`）：刷新不丢、可分享链接。
 *  - **权限门控**：没有 `user:read` 直接整页 403，而不是让表格去报 403 错误。
 *  - **手机号在服务端脱敏**：这里拿到的 `phone` 本来就是 `138****8888`。
 *  - **空态区分**"还没有用户"与"筛选后没结果"，后者给"清空筛选"出口。
 *  - **loading 用骨架**且保留上一页数据（`keepPreviousData`），翻页不闪白。
 */

const DEFAULTS = { page: 1, page_size: 20, keyword: "", status: "" };

export default function UsersPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const router = useRouter();
  const canManage = hasPermission(P.userManage);
  const updateStatus = useUpdateUserStatus();
  const fb = useRowActionFeedback();
  /** 停用/启用的二次确认目标；`reason` 只在停用时可选填 */
  const [statusTarget, setStatusTarget] = useState<AdminUserItem | null>(null);
  const [statusReason, setStatusReason] = useState("");
  const { state, setState, setPage, reset, hasFilters } = useTableState(DEFAULTS);

  // 搜索框是"待提交"的本地态：输入过程中不该每敲一个字就打一次接口。
  // 按回车或点"搜索"才写入 URL（也就才触发请求）。
  const [keywordDraft, setKeywordDraft] = useState(state.keyword);

  const page = Number(state.page) || 1;
  const pageSize = Number(state.page_size) || 20;

  const query = useUsers({
    page,
    page_size: pageSize,
    keyword: state.keyword || undefined,
    status: state.status || undefined,
  });

  if (!authLoading && !hasPermission(P.userRead)) {
    return <ForbiddenState need={P.userRead} />;
  }

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setState({ keyword: keywordDraft.trim() });
  };

  const columns: Column<AdminUserItem>[] = [
    {
      key: "id",
      title: "用户 ID",
      headClassName: "w-[190px]",
      render: (u) => <span className="yj-json text-xs text-muted-foreground">{u.id}</span>,
    },
    {
      key: "phone",
      title: "手机号",
      render: (u) => <span className="yj-json text-sm">{u.phone ?? "—"}</span>,
    },
    {
      key: "nickname",
      title: "昵称",
      render: (u) => <span className="text-sm">{u.nickname || "—"}</span>,
    },
    {
      key: "roles",
      title: "角色",
      render: (u) =>
        u.roles.length ? (
          <div className="flex flex-wrap gap-1">
            {u.roles.map((r) => (
              <Badge key={r} variant="secondary" className="text-[10px]">
                {roleLabel(r)}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">无角色</span>
        ),
    },
    {
      key: "status",
      title: "状态",
      render: (u) => (
        <Badge variant={u.status === "active" ? "success" : "warning"} className="text-[10px]">
          {statusLabel(u.status)}
        </Badge>
      ),
    },
    {
      key: "source",
      title: "注册来源",
      render: (u) => (
        <span className="text-xs text-muted-foreground">{u.register_source ?? "—"}</span>
      ),
    },
    {
      key: "last_login",
      title: "最近登录",
      render: (u) => (
        <span className="yj-json text-xs text-muted-foreground">
          {u.last_login_at ? formatDateMinute(u.last_login_at) : "从未登录"}
        </span>
      ),
    },
    {
      key: "created_at",
      title: "注册时间",
      render: (u) => (
        <span className="yj-json text-xs text-muted-foreground">
          {formatDateMinute(u.created_at)}
        </span>
      ),
    },
    {
      key: "ops",
      title: "操作",
      headClassName: "w-[150px] whitespace-nowrap",
      render: (u) => {
        const busy = fb.feedbackOf(u.id)?.phase === "pending";
        // ⚠️ `locked` / `deleted` **不给按钮** —— 它们归系统（登录失败计数）与注销流程管。
        // 后端也会 40001 拒绝，但"不给一个必然失败的操作"是前端该守住的那一半。
        const systemManaged = u.status === "locked" || u.status === "deleted";
        const on = u.status === "active";
        return (
          <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0"
              onClick={() => router.push(`/users/${u.id}`)}
            >
              详情
            </Button>
            {systemManaged ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex cursor-help text-[11px] text-muted-foreground">
                    {statusLabel(u.status)}（系统管理）
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {u.status === "locked"
                    ? "锁定由系统按登录失败次数自动写入，登录成功后会自动解除 —— 不提供人工开关。"
                    : "注销走独立流程（涉及订单、权益与数据留存），不在这里改。"}
                </TooltipContent>
              </Tooltip>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <Button
                      variant="link"
                      size="sm"
                      className={cn(
                        "h-auto p-0",
                        on && "text-muted-foreground hover:text-destructive",
                      )}
                      disabled={!canManage || busy}
                      onClick={() => {
                        setStatusTarget(u);
                        setStatusReason("");
                      }}
                    >
                      {on ? "停用" : "启用"}
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {!canManage ? (
                    <>
                      需要 <code className="yj-json">user:manage</code> 权限。请联系系统管理员开通。
                    </>
                  ) : on ? (
                    <>停用后该用户的**下一个请求**即被拒绝，并注销其全部设备会话。</>
                  ) : (
                    <>启用后该用户可以正常登录。之前被注销的会话需要重新登录。</>
                  )}
                </TooltipContent>
              </Tooltip>
            )}
            <RowActionMarker feedback={fb.feedbackOf(u.id)} />
          </div>
        );
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="用户管理"
        description="查看平台注册用户、角色与登录情况。手机号默认脱敏展示。"
      />

      {/* ---- 筛选区 ---- */}
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <form onSubmit={submitSearch} className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={keywordDraft}
              onChange={(e) => setKeywordDraft(e.target.value)}
              placeholder="手机号 / 昵称 / 用户名"
              className="w-64 pl-9"
            />
          </div>
          <Button type="submit" variant="secondary">
            搜索
          </Button>
        </form>

        <div className="flex items-center gap-1.5">
          {[
            { value: "", label: "全部状态" },
            { value: "active", label: "正常" },
            { value: "disabled", label: "已禁用" },
            { value: "locked", label: "已锁定" },
          ].map((o) => (
            <Button
              key={o.value || "all"}
              size="sm"
              variant={state.status === o.value ? "default" : "outline"}
              onClick={() => setState({ status: o.value })}
            >
              {o.label}
            </Button>
          ))}
        </div>

        {hasFilters ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setKeywordDraft("");
              reset();
            }}
          >
            <X className="h-3.5 w-3.5" />
            清空筛选
          </Button>
        ) : null}
      </div>

      {/* ---- 表格 ---- */}
      <DataTable<AdminUserItem>
        columns={columns}
        rows={query.data?.items}
        total={query.data?.total}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(n) => setState({ page_size: n })}
        isLoading={query.isLoading || query.isFetching}
        error={query.error}
        onRetry={() => void query.refetch()}
        filtered={hasFilters}
        emptyTitle={hasFilters ? "没有符合条件的用户" : "还没有任何用户"}
        emptyDescription={
          hasFilters
            ? "换个手机号或昵称再试，或者清空筛选条件查看全部用户。"
            : "平台还没有注册用户。可以通过 C 端注册或调用 /auth/register 接口创建。"
        }
        onClearFilters={() => {
          setKeywordDraft("");
          reset();
        }}
        rowKey={(u) => u.id}
        rowClassName={(u) => rowClassFor(fb.feedbackOf(u.id))}
        onRowClick={(u) => router.push(`/users/${u.id}`)}
      />

      <p className={cn("mt-3 text-xs text-muted-foreground")}>
        提示：列表里的手机号是<strong>后端脱敏</strong>后的结果（如 138****8888），
        明文仅在用户详情页、且当前账号拥有 <code className="yj-json">user:export</code> 时才会返回。
      </p>

      {/* ---------------- 停用 / 启用确认 ----------------
          停用**比启用更慎重**：二次确认 + 可选填原因（写审计）。
          启用只做一次简单确认 —— 恢复访问权限不是破坏性动作。 */}
      <ConfirmDialog
        open={!!statusTarget}
        onOpenChange={(o) => {
          if (!o) {
            setStatusTarget(null);
            setStatusReason("");
          }
        }}
        title={
          statusTarget?.status === "active"
            ? `确认停用「${statusTarget.nickname || statusTarget.phone}」？`
            : `确认启用「${statusTarget?.nickname || statusTarget?.phone}」？`
        }
        destructive={statusTarget?.status === "active"}
        loading={fb.feedbackOf(statusTarget?.id ?? "")?.phase === "pending"}
        confirmText={statusTarget?.status === "active" ? "停用该账号" : "启用该账号"}
        description={
          statusTarget?.status === "active" ? (
            <div className="space-y-2 text-sm">
              <p>
                将停用 <strong>{statusTarget.nickname || statusTarget.phone}</strong>（
                {statusTarget.phone}）：
              </p>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                <li>
                  该用户<strong>下一个请求即被拒绝</strong>（不需要等令牌过期）；
                </li>
                <li>
                  同时<strong>注销其全部设备会话</strong>；
                </li>
                <li>无法再登录，直到被重新启用。</li>
              </ul>
              <div className="space-y-1 pt-1">
                <label className="text-xs text-muted-foreground" htmlFor="disable-reason">
                  停用原因（可选，会写入审计日志）
                </label>
                <Input
                  id="disable-reason"
                  value={statusReason}
                  maxLength={200}
                  placeholder="如：多次恶意提交，按运营规范停用"
                  onChange={(e) => setStatusReason(e.target.value)}
                />
              </div>
            </div>
          ) : (
            <div className="space-y-2 text-sm">
              <p>
                将重新启用 <strong>{statusTarget?.nickname || statusTarget?.phone}</strong>
                ，该用户可以正常登录。
              </p>
              <p className="text-xs text-muted-foreground">
                之前被注销的会话不会自动恢复，用户需要重新登录一次。
              </p>
            </div>
          )
        }
        onConfirm={async () => {
          const target = statusTarget;
          if (!target) return;
          const next = target.status === "active" ? "disabled" : "active";
          setStatusTarget(null);
          await fb.run({
            id: target.id,
            // ★ 行**留在原地**（只是状态变了）——
            //   这正是 useRowActionFeedback 的 keepRow 存在的理由：
            //   早期只有"已移除"一种结局，拿它做停用会把还在列表里的行错误隐掉。
            keepRow: true,
            pendingLabel: next === "disabled" ? "停用中…" : "启用中…",
            doneLabel: next === "disabled" ? "已停用" : "已启用",
            action: () =>
              updateStatus.mutateAsync({
                userId: target.id,
                payload: {
                  status: next,
                  reason: next === "disabled" ? statusReason || null : null,
                },
              }),
            // 状态切换是幂等的（设成同一个值无害），允许重试
            retryable: true,
            errorTitle: next === "disabled" ? "停用失败" : "启用失败",
            successToast: (res) => ({
              title: next === "disabled" ? "已停用该账号" : "已启用该账号",
              description: res.message,
            }),
          });
          setStatusReason("");
        }}
      />
    </>
  );
}
