"use client";

import { CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { InlineError } from "@/components/InlineError";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { qStatusLabel } from "@/lib/question";
import type { QStatus } from "@/lib/types";

type Decision = "approve" | "reject";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 要审的题。`null` 时不渲染内容，避免闪一帧空弹窗。 */
  question: { id: string; status: QStatus; version: number } | null;
  loading?: boolean;
  /** 审核请求的失败对象（react-query 的 `mutation.error`）。 */
  error?: unknown;
  onConfirm: (decision: Decision, comment: string | undefined) => void | Promise<void>;
};

/**
 * 审核（通过 / 驳回）弹窗。
 *
 * ## 它不是"又一个确认框"，三条都来自既有约定
 *
 * 1. **复述关键信息**（`ConfirmDialog` 的注释）：确认框要说清"从什么变成什么"，
 *    所以标题区直接画出 `草稿 → 已发布`。用户点第二次时应该是**核对**，不是盲签。
 *
 * 2. **驳回理由必填，且做成按钮禁用**（硬约定 D）：不是在 `onClick` 里 `if (!reason) return`
 *    —— 那会让用户点了没反应，以为坏了。禁用 + 旁边写清原因才可理解。
 *    理由必填的原因也写出来：`rejected` 原本是个**没有理由落点的空转状态**，
 *    允许"驳回但不说为什么"等于只补了一半（`docs/15` §2.1）。
 *
 * 3. **通过时理由选填**：它是备注，不是必要信息。**不强迫**用户为了通过去编一句话。
 *
 * ## 失败时弹窗不关
 *
 * 与 `RollbackDialog` 同一条规矩：失败信息画在**弹窗内部**（`InlineError` + 重试），
 * 父层不在 catch 里关它。否则用户只看到弹窗"闪了一下还在"，
 * 既不知道成没成，也找不到重试入口。
 *
 * ⚠️ 最常见的失败是 `40901`（乐观锁 / 状态机拒绝）。后端消息里**本来就带了"下一步怎么做"**
 * （例如"已发布的题要下线请走归档"），`InlineError` 会全文展示，前端**不再自己拼文案**。
 */
export function QuestionReviewDialog({
  open,
  onOpenChange,
  question,
  loading = false,
  error,
  onConfirm,
}: Props) {
  const [decision, setDecision] = useState<Decision>("approve");
  const [comment, setComment] = useState("");

  // 每次打开都重置 —— 残留上一次的"驳回 + 理由"会让下一个人误提交别人的话
  useEffect(() => {
    if (open) {
      setDecision("approve");
      setComment("");
    }
  }, [open]);

  const trimmed = comment.trim();
  const rejectWithoutReason = decision === "reject" && trimmed === "";
  const current = question?.status ?? "draft";
  const target: QStatus = decision === "approve" ? "published" : "rejected";

  const submit = () => void onConfirm(decision, trimmed === "" ? undefined : trimmed);

  return (
    <Dialog open={open} onOpenChange={loading ? undefined : onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            审核这道题
          </DialogTitle>
          <DialogDescription asChild>
            <div className="text-sm">
              <span className="text-muted-foreground">状态将变为：</span>{" "}
              <span className="font-medium">{qStatusLabel(current)}</span>
              <span className="mx-1.5 text-muted-foreground">→</span>
              <span className="font-medium">{qStatusLabel(target)}</span>
            </div>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {/* ---------------- 结论 ---------------- */}
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={decision === "approve" ? "default" : "outline"}
              size="sm"
              className="justify-center"
              onClick={() => setDecision("approve")}
              disabled={loading}
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              通过并发布
            </Button>
            <Button
              type="button"
              variant={decision === "reject" ? "destructive" : "outline"}
              size="sm"
              className="justify-center"
              onClick={() => setDecision("reject")}
              disabled={loading}
            >
              <XCircle className="h-3.5 w-3.5" />
              驳回
            </Button>
          </div>

          {/* ---------------- 理由 ---------------- */}
          <div className="space-y-1.5">
            <Label htmlFor="q-review-comment" className="text-xs">
              审核意见
              {decision === "reject" ? (
                <span className="text-destructive">（驳回必填）</span>
              ) : (
                <span className="text-muted-foreground">（选填，可作备注）</span>
              )}
            </Label>
            <Input
              id="q-review-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={
                decision === "reject"
                  ? "例如：选项 B 与题干表述矛盾，请核对后重新提交"
                  : "例如：题面与解析一致，已核对来源"
              }
              maxLength={500}
              disabled={loading}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {decision === "reject"
                ? "驳回必须说明原因 —— 否则出题人只能自己猜。该理由会写进变更日志（完整保留）。"
                : "通过时这句话只会作为备注记入变更日志。"}
            </p>
          </div>

          {/* ---------------- 失败（弹窗保持打开，就地重试）---------------- */}
          {error ? (
            <InlineError
              title="审核"
              error={error}
              onRetry={loading ? undefined : submit}
              retrying={loading}
              hint="审核需要当前版本号一致（乐观锁）。若提示版本冲突，请关闭弹窗后刷新页面再操作。"
            />
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={loading}
          >
            取消
          </Button>
          <Button
            variant={decision === "reject" ? "destructive" : "default"}
            size="sm"
            onClick={submit}
            disabled={loading || rejectWithoutReason}
            title={rejectWithoutReason ? "驳回必须填写审核意见" : undefined}
          >
            {loading ? "提交中…" : decision === "approve" ? "确认通过" : "确认驳回"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
