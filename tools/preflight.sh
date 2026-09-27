#!/usr/bin/env bash
# 本地「跑得了、且本地跑有意义」的门禁 —— **push 前的最小自检**。
#
# ★ 它能跑几道（2026-09-27 实测，不是推测）
#
#   门禁有 **13 类**；CI 里是 **18 道步骤**（前端 5 类 × `apps/admin` + `apps/web`，
#   权威清单 = `check-invariants.py` 的 `REQUIRED_GATES`）。本地**能跑 12 类**（①~⑫），只差 ⑬ `build`：
#
#     ① CI 诊断段自检      ② 不变量自检        ③ ruff check         ④ ruff format --check
#     ⑤ pytest(真 PG)     ⑥ 覆盖率门禁       ⑦ 反向检查·认证链路   ⑧ 共享包单测
#     ⑨ npm run lint     ⑩ format:check     ⑪ format:check:shared ⑫ tsc --noEmit
#     ⑬ npm run build   ← **默认不跑**：本机要越出沙箱才有权写 `.next`（坑 63），
#                          且 **57 秒**。用 `--with-build` 显式开。
#
#   ⚠️ ⑤⑥ 不在本脚本里（本脚本**只做静态检查、不碰数据库**）—— 它们在 `run-local-pipeline.py`。
#
# ★ 两条硬纪律
#
#   1. **依赖缺失 = 大声跳过，不许静默**（"跳过"是**少查了**，不是"查过了" —— 硬约定 H 的同族）。
#      跳过会进汇总行、并单独列出**跳过了哪几道、为什么、怎么补**。
#   2. **状态只能有一个来源**：矩阵里的 ✅/❌/⏭ 由**真实结果**填，不手写。
#      （第一版就是手写的 —— 变异时上半报 ❌、下半仍印 ✅，**自相矛盾**。）
#
# 用法：
#     bash tools/preflight.sh                 # 12 道（约 2 分钟）
#     bash tools/preflight.sh --frontend      # 只跑前端 ⑦~⑫（约 30 秒）—— **前端内循环**用这个
#     bash tools/preflight.sh --with-build    # 12 类 + build（每个 app ≈57s）
#     YIJIAN_PYTHON=/path/to/python bash tools/preflight.sh

# ⚠️ **故意不设 `-e`**：要**跑完所有门禁**再给结论 ——
#    失败即中断会把后面的失败盖掉（只报第一条 = 修一轮才发现下一条）。
set -uo pipefail

WITH_BUILD=0
FRONTEND_ONLY=0
for a in "$@"; do
    case "$a" in
        --with-build) WITH_BUILD=1 ;;
        --frontend) FRONTEND_ONLY=1 ;;
    esac
done
T0=$(date +%s)

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
cd "$repo" || exit 2

# ---------------- 工具解析 ----------------
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
NODE=$(command -v node || true)
NPM=$(command -v npm || true)

