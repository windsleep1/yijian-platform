"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import {
  ApiError,
  backupFileName,
  exportUserData,
  importUserData,
  peekUserData,
  type UserDataMeta,
} from "@/lib/api";

/**
 * 数据备份（`/me/backup`）—— **PWA 独有**（C 端没有：那边数据在服务端）。
 *
 * ## 为什么必须有这一页
 * 本应用的**唯一**存储是浏览器 IndexedDB，而它**会**被清掉：
 * 用户清缓存、换手机、浏览器回收存储 —— 任何一种都会让"我的记录"消失，
 * **没有任何提示**。⇒ 导出/导入不是锦上添花，是**零后端方案的必要配套**。
 *
 * ## ★★ 与「换题库」的区别（用户最容易混淆的一处）
 *
 * | | 换题库（在 `/setup`） | 数据备份（本页） |
 * |---|---|---|
 * | 换的是什么 | 整份**题库**（题目 / 章节 / 知识点） | 我的**记录**（答题 / 错题 / 收藏 / 标记 / 笔记） |
 * | 文件 | `pwa-bank.json`（5.85 MB） | `yijian-pwa-data-*.json`（几百 KB） |
 * | 副作用 | **清空全部数据** | **覆盖我的记录**（题库不动） |
 *
 * ⇒ 两处的确认框文案**刻意不一样**，且各自点明对方的存在（导错文件时能立刻反应过来）。
 *
 * ## ★ 顺序纪律：**先看 → 再问 → 才写**
 * 选完文件**只读元信息**（`peekUserData`），**不碰库** —— 用户可以选错文件，
 * 而"选错"不该有任何后果。二次确认之后才执行。
 */

type Stage =
  | { k: "idle" }
  | { k: "exporting" }
  | { k: "exported"; name: string; total: number }
  | { k: "confirm"; file: File; meta: UserDataMeta }
  | { k: "importing" }
  | { k: "imported"; counts: Record<string, number> };

/** 逐 store 的中文名（结果展示用；用户看不懂 `marks` / `items`）。 */
const LABEL: Record<string, string> = {
  sessions: "练习记录",
  items: "答题明细",
  wrong: "错题本",
  marks: "标记",
  favorites: "收藏",
  notes: "笔记",
};

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

