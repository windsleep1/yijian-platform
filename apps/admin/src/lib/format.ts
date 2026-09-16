/**
 * 展示层格式化工具。
 *
 * 关于手机号脱敏：**本文件刻意不提供 maskPhone()。**
 * 脱敏必须由后端决定（`phone` 永远脱敏 / `phone_full` 按 `user:export` 下发）。
 * 前端"把字符盖住"不是脱敏——明文仍躺在响应体里，F12 一览无余。
 * 在这里放一个 maskPhone 只会诱导后人用它去"修"泄露问题。
 */

/** 全站统一按北京时间渲染。后端存 UTC，浏览器可能在别的时区。 */
const TZ = "Asia/Shanghai";

const dateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** ISO(UTC) → `2026-09-15 15:04:05`（北京时间）。 */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // zh-CN 的 Intl 输出形如 "2026/09/15 15:04:05"，统一成短横线更好读
  return dateTimeFmt.format(d).replace(/\//g, "-");
}

/** 只到分钟，列表里用。 */
export function formatDateMinute(iso?: string | null): string {
  const s = formatDateTime(iso);
  return s === "—" ? s : s.slice(0, 16);
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("zh-CN");
}

// ---------------------------------------------------------------- JSON diff

export type DiffKind = "changed" | "added" | "removed";

export type DiffRow = {
  /** 点号路径，如 `roles[0].code` */
  path: string;
  before: unknown;
  after: unknown;
  kind: DiffKind;
};

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** 把嵌套对象/数组压平成 `path -> 叶子值`。数组按下标展开。 */
export function flattenJson(value: unknown, prefix = "", out: Record<string, unknown> = {}) {
  if (Array.isArray(value)) {
    if (value.length === 0) out[prefix || "(root)"] = [];
    value.forEach((v, i) => flattenJson(v, prefix ? `${prefix}[${i}]` : `[${i}]`, out));
    return out;
  }
  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    if (keys.length === 0) out[prefix || "(root)"] = {};
    for (const k of keys) flattenJson(value[k], prefix ? `${prefix}.${k}` : k, out);
    return out;
  }
  out[prefix || "(root)"] = value;
  return out;
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/**
 * before / after 的字段级 diff。
 *
 * 这是审计抽屉的核心：列表里只看到 `action`，**真正要回答的是"到底改了什么"**。
 * 把嵌套 JSON 拍平成一行行"路径 + 改前 + 改后"，比并排两坨 JSON 好读得多。
 */
export function diffJson(before: unknown, after: unknown): DiffRow[] {
  const b = flattenJson(before);
  const a = flattenJson(after);
  const paths = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).sort();

  const rows: DiffRow[] = [];
  for (const path of paths) {
    const hasB = path in b;
    const hasA = path in a;
    if (hasB && hasA) {
      if (!sameValue(b[path], a[path])) {
        rows.push({ path, before: b[path], after: a[path], kind: "changed" });
      }
    } else if (hasA) {
      rows.push({ path, before: undefined, after: a[path], kind: "added" });
    } else {
      rows.push({ path, before: b[path], after: undefined, kind: "removed" });
    }
  }
  return rows;
}

/** 值的紧凑展示（字符串加引号，便于分清 `"7"` 和 `7`）。 */
export function previewValue(v: unknown): string {
  if (v === undefined) return "—";
  if (v === null) return "null";
  if (typeof v === "string") return v;
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function prettyJson(v: unknown): string {
  if (v === null || v === undefined) return "null";
  return JSON.stringify(v, null, 2);
}

// ---------------------------------------------------------------- 时间筛选

/**
 * `<input type="datetime-local">` 的值（本地时间，无时区）→ ISO UTC 字符串。
 *
 * 坑：`new Date("2026-09-15T10:00")` 在浏览器里按**本地时区**解释，
 * 而我们要的是"用户输入的北京时间对应的时间点"。所以显式补上 +08:00。
 * 少了这一步，筛选范围会整体偏移 8 小时（看起来"筛了今天却查出昨天"）。
 */
export function localInputToIsoUtc(local: string): string | undefined {
  if (!local) return undefined;
  const withTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(local) ? local : `${local}:00+08:00`;
  const d = new Date(withTz);
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString();
}
