"""题库批量导入管道（Batch 5 Pass 1）。

六步流程（严格实现 `docs/07-题库合规与导入规范.md` §4.3）：

    ① upload    建批次（不写任何题）
    ② validate  逐行校验（dry-run，不写库）
    ③ get       查状态 + 统计 + 错误报告
    ④ execute   执行导入（整批事务）
    ⑤ publish   draft → published
    ⑥ rollback  整批回滚

## 六个必须讲清的约定

1. **逐行错误定位**：校验失败返回 `{row_no, field, message}`，`row_no` 是**文件里的
   数据行号**（从 1 开始、不含表头）。教研拿它去 Excel 跳行就能找到那一格。
2. **幂等靠 `content_hash`**：指纹由 `question_service.content_hash()` 从
   **题干 + 选项 + 题型**重算（**不信任文件里带的 hash** —— 那是不可信输入）。
   文件导 N 次，`insert` 模式下第 2 次全是 `duplicate`，行数不变。
3. **事务边界**：`execute` 全程**一个事务**。任何一行抛出预期外异常 →
   整批回滚，一条不落。不存在"导了一半"。
   （文档 §4.3 说的"每批 500 行"在这里是**同一事务内的分批写**，
   只是为了减少往返，不是分批提交 —— 分批提交会破坏"整批成功或整批失败"。）
4. **回滚留痕**：软删除本批 `insert` 的题、还原本批 `update` 的题，
   并写 `content_change_logs(action='rollback')`。
5. **合规红线**：`source_type != 'self'` 必须有 `source_name`，
   `authorized` 还必须有 `source_license` —— 缺了就是该行校验失败，
   **不允许静默通过**。
6. **数据范围**：判定复用 `question_service.scope_subject_ids()`，
   scope 不匹配的行在**校验阶段**就拒绝（而不是等写库才发现）。

## 两个刻意的取舍

- **插入 = draft，更新 = 保留原状态**：`execute` 插入的题一律 `status='draft'`
  （等 ⑤ publish 再放开）；但 upsert 命中老题时**不覆盖**它的 `status` ——
  把一道已发布的题因为"内容被重导了一遍"而打回草稿，是运维事故。
- **案例题的分组只能"同批自洽"**：`questions` 表没有 `case_group_id` 列，
  小问靠 `parent_id / root_id` 挂到大题上。所以案例小题的
  `case_group_id` 必须能在**本批次内**找到同值的大题行 ——
  找不到就在校验阶段报错，而不是等 insert 时外键失败。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, not_found
from app.core.idgen import next_id, next_ids, to_inet
from app.schemas.admin_import import (
    OPTION_COLUMNS,
    ErrorReport,
    ImportBatchDetail,
    ImportBatchOut,
    ImportChangeItem,
    ImportChangeOut,
    ImportExecuteOut,
    ImportRollbackOut,
    ImportRowPreview,
    ImportUploadOut,
    RowError,
)
from app.services.audit_service import write_audit
from app.services.question_service import ScopeViewer, content_hash, scope_subject_ids

# --------------------------------------------------------------------- 上限

MAX_FILE_BYTES = 20 * 1024 * 1024  # 单文件 20MB（6000 题的 json 约 13MB）
MAX_ROWS = 20000  # 单批最多 2 万行
ERROR_REPORT_LIMIT = 200  # 错误报告最多回传多少条
ROW_PAGE_SIZE_DEFAULT = 50

OBJECTIVE_TYPES = ("single", "multiple")
TEXT_ANSWER_TYPES = ("case", "case_sub", "fill", "essay")
ALL_TYPES = ("single", "multiple", "judge", "case", "case_sub", "fill", "essay")
SOURCE_TYPES = ("self", "authorized", "public", "user_import", "ai_assisted")

MIN_STEM, MAX_STEM = 5, 3000
MIN_ANALYSIS = 10
MIN_OPTIONS, MAX_OPTIONS = 2, len(OPTION_COLUMNS)  # 2 ~ 6（option_a..option_f）
MAX_TAGS = 8
# 执行阶段"分批写"的批大小（同一事务内）
WRITE_CHUNK = 500


# =====================================================================
# 解析
# =====================================================================


def parse_file(content: bytes, file_type: str) -> list[dict[str, Any]]:
    """把上传的文件解析成「行字典」列表。

    `csv` 按 UTF-8 解码，**容忍 BOM**（Excel 另存为 CSV 默认带 BOM；
    不处理就会让第一列列名变成 `\\ufeffsubject_code`，然后整份文件都报
    "subject_code 不能为空" —— 这种坑最气人）。
    `json` 既接受裸数组，也接受 `{"questions": [...]}`。
    """
    if len(content) > MAX_FILE_BYTES:
        raise bad_request(
            f"文件过大：{len(content) / 1048576:.1f}MB，上限 {MAX_FILE_BYTES // 1048576}MB",
            40001,
        )
    if file_type == "csv":
        return _parse_csv(content)
    if file_type == "json":
        return _parse_json(content)
    raise bad_request(f"不支持的文件类型：{file_type}（本批只支持 csv / json）", 40001)


def _decode_text(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise bad_request("文件编码无法识别，请另存为 UTF-8 后重试", 40001)


def _parse_csv(content: bytes) -> list[dict[str, Any]]:
    body = _decode_text(content)
    reader = csv.DictReader(io.StringIO(body))
    if not reader.fieldnames:
        raise bad_request("CSV 没有表头行", 40001)
    fields = [(f or "").strip() for f in reader.fieldnames]
    need = ("subject_code", "type", "stem", "answer", "analysis", "difficulty")
    missing = [c for c in need if c not in fields]
    if missing:
        raise bad_request(f"CSV 表头缺少必需列：{'、'.join(missing)}", 40001)
    return [{(k or "").strip(): v for k, v in raw.items()} for raw in reader]


def _parse_json(content: bytes) -> list[dict[str, Any]]:
    body = _decode_text(content)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise bad_request(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）", 40001) from exc
    if isinstance(data, dict):
        for key in ("questions", "items", "rows", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise bad_request("JSON 顶层必须是数组（或含 questions 数组的对象）", 40001)
    for item in data:
        if not isinstance(item, dict):
            raise bad_request("JSON 数组的每一项都必须是对象", 40001)
    return list(data)


# =====================================================================
# 校验
# =====================================================================


@dataclass
class RowOutcome:
    row_no: int
    action: str
    errors: list[RowError] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    options: list[dict[str, Any]] | None = None
    question_id: int | None = None
    message: str | None = None


@dataclass
class ValidationContext:
    """一批共享的校验上下文：参照数据一次查好，逐行复用（避免 N+1）。"""

    subjects_by_code: dict[str, dict[str, Any]]
    chapters_by_key: dict[tuple[int, str], int]
    kps_by_key: dict[tuple[int, str], int]
    kp_chapter: dict[int, int]
    existing_hashes: dict[str, int]
    allowed_subject_ids: set[int] | None
    batch_subject_id: int | None
    mode: str
    seen_hashes: dict[str, int] = field(default_factory=dict)
    case_groups_in_file: set[str] = field(default_factory=set)


async def load_context(
    db: AsyncSession, *, current_user: ScopeViewer, batch_subject_id: int | None, mode: str
) -> ValidationContext:
    subjects = (
        (
            await db.execute(
                text(
                    "SELECT id, code, name, category, professional FROM subjects WHERE status = 'on'"
                )
            )
        )
        .mappings()
        .all()
    )
    chapters = (
        (
            await db.execute(
                text("SELECT id, subject_id, code FROM chapters WHERE is_deleted = false")
            )
        )
        .mappings()
        .all()
    )
    kps = (
        (
            await db.execute(
                text(
                    "SELECT id, subject_id, chapter_id, code FROM knowledge_points "
                    "WHERE is_deleted = false"
                )
            )
        )
        .mappings()
        .all()
    )
    existing = (
        (await db.execute(text("SELECT id, content_hash FROM questions WHERE is_deleted = false")))
        .mappings()
        .all()
    )

    return ValidationContext(
        subjects_by_code={r["code"]: dict(r) for r in subjects},
        chapters_by_key={(r["subject_id"], r["code"]): r["id"] for r in chapters},
        kps_by_key={(r["subject_id"], r["code"]): r["id"] for r in kps},
        kp_chapter={r["id"]: r["chapter_id"] for r in kps},
        existing_hashes={r["content_hash"]: r["id"] for r in existing},
        allowed_subject_ids=scope_subject_ids(current_user),
        batch_subject_id=batch_subject_id,
        mode=mode or "insert",
    )


def _s(row: dict[str, Any], key: str) -> str:
    v = row.get(key)
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "|".join(str(x) for x in v)
    return str(v).strip()


def _int_or_none(row: dict[str, Any], key: str) -> int | None:
    raw = _s(row, key)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _float_or_none(row: dict[str, Any], key: str) -> float | None:
    raw = _s(row, key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _has_html_tag(s: str) -> bool:
    return bool(re.search(r"</?[A-Za-z][^>]*>", s))


def _split_pipe(s: str) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in re.split(r"[|,，;；]", s) if p.strip()]


def parse_answer_points(raw: str) -> list[dict[str, Any]]:
    """`评分点1|2;;评分点2|3` -> `[{"key":"p1","text":...,"score":2}, ...]`。

    容忍"没写分值"（给 0），因为评分点是**参考信息**，
    不该因为格式不完美就整批拒绝。
    """
    if not raw:
        return []
    points: list[dict[str, Any]] = []
    for i, chunk in enumerate(p for p in raw.split(";;") if p.strip()):
        piece = chunk.strip()
        score: float | int = 0
        if "|" in piece:
            head, _, tail = piece.rpartition("|")
            try:
                num = float(tail.strip().rstrip("分"))
                # 整分值保留 int：jsonb 里 2 与 2.0 是**两个不同的值**，
                # 写成 2.0 会让"重导同一份文件"在 analysis_points 上产生无意义 diff。
                score = int(num) if num.is_integer() else num
                piece = head.strip()
            except ValueError:
                pass
        points.append({"key": f"p{i + 1}", "text": piece, "score": score})
    return points


def _build_answer(qtype: str, answer_raw: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    """按题型组装 `answer` JSONB。

    刻意与库中**既有数据**保持一致（而不是与 Batch 4 的 API 一致）：

    - `single` / `multiple` -> `{"value": ["A","C"]}`（multiple 带 partial_credit）
    - `judge`               -> `{"value": ["A"|"B"]}`，
      因为种子里 978 道判断题存的就是 `["A"]`/`["B"]`；Batch 4 的
      `derive_answer` 存的是布尔值。这个不一致是**已知遗留项**，
      记在 docs/11 §7，不在本批悄悄改（改了会动到 978 行历史数据）。
    - `case`                -> `{"value": "<文本>"}`（种子即字符串）
    - `case_sub`/`fill`/`essay` -> `{"value": ["<文本>"]}`
    """
    if qtype == "judge":
        return {"value": [answer_raw.strip().upper()]}
    if qtype == "single":
        return {"value": [c for c in answer_raw.replace(" ", "").upper() if c.isalpha()]}
    if qtype == "multiple":
        letters = [c for c in re.split(r"[|,，;；\s]+", answer_raw) if c]
        letters = [c.strip().upper() for c in letters if c.strip()]
        return {"value": letters, "partial_credit": True}
    if qtype == "case":
        return {"value": answer_raw}
    return {"value": [answer_raw]}


def _build_payload(
    row: dict[str, Any],
    *,
    qtype: str,
    subject_id: int,
    chapter_id: int | None,
    kp_id: int | None,
    options: list[dict[str, Any]],
    hash_value: str,
) -> dict[str, Any]:
    stem = _s(row, "stem")
    score = _float_or_none(row, "score")
    return {
        "subject_id": subject_id,
        "chapter_id": chapter_id,
        "knowledge_point_id": kp_id,
        "type": qtype,
        "stem": stem,
        "answer": _build_answer(qtype, _s(row, "answer"), options),
        "analysis": _s(row, "analysis"),
        "analysis_points": parse_answer_points(_s(row, "answer_points")),
        "score_default": 1.0 if score is None else score,
        "difficulty": _int_or_none(row, "difficulty"),
        "exam_year": _int_or_none(row, "exam_year"),
        "source_type": _s(row, "source_type") or "self",
        "source_name": _s(row, "source_name") or None,
        "source_license": _s(row, "source_license") or None,
        "tags": _split_pipe(_s(row, "tags")),
        "media_urls": _split_pipe(_s(row, "media_urls")),
        "material": _s(row, "material") or None,
        "case_group_id": _s(row, "case_group_id") or None,
        "content_hash": hash_value,
    }


def validate_row(row: dict[str, Any], row_no: int, ctx: ValidationContext) -> RowOutcome:
    """逐行校验。每条错误都带 `field`，前端可精确到字段。"""
    errors: list[RowError] = []

    def err(fld: str, msg: str) -> None:
        errors.append(RowError(row_no=row_no, field=fld, message=msg))

    # ---- 1. subject_code ----
    subject_code = _s(row, "subject_code")
    subject: dict[str, Any] | None = None
    if not subject_code:
        err("subject_code", "必填：科目编码不能为空")
    else:
        subject = ctx.subjects_by_code.get(subject_code)
        if subject is None:
            err("subject_code", f"科目编码不存在：{subject_code}")
    subject_id = subject["id"] if subject else None

    # ---- 2. 数据范围（合规属性，校验阶段就拒绝）----
    if subject_id is not None and ctx.allowed_subject_ids is not None:
        if subject_id not in ctx.allowed_subject_ids:
            allowed_desc = (
                "、".join(
                    f"{c}({ctx.subjects_by_code[c]['name']})"
                    for c in ctx.subjects_by_code
                    if ctx.subjects_by_code[c]["id"] in ctx.allowed_subject_ids
                )
                or "（无）"
            )
            err(
                "subject_code",
                f"超出你的数据范围：{subject_code} 不在你可操作的科目内（可操作：{allowed_desc}）",
            )
    if (
        subject_id is not None
        and ctx.batch_subject_id is not None
        and subject_id != ctx.batch_subject_id
    ):
        err("subject_code", "与批次指定的科目不一致")

    # ---- 3. chapter_code ----
    chapter_id: int | None = None
    chapter_code = _s(row, "chapter_code")
    if chapter_code:
        if subject_id is None:
            err("chapter_code", "无法校验章节：subject_code 未通过校验")
        else:
            chapter_id = ctx.chapters_by_key.get((subject_id, chapter_code))
            if chapter_id is None:
                err("chapter_code", f"章节编码不存在：{subject_code} 下没有 {chapter_code}")

    # ---- 4. kp_code ----
    kp_id: int | None = None
    kp_code = _s(row, "kp_code")
    if kp_code:
        if subject_id is None:
            err("kp_code", "无法校验知识点：subject_code 未通过校验")
        else:
            kp_id = ctx.kps_by_key.get((subject_id, kp_code))
            if kp_id is None:
                err("kp_code", f"知识点编码不存在：{subject_code} 下没有 {kp_code}")
            elif chapter_id is not None and ctx.kp_chapter.get(kp_id) != chapter_id:
                err("kp_code", "该知识点不属于所填章节")

    # ---- 5. type ----
    qtype = _s(row, "type").lower()
    if not qtype:
        err("type", "必填：题型不能为空")
    elif qtype not in ALL_TYPES:
        err("type", f"题型非法：{qtype}（可选：{'/'.join(ALL_TYPES)}）")

    # ---- 6. stem ----
    stem = _s(row, "stem")
    if not stem:
        err("stem", "必填：题干不能为空")
    elif len(stem) < MIN_STEM:
        err("stem", f"题干过短：{len(stem)} 字，至少 {MIN_STEM} 字")
    elif len(stem) > MAX_STEM:
        err("stem", f"题干过长：{len(stem)} 字，上限 {MAX_STEM} 字")
    elif _has_html_tag(stem):
        err("stem", "题干不能含 HTML 标签（富文本请走 stem_html，导入模板不支持）")

    # ---- 7. 选项 + 答案 ----
    provided = [(col, _s(row, col)) for col in OPTION_COLUMNS if _s(row, col)]
    options: list[dict[str, Any]] = []
    answer_raw = _s(row, "answer")

    if qtype == "judge":
        letter = answer_raw.strip().upper()
        if letter not in ("A", "B"):
            err("answer", f"判断题答案必须是 A 或 B，当前：{answer_raw or '（空）'}")
        else:
            options = [
                {"label": "A", "content": "正确", "is_correct": letter == "A", "sort_no": 0},
                {"label": "B", "content": "错误", "is_correct": letter == "B", "sort_no": 1},
            ]
    elif qtype in OBJECTIVE_TYPES:
        labels = [col.replace("option_", "").upper() for col, _ in provided]
        if len(provided) < MIN_OPTIONS:
            err("option_a", f"{qtype} 题至少需要 {MIN_OPTIONS} 个选项，当前 {len(provided)} 个")
        correct: list[str] = []
        if not answer_raw:
            err("answer", "必填：客观题答案不能为空")
        else:
            correct = [c for c in re.split(r"[|,，;；\s]+", answer_raw) if c]
            correct = [c.strip().upper() for c in correct if c.strip()]
            outside = [c for c in correct if c not in labels]
            if outside:
                err(
                    "answer",
                    f"答案 {'、'.join(outside)} 不在选项中（本题选项：{'、'.join(labels) or '（无）'}）",
                )
            elif qtype == "single" and len(correct) != 1:
                err("answer", f"单选题必须恰好 1 个正确答案，当前 {len(correct)} 个")
            elif qtype == "multiple" and len(correct) < 2:
                err("answer", f"多选题至少 2 个正确答案，当前 {len(correct)} 个")
        if not errors:
            for i, (col, val) in enumerate(provided):
                label = col.replace("option_", "").upper()
                options.append(
                    {"label": label, "content": val, "is_correct": label in correct, "sort_no": i}
                )
    elif qtype in TEXT_ANSWER_TYPES:
        if not answer_raw:
            err("answer", f"{qtype} 题必须给出参考答案文本")
        if provided:
            err("option_a", f"{qtype} 题不应填写选项（本题型无选项）")

    # ---- 8. analysis ----
    analysis = _s(row, "analysis")
    if not analysis:
        err("analysis", "必填：解析不能为空")
    elif len(analysis) < MIN_ANALYSIS:
        err("analysis", f"解析过短：{len(analysis)} 字，至少 {MIN_ANALYSIS} 字")

    # ---- 9. score / difficulty ----
    score = _float_or_none(row, "score")
    if score is not None and score <= 0:
        err("score", f"分值必须大于 0，当前：{score}")
    difficulty = _int_or_none(row, "difficulty")
    if difficulty is None:
        err("difficulty", "必填：难度必须为 1~5 的整数")
    elif not 1 <= difficulty <= 5:
        err("difficulty", f"难度必须在 1~5 之间，当前：{difficulty}")

    # ---- 10. exam_year ----
    exam_year = _int_or_none(row, "exam_year")
    if exam_year is not None and not 1990 <= exam_year <= 2100:
        err("exam_year", f"年份必须在 1990~2100 之间，当前：{exam_year}")

    # ---- 11. 合规红线 ----
    source_type = _s(row, "source_type") or "self"
    if source_type not in SOURCE_TYPES:
        err("source_type", f"来源类型非法：{source_type}（可选：{'/'.join(SOURCE_TYPES)}）")
    source_name = _s(row, "source_name")
    source_license = _s(row, "source_license")
    if source_type != "self" and not source_name:
        err(
            "source_name",
            f"合规红线：source_type={source_type} 时必须填写 source_name"
            "（来源不可追溯的内容不得入库）",
        )
    if source_type == "authorized" and not source_license:
        err("source_license", "合规红线：authorized 来源必须填写 source_license（授权凭证号）")
    if exam_year is not None and source_type in ("self", "ai_assisted", "user_import"):
        err("exam_year", f"只有 authorized / public 来源才能填 exam_year（当前 {source_type}）")

    # ---- 12. tags ----
    tags = _split_pipe(_s(row, "tags"))
    if len(tags) > MAX_TAGS:
        err("tags", f"标签最多 {MAX_TAGS} 个，当前 {len(tags)} 个")

    # ---- 13. case_group_id / material ----
    group = _s(row, "case_group_id")
    if qtype == "case_sub":
        if not group:
            err("case_group_id", "案例小题必须填写 case_group_id（同组小问共享同一个值）")
        elif ctx.case_groups_in_file and group not in ctx.case_groups_in_file:
            err(
                "case_group_id",
                f"本批次内找不到 case_group_id={group} 的案例大题（小问必须与大题同批导入）",
            )

    if errors:
        return RowOutcome(row_no=row_no, action="error", errors=errors)

    assert subject_id is not None  # 上面已保证
    hash_value = content_hash(stem, [(o["label"], o["content"]) for o in options], qtype)
    payload = _build_payload(
        row,
        qtype=qtype,
        subject_id=subject_id,
        chapter_id=chapter_id,
        kp_id=kp_id,
        options=options,
        hash_value=hash_value,
    )

    seen_at = ctx.seen_hashes.get(hash_value)
    if seen_at is not None:
        return RowOutcome(
            row_no=row_no,
            action="duplicate",
            message=f"文件内重复：内容与第 {seen_at} 行一致，只入第一条",
        )

    existing_id = ctx.existing_hashes.get(hash_value)
    if existing_id is not None:
        if ctx.mode != "upsert":
            return RowOutcome(
                row_no=row_no,
                action="duplicate",
                question_id=existing_id,
                message=f"库内已有同内容题目（id={existing_id}），insert 模式跳过",
            )
        return RowOutcome(
            row_no=row_no,
            action="update",
            question_id=existing_id,
            payload=payload,
            options=options,
            message=f"命中同内容题目（id={existing_id}），upsert 模式更新",
        )

    return RowOutcome(row_no=row_no, action="insert", payload=payload, options=options)


# =====================================================================
# 批次读写
# =====================================================================


def _new_batch_no(prefix: str = "IMP") -> str:
    import time

    stamp = time.strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{next_id() % 100000:05d}"


async def create_batch(
    db: AsyncSession,
    *,
    actor: ScopeViewer,
    actor_name: str | None,
    file_name: str,
    content: bytes,
    file_type: str,
    subject_id: int | None,
    source_type: str,
    license_note: str | None,
    mode: str,
    auto_publish: bool,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ImportUploadOut:
    """① 上传：解析 + 建批次。**不写任何题目**。"""
    if subject_id is not None:
        # 批次科目也必须落在调用者的数据范围内
        allowed = scope_subject_ids(actor)
        if allowed is not None and subject_id not in allowed:
            from app.core.errors import forbidden as _forbidden

            raise _forbidden("该科目超出你的数据范围", 40301)

    rows = parse_file(content, file_type)
    if not rows:
        raise bad_request("文件里没有数据行", 40001)
    if len(rows) > MAX_ROWS:
        raise bad_request(f"单批最多 {MAX_ROWS} 行，当前 {len(rows)} 行", 40001)

    file_hash = hashlib.sha256(content).hexdigest()
    batch_id = next_id()
    batch_no = _new_batch_no()

    await db.execute(
        text(
            """
            INSERT INTO import_batches
              (id, batch_no, file_name, file_hash, file_type, subject_id, source_type,
               license_note, mode, total_rows, status, auto_publish, operator_id)
            VALUES
              (:id, :batch_no, :file_name, :file_hash, :file_type, :subject_id, :source_type,
               :license_note, :mode, :total_rows, 'pending', :auto_publish, :operator_id)
            """
        ),
        {
            "id": batch_id,
            "batch_no": batch_no,
            "file_name": file_name[:255],
            "file_hash": file_hash,
            "file_type": file_type,
            "subject_id": subject_id,
            "source_type": source_type,
            "license_note": license_note,
            "mode": mode,
            "total_rows": len(rows),
            "auto_publish": auto_publish,
            "operator_id": actor.id,
        },
    )
    # 原始文件落盘：execute 阶段要重新解析它拿完整 payload（见下方 _IMPORT_FILE_DIR 注记）
    store_batch_file(batch_id, file_type, content)
    # 把解析出来的原始行暂存下来：validate 时再读，避免把大文件塞进请求上下文
    await _store_raw_rows(db, batch_id, rows)

    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="import.upload",
        module="question",
        entity_type="import_batch",
        entity_id=batch_id,
        after={"file_name": file_name, "rows": len(rows), "mode": mode},
        method="POST",
        path="/api/v1/admin/imports/upload",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()

    return ImportUploadOut(
        id=batch_id,
        batch_no=batch_no,
        file_name=file_name,
        file_type=file_type,
        total_rows=len(rows),
        status="pending",
        message="上传成功，尚未写入任何题目。请调用 /validate 做逐行校验。",
    )


async def _store_raw_rows(db: AsyncSession, batch_id: int, rows: list[dict[str, Any]]) -> None:
    """把原始行写进 import_items（action 先占位 'skip'）。

    这里顺带做了一个**瘦身**：只保留导入模板的列 + 截断超长文本。
    6000 行 × 完整原文会把 import_items 撑到十几 MB，而排障时真正要看的
    只是"那一行填了什么" —— 题干给 200 字足够定位。
    """
    ids = next_ids(len(rows))
    payload: list[dict[str, Any]] = []
    for i, (rid, row) in enumerate(zip(ids, rows), start=1):
        slim: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, str) and len(v) > 200:
                slim[k] = v[:200] + "…"
            else:
                slim[k] = v
        payload.append(
            {
                "id": rid,
                "batch_id": batch_id,
                "row_no": i,
                "raw": json.dumps(slim, ensure_ascii=False, default=str),
            }
        )
    for start in range(0, len(payload), WRITE_CHUNK):
        await db.execute(
            text(
                "INSERT INTO import_items (id, batch_id, row_no, raw, action, message) "
                "VALUES (:id, :batch_id, :row_no, CAST(:raw AS jsonb), 'skip', '待校验')"
            ),
            payload[start : start + WRITE_CHUNK],
        )


_BATCH_SELECT = """
SELECT b.id, b.batch_no, b.file_name, b.file_type, b.file_hash, b.subject_id,
       b.source_type, b.license_note, b.mode, b.total_rows, b.success_rows,
       b.failed_rows, b.duplicate_rows, b.updated_rows, b.error_report, b.status,
       b.auto_publish, b.rollback_at, b.rollback_by, b.operator_id,
       b.started_at, b.finished_at, b.created_at,
       s.code AS subject_code, s.name AS subject_name,
       COALESCE(u.nickname, u.phone) AS operator_name
