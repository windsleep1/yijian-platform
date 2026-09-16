"""
雪花 ID 生成器。

64 位布局：1 位符号 + 41 位毫秒时间戳 + 10 位机器号 + 12 位序列
    | 1 |   41   |  10  |    12    |
可容纳：约 69 年时间跨度、1024 台机器、单机每毫秒 4096 个 ID。

为什么不用数据库自增：将来分库或数据迁移时主键不必重排；
批量导入时可以预先在应用层分配 ID。
"""

from __future__ import annotations

import threading
import time

EPOCH = 1_700_000_000_000          # 自定义纪元 2023-11-15，延长可用年限
WORKER_BITS = 10
SEQUENCE_BITS = 12
MAX_WORKER_ID = (1 << WORKER_BITS) - 1
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1
WORKER_SHIFT = SEQUENCE_BITS
TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_BITS


class SnowflakeGenerator:
    def __init__(self, worker_id: int = 1) -> None:
        if not 0 <= worker_id <= MAX_WORKER_ID:
            raise ValueError(f"worker_id 必须在 0~{MAX_WORKER_ID} 之间")
        self._worker_id = worker_id
        self._sequence = 0
        self._last_ts = -1
        self._lock = threading.Lock()

    def _wait_next_ms(self, last_ts: int) -> int:
        ts = int(time.time() * 1000)
        while ts <= last_ts:
            time.sleep(0.0005)
            ts = int(time.time() * 1000)
        return ts

    def next_id(self) -> int:
        with self._lock:
            ts = int(time.time() * 1000)
            if ts < self._last_ts:
                # 时钟回拨：等待追平，避免生成重复 ID
                ts = self._wait_next_ms(self._last_ts)
            if ts == self._last_ts:
                self._sequence = (self._sequence + 1) & MAX_SEQUENCE
                if self._sequence == 0:
                    ts = self._wait_next_ms(self._last_ts)
            else:
                self._sequence = 0
            self._last_ts = ts

            return ((ts - EPOCH) << TIMESTAMP_SHIFT) | (self._worker_id << WORKER_SHIFT) | self._sequence


_generator: SnowflakeGenerator | None = None


def get_id_generator() -> SnowflakeGenerator:
    global _generator
    if _generator is None:
        from app.core.config import settings

        _generator = SnowflakeGenerator(settings.snowflake_worker_id)
    return _generator


def next_id() -> int:
    return get_id_generator().next_id()


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
