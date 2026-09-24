# B 端前后端联调常见坑

> **56 条**实战坑，覆盖 Batch 2 ~ Batch 7（认证/RBAC → 管理后台 → 题库 CRUD
> → 导入管道 → 导入向导 → 组卷引擎）+ 数据范围收口 + 门禁整顿 + 补测批。每条都按
> **现象 → 根因 → 解法 → 在本项目里落在哪个文件** 写，方便以后照方抓药。
>
> 读法建议：
> - 排序大致按"杀伤力"。**前 5 条**是会让人查半天的（雪花 ID 精度、Rotation 并发、
>   disabled 不出 tooltip、脱敏位置、时区）。
> - **第 15 条**是取舍留痕（token 存 localStorage），**不是 bug，是明知的选择**。
> - 按批次回溯：**1~16** Batch 2/3 ｜ **17~23** Batch 3/4 验收 ｜
>   **24~28** Batch 5 导入管道 ｜ **29~33** Batch 6 导入向导 ｜
>   **34~47** Batch 7 组卷引擎（前置修正 + 恢复接口 + Pass 2a + 结构防护）｜
>   **48~50** 数据范围收口（八条题目入口 + 批量语义 + 工具陷阱）｜
>   **51** 门禁整顿（验收脚本的"假绿"）。
> - 末尾四份**检查清单**（新增导入管道 / 新增后台页面 / 新增状态回退接口 / 一批交付收尾）
>   是前面各条的"可执行版"，开新功能前照着过一遍，大部分坑能提前避开。

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

## 29. 回滚确认框的数字：`success_rows` ≠ "将删除的题数" ★（Batch 6 浏览器走查发现）

**现象**：一个 upsert 批次导了 12 道题，其中 10 道是命中库里已有题的**更新**、只有 2 道是**新建**。
回滚确认弹窗却提示"将软删除 **12** 道题"—— 是实际破坏范围的 6 倍。

**根因**：后端 `success_rows` 是**写入成功行数**，语义是"这一批处理成功了 N 行"，
在 upsert 模式下**包含命中已有题的更新行**。而回滚对这两类行的动作是**不同**的：

| 行的来源 | 回滚动作 |
|---|---|
| 本批新建（insert） | **软删除**（`is_deleted=true`） |
| 本批命中已有题（upsert update） | **还原**到导入前那一版（版本回退），题还在 |

拿 `success_rows` 当"将软删除"的数量，就把"还原"也数进了"删除"。

**修复**（`RollbackDialog.tsx`）——拆成两个数，并**分开说**：

```ts
软删除 = Math.max(0, success_rows - updated_rows)   // 本批真正新建的
还原   = updated_rows                               // 本批覆盖过的老题
```

再补一句"跳过 N 道重复题 → 本就不属于本批写入，不受影响"，
让三个数（软删除 / 还原 / 跳过）加起来正好等于文件总行数，用户能自己对账。

**教训**：**破坏性操作的确认框，数字必须按"将被执行的动作"分类，
而不是照抄后端返回的汇总字段**。后端字段是给程序算的，不是给用户看的。
凡是把"后端计数"直接塞进"将删除 N 条"的地方，都要问一句：
**这个 N，和即将发生的动作，真的是同一件事吗？**

---

## 30. 失败信息画在弹窗**外面** → 用户只看到弹窗"闪了一下还在" ★（Batch 6 走查发现）

**现象**：回滚失败时，弹窗不关（这是对的，为了便于重试），
但错误 toast 在弹窗**背后** —— 用户只感觉到弹窗"闪了一下又回来了"，
既不知道成没成，也找不到重试入口。

**根因**：错误提示被放在**页面级**（`MutationCache` 统一弹 toast），
而这次提交动作是**从一个模态框里发起的**。模态框天然遮挡页面级反馈。

**修复**：`RollbackDialog` 新增 `error` prop，在弹窗**内部**画 `InlineError`，
并复用弹窗自己持有的 `reason` state 做重试（用户刚填的回滚原因不会被清掉）：

```tsx
<InlineError
  title="回滚"
  error={error}
  onRetry={() => void onConfirm(reason.trim() || undefined)}
  retrying={loading}
  hint="回滚是整批一个事务：失败即代表题库里一道题都没动，批次状态也不会变。"
/>
```

**教训**：**反馈要画在"提交它的那个容器"里。** 全局 toast 是兜底，不是替代。
判据很简单：**这个动作是从哪个容器发起的？失败信息就必须出现在那个容器可见的范围内。**
模态框里发起的写操作，失败态一律画进模态框。

---

## 31. 无头浏览器抓不到下载 → 别为一个**环境限制**去改产品代码 ★（Batch 6 走查踩到）

**现象**：`agent-browser download <选择器> <路径>` 恒定返回 `Download was canceled`，
文件永远抓不到。第一反应是"下载实现有 bug"。

**排查**：先隔离 —— 在页面里手插一个 `data:text/plain;base64,...` 的 `<a download>` 再抓，
**同样失败**。说明不是应用的问题，是这个无头环境根本不暴露下载事件。

**期间的一个"假修复"（已还原，留痕）**：我一度怀疑是 `downloadTextFile` 里
`URL.revokeObjectURL` 的时机太早（`setTimeout(..., 0)` 只是"排到宏任务末尾"，
对"取消一次跨进程的文件下载"可能不够），改成 1000ms 去验证 ——
**结果一样是 canceled**，证明与 revoke 时机无关。**已把改动还原**：

```ts
// 立刻 revoke 在部分浏览器会打断下载，挪到下一帧
window.setTimeout(() => URL.revokeObjectURL(url), 0);
```

**替代方案**（`tools/local-verify/ab-capture-download.js`）：不抓文件，**抓字节**。
用 `AGENT_BROWSER_INIT_SCRIPTS` 注入脚本包住 `URL.createObjectURL`，
把传入 Blob 的 `type` / `size` / **前 4 字节** / 全文存到 `window.__yb*`：

```bash
export AGENT_BROWSER_INIT_SCRIPTS="$PWD/tools/local-verify/ab-capture-download.js"
# 点下载按钮后：
#   eval "JSON.stringify({head:window.__ybBlobHead,text:window.__ybBlobText})"
```

这比"文件是否落盘"**更强**：BOM（`EF BB BF`）在不在、是不是 `\r\n`、列头对不对，
全部可在字节层面断言。

> ⚠️ 两个坑中坑：
> 1. **`--init-script` 是"启动参数"** —— 如果先跑 `set viewport`，daemon 已经起来了，
>    之后再加 `--init-script open ...` 它不生效。用**环境变量** `AGENT_BROWSER_INIT_SCRIPTS`，
>    它对每条命令都生效。
> 2. **`Blob.text()` 会把 BOM 吃掉**（UTF-8 解码时 BOM 被当作字节序标记剥掉）。
>    要验证 BOM 必须看**原始字节**（`blob.arrayBuffer()`），不能只看 `text()`。

**教训**：**任何"看起来像 bug"的现象，先做一次"最小可复现的隔离验证"，
区分「应用的问题」和「环境的问题」。** 隔离用的那 5 行代码，成本远低于改错一处产品逻辑。
以及：**证伪之后要还原。** 没有证据支持的改动，留在代码里就是技术债。

---

## 32. 截图目录里的"证据"可能是登录页 —— 提交前必须 md5 去重 + 亲眼看一下 ★（Batch 6 收尾发现）

**现象**：`docs/screenshots/batch6/` 里有 4 张文件，名字分别是
`01-imports-list.png` / `02-imports-new.png` / `02-login-filled.png` / `_02-new.png`，
**内容全是登录页**（上一轮走查在登录步骤中断，浏览器停在登录页就截了图）。
另外 `12-batch-changes.png` 与 `13-published.png` **逐字节相同**（滚动截图没滚到位）。

如果不看内容直接提交，整套"验收证据"就是假的 —— **比没有截图更糟**，
因为它会让人以为验收做过了。

**修复**：
1. 删掉全部 4 张登录页截图 + 1 张重复图，重新抓。
2. 抓完用 md5 分组，逐组确认"同哈希 = 真的应该相同"。

```python
import hashlib, glob
for p in sorted(glob.glob('*.png')):
    print(p, hashlib.md5(open(p,'rb').read()).hexdigest()[:10])
```

**教训**：**截图是证据，证据必须验真。** 三条硬规矩：
- 文件名里的编号**不代表内容**；提交前至少抽查每张图的开头几张（本批就是靠 `Read` 图片发现的）。
- **同哈希 = 同一张图**。任何"应该不同"的两张图如果同哈希，就是没抓到。
- 顺便记：仓库根目录一度出现过一个 27KB 的 `ProtectWorkBuddyONE`（PNG），
  是 agent-browser 路径含空格被截断产生的垃圾文件 —— **它进过 `git status`**。
  提交前扫一遍 `git status` 的 `??` 列表，不认识的文件先查清楚再说。

---

## 33. spec 里的状态名 ≠ 后端枚举：一个组件要能同时说两套语言 ★（Batch 6 走查发现）

**现象**：验收标准写的是 `pending → validated → importing → done → rolled_back`，
但后端 `ImportStatus` 枚举里**根本没有 `validated`**，而且 `done` 同时表示
"校验完了（还没执行）"和"执行完了"两种状态。

**根因**：坑 26 的连锁反应 —— 我们**刻意**没给 `status` 加枚举值，
改用"是否已执行"（`content_change_logs.batch_id` 有记录）来区分。
于是"业务语义"和"库里的值"是两套名字，`validated` / `published` 是前者，库里没有。

**修复**：**不新增后端枚举，也不在前端编造。** `FlowStep` 同时带两个字段：

```ts
{ key: "validated", label: "校验完成", stage: "validated", hint: "done", state: "todo" }
//                                    ^ spec 业务名            ^ 后端真实值
```

UI 主标签用 `label`（教研看得懂），下面一行等宽小字显示 `hint`（后端排查对得上）。
一个组件同时说清"业务在讲什么"和"库里存的是什么"。

| spec 名 | 后端值 | 说明 |
|---|---|---|
| `pending` | `pending` | 一致 |
| `validated` | `done` + 未执行 | 派生，不是枚举 |
| `importing` | `importing` | 一致 |
| `done`（已入库） | `done` + 已执行 | 同上，靠 `batch_id` 判定 |
| `rolled_back` | `rolled_back` | 一致 |
| `published` | **不建状态** | 发布是 `questions.status` 的动作，批次仍停在 `done` |

**教训**：**当"验收文档的词汇"和"数据库的词汇"不一致时，
不要默默选一边 —— 把两边都显示出来。** 对用户说人话，对排查说行话。
另外，`published` 这种"看起来像批次状态"的东西值得单独想一想：
**它到底是"这批导入"的属性，还是"被导入的那些题"的属性？** 这里是后者。

---

## 34. `subjects` 表**没有** `is_deleted` —— 别假设每张表都有软删除列 ★（Batch 7 Pass 1 实测）

**现象**：`POST /admin/exams` 稳定 50001，日志里是

```
asyncpg.exceptions.UndefinedColumnError: column "is_deleted" does not exist
[SQL: SELECT 1 FROM subjects WHERE id = $1 AND is_deleted = false]
```

**根因**：写"科目是否存在"的校验时想当然加了 `is_deleted = false`。
但 **`subjects` 用的是 `status IN ('on','off')`**，根本没有 `is_deleted` 列。

**全库哪些表真有 `is_deleted`**（`db/schema.sql` 实测）：

| 有 `is_deleted` | 没有（用别的机制） |
|---|---|
| `questions`、`exams`、`users` | `subjects`（`status`）、`paper_rules`（`status`）、`exam_sections`、`exam_questions`、`question_versions`、`content_change_logs`、`roles`、`permissions` |

**教训**：**"某张表有软删除"是一个需要查证的事实，不是可以类推的常识。**
Batch 4/5 一直在跟 `questions` / `exams` 打交道，很容易形成"库里到处都是 `is_deleted`"的错觉。
写任何"存在性/有效性校验"之前，先 `grep -A 20 "CREATE TABLE <表名>" db/schema.sql` 看一眼。

> 修法落点：`exam_service._assert_subject_usable()` —— 顺手把"科目被停用"也归到同一个校验里，
> 并把这个事实写进函数 docstring，避免下一个人再踩。

---

## 35. SELECT 列清单与 `row["x"]` 取值清单**会悄悄对不上**，且报错很像数据库问题 ★（Batch 7 Pass 1 实测）

**现象**：给 `_EXAM_SELECT` 补字段时漏了 `e.is_deleted`，而代码里照常 `row["is_deleted"]`：

```
sqlalchemy.exc.NoSuchColumnError: Could not locate column in row for column 'is_deleted'
```

**为什么比坑 34 更阴**：坑 34 是**数据库**报的（`UndefinedColumnError`，带 `[SQL: ...]`）；
这一条是**纯 Python 侧**报的 —— SQL 执行得好好的，只是结果行里没那一列。
堆栈里**没有 `[SQL: ...]` 那一行**，第一眼很容易当成"SQL 写错了"，
然后去反复改 WHERE 子句，改半天没有用。

**判据**：看有没有 `[SQL: ...]`。
- 有 → 数据库不认识这个列 → 改 SQL。
- 没有、只有 `Could not locate column in row` → SQL 没问题，是**取值端**与 **select 端**不一致。

**教训**：**`SELECT` 常量与"从这行取哪些字段"的函数要挨着写。**
本模块把 `_EXAM_SELECT` 和 `_exam_item()` / `_load_exam_row()` 放在相邻位置，
就是为了让这种不一致在 diff 里可见。列清单一旦散落到十个地方，这类 bug 就查不动了。

---

## 36. 权限**整包**发放 → 想验证"能看不能发"根本构造不出来 ★（Batch 7 Pass 1 实测）

