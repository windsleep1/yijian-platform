"use client";

import { Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { DataTable, type Column } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTableState } from "@/hooks/useTableState";
import { useUsers } from "@/hooks/useUsers";
import { useAuth } from "@/lib/auth-context";
import { formatDateMinute } from "@/lib/format";
import { P, roleLabel, statusLabel } from "@/lib/permission";
import type { AdminUserItem } from "@/lib/types";
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
      render: (u) => (
        <span className="yj-json text-xs text-muted-foreground">{u.id}</span>
      ),
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
      title: "",
      headClassName: "w-[80px]",
      render: (u) => (
        <Button
          variant="link"
          size="sm"
          className="h-auto p-0"
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/users/${u.id}`);
          }}
        >
          详情
        </Button>
      ),
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
        onRowClick={(u) => router.push(`/users/${u.id}`)}
      />

      <p className={cn("mt-3 text-xs text-muted-foreground")}>
        提示：列表里的手机号是<strong>后端脱敏</strong>后的结果（如 138****8888），
        明文仅在用户详情页、且当前账号拥有 <code className="yj-json">user:export</code> 时才会返回。
      </p>
    </>
  );
}
