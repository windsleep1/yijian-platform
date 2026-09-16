# 一建通 · 管理后台 v0.1（Batch 3）

一级建造师学习备考平台的 **B 端管理后台**。本批交付 4 个页面 + 后端 4 个接口的联调闭环。

技术栈：Next.js 14（App Router）+ TypeScript + Tailwind + shadcn/ui + TanStack Query v5。

---

## 1. 快速开始

### 1.1 先起后端

```bash
# 方式 A：有 Docker
cd deploy && docker compose up -d --build

# 方式 B：没有 Docker（本机 PostgreSQL，Windows）
powershell -ExecutionPolicy Bypass -File tools/local-verify/serve-local.ps1
# 起 PG + 建库 + 重放 RBAC 种子 + 初始化超管 + 起 API，并**保持运行**。
# 注意：这种方式 API 在 8123 端口，不是 8000

# 想前台看实时访问日志时：
powershell -ExecutionPolicy Bypass -File tools/local-verify/serve-local.ps1 -Foreground
# 停止：... serve-local.ps1 -Stop
```

> ⚠️ `serve-local.ps1` 会设置 `JWT_SECRET=local-verify-secret-not-for-production`。
> 如果你要自己签 token 做测试（比如 §5.2 的静默刷新探针），必须用同一个密钥 ——
> 它和 `app/core/config.py` 里的 `DEV_SECRET` 默认值**不是**同一个。

后端起来后确认一下：

```bash
curl -s http://localhost:8000/api/v1/health   # 或 :8123
# {"code":0,...,"data":{"dependencies":{"postgres":"up","redis":"up"}}}
```

### 1.2 再起前端

```bash
cd apps/admin
cp .env.local.example .env.local     # 按上面选的后端端口改 NEXT_PUBLIC_API_BASE
npm install
npm run dev
```

打开 <http://localhost:3000>，未登录会自动跳到 `/login`。

> 用 `run-smoke.ps1` 起后端时，`.env.local` 要写成
> `NEXT_PUBLIC_API_BASE=http://localhost:8123/api/v1`。

### 1.3 登录超管

| 项 | 值 |
|---|---|
| 手机号 | `13800000000` |
| 密码 | `Admin@123456` |

这个账号由 `python -m app.cli seed-admin` 创建（docker compose 启动时自动执行）。
如果你直接连了一个空的库、登录报"账号不存在"，手动跑一次：

```bash
cd apps/api
DATABASE_URL='postgresql+asyncpg://yijian@127.0.0.1:5432/yijian' \
ADMIN_INIT_PHONE=13800000000 ADMIN_INIT_PASSWORD=Admin@123456 \
python -m app.cli seed-admin
```

如果登录后「角色」下拉是空的、或分配 `viewer` 报「角色不存在」，说明库里的角色种子不全
（`init-db` 只在**空库**跑 schema.sql，老库拿不到后加的角色）：

```bash
python -m app.cli seed-rbac     # 幂等重放角色/权限种子
```

---

## 2. 四个页面

| 路由 | 说明 | 需要的权限 |
|---|---|---|
| `/login` | 密码登录，支持 `?next=` 回跳 | — |
| `/users` | 用户列表：分页 / 关键词搜索 / 状态筛选 / 角色标签 | `user:read` |
| `/users/[id]` | 用户详情 + 分配角色（弹窗 + 二次确认） | `user:read`；改角色另需 `user:manage` |
| `/audit-logs` | 审计日志：多条件筛选 + diff 抽屉 | `system:audit` |
| `/forbidden` | 403 落地页（说明缺哪个权限、当前角色、该找谁） | — |

---

## 3. 验证 RBAC：三个账号走一遍

本批要验证的"权限门控"有三种形态，**分别需要三种角色**。下面是完整的复现脚本。

### 3.0 准备三个测试账号

**最快的方式（推荐）**——一条命令建好 `viewer` 账号并分配角色，
同时它会写入一条 `user.assign_roles` 审计记录（正好用来验收审计页）：

```bash
powershell -ExecutionPolicy Bypass -File tools/local-verify/seed-demo-users.ps1
# 幂等，可重复执行。输出里会打印 viewer 的手机号 / 密码 / user_id
```

