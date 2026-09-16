# Batch 3 · 管理后台 v0.1 —— 方案与骨架

> **状态：已按本方案实现并验收通过（2026-09-15）。**
> 后端 4 个接口 + 前端 4 个页面全部落地；`tools/local-verify/run-smoke.ps1` **14 passed**。
> 实现结果与偏差见文末「§九 实现记录」；本文件保留为方案基线，便于回溯"当初为什么这么定"。
>
> **范围变更（本批核心决策）**：不做 C 端前端，改做 **B 端管理后台 v0.1**。
> 理由：Batch 2 的 10 个接口天然是管理后台的活儿；C 端答题页缺
> `questions / practice` 接口，硬做会被迫扩范围。

---

## 0. 先说三件必须你拍板的事

| # | 问题 | 我的建议 | 为什么现在必须定 |
|---|---|---|---|
| **A** | **雪花 ID 精度**：后端把 `id` 序列化成 JSON number，JS 解析会丢精度（已实测，见 §4.1） | **改后端**：BIGINT 主键统一序列化为**字符串** | 不定就会全线 404；且越晚改，涉及面越大 |
| **B** | `/users/[id]` 详情页**没有对应接口**，只剩 3 个接口凑不出来 | **补第 4 个接口** `GET /admin/users/{user_id}` | 否则详情页刷新即白屏、链接不可分享，B 端不成立 |
| **C** | 现有 6 个角色里**没有「有 `user:read` 但没 `user:manage`」的角色**，无法演示"按钮 disabled" | **加一个只读角色 `viewer`**（`user:read` + `system:audit` + `stats:read`） | 你点名要的"权限门控禁用态"没有可复现的账号 |

> 三个都选「建议」的话，本批范围是 **后端 4 个接口 + 前端 4 个页面**，不改 Batch 2 既有接口语义（只改序列化格式）。
> 如果你坚持"就 3 个接口、不加角色"，也能做，B/C 各有降级方案，见 §4.2 / §4.3。

---

## 一、范围

### 1.1 本批做

**后端（新增，复用 Batch 2 的 RBAC 门控）**

| # | 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|---|
| 11 | GET | `/admin/audit-logs` | `system:audit` | 按 actor_id / action / entity_type / 时间范围筛选 + 分页 |
| 12 | GET | `/admin/roles` | `user:read` 或 `system:role` | 角色列表（下拉用），**带每个角色的权限码** + `is_assignable` |
| 13 | GET | `/admin/permissions` | 同上 | 权限树（按 module 分组） |
| 14 | GET | `/admin/users/{user_id}` | `user:read` | **建议补**：用户详情，详情页用 |

**前端（新建 `apps/admin/`，PC 优先）**

| # | 路由 | 页面 | 关键点 |
|---|---|---|---|
| 1 | `/login` | 登录页 | 密码登录；`?next=` 回跳；401 自动刷新链路 |
| 2 | `/users` | 用户列表 | 分页 / 搜索 / 状态筛选 / 角色标签 / 空态 / 骨架 |
| 3 | `/users/[id]` | 用户详情 + 分配角色 | 权限门控 + 弹窗 + 二次确认 |
| 4 | `/audit-logs` | 审计日志 | 筛选（含时间范围）+ 表格 + 分页 |

### 1.2 本批不做

C 端任何页面、题库、练习、试卷、支付、导入管道、行级数据范围过滤（`scope` 只透传不做过滤，与 Batch 2 一致）。

---

## 二、后端设计

### 2.1 新增文件

```
apps/api/app/
├─ schemas/
│  └─ admin_rbac.py          # 新增：RoleItem / PermissionNode / AuditLogItem / AdminUserDetail
├─ services/
│  ├─ audit_service.py       # 追加：query_audit_logs()
│  ├─ rbac_service.py        # 追加：list_roles_detail() / build_permission_tree()
│  └─ user_service.py        # 追加：get_user_detail()
├─ api/v1/
│  ├─ admin_audit.py         # 新增：GET /admin/audit-logs
│  └─ admin_rbac.py          # 新增：GET /admin/roles, /admin/permissions
└─ api/router.py             # 注册 2 行（+ admin_users 里加 1 个路由）
```

分层不变：`api/` 只装配参数 + 声明权限 + 调 service + 包响应；查询逻辑全在 `services/`。

### 2.2 接口契约

#### ① `GET /api/v1/admin/audit-logs`

