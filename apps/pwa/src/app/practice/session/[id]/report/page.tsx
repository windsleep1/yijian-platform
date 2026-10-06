"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { SessionReport } from "@/lib/types";

/**
 * 结果页（**P2c-1**：总分 / 正确率 / 用时 / 知识点分布）。
 *
 * 路由在 `(tabs)` **之外**，与答题页同规矩：结果要整屏看，底下挂一条 Tab 栏只会让人误点走。
 *
 * ★★ **这个页面的三条判据**（都是"数据从哪来"的判据）：
 *
 * ① **正确率可以是 `null`** —— 后端「零分母返 `null`」是本项目红线。
 *    前端因此**必须**把 `null` 显示成「—」，而不是 `0%`：
 *    一道题都没答 与 全答错了 在界面上长得一样，是**产品缺陷**（用户会以为自己全错）。
 *
 * ② **用时来自服务端**（`duration_sec` = `finished_at - started_at`，后端算）。
 *    前端不自己减时间戳 —— 那会把客户端时钟不准引进来，而且服务端算的才是**唯一**口径。
 *
 * ③ **知识点分布的顺序不由前端决定**：后端已按正确率**升序**（最弱的在前）。
 *    前端**不重排** —— 一旦重排，两边就各有一套顺序，而"哪套是对的"没人说得清。
 *
 * ⚠️ 报告在 `status === 'doing'` 时也拿得到（后端不设更严的准入，硬约定 A）——
 *    所以这里要处理"没交卷"这个状态，而不是假装它不存在。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** 秒 → `mm:ss`（超过一小时才显示小时位 —— 练习不会超过一小时，别把界面撑宽）。 */
function mmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const rest = s % 60;
  if (m >= 60) return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
  return `${m}:${String(rest).padStart(2, "0")}`;
}

/** ⚠️ `null` ⇒ 「—」。**绝不要** `?? 0` —— 那正是"零分母返 null"要防的显示事故。 */
function pct(v: number | null): string {
  return v === null ? "—" : `${Math.round(v * 100)}%`;
}

/** 正确率的颜色档。**只用于"该不该补这里"的提示**，不参与任何计算。 */
function tone(v: number | null): string {
  if (v === null) return "bg-line text-sub";
  if (v >= 0.8) return "bg-green-600 text-white";
  if (v >= 0.5) return "bg-amber-500 text-white";
  return "bg-red-600 text-white";
}

