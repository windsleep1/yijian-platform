"""导入管道 · 逐行校验契约（⑤c-1 §3.2 + ⑤c-2 §3.3 / §3.4）。

**为什么只测 `validate_row` 这一个入口**（`docs/19` §7 的判据）：
`_s` / `_int_or_none` / `_float_or_none` / `parse_answer_points` / `_build_answer` 都是
**私有实现** —— 直接测它们会在"重命名 / 内联 / 拆分"时变红，那是"重构实现但不改契约"的假红。
它们的每个分支都由本文件的用例**间接覆盖**（经 `validate_row` → `_build_payload`）。

`validate_row(row, row_no, ctx) -> RowOutcome` 的契约：
  - 合法行 → `action ∈ {insert, update, duplicate}`，且带 `payload`
  - 不合法 → `action == "error"`，`errors` 里每条带 `field`（前端据此精确定位）

**统一手法**：`_row()` 给一行**完全合法**的单选题，每个用例只破坏**一个**字段 ——
这样"报错"必然来自那一处，断言才立得住。

覆盖的缺口行（`docs/19` §3.2 / §3.3 / §3.4 共 63 条）：
  §3.2  253 / 255 / 265 / 266 / 272 / 275 / 276
  §3.3  297–312（评分点整段）/ 336 / 337 / 338
  §3.4  387 / 391 / 414 / 421 / 432 / 436 / 438 / 443 / 445 / 450 / 452 / 454 / 456 /
        466 / 475 / 478 / 489 / 491 / 498 / 499 / 500 / 501 / 502 / 507 / 509 / 514 /
        517 / 519 / 524 / 529 / 533 / 539 / 541 / 546 / 551 / 552 / 553 / 554 / 576
"""

from typing import Any

import pytest

from app.services.import_service import ValidationContext, validate_row

SUBJECT_ID = 7
CHAPTER_ID = 70
KP_ID = 700

# 一个"只授权某科目"的上下文用不到这里（`allowed_subject_ids` 传 None = 不限制）；
# 行级数据范围拒绝（405）**早已被覆盖**（见 docs/19 §3.4 的纠正），故本文件不重复。


def _ctx(**over: Any) -> ValidationContext:
    base: dict[str, Any] = {
        "subjects_by_code": {"SW": {"id": SUBJECT_ID, "code": "SW", "name": "社会工作"}},
        "chapters_by_key": {(SUBJECT_ID, "CH1"): CHAPTER_ID},
        "kps_by_key": {(SUBJECT_ID, "KP1"): KP_ID},
        "kp_chapter": {KP_ID: CHAPTER_ID},
        "existing_hashes": {},
        "allowed_subject_ids": None,
        "batch_subject_id": None,
        "mode": "insert",
    }
    base.update(over)
    return ValidationContext(**base)


def _row(**over: Any) -> dict[str, Any]:
    """一行**完全合法**的单选题。用例只改一个字段（或显式传 None 去掉选项）。"""
    base: dict[str, Any] = {
        "subject_code": "SW",
        "type": "single",
        "stem": "下列哪一项是正确的？",
        "answer": "A",
        "analysis": "因为教材第三章讲了这一点。",
        "difficulty": "3",
        "option_a": "第一个选项",
        "option_b": "第二个选项",
    }
    base.update(over)
    return base


def _fields(outcome: Any) -> list[str]:
    return [e.field for e in outcome.errors]


# ============================================================ 正向对照


def test_valid_row_is_accepted_and_carries_payload() -> None:
    """正向对照：合法行 → `insert` + 完整 payload。

    没有它，下面所有"报错"都可能是**别的字段本来就坏了**（假绿的反面）。
    """
    outcome = validate_row(_row(), 1, _ctx())
    assert outcome.action == "insert"
    assert outcome.errors == []
    assert outcome.payload is not None
    assert outcome.payload["subject_id"] == SUBJECT_ID
    assert outcome.payload["chapter_id"] is None  # 未填章节 → None，不是 0
    assert outcome.options is not None and len(outcome.options) == 2


# ============================================================ §3.2 取值边界


def test_missing_key_is_treated_as_empty_string() -> None:
    """`row` 里**没有**这个 key（JSON 少写一列）→ 当成空串处理（覆盖 253）。

    ⚠️ 与 CSV 的差别：CSV 下 `DictReader` 会给所有列名赋空串；只有 JSON 输入会真的
    "缺 key"。两者最终都得到 `""`，但走的是**不同代码路径** —— 这里固定的是后者。
    """
    row = _row()
    del row["analysis"]
    outcome = validate_row(row, 1, _ctx())
    assert outcome.action == "error"
    assert _fields(outcome) == ["analysis"]  # 缺 key 报的是"必填"，不是崩
    # ⚠️ 只断言 field 不够：若实现把缺 key 当成某个**非空**值，field 仍然对。
    # 必须断言"必填"语义 —— 变异验证（把 return "" 改成 return "x"）会打掉这一点。
    assert "必填" in outcome.errors[0].message


