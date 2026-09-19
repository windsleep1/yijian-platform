"use client";

import { Pause, Pencil, Play, Plus, Search, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column, type SortState } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { PaperRuleDialog } from "@/components/PaperRuleDialog";
import { RowActionMarker, rowClassFor } from "@/components/RowActionMarker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDeletePaperRule, usePaperRules, useUpdatePaperRule } from "@/hooks/useExams";
import { useChapterTree } from "@/hooks/useQuestions";
import { useRowActionFeedback } from "@/hooks/useRowActionFeedback";
import { useTableState } from "@/hooks/useTableState";
import { useAuth } from "@/lib/auth-context";
import {
  EXAM_TYPE_LABELS,
  RULE_STATUS_LABELS,
  examTypeLabel,
  ruleStatusLabel,
  ruleStrategyLabel,
} from "@/lib/exam";
import { formatDateMinute } from "@/lib/format";
import { P } from "@/lib/permission";
import { qTypeLabel } from "@/lib/question";
import type { ExamType, PaperRuleOut, RuleStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 组卷规则管理。
 *
 * ## 两个行级异步操作，刻意用了 `useRowActionFeedback` 的**两种结局**
 *
 * | 操作 | 结局 | `keepRow` |
 * |---|---|---|
 * | 删除 | 这一行该走了（硬删除） | `false`（默认）→ 就地标记后延时移除 |
 * | 启用 / 停用 | 这一行还在，只是换了状态 | `true` → 只标记，不移除 |
 *
 * 这正是那个组件要支持两种模式的原因：早期只有"移除"一种，
 * 拿它做"停用"会把还在列表里的行错误地隐掉，用户以为被删了。
 *
 * ## ⚠️ 删除规则是**硬删除**（真 `DELETE`）
 *
 * 与试卷的"归档（软删除）"不同 —— `paper_rules` 表**没有 `is_deleted` 列**。
 * 所以确认框里必须把"不可恢复"说清楚，而不是含糊地写"删除"。
 * 好消息是：**删规则不影响已经组好的卷**（试卷各自持有自己的题目与分段）。
 */
const DEFAULTS = {
  page: 1,
  page_size: 20,
  subject_id: "",
  status: "",
  type: "",
  order_by: "updated_at",
  order: "desc",
};

const NON_FILTER_KEYS = ["order_by", "order"] as const;
const ANY = "__any__";
const ORDERABLE = new Set(["updated_at", "id"]);

export default function PaperRulesPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const { state, setState, setPage, reset, hasFilters } = useTableState(DEFAULTS, NON_FILTER_KEYS);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<PaperRuleOut | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PaperRuleOut | null>(null);

  const page = Number(state.page) || 1;
  const pageSize = Number(state.page_size) || 20;

  const tree = useChapterTree();
  const subjects = useMemo(
    () => (tree.data?.items ?? []).map((g) => g.subject),
    [tree.data],
  );

  const query = usePaperRules({
    page,
    page_size: pageSize,
    subject_id: state.subject_id || undefined,
    status: (state.status || undefined) as RuleStatus | undefined,
    type: (state.type || undefined) as ExamType | undefined,
  });

  const remove = useDeletePaperRule();
  const updateRule = useUpdatePaperRule();
  const fb = useRowActionFeedback();

  const canCreate = hasPermission(P.examCreate);

  const stateKey = JSON.stringify(state);
  useEffect(() => {
    fb.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stateKey]);

  if (!authLoading && !hasPermission(P.examRead)) {
    return <ForbiddenState need={P.examRead} />;
  }

  const rows = fb.visibleRows(query.data?.items ?? [], (r) => r.id);

  const sort: SortState | null = ORDERABLE.has(state.order_by)
    ? { key: state.order_by, order: state.order === "asc" ? "asc" : "desc" }
    : null;

  const columns: Column<PaperRuleOut>[] = [
    {
      key: "name",
      title: "规则",
      headClassName: "min-w-[240px]",
      render: (r) => (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="line-clamp-1 text-sm font-medium">{r.name}</span>
            {r.status === "off" ? (
              <Badge variant="secondary" className="text-[10px]">
                已停用
              </Badge>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-1">
            {r.rules.slice(0, 3).map((x, i) => (
              <Badge key={i} variant="outline" className="text-[10px]">
                {qTypeLabel(x.type)} ×{x.count}
              </Badge>
            ))}
            {r.rules.length > 3 ? (
              <span className="text-[10px] text-muted-foreground">+{r.rules.length - 3}</span>
            ) : null}
          </div>
          <RowActionMarker feedback={fb.feedbackOf(r.id)} />
        </div>
      ),
    },
    {
      key: "subject",
      title: "科目 / 类型",
      headClassName: "min-w-[140px]",
      render: (r) => (
        <div className="min-w-0 text-xs">
          <div className="truncate" title={r.subject_name ?? `#${r.subject_id}`}>
            {r.subject_name ?? `#${r.subject_id}`}
          </div>
          <div className="truncate text-muted-foreground">{examTypeLabel(r.type)}</div>
        </div>
      ),
    },
    {
      key: "planned",
      title: "计划题量",
      headClassName: "w-[120px] whitespace-nowrap",
      render: (r) => (
        <div className="whitespace-nowrap text-xs">
          <div className="yj-json">
            {r.planned_count} 题 · {r.planned_score} 分
          </div>
          <div className="text-muted-foreground">{r.duration_min} 分钟</div>
        </div>
      ),
    },
    {
      key: "strategy",
      title: "策略",
      headClassName: "w-[110px] whitespace-nowrap",
      render: (r) => (
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {ruleStrategyLabel(r.strategy)}
        </span>
      ),
    },
    {
      key: "status",
      title: "状态",
      headClassName: "w-[80px] whitespace-nowrap",
      render: (r) => (
        <Badge
          variant={r.status === "on" ? "success" : "secondary"}
          className="whitespace-nowrap text-[10px]"
        >
          {ruleStatusLabel(r.status)}
        </Badge>
      ),
    },
    {
      key: "updated_at",
      title: "更新时间",
      sortable: true,
      headClassName: "w-[130px] whitespace-nowrap",
      render: (r) => (
        <div className="whitespace-nowrap text-xs text-muted-foreground">
          <div className="yj-json">{formatDateMinute(r.updated_at)}</div>
          <div className="truncate text-[11px]">{r.created_by_name ?? "—"}</div>
        </div>
      ),
    },
    {
      key: "ops",
      title: "操作",
      headClassName: "w-[188px] whitespace-nowrap",
      render: (r) => {
        const busy = fb.feedbackOf(r.id)?.phase === "pending";
        const on = r.status === "on";
        return (
          <div className="flex items-center gap-1" onClick={(ev) => ev.stopPropagation()}>
            {/* ---- 启用 / 停用：行留在原地，所以 keepRow ---- */}
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-muted-foreground"
                    disabled={!canCreate || busy}
                    onClick={() =>
                      void fb.run({
                        id: r.id,
                        keepRow: true, // ★ 这一行不走，只是换状态
                        pendingLabel: on ? "停用中…" : "启用中…",
                        doneLabel: on ? "已停用" : "已启用",
                        action: () =>
                          updateRule.mutateAsync({ id: r.id, payload: { status: on ? "off" : "on" } }),
                        // 状态切换是幂等的（设成同一个值无害），允许重试
                        retryable: true,
                        errorTitle: on ? "停用失败" : "启用失败",
                        successToast: () => ({
                          title: on ? `已停用「${r.name}」` : `已启用「${r.name}」`,
                          description: on
                            ? "组卷时会拒绝使用停用规则，但已经组好的卷不受影响。"
                            : "现在可以用这条规则组卷了。",
                        }),
                      })
                    }
                  >
                    {on ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                    {on ? "停用" : "启用"}
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                {!canCreate ? (
                  <>
                    需要 <code className="yj-json">exam:create</code> 权限。请联系系统管理员开通。
                  </>
                ) : on ? (
                  <>停用后组卷会拒绝这条规则；已组好的卷不受影响。</>
                ) : (
                  <>启用后即可用它组卷。</>
                )}
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2"
                    disabled={!canCreate || busy}
                    onClick={() => {
                      setEditing(r);
                      setDialogOpen(true);
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    编辑
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                {canCreate ? (
                  <>改规则是**整体替换**，不是合并；保存前会先试算一遍。</>
                ) : (
                  <>
                    需要 <code className="yj-json">exam:create</code> 权限。
                  </>
                )}
              </TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-muted-foreground hover:text-destructive"
                    disabled={!canCreate || busy || !r.can_delete}
                    onClick={() => setDeleteTarget(r)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    删除
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                {!canCreate ? (
                  <>
                    需要 <code className="yj-json">exam:create</code> 权限。
                  </>
                ) : (
                  <>⚠️ 硬删除，不可恢复。删规则不影响已组好的卷。</>
                )}
              </TooltipContent>
            </Tooltip>
          </div>
        );
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="组卷规则"
        description="规则描述「要什么题」（题型 / 题数 / 分值 / 难度 / 年份）。编辑时实时试算，保存前就知道按这套规则能抽出多少题、题库够不够。"
        actions={
          hasPermission(P.examCreate) ? (
            <Button
              size="sm"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              新建规则
            </Button>
          ) : null
        }
      />

      {/* ---------------- 筛选 ---------------- */}
      <div className="mb-4 flex flex-wrap items-end gap-2 rounded-lg border bg-card p-3">
        <Select
          value={state.subject_id || ANY}
          onValueChange={(v) => setState({ subject_id: v === ANY ? "" : v })}
        >
          <SelectTrigger className="w-[168px]">
            <SelectValue placeholder={tree.isLoading ? "加载中…" : "全部科目"} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>全部科目</SelectItem>
            {subjects.map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={state.status || ANY} onValueChange={(v) => setState({ status: v === ANY ? "" : v })}>
          <SelectTrigger className="w-[126px]">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>全部状态</SelectItem>
            {(Object.keys(RULE_STATUS_LABELS) as RuleStatus[]).map((s) => (
              <SelectItem key={s} value={s}>
                {RULE_STATUS_LABELS[s]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={state.type || ANY} onValueChange={(v) => setState({ type: v === ANY ? "" : v })}>
          <SelectTrigger className="w-[132px]">
            <SelectValue placeholder="全部类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>全部类型</SelectItem>
            {(Object.keys(EXAM_TYPE_LABELS) as ExamType[]).map((t) => (
              <SelectItem key={t} value={t}>
                {EXAM_TYPE_LABELS[t]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {hasFilters ? (
          <Button variant="ghost" size="sm" onClick={reset}>
            <X className="h-3.5 w-3.5" />
            清空筛选
          </Button>
        ) : null}
      </div>

      <DataTable<PaperRuleOut>
        columns={columns}
        rows={rows}
        total={query.data?.total}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        onPageSizeChange={(n) => setState({ page_size: n })}
        sort={sort}
        onSortChange={(s) => setState({ order_by: s.key, order: s.order })}
        isLoading={query.isLoading || query.isFetching}
        error={query.error}
        onRetry={() => void query.refetch()}
        filtered={hasFilters}
        emptyTitle={hasFilters ? "没有符合条件的规则" : "还没有组卷规则"}
        emptyDescription={
          hasFilters
            ? "换个科目或状态再试。"
            : "还没有配过组卷规则。建一条之后，就能用「规则自动组卷」一键生成整张卷。"
        }
        onClearFilters={reset}
        rowKey={(r) => r.id}
        rowClassName={(r) => rowClassFor(fb.feedbackOf(r.id))}
      />

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>每页 {pageSize} 条 · 本页 {rows.length} 条</span>
        {fb.dismissedCount > 0 ? (
          <span className="text-emerald-700">
            本次已就地处理 {fb.dismissedCount} 条并从当前视图隐藏 · 翻页或改筛选后恢复显示
          </span>
        ) : null}
        <span className="flex items-center gap-1">
          <Search className="h-3 w-3" />
          规则改完会自动试算；保存时若题库不足会再确认一次
        </span>
      </div>

      {/* ---------------- 新建 / 编辑 ---------------- */}
      <PaperRuleDialog open={dialogOpen} onOpenChange={setDialogOpen} rule={editing} />

      {/* ---------------- 删除确认（硬删除，必须说清不可恢复） ---------------- */}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title={`确认删除规则「${deleteTarget?.name ?? ""}」？`}
        destructive
        loading={fb.feedbackOf(deleteTarget?.id ?? "")?.phase === "pending"}
        confirmText="永久删除"
        description={
          <div className="space-y-2 text-sm">
            <p className="rounded-md bg-destructive/10 p-2 text-destructive">
              ⚠️ 这是<strong>硬删除</strong>（真 `DELETE`），<strong>不可恢复</strong>。
              组卷规则表没有"归档"机制，删掉就没了 —— 需要重建。
            </p>
            <p className="text-xs text-muted-foreground">
              好消息：<strong>不影响已经组好的试卷</strong>。
              试卷各自持有自己的题目与分段，规则只是"怎么抽"的模板。
            </p>
            <p className="text-xs text-muted-foreground">
              如果只是暂时不想用它，建议改成<strong>「停用」</strong> ——
              同样能让组卷拒绝使用，而且随时可以启用回来。
            </p>
          </div>
        }
        onConfirm={async () => {
          const target = deleteTarget;
          if (!target) return;
          setDeleteTarget(null);
          await fb.run({
            id: target.id,
            doneLabel: "已删除",
            action: () => remove.mutateAsync({ id: target.id }),
            // 硬删除**不幂等**：重试第二次必然是 40401 —— 不给重试键
            retryable: false,
            errorTitle: "删除失败",
            successToast: () => ({
              title: `已删除规则「${target.name}」`,
              description: "已组好的试卷不受影响。",
            }),
          });
        }}
      />
    </>
  );
}
