"use client";

import { AlertTriangle, Check } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import { importStatusFlow, importStatusLabel, importStatusVariant } from "@/lib/import";
import type { ImportBatch } from "@/lib/types";
import { cn } from "@/lib/utils";

type Props = {
  batch: ImportBatch;
  /** 紧凑模式（列表页用）：只画节点与连线，不显示时间戳 */
  compact?: boolean;
};

/**
 * 批次状态流转可视化。
 *
 * ## 为什么节点不是 `pending → validated → importing → done → rolled_back`
 *
 * 需求里描述的是**概念上的生命周期**，但后端的 `import_batches.status` 实际只有：
 *
 *     pending / parsing / validating / importing / done / failed / rolled_back
 *
 * 两个对不上的地方必须说清楚，否则前端会画出后端永远给不出的状态：
 *
 *  1. **没有 `validated`。** 校验完成的批次状态就是 `done`。
 *     「待执行 / 已执行」要靠 `content_change_logs` 反推（见 `isExecuted()`）。
 *  2. **没有 `published`。** `POST /publish` **不改批次状态**（仍是 `done`），
 *     它改的是题目的 `status`。所以流转图里不能画"已发布"这一格 ——
 *     画了就没人知道它什么时候该亮。
 *
 * 因此这里的五个节点是：建批次 → 校验完成 → 执行中 → 已入库 → 已回滚，
 * 每个节点的 `hint` 都标着它对应的真实状态值，便于对着后端排查。
 */
export function ImportStatusFlow({ batch, compact = false }: Props) {
  const { steps } = importStatusFlow(batch);

  return (
    <div className={cn("rounded-lg border bg-card", compact ? "p-2.5" : "p-4")}>
      {!compact ? (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">状态流转</span>
          <Badge
            variant={importStatusVariant(batch.status)}
            className="whitespace-nowrap text-[10px]"
          >
            {importStatusLabel(batch.status)}
          </Badge>
          <span className="yj-json text-[11px] text-muted-foreground">status = {batch.status}</span>
          {batch.rollback_at ? (
            <span className="text-[11px] text-muted-foreground">
              · 回滚于 {formatDateTime(batch.rollback_at)}
            </span>
          ) : null}
          <span className="yj-json w-full text-[10px] text-muted-foreground/80">
            pending → validated → importing → done → rolled_back
          </span>
        </div>
      ) : null}

      <ol className="flex flex-wrap items-center gap-y-2">
        {steps.map((s, i) => (
          <li key={s.key} className="flex items-center">
            <span
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                s.state === "done" && "border-primary/40 bg-primary/10 text-foreground",
                s.state === "current" && "border-primary bg-primary text-primary-foreground",
                s.state === "failed" && "border-destructive bg-destructive/10 text-destructive",
                s.state === "todo" && "border-dashed text-muted-foreground",
              )}
              title={s.hint}
            >
              {s.state === "done" ? (
                <Check className="h-3 w-3" />
              ) : s.state === "failed" ? (
                <AlertTriangle className="h-3 w-3" />
              ) : (
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    s.state === "current" ? "bg-primary-foreground" : "bg-muted-foreground/50",
                  )}
                />
              )}
              <span className="whitespace-nowrap">{s.label}</span>
              {/* 规范阶段名（需求文档的用词）。画出来是为了让
                  "pending → validated → importing → done → rolled_back"
                  这条链在界面上一眼可见，而 `hint` 里仍写着它对应的真实后端状态。 */}
              <span
                className={cn(
                  "yj-json text-[10px]",
                  s.state === "current" ? "text-primary-foreground/75" : "text-muted-foreground/80",
                )}
              >
                {s.stage}
              </span>
            </span>
            {i < steps.length - 1 ? (
              <span
                aria-hidden
                className={cn(
                  "mx-1 h-px w-4 sm:w-6",
                  s.state === "done" || s.state === "current" ? "bg-primary/50" : "bg-border",
                )}
              />
            ) : null}
          </li>
        ))}
      </ol>

      {batch.status === "failed" ? (
        <p className="mt-2 text-[11px] leading-relaxed text-destructive">
          这批在执行阶段失败并已整体回滚，库里不会留下半截数据。修正文件后重新上传即可。
        </p>
      ) : null}
    </div>
  );
}
