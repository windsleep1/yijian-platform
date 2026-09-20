/**
 * 题库领域的纯逻辑与文案映射。
 *
 * 放在 `lib/` 而不是组件里，是因为其中 `validateDraft` 是
 * **B 端特征①（答案互斥/答案必须在选项内）在前端的那一半**，
 * 值得单独可读、可测，而不是埋在某个组件的 onChange 里。
 *
 * ## 前后端校验的分工（不要搞混）
 *
 * 后端 `_validate_options` 是**唯一的真相**：它拦下所有非法组合，返回 `40001`。
 * 前端的 `validateDraft` 不是"第二道防线"，而是**提前告知**——
 * 让用户在点"保存"之前就知道哪里不对，而不是提交后吃一个红 toast。
 * 所以两边规则必须一致；不一致时以后端为准，并回来修这里。
 */

import type {
  EditableQType,
  QStatus,
  QType,
  QuestionCreateIn,
  QuestionDetail,
  QuestionOptionIn,
  SourceType,
} from "./types";

/**
 * 「有标号和正确答案」的最小结构。
 *
 * 刻意不直接用 `OptionDraft`：`toggleCorrect` / `relabel` 是纯粹的数组变换，
 * 只依赖这两个字段。把它们绑死在带 `key` 的表单类型上会让以后想复用
 * （比如给别的地方做选项预览）时被迫造一堆假 `key`。
 */
export type OptionDraftLike = { label: string; is_correct: boolean };

// 与后端 app/schemas/admin_question.py 的常量保持一致
export const MIN_OPTIONS = 2;
export const MAX_OPTIONS = 8;
export const MIN_MULTIPLE_CORRECT = 2;

/** 选项标号池：最多 8 个选项，用得到 A~H。 */
export const LETTERS = "ABCDEFGH".split("");

export const EDITABLE_TYPES: readonly EditableQType[] = ["single", "multiple", "judge"];

export function isEditableType(type: string): type is EditableQType {
  return (EDITABLE_TYPES as readonly string[]).includes(type);
}

// ---------------------------------------------------------------- 文案映射

export const Q_TYPE_LABELS: Record<QType, string> = {
  single: "单选题",
  multiple: "多选题",
  judge: "判断题",
  case: "案例题",
  case_sub: "案例小问",
  fill: "填空题",
  essay: "简答题",
};

export function qTypeLabel(code: string): string {
  return Q_TYPE_LABELS[code as QType] ?? code;
}

export const Q_STATUS_LABELS: Record<QStatus, string> = {
  draft: "草稿",
  reviewing: "待审核",
  published: "已发布",
  rejected: "已驳回",
  archived: "已归档",
};

export function qStatusLabel(code: string): string {
  return Q_STATUS_LABELS[code as QStatus] ?? code;
}

/** 状态徽章配色（复用 ui/badge 的 variant）。 */
export function qStatusVariant(
  code: string,
): "success" | "warning" | "info" | "secondary" | "destructive" {
  switch (code) {
    case "published":
      return "success";
    case "reviewing":
      return "info";
    case "rejected":
      return "destructive";
    case "archived":
      return "secondary";
    default:
      return "warning"; // draft
  }
}

export const DIFFICULTY_LABELS: Record<number, string> = {
  1: "1 · 很易",
  2: "2 · 较易",
  3: "3 · 中等",
  4: "4 · 较难",
  5: "5 · 很难",
};

export function difficultyLabel(n: number): string {
  return DIFFICULTY_LABELS[n] ?? String(n);
}

export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  self: "自有原创",
  public: "公开资料",
  authorized: "授权引进",
  user_import: "用户导入",
  ai_assisted: "AI 辅助生成",
};

export function sourceTypeLabel(code: string): string {
  return SOURCE_TYPE_LABELS[code as SourceType] ?? code;
}

// ---------------------------------------------------------------- B 端特征①：答案互斥

/**
 * 标记正确答案时的**互斥规则**（前端这一半）。
 *
 * - `single`：把点中的这个设为唯一正确，其余全部取消 —— 这就是"单选不能标 2 个答案"。
 * - `multiple`：普通勾选/取消，可以多个。
 * - `judge`：判断题没有选项，走 `judge_answer`，这里直接原样返回。
 *
 * 返回新数组（不原地改），便于直接喂给 React 的 setState。
 */
