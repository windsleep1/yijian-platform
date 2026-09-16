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

    # ---------- 4. 选一个「真的装了依赖」的 Python ----------
    # 教训：PATH 上的 `python` 很可能是 Anaconda 之类，未必有 fastapi/asyncpg/fakeredis。
    # 这里按优先级逐个探测，第一个能 import 全部依赖的才用。
    $probe = "import fastapi, sqlalchemy, asyncpg, fakeredis, uvicorn, pytest, httpx"
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
        Fail "未找到满足依赖的 Python（需 fastapi/sqlalchemy/asyncpg/fakeredis/uvicorn/pytest/httpx）。可用 -Python <绝对路径> 指定，或先在目标解释器执行： pip install -r apps/api/requirements.txt fakeredis pytest httpx"
    }
    Write-Host "[local-verify] 使用 Python: $pyExe"

    # ---------- 4.5 初始化超管（等价于 docker-entrypoint.sh 里的 seed-admin）----------
    # schema.sql 只灌 roles/permissions，不含任何用户；超管由 app.cli 创建。
    # 不跑这一步，tests/test_smoke.py::test_rbac_flow 会因「超管登录失败」被 skip，
    # 于是 POST /admin/users/{id}/roles（以及 ANY(CAST(:uids AS bigint[]))）就永远没被覆盖。
    $env:DATABASE_URL           = "postgresql+asyncpg://yijian@127.0.0.1:$Port/$DbName"
    $env:APP_ENV                = "local"
    $env:JWT_SECRET             = "local-verify-secret-not-for-production"
    $env:ADMIN_INIT_PHONE       = "13800000000"
    $env:ADMIN_INIT_PASSWORD    = "Admin@123456"

    Write-Host "[local-verify] 重放 RBAC 种子（幂等，补 viewer 等后加角色）..."
    Push-Location (Join-Path $repo "apps\api")
    try {
        & $pyExe -m app.cli seed-rbac
        $rcRbac = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rcRbac -ne 0) { Fail "seed-rbac 失败（角色/权限种子）" }

    Write-Host "[local-verify] 初始化超级管理员 ..."
    Push-Location (Join-Path $repo "apps\api")
    try {
        & $pyExe -m app.cli seed-admin
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

    # ---------- 5. 起 API（真 PG + fakeredis）----------
    $apiLog = Join-Path $env:TEMP "yijian-api.log"

    Write-Host "[local-verify] 启动 API :$ApiPort ..."
    # 用 ProcessStartInfo + UseShellExecute=$true 让 API 进程「完全脱离」当前 stdio。
    # 否则这个长生命周期子进程会占着调用方的 stdout 管道句柄，调用方要等它退出才拿到 EOF（卡死）。
    # 既然脱离了 stdio，API 日志由 serve_fake_redis.py 自己写入 --log-file。
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName         = $pyExe
    $psi.Arguments        = "`"$(Join-Path $here 'serve_fake_redis.py')`" --pg-port $Port --api-port $ApiPort --log-file `"$apiLog`""
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
        & $pyExe -m pytest tests -v --tb=short 2>&1 | Tee-Object -FilePath $pytestLog
        $rc = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($rc -ne 0) { Fail "pytest 未全部通过（exit=$rc，详见 $pytestLog）" }

    # 收尾前确认：本次 pytest 打的确实是刚起的那个进程（pid 已随 finally 停止）。
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