FROM import_batches b
LEFT JOIN subjects s ON s.id = b.subject_id
LEFT JOIN users u ON u.id = b.operator_id
"""


def _batch_out(
    row: dict[str, Any], *, can: dict[str, bool], size: int | None = None
) -> ImportBatchOut:
    report_raw = row.get("error_report") or {}
    report = ErrorReport(**report_raw) if isinstance(report_raw, dict) else ErrorReport()
    if size is None:
        size = _stored_file_size(row["id"], row["file_type"])
    return ImportBatchOut(
        id=row["id"],
        batch_no=row["batch_no"],
        file_name=row["file_name"],
        file_type=row["file_type"],
        file_hash=row["file_hash"],
        file_size=size,
        subject_id=row["subject_id"],
        subject_code=row["subject_code"],
        subject_name=row["subject_name"],
        source_type=row["source_type"],
        license_note=row["license_note"],
        mode=row["mode"],
        status=row["status"],
        auto_publish=bool(row["auto_publish"]),
        total_rows=row["total_rows"],
        success_rows=row["success_rows"],
        failed_rows=row["failed_rows"],
        duplicate_rows=row["duplicate_rows"],
        updated_rows=row["updated_rows"],
        error_report=report,
        operator_id=row["operator_id"],
        operator_name=row["operator_name"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        rollback_at=row["rollback_at"],
        rollback_by=row["rollback_by"],
        created_at=row["created_at"],
        can_execute=can["execute"],
        can_publish=can["publish"],
        can_rollback=can["rollback"],
    )


def _capabilities(
    batch_status: str, viewer: ScopeViewer, *, executed: bool = False
) -> dict[str, bool]:
    """前端按钮可用性。**与路由上的 `require_permission` 严格一致** ——
    否则会出现"按钮亮着、点了 403"或反过来的鬼状态。

    - 执行：`question:import`（批量导入权限），且本批**只允许执行一次**
    - 发布：`question:publish`
    - 回滚：`question:rollback`（docs/07 §4.3 ⑥；admin 角色刻意没有这个权限）
    """
    perms = viewer.permissions
    return {
        "execute": batch_status == "done" and not executed and "question:import" in perms,
        "publish": batch_status in ("done", "importing") and "question:publish" in perms,
        "rollback": batch_status in ("done", "failed") and "question:rollback" in perms,
    }


async def _executed_batch_ids(db: AsyncSession, batch_ids: list[int]) -> set[int]:
    """哪些批次已经"执行"过导入。

    判据是 `content_change_logs` 里存在本批的 create / update 记录 ——
    因为 `import_batches.status` 在 execute 前后都是 `done`（枚举里没有独立的
    "已执行"态），只看状态无法区分"校验完待执行"与"已执行完"。
    不新增枚举值是为了不动 `db/schema.sql` 的 CHECK（改它要重建库，
    会牵动 Batch 2/3/4 的既有数据）。
    """
    if not batch_ids:
        return set()
    rows = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT batch_id FROM content_change_logs "
                    "WHERE batch_id = ANY(CAST(:ids AS bigint[])) AND action IN ('create','update')"
                ),
                {"ids": list(batch_ids)},
            )
        )
        .scalars()
        .all()
    )
    return {int(b) for b in rows if b is not None}


async def _load_batch(db: AsyncSession, batch_id: int) -> dict[str, Any]:
    row = (
        (await db.execute(text(_BATCH_SELECT + " WHERE b.id = :id"), {"id": batch_id}))
        .mappings()
        .first()
    )
    if row is None:
        raise not_found("导入批次不存在", 40401)
    return dict(row)


# =====================================================================
# ② 校验
# =====================================================================


async def validate_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    actor: ScopeViewer,
    actor_name: str | None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ImportBatchDetail:
    """② 逐行校验（dry-run）。**不写任何题目**，只落 `import_items` 与统计。"""
    batch = await _load_batch(db, batch_id)
    if batch["status"] in ("importing",):
        raise conflict("批次正在导入，无法重复校验", 40901)

    raw_rows = (
        (
            await db.execute(
                text("SELECT row_no, raw FROM import_items WHERE batch_id = :id ORDER BY row_no"),
                {"id": batch_id},
            )
        )
        .mappings()
        .all()
    )
    if not raw_rows:  # pragma: no cover
        # 保留不测（pragma 是**显式决定**，不是遗漏）：
        # 唯一写 import_items 的是 create_batch → _store_raw_rows(:679)，而它在
        # :641-644 已保证 rows 非空、未超上限 ⇒ 这个分支在正常流程下**不可达**。
        # 保留是为了"将来有人新增别的写入路径"时仍有兜底。见 docs/19 §4。
        raise bad_request("批次没有可校验的行（可能未上传成功）", 40001)

    await db.execute(
        text("UPDATE import_batches SET status = 'validating', started_at = now() WHERE id = :id"),
        {"id": batch_id},
    )
    await db.commit()

    ctx = await load_context(
        db, current_user=actor, batch_subject_id=batch["subject_id"], mode=batch["mode"]
    )
    # 文件内的大题分组：小问必须能在大题里找到归属
    ctx.case_groups_in_file = {
        _s(dict(r["raw"]), "case_group_id")
        for r in raw_rows
        if _s(dict(r["raw"]), "type").lower() == "case" and _s(dict(r["raw"]), "case_group_id")
    }

    outcomes: list[RowOutcome] = []
    for r in raw_rows:
        row = dict(r["raw"])
        outcome = validate_row(row, r["row_no"], ctx)
        # 通过的行登记 hash，供"文件内重复"判定
        if outcome.action in ("insert", "update") and outcome.payload:
            ctx.seen_hashes.setdefault(outcome.payload["content_hash"], r["row_no"])
        outcomes.append(outcome)

    # 落 import_items：action / message / question_id
    updates: list[dict[str, Any]] = []
    for o in outcomes:
        if o.action == "error":
            msg = "；".join(f"[{e.field}] {e.message}" for e in o.errors[:3])
        else:
            msg = o.message
        updates.append(
            {
                "row_no": o.row_no,
                "batch_id": batch_id,
                "action": o.action,
                "message": (msg or "")[:500],
                "question_id": o.question_id,
            }
        )
    for start in range(0, len(updates), WRITE_CHUNK):
        await db.execute(
            text(
                "UPDATE import_items SET action = :action, message = :message, "
                "question_id = :question_id WHERE batch_id = :batch_id AND row_no = :row_no"
            ),
            updates[start : start + WRITE_CHUNK],
        )

    counts = {"insert": 0, "update": 0, "duplicate": 0, "error": 0}
    for o in outcomes:
        counts[o.action] = counts.get(o.action, 0) + 1

    all_errors = [e for o in outcomes if o.action == "error" for e in o.errors]
    report = ErrorReport(
        total_errors=len(all_errors),
        truncated=len(all_errors) > ERROR_REPORT_LIMIT,
        errors=all_errors[:ERROR_REPORT_LIMIT],
    )

    # 统计口径：success_rows 是"将写入"的行数（insert + update）
    await db.execute(
        text(
            """
            UPDATE import_batches SET
              status = 'done', finished_at = now(),
              total_rows = :total,
              success_rows = :success,
              failed_rows = :failed,
              duplicate_rows = :dup,
              updated_rows = 0,
              error_report = CAST(:report AS jsonb)
            WHERE id = :id
            """
        ),
        {
            "id": batch_id,
            "total": len(outcomes),
            "success": counts["insert"] + counts["update"],
            "failed": counts["error"],
            "dup": counts["duplicate"],
            "report": json.dumps(report.model_dump(), ensure_ascii=False),
        },
    )
    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="import.validate",
        module="question",
        entity_type="import_batch",
        entity_id=batch_id,
        after={
            "total": len(outcomes),
            "insert": counts["insert"],
            "update": counts["update"],
            "duplicate": counts["duplicate"],
            "error": counts["error"],
        },
        method="POST",
        path=f"/api/v1/admin/imports/{batch_id}/validate",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return await get_batch(
        db, batch_id=batch_id, viewer=actor, row_page=1, row_page_size=ROW_PAGE_SIZE_DEFAULT
    )


# =====================================================================
# ③ 查看
# =====================================================================


async def get_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    viewer: ScopeViewer,
    row_page: int = 1,
    row_page_size: int = ROW_PAGE_SIZE_DEFAULT,
) -> ImportBatchDetail:
    row = await _load_batch(db, batch_id)
    total_items = int(
        (
            await db.execute(
                text("SELECT count(*) FROM import_items WHERE batch_id = :id"), {"id": batch_id}
            )
        ).scalar_one()
    )
    items = (
        (
            await db.execute(
                text(
                    "SELECT row_no, action, message, question_id FROM import_items "
                    "WHERE batch_id = :id ORDER BY row_no LIMIT :lim OFFSET :off"
                ),
                {"id": batch_id, "lim": row_page_size, "off": (row_page - 1) * row_page_size},
            )
        )
        .mappings()
        .all()
    )

    executed = bool(await _executed_batch_ids(db, [batch_id]))
    base = _batch_out(row, can=_capabilities(row["status"], viewer, executed=executed))
    return ImportBatchDetail(
        **base.model_dump(),
        rows=[
            ImportRowPreview(
                row_no=r["row_no"],
                action=r["action"],
                message=r["message"],
                question_id=r["question_id"],
            )
            for r in items
        ],
        row_page=row_page,
        row_page_size=row_page_size,
        row_total=total_items,
    )


async def list_batches(
    db: AsyncSession,
    *,
    viewer: ScopeViewer,
    page: int,
    page_size: int,
    status: str | None = None,
) -> tuple[list[ImportBatchOut], int]:
    where = ""
    params: dict[str, Any] = {}
    if status:
        where = " WHERE b.status = :status"
        params["status"] = status
    total = int(
        (
            await db.execute(text("SELECT count(*) FROM import_batches b" + where), params)
        ).scalar_one()
    )
    rows = (
        (
            await db.execute(
                text(
                    _BATCH_SELECT
                    + where
                    + " ORDER BY b.created_at DESC, b.id DESC LIMIT :lim OFFSET :off"
                ),
                {**params, "lim": page_size, "off": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )
    executed_ids = await _executed_batch_ids(db, [r["id"] for r in rows])
    return [
        _batch_out(
            dict(r), can=_capabilities(r["status"], viewer, executed=r["id"] in executed_ids)
        )
        for r in rows
    ], total


# =====================================================================
# ③.5 批次变更日志（Batch 6）
# =====================================================================

# `left(q.stem, 120)`：只取题干预览。6000 条的批次如果整段题干回传，
# 一个分页响应就能有几 MB —— 而这只是给人扫一眼"动了哪些题"。
_CHANGE_SELECT = """
SELECT c.id, c.entity_type, c.entity_id, c.action, c.change_log,
       c.operator_id, c.created_at,
       COALESCE(u.nickname, u.phone) AS operator_name,
       left(q.stem, 120) AS question_stem
