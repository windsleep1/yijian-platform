# -*- coding: utf-8 -*-
"""批量导入前的雪花 ID 体检探针 —— Batch 5 Pass 0。

纯本地跑，不需要服务、不需要数据库：

    python tools/local-verify/probe-snowflake-batch.py

## 为什么单独写这个探针

Batch 4 的坑 #21 只证明了「单个 ID 过 JS `Number()` 会失真」。
Batch 5 要一次生成几千个 ID，于是出现一个新问题：

    同一毫秒内连出的多个 ID，到底是不是**不同的**？

这个探针把两件事**分开**测，避免把两种不同性质的 bug 混为一谈：

- **A. 生成器本身**（Python `int`）会不会重复？—— 这是唯一性，数据库里存的就是它。
- **B. 这些 ID 过 `float64` 之后**会不会塌缩？—— 这是传输层，与生成器无关。

结论必须先分清 A 和 B，否则会去"修"一个没坏的东西，而放过真正坏的地方。

## 断言

1. 连出 6000 个（= 种子题库规模）ID，`len(set(ids)) == 6000` —— A 必须成立。
2. 同一毫秒内 seq 取满 4096 个后**换毫秒**，不重复使用任何 seq —— A 必须成立。
3. 展示 B 的塌缩程度（同一毫秒的 ID 过 float64 后剩多少 distinct）——
   这是**提示性**输出，不是失败条件，用来解释"为什么 ID 必须走字符串"。

退出码非 0 表示 A 类断言失败。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                "apps", "api"))

from app.core.idgen import (  # noqa: E402
    MAX_SEQUENCE,
    SEQUENCE_BITS,
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


class FakeClock:
    """可控时钟：sleep 会真的推进毫秒，用来逼出「同毫秒 seq 取满」的边界。"""

    def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
        self._ms = start_ms

    def time(self) -> float:  # noqa: A003
        return self._ms / 1000.0

    def sleep(self, seconds: float) -> None:
        self._ms += max(1, int(seconds * 1000))


def main() -> int:
    print("== A. 生成器唯一性（数据库里存的就是这些 int）==")

    gen = SnowflakeGenerator(worker_id=1)
    ids = [gen.next_id() for _ in range(6000)]
    distinct = len(set(ids))
    print(f"  连续生成 6000 个：distinct = {distinct}")
    check("6000 个 ID 互不重复", distinct == 6000, f"distinct={distinct}")

    # 单调递增也是唯一性的一个强佐证（且对分页/游标友好）
    check("严格单调递增", all(b > a for a, b in zip(ids, ids[1:])))

    print()
    print("== A2. 同一毫秒把 seq 取满（4096）后的边界 ==")

    # 冻结时钟在同一毫秒：生成 MAX_SEQUENCE+1 个。
    # 正确实现会：前 4096 个用 seq=0..4095，第 4097 个必须换毫秒，而不是回绕复用 seq。
    import app.core.idgen as idgen_mod  # noqa: E402

    clock = FakeClock()
    real_time = idgen_mod.time
    idgen_mod.time = clock  # type: ignore[assignment]
    try:
        frozen = SnowflakeGenerator(worker_id=1)
        burst = [frozen.next_id() for _ in range(MAX_SEQUENCE + 5)]
    finally:
        idgen_mod.time = real_time  # type: ignore[assignment]

    capacity = MAX_SEQUENCE + 1  # 一个毫秒能容纳 4096 个（seq=0..4095）
    seqs = [i & MAX_SEQUENCE for i in burst]
    tss = [i >> (SEQUENCE_BITS + 10) for i in burst]
    print(f"  冻结同一毫秒，连取 {len(burst)} 个："
          f"distinct = {len(set(burst))}，跨 {len(set(tss))} 个毫秒")
    check("取满一个毫秒的容量后不重复", len(set(burst)) == len(burst))
    check(f"前 {capacity} 个吃满 seq=0..{MAX_SEQUENCE}，第 {capacity + 1} 个换毫秒且 seq 归零",
          seqs[:capacity] == list(range(capacity)) and seqs[capacity] == 0)

    print()
    print("== B. 同样这些 ID 过 float64 会怎样（提示性，不是失败条件）==")

    via_float = len({float(i) for i in ids})
    print(f"  6000 个 ID -> float64 后只剩 {via_float} 个不同值"
          f"（塌缩率 {100 * (1 - via_float / 6000):.1f}%）")

    base = (ids[0] >> (SEQUENCE_BITS + 10)) << (SEQUENCE_BITS + 10)
    base |= (1 << WORKER_SHIFT)
    in_one_ms = len({float(base + s) for s in range(MAX_SEQUENCE)})
    print(f"  同一毫秒内 {MAX_SEQUENCE} 个 ID -> float64 后只剩 {in_one_ms} 个"
          f" -> 说明 B 类问题**只在传输层**，与生成器无关")

    if hasattr(idgen_mod, "next_ids"):
        print()
        print("== C. 批量预分配接口 next_ids() ==")
        gen2 = SnowflakeGenerator(worker_id=1)
        batch = gen2.next_ids(6000)
        check("next_ids(6000) 互不重复", len(set(batch)) == 6000,
              f"distinct={len(set(batch))}")
    else:
        print()
        print("== C. next_ids() 尚未实现（Pass 0 待补）==")

    print()
    if FAILED:
        print(f"结果：{len(FAILED)} 项失败 -> {FAILED}")
        return 1
    print("结果：A 类（唯一性）全部通过 ✅")
    print("       B 类是传输层问题：ID 必须全程以**字符串**传递，禁止 Number()/float()。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
