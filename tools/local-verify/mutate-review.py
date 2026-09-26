"""题目审核链的**变异验证** harness（Batch 9）。

## 为什么要单独一个 harness

审核链的正确性建立在**"写进去了"**这件事上 —— 而"写没写"最容易被一个
`if` 写错、一行 `sets` 漏掉悄悄破坏，且**功能测试仍然全绿**（接口返回 200、
状态也对，只是审核人那一列永远是 NULL）。所以按项目规矩：**安全 / 审计类改动必须做变异验证**
（打掉唯一判定点，看用例是否真变红）。

## 为什么不能只"改一下跑一遍"

这些用例是 **HTTP 层**的 —— 被变异的代码跑在 **API 进程**里。
所以每个变异都必须：**改文件 → 重启 API → 跑用例 → 还原**。
本脚本把这一圈自动化，并且遵守三条已有的硬约定：

| 约定 | 落点 |
|---|---|
| **K2** 非幂等操作要读回状态 | 变异后**读回文件**确认真的变了（否则是"未应用"，不是"存活"） |
| 变异还原**别用 `git checkout -- <file>`** | 用 `shutil.copy2` 的**文件备份**（`git checkout --` 会连带丢掉未提交的改动） |
| **锚点不匹配 = 未应用，且让整轮非 0** | 单独计数 `unapplied`，并计入退出码 |

## 判据

    捕获   = 该轮 pytest **非 0**      → 用例挡住了这个变异（好）
    存活   = 该轮 pytest **为 0**      → **用例太弱**（要么补断言，要么这个变异本就无害）
    未应用 = 锚点没找到 / 文件没变     → **这次变异没打上去**，不能读成"存活"

⚠️ 还有一种情况要人判断：**"变异存活"的第三种解释 —— 变异本身不成立**
（被保护的性质有**多层**，只打掉一层什么都不会发生）。见 `MEMORY-detail.md` 2026-09-25 那条。
本脚本每条变异都**只打掉一个点**，所以如果出现存活，先问"这个点真的被保护了吗"。

用法：

    python tools/local-verify/mutate-review.py            # 跑全部变异
    python tools/local-verify/mutate-review.py --list     # 只看变异清单
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "apps" / "api"
SERVICE = API_DIR / "app" / "services" / "question_service.py"
PG_BIN = Path(r"C:/Users/15437/.workbuddy/binaries/pg/pg16/Library/bin")
PG_DATA = Path(r"C:/Users/15437/.workbuddy/binaries/pg/data16")
PY = r"C:/Users/15437/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
TMP = Path(r"C:/Users/15437/AppData/Local/Temp")
PG_PORT, API_PORT = 55432, 8123

#: 变异清单。每条 `(名字, 原文, 替换成)` —— 原文必须**逐字**出现一次，否则算"未应用"。
MUTATIONS: list[tuple[str, str, str]] = [
    (
        "M1 审核不写 reviewed_at",
        '        "reviewed_at = now()",\n',
        "",
    ),
    (
        "M2 审核不写 reviewed_by",
        '        "reviewed_by = :actor",\n',
        "",
    ),
    (
        "M3 幂等分支被绕过（已发布的题再 approve 会走到状态机拒绝）",
        "    if old_status == target:\n"
        "        return await _build_detail(db, row=row, viewer=actor), True\n",
        "    if False:\n        return await _build_detail(db, row=row, viewer=actor), True\n",
    ),
    (
        "M4 reject 也写 published_at（approve 条件恒真）",
        '    if payload.decision == "approve":\n',
        "    if True:\n",
    ),
    (
        "M5 审核复用旧 action（review → update），留痕查不出来",
        '        action="review",\n',
        '        action="update",\n',
    ),
]

PYTEST_TARGET = "tests/test_question_review.py"

ENV = dict(os.environ)
ENV.update(
    {
        "PYTHONUTF8": "1",
        "PGCLIENTENCODING": "UTF8",
        "DATABASE_URL": f"postgresql+asyncpg://yijian@127.0.0.1:{PG_PORT}/yijian",
        # ⚠️ **必须有这一行**：`tests/conftest.py` 是
        # `BASE = os.environ.get("AI_BASE", "http://localhost:8000")` ——
        # 不设它，用例会去连 8000 端口，连不上就把**整个模块 skip 掉**，
        # 而 `pytest` 在"全部 skip"时**退出码是 0**（见 run_pytest 的基线守卫）。
        # 2026-09-25 实测：漏了这一行 → 12 skipped → harness 报"基线全绿" → 4 个假"存活"。
        "AI_BASE": f"http://127.0.0.1:{API_PORT}",
        "APP_ENV": "local",
        "JWT_SECRET": "local-verify-secret-not-for-production",
        "ADMIN_INIT_PHONE": "13800000000",
        "ADMIN_INIT_PASSWORD": "Admin@123456",
        "SMS_PROVIDER": "mock",
    }
)


def say(msg: str) -> None:
    print(f"[mutate] {msg}", flush=True)


def wait_port(port: int, label: str, tries: int = 60) -> None:
    for _ in range(tries):
        s = socket.socket()
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            say(f"{label} 就绪 (:{port})")
            return
        except Exception:
            pass
        finally:
            s.close()
        time.sleep(1)
    raise SystemExit(f"{label} 未在 {tries}s 内就绪")


def health() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/api/v1/health", timeout=3):
            return True
    except Exception:
        return False


def start_api(log_name: str) -> subprocess.Popen:
    log = open(TMP / log_name, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [
            PY,
            "serve_fake_redis.py",
            "--pg-port",
            str(PG_PORT),
            "--api-port",
            str(API_PORT),
            "--log-file",
            str(TMP / "mutate-api-inner.log"),
        ],
        cwd=str(REPO / "tools" / "local-verify"),
        env=ENV,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if health():
            return proc
        time.sleep(1)
    proc.terminate()
    raise SystemExit("API 未在 60s 内就绪")


def stop_api(proc: subprocess.Popen) -> None:
    """等端口真的释放 —— 否则下一轮 API 起不来（"端口开着 ≠ 就绪"的反面同样成立）。"""
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()
        proc.wait(timeout=30)
    for _ in range(30):
        if not health():
            return
        time.sleep(1)
    raise SystemExit("API 停止后仍在响应 —— 下一轮会连到旧进程，结论不可信")


def run_pytest() -> tuple[int, str]:
    """跑一遍子集。返回 `(退出码, 摘要行)`。

    ⚠️ **调用方不能只看退出码**：pytest 在**全部用例被 skip** 时同样退出 0。
    2026-09-25 实测过一次 —— harness 漏了 `AI_BASE`，12 条全 skip、退出码 0，
    于是"基线全绿"成立，接着跑出 **4 个假的"变异存活"**。
    所以这里把摘要行也返回出去，由 `assert_ran()` 再判一次"到底跑了没有"。
    """
    r = subprocess.run(
        # ⚠️ `-rfEXs`：`-r` 是**替换**默认值 —— 只写 `-rs` 会把默认的 `-rfE` 顶掉，
        #    短汇总里**就不会有 `FAILED` 行**（本机实测）⇒ 变红时只说"有失败"、
        #    不说"哪条失败"。三处（本文件 / run-smoke.ps1 / CI 的 pytest 步骤）同口径。
        [PY, "-m", "pytest", PYTEST_TARGET, "-q", "-rfEXs", "-p", "no:cacheprovider"],
        cwd=str(API_DIR),
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    summary = lines[-1][:160] if lines else "(无输出)"
    say("  pytest: " + summary)
    # skip 原因单独打出来 —— 全 skip 时这是**唯一的线索**
    for ln in lines:
        if ln.startswith("SKIPPED"):
            say("    " + ln[:160])
    return r.returncode, summary


def assert_ran(summary: str) -> str | None:
    """判断这一轮"**真的跑了**"。返回 None 表示没问题，否则返回错误说明。

    判据（硬约定 M / "绿红判据本身也要先被验证"）：
      · 至少 1 条 passed —— 否则谈不上"绿"；
      · 0 条 skipped —— 本子集的设计里**没有任何"换个干净环境仍该 skip"的用例**
        （`sql_fetch` 的 skip 依赖 `DATABASE_URL`，本 harness 一定会设）。
        出现 skip 就说明**环境配错了**，不是"合理地跳过"。
    """
    import re

    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", summary)) else 0
    skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", summary)) else 0
    if passed == 0:
        return f"一条都没通过（摘要：{summary}）—— 这一轮没有意义"
    if skipped > 0:
        return f"有 {skipped} 条被 skip（摘要：{summary}）—— 环境没配对，不是「该跳过」"
    return None


def main() -> int:
    if "--list" in sys.argv:
        for i, (name, _, _) in enumerate(MUTATIONS, 1):
            print(f"{i}. {name}")
        return 0

    original = SERVICE.read_text(encoding="utf-8")
    backup = TMP / "mutate-review-question_service.py.bak"
    shutil.copy2(SERVICE, backup)  # ← 文件备份还原（不用 git checkout --）
    say(f"已备份 {SERVICE.name} → {backup.name}")

    (PG_DATA / "postmaster.pid").unlink(missing_ok=True)
    # 不保留句柄：PG 的收尾统一走 `pg_ctl stop`（见 finally），
    # 而 `postgres.exe` 被 terminate 会留下不干净的关闭 —— 这一点与 API 那次不同。
    subprocess.Popen(
        [
            str(PG_BIN / "postgres.exe"),
            "-D",
            str(PG_DATA),
            "-p",
            str(PG_PORT),
            "-c",
            "listen_addresses=127.0.0.1",
        ],
        stdout=open(TMP / "mutate-pg.log", "w", encoding="utf-8"),  # noqa: SIM115
        stderr=subprocess.STDOUT,
    )
    results: list[tuple[str, str]] = []
    try:
        wait_port(PG_PORT, "PostgreSQL")
        for sub in ("seed-rbac", "seed-admin"):
            subprocess.run(
                [PY, "-m", "app.cli", sub],
                cwd=str(API_DIR),
                env=ENV,
                capture_output=True,
                timeout=300,
            )

        # ---- 基线：未变异时必须全绿，否则下面的"变红"说明不了任何事 ----
        say("基线（未变异）……")
        api = start_api("mutate-api-baseline.log")
        try:
            rc, summary = run_pytest()
        finally:
            stop_api(api)
        if rc != 0:
            say(f"!! 基线就是红的（rc={rc}）—— 先修好再谈变异")
            return 2
        problem = assert_ran(summary)
        if problem:
            say(f"!! 基线不成立：{problem}")
            return 2
        say("基线全绿 ✓（且已确认**真的跑了**，不是全 skip）")

        # ---- 逐个变异 ----
        for name, old, new in MUTATIONS:
            say(f"=== {name} ===")
            if original.count(old) != 1:
                say(f"  锚点出现 {original.count(old)} 次（要求恰好 1）→ 记为**未应用**")
                results.append((name, "未应用"))
                continue
            mutated = original.replace(old, new, 1)
            SERVICE.write_text(mutated, encoding="utf-8")

            # K2：读回文件，确认变异真的写进去了（否则"没打上去"会被读成"存活"）
            if SERVICE.read_text(encoding="utf-8") == original:
                say("  文件内容未变 → 记为**未应用**")
                results.append((name, "未应用"))
                continue

            api = start_api(f"mutate-api-{len(results)}.log")
            try:
                rc, summary = run_pytest()
                problem = assert_ran(summary) if rc == 0 else None
            finally:
                stop_api(api)
                # 每轮结束立刻还原，缩小"仓库处于变异态"的窗口
                shutil.copy2(backup, SERVICE)
                if SERVICE.read_text(encoding="utf-8") != original:
                    raise SystemExit(f"还原失败：{SERVICE.name} 没回到原样，请手动处理！")

            if problem:
                # ⚠️ 这一轮**根本没跑** → 不能读成"存活"（那是把"没打上去"当成"打不动"）
                say(f"  → ⚠️ 未跑成：{problem}")
                results.append((name, "未跑成"))
            else:
                results.append((name, "捕获" if rc != 0 else "存活"))
                say(f"  → {'✅ 捕获' if rc != 0 else '❌ 存活'}")
    finally:
        shutil.copy2(backup, SERVICE)
        subprocess.run(
            [str(PG_BIN / "pg_ctl.exe"), "-D", str(PG_DATA), "stop", "-m", "fast"],
            capture_output=True,
        )
        say("PG 已停")

    restored = SERVICE.read_text(encoding="utf-8") == original
    say("")
    say("================ 结果 ================")
    for name, verdict in results:
        say(f"  {verdict:6s}  {name}")
    caught = sum(1 for _, v in results if v == "捕获")
    alive = sum(1 for _, v in results if v == "存活")
    unapplied = sum(1 for _, v in results if v == "未应用")
    notrun = sum(1 for _, v in results if v == "未跑成")
    say(f"  捕获 {caught} / 存活 {alive} / 未应用 {unapplied} / 未跑成 {notrun}")
    say(f"  仓库已还原：{'是' if restored else '否 ★★ 手动检查！'}")

    # 未应用 = 这次变异没打上去；未跑成 = 这一轮没跑 → **两者都不能读成存活** → 非 0
    return 0 if (alive == 0 and unapplied == 0 and notrun == 0 and restored) else 1


if __name__ == "__main__":
    sys.exit(main())
