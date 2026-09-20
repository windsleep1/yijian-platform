"""雪花 ID 生成器守卫测试 —— Batch 5 Pass 0。

这些是**纯单元测试**：不连数据库、不连 API，所以随时可跑：

    cd apps/api && pytest tests/test_idgen.py -v

## 为什么值得单独一个文件

坑 #21 的原始版本只证明了"ID 过 JS Number() 会失真"。但那个结论有一个危险的
误读空间：会让人以为"是生成器坏了，去修生成器"。实际不是 ——

- 生成器（Python int）一直是对的：6000 个 ID 互不重复、严格递增。
- 坏的是**传输层**：同一毫秒的 4096 个 ID 过 float64 后只剩约 64 个值。

所以这里的测试刻意分成两组：

- `TestUniqueness`  —— 冻结生成器的不变量 A（唯一性），包括"同毫秒 seq 取满"的边界。
- `TestTransportGuard` —— 冻结不变量 B（ID 必须走字符串），并**量化**塌缩，
  任何人想"顺手把 ID 转成数字"时，这两组测试都会立刻说话。
"""

from __future__ import annotations

import time

import pytest

import app.core.idgen as idgen
from app.core.idgen import (
    EPOCH,
    MAX_SAFE_FLOAT_INT,
    MAX_SEQUENCE,
    SEQUENCE_CAPACITY,
    SnowflakeGenerator,
    assert_float_safe,
    decode,
    float_loss_report,
    is_float_safe,
    next_ids,
)


# =====================================================================
# 不变量 A：唯一性
# =====================================================================


class TestUniqueness:
    def test_连续生成5000个不重复(self) -> None:
        """验收要求：连续生成 5000 个 ID，len(set(ids)) == 5000。"""
        gen = SnowflakeGenerator(worker_id=1)
        ids = [gen.next_id() for _ in range(5000)]
        assert len(set(ids)) == 5000, f"出现重复：{len(set(ids))}/5000"

    def test_高频连续生成也不重复(self) -> None:
        """验收要求：模拟高频调用（time.sleep(0) 连续生成）不重复。

        12000 个约跨 3 个毫秒（每毫秒 4096 容量），必然触发"同毫秒 seq>0"
        与"seq 取满换毫秒"两条路径 —— 正是批量导入会走的路径。
        """
        gen = SnowflakeGenerator(worker_id=1)
        ids: list[int] = []
        for i in range(12000):
            ids.append(gen.next_id())
            if i % 100 == 0:
                time.sleep(0)  # 让出时间片，模拟真实调度抖动
        assert len(set(ids)) == 12000, f"出现重复：{len(set(ids))}/12000"

    def test_批量预分配6000个不重复(self) -> None:
        """导入管道用的 next_ids()：等于种子题库规模。"""
        gen = SnowflakeGenerator(worker_id=1)
        ids = gen.next_ids(6000)
        assert len(set(ids)) == 6000
        assert gen.next_ids(0) == []

    def test_严格单调递增(self) -> None:
        """单调递增：分页 / 游标 / 增量同步都依赖它。"""
        gen = SnowflakeGenerator(worker_id=1)
        ids = [gen.next_id() for _ in range(3000)]
        assert all(b > a for a, b in zip(ids, ids[1:]))

    def test_同毫秒seq取满后换毫秒而不是回绕(self) -> None:
        """边界：冻结时钟在同一毫秒，连取一整个容量 + 若干。

        正确行为 = 前 4096 个吃满 seq 0..4095，第 4097 个换到下一个毫秒、
        seq 归零。**错误行为**（旧写法的隐患）= 在同一毫秒把 seq 回绕成 0 复用，
        从而在同一毫秒产出两个完全相同的 ID。
        """
        clock = _FakeClock()
        gen = SnowflakeGenerator(worker_id=1)
        with _frozen_time(clock):
            ids = [gen.next_id() for _ in range(SEQUENCE_CAPACITY + 5)]

        assert len(set(ids)) == SEQUENCE_CAPACITY + 5

        seqs = [i & MAX_SEQUENCE for i in ids]
        # 前 4096 个恰好是 0..4095
        assert seqs[:SEQUENCE_CAPACITY] == list(range(SEQUENCE_CAPACITY))
        # 第 4097 个换毫秒并从 0 重来
        assert seqs[SEQUENCE_CAPACITY] == 0
        # 后 5 个落在新的毫秒里
        assert len({i >> 22 for i in ids}) == 2

    def test_多轮突发不交叉重复(self) -> None:
        """很多轮小批量，轮与轮之间也不重复。"""
        gen = SnowflakeGenerator(worker_id=1)
        seen: set[int] = set()
        for _ in range(50):
            batch = gen.next_ids(200)
            assert len(set(batch)) == 200
            assert not (seen & set(batch))
            seen |= set(batch)

    def test_并发调用不重复(self) -> None:
        """多线程并发：_lock 串行化，不应出现重复。"""
        import threading

        gen = SnowflakeGenerator(worker_id=1)
        out: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            local = [gen.next_id() for _ in range(500)]
            with lock:
                out.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(out) == 4000
        assert len(set(out)) == 4000

    def test_worker_id越界报错(self) -> None:
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=-1)
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=idgen.MAX_WORKER_ID + 1)

    def test_负数count报错(self) -> None:
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=1).next_ids(-1)

    def test_decode可还原worker与seq(self) -> None:
        gen = SnowflakeGenerator(worker_id=7)
        sid = gen.next_id()
        info = decode(sid)
        assert info["id"] == sid
        assert info["worker_id"] == 7
        assert info["sequence"] == 0
        # 生成的时刻应当就在"现在"附近（容差 5 秒）
        assert abs(info["timestamp_ms"] - int(time.time() * 1000)) < 5000


