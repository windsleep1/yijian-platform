"use client";

import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { qTypeLabel } from "@/lib/question";
import type { PaperRulePreviewOut } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 组卷规则的**试算结果**面板（dry-run，不写库）。
 *
 * 规则编辑页与「新建试卷」的自动组卷模式共用这一个组件 —— 两处要回答的是同一个问题：
 * **"按这套规则，现在能抽出多少题"**。写两份必然漂移。
 *
 * 三个刻意的展示决定：
 *
 * 1. **缺口说人话**：「还差 73 道案例题」，而不是丢一个 `-73` 让人自己做减法。
 * 2. **解释"为什么抽不到"**：把放宽阶梯每档的候选数摊开
 *    （"限定题型+难度时 3 道 → 只限题型 12 道"）。
 *    只说"不够"，用户不知道该补题库还是该松条件，只能瞎试。
 * 3. **明细可折叠**：抽题结果默认收起 —— 40 道题的清单不该把整页撑长，
 *    而"能抽到多少"的结论才是用户第一眼要看的。
 */
export function RulePreviewPanel({
  preview,
  pending,
  error,
  onRefresh,
  className,
}: {
  preview: PaperRulePreviewOut | null;
  pending: boolean;
  error?: unknown;
  onRefresh?: () => void;
  className?: string;
}) {
  const [showSample, setShowSample] = useState(false);

  if (pending) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 rounded-lg border bg-card p-4 text-sm text-muted-foreground",
          className,
        )}
      >
        <Loader2 className="h-4 w-4 animate-spin" />
        正在试算（不影响任何数据）…
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={cn("rounded-lg border border-destructive/40 bg-destructive/5 p-4", className)}
      >
        <p className="text-sm font-medium text-destructive">试算失败</p>
        <p className="mt-1 text-xs text-muted-foreground">
          规则本身可能有填写问题（比如题数不是正数）。改好后会自动重试。
        </p>
        {onRefresh ? (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRefresh}>
            <RefreshCw className="h-3.5 w-3.5" />
            重新试算
          </Button>
        ) : null}
      </div>
    );
  }

  if (!preview) {
    return (
      <div className={cn("rounded-lg border bg-card p-4 text-sm text-muted-foreground", className)}>
        <span className="flex items-center gap-2">
          <Info className="h-4 w-4" />
          填好规则后这里会自动试算 —— 告诉你**现在能抽出多少题**，以及题库够不够。
          <br />
          （试算是纯读的，不会创建试卷、也不会占用题目。）
        </span>
      </div>
    );
  }

  const gap = preview.total_missing > 0;

  return (
    <div className={cn("space-y-3", className)}>
      {/* ---------------- 结论 ---------------- */}
      <div
        className={cn(
          "rounded-lg border p-3",
          gap ? "border-amber-300 bg-amber-50" : "border-emerald-300 bg-emerald-50",
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          {gap ? (
            <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
          ) : (
            <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" />
          )}
          <span className={cn("text-sm font-medium", gap ? "text-amber-800" : "text-emerald-800")}>
            {gap
              ? `题库不足：需要 ${preview.total_need} 道，只能抽到 ${preview.total_got} 道，还差 ${preview.total_missing} 道`
              : `题库充足：${preview.total_need} 道都能抽满`}
          </span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            耗时 {preview.duration_ms}ms
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          按实际抽到的题算是 <strong>{preview.total_score}</strong> 分； 抽满的话是{" "}
          <strong>{preview.planned_score}</strong> 分。
        </p>
        {/* 后端已经把人话总结好了，直接展示，不自己再拼一遍 */}
        <p className={cn("mt-1.5 text-xs", gap ? "text-amber-800" : "text-emerald-800")}>
          {preview.message}
        </p>
      </div>

      {/* ---------------- 逐条规则 ---------------- */}
      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-3 py-1.5 text-left font-medium">规则</th>
              <th className="w-[92px] px-3 py-1.5 text-right font-medium">计划</th>
              <th className="w-[92px] px-3 py-1.5 text-right font-medium">能抽到</th>
              <th className="w-[92px] px-3 py-1.5 text-right font-medium">缺口</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {preview.items.map((it) => (
              <tr key={it.rule_index} className={cn(it.missing > 0 && "bg-amber-50/60")}>
                <td className="px-3 py-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium">{qTypeLabel(it.question_type)}</span>
                    <span className="yj-json text-[10px] text-muted-foreground">
                      {it.score} 分/题
                    </span>
                    {it.missing > 0 ? (
                      <Badge variant="warning" className="text-[10px]">
                        还差 {it.missing} 道{qTypeLabel(it.question_type)}
                      </Badge>
                    ) : null}
                  </div>
                  {/* 为什么抽不到：把放宽阶梯摊开 */}
                  {it.missing > 0 && Object.keys(it.stage_counts).length ? (
                    <div className="mt-0.5 text-[11px] text-amber-800">
                      逐级放宽后各档候选数：
                      {Object.entries(it.stage_counts)
                        .map(([stage, n]) => `${stage}=${n}`)
                        .join(" · ")}
                    </div>
                  ) : null}
                </td>
                <td className="yj-json px-3 py-1.5 text-right">{it.need}</td>
                <td className="yj-json px-3 py-1.5 text-right font-medium">{it.got}</td>
                <td
                  className={cn(
                    "yj-json px-3 py-1.5 text-right",
                    it.missing > 0 ? "font-medium text-amber-700" : "text-muted-foreground",
                  )}
                >
                  {it.missing > 0 ? `-${it.missing}` : "0"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ---------------- 抽题明细（可折叠） ---------------- */}
      {preview.sample.length ? (
        <div className="overflow-hidden rounded-lg border">
          <button
            type="button"
            className="flex w-full items-center justify-between bg-muted/40 px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setShowSample((v) => !v)}
          >
            <span>
              抽题明细（{showSample ? "点击收起" : `共 ${preview.sample.length} 道，点击展开`}）
            </span>
            <span>{showSample ? "−" : "+"}</span>
          </button>
          {showSample ? (
            <ul className="divide-y">
              {preview.sample.map((s) => (
                <li key={s.question_id} className="px-3 py-2">
                  <p className="line-clamp-2 text-xs leading-relaxed">
                    {s.stem_preview || "（无题干预览）"}
                  </p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                    <Badge variant="outline" className="text-[10px]">
                      {qTypeLabel(s.question_type)}
                    </Badge>
                    <span>{s.difficulty != null ? `难度 ${s.difficulty}` : "难度未知"}</span>
                    <span className="yj-json">{s.score} 分</span>
                    <span>来自：{s.rule_label}</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
