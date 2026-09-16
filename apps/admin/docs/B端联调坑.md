# B 端前后端联调常见坑

> 本批（Batch 3 管理后台骨架 + Batch 4 题库 CRUD）实际踩到或明确识别出的坑。
> 每条都按 **现象 → 根因 → 解法 → 在本项目里落在哪个文件** 写，方便以后照方抓药。
>
> 排序大致按"杀伤力"：前 5 条是会让人查半天的，中间是写一次就能避开的，
> 第 15 条是取舍留痕，第 16 条是验收时实测出来并已修掉的。
> 第 20~22 条是 Batch 4 验收时新踩的：20 是"权限漏配导致主用户进不来"，
> 21 是对第 1 条的补完（**请求方向**同样会丢精度），22 是"假成功"类问题。

---

## 1. 雪花 ID 精度丢失 —— 最隐蔽的一个（已实测）

**现象**

用户在列表里点「分配角色」，接口却返回 `40401 用户不存在`。把 id 从响应里复制出来对比，
发现尾数变了：

```
后端返回      375228939615866880
前端 JSON.parse 后  375228939615866900     ← 已经不是同一个数了
```

更麻烦的是它**静默**发生——不报错、不抛异常，只是数字悄悄变了。

**根因**

JS 的 `Number` 是 IEEE-754 双精度，能精确表示的整数上限是 `2^53 - 1 = 9007199254740991`（16 位）。
雪花 ID 是 18~19 位，**必然丢精度**。实测（Node 22）：

```
MAX_SAFE_INTEGER = 9007199254740991
raw id      = 375228939615866880
parsed id   = 375228939615866900
precision lost = true
```

**解法（两层，缺一不可）**

1. **根治：后端把 ID 序列化成字符串。**
   `apps/api/app/schemas/types.py` 定义了 `BigIntStr = Annotated[int, PlainSerializer(str, when_used="json")]`，
   所有出参的 ID 字段都套上它。`when_used="json"` 意味着只影响 JSON 输出，
   `model_dump()`（python 模式）仍是 int，内部代码不会拿到字符串当 int 用。
   Swagger 里也直接显示 `type: string`，契约是自解释的。

2. **防御垫（非主路径）：前端提前给裸数字加引号。**
   `apps/admin/src/lib/json-bigint.ts` 的 `parseJsonSafe()` 在 `JSON.parse` **之前**用正则
   把值位置的 16 位以上裸数字补上引号。

**⚠️ 这里有个必须讲清的点：不能在 `parse` 之后再转换。**

精度丢失发生在 `JSON.parse` 内部。事后再遍历对象把 number 转字符串毫无意义：

```js
JSON.parse('{"id":375228939615866880}')  // → { id: 375228939615866900 }
String(parsed.id)                        // → "375228939615866900"   ← 已经错了，救不回来
```

所以**绝对不要**写 `number → BigInt → string` 的"还原逻辑"：
`BigInt(375228939615866900)` 得到的仍是错的数字，而且会给调用方"已修好"的错觉，比不做更危险。
`json-bigint.ts` 的注释里写明了这一点。

**顺带一个体检手段**：`apps/admin/src/lib/api.ts` 的 `assertNoUnsafeIds()` 会在 dev 模式下
遍历响应，发现超出安全整数范围的 number 就 `console.warn`。哪天有人新加接口忘了套 `BigIntStr`，
控制台会立刻报出来，而不是等上线后 404。

**落点**：`apps/api/app/schemas/types.py`、`apps/admin/src/lib/json-bigint.ts`、`apps/admin/src/lib/api.ts`

---

## 2. Refresh Token Rotation + 并发刷新互相作废

**现象**

页面打开时并发发了 3 个请求，全 401。前端逐个去 refresh，结果 2 个请求莫名失败，
用户被踢回登录页。刷新一次就好，所以很难复现。

**根因**

后端是 **Rotation**：`POST /auth/refresh` 成功后会**作废旧 refresh_token**。
三个请求各自拿着同一个旧 refresh_token 去刷：
第一个成功拿到新的，第二、三个带着已作废的旧 token → `40105` → 判定"刷新失败" → 跳登录。

**解法**

**单飞（single-flight）刷新**：用模块级 Promise 把并发收敛成一次。

```ts
let inflightRefresh: Promise<boolean> | null = null;

async function refreshToken(): Promise<boolean> {
  inflightRefresh ??= (async () => {
    try { /* ... */ } finally { inflightRefresh = null; }   // finally 里清，否则一次抖动就永久锁死
  })();
  return inflightRefresh;
}
```

同时在 `finally` 里清空（而不是成功后才清），否则一次网络抖动会让 refresh 永远不再发起。

还有两条容易漏的：
- **必须把新的 `refresh_token` 一起存回去** —— Rotation 就是"旧的换新的"，
  只存 access_token 的话下一次刷新必然失败。
- **要有 `_retried` 标记**，避免"401 → 刷新 → 还是 401 → 再刷新"的死循环。

**落点**：`apps/admin/src/lib/api.ts`

---

## 3. `disabled` 的按钮不出 tooltip ★

**现象**

给「分配角色」按钮套上 Tooltip，按钮变灰了，但**悬停没有任何反应**。
很容易怀疑到 radix 版本或者 Tooltip 配置上去。

**根因**

`disabled` 的表单元素**不派发鼠标事件**（`pointerenter` / `mouseenter` 都不发）。
shadcn 的 Button 还额外带了 `disabled:pointer-events-none`，连事件命中都做不到。
`TooltipTrigger` 挂在按钮上，于是永远等不到 hover。

**解法**

在按钮**外面套一层 `<span>`**，让 span 承接鼠标事件，按钮只负责"灰着"：

```tsx
<TooltipTrigger asChild>
  <span className="inline-block cursor-not-allowed" aria-disabled="true">
    {cloneElement(children, { disabled: true })}
  </span>
</TooltipTrigger>
```

**而且 tooltip 文案必须可执行**：不能只写"无权限"，要写清缺哪个权限、找谁开通：

> 你没有执行此操作的权限
> 需要「user:manage」，请联系系统管理员为你的账号开通

否则用户只会来问开发。

**落点**：`apps/admin/src/components/PermissionGate.tsx`

---

## 4. 脱敏做在前端 = 没做

**现象**

用户列表里手机号显示成 `138****8888`，看起来挺好。随手 F12 看响应体：
**明文手机号就在里面**。前端只是"没显示",不是"没泄露"。

**根因**

把"展示"当成了"访问控制"。任何能打开浏览器的人都能看到原始响应；
抓包、日志回放、前端埋点上报，都会把明文带出去。

**解法**

**脱敏必须在服务端完成，并且按权限决定下发什么。** 本项目的做法：

| 字段 | 含义 | 下发条件 |
|---|---|---|
| `phone` | 永远是脱敏值（`138****8888`） | 所有有 `user:read` 的调用者 |
| `phone_full` | 明文 | **仅当调用者拥有 `user:export`**，否则为 `null` |

- 列表接口**只返回 `phone`**（连 `phone_full` 这个字段都没有）。
- 详情接口两个都返回，但 `phone_full` 按权限决定是否为 `null`。
- 前端 `apps/admin/src/lib/format.ts` **刻意不提供 `maskPhone()`** ——
  有这个函数存在，就会诱导后人用它去"修"泄露问题。函数不存在，就只能去后端改。

**落点**：`apps/api/app/services/user_service.py`（`mask_phone` / `get_user_detail`）、
`apps/admin/src/lib/format.ts` 的注释、`apps/admin/src/app/(console)/users/[id]/page.tsx`

---

## 5. 时区：审计时间差 8 小时

**现象**

审计日志里"刚刚"发生的操作，显示时间是 8 小时前（或后）。
本地开发看不出来（机器时区就是 +08），换一台 UTC 的 CI 机器或者海外同事看就错位。

**根因**

后端存 `TIMESTAMPTZ`、返回 UTC 的 ISO 串（`2026-09-15T07:05:12Z`）。
前端 `new Date(iso).toLocaleString()` 会按**浏览器本地时区**渲染，
于是同一份数据在不同机器上显示不同时间。

**解法**

1. 展示统一走 `Intl.DateTimeFormat` 并**显式指定 `timeZone: "Asia/Shanghai"`**：

