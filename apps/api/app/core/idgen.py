"""
雪花 ID 生成器。

64 位布局：1 位符号 + 41 位毫秒时间戳 + 10 位机器号 + 12 位序列
    | 1 |   41   |  10  |    12    |
可容纳：约 69 年时间跨度、1024 台机器、单机每毫秒 4096 个 ID。

为什么不用数据库自增：将来分库或数据迁移时主键不必重排；
批量导入时可以预先在应用层分配 ID。

=====================================================================
Batch 5 Pass 0：两个必须讲清的不变量（这是坑 #21 的"另一半"）
=====================================================================

本模块产生的是 **64 位整数**，Python 侧永远精确，写进 PostgreSQL 的 BIGINT 也精确。
但同一批 ID 一旦经过 JS 的 `Number()` 或 Python 的 `float()`，就会塌缩 —— 因为

    |ID| ≈ 3.7e17，落在 2^58~2^59 之间，float64 的 ULP = 2^(58-52) = 64

浮点只能表示该量级下 64 的倍数。而本布局里低 22 位 = `worker<<12 | seq`，
同一毫秒内 seq=0..4095 会产生 4096 个只差几位的 ID，过 float64 后
**只剩下约 64 个不同的值**（实测 6000 个 ID → 99 个值，塌缩 98.4%）。

于是有两条铁律，别再踩：

    不变量 A（本模块负责）：同一次运行内，任意两个 `next_id()` 的结果必须不同。
        同一毫秒 seq 用满 4096 个后，**换毫秒**重新从 0 开始，绝不回绕复用 seq。

    不变量 B（调用方负责）：ID 跨进程/跨语言传递时**必须走字符串**。
        禁止 `Number()` / `float()` / `JSON.parse` 后当数字用。
        本项目统一用 `BigIntStr`（见 `app/schemas/types.py`）序列化。

`is_float_safe()` / `assert_float_safe()` / `float_loss_report()` 是给不变量 B 用的
显式守卫：想知道"这批 ID 会不会在传输层出事"，调它们就行。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

EPOCH = 1_700_000_000_000          # 自定义纪元 2023-11-15，延长可用年限
WORKER_BITS = 10
SEQUENCE_BITS = 12
MAX_WORKER_ID = (1 << WORKER_BITS) - 1
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1
WORKER_SHIFT = SEQUENCE_BITS
TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_BITS

# 一个毫秒能容纳的 ID 数（seq 0..MAX_SEQUENCE）
SEQUENCE_CAPACITY = MAX_SEQUENCE + 1

# JS `Number.MAX_SAFE_INTEGER` / IEEE754 双精度可精确表示的最大整数：2^53-1
MAX_SAFE_FLOAT_INT = (1 << 53) - 1


# ============================================================ 传输层守卫（不变量 B）


def is_float_safe(value: int) -> bool:
    """该整数过一遍 float64 再回来，是否还是它自己。

    `float(v) == v` 是唯一可靠的判据 —— 不要用 `abs(v) <= 2**53` 之类的近似，
    2^53 附近还有能精确表示的偶数。
    """
    try:
        return float(value) == value
    except (OverflowError, TypeError):
        return False


def assert_float_safe(value: int, *, what: str = "id") -> None:
    """断言一个值可以安全地以 JSON number 传输。本项目的雪花 ID **一定**过不了这条。

    它的用途不是"检查生成器"，而是卡住"想把 ID 塞进 JSON number"的调用方 ——
    失败即说明这里必须改用字符串。
    """
    if not is_float_safe(value):
        raise ValueError(
            f"{what}={value} 超出 float64 可精确表示范围（>2^53-1），"
            f"以 JSON number 传输会被静默舍入成 {int(float(value))}。"
            f"请改为字符串传递（本项目统一用 BigIntStr）。"
        )


def float_loss_report(values: list[int]) -> dict[str, object]:
    """诊断用：一批 ID 过 float64 之后塌缩到什么程度。

    返回 `{total, distinct_after_float, collapsed_into, max_group, samples}`。
    `max_group` 越大，说明"多少个不同的 ID 被压成了同一个值"。
    """
    groups: dict[int, list[int]] = {}
    for v in values:
        groups.setdefault(int(float(v)), []).append(v)
    worst = max(groups.values(), key=len) if groups else []
    return {
        "total": len(values),
        "distinct_after_float": len(groups),
        "collapsed_into": len(groups),
        "max_group": len(worst),
        "samples": worst[:5],
    }


def decode(snowflake_id: int) -> dict[str, object]:
    """把一个雪花 ID 拆回 (timestamp, worker_id, sequence)，排障时用。"""
    ts = (snowflake_id >> TIMESTAMP_SHIFT) + EPOCH
    worker = (snowflake_id >> WORKER_SHIFT) & MAX_WORKER_ID
    seq = snowflake_id & MAX_SEQUENCE
    return {
        "id": snowflake_id,
        "timestamp_ms": ts,
        "worker_id": worker,
        "sequence": seq,
        "generated_at": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
        "float_safe": is_float_safe(snowflake_id),
    }


# ============================================================ 生成器（不变量 A）


class SnowflakeGenerator:
    """线程安全的雪花 ID 生成器。

    `worker_id` 必须在**部署维度上唯一**（多进程 / 多副本 / 多容器各配一个）：
    两个进程用同一个 worker_id 会在同一毫秒发出完全相同的 ID。
    单进程内由 `_lock` 串行化，不需要额外加锁。
    """

    def __init__(self, worker_id: int = 1) -> None:
        if not 0 <= worker_id <= MAX_WORKER_ID:
            raise ValueError(f"worker_id 必须在 0~{MAX_WORKER_ID} 之间")
        self._worker_id = worker_id
        self._sequence = 0
        self._last_ts = -1
        self._lock = threading.Lock()

    # -- 时间 ---------------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _wait_next_ms(last_ts: int) -> int:
        """自旋到**严格大于** last_ts 的毫秒。时钟回拨也走这里。"""
        ts = SnowflakeGenerator._now_ms()
        while ts <= last_ts:
            time.sleep(0.0005)
            ts = SnowflakeGenerator._now_ms()
        return ts

    # -- 组装 ---------------------------------------------------------

    def _compose(self, ts: int, sequence: int) -> int:
        return ((ts - EPOCH) << TIMESTAMP_SHIFT) | (self._worker_id << WORKER_SHIFT) | sequence

    # -- 分配 ---------------------------------------------------------

    def next_id(self) -> int:
        """取一个 ID。

        seq 的分配规则（不变量 A 的核心）：

        - 进入新的毫秒 → `seq = 0`
        - 同一毫秒内再来 → `seq += 1`
        - 同一毫秒内 seq 已经取到 `MAX_SEQUENCE` → **换到下一个毫秒**，seq 归零

        最后一条是关键：**绝不在同一毫秒把 seq 回绕成 0 复用**。
        旧写法 `(seq + 1) & MAX_SEQUENCE` 之后才判断是否换毫秒，虽然也换，
        但"先回绕、后换毫秒"的写法把正确性藏在赋值顺序里，极易被后人改坏。
        这里改成"先判断容量、再决定是否换毫秒"，正确性是显式的。
        """
        with self._lock:
            ts = self._now_ms()

            # 时钟回拨：宁可等，不可重发（等不到就继续等，绝不退化成重复 ID）
            if ts < self._last_ts:
                ts = self._wait_next_ms(self._last_ts)

            if ts == self._last_ts:
                if self._sequence >= MAX_SEQUENCE:
                    # 本毫秒容量耗尽：换毫秒，seq 从 0 重来（不是回绕）
                    ts = self._wait_next_ms(self._last_ts)
                    self._sequence = 0
                else:
                    self._sequence += 1
            else:
                self._sequence = 0

            self._last_ts = ts
            return self._compose(ts, self._sequence)

    def next_ids(self, count: int) -> list[int]:
        """批量预分配 `count` 个 ID —— 导入管道专用。

        除了逐个 `next_id()`，还内置一次**唯一性断言**：
        批量导入一旦拿到重复 ID，写库时会表现为"覆盖"而不是报错，
        属于最难查的一类数据事故。所以这里宁愿直接抛错。
        """
        if count < 0:
            raise ValueError("count 不能为负数")
        if count == 0:
            return []
        out = [self.next_id() for _ in range(count)]
        if len(set(out)) != count:
            raise AssertionError(
                f"雪花 ID 生成器产生重复：请求 {count} 个，去重后只剩 {len(set(out))} 个"
            )
        return out


_generator: SnowflakeGenerator | None = None
_generator_lock = threading.Lock()


def get_id_generator() -> SnowflakeGenerator:
    global _generator
    if _generator is None:
        with _generator_lock:
            if _generator is None:
                from app.core.config import settings

                _generator = SnowflakeGenerator(settings.snowflake_worker_id)
    return _generator


def next_id() -> int:
    return get_id_generator().next_id()


def next_ids(count: int) -> list[int]:
    """批量预分配（导入管道用）。"""
    return get_id_generator().next_ids(count)


def to_inet(value: str | None):
    """把字符串 IP 转成 asyncpg 能写入 INET 列的对象；非法则返回 None。"""
    import ipaddress

    if not value:
        return None
    candidate = value.split(",")[0].strip()
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


__all__ = [
    "EPOCH",
    "WORKER_BITS",
    "SEQUENCE_BITS",
    "SEQUENCE_CAPACITY",
    "MAX_WORKER_ID",
    "MAX_SEQUENCE",
    "WORKER_SHIFT",
    "TIMESTAMP_SHIFT",
    "MAX_SAFE_FLOAT_INT",
    "SnowflakeGenerator",
    "get_id_generator",
    "next_id",
    "next_ids",
    "to_inet",
    "is_float_safe",
    "assert_float_safe",
    "float_loss_report",
    "decode",
]