建出来的账号：`13900000001 / Viewer@123456`，角色 `viewer`。

**手动方式**：用超管登录后，在 `/users` 页面把账号分别设成不同角色。
没有现成账号的话，先用 C 端接口注册（注册接口不需要登录）：

```bash
BASE=http://localhost:8000/api/v1
register() {
  CODE=$(curl -s -X POST $BASE/auth/sms/send -H 'Content-Type: application/json' \
    -d "{\"phone\":\"$1\",\"scene\":\"register\"}" | python -c "import sys,json;print(json.load(sys.stdin)['data']['dev_code'])")
  curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
    -d "{\"phone\":\"$1\",\"code\":\"$CODE\",\"password\":\"Passw0rd123\"}"
}

register 13900000001   # → 待设为 operator
register 13900000002   # → 待设为 viewer
register 13900000003   # → 待设为 researcher
```

然后在后台 `/users` 里搜到这三个号，逐个「分配角色」。
（也可以直接用接口，见 §4 的 curl 示例。）

### 3.1 `operator` —— 能进用户页，**看不到审计日志**

`operator` 的权限是 `content + stats + user` 三个模块，有 `user:read`、`user:manage`，
但**没有** `system:audit`。

| 预期 | 观察点 |
|---|---|
| 侧边栏只剩「用户管理」一项 | **菜单按权限隐藏** —— 看得到就能进，不给"点了才 403"的误导 |
| `/users` 正常显示 | 有 `user:read` |
| `/users/[id]` 的「分配角色」**可点** | 有 `user:manage` |
| 详情页手机号显示**明文** | 有 `user:export` |
| 手动访问 `/audit-logs` → 整页 403 | 缺 `system:audit`，且页面写清"缺哪个权限" |

### 3.2 `viewer` —— 用户页**只读**，按钮置灰 + tooltip ★

这是本批专门为"演示禁用态"加的角色（`user:read` + `system:audit` + `stats:read`）。
**种子里的另外 6 个角色没有一个能复现这个状态**：

```
super_admin  全部权限                      → 有 user:manage
admin        除 question:rollback 外全部    → 有 user:manage
operator     content + stats + user        → 有 user:manage   ← 也不行
researcher   question + exam + stats       → 没有 user:read
teacher      course + exam + stats         → 没有 user:read
student      无权限                         → 没有 user:read
```

| 预期 | 观察点 |
|---|---|
| 侧边栏有「用户管理」和「审计日志」两项 | 两个权限都有 |
| `/users` 正常显示 | |
| 详情页「分配角色」**置灰**，**悬停出现 tooltip** | 缺 `user:manage`。tooltip 文案要说清"缺什么权限 + 找谁开通" |
| 详情页手机号**只有脱敏值**，旁边写"需 user:export 才能查看明文" | 后端根本没下发 `phone_full` |
| `/audit-logs` 可以看 | 有 `system:audit` |
| 试图绕过前端直接 `PUT .../roles` | 后端返回 **40301** —— 前端门控是体验，后端才是墙 |

> 想验证"置灰只是体验、后端才是真的"，用 viewer 的 token 直接打接口：
> ```bash
> curl -s -X PUT $BASE/admin/users/<某个id>/roles \
>   -H "Authorization: Bearer $VIEWER" -H 'Content-Type: application/json' \
>   -d '{"role_codes":["admin"]}'
> # {"code":40301,"message":"没有操作权限（需要：user:manage）",...}
> ```

### 3.3 `researcher` —— 登录成功但**无处可去**

| 预期 | 观察点 |
|---|---|
| 登录页提示"登录成功，但当前角色没有任何后台权限" | 不让用户对着空白页发愣 |
| 侧边栏显示"当前角色没有任何后台菜单可访问" | 给出角色名和下一步 |
| 手动访问 `/users` → 403 | |

---

## 4. 后端接口 curl 示例（Batch 3 新增 4 个）

