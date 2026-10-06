import type { Metadata, Viewport } from "next";

import "./globals.css";
import SwRegister from "./sw-register";

/**
 * 根布局 —— 与 `apps/web` 的同款，**唯一差别是标题后缀**（"离线版"）。
 * ★ 为什么保留 `SwRegister`：离线能力来自 IndexedDB，但**壳必须能被 SW 打开**，
 *   否则断网时是一个浏览器错误页 —— 那会被误判成"IndexedDB 坏了"（方案 §2.2）。
 */
export const metadata: Metadata = {
  title: "一建通 · 离线版",
  description: "一级建造师备考（个人离线版 · 数据只存在这台设备上）",
  /* ★ iOS 侧：Safari **不读 manifest 里的 icons**，只认这个 link（180×180）。
     没有它，"添加到主屏"会拿页面截图当图标 —— 看起来就像"我们没做图标"。 */
  icons: { apple: "/icons/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "一建通", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  /* ★ 必须与 `app/manifest.ts` 的 `theme_color` **同值**（一个是 meta、一个是 manifest，
     写岔了就会出现"地址栏白、装到桌面后状态栏蓝"这种自相矛盾，而两边都不报错）。 */
  themeColor: "#205CE9",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">
        {children}
        <SwRegister />
      </body>
    </html>
  );
}
