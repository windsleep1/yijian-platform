"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { CollectionList } from "@/lib/types";

/**
 * 我的收藏 / 标记（**P2c-4**）—— 两个页签、共用一套列表。
 *
 * ★ **为什么标记也有页面**：标记可以打在**从没错过**的题上，那种题**不在错题本里**
 *   ⇒ 如果只在错题本里加「已标记」筛选，用户会问"我标的题去哪看"。
 *   两个列表**形状完全一样**（后端 `kind` 切题源）⇒ 这里一份 UI 两个页签。
 *
 * ★ 路由按 **IA（`docs/02` 页 35）** 放在 `/me/*` 下；与错题本在 `/practice/wrong` 不一致
 *   是**有意的**（新功能没历史包袱，就按 IA 走；导航从 Tab 统一进，用户看不到路径差异）。
 *
 * ★ 三条与错题本**同源**的判据（都是拿事故换来的，这里照抄）：
 *   ① **分面与列表同源**：`subjects` 来自**同一份响应**，前端**不许**自己算，
 *      也不许拿 `/subjects` 去凑（那会得到几个点了没反应的 chip）；
 *   ② **切筛选必须回第 1 页**（否则"第 3 页 + 新筛选 ⇒ 空列表"）；
 *   ③ **空态要说清是"没有收藏"还是"这个筛选下没有"**——两者都得有话说。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

const PAGE_SIZE = 10;

/** 两个页签的文案与「取消」动词。★ 只在这里出现一次，两处不要各写一套。 */
const KIND_META: Record<"favorite" | "mark", { tab: string; remove: string; empty: string }> = {
  favorite: {
    tab: "收藏",
    remove: "取消收藏",
    empty: "还没有收藏。做题时点「☆ 收藏」就会到这里。",
  },
  mark: { tab: "标记", remove: "取消标记", empty: "还没有标记。做题时点「⚑ 标记」就会到这里。" },
};

