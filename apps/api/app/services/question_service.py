"""题库服务（管理端）。

## 分层注记

本模块**不 import `core.deps`**（那是 fastapi 的地盘，见项目分层铁律）。
数据范围钩子需要"调用者是谁"，于是用 `ScopeViewer` 协议做结构化子类型 ——
`CurrentUser` 天然满足（它有 `id` / `roles` / `permissions`），直接传进来即可。

## 为什么用原生 SQL 而不是 ORM

ORM 只覆盖了 Batch 2 的 8 张表（用户/角色/权限/会话/审计）。
题库的 5 张表走原生 SQL，与 `rbac_service` / `user_service` 的既有做法一致。

## 三个必须讲清的约定

1. **`answer` 由选项推导**（`derive_answer`），不接受单独传入 ——
   这样"答案必须都在选项里"是结构上不可违反的，而不是靠比对去兜。
2. **编辑用乐观锁**：调用方回传 `version`，与库里不一致直接 `40901`，
   不做"覆盖"也不做"自动合并"。B 端多教研同时改一道题时，静默覆盖是最坏的结果。
3. **`is_deleted`（软删除）与 `status='archived'`（业务归档）是两回事**，
   列表开关 `include_deleted` 管的是前者。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field as dc_field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.idgen import next_id
from app.schemas.admin_question import (
    ChapterNode,
    ChapterTreeOut,
    KnowledgePointItem,
    KnowledgePointListOut,
    QuestionBatchDeleteOut,
    QuestionCreateIn,
    QuestionDeleteOut,
    QuestionDetail,
    QuestionListItem,
    QuestionOptionIn,
    QuestionOptionOut,
    QuestionRestoreOut,
    QuestionUpdateIn,
    QuestionVersionItem,
    SubjectBrief,
    SubjectChapterGroup,
    _validate_options,
)
from app.services.audit_service import write_audit

# 列表里题干截断长度。教研要能认出是哪道题，不需要看全文。
STEM_PREVIEW_LEN = 160
# 详情页内联返回的历史版本条数
VERSION_LIMIT = 10
# 本批可编辑的题型（案例题/主观题留到下一批）
EDITABLE_TYPES = ("single", "multiple", "judge")


@runtime_checkable
class ScopeViewer(Protocol):
    """数据范围过滤所需的调用者信息。

    刻意不依赖 `core.deps.CurrentUser`（那会引入 fastapi 依赖），
    只声明钩子真正用得到的属性。
    """

    id: int
    roles: list[str]
    permissions: set[str]
    scopes: list[dict]


# ============================================================ 纯函数（可单测）


def _norm(s: str | None) -> str:
    """内容指纹用的归一化：去标签、去空白、去标点、全角转半角。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u3000", " ")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[\s\u200b]+", "", s)
    s = re.sub(r"[（(]\s*[)）]", "", s)
    s = re.sub(r"[，,。.；;：:！!？?、]", "", s)
    return s.lower()


def content_hash(stem: str, options: list[tuple[str, str]], qtype: str) -> str:
    """归一化内容指纹（SHA-256）。

    与 `db/schema.sql` 的 `uq_questions_hash` 部分唯一索引配套：
    同题干 + 同选项的题在库内只允许存在一条（未删除的）。
    算法来源：`docs/03-数据库设计.md` §「内容指纹 content_hash 与去重」。
    """
    parts = [qtype, _norm(stem)]
    for label, content in sorted(options):
        parts.append(f"{label}:{_norm(content)}")
    return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()


def derive_answer(
    qtype: str,
    options: list[QuestionOptionIn] | list[dict[str, Any]],
    judge_answer: bool | None = None,
) -> dict[str, Any]:
    """从选项推导 `answer` JSONB。

    `single`  → `{"value": ["B"]}`
    `multiple`→ `{"value": ["A","C"], "partial_credit": true}`
    `judge`   → `{"value": [true]}`

    答案**只可能来自选项本身**，所以"多选题答案必须都在选项里"是结构上成立的。
    下面那句子集校验留作廉价的不变量断言 —— 真触发了说明代码被改坏了，值得直接报错。
    """
    if qtype == "judge":
        return {"value": [bool(judge_answer)]}

    labels = [str(o.label if isinstance(o, dict) else o.label).strip().upper() for o in options]
    correct = [
        str(o.label if isinstance(o, dict) else o.label).strip().upper()
        for o in options
        if (o.get("is_correct") if isinstance(o, dict) else o.is_correct)
    ]
    if not set(correct).issubset(set(labels)):
        raise bad_request("答案只能来自选项标号本身", 40001)

    if qtype == "single":
        return {"value": correct}
    return {"value": correct, "partial_credit": True}


def _normalize_options(options: list[QuestionOptionIn]) -> list[tuple[str, str, bool, str | None]]:
    """统一大写标号并补齐 sort_no 顺序，返回 (label, content, is_correct, content_html)。"""
    out = []
    for i, o in enumerate(options):
        out.append((o.label.strip().upper(), o.content, bool(o.is_correct), o.content_html))
    return out


# ============================================================ 数据范围钩子


@dataclass
class QuestionQuery:
    """列表查询的可变装配体（WHERE 片段 + 参数）。

    存在的唯一理由是给 `apply_data_scope` 一个**稳定的落点**：
    `list_questions` 一定会把装配好的 query 交给钩子，
    下一批补数据范围过滤时，改动面只在钩子内部这一处。
    """

    where: list[str] = dc_field(default_factory=list)
    params: dict[str, Any] = dc_field(default_factory=dict)

    def add(self, cond: str, **params: Any) -> "QuestionQuery":
        self.where.append(cond)
        self.params.update(params)
        return self


def scope_subject_ids(current_user: ScopeViewer) -> set[int] | None:
    """调用者可见的 `subject_id` 集合；`None` 表示**不限制**。

    这是数据范围的**单一事实源**：列表查询（`apply_data_scope`）与
    导入校验（`import_service`）都调它，避免两处各写一套过滤规则而慢慢漂移。

    读的是 `user_roles.scope_type / scope_id`（经 `CurrentUser.scopes` 传进来）：

    | scope_type | 含义 | 本批处理 |
    |---|---|---|
    | `global` | 全站 | 返回 `None`（不限制） |
    | `subject` | 限定科目，`scope_id = subjects.id` | 收进集合 |
    | `professional` | 限定专业 | **暂未映射**，按"失败关闭"处理 |
    | `course` | 限定课程 | **暂未映射**，按"失败关闭"处理 |

    **为什么 professional 是"失败关闭"而不是"不限制"**：
    数据范围是**合规属性**（教研不该看到/改动其它专业的题），
    猜错方向宁可少放行。`subjects` 表里"专业"其实就是一个科目
    （`category='professional'`, `professional='sz'` 之类），
    所以眼下用 `subject` 范围就能精确表达"只能动自己专业的题"，
    `professional` 的数值映射留到真正需要跨科目专业分组时再做。

    多条范围取**并集**；只要有一条 `global`，就直接不限制。
    """
    scopes = list(getattr(current_user, "scopes", None) or [])
    if not scopes:
        # 没有任何范围记录 —— 实际上不可达：没有角色就没有权限，早就 403 了。
        return None
    if any((s.get("scope_type") or "").lower() == "global" for s in scopes):
        return None

    ids: set[int] = set()
    for s in scopes:
        if (s.get("scope_type") or "").lower() == "subject" and s.get("scope_id") is not None:
            ids.add(int(s["scope_id"]))
    if ids:
        return ids
    # 只挂了 professional / course 范围，本批还没有映射规则 → 收敛到空集（失败关闭）
    return set()


