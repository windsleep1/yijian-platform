"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { Note, NoteList } from "@/lib/types";

/**
 * 我的笔记（**P2c-5**）。
 *
 * ★ 路由按 **IA（`docs/02` 页 36）** 放在 `/me/notes` 下 —— 与 `/me/favorites` 一致。
 *
 * ★★ **这一页刻意不做「点进去看这道题」**：全仓**没有通用单题页**，只有
 *   `/practice/wrong/[qid]`，而它的语义是**「错题」**（要求 `wrong_questions` 里有一行，
 *   没有就 404）⇒ 硬塞进去会让「没错过的那道题的笔记」点开是**错的上下文**，
 *   而「没错过」正是笔记最常见的用法。⇒ 本页**就地编辑 / 删除**，不跳转。
 *   （"跳回原题"需要一个**通用单题页**，已登记成待办。）
 *
 * ★ 三条与错题本 / 收藏页**同源**的判据（都是拿事故换来的）：
 *   ① **分面与列表同源**（`subjects` 来自同一份响应，前端不许自己算）；
 *   ② **切筛选必须回第 1 页**；
 *   ③ **空态要说清是「还没写过」还是「这个筛选下没有」**。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

const PAGE_SIZE = 10;

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

export default function NotesPage() {
  const [data, setData] = useState<NoteList | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  //: 正在编辑哪一条 + 它的草稿（同时只允许一条 —— 状态少了，"改的是哪条"就没有歧义）。
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const load = useCallback(async (nextSubject: string | null, nextPage: number) => {
    setBusy(true);
    setErr("");
    try {
      const d = await request<NoteList>("/practice/notes", {
        query: {
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
  }, []);

  useEffect(() => {
    void load(subject, page);
  }, [load, subject, page]);

  /** 换科目要**回第 1 页**（否则会拿到一个空列表）。 */
  const pickSubject = (sid: string | null) => {
    setSubject(sid);
    setPage(1);
  };

  /** 保存修改 —— 成功后就地替换那一条（不必再取一次整页）。 */
  const save = async (noteId: string) => {
    if (busy) return;
    const content = draft.trim();
    if (content === "") return;
    setBusy(true);
    setErr("");
    try {
      // ★ 用**响应里**那条的 `content` / `updated_at`（后端权威值），
      //   不要前端 `new Date()` 现造一个 —— 那与"`updated_at` 由触发器维护"直接打架，
      //   而症状是"刚改完显示的时间比现在早/晚几秒"，没人会去查。
      const updated = await request<Note>(`/practice/notes/${noteId}`, {
        method: "PUT",
        body: { content },
      });
      setData((d) =>
        d === null
          ? d
          : {
              ...d,
              items: d.items.map((it) =>
                it.id === noteId
                  ? { ...it, content: updated.content, updated_at: updated.updated_at }
                  : it,
              ),
            },
      );
      setEditingId(null);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  /** 删除 —— 会**改总数**，所以重新取一次（这一页有分页，就地删会让页码算错）。 */
  const remove = async (noteId: string) => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      await request(`/practice/notes/${noteId}`, { method: "DELETE" });
      setEditingId(null);
      await load(subject, page);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const total = data?.total ?? 0;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const filtered = subject !== null;

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <Link href="/me" className="text-xs text-sub underline">
        ← 我的
      </Link>
      <h1 className="mt-3 text-2xl font-semibold">我的笔记</h1>

      <p data-note-total className="mt-3 text-sm text-sub">
        {data === null ? " " : `共 ${total} 条笔记`}
      </p>

      {/* ★ 分面来自**同一份响应**（分面恒为全量，后端保证） */}
      <div data-note-facets className="mt-3 flex flex-wrap gap-2">
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
          data-note-error
          className="mt-4 rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700"
        >
          {err}
        </p>
      )}

      {busy && data === null && <p className="mt-6 text-sm text-sub">加载中…</p>}

      {data !== null && data.items.length === 0 && (
        <p
          data-note-empty={filtered ? "filtered" : "no-data"}
          className="mt-6 rounded-xl border border-dashed border-line p-4 text-sm text-sub"
        >
          {filtered
            ? "这个科目下还没有笔记 —— 换个科目看看，或者点「全部」。"
            : "还没有写过笔记。做题时点「✎ 笔记」就会到这里。"}
        </p>
      )}

      {data !== null && data.items.length > 0 && (
        <ul data-note-list className="mt-4 space-y-3">
          {data.items.map((it) => (
            <li
              key={it.id}
              data-note-row={it.id}
              data-question-available={it.question_available ? "1" : "0"}
              className="rounded-xl border border-line p-4"
            >
              <p className="text-sm whitespace-pre-wrap break-words">{it.content}</p>
              <p className="mt-2 text-xs text-sub">
                {it.subject_name && <span>{it.subject_name}</span>}
                {it.chapter_name && <span> · {it.chapter_name}</span>}
                <span> · {when(it.updated_at)}</span>
                {/* ★★ 约定 T：题目下架后这条笔记**仍然列在这里**，标出来让用户知道
                    为什么点不开（本页本来就没有链接，所以只做提示）。 */}
                {!it.question_available && (
                  <span
                    data-question-gone="1"
                    className="ml-1 rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-500"
                  >
                    题目已下架
                  </span>
                )}
              </p>
              {it.stem && (
                <p
                  data-note-stem={it.id}
                  className="mt-2 line-clamp-2 rounded-lg bg-neutral-50 px-2 py-1 text-xs text-sub"
                >
                  {it.stem}
                </p>
              )}

              {editingId === it.id ? (
                <>
                  <textarea
                    data-note-edit-input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={3}
                    className="mt-3 w-full rounded-lg border border-line p-2 text-sm"
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      data-note-edit-save={it.id}
                      disabled={busy || draft.trim() === ""}
                      onClick={() => void save(it.id)}
                      className="min-h-touch flex-1 rounded-lg border border-brand text-xs font-medium text-brand disabled:opacity-40"
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      data-note-edit-cancel={it.id}
                      disabled={busy}
                      onClick={() => setEditingId(null)}
                      className="min-h-touch flex-1 rounded-lg border border-line text-xs text-sub disabled:opacity-40"
                    >
                      取消
                    </button>
                  </div>
                </>
              ) : (
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    data-note-edit={it.id}
                    disabled={busy}
                    onClick={() => {
                      setEditingId(it.id);
                      setDraft(it.content);
                    }}
                    className="min-h-touch flex-1 rounded-lg border border-line text-xs text-sub disabled:opacity-40"
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    data-note-del={it.id}
                    disabled={busy}
                    onClick={() => void remove(it.id)}
                    className="min-h-touch flex-1 rounded-lg border border-line text-xs text-sub disabled:opacity-40"
                  >
                    删除
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {data !== null && pages > 1 && (
        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            data-note-prev
            disabled={page <= 1 || busy}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="min-h-touch flex-1 rounded-xl border border-line text-sm disabled:opacity-40"
          >
            ← 上一页
          </button>
          <span data-note-pageno className="px-4 text-xs text-sub">
            {page} / {pages}
          </span>
          <button
            type="button"
            data-note-next
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