function when(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

export default function FavoritesPage() {
  const [kind, setKind] = useState<"favorite" | "mark">("favorite");
  const [data, setData] = useState<CollectionList | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(
    async (nextKind: "favorite" | "mark", nextSubject: string | null, nextPage: number) => {
      setBusy(true);
      setErr("");
      try {
        const d = await request<CollectionList>("/practice/favorites", {
          query: {
            kind: nextKind,
            subject_id: nextSubject ?? undefined,
            page: nextPage,
            page_size: PAGE_SIZE,
          },
        });
        setData(d);
      } catch (e) {
        setErr(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    void load(kind, subject, page);
  }, [load, kind, subject, page]);

  /** 换页签 / 换科目都要**回第 1 页**（否则会拿到一个空列表）。 */
  const pickKind = (k: "favorite" | "mark") => {
    setKind(k);
    setSubject(null);
    setPage(1);
  };
  const pickSubject = (sid: string | null) => {
    setSubject(sid);
    setPage(1);
  };

  /** 取消（收藏 / 标记）—— 成功后就地把它从列表里去掉，不必再取一次。 */
  const remove = async (qid: string) => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      await request(`/practice/${kind === "favorite" ? "favorites" : "marks"}/${qid}`, {
        method: "DELETE",
      });
      const d = await request<CollectionList>("/practice/favorites", {
        query: { kind, subject_id: subject ?? undefined, page, page_size: PAGE_SIZE },
      });
      setData(d);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const meta = KIND_META[kind];
  const total = data?.total ?? 0;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const filtered = subject !== null;

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <Link href="/me" className="text-xs text-sub underline">
        ← 我的
      </Link>
      <h1 className="mt-3 text-2xl font-semibold">我的收藏 / 标记</h1>

      {/* 页签：两个列表共用一套 UI，只有题源不同（后端 `kind`）。 */}
      <div className="mt-4 flex items-center gap-2">
        {(["favorite", "mark"] as const).map((k) => (
          <button
            key={k}
            type="button"
            data-kind-tab={k}
            aria-pressed={kind === k}
            onClick={() => pickKind(k)}
            className={`min-h-touch flex-1 rounded-xl border text-sm ${
              kind === k ? "border-brand bg-brand/10 font-medium" : "border-line text-sub"
            }`}
          >
            {KIND_META[k].tab}
          </button>
        ))}
      </div>

      <p data-collect-total className="mt-3 text-sm text-sub">
        {data === null ? " " : `共 ${total} 道${meta.tab}`}
        {subject && data?.subjects[0] ? `（${data.subjects[0].name}）` : ""}
      </p>

      {/* ★ 分面来自**同一份响应**（后端在筛选时只返选中科目，但分面恒为全量） */}
      <div data-collect-facets className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-facet="all"
          aria-pressed={subject === null}
          onClick={() => pickSubject(null)}
          className={`min-h-touch rounded-full border px-4 text-xs ${
            subject === null ? "border-brand bg-brand/10 font-medium" : "border-line text-sub"
          }`}
        >
          全部
        </button>
        {(data?.subjects ?? []).map((s) => (
          <button
            key={s.subject_id}
            type="button"
            data-facet={s.subject_id}
            aria-pressed={subject === s.subject_id}
            onClick={() => pickSubject(s.subject_id)}
            className={`min-h-touch rounded-full border px-4 text-xs ${
              subject === s.subject_id
                ? "border-brand bg-brand/10 font-medium"
                : "border-line text-sub"
            }`}
          >
            {s.name} {s.count}
          </button>
        ))}
      </div>

      {err && (
        <p
          data-collect-error
          className="mt-4 rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700"
        >
          {err}
        </p>
      )}

      {busy && data === null && <p className="mt-6 text-sm text-sub">加载中…</p>}

      {data !== null && data.items.length === 0 && (
        <p
          data-collect-empty={filtered ? "filtered" : "no-data"}
          className="mt-6 rounded-xl border border-dashed border-line p-4 text-sm text-sub"
        >
          {filtered ? "这个科目下还没有 —— 换个科目看看，或者点「全部」。" : meta.empty}
        </p>
      )}

      {data !== null && data.items.length > 0 && (
        <ul data-collect-list className="mt-4 space-y-3">
          {data.items.map((it) => {
            const inner = (
              <>
                <p className="line-clamp-2 text-sm leading-6">{it.stem}</p>
                <p className="mt-2 text-xs text-sub">
                  {it.subject_name && <span>{it.subject_name}</span>}
                  {it.chapter_name && <span> · {it.chapter_name}</span>}
                  {it.collected_at && <span> · {when(it.collected_at)}</span>}
                  {/* ★★ 约定 T：题目下架后这一行**仍然在**，标出来让用户知道**为什么点不开**。 */}
                  {!it.question_available && (
                    <span
                      data-question-gone="1"
                      className="ml-1 rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-500"
                    >
                      题目已下架
                    </span>
                  )}
                  {/* ★ 另一个状态也显示出来 —— 一道题可以既收藏又标记 */}
                  {kind === "favorite" && it.marked && (
                    <span className="ml-1 rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">
                      ⚑ 已标记
                    </span>
                  )}
                  {kind === "mark" && it.favorited && (
                    <span className="ml-1 rounded bg-brand/10 px-1.5 py-0.5 text-brand">
                      ★ 已收藏
                    </span>
                  )}
                </p>
              </>
            );
            return (
              <li
                key={it.question_id}
                data-collect-row={it.question_id}
                data-question-available={it.question_available ? "1" : "0"}
                className="rounded-xl border border-line p-4"
              >
                {/* ★★ **只有错题本里有这道题才渲染成链接**（`in_wrong_book`）：
                    目标页 `/practice/wrong/{qid}` 要求 `wrong_questions` 里有一行，没有就 **404**
                    —— 而「收藏了但从没错过」的题很常见 ⇒ 原来每行都是链接，一半的点开是报错页。 */}
                {it.in_wrong_book ? (
                  <Link href={`/practice/wrong/${it.question_id}`} className="block">
                    {inner}
                  </Link>
                ) : (
                  <div className="block">{inner}</div>
                )}
                <button
                  type="button"
                  data-collect-remove={it.question_id}
                  disabled={busy}
                  onClick={() => void remove(it.question_id)}
                  className="min-h-touch mt-3 w-full rounded-lg border border-line text-xs text-sub disabled:opacity-40"
                >
                  {meta.remove}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {data !== null && pages > 1 && (
        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            data-collect-prev
            disabled={page <= 1 || busy}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="min-h-touch flex-1 rounded-xl border border-line text-sm disabled:opacity-40"
          >
            ← 上一页
          </button>
          <span data-collect-pageno className="px-4 text-xs text-sub">
            {page} / {pages}
          </span>
          <button
            type="button"
            data-collect-next
            disabled={page >= pages || busy}
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            className="min-h-touch flex-1 rounded-xl border border-line text-sm disabled:opacity-40"
          >
            下一页 →
          </button>
        </div>
      )}
    </main>
  );
}
