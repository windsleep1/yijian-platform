# 一键本地验收：起 PostgreSQL → **一次性库**（建库 + schema + 迁移）→ 起 API → 跑 pytest → 销毁库。
# 不需要 Docker。★ 默认每次跑在**全新库**上 —— 数据状态与 CI 可比（BL-17 / docs/21 §4.13）。
#
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepRunning
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepDb        # 留着一性库排查
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -DbName yijian # 跑在累积的开发库上
#
# 若自动挑选的 Python 解释器不对，可显式指定：
#   ... -Python "<python.exe 的完整路径>"
#
[CmdletBinding()]
param(
    [string]$Prefix  = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16",
    [string]$DataDir = "$env:USERPROFILE\.workbuddy\binaries\pg\data16",
    [int]   $Port    = 55432,
    [int]   $ApiPort = 8123,
    [string]$Python  = "python",
    # ⚠️ 默认 **空 = 一次性临时库**（`yijian_smoke_<pid>`，跑完销毁）—— BL-17 / `docs/21` §4.13。
    #    为什么默认必须是一次性：本地开发库 `yijian` **跨会话累积**，覆盖率会被历史数据抬高
    #    （2026-09-27 实测差 2 行，`import_service.py:1427-1428`），与 CI 的"全新库"**不可比**。
    #    一个"会忘的 `-Fresh` 开关"救不了这件事 —— **默认行为就该是与 CI 可比**。
    #    要跑在累积的开发库上（故意为之的调试）：显式 `-DbName yijian`。
    [string]$DbName  = "",
    [string]$DbUser  = "yijian",
    # 想跑完留着一性库、事后连上去看看？加这个开关（此时不销毁）。
    [switch]$KeepDb,
    # 覆盖率门槛**不在本文件里写数字** —— 单一来源是仓库根 `.coveragerc` 的 `fail_under`，
    # `coverage report` 会自己按它判退出码。这里只负责"跑报告 + 看退出码"。
    [switch]$NoCoverage,
    [switch]$KeepRunning
)

# 本脚本大量调用外部程序（psql / pg_ctl / python）。统一用 Continue，
# 再靠 $LASTEXITCODE 显式判断，避免外部程序往 stderr 写一行就被判为致命错误。
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here "..\..")).Path
$bin  = Join-Path $Prefix "Library\bin"
$psql = Join-Path $bin "psql.exe"
$env:PGCLIENTENCODING = "UTF8"

$apiProc = $null
# 本次是否跑在**自己建的一次性库**上（§2 里置位）—— finally 据此决定要不要销毁。
# 在这里初始化而不是在 §2：§2 之前若就 Fail，finally 仍会读它。
$ephemeral = $false

function Fail($msg) {
    Write-Host "[local-verify] 失败：$msg" -ForegroundColor Red
    exit 1
}

# 注：原先这里有个 `Invoke-Psql` 包装函数。建库/建表/迁移委托给 `smoke-db.py` 之后，
# 它**没有任何调用点**了 ⇒ 2026-09-27 删除。留着它就是留一个"偷偷建库的第二入口"，
# 与本批"逻辑只能有一份"的原则直接冲突（BL-17）。
# 仍需 psql 时：`& $psql -h 127.0.0.1 -p $Port -U $DbUser -d <db> -tAc "<sql>"`。