def ensure_subject_visible(viewer: ScopeViewer, subject_id: int, what: str = "该科目") -> None:
    """科目的**准入闸**：不在数据范围内就 `40301`。与 `scope_subject_ids` 同一份判定。

    ## 为什么必须有它（数据范围收口的由来）

    收口前只有 `list_questions` 套了范围过滤 —— **列表看不见，但照着 id 直接请求
    就能读到、改到、删掉别的科目的题**。"列表过滤"防的是"翻到"，防不了"猜到"。
    凡是**按 id 寻址**的入口都得自己再拦一次，否则等于"知道 id 就能越权"。

    ## 语义

    - `allowed is None`（`global` 范围）→ 放行；
    - `allowed` 为空集（只挂了未映射的 `professional` / `course`）→ **全拒绝**
      （失败关闭，理由见 `scope_subject_ids`）；
    - 否则 `subject_id` 必须落在集合里。

    ## 为什么报 40301 而不是 40401

    对象**确实存在**，只是不归你。报 404 会让排查的人以为 id 写错了，
    去找一个并不存在的问题，反而绕远路。（这也与硬约定 A 划清界限：
    那条说的是"只读函数**夹带**比调用方更严的准入"；本条是**准入判据本身**，
    读路径与写路径**共用同一份**，不会出现上下层不一致。）
    """
    allowed = scope_subject_ids(viewer)
    if allowed is None:
        return
    if subject_id not in allowed:
        raise forbidden(
            f"{what}不在你的数据范围内（当前账号只被授权了 {len(allowed)} 个科目）", 40301
        )


def scope_clause(viewer: ScopeViewer, column: str) -> tuple[str, dict[str, Any]]:
    """生成「科目范围」的 SQL 片段 `(sql, params)`，供**原生 SQL** 拼 `WHERE` 用。

    与 `apply_data_scope` 是同一判定的两种形态：那个服务 `QuestionQuery`（题库列表），
    本函数服务直接拼 SQL 的试卷/规则列表。两者都调 `scope_subject_ids`，口径不会漂。

    ⚠️ **空集合也必须真的生成 SQL** —— `= ANY(空数组)` 恒假才是"失败关闭"；
    若写成"没范围就不加这个 WHERE"，那是失败**开启**。
    """
    allowed = scope_subject_ids(viewer)
    if allowed is None:
        return "", {}
    return (
        f" AND {column} = ANY(CAST(:scope_subject_ids AS bigint[]))",
        {"scope_subject_ids": sorted(allowed)},
    )


def apply_data_scope(query: QuestionQuery, current_user: ScopeViewer) -> QuestionQuery:
    """按 `user_roles.scope_type / scope_id` 过滤可见题目。

    Batch 4 起这个钩子就已经被 `list_questions` 调用，但一直原样返回；
    **Batch 5 第一次真正生效**（导入管道同时复用 `scope_subject_ids`）。

    生成的是 `q.subject_id = ANY(:scope_subject_ids)`，走
    `idx_questions_filter` 的前导列 `subject_id`，不额外加索引。
    """
    allowed = scope_subject_ids(current_user)
    if allowed is None:
        return query
    if not allowed:
        # 失败关闭：没有任何可见科目 → 恒假。用 `= ANY('{}')` 表达空集，
        # 比 `1 = 0` 更能说明"是范围为空，不是逻辑错误"。
        return query.add(
            "q.subject_id = ANY(CAST(:scope_subject_ids AS bigint[]))", scope_subject_ids=[]
        )
    return query.add(
        "q.subject_id = ANY(CAST(:scope_subject_ids AS bigint[]))",
        scope_subject_ids=sorted(allowed),
    )


# ============================================================ 内部查询工具

_Q_SELECT = """
SELECT q.id, q.subject_id, q.chapter_id, q.knowledge_point_id,
       q.type, q.stem, q.difficulty, q.score_default,
       q.status, q.version, q.is_deleted, q.keywords, q.exam_year,
       q.created_at, q.updated_at,
       s.name AS subject_name,
       c.name AS chapter_name,
       kp.name AS knowledge_point_name,
       COALESCE(cu.nickname, cu.phone) AS created_by_name,
       COALESCE(uu.nickname, uu.phone) AS updated_by_name
FROM questions q
JOIN subjects s ON s.id = q.subject_id
LEFT JOIN chapters c ON c.id = q.chapter_id
LEFT JOIN knowledge_points kp ON kp.id = q.knowledge_point_id
LEFT JOIN users cu ON cu.id = q.created_by
LEFT JOIN users uu ON uu.id = q.updated_by
"""


async def _options_by_question(
    db: AsyncSession, question_ids: list[int], *, correct_only: bool = False
) -> dict[int, list[dict[str, Any]]]:
    """一次取回整页题目的选项，避免 N+1（与 `user_service._roles_map` 同一套路）。"""
    if not question_ids:
        return {}
    sql = (
        "SELECT id, question_id, label, content, content_html, is_correct, sort_no "
        "FROM question_options WHERE question_id = ANY(CAST(:qids AS bigint[])) "
        + ("AND is_correct = true " if correct_only else "")
        + "ORDER BY question_id, sort_no, label"
    )
    rows = (await db.execute(text(sql), {"qids": question_ids})).mappings().all()
    result: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        result.setdefault(r["question_id"], []).append(dict(r))
    return result


async def _ensure_subject(db: AsyncSession, subject_id: int) -> None:
    exists = await db.scalar(text("SELECT id FROM subjects WHERE id = :sid"), {"sid": subject_id})
    if not exists:
        raise bad_request(f"科目不存在：{subject_id}", 40001)


async def _ensure_chapter(db: AsyncSession, chapter_id: int, subject_id: int) -> None:
    row = (
        await db.execute(
            text("SELECT subject_id FROM chapters WHERE id = :cid AND is_deleted = false"),
            {"cid": chapter_id},
        )
    ).first()
    if row is None:
        raise bad_request(f"章节不存在：{chapter_id}", 40001)
    if row[0] != subject_id:
        raise bad_request("章节与所选科目不匹配，请重新选择", 40001)


async def _find_hash_conflict(
    db: AsyncSession, hash_value: str, *, exclude_id: int | None = None
) -> int | None:
    """内容指纹冲突检测，返回冲突题目 id。"""
    sql = "SELECT id FROM questions WHERE content_hash = :h AND is_deleted = false"
    params: dict[str, Any] = {"h": hash_value}
    if exclude_id is not None:
        sql += " AND id <> :xid"
        params["xid"] = exclude_id
    return await db.scalar(text(sql + " LIMIT 1"), params)


