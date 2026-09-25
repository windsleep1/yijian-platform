<#
    Unit test for the `COV-CLEAN` block inside `run-smoke.ps1`.

    ## Why this test exists (hard rule H: a gate script must itself be gated)

    The cleanup step DELETES FILES -- getting it wrong is worse than not having it:
      * too little  -> stale data turns the local number into a FALSE RED
                       (hit for real on 2026-09-25: a leftover .coverage reported
                        4567/218 while the authoritative value was 4567/210, off by 8 lines);
      * too much    -> it deletes `.coveragerc` itself, and the gate fails SILENTLY
                       (coverage still runs, the threshold is just gone -- and "pass"
                       looks perfectly normal).

    ## It tests the REAL code, not a copy

    The block is extracted IN PLACE from `run-smoke.ps1` between the markers
    `<<< COV-CLEAN:START >>>` / `<<< COV-CLEAN:END >>>`, then executed with `& $sb`.
    => there is no way for "the logic in the test" to drift from "the logic in the script"
       (that drift is exactly how pit 51 produced a fake green).

    ## Two scenarios (B is a MUTATION check)

      A normal  : whole-family cleanup works, and `.coveragerc` + an unrelated file SURVIVE
      B mutated : the glob AND the `Where-Object` exclusion are BOTH removed -- i.e. the
                  realistic "let me simplify this" mistake, ending in a naive
                  `Get-ChildItem .coverage*` -> MUST fail at the "is the config still
                  there" assertion
                  => proves that assertion is not decoration
                     (hard rule J: break the thing under measurement, the reading must change)

    ## The mutation lesson (learned the hard way, 2026-09-25)

    The cleanup has TWO layers against eating `.coveragerc`:
      (1) the glob is `.coverage.*` (with a dot) instead of `.coverage*`;
      (2) an explicit `Where-Object { $_.Name -ne ".coveragerc" }` filter.
    The first version of scenario B mutated ONLY layer (1) -> nothing dangerous happened,
    the assertion did not fire, and the test reported "mutation survived".
    That reading was misleading: it was not "the test is too weak", it was
    "**the mutation was not a valid mutation**" -- removing one of two redundant guards
    breaks nothing. A faithful mutation must remove BOTH.

    ## Safety

    Cleanup is destructive, so this harness only ever runs it inside a %TEMP% fixture
    and asserts `$repo` points at that fixture before every call.
    Otherwise scenario B would delete the REAL repo's `.coveragerc`
    (the first version of this script did exactly that -- fixed).

    ## Print convention

    Output is pure ASCII on purpose: a `.ps1` is read as UTF-8 only when it has a BOM,
    but its *console output* crosses host boundaries where the code page is unknowable.
    Chinese lives in these comments (read as source), not in strings (see pit list #9).
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # tools/local-verify -> repo root
$smoke    = Join-Path $PSScriptRoot "run-smoke.ps1"
$fixture  = Join-Path $env:TEMP ("yijian-cov-clean-test-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
$tempRoot = [System.IO.Path]::GetFullPath($env:TEMP)

# The extracted block calls this -- it must exist before invocation.
function Fail { param([string]$Message) throw ("FAIL: " + $Message) }

function New-Fixture {
    param([string]$Dir)
    Remove-Item -Path $Dir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Dir "htmlcov") -Force | Out-Null
    Set-Content -Path (Join-Path $Dir "htmlcov\index.html") -Value "<html>stale</html>" -Encoding UTF8
    # Coverage artifacts, including one name left over from a previous naming scheme --
    # that leftover is the whole reason for the cleanup change under test.
    foreach ($n in @(".coverage", ".coverage.api", ".coverage.cli", ".coverage.tests",
                     ".coverage.legacy-name", "coverage.xml")) {
        Set-Content -Path (Join-Path $Dir $n) -Value "dummy" -Encoding UTF8
    }
    # These two MUST survive:
    Set-Content -Path (Join-Path $Dir ".coveragerc") -Value "[report]`nfail_under = 94.40" -Encoding UTF8
    Set-Content -Path (Join-Path $Dir "keepme.txt")  -Value "not a coverage artifact" -Encoding UTF8
}

function Get-CleanBlock {
    param([string]$Path)
    $text = Get-Content -Path $Path -Encoding UTF8
    $s = ($text | Select-String -Pattern "COV-CLEAN:START" | Select-Object -First 1).LineNumber
    $e = ($text | Select-String -Pattern "COV-CLEAN:END"   | Select-Object -First 1).LineNumber
    if (-not $s -or -not $e) {
        # No markers means this test cannot run. It must FAIL, not skip: a clean
        # environment would still have the markers (hard rule M).
        throw ("markers COV-CLEAN:START / END not found in " + $Path + " -- extraction failed, refusing to pass silently")
    }
    return ($text[$s..($e - 2)] -join [Environment]::NewLine)
}

# Safety gate: the real repo root must never be touched by the extracted code.
function Assert-Sandboxed {
    param([string]$Dir)
    $full = [System.IO.Path]::GetFullPath($Dir)
    if (-not $full.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ("refusing to run: cleanup target " + $full + " is not under TEMP")
    }
    $a = $full.TrimEnd('\')
    $b = ([System.IO.Path]::GetFullPath($repoRoot)).TrimEnd('\')
    if ($a -eq $b) { throw "refusing to run: cleanup target is the repo root" }
}

$failures = New-Object System.Collections.ArrayList
function Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = "")
    if ($Ok) { Write-Host ("  [PASS] " + $Name) }
    else {
        Write-Host ("  [FAIL] " + $Name + "  " + $Detail) -ForegroundColor Red
        [void]$failures.Add($Name)
    }
}

Write-Host "[cov-clean-test] block extracted in place from run-smoke.ps1 (not a copy)"
$block = Get-CleanBlock -Path $smoke
Write-Host ("[cov-clean-test] extracted lines = " + ($block -split [Environment]::NewLine).Count)
Write-Host ("[cov-clean-test] fixture dir     = " + $fixture)
Write-Host ""

# ============================================================ Scenario A: normal
Write-Host "[A] whole-family cleanup (normal)"

New-Fixture -Dir $fixture
$repo       = $fixture      # the extracted block reads exactly this variable
$NoCoverage = $false
Assert-Sandboxed -Dir $repo
$sbA = [scriptblock]::Create($block)
& $sbA *>&1 | ForEach-Object { if (("" + $_) -notmatch "^\s*$") { Write-Host ("    | " + $_) } }

$leftCov = @(Get-ChildItem -Path $fixture -File -Force | Where-Object {
    $_.Name -eq ".coverage" -or $_.Name -like ".coverage.*" -or $_.Name -eq "coverage.xml" })
Check "coverage artifacts removed (incl. leftover from old naming scheme)" ($leftCov.Count -eq 0) `
      ("still present: " + (($leftCov | ForEach-Object { $_.Name }) -join ", "))
Check "htmlcov directory removed" (-not (Test-Path (Join-Path $fixture "htmlcov")))
Check "MUST-SURVIVE: .coveragerc (wildcard did not eat the gate config)" (Test-Path (Join-Path $fixture ".coveragerc"))
Check "MUST-SURVIVE: unrelated file keepme.txt" (Test-Path (Join-Path $fixture "keepme.txt"))
Write-Host ""

# ============================================================ Scenario B: mutation
Write-Host "[B] mutation: naive '.coverage*' glob + no exclusion (the 'simplify it' mistake)"

# Two-layer mutation -- see the header note. Removing only the glob leaves the Where-Object
# guard in place, which is not a dangerous mutation at all.
$mutated = $block -replace '\.coverage\.\*', '.coverage*'
$mutated = $mutated -replace `
    '(?s)-File -ErrorAction SilentlyContinue \|\s*\r?\n\s*Where-Object \{[^}]*\}', `
    '-File -ErrorAction SilentlyContinue'
Check "mutation actually changed the code (otherwise this scenario tests nothing)" ($mutated -ne $block)
Check "mutation removed BOTH guards (glob dot + Where-Object filter)" `
      (($mutated -notmatch '\.coverage\.\*') -and ($mutated -notmatch 'Where-Object \{[^}]*coveragerc'))

New-Fixture -Dir $fixture
$repo       = $fixture
$NoCoverage = $false
Assert-Sandboxed -Dir $repo
$threw  = $false
$errMsg = ""
$sbB = [scriptblock]::Create($mutated)
try { & $sbB *>$null } catch { $threw = $true; $errMsg = $_.Exception.Message }

Check "mutation raises an error (so the assertion really does block)" $threw `
      "no error raised -> that assertion is decoration"
Check "error message points at .coveragerc" ($errMsg -match "coveragerc") ("got: " + $errMsg)
Check "mutation really did delete .coveragerc (the danger is real)" `
      (-not (Test-Path (Join-Path $fixture ".coveragerc")))
Write-Host ""

Remove-Item -Path $fixture -Recurse -Force -ErrorAction SilentlyContinue

if ($failures.Count -gt 0) {
    Write-Host ("[cov-clean-test] FAILED (" + $failures.Count + "): " + ($failures -join " / ")) -ForegroundColor Red
    exit 1
}
Write-Host "[cov-clean-test] ALL PASSED (scenario A: 4 checks, scenario B: 5 checks)" -ForegroundColor Green
exit 0
