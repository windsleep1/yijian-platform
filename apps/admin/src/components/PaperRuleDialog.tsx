"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { RuleEditor, StrategySelect, draftsToRules, emptyRuleDraft, ruleToDraft, validateDrafts, type RuleDraft } from "@/components/RuleEditor";
import { RulePreviewPanel } from "@/components/RulePreviewPanel";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreatePaperRule, usePreviewPaperRule, useUpdatePaperRule } from "@/hooks/useExams";
import { useChapterTree } from "@/hooks/useQuestions";
import { EXAM_TYPE_LABELS } from "@/lib/exam";
import type { ExamType, PaperRuleOut, RuleStatus, RuleStrategy } from "@/lib/types";

/** 试算的防抖间隔：改一个数字就打一次接口太吵，600ms 够用且手感跟得上。 */
const PREVIEW_DEBOUNCE_MS = 600;

/**
 * 新建 / 编辑组卷规则。
 *
 * ## 这一页的核心是「**保存之前就知道能抽到什么程度**」
 *
 * 没有试算的话，用户的路径是：
 * `保存 → 去建卷 → 组卷 → 发现抽不满 → 回来改 → 再走一遍`，
 * 一轮好几次往返，而且**每次都真实落库**（留下一条抽不满的规则和一张残缺的卷）。
 *
 * 所以这里把 dry-run 做成**实时**的：改完规则 600ms 后自动试算，
 * 结果就地显示。保存时如果还有缺口，会**再确认一次**（但不阻止保存 ——
 * "先把规则存下来、等题库补齐"是完全合理的用法）。
 */
