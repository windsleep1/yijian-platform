# 28 · PWA 收尾 · 方案（**待确认 —— 确认后才写代码**）

> **作用**：开工前把**边界**定死。**这份文件本身不是实现。**
> **依据**：`docs/26` §4（**D2 / D3 / D4** 已经写好了三条验收判据）+ 2026-10-02 用户口径。
> **只做 C 端**（`apps/web`）；B 端（`apps/admin`）**不做** PWA。
> ★ **与 BL-21 无关**：BL-21 是"个人 PWA（**功能层**：标记 / 收藏 / 笔记）"，主触发是
> "开始做个人 PWA 时"，**不是**本批。本批是**基建层**（manifest / SW / 可安装）。
> 一个词（"PWA"）指了两层，本文件只管基建层（已同步 `MEMORY` / `docs/21` / `docs/26` §6）。

## 0. 一句话范围

让 C 端**可安装**：manifest + 图标 + 一个**只缓存静态资源**的 Service Worker + 离线兜底页。

| 做 | 不做（**并写清为什么**） |
|---|---|
| manifest（`app/manifest.ts`） | ❌ **缓存 API 响应** —— 那和"离线答题"是两件事 |
| 图标 192 / 512 / maskable / apple-touch | ❌ **离线业务逻辑**（属 BL-21，触发点未到） |
| SW 只接管 `/_next/static/*` 与 `/icons/*` | ❌ `next-pwa` / `@serwist/next`（结论见 §2） |
| 离线兜底页（自包含静态 HTML） | ❌ B 端；❌ 推送 / 后台同步 / Workbox 运行时 |
| `display: standalone`（可安装） | ❌ Lighthouse 打分（它不是一个判据，见 §5 末） |

## 1. 现状（**实测**，不是假设）

| 事实 | 值 | 怎么知道的 |
|---|---|---|
| Next 版本 | **14.2.18**，App Router（`src/app`） | `apps/web/package.json` |
| 现有 PWA 设施 | **完全没有**：无 `public/`、无 manifest、无 SW、无 `next-pwa` | `ls apps/web/public` ⇒ 不存在 |
| 图标资产 | **全仓 0 个**（只有 `apps/admin/docs/screenshots/` 的截图） | `find . -name "*.png"` |
| 品牌色 | `hsl(222 82% 52%)` = **`#205CE9`** | `apps/web/src/app/globals.css:19`（本机算出来的） |
| 本机能否 `next build` | **不能**（删除守卫）⇒ **prod 产物只能由 CI 验** | `tools/local-verify/e2e-web.py` 抬头 |
| 本机 C 端走查 | 必须加 `--e2e-dev`（跑 `next dev`） | 同上 |
| CI 的 C 端门禁 | lint / format:check / format:check:shared / tsc / **build**（**无 E2E**） | `.github/workflows/ci.yml` → `frontend-web` |
| 已有验收判据 | `docs/26` §4 的 **D2 / D3 / D4** | `docs/26-作品集收尾清单.md` |

### 1.1 ★★ 最大的风险点（先说 —— 它决定实现形状）

`apps/web/src/middleware.ts` 的 matcher：

```
"/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"
```

⇒ **`.js` / `.html` / `.webmanifest` 都不在排除名单里**，于是这三个 URL 会**走中间件**：

| 路径 | 未登录时会发生什么 | 后果 |
|---|---|---|
| `/sw.js` | 302 → `/login` | **SW 注册失败**（拿到的是 HTML，`Content-Type` 也不对） |
| `/manifest.webmanifest` | 302 → `/login` | 浏览器**取不到 manifest** ⇒ 装不上 |
| `/offline.html` | 302 → `/login` | 离线兜底页**预缓存到的其实是登录页** |

★ **已登录用户完全看不到这个问题**（有 cookie ⇒ 直接放行）⇒ 它最容易在
"我自己测的时候是好的"里漏掉 —— 而**面试官/陌生访客恰恰是未登录的**。

⇒ 必须把这三个加进 `PUBLIC_PREFIXES`（§3.1）。**这是本方案的核心修复项，不是可选项。**

## 2. `next-pwa` 还是手写？—— **手写**

**结论：手写**（约 80 行 `public/sw.js`），**不引入任何新依赖**。理由（每条都可核）：

