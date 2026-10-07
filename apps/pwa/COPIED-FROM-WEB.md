# PWA 相对 C 端的**复制品清单**（门禁 `不变量 12` 读它）

`apps/pwa` 的 UI 是**从 `apps/web` 复制的** —— 这是用户 2026-10-06 定的方案（不抽共享包：抽包要动 C 端，风险大、收益低）。

**复制带来的新风险**：C 端与 PWA 的同一份 UI 变成两份 ⇒ 在 C 端修了 bug、PWA 侧没修，而且**不报错**（「同一个事实两种写法」那一族）。

⇒ 对策就是**本清单 + 一条门禁**（`tools/local-verify/check-invariants.py`）：

1. 清单**必须覆盖**每一个「两个 app 都有」的文件（漏登记 ⇒ 门禁红）；
2. 标 `same` 的**必须逐字节相同**（下面给了 sha256 前 16 位）；
3. 标 `fork` 的**必须写理由**，而理由是**可判定的** ——
   要指名一个具体依赖或数据差异，并给出一条能验它的命令。

   ★ 反面写法（门禁会直接判红）：`有意简化` / `暂时这样` / `后续再改` / `先复制过来`
     —— **它们能套在任何一个文件上**，等于没有理由（与「以后 / 时机未到」是同一族）。

4. `pwa_only` 是「只在 PWA 存在」的文件：它同样要写理由，且门禁会核对
   **C 端确实没有同名文件**（防止把「其实是复制品」的东西挂到这一类里逃避比对）。

---

## 1. 逐字节相同（22 个）—— C 端一改，这里就红，提醒你同步

| 文件（`apps/pwa/` 与 `apps/web/` 下同路径） | sha256[:16] |
|---|---|
| `.eslintrc.cjs` | `68df91e498d4e31c` |
| `.prettierignore` | `627ce4007bcf276b` |
| `next.config.mjs` | `c7a6b6e0a872972c` |
| `postcss.config.mjs` | `efa65c65e17d51d5` |
| `prettier.config.mjs` | `59d872aa6141cfef` |
| `public/icons/apple-touch-icon.png` | `7c366de5bbd707ce` |
| `public/icons/icon-192.png` | `65b07ccbdcafca5d` |
| `public/icons/icon-512.png` | `32bed92a895a8710` |
| `public/icons/maskable-512.png` | `f42b285d5e39fb56` |
| `public/offline.html` | `d4343b91a918a531` |
| `src/app/(tabs)/me/favorites/page.tsx` | `cc153de9111f97cf` |
| `src/app/(tabs)/me/notes/page.tsx` | `57f09c9e20bc46eb` |
| `src/app/(tabs)/practice/page.tsx` | `7f815474515afc21` |
| `src/app/globals.css` | `0e14476ef0f44d95` |
| `src/app/manifest.ts` | `e74091c86dff4327` |
| `src/app/practice/session/[id]/page.tsx` | `64adc07d16e5cbff` |
| `src/app/practice/session/[id]/report/page.tsx` | `b06745289f262bc8` |
| `src/app/practice/wrong/[qid]/page.tsx` | `3ec3ed85775dcfa7` |
| `src/app/practice/wrong/page.tsx` | `11a7ce446881ddf7` |
| `src/app/sw-register.tsx` | `fed0a8a7e1b49538` |
| `src/lib/types.ts` | `03b3ed0edcea9c08` |
| `tailwind.config.ts` | `862839061fed3b10` |

## 2. 有意分叉（8 个）—— 每个理由都要能验

### `package.json`

- **理由**：包名 / 端口 / 描述不同（3002），且**不消费** `packages/api-core`（那个包是认证链路的唯一实现，本应用没有认证链路）
- **判据（怎么验）**：`grep -c 'api-core' apps/pwa/package.json` → 0
- **是否收敛**：永久

### `public/sw.js`

- **理由**：缓存策略不同：C 端的数据在网络上 ⇒ **不缓存页面 HTML**（缓存只会制造陈旧页面）；本应用的数据本来就在本机 ⇒ 必须缓存**应用壳**（否则离线只剩浏览器错误页）
- **判据（怎么验）**：两个文件的 `fetch` 处理分支不同（本文件是 `cache-first` + 后台更新）
- **是否收敛**：永久

