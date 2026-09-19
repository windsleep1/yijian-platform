"use client";

import { AlertTriangle, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RULE_STRATEGY_LABELS, ruleStrategyLabel } from "@/lib/exam";
import { qTypeLabel } from "@/lib/question";
import type { PaperRuleOut, RuleItem, RuleStrategy, QType } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 组卷可选题型。`case_sub` **不在**这里 —— 它必须跟随父题一起进卷，单独抽会被后端拒。 */
export const COMPOSABLE_QTYPES: QType[] = ["single", "multiple", "judge", "case", "fill", "essay"];

/** 编辑器用的草稿（数字存字符串，便于"清空"和"填一半"）。 */
export type RuleDraft = {
  type: QType;
  count: string;
  score: string;
  /** 难度闭区间；两个都空 = 不限 */
  difficultyMin: string;
  difficultyMax: string;
  /** 空 = 不限 */
  year: string;
  preferUnused: boolean;
};

export const ANY = "__any__";

export function emptyRuleDraft(type: QType = "single"): RuleDraft {
  return { type, count: "10", score: "1", difficultyMin: "", difficultyMax: "", year: "", preferUnused: true };
}

export function ruleToDraft(r: RuleItem): RuleDraft {
  return {
    type: r.type,
    count: String(r.count),
    score: String(r.score),
    difficultyMin: r.difficulty?.[0] != null ? String(r.difficulty[0]) : "",
    difficultyMax: r.difficulty?.[1] != null ? String(r.difficulty[1]) : "",
    year: r.year != null ? String(r.year) : "",
    preferUnused: r.prefer_unused,
  };
}

export function draftsToRules(drafts: RuleDraft[]): RuleItem[] {
  return drafts.map((d) => {
    const min = d.difficultyMin ? Number(d.difficultyMin) : null;
    const max = d.difficultyMax ? Number(d.difficultyMax) : null;
    return {
      type: d.type,
      count: Number(d.count) || 0,
      score: Number(d.score) || 0,
      difficulty: min != null && max != null ? [min, max] : null,
      kp_ids: [],
      chapter_ids: [],
      year: d.year ? Number(d.year) : null,
      prefer_unused: d.preferUnused,
    };
  });
}

export function plannedFromDrafts(drafts: RuleDraft[]): { count: number; score: number } {
  let count = 0;
  let score = 0;
  for (const d of drafts) {
    const c = Number(d.count) || 0;
    count += c;
    score += c * (Number(d.score) || 0);
  }
  return { count, score: Math.round(score * 100) / 100 };
}

export type DraftIssue = { index: number; field: string; message: string };

/**
 * 逐字段校验。**返回结构化的问题列表**（而不是一个 boolean）——
 * 界面要能就地把红字标在出错的那一行的那个输入框下面，
 * 而不是在表单顶部甩一句"表单填写有误"让人自己找。
 */
export function validateDrafts(drafts: RuleDraft[]): DraftIssue[] {
  const issues: DraftIssue[] = [];
  if (drafts.length === 0) {
    return [{ index: -1, field: "rules", message: "至少要有一条抽题规则，否则这份卷子没有任何题目来源。" }];
  }
  drafts.forEach((d, i) => {
    const count = Number(d.count);
    if (!d.count.trim() || !Number.isFinite(count) || count < 1) {
      issues.push({ index: i, field: "count", message: "题数必须是 ≥1 的整数" });
    } else if (count > 200) {
      issues.push({ index: i, field: "count", message: "单条规则最多 200 道" });
    } else if (!Number.isInteger(count)) {
      issues.push({ index: i, field: "count", message: "题数必须是整数" });
    }

    const score = Number(d.score);
    if (!d.score.trim() || !Number.isFinite(score) || score <= 0) {
      issues.push({ index: i, field: "score", message: "每题分值必须 >0" });
    } else if (score > 100) {
      issues.push({ index: i, field: "score", message: "每题分值最大 100" });
    }

    // 难度：要么都不填，要么都填且 min<=max
    const hasMin = !!d.difficultyMin;
    const hasMax = !!d.difficultyMax;
    if (hasMin !== hasMax) {
      issues.push({ index: i, field: "difficulty", message: "难度区间要两端都填，或都留空（不限）" });
    } else if (hasMin && hasMax && Number(d.difficultyMin) > Number(d.difficultyMax)) {
      issues.push({ index: i, field: "difficulty", message: "难度下限不能高于上限" });
    }

    if (d.year) {
      const y = Number(d.year);
      if (!Number.isFinite(y) || y < 2000 || y > 2100) {
        issues.push({ index: i, field: "year", message: "年份需在 2000~2100" });
      }
    }
  });
  return issues;
}