export function toggleCorrect<O extends OptionDraftLike>(
  options: O[],
  index: number,
  type: EditableQType,
): O[] {
  if (type === "single") {
    return options.map((o, i) => ({ ...o, is_correct: i === index }));
  }
  return options.map((o, i) => (i === index ? { ...o, is_correct: !o.is_correct } : o));
}

/** 选项数量变化后重新分配标号（A/B/C…），并保持顺序。 */
export function relabel<O extends OptionDraftLike>(options: O[]): O[] {
  return options.map((o, i) => ({ ...o, label: LETTERS[i] ?? String(i + 1) }));
}

// ---------------------------------------------------------------- 校验

/** 表单草稿。字段用字符串承载输入框的原始值，提交时才转成后端的强类型。 */
export type OptionDraft = {
  /** React key，与业务无关 */
  key: string;
  label: string;
  content: string;
  content_html: string;
  is_correct: boolean;
};

export type QuestionDraft = {
  subject_id: string;
  chapter_id: string;
  type: EditableQType;
  stem: string;
  judge_answer: boolean | null;
  analysis: string;
  difficulty: number;
  score_default: string;
  status: QStatus;
  exam_year: string;
  keywords: string;
  tags: string;
  source_type: SourceType;
  source_name: string;
  source_license: string;
  copyright_holder: string;
  options: OptionDraft[];
};

let seq = 0;
export function nextOptionKey(): string {
  seq += 1;
  return `opt-${seq}`;
}

export function emptyDraft(): QuestionDraft {
  return {
    subject_id: "",
    chapter_id: "",
    type: "single",
    stem: "",
    judge_answer: null,
    analysis: "",
    difficulty: 3,
    score_default: "1",
    status: "draft",
    exam_year: "",
    keywords: "",
    tags: "",
    source_type: "self",
    source_name: "",
    source_license: "",
    copyright_holder: "",
    options: relabel(
      LETTERS.slice(0, 4).map((label) => ({
        key: nextOptionKey(),
        label,
        content: "",
        content_html: "",
        is_correct: false,
      })),
    ),
  };
}

/**
 * 校验草稿。返回**第一条**错误的中文说明，或 `null` 表示可以提交。
 *
 * 与后端 `_validate_options` + `QuestionCreateIn._check` 一一对应。
 * 只报第一条：一次甩出五条错，用户反而不知道该先改哪个。
 */
export function validateDraft(d: QuestionDraft): string | null {
  if (!d.subject_id) return "请先选择科目。";
  if (!d.stem.trim()) return "题干不能为空。";

  const score = Number(d.score_default);
  if (!Number.isFinite(score) || score <= 0 || score > 100) {
    return "默认分值需为 0~100 之间的正数。";
  }
  if (d.exam_year.trim()) {
    const y = Number(d.exam_year);
    if (!Number.isInteger(y) || y < 2000 || y > 2100) return "年份需为 2000~2100 之间的整数。";
  }

  if (d.type === "judge") {
    if (d.judge_answer === null) return "判断题必须选择「正确」或「错误」。";
  } else {
    const opts = d.options;
    if (opts.length < MIN_OPTIONS)
      return `选项至少要有 ${MIN_OPTIONS} 个（当前 ${opts.length} 个）。`;
    if (opts.length > MAX_OPTIONS) return `选项最多 ${MAX_OPTIONS} 个（当前 ${opts.length} 个）。`;

    const labels = opts.map((o) => o.label.trim().toUpperCase());
    if (labels.some((l) => !l)) return "选项标号不能为空。";
    const dup = labels.find((l, i) => labels.indexOf(l) !== i);
    if (dup) return `选项标号重复：${dup}。`;

    const blank = opts.findIndex((o) => !o.content.trim());
    if (blank >= 0) return `第 ${blank + 1} 个选项的内容不能为空。`;

    const correct = opts.filter((o) => o.is_correct).length;
    if (d.type === "single") {
      // ← B 端特征①：单选题不能标两个正确答案
      if (correct === 0) return "单选题必须标记 1 个正确答案（当前一个都没标）。";
      if (correct !== 1) return `单选题只能有 1 个正确答案，当前标了 ${correct} 个。`;
    } else if (d.type === "multiple") {
      if (correct < MIN_MULTIPLE_CORRECT) {
        return `多选题至少要有 ${MIN_MULTIPLE_CORRECT} 个正确答案（当前 ${correct} 个）。只有一个正确答案的题目请改用单选题。`;
      }
    }
  }

  // 合规三件套（见 docs/07 与项目铁律）
  if (d.source_type !== "self" && !d.source_name.trim()) {
    return `来源类型为「${sourceTypeLabel(d.source_type)}」时必须填写来源名称。`;
  }
  if (d.source_type === "authorized" && !d.source_license.trim()) {
    return "授权引进的题目必须填写授权凭证号 / 许可说明。";
  }

  return null;
}