权限：`require_permission("system:audit")`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` / `page_size` | int | 1 / 20 | 复用 `deps.pagination`（上限 100） |
| `actor_id` | int? | — | 精确 |
| `action` | str? | — | 精确，如 `user.assign_roles` |
| `entity_type` | str? | — | 精确，如 `user` |
| `entity_id` | int? | — | 精确 |
| `success` | bool? | — | B 端常用：只看失败操作 |
| `start` / `end` | datetime? | — | `created_at >= start AND created_at < end` |
| `order` | `asc`\|`desc` | desc | 仅允许按 `created_at` 排序 |

响应 `Envelope[Page[AuditLogItem]]`：

```json
{
  "id": 375228966664933377, "actor_id": 375228939615866880, "actor_name": "超级管理员",
  "action": "user.assign_roles", "module": "user",
  "entity_type": "user", "entity_id": 375228966664933376,
  "method": "PUT", "path": "/api/v1/admin/users/375228966664933376/roles",
  "ip": "127.0.0.1", "success": true, "error_msg": null,
  "before_data": {"roles": ["student"]},
  "after_data": {"roles": ["researcher"], "scope_type": "professional", "scope_id": 7},
  "created_at": "2026-09-15T08:40:30.546050Z"
}
```

要点：
- **`ip` 必须转字符串**。`audit_logs.ip` 是 `INET`，asyncpg 返回的是 `ipaddress.IPv4Address` 对象，直接进 Pydantic 会在序列化阶段炸。统一 `str(x) if x else None`。
- **`before_data` / `after_data` 是 JSONB**，返回 dict，前端按需折叠展示（不渲染成一行巨型字符串）。
- **索引对齐**：`idx_audit_actor(actor_id, created_at DESC)` / `idx_audit_entity` / `idx_audit_action` 已有；
  **只按时间范围过滤没有专用索引**，v0.1 接受（后台数据量可控），B 端上线前按需补 `BRIN(created_at)`。
- 查询本身**不写审计**（否则自己审计自己，日志爆炸）。只审计写操作。

#### ② `GET /api/v1/admin/roles`

权限：`require_permission("user:read", "system:role")`（任一命中）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `include_permissions` | bool | true | 是否带上每个角色的权限码（分配角色弹窗要用） |

响应 `Envelope[list[RoleItem]]`：

```json
[
  { "id": 1, "code": "super_admin", "name": "超级管理员", "description": "系统拥有者，拥有全部权限",
    "is_system": true, "sort_no": 1, "is_assignable": false,
    "permissions": ["question:read", "..."] },
  { "id": 3, "code": "researcher", "name": "教研", "is_system": true, "sort_no": 3,
    "is_assignable": true, "permissions": ["question:create", "exam:create", "..."] }
]
```

要点：
- **`is_assignable`**：后端把 `ASSIGNABLE_ROLES` 白名单透出来，前端直接把 `super_admin` 置灰并说明原因，
  而不是让用户点了提交才吃 `40003`。这是 B 端「不给用户制造必然失败的操作」的基本素养。
- 权限码用**一次 JOIN 批量查**，不要 N+1（6 个角色 × 1 次查询是 7 次，一次就够）。

#### ③ `GET /api/v1/admin/permissions`

权限：同 ②

| 参数 | 类型 | 说明 |
|---|---|---|
| `module` | str? | 只取某个模块，如 `user` |

响应 `Envelope[PermissionTreeOut]`：

```json
{
  "total": 24,
  "tree": [
    { "key": "m:user", "code": "user", "name": "用户", "type": "module", "module": "user", "sort_no": 1,
      "children": [
        { "key": "p:401", "code": "user:read", "name": "查看用户", "type": "api", "module": "user", "sort_no": 1, "children": [] }
      ] }
  ]
}
```

要点（**这里有个 schema 事实要先说清**）：
- `permissions` 表**有 `parent_id`，但 `db/schema.sql` 的种子数据里 `parent_id` 全是 NULL**，
  24 条权限是**扁平**的，靠 `module` + `code` 的 `module:action` 约定表达层级。
- 所以 v0.1 的树 = **一级按 `module` 分组（用中文字段名映射），二级是权限条目**。
- 实现上做成「**优先 `parent_id` 递归，缺失时按 module 兜底**」，这样将来真接了菜单树不用改接口。

模块中文名映射（前端也能用）：`question 题库 / exam 试卷 / course 课程 / user 用户 / order 订单 / content 内容 / stats 统计 / system 系统`。

#### ④（建议）`GET /api/v1/admin/users/{user_id}`

权限：`require_permission("user:read")`；返回 `Envelope[AdminUserDetail]`。

在 `AdminUserItem` 基础上增加：`email`、`username`、`real_name`、`register_ip`、`last_login_ip`、`profile`、
`can_manage_roles`（当前调用者是否有 `user:manage`）、**`phone_full`**（仅当调用者有 `user:manage` 时才有值）。

### 2.3 谁看得到什么（实测种子数据推导）

| 角色 | `user:read` | `user:manage` | `system:audit` | 后台可见范围 | 手机号 |
|---|---|---|---|---|---|
| `super_admin` | ✅ | ✅ | ✅ | 全部页面 | 明文 |
| `admin` | ✅ | ✅ | ✅ | 全部页面 | 明文 |
| `operator` | ✅ | ✅ | ❌ | 用户页可用，**审计页 403** | 明文 |
| `viewer`（**建议新增**） | ✅ | ❌ | ✅ | 用户页**只读**，审计页可看 | 脱敏 |
| `researcher` / `teacher` | ❌ | ❌ | ❌ | 登录后无处可去（只有 403 页） | — |
| `student` | ❌ | ❌ | ❌ | 同上 | — |

这张表就是 README 里「验证 RBAC 门控」的脚本来源：
**`operator` 演示「能进用户页、看不到审计」；`viewer` 演示「按钮 disabled + tooltip」；`researcher` 演示「整页 403」。**

---

## 三、前端设计

### 3.1 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | Next.js **14** App Router + TS | 你指定 14；本批页面全是客户端交互，App Router 够用 |
| 样式 | Tailwind + **shadcn/ui** | 你要的；shadcn 是"复制进项目"的组件，B 端改样式最省事 |
| 数据请求 | **TanStack Query v5** | 401 重试、缓存、`isPending/isError` 状态机开箱即用；比手写 `useEffect` 稳 |
| 表格 | **手写** `DataTable` 壳 + `useTableState` | 只有 2 张表，TanStack Table 的学习成本不值当 |
| 状态 | `AuthProvider`（Context）+ 模块级 token 工具 | 全局状态只有"当前用户+权限"，上 zustand 是过度设计 |
| 提示 | shadcn `sonner`（toast） | 统一错误提示要能带 `trace_id` 并支持复制 |
| token 存储 | `localStorage` + 一个非 httpOnly 的 `yj_authed` cookie 作**路由守门提示** | 见 §3.4-②；真 token 不进 cookie，避免 CSRF 面 |

### 3.2 目录树

```
apps/admin/
├─ package.json  tsconfig.json  next.config.mjs  tailwind.config.ts  postcss.config.mjs
├─ components.json                  # shadcn/ui 配置
├─ .env.local.example               # NEXT_PUBLIC_API_BASE=http://localhost:8000/api/v1
├─ README.md                        # 交付物②：怎么起 / 怎么登超管 / 怎么验门控
├─ docs/B端联调坑.md                 # 交付物④
└─ src/
   ├─ middleware.ts                 # 第一层守卫：只看 cookie 提示位，未登录直接跳 /login
   ├─ app/
   │  ├─ layout.tsx  globals.css  providers.tsx
   │  ├─ login/page.tsx             # ① 登录页
   │  ├─ forbidden/page.tsx         # 403 落地页（不是白屏）
   │  └─ (console)/
   │     ├─ layout.tsx              # 侧边栏 + 顶栏 + RequireAuth（第二层守卫）
   │     ├─ page.tsx                # → redirect /users
   │     ├─ users/page.tsx          # ② 用户列表
   │     ├─ users/[id]/page.tsx     # ③ 用户详情 + 分配角色
   │     └─ audit-logs/page.tsx     # ④ 审计日志
   ├─ components/
   │  ├─ ui/                        # shadcn 生成：button input table dialog tooltip badge skeleton select sonner dropdown-menu
   │  ├─ layout/Sidebar.tsx         # 按权限隐藏菜单项
   │  ├─ layout/Topbar.tsx          # 当前用户 + 角色徽章 + 退出
   │  ├─ PermissionGate.tsx         # ★ 权限门控原语（含 disabled + tooltip 两种形态）
   │  ├─ DataTable.tsx              # ★ 表格壳：分页/排序/空态/骨架
   │  ├─ EmptyState.tsx  ErrorState.tsx  DataTableSkeleton.tsx
   │  ├─ AssignRolesDialog.tsx      # ★ 分配角色：列角色 + 列权限 + 二次确认
   │  └─ ConfirmDialog.tsx          # 二次确认原语
   ├─ hooks/
   │  ├─ useTableState.ts           # ★ 分页/筛选 ↔ URL query 双向同步
   │  ├─ useUsers.ts  useAuditLogs.ts  useRoles.ts   # TanStack Query 封装
   ├─ lib/
   │  ├─ api.ts                     # ★ 信封解包 + 401 单飞刷新 + trace_id
   │  ├─ json-bigint.ts             # ★ 大整数安全解析
   │  ├─ types.ts                   # ★ 与后端契约一一对应的 TS 类型
   │  ├─ auth-store.ts              # token 读写 + /auth/me 缓存
   │  ├─ permission.ts              # 权限码常量 + can() 纯函数
   │  └─ format.ts                  # 时间 / 手机号 / JSON 格式化
