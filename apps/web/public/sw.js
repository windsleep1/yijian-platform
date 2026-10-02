/* 一建通 · C 端 Service Worker（`apps/web/public/sw.js`）
 *
 * ## 它唯一的承诺
 * **静态资源能用缓存 + 离线打开不白屏。**
 * 它**不是**离线功能的地基 —— 离线答题 / 作答队列 / 回传同步属于 BL-21，本批明确不做。
 * 判据（`docs/26` §4 的 D3 / D4）：可安装 + 断网打开看到离线页（而不是浏览器错误页）。
 *
 * ## 三条规则 + 一条默认：**默认拒绝**，不是黑名单
 *   ① 非 GET         → 不接管
 *   ② 跨域           → 不接管（后端在**另一个域** ⇒ 它的响应天然进不了缓存；
 *                      将来若改成同域，靠 ③ 继续挡住）
 *   ③ 前缀不在白名单 → 不接管（**这一条挡住了所有接口路径**）
 *   ④ `mode=navigate` → network-first；**失败才**回离线页
 *
 * ★★ 为什么是"默认拒绝"而不是"排除接口"：黑名单要**穷举**所有不该缓存的东西
 *    （接口、鉴权、下载、埋点……），漏掉一个 = "**悄悄缓存了不该缓存的响应**"，
 *    而那种缺陷**不报错、也扫不出来**；白名单只会漏"本该缓存的没缓存"，
 *    症状是"慢一点"，**看得见**。同一份工作量，失败方向完全不同。
 *
 * ★ 为什么**不缓存页面 HTML**：HTML 与登录态相关且会变，缓存它等于引入"陈旧页面"
 *    这个新失败面；而我们的承诺只到"不白屏" ⇒ 导航一律走网络，只有**断网**才兜底。
 *
 * ★ 为什么**不给导航加超时**：加了就会把"**慢**"误判成"**离线**"。
 *    后端冷启动要 50 秒（虽然导航打的是 Vercel、不走后端），但没理由引入这个误判面。
 */

const VERSION = "v1";

/*: 缓存命名空间前缀 —— `activate` 时只清理**我们自己**的旧缓存，不碰别人的。 */
const NS = "yj-static-";

const CACHE = NS + VERSION;

/*: 离线兜底页。**必缓存**：它拿不到 ⇒ "不白屏"这个承诺就是假的（见 `precacheInto`）。 */
const OFFLINE_URL = "/offline.html";

/*: ★★ **白名单**（规则 ③ 读的就是它）。两类的共同点是"**内容寻址、不会变**"：
 *    · `/_next/static/` 下的文件名带构建 hash ⇒ 同一个 URL 的字节永远一样；
 *    · `/icons/` 是随版本发布的静态图。
 *    正因为"不会变"，才敢用 cache-first（永不需要过期策略）。
 *    ⚠️ 这个数组**会被不变量 10-c 解析并断言**：必须含 `/_next/static/`，
 *       且**任何一项都不许指向接口路径** —— 改这里等于改契约，门禁会响。 */
const CACHE_PREFIXES = ["/_next/static/", "/icons/"];

/*: 预缓存清单。分两档：`OFFLINE_URL` 是**必需**，其余是**尽力而为**。 */
const PRECACHE_REQUIRED = [OFFLINE_URL];
const PRECACHE_OPTIONAL = ["/icons/icon-192.png", "/icons/icon-512.png", "/manifest.webmanifest"];

/* ---------------------------------------------------------------- 工具 */

function isCacheable(url) {
  return CACHE_PREFIXES.some((p) => url.pathname.startsWith(p));
}

/**
 * 预缓存一项。
 *
 * ★★ **命中重定向视为失败**：`fetch()` 会**静默跟随** 302，于是
 *    "`/offline.html` 被抓成 200" 实际上是**登录页的 HTML**（未登录时被中间件跳走）。
 *    不查 `res.redirected` 的话，离线页里会显示登录表单，而且 **install 是成功的** ——
 *    这正是 2026-10-02 那次方案评审里预判的坑（`docs/28` §1.1）。
 *    ⇒ 判据：**要缓存的东西，必须验它真的是那个东西**，不能只看状态码是 200。
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
         ⇒ 宁可"没有 SW"（应用照常联网用），也不要"有 SW 但离线打开是白屏" ——
           后者会把一个假承诺留在用户设备上，而且**没有地方会自动变红**。 */
      for (const url of PRECACHE_REQUIRED) {
        await precacheOne(cache, url);
      }

      /* 可选项：失败就算了（少一张图标而已，不影响"不白屏"）。 */
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

  // 规则 ①：只处理 GET（POST 等一律不接管）
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // 规则 ②：跨域不接管
  if (url.origin !== self.location.origin) return;

  // 规则 ④：页面导航 —— network-first，**失败才**回离线页
  if (req.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          return await fetch(req);
        } catch {
          const cached = await caches.match(OFFLINE_URL);
          if (cached) return cached;
          // 兜底（正常走不到：`install` 拿不到离线页时整个 SW 都不会激活）
          return new Response("当前离线，且离线页没缓存成功。", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          });
        }
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
