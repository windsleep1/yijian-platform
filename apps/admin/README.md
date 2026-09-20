# 一建通 · 管理后台 v0.1（Batch 7）

一级建造师学习备考平台的 **B 端管理后台**。累计交付：题库 CRUD（Batch 4）+ 题库批量导入管道与导入向导（Batch 5/6）+ 组卷引擎（Batch 7，含试卷列表 / 详情编辑 /
新建试卷 / 组卷规则管理 / 实时试算）+ 状态机补齐（试卷下线 / 账号停用启用）+ 数据范围收口。

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

## 2. 页面

| 路由 | 说明 | 需要的权限 |
|---|---|---|
| `/login` | 密码登录，支持 `?next=` 回跳 | — |
| `/questions` `/questions/[id]` | 题库列表 / 题目详情编辑（Batch 4） | `question:read`；写操作另需 `question:create|update|delete` |
| `/imports` | 导入批次列表：历史 / 状态 / 回滚入口（Batch 6） | `question:read` |
| `/imports/new` | 导入向导三步：上传 → 校验（dry-run）→ 预览确认 → 执行 → 发布（Batch 6） | `question:import`；发布另需 `question:publish` |
| `/imports/[id]` | 批次详情：状态流转 / 统计 / 逐行结果 / 变更日志 / 回滚（Batch 6） | `question:read`；回滚另需 `question:rollback` |
| `/users` | 用户列表：分页 / 关键词搜索 / 状态筛选 / 角色标签 | `user:read` |
| `/users/[id]` | 用户详情 + 分配角色（弹窗 + 二次确认） | `user:read`；改角色另需 `user:manage` |
| `/audit-logs` | 审计日志：多条件筛选 + diff 抽屉 | `system:audit` |
| `/forbidden` | 403 落地页（说明缺哪个权限、当前角色、该找谁） | — |

菜单项全部由 `src/lib/permission.ts` 的 `MODULE_ENTRIES` 推导（唯一真相，见坑 20）。

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

### 3.4 如何验证导入流程（Batch 5/6）

导入是**唯一一个会批量写库**的入口，所以要验证的不只是"能不能导进去"，
还有"错了会不会半截脏数据"、"导错了能不能收回"。下面分三层，**由快到慢**。

#### 3.4.1 接口层：一条命令

```bash
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepRunning
# 期望：126 passed, 1 skipped
#   test_admin_v5.py  13 个  ← 导入管道（上传/校验/执行/发布/回滚/变更日志/数据范围）
#   test_idgen.py     15 个  ← 雪花 ID 精度
#   test_admin_v3/v4、test_smoke  ← Batch 2/3/4（不能回归）
```

`1 skipped` 是 6000 行全量的环境变量门控用例（Batch 5 起就有，需显式开启）。

#### 3.4.2 真实体量：把 Batch 1 的 6000 道仿真题灌一遍

```bash
export DATABASE_URL="postgresql+asyncpg://yijian@127.0.0.1:55432/yijian"   # 端口按你的环境改

# 全量体检：只上传 + 校验，一行都不写库（约 12s）
python tools/local-verify/import-sim-bank.py --dry-run

# 真灌库（upsert 模式：逐条命中已有题，题量不变）
python tools/local-verify/import-sim-bank.py

# 幂等验证：insert 模式重导同一份 → 期望 duplicate=6000 / success=0
python tools/local-verify/import-sim-bank.py --mode insert
```

> ⚠️ 别把 `data/seed/questions.csv` 直接喂给导入接口 —— 那是 Batch 1 的**导出**格式，
> 不是导入模板，直接喂会把 6000 道题的章节关系洗成 NULL（坑 28）。
> 这个脚本的作用就是从 `questions.json` 重新生成**严格 24 列模板**。

只想产出文件、给浏览器手工走查用（不上传）：

```bash
python tools/local-verify/import-sim-bank.py --out /tmp/simbank.csv --limit 300
# UTF-8 with BOM，12~300 行随你切
```

#### 3.4.3 浏览器层：教研视角走一遍四个验收场景

登录 `13800000000 / Admin@123456`，从侧边栏「题库导入」进。

| # | 场景 | 怎么点 | 期望 | 截图 |
|---|---|---|---|---|
| ① | 好文件走完整流程 | `/imports/new` 上传 `docs/samples/batch6-questions.csv` → 上传并校验 → 下一步 → 执行 → 发布 | 校验页明写"未写库"；预览页给**具体数字**（新增 12）；发布后题库列表能看到 | `01`~`06`、`14` |
| ② | 故意写错的文件 | 上传 `docs/samples/batch5-wrong-file.csv` → 上传并校验 | 错误表按行号排序（第 57 / 100 行）；点行展开**字段级**详情；下载 CSV 打开是 `row_no,field,message` 三列 | `07`~`09` |
| ③ | 同一份文件导两次 | 把②用过的文件再导一遍 | 第二次**新增 0 / 更新 0 / 跳过 12 / 未通过 0**；**库中行数不变** | `15`、`16` |
| ④ | 导错了要收回 | 批次详情 → 整批回滚 → 弹窗里**手输批次号** → 确认回滚 | 弹窗写"将软删除 N 道题"；回滚后状态 `rolled_back`；该批题目归档（题库计数下降） | `17`~`19` |

