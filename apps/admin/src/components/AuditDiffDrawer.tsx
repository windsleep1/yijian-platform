"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { CheckCircle2, Copy, FileJson, ListTree, X, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { diffJson, formatDateTime, previewValue, prettyJson } from "@/lib/format";
import type { AuditLogItem } from "@/lib/types";
import { cn } from "@/lib/utils";

type Tab = "diff" | "raw";

/**
 * 审计详情抽屉。
 *
 * 列表页故意只展示 `action + entity + actor + 时间`（一屏能扫完），
 * "到底改了什么"放在抽屉里 —— 这正是后端把 `before_data` / `after_data` 一起返回的原因。
 *
 * 两种视图：
 *   - **变更字段（默认）**：把 before/after 拍平成"路径 + 改前 + 改后"，只列**变化**的项并高亮。
 *     审一次角色变更，需要回答的是"从什么变成什么"，而不是读两坨 JSON 做心算。
 *   - **原始 JSON**：并排两栏，给需要核对完整快照的场景（比如排查"某个字段为什么没了"）。
 */
export function AuditDetailDrawer({
  log,
  open,
  onOpenChange,
}: {
  log: AuditLogItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [tab, setTab] = useState<Tab>("diff");

  const rows = useMemo(() => {
    if (!log) return [];
    return diffJson(log.before_data, log.after_data);
  }, [log]);

  const copy = (text: string, label: string) => {
    void navigator.clipboard?.writeText(text);
    toast.success(`${label} 已复制`);
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-2xl flex-col border-l bg-background shadow-xl duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right">
          <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="flex items-center gap-2 text-base font-semibold">
                审计详情
                {log?.success ? (
                  <Badge variant="success" className="gap-1">
                    <CheckCircle2 className="h-3 w-3" />
                    成功
                  </Badge>
                ) : (
                  <Badge variant="destructive" className="gap-1">
                    <XCircle className="h-3 w-3" />
                    失败
                  </Badge>
                )}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-1 text-xs text-muted-foreground">
                记录 ID {log?.id}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0">
                <X className="h-4 w-4" />
                <span className="sr-only">关闭</span>
              </Button>
            </DialogPrimitive.Close>
          </div>

          {log ? (
            <>
              {/* ---- 元信息 ---- */}
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-b px-5 py-4 text-sm">
                <Meta label="操作">
                  <code className="yj-json rounded bg-muted px-1.5 py-0.5 text-xs">
                    {log.action}
                  </code>
                </Meta>
                <Meta label="时间">{formatDateTime(log.created_at)}</Meta>
                <Meta label="操作人">
                  {log.actor_name || "—"}
                  {log.actor_id ? (
                    <button
                      type="button"
                      onClick={() => copy(log.actor_id!, "操作人 ID")}
                      className="ml-1 text-muted-foreground hover:text-foreground"
                      title="复制操作人 ID"
                    >
                      <Copy className="inline h-3 w-3" />
                    </button>
                  ) : null}
                </Meta>
                <Meta label="操作对象">
                  {log.entity_type ? (
                    <span>
                      <code className="yj-json rounded bg-muted px-1.5 py-0.5 text-xs">
                        {log.entity_type}
                      </code>
                      <span className="ml-1 text-muted-foreground">#{log.entity_id ?? "—"}</span>
                    </span>
                  ) : (
                    "—"
                  )}
                </Meta>
                <Meta label="来源 IP">{log.ip ?? "—"}</Meta>
                <Meta label="请求">
                  {log.method ? (
                    <span>
                      <Badge variant="secondary" className="yj-json mr-1 text-[10px]">
                        {log.method}
                      </Badge>
                      <span className="yj-json break-all text-xs text-muted-foreground">
                        {log.path}
                      </span>
                    </span>
                  ) : (
                    "—"
                  )}
                </Meta>
                {log.error_msg ? (
                  <Meta label="错误信息" className="col-span-2">
                    <span className="text-destructive">{log.error_msg}</span>
                  </Meta>
                ) : null}
              </dl>

              {/* ---- 视图切换 ---- */}
              <div className="flex items-center gap-2 border-b px-5 py-2">
                <Button
                  size="sm"
                  variant={tab === "diff" ? "default" : "ghost"}
                  onClick={() => setTab("diff")}
                >
                  <ListTree className="h-3.5 w-3.5" />
                  变更字段
                  {rows.length ? (
                    <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-[10px]">
                      {rows.length}
                    </Badge>
                  ) : null}
                </Button>
                <Button
                  size="sm"
                  variant={tab === "raw" ? "default" : "ghost"}
                  onClick={() => setTab("raw")}
                >
                  <FileJson className="h-3.5 w-3.5" />
                  原始 JSON
                </Button>
              </div>

              <div className="flex-1 overflow-y-auto px-5 py-4">
                {tab === "diff" ? (
                  rows.length ? (
                    <div className="overflow-hidden rounded-md border">
                      <table className="w-full text-sm">
                        <thead className="bg-muted/50 text-xs text-muted-foreground">
                          <tr>
                            <th className="w-[38%] px-3 py-2 text-left font-medium">字段路径</th>
                            <th className="px-3 py-2 text-left font-medium">改前</th>
                            <th className="px-3 py-2 text-left font-medium">改后</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((r) => (
                            <tr key={r.path} className="border-t">
                              <td className="yj-json px-3 py-2 align-top text-xs">{r.path}</td>
                              <td className="yj-json px-3 py-2 align-top text-xs">
                                <ValueCell value={r.before} kind={r.kind === "added" ? "missing" : "before"} />
                              </td>
                              <td className="yj-json px-3 py-2 align-top text-xs">
                                <ValueCell value={r.after} kind={r.kind === "removed" ? "missing" : "after"} />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="py-10 text-center text-sm text-muted-foreground">
                      这条记录没有字段级变更（可能是一次查询、或 before/after 均为空）。
                    </p>
                  )
                ) : (
                  <div className="grid gap-4 md:grid-cols-2">
                    <pre className="yj-json overflow-x-auto rounded-md border bg-muted/40 p-3">
                      <span className="mb-2 block font-sans text-xs font-medium text-muted-foreground">
                        before_data
                      </span>
                      {prettyJson(log.before_data)}
                    </pre>
                    <pre className="yj-json overflow-x-auto rounded-md border bg-emerald-50 p-3">
                      <span className="mb-2 block font-sans text-xs font-medium text-emerald-800">
                        after_data
                      </span>
                      {prettyJson(log.after_data)}
                    </pre>
                  </div>
                )}
              </div>
            </>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function Meta({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn(className)}>
      <dt className="mb-0.5 text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all">{children}</dd>
    </div>
  );
}

/** 变化项高亮：新增绿底、移除红删除线、修改黄底。 */
function ValueCell({ value, kind }: { value: unknown; kind: "before" | "after" | "missing" }) {
  if (kind === "missing") {
    return <span className="text-muted-foreground">—</span>;
  }
  const text = previewValue(value);
  return (
    <span
      className={cn(
        "rounded px-1 py-0.5",
        kind === "after"
          ? "bg-emerald-100 text-emerald-900"
          : "bg-amber-100 text-amber-900 line-through decoration-amber-500/60",
      )}
    >
      {text}
    </span>
  );
}
