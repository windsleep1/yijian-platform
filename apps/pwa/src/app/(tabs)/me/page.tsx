"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, bankSummary, type BankSummary } from "@/lib/api";

/**
 * 我的（Tab 3）—— 本地该有的三样，**没有账号相关**。
 *
 * ★ 本页**不是** `apps/web` 那页的复制品（它是**分叉**）：
 *   C 端那页是「资料 + 引导 + 退出登录」，全是账号相关的东西 —— 而本应用
 *   **没有账号**（单机单用户）。⇒ 分叉理由**可判定**：
 *   「它 import 了 `@/lib/auth-store` 并调 `GET /auth/me`，而本应用既没有
 *     auth-store，也没有这个接口」。
 *
 * ## 三条文案纪律（每一条对应一个"用户会做错事"的场景）
 *
 * 1. ★★ **「换题库」与「数据备份」是两个不同动作**，必须一眼分得清：
 *    · **换题库** = 换掉整份**题库**（题目 / 章节 / 知识点）；
 *    · **数据备份** = 我的**记录**（答题 / 错题 / 收藏 / 标记 / 笔记）。
 *    ⇒ 所以「换题库」放在**题库信息**那一块（它属于题库），
 *      而「数据备份」是独立一张卡（它属于我）。
 *      把两者并排放，用户就会以为"备份 = 换题库前先存一份题库"。
 *
 * 2. ★ **不显示"最后同步时间"** —— 本应用**没有同步**（零后端是它的设计前提）。
 *    为了"看起来完整"引入一个**不存在的概念**，比少一个字段糟得多
 *    （用户看到"最后同步"会以为数据在云端有副本 —— 那正是备份存在的意义）。
 *
 * 3. 版本显示**短 hash**（前 8 位）：完整指纹是 64 位十六进制，**没人会读**，
 *    而短 hash 足够回答"我这份是不是同一个"。
 */
export default function MePage() {
  const [bank, setBank] = useState<BankSummary | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      setBank(await bankSummary());
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">我的</h1>
      <p className="mt-1 text-sm text-sub">数据都在这台设备上</p>

      {err ? (
        <p role="alert" className="mt-6 text-sm text-danger">
          {err}
        </p>
      ) : null}

      {/* ★ 我的收藏 / 标记（P2c-4）—— 与 C 端同路由（`docs/02` 页 35 的 IA）。 */}
      <Link
        href="/me/favorites"
        data-entry="favorites"
        className="min-h-touch mt-8 flex w-full items-center justify-between rounded-lg border border-line px-4 font-medium"
      >
        <span>我的收藏 / 标记</span>
        <span className="text-sub">→</span>
      </Link>

      {/* ★ 我的笔记（P2c-5）—— 同按 IA（`docs/02` 页 36）放 `/me/*` 下。 */}
      <Link
        href="/me/notes"
        data-entry="notes"
        className="min-h-touch mt-3 flex w-full items-center justify-between rounded-lg border border-line px-4 font-medium"
      >
        <span>我的笔记</span>
        <span className="text-sub">→</span>
      </Link>

      {/* ★ 数据备份 —— **与"换题库"分开**（见抬头纪律 1）。 */}
      <Link
        href="/me/backup"
        data-entry="backup"
        className="min-h-touch mt-3 flex w-full items-center justify-between rounded-lg border border-line px-4 font-medium"
      >
        <span>数据备份</span>
        <span className="text-sub">导出 / 恢复我的记录 →</span>
      </Link>

      {/* ---------------------------------------------------------- 题库信息 */}
      <h2 className="mt-10 text-sm font-medium text-sub">题库信息</h2>
      {!loaded ? (
        <p className="mt-2 text-sm text-sub">加载中…</p>
      ) : bank === null ? (
        <div className="mt-3 rounded-lg border border-line p-4">
          <p className="text-sm">还没有题库。</p>
          <Link
            href="/setup"
            data-entry="import-bank"
            className="min-h-touch mt-3 flex w-full items-center justify-center rounded-lg border border-line font-medium"
          >
            去导入题库
          </Link>
        </div>
      ) : (
        <>
          <dl className="mt-2 space-y-2 text-sm">
            <div className="flex justify-between border-b border-line py-2">
              <dt className="text-sub">题库版本</dt>
              {/* ★ 短 hash：完整指纹 64 位十六进制，没人会读（抬头纪律 3） */}
              <dd className="font-mono" data-bank-version>
                {bank.bank_version.slice(0, 8)}
              </dd>
            </div>
            <div className="flex justify-between border-b border-line py-2">
              <dt className="text-sub">题目数量</dt>
              <dd data-bank-questions>{bank.questions}</dd>
            </div>
            <div className="flex justify-between border-b border-line py-2">
              <dt className="text-sub">科目数</dt>
              <dd data-bank-subjects>{bank.subjects}</dd>
            </div>
            <div className="flex justify-between py-2">
              {/* ★ 如实命名：这是**题库包生成**的时间，不是"我导进来的时间" ——
                  后者本应用**没有记录**（`meta` 里只有包的指纹与导入哨兵）。
                  ★ 不把它写成"导入时间"：那是**两个不同的时间**，
                    混用会让用户在排障时对不上号。 */}
              <dt className="text-sub">题库包生成于</dt>
              <dd data-bank-exported>{bank.exported_at.slice(0, 10)}</dd>
            </div>
          </dl>

          {/* ★ 「换题库」——**属于题库那一块**，不放在"数据"里（抬头纪律 1）。
              它跳到 `/setup`（那里有完整的"选文件 → 二次确认 → 清空替换"流程）。 */}
          <Link
            href="/setup"
            data-entry="change-bank"
            className="min-h-touch mt-4 flex w-full items-center justify-center rounded-lg border border-line font-medium"
          >
            换题库（会清空全部数据）
          </Link>
        </>
      )}
    </main>
  );
}