截图存档在 `docs/screenshots/batch6/`（18 张）。走查脚本与产物见
`docs/12-Batch6-导入向导-方案与验收.md` §9。

**四个容易看漏的点**（都是本条链路的"验收含金量"所在）：

1. **校验页是 dry-run** —— 校验完**故意不执行**，去题库列表看，题目数量必须**没变**。
2. **预览页的数字不能是"通过"** —— 必须是"新增 N / 更新 N / 跳过 N / 未通过 N"四个具体数。
3. **回滚要手输批次号** —— 输错一位，"确认回滚"必须仍是置灰的（这是防"点错批次"的减速带）。
4. **回滚失败时弹窗保持打开** —— 失败信息画在**弹窗内部**并给重试；不是弹个 toast 就没了（坑 30）。

### 3.5 如何验证组卷引擎（Batch 7 Pass 1，后端）

组卷的验收要点不是"能不能生成卷子"，而是**三件容易做错的事**：
缺口要如实回传、卷面必须自洽、发布后不能被改题悄悄影响。
Pass 2 前端未落地前，直接打接口验证：

```bash
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
# 期望：126 passed, 1 skipped（其中 test_admin_v7.py 58 条是组卷用例）
```

只跑组卷用例（复用已运行的 API）：

```bash
cd apps/api
AI_BASE=http://127.0.0.1:8123 \
DATABASE_URL="postgresql+asyncpg://yijian@127.0.0.1:55432/yijian" \
python -m pytest tests/test_admin_v7.py -v
```

**七个必须亲手确认的点**（完整 curl 见 `docs/13-Batch7-组卷引擎-方案与验收.md` §8）：

| # | 确认什么 | 怎么看 |
|---|---|---|
| ① | **缺口不静默凑数** | 配一条"要 100 道 case 题"的规则（库里只有 27 道）→ `shortfalls[0]` 的 `need=100/got=27/missing=73`，且**卷面题数 = 27**，不是 100 |
| ② | **卷面自洽** | 组卷后 `validate` 的 `ok=true`、`errors=[]`；分段 `actual_count == question_count` |
| ③ | **版本锁定真的生效** | 发布 → 改某道题的题干（version+1）→ 重新打开试卷：`locked_version` 停在旧版本、`version_drift=true`，且 `stem_preview` 是**旧题干** |
| ④ | **数据范围** | 用挂 `subject` 范围的教研账号，去建/组别科目的卷 → `40301`；列表里也看不到别科目的规则 |
| ⑤ | **归档不进默认列表** | 归档一份卷 → `GET /admin/exams` 看不到；加 `include_deleted=true` 能看到（带 `is_deleted=true`）；**详情仍可打开**、四个 `can_*` 全 false |
| ⑥ | **viewer 只读** | 用 `viewer` 登录 → 能看列表/详情 → 发布/归档/恢复分别返回 `40301`（消息含 `exam:publish` / `exam:create`，tooltip 直接用） |
| ⑦ | **恢复的关联校验** | 把某题的章节软删（`UPDATE chapters SET is_deleted=true WHERE id=…`）→ 恢复该题返回 `40901` 且说明是"章节已删除"、**`is_deleted` 一点没变**；把章节改回来后**同一个请求就能通过** |

> ℹ️ 两个**曾经**的限制，已在 Pass 2 开工前修掉（`docs/13` §1.2 / §3.3 / §3.5 / §3.6）：
> - 版本锁定已改为**独立列** `exam_questions.locked_version`（迁移
>   `db/migrations/20260917-01-*.sql` 含回填）；`rule_config.question_locks` 已 deprecated，
>   仍双写 + 读时回退，下一批清理。
> - `viewer` 角色补了 `exam:read`，所以**能**复现"能看试卷、发布按钮置灰"
>   （其他角色的 exam 权限仍是整包发放，见坑 36）。
>
> 另一个**仍然存在**、验收时别当成 bug 的点：`exam` 模块的四条权限按模块整包发给
> super_admin / admin / researcher / teacher，所以只有 `viewer` 是只读的；
> 想演示别的"只读"组合仍需往 `viewer` 上补权限（不要新建角色）。

---

## 4. 后端接口 curl 示例

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

Swagger 在 <http://localhost:8000/docs>，50 个接口都有中文 summary 和 description。

---

## 5. 验收

### 5.1 接口层：一条命令跑 pytest

```bash
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
```

一条命令完成：起 PostgreSQL → 载入 schema → 重放 RBAC 种子 → 初始化超管 → 起 API → 跑 pytest → 收尾。
**期望 `126 passed, 1 skipped`**：