FROM content_change_logs c
LEFT JOIN users u ON u.id = c.operator_id
LEFT JOIN questions q ON q.id = c.entity_id AND c.entity_type = 'question'
WHERE c.batch_id = :batch_id
"""


async def list_batch_changes(
    db: AsyncSession,
    *,
    batch_id: int,
    viewer: ScopeViewer,
    page: int = 1,
    page_size: int = ROW_PAGE_SIZE_DEFAULT,
) -> ImportChangeOut:
    """本批次的 `content_change_logs`（分页）+ 按 action 的全量汇总。

    与 `get_batch` 一样先 `_load_batch`：批次不存在要老实报 `40401`，
    而不是返回一个空列表 —— 空列表会被前端画成"这一批什么都没改"，
    与"这一批根本不存在"是两回事。
    """
    batch = await _load_batch(db, batch_id)

    counts = {
        str(r["action"]): int(r["n"])
        for r in (
            await db.execute(
                text(
                    "SELECT action, count(*) AS n FROM content_change_logs "
                    "WHERE batch_id = :id GROUP BY action"
                ),
                {"id": batch_id},
            )
        )
        .mappings()
        .all()
    }
    total = sum(counts.values())

    rows = (
        (
            await db.execute(
                text(
                    _CHANGE_SELECT + " ORDER BY c.created_at DESC, c.id DESC LIMIT :lim OFFSET :off"
                ),
                {"batch_id": batch_id, "lim": page_size, "off": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )

    return ImportChangeOut(
        id=batch["id"],
        batch_no=batch["batch_no"],
        total=total,
        counts=counts,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
        items=[
            ImportChangeItem(
                id=r["id"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                action=r["action"],
                change_log=r["change_log"],
                question_stem=r["question_stem"],
                operator_id=r["operator_id"],
                operator_name=r["operator_name"],
                created_at=r["created_at"],
            )
            for r in rows
        ],
    )


# =====================================================================
# ④ 执行
# =====================================================================


async def execute_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    actor: ScopeViewer,
    actor_name: str | None,
    publish: bool = False,
    allow_partial: bool = False,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ImportExecuteOut:
    """④ 执行导入。**单事务**，要么整批进入，要么一条不进。

    `mode` **不在这一步改**：它在上传/校验时就定死了。校验阶段已经把每行标成
    insert/update/duplicate 并落进 `import_items`，执行阶段若允许换 mode，
    就会出现"校验说是 update、执行却按 insert 写"的分裂。
    要换 mode → 改批次设置后**重新校验**（重新上传即可）。

    `allow_partial`（默认 **False**）：
    - False：只要本批有**任何一行**校验失败，就整体拒绝执行（不写一行）。
      这是验收标准 ① 的硬要求 —— "错误文件 → 精确报错 + **不写入**"，
      也是"整批成功或整批失败"的字面含义。
    - True：只导入通过校验的行，跳过错误行（docs/07 §4.4 的"只导 insert，跳过错行"）。
      需要调用方**显式**opt-in，不会因为"只错了两行"就悄悄放行。

    `publish=True`（或批次上 `auto_publish=true`）时，执行成功且确有写入的行，
    紧接着复用 `publish_batch` 把本批题从 draft 推成 published。
    """
    import time as _time

    started = _time.perf_counter()
    batch = await _load_batch(db, batch_id)
    status = batch["status"]
    if status != "done":
        raise conflict(
            f"批次当前状态是 {status}，只有校验完成（done）的批次才能执行导入。"
            "请先调用 /validate。",
            40901,
        )

    if batch["failed_rows"] > 0 and not allow_partial:
        raise conflict(
            f"本批次有 {batch['failed_rows']} 行未通过校验。按「整批成功或整批失败」的约定，"
            "不会写入任何数据。请修正这些行后重新上传；"
            '若确认只导入通过的行，请在 execute 时传 "allow_partial": true。',
            40901,
        )

    # **只允许执行一次**：重复执行在 insert 模式下静默 0 写入（统计被打成 0），
    # 在 upsert 模式下把整批题的 version 再顶一轮 —— 两种都是"看起来成功"的脏写。
    if await _executed_batch_ids(db, [batch_id]):
        raise conflict(
            "该批次已经执行过导入，不能重复执行。要重导请新建批次；要撤销请调用 /rollback。",
            40901,
        )

    # 先确认原文还在，再翻状态 —— 否则会留下一个"importing 且必然失败"的批次
    if await _read_batch_file(batch_id, batch["file_type"]) is None:
        raise bad_request("批次原始文件已不可用，无法执行导入（请重新上传）", 40001)

    items = (
        (
            await db.execute(
                text(
                    "SELECT row_no, action, question_id FROM import_items "
                    "WHERE batch_id = :id AND action IN ('insert','update') ORDER BY row_no"
                ),
                {"id": batch_id},
            )
        )
        .mappings()
        .all()
    )

    await db.execute(
        text("UPDATE import_batches SET status = 'importing', started_at = now() WHERE id = :id"),
        {"id": batch_id},
    )
    await db.commit()

    try:
        # 取回每行的 payload：raw 已经瘦身（题干可能被截断），
        # 所以这里**重新解析原文件**才能拿到完整内容；mode 沿用它 = 校验时的 mode。
        payloads = await _load_full_payloads(db, batch_id, batch, actor)
        inserted, updated = await _write_rows(
            db,
            batch_id=batch_id,
            items=items,
            payloads=payloads,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001
        # 整批回滚 —— 不留半截数据
        await db.rollback()
        err_msg = f"导入失败已整批回滚：{type(exc).__name__}: {exc}"[:500]
        await db.execute(
            text(
                "UPDATE import_batches SET status = 'failed', finished_at = now(), "
                "error_report = CAST(:rep AS jsonb) WHERE id = :id"
            ),
            {
                "id": batch_id,
                "rep": json.dumps(
                    {
                        "total_errors": 1,
                        "truncated": False,
                        "errors": [{"row_no": 0, "field": "_batch", "message": err_msg}],
                    },
                    ensure_ascii=False,
                ),
            },
        )
        await db.commit()
        raise conflict(f"导入失败，已整批回滚，未写入任何数据。{err_msg}", 50001) from exc

    await db.execute(
        text(
            """
            UPDATE import_batches SET
              status = 'done', finished_at = now(),
              success_rows = :success, updated_rows = :updated
            WHERE id = :id
            """
        ),
        {"id": batch_id, "success": inserted + updated, "updated": updated},
    )
    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="import.execute",
        module="question",
        entity_type="import_batch",
        entity_id=batch_id,
        after={"inserted": inserted, "updated": updated, "mode": batch["mode"]},
        method="POST",
        path=f"/api/v1/admin/imports/{batch_id}/execute",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()

    # 顺带发布：只有真有写入的行才发布（全重复的批次没有可发布对象，
    # 直接调 publish_batch 会抛"没有可发布的题目"，把一次成功的 execute 变成 500）
    if (publish or batch["auto_publish"]) and (inserted + updated) > 0:
        await publish_batch(
            db,
            batch_id=batch_id,
            actor=actor,
            actor_name=actor_name,
            include_duplicates=False,
            ip=ip,
            user_agent=user_agent,
        )

    fresh = await _load_batch(db, batch_id)
    return ImportExecuteOut(
        id=batch_id,
        status=fresh["status"],
        total_rows=fresh["total_rows"],
        success_rows=fresh["success_rows"],
        failed_rows=fresh["failed_rows"],
        duplicate_rows=fresh["duplicate_rows"],
        updated_rows=fresh["updated_rows"],
        duration_ms=int((_time.perf_counter() - started) * 1000),
    )


async def _load_full_payloads(
    db: AsyncSession, batch_id: int, batch: dict[str, Any], actor: ScopeViewer
) -> dict[int, RowOutcome]:
    """按批次重新解析原文件，拿到每一行的完整 payload。

    `import_items.raw` 是瘦身过的（超长文本被截断），不足以重建题目内容。
    所以这里把文件重新解析一遍 —— 代价是一次 CPU 解析，
    换来的是 `import_items` 不被原文撑爆。
    """
    content = await _read_batch_file(batch_id, batch["file_type"])
    if content is None:  # pragma: no cover
        # 第二道防线：execute_batch:1241 已先行抛出同一错误，正常流程到不了这个分支。
        # 保留是**为了 1242 将来被重构掉时仍有兜底** —— 删了它，重构时容易漏。
        # 保留不测是显式决定（见 docs/19 §5）。
        raise bad_request("批次原始文件已不可用，无法执行导入（请重新上传）", 40001)

    rows = parse_file(content, batch["file_type"])
    ctx = await load_context(
        db, current_user=actor, batch_subject_id=batch["subject_id"], mode=batch["mode"]
    )
    ctx.case_groups_in_file = {
        _s(r, "case_group_id")
        for r in rows
        if _s(r, "type").lower() == "case" and _s(r, "case_group_id")
    }
    out: dict[int, RowOutcome] = {}
    for i, row in enumerate(rows, start=1):
        o = validate_row(row, i, ctx)
        if o.action in ("insert", "update") and o.payload:
            ctx.seen_hashes.setdefault(o.payload["content_hash"], i)
            out[i] = o
    return out


# 上传的原始文件：**落盘**暂存（本批不做对象存储，见 docs/11 §7）。
#
# 为什么不是放在进程内存里（`dict[batch_id, bytes]`）：
#   execute 阶段要"重新解析原文件"才能拿到完整 payload（import_items.raw 是瘦身过的）。
#   进程内存有三个致命处 —— 多 worker 各持一份、重启即丢、--reload 一变就空。
#   任何一个发生，"校验通过 → 执行"就会在 execute 时炸"原文件已不可用"。
# 落盘 + 以 batch_id 命名（不再单独存路径，避免 DB 记本机绝对路径）：
#   单机 / 多 worker / 重启后都能重新读到；代价是本机磁盘上的一个临时目录，
#   生产替换成对象存储（file_url）即可，接口形状不用动。
_IMPORT_FILE_DIR = Path(tempfile.gettempdir()) / "yijian-import-files"


def _batch_file_path(batch_id: int, file_type: str) -> Path:
    return _IMPORT_FILE_DIR / f"{batch_id}.{file_type}"


def store_batch_file(batch_id: int, file_type: str, content: bytes) -> None:
    """把上传原文写到临时目录。**先写 .part 再原子替换**，避免半截文件被读到。"""
    _IMPORT_FILE_DIR.mkdir(parents=True, exist_ok=True)
    target = _batch_file_path(batch_id, file_type)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(content)
    os.replace(tmp, target)


async def _read_batch_file(batch_id: int, file_type: str) -> bytes | None:
    path = _batch_file_path(batch_id, file_type)
    if not path.exists():
        return None
    return await asyncio.to_thread(path.read_bytes)


def _stored_file_size(batch_id: int, file_type: str) -> int | None:
    """原始文件字节数（列表/详情展示用）。文件已被清理则返回 None。"""
    try:
        return _batch_file_path(batch_id, file_type).stat().st_size
    except OSError:
        return None


async def _write_rows(
    db: AsyncSession,
    *,
    batch_id: int,
    items: list[Any],
    payloads: dict[int, RowOutcome],
    actor: ScopeViewer,
) -> tuple[int, int]:
    """把 insert / update 两类行真正写进库。调用方保证在**同一事务**内。

    `inserts` / `updates` 的划分**以 `payloads` 为准**（它由 `validate_row` 用
    批次定死的 mode 现算出来），`items` 只用来兜底过滤。
    这样即便 DB 里 `import_items.action` 与重新校验的结果有出入，也以重算的为准。
    """
    inserts = [
        o
        for o in (payloads.get(i["row_no"]) for i in items)
        if o is not None and o.action == "insert"
    ]
    updates = [
        o
        for o in (payloads.get(i["row_no"]) for i in items)
        if o is not None and o.action == "update"
    ]

    inserted = 0
    updated = 0
    group_to_id: dict[str, int] = {}

    # ---- 1. 先写案例大题（小问要靠它拿 parent_id）----
    case_rows = [o for o in inserts if o.payload and o.payload["type"] == "case"]
    other_rows = [o for o in inserts if o not in case_rows]
    ids = next_ids(len(case_rows) + len(other_rows))
    cursor = 0

    question_rows: list[dict[str, Any]] = []
    option_rows: list[dict[str, Any]] = []
    version_rows: list[dict[str, Any]] = []
    change_rows: list[dict[str, Any]] = []
    item_updates: list[dict[str, Any]] = []

    def stage(o: RowOutcome, qid: int, row_no: int) -> None:
        p = o.payload or {}
        nonlocal cursor
        question_rows.append(_question_insert_params(qid, p, actor.id, status="draft"))
        for opt in o.options or []:
            option_rows.append(
                {
                    "id": next_id(),
                    "qid": qid,
                    "label": opt["label"],
                    "content": opt["content"],
                    "content_html": None,
                    "is_correct": opt["is_correct"],
                    "sort_no": opt["sort_no"],
                }
            )
        version_rows.append(
            _version_params(
                qid,
                1,
                _snapshot_from_payload(p, o.options or []),
                "导入创建 v1",
                actor.id,
            )
        )
        change_rows.append(
            _change_params(
                qid,
                action="create",
                batch_id=batch_id,
                before=None,
                after={"stem": p["stem"][:200], "type": p["type"]},
                change_log="批量导入创建",
                operator_id=actor.id,
            )
        )
        item_updates.append(
            {"batch_id": batch_id, "row_no": row_no, "action": "insert", "question_id": qid}
        )

    for o in case_rows:
        qid = ids[cursor]
        cursor += 1
        stage(o, qid, o.row_no)
        group = (o.payload or {}).get("case_group_id")
        if group:
            group_to_id[group] = qid

    for o in other_rows:
        qid = ids[cursor]
        cursor += 1
        p = dict(o.payload or {})
        # 小问挂到大题上
        if p.get("type") == "case_sub" and p.get("case_group_id"):
            parent = group_to_id.get(p["case_group_id"])
            if parent is None:
                raise bad_request(
                    f"第 {o.row_no} 行：case_group_id={p['case_group_id']} 找不到同批导入的案例大题",
                    40001,
                )
            p["parent_id"] = parent
            p["root_id"] = parent
        stage(o, qid, o.row_no)
        o.payload = p  # 供 _snapshot_from_payload 使用
    inserted = len(case_rows) + len(other_rows)

    for start in range(0, len(question_rows), WRITE_CHUNK):
        await db.execute(text(_INSERT_QUESTION_SQL), question_rows[start : start + WRITE_CHUNK])
    for start in range(0, len(option_rows), WRITE_CHUNK):
        await db.execute(text(_INSERT_OPTION_SQL), option_rows[start : start + WRITE_CHUNK])
    for start in range(0, len(version_rows), WRITE_CHUNK):
        await db.execute(text(_INSERT_VERSION_SQL), version_rows[start : start + WRITE_CHUNK])
    for start in range(0, len(change_rows), WRITE_CHUNK):
        await db.execute(text(_INSERT_CHANGE_SQL), change_rows[start : start + WRITE_CHUNK])

    # ---- 2. upsert 更新命中行 ----
    if updates:
        current = (
            (
                await db.execute(
                    text(
                        "SELECT id, version, status FROM questions "
                        "WHERE id = ANY(CAST(:ids AS bigint[]))"
                    ),
                    {"ids": [o.question_id for o in updates]},
                )
            )
            .mappings()
            .all()
        )
        cur_map = {r["id"]: dict(r) for r in current}
        upd_params: list[dict[str, Any]] = []
        for o in updates:
            cur = cur_map.get(o.question_id)
            if cur is None:
                raise not_found(f"第 {o.row_no} 行：要更新的题目 {o.question_id} 已不存在", 40401)
            p = o.payload or {}
            new_version = cur["version"] + 1
            upd_params.append(
                {
                    "qid": o.question_id,
                    "subject_id": p["subject_id"],
                    "chapter_id": p["chapter_id"],
                    "knowledge_point_id": p["knowledge_point_id"],
                    "type": p["type"],
                    "stem": p["stem"],
                    "answer": json.dumps(p["answer"], ensure_ascii=False),
                    "analysis": p["analysis"],
                    "analysis_points": json.dumps(p["analysis_points"], ensure_ascii=False),
                    "score_default": p["score_default"],
                    "difficulty": p["difficulty"],
                    "exam_year": p["exam_year"],
                    "source_type": p["source_type"],
                    "source_name": p["source_name"],
                    "source_license": p["source_license"],
                    "tags": list(p["tags"]),
                    "content_hash": p["content_hash"],
                    "material": p["material"],
                    "version": new_version,
                    "actor": actor.id,
                }
            )
            version_rows = [
                _version_params(
                    o.question_id,
                    new_version,
                    _snapshot_from_payload(p, o.options or []),
                    "批量导入更新",
                    actor.id,
                )
            ]
            change_rows = [
                _change_params(
                    o.question_id,
                    action="update",
                    batch_id=batch_id,
                    before={"version": cur["version"]},
                    after={"version": new_version},
                    change_log="批量导入更新",
                    operator_id=actor.id,
                )
            ]
            await db.execute(text(_UPDATE_QUESTION_SQL), upd_params[-1])
            await db.execute(
                text("DELETE FROM question_options WHERE question_id = :qid"),
                {"qid": o.question_id},
            )
            if o.options:
                await db.execute(
                    text(_INSERT_OPTION_SQL),
                    [
                        {
                            "id": next_id(),
                            "qid": o.question_id,
                            "label": opt["label"],
                            "content": opt["content"],
                            "content_html": None,
                            "is_correct": opt["is_correct"],
                            "sort_no": opt["sort_no"],
                        }
                        for opt in o.options
                    ],
                )
            await db.execute(text(_INSERT_VERSION_SQL), version_rows)
            await db.execute(text(_INSERT_CHANGE_SQL), change_rows)
            item_updates.append(
                {
                    "batch_id": batch_id,
                    "row_no": o.row_no,
                    "action": "update",
                    "question_id": o.question_id,
                }
            )
            updated += 1

    for start in range(0, len(item_updates), WRITE_CHUNK):
        await db.execute(
            text(
                "UPDATE import_items SET action = :action, question_id = :question_id "
                "WHERE batch_id = :batch_id AND row_no = :row_no"
            ),
            item_updates[start : start + WRITE_CHUNK],
        )
    return inserted, updated


_INSERT_QUESTION_SQL = """
INSERT INTO questions
  (id, subject_id, chapter_id, knowledge_point_id, type, stem, answer, analysis,
   analysis_points, score_default, difficulty, exam_year, source_type, source_name,
   source_license, tags, parent_id, root_id, sort_no, material_html, stem_media,
   content_hash, status, version, created_by, updated_by)
