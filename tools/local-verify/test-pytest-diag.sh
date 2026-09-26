#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Unit-verify the pytest diagnostics block inside .github/workflows/ci.yml.
#
# WHY this harness exists: the first version of that block emitted **zero**
# annotations on a real CI run. Its three greps were all written for the HANG
# shape, while that run was a plain failure; and the pytest command line used
# `-q -rs`, which REPLACES the default `-rfE` — so the `FAILED ...` summary
# line never existed either. Nothing errored. The diagnosis simply said
# nothing, and the one thing that could have told us (`tail`) was written to
# stdout, which is 403 to anonymous readers. Same family as pit 51 / hard
# rule H: a check you cannot read is not a check.
#
# So the block gets a test. The block is extracted IN PLACE from ci.yml via
# the <<< PYTEST-DIAG:START/END >>> markers (same trick as test-cov-clean.ps1)
# so there is no second copy to drift.
#
# Usage: bash tools/local-verify/test-pytest-diag.sh
# Exit : 0 = all checks passed, 1 = at least one failed
# ---------------------------------------------------------------------------
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 2
CI=.github/workflows/ci.yml
TMP=$(mktemp -d)
# ⚠️ **故意不清理**这个临时目录（只放几个 KB 夹具）。
#    理由有两条，都是实测出来的：
#      ① 本机沙箱的删除预算是**按轮累计**的（坑 62）—— 一个 `trap ... rm -rf` 会让这个
#         harness 跑几次之后**整个进程被 SIGTERM 掉、且一个字都不输出**
#         （表现和"脚本坏了"一模一样，非常误导）。
#      ② CI 的 runner 是一次性的，留几 KB 无需在意。
#    ⇒ 结论：**验证脚本不应该依赖"能删文件"这件事**。

fails=0
check() { # check <description> <0|1>
  if [ "$2" = "1" ]; then
    echo "  [ok]   $1"
  else
    echo "  [FAIL] $1"
    fails=$((fails + 1))
  fi
}

has() { printf '%s' "$1" | grep -qF -- "$2"; }        # exit status only
has_flag() { has "$1" "$2" && echo 1 || echo 0; }    # prints 1/0
# ⚠️ 别写成 `$(has ... && echo 0 || echo 1)` —— `has` 的退出码是它最后一条命令
#    （`echo`，永远 0）的退出码，于是 `&&` 总成立、替换结果是**两行**，判据永远不成立。
#    这条是我第一版 harness 里的真 bug，被它自己抓出来了。

# ---------------------------------------------------------------------------
# 0. Extract the block (and guard that extraction really worked)
# ---------------------------------------------------------------------------
# ⚠️ 用**带尖括号的完整标记**匹配，并且**先断言标记只出现一次**。
#    2026-09-26 实测过一次事故：ci.yml 里另一处**注释提到了这对标记**，于是 awk 从那条注释开始截，
#    抓到 235 行垃圾（还因为用 `cut -c` 按**字节**切了中文 ⇒ 乱码），最终进程被 SIGTERM、
#    **一个字都不输出** —— 表现和"脚本坏了"一模一样。两个教训：
#      ① 标记匹配要够具体（完整形态）+ 断言唯一；
#      ② 去缩进要用**空格**匹配（`sed 's/^ \{12\}//'`），**别用 `cut -c`**（按字节会切坏中文）。
START_MARK='<<< PYTEST-DIAG:START >>>'
END_MARK='<<< PYTEST-DIAG:END >>>'
echo "[0] extraction"
check "the START marker appears exactly once in ci.yml" \
  "$([ "$(grep -cF "$START_MARK" "$CI")" = "1" ] && echo 1 || echo 0)"
BLOCK=$(awk -v s="$START_MARK" -v e="$END_MARK" \
  'index($0,s){f=1;next} index($0,e){f=0} f' "$CI" | sed 's/^ \{12\}//')
check "block extracted from ci.yml (non-empty)" "$([ -n "$BLOCK" ] && echo 1 || echo 0)"
check "extraction starts at the marker block (its first line is a comment)" \
  "$([ "$(printf '%s' "$BLOCK" | head -1 | cut -c1)" = "#" ] && echo 1 || echo 0)"
