/**
 * 认证链路里**必须两端一致**的那三件事 —— 这是它们**唯一**的实现。
 *
 * ## 为什么它存在（而不是在两个 app 里各复制一份）
 *
 * 起因：C 端（`apps/web`）要复用 `apps/admin/src/lib/api.ts`。而那个文件里有三处
 * 「不一致就出事」的逻辑：
 *
 *   1. **401 单飞刷新** —— 后端是 Refresh Token Rotation。三个请求同时 401 时若各自去
 *      refresh，第二个到达的会因**旧 token 已作废**而拿到 `40105`：**用户莫名被踢**。
 *   2. **刷新后必须把新的 `refresh_token` 存回** —— Rotation 的语义就是"旧换新"，
 *      不存回 ⇒ 下一次刷新必然失败。
 *   3. **码集** —— `40100/40101/40104/40105` 才进刷新重试；`40102`（签名无效 / 类型不对 /
 *      被篡改）刷一百次也一样，**但"不重试"不等于"不跳登录"**：坏 token 留在本地，
 *      用户只会卡在死页面上，连手动登出的入口都没有。
 *      ⚠️ `50003`（依赖服务不可用）**不是认证错误，绝不能触发跳登录** ——
 *      否则 Redis 抖一下，全站用户都被踢出去。
 *
 * 原方案是"复制 + 两端注释互指 + 我小心"。**那不是机制**：不一致不会报错、不会变红，
 * 只会在某次 401 里表现为"用户莫名被踢" —— 而那时人在查后端 Rotation，不在看两个前端文件。
 * ⇒ 改成本模块：**消除第二份实现**。保证强度分级见 `docs/22-C端-方案.md` §9.2.2。
 *
 * ## 两个"结构性不变量"（靠类型/结构，不靠注释提醒）
 *
 * - **成对存回**：`refresh()` 只可能**成对**写 token —— 响应里缺 `refresh_token` 时直接判失败，
 *   不存在"只存了 access_token"这种半截状态（那正是第 2 条坑的形状）。
 * - **穷尽性**：`classifyAuthFailure()` 返回**闭集合**；调用方 `switch` 时被 `never` 守卫挡住 ——
 *   将来新增一种 kind，漏处理会变成**编译错误**，而不是运行时静默走错分支。
 *
 * ## 这个模块**不**负责什么
 *
 * 信封解包、`trace_id` 取值、雪花 ID 开发期体检、跳登录、以及 `send()` 里的重试编排 ——
 * 那些留在各 app 自己的 `lib/api.ts`（两端业务不同，不该强行共用）。
 * 存储也**不进这里**：只要求一个 `{ getRefreshToken, setTokens }` 形状（依赖注入），
 * 所以将来 C 端要迁 httpOnly cookie 时，**本模块一行都不用改**。
 */

/**
 * 值得"刷新一次再重试"的认证类错误码。
 * 刻意不含 `40102`（签名无效，重试无意义）与 `40103`（密码错误，属登录接口自身）。
 */
export const RETRYABLE_AUTH_CODES: ReadonlySet<number> = new Set([40100, 40101, 40104, 40105]);

/**
 * 认证类错误，但**刷新也救不回来** —— 凭证本身就不合法（`40102`：签名不对 / 类型不对 / 被篡改）。
 *
 * 与 `RETRYABLE_AUTH_CODES` 的差别**只在"要不要先试一次 refresh"**，不在"要不要跳登录"：
 * 两者都必须清凭证跳登录，否则用户卡在死页面上反复点"重试"。
 */
export const FATAL_AUTH_CODES: ReadonlySet<number> = new Set([40102]);

/** 认证失败的三分类 —— **闭集合**，这是穷尽性守卫能成立的前提。 */
export type AuthFailureKind = "retryable" | "fatal" | "other";

/**
 * 唯一的分类入口。
 * ⚠️ **`50003` 落到 `"other"`**（依赖服务不可用不是认证错误）—— 这条是刻意的，
 * 重写这段逻辑时最容易把它并进"认证错误"，后果是 Redis 抖动踢掉全站用户。
 */