```ts
const dateTimeFmt = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
```

2. **时间范围筛选还有第二层坑**：`<input type="datetime-local">` 给的是**无时区的本地时间串**
   （`2026-09-15T10:00`）。`new Date("2026-09-15T10:00")` 按浏览器本地时区解释，
   但我们想要的是"用户输入的北京时间对应的时间点"。所以要**显式补 `+08:00`** 再转 UTC：

```ts
const withTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(local) ? local : `${local}:00+08:00`;
return new Date(withTz).toISOString();
```

少这一步，筛选范围会整体偏移 8 小时，表现是"我筛今天，却查出昨天的记录"。

3. 时间范围统一用**半开区间 `[start, end)`** 传递给后端，
   避免"同一天的 end"这种边界歧义（到底含不含 23:59:59？）。

**落点**：`apps/admin/src/lib/format.ts`（`formatDateTime` / `localInputToIsoUtc`）、
`apps/api/app/services/audit_service.py`（`_conditions` 里 `created_at < end`）

---

## 6. `INET` 字段直接进 Pydantic 会 500

**现象**

给审计日志加上 `ip` 字段后，接口突然 500，日志里是 Pydantic 的序列化错误。

**根因**

`audit_logs.ip` 是 PostgreSQL 的 `INET` 类型。asyncpg 读出来**不是一个字符串**，
而是 `ipaddress.IPv4Address` / `IPv6Address` **对象**。
遇到 `ip: str | None` 的声明，Pydantic 校验阶段就会失败。

**解法**

在 service 层统一转字符串，**不要指望自动转换**：

```python
ip=str(row.ip) if row.ip is not None else None,
```

写入方向同理：`app/core/idgen.py` 的 `to_inet()` 把字符串转成 `ipaddress` 对象，
非法值返回 `None`（而不是抛异常），避免因为一个奇怪的 `X-Forwarded-For` 把主流程打挂。

**落点**：`apps/api/app/services/audit_service.py::_to_item`、`apps/api/app/core/idgen.py::to_inet`

---

## 7. HTTP 状态码 ≠ 业务码

**现象**

前端按 `res.status === 400` 分支处理参数错误，结果漏了 `422`；
另一个地方按 `code === 400` 判断，永远不成立。

**根因**

有两套编号：HTTP 状态码（传输层）和业务码（信封里的 `code`）。
两套混用必然漏分支。

**解法**

**只用 `code`。** 本项目后端的约定是：

- 任何响应都是 `{code, message, data, trace_id, server_time}`；
- 参数校验失败 → HTTP **422** + `code: 40001`；
- 业务异常 → HTTP 400/401/403/404 + 对应的 `40001/401xx/403xx/404xx`；
- 前端只判 `code`，`httpStatus` 仅用于提示语气（比如 403 用"没有操作权限"）。

**落点**：`apps/api/app/core/errors.py`（`register_exception_handlers`）、
`apps/admin/src/lib/api.ts`（只按 `body.code` 分流）

---

## 8. 依赖服务不可用（503）被当成"登录过期"

**现象**

Redis 抖了一下，前端所有用户被踢回登录页。

**根因**

后端在依赖不可用时返回 `code: 50003`（HTTP 503）。
前端如果简单地"看到非 0 就尝试 refresh / 跳登录"，就会把一次服务端抖动放大成全员登出。

**解法**

维护一个**明确的白名单**，只有真正的认证类错误才进刷新重试链路：

```ts
const RETRYABLE_AUTH_CODES = new Set([40100, 40101, 40104, 40105]);
```

`50003` 不在里面，于是它走统一的错误 toast（"依赖服务暂时不可用，请稍后重试"），
用户留在原地，服务恢复后点一下重试即可。

**落点**：`apps/admin/src/lib/api.ts`

---

## 9. 哪些 401 不该重试

**现象**

用一个被篡改/伪造的 token，前端陷入"刷新 → 401 → 刷新"的循环，控制台刷屏。

**根因**

把"所有 401"都当成"access_token 过期"。

**解法**

区分错误码：

| code | 含义 | 该不该刷新重试 | 最终归宿 |
|---|---|---|---|
| `40100` | 未携带/缺少 token | ✅ | 刷新失败 → 跳登录 |
| `40101` | token 过期 | ✅ | 刷新成功 → 留在原地；失败 → 跳登录 |
| `40104` | 账号不存在或已注销 | ✅（刷新也没用，但刷新失败会跳登录，链路一致） | 跳登录 |
| `40105` | refresh_token 已失效 | ✅ | 跳登录 |
| `40102` | **签名无效**（被篡改） | ❌ 重试一万次也一样 | **跳登录**（见坑 16） |
| `40103` | 密码错误（登录接口自身） | ❌ 它不是"会话"问题 | 留在登录页提示 |
| `40305` | 账号被禁用/锁定 | ❌ 刷新救不回来，直接提示 | 留在原地提示 |

另外必须配合 `_retried` 标记，保证整条链路最多刷新一次。

**注意最后两列是分开的**：`40102` 是"不重试、但必须跳登录"。
把这两件事混成一个判断，就会出现坑 16 里那个点不动的「重试」按钮。

**落点**：`apps/admin/src/lib/api.ts`

---

## 10. `trace_id` 有两个来源

**现象**

业务错误（比如 403）的提示里能带出 trace_id，但网关层错误（nginx 502、超时返回 HTML）没有，
用户报障时给不出任何可用于排障的标识。

**根因**

trace_id 有两个地方：**响应信封里的 `trace_id`**（业务错误一定有）
和**响应头 `X-Request-ID`**（框架/网关层）。

**解法**

```ts
const headerTrace = res.headers.get("X-Request-ID") ?? "";
// ...
throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);
```

并且后端 CORS 必须 `expose_headers=["X-Request-ID"]`，否则跨域时前端**读不到这个响应头**
（浏览器默认只暴露少数几个安全头）。本项目已在 `app/main.py` 里配上。

**顺带**：还要处理"响应体根本不是 JSON"的情况（204 空响应、网关 502 返回 HTML）。
不能无脑 `res.json()`，要先 `res.text()` 再 try-parse：

```ts
const text = await res.text();
let body = null;
if (text) { try { body = parseJsonSafe(text); } catch { body = null; } }
if (!body) throw new ApiError(50001, `请求失败（HTTP ${res.status}）...`, headerTrace, res.status);
```

**落点**：`apps/admin/src/lib/api.ts`、`apps/api/app/main.py`、`apps/api/app/core/response.py`

---

## 11. 表格状态放 `useState` → 刷新就回第一页

**现象**

用户筛了 5 分钟、翻到第 7 页，手一抖 F5，全部回到初始状态。想把"我筛出来的结果"发给同事，
只能截图。

**根因**

分页/筛选状态存在组件内存里。

**解法**

**状态进 URL query。** 一次投入换来三件事：刷新不丢、可分享链接、浏览器后退符合直觉。

本项目抽成了 `useTableState`，其中有两个必须注意的细节：

- **改了筛选条件要自动把页码重置为 1。** 否则还停在第 7 页，而在新条件下第 7 页根本不存在，
  结果是空白页 —— 用户会以为"筛出来没数据"。
- `defaults` 必须是**模块级常量对象**（引用稳定）。它进 `useMemo` 依赖，
  在渲染里现写字面量会导致每次渲染重建 state，把请求打成死循环。

**落点**：`apps/admin/src/hooks/useTableState.ts`

---

## 12. 空态没区分"没数据"和"筛出来没结果"

**现象**

用户加了三个筛选条件，看到"暂无数据"，于是反复点刷新、反复改条件，最后来问"是不是坏了"。

**根因**

两种"空"用了同一句文案，而且没有出口。

**解法**

分开处理，并且**筛选态的空要提供"清空筛选"按钮**：

| 场景 | 文案 | 出口 |
|---|---|---|
| 从来没有数据 | "还没有任何用户" + 说明数据从哪来 | — |
| 筛选/搜索后为空 | "没有符合条件的用户" | 「清空筛选条件」按钮 |

另外**分页条要一直显示**，并且写清"共 N 条 / 第 X / Y 页"——
B 端用户需要明确知道自己筛出来了几条。