def _row_to_list_item(row: dict[str, Any], opts: list[dict[str, Any]]) -> QuestionListItem:
    stem = row["stem"] or ""
    return QuestionListItem(
        id=row["id"],
        subject_id=row["subject_id"],
        subject_name=row["subject_name"],
        chapter_id=row["chapter_id"],
        chapter_name=row["chapter_name"],
        knowledge_point_id=row.get("knowledge_point_id"),
        knowledge_point_name=row.get("knowledge_point_name"),
        type=row["type"],
        stem=stem[:STEM_PREVIEW_LEN] + ("…" if len(stem) > STEM_PREVIEW_LEN else ""),
        difficulty=row["difficulty"],
        score_default=float(row["score_default"] or 0),
        status=row["status"],
        version=row["version"],
        is_deleted=row["is_deleted"],
        option_count=len(opts),
        correct_labels=[o["label"] for o in opts if o["is_correct"]],
        keywords=row["keywords"],
        created_by_name=row["created_by_name"],
        updated_by_name=row["updated_by_name"],
        updated_at=row["updated_at"],
        created_at=row["created_at"],
    )


def _version_chain(db_unused: None = None) -> None:  # pragma: no cover
    """占位：保持模块内"版本相关逻辑集中"的可读性。"""


def _snapshot(
    row: dict[str, Any], options: list[dict[str, Any]], answer: dict[str, Any]
) -> dict[str, Any]:
    """生成一个完整版本快照，存进 `question_versions.snapshot`。

    只放"内容"字段，不放 `updated_by` 这类运营元信息 —— 快照是给教研看历史题面的，
    不是给审计看的（审计走 `content_change_logs` 与 `audit_logs`）。
    """
    return {
        "stem": row["stem"],
        "stem_html": row.get("stem_html"),
        "analysis": row.get("analysis"),
        "analysis_html": row.get("analysis_html"),
        "type": row["type"],
        "difficulty": row["difficulty"],
        "score_default": float(row["score_default"] or 0),
        "status": row["status"],
        "subject_id": row["subject_id"],
        "chapter_id": row.get("chapter_id"),
        "exam_year": row.get("exam_year"),
        "keywords": row.get("keywords"),
        "tags": row.get("tags") or [],
        "source_type": row.get("source_type"),
        "source_name": row.get("source_name"),
        "source_license": row.get("source_license"),
        "answer": answer,
        "options": [
            {
                "label": o["label"],
                "content": o["content"],
                "content_html": o.get("content_html"),
                "is_correct": o["is_correct"],
                "sort_no": o["sort_no"],
            }
            for o in options
        ],
    }


# ============================================================ 列表 / 详情


async def list_questions(
    db: AsyncSession,
    *,
    current_user: ScopeViewer,
    page: int,
    page_size: int,
    subject_id: int | None = None,
    chapter_id: int | None = None,
    knowledge_point_id: int | None = None,
    qtype: str | None = None,
    difficulty: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
    order_by: str = "updated_at",
    order: str = "desc",
) -> tuple[list[QuestionListItem], int]:
    """题目列表。

    `include_deleted=False`（默认）只看未删除的题；置 true 才把软删除的带出来
    —— 对应前端顶部那个「显示已归档」开关。

    排序字段走白名单映射，**不接受调用方直接传列名**（否则就是注入面）。

    `knowledge_point_id` 是 Batch 7 Pass 2 补的：组卷时"加题"要按知识点挑，
    而在此之前只能按章节/题型/难度（章节比知识点粗得多，挑不细）。
    """
    q = QuestionQuery()
    if not include_deleted:
        q.add("q.is_deleted = false")
    if subject_id is not None:
        q.add("q.subject_id = :subject_id", subject_id=subject_id)
    if chapter_id is not None:
        q.add("q.chapter_id = :chapter_id", chapter_id=chapter_id)
    if knowledge_point_id is not None:
        q.add("q.knowledge_point_id = :kp_id", kp_id=knowledge_point_id)
    if qtype:
        q.add("q.type = :qtype", qtype=qtype)
    if difficulty is not None:
        q.add("q.difficulty = :difficulty", difficulty=difficulty)
    if status:
        q.add("q.status = :status", status=status)
    if keyword:
        # 题干用 ILIKE：v0.1 数据量下够用；上了 pg_trgm 索引后可换 similarity
        q.add(
            "(q.stem ILIKE :kw OR q.keywords ILIKE :kw OR q.stem_html ILIKE :kw)",
            kw=f"%{keyword.strip()}%",
        )

    # ---- 数据范围钩子（Batch 5 在这里生效）----
    q = apply_data_scope(q, current_user)

    where_sql = (" WHERE " + " AND ".join(q.where)) if q.where else ""

    total = int(
        (
            await db.execute(text("SELECT count(*) FROM questions q" + where_sql), q.params)
        ).scalar_one()
    )

    order_col = {
        "updated_at": "q.updated_at",
        "created_at": "q.created_at",
        "difficulty": "q.difficulty",
        "id": "q.id",
    }.get(order_by, "q.updated_at")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"

    rows = (
        (
            await db.execute(
                text(
                    _Q_SELECT
                    + where_sql
                    # id 兜底：同一毫秒内的顺序不稳定，否则翻页会重复/漏项
                    + f" ORDER BY {order_col} {direction}, q.id {direction}"
                    + " LIMIT :limit OFFSET :offset"
                ),
                {**q.params, "limit": page_size, "offset": (page - 1) * page_size},
            )
        )
        .mappings()
        .all()
    )

    opt_map = await _options_by_question(db, [r["id"] for r in rows])
    items = [_row_to_list_item(dict(r), opt_map.get(r["id"], [])) for r in rows]
    return items, total


async def _load_question_row(
    db: AsyncSession, question_id: int, *, for_update: bool = False
) -> dict[str, Any] | None:
    sql = "SELECT * FROM questions WHERE id = :qid"
    if for_update:
        sql += " FOR UPDATE"
    row = (await db.execute(text(sql), {"qid": question_id})).mappings().first()
    return dict(row) if row else None


