"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { request } from "@/lib/api";
import { clearTokens, hasToken } from "@/lib/auth-store";
import type { Me } from "@/lib/types";

/**
 * 首页 —— P1 闭环的中段：**显示当前用户信息 + 退出登录**。
 *
 * ⚠️ 它**刻意长得很丑**（`docs/24` §1.1 第 4 行：首页不做 dashboard 聚合）。
 * P1 要验的是"数据能不能从后端拿到、退出能不能真的退掉"，
 * 不是"首页好不好看"。真正的首页（Tab 布局 / 入口卡片）在 P2，那时才动版式。
 *
 * ## 这里有两个**必须分开**的失败
 *
 * | 情况 | 表现 | 处理 |
 * |---|---|---|
 * | **没 token** | 还没发请求 | 直接 `replace("/login")` —— 不发无谓的 401 |
 * | **token 失效/被撤销** | 后端回**认证类**业务码 | `api.ts` 已经清凭证 + 跳登录，**本页不要再跳**（会横跳） |
 *
 * 第二条正是 P0 会话墙带来的：**登出即时生效** —— 在别处登出后，这个页面
 * 下一次请求就会被会话校验拦下（`docs/24` §9.5）。
 *
 * ⚠️ 上面**刻意不写具体码值**：`tools/local-verify/check-auth-chain.mjs` 明确禁止
 * 在 `apps/<app>/src` 里出现码字面量，**连注释也不许** —— 带码值的注释是"第二份说明"，
 * 改了码表却忘了改注释就是静默漂移。要引用就写模块名（`classifyAuthFailure`）。
 *
 * ⚠️ 顺带一个**写注释本身**的坑：这里原来写的是 `apps/<star>/src` 的**星号形式**，
 * 而"星号 + 斜杠"在块注释里**就是注释结束符** —— 于是注释被提前闭合、
 * 整个文件语法坏掉（`tsc` / `lint` / `prettier` 同时红）。写 glob 时用 `<app>` 之类的占位。
 */
export default function HomePage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!hasToken()) {
      router.replace("/login");
      return;
    }
    setError("");
    try {
      setMe(await request<Me>("/auth/me"));
    } catch (err) {
      // ⚠️ 认证类错误已经在 `api.ts` 里跳登录了；这里只处理**非认证**失败（如 50003 依赖不可用），
      //    并**明确告诉用户可重试** —— 否则就是一个白屏。
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onLogout() {
    setBusy(true);
    try {
      // 撤销服务端会话（`revoked_sessions`）；**失败也要清本地** ——
      // 否则网络一抖用户就"退不出去"，而本地还有 token 更危险。
      await request<{ revoked_sessions: number }>("/auth/logout", { method: "POST" });
    } catch {
      // 吞掉：登出的语义是"我要离开"，不该因为一次请求失败而留在原地
    } finally {
      clearTokens();
      router.replace("/login");
    }
  }

  if (error) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
        <button
          onClick={() => void load()}
          className="min-h-touch mt-4 w-full rounded-lg border border-line font-medium"
        >
          重试
        </button>
      </main>
    );
  }

  if (!me) {
    return <main className="mx-auto max-w-md px-6 py-10 text-sm text-sub">加载中…</main>;
  }

  return (
    <main className="mx-auto max-w-md px-6 py-10">
      <h1 className="text-2xl font-semibold">你好，{me.nickname || me.phone || "同学"}</h1>
      <p className="mt-1 text-sm text-sub">{me.phone}</p>

      <dl className="mt-8 space-y-2 text-sm">
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">用户 ID</dt>
          {/* ★ 原样展示字符串 —— **绝不要 Number() 它**（雪花 ID 超出 JS 安全整数） */}
          <dd className="font-mono">{me.id}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">角色</dt>
          <dd>{me.roles.join("、") || "—"}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">报考科目</dt>
          <dd>{me.profile?.professional ?? "未设置"}</dd>
        </div>
        <div className="flex justify-between py-2">
          <dt className="text-sub">引导</dt>
          <dd>{me.profile?.onboarded_at ? "已完成" : "未完成"}</dd>
        </div>
      </dl>

      <button
        onClick={() => void onLogout()}
        disabled={busy}
        className="min-h-touch mt-8 w-full rounded-lg border border-line font-medium disabled:opacity-60"
      >
        {busy ? "退出中…" : "退出登录"}
      </button>
    </main>
  );
}