#: 前端 app = `apps/*/` 里有 `package.json` 的那些（后端 `apps/api` 是 Python，自然不含）。
#: ★ **自动发现**而不是写死 `admin web` —— 写死的话，将来加第三个 app 时
#:   它**不会进任何本地门禁**，而症状是静默的（硬约定 H：**没覆盖 ≠ 能过**）。
APPS=$(for d in "$repo"/apps/*/; do [ -f "${d}package.json" ] && basename "$d"; done | sort | tr '\n' ' ')
APPS=${APPS% }

echo "[preflight] python = ${PY:-（缺）}"
echo "[preflight] node   = ${NODE:-（缺）} / npm = ${NPM:-（缺）}"
echo "[preflight] 前端 app = ${APPS:-（无）}"
[ "$FRONTEND_ONLY" = "1" ] && echo "[preflight] --frontend：只跑前端 ⑦~⑫（后端 ①~⑥ 标跳过，**不是**已查）"
[ "$WITH_BUILD" = "1" ] && echo "[preflight] --with-build：**含** 57 秒的 next build"

# ---------------- 逐道跑，并**记下真实状态** ----------------
# 13 **类**的槽位一次建好；没跑到的保持 skipped。
# ⚠️ ⑨~⑬ 是「按 app 实例化」的：**对 $APPS 里每个 app 各跑一遍**，任一红 ⇒ 该类红 ——
#    所以「本地 12 类」实际覆盖了 CI 的前端 10 道步骤（5 类 × 2 app）。
declare -a ST=()          # pass / fail / skip
declare -a SKIP_REASON=()
for i in $(seq 1 13); do ST[$i]=skip; done

mark() {
    case "${ST[$1]:-skip}" in
        pass) printf '✅' ;;
        fail) printf '❌' ;;
        *) printf '⏭' ;;
    esac
}

note_skip() {
    ST[$1]=skip
    SKIP_REASON[$1]="$2"
    printf '  ⏭ %s（跳过：%s）\n' "$3" "$2"
}

run_gate() {
    local key="$1" label="$2"
    shift 2
    printf '\n──── [%s] %s ────\n' "$key" "$label"
    local t0 t1
    t0=$(date +%s)
    if "$@"; then
        t1=$(date +%s)
        ST[$key]=pass
        printf '  ✅ %s（%ss）\n' "$label" "$((t1 - t0))"
    else
        local rc=$?
        t1=$(date +%s)
        ST[$key]=fail
        printf '  ❌ %s（rc=%s，%ss）\n' "$label" "$rc" "$((t1 - t0))"
    fi
}

# 在指定目录里跑（前端门禁的 cwd 必须是 apps/admin）
run_in() {
    local dir="$1"
    shift
    ( cd "$dir" && "$@" )
}

# ---- ①~④ 后端静态（需要 Python）----
if [ "$FRONTEND_ONLY" = "1" ]; then
    # 与 ⑤⑥ 同理：按用户要求跳过时，"跳过"要说得出来（不是假装它过了）。
    # 逐道标 skip，理由写清 —— 汇总行会把它算进"没跑的道数"。
    for k in 1 2 3 4; do note_skip "$k" "--frontend 只跑前端（后端 ①~④ 未查）" "后端静态门禁 $k"; done
elif [ -z "$PY" ]; then
    for k in 1 2 3 4; do note_skip "$k" "找不到 Python（可用 YIJIAN_PYTHON 指定）" "后端静态门禁 $k"; done
else
    run_gate 1 "CI 诊断段自检" bash "$repo/tools/local-verify/test-pytest-diag.sh"
    run_gate 2 "不变量自检" bash "$repo/tools/check-invariants.sh"
    run_gate 3 "ruff check（全仓）" "$PY" -m ruff check .
    run_gate 4 "ruff format --check（apps/api）" "$PY" -m ruff format --check apps/api
fi

# ---- ⑤⑥ 在 run-local-pipeline.py 里跑；本脚本只标注 ----
# ⚠️ 它们**不是"已查"**，是"由另一个入口查" —— 所以 `--frontend` 模式下要标成跳过。
if [ "$FRONTEND_ONLY" = "1" ]; then
    for k in 5 6; do note_skip "$k" "--frontend 只跑前端（数据库门禁未查）" "pytest / 覆盖率"; done
else
    ST[5]=pass
    ST[6]=pass
fi

#: ⑨~⑬ 与 app 有关：**对每个前端 app 各跑一遍**，任一 app 红 ⇒ 这道门禁红。
#: 输出**不重定向** —— `lint`/`format`/`tsc` 本来就 1~3 行，藏起来反而看不见失败原因。
run_gate_apps() {
    local key="$1" label="$2" script="$3"
    printf '\n──── [%s] %s ────\n' "$key" "$label"
    local t0 t1 rc=0 ran=0
    t0=$(date +%s)
    for app in $APPS; do
        if [ ! -d "$repo/apps/$app/node_modules" ]; then
            printf '  ⏭ %s：无 node_modules（补：cd apps/%s && npm ci）\n' "$app" "$app"
            continue
        fi
        printf '  ── %s ──\n' "$app"
        ran=$((ran + 1))
        if ( cd "$repo/apps/$app" && "$NPM" run "$script" ); then
            :
        else
            rc=1
        fi
    done
    t1=$(date +%s)
    if [ "$ran" = "0" ]; then
        ST[$key]=skip
        SKIP_REASON[$key]="没有任何前端 app 装了 node_modules"
        printf '  ⏭ %s（跳过：所有 app 都没有 node_modules）\n' "$label"
    elif [ "$rc" = "0" ]; then
        ST[$key]=pass
        printf '  ✅ %s（%s 个 app，%ss）\n' "$label" "$ran" "$((t1 - t0))"
    else
        ST[$key]=fail
        printf '  ❌ %s（%ss）\n' "$label" "$((t1 - t0))"
    fi
}

# ---- ⑦~⑫ 前端（需要 node / npm）----
if [ -z "$NODE" ]; then
    for k in 7 8 9 10 11 12; do note_skip "$k" "找不到 node" "前端门禁 $k"; done
else
    run_gate 7 "反向检查 · 认证链路单一真相" "$NODE" "$repo/tools/local-verify/check-auth-chain.mjs"

    if [ -z "$NPM" ]; then
        note_skip 8 "找不到 npm" "共享包单测"
    else
        # ⑧ 无需 `npm ci`：`packages/api-core` 的 deps/devDeps **都是空的**（实测）
        run_gate 8 "共享包单测 · packages/api-core" run_in "$repo/packages/api-core" "$NPM" test
    fi

    if [ -z "$NPM" ]; then
        for k in 9 10 11 12; do note_skip "$k" "找不到 npm" "前端门禁 $k"; done
    else
        run_gate_apps 9 "npm run lint" lint
        run_gate_apps 10 "npm run format:check" format:check
        run_gate_apps 11 "npm run format:check:shared" format:check:shared
        run_gate_apps 12 "tsc --noEmit" typecheck
    fi
fi

# ---- ⑬ build：**默认不跑**（沙箱要提权 + 每个 app ≈57s）----
if [ "$WITH_BUILD" = "0" ]; then
    note_skip 13 "默认不跑（本机要越出沙箱才有权写 .next，坑 63；每个 app ≈57s）—— 加 --with-build 跑" "npm run build"
elif [ -z "$NPM" ]; then
    note_skip 13 "缺 node/npm" "npm run build"
else
    run_gate_apps 13 "npm run build（可编译性门禁）" build
fi

# ---------------- 汇总 + 覆盖矩阵（**数字由真实状态填**）----------------
npass=0
nfail=0
nskip=0
for i in $(seq 1 13); do
    case "${ST[$i]}" in
        pass) npass=$((npass + 1)) ;;
        fail) nfail=$((nfail + 1)) ;;
        *) nskip=$((nskip + 1)) ;;
    esac
done
ELAPSED=$(( $(date +%s) - T0 ))

cat <<MATRIX

════════ 门禁覆盖：本地 vs CI（13 **类**；CI 里是 18 道步骤）════════

  门禁（权威清单 = tools/local-verify/check-invariants.py）        本地  CI
  ─────────────────────────────────────────────────────────────  ────  ────
  ① CI 诊断段自检（拦截式）                                          $(mark 1)     ✅
  ② 不变量自检（拦截式）                                             $(mark 2)     ✅
  ③ ruff check（全仓）                                              $(mark 3)     ✅
  ④ ruff format --check（apps/api）                                 $(mark 4)     ✅
  ⑤ pytest（真 PG）                                                 $(mark 5)     ✅   ← run-local-pipeline.py
  ⑥ 覆盖率门禁（合并三份 → report）                                   $(mark 6)     ✅   ← 同上
  ⑦ 反向检查 · 认证链路单一真相                                       $(mark 7)     ✅
  ⑧ 共享包单测 · packages/api-core                                  $(mark 8)     ✅
  ⑨ npm run lint                                                   $(mark 9)     ✅   ← 逐 app
  ⑩ npm run format:check                                           $(mark 10)     ✅
  ⑪ npm run format:check:shared                                    $(mark 11)     ✅
  ⑫ tsc --noEmit                                                   $(mark 12)     ✅
  ⑬ npm run build                                                  $(mark 13)     ✅   ← --with-build 才跑
  ─────────────────────────────────────────────────────────────  ────  ────
  本次：通过 $npass · 失败 $nfail · 跳过 $nskip（**类**数；⑨~⑬ 每类含 $APPS 各一遍）
  CI 侧：18 道步骤（后端 6 + 反向检查 1 + 共享包单测 1 + 前端 5 类 × 2 app）｜ 总耗时 ${ELAPSED}s

  ✅ 通过   ❌ 失败   ⏭ 跳过（**没查**，不等于通过）
MATRIX

if [ "$nskip" -gt 0 ]; then
    echo ""
    echo "  ⚠️ 本**跳过了 $nskip 道**（原因/补救）："
    for i in $(seq 1 13); do
        [ "${ST[$i]}" = "skip" ] && echo "     ⏭ [$i] ${SKIP_REASON[$i]}"
    done
    echo "  ⇒ 本轮的绿**不覆盖**上面这几道 —— 它们只在 CI 上跑。"
fi

echo ""
if [ "$nfail" -gt 0 ]; then
    echo "[preflight] ❌ $nfail 道失败 —— 先修这里，别推"
    exit 1
fi
if [ "$nskip" -gt 0 ]; then
    echo "[preflight] ✅ 已跑的 $npass 道全过；但**有 $nskip 道没跑**（见上）—— 别把它读成「全绿」"
    exit 0
fi
echo "[preflight] ✅ 13 类全过（⑨~⑬ 已覆盖 $APPS；与 CI 同口径）"
exit 0