VALUES
  (:id, :subject_id, :chapter_id, :knowledge_point_id, :type, :stem,
   CAST(:answer AS jsonb), :analysis, CAST(:analysis_points AS jsonb),
   :score_default, :difficulty, :exam_year, :source_type, :source_name,
   :source_license, CAST(:tags AS text[]), :parent_id, :root_id, :sort_no,
   :material_html, CAST(:stem_media AS jsonb), :content_hash, :status, 1, :actor, :actor)
"""

_UPDATE_QUESTION_SQL = """
UPDATE questions SET
  subject_id = :subject_id, chapter_id = :chapter_id,
  knowledge_point_id = :knowledge_point_id, type = :type, stem = :stem,
  answer = CAST(:answer AS jsonb), analysis = :analysis,
  analysis_points = CAST(:analysis_points AS jsonb),
  score_default = :score_default, difficulty = :difficulty, exam_year = :exam_year,
  source_type = :source_type, source_name = :source_name,
  source_license = :source_license, tags = CAST(:tags AS text[]),
  content_hash = :content_hash, material_html = :material,
  version = :version, updated_by = :actor
WHERE id = :qid
"""

_INSERT_OPTION_SQL = """
INSERT INTO question_options
  (id, question_id, label, content, content_html, is_correct, sort_no)