```bash
BASE=http://localhost:8000/api/v1      # run-smoke 路径改成 :8123

# 登超管拿 token
ADMIN=$(curl -s -X POST $BASE/auth/login/password -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","password":"Admin@123456"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

H="Authorization: Bearer $ADMIN"

# ⑪ 审计日志：按 action 过滤 + 只看成功 + 分页
curl -s "$BASE/admin/audit-logs?action=user.assign_roles&success=true&page=1&page_size=20" -H "$H" | python -m json.tool

# ⑪b 按时间范围（半开区间 [start, end)，UTC）与对象过滤
curl -s "$BASE/admin/audit-logs?entity_type=user&entity_id=375228966664933376&start=2026-09-01T00:00:00Z" -H "$H" | python -m json.tool

# ⑫ 角色列表（带每个角色的权限码 + is_assignable）
curl -s "$BASE/admin/roles" -H "$H" | python -m json.tool
curl -s "$BASE/admin/roles?include_permissions=false" -H "$H" | python -m json.tool   # 少一次 JOIN

# ⑬ 权限树（按模块分组）
curl -s "$BASE/admin/permissions" -H "$H" | python -m json.tool
curl -s "$BASE/admin/permissions?module=user" -H "$H" | python -m json.tool

# ⑭ 用户详情：注意 id 是**字符串**，phone 永远脱敏，phone_full 只在有 user:export 时有值
curl -s "$BASE/admin/users/375228966664933376" -H "$H" | python -m json.tool

# 分配角色（整体替换）
curl -s -X PUT "$BASE/admin/users/375228966664933376/roles" -H "$H" \
  -H 'Content-Type: application/json' -d '{"role_codes":["viewer"]}' | python -m json.tool

# 反面用例：学员 token 访问审计日志 → 期望 40301
# 反面用例：不存在的用户 → HTTP 404 + code 40401（不是 200 + 空对象）
curl -s -i "$BASE/admin/users/999999999999999999" -H "$H" | head -1
```

Swagger 在 <http://localhost:8000/docs>，14 个接口都有中文 summary 和 description。

---

## 5. 验收

### 5.1 接口层：一条命令跑 pytest

```bash
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
```

一条命令完成：起 PostgreSQL → 载入 schema → 重放 RBAC 种子 → 初始化超管 → 起 API → 跑 pytest → 收尾。
**期望 `14 passed`**：

```
tests/test_admin_v3.py  8 个   ← 本批新增（用户详情 / viewer 流程 / 角色权限契约 / 审计筛选）
tests/test_smoke.py     6 个   ← Batch 2 的认证链路 + RBAC（不能回归）
```

> `run-smoke.ps1` 用的是 8123 端口，并且**结束时会把 PostgreSQL 停掉**。
> 如果你已经用 `serve-local.ps1` 起着后端，别同时跑它；
> 可以只跑测试（复用正在运行的 API）：
>
> ```bash
> cd apps/api
> AI_BASE=http://127.0.0.1:8123 python -m pytest tests -v
> ```

### 5.2 浏览器层：静默刷新链路探针

`access_token` 的 TTL 是 120 分钟，"等它过期"没法用来验证刷新链路。
这个探针用 dev 密钥签一个**已过期但签名合法**的 token，塞进浏览器再导航，
断言"没被踢到登录页"且"access_token 换新了"：

```bash
python tools/local-verify/probe-silent-refresh.py
```

需要先有一个开着的浏览器会话（`agent-browser open http://localhost:3000`）。
细节与它查出来的那个 bug 见 `docs/B端联调坑.md` 第 16 条。

### 5.3 人工走查：六个 B 端能力

按这个顺序点一遍，六个能力都能看到（截图存档在 `docs/screenshots/`）：

| # | 能力 | 怎么点 | 期望 |
|---|---|---|---|
| ① | 权限门控 | 用超管登录 | 侧边栏两项都在；`/users/[id]` 的「分配角色」可点 |
| ① | 权限门控 | 换成 `viewer` 登录 | 「分配角色」**置灰**，悬停出 tooltip；手机号只有脱敏值 |
| ② | 401 拦截（刷新） | `python tools/local-verify/probe-silent-refresh.py` | 停在原页，token 换新 |
| ② | 401 拦截（跳登录） | F12 把 `yj_access_token` 改成垃圾，刷新 | 跳 `/login?next=...`，token 被清空 |
| ③ | 错误 toast + trace_id | 访问 `/users/999999999999999999` | "用户不存在" + 「复制 trace_id」按钮 |
| ④ | 表格 | `/users` 翻页 / 搜索 / 切状态；搜一个不存在的词 | 页码进 URL；空态区分"没数据"与"筛没了" |
| ⑤ | 手机号脱敏 | 超管看详情 vs `viewer` 看详情 | 超管有「复制明文手机号」；viewer 只有 `138****0001` |
| ⑥ | 分配角色 | `/users/[id]` → 分配角色 | 弹窗列出每个角色**包含多少权限** + 变更预览 + **二次确认** |
| — | 审计留痕 | 改完角色去 `/audit-logs` | 最新一条 `user.assign_roles`；点「查看」出 diff 抽屉 |

