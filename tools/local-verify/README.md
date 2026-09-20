# 本地验证（不依赖 Docker）

> **CI 跑的是同一套。** `.github/workflows/ci.yml` 的后端 job 逐步复刻了
> `run-smoke.ps1` 的链路：起 PG → 建表 → 迁移 → 种子(RBAC/超管) → **题库种子** →
> 起 API → pytest。**每一步两边都是同一个东西，不是各写一套。**
>
> ### 题库种子：`seed-questions.py`（两边共用的唯一实现）
>
> `db/schema.sql` 只灌 subjects / chapters / RBAC，**不含 `questions` /
> `knowledge_points`**；题库由 `db/seed/gen_seed_questions.py` 产出，落在 `data/`
> （**gitignored**）。**不灌它在全新库上会 32 条用例失败**（`test_admin_v7.py` 的
> 组卷 / 加题 / 知识点下拉无题可抽）。
>
> 生成 + 灌库的逻辑**只有 `seed-questions.py` 一份**，CI 与 `run-smoke.ps1` 都调它 ——
> 两边各写一套的话迟早漂，又回到"本地绿、CI 红"的口径分歧（正是坑 51 的形态）。
>
> ```bash
> python tools/local-verify/seed-questions.py --pg-port 55432 --db yijian --db-user yijian
> ```
>
> 幂等（SQL 自带 `ON CONFLICT`），约 5s；**刻意不做"库里已有 6000 题就跳过"的快捷判断**
> —— 那正是假绿的成因（"环境恰好脏"时跳过 → 行为与干净环境不同）。
>
> 背景见 `apps/admin/docs/B端联调坑.md` 坑 51 与硬约定 H
> （**验收脚本本身也要被验收：把缓存/库全删掉，它还能不能绿？**）。


> 用途：在没有 Docker 的机器上，用**真实 PostgreSQL** 把后端跑起来并验收
> （Batch 2 起引入，后续批次持续复用；覆盖认证 / RBAC / 题库 CRUD / 导入管道）。
> Redis 用 `fakeredis` 顶替（只模拟命令行为，其余代码路径 100% 真实）。
>
> 已在 Windows + PostgreSQL 16.15 + Python 3.13 上实测通过：**`126 passed, 1 skipped`**。

---

## 0. 一次性准备

### 0.1 装 PostgreSQL（免安装、免管理员，conda 独立前缀）

```powershell
# 只从 conda-forge 取包，不碰 base 环境
& "$env:USERPROFILE\anaconda3\Scripts\conda.exe" create `
  -p "$env:USERPROFILE\.workbuddy\binaries\pg\pg16" `
  --override-channels -c conda-forge -y postgresql=16
```

校验：

```powershell
& "$env:USERPROFILE\.workbuddy\binaries\pg\pg16\Library\bin\pg_ctl.exe" --version
# pg_ctl (PostgreSQL) 16.x
```

### 0.2 装 Python 依赖

```powershell
cd apps/api
pip install -r requirements.txt
pip install fakeredis          # 仅本地验证需要，不进 requirements.txt
```

