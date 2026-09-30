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
        --add  "<锚点（**必须到行尾为止**，或干脆整行）>"="<要插入的整行文本>" \\
        --budget 0

    python tools/local-verify/mem-update.py --end          # 收口 + 断言 + 清理

    python tools/local-verify/mem-update.py --status       # 只报数（当前字节 / 余量 / 批状态）

    # ★ 逐对打印**字节差**（"别估、量"的可观测形式）
    python tools/local-verify/mem-update.py --measure \
        --sink "…"=>"…" --add "…"=>"…"

★ **「下沉」与「删掉」不是同一件事**：**单段** `--sink` 删掉 ≥ 200 字节时，**必须**声明
  这段内容去哪了 ——
  这段内容去哪了 ——
    · `--sink-to "<短语>"`：该短语**既**是被删原文的子串、**又**出现在 `MEMORY-detail.md` 里；
    · `--sink-inplace "<理由>"`：本批**不是下沉**（就地收紧 / 订正过期信息），理由会留痕。
  判据：**只过「主文件变小」叫删除，两条都过才叫下沉**（坑 79）。

也可以**一次做完** —— **有 `--sink` / `--add` 时，脚本会先应用、再收口**：

    python tools/local-verify/mem-update.py --sink "…"=>"…" --add "…"=>"…" --end --budget 0

## 七条约束（**本工具自己的编号** —— 与仓库的「不变量 1~8」是**两套**；违反任一条都拒绝/回滚）

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
5. **`--end` 必须在变更之后执行**（2026-09-26 修）：这段原先排在变更**之前** ⇒
   `--sink X --end` 这样的**一次调用**里，`--end` 先跑、报一句"没有进行中的批"就 `return 2`，
   **`--sink` 从来没有执行** —— 而文件**一个字都没改**（实测：13 处下沉 + 4 条新增全被吞掉）。
   > 教训：**一个"看起来像失败"的报错，可能掩盖"命令根本没做那件事"**。
   > 拿它当判据之前，先看**文件字节数有没有变**（坑 64 的同族：看字节，不看叙事）。
6. **`--add` 的锚点必须停在行边界**（2026-09-28 加，见 `add_after_line`）：
   它原来是"在锚点子串之后插入"，于是**锚点取半行时会把这一行劈成两半** ——
   前半留在原行、后半被推到新块后面。症状**看起来像**"新内容没插进去"，实际是**把旧行拆了**。
   > 判据：锚点结尾要么是 `\n`、要么是文末；否则**拒绝**（不替你"补到行尾" —— 那只是
   > 把一种静默换成另一种静默）。`--self-test` 里有正例/反例各一条。

7. ★ **「下沉」必须有两个断言**（2026-09-29 加，见 `verify_sink_destinations`；坑 79）：
   ① 主文件变小（原本就有）；② **被删掉的内容真的出现在 `MEMORY-detail.md` 里**（新增）。
   **只过 ① 叫「删除」，不叫「下沉」** —— 上一批实测踩到：4 条规则被 `--sink` 拿掉、
   却从没写进 detail，而当时的判据**管量不管去处**，全程没响。
   > 判据：`--sink` 是**承诺**，承诺要被**机械检查**兑现 —— 不能靠"我记得写了"。
   > 与所有「机械对账」同族（`mem-update.py` 执行 Q、`check-invariants` 执行 §8 / 不变量 6~8）。

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
- **`--sink-to` 只回答"我声明的那个短语在不在 detail 里"** —— 它**不判断**"这段内容是不是
  被完整搬走了"。短语是你自己挑的，挑个没有代表性的短语仍能过 ⇒ 它是**留痕**，不是**审计**。
- **`--sink-inplace "<理由>"` 不做任何验证**（工具无法判断"压缩后内容还在不在"）：它只是一个
  **留痕的声明** —— 所以**必须写理由**（空理由被拒），脚本会**大声打印**出来，
  让人一眼看见"这批减法没走 detail"。
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
from typing import Callable

MEM = Path(__file__).resolve().parents[2].parent / ".workbuddy" / "memory" / "MEMORY.md"
STATE = MEM.parent / ".mem-batch.json"
BACKUP = MEM.parent / ".mem-batch.bak"
TRUNCATE_AT = 15396  # 实测的注入截断点（超过它 = 内容会被吃掉）
DUP_MIN_LEN = (
    12  # 判重只看"整行 strip 后相同、且长度 ≥ 12"的行（`---` / `> ` / `|` 这类短行不参与）
)


