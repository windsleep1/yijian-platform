"use client";

import { AlertTriangle, ArrowLeft, Check, Loader2, Search, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAddExamQuestions } from "@/hooks/useExams";
import { useChapterTree, useKnowledgePoints, useQuestions } from "@/hooks/useQuestions";
import { DIFFICULTY_LABELS, difficultyLabel, qTypeLabel } from "@/lib/question";
import type { ExamDetail, ExamAddQuestionsOut, QType } from "@/lib/types";
import { cn } from "@/lib/utils";

const ANY = "__any__";
const PAGE_SIZE = 20;

const PICKABLE_TYPES: QType[] = ["single", "multiple", "judge", "case", "fill", "essay"];

type Step = "pick" | "confirm" | "result";

/**
 * 手动加题面板。
 *
 * ## 这是"把数据加到另一处"的模式，和「给用户分配角色」同构
 *
 * 两者的骨架完全一样：**在一堆候选里挑 → 明确看到挑了什么 → 二次确认 → 提交 → 回执**。
 * 所以本面板刻意对齐 `AssignRolesDialog` 的几条做法：
 *
 * 1. **已经在目标里的候选项显示状态并禁选** —— 这里就是「已在卷面」。
 *    让用户勾上一个注定被后端跳过的题，是把失败操作摆到人面前。
 * 2. **筛选结果要能看出自己筛的是什么** —— 这里具体是**知识点名**
 *    （章节名已经在题库列表里，但知识点才是挑题时真正关心的那一层）。
 * 3. **选完先预览"将加入 N 道题"，确认后才提交** —— 用户点第二次时是在**核对**，
 *    不是盲签。
 *
 * ## 一处本面板独有的增强：**预测归档**
 *
 * 预览页会把每道选中的题**落到哪个分段**直接算出来（按题型匹配），
 * 并提前标出"这些题的题型没有对应分段，会被跳过"。
 * 后端本来就会逐条回 `skipped[].reason`，但让用户在提交**之前**看到，
 * 比提交之后解释好得多 —— 否则体验就是"我选了 5 道，怎么只进来 2 道"。
 */
export function AddQuestionsDialog({
  open,
  onOpenChange,
  exam,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  exam: ExamDetail;
  onAdded?: (result: ExamAddQuestionsOut) => void;
}) {
  // 关闭时整个卸载：候选列表的查询只在面板打开时才该发出去
  if (!open) return null;
  return <PickerBody onOpenChange={onOpenChange} exam={exam} onAdded={onAdded} />;
}