**现象**：想给"发布需要 `exam:publish`"写一条权限门控用例，预期教研（`researcher`）能读不能发，
结果**发布直接成功了**。

**根因**：`db/schema.sql` 的权限种子是按**模块整包**发权限的：

```sql
INSERT INTO role_permissions SELECT 3, id FROM permissions WHERE module IN ('question','exam','stats');
```

于是 `exam` 模块的四条（`read` / `create` / `publish` / `grade`）
**同时**发给了 `super_admin`、`admin`、`researcher`、`teacher` 四个角色。
查库确认：这四个角色的 exam 权限**完全一样**，没有任何一个"缺 publish"。

**后果**：**当前角色模型无法表达"试卷只读"**。
这跟 Batch 3 遇到的"有 `user:read` 的都同时有 `user:manage`"是**同一类缺口**
（那次是靠新增 `viewer` 角色补上的）。

**本批的处理**：不新增角色（改种子超出组卷引擎的范围），
**把用例改成如实断言现状** —— `researcher` 走完 建卷→组卷→发布 全链路，
同时在 docstring 与 `docs/13` 的遗留事项里写明这个缺口。

**教训**：**写权限用例之前，先查一遍"这个状态在种子里能不能被构造出来"。**
`SELECT r.code, p.code FROM role_permissions rp JOIN roles r ON ... JOIN permissions p ON ...`
一条 SQL 的事，能省掉一轮"以为测了其实没测"。
另外，用例断言**现状**时要把"这是现状，不是理想"写进注释 ——
否则下一个人会把这条用例当成"设计意图"来维护。

---

## 37. 本机 PostgreSQL 必须作为**独立后台进程**常驻，否则会被工具会话回收 ★（Batch 7 Pass 1 实测）

**现象**：在同步工具调用里跑 `pg_ctl start`（或 `start-pg.ps1`），脚本自己打印了
`PostgreSQL 已就绪 -> 127.0.0.1:55432`，**但调用一返回，55432 立刻连不上**：

```
[local-verify] PostgreSQL 已就绪 -> 127.0.0.1:55432
55432: closed          ← 同一个调用序列的下一步
```

而且 abrupt kill 会留下 **stale `postmaster.pid`**，下次 `pg_ctl start` 直接
`another server might be running` 拒绝启动 —— 而 `run-smoke.ps1` **没有** stale-pid 自愈逻辑。

**修法（二选一）**：

1. **把 `postgres.exe` 本身当后台任务跑**（推荐用于反复迭代）：
   它就是那个常驻进程，不会因为"父 shell 退出"被回收。
   ```bash
   "$PGBIN/postgres.exe" -D "$PGDATA" -p 55432 -c listen_addresses=127.0.0.1   # 作为后台任务
   ```
2. **交给 `run-smoke.ps1` 全权托管**（推荐用于跑验收）：它会自己起 PG、跑完停掉。
   但**跑之前必须确认没有别的实例占着 55432/8123**，且**先删掉 stale `postmaster.pid`**。

**清理 stale pid 前必须先确认没有 postgres 在跑**：

```powershell
$procs = @(Get-Process -Name postgres -ErrorAction SilentlyContinue)
if ($procs.Count -eq 0) { Remove-Item "$env:USERPROFILE\.workbuddy\binaries\pg\data16\postmaster.pid" -Force }
```

**教训**：**"脚本说它起来了" ≠ "它还在"。** 判断服务是否常驻，要在**下一次**工具调用里验端口，
而不是信同一次调用里的成功输出。这也是坑 17（端口预检）的兄弟条 ——
一个防"旧进程占着端口"，一个防"新进程已被回收"，两者症状都是"验收假绿"。

---

## 38. 外层放行了、内层又拦死 —— 别只改调用链上的第一处判断 ★（Batch 7 前置修正实测）

**现象**：给试卷做软删除后，`GET /admin/exams/{id}` 依然返回 `40401 试卷不存在`。
而直连库用**完全相同**的 SQL 查，行明明在（`is_deleted=true`）。
更迷惑的是：`_load_exam_row()` 里**没有任何** `is_deleted` 过滤，它只在 `row is None` 时 404。

**根因**：404 是**另一个函数**抛的。

```
GET /admin/exams/{id}
  └─ exam_service.get_exam_detail()
       ├─ _load_exam_row()          ← 我改的这里（已放行归档卷）
       ├─ _ensure_subject_visible()
       └─ validate_exam()           ← ★ 它也有一份 if row["is_deleted"]: raise not_found
```

`get_exam_detail` 为了把校验结果内联进详情（前端详情页要显示 error/warning），
会**在内部调用** `validate_exam()`。而 `validate_exam` 自己抄了一份"归档就 404"的判断
—— 我只删了外层那一份。

**为什么只读函数不该持有比读接口更严的准入**：
`validate_exam` 是**只读**的（不改任何数据），而且它是**只读接口的依赖**。
一个只读的、被读取路径复用的函数，夹带"归档即不可见"的准入条件，
就会让调用方怎么改都改不动 —— 上层放行、下层拦死，症状还指向错误的层。

**查法**（两条都用上，才定得准）：

1. **grep 同类断言**，而不是只看报错那个函数：
   ```bash
   grep -n "is_deleted" app/services/exam_service.py     # 看看一共几处
   ```
   再定位每一处所属的函数：
   ```python
   # 用最近的 def 反查某一行属于哪个函数
   ```
2. **看访问日志里的状态码分布**：同一个接口对"未归档"返回 200、对"已归档"返回 404，
   说明**不是**路由/参数问题，而是被业务判断拦了（业务码 `40401` 会映射成 HTTP 404）。

**教训**：**改"谁能看见 / 谁能操作"这类判断时，先把调用链上所有同名检查都找出来。**
读路径上的准入条件应当只写一次（或至少只写在最外层）。
写操作（compose / update / publish）继续保留"归档不可改"是**对的** ——
区别在于它们是**写**。

---

## 39. 改了 `db/schema.sql` 对**已有数据的库**不生效 ★（Batch 7 前置修正实测）

**现象**：给 `exam_questions` 加了 `locked_version` 列、给 `viewer` 补了 `exam:read`，
只改了 `db/schema.sql` —— 本地跑验收**全绿**，但线上/老库上接口报"列不存在"。

**根因**：`run-smoke.ps1` 的建表步骤是**条件执行**的：

```powershell
$r = Invoke-Psql -PsqlArgs @("-d", $DbName, "-tAc", "SELECT to_regclass('public.users')")
if ([string]::IsNullOrWhiteSpace($r.Text)) {   # ← 只有**首次建库**才载入 schema.sql
    ... -f db/schema.sql
} else { Write-Host "表已存在，跳过建表" }
```

库已存在 → 整段跳过 → **schema.sql 里改了什么都白改**。

**修法（本次已落地）**：

