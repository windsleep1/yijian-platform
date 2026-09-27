/**
 * token 存取 —— 与 `apps/admin/src/lib/auth-store.ts` 同源（只复制，不共用，理由见 `json-bigint.ts` 抬头）。
 *
 * ## ⚠️ 已知反模式（留痕，首批不修）
 *
 * token 存在 `localStorage`。练手阶段可接受：不需要后端配合、没有跨域 cookie 的坑。
 * 生产应换 **httpOnly + Secure + SameSite=Lax Cookie + CSRF**，因为 localStorage 有两个硬伤：
 *
 *   1. **XSS 直接拿走长期凭据**（任何一处 XSS 都能读走 `refresh_token`，攻击者可长期 refresh）；
 *   2. **没有服务端侧失效入口** —— 前端存的东西服务端管不着。
 *      ⚠️ 但注意：**这一条的危害已经被 P0 的会话墙削掉一大半** ——
 *      `current_user` 现在会读 `user_sessions.revoked_at` ⇒ **服务端撤销即刻生效**，
 *      不再"等到自然过期"。这条正是 `docs/24` §9.5 记的那处语义升级。
 *
 * ## ★ C 端与 admin 的**一处刻意不同：提示位 cookie 的默认名**
 *
 * cookie 按 **host** 隔离、**不按端口** —— 本地两个 app 都在 `localhost` 上
 * （admin :3000 / web :3001）时，同名 cookie 会**互相串**：
 * 登了 admin 会让 web 的 middleware 以为已登录（于是点进受保护页、再由 `/auth/me`
 * 兜底跳回来）。⇒ C 端默认名是 **`yj_web_authed`**，admin 是 `yj_authed`。
 *
 * ## 路由守卫为什么要一个额外的 cookie
 *
 * `middleware.ts` 跑在 Edge Runtime，**读不到 localStorage** ⇒ 额外写一个
 * **非 httpOnly 的标志位 cookie**（不含任何凭据、伪造也没用），只做"未登录先跳登录页"的秒级提示。
 * 真正的判定永远是 `/auth/me`（第二层守卫）——**双层守卫是体验设计，不是安全设计**。
 */

const ACCESS_KEY = "yj_access_token";
const REFRESH_KEY = "yj_refresh_token";

//: ⚠️ 默认名与 admin **必须不同**（cookie 按 host 隔离、不按端口）—— 见抬头注释。
export const AUTH_HINT_COOKIE = process.env.NEXT_PUBLIC_AUTH_HINT_COOKIE || "yj_web_authed";

function store(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    // 隐私模式 / 禁用存储时 localStorage 会抛异常，降级为"仅内存会话"
    return null;
  }
}

export function getAccessToken(): string | null {
  return store()?.getItem(ACCESS_KEY) ?? null;
}

export function getRefreshToken(): string | null {
  return store()?.getItem(REFRESH_KEY) ?? null;
}

export function hasToken(): boolean {
  return !!getAccessToken();
}

export function setTokens(accessToken: string, refreshToken: string): void {
  const s = store();
  if (!s) return;
  s.setItem(ACCESS_KEY, accessToken);
  s.setItem(REFRESH_KEY, refreshToken);
  setAuthHint(true);
}

export function clearTokens(): void {
  const s = store();
  s?.removeItem(ACCESS_KEY);
  s?.removeItem(REFRESH_KEY);
  setAuthHint(false);
}

/** 写/清那个"提示位" cookie。不是鉴权凭据，只服务于 middleware 的快速跳转。 */
export function setAuthHint(on: boolean): void {
  if (typeof document === "undefined") return;
  const base = `${AUTH_HINT_COOKIE}=`;
  if (on) {
    document.cookie = `${base}1; path=/; max-age=${7 * 24 * 3600}; SameSite=Lax`;
  } else {
    document.cookie = `${base}; path=/; max-age=0; SameSite=Lax`;
  }
}