**落点**：`apps/admin/src/components/EmptyState.tsx`、`apps/admin/src/components/DataTable.tsx`

---

## 13. N+1 查询：角色列表慢

**现象**

角色列表接口随着角色/权限变多越来越慢，日志里一次请求打了 7 条 SQL。

**根因**

"先查角色列表，再循环逐个查它的权限" —— 6 个角色就是 1 + 6 = 7 次往返。

**解法**

**一次 `LEFT JOIN` 批量取全量**，然后在应用层把行合并成对象：

```sql
SELECT r.id, r.code, r.name, ..., p.code AS perm_code
FROM roles r
LEFT JOIN role_permissions rp ON rp.role_id = r.id
LEFT JOIN permissions p ON p.id = rp.permission_id
ORDER BY r.sort_no, p.sort_no, p.id
```

合并时用 `dict[id]` 去重，遇到同一角色只创建一次对象、后续只 append 权限码。
`LEFT JOIN`（而不是 `INNER JOIN`）保证"没有任何权限的角色"也不会被漏掉
（`student` 就是这种）。

**落点**：`apps/api/app/services/rbac_service.py::list_roles_with_permissions`

---

## 14. 改完数据，列表还是旧的（缓存没失效）

**现象**

分配完角色，回到用户列表，角色标签还是老的。用户以为没保存成功，再点一次，结果报 40003。

**根因**

TanStack Query 有缓存，且不知道"这次变更影响了哪些数据"。

**解法**

`onSuccess` 里**把所有受影响的 query key 都 invalidate 掉**。本项目一次角色变更影响三处：

```ts
onSuccess: () => {
  void qc.invalidateQueries({ queryKey: ["user", userId] });   // 详情页的角色块
  void qc.invalidateQueries({ queryKey: ["users"] });          // 列表里的角色标签
  void qc.invalidateQueries({ queryKey: ["audit-logs"] });     // 这次操作刚写入一条审计
},
```

**别指望"返回列表时自动刷新"** —— 那依赖 `refetchOnMount` 和 `staleTime`，
一旦 `staleTime` 设长了就不会重取。显式 invalidate 才可靠。

**另外**：后端的权限缓存（Redis `rbac:priv:{uid}`，TTL 300s）在改角色时也要清，
否则被改的人要等 5 分钟才生效。本项目 `assign_roles` 里已经 `invalidate_privileges`。

**落点**：`apps/admin/src/hooks/useUsers.ts`、`apps/api/app/services/rbac_service.py::assign_roles`

---

## 15. ⚠️ token 存 `localStorage` —— 已知反模式（留痕，本批不修）

**这条不是"踩到的坑"，是"故意留下的债"。写在这里是为了将来能一次收干净。**

**现状**

`apps/admin/src/lib/auth-store.ts` 把 `access_token` / `refresh_token` 存在 `localStorage`，
通过 `Authorization: Bearer` 头发送。

**为什么练手阶段这么做**

省事，而且省掉的都是"必须后端配合"的事：

- 不需要 `Set-Cookie` 那一套（`Domain` / `Path` / `SameSite` / `Secure`）；
- 不需要在 CORS 里把 `allow_origins` 从 `*` 收紧（当前 `allow_credentials=True` + `allow_origins=["*"]`
  这个组合**用 cookie 时会被浏览器直接拦掉**，见下）；
- 不需要实现 CSRF 防护；
- 前端调试直观，F12 就能看到 token。

**为什么生产必须换 `httpOnly` + `Secure` + `SameSite=Lax` Cookie + CSRF Token**

1. **XSS 的收益天差地别。**
   `localStorage` 能被 JS 读到，所以任何一处 XSS（第三方脚本、富文本渲染、
   一个不小心的 `dangerouslySetInnerHTML`）都能直接
   `localStorage.getItem("yj_refresh_token")` **把长期凭据拿走**，
   攻击者可以持续 refresh，即使用户关掉页面也还在。
   `httpOnly` cookie 读不到，XSS 最多"借当前会话发请求"，而且会随用户关站而失效。
   **refresh_token 是长期凭据，它的存放位置决定了 XSS 是"局部事件"还是"账号沦陷"。**

2. **没有服务端侧失效入口。**
   `localStorage` 是纯前端的，服务端管不着。用户点"退出登录"只是本地删掉，
   **被窃取的 token 在有效期内依然能用**。
   换 Cookie 后可以配合服务端会话表：改密码 / 踢线 / 检测到异常登录时
   `DELETE FROM user_sessions WHERE ...`，立刻生效。

**换过去的代价（也就是本批不做的原因）**

- CORS 必须显式列 origin：`allow_credentials=True` 时**不允许** `allow_origins=["*"]`，
  浏览器会拒绝。要改成配置化的白名单（本项目 `main.py` 里已经预留了
  `CORS_ORIGINS` 环境变量分支，生产走的是白名单模式）。
- 必须实现 CSRF 防护：推荐"双提交 Cookie"（一个可读的 csrf cookie + 一个同名请求头，
  服务端比对），或者依赖 `SameSite=Lax` + 只允许 `POST/PUT/DELETE` 走自定义头。
- 前端 `fetch` 要加 `credentials: "include"`。
- 本地开发要处理 `localhost` 不同端口算跨域的问题（用 devServer 代理可以绕开）。

**迁移清单**（将来做的时候照着改）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `apps/api/app/main.py` | CORS 白名单化（去掉 `["*"]`），保留 `allow_credentials=True` |
| 2 | `apps/api/app/api/v1/auth.py` | 登录/刷新改为 `Set-Cookie`（httpOnly+Secure+SameSite=Lax），响应体不再返回 token |
| 3 | `apps/api/app/core/security.py` | 增加 CSRF token 生成与校验依赖 |
| 4 | `apps/api/app/api/v1/auth.py` | 登出时清 cookie + 删除对应 `user_sessions` 行（真正的服务端失效） |
| 5 | `apps/admin/src/lib/api.ts` | `credentials: "include"`，去掉 `Authorization` 头，加 `X-CSRF-Token` |
| 6 | `apps/admin/src/lib/auth-store.ts` | 只保留"是否已登录"的提示位（`yj_authed`），删掉 token 存取 |
| 7 | `apps/admin/src/middleware.ts` | 可以直接读真正的会话 cookie 判定（仍需以 `/auth/me` 为准） |

**落点**：`apps/admin/src/lib/auth-store.ts` 顶部注释、`apps/admin/README.md` §6 限制表第 1 条

---

## 16. "不重试" ≠ "不跳登录"：坏 token 把用户关在假死页面里 ★（已实测修复）

**现象**

把 `localStorage` 里的 `yj_access_token` 改成一串垃圾（模拟 token 被篡改 / 换过
`JWT_SECRET`），刷新页面。预期是"跳登录页"，实际得到的是一个错误页：

```
[重试]                 ← 按钮
trace_id: ecf0256e...  ← 可复制
```

点「重试」——还是同一页。**点一百次都一样。** 用户此刻的正确出路只有重新登录，
但页面上没有任何入口能到登录页（连"退出登录"都在控制台布局里，而布局根本没渲染出来）。

**根因**

后端对"签名不合法"的 token 返回 `40102`（`apps/api/app/core/security.py`）。
前端 `api.ts` 里 `40102` 被**刻意**排除在刷新白名单外 —— 这一步是对的，
篡改过的 token 刷一万次也没用。

错在**后半步**：代码只判断了"要不要刷新"，没判断"要不要跳登录"。
`40102` 既不进刷新分支，就落到了最后那句"其余业务错误：交给上层统一 toast"，
于是它被当成普通业务错误（比如 40003）处理了 —— 而普通业务错误是**不该跳登录**的。

一句话：**"值不值得重试"和"要不要重新登录"是两个正交的判断，被合并成了一个。**

```
不进刷新链路  →  ≠  →  应该留在原地让用户重试
```

**解法**

把两个判断拆成两个集合：

```ts
/** 值得"刷新一次再重试" */
const RETRYABLE_AUTH_CODES = new Set([40100, 40101, 40104, 40105]);

/** 刷新也救不回来 —— 凭证本身就不合法，必须清凭证跳登录 */
const FATAL_AUTH_CODES = new Set([40102]);
```

