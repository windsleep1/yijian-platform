"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { Chapter, Subject } from "@/lib/types";

/**
 * 练习（Tab 2）—— **P2b-1：选科目 → 选章节 → 建练习**。
 *
 * ★ 本批的验收判据就是"**数据流跑通**"（用户 2026-09-29 的原话）：
 *   后端到前端完整往返一次，**先跑通再加交互**。
 *   所以下面**刻意不做**的事，都在界面上写明"在 P2b-2"，而不是留白 ——
 *   留白在新人眼里和"坏了"分不清（P2a 的 `practice/page.tsx` 已经守过同一条）。
 */

/** 统一的错误文案：**带上业务码与 trace_id** —— 用户截图报障时这两个字段是唯一线索。 */
function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** 一次抽多少题。**写成一个常量**，别散在 JSX 里（P2b-2 要把它做成可选项）。 */
const SESSION_SIZE = 10;

export default function PracticePage() {
  const router = useRouter();
  const [subjects, setSubjects] = useState<Subject[] | null>(null);
  const [subject, setSubject] = useState<Subject | null>(null);
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    request<Subject[]>("/subjects")
      .then((d) => alive && setSubjects(d))
      .catch((e) => alive && setErr(errText(e)));
    return () => {
      alive = false;
    };
  }, []);

  const pickSubject = useCallback(async (s: Subject) => {
    setSubject(s);
    setChapters(null);
    setErr("");
    setBusy(true);
    try {
      setChapters(await request<Chapter[]>(`/subjects/${s.id}/chapters`));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(
    async (c: Chapter) => {
      if (!subject || busy) return;
      setBusy(true);
      setErr("");
      try {
        const d = await request<{ id: string }>("/practice/sessions", {
          method: "POST",
          body: { subject_id: subject.id, chapter_id: c.id, count: SESSION_SIZE },
        });
        // ★ 成功后**不**把 busy 复位：这一刻到路由切换之间按钮若变回可点，
        //   用户会连点两次 ⇒ 建出两个 session（硬约定 D 的同族：待定期间必须禁用）。
        router.push(`/practice/session/${d.id}`);
      } catch (e) {
        setErr(errText(e));
        setBusy(false);
      }
    },
    [subject, busy, router],
  );

  const publicOnes = (subjects ?? []).filter((s) => s.category === "public");
  const proOnes = (subjects ?? []).filter((s) => s.category !== "public");

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">练习</h1>
      <p className="mt-1 text-sm text-sub">选一门科目，再选章节，开始刷题</p>
      {/* ★ 错题本（P2c-2）：入口放在**科目列表之上** —— 它的位置比「再选一次科目」
          更靠前，因为"回看我错过的题"比"再刷一批新题"更常用。 */}
      <Link
        href="/practice/wrong"
        data-entry="wrong-book"
        className="mt-4 flex min-h-touch items-center justify-between rounded-xl border border-line px-4 text-sm"
      >
        <span>错题本</span>
        <span className="text-sub">看我错过的题 →</span>
      </Link>

      {err && (
        <p className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {err}
        </p>
      )}

      {/* ---- 第 1 步：选科目 ---- */}
      <section className="mt-6">
        <h2 className="text-sm font-medium text-sub">1 · 选科目</h2>
        {subjects === null && !err && <p className="mt-3 text-sm text-sub">加载中…</p>}
        {[
          { title: "公共课", items: publicOnes },
          { title: "专业课", items: proOnes },
        ].map((g) =>
          g.items.length === 0 ? null : (
            <div key={g.title} className="mt-3">
              <p className="text-xs text-sub">{g.title}</p>
              <ul className="mt-2 flex flex-wrap gap-2">
                {g.items.map((s) => {
                  const active = subject?.id === s.id;
                  return (
                    <li key={s.id}>
                      <button
                        type="button"
                        onClick={() => void pickSubject(s)}
                        aria-pressed={active}
                        className={`min-h-touch rounded-full border px-3 text-sm ${
                          active ? "border-brand bg-brand/10 text-brand" : "border-line text-ink"
                        }`}
                      >
                        {s.short_name ?? s.name}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ),
        )}
      </section>

      {/* ---- 第 2 步：选章节 ---- */}
      {subject && (
        <section className="mt-8">
          <h2 className="text-sm font-medium text-sub">2 · 选章节</h2>
          {busy && chapters === null && <p className="mt-3 text-sm text-sub">加载章节…</p>}
          {chapters !== null && chapters.length === 0 && (
            <p className="mt-3 text-sm text-sub">这门科目还没有章节。</p>
          )}
          <ul className="mt-3 space-y-2">
            {(chapters ?? []).map((c) => {
              // ★ 判据是 `question_count`（后端**实时算**，不是那个没人刷的冗余列）。
              //   0 题的章节必须点不动 —— 让用户点进去再报 404 是更差的体验。
              const empty = c.question_count === 0;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    disabled={busy || empty}
                    onClick={() => void start(c)}
                    style={{ paddingLeft: `${0.75 + (c.level - 1) * 1}rem` }}
                    className="flex min-h-touch w-full items-center justify-between rounded-xl border border-line px-3 py-2 text-left disabled:opacity-45"
                  >
                    <span className="pr-2 text-sm">
                      {c.name}
                      {c.outline_ref && (
                        <span className="ml-2 text-xs text-sub">{c.outline_ref}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-sub">
                      {empty
                        ? "暂无题目"
                        : c.my_answered > 0
                          ? `做过 ${c.my_answered} / ${c.question_count}`
                          : `${c.question_count} 题`}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {busy && <p className="mt-3 text-sm text-sub">正在创建练习…</p>}
        </section>
      )}

      <p className="mt-8 rounded-xl border border-dashed border-line p-4 text-xs text-sub">
        本批（P2b-1）只做到「建好练习 + 答第一题」。
        <br />
        切题 / 答题卡 / 交卷报告 / 错题本**已落地**（P2b-2a·2b / P2c-1 / P2c-2）； 长按标记 / 收藏 /
        笔记 见 **BL-21**（个人 PWA 时）。
      </p>

      <Link href="/" className="mt-6 block text-center text-sm text-brand">
        先回首页
      </Link>
    </main>
  );
}
