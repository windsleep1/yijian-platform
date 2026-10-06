/* 一建通 · 个人 PWA 的 Service Worker（`apps/pwa/public/sw.js`）
 *
 * ## 它唯一的承诺
 * **应用壳（HTML / JS / CSS / 图标）离线能打开。**
 *
 * ## ★★ 它**不是**离线能力的地基 —— 那件事归 IndexedDB（方案 §2.2）
 * 题库、答题记录、错题、收藏、标记、笔记**全在 IndexedDB**。
 * 本文件**不缓存** `pwa-bank.json`、也不做任何"数据请求的运行时缓存"。
 *
 * 为什么这条要写死在这儿：直觉会让人"用 SW 把题库缓存起来"，而那有两个后果
 *   ① 13.6 MB 的包再存一份（占双份空间）；
 *   ② 换题库之后 SW 与 IndexedDB **各有一份**、互相不一致 ——
 *      症状是「**导入成功了但题目还是旧的**」，而且**不报错**。
 * ★ 判据（进 `check-invariants.py` 不变量 12）：本文件的预缓存清单里
 *   **不许出现任何数据文件名**（`pwa-bank` / `questions.json` / `.sql`）。
 *
 * ## 与 C 端 `apps/web/public/sw.js` 的两处**刻意的差别**
 * | | C 端 | 本文件 |
 * |---|---|---|
 * | 导航请求 | network-first，**失败才**回离线页 | **cache-first**（离线优先）+ 后台更新 |
 * | 页面 HTML | **不缓存**（登录态相关，缓存它会引入"陈旧页面"） | **缓存**（这就是"壳"本身） |
 * ⇒ 差别的原因见上面：C 端的数据在**网络**上，缓存 HTML 只会制造陈旧；
 *   而本应用的数据**本来就在本机**，壳不缓存就只剩一个浏览器错误页。
 *
 * ## 已知边界（如实写，不假装）
 * 离线时**只有你访问过的页面**打得开（每条路由的 HTML 各是一份）。
 * 所以下面把 `/`、`/practice`、`/setup` 三个主路由**预缓存**了；
 * 具体的答题页要"进去过一次"才能离线打开。
 */

const VERSION = "v1";

/*: 缓存命名空间前缀 —— `activate` 时只清理**我们自己**的旧缓存，不碰别人的。 */
const NS = "yj-pwa-";

const CACHE = NS + VERSION;

/*: 离线兜底页。**必缓存**：它拿不到 ⇒ "不白屏"这个承诺就是假的（见 `precacheOne`）。 */
const OFFLINE_URL = "/offline.html";

/*: `cache-first` 的白名单（内容寻址、不会变）。⚠️ 不变量会解析这个数组：
 *   必须含 `/_next/static/`，且**任何一项都不许指向接口或数据文件**。 */
const CACHE_PREFIXES = ["/_next/static/", "/icons/"];

/*: 应用壳路由（预缓存，尽力而为）。 */
const SHELL_ROUTES = ["/", "/practice", "/setup"];

/*: 预缓存清单。`OFFLINE_URL` 与 `/` 是**必需**（拿不到就没有壳），其余尽力而为。 */
const PRECACHE_REQUIRED = [OFFLINE_URL, "/"];
const PRECACHE_OPTIONAL = [
  ...SHELL_ROUTES.filter((u) => u !== "/"),
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/manifest.webmanifest",
];

/* ---------------------------------------------------------------- 工具 */

function isCacheable(url) {
  return CACHE_PREFIXES.some((p) => url.pathname.startsWith(p));
}

/**
 * 预缓存一项。
 *
 * ★★ **命中重定向视为失败**：`fetch()` 会**静默跟随** 302，于是
 *    "抓到了 200" 实际上是**别的页面**。不查 `res.redirected` 的话，
 *    install 会成功、但用户离线时打开的是错的那一页。
 *    ⇒ 判据：**要缓存的东西，必须验它真的是那个东西**，不能只看状态码。
 */
async function precacheOne(cache, url) {
  const res = await fetch(url, { cache: "reload", redirect: "follow" });
  if (!res.ok || res.redirected) {
    throw new Error(`${url} ⇒ HTTP ${res.status}${res.redirected ? "（被重定向了）" : ""}`);
  }
  await cache.put(url, res);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      /* 必需项：失败要让 install **整体失败**。
         ⇒ 宁可"没有 SW"（应用照常联网用），也不要"有 SW 但它缓存的是错的页面"。 */
      for (const url of PRECACHE_REQUIRED) {
        await precacheOne(cache, url);
      }
      await Promise.all(
        PRECACHE_OPTIONAL.map((url) =>
          precacheOne(cache, url).catch((e) => console.warn("[sw] 预缓存跳过", url, e.message)),
        ),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((n) => n.startsWith(NS) && n !== CACHE).map((n) => caches.delete(n)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;

  // 规则 ①：只处理 GET
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // 规则 ②：跨域不接管
  if (url.origin !== self.location.origin) return;

  // 规则 ④：页面导航 —— **cache-first**（离线优先），后台顺手更新
  if (req.mode === "navigate") {
    event.respondWith(
      (async () => {
        const cached = await caches.match(req, { ignoreSearch: true });
        // 后台更新：**不 await**（离线时这个 fetch 会失败，那我们本来就在用缓存）
        const revalidate = fetch(req)
          .then(async (res) => {
            if (res && res.ok && !res.redirected) {
              const copy = res.clone();
              const c = await caches.open(CACHE);
              await c.put(req, copy);
            }
            return res;
          })
          .catch(() => null);

        if (cached) return cached;

        const fresh = await revalidate;
        if (fresh) return fresh;

        const offline = await caches.match(OFFLINE_URL);
        if (offline) return offline;
        return new Response("当前离线，且离线页没缓存成功。", {
          status: 503,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      })(),
    );
    return;
  }

  // 规则 ③：只有白名单前缀才接管（**默认拒绝**）
  if (!isCacheable(url)) return;

  event.respondWith(
    (async () => {
      const cached = await caches.match(req);
      if (cached) return cached;
      const res = await fetch(req);
      if (res && res.ok && !res.redirected) {
        const copy = res.clone();
        caches
          .open(CACHE)
          .then((c) => c.put(req, copy))
          .catch(() => {});
      }
      return res;
    })(),
  );
});