1. 我们要的策略只有 **3 条规则**（§3.3）。`next-pwa` 的默认是"**预缓存一切 + 接管所有请求**"，
   要把它调到我们的形状，得写配置去**关掉**它的默认行为 —— 配置量不小于手写。
2. `next-pwa` 最后发布是 **5.6.0 / 2023**，形态是 **webpack 插件**；本项目是 Next 14 + App Router。
   引入它 = 新增一条"构建期注入"的依赖链。按 `docs/21` 的"**不主动加新基础设施**"，**不加**。
3. 它默认会缓存**页面路由与 API 响应**（**正是我们明确不要的**）——
   一个"默认做了我们禁止的事"的工具，只能靠配置关；而**配置漂移不会报错**。
4. 我们要它**能被不依赖打包器地读**：`public/sw.js` 是纯静态文件 ⇒ 不变量 10 可以直接断言
   它的内容（"**不得出现 `/api`**"，§4-c）。插件生成的产物做不到这一点。

★ **代价（如实写）**：没有 Workbox 的版本管理 / 缓存过期策略。本方案的替代是
"**只缓存内容寻址的不可变资源**"（`/_next/static/*` 自带 hash）+ "**只预缓存一个离线页**"
⇒ **不需要过期策略**，缓存也自然有界（增长只来自用户真浏览过的 chunk）。

## 3. 实现清单（逐项）

### 3.1 中间件白名单（**必须最先做**）

`apps/web/src/middleware.ts`：

```ts
const PUBLIC_PREFIXES = [
  "/login",
  "/register",
  "/sw.js",                  // ★ 静态文件，不是页面
  "/manifest.webmanifest",
  "/offline.html",
];
```

⚠️ **连带改动（不写清就会假红）**：不变量 9 会把这三个当成"**公开页面**"，
进而要求"**必须别处有入口**" ⇒ **当场误报**。
⇒ 同时改 `tools/local-verify/check-invariants.py`：**跳过含 `.` 的路径**
（判据：**带扩展名 = 静态文件，不是页面入口**）。
判据：改完 `check-invariants.py` 仍是 **38 项 / 0 失败**（数字不变，属"扩大范围但不新增条目"）。

### 3.2 manifest：`apps/web/src/app/manifest.ts`

用 App Router 的 MetadataRoute（**不手写 `<link>`**）：

```ts
import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "一建通 · 一级建造师学习备考平台",
    short_name: "一建通",
    start_url: "/",
    scope: "/",
    display: "standalone",   // ← 判据"打开无浏览器地址栏"靠它
    background_color: "#ffffff",
    theme_color: "#ffffff",  // ← 见 §3.6 决策点 1
    lang: "zh-CN",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
```

- 放在 `src/app/manifest.ts` ⇒ Next 自动产出 `/manifest.webmanifest` 并**在 `<head>` 注入
  `<link rel="manifest">`**。
- ⚠️ `start_url: "/"` 在未登录时会经中间件跳 `/login` —— **这是预期**（装到桌面后打开看到登录页，不是 bug）。

### 3.3 Service Worker：`apps/web/public/sw.js`（手写）

**三条规则 + 一条默认**（**默认拒绝**，不是黑名单）：

1. **非 `GET`** ⇒ 不接管。
2. **跨域** ⇒ 不接管。（后端 API 在**另一个域**（Render）⇒ 天然不会被缓存；
   将来若改成同域，靠第 3 条继续挡住。）
3. **只有命中白名单前缀才接管**：`/_next/static/`（JS / CSS / 字体，内容寻址）、`/icons/`。
   **其余一律不接管**（含 `/api/**`）。
4. 另有单独一条：`mode === "navigate"`（打开页面）走下面的表。

| 请求 | 策略 |
|---|---|
| `/_next/static/*`、`/icons/*` | **cache-first**（不可变资源：命中即返回；未命中则取网并写入） |
| `mode === "navigate"` | **network-first**；**失败**时才回 `/offline.html` |
| 其它（含 `/api/*`） | **不接管**，交给浏览器 |

- **预缓存**（`install`）：只放 `/offline.html` + 两个图标 + manifest ⇒ 小、快、能离线。
- `activate`：删掉**名字不等于当前 cache** 的旧 cache；`skipWaiting()` + `clients.claim()`。
- ★ **不给 navigation 加超时**：那会把"**慢**"误判成"**离线**"。本项目后端冷启动就要 50s
  （虽然导航打的是 Vercel、不走 Render），但**没有理由引入这个误判面**。