function PickerBody({
  exam,
  onOpenChange,
  onAdded,
}: {
  exam: ExamDetail;
  onOpenChange: (open: boolean) => void;
  onAdded?: (result: ExamAddQuestionsOut) => void;
}) {
  const [step, setStep] = useState<Step>("pick");

  // ---- 筛选 ----
  const [chapterId, setChapterId] = useState("");
  const [kpId, setKpId] = useState("");
  const [qtype, setQtype] = useState<QType | "">("");
  const [difficulty, setDifficulty] = useState("");
  const [keywordDraft, setKeywordDraft] = useState("");
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);

  const [picked, setPicked] = useState<string[]>([]);
  const [result, setResult] = useState<ExamAddQuestionsOut | null>(null);

  const add = useAddExamQuestions(exam.id);

  /** ⚠️ 全是字符串，绝不 Number()（雪花 ID 精度，见 lib/json-bigint.ts） */
  const existingIds = useMemo(
    () =>
      new Set(
        exam.sections.flatMap((s) => s.questions.map((q) => q.question_id)),
      ),
    [exam.sections],
  );

  /** 题型 → 该题型的分段（后端按同样规则自动挂段）。卷面没有该题型的分段时为 undefined。 */
  const sectionByType = useMemo(() => {
    const m = new Map<string, { id: string; name: string }>();
    for (const s of exam.sections) {
      if (!m.has(s.question_type)) m.set(s.question_type, { id: s.id, name: s.name });
    }
    return m;
  }, [exam.sections]);

  const tree = useChapterTree(exam.subject_id);
  const chapterOptions = useMemo(
    () => tree.data?.items[0]?.chapters ?? [],
    [tree.data],
  );

  const kps = useKnowledgePoints({
    subject_id: exam.subject_id,
    chapter_id: chapterId || undefined,
  });
  const kpOptions = useMemo(() => {
    const items = kps.data?.items ?? [];
    // 没选章节时后端会返回整个科目的知识点，量可能不小 —— 只展示前 200 个并提示
    return items;
  }, [kps.data]);

  const query = useQuestions({
    page,
    page_size: PAGE_SIZE,
    subject_id: exam.subject_id,
    chapter_id: chapterId || undefined,
    knowledge_point_id: kpId || undefined,
    type: (qtype || undefined) as QType | undefined,
    difficulty: difficulty ? Number(difficulty) : undefined,
    keyword: keyword || undefined,
    order_by: "updated_at",
    order: "desc",
  });

  // 改筛选就回第 1 页 + 清空已选（避免"跨筛选条件"的选中混在一起提交）
  useEffect(() => {
    setPage(1);
    setPicked([]);
  }, [chapterId, kpId, qtype, difficulty, keyword]);

  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const toggle = (id: string) => {
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  /**
   * 预测：已选的题会落到哪个分段 / 哪些必然被跳过。
   *
   * 与后端 `add_exam_questions` 的判据保持一致（题型匹配分段；题目必须已发布）。
   * 这是"复制了一份后端逻辑"，但**只用于预览**，真正的判定仍以后端返回为准 ——
   * 前端预测错了顶多是提示不准，不会写坏数据。
   */
  const preview = useMemo(() => {
    const willAdd: { id: string; type: QType; sectionName: string }[] = [];
    const willSkipNoSection: { id: string; type: QType }[] = [];
    for (const p of picked) {
      const row = rows.find((r) => r.id === p);
      const type = (row?.type ?? "") as QType;
      const sec = sectionByType.get(type);
      if (sec) willAdd.push({ id: p, type, sectionName: sec.name });
      else willSkipNoSection.push({ id: p, type });
    }
    return { willAdd, willSkipNoSection };
  }, [picked, rows, sectionByType]);

  const submit = async () => {
    const res = await add.mutateAsync({
      question_ids: picked,
      // 不传 section_id：让后端按题型自动挂段（与预览页展示的规则一致）
    });
    setResult(res);
    setStep("result");
    onAdded?.(res);
    if (res.added > 0) setPicked([]);
  };

  // ---------------------------------------------------------------- 渲染

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>
            {step === "pick"
              ? "手动加题"
              : step === "confirm"
                ? "确认加入卷面"
                : "加题结果"}
          </DialogTitle>
          <DialogDescription>
            {step === "pick" ? (
              <>
                从科目「{exam.subject_name ?? exam.subject_id}」的题库里挑题加入本卷。
                题目会**按题型自动挂到同题型的分段**；已在卷面里的题会标出来且不可选。
              </>
            ) : step === "confirm" ? (
              <>下面是要加入的题与它们的落点。确认无误后提交。</>
            ) : (
              <>提交已完成。卷面题数已重算。</>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* ======================= 步骤 1：挑选 ======================= */}
        {step === "pick" ? (
          <div className="space-y-3">
            {/* ---- 筛选 ---- */}
            <div className="flex flex-wrap items-center gap-2">
              <Select
                value={chapterId || ANY}
                onValueChange={(v) => {
                  setChapterId(v === ANY ? "" : v);
                  setKpId("");
                }}
              >
                <SelectTrigger className="w-[190px]">
                  <SelectValue placeholder={tree.isLoading ? "加载中…" : "全部章节"} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>全部章节</SelectItem>
                  {chapterOptions.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}（{c.question_count}）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={kpId || ANY}
                disabled={!chapterId}
                onValueChange={(v) => setKpId(v === ANY ? "" : v)}
              >
                <SelectTrigger className="w-[230px]">
                  <SelectValue placeholder={chapterId ? "全部知识点" : "先选章节"} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>全部知识点</SelectItem>
                  {kpOptions.map((k) => (
                    <SelectItem key={k.id} value={k.id}>
                      {k.name}（{k.question_count}）
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={qtype || ANY} onValueChange={(v) => setQtype(v === ANY ? "" : (v as QType))}>
                <SelectTrigger className="w-[130px]">
                  <SelectValue placeholder="全部题型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>全部题型</SelectItem>
                  {PICKABLE_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {qTypeLabel(t)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select
                value={difficulty || ANY}
                onValueChange={(v) => setDifficulty(v === ANY ? "" : v)}
              >
                <SelectTrigger className="w-[120px]">
                  <SelectValue placeholder="全部难度" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>全部难度</SelectItem>
                  {[1, 2, 3, 4, 5].map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      {DIFFICULTY_LABELS[n]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <form
                className="flex items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  setKeyword(keywordDraft.trim());
                }}
              >
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={keywordDraft}
                    onChange={(e) => setKeywordDraft(e.target.value)}
                    placeholder="题干 / 关键词"
                    className="w-56 pl-9"
                  />
                </div>
                <Button type="submit" variant="secondary" size="sm">
                  搜索
                </Button>
              </form>
            </div>

            {/* ---- 候选列表 ---- */}
            <div className="max-h-[42vh] overflow-y-auto rounded-md border">
              {query.isLoading ? (
                <div className="flex items-center gap-2 px-3 py-8 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载题目…
                </div>
              ) : query.error ? (
                <p className="px-3 py-8 text-sm text-destructive">
                  题目加载失败。请关闭面板后重试。
                </p>
              ) : rows.length === 0 ? (
                <p className="px-3 py-8 text-center text-sm text-muted-foreground">
                  这个筛选条件下没有题目。换个章节或放宽难度试试。
                </p>
              ) : (
                <ul className="divide-y">
                  {rows.map((q) => {
                    const already = existingIds.has(q.id);
                    const notPublished = q.status !== "published";
                    const disabled = already || notPublished;
                    const checked = picked.includes(q.id);
                    const sec = sectionByType.get(q.type);
                    return (
                      <li
                        key={q.id}
                        className={cn(
                          "flex items-start gap-3 px-3 py-2.5 transition-colors",
                          checked && "bg-primary/5",
                          disabled && "opacity-60",
                        )}
                      >
                        <Checkbox
                          className="mt-1"
                          checked={checked}
                          disabled={disabled}
                          onCheckedChange={() => toggle(q.id)}
                          aria-label={`选择题目 ${q.id}`}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="line-clamp-2 text-sm leading-relaxed">{q.stem || "（空题干）"}</p>
                          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
                            <Badge variant="outline" className="text-[10px]">
                              {qTypeLabel(q.type)}
                            </Badge>
                            <span className="text-muted-foreground">
                              {difficultyLabel(q.difficulty)}
                            </span>
                            <span className="text-muted-foreground">
                              v{q.version}
                            </span>
                            {/* ③ 筛选结果要能看出挂在哪 —— 章节 + **知识点名** */}
                            <span className="truncate text-muted-foreground" title={q.chapter_name ?? ""}>
                              {q.chapter_name ?? "未指定章节"}
                            </span>
                            {q.knowledge_point_name ? (
                              <Badge variant="secondary" className="text-[10px]">
                                {q.knowledge_point_name}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground">未挂知识点</span>
                            )}
                            {!sec ? (
                              <Badge variant="warning" className="text-[10px]">
                                卷面无{qTypeLabel(q.type)}分段
                              </Badge>
                            ) : null}
                            {notPublished ? (
                              <Badge variant="warning" className="text-[10px]">
                                非已发布
                              </Badge>
                            ) : null}
                            {/* ① 已在卷面的题：显示状态且禁选 */}
                            {already ? (
                              <Badge variant="success" className="gap-1 text-[10px]">
                                <Check className="h-3 w-3" />
                                已在卷面
                              </Badge>
                            ) : null}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {/* ---- 分页 ---- */}
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                共 {total} 道 · 第 {page} / {totalPages} 页 · 已在卷面的题已置灰
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  上一页
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页
                </Button>
              </div>
            </div>
          </div>
        ) : null}

        {/* ======================= 步骤 2：预览确认 ======================= */}
        {step === "confirm" ? (
          <div className="space-y-3">
            <div className="rounded-md border bg-muted/40 p-3">
              <p className="text-sm">
                将加入 <span className="font-semibold">{preview.willAdd.length}</span> 道题
                {preview.willSkipNoSection.length > 0 ? (
                  <>
                    ，另有 <span className="font-semibold text-amber-700">
                      {preview.willSkipNoSection.length}
                    </span>{" "}
                    道因卷面缺少对应题型的分段会被跳过
                  </>
                ) : null}
                。
              </p>
            </div>

            {preview.willAdd.length > 0 ? (
              <div className="max-h-[32vh] overflow-y-auto rounded-md border">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 text-muted-foreground">
                    <tr>
                      <th className="px-3 py-1.5 text-left font-medium">题目</th>
                      <th className="w-[120px] px-3 py-1.5 text-left font-medium">题型</th>
                      <th className="w-[200px] px-3 py-1.5 text-left font-medium">将挂到分段</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {preview.willAdd.map((x) => (
                      <tr key={x.id}>
                        <td className="yj-json px-3 py-1.5 text-muted-foreground">#{x.id}</td>
                        <td className="px-3 py-1.5">{qTypeLabel(x.type)}</td>
                        <td className="px-3 py-1.5">{x.sectionName}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}

            {preview.willSkipNoSection.length > 0 ? (
              <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs">
                <p className="flex items-center gap-1.5 font-medium text-amber-800">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  以下题型在卷面里没有对应分段，提交后会被跳过
                </p>
                <p className="mt-1 text-amber-800">
                  {Array.from(new Set(preview.willSkipNoSection.map((x) => qTypeLabel(x.type)))).join(
                    "、",
                  )}
                  —— 先在「卷面结构」里加一个该题型的分段，再回来加题。
                </p>
              </div>
            ) : null}

            <p className="text-xs text-muted-foreground">
              分段的**计划题数不会自动变大**：加超了之后校验会提示，由你决定是补题结构还是
              把计划题数改成实际值。
            </p>
          </div>
        ) : null}

        {/* ======================= 步骤 3：结果回执 ======================= */}
        {step === "result" && result ? (
          <div className="space-y-3">
            <div className="rounded-md border p-3">
              <p className="text-sm">
                成功加入 <span className="font-semibold text-emerald-700">{result.added}</span> 道
                {result.skipped.length > 0 ? (
                  <>
                    ，跳过{" "}
                    <span className="font-semibold text-amber-700">{result.skipped.length}</span> 道
                  </>
                ) : null}
                。卷面现有 <span className="font-semibold">{result.question_count}</span> 题、
                共 <span className="font-semibold">{result.total_score}</span> 分。
              </p>
            </div>

            {result.skipped.length > 0 ? (
              <div className="max-h-[32vh] overflow-y-auto rounded-md border">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 text-muted-foreground">
                    <tr>
                      <th className="px-3 py-1.5 text-left font-medium">题目 ID</th>
                      <th className="px-3 py-1.5 text-left font-medium">被跳过的原因</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {result.skipped.map((s) => (
                      <tr key={s.question_id}>
                        <td className="yj-json px-3 py-1.5 text-muted-foreground">
                          #{s.question_id}
                        </td>
                        <td className="px-3 py-1.5">{s.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ) : null}

        <DialogFooter>
          {step === "pick" ? (
            <>
              <span className="mr-auto text-xs text-muted-foreground">
                已选 <span className="font-semibold text-foreground">{picked.length}</span> 道
              </span>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button disabled={picked.length === 0} onClick={() => setStep("confirm")}>
                <Sparkles className="h-3.5 w-3.5" />
                下一步：预览
              </Button>
            </>
          ) : step === "confirm" ? (
            <>
              <Button variant="outline" onClick={() => setStep("pick")}>
                <ArrowLeft className="h-3.5 w-3.5" />
                返回修改
              </Button>
              <Button
                disabled={add.isPending || preview.willAdd.length === 0}
                onClick={() => void submit()}
              >
                {add.isPending ? "提交中…" : `确认加入 ${preview.willAdd.length} 道题`}
              </Button>
            </>
          ) : (
            <Button onClick={() => onOpenChange(false)}>完成</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
