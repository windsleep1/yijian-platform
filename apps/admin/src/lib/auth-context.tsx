"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { request } from "./api";
import { clearTokens, getAccessToken, setTokens } from "./auth-store";
import { can, canAny } from "./permission";
import type { MeOut, TokenPairOut } from "./types";

type AuthContextValue = {
  me: MeOut | null;
  roles: string[];
  permissions: string[];
  /** 仍在确定登录态/权限时为 true。UI 必须据此禁用"写"操作，避免闪现可点按钮。 */
  isLoading: boolean;
  isAuthenticated: boolean;
  /** /auth/me 失败时的错误（401 且刷新失败时 api.ts 已发起跳登录） */
  error: Error | null;
  hasPermission: (code: string) => boolean;
  hasAnyPermission: (codes: readonly string[]) => boolean;
  login: (phone: string, password: string) => Promise<MeOut>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<MeOut | null>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const router = useRouter();

  // localStorage 只在客户端可读。挂载前一律视为"未登录 + 加载中"，
  // 否则 SSR 出来的 HTML 与客户端首帧不一致（hydration mismatch）。
  const [mounted, setMounted] = useState(false);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    setToken(getAccessToken());
    setMounted(true);
  }, []);

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => request<MeOut>("/auth/me"),
    enabled: mounted && !!token,
    // 认证失败不做重试：刷新逻辑已在 api.ts 里单独实现（单飞），
    // 这里再包一层 retry 只会放大登录态的抖动。
    retry: false,
    staleTime: 30_000,
  });

  const login = useCallback(
    async (phone: string, password: string) => {
      const data = await request<TokenPairOut>("/auth/login/password", {
        method: "POST",
        body: { phone, password, platform: "pc" },
      });
      setTokens(data.access_token, data.refresh_token);
      // 登录响应里已经带了完整的 user（含 permissions），直接灌进缓存，
      // 省掉一次 /auth/me 往返 —— 打开后台第一屏更快。
      qc.setQueryData(["me"], data.user);
      setToken(data.access_token);
      return data.user;
    },
    [qc],
  );

  const logout = useCallback(async () => {
    try {
      await request("/auth/logout", { method: "POST" });
    } catch {
      // 登出接口失败不该阻塞用户离开：本地凭据一定要清掉。
      // （服务端那条 session 记录会随有效期自然过期。）
    }
    clearTokens();
    setToken(null);
    qc.clear();
    router.replace("/login");
  }, [qc, router]);

  const refreshMe = useCallback(async () => {
    const me = await request<MeOut>("/auth/me");
    qc.setQueryData(["me"], me);
    return me;
  }, [qc]);

  const value = useMemo<AuthContextValue>(() => {
    const me = meQuery.data ?? null;
    const permissions = me?.permissions ?? [];
    return {
      me,
      roles: me?.roles ?? [],
      permissions,
      isLoading: !mounted || (!!token && meQuery.isLoading),
      isAuthenticated: !!me,
      error: (meQuery.error as Error | null) ?? null,
      hasPermission: (code: string) => can(permissions, code),
      hasAnyPermission: (codes: readonly string[]) => canAny(permissions, codes),
      login,
      logout,
      refreshMe,
    };
  }, [meQuery.data, meQuery.error, meQuery.isLoading, mounted, token, login, logout, refreshMe]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 <AuthProvider> 内部使用");
  return ctx;
}