async def _load_options(db: AsyncSession, question_id: int) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, label, content, content_html, is_correct, sort_no "
                    "FROM question_options WHERE question_id = :qid ORDER BY sort_no, label"
                ),
                {"qid": question_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _load_versions(
    db: AsyncSession, question_id: int, current_version: int, *, limit: int = VERSION_LIMIT
) -> list[QuestionVersionItem]:
    rows = (
        (
            await db.execute(
                text(
                    """
                SELECT v.id, v.version, v.change_log, v.operator_id, v.snapshot, v.created_at,
                       COALESCE(u.nickname, u.phone) AS operator_name
                FROM question_versions v
                LEFT JOIN users u ON u.id = v.operator_id
                WHERE v.question_id = :qid
                ORDER BY v.version DESC
                LIMIT :lim
                """
                ),
                {"qid": question_id, "lim": limit},
            )
        )
        .mappings()
        .all()
    )
    return [
        QuestionVersionItem(
            id=r["id"],
            version=r["version"],
            change_log=r["change_log"],
            operator_id=r["operator_id"],
            operator_name=r["operator_name"],
            is_current=(r["version"] == current_version),
            snapshot=r["snapshot"] or {},
            created_at=r["created_at"],
        )
        for r in rows
    ]


async def get_question_detail(
    db: AsyncSession, *, question_id: int, viewer: ScopeViewer
) -> QuestionDetail:
    """题目详情。

    - 不存在的 id → `40401`（**不是** 200 + 空对象）
    - 软删除的题**仍可查看**（详情页要能展示"这条已归档"），但 `is_deleted=true` 会带出去
    - 历史版本内联返回（最新在前，最多 10 条），只读
    - **数据范围**：题目的科目不在调用者范围内 → `40301`（列表看不见 ≠ 按 id 也读不到）
    """
    row = await _load_question_row(db, question_id)
    if row is None:
        raise not_found("题目不存在", 40401)
    ensure_subject_visible(viewer, int(row["subject_id"]), "题目所属科目")

    options = await _load_options(db, question_id)
    subject_name = await db.scalar(
        text("SELECT name FROM subjects WHERE id = :sid"), {"sid": row["subject_id"]}
    )
    chapter_name = None
    if row["chapter_id"]:
        chapter_name = await db.scalar(
            text("SELECT name FROM chapters WHERE id = :cid"), {"cid": row["chapter_id"]}
        )

    answer = row["answer"] or {}
    correct_labels = [o["label"] for o in options if o["is_correct"]]

    return QuestionDetail(
        id=row["id"],
        subject_id=row["subject_id"],
        subject_name=subject_name,
        chapter_id=row["chapter_id"],
        chapter_name=chapter_name,
        knowledge_point_id=row["knowledge_point_id"],
        type=row["type"],
        stem=row["stem"],
        stem_html=row["stem_html"],
        analysis=row["analysis"],
        analysis_html=row["analysis_html"],
        answer=answer,
        options=[
            QuestionOptionOut(
                id=o["id"],
                label=o["label"],
                content=o["content"],
                content_html=o["content_html"],
                is_correct=o["is_correct"],
                sort_no=o["sort_no"],
            )
            for o in options
        ],
        correct_labels=correct_labels,
        difficulty=row["difficulty"],
        score_default=float(row["score_default"] or 0),
        status=row["status"],
        version=row["version"],
        is_deleted=row["is_deleted"],
        exam_year=row["exam_year"],
        keywords=row["keywords"],
        tags=list(row["tags"] or []),
        source_type=row["source_type"],
        source_name=row["source_name"],
        source_license=row["source_license"],
        copyright_holder=row["copyright_holder"],
        content_hash=row["content_hash"],
        created_by=row["created_by"],
        created_by_name=await _user_label(db, row["created_by"]),
        updated_by=row["updated_by"],
        updated_by_name=await _user_label(db, row["updated_by"]),
        published_at=row["published_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        can_edit="question:update" in viewer.permissions,
        can_delete="question:delete" in viewer.permissions,
        editable=row["type"] in EDITABLE_TYPES,
        versions=await _load_versions(db, question_id, row["version"]),
    )


async def _user_label(db: AsyncSession, user_id: int | None) -> str | None:
    if not user_id:
        return None
    return await db.scalar(
        text("SELECT COALESCE(nickname, phone) FROM users WHERE id = :uid"), {"uid": user_id}
    )


# ============================================================ 创建


async def create_question(
    db: AsyncSession,
    *,
    actor: ScopeViewer,
    actor_name: str | None,
    payload: QuestionCreateIn,
    ip: str | None = None,
    user_agent: str | None = None,
) -> QuestionDetail:
    # 数据范围**最先校验**（在任何写库动作之前）—— 越权请求必须零副作用。
    ensure_subject_visible(actor, payload.subject_id, "题目所属科目")
    await _ensure_subject(db, payload.subject_id)
    if payload.chapter_id is not None:
        await _ensure_chapter(db, payload.chapter_id, payload.subject_id)

    options = _normalize_options(payload.options)
    hash_value = content_hash(payload.stem, [(lbl, c) for lbl, c, _, _ in options], payload.type)

    existing = await _find_hash_conflict(db, hash_value)
    if existing is not None:
        raise conflict(
            f"内容重复：与题目 {existing} 的题干与选项完全一致（去重指纹相同）。"
            "若确有需要，请先修改题干或选项使其有实质差异。",
            40901,
        )

    answer = derive_answer(payload.type, payload.options, payload.judge_answer)
    qid = next_id()

    try:
        await db.execute(
            text(
                """
                INSERT INTO questions
                  (id, subject_id, chapter_id, type, stem, stem_html, answer, analysis,
                   analysis_html, score_default, difficulty, exam_year, source_type, source_name,
                   source_license, copyright_holder, tags, keywords, content_hash, status,
                   version, root_id, created_by, updated_by)
                VALUES
                  (:id, :subject_id, :chapter_id, :type, :stem, :stem_html,
                   CAST(:answer AS jsonb), :analysis, :analysis_html, :score_default, :difficulty,
                   :exam_year, :source_type, :source_name, :source_license, :copyright_holder,
                   CAST(:tags AS text[]), :keywords, :content_hash, :status,
                   1, :id, :actor, :actor)
                """
            ),
            {
                "id": qid,
                "subject_id": payload.subject_id,
                "chapter_id": payload.chapter_id,
                "type": payload.type,
                "stem": payload.stem,
                "stem_html": payload.stem_html,
                "answer": _json(answer),
                "analysis": payload.analysis,
                "analysis_html": payload.analysis_html,
                "score_default": payload.score_default,
                "difficulty": payload.difficulty,
                "exam_year": payload.exam_year,
                "source_type": payload.source_type,
                "source_name": payload.source_name,
                "source_license": payload.source_license,
                "copyright_holder": payload.copyright_holder,
                "tags": list(payload.tags),
                "keywords": payload.keywords,
                "content_hash": hash_value,
                "status": payload.status,
                "actor": actor.id,
            },
        )
        await _insert_options(db, qid, options)
        await _insert_version(
            db,
            qid,
            1,
            _snapshot(
                {
                    "stem": payload.stem,
                    "stem_html": payload.stem_html,
                    "analysis": payload.analysis,
                    "analysis_html": payload.analysis_html,
                    "type": payload.type,
                    "difficulty": payload.difficulty,
                    "score_default": payload.score_default,
                    "status": payload.status,
                    "subject_id": payload.subject_id,
                    "chapter_id": payload.chapter_id,
                    "exam_year": payload.exam_year,
                    "keywords": payload.keywords,
                    "tags": list(payload.tags),
                    "source_type": payload.source_type,
                    "source_name": payload.source_name,
                    "source_license": payload.source_license,
                },
                [
                    {"label": lbl, "content": c, "content_html": h, "is_correct": ic, "sort_no": i}
                    for i, (lbl, c, ic, h) in enumerate(options)
                ],
                answer,
            ),
            "创建题目 v1",
            actor.id,
        )
        await _record_change(
            db,
            entity_id=qid,
            action="create",
            before=None,
            after={
                "stem": payload.stem,
                "type": payload.type,
                "status": payload.status,
                "correct_labels": answer.get("value"),
            },
            change_log="创建题目",
            operator_id=actor.id,
            ip=ip,
        )
    except IntegrityError as exc:
        await db.rollback()
        if "uq_questions_hash" in str(exc.orig):
            raise conflict("内容重复：同题干同选项的题目已存在", 40901) from exc
        raise

    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="question.create",
        module="question",
        entity_type="question",
        entity_id=qid,
        after={"stem": payload.stem[:200], "type": payload.type, "status": payload.status},
        method="POST",
        path="/api/v1/admin/questions",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return await get_question_detail(db, question_id=qid, viewer=actor)


# ============================================================ 编辑


async def update_question(
    db: AsyncSession,
    *,
    actor: ScopeViewer,
    actor_name: str | None,
    question_id: int,
    payload: QuestionUpdateIn,
    ip: str | None = None,
    user_agent: str | None = None,
) -> QuestionDetail:
    """编辑题目：`version + 1`，写 `question_versions` 快照与 `content_change_logs`。

    流程刻意是「**合并 → 校验最终态 → 落库**」，而不是「逐字段校验」——
    因为改 `type`（单选↔多选）会让选项的合法性规则整体变化，
    只有对合并后的最终状态做校验才是正确的。
    """
    # FOR UPDATE：并发编辑时让后来者排队，配合 version 判断做到"不静默覆盖"
    row = await _load_question_row(db, question_id, for_update=True)
    if row is None:
        raise not_found("题目不存在", 40401)
    # 数据范围：**当前所属科目**与**要改到的科目**都必须在范围内。
    # 后者不能漏 —— 否则能把题"搬进"一个自己没被授权的科目，
    # 等于绕过新建时的那道闸。两道闸都排在 `is_deleted` / `version` 之前：
    # 越权者不该从报错里读出"这道题有没有被归档、别人是不是改过"。
    ensure_subject_visible(actor, int(row["subject_id"]), "题目所属科目")
    if payload.subject_id is not None:
        ensure_subject_visible(actor, payload.subject_id, "目标科目")
    if row["is_deleted"]:
        raise bad_request("题目已归档（软删除），请先恢复后再编辑", 40001)
    if row["version"] != payload.version:
        raise conflict(
            f"版本冲突：你手上是 v{payload.version}，库里已经是 v{row['version']}。"
            "说明有人先改过这道题，请刷新后重试。",
            40901,
        )

    before_row = row
    old_options = await _load_options(db, question_id)

    # ---- 1. 合并出最终态 ----
    final_type = payload.type or row["type"]
    if final_type not in EDITABLE_TYPES:
        raise bad_request(
            f"本批只支持编辑 {('、'.join(EDITABLE_TYPES))} 三种题型，"
            f"当前题目是 {final_type}（案例题/主观题留待下一批）",
            40001,
        )

    if payload.options is not None:
        final_options_in = payload.options
    else:
        # 没传选项 → 沿用库里现有选项（转成入参形态参与统一校验）
        final_options_in = [
            QuestionOptionIn(
                label=o["label"],
                content=o["content"],
                content_html=o.get("content_html"),
                is_correct=o["is_correct"],
            )
            for o in old_options
        ]

    # 判断题答案：显式传了用传的，否则沿用库里 answer.value[0]
    if final_type == "judge":
        judge_answer: bool | None = payload.judge_answer
        if judge_answer is None:
            old_val = (row["answer"] or {}).get("value") or []
            judge_answer = bool(old_val[0]) if old_val else None
        # 切换到判断题时，库里可能还挂着旧选项 → 需要清掉
        if final_options_in and payload.options is None:
            final_options_in = []
    else:
        judge_answer = None
        if not final_options_in:
            raise bad_request("单选题/多选题必须有选项", 40001)

    # ---- 2. 统一校验最终态（与创建同一套规则）----
    try:
        _validate_options(final_type, list(final_options_in))
    except ValueError as exc:
        raise bad_request(str(exc), 40001) from exc
    if final_type == "judge" and judge_answer is None:
        raise bad_request("判断题必须给出 judge_answer", 40001)

    final_options = _normalize_options(list(final_options_in))
    final_stem = payload.stem if payload.stem is not None else row["stem"]
    new_hash = content_hash(final_stem, [(lbl, c) for lbl, c, _, _ in final_options], final_type)

    dup = await _find_hash_conflict(db, new_hash, exclude_id=question_id)
    if dup is not None:
        raise conflict(f"内容重复：改完后与题目 {dup} 的题干与选项完全一致（去重指纹相同）", 40901)

    final_answer = derive_answer(final_type, list(final_options_in), judge_answer)

    # ---- 3. 逐字段落库，只更新真正变化的列 ----
    sets: list[str] = []
    params: dict[str, Any] = {"qid": question_id, "ver": row["version"] + 1, "actor": actor.id}
    simple = {
        "stem": final_stem,
        "stem_html": payload.stem_html if payload.stem_html is not None else row["stem_html"],
        "analysis": payload.analysis if payload.analysis is not None else row["analysis"],
        "analysis_html": payload.analysis_html
        if payload.analysis_html is not None
        else row["analysis_html"],
        "difficulty": payload.difficulty if payload.difficulty is not None else row["difficulty"],
        "score_default": payload.score_default
        if payload.score_default is not None
        else row["score_default"],
        "status": payload.status if payload.status is not None else row["status"],
        "exam_year": payload.exam_year if payload.exam_year is not None else row["exam_year"],
        "keywords": payload.keywords if payload.keywords is not None else row["keywords"],
        "source_name": payload.source_name
        if payload.source_name is not None
        else row["source_name"],
        "source_license": payload.source_license
        if payload.source_license is not None
        else row["source_license"],
        "copyright_holder": payload.copyright_holder
        if payload.copyright_holder is not None
        else row["copyright_holder"],
        "type": final_type,
        "subject_id": payload.subject_id if payload.subject_id is not None else row["subject_id"],
        "chapter_id": payload.chapter_id if payload.chapter_id is not None else row["chapter_id"],
        "source_type": payload.source_type
        if payload.source_type is not None
        else row["source_type"],
    }
    for col, val in simple.items():
        sets.append(f"{col} = :{col}")
        params[col] = val

    if payload.tags is not None:
        sets.append("tags = CAST(:tags AS text[])")
        params["tags"] = list(payload.tags)
    sets.append("answer = CAST(:answer AS jsonb)")
    params["answer"] = _json(final_answer)
    sets.append("content_hash = :content_hash")
    params["content_hash"] = new_hash
    sets.append("version = :ver")
    sets.append("updated_by = :actor")

    if params["subject_id"] != row["subject_id"]:
        await _ensure_subject(db, params["subject_id"])
    if params["chapter_id"] is not None and params["chapter_id"] != row["chapter_id"]:
        await _ensure_chapter(db, params["chapter_id"], params["subject_id"])

    await db.execute(text(f"UPDATE questions SET {', '.join(sets)} WHERE id = :qid"), params)

    # 选项整体替换：改标号比逐条 diff 简单且不会留下孤儿行
    if payload.options is not None or final_type != before_row["type"]:
        await db.execute(
            text("DELETE FROM question_options WHERE question_id = :qid"), {"qid": question_id}
        )
        await _insert_options(db, question_id, final_options)

    new_row = {
        **row,
        **{k: v for k, v in simple.items()},
        "answer": final_answer,
        "tags": params.get("tags", row["tags"]),
    }

    # ---- 4. 变更字段 diff（只记变化的，保持可读）----
    changed: dict[str, dict[str, Any]] = {}
    for key in (
        "stem",
        "stem_html",
        "analysis",
        "analysis_html",
        "type",
        "difficulty",
        "score_default",
        "status",
        "exam_year",
        "keywords",
        "subject_id",
        "chapter_id",
        "source_type",
        "source_name",
        "source_license",
    ):
        old_v, new_v = row.get(key), simple.get(key)
        if old_v != new_v:
            changed[key] = {"before": _jsonable(old_v), "after": _jsonable(new_v)}
    old_opts = [
        {"label": o["label"], "content": o["content"], "is_correct": o["is_correct"]}
        for o in old_options
    ]
    new_opts = [{"label": lbl, "content": c, "is_correct": ic} for lbl, c, ic, _ in final_options]
    if old_opts != new_opts:
        changed["options"] = {"before": old_opts, "after": new_opts}
    if old_opts and old_opts != new_opts and "correct_labels" not in changed:
        old_correct = [o["label"] for o in old_options if o["is_correct"]]
        new_correct = [lbl for lbl, _, ic, _ in final_options if ic]
        if old_correct != new_correct:
            changed["correct_labels"] = {"before": old_correct, "after": new_correct}

    new_version = row["version"] + 1
    summary = "、".join(list(changed.keys())[:8]) or "无字段变化"
    await _insert_version(
        db,
        question_id,
        new_version,
        _snapshot(
            new_row,
            [
                {"label": lbl, "content": c, "content_html": h, "is_correct": ic, "sort_no": i}
                for i, (lbl, c, ic, h) in enumerate(final_options)
            ],
            final_answer,
        ),
        f"编辑题目：{summary}",
        actor.id,
    )
    await _record_change(
        db,
        entity_id=question_id,
        action="update",
        before={k: v["before"] for k, v in changed.items()} or None,
        after={k: v["after"] for k, v in changed.items()} or None,
        change_log=f"编辑题目，涉及字段：{summary}",
        operator_id=actor.id,
        ip=ip,
    )

    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="question.update",
        module="question",
        entity_type="question",
        entity_id=question_id,
        before={"version": row["version"]},
        after={"version": new_version, "changed": list(changed)},
        method="PUT",
        path=f"/api/v1/admin/questions/{question_id}",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return await get_question_detail(db, question_id=question_id, viewer=actor)


# ============================================================ 删除


async def soft_delete_question(
    db: AsyncSession,
    *,
    actor: ScopeViewer,
    actor_name: str | None,
    question_id: int,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> QuestionDeleteOut:
    """软删除单题。幂等：已经删过的再删返回 `40401`（而不是假装成功）。

    数据范围：科目不在调用者范围内 → `40301`，且**在任何写库动作之前**。
    """
    row = await _load_question_row(db, question_id, for_update=True)
    if row is None:
        raise not_found("题目不存在", 40401)
    ensure_subject_visible(actor, int(row["subject_id"]), "题目所属科目")
    if row["is_deleted"]:
        raise bad_request("题目已经是删除状态，无需重复删除", 40001)

    new_version = row["version"] + 1
    await db.execute(
        text(
            "UPDATE questions SET is_deleted = true, updated_by = :actor, "
            "version = :ver WHERE id = :qid"
        ),
        {"actor": actor.id, "ver": new_version, "qid": question_id},
    )
    await _record_change(
        db,
        entity_id=question_id,
        action="delete",
        before={"is_deleted": False, "stem": row["stem"]},
        after={"is_deleted": True},
        change_log=reason or "软删除题目",
        operator_id=actor.id,
        ip=ip,
    )
    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="question.delete",
        module="question",
        entity_type="question",
        entity_id=question_id,
        before={"stem": row["stem"][:200]},
        after={"is_deleted": True, "reason": reason},
        method="DELETE",
        path=f"/api/v1/admin/questions/{question_id}",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return QuestionDeleteOut(id=question_id, is_deleted=True, version=new_version)


async def _assert_question_relations_ok(db: AsyncSession, row: dict[str, Any]) -> None:
    """恢复前校验题目挂的**章节 / 知识点**还在不在。

    为什么必须查：`questions.chapter_id` / `knowledge_point_id` 是**弱引用**
    （只有 `REFERENCES`，没写 `ON DELETE` 行为；而且章节/知识点是**软删除**，
    数据库层面根本不会拦）。所以章节被删掉后，题目仍然"挂"在一个已删除的节点上。

    如果直接恢复，这道题就出现在了一个不存在的章节里 ——
    按章节筛选时它既不属于任何章节、又占着列表的位置，是最难查的一类脏数据。
    **宁可拒绝恢复并说清楚，也不静默恢复到悬空引用上。**

    知识点还要额外看它**所属的章节**：`knowledge_points.chapter_id` 是必填的，
    父章节没了，这个知识点同样是悬空的。
    """
    problems: list[str] = []

    chapter_id = row.get("chapter_id")
    if chapter_id:
        r = (
            (
                await db.execute(
                    text("SELECT name, is_deleted FROM chapters WHERE id = :cid"),
                    {"cid": chapter_id},
                )
            )
            .mappings()
            .first()
        )
        if r is None:
            problems.append(f"章节已不存在（chapter_id={chapter_id}）")
        elif r["is_deleted"]:
            problems.append(f"章节「{r['name']}」已删除")

    kp_id = row.get("knowledge_point_id")
    if kp_id:
        kp = (
            (
                await db.execute(
                    text(
                        "SELECT k.name, k.is_deleted, k.chapter_id, c.name AS chapter_name, "
                        "       c.is_deleted AS chapter_deleted, c.id AS chapter_exists "
                        "FROM knowledge_points k "
                        "LEFT JOIN chapters c ON c.id = k.chapter_id "
                        "WHERE k.id = :kid"
                    ),
                    {"kid": kp_id},
                )
            )
            .mappings()
            .first()
        )
        if kp is None:
            problems.append(f"知识点已不存在（knowledge_point_id={kp_id}）")
        else:
            if kp["is_deleted"]:
                problems.append(f"知识点「{kp['name']}」已删除")
            if kp["chapter_exists"] is None:
                problems.append(f"知识点「{kp['name']}」所属章节已不存在")
            elif kp["chapter_deleted"]:
                problems.append(f"知识点「{kp['name']}」所属章节「{kp['chapter_name']}」已删除")

    if problems:
        raise conflict(
            "题目关联的数据已失效，拒绝恢复："
            + "；".join(problems)
            + "。请先恢复对应的章节/知识点，或把这道题的章节/知识点改到有效节点上再恢复。"
            "（不会静默恢复到不存在的章节上）",
            40901,
        )


async def restore_question(
    db: AsyncSession,
    *,
    actor: ScopeViewer,
    actor_name: str | None,
    question_id: int,
    ip: str | None = None,
    user_agent: str | None = None,
) -> QuestionRestoreOut:
    """恢复被软删除的题目（与 `soft_delete_question` 对称）。

    三条刻意的行为：

    1. **幂等**：题目本来就没被删除 → 返回 `code=0` + `already_active=true`，
       **不报错也不产生写入**。"目标状态已达成"不是失败 ——
       否则前端重试、或批量恢复里混进一道没删的题，就得专门写容错。
    2. **恢复前校验关联数据**（见 `_assert_question_relations_ok`）——
       章节/知识点已失效时拒绝并说明原因。
    3. **版本 +1**：恢复是一次内容变更，与删除对称，`version` 也要往前走，
       否则乐观锁会出现"删除再恢复之后版本号回到旧值"的诡异现象。
    """
    row = await _load_question_row(db, question_id, for_update=True)
    if row is None:
        raise not_found("题目不存在", 40401)
    # 范围闸排在幂等分支**之前**：越权者不该靠"已经没删"这个返回拿到 200。
    ensure_subject_visible(actor, int(row["subject_id"]), "题目所属科目")

    if not row["is_deleted"]:
        return QuestionRestoreOut(
            id=question_id,
            is_deleted=False,
            version=row["version"],
            already_active=True,
            message="题目当前未被删除，无需恢复。",
        )

    await _assert_question_relations_ok(db, row)

    new_version = row["version"] + 1
    await db.execute(
        text(
            "UPDATE questions SET is_deleted = false, updated_by = :actor, "
            "version = :ver WHERE id = :qid"
        ),
        {"actor": actor.id, "ver": new_version, "qid": question_id},
    )
    await _record_change(
        db,
        entity_id=question_id,
        action="restore",
        before={"is_deleted": True, "stem": row["stem"]},
        after={"is_deleted": False},
        change_log="恢复题目",
        operator_id=actor.id,
        ip=ip,
    )
    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="question.restore",
        module="question",
        entity_type="question",
        entity_id=question_id,
        before={"is_deleted": True},
        after={"is_deleted": False},
        method="POST",
        path=f"/api/v1/admin/questions/{question_id}/restore",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return QuestionRestoreOut(
        id=question_id, is_deleted=False, version=new_version, message="已恢复"
    )


async def batch_soft_delete(
    db: AsyncSession,
    *,
    actor: ScopeViewer,
    actor_name: str | None,
    ids: list[int],
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> QuestionBatchDeleteOut:
    """批量软删除。

    - 去重后逐条判断，已删除 / 不存在的进 `skipped`（**不报错**，批量操作不该因为
      其中一条有问题就整批失败）
    - 整批共用一个 `batch_id`，写进 `content_change_logs.batch_id`，
      将来要"撤销这一批"或者追溯"谁在什么时候批量删了什么"就靠它
    - ⚠️ **但数据范围越权是整批 `40301`、零副作用**（不算 `skipped`，理由见函数中部注释）
    """
    uniq = list(dict.fromkeys(ids))
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, subject_id, stem, is_deleted FROM questions "
                    "WHERE id = ANY(CAST(:ids AS bigint[]))"
                ),
                {"ids": uniq},
            )
        )
        .mappings()
        .all()
    )
    found = {r["id"]: dict(r) for r in rows}

    # ---- 数据范围：**整批拒绝**，不是"把越权的那几条塞进 skipped" ----
    #
    # 为什么不复用下面的 `skipped`：`skipped` 的语义是"目标状态已达成 / 对象不存在"
    # —— 那些情况下**继续处理其余几条是合理的**。越权不属于这一类，它是"**你根本不许动**"。
    # 若这里也悄悄跳过，批量入口就变成比单条入口（`40301`）**更弱**的一道闸：
    # 越权者只要改走批量接口，403 就降级成一句不起眼的 `skipped`，
    # 而且**会被照常写进审计日志当作正常操作**。
    # 按硬约定 C 的判据（要不要"放过"看目标状态是否已达成）—— 越权不满足，故整批拒绝、零副作用。
    if uniq:
        allowed = scope_subject_ids(actor)
        if allowed is not None:
            outsiders = [
                i for i in uniq if i in found and int(found[i]["subject_id"]) not in allowed
            ]
            if outsiders:
                preview = "、".join(str(i) for i in outsiders[:5])
                if len(outsiders) > 5:
                    preview += f" 等 {len(outsiders)} 道"
                raise forbidden(
                    f"有 {len(outsiders)} 道题不在你的数据范围内（{preview}），"
                    f"**整批未执行**。当前账号只被授权了 {len(allowed)} 个科目，"
                    "请从选择里去掉它们后重试。",
                    40301,
                )

    to_delete = [i for i in uniq if i in found and not found[i]["is_deleted"]]
    skipped = [i for i in uniq if i not in found or found[i]["is_deleted"]]

    batch_id = next_id()
    if to_delete:
        await db.execute(
            text(
                "UPDATE questions SET is_deleted = true, updated_by = :actor, "
                "version = version + 1 WHERE id = ANY(CAST(:ids AS bigint[]))"
            ),
            {"actor": actor.id, "ids": to_delete},
        )
        for qid in to_delete:
            await _record_change(
                db,
                entity_id=qid,
                action="delete",
                batch_id=batch_id,
                before={"is_deleted": False, "stem": found[qid]["stem"]},
                after={"is_deleted": True},
                change_log=reason or f"批量软删除（共 {len(to_delete)} 道）",
                operator_id=actor.id,
                ip=ip,
            )

    await write_audit(
        db,
        actor_id=actor.id,
        actor_name=actor_name,
        action="question.batch_delete",
        module="question",
        entity_type="question",
        entity_id=None,
        after={
            "ids": [str(i) for i in to_delete],
            "deleted": len(to_delete),
            "skipped": [str(i) for i in skipped],
            "batch_id": str(batch_id),
        },
        method="POST",
        path="/api/v1/admin/questions/batch-delete",
        ip=ip,
        user_agent=user_agent,
    )
    await db.commit()
    return QuestionBatchDeleteOut(deleted=len(to_delete), skipped=skipped, batch_id=batch_id)


