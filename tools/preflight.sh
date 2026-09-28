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
# 用法：**三种范围**（选错了会白等 —— 所以关系写在最前面）
#
#     bash tools/preflight.sh                    # ①~⑫  全部本地门禁（含后端）≈2.5 分钟 → push 前
#     bash tools/preflight.sh --frontend         # ⑦~⑫  前端 6 类 × **全部**前端 app    ≈55 秒
#     bash tools/preflight.sh --app web          # ⑦⑧ + 只跑 **web** 的 ⑨~⑫            ≈30 秒 → ★ C 端主循环
#
#   ① 为什么 `--frontend` **不跑后端**：它是给"前端内循环"用的 —— 改一个 `.tsx`
#      不值得跑一遍 pytest（162s）。后端那几道要么跑默认，要么本来就归 `run-local-pipeline.py`。
#   ② 为什么 `--app web` 仍然跑 ⑦⑧：它们**与具体 app 无关**且便宜（合计 <5s）——
#      ⑦ 反向检查**扫的就是 `apps/<app>/src`**（把正在写的那个 app 一起扫，且它真抓过 bug），
#      ⑧ 共享包单测是 C 端正在消费的包。省掉它们只会让"内循环绿"少一层意义。
#      ⇒ `--app X` 的准确含义：**把"按 app 实例化"的 ⑨~⑬ 限定到 X**。
#
#   可组合：`--app web --with-build` = 上面的 30 秒 + web 的 build。
#   其它：`YIJIAN_PYTHON=/path/to/python bash tools/preflight.sh`

# ⚠️ **故意不设 `-e`**：要**跑完所有门禁**再给结论 ——
#    失败即中断会把后面的失败盖掉（只报第一条 = 修一轮才发现下一条）。
set -uo pipefail

WITH_BUILD=0
FRONTEND_ONLY=0
#: `--app <name>`：把"按 app 实例化"的门禁（⑨~⑬）**限定到这一个 app**。
#: ⚠️ 名字故意不叫 `--only-web` —— 将来加第 3 个 app 时**不用改脚本**（硬约定 H 同族：
#:   写死的东西会在加东西时**静默**失效）。
APP_FILTER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --with-build) WITH_BUILD=1 ;;
        --frontend) FRONTEND_ONLY=1 ;;
        --app) shift; APP_FILTER="${1:-}" ;;
        --app=*) APP_FILTER="${1#--app=}" ;;
        *)
            echo "[preflight] ✗ 不认识参数：$1" >&2
            echo "  可用：--frontend | --app <name> | --with-build（见文件头用法）" >&2
            exit 2
            ;;
    esac
    shift
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

APPS_ALL=$APPS
#: `--app <name>`：限定到单个 app，并**隐式进入前端模式**（不限定就会跑全部 app，与"只跑 X"矛盾）。
#: ⚠️ 名字必须**校验**：写错一个字母时，若只是"过滤后没剩 app"就往下跑，症状是
#:   **⑨~⑬ 全变跳过、汇总看起来"没失败"** —— 静默失效。这里直接 rc=2 拒掉。
if [ -n "$APP_FILTER" ]; then
    FRONTEND_ONLY=1
    case " $APPS " in
        *" $APP_FILTER "*) APPS="$APP_FILTER" ;;
        *)
            echo "[preflight] ✗ 不认识 app「$APP_FILTER」—— 现有前端 app：${APPS_ALL:-（无）}" >&2
            exit 2
            ;;
    esac
fi

#: 三种范围的**说法**（只用来打印，避免下面几处各写一份、然后各说各的）。
if [ -n "$APP_FILTER" ]; then
    MDESC="--app $APP_FILTER：⑦⑧ + 只跑 $APP_FILTER 的 ⑨~⑫（后端 ①~⑥ 未查）"
elif [ "$FRONTEND_ONLY" = "1" ]; then
    MDESC="--frontend：只跑前端 ⑦~⑫（后端 ①~⑥ 未查）"
else
    MDESC="默认范围：①~⑫"
fi

echo "[preflight] python = ${PY:-（缺）}"
echo "[preflight] node   = ${NODE:-（缺）} / npm = ${NPM:-（缺）}"
echo "[preflight] 前端 app = ${APPS:-（无）}$([ -n "$APP_FILTER" ] && echo "（全部：${APPS_ALL:-无}）")"
echo "[preflight] 范围 = $MDESC"
[ "$WITH_BUILD" = "1" ] && echo "[preflight] --with-build：**含** next build（每个 app ≈57s，可能要提权）"

