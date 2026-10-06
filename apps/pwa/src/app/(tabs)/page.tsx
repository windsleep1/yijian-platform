"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { bankSummary, recoverInterruptedImport, type BankSummary } from "@/lib/api";

/**
 * 首页 —— **"题库现在是什么状态"** 这一个问题。
 *
 * ★★ 它为什么必须存在（而不是直接跳 `/setup`）：
 *   `start_url` 是 `/`，而 **PWA 的安装前提是"能打开"**。
 *   把首页做成"没题库就重定向去 /setup"看着更省事，但重定向会让
 *   **安装态下的回退路径少一层**（用户从主屏点开 → 又跳一次 → 有的浏览器会掉出独立窗口）。
 *   首页直接显示状态、给一个明确的入口，比替用户决定"你现在该去哪"稳。
 *
 * ★ 这个页面**不碰题目**，只问两件事：有没有题库、上次导入是不是没导完。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

export default function HomePage() {
  const [bank, setBank] = useState<BankSummary | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [recovered, setRecovered] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      // ★ 先做**可中断导入的自检**：上次导到一半 ⇒ 整库清空（方案 §4 约束 1）。
      //   顺序不能反 —— 先读摘要的话，会读到一个"半份题库"的数量并显示出来。
      setRecovered(await recoverInterruptedImport());
      setBank(await bankSummary());
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">一建通 · 离线版</h1>
      <p className="mt-1 text-sm text-sub">数据只存在这台设备上，不上传、不联网也能用</p>

      {err && (
        <p className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {err}
        </p>
      )}

      {recovered && (
        <p
          data-import-recovered="1"
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800"
        >
          上次的导入没有完成，已经清掉那半份数据。请重新导入题库。
        </p>
      )}

      {!loaded && <p className="mt-6 text-sm text-sub">加载中…</p>}

      {loaded && bank === null && (
        <section className="mt-6" data-bank="none">
          <p className="text-sm text-sub">
            还没有题库。导入之后就能选题、答题 —— 题目与作答记录都保存在本机。
          </p>
          <Link
            href="/setup"
            data-entry="setup"
            className="mt-4 flex min-h-touch items-center justify-center rounded-xl bg-brand px-4 text-sm text-brand-fg"
          >
            导入题库
          </Link>
        </section>
      )}

      {loaded && bank !== null && (
        <section className="mt-6" data-bank="ready">
          <div className="rounded-xl border border-line p-4">
            <p className="text-sm">
              题库：<span className="font-medium">{bank.questions}</span> 题 ·{" "}
              <span className="font-medium">{bank.options}</span> 个选项 ·{" "}
              <span className="font-medium">{bank.knowledge_points}</span> 个知识点
            </p>
            {/* ⚠️ 指纹只给前 8 位：完整 64 位十六进制在手机上换行很难看，
                而"是不是同一个包"这个判断前 8 位就够（本来就是给人眼比对用的）。 */}
            <p className="mt-1 text-xs text-sub">
              题库指纹 {bank.bank_version.slice(0, 8)} · 导入于{" "}
              {new Date(bank.exported_at).toLocaleString("zh-CN")}
            </p>
          </div>
          <Link
            href="/practice"
            data-entry="practice"
            className="mt-4 flex min-h-touch items-center justify-center rounded-xl bg-brand px-4 text-sm text-brand-fg"
          >
            开始练习
          </Link>
          <Link href="/setup" className="mt-3 block text-center text-sm text-sub">
            换一个题库
          </Link>
        </section>
      )}
    </main>
  );
}
