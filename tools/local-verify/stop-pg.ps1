# 停止本地 PostgreSQL。
[CmdletBinding()]
param(
    [string]$Prefix  = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16",
    [string]$DataDir = "$env:USERPROFILE\.workbuddy\binaries\pg\data16",
    [int]   $Port    = 55432
)

$ErrorActionPreference = "Continue"
$bin = Join-Path $Prefix "Library\bin"
if (-not (Test-Path (Join-Path $DataDir "PG_VERSION"))) {
    Write-Host "[local-verify] 数据目录不存在，无需停止"
    exit 0
}

# 幂等：本来就没在跑就直接返回
& (Join-Path $bin "pg_isready.exe") -h 127.0.0.1 -p $Port *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[local-verify] PostgreSQL 未在运行，无需停止"
    exit 0
}

& (Join-Path $bin "pg_ctl.exe") -D $DataDir stop -m fast *> $null
& (Join-Path $bin "pg_isready.exe") -h 127.0.0.1 -p $Port *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[local-verify] 警告：PostgreSQL 仍在运行" -ForegroundColor Yellow
    exit 1
}
Write-Host "[local-verify] PostgreSQL 已停止（数据保留在 $DataDir）"
exit 0
