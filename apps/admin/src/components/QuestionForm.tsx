"use client";

import { AlertTriangle, Check, Plus, Trash2 } from "lucide-react";
import { useMemo } from "react";

import { MarkdownPreview } from "@/components/MarkdownPreview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  DIFFICULTY_LABELS,
  MAX_OPTIONS,
  MIN_MULTIPLE_CORRECT,
  MIN_OPTIONS,
  Q_STATUS_LABELS,
  SOURCE_TYPE_LABELS,
  diffDraft,
  draftToCreate,
  isEditableType,
  nextOptionKey,
  relabel,
  sourceTypeLabel,
  toggleCorrect,
  validateDraft,
  type OptionDraft,
  type QuestionDraft,
} from "@/lib/question";
import type {
  ChapterTreeOut,
  EditableQType,
  QuestionCreateIn,
  QuestionDetail,
  QStatus,
  SourceType,
  SubjectChapterGroup,
} from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 题目表单（新建 / 编辑共用）。
 *
 * ## B 端特征落点
 *
 * - **① 答案互斥**：单选题的"正确答案"用同名原生 `<input type="radio">`，
 *   点第二个时浏览器自己就把第一个取消掉了；state 层 `toggleCorrect` 再做一遍。
 *   两层都在，是因为"能标两个正确答案"是这道题最不能出的错 ——
 *   多选则用 checkbox，且提交前校验"至少 2 个正确"。
 * - **⑥ markdown/纯文本 + HTML 预览优先**：题干与解析都是 markdown 文本框 + 实时预览。
 *   刻意不做工具栏式富文本（见 `MarkdownPreview` 的注释）。
 * - **⑤ 章节联动**：章节下拉的数据来自 `subject_id` 过滤后的章节树；
 *   换科目会把已选章节清掉（否则会提交"市政的章节挂在建筑科目下"，
 *   后端会以 `40001` 拒绝，用户却不知道为什么）。
 *
 * ## 为什么表单只发出"新建入参 / 变更 patch"，自己不调接口
 *
 * 保存这件事在新建页和详情页的后续动作不同（一个要跳转、一个要就地刷新版本号），
 * 而且详情页还要处理 `40901` 版本冲突的特殊提示。把它留在页面里，
 * 表单就只需要对"输入 → 输出"负责。
 */
export type QuestionFormOutput =
  | { kind: "create"; payload: QuestionCreateIn }
  | { kind: "edit"; patch: (Partial<QuestionCreateIn> & { version: number }) | null };

type Props = {
  mode: "create" | "edit";
  draft: QuestionDraft;
  onDraftChange: (next: QuestionDraft) => void;
  /** 科目 → 章节的联动数据源（整个章节树，组件内部自己按 subject 过滤） */
  tree?: ChapterTreeOut;
  treeLoading?: boolean;
  /** 编辑模式下用于对比出 patch */
  original?: QuestionDetail;
  submitting?: boolean;
  onSubmit: (out: QuestionFormOutput) => void;
  onCancel?: () => void;
  submitLabel?: string;
};

/** Radix Select 的 Item 不接受空字符串 value，用哨兵表示"未选择"。 */
const NONE = "__none__";