```
tests/test_idgen.py      15 个   ← 雪花 ID 精度（Batch 5）
tests/test_admin_v3.py   14 个   ← 用户详情 / viewer 流程 / 角色权限契约 / 审计筛选 /
                                  停用启用账号（三道守卫 + 留痕 + 权限墙）
tests/test_admin_v4.py   20 个   ← 题库 CRUD（Batch 4）+ 数据范围收口
                                  （详情/新建/编辑/删除/批量/恢复/两个下拉）
tests/test_admin_v5.py   14 个   ← 导入管道 + 变更日志（Batch 5/6）
                                  其中 1 条是 6000 行全量门控用例，默认 skip
tests/test_admin_v7.py   58 个   ← 组卷引擎（Batch 7）：规则 CRUD / 组卷算法 / 缺口 /
                                  校验 / 版本锁定 / 数据范围 / viewer 只读 /
                                  试卷归档 + 恢复 / 题目恢复 / 手动加题移题
tests/test_smoke.py       6 个   ← Batch 2 的认证链路 + RBAC（不能回归）
─────────────────────────────────────────────────────────────
共 127 条 collected → 126 passed, 1 skipped
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
│     ├─ questions/ questions/[id]/   题库 CRUD（Batch 4）
│     ├─ imports/ imports/new/ imports/[id]/   ★ 导入向导（Batch 6）
│     └─ users/ users/[id]/ audit-logs/
├─ src/components/
│  ├─ PermissionGate.tsx              ★ 权限门控（disabled + tooltip）
│  ├─ DataTable.tsx                   ★ 表格壳（分页/排序/空态/骨架）
│  ├─ AssignRolesDialog.tsx           ★ 分配角色（列权限 + diff + 二次确认）
│  ├─ AuditDiffDrawer.tsx             ★ 审计 diff 抽屉
│  ├─ StepWizard.tsx                  ★ 导入步骤条（高亮/打勾/不可跳步）
│  ├─ ErrorReportTable.tsx            ★ 错误表（排序/展开字段级/下载 CSV）
│  ├─ RollbackDialog.tsx              ★ 回滚二次确认（手输批次号 + 失败降级）
│  ├─ ImportStatCards.tsx / ImportStatusFlow.tsx / InlineError.tsx
│  └─ ui/                             shadcn/ui 组件
├─ src/hooks/                          useTableState / useUsers / useAuditLogs / useRoles / useImports
├─ src/lib/
│  ├─ api.ts                          ★ 信封解包 + 401 分流（刷新 / 跳登录）+ trace_id
│  ├─ json-bigint.ts                  ★ 大整数 ID 解析安全带（防御垫）
│  ├─ import.ts                       ★ 导入：状态流推导 / CSV 构造(BOM+CRLF) / 下载 / 前置校验
│  ├─ types.ts                        与后端契约一一对应
│  └─ auth-store.ts / auth-context.tsx / permission.ts / format.ts
├─ docs/
│  ├─ B端联调坑.md                     50 条踩过的坑
│  └─ screenshots/                    人工走查截图存档（batch3 平铺 15 张 / batch4 13 /
│                                     batch6 18 / batch7-pass2a 14 / batch7-pass2b 13 /
│                                     batch7-state-machine 4）
```

配套文档：**`docs/B端联调坑.md`** —— 50 条前后端联调踩过的坑（含雪花 ID、Rotation 并发、
disabled 不出 tooltip、时区、脱敏位置、401 分流、导入管道的含错写库/原文落盘，
Batch 6 的回滚数字语义、模态框失败态、下载验真，
Batch 7 的 `subjects` 无 `is_deleted`、SELECT 列与取值清单不一致、
权限整包发放无法表达"只读"、本机 PG 必须独立后台常驻、
**只读函数夹带准入条件导致外层放行内层拦死**、**改 schema.sql 对老库不生效**、
**弱引用 + 软删除的悬空引用没人拦**、**幂等的判据是"状态达成"不是"没发生"**、
**编辑接口顺手删掉关联数据**、**开关式列表里写后失效缓存让行原地复活**、
**`npm run lint` 是假门禁** 等）；
末尾四份**检查清单**可直接照着过。

配套脚本：

| 脚本 | 用途 |
|---|---|
| `tools/local-verify/serve-local.ps1` | 起后端并**保持运行**（联调用） |
| `tools/local-verify/run-smoke.ps1` | 一键验收：起环境 → pytest → 收尾 |
| `tools/local-verify/seed-demo-users.ps1` | 建 `viewer`/`researcher` 演示账号 + 写角色变更审计 |
| `tools/local-verify/probe-silent-refresh.py` | 浏览器端静默刷新链路探针 |
| `tools/local-verify/import-sim-bank.py` | 把 6000 道仿真题按**严格 24 列模板**灌库；`--out` 只产出文件 |
| `tools/local-verify/probe-import-pipeline.py` | 导入管道端到端探针（含数据范围） |
| `tools/local-verify/ab-capture-download.js` | 测试基建：拦 `URL.createObjectURL` 抓导出字节（坑 31） |

---

## 8. 常用命令

```bash
npm run dev         # 开发（:3000）
npm run build       # 生产构建
npm run start       # 跑构建产物
npm run typecheck   # 只做类型检查（tsc --noEmit）
npm run lint        # ESLint
```
