"use client";

import { useEffect } from "react";

/**
 * 注册 C 端 Service Worker（`public/sw.js`）。
 *
 * 它只做一件事，**故意不做别的**：注册 + 失败时安静地记一笔。
 *
 * ## ★★ 为什么必须卡 `NODE_ENV === "production"`
 *
 * 这一行是本批"**已有的 E2E 不回归**"这条验收判据的**结构性**保证，不是随手加的：
 *
 * - 本机跑 C 端走查**只能**用 `next dev`（`--e2e-dev`）—— 本机沙箱跑不了
 *   `next build`（删除守卫 + `.next/trace` 的 EPERM，见 `e2e-web.py` 抬头）。
 * - 而 `next dev` 下 `NODE_ENV` 是 `development` ⇒ **SW 根本不注册** ⇒
 *   现有 E2E 跑在与以前**逐字节相同**的运行形态下。
 *
 * ⚠️ 反过来说：**这条判据不能靠"我跑了一遍 E2E 是绿的"来证明** ——
 * 那种证据只能说明"这次没撞上"。写成结构性隔离，才是"以后也不会撞上"。
 *
 * ## 为什么 dev 下**不该**注册（哪怕能）
 *
 * dev 的 chunk 名字稳定但内容每次变，SW 的 cache-first 会把**旧 chunk 留住** ⇒
 * 症状是"我改了代码，页面没生效"，且刷新也救不回来（缓存优先）。
 * 这种噪音一旦出现，人的第一反应是怀疑自己的改动，而不是怀疑 SW。
 *
 * ## 为什么失败不弹提示
 *
 * SW 是**锦上添花**：没有它，应用照常联网可用（这正是 `install` 里
 * "必需项拿不到就让整个 install 失败"的同一套取舍）。
 * 弹窗会把一件"用户无法处理、也不影响使用"的事推到用户面前。
 * ⇒ 用 `console.warn` 留在 DevTools 里给**开发者**看。
 */
export default function SwRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;

    navigator.serviceWorker.register("/sw.js").catch((err: unknown) => {
      console.warn("[pwa] Service Worker 注册失败（不影响联网使用）", err);
    });
  }, []);

  return null;
}
