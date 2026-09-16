/**
 * token 存取。
 *
 * ⚠️ **已知反模式（留痕，本批不修）**
 * ---------------------------------------------------------------
 * token 存在 `localStorage`。这在练手阶段是可以接受的取舍，因为它最省事：
 * 不需要后端配合（无需 Set-Cookie + SameSite + CSRF 双提交），
 * 前端 `fetch` 直接带 `Authorization` 头即可，也没有跨域 cookie 的坑。
 *
 * 但生产环境应当换成 **httpOnly + Secure + SameSite=Lax 的 Cookie + CSRF Token**，
 * 原因是 localStorage 有两个硬伤：
 *
 *   1. **XSS 直接拿走长期凭据。** 任何一处 XSS（第三方脚本、富文本渲染、
 *      一个 `dangerouslySetInnerHTML`）都能 `localStorage.getItem()` 读走 refresh_token，
 *      攻击者可以长期 refresh。httpOnly cookie 读不到，XSS 只能"借当前会话"发请求，
 *      危害面小得多。
 *   2. **没有服务端侧失效入口。** 前端存的东西服务端管不着，用户"退出登录"只是本地删掉，
 *      被窃取的 token 在过期前仍然有效。Cookie + 服务端会话表才能做到即时踢线。
 *
 * 换 Cookie 的代价（也是为什么本批不做）：必须显式配置 CORS `allow_origins`
 * （`allow_credentials=true` 时不允许用 `*`），并额外实现 CSRF 防护
 * （双提交 cookie 或 SameSite + 自定义头校验）。
 * 详细讨论见 `apps/admin/docs/B端联调坑.md` 第 15 条与 `docs/09` 的联调坑一节。
 *
 * ## 路由守卫为什么要一个额外的 cookie
 *
 * `middleware.ts` 跑在 Edge Runtime，**读不到 localStorage**。
 * 所以这里额外写一个**非 httpOnly 的标志位 cookie**（`yj_authed=1`），
 * 它不含任何凭据、伪造也没有用，只是让 middleware 做"未登录先跳登录页"的秒级提示。
 * 真正的判定永远是 `/auth/me`（第二层守卫）。双层守为的是**体验**，不是安全。
 */

const ACCESS_KEY = "yj_access_token";
const REFRESH_KEY = "yj_refresh_token";

export const AUTH_HINT_COOKIE =
  process.env.NEXT_PUBLIC_AUTH_HINT_COOKIE || "yj_authed";

/** 当前用户信息不放 localStorage —— 由 AuthProvider 用 TanStack Query 持有，
 *  登录成功后直接把 `/auth/login` 返回的 user 灌进 query cache，无需二次落盘。 */

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