并在刷新分支**之前**处理：

```ts
if (FATAL_AUTH_CODES.has(body.code)) {
  redirectToLogin();          // 内部已 clearTokens()（含清掉 yj_authed 提示位 cookie）
  throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);
}
```

`redirectToLogin()` 里那句 `if (pathname.startsWith("/login")) return;` 依然必要 ——
否则登录页自己收到 401 时会来回横跳。

**怎么在本地验证（不用等 120 分钟）**

access_token 的 TTL 是 120 分钟，靠"等它过期"验证刷新链路不现实。
`tools/local-verify/probe-silent-refresh.py` 用 dev 密钥**自己签一个已过期但签名合法**的
access_token（`exp` 在过去 → 后端返回 `40101`），塞进浏览器 localStorage 再导航，
然后断言两件事：**没有跳到登录页**、**access_token 变了**（证明 Rotation 的新 pair 已落盘）。

脚本里带一步自检：先直接拿这个 token 打一次 `/auth/me`，如果返回的不是 `40101`
而是 `40102`，说明脚本里的 `JWT_SECRET` 和运行中的 API 不一致，
**会在碰浏览器之前就退出并说明原因** —— 否则你会把一个"签名不对"的假失败
当成"刷新链路坏了"，白查半天。

> 这个坑就是这么被发现的：探针第一次跑报 `40102`，暴露了 `serve-local.ps1`
> 设的 `JWT_SECRET` 与 `config.py` 的默认值不同。

**落点**：`apps/admin/src/lib/api.ts`（`FATAL_AUTH_CODES` + 分支）、
`tools/local-verify/probe-silent-refresh.py`

---

## 17. 上一轮遗留的 API 进程占着端口 → 整套验收"假绿" ★（已实测，已加预检）

**症状**：`run-smoke.ps1` 报「API 已就绪」，pytest 里 Batch 2/3 全过，
但所有新接口一律 `40401 Not Found`（Starlette 默认 404）。

**根因**：上一轮会话留了一个 uvicorn 还占着 `8123`。新 API 启动时**端口已占用 → 绑定失败并退出**，
而 `run-smoke.ps1` 的健康检查只验「`GET /health` 有没有 200」——
它打到了**那个旧进程**上，愉快地通过了。于是整套验收静默地测在**旧代码**上。

> 这是最坏的一类失败：**不是红，是绿**。
> 如果旧进程恰好也有全部接口，这批验收会"全部通过"，而你测的是上一版二进制。

**排查一句话**：

```powershell
netstat -ano | findstr :8123        # 看有没有 LISTENING（TIME_WAIT 不算）
# 有 → Get-Process -Id <pid> 确认是不是上一轮的 python，然后 Stop-Process -Id <pid> -Force
```

**根治**（已落到 `run-smoke.ps1` 的 4.6 步）：起 API **之前**先查端口，
被占就直接失败并打印占用它的 pid，把"假绿"转成"大声报错"：

```powershell
$occupied = @(Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction Stop)
if ($occupied.Count -gt 0) {
    $pids = ($occupied | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    Fail "端口 $ApiPort 已被占用（pid: $pids）。……本次验收会静默测到旧代码上。"
}
```

**推广**：任何"起服务 → 打健康检查 → 跑用例"的验收脚本都有这个盲点。
健康检查回答的是"端口上有人应答吗"，**不是**"应答的是我刚起的那个进程吗"。
判定标准要选能区分这两者的——端口预检是最省事的一种。

**落点**：`tools/local-verify/run-smoke.ps1`（4.6 端口占用预检）

---

## 18. `compileall` 过、OpenAPI 也生成得出来，`NameError` 仍然潜伏到运行时 ★（已实测）

**症状**：`DELETE /admin/questions/{id}` 返回 `50001 服务内部错误`，
日志里是 `NameError: name 'QuestionDeleteOut' is not defined. Did you mean: 'QuestionBatchDeleteOut'?`

**根因**：`question_service.soft_delete_question` 里写了 `return QuestionDeleteOut(...)`，
但**忘了把这个名字加进 import 列表**（Edit 工具那次静默失败了，见坑 19）。

**为什么三道关全过**：

| 关卡 | 为什么不报错 |
|---|---|
| `python -m compileall` | 只看**语法**，不做名字解析。`QuestionDeleteOut(...)` 语法完全合法 |
| `from app.main import app` | 该文件有 `from __future__ import annotations`，**注解变成字符串不求值**；而且名字只出现在**函数体内**，import 期不执行 |
| OpenAPI 生成 | 只看路由签名与 `response_model`，不执行处理函数 |

直到真的走到那一行才炸。**没被测试覆盖的分支会一直潜伏**。

**根治**：加一条静态守卫（`tests/test_admin_v4.py::test_new_modules_have_no_undefined_globals`）——
用 `dis` 扫模块内**由本文件编译出来**的函数代码对象，凡是 `LOAD_GLOBAL` 的名字
既不在模块命名空间也不在内建里，就判为"引用了没导入"。

```python
for ins in dis.get_instructions(code):
    if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME") and ins.argval not in available:
        missing.add(ins.argval)
```

**两个必须注意的细节**（都实测踩过）：

1. **必须排除生成代码**。`@dataclass` 的 `__init__`/`__repr__` 是 `exec` 出来的，
   `co_filename == "<string>"`，它们的全局名来自 `dataclasses` 模块 ——
   拿本模块的命名空间去判会凭空报出一堆"未定义"（本会话就误报了 `get_ident`）。
   判据：`os.path.basename(code.co_filename) != os.path.basename(mod.__file__)` 就跳过。
2. 别把它写成"通用 linter"。它的定位是**兜住"跑不到就发现不了"的那类名字错**，
   不做类型检查、不做未使用变量告警。

**推广**：`compileall` + 能起服务 + OpenAPI 能生成，**不等于代码是活的**。
三者之和仍然只是一个"语法/签名"级检查。能执行到路径的只有测试——
所以"新增分支要配一条测试"不是洁癖，是唯一能发现这类错的手段；
在测试覆盖不到的角落，静态扫描是廉价的补丁。

**落点**：`apps/api/tests/test_admin_v4.py`、`apps/api/app/services/question_service.py`

---

## 19. Edit 工具偶发静默失败 —— 一次漏掉 import，一次漏掉 `__all__`

本会话再次复现（Batch 2 记为 4 次，Batch 4 又中 2 次）：

- `question_service.py` 的 **import 列表**加了 `QuestionDeleteOut` → **没生效**（返回 Successfully）
- `admin_question.py` 的 **`__all__`** 加了 `"QuestionDeleteOut"` → **没生效**

但同一批 Edit 里，**函数体的改动都生效了**。结果就是坑 18 那个
"定义有、导出没有、导入没有"的诡异中间态。

**唯一可靠的应对**：**改完关键文件立刻回读**（`Grep`/`Read` 目标行），
不要相信 Edit 的返回值。本会话是靠 `Grep QuestionDeleteOut` 才定位到漏了哪两处。

**经验**：**一次 Edit 只改一处**，改完马上回读。多处改动合并成一次 Edit 时，
失败了你不知道是哪一处没落地。

---

## 20. 登录门禁把"落地页"写死成 `/users` → 教研进不了后台 ★（Batch 4 验收实测）

**现象**

`researcher`（教研）角色有 `question:read`，是题库模块的**主要使用者**。用它登录后：
要么被登录页判定为"没有后台权限"直接踢到 403，要么登录成功却被送到 `/users` 再吃一个 403。
换句话说，**最该进题库的人进不来**。

**根因**

"谁能进后台""进来落在哪一页"被**写死在几个地方**，且都以用户管理为准：

- 登录页把允许列表写死成 `["user:read", "system:audit"]`
- 登录成功、控制台首页 `redirect`、`(console)/layout` 的 `next` 兜底、`middleware`
  这 4 处各自写死了 `/users`

只要新增一个模块（Batch 4 的题库），这个写死点就会漏掉新权限。**同一个事实有 5 份拷贝，
改不全就是 403。**

**解法：把"模块清单"收敛成一份，其余全部由它推导**

`apps/admin/src/lib/permission.ts` 增加单一事实源：

