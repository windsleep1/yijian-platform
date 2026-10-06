/**
 * 本地数据库（IndexedDB）—— 个人 PWA 的**唯一**持久化层。
 *
 * ## 它替代了什么
 * C 端的数据在**服务端**（PostgreSQL，经 `/api/v1` 访问）；PWA 没有服务端，
 * 数据在本机浏览器里。⇒ 本文件 + `api.ts` 就是"C 端那半边后端"。
 *
 * ## ★ 与 SW 的分工（方案 §2.2）
 * **离线能力来自这里，不是 Service Worker。** `public/sw.js` 只缓存**应用壳**；
 * 题库 / 答题记录 / 错题 / 收藏 / 标记 / 笔记**全在本文件管的库里**。
 * 判据：断网后能答题 ⇒ 是 IndexedDB 在干活；"壳能打开"是另一件事，得分开验。
 *
 * ## 红线（三条，都是项目已有约定，别在这里丢掉）
 * 1. **日界一律 `Asia/Shanghai`** —— 见 `todayInShanghai()`；不要用 `toLocaleDateString()` 的默认时区。
 * 2. **零分母返 `null`**（见 `api.ts` 的统计），不是 `0`。
 * 3. **软删要过滤** —— `notes.deleted_at` / `wrong.removed_at` 在**每条读路径**上都要带。
 */

/** 库名带后缀，将来要换结构时改名即可（同源多库，不互相污染）。 */
export const DB_NAME = "yijian-pwa";

/**
 * 结构版本。
 * ★ 加/改 store 或索引都**必须**+1 并在 `migrate()` 里写清怎么升 ——
 *   否则老用户打开时 `onupgradeneeded` 不会跑，表现为**索引缺失**（查询结果为空，不报错）。
 */
export const DB_VERSION = 1;

export type StoreName =
  | "meta"
  | "subjects"
  | "chapters"
  | "kps"
  | "questions"
  | "sessions"
  | "items"
  | "wrong"
  | "favorites"
  | "marks"
  | "notes";

type IndexSpec = { name: string; keyPath: string | string[]; unique?: boolean };
type StoreSpec = { keyPath: string; indexes: IndexSpec[] };

/**
 * store 结构表 —— **一张表说清"有什么、怎么查"**。
 *
 * ⚠️ `items` 的主键用 `item_id`（不用方案 §3.3 写的 `[session_id, seq]`）——
 *    理由：答题页全程按 `item_id` 寻址（提交、跳题、标记），复合主键会让每次访问
 *    都要先算出 seq，而 seq 是**可变的**（重练/换题源）。`session_id` 降级为索引。
 */
export const STORES: Record<StoreName, StoreSpec> = {
  /** 单表单条记录（`key` 是字符串键）。题库指纹、导入状态、评分规则都放这儿。 */
  meta: { keyPath: "key", indexes: [] },
  subjects: {
    keyPath: "id",
    indexes: [{ name: "sort_no", keyPath: "sort_no" }],
  },
  chapters: {
    keyPath: "id",
    indexes: [
      { name: "subject_id", keyPath: "subject_id" },
      { name: "sort_no", keyPath: "sort_no" },
    ],
  },
  /** 知识点（`knowledge_point_id` → 名字）。报告的"按知识点"维度靠它。 */
  kps: {
    keyPath: "id",
    indexes: [{ name: "subject_id", keyPath: "subject_id" }],
  },
  questions: {
    keyPath: "id",
    indexes: [
      { name: "subject_id", keyPath: "subject_id" },
      { name: "chapter_id", keyPath: "chapter_id" },
      { name: "type", keyPath: "type" },
    ],
  },
  sessions: {
    keyPath: "id",
    indexes: [{ name: "started_at", keyPath: "started_at" }],
  },
  items: {
    keyPath: "item_id",
    indexes: [
      { name: "session_id", keyPath: "session_id" },
      { name: "question_id", keyPath: "question_id" },
    ],
  },
  wrong: {
    keyPath: "question_id",
    indexes: [
      { name: "subject_id", keyPath: "subject_id" },
      { name: "last_wrong_at", keyPath: "last_wrong_at" },
    ],
  },
  favorites: {
    keyPath: "question_id",
    indexes: [{ name: "collected_at", keyPath: "collected_at" }],
  },
  marks: {
    keyPath: "question_id",
    indexes: [{ name: "collected_at", keyPath: "collected_at" }],
  },
  notes: {
    keyPath: "id",
    indexes: [
      { name: "question_id", keyPath: "question_id" },
      { name: "subject_id", keyPath: "subject_id" },
      { name: "created_at", keyPath: "created_at" },
    ],
  },
};

let dbPromise: Promise<IDBDatabase> | null = null;

/** 把 IDBRequest 包成 Promise（只在 `upgradeneeded` 里用得到的那部分）。 */
function req<T>(r: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error ?? new Error("IndexedDB 请求失败"));
  });
}

/** 建表 / 升级。★ **只加不改**：已存在的 store 不动（改字段要靠版本号 + 迁移函数）。 */
function migrate(db: IDBDatabase): void {
  for (const [name, spec] of Object.entries(STORES) as [StoreName, StoreSpec][]) {
    const store = db.objectStoreNames.contains(name)
      ? db.transaction(name).objectStore(name)
      : db.createObjectStore(name, { keyPath: spec.keyPath });
    for (const ix of spec.indexes) {
      if (store.indexNames.contains(ix.name)) continue;
      store.createIndex(ix.name, ix.keyPath, { unique: ix.unique ?? false });
    }
  }
}