export function PaperRuleDialog({
  open,
  onOpenChange,
  rule,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 传了就是编辑，不传就是新建 */
  rule?: PaperRuleOut | null;
}) {
  const isEdit = !!rule;
  const create = useCreatePaperRule();
  const update = useUpdatePaperRule();
  const preview = usePreviewPaperRule();

  const tree = useChapterTree();
  const subjects = useMemo(() => (tree.data?.items ?? []).map((g) => g.subject), [tree.data]);

  const [name, setName] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [type, setType] = useState<ExamType>("mock");
  const [duration, setDuration] = useState("180");
  const [strategy, setStrategy] = useState<RuleStrategy>("random");
  const [status, setStatus] = useState<RuleStatus>("on");
  const [drafts, setDrafts] = useState<RuleDraft[]>([emptyRuleDraft()]);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const saving = create.isPending || update.isPending;

  // 打开时用最新的服务端数据填充。
  // 依赖用 [open]（而不是 rule 对象）：列表后台 refetch 不该冲掉正在填的内容。
  useEffect(() => {
    if (!open) return;
    setName(rule?.name ?? "");
    setSubjectId(rule?.subject_id ?? "");
    setType(rule?.type ?? "mock");
    setDuration(String(rule?.duration_min ?? 180));
    setStrategy(rule?.strategy ?? "random");
    setStatus(rule?.status ?? "on");
    setDrafts(rule ? rule.rules.map(ruleToDraft) : [emptyRuleDraft()]);
    preview.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const issues = validateDrafts(drafts);
  const canPreview = !!subjectId && issues.length === 0;

  // ---------------------------------------------------------------- 实时试算（防抖）
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!open) return;
    if (timer.current) clearTimeout(timer.current);
    if (!canPreview) {
      preview.reset();
      return;
    }
    timer.current = setTimeout(() => {
      preview.mutate({
        subject_id: subjectId,
        rules: draftsToRules(drafts),
        strategy,
        // 固定 seed：调参时结果稳定，用户才能对照"我改了这条，抽出多少变没变"
        seed: 20260101,
        sample_limit: 12,
      });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, subjectId, strategy, JSON.stringify(drafts)]);

  const nameBad = !name.trim();
  const durationNum = Number(duration);
  const durationBad = !Number.isFinite(durationNum) || durationNum < 1 || durationNum > 600;
  const formBad = nameBad || !subjectId || durationBad || issues.length > 0;

  /**
   * ⚠️ **试算没回来之前不许保存。**
   *
   * 踩过一次：保存按钮只看 `preview.data` 判断要不要弹"题库不足"的确认框 ——
   * 而试算有 600ms 防抖 + 一次网络往返，**在这期间 `preview.data` 是 null**，
   * 于是 `gap === false`，点"保存规则"就**直接把缺口预警绕过去了**。
   * 实测：脚本改完题数立刻保存，缺口确认框一次都没出现，规则照样写进了库。
   *
   * 教训：**拿异步结果当闸门，就必须等结果回来才放行** ——
   * "还没结论" 和 "结论是通过" 是两件事，不能都当成"可以通过"。
   * 这里把"等结论"直接做成按钮禁用，而不是让用户点下去以后才发现。
   */
  const awaitingVerdict = canPreview && !preview.data && !preview.error;
  const gap = preview.data ? !preview.data.ok : false;

  const submit = async () => {
    const payload = {
      name: name.trim(),
      subject_id: subjectId,
      type,
      duration_min: durationNum,
      rules: draftsToRules(drafts),
      strategy,
      ...(isEdit ? { status } : {}),
    };
    try {
      if (isEdit && rule) await update.mutateAsync({ id: rule.id, payload });
      else await create.mutateAsync(payload);
      onOpenChange(false);
    } catch {
      // 统一错误 toast 由 MutationCache 负责；保持对话框打开便于修正
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={saving ? undefined : onOpenChange}>
        <DialogContent className="max-w-5xl">
          <DialogHeader>
            <DialogTitle>{isEdit ? "编辑组卷规则" : "新建组卷规则"}</DialogTitle>
            <DialogDescription>
              规则描述**要什么题**（题型 / 题数 / 分值 / 难度 / 年份）。
              能不能凑够由组卷时决定 —— 凑不够会如实报缺口，**系统不会用别的题顶替**。
              <span className="mt-1 block text-amber-700">
                下面会实时试算：告诉你按现在这套规则**能抽出多少题**，题库够不够。
              </span>
            </DialogDescription>
          </DialogHeader>

          <div className="grid max-h-[70vh] gap-4 overflow-y-auto pr-1 lg:grid-cols-[1.15fr_1fr]">
            {/* ---------------- 左：表单 ---------------- */}
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="rule-name">规则名称</Label>
                <Input
                  id="rule-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={160}
                  placeholder="如：2026 建筑实务模拟卷（标准）"
                />
                {nameBad ? <p className="text-[11px] text-destructive">名称不能为空。</p> : null}
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>科目</Label>
                  <Select value={subjectId || "__none__"} onValueChange={(v) => setSubjectId(v === "__none__" ? "" : v)}>
                    <SelectTrigger>
                      <SelectValue placeholder={tree.isLoading ? "加载中…" : "请选择科目"} />
                    </SelectTrigger>
                    <SelectContent>
                      {subjects.map((s) => (
                        <SelectItem key={s.id} value={s.id}>
                          {s.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!subjectId ? (
                    <p className="text-[11px] text-muted-foreground">
                      选定科目后才能试算（题目是在科目范围内抽的）。
                    </p>
                  ) : null}
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
                  <Label htmlFor="rule-duration">考试时长（分钟）</Label>
                  <Input
                    id="rule-duration"
                    value={duration}
                    inputMode="numeric"
                    onChange={(e) => setDuration(e.target.value.replace(/[^\d]/g, ""))}
                  />
                  {durationBad ? (
                    <p className="text-[11px] text-destructive">时长需在 1~600 分钟之间。</p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
                  <Label>抽题策略</Label>
                  <StrategySelect value={strategy} onChange={setStrategy} />
                </div>

                {isEdit ? (
                  <div className="space-y-1.5">
                    <Label>启用状态</Label>
                    <Select value={status} onValueChange={(v) => setStatus(v as RuleStatus)}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="on">启用</SelectItem>
                        <SelectItem value="off">停用（组卷时会拒绝使用）</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                ) : null}
              </div>

              <div className="space-y-2 rounded-md border p-3">
                <div className="flex items-center justify-between">
                  <Label className="text-xs text-muted-foreground">抽题约束</Label>
                  <span className="text-[11px] text-muted-foreground">
                    每条 = 一类题的抽取条件
                  </span>
                </div>
                <RuleEditor drafts={drafts} onChange={setDrafts} disabled={saving} />
              </div>
            </div>

            {/* ---------------- 右：试算 ---------------- */}
            <div className="space-y-2">
              <Label className="text-xs text-muted-foreground">试算结果（dry-run，不落库）</Label>
              <RulePreviewPanel
                preview={canPreview ? preview.data ?? null : null}
                pending={canPreview && preview.isPending}
                error={canPreview ? preview.error : undefined}
                onRefresh={() =>
                  preview.mutate({
                    subject_id: subjectId,
                    rules: draftsToRules(drafts),
                    strategy,
                    seed: 20260101,
                  })
                }
              />
            </div>
          </div>

          <DialogFooter>
            {formBad ? (
              <span className="mr-auto flex items-center gap-1.5 text-[11px] text-destructive">
                <AlertTriangle className="h-3.5 w-3.5" />
                {issues.length > 0
                  ? `有 ${issues.length} 处规则填写问题，修好才能保存`
                  : nameBad
                    ? "请填写规则名称"
                    : durationBad
                      ? "时长不合法"
                      : "请选择科目"}
              </span>
            ) : gap ? (
              <span className="mr-auto flex items-center gap-1.5 text-[11px] text-amber-700">
                <AlertTriangle className="h-3.5 w-3.5" />
                当前题库不足，仍可保存（点保存时会再确认一次）
              </span>
            ) : awaitingVerdict ? (
              <span className="mr-auto flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在试算，等结论出来才能保存（避免绕过"题库不足"的确认）
              </span>
            ) : null}
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
              取消
            </Button>
            <Button
              disabled={formBad || saving || awaitingVerdict}
              onClick={() => (gap ? setConfirmOpen(true) : void submit())}
            >
              {saving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  保存中…
                </>
              ) : (
                "保存规则"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- 题库不足时的二次确认：允许保存，但要让用户明确知道代价 ---- */}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="题库不足，仍要保存这条规则？"
        loading={saving}
        confirmText="仍然保存"
        description={
          <div className="space-y-2 text-sm">
            <p className="text-amber-700">
              按当前规则，需要 <strong>{preview.data?.total_need}</strong> 道题，
              但只能抽到 <strong>{preview.data?.total_got}</strong> 道，
              还差 <strong>{preview.data?.total_missing}</strong> 道。
            </p>
            <p className="text-xs text-muted-foreground">
              保存后用它组卷会出现<strong>同样的缺口</strong> ——
              系统不会用别的题顶替，抽不满的卷还需要人工补题才能发布。
            </p>
            <p className="text-xs text-muted-foreground">
              如果只是"先把规则存下来、等题库补齐"，那就保存；
              如果现在就要出一份完整的卷，建议先放宽条件（减少题数 / 放开难度范围 / 去掉年份限制）。
            </p>
          </div>
        }
        onConfirm={() => {
          setConfirmOpen(false);
          void submit();
        }}
      />
    </>
  );
}