```ts
export const MODULE_ENTRIES = [
  { perm: P.questionRead, href: "/questions",  label: "题库管理" },
  { perm: P.userRead,     href: "/users",      label: "用户管理" },
  { perm: P.systemAudit,  href: "/audit-logs", label: "审计日志" },
] as const;

export function canEnterConsole(perms): boolean  { return MODULE_ENTRIES.some((m) => can(perms, m.perm)); }
export function landingPath(perms): string | null { return MODULE_ENTRIES.find((m) => can(perms, m.perm))?.href ?? null; }
```

然后 5 个入口全部改成调用它：登录页用 `canEnterConsole` 判断、
`resolveTarget = sp.get("next") || landingPath(perms) || "/forbidden"`，
控制台首页 / layout 兜底 / middleware 一律改成 `/`（交给首页自己按权限决定）。
侧边栏也直接由 `MODULE_ENTRIES` 渲染，不再维护第二份导航表。

**验证方式**：superadmin 登录后应落在 `/questions`（题库是第一个有权限的模块），
而不是以前的 `/users`。

**落点**：`apps/admin/src/lib/permission.ts`、`app/login/page.tsx`、
`app/(console)/page.tsx`、`app/(console)/layout.tsx`、`src/middleware.ts`、
`src/components/layout/Sidebar.tsx`

**教训**：任何"权限 → 去向"的映射，只要在多处出现，就一定会漏。
**收敛成一份 + 全部由它推导**，比"记住还有 4 处要改"可靠得多。

---

## 21. 雪花 ID 在**请求方向**同样会丢精度（坑 1 的补完）★（Batch 4 验收实测，✅ Batch 5 Pass 0 已修）

**现象**

题库列表勾选 2 道题 → 批量删除 → 接口 `HTTP 200`、业务码 `code=0`，
但 `data.deleted = 0`、`skipped` 里有 2 个 ID。前端 toast 写「已删除 0 道题」，
**不报错**，操作者以为删了，其实一条没动。

**根因**

坑 1 只堵了**响应方向**（后端 `BigIntStr` + 前端 `parseJsonSafe`）。
但这次出问题的是**我们自己发出去的请求体**：

```ts
// ❌ 旧写法
batchDelete.mutateAsync({ ids: selected.map(Number) });
//                                     ^^^^^^^ 在 JSON.stringify 之前就已经舍入
```

`Number()` 发生在序列化之前，请求体里带的已经是错值，后端按 ID 查不到行 →
`skipped`。响应方向的防御垫完全拦不住它。

**⚠️ 这条坑的真正难点：它不是"必然触发"，而是取值相关**

本项目的雪花布局是 `ts << 22 | worker << 12 | seq`（`apps/api/app/core/idgen.py`），
`worker_id` 默认 1，所以低 22 位 = `4096 | seq`。

当前 ID 量级约 3.7e17（位于 `2^58`~`2^59`），此时 float64 的 **ULP = 2^(58-52) = 64**，
而 4096 恰好是 64 的倍数：

| `seq` | 低 22 位 | `mod 64` | `Number()` 结果 |
|---|---|---|---|
| 0（每毫秒第一个） | 4096 | 0 | **无损** |
| 1~63（同一毫秒内第 2~64 个） | 4097~4159 | 1~63 | **失真** |
| 64 | 4160 | 0 | 无损 |

后果比"偶尔删不掉"更糟：**同一毫秒内那 64 个不同的 ID，经 `Number()` 后塌缩到同一批值**
（实测 64 个 ID → 只剩 2 个不同的 float64 值），等于把 64 道题指向同一个不存在的 ID。

实测（`tools/local-verify/probe-id-precision.py`，用真实生成器）：

```
#                   ID  seq  mod64          Number() 之后  失真
1   375484566565031936    0      0   375484566031936  否
2   375484566565031937    1      1   375484566565031936  是
...
64 个不同的 ID -> Number() 之后只剩 2 个不同的值
```

**为什么一直没暴露**：后台手点新建，每次一个 HTTP 往返，几乎总是落在不同毫秒的
`seq=0` —— 全量扫描 6021 条真实数据，**失真 0 条**。
但一旦走**批量导入 / 脚本刷数据 / 并发新建**（同一毫秒连出多个 ID），立刻翻车。
而 `idgen.py` 的注释里恰好写着"批量导入时可以预先在应用层分配 ID"——
也就是说这是一颗**已经埋好的雷**。

**解法（三层）**

1. **类型层**：`QuestionBatchDeleteIn.ids: string[]`（原来是 `number[]`），
   `QuestionCreateIn.subject_id / chapter_id: string`。
   让"传数字"在编译期就过不去。
2. **调用层**：一律 `ids: selected`（原样传字符串）、`subject_id: d.subject_id`，
   `diffDraft` / `draftToCreate` 里**所有 `Number(...id)` 全部去掉**。
3. **体检层**：`api.ts` 新增 `assertNoUnsafeIdInBody()`，dev 模式下遍历**请求体**，
   发现超出安全整数范围的 number 就 `console.warn` ——
   与响应方向的 `assertNoUnsafeIds()` 形成闭环。

**⚠️ 鉴伪提醒**：`skipped` 同时覆盖两种情况——「ID 不存在」**和**「该题本来就是软删除态」
（见 `question_service.py` 的 `to_delete / skipped` 划分）。
所以看到 `deleted=0, skipped=[...]` 时，**先确认是不是重复删了同一批**，
再怀疑精度问题。本会话就先用「连删两次」复现了 `skipped`，才排除掉这一半可能。

**落点**：`apps/admin/src/lib/types.ts`、`src/lib/question.ts`、`src/lib/api.ts`、
`src/app/(console)/questions/page.tsx`、`tools/local-verify/probe-id-precision.py`

**教训**：ID 必须**全程**以字符串传递——从后端出参，到前端 state，到请求体。
任何一个环节 `Number()` 一下，整条链路就断了；而且断得**静默**。

### ✅ Batch 5 Pass 0：已修（附一个重要更正）

写批量导入之前，先把这条彻底钉死。修的过程中**推翻了一个前提**，值得记下来：

> **更正**：这条坑**不是生成器坏了**。
> 用真实生成器实测（`tools/local-verify/probe-snowflake-batch.py`）：
> 连续生成 6000 个 ID，`len(set(ids)) == 6000`、严格单调递增 —— Python 侧从没重复过。
> 塌缩只发生在 `float64` 那一刻。旧标题说"批量导入必踩"是对的，
> 但"踩"的是**传输层**，不是生成器。

所以在 `apps/api/app/core/idgen.py` 里把它拆成**两个不变量**分开治：

| 不变量 | 归属 | 内容 |
|---|---|---|
| **A. 唯一性** | `idgen.py` 负责 | 同一毫秒 seq 取满 4096 个后**换毫秒**，绝不把 seq 回绕成 0 复用 |
| **B. 传输** | 调用方负责 | ID 跨进程/跨语言**必须走字符串**，禁止 `Number()` / `float()` |

**修法一（不变量 A）**：`next_id()` 的 seq 分配从
`(seq + 1) & MAX_SEQUENCE` 之后才判断换毫秒，改成**先判容量、再决定是否换毫秒**：

```python
if ts == self._last_ts:
    if self._sequence >= MAX_SEQUENCE:      # 容量先判
        ts = self._wait_next_ms(self._last_ts)   # 换毫秒
        self._sequence = 0                       # 归零，不是回绕
    else:
        self._sequence += 1
else:
    self._sequence = 0
```

旧写法虽然也换毫秒，但"先回绕、后换毫秒"把正确性藏在赋值顺序里，极易被后人改坏。
同时新增 **`next_ids(count)`** 批量预分配（导入管道用），内置
`len(set(out)) == count` 断言 —— 模块注释里"批量导入时预先分配 ID"那句话，
到这一批才真正有了实现。

**修法二（不变量 B）**：`idgen.py` 新增三个显式守卫，让"想把 ID 变成数字"这件事**当场报错**：

- `is_float_safe(v)` —— `float(v) == v` 才是可靠判据（注意 `2^53` 本身**可以**精确表示，
  不可表示的是 `2^53+1`，所以安全上限是 `2^53-1`）；
- `assert_float_safe(v)` —— 失败即抛 `ValueError`，提示改用字符串；
- `float_loss_report(ids)` —— 量化"这批 ID 如果走了 float64 会烂成什么样"。

