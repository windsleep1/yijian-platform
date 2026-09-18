"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** 必须写清"这一下点下去会发生什么"，包括影响范围和是否可以撤销。 */
  description?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  destructive?: boolean;
  loading?: boolean;
  /**
   * 额外禁用确认键（如"必须先勾选『我明白』"）。
   *
   * 存在的理由：把"前置条件"做成**按钮禁用**而不是"点了没反应"。
   * 早期做法是在 `onConfirm` 里 `if (!ack) return;` —— 用户点了按钮，
   * 界面毫无变化，只会以为坏了。禁用键 + 旁边写清原因，才是可理解的交互。
   */
  confirmDisabled?: boolean;
  /** 禁用确认键时显示的原因（会出现在按钮的 title 上） */
  confirmDisabledReason?: string;
  onConfirm: () => void | Promise<void>;
};

/**
 * 二次确认原语。
 *
 * B 端里"不可撤销的写操作"必须二次确认，而且确认框要**复述关键信息**
 * （改的是谁、从什么变成什么），不能只写"确定吗？"——
 * 用户点第二次时应该是"核对"，不是"盲签"。
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText = "确认",
  cancelText = "取消",
  destructive = false,
  loading = false,
  confirmDisabled = false,
  confirmDisabledReason,
  onConfirm,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={loading ? undefined : onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription asChild>{description}</DialogDescription> : null}
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={() => void onConfirm()}
            disabled={loading || confirmDisabled}
            title={confirmDisabled ? confirmDisabledReason : undefined}
          >
            {loading ? "处理中…" : confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
