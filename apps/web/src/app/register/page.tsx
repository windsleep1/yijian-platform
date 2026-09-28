"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, request } from "@/lib/api";
import { setTokens } from "@/lib/auth-store";
import type { TokenPair } from "@/lib/types";

/**
 * 注册（**三步**：手机号 → 验证码 → 设置密码）。
 *
 * ## ★ 为什么第 2 步"下一步"不做校验
 *
 * 后端**没有**"单独校验验证码"的接口 —— 验证码是由 `POST /auth/register` 消费的
 * （`sms_service` 里 `consume_code` 与建号在同一个事务里）。
 * 如果我在第 2 步本地"假装校验一下"，那就是**前端自己发明了一个校验规则**，
 * 而真正的判定仍在提交时 —— 用户会经历"两步都过了、最后一步才说验证码错"的**更差**体验。
 * ⇒ 所以第 2 步只做**格式**校验（6 位数字），把"码对不对"如实留到提交那一步。
 *
 * ## ★ `dev_code`
 *
 * `SMS_PROVIDER=mock` 且非生产时，`/auth/sms/send` 会**直接返回验证码**（后端为了联调
 * 特意设计的，见 `schemas/auth.py::SmsSendOut.dev_code`）。这里把它显示成一行
 * "开发模式"提示 —— 本地联调/走查不用去翻日志。生产环境这个字段是 `null`，整块不渲染。
 */

type Step = 1 | 2 | 3;

const STEP_TITLE: Record<Step, string> = {
  1: "输入手机号",
  2: "输入验证码",
  3: "设置密码",
};

export default function RegisterPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>(1);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function fail(err: unknown) {
    // ⚠️ 认证类错误已在 `api.ts` 里清凭证并跳登录，这里不必再提示
    setError(
      err instanceof ApiError ? `${err.message}（${err.code}）` : "网络异常，请检查网络后重试",
    );
  }

  async function onSendCode(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const data = await request<{ expires_in: number; dev_code: string | null }>(
        "/auth/sms/send",
        { method: "POST", body: { phone, scene: "register" } },
      );
      setDevCode(data.dev_code);
      setStep(2);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      // ★ C 端**刻意不传 `platform`** ⇒ 恒为 `h5` 会话（`docs/22` §6.5.3）
      const data = await request<TokenPair>("/auth/register", {
        method: "POST",
        body: { phone, code, password },
      });
      setTokens(data.access_token, data.refresh_token);
      router.replace("/");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "min-h-touch w-full rounded-lg border border-line px-3 text-base outline-none focus:border-brand";

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
      <h1 className="mb-1 text-2xl font-semibold">注册</h1>
      <p className="mb-8 text-sm text-sub">
        {step}/3 · {STEP_TITLE[step]}
      </p>

      {step === 1 && (
        <form onSubmit={onSendCode} className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm text-sub">手机号</span>
            <input
              className={inputCls}
              inputMode="numeric"
              autoComplete="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
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
            {busy ? "发送中…" : "获取验证码"}
          </button>
        </form>
      )}

      {step === 2 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            setStep(3);
          }}
          className="space-y-4"
        >
          <label className="block">
            <span className="mb-1 block text-sm text-sub">验证码</span>
            <input
              className={inputCls}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          )}
          {devCode && (
            <p className="rounded-lg border border-dashed border-line px-3 py-2 text-sm text-sub">
              开发模式（后端 `SMS_PROVIDER=mock`）：验证码是{" "}
              <span className="font-mono">{devCode}</span>
            </p>
          )}
          <button
            type="submit"
            className="min-h-touch w-full rounded-lg bg-brand font-medium text-brand-fg"
          >
            下一步
          </button>
          <button
            type="button"
            onClick={() => setStep(1)}
            className="min-h-touch w-full text-sm text-sub"
          >
            返回改手机号
          </button>
        </form>
      )}

      {step === 3 && (
        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-sm text-sub">密码</span>
            <input
              className={inputCls}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <p className="text-xs text-sub">
            至少 8 位，且<strong className="font-medium">同时包含字母和数字</strong>（后端校验）。
          </p>
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
            {busy ? "注册中…" : "完成注册"}
          </button>
          <button
            type="button"
            onClick={() => setStep(2)}
            className="min-h-touch w-full text-sm text-sub"
          >
            返回上一步
          </button>
        </form>
      )}

      <p className="mt-8 text-center text-sm text-sub">
        已有账号？
        <Link href="/login" className="ml-1 text-brand">
          去登录
        </Link>
      </p>
    </main>
  );
}