export function classifyAuthFailure(code: number): AuthFailureKind {
  if (FATAL_AUTH_CODES.has(code)) return "fatal";
  if (RETRYABLE_AUTH_CODES.has(code)) return "retryable";
  return "other";
}

/** 取 token 的**唯一**渠道（各 app 注入自己的实现；本模块不知道 localStorage 是谁）。 */
export type RefreshStorage = {
  getRefreshToken(): string | null;
  /** ⚠️ 必须**成对**写：`setTokens(access, refresh)`。Rotation 下只写 access 是不合法的状态。 */
  setTokens(accessToken: string, refreshToken: string): void;
};

export type AuthChainOptions = {
  /** 与 `lib/api.ts` 的 `API_BASE` 同源（含 `/api/v1`） */
  apiBase: string;
  storage: RefreshStorage;
  /** 解信封用（各 app 的 `parseJsonSafe`）；默认 `JSON.parse`。返回体形状在包内自行校验。 */
  parseJson?: (text: string) => unknown;
  /** 测试注入用；默认全局 `fetch` */
  fetchImpl?: typeof fetch;
  /** 刷新请求的超时（毫秒）。**必须有界**（硬约定 N），默认 10s。 */
  timeoutMs?: number;
};

export type AuthChain = {
  /**
   * 刷新一次。**并发调用只会真正发起一次请求**，其余等同一个 Promise。
   *
   * @returns 是否拿到了新的 token 对（网络失败 / 业务码非 0 / 响应缺字段 一律 `false`）
   */
  refresh(): Promise<boolean>;
};

const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * 从响应体里取 token 对 —— **防御式**，这是"成对存回"不变量的落点。
 *
 * 只要 `code !== 0`、或 `data` 不是对象、或两个字段有一个不是字符串，就返回 `null`
 * ⇒ 上层判为"刷新失败"（跳登录），而**不会**进入"只存了一半"的状态。
 */
function readTokenPair(raw: unknown): { access_token: string; refresh_token: string } | null {
  if (typeof raw !== "object" || raw === null) return null;
  const envelope = raw as { code?: unknown; data?: unknown };
  if (envelope.code !== 0) return null;
  if (typeof envelope.data !== "object" || envelope.data === null) return null;
  const data = envelope.data as { access_token?: unknown; refresh_token?: unknown };
  if (typeof data.access_token !== "string" || typeof data.refresh_token !== "string") return null;
  return { access_token: data.access_token, refresh_token: data.refresh_token };
}

/**
 * 造一个"单飞"刷新器。
 *
 * 单飞的含义：并发 401 只允许发起**一次** `/auth/refresh`，其余等同一个 Promise。
 * `inflight` 在 `finally` 里复位 —— 否则一次网络抖动会把后续所有刷新**永久锁死**
 * （那同样是"用户莫名被踢"，只是原因更隐蔽）。
 */
export function createSingleFlightRefresh(opts: AuthChainOptions): AuthChain {
  const doFetch: typeof fetch = opts.fetchImpl ?? fetch;
  const parse = opts.parseJson ?? ((text: string) => JSON.parse(text) as unknown);
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  let inflight: Promise<boolean> | null = null;

  const refresh = (): Promise<boolean> => {
    inflight ??= (async () => {
      const rt = opts.storage.getRefreshToken();
      if (!rt) return false;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs);
      try {
        const res = await doFetch(`${opts.apiBase}/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: rt }),
          signal: ctrl.signal,
        });
        const text = await res.text();
        const pair = text ? readTokenPair(parse(text)) : null;
        if (!pair) return false;
        // Rotation：**成对**存回（新的 refresh_token 不存回 ⇒ 下次刷新必失败）
        opts.storage.setTokens(pair.access_token, pair.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        clearTimeout(timer);
        // 放在 finally：保证下一次还能再发起
        inflight = null;
      }
    })();
    return inflight;
  };

  return { refresh };
}