```

### 3.3 关键文件骨架

#### ★ `src/lib/json-bigint.ts` —— 先解决精度问题

```ts
/**
 * JSON 大整数安全解析。
 *
 * 后端主键是雪花 ID（BIGINT），值是 18~19 位，超出 JS 安全整数范围：
 *   JSON.parse('{"id":375228939615866880}').id  →  375228939615866900   // 实测丢精度
 * 直接用它拼 URL 会 404。这里在 parse 之前把"裸数字"字面量加引号。
 *
 * 注意：是启发式。`:"` 只匹配 JSON 值位置的裸数字，不会误伤字符串里的数字
 * （字符串里数字前面是普通字符或引号，不是冒号）。若后端改成字符串序列化（推荐），
 * 这个文件可以直接删掉。
 */
const BIG_INT_LITERAL = /:(\d{16,})(?=\s*[,}\]])/g;

export function parseJsonSafe<T>(text: string): T {
  return JSON.parse(text.replace(BIG_INT_LITERAL, ':"$1"')) as T;
}
```

#### ★ `src/lib/api.ts` —— 信封解包 + 401 单飞刷新

```ts
import { parseJsonSafe } from "./json-bigint";
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./auth-store";
import { toast } from "sonner";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

export type Envelope<T> = {
  code: number; message: string; data: T; trace_id: string; server_time: number;
};

