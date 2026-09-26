/**
 * `@yijian/api-core` 的单元测试（第 3 级保证：**钉全量码表 + 单飞语义**）。
 *
 * 跑法：`npm test`（= `node --test test/auth-chain.test.ts`，靠 Node 22 的 TS 类型擦除）。
 *
 * ⚠️ 这些断言是**值级**的，不是"性质级"的 —— 判据：**改一个码 / 删掉单飞，必须变红**。
 * 变异验证记录见 `docs/22` §9.2.2 与提交说明。
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  FATAL_AUTH_CODES,
  RETRYABLE_AUTH_CODES,
  classifyAuthFailure,
  createSingleFlightRefresh,
} from "../src/index.ts";

/** 造一个受控的假 storage —— 顺带记录调用次数（断言"确实被调用过"，不只看返回值）。 */
function makeStorage(refreshToken: string | null = "rt-old") {
  const calls: { access: string; refresh: string }[] = [];
  return {
    calls,
    getRefreshToken: () => refreshToken,
    setTokens: (access: string, refresh: string) => {
      calls.push({ access, refresh });
    },
  };
}

/** 造一个假 fetch：返回指定信封，并记录调用次数。 */
function makeFetch(body: unknown, init: { ok?: boolean; rejectWith?: Error } = {}) {
  let calls = 0;
  let lastUrl = "";
  const impl = (async (url: string | URL | Request) => {
    calls += 1;
    lastUrl = String(url);
    if (init.rejectWith) throw init.rejectWith;
    await new Promise((r) => setTimeout(r, 1)); // 让并发调用真的重叠
    return {
      status: init.ok === false ? 500 : 200,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }) as unknown as typeof fetch;
  return {
    impl,
    get calls() {
      return calls;
    },
    get lastUrl() {
      return lastUrl;
    },
  };
}

const okBody = {
  code: 0,
  message: "ok",
  data: { access_token: "at-new", refresh_token: "rt-new" },
};

// ---------------------------------------------------------------- 码表（值级）

test("码表：40100/40101/40104/40105 进刷新重试", () => {
  for (const code of [40100, 40101, 40104, 40105]) {
    assert.equal(classifyAuthFailure(code), "retryable", `code=${code}`);
    assert.equal(RETRYABLE_AUTH_CODES.has(code), true, `码集里应有 ${code}`);
  }
});

test("码表：40102 是致命的（刷新救不回来）", () => {
  assert.equal(classifyAuthFailure(40102), "fatal");
  assert.equal(FATAL_AUTH_CODES.has(40102), true);
});

test("码表：50003 是 other —— 绝不能触发跳登录（Redis 抖一下会踢掉全站）", () => {
  assert.equal(classifyAuthFailure(50003), "other");
  assert.equal(RETRYABLE_AUTH_CODES.has(50003), false);
  assert.equal(FATAL_AUTH_CODES.has(50003), false);
});

test("码表：40103（密码错误）与 0（成功）都落到 other", () => {
  assert.equal(classifyAuthFailure(40103), "other");
  assert.equal(classifyAuthFailure(0), "other");
});

test("码集本身是闭集合：只有这四个可重试、只有一个致命", () => {
  assert.deepEqual(
    [...RETRYABLE_AUTH_CODES].sort((a, b) => a - b),
    [40100, 40101, 40104, 40105],
  );
  assert.deepEqual([...FATAL_AUTH_CODES], [40102]);
});

// ---------------------------------------------------------------- 单飞（行为级）

test("单飞：并发三次只发一次请求，且成对存回新 token", async () => {
  const storage = makeStorage();
  const f = makeFetch(okBody);
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
  });

  const [a, b, c] = await Promise.all([chain.refresh(), chain.refresh(), chain.refresh()]);

  assert.deepEqual([a, b, c], [true, true, true]);
  assert.equal(f.calls, 1, "并发刷新必须只发一次（否则 Rotation 下第二个请求会 40105）");
  assert.deepEqual(storage.calls, [{ access: "at-new", refresh: "rt-new" }]);
  assert.equal(f.lastUrl, "http://x/api/v1/auth/refresh");
});

test("单飞：一轮结束后还能再发起（inflight 必须在 finally 里复位）", async () => {
  const storage = makeStorage();
  const f = makeFetch(okBody);
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
  });

  await chain.refresh();
  await chain.refresh();
  assert.equal(f.calls, 2, "第一轮结束后 inflight 未复位 ⇒ 后续刷新被永久锁死");
});

test("没有 refresh_token 时不发请求，直接判失败", async () => {
  const storage = makeStorage(null);
  const f = makeFetch(okBody);
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
  });

  assert.equal(await chain.refresh(), false);
  assert.equal(f.calls, 0);
  assert.deepEqual(storage.calls, []);
});

test("业务码非 0 时判失败，且**不写**任何 token", async () => {
  const storage = makeStorage();
  const f = makeFetch({ code: 40105, message: "refresh token 已作废" });
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
  });

  assert.equal(await chain.refresh(), false);
  assert.deepEqual(storage.calls, [], "失败态绝不能写 token");
});

test("★ 响应缺 refresh_token 时判失败 —— 不允许出现“只存了 access”的半截状态", async () => {
  const storage = makeStorage();
  const f = makeFetch({ code: 0, message: "ok", data: { access_token: "at-new" } });
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
  });

  assert.equal(await chain.refresh(), false);
  assert.deepEqual(storage.calls, [], "缺字段时必须整体判失败，而不是存一半");
});

test("网络异常时判失败，且 inflight 复位（下一次仍会真的发请求）", async () => {
  const storage = makeStorage();
  const f = makeFetch(okBody, { rejectWith: new Error("boom") });
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
  });

  assert.equal(await chain.refresh(), false);
  assert.equal(await chain.refresh(), false);
  assert.equal(f.calls, 2, "异常路径也必须复位 inflight");
});

test("空响应体（204 之类）判失败，不抛异常", async () => {
  const storage = makeStorage();
  const f = makeFetch(okBody);
  const impl = (async () =>
    ({ status: 204, text: async () => "" }) as unknown as Response) as unknown as typeof fetch;
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: impl,
  });

  assert.equal(await chain.refresh(), false);
  assert.equal(f.calls, 0);
});

// ---------------------------------------------------------------- 注入 / 超时

test("注入的 parseJson 确实被调用过（证明它没被忽略，而不是“结果碰巧对”）", async () => {
  const storage = makeStorage();
  const f = makeFetch(okBody);
  let seen = "";
  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: f.impl,
    parseJson: (text: string) => {
      seen = text;
      return JSON.parse(text) as unknown;
    },
  });

  assert.equal(await chain.refresh(), true);
  assert.ok(seen.includes("at-new"), "注入的解析器应收到原始响应文本");
});

test("刷新有超时上界（硬约定 N：不得无界等待）", async () => {
  const storage = makeStorage();
  // 假 fetch：只有在 signal abort 时才 reject —— 模拟"对端不回话"
  const impl = ((_url: unknown, options?: { signal?: AbortSignal }) =>
    new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => {
        reject(new Error("aborted"));
      });
    })) as unknown as typeof fetch;

  const chain = createSingleFlightRefresh({
    apiBase: "http://x/api/v1",
    storage,
    fetchImpl: impl,
    timeoutMs: 20,
  });

  const t0 = Date.now();
  assert.equal(await chain.refresh(), false);
  const elapsed = Date.now() - t0;
  assert.ok(elapsed < 1000, `应有超时上界，实际耗时 ${elapsed}ms`);
});