// ---------------------------------------------------------------- 草稿 ↔ 后端入参

function splitTags(raw: string): string[] {
  return raw
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 20);
}

function toOptions(d: QuestionDraft): QuestionOptionIn[] {
  return d.options.map((o) => ({
    label: o.label.trim().toUpperCase(),
    content: o.content.trim(),
    content_html: o.content_html.trim() || null,
    is_correct: o.is_correct,
  }));
}

/** 草稿 → 新建入参。调用前必须先过 `validateDraft`。 */
export function draftToCreate(d: QuestionDraft): QuestionCreateIn {
  const base: QuestionCreateIn = {
    // ID 原样传字符串，**不要 Number()** —— 雪花 ID 一过 Number 就被舍入（见 types.ts 说明）
    subject_id: d.subject_id,
    chapter_id: d.chapter_id || null,
    type: d.type,
    stem: d.stem.trim(),
    analysis: d.analysis.trim() || null,
    difficulty: d.difficulty,
    score_default: Number(d.score_default),
    status: d.status,
    exam_year: d.exam_year.trim() ? Number(d.exam_year) : null,
    keywords: d.keywords.trim() || null,
    tags: splitTags(d.tags),
    source_type: d.source_type,
    source_name: d.source_name.trim() || null,
    source_license: d.source_license.trim() || null,
    copyright_holder: d.copyright_holder.trim() || null,
    options: d.type === "judge" ? [] : toOptions(d),
  };
  if (d.type === "judge") base.judge_answer = d.judge_answer;
  return base;
}

/** 详情 → 草稿（用于"编辑"表单初始化）。 */
export function draftFromDetail(q: QuestionDetail): QuestionDraft {
  const judge = q.type === "judge" ? (q.answer?.value?.[0] as boolean | undefined) ?? false : null;
  return {
    subject_id: q.subject_id,
    chapter_id: q.chapter_id ?? "",
    type: isEditableType(q.type) ? q.type : "single",
    stem: q.stem ?? "",
    judge_answer: judge,
    analysis: q.analysis ?? "",
    difficulty: q.difficulty,
    score_default: String(q.score_default ?? 1),
    status: q.status,
    exam_year: q.exam_year ? String(q.exam_year) : "",
    keywords: q.keywords ?? "",
    tags: (q.tags ?? []).join("，"),
    source_type: q.source_type,
    source_name: q.source_name ?? "",
    source_license: q.source_license ?? "",
    copyright_holder: q.copyright_holder ?? "",
    options: q.options.length
      ? relabel(
          [...q.options]
            .sort((a, b) => a.sort_no - b.sort_no)
            .map((o) => ({
              key: nextOptionKey(),
              label: o.label,
              content: o.content,
              content_html: o.content_html ?? "",
              is_correct: o.is_correct,
            })),
        )
      : emptyDraft().options,
  };
}

const norm = (s: string | null | undefined) => (s ?? "").trim();
const normOrNull = (s: string | null | undefined) => norm(s) || null;

/**
 * 草稿 vs 原始详情 → **只含变化字段的 patch**。没有任何变化时返回 `null`。
 *
 * 为什么非要算 diff、而不是整份回传：
 *  1. 后端把 `undefined` 解释为"沿用现值"，整份回传等于"什么都算改了"，
 *     `content_change_logs` 的 diff 会退化成一坨全字段快照，失去可读性；
 *  2. 两个人同时编辑同一道题、改的字段不同时，全量回传会互相覆盖。
 * 所以这里宁可多写一层比较。
 */
