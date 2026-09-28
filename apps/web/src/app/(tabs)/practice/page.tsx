"use client";

import Link from "next/link";

/**
 * 练习（Tab 2）—— **P2a 只放占位**。
 *
 * ⚠️ 为什么现在就建这个页面、而不是等 P2b：
 *   三条 Tab 的验收判据是"**都能切到**"。少一条 Tab，切过去就是 404 —— 而
 *   "页面能点进去"是 C 端最基本的底线（比少一个功能严重，P1 已经在登录页的
 *   "去注册"链接上守过同一条）。
 *   所以在 P2b 把真功能填进来之前，这里**明确写着"还没有"**，而不是一个空白页
 *   （空白页在新人眼里和"坏了"分不清）。
 */
export default function PracticePage() {
  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">练习</h1>
      <p className="mt-1 text-sm text-sub">按章节刷题、看解析、记错题</p>

      <div className="mt-8 rounded-xl border border-dashed border-line p-6 text-center">
        <p className="font-medium">这一屏还没做</p>
        <p className="mt-2 text-sm text-sub">
          章节选择 / 答题 / 判分 / 结果 / 错题本 —— 排在下一批（P2b / P2c）。
        </p>
      </div>

      <Link href="/" className="mt-6 block text-center text-sm text-brand">
        先回首页
      </Link>
    </main>
  );
}
