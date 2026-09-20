"use client";

import { Eye, Filter, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import { AuditDetailDrawer } from "@/components/AuditDiffDrawer";
import { DataTable, type Column } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuditLogs } from "@/hooks/useAuditLogs";
import { useTableState } from "@/hooks/useTableState";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime, localInputToIsoUtc } from "@/lib/format";
import { P } from "@/lib/permission";
import type { AuditLogItem } from "@/lib/types";

/**
 * 审计日志。
 *
 * 三个刻意的设计选择：
 *
 *  1. **列表只展示 action / 对象 / 操作人 / 时间**，一屏能扫完。
 *     `before_data` / `after_data` 放到抽屉里做 diff —— 见 `AuditDiffDrawer`。
 *  2. **时间按 Asia/Shanghai 渲染**。后端返回的是 UTC ISO 串，
 *     直接 `toLocaleString()` 会跟浏览器时区走，跨时区协作时对不上时间。
 *  3. **时间范围用半开区间 `[start, end)`**，与后端一致。这里把
 *     `<input type="datetime-local">` 的本地时间显式当成 +08:00 再转 UTC，
 *     否则整体偏移 8 小时（表现为"筛今天却查出昨天"）。
 */

const DEFAULTS = {
  page: 1,
  page_size: 20,
  actor_id: "",
  action: "",
  entity_type: "",
  entity_id: "",
  success: "",
  start: "",
  end: "",
  order: "desc",
};

/** 已知动作码，给输入框做 datalist 提示（后端暂无 distinct 接口）。 */
const KNOWN_ACTIONS = ["user.assign_roles"];

const ENTITY_TYPES = ["", "user", "question", "exam", "course", "order"];

