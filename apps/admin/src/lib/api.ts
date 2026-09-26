/**
 * 统一的 HTTP 客户端：信封解包 + trace_id 透出 + 认证链路的**编排**。
 *
 * ## 认证链路（该不该重试 / 单飞 / Rotation 存回）**不在这个文件里**
 *
 * 那三处逻辑必须与 C 端（`apps/web`）**逐字一致**，所以它们的**唯一实现**在
 * `packages/api-core`（`@yijian/api-core`）—— 本文件只负责**调用**它。
 * 码表、以及"为什么并发刷新会把人踢出去"都写在那个模块的注释里：
 * **坑要挨着实现写，别在这儿再抄一份**（抄一份就是又造一个会漂的真相）。
 *
 * 本文件剩下的部分，仍然是"坑最密"的地方：
 *
 *  1. **trace_id 有两个来源。** 业务错误在信封里（`body.trace_id`），
 *     网关/框架层错误（502 HTML、nginx 超时页）没有信封，只能读响应头
 *     `X-Request-ID`（后端 CORS 里已 `expose_headers`，否则前端读不到）。
 *  2. **没有信封 ≠ 网络失败**：先读 text 再尝试解析 —— 204 空响应、网关 HTML
 *     都不能无脑 `res.json()`。
 *  3. **跳登录只在本文件这一处**（`redirectToLogin`）：已经在登录页就不再跳，
 *     否则刷新失败时会来回横跳；跳之前必须 `clearTokens()`，
 *     否则坏 token 留在本地，用户卡在死页面上连手动登出都做不到。
 *  4. **雪花 ID 两个方向都要体检**（响应侧 `assertNoUnsafeIds` / 请求侧
 *     `assertNoUnsafeIdInBody`）—— 见下面各自的注释。
 */

import { classifyAuthFailure, createSingleFlightRefresh } from "@yijian/api-core";

import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./auth-store";
import { looksLikeUnsafeId, parseJsonSafe } from "./json-bigint";
import type { Envelope } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(
    /** 业务码（0 表示成功，成功不会构造 ApiError） */
    readonly code: number,
    message: string,
    /** 排障用，UI 上要能一键复制 */
    readonly traceId: string,
    readonly httpStatus: number,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** 认证类错误且已经走过刷新仍失败 —— 此时 api.ts 已经发起跳转，UI 不该再弹 toast。 */
  get isAuthRedirect(): boolean {
    return this.httpStatus === 401;
  }
}

/**
 * 认证链路的唯一实例：码集判定 + 单飞刷新 + Rotation 成对存回。
 *
 * 实现、以及"为什么必须成对存回 / 为什么 `50003` 不能触发跳登录"都在
 * `packages/api-core`（本文件不再有第二份 —— 这正是它存在的理由）。
 * 存储走依赖注入：包不知道 localStorage 是谁，将来 C 端迁 cookie 时包不用改。
 */
const authChain = createSingleFlightRefresh({
  apiBase: API_BASE,
  storage: { getRefreshToken, setTokens },
  parseJson: (text) => parseJsonSafe<unknown>(text),
});

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  // 已经在登录页就不要再跳，否则刷新失败时会来回横跳
  if (window.location.pathname.startsWith("/login")) return;
  clearTokens();
  window.location.href = `/login?next=${encodeURIComponent(here)}`;
}

/** 开发期体检：响应里若出现"超出安全整数范围的裸数字"，说明有接口漏了 BigIntStr。 */
function assertNoUnsafeIds(data: unknown, path: string): void {
  if (process.env.NODE_ENV === "production") return;
  const seen = new Set<unknown>();
  const walk = (v: unknown, p: string) => {
    if (typeof v === "number" && looksLikeUnsafeId(v)) {
      // eslint-disable-next-line no-console
      console.warn(
        `[api] ${path}${p} 收到超出 JS 安全整数范围的 number（${v}）。` +
          "ID 应当由后端序列化为字符串，请检查该接口的 Pydantic 模型是否漏了 BigIntStr。",
      );
      return;
    }
    if (typeof v !== "object" || v === null || seen.has(v)) return;
    seen.add(v);
    if (Array.isArray(v)) v.forEach((x, i) => walk(x, `${p}[${i}]`));
    else Object.entries(v as Record<string, unknown>).forEach(([k, x]) => walk(x, `${p}.${k}`));
  };
  walk(data, "");
}