1. 新增 `db/migrations/` 目录，放**幂等**迁移脚本
   （`ADD COLUMN IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），命名按日期排序：
   ```
   db/migrations/20260917-01-locked-version-and-viewer-exam-read.sql
   ```
2. `run-smoke.ps1` 新增 **3.5 步**：按文件名排序、逐个 `psql -f` 执行 `db/migrations/*.sql`。
   因为脚本自身幂等，可以无条件重跑。

**教训**：**"改 schema" 和 "改建表脚本" 是两件事。**
只要项目里有任何一个"表已存在就跳过"的建表逻辑，就必须同时有迁移通道 ——
否则新结构只在新库上存在，而那正是验收环境，**问题会在生产才爆**。

**顺带一个断言习惯**：迁移里的**回填**逻辑不能只靠"跑一遍没报错"来验收。
本批专门造了一行"迁移前"的数据（JSONB 有锁、列是 NULL）→ 跑迁移 → 断言列被填成正确值
→ **再跑一次**断言幂等。回填写错的代价是"历史数据静默残缺"，比报错严重得多。

---

## 40. 弱引用 + 软删除 = 悬空引用**没人拦**，恢复/启用前必须自己校验 ★（Batch 7 恢复接口）

**现象**：把章节软删除（`chapters.is_deleted=true`）之后，挂在它下面的题目**毫无异常** ——
数据库不报错、接口不报错。直到有人点"恢复题目"，这道题就出现在了一个**已经不存在的章节**里。

**根因**：两个机制叠在一起，刚好把守卫都绕开了。

| 机制 | 后果 |
|---|---|
| `questions.chapter_id` 是**弱引用** —— 只有 `REFERENCES chapters(id)`，**没写 `ON DELETE` 行为** | 数据库不会级联、也不会拦 |
| 章节/知识点用的是**软删除**（`is_deleted=true`），不是 `DELETE` | **外键根本没被触发** —— 行还在，"引用完整性"在数据库眼里是成立的 |

于是"章节没了但题目还指着它"这个状态，从数据库的视角看**完全合法**。
这不是 bug，这是**软删除 + 弱引用的固有代价**：**参照完整性得由业务代码自己兜**。

**修法（本项目已落地）**：所有"把对象从删除态拉回来"的入口，都要先校验关联数据：

```
restore_question  →  chapter_id 还在且未删？knowledge_point_id 还在且未删？
                     知识点**所属章节**还在且未删？（knowledge_points.chapter_id 是必填）
restore_exam      →  所属科目 status 还是 'on'？
                     已发布的卷，卷面题目有没有被归档？
```

不满足就 `40901` + **说清是哪一条** + 给可执行的下一步，
**绝不静默恢复到一个不成立的状态上**。

**判断哪里需要加这种校验**：问一句"**这个对象依赖的东西，会不会在我看不见的地方被单独删掉？**"
- 依赖的是**必填外键 + 硬删除** → 数据库帮你兜，不用管
- 依赖的是**可空弱引用**（`chapter_id` / `knowledge_point_id`）或**软删除** → **自己兜**

**同类隐患**（同一原则，将来写的时候留意）：
- "启用"停用的科目/章节时，要不要检查父节点还在不在
- 把题目改挂到某个章节时，校验目标章节是否已删
- 归档一份卷之后，它引用的题目被删 —— 恢复试卷时已按这条拦住了

---

## 41. "幂等"的判据是**目标状态已达成**，不是"这次操作没发生" ★（Batch 7 恢复接口）

**背景**：恢复接口（`restore`）要求幂等 —— 已经被恢复的对象再调一次，应当返回成功。

**两种写法，差别很大**：

| 写法 | 已经未删除时 | 问题 |
|---|---|---|
| ❌ 沿用"删除"的套路 `if not is_deleted: raise bad_request(...)` | 报错 | 前端重试、或批量恢复里混进一道没删的题，就得专门写容错；**"我要它没被删"这个诉求其实已经满足了** |
| ✅ 返回 `code=0` + `already_active=true` | 成功 | 调用方只需看 `already_active` 决定要不要提示"无需恢复" |

**关键**：幂等的定义是"**同样的请求执行一次和执行多次，产生的状态相同**"，
而不是"第二次不能报错"也不是"第二次必须什么也不做"。
**目标状态已达成 → 就是成功**（HTTP 语义里的 `PUT` 就是这个意思）。

**两个必须一起想清楚的细节**：

1. **幂等分支绝不能有写入副作用** —— 特别是**不要动 `version`**。
   本项目的题目恢复会让 `version + 1`（恢复是一次内容变更），
   但如果走幂等分支还 +1，重试几次版本号就飙上去了，乐观锁会立刻误判冲突。
   用例直接断言了这点：`again["data"]["version"] == 3`（幂等分支不该再 +1）。
2. **给调用方一个"区分信号"** —— 返回 `already_active: true`。
   否则前端没法决定是该弹"已恢复"还是"无需恢复"，只能统一弹个含糊的 toast。

**反例（Batch 4 的软删除就是"不幂等"的，而且是**对的**）**：
`DELETE` 一个已经删掉的题返回 `40001 已经是删除状态`。
因为"删除"的语义是**一次变更**，重复执行说明调用方状态认知有问题，
早点报出来比默默吞掉更好。**同样两次调用，一个要报错一个不能报错 —— 取决于语义，
不是取决于"要不要图省事"。**

---

## 42. 一个"看起来很无害"的编辑接口，其实会**删掉关联数据** ★（Batch 7 Pass 2a）

**现象**：`PUT /admin/exams/{id}` 只是想改考试时长/标题，顺手把详情页拿到的
`sections` 一起回传了 —— 结果**卷面 40 道题全没了**。接口返回 200，没有报错。

**根因**：`update_exam` 收到 `sections` 时会先删后建：

```python
if payload.sections is not None:
    await db.execute(text("DELETE FROM exam_sections  WHERE exam_id = :eid"), ...)
    await db.execute(text("DELETE FROM exam_questions WHERE exam_id = :eid"), ...)   # ★
    for idx, s in enumerate(...):
        await _insert_section(db, exam_id, seq=idx + 1, section=s)
```

语义上是对的（**分段是卷面的骨架，重建骨架必然要重排题目**），
但 `ExamUpdateIn` 里 `sections` 只是**其中一个可选字段**，
和 `title` / `duration_min` 并列 —— 从签名上完全看不出它有这么重的副作用。

更阴的是**前端很容易顺手回传**："详情页的数据是 `ExamDetail`，
`PUT` 的入参是 `ExamUpdateIn`，直接把详情对象丢进去最省事"。
而详情对象里**恰好就有 `sections`**（还带着每段的 `questions`）。

**修法（分两步，第二步才是正解）**

**第一步（当时的应急措施）**：前端"提交前构造 payload，绝不把读到的对象原样回传"，
破坏性入口单独拆出去 + 显式勾选确认。
→ **这只是约定。** 一个人忘了，就再删一次整卷。

**第二步（正式修法，已落地）—— 把"不可能"写进结构**：

| 手段 | 内容 |
|---|---|
| **拆接口** | `sections` 从 `PUT /admin/exams/{id}` 上剥离，单独走 `PUT /admin/exams/{id}/sections` |
| **禁未知字段** | 元数据接口 `model_config = ConfigDict(extra="forbid")`，多一个字段就拒 |
| **针对性拒绝** | 传 `sections` 命中专门的分支 → `40001` + **可执行的错误信息**（告诉你该用哪个接口） |
| **显式确认** | `/sections` 必须传 `expected_question_count`（你读到的当前卷面题数），对不上就 `40901` |
| **diff 兜底** | 任何写路径若让卷面题数**净减少超过阈值** → 拒绝，并给出该传的参数值 |

其中 **`expected_question_count` 一箭双雕**：既是防误操作，又是**乐观并发** ——
两人同时改一张卷，后提交的必然对不上，不会把前一个人刚加的题默默清掉。

**diff 兜底为什么必要**：拆接口只防住"调用方**故意**改结构"，
防不住**副作用** —— `auto-compose` 传 `replace=true` 时也会先清空再重建，
而它的语义是"重新抽题"，调用方未必意识到这会删掉手里已有的题。
所以兜底**不看调用方想要什么，只看实际发生的结果**：操作前后各数一次卷面行。
并且**必须在 `commit` 之前判定** —— 抛异常 → 会话回滚 → 一行都没写；
放到 commit 之后就只能"删了再报错"，那是不可逆的。

**教训（这条比坑本身更重要）**：

> **破坏性的副作用不能靠约定防，要做结构防护。**
> 当你想写"这里要注意不要传 X"的时候，那说明**接口设计有问题** ——
> 正确的做法是让 X **传不进来**（禁字段 / 拆接口），
> 而不是在文档里加一条"请注意"。文档会被跳过，编译器/校验器不会。

**判断哪里该做结构防护**：问一句"**如果调用方多传了一个字段，会发生什么？**"
- 忽略它 → 无所谓；
- 覆盖它 → 用 `extra="forbid"` 挡掉；
- **删掉一堆别的数据** → 这个字段**根本不该出现在这个接口上**。

**同类隐患**（同一原则，将来写的时候留意）：
- `POST /admin/questions` 建题接口的 `options` 会不会"先删后建"；
- 导入回滚（`question:rollback`）能一次撤掉整批 —— 有没有让调用方
  说清"撤的是哪一批、多少行"；
- 任何带 `replace` / `overwrite` / `reset` 字样的入参。

---

## 43. 开关式列表里"写后失效缓存"会让刚完成的行**原地复活** ★（Batch 7 Pass 2a）

**现象**：归档/恢复的"就地反馈"做得好好的 —— 行标记「已恢复」、淡出、3 秒后移除 ——
但一加上 `invalidateQueries(["exams"])` 就全乱了：标记完的那一行**立刻原地复活**，
标记被冲掉，3 秒后也不消失。

**根因**：这类列表的开关语义是**「放宽范围」而不是「缩小范围」**：

```
GET /admin/exams?include_deleted=true
  → 返回"所有的卷"（含已归档的），而不是"仅已归档的"
```

所以恢复之后那一条**本来就还在结果集里**（它只是不再是"已归档"而已）。
一旦重新拉取，本地"我把它标记成已完成、准备移除"的意图就没了依据 ——
服务端说"它还在"，UI 就得把它画回来。

**修法**：**就地反馈期间不要失效这个列表的缓存。**

- 归档 / 恢复这类**行级**操作：`onSuccess` 里只失效
  `["exam", id]` 与 `["audit-logs"]`，**不动 `["exams"]`**；
- 用**本地状态**表达"本次会话里这一行完成了/不显示了"（`useRowActionFeedback` 的
  `dismissed` 集合）；
- 在**用户主动离开当前视图**时清掉本地状态（翻页、改筛选、手动刷新），
  把控制权交回服务端真相 —— 并**在表外写明**："本次已就地处理 N 条并从当前视图隐藏，
  翻页或改筛选后恢复显示"。**说了，用户就不会以为数据丢了**；
- **组卷、加题、移题**这类"整卷变了"的操作不受此限 —— 它们不是行级的，
  正常失效列表与详情。

**教训**：**"放宽范围"的开关和"筛选条件"不是一回事**，
它在缓存层面也一样 —— 失效之后行不会消失，只会"回到它本来的样子"。
就地反馈是**视图级意图**，就得用视图级状态去表达，不能指望服务端帮你隐藏。

---

## 44. `npm run lint` 是个**假门禁**：脚本在，配置不在 ★（Batch 7 Pass 2a）

**现象**：`package.json` 里有 `"lint": "next lint"`，CI/交付清单里也写着"跑 lint"。
实际执行会**卡在交互式提问**上：

```
? How would you like to configure ESLint? https://nextjs.org/docs/basic-features/eslint
❯  Strict (recommended)
   Base
   Cancel
```

因为仓库里**根本没有 eslint 配置**（无 `.eslintrc*`）。在无人值守的脚本里，
它会一直等输入；在终端里，开发者为图快随手选一个，就**悄悄生成了不在评审范围内的配置**。

**它为什么危险**：这类"**脚本存在但不可用**"的门禁比"没有门禁"更糟 ——
交付清单上打了勾，实际从没检查过。同类信号还有：
`test` 脚本指向不存在的目录、`typecheck` 目标为空、CI 里的 `|| true`。

**当前处理**：本批**只记录不修**（改 lint 配置会引入一次大范围风格变更，
与"跨批次契约审计"是同一类工作，应当独立成批）。在此之前：

- 交付清单里**别再写"已跑 lint"**；实际执行的是 `tsc --noEmit`（这个是真的会跑）；
- 想启用时按 `next lint` 的引导生成配置，并把**首次全量结果单独提交**，
  免得和业务改动混在一起没法 review。

---

## 45. 夹具只会"删"，就会悄悄把**共享数据**打出洞 ★（Batch 7 fix-42 时发现）

**现象**：改了 `PUT /admin/exams/{id}` 的接口后跑全量冒烟，
**`test_admin_v5.py::test_seed_bank_mapping_matches_existing_rows` 挂了**：

```
期望 300 行为 duplicate（说明全部命中库内已有题），实际 duplicate=299 success=1
```

一个**完全没碰过导入管道**的改动，却让导入的幂等验收失败。

**根因**：不是导入管道的问题，是**种子题库被打出了一个洞**。

- `test_admin_v7.py::test_exam_restore_rejects_published_with_archived_questions`
  要构造"已发布的卷里有题被归档"这个场景 ——
  它 `compose` 一张卷（**抽的是共享的种子题**，因为规则不限定来源），
  然后把卷面第一道题**软删除**当"违规样本"。
- 它把这道题登记进了 `created.questions` —— 而那个清单的清理动作是
  **`DELETE /admin/questions/{id}`（软删除）**。
- 于是清理不仅没有还原，反而**确认了这次破坏**：种子题被永久软删除。

**为什么它当场不报错、还能连过好几次**：

1. 受影响的是**另一个文件**的用例（`v5` 的种子映射），
   而不是当事用例 —— 破坏者自己不疼；
2. 同一次 run 里 **v5 在 v7 之前跑**，当时数据还是干净的 → v5 通过，
   v7 才动手删。所以**只有下一次整体 run 才会暴露**；
3. 组卷是 `seed=42` 确定性抽题，但**候选题集在缩小** ——
   每跑一次就删掉一道，下一次抽到的是**另一道**。
   于是这 12 次运行各删一道，**慢慢从"样本之外"漂进了"样本之内"**，
   直到某一道落进 v5 取的前 300 条里，才终于炸出来。
   （实测数据库里已累积 **12 条**被误删的种子判断题，时间跨度 09-17 ~ 09-18。）

**修法**：夹具区分**两种语义**，而不是只提供"删"这一个动作。

| 清单 | 语义 | 清理动作 |
|---|---|---|
| `created.questions` | **本用例自己建的**脏数据 | 软删除 |
| `created.borrowed` | **从共享数据借来的**（种子题库） | **恢复原状**（`/restore`），绝不删 |

当事用例改成 `created.borrowed.append(victim)`。
另：验收脚本（`tools/local-verify/seed-*.py`）同理 ——
它原本"挑一道种子题改题干"来演示版本漂移，而**改题干会改内容指纹**
（`questions.content_hash`），效果和删掉一样（那道题不再命中种子行）。
已改成**自己造一道题**来演示。

**教训（可以推广的两条）**：

> **1. 夹具只提供一种清理动作，就是在诱导后来的人破坏共享数据。**
> 建夹具时先问："这个数据是我造的，还是借的？" 借的必须能还原。
>
> **2. 破坏共享数据的故障，往往在"另一个文件的用例"和"下一次运行"才现形。**
> 所以整套用例能连过几次**不代表**没问题 —— 修完这类坑，
> 要**连跑两次**全量，第二次才是真正的判据。

**同类隐患**：任何"为了构造场景而改共享数据"的用例 ——
改分数的、改状态的、改归属的。判据一样：**改完要能还原，且还原动作是对的语义**。

---

## 46. 拿异步结果当闸门，就必须**等结果回来才放行** ★（Batch 7 Pass 2b）

**现象**：规则编辑页做了一个 real-time 试算（dry-run，600ms 防抖 + 一次请求）。
保存时**若试算显示"题库不足"，就弹二次确认**。
看起来没问题 —— 但实测：E2E 脚本改完题数**立刻**点保存，
**缺口确认框一次都没出现**，规则照样写进了库。

**根因**：闸门是这么写的：

```ts
const gap = preview.data ? !preview.data.ok : false;
onClick={() => (gap ? setConfirmOpen(true) : void submit())}
```

`preview.data` 在**试算还没回来时是 `null`** —— 于是 `gap === false`，
代码走了 `submit()` 那条路。**"还没结论"被当成了"结论是通过"。**

这类 bug 的可怕之处在于**它只在慢的那一瞬间出现**：
手工点的时候大概率试算已经回来了（人对 600ms 不敏感），
只有脚本/快速操作才会稳定复现。所以它**很难在人工验收里被发现**。

**修法**：把"等结论"做成**显式状态**，并直接落到按钮的禁用上：

```ts
const awaitingVerdict = canPreview && !preview.data && !preview.error;
<Button disabled={formBad || saving || awaitingVerdict} ...>
```

并在旁边写清为什么禁用（"正在试算，等结论出来才能保存"），
而不是让用户点下去以后才发现"怎么没反应"。

**教训（可以推广的一条）**：

> **三态，不是两态。** 任何"用异步结果决定要不要拦"的地方，
> 状态都是 **待定 / 通过 / 不通过** —— 把"待定"合并进"通过"就等于**开了后门**；
> 合并进"不通过"又会**误拦**。必须单独处理，并且**优先选择"等"而不是"猜"**。

**同类隐患**（同一个模式，将来写的时候留意）：
- 保存前的"重名检查""配额检查"是异步的 → 检查没回来就提交；
- 删除前的"是否被引用"检查；
- 支付/下单前的"库存校验"。
判据一样：**这个判断的结果没回来时，提交会不会绕过去？**

---

## 47. 行级操作里，"失效列表"和"就地反馈"是**互斥**的 ★（Batch 7 Pass 2b）

**现象**：规则列表的"删除"做了就地反馈（标记「已删除」→ 3 秒后从视图移除）。
但实测**标记从来没出现过** —— 点了删除，那一行直接没了。

**根因**：`useDeletePaperRule` 的 `onSuccess` 里有一句
`invalidateQueries(["paper-rules"])`。于是：
服务端删除成功 → 缓存失效 → **立刻重新拉取** → 列表里已经没有这条了 →
**行在标记出现之前就消失了**。

**这不是新坑，是坑 43 换个马甲又犯了一次。**
坑 43 说的是"开关式列表里写后失效缓存会让行原地复活"，
这里是"删除场景下写后失效缓存会让行**提前消失**" ——
**方向相反，成因完全相同：行级操作的"视图意图"被服务端的重新拉取冲掉了。**

**修法**：行级操作**一律不失效当前列表**（`dismissed` 集合是视图层的真相），
需要的话只失效**不在当前视图里**的缓存（审计日志、详情、列表之外的东西）。

那"缓存不就脏了"？**不会**：`dismissed` 只在当前视图生效，
用户一翻页/改筛选就 `reset()` 并重新拉取，届时服务端的真相自然生效
（删除的行本来就没了，不会回来）。
**所以失效与否对最终结果没影响，只影响用户能不能看到反馈。**

**判据**（一句话就能判断该不该在这里 `invalidateQueries`）：

> **这个操作结束后，那一行"应该还在列表里"还是"应该走了"？**
> - 应该走了（删除 / 归档 / 恢复）→ **别失效**，交给就地反馈；
> - 应该还在但状态变了（启用 / 停用 / 改名）→ **可以失效**，让它刷新出新的状态。

**为什么容易反复犯**：`invalidateQueries` 是"正确性直觉"的默认动作 ——
改完数据就该刷新，听起来永远对。但**在行级操作里，它和"给用户看反馈"是抢时间的**。
所以这类操作要有意识地把"刷新"这一步让给反馈机制。

---

---

## 48. 「列表过滤」防的是"翻到"，不是"猜到" ★（数据范围收口）

**现象**

`researcher` 挂了 `subject: 2007`（市政）范围。打开题库列表，只看得到市政的题 ——
看起来数据范围生效了。但把地址改成 `/questions/<一个建筑实务的 id>`，
详情页**正常打开**；点「编辑」能改，点「删除」能删。

**根因**

收口前只有 `list_questions` 走了 `apply_data_scope`。其余七条入口
（详情 / 新建 / 编辑 / 删除 / 批量删除 / 恢复 / 两个下拉）虽然**都收到了**
`actor: ScopeViewer`，**却一次都没读过它** —— 形参在签名里躺着，等于没有。

**为什么"列表过滤"不算防护**

列表过滤防的是"**翻到**"（靠浏览撞见别科目的题），防不了"**猜到**"（知道 id 就直达）。
这是两个不同的攻击面。更糟的是：**列表过滤会给人一种"范围已经生效"的错觉** ——
验收时看一眼列表是干净的，就以为收口做完了。

> **判据：凡是按 id 寻址的入口，都必须自己再拦一次。**
> 问的不是"有没有做过滤"，而是"**能不能不经过列表直接到达这个对象**"。

**解法：把闸落到唯一一处，所有入口复用**

```python
# question_service.py —— 与 scope_subject_ids 并排，同一份判定
def ensure_subject_visible(viewer, subject_id, what="该科目") -> None:
    allowed = scope_subject_ids(viewer)
    if allowed is None:          # global：不限制
        return
    if subject_id not in allowed:
        raise forbidden(f"{what}不在你的数据范围内（当前账号只被授权了 N 个科目）", 40301)
```

顺带把 `exam_service` 里那份**自己实现的副本**（`_ensure_subject_visible` /
`_scope_clause`）删掉、改成别名 —— 否则"唯一来源"只是口号，两份迟早漂。

**三个容易做错的细节**

1. **闸要排在"状态类"判断之前**。`restore` 的幂等分支（"本来就没删" → `200` +
   `already_active=true`）若排在范围闸之前，越权者能靠它拿到 **200** ——
   等于用返回值确认了对象存在。**授权判断必须先于任何业务状态披露。**
2. **编辑要拦两个科目**。`subject_id` 是可改字段：只拦"当前所属"，
   就能把题**搬进**一个自己没被授权的科目，等于绕过新建时那道闸。
3. **写路径的闸要在"任何写库动作之前"**。新建时先建再报错 = 越权请求也落了库。

**为什么报 `40301` 而不是 `40401`**

对象**确实存在**，只是不归你。报 404 会让排查的人以为 id 写错了，去找一个并不存在的问题。
（与坑 38 / 硬约定 A 划清界限：那条讲"只读函数**夹带**比调用方更严的准入"；
本条是**准入判据本身**，读路径与写路径**共用同一份**，不会出现上下层不一致。）

**落点**：`question_service.ensure_subject_visible` / `scope_clause`；
八条入口各一处调用；用例 `test_admin_v4.py::test_scope_*`（8 条，含对照组）。

---

## 49. 批量入口不能把"越权"降级成 `skipped` —— 那会让批量比单条**更容易绕过** ★

**现象**

`batch-delete` 的既有语义是「已删除 / 不存在的进 `skipped`，不报错」。收口时按同样思路
把"不在数据范围内"也塞进了 `skipped`。变异验证时拿到：

```json
{"deleted": 2, "skipped": []}      ← 越权的那道题被一起删掉了
```

**根因**

`skipped` 的语义是"**目标状态已达成 / 对象不存在**"—— 那些情况下继续处理其余几条是合理的。
**越权不属于这一类**，它是"你根本不许动"。把两者并成一类，就用一个"更友好"的返回
换掉了一次拦截。

**为什么"更友好地跳过"是错的**

1. **批量入口会变成比单条入口更弱的闸**。单条删返回 `40301`，批量删只给一句不起眼的
   `skipped` —— 越权者只要改走批量接口就绕过去了。**静默忽略在攻击面上等于放行。**
2. **越权尝试会被当正常操作写进审计日志**。`after.ids` 里只有真正删掉的那些，
   越权的消失在 `skipped` 数组里；审计员按 `action='question.batch_delete'` 查，
   看到的是一次**成功的批量删除**。

> **判据（就是硬约定 C 那条）：要不要"放过"某个元素，看目标状态是否已达成。**
> 越权不是"已达成"，是"不许" → **整批拒绝、零副作用、说清是哪几条。**

```python
if outsiders:
    raise forbidden(
        f"有 {len(outsiders)} 道题不在你的数据范围内（{preview}），**整批未执行**。"
        f"当前账号只被授权了 {len(allowed)} 个科目，请从选择里去掉它们后重试。", 40301)
```

文案里"**整批未执行**"不能省 —— 调用方看到 `40301` 还得知道**别的东西有没有被改动**。

**落点**：`question_service.batch_soft_delete`；用例
`test_admin_v4.py::test_scope_batch_delete_is_all_or_nothing`（断言了两道题都**没被删**）。

---

## 50. 同一文件并行发两条 `Edit`，会**互相覆盖**，而工具两条都报成功 ★

**现象**

一次给同一个文件连发两条 `Edit`（一处加 import、一处插函数），**两条都回
「Successfully edited」**。回读发现**只落了一条** —— 另一条被静默冲掉了。

更坏的是"半落"：`list_chapter_tree` 的**调用处**改成了引用 `scope_sql`，
而**定义 `scope_sql` 的那段没落** → 文件语法通过、`import` 通过、OpenAPI 生成通过，
**直到请求真的打进去才 `NameError`**。

**根因**

两条 `Edit` 各自"读全文 → 替换 → 写全文"。**并行发出时，后写的那次覆盖了先写的。**
工具报的是"我成功写了一个文件"，**不是**"这个文件的最终状态满足两条意图" ——
所以两条都返回成功，谁也没骗你，但结果仍是错的。

**这是老坑 33（`Edit` 偶发静默失败）的近亲，但触发条件更明确**：
不是随机失败，而是**只要并行写同一文件就必然互相覆盖**。

**解法 / 判据**

- **同一个文件的多处修改，永远串行发** —— 一条回读确认后再发下一条。
- 改完**不要只看工具回显**：`grep -c` 数一数关键符号的命中次数，或直接 `compileall`。
- 编译通过**不足以**证明改动落全了（这正是不足之处：`scope_sql` 是**局部变量**，
  `compileall` 与 `test_new_modules_have_no_undefined_globals`（只查 LOAD_GLOBAL）
  都看不见它）。
- **变异验证能兜住**：把唯一的判定点（`scope_subject_ids` 改成恒返回 `None`）打掉，
  看新用例是否真的变红。本次实测 8 条里 7 条变红（第 8 条是对照组，本就该常绿）——
  这一步同时验证了"测试在守门"和"守卫没落全"。

**落点**：本次收口踩到；`question_service.py` 的 `get_question_detail` /
`batch_soft_delete` 守卫与 `list_chapter_tree` 签名都各丢过一次。

---

---

## 51. 门禁脚本在**开发机上绿**，不等于它在**干净机器上能绿** ★（门禁整顿第一步）

**现象**

`tools/local-verify/run-smoke.ps1` 是这个项目"第 1 优先"的验收手段，本地跑过很多轮、
每轮都是 `126 passed, 1 skipped`。第一次把它接到 CI（在**全新 PostgreSQL** 上跑同一链路）时：

```
32 failed, 91 passed, 4 skipped
```

**根因**

`db/schema.sql` 只灌 `subjects` / `chapters` / RBAC，**不含 `questions` 与 `knowledge_points`**。
题库来自 `db/seed/gen_seed_questions.py` 生成的 `data/seed/questions.sql`，
而 `data/` 是 **gitignored** 的（设计上"可复现所以不入库"）。

`run-smoke.ps1` **从来没有这一步**。它之所以一直绿，是因为**开发机的库里早先被手工灌过
种子题**（9832 道）—— 脚本在裸机上根本跑不通，只是从来没人试过。

**为什么这条比"脚本报错"更危险**

它给出的是**假绿**：门禁说的是"通过"，而不是"失败"。
而且这份假绿**只在"环境恰好脏"时成立** —— 换台机器、或把库清掉，立刻 32 条红。

这和坑 17（`.next` 抢锁导致页面不 hydrate）、坑 44（`npm run lint` 没配置）是**同一族**：

> **门禁的存在感很低 —— 直到你需要它挡一次。** 而它挡不住的那一次，代价最大。

**判据**

> 一个门禁可信，当且仅当它在**从零开始的环境**里能给出同样的结论。
> 问法：**"如果把 `node_modules` / 数据库 / 缓存全删掉，它还能不能绿？"**

**解法（本次只做 CI 侧）**

```bash
python db/seed/gen_seed_questions.py --format sql --out data/seed   # 0.29s，自包含、离线、可复现
psql -f data/seed/questions.sql                                     # ON CONFLICT 幂等，约 5s
```

`questions.sql` 自带 `INSERT INTO knowledge_points` + `INSERT INTO questions`，
灌完是 **6000 题 / 20156 选项 / 52 知识点**。

**✅ 已修（2026-09-20 当天，同一批收尾）**

生成 + 灌库抽成了 **`tools/local-verify/seed-questions.py`**，
`run-smoke.ps1` 与 CI **调的是同一个脚本**。

> **为什么一定要抽成脚本，而不是在两处各写两行命令** —— 修这条坑时最容易犯的错
> 就是"CI 与本地各写一套"，那两套迟早漂，于是又回到"本地绿、CI 红"的口径分歧，
> **也就是这条坑本身**。所以实现只有一份。

抽成脚本时另外做了两个决定：

1. **刻意不做"库里已有 6000 题就跳过"的快捷判断** —— 那正是假绿的成因：
   "环境恰好 dirty"时跳过 → 行为与干净环境不同（而且它只看 `questions`，
   漏了 `knowledge_points` 为空的情况）。SQL 自带 `ON CONFLICT`，无条件重跑约 5s，
   **"自包含可复现" 值这 5 秒**。
2. **脚本自带自查**：灌完立刻断言 `questions >= 6000` 且 `knowledge_points > 0`，
   不满足就非 0 退出 —— 别等到 pytest 才红 32 条。

**验收方式（照硬约定 H 来的）**：把数据库整个 `DROP DATABASE yijian WITH (FORCE)` 掉，
再跑 `run-smoke.ps1` —— 从零库仍然 `126 passed, 1 skipped`。

**顺带记住**：`data/` 整体 gitignored，所以 CI 与裸机**必须自己生成种子**，
不能假设库里已经有题。

---

## 52. 覆盖率门禁的两个陷阱：**量错进程**，和**门槛定在达不到的地方** ★（门禁整顿第二步）

2026-09-20 加覆盖率门禁时，连着踩了两个 —— 一个让**数字失真**，一个让**门禁失效**。
两个都是"看起来很对"的做法。

### 陷阱一：在 pytest 进程里量 `--cov=app`

本项目的用例是**通过 HTTP** 打到独立 uvicorn 进程的
（`tests/conftest.py` 里就是个普通 `httpx.Client`，`base_url` 来自 `AI_BASE`）。
于是 `pytest --cov=app` 量到的只是**测试自己导入的那几个纯函数模块**
（`lib/idgen` 之类），业务代码一行都不在里面 —— 数字会低到没有参考价值。

**判据：问"被测代码到底跑在哪个进程里？"** 答案不是"跑 pytest 的那个"。
本项目必须**在 API 进程里采**：

```bash
serve_fake_redis.py --coverage --cov-data-file <repo>/.coverage \
                    --shutdown-file <tmp>/yijian-cov-stop
```

### 陷阱一之半：采集了，但**数据没落盘**

`coverage` 靠进程正常退出来写数据文件（`atexit`）。而 `run-smoke.ps1` 原来收尾用
`Stop-Process -Force` —— **硬杀**，进程直接消失，`atexit` 不跑 → **一个字都写不出来**。
更坏的是：报告会把"没有数据"显示成 **0%** 而不是报错 —— 又一个假绿
（与坑 51 同族：给的是"通过/一个数字"，而不是"失败"）。

**修法**：不用信号（Windows 上给别的进程发不了 SIGTERM，且 `signal` 只在 main 线程生效），
改用**哨兵文件** —— 外部放一个文件，进程自己收尾写盘。收尾后再断言数据文件**非空**，
空就硬失败。同时在日志里留一行 `[cov] data saved to ...` 作为"真的写盘了"的凭据。

### 陷阱二：门槛 = "实测值 + 5~10%"

这是最初的需求原文。但实测是 **65.64%**，+5pp = **71% > 65.64%** ——
门槛一落地 `coverage report` 就必然非 0 退出，`run-smoke.ps1` 不再全绿、CI 白不起来。

**两条要求在数学上不能同时成立**：`门槛 = 实测 + 5` 与 `门禁现在能过` 互斥。
而"恒红的门禁等于没有门禁"是已经定过的判据（硬约定 H）——
所以取"门禁必须真能跑"，把门槛设在**实测值下方一点点**（65%），当**棘轮**用：
**任何让覆盖率掉 1 个点以上的改动都会红**，这才是棘轮该起的作用。

> **给下一次的通用做法**：先量出实测值 V，门槛取 **V 下方 0.5~1pp**（留抖动余量），
> 再把"V + 5~10"记成**目标值**，写进配置文件并说明"达到后提到 XX"。
> 想一步到位把门槛定在未来值，等于给自己装一个永远红着的灯 ——
> 它的唯一结局是被忽略，然后被删掉。

**覆盖率数字要有依据、可复核**（本次的完整依据写在 `.coveragerc`）：

```
schemas  925/14  →  98.5%        services  2286/1153  →  49.6%   ← 大头
db       152/10  →  93.4%        (顶层)     222/192   →  13.5%   ← cli/main
core     398/59  →  85.2%
api      322/51  →  84.2%        TOTAL     4305/1479  →  65.64%
```

### 顺带：门槛的**单一来源**

`fail_under` 只写在仓库根 `.coveragerc` 的 `[report]` 里。`coverage report`
会自动读它，低于门槛就以非 0 退出 —— **CLI 与本地脚本都不再重写一遍数字**。
两处各写一份的话，改门槛时必然漏一处，然后出现"本地过、CI 红"。

### 复查清单

- [ ] 覆盖率是不是在**业务代码真正运行的那个进程**里采的？
- [ ] 采集进程是**正常退出**的吗（数据文件非空 + 日志里有落盘凭据）？
- [ ] 门槛值**现在能过**吗？（起一个"恒红门禁"比不加门禁更糟）
- [ ] 门槛数字有**实测依据**吗？写在哪个文件里？
- [ ] 门槛是不是**只有一个来源**（本地与 CI 读同一份配置）？
- [ ] 起一个真的越界值验证门禁**真的会红**（变异验证，硬约定 G 的配套动作）

---

## 53. **"能被人读到"≠"能被工具读到"**：往结构化文件里塞注释，等于把它对工具废掉 ★

接入 prettier 时它直接报错：

```
[error] components.json: SyntaxError: Unexpected character '，'. (2:36)
[error]   1 | <!-- shadcn/ui 配置。版本锁 v3 的 tailwind。
[error] > 2 |      本项目没有用 `npx shadcn@latest add`，组件是按官方 v3 结构手写进 src/components/ui 的，
```

`apps/admin/components.json` 顶部是一段 `<!-- -->` **HTML 注释** ——
而 **HTML 注释既不是合法 JSON、也不是合法 JSONC**（JSONC 只认 `//` 与 `/* */`）。
用 `JSON.parse` 验证（**这正是 shadcn CLI 读它的方式**）：

```
JSON.parse 失败 → Unexpected token '<', "<!-- shadc"... is not valid JSON
```

也就是说：**这份配置对它自己的用途是坏的**，而且**已经坏了很久**。

### 为什么一直没人发现

**因为没有任何东西会因此报错。** 本项目没有真的用过
`npx shadcn@latest add`（组件是按官方 v3 结构手写进 `src/components/ui/` 的），
所以那份配置**从来没有被读过** —— 一个"写来给以后用"的文件，
坏掉了也不会有人知道，直到某天真的去 add 一个组件。

### 判据

**往结构化文件（JSON / YAML / TOML / XML schema）里塞注释之前，先问：
"读这个文件的那个工具，认不认注释？"**

- 认（`tsconfig.json` 走 jsonc 解析、`ruff.toml` 认 `#`）→ 写，而且**应该写**
- 不认（`package.json`、`components.json`、多数 schema 校验器）→ **说明搬到别处**，
  通常是同一目录的 README

