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

## 四条硬约束（前三条都会**回滚**）

1. **`--sink` 必须真的匹配上**：锚点找不到 → 整次操作回滚并非 0 退出
   （否则"我以为减了"会变成一个安静的假账）。
2. **有 `--add` 就必须有 `--sink`**：想加内容却一个下沉都没做 → 直接拒绝
   （这就是"没找到下沉项就先别加"的机械形式）。
3. **批末字节 ≤ 批初字节 + budget**（`--budget` 默认 **0**，即**净增必须为 0**），
   且任何时刻都**不得越过注入截断点 15396**。
4. **不制造重复行**（2026-09-26 补，见下）：`--add` 的新块里若有一行**已经在文件里存在**，
   拒绝并回滚；改完之后再做一次**全局判重**（整行相同、去掉首尾空白后长度 ≥ 12 的行）。
   > 为什么加这条：我实际踩过 —— `--add` 是**追加**，而我把**锚点原文也写进了新块**，
   > 于是同一行在文件里出现了两次。**脚本当时没说话**，是我事后肉眼发现的。
   > 判据：**"加完之后有没有重复项"是脚本能机械回答的问题，就不该由人来看。**

## 能力边界（写在 `--help` 里，不靠用的人记住）

- 它只会做「**锚点恰好匹配 1 次**的字符串替换 / 追加」，**不理解 Markdown 结构**
  （不知道哪些行是标题、哪些是列表项）。
- 它**不判断内容对不对**：语义是否等价、是否符合本文件的写法规范、数字是否过期 —— 都是你自己的事。
- 判重是**字面**的：两行**语义重复但字面不同**（例如同一件事换个说法）**检不出来**。
- 短行不参与判重（`len < 12`，如 `---`、`> `、`|`）—— 见 `DUP_MIN_LEN`。
- 只操作**一个文件**（`MEMORY.md`）；不做跨文件 / 跨仓库的动作。
- **自动恢复只在两种情况下发生**：`--end` 时越过截断点，或本次变更被约束拒绝。
  其它情况（例如你事后觉得改错了）**需要人工**：备份在 `.workbuddy/memory/.mem-batch.bak`。
- 它**不会**替你决定"该下沉哪一条"—— 那正是硬约定 Q 的第一步，属于判断，不属于机械。
- ★ `--self-test`：**证明上面这些判据自己会响**（正例 + 反例 + 边界各一条）。
  > 依据：硬约定 J —— **判据本身也要能被证伪**；不能构造出"它应该报相反结果"的场景，它就不是判据。

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
DUP_MIN_LEN = 12  # 判重只看"整行 strip 后相同、且长度 ≥ 12"的行（`---` / `> ` / `|` 这类短行不参与）


def line_dups(text: str) -> dict[str, int]:
    """**重复行** → 出现次数（整行 strip 后相同，且长度 ≥ `DUP_MIN_LEN`）。"""
    counter: dict[str, int] = {}
    for raw in text.splitlines():
        s = raw.strip()
        if len(s) >= DUP_MIN_LEN:
            counter[s] = counter.get(s, 0) + 1
    return {k: v for k, v in counter.items() if v > 1}


def existing_lines(text: str) -> set[str]:
    return {ln.strip() for ln in text.splitlines() if len(ln.strip()) >= DUP_MIN_LEN}


def block_conflicts(block: str, existing: set[str]) -> list[str]:
    """新块里那些**文件里已经有**的行。

    最常见的原因：**把锚点原文也写进了新块** —— 而 `--add` 是**追加**
    （`anchor + "\\n" + block`）⇒ 同一行会出现两次。我实际犯过这个错，且脚本当时没说话。
    """
    out: list[str] = []
    for raw in block.splitlines():
        s = raw.strip()
        if len(s) >= DUP_MIN_LEN and s in existing and s not in out:
            out.append(s)
    return out


