/**
 * 本地 API —— 把 **C 端那套 REST 形状**架在 IndexedDB 上（个人 PWA 的数据层）。
 *
 * ## 为什么是这个形状（而不是"每个页面直接调 db"）
 *
 * C 端的页面是**照着 `request<T>(path, opts)` 写**的。PWA 要复用那些页面，
 * 有两条路：
 *
 *   A. **每个页面各自改成 `db.*`** —— 12 个页面各改一遍 ⇒ 复制品全部与 C 端分叉，
 *      而"下游悄悄落后"正是本项目最怕的那种缺陷（在 C 端修了、PWA 没修，**不报错**）。
 *   B. **保持 `request` 这个面**，把实现换成 IndexedDB（本文件）——
 *      ⇒ 页面**逐字节相同**，需要人工维护分叉的只剩**这一个文件**。
 *
 * 选 B。代价是这个文件要"演一遍后端"，而它演得对不对可以用**两侧对拍**验
 *（判分：`tools/pwa/parity.py`；其余见 `docs/32` §8）。
 *
 * ## 与 C 端 `lib/api.ts` 的**根本差别**（两处，就这两处）
 * | | C 端 | 本文件 |
 * |---|---|---|
 * | 数据源 | HTTP（`fetch`）到 `/api/v1` | **IndexedDB** |
 * | 认证 | token / 401 刷新 / 单飞 | **没有** —— 单机单用户（这就是"零成本"的代价） |
 *
 * ★ 判据（这条支撑着"逐字节复制"的合法性）：**本文件里不许出现 `fetch(`**。
 *   `COPIED-FROM-WEB.md` 的门禁会 grep 它。
 *
 * ## 错误码与后端**同值**
 * `40401` 不存在 · `40901` 状态冲突 · `40001` 请求不合法 · `50001` 兜底。
 * 前端那些 `e.code === 40401` 的分支因此不用分两套写法。
 */

import { gradeAnswer, LocalBadRequest, normalizeUserValue } from "./grade.mjs";
import {
  counts,
  idbAll,
  idbDelete,
  idbGet,
  idbPut,
  idbPutMany,
  metaGet,
  metaSet,
  openDb,
  STORES,
  todayInShanghai,
  tx,
  wipeAll,
  type StoreName,
} from "./db";
import type {
  AnswerResult,
  Chapter,
  CollectionItem,
  CollectionList,
  FlagState,
  KpStat,
  Note,
  NoteList,
  NoteListOfQuestion,
  PracticeSession,
  SessionItem,
  SessionReport,
  SessionProgress,
  Subject,
  WrongDetail,
  WrongItem,
  WrongList,
  WrongSubject,
} from "./types";

export type QueryValue = string | number | boolean | undefined | null;
export type QueryParams = Record<string, unknown>;

export type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
  /** 与 C 端同名的占位参数：本层没有重试语义，留着是为了**页面逐字节可复制**。 */
  _retried?: boolean;
};

/** 与 C 端 `ApiError` 同形。★ `traceId` 在单机里没有来源，恒为 `""` —— 保留字段是为页面可复制。 */
export class ApiError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly traceId: string,
    readonly httpStatus: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isAuthRedirect(): boolean {
    return false;
  }
}

/** 本层把错误码**冻结在函数里**：单机环境没有"和别处保持一致"的问题，
 *  但保留一个可覆盖的参数，是为了让调用处读起来与后端一致（`not_found(msg, code)`）。 */
function notFound(msg: string, code = 40401): ApiError {
  return new ApiError(code, msg, "", 404);
}
function conflict(msg: string, code = 40901): ApiError {
  return new ApiError(code, msg, "", 409);
}

/* ============================================================ 行的形状（store 里存什么） */

/** 题库包里的元信息（`meta` store 的 `bank` 键）。 */
export type BankMeta = {
  schema_version: number;
  bank_version: string;
  exported_at: string;
  totals: { questions: number; options: number; knowledge_points: number };
  by_subject: Record<string, number>;
  by_type: Record<string, number>;
  by_difficulty: Record<string, number>;
  /** ★★ 评分规则**随包下发** —— 判分不许自带字面量（见 `grade.mjs` 抬头）。 */
  grading_rules: { partial_credit_ratio: number; gradable_types: string[] };
};

type QuestionRow = {
  id: string;
  subject_id: string;
  chapter_id: string | null;
  knowledge_point_id: string | null;
  type: string;
  stem: string;
  stem_html: string | null;
  answer: { value: unknown[]; partial_credit?: boolean };
  analysis: string | null;
  analysis_html: string | null;
  options: { label: string; content: string; content_html: string | null }[];
  score_default: number;
  difficulty: number;
  status: string;
};

/**
 * 章节在 store 里的**真实形状**：比接口出参多两个字段。
 * ★ `path` 是"含子章节"的判断依据（与后端 `c.path LIKE :cprefix` 同口径）；
 *   `subject_id` 是"这门科目有哪些章节"的依据。两者都**不出现在出参里**
 *   （C 端的 `ChapterOut` 也没有它们），所以这里是本层自己的行类型。
 */
type ChapterRow = Chapter & { subject_id: string; path: string };

type ItemRow = {
  item_id: string;
  session_id: string;
  seq: number;
  question_id: string;
  /** `null` = 还没答（**幂等与"要不要揭示答案"都看它**）。 */
  answered_at: string | null;
  my_value: unknown[] | null;
  is_correct: boolean | null;
  score: number | null;
};

type SessionRow = {
  id: string;
  mode: string;
  status: string;
  subject_id: string | null;
  chapter_id: string | null;
  title: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  answered: number;
  correct: number;
  score: number;
};

/** 本机单用户 ⇒ **不需要雪花 ID**（那套是为了分布式唯一）。前缀只为肉眼可辨。 */
function newId(prefix: string): string {
  const r = crypto.getRandomValues(new Uint32Array(2));
  return `${prefix}_${r[0].toString(36)}${r[1].toString(36)}${Date.now().toString(36)}`;
}

const nowIso = () => new Date().toISOString();

/** 读包里的评分规则。**读不到就抛**（与 `grade.mjs` 同一态度：不默认、不兜底）。 */
async function gradingRules() {
  const bank = await metaGet<BankMeta>("bank");
  if (bank === null) {
    throw notFound("还没有导入题库。请先到「导入题库」页面导入 pwa-bank.json。");
  }
  return bank.grading_rules;
}

/* ============================================================ id 归一（**唯一入口**） */

/**
 * 把**跨边界**的 id 立即归一成字符串 —— 全应用唯一的写法。
 *
 * ★ 为什么必须有它（2026-10-07 实测踩到；症状是"**功能没了，但不报错**"）：
 *   包里（JSON）的 id 是**数字**，而 URL 段 / 查询串 / 路由参数给的全是**字符串**
 *   ⇒ `c.subject_id === sid` 这类**严格比较恒为假** ⇒ 章节列表恒空
 *     （页面显示"这门科目还没有章节" —— 6 门科目一个章节都点不动）。
 *   同族：`idbGet("questions", qid)` 拿字符串 key 去查数字主键 ⇒ 查不到 ⇒ 标记 / 收藏全 `40401`。
 *   ⇒ 对策**不是**在每个比较点补 `String()`（那是打地鼠），而是**在边界上统一形状**
 *     —— 正是本项目的不变量 8「**一个字段只有一种写法**」。
 */
export function asId(v: string | number): string {
  return String(v);
}

/**
 * 把一行里**所有 id 字段**归一成字符串 —— **按名字认，不逐个列字段**。
 *
 * 判据：键名是 `id`，或**以 `_id` 结尾**（`subject_id` / `chapter_id` / `knowledge_point_id` …）。
 * ★ 为什么不写一张"哪些字段是 id"的清单：**清单会漏**，而漏掉的那一项**不报错** ——
 *   它只是又悄悄变回数字，等下一次"某个列表是空的"再冒出来。
 * ★ 只动 `string | number`；`null` / 对象 / 数组**原样留着**（`null` 不许变成 `"null"`）。
 */
function normalizeIds(row: unknown): unknown {
  if (row === null || typeof row !== "object" || Array.isArray(row)) return row;
  const src = row as Record<string, unknown>;
  const out: Record<string, unknown> = { ...src };
  for (const [k, v] of Object.entries(src)) {
    if (k !== "id" && !k.endsWith("_id")) continue;
    if (typeof v === "string" || typeof v === "number") out[k] = String(v);
  }
  return out;
}

/* ============================================================ 题库导入（本模块的核心之一） */

export type ImportProgress = {
  phase: "reading" | "clearing" | "writing" | "verifying" | "done";
  /** 已写入的行数 / 预计总行数（用于进度条）。 */
  written: number;
  total: number;
  note?: string;
};

/** 与 `tools/pwa/export-bank.py` 约定的包版本。不认识的版本**当场拒绝**。 */
export const BANK_SCHEMA_VERSION = 1;