> **务必确认装到了哪个解释器。** 这是最容易踩的一脚：
> PATH 上的 `python` 很可能是 Anaconda/系统 Python，而依赖装进了另一个 venv。
> 本机实测：`python` → Anaconda 3.12.7（**没有** fastapi/asyncpg/fakeredis）；
> 依赖齐全的是托管 venv
> `C:\Users\<你>\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（3.13.14）。
>
> `run-smoke.ps1` 现在会按优先级自动探测（显式 `-Python` → `$env:YIJIAN_PYTHON` →
> 上面那个托管 venv → PATH 的 `python`），**取第一个能 `import fastapi/sqlalchemy/asyncpg/fakeredis/uvicorn/pytest/httpx` 全部成功的**。
> 建议在装依赖前先确认打进哪个解释器：
>
> ```powershell
> python -c "import sys,fastapi,asyncpg,fakeredis; print(sys.executable)"
> ```
>
> 装错了就显式指定：`run-smoke.ps1 -Python "<那个解释器的绝对路径>"`。

---

## 1. 一键跑通

```powershell
cd yijian-platform
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
```

脚本会依次：初始化并启动 PG → 建库 → 载入 `db/schema.sql`（仅首次建库）
→ **应用 `db/migrations/*.sql`** → **灌题库种子** → **初始化超管**
→ 起 API（真 PG + fakeredis，**带覆盖率采集**）→ 跑 `pytest tests -v`
→ **停 API 并出覆盖率报告（门槛门禁）** → 收尾停止服务。

加 `-NoCoverage` 可跳过覆盖率采集（只测用例）；`-KeepRunning` 保留 PG 不关。

> ⚠️ **题库种子这一步不能省**（坑 51）：`db/schema.sql` 只灌
> subjects / chapters / RBAC，**不含 `questions` 与 `knowledge_points`** ——
> 题库由生成器产出、落在 gitignored 的 `data/`。少了它，在**真正全新的库**上
> 会 **32 条用例失败**（组卷 / 加题 / 知识点下拉无题可抽）。
> 本脚本过去一直绿，只因开发机的库早先被手工灌过 —— 那是**假绿**（硬约定 H）。
> 生成 + 灌库的逻辑抽在 `seed-questions.py`，**CI 调的是同一个脚本**。

> ⚠️ **为什么迁移是单独一步**：`db/schema.sql` **只在首次建库时**载入 ——
> 脚本会先查 `to_regclass('public.users')`，库已存在就**整段跳过**。
> 所以"加列 / 回填 / 补种子权限"这类改动如果只改 schema.sql，**老库永远拿不到**，
> 而验收环境恰好是新库 → **问题会留到生产才爆**。
>
> 因此：结构变更一律写进 `db/migrations/`（按日期命名，如
> `20260917-01-locked-version-and-viewer-exam-read.sql`），**脚本自身必须幂等**
> （`ADD COLUMN IF NOT EXISTS` / `ON CONFLICT DO NOTHING`），这样才能无条件重跑。
> 手工起环境（不用 run-smoke）时，记得自己 `psql -f` 跑一遍这批脚本。

两个副产物日志（排查时直接看）：

| 文件 | 内容 |
|---|---|
| `%TEMP%\yijian-api.log` | uvicorn 启动日志 + 每个请求的访问日志（由 `serve_fake_redis.py` 自己落盘）。**收尾时会多一行 `[cov] data saved to ...`** —— 那是覆盖率落盘的凭据；没有它说明进程是被硬杀的，本次覆盖率不可信 |
| `%TEMP%\yijian-pytest.log` | pytest 完整输出（UTF-16，`Get-Content -Encoding Unicode` 读） |
| `%TEMP%\yijian-coverage.log` | 覆盖率逐文件明细（门槛没通过时会打印尾部 12 行） |

期望输出结尾（pytest 按文件名排序，`test_smoke.py` 在最后，所以摘要行跟在它后面）：

```
tests/test_smoke.py::test_health PASSED
tests/test_smoke.py::test_register_and_me PASSED
tests/test_smoke.py::test_password_login_refresh_logout PASSED
tests/test_smoke.py::test_unauthorized_access PASSED
tests/test_smoke.py::test_rbac_flow PASSED
tests/test_smoke.py::test_rate_limit_on_sms PASSED
===================== 126 passed, 1 skipped in 31.95s =====================
```

> 前面还会有 `test_admin_v3.py`（14）、`test_admin_v4.py`（20）、`test_admin_v5.py`（14）、
> `test_admin_v7.py`（58）、`test_idgen.py`（15）、`test_smoke.py`（6）。
> **当前全量是 `126 passed, 1 skipped`** ——
> 那个 `1 skipped` 是 `test_admin_v5.py` 里的 6000 行全量导入用例，需环境变量显式开启才会跑。

> **`test_rbac_flow` 必须是 `PASSED` 而不是 `SKIPPED`。**
> 它是唯一真正打到 `PUT /admin/users/{id}/roles` 和 `ANY(CAST(:uids AS bigint[]))` 的用例。
> 如果看到 `SKIPPED (超管登录失败...)`，说明**超管没初始化**（`db/schema.sql` 只灌角色/权限，
> 不含任何用户）——这也是为什么 `run-smoke.ps1` 里有一步 `python -m app.cli seed-admin`，
> 它在 Docker 里由 `apps/api/docker-entrypoint.sh` 负责。

---

### 覆盖率门禁（2026-09-20 起）

**为什么必须在 API 进程里采**：用例是**通过 HTTP** 打到独立 uvicorn 的
（`apps/api/tests/conftest.py` 里就是个普通 `httpx.Client`，base_url 来自 `AI_BASE`）。
所以 `pytest --cov=app` 只量得到测试自己导入的几个纯函数模块 —— 数字低得没意义。
**业务代码全跑在 API 那个进程里，就在那里采。**

**为什么需要哨兵文件**：收尾若用 `Stop-Process -Force` / `kill`，进程直接消失、
`atexit` 不跑 → 覆盖率数据**一个字都写不出来**（而报告会显示 0% 而不是报错，
是最难发现的一种假绿）。所以改成本脚本放一个哨兵文件，API 自己收尾写盘：

```powershell
# 手工分步跑时同样适用
python serve_fake_redis.py --pg-port 55432 --api-port 8123 `
  --coverage --cov-data-file "..\..\.coverage" --shutdown-file "$env:TEMP\yijian-cov-stop"
# …跑完 pytest 之后：
Set-Content -Path "$env:TEMP\yijian-cov-stop" -Value stop -Encoding ascii
# 等 API 自己退出，再从**仓库根**出报告（数据里的路径是相对的，换目录会报 No data）
cd ..\..
python -m coverage report --data-file .coverage --skip-covered
```

**门槛只有一个来源**：仓库根 `.coveragerc` 的 `fail_under`（当前 **65**）。
`run-smoke.ps1` 与 CI 都不在命令行重写数字，只检查 `coverage report` 的退出码。
门槛的取值依据（实测 65.64% → 取"实测值下方一点点"作棘轮）写在 `.coveragerc` 里。

> 首次实测：44 文件 / 4305 语句 / 未覆盖 1479 → **65.64%**。
> `services` 只有 49.6%（缺的是 Batch 8+ 还没写的代码），`(顶层)` 13.5%
> （`cli.py` / `main.py` 只有启动路径被跑到）。

---

## 2. 分步执行（便于排查）

```powershell
cd yijian-platform/tools/local-verify

# 2.1 初始化 + 启动 PG（首次会自动 initdb）
.\start-pg.ps1
# → 监听 127.0.0.1:55432

# 2.2 建库 + 建表
$psql = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16\Library\bin\psql.exe"
$env:PGCLIENTENCODING = "UTF8"
& $psql -h 127.0.0.1 -p 55432 -U yijian -d postgres -c "CREATE DATABASE yijian"
& $psql -h 127.0.0.1 -p 55432 -U yijian -d yijian -v ON_ERROR_STOP=1 -f ..\..\db\schema.sql
# → 64 张表

# 2.3 初始化超管（关键！不跑这步 test_rbac_flow 会 SKIPPED）
$py = "$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$env:DATABASE_URL        = "postgresql+asyncpg://yijian@127.0.0.1:55432/yijian"
$env:APP_ENV             = "local"
$env:ADMIN_INIT_PHONE    = "13800000000"
$env:ADMIN_INIT_PASSWORD = "Admin@123456"
cd ..\..\apps\api
& $py -m app.cli seed-admin     # 幂等：已存在则重置密码并激活

# 2.4 起 API（真 PG + fakeredis）
cd ..\..\tools\local-verify
& $py serve_fake_redis.py --pg-port 55432 --api-port 8123 --log-file "$env:TEMP\yijian-api.log"
# → 日志写在 $env:TEMP\yijian-api.log，里面有 "Uvicorn running on http://127.0.0.1:8123"

# 2.5 另开一个窗口跑冒烟
$env:AI_BASE = "http://127.0.0.1:8123"
cd ..\..\apps\api
& $py -m pytest tests -v --tb=short

# 2.6 收尾
cd ..\..\tools\local-verify
.\stop-pg.ps1
```

---

## 3. 单独验证 `_roles_map` 的数组绑定

这是 Batch 2 唯一一条「靠约定而非靠测试」保证过的 SQL：

```sql
WHERE ur.user_id = ANY(CAST(:uids AS bigint[]))
```

针对真实 PostgreSQL 16 的执行结果（三类参数都验过）：

| 传参 | SQL 层结果 | 备注 |
|---|---|---|
| `{"uids": [id1, id2, id3]}` | 3 行 | 正常 |
| `{"uids": [id1]}` | 1 行 | 正常 |
| `{"uids": []}` | 0 行（不报错） | 额外保险：服务层 `if not user_ids: return {}` 会直接短路，压根不落库 |

复验（推荐，直接跑探针脚本，会真实地把 Python `list[int]` 绑给 asyncpg）：

```powershell
# 需要 PG 在跑、且 yijian 库里有 users/user_roles/roles 数据
& $py .\probe_roles_map.py
# [probe] 3 个 id             -> OK   3 行  [...]
# [probe] 1 个 id             -> OK   1 行  [...]
# [probe] 0 个 id（空列表）    -> OK   0 行  []
# [probe] RESULT = PASS
```

也可以用 psql 只验 SQL 文本（注意：这样验不到驱动绑定，只排除「cast 写法」这一类问题）：

```powershell
$psql = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16\Library\bin\psql.exe"
& $psql -h 127.0.0.1 -p 55432 -U yijian -d yijian -c @"
SELECT ur.user_id, r.code
FROM user_roles ur JOIN roles r ON r.id = ur.role_id
WHERE ur.user_id = ANY(CAST(ARRAY[1,2,3] AS bigint[]));
"@
```

**为什么这个 cast 是必须的（不是装饰）**：asyncpg 要从预备语句的**参数 OID** 推断
Python 值该用哪个编解码器。显式 `CAST(... AS bigint[])` 让 PG 报告参数类型为 `int8[]`，
asyncpg 才会用 int8 数组编解码器去编码 Python 的 `list[int]`。
如果去掉 cast，参数类型退化成 `unknown`，就依赖 PG 从上下文反推——多数情况能推出来，
但不该把正确性押在推断上。

### 如果它真的失败了，按这三种可能排查

| 可能 | 典型报错 | 判定方法 |
|---|---|---|
| **① cast 写法错** | `PostgresSyntaxError` / `DatatypeMismatchError: cannot cast type ... to bigint[]` | 把 SQL 拿去 psql 里跑，加上 `PREPARE` 看报错位置。SQL 文本本身有问题时，psql 也会报同样的错 |
| **② 驱动绑定问题** | `invalid input for query argument $1: ... (expected list, got ...)` 或 `DataError` | 说明 SQL 对但值没被编码成数组。用 `sqlalchemy.dialects.postgresql.ARRAY(BigInteger)` + `bindparam(type_=...)` 显式声明类型可绕过 |
| **③ 参数类型不对** | 同上，或 `IdleInTransactionSessionTimeout` | 检查传入的是 `list[int]` 而不是 `tuple`／`list[str]`。雪花 ID 是 `int`，字符串形式的 ID 会类型不匹配 |

一句话排查顺序：**先在 psql 里用字面量跑通 SQL（排除①）→ 再用 Python 传 list 跑（排除③）→ 最后换 `bindparam` 显式类型（排除②）**。

---

## 4. 关于 SQLite 兜底（**仅用于验证，不可交付**）

如果连 PostgreSQL 都起不来，可以用 SQLite 跑通「接口层 → 服务层」的骨架，
但必须清楚它**验不了**什么：

`schema.sql` 里有一批 PG 专有特性，SQLite 全都不支持：

| PG 特性 | 出现在 | SQLite 行为 |
|---|---|---|
| `JSONB` | `user_profiles.target_subjects`、`audit_logs.before_data/after_data` | 无此类型，需降级为 `TEXT`，`->>` 等操作符失效 |
| `INET` | `users.register_ip/last_login_ip`、`user_sessions.ip`、`audit_logs.ip` | 无此类型，需降级为 `TEXT`；`to_inet()` 返回的 `ipaddress` 对象写不进去 |
| `ANY(... )` + 数组 | `user_service._roles_map` | SQLite 无数组类型，**恰好这条就是要验的 SQL，换成 SQLite 等于没验** |
| `now()` / `TIMESTAMPTZ` | 全局 | 无时区概念，需改 `CURRENT_TIMESTAMP` |
| 部分唯一索引 / `ON CONFLICT DO NOTHING` | `schema.sql` 多处 | 部分支持，`DO NOTHING` 需 PG 3.24+ 才可用 |
| `CREATE EXTENSION` / `TRIGGER` / `VIEW` | `schema.sql` 头部与末尾 | 不支持，须整段删除 |

**结论**：SQLite 只能验证「路由注册、参数校验、响应信封、异常映射、错误码」这类
与数据库无关的行为——而这些**已经用 `httpx.ASGITransport` 在无数据库的情况下验过了**
（见 `docs/08-Batch2-验收报告.md` 3.3 节）。所以为了验证 Batch 2 而去改 SQLite 是负收益。

真要改，最小改动面是：
1. `app/db/models.py`：`INET` → `String(45)`，`JSONB` → `JSON`（SQLAlchemy 通用类型）
2. `app/db/base.py`：`database_url` 换成 `sqlite+aiosqlite:///./local.db`
3. `app/core/idgen.py`：`to_inet()` 直接返回字符串
4. `db/schema.sql`：另写一份精简 DDL，只保留 8 张表、去掉扩展/触发器/视图/`CHECK` 里的 PG 语法
5. `app/services/*.py`：`ANY(CAST(:uids AS bigint[]))` 改成 `IN :uids` + `expanding` 参数

改到第 5 步时，你已经把要验证的东西改掉了。

---

## 5. 已踩过的坑（Windows / PowerShell 5.1）

这些坑都真实卡过流程，记下来省得再踩：

### 5.1 `.ps1` 文件必须是 UTF-8 **带 BOM**

PowerShell 5.1 默认按 ANSI（中文机器上是 GBK）解码 `.ps1`。
UTF-8 无 BOM 的中文脚本会出现**字符串字面量中断**：

```
ParserError: 字符串缺少终止符: "。
```

写入时显式带 BOM：

```powershell
[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($true)))
```

### 5.2 `pg_ctl start | Out-Null` 会**卡死**

`pg_ctl` 会 fork 出守护进程 `postgres.exe`，子进程**继承父进程的 stdout 句柄**。
一旦把 pg_ctl 的 stdout 接到调用方管道（`| Out-Null`、`| Out-String`、`| Tee-Object` 都算），
管道永远等不到 EOF，调用方就一直挂在那里——而 PG 其实**已经起来了**。

正确做法（`start-pg.ps1` 用的就是这招）：用 `ProcessStartInfo` + `UseShellExecute = $true`
让 pg_ctl 完全脱离当前进程的 stdio，然后**轮询 `pg_isready` 判断是否就绪**：

```powershell
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName        = "...\pg_ctl.exe"
$psi.Arguments       = 'start -D "<data>" -l "<log>" -o "-p 55432 -c listen_addresses=127.0.0.1"'
$psi.UseShellExecute = $true
$psi.WindowStyle     = [System.Diagnostics.ProcessWindowStyle]::Hidden
$ctl = [System.Diagnostics.Process]::Start($psi)
$null = $ctl.WaitForExit(30000)
```

### 5.3 `Start-Process` 在本机会抛「已添加项」

用 `Start-Process -RedirectStandardOutput ...` 时可能报：

```
已添加项。字典中的关键字:“PATH”所添加的关键字:“Path”
```

原因是进程环境块里同时存在 `PATH` 和 `Path` 两个仅大小写不同的键，
`Start-Process` 内部建字典时撞了。**绕开方式：不用 `Start-Process`，改用 `ProcessStartInfo`。**

### 5.4 长生命周期子进程（uvicorn）同理不能继承 stdio

API 进程活到测试结束，如果它继承了调用方的 stdout，调用方要等它退出才拿到 EOF。
所以 `serve_fake_redis.py` 被拉起来时用 `UseShellExecute = $true` 脱离 stdio，
日志改成自己写文件（`--log-file`）。

### 5.5 `$ErrorActionPreference = "Stop"` 会被外部程序的 stderr 误伤

PS 5.1 会把**原生命令写到 stderr 的任意一行**转成 ErrorRecord，在 `Stop` 下直接终止。
`python -c "import fakeredis"` 失败（traceback 走 stderr）、psql 的 `NOTICE` 都会触发。
本脚本统一用 `Continue` + 显式判断 `$LASTEXITCODE`。

### 5.6 命令参数位置不接受跨行括号表达式

```powershell
Fail ("第一行"
    + "第二行")     # ← PS 5.1 解析失败：表达式中缺少右“)”
```

在**命令参数位置**（`Fail (...)`、`Write-Host (...)`）必须写成单行或先用变量拼好。

### 5.7 `Tee-Object` 写出的是 UTF-16

`Tee-Object -FilePath` 默认 `Unicode`（UTF-16LE），用文本编辑器/普通读取会当成二进制。
读的时候要：

```powershell
Get-Content .\x.log -Encoding Unicode
```

### 5.8 `Invoke-RestMethod` 看到中文是乱码（不是后端的锅）

PS 5.1 的 `Invoke-RestMethod` 在响应头没有 `charset` 时按 Latin-1 解码，
于是 `{"message":"登录成功"}` 显示成 `{"message":"ç»å½æå"}`。
FastAPI 返回 `application/json`（JSON 规范即 UTF-8，不带 charset）是**正确的**，
httpx、curl、浏览器都能正常解码——用 `pytest` 断言中文字段是通过的，可以佐证。