check "extraction did not run away (block is small)" \
  "$([ "$(printf '%s' "$BLOCK" | wc -l)" -lt 80 ] && echo 1 || echo 0)"
check "block is self-contained (sets up the summary file itself)" \
  "$(has_flag "$BLOCK" 'sumfile=')"
check "block contains real logic, not just comments" "$(has_flag "$BLOCK" 'log_bytes=')"
check "block still emits the raw tail (the fallback that was missing)" \
  "$(has_flag "$BLOCK" 'tail -25')"

# ---------------------------------------------------------------------------
# 1. Fixtures: three log shapes, all taken from real local runs
# ---------------------------------------------------------------------------
cat >"$TMP/f1_plain_failure.log" <<'EOF'
F.                                                                       [100%]
================================== FAILURES ===================================
___________________________ test_alpha ____________________________

    def test_alpha() -> None:
>       assert 1 == 2
E       AssertionError: ALPHA_FAILURE

tests/test_alpha.py:13: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_alpha.py::test_alpha - AssertionError: ALPHA_FAILURE
1 failed, 1 passed in 12.34s
EOF

cat >"$TMP/f2_hang.log" <<'EOF'
F+++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++
~~~~~~~~~~~~~~~~~~~~~~~~~ Stack of MainThread (34800) ~~~~~~~~~~~~~~~~~~~~~~~~~
  File "<frozen runpy>", line 88, in _run_code
  File "/home/runner/work/yijian-platform/yijian-platform/apps/api/tests/test_beta.py", line 18, in test_beta_hang
    time.sleep(30)
+++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++
EOF

: >"$TMP/f3_empty.log"

# F4 reproduces the OLD command line's shape: a FAILURES section but NO
# `FAILED ...` summary line (that is exactly what `-q -rs` produced), plus one
# marker line that is reachable ONLY through the raw-tail fallback.
cat >"$TMP/f4_old_rs_no_summary.log" <<'EOF'
F.                                                                       [100%]
================================== FAILURES ===================================
___________________________ test_gamma ____________________________

tests/test_gamma.py:13: AssertionError
PROBE_OLD_STYLE_TAIL_MARKER
1 failed, 1 passed in 11.11s
EOF

echo "[0b] fixture preconditions"
check "F1 has a FAILED summary line (the shape we now guarantee)" \
  "$(grep -qE '^FAILED ' "$TMP/f1_plain_failure.log" && echo 1 || echo 0)"
check "F4 has NO FAILED summary line (reproduces the old -rs shape)" \
  "$(grep -qE '^FAILED ' "$TMP/f4_old_rs_no_summary.log" && echo 0 || echo 1)"
check "F3 is really 0 bytes" \
  "$([ "$(wc -c <"$TMP/f3_empty.log" | tr -d ' ')" = "0" ] && echo 1 || echo 0)"

# ---------------------------------------------------------------------------
# 2. Drive the extracted block over each fixture
# ---------------------------------------------------------------------------
run_block() { # run_block <block> <log> ; prints stdout+stderr
  local blk="$1" log="$2"
  : >"$TMP/summary.md"
  LOG="$log" rc=1 GITHUB_STEP_SUMMARY="$TMP/summary.md" bash -c "$blk" 2>&1
}

echo "[1] plain failure (-rfEXs shape)"
OUT1=$(run_block "$BLOCK" "$TMP/f1_plain_failure.log")
check "header says verdict=plain" "$(has_flag "$OUT1" 'verdict=plain')"
check "reports log_bytes (non-zero)" "$(has_flag "$OUT1" 'log_bytes=')"
check "names the failing test" "$(has_flag "$OUT1" 'test_alpha')"
check "second channel: step summary got content" "$(has_flag "$(cat "$TMP/summary.md")" 'verdict=plain')"