# ============================================================ 章节树


async def list_chapter_tree(
    db: AsyncSession, *, viewer: ScopeViewer, subject_id: int | None = None
) -> ChapterTreeOut:
    """章节树（下拉用）。

    - 不传 `subject_id` → 返回**调用者有数据范围的全部科目**分组；传了就只返回那一组
    - `question_count` 是**实时统计**（不含软删除），不是读 `chapters.question_count`
      那个冗余列 —— 冗余列的刷新任务还没做，读了会全是 0，反而误导人
    - 当前种子的章节都是 level=1 的扁平结构，但这里按 `parent_id` **递归成树**，
      将来加了二级章节，接口形状不用变
    - **数据范围**：显式指定别的科目 → `40301`；不指定时**只列出有权限的科目分组**
      （下拉是"能选什么"的清单，把没权限的科目摆上去，等于让用户点一个注定 403 的选项）
    """
    if subject_id is not None:
        ensure_subject_visible(viewer, subject_id, "科目")

    # 不指定科目时，这份"能选哪些科目"的清单同样按数据范围收口。
    # ⚠️ `subjects` 在这条 SQL 里**没有别名**，所以列名直接写 `id`。
    scope_sql, scope_params = scope_clause(viewer, "id")

    sub_params: dict[str, Any] = {}
    sub_where = "WHERE is_deleted = false"
    if subject_id is not None:
        sub_where += " AND subject_id = :sid"
        sub_params["sid"] = subject_id

    chapter_rows = (
        (
            await db.execute(
                text(
                    "SELECT id, subject_id, parent_id, code, name, level, sort_no "
                    f"FROM chapters {sub_where} ORDER BY subject_id, sort_no, id"
                ),
                sub_params,
            )
        )
        .mappings()
        .all()
    )

    q_params: dict[str, Any] = {}
    q_where = "WHERE is_deleted = false AND chapter_id IS NOT NULL"
    if subject_id is not None:
        q_where += " AND subject_id = :sid"
        q_params["sid"] = subject_id
    count_rows = (
        (
            await db.execute(
                text(
                    f"SELECT chapter_id, count(*) AS n FROM questions {q_where} GROUP BY chapter_id"
                ),
                q_params,
            )
        )
        .mappings()
        .all()
    )
    counts = {r["chapter_id"]: int(r["n"]) for r in count_rows}

    subject_rows = (
        (
            await db.execute(
                text(
                    "SELECT id, code, name, short_name, professional FROM subjects "
                    "WHERE status = 'on' "
                    + ("AND id = :sid " if subject_id is not None else "")
                    + scope_sql
                    + "ORDER BY sort_no, id"
                ),
                {**sub_params, **scope_params},
            )
        )
        .mappings()
        .all()
    )

    # subject_id -> [节点]（先建扁平，再挂 children）
    by_subject: dict[int, list[dict[str, Any]]] = {}
    for r in chapter_rows:
        by_subject.setdefault(r["subject_id"], []).append(dict(r))

    groups: list[SubjectChapterGroup] = []
    total = 0
    for s in subject_rows:
        flat = by_subject.get(s["id"], [])
        nodes = {
            r["id"]: ChapterNode(
                id=r["id"],
                subject_id=r["subject_id"],
                parent_id=r["parent_id"],
                code=r["code"],
                name=r["name"],
                level=r["level"],
                sort_no=r["sort_no"],
                question_count=counts.get(r["id"], 0),
            )
            for r in flat
        }
        roots: list[ChapterNode] = []
        for r in flat:
            node = nodes[r["id"]]
            parent = nodes.get(r["parent_id"]) if r["parent_id"] else None
            if parent is not None:
                parent.children.append(node)
            else:
                roots.append(node)
        total += len(nodes)
        groups.append(
            SubjectChapterGroup(
                subject=SubjectBrief(
                    id=s["id"],
                    code=s["code"],
                    name=s["name"],
                    short_name=s["short_name"],
                    professional=s["professional"],
                ),
                chapters=roots,
            )
        )

    return ChapterTreeOut(subject_id=subject_id, total=total, items=groups)


