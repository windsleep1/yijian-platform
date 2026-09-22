# 一键本地验收：起 PostgreSQL → 建库 → 载入 schema → 起 API → 跑 pytest → 收尾。
# 不需要 Docker。各步骤幂等，可重复执行。
#
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepRunning
#
# 若自动挑选的 Python 解释器不对，可显式指定：
#   ... -Python "C:\path\to\python.exe"
#
[CmdletBinding()]
param(
    [string]$Prefix  = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16",
    [string]$DataDir = "$env:USERPROFILE\.workbuddy\binaries\pg\data16",
    [int]   $Port    = 55432,
    [int]   $ApiPort = 8123,
    [string]$Python  = "python",
    [string]$DbName  = "yijian",
    [string]$DbUser  = "yijian",
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

function Fail($msg) {
    Write-Host "[local-verify] 失败：$msg" -ForegroundColor Red
    exit 1
}

function Invoke-Psql {
    param([string[]]$PsqlArgs, [switch]$Quiet)
    $out = & $psql -h 127.0.0.1 -p $Port -U $DbUser @PsqlArgs 2>&1
    $rc  = $LASTEXITCODE
    $text = ($out | Out-String).Trim()
    if ($rc -ne 0 -and -not $Quiet) {
        Write-Host "[local-verify] psql 输出："
        Write-Host $text
    }
    return [pscustomobject]@{ Text = $text; ExitCode = $rc }
}

try {
    # ---------- 1. PostgreSQL ----------
    & (Join-Path $here "start-pg.ps1") -Prefix $Prefix -DataDir $DataDir -Port $Port -DbUser $DbUser
    if ($LASTEXITCODE -ne 0) { Fail "PostgreSQL 启动失败" }

    # ---------- 2. 建库（幂等）----------
    $r = Invoke-Psql -PsqlArgs @("-d", "postgres", "-tAc",
        "SELECT 1 FROM pg_database WHERE datname='$DbName'")
    if ($r.Text.Trim() -ne "1") {
        Write-Host "[local-verify] 创建数据库 $DbName ..."
        $c = Invoke-Psql -PsqlArgs @("-d", "postgres", "-q", "-c", "CREATE DATABASE $DbName")
        if ($c.ExitCode -ne 0) { Fail "创建数据库 $DbName 失败" }
    } else {
        Write-Host "[local-verify] 数据库 $DbName 已存在"
    }

    # ---------- 3. 建表（幂等）----------
    $r = Invoke-Psql -PsqlArgs @("-d", $DbName, "-tAc", "SELECT to_regclass('public.users')")
    if ([string]::IsNullOrWhiteSpace($r.Text)) {
        Write-Host "[local-verify] 载入 db/schema.sql ..."
        $schema = Join-Path $repo "db\schema.sql"
        if (-not (Test-Path $schema)) { Fail "找不到 $schema" }
        $s = Invoke-Psql -PsqlArgs @("-d", $DbName, "-q", "-v", "ON_ERROR_STOP=1", "-f", $schema)
        if ($s.ExitCode -ne 0) { Fail "schema.sql 执行失败" }
        $n = Invoke-Psql -PsqlArgs @("-d", $DbName, "-tAc",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
        Write-Host "[local-verify] 建表完成，public 下 $($n.Text) 张表"
    } else {
        Write-Host "[local-verify] 表已存在，跳过建表"
    }

    # ---------- 3.5 迁移（幂等，按文件名排序）----------
    # ⚠️ 为什么必须有这一步：schema.sql **只在首次建库时**载入（上面那个 to_regclass 判断），
    #    库已存在时整段跳过。所以任何"加列 / 回填 / 补种子权限"如果只改 schema.sql，
    #    对已有数据的库**永远不生效** —— 表现为"新代码读一个不存在的列"。
    #    所有迁移脚本必须自身幂等（ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING），
    #    这样才能无条件重跑。
    $migDir = Join-Path $repo "db\migrations"
    if (Test-Path $migDir) {
        $migs = @(Get-ChildItem -Path $migDir -Filter *.sql | Sort-Object Name)
        if ($migs.Count -gt 0) {
            Write-Host "[local-verify] 应用 $($migs.Count) 个迁移脚本 ..."
            foreach ($m in $migs) {
                $mr = Invoke-Psql -PsqlArgs @("-d", $DbName, "-q", "-v", "ON_ERROR_STOP=1", "-f", $m.FullName)
                if ($mr.ExitCode -ne 0) { Fail "迁移 $($m.Name) 执行失败" }
                Write-Host "[local-verify]   ✓ $($m.Name)"
            }
        }
    }

    # ---------- 4. 选一个「真的装了依赖」的 Python ----------
    # 教训：PATH 上的 `python` 很可能是 Anaconda 之类，未必有 fastapi/asyncpg/fakeredis。
    # 这里按优先级逐个探测，第一个能 import 全部依赖的才用。
    # ⚠️ `greenlet` 必须在列：`.coveragerc` 的 `[run] concurrency = greenlet` 依赖它。
    #    它是 `sqlalchemy[asyncio]` 的传递依赖（本地"碰巧装了"≠ CI 装得到），
    #    所以在这里显式声明为前置条件 —— 缺了要**当场报错**，而不是等覆盖率先跑出个错数字。
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
    $covApi      = Join-Path $repo ".coverage.api"
    $covCli      = Join-Path $repo ".coverage.cli"
    $covTests    = Join-Path $repo ".coverage.tests"
    $covDataFile = Join-Path $repo ".coverage"          # combine 之后的最终报告用
    $covStopFile = Join-Path $env:TEMP "yijian-cov-stop"
    if (-not $NoCoverage) {
        foreach ($f in @($covApi, $covCli, $covTests, $covDataFile)) {
            Remove-Item -Path $f -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -Path $covStopFile -Force -ErrorAction SilentlyContinue
    }

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
        # ⚠️ `-rs`（打印每条 skip 的**文件:行号 + 原因**）不是装饰，是必需：
        #    一个裸的 `N skipped` 会把"某条用例从没跑过"藏起来 —— 坑 55 就是这样
        #    在 CI 上藏了几个月的（覆盖率上只表现为"少 13 行"）。硬约定 M 的同一条道理：
        #    **把"没跑"这件事本身变成可见信息**。CI 的 pytest 步骤也带 `-rs`（保持两边一致）。
        & $pyExe -m coverage run --data-file $covTests --source app --rcfile (Join-Path $repo ".coveragerc") -m pytest tests -v --tb=short -rs 2>&1 | Tee-Object -FilePath $pytestLog
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
    if (-not $KeepRunning) {
        & (Join-Path $here "stop-pg.ps1") -Prefix $Prefix -DataDir $DataDir -Port $Port
    } else {
        Write-Host "[local-verify] 按 -KeepRunning 保留 PostgreSQL 运行"
    }
}
