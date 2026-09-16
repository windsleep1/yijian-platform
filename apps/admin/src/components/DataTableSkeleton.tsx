import { Skeleton } from "@/components/ui/skeleton";
import { TableCell, TableRow } from "@/components/ui/table";

/**
 * 表格骨架。
 *
 * **行高必须与真实行一致**（本项目的行高是 `py-3` + `text-sm` ≈ 45px）。
 * 否则数据回来的瞬间整个表格会跳一下，眼睛很难受，也会让人误以为点错了东西。
 * 同理，骨架行数取 pageSize，而不是写死 5 行。
 */
export function DataTableSkeleton({ rows = 8, cols }: { rows?: number; cols: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <TableRow key={r} className="hover:bg-transparent">
          {Array.from({ length: cols }).map((__, c) => (
            <TableCell key={c} className="px-4 py-3">
              <Skeleton className={c === 0 ? "h-4 w-28" : "h-4 w-20"} />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}
