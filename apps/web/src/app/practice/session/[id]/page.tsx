"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { AnswerResult, PracticeSession, SessionItem } from "@/lib/types";

/**
 * 答题页（**P2b-1：显示当前题 → 选择 → 提交判分 → 显示解析**）。
 *
 * 路由在 `(tabs)` **之外**：答题应该是整屏的，底下挂一条 Tab 栏只会让人误点走
 * （而且走了之后"当前进度"的语义就含混了）。这与 `/onboarding` 的处理一致。
 *
 * ★★ **"刷新后还在"是怎么成立的**（验收判据之一）：
 *   页面 id 在 URL 里（`/practice/session/<id>`），刷新只会重新 `GET` 一次同一条记录；
 *   而"当前该答哪一题"由后端的 `current_item_id` 决定 —— 前端**不自己存游标**。
 *   判据：把 localStorage 清干净再刷新，仍然停在同一题（游标不在前端）。
 *
 * ★ **幂等与"重复提交"**：如果这一题**已经答过**（`item.answered`），
 *   页面直接渲染既有结果，**不提供再次提交** —— 后端也拦（幂等分支零写入），
 *   两层都在，是因为"前端能点"与"后端能接"是两件事。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** 判分结果。"刚提交的"与"刷新后从题目里读回来的"归一成同一个形状。 */
type Revealed = {
  is_correct: boolean;
  score: number;
  answer: unknown[];
  analysis: string | null;
  /** 刚提交、且后端告诉我们它其实早就答过（`idempotent`）。 */
  idempotent: boolean;
};

function fromItem(it: SessionItem): Revealed | null {
  if (!it.answered) return null;
  return {
    is_correct: it.is_correct === true,
    score: it.score ?? 0,
    answer: it.answer?.value ?? [],
    analysis: it.analysis,
    idempotent: false,
  };
}

function show(v: unknown): string {
  if (typeof v === "boolean") return v ? "正确" : "错误";
  return String(v);
}

/** 已选值 → 提交体。**判断题传布尔、选择题传标号数组**（后端按题型校验）。 */
function toValue(it: SessionItem, picked: string[], judge: boolean | null): unknown[] | null {
  if (it.type === "judge") return judge === null ? null : [judge];
  if (it.type === "single") return picked.length === 1 ? picked : null;
  if (it.type === "multiple") return picked.length > 0 ? [...picked].sort() : null;
  return null;
}

