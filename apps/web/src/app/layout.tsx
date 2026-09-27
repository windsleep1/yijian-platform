import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "一建通",
  description: "一级建造师备考",
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
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
