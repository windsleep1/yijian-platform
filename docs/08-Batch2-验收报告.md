# Batch 2 验收报告 · 认证 + RBAC + 分层骨架

> 交付时间：2026-09-15 ｜ 版本：`apps/api/app/__init__.py` → `0.2.0`
> 范围约定（来自需求）：**只做认证 + RBAC + 分层骨架，接口 ≤ 10 个，必须能 `docker compose up` 跑通。**

---

## 一、交付范围

### 1.1 接口清单（**共 10 个**，达标）

| # | 方法 | 路径 | 鉴权 | 权限码 / 限制 | 说明 |
|---|---|---|---|---|---|
| 1 | GET | `/api/v1/health` | 公开 | — | 探活 app / postgres / redis，退化时 `status=degraded` 但仍 200 |
| 2 | POST | `/api/v1/auth/sms/send` | 公开 | 20 次/分/IP | 发验证码；`mock` 通道回显 `dev_code` |
| 3 | POST | `/api/v1/auth/register` | 公开 | 10 次/分/IP | 注册即登录，自动授予 `student` |
| 4 | POST | `/api/v1/auth/login/password` | 公开 | 20 次/分/IP + 失败锁定 | 连续 5 次错误锁定 15 分钟 |
| 5 | POST | `/api/v1/auth/login/sms` | 公开 | 20 次/分/IP | 未注册自动建号（降低工地扫码门槛） |
| 6 | POST | `/api/v1/auth/refresh` | refresh_token | 30 次/分/IP | **Rotation**：刷新即作废旧凭证 |
| 7 | POST | `/api/v1/auth/logout` | Bearer | — | 支持 `all_devices=true` 全端下线 |
| 8 | GET | `/api/v1/auth/me` | Bearer | — | 账号 + 档案 + 角色 + 权限码 + 数据范围 |
| 9 | GET | `/api/v1/admin/users` | Bearer | `user:read` | 用户列表，手机号脱敏 |
| 10 | PUT | `/api/v1/admin/users/{user_id}/roles` | Bearer | `user:manage` | **整体替换**角色，写审计 + 清权限缓存 |

> 其余业务接口（科目/题库/刷题/模考）按需求留给 Batch 4，本批不铺开。

### 1.2 分层骨架

```
apps/api/app/
├─ main.py            应用入口：TraceId 中间件 → CORS → 异常处理 → 路由挂载
├─ cli.py             运维命令：wait-db / wait-redis / init-db / seed-admin / seed-questions
├─ core/              横切能力（不依赖 services / api）
│   ├─ config.py      Pydantic Settings；prod 环境拒绝默认密钥
│   ├─ security.py    bcrypt 哈希 + JWT 签发/校验（access 2h / refresh 30d）
│   ├─ deps.py        DB会话 / Redis / 当前用户 / require_permission / rate_limit / 分页
│   ├─ errors.py      BizError + 全局处理器；错误码分段
│   ├─ response.py    统一信封 Envelope / Page / TraceId 中间件
│   └─ idgen.py       雪花 ID（BIGINT）+ INET 安全解析
├─ db/                引擎与会话、Redis 单例、ORM 模型（只描述结构）
├─ schemas/           入参出参模型（只做校验与序列化）
├─ services/          业务逻辑（不感知 HTTP，可脱离 FastAPI 单测）
│   ├─ auth_service.py   注册/登录/刷新/登出
│   ├─ rbac_service.py   权限加载（Redis 缓存 300s）/ 角色分配 / 缓存失效
│   ├─ sms_service.py    验证码风控（冷却 + 日限）
│   ├─ user_service.py   管理端用户列表 / 角色分配
│   └─ audit_service.py  审计留痕（失败不影响主流程）
└─ api/v1/            接口层（只做参数装配 + 权限声明 + 调 service + 包响应）
    ├─ health.py  auth.py  admin_users.py
```

**分层铁律**：`api/` 不写业务、不直接查库；`services/` 不 import `fastapi`；`db/models` 不写业务方法。

---

## 二、关键设计决策

