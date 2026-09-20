"use client";

import { AlertTriangle, ChevronDown, ChevronRight, Download, Scissors } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { downloadTextFile, errorReportFilename, errorsToCsv } from "@/lib/import";
import type { RowError } from "@/lib/types";
import { cn } from "@/lib/utils";

type Props = {
  errors: RowError[];
  /** 后端给的**完整**错误数（`errors` 可能被截断到 200 条） */
  totalErrors?: number;
  truncated?: boolean;
  /** 批次号，只用于下载文件名 */
  batchNo?: string;
  /** 表格区最大高度，避免 200 行把页面撑成一长条 */
  maxHeightClassName?: string;
};

/**
 * 错误报告表。
 *
 * 这是整个导入流程里**教研唯一真正要看的东西**，所以三件事必须做到位：
 *
 *  1. **必须按 `row_no` 升序**。后端回传顺序不保证，但人的动作是
 *     "打开 Excel 从上往下改"，顺序乱了就得来回跳。
 *     （`row_no` 是**数据行号，从 1 起、不含表头**，页面上要写清楚，
 *     否则用户按 Excel 的显示行号去数会差一行。）
 *  2. **点行要能看到字段级详情**。列表里 `message` 会被截断，
 *     而用户要的就是完整原因；同时把"在 Excel 里跳到第 N 行"算给他。
 *  3. **CSV 必须能下载、能直接用 Excel 打开** —— 带 BOM（否则中文乱码）、
 *     正确转义（错误信息里有逗号和括号）、并且把"已截断"写进文件里
 *     （不然用户会以为总共就这么几条错）。
 */
export function ErrorReportTable({
  errors,
  totalErrors,
  truncated,
  batchNo = "",
  maxHeightClassName = "max-h-[420px]",
}: Props) {
  const [expanded, setExpanded] = useState<number | null>(null);

  const sorted = useMemo(() => [...errors].sort((a, b) => a.row_no - b.row_no), [errors]);
  const total = totalErrors ?? sorted.length;

  if (!sorted.length) {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 px-4 py-6 text-center text-sm text-emerald-800">
        没有错误行 —— 文件全部通过校验。
      </div>
    );
  }

  const handleDownload = () => {
    downloadTextFile(
      errorReportFilename(batchNo || "batch"),
      errorsToCsv(sorted, { totalErrors: total, truncatedAt: sorted.length }),
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="destructive" className="gap-1">
            <AlertTriangle className="h-3 w-3" />
            {total} 条错误
          </Badge>
          <span className="text-xs text-muted-foreground">
            行号是文件里的<strong className="text-foreground">数据行号</strong>
            （从 1 起、不含表头）—— 拿它去 Excel 跳行即可定位。
          </span>
        </div>
        {/* data-testid：E2E 要能稳定点到这个按钮（`agent-browser` 只认 CSS，
            不支持 `text=` / `:has-text()`）。与拖拽区的 `import-dropzone` 同一套约定。 */}
        <Button
          variant="outline"
          size="sm"
          onClick={handleDownload}
          data-testid="download-error-report"
        >
          <Download className="h-3.5 w-3.5" />
          下载错误报告 CSV
        </Button>
      </div>

      {truncated ? (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900">
          报告已截断：页面与 CSV 只含前 <strong>{sorted.length}</strong> 条，实际共{" "}
          <strong>{total}</strong> 条错误。建议先改掉已列出的这些行，重新上传后再看下一批。
        </p>
      ) : null}

      <div className={cn("overflow-auto rounded-lg border bg-card", maxHeightClassName)}>
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableHead className="w-[40px]" />
              <TableHead className="w-[92px] whitespace-nowrap">行号</TableHead>
              <TableHead className="w-[150px] whitespace-nowrap">字段</TableHead>
              <TableHead className="min-w-[260px]">原因</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((e) => {
              const open = expanded === e.row_no;
              return (
                // key 必须挂在外层 Fragment 上 —— 挂在里面的 TableRow 上，
                // React 会报 "Each child in a list should have a unique key"
                <Fragment key={`e-${e.row_no}-${e.field}`}>
                  <TableRow
                    onClick={() => setExpanded(open ? null : e.row_no)}
                    className="cursor-pointer"
                  >
                    <TableCell className="py-2">
                      {open ? (
                        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </TableCell>
                    <TableCell className="yj-json py-2 text-xs font-medium">
                      第 {e.row_no} 行
                    </TableCell>
                    <TableCell className="py-2">
                      <code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{e.field}</code>
                    </TableCell>
                    <TableCell className="py-2 text-xs">
                      <span className="line-clamp-1">{e.message}</span>
                    </TableCell>
                  </TableRow>

                  {open ? (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={4} className="bg-muted/30">
                        <div className="space-y-2 py-1 text-xs">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline" className="yj-json text-[10px]">
                              第 {e.row_no} 行
                            </Badge>
                            <Badge variant="outline" className="text-[10px]">
                              字段：<code className="ml-1">{e.field}</code>
                            </Badge>
                          </div>
                          <p className="leading-relaxed text-foreground">{e.message}</p>
                          <p className="flex items-start gap-1.5 leading-relaxed text-muted-foreground">
                            <Scissors className="mt-0.5 h-3 w-3 shrink-0" />
                            <span>
                              定位方法：打开原始文件，跳到第 <strong>{e.row_no}</strong> 个数据行
                              （不含表头），检查 <code>{e.field}</code> 这一列。改好后重新上传即可。
                            </span>
                          </p>
                        </div>
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