echo "[2] hang (pytest-timeout thread dump)"
OUT2=$(run_block "$BLOCK" "$TMP/f2_hang.log")
check "header says verdict=hang" "$(has_flag "$OUT2" 'verdict=hang')"
check "locates the hung test" "$(has_flag "$OUT2" 'test_beta_hang')"

echo "[3] empty log (self-describing, not silent)"
OUT3=$(run_block "$BLOCK" "$TMP/f3_empty.log")
check "header self-reports an empty log: log_bytes=0" "$(has_flag "$OUT3" 'log_bytes=0')"

echo "[4] old -rs shape: no summary line, but the raw tail must still be readable"
OUT4=$(run_block "$BLOCK" "$TMP/f4_old_rs_no_summary.log")
TAIL_REACHABLE=0
has "$OUT4" 'PROBE_OLD_STYLE_TAIL_MARKER' && TAIL_REACHABLE=1
check "raw tail reached an annotation (this is the fix)" "$TAIL_REACHABLE"

# ---------------------------------------------------------------------------
# 3. Mutation: prove check [4] is not decoration (hard rule J).
#    "Someone changes the tail depth to 0" -> the fallback dies -> [4] must go red.
# ---------------------------------------------------------------------------
echo "[5] mutation: tail -25 -> tail -0 (breaks the fallback)"
MUT=$(printf '%s' "$BLOCK" | sed 's/tail -25/tail -0/')
check "mutation actually changed the block" "$([ "$MUT" != "$BLOCK" ] && echo 1 || echo 0)"
OUT5=$(run_block "$MUT" "$TMP/f4_old_rs_no_summary.log")
MUT_TAIL_REACHABLE=0
has "$OUT5" 'PROBE_OLD_STYLE_TAIL_MARKER' && MUT_TAIL_REACHABLE=1
check "mutated block NO LONGER reaches the tail marker (so [4] can really fail)" \
  "$([ "$MUT_TAIL_REACHABLE" = "0" ] && echo 1 || echo 0)"
check "and the header is still emitted (header does not depend on the tail)" \
  "$(has_flag "$OUT5" 'verdict=plain')"

# ---------------------------------------------------------------------------
# 4. ★★ 最重要的一组：把**整段步骤脚本**按 **GitHub 真正的方式**跑一遍。
#
#    GitHub 的 `run:` 默认是 `bash -e {0}` —— **`-e` 本来就开着**，它不是靠 `set -e` 才打开的。
#    ⇒ 那条"pytest 会失败"的管道一旦非零，`-e` 立刻结束整个脚本，**下面的诊断根本没机会跑**。
#    2026-09-26 实测（同一段脚本、只换 shell 选项）：`bash -eo pipefail` → **输出为空**；
#    `bash` → 诊断正常。这正是"annotation 一条都没发出来"的真因，骗过了两轮。
#    ⚠️ 上面 [1]~[5] 用 `bash -c` 跑的是**诊断段**、且**没有 -e** —— 所以它们**永远抓不到这个坑**。
#       本组存在的唯一理由就是把那个"两个口径"补上。
# ---------------------------------------------------------------------------
echo "[6] the real step script, run the way GitHub runs it (bash -e)"
STEP=$(awk '/^      - name: pytest$/{s=1;next} s&&/^      - name:/{exit} s&&/^        run: \|$/{r=1;next} r{print}' "$CI" \
  | sed 's/^ \{10\}//')
check "pytest step script extracted (non-empty)" "$([ -n "$STEP" ] && echo 1 || echo 0)"
check "script explicitly disables -e (a 'set +e' line BEFORE the pipeline)" \
  "$(printf '%s\n' "$STEP" | awk '/^set \+e$/{e=NR} /^coverage run/{c=NR} END{print (e && c && e < c) ? 1 : 0}')"

mkdir -p "$TMP/ws"
FAILSTEP=$(printf '%s\n' "$STEP" | sed 's/^coverage run/false/')
check "mutation applied (coverage run -> false, i.e. a failing pipeline)" \
  "$(has_flag "$FAILSTEP" 'false --data-file')"