/**
 * 开发期体检（**请求方向**）：请求体里若出现"像雪花 ID 的裸数字"，立刻告警。
 *
 * 为什么必须补这一道 —— 这是 Batch 4 验收时真实踩到的坑：
 * 批量删除传的是 `ids: selected.map(Number)`，ID 在 `JSON.stringify` **之前**
 * 就已经被 float64 舍入。后端查不到这些 ID，于是返回
 * `code:0, deleted:0, skipped:[...]` —— **HTTP 200、业务码 0、不抛错**，
 * 前端 toast 写"已删除 0 道题"，用户以为删成功了，实际一条都没动。
 *
 * ⚠️ 这个坑**不是必然触发**，而是取值相关，所以更难发现：
 * 本项目的雪花 ID 布局是 `ts << 22 | worker << 12 | seq`，worker_id=1 时
 * 低 22 位 = `4096 | seq`。ID 量级约 3.7e17（位于 2^58~2^59），float64 的
 * ULP 恰好是 64，而 4096 是 64 的倍数 —— 于是：
 *   · `seq = 0`（每毫秒第一个 ID）→ mod 64 == 0 → **无损**；
 *   · `seq = 1..63`（同一毫秒内的第 2~64 个）→ mod 64 == 1..63 → **失真**，
 *     而且这 63 个不同的 ID 会**塌缩到同一个值**（都舍入到该毫秒 seq=0 那个数）。
 * 后台手点新建、每次一个 HTTP 往返，几乎总是落在不同毫秒的 seq=0，所以一直没暴露；
 * 一旦走批量导入 / 脚本刷数据 / 并发新建，同一毫秒连出多个 ID，立刻翻车。
 *
 * 响应方向的 `assertNoUnsafeIds` 拦不住它：出问题的不是后端下发的数据，
 * 而是**我们自己发出去的数据**。两个方向都要查，才算闭环。
 *
 * 只告警不拦截：本地开发时立刻能看见，生产环境该检查被 `NODE_ENV` 短路掉。
 */
function assertNoUnsafeIdInBody(body: unknown, path: string): void {
  if (process.env.NODE_ENV === "production") return;
  if (typeof body !== "object" || body === null) return;
  const seen = new Set<unknown>();
  const walk = (v: unknown, p: string) => {
    if (typeof v === "number" && looksLikeUnsafeId(v)) {
      // eslint-disable-next-line no-console
      console.warn(
        `[api] 请求 ${path} 的 body${p} 里出现了超出安全整数范围的 number（${v}）。` +
          "它很可能已经被舍入，后端会把它当成一个不存在的 ID。" +
          "ID 请一律以字符串传递，不要用 Number() 转换雪花 ID。",
      );
      return;
    }
    if (typeof v !== "object" || v === null || seen.has(v)) return;
    seen.add(v);
    if (Array.isArray(v)) v.forEach((x, i) => walk(x, `${p}[${i}]`));
    else Object.entries(v as Record<string, unknown>).forEach(([k, x]) => walk(x, `${p}.${k}`));
  };
  walk(body, "");
}

export type QueryValue = string | number | boolean | undefined | null;

/**
 * 查询参数容器。
 *
 * 故意放宽成 `unknown` 而不是 `QueryValue`：调用方想直接把
 * `ListUsersQuery` 这样的领域类型传进来，不该被迫写 `as Record<...>` 这种
 * "让类型系统闭嘴"的转换。合法性在下面运行时过滤，比编译期强转可靠。
 */
export type QueryParams = Record<string, unknown>;

export type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: QueryParams;
  body?: unknown;
  signal?: AbortSignal;
  /** 内部用：已重试过一轮就不再重试，避免死循环 */
  _retried?: boolean;
};

/**
 * 信封解包的**唯一出口**。
 *
 * `doFetch` 是个闭包而不是 `Response`，因为 401 刷新后需要**原样重放**这次请求
 * （含请求体）。抽成 `send` 之后，JSON 请求与 multipart 上传共用同一套
 * 「信封 → 业务码 → 单飞刷新 → trace_id」逻辑 —— 上传接口不需要另写一份，
 * 也就不会出现"上传路由忘了处理 401"这种只在断网/过期时才炸的分裂。
 */
