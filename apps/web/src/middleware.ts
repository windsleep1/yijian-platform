/**
 * 第一层守卫（Edge Runtime）—— 只看那个"提示位" cookie，**不做真实鉴权**，
 * 纯粹为了体验：否则未登录用户会先看到首页骨架闪一下再跳走。
 *
 * ⚠️ **它不能当安全边界**：cookie 是前端自己写的、用户随手可伪造，
 * 伪造后只是"能看到页面骨架"，所有接口仍然 401
 * —— 真正的判定在 `/auth/me`（`page.tsx`）+ 后端。
 * **双层守卫是体验设计，不是安全设计。**
 *
 * ⚠️ 与 `apps/admin/src/middleware.ts` 的**唯一实质差别**：cookie 名不同
 * （`yj_web_authed` / `yj_authed`）。原因是 cookie 按 **host** 隔离、**不按端口** ——
 * 本地两个 app 都在 `localhost` 上，同名会互相串。详见 `auth-store.ts` 抬头。
 */

import { NextResponse, type NextRequest } from "next/server";

const HINT_COOKIE = process.env.NEXT_PUBLIC_AUTH_HINT_COOKIE || "yj_web_authed";

/** C 端目前只有登录页是公开的（注册页 P2 加进来时**必须同步加到这里**）。 */
const PUBLIC_PREFIXES = ["/login"];

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const authedHint = req.cookies.get(HINT_COOKIE)?.value === "1";
  const isPublic = PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));

  if (isPublic) {
    // 已登录还去登录页 → 送回首页（首页自己会再问 `/auth/me` 确认）
    if (authedHint) {
      const url = req.nextUrl.clone();
      url.pathname = "/";
      url.search = "";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  if (!authedHint) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // 排除静态资源与图片，避免 middleware 在 /_next/static 上白跑一遍
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