export type ImportResult = {
  bank_version: string;
  questions: number;
  options: number;
};

export type BankSummary = {
  bank_version: string;
  exported_at: string;
  questions: number;
  options: number;
  knowledge_points: number;
  /** ★ 科目数（= `meta.by_subject` 的键数）—— 「题库信息」卡要显示它。
   *  `meta.totals` 里**没有**这一项（它只有 questions / options / knowledge_points）。 */
  subjects: number;
};

/** 当前已导入的题库摘要；没导入过返 `null`。 */
export async function bankSummary(): Promise<BankSummary | null> {
  const bank = await metaGet<BankMeta>("bank");
  if (bank === null) return null;
  return {
    bank_version: bank.bank_version,
    exported_at: bank.exported_at,
    questions: bank.totals.questions,
    options: bank.totals.options,
    knowledge_points: bank.totals.knowledge_points,
    subjects: Object.keys(bank.by_subject ?? {}).length,
  };
}

/**
 * 只读文件的**元信息**，不写库。
 *
 * ★ 为什么需要它（这是换题库那条流程的关键）：`importBank` 一进去就清库，
 *   而"要不要清"的**判断必须在清之前做**。没有这个函数，页面就只能
 *   "先导入再说"—— 那等于**没有二次确认**（方案 §4.1 的第一条硬要求）。
 *
 * @throws ApiError 文件不是 JSON / 不是题库包 / 版本不认识
 */
export async function peekBankMeta(file: File): Promise<BankMeta> {
  let pkg: { meta?: BankMeta };
  try {
    pkg = JSON.parse(await file.text());
  } catch {
    throw new ApiError(40001, "这个文件不是 JSON，或者已经损坏。", "", 400);
  }
  const meta = pkg?.meta;
  if (!meta || typeof meta.schema_version !== "number") {
    throw new ApiError(40001, "文件里没有题库元信息（meta），像是别的 JSON。", "", 400);
  }
  if (meta.schema_version !== BANK_SCHEMA_VERSION) {
    throw new ApiError(
      40001,
      `题库包的版本是 ${meta.schema_version}，本应用只认识 ${BANK_SCHEMA_VERSION}。` +
        "请用当前版本的 tools/pwa/export-bank.py 重新导出。",
      "",
      400,
    );
  }
  return meta;
}

/** 导入时"写的顺序"—— `questions` 最大，放最后，好让进度条先动起来。 */
const IMPORT_ORDER: StoreName[] = ["subjects", "chapters", "kps", "questions"];

/** 每批写多少行。500 是"进度条够细"与"事务不太长"之间的折中。 */
const IMPORT_CHUNK = 500;

/**
 * 导入题库包。
 *
 * ★★ **可中断重入**（方案 §4 约束 1）：`meta.importing` 是一个**哨兵**。
 *   写数据**前**置上、全部完成（含对账）**后**才清。
 *   启动时若见到它还在 ⇒ 说明上次导到一半被关了 ⇒ **先整库清空再重来**。
 *   为什么必须整库清空而不是"接着写"：接着写会得到**半份 + 半份**，
 *   而两份的交界处是**混的**（题目来自新旧两个包），症状是"某些题搜不到"、**不报错**。
 *
 * @param file      用户的 `pwa-bank.json`
 * @param onProgress 进度回调（可选）
 * @param force     已导入过题库时，是否确认"清空并替换"（方案 §4.1）
 */
export async function importBank(
  file: File,
  onProgress?: (p: ImportProgress) => void,
  force = false,
): Promise<ImportResult> {
  const say = (phase: ImportProgress["phase"], written: number, total: number, note?: string) =>
    onProgress?.({ phase, written, total, note });

  say("reading", 0, 1);
  const text = await file.text();
  let pkg: {
    meta: BankMeta;
    subjects: unknown[];
    chapters: unknown[];
    kps: unknown[];
    questions: unknown[];
  };
  try {
    pkg = JSON.parse(text);
  } catch {
    throw new ApiError(40001, "这个文件不是 JSON，或者已经损坏。", "", 400);
  }

  const meta = pkg?.meta;
  if (!meta || typeof meta.schema_version !== "number") {
    throw new ApiError(40001, "文件里没有题库元信息（meta），像是别的 JSON。", "", 400);
  }
  if (meta.schema_version !== BANK_SCHEMA_VERSION) {
    throw new ApiError(
      40001,
      `题库包的版本是 ${meta.schema_version}，本应用只认识 ${BANK_SCHEMA_VERSION}。` +
        "请用当前版本的 tools/pwa/export-bank.py 重新导出。",
      "",
      400,
    );
  }

  const existing = await metaGet<BankMeta>("bank");
  if (existing !== null && !force) {
    // 已经导过 ⇒ 调用方必须先确认「清空并替换」（二次确认在 /setup 里）。
    throw new ApiError(
      40901,
      `当前已有题库（${existing.bank_version.slice(0, 8)}）。换题库会清空全部数据，请先确认。`,
      "",
      409,
    );
  }

  // ① 清空（含上次可能残留的半份）
  say("clearing", 0, 1);
  await wipeAll();

  // ② 哨兵：现在起，任何中断都会被下次启动识别为"导到一半"
  await metaSet("importing", { started_at: nowIso(), bank_version: meta.bank_version });

  // ★★ **id 在这一行归一成字符串**（`normalizeIds`）—— 这是"包 → 库"的**唯一入口**。
  //    库里从此只有一种写法，URL / 查询串那种字符串 id 才比得上（见文件头的 `asId`）。
  const payload: Record<string, unknown[]> = {
    subjects: (pkg.subjects ?? []).map(normalizeIds),
    chapters: (pkg.chapters ?? []).map(normalizeIds),
    kps: (pkg.kps ?? []).map(normalizeIds),
    questions: (pkg.questions ?? []).map(normalizeIds),
  };
  const totalRows = IMPORT_ORDER.reduce((n, s) => n + (payload[s]?.length ?? 0), 0);
  let written = 0;

  // ③ 分批写（**每个 store 一个事务**；任一批失败 ⇒ 抛错 ⇒ 哨兵还在 ⇒ 下次整库重来）
  say("writing", 0, totalRows);
  for (const store of IMPORT_ORDER) {
    const rows = payload[store] ?? [];
    for (let i = 0; i < rows.length; i += IMPORT_CHUNK) {
      await idbPutMany(store, rows.slice(i, i + IMPORT_CHUNK));
      written += Math.min(IMPORT_CHUNK, rows.length - i);
      say("writing", written, totalRows);
    }
  }

  // ④ 对账 —— **必做，且要能证伪**（方案 §8 判据 2）
  say("verifying", written, totalRows);
  const after = await counts();
  const mismatch = await reconcile(meta, after);
  if (mismatch !== null) {
    // 不进主界面、并说清**差在哪一项**（"导入失败"四个字帮不了任何人）
    throw new ApiError(40001, `导入对账没通过：${mismatch}`, "", 400);
  }

  // ⑤ 对账通过才落 meta —— 顺序不能反：
  //    先落 meta 再对账的话，一个失败的导入会留下"看起来已导入"的状态
  await metaSet("bank", meta);
  await metaSet("importing", null);
  say("done", totalRows, totalRows);
  return {
    bank_version: meta.bank_version,
    questions: meta.totals.questions,
    options: meta.totals.options,
  };
}

/**
 * 对账：包里的 `meta.totals` 与**库里真实的行数**逐项比。一致返 `null`，否则一句话说清。
 * ★ 逐 store 比而不是只比总数 —— 只比总数的话"题少了 3 道、选项多了 3 条"能互相抵消。
 */
async function reconcile(
  meta: BankMeta,
  actual: Record<StoreName, number>,
): Promise<string | null> {
  const parts: string[] = [];
  if (actual.questions !== meta.totals.questions) {
    parts.push(`题目 ${actual.questions} ≠ ${meta.totals.questions}`);
  }
  const opts = await countImportedOptions();
  if (opts !== meta.totals.options) parts.push(`选项 ${opts} ≠ ${meta.totals.options}`);
  if (actual.kps !== meta.totals.knowledge_points) {
    parts.push(`知识点 ${actual.kps} ≠ ${meta.totals.knowledge_points}`);
  }
  // 科目分布：包里的 `by_subject` 是**科目码 → 题数**，与库里的按科目计数比
  const perSubject = await questionCountBySubject();
  for (const [code, n] of Object.entries(meta.by_subject)) {
    const got = perSubject[code] ?? 0;
    if (got !== n) parts.push(`${code} ${got} ≠ ${n}`);
  }
  return parts.length === 0 ? null : parts.join("；");
}

async function countImportedOptions(): Promise<number> {
  const rows = await idbAll<QuestionRow>("questions");
  return rows.reduce((n, q) => n + (q.options?.length ?? 0), 0);
}

