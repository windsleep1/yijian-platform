"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * 底部 Tab —— 与 `apps/web` 的同款，**少一个 Tab、少两层守卫**。
 *
 * ## 与 C 端的差别
 * 1. **没有 `middleware.ts` 与 `hasToken()` 两层守卫** —— 单机单用户，
 *    没有"未登录"这个状态。★ 留着它们会变成**永远不触发的守卫**：
 *    那种东西在真机上表现成**白屏 / 跳错页**，而本地因为"看着正常"永远发现不了。
 * 2. **「我的」Tab 的内容被换掉了（不是砍掉）** —— C 端那个 Tab 下面是账号相关
 *    （改密码 / 退出 / 消息中心），这里换成本地该有的三样：
 *    **收藏 / 标记**、**笔记**、**数据备份与题库信息**。
 *    ★ 这条注释 2026-10-06 改过一次：原文写着"没有「我的」Tab…设置（导出/导入）在 B-3 加"
 *    —— 加了 Tab 之后，**那句就成了假话**（"没有"变成了"有"、"B-3"变成了"B-2"）。
 *    环境一变，注释就是假的；**改代码时要连它一起改**。
 *
 * ## 为什么仍然用**路由组** `(tabs)`
 * 同 C 端：同一 layout 下的兄弟路由切换是**局部替换 children** ⇒ 底栏不重建
 * （不闪、不丢滚动位置）。`(tabs)` 是路由组，括号不进 URL ⇒ 首页仍是 `/`。
 */

/** ⚠️ 这里**不写死**"以后会有几个 Tab" —— 加 Tab 就往数组里加一项。 */
const TABS = [
  { href: "/", label: "首页", d: "M3 10.5 12 3l9 7.5M5 9.5V21h14V9.5" },
  { href: "/practice", label: "练习", d: "M4 5h16v14H4zM8 9h8M8 13h5" },
  // ★ 图标与"我的"语义一致（人像：头 + 肩），与另两个同风格（`fill="none"` + `stroke`）。
  //   三个 Tab 的排版**不用改**：`<ul>` 是 `flex` + 每项 `flex-1` ⇒ 自动三等分。
  //   （底栏高度由 `min-h-touch` 定，与 Tab 数量无关 ⇒ `pb-20` 也不用动。）
  {
    href: "/me",
    label: "我的",
    d: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  },
] as const;

export default function TabsLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
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
                  // min-h-touch（44px）来自 tailwind.config：可点区域写成 token，
                  // 散在各页写 h-12 就没法保证一致。
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