| 决策点 | 做法 | 为什么 |
|---|---|---|
| refresh token 存储 | 只存 **SHA-256 摘要** 到 `user_sessions` | 库泄露也无法直接冒用登录态 |
| 令牌刷新 | **Rotation**：作废旧会话 + 签发新会话 | 一次性 refresh token，天然抗重放 |
| 权限缓存 | Redis `rbac:priv:{uid}`，TTL 300s；改角色即删 | 权限变更立即生效，无需等缓存过期 |
| 权限模型 | `user → user_roles(带 scope) → role → role_permissions → permission` | 为「教研只管某科目」这类数据范围预留 |
| 越权提权防护 | `super_admin` **只能由 CLI 授予**，后台不可分配 | 后台被拖库也拿不到最高权限 |
| 主键 | 雪花 ID（BIGINT），非自增 | 便于将来分库与批量预分配 |
| 限流降级 | Redis 挂掉时**放行并记日志** | 限流不该成为可用性单点 |
| 验证码降级 | Redis 挂掉时返回 **503 + 统一信封**（不吐 500 堆栈） | 验证码必须落 Redis，无法降级，但要给前端可读错误 |
| 登录失败提示 | 统一返回「手机号或密码不正确」 | 防止通过错误文案枚举已注册手机号 |
| 短信通道 | `SMS_PROVIDER=mock`，非生产环境回显 `dev_code` | 不申请短信服务也能跑通全链路 |
| 时间基准 | 每个响应都带 `server_time` | 考试倒计时以服务端为准，防改本地时间作弊 |

### 统一响应体（全站一致）

```json
{ "code": 0, "message": "ok", "data": {}, "trace_id": "…", "server_time": 1789458171000 }
```

错误码分段：`40001+` 参数 ｜ `40101+` 认证 ｜ `40301+` 权限 ｜ `40401+` 不存在 ｜
`40901+` 冲突 ｜ `42901+` 限流 ｜ `50001+` 服务端（`50003` = 依赖不可用，HTTP 503）。

`trace_id` 同时写入响应头 `X-Request-ID`，便于按请求排障。

---

## 三、本批实测结果

> 编写环境**未安装 Docker**（`docker` 命令不存在），因此容器编排的实机启动由使用者按
> 第四节命令执行。
>
> 但「没 Docker」不等于「没验证」：本轮已用**本机真实 PostgreSQL 16.15 + 真实 uvicorn**
> 把整条链路端到端跑通（见 3.5），并因此抓出 2 个离线测试**根本测不到**的致命缺陷（见 3.6）。
> 不依赖 Docker 的复验工具在 `tools/local-verify/`，用法见该目录 `README.md`。

### 3.1 语法编译 ✅

对 `apps/api` 全量 `python -m compileall`，退出码 0，无语法错误。

### 3.2 模块/路由冒烟 ✅ 9/9 通过

用隔离 venv 安装真实依赖后执行：

| 检查项 | 结果 |
|---|---|
| `app.core.config` 加载（含 prod 密钥校验） | ✅ |
| bcrypt 哈希 + 校验、JWT access/refresh 往返 | ✅ |
| 雪花 ID 单调递增、`to_inet` 非法 IP 返回 None | ✅ |
| 统一信封字段集、分页 `has_more` 边界 | ✅ |
| ORM `User` 列集合与 schema 一致 | ✅ |
| Pydantic 校验（非法手机号被拦截） | ✅ |
| `Privileges.has / has_all` | ✅ |
| **路由收集 → 恰好 10 个 v1 接口** | ✅ |
| OpenAPI schema 可生成（10 paths） | ✅ |

### 3.3 HTTP 层冒烟（ASGI 直驱，无需 DB/Redis）✅ 9/9 通过

| 用例 | 结果 |
|---|---|
| 依赖全挂时 `/health` 仍 200 + `degraded`，`trace_id` 与响应头一致 | ✅ |
| 自定义 `X-Request-ID` 被透传回显 | ✅ |
| 无 token 访问 `/auth/me` → `401` / `40100` | ✅ |
| 伪造 token → `401` / `40102` | ✅ |
| 参数校验失败 → **422 + 统一信封 `40001`**（非 FastAPI 默认体） | ✅ |
| 未知路径 → `404` / `40401` | ✅ |
| `/openapi.json` 可访问，10 个 v1 接口 | ✅ |
| `/` → `307` 跳 `/docs` | ✅ |
| **Redis 不可用时发短信 → `503` / `50003` 干净降级** | ✅ |

