"use client";

import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InlineError } from "@/components/InlineError";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ImportBatch } from "@/lib/types";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 要回滚的批次（`null` 时不渲染内容，避免闪一帧空弹窗） */
  batch: Pick<
    ImportBatch,
    "id" | "batch_no" | "success_rows" | "updated_rows" | "duplicate_rows" | "file_name"
  > | null;
  loading?: boolean;
  /** 回滚请求的失败对象（react-query 的 `mutation.error`）。 */
  error?: unknown;
  onConfirm: (reason: string | undefined) => void | Promise<void>;
};

/**
 * 回滚二次确认。
 *
 * 回滚是**批量的、面向线上的**破坏性操作，所以确认框不能只写"确定吗？"：
 *
 *  1. **复述具体数字**（"将软删除 N 道题"）。而且 N 必须算对 ——
 *     后端 `success_rows` 是"写入成功行数"，**包含 upsert 命中的更新行**，
 *     那些题回滚时是被**还原**而不是被删除。
 *     直接拿 `success_rows` 当"将软删除"的数量，在 upsert 批次上会夸大。
 *     所以这里拆成两个数：
 *         软删除 = success_rows - updated_rows（本批真正新建的题）
 *         还原   = updated_rows             （本批覆盖过的老题，回到导入前那一版）
 *
 *  2. **要求手输批次号**。这是"破坏性操作的最后一道减速带"——
 *     复制粘贴一个批次号远比点一下按钮需要更多注意力，
 *     能挡住"点错了批次"和"连点两下"。
 *
 *  3. **说清不可自动撤销**。回滚本身会写审计与变更日志，但**没有"再撤销回滚"的入口**，
 *     要说出来，而不是让用户以为还能一键还原。
 *
 *  4. **失败必须在弹窗里说清**。回滚失败时弹窗**保持打开**（父层不在 catch 里关它），
 *     所以失败信息要画在弹窗内部 —— 否则用户只看到弹窗"闪了一下还在"，
 *     既不知道成没成，也找不到重试入口。`hint` 里点明"整批一个事务、失败即一道题都没动"，
 *     这是用户此刻最想知道的事。
 */
export function RollbackDialog({
  open,
  onOpenChange,
  batch,
  loading = false,
  error,
  onConfirm,
}: Props) {
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");

  // 每次打开都清空 —— 残留上一次的输入会让"手输批次号"这道减速带失效
  useEffect(() => {
    if (open) {
      setTyped("");
      setReason("");
    }
  }, [open]);

  const expected = batch?.batch_no ?? "";
  const inserts = Math.max(0, (batch?.success_rows ?? 0) - (batch?.updated_rows ?? 0));
  const updates = batch?.updated_rows ?? 0;
  const matched = typed.trim() === expected && expected !== "";

  return (
    <Dialog open={open} onOpenChange={loading ? undefined : onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            确认回滚这一批导入？
          </DialogTitle>
          <DialogDescription asChild>
            <div className="space-y-3 text-sm">
              {batch ? (
                <>
                  <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3">
                    <p className="leading-relaxed">
                      将<strong className="text-destructive">软删除 {inserts} 道题</strong>
                      {updates > 0 ? (
                        <>
                          ，并把 <strong>{updates}</strong> 道被本批覆盖过的题
                          <strong>还原到导入前的版本</strong>
                        </>
                      ) : null}
                      。
                    </p>
                    <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                      <li>· 新增 {inserts} 道 → 软删除（`is_deleted=true`，数据仍在库里可追溯）</li>
                      {updates > 0 ? <li>· 更新 {updates} 道 → 版本回退到导入前那一版</li> : null}
                      {(batch.duplicate_rows ?? 0) > 0 ? (
                        <li>· 跳过 {batch.duplicate_rows} 道重复题 → 本就不属于本批写入，不受影响</li>
                      ) : null}
                    </ul>
                  </div>

                  <p className="text-xs leading-relaxed text-muted-foreground">
                    <strong className="text-foreground">此操作不可自动撤销</strong>
                    ：回滚会写入审计日志与变更日志留下痕迹，但系统不提供"再撤销回滚"的入口。
                    如果只是想重新导入，回滚后重新走一次上传即可。
                  </p>

                  <div className="space-y-1.5">
                    <Label htmlFor="rb-reason" className="text-xs">
                      回滚原因（选填，会写进变更日志）
                    </Label>
                    <Input
                      id="rb-reason"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="例如：科目选错，需换到建筑实务重新导入"
                      maxLength={200}
                      disabled={loading}
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="rb-batchno" className="text-xs">
                      请输入批次号 <code className="yj-json text-foreground">{expected}</code> 以确认
                    </Label>
                    <Input
                      id="rb-batchno"
                      value={typed}
                      onChange={(e) => setTyped(e.target.value)}
                      placeholder={expected}
                      autoComplete="off"
                      disabled={loading}
                      aria-invalid={typed.length > 0 && !matched}
                      className={typed.length > 0 && !matched ? "border-destructive" : undefined}
                    />
                    {typed.length > 0 && !matched ? (
                      <p className="text-[11px] text-destructive">
                        批次号不一致。请完整输入 <code className="yj-json">{expected}</code>。
                      </p>
                    ) : null}
                  </div>
                </>
              ) : null}
            </div>
          </DialogDescription>
        </DialogHeader>

        {error ? (
          <InlineError
            title="回滚"
            error={error}
            // 复用弹窗自己持有的 `reason`，不必让父层再接一个回调 ——
            // 重试时用户刚填的回滚原因还在，不会被清掉。
            onRetry={() => void onConfirm(reason.trim() || undefined)}
            retrying={loading}
            hint="回滚是整批一个事务：失败即代表题库里一道题都没动，批次状态也不会变。可以直接重试；若持续失败，请把 trace_id 交给后端排查。"
          />
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            取消
          </Button>
          <Button
            variant="destructive"
            disabled={!matched || loading}
            onClick={() => void onConfirm(reason.trim() || undefined)}
          >
            {loading ? "回滚中…" : "确认回滚"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
