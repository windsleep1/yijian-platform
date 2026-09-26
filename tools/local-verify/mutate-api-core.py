#!/usr/bin/env python
"""`packages/api-core` 的变异验证（硬约定 J + 第 2/3 级保证的实证）。

## 为什么这个 harness 是必要的

`docs/22` §9.2.2 声称有"四级保证"。**声称不是证据** —— 这个脚本把其中两级
（编译期穷尽 / 单测钉码表）当场打掉，看它们**是否真的变红**。

## 三类结果必须分开计（2026-09-25 的教训，见 MEMORY 验证套路）

    捕获    锚点打上去了、检查变红了          ← 期望结果
    存活    锚点打上去了、检查**还是绿的**    ← 真问题（用例太弱）
    未应用  锚点没匹配上                       ← 这一轮**什么都没验**，不能读成"存活"

并且：检查**跑没跑成**单独判 —— 变异写坏成语法错误时命令也会非 0，
但那不是"捕获"（`node --test` 的 `# fail` 计数、`tsc` 的 `error TS` 是判据）。

用法：`python tools/local-verify/mutate-api-core.py`（仓库根或任意位置均可）
退出码：0 = 全部捕获且仓库已还原；非 0 = 有存活/未应用/未跑成/还原失败
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "packages" / "api-core"
ADMIN = REPO / "apps" / "admin"
CHAIN = PKG / "src" / "auth-chain.ts"

NODE = "node"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"

# 每条：名字 / 目标文件 / (原文, 变异文) / 检查命令与工作目录 / 判红依据
MUTATIONS: list[dict] = [
    {
        "name": "M1 码集少一个码（40104 不再进刷新重试）",
        "file": CHAIN,
        "old": "new Set([40100, 40101, 40104, 40105])",
        "new": "new Set([40100, 40101, 40105])",
        "cmd": [NODE, "--test", "test/auth-chain.test.ts"],
        "cwd": PKG,
        "expect": ("node_fail", "码表取值被改，单测必须报出失败用例"),
    },
    {
        "name": "M2 把 50003 并进致命码（Redis 抖一下踢掉全站）",
        "file": CHAIN,
        "old": "export const FATAL_AUTH_CODES: ReadonlySet<number> = new Set([40102]);",
        "new": "export const FATAL_AUTH_CODES: ReadonlySet<number> = new Set([40102, 50003]);",
        "cmd": [NODE, "--test", "test/auth-chain.test.ts"],
        "cwd": PKG,
        "expect": ("node_fail", "50003 被误判为致命，单测必须报出失败用例"),
    },
    {
        "name": "M3 破坏单飞（并发三次 → 发三次请求）",
        "file": CHAIN,
        "old": "inflight ??= (async () => {",
        "new": "inflight = (async () => {",
        "cmd": [NODE, "--test", "test/auth-chain.test.ts"],
        "cwd": PKG,
        "expect": ("node_fail", "单飞失效，并发用例必须报出失败"),
    },
    {
        "name": "M4 去掉 finally 复位（一次抖动后永久锁死）",
        "file": CHAIN,
        "old": "        clearTimeout(timer);\n        // 放在 finally：保证下一次还能再发起\n        inflight = null;",
        "new": "        clearTimeout(timer);",
        "cmd": [NODE, "--test", "test/auth-chain.test.ts"],
        "cwd": PKG,
        "expect": ("node_fail", "inflight 不复位，第二轮用例必须报出失败"),
    },
    {
        "name": "M5 削弱成对存回（只校验 access_token）",
        "file": CHAIN,
        "old": '  if (typeof data.access_token !== "string" || typeof data.refresh_token !== "string") return null;',
        "new": '  if (typeof data.access_token !== "string") return null;',
        "cmd": [NODE, "--test", "test/auth-chain.test.ts"],
        "cwd": PKG,
        "expect": ("node_fail", "缺 refresh_token 会被当成成功并只存一半，用例必须报出失败"),
    },
    {
        "name": "M6 给 AuthFailureKind 加一个成员（调用方 switch 漏分支）",
        "file": CHAIN,
        "old": 'export type AuthFailureKind = "retryable" | "fatal" | "other";',
        "new": 'export type AuthFailureKind = "retryable" | "fatal" | "other" | "expired";',
        "cmd": [NPM, "run", "typecheck"],
        "cwd": ADMIN,
        "expect": ("tsc_error", "闭集合被扩，api.ts 的穷尽性守卫必须变成编译错误"),
    },
]


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    r = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def verdict(rc: int, out: str, kind: str) -> str:
    """把一次运行结果判成 捕获 / 存活 / 未跑成。"""
    if kind == "node_fail":
        m = re.search(r"^# fail (\d+)", out, re.M)
        if m and int(m.group(1)) > 0:
            return "捕获"
        if rc != 0:
            return "未跑成"  # 语法错/找不到文件等：命令非 0，但**不是**用例抓到的
        return "存活"
    if kind == "tsc_error":
        if rc != 0 and "error TS" in out:
            return "捕获"
        if rc != 0:
            return "未跑成"
        return "存活"
    raise AssertionError(f"未知判据 {kind}")


def main() -> int:
    if not CHAIN.exists():
        print(f"✗ 找不到 {CHAIN} —— 先确认仓库结构")
        return 2

    backup = CHAIN.with_suffix(".ts.bak")
    # ⚠️ **按字节**读写与比较，不用 `read_text` / `write_text`：
    #    Python 的文本模式在 Windows 上会把 `\n` 写成 `\r\n`，而**读的时候又转回来** ——
    #    于是"还原成功"的文本比较**看不出行尾被改了**（2026-09-26 实测踩到：
    #    一次变异跑完，`auth-chain.ts` 变成 157 个 CRLF，而 harness 报"已还原"，
    #    最后是 prettier 的 `format:check` 把它抓出来 —— 又一个"假绿"。
    #    判据：**还原要字节级**，否则它只是在比较"语义等价"而不是"文件没变"。）
    original = CHAIN.read_bytes()
    shutil.copy2(CHAIN, backup)

    results: list[tuple[str, str]] = []
    try:
        for m in MUTATIONS:
            print(f"\n=== {m['name']} ===")
            text = CHAIN.read_bytes().decode("utf-8")
            if m["old"] not in text:
                results.append((m["name"], "未应用"))
                print("  ✗ 锚点不匹配 —— 本轮什么都没验（记为「未应用」，整轮非 0）")
                continue
            if text.count(m["old"]) > 1:
                results.append((m["name"], "未应用"))
                print(f"  ✗ 锚点出现 {text.count(m['old'])} 次 —— 拒绝盲改，记为「未应用」")
                continue

            CHAIN.write_bytes(text.replace(m["old"], m["new"], 1).encode("utf-8"))
            rc, out = run(m["cmd"], m["cwd"])
            v = verdict(rc, out, m["expect"][0])
            results.append((m["name"], v))
            print(f"  → {v}（期望：{m['expect'][1]}）")
            if v != "捕获":
                tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
                print("    检查输出尾部：")
                for ln in tail:
                    print(f"      {ln}")

            # 每轮立刻还原，缩小"仓库处于变异态"的窗口
            CHAIN.write_bytes(original)
            if CHAIN.read_bytes() != original:
                print("  ★★ 还原失败（字节级），立即中止！")
                return 3
    finally:
        CHAIN.write_bytes(original)

    restored = CHAIN.read_bytes() == original
    backup.unlink(missing_ok=True)

    caught = sum(1 for _, v in results if v == "捕获")
    alive = sum(1 for _, v in results if v == "存活")
    unapplied = sum(1 for _, v in results if v == "未应用")
    notrun = sum(1 for _, v in results if v == "未跑成")
    print("\n================ 汇总 ================")
    print(f"  捕获 {caught} / 存活 {alive} / 未应用 {unapplied} / 未跑成 {notrun}")
    print(f"  仓库已还原：{'是' if restored else '否 ★★ 手动检查！'}")
    if unapplied or notrun:
        print("  ⚠️ 「未应用」「未跑成」都**不能**读成「存活」，但同样让整轮非 0。")
    return 0 if (alive == 0 and unapplied == 0 and notrun == 0 and restored) else 1


if __name__ == "__main__":
    sys.exit(main())
