"use client";

import { ArrowDown, ArrowUp, ArrowUpDown, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { DataTableSkeleton } from "@/components/DataTableSkeleton";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export type SortState = { key: string; order: "asc" | "desc" };

export type Column<T> = {
  key: string;
  title: string;
  headClassName?: string;
  cellClassName?: string;
  /** 是否允许点击表头排序。需要父组件传 onSortChange 才有意义。 */
  sortable?: boolean;
  render: (row: T) => ReactNode;
  /**
   * 自定义表头内容（整格替换 `title` + 排序按钮）。
   *
   * 存在的理由很具体：题库的"批量删除"需要**表头里的全选框**，
   * 而全选框的状态（全选 / 半选 / 未选）只有页面知道。
   * 与其给 DataTable 塞一个 `selectable` 专用 API（把通用组件绑死在一种业务上），
   * 不如开一个"表头我自己画"的口子 —— 排序逻辑仍由父组件通过 `sortable` 之外的方式自行处理。
   */
  renderHead?: () => ReactNode;
};

type Props<T> = {
  columns: Column<T>[];
  rows: T[] | undefined;
  total?: number;
  page: number;
  pageSize: number;
  onPageChange: (p: number) => void;
  onPageSizeChange?: (n: number) => void;
  sort?: SortState | null;
  onSortChange?: (s: SortState) => void;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** 有筛选条件时传 true —— 空态文案与出口都不一样（见 EmptyState 注释） */
  filtered?: boolean;
  onClearFilters?: () => void;
  emptyTitle?: string;
  emptyDescription?: string;
  rowKey: (r: T) => string;
  onRowClick?: (r: T) => void;
  /**
   * 每一行的附加 className。
   *
   * 存在的理由很具体：归档/恢复这类**行级异步操作**需要"整行淡出"来表示已完成
   * （见 `useRowActionFeedback` + `RowActionMarker`），而表格本身不该知道
   * "反馈状态"是什么概念 —— 它只接受一个 className 回调。
   */
  rowClassName?: (r: T) => string;
};

const PAGE_SIZES = [10, 20, 50, 100];

/**
 * 表格壳：分页 / 排序 / 空态 / 骨架 / 错误态 一次解决。
 *
 * 为什么手写而不用 TanStack Table：本项目只有两张表，
 * 引入一个 headless 表格库的学习 + 适配成本不划算。
 *
 * 三个容易被忽略的细节：
 *  - **加载中用骨架而不是"空白 + 转圈"**，骨架行高与真实行一致，数据回来不跳动。
 *  - **空态区分"没数据"和"筛选后没结果"**，后者要给"清空筛选"的出口。
 *  - **总数、页码、每页条数都要显示**。B 端用户需要明确知道"我筛出来了几条"。
 */
export function DataTable<T>({
  columns,
  rows,
  total = 0,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  sort,
  onSortChange,
  isLoading,
  error,
  onRetry,
  filtered = false,
  onClearFilters,
  emptyTitle,
  emptyDescription,
  rowKey,
  onRowClick,
  rowClassName,
}: Props<T>) {
  if (error) {
    return (
      <div className="rounded-lg border bg-card">
        <ErrorState error={error} onRetry={onRetry} />
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const hasRows = !!rows?.length;
  const showSkeleton = isLoading && !hasRows;

  const toggleSort = (key: string) => {
    if (!onSortChange) return;
    const next: SortState =
      sort?.key === key
        ? { key, order: sort.order === "asc" ? "desc" : "asc" }
        : { key, order: "desc" };
    onSortChange(next);
  };

  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/40 hover:bg-muted/40">
            {columns.map((c) => (
              <TableHead key={c.key} className={cn(c.headClassName)}>
                {c.renderHead ? (
                  c.renderHead()
                ) : c.sortable && onSortChange ? (
                  <button
                    type="button"
                    onClick={() => toggleSort(c.key)}
                    className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
                  >
                    {c.title}
                    {sort?.key === c.key ? (
                      sort.order === "asc" ? (
                        <ArrowUp className="h-3 w-3" />
                      ) : (
                        <ArrowDown className="h-3 w-3" />
                      )
                    ) : (
                      <ArrowUpDown className="h-3 w-3 opacity-40" />
                    )}
                  </button>
                ) : (
                  c.title
                )}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>

        <TableBody>
          {showSkeleton ? (
            <DataTableSkeleton rows={Math.min(pageSize, 8)} cols={columns.length} />
          ) : !hasRows ? (
            <TableRow className="hover:bg-transparent">
              <TableCell colSpan={columns.length} className="p-0">
                {filtered ? (
                  <EmptyState
                    icon={SearchX}
                    title={emptyTitle ?? "没有符合条件的结果"}
                    description={
                      emptyDescription ??
                      "当前筛选条件没有匹配到任何记录。可以放宽条件，或者直接清空筛选重新开始。"
                    }
                    action={
                      onClearFilters ? (
                        <Button variant="outline" size="sm" onClick={onClearFilters}>
                          清空筛选条件
                        </Button>
                      ) : undefined
                    }
                  />
                ) : (
                  <EmptyState
                    title={emptyTitle ?? "还没有数据"}
                    description={emptyDescription ?? "这里暂时是空的。"}
                  />
                )}
              </TableCell>
            </TableRow>
          ) : (
            rows!.map((row) => (
              <TableRow
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(onRowClick && "cursor-pointer", rowClassName?.(row))}
              >
                {columns.map((c) => (
                  <TableCell key={c.key} className={cn(c.cellClassName)}>
                    {c.render(row)}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      {/* 分页条：始终显示，即使只有一页 —— 用户需要知道"总共几条" */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
        <div className="text-xs text-muted-foreground">
          共 <span className="font-medium text-foreground">{total}</span> 条
          {total > 0 ? (
            <>
              {" "}
              · 第 <span className="font-medium text-foreground">{page}</span> / {totalPages} 页
            </>
          ) : null}
        </div>

        <div className="flex items-center gap-3">
          {onPageSizeChange ? (
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              每页
              <select
                className="h-8 rounded-md border border-input bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={pageSize}
                onChange={(e) => onPageSizeChange(Number(e.target.value))}
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              条
            </label>
          ) : null}

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => onPageChange(page + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