### 3.4 静态一致性校验 ✅

| 检查项 | 结果 |
|---|---|
| `db/schema.sql` 解析 | 64 表 + 3 视图 + 102 索引，1508 行 |
| 8 个 ORM 模型 ↔ schema 列比对 | 无缺失、无多余 |
| 服务层原生 SQL 引用列（rbac/auth/user） | 全部可解析到真实列 |
| 种子角色 | `super_admin / admin / researcher / teacher / operator / student` 齐全 |
| 种子权限 | `user:read`(401)、`user:manage`(402)、`question:create` … 齐全 |
| 角色-权限映射 | `admin`(2)=除 `question:rollback` 外全部（含 user:read/manage）；`researcher`(3)=question/exam/stats；`student`(6)=**无权限** → 访问管理端必 403 |

> 3.4 的结论与 `tests/test_smoke.py` 的断言完全对应，说明冒烟用例在真库上应当通过。

### 3.5 真实 PostgreSQL + 真实 uvicorn 端到端验收 ✅ 6/6 通过

不依赖 Docker 的替代路径：conda-forge 装 PostgreSQL 16.15（独立前缀，免管理员）→
`initdb` + 在 `127.0.0.1:55432` 启动 → 建库 → 载入 `db/schema.sql` →
用 `fakeredis` 顶替 Redis → 起**真实 uvicorn** → 跑仓库自带的 `tests/test_smoke.py`。

**从空库（先 `DROP DATABASE`）冷启动的一键复现**：

```powershell
cd yijian-platform
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
```

实测输出（完整、逐条）：

```
[local-verify] 启动 PostgreSQL :55432 ...
[local-verify] PostgreSQL 已就绪 -> 127.0.0.1:55432 (user=yijian, 无密码/trust)
[local-verify] 创建数据库 yijian ...
[local-verify] 载入 db/schema.sql ...
[local-verify] 建表完成，public 下 64 张表
[local-verify] 使用 Python: ...\python\envs\default\Scripts\python.exe
[local-verify] 初始化超级管理员 ...
[local-verify] 启动 API :8123 ...
[local-verify] API 已就绪 -> http://127.0.0.1:8123/docs

tests/test_smoke.py::test_health PASSED                                  [ 16%]
tests/test_smoke.py::test_register_and_me PASSED                         [ 33%]
tests/test_smoke.py::test_password_login_refresh_logout PASSED           [ 50%]
tests/test_smoke.py::test_unauthorized_access PASSED                     [ 66%]
tests/test_smoke.py::test_rbac_flow PASSED                               [ 83%]
tests/test_smoke.py::test_rate_limit_on_sms PASSED                       [100%]
============================== 6 passed in 1.99s ==============================

[local-verify] 全部通过。
```

**1️⃣ 你最关心的隐患点：`ANY(CAST(:uids AS bigint[]))` —— 实测可用 ✅**

对真实 PG + 真实 asyncpg + SQLAlchemy 2.0，把 Python `list[int]` 绑到该 SQL：

| 传参 | 结果 |
|---|---|
| `{"uids": [id1, id2, id3]}` | ✅ 3 行 |
| `{"uids": [id1]}` | ✅ 1 行 |
| `{"uids": []}` | ✅ 0 行（不报错） |

复验脚本：`python tools/local-verify/probe_roles_map.py` → `RESULT = PASS`。

**2️⃣ 对应接口实测（就是你点名的那条）**

```http
PUT /api/v1/admin/users/375228966664933376/roles
Body: {"role_codes":["researcher"],"scope_type":"professional","scope_id":7}
```

返回：

```json
{"code":0,"message":"角色已更新","data":{
  "user_id":375228966664933376,
  "roles":[{"id":3,"code":"researcher","name":"教研","scope_type":"professional","scope_id":7}],
  "granted_permissions":["exam:create","exam:grade","exam:publish","exam:read",
    "question:create","question:delete","question:import","question:publish",
    "question:read","question:review","question:rollback","question:update","stats:read"]
}}
```

同时 `GET /api/v1/admin/users?page=1&page_size=5`（走 `_roles_map` 批量取角色）返回
`code:0`、`total:4`，每条记录的 `roles` 都已正确回填、手机号已脱敏（`134****1345`）。

