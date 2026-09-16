# seed-demo-users.ps1
#
# Prepares demo data for the "B-end admin console v0.1" acceptance walkthrough.
#
# What it does:
#   1) Logs in as superadmin to obtain an access_token
#   2) Registers a viewer account through the SMS-code flow (default 13900000001)
#   3) Assigns the "viewer" role to that account as superadmin. This step also
#      writes an audit_logs row, which is exactly what the acceptance criterion
#      "audit log shows the role change" needs.
#
# Idempotent: if the account already exists it falls back to password login.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\local-verify\seed-demo-users.ps1
#
# NOTE: this script is deliberately pure ASCII (the nickname is written with
# JSON \uXXXX escapes) because Windows PowerShell 5.1 mis-reads non-ASCII
# characters in UTF-8 .ps1 files that lack a BOM.

param(
    [string]$Base = "http://localhost:8123/api/v1",
    [string]$AdminPhone = "13800000000",
    [string]$AdminPassword = "Admin@123456",
    [string]$ViewerPhone = "13900000001",
    [string]$ViewerPassword = "Viewer@123456"
)

$ErrorActionPreference = "Stop"

$lines = New-Object System.Collections.ArrayList
function Note([string]$t) { [void]$lines.Add($t) }

function Invoke-PostJson {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Json,
        [string]$Token = ""
    )
    $headers = @{}
    if ($Token) { $headers["Authorization"] = "Bearer $Token" }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)
    return Invoke-RestMethod -Method Post -Uri $Uri -ContentType "application/json" -Headers $headers -Body $bytes
}

function Fail([string]$msg) {
    Write-Output "FAIL: $msg"
    $lines | ForEach-Object { Write-Output $_ }
    exit 1
}

# ------------------------------------------------------------- 1) admin login
$loginBody = "{`"phone`":`"$AdminPhone`",`"password`":`"$AdminPassword`"}"
try {
    $r = Invoke-PostJson -Uri "$Base/auth/login/password" -Json $loginBody
} catch {
    Fail "superadmin login failed - is the API up on $Base ? $($_.Exception.Message)"
}
$saToken = $r.data.access_token
Note "superadmin_id=$($r.data.user.id)"
Note "superadmin_roles=$($r.data.user.roles -join ',')"
Note "superadmin_perms=$($r.data.user.permissions -join ',')"

# ---------------------------------------------------------------- 2) SMS code
$smsBody = "{`"phone`":`"$ViewerPhone`",`"scene`":`"register`"}"
$r = Invoke-PostJson -Uri "$Base/auth/sms/send" -Json $smsBody
$code = $r.data.dev_code
Note "dev_code=$code"
if (-not $code) {
    Fail "dev_code is empty - need SMS_PROVIDER=mock and APP_ENV != prod"
}

# --------------------------------------------------------- 3) register viewer
# nickname = "\u53ea\u8bfb\u89c2\u5bdf\u5458" == a read-only observer name
$regBody = "{`"phone`":`"$ViewerPhone`",`"code`":`"$code`",`"password`":`"$ViewerPassword`",`"nickname`":`"\u53ea\u8bfb\u89c2\u5bdf\u5458`"}"
$viewerId = $null
try {
    $r = Invoke-PostJson -Uri "$Base/auth/register" -Json $regBody
    $viewerId = $r.data.user.id
    Note "viewer_registered=1"
} catch {
    $relogin = "{`"phone`":`"$ViewerPhone`",`"password`":`"$ViewerPassword`"}"
    $r = Invoke-PostJson -Uri "$Base/auth/login/password" -Json $relogin
    $viewerId = $r.data.user.id
    Note "viewer_already_existed=1"
}
Note "viewer_id=$viewerId"

# ------------------------------------- 4) assign viewer role (writes audit row)
$assignBody = "{`"role_codes`":[`"viewer`"],`"scope_type`":`"global`"}"
$headers = @{ Authorization = "Bearer $saToken" }
$bytes = [System.Text.Encoding]::UTF8.GetBytes($assignBody)
$r = Invoke-RestMethod -Method Put -Uri "$Base/admin/users/$viewerId/roles" -Headers $headers -ContentType "application/json" -Body $bytes
Note "assigned_roles=$(($r.data.roles | ForEach-Object { $_.code }) -join ',')"
Note "granted_perms=$(($r.data.granted_permissions) -join ',')"

Note ""
Note "ADMIN_PHONE=$AdminPhone"
Note "ADMIN_PASSWORD=$AdminPassword"
Note "VIEWER_PHONE=$ViewerPhone"
Note "VIEWER_PASSWORD=$ViewerPassword"

$lines | ForEach-Object { Write-Output $_ }
$lines | Out-File -FilePath "$env:TEMP\yb-seed-demo-users.txt" -Encoding UTF8