**实测数据（新增探针 `probe-snowflake-batch.py`）**：

```
连续生成 6000 个：distinct = 6000                     # A 通过
6000 个 ID -> float64 后只剩 98 个不同值（塌缩率 98.4%）  # B 的代价
同一毫秒内 4095 个 ID -> float64 后只剩 65 个
冻结同一毫秒连取 4100 个：distinct = 4100，跨 2 个毫秒    # 不变量 A 的边界
```

还有个**反直觉**的点，顺手记下：`seq=0` 的 ID（低 22 位 = 4096，是 64 的倍数）
**恰好落在 float64 网格上、是无损的**；失真的是 `seq=1..63`。
这就是"手点新建（总在 seq=0）永远测不出来、批量导入（必然 seq>0）必踩"的根因。

**守卫测试位置（回归防线）**

- `apps/api/tests/test_idgen.py` —— **15 个用例**，纯单元、不依赖 DB/API：
  - `TestUniqueness::test_连续生成5000个不重复`（验收要求）
  - `TestUniqueness::test_高频连续生成也不重复`（验收要求，`sleep(0)` 抖动 + 12000 个）
  - `TestUniqueness::test_同毫秒seq取满后换毫秒而不是回绕`（冻结时钟的边界）
  - `TestUniqueness::test_并发调用不重复`（8 线程 × 500）
  - `TestTransportGuard::test_雪花ID的float安全性取决于seq`（把上面的反直觉点钉住）
- `tools/local-verify/probe-snowflake-batch.py` —— 可独立跑的体检探针，
  输出 A/B 两类的实测数字，供评审与文档引用。

---

## 22. 剪贴板写入失败，却照样报「已复制」—— 假成功 + unhandled rejection ★（Batch 4 验收实测）

**现象**

题库列表勾 2 条 → 点「复制 ID」→ toast 弹出绿色的「已复制 2 个题目 ID」，
同时页面左下角冒出 Next.js 开发覆盖层的红色 **`1 error`** 徽标。

**根因**

```ts
// ❌ 旧写法（questions/page.tsx）
onClick={() => {
  void navigator.clipboard?.writeText(selected.join("\n"));
  toast.success(`已复制 ${selected.length} 个题目 ID`);   // ← 无条件成功
}}
```

两个问题叠在一起：

1. **`void` 丢掉了 Promise**。剪贴板写入是异步的，而且**可能被拒绝**
   （权限被拒 / 文档未聚焦 / 非安全上下文）。实测在本环境下直接拒绝：
   ```
   secureContext: true
   clipboard: "object"
   writeText -> REJECTED: NotAllowedError: Write permission denied.
   ```
   被 `void` 丢掉的拒绝变成 **unhandled rejection**，Next.js 开发覆盖层就会甩一个红点出来。

2. **成功提示不挂钩真实结果**。无论写没写成功都弹「已复制」，是典型的
   **假成功**——和坑 21 的"批量删除静默跳过"是同一类问题：
   **UI 反馈没有绑定操作的真实结果。**

**解法**

```ts
try {
  if (!navigator.clipboard) throw new Error("browser has no clipboard api");
  await navigator.clipboard.writeText(selected.join("\n"));
  toast.success(`已复制 ${selected.length} 个题目 ID`);
} catch {
  toast.error("复制失败：浏览器拒绝了剪贴板写入", {
    description: "请检查浏览器剪贴板权限，或在题目详情页手动选中复制。",
  });
}
```

验证：改完后点「复制 ID」，toast 变成**红色错误提示**，且 `unhandledrejection` 计数为 **0**。

**同类残留（Batch 3 代码，本批未改，留作后续）**：
`app/(console)/users/[id]/page.tsx`（2 处）、`components/AuditDiffDrawer.tsx`、
`app/providers.tsx`（复制 trace_id）、`components/ErrorState.tsx`
—— 都是 `void navigator.clipboard?.writeText(...)` + 无条件 success。
建议后续抽一个 `copyText()` 小工具统一收口。

**落点**：`apps/admin/src/app/(console)/questions/page.tsx`

**教训**：凡是"乐观地直接报成功"的地方，都要问一句——
**这个成功提示，是接到了真实结果，还是只是"我发出了请求"？**

---

## 23. 用例"隔一天只能跑两遍"：短信按 IP 每日限流 20 次 + fakeredis 状态在进程内 ★（Batch 4 验收实测）

**现象**

第一遍 `pytest` 26 passed；过一会儿再跑，突然变成 **9 failed / 17 passed**，
报错都指向同一个地方：

```
tests/conftest.py:55: AssertionError
{'code': 42902, 'data': None, 'message': '今日验证码发送次数已达上限，请明天再试'}
```

看起来像"注册链路挂了"，其实注册链路一点问题没有。

**根因（两层，叠在一起）**

1. `conftest.py` 的 `register()` 走的是**真实注册流程**：先 `send_code()` 拿验证码再注册。
   而 `sms_service.py` 有**按 IP 的每日上限**，`config.py` 里默认
   `sms_daily_limit_per_ip = 20`。
   本地联调所有请求都来自 `127.0.0.1` —— **同一个 IP**。
   跑一遍全量用例就要注册十几个用户，**两遍就把当天额度用光**。

2. 限流计数存在 Redis 里，而本地用的 `tools/local-verify/serve_fake_redis.py` 是
   **`fakeredis` 直接在 API 进程内**（不是独立端口）。
   所以**没有端口可以连上去 `FLUSHDB`** —— 把 6379/63790 都试一遍只会得到
   `ConnectionError: 目标计算机积极拒绝`（这本身就是个误导信号：API 明明活着，
   你却在怀疑 Redis 没起）。

**解法**

- **临时**：重启 API 进程即可清零（`fakeredis` 状态随进程消失）。
  改完 <code>config.py</code> 里的额度后也要重启才生效。
- **根治（建议）**：本地/CI 把 `SMS_DAILY_LIMIT_PER_IP` 调大（或让
  `send_code` 在 `APP_ENV=test` 时跳过限流计数），否则这套用例**每天只能跑两遍**，
  快速迭代时非常难受。

**怎么快速判断是"限流"而不是"真挂了"**：看报错的 `message`。
限流会明确写"今日验证码发送次数已达上限"或"发送过于频繁，请 N 秒后再试"；
真正的链路故障不会这么具体。

**落点**：`apps/api/app/services/sms_service.py`、`apps/api/app/core/config.py`
（`sms_daily_limit_per_phone = 10` / `sms_daily_limit_per_ip = 20`）、
`apps/api/tests/conftest.py`、`tools/local-verify/serve_fake_redis.py`

**教训**：**先怀疑状态，再怀疑代码。** 同一个套件"刚才还绿、现在红了"，
八成是**有状态的东西**（限流计数、缓存、软删除数据、端口占用）在起作用 ——
坑 17 是端口，坑 21 的 `skipped` 是软删除数据，这条是限流计数。

**Batch 5 补充：一个**不用重启也能跑多遍**的绕法**

上面的"临时解法"是重启 API。但如果你只是在**自己新写的用例**里需要注册用户，
可以让这个用例把限流桶**隔到自己的假 IP 上** —— 服务端 `trust_proxy_headers=true`
时，客户端 IP 以 `X-Forwarded-For` 首个地址为准（`deps.client_ip`）：

```python
# apps/api/tests/test_admin_v5.py::fresh_user
ip = f"203.0.113.{random.randint(2, 250)}"          # TEST-NET-3 段，不会被路由
h = {"X-Forwarded-For": ip}
client.post(f"{API}/auth/sms/send", json={"phone": phone, "scene": "register"}, headers=h)
client.post(f"{API}/auth/register", headers=h, json={...})
```

这样每个用例 1 次发送/注册，各占一个桶，**不会去抢整个套件共用的那份 20 次额度**。
注意：这只对"你自己写的用例"有效；`test_smoke.py` 用的 `conftest.send_code` 不带这个头，
全量跑很多遍依然会撞上限 —— 真正一劳永逸的还是上面那条"根治（建议）"。

**落点**：`apps/api/tests/test_admin_v5.py::fresh_user`

---