async function questionCountBySubject(): Promise<Record<string, number>> {
  const [qs, subs] = await Promise.all([
    idbAll<QuestionRow>("questions"),
    idbAll<Subject>("subjects"),
  ]);
  const codeById = new Map(subs.map((s) => [s.id, s.code]));
  const out: Record<string, number> = {};
  for (const q of qs) {
    const code = codeById.get(q.subject_id);
    if (code) out[code] = (out[code] ?? 0) + 1;
  }
  return out;
}

/**
 * 启动自检：上次是不是导到一半就关了（方案 §4 约束 1 的落地处）。
 * 返回 `true` 表示**这次做了一次整库清空**（调用方据此提示用户"上次没导完，请重新导入"）。
 */
export async function recoverInterruptedImport(): Promise<boolean> {
  const flag = await metaGet<unknown>("importing");
  if (flag === null || flag === undefined) return false;
  await wipeAll();
  return true;
}

/* ============================================================ 路由表 */

type Handler = (m: {
  m: string;
  seg: string[];
  query: URLSearchParams;
  body: unknown;
}) => Promise<unknown>;

/** `:name` 段 → 捕获。只在**路径段**级别匹配，不做正则黑魔法。 */
function route(pattern: string, handler: Handler) {
  return { seg: pattern.split("/").filter(Boolean), handler };
}

/**
 * ★★ 路由表：**凡是把 URL 段当 id 用的地方，一律 `asId(...)`**。
 *
 * 这不是装饰 —— 它就是"跨边界的 id 立即归一"这条规则**在代码里的落点**，
 * 而且**可被门禁 grep**（不变量 14：`seg[n]` / `query.get("<…id>")` 必须出现在 `asId(` 里）。
 * 少了它，新增一条路由时没人会想起"id 的形状"这件事（`listChapters` 就是这么坏的）。
 */
const ROUTES = [
  route("subjects", () => listSubjects()),
  route("subjects/:sid/chapters", ({ seg }) => listChapters(asId(seg[1]))),

  route("practice/sessions", ({ m, body }) => {
    if (m !== "POST") throw notFound("不支持的方法");
    return createSession(
      body as {
        mode?: string;
        subject_id?: string;
        chapter_id?: string;
        question_ids?: string[];
        count?: number;
      },
    );
  }),
  route("practice/sessions/:sid", ({ seg }) => getSession(asId(seg[2]))),
  route("practice/sessions/:sid/answer", ({ seg, body }) =>
    submitAnswer(asId(seg[2]), body as { item_id?: string; value?: unknown[] }),
  ),
  route("practice/sessions/:sid/finish", ({ seg }) => finishSession(asId(seg[2]))),
  route("practice/sessions/:sid/report", ({ seg }) => sessionReport(asId(seg[2]))),

  route("practice/wrong-questions", ({ query }) => listWrong(query)),
  route("practice/wrong-questions/:qid", ({ seg }) => wrongDetail(asId(seg[2]))),

  route("practice/marks/:qid", ({ m, seg }) => toggleFlag(m, "marks", asId(seg[2]))),
  route("practice/favorites/:qid", ({ m, seg }) => toggleFlag(m, "favorites", asId(seg[2]))),
  route("practice/favorites", ({ query }) => listCollections(query)),

  route("practice/questions/:qid/notes", ({ m, seg, body }) =>
    m === "POST"
      ? addNote(asId(seg[2]), body as { content?: unknown })
      : listNotesOfQuestion(asId(seg[2])),
  ),
  route("practice/notes/:nid", ({ m, seg, body }) => {
    if (m === "PUT") return editNote(asId(seg[2]), body as { content?: unknown });
    if (m === "DELETE") return removeNote(asId(seg[2]));
    throw notFound("不支持的方法");
  }),
  route("practice/notes", ({ query }) => listNotes(query)),
];

/**
 * 发起一次"请求"。**签名与 C 端逐字一致** —— 这是页面能逐字节复制的前提。
 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const m = opts.method ?? "GET";
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    qs.set(k, String(v));
  }
  const [pathOnly, inlineQuery = ""] = path.split("?");
  const seg = pathOnly.split("/").filter(Boolean);
  const query = new URLSearchParams(inlineQuery);
  for (const [k, v] of qs) query.set(k, v);

  for (const r of ROUTES) {
    if (r.seg.length !== seg.length) continue;
    if (!r.seg.every((p, i) => p.startsWith(":") || p === seg[i])) continue;
    try {
      // `await` 把同步抛出的异常也收进 catch —— 否则 `LocalBadRequest` 会漏成未处理拒绝
      return (await r.handler({ m, seg, query, body: opts.body })) as T;
    } catch (e) {
      if (e instanceof ApiError) throw e;
      if (e instanceof LocalBadRequest) {
        throw new ApiError(e.code, e.message, "", 400);
      }
      throw e;
    }
  }
  // ★ 出声：本层没实现的接口**点名报**，不静默返回空数组（那会被误当成"没有数据"）
  throw new ApiError(50001, `本地数据层没有实现这个接口：${m} ${path}`, "", 501);
}

/* ============================================================ 科目 / 章节 */

async function listSubjects(): Promise<Subject[]> {
  const rows = await idbAll<Subject>("subjects");
  return rows.slice().sort((a, b) => a.sort_no - b.sort_no || (a.id < b.id ? -1 : 1));
}

/**
 * 章节 + **实时题数**。
 * ★ C 端这三个数是后端算的（含子章节）；这里同样**现算**，不读那个"定时刷新"的冗余列
 *   （项目里那条冗余列根本没人刷 —— 照抄它就会显示错的题数）。
 */
async function listChapters(sid: string): Promise<Chapter[]> {
  const [chapters, questions] = await Promise.all([
    idbAll<ChapterRow>("chapters"),
    idbAll<QuestionRow>("questions"),
  ]);
  const mine = chapters.filter((c) => c.subject_id === sid);
  const pathById = new Map(chapters.map((c) => [c.id, c.path]));
  const pathOf = (cid: string) => pathById.get(cid) ?? null;
  // ★ "这个章节（**含子章节**）里有哪些题" —— 与后端 `c.path LIKE :cprefix` 同口径。
  //   用 path 前缀而不是 parent_id 递归：一次比较代替一次遍历。
  const inChapter = (q: QuestionRow, path: string) =>
    q.chapter_id !== null && (pathOf(q.chapter_id) ?? "").startsWith(path);
  const done = await answeredQuestionIds();
  return mine
    .map((c) => {
      const qs = questions.filter((q) => inChapter(q, c.path));
      const answered = qs.filter((q) => done.has(q.id)).length;
      return {
        ...c,
        question_count: qs.length,
        my_answered: answered,
        // 正确数这里**不猜**：`user_question_state` 那套跨练习的"对了几次"在本层没有对应物，
        // 而"做题数"有（items 里答过）。宁可少一个数，也不要一个凭印象的 0。
        my_correct: 0,
      };
    })
    .sort((a, b) => a.sort_no - b.sort_no || (a.id < b.id ? -1 : 1));
}

/** `wrong` store 的行（比出参多几个**只在本层用**的字段）。 */
type WrongRow = {
  question_id: string;
  subject_id: string | null;
  chapter_id: string | null;
  wrong_count: number;
  retry_correct: number;
  mastered_level: number;
  last_wrong_at: string;
  /** ★ 约定 T 的对应物：软删标记（后端 `wrong_questions.is_removed`）。 */
  removed_at: string | null;
  created_at: string;
  updated_at: string;
};

/**
 * 抽题：**错题重练**的题源（`mode='wrong'`）。
 *
 * ★ 与后端 `_WRONG_PICK_HEAD` / `_WRONG_PICK_TAIL` **逐条对应**（复制品，来源写在这儿）：
 *
 *     w.user_id = :uid                  → 本地库只有"我"⇒ 无对应物（§1.1 的机械判据）
 *     w.is_removed = false              → `removed_at === null`
 *     JOIN questions q                  → 题不在本地题库里 ⇒ 抽不到（约定 T：行还在，但点不开）
 *     q.status = 'published'            → 同上
 *     q.type IN (可判分题型)             → `rules.gradable_types.includes(q.type)`
 *     (:sid IS NULL OR w.subject_id = :sid) → `opts.subjectId === null ||`
 *     ORDER BY last_wrong_at DESC, question_id → 同一句比较
 *
 * ★ 指定题时**走同一条判定**（后端也只是多一条 `AND w.question_id IN :qids`）⇒
 *   "别人的错题 / 没错过的题 / 下架的题"**即使显式传 id 也抽不到** ——
 *   表现一致，也就**不泄露"这个 id 存不存在"**。
 */