export default function AuditLogsPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const { state, setState, setPage, reset, hasFilters } = useTableState(DEFAULTS);
  const [detail, setDetail] = useState<AuditLogItem | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 草稿态：输入框里的内容先不写 URL，点"查询"才生效（避免每敲一个字打一次接口）
  const [draft, setDraft] = useState({
    actor_id: state.actor_id,
    action: state.action,
    entity_id: state.entity_id,
  });

  const page = Number(state.page) || 1;
  const pageSize = Number(state.page_size) || 20;

  const query = useAuditLogs({
    page,
    page_size: pageSize,
    actor_id: state.actor_id || undefined,
    action: state.action || undefined,
    entity_type: state.entity_type || undefined,
    entity_id: state.entity_id || undefined,
    success: state.success === "" ? undefined : state.success === "true",
    start: localInputToIsoUtc(state.start),
    end: localInputToIsoUtc(state.end),
    order: state.order === "asc" ? "asc" : "desc",
  });

  if (!authLoading && !hasPermission(P.systemAudit)) {
    return <ForbiddenState need={P.systemAudit} />;
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setState({
      actor_id: draft.actor_id.trim(),
      action: draft.action.trim(),
      entity_id: draft.entity_id.trim(),
    });
  };

  const columns: Column<AuditLogItem>[] = [
    {
      key: "created_at",
      title: "时间",
      headClassName: "w-[190px]",
      render: (a) => <span className="yj-json text-xs">{formatDateTime(a.created_at)}</span>,
    },
    {
      key: "action",
      title: "操作",
      render: (a) => (
        <code className="yj-json rounded bg-muted px-1.5 py-0.5 text-[11px]">{a.action}</code>
      ),
    },
    {
      key: "entity",
      title: "操作对象",
      render: (a) =>
        a.entity_type ? (
          <span className="text-xs">
            <code className="yj-json rounded bg-muted px-1.5 py-0.5">{a.entity_type}</code>
            <span className="yj-json ml-1 text-muted-foreground">{a.entity_id ?? "—"}</span>
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      key: "actor",
      title: "操作人",
      render: (a) => (
        <span className="text-xs">
          {a.actor_name || "—"}
          {a.actor_id ? (
            <span className="yj-json ml-1 text-muted-foreground">{a.actor_id}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "result",
      title: "结果",
      headClassName: "w-[80px]",
      render: (a) => (
        <Badge variant={a.success ? "success" : "destructive"} className="text-[10px]">
          {a.success ? "成功" : "失败"}
        </Badge>
      ),
    },
    {
      key: "ip",
      title: "来源 IP",
      render: (a) => <span className="yj-json text-xs text-muted-foreground">{a.ip ?? "—"}</span>,
    },
    {
      key: "ops",
      title: "",
      headClassName: "w-[80px]",
      render: (a) => (
        <Button
          variant="link"
          size="sm"
          className="h-auto p-0"
          onClick={(e) => {
            e.stopPropagation();
            setDetail(a);
            setDrawerOpen(true);
          }}
        >
          <Eye className="h-3.5 w-3.5" />
          查看
        </Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="审计日志"
        description="所有写操作（改角色、发布内容、改配置等）都会留痕。时间按北京时间（Asia/Shanghai）展示。"
      />

      {/* ---- 筛选区 ---- */}
      <form onSubmit={submit} className="mb-4 space-y-3 rounded-lg border bg-card p-4">
        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Filter className="h-3.5 w-3.5" />
          筛选条件
        </div>

        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1.5">
            <Label htmlFor="f-actor" className="text-xs text-muted-foreground">
              操作人 ID
            </Label>
            <Input
              id="f-actor"
              value={draft.actor_id}
              onChange={(e) =>
                setDraft({ ...draft, actor_id: e.target.value.replace(/[^\d]/g, "") })
              }
              placeholder="精确匹配，如 375228939615866880"
              className="yj-json h-9 text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="f-action" className="text-xs text-muted-foreground">
              操作码
            </Label>
            <Input
              id="f-action"
              list="known-actions"
              value={draft.action}
              onChange={(e) => setDraft({ ...draft, action: e.target.value })}
              placeholder="如 user.assign_roles"
              className="yj-json h-9 text-xs"
            />
            <datalist id="known-actions">
              {KNOWN_ACTIONS.map((a) => (
                <option key={a} value={a} />
              ))}
            </datalist>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="f-entity-id" className="text-xs text-muted-foreground">
              对象 ID
            </Label>
            <Input
              id="f-entity-id"
              value={draft.entity_id}
              onChange={(e) =>
                setDraft({ ...draft, entity_id: e.target.value.replace(/[^\d]/g, "") })
              }
              placeholder="精确匹配"
              className="yj-json h-9 text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">对象类型</Label>
            <div className="flex flex-wrap gap-1">
              {ENTITY_TYPES.map((t) => (
                <Button
                  key={t || "all"}
                  type="button"
                  size="sm"
                  variant={state.entity_type === t ? "default" : "outline"}
                  className="h-9"
                  onClick={() => setState({ entity_type: t })}
                >
                  {t || "全部"}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">执行结果</Label>
            <div className="flex gap-1">
              {[
                { value: "", label: "全部" },
                { value: "true", label: "成功" },
                { value: "false", label: "失败" },
              ].map((o) => (
                <Button
                  key={o.value || "all"}
                  type="button"
                  size="sm"
                  variant={state.success === o.value ? "default" : "outline"}
                  className="h-9"
                  onClick={() => setState({ success: o.value })}
                >
                  {o.label}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="f-start" className="text-xs text-muted-foreground">
              起始时间（含）
            </Label>
            <Input
              id="f-start"
              type="datetime-local"
              value={state.start}
              onChange={(e) => setState({ start: e.target.value })}
              className="h-9 text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="f-end" className="text-xs text-muted-foreground">
              结束时间（不含）
            </Label>
            <Input
              id="f-end"
              type="datetime-local"
              value={state.end}
              onChange={(e) => setState({ end: e.target.value })}
              className="h-9 text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">时间排序</Label>
            <div className="flex gap-1">
              {[
                { value: "desc", label: "最新在前" },
                { value: "asc", label: "最早在前" },
              ].map((o) => (
                <Button
                  key={o.value}
                  type="button"
                  size="sm"
                  variant={state.order === o.value ? "default" : "outline"}
                  className="h-9"
                  onClick={() => setState({ order: o.value })}
                >
                  {o.label}
                </Button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" size="sm">
            查询
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setDraft({ actor_id: "", action: "", entity_id: "" });
              reset();
            }}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            重置
          </Button>
          {hasFilters ? (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <X className="h-3 w-3" />
              已应用筛选条件
            </span>
          ) : null}
        </div>
      </form>

      <DataTable<AuditLogItem>
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
        emptyTitle={hasFilters ? "没有符合条件的审计记录" : "还没有审计记录"}
        emptyDescription={
          hasFilters
            ? "试试放宽时间范围，或清空筛选条件。注意 action / 对象 ID 都是精确匹配，不支持模糊搜索。"
            : "系统还没有产生任何写操作。去用户详情页分配一次角色，这里就会出现一条 user.assign_roles。"
        }
        onClearFilters={() => {
          setDraft({ actor_id: "", action: "", entity_id: "" });
          reset();
        }}
        rowKey={(a) => a.id}
        onRowClick={(a) => {
          setDetail(a);
          setDrawerOpen(true);
        }}
      />

      <AuditDetailDrawer log={detail} open={drawerOpen} onOpenChange={setDrawerOpen} />
    </>
  );
}