async def list_knowledge_points(
    db: AsyncSession,
    *,
    viewer: ScopeViewer,
    subject_id: int | None = None,
    chapter_id: int | None = None,
    keyword: str | None = None,
) -> KnowledgePointListOut:
    """知识点下拉数据源（Batch 7 Pass 2 补）。

    存在的理由：组卷「加题」面板要按知识点挑题，而在那之前**没有任何接口能列出知识点** ——
    只能从题目里反推，那样下拉里只会出现"当前页见过的"知识点，筛不干净。
    与章节树同一角色：**下拉数据源**，所以 `question_count` 同样是实时统计
    （`knowledge_points.question_count` 冗余列同样没刷新，读了会全是 0）。

    `is_deleted = false` 是硬过滤：已删除的知识点不该出现在筛选下拉里，
    否则用户筛一个"已经不存在的知识点"，结果必然是空 —— 一个注定空手而归的选项。

    **数据范围**：显式指定别的科目 → `40301`；否则按 `kp.subject_id` 收口
    （与章节树同理，下拉里不该出现选不了的选项）。
    """
    if subject_id is not None:
        ensure_subject_visible(viewer, subject_id, "科目")

    conds: list[str] = ["kp.is_deleted = false", "c.is_deleted = false"]
    params: dict[str, Any] = {}
    if subject_id is not None:
        conds.append("kp.subject_id = :sid")
        params["sid"] = subject_id
    if chapter_id is not None:
        conds.append("kp.chapter_id = :cid")
        params["cid"] = chapter_id
    if keyword and keyword.strip():
        conds.append("(kp.name ILIKE :kw OR kp.code ILIKE :kw)")
        params["kw"] = f"%{keyword.strip()}%"

    scope_sql, scope_params = scope_clause(viewer, "kp.subject_id")

    rows = (
        (
            await db.execute(
                text(
                    "SELECT kp.id, kp.subject_id, kp.chapter_id, kp.code, kp.name, "
                    "       kp.importance, c.name AS chapter_name, "
                    "       COALESCE(cnt.n, 0) AS question_count "
                    "FROM knowledge_points kp "
                    "JOIN chapters c ON c.id = kp.chapter_id "
                    "LEFT JOIN ("
                    "    SELECT knowledge_point_id, count(*) AS n FROM questions "
                    "    WHERE is_deleted = false AND knowledge_point_id IS NOT NULL "
                    "    GROUP BY knowledge_point_id"
                    ") cnt ON cnt.knowledge_point_id = kp.id "
                    "WHERE " + " AND ".join(conds) + scope_sql + " "
                    "ORDER BY c.sort_no, kp.sort_no, kp.id"
                ),
                {**params, **scope_params},
            )
        )
        .mappings()
        .all()
    )

    return KnowledgePointListOut(
        items=[
            KnowledgePointItem(
                id=r["id"],
                subject_id=r["subject_id"],
                chapter_id=r["chapter_id"],
                chapter_name=r["chapter_name"],
                code=r["code"],
                name=r["name"],
                importance=r["importance"],
                question_count=int(r["question_count"] or 0),
            )
            for r in rows
        ]
    )