export default function PracticeSessionPage() {
  const { id } = useParams<{ id: string }>();
  const [sess, setSess] = useState<PracticeSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [judge, setJudge] = useState<boolean | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  /**
   * ★★ **当前题由它定，不由 `sess.current_item_id` 现算** —— 这是本轮修掉的一个真 bug。
   *
   * 游标只在**首次加载**时定一次：断点恢复 = 回到第一道没答的题。
   * 提交之后**不重定** —— 第一版是"提交后刷新 session，游标由后端现算"，
   * 于是答完第 1 题、页面**立刻跳到第 2 题**：判分结论与解析一闪而过，
   * **用户根本看不到解析**（而"显示解析"正是本批的验收判据之一）。
   *
   * 判据：**"刷新数据"与"移动游标"是两件事** —— 进度（已答 N/M）该跟着后端走，
   * 游标该跟着用户的操作走。把它们绑在同一个状态上，就会出现这种"数据对了、界面错了"。
   * （切题是 P2b-2 的事；到那时**由用户动作**改游标，而不是由一次 fetch 的副产品改。）
   */
  const [cursor, setCursor] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setBusy(true);
    request<PracticeSession>(`/practice/sessions/${id}`)
      .then((d) => {
        if (!alive) return;
        setSess(d);
        setCursor(
          d.current_item_id ??
            d.items.find((x) => !x.answered)?.item_id ??
            d.items[0]?.item_id ??
            null,
        );
      })
      .catch((e) => alive && setErr(errText(e)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [id]);

  const current: SessionItem | null =
    sess === null || cursor === null
      ? null
      : (sess.items.find((it) => it.item_id === cursor) ?? sess.items[0] ?? null);

  // 换题就清空选择与结果 —— 否则上一题的解析会挂在这一题下面（很难发现的错位）。
  // ⚠️ 依赖是 `cursor`（**用户动作**），不是 `sess` —— 提交后那次 refresh 不该重置界面。
  useEffect(() => {
    setPicked([]);
    setJudge(null);
    setRevealed(current ? fromItem(current) : null);
    // current 是由 cursor + sess 派生的；用 cursor 当依赖才不会每次 refresh 都重置
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor, sess?.id]);

  const toggle = useCallback(
    (label: string) => {
      if (revealed || busy) return;
      setPicked((prev) =>
        current?.type === "multiple"
          ? prev.includes(label)
            ? prev.filter((x) => x !== label)
            : [...prev, label]
          : [label],
      );
    },
    [revealed, busy, current?.type],
  );

  const submit = useCallback(async () => {
    if (!current || !sess || busy || revealed) return;
    const value = toValue(current, picked, judge);
    if (value === null) {
      setErr("请先选择一个答案");
      return;
    }
    setErr("");
    setBusy(true);
    try {
      const r = await request<AnswerResult>(`/practice/sessions/${sess.id}/answer`, {
        method: "POST",
        body: { item_id: current.item_id, value },
      });
      setRevealed({
        is_correct: r.is_correct,
        score: r.score,
        answer: r.correct_answer.value,
        analysis: r.analysis,
        idempotent: r.idempotent,
      });
      // 提交后**同步刷新一次 session** —— 进度（已答 X/N）与"下一道该答哪题"
      // 都由后端说了算，前端不自己推（两处各自推算必然会漂）。
      setSess(await request<PracticeSession>(`/practice/sessions/${sess.id}`));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }, [current, sess, busy, revealed, picked, judge]);

  const supported = current !== null && ["single", "multiple", "judge"].includes(current.type);
  const canSubmit = !busy && !revealed && supported && toValue(current!, picked, judge) !== null;

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <div className="flex items-center justify-between">
        <Link href="/practice" className="text-sm text-brand">
          ← 换章节
        </Link>
        {sess && (
          <span className="text-xs text-sub">
            已答 {sess.answered} / {sess.total}
          </span>
        )}
      </div>

      {err && (
        <p className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          {err}
        </p>
      )}

      {sess === null && !err && <p className="mt-8 text-sm text-sub">加载中…</p>}

      {sess && current && (
        <>
          <h1 className="mt-4 text-lg font-semibold">
            {sess.chapter_name ?? sess.subject_name ?? "练习"}
          </h1>
          <p className="mt-1 text-xs text-sub">
            第 {current.seq} 题 / 共 {sess.total} 题 ·{" "}
            {current.type === "single"
              ? "单选"
              : current.type === "multiple"
                ? "多选"
                : current.type === "judge"
                  ? "判断"
                  : current.type}
          </p>

          <p className="mt-5 whitespace-pre-wrap text-base leading-relaxed">{current.stem}</p>

          {!supported && (
            <p className="mt-4 rounded-lg border border-dashed border-line p-3 text-sm text-sub">
              这类题（{current.type}）暂不支持在线判分。
            </p>
          )}

          {supported && (
            <ul className="mt-5 space-y-2">
              {current.type === "judge"
                ? [
                    { label: "正确", value: true },
                    { label: "错误", value: false },
                  ].map((o) => {
                    const active = judge === o.value;
                    return (
                      <li key={o.label}>
                        <button
                          type="button"
                          disabled={busy || revealed !== null}
                          aria-pressed={active}
                          onClick={() => setJudge(o.value)}
                          className={`min-h-touch w-full rounded-xl border px-4 text-left text-sm disabled:opacity-60 ${
                            active ? "border-brand bg-brand/10 text-brand" : "border-line"
                          }`}
                        >
                          {o.label}
                        </button>
                      </li>
                    );
                  })
                : current.options.map((o) => {
                    const active = picked.includes(o.label);
                    // 判完分之后把"我选的"和"正确答案"分别标出来 —— 只标对错
                    // 而不给正确答案，用户还得自己回头数标号（体验 + 判据都不合格）。
                    const isRight =
                      revealed !== null && revealed.answer.some((x) => show(x) === o.label);
                    const isMine = revealed !== null && picked.includes(o.label);
                    return (
                      <li key={o.label}>
                        <button
                          type="button"
                          disabled={busy || revealed !== null}
                          aria-pressed={active}
                          onClick={() => toggle(o.label)}
                          className={`flex min-h-touch w-full items-start gap-3 rounded-xl border px-3 py-2 text-left text-sm disabled:opacity-60 ${
                            active && revealed === null ? "border-brand bg-brand/10" : "border-line"
                          } ${isRight ? "border-green-500" : ""}`}
                        >
                          <span className="mt-0.5 shrink-0 font-medium">{o.label}</span>
                          <span className="flex-1 whitespace-pre-wrap">{o.content}</span>
                          {revealed !== null && (
                            <span className="shrink-0 text-xs text-sub">
                              {isRight ? "正确答案" : isMine ? "我选的" : ""}
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
            </ul>
          )}

          {revealed === null ? (
            <button
              type="button"
              disabled={!canSubmit}
              onClick={() => void submit()}
              className="mt-6 min-h-touch w-full rounded-xl bg-brand text-center font-medium text-white disabled:opacity-45"
            >
              {busy ? "提交中…" : "提交"}
            </button>
          ) : (
            <section className="mt-6 rounded-xl border border-line p-4">
              <p
                className={`font-medium ${revealed.is_correct ? "text-green-600" : "text-red-600"}`}
              >
                {revealed.is_correct ? "答对了" : "答错了"}
                <span className="ml-2 text-xs font-normal text-sub">
                  得分 {revealed.score}
                  {revealed.idempotent ? " · 这题之前已经答过（已按既有结果返回）" : ""}
                </span>
              </p>
              <p className="mt-3 text-sm">
                <span className="text-sub">正确答案：</span>
                {revealed.answer.map(show).join("、")}
              </p>
              <p className="mt-3 whitespace-pre-wrap text-sm">
                <span className="text-sub">解析：</span>
                {revealed.analysis ?? "（这道题没有解析）"}
              </p>
            </section>
          )}

          <p className="mt-5 rounded-xl border border-dashed border-line p-3 text-xs text-sub">
            下一题 / 答题卡 / 交卷报告在 P2b-2。想接着练这章，回上一步再建一次。
          </p>
        </>
      )}

      {sess && !current && <p className="mt-8 text-sm text-sub">这次练习里没有题目。</p>}
    </main>
  );
}
