import type { Metadata, Viewport } from "next";

import "./globals.css";
import SwRegister from "./sw-register";

export const metadata: Metadata = {
  title: "一建通",
  description: "一级建造师备考",
  /* ★ iOS 侧：Safari **不读 manifest 里的 icons**，只认这个 link（180×180）。
     没有它，"添加到主屏"会拿页面截图当图标 —— 看起来就像"我们没做图标"。
     ⚠️ Android 与 iOS 是**两条独立的路**：Android 走 manifest，iOS 走 meta。
        只写一头，就会在另一头静默失效（而两头的表现都是"图标不对"，很难分辨是谁缺了）。 */
  icons: { apple: "/icons/apple-touch-icon.png" },
  /* ★ 从主屏打开时进**全屏**（无 Safari 地址栏）—— 与 manifest 的 `display: standalone`
     是同一件事的 iOS 写法（iOS 16.4 起也认 manifest，但老的仍只认这个 meta）。 */
  appleWebApp: { capable: true, title: "一建通", statusBarStyle: "default" },
};

/**
 * 移动端 viewport —— **C 端的第一个"和 B 端不一样"的地方**。
 *
 * ⚠️ `maximumScale` / `userScalable` **刻意不设**（默认允许缩放）：
 * 禁用缩放会让视力不佳的用户没法放大文字，是无障碍上的硬伤；
 * "防止双击缩放"应该靠 CSS `touch-action` 解决（P3），不是靠锁 viewport。
 *
 * `viewportFit: "cover"` 让页面铺到 iPhone 的刘海/圆角区域 ——
 * 配套要用 `env(safe-area-inset-*)`（P3 做底部 Tab 时处理）。
 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  /* ★ 2026-10-02：由 `#ffffff` 改为**品牌蓝 `#205CE9`**（= `--brand`，主按钮同色）。
     它决定浏览器/状态栏的着色 —— 属**可见变更**，已获用户确认。
     ⚠️ 必须与 `app/manifest.ts` 的 `theme_color` **同值**：一个是 meta、一个是 manifest，
        写岔了就会出现"地址栏白、装到桌面后状态栏蓝"这种自相矛盾（而两边都不报错）。 */
  themeColor: "#205CE9",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">
        {children}
        {/* 只做"注册 Service Worker"一件事；生产环境才生效（见组件内注释）。 */}
        <SwRegister />
      </body>
    </html>
  );
}