DETAIL = MEM.parent / "MEMORY-detail.md"
#: ★ **单段** `--sink` 删掉 ≥ 这么多字节 ⇒ 那是「删掉了一段内容」（≈ 一条规则 / 一段判据
#:   的量级），不是「换个说法」⇒ 必须用 `--sink-to` 声明它去了 detail 的哪里
#:   （或用 `--sink-inplace "<理由>"` 明确声明这批不是下沉）。
SINK_VERIFY_MIN = 200
SINK_TO_MIN_LEN = 6


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


def add_after_line(text: str, anchor: str) -> int:
    """`--add` 的插入位置 = **锚点所在那一行的行尾**。

    ⚠️ 2026-09-28 加这道守卫（实测踩到）：`--add` 原来是在**锚点子串**之后插入
    （`text.replace(anchor, anchor + "\\n" + block)`）⇒ 锚点只取半行时，会把这一行**劈成两半**：
    前半点留在原行、后半点被推到新块后面。症状**看起来像**"新内容没插进去"，
    实际是**把旧行拆了** —— 实测：`--add "- ⚠️ **本机跑全量**：\\`run-local-pipeline.py\\`"` 把
    那一行拆成了"只剩开头的残行 + 新块 + 原来的后半句"。

    ⇒ 判据：**锚点必须停在行边界上**（匹配结尾要么是 `\\n`，要么是文末）。
      不满足就**拒绝**，而**不是**"聪明地替你补到行尾" —— 那只是把一种静默换成另一种静默。
    """
    n = text.count(anchor)
    if n != 1:
        raise ValueError(f"--add 锚点匹配 {n} 次（要求恰好 1 次）：{anchor[:60]!r}")
    start = text.index(anchor)
    end = start + len(anchor)
    if end != len(text) and text[end] != "\n":
        rest_of_line = text[end:].split("\n", 1)[0]
        raise ValueError(
            "--add 锚点**停在一行中间** —— 拒绝（否则会把这一行劈成两半）。\n"
            f"      锚点结尾之后还有：{rest_of_line[:60]!r}\n"
            "      ⇒ 把锚点写成**到行尾为止**的整段文本（或干脆整行）。"
        )
    return end


def _detail_body() -> str:
    return DETAIL.read_text(encoding="utf-8") if DETAIL.exists() else ""


