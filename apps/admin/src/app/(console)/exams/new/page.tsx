"use client";

import { AlertTriangle, ArrowLeft, Check, Loader2, Sparkles, Wand2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import {
  COMPOSABLE_QTYPES,
  RuleEditor,
  draftsToRules,
  emptyRuleDraft,
  validateDrafts,
  type RuleDraft,
} from "@/components/RuleEditor";
import { RulePreviewPanel } from "@/components/RulePreviewPanel";
import { Badge } from "@/components/ui/badge";
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
import {
  useComposeExam,
  useCreateExam,
  usePaperRules,
  usePreviewPaperRule,
} from "@/hooks/useExams";
import { useChapterTree } from "@/hooks/useQuestions";
import { useAuth } from "@/lib/auth-context";
import { EXAM_TYPE_LABELS, qtypeLabel } from "@/lib/exam";
import { P } from "@/lib/permission";
import type { ExamSectionIn, ExamType, QType, RuleItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const PREVIEW_DEBOUNCE_MS = 600;

type Mode = "manual" | "rule";

/** 手动模式的分段草稿。 */
type SectionDraft = {
  name: string;
  question_type: QType;
  question_count: string;
  score_per: string;
};

function emptySection(qtype: QType = "single"): SectionDraft {
  return { name: qtypeLabel(qtype), question_type: qtype, question_count: "10", score_per: "1" };
}

/**
 * 新建试卷。两种模式，**界限要一眼看清**（用户明确要求"模式切换清晰"）：
 *
 * | 模式 | 做什么 | 建完去哪 |
 * |---|---|---|
 * | **手动选题** | 自己定卷面结构，建完逐题挑 | 跳到详情页并**自动打开「加题」面板** |
 * | **规则自动组卷** | 按一套规则自动抽题，**建之前就能试算** | 跳到详情页看组卷结果与缺口 |
 *
 * ## 为什么"规则自动组卷"必须先试算
 *
 * 没有它，用户要点"创建"才知道抽不满 —— 于是库里多出一张残缺的卷和一个残缺的规则引用，
 * 还得回头删。试算是纯读的，把"能不能成"提前到了点击之前。
 */
export default function NewExamPage() {
  const router = useRouter();
  const { hasPermission, isLoading: authLoading } = useAuth();

  const tree = useChapterTree();
  const subjects = useMemo(() => (tree.data?.items ?? []).map((g) => g.subject), [tree.data]);

  const create = useCreateExam();
  const compose = useComposeExam();
  const preview = usePreviewPaperRule();

  const [mode, setMode] = useState<Mode>("manual");

  // ---- 基本信息 ----
  const [subjectId, setSubjectId] = useState("");
  const [title, setTitle] = useState("");
  const [type, setType] = useState<ExamType>("mock");
  const [examYear, setExamYear] = useState("");
  const [paperNo, setPaperNo] = useState("");
  const [duration, setDuration] = useState("180");
  const [passScore, setPassScore] = useState("0");
  const [isFree, setIsFree] = useState(false);
  const [introHtml, setIntroHtml] = useState("");

  // ---- 手动模式：分段 ----
  const [sections, setSections] = useState<SectionDraft[]>([
    { name: "一、单项选择题", question_type: "single", question_count: "20", score_per: "1" },
    { name: "二、多项选择题", question_type: "multiple", question_count: "10", score_per: "2" },
    { name: "三、判断题", question_type: "judge", question_count: "10", score_per: "1" },
  ]);

  // ---- 规则模式：来源（已保存规则 / 临时规则）----
  const [ruleSource, setRuleSource] = useState<"saved" | "inline">("saved");
  const [savedRuleId, setSavedRuleId] = useState("");
  const [drafts, setDrafts] = useState<RuleDraft[]>([
    { ...emptyRuleDraft("single"), count: "20" },
    { ...emptyRuleDraft("multiple"), count: "10", score: "2" },
    { ...emptyRuleDraft("judge"), count: "10" },
  ]);

  const rulesQuery = usePaperRules({
    page: 1,
    page_size: 100,
    status: "on",
    subject_id: subjectId || undefined,
  });
  const savedRules = rulesQuery.data?.items ?? [];
  const savedRule = savedRules.find((r) => r.id === savedRuleId) ?? null;

  /** 规则模式下真正要提交的约束数组 */
  const effectiveRules: RuleItem[] = useMemo(() => {
    if (ruleSource === "saved") return savedRule?.rules ?? [];
    return draftsToRules(drafts);
  }, [ruleSource, savedRule, drafts]);

  const inlineIssues = ruleSource === "inline" ? validateDrafts(drafts) : [];

  // 选了"已保存规则"但科目还没定 → 自动带上规则的科目，省一步
  useEffect(() => {
    if (ruleSource === "saved" && savedRule && !subjectId) setSubjectId(savedRule.subject_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedRuleId]);

  // ---- 实时试算（防抖）：规则模式的核心 ----
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const canPreview =
    mode === "rule" && !!subjectId && effectiveRules.length > 0 && inlineIssues.length === 0;
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!canPreview) {
      preview.reset();
      return;
    }
    timer.current = setTimeout(() => {
      preview.mutate({
        subject_id: subjectId,
        rules: effectiveRules,
        seed: 20260101,
        sample_limit: 12,
      });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, subjectId, canPreview, JSON.stringify(effectiveRules)]);

  if (!authLoading && !hasPermission(P.examCreate)) {
    return <ForbiddenState need={P.examCreate} />;
  }

  const durationNum = Number(duration);
  const passNum = Number(passScore);
  const manualPlanned = sections.reduce(
    (acc, s) => {
      const c = Number(s.question_count) || 0;
      return { count: acc.count + c, score: acc.score + c * (Number(s.score_per) || 0) };
    },
    { count: 0, score: 0 },
  );
  const sectionInvalid = sections.some(
    (s) => !s.name.trim() || !(Number(s.question_count) > 0) || !(Number(s.score_per) > 0),
  );

  const baseBad =
    !title.trim() ||
    !subjectId ||
    !Number.isFinite(durationNum) ||
    durationNum < 1 ||
    durationNum > 600 ||
    !Number.isFinite(passNum) ||
    passNum < 0;
  const submitBad =
    baseBad ||
    (mode === "manual"
      ? sectionInvalid || sections.length === 0
      : effectiveRules.length === 0 || inlineIssues.length > 0);

  const busy = create.isPending || compose.isPending;

  const submit = async () => {
    const payloadSections: ExamSectionIn[] =
      mode === "manual"
        ? sections.map((s, i) => ({
            name: s.name.trim(),
            question_type: s.question_type,
            question_count: Number(s.question_count) || 0,
            score_per: Number(s.score_per) || 1,
            sort_no: i,
          }))
        : // 规则模式**先建空卷**，再让 compose 用 `apply_sections` 按规则建分段 ——
          // 这样分段里的"计划题数"天然等于规则要的题数（抽不满时缺口才说得清）
          [];

    let examId = "";
    try {
      const exam = await create.mutateAsync({
        subject_id: subjectId,
        title: title.trim(),
        type,
        exam_year: examYear ? Number(examYear) : null,
        paper_no: paperNo.trim() || null,
        duration_min: durationNum,
        pass_score: passNum,
        intro_html: introHtml.trim() || null,
        is_free: isFree,
        sections: payloadSections,
      });
      examId = exam.id;

      if (mode === "manual") {
        toast.success("试卷已创建", {
          description: "接下来从题库里挑题加入卷面。",
        });
        router.push(`/exams/${exam.id}#add`);
        return;
      }

      // ---- 规则模式：建完立刻组卷 ----
      const res = await compose.mutateAsync({
        examId: exam.id,
        payload: {
          rule_id: ruleSource === "saved" ? savedRule?.id ?? null : null,
          rules: ruleSource === "inline" ? effectiveRules : null,
          replace: true,
          apply_sections: true,
        },
      });

      if (res.shortfalls.length > 0) {
        toast.warning(`已组卷，但有 ${res.shortfalls.length} 条规则没抽满`, {
          description: res.message,
          duration: 10000,
        });
      } else {
        toast.success(`组卷完成：${res.question_count} 题 / ${res.total_score} 分`, {
          description: "可继续加题、校验后发布。",
        });
      }
      router.push(`/exams/${exam.id}`);
    } catch (err) {
      // 组卷失败时试卷已经建出来了 —— 不静默丢弃，把用户送到详情页自己处理
      if (examId) {
        toast.error("试卷已创建，但自动组卷失败", {
          description: "已跳到详情页，可以在那里重试组卷或改为手动加题。",
        });
        router.push(`/exams/${examId}`);
      }
      void err;
    }
  };

  return (
    <>
      <PageHeader
        title="新建试卷"
        description="先填基本信息，再选一种建卷方式：手动挑题，或按规则自动组卷（可先试算）。"
        actions={
          <Button variant="ghost" size="sm" onClick={() => router.push("/exams")}>
            <ArrowLeft className="h-3.5 w-3.5" />
            返回列表
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        {/* ================= 左：基本信息 + 模式 ================= */}
        <div className="space-y-4">
          <section className="rounded-lg border bg-card p-4">
            <h2 className="mb-3 text-sm font-medium">基本信息</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="new-title">试卷标题</Label>
                <Input
                  id="new-title"
                  value={title}
                  maxLength={200}
                  placeholder="如：2026 一级建造师《工程经济》模拟卷 A"
                  onChange={(e) => setTitle(e.target.value)}
                />
                {!title.trim() ? (
                  <p className="text-[11px] text-destructive">标题不能为空。</p>
                ) : null}
              </div>

              <div className="space-y-1.5">
                <Label>科目</Label>
                <Select
                  value={subjectId || "__none__"}
                  onValueChange={(v) => setSubjectId(v === "__none__" ? "" : v)}
                >
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
                  <p className="text-[11px] text-muted-foreground">题目只在所选科目内抽取。</p>
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
                <Label htmlFor="new-year">考试年份</Label>
                <Input
                  id="new-year"
                  value={examYear}
                  inputMode="numeric"
                  placeholder="留空 = 不限"
                  onChange={(e) => setExamYear(e.target.value.replace(/[^\d]/g, "").slice(0, 4))}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="new-paper-no">卷号</Label>
                <Input
                  id="new-paper-no"
                  value={paperNo}
                  maxLength={32}
                  placeholder="如 A 卷"
                  onChange={(e) => setPaperNo(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="new-duration">时长（分钟）</Label>
                <Input
                  id="new-duration"
                  value={duration}
                  inputMode="numeric"
                  onChange={(e) => setDuration(e.target.value.replace(/[^\d]/g, ""))}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="new-pass">及格分</Label>
                <Input
                  id="new-pass"
                  value={passScore}
                  inputMode="decimal"
                  onChange={(e) => setPassScore(e.target.value.replace(/[^\d.]/g, ""))}
                />
              </div>

              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="new-intro">简介（可选）</Label>
                <Input
                  id="new-intro"
                  value={introHtml}
                  maxLength={2000}
                  onChange={(e) => setIntroHtml(e.target.value)}
                />
              </div>

              <label className="flex w-fit cursor-pointer items-center gap-2 text-sm sm:col-span-2">
                <Checkbox checked={isFree} onCheckedChange={(v) => setIsFree(v === true)} />
                <span>免费开放</span>
              </label>
            </div>
          </section>

          {/* ---------------- 模式切换（要一眼看清） ---------------- */}
          <section className="rounded-lg border bg-card p-4">
            <h2 className="mb-3 text-sm font-medium">建卷方式</h2>
            <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="建卷方式">
              {(
                [
                  {
                    key: "manual" as Mode,
                    icon: Check,
                    label: "手动选题",
                    hint: "自己定卷面结构，建完逐题挑",
                  },
                  {
                    key: "rule" as Mode,
                    icon: Wand2,
                    label: "规则自动组卷",
                    hint: "按规则自动抽题，建之前可试算",
                  },
                ] as const
              ).map((m) => {
                const active = mode === m.key;
                const Icon = m.icon;
                return (
                  <button
                    key={m.key}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setMode(m.key)}
                    className={cn(
                      "rounded-lg border p-3 text-left transition-colors",
                      active
                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                        : "hover:border-primary/40 hover:bg-muted/40",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon
                        className={cn("h-4 w-4", active ? "text-primary" : "text-muted-foreground")}
                      />
                      <span className="text-sm font-medium">{m.label}</span>
                      {active ? (
                        <Badge variant="default" className="ml-auto text-[10px]">
                          当前
                        </Badge>
                      ) : null}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{m.hint}</p>
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">
              {mode === "manual"
                ? "建完会跳到详情页并自动打开「加题」面板 —— 从题库里逐题挑选。"
                : "先建一张空卷，再按规则自动抽题。**抽不满会如实报缺口**，不会用别的题顶替。"}
            </p>
          </section>

          {/* ---------------- 手动模式：卷面结构 ---------------- */}
          {mode === "manual" ? (
            <section className="rounded-lg border bg-card p-4">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium">卷面结构</h2>
                <span className="text-xs text-muted-foreground">
                  计划 {manualPlanned.count} 道 · 满分 {manualPlanned.score} 分
                </span>
              </div>

              <div className="space-y-2">
                {sections.map((s, i) => (
                  <div key={i} className="grid grid-cols-12 items-end gap-2 rounded-md border p-2">
                    <div className="col-span-5 space-y-1">
                      <Label className="text-[11px] text-muted-foreground">分段名称</Label>
                      <Input
                        className="h-9"
                        value={s.name}
                        maxLength={96}
                        onChange={(e) =>
                          setSections((p) =>
                            p.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)),
                          )
                        }
                      />
                    </div>
                    <div className="col-span-3 space-y-1">
                      <Label className="text-[11px] text-muted-foreground">题型</Label>
                      <Select
                        value={s.question_type}
                        onValueChange={(v) =>
                          setSections((p) =>
                            p.map((x, k) => (k === i ? { ...x, question_type: v as QType } : x)),
                          )
                        }
                      >
                        <SelectTrigger className="h-9">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {COMPOSABLE_QTYPES.map((t) => (
                            <SelectItem key={t} value={t}>
                              {qtypeLabel(t)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="col-span-2 space-y-1">
                      <Label className="text-[11px] text-muted-foreground">计划题数</Label>
                      <Input
                        className="h-9"
                        value={s.question_count}
                        inputMode="numeric"
                        onChange={(e) =>
                          setSections((p) =>
                            p.map((x, k) =>
                              k === i
                                ? { ...x, question_count: e.target.value.replace(/[^\d]/g, "") }
                                : x,
                            ),
                          )
                        }
                      />
                    </div>
                    <div className="col-span-2 space-y-1">
                      <Label className="text-[11px] text-muted-foreground">每题分</Label>
                      <Input
                        className="h-9"
                        value={s.score_per}
                        inputMode="decimal"
                        onChange={(e) =>
                          setSections((p) =>
                            p.map((x, k) =>
                              k === i
                                ? { ...x, score_per: e.target.value.replace(/[^\d.]/g, "") }
                                : x,
                            ),
                          )
                        }
                      />
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-2 flex items-center justify-between">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSections((p) => [...p, emptySection()])}
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  添加分段
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={sections.length <= 1}
                  onClick={() => setSections((p) => p.slice(0, -1))}
                >
                  删除最后一段
                </Button>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">
                分段声明"哪个题型、多少道、每题多少分"，是卷面的骨架。
                <strong>发布前校验会拿实际题数与这里的计划值逐段比对</strong>，
                所以这里填的数字要和你打算挑的题量一致。
              </p>
            </section>
          ) : null}

          {/* ---------------- 规则模式：规则来源 ---------------- */}
          {mode === "rule" ? (
            <section className="rounded-lg border bg-card p-4">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-medium">抽题规则</h2>
                <Button variant="link" size="sm" className="h-auto p-0" asChild>
                  <a href="/paper-rules">去管理规则 →</a>
                </Button>
              </div>

              <div className="mb-3 flex gap-2">
                {(
                  [
                    { key: "saved" as const, label: "用已保存的规则" },
                    { key: "inline" as const, label: "临时配一套" },
                  ] as const
                ).map((o) => (
                  <Button
                    key={o.key}
                    type="button"
                    size="sm"
                    variant={ruleSource === o.key ? "default" : "outline"}
                    onClick={() => setRuleSource(o.key)}
                  >
                    {o.label}
                  </Button>
                ))}
              </div>

              {ruleSource === "saved" ? (
                <div className="space-y-2">
                  <Select
                    value={savedRuleId || "__none__"}
                    onValueChange={(v) => setSavedRuleId(v === "__none__" ? "" : v)}
                  >
                    <SelectTrigger>
                      <SelectValue
                        placeholder={rulesQuery.isLoading ? "加载中…" : "选择一条组卷规则"}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {savedRules.length === 0 ? (
                        <SelectItem value="__none__" disabled>
                          该科目下没有启用的规则
                        </SelectItem>
                      ) : (
                        savedRules.map((r) => (
                          <SelectItem key={r.id} value={r.id}>
                            {r.name}（{r.planned_count} 题 / {r.planned_score} 分）
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                  {savedRule ? (
                    <div className="flex flex-wrap gap-1">
                      {savedRule.rules.map((x, i) => (
                        <Badge key={i} variant="outline" className="text-[10px]">
                          {qtypeLabel(x.type)} ×{x.count}（{x.score} 分/题）
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      没有合适的规则？切到「临时配一套」直接在这里配，或先去
                      <a href="/paper-rules" className="mx-1 text-primary hover:underline">
                        组卷规则
                      </a>
                      建一条可复用的。
                    </p>
                  )}
                </div>
              ) : (
                <RuleEditor drafts={drafts} onChange={setDrafts} disabled={busy} />
              )}
            </section>
          ) : null}
        </div>

        {/* ================= 右：试算 + 提交 ================= */}
        <div className="space-y-4">
          <section className="rounded-lg border bg-card p-4">
            <h2 className="mb-3 text-sm font-medium">
              {mode === "rule" ? "试算（实时）" : "创建预览"}
            </h2>

            {mode === "rule" ? (
              <RulePreviewPanel
                preview={canPreview ? preview.data ?? null : null}
                pending={canPreview && preview.isPending}
                error={canPreview ? preview.error : undefined}
                onRefresh={() =>
                  preview.mutate({ subject_id: subjectId, rules: effectiveRules, seed: 20260101 })
                }
              />
            ) : (
              <div className="space-y-2 rounded-lg border bg-muted/30 p-3 text-sm">
                <p>
                  将创建一份<strong>空卷</strong>，包含 {sections.length} 个分段、 计划{" "}
                  {manualPlanned.count} 道题、满分 {manualPlanned.score} 分。
                </p>
                <p className="text-xs text-muted-foreground">
                  创建后会跳到详情页并自动打开「加题」面板，从题库里逐题挑选。
                  挑完之后校验会提示"分段还差几道"，直到填满才能发布。
                </p>
                <ul className="mt-1 space-y-0.5">
                  {sections.map((s, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge variant="outline" className="text-[10px]">
                        {qtypeLabel(s.question_type)}
                      </Badge>
                      <span>{s.name || "（未命名）"}</span>
                      <span className="yj-json ml-auto text-muted-foreground">
                        {s.question_count || 0} 道 × {s.score_per || 0} 分
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          {mode === "rule" && preview.data && !preview.data.ok ? (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="space-y-1 text-xs text-amber-800">
                <p className="font-medium">题库不足时会怎样？</p>
                <p>
                  仍然会创建并组卷，但**只写入抽到的题**（
                  {preview.data.total_got} 道），缺口会写进卷面并在详情页持续提示 ——
                  系统不会用别的题顶替。你可以之后补题库再重新组卷，或手动加题补齐。
                </p>
              </div>
            </div>
          ) : null}

          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => router.push("/exams")} disabled={busy}>
              取消
            </Button>
            <Button disabled={submitBad || busy} onClick={() => void submit()}>
              {busy ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {mode === "rule" ? "创建并组卷中…" : "创建中…"}
                </>
              ) : mode === "rule" ? (
                <>
                  <Wand2 className="h-3.5 w-3.5" />
                  创建并自动组卷
                </>
              ) : (
                <>
                  <Check className="h-3.5 w-3.5" />
                  创建空卷并去挑题
                </>
              )}
            </Button>
            {submitBad ? (
              <span className="text-[11px] text-destructive">
                {baseBad
                  ? "请先补全基本信息（标题 / 科目 / 时长 / 及格分）"
                  : mode === "manual"
                    ? "分段有填写问题（名称 / 题数 / 分值）"
                    : inlineIssues.length
                      ? `规则有 ${inlineIssues.length} 处问题`
                      : "请先选择一条规则"}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