function Field({
  label,
  hint,
  required,
  htmlFor,
  children,
  className,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  htmlFor?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
        {label}
        {required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </Label>
      {children}
      {hint ? <p className="text-[11px] leading-relaxed text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function SectionCard({
  title,
  desc,
  children,
}: {
  title: string;
  desc?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border bg-card p-4">
      <div className="mb-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {desc ? (
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{desc}</p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

export function QuestionForm({
  mode,
  draft,
  onDraftChange,
  tree,
  treeLoading,
  original,
  submitting,
  onSubmit,
  onCancel,
  submitLabel,
}: Props) {
  const patch = (p: Partial<QuestionDraft>) => onDraftChange({ ...draft, ...p });

  const groups: SubjectChapterGroup[] = useMemo(() => tree?.items ?? [], [tree?.items]);
  const currentGroup = useMemo(
    () => groups.find((g) => g.subject.id === draft.subject_id),
    [groups, draft.subject_id],
  );
  const chapterOptions = currentGroup?.chapters ?? [];

  const error = validateDraft(draft);
  const correctCount = draft.options.filter((o) => o.is_correct).length;
  const editable = isEditableType(draft.type);

  // ---- 选项编辑 ----
  const addOption = () => {
    if (draft.options.length >= MAX_OPTIONS) return;
    patch({
      options: relabel([
        ...draft.options,
        { key: nextOptionKey(), label: "", content: "", content_html: "", is_correct: false },
      ]),
    });
  };

  const removeOption = (index: number) => {
    if (draft.options.length <= MIN_OPTIONS) return;
    patch({ options: relabel(draft.options.filter((_, i) => i !== index)) });
  };

  const editOption = (index: number, p: Partial<OptionDraft>) => {
    patch({ options: draft.options.map((o, i) => (i === index ? { ...o, ...p } : o)) });
  };

  /** B 端特征①：交给 `toggleCorrect`，单选互斥、多选可多。 */
  const markCorrect = (index: number) => {
    patch({ options: toggleCorrect(draft.options, index, draft.type) });
  };

  const switchType = (next: EditableQType) => {
    // 切到单选时，若原来标了多个正确，只保留第一个 —— 否则用户一切题型就"莫名报错"
    let options = draft.options;
    if (next === "single" && options.filter((o) => o.is_correct).length > 1) {
      const first = options.findIndex((o) => o.is_correct);
      options = options.map((o, i) => ({ ...o, is_correct: i === first }));
    }
    patch({
      type: next,
      options,
      judge_answer: next === "judge" ? draft.judge_answer ?? null : null,
    });
  };

  const submit = () => {
    if (error) return;
    if (mode === "create") {
      onSubmit({ kind: "create", payload: buildCreate(draft) });
      return;
    }
    onSubmit({ kind: "edit", patch: buildPatch(original, draft) });
  };

  return (
    <div className="space-y-4">
      {/* ---------------- 基本信息 ---------------- */}
      <SectionCard title="基本信息" desc="科目决定章节候选，题型决定答案区的形态。">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="科目" required>
            <Select
              value={draft.subject_id || NONE}
              onValueChange={(v) =>
                // 换科目必须清章节：否则会提交"市政的章节 + 建筑的科目"，后端 40001
                patch({ subject_id: v === NONE ? "" : v, chapter_id: "" })
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={treeLoading ? "加载中…" : "请选择科目"} />
              </SelectTrigger>
              <SelectContent>
                {groups.map((g) => (
                  <SelectItem key={g.subject.id} value={g.subject.id}>
                    {g.subject.name}
                    {g.subject.professional ? `（${g.subject.professional}）` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="章节" hint={draft.subject_id ? undefined : "请先选择科目"}>
            <Select
              value={draft.chapter_id || NONE}
              disabled={!draft.subject_id}
              onValueChange={(v) => patch({ chapter_id: v === NONE ? "" : v })}
            >
              <SelectTrigger>
                <SelectValue placeholder={draft.subject_id ? "可不选" : "先选科目"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>不指定章节</SelectItem>
                {chapterOptions.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}（{c.question_count} 题）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="题型" required hint="本批只支持单选 / 多选 / 判断">
            <Select value={draft.type} onValueChange={(v) => switchType(v as EditableQType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="single">单选题</SelectItem>
                <SelectItem value="multiple">多选题</SelectItem>
                <SelectItem value="judge">判断题</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <Field label="状态" required>
            <Select value={draft.status} onValueChange={(v) => patch({ status: v as QStatus })}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(Q_STATUS_LABELS) as QStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>
                    {Q_STATUS_LABELS[s]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
      </SectionCard>

      {/* ---------------- 题干 ---------------- */}
      <SectionCard
        title="题干"
        desc="支持简单 Markdown：**粗体**、*斜体*、`代码`、- 列表、1. 有序列表。右侧为实时预览。"
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <textarea
            value={draft.stem}
            onChange={(e) => patch({ stem: e.target.value })}
            rows={8}
            placeholder="例如：关于市政公用工程施工安全管理，下列说法正确的是？"
            className="w-full resize-y rounded-md border border-input bg-background p-3 text-sm leading-relaxed focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <div className="min-h-[120px] rounded-md border bg-muted/30 p-3 text-sm">
            <MarkdownPreview text={draft.stem} emptyHint="预览会随输入实时更新。" />
          </div>
        </div>
      </SectionCard>

      {/* ---------------- 答案 ---------------- */}
      <SectionCard
        title="答案"
        desc={
          draft.type === "single"
            ? "单选题：点选「正确」时其他选项会自动取消 —— 单选不允许有两个正确答案。"
            : draft.type === "multiple"
              ? `多选题：勾选所有正确答案，至少 ${MIN_MULTIPLE_CORRECT} 个。`
              : "判断题：直接选择「正确」或「错误」。"
        }
      >
        {draft.type === "judge" ? (
          <div className="flex flex-wrap items-center gap-4">
            {[
              { v: true, label: "正确（√）" },
              { v: false, label: "错误（×）" },
            ].map((o) => (
              <label key={String(o.v)} className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="judge_answer"
                  className="h-4 w-4"
                  checked={draft.judge_answer === o.v}
                  onChange={() => patch({ judge_answer: o.v })}
                />
                {o.label}
              </label>
            ))}
            {draft.judge_answer === null ? (
              <span className="text-xs text-muted-foreground">尚未选择</span>
            ) : null}
          </div>
        ) : (
          <div className="space-y-2">
            {draft.options.map((o, i) => (
              <div
                key={o.key}
                className={cn(
                  "flex flex-wrap items-center gap-2 rounded-md border p-2 transition-colors",
                  o.is_correct && "border-emerald-300 bg-emerald-50/60",
                )}
              >
                {/* 正确答案控件：单选用同名 radio（浏览器层强制互斥），多选用 checkbox */}
                <label
                  className="flex cursor-pointer items-center gap-1.5 pl-1"
                  title={draft.type === "single" ? "设为唯一正确答案" : "勾选/取消该正确答案"}
                >
                  <input
                    type={draft.type === "single" ? "radio" : "checkbox"}
                    name={draft.type === "single" ? "single_correct" : undefined}
                    className="h-4 w-4"
                    checked={o.is_correct}
                    onChange={() => markCorrect(i)}
                  />
                  <span className="text-[11px] text-muted-foreground">正确</span>
                </label>

                <span className="yj-json w-6 shrink-0 text-center text-sm font-semibold">
                  {o.label}
                </span>

                <Input
                  value={o.content}
                  onChange={(e) => editOption(i, { content: e.target.value })}
                  placeholder={`选项 ${o.label} 的内容`}
                  className="min-w-[220px] flex-1"
                />

                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="text-muted-foreground hover:text-destructive"
                  disabled={draft.options.length <= MIN_OPTIONS}
                  onClick={() => removeOption(i)}
                  title={
                    draft.options.length <= MIN_OPTIONS
                      ? `至少保留 ${MIN_OPTIONS} 个选项`
                      : "删除该选项"
                  }
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}

            <div className="flex flex-wrap items-center gap-3 pt-1">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={draft.options.length >= MAX_OPTIONS}
                onClick={addOption}
              >
                <Plus className="h-3.5 w-3.5" />
                添加选项
              </Button>
              <span className="text-xs text-muted-foreground">
                已标 {correctCount} 个正确答案 · 选项 {draft.options.length}/{MAX_OPTIONS}
              </span>
              {draft.type === "single" && correctCount === 1 ? (
                <Badge variant="success" className="text-[10px]">
                  单选答案 OK
                </Badge>
              ) : null}
            </div>
          </div>
        )}
      </SectionCard>

      {/* ---------------- 解析 ---------------- */}
      <SectionCard title="解析" desc="同样支持简单 Markdown，右侧实时预览。">
        <div className="grid gap-4 lg:grid-cols-2">
          <textarea
            value={draft.analysis}
            onChange={(e) => patch({ analysis: e.target.value })}
            rows={6}
            placeholder="说明为什么选这个答案，以及干扰项错在哪。"
            className="w-full resize-y rounded-md border border-input bg-background p-3 text-sm leading-relaxed focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <div className="min-h-[100px] rounded-md border bg-muted/30 p-3 text-sm">
            <MarkdownPreview text={draft.analysis} emptyHint="还没有填写解析。" />
          </div>
        </div>
      </SectionCard>

      {/* ---------------- 属性 ---------------- */}
      <SectionCard title="属性">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="难度">
            <Select
              value={String(draft.difficulty)}
              onValueChange={(v) => patch({ difficulty: Number(v) })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[1, 2, 3, 4, 5].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {DIFFICULTY_LABELS[n]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="默认分值" required hint="0~100，可为小数">
            <Input
              value={draft.score_default}
              onChange={(e) => patch({ score_default: e.target.value })}
              inputMode="decimal"
            />
          </Field>

          <Field label="年份" hint="真题年份，可留空">
            <Input
              value={draft.exam_year}
              onChange={(e) => patch({ exam_year: e.target.value })}
              placeholder="如 2025"
              inputMode="numeric"
            />
          </Field>

          <Field label="关键词" hint="用于列表模糊搜索，逗号分隔">
            <Input
              value={draft.keywords}
              onChange={(e) => patch({ keywords: e.target.value })}
              placeholder="如 安全管理,基坑"
            />
          </Field>

          <Field
            label="标签"
            hint="最多 20 个，逗号或空格分隔"
            className="sm:col-span-2 lg:col-span-4"
          >
            <Input
              value={draft.tags}
              onChange={(e) => patch({ tags: e.target.value })}
              placeholder="如 高频考点 易错"
            />
          </Field>
        </div>
      </SectionCard>

      {/* ---------------- 合规 ---------------- */}
      <SectionCard
        title="题库合规"
        desc="来源类型必填。非「自有原创」必须写明来源；「授权引进」还必须填授权凭证。这是合规红线，后端同样会校验。"
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="来源类型" required>
            <Select
              value={draft.source_type}
              onValueChange={(v) => patch({ source_type: v as SourceType })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(SOURCE_TYPE_LABELS) as SourceType[]).map((s) => (
                  <SelectItem key={s} value={s}>
                    {SOURCE_TYPE_LABELS[s]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field
            label="来源名称"
            required={draft.source_type !== "self"}
            hint={draft.source_type !== "self" ? "必填：书名/网站/机构名" : "自有原创可留空"}
          >
            <Input
              value={draft.source_name}
              onChange={(e) => patch({ source_name: e.target.value })}
              placeholder={draft.source_type === "self" ? "—" : "如 中国建筑工业出版社《市政实务》"}
              disabled={draft.source_type === "self"}
            />
          </Field>

          <Field
            label="授权凭证 / 许可说明"
            required={draft.source_type === "authorized"}
            hint={draft.source_type === "authorized" ? "必填：授权编号或说明" : "仅授权引进需要"}
          >
            <Input
              value={draft.source_license}
              onChange={(e) => patch({ source_license: e.target.value })}
              placeholder={draft.source_type === "authorized" ? "如 SC-2026-0301" : "—"}
              disabled={draft.source_type !== "authorized"}
            />
          </Field>

          <Field label="版权方" className="sm:col-span-2">
            <Input
              value={draft.copyright_holder}
              onChange={(e) => patch({ copyright_holder: e.target.value })}
              placeholder="可留空"
            />
          </Field>
        </div>
      </SectionCard>

      {/* ---------------- 底部：校验 + 动作 ---------------- */}
      <div className="sticky bottom-0 -mx-6 border-t bg-background/95 px-6 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 text-sm">
            {error ? (
              <span className="flex items-start gap-1.5 text-destructive">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-emerald-700">
                <Check className="h-4 w-4" />
                {mode === "edit" ? "校验通过，保存后版本号会 +1" : "校验通过，可以保存"}
              </span>
            )}
            {!editable ? (
              <span className="mt-1 block text-xs text-muted-foreground">
                当前题型本批不可编辑。
              </span>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            {onCancel ? (
              <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
                取消
              </Button>
            ) : null}
            <Button type="button" onClick={submit} disabled={!!error || submitting || !editable}>
              {submitting ? "保存中…" : submitLabel ?? (mode === "create" ? "创建题目" : "保存")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * 把"输入 → 后端入参"的两个转换在此收口，页面就不用各自 import 一遍 `lib/question`。
 * 表单只管输出，不负责提交 —— 创建/编辑的后续动作（跳转、冲突处理）留在页面里。
 */
function buildCreate(d: QuestionDraft): QuestionCreateIn {
  return draftToCreate(d);
}

function buildPatch(original: QuestionDetail | undefined, d: QuestionDraft) {
  if (!original) return null;
  return diffDraft(original, d);
}

// 让"来源类型"文案在同一份 UI 里只有一处来源
export { sourceTypeLabel };