### 修法与教训

改成纯 JSON，说明搬进 `apps/admin/README.md`。

> **"能被人读到"和"能被工具读到"是两件事。**
> 一段注释让你多知道一件事，代价是**那个文件对工具彻底失效** ——
> 而且失效的方式是"静默"的：没有编译错误、没有测试失败、没有任何 lint 会说话。
> 与坑 44 / 51 / 52 同族：**它们都是"没有任何东西会报错"的那一类问题。**

### 复查清单

- [ ] 结构化配置文件里有没有注释？**读它的工具认不认？**
- [ ] 用**那个工具读取它的方式**验证一次（JSON 就用 `JSON.parse`，别用编辑器"看起来很对"）
- [ ] 有没有"写来给以后用、因此从来没被读过"的配置文件？（它们坏掉了没人知道）

---

## 54. 覆盖率报"缺失"的行，可能**全都是采集边界造的假象** ★（补测批 · 第一步）

补测批第一步的诊断结论：`services` 层**不是 49.6%，是 86.6%**。
`TOTAL` 1476 条"缺失"里 **1087 条（74%）是幻影**，而且来自**三处互不相干**的边界问题——
它们**同时存在**，所以逐个排查时每一次都被单独误判过。

### 三处边界（症状完全不同）

| # | 症状 | 根因 | 规模 |
|---|---|---|---|
| 1 | 同一个函数里，**DB `await` 之后的行全部记成"未执行"**；同时把没走的分支记成走过 | SQLAlchemy async 的**每个** DB 调用都经 `greenlet_spawn()` **切一次 greenlet**；coverage 默认 `concurrency = thread` 不感知切栈 | 897 |
| 2 | `cli.py` 记成 **190/190 全未覆盖**，而冒烟日志里它明明打印过 `[cli] RBAC seed replayed` | 覆盖率**只在 API 进程**采集；CLI 跑在**另一个进程**且没插桩 | 190 |
| 3 | `float_loss_report` 记成未覆盖，而用它的用例 **PASSED** | 门禁只量 API 进程；**纯单元测试在 pytest 进程里直接调 app 代码** | 24 |

