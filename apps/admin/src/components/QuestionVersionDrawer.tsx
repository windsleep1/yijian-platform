"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { ChevronRight, History, Lock, X } from "lucide-react";
import { useState } from "react";

import { MarkdownPreview } from "@/components/MarkdownPreview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import { difficultyLabel, qStatusLabel, qStatusVariant, qTypeLabel } from "@/lib/question";
import type { QuestionSnapshot, QuestionVersionItem } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 版本历史抽屉（**只读**）。
 *
 * B 端特征②的后半句是「版本历史只读」——这句话得落到 UI 上：
 * 抽屉里**没有任何"回滚/恢复"按钮**，并且在顶部明说原因。
 * 一个不给回滚入口的版本列表，用户的第一反应是"按钮是不是没加载出来"；
 * 写清楚"本批只做留痕，回滚在后续批次"，才算把话说完整。
 *
 * 数据来自 `QuestionDetail.versions`：后端已按版本倒序、最多 10 条、
 * 且 `snapshot` 是**当时的完整快照**（不是 diff），所以能原样回放"那一刻这道题长什么样"。
 */
export function QuestionVersionDrawer({
  open,
  onOpenChange,
  versions,
  currentVersion,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  versions: QuestionVersionItem[];
  currentVersion: number;
}) {
  const [expanded, setExpanded] = useState<string | null>(versions[0]?.id ?? null);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-xl flex-col border-l bg-background shadow-xl duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right">
          <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="flex items-center gap-2 text-base font-semibold">
                <History className="h-4 w-4" />
                版本历史
                <Badge variant="secondary" className="text-[10px]">
                  只读
                </Badge>
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-1 text-xs leading-relaxed text-muted-foreground">
                当前 v{currentVersion} · 共 {versions.length} 条历史（后端最多保留 10 条）
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0">
                <X className="h-4 w-4" />
                <span className="sr-only">关闭</span>
              </Button>
            </DialogPrimitive.Close>
          </div>

          <p className="flex gap-2 border-b bg-muted/40 px-5 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
            <Lock className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              本批只提供"看历史"，不提供回滚 —— 回滚会牵动已发布试卷与作答记录，
              属于后续批次的独立能力。每次保存都会自增版本号并写一条变更日志。
            </span>
          </p>

          <div className="flex-1 overflow-y-auto px-5 py-4">
            {versions.length ? (
              <ol className="space-y-2">
                {versions.map((v) => {
                  const openThis = expanded === v.id;
                  return (
                    <li key={v.id} className="overflow-hidden rounded-md border">
                      <button
                        type="button"
                        onClick={() => setExpanded(openThis ? null : v.id)}
                        className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-accent/50"
                      >
                        <ChevronRight
                          className={cn(
                            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                            openThis && "rotate-90",
                          )}
                        />
                        <span className="yj-json shrink-0 text-sm font-semibold">v{v.version}</span>
                        {v.is_current ? (
                          <Badge variant="success" className="text-[10px]">
                            当前版本
                          </Badge>
                        ) : null}
                        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                          {v.change_log || "（未记录变更说明）"}
                        </span>
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {v.operator_name ?? "—"}
                        </span>
                        <span className="yj-json shrink-0 text-[11px] text-muted-foreground">
                          {formatDateTime(v.created_at)}
                        </span>
                      </button>

                      {openThis ? (
                        <div className="border-t bg-muted/20 px-3 py-3">
                          <SnapshotView snapshot={v.snapshot} />
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">
                这道题还没有版本记录。保存一次改动后，这里会出现 v1 / v2 …
              </p>
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** 快照回放：把"那一刻"的题干/答案/解析按只读方式摊开。 */
function SnapshotView({ snapshot }: { snapshot: QuestionSnapshot }) {
  const options = Array.isArray(snapshot.options) ? [...snapshot.options] : [];
  options.sort((a, b) => (a.sort_no ?? 0) - (b.sort_no ?? 0));

  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-1.5">
        {snapshot.type ? (
          <Badge variant="outline" className="text-[10px]">
            {qTypeLabel(snapshot.type)}
          </Badge>
        ) : null}
        {snapshot.status ? (
          <Badge variant={qStatusVariant(snapshot.status)} className="text-[10px]">
            {qStatusLabel(snapshot.status)}
          </Badge>
        ) : null}
        {typeof snapshot.difficulty === "number" ? (
          <span className="text-[11px] text-muted-foreground">
            难度 {difficultyLabel(snapshot.difficulty)}
          </span>
        ) : null}
      </div>

      <Field label="题干">
        {snapshot.stem ? (
          <MarkdownPreview text={snapshot.stem} emptyHint="（空）" />
        ) : (
          <span className="text-xs text-muted-foreground">（空）</span>
        )}
      </Field>

      {options.length ? (
        <Field label="选项">
          <ul className="space-y-1">
            {options.map((o, i) => (
              <li key={`${o.label}-${i}`} className="flex items-start gap-2 text-xs">
                <span className="yj-json w-4 shrink-0 font-semibold">{o.label}</span>
                <span className={cn("min-w-0 flex-1", o.is_correct && "font-medium")}>
                  {o.content}
                </span>
                {o.is_correct ? (
                  <Badge variant="success" className="shrink-0 text-[10px]">
                    正确
                  </Badge>
                ) : null}
              </li>
            ))}
          </ul>
        </Field>
      ) : null}

      {snapshot.answer?.value?.length ? (
        <Field label="答案">
          <span className="yj-json text-xs">{snapshot.answer.value.map(String).join("、")}</span>
        </Field>
      ) : null}

      {snapshot.analysis ? (
        <Field label="解析">
          <MarkdownPreview text={snapshot.analysis} emptyHint="（空）" />
        </Field>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[11px] text-muted-foreground">{label}</div>
      <div>{children}</div>
    </div>
  );
}