**3️⃣ 与仓库自带冒烟测试的对应关系**

`test_rbac_flow` **必须 `PASSED`**——它是唯一真正打到上述接口和 SQL 的用例。
本轮首次冷跑时它一度被 `SKIPPED (超管登录失败)`：因为 `db/schema.sql` 只灌角色/权限、
**不含任何用户**，超管要靠 `python -m app.cli seed-admin` 创建（Docker 里由
`docker-entrypoint.sh` 负责）。本地路径已补上该步骤，**不复现此坑**。

### 3.6 本批发现并修复的问题

| # | 问题 | 影响 | 修复 |
|---|---|---|---|
| 1 | `deps.rate_limit` 写了 `except too_many:`，而 `too_many()` 是**构造函数不是异常类** | 上批曾表述为「整个认证链路不可用」，**此处更正**：它只在「请求超限」或「Redis 不可用」时触发，此时抛 `TypeError` → 该接口 500；正常请求不受影响 | 重构：`try` 只包住 Redis 调用，限流超限在 `try` 外 `raise too_many()` |
| 2 | Redis 不可用时 `/auth/sms/send` 抛原始 `ConnectionError` → 500 堆栈 | 前端拿到无意义 500 | `errors.unavailable()`（503/50003）+ `sms_service` 守卫；业务异常原样透传 |
| 3 | 子包缺 `__init__.py`（`core/db/schemas/services/api/v1`） | 隐式命名空间包，Docker 内 `COPY` 后存在导入风险 | 补齐 7 个 `__init__.py` 并注明各层职责 |
| 4 | Postgres 硬编码 `shared_buffers=1GB` / `max_connections=200` | 小内存 Docker Desktop 上 Postgres 可能启动失败 → `docker compose up` 整体失败 | 改为环境变量可调，默认降到笔记本安全值（256MB / 100），并在 `.env.example` 注释生产建议值 |
| 5 | **`DeviceIn` 缺 `app_version` 字段**，而 `auth_service` 的 `register / login_by_password / login_by_sms` 都读 `payload.app_version` | **致命**：这 3 个接口的**正常路径**必然 `AttributeError` → 500。注册/密码登录是主流程，等于平台不可用 | 给 `DeviceIn` 补 `app_version`（`str \| None`，`max_length=24`） |
| 6 | **`audit_service.write_audit` 的 `actor_name` 是无默认值的 keyword-only 参数**，而 `auth_service.logout` 调用时没传 | **致命**：`POST /auth/logout` 每次调用都在**参数绑定阶段**就 `TypeError` → 500（发生在 `write_audit` 内部 try 之前，兜不住） | `actor_name` 默认 `None`；同时 `logout()` 透传 `actor_name`，端点传 `me.display_name` |

> **第 5、6 条为什么之前没发现**：离线验证只做到 `httpx.ASGITransport` 直驱，请求在
> Pydantic 校验阶段就返回 422 了，**根本没走到 service 层**；而 mock 掉 session 的单测
> 又是手写调用、绕过了真实入口。只有「真 HTTP + 真 PG」才能把它们打出来。
> 这两条也正是本轮补真库验证的最大收益。

### 3.7 仍未完成的验证（明确声明）

- **未实机执行 `docker compose up`**：编写环境没有 Docker，无法构建镜像、拉起容器。
  代码、编排、依赖、健康检查均已在静态层面核对一致，且应用层已被真 PG 端到端验证过，
  但**容器级启动仍需在你本机确认一次**（见第四节）。
- 原「未连真实 PostgreSQL 跑过 SQL」一项**已于本轮消除**：见 3.5，
  `ANY(CAST(:uids AS bigint[]))` 已在真库上按 3 种参数形态验证通过。

---

## 四、验收步骤（在你本机执行）

### 4.0（可选）本机没有 Docker 时的替代路径

用本机 PostgreSQL 直接跑，等价于下面 4.1~4.3，且**已实测通过**：

```powershell
cd yijian-platform
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
```

它会自动完成：起 PG（首次 `initdb`）→ 建库 → 载 `db/schema.sql` → **`seed-admin` 初始化超管**
→ 起 API（`fakeredis` 顶替 Redis）→ `pytest tests -v` → 收尾停服务。
详细说明与排错见 `tools/local-verify/README.md`。

