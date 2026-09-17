"use client";

import { ArrowLeft, FileSpreadsheet, History, ListChecks, RotateCcw, Rocket, Send } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/DataTable";
import { ErrorReportTable } from "@/components/ErrorReportTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { ImportStatCards } from "@/components/ImportStatCards";
import { ImportStatusFlow } from "@/components/ImportStatusFlow";
import { InlineError } from "@/components/InlineError";
import { PageHeader } from "@/components/PageHeader";
import { RollbackDialog } from "@/components/RollbackDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useExecuteImport,
  useImport,
  useImportChanges,
  usePublishImport,
  useRollbackImport,
  useValidateImport,
} from "@/hooks/useImports";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";
import {
  changeActionLabel,
  formatBytes,
  importActionLabel,
  importActionVariant,
  importStatusLabel,
  importStatusVariant,
  isExecuted,
} from "@/lib/import";
import { P } from "@/lib/permission";
import { sourceTypeLabel } from "@/lib/question";
import type { ImportChangeItem, ImportRowPreview } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 批次详情。
 *
 * 一页回答完"这一批到底发生了什么"，所以四个区块缺一不可：
 *
 *  - **状态流转**：raw status 字符串 + 五个节点，一眼看出停在哪；
 *  - **统计**：总数 / 新增 / 更新 / 跳过 / 失败分开给；
 *  - **错误报告**：逐行 `{row_no, field, message}`，可下 CSV（与向导页同一个组件，
 *    同一个下载函数 —— 两处出口行为必须一致）；
 *  - **变更日志**：本批动了哪几道题、谁动的、什么时候（`content_change_logs`）。
 *
 * 每个写操作失败都有自己的 `InlineError` + 重试：执行失败、发布失败、回滚失败
 * 在 B 端的处置方式完全不同，不能都退化成一句"操作失败"。
 */