def verify_sink_destinations(
    sunk_items: list[tuple[str, int]],
    phrases: list[str],
    inplace: str | None,
    detail_body: str,
) -> str | None:
    """★ 硬约束 7：**「下沉」与「删掉」不是同一件事**（返回 `None` = 通过，否则是拒绝原因）。

    上一批实测踩到：用 `--sink` 从 `MEMORY.md` 拿掉 4 条规则、**从没写进 `MEMORY-detail.md`** ——
    那不是下沉，是**删除**。而当时的判据只保证「主文件变小 + 无重复行」⇒ **管量不管去处**。

    ⇒ **「下沉」= 两个断言都过**：
      ① 主文件变小（原本就有）；
      ② **被删掉的内容出现在 detail 里**（新增）。

    **怎么"机械地"回答 ②**（本工具不理解 Markdown，也不判断语义）：
      调用方用 `--sink-to <短语>` 声明"这段内容去了 detail 的这句"；脚本断言该短语
      **既**是被删原文的子串、**又**出现在 `detail_body` 里 —— 两条同时成立，
      才说明"这一段真的搬过去了"，而不是"我口头记得搬了"。
      ⚠️ 为什么要求"是被删原文的子串"：否则 `--sink-to "的"` 之类能轻易骗过它。
      ⚠️ 短语必须够长（`SINK_TO_MIN_LEN`）：短串在任何文档里都能匹配 ⇒ 等于没查。
      ⚠️ 阈值是**逐段**的（`SINK_VERIFY_MIN`）：**单段**不足阈值 ⇒ 换个说法，无需声明；
        **只要声明了就一定验证**（声明是承诺，承诺要兑现）。
      ⚠️ `--sink-inplace "<理由>"` = 明确声明"这批不是下沉：就地收紧 / 订正过期信息"。
        它**没有机械验证**（工具判断不了"压缩后内容还在不在"），所以**必须写理由**，
        而且会**大声打印** —— 它是一条**留痕的判断**，不是一个安静的口子。
    """
    if inplace is not None and not inplace.strip():
        return (
            "`--sink-inplace` **必须写理由**（空理由 = 一个安静的口子）。\n"
            '      例：`--sink-inplace "订正过期的『共 19 条』行 + 就地收紧几条长句"`'
        )

    # ★ 声明了就一定验证（无论这一段删了多少）—— 否则"声明"会退化成客套话
    if phrases:
        for p in phrases:
            if len(p.strip()) < SINK_TO_MIN_LEN:
                return (
                    f"--sink-to 短语太短（{len(p.strip())} < {SINK_TO_MIN_LEN}）：{p!r}\n"
                    "      ⇒ 短串在任何文档里都能匹配，等于没查。换一个**有代表性的短语**。"
                )
            if not any(p in t for t, _ in sunk_items):
                return (
                    f"--sink-to 短语**不是任何一段被删原文的子串**：{p!r}\n"
                    "      ⇒ 它没被删掉，就谈不上「下沉」。短语要从**被删原文里原样摘**。"
                )
            if p not in detail_body:
                return (
                    f"--sink-to 短语在被删原文里，但**不在** `{DETAIL.name}` 里：{p!r}\n"
                    "      ⇒ 这**不是下沉，是删除**（坑 79）。\n"
                    "      先把内容写进 detail，**再**回来 sink —— 顺序反了就没人回头补。"
                )
        if inplace is not None:
            say(f"  ⚖️ 另有就地处理：{inplace.strip()}（**无机械验证**，留痕）")
        return None

    offenders = [(t, d) for t, d in sunk_items if d >= SINK_VERIFY_MIN]
    if not offenders:
        if inplace is not None:
            say(
                "  ⚖️ 就地处理（**非下沉**，最长一段只删 "
                f"{max((d for _, d in sunk_items), default=0)} 字节）：{inplace.strip()}"
            )
        return None
    if inplace is not None:
        say(
            f"  ⚖️ 就地处理（**非下沉**，其中 {len(offenders)} 段 ≥{SINK_VERIFY_MIN} 字节）："
            f"{inplace.strip()} —— **无机械验证**，此声明被留痕"
        )
        return None
    big_t, big_d = max(offenders, key=lambda kv: kv[1])
    return (
        f"本次有 {len(offenders)} 段 `--sink` **单段删掉 ≥{SINK_VERIFY_MIN} 字节**"
        f"（最大一段 {big_d} 字节：{big_t[:48]!r}…）\n"
        "      ⇒ 这是「删掉了一段内容」，不是「换个说法」。必须二选一声明它去哪了：\n"
        '        · `--sink-to "<短语>"`：该短语**既**是被删原文的子串、**又**出现在 '
        f"`{DETAIL.name}` 里\n"
        '        · `--sink-inplace "<理由>"`：这批**不是下沉**（就地收紧 / 订正过期信息），留痕\n'
        "      判据：**只过「主文件变小」叫删除，两条都过才叫下沉**（坑 79）。"
    )


