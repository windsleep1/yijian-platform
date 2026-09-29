"""`questions.answer` 的**规范形式** —— 全仓唯一的一处定义（含读宽容 / 写规范 / 校验）。

## 为什么需要它

2026-09-29 之前，同一个库里**判断题有两套答案写法**，而且都叫 `type='judge'`：

| 来源 | `answer` |
|---|---|
| 管理端新建（`question_service.derive_answer`） | `{"value": [true]}` |
| 种子生成器（`db/seed/gen_seed_questions.py`） | `{"value": ["A"]}`（A = 表述正确） |
| 导入管道（`import_service._build_answer`） | `{"value": ["A"]}` |

后果不是"少了个规范"，而是**每个读取方各自猜**：C 端判分第一版直接 `bool(correct[0])`，
而 `bool("A") == True` ⇒ **答错的判断题被判成对**（不报错、还给满分）。
**判据：两种格式都在跑 ⇒ 一定有读取方在处理类型强制。任何依赖类型强制的判分逻辑都是定时炸弹。**

## 规范形式（**这里就是定义**）

顶层只允许一个键 `value`（多选题允许再加一个 `partial_credit`）。
`value` 的**元素类型由题型决定**：

| `type` | `value` | 说明 |
|---|---|---|
| `single` | `["B"]` | **恰好 1 个**大写标号；必须出现在本题 options 里 |
| `multiple` | `["A","C"]` + `partial_credit: true` | ≥2 个互异标号；都在 options 里 |
| `judge` | `[true]` / `[false]` | **恰好 1 个布尔** —— ★ 判定的就是这一条 |
| `case` | `"参考答案文本"` | 主观题：字符串（评分点另有结构） |
| `case_sub` / `fill` / `essay` | `["参考答案文本"]` | 主观题：文本数组 |

**为什么 `judge` 取布尔而不是 `"A"/"B"`**：布尔是**自描述**的。
取 `["A"]` 的话，"A 表示正确"这条语义**不在数据里**（种子的判断题甚至没有 options），
每个读取方都得自带一张映射表 —— 那正是这次事故的形状。

## 三条规则

1. **写路径只许走 `canonical_doc()`** —— 它在这里，也只在这里。
   （静态上由**不变量 8** 守着：`{"value"` 这个字面量只允许出现在少数登记过的文件里。）
2. **读路径宽容**：`judge_bool()` / `normalize_tokens()` 认旧格式（`"A"`/`"B"`/`"对"`/`"错"`…），
   **只在内存里归一，绝不因为读而改数据**。
3. **数据一致性由数据级用例守**（`tests/test_answer_format.py`）：
   库里出现第三种写法、或旧写法没被迁移干净 ⇒ 红。

## 为什么本文件**故意不依赖 Pydantic**

它必须能被**独立的种子生成器**（`db/seed/gen_seed_questions.py`，由 `seed-questions.py`
用 `sys.executable` 单独起进程跑）和**迁移校验**直接 import。
带一个 web 框架依赖进来，那两处就只能各抄一份 —— 而"各抄一份"正是本文件要终结的东西。
⚠️ 放在 `schemas/` 下是听从"写进 schema/校验"的指引；**它不是 Pydantic 模型**，
所以抬头把这件事说明白，免得下一个人以为这里该有 `BaseModel`。
"""

from __future__ import annotations

from typing import Any

#: 判断题的"真 / 假"token。**宽进**（旧数据 + 人写的手工数据都可能出现这些写法），
#: **窄出**（`canonical_doc` 只产出布尔）。
JUDGE_TRUE_TOKENS = frozenset({"A", "T", "TRUE", "Y", "YES", "对", "正确", "√", "1"})
JUDGE_FALSE_TOKENS = frozenset({"B", "F", "FALSE", "N", "NO", "错", "错误", "×", "0"})

#: 客观题（有唯一正确答案、可机判）。主观题的 `value` 是文本，不受标号规则约束。
OBJECTIVE_TYPES = ("single", "multiple", "judge")
TEXT_VALUE_TYPES = ("case", "case_sub", "fill", "essay")


def judge_bool(x: Any) -> bool | None:
    """把一个判断题的答案 token 解成布尔；**认不出来返回 `None`**（绝不猜）。

    ⚠️ 返回 `None` 而不是 `False`：`False` 会把"这题的数据我不认识"伪装成"答案是错"。
    """
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        s = x.strip().upper()
        if s in JUDGE_TRUE_TOKENS:
            return True
        if s in JUDGE_FALSE_TOKENS:
            return False
    return None


