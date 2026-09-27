#!/usr/bin/env bash
# 本地「跑得了、且本地跑有意义」的门禁 —— **push 前的最小自检**。
#
# ★ 为什么需要它（用户 2026-09-27 追问「本地跑几道」）
#
#   CI 有 **13 道**门禁，而本地两条入口（`run-smoke.ps1` / `run-local-pipeline.py`）
#   原本**只跑 2 道**（`pytest` + 覆盖率）⇒ "本地绿"离"能 push"很远，
#   而**这个差距是隐形的**：将来"本地绿但 CI 红"时，人会以为是环境问题，
#   实际是**门禁覆盖不同**。本脚本做两件事：
#
#     ① 把本地跑得了的门禁**真跑掉**（fail-fast 到 push 之前）；
#     ② 把差的那几道**打印出来** —— 差距要可见，不能靠人记。
#
#   本地 **6 道** / CI **13 道**，差 **7 道**（全在前端）。逐道明细见 `docs/24` §10。
#
# ⚠️ 与 `tools/check-invariants.sh` 的分工：
#   `check-invariants.sh` 查的是**不变量**（门禁在不在、门槛有没有降…），
#   它本身是 13 道里的**一道**；本脚本是**把几道门禁打成一包跑**。
#   所以本脚本会**调用**它 —— 两者不是一回事，别合并。
#
# 用法：
#     bash tools/preflight.sh
#     YIJIAN_PYTHON=/path/to/python bash tools/preflight.sh    # 指定解释器

# ⚠️ **故意不设 `-e`**：要**跑完所有门禁**再给结论 ——
#    失败即中断会把后面的失败盖掉（只报第一条 = 修一轮才发现下一条）。
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
cd "$repo" || exit 2

# ---------------- 选解释器 ----------------
# 与 `run-local-pipeline.py` 同一优先级：受管 venv 优先（里面有 ruff / pytest 等）。
PY=${YIJIAN_PYTHON:-}
if [ -z "$PY" ]; then
    for cand in \
        "$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
        "$HOME/.workbuddy/binaries/python/envs/default/bin/python" \
        python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then PY=$cand; break; fi
        [ -x "$cand" ] && { PY=$cand; break; }
    done
fi
if [ -z "$PY" ]; then
    echo "✗ 找不到 Python（可用 YIJIAN_PYTHON 指定）" >&2
    exit 2
fi
echo "[preflight] 解释器 = $PY"

# ---------------- 逐道跑，并**记下真实结果** ----------------
#
# ★ 为什么结果要记下来、而不是在下面的表里手写 `✅`：
#   第一版就是这么写的 —— 结果**注入 lint 错误做变异**时，上面报 `❌`
#   而下面的表仍然印着 `✅`，**自相矛盾**。一张手写的状态表和一句动态结论打架时，
#   看的人只会更迷惑（"到底红没红？"）。**状态只能有一个来源。**
declare -a GATE_RC=()

run_gate() {
    local key="$1" label="$2"
    shift 2
    printf '\n──── %s ────\n' "$label"
    if "$@"; then
        GATE_RC[$key]=0
        printf '  ✅ %s\n' "$label"
    else
        local rc=$?
        GATE_RC[$key]=$rc
        printf '  ❌ %s（rc=%s）\n' "$label" "$rc"
    fi
}

# 标签里**不能有空格以外的引号歧义** —— 用 '…' 包住整串，参数逐段传。
run_gate 1 "CI 诊断段自检" bash "$repo/tools/local-verify/test-pytest-diag.sh"
run_gate 2 "不变量自检" bash "$repo/tools/check-invariants.sh"
run_gate 3 "ruff check（全仓）" "$PY" -m ruff check .
run_gate 4 "ruff format --check（apps/api）" "$PY" -m ruff format --check apps/api

fails=0
for i in 1 2 3 4; do
    [ "${GATE_RC[$i]:-1}" = "0" ] || fails=$((fails + 1))
done

mark() {
    if [ "${GATE_RC[$1]:-1}" = "0" ]; then printf '✅'; else printf '✗'; fi
}

# ---------------- 覆盖矩阵：差距必须可见 ----------------
# `@1@`..`@4@` 是**占位符**，下面用真实结果替换 —— 不手写 ✅（理由见上）。
matrix=$(cat <<'MATRIX'

════════════════ 门禁覆盖：本地 vs CI ════════════════

  门禁（13 道，权威清单 = tools/local-verify/check-invariants.py）
  ──────────────────────────────────────────────── 本地  CI
  ① CI 诊断段自检（拦截式）                              @1@   ✅
  ② 不变量自检（拦截式）                                 @2@   ✅
  ③ ruff check（全仓）                                  @3@   ✅
  ④ ruff format --check（apps/api）                     @4@   ✅
  ⑤ pytest（真 PG）                                     ✅   ✅   ← 由 run-local-pipeline.py 跑
  ⑥ 覆盖率门禁（合并三份 → report）                       ✅   ✅   ← 同上
  ──────────────────────────────────────────────── 本地  CI
  ⑦ 反向检查 · 认证链路单一真相                           ✗   ✅   node tools/local-verify/check-auth-chain.mjs
  ⑧ 共享包单测 · packages/api-core                      ✗   ✅   （需 npm ci）
  ⑨ npm run lint                                       ✗   ✅
  ⑩ npm run format:check                               ✗   ✅
  ⑪ npm run format:check:shared                        ✗   ✅
  ⑫ tsc --noEmit                                       ✗   ✅
  ⑬ npm run build                                      ✗   ✅   ⚠️ 本机红**推不出**代码结论（坑 63）

  ⚠️ ①~⑥ 的"本地"列是**本次真实结果**（`@1@`..`@4@` 由脚本替换，⑤⑥ 见下面的说明）；
     ⑦~⑬ 恒为 ✗ —— **它们本地根本不跑**，不是"跑失败了"。

  本地 6 道 / CI 13 道，差 7 道（**全在前端**）。

  ★ 本地绿**保证**：①~⑥ 通过。
  ★ 本地绿**不保证**：⑦~⑬ 通过 —— 那 7 道只在 CI 上跑。
  ⚠️ 所以"本地绿但 CI 红"的第一嫌疑是**前端**，不是环境问题。
     ⑦ 可以本地跑（只要 node 在）：node tools/local-verify/check-auth-chain.mjs
     ⑨⑩⑪⑫ 在 `apps/admin` 里**装了 node_modules** 时也能本地跑；
     ⚠️ ⑬ `build` 不行：本机失败**都在编译前**（坑 63）⇒ 它的红**不构成代码结论**。

  ⚠️ ⑤⑥ 不在本脚本里跑（本脚本**只做静态检查，不碰数据库**）——
     它们在 `run-local-pipeline.py` 里，共同构成"本地 6 道"。
═════════════════════════════════════════════════════
MATRIX
)

for i in 1 2 3 4; do
    matrix=${matrix//@$i@/$(mark "$i")}
done
printf '%s\n' "$matrix"

if [ "$fails" -eq 0 ]; then
    echo "[preflight] ✅ 本地 4 道全过（⑤⑥ 由 run-local-pipeline.py 跑 ⇒ 本地共 6 道；CI 另有 7 道前端）"
    exit 0
fi
echo "[preflight] ❌ $fails 道失败 —— 先修这里，别推"
exit 1
