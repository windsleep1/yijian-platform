# -*- coding: utf-8 -*-
"""复现并证明「雪花 ID 过 JS Number() 就失真」——Batch 4 坑 #21 的回归探针。

不需要启动服务，纯本地跑：

    python tools/local-verify/probe-id-precision.py

## 背景

前端批量删除曾写成 `ids: selected.map(Number)`。雪花 ID 是 18~19 位十进制，
超过 JS 安全整数上限（2^53-1 = 9007199254740991，16 位），`Number()` 会把它
舍入到最近的 float64。后端按舍入后的 ID 查不到行，`batch-delete` 于是返回
`code=0, deleted=0, skipped=[...]` —— HTTP 200、业务码 0、**不抛错**，
前端 toast 显示「已删除 0 道题」，操作者以为成功，其实一条都没动。

## 为什么这个坑特别难发现

它不是"必然崩"，而是**取值相关**：

本项目的 ID 布局是 `ts << 22 | worker << 12 | seq`（见 apps/api/app/core/idgen.py），
`worker_id` 默认 1，所以：

    低 22 位 = (1 << 12) | seq = 4096 | seq

当前 ID 量级约 3.7e17，落在 2^58~2^59 之间，float64 的 ULP = 2^(58-52) = **64**。
而 4096 正好是 64 的倍数，于是：

    seq = 0        -> 4096       -> mod 64 == 0  -> 恰好无损
    seq = 1 .. 63  -> 4097..4159 -> mod 64 == 1..63 -> 失真
    seq = 64       -> 4160       -> mod 64 == 0  -> 又无损

后台手点新建时，每次一个 HTTP 往返，几乎总是落在不同毫秒的 seq=0 —— 所以
按"一道一道手动建"的用法测，永远不会发现问题。一旦走批量导入 / 脚本刷数据 /
并发新建，同一毫秒连出多个 ID，seq 从 1 开始，立刻翻车。

更糟的是：同一毫秒内那 64 个不同的 ID，经 `Number()` 后**塌缩到同一批值**，
等于把 64 道不同的题指向同一个"不存在的 ID"。

## 本探针断言什么

1. seq=0 的 ID 无损；seq=1..63 的 ID 失真。
2. 同一毫秒内 64 个连续 ID，经 `Number()` 后剩下的不同值远少于 64。
3. 失真后的值确实不等于原值（即后端一定查不到）。

退出码非 0 表示断言失败。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "apps", "api"))

from app.core.idgen import (  # noqa: E402
    EPOCH,
    TIMESTAMP_SHIFT,
    WORKER_SHIFT,
    SnowflakeGenerator,
)

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name} {detail}")


def js_number(text: str) -> int:
    """模拟 JS 的 Number(str)：先按 float64 解析，再取整。"""
    return int(float(text))


def main() -> int:
    print("== 1. 同一毫秒内连续生成 ID，看 seq 的影响 ==")
    gen = SnowflakeGenerator(worker_id=1)
    ids = [gen.next_id() for _ in range(8)]

    print(f"  {'#':>2} {'ID':>20} {'seq':>4} {'mod64':>6} {'Number() 之后':>20}  失真")
    lossy = 0
    for n, i in enumerate(ids, 1):
        seq = i & ((1 << 12) - 1)
        rounded = js_number(str(i))
        bad = rounded != i
        lossy += bad
        print(f"  {n:>2} {str(i):>20} {seq:>4} {i % 64:>6} {str(rounded):>20}"
              f"  {'是' if bad else '否'}")

    first_seq = ids[0] & ((1 << 12) - 1)
    check("第 1 个 ID 落在 seq=0（每毫秒首个）", first_seq == 0, f"seq={first_seq}")
    check("seq=0 的 ID 无损", js_number(str(ids[0])) == ids[0])
    check("后续同一毫秒的 ID 失真", lossy >= 1, f"失真 {lossy} 个")
    check("seq=1 的 ID 一定失真", js_number(str(ids[1])) != ids[1])

    print()
    print("== 2. 同一毫秒 64 个 ID 经 Number() 后塌缩 ==")
    base = (ids[0] >> TIMESTAMP_SHIFT) << TIMESTAMP_SHIFT  # 抹掉 seq/worker 位
    base |= (1 << WORKER_SHIFT)                            # worker_id = 1
    collapsed = {js_number(str(base + s)) for s in range(64)}
    print(f"  64 个不同的 ID -> Number() 之后只剩 {len(collapsed)} 个不同的值")
    check("塌缩（不同值远少于 64）", len(collapsed) < 64, f"size={len(collapsed)}")
    check("塌缩后至少还剩 1 个值", len(collapsed) >= 1)

    print()
    print("== 3. 安全整数边界与 ULP ==")
    max_safe = 2 ** 53 - 1
    print(f"  JS 安全整数上限 = {max_safe}（{len(str(max_safe))} 位）")
    print(f"  当前雪花 ID 量级 ≈ {base}（{len(str(base))} 位）")
    check("雪花 ID 已越过安全整数上限", base > max_safe)

    # 量级落在 2^58~2^59，ULP = 2^(58-52) = 64：
    # +1 加不动（被舍回原值），+64 才能前进 1 个可表示单位。
    ulp_probe = float(2 ** 58)
    check("该量级下 +1 加不动（粒度 > 1）", float(2 ** 58 + 1) == ulp_probe,
          f"{float(2 ** 58 + 1)!r}")
    check("该量级下 ULP == 64", float(2 ** 58 + 64) - ulp_probe == 64,
          f"diff={float(2 ** 58 + 64) - ulp_probe}")

    print()
    print(f"  布局: EPOCH={EPOCH}  TIMESTAMP_SHIFT={TIMESTAMP_SHIFT}  "
          f"WORKER_SHIFT={WORKER_SHIFT}")

    print()
    if FAILED:
        print(f"结果：{len(FAILED)} 项失败 -> {FAILED}")
        return 1
    print("结果：全部通过 ✅  （结论：ID 必须全程以字符串传递，禁止 Number()）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
