"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { useUpdateExam } from "@/hooks/useExams";
import { EXAM_TYPE_LABELS } from "@/lib/exam";
import type { ExamDetail, ExamType } from "@/lib/types";

/**
 * 编辑试卷的**基本信息**。
 *
 * ## 一个必须说清的设计约束
 *
 * 这个弹窗**只发非 `sections` 字段**。原因在后端：
 * `update_exam` 收到 `sections` 时会
 * `DELETE FROM exam_questions WHERE exam_id = ...` —— **整卷题目会被清空**。
 *
 * "分段结构是卷面的骨架，重建骨架必然要重排题目"这个语义本身是合理的，
 * 但它绝不该被"我只是想改个时长"顺手触发。所以**两件事分成两个入口**：
 *
 * - 本弹窗：标题 / 类型 / 年份 / 卷号 / 时长 / 及格线 / 是否免费 / 简介 —— 安全，随时可改；
 * - 「编辑卷面结构」：改分段与计划题数 —— **会清空题目**，需要单独确认（见 `ExamSectionsDialog`）。
 *
 * 弹窗里会明写这句，避免用户以为"格式那一栏也能一起改"。
 */
export function ExamBasicInfoDialog({
  open,
  onOpenChange,
  exam,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  exam: ExamDetail;
}) {
  const update = useUpdateExam(exam.id);

  const [title, setTitle] = useState(exam.title);
  const [type, setType] = useState<ExamType>(exam.type);
  const [examYear, setExamYear] = useState(exam.exam_year ? String(exam.exam_year) : "");
  const [paperNo, setPaperNo] = useState(exam.paper_no ?? "");
  const [duration, setDuration] = useState(String(exam.duration_min));
  const [passScore, setPassScore] = useState(String(exam.pass_score));
  const [isFree, setIsFree] = useState(exam.is_free);
  const [introHtml, setIntroHtml] = useState(exam.intro_html ?? "");

  // 依赖用 [open] 而不是整个 exam —— 详情页会后台 refetch，
  // 依赖对象会冲掉用户正在填的内容（与题库编辑页同一条坑）。
  useEffect(() => {
    if (!open) return;
    setTitle(exam.title);
    setType(exam.type);
    setExamYear(exam.exam_year ? String(exam.exam_year) : "");
    setPaperNo(exam.paper_no ?? "");
    setDuration(String(exam.duration_min));
    setPassScore(String(exam.pass_score));
    setIsFree(exam.is_free);
    setIntroHtml(exam.intro_html ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const durationNum = Number(duration);
  const passNum = Number(passScore);
  const durationBad = !Number.isFinite(durationNum) || durationNum < 1 || durationNum > 600;
  const passBad = !Number.isFinite(passNum) || passNum < 0 || passNum > 1000;
  const titleBad = !title.trim();

  const submit = async () => {
    try {
      await update.mutateAsync({
        title: title.trim(),
        type,
        exam_year: examYear ? Number(examYear) : null,
        paper_no: paperNo.trim() || null,
        duration_min: durationNum,
        pass_score: passNum,
        is_free: isFree,
        intro_html: introHtml.trim() || null,
      });
      onOpenChange(false);
    } catch {
      // 统一错误 toast 在 Providers 的 MutationCache.onError 里；
      // 这里保持弹窗打开，方便用户改完再试。
    }
  };

  return (
    <Dialog open={open} onOpenChange={update.isPending ? undefined : onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑基本信息</DialogTitle>
          <DialogDescription>
            这里<strong>不包含卷面结构</strong>。分段与计划题数在「编辑卷面结构」里
            —— 那一步会清空题目，所以单独确认。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="exam-title">试卷标题</Label>
            <Input
              id="exam-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
            {titleBad ? <p className="text-[11px] text-destructive">标题不能为空。</p> : null}
          </div>

          <div className="space-y-1.5">
            <Label>试卷类型</Label>
            <Select value={type} onValueChange={(v) => setType(v as ExamType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(EXAM_TYPE_LABELS) as ExamType[]).map((t) => (
                  <SelectItem key={t} value={t}>
                    {EXAM_TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exam-year">考试年份</Label>
            <Input
              id="exam-year"
              value={examYear}
              inputMode="numeric"
              placeholder="如 2026；留空表示不限"
              onChange={(e) => setExamYear(e.target.value.replace(/[^\d]/g, "").slice(0, 4))}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exam-paper-no">卷号</Label>
            <Input
              id="exam-paper-no"
              value={paperNo}
              placeholder="如 A 卷"
              maxLength={32}
              onChange={(e) => setPaperNo(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exam-duration">考试时长（分钟）</Label>
            <Input
              id="exam-duration"
              value={duration}
              inputMode="numeric"
              onChange={(e) => setDuration(e.target.value.replace(/[^\d]/g, ""))}
            />
            {durationBad ? (
              <p className="text-[11px] text-destructive">时长需在 1 ~ 600 分钟之间。</p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="exam-pass">及格分</Label>
            <Input
              id="exam-pass"
              value={passScore}
              inputMode="decimal"
              onChange={(e) => setPassScore(e.target.value.replace(/[^\d.]/g, ""))}
            />
            {passBad ? (
              <p className="text-[11px] text-destructive">及格分需在 0 ~ 1000 之间。</p>
            ) : null}
            {passNum === 0 ? (
              <p className="text-[11px] text-amber-700">
                填 0 表示不设及格线：校验会给一条提醒（不影响发布）。
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="exam-intro">试卷简介（可选）</Label>
            <Input
              id="exam-intro"
              value={introHtml}
              maxLength={2000}
              onChange={(e) => setIntroHtml(e.target.value)}
            />
          </div>

          <label className="flex w-fit cursor-pointer items-center gap-2 text-sm sm:col-span-2">
            <Checkbox checked={isFree} onCheckedChange={(v) => setIsFree(v === true)} />
            <span>免费开放（勾选后学员无需购买即可作答）</span>
          </label>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={update.isPending}>
            取消
          </Button>
          <Button
            disabled={update.isPending || titleBad || durationBad || passBad}
            onClick={() => void submit()}
          >
            {update.isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                保存中…
              </>
            ) : (
              "保存"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