async function send<T>(
  path: string,
  doFetch: () => Promise<Response>,
  allowAuthRetry: boolean,
): Promise<T> {
  const res = await doFetch();
  const headerTrace = res.headers.get("X-Request-ID") ?? "";

  // 先读 text 再尝试解析：204 空响应、网关 502 返回 HTML 都不能无脑 res.json()
  const text = await res.text();
  let body: Envelope<T> | null = null;
  if (text) {
    try {
      body = parseJsonSafe<Envelope<T>>(text);
    } catch {
      body = null;
    }
  }

  // ---- 没有信封：网络层 / 网关层错误 ----
  if (!body) {
    throw new ApiError(
      50001,
      `请求失败（HTTP ${res.status}）。响应不是本站约定的 JSON 信封，可能是网关错误。`,
      headerTrace,
      res.status,
    );
  }

  // ---- 业务成功 ----
  if (body.code === 0) {
    assertNoUnsafeIds(body.data, path);
    return body.data;
  }

  // ---- 认证类错误：三分类由 `classifyAuthFailure` 决定（单一真相 → packages/api-core）----
  //
  // ⚠️ 这里用 `switch` + `never` 守卫，而不是几个 `if`：**新增一种 kind 却忘了处理，
  //    会变成编译错误**。这正是把码表搬进共享包换来的东西（`docs/22` §9.2.2 第 2 级）。
  const kind = classifyAuthFailure(body.code);
  switch (kind) {
    // 凭证本身不合法：refresh 也救不回来，且**不能**留下"永远失败的重试键"
    case "fatal":
      redirectToLogin();
      throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);

    // 认证类错误：单飞刷新后**重放一次**（已重试过就不再重试，否则死循环）
    case "retryable": {
      if (!allowAuthRetry) break;
      const refreshed = await authChain.refresh();
      if (!refreshed) {
        redirectToLogin();
        throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);
      }
      return send<T>(path, doFetch, false);
    }

    case "other":
      break;

    default: {
      // 穷尽性守卫。走到这里说明 `AuthFailureKind` 加了新成员而上面没加分支。
      const unreachable: never = kind;
      throw new Error(`未处理的认证失败类型：${String(unreachable)}`);
    }
  }

  // ---- 其余业务错误：交给上层统一 toast（带 trace_id）----
  throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);
}

/**
 * 发起请求并**返回解包后的 data**（不带信封）。
 * 业务码非 0 一律抛 `ApiError`，由 TanStack Query 的 onError 统一 toast。
 */
export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  if (opts.body !== undefined) assertNoUnsafeIdInBody(opts.body, path);

  const url = new URL(`${API_BASE}${path}`);
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    // 注意：`false` 必须保留（`success=false` 是有效的筛选条件），
    // 只跳过 undefined / null / 空字符串。
    if (v === undefined || v === null || v === "") continue;
    if (typeof v !== "string" && typeof v !== "number" && typeof v !== "boolean") {
      if (process.env.NODE_ENV !== "production") {
        // eslint-disable-next-line no-console
        console.warn(`[api] 查询参数 ${k} 不是基本类型，已忽略：`, v);
      }
      continue;
    }
    url.searchParams.set(k, String(v));
  }

  const doFetch = async () => {
    const headers: Record<string, string> = {};
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    // token 每次都重新取：刷新后重放必须用新 token，闭包里缓存旧值就白刷了
    const at = getAccessToken();
    if (at) headers.Authorization = `Bearer ${at}`;

    return fetch(url.toString(), {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  };

  return send<T>(path, doFetch, !opts._retried);
}

/**
 * **multipart/form-data** 请求（文件上传）。
 *
 * 为什么不能复用 `request`：`Content-Type` 必须留给浏览器 ——
 * 它要自己生成 `boundary=...`。手写 `application/json` 或手拼
 * `multipart/form-data` 都会让后端解析失败（FastAPI 会直接 422/500）。
 *
 * ⚠️ **放进 FormData 的 ID 必须是字符串。** 浏览器会把数字转成十进制字符串，
 * 但**已经丢过精度的数字**转出来仍是错的（`375273861765140480` → `375273861765140500`）。
 * 所以调用方一律 `String(id)`，别指望 FormData 兜住 —— 这与 `assertNoUnsafeIdInBody`
 * 拦的是同一类问题，只是这里没有可结构化遍历的对象。
 *
 * 好处：401 单飞刷新、trace_id 提取、信封解包全部与 JSON 请求共用 `send`，
 * 上传路由不会成为认证链路上的例外。
 */
export async function uploadRequest<T>(
  path: string,
  form: FormData,
  opts: { signal?: AbortSignal } = {},
): Promise<T> {
  if (process.env.NODE_ENV !== "production") {
    for (const [k, v] of form.entries()) {
      if (typeof v === "number" && looksLikeUnsafeId(v)) {
        // eslint-disable-next-line no-console
        console.warn(
          `[api] 上传 ${path} 的表单字段 ${k} 是超出安全整数范围的 number（${v}）。` +
            "这可能已经被舍入，请改用 String(id)。",
        );
      }
    }
  }

  const doFetch = async () => {
    const headers: Record<string, string> = {};
    // 刻意不设 Content-Type —— 浏览器要自己补 boundary
    const at = getAccessToken();
    if (at) headers.Authorization = `Bearer ${at}`;
    return fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers,
      body: form,
      signal: opts.signal,
    });
  };

  return send<T>(path, doFetch, true);
}
