# seed-demo-users.ps1
#
# Prepares demo data for the "B-end admin console" acceptance walkthrough.
#
# What it does:
#   1) Logs in as superadmin to obtain an access_token
#   2) Registers a viewer account through the SMS-code flow (default 13900000001)
#   3) Assigns the "viewer" role to that account as superadmin. This step also
#      writes an audit_logs row, which is exactly what the acceptance criterion
#      "audit log shows the role change" needs.
#   4) (Batch 5) Registers a researcher account (default 13900000002) and assigns
#      the "researcher" role with a *subject* data scope (default subject id 2007,
#      SW-SZ = municipal engineering). This is the account used to verify
#      acceptance criterion 6: "a researcher may only import questions that
#      belong to their own professional subject".
#
# Idempotent: if an account already exists it falls back to password login.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\local-verify\seed-demo-users.ps1
#
# NOTE: this script is deliberately pure ASCII (Chinese nicknames are written with
# JSON \uXXXX escapes) because Windows PowerShell 5.1 mis-reads non-ASCII
# characters in UTF-8 .ps1 files that lack a BOM.

param(
    [string]$Base = "http://localhost:8123/api/v1",
    [string]$AdminPhone = "13800000000",
    [string]$AdminPassword = "Admin@123456",
    [string]$ViewerPhone = "13900000001",
    [string]$ViewerPassword = "Viewer@123456",
    [string]$ResearcherPhone = "13900000002",
    [string]$ResearcherPassword = "Researcher@123456",
    [int]$ResearcherScopeSubjectId = 2007
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

function Invoke-PutJson {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Json,
        [Parameter(Mandatory = $true)][string]$Token
    )
    $headers = @{ Authorization = "Bearer $Token" }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)
    return Invoke-RestMethod -Method Put -Uri $Uri -ContentType "application/json" -Headers $headers -Body $bytes
}

function Fail([string]$msg) {
    Write-Output "FAIL: $msg"
    $lines | ForEach-Object { Write-Output $_ }
    exit 1
}

# Register a phone through the mock SMS flow, or fall back to password login when
# the account already exists. Returns the user id.
function Ensure-Account {
    param(
        [Parameter(Mandatory = $true)][string]$Phone,
        [Parameter(Mandatory = $true)][string]$Password,
        [Parameter(Mandatory = $true)][string]$NicknameEscaped
    )
    $smsBody = "{`"phone`":`"$Phone`",`"scene`":`"register`"}"
    $r = Invoke-PostJson -Uri "$Base/auth/sms/send" -Json $smsBody
    $code = $r.data.dev_code
    if (-not $code) {
        Fail "dev_code is empty - need SMS_PROVIDER=mock and APP_ENV != prod"
    }
    $regBody = "{`"phone`":`"$Phone`",`"code`":`"$code`",`"password`":`"$Password`",`"nickname`":`"$NicknameEscaped`"}"
    try {
        $r = Invoke-PostJson -Uri "$Base/auth/register" -Json $regBody
        return @{ id = $r.data.user.id; created = $true }
    } catch {
        $relogin = "{`"phone`":`"$Phone`",`"password`":`"$Password`"}"
        $r = Invoke-PostJson -Uri "$Base/auth/login/password" -Json $relogin
        return @{ id = $r.data.user.id; created = $false }
    }
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

# ------------------------------------------- 2) + 3) viewer: register + role
# nickname = "\u53ea\u8bfb\u89c2\u5bdf\u5458" == a read-only observer name
$v = Ensure-Account -Phone $ViewerPhone -Password $ViewerPassword -NicknameEscaped "\u53ea\u8bfb\u89c2\u5bdf\u5458"
$viewerId = $v.id
if ($v.created) { Note "viewer_registered=1" } else { Note "viewer_already_existed=1" }
Note "viewer_id=$viewerId"

$assignBody = "{`"role_codes`":[`"viewer`"],`"scope_type`":`"global`"}"
$r = Invoke-PutJson -Uri "$Base/admin/users/$viewerId/roles" -Json $assignBody -Token $saToken
Note "viewer_roles=$(($r.data.roles | ForEach-Object { $_.code }) -join ',')"
Note "viewer_granted_perms=$(($r.data.granted_permissions) -join ',')"

# ------------------------- 4) researcher: register + subject-scoped role (B5)
# nickname = "\u6559\u7814-\u5e02\u653f" == "researcher - municipal"
$rs = Ensure-Account -Phone $ResearcherPhone -Password $ResearcherPassword -NicknameEscaped "\u6559\u7814-\u5e02\u653f"
$researcherId = $rs.id
if ($rs.created) { Note "researcher_registered=1" } else { Note "researcher_already_existed=1" }
Note "researcher_id=$researcherId"

# scope_type="subject" + scope_id=2007 pins this account to SW-SZ only.
$resBody = "{`"role_codes`":[`"researcher`"],`"scope_type`":`"subject`",`"scope_id`":$ResearcherScopeSubjectId}"
$r = Invoke-PutJson -Uri "$Base/admin/users/$researcherId/roles" -Json $resBody -Token $saToken
Note "researcher_roles=$(($r.data.roles | ForEach-Object { $_.code }) -join ',')"
Note "researcher_granted_perms=$(($r.data.granted_permissions) -join ',')"
Note "researcher_scope=subject:$ResearcherScopeSubjectId"
if (($r.data.granted_permissions) -notcontains "question:import") {
    Fail "researcher did not get question:import - check the role/permission seed"
}

Note ""
Note "ADMIN_PHONE=$AdminPhone"
Note "ADMIN_PASSWORD=$AdminPassword"
Note "VIEWER_PHONE=$ViewerPhone"
Note "VIEWER_PASSWORD=$ViewerPassword"
Note "RESEARCHER_PHONE=$ResearcherPhone"
Note "RESEARCHER_PASSWORD=$ResearcherPassword"
Note "RESEARCHER_SCOPE_SUBJECT_ID=$ResearcherScopeSubjectId"

$lines | ForEach-Object { Write-Output $_ }
$lines | Out-File -FilePath "$env:TEMP\yb-seed-demo-users.txt" -Encoding UTF8
