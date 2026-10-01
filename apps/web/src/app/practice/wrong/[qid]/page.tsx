"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import type { WrongDetail } from "@/lib/types";

/**
 * 错题详情（**P2c-2**）—— 含**正确答案与解析**。
 *
 * ★★ 为什么这里可以给答案（而答题页在未作答时不给）：
 *   P2b-1 那条规则防的是「**没答就看到答案**」；错题本的**前提**是「你已经答错了」。
 *   两条规则的目标其实是同一个：**答案只在"你已经和这道题交过手"之后才给**。
 *
 * ⚠️ 但"给答案"这个能力**必须由后端那道门把关**（没错过的题返回 404）——
 *   前端不要"兜底成空答案"，也**不要**把 404 显示成"加载失败"：
 *   403/404 在这里的含义是"**这道题不在你的错题本里**"，要如实说出来。
 *
 * ⚠️ 本页**不显示"我当时选了什么"**：`wrong_questions` 表里没有 `user_answer`
 *   （那是 `practice_items` 的列）。要么加一次查询、要么就诚实地不显示 ——
 *   编一个"你选了 B"比不显示更糟。留待需要时再加接口。
 */

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.message}（${e.code}${e.traceId ? ` · ${e.traceId}` : ""}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** 判分与答案的显示口径与答题页一致：判断题的布尔显示成中文。 */
function show(v: unknown): string {
  if (typeof v === "boolean") return v ? "正确" : "错误";
  return String(v);
}

const TYPE_LABEL: Record<string, string> = {
  single: "单选题",
  multiple: "多选题",
  judge: "判断题",
};

export default function WrongDetailPage() {
  const { qid } = useParams<{ qid: string }>();
  const [d, setD] = useState<WrongDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!qid) return;
    let alive = true;
    setBusy(true);
    request<WrongDetail>(`/practice/wrong-questions/${qid}`)
      .then((x) => {
        if (alive) setD(x);
      })
      .catch((e) => {
        if (!alive) return;
        // 40401 = "这道题不在你的错题本里" —— 这是一个**正常的答案**，不是故障。
        if (e instanceof ApiError && e.code === 40401) setMissing(true);
        else setErr(errText(e));
      })
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [qid]);

  if (missing) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p
          data-wrong-missing
          className="rounded-xl border border-dashed border-line p-4 text-sm text-sub"
        >
          这道题不在你的错题本里 —— 只有**答错过**的题才会出现在这里。
        </p>
        <Link
          href="/practice/wrong"
          className="mt-6 block text-center text-sm text-brand underline"
        >
          回错题本
        </Link>
      </main>
    );
  }

  if (err) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p className="rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700">{err}</p>
        <Link
          href="/practice/wrong"
          className="mt-6 block text-center text-sm text-brand underline"
        >
          回错题本
        </Link>
      </main>
    );
  }

  if (!d) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p className="text-sm text-sub">{busy ? "加载中…" : " "}</p>
      </main>
    );
  }

  const right = new Set(d.answer.value.map(show));

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <Link href="/practice/wrong" className="text-xs text-sub underline">
        ← 错题本
      </Link>

      <p className="mt-3 flex items-center gap-2 text-xs text-sub">
        <span className="rounded-md border border-line px-2 py-0.5">
          {TYPE_LABEL[d.type] ?? d.type}
        </span>
        {d.chapter_name && <span>{d.chapter_name}</span>}
      </p>

      <h1 data-wrong-stem className="mt-3 whitespace-pre-wrap text-base leading-7">
        {d.stem}
      </h1>

      {d.options.length > 0 && (
        <ul className="mt-4 space-y-2">
          {d.options.map((o) => (
            <li key={o.label}>
              <div
                data-option={o.label}
                data-option-right={right.has(o.label) ? "1" : "0"}
                className={`flex items-start gap-3 rounded-xl border px-3 py-2 text-sm ${
                  right.has(o.label) ? "border-green-500" : "border-line"
                }`}
              >
                <span className="mt-0.5 shrink-0 font-medium">{o.label}</span>
                <span className="flex-1 whitespace-pre-wrap">{o.content}</span>
                {right.has(o.label) && (
                  <span className="shrink-0 text-xs text-green-600">正确答案</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <section className="mt-5 rounded-xl border border-line p-4">
        <p data-wrong-answer className="text-sm">
          <span className="text-sub">正确答案：</span>
          {d.answer.value.map(show).join("、")}
        </p>
        <p className="mt-3 whitespace-pre-wrap text-sm">
          <span className="text-sub">解析：</span>
          {d.analysis ?? "（这道题没有解析）"}
        </p>
      </section>

      <section data-wrong-record className="mt-4 rounded-xl border border-dashed border-line p-4">
        <p className="text-xs font-medium">这道题的记录</p>
        <p className="mt-2 text-xs text-sub">
          错过 <span className="font-medium text-red-600">{d.wrong_count}</span> 次
          {d.retry_correct > 0 && ` · 之后答对 ${d.retry_correct} 次`}
          {d.reason_tag && ` · 标记：${d.reason_tag}`}
        </p>
        <p className="mt-1 text-xs text-sub">
          {d.last_wrong_at ? `最近错于 ${new Date(d.last_wrong_at).toLocaleString()}` : " "}
        </p>
      </section>

      <p className="mt-8 rounded-xl border border-dashed border-line p-3 text-xs text-sub">
        「重练这道题 / 这一批错题」排在后面的批次 —— 它要先做 `mode='wrong'` 的建练习逻辑。
      </p>
    </main>
  );
}
