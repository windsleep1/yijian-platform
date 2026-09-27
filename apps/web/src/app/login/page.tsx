"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import { hasToken, setTokens } from "@/lib/auth-store";
import type { TokenPair } from "@/lib/types";

/**
 * 登录页 —— P1 的闭环入口。
 *
 * ## ★ 为什么**不用** `useSearchParams()`（这是个真实的坑，记一笔）
 *
 * 读 `?next=` 最直觉的写法是 `useSearchParams()`。但它会让这个路由**退化成客户端渲染**，
 * Next 会要求在 `<Suspense>` 边界里用 —— 于是 **SSR 出来的 HTML 只有 fallback**：
 *
 *     一建通 加载中…      ← 实测：`curl /login` 拿到的就是这个（5000 字节的壳）
 *
 * **而登录页恰恰是最不该这样的一屏**：它是 App 的入口，移动端弱网时用户会先盯着
 * "加载中…"好几秒等 JS 到位。⇒ 改成在 `useEffect` 里读 `window.location.search`：
 * 它**不参与渲染**，所以既不产生 hydration 不一致，页面也照常被预渲染。
 *
 * ⚠️ 顺便：`next` 必须做**同源校验**（只接受以 `/` 开头）—— 否则
 * `?next=https://evil.com` 就是一个**开放重定向**（钓鱼最爱用"看起来是本站在跳转"）。
 */
export default function LoginPage() {
  const router = useRouter();

  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  /** 登录后回哪里。**只接受站内路径**（防开放重定向）。 */
  function readNext(): string {
    if (typeof window === "undefined") return "/";
    const v = new URLSearchParams(window.location.search).get("next");
    return v && v.startsWith("/") && !v.startsWith("//") ? v : "/";
  }

  // 已经有 token 就别停在登录页（例如手敲了 /login）
  useEffect(() => {
    if (hasToken()) router.replace(readNext());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- readNext 是纯函数，无依赖
  }, [router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      // ★ **C 端刻意不传 `platform`** —— 因此恒为 `h5` 会话，永远拿不到管理端会话
      //   （`docs/22` §6.5.3 第 4 步）。这不是"忘了传"，是设计。
      const data = await request<TokenPair>("/auth/login/password", {
        method: "POST",
        body: { phone, password },
      });
      setTokens(data.access_token, data.refresh_token);
      router.replace(readNext());
    } catch (err) {
      // ⚠️ 认证类错误已经在 `api.ts` 里清凭证并跳转了，这里不必再提示
      setError(
        err instanceof ApiError ? `${err.message}（${err.code}）` : "网络异常，请检查网络后重试",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
      <h1 className="mb-1 text-2xl font-semibold">一建通</h1>
      <p className="mb-8 text-sm text-sub">登录后开始刷题</p>

      <form onSubmit={onSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-1 block text-sm text-sub">手机号</span>
          <input
            className="min-h-touch w-full rounded-lg border border-line px-3 text-base outline-none focus:border-brand"
            inputMode="numeric"
            autoComplete="username"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            required
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm text-sub">密码</span>
          <input
            className="min-h-touch w-full rounded-lg border border-line px-3 text-base outline-none focus:border-brand"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="min-h-touch w-full rounded-lg bg-brand font-medium text-brand-fg disabled:opacity-60"
        >
          {busy ? "登录中…" : "登录"}
        </button>
      </form>

      {/* ⚠️ P1 只做**登录闭环**：注册页（`/register`）是 P2 ——
          这里**刻意不放"去注册"的链接**：指向一个还不存在的路由会 404，
          而"页面能点进去"是 C 端最基本的体验底线（比少一个入口严重）。
          P2 加 `/register` 时，把入口补在这里。 */}
    </main>
  );
}
