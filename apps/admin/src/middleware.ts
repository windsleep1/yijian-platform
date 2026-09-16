/**
 * 第一层守卫（Edge Runtime）—— 只看那个"提示位" cookie，**不做真实鉴权**。
 *
 * 为什么需要它：如果只靠客户端 `/auth/me` 判定，未登录用户会先看到控制台骨架屏
 * 闪一下再跳走。这里用一个非 httpOnly 的 cookie 做秒级重定向，纯粹为了体验。
 *
 * 为什么它**不能**当安全边界：
 *   - cookie 是前端自己写的，用户可以随手伪造，伪造后只是"能看到页面骨架"，
 *     所有接口仍然会返回 401 —— 真正的判定在 `AuthProvider` + 后端 RBAC。
 *   - Edge Runtime 读不到 localStorage，所以只能靠 cookie 传递这个提示。
 *
 * 也就是说：**双层守卫是体验设计，不是安全设计。** 别把它当权限控制用。
 */

import { NextResponse, type NextRequest } from "next/server";

const HINT_COOKIE = process.env.NEXT_PUBLIC_AUTH_HINT_COOKIE || "yj_authed";

const PUBLIC_PREFIXES = ["/login", "/forbidden"];

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const authedHint = req.cookies.get(HINT_COOKIE)?.value === "1";

  const isPublic = PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));

  if (isPublic) {
    // 已登录还去登录页 → 送进控制台首页，由它按权限决定落在哪个模块。
    //
    // 这里**刻意不写死 `/users`**：Edge Runtime 读不到 localStorage，
    // 拿不到当前用户的权限，任何写死的落地页都会对某个角色是 403。
    // `/` 是个纯客户端跳板，它能看到权限。（Batch 4 加题库模块时踩过这个坑。）
    if (pathname.startsWith("/login") && authedHint) {
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