### `src/app/(tabs)/layout.tsx`

- **理由**：C 端有两层登录守卫（`middleware.ts` + `hasToken()`）与 3 个 Tab；本应用没有登录这个状态，只有 2 个 Tab
- **判据（怎么验）**：`apps/pwa/src/middleware.ts` 不存在（test ! -f）
- **是否收敛**：永久

### `src/app/(tabs)/me/page.tsx`

- **理由**：C 端那页是「资料 + 引导 + 退出登录」，**全是账号相关**；本应用没有账号（单机单用户）⇒ 换成本地该有的三样：收藏/标记、笔记、数据备份与题库信息
- **判据（怎么验）**：`grep -c 'auth-store\|auth/me' apps/pwa/src/app/(tabs)/me/page.tsx` → 0
- **是否收敛**：永久

### `src/app/(tabs)/page.tsx`

- **理由**：C 端首页读 `/auth/me` 的 profile、并按 `onboarded_at` 决定是否送去引导；本应用没有账号与引导 ⇒ 首页改问**题库状态**（有没有导入过）
- **判据（怎么验）**：`grep -c 'auth/me' apps/pwa/src/app/(tabs)/page.tsx` → 0
- **是否收敛**：永久

### `src/app/layout.tsx`

- **理由**：站点标题/描述不同（`一建通 · 离线版`），且不再需要与登录态相关的元信息
- **判据（怎么验）**：`grep -c '离线版' apps/pwa/src/app/layout.tsx` → 1
- **是否收敛**：永久

### `src/lib/api.ts`

- **理由**：数据源不同：C 端是 HTTP（`fetch` 到 `/api/v1`）、带 token 与 401 单飞刷新；这里读 **IndexedDB**，且没有任何认证链路
- **判据（怎么验）**：本文件里 `fetch(` 出现 0 次（有的话说明 HTTP 那一半没删干净）
- **是否收敛**：永久

### `tsconfig.json`

- **理由**：没有 `@yijian/api-core` 的路径映射（理由同上），因此也不需要 `allowImportingTsExtensions`
- **判据（怎么验）**：`grep -c 'api-core' apps/pwa/tsconfig.json` → 0
- **是否收敛**：永久

## 3. 只在 PWA 存在（4 个）

| 文件 | 为什么 C 端没有对应物 | 是否收敛 |
|---|---|---|
| `src/app/(tabs)/me/backup/page.tsx` | 数据备份（导出/导入**用户数据**）—— C 端的数据在服务端，没有「备份成一个文件」这件事（它的备份 = 数据库备份） | 永久 |
| `src/app/setup/page.tsx` | 导入题库 + 换题库的二次确认 —— C 端的题库在服务端，没有「导入」这件事 | 永久 |
| `src/lib/db.ts` | IndexedDB 的 schema 与读写助手 —— C 端没有数据层（它在服务端），无处可抄 | 永久 |
| `src/lib/grade.mjs` | 判分的**哑比较**实现。★ 它不是 C 端 `grade()` 的复制品，而是「包已归一」前提下的等价实现 —— 等价性由 `tools/pwa/parity.py` 逐条对拍守住 | 永久 |

---

## 4. C 端有、PWA **刻意没有**的文件（不算分叉，因为是「没复制」）

| C 端文件 | 为什么 PWA 没有 |
|---|---|
| `src/middleware.ts` | 没有登录 ⇒ 没有「未登录跳转」这件事。留着它就是**永不触发的守卫** |
| `src/lib/auth-store.ts` | 同上（它存 token） |
| `src/lib/json-bigint.ts` | 它是为了**雪花 ID 不被 `JSON.parse` 舍入**；本应用没有服务端 ID |
| `src/app/login` `register` `onboarding` | 本版**砍掉**（零后端的代价，用户已确认） |
| `src/app/(tabs)/me/**` | 账号相关的 Tab；设置（导出/导入）在 B-3 加 |
| `src/app/practice/wrong/**` | 属 **B-2**（错题本 + 收藏 + 标记 + 笔记） |

---

## 5. 机器可读部分（门禁读这个代码块，不要手改格式）