---

## 6. 已知限制

| # | 限制 | 说明 |
|---|---|---|
| 1 | token 存 `localStorage` | **练手阶段的取舍，生产应换 httpOnly cookie + CSRF token。** 为什么见 `docs/B端联调坑.md` 第 15 条 |
| 2 | 路由守卫是"双层但非安全边界" | `middleware.ts` 只看一个可伪造的提示 cookie；真正的判定是 `/auth/me` + 后端 RBAC |
| 3 | 无行级数据范围过滤 | `scope_type/scope_id` 只透传不过滤，与 Batch 2 保持一致（Batch 4 落地） |
| 4 | 审计的时间范围筛选无专用索引 | 只按 `created_at` 过滤走不到索引，v0.1 数据量可控；上线前补 `BRIN(created_at)` |
| 5 | 操作日志列表不支持模糊搜索 | `action` / `entity_id` 都是精确匹配（后端如此）；需要模糊再加 `ILIKE` |
| 6 | 分配角色不支持"追加"语义 | 后端是整体替换（`PUT`），弹窗里已明确提示 |

---

## 7. 目录速查

```
apps/admin/
├─ src/middleware.ts                  第一层守卫（cookie 提示位）
├─ src/app/
│  ├─ login/ forbidden/               公开页
│  └─ (console)/                      带侧边栏的控制台
│     ├─ layout.tsx                   ★ 第二层守卫 RequireAuth
│     ├─ users/ users/[id]/ audit-logs/
├─ src/components/
│  ├─ PermissionGate.tsx              ★ 权限门控（disabled + tooltip）
│  ├─ DataTable.tsx                   ★ 表格壳（分页/排序/空态/骨架）
│  ├─ AssignRolesDialog.tsx           ★ 分配角色（列权限 + diff + 二次确认）
│  ├─ AuditDiffDrawer.tsx             ★ 审计 diff 抽屉
│  └─ ui/                             shadcn/ui 组件
├─ src/hooks/                          useTableState / useUsers / useAuditLogs / useRoles
├─ src/lib/
│  ├─ api.ts                          ★ 信封解包 + 401 分流（刷新 / 跳登录）+ trace_id
│  ├─ json-bigint.ts                  ★ 大整数 ID 解析安全带（防御垫）
│  ├─ types.ts                        与后端契约一一对应
│  └─ auth-store.ts / auth-context.tsx / permission.ts / format.ts
├─ docs/
│  ├─ B端联调坑.md                     16 条踩过的坑
│  └─ screenshots/                    人工走查的截图存档（01~15）
```

配套文档：**`docs/B端联调坑.md`** —— 16 条前后端联调踩过的坑（含雪花 ID、Rotation 并发、
disabled 不出 tooltip、时区、脱敏位置、401 分流等）。

配套脚本：

| 脚本 | 用途 |
|---|---|
| `tools/local-verify/serve-local.ps1` | 起后端并**保持运行**（联调用） |
| `tools/local-verify/run-smoke.ps1` | 一键验收：起环境 → pytest → 收尾 |
| `tools/local-verify/seed-demo-users.ps1` | 建 `viewer` 演示账号 + 写一条角色变更审计 |
| `tools/local-verify/probe-silent-refresh.py` | 浏览器端静默刷新链路探针 |

---

## 8. 常用命令

```bash
npm run dev         # 开发（:3000）
npm run build       # 生产构建
npm run start       # 跑构建产物
npm run typecheck   # 只做类型检查（tsc --noEmit）
npm run lint        # ESLint
```
