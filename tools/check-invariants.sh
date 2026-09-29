#!/usr/bin/env bash
# tools/check-invariants.sh —— 「不变量自检」的薄包装（逻辑在 tools/local-verify/check-invariants.py）
#
# 用户 2026-09-27 的原话：不变量**不是"开工前确认一次"**，是"**每次 push 后跑一次**" ——
# P0→P3 每推一次都可能把它们破掉。所以它必须是一条**命令**，而不是一个清单。
#
# 用法：   bash tools/check-invariants.sh
# 退出码： 0 = 全部通过；非 0 = 有不变量被破（详情在输出里）
#
# ⚠️ 刻意**不用 `set -e`**：要让 6 条检查都跑完 —— 一次看到全部问题，
#    与 CI 诊断段要 `set +e` 是同一条理由（坑 66）。
# ⚠️ 逻辑不写在这里：`.sh` 只负责"找解释器"，可移植的判据全在 `.py` 里
#    （这样它才能被单测和变异验证，硬约定 H/J）。

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- 找一个能跑标准库的 Python ----
# 这个脚本**只用标准库**，所以不挑解释器（不像 run-smoke.ps1 要探测依赖）。
PY=""
for c in "${YIJIAN_PYTHON:-}" \
         "$HOME/.workbuddy/binaries/python/versions/3.13.12/python.exe" \
         "$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
         python3 python; do
  [ -n "$c" ] || continue
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then
    PY="$c"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "[invariants] 找不到 Python（本脚本只用标准库，任意 3.8+ 即可）。" >&2
  echo "[invariants] 可用 YIJIAN_PYTHON=/path/to/python 指定。" >&2
  exit 2
fi

exec "$PY" "$HERE/local-verify/check-invariants.py" "$@"