def test_none_value_is_treated_as_empty_string() -> None:
    """显式传 `None` 与"缺 key"等价（覆盖 253 的另一半）。"""
    outcome = validate_row(_row(analysis=None), 1, _ctx())
    assert outcome.action == "error"
    assert _fields(outcome) == ["analysis"]
    assert "必填" in outcome.errors[0].message


def test_list_field_is_joined_for_pipe_delimited_parsing() -> None:
    """JSON 里 `"tags": ["a","b"]` 这类数组 → 拼成 `a|b` 再走竖线解析（覆盖 255）。"""
    outcome = validate_row(_row(tags=["甲", "乙", "丙"]), 1, _ctx())
    assert outcome.action == "insert"
    assert outcome.payload is not None
    assert outcome.payload["tags"] == ["甲", "乙", "丙"]


def test_blank_score_falls_back_to_default() -> None:
    """`score` 留空 → `None` → 落库时给默认 1.0，**不报错**（覆盖 272）。"""
    outcome = validate_row(_row(score=""), 1, _ctx())
    assert outcome.action == "insert"
    assert outcome.payload is not None
    assert outcome.payload["score_default"] == 1.0


def test_non_numeric_score_falls_back_to_default() -> None:
    """`score` 填了非数字 → 当没填处理（覆盖 275 / 276）。

    刻意的宽容：分值不是必填项，写错不该让整批拒收。
    """
    outcome = validate_row(_row(score="很多分"), 1, _ctx())
    assert outcome.action == "insert"
    assert outcome.payload is not None
    assert outcome.payload["score_default"] == 1.0


# ============================================================ §3.3 评分点与答案组装


def test_answer_points_are_parsed_with_all_four_forms() -> None:
    """评分点解析：整分值 / 小分值 / 不带分值 / 分值非法 —— **一次覆盖整段**（覆盖 297–312）。

    契约（docstring）：容忍"没写分值"（给 0），因为评分点是**参考信息**，不该因格式不完美就整批拒绝。
    """
    raw = "要点一|2;;要点二|3.5;;要点三;;要点四|abc;;要点五|4分"
    outcome = validate_row(_row(answer_points=raw), 1, _ctx())
    assert outcome.action == "insert"
    assert outcome.payload is not None
    points = outcome.payload["analysis_points"]
    assert points == [
        {"key": "p1", "text": "要点一", "score": 2},  # 整分值保留 int（2 与 2.0 在 jsonb 里不同）
        {"key": "p2", "text": "要点二", "score": 3.5},  # 小分值保留 float
        {"key": "p3", "text": "要点三", "score": 0},  # 没写分值 → 0
        {"key": "p4", "text": "要点四|abc", "score": 0},  # 分值非法 → 整段当文本、给 0
        {"key": "p5", "text": "要点五", "score": 4},  # "4分" 的后缀要能去掉
    ]
    # ⚠️ 上面的 dict 相等**区分不出 2 与 2.0**（Python 里 2 == 2.0）。
    # 而 docstring 的契约正是"整分值保留 int" —— 必须单独断言类型，
    # 否则变异"总是返回 float"会存活（测试全绿但契约已破）。
    assert [type(p["score"]).__name__ for p in points] == ["int", "float", "int", "int", "int"]


def test_case_question_answer_is_plain_text() -> None:
    """`case`（案例大题）的答案存**纯文本**，不是数组（覆盖 336 / 337）。

    为什么是文本：种子库里案例题就是字符串形态，导入要与既有数据一致（docs/11 §7）。
    """
    outcome = validate_row(
        _row(type="case", answer="参考答案文本", option_a=None, option_b=None), 1, _ctx()
    )
    assert outcome.action == "insert"
    assert outcome.payload is not None
    assert outcome.payload["answer"] == {"value": "参考答案文本"}


def test_text_answer_types_wrap_answer_in_list() -> None:
    """`fill` / `essay` / `case_sub` 的答案存**单元素数组**（覆盖 338 + 498）。

    498 是 `elif qtype in TEXT_ANSWER_TYPES:` —— 在这之前**没有任何用例走过文本题型分支**。
    """
    outcome = validate_row(
        _row(type="fill", answer="参考答案", option_a=None, option_b=None), 1, _ctx()
    )
    assert outcome.action == "insert"
    assert outcome.payload is not None
    assert outcome.payload["answer"] == {"value": ["参考答案"]}


def test_same_content_twice_in_one_file_keeps_only_the_first() -> None:
    """**文件内重复**：同一份文件里两行内容一致 → 只入第一条（覆盖 576）。

    ⚠️ 与"库内重复"（已有同内容题目）是**两条不同的判定**：前者看 `seen_hashes`（本批内），
    后者看 `existing_hashes`（库里）。这条在此之前从未被触发过。
    """
    ctx = _ctx()
    first = validate_row(_row(), 1, ctx)
    assert first.action == "insert"
    assert first.payload is not None
    ctx.seen_hashes[first.payload["content_hash"]] = 1  # 模拟 validate_batch 的登记动作

    second = validate_row(_row(), 2, ctx)
    assert second.action == "duplicate"
    assert second.message is not None and "第 1 行" in second.message