export default function PracticeReportPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [rep, setRep] = useState<SessionReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setBusy(true);
    request<SessionReport>(`/practice/sessions/${id}/report`)
      .then((d) => {
        if (!alive) return;
        setRep(d);
      })
      .catch((e) => alive && setErr(errText(e)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [id]);

  /** 「再练一遍」：**新的一次练习**（不是重做同一份）。
   *
   * ★ 后端抽题是**确定性**的（未做过的优先、其次按 id）—— 所以"再练一遍"会抽到
   *   与上次不同的题（上次那些已经做过了）。这正是我们要的：练的是这一章，不是这一份卷。 */
  const again = async () => {
    if (!rep) return;
    setBusy(true);
    setErr("");
    try {
      const r = await request<{ id: string }>("/practice/sessions", {
        method: "POST",
        body: {
          subject_id: rep.subject_id,
          chapter_id: rep.chapter_id,
          count: Math.min(100, Math.max(1, rep.total || 10)),
        },
      });
      router.push(`/practice/session/${r.id}`);
    } catch (e) {
      setErr(errText(e));
      setBusy(false);
    }
  };

  if (err) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p
          data-report-error
          className="rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700"
        >
          {err}
        </p>
        <Link href="/practice" className="mt-6 block text-center text-sm text-brand underline">
          回练习
        </Link>
      </main>
    );
  }

  if (!rep) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p className="text-sm text-sub">加载中…</p>
      </main>
    );
  }

  const notFinished = rep.status !== "finished";

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <Link href="/practice" className="text-xs text-sub underline">
        ← 练习
      </Link>
      <h1 className="mt-3 text-lg font-semibold">{rep.title || "练习报告"}</h1>
      <p className="mt-1 text-xs text-sub">
        {[rep.subject_name, rep.chapter_name].filter(Boolean).join(" · ") || "—"}
      </p>

      {notFinished && (
        <p
          data-report-banner="doing"
          className="mt-4 rounded-xl border border-dashed border-line p-3 text-xs text-sub"
        >
          这次练习还没交卷 —— 下面的数字是**到目前为止**的。
          <Link href={`/practice/session/${rep.id}`} className="ml-1 text-brand underline">
            接着练
          </Link>
        </p>
      )}

      {/* 得分 */}
      <section className="mt-5 rounded-2xl border border-line p-5 text-center">
        <p data-report-score className="text-4xl font-semibold">
          {rep.score}
          <span className="ml-1 text-sm font-normal text-sub">分</span>
        </p>
        <p className="mt-1 text-xs text-sub">
          答对 {rep.correct} / {rep.answered} 题（共抽 {rep.total} 题）
        </p>
      </section>

      {/* 三个数 */}
      <section className="mt-4 grid grid-cols-3 gap-3 text-center">
        <div className="rounded-xl border border-line py-3">
          <p data-report-accuracy className="text-lg font-semibold">
            {pct(rep.accuracy)}
          </p>
          <p className="mt-0.5 text-xs text-sub">正确率</p>
        </div>
        <div className="rounded-xl border border-line py-3">
          <p data-report-duration className="text-lg font-semibold">
            {mmss(rep.duration_sec)}
          </p>
          <p className="mt-0.5 text-xs text-sub">用时</p>
        </div>
        <div className="rounded-xl border border-line py-3">
          <p data-report-answered className="text-lg font-semibold">
            {rep.answered}/{rep.total}
          </p>
          <p className="mt-0.5 text-xs text-sub">已答</p>
        </div>
      </section>

      {/* 知识点分布 */}
      <section className="mt-6">
        <h2 className="text-sm font-medium">知识点分布</h2>
        <p className="mt-1 text-xs text-sub">
          按正确率<strong>从低到高</strong> —— 最上面的是这次最该补的。
        </p>
        {rep.by_kp.length === 0 ? (
          <p
            data-report-kp="empty"
            className="mt-3 rounded-xl border border-dashed border-line p-3 text-xs text-sub"
          >
            还没有已作答的题，所以没有知识点分布。
          </p>
        ) : (
          <ul data-report-kp="list" className="mt-3 space-y-3">
            {rep.by_kp.map((k) => (
              <li key={k.knowledge_point_id ?? k.name}>
                <div className="flex items-baseline justify-between text-sm">
                  <span className="flex-1 pr-2">{k.name}</span>
                  <span className="shrink-0 text-xs text-sub">
                    {k.correct}/{k.total} · {pct(k.accuracy)}
                  </span>
                </div>
                {/* 条形宽度 = 正确率本身（不是"掌握度"之类的推算值 —— 那种数没人能验） */}
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-line">
                  <span
                    className={`block h-full rounded-full ${tone(k.accuracy)}`}
                    style={{ width: `${Math.round((k.accuracy ?? 0) * 100)}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 出口 */}
      <div className="mt-8 space-y-3">
        {rep.chapter_id && (
          <button
            type="button"
            data-report-again
            disabled={busy}
            onClick={() => void again()}
            className="min-h-touch w-full rounded-xl bg-brand text-center text-sm font-medium text-white disabled:opacity-45"
          >
            {busy ? "正在建新的练习…" : "再练一遍（新抽一批）"}
          </button>
        )}
        <Link
          href="/practice"
          className="min-h-touch block w-full rounded-xl border border-line text-center text-sm leading-[3rem]"
        >
          回练习
        </Link>
      </div>
    </main>
  );
}