printf '%s\n' "$FAILSTEP" >"$TMP/step_fail.sh"
GITHUB_WORKSPACE="$TMP/ws" GITHUB_STEP_SUMMARY="$TMP/s6.md" \
  bash -eo pipefail "$TMP/step_fail.sh" >"$TMP/s6.out" 2>&1
check "with -e ON (GitHub's default) the diagnostics DO run" \
  "$(has_flag "$(cat "$TMP/s6.out")" 'verdict=')"
# ★ 2026-09-27 加：**测试计数**也要能读到（"CI 与本地跑的是不是同一批用例"要靠它）。
check "…and the pytest summary reaches an annotation (always, not only on failure)" \
  "$(has_flag "$(cat "$TMP/s6.out")" '::notice title=pytest 摘要::')"
check "…and that emit sits BEFORE the failure branch (so it also fires on success)" \
  "$(printf '%s\n' "$STEP" | awk '/::notice title=pytest 摘要::/{e=NR} /^if \[/{c=NR} END{print (e && c && e < c) ? 1 : 0}')"

# ★ 证伪：把 `set +e` 拿掉，同一个场景必须**变哑**。
#    （不能构造出"它应该报相反结果"的场景，这条检查就不是检查 —— 硬约定 J）
printf '%s\n' "$FAILSTEP" | grep -v '^set +e$' >"$TMP/step_noe.sh"
check "falsification: the 'set +e' line is really gone in the control script" \
  "$([ "$(grep -c '^set +e$' "$TMP/step_noe.sh")" = "0" ] && echo 1 || echo 0)"
GITHUB_WORKSPACE="$TMP/ws" GITHUB_STEP_SUMMARY="$TMP/s6b.md" \
  bash -eo pipefail "$TMP/step_noe.sh" >"$TMP/s6b.out" 2>&1
# ⚠️ 这里**不能**写成 `$(has_flag … && echo 0 || echo 1)` —— `has_flag` 的退出码恒为 0
#    （它最后一条命令是 `echo`），于是 `&&` 总成立、替换结果是**两行**，判据永远不成立。
#    我在这份文件里**已经犯过两次**，所以下面 [0c] 组把它变成了自检。
NO_E_SPOKE=0
has "$(cat "$TMP/s6b.out")" 'verdict=' && NO_E_SPOKE=1
check "falsification: WITHOUT set +e it goes silent (so the check above can really fail)" \
  "$([ "$NO_E_SPOKE" = "0" ] && echo 1 || echo 0)"

echo "[0c] harness self-guard (a judgement must return ONE value)"
BADPAT='\$\(has(_flag)? [^)]*&& echo'
# 只扫**代码行**：注释里会出现这个模式的名字（就是来解释它的），扫进去会变成"检测器抓自己的说明"。
BAD_HITS=$(grep -nE "$BADPAT" "$0" | grep -vE '^[0-9]+:[[:space:]]*#' || true)
check "no has-flag-then-echo anti-pattern in code lines" \
  "$([ -z "$BAD_HITS" ] && echo 1 || echo 0)"
if [ -n "$BAD_HITS" ]; then
  echo "        offender(s): $BAD_HITS"
fi
# ★ 这条守卫**能不能响**不需要再造对照：它 2026-09-26 就**真的响过一次**
#   （当时 [6] 组里就是这么写的，被判 FAIL，我才发现判据永远拿不到 0/1）。
#   —— 这正是硬约定 J 要的"它应该报相反结果的场景"已经发生过。