export function diffDraft(
  original: QuestionDetail,
  d: QuestionDraft,
): (Partial<QuestionCreateIn> & { version: number }) | null {
  const before = draftFromDetail(original);
  const patch: Partial<QuestionCreateIn> = {};

  if (norm(before.stem) !== norm(d.stem)) patch.stem = d.stem.trim();
  if (norm(before.analysis) !== norm(d.analysis)) patch.analysis = normOrNull(d.analysis);
  if (before.type !== d.type) patch.type = d.type;
  if (Number(before.score_default) !== Number(d.score_default))
    patch.score_default = Number(d.score_default);
  if (before.difficulty !== d.difficulty) patch.difficulty = d.difficulty;
  if (before.status !== d.status) patch.status = d.status;
  if (norm(before.keywords) !== norm(d.keywords)) patch.keywords = normOrNull(d.keywords);
  if (norm(before.exam_year) !== norm(d.exam_year)) {
    patch.exam_year = d.exam_year.trim() ? Number(d.exam_year) : null;
  }
  if (norm(before.source_type) !== norm(d.source_type)) patch.source_type = d.source_type;
  if (norm(before.source_name) !== norm(d.source_name))
    patch.source_name = normOrNull(d.source_name);
  if (norm(before.source_license) !== norm(d.source_license)) {
    patch.source_license = normOrNull(d.source_license);
  }
  if (norm(before.copyright_holder) !== norm(d.copyright_holder)) {
    patch.copyright_holder = normOrNull(d.copyright_holder);
  }
  // ⚠️ subject_id / chapter_id 必须保持字符串（雪花 ID，18~19 位）。
  // 这里曾经写成 Number(d.subject_id)，会把 375273861765140480 悄悄变成
  // 375273861765140500，后端按 ID 匹配不到 → 静默改错章节 / 报 not found。
  // 详见 apps/admin/docs/B端联调坑.md 第 21 条。
  if (norm(before.subject_id) !== norm(d.subject_id)) {
    patch.subject_id = d.subject_id || undefined;
  }
  if (norm(before.chapter_id) !== norm(d.chapter_id)) {
    patch.chapter_id = d.chapter_id || null;
  }
  if (JSON.stringify(splitTags(before.tags)) !== JSON.stringify(splitTags(d.tags))) {
    patch.tags = splitTags(d.tags);
  }

  // 判断题的答案
  if (d.type === "judge") {
    if (before.judge_answer !== d.judge_answer) patch.judge_answer = d.judge_answer;
  } else if (before.type === "judge" || before.type !== d.type) {
    // 从判断题切到客观题：选项必然要一起提交
    patch.options = toOptions(d);
  } else if (optionsChanged(before.options, d.options)) {
    patch.options = toOptions(d);
  }

  if (Object.keys(patch).length === 0) return null;
  return { ...patch, version: original.version };
}

function optionsChanged(before: OptionDraft[], after: OptionDraft[]): boolean {
  const shape = (list: OptionDraft[]) =>
    list.map((o) => [
      o.label.trim().toUpperCase(),
      norm(o.content),
      normOrNull(o.content_html),
      o.is_correct,
    ]);
  return JSON.stringify(shape(before)) !== JSON.stringify(shape(after));
}

/** 人类可读的"将改动这些字段"，用于保存前提示与保存后的 toast。 */
export function describePatch(patch: Record<string, unknown>): string {
  const LABELS: Record<string, string> = {
    stem: "题干",
    analysis: "解析",
    type: "题型",
    options: "选项/答案",
    difficulty: "难度",
    score_default: "分值",
    status: "状态",
    keywords: "关键词",
    exam_year: "年份",
    tags: "标签",
    subject_id: "科目",
    chapter_id: "章节",
    source_type: "来源类型",
    source_name: "来源名称",
    source_license: "授权凭证",
    copyright_holder: "版权方",
    judge_answer: "判断题答案",
  };
  const names = Object.keys(patch)
    .filter((k) => k !== "version")
    .map((k) => LABELS[k] ?? k);
  return names.length ? names.join("、") : "无";
}
