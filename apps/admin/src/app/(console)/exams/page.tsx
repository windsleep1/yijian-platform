"use client";

import { Archive, ArchiveRestore, Eye, Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column, type SortState } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { RowActionMarker, rowClassFor } from "@/components/RowActionMarker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  useArchiveExam,
  useExams,
  useRestoreExam,
} from "@/hooks/useExams";
import { useRowActionFeedback } from "@/hooks/useRowActionFeedback";
import { useChapterTree } from "@/hooks/useQuestions";
import { useTableState } from "@/hooks/useTableState";
import { useAuth } from "@/lib/auth-context";
import { EXAM_STATUS_LABELS, EXAM_TYPE_LABELS, examStatusLabel, examStatusVariant, examTypeLabel } from "@/lib/exam";
import { formatDateMinute } from "@/lib/format";
import { P } from "@/lib/permission";
import type { ExamListItem, ExamStatus, ExamType } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 试卷列表。
 *
 * ## 与本页相关的两条硬约定
 *
 * - **④ 默认只看未归档，顶部一个「显示已归档」开关放宽范围。**
 *   它是"放宽"不是"筛选"，所以进 `nonFilterKeys` —— 否则"一张卷都没有"会被误报成
 *   "没有符合条件的试卷"。
 * - **归档 / 恢复走"就地反馈"**（`useRowActionFeedback`）：停留当前视图、
 *   目标行就地标记后延时移除、toast 带 [查看] 出口。
 *   **恢复按钮只在「显示已归档」模式下出现** —— 它不该出现在一个根本看不到
 *   归档卷的列表里（那是个点了会"什么都没变"的按钮）。
 */
const DEFAULTS = {
  page: 1,
  page_size: 20,
  subject_id: "",
  type: "",
  status: "",
  keyword: "",
  /** 「显示已归档」—— 空串即关闭，只在 URL 里写成 "true" */
  include_deleted: "",
  order_by: "updated_at",
  order: "desc",
};

const NON_FILTER_KEYS = ["include_deleted", "order_by", "order"] as const;

/** Radix Select 不接受空字符串 value，用哨兵表示「全部」。 */
const ANY = "__any__";

const ORDERABLE = new Set(["updated_at", "created_at", "published_at", "id"]);

const ORDER_LABELS: Record<string, string> = {
  updated_at: "更新时间",
  created_at: "创建时间",
  published_at: "发布时间",
  id: "ID",
};

