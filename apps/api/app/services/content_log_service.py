"""`content_change_logs` 的**唯一写入通道**。

## 为什么要有这个模块（2026-09-25 收口）

这张表原本有 **4 个各自独立的写入点**，而且 SQL 是**逐字复制**出来的同一份 8 列 INSERT：

| 位置 | 形态 | `entity_type` |
|---|---|---|
| `question_service._record_change` | helper | 写死 `'question'` |
| `exam_service._write_change_log` | helper | 参数化 |
| `user_service.update_user_status` | **内联 SQL** | 写死 `'user'` |
| `import_service._INSERT_CHANGE_SQL` | **内联 SQL + 批量 executemany** | 写死 `'question'` |

加第 5 个消费者（题目审核 `action='review'`）之前先收口 ——
否则"加一列 / 加一个 action 值 / 改 `diff` 形状"要改 **5 个地方**，
**漏一个不会报错**，只会让审计抽屉少渲染一块（与坑 44 / 51 / 53 同族：
"没有任何东西会报错"的那一类）。

## 为什么不是"一个函数包打天下"（**这条要说清楚，否则会被误读成没做完**）

`import_service` 的写入是**批量**的：它把一次导入的上千行变更攒进 `change_rows`，
再按 `WRITE_CHUNK` 分批 `db.execute(text(SQL), chunk)`，**一批一次往返**。
改成"逐行 await 一个 async 函数"会让一次导入多出上千次往返 ——
**那是拿导入性能换代码整齐，不值。**

⇒ 所以对外只给三样东西，覆盖**两种调用模式**：

    CHANGE_LOG_INSERT_SQL   唯一的 SQL 文本（批量路径直接用它）
    change_log_params(...)  唯一的参数构造（两条路径共用，保证形状一致）
    record_change(...)      单行便捷入口 = 上面两个的组合（交互路径用它）

**统一的是契约（SQL 与参数形状），不是调用方式。** 四种场景全部能表达，
只是批量那一种继续走 `executemany` —— 这是**有据的不统一**，不是漏了。

## `diff` 的形状（契约，别改）

`diff` 固定是 `{"before": ..., "after": ...}` ——
前端审计抽屉的 `diffJson` 就是按这个结构渲染字段级差异的。
两个值都可以是 `None`（例如"新建"没有 before），但**两个键始终存在**。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idgen import next_id, to_inet

#: `change_log` 列是 `VARCHAR(500)` —— 超长由这里统一截断，
#: 而不是各调用方自己 `[:500]`（那样迟早有一处忘了，然后在库里报 22001 字符串过长）。
#: ⚠️ 截断只作用于 `change_log`（给人看的摘要）；**完整理由在 `diff` 里，不截断**。
CHANGE_LOG_MAX = 500

#: 唯一 SQL。`entity_type` 是**参数**（原先两处把它写死在 SQL 里，是这次收口的主要目标）。
CHANGE_LOG_INSERT_SQL = """
INSERT INTO content_change_logs
  (id, entity_type, entity_id, action, batch_id, diff, change_log, operator_id, operator_ip)
VALUES (:id, :entity_type, :entity_id, :action, :batch_id, CAST(:diff AS jsonb),
        :change_log, :operator_id, :operator_ip)
"""


def dumps(value: Any) -> str:
    """`diff` / 快照类 JSON 的统一序列化口径（`default=str` 兜住 datetime 等）。"""
    return json.dumps(value, ensure_ascii=False, default=str)


def change_log_params(
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    operator_id: int | None,
    before: Any = None,
    after: Any = None,
    change_log: str = "",
    ip: str | None = None,
    batch_id: int | None = None,
) -> dict[str, Any]:
    """构造一行 `content_change_logs` 的绑定参数（**单行与批量共用**）。

    ⚠️ `operator_ip` 是 `INET` 列：必须经 `to_inet()` 转成 `ipaddress` 对象，
    直接塞字符串 asyncpg 会报类型不匹配（坑 6 / 坑 13）。
    """
    return {
        "id": next_id(),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "batch_id": batch_id,
        "diff": dumps({"before": before, "after": after}),
        "change_log": (change_log or "")[:CHANGE_LOG_MAX],
        "operator_id": operator_id,
        "operator_ip": to_inet(ip),
    }


async def record_change(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    operator_id: int | None,
    before: Any = None,
    after: Any = None,
    change_log: str = "",
    ip: str | None = None,
    batch_id: int | None = None,
) -> None:
    """写一条 `content_change_logs`（单行）。**交互路径都走这里。**

    不 `commit`、不吞异常 —— 事务边界与错误语义由调用方决定
    （与"审计失败不能影响主流程"的 `write_audit` **刻意不同**：
    变更日志是业务留痕，写不进去就说明这次变更是不可追溯的，**应当让整个请求失败**）。
    """
    await db.execute(
        text(CHANGE_LOG_INSERT_SQL),
        change_log_params(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            operator_id=operator_id,
            before=before,
            after=after,
            change_log=change_log,
            ip=ip,
            batch_id=batch_id,
        ),
    )


__all__ = [
    "CHANGE_LOG_INSERT_SQL",
    "CHANGE_LOG_MAX",
    "change_log_params",
    "dumps",
    "record_change",
]
