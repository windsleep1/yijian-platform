"use client";

import { KeyRound, Loader2, Phone, ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { canEnterConsole, landingPath, roleLabel } from "@/lib/permission";

function LoginForm() {
  const { login, me, isLoading } = useAuth();
  const router = useRouter();
  const sp = useSearchParams();

  /** 落地页 = 显式 `?next=`（用户本来想去的地方），否则按权限挑一个进得去的模块。 */
  const resolveTarget = (perms: string[] | undefined | null) =>
    sp.get("next") || landingPath(perms) || "/forbidden";

  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 已登录用户直接回跳（middleware 也会拦一道，双保险）
  useEffect(() => {
    if (!isLoading && me) router.replace(resolveTarget(me.permissions));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, me, router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const user = await login(phone.trim(), password);
      // 登录成功但一个后台模块都没有的角色（student / teacher）会走到 403 页面 ——
      // 这里先给一句明确的提示，比让人对着空白页发愣好。
      //
      // ⚠️ 判定依据是「有没有任意一个模块的入口权限」，不是写死的那两个权限码。
      //    Batch 4 的 researcher 只有 question:read，用写死列表会把它误杀。
      if (!canEnterConsole(user.permissions)) {
        setError(
          `登录成功，但当前角色（${user.roles.map(roleLabel).join("、") || "无"}）` +
            "没有任何后台权限，请联系管理员分配。",
        );
        setSubmitting(false);
        return;
      }
      router.replace(resolveTarget(user.permissions));
    } catch (err) {
      // 登录接口的错误**不走**统一 toast：这是表单自身的错误，
      // 应该贴着表单显示（"密码错误"弹在屏幕角落会让人找不到原因）。
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "登录失败，请稍后重试";
      setError(msg);
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 rounded-full bg-primary/10 p-3">
            <ShieldCheck className="h-6 w-6 text-primary" />
          </div>
          <h1 className="text-lg font-semibold">一建通 · 管理后台</h1>
          <p className="mt-1 text-sm text-muted-foreground">请使用管理员账号登录</p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-lg border bg-card p-6 shadow-sm">
          <div className="space-y-1.5">
            <Label htmlFor="phone">手机号</Label>
            <div className="relative">
              <Phone className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="phone"
                name="phone"
                autoComplete="username"
                inputMode="numeric"
                placeholder="11 位手机号"
                className="pl-9"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="password">密码</Label>
            <div className="relative">
              <KeyRound className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                placeholder="请输入密码"
                className="pl-9"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          </div>

          {error ? (
            <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs leading-relaxed text-destructive">
              {error}
            </p>
          ) : null}

          <Button type="submit" className="w-full" disabled={submitting || !phone || !password}>
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                登录中…
              </>
            ) : (
              "登录"
            )}
          </Button>
        </form>

        <div className="mt-4 rounded-md border border-dashed bg-card/60 p-3 text-xs leading-relaxed text-muted-foreground">
          <p className="font-medium text-foreground">本地联调默认超管</p>
          <p className="yj-json mt-1">13800000000 / Admin@123456</p>
          <p className="mt-1">
            该账号由 <code className="yj-json">python -m app.cli seed-admin</code> 创建
            （docker compose 启动时自动执行，本地验收脚本
            <code className="yj-json"> tools/local-verify/run-smoke.ps1</code> 也会执行）。
          </p>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    // useSearchParams 必须包 Suspense，否则 `next build` 直接报错
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
