"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { hasToken } from "@/lib/auth-store";

/**
 * ★ **三条 Tab 共用同一个 layout** —— 底部导航只在这里写一次。
 *
 * 为什么用**路由组** `(tabs)` 而不是把导航塞进每个页面：
 *
 *   1. **切 Tab 不重挂载布局**：Next 的 App Router 里，同一 layout 下的兄弟路由切换是
 *      **局部替换 children**，底栏的 DOM 与状态都在 —— 而这正是"Tab"该有的手感。
 *      把导航写进三个页面里，每次切换都要重建一遍底栏（闪烁 + 滚动位置丢失）。
 *   2. **越权/未登录的守卫只写一处**：见下面的第 2 层守卫。
 *   3. `(tabs)` 是**路由组**（括号不进 URL）⇒ 首页仍是 `/`，不是 `/tabs`。
 *
 * ## 双层守卫（与 `apps/admin` 同一套思路）
 *
 *   · **第 1 层（`middleware.ts`）**：只读那个"提示位" cookie，未登录直接 307 到 `/login`。
 *     它跑在 Edge Runtime，**读不到 localStorage**，也**不是安全边界**（cookie 可伪造）。
 *   · **第 2 层（本文件）**：`localStorage` 里没有 access token ⇒ 送登录页。
 *     这一层才是"真的没登录"的判断；两层都在，是为了**消除首屏闪一下**。
 *   ⚠️ 真正的判定永远是**后端**（`/auth/me` + P0 的会话墙）。
 *
 * ⚠️ **不能在渲染期直接 `hasToken()`**：它读 `localStorage`，服务端渲染时恒为 `false`
 *   ⇒ 首屏 HTML 与客户端第一次渲染不一致 = hydration mismatch。
 *   所以先渲染 `mounted=false` 的骨架，`useEffect` 里再切真身。
 */
const TABS = [
  { href: "/", label: "首页", d: "M3 10.5 12 3l9 7.5M5 9.5V21h14V9.5" },
  { href: "/practice", label: "练习", d: "M4 5h16v14H4zM8 9h8M8 13h5" },
  { href: "/me", label: "我的", d: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21c0-4 4-6 8-6s8 2 8 6" },
] as const;

export default function TabsLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    // 第 2 层守卫：没 token 就别留在 Tab 里（middleware 挡的是"带不上 cookie"的那种，
    // 而 localStorage 被手动清掉、cookie 还在的情况只能靠这里）。
    if (!hasToken()) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [router, path]);

  if (!mounted) {
    // 骨架：与真身的**高度**一致，避免守卫跳转时页面跳一下。
    return <main className="px-6 py-10 text-sm text-sub">加载中…</main>;
  }

  return (
    <div className="flex min-h-screen flex-col">
      {/* pb-20 = 给底栏留位（底栏是 fixed，不占流） */}
      <div className="flex-1 pb-20">{children}</div>

      <nav
        aria-label="主导航"
        className="fixed inset-x-0 bottom-0 border-t border-line bg-page/95 backdrop-blur"
      >
        <ul className="mx-auto flex max-w-md">
          {TABS.map((t) => {
            const active = path === t.href;
            return (
              <li key={t.href} className="flex-1">
                <Link
                  href={t.href}
                  aria-current={active ? "page" : undefined}
                  // min-h-touch（44px）来自 tailwind.config：**可点区域写成 token**，
                  // 散在各页写 h-12 就没法保证一致（冻结清单 §2 的 C 端约束）。
                  className={`flex min-h-touch flex-col items-center justify-center gap-0.5 text-xs ${
                    active ? "text-brand" : "text-sub"
                  }`}
                >
                  <svg
                    viewBox="0 0 24 24"
                    className="h-6 w-6"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d={t.d} />
                  </svg>
                  {t.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