### 边界 #1 的指纹：看"缺失区域的形状"

```
deps.py:111  ✅ 有记录   ← 第一个 await（切栈**之前**）
deps.py:112-118 ❌ 全丢  ← 切回来之后，追踪器的"当前帧"已经指错
deps.py:121  ✅ 有记录   ← 模块级，另一个帧，不受影响
```

判别性实验（`tools/local-verify/_probe_greenlet.py`：同进程、只改 `concurrency` 一个变量）
用两处**必然执行**的行当判据 —— 用例断言的**就是** `auth.py:76` 的返回值，
`deps.py:117` 在每个带鉴权的请求里：

| 配置 | `auth.py:76` | `deps.py:117` | `auth_service.py` 覆盖行数 |
|---|---|---|---|
| 默认 `thread` | ❌ | ❌ | 119 |
| **`greenlet`** | ✅ | ✅ | **181** |

⚠️ **旧数据不是"偏低"，是"偏错"**：同一个文件、同样三个请求，
旧口径记了 **93** 行、新口径 **78** 行 —— 旧口径**多记了 15 行没执行的分支**。
**两个方向都错**的数据，既不能定门槛，也不能用来判断"哪里有缺口"。

### 为什么排错了半天（顺序上的教训）

先排除了**四种**看起来更可能的原因，全部**实测**否掉：

1. 追踪内核 —— 默认 / `pytrace` / `ctrace` **结果完全一致**
   → 结论：不是"用哪个内核"，是"内核与外部的栈切换没有对接"
2. `asyncpg` 本身 —— 裸 asyncpg 跑同样的查询**不丢**
   → 结论：丢事件的是 **SQLAlchemy async 那一层**（= 引入了 greenlet 的那一层）
3. 过期的 `.pyc`（字节码行号错位）—— 逐条比对，**完全一致**
4. 同一文件被记在两条路径下 —— `measured_files` 检查，**无重复**

**教训**：当一个现象"在三种配置下表现完全相同"时，
**别再换配置，要去换"被观察的对象"** —— 换个"必然执行"的对照行，
比再试第四种内核快得多。这次真正的突破口是
**"用一个每个请求必经的模块当对照"**，一眼看出"必跑的行也缺"。

### 附带教训：AST 里 `def` 的名字**不是** `Name` 节点

写"零引用 = 死代码"的探测器时，我按"定义本身也算一次绑定"去减计数，
结果**把唯一的调用点减掉了**，满屏 28 个"死代码"（真的只有 4 个）。

```python
# ❌ 错误：以为 def 的名字会进 refs
n_refs = refs.get(name, 0) - def_count[name]
# ✅ 正确：`def foo` 里的 foo 是普通字符串字段，**根本不会进 refs**
n_refs = refs.get(name, 0)
```

顺带：**框架接线的函数天然"零引用"**，必须显式排除，否则全是假阳性 ——
FastAPI 路由（装饰器接线）、pydantic 校验器（`@model_validator` 等）、
异常处理器、`BaseHTTPMiddleware.dispatch`。本项目 337 个定义里，
这类"靠框架按名字调用"的有 **51 + 27** 个。

### 判据

**覆盖率报告说某行"没执行"时，第一件事不是去补测试，是问：
"它为什么不可能没执行？"** —— 找一条**必然执行**的行做对照。

- 找得到对照行且它也是"缺失" → **先修采集**，别看缺口
  （否则你补的是一份假清单，**补完数字也不动**）
- 只缺某几行、对照行正常 → 才是真的缺口

**推论（三处边界各自的修法，按性价比排）**：
1. 配 `[run] concurrency = greenlet` —— 一行配置，换回 897 条真实数字，**必做**
2. `coverage run -m pytest` + `coverage combine` —— 让单元测试的执行进账
3. 给"另一个进程"（CLI / 运维脚本）加插桩后 `combine`
   —— 或**显式 `omit` 并写明理由**（⚠️ 但"删掉 33% 的账"长得像藏账，优先选插桩）

### 复查清单

- [ ] 覆盖率配了 `greenlet` 吗？（本项目 DB 走 SQLAlchemy async，**必配**）
- [ ] 报"缺失"的行里，有没有**每个请求必经**的行？有 → 先修采集
- [ ] 门禁采集的那个进程，**跑得到**被测代码吗？（CLI / 单元测试 / 定时任务都是反例）
- [ ] 写"零引用/死代码"探测器时，**显式排除框架接线**，并列出手动反查结果
- [ ] 数字有多个口径时，**汇报里写明是哪个口径**（本项目已因此吃过大亏）

> 完整分类清单（A/B/C 逐条）见 `docs/18-覆盖率缺口诊断.md`。

---

## 55. 缺了 gitignored 的生成物时，`pytest.skip` 会把"这条从没跑过"伪装成一个绿对勾 ★（补测批 · 发现于口径对齐验证）

**现象**：CI 全绿、本地全绿，但两边覆盖率**差 13 行**（CI 89.00% / 本地 89.25%），
逐文件对比只有 `services/import_service.py` 不一致（CI 缺 122 / 本地缺 109）。

**排除过程**（每条都有证据，不是猜）：

| 假设 | 证据 | 结论 |
|---|---|---|
| 本地多装了包 → 走了不同分支 | `app/` 里 `importlib\|ImportError\|find_spec` **0 命中** | 排除 |
| `.env` 差异（gitignored，CI 没有） | **`.env` 根本不存在**；`import_service` 不读 `settings` | 排除 |
| Python 3.12(CI) / 3.13(本地) 计数口径 | **逐文件 Stmts 完全相同** | 排除 |

**真根因**：

```
tests/test_admin_v5.py:48   SEED_JSON = REPO / "data" / "seed" / "questions.json"
_seed_items()               文件不存在 → pytest.skip("缺少种子文件 …")
seed-questions.py           写死 --format sql     ← 只产出 questions.sql + manifest
gen_seed_questions.py       默认才是 json,sql,csv
data/                       gitignored（data/ + !data/.gitkeep）
```

→ CI（全新环境）**没有** `data/seed/questions.json` → `test_seed_bank_mapping_matches_existing_rows`
（验收⑤ 的「无损」证明）**静默 skip**；本地跑得起来，**只因为有一份 9-15 的遗留副本** ——
今天 10:38 那次重写只更新了 `.sql` 与 `manifest`，`questions.json` / `questions.csv` 的
mtime 还停在 9-15。

**两条判据**：

1. **"本地有"和"环境本该有"是两件事。** 遗留副本与重新生成的文件**逐字节一致**
   （`4db36a9c…`）—— 内容没错，错的是**没人在干净环境里生成它**。
   `data/` 被 gitignore 的那一刻起，"本地碰巧有"就不再是任何论据。
2. **这类问题的唯一入口是覆盖率数字。** skip 不报错、不变红，日志里就是 `2 skipped`
   混在 600 行中。**是"CI vs 本地逐文件对比"把它逼出来的** —— 这也是口径对齐这一批
   当场就回本的地方。

**修法（A + B，缺一不可）**：

- **A**：`seed-questions.py` 加 `--format`（默认 `sql,json`）→ CI 也产出 `questions.json`。
  实测增量 **+0.05s**（1.14s → 1.19s），两个环境从此走同一份逻辑。
- **B**：`_seed_items()` 的 `pytest.skip` 改 `pytest.fail`（**硬约定 M**）→ 下次再缺**立刻红**，
  失败消息自带"缺什么 / 它是什么 / 怎么产生"。

⚠️ **顺序不能反**：先 B 后 A，CI 会当场红（那时 CI 仍然缺文件）。**A 验证通过后再改 B。**

**★ 与坑 51 的关系（同一根因，两个相反方向）**：

| | 缺什么 | 表现 | 看得见吗 |
|---|---|---|---|
| **坑 51** | 题库种子（全新库 0 题） | **32 failed** | ✅ 立刻 |
| **坑 55** | `questions.json`（gitignored 生成物） | **静默 skip** | ❌ 只能靠覆盖率对比 |

同一个 `data/` gitignore 根因。修坑 51 时加的"题库种子"这一步**只产出了 `.sql`**
（够灌库就行），没人注意到还有一条用例要 **`.json`**。
**教训：把某个生成物加进管道时，要连"还有谁在消费它的其他格式"一起查。**

---
### ★ 验证（2026-09-22 15:12，CI 侧实测——本条已闭合）

| 观测 | 老 run（`3b4b61f`） | 新 run（`a13d4d8`） |
|---|---|---|
| pytest 汇总 | `127 passed, 2 skipped` | **`128 passed, 1 skipped`** |
| 两条 skip 是谁 | `YIJIAN_BIG_IMPORT` + **本条（缺 `questions.json`）** | 只剩 `YIJIAN_BIG_IMPORT` |
| `import_service.py` 未覆盖 | **122** | **109**（**−13，缺口正好闭合**） |
| TOTAL 未覆盖 | 473 | **460** |

- `-rs` 让"另一条 skip 是谁"当场可见：`SKIPPED [1] tests/test_admin_v5.py:715: …YIJIAN_BIG_IMPORT=1…`
- 修后两地残余差异**只剩 2 行**，在 `cli.py`，**方向是 CI 覆盖更多**（`_wait_db` 重试多跑一轮 ——
  CI 的 PG 是 service container、稍慢）→ **时序差异，不是缺陷**。
- ⚠️ 中途一度用一份**来源认错**的覆盖率块（其实是老 run 的）做过"自我否定"，把本条的正确归因
  推翻了一次。教训单独记为 **坑 56**。


## 56. 粘贴回来的日志必须能回答"它来自哪一次运行" ★

