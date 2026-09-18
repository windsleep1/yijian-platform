"use client";

import { AlertTriangle, Plus, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ConfirmDialog";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useReplaceExamSections } from "@/hooks/useExams";
import { ApiError } from "@/lib/api";
import { qTypeLabel } from "@/lib/question";
import type { ExamDetail, ExamSectionIn, QType } from "@/lib/types";

const PICKABLE_TYPES: QType[] = ["single", "multiple", "judge", "case", "fill", "essay"];

type Draft = {
  name: string;
  question_type: QType;
  question_count: string;
  score_per: string;
};

function toDraft(exam: ExamDetail): Draft[] {
  return exam.sections.map((s) => ({
    name: s.name,
    question_type: s.question_type,
    question_count: String(s.question_count),
    score_per: String(s.score_per),
  }));
}

/**
 * 编辑**卷面结构**（分段与计划题数）。
 *
 * ## ⚠️ 这一步会清空卷面题目
 *
 * 后端 `update_exam` 收到 `sections` 时会先
 * `DELETE FROM exam_questions WHERE exam_id = ...` 再重建分段。
 * 语义上说得通 —— **分段是卷面的骨架，换骨架必然要重排题目** ——
 * 但代价是"现有 N 道题全部离开卷面"（题目本身在题库里不受影响）。
 *
 * 所以这里做了三件事，缺一不可：
 *
 * 1. **明确说出会被清掉多少题**（不是"可能影响"，是"将清空现有的 N 道题"）；
 * 2. **必须勾选"我明白"才能确认** —— 让用户的手停一下，
 *    而不是连点两下就过去了；
 * 3. **已发布且未开「允许发布后编辑」时直接禁用入口**，
 *    而不是让用户填完一屏再吃一个 40901。
 *
 * 这条与 `docs` 里「破坏性操作的确认框，数字要按**即将执行的动作**分类算」
 * 是同一条约定：确认框里的数字必须是"这一下会清掉几道题"，
 * 而不是照抄页面顶部的总题数。
 */
export function ExamSectionsDialog({
  open,
  onOpenChange,
  exam,
  onRefresh,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  exam: ExamDetail;
  /** 乐观并发冲突时让父组件刷新详情（拿最新的 question_count 重试） */
  onRefresh?: () => void;
}) {
  const replace = useReplaceExamSections(exam.id);
  const [drafts, setDrafts] = useState<Draft[]>(() => toDraft(exam));
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [ack, setAck] = useState(false);
  /** 「卷面已变化」冲突：单独提示，因为这需要用户**先刷新**才能继续 */
  const [stale, setStale] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDrafts(toDraft(exam));
    setAck(false);
    setStale(false);
    // 依赖 [open]：详情页后台 refetch 不该冲掉正在填的表单
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const frozen = exam.status === "published" && !exam.can_edit;
  const plannedTotal = useMemo(
    () => drafts.reduce((s, d) => s + (Number(d.question_count) || 0), 0),
    [drafts],
  );

  const patch = (i: number, next: Partial<Draft>) =>
    setDrafts((prev) => prev.map((d, idx) => (idx === i ? { ...d, ...next } : d)));

  const addRow = () =>
    setDrafts((prev) => [
      ...prev,
      { name: `第 ${prev.length + 1} 部分`, question_type: "single", question_count: "0", score_per: "1" },
    ]);

  const removeRow = (i: number) => setDrafts((prev) => prev.filter((_, idx) => idx !== i));

  const invalid = drafts.some(
    (d) =>
      !d.name.trim() ||
      !Number.isFinite(Number(d.question_count)) ||
      Number(d.question_count) < 0 ||
      !Number.isFinite(Number(d.score_per)) ||
      Number(d.score_per) <= 0,
  );

  const payloadSections: ExamSectionIn[] = drafts.map((d, i) => ({
    name: d.name.trim(),
    question_type: d.question_type,
    question_count: Number(d.question_count) || 0,
    score_per: Number(d.score_per) || 1,
    sort_no: i,
  }));

  return (
    <>
      <Dialog open={open} onOpenChange={replace.isPending ? undefined : onOpenChange}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>编辑卷面结构</DialogTitle>
            <DialogDescription>
              分段是卷面的骨架：每个分段声明「哪个题型、多少道、每题多少分」。
              发布前的校验会拿实际题数与计划题数逐段比对。
            </DialogDescription>
          </DialogHeader>

          {frozen ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
              这份试卷<strong>已发布</strong>，卷面结构不可再改。
              如需调整，请先把它下线到草稿态，或新建一份试卷。
            </div>
          ) : null}

          {stale ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
              <p className="font-medium text-destructive">卷面已变化，请刷新后重试</p>
              <p className="mt-1 text-muted-foreground">
                从你打开这个对话框到现在，这张卷的题数被改过了
                （当前是 <strong>{exam.question_count}</strong> 道）。
                为避免误清掉别人刚加的题，后端拒绝了这次提交，<strong>没有写入任何数据</strong>。
                点「刷新」拿到最新版本后，请重新确认一遍再提交。
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => {
                  onRefresh?.();
                  setStale(false);
                  setDrafts(toDraft(exam));
                }}
              >
                <RotateCw className="h-3.5 w-3.5" />
                刷新并重填
              </Button>
            </div>
          ) : null}

          {/* ⚠️ 最要紧的一句话放最上面 */}
          <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="space-y-1">
              <p className="font-medium">
                保存会<strong>清空卷面现有的 {exam.question_count} 道题</strong>，并重建分段。
              </p>
              <p>
                分段是卷面的骨架，重建骨架必须重排题目 —— 后端会把
                `exam_questions` 里这张卷的行全部删掉再按新分段重建。
                <strong>题目本身在题库里不受影响</strong>，之后可以重新组卷或手动加题补回来。
              </p>
            </div>
          </div>

          <div className="space-y-2">
            {drafts.map((d, i) => (
              <div key={i} className="grid grid-cols-12 items-end gap-2 rounded-md border p-2">
                <div className="col-span-4 space-y-1">
                  <Label className="text-[11px] text-muted-foreground">分段名称</Label>
                  <Input
                    value={d.name}
                    onChange={(e) => patch(i, { name: e.target.value })}
                    maxLength={96}
                    className="h-9"
                  />
                </div>
                <div className="col-span-3 space-y-1">
                  <Label className="text-[11px] text-muted-foreground">题型</Label>
                  <Select
                    value={d.question_type}
                    onValueChange={(v) => patch(i, { question_type: v as QType })}
                  >
                    <SelectTrigger className="h-9">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PICKABLE_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {qTypeLabel(t)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="col-span-2 space-y-1">
                  <Label className="text-[11px] text-muted-foreground">计划题数</Label>
                  <Input
                    value={d.question_count}
                    inputMode="numeric"
                    onChange={(e) => patch(i, { question_count: e.target.value.replace(/[^\d]/g, "") })}
                    className="h-9"
                  />
                </div>
                <div className="col-span-2 space-y-1">
                  <Label className="text-[11px] text-muted-foreground">每题分值</Label>
                  <Input
                    value={d.score_per}
                    inputMode="decimal"
                    onChange={(e) => patch(i, { score_per: e.target.value.replace(/[^\d.]/g, "") })}
                    className="h-9"
                  />
                </div>
                <div className="col-span-1 flex justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-9 px-2 text-muted-foreground hover:text-destructive"
                    disabled={drafts.length <= 1}
                    onClick={() => removeRow(i)}
                    title={drafts.length <= 1 ? "至少要保留一个分段" : "删除该分段"}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            ))}

            <div className="flex items-center justify-between">
              <Button variant="outline" size="sm" onClick={addRow}>
                <Plus className="h-3.5 w-3.5" />
                添加分段
              </Button>
              <span className="text-xs text-muted-foreground">
                计划合计 <span className="font-medium text-foreground">{plannedTotal}</span> 道
                · 满分{" "}
                <span className="font-medium text-foreground">
                  {payloadSections.reduce((s, x) => s + x.question_count * x.score_per, 0)}
                </span>{" "}
                分
              </span>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={replace.isPending}>
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={frozen || invalid || replace.isPending || drafts.length === 0}
              onClick={() => setConfirmOpen(true)}
            >
              保存卷面结构
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- 二次确认：数字按「即将执行的动作」算，并且必须显式勾选 ---- */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={(o) => {
          setConfirmOpen(o);
          if (!o) setAck(false);
        }}
        title="确认重建卷面结构？"
        destructive
        loading={replace.isPending}
        confirmText={`清空 ${exam.question_count} 道题并保存`}
        confirmDisabled={!ack}
        confirmDisabledReason="请先勾选下方「我明白…」"
        description={
          <div className="space-y-3 text-sm">
            <p>
              即将把卷面结构改为 <strong>{drafts.length}</strong> 个分段、
              计划 <strong>{plannedTotal}</strong> 道题。
            </p>
            <p className="rounded-md bg-destructive/10 p-2 text-destructive">
              这会<strong>清空卷面现有的 {exam.question_count} 道题</strong>
              （`exam_questions` 里这张卷的行会被删除并重建）。
              题目本身在题库里不受影响，但需要重新组卷或手动加题补回来。
            </p>
            <label className="flex items-start gap-2 text-xs">
              <Checkbox
                checked={ack}
                onCheckedChange={(v) => setAck(v === true)}
                className="mt-0.5"
              />
              <span>
                我明白这会清空卷面现有的 {exam.question_count} 道题，
                且需要重新补题才能发布。
              </span>
            </label>
          </div>
        }
        onConfirm={async () => {
          if (!ack) return;
          try {
            await replace.mutateAsync({
              sections: payloadSections,
              // ⚠️ 乐观并发：把**我读到的**当前卷面题数交给后端核对。
              // 期间有人改过这张卷 → 后端 40901，不会把对方的题默默清掉。
              expected_question_count: exam.question_count,
            });
            setConfirmOpen(false);
            setAck(false);
            onOpenChange(false);
          } catch (err) {
            // 「卷面已变化」要单独处理：用户必须先刷新拿到最新数据，
            // 否则改多少次都会撞同一堵墙（错误 toast 只说了一次，容易漏）。
            if (err instanceof ApiError && err.code === 40901 && err.message.includes("卷面已变化")) {
              setStale(true);
              setConfirmOpen(false);
              setAck(false);
              onRefresh?.();
            }
            // 其余错误的 toast 已由 MutationCache 统一弹出；保持对话框打开便于重试
          }
        }}
      />
    </>
  );
}