def self_test() -> int:
    """证明这些判据**自己会响**：正例 / 反例 / 边界各一条（硬约定 J）。"""
    base = (
        "AAA 这是一条足够长的已有行，长度超过判重阈值\n"
        "BBB 这是另一条足够长的已有行，也超过判重阈值\n"
        "---\n"
        "| a | b |\n"
    )
    cases: list[tuple[str, int, int]] = [
        ("1) 干净的新块 → 不该报", len(block_conflicts("CCC 一条全新的长行，文件里没有", existing_lines(base))), 0),
        (
            "2) 新块里含锚点原文 → 必须报（我犯过的那个错）",
            len(block_conflicts("AAA 这是一条足够长的已有行，长度超过判重阈值", existing_lines(base))),
            1,
        ),
        (
            "3) 新块里含别处的已有行 → 必须报",
            len(block_conflicts("BBB 这是另一条足够长的已有行，也超过判重阈值", existing_lines(base))),
            1,
        ),
        ("4) 边界：重复的**短行**不参与判重", len(line_dups("---\n---\n| a |\n| a |\n")), 0),
        (
            "5) 全局判重：真重复了要报",
            len(line_dups(base + "AAA 这是一条足够长的已有行，长度超过判重阈值\n")),
            1,
        ),
    ]
    bad = 0
    for name, got, want in cases:
        ok = got == want
        print(f"  [{'ok' if ok else 'FAIL'}] {name}（got={got} want={want}）")
        bad += 0 if ok else 1
    print(f"[mem-update --self-test] {'ALL PASSED' if bad == 0 else f'{bad} CHECK(S) FAILED'}")
    return 0 if bad == 0 else 1


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
    ap = argparse.ArgumentParser(
        description=__doc__,  # ★ `--help` 直接打印上面那份**唯一**的说明（含「能力边界」）
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--sink", action="append", default=[], metavar="OLD=>NEW")
    ap.add_argument("--add", action="append", default=[], metavar="ANCHOR=>TEXT")
    ap.add_argument("--budget", type=int, default=0, help="批内允许的净增字节（默认 0）")
    ap.add_argument("--end", action="store_true", help="收口本批：断言 + 清理")
    ap.add_argument("--status", action="store_true", help="只报数")
    ap.add_argument("--self-test", action="store_true", help="证明判据自己会响（不碰 MEMORY.md）")
    ap.add_argument("--force-end", action="store_true", help="放弃本批（保留当前内容，删状态）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

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
            # ★ 约束 4a：新块里不能有"文件里已经有"的行。
            #    `--add` 是**追加**（anchor + "\n" + block）⇒ 把锚点原文也写进新块，就会出现两次。
            #    我实际犯过这个错，而当时脚本一句话都没说（硬约定 J：判据要能证伪，也要真的响）。
            dup_in_block = block_conflicts(block, existing_lines(text))
            if dup_in_block:
                raise SystemExit(
                    f"--add 的新块里有 {len(dup_in_block)} 行与文件已有行**完全相同** —— 拒绝。\n"
                    "      最常见的原因是**把锚点原文也写进了新块**（本脚本是追加 ⇒ 该行会出现两次）。\n"
                    "      重复行：" + " ｜ ".join(c[:60] for c in dup_in_block[:3])
                )
            text = text.replace(anchor, f"{anchor}\n{block}", 1)
            added += len(block.encode())
            say(f"  新增 {len(block.encode())} 字节（锚点：{anchor[:40]!r}…）")

        # ★ 约束 4b：改完再做一次**全局判重**（管住"下沉 + 新增组合起来"产生的重复）
        dups = line_dups(text)
        if dups:
            worst = sorted(dups.items(), key=lambda kv: -kv[1])[:3]
            raise SystemExit(
                f"改完出现 {len(dups)} 种重复行 —— 拒绝（本脚本不该制造重复）："
                + " ｜ ".join(f"{k[:50]!r}×{v}" for k, v in worst)
            )

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