async function pickWrongQuestions(opts: {
  subjectId: string | null;
  questionIds: string[] | null;
  count: number;
}): Promise<string[]> {
  const [rows, questions, rules] = await Promise.all([
    idbAll<WrongRow>("wrong"),
    idbAll<QuestionRow>("questions"),
    gradingRules(),
  ]);
  const qById = new Map(questions.map((q) => [q.id, q]));
  const wanted = opts.questionIds === null ? null : new Set(opts.questionIds);
  return rows
    .filter((r) => {
      if (r.removed_at !== null) return false;
      if (opts.subjectId !== null && r.subject_id !== opts.subjectId) return false;
      if (wanted !== null && !wanted.has(r.question_id)) return false;
      const q = qById.get(r.question_id);
      if (!q) return false;
      if (q.status !== "published") return false;
      return rules.gradable_types.includes(q.type);
    })
    .sort(
      (a, b) =>
        (a.last_wrong_at < b.last_wrong_at ? 1 : -1) || (a.question_id < b.question_id ? -1 : 1),
    )
    .slice(0, opts.count)
    .map((r) => r.question_id);
}

/** 「我做过的题」集合 —— 抽题时排在后面（与后端 `_PICK_SQL` 同口径）。 */
async function answeredQuestionIds(): Promise<Set<string>> {
  const items = await idbAll<ItemRow>("items");
  return new Set(items.filter((i) => i.answered_at !== null).map((i) => i.question_id));
}

/* ============================================================ 建练习 / 取练习 */

async function createSession(body: {
  mode?: string;
  subject_id?: string;
  chapter_id?: string;
  question_ids?: string[];
  count?: number;
}): Promise<{ id: string }> {
  const mode = body.mode ?? "chapter";
  if (mode !== "chapter" && mode !== "wrong") {
    throw new ApiError(40001, 'mode 只能是 "chapter" 或 "wrong"', "", 400);
  }
  // ★★ 必填项**按 mode 分叉** —— 与后端 `SessionCreateIn._check` 同口径。
  //   写成"进来先判"而不是散在两段里：调用方传错时**第一句话就指出是哪个参数的问题**。
  if (mode === "chapter" && !body.subject_id) {
    throw new ApiError(40001, "请先选科目", "", 400);
  }
  if (mode !== "wrong" && body.question_ids && body.question_ids.length > 0) {
    throw new ApiError(40001, "question_ids 只在 mode='wrong' 下有意义", "", 400);
  }
  const count = Math.max(1, Math.min(Number(body.count) || 10, 100));
  const rules = await gradingRules();

  // 题源与"会话标题 / subject_id"三件事**按 mode 一起定**（后端的写法也是这样的：
  // 一个 if 里同时产出 `picked` / `title` / `chapter_id = None`）。
  let chosen: string[];
  let title: string;
  let sessionSubjectId: string | null;
  let sessionChapterId: string | null;

  if (mode === "wrong") {
    chosen = await pickWrongQuestions({
      subjectId: body.subject_id ?? null,
      questionIds: body.question_ids && body.question_ids.length > 0 ? body.question_ids : null,
      count,
    });
    if (chosen.length === 0) {
      // ★ 与后端**同一句话**：空错题本（= 筛选后为空）是 `40401`，
      //   而它**不是故障** —— 所以文案必须带"怎么才能有题"。
      throw notFound(
        "错题本里还没有可重练的题 —— 这里只放**你自己答错过**的题，先去练习里做几道。",
        40401,
      );
    }
    title = "错题重练";
    // ★ 跨科目重练是**合法场景**（`question_ids` 可以横跨科目）⇒ 会话的 subject_id 允许为 NULL。
    //   后端同（`practice_sessions.subject_id` 可空）。
    sessionSubjectId = body.subject_id ?? null;
    sessionChapterId = null;
  } else {
    const sid = body.subject_id;
    if (sid === undefined) throw new ApiError(40001, "请先选科目", "", 400);
    const [all, chapters, subjects, done] = await Promise.all([
      idbAll<QuestionRow>("questions"),
      idbAll<ChapterRow>("chapters"),
      idbAll<Subject>("subjects"),
      answeredQuestionIds(),
    ]);
    const chapterPath = body.chapter_id
      ? (chapters.find((c) => c.id === body.chapter_id)?.path ?? null)
      : null;
    const inScope = (q: QuestionRow) => {
      if (q.subject_id !== sid) return false;
      if (q.status !== "published") return false;
      if (!rules.gradable_types.includes(q.type)) return false;
      if (chapterPath === null) return true;
      return (
        (q.chapter_id &&
          (chapters.find((c) => c.id === q.chapter_id)?.path ?? "").startsWith(chapterPath)) ===
        true
      );
    };
    const pool = all.filter(inScope);
    // 排序：**没做过的在前**、同组按 id —— 与后端 `ORDER BY (uqs.id IS NOT NULL), q.id` 同口径。
    // ★ 刻意**不随机**：C 端也是确定的。随机会让"同一章两次练习抽到的题不同"，
    //   而 E2E 与排障都依赖"同一输入 → 同一题序"。
    pool.sort((a, b) => {
      const da = done.has(a.id) ? 1 : 0;
      const db2 = done.has(b.id) ? 1 : 0;
      return da - db2 || (a.id < b.id ? -1 : 1);
    });
    chosen = pool.slice(0, count).map((q) => q.id);
    if (chosen.length === 0) {
      throw notFound("这个章节里还没有可练习的题。", 40401);
    }
    const chapterName = body.chapter_id
      ? (chapters.find((c) => c.id === body.chapter_id)?.name ?? null)
      : null;
    const subjectName = subjects.find((s) => s.id === sid)?.name ?? null;
    title = chapterName ? `${chapterName} · 练习` : `${subjectName ?? "练习"}`;
    sessionSubjectId = sid;
    sessionChapterId = body.chapter_id ?? null;
  }

  const session: SessionRow = {
    id: newId("s"),
    mode,
    status: "doing",
    subject_id: sessionSubjectId,
    chapter_id: sessionChapterId,
    title,
    started_at: nowIso(),
    finished_at: null,
    total: chosen.length,
    answered: 0,
    correct: 0,
    score: 0,
  };
  const items: ItemRow[] = chosen.map((qid, i) => ({
    item_id: newId("i"),
    session_id: session.id,
    seq: i + 1,
    question_id: qid,
    answered_at: null,
    my_value: null,
    is_correct: null,
    score: null,
  }));
  await tx(["sessions", "items"], "readwrite", (g) => {
    g("sessions").put(session);
    for (const it of items) g("items").put(it);
  });
  return { id: session.id };
}

/** `notes` 在 store 里的真实形状：比接口出参多两个**只在本层用**的字段。 */
type NoteRow = Note & {
  /** 所属科目（列表页按科目筛选要用；接口出参里是从题目现算的）。 */
  subject_id?: string | null;
  /** ★ **软删时间戳**（不是 `is_deleted` 布尔）。每条读路径都要带它（红线 3）。 */
  deleted_at?: string | null;
};

/** 揭示答案的门槛 —— **与后端 `_may_reveal` 同口径**：答过 **或** 整场已结束。 */
function mayReveal(sessionStatus: string, answered: boolean): boolean {
  return answered || sessionStatus === "finished";
}

async function buildItems(session: SessionRow): Promise<SessionItem[]> {
  const [items, questions, marks, favs, notes] = await Promise.all([
    idbAll<ItemRow>("items"),
    idbAll<QuestionRow>("questions"),
    idbAll<{ question_id: string }>("marks"),
    idbAll<{ question_id: string }>("favorites"),
    idbAll<NoteRow>("notes"),
  ]);
  const qById = new Map(questions.map((q) => [q.id, q]));
  const markSet = new Set(marks.map((x) => x.question_id));
  const favSet = new Set(favs.map((x) => x.question_id));
  const noteCount = new Map<string, number>();
  for (const n of notes) {
    if (n.deleted_at) continue; // ★ 软删过滤（红线 3）
    noteCount.set(n.question_id, (noteCount.get(n.question_id) ?? 0) + 1);
  }
  return items
    .filter((i) => i.session_id === session.id)
    .sort((a, b) => a.seq - b.seq)
    .flatMap((i) => {
      const q = qById.get(i.question_id);
      if (!q) return []; // 题库换过 ⇒ 指向不存在的题。**不静默计数，直接不出现**
      const answered = i.answered_at !== null;
      const reveal = mayReveal(session.status, answered);
      const out: SessionItem = {
        item_id: i.item_id,
        seq: i.seq,
        question_id: q.id,
        type: q.type,
        stem: q.stem,
        stem_html: q.stem_html,
        // ★ **答题前不发选项的正确答案**：`options` 本来就不含 `is_correct`（导出时就剥了）
        options: q.options,
        my_value: i.my_value,
        is_correct: answered ? (i.is_correct ?? false) : null,
        score: answered ? (i.score ?? 0) : null,
        answered,
        marked: markSet.has(q.id),
        favorited: favSet.has(q.id),
        // ⚠️ 未作答时**恒为 null**（页面据此显示"未揭示"），不要兜底成 `{}`
        answer: reveal ? { value: q.answer.value } : null,
        analysis: reveal ? q.analysis : null,
        analysis_html: reveal ? q.analysis_html : null,
        note_count: noteCount.get(q.id) ?? 0,
      };
      return [out];
    });
}