export default function ExamsPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const router = useRouter();
  const { state, setState, setPage, reset, hasFilters } = useTableState(DEFAULTS, NON_FILTER_KEYS);

  const [keywordDraft, setKeywordDraft] = useState(state.keyword);
  const [archiveTarget, setArchiveTarget] = useState<ExamListItem | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<ExamListItem | null>(null);

  const page = Number(state.page) || 1;
  const pageSize = Number(state.page_size) || 20;
  const includeDeleted = state.include_deleted === "true";

  const tree = useChapterTree();
  const groups = useMemo(() => tree.data?.items ?? [], [tree.data]);

  const query = useExams({
    page,
    page_size: pageSize,
    subject_id: state.subject_id || undefined,
    type: (state.type || undefined) as ExamType | undefined,
    status: (state.status || undefined) as ExamStatus | undefined,
    keyword: state.keyword || undefined,
    include_deleted: includeDeleted ? true : undefined,
    order_by: (ORDERABLE.has(state.order_by) ? state.order_by : "updated_at") as
      | "updated_at"
      | "created_at"
      | "published_at"
      | "id",
    order: state.order === "asc" ? "asc" : "desc",
  });

  const archive = useArchiveExam();
  const restore = useRestoreExam();
  const fb = useRowActionFeedback();

  const canCreate = hasPermission(P.examCreate);
  const canPublish = hasPermission(P.examPublish);

  /**
   * 筛选/翻页一变就清掉就地反馈状态。
   *
   * 本地"已移除"集合是**视图级**的表达（"本次会话里这条归档完就别显示了"），
   * 换了筛选条件就不该继续生效 —— 用户此时想看的就是服务端真相。
   */
  const stateKey = JSON.stringify(state);
  useEffect(() => {
    fb.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stateKey]);

  if (!authLoading && !hasPermission(P.examRead)) {
    return <ForbiddenState need={P.examRead} />;
  }

  /** 服务端结果 → 滤掉本地已移除的行 */
  const rows = fb.visibleRows(query.data?.items ?? [], (r) => r.id);

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setState({ keyword: keywordDraft.trim() });
  };

  const sort: SortState | null = ORDERABLE.has(state.order_by)
    ? { key: state.order_by, order: state.order === "asc" ? "asc" : "desc" }
    : null;

  const columns: Column<ExamListItem>[] = [
    {
      key: "title",
      title: "试卷",
      headClassName: "min-w-[280px]",
      render: (e) => {
        const f = fb.feedbackOf(e.id);
        return (
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="line-clamp-1 text-sm font-medium">{e.title}</span>
              {e.is_deleted ? (
                <Badge variant="destructive" className="gap-1 text-[10px]">
                  <Archive className="h-3 w-3" />
                  已归档
                </Badge>
              ) : null}
              {e.has_subjective ? (
                <Badge variant="outline" className="text-[10px]">
                  含主观题
                </Badge>
              ) : null}
              {e.is_free ? (
                <Badge variant="secondary" className="text-[10px]">
                  免费
                </Badge>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <span className="yj-json">{e.paper_no ?? "无卷号"}</span>
              <span>·</span>
              <span>{e.exam_year ? `${e.exam_year} 年` : "未标年份"}</span>
            </div>
            {/* 行内反馈就长在标题下方：用户视线本来就在这一格 */}
            <RowActionMarker feedback={f} />
          </div>
        );
      },
    },
    {
      key: "subject",
      title: "科目 / 类型",
      headClassName: "min-w-[150px]",
      render: (e) => (
        <div className="min-w-0 text-xs">
          <div className="truncate" title={e.subject_name ?? `#${e.subject_id}`}>
            {e.subject_name ?? `#${e.subject_id}`}
          </div>
          <div className="truncate text-muted-foreground">{examTypeLabel(e.type)}</div>
        </div>
      ),
    },
    {
      key: "paper",
      title: "题量 / 总分",
      headClassName: "w-[120px] whitespace-nowrap",
      render: (e) => (
        <div className="whitespace-nowrap text-xs">
          <div className="yj-json">
            {e.question_count} 题 · {e.total_score} 分
          </div>
          <div className="text-muted-foreground">
            {e.duration_min} 分钟 · 及格 {e.pass_score}
          </div>
        </div>
      ),
    },
    {
      key: "status",
      title: "状态",
      headClassName: "w-[88px] whitespace-nowrap",
      render: (e) => (
        <Badge variant={examStatusVariant(e.status)} className="whitespace-nowrap text-[10px]">
          {examStatusLabel(e.status)}
        </Badge>
      ),
    },
    {
      key: "updated_at",
      title: "更新时间",
      sortable: true,
      headClassName: "w-[130px] whitespace-nowrap",
      render: (e) => (
        <div className="whitespace-nowrap text-xs text-muted-foreground">
          <div className="yj-json">{formatDateMinute(e.updated_at)}</div>
          <div className="truncate text-[11px]">{e.created_by_name ?? "—"}</div>
        </div>
      ),
    },
    {
      key: "ops",
      title: "操作",
      headClassName: "w-[184px] whitespace-nowrap",
      render: (e) => {
        const f = fb.feedbackOf(e.id);
        const busy = f?.phase === "pending";
        return (
          <div className="flex items-center gap-1" onClick={(ev) => ev.stopPropagation()}>
            <Button variant="ghost" size="sm" className="h-7 px-2" asChild>
              <Link href={`/exams/${e.id}`}>
                <Eye className="h-3.5 w-3.5" />
                详情
              </Link>
            </Button>

            {/* ---------------- 恢复：只在「显示已归档」模式下出现 ---------------- */}
            {includeDeleted && e.is_deleted ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* disabled 的元素不派发鼠标事件 → 必须用 span 兜住才收得到 hover */}
                  <span className="inline-flex">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2"
                      disabled={!canPublish || busy}
                      onClick={() => setRestoreTarget(e)}
                    >
                      <ArchiveRestore className="h-3.5 w-3.5" />
                      恢复
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {!canPublish ? (
                    <>
                      恢复需要 <code className="yj-json">exam:publish</code> 权限。
                      请联系系统管理员开通（已发布的卷恢复后立刻重新对外可见，所以按发布对待）。
                    </>
                  ) : (
                    <>恢复后本行会先标记「已恢复」，3 秒后从当前视图移除。</>
                  )}
                </TooltipContent>
              </Tooltip>
            ) : null}

            {/* ---------------- 归档：未归档的卷才有 ---------------- */}
            {!e.is_deleted ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-muted-foreground hover:text-destructive"
                      disabled={!canCreate || busy}
                      onClick={() => setArchiveTarget(e)}
                    >
                      <Archive className="h-3.5 w-3.5" />
                      归档
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {!canCreate ? (
                    <>
                      归档需要 <code className="yj-json">exam:create</code> 权限。
                      请联系系统管理员开通。
                    </>
                  ) : (
                    <>归档只标记删除位：题目与卷面数据都保留，详情仍可打开。</>
                  )}
                </TooltipContent>
              </Tooltip>
            ) : null}
          </div>
        );
      },
    },
  ];

  return (
    <>
      <PageHeader
        title="试卷管理"
        description="按科目 / 类型 / 状态筛选试卷。默认只显示未归档的卷；归档只标记删除位，题目与卷面都保留。"
        actions={
          hasPermission(P.examCreate) ? (
            <Button size="sm" asChild>
              <Link href="/exams/new">
                <Plus className="h-3.5 w-3.5" />
                新建试卷
              </Link>
            </Button>
          ) : null
        }
      />

      {/* ---------------- 筛选区 ---------------- */}
      <div className="mb-4 space-y-3 rounded-lg border bg-card p-3">
        <div className="flex flex-wrap items-end gap-2">
          <form onSubmit={submitSearch} className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={keywordDraft}
                onChange={(e) => setKeywordDraft(e.target.value)}
                placeholder="试卷标题 / 卷号"
                className="w-60 pl-9"
              />
            </div>
            <Button type="submit" variant="secondary">
              搜索
            </Button>
          </form>

          <Select
            value={state.subject_id || ANY}
            onValueChange={(v) => setState({ subject_id: v === ANY ? "" : v })}
          >
            <SelectTrigger className="w-[168px]">
              <SelectValue placeholder={tree.isLoading ? "加载中…" : "全部科目"} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部科目</SelectItem>
              {groups.map((g) => (
                <SelectItem key={g.subject.id} value={g.subject.id}>
                  {g.subject.name}
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

          <Select
            value={state.status || ANY}
            onValueChange={(v) => setState({ status: v === ANY ? "" : v })}
          >
            <SelectTrigger className="w-[126px]">
              <SelectValue placeholder="全部状态" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部状态</SelectItem>
              {(Object.keys(EXAM_STATUS_LABELS) as ExamStatus[]).map((s) => (
                <SelectItem key={s} value={s}>
                  {EXAM_STATUS_LABELS[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

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

        {/* ---- ④ 显示已归档：放宽范围，不参与"筛选过"的判定 ---- */}
        <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-muted-foreground">
          <Checkbox
            checked={includeDeleted}
            onCheckedChange={(v) => setState({ include_deleted: v === true ? "true" : "" })}
          />
          <span>
            显示已归档的试卷
            {includeDeleted ? (
              <span className="ml-1 text-amber-700">
                —— 已归档的卷会出现在下面，并显示「恢复」按钮
              </span>
            ) : null}
          </span>
        </label>
      </div>

      <DataTable<ExamListItem>
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
        emptyTitle={hasFilters ? "没有符合条件的试卷" : "还没有任何试卷"}
        emptyDescription={
          hasFilters
            ? "换个关键词，或者放宽科目/类型/状态条件再试。"
            : "还没有建过卷。点右上角「新建试卷」手动选题，或按组卷规则自动抽题。"
        }
        onClearFilters={() => {
          setKeywordDraft("");
          reset();
        }}
        rowKey={(e) => e.id}
        onRowClick={(e) => router.push(`/exams/${e.id}`)}
        rowClassName={(e) => rowClassFor(fb.feedbackOf(e.id))}
      />

      {/* 表外状态行：当前排序 / 范围 / 本次会话的就地操作 */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          排序：
          {sort ? `${ORDER_LABELS[sort.key] ?? sort.key} ${sort.order === "asc" ? "升序" : "降序"}` : "—"}
        </span>
        <span>每页 {pageSize} 条 · 本页 {rows.length} 条</span>
        <span className={cn(includeDeleted && "text-amber-700")}>
          {includeDeleted ? "范围：含已归档试卷" : "范围：仅未归档试卷"}
        </span>
        {fb.dismissedCount > 0 ? (
          <span className="text-emerald-700">
            本次已就地处理 {fb.dismissedCount} 条并从当前视图隐藏 · 翻页或改筛选后恢复显示
          </span>
        ) : null}
        {hasFilters ? <span>已应用筛选条件</span> : null}
      </div>

      {/* ---------------- 归档二次确认 ---------------- */}
      <ConfirmDialog
        open={!!archiveTarget}
        onOpenChange={(o) => !o && setArchiveTarget(null)}
        title={`确认归档「${archiveTarget?.title ?? ""}」？`}
        destructive
        loading={fb.feedbackOf(archiveTarget?.id ?? "")?.phase === "pending"}
        confirmText="归档这份试卷"
        description={
          <div className="space-y-2 text-sm">
            <p>
              将把这份试卷<strong className="text-destructive">归档（软删除）</strong>，
              它不再出现在默认列表里。
            </p>
            {archiveTarget?.status === "published" ? (
              <p className="text-amber-700">
                ⚠️ 这份卷当前是<strong>已发布</strong>状态 —— 归档后不应再用于考试。
              </p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              题目与卷面数据<strong>都会保留</strong>，详情仍可打开；
              打开顶部「显示已归档的试卷」就能找到它并恢复。本次操作会写入审计日志。
            </p>
          </div>
        }
        onConfirm={async () => {
          const target = archiveTarget;
          if (!target) return;
          setArchiveTarget(null);
          await fb.run({
            id: target.id,
            doneLabel: "已归档",
            action: () => archive.mutateAsync({ id: target.id, reason: "后台归档" }),
            // 归档是可逆的（有恢复接口），所以允许重试
            retryable: true,
            errorTitle: "归档失败",
            successToast: () => ({
              title: `已归档「${target.title}」`,
              description: "题目与卷面都保留。打开「显示已归档的试卷」可找到并恢复。",
              actionLabel: "查看已归档",
              onAction: () => router.push("/exams?include_deleted=true"),
            }),
          });
        }}
      />

      {/* ---------------- 恢复二次确认 ---------------- */}
      <ConfirmDialog
        open={!!restoreTarget}
        onOpenChange={(o) => !o && setRestoreTarget(null)}
        title={`确认恢复「${restoreTarget?.title ?? ""}」？`}
        loading={fb.feedbackOf(restoreTarget?.id ?? "")?.phase === "pending"}
        confirmText="恢复这份试卷"
        description={
          <div className="space-y-2 text-sm">
            <p>
              将把这份试卷<strong>从归档状态恢复</strong>，
              它立刻重新出现在默认列表里。
            </p>
            <p className="text-xs text-muted-foreground">
              恢复<strong>不会改变试卷状态</strong>
              {restoreTarget ? `（仍是「${EXAM_STATUS_LABELS[restoreTarget.status]}」）` : ""}
              ，也不会改动任何题目。
            </p>
            <p className="text-xs text-muted-foreground">
              恢复后本行会先标记「已恢复」，3 秒后从当前视图移除；点 toast 里的
              [查看] 可以跳到默认列表。
            </p>
          </div>
        }
        onConfirm={async () => {
          const target = restoreTarget;
          if (!target) return;
          setRestoreTarget(null);
          await fb.run({
            id: target.id,
            doneLabel: "已恢复",
            // restore 是幂等的（后端已恢复时返回 code=0 + already_active），
            // 所以重试是安全的
            retryable: true,
            errorTitle: "恢复失败",
            action: () => restore.mutateAsync({ id: target.id }),
            successToast: (res) => ({
              title: `已恢复「${target.title}」`,
              description: res.already_active
                ? "这份卷本来就没有被归档。"
                : res.message,
              actionLabel: "查看默认列表",
              onAction: () => router.push("/exams"),
            }),
          });
        }}
      />
    </>
  );
}