export default function BackupPage() {
  const [stage, setStage] = useState<Stage>({ k: "idle" });
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  /** 导出：把用户数据落成一个 JSON 文件（**不含题库**）。 */
  const onExport = useCallback(async () => {
    setErr("");
    setStage({ k: "exporting" });
    try {
      const bundle = await exportUserData();
      const name = backupFileName();
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      // ★ 立刻释放：不释放会在页面生命周期内一直占着这块内存（大备份时明显）
      URL.revokeObjectURL(url);
      const total = Object.values(bundle.counts).reduce((n, x) => n + (x ?? 0), 0);
      setStage({ k: "exported", name, total });
    } catch (e) {
      setErr(errText(e));
      setStage({ k: "idle" });
    }
  }, []);

  /** 选文件：**只读元信息**，然后问用户（顺序见抬头）。 */
  const onPick = useCallback(async (file: File) => {
    setErr("");
    try {
      const meta = await peekUserData(file);
      setStage({ k: "confirm", file, meta });
    } catch (e) {
      setErr(errText(e));
      setStage({ k: "idle" });
    } finally {
      // 让"选同一个文件"能再次触发 change（否则改完文件再选它不会响）
      if (fileRef.current) fileRef.current.value = "";
    }
  }, []);

  /** 用户点了"确认覆盖"之后才真正执行。 */
  const onConfirm = useCallback(async (file: File) => {
    setErr("");
    setStage({ k: "importing" });
    try {
      const r = await importUserData(file);
      setStage({ k: "imported", counts: r.counts });
    } catch (e) {
      setErr(errText(e));
      setStage({ k: "idle" });
    }
  }, []);

  const busy = stage.k === "exporting" || stage.k === "importing";

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <Link href="/me" className="text-xs text-sub underline">
        ← 我的
      </Link>
      <h1 className="mt-3 text-2xl font-semibold">数据备份</h1>
      <p className="mt-1 text-sm text-sub">
        数据只存在这台设备上。清缓存、换手机都会让它消失 —— 建议定期导出。
      </p>

      {err ? (
        <p role="alert" className="mt-4 text-sm text-danger">
          {err}
        </p>
      ) : null}

      {/* ---------------------------------------------------------------- 导出 */}
      <section className="mt-8">
        <h2 className="text-sm font-medium">导出</h2>
        <p className="mt-1 text-xs text-sub">
          含你的<strong>练习记录 / 答题明细 / 错题本 / 标记 / 收藏 / 笔记</strong>。
          <br />
          <strong>不含题库</strong>（题目可由 <code className="font-mono">pwa-bank.json</code>{" "}
          重新导入，所以没必要塞进备份）。
        </p>
        <button
          onClick={() => void onExport()}
          disabled={busy}
          data-export
          className="min-h-touch mt-3 w-full rounded-lg border border-line font-medium disabled:opacity-60"
        >
          {stage.k === "exporting" ? "导出中…" : "导出我的数据"}
        </button>
        {stage.k === "exported" ? (
          <p className="mt-3 text-sm text-sub" data-exported>
            已导出 {stage.total} 条到 <span className="font-mono">{stage.name}</span>
          </p>
        ) : null}
      </section>

      {/* ---------------------------------------------------------------- 导入 */}
      <section className="mt-10">
        <h2 className="text-sm font-medium">导入（恢复）</h2>
        <p className="mt-1 text-xs text-sub">
          选一份之前导出的备份文件，把记录恢复到这台设备。
          <br />
          <span className="text-danger">会覆盖当前的记录</span>（题库不受影响）。
        </p>

        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          data-import-file
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void onPick(f);
          }}
        />

        {stage.k === "confirm" ? (
          /* ★★ 二次确认块 —— **必须写清会覆盖什么**，而不是"确认导入"四个字。
             用户要能在这里看出：这是"我的记录"的替换，**不是**换题库。 */
          <div data-confirm-block className="mt-3 rounded-lg border border-danger p-4">
            <p className="text-sm font-medium text-danger">
              导入会<strong>覆盖</strong>当前所有答题记录、错题本、标记、收藏、笔记。
            </p>
            <ul className="mt-2 space-y-1 text-xs text-sub">
              {Object.entries(stage.meta.counts).map(([k, n]) => (
                <li key={k}>
                  · {LABEL[k] ?? k}：
                  {n < 0 ? <span className="text-danger">这一段缺失，文件不完整</span> : `${n} 条`}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-sub">
              备份来自题库 <span className="font-mono">{stage.meta.bank_version.slice(0, 8)}</span>
              （{stage.meta.exported_at.slice(0, 10)}）
            </p>
            {!stage.meta.bank_matches ? (
              /* ★ 提前说清，而不是等他点了才报错：不匹配时导入**必然**失败
                 （题对不上号 ⇒ 记录全成孤儿）。⇒ 按钮也禁掉。 */
              <p role="alert" className="mt-2 text-xs text-danger" data-bank-mismatch>
                这份备份属于<strong>另一个题库</strong>，当前题库对不上号。
                请先导入与备份时相同的题库，再回来恢复数据。
              </p>
            ) : null}
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => setStage({ k: "idle" })}
                data-confirm-cancel
                className="min-h-touch flex-1 rounded-lg border border-line text-sm font-medium"
              >
                取消
              </button>
              <button
                onClick={() => void onConfirm(stage.file)}
                disabled={!stage.meta.bank_matches}
                data-confirm-import
                className="min-h-touch flex-1 rounded-lg bg-danger text-sm font-medium text-white disabled:opacity-40"
              >
                确认覆盖并导入
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            data-import
            className="min-h-touch mt-3 w-full rounded-lg border border-line font-medium disabled:opacity-60"
          >
            {stage.k === "importing" ? "导入中…" : "选择备份文件"}
          </button>
        )}

        {stage.k === "imported" ? (
          <div className="mt-3 text-sm" data-imported>
            <p className="text-brand">已恢复。当前：</p>
            <ul className="mt-1 space-y-1 text-xs text-sub">
              {Object.entries(stage.counts).map(([k, n]) => (
                <li key={k}>
                  · {LABEL[k] ?? k}：{n} 条
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {/* ★ 点明"换题库在别处" —— 用户混淆时能立刻被纠正（抬头那张表的第一行）。 */}
      <p className="mt-10 text-xs text-sub">
        想换的是<strong>题库</strong>（题目 / 章节）而不是记录？ 去「
        <Link href="/me" className="underline">
          我的
        </Link>{" "}
        → 题库信息 → 换题库」。
      </p>
    </main>
  );
}