# ============================================================ 落库小工具


async def _insert_options(
    db: AsyncSession, question_id: int, options: list[tuple[str, str, bool, str | None]]
) -> None:
    for i, (label, content, is_correct, content_html) in enumerate(options):
        await db.execute(
            text(
                "INSERT INTO question_options "
                "(id, question_id, label, content, content_html, is_correct, sort_no) "
                "VALUES (:id, :qid, :label, :content, :content_html, :is_correct, :sort_no)"
            ),
            {
                "id": next_id(),
                "qid": question_id,
                "label": label,
                "content": content,
                "content_html": content_html,
                "is_correct": is_correct,
                "sort_no": i,
            },
        )


async def _insert_version(
    db: AsyncSession,
    question_id: int,
    version: int,
    snapshot: dict[str, Any],
    change_log: str,
    operator_id: int,
) -> None:
    await db.execute(
        text(
            "INSERT INTO question_versions "
            "(id, question_id, version, snapshot, change_log, operator_id) "
            "VALUES (:id, :qid, :ver, CAST(:snap AS jsonb), :cl, :op)"
        ),
        {
            "id": next_id(),
            "qid": question_id,
            "ver": version,
            "snap": _json(snapshot),
            "cl": change_log[:500],
            "op": operator_id,
        },
    )