- ★ **不预缓存 React chunk**：那会把"缓存策略"和"构建产物清单"耦合成一张**需要人工维护**的
  列表，而它会漂移。用户浏览过的 chunk 由规则 3 自然进缓存 —— **够用且不漂移**。

### 3.4 离线兜底页：`apps/web/public/offline.html`

**自包含**（内联 CSS、**不引 `/_next/static`** —— 因为它要在"什么都拿不到"时显示）：
一屏"**当前离线**" + 品牌色 + 一个"重试"按钮（`location.reload()`）。
判据（`docs/26` D4）：断网刷新 ⇒ 看到**这个页**，不是浏览器默认错误页。

### 3.5 注册：`apps/web/src/app/sw-register.tsx` + 挂到 `layout.tsx`

```tsx
"use client";
// 省略 useEffect 外壳：核心三行
if (process.env.NODE_ENV !== "production") return;          // ★ dev 不注册
if (!("serviceWorker" in navigator)) return;
navigator.serviceWorker.register("/sw.js").catch(() => {});  // 失败不打扰用户
```

★★ **为什么必须卡 `NODE_ENV === "production"`**：
- 本机 E2E**只能**跑 `next dev`（`--e2e-dev`）⇒ **dev 不注册 ⇒ 现有 E2E 完全不受影响**。
  验收判据"**已有的 E2E 不回归**"由此**结构性**满足，而不是靠"我跑了一遍"。
- dev 下 SW 会缓存旧 chunk，把"改了代码没生效"变成日常噪音。

同时 `layout.tsx` 的 `metadata` 补 **iOS 侧**（Safari **不读 manifest 的 icon**）：
`icons: { apple: "/icons/apple-touch-icon.png" }`、
`appleWebApp: { capable: true, title: "一建通", statusBarStyle: "default" }`。

### 3.6 两个**要你拍板的小决定**

1. **状态栏 / 主题色**：建议 `theme_color = "#ffffff"`（与现有 `viewport.themeColor` 一致，
   **不改变任何现有观感**）。若想要品牌蓝状态栏 ⇒ `#205CE9`，但那**同时**会改掉浏览器地址栏
   的着色（属可见变更，需你点头）。
2. **图标**：本机能生成（Pillow 10.4 + 微软雅黑已就位）。建议 `#205CE9` 底 + 白字 **"建"**；
   `maskable` 版把字缩到中心 **60%**（安全区），`apple-touch-icon` 用 180×180。
   **若你已有 logo，把文件给我，我就只用它做缩放与 maskable 版**（不自己画）。

### 3.7 `next.config.mjs`：给 `/sw.js` 定缓存

加一条 header：`/sw.js` ⇒ `Cache-Control: no-store`。
理由：**不赌平台的默认值**（Vercel 对 `public/` 的默认缓存策略我没有证据）。
Chrome 68+ 默认已绕过 HTTP 缓存取 SW 脚本，但这条让它**不可能**发生"SW 更新不及时"。

## 4. 治根：不变量 10（**PWA 契约**）

`tools/local-verify/check-invariants.py` 新增第 10 组（**静态契约，不依赖浏览器**）：

| # | 断言 | 为什么值得写成门禁 |
|---|---|---|
| a | `src/app/manifest.ts` 含 `display: "standalone"` / `start_url` / `name`，且 `icons` 里**同时有 192 与 512** | 缺 512 时 Chrome **不显示"安装"**，而页面看起来完全正常 |
| b | **读 PNG 的 IHDR 头**：`icon-192.png` 的真实像素必须是 192×192（512 / 180 同理） | "文件名说 192、图其实是 512" —— **只有真机才发现**（本机没浏览器可读数） |
| c | `public/sw.js` **必须**出现 `/_next/static`（正面）；**不得**出现 `/api`（负面） | 这是"**不缓存 API**"的**唯一机械保证** |
| d | `middleware.ts::PUBLIC_PREFIXES` **必须**含 `/sw.js` / `/manifest.webmanifest` / `/offline.html` | §1.1 那个"**登录用户看不出**"的坑，只有这条能挡 |

★ **每条都要有"应该红"的场景真红过**（硬约定 J）—— 收尾时逐条做：改坏 → 跑 → 恢复。

## 5. 验收（**分层写清"哪一层证明了什么"**）