# =====================================================================
# 不变量 B：传输层守卫
# =====================================================================


class TestTransportGuard:
    def test_float安全边界(self) -> None:
        # 注意边界本身有个反直觉的点：2^53 能被 float64 精确表示，
        # 真正不可精确表示的是 2^53 + 1。所以"安全整数"上限是 2^53-1。
        assert is_float_safe(MAX_SAFE_FLOAT_INT) is True
        assert is_float_safe(2**53) is True  # 恰好可表示
        assert is_float_safe(2**53 + 1) is False  # 第一个不可表示的整数
        assert is_float_safe(0) is True
        assert is_float_safe(-MAX_SAFE_FLOAT_INT) is True

    def test_雪花ID的float安全性取决于seq(self) -> None:
        """这条是**刻意**断言的反例，纠正一个常见误解。

        不是"雪花 ID 一定过不了 float64" —— 而是**取决于低几位**：

            低 22 位 = worker << 12 | seq，worker=1 时为 4096 | seq
            4096 是 64 的倍数 -> seq=0 的 ID 恰好落在 float64 网格上（无损）
            seq=1..63         -> 落在网格之间（失真，且 64 个塌缩成 1 个）

        所以"手点新建"（每次落在不同毫秒的 seq=0）永远测不出问题，
        而"批量导入"（同毫秒连出 seq>0）必踩 —— 这就是坑 #21 的完整版。
        """
        real = SnowflakeGenerator(worker_id=1).next_id()
        base = (real >> 22) << 22 | (1 << 12)  # 抹掉 seq，保留 worker=1，seq=0
        assert is_float_safe(base) is True  # seq=0 无损
        assert is_float_safe(base | 1) is False  # seq=1 失真
        assert is_float_safe(base | 63) is False  # seq=63 失真
        assert is_float_safe(base | 64) is True  # seq=64 又回到网格上

        with pytest.raises(ValueError, match="字符串"):
            assert_float_safe(base | 1)

    def test_塌缩量化报告(self) -> None:
        """量化"如果这批 ID 走了 float64 会烂成什么样"，给文档与评审看。"""
        ids = SnowflakeGenerator(worker_id=1).next_ids(6000)
        report = float_loss_report(ids)
        assert report["total"] == 6000
        # 同一毫秒的 ID 只差低 12 位，float64 在 3.7e17 量级的粒度是 64
        assert report["distinct_after_float"] < 200, report
        assert report["max_group"] > 1, report

    def test_同毫秒ID经float64会塌缩(self) -> None:
        """真实量级下，同一毫秒的 ID 过 float64 会大量塌缩。

        必须用**真实时钟**：冻结时钟会把时间戳压到纪元附近，ID 变小、
        float64 就"变准"了 —— 那反而测不出问题（也算一个坑）。
        """
        ids = SnowflakeGenerator(worker_id=1).next_ids(SEQUENCE_CAPACITY)
        report = float_loss_report(ids)
        assert report["distinct_after_float"] < report["total"], report
        assert report["max_group"] > 1, report


# =====================================================================
# 小工具
# =====================================================================


class _FakeClock:
    """可控时钟：sleep 会真的推进毫秒（至少 1ms），用来冻结/推进时间。"""

    def __init__(self, start_ms: int = EPOCH + 100_000) -> None:
        self._ms = start_ms

    def time(self) -> float:
        return self._ms / 1000.0

    def sleep(self, seconds: float) -> None:
        self._ms += max(1, int(seconds * 1000))


class _frozen_time:
    """把 idgen 模块里的 time 换成假表。"""

    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        self._real = None

    def __enter__(self):
        self._real = idgen.time
        idgen.time = self._clock  # type: ignore[assignment]
        return self._clock

    def __exit__(self, *exc):
        idgen.time = self._real  # type: ignore[assignment]
        return False


def test_模块默认单例可用() -> None:
    """next_ids() 走全局单例（会惰性读取 settings.snowflake_worker_id）。"""
    ids = next_ids(10)
    assert len(set(ids)) == 10
