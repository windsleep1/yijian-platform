"""跨 schema 复用的字段类型。

为什么需要 `BigIntStr`
---------------------
主键是雪花 ID（BIGINT），值 18~19 位，**超出 JS 安全整数范围**（`2^53-1 = 9007199254740991`）。
Node 22 实测：

    JSON.parse('{"id":375228939615866880}').id  →  375228939615866900   // 静默丢精度

前端拿这个被抹平的 id 去拼 `PUT /admin/users/{id}/roles`，后端查不到 → **必然 404**。
而它不报错、不抛异常，只是 id 尾数悄悄变了，属于最难查的一类 bug。

因此：**所有对外的 ID 字段一律序列化成字符串**。这是根治手段。
前端 `parseJsonSafe` 只是防御垫，见 `apps/admin/src/lib/json-bigint.ts`。

实现要点
--------
- `PlainSerializer` 只影响**序列化**（出参），不影响校验，所以入参继续用 `int` 完全没问题。
- `when_used="json"`：只有 JSON 输出才转字符串；`model_dump()`（python 模式）仍是 `int`，
  避免内部代码拿着字符串当 int 用（例如 `next_id()`、SQL 参数绑定）。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import PlainSerializer


def _to_str(v: int) -> str:
    return str(v)


def _to_str_opt(v: int | None) -> str | None:
    return None if v is None else str(v)


BigIntStr = Annotated[int, PlainSerializer(_to_str, return_type=str, when_used="json")]
"""必填 BIGINT，JSON 输出为字符串（如 `"375228939615866880"`）。"""

BigIntStrOpt = Annotated[
    int | None, PlainSerializer(_to_str_opt, return_type=str | None, when_used="json")
]
"""可空 BIGINT，JSON 输出为字符串或 `null`。"""

__all__ = ["BigIntStr", "BigIntStrOpt"]