```json
{
  "generated_by": "tools/pwa/gen-copied-manifest.py",
  "copied_identical": [
    {
      "to": ".eslintrc.cjs",
      "from": ".eslintrc.cjs",
      "sha256": "68df91e498d4e31c",
      "identical": true
    },
    {
      "to": ".prettierignore",
      "from": ".prettierignore",
      "sha256": "627ce4007bcf276b",
      "identical": true
    },
    {
      "to": "next.config.mjs",
      "from": "next.config.mjs",
      "sha256": "c7a6b6e0a872972c",
      "identical": true
    },
    {
      "to": "postcss.config.mjs",
      "from": "postcss.config.mjs",
      "sha256": "efa65c65e17d51d5",
      "identical": true
    },
    {
      "to": "prettier.config.mjs",
      "from": "prettier.config.mjs",
      "sha256": "59d872aa6141cfef",
      "identical": true
    },
    {
      "to": "public/icons/apple-touch-icon.png",
      "from": "public/icons/apple-touch-icon.png",
      "sha256": "7c366de5bbd707ce",
      "identical": true
    },
    {
      "to": "public/icons/icon-192.png",
      "from": "public/icons/icon-192.png",
      "sha256": "65b07ccbdcafca5d",
      "identical": true
    },
    {
      "to": "public/icons/icon-512.png",
      "from": "public/icons/icon-512.png",
      "sha256": "32bed92a895a8710",
      "identical": true
    },
    {
      "to": "public/icons/maskable-512.png",
      "from": "public/icons/maskable-512.png",
      "sha256": "f42b285d5e39fb56",
      "identical": true
    },
    {
      "to": "public/offline.html",
      "from": "public/offline.html",
      "sha256": "d4343b91a918a531",
      "identical": true
    },
    {
      "to": "src/app/(tabs)/me/favorites/page.tsx",
      "from": "src/app/(tabs)/me/favorites/page.tsx",
      "sha256": "cc153de9111f97cf",
      "identical": true
    },
    {
      "to": "src/app/(tabs)/me/notes/page.tsx",
      "from": "src/app/(tabs)/me/notes/page.tsx",
      "sha256": "57f09c9e20bc46eb",
      "identical": true
    },
    {
      "to": "src/app/(tabs)/practice/page.tsx",
      "from": "src/app/(tabs)/practice/page.tsx",
      "sha256": "7f815474515afc21",
      "identical": true
    },
    {
      "to": "src/app/globals.css",
      "from": "src/app/globals.css",
      "sha256": "0e14476ef0f44d95",
      "identical": true
    },
    {
      "to": "src/app/manifest.ts",
      "from": "src/app/manifest.ts",
      "sha256": "e74091c86dff4327",
      "identical": true
    },
    {
      "to": "src/app/practice/session/[id]/page.tsx",
      "from": "src/app/practice/session/[id]/page.tsx",
      "sha256": "64adc07d16e5cbff",
      "identical": true
    },
    {
      "to": "src/app/practice/session/[id]/report/page.tsx",
      "from": "src/app/practice/session/[id]/report/page.tsx",
      "sha256": "b06745289f262bc8",
      "identical": true
    },
    {
      "to": "src/app/practice/wrong/[qid]/page.tsx",
      "from": "src/app/practice/wrong/[qid]/page.tsx",
      "sha256": "3ec3ed85775dcfa7",
      "identical": true
    },
    {
      "to": "src/app/practice/wrong/page.tsx",
      "from": "src/app/practice/wrong/page.tsx",
      "sha256": "11a7ce446881ddf7",
      "identical": true
    },
    {
      "to": "src/app/sw-register.tsx",
      "from": "src/app/sw-register.tsx",
      "sha256": "fed0a8a7e1b49538",
      "identical": true
    },
    {
      "to": "src/lib/types.ts",
      "from": "src/lib/types.ts",
      "sha256": "03b3ed0edcea9c08",
      "identical": true
    },
    {
      "to": "tailwind.config.ts",
      "from": "tailwind.config.ts",
      "sha256": "862839061fed3b10",
      "identical": true
    }
  ],
  "copied_fork": [
    {
      "to": "package.json",
      "from": "package.json",
      "reason": "包名 / 端口 / 描述不同（3002），且**不消费** `packages/api-core`（那个包是认证链路的唯一实现，本应用没有认证链路）",
      "check": "`grep -c 'api-core' apps/pwa/package.json` → 0",
      "converge": "永久"
    },
    {
      "to": "public/sw.js",
      "from": "public/sw.js",
      "reason": "缓存策略不同：C 端的数据在网络上 ⇒ **不缓存页面 HTML**（缓存只会制造陈旧页面）；本应用的数据本来就在本机 ⇒ 必须缓存**应用壳**（否则离线只剩浏览器错误页）",
      "check": "两个文件的 `fetch` 处理分支不同（本文件是 `cache-first` + 后台更新）",
      "converge": "永久"
    },
    {
      "to": "src/app/(tabs)/layout.tsx",
      "from": "src/app/(tabs)/layout.tsx",
      "reason": "C 端有两层登录守卫（`middleware.ts` + `hasToken()`）与 3 个 Tab；本应用没有登录这个状态，只有 2 个 Tab",
      "check": "`apps/pwa/src/middleware.ts` 不存在（test ! -f）",
      "converge": "永久"
    },
    {
      "to": "src/app/(tabs)/me/page.tsx",
      "from": "src/app/(tabs)/me/page.tsx",
      "reason": "C 端那页是「资料 + 引导 + 退出登录」，**全是账号相关**；本应用没有账号（单机单用户）⇒ 换成本地该有的三样：收藏/标记、笔记、数据备份与题库信息",
      "check": "`grep -c 'auth-store\\|auth/me' apps/pwa/src/app/(tabs)/me/page.tsx` → 0",
      "converge": "永久"
    },
    {
      "to": "src/app/(tabs)/page.tsx",
      "from": "src/app/(tabs)/page.tsx",
      "reason": "C 端首页读 `/auth/me` 的 profile、并按 `onboarded_at` 决定是否送去引导；本应用没有账号与引导 ⇒ 首页改问**题库状态**（有没有导入过）",
      "check": "`grep -c 'auth/me' apps/pwa/src/app/(tabs)/page.tsx` → 0",
      "converge": "永久"
    },
    {
      "to": "src/app/layout.tsx",
      "from": "src/app/layout.tsx",
      "reason": "站点标题/描述不同（`一建通 · 离线版`），且不再需要与登录态相关的元信息",
      "check": "`grep -c '离线版' apps/pwa/src/app/layout.tsx` → 1",
      "converge": "永久"
    },
    {
      "to": "src/lib/api.ts",
      "from": "src/lib/api.ts",
      "reason": "数据源不同：C 端是 HTTP（`fetch` 到 `/api/v1`）、带 token 与 401 单飞刷新；这里读 **IndexedDB**，且没有任何认证链路",
      "check": "本文件里 `fetch(` 出现 0 次（有的话说明 HTTP 那一半没删干净）",
      "converge": "永久"
    },
    {
      "to": "tsconfig.json",
      "from": "tsconfig.json",
      "reason": "没有 `@yijian/api-core` 的路径映射（理由同上），因此也不需要 `allowImportingTsExtensions`",
      "check": "`grep -c 'api-core' apps/pwa/tsconfig.json` → 0",
      "converge": "永久"
    }
  ],
  "pwa_only": [
    {
      "to": "src/app/(tabs)/me/backup/page.tsx",
      "why": "数据备份（导出/导入**用户数据**）—— C 端的数据在服务端，没有「备份成一个文件」这件事（它的备份 = 数据库备份）",
      "converge": "永久"
    },
    {
      "to": "src/app/setup/page.tsx",
      "why": "导入题库 + 换题库的二次确认 —— C 端的题库在服务端，没有「导入」这件事",
      "converge": "永久"
    },
    {
      "to": "src/lib/db.ts",
      "why": "IndexedDB 的 schema 与读写助手 —— C 端没有数据层（它在服务端），无处可抄",
      "converge": "永久"
    },
    {
      "to": "src/lib/grade.mjs",
      "why": "判分的**哑比较**实现。★ 它不是 C 端 `grade()` 的复制品，而是「包已归一」前提下的等价实现 —— 等价性由 `tools/pwa/parity.py` 逐条对拍守住",
      "converge": "永久"
    }
  ]
}
```

