# 起后端并**保持运行**，用于前后端联调（对比 run-smoke.ps1：那个是跑完就收尾）。
#
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/serve-local.ps1
#   powershell -ExecutionPolicy Bypass -File tools/local-verify/serve-local.ps1 -Stop
#
# 做四件事：起 PostgreSQL → 建库/载入 schema（幂等）→ 重放 RBAC 种子 + 初始化超管 → 起 API（后台常驻）。
# 脚本本身会退出，API 与 PostgreSQL 留在后台。API 的 PID 写在 %TEMP%\yijian-api.pid。
#
# 各步骤幂等，可重复执行。
[CmdletBinding()]
param(
    [string]$Prefix  = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16",
    [string]$DataDir = "$env:USERPROFILE\.workbuddy\binaries\pg\data16",
    [int]   $Port    = 55432,
    [int]   $ApiPort = 8123,
    [string]$Python  = "python",
    [string]$DbName  = "yijian",
    [string]$DbUser  = "yijian",
    [switch]$Stop,
    [switch]$SkipSeed,
    [switch]$Foreground
)

$ErrorActionPreference = "Continue"
$here    = $PSScriptRoot
$repo    = (Resolve-Path (Join-Path $here "..\..")).Path
$bin     = Join-Path $Prefix "Library\bin"
$psql    = Join-Path $bin "psql.exe"
$pidFile = Join-Path $env:TEMP "yijian-api.pid"
$apiLog  = Join-Path $env:TEMP "yijian-api.log"
$env:PGCLIENTENCODING = "UTF8"

function Fail($msg) {
    Write-Host "[serve-local] 失败：$msg" -ForegroundColor Red
    exit 1
}

function Invoke-Psql {
    param([string[]]$PsqlArgs)
    $out = & $psql -h 127.0.0.1 -p $Port -U $DbUser @PsqlArgs 2>&1
    return [pscustomobject]@{ Text = ($out | Out-String).Trim(); ExitCode = $LASTEXITCODE }
}