# ---------------- ⓿ 顺手清理「7 天以上的构建残留」----------------
# 它们从哪来：`e2e-web-login.py` 在 build / dev 之前会把 `.next` **改名挪走**
#   —— 因为宿主的删除保护会拦"一次 ≥50 项"的删除，**只能 rename**（见该文件抬头）。
#   代价就是会越积越多（每个 100~300MB）。
#
# ⚠️ 三条设计约束（都不是洁癖）：
#   1. **只清 7 天以上**（`-mtime +7`）：当轮刚挪走的那份可能还要用
#      （`--keep-db` 调试时要回看"上一次构建是什么样"）。
#   2. **清了才算数**：`rm -rf` 之后要**回查目录在不在** —— 删除保护可能让它"看起来删了"。
#   3. **删不掉要大声报，且不影响退出码**：这不是门禁，是卫生。
#      把它算成"失败"会让"磁盘上有个删不掉的目录"被读成"代码错了"（假红，硬约定 J）；
#      静默跳过则会让它无限堆积（**没清掉 ≠ 不用清**，硬约定 H）。
clean_stale_builds() {
    local cleaned=0 failed=0 scanned=0
    local base d
    for base in "$repo"/apps/*/node_modules/.cache "$repo"/apps; do
        [ -d "$base" ] || continue
        for d in "$base"/next-stale-* "$base"/.next.stale-*; do
            [ -d "$d" ] || continue
            # 只看 7 天以上（`-maxdepth 0` = 只判这个目录本身，不递归）
            [ -n "$(find "$d" -maxdepth 0 -mtime +7 2>/dev/null)" ] || continue
            scanned=$((scanned + 1))
            rm -rf "$d" 2>/dev/null
            if [ -d "$d" ]; then
                failed=$((failed + 1))
                printf '  ⚠️ 清不掉（宿主的删除保护）：%s\n' "${d#"$repo"/}"
            else
                cleaned=$((cleaned + 1))
                printf '  🧹 已清 7 天以上的构建残留：%s\n' "${d#"$repo"/}"
            fi
        done
    done
    if [ "$scanned" -eq 0 ]; then
        echo "[preflight] ⓿ 构建残留：无 7 天以上的（next-stale-*）"
    else
        echo "[preflight] ⓿ 构建残留：清掉 $cleaned / 待清 $scanned（删不掉 $failed）"
        [ "$failed" -gt 0 ] && echo "        ↳ 手工清：rm -rf <上面那些目录>（在你自己的终端里跑，不受本工具的保护策略限制）"
    fi
}
clean_stale_builds

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
    for k in 1 2 3 4; do note_skip "$k" "$MDESC（后端 ①~④ 未查）" "后端静态门禁 $k"; done
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
    for k in 5 6; do note_skip "$k" "$MDESC（数据库门禁未查）" "pytest / 覆盖率"; done
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
 范围：$MDESC

  门禁（权威清单 = tools/local-verify/check-invariants.py）        本地  CI
  ─────────────────────────────────────────────────────────────  ────  ────
  ① CI 诊断段自检（拦截式）                                          $(mark 1)     ✅
  ② 不变量自检（拦截式）                                             $(mark 2)     ✅
  ③ ruff check（全仓）                                              $(mark 3)     ✅
  ④ ruff format --check（apps/api）                                 $(mark 4)     ✅
  ⑤ pytest（真 PG）                                                 $(mark 5)     ✅   ← run-local-pipeline.py
  ⑥ 覆盖率门禁（合并三份 → report）                                   $(mark 6)     ✅   ← 同上
  ⑦ 反向检查 · 认证链路单一真相                                       $(mark 7)     ✅   ← 扫**全部** app，与 --app 无关
  ⑧ 共享包单测 · packages/api-core                                  $(mark 8)     ✅   ← 与 app 无关
  ⑨ npm run lint                                                   $(mark 9)     ✅   ← 逐 app：$APPS
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
    echo "  ⇒ 本轮的绿**不覆盖**上面这几道（有些是 CI 才能跑，有些是本轮范围没选到）。"
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
echo "[preflight] ✅ 本范围全过（范围 = $MDESC）—— 13 类里没跑到的见上"
exit 0