**现象**：一次排错里，CI 的覆盖率块被贴回来两次，其中一次**实为上一个 run 的输出** ——
两次 run 的行数、格式、甚至 TOTAL 都几乎一样。我据此做了**自我否定**（"13 行的归因错了"），
并写进了文档与记忆。**下一轮拿到带 `-rs` 的那次 run 后，原归因被证明完全正确。**

**为什么危险**：这类"证据"看起来最可信 —— 有真实数字、格式正确、来源是官方日志。
**它唯一的缺陷是没有身份**。而没有身份的测量值，与"凭印象报出来的数字"在推理上等价。

**判据**：

> **任何贴回来 / 导出 / 截图的证据，必须能回答"它来自哪一次运行"**
> （run id / commit SHA / 时间戳）。答不上来的**只能当线索，不能当判据** ——
> 更不能用来推翻已有结论。

**这次的代价**：一轮往返 + 一次公开的自我否定 + 文档里临时写下的一段错话。

**配套做法（让日志自带身份）**：

- ✅ 门禁步骤里已有 `sha256sum .coverage` 指纹 —— 两次 run 一比就知道是不是同一次
- ✅ `-rs` 让每条 skip 的**文件:行号 + 原因**可见（本条事故里正是它一举定位）
- ⬜ **建议再加一行**：`echo "commit=$GITHUB_SHA run=$GITHUB_RUN_ID"`（放在采集/门禁步骤开头），
  这样**复制任何片段都自带出处**，粘贴回来的人不需要回忆是从哪个页面复制的

**与坑 53 / 55 同族**：都是"信息在传递过程中丢掉了**判断它可信所需的那一部分**"。
坑 53 丢的是"读它的工具认不认"，坑 55 丢的是"这条用例到底跑没跑"，
本条丢的是"这份输出是哪一次的"。

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
- [ ] **破坏性操作的确认框**：里面的数字是按**即将执行的动作**分类算的吗？还是照抄了后端的汇总字段？（坑 29）
- [ ] **模态框里发起的写操作**：失败态画进**模态框内部**了吗？还是被弹窗挡住了？（坑 30）
- [ ] **下载 / 剪贴板这类环境相关能力**：先做隔离验证，分清是应用问题还是环境问题；证伪的改动要还原（坑 31）
- [ ] **提交前截图验真**：md5 去重（同哈希 = 同一张图）+ 抽查内容；`git status` 的 `??` 里有没有不认识的文件？（坑 32）
- [ ] **验收文档的词汇 vs 后端枚举**：不一致时是"默默选一边"还是"两套都显示"？顺便问一句：这个语义是**批次**的属性，还是**题目**的属性？（坑 33）

---

## 附：一条"新增状态回退接口"的检查清单

"把对象从**删除/停用**态拉回来"的接口（`restore` / `enable` / `undo` / `cancel` …）
比"往前推"的接口更容易出事 —— 因为它要面对一个**可能已经腐朽的旧状态**。
照着过：

- [ ] **幂等**：已处于目标状态时返回 `code=0`，而不是报错（坑 41）
      - 且**幂等分支没有任何写入副作用**，尤其**不要动 `version`**
      - 给调用方一个区分信号（如 `already_active: true`）
- [ ] **关联数据校验**：这个对象依赖的东西，会不会被单独删掉？（坑 40）
      - 可空弱引用（`chapter_id` / `knowledge_point_id`）→ **必须自己查**
      - 软删除的目标（章节/知识点/科目）→ **必须自己查**（外键不会被触发）
      - 连"依赖的依赖"也要查（知识点的**父章节**）
- [ ] **拒绝时说清楚**：哪一条不满足 + 可执行的下一步，别只回一句"无法恢复"
- [ ] **拒绝 = 零副作用**：断言失败后 `is_deleted` / `status` 一点没变
- [ ] **留痕**：`content_change_logs` 的 `action=restore`，`diff` 形状与 `delete` 对齐
      （`{before:{is_deleted:true}, after:{is_deleted:false}}`），前端审计抽屉能直接渲染
- [ ] **权限不新增**：复用既有权限码，并在注释里写清**为什么**选那一条
      （例：试卷恢复用 `exam:publish` 而非 `exam:create` —— 恢复已发布的卷等于让它重新生效）
- [ ] **反向可逆**：校验失败被拒之后，把关联数据修好，**同一个请求应当能通过**
      （用例里要覆盖，否则等于把对象永久锁死）

---

## 57. pytest 没有超时机制 —— 一条"把网络超时当被测对象"的用例能把 CI 拖到 28 分钟 ★（2026-09-23）

**现象**：`18aed67` 那次 CI，前 13 步一共 **52 秒**全部 success，**第 14 步 `pytest` 跑了 1689 秒**
（28 分 9 秒）仍没结束，只能人工取消。取消掉的步骤**没有汇总行、也不报是哪条用例**
—— 排障等于从"沉默"里猜。

**根因**：新增的 CLI 用例里，两条"不可达"分支**真去连了一个删掉 host 的地址**（`127.0.0.1:1`）。
写它的时候的理由是"ECONNREFUSED 是瞬时的"（本地确实是）—— **那个理由只在包被拒绝（RST）时成立**。
一旦包被**丢弃（DROP）**，代价就由**操作系统的 SYN 重试预算**决定，与代码无关。

实测（本机，全部走**生产函数** `cli.main`，`--timeout 1`）：

| 目标 | 被测函数 | 耗时 | 谁在兜底 |
|---|---|---|---|
| `127.0.0.1:1`（RST） | `_wait_db` | 3.55s | 本机沙箱给每次连接加 ~2s |
| `10.255.255.1:5432`（DROP） | `_wait_db` | **6.52s** | asyncpg 自带 `timeout=5` ✅ 有界 |
| `127.0.0.1:1`（RST） | `_wait_redis` | 3.56s | — |
| `10.255.255.1:6379`（DROP） | `_wait_redis` | **22.55s** | **无人兜底**（Windows 21s；Linux `tcp_syn_retries=6` ≈128s）❌ |

裸 socket 对照：`127.0.0.1:54321` 2.04s / `10.255.255.1:5432` 21.04s。

**⇒ 那条用例跑多久，成了"宿主机 TCP 栈的属性"，而不是"被测代码的属性"** ——
这正是"**把网络超时当单元测试**"。`_wait_redis` 的 `from_url` **没有任何应用层超时**
（只有 asyncpg 那一侧有 `timeout=5`），所以它是两者里更危险的那个。

**处置（两条并列）**：

1. 把**连接函数换掉**（mock），别再"删掉 host 只留端口去真连" —— 那只是把"网络超时"换个地址再赌一次；
2. `ci.yml` 的 pytest 步骤加 **`timeout-minutes: 12`**（绿的时候约 3~4 分钟）。

第 2 条**不修根因**，只把"无限期挂住"变成"**有界的、自己会说话的失败**"——
与坑 51「恒红的门禁等于没有门禁」同一条思路的反面：**不说话的失败等于没有失败**。

**守卫要两条，缺一不可**（这一条是本轮**实测**出来的，不是推出来的）：

- ① 断言"**连接函数确实被调用过**" —— **确定性**，抓得住"mock 被删掉"；
- ② 耗时**上界** —— 抓几十秒的量级。

**只写 ② 会漏**：把 mock 打掉之后，本机真连 RST 只要 **3.5s**，**落在 5s 上界之内 → 用例照样绿**，
而"依赖网络"的毛病已经回来了。（先写的只有 ②，反面验证时就是这么发现的。）

### ⚠️ 未解释的观察（**OPEN**，2026-09-23 立）

> **这一条不是"已修好的坑"，是一条**挂着的问题**。**
> 修复已经做了（见上），但**修复的完整性还没有被证明**。
> 写在这里的唯一目的：**别让"已修"被读成"已归因"。**

**★★ 一句话结论（2026-09-23 补，用户指定口径）**：
修复后实测 **732s**（撞 12 分钟 timeout）—— 说明：
**A 只解释部分，第三个卡点未定位。**
定位依赖 **pytest 日志尾部**（`-q` 圆点与用例顺序一一对应）。

**观察**：pytest 在 CI 上单次耗时 **1689s（28 分钟）**，而且**没有跑完**（被人工取消）。

**已知原因能解释到哪**：`_wait_redis` 真连不可达地址 —— 上表实测：本机 RST 3.56s、
**丢包 22.55s**、Linux 按 `tcp_syn_retries=6` 外推 ≈128s。
**这条只解释到"分钟级"，解释不了 28 分钟。**

**已否证的候选**：B（`_scratch_database` 的 `CREATE`/`DROP DATABASE` 阻塞）——
有 `idle in transaction` 旁观者时 `CREATE` 0.90s、`DROP ... WITH (FORCE)` 6.67s，**不阻塞**。

**⇒ 28 分钟的完整归因未完成。** 差的材料：**该 run 的 pytest 步骤日志尾部**
（job 日志要认证，匿名读不到；step 级状态倒是能匿名从 job 页 HTML 里拿到 —— 见 `docs/18` §12.1）。

**★★ 判定"修复完不完整"的唯一依据：修复后那次 CI 的 pytest 步骤实际耗时。**

| 修复后 pytest 用时 | 结论 |
|---|---|
| **3~4 分钟**（与历史绿值一致） | A 是全部原因 → **修复完整** |
| **6~10 分钟** | A 只解释一部分 → 还有第二个慢点 |
| **撞到 12 分钟超时**（新加的 `timeout-minutes`） | **有第三个未知卡点** → 必须重新归因 |

#### 📌 实测结果（2026-09-23 17:4x，run `35844461552`，sha `a1e4301`）

**判定：第三分支 —— 有第三个未知卡点。A 不是（全部）原因。**

| 项 | 实测 |
|---|---|
| 前 13 步（安装/建表/迁移/种子/题库/起 API） | **54s**（09:43:14 → 09:44:08），全部 success |
| **pytest（第 14 步）** | 09:44:08 → **09:56:20 = 732s（12 分 12 秒）**，`conclusion=failure` |
| 第 15 步覆盖率门禁 | `skipped` |

⇒ ✅ **`timeout-minutes: 12` 生效**：同样的卡死，上一次烧掉 **1689s** 还要人发现，
这一次 **732s 就自己失败**（`exceeded the maximum execution time` 那一路）。
❌ **但卡死没有消失** —— 上一次 1689s、这一次 732s，两次都是「pytest 起步即卡」，
说明**它是可复现的、Linux 特有的**，而 A（网络超时）解释不了它。

> **⚠️ 更正（2026-09-23 18:1x，紧邻补注 —— 不改上面那句，按对账原则"保留原文 + 更正"）**
>
> **"可复现"这个判断已被推翻。** 紧接着的那次 run `35846416245`（sha `2e7d0ac`）
> **全部步骤 success**：pytest **43s**、覆盖率门禁 2s success（顺带证明 **93.10 这道新门槛在 CI 上通过**）。
> 而 `2e7d0ac` 与 `a1e4301` **只差文档** —— 业务代码与用例**完全一样**。
>
> | run | sha | 代码差异 | pytest |
> |---|---|---|---|
> | 35838184843 | `18aed67` | 只差文档 | **1689s**（人工取消） |
> | 35844461552 | `a1e4301` | 只差文档 | **732s**（撞 12 分钟 timeout）→ failure |
> | **35846416245** | **`2e7d0ac`** | — | **43s 绿** ✅ |
>
> ⇒ **三次里两次卡、一次绿，代码相同** → **是间歇性（flaky）的，不是确定性缺陷**。
> **"可复现的、Linux 特有的"应改成「flaky，2/3 命中」。**
> ⇒ 间歇 + 只依赖时序 ⇒ 更像**某处"没有超时的等待"被时序撞上**，而不是数据/环境缺陷。
>
> **待验证的预测**（用贴回来的日志尾部核）：若是 `test_wait_redis_returns_0_when_it_answers`
> （圆点 **#113**）—— 它是全套里**唯一一处无界等待**：自写 RESP 桩 + redis-py `ping()`
> **没有 `socket_timeout`**，所以 `--timeout 10` **救不了它**（卡在 `await` 内部，
> 重试循环的 deadline 根本轮不到检查）。

**下一步取证（最便宜的路径已备好）**：
`-q` 的进度圆点**与用例顺序一一对应**（无随机插件）。253 条用例的文件边界：

| 圆点序号 | 文件 |
|---|---|
| #1–14 | `test_admin_v3.py` |
| #15–34 | `test_admin_v4.py` |
| #35–48 | `test_admin_v5.py` |
| #49–106 | `test_admin_v7.py` |
| #107–110 | `test_auth_session.py` |
| **#111–141** | **`test_cli_commands.py`（本坑涉及的 31 条）** |
| #142–156 | `test_idgen.py` |
| #157–171 | `test_import_parse.py` |
| #172–186 | `test_import_pipeline.py` |
| #187–231 | `test_import_validate.py` |
| #232–233 | `test_rate_limit.py` |
| #234–239 | `test_smoke.py` |
| #240–248 | `test_sms_inproc.py` |
| #249–253 | `test_sms_login.py` |

⇒ **把 pytest 步骤日志的最后 1~3 行贴出来**（`-q` 的圆点行）即可定位到具体用例：
第 1 行满 80 个点，第 2 行从 #81 起，依次数过去就是序号。


⚠️ 这条 OPEN 观察比同族其他坑**更隐蔽一点**：同族（坑 50/51/54/55/56）都是
"没有任何东西会报错"，而**这一条会"报成功"** —— 修复看起来对、CI 也绿，
只是花了 6 分钟；到那时候**没人会去问"那 5 分钟是谁花的"**。
所以"耗时"必须当成**结论性证据**记下来，而不是当成一次顺利的 CI。

**🔧 取证步骤的决定（2026-09-23 用户裁定）：暂不加。**

#### 🎯 归因完成（2026-09-23 18:2x）—— **OPEN 关闭**