# ---------------------------------------------------------------- -Stop
if ($Stop) {
    if (Test-Path $pidFile) {
        $apiPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($apiPid) {
            Stop-Process -Id ([int]$apiPid) -Force -ErrorAction SilentlyContinue
            Write-Host "[serve-local] 已停止 API (PID $apiPid)"
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "[serve-local] 没有记录 API PID，跳过"
    }
    & (Join-Path $here "stop-pg.ps1") -Prefix $Prefix -DataDir $DataDir -Port $Port
    exit 0
}

# ---------------------------------------------------------------- 1. PostgreSQL
& (Join-Path $here "start-pg.ps1") -Prefix $Prefix -DataDir $DataDir -Port $Port -DbUser $DbUser
if ($LASTEXITCODE -ne 0) { Fail "PostgreSQL 启动失败" }

# ---------------------------------------------------------------- 2. 建库（幂等）
$r = Invoke-Psql -PsqlArgs @("-d", "postgres", "-tAc", "SELECT 1 FROM pg_database WHERE datname='$DbName'")
if ($r.Text.Trim() -ne "1") {
    Write-Host "[serve-local] 创建数据库 $DbName ..."
    $c = Invoke-Psql -PsqlArgs @("-d", "postgres", "-q", "-c", "CREATE DATABASE $DbName")
    if ($c.ExitCode -ne 0) { Fail "创建数据库 $DbName 失败" }
}

# ---------------------------------------------------------------- 3. 建表（幂等）
$r = Invoke-Psql -PsqlArgs @("-d", $DbName, "-tAc", "SELECT to_regclass('public.users')")
if ([string]::IsNullOrWhiteSpace($r.Text)) {
    Write-Host "[serve-local] 载入 db/schema.sql ..."
    $schema = Join-Path $repo "db\schema.sql"
    if (-not (Test-Path $schema)) { Fail "找不到 $schema" }
    $s = Invoke-Psql -PsqlArgs @("-d", $DbName, "-q", "-v", "ON_ERROR_STOP=1", "-f", $schema)
    if ($s.ExitCode -ne 0) { Fail "schema.sql 执行失败" }
    Write-Host "[serve-local] 建表完成"
} else {
    Write-Host "[serve-local] 表已存在，跳过建表"
}

# ---------------------------------------------------------------- 4. 选 Python（同 run-smoke.ps1 的逻辑）
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
if (-not $pyExe) { Fail "未找到满足依赖的 Python。可用 -Python <绝对路径> 指定。" }
Write-Host "[serve-local] 使用 Python: $pyExe"

# ---------------------------------------------------------------- 5. 种子
$env:DATABASE_URL        = "postgresql+asyncpg://yijian@127.0.0.1:$Port/$DbName"
$env:APP_ENV             = "local"
$env:JWT_SECRET          = "local-verify-secret-not-for-production"
$env:ADMIN_INIT_PHONE    = "13800000000"
$env:ADMIN_INIT_PASSWORD = "Admin@123456"
$env:SMS_PROVIDER        = "mock"

if (-not $SkipSeed) {
    Push-Location (Join-Path $repo "apps\api")
    try {
        & $pyExe -m app.cli seed-rbac
        if ($LASTEXITCODE -ne 0) { Fail "seed-rbac 失败" }
        & $pyExe -m app.cli seed-admin
        if ($LASTEXITCODE -ne 0) { Fail "seed-admin 失败" }
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------- 6. 已有实例先停掉
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($old) {
        Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- 7. 起 API
# 两种模式：
#   - 默认：脱离 stdio 后台常驻（UseShellExecute=$true），脚本退出后 API 仍在。
#     ⚠️ 注意：如果本脚本是从一个"用完即回收"的会话里跑的（CI step、某些沙箱/任务包装器），
#     整棵进程树会在会话结束时被清理，API 也会跟着死。那种场景请用 -Foreground。
#   - -Foreground：在当前会话前台跑 uvicorn（阻塞），日志直接打在终端。
#     适合"把 serve-local 挂成一个常驻后台任务"或想看实时访问日志时用。
if ($Foreground) {
    Write-Host "[serve-local] 前台启动 API :$ApiPort（Ctrl+C 停止）..." -ForegroundColor Green
    Write-Host "[serve-local] API      : http://127.0.0.1:$ApiPort"
    Write-Host "[serve-local] Swagger  : http://127.0.0.1:$ApiPort/docs"
    Write-Host "[serve-local] 超管账号 : 13800000000 / Admin@123456"
    Write-Host "[serve-local] 前端 .env.local: NEXT_PUBLIC_API_BASE=http://localhost:$ApiPort/api/v1" -ForegroundColor Yellow
    Write-Host ""
    Push-Location $here
    try {
        & $pyExe (Join-Path $here "serve_fake_redis.py") --pg-port $Port --api-port $ApiPort --log-file $apiLog
    } finally {
        Pop-Location
    }
    exit $LASTEXITCODE
}

Write-Host "[serve-local] 启动 API :$ApiPort ..."
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName         = $pyExe
$psi.Arguments        = "`"$(Join-Path $here 'serve_fake_redis.py')`" --pg-port $Port --api-port $ApiPort --log-file `"$apiLog`""
$psi.WorkingDirectory = $here
$psi.UseShellExecute  = $true
$psi.WindowStyle      = [System.Diagnostics.ProcessWindowStyle]::Hidden
$proc = [System.Diagnostics.Process]::Start($psi)
Set-Content -Path $pidFile -Value $proc.Id -Encoding ASCII

$ready = $false
foreach ($i in 1..40) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) { break }
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v1/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host "[serve-local] API 未就绪，日志尾部（$apiLog）："
    if (Test-Path $apiLog) { Get-Content $apiLog -Tail 40 }
    Fail "API 启动失败"
}

Write-Host ""
Write-Host "[serve-local] 后端已就绪并在后台运行" -ForegroundColor Green
Write-Host "  API      : http://127.0.0.1:$ApiPort"
Write-Host "  Swagger  : http://127.0.0.1:$ApiPort/docs"
Write-Host "  超管账号 : 13800000000 / Admin@123456"
Write-Host "  API PID  : $($proc.Id)（记录在 $pidFile）"
Write-Host "  日志     : $apiLog"
Write-Host ""
Write-Host "  前端联调请在 apps/admin/.env.local 写："
Write-Host "    NEXT_PUBLIC_API_BASE=http://localhost:$ApiPort/api/v1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  停止：powershell -ExecutionPolicy Bypass -File tools/local-verify/serve-local.ps1 -Stop"
