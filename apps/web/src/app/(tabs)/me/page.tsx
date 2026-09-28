"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { request } from "@/lib/api";
import { clearTokens, hasToken } from "@/lib/auth-store";
import type { Me } from "@/lib/types";

/**
 * 我的（Tab 3）—— 资料 + 引导入口 + **退出登录**。
 *
 * ★ **退出登录从首页搬到这里**（P2a 的版式变化）：它属于"账号相关操作"，
 *   放在 Tab 3 才是 C 端的常规位置。⚠️ 因此 P1 的登录走查脚本也跟着改了
 *   —— 那不是回归，是**版式变了**（`docs/24` §13 已同步）。
 */
export default function MePage() {
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
      // 否则网络一抖用户就"退不出去"，而本地还留着有效的 refresh_token 更危险。
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

  const onboarded = !!me.profile?.onboarded_at;

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">我的</h1>
      <p className="mt-1 text-sm text-sub">{me.phone}</p>

      <dl className="mt-8 space-y-2 text-sm">
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">昵称</dt>
          <dd>{me.nickname || "—"}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">用户 ID</dt>
          {/* ★ 原样展示字符串 —— 绝不要 Number()（雪花 ID 超 JS 安全整数） */}
          <dd className="font-mono">{me.id}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">角色</dt>
          <dd>{me.roles.join("、") || "—"}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">报考专业</dt>
          <dd>{me.profile?.professional ?? "未设置"}</dd>
        </div>
        <div className="flex justify-between border-b border-line py-2">
          <dt className="text-sub">考试年份</dt>
          <dd>{me.profile?.exam_year ?? "未设置"}</dd>
        </div>
        <div className="flex justify-between py-2">
          <dt className="text-sub">引导</dt>
          <dd>{onboarded ? "已完成" : "未完成"}</dd>
        </div>
      </dl>

      <Link
        href="/onboarding"
        className="min-h-touch mt-8 flex w-full items-center justify-center rounded-lg border border-line font-medium"
      >
        {onboarded ? "修改报考信息" : "去完成引导"}
      </Link>

      <button
        onClick={() => void onLogout()}
        disabled={busy}
        className="min-h-touch mt-4 w-full rounded-lg border border-line font-medium text-danger disabled:opacity-60"
      >
        {busy ? "退出中…" : "退出登录"}
      </button>
    </main>
  );
}
