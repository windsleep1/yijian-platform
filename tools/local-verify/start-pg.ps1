# 启动本地 PostgreSQL（免安装、免管理员）。
# 首次运行会自动 initdb。默认监听 127.0.0.1:55432（刻意避开 5432，防止和已有实例冲突）。
[CmdletBinding()]
param(
    [string]$Prefix  = "$env:USERPROFILE\.workbuddy\binaries\pg\pg16",
    [string]$DataDir = "$env:USERPROFILE\.workbuddy\binaries\pg\data16",
    [int]   $Port    = 55432,
    [string]$DbUser  = "yijian"
)

$ErrorActionPreference = "Stop"
$bin = Join-Path $Prefix "Library\bin"
if (-not (Test-Path (Join-Path $bin "pg_ctl.exe"))) {
    throw "未找到 PostgreSQL 二进制：$bin`n请先按 tools/local-verify/README.md 第 0.1 节安装。"
}

$log = Join-Path (Split-Path $DataDir -Parent) "pg16.log"

# 幂等：已经在跑就直接返回，避免 pg_ctl start 报 "another server might be running"
& (Join-Path $bin "pg_isready.exe") -h 127.0.0.1 -p $Port *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[local-verify] PostgreSQL 已在运行 -> 127.0.0.1:$Port"
    exit 0
}

if (-not (Test-Path (Join-Path $DataDir "PG_VERSION"))) {
    Write-Host "[local-verify] 初始化数据目录 $DataDir ..."
    & (Join-Path $bin "initdb.exe") -D $DataDir -U $DbUser `
        --encoding=UTF8 --locale=C -A trust | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "initdb 失败" }
}

Write-Host "[local-verify] 启动 PostgreSQL :$Port ..."
# 两个坑，都必须绕开：
#   1) pg_ctl 会 fork 出守护进程 postgres.exe，守护进程会继承父进程的 stdio 句柄。
#      一旦把 pg_ctl 的 stdout 接到调用方管道（`| Out-Null` / `| Out-String` 等），
#      调用方会永远等不到 EOF，表现为“卡死”。
#   2) Start-Process 在部分环境会因环境变量大小写重复（PATH / Path）抛
#      “已添加项。字典中的关键字:'PATH'所添加的关键字:'Path'”。
# 因此这里改用 ProcessStartInfo 且 UseShellExecute=$true：pg_ctl 完全脱离当前进程的 stdio，
# 也不走 Start-Process 那条会踩坑的环境变量重定向路径。服务端日志由 pg_ctl 的 -l 写入 $log。
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName        = Join-Path $bin "pg_ctl.exe"
$psi.Arguments       = "start -D `"$DataDir`" -l `"$log`" -o `"-p $Port -c listen_addresses=127.0.0.1`""
$psi.UseShellExecute = $true
$psi.WindowStyle     = [System.Diagnostics.ProcessWindowStyle]::Hidden
$ctl = [System.Diagnostics.Process]::Start($psi)
if (-not $ctl.WaitForExit(30000)) { throw "pg_ctl start 超过 30s 未返回" }
if ($ctl.ExitCode -ne 0) {
    Write-Host "[local-verify] pg_ctl 退出码 $($ctl.ExitCode)，日志尾部："
    if (Test-Path $log) { Get-Content $log -Tail 30 }
    throw "pg_ctl start 失败"
}

$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Milliseconds 500
    & (Join-Path $bin "pg_isready.exe") -h 127.0.0.1 -p $Port *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
}
if (-not $ok) {
    Write-Host "[local-verify] 启动失败，日志尾部："
    if (Test-Path $log) { Get-Content $log -Tail 30 }
    throw "PostgreSQL 未在预期时间内就绪"
}

Write-Host "[local-verify] PostgreSQL 已就绪 -> 127.0.0.1:$Port (user=$DbUser, 无密码/trust)"
Write-Host "[local-verify] 停止： .\stop-pg.ps1"