async function getSession(sid: string): Promise<PracticeSession> {
  const s = await idbGet<SessionRow>("sessions", sid);
  if (!s) throw notFound("练习不存在", 40401);
  const [chapters, subjects] = await Promise.all([
    idbAll<ChapterRow>("chapters"),
    idbAll<Subject>("subjects"),
  ]);
  const items = await buildItems(s);
  // 落点 = **第一道还没作答的题**（断点恢复的语义；前端不自己算）
  const current = items.find((x) => !x.answered)?.item_id ?? null;
  return {
    id: s.id,
    mode: s.mode,
    status: s.status,
    subject_id: s.subject_id,
    subject_name: subjects.find((x) => x.id === s.subject_id)?.name ?? null,
    chapter_id: s.chapter_id,
    chapter_name: chapters.find((x) => x.id === s.chapter_id)?.name ?? null,
    title: s.title,
    current_item_id: current,
    total: s.total,
    answered: s.answered,
    correct: s.correct,
    score: s.score,
    items,
  };
}

function progressOf(s: SessionRow): SessionProgress {
  return { total: s.total, answered: s.answered, correct: s.correct, score: s.score };
}

async function submitAnswer(
  sid: string,
  body: { item_id?: string; value?: unknown[] },
): Promise<AnswerResult> {
  const s = await idbGet<SessionRow>("sessions", sid);
  if (!s) throw notFound("练习不存在", 40401);
  if (s.status !== "doing") throw conflict("这次练习已经结束了，不能再提交答案", 40901);

  const item = await idbGet<ItemRow>("items", String(body.item_id));
  if (!item || item.session_id !== sid) throw notFound("这道题不在本次练习里", 40401);
  const q = await idbGet<QuestionRow>("questions", item.question_id);
  if (!q) throw notFound("这道题已经从题库里消失了", 40401);

  const rules = await gradingRules();

  // ---- 幂等分支：已作答 ⇒ **零写入**（硬约定 C：判据 = "目标状态已达成"）----
  if (item.answered_at !== null) {
    return {
      item_id: item.item_id,
      is_correct: item.is_correct ?? false,
      score: item.score ?? 0,
      correct_answer: { value: q.answer.value },
      my_value: item.my_value ?? [],
      analysis: q.analysis,
      analysis_html: q.analysis_html,
      idempotent: true,
      session: progressOf(s),
    };
  }

  const userValue = normalizeUserValue(q.type, body.value ?? []);
  const { isCorrect, ratio } = gradeAnswer({
    type: q.type,
    correct: q.answer.value,
    user: userValue,
    partialCredit: q.answer.partial_credit === true,
    rules,
  });
  // ★ `round(x, 2)` 与后端同 —— 不四舍五入的话，0.5×0.3 会写成 0.15000000000000002，
  //   而"得分"是会被用户看到的数字
  const score = Math.round((q.score_default ?? 1) * ratio * 100) / 100;
  const at = nowIso();

  // ★★ 错题本那一行**先读、再写**，而且**事务里只有同步语句**。
  //   为什么：IndexedDB 的事务在"一个 `await` 之后没有挂起请求"时**会自动提交**，
  //   于是"读 → await → 写"的写法会随机报 `TransactionInactiveError`
  //   —— 而且**只在慢设备上偶发**（很难复现的那种）。把读挪到事务外就彻底没有这个面。
  const wrongBefore = await idbGet<{
    wrong_count: number;
    retry_correct: number;
    mastered_level: number;
    created_at: string;
  }>("wrong", item.question_id);

  // 错题本 / 重练计数与 items **在同一个事务**里落盘 —— 否则会出现
  // "题记了分、错题本没记"的半截状态（那正是"报告与错题本对不上"的来源）。
  //
  // ★★ 三种情形**分开写**（不合成一个三元表达式 —— 上面那版就写错过：
  //    "章节练习答对、但这道题恰好在错题本里"会误给 `retry_correct` +1，
  //    也就是 P2c-3 好不容易收紧的那个语义又被放开）：
  let wrongNext: Record<string, unknown> | null = null;
  if (!isCorrect) {
    wrongNext = {
      question_id: item.question_id,
      subject_id: q.subject_id,
      chapter_id: q.chapter_id,
      wrong_count: (wrongBefore?.wrong_count ?? 0) + 1,
      retry_correct: wrongBefore?.retry_correct ?? 0,
      mastered_level: wrongBefore?.mastered_level ?? 0,
      last_wrong_at: at,
      // ★ 约定 T 的对应物：答错时把它**从"移除"状态拉回来**（后端 `is_removed = false` 同义）
      removed_at: null,
      created_at: wrongBefore?.created_at ?? at,
      updated_at: at,
    };
  } else if (s.mode === "wrong" && wrongBefore) {
    // ★★ **只有"重练答对"才算重练答对** —— `retry_correct` 回答的是"这道错题我攻克了吗"。
    //    章节练习里答对**不算**（它与 C 端 `submit_answer` 的收紧口径一致）。
    wrongNext = {
      ...wrongBefore,
      retry_correct: wrongBefore.retry_correct + 1,
      updated_at: at,
    };
  }

  await tx(["items", "sessions", "wrong"], "readwrite", (g) => {
    const next: ItemRow = {
      ...item,
      answered_at: at,
      my_value: userValue,
      is_correct: isCorrect,
      score,
    };
    g("items").put(next);
    if (wrongNext !== null) g("wrong").put(wrongNext);
  });

  const fresh = await refreshCounters(sid);
  return {
    item_id: item.item_id,
    is_correct: isCorrect,
    score,
    correct_answer: { value: q.answer.value },
    my_value: userValue,
    analysis: q.analysis,
    analysis_html: q.analysis_html,
    idempotent: false,
    session: progressOf(fresh),
  };
}

/**
 * 会话计数的**唯一实现**（`total/answered/correct/score`）。
 * ★ 与后端 `_REFRESH_COUNTERS` 同一句 SQL 的等价物：**从 items 现算**，不做增量。
 *   增量（`answered += 1`）在幂等分支与并发下都会漂 —— 而"报告页与进度条不一致"
 *   正是那种**没有报错、只能靠肉眼发现**的缺陷。
 */
async function refreshCounters(sid: string): Promise<SessionRow> {
  const s = await idbGet<SessionRow>("sessions", sid);
  if (!s) throw notFound("练习不存在", 40401);
  const items = (await idbAll<ItemRow>("items")).filter((i) => i.session_id === sid);
  const answered = items.filter((i) => i.answered_at !== null);
  const next: SessionRow = {
    ...s,
    total: items.length,
    answered: answered.length,
    correct: answered.filter((i) => i.is_correct === true).length,
    score: Math.round(answered.reduce((n, i) => n + (i.score ?? 0), 0) * 100) / 100,
  };
  await idbPut("sessions", next);
  return next;
}

async function finishSession(sid: string): Promise<{ id: string }> {
  const s = await idbGet<SessionRow>("sessions", sid);
  if (!s) throw notFound("练习不存在", 40401);
  // 幂等：已结束就**不再改 `finished_at`**（否则反复点"交卷"会把它一直往后推）
  if (s.status !== "finished") {
    await idbPut("sessions", { ...s, status: "finished", finished_at: nowIso() });
  }
  await refreshCounters(sid);
  return { id: sid };
}

async function sessionReport(sid: string): Promise<SessionReport> {
  const s = await refreshCounters(sid);
  const [items, questions, kps, subjects, chapters] = await Promise.all([
    idbAll<ItemRow>("items"),
    idbAll<QuestionRow>("questions"),
    idbAll<{ id: string; name: string }>("kps"),
    idbAll<Subject>("subjects"),
    idbAll<ChapterRow>("chapters"),
  ]);
  const qById = new Map(questions.map((q) => [q.id, q]));
  const kpName = new Map(kps.map((k) => [k.id, k.name]));
  const mine = items.filter((i) => i.session_id === sid).sort((a, b) => a.seq - b.seq);
  const answered = mine.filter((i) => i.answered_at !== null);

  // 按知识点聚合 —— ★ 报告的核心维度之一（方案 §3.4）
  const buc = new Map<string, { total: number; correct: number }>();
  for (const i of answered) {
    const q = qById.get(i.question_id);
    if (!q) continue;
    const key = q.knowledge_point_id ?? "";
    const cur = buc.get(key) ?? { total: 0, correct: 0 };
    cur.total += 1;
    if (i.is_correct === true) cur.correct += 1;
    buc.set(key, cur);
  }
  const by_kp: KpStat[] = [...buc.entries()]
    .map(([id, v]) => ({
      knowledge_point_id: id === "" ? null : id,
      name: id === "" ? "（未归类）" : (kpName.get(id) ?? "（已删除的知识点）"),
      total: v.total,
      correct: v.correct,
      // ★ 零分母返 null（红线 2）—— 一处都不能写成 0
      accuracy: v.total === 0 ? null : Math.round((v.correct / v.total) * 1000) / 10,
    }))
    // 按正确率**升序**（最弱的在前）；`null` 排最后（"没做过"不是"最弱"）
    .sort((a, b) => (a.accuracy ?? 101) - (b.accuracy ?? 101) || a.name.localeCompare(b.name));

  const started = new Date(s.started_at).getTime();
  const ended = s.finished_at ? new Date(s.finished_at).getTime() : Date.now();
  return {
    id: s.id,
    mode: s.mode,
    status: s.status,
    title: s.title,
    subject_id: s.subject_id,
    subject_name: subjects.find((x) => x.id === s.subject_id)?.name ?? null,
    chapter_id: s.chapter_id,
    chapter_name: chapters.find((x) => x.id === s.chapter_id)?.name ?? null,
    total: s.total,
    answered: s.answered,
    correct: s.correct,
    score: s.score,
    accuracy: s.answered === 0 ? null : Math.round((s.correct / s.answered) * 1000) / 10,
    // ★ 用时按 **Asia/Shanghai** 无关（这是**时长**不是日界），但必须由服务端语义算 → 这里等价
    duration_sec: Math.max(0, Math.round((ended - started) / 1000)),
    started_at: s.started_at,
    finished_at: s.finished_at,
    by_kp,
  };
}