# ---------------------------------------------------------------------------
# [7] 覆盖率数字必须走「匿名读得到」的通道
#
#   背景：覆盖率门禁自 09-24 起**一次都没跑到**（前一步被 12 分钟整步超时杀掉）；
#   2026-09-26 修好后它跑起来了（2 秒 success）—— 但数字写在 **stdout**，
#   而 job 日志正文匿名 **403**，于是「CI 的覆盖率到底是多少」依旧读不到。
#   与 pytest 诊断段**同一个坑**（坑 66）：**写在 stdout 等于没写**。
#
#   本组把提取出来的那一段**原样跑一遍**（桩 `coverage` 喂夹具），断言：
#     ① 数字真的进了 annotation；② `%` 已转义；③ 退出码**原样传出去**（门禁还拦得住）；
#     ④ 删掉 emit 那行就**必须**变红（否则这条检查是装饰）。
#   ⚠️ 用 `bash -eo pipefail`（= GitHub `run:` 的真实形态），不是无 `-e` 的 bash。
# ---------------------------------------------------------------------------
COV_S='<<< COV-ECHO:START >>>'
COV_E='<<< COV-ECHO:END >>>'
echo "[7] coverage echo (the numbers must reach a readable channel)"
check "the COV-ECHO START marker appears exactly once in ci.yml" \
  "$([ "$(grep -cF "$COV_S" "$CI")" = "1" ] && echo 1 || echo 0)"
CB=$(awk -v s="$COV_S" -v e="$COV_E" 'index($0,s){f=1;next} index($0,e){f=0} f' "$CI" \
  | sed 's/^ \{10\}//')
check "block extracted (non-empty)" "$([ -n "$CB" ] && echo 1 || echo 0)"
check "block is small (extraction did not run away)" \
  "$([ "$(printf '%s' "$CB" | wc -l)" -lt 45 ] && echo 1 || echo 0)"
# ★ 自包含判据：它必须**自己**算出 FPRINT / REPORT / cov_rc。
#   （2026-09-26 有过一次教训：标记没包住变量定义 ⇒ 抽出去拿到 `$null`，
#     而脚本本身跑起来没问题 —— **这个错只会在"想测它"的时候暴露**。）
check "block is self-contained (defines FPRINT / REPORT / cov_rc itself)" \
  "$([ "$(printf '%s' "$CB" | grep -cE '^(FPRINT|REPORT|cov_rc)=')" = "3" ] && echo 1 || echo 0)"

mkdir -p "$TMP/covws"
printf 'not-a-real-coverage-file\n' >"$TMP/covws/.coverage"
cat >"$TMP/covws/report_ok.txt" <<'COVEOF'
Name                                              Stmts   Miss  Cover
---------------------------------------------------------------------
apps\api\app\services\stats_service.py              133      0   100%
apps\api\app\services\user_service.py                90      8    91%
---------------------------------------------------------------------
TOTAL                                              4672    214    95%
COVEOF
check "fixture really ends with a TOTAL line (the thing we grep for)" \
  "$(grep -qE '^TOTAL' "$TMP/covws/report_ok.txt" && echo 1 || echo 0)"

# 桩：把夹具当 `coverage report` 的输出、按指定退出码退出。
# 用**函数 + export -f** 而不是往 PATH 里放脚本 —— 本机（Windows/Git Bash）
# 执行一个无扩展名的脚本文件不可靠，会导致"本地假红、CI 却绿"（又一种两个口径）。
coverage() { cat "$COV_FIXTURE"; return "$COV_EXIT"; }
export -f coverage

run_cov() { # run_cov <block> <fixture> <exit> <summary-file>
  : >"$4"
  ( cd "$TMP/covws" || exit 2
    COV_FIXTURE="$2" COV_EXIT="$3" GITHUB_STEP_SUMMARY="$4" \
      bash -eo pipefail -c "$1" 2>&1 )
}

echo "[7a] rc=0（门禁通过）"
OUT7=$(run_cov "$CB" "$TMP/covws/report_ok.txt" 0 "$TMP/s7.md")
RC7=$?
check "rc=0 is passed through (the gate stays green when it should)" \
  "$([ "$RC7" = "0" ] && echo 1 || echo 0)"
check "TOTAL reaches an annotation (★ this is the fix)" \
  "$(has_flag "$OUT7" '::notice title=覆盖率::')"
check "…and it carries the real numbers" "$(has_flag "$OUT7" '4672')"
check "…and the '%' is escaped for the workflow-command payload" \
  "$(has_flag "$OUT7" '95%25')"
check "the fingerprint reaches an annotation too" \
  "$(has_flag "$OUT7" '::notice title=覆盖率指纹::')"
