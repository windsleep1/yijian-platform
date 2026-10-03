"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { WrongList } from "@/lib/types";

/**
 * 错题本（Tab 之外的独立页）—— **P2c-2 收窄版**：列出所有错题 / 按科目筛选 / 点一题进详情。
 *
 * ★ 本页（P2c-2 + P2c-3）：
 *   · 列出错题 / 按科目筛选 / 点一题进详情；
 *   · **错题重练**（P2c-3）：每行一个「重练这道题」，列表顶部一个「重练这一组」
 *     —— 都是 `POST /practice/sessions {mode:'wrong'}` 然后进**现有**答题页，
 *     **不新建页面**（那条链路的交卷 / 报告 / 断点恢复全都现成）。
 *
 * ★ 仍然划出去的（不是"忘了"）：按章节·时间筛选 ｜ 掌握度标记 ｜ 这题的收藏 / 笔记。
 * ⚠️ 划出去**不再写在界面上**（BL-24）：用户不会关心"P2c-3 / BL-21"这种编号，
 *    而这类文字**会随批次过期**——它属于代码注释与 `docs/24`，不属于渲染出来的 DOM。
 *
 * ★★ 两个"数据从哪来"的判据（与 P2c-1 同源）：
 *   ① **分面 `subjects` 与列表同源**：切了科目之后，chip 上的条数必须是"这个筛选下还有多少"。
 *      后端已经只返选中科目的分面 ⇒ 前端**不许**自己算，也不许拿 `/subjects` 去凑
 *      （那会得到 6 个点了没反应的 chip）。
 *   ② **空态要说清"是没数据"还是"这个筛选下没数据"**：两者都得有话说，
 *      否则用户会把"筛错了科目"读成"错题本空了"。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

const PAGE_SIZE = 10;

/** 「最近错于」的相对时间。**只用来排序提示**，不参与任何判断。 */
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