/* ============================================================ 标记 / 收藏 */

async function toggleFlag(
  m: string,
  which: "marks" | "favorites",
  qid: string,
): Promise<FlagState> {
  const q = await idbGet<QuestionRow>("questions", qid);
  if (m === "PUT") {
    // ★ 新增要求题目**在本地题库里**（否则会留下指向不存在题的脏记录，
    //   它在列表里表现成**空题干** —— 比报错更难查）
    if (!q) throw notFound("这道题不在当前题库里", 40401);
    const existing = await idbGet<{ question_id: string; collected_at: string }>(which, qid);
    if (!existing) {
      // 幂等：已存在就**什么都不做**（连 `collected_at` 都不动 —— 与后端 `ON CONFLICT DO NOTHING` 同）
      await idbPut(which, { question_id: qid, collected_at: nowIso() });
    }
  } else if (m === "DELETE") {
    // ★★ 取消**不要求题目存在**（硬约定 T）：题目下架后，"我的东西我要能撤"必须还成立，
    //    否则那条记录会**既看不见、又撤不掉**，卡死在列表里。
    await idbDelete(which, qid);
  } else {
    throw notFound("不支持的方法");
  }
  const [marks, favs] = await Promise.all([hasRow("marks", qid), hasRow("favorites", qid)]);
  return { question_id: qid, marked: marks, favorited: favs };
}

async function hasRow(store: StoreName, key: string): Promise<boolean> {
  return (await idbGet(store, key)) !== undefined;
}

async function listCollections(query: URLSearchParams): Promise<CollectionList> {
  const kind = query.get("kind") ?? "";
  if (kind !== "favorite" && kind !== "mark") {
    throw new ApiError(40001, 'kind 只能是 "favorite" 或 "mark"', "", 400);
  }
  const page = Math.max(1, Number(query.get("page") ?? 1) || 1);
  const size = Math.max(1, Math.min(Number(query.get("page_size") ?? 20) || 20, 100));
  const sid = asId(query.get("subject_id") ?? "");
  const store: StoreName = kind === "favorite" ? "favorites" : "marks";
  const [rows, questions, subjects, chapters] = await Promise.all([
    idbAll<{ question_id: string; collected_at: string }>(store),
    idbAll<QuestionRow>("questions"),
    idbAll<Subject>("subjects"),
    idbAll<ChapterRow>("chapters"),
  ]);
  const qById = new Map(questions.map((q) => [q.id, q]));
  const subName = new Map(subjects.map((s) => [s.id, s.name]));
  const chName = new Map(chapters.map((c) => [c.id, c.name]));
  const sorted = rows.slice().sort((a, b) => (a.collected_at < b.collected_at ? 1 : -1));
  const items: CollectionItem[] = [];
  for (const r of sorted) {
    const q = qById.get(r.question_id);
    items.push({
      question_id: r.question_id,
      subject_id: q?.subject_id ?? null,
      subject_name: q ? (subName.get(q.subject_id) ?? null) : null,
      chapter_name: q?.chapter_id ? (chName.get(q.chapter_id) ?? null) : null,
      type: q?.type ?? "",
      // ★ 题目不在本地题库里 ⇒ 题干留空、`question_available=false`（约定 T：**行仍然在**）
      stem: q?.stem ?? "",
      stem_html: q?.stem_html ?? null,
      collected_at: r.collected_at,
      marked: await hasRow("marks", r.question_id),
      favorited: await hasRow("favorites", r.question_id),
      question_available: q !== undefined,
      in_wrong_book: await hasRow("wrong", r.question_id),
    });
  }
  // ★ 分面**恒为全量**（先算分面，再用筛选收缩列表）—— 与后端 `list_collections` 同一条判据。
  //   ⚠️ B-1 第一版**只取了 `kind`、把整个 query 丢掉** ⇒ 页面传的
  //      `subject_id` / `page` / `page_size` **全部无效**：分页与科目筛选**静默失效**
  //      （列表永远是第一页全量）。它**不报错** —— 拿到的数据"看起来合理"，
  //      只有翻页或切科目时才看得出来。本批修（方案 §1.3 漏了这一条，见 §11 记录）。
  const facets = facetOf(items);
  const filtered = sid ? items.filter((x) => (x.subject_id ?? "") === sid) : items;
  const start = (page - 1) * size;
  return {
    kind: kind === "favorite" ? "favorite" : "mark",
    total: filtered.length,
    page,
    page_size: size,
    subjects: facets,
    items: filtered.slice(start, start + size),
  };
}

