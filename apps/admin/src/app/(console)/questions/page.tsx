"use client";

import { Archive, Plus, Search, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, type Column, type SortState } from "@/components/DataTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
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
import { useBatchDeleteQuestions, useChapterTree, useQuestions } from "@/hooks/useQuestions";
import { useTableState } from "@/hooks/useTableState";
import { useAuth } from "@/lib/auth-context";
import { formatDateMinute } from "@/lib/format";
import { P } from "@/lib/permission";
import {
  DIFFICULTY_LABELS,
  Q_STATUS_LABELS,
  difficultyLabel,
  qStatusLabel,
  qStatusVariant,
  qTypeLabel,
} from "@/lib/question";
import type { QStatus, QType, QuestionListItem } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 题目列表（题库）。
 *
 * ## B 端特征落点
 *
 * - **③ 批量操作栏**：**选中 ≥1 条才出现**。没选任何东西时就显示一条"批量删除"，
 *   点下去只会得到"请先选择题目"——那是把一个必然失败的操作摆在用户面前。
 *   二次确认里必须**复述数量**（"将软删除 N 道题"），否则用户点第二次时是在盲签。
 * - **④ 默认只看未删除，顶部一个「显示已归档」开关放宽范围**。
 *   这个开关**不是筛选条件**（它是放宽而非缩小），所以传进 `useTableState` 的
 *   `nonFilterKeys`：否则"库里一道题都没有"会被误报成"没有符合筛选条件的题目"。
 * - **⑤ 章节下拉联动科目**：章节候选取自 `subject_id` 过滤后的章节树，
 *   换科目时清空章节（后端会拒"市政章节挂在建筑科目下"，提前避开）。
 * - 筛选/分页/排序全部进 URL（`useTableState`），刷新不丢、可分享。
 */
const DEFAULTS = {
  page: 1,
  page_size: 20,
  subject_id: "",
  chapter_id: "",
  type: "",
  difficulty: "",
  status: "",
  keyword: "",
  /** 「显示已归档」—— 空串即关闭（默认只看未删除），只在 URL 里写成 "true" */
  include_deleted: "",
  order_by: "updated_at",
  order: "desc",
};

/** 这些键不是"筛选条件"：排序与「显示已归档」只影响范围/顺序，不影响空态文案。 */
const NON_FILTER_KEYS = ["include_deleted", "order_by", "order"] as const;

/** Radix Select 不接受空字符串 value，用哨兵表示「全部」。 */
const ANY = "__any__";

const ORDERABLE = new Set(["updated_at", "created_at", "difficulty", "id"]);

/** 后端 `order_by` 白名单对应的中文名（状态行展示用，避免露出 created_at 这种字段名）。 */
const ORDER_LABELS: Record<string, string> = {
  updated_at: "更新时间",
  created_at: "创建时间",
  difficulty: "难度",
  id: "ID",
};