# ============================================================ §3.4 逐行拒绝

# (row 覆盖, 期望报错字段, ctx 覆盖)
REJECT_CASES: list[tuple[dict[str, Any], str, dict[str, Any]]] = [
    # --- 必填与存在性 ---
    ({"subject_code": ""}, "subject_code", {}),
    ({"subject_code": "XX"}, "subject_code", {}),
    ({"type": ""}, "type", {}),
    ({"stem": ""}, "stem", {}),
    ({"answer": ""}, "answer", {}),
    ({"analysis": ""}, "analysis", {}),
    ({"difficulty": ""}, "difficulty", {}),
    ({"difficulty": "很难"}, "difficulty", {}),  # 覆盖 265 / 266：非数字 → 当没填
    # --- 跨字段一致性 ---
    ({}, "subject_code", {"batch_subject_id": 999}),  # 行内科目 ≠ 批次科目
    ({"subject_code": "XX", "chapter_code": "CH1"}, "chapter_code", {}),  # 科目没过，章节没法校验
    ({"subject_code": "XX", "kp_code": "KP1"}, "kp_code", {}),  # 科目没过，知识点没法校验
    ({"kp_code": "KP9"}, "kp_code", {}),  # 知识点不存在
    ({"chapter_code": "CH1", "kp_code": "KP1"}, "kp_code", {"kp_chapter": {KP_ID: 999}}),
    ({"type": "judge", "answer": "对"}, "answer", {}),  # 判断题只认 A / B
    ({"answer": "A|B"}, "answer", {}),  # 单选题必须恰好 1 个正确答案
    ({"type": "multiple", "answer": "A"}, "answer", {}),  # 多选题至少 2 个
    # --- 文本题型 ---
    ({"type": "fill", "answer": "", "option_a": None, "option_b": None}, "answer", {}),
    ({"type": "fill", "answer": "文本", "option_a": "x", "option_b": "y"}, "option_a", {}),
    # --- 长度与范围 ---
    ({"stem": "短"}, "stem", {}),
    ({"stem": "x" * 3001}, "stem", {}),
    ({"stem": "<b>带标签的题干内容</b>"}, "stem", {}),
    ({"option_a": "", "option_b": ""}, "option_a", {}),  # 选项数 < 2
    ({"analysis": "太短"}, "analysis", {}),
    ({"score": "0"}, "score", {}),
    ({"difficulty": "9"}, "difficulty", {}),
    ({"exam_year": "1800", "source_type": "public"}, "exam_year", {}),
    ({"tags": "甲,乙,丙,丁,戊,己,庚,辛,壬"}, "tags", {}),  # 9 个 > 上限 8
    # --- 枚举合法性 ---
    ({"type": "单选题"}, "type", {}),
    ({"source_type": "unknown"}, "source_type", {}),
    # --- 合规红线 ---
    ({"source_type": "public"}, "source_name", {}),
    ({"source_type": "authorized", "source_name": "某出版社"}, "source_license", {}),
    ({"exam_year": "2020", "source_type": "self"}, "exam_year", {}),
    # --- 案例小题绑定 ---
    (
        {
            "type": "case_sub",
            "case_group_id": "",
            "answer": "文本",
            "option_a": None,
            "option_b": None,
        },
        "case_group_id",
        {},
    ),
    (
        {
            "type": "case_sub",
            "case_group_id": "G9",
            "answer": "文本",
            "option_a": None,
            "option_b": None,
        },
        "case_group_id",
        {"case_groups_in_file": {"G1"}},
    ),
]


@pytest.mark.parametrize(
    "over,field,ctx_over",
    [pytest.param(o, f, c, id=f"{f}-{i}") for i, (o, f, c) in enumerate(REJECT_CASES)],
)
def test_invalid_row_is_rejected_with_field(
    over: dict[str, Any], field: str, ctx_over: dict[str, Any]
) -> None:
    """每一处非法输入都必须：`action == "error"` 且**点名出错的字段**。

    `field` 是前端"精确定位到列"的依据 —— 位置错了，教研会去改错行。
    """
    outcome = validate_row(_row(**over), 1, _ctx(**ctx_over))
    assert outcome.action == "error", f"应当拒绝，却得到 {outcome.action}"
    assert field in _fields(outcome), f"期望 {field}，实际 {_fields(outcome)}"
    assert outcome.payload is None  # 拒绝的行不得带 payload（否则可能被误写）


def test_compliance_rejections_are_all_reported_at_once() -> None:
    """合规红线**一次全报**，而不是"修好一条再暴露下一条"。

    三条红线（来源名 / 授权凭证 / 年份只允许 authorized+public）是独立的，
    同时违反就要同时列出 —— 否则用户要来回上传三遍。
    """
    outcome = validate_row(_row(source_type="authorized", exam_year="2020"), 1, _ctx())
    assert outcome.action == "error"
    fields = _fields(outcome)
    assert "source_name" in fields
    assert "source_license" in fields