function facetOf(
  items: { subject_id: string | null; subject_name: string | null }[],
): WrongSubject[] {
  const m = new Map<string, WrongSubject>();
  for (const it of items) {
    const id = it.subject_id ?? "";
    const cur = m.get(id) ?? {
      subject_id: id,
      name: it.subject_name ?? "（已下架科目）",
      count: 0,
    };
    cur.count += 1;
    m.set(id, cur);
  }
  return [...m.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

/* ============================================================ 笔记 */

const NOTE_MAX = 2000;

function noteContent(body: { content?: unknown }): string {
  const raw = typeof body?.content === "string" ? body.content.trim() : "";
  // 与后端 `min_length=1` 同一条规则的前端镜像（空笔记是"误触"，不是内容）
  if (raw === "") throw new ApiError(40001, "笔记内容不能为空", "", 400);
  if (raw.length > NOTE_MAX) {
    throw new ApiError(40001, `笔记最多 ${NOTE_MAX} 个字`, "", 400);
  }
  return raw;
}

async function listNotesOfQuestion(qid: string): Promise<NoteListOfQuestion> {
  const notes = await idbAll<NoteRow>("notes");
  return {
    items: notes
      .filter((n) => n.question_id === qid && !n.deleted_at) // ★ 软删过滤（红线 3）
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
  };
}

async function addNote(qid: string, body: { content?: unknown }): Promise<Note> {
  const content = noteContent(body);
  const q = await idbGet<QuestionRow>("questions", qid);
  if (!q) throw notFound("这道题不在当前题库里", 40401);
  const at = nowIso();
  const note: Note = {
    id: newId("n"),
    question_id: qid,
    content,
    question_available: true,
    created_at: at,
    // ★ C 端这个字段由**触发器**维护；本地没有触发器 ⇒ 写入时显式置上，
    //   并保证"只有编辑会改它"（语义与触发器一致，不是另立一套）
    updated_at: at,
  };
  await idbPut("notes", { ...note, subject_id: q.subject_id, deleted_at: null });
  return note;
}

async function editNote(nid: string, body: { content?: unknown }): Promise<Note> {
  const content = noteContent(body);
  const row = await idbGet<NoteRow>("notes", nid);
  // ★ 不存在 / 已软删 / 不是我的 —— **一律 40401**（不用 403：403 会泄露"这条存在"）
  if (!row || row.deleted_at) throw notFound("笔记不存在", 40401);
  const next = { ...row, content, updated_at: nowIso() };
  await idbPut("notes", next);
  return {
    id: next.id,
    question_id: next.question_id,
    content: next.content,
    question_available: (await idbGet("questions", next.question_id)) !== undefined,
    created_at: next.created_at,
    updated_at: next.updated_at,
  };
}

async function removeNote(nid: string): Promise<unknown> {
  const row = await idbGet<NoteRow>("notes", nid);
  // ★ **软删**（表就是这么设计的；`DELETE` 影响 0 行也算"目标状态已达成" ⇒ 幂等）
  if (row && !row.deleted_at) {
    await idbPut("notes", { ...row, deleted_at: nowIso(), updated_at: nowIso() });
  }
  return { id: nid };
}

async function listNotes(query: URLSearchParams): Promise<NoteList> {
  const page = Math.max(1, Number(query.get("page") ?? 1) || 1);
  const size = Math.max(1, Math.min(Number(query.get("page_size") ?? 20) || 20, 100));
  const sid = asId(query.get("subject_id") ?? "");
  const [notes, questions, subjects, chapters] = await Promise.all([
    idbAll<NoteRow & { subject_id?: string | null }>("notes"),
    idbAll<QuestionRow>("questions"),
    idbAll<Subject>("subjects"),
    idbAll<ChapterRow>("chapters"),
  ]);
  const qById = new Map(questions.map((q) => [q.id, q]));
  const subName = new Map(subjects.map((s) => [s.id, s.name]));
  const chName = new Map(chapters.map((c) => [c.id, c.name]));

  const alive = notes
    .filter((n) => !n.deleted_at)
    .map((n) => {
      const q = qById.get(n.question_id);
      return {
        ...n,
        question_available: q !== undefined,
        subject_id: q?.subject_id ?? n.subject_id ?? null,
        subject_name: q ? (subName.get(q.subject_id) ?? null) : null,
        chapter_name: q?.chapter_id ? (chName.get(q.chapter_id) ?? null) : null,
        type: q?.type ?? null,
        stem: q?.stem ?? null,
        stem_html: q?.stem_html ?? null,
      };
    })
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  // ★ 分面**恒为全量**（不随筛选收缩）—— 与错题本 / 收藏同一条判据
  const facets = facetOf(alive);
  const filtered = sid ? alive.filter((n) => (n.subject_id ?? "") === sid) : alive;
  const start = (page - 1) * size;
  return {
    total: filtered.length,
    page,
    page_size: size,
    subjects: facets,
    items: filtered.slice(start, start + size),
  };
}

/**
 * 错题本列表（B-2 的页面用它）。
 *
 * ★ 与后端 `list_wrong` 同口径：
 *   · 倒序 `last_wrong_at DESC`（**最近错的在前**）—— 入口是"我刚错的那些题"；
 *   · `subjects`（分面）**恒为全量**，即使传了 `subject_id`；
 *     ★ 反例记在这里：后端第一版写成"只返选中科目"，理由是怕"chip 写着 3、列表 1 条"——
 *       那个理由不成立（一条错题只属于一个科目），代价却是**切一次科目后其他 chip
 *       从 DOM 里消失 ⇒ 切不回去**（p2c2 走查第一次跑就抓到了）。
 *   · `marked_only` **只筛列表**、不动分面；
 *   · 分页在 service 里**夹一次**（`page >= 1`、`page_size ∈ [1, 100]`），不靠调用方。
 *
 * ★ 约定 T：题目不在本地题库里（换过包 / 下架）⇒ **行仍然在**，只是 `question_available=false`。
 *   不过滤、不删除 —— "我的内容"不随"平台内容"的生命周期变化。
 *
 * ⚠️ `marked` 在下面**一次性预读成 Set**（不再逐行 `await hasRow(...)`）：
 *   循环里 `await` 会让每次迭代多一个 IDB 往返，而且**读的顺序不再由一个事务保证**。
 *   等价改写，不是新语义。
 */
async function listWrong(query: URLSearchParams): Promise<WrongList> {
  const page = Math.max(1, Number(query.get("page") ?? 1) || 1);
  const size = Math.max(1, Math.min(Number(query.get("page_size") ?? 20) || 20, 100));
  const sid = asId(query.get("subject_id") ?? "");
  // ★ C 端传的是 `marked_only: true`，而 `request()` 会 `String(v)` ⇒ 这里收到的是 `"true"`。
  //   写成 `=== "true"` 而不是 `Boolean(...)`：后者会把字符串 `"false"` 判成真值。
  const markedOnly = query.get("marked_only") === "true";
  const [rows, questions, subjects, chapters, marks] = await Promise.all([
    idbAll<WrongRow>("wrong"),
    idbAll<QuestionRow>("questions"),
    idbAll<Subject>("subjects"),
    idbAll<ChapterRow>("chapters"),
    idbAll<{ question_id: string }>("marks"),
  ]);
  const qById = new Map(questions.map((q) => [q.id, q]));
  const subName = new Map(subjects.map((s) => [s.id, s.name]));
  const chName = new Map(chapters.map((c) => [c.id, c.name]));
  const markSet = new Set(marks.map((x) => x.question_id));
  const alive = rows
    .filter((r) => r.removed_at === null) // 软删过滤（后端 `w.is_removed = false`）
    .slice()
    .sort(
      (a, b) =>
        (a.last_wrong_at < b.last_wrong_at ? 1 : -1) || (a.question_id < b.question_id ? -1 : 1),
    );
  const items: WrongItem[] = [];
  for (const r of alive) {
    const q = qById.get(r.question_id);
    items.push({
      question_id: r.question_id,
      subject_id: q?.subject_id ?? null,
      subject_name: q ? (subName.get(q.subject_id) ?? null) : null,
      chapter_name: q?.chapter_id ? (chName.get(q.chapter_id) ?? null) : null,
      type: q?.type ?? "",
      stem: q?.stem ?? "",
      wrong_count: r.wrong_count,
      retry_correct: r.retry_correct,
      mastered_level: r.mastered_level,
      last_wrong_at: r.last_wrong_at,
      marked: markSet.has(r.question_id),
      question_available: q !== undefined,
    });
  }
  const facets = facetOf(items);
  let filtered = items;
  if (sid) filtered = filtered.filter((x) => (x.subject_id ?? "") === sid);
  if (markedOnly) filtered = filtered.filter((x) => x.marked);
  const start = (page - 1) * size;
  return {
    total: filtered.length,
    page,
    page_size: size,
    subjects: facets,
    items: filtered.slice(start, start + size),
  };
}

/**
 * 错题详情（**含正确答案与解析**）。
 *
 * ★ 与后端 `get_wrong_detail` 同口径：那道题必须在**我的**错题本里、且未软删；
 *   否则 `40401` 且**不返答案**（答案是给"已经和这道题交过手"的人的）。
 * ★ "题不在本地题库里"也走**同一个 `40401`** —— 和后端一样**不区分**两种原因：
 *   区分开就等于告诉调用方"这条错题存在，只是题没了"，而页面拿到的都是"打不开"。
 */
async function wrongDetail(qid: string): Promise<WrongDetail> {
  const [row, q, subjects, chapters] = await Promise.all([
    idbGet<WrongRow>("wrong", qid),
    idbGet<QuestionRow>("questions", qid),
    idbAll<Subject>("subjects"),
    idbAll<ChapterRow>("chapters"),
  ]);
  if (!row || row.removed_at !== null || !q) {
    throw notFound("这道题不在你的错题本里", 40401);
  }
  return {
    question_id: q.id,
    subject_id: q.subject_id,
    subject_name: subjects.find((s) => s.id === q.subject_id)?.name ?? null,
    chapter_name: q.chapter_id ? (chapters.find((c) => c.id === q.chapter_id)?.name ?? null) : null,
    type: q.type,
    stem: q.stem,
    stem_html: q.stem_html,
    options: q.options,
    // ★ 不在这里做第二次"归一"：题库包在**导出时**已经归一（`canonical_doc`），
    //   而 PWA 侧再归一 == 放一张 token 表 == 第二份判分语义（方案 §3.1 禁止）。★
    answer: { value: q.answer.value },
    analysis: q.analysis,
    analysis_html: q.analysis_html,
    wrong_count: row.wrong_count,
    retry_correct: row.retry_correct,
    mastered_level: row.mastered_level,
    // ★ 恒为 null，**不是漏写**：C 端的 `reason_tag`（"错因标签"）那一列没有任何写入方，
    //   而 PWA 的 `wrong` store 根本没有这个字段。写了才是"凭印象造一个值"。
    reason_tag: null,
    last_wrong_at: row.last_wrong_at,
  };
}

/* ============================================================ 数据备份（PWA 独有） */

/**
 * 「**用户数据**」的 store 清单 —— **不含题库**。
 *
 * ★ 这是**一处定义**：导出与导入都遍历它（两处各写一份必然漂）。
 * ★ 什么算"用户数据"：**我产生的**（答题记录 / 错题 / 收藏 / 标记 / 笔记）。
 *   什么不算：
 *   · `subjects` / `chapters` / `kps` / `questions` —— 那是题库，可由 `pwa-bank.json`
 *     重新导入（5.85 MB）；塞进备份会让文件巨大，而"能打开看"正是备份的价值之一；
 *   · `meta` —— 它存的是**题库**指纹与导入哨兵，属于题库侧（不是"我的东西"）。
 */
export const USER_DATA_STORES = [
  "sessions",
  "items",
  "wrong",
  "marks",
  "favorites",
  "notes",
] as const;

/** 备份文件的形状。★ `format` 是**用途标识** —— 导错文件时要能**立刻说清**是哪一种。 */
export type UserDataBundle = {
  format: "yijian-pwa-userdata";
  format_version: number;
  exported_at: string;
  /** ★ 导出时的题库指纹：**导入时必须与本机题库一致**（题对不上号的记录没有意义）。 */
  bank_version: string;
  /** 各 store 行数（导入后**对账**用 —— 没有它就只能"看着像导成功了"）。 */
  counts: Partial<Record<(typeof USER_DATA_STORES)[number], number>>;
  data: Partial<Record<(typeof USER_DATA_STORES)[number], unknown[]>>;
};

/**
 * 备份文件的建议名。
 *
 * ★ 用 `todayInShanghai()` 而不是 `toISOString().slice(0,10)`：后者是 **UTC 日期** ——
 *   晚上 8 点之后导出的备份会**少一天**，而且只在跨零点前后看得出来。
 *   （与项目「日界按 `Asia/Shanghai`」的红线同一条。）
 */
export function backupFileName(at: Date = new Date()): string {
  return `yijian-pwa-data-${todayInShanghai(at)}.json`;
}

/** 备份格式的版本。★ **与题库的 `schema_version` 是两件事**：一个管数据形状、一个管题库形状。 */
const USERDATA_FORMAT_VERSION = 1;

/**
 * 导出**用户数据**（**不含题库**）。
 *
 * ★ 返回**纯对象**（不是 Blob）：页面自己决定怎么落盘（下载 / 展示 / 复制），
 *   而"数据里有什么"这件事只在这里定义一次。
 */
export async function exportUserData(): Promise<UserDataBundle> {
  const bank = await metaGet<BankMeta>("bank");
  if (bank === null) {
    throw notFound("还没有题库 —— 先到「导入题库」导入题库，再来备份数据。");
  }
  const data: UserDataBundle["data"] = {};
  const cnt: UserDataBundle["counts"] = {};
  for (const s of USER_DATA_STORES) {
    const rows = await idbAll<unknown>(s);
    data[s] = rows;
    cnt[s] = rows.length;
  }
  return {
    format: "yijian-pwa-userdata",
    format_version: USERDATA_FORMAT_VERSION,
    exported_at: nowIso(),
    bank_version: bank.bank_version,
    counts: cnt,
    data,
  };
}

/** `peekUserData()` 的结果 —— 只读元信息，**不写库**。
 *  ★ `bank_matches` 不是"错误"，是**现状**：不匹配时页面要能提前告诉用户
 *    "这份备份属于别的题库"，而不是等他点了"确认覆盖"才报错。
 */
export type UserDataMeta = {
  format_version: number;
  exported_at: string;
  bank_version: string;
  counts: Record<string, number>;
  /** 与**当前**题库是否同一个（导入的硬前提）。 */
  bank_matches: boolean;
};

/**
 * 只读元信息（**不写库**）—— 二次确认之前用它。
 *
 * ★ 顺序很重要：**先看再问、问完才写**。反过来的话，"用户选了文件"这一个动作
 *   就已经把库清掉了 —— 而他可能只是选错了文件。
 *   （与 `/setup` 的 `peekBankMeta(file)` 同一个模式。）
 */
export async function peekUserData(file: File): Promise<UserDataMeta> {
  let raw: Partial<UserDataBundle>;
  try {
    raw = JSON.parse(await file.text());
  } catch {
    throw new ApiError(40001, "这个文件不是 JSON，或者已经损坏。", "", 400);
  }
  if (raw?.format !== "yijian-pwa-userdata") {
    throw new ApiError(
      40001,
      "这不是数据备份文件。如果你要换题库，请走「我的 → 题库信息 → 换题库」。",
      "",
      400,
    );
  }
  const bank = await metaGet<BankMeta>("bank");
  const counts: Record<string, number> = {};
  for (const s of USER_DATA_STORES) {
    const rows = raw.data?.[s];
    counts[s] = Array.isArray(rows) ? rows.length : -1; // -1 = 这一段缺失（导入时会被拒）
  }
  return {
    format_version: Number(raw.format_version ?? 0),
    exported_at: String(raw.exported_at ?? ""),
    bank_version: String(raw.bank_version ?? ""),
    counts,
    bank_matches: bank !== null && raw.bank_version === bank.bank_version,
  };
}

/**
 * 只清**用户数据**的 6 个 store（**不碰题库**）。
 *
 * ★ 为什么不复用 `db.ts::wipeAll()`：它清**全部** store（含 `questions` 等）
 *   —— "恢复用户数据"不该顺带把题库删掉（那要重新导 5.85 MB，还要求版本一致）。
 */
async function wipeUserData(): Promise<void> {
  const names = [...USER_DATA_STORES] as StoreName[];
  await tx(names, "readwrite", (g) => {
    for (const n of names) g(n).clear();
  });
}

/**
 * 导入用户数据（**整体替换**，不是合并）。
 *
 * ★ **为什么是整体替换**：合并的语义**没法定义** —— 同一条笔记在备份与当前各有一份时
 *   "谁的胜"？按时间？那"我在备份之后删掉的收藏"就会复活。**说不出规则的合并 = 迟早出错。**
 *   ⇒ 调用方**必须先做二次确认**，且确认框要**写清会覆盖什么**（文案见 `me/backup` 页）。
 *
 * ⚠️ 本函数**不负责**问"是否真的要覆盖" —— 二次确认是 **UI 的职责**
 *   （与 `importBank(file, onProgress, force)` 同一个约定：数据层不做交互）。
 */
export async function importUserData(file: File): Promise<{ counts: Record<string, number> }> {
  let raw: Partial<UserDataBundle>;
  try {
    raw = JSON.parse(await file.text());
  } catch {
    throw new ApiError(40001, "这个文件不是 JSON，或者已经损坏。", "", 400);
  }
  if (raw?.format !== "yijian-pwa-userdata") {
    // ★ 导错文件是最常见的失误 —— 文案要**指出正确的入口**，不是"格式错误"四个字
    throw new ApiError(
      40001,
      "这不是数据备份文件。如果你要换题库，请走「我的 → 题库信息 → 换题库」。",
      "",
      400,
    );
  }
  if (raw.format_version !== USERDATA_FORMAT_VERSION) {
    throw new ApiError(
      40001,
      `备份格式版本是 ${raw.format_version}，本应用只认识 ${USERDATA_FORMAT_VERSION}。`,
      "",
      400,
    );
  }
  const bank = await metaGet<BankMeta>("bank");
  if (bank === null) {
    throw new ApiError(40001, "还没有题库 —— 先导入题库，再恢复数据。", "", 400);
  }
  if (raw.bank_version !== bank.bank_version) {
    // ★★ 这一条**必须拦**：备份里的 `question_id` 指向的是**另一个题库** ⇒
    //   导进去会得到一堆"题已不在"的孤儿记录 —— **不报错**，但列表里全是空题干。
    throw new ApiError(
      40901,
      "这份备份属于**另一个题库**（或题库版本不同），当前题库对不上号。" +
        "请先导入与备份时相同的题库，再恢复数据。",
      "",
      409,
    );
  }

  const data = raw.data ?? {};
  for (const s of USER_DATA_STORES) {
    if (!Array.isArray(data[s])) {
      throw new ApiError(40001, `备份里缺少「${s}」这一段，文件可能不完整。`, "", 400);
    }
  }

  // ① 清空**用户数据**（题库不动）
  await wipeUserData();
  // ② 分批写（**每个 store 一个事务**）。任一失败 ⇒ 抛错 —— 此时库里是**空的**，
  //    调用方要能看出"没成功"（而不是以为成功了）。
  for (const s of USER_DATA_STORES) {
    // ★ 同上：备份文件也是**跨边界**的（JSON）⇒ 进来就归一。
    //   顺带的一个好处：**修好之前导出的备份**（里面是数字 id）也能被它救回来。
    const rows = (data[s] as unknown[]).map(normalizeIds);
    for (let i = 0; i < rows.length; i += IMPORT_CHUNK) {
      await idbPutMany(s, rows.slice(i, i + IMPORT_CHUNK));
    }
  }
  // ③ 对账 —— **逐 store** 比（只比总数的话"会话少 1 条、items 多 1 条"能互相抵消）
  const after = await counts();
  const mismatch: string[] = [];
  for (const s of USER_DATA_STORES) {
    const want = raw.counts?.[s];
    if (typeof want === "number" && after[s] !== want) {
      mismatch.push(`${s}：备份 ${want} 条、写入后 ${after[s]} 条`);
    }
  }
  if (mismatch.length > 0) {
    throw new ApiError(40001, `恢复对账没通过：${mismatch.join("；")}`, "", 400);
  }
  const out: Record<string, number> = {};
  for (const s of USER_DATA_STORES) out[s] = after[s];
  return { counts: out };
}

/** 打开数据库（首页/自检用）。页面不该直接碰它，但"有没有库"这件事要能问。 */
export { openDb, STORES };
