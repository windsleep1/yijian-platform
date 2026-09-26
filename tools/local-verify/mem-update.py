#!/usr/bin/env python
"""`MEMORY.md` 的**物理约束**更新器（硬约定 Q 的执行工具）。

## 为什么需要它（而不是"记得先减后加"）

Q 规则已经写得很清楚，**但连续两批都滑成"先加后减"**（2026-09-25 冲到 15349/余量 47；
2026-09-26 冲到 15770/越截断点）。原因不是"不小心"，是**规则没有物理约束** ——
人可以绕过一句口号，绕不过一个会回滚的脚本。

## 用法

    # 批内第一次调用会自动开启一个"批"（记下开始字节数）
    python tools/local-verify/mem-update.py \\
        --sink "<要被替换掉的原文>"="<替换成什么>" \\
        --add  "<锚点行的一部分>"="<要插入的整行文本>" \\
        --budget 0

    python tools/local-verify/mem-update.py --end          # 收口 + 断言 + 清理

    python tools/local-verify/mem-update.py --status       # 只报数（当前字节 / 余量 / 批状态）

## 三条硬约束（都会**回滚**）

1. **`--sink` 必须真的匹配上**：锚点找不到 → 整次操作回滚并非 0 退出
   （否则"我以为减了"会变成一个安静的假账）。
2. **有 `--add` 就必须有 `--sink`**：想加内容却一个下沉都没做 → 直接拒绝
   （这就是"没找到下沉项就先别加"的机械形式）。
3. **批末字节 ≤ 批初字节 + budget**（`--budget` 默认 **0**，即**净增必须为 0**），
   且任何时刻都**不得越过注入截断点 15396**。

## 实现细节（两个刻意的选择）

- **全程按字节**（`read_bytes` / `write_bytes`）：文本模式在 Windows 上会把 LF 写成 CRLF，
  而**读回比较看不出来**（坑 64）。这里的"回滚"必须是真的字节级回滚。
- **批状态与备份落盘**（`.workbuddy/memory/.mem-batch.json` / `.mem-batch.bak`）：
  脚本崩了也不会把文件留在半改状态 —— 下次调用会先看到备份并提示恢复。

退出码：0 成功；1 被约束拒绝（已回滚）；2 用法错误。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MEM = Path(__file__).resolve().parents[2].parent / ".workbuddy" / "memory" / "MEMORY.md"
STATE = MEM.parent / ".mem-batch.json"
BACKUP = MEM.parent / ".mem-batch.bak"
TRUNCATE_AT = 15396  # 实测的注入截断点（超过它 = 内容会被吃掉）


def size_of(p: Path) -> int:
    return len(p.read_bytes())


def say(msg: str) -> None:
    print(f"[mem] {msg}", flush=True)


def load_state() -> dict | None:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return None


def save_state(d: dict) -> None:
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def open_batch() -> dict:
    st = {
        "start_bytes": size_of(MEM),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sunk": 0,
        "added": 0,
        "calls": 0,
    }
    shutil.copy2(MEM, BACKUP)  # 崩溃也不丢
    save_state(st)
    say(f"开批：start_bytes={st['start_bytes']}（备份 → {BACKUP.name}）")
    return st


def unescape(s: str) -> str:
    """允许在命令行里用 `\\n` / `\\t` 表示换行与制表符（多行/缩进锚点在 shell 里没法直接写）。"""
    return s.replace("\\n", "\n").replace("\\t", "\t")


def parse_pair(s: str, flag: str) -> tuple[str, str]:
    if "=>" not in s:
        say(f"✗ {flag} 需要 `原文=>替换成什么` 的形式")
        sys.exit(2)
    old, new = s.split("=>", 1)
    if not old:
        say(f"✗ {flag} 的原文不能为空")
        sys.exit(2)
    return unescape(old), unescape(new)


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--sink", action="append", default=[], metavar="OLD=>NEW")
    ap.add_argument("--add", action="append", default=[], metavar="ANCHOR=>TEXT")
    ap.add_argument("--budget", type=int, default=0, help="批内允许的净增字节（默认 0）")
    ap.add_argument("--end", action="store_true", help="收口本批：断言 + 清理")
    ap.add_argument("--status", action="store_true", help="只报数")
    ap.add_argument("--force-end", action="store_true", help="放弃本批（保留当前内容，删状态）")
    args = ap.parse_args()

    if not MEM.exists():
        say(f"✗ 找不到 {MEM}")
        return 2

    if args.status:
        st = load_state()
        b = size_of(MEM)
        say(f"当前 = {b} bytes ｜ 距截断点 {TRUNCATE_AT} 余量 = {TRUNCATE_AT - b}")
        say(f"批状态：{st if st else '（未开批）'}")
        return 0

    if args.force_end:
        STATE.unlink(missing_ok=True)
        BACKUP.unlink(missing_ok=True)
        say("已放弃本批（内容保留、状态清理）")
        return 0

    # ---------- 收口 ----------
    if args.end:
        st = load_state()
        if not st:
            say("✗ 没有进行中的批（先做一次 --sink/--add）")
            return 2
        end = size_of(MEM)
        growth = end - st["start_bytes"]
        allowed = args.budget if args.budget != 0 else 0
        ok = growth <= allowed and end <= TRUNCATE_AT
        say(f"批收口：start={st['start_bytes']} end={end} 净增={growth} 允许={allowed}")
        say(f"  下沉 {st['sunk']} / 新增 {st['added']} 字节（{st['calls']} 次调用）")
        if end > TRUNCATE_AT:
            say(f"✗ 越过注入截断点 {TRUNCATE_AT}！—— 立即从 {BACKUP.name} 恢复")
            if BACKUP.exists():
                shutil.copy2(BACKUP, MEM)
            STATE.unlink(missing_ok=True)
            return 1
        if not ok:
            say("✗ 净增超过预算 —— 减法没做到位。**本批不通过**（内容保留，便于你重做减法）")
            return 1
        STATE.unlink(missing_ok=True)
        BACKUP.unlink(missing_ok=True)
        say(f"✓ 通过。距截断点余量 = {TRUNCATE_AT - end}")
        return 0

    # ---------- 变更 ----------
    if not args.sink and not args.add:
        say("✗ 什么都没要求做（--sink / --add / --status / --end 至少一个）")
        return 2

    if args.add and not args.sink:
        say("✗ 有 --add 但一个 --sink 都没有 —— 拒绝「只加不减」（硬约定 Q 的机械形式）")
        return 1

    st = load_state() or open_batch()
    original = MEM.read_bytes()
    text = original.decode("utf-8")
    before = len(original)
    sunk = added = 0

    try:
        for spec in args.sink:
            old, new = parse_pair(spec, "--sink")
            if text.count(old) != 1:
                raise SystemExit(
                    f"--sink 锚点匹配 {text.count(old)} 次（要求恰好 1 次）：{old[:60]!r}"
                )
            text = text.replace(old, new, 1)
            delta = len(old.encode()) - len(new.encode())
            sunk += delta
            say(f"  下沉 {delta:+d} 字节：{old[:48]!r}…")

        for spec in args.add:
            anchor, block = parse_pair(spec, "--add")
            block = unescape(block)
            if text.count(anchor) != 1:
                raise SystemExit(
                    f"--add 锚点匹配 {text.count(anchor)} 次（要求恰好 1 次）：{anchor[:60]!r}"
                )
            text = text.replace(anchor, f"{anchor}\n{block}", 1)
            added += len(block.encode())
            say(f"  新增 {len(block.encode())} 字节（锚点：{anchor[:40]!r}…）")

        MEM.write_bytes(text.encode("utf-8"))
        after = size_of(MEM)
        st["sunk"] += sunk
        st["added"] += added
        st["calls"] += 1
        save_state(st)
    except SystemExit as exc:
        MEM.write_bytes(original)  # ★ 按字节回滚
        say(f"✗ {exc} → 已按字节回滚（文件未变：{size_of(MEM) == before}）")
        return 1

    growth = after - st["start_bytes"]
    budget = args.budget if args.budget != 0 else 0
    say(f"本次：{before} → {after}（本批净增 {growth}，预算 {budget}）")
    if after > TRUNCATE_AT:
        MEM.write_bytes(original)
        say(f"✗ 越过截断点 {TRUNCATE_AT} → 已回滚")
        return 1
    if growth > budget:
        say(f"⚠️ 本批净增已超预算 {budget} —— 还来得及：补一次 --sink，或等 --end 时不通过")
    say(f"距截断点余量 = {TRUNCATE_AT - after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