def self_test() -> int:
    """证明这些判据**自己会响**：正例 / 反例 / 边界各一条（硬约定 J）。"""
    base = (
        "AAA 这是一条足够长的已有行，长度超过判重阈值\n"
        "BBB 这是另一条足够长的已有行，也超过判重阈值\n"
        "---\n"
        "| a | b |\n"
    )
    cases: list[tuple[str, int, int]] = [
        (
            "1) 干净的新块 → 不该报",
            len(block_conflicts("CCC 一条全新的长行，文件里没有", existing_lines(base))),
            0,
        ),
        (
            "2) 新块里含锚点原文 → 必须报（我犯过的那个错）",
            len(
                block_conflicts(
                    "AAA 这是一条足够长的已有行，长度超过判重阈值", existing_lines(base)
                )
            ),
            1,
        ),
        (
            "3) 新块里含别处的已有行 → 必须报",
            len(
                block_conflicts(
                    "BBB 这是另一条足够长的已有行，也超过判重阈值", existing_lines(base)
                )
            ),
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

    # ★ 约束 4c（2026-09-28 补）：`--add` 的锚点**必须停在行边界**。
    #   正例 + 反例各一条 —— 反例就是当天真实踩到的那个形状。
    extra: list[tuple[str, bool, bool]] = [
        (
            "6) 锚点是整行（结尾即 \\n）→ 允许",
            add_after_line("- A：`cmd`\n下一行\n", "- A：`cmd`") == len("- A：`cmd`"),
            True,
        ),
        (
            "7) 锚点停在行中间 → 必须拒绝（会把整行劈开）",
            _raises(lambda: add_after_line("- A：`cmd` 后面还有字\n", "- A：`cmd`")),
            True,
        ),
    ]
    for name, got, want in extra:
        ok = got == want
        print(f"  [{'ok' if ok else 'FAIL'}] {name}（got={got}）")
        bad += 0 if ok else 1

    # ★ 约束 7（2026-09-29 补）：**「下沉」必须两个断言都过**（主文件变小 + 内容到了 detail）。
    #   反例里最关键的一条是"删了一大段却说不出它去哪了"—— 那正是上一批真实踩到的形状。
    #   用例 8 还抓出我自己写错的测试数据：短语必须是**被删原文的子串**，我第一版写
    #   `「守卫分两种」` 而原文是 `「守卫分两种，别混」` ⇒ 它按设计拒了我。
    fake_detail = "…这里记着「守卫分两种，别混」的完整判据…\n另一句只出现在 detail 里的话。\n"
    sunk_text = "### 「守卫分两种，别混」\n- 覆盖率只说有没有跑到；契约守卫才拦回归。\n" * 4
    sink_cases: list[tuple[str, bool, bool]] = [
        (
            "8) 单段删 900 + 声明了去处（短语在原文里、也在 detail 里）→ 通过",
            verify_sink_destinations(
                [(sunk_text, 900)], ["「守卫分两种，别混」"], None, fake_detail
            )
            is None,
            True,
        ),
        (
            "9) 单段删 900 + **没声明** → 拒绝（上一批踩到的形状）",
            verify_sink_destinations([(sunk_text, 900)], [], None, fake_detail) is not None,
            True,
        ),
        (
            "10) 声明了但短语**不在 detail** → 拒绝（= 删除，不是下沉）",
            verify_sink_destinations(
                [(sunk_text, 900)], ["「守卫分两种，别混」"], None, "无关正文\n"
            )
            is not None,
            True,
        ),
        (
            "11) 声明了但短语**不是被删原文的子串** → 拒绝（否则挑个无关短语就能骗过它）",
            verify_sink_destinations(
                [(sunk_text, 900)], ["另一句只出现在 detail 里的话"], None, fake_detail
            )
            is not None,
            True,
        ),
        (
            "12) 单段删 100（< 阈值 200）→ 换个说法，无需声明",
            verify_sink_destinations([(sunk_text, 100)], [], None, fake_detail) is None,
            True,
        ),
        (
            "13) 声明 --sink-inplace + 写了理由 → 通过（**无机械验证**，只留痕）",
            verify_sink_destinations([(sunk_text, 900)], [], "订正过期行", fake_detail) is None,
            True,
        ),
        (
            "14) 短语太短 → 拒绝（短串等于没查）",
            verify_sink_destinations([(sunk_text, 900)], ["守卫"], None, fake_detail) is not None,
            True,
        ),
        (
            "15) --sink-inplace 但**理由为空** → 拒绝（空开关 = 安静的口子）",
            verify_sink_destinations([(sunk_text, 900)], [], "", fake_detail) is not None,
            True,
        ),
        (
            "16) ★ 逐段判据：一批里最大的一段只删 100 → 不触发",
            verify_sink_destinations(
                [(sunk_text, 100), (sunk_text, 90), (sunk_text, 80)], [], None, fake_detail
            )
            is None,
            True,
        ),
        (
            "17) ★ 逐段判据：同批里只要有一段删 900 → 触发",
            verify_sink_destinations(
                [(sunk_text, 900), (sunk_text, 30), (sunk_text, 20)], [], None, fake_detail
            )
            is not None,
            True,
        ),
        (
            "18) ★ 声明在小段上也必须被验证（声明是承诺，不能'小段就算了'）",
            verify_sink_destinations([(sunk_text, 50)], ["「守卫分两种，别混」"], None, "无关\n")
            is not None,
            True,
        ),
    ]
    for name, got, want in sink_cases:
        ok = got == want
        print(f"  [{'ok' if ok else 'FAIL'}] {name}（got={got}）")
        bad += 0 if ok else 1

    print(f"[mem-update --self-test] {'ALL PASSED' if bad == 0 else f'{bad} CHECK(S) FAILED'}")
    return 0 if bad == 0 else 1


def _raises(fn: Callable[[], object]) -> bool:
    try:
        fn()
    except Exception:  # noqa: BLE001 —— 自检只关心"有没有响"，不关心哪种异常
        return True
    return False


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


def do_end(budget: int) -> int:
    """收口本批：断言净增 ≤ 预算，越线时从备份恢复。

    ⚠️ 只能在**变更之后**调用 —— 见 `main()` 里那段注释（`--sink X --end` 曾经静默不生效）。
    """
    st = load_state()
    if not st:
        say("✗ 没有进行中的批（先做一次 --sink/--add）")
        return 2
    end = size_of(MEM)
    growth = end - st["start_bytes"]
    allowed = budget if budget != 0 else 0
    ok = growth <= allowed and end <= TRUNCATE_AT
    say(f"批收口：start={st['start_bytes']} end={end} 净增={growth} 允许={allowed}")
    say(f"  下沉 {st['sunk']} / 新增 {st['added']} 字节（{st['calls']} 次调用）")
    if end > TRUNCATE_AT:
        say(f"✗ 越过注入截断点 {TRUNCATE_AT}！—— 立即从 {BACKUP.name} 恢复")
        if BACKUP.exists():
            shutil.copy2(BACKUP, MEM)
        # trash-ok: 单文件（批状态 json），非递归
        STATE.unlink(missing_ok=True)
        return 1
    if not ok:
        say("✗ 净增超过预算 —— 减法没做到位。**本批不通过**（内容保留，便于你重做减法）")
        return 1
    # trash-ok: 单文件（批状态 json），非递归
    STATE.unlink(missing_ok=True)
    # trash-ok: 单文件（批备份 bak），非递归
    BACKUP.unlink(missing_ok=True)
    say(f"✓ 通过。距截断点余量 = {TRUNCATE_AT - end}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,  # ★ `--help` 直接打印上面那份**唯一**的说明（含「能力边界」）
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--sink", action="append", default=[], metavar="OLD=>NEW")
    ap.add_argument(
        "--sink-to",
        action="append",
        default=[],
        metavar="PHRASE",
        help="声明被删内容去了 detail 的哪一句（须**既**是被删原文的子串、**又**在 detail 里）",
    )
    ap.add_argument(
        "--sink-inplace",
        default=None,
        metavar="REASON",
        help="声明本批减法**不是下沉**（就地收紧 / 订正过期信息）+ 写清理由 —— **无机械验证**，留痕",
    )
    ap.add_argument("--add", action="append", default=[], metavar="ANCHOR=>TEXT")
    ap.add_argument("--budget", type=int, default=0, help="批内允许的净增字节（默认 0）")
    ap.add_argument("--end", action="store_true", help="收口本批：断言 + 清理")
    ap.add_argument("--status", action="store_true", help="只报数")
    ap.add_argument(
        "--measure",
        action="store_true",
        help="逐对打印**字节差**（别估、量）—— 与 --dry-run 一样不写文件",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="预演：一次报出**每个**锚点匹配几次（不写文件）—— 锚点写错时不必一轮一轮试",
    )
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
        # trash-ok: 单文件（批状态 json），非递归
        STATE.unlink(missing_ok=True)
        # trash-ok: 单文件（批备份 bak），非递归
        BACKUP.unlink(missing_ok=True)
        say("已放弃本批（内容保留、状态清理）")
        return 0

    # ---------- 收口 ----------
    # ⚠️ 2026-09-26 实测的一个**真陷阱**：这段原来排在「变更」**之前** ⇒ `--sink X --end`
    #    这样**一次调用**里，`--end` 先跑、发现"没有进行中的批"就 `return 2`，
    #    **sink 从来没有执行** —— 而我只看到一句"没有进行中的批"，极易误判成别的问题
    #    （文件其实一个字都没改）。
    #    ⇒ 规则：**有 --sink / --add 时先应用、再收口**；只有"光 --end"时才单独收口。
    if args.end and not (args.sink or args.add):
        return do_end(args.budget)

    # ---------- 变更 ----------
    if not args.sink and not args.add:
        say("✗ 什么都没要求做（--sink / --add / --status / --end 至少一个）")
        return 2

    if args.add and not args.sink:
        say("✗ 有 --add 但一个 --sink 都没有 —— 拒绝「只加不减」（硬约定 Q 的机械形式）")
        return 1

    # ---------- 预演（只校验、不写） ----------
    # ⚠️ 为什么需要它：锚点写错时脚本**只报第一个**就整批回滚，于是"到底哪几个锚点不对"
    #    要一轮一轮试（2026-09-26 实测浪费了 5 轮往返）。⇒ 预演**一次把全部锚点的匹配数报出来**，
    #    并且把"新块里有重复行"这类问题也一并列出。
    if args.dry_run or args.measure:
        probe = MEM.read_bytes().decode("utf-8")
        bad = 0
        sunk_old: list[tuple[str, int]] = []
        net_removed = 0
        for spec in args.sink:
            old, new = parse_pair(spec, "--sink")
            n = probe.count(old)
            delta = len(old.encode()) - len(new.encode())
            say(f"  [{'ok' if n == 1 else 'BAD'}] --sink 匹配 {n} 次：{old[:52]!r}")
            if args.measure:
                say(f"        Δ {delta:+d} 字节（{len(old.encode())} → {len(new.encode())}）")
            bad += 0 if n == 1 else 1
            if n == 1:
                probe = probe.replace(old, new, 1)
                sunk_old.append((old, max(0, delta)))
                net_removed += max(0, delta)
        for spec in args.add:
            anchor, block = parse_pair(spec, "--add")
            dup = block_conflicts(block, existing_lines(probe))
            problems = []
            if probe.count(anchor) != 1:
                problems.append(f"锚点匹配 {probe.count(anchor)} 次")
            try:
                pos = add_after_line(probe, anchor)
            except ValueError:
                pos = None
                problems.append("锚点没停在**行边界**（会把这行劈开）")
            if dup:
                problems.append("新块有重复行")
            say(
                f"  [{'ok' if not problems else 'BAD'}] --add："
                f"{'、'.join(problems) if problems else 'ok'}（锚点：{anchor[:44]!r}）"
            )
            bad += 1 if problems else 0
            if args.measure and pos is not None and not problems:
                say(f"        Δ {len(block.encode()):+d} 字节（新增）")
            if pos is not None and not problems:
                probe = probe[:pos] + "\n" + block + probe[pos:]
        why = verify_sink_destinations(sunk_old, args.sink_to, args.sink_inplace, _detail_body())
        if why:
            say(f"  [BAD] 下沉去处未声明/未验证（净删 {net_removed} 字节）—— 真跑时会被拒绝+回滚：")
            for ln in why.splitlines():
                say("      " + ln)
            bad += 1
        elif args.sink:
            say(
                f"  [ok] 下沉去处：净删 {net_removed} 字节"
                + (
                    f"（就地处理：{args.sink_inplace.strip()}）"
                    if args.sink_inplace is not None
                    else f"，声明了 {len(args.sink_to)} 个短语"
                )
            )
        print(
            f"[mem] 预演结束：{bad} 条 BAD（**文件未改**）；全部 ok 时正文将变为 "
            f"{len(probe.encode('utf-8'))} 字节",
            flush=True,
        )
        return 1 if bad else 0

    st = load_state() or open_batch()
    original = MEM.read_bytes()
    text = original.decode("utf-8")
    before = len(original)
    sunk = added = 0

    try:
        sunk_old: list[tuple[str, int]] = []
        net_removed = 0
        for spec in args.sink:
            old, new = parse_pair(spec, "--sink")
            if text.count(old) != 1:
                raise SystemExit(
                    f"--sink 锚点匹配 {text.count(old)} 次（要求恰好 1 次）：{old[:60]!r}"
                )
            text = text.replace(old, new, 1)
            delta = len(old.encode()) - len(new.encode())
            sunk += delta
            sunk_old.append((old, max(0, delta)))
            net_removed += max(0, delta)
            say(f"  下沉 {delta:+d} 字节：{old[:48]!r}…")

        # ★ 硬约束 7：净删够多 ⇒ **必须**声明它去了哪（否则拒绝 + 回滚）。见 verify_sink_destinations。
        why = verify_sink_destinations(sunk_old, args.sink_to, args.sink_inplace, _detail_body())
        if why:
            raise SystemExit(why)

        for spec in args.add:
            anchor, block = parse_pair(spec, "--add")
            block = unescape(block)
            # ★ 约束 4c：锚点**必须停在行边界**（否则会把这一行劈成两半）—— 2026-09-28 实测踩到
            try:
                pos = add_after_line(text, anchor)
            except ValueError as e:
                raise SystemExit(str(e)) from None
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
            text = text[:pos] + "\n" + block + text[pos:]
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
    if args.end:
        return do_end(args.budget)
    return 0


if __name__ == "__main__":
    sys.exit(main())