/** 打开（首次会建表）。**单例**：并发打开同一个库在 Safari 上会慢得离谱。 */
export function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const r = indexedDB.open(DB_NAME, DB_VERSION);
    r.onupgradeneeded = () => migrate(r.result);
    r.onsuccess = () => {
      // 另一个标签页触发了升级 ⇒ 本连接会被强制关闭，此时后续事务全报
      // `InvalidStateError`。**出声**比"操作静默失败"好查得多。
      r.result.onversionchange = () => {
        r.result.close();
        dbPromise = null;
      };
      resolve(r.result);
    };
    r.onerror = () => reject(r.error ?? new Error("打不开 IndexedDB"));
    // 隐私模式 / 存储被禁：给一条**说人话**的错误，而不是让上层看到 `null`
    r.onblocked = () => reject(new Error("数据库被另一个标签页占用，请关掉其它页面后重试"));
  });
  return dbPromise;
}

/** 在一个事务里干活。`fn` 抛错 ⇒ 事务中止（**要么全成、要么全不成**）。 */
export async function tx<T>(
  stores: StoreName | StoreName[],
  mode: IDBTransactionMode,
  fn: (get: (s: StoreName) => IDBObjectStore) => Promise<T> | T,
): Promise<T> {
  const db = await openDb();
  const names = Array.isArray(stores) ? stores : [stores];
  return new Promise<T>((resolve, reject) => {
    const t = db.transaction(names, mode);
    let out: T;
    t.oncomplete = () => resolve(out);
    t.onerror = () => reject(t.error ?? new Error("事务失败"));
    t.onabort = () => reject(t.error ?? new Error("事务被中止"));
    Promise.resolve(fn((s) => t.objectStore(s)))
      .then((v) => {
        out = v;
      })
      .catch((e) => {
        // 主动 abort：让"批量写一半失败"不会留下半份数据（导入的"不许留半份"就靠它）
        try {
          t.abort();
        } catch {
          /* 已结束的事务再 abort 会抛，忽略 */
        }
        reject(e);
      });
  });
}

export function idbGet<T>(store: StoreName, key: IDBValidKey): Promise<T | undefined> {
  return tx(store, "readonly", (g) => req<T | undefined>(g(store).get(key)));
}

export function idbAll<T>(store: StoreName, query?: IDBValidKey | IDBKeyRange): Promise<T[]> {
  return tx(store, "readonly", (g) => req<T[]>(g(store).getAll(query)));
}

export function idbCount(store: StoreName): Promise<number> {
  return tx(store, "readonly", (g) => req<number>(g(store).count()));
}

export function idbPut(store: StoreName, row: unknown): Promise<void> {
  return tx(store, "readwrite", (g) => {
    g(store).put(row as never);
  });
}

/** 批量写。**一个事务** —— 导入的"不许留下半份数据"靠它（见 `api.ts::importBank`）。 */
export function idbPutMany(store: StoreName, rows: unknown[]): Promise<void> {
  return tx(store, "readwrite", (g) => {
    const os = g(store);
    for (const r of rows) os.put(r as never);
  });
}

export function idbDelete(store: StoreName, key: IDBValidKey): Promise<void> {
  return tx(store, "readwrite", (g) => {
    g(store).delete(key);
  });
}

/** 清空**每一个** store —— 换题库用（方案 §4.1：换包 = 清空全部数据）。 */
export function wipeAll(): Promise<void> {
  const names = Object.keys(STORES) as StoreName[];
  return tx(names, "readwrite", (g) => {
    for (const n of names) g(n).clear();
  });
}

/** 逐 store 行数 —— 导入对账与"数据往返"验收都用它（**可证伪**：清空后应全为 0）。 */
export async function counts(): Promise<Record<StoreName, number>> {
  const names = Object.keys(STORES) as StoreName[];
  const out = {} as Record<StoreName, number>;
  for (const n of names) out[n] = await idbCount(n);
  return out;
}

/* ---------------------------------------------------------------- meta 小工具 */

export type MetaRow = { key: string; value: unknown };

export async function metaGet<T>(key: string): Promise<T | null> {
  const row = await idbGet<MetaRow>("meta", key);
  return row === undefined ? null : (row.value as T);
}

export async function metaSet(key: string, value: unknown): Promise<void> {
  await idbPut("meta", { key, value });
}

/**
 * "今天"的日期串（`YYYY-MM-DD`），**按 `Asia/Shanghai`**。
 *
 * ★ 为什么不用 `new Date().toLocaleDateString("zh-CN")`：它按**设备时区**算 ——
 *   用户把手机时区改成 UTC，跨零点时"今天答了几题"就会错一天，而且**不报错**。
 *   本项目的日界红线是 `Asia/Shanghai`（与后端 `app/core/timeutil` 同口径）。
 * ★ `en-CA` 的格式恰好是 `YYYY-MM-DD`（用 `Intl` 拿日期部分，比自己拼 `getUTC*` 稳）。
 */
export function todayInShanghai(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}
