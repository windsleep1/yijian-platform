/**
 * 导入向导的**纯逻辑与文案**（Batch 6）。
 *
 * 与 `lib/question.ts` 同一套分工：能被"读一遍就对"的规则放这里，
 * 组件里只留渲染与状态。
 *
 * 本文件里有三件事值得单独说明，它们都是**踩过或差点踩到**的地方：
 *
 *  1. **状态流转图不能编状态。** 后端 `import_batches.status` 只有
 *     `pending / parsing / validating / importing / done / failed / rolled_back`，
 *     **没有 `validated`**（校验完也是 `done`），也**没有 `published`**
 *     （发布不会改批次状态）。所以流转图宁可少画一格，也不画后端给不出的节点。
 *
 *  2. **错误报告 CSV 必须带 BOM。** 不带 BOM 的 UTF-8 CSV 用 Excel 打开是乱码 ——
 *     而"错误报告能下载、能用 Excel 打开"正是验收标准之一。
 *
 *  3. **CSV 要正确转义。** 错误信息里会出现中文顿号、逗号、甚至引号
 *     （"答案 D 不在选项中（本题选项：A、B、C）"）。不转义的话，这一格会被
 *     逗号切成三列，Excel 里整张表错位。
 */

import type { ImportBatch, ImportStatus, RowError } from "./types";

// ---------------------------------------------------------------- 文案映射

export const IMPORT_STATUS_LABELS: Record<ImportStatus, string> = {
  pending: "待校验",
  parsing: "解析中",
  validating: "校验中",
  importing: "导入中",
  done: "校验完成",
  failed: "失败",
  rolled_back: "已回滚",
};

export function importStatusLabel(code: string): string {
  return IMPORT_STATUS_LABELS[code as ImportStatus] ?? code;
}

/** 状态徽章配色（复用 `ui/badge` 的 variant）。 */
export function importStatusVariant(
  code: string,
): "success" | "warning" | "info" | "secondary" | "destructive" | "outline" {
  switch (code) {
    case "done":
      return "info";
    case "rolled_back":
      return "secondary";
    case "failed":
      return "destructive";
    case "importing":
    case "validating":
    case "parsing":
      return "warning";
    default:
      return "outline"; // pending
  }
}

/** 逐行结果的 `action` → 中文。 */
export const IMPORT_ACTION_LABELS: Record<string, string> = {
  insert: "新增",
  update: "更新",
  duplicate: "跳过（重复）",
  skip: "跳过",
  error: "错误",
};

export function importActionLabel(code: string): string {
  return IMPORT_ACTION_LABELS[code] ?? code;
}

export function importActionVariant(
  code: string,
): "success" | "warning" | "info" | "secondary" | "destructive" {
  switch (code) {
    case "insert":
      return "success";
    case "update":
      return "info";
    case "duplicate":
    case "skip":
      return "secondary";
    case "error":
      return "destructive";
    default:
      return "secondary";
  }
}

/** 变更日志的 `action` → 中文（`content_change_logs.action`）。 */
export const CHANGE_ACTION_LABELS: Record<string, string> = {
  create: "新增",
  update: "更新",
  publish: "发布",
  rollback: "回滚",
  archive: "归档",
  delete: "删除",
};

