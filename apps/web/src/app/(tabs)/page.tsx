"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { request } from "@/lib/api";
import { hasToken } from "@/lib/auth-store";
import type { Me } from "@/lib/types";

/**
 * 首页（Tab 1）。**刻意长得很朴素** —— P2a 只做"Tab 能切、资料能显示、引导能进"，
 * 真正的首页聚合（今日进度 / 倒计时 / 入口卡）属于后面的批次（`docs/24` §1.1）。
 *
 * ## 三种失败必须分开（沿用 P1 的写法）
 *
 * | 情况 | 表现 | 处理 |
 * |---|---|---|
 * | **没 token** | 还没发请求 | `replace("/login")`（不发无谓的 401） |
 * | **token 失效 / 被撤销** | 后端回认证类码 | `api.ts` 已经清凭证 + 跳登录，**本页不再跳**（否则横跳） |
 * | **非认证失败**（如依赖不可用） | 有 `role=alert` 的错误 | 给"重试"，不要白屏 |
 *
 * ⚠️ 引入 Tab 布局后，第 1、2 条**又多了一层**：`(tabs)/layout.tsx` 也会做"没 token 就跳"。
 *   两处都保留 —— 布局那层管"整个 Tab 区"，本页这层管"数据拿不到时别停在空白页"。
 *   它们**不冲突**：都是往 `/login` 去，而 `api.ts` 的 `redirectToLogin` 有
 *   "已经在登录页就不再跳"的守卫（否则会来回横跳）。
 */
export default function HomePage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!hasToken()) {
      router.replace("/login");
      return;
    }
    setError("");
    try {
      setMe(await request<Me>("/auth/me"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
        <button
          onClick={() => void load()}
          className="min-h-touch mt-4 w-full rounded-lg border border-line font-medium"
        >
          重试
        </button>
      </main>
    );
  }

  if (!me) {
    return <main className="mx-auto max-w-md px-6 py-10 text-sm text-sub">加载中…</main>;
  }

  const onboarded = !!me.profile?.onboarded_at;

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">你好，{me.nickname || me.phone || "同学"}</h1>
      <p className="mt-1 text-sm text-sub">{me.phone}</p>

      {/* ★ 未完成引导时的入口卡。**刻意不做"强制跳转"** ——
          用户 P2a 的两条验收判据（"注册 → 进首页" 与 "引导 → 进首页"）都落在首页，
          所以引导是**可后补**的；真要阻塞式，改这里 + `(tabs)/layout.tsx` 即可。 */}
      {!onboarded && (
        <Link
          href="/onboarding"
          className="mt-6 block rounded-xl border border-brand/40 bg-brand/5 p-4"
        >
          <p className="font-medium text-brand">还差一步：选专业和考试年份</p>
          <p className="mt-1 text-sm text-sub">选完才能按你的考纲出题（约 30 秒）</p>
        </Link>
      )}

      <dl className="mt-8 space-y-2 text-sm">
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">用户 ID</dt>
          {/* ★ 原样展示字符串 —— **绝不要 Number() 它**（雪花 ID 超出 JS 安全整数） */}
          <dd className="font-mono">{me.id}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">报考科目</dt>
          <dd>{me.profile?.professional ?? "未设置"}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">考试年份</dt>
          <dd>{me.profile?.exam_year ?? "未设置"}</dd>
        </div>
        <div className="flex justify-between py-2">
          <dt className="text-sub">引导</dt>
          <dd>{onboarded ? "已完成" : "未完成"}</dd>
        </div>
      </dl>
    </main>
  );
}