## 24. 含错的文件照样写库：`validate` 报 2 行错，`execute` 却写了 98 行 ★（Batch 5 验收实测）

**现象**

按验收标准 ① 造了一份"100 行里第 57 行答案不在选项、第 100 行章节编码不存在"的文件：

```
校验结果 total=100 success=98 failed=2 total_errors=2
    row_no=57   field=answer       答案 D 不在选项中（本题选项：A、B、C）
    row_no=100  field=chapter_code 章节编码不存在：SW-SZ 下没有 SZ-99
```

校验报告完全正确。接着调 `execute` —— **它把通过的 98 行全写进库了**。
而验收标准 ① 的原文是"精确报出行号+字段+原因，**不写入**"。

**根因（两处文档打架，代码跟着歪）**

`docs/07` §4.4 的示例里写着"执行（只导 insert，**跳过错行**）"，
读起来像是"execute 天然会跳过错误行、把好的导进去"。于是实现就顺着这个语义走：
execute 只挑 `action IN ('insert','update')` 的行写，错误行本来就不在集合里。

但验收标准① 要的是"错误文件 → 不写入"，要求 3 又写着"**整批成功或整批失败**"。
两条要求合起来的唯一自洽读法是：**有错行就不许执行**。

**修法：默认严格，跳过错行必须显式 opt-in**

```python
# import_service.execute_batch
if batch["failed_rows"] > 0 and not allow_partial:
    raise conflict(f"本批次有 {batch['failed_rows']} 行未通过校验。按「整批成功或整批失败」"
                   "的约定，不会写入任何数据。请修正后重新上传；"
                   "若确认只导入通过的行，请在 execute 时传 \"allow_partial\": true。", 40901)
```

`ImportExecuteIn.allow_partial` 默认 `false`。这样：
- 验收① 成立（默认一行不写）；
- `docs/07` §4.4 的能力也没丢（想要就显式要）—— **不是简化，是把默认值从"宽松"拧成"严格"**。

**落点**：`apps/api/app/services/import_service.py::execute_batch`、
`apps/api/app/schemas/admin_import.py::ImportExecuteIn`

**测试位置**：`apps/api/tests/test_admin_v5.py::test_wrong_file_reports_row_and_field_and_writes_nothing`
（同一批次：先断言默认 40901 + 题量不变，再断言 `allow_partial=true` 写入 98 行、回滚后回到基线）

**教训**：当两份文档对同一行为各说各话时，**取"更严"的那条**并写成默认值，
把"更松"的那条降级成显式开关。默认值选松，等于把事故当默认。

---

## 25. 上传的原文从没落盘：`execute` 永远"原文件已不可用" ★（Batch 5 自审发现）

**现象**

`import_service` 里明明有这段：

```python
# 上传的原始文件：进程内暂存（本批不做对象存储）
_FILE_STORE: dict[int, bytes] = {}

async def _read_batch_file(batch_id: int) -> bytes | None:
    return _FILE_STORE.get(batch_id)
```

但 `grep -n _FILE_STORE` 的结果只有**定义**和**读取**两处 ——
`create_batch` 里**根本没有写入**。也就是说 `_read_batch_file()` 恒返回 `None`，
`execute` 必然抛"批次原始文件已不可用"。

**为什么会写成这样（值得记的思维陷阱）**

`execute` 需要"重新解析原文件"才能拿到完整 payload ——
因为 `import_items.raw` 是**瘦身过的**（超长文本截断到 200 字），不足以重建题目内容。
这个设计是对的，但它把"原文必须持久化"变成了一条**隐性依赖**；
而写 `create_batch` 时注意力都在"解析 + 建批次 + 存行"上，
"存原文"这件事只留下了一个**看起来很完整的空容器**。
`compileall` 过、OpenAPI 生成正常、`import` 不报错 —— 只有真跑一次才会炸。

**修法：落盘 + 原子替换；顺手把进程内存这个坑也填了**

```python
_IMPORT_FILE_DIR = Path(tempfile.gettempdir()) / "yijian-import-files"

def store_batch_file(batch_id: int, file_type: str, content: bytes) -> None:
    _IMPORT_FILE_DIR.mkdir(parents=True, exist_ok=True)
    target = _batch_file_path(batch_id, file_type)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(content)
    os.replace(tmp, target)          # 原子替换：不会读到半截文件

async def _read_batch_file(batch_id: int, file_type: str) -> bytes | None:
    ...                              # asyncio.to_thread 读盘，避免阻塞事件循环
```

**为什么不继续用进程内 dict**：`execute` 与 `upload` 可能落在**不同 worker**，
进程重启 / `--reload` 一改代码就丢。任何一个发生，都会在 execute 时表现为
"上传明明成功了却读不到原文"。落盘后这些都不成立；生产换对象存储（`import_batches.file_url`）时
接口形状不用动。

另外把"先确认原文还在"提到**翻状态之前** —— 否则会留下一个 `status='importing'`
却必然失败的批次（这种半截状态最难排查）。

**落点**：`apps/api/app/services/import_service.py`（`_IMPORT_FILE_DIR` / `store_batch_file` /
`_read_batch_file` / `execute_batch` 的前置检查）

**测试位置**：任何一条走完 `upload → validate → execute` 的用例都会覆盖
（`test_full_pipeline_execute_publish_rollback` 等）；另见 `import-sim-bank.py` 的 6000 行全量。

**教训**：**"定义了一个容器" ≠ "往里写过东西"**。
`grep` 一个变量名，看清它出现在"写"还是"只出现在读" —— 这是最便宜的自审。

---

## 26. `execute` 允许覆盖 `mode` → 校验与执行分裂；且 `done` 分不清"待执行/已执行" ★（Batch 5 自审发现）

**现象一：mode 覆盖**

初版 `ImportExecuteIn` 有个 `mode` 字段（"不传则用批次创建时的设置"）。问题在于：
**校验阶段**已经按 `batch.mode` 把每行判成 `insert / update / duplicate` 落进 `import_items`，
**执行阶段**若按另一个 mode 重算 payload，就会出现"校验说 update、执行按 insert 写"。
这种 bug **只在数据里看得出来**，接口全部 200。

**修法**：删掉 execute 的 mode 覆盖。mode 在**上传时定死**，要换就重新上传（重新校验）。
`execute` 重算 payload 时一律沿用 `batch["mode"]`，与校验阶段同源。

**现象二：`done` 是三个意思**

`import_batches.status` 的枚举是
`pending / parsing / validating / importing / done / failed / rolled_back`，
**没有独立的"已执行"态** —— 于是 `done` 同时表示"校验完待执行"和"已经导完了"。
结果：同一批可以 `execute` 两次。危害是**静默**的：

- `insert` 模式重跑：全部命中为 `duplicate` → 0 命中 → `success_rows` 被重写成 **0**（统计被打烂）；
- `upsert` 模式重跑：6000 道题 `version` **再顶一轮**。

**修法**：不动 schema（改 CHECK 要重建库，会牵动 Batch 2/3/4 数据），
改用"本批是否已有 `content_change_logs(action IN ('create','update'))`"来判定"已执行"：

```python
if await _executed_batch_ids(db, [batch_id]):
    raise conflict("该批次已经执行过导入，不能重复执行。要重导请新建批次；要撤销请调用 /rollback。", 40901)
```

`can_execute` 也据此计算 —— 这样列表页的按钮状态与接口行为**永远一致**
（不会出现"按钮亮着、点了 409"）。

**落点**：`apps/api/app/services/import_service.py`（`_executed_batch_ids` / `_capabilities`）、
`apps/api/app/schemas/admin_import.py::ImportExecuteIn`

**测试位置**：`test_execute_twice_and_rollback_twice_are_rejected`、`test_rollback_restores_upserted_questions`

---

## 27. 校验拦得住业务规则，拦不住**列宽** —— 于是它成了"中途异常"的最佳构造 ★（Batch 5 验收实测）

**现象**

要验"执行中途某行异常 → 整批回滚"（验收④），最自然的构造是：
**让某一行能过校验、却写不进库**。`validate_row` 只做业务规则（必填/枚举/外键/题型-选项一致性/
答案合法性/合规红线），**不做长度上限**；而 `questions.source_name` 是 `VARCHAR(160)`。
于是：