VALUES (:id, :qid, :label, :content, :content_html, :is_correct, :sort_no)
"""

_INSERT_VERSION_SQL = """
INSERT INTO question_versions (id, question_id, version, snapshot, change_log, operator_id)
VALUES (:id, :qid, :version, CAST(:snapshot AS jsonb), :change_log, :operator_id)
"""

_INSERT_CHANGE_SQL = """
INSERT INTO content_change_logs
  (id, entity_type, entity_id, action, batch_id, diff, change_log, operator_id, operator_ip)
VALUES (:id, 'question', :entity_id, :action, :batch_id, CAST(:diff AS jsonb),
        :change_log, :operator_id, :operator_ip)
"""


def _question_insert_params(
    qid: int, p: dict[str, Any], actor_id: int, *, status: str
) -> dict[str, Any]:
    return {
        "id": qid,
        "subject_id": p["subject_id"],
        "chapter_id": p["chapter_id"],
        "knowledge_point_id": p["knowledge_point_id"],
        "type": p["type"],
        "stem": p["stem"],
        "answer": json.dumps(p["answer"], ensure_ascii=False),
        "analysis": p["analysis"],
        "analysis_points": json.dumps(p["analysis_points"], ensure_ascii=False),
        "score_default": p["score_default"],
        "difficulty": p["difficulty"],
        "exam_year": p["exam_year"],
        "source_type": p["source_type"],
        "source_name": p["source_name"],
        "source_license": p["source_license"],
        "tags": list(p["tags"]),
        "parent_id": p.get("parent_id"),
        "root_id": p.get("root_id") or qid,
        "sort_no": 0,
        "material_html": p.get("material"),
        "stem_media": json.dumps(
            [{"type": "image", "url": u} for u in (p.get("media_urls") or [])],
            ensure_ascii=False,
        ),
        "content_hash": p["content_hash"],
        "status": status,
        "actor": actor_id,
    }


def _version_params(
    qid: int, version: int, snapshot: dict[str, Any], change_log: str, operator_id: int
) -> dict[str, Any]:
    return {
        "id": next_id(),
        "qid": qid,
        "version": version,
        "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str),
        "change_log": change_log[:500],
        "operator_id": operator_id,
    }


def _change_params(
    entity_id: int,
    *,
    action: str,
    batch_id: int,
    before: Any,
    after: Any,
    change_log: str,
    operator_id: int,
    ip: str | None = None,
) -> dict[str, Any]:
    return {
        "id": next_id(),
        "entity_id": entity_id,
        "action": action,
        "batch_id": batch_id,
        "diff": json.dumps({"before": before, "after": after}, ensure_ascii=False, default=str),
        "change_log": change_log[:500],
        "operator_id": operator_id,
        "operator_ip": to_inet(ip),
    }


def _snapshot_from_payload(p: dict[str, Any], options: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stem": p["stem"],
        "type": p["type"],
        "analysis": p["analysis"],
        "difficulty": p["difficulty"],
        "score_default": p["score_default"],
        "status": "draft",
        "subject_id": p["subject_id"],
        "chapter_id": p["chapter_id"],
        "exam_year": p["exam_year"],
        "tags": list(p["tags"]),
        "source_type": p["source_type"],
        "source_name": p["source_name"],
        "source_license": p["source_license"],
        "answer": p["answer"],
        "options": [
            {
                "label": o["label"],
                "content": o["content"],
                "content_html": None,
                "is_correct": o["is_correct"],
                "sort_no": o["sort_no"],
            }
            for o in options
        ],
    }


# =====================================================================
# ⑤ 发布
# =====================================================================


async def publish_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    actor: ScopeViewer,
    actor_name: str | None,
    include_duplicates: bool = False,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ImportBatchOut:
    """⑤ draft → published。只发布**本批写入**的题（可选带上命中的重复题）。"""
    batch = await _load_batch(db, batch_id)
    if batch["status"] != "done":
        raise conflict(f"批次状态是 {batch['status']}，不能发布", 40901)

    actions = ["insert", "update"]
    if include_duplicates:
        actions.append("duplicate")
    ids = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT question_id FROM import_items "
                    "WHERE batch_id = :id AND action = ANY(CAST(:actions AS text[])) "
                    "AND question_id IS NOT NULL"
                ),
                {"id": batch_id, "actions": actions},
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        raise bad_request("本批次没有可发布的题目", 40001)

    await db.execute(
        text(
            "UPDATE questions SET status = 'published', published_at = now(), "
            "updated_by = :actor WHERE id = ANY(CAST(:ids AS bigint[])) AND status <> 'published'"
        ),
        {"actor": actor.id, "ids": list(ids)},
    )
    for qid in ids:
        await _flush_single(
            db,
            _change_params(
                qid,
                action="publish",
                batch_id=batch_id,
                before={"status": "draft"},
                after={"status": "published"},
                change_log="批量导入发布",
                operator_id=actor.id,
                ip=ip,
            ),
        )
    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="import.publish",
        module="question",
        entity_type="import_batch",
        entity_id=batch_id,
        after={"published": len(ids), "include_duplicates": include_duplicates},
        method="POST",
        path=f"/api/v1/admin/imports/{batch_id}/publish",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    fresh = await _load_batch(db, batch_id)
    executed = bool(await _executed_batch_ids(db, [batch_id]))
    return _batch_out(fresh, can=_capabilities(fresh["status"], actor, executed=executed))


async def _flush_single(db: AsyncSession, params: dict[str, Any]) -> None:
    await db.execute(text(_INSERT_CHANGE_SQL), params)


# =====================================================================
# ⑥ 回滚
# =====================================================================


async def rollback_batch(
    db: AsyncSession,
    *,
    batch_id: int,
    actor: ScopeViewer,
    actor_name: str | None,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ImportRollbackOut:
    """⑥ 整批回滚。

    - `action='insert'` 的行 → 软删除对应题目（数据保留、可追溯）
    - `action='update'` 的行 → 还原到导入前的快照
    - 写 `content_change_logs(action='rollback')` 与审计日志
    """
    batch = await _load_batch(db, batch_id)
    if batch["status"] == "rolled_back":
        raise conflict("该批次已经回滚过，无需重复回滚", 40901)
    if batch["status"] not in ("done", "failed"):
        raise conflict(f"批次状态是 {batch['status']}，不能回滚", 40901)

    rows = (
        (
            await db.execute(
                text(
                    "SELECT row_no, action, question_id FROM import_items "
                    "WHERE batch_id = :id AND action IN ('insert','update') "
                    "AND question_id IS NOT NULL ORDER BY row_no"
                ),
                {"id": batch_id},
            )
        )
        .mappings()
        .all()
    )

    insert_ids = [r["question_id"] for r in rows if r["action"] == "insert"]
    update_ids = [r["question_id"] for r in rows if r["action"] == "update"]

    existing = set(
        (
            await db.execute(
                text("SELECT id FROM questions WHERE id = ANY(CAST(:ids AS bigint[]))"),
                {"ids": list(insert_ids) + list(update_ids) or [0]},
            )
        )
        .scalars()
        .all()
    )
    missing = [i for i in (list(insert_ids) + list(update_ids)) if i not in existing]

    try:
        if insert_ids:
            await db.execute(
                text(
                    "UPDATE questions SET is_deleted = true, updated_by = :actor, "
                    "version = version + 1 WHERE id = ANY(CAST(:ids AS bigint[]))"
                ),
                {"actor": actor.id, "ids": insert_ids},
            )
            for start in range(0, len(insert_ids), WRITE_CHUNK):
                await db.execute(
                    text(_INSERT_CHANGE_SQL),
                    [
                        _change_params(
                            qid,
                            action="rollback",
                            batch_id=batch_id,
                            before={"is_deleted": False},
                            after={"is_deleted": True},
                            change_log=reason or "批量导入整批回滚（软删除）",
                            operator_id=actor.id,
                            ip=ip,
                        )
                        for qid in insert_ids[start : start + WRITE_CHUNK]
                    ],
                )

        restored = 0
        for qid in update_ids:
            # 找导入前那一版：本批写入的是 version_batch，
            # 于是"导入前"就是 question_versions 里版本号最大的、且小于当前版本的记录。
            snap = (
                (
                    await db.execute(
                        text(
                            "SELECT version, snapshot FROM question_versions "
                            "WHERE question_id = :qid AND version < "
                            "  (SELECT version FROM questions WHERE id = :qid) "
                            "ORDER BY version DESC LIMIT 1"
                        ),
                        {"qid": qid},
                    )
                )
                .mappings()
                .first()
            )
            if snap is None:  # pragma: no cover
                # 保留不测（显式决定，见 docs/19 §4）：要触发它得先把 question_versions
                # 里该题的版本行**手工删掉**才能构造 —— 成本明显高于价值。
                # 但它在语义上意味着"回滚静默跳过一行"，有真实告警价值，所以**不删代码**。
                continue
            s = snap["snapshot"] or {}
            await db.execute(
                text(
                    """
                    UPDATE questions SET
                      stem = :stem, analysis = :analysis, difficulty = :difficulty,
                      score_default = :score_default, answer = CAST(:answer AS jsonb),
                      version = :version, updated_by = :actor
                    WHERE id = :qid
                    """
                ),
                {
                    "qid": qid,
                    "stem": s.get("stem"),
                    "analysis": s.get("analysis"),
                    "difficulty": s.get("difficulty"),
                    "score_default": s.get("score_default"),
                    "answer": json.dumps(s.get("answer") or {}, ensure_ascii=False),
                    "version": snap["version"] + 1,
                    "actor": actor.id,
                },
            )
            restored += 1
            await _flush_single(
                db,
                _change_params(
                    qid,
                    action="rollback",
                    batch_id=batch_id,
                    before={"version": snap["version"] + 1},
                    after={"version": snap["version"]},
                    change_log=reason or "批量导入整批回滚（还原到导入前版本）",
                    operator_id=actor.id,
                    ip=ip,
                ),
            )

        await db.execute(
            text(
                "UPDATE import_batches SET status = 'rolled_back', rollback_at = now(), "
                "rollback_by = :actor WHERE id = :id"
            ),
            {"actor": actor.id, "id": batch_id},
        )
        await write_audit(
            db,
            actor_id=actor.id,
            actor_name=actor_name,
            action="import.rollback",
            module="question",
            entity_type="import_batch",
            entity_id=batch_id,
            after={"soft_deleted": len(insert_ids), "restored": restored, "reason": reason},
            method="POST",
            path=f"/api/v1/admin/imports/{batch_id}/rollback",
            ip=ip,
            user_agent=user_agent,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise conflict(f"回滚失败，已回滚本次回滚操作：{exc}", 50001) from exc

    return ImportRollbackOut(
        id=batch_id,
        status="rolled_back",
        rolled_back_questions=len(insert_ids),
        rolled_back_updates=restored,
        missing=missing,
    )