**真凶：`test_admin_v7.py` 里某条用例的 teardown**（**不是**本轮新增的 `test_cli_commands.py`）。

- **定位**：用户贴回的日志尾部 = `..............................................s......................... [ 28%]`
  → **72 个圆点**。`-q` 的圆点是**每条用例跑完才写**的（本地实测：跑到一半能读到"半行"，
  说明每条都刷盘），按「圆点 ↔ 用例顺序」（253 条，一行 80 个）⇒ **第 73 条**：
  `test_admin_v7.py::test_exam_restore_roundtrip_and_idempotent`。
  ⭐ **第 72 条的点已经打出来了** ⇒ 卡的是**第 72 条的 teardown**，不是第 73 条的 body。
- ⚠️ **我原先的预测（#113 `test_wait_redis_returns_0_when_it_answers`）被这条日志否证**。
  如实记下，免得下次又把"最可疑的那个"当成"就是那个"：
  **"名单里只有一个候选"不等于"原因只有一个"**。
- **机制（结构性，不是猜）**：`created` fixture 的清理（`test_admin_v7.py:93–102`）走
  `conftest.sql_exec` 做 `DELETE`；而 `sql_exec` 是 `asyncpg.connect(dsn)` ——
  **既无 `timeout` 也无 `command_timeout`**。asyncpg 的 `timeout=` 只管**建连**，
  **单条命令**要 `command_timeout=`，默认 `None` = **无限等**。
  ⇒ 只要撞上别人（API 进程）**未提交的行锁**，`DELETE` 就**永远等下去** —— 不是报错，是沉默。
  **间歇性**也对上了：取决于那一瞬 API 手里有没有未提交的事务。
- **实测（本机，构造未提交的行锁 + 子进程持锁）**：
  · 老形态（无 `command_timeout`）：外层 8s 兜底**都等不到** → **会一直等** ✅ 根因成立
  · 修好后 `conftest.sql_exec`：**30.4s 抛 `TimeoutError`** → **有界失败** ✅ 修复成立
- **审计（用户要求：不止修一条）**：`tests/` 全部 + `tools/local-verify/`，40 个文件，
  判据「**这条如果对端永远不响应，它会一直等吗？**」→ 扫出并修掉 **9 处**同类无界等待：
  `conftest.sql_exec` / `sql_fetch`（**共享，影响面最大**）、`test_admin_v4.py` / `test_admin_v5.py`
  各自的本地直连 helper、`test_cli_commands.py` 的 3 个临时库连接、
  `tools/local-verify/import-sim-bank.py`、`restore-e2e-softdeleted.py`、`test_idgen.py` 的
  `t.join()`（无超时 → `join(timeout=60)` + 断言线程已结束）。
  **其余调用点本已有界**（httpx `timeout=15`、`cdp_browser.wait_for`），未动。

- **📌 修复后 CI 结果（run #27，sha `e4ded57`）**：pytest **70s → `success`**、
  覆盖率门禁 1s `success`、整轮 **2m12s** —— 落在历史绿 run 的正常区间（1m09s–2m12s），
  **不再是 732s / 1689s**。⇒ **修复有效**。
- **⚠️ 更正（同一次落档内、5 分钟内自己发现写错）**：run #24（sha `ca53019`）整轮 **10m24s**，
  pytest **560s 且 `cancelled`** —— 它**不是**"慢但完成"，而是被
  **`concurrency: cancel-in-progress`** 顶掉的（我随后推 `a1e4301` 时，同一个 concurrency group
  把 #24 取消了）。
  ★ **教训：时长不是结论的替代品。** 我先看到 10m24s，就推断"等了很久但最终完成"；
  而 `conclusion` 说的是"被取消"。**正确读法：先看 `conclusion`，再看时长 ——
  时长只是线索，不是判据**（与坑 56「证据必须能追溯来源」同族）。
  ⇒ 三次卡住的形态因此更清楚：`#23` 1689s（人工取消）/ `#24` 560s（被顶掉）/
  `#25` 732s（撞 12 分钟超时）—— **三次都是"卡住不结束"，没有一次是"慢但完成"**。



理由：**省一次 CI 运行** —— 当前走「**人贴日志尾部**」这条路（见上表，圆点 ↔ 用例一一对应，
贴最后 1~3 行即可定位）。**如果将来"贴日志"变成常态**（排查不再是偶发），
再加那个 `if: always()` 的取证步骤（pytest 每起一条用例写一行到文件 →
后续步骤把它 `::error::` 成**注释**，注释匿名可读，不依赖 job 日志权限）。**现在不是。**

## 58. SQL 绑定参数的「类型契约」—— 注释里的 `:name` 也是参数 ★（2026-09-24）

写 Batch 8 的统计看板数据层时踩到的一族坑。共同点：**写的时候看起来完全正常**，
全部是**类型 / 解析层面的契约**，不在"想清楚算法"的射程内。

| # | 写法 | 症状 | 根因 |
|---|---|---|---|
| 1 | `CAST(:day AS date)`，却传 `"2026-09-24"`（字符串） | `invalid input for query argument $1: 'str' object has no attribute 'toordinal'` | `CAST` 会把绑定参数**推断成 `date`**，asyncpg 的 date codec 要 `date` 对象。**类型是契约的一部分**，不能指望"字符串也能用" |
| 2 | 一个占位符同时用于 `smallint` 与 `numeric` 两列（`$7` 既当 `correct` 又当 `score`） | `AmbiguousParameterError: inconsistent types deduced for parameter $7` | PG 从**一个位置**推出两种类型。造数脚本与测试装置里**各踩一次** —— 说明它不是偶发 |
| 3 | SQL **注释**里写 `CAST(:x AS interval)` 当举例 | `InvalidRequestError: A value is required for bind parameter 'x'` | `text()` 解析的是**整段字符串，含注释**。给一个**没有值**的名字就炸；给一个**恰好存在**的名字则**静默共享同一个绑定** —— 后者更隐蔽 |
| 4 | `ZoneInfo("Asia/Shanghai")` | 本地 `ZoneInfoNotFoundError`；**CI（Linux）正常** | Windows 要额外的 `tzdata` 包。长得像"环境问题"而不像代码问题，实际是**选了不可移植的写法** |
| 5 | `CAST(:step AS interval)` 传 `"1 month"` | 需要 `timedelta`，而 `timedelta` **表达不了"1 个日历月"** | 30 天会逐月漂移。正解是**不传这个参数**，在 SQL 里按粒度 `CASE` 出来 |

**处置**（已写进 `stats_service.py` / `test_stats_service.py` 的注释）：
- 参数按**真实类型**传（`date` 传 `date`、`int` 传 `int`），**不传 `isoformat()` 字符串**；
- 一个占位符只服务一种类型（宁可多一个 `:score`）；
- **注释里不写 `:name`**（写 `name` 或 `` `name` ``）—— 已全量清理 **24 处**；
- 时区用**固定 +08**（与 `sms_service.CN_TZ` 一致），不为一个"本来就没有夏令时"的时区引依赖；
- "表达不精确的 interval"（月）在 SQL 里 `CASE` 出来，不走参数。

### 附：判据本身也要被验证 —— 一个当场抓到的例子

§④ 的判据第一版写的是 `"text(" in code`，结果数出 **3** 处（应为 2）——
`read_text(` 被**当成子串命中**了。改成 `re.findall(r"(?<![\w.])text\(", code)` 才对。
同族问题：判据写松了，**它永远不会红**，于是"守卫存在"和"守卫有效"被混为一谈。

### ★ "变异存活"的一次真实记录（两种解释，别搞混）

变异验证跑了三轮。**第一轮：10 捕获 / 1 存活 / 2 未应用。**

- **存活的那条** = 把 `funnel.sql` 的 `o.status = 'paid'` 改成 `o.status <> 'paid'`。
  原因：漏斗用例**只验了单调性**，而"有任何订单"**依然单调**。
  ⇒ 这是"**测试太弱**"（不是 harness 配错）。修法：三段都改成**与独立直查对账**。
- **"未应用"的两条**（锚点用了反引号 / 锚点命中多处）**不能算"存活"** ——
  变异根本没打上去，用例当然是绿的。这正是"变异存活有两种解释"里最容易被误判的一种。
  ⇒ harness 现在把"**锚点不匹配**"单独计成 `未应用`，并让整轮返回非 0。

修完复跑：**13 捕获 / 0 存活 / 0 未应用**，还原后 22 条用例全绿。

**同族**：坑 50（并行 Edit 互相覆盖）、坑 54（采集边界）、坑 55（skip 伪装绿）、坑 56（日志出处）、
坑 57（无界等待）—— 全是**"没有任何东西会报错"**那一类。

## 59. S1-b：一族「环境问题伪装成缺陷 / 报错指向别处」（2026-09-24）

给统计看板接 HTTP 层（缓存 + 权限 + 端到端）时踩到的 6 处。共同点：
**症状与原因相隔很远**，而且**没有一处是"业务代码写错"** —— 全在"环境 / 判据 / 工具"这一层。

| # | 现象 | 真因 | 下次一眼认出 |
|---|---|---|---|
| 1 | `GET /admin/stats/trends` → **500** | `response_model` 校验失败：我把 `optional_series` 声明成 `list[dict]`，它实际是 **dict**（`{"avg_duration_ms_median": [...]}`） | **500 且日志里是 `ResponseValidationError`** = 契约写错，不是逻辑错。日志直接给出 `loc=('response','data','optional_series')` |
| 2 | 3 条 `test_smoke` 失败：`42902 今日验证码发送次数已达上限` | **不是缺陷**：`register()` 走 `127.0.0.1` 的**共享限流桶**，当天已跑了好几轮全量 → 桶耗尽（fakeredis 在 API 进程内存里） | 见 `42902` 先问"**今天跑了几轮**"；**重启 API 即复位**（内存态）。`fresh_user` 用假 IP 就是为了避开它 |
| 3 | `test_logout_without_session_id_returns_40001` 报 `40102 登录凭证无效` | **不是缺陷、也不是 flaky**：该用例**手搓 JWT**（`_access_token_without_sid`），用**测试进程**的 `JWT_SECRET` 签名，API 进程用自己的验签 ⇒ **两进程 env 不同源** | `40102` + 用例里有"手搓 token" ⇒ 查 **`JWT_SECRET` 是否同源**。`run-smoke.ps1` 给两个进程同一套 env，所以 CI 是绿的 |
| 4 | `httpx.ASGITransport` 报 `AttributeError: '__enter__'` | httpx 新版 **`ASGITransport` 是 async-only**，必须配 `AsyncClient`（同步 `Client` 直接炸） | 看到 `'__enter__'` 就想到"这个 transport / client 是不是 async-only" |
| 5 | 我写的结构断言**每条路由都报缺权限**（**假红**） | `require_permission(...)` 返回**闭包** `_checker`，`repr` 里既没有参数也没有函数名 ⇒ `"stats:read" in repr(dep)` **永远是 False**。权限码只在 `__closure__` 的自由变量里 | 断言"有依赖"不够，**在 `repr` 里找字符串更不够** —— 得读闭包（代码见下） |
| 6 | 变异验证 14 个里 **1 个存活** | 测试只在"第一次（未命中）**之后**"读缓存 ⇒ 验的是**性质**（"写进去了"）而不是**值**（"任何时点都不含 `cached=True`"） | 同「变异存活 ① 的典型症状」：**修法是把断言补到另一个时点**，不是加更多性质断言 |

### 5 的代码：「从闭包读权限码」

```python
def _declared_permissions(route) -> set[str]:
    found: set[str] = set()
    for dep in getattr(route, "dependencies", None) or []:
        fn = getattr(dep, "dependency", None)
        for cell in getattr(fn, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:      # 尚未绑定的 cell
                continue
            if isinstance(value, tuple):
                found |= {v for v in value if isinstance(v, str)}
    return found
```

⇒ 有了它，"**把 `stats:read` 写成 `stat:read`**"这种变异才被捕获
（否则那条用例只是个**永远绿的摆设** —— 它连"权限码写错了"都发现不了）。

### ★ 一条正面记录：`response_model` 真的在把关

第 1 条虽是我的错，但它同时证明**契约声明是有效的**：
类型写错不会"静默返回一个结构不对的 JSON"，而是**当场 500 + 明确指出哪个字段**。
这正是"**断言契约本身，而不是它当下的长相**"在**运行时**的对应物 ——
文档里的那句判据，在这里被 FastAPI 替我执行了一次。

**同族**：坑 50/54/55/56/57/58 —— 全是"**没有任何东西会报错**"或"**报错指向的地方不是失败点**"。

## 60. `next build` 在**本机沙箱**下跑不通（但 CI 不跑它）★（2026-09-24）

写 Batch 8 的前端（S1-c）时踩到的。**没有一处是源码问题** —— 全是"本机环境 != CI"。

| # | 现象 | 真因 | 处置 |
|---|---|---|---|
| 1 | `next build` 报 `[safe-delete] 操作失败: genie-trash ETIMEDOUT` | Next 构建**开始前**会 `recursiveDelete('.next')` 清旧产物，而沙箱给 Node 的 `fs.rmdir` 装了**批量删除守卫**（`node-safe-delete-shim.cjs`）→ 超时 | 把 `.next` **移出**仓库（`mv`，rename 不触发删除守卫）→ 该错误消失 |
| 2 | 移出后仍失败：`EPERM: operation not permitted, open '...\.next\trace'` | 沙箱**不允许在仓库内创建 `.next`**（写 trace 文件就被拒） | **接受**：本机不跑 build |
| 3 | 上游：以为"build 挂了" | 两次都是**构建开始前**失败（一次在删旧产物、一次在写 trace），**与源码一行关系都没有** —— 但报错离源码很近，容易误判 | **看报错发生在哪一步**，别急着改代码 |