> `seed-admin` 这步不能省：`db/schema.sql` 只灌角色/权限、不含用户，
> 少了它 `test_rbac_flow` 会因超管登录失败被 `SKIPPED`——那样恰好就绕过了本批最该验的接口。

### 4.1 一键启动

```bash
cd yijian-platform/deploy

cp .env.example .env
# 至少替换：POSTGRES_PASSWORD / REDIS_PASSWORD / JWT_SECRET / ADMIN_INIT_PASSWORD
# 生成随机密钥： python -c "import secrets;print(secrets.token_urlsafe(48))"

docker compose up -d --build
docker compose logs -f api
```

**启动成功的日志特征**（按顺序）：

```
[entrypoint] 环境: APP_ENV=local
[cli] PostgreSQL 已就绪
[cli] Redis 已就绪
[cli] 建表完成，public schema 下共 67 张表/视图
[cli] 已创建超管账号 13800000000
[entrypoint] 启动: uvicorn app.main:app --host 0.0.0.0 --port 8000
INFO:     Uvicorn running on http://0.0.0.0:8000
```

> 若日志停在「PostgreSQL 已就绪」之前，说明容器还在等依赖，正常最多等 1~2 分钟。
> `docker compose ps` 中 `postgres`、`redis` 应为 `healthy`。

### 4.2 健康检查

```bash
curl -s http://localhost:8000/api/v1/health
# {"code":0,...,"data":{"status":"ok","dependencies":{"postgres":"up","redis":"up"},...}}
```

### 4.3 跑冒烟测试

```bash
cd ../apps/api
pip install -r requirements.txt
ADMIN_INIT_PHONE=13800000000 ADMIN_INIT_PASSWORD=Admin@123456 pytest tests -v
```

期望：`6 passed`（健康 / 注册+me / 登录-刷新-登出 / 未授权 / RBAC 全流程 / 短信限流）。

### 4.4 手工验证 RBAC（最直观）

```bash
# 1) 超管登录拿 token
ADMIN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login/password \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","password":"Admin@123456"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

# 2) 走一遍注册拿学员 token
CODE=$(curl -s -X POST http://localhost:8000/api/v1/auth/sms/send \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000009","scene":"register"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['dev_code'])")

STU=$(curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d "{\"phone\":\"13800000009\",\"code\":\"$CODE\",\"password\":\"Passw0rd123\"}" \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")

# 3) 学员访问管理端 → 必须 40301
curl -s http://localhost:8000/api/v1/admin/users -H "Authorization: Bearer $STU"

# 4) 超管给该学员授 researcher
curl -s -X PUT http://localhost:8000/api/v1/admin/users/2/roles \
  -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"role_codes":["researcher"],"scope_type":"professional","scope_id":7}'

# 5) 同一个学员 token 立刻再查 /auth/me → 角色已变（权限缓存已失效）
curl -s http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $STU"
```

### 4.5 验收通过标准

- [ ] `docker compose up -d --build` 后 3 个容器全部 `healthy`
- [ ] `/api/v1/health` 返回 `status=ok`
- [ ] `pytest tests -v` 全绿
- [ ] 4.4 第 3 步返回 `40301`，第 5 步角色已变为 `researcher` 且 `scope_type=professional`
- [ ] Swagger `http://localhost:8000/docs` 正好列出 10 个接口

---

## 五、下一批建议

| 批次 | 内容 |
|---|---|
| Batch 3 | 前端骨架（Next.js 14 App Router + TS + Tailwind，移动端优先 PWA）：首页倒计时、章节练习、答题卡、错题本、模考、成绩报告 |
| Batch 4 | 题库导入流水线（合规审核/版本/回滚）、组卷引擎、记忆曲线算法、后台管理端完整化 |
| Batch 5 | 小程序端、支付对接、数据看板、压测与上线 |

**Batch 4 开工前建议先做一次数据范围落地**：本批 `user_roles.scope_type/scope_id` 已经把模型建好，
但「按科目/专业过滤查询结果」的行级过滤尚未实现（已在 `rbac_service` 文档串中标注），
应与题库模块一起落地，避免权限模型与业务查询脱节。