def canonical_doc(qtype: str, tokens: Any, *, partial_credit: bool | None = None) -> dict[str, Any]:
    """**唯一**的 `answer` 构造入口。按题型产出规范形式。

    - `judge`：`tokens` 可以是 `[True]` / `["A"]` / `True` —— 一律产出 `{"value": [bool]}`
    - `single` / `multiple`：`tokens` 是标号（字符串会 `strip().upper()`）
    - `case`：`tokens` 是文本 → `{"value": "<文本>"}`
    - `case_sub` / `fill` / `essay`：`{"value": ["<文本>"]}`
    - **认不出来的题型** ⇒ 抛 `ValueError`（不静默按"文本数组"处理：那会把新题型悄悄塞进旧形状）
    """
    seq = tokens if isinstance(tokens, (list, tuple)) else [tokens]
    if qtype == "judge":
        if len(seq) != 1:
            raise ValueError(f"判断题的答案必须恰好 1 个 token，收到 {seq!r}")
        b = judge_bool(seq[0])
        if b is None:
            raise ValueError(f"判断题不认识的答案写法：{seq[0]!r}")
        return {"value": [b]}
    if qtype == "single":
        return {"value": [str(t).strip().upper() for t in seq]}
    if qtype == "multiple":
        out: dict[str, Any] = {"value": sorted({str(t).strip().upper() for t in seq})}
        # `partial_credit` 只在**显式为真**时出现：规范形式里"没有这个键"= 不允许部分分。
        if partial_credit:
            out["partial_credit"] = True
        return out
    if qtype == "case":
        return {"value": seq[0] if len(seq) == 1 else "".join(str(t) for t in seq)}
    if qtype in ("case_sub", "fill", "essay"):
        return {"value": [str(t) for t in seq]}
    raise ValueError(f"未知题型：{qtype!r}（规范形式里没有它的位置 —— 别默认按文本数组处理）")


def normalize_tokens(qtype: str, raw: Any) -> list[Any]:
    """**读路径**：把任意历史形态的 `value` 归一到规范 token（**只在内存里**）。

    宽容的三种输入：`dict`（`{"value": [...]}`）/ JSON 文本 / 裸 list 或标量。
    归不出来时返回**空列表**（调用方按"没有答案"处理，而不是"答案是错的"）——
    这两件事在判分上同归，但在**排障**上完全不同（见 `check_doc`）。
    """
    doc = raw if isinstance(raw, dict) else _maybe_json(raw)
    if isinstance(doc, dict):
        v = doc.get("value")
    else:
        v = doc if isinstance(doc, list) else ([doc] if doc is not None else [])
    if v is None:
        return []
    seq = v if isinstance(v, list) else [v]
    if qtype == "judge":
        return [b for b in (judge_bool(x) for x in seq) if b is not None]
    if qtype == "case":
        return [seq[0]] if seq else []
    return [str(x).strip().upper() if qtype in ("single", "multiple") else str(x) for x in seq]


def _maybe_json(raw: Any) -> Any:
    import json

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def check_doc(qtype: str, raw: Any) -> str | None:
    """**数据级校验**：这份 `answer` 是否符合规范形式？符合返回 `None`，否则一句话说清。

    它是 `tests/test_answer_format.py` 的判据本体（用例只负责把它跑到库里的每一行上）。
    这里**只判断、不改数据** —— "遇旧格式就顺手改掉"会让检查器变成写入器，
    那就再也分不清"库里本来就是对的"和"检查时被修过"了。
    """
    doc = _maybe_json(raw)
    if not isinstance(doc, dict):
        return f"顶层不是对象：{type(doc).__name__}"
    if "value" not in doc:
        return "缺少 value"
    if qtype == "judge":
        v = doc["value"]
        if not isinstance(v, list) or len(v) != 1 or not isinstance(v[0], bool):
            return f'判断题的 value 必须是 [true]/[false]，实际 {v!r}（旧写法 "A"/"B" 必须迁移）'
        return None
    if qtype in ("single", "multiple"):
        v = doc["value"]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
            return f"客观题的 value 必须是非空标号数组，实际 {v!r}"
        if len(set(v)) != len(v):
            return f"标号重复：{v!r}"
        if qtype == "single" and len(v) != 1:
            return f"单选题必须恰好 1 个标号，实际 {len(v)} 个"
        if qtype == "multiple" and len(v) < 2:
            return f"多选题至少 2 个标号，实际 {len(v)} 个"
        return None
    if qtype == "case":
        return (
            None
            if isinstance(doc["value"], str)
            else f"案例题的 value 必须是字符串，实际 {type(doc['value']).__name__}"
        )
    if qtype in TEXT_VALUE_TYPES:
        v = doc["value"]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
            return f"主观题的 value 必须是文本数组，实际 {v!r}"
        return None
    return f"未知题型：{qtype!r}"


__all__ = [
    "JUDGE_FALSE_TOKENS",
    "JUDGE_TRUE_TOKENS",
    "OBJECTIVE_TYPES",
    "TEXT_VALUE_TYPES",
    "canonical_doc",
    "check_doc",
    "judge_bool",
    "normalize_tokens",
]
