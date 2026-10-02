import type { MetadataRoute } from "next";

/**
 * C 端 PWA 的 manifest —— 放在 `src/app/manifest.ts`，Next 会：
 *   ① 产出 `/manifest.webmanifest`；
 *   ② **自动**在 `<head>` 注入 `<link rel="manifest">`（不用手写 link）。
 *
 * ⚠️ **它自己也要能被人拿到**：这个路径会被 `middleware.ts` 拦到（matcher 只排除了
 * 图片与 `_next/*`），未登录访客会被 302 到 `/login` ⇒ 浏览器拿到的是 HTML，
 * manifest 解析失败 ⇒ **装不上**。所以 `PUBLIC_PREFIXES` 里必须有它
 * （不变量 10-d 会断言这件事 —— 这个坑**登录状态下看不出来**）。
 *
 * ★ 颜色口径（2026-10-02 用户确认）：
 *   · `theme_color` = **`#205CE9`** —— 即 `globals.css` 的 `--brand: 222 82% 52%`，
 *     也就是 C 端**主按钮**的颜色（**全站只有这一个蓝**，已 grep 核对）。
 *     它决定 Android 上"添加到主屏"后状态栏的着色；同时 `layout.tsx` 的
 *     `viewport.themeColor` 也改成同一个值，否则会出现
 *     "meta 说白、manifest 说蓝"的不一致。
 *   · `background_color` = 白 —— 它是启动画面的底色，与 C 端的 `--bg` 一致。
 *
 * ★ `display: "standalone"` 是判据"**打开后没有浏览器地址栏**"的唯一来源。
 * ★ `start_url: "/"` 在未登录时会经中间件跳 `/login` —— **这是预期**，
 *   不是 bug（装到桌面后打开看到登录页）。
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "一建通 · 一级建造师学习备考平台",
    short_name: "一建通",
    description: "一级建造师的刷题、模拟考试与进度管理",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#205CE9",
    lang: "zh-CN",
    icons: [
      /* ★★ `any` 与 `maskable` **分开写**（2026-10-02 用户要求）。
         `maskable` 的图必须把主体收在中心 80% 直径的安全圆内 —— Android 会按
         设备主题把图标裁成圆形 / 水滴 / 方形，画到边缘的部分**真的会被切掉**。
         所以它是**另一张图**（字更小），不是同一个文件换个 purpose 名字。
         ⚠️ 尺寸由 `tools/local-verify/gen-pwa-icons.py` 生成时自验（读 PNG 的 IHDR），
            并由不变量 10-b 在门禁里复核 —— **不信文件名**。 */
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