export default function QuestionsPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const router = useRouter();
  const { state, setState, setPage, reset, hasFilters } = useTableState(DEFAULTS, NON_FILTER_KEYS);

  // 搜索框的"待提交"本地态：输入过程中不该每敲一个字打一次接口
  const [keywordDraft, setKeywordDraft] = useState(state.keyword);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const page = Number(state.page) || 1;
  const pageSize = Number(state.page_size) || 20;
  const includeDeleted = state.include_deleted === "true";

  // 章节树取"全部科目"（不传 subject_id），科目下拉与章节联动都靠这一份数据，
  // 避免"选中科目后其它科目从下拉里消失"。
  const tree = useChapterTree();
  const groups = useMemo(() => tree.data?.items ?? [], [tree.data]);
  const chapterOptions = useMemo(
    () => groups.find((g) => g.subject.id === state.subject_id)?.chapters ?? [],
    [groups, state.subject_id],
  );

  const query = useQuestions({
    page,
    page_size: pageSize,
    subject_id: state.subject_id || undefined,
    chapter_id: state.chapter_id || undefined,
    type: (state.type || undefined) as QType | undefined,
    difficulty: state.difficulty ? Number(state.difficulty) : undefined,
    status: (state.status || undefined) as QStatus | undefined,
    keyword: state.keyword || undefined,
    // 只有开启时才传：后端默认就是"不含软删除"
    include_deleted: includeDeleted ? true : undefined,
    order_by: (ORDERABLE.has(state.order_by) ? state.order_by : "updated_at") as
      | "updated_at"
      | "created_at"
      | "difficulty"
      | "id",
    order: state.order === "asc" ? "asc" : "desc",
  });

  const batchDelete = useBatchDeleteQuestions();

  /**
   * 筛选条件一变就清空选中。
   *
   * 不清的后果很严重：在第 1 页选了两道题，翻到第 3 页又选一道，
   * 批量删除会删掉三页里的题 —— 而用户眼里只看到当前页那一道。
   * 让"选中"的作用域永远是"当前这一屏"，才不会误伤。
   */
  const stateKey = JSON.stringify(state);
  useEffect(() => {
    setSelected([]);
    // stateKey 覆盖了所有筛选项/页码/排序，任一变化都要清空选中
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stateKey]);

  if (!authLoading && !hasPermission(P.questionRead)) {
    return <ForbiddenState need={P.questionRead} />;
  }

  const rows = query.data?.items ?? [];
  const pageIds = rows.map((r) => r.id);
  const selectedOnPage = pageIds.filter((id) => selected.includes(id));
  const allSelected = pageIds.length > 0 && selectedOnPage.length === pageIds.length;
  const someSelected = selectedOnPage.length > 0 && !allSelected;

  const toggleRow = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const toggleAll = () => {
    setSelected((prev) =>
      allSelected ? prev.filter((id) => !pageIds.includes(id)) : Array.from(new Set([...prev, ...pageIds])),
    );
  };

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setState({ keyword: keywordDraft.trim() });
  };

  const sort: SortState | null = ORDERABLE.has(state.order_by)
    ? { key: state.order_by, order: state.order === "asc" ? "asc" : "desc" }
    : null;

  const columns: Column<QuestionListItem>[] = [
    {
      key: "select",
      title: "",
      headClassName: "w-[36px]",
      // 全选框放在表头里：勾选的作用域是"本页"，那入口就该长在表头上
      renderHead: () => (
        <Checkbox
          checked={allSelected ? true : someSelected ? "indeterminate" : false}
          onCheckedChange={toggleAll}
          disabled={!pageIds.length}
          aria-label="全选本页题目"
          title="全选 / 取消全选本页"
        />
      ),
      render: (q) => (
        // stopPropagation：勾选框在行内，不拦一下会顺带触发"进详情"的行点击
        <div onClick={(e) => e.stopPropagation()}>
          <Checkbox
            checked={selected.includes(q.id)}
            onCheckedChange={() => toggleRow(q.id)}
            aria-label={`选择题目 ${q.id}`}
          />
        </div>
      ),
    },
    {
      key: "stem",
      title: "题干",
      headClassName: "min-w-[260px]",
      render: (q) => (
        <div className="space-y-1">
          <p className="line-clamp-2 text-sm leading-relaxed">{q.stem || "（空题干）"}</p>
          <div className="flex flex-wrap items-center gap-1">
            <Badge variant="outline" className="text-[10px]">
              {qTypeLabel(q.type)}
            </Badge>
            {q.is_deleted ? (
              <Badge variant="destructive" className="gap-1 text-[10px]">
                <Archive className="h-3 w-3" />
                已归档
              </Badge>
            ) : null}
            {q.keywords ? (
              <span className="truncate text-[11px] text-muted-foreground">{q.keywords}</span>
            ) : null}
          </div>
        </div>
      ),
    },
    {
      key: "subject",
      title: "科目 / 章节",
      headClassName: "min-w-[160px] whitespace-nowrap",
      render: (q) => (
        // 这一格必须 nowrap + truncate：章节名有"专业工程管理与实务（市政公用工程）第1章…"这种长串，
        // 不给宽度限制的话它会逐字换行，把单元格撑成一根竖条（整行高到没法看）。
        <div className="min-w-0 text-xs">
          <div className="truncate" title={q.subject_name ?? `#${q.subject_id}`}>
            {q.subject_name ?? `#${q.subject_id}`}
          </div>
          <div
            className="truncate text-muted-foreground"
            title={q.chapter_name ?? "未指定章节"}
          >
            {q.chapter_name ?? "未指定章节"}
          </div>
        </div>
      ),
    },
    {
      key: "correct",
      title: "答案",
      headClassName: "w-[84px] whitespace-nowrap",
      render: (q) =>
        q.correct_labels.length ? (
          <div className="flex flex-wrap gap-1">
            {q.correct_labels.map((l) => (
              <Badge key={l} variant="success" className="yj-json whitespace-nowrap text-[10px]">
                {l}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      key: "difficulty",
      title: "难度",
      sortable: true,
      headClassName: "w-[84px] whitespace-nowrap",
      render: (q) => (
        <span className="whitespace-nowrap text-xs text-muted-foreground">
          {difficultyLabel(q.difficulty)}
        </span>
      ),
    },
    {
      key: "status",
      title: "状态",
      headClassName: "w-[82px] whitespace-nowrap",
      render: (q) => (
        // whitespace-nowrap 是必须的：Badge 里的中文一旦允许换行，
        // "草稿" 会被拆成上下两行贴在窄列里，看起来像坏掉了
        <Badge variant={qStatusVariant(q.status)} className="whitespace-nowrap text-[10px]">
          {qStatusLabel(q.status)}
        </Badge>
      ),
    },
    {
      key: "version",
      title: "版本",
      headClassName: "w-[58px] whitespace-nowrap",
      render: (q) => (
        <span className="yj-json whitespace-nowrap text-xs text-muted-foreground">v{q.version}</span>
      ),
    },
    {
      key: "updated_at",
      title: "更新时间",
      sortable: true,
      headClassName: "w-[122px] whitespace-nowrap",
      render: (q) => (
        <div className="whitespace-nowrap text-xs text-muted-foreground">
          <div className="yj-json">{q.updated_at ? formatDateMinute(q.updated_at) : "—"}</div>
          <div className="truncate text-[11px]" title={q.updated_by_name ?? q.created_by_name ?? ""}>
            {q.updated_by_name ?? q.created_by_name ?? "—"}
          </div>
        </div>
      ),
    },
    {
      key: "ops",
      title: "",
      headClassName: "w-[72px]",
      render: (q) => (
        <Button
          variant="link"
          size="sm"
          className="h-auto p-0"
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/questions/${q.id}`);
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
        title="题库管理"
        description="按科目 / 章节 / 题型 / 难度 / 状态筛选题目。默认只显示未删除的题目；勾选后可批量软删除。"
        actions={
          hasPermission(P.questionCreate) ? (
            <Button size="sm" asChild>
              <Link href="/questions/new">
                <Plus className="h-3.5 w-3.5" />
                新建题目
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
                placeholder="题干 / 关键词"
                className="w-64 pl-9"
              />
            </div>
            <Button type="submit" variant="secondary">
              搜索
            </Button>
          </form>

          <Select
            value={state.subject_id || ANY}
            onValueChange={(v) => setState({ subject_id: v === ANY ? "" : v, chapter_id: "" })}
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

          <Select
            value={state.chapter_id || ANY}
            disabled={!state.subject_id}
            onValueChange={(v) => setState({ chapter_id: v === ANY ? "" : v })}
          >
            <SelectTrigger className="w-[190px]">
              <SelectValue placeholder={state.subject_id ? "全部章节" : "先选科目"} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部章节</SelectItem>
              {chapterOptions.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}（{c.question_count}）
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={state.type || ANY}
            onValueChange={(v) => setState({ type: v === ANY ? "" : v })}
          >
            <SelectTrigger className="w-[132px]">
              <SelectValue placeholder="全部题型" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部题型</SelectItem>
              <SelectItem value="single">单选题</SelectItem>
              <SelectItem value="multiple">多选题</SelectItem>
              <SelectItem value="judge">判断题</SelectItem>
              <SelectItem value="case">案例题</SelectItem>
              <SelectItem value="case_sub">案例小问</SelectItem>
              <SelectItem value="fill">填空题</SelectItem>
              <SelectItem value="essay">简答题</SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={state.difficulty || ANY}
            onValueChange={(v) => setState({ difficulty: v === ANY ? "" : v })}
          >
            <SelectTrigger className="w-[126px]">
              <SelectValue placeholder="全部难度" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部难度</SelectItem>
              {[1, 2, 3, 4, 5].map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {DIFFICULTY_LABELS[n]}
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
              {(Object.keys(Q_STATUS_LABELS) as QStatus[]).map((s) => (
                <SelectItem key={s} value={s}>
                  {Q_STATUS_LABELS[s]}
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
            显示已归档（软删除）的题目
            {includeDeleted ? (
              <span className="ml-1 text-amber-700">
                —— 当前列表包含已删除题目，批量删除前请留意
              </span>
            ) : null}
          </span>
        </label>
      </div>

      {/* ---------------- ③ 批量操作栏：选中 ≥1 条才出现 ---------------- */}
      {selected.length > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-primary/40 bg-primary/5 px-3 py-2">
          <span className="text-sm">
            已选 <span className="font-semibold">{selected.length}</span> 道题
          </span>
          <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
            取消选择
          </Button>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                // 剪贴板写入是**异步且可能被拒绝**的（权限被拒 / 文档未聚焦 / 非安全上下文）。
                // 早期写法 `void navigator.clipboard?.writeText(...)` 有两个毛病：
                //   1. Promise 被 void 丢掉，一旦 reject 就是 unhandled rejection，
                //      Next.js 开发覆盖层会直接甩一个 "1 error" 红点出来；
                //   2. 无论写没写成功都弹「已复制」——**假成功**，
                //      跟批量删除静默跳过是同一类问题（UI 反馈不挂钩真实结果）。
                // 详见 apps/admin/docs/B端联调坑.md 第 22 条。
                try {
                  if (!navigator.clipboard) {
                    throw new Error("browser has no clipboard api");
                  }
                  await navigator.clipboard.writeText(selected.join("\n"));
                  toast.success(`已复制 ${selected.length} 个题目 ID`);
                } catch {
                  toast.error("复制失败：浏览器拒绝了剪贴板写入", {
                    description: "请检查浏览器剪贴板权限，或在题目详情页手动选中复制。",
                  });
                }
              }}
            >
              复制 ID
            </Button>
            {hasPermission(P.questionDelete) ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setConfirmOpen(true)}
                disabled={batchDelete.isPending}
              >
                <Trash2 className="h-3.5 w-3.5" />
                批量删除
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* ---------------- 表格 ---------------- */}
      <DataTable<QuestionListItem>
        columns={columns}
        rows={query.data?.items}
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
        emptyTitle={hasFilters ? "没有符合条件的题目" : "题库还是空的"}
        emptyDescription={
          hasFilters
            ? "换个关键词，或者放宽科目/题型/状态条件再试。"
            : "还没有录入任何题目。可以点右上角「新建题目」手动创建一道，或等待后续的批量导入能力。"
        }
        onClearFilters={() => {
          setKeywordDraft("");
          reset();
        }}
        rowKey={(q) => q.id}
        onRowClick={(q) => router.push(`/questions/${q.id}`)}
      />

      {/* 表头之外的一行状态：让用户随时知道"当前的排序与范围"是什么 */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          排序：
          {sort
            ? `${ORDER_LABELS[sort.key] ?? sort.key} ${sort.order === "asc" ? "升序" : "降序"}`
            : "—"}
        </span>
        <span>每页 {pageSize} 条 · 本页 {pageIds.length} 条</span>
        <span className={cn(includeDeleted && "text-amber-700")}>
          {includeDeleted ? "范围：含已归档题目" : "范围：仅未删除题目"}
        </span>
        {hasFilters ? <span>已应用筛选条件</span> : null}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`确认批量删除 ${selected.length} 道题？`}
        destructive
        loading={batchDelete.isPending}
        confirmText={`软删除 ${selected.length} 道题`}
        description={
          <span className="space-y-2 text-sm">
            <span className="block">
              将<strong className="text-destructive">软删除 {selected.length} 道题</strong>
              （仅标记删除位，不改动题干与选项数据）。
            </span>
            <span className="block text-muted-foreground">
              删除后这些题不再出现在默认列表；打开顶部「显示已归档」仍可查到。
              本次操作会写入审计日志，需要恢复请联系管理员。
            </span>
            <span className="yj-json block max-h-20 overflow-y-auto break-all text-[11px] text-muted-foreground">
              {selected.join("、")}
            </span>
          </span>
        }
        onConfirm={async () => {
          try {
            // ⚠️ 必须原样传字符串。写成 `selected.map(Number)` 会把雪花 ID 舍入
            // （375273861765140480 → 375273861765140500），后端查不到就静默跳过、
            // 返回 deleted:0 —— 页面提示"已删除 0 道题"，用户以为删了。
            const res = await batchDelete.mutateAsync({ ids: selected, reason: "后台批量删除" });
            toast.success(`已软删除 ${res.deleted} 道题`, {
              description: res.skipped.length
                ? `${res.skipped.length} 条因不存在或已删除被跳过。`
                : "可在「显示已归档」中查看。",
            });
            setSelected([]);
            setConfirmOpen(false);
          } catch {
            // 错误 toast 已由 MutationCache 统一弹出；这里保持对话框打开，便于用户重试
          }
        }}
      />
    </>
  );
}
