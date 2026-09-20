"use client";

import { FileSpreadsheet, Plus, RotateCcw, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { ImportStatusFlow } from "@/components/ImportStatusFlow";
import { PageHeader } from "@/components/PageHeader";
import { RollbackDialog } from "@/components/RollbackDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useImports, useRollbackImport } from "@/hooks/useImports";
import { useTableState } from "@/hooks/useTableState";
import { useAuth } from "@/lib/auth-context";
import { formatDateMinute } from "@/lib/format";
import {
  IMPORT_STATUS_LABELS,
  importStatusLabel,
  importStatusVariant,
  nextActionHint,
} from "@/lib/import";
import { P } from "@/lib/permission";
import { sourceTypeLabel } from "@/lib/question";
import type { ImportBatch, ImportStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const DEFAULTS = { page: 1, page_size: 20, status: "" };

/** Radix Select 不接受空字符串 value。 */
const ANY = "__any__";

/**
 * 导入批次列表。
 *
 * 这一页要回答三个问题（B 端用户真正会来这一页的时刻，都是因为其中一个）：
 *
 *  1. **"我上周导的那批现在什么状态？"** → 状态列 + 状态流转图 + 下一步提示。
 *  2. **"导错了，能撤吗？"** → 每行直接给回滚入口（但要有 `question:rollback`
 *     才亮，否则点下去只会吃 403）。
 *  3. **"到底进了多少题？"** → 统计列把 总数/新增/更新/跳过/失败 分开列，
 *     而不是只给一个"成功 6000"。
 *
 * 状态与页码进 URL（`useTableState`）：刷新不丢、可把链接发给同事。
 */
export default function ImportsPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const router = useRouter();
  const { state, setState, setPage, reset, hasFilters } = useTableState(DEFAULTS);

  const [rollbackTarget, setRollbackTarget] = useState<ImportBatch | null>(null);

  const page = Number(state.page) || 1;
  const pageSize = Number(state.page_size) || 20;

  const query = useImports({
    page,
    page_size: pageSize,
    status: (state.status || undefined) as ImportStatus | undefined,
  });
  const rollback = useRollbackImport();

  if (!authLoading && !hasPermission(P.questionRead)) {
    return <ForbiddenState need={P.questionRead} />;
  }

  const columns: Column<ImportBatch>[] = [
    {
      key: "batch",
      title: "批次 / 文件",
      headClassName: "min-w-[240px]",
      render: (b) => (
        <div className="space-y-1">
          <div className="flex items-center gap-1.5">
            <FileSpreadsheet className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <code className="yj-json text-xs font-medium">{b.batch_no}</code>
          </div>
          <div className="truncate text-[11px] text-muted-foreground" title={b.file_name}>
            {b.file_name}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <Badge variant="outline" className="yj-json text-[10px]">
              {b.mode}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              {sourceTypeLabel(b.source_type)}
            </Badge>
            {b.auto_publish ? (
              <Badge variant="warning" className="text-[10px]">
                自动发布
              </Badge>
            ) : null}
          </div>
        </div>
      ),
    },
    {
      key: "subject",
      title: "科目",
      headClassName: "min-w-[140px] whitespace-nowrap",
      render: (b) => (
        <div className="min-w-0 text-xs">
          <div className="truncate" title={b.subject_name ?? "未指定"}>
            {b.subject_name ?? "未指定"}
          </div>
          {b.subject_code ? (
            <div className="yj-json truncate text-[11px] text-muted-foreground">
              {b.subject_code}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      key: "stats",
      title: "行数统计",
      headClassName: "min-w-[190px] whitespace-nowrap",
      render: (b) => (
        <div className="space-y-0.5 text-[11px]">
          <div className="text-muted-foreground">
            共 <span className="yj-json font-medium text-foreground">{b.total_rows}</span> 行
          </div>
          <div className="flex flex-wrap gap-x-2.5 gap-y-0.5">
            <span className="text-emerald-700">
              新增 {Math.max(0, b.success_rows - b.updated_rows)}
            </span>
            <span className="text-sky-700">更新 {b.updated_rows}</span>
            <span className="text-muted-foreground">跳过 {b.duplicate_rows}</span>
            <span className={cn(b.failed_rows > 0 ? "text-destructive" : "text-muted-foreground")}>
              失败 {b.failed_rows}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "status",
      title: "状态 / 流转",
      headClassName: "min-w-[300px]",
      render: (b) => (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <Badge
              variant={importStatusVariant(b.status)}
              className="whitespace-nowrap text-[10px]"
            >
              {importStatusLabel(b.status)}
            </Badge>
            <span className="text-[11px] text-muted-foreground">{nextActionHint(b)}</span>
          </div>
          <ImportStatusFlow batch={b} compact />
        </div>
      ),
    },
    {
      key: "operator",
      title: "操作人 / 时间",
      headClassName: "w-[136px] whitespace-nowrap",
      render: (b) => (
        <div className="whitespace-nowrap text-xs text-muted-foreground">
          <div className="truncate" title={b.operator_name ?? "—"}>
            {b.operator_name ?? "—"}
          </div>
          <div className="yj-json text-[11px]">
            {b.created_at ? formatDateMinute(b.created_at) : "—"}
          </div>
        </div>
      ),
    },
    {
      key: "ops",
      title: "",
      headClassName: "w-[132px]",
      render: (b) => (
        <div className="flex items-center gap-1">
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0"
            onClick={(e) => {
              e.stopPropagation();
              router.push(`/imports/${b.id}`);
            }}
          >
            详情
          </Button>
          {/* 回滚入口要**两道都过**：服务端说状态允许（`can_rollback`）+ 本账号确实有
              `question:rollback`。"按钮亮着点了却 403"比"按钮压根不出现"更糟 ——
              前者让用户以为系统坏了，后者他会去问管理员要权限。 */}
          {b.can_rollback && hasPermission(P.questionRollback) ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-auto px-1.5 py-0 text-destructive hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                setRollbackTarget(b);
              }}
            >
              <RotateCcw className="h-3 w-3" />
              回滚
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="题库导入"
        description="批量导入的历史批次。点开任一批次可以看逐行结果、错误报告与变更日志；导错了可以整批回滚。"
        actions={
          hasPermission(P.questionImport) ? (
            <Button size="sm" asChild>
              <Link href="/imports/new">
                <Plus className="h-3.5 w-3.5" />
                新建导入
              </Link>
            </Button>
          ) : null
        }
      />

      {/* ---- 筛选 ---- */}
      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3">
        <Select
          value={state.status || ANY}
          onValueChange={(v) => setState({ status: v === ANY ? "" : v })}
        >
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>全部状态</SelectItem>
            {(Object.keys(IMPORT_STATUS_LABELS) as ImportStatus[]).map((s) => (
              <SelectItem key={s} value={s}>
                {IMPORT_STATUS_LABELS[s]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {hasFilters ? (
          <Button variant="ghost" size="sm" onClick={reset}>
            清空筛选
          </Button>
        ) : null}

        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <Upload className="h-3.5 w-3.5" />
          批次只允许执行一次；要重导请新建批次
        </span>
      </div>

      <DataTable<ImportBatch>
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
        emptyTitle={hasFilters ? "没有这个状态的批次" : "还没有导入过题库"}
        emptyDescription={
          hasFilters
            ? "换一个状态试试，或者清空筛选看全部批次。"
            : "还没有任何导入批次。点右上角「新建导入」，上传 CSV / JSON 走一遍三步向导。"
        }
        onClearFilters={reset}
        rowKey={(b) => b.id}
        onRowClick={(b) => router.push(`/imports/${b.id}`)}
      />

      <RollbackDialog
        open={!!rollbackTarget}
        onOpenChange={(o) => {
          if (!o) setRollbackTarget(null);
        }}
        batch={rollbackTarget}
        loading={rollback.isPending}
        error={rollback.error}
        onConfirm={async (reason) => {
          if (!rollbackTarget) return;
          try {
            const res = await rollback.mutateAsync({ id: rollbackTarget.id, reason });
            toast.success("已整批回滚", {
              description: `软删除 ${res.rolled_back_questions} 道，还原 ${res.rolled_back_updates} 道${
                res.missing.length ? `；${res.missing.length} 道在库中已找不到` : ""
              }。`,
            });
            setRollbackTarget(null);
          } catch {
            // 错误 toast 由 MutationCache 统一弹出；保持弹窗打开便于重试
          }
        }}
      />
    </>
  );
}
