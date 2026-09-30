"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { AnswerResult, PracticeSession, SessionItem } from "@/lib/types";

/**
 * 答题页（**P2b-1：显示当前题 → 选择 → 提交判分 → 显示解析**；
 * **P2b-2a：上一题 / 下一题 按钮切题**）。
 *
 * 路由在 `(tabs)` **之外**：答题应该是整屏的，底下挂一条 Tab 栏只会让人误点走
 * （而且走了之后"当前进度"的语义就含混了）。这与 `/onboarding` 的处理一致。
 *
 * ★★ **"刷新后还在"是怎么成立的**（验收判据之一）：
 *   页面 id 在 URL 里（`/practice/session/<id>`），刷新只会重新 `GET` 一次同一条记录；
 *   而"落点"由后端的 `current_item_id` 决定 —— 前端**不自己存游标**。
 *   判据：把 localStorage 清干净再刷新，仍然落在同一题（游标不在前端）。
 *
 *   切题之后**刷新会回到服务端的落点**（不是你看的那一题）—— 这是**有意的**：
 *   落点 = "第一道还没答的题"，是**断点恢复**的语义；而你看哪一题是**会话内的临时状态**，
 *   它不该被持久化（否则"复习某道旧题"会把断点也带走）。
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

  /**
   * ★ **切题（P2b-2a）** —— 游标只由**用户动作**改。三个刻意的选择：
   *
   * ① **不发请求**：切题是本地状态变化，改的只是"用户在看哪一题"；
   *    进度（已答 N/M）仍然只由**提交**驱动 ⇒ 两者独立更新（硬约定 S）。
   *    判据：点几次上一题/下一题，"已答 N/M"**一个数字都不该动**。
   * ② 边界**不循环**：第 1 题的「上一题」、最后一题的「下一题」**显式 disabled** ——
   *    在用户那里，"点了没反应"与"这个按钮不能点"是两件不同的事。
   * ③ `busy` 期间**禁掉**：否则请求还在飞、游标已经走了，回来会把结果挂到**另一题**下面。
   */
  const idx =
    sess === null || cursor === null ? -1 : sess.items.findIndex((it) => it.item_id === cursor);
  const canPrev = idx > 0 && !busy;
  const canNext = sess !== null && idx >= 0 && idx < sess.items.length - 1 && !busy;
  const go = useCallback(
    (delta: number) => {
      if (sess === null || idx < 0) return;
      const next = sess.items[idx + delta];
      if (next) setCursor(next.item_id);
    },
    [sess, idx],
  );

  /* ==================== 滑动切题（P2b-2a 后半）====================
   * 与按钮切题**同一个语义**：只改本地游标，**不发请求、不动进度**（硬约定 S）。
   *
   * 四条判据（都在下面的代码里有对应处）：
   * ① **阈值**：`max(屏宽 25%, 80px)`。太小 ⇒ 误触；太大 ⇒ 用户以为没反应。
   * ② **方向判定**：`|dx| > |dy| * 1.5` 才算切题；否则那是用户在**滚题目**。
   *    ⚠️ 只判"谁大"不够 —— 斜着滑时 `|dx|` 常常只大一点点，那种情况该判成滚动。
   * ③ **视觉反馈**：滑动中 `translateX` **跟着手指走**；松手超阈值 ⇒ 滑出并切题，
   *    未超 ⇒ 弹回。**没有反馈，用户不知道生效了没**。
   * ④ **只对触摸设备启用**：桌面**不**把鼠标拖动当切题（只保留按钮）。
   */
  const [touchable, setTouchable] = useState(false);
  const [dragX, setDragX] = useState(0);
  const [animating, setAnimating] = useState(false);
  const touch = useRef<{ x0: number; y0: number; axis: "?" | "x" | "y"; dx: number } | null>(null);

  useEffect(() => {
    setTouchable(window.matchMedia("(pointer: coarse)").matches || "ontouchstart" in window);
  }, []);

  const swipeThreshold = () => Math.max(window.innerWidth * 0.25, 80);

  const onTouchStart = (e: React.TouchEvent) => {
    if (!touchable || e.touches.length !== 1) return;
    const t = e.touches[0];
    touch.current = { x0: t.clientX, y0: t.clientY, axis: "?", dx: 0 };
    setAnimating(false);
    setDragX(0);
  };

  const onTouchMove = (e: React.TouchEvent) => {
    const st = touch.current;
    if (!st || e.touches.length !== 1) return;
    const t = e.touches[0];
    const dx = t.clientX - st.x0;
    const dy = t.clientY - st.y0;
    if (st.axis === "?") {
      if (Math.abs(dx) > Math.abs(dy) * 1.5 && Math.abs(dx) > 10) st.axis = "x";
      else if (Math.abs(dy) > 10) st.axis = "y";
      else return; // 还没走够，先不定方向
    }
    if (st.axis !== "x") return; // 纵向 ⇒ 让浏览器滚页面，我们不管
    st.dx = dx;
    setDragX(dx);
  };

  const onTouchEnd = () => {
    const st = touch.current;
    touch.current = null;
    if (!st || st.axis !== "x") return;
    const w = window.innerWidth;
    const thresh = swipeThreshold();
    const out = Math.round(w * 0.45);
    setAnimating(true);
    if (st.dx <= -thresh && canNext) {
      setDragX(-out);
      window.setTimeout(() => {
        go(1);
        setDragX(0);
      }, 160);
    } else if (st.dx >= thresh && canPrev) {
      setDragX(out);
      window.setTimeout(() => {
        go(-1);
        setDragX(0);
      }, 160);
    } else {
      setDragX(0); // 未超阈值 ⇒ **弹回**
    }
  };

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
          {/* ★ 滑动切题：**整块题目**跟着手指位移。`touch-action: pan-y` 让纵向仍可滚动，
              横向归我们 —— 这样不必 `preventDefault`（被动监听里叫不动它）。 */}
          <div
            data-swipe-card
            onTouchStart={onTouchStart}
            onTouchMove={onTouchMove}
            onTouchEnd={onTouchEnd}
            style={{
              transform: `translateX(${dragX}px)`,
              transition: animating ? "transform 160ms ease-out" : "none",
              touchAction: "pan-y",
            }}
          >
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
                              active && revealed === null
                                ? "border-brand bg-brand/10"
                                : "border-line"
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

            {/* ★ 切题（P2b-2a）：**纯本地**，不发请求、不动进度（硬约定 S）。 */}
            <div className="mt-4 flex items-center gap-3">
              <button
                type="button"
                disabled={!canPrev}
                onClick={() => go(-1)}
                className="min-h-touch flex-1 rounded-xl border border-line text-sm disabled:opacity-40"
              >
                ← 上一题
              </button>
              <button
                type="button"
                disabled={!canNext}
                onClick={() => go(1)}
                className="min-h-touch flex-1 rounded-xl border border-line text-sm disabled:opacity-40"
              >
                下一题 →
              </button>
            </div>
          </div>

          <p className="mt-5 rounded-xl border border-dashed border-line p-3 text-xs text-sub">
            答题卡 / 长按标记在 P2b-2b；交卷报告 / 历史 /
            错题本随后。想接着练这章，回上一步再建一次。
          </p>
        </>
      )}

      {sess && !current && <p className="mt-8 text-sm text-sub">这次练习里没有题目。</p>}
    </main>
  );
}