check "second channel: the step summary got the numbers" \
  "$(has_flag "$(cat "$TMP/s7.md")" '4672')"
# ★ 2026-09-27 加：**逐文件**。没有它，`TOTAL` 只能告诉你"差几行"，不能告诉你"差在哪"
#   （实测：CI 216 vs 本地 214，光有 TOTAL 定位不了那 2 行）。
check "per-file rows reach an annotation (TOTAL alone cannot localize)" \
  "$(has_flag "$OUT7" '::notice title=覆盖率逐文件::')"
check "…and they carry a real file row" "$(has_flag "$OUT7" 'user_service.py')"
check "…and rows are joined with %0A (one notice, not N)" "$(has_flag "$OUT7" '%0A')"
PF_HEADER_LEAKED=0
has "$OUT7" '::notice title=覆盖率逐文件::Name' && PF_HEADER_LEAKED=1
check "…and the 'Name' header row is excluded" \
  "$([ "$PF_HEADER_LEAKED" = "0" ] && echo 1 || echo 0)"

echo "[7b] rc=1（门禁拦下来）—— 拦截式必须还在"
OUT7B=$(run_cov "$CB" "$TMP/covws/report_ok.txt" 1 "$TMP/s7b.md")
RC7B=$?
check "a non-zero coverage rc is passed through (the gate still BLOCKS)" \
  "$([ "$RC7B" = "1" ] && echo 1 || echo 0)"
check "…and a failing gate still SAYS so (not silent)" \
  "$(has_flag "$OUT7B" '::notice title=覆盖率::rc=1')"

echo "[7c] mutation: 删掉 TOTAL 那条 notice —— [7a] 的检查必须能红"
CMUT=$(printf '%s\n' "$CB" | grep -v 'title=覆盖率::' || true)
check "mutation actually removed the TOTAL notice line" \
  "$([ "$(printf '%s\n' "$CMUT" | grep -c 'title=覆盖率::')" = "0" ] && echo 1 || echo 0)"
OUT7C=$(run_cov "$CMUT" "$TMP/covws/report_ok.txt" 0 "$TMP/s7c.md")
# ⚠️ 这里**不能**图省事写成 `$(has_flag … && echo 0 || echo 1)` —— 见 [0c]。
#    我在写这一行的当下又犯了一次（第三次），是 [0c] 当场把它拦下来的。
NOTICE_GONE=0
has "$OUT7C" '::notice title=覆盖率::' || NOTICE_GONE=1
check "mutated block NO LONGER emits the TOTAL notice (so [7a] can really fail)" "$NOTICE_GONE"
check "…but it still emits the fingerprint notice (proves the block RAN, i.e. not 假存活)" \
  "$(has_flag "$OUT7C" '::notice title=覆盖率指纹::')"

echo "[7d] mutation: 删掉逐文件 notice —— [7a] 的逐文件检查必须能红"
CMUT2=$(printf '%s\n' "$CB" | grep -v 'title=覆盖率逐文件::' || true)
MUT2_DROPPED=0
has "$CMUT2" 'title=覆盖率逐文件::' || MUT2_DROPPED=1
check "mutation actually removed the per-file notice line" "$MUT2_DROPPED"
OUT7D=$(run_cov "$CMUT2" "$TMP/covws/report_ok.txt" 0 "$TMP/s7d.md")
MUT2_STILL=0
has "$OUT7D" '::notice title=覆盖率逐文件::' && MUT2_STILL=1
check "mutated block NO LONGER emits the per-file notice (so [7a] can really fail)" \
  "$([ "$MUT2_STILL" = "0" ] && echo 1 || echo 0)"
check "…but the TOTAL notice is still there (proves the block RAN)" \
  "$(has_flag "$OUT7D" '::notice title=覆盖率::')"

echo
if [ "$fails" -eq 0 ]; then
  echo "[pytest-diag-test] ALL PASSED"
  exit 0
fi
echo "[pytest-diag-test] $fails CHECK(S) FAILED"
exit 1
