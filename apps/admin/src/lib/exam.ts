/**
 * 试卷 / 组卷的展示层常量与纯函数。
 *
 * 与 `lib/question.ts` 同一个定位：**枚举 → 中文**的映射只有一份，
 * 外加几个"把后端的数字变成人话"的小函数。页面里不写 `status === "draft" ? "草稿" : ...`。
 */

import type { ExamSectionOut, ExamStatus, ExamType, RuleStatus, RuleStrategy } from "./types";

// ---------------------------------------------------------------- 枚举 → 中文

export const EXAM_TYPE_LABELS: Record<ExamType, string> = {
  real: "真题",
  mock: "模拟卷",
  chapter_test: "章节测试",
  sprint: "冲刺卷",
  daily: "每日一练",
  custom: "自定义",
};

export function examTypeLabel(t: string): string {
  return EXAM_TYPE_LABELS[t as ExamType] ?? t;
}

export const EXAM_STATUS_LABELS: Record<ExamStatus, string> = {
  draft: "草稿",
  //: 预留：审核流程未开（docs/14 P1），当前无接口能写入该状态
  reviewing: "待审",
  published: "已发布",
  off: "已下线",
  // ⚠️ `archived` 已删（20260919-01）—— 与 is_deleted 语义重复且从未被写入。
  // 之前它在筛选下拉里显示为「已停用」，是个永远筛不出东西的选项。
};

export function examStatusLabel(s: string): string {
  return EXAM_STATUS_LABELS[s as ExamStatus] ?? s;
}

/** 与 `lib/question.ts` 的 `qStatusVariant` 同一套语义。 */
export function examStatusVariant(
  s: ExamStatus,
): "default" | "secondary" | "success" | "warning" | "destructive" | "outline" {
  switch (s) {
    case "published":
      return "success";
    case "reviewing":
      return "warning";
    case "draft":
      return "secondary";
    case "off":
      return "outline";
    default:
      return "outline";
  }
}

export const RULE_STATUS_LABELS: Record<RuleStatus, string> = {
  on: "启用",
  off: "停用",
};

export function ruleStatusLabel(s: string): string {
  return RULE_STATUS_LABELS[s as RuleStatus] ?? s;
}

export const RULE_STRATEGY_LABELS: Record<RuleStrategy, string> = {
  random: "随机抽题",
  weak_first: "薄弱优先",
  coverage: "知识点覆盖",
  history_similar: "相似历史",
};

export function ruleStrategyLabel(s: string): string {
  return RULE_STRATEGY_LABELS[s as RuleStrategy] ?? s;
}

/** 题型中文名。与题库页保持一致（那边在 `lib/question.ts`，这里只补齐组卷用得到的）。 */
export const QTYPE_LABELS: Record<string, string> = {
  single: "单项选择题",
  multiple: "多项选择题",
  judge: "判断题",
  case: "案例题",
  case_sub: "案例小问",
  fill: "填空题",
  essay: "简答题",
};

export function qtypeLabel(t: string): string {
  return QTYPE_LABELS[t] ?? t;
}

/** 题型短名（列宽紧张时用，如分段标题）。 */
export function qtypeShort(t: string): string {
  return qtypeLabel(t).replace(/题$/, "").replace("项选择", "选");
}

// ---------------------------------------------------------------- 分段缺口

export type SectionGap = {
  /** 计划题数（卷面契约） */
  planned: number;
  /** 实际入卷题数 */
  actual: number;
  /** `actual - planned`：负数 = 还差，正数 = 多了，0 = 对齐 */
  diff: number;
  state: "ok" | "short" | "over";
  /** 一句人话，直接展示 —— **不要只给数字差异** */
  text: string;
};

/**
 * 分段的"计划 vs 实际"。
 *
 * 刻意返回一句**人话**（`还差 3 道多项选择题`），而不是让每个调用方各拼一遍
 * ——"只给数字差异"是 B 端最容易被诟病的表达方式：用户看到 `-3`
 * 得自己反应过来是少 3 道、还得回头看这是哪个分段。
 */
export function sectionGap(
  section: Pick<ExamSectionOut, "question_count" | "actual_count" | "question_type">,
): SectionGap {
  const planned = section.question_count;
  const actual = section.actual_count;
  const diff = actual - planned;
  const label = qtypeLabel(section.question_type);

  if (diff === 0) {
    return { planned, actual, diff, state: "ok", text: `计划 ${planned} 道，已排满` };
  }
  if (diff < 0) {
    return {
      planned,
      actual,
      diff,
      state: "short",
      text: `还差 ${-diff} 道${label}（计划 ${planned} 道，实际 ${actual} 道）`,
    };
  }
  return {
    planned,
    actual,
    diff,
    state: "over",
    text: `多了 ${diff} 道${label}（计划 ${planned} 道，实际 ${actual} 道）`,
  };
}

