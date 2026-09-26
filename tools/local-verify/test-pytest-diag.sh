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

echo
if [ "$fails" -eq 0 ]; then
  echo "[pytest-diag-test] ALL PASSED"
  exit 0
fi
echo "[pytest-diag-test] $fails CHECK(S) FAILED"
exit 1