export default function WrongBookPage() {
  const router = useRouter();
  const [data, setData] = useState<WrongList | null>(null);
  const [subject, setSubject] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  //: 建重练会话期间的**待定态**（硬约定 D：待定期间按钮必须 disabled）。
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async (nextSubject: string | null, nextPage: number) => {
    setBusy(true);
    setErr("");
    try {
      const d = await request<WrongList>("/practice/wrong-questions", {
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

  const pick = (sid: string | null) => {
    setSubject(sid);
    setPage(1); // ★ 换筛选必须回到第 1 页 —— 否则会出现"第 3 页 + 新筛选 ⇒ 空列表"
  };

  const total = data?.total ?? 0;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  //: 「重练这一组」一次最多建 100 题（后端 `count` 上限）—— 标签上**如实**写这个数。
  const retryAllCount = Math.min(total, 100);

  /**
   * 建一次**错题重练**会话并进答题页。`ids` 给了 = 「重练这一题」；不给 = 「重练这一组」。
   *
   * ★ 三条与建章节练习同款的纪律：
   *   ① **成功后不复位 `starting`** —— 这一瞬到路由切换之间若按钮变回可点，
   *      用户连点就会建出两个会话（硬约定 D 的同族）；
   *   ② 失败**留在原地说原因**：错题本为空 / 筛选后为空都是 `40401`，
   *      而它**不是故障**（后端文案已带「怎么才能有题」）⇒ 不要跳走、不要吞掉；
   *   ③ id 一律按**字符串**传（`request` 里有 `assertNoUnsafeIdInBody` 兜底）。
   */
  const retry = async (ids: string[] | null) => {
    if (starting) return;
    setStarting(true);
    setErr("");
    try {
      const d = await request<{ id: string }>("/practice/sessions", {
        method: "POST",
        body: {
          mode: "wrong",
          question_ids: ids ?? undefined,
          subject_id: ids ? undefined : (subject ?? undefined),
          count: ids ? ids.length : retryAllCount,
        },
      });
      router.push(`/practice/session/${d.id}`);
    } catch (e) {
      setErr(errText(e));
      setStarting(false);
    }
  };

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <Link href="/practice" className="text-xs text-sub underline">
        ← 练习
      </Link>
      <h1 className="mt-3 text-2xl font-semibold">错题本</h1>
      <p data-wrong-total className="mt-1 text-sm text-sub">
        {data === null ? " " : `共 ${total} 道错题`}
        {subject && data?.subjects[0] ? `（${data.subjects[0].name}）` : ""}
      </p>

      {/* 科目筛选：★ 分面来自**同一份响应**，带条数（后端在筛选时只返选中的那个） */}
      <div data-wrong-facets className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          data-facet="all"
          aria-pressed={subject === null}
          onClick={() => pick(null)}
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
            onClick={() => pick(s.subject_id)}
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

      {data !== null && data.items.length > 0 && (
        <button
          type="button"
          data-retry-all
          disabled={starting}
          onClick={() => void retry(null)}
          className="mt-4 flex min-h-touch w-full items-center justify-center rounded-xl bg-brand text-sm font-medium text-brand-fg disabled:opacity-40"
        >
          {starting ? "正在创建重练…" : `重练这一组（${retryAllCount} 道）`}
        </button>
      )}

      {err && (
        <p
          data-wrong-error
          className="mt-4 rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700"
        >
          {err}
        </p>
      )}

      {busy && data === null && <p className="mt-6 text-sm text-sub">加载中…</p>}

      {data !== null && data.items.length === 0 && (
        <p
          data-wrong-empty={subject === null ? "no-data" : "filtered"}
          className="mt-6 rounded-xl border border-dashed border-line p-4 text-sm text-sub"
        >
          {subject === null
            ? "还没有错题。去练习里做几道，答错的题会自动记到这里。"
            : "这个科目下还没有错题 —— 换个科目看看，或者点「全部」。"}
        </p>
      )}

      {data !== null && data.items.length > 0 && (
        <ul data-wrong-list className="mt-4 space-y-3">
          {data.items.map((it) => (
            <li
              key={it.question_id}
              data-wrong-item={it.question_id}
              className="rounded-xl border border-line p-4"
            >
              <Link
                href={`/practice/wrong/${it.question_id}`}
                data-wrong-row={it.question_id}
                className="block"
              >
                <p className="line-clamp-2 text-sm leading-6">{it.stem}</p>
                <p className="mt-2 text-xs text-sub">
                  <span className="font-medium text-red-600">错 {it.wrong_count} 次</span>
                  {it.retry_correct > 0 && <span> · 重练答对 {it.retry_correct} 次</span>}
                  {it.chapter_name && <span> · {it.chapter_name}</span>}
                  {it.last_wrong_at && <span> · {when(it.last_wrong_at)}</span>}
                </p>
              </Link>
              {/* ★ 「重练」不放在上面那个 <Link> 里：嵌套交互元素在真机上
                  点哪儿都可能触发行跳转 —— 那正是"按钮点了没反应"的经典成因。 */}
              <div className="mt-3 flex items-center gap-2">
                <button
                  type="button"
                  data-retry={it.question_id}
                  disabled={starting}
                  onClick={() => void retry([it.question_id])}
                  className="min-h-touch flex-1 rounded-lg border border-brand text-xs font-medium text-brand disabled:opacity-40"
                >
                  重练这道题
                </button>
                {it.retry_correct > 0 && (
                  <span
                    data-retried={it.question_id}
                    className="rounded-md bg-green-50 px-2 py-1 text-xs text-green-700"
                  >
                    已重练 ✓
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {data !== null && pages > 1 && (
        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            data-wrong-prev
            disabled={page <= 1 || busy}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="min-h-touch flex-1 rounded-xl border border-line text-sm disabled:opacity-40"
          >
            ← 上一页
          </button>
          <span data-wrong-pageno className="px-4 text-xs text-sub">
            {page} / {pages}
          </span>
          <button
            type="button"
            data-wrong-next
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