export function changeActionLabel(code: string): string {
  return CHANGE_ACTION_LABELS[code] ?? code;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

// ---------------------------------------------------------------- 状态流转

export type FlowState = "done" | "current" | "todo" | "failed";
export type FlowStep = {
  key: string;
  label: string;
  hint: string;
  state: FlowState;
  /**
   * 需求文档里的**规范阶段名**（`pending` / `validated` / `importing` / `done` / `rolled_back`）。
   *
   * 为什么要单独留一个字段而不是直接用后端状态：需求给的这五个名字是**概念上的生命周期**，
   * 后端的 `import_batches.status` 并不完全对得上（没有 `validated`，也没有 `published`）。
   * 把规范名显式画在节点上，好处是验收时"pending → validated → importing → done → rolled_back"
   * 这条链在界面上一眼可见，同时 `hint` 里仍然写着它对应的真实后端状态 ——
   * 既不糊弄验收，也不对后端撒谎。
   */
  stage: string;
};

/**
 * 本批是否**已经写入过题库**。
 *
 * 后端没有单独的"已执行"状态位（执行完仍然是 `done`），所以只能拼证据：
 *
 *  1. `success_rows + updated_rows > 0` —— 明确写过东西，铁证；
 *  2. `can_execute === false` 且 `status === 'done'` —— 服务端按
 *     `content_change_logs.batch_id` 判定"这一批执行过了"（本批只允许执行一次），
 *     对拥有 `question:import` 的账号成立。
 *
 * 两条都不成立时**不猜**：把节点停在「校验完成」。宁可比真实进度少画一格，
 * 也不要画出一个后端其实没告诉我们的状态 —— 那是"UI 撒谎"。
 */
export function isExecuted(
  b: Pick<ImportBatch, "status" | "success_rows" | "updated_rows" | "can_execute">,
): boolean {
  if (b.status === "rolled_back") return true;
  if (b.status === "importing") return true;
  if (b.success_rows + b.updated_rows > 0) return true;
  return b.status === "done" && b.can_execute === false;
}

/**
 * 批次状态流转：建批次 → 校验完成 → 执行中 → 已入库 → 已回滚。
 *
 * 五个节点对应**后端真实状态**，映射关系写在 `hint` 里，
 * 免得下一个人对着 `validated` 这个不存在的状态去找后端要。
 */
export function importStatusFlow(b: ImportBatch): { steps: FlowStep[]; failedAt: number | null } {
  const executed = isExecuted(b);

  const steps: FlowStep[] = [
    { key: "created", label: "建批次", hint: "pending", stage: "pending", state: "todo" },
    {
      key: "validated",
      label: "校验完成",
      hint: "done · 试算未写库",
      stage: "validated",
      state: "todo",
    },
    {
      key: "importing",
      label: "执行中",
      hint: "importing",
      stage: "importing",
      state: "todo",
    },
    { key: "stored", label: "已入库", hint: "done · 已写入", stage: "done", state: "todo" },
    {
      key: "rolled_back",
      label: "已回滚",
      hint: "rolled_back",
      stage: "rolled_back",
      state: "todo",
    },
  ];

  const markThrough = (n: number) => {
    for (let i = 0; i < n; i += 1) steps[i].state = "done";
  };

  let failedAt: number | null = null;

  switch (b.status) {
    case "pending":
      steps[0].state = "current";
      break;
    case "parsing":
    case "validating":
      markThrough(1);
      steps[1].state = "current";
      break;
    case "importing":
      markThrough(2);
      steps[2].state = "current";
      break;
    case "failed":
      // 失败发生在执行阶段（校验阶段的错误不改批次状态，只累加 failed_rows）
      markThrough(2);
      steps[2].state = "failed";
      failedAt = 2;
      break;
    case "rolled_back":
      markThrough(5);
      steps[4].state = "current";
      break;
    default: // done
      if (executed) {
        markThrough(4);
        steps[3].state = "current";
      } else {
        markThrough(2);
        steps[1].state = "current";
      }
      break;
  }

  return { steps, failedAt };
}

/** 批次是否还需要用户做点什么（用于列表页给出"下一步"提示）。 */
export function nextActionHint(b: ImportBatch): string {
  if (b.status === "pending") return "尚未校验";
  if (b.status === "failed") return "执行失败，可在详情页重试或重新上传";
  if (b.status === "rolled_back") return "已回滚，如需重导请新建批次";
  if (b.status === "done") {
    if (!isExecuted(b)) {
      return b.failed_rows > 0
        ? `有 ${b.failed_rows} 行未通过校验，执行前需先修正或选择"仅导入通过的行"`
        : "待执行导入";
    }
    return "已入库";
  }
  return "处理中…";
}

// ---------------------------------------------------------------- 统计卡片

export type StatCard = {
  key: string;
  label: string;
  value: number;
  /** 语义色，别让六个数字长得一模一样 */
  tone: "default" | "success" | "warning" | "danger" | "muted";
  hint?: string;
};

/** 校验结果的统计卡片。数字必须**具体**，不能只写"通过"。 */
export function validateStats(
  b: Pick<
    ImportBatch,
    "total_rows" | "success_rows" | "failed_rows" | "duplicate_rows" | "updated_rows"
  >,
): StatCard[] {
  return [
    { key: "total", label: "文件总行数", value: b.total_rows, tone: "default" },
    {
      key: "insert",
      label: "将新增",
      value: b.success_rows,
      tone: "success",
      hint: "库里没有同内容题",
    },
    {
      key: "update",
      label: "将更新",
      value: b.updated_rows,
      tone: "default",
      hint: "upsert 模式命中已有题",
    },
    {
      key: "duplicate",
      label: "将跳过",
      value: b.duplicate_rows,
      tone: "muted",
      hint: "命中重复（insert 模式不改动）",
    },
    {
      key: "failed",
      label: "未通过",
      value: b.failed_rows,
      tone: b.failed_rows > 0 ? "danger" : "muted",
      hint: b.failed_rows > 0 ? "见下方错误报告" : "无",
    },
  ];
}

// ---------------------------------------------------------------- 错误报告 CSV

const CSV_HEADER = ["row_no", "field", "message"] as const;

/**
 * 单个 CSV 单元格转义。
 *
 * 含 `,` / `"` / 换行时必须用双引号包起来，并把内部的 `"` 写成 `""`。
 * 错误信息里逗号与引号都很常见（如 `答案 D 不在选项中（本题选项：A、B、C）`），
 * 不转义的话整张表在 Excel 里会错位。
 */
function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 错误报告 → CSV 文本（**已含 UTF-8 BOM**，Excel 直接双击不乱码）。 */
export function errorsToCsv(
  errors: RowError[],
  opts?: { truncatedAt?: number; totalErrors?: number },
): string {
  const rows = [...errors].sort((a, b) => a.row_no - b.row_no);
  const lines = [CSV_HEADER.join(",")];
  for (const e of rows) {
    lines.push([csvCell(e.row_no), csvCell(e.field), csvCell(e.message)].join(","));
  }
  // 截断必须写进文件里。否则教研拿到 200 行的 CSV，
  // 会以为"只有 200 个错误"，而真实错误数是 totalErrors。
  const total = opts?.totalErrors ?? rows.length;
  const limit = opts?.truncatedAt ?? rows.length;
  if (total > limit) {
    lines.push("");
    lines.push(
      [
        csvCell(""),
        csvCell("# 注意"),
        csvCell(
          `报告已截断：此处 ${rows.length} 条，实际共 ${total} 条错误。请先修正已列出的行后重新校验。`,
        ),
      ].join(","),
    );
  }
  // \r\n：Excel 对 LF-only 的 CSV 兼容性一般
  return `\ufeff${lines.join("\r\n")}\r\n`;
}

/** 错误报告文件名：带上批次号，便于多批对比时区分。 */
export function errorReportFilename(batchNo: string): string {
  const safe = (batchNo || "batch").replace(/[\\/:*?"<>|\s]+/g, "_");
  return `错误报告-${safe}.csv`;
}

/**
 * 触发浏览器下载。
 *
 * 必须 `URL.revokeObjectURL`：不清的话每下载一次就泄漏一个 blob URL，
 * 导几十次之后页面内存会明显涨。
 */
export function downloadTextFile(
  filename: string,
  content: string,
  mime = "text/csv;charset=utf-8",
): void {
  if (typeof window === "undefined") return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 立刻 revoke 在部分浏览器会打断下载，挪到下一帧
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

// ---------------------------------------------------------------- 上传前置校验

/** 单文件上限（与后端 `MAX_FILE_BYTES` 一致，20MB）。 */
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

/**
 * 前端先拦一道"明显不对的文件"。
 *
 * 这不是安全措施（后端才是），目的是**省一次往返**并把话说清楚：
 * 选到 .xlsx 时前端就该告诉用户"另存为 CSV"，而不是传上去等后端报 40001。
 */
export function validateLocalFile(file: { name: string; size: number }): string | null {
  const lower = file.name.toLowerCase();
  if (!/\.(csv|json)$/.test(lower)) {
    if (/\.(xlsx|xls)$/.test(lower)) {
      return "本批不支持 Excel（xlsx/xls）。请在 Excel 里「另存为 → CSV UTF-8（逗号分隔）」后再上传。";
    }
    return "只支持 .csv 与 .json 两种文件。";
  }
  if (file.size === 0) return "文件是空的。";
  if (file.size > MAX_FILE_BYTES) {
    return `文件过大（${(file.size / 1048576).toFixed(1)}MB），上限 ${MAX_FILE_BYTES / 1048576}MB。`;
  }
  return null;
}

/** 可上传的来源类型（与 `lib/question.ts` 的 SOURCE_TYPE_LABELS 同一批 key）。 */
export const IMPORT_SOURCE_TYPES = [
  "self",
  "public",
  "authorized",
  "user_import",
  "ai_assisted",
] as const;
