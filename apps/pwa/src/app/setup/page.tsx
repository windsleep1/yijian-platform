"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  bankSummary,
  importBank,
  peekBankMeta,
  recoverInterruptedImport,
  type BankMeta,
  type BankSummary,
  type ImportProgress,
} from "@/lib/api";

/**
 * 导入题库（`/setup`）—— **PWA 的入口**，也是本批唯一的"新页面"。
 *
 * ## 三条设计判据（都来自方案 §4 / §4.1）
 *
 * ① **空态要能进**：没题库也打得开（它本来就是首页的落点），
 *    否则"装不上，也就没有 PWA"。
 * ② **可中断重入**：写之前先落哨兵、写完（含对账）才清 —— 由 `importBank` 负责；
 *    本页只负责把"上次没导完"这件事**说给用户听**（`recoverInterruptedImport` 的返回值）。
 * ③ ★★ **换题库 = 清空所有数据，且必须二次确认**：这一条是**本页最重要**的部分。
 *    判断用的是**指纹**（新包的 `bank_version` 与库里的比）：
 *      · 一样 ⇒ 只提供「重新导入」（没有数据会被"清掉换新"）
 *      · 不一样 ⇒ 弹确认框，**逐个列出会消失的东西**，并要求先备份
 *    ★ 为什么不能省：换包时 `question_id` 对不上，旧的答题记录/错题/收藏/标记/笔记
 *      会指向**不存在的题** —— 界面上表现为**空题干行**，而且**不报错**。
 *
 * ⚠️ 「传到手机」这一步**无法自动化**（AirDrop / 微信文件传输 / U 盘都行），
 *    如实写在页面上，不假装有一个"一键"。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

type Stage =
  | { k: "idle" }
  | { k: "confirmReplace"; file: File; next: BankMeta }
  | { k: "confirmReimport"; file: File; next: BankMeta }
  | { k: "importing"; progress: ImportProgress }
  | { k: "done"; questions: number; options: number; version: string };

export default function SetupPage() {
  const [bank, setBank] = useState<BankSummary | null>(null);
  const [stage, setStage] = useState<Stage>({ k: "idle" });
  const [err, setErr] = useState("");
  const [recovered, setRecovered] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setRecovered(await recoverInterruptedImport());
    setBank(await bankSummary());
  }, []);

  useEffect(() => {
    refresh().catch((e) => setErr(errText(e)));
  }, [refresh]);

  const run = useCallback(
    async (file: File, force: boolean) => {
      setErr("");
      try {
        const r = await importBank(
          file,
          (progress) => setStage({ k: "importing", progress }),
          force,
        );
        await refresh();
        setStage({
          k: "done",
          questions: r.questions,
          options: r.options,
          version: r.bank_version,
        });
      } catch (e) {
        setErr(errText(e));
        setStage({ k: "idle" });
        await refresh().catch(() => {});
      } finally {
        // 让"选同一个文件"能再次触发 change（否则改完文件再选它不会响）
        if (fileRef.current) fileRef.current.value = "";
      }
    },
    [refresh],
  );

  /** 选完文件：**先只看元信息**，再决定要不要问用户（`importBank` 一进去就清库）。 */
  const onPick = useCallback(
    async (file: File) => {
      setErr("");
      try {
        const next = await peekBankMeta(file);
        if (bank === null) {
          // 首次导入：没有任何东西会被清掉 ⇒ **不弹确认**（弹了反而像在吓人）
          setStage({ k: "importing", progress: { phase: "reading", written: 0, total: 1 } });
          await run(file, false);
          return;
        }
        // ★ 指纹相同 = 同一个包 ⇒ 只提供「重新导入」；不同 = 换题库 ⇒ 走破坏性确认
        setStage(
          next.bank_version === bank.bank_version
            ? { k: "confirmReimport", file, next }
            : { k: "confirmReplace", file, next },
        );
      } catch (e) {
        setErr(errText(e));
        setStage({ k: "idle" });
      }
    },
    [bank, run],
  );

  const busy = stage.k === "importing";
  const pct =
    stage.k === "importing" && stage.progress.total > 0
      ? Math.min(100, Math.round((stage.progress.written / stage.progress.total) * 100))
      : 0;

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">导入题库</h1>
      <p className="mt-1 text-sm text-sub">
        需要一个 <code className="text-xs">pwa-bank.json</code> 文件
      </p>

      {recovered && (
        <p
          data-import-recovered="1"
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800"
        >
          上次的导入没有完成，已经清掉那半份数据。请重新导入。
        </p>
      )}

      {err && (
        <p
          data-setup-error="1"
          className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700"
        >
          {err}
        </p>
      )}

      <section className="mt-6 rounded-xl border border-line p-4">
        <p className="text-xs text-sub">当前题库</p>
        {bank === null ? (
          <p className="mt-1 text-sm" data-bank-state="none">
            还没有导入过
          </p>
        ) : (
          <p className="mt-1 text-sm" data-bank-state="ready">
            <span className="font-medium">{bank.questions}</span> 题 · 指纹{" "}
            {bank.bank_version.slice(0, 8)} · 导入于{" "}
            {new Date(bank.exported_at).toLocaleString("zh-CN")}
          </p>
        )}
      </section>

      {/* ---------------- 选文件 ---------------- */}
      <label className="mt-6 block text-sm font-medium">选择题库文件</label>
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        data-setup-file="1"
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void onPick(f);
        }}
        className="mt-2 block w-full text-sm"
      />

      {/* ---------------- 进度 ---------------- */}
      {stage.k === "importing" && (
        <div className="mt-6" data-setup-progress="1">
          <div className="h-2 w-full overflow-hidden rounded-full bg-line">
            <div
              className="h-full bg-brand transition-all"
              style={{ width: `${pct}%` }}
              data-progress-pct={pct}
            />
          </div>
          <p className="mt-2 text-xs text-sub">
            {stage.progress.phase === "reading" && "读取文件…"}
            {stage.progress.phase === "clearing" && "清空旧数据…"}
            {stage.progress.phase === "writing" &&
              `写入中 ${stage.progress.written} / ${stage.progress.total}`}
            {stage.progress.phase === "verifying" && "对账中（核对题量 / 选项数 / 科目分布）…"}
            {stage.progress.phase === "done" && "完成"}
          </p>
        </div>
      )}

      {/* ---------------- 换题库的二次确认（★ 本页最重要的一块） ---------------- */}
      {stage.k === "confirmReplace" && (
        <div
          data-setup-confirm="replace"
          className="mt-6 rounded-xl border border-red-300 bg-red-50 p-4"
        >
          <p className="text-sm font-medium text-red-800">这会清空你的全部数据</p>
          <dl className="mt-3 space-y-1 text-sm text-red-900">
            <div className="flex justify-between gap-4">
              <dt className="text-sub">当前题库</dt>
              <dd className="font-mono">{bank?.bank_version.slice(0, 8)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-sub">准备换成</dt>
              <dd className="font-mono">{stage.next.bank_version.slice(0, 8)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-sub">新包题量</dt>
              <dd>{stage.next.totals.questions} 题</dd>
            </div>
          </dl>
          <p className="mt-3 text-sm text-red-900">
            两个题库的题目编号不一定对得上，所以下面的东西会**全部消失且无法恢复**：
            答题记录、错题本、收藏、标记、笔记。
          </p>
          <p className="mt-2 text-sm font-medium text-red-900">
            建议先到「我的 → 设置 → 导出全部数据」备份。
          </p>
          <div className="mt-4 flex gap-3">
            <button
              type="button"
              data-confirm="cancel"
              onClick={() => setStage({ k: "idle" })}
              className="min-h-touch flex-1 rounded-xl border border-line bg-page text-sm"
            >
              取消
            </button>
            <button
              type="button"
              data-confirm="replace"
              onClick={() => {
                setStage({ k: "importing", progress: { phase: "reading", written: 0, total: 1 } });
                void run(stage.file, true);
              }}
              className="min-h-touch flex-1 rounded-xl bg-danger px-4 text-sm text-white"
            >
              清空并导入
            </button>
          </div>
        </div>
      )}

      {stage.k === "confirmReimport" && (
        <div data-setup-confirm="reimport" className="mt-6 rounded-xl border border-line p-4">
          <p className="text-sm">这就是当前题库（指纹相同）。要重新导入一遍吗？</p>
          <p className="mt-1 text-xs text-sub">
            重新导入会清空数据再写入 —— 同一个包，题目编号是对得上的。
          </p>
          <div className="mt-4 flex gap-3">
            <button
              type="button"
              data-confirm="cancel"
              onClick={() => setStage({ k: "idle" })}
              className="min-h-touch flex-1 rounded-xl border border-line text-sm"
            >
              取消
            </button>
            <button
              type="button"
              data-confirm="reimport"
              onClick={() => {
                setStage({ k: "importing", progress: { phase: "reading", written: 0, total: 1 } });
                void run(stage.file, true);
              }}
              className="min-h-touch flex-1 rounded-xl bg-brand px-4 text-sm text-brand-fg"
            >
              重新导入
            </button>
          </div>
        </div>
      )}

      {stage.k === "done" && (
        <div data-setup-done="1" className="mt-6 rounded-xl border border-line p-4">
          <p className="text-sm font-medium">导入完成，对账通过</p>
          <p className="mt-1 text-sm text-sub">
            {stage.questions} 题 · {stage.options} 个选项 · 指纹 {stage.version.slice(0, 8)}
          </p>
          <Link
            href="/practice"
            className="mt-4 flex min-h-touch items-center justify-center rounded-xl bg-brand px-4 text-sm text-brand-fg"
          >
            去练习
          </Link>
        </div>
      )}

      {/* ---------------- 怎么拿到这个文件（如实写，不假装一键） ---------------- */}
      <section className="mt-8 rounded-xl border border-line p-4 text-xs text-sub">
        <p className="font-medium text-ink">怎么拿到 pwa-bank.json</p>
        <ol className="mt-2 list-decimal space-y-1 pl-4">
          <li>
            在电脑上跑 <code>python tools/pwa/export-bank.py</code>，产出{" "}
            <code>data/seed/pwa-bank.json</code>
          </li>
          <li>把这个文件传到手机（AirDrop / 微信文件传输 / U 盘 / 网盘都行）</li>
          <li>回到本页，选它</li>
        </ol>
        <p className="mt-2">
          ★ 「传到手机」这一步没有自动化 —— 这台设备是离线的，没有可下载的来源。
        </p>
      </section>

      <Link href="/" className="mt-6 block text-center text-sm text-brand">
        回首页
      </Link>
    </main>
  );
}
