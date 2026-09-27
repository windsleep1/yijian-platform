/**
 * C 端的统一 HTTP 客户端：信封解包 + trace_id 透出 + 认证链路的**编排**。
 *
 * ★ **认证链路（该不该重试 / 单飞 / Rotation 存回）不在这个文件里** ——
 * 那三处必须与 `apps/admin` **逐字一致**，所以**唯一实现**在
 * `packages/api-core`（`@yijian/api-core`）。本文件只负责**调用**它。
 *
 * ⚠️ **这是 `packages/api-core` 的第二个消费方**（第一次真实使用）。
 * 用得不顺手的地方要**反馈回包**，不要"为了用它而写不该写的代码" ——
 * 判据（用户 2026-09-27）：**"有没有为了用它而写了不该写的代码？"** 有 ⇒ 包的分层要调整。
 * （当前结论见 `docs/24` §11。）
 *
 * 本文件剩下的部分仍是"坑最密"的地方：
 *
 *  1. **trace_id 有两个来源** —— 业务错误在信封里（`body.trace_id`），
 *     网关/框架层错误（502 HTML、nginx 超时页）没有信封，只能读响应头 `X-Request-ID`
 *     （后端 CORS 里已 `expose_headers`，否则前端读不到）。
 *  2. **没有信封 ≠ 网络失败** —— 先读 text 再尝试解析；204 空响应、网关 HTML
 *     都不能无脑 `res.json()`。
 *  3. **跳登录只在本文件这一处**（`redirectToLogin`）：已经在登录页就不再跳，
 *     否则刷新失败时会来回横跳；跳之前必须 `clearTokens()`，
 *     否则坏 token 留在本地，用户卡在死页面上连手动登出都做不到。
 *  4. **雪花 ID 两个方向都要体检**（响应侧 `assertNoUnsafeIds` / 请求侧
 *     `assertNoUnsafeIdInBody`）—— ID 是 18~19 位，`JSON.parse` / `JSON.stringify`
 *     都会静默舍入。P1 只传手机号/密码（不含 ID），但这两道**现在就装上**：
 *     它们是"漏了就只在特定取值下才炸"的那类（见 `assertNoUnsafeIdInBody` 注释里的实测），
 *     等 P2 真的开始传 ID 时再补，多半会漏。
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

  /** 认证类错误且已经走过刷新仍失败 —— 此时 api.ts 已经发起跳转，UI 不该再弹提示。 */
  get isAuthRedirect(): boolean {
    return this.httpStatus === 401;
  }
}

/**
 * 认证链路的唯一实例：码集判定 + 单飞刷新 + Rotation 成对存回。
 * 存储走**依赖注入** —— 包不知道 localStorage 是谁，将来 C 端迁 httpOnly cookie 时**包不用改**。
 */
const authChain = createSingleFlightRefresh({
  apiBase: API_BASE,
  storage: { getRefreshToken, setTokens },
  parseJson: (text) => parseJsonSafe<unknown>(text),
});

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  // 已经在登录页就不要再跳，否则刷新失败时会来回横跳
  if (window.location.pathname.startsWith("/login")) return;
  const here = window.location.pathname + window.location.search;
  clearTokens();
  window.location.href = `/login?next=${encodeURIComponent(here)}`;
}

/** 开发期体检：响应里若出现"超出安全整数范围的裸数字"，说明有接口漏了 `BigIntStr`。 */
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
 * ⚠️ 为什么响应方向的检查拦不住它：出问题的不是后端下发的数据，
 * 而是**我们自己发出去的数据** —— ID 在 `JSON.stringify` **之前**就已经被 float64 舍入，
 * 后端查不到这些 ID，于是返回 `code:0, deleted:0, skipped:[...]`
 * —— **HTTP 200、业务码 0、不抛错**，UI 写"已删除 0 条"，用户以为成功了。
 *
 * ⚠️ 而且它**不是必然触发、是取值相关**：本项目雪花 ID 布局 `ts << 22 | worker << 12 | seq`，
 * worker_id=1 时低 22 位 = `4096 | seq`；ID 量级约 3.7e17，float64 的 ULP 恰好是 64，
 * 而 4096 是 64 的倍数 ⇒ `seq = 0` 无损、`seq = 1..63` **失真且 63 个不同 ID 塌缩成同一个值**。
 * 手点新建几乎总落在不同毫秒的 seq=0，所以一直没暴露；一旦走批量/并发立刻翻车。
 *
 * 只告警不拦截（生产环境被 `NODE_ENV` 短路）。
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
 * `doFetch` 是闭包而不是 `Response`，因为 401 刷新后需要**原样重放**这次请求（含请求体）。
 * 抽成 `send` 之后，将来所有请求共用同一套「信封 → 业务码 → 单飞刷新 → trace_id」逻辑。
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
  // ⚠️ 用 `switch` + `never` 守卫而不是几个 `if`：**新增一种 kind 却忘了处理会变成编译错误**。
  //    这正是把码表搬进共享包换来的东西（`docs/22` §9.2.2 的"编译期穷尽"）。
  const kind = classifyAuthFailure(body.code);
  switch (kind) {
    case "fatal":
      // 凭证本身不合法：refresh 也救不回来，**必须**清凭证跳登录 ——
      // 否则坏 token 留在本地，用户卡在死页面上反复点"重试"
      redirectToLogin();
      throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);

    case "retryable": {
      // 认证类错误：单飞刷新后**重放一次**（已重试过就不再重试，否则死循环）
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

  // ---- 其余业务错误：交给调用方统一提示（带 trace_id）----
  throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);
}

/**
 * 发起请求并**返回解包后的 data**（不带信封）。
 * 业务码非 0 一律抛 `ApiError`。
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