### ★ 关键判断：`next build` **不在 CI 的前端门禁里**

`ci.yml` 的前端 job（名字就叫「前端（tsc --noEmit）」）只跑：

    npm ci  →  npm run lint  →  npm run format:check  →  tsc --noEmit

**没有 `next build`。** ⇒ 本机跑不通 build **不会**让 CI 变红，
而三道**真门禁**（lint / format:check / tsc）本地已全绿。

⚠️ 但反过来说：**build 能发现的问题（SSR 报错、打包体积）本地也发现不了** ——
所以本批改成了**可执行的替代验证**（见下）。

### ★ ECharts 的 SSR 安全：怎么在**没有 build** 的情况下证明

`echarts.init()` 需要真实 DOM，而 Next 的 App Router **默认会在服务端渲染客户端组件**。
真要出事，就是构建/SSR 阶段直接崩。用两条证据替代 build：

**① 可执行证据（Node 里跑一遍最危险的那一半）**

    node -e "const e=require('echarts/core'); ... e.use([...]); console.log(typeof window)"

实测：**顶层导入 + `use()` 在无 DOM 的 Node 里成功**（`window` / `document` 都是 `undefined`），
而 `e.init(null)` **如期报错** `Initialize failed: invalid dom` ——
⇒ `init` 必须在**有 DOM 时**才调，也就是只能放在 `useEffect` 里（effect 只在浏览器跑）。

**② 静态证据**：`EChartBase.tsx` 里 `echarts.init` 与 `ResizeObserver` 都在 `useEffect` 内，
模块顶层只有 `echarts.use([...])`；外层 `EChart.tsx` 用 `next/dynamic(..., { ssr: false })`
（**在这一层内部**做掉，业务侧不用知道）。

### ★ 一条容易被忽略的事实：**前端改动不进 Python 覆盖率**

`coverage run --source app` 采的是 `apps/api/app/`；前端 `apps/admin` 的 `package.json` 里
**没有**覆盖率脚本。⇒ **S1-c 这类纯前端批次不会让 `fail_under` 动**。
（但**顺手改的后端**会 —— 那部分必须带测试，见下。）

### ★ 本批"顺手改后端"的做法（覆盖率算法的第一次实战）

前端要渲染"真 / 造"标记，但当时只有 `distributions` 的 meta 带 `origin_label`，
其余端点**没有** —— 前端就得写一条兜底分支，而那条分支一写下来，就等于
"前端开始判断真假"，`docs/20` §7 判据 13 当场只落实了一半。

所以补了 4 处 `origin_label`（overview / trends / funnel / weak_points，各 1 行）
**并同时补了 `test_every_endpoint_meta_carries_origin_label`** ——
"顺手改后端"必须带测试，否则新增的行没人覆盖，覆盖率会往下走
（`.coveragerc` 里那段"k=2 还能容多少条未测代码"讲的正是这件事）。

**同族**：坑 50/54/55/56/57/58/59 —— "**没有任何东西会报错**"或"**报错指向的地方不是失败点**"。

## 附：一批交付收尾的固定动作

每批做完，**在提交前**按这个清单过一遍（都是上面坑的"可执行版"）：

### 1. 扫陈旧引用

搜"把**当前状态**写成**旧值**"的地方，统一到当前值：

```bash
git grep -nE "[0-9]+ passed|v0\.1（Batch [0-9]）|当前版本（Batch [0-9]）|Batch 1 ~ [0-9]" -- . ':!*.lock'
```

重点看这几处（最容易漏）：

- `package.json` / `app/layout.tsx` 的 description、`main.py` 的 Swagger DESCRIPTION
- 根 `README.md`：批次状态表、验收文档链接、目录树、测试数、§「下一步」
- `tools/local-verify/README.md` 的"期望输出"块
- 目录树/端口/条数这类**会随批次增长的数字**（接口数、用例数、截图数、坑条数）

> ⚠️ **必须区分两类，不能一刀切：**
> - **当前状态类** → 必须改
> - **历史快照类** → **不要改**。每批验收文档里的"当时"数字（`docs/09` 的 `14 passed`、
>   `docs/10` 的 `26 passed`…）是**验收记录**，改写等于篡改历史。
>   各自标题已限定批次，读者不会误读。

### 2. 截图 / 导出物验真

- [ ] md5 分组去重（**同哈希 = 同一张图**；"应该不同"的两张图同哈希 = 没抓到）
- [ ] 抽查内容（曾出现截图目录混进**登录页**截图、文件名却写着目标页的情况）
- [ ] `git status` 的 `??` 列表逐个确认（曾出现 agent-browser 路径含空格被截断，
      在仓库根目录留下一个 `ProtectWorkBuddyONE` 垃圾 PNG，**差点被提交**）
- [ ] ★ **未跟踪的"目录"要单独盯** —— `git status`（含 `--porcelain`）默认把未跟踪目录
      **折叠成一行**（`?? some/dir/`），**不列出里面的文件**；扫"文件列表"时极易跳过它。
      查法：`git status --porcelain -uall` 展开，或**专门看 `??` 里以 `/` 结尾的项**。
      ⚠️ 2026-09-24 实测：`apps/api/%SystemDrive%/`（4 个 Windows 组件缓存 DB / 940K）
      就是这样冒出来的，一个 `git add -A` 就会提交进去。
      **已加 `.gitignore` 规则挡住这一类**（`%VAR%/` 与 `$env:*/`）——
      路径不展开是**稳定行为不是偶发**，挡住比每次靠人看见靠得住。

### 3. push 后核对远端 HEAD ⚠️

```bash
git push origin main
REMOTE=$(git ls-remote --heads origin main | cut -f1)
LOCAL=$(git rev-parse HEAD)
[ "$REMOTE" = "$LOCAL" ] && echo "✅ 远端 == 本地" || echo "❌ 不一致"
```

**为什么必须做**：Batch 5 的提交 `810c50d` 曾**只在本地**，远端仍停在 `ac0e584`，
而当时以为"已经推过了"。本地 `git log` 完全看不出这件事，只有 `ls-remote` 对账才暴露。

**⚠️ 别忘了区分两种"核对失败"**（Batch 7 实测踩到）：

| 现象 | 含义 | 该怎么办 |
|---|---|---|
| `remote=<别的哈希>` | 远端确实没收到 | 真的没推上去，查 push 输出 |
| `remote=`（**空**） + `kex_exchange_identification` / `Connection ... abort` | **网络抖动**，不是没推上去 | **重试 ls-remote**，别急着重新 push |

实测：一次 `git push` 的输出里明明有 `d8a15bf..b7d07b7  main -> main`，
但紧跟的 `ls-remote` 因 SSH 连接被中断而返回空 —— 若此时就下结论"没推上去"，
会白白重推一次（甚至误开 force push）。**先重试，再判断。**

**汇报时给出哈希，不要只说"已推送"。**

**⚠️ 第四次（2026-09-21 实测踩到）：push 的输出被管道吞掉，看起来"命令跑过了"**

```bash
git push origin main 2>&1 | tail -4; echo "rc=$?"    # ❌ 输出只有一行 rc=1，什么都看不见
git push origin main > push.log 2>&1; echo "exit=$?" # ✅ 重定向到文件，才看到真实结果
```

第一次执行时 `| tail -4` 把输出吃干净了（只留下一个没有意义的 `rc`），
**远端仍停在 `f7cd9bb`、本地领先 3 个 commit** —— 是 `ls-remote` 对账**当场抓住**的。
第二次不经管道、直接重定向到文件，一行就看清：`f7cd9bb..30487d6  main -> main`。

**判据**：**不要给 `git push` 接管道**。要看输出就 `> 文件 2>&1` 再读文件；
管道会把"失败原因"和"成功回显"一起吞掉，而**两者都表现为"什么都没有"**。

这是 E 这条规则的**第四次**（前三次分别在 Batch 5 / Batch 7 / 数据范围收口批）——
但也是**第一次被当场抓住而不是等到下个会话**：说明对账本身是有用的，
**问题从来不在"忘了对账"，而在"把'没回显'当成了'已成功'"**。

### 4. 静态门禁 + 回归

- [ ] `npm run typecheck` / `npm run lint`（前端）与 `python -m compileall` / `ruff check`（后端）都 exit 0
- [ ] `run-smoke.ps1` 全绿，且**上一批的用例不回归**（对照上一批的 `passed` 数，+N 才对）
- [ ] 跑 `build` **前**先杀掉 `next dev`（共用 `.next`，否则页面不 hydrate —— 坑 17）

### 5. 覆盖率数字对照（每批必看）★

- [ ] 跑一次 `coverage report`，**把当前数字记下来**，与门槛和"下一档触发条件"对照
- [ ] 若**触发条件已满足** → 本批就把 `fail_under` 抬上去，并写一条新的"下一档 + 触发条件"

```powershell
# 一键跑完就有（run-smoke.ps1 收尾会打印 TOTAL 行）；手工复核：
cd <repo>
python -m coverage report --data-file .coverage --skip-covered
# 单看某一层：
python -m coverage report --data-file .coverage --include "*/app/services/*"
```

**为什么单列一节**：门槛是**棘轮**，而棘轮只有"会动"才叫棘轮。
`fail_under` 一旦写死就没人回头看 —— 它会从"门禁"退化成"一个数字"。
所以把"看当前数字 + 对照触发条件"做成**每批的固定动作**，
而不是依赖"当时说过达到后要改"。

当前（2026-09-21 更正）状态与触发条件写在仓库根 `.coveragerc`：

```
fail_under = 65                  ← 待重定（见下行）
触发：实测 ≥ 90%  → 动作：改成「实测值 − 1」并重写这一段
```

⚠️ **2026-09-21 更正**：上面原有的 `65` 与「触发：≥70% 或 services ≥60%」
是拿**作废的实测数字**定的 —— 那套数字缺了 `[run] concurrency = greenlet`，
是采集假象（坑 54）。修正后实测 **86.55%**，于是：

- 旧的触发条件**在更正当天就已自满足**（86.55 ≥ 70，services 86.6 ≥ 60），
  也就是说它从"以后要动的棘轮"变成了"从来没动过的棘轮" —— 已改成 ≥ 90%
- 旧门槛 65 按新实测算 **"掉 21 个点都还是绿的"**，
  重定方案（建议先提到 85）见 `docs/18-覆盖率缺口诊断.md` §6，**等裁定**

⚠️ 顺带记一条**易混的描述**：`services` 层的缺口 **不是"未来批次还没写的代码"**，
而是**已有代码里没被测试打到的部分**（错误分支 / 边界处理 / 辅助方法）——
**补测现在就能做**。把它误当成"等未来"，就会一直等下去。
（修正后 services 是 **86.6%**，不是 49.6%。）

### 6. 凡"声称零逻辑变化"的提交，都要**自证** ★

**判据：这个 commit 凭什么让我相信"逻辑没变"？** —— 答案不能是"我看过了"。

适用对象：格式化、改名、注释整理、依赖升级、任何 diff 大到**无法逐行读**的改动。
（本项目已用过一次：Commit B 全仓格式化，90 文件 / +3638 −1631。）

| 技术栈 | 自证方式 | 为什么它比"测试通过"硬 |
|---|---|---|
| Python | 改动前后两份源码各 `ast.parse` → `ast.dump` **逐字节比对** | 测试只覆盖**跑到的路径**；AST 覆盖**每一行** |
| TS / TSX | **`next build` 真的跑一遍** | `tsc --noEmit` 只看类型、eslint 只看规则，**都不覆盖打包** |
| 配置 / 数据文件 | 用**读它的那个工具**读一次（`JSON.parse` / 解析器） | 编辑器"看起来对"不算（坑 53） |

```python
# Python 侧的自证（逐字节比对，不用人眼看 diff）
import ast, io, subprocess
for f in changed_py_files:
    old = subprocess.run(["git", "show", f"HEAD:{f}"], capture_output=True,
                         text=True, encoding="utf-8").stdout
    new = io.open(f, encoding="utf-8").read()
    assert ast.dump(ast.parse(old)) == ast.dump(ast.parse(new)), f"语义变了：{f}"
```

```bash
# TS 侧的自证
cd apps/admin && npx next build     # 构建成功 = 打包/类型/SSR 全过
```

> ⚠️ **只在"声称零逻辑变化"时用** —— 它证明的是"没变"。
> 真的改了逻辑，就该走测试与变异验证（见 §4 与坑 52 的复查清单），
> 而不是拿"AST 不一致"当挡箭牌。

**为什么值得单列一节**：这类提交的 diff **本来就大到读不动**（几千行），
"逐行看一遍"实际上是"扫一眼就点同意"。**没有自证，评审就退化成了信任** ——
而格式化提交恰恰是最容易出现"顺手改了一行逻辑"的地方，因为它看起来最无害。

### 7. 工作区必须干净 —— 特别是**意料之外的未跟踪文件**

- [ ] `git status --porcelain` 必须**空**；有东西就问一句"**这是我这次产生的吗？**"
- [ ] 收尾时**专门扫一遍未跟踪文件**，别只看已跟踪文件的 diff

**为什么**：2026-09-21 收尾时仓库根冒出 4 个 **0 字节**垃圾文件，名字是
`-`、`⚠️`、`**交互式提问**，在`、`（同一族问题见` —— 从名字看，
极可能是**命令文本里的 `>` 被当成了重定向**（以 `>` 开头的 markdown 引用行最容易被这样吞掉）。

它们的特征是**很好认**：**0 字节 + 名字是"一段话"而不是"一个路径"**。危险点在于：

- 不影响构建、不影响测试、`coverage` 也不报 —— **没有任何门禁会说话**
- 但 `git add -A`（收尾提交时最顺手的那条）**会把它们一起提交进仓库**

与坑 44 / 51 / 52 / 53 同族：**都是"没有任何东西会报错"的那一类问题**。