| 层 | 怎么验 | 能证明 / **不能**证明 |
|---|---|---|
| 静态 | `npm run lint` / `format:check` / `tsc --noEmit` | 语法与类型 |
| 门禁 | `check-invariants.py`（含第 10 组）+ 逐条变异 | §4 的 4 条契约；**不能**证明浏览器行为 |
| 本机真验 · manifest | `next dev` → `curl -s localhost:3001/manifest.webmanifest` 断言字段非空 | metadata route **真的产出**了（dev 也能验） |
| 本机真验 · SW 行为（**推荐做**） | 用已有的 CDP 浏览器：手动 `register('/sw.js')` → reload → 断言有 controller → **CDP 断网** → 导航 → 断言页面含"当前离线" | **"离线不白屏"是真的**；否则这条只能靠真机 |
| CI | `web · npm run build` 必过 | 新文件进得了构建（**本机跑不了 build**） |
| 不回归 | `run-local-pipeline.py --e2e-web --e2e-dev --e2e-scenario p2a`（+ 其余场景） | dev 路径不受影响（由 §3.5 **结构保证**） |
| 线上 | Vercel 重新部署 → DevTools → Application：Manifest 可读 / SW = activated / **Installable** | 真正的目标环境 |
| **真机（你做）** | 见 §7 | **唯一**能确认"添加到主屏 + standalone"的层 |

★ **明确不做**：Lighthouse 打分 —— 它会因无关项波动，**不是一个判据**。

## 6. 交付物清单（**确认后才动手**）

**新增**：
- `apps/web/src/app/manifest.ts`、`apps/web/src/app/sw-register.tsx`
- `apps/web/public/sw.js`、`apps/web/public/offline.html`
- `apps/web/public/icons/{icon-192,icon-512,maskable-512,apple-touch-icon}.png`
- `tools/local-verify/gen-pwa-icons.py`（生成图标的脚本，**与 PNG 一起提交** ⇒ 换 logo 时可重跑）

**修改**：`apps/web/src/middleware.ts`、`apps/web/src/app/layout.tsx`、`apps/web/next.config.mjs`、
`tools/local-verify/check-invariants.py`、`docs/26`（§0 / §4 状态）、本文件（转成"已实现"）、当日日志

**不动**：`apps/admin/**`（B 端不做）、`apps/api/**`（**零后端改动**）、离线业务逻辑（BL-21）

## 7. 真机步骤（**你来做**）

### Android（Chrome）
1. 打开 `https://yijian-platform-tau.vercel.app/`（**要能正常加载** ⇒ 可能需要代理）
2. 右上角 ⋮ → **添加到主屏幕** → 确认
3. 从桌面图标打开 ⇒ **没有地址栏**（standalone）
4. **开飞行模式**再打开 ⇒ 应出现"**当前离线**"页（不是恐龙头 / 白屏）

### iOS（Safari）
1. 打开同一网址 → 底部**分享** → **添加到主屏幕**
2. 从桌面打开 ⇒ 无 Safari 地址栏（靠 `appleWebApp.capable`）
3. 飞行模式下打开 ⇒ "当前离线"页

★ 判据 = `docs/26` D3 / **D4** 逐条打勾；**不要求**离线能答题。
★ 失败时把**现象**告诉我（哪一步、什么画面、截图）—— 我来对代码。

## 8. 风险与回滚

| 风险 | 应对 |
|---|---|
| SW 有 bug ⇒ 用户被卡在旧资源上 | **回滚 = 换一个"自杀"版 `sw.js`**（`unregister()` + 清 cache）。⚠️ **只删掉注册代码不会移除已安装的 SW** |
| 图标尺寸/格式不被接受 | 不变量 10-b（读 IHDR 真字节）+ §7 真机 |
| manifest 被中间件挡住 | 不变量 10-d |
| 现有 E2E 受影响 | §3.5 的结构隔离 + §5 跑一遍 |

---

## 9. 要你回的三句话（**回了我就开工**）

1. **主题色**：`#ffffff`（建议，零观感变化）还是 `#205CE9`（品牌蓝状态栏）？
2. **图标**：用我生成的"`#205CE9` 底 + 白字 **建**"，还是你给我 logo？
3. **§5 那条"本机 CDP 验离线不白屏"**（推荐做，能把最关键的判据从"真机才知道"变成"本机测过"）—— 做不做？