/**
 * 跨规则的**配比提示**（不是错误，所以单独一个函数）。
 *
 * 典型场景：两条规则题型与筛选条件完全相同 —— 后端允许（第二条从剩下的题里抽），
 * 但第二条能抽到的往往比第一条少。用户看到"配比 60+60"以为能有 120 道，
 * 实际可能只抽到 90。**提前说出来，比事后解释缺口好。**
 */
export function pairingWarnings(drafts: RuleDraft[]): string[] {
  const seen = new Map<string, number>();
  const out: string[] = [];
  drafts.forEach((d, i) => {
    const key = [d.type, d.difficultyMin, d.difficultyMax, d.year].join("|");
    const prev = seen.get(key);
    if (prev != null) {
      out.push(
        `第 ${prev + 1} 条与第 ${i + 1} 条规则的题型和筛选条件完全相同：` +
          `它们会先后从同一个题池里抽题，第 ${i + 1} 条实际能抽到的通常更少（同一道题不会进卷两次）。`,
      );
    } else {
      seen.set(key, i);
    }
  });
  return out;
}

// ---------------------------------------------------------------- 组件

export function RuleEditor({
  drafts,
  onChange,
  disabled,
}: {
  drafts: RuleDraft[];
  onChange: (next: RuleDraft[]) => void;
  disabled?: boolean;
}) {
  const issues = validateDrafts(drafts);
  const errorsOf = (i: number, field: string) =>
    issues.filter((x) => x.index === i && x.field === field).map((x) => x.message);
  const warnings = pairingWarnings(drafts);

  const patch = (i: number, next: Partial<RuleDraft>) =>
    onChange(drafts.map((d, idx) => (idx === i ? { ...d, ...next } : d)));

  const planned = plannedFromDrafts(drafts);

  return (
    <div className="space-y-2">
      {drafts.map((d, i) => {
        const errCount = errorsOf(i, "count");
        const errScore = errorsOf(i, "score");
        const errDiff = errorsOf(i, "difficulty");
        const errYear = errorsOf(i, "year");
        const bad = errCount.length || errScore.length || errDiff.length || errYear.length;
        return (
          <div
            key={i}
            className={cn("rounded-md border p-2", bad ? "border-destructive/50 bg-destructive/5" : "")}
          >
            <div className="grid grid-cols-12 items-end gap-2">
              <div className="col-span-3 space-y-1">
                <Label className="text-[11px] text-muted-foreground">题型</Label>
                <Select
                  value={d.type}
                  disabled={disabled}
                  onValueChange={(v) => patch(i, { type: v as QType })}
                >
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {COMPOSABLE_QTYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {qTypeLabel(t)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="col-span-2 space-y-1">
                <Label className="text-[11px] text-muted-foreground">题数</Label>
                <Input
                  className="h-9"
                  value={d.count}
                  disabled={disabled}
                  inputMode="numeric"
                  onChange={(e) => patch(i, { count: e.target.value.replace(/[^\d]/g, "") })}
                />
              </div>

              <div className="col-span-2 space-y-1">
                <Label className="text-[11px] text-muted-foreground">每题分</Label>
                <Input
                  className="h-9"
                  value={d.score}
                  disabled={disabled}
                  inputMode="decimal"
                  onChange={(e) => patch(i, { score: e.target.value.replace(/[^\d.]/g, "") })}
                />
              </div>

              <div className="col-span-2 space-y-1">
                <Label className="text-[11px] text-muted-foreground">难度下限</Label>
                <Select
                  value={d.difficultyMin || ANY}
                  disabled={disabled}
                  onValueChange={(v) => patch(i, { difficultyMin: v === ANY ? "" : v })}
                >
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="不限" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>不限</SelectItem>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <SelectItem key={n} value={String(n)}>
                        {n}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="col-span-2 space-y-1">
                <Label className="text-[11px] text-muted-foreground">难度上限</Label>
                <Select
                  value={d.difficultyMax || ANY}
                  disabled={disabled}
                  onValueChange={(v) => patch(i, { difficultyMax: v === ANY ? "" : v })}
                >
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="不限" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>不限</SelectItem>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <SelectItem key={n} value={String(n)}>
                        {n}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="col-span-1 flex justify-end">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-9 px-2 text-muted-foreground hover:text-destructive"
                  disabled={disabled || drafts.length <= 1}
                  onClick={() => onChange(drafts.filter((_, idx) => idx !== i))}
                  title={drafts.length <= 1 ? "至少要保留一条规则" : "删除该规则"}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
              <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Checkbox
                  checked={d.preferUnused}
                  disabled={disabled}
                  onCheckedChange={(v) => patch(i, { preferUnused: v === true })}
                />
                优先抽没被用过的题
              </label>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-muted-foreground">限定年份</span>
                <Input
                  className="h-7 w-20 text-xs"
                  value={d.year}
                  disabled={disabled}
                  placeholder="不限"
                  inputMode="numeric"
                  onChange={(e) => patch(i, { year: e.target.value.replace(/[^\d]/g, "").slice(0, 4) })}
                />
              </div>
            </div>

            {bad ? (
              <div className="mt-1 space-y-0.5">
                {[...errCount, ...errScore, ...errDiff, ...errYear].map((m, k) => (
                  <p key={k} className="flex items-center gap-1 text-[11px] text-destructive">
                    <AlertTriangle className="h-3 w-3" />
                    {m}
                  </p>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onChange([...drafts, emptyRuleDraft()])}
        >
          <Plus className="h-3.5 w-3.5" />
          添加一条规则
        </Button>
        <span className="text-xs text-muted-foreground">
          计划 <span className="font-medium text-foreground">{planned.count}</span> 道 · 满分{" "}
          <span className="font-medium text-foreground">{planned.score}</span> 分
        </span>
      </div>

      {warnings.length ? (
        <div className="space-y-1 rounded-md border border-amber-300 bg-amber-50 p-2">
          {warnings.map((w, i) => (
            <p key={i} className="flex items-start gap-1.5 text-[11px] text-amber-800">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              {w}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** 策略下拉（新建 / 编辑规则用）。本批后端只实现了 `random` 的语义，其余是占位。 */
export function StrategySelect({
  value,
  onChange,
  disabled,
}: {
  value: RuleStrategy;
  onChange: (v: RuleStrategy) => void;
  disabled?: boolean;
}) {
  return (
    <Select value={value} disabled={disabled} onValueChange={(v) => onChange(v as RuleStrategy)}>
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {(Object.keys(RULE_STRATEGY_LABELS) as RuleStrategy[]).map((s) => (
          <SelectItem key={s} value={s}>
            {ruleStrategyLabel(s)}
            {s !== "random" ? "（暂未实现，按随机处理）" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** 从一条已保存的规则取它的约束数组（试算 / 新建卷时复用）。 */
export function rulesOfRule(rule: PaperRuleOut): RuleItem[] {
  return rule.rules;
}
