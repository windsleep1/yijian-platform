"""**管道回执**：让"由别的工具跑过"这件事**有证据**，而不是一句声明。

## 它解决什么

`tools/preflight.sh` 只做静态检查（不碰数据库），所以 **⑤ pytest / ⑥ 覆盖率** 从来不是它跑的
（由 `tools/local-verify/run-local-pipeline.py` 跑）。原来它把这两道**直接标成 ✅** ——
于是"12/12 全过"里混进了两个**没跑过**的 ✅。用户 2026-09-29 把它点名为
**假绿的新变种**，并要求引入第 4 种状态：

    ✅ 本地跑过且通过 ｜ ❌ 本地跑过但失败 ｜ ⏭ 明确跳过（带原因）｜ 📋 由别的工具跑过（引用它的结论）

★ **`📋` 与 `✅` 的区别不是"谁跑的"，而是"我知道谁跑了、而且它通过了"。**
  那就必须**有据可查** —— 否则 `📋` 只是把一句假话换了个符号（**新假绿**）。
  ⇒ 管道跑完写一份**带身份的回执**；`preflight` 只在**回执与当前代码同源**时才引用它。

## "同源"是怎么判的

回执里记 `commit` + `tree`（`git status --porcelain` 与 `git diff HEAD` 一起哈希）。
`preflight` 现场算一遍，**两个都相等**才认：

- 只比 commit 不够：跑完管道之后**改了代码**（工作树脏了）⇒ 回执就不再代表当前代码；
- 把 `status --porcelain`（文件名+状态）也哈希进去，是为了让**新增/删除文件**也算变化
  （新增一个测试文件足以改变 pytest 的结论，而 `git diff HEAD` 看不见未跟踪文件）。

⚠️ 回执放在 **`%TEMP%`**，不落进仓库：它是**工具产物**，不该出现在 `git status` 里
  （同族教训：变异验证曾在仓库里留过一个 `.mutantbak`）。
  代价是系统清临时目录后回执会丢 —— 那时 `preflight` 会退回 `⏭ 明确跳过`，
  **方向是安全的**（宁可说"没跑"，不可说"跑过了"）。

CLI（给 bash 用，所以只吐**每行一条**的纯文本，不用它去解析 JSON）：

    python tools/local-verify/pipeline_stamp.py --status
    → "pytest\tfresh\t354 passed, 1 skipped（回执时间 …）"
    → "coverage\tfresh\tTOTAL 5101 235 95%（回执时间 …）"
    → "pytest\tfailed\tpytest rc=1 / 覆盖率 rc=2"
    → "coverage\tstale\t回执来自 abc1234/tree 9f8e7d，当前是 def5678/tree 1234ab"
    → "pytest\tmissing\t还没有管道回执（跑 run-local-pipeline.py 会写）"

★ **逐门禁一行**（而不是一行总结）：`preflight` 的 ⑤ 与 ⑥ 是两个槽位，
  它们的状态**可以不同**（pytest 挂了、覆盖率没跑）—— 挤成一行就只能二选一地标错。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STAMP = Path(tempfile.gettempdir()) / "yijian-pipeline-stamp.json"


def _git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        return r.stdout if r.returncode == 0 else ""
    except OSError:
        return ""


def fingerprint() -> dict[str, str]:
    """当前代码的指纹：`commit` + 工作树哈希（含未跟踪文件的名字与状态）。"""
    commit = _git("rev-parse", "HEAD").strip()
    blob = _git("status", "--porcelain") + "\x00" + _git("diff", "HEAD")
    return {"commit": commit, "tree": hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]}


def write_stamp(*, pytest_rc: int, cov_rc: int, passed: str, total: str) -> Path:
    """管道收尾时写回执。**成功和失败都写** —— 失败也是一种"我知道它跑过了"。"""
    fp = fingerprint()
    STAMP.write_text(
        json.dumps(
            {
                **fp,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "pytest_rc": pytest_rc,
                "cov_rc": cov_rc,
                "passed": passed,
                "total": total,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return STAMP


def read_stamp() -> dict | None:
    if not STAMP.exists():
        return None
    try:
        return json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status() -> list[tuple[str, str, str]]:
    """逐门禁返回 `[(gate, state, detail), …]`；state ∈ fresh / failed / stale / missing。"""
    st = read_stamp()
    if st is None:
        why = "还没有管道回执（跑 `run-local-pipeline.py` 会写）"
        return [("pytest", "missing", why), ("coverage", "missing", why)]
    fp = fingerprint()
    if st.get("commit") != fp["commit"] or st.get("tree") != fp["tree"]:
        why = (
            f"回执来自 {str(st.get('commit'))[:7]}/tree {st.get('tree')}，"
            f"当前是 {fp['commit'][:7]}/tree {fp['tree']} —— **代码变了，回执不再代表它**"
        )
        return [("pytest", "stale", why), ("coverage", "stale", why)]
    at = st.get("at")
    pytest_rc = int(st.get("pytest_rc", 1))
    cov_rc = int(st.get("cov_rc", 1))
    if pytest_rc != 0:
        return [("pytest", "failed", f"rc={pytest_rc}（回执时间 {at}）"),
                ("coverage", "fresh" if cov_rc == 0 else "failed",
                 f"{st.get('total') or '（没摘到 TOTAL 行）'}（回执时间 {at}）")]
    if cov_rc != 0:
        return [("pytest", "fresh", f"{st.get('passed') or '（没摘到摘要行）'}（回执时间 {at}）"),
                ("coverage", "failed", f"rc={cov_rc}（回执时间 {at}）")]
    return [
        ("pytest", "fresh", f"{st.get('passed') or '（没摘到摘要行）'}（回执时间 {at}）"),
        ("coverage", "fresh", f"{st.get('total') or '（没摘到 TOTAL 行）'}（回执时间 {at}）"),
    ]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--status" not in args:
        print(__doc__)
        return 2
    for gate, state, detail in status():
        print(f"{gate}\t{state}\t{detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