export default function ImportDetailPage() {
  const params = useParams<{ id: string }>();
  const id = typeof params?.id === "string" ? params.id : "";
  const { hasPermission, isLoading: authLoading } = useAuth();

  const [rowPage, setRowPage] = useState(1);
  const [rowPageSize, setRowPageSize] = useState(50);
  const [changePage, setChangePage] = useState(1);
  const [rollbackOpen, setRollbackOpen] = useState(false);

  const query = useImport(id || undefined, rowPage, rowPageSize);
  const changes = useImportChanges(id || undefined, changePage, 50);

  const validate = useValidateImport();
  const execute = useExecuteImport();
  const publish = usePublishImport();
  const rollback = useRollbackImport();

  if (!authLoading && !hasPermission(P.questionRead)) {
    return <ForbiddenState need={P.questionRead} />;
  }

  if (query.isLoading) {
    return (
      <>
        <PageHeader title="导入批次" description="加载中…" />
        <div className="space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </>
    );
  }

  if (query.error || !query.data) {
    return (
      <>
        <PageHeader
          title="导入批次"
          actions={
            <Button variant="outline" size="sm" asChild>
              <Link href="/imports">
                <ArrowLeft className="h-3.5 w-3.5" />
                返回列表
              </Link>
            </Button>
          }
        />
        <InlineError
          title="加载批次详情"
          error={query.error ?? new Error("批次不存在或已被清理")}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
          hint="批次 ID 是从地址栏取的。如果这个链接是从别处复制来的，确认一下 ID 有没有被截断。"
        />
      </>
    );
  }

  const batch = query.data;
  const executed = isExecuted(batch);
  const canRevalidate = batch.status === "pending" || batch.status === "failed";

  const rowColumns: Column<ImportRowPreview>[] = [
    {
      key: "row_no",
      title: "行号",
      headClassName: "w-[88px] whitespace-nowrap",
      render: (r) => <span className="yj-json text-xs">第 {r.row_no} 行</span>,
    },
    {
      key: "action",
      title: "判定",
      headClassName: "w-[132px] whitespace-nowrap",
      render: (r) => (
        <Badge variant={importActionVariant(r.action)} className="whitespace-nowrap text-[10px]">
          {importActionLabel(r.action)}
        </Badge>
      ),
    },
    {
      key: "message",
      title: "说明",
      headClassName: "min-w-[220px]",
      render: (r) => (
        <span className="line-clamp-1 text-xs text-muted-foreground">{r.message || "—"}</span>
      ),
    },
    {
      key: "question_id",
      title: "题目 ID",
      headClassName: "w-[190px] whitespace-nowrap",
      render: (r) =>
        r.question_id ? (
          <Link
            href={`/questions/${r.question_id}`}
            className="yj-json text-xs text-primary underline-offset-2 hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {r.question_id}
          </Link>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
  ];

  const changeColumns: Column<ImportChangeItem>[] = [
    {
      key: "action",
      title: "动作",
      headClassName: "w-[92px] whitespace-nowrap",
      render: (c) => (
        <Badge
          variant={c.action === "rollback" ? "warning" : "info"}
          className="whitespace-nowrap text-[10px]"
        >
          {changeActionLabel(c.action)}
        </Badge>
      ),
    },
    {
      key: "stem",
      title: "题目",
      headClassName: "min-w-[260px]",
      render: (c) => (
        <div className="min-w-0 space-y-0.5">
          <p className="line-clamp-1 text-xs">{c.question_stem ?? "（题干取不到）"}</p>
          <Link
            href={`/questions/${c.entity_id}`}
            className="yj-json text-[11px] text-primary underline-offset-2 hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {c.entity_id}
          </Link>
        </div>
      ),
    },
    {
      key: "change_log",
      title: "说明",
      headClassName: "min-w-[200px]",
      render: (c) => (
        <span className="line-clamp-1 text-xs text-muted-foreground">{c.change_log || "—"}</span>
      ),
    },
    {
      key: "operator",
      title: "操作人 / 时间",
      headClassName: "w-[150px] whitespace-nowrap",
      render: (c) => (
        <div className="whitespace-nowrap text-xs text-muted-foreground">
          <div className="truncate">{c.operator_name ?? "—"}</div>
          <div className="yj-json text-[11px]">
            {c.created_at ? formatDateTime(c.created_at).slice(0, 16) : "—"}
          </div>
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title={`批次 ${batch.batch_no}`}
        description={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="flex items-center gap-1.5">
              <FileSpreadsheet className="h-3.5 w-3.5" />
              <span className="font-medium text-foreground">{batch.file_name}</span>
              <span className="yj-json text-muted-foreground">{formatBytes(batch.file_size)}</span>
            </span>
            <span>
              科目：<span className="text-foreground">{batch.subject_name ?? "未指定"}</span>
            </span>
            <span>
              模式：<code className="yj-json text-foreground">{batch.mode}</code>
            </span>
            <span>
              来源：<span className="text-foreground">{sourceTypeLabel(batch.source_type)}</span>
            </span>
            <span>
              操作人：<span className="text-foreground">{batch.operator_name ?? "—"}</span>
            </span>
          </span>
        }
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href="/imports">
              <ArrowLeft className="h-3.5 w-3.5" />
              返回列表
            </Link>
          </Button>
        }
      />

      <div className="space-y-5">
        <ImportStatusFlow batch={batch} />

        {/* ---- 操作区 ---- */}
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3">
          <Badge variant={importStatusVariant(batch.status)} className="whitespace-nowrap">
            {importStatusLabel(batch.status)}
          </Badge>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {canRevalidate && hasPermission(P.questionImport) ? (
              <Button
                variant="outline"
                size="sm"
                disabled={validate.isPending}
                onClick={async () => {
                  try {
                    await validate.mutateAsync(batch.id);
                    toast.success("已重新校验", { description: "校验是 dry-run，没有写入任何题目。" });
                  } catch {
                    /* 见 InlineError */
                  }
                }}
              >
                {validate.isPending ? "校验中…" : "重新校验"}
              </Button>
            ) : null}

            {/* 执行：服务端算好的 can_execute（= 未执行过 + 有 question:import） */}
            {batch.can_execute ? (
              <Button
                size="sm"
                disabled={execute.isPending}
                onClick={async () => {
                  try {
                    const ex = await execute.mutateAsync({ id: batch.id });
                    toast.success(`导入完成：写入 ${ex.success_rows} 题`, {
                      description: "题目为草稿状态，发布后才会进入线上题库。",
                    });
                  } catch {
                    /* 见 InlineError */
                  }
                }}
                title={
                  batch.failed_rows > 0
                    ? `本批有 ${batch.failed_rows} 行未通过校验，按约定会被整体拒绝`
                    : undefined
                }
              >
                <Rocket className="h-3.5 w-3.5" />
                {execute.isPending ? "执行中…" : "执行导入"}
              </Button>
            ) : null}

            {/* 发布：can_publish 只判状态+权限，这里再要求"确实写入过"，
                否则会对一个什么都没导的批次亮起发布按钮，点下去吃 40001 */}
            {batch.can_publish && executed ? (
              <Button
                size="sm"
                variant="outline"
                disabled={publish.isPending}
                onClick={async () => {
                  try {
                    await publish.mutateAsync({ id: batch.id });
                    toast.success("已发布到线上题库");
                  } catch {
                    /* 见 InlineError */
                  }
                }}
              >
                <Send className="h-3.5 w-3.5" />
                {publish.isPending ? "发布中…" : "发布本批题目"}
              </Button>
            ) : null}

            {batch.can_rollback && hasPermission(P.questionRollback) ? (
              <Button variant="destructive" size="sm" onClick={() => setRollbackOpen(true)}>
                <RotateCcw className="h-3.5 w-3.5" />
                整批回滚
              </Button>
            ) : null}
          </div>
        </div>

        {validate.isError ? (
          <InlineError
            title="重新校验"
            error={validate.error}
            onRetry={() => void validate.mutateAsync(batch.id).catch(() => {})}
            retrying={validate.isPending}
          />
        ) : null}

        {execute.isError ? (
          <InlineError
            title="执行导入"
            error={execute.error}
            onRetry={() => void execute.mutateAsync({ id: batch.id }).catch(() => {})}
            retrying={execute.isPending}
            hint="执行是整批事务：失败时库里一条都没写进去，重试是安全的。若提示「已经执行过导入」，说明这批已经导过了，不要重复执行。"
          />
        ) : null}

        {publish.isError ? (
          <InlineError
            title="发布"
            error={publish.error}
            onRetry={() => void publish.mutateAsync({ id: batch.id }).catch(() => {})}
            retrying={publish.isPending}
            hint="题目已经入库，只是发布失败。重试即可，不会重复写入。"
          />
        ) : null}

        {/* ---- 统计 ---- */}
        <div className="space-y-2">
          <h2 className="text-sm font-medium">统计</h2>
          <ImportStatCards batch={batch} />
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <span>
              创建：<span className="yj-json">{formatDateTime(batch.created_at)}</span>
            </span>
            <span>
              开始：<span className="yj-json">{formatDateTime(batch.started_at)}</span>
            </span>
            <span>
              结束：<span className="yj-json">{formatDateTime(batch.finished_at)}</span>
            </span>
            {batch.rollback_at ? (
              <span className="text-amber-700">
                回滚：<span className="yj-json">{formatDateTime(batch.rollback_at)}</span>
              </span>
            ) : null}
          </div>
        </div>

        {/* ---- 错误报告 ---- */}
        <div className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <ListChecks className="h-4 w-4" />
            错误报告
          </h2>
          {batch.error_report.total_errors === 0 && !batch.total_rows ? (
            <p className="rounded-lg border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
              这一批还没有校验过（没有逐行结果）。点上方「重新校验」跑一次试算。
            </p>
          ) : (
            <ErrorReportTable
              errors={batch.error_report.errors}
              totalErrors={batch.error_report.total_errors}
              truncated={batch.error_report.truncated}
              batchNo={batch.batch_no}
            />
          )}
        </div>

        {/* ---- 逐行结果 ---- */}
        <div className="space-y-2">
          <h2 className="text-sm font-medium">
            逐行结果
            <span className="ml-2 font-normal text-muted-foreground">
              共 {batch.row_total} 行（分页展示，避免一次渲染上万个单元格）
            </span>
          </h2>
          <DataTable<ImportRowPreview>
            columns={rowColumns}
            rows={batch.rows}
            total={batch.row_total}
            page={batch.row_page}
            pageSize={batch.row_page_size}
            onPageChange={setRowPage}
            onPageSizeChange={(n) => {
              setRowPageSize(n);
              setRowPage(1);
            }}
            isLoading={query.isFetching}
            emptyTitle="还没有逐行结果"
            emptyDescription="先跑一次校验（dry-run），这里就会出现每一行的判定。"
            rowKey={(r) => String(r.row_no)}
          />
        </div>

        {/* ---- 变更日志 ---- */}
        <div className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <History className="h-4 w-4" />
            变更日志
            <span className="font-normal text-muted-foreground">
              {/* counts 是全量汇总，不随分页变化 */}
              {changes.data
                ? `共 ${changes.data.total} 条${
                    Object.keys(changes.data.counts).length
                      ? "： " +
                        Object.entries(changes.data.counts)
                          .map(([k, v]) => `${changeActionLabel(k)} ${v}`)
                          .join(" / ")
                      : ""
                  }`
                : ""}
            </span>
          </h2>
          {changes.error ? (
            <InlineError
              title="加载变更日志"
              error={changes.error}
              onRetry={() => void changes.refetch()}
              retrying={changes.isFetching}
            />
          ) : (
            <DataTable<ImportChangeItem>
              columns={changeColumns}
              rows={changes.data?.items}
              total={changes.data?.total ?? 0}
              page={changePage}
              pageSize={50}
              onPageChange={setChangePage}
              isLoading={changes.isLoading}
              emptyTitle="这一批还没有产生变更"
              emptyDescription={
                executed
                  ? "本批执行后没有写入任何题目（例如全部命中重复），因此没有变更记录。"
                  : "还没有执行导入。变更日志在执行（或回滚）之后才会有内容。"
              }
              rowKey={(c) => c.id}
            />
          )}
        </div>
      </div>

      <RollbackDialog
        open={rollbackOpen}
        onOpenChange={setRollbackOpen}
        batch={batch}
        loading={rollback.isPending}
        error={rollback.error}
        onConfirm={async (reason) => {
          try {
            const res = await rollback.mutateAsync({ id: batch.id, reason });
            toast.success("已整批回滚", {
              description: `软删除 ${res.rolled_back_questions} 道，还原 ${res.rolled_back_updates} 道${
                res.missing.length ? `；${res.missing.length} 道在库中已找不到` : ""
              }。`,
            });
            setRollbackOpen(false);
          } catch {
            /* 错误 toast 由 MutationCache 弹出；弹窗保持打开便于重试。
               弹窗内部的 InlineError（error 传入）负责"解释 + 重试"，
               下面那段则管"用户点取消关掉弹窗之后还能看到失败"。 */
          }
        }}
      />

      {rollback.isError && !rollbackOpen ? (
        <div className="mt-4">
          <InlineError
            title="回滚"
            error={rollback.error}
            onRetry={() => setRollbackOpen(true)}
            hint="回滚失败不会留下半截状态：本批题目要么全部回滚，要么全部保持原样。"
          />
        </div>
      ) : null}

      <p className={cn("mt-4 text-[11px] text-muted-foreground")}>
        批次 ID：<span className="yj-json">{batch.id}</span> · 文件哈希：
        <span className="yj-json">{batch.file_hash.slice(0, 16)}…</span>
      </p>
    </>
  );
}
