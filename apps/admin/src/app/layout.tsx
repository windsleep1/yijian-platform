import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "一建通 · 管理后台",
  description: "一级建造师学习备考平台 · 管理后台 v0.1（Batch 7）",
};

/**
 * 根布局。
 *
 * 刻意**不使用 `next/font` 拉 Google Fonts** —— 构建期需要外网，
 * 内网/离线环境会直接 build 失败。Tailwind 的默认字体栈在任何机器上都能用。
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