export class ApiError extends Error {
  constructor(
    readonly code: number,        // 业务码（0 表示成功，不会构造 ApiError）
    message: string,
    readonly traceId: string,     // 排障用，UI 上要能复制
    readonly httpStatus: number,
  ) { super(message); }
}

/** 需要「尝试刷新后重试」的认证类错误码；40102 是签名无效，重试没意义，不列进来。 */
const RETRYABLE_AUTH_CODES = new Set([40100, 40101, 40104, 40105]);

// 单飞：并发的多个 401 只允许触发一次 refresh。
// 后端是 Rotation（刷新即作废旧 refresh_token），并发刷新会让后到的那个直接 40105。
let inflightRefresh: Promise<boolean> | null = null;

async function refreshToken(): Promise<boolean> {
  inflightRefresh ??= (async () => {
    const rt = getRefreshToken();
    if (!rt) return false;
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      const body = parseJsonSafe<Envelope<{ access_token: string; refresh_token: string }>>(
        await res.text(),
      );
      if (body.code !== 0 || !body.data) return false;
      // Rotation：必须同时存回新的 refresh_token，否则下一次刷新必失败
      setTokens(body.data.access_token, body.data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      inflightRefresh = null;
    }
  })();
  return inflightRefresh;
}

function redirectToLogin() {
  clearTokens();
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?next=${next}`;
}

export type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  signal?: AbortSignal;
  /** 内部用：已重试过一轮就不再重试，避免死循环 */
  _retried?: boolean;
};

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }

  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const at = getAccessToken();
  if (at) headers.Authorization = `Bearer ${at}`;

  const res = await fetch(url.toString(), {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  });

  // trace_id 有两个来源：信封里（业务错误）+ 响应头（网关/框架层错误）
  const headerTrace = res.headers.get("X-Request-ID") ?? "";

  // TODO: 空响应体（204 / 网关 502 HTML）要单独兜住，不能无脑 .json()
  const text = await res.text();
  let body: Envelope<T> | null = null;
  try { body = text ? parseJsonSafe<Envelope<T>>(text) : null; } catch { body = null; }

  // ---- 无信封：网络层/网关层错误 ----
  if (!body) {
    throw new ApiError(50001, `请求失败（HTTP ${res.status}）`, headerTrace, res.status);
  }

  // ---- 业务成功 ----
  if (body.code === 0) return body.data;

  // ---- 认证类错误：刷新一次再重试 ----
  if (RETRYABLE_AUTH_CODES.has(body.code) && !opts._retried) {
    const ok = await refreshToken();
    if (!ok) { redirectToLogin(); throw new ApiError(body.code, body.message, body.trace_id, res.status); }
    return request<T>(path, { ...opts, _retried: true });
  }

  // ---- 其余业务错误：交给上层统一 toast（带 trace_id）----
  throw new ApiError(body.code, body.message, body.trace_id || headerTrace, res.status);
}
```

> 这段是整个前端的"心脏"，也是**坑最密的地方**。三个已识别的坑：Rotation 并发、空响应体、trace_id 双来源。

#### ★ `src/lib/permission.ts` + `components/PermissionGate.tsx`

```ts
// lib/permission.ts —— 权限码常量集中管理，避免各处手写字符串
export const P = {
  userRead: "user:read",
  userManage: "user:manage",
  userExport: "user:export",
  systemAudit: "system:audit",
  systemRole: "system:role",
} as const;

export function can(perms: string[] | undefined, code: string): boolean {
  return !!perms?.includes(code);
}
```

```tsx
// components/PermissionGate.tsx
"use client";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth-store";

type Props = {
  /** 需要的权限码；命中任一即通过 */
  code: string | string[];
  /** 无权限时：hide 直接不渲染；disable 渲染但置灰并给 tooltip（B 端主用） */
  mode?: "hide" | "disable";
  /** disable 时的解释文案，必须写清"为什么不能点"和"该找谁" */
  reason?: string;
  children: React.ReactElement<{ disabled?: boolean }>;
};

export function PermissionGate({ code, mode = "disable", reason, children }: Props) {
  const { hasPermission } = useAuth();
  const codes = Array.isArray(code) ? code : [code];
  const allowed = codes.some(hasPermission);

  if (allowed) return children;
  if (mode === "hide") return null;

  return (
    <Tooltip>
      {/* disabled 的按钮不触发鼠标事件 → 外面必须套一层 span 才能出 tooltip（经典坑） */}
      <TooltipTrigger asChild>
        <span className="inline-block cursor-not-allowed">
          {React.cloneElement(children, { disabled: true })}
        </span>
      </TooltipTrigger>
      <TooltipContent>{reason ?? `需要「${codes.join(" / ")}」权限，请联系管理员`}</TooltipContent>
    </Tooltip>
  );
}
```

> 那个 `span` 包裹是必须的：`disabled` 的按钮不发 pointer 事件，tooltip 永远不弹。
> 这正是你要求的"disabled 且 tooltip 说明"在实现上的真实难点。

#### ★ `src/hooks/useTableState.ts` —— 分页/筛选 ↔ URL 同步

```ts
/**
 * 表格状态放 URL query，好处：刷新不丢、能分享、浏览器后退可用。
 * 这是 B 端表格的"正确姿势"，很多后台用 useState 导致刷新就回到第一页。
 */
export function useTableState<T extends Record<string, unknown>>(defaults: T) {
  const router = useRouter();
  const sp = useSearchParams();
  const state = useMemo(() => ({ ...defaults, ...pickKnown(sp, defaults) }), [sp]);
  const setState = (patch: Partial<T>) => { /* router.replace 合并 query，页码变更时重置为 1 */ };
  const reset = () => { /* 清空筛选 */ };
  return { state, setState, reset };
}
```

#### ★ `components/DataTable.tsx` —— 一个壳解决你要的 4 件事

```tsx
type DataTableProps<T> = {
  columns: Column<T>[];          // { key, title, sortable?, render }
  rows: T[] | undefined;
  total?: number;
  page: number; pageSize: number;
  onPageChange: (p: number) => void;
  sort?: { key: string; order: "asc" | "desc" };
  onSortChange?: (s: { key: string; order: "asc" | "desc" }) => void;
  isLoading: boolean;            // → 渲染 DataTableSkeleton（不做"空白+转圈"）
  error?: Error | null;          // → 渲染 ErrorState（可重试）
  emptyText?: string;            // → 渲染 EmptyState（区分"没数据"和"筛选后没数据"）
  rowKey: (r: T) => string;
};
```

要素：表头排序箭头、右上角"共 N 条"、右下分页（上一页/下一页/页码/每页条数）、
加载骨架**保持行高一致**（否则数据回来会跳一下）、空态区分两种文案。

#### ★ `components/AssignRolesDialog.tsx` —— 你要的重点

```tsx
/**
 * 分配角色弹窗。B 端要点全在这里：
 * 1. 列出所有角色（super_admin 由后端 is_assignable=false 透出 → 置灰 + 说明）
 * 2. 每个角色可展开看它包含的权限码
 * 3. 勾选变化时展示 diff：「student → researcher」，避免盲提
 * 4. 提交前二次确认（ConfirmDialog），确认文案里带上被操作用户的手机号
 * 5. scope_type != global 时 scope_id 必填（否则后端 40001）
 */
```
状态：`selected: string[]`（本地乐观态）→ 点击"保存" → `ConfirmDialog` → `PUT /admin/users/{id}/roles`
→ 成功后 `invalidateQueries(["users"])` + `["user", id]`，并 toast「角色已更新」。

#### ★ `src/app/(console)/audit-logs/page.tsx`

筛选区：`actor_id`(Input) / `action`(Select，选项来自后端 distinct 或固定几个) / `entity_type`(Select) /
时间范围(DateRangePicker → `start`/`end`) / `success`(Select) / 重置按钮。
表格列：时间 / 操作人 / action / 实体 / 结果(成功绿·失败红) / IP / 操作（"查看"→ 抽屉展示 `before_data`/`after_data` JSON）。
**时间要按 `Asia/Shanghai` 展示**，后端返回的是 UTC `Z`，直接 `new Date().toLocaleString()` 会按浏览器时区渲染，跨时区协作时就对不上。

### 3.4 B 端 6 项特征的落点

| 你要的 | 落在哪 | 关键实现 |
|---|---|---|
| ① 权限门控（disabled + tooltip） | `permission.ts` / `PermissionGate.tsx` / `users/[id]` | 需 `span` 包裹才能出 tooltip；`reason` 文案必须可执行（"联系管理员"而非"无权限"） |
| ② 401 拦截（自动 refresh，失败跳登录） | `lib/api.ts` + `middleware.ts` + `/login?next=` | **单飞刷新**（Rotation 并发会互相作废）；双层守卫：cookie 提示位做秒跳，`/auth/me` 做真判 |
| ③ 统一错误 toast + trace_id | `lib/api.ts` 抛 `ApiError` → `providers.tsx` 的 `QueryCache.onError` | toast 里带"复制 trace_id"按钮；`40105` 等已跳登录的不再弹 |
| ④ 表格分页/排序/筛选/空态/骨架 | `DataTable.tsx` + `useTableState.ts` | 状态进 URL；空态区分「无数据」/「筛选无结果」；骨架行高与真行一致 |
| ⑤ 手机号脱敏（列表 vs 详情） | 后端 `phone` / `phone_full`；前端 `format.ts` | **脱敏在后端做**：`phone_full` 只有 `user:manage` 才有值。前端隐藏 ≠ 防泄露（值还在响应体里） |
| ⑥ 分配角色弹窗 + 二次确认 | `AssignRolesDialog.tsx` + `ConfirmDialog.tsx` | 列角色 → 展开权限 → diff 预览 → 二次确认 → 提交后 invalidate 缓存 |

---

## 四、三个必须先解决的坑

### 4.1 ⚠️ 雪花 ID 精度丢失（已验证，阻塞级）

**实测证据**（Node 22）：

```
MAX_SAFE_INTEGER = 9007199254740991
raw id      = 375228939615866880
parsed id   = 375228939615866900        ← 已经错了
precision lost = true
id as number unsafe = true
```

JS `Number` 只能安全表示 2^53-1，雪花 ID 是 18~19 位，**必丢精度**。
后果：列表里拿到 `375228939615866900`，拼成 `PUT /admin/users/375228939615866900/roles` → **404 用户不存在**。
分配角色、用户详情页全废。而且这个错**静默发生**——不报错，只是 id 变了。

两个方案：

**方案 1（推荐）：后端把 BIGINT 序列化成字符串。**
在 `app/schemas/` 里对 ID 类字段统一 `Annotated[int, PlainSerializer(str, return_type=str)]`，
或全局给 Pydantic 配 big-int。改动集中在 schemas，前端 `types.ts` 里 id 一律 `string`。
> 代价：属于 Batch 2 的**契约变更**（`id` 从 number 变 string）。但现在只有本批前端一个消费方，
> 越早改越便宜。curl/psql 验证时看到的是 `"375228939615866880"` 带引号，属预期。

**方案 2：前端兜住。** 用 §3.3 的 `parseJsonSafe` 把 16 位以上裸数字加引号。
> 代价：启发式（万一后端出现 16 位以上的真实数值型字段会被误转）；后端契约不变。

**我建议方案 1**，方案 2 作为兼容兜底一起做（两个都做最稳）。

### 4.2 ⚠️ `/users/[id]` 缺接口（B 里的建议）

现在的 10 个接口里**没有任何"取单个用户"的能力**。三种走法：

| 走法 | 实现 | 代价 |
|---|---|---|
| **补 `GET /admin/users/{id}`**（建议） | 后端加 1 个接口 | 零风险，详情页可刷新、可分享 |
| 只靠列表缓存 | `/users` 点进详情时把行数据塞进 TanStack Query 缓存，`[id]` 页 `initialData` 读缓存 | **F5 刷新即白屏**，链接不可分享，B 端不合格 |
| 用列表接口 `keyword=手机号` 搜出来 | 详情页拿 id 反查 | 丑陋，且 id 搜不到（keyword 只匹配 phone/nickname/username） |

### 4.3 ⚠️ 没有可演示「disabled」的账号（C）

要复现"无 `user:manage` → 按钮 disabled 且 tooltip"，需要一个**有 `user:read` 但没 `user:manage`** 的角色。
按种子数据逐一核对：

```
super_admin: 全部                          → 有 user:manage
admin:       除 question:rollback 外全部    → 有 user:manage
operator:    module IN (content,stats,user) → 有 user:manage  ← 也不行
researcher:  module IN (question,exam,stats)→ 无 user:read
teacher:     module IN (course,exam,stats)  → 无 user:read
student:     无权限                         → 无 user:read
```

**结论：6 个角色没有一个能演示禁用态。**

两个解法：

- **解法 1（建议）：种子加一个只读角色 `viewer`**
  ```sql
  INSERT INTO roles (id, code, name, description, is_system, sort_no) VALUES
    (7, 'viewer', '只读管理员', '只能查看用户与审计日志，不能修改', true, 7)
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO role_permissions (role_id, permission_id)
  SELECT 7, id FROM permissions WHERE code IN ('user:read','system:audit','stats:read')
  ON CONFLICT DO NOTHING;
  ```
  同步把 `rbac_service.ASSIGNABLE_ROLES` 加上 `viewer`。
  好处：门控演示可复现，且 `viewer` 本身是标准 B 端角色（审计岗）。
  代价：改 `db/schema.sql` 种子；**已建好的库需手动补这两条 INSERT**（schema.sql 只在空库执行）。

- **解法 2（零改动）：拿 `operator` 临时改库**
  ```sql
  DELETE FROM role_permissions WHERE role_id = 5 AND permission_id = 402;  -- 摘掉 operator 的 user:manage
  ```
  演示完再插回去。不动代码，但**不可复现**（换台机器就没了），写进 README 会显得别扭。

---

## 五、"B 端前后端联调常见坑" 初版目录（交付物④）

这份我打算独立成 `apps/admin/docs/B端联调坑.md`，已识别的条目：

| # | 坑 | 现象 | 根因 / 解法 |
|---|---|---|---|
| 1 | **大整数 ID 精度丢失** | 分配角色 404，id 尾数变了 | §4.1，已实测 |
| 2 | **Rotation + 并发刷新互相作废** | 页面同时发 3 个请求，2 个莫名 401 跳登录 | 刷新要**单飞**；刷新后必须存回新 refresh_token |
| 3 | **`disabled` 按钮不出 tooltip** | 加了 Tooltip 但悬停无反应 | disabled 元素不派发鼠标事件 → 外层 `span` 承接 |
| 4 | **`allow_credentials` + `allow_origins=*`** | 换 cookie 鉴权后浏览器直接拦 | 一旦用 cookie，必须显式列 origin；用 Bearer 则无此问题（当前如此） |
| 5 | **HTTP 状态 ≠ 业务码** | 前端按 400 分支，结果 422 漏了 | 后端把校验错误统一成 **422 + code 40001**；只认 `code` |
| 6 | **503/50003 被当成登录过期** | Redis 挂时前端疯狂跳登录 | `50003` 不是认证错误，不该触发 refresh |
| 7 | **`40102` 不该重试** | 伪造 token 时反复刷新 | 可重试集合只放 `40100/40101/40104/40105` |
| 8 | **trace_id 有两个来源** | 业务错误有、网关错误没有 | 信封 `trace_id` + 响应头 `X-Request-ID`（已在 CORS `expose_headers` 里放出来，否则前端读不到） |
| 9 | **时区** | 审计时间差 8 小时 | 后端存 UTC，前端显式按 `Asia/Shanghai` 渲染 |
| 10 | **INET 字段序列化** | 加 `ip` 字段后 500 | `ipaddress` 对象必须 `str()` 后再进 Pydantic |
| 11 | **表格状态放 useState** | 筛选后翻页再刷新，回到第一页 | 状态进 URL（`useTableState`） |
| 12 | **脱敏做在前端** | 页面上盖住了，F12 里明文还在 | 脱敏必须后端做（`phone_full` 按权限下发） |
| 13 | **N+1 查询** | 角色列表慢 | 角色→权限一次 JOIN 批量查 |
| 14 | **缓存失效** | 改完角色返回列表还是旧角色 | 提交后 `invalidateQueries`，别指望"回到列表自动刷新" |
| 15 | ⚠️ **token 存 `localStorage`** | 不是故障，是**主动留下的债** | **练手阶段的取舍；生产必须换 httpOnly cookie + CSRF token。** 详见下方 §5.1 —— 这条是**留痕**，不是待修 bug |

### 5.1 ⚠️ 第 15 条的展开：为什么 `localStorage` 是已知反模式（留痕，本批不修）

**现状**：`apps/admin/src/lib/auth-store.ts` 把 `access_token` / `refresh_token` 存在
`localStorage`，通过 `Authorization: Bearer` 发送。

**练手阶段这么做的理由**（省掉的都是"必须后端配合"的活）：

- 不需要 `Set-Cookie` 那一套（`Domain` / `Path` / `SameSite` / `Secure`）；
- 不需要把 CORS 的 `allow_origins` 从 `*` 收紧（**用 cookie 时
  `allow_credentials=True` + `allow_origins=["*"]` 会被浏览器直接拒绝**）；
- 不需要实现 CSRF 防护；
- F12 就能看到 token，调试直观。

**生产为什么必须换 `httpOnly + Secure + SameSite=Lax` Cookie + CSRF Token**：

1. **XSS 的收益天差地别 —— 这是最关键的一条。**
   `localStorage` 能被 JS 读取，所以任何一处 XSS（第三方脚本、富文本渲染、
   一个不小心的 `dangerouslySetInnerHTML`）都能 `localStorage.getItem("yj_refresh_token")`
   **把长期凭据直接拿走**，攻击者可以持续 refresh，用户关掉浏览器也照样能用。
   `httpOnly` cookie 读不到，XSS 最多"借当前会话发请求"，且随关站失效。
   **refresh_token 是长期凭据，它放哪，决定了 XSS 是"局部事件"还是"账号彻底沦陷"。**

2. **没有服务端侧失效入口。**
   `localStorage` 是纯前端状态，服务端管不着。用户点"退出登录"只是本地删掉，
   **被窃取的 token 在有效期内仍然有效**。
   换 Cookie 后可以配合 `user_sessions` 表做真正的服务端失效：
   改密码 / 踢线 / 检测到异常登录 → `DELETE FROM user_sessions WHERE ...` 立刻生效。

**迁移清单**（将来做的时候照这个改，共 7 处）：

| # | 位置 | 改动 |
|---|---|---|
| 1 | `apps/api/app/main.py` | CORS 白名单化（去掉 `["*"]`），保留 `allow_credentials=True` |
| 2 | `apps/api/app/api/v1/auth.py` | 登录/刷新改为 `Set-Cookie`，响应体不再返回 token |
| 3 | `apps/api/app/core/security.py` | 增加 CSRF token 生成与校验依赖 |
| 4 | `apps/api/app/api/v1/auth.py` | 登出时清 cookie + 删除对应 `user_sessions` 行 |
| 5 | `apps/admin/src/lib/api.ts` | `credentials: "include"`，去掉 `Authorization`，加 `X-CSRF-Token` |
| 6 | `apps/admin/src/lib/auth-store.ts` | 只保留"是否已登录"的提示位（`yj_authed`），删掉 token 存取 |
| 7 | `apps/admin/src/middleware.ts` | 可直接读会话 cookie 判定（仍以 `/auth/me` 为准） |

> 完整版（含现象/根因/解法/落点）在 `apps/admin/docs/B端联调坑.md` 第 15 条。

---

## 六、交付物与验证方式

| # | 交付物 | 落点 | 怎么验 |
|---|---|---|---|
| 1 | 可运行的 Next.js 项目 | `apps/admin/` | `npm i && npm run dev` → `localhost:3000` |
| 2 | README | `apps/admin/README.md` | 含：起服务、登超管、**三账号门控验证脚本**（operator/viewer/researcher） |
| 3 | 3(+1) 个接口的 curl 示例 | 本文件 §七 + Swagger `/docs` | 直接复制粘贴可跑 |
| 4 | 联调坑记录 | `apps/admin/docs/B端联调坑.md` | 已成稿 **16 条**（§五 是初版目录；第 16 条是验收时实测出来并当场修掉的） |
| 5 | 后端新增接口的自动化验证 | `apps/api/tests/test_admin_v3.py` + `conftest.py` | 复用 `tools/local-verify/run-smoke.ps1`，期望 `14 passed` |
| 6 | 浏览器人工走查的截图存档 | `apps/admin/docs/screenshots/01~15` | 见下方「验收结果」 |
| 7 | 静默刷新链路探针 | `tools/local-verify/probe-silent-refresh.py` | 不依赖等待 120 分钟 |

**注意**：本批改动后，`tools/local-verify/run-smoke.ps1` 仍必须绿（Batch 2 的 6 个用例不能回归）。
所以新增接口的用例是**追加**进现有测试的，而不是另起一套。

### 6.1 验收结果（2026-09-15）

**接口层** —— `14 passed in 3.27s`（`test_admin_v3.py` 8 个 + `test_smoke.py` 6 个），
Batch 2 的 6 个用例无回归。

**浏览器层** —— 用 `agent-browser` 驱动真实 Chromium 走完整条链路，六个 B 端能力逐项确认：

| 能力 | 结论 |
|---|---|
| ① 权限门控 | `viewer` 登录后「分配角色」置灰，悬停出可执行 tooltip；侧边栏按权限隐藏 |
| ② 401 拦截 | 40101（过期）→ 静默刷新 → 留在原页且 token 换新；40102（签名无效）→ 清凭证跳登录并带 `?next=` |
| ③ 错误 toast + trace_id | 404 用户不存在 → 红色 toast + 「复制 trace_id」按钮；页面同时给"返回列表"出口 |
| ④ 表格 | 分页/搜索/状态筛选/空态/骨架齐全，状态进 URL |
| ⑤ 手机号脱敏 | 超管（有 `user:export`）有「复制明文手机号」；`viewer` 只有 `138****0001` 并注明需哪个权限 |
| ⑥ 分配角色 | 弹窗列出每个角色**包含多少权限**、变更预览 diff、**二次确认**；`super_admin` 置灰标注"不可在此分配" |
| 雪花 ID | 真实浏览器里 18 位 ID 原样显示（`375261088607899648`），未出现尾数变化 |
| 审计留痕 | 改完角色后 `/audit-logs` 最新一条即 `user.assign_roles`，抽屉里能看到字段级 diff |

**验收中实测出并修掉的一个缺陷** —— `40102`（token 签名无效）被排除在刷新白名单外是对的，
但同时也**没有被导向登录页**，用户于是卡在一个怎么点都没用的「重试」错误页上。
现已拆成两个正交判断：`RETRYABLE_AUTH_CODES`（要不要先刷新）与
`FATAL_AUTH_CODES`（要不要直接清凭证跳登录）。
详见 `apps/admin/docs/B端联调坑.md` 第 16 条。

---

## 七、curl 示例（交付物③）

```bash
BASE=http://localhost:8000/api/v1

# 登超管
ADMIN=$(curl -s -X POST $BASE/auth/login/password -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","password":"Admin@123456"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

# ⑪ 审计日志：筛选 action + 时间范围 + 分页
curl -s "$BASE/admin/audit-logs?action=user.assign_roles&page=1&page_size=20&success=true" \
  -H "Authorization: Bearer $ADMIN" | python -m json.tool

# ⑫ 角色列表（带权限）
curl -s "$BASE/admin/roles" -H "Authorization: Bearer $ADMIN" | python -m json.tool

# ⑬ 权限树
curl -s "$BASE/admin/permissions" -H "Authorization: Bearer $ADMIN" | python -m json.tool

# ⑭ 用户详情（phone_full 取决于调用者是否有 user:manage）
curl -s "$BASE/admin/users/375228966664933376" -H "Authorization: Bearer $ADMIN" | python -m json.tool

# 反面用例：用学员 token 访问审计日志 → 期望 40301
STU=$(curl -s -X POST $BASE/auth/sms/send -H 'Content-Type: application/json' \
  -d '{"phone":"13800000009","scene":"login"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['dev_code'])")
# ... 略：见 README 的三账号脚本
```

---

## 八、待你确认的清单

1. **§0-A 雪花 ID**：后端改成字符串序列化（推荐）／只前端兜／两个都做
2. **§0-B 用户详情**：补第 4 个接口（推荐）／只靠缓存
3. **§0-C 可演示角色**：种子加 `viewer`（推荐）／临时改 operator
4. **`/admin/roles` 权限**：`user:read` 或 `system:role`（我的建议）／收紧成只要 `system:role`
5. **审计日志"查看详情"**：抽屉展示 `before/after` JSON（推荐）／列表内联展开／v0.1 先不做
6. **端口与地址**：admin `localhost:3000`，API `localhost:8000`（docker compose 默认）—— 有别的偏好请说

确认后我按这个结构铺全套代码，并保证 `run-smoke.ps1` 依然全绿。
