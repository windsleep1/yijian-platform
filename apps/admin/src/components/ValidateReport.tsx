"use client";

import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { sectionGap, validateGuidance } from "@/lib/exam";
import type { ExamSectionOut, ExamValidateOut } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 卷面校验报告的**结构化**展示。
 *
 * 三件事必须做对（否则校验就等于没做）：
 *
 * 1. **分段缺口要说人话。** 后端 `SECTION_NOT_FILLED` 的原文是
 *    "分段「单项选择题」计划 60 道，实际 52 道" —— 准确但要求读者自己做减法、
 *    还要意识到"这属于哪一类问题"。这里改成 **「还差 8 道单项选择题」**，
 *    并**整段标红**（用户提的要求：对不上时整段标红，不要只给数字差异）。
 * 2. **每条都给"下一步怎么做"。** 只说问题不说办法，用户看完仍然卡在原地。
 *    映射表在 `lib/exam.ts` 的 `VALIDATE_GUIDANCE`。
 * 3. **分清"必须修"和"只是提醒"。** error 不修就发布不了，warning 不影响发布
 *    （比如 `VERSION_DRIFT` —— 试卷按锁定版本作答，本来就不需要处理）。
 *    两者混在一起列，用户会以为每条都得改，反而不敢发布。
 */
export function ValidateReport({
  validation,
  sections,
  className,
}: {
  validation: ExamValidateOut | null;
  /** 用来渲染"哪一段差多少"，比后端 message 更直观 */
  sections: ExamSectionOut[];
  className?: string;
}) {
  if (!validation) {
    return (
      <div className={cn("rounded-lg border bg-card p-4", className)}>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Info className="h-4 w-4" />
          还没有校验结果。点「校验卷面」跑一次 —— 发布前必须通过（无 error 级问题）。
        </p>
      </div>
    );
  }

  const gaps = sections.map((s) => ({ s, gap: sectionGap(s) })).filter((x) => x.gap.state !== "ok");
  const hasErrors = validation.errors.length > 0;

  return (
    <div className={cn("space-y-3", className)}>
      {/* ---------------- 结论 ---------------- */}
      <div
        className={cn(
          "flex flex-wrap items-center gap-3 rounded-lg border p-3",
          hasErrors ? "border-destructive/40 bg-destructive/5" : "border-emerald-300 bg-emerald-50",
        )}
      >
        {hasErrors ? (
          <XCircle className="h-5 w-5 shrink-0 text-destructive" />
        ) : (
          <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
        )}
        <div className="min-w-0 flex-1">
          <p className={cn("text-sm font-medium", hasErrors ? "text-destructive" : "text-emerald-800")}>
            {hasErrors
              ? `校验未通过：有 ${validation.errors.length} 个必须修的问题，修好才能发布`
              : "校验通过：可以发布"}
            {validation.warnings.length > 0 ? (
              <span className="ml-2 font-normal text-amber-700">
                另有 {validation.warnings.length} 条提醒（不影响发布）
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            卷面 {validation.question_count} 题 · 合计 {validation.total_score} 分
          </p>
        </div>
      </div>

      {/* ---------------- 分段缺口：整段标红 ---------------- */}
      {gaps.length > 0 ? (
        <div className="overflow-hidden rounded-lg border border-destructive/40">
          <div className="border-b border-destructive/30 bg-destructive/10 px-3 py-2">
            <p className="text-xs font-medium text-destructive">
              分段的实际题数与计划对不上（{gaps.length} 段）
            </p>
          </div>
          <ul className="divide-y">
            {gaps.map(({ s, gap }) => (
              <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                {/* 整段标红：不只是数字差异 */}
                <span className="text-sm font-medium text-destructive">{s.name}</span>
                <span className="text-xs text-destructive">{gap.text}</span>
                <span className="yj-json ml-auto text-[11px] text-muted-foreground">
                  计划 {gap.planned} · 实际 {gap.actual}
                </span>
              </li>
            ))}
          </ul>
          <p className="border-t bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            两条出路：① 补题 / 移题让实际回到计划值；② 点「编辑卷面结构」把计划题数改成实际值
            —— 那等于明确认可新的卷面构成。
          </p>
        </div>
      ) : null}

      {/* ---------------- error 明细 ---------------- */}
      {validation.errors.length > 0 ? (
        <IssueGroup
          tone="error"
          title="必须修的问题"
          issues={validation.errors.map((e) => ({ ...e, guidance: validateGuidance(e.code) }))}
        />
      ) : null}

      {/* ---------------- warning 明细 ---------------- */}
      {validation.warnings.length > 0 ? (
        <IssueGroup
          tone="warning"
          title="提醒（不影响发布）"
          issues={validation.warnings.map((e) => ({ ...e, guidance: validateGuidance(e.code) }))}
        />
      ) : null}
    </div>
  );
}

function IssueGroup({
  tone,
  title,
  issues,
}: {
  tone: "error" | "warning";
  title: string;
  issues: { level: string; code: string; message: string; guidance: { title: string; action: string } }[];
}) {
  const isError = tone === "error";
  return (
    <div className={cn("overflow-hidden rounded-lg border", isError ? "border-destructive/30" : "border-amber-300")}>
      <div className={cn("px-3 py-2", isError ? "bg-destructive/10" : "bg-amber-50")}>
        <p className={cn("text-xs font-medium", isError ? "text-destructive" : "text-amber-800")}>
          {title}
        </p>
      </div>
      <ul className="divide-y">
        {issues.map((it, i) => (
          <li key={`${it.code}-${i}`} className="space-y-1 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              {isError ? (
                <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
              ) : (
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" />
              )}
              <span className="text-sm font-medium">{it.guidance.title}</span>
              <Badge variant="outline" className="yj-json text-[10px]">
                {it.code}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">{it.message}</p>
            {/* 每条都有"下一步" —— 这是这张报告存在的意义 */}
            <p className="flex items-start gap-1.5 text-xs">
              <span className="shrink-0 font-medium text-foreground">下一步：</span>
              <span className="text-muted-foreground">{it.guidance.action}</span>
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