```python
rows.append(row(source_type="authorized",
                source_name="授权来源" * 50,      # 200 字 > 160
                source_license="HT-2026-0001"))
```

→ 校验 `success=6 failed=0`（全过！），`execute` 时 PG 报 `value too long for type character varying(160)`，
整批回滚、`status='failed'`、题量不变。

**这不是 bug，是分工**：validate 管业务规则，列宽这类结构性约束由 DB 兜底。
但**必须写下来**，否则下一个人会以为"校验通过 = 一定能写进库"。
另外要清楚：`_write_rows` 的"每 500 行一批"是**同一事务内的分批写**，不是分批提交 ——
所以第 6 行炸了，前 5 行也一起回滚（这正是验收④要的）。

**落点**：`apps/api/app/services/import_service.py::_write_rows`、`validate_row`、
`db/schema.sql`（`questions.source_name VARCHAR(160)`）

**测试位置**：`test_mid_import_failure_rolls_back_everything`

**教训**：**"校验通过"和"能落库"是两件事**。想验事务边界，就要故意用
"能过业务校验、过不了 DB 约束"的数据 —— 而不是随便写一行错数据（那会被 validate 提前拦下，验不到 execute）。

---

## 28. Batch 1 的导出格式 ≠ Batch 5 的导入模板：直接喂进去会把 6000 道题的章节洗成 NULL ★（Batch 5 验收实测）

**现象 / 风险**

`data/seed/questions.csv` 是 Batch 1 的**导出**，看起来"列名几乎一样"，但差在三处：

| 差异 | seed 导出 | 导入模板（`docs/07` §4.2） | 直接喂进管道的后果 |
|---|---|---|---|
| 章节 / 知识点 | `chapter_id` / `knowledge_point_id`（数字 ID） | `chapter_code` / `kp_code`（业务编码） | 两列都不认识 → **章节关系全丢（写成 NULL）** |
| 评分点 | 无该列 | `answer_points`（`文本｜分值;;…`） | 352 道案例小问的评分点丢失 |
| 案例分组 | 只有 `parent_id` | `case_group_id` | 小问找不到大题 → 校验直接报错 |

在 upsert 模式下，"章节关系写成 NULL"= **把库里 6000 道题的章节洗掉**，属于数据事故。
所以验收⑤ 专门写了一个转换器 `tools/local-verify/import-sim-bank.py`，
从 `questions.json`（信息最全）重编成严格 24 列的模板文件。

**第二个必须提前体检的点：`content_hash` 必须逐条对齐**

6000 道 seed 题**已经在库里**。如果导入时重算的 `content_hash` 与库里的差一个字节：

- `insert` 模式 → 全部 miss，插出 6000 道重复（题量翻倍）；
- `upsert` 模式 → 全部 miss，走 insert 分支，同样是重复。

所以转换后**先做 hash 对齐体检再灌库**。实测结果：

```
连续重算 6000 条 content_hash：ok=6000  bad=0        # 与 seed 存的完全一致
```

落成两条回归防线：

- `test_seed_bank_mapping_matches_existing_rows`（不必开全量也能跑）：
  取 300 道 seed 用 `insert` 模式跑 → 断言 **`duplicate=300`、`success=0`**。
  「全部命中已有题」这一条同时证明**转换器没改坏内容** + **幂等在真实体量上成立**，
  而且**一行都不写库**，可以天天跑。
- `import-sim-bank.py --dry-run`：全量 6000 行的体检（约 12s），只上传+校验。

**顺带记两条"重导会改什么"（都是可解释的规范化，不是静默篡改）**

1. `multiple` 题的 `answer` 会补上 `"partial_credit": true` —— 对齐 `docs/03` §5.1 的权威定义
   （seed 漏了这个键；`single`/`judge`/`case`/`case_sub` 与 seed 完全一致）。
2. `analysis_points` 的整分值写成 `2` 而不是 `2.0` —— **jsonb 里 `2` 与 `2.0` 是两个不同的值**，
   所以 `parse_answer_points()` 特意 `int(num) if num.is_integer()`，避免"重导同一份文件"产生无意义 diff。

**落点**：`tools/local-verify/import-sim-bank.py`、`apps/api/tests/test_admin_v5.py`（两个 seed 用例）、
`apps/api/app/services/import_service.py::parse_answer_points`

**教训**：**"看起来列名一样"是最贵的错觉**。跨批次复用数据前，先把它和**当前**契约逐列对齐，
再拿主键/指纹字段做**逐条**体检 —— 抽样 10 条对得上，不代表 6000 条对得上。

---

## 附：一条"新增导入管道"的检查清单

照这个顺序做，上面 24~28 逐条都能提前避开：

- [ ] **闸门**：文件类型/大小/编码在**上传**就拒，别等到解析（坑 24 的邻位：错误要早报）
- [ ] **dry-run 必须零副作用**：`validate` 一行都不许写库，且要能被反复调用
- [ ] **逐行错误**：`{row_no, field, message}`，`row_no` 是**文件里的数据行号**（不含表头）
- [ ] **默认严格**：有错行 → 整体拒绝；"跳过错行"做成显式开关（坑 24）
- [ ] **原文持久化**：`execute` 要重新解析原文，就先确认它**真的被写过**、且**能跨进程读到**（坑 25）
- [ ] **mode 单一来源**：定在上传/校验，执行阶段不许改（坑 26）
- [ ] **幂等靠指纹**：`content_hash` 由**服务端重算**，不信文件里带的（坑 28）
- [ ] **事务边界**：分批写 ≠ 分批提交；`finally` 兜底整批回滚，`status='failed'`
- [ ] **回滚留痕**：软删除 `insert`、还原 `update`、写 `content_change_logs(action='rollback')`
- [ ] **合规红线**：`source_type != 'self'` 必须有 `source_name`（`authorized` 还要 `source_license`）
- [ ] **数据范围**：复用 `scope_subject_ids()` 这**一个**事实源，别在导入里再写一套过滤

---

## 附：一份"新增一个后台页面"的检查清单

照这个顺序做，上面大部分坑都能提前避开：

- [ ] 接口是否返回了 ID？→ 后端出参字段套 `BigIntStr`（坑 1）
- [ ] 接口是否需要新权限？→ 后端 `require_permission(...)` + 前端菜单/PermissionGate + 页内 403 兜底
- [ ] 有没有 `INET` / `JSONB` / `NUMERIC` 字段？→ service 层显式转换（坑 6）
- [ ] 表格状态是否进了 URL？（坑 11）
- [ ] 空态是否区分了两种情况？（坑 12）
- [ ] loading 是否用了与真实行等高的骨架？
- [ ] 写操作成功后是否 invalidate 了所有受影响的 key？（坑 14）
- [ ] 展示时间是否指定了 `Asia/Shanghai`？（坑 5）
- [ ] 跑验收前 `netstat -ano | findstr :8123` 确认没有遗留进程占端口？（坑 17）
- [ ] 新增的 service/schema 模块有没有"引用了没导入"的名字？跑静态守卫（坑 18）
- [ ] 关键文件的 Edit 是否逐个回读确认落地了？（坑 19）
- [ ] 有敏感字段吗？→ **后端**按权限决定下发内容（坑 4）
- [ ] 会返回 401 吗？→ 确认每个 401xx 的"要不要刷新 / 要不要跳登录"都归了位（坑 9、坑 16）
- [ ] 不可撤销的写操作是否二次确认？确认文案是否复述了关键信息？
- [ ] 按钮置灰是否给了可执行的 tooltip？是否套了 `<span>`？（坑 3）
- [ ] 该接口的自动化用例加了吗？`run-smoke.ps1` 还绿吗？
- [ ] **新增模块的落地页**：`MODULE_ENTRIES` 加了吗？"谁能进后台/落在哪"是否全部由它推导，
      没有第二处写死的 `/users`？（坑 20）
- [ ] **所有 ID 是否全程走字符串**：出参 `BigIntStr` → 前端 state → **请求体**；
      有没有漏一个 `Number()`？跑 `probe-id-precision.py`（坑 1、坑 21）
- [ ] **每个"成功"提示**是不是真的绑定了操作结果？有没有乐观报成功 / `void promise` 丢拒绝？（坑 22）