async def _record_change(
    db: AsyncSession,
    *,
    entity_id: int,
    action: str,
    before: Any,
    after: Any,
    change_log: str,
    operator_id: int,
    ip: str | None = None,
    batch_id: int | None = None,
) -> None:
    """写 `content_change_logs`。diff 固定是 `{before, after}` 形状 ——
    前端审计抽屉的 `diffJson` 就是按这个结构渲染字段级差异的。"""
    from app.core.idgen import to_inet

    await db.execute(
        text(
            "INSERT INTO content_change_logs "
            "(id, entity_type, entity_id, action, batch_id, diff, change_log, operator_id, operator_ip) "
            "VALUES (:id, 'question', :eid, :action, :batch_id, CAST(:diff AS jsonb), :cl, :op, :ip)"
        ),
        {
            "id": next_id(),
            "eid": entity_id,
            "action": action,
            "batch_id": batch_id,
            "diff": _json({"before": before, "after": after}),
            "cl": change_log[:500],
            "op": operator_id,
            "ip": to_inet(ip),
        },
    )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def _jsonable(value: Any) -> Any:
    from datetime import datetime as _dt

    if isinstance(value, _dt):
        return value.isoformat()
    return value


__all__ = [
    "ScopeViewer",
    "QuestionQuery",
    "apply_data_scope",
    "scope_subject_ids",
    "ensure_subject_visible",
    "scope_clause",
    "content_hash",
    "derive_answer",
    "list_questions",
    "get_question_detail",
    "create_question",
    "update_question",
    "soft_delete_question",
    "batch_soft_delete",
    "list_chapter_tree",
    "EDITABLE_TYPES",
]