try {
    # ---------- 0. 本地门禁预检（静态 4 道，fail-fast）----------
    # 本地共 **6 道**门禁：这里 4 道（诊断自检 / 不变量 / ruff check / ruff format）
    # + 后面的 pytest / 覆盖率 2 道。**CI 另有 7 道前端** —— 差几道、为什么差，
    # 见 `tools/preflight.sh` 打印的覆盖矩阵与 `docs/24` §10。
    # ⚠️ 它需要 bash（那 4 道里有两道是 bash harness）。找不到就**大声跳过**：
    #    "跳过"是**少查了 4 道**，不是"查过了"（硬约定 H 的同族）。
    $bashCmd = Get-Command bash -ErrorAction SilentlyContinue
    if (-not $bashCmd) {
        Write-Host "[local-verify] !! 找不到 bash ⇒ 跳过本地门禁预检（少查 4 道，不等于通过）"
        Write-Host "[local-verify]    装了 Git Bash 之后手工跑：bash tools/preflight.sh"
    } else {
        & $bashCmd.Source (Join-Path $repo "tools/preflight.sh")
        if ($LASTEXITCODE -ne 0) { Fail "本地门禁预检未通过 —— 先修这里，别往下跑" }
    }

    # ---------- 1. PostgreSQL ----------
    & (Join-Path $here "start-pg.ps1") -Prefix $Prefix -DataDir $DataDir -Port $Port -DbUser $DbUser
    if ($LASTEXITCODE -ne 0) { Fail "PostgreSQL 启动失败" }

    # ---------- 1.5 选一个「真的装了依赖」的 Python ----------
    # 教训：PATH 上的 `python` 很可能是 Anaconda 之类，未必有 fastapi/asyncpg/fakeredis。
    # 这里按优先级逐个探测，第一个能 import 全部依赖的才用。
    # ⚠️ `greenlet` 必须在列：`.coveragerc` 的 `[run] concurrency = greenlet` 依赖它。
    #    它是 `sqlalchemy[asyncio]` 的传递依赖（本地"碰巧装了"≠ CI 装得到），
    #    所以在这里显式声明为前置条件 —— 缺了要**当场报错**，而不是等覆盖率先跑出个错数字。
    #
    # ⚠️ 2026-09-27 上移：这一段原先排在"建库/建表/迁移"之后，但那段现在委托给
    #    `smoke-db.py`（BL-17），而**调 Python 工具必须先有解释器** ⇒ 探测必须最先做。
    $probe = "import fastapi, sqlalchemy, asyncpg, fakeredis, uvicorn, pytest, httpx, coverage, greenlet"
    $cands = New-Object System.Collections.Generic.List[string]
    if ($Python -and $Python -ne "python") { $cands.Add($Python) }
    if ($env:YIJIAN_PYTHON) { $cands.Add($env:YIJIAN_PYTHON) }
    $cands.Add("$env:USERPROFILE\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
    $cands.Add("python")

    $pyExe = $null
    foreach ($c in $cands) {
        $src = $c
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
            $g = Get-Command $c -ErrorAction SilentlyContinue
            if ($g) { $src = $g.Source } else { continue }
        }
        & $src -c $probe 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $pyExe = $src; break }
    }
    if (-not $pyExe) {
        # 注意：这里必须写成单行 —— PowerShell 5.1 在命令参数位置不接受跨行的括号表达式。
        Fail "未找到满足依赖的 Python（需 fastapi/sqlalchemy/asyncpg/fakeredis/uvicorn/pytest/httpx/coverage/greenlet）。可用 -Python <绝对路径> 指定，或先在目标解释器执行： pip install -r apps/api/requirements.txt"
    }
    Write-Host "[local-verify] 使用 Python: $pyExe"

    # ---------- 2. 库：默认**一次性**（BL-17）----------
    # 为什么整段换成调 `smoke-db.py`，而不是继续在这里用 psql 写：
    #   ① **逻辑只能有一份** —— 同一个「建库 + schema + migrations」若 PS 写一份、
    #      另一处再写一份，迟早漂（本文件「题库种子」那段已立过同一条规矩：两边各写一套
    #      就又回到"本地绿、CI 红"的口径分歧）；
    #   ② 一次性库必须带**安全闸**（拒绝误删开发库）与**自检**，而这两样都要落成
    #      可执行代码才好被验证（`smoke-db.py --self-test`，硬约定 H/J）。
    # ⚠️ 仍然必须载入 db/migrations/：schema.sql **只在首次建库时**载入，之后的
    #    「加列 / 回填 / 补种子权限」全靠迁移；只改 schema.sql 对已有库**永远不生效**
    #    （表现为"新代码读一个不存在的列"）。所有迁移脚本自身幂等，可无条件重跑。
    if (-not $DbName) {
        # 名字由 smoke-db.py 生成（`yijian_smoke_<pid>`）——单一来源，前缀可 grep。
        $DbName = (& $pyExe (Join-Path $here "smoke-db.py") name).Trim()
        $ephemeral = $true
        Write-Host "[local-verify] 一次性库：$DbName（跑完销毁；要保留请加 -KeepDb）"
    } else {
        Write-Host "[local-verify] 注意：显式指定 -DbName $DbName ⇒ 不销毁，且该库可能**跨会话累积** —— 与 CI 的干净基线不可比"
    }
    & $pyExe (Join-Path $here "smoke-db.py") provision `
        --psql $psql --port $Port --user $DbUser --db $DbName --repo $repo
    if ($LASTEXITCODE -ne 0) { Fail "建库 / 建表 / 迁移失败（smoke-db.py provision --db $DbName）" }
    Write-Host "[local-verify] 建库完成（schema.sql + migrations）"

    # 注：原先这里还有「第 3 步（建表）」「第 3.5 步（迁移）」「第 4 步（选 Python）」——
    #     ① 建表 / 迁移已并入 §2 的 `smoke-db.py provision`（同一件事**逻辑只留一份**）；
    #     ② 选 Python 已上移到 §1.5（调 Python 工具必须先有解释器）。
    #     ⇒ 编号跳过 3 / 4 **不是遗漏**，是这两段被合并/上移了。

    # ---------- 4.2 题库种子（生成 + 灌库）----------
    # ⚠️ 为什么必须有这一步（坑 51）：`db/schema.sql` 只灌 subjects / chapters / RBAC，
    #    **不含 questions 与 knowledge_points**；题库由 `db/seed/gen_seed_questions.py` 产出，
    #    而生成物落在 `data/`（**gitignored**）。没有这一步，在**真正全新的库**上会有
    #    32 条用例失败（组卷 / 加题 / 知识点下拉无题可抽）—— 本脚本过去一直绿，
    #    只是因为开发机的库早先被手工灌过种子题。那是**假绿**（硬约定 H）。
    #
    # ⚠️ 生成 + 灌库的**逻辑只有一份**：CI（.github/workflows/ci.yml）调的是同一个脚本。
    #    两边各写一套的话迟早漂，就又回到"本地绿、CI 红"的口径分歧。
    #
    # ⚠️ 刻意**不做**"库里已有 6000 题就跳过"的快捷判断 —— 那正是假绿的成因
    #    （"环境恰好脏"时跳过 → 行为与干净环境不同）。SQL 自带 ON CONFLICT，无条件重跑即可。
    Write-Host "[local-verify] 灌题库种子（生成 + 灌库，幂等，约 5s）..."
    Push-Location $here
    try {
        & $pyExe (Join-Path $here "seed-questions.py") --pg-port $Port --db $DbName --db-user $DbUser --psql $psql
        $rcSeedQ = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rcSeedQ -ne 0) { Fail "题库种子灌入失败（gen_seed_questions.py / psql）" }

    # ---------- 4.5 初始化超管（等价于 docker-entrypoint.sh 里的 seed-admin）----------
    # schema.sql 只灌 roles/permissions，不含任何用户；超管由 app.cli 创建。
    # 不跑这一步，tests/test_smoke.py::test_rbac_flow 会因「超管登录失败」被 skip，
    # 于是 POST /admin/users/{id}/roles（以及 ANY(CAST(:uids AS bigint[]))）就永远没被覆盖。
    $env:DATABASE_URL           = "postgresql+asyncpg://yijian@127.0.0.1:$Port/$DbName"
    $env:APP_ENV                = "local"
    $env:JWT_SECRET             = "local-verify-secret-not-for-production"
    $env:ADMIN_INIT_PHONE       = "13800000000"
    $env:ADMIN_INIT_PASSWORD    = "Admin@123456"

    # ---------- 4.55 覆盖率数据文件的三个去向（硬约定 L：跨进程执行 = 跨进程测量）----------
    # ⚠️ **三份分开采、最后 combine**，而不是只采 API 进程：
    #      .coverage.api    API 进程（HTTP 用例打到的业务代码）
    #      .coverage.cli    CLI 进程（seed-rbac / seed-admin 跑在**另一个进程**里）
    #      .coverage.tests  pytest 进程（单元测试**直接调 app 代码**的那部分）
    # 少任何一份，那条路径的执行就"不在账上"，而覆盖率**看起来仍然正常** ——
    # 这正是硬约定 J / L 要防的假数字（实测：cli.py 曾报 190/190 全未覆盖，
    # 而冒烟日志里它明明打印过 `[cli] RBAC seed replayed`）。
    # 采集前先清场：否则 --append 会把上一轮的数据攒进来，数字虚高且不可复现。
    #
    # ---- ⚠️ 2026-09-25 改：按「**整族**」清，不再只删"本脚本会产生的那几个名字" ----
    # 起因（实测）：仓库根残留了一个 **9-24 的 `.coverage`**（**不是当次产生的**）。
    # 它给出 4567/**218** → 95.23%，而权威值是 4567/**210** → 95.40%（差 8 行，全在
    # `stats_service.py`）。把它当"本地数字"去和 CI 比 → 得到**假红**，
    # 然后去查一个根本不存在的问题（硬约定 J 的反面）。
    # ⇒ **只删"自己产生的名字"，就永远清不掉"上一次改了命名方案后留下的"那种文件。**
    # 口径直接对齐 `.gitignore`：`.coverage` / `.coverage.*` / `coverage.xml` / `htmlcov/`。
    #
    # 🔴 **绝对不要写成 `Remove-Item .coverage*`** —— 那个通配符**会匹配 `.coveragerc`**，
    # 把门禁配置本身删掉（已实测：`Get-ChildItem .coverage*` → `.coverage | .coveragerc`）。
    # 带点的 `.coverage.*` 才安全（`.coveragerc` 里 `.coverage` 后面跟的是 `r`，不是 `.`）。
    # 下面还有一道"配置还在吗"的断言兜底 —— 清场最坏的结果就是静默毁掉门禁。
    # <<< COV-CLEAN:START >>>  （标记供 `tools/local-verify/test-cov-clean.ps1` 提取本段做**单元验证**：
    #   清场本身是个"删文件"的动作，删错比不删危险 —— 所以它必须有测试，见硬约定 H）
    #
    # ⚠️ **标记必须包住变量定义**：这一段对外的输入只有 `$repo` 与 `$NoCoverage`。
    #    第一版把标记写在 `$covStopFile` **之后**，于是别的 `foreach` 拿到 `$null`
    #    —— 是 harness 报的"提取段不自包含"，不是脚本本身的错（跑起来时变量在）。
    #    但"能被独立提取"正是它能被测的前提，所以标记上移到这里。
    $covApi      = Join-Path $repo ".coverage.api"
    $covCli      = Join-Path $repo ".coverage.cli"
    $covTests    = Join-Path $repo ".coverage.tests"
    $covDataFile = Join-Path $repo ".coverage"          # combine 之后的最终报告用
    $covStopFile = Join-Path $env:TEMP "yijian-cov-stop"
    if (-not $NoCoverage) {
        # 1) 整族：`.coverage.*`（含未来新增的第 4 份采集文件，无需再改这里）
        $staleCov = Get-ChildItem -Path (Join-Path $repo ".coverage.*") -File -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -ne ".coveragerc" }
        foreach ($f in $staleCov) {
            Write-Host ("[local-verify] 清场：删除历史覆盖率文件 {0}" -f $f.Name)
            # trash-ok: 单文件（一个历史覆盖率文件），非递归
            Remove-Item -Path $f.FullName -Force -ErrorAction SilentlyContinue
        }
        # 2) 单个名字 + 派生物（`.gitignore` 里同族的另外两类）
        foreach ($f in @($covDataFile, (Join-Path $repo "coverage.xml"), $covStopFile)) {
            # trash-ok: 单文件（同上），非递归
            Remove-Item -Path $f -Force -ErrorAction SilentlyContinue
        }
        # ★ `htmlcov/` 是**目录** ⇒ 按项目约定 R **只能 rename**（宿主的删除保护会拦递归删除；
        #   实测过一次 `htmlcov/` 55 项就把后续的 `coverage combine` 额度吃光了，坑 62）。
        #   落在 `<repo>/.trash/coverage/`，由宿主或人回收。★ `test-cov-clean.ps1` 断言的是
        #   "它**不在原处**"（`-not (Test-Path .../htmlcov)`）—— rename 与删除在这条判据上**等价**，
        #   所以这个改动没有削弱那条测试。
        $htmlcov = Join-Path $repo "htmlcov"
        if (Test-Path -LiteralPath $htmlcov) {
            $trashCov = Join-Path (Join-Path $repo ".trash") "coverage"
            New-Item -ItemType Directory -Path $trashCov -Force | Out-Null
            $dst = Join-Path $trashCov ("htmlcov-" + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
            Move-Item -LiteralPath $htmlcov -Destination $dst -Force -ErrorAction SilentlyContinue
        }

        # 3) 兜底断言：清场最坏的结果是"把 .coveragerc 也删了"，而那样门禁会**静默失效**
        #    （覆盖率照跑，只是没了门槛）。宁可当场报错，也不要跑出一个"通过"的假象。
        if (-not (Test-Path (Join-Path $repo ".coveragerc"))) {
            # trash-ok: 这是**错误提示文本**里的字面量，不是删除动作
            Fail "清场把 .coveragerc 删掉了 —— 检查 Remove-Item 的通配符（`.coverage*` 会匹配 `.coveragerc`，要用 `.coverage.*`）"
        }
    }
    # <<< COV-CLEAN:END >>>

    Write-Host "[local-verify] 重放 RBAC 种子（幂等，补 viewer 等后加角色）..."
    Push-Location (Join-Path $repo "apps\api")
    try {
        # CLI 跑在**它自己的进程**里 → 必须在**它自己的进程**里插桩（硬约定 L）。
        # --append：seed-rbac 与 seed-admin 是两次进程，共用一份 .coverage.cli
        # （实测 --append 在文件不存在时不报错，直接创建）。
        & $pyExe -m coverage run --append --data-file $covCli --source app --rcfile (Join-Path $repo ".coveragerc") -m app.cli seed-rbac
        $rcRbac = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rcRbac -ne 0) { Fail "seed-rbac 失败（角色/权限种子）" }

    Write-Host "[local-verify] 初始化超级管理员 ..."
    Push-Location (Join-Path $repo "apps\api")
    try {
        & $pyExe -m coverage run --append --data-file $covCli --source app --rcfile (Join-Path $repo ".coveragerc") -m app.cli seed-admin
        $rcSeed = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rcSeed -ne 0) { Fail "seed-admin 失败（超管初始化）" }

    # ---------- 4.6 端口占用预检 ----------
    # 坑（2026-09-15 实测踩到）：上一轮遗留的 API 进程还占着 $ApiPort 时，
    # 新 API 会因端口被占而启动失败，但下面的健康检查会**打到那个旧进程上并通过** ——
    # 于是整套验收静默地测在旧代码上。新接口一律 404 才会暴露，运气好就"全绿"。
    # 假绿比直接失败危险得多，所以这里宁可硬失败。
    $occupied = @()
    try {
        $occupied = @(Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction Stop)
    } catch { }
    if ($occupied.Count -gt 0) {
        $pids = ($occupied | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
        Fail "端口 $ApiPort 已被占用（pid: $pids）。这多半是上一轮遗留的 API 进程 —— 不先杀掉它，本次验收会静默测到旧代码上。请先执行： Stop-Process -Id $pids -Force"
    }
    Write-Host "[local-verify] 端口 $ApiPort 空闲，预检通过"

    # ---------- 5. 起 API（真 PG + fakeredis，默认带覆盖率采集）----------
    $apiLog = Join-Path $env:TEMP "yijian-api.log"

    # 覆盖率数据文件已在 §4.55 定义并清场（三份分开采 + 最后 combine）。
    # 这里只说明**为什么 API 进程是采集大头**：用例是 HTTP 打到它的，
    # 业务代码全跑在那边；在 pytest 进程里跑 --cov=app 只会量到测试自己。

    Write-Host "[local-verify] 启动 API :$ApiPort ..."
    # 用 ProcessStartInfo + UseShellExecute=$true 让 API 进程「完全脱离」当前 stdio。
    # 否则这个长生命周期子进程会占着调用方的 stdout 管道句柄，调用方要等它退出才拿到 EOF（卡死）。
    # 既然脱离了 stdio，API 日志由 serve_fake_redis.py 自己写入 --log-file。
    $apiArgs = "`"$(Join-Path $here 'serve_fake_redis.py')`" --pg-port $Port --api-port $ApiPort --log-file `"$apiLog`""
    if (-not $NoCoverage) {
        # ⚠️ --shutdown-file 是必须的：本脚本收尾用的是 Stop-Process -Force（硬杀），
        #    进程直接消失、atexit 不跑 → coverage 一个字都写不出来。改成"放哨兵文件 →
        #    进程自己收尾"（见 serve_fake_redis.py 的说明）。
        $apiArgs += " --coverage --cov-data-file `"$covApi`" --shutdown-file `"$covStopFile`""
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName         = $pyExe
    $psi.Arguments        = $apiArgs
    $psi.WorkingDirectory = $here
    $psi.UseShellExecute  = $true
    $psi.WindowStyle      = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $apiProc = [System.Diagnostics.Process]::Start($psi)

    $ready = $false
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 500
        if ($apiProc.HasExited) { break }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v1/health" `
                 -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Write-Host "[local-verify] API 未就绪，日志尾部（$apiLog）："
        if (Test-Path $apiLog) { Get-Content $apiLog -Tail 40 }
        Fail "API 启动失败"
    }
    Write-Host "[local-verify] API 已就绪 -> http://127.0.0.1:$ApiPort/docs"

    # ---------- 6. 冒烟 ----------
    # 同时把 pytest 输出落一份到 $pytestLog，便于事后核对（终端里也照常显示）。
    $pytestLog = Join-Path $env:TEMP "yijian-pytest.log"
    $env:AI_BASE = "http://127.0.0.1:$ApiPort"
    Push-Location (Join-Path $repo "apps\api")
    try {
        # pytest 进程也要插桩：单元测试里有**直接调 app 代码**的用例
        # （test_idgen / test_admin_v4::test_content_hash_and_answer_derivation），
        # 它们在 API 进程的账上根本不会出现。
        # ⚠️ `-rfEXs`（**不是** `-rs`）—— 两个理由，缺一个都会让"没跑 / 挂了"变得不可见：
        #   ① `-rs` 打印每条 skip 的**文件:行号 + 原因**，这本身是必需的：裸的 `N skipped`
        #      会把"某条用例从没跑过"藏起来 —— 坑 55 就这样在 CI 上藏了几个月
        #      （覆盖率上只表现为"少 13 行"）。硬约定 M 的同一条道理。
        #   ② ★ 但 `-r` 是**替换**默认值：只写 `-rs` 会把默认的 `-rfE` 一起顶掉，
        #      ⇒ **短汇总里根本没有 `FAILED` 行**（2026-09-26 本机实测）。
        #      而那一行正是"哪条用例挂了"的第一手信息 ⇒ 必须写成 `-rfEXs`。
        #      ⚠️ 本文件与 CI 的 pytest 步骤**必须同口径**（CI 那边同样是 `-rfEXs`）。
        & $pyExe -m coverage run --data-file $covTests --source app --rcfile (Join-Path $repo ".coveragerc") -m pytest tests -v --tb=short -rfEXs 2>&1 | Tee-Object -FilePath $pytestLog
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rc -ne 0) { Fail "pytest 未全部通过（exit=$rc，详见 $pytestLog）" }

    # ---------- 7. 覆盖率门禁 ----------
    # ⚠️ 顺序很关键：**必须先让 API 优雅退出**（放哨兵 → 它自己 cov.save() 写盘），
    #    再出报告。若直接跳到 finally 的 Stop-Process -Force，数据文件是空的，
    #    报告会变成 0%（而不是报错）—— 正是硬约定 H 说的"假绿"的另一种形态。
    if (-not $NoCoverage) {
        Write-Host "[local-verify] 停止 API 并落盘覆盖率 ..."
        Set-Content -Path $covStopFile -Value "stop" -Encoding ascii
        $waited = 0
        while (-not $apiProc.HasExited -and $waited -lt 40) {
            Start-Sleep -Milliseconds 500
            $waited++
        }
        if (-not $apiProc.HasExited) {
            Fail "API 收到哨兵后 20s 仍未退出 —— 覆盖率数据可能没落盘，不要相信本次覆盖率"
        }
        if (-not (Test-Path $covApi) -or (Get-Item $covApi).Length -eq 0) {
            Fail "API 进程的覆盖率数据为空（$covApi）—— 采集没生效，本次数字不可信"
        }

        # 硬约定 L / 用户约束③：三份都必须存在且非空，否则**报错而不是静默合并**。
        # 静默合并的后果是"某条路径根本没跑到，但覆盖率看着正常" —— 假数字。
        foreach ($pair in @(
            @(".coverage.api  （API 进程）", $covApi),
            @(".coverage.cli  （CLI 进程）", $covCli),
            @(".coverage.tests（pytest 进程）", $covTests))) {
            if (-not (Test-Path $pair[1]) -or (Get-Item $pair[1]).Length -eq 0) {
                Fail ("覆盖率数据缺失或为空：{0} —— 拒绝静默合并。三个进程都要有数据，缺一份就说明那条路径的执行不在账上。" -f $pair[0])
            }
        }

        Write-Host "[local-verify] 合并三份覆盖率数据（api / cli / tests）..."
        foreach ($pair in @(@("api", $covApi), @("cli", $covCli), @("tests", $covTests))) {
            Write-Host ("  {0,-6} {1,8:N0} bytes" -f $pair[0], (Get-Item $pair[1]).Length)
        }
        # ⚠️ combine 会把**输入文件删掉**（已实测）；每个文件里的路径是相对路径，
        #    所以必须从仓库根跑，与下面的 report 一致。
        Push-Location $repo
        try {
            $combineOut = & $pyExe -m coverage combine --data-file $covDataFile $covApi $covCli $covTests 2>&1
            $rcCombine = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($rcCombine -ne 0) {
            $combineOut | ForEach-Object { Write-Host "  $_" }
            Fail "coverage combine 失败（exit=$rcCombine）—— 不继续出报告，避免把不完整的数据当成结论"
        }
        $combineOut | ForEach-Object { Write-Host "  $_" }
        if (-not (Test-Path $covDataFile) -or (Get-Item $covDataFile).Length -eq 0) {
            Fail "combine 之后的 $covDataFile 为空 —— 合并没生效"
        }

        Write-Host ""
        Write-Host "[local-verify] 覆盖率（门槛见仓库根 .coveragerc 的 fail_under）..."
        # 报告从仓库根跑：数据文件里的路径是相对的，换目录会报 "No data to report"。
        Push-Location $repo
        try {
            $covOut = & $pyExe -m coverage report --data-file $covDataFile --skip-covered 2>&1
            $covRc  = $LASTEXITCODE
            $covOut | Out-String | Set-Content -Path (Join-Path $env:TEMP "yijian-coverage.log") -Encoding UTF8
            # 总览行单独打一次，避免被逐文件明细淹没
            ($covOut | Select-String -Pattern "^TOTAL") | ForEach-Object { Write-Host "  $($_.Line)" -ForegroundColor Cyan }
            if ($covRc -ne 0) {
                Write-Host "[local-verify] 逐文件明细（$env:TEMP\yijian-coverage.log）："
                $covOut | Select-Object -Last 12 | ForEach-Object { Write-Host "  $_" }
            }
        } finally {
            Pop-Location
        }
        if ($covRc -ne 0) {
            Fail "覆盖率低于 .coveragerc 里的 fail_under（exit=$covRc，详见 $env:TEMP\yijian-coverage.log）"
        }
    } else {
        Write-Host "[local-verify] 按 -NoCoverage 跳过覆盖率采集"
    }

    Write-Host ""
    Write-Host "[local-verify] 全部通过。" -ForegroundColor Green
}
finally {
    if ($apiProc -and -not $apiProc.HasExited) {
        Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "[local-verify] API 已停止"
    }
    # ---------- 销毁一次性库（BL-17）----------
    # ⚠️ 顺序：**API 停掉之后**再 DROP —— DROP DATABASE 要求"没有活动连接"；虽然
    #    smoke-db.py 会先 pg_terminate_backend，让连接自己散干净更省事。
    # ⚠️ **只在本次是自己建的一次性库时才删**。显式 `-DbName` 传进来的库（比如开发库
    #    `yijian`）绝不在这里碰 —— `smoke-db.py` 的 `assert_smoke_db` 是第二道闸
    #    （名字不匹配 `yijian_smoke_*` 即拒绝，硬约定 O）。
    if ($ephemeral) {
        if ($KeepDb) {
            Write-Host "[local-verify] 按 -KeepDb 保留一次性库 $DbName（记得手动清理）" -ForegroundColor Yellow
        } elseif ($pyExe) {
            & $pyExe (Join-Path $here "smoke-db.py") drop --psql $psql --port $Port --user $DbUser --db $DbName
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[local-verify] 已销毁一次性库 $DbName"
            } else {
                Write-Host "[local-verify] ⚠️ 销毁一次性库 $DbName 失败 —— 下次 provision 会先收掉它（同名残留保护）" -ForegroundColor Yellow
            }
        }
    }
    if (-not $KeepRunning) {
        & (Join-Path $here "stop-pg.ps1") -Prefix $Prefix -DataDir $DataDir -Port $Port
    } else {
        Write-Host "[local-verify] 按 -KeepRunning 保留 PostgreSQL 运行"
    }
}