/** 整张卷有没有对不上的分段。 */
export function hasSectionGap(
  sections: Pick<ExamSectionOut, "question_count" | "actual_count">[],
): boolean {
  return sections.some((s) => s.question_count !== s.actual_count);
}

// ---------------------------------------------------------------- 校验结果的结构化指引

export type ValidateGuidance = {
  /** 这一条到底在说什么（比后端 message 更"面向操作者"） */
  title: string;
  /** **下一步怎么做** —— 校验报告里最该有、也最常被漏掉的东西 */
  action: string;
};

/**
 * 校验错误码 → 「是什么 + 下一步」。
 *
 * 为什么需要这张表：后端已经给了准确的 `message`（"计划 60 道实际 52 道"），
 * 但**只说了问题，没说该怎么办**。教研看到"固定卷面下 SECTION_NOT_FILLED"
 * 的第一反应是"那我该去哪改"—— 于是每条都配一个可执行的动作。
 *
 * 找不到的 code 会兜底成"按提示修正后重新校验"，不会渲染出空白。
 */
export const VALIDATE_GUIDANCE: Record<string, ValidateGuidance> = {
  NO_QUESTIONS: {
    title: "卷面一道题都没有",
    action: "点「自动组卷」按规则抽题；也可以「手动加题」自己挑。",
  },
  SECTION_NOT_FILLED: {
    title: "分段的实际题数与计划对不上",
    action:
      "两个选择：① 补题 / 移题，让实际题数回到计划值；② 把「卷面结构」里的计划题数改成实际值 —— 那等于明确认可新的卷面构成。",
  },
  SECTION_SCORE_MISMATCH: {
    title: "分段分值对不上（题数 × 每题分 ≠ 分总分）",
    action: "检查该分段的「每题分值」是否被改过；通常是改分值后没重新组卷。",
  },
  TOTAL_SCORE_MISMATCH: {
    title: "试卷登记总分与实际卷面题分之和不一致",
    action: "重新组卷或加/移题后会自动重算；若仍不一致，检查是否有分段的每题分值被单独改过。",
  },
  TOTAL_COUNT_MISMATCH: {
    title: "试卷登记题量与实际不一致",
    action: "重新组卷或加减题即可自动修正。",
  },
  DUPLICATE_QUESTION: {
    title: "同一道题在卷面出现了多次",
    action: "在卷面列表里找到重复项，把多余的那条「移出卷面」。",
  },
  QUESTION_NOT_PUBLISHED: {
    title: "卷面里有题目不是「已发布」状态",
    action: "到题库把这几道题发布，或把它们移出卷面、换一道已发布的题。",
  },
  QUESTION_DELETED: {
    title: "卷面里有题目已被归档（软删除）",
    action:
      "三个选择：① 到题库恢复这些题（注意：若题目挂的章节也被删了，恢复会被拒绝，需先处理章节）；② 移出卷面换别的题；③ 重新组卷。",
  },
  COMPOSE_SHORTFALL: {
    title: "上次组卷有规则没凑够题（缺口未自动顶替）",
    action:
      "按缺口面板补齐：放宽该规则的难度/年份/知识点条件，或减少需要的题数，然后重新组卷。**系统不会自动凑数**。",
  },
  NO_PASS_SCORE: {
    title: "未设置及格线",
    action: "在「基本信息」里填及格分。只是提醒，不拦住发布。",
  },
  VERSION_DRIFT: {
    title: "有题目在发布后被修改过",
    action:
      "**不需要处理**：试卷按锁定版本作答与展示，考生看到的是发布时那一版。若确实想让新版本生效，需要下线后重新发布。",
  },
};

export function validateGuidance(code: string): ValidateGuidance {
  return (
    VALIDATE_GUIDANCE[code] ?? {
      title: "校验未通过",
      action: "按上面的说明修正后，重新点「校验」再发布。",
    }
  );
}

// ---------------------------------------------------------------- 组卷缺口（shortfalls）

/**
 * 缺口列表 → 一句可读的总结。
 *
 * `need / got / missing` 三个数字光摆出来，用户得自己算"到底差多少"；
 * 直接说"共缺 73 道"（并把最大的那条点名）才有用。
 */
export function shortfallSummary(
  gaps: { need: number; got: number; missing: number; rule_label: string }[],
): string {
  if (!gaps.length) return "没有缺口";
  const totalMissing = gaps.reduce((s, g) => s + g.missing, 0);
  const worst = [...gaps].sort((a, b) => b.missing - a.missing)[0];
  return `共缺 ${totalMissing} 道；缺口最大的是「${worst.rule_label}」（缺 ${worst.missing} 道）`;
}
