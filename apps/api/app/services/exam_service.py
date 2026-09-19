"""管理端 · 试卷与组卷引擎（Batch 7 Pass 1）。

    GET    /admin/paper-rules              组卷规则列表
    POST   /admin/paper-rules              新建规则
    PUT    /admin/paper-rules/{id}         编辑规则
    DELETE /admin/paper-rules/{id}         删除规则（硬删）
    GET    /admin/exams                    试卷列表
    POST   /admin/exams                    创建试卷（含卷面分段）
    GET    /admin/exams/{id}               试卷详情（分段 + 题目 + 锁定版本）
    PUT    /admin/exams/{id}               编辑试卷
    DELETE /admin/exams/{id}               归档（软删除）
    POST   /admin/exams/{id}/restore       恢复（解除归档）
    POST   /admin/exams/{id}/questions     手动加题
    DELETE /admin/exams/{id}/questions/{eq_id}  移出一题
    POST   /admin/exams/{id}/auto-compose  规则自动组卷
    POST   /admin/exams/{id}/validate      卷面校验
    POST   /admin/exams/{id}/publish       发布（写版本快照锁定）

## 组卷算法（`docs/05 §3.2`）

每条规则**逐级放宽**，每步都记下候选数；放宽到底仍不够 → 回传 `Shortfall`，
**绝不静默用别的题顶上**。

| 步 | 约束 | 说明 |
|---|---|---|
| 1 | type + difficulty + chapter/kp + year | 精确匹配 |
| 2 | 去掉 difficulty | "不足则放宽难度范围" |
| 3 | 只留 type | "仍不足则放宽知识点" |

**为什么要逐步放宽而不是一次性放宽**：教研发规则时真正在意的是"这 60 道单选要覆盖
这几个知识点、难度 2~4"。直接一步放宽到"是单选就行"，抽出来的卷子会悄悄偏题。
分步放宽 + 缺口回传，等于告诉教研"我尽力了，差 8 道，已经放宽到不限难度了"。

**加权采样**：优先选**从未进过任何试卷**的题，其次选使用次数少的。
权重 `w = 4.0 if usage == 0 else 1/(1+usage)`，再按权重无放回抽样。
不带权重的话，大题库里反复抽到同一批"高频题"是必然事件。

## 版本锁定

**主存储是 `exam_questions.locked_version`（独立列）。** 发布时把每道题的当前版本写进卷面行；
`get_exam_detail` 再拿锁定版本去 `question_versions` 取**当时的题干快照** ——
所以"改了题也不影响已发布试卷"是**真的**，不只是记了个数字。

> **历史与遗留**：Pass 1 最初把锁定挤在 `exams.rule_config.question_locks`（JSONB）里，
> 那是"不改 schema"这个**过度约束**下的将就 —— 版本锁定是试卷的核心语义，
> 不该寄居在一个描述"答题行为"的字段里。现已补列：
> `db/migrations/20260917-01-locked-version-and-viewer-exam-read.sql`（含从 JSONB 回填）。
>
> **JSONB 仍双写，但已 deprecated**（保留一次回滚余地，下一批清理）：
> - **读**：`_question_locks()` 优先读列；列为 NULL 才回退 JSONB
>   （覆盖"迁移已执行但代码先上线"的时间窗，以及未回填的历史行）；
> - **写**：`publish_exam()` 双写（列 + JSONB）；`compose_exam()` 两处都清。

## 分层

本模块是纯业务：**不 import fastapi**。数据范围的 `ScopeViewer` 协议从
`question_service` 借用（那里是唯一事实源），不直接依赖 `core.deps.CurrentUser`。
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import errors
from app.core.idgen import next_id, to_inet
from app.schemas.admin_exam import (
    OBJECTIVE_TYPES,
    ExamAddQuestionsIn,
    ExamAddQuestionsOut,
    ExamComposeIn,
    ExamComposeOut,
    ExamCreateIn,
    ExamDetail,
    ExamListItem,
    ExamPublishIn,
    ExamPublishOut,
    ExamQuestionItem,
    ExamRemoveQuestionOut,
    ExamRestoreOut,
    ExamSectionDetail,
    ExamSectionsReplaceIn,
    ExamSectionsReplaceOut,
    ExamSectionIn,
    ExamSectionOut,
    ExamSoftDeleteOut,
    ExamUpdateIn,
    ExamValidateIssue,
    ExamValidateOut,
    PaperRuleCreateIn,
    PaperRuleDeleteOut,
    PaperRuleOut,
    PaperRulePreviewIn,
    PaperRulePreviewOut,
    PaperRuleUpdateIn,
    PreviewQuestionItem,
    RuleItem,
    RulePreviewItem,
    Shortfall,
    SkippedQuestion,
)

logger = logging.getLogger("app.exam")

#: 数据范围的判定复用题库那一份唯一事实源（避免两处各写一套过滤规则慢慢漂移）。
from app.services import config_service  # noqa: E402
from app.services.question_service import ScopeViewer, scope_subject_ids  # noqa: E402


class Actor(ScopeViewer, Protocol):
    """写操作的执行者（接口层传 `CurrentUser`，测试里传轻量替身）。"""

    def __str__(self) -> str: ...


# ============================================================ 常量


TYPE_LABELS: dict[str, str] = {
    "single": "单项选择题",
    "multiple": "多项选择题",
    "judge": "判断题",
    "case": "案例分析题",
    "case_sub": "案例小问",
    "fill": "填空题",
    "essay": "简答题",
}

STATUS_LABELS: dict[str, str] = {
    "draft": "草稿",
    "reviewing": "审核中",
    "published": "已发布",
    "off": "已下线",
    "archived": "已归档",
}

#: 加权采样里"从未用过"的题拿到的权重倍数。
#: 4.0 是经验值：足够让新题明显优先，又不至于把老题饿死（老题仍按 1/(1+usage) 参与竞争）。
UNUSED_WEIGHT = 4.0


# ============================================================ 小工具


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rule_item_from_json(raw: Any) -> RuleItem:
    """库里 `rules` 是 JSONB，取出来是 list[dict] / str，统一成 `RuleItem`。"""
    if isinstance(raw, str):
        raw = json.loads(raw)
    return RuleItem.model_validate(raw)


def _rule_config_of(row: Any) -> dict[str, Any]:
    """读 `exams.rule_config`。asyncpg 可能给 dict（已解码）或 str。"""
    rc = row.get("rule_config") if hasattr(row, "get") else row["rule_config"]
    if rc is None:
        return {}
    if isinstance(rc, str):
        try:
            return json.loads(rc)
        except ValueError:
            return {}
    return dict(rc)


def _merge_rule_config(current: dict[str, Any], **updates: Any) -> dict[str, Any]:
    """合并写 `rule_config`。

    **必须读-改-写**：组卷写 `shortfalls` / 发布写 `question_locks`（deprecated），
    两者不能互相覆盖。这是把它当"一个 JSONB 存多件事"的必然代价 ——
    版本锁定已经搬到独立列，剩下的 `shortfalls` / `compose` 若要拆，可以照同样的路子走。
    """
    merged = dict(current or {})
    for k, v in updates.items():
        merged[k] = v
    return merged


# ============================================================ 数据范围


def _visible_subject_ids(viewer: ScopeViewer) -> set[int] | None:
    return scope_subject_ids(viewer)


def _ensure_subject_visible(viewer: ScopeViewer, subject_id: int, what: str = "该科目") -> None:
    """教研只能碰自己科目范围内的试卷 / 规则（`docs/05` 的数据范围要求）。"""
    allowed = _visible_subject_ids(viewer)
    if allowed is None:
        return
    if subject_id not in allowed:
        raise errors.forbidden(
            f"{what}不在你的数据范围内（当前账号只被授权了 {len(allowed)} 个科目）", 40301
        )


async def _assert_subject_usable(db: AsyncSession, subject_id: int) -> None:
    """科目必须存在且处于 `status='on'`。

    ⚠️ **`subjects` 表没有 `is_deleted` 列** —— 它用 `status IN ('on','off')` 表达停用。
    （踩过：想当然写了 `is_deleted = false`，直接 `UndefinedColumnError` → 500。
    全库只有部分表有 `is_deleted`：`questions` / `exams` 有，`subjects` / `paper_rules` /
    `exam_sections` / `exam_questions` 都没有。查表结构请以 `db/schema.sql` 为准。）
    """
    row = (
        await db.execute(
            text("SELECT name, status FROM subjects WHERE id = :sid"), {"sid": subject_id}
        )
    ).mappings().first()
    if row is None:
        raise errors.bad_request("科目不存在", 40001)
    if row["status"] != "on":
        raise errors.bad_request(f"科目「{row['name']}」已停用，不能用于组卷", 40001)


def _scope_clause(viewer: ScopeViewer, column: str) -> tuple[str, dict[str, Any]]:
    allowed = _visible_subject_ids(viewer)
    if allowed is None:
        return "", {}
    if not allowed:
        return f" AND {column} = ANY(CAST(:scope_subject_ids AS bigint[]))", {
            "scope_subject_ids": []
        }
    return f" AND {column} = ANY(CAST(:scope_subject_ids AS bigint[]))", {
        "scope_subject_ids": sorted(allowed)
    }


# ============================================================ 组卷规则 CRUD


_RULE_SELECT = """
SELECT r.id, r.name, r.subject_id, r.type, r.duration_min, r.rules, r.strategy,
       r.status, r.created_by, r.created_at, r.updated_at,
       s.name AS subject_name, u.nickname AS creator_nickname, u.phone AS creator_phone
FROM paper_rules r
LEFT JOIN subjects s ON s.id = r.subject_id
LEFT JOIN users u    ON u.id = r.created_by
"""


def _rule_out(row: Any, viewer: ScopeViewer) -> PaperRuleOut:
    raw_rules = row["rules"]
    if isinstance(raw_rules, str):
        raw_rules = json.loads(raw_rules)
    items = [RuleItem.model_validate(x) for x in (raw_rules or [])]
    subject_id = int(row["subject_id"])
    allowed = _visible_subject_ids(viewer)
    in_scope = allowed is None or subject_id in allowed
    return PaperRuleOut(
        id=int(row["id"]),
        name=row["name"],
        subject_id=subject_id,
        subject_name=row.get("subject_name"),
        type=row["type"],
        duration_min=int(row["duration_min"]),
        rules=items,
        strategy=row["strategy"],
        status=row["status"],
        planned_count=sum(i.count for i in items),
        planned_score=round(sum(i.count * i.score for i in items), 2),
        created_by=int(row["created_by"]) if row.get("created_by") else None,
        created_by_name=row.get("creator_nickname") or row.get("creator_phone"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        can_edit=in_scope,
        can_delete=in_scope,
    )


async def list_paper_rules(
    db: AsyncSession,
    *,
    viewer: ScopeViewer,
    page: int = 1,
    page_size: int = 20,
    subject_id: int | None = None,
    status: str | None = None,
    rule_type: str | None = None,
) -> tuple[list[PaperRuleOut], int]:
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    scope_sql, scope_params = _scope_clause(viewer, "r.subject_id")
    where.append(scope_sql.lstrip(" AND ") or "1 = 1")
    params.update(scope_params)
    if subject_id is not None:
        where.append("r.subject_id = :subject_id")
        params["subject_id"] = subject_id
    if status:
        where.append("r.status = :status")
        params["status"] = status
    if rule_type:
        where.append("r.type = :rule_type")
        params["rule_type"] = rule_type

    where_sql = " AND ".join(where)
    total = await db.scalar(
        text(f"SELECT count(*) FROM paper_rules r WHERE {where_sql}"), params
    )
    rows = (
        await db.execute(
            text(
                f"{_RULE_SELECT} WHERE {where_sql} "
                "ORDER BY r.updated_at DESC, r.id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
    ).mappings().all()
    return [_rule_out(r, viewer) for r in rows], int(total or 0)


async def _load_rule_row(db: AsyncSession, rule_id: int, *, for_update: bool = False) -> Any:
    sql = _RULE_SELECT + " WHERE r.id = :rid"
    if for_update:
        sql += " FOR UPDATE OF r"
    row = (await db.execute(text(sql), {"rid": rule_id})).mappings().first()
    if row is None:
        raise errors.not_found("组卷规则不存在", 40401)
    return row


async def create_paper_rule(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    payload: PaperRuleCreateIn,
    ip: str | None = None,
) -> PaperRuleOut:
    _ensure_subject_visible(actor, payload.subject_id, "规则所属科目")
    await _assert_subject_usable(db, payload.subject_id)

    rid = next_id()
    await db.execute(
        text(
            "INSERT INTO paper_rules (id, name, subject_id, type, duration_min, rules, strategy, status, created_by) "
            "VALUES (:id, :name, :sid, :type, :dur, CAST(:rules AS jsonb), :strategy, 'on', :by)"
        ),
        {
            "id": rid, "name": payload.name, "sid": payload.subject_id, "type": payload.type,
            "dur": payload.duration_min,
            "rules": _json([r.model_dump() for r in payload.rules]),
            "strategy": payload.strategy, "by": actor.id,
        },
    )
    await _write_change_log(
        db, entity_type="paper_rule", entity_id=rid, action="create",
        actor_id=actor.id, ip=ip, change_log=f"新建组卷规则「{payload.name}」",
        before=None, after={"name": payload.name, "rules": [r.model_dump() for r in payload.rules]},
    )
    await db.commit()
    row = await _load_rule_row(db, rid)
    return _rule_out(row, actor)


async def update_paper_rule(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    rule_id: int,
    payload: PaperRuleUpdateIn,
    ip: str | None = None,
) -> PaperRuleOut:
    row = await _load_rule_row(db, rule_id, for_update=True)
    _ensure_subject_visible(actor, int(row["subject_id"]), "规则所属科目")

    sets: list[str] = []
    params: dict[str, Any] = {"rid": rule_id}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    def _set(col: str, key: str, value: Any, stored: Any) -> None:
        sets.append(f"{col} = :{key}")
        params[key] = value
        before[col] = stored
        after[col] = value

    if payload.subject_id is not None and payload.subject_id != int(row["subject_id"]):
        _ensure_subject_visible(actor, payload.subject_id, "规则所属科目")
        _set("subject_id", "sid", payload.subject_id, int(row["subject_id"]))
    if payload.name is not None:
        _set("name", "name", payload.name, row["name"])
    if payload.type is not None:
        _set("type", "type", payload.type, row["type"])
    if payload.duration_min is not None:
        _set("duration_min", "dur", payload.duration_min, int(row["duration_min"]))
    if payload.rules is not None:
        new_rules = [r.model_dump() for r in payload.rules]
        sets.append("rules = CAST(:rules AS jsonb)")
        params["rules"] = _json(new_rules)
        before["rules"] = row["rules"] if isinstance(row["rules"], list) else json.loads(row["rules"])
        after["rules"] = new_rules
    if payload.strategy is not None:
        _set("strategy", "strategy", payload.strategy, row["strategy"])
    if payload.status is not None:
        _set("status", "status", payload.status, row["status"])

    if not sets:
        raise errors.bad_request("没有任何要修改的字段", 40001)

    sets.append("updated_at = now()")
    await db.execute(text(f"UPDATE paper_rules SET {', '.join(sets)} WHERE id = :rid"), params)
    await _write_change_log(
        db, entity_type="paper_rule", entity_id=rule_id, action="update",
        actor_id=actor.id, ip=ip, change_log=f"编辑组卷规则「{after.get('name', row['name'])}」",
        before=before, after=after,
    )
    await db.commit()
    return _rule_out(await _load_rule_row(db, rule_id), actor)


async def delete_paper_rule(
    db: AsyncSession, *, actor: Actor, rule_id: int, ip: str | None = None
) -> PaperRuleDeleteOut:
    """**硬删除**。

    `paper_rules` 没有 `is_deleted` 列，也不被 `exams` 外键引用 ——
    删掉规则不会影响已经组好的卷子（它们各自持有自己的题目与分段）。
    想保留规则又不想用，把 `status` 改成 `off` 即可。
    """
    row = await _load_rule_row(db, rule_id)
    _ensure_subject_visible(actor, int(row["subject_id"]), "规则所属科目")
    await db.execute(text("DELETE FROM paper_rules WHERE id = :rid"), {"rid": rule_id})
    await _write_change_log(
        db, entity_type="paper_rule", entity_id=rule_id, action="delete",
        actor_id=actor.id, ip=ip, change_log=f"删除组卷规则「{row['name']}」（硬删除）",
        before={"name": row["name"], "rules": row["rules"]}, after=None,
    )
    await db.commit()
    return PaperRuleDeleteOut(
        id=rule_id,
        name=row["name"],
        hard_deleted=True,
        message="规则已删除。已用它组过的试卷不受影响；想停用而不删除，请改用 status=off。",
    )


# ============================================================ 试卷 CRUD


_EXAM_SELECT = """
SELECT e.id, e.subject_id, e.professional, e.title, e.type, e.exam_year, e.paper_no,
       e.total_score, e.pass_score, e.question_count, e.duration_min, e.difficulty,
       e.has_subjective, e.intro_html, e.rule_config, e.is_free, e.status, e.published_at,
       e.is_deleted, e.created_by, e.created_at, e.updated_at,
       s.name AS subject_name, u.nickname AS creator_nickname, u.phone AS creator_phone
FROM exams e
LEFT JOIN subjects s ON s.id = e.subject_id
LEFT JOIN users u    ON u.id = e.created_by
"""


def _exam_item(row: Any) -> ExamListItem:
    return ExamListItem(
        id=int(row["id"]),
        subject_id=int(row["subject_id"]),
        subject_name=row.get("subject_name"),
        title=row["title"],
        type=row["type"],
        status=row["status"],
        exam_year=row.get("exam_year"),
        paper_no=row.get("paper_no"),
        question_count=int(row["question_count"] or 0),
        total_score=float(row["total_score"] or 0),
        pass_score=float(row["pass_score"] or 0),
        duration_min=int(row["duration_min"] or 0),
        difficulty=float(row["difficulty"] or 0),
        has_subjective=bool(row["has_subjective"]),
        is_free=bool(row["is_free"]),
        is_deleted=bool(row.get("is_deleted")),
        published_at=row.get("published_at"),
        updated_at=row["updated_at"],
        created_by=int(row["created_by"]) if row.get("created_by") else None,
        created_by_name=row.get("creator_nickname") or row.get("creator_phone"),
    )


async def list_exams(
    db: AsyncSession,
    *,
    viewer: ScopeViewer,
    page: int = 1,
    page_size: int = 20,
    subject_id: int | None = None,
    exam_type: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[ExamListItem], int]:
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    scope_sql, scope_params = _scope_clause(viewer, "e.subject_id")
    where.append(scope_sql.lstrip(" AND ") or "1 = 1")
    params.update(scope_params)
    if not include_deleted:
        where.append("e.is_deleted = false")
    if subject_id is not None:
        where.append("e.subject_id = :subject_id")
        params["subject_id"] = subject_id
    if exam_type:
        where.append("e.type = :exam_type")
        params["exam_type"] = exam_type
    if status:
        where.append("e.status = :status")
        params["status"] = status
    if keyword:
        where.append("(e.title ILIKE :kw OR e.paper_no ILIKE :kw)")
        params["kw"] = f"%{keyword}%"

    where_sql = " AND ".join(where)
    total = await db.scalar(text(f"SELECT count(*) FROM exams e WHERE {where_sql}"), params)
    rows = (
        await db.execute(
            text(
                f"{_EXAM_SELECT} WHERE {where_sql} "
                "ORDER BY e.updated_at DESC, e.id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
    ).mappings().all()
    return [_exam_item(r) for r in rows], int(total or 0)


async def _load_exam_row(db: AsyncSession, exam_id: int, *, for_update: bool = False) -> Any:
    sql = _EXAM_SELECT + " WHERE e.id = :eid"
    if for_update:
        sql += " FOR UPDATE OF e"
    row = (await db.execute(text(sql), {"eid": exam_id})).mappings().first()
    if row is None:
        raise errors.not_found("试卷不存在", 40401)
    return row


def _section_score(count: int, score_per: float) -> float:
    return round(float(count) * float(score_per), 2)


async def create_exam(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    payload: ExamCreateIn,
    ip: str | None = None,
) -> ExamDetail:
    _ensure_subject_visible(actor, payload.subject_id, "试卷所属科目")
    await _assert_subject_usable(db, payload.subject_id)

    exam_id = next_id()
    total_planned = sum(s.question_count for s in payload.sections)
    score_planned = round(sum(_section_score(s.question_count, s.score_per) for s in payload.sections), 2)
    has_subjective = any(s.question_type not in OBJECTIVE_TYPES for s in payload.sections)

    await db.execute(
        text(
            "INSERT INTO exams (id, subject_id, professional, title, type, exam_year, paper_no, "
            "  source_type, total_score, pass_score, question_count, duration_min, difficulty, "
            "  has_subjective, intro_html, rule_config, is_free, status, created_by) "
            "VALUES (:id, :sid, :prof, :title, :type, :year, :paper_no, 'self', 0, :pass, 0, :dur, 0, "
            "  :subj, :intro, CAST('{}' AS jsonb), :free, 'draft', :by)"
        ),
        {
            "id": exam_id, "sid": payload.subject_id, "prof": payload.professional,
            "title": payload.title, "type": payload.type, "year": payload.exam_year,
            "paper_no": payload.paper_no, "pass": payload.pass_score,
            "dur": payload.duration_min, "subj": has_subjective,
            "intro": payload.intro_html, "free": payload.is_free, "by": actor.id,
        },
    )
    for idx, s in enumerate(sorted(payload.sections, key=lambda x: x.sort_no) or []):
        await _insert_section(db, exam_id, seq=idx + 1, section=s)

    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="create",
        actor_id=actor.id, ip=ip, change_log=f"新建试卷「{payload.title}」",
        before=None,
        after={"title": payload.title, "subject_id": payload.subject_id,
               "sections": total_planned, "planned_score": score_planned},
    )
    await db.commit()
    return await get_exam_detail(db, exam_id=exam_id, viewer=actor)


async def _insert_section(db: AsyncSession, exam_id: int, *, seq: int, section: ExamSectionIn) -> int:
    sid = next_id()
    await db.execute(
        text(
            "INSERT INTO exam_sections (id, exam_id, seq, name, question_type, question_count, "
            "  score_per, section_score, sort_no) "
            "VALUES (:id, :eid, :seq, :name, :qt, :cnt, :sp, :ss, :sort)"
        ),
        {
            "id": sid, "eid": exam_id, "seq": seq,
            "name": section.name or TYPE_LABELS.get(section.question_type, section.question_type),
            "qt": section.question_type, "cnt": section.question_count,
            "sp": section.score_per,
            "ss": _section_score(section.question_count, section.score_per),
            "sort": section.sort_no,
        },
    )
    return sid


async def update_exam(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    payload: ExamUpdateIn,
    ip: str | None = None,
) -> ExamDetail:
    row = await _load_exam_row(db, exam_id, for_update=True)
    if row["is_deleted"]:
        raise errors.bad_request("试卷已删除，无法编辑", 40001)
    _ensure_subject_visible(actor, int(row["subject_id"]), "试卷所属科目")

    sets: list[str] = []
    params: dict[str, Any] = {"eid": exam_id}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    def _set(col: str, key: str, value: Any, stored: Any) -> None:
        sets.append(f"{col} = :{key}")
        params[key] = value
        before[col] = stored
        after[col] = value

    if payload.title is not None:
        _set("title", "title", payload.title, row["title"])
    if payload.type is not None:
        _set("type", "type", payload.type, row["type"])
    if payload.professional is not None:
        _set("professional", "prof", payload.professional, row.get("professional"))
    if payload.exam_year is not None:
        _set("exam_year", "year", payload.exam_year, row.get("exam_year"))
    if payload.paper_no is not None:
        _set("paper_no", "paper_no", payload.paper_no, row.get("paper_no"))
    if payload.duration_min is not None:
        _set("duration_min", "dur", payload.duration_min, int(row["duration_min"]))
    if payload.pass_score is not None:
        _set("pass_score", "pass", payload.pass_score, float(row["pass_score"] or 0))
    if payload.intro_html is not None:
        _set("intro_html", "intro", payload.intro_html, row.get("intro_html"))
    if payload.is_free is not None:
        _set("is_free", "free", payload.is_free, bool(row["is_free"]))

    # 这里**不再**有 `sections` 分支 —— 它已从 `ExamUpdateIn` 上彻底移除，
    # 传进来会被 Pydantic 挡在进门之前（`extra="forbid"` + `_reject_sections`）。
    # 别在这里再写一遍"如果传了 sections 就……"：那等于把已经拆掉的耦合又接回去。

    if not sets:
        raise errors.bad_request("没有任何要修改的字段", 40001)
    if sets:
        sets.append("updated_at = now()")
        await db.execute(text(f"UPDATE exams SET {', '.join(sets)} WHERE id = :eid"), params)

    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="update",
        actor_id=actor.id, ip=ip, change_log=f"编辑试卷「{after.get('title', row['title'])}」",
        before=before, after=after,
    )
    await db.commit()
    return await get_exam_detail(db, exam_id=exam_id, viewer=actor)


# ---------------------------------------------------------------- 详情


async def _load_sections(db: AsyncSession, exam_id: int) -> list[Any]:
    return (
        await db.execute(
            text(
                "SELECT s.id, s.seq, s.name, s.question_type, s.question_count, s.score_per, "
                "       s.section_score, s.sort_no, "
                "       COALESCE(x.cnt, 0) AS actual_count, COALESCE(x.sc, 0) AS actual_score "
                "FROM exam_sections s "
                "LEFT JOIN (SELECT section_id, count(*) cnt, COALESCE(sum(score),0) sc "
                "             FROM exam_questions WHERE exam_id = :eid GROUP BY section_id) x "
                "       ON x.section_id = s.id "
                "WHERE s.exam_id = :eid "
                "ORDER BY s.sort_no, s.seq"
            ),
            {"eid": exam_id},
        )
    ).mappings().all()


async def _load_exam_questions(db: AsyncSession, exam_id: int) -> list[Any]:
    """取卷面题目 + 题目**当前**版本 + **锁定版本**。

    锁定版本优先读 `exam_questions.locked_version`（Batch 7 补的独立列）；
    该列为 NULL 时由 `_question_locks()` 回退到 `rule_config.question_locks`。
    """
    return (
        await db.execute(
            text(
                "SELECT eq.id, eq.section_id, eq.question_id, eq.seq, eq.score, "
                "       eq.locked_version, "
                "       q.type AS question_type, q.stem, q.difficulty, q.chapter_id, "
                "       q.version AS current_version, q.is_deleted, q.status AS q_status "
                "FROM exam_questions eq "
                "JOIN questions q ON q.id = eq.question_id "
                "WHERE eq.exam_id = :eid "
                "ORDER BY eq.seq, eq.id"
            ),
            {"eid": exam_id},
        )
    ).mappings().all()


def _question_locks(qrows: Sequence[Any], rc: dict[str, Any]) -> dict[str, int]:
    """卷面每道题锁定的版本，键是 `question_id` 的字符串形式。

    **优先读 `exam_questions.locked_version`**（独立列，Batch 7 前置修正加的），
    该列为 NULL 时才回退到 `rule_config.question_locks`（**deprecated**，下批清理）。

    回退分支保留的理由：让"加了列但还没回填"的库仍然工作 ——
    迁移脚本虽然会回填，但生产库执行迁移与代码上线之间总有时间窗。
    """
    fallback = {str(k): int(v) for k, v in (rc.get("question_locks") or {}).items()}
    out: dict[str, int] = {}
    for r in qrows:
        qid = int(r["question_id"])
        locked = r.get("locked_version")
        if locked is None:
            locked = fallback.get(str(qid))
        if locked is not None:
            out[str(qid)] = int(locked)
    return out


async def _load_locked_snapshots(db: AsyncSession, pairs: Sequence[tuple[int, int]]) -> dict[tuple[int, int], dict]:
    """批量取锁定版本快照。`pairs` = [(question_id, version), ...]。"""
    if not pairs:
        return {}
    qids = sorted({p[0] for p in pairs})
    rows = (
        await db.execute(
            text(
                "SELECT question_id, version, snapshot FROM question_versions "
                "WHERE question_id = ANY(CAST(:qids AS bigint[]))"
            ),
            {"qids": qids},
        )
    ).mappings().all()
    return {
        (int(r["question_id"]), int(r["version"])): (
            json.loads(r["snapshot"]) if isinstance(r["snapshot"], str) else dict(r["snapshot"] or {})
        )
        for r in rows
    }


def _stem_preview(stem: str | None, limit: int = 60) -> str:
    s = (stem or "").replace("\n", " ").strip()
    return s if len(s) <= limit else s[:limit] + "…"


async def get_exam_detail(db: AsyncSession, *, exam_id: int, viewer: ScopeViewer) -> ExamDetail:
    """试卷详情。

    **已归档（软删除）的卷仍可查看** —— 对称 Batch 4 题目软删除的处理：
    详情页要能显示"这张卷已归档"，而不是给个 404 让用户以为它从没存在过。
    只有真正不存在的 id 才 `40401`。
    """
    row = await _load_exam_row(db, exam_id)
    _ensure_subject_visible(viewer, int(row["subject_id"]), "试卷所属科目")

    deleted = bool(row["is_deleted"])

    rc = _rule_config_of(row)
    qrows = await _load_exam_questions(db, exam_id)
    locks = _question_locks(qrows, rc)

    # 只有"锁定版本 != 当前版本"的行才去取快照，避免为 100 道题白读 100 个 JSONB
    drift_pairs = [
        (int(r["question_id"]), locks[str(int(r["question_id"]))])
        for r in qrows
        if str(int(r["question_id"])) in locks
        and int(locks[str(int(r["question_id"]))]) != int(r["current_version"])
    ]
    snapshots = await _load_locked_snapshots(db, drift_pairs)

    qitems: list[ExamQuestionItem] = []
    for r in qrows:
        qid = int(r["question_id"])
        locked = locks.get(str(qid))
        drift = locked is not None and locked != int(r["current_version"])
        stem = r["stem"]
        if drift:
            snap = snapshots.get((qid, int(locked)))
            if snap and snap.get("stem"):
                # ★ 已发布试卷展示**锁定版本**的题干，而不是被改过的当前版本。
                stem = snap["stem"]
        qitems.append(
            ExamQuestionItem(
                id=int(r["id"]),
                question_id=qid,
                section_id=int(r["section_id"]) if r.get("section_id") else None,
                seq=int(r["seq"]),
                score=float(r["score"]),
                question_type=r["question_type"],
                stem_preview=_stem_preview(stem),
                difficulty=r.get("difficulty"),
                chapter_id=int(r["chapter_id"]) if r.get("chapter_id") else None,
                locked_version=int(locked) if locked is not None else None,
                current_version=int(r["current_version"]),
                version_drift=drift,
            )
        )

    by_section: dict[int, list[ExamQuestionItem]] = {}
    for qi in qitems:
        if qi.section_id is not None:
            by_section.setdefault(int(qi.section_id), []).append(qi)

    sections = [
        ExamSectionDetail(
            id=int(s["id"]), seq=int(s["seq"]), name=s["name"],
            question_type=s["question_type"], question_count=int(s["question_count"]),
            score_per=float(s["score_per"]), section_score=float(s["section_score"]),
            sort_no=int(s["sort_no"]), actual_count=int(s["actual_count"]),
            actual_score=float(s["actual_score"]),
            questions=by_section.get(int(s["id"]), []),
        )
        for s in await _load_sections(db, exam_id)
    ]

    shortfalls = [Shortfall.model_validate(x) for x in (rc.get("shortfalls") or [])]
    validation = await validate_exam(db, exam_id=exam_id, viewer=viewer)

    item = _exam_item(row)
    return ExamDetail(
        **item.model_dump(exclude={"validation"}),
        professional=row.get("professional"),
        intro_html=row.get("intro_html"),
        sections=sections,
        shortfalls=shortfalls,
        validation=validation,
        created_at=row["created_at"],
        can_edit=not deleted
        and not (row["status"] == "published" and not rc.get("allow_edit_after_publish")),
        can_compose=not deleted and row["status"] in ("draft", "reviewing", "off"),
        can_publish=not deleted and row["status"] in ("draft", "reviewing"),
        can_unpublish=not deleted and row["status"] == "published",
        can_delete=not deleted,
    )


async def replace_exam_sections(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    payload: ExamSectionsReplaceIn,
    ip: str | None = None,
) -> ExamSectionsReplaceOut:
    """重建卷面结构 —— **唯一**允许改分段的入口（坑 42 的结构防护）。

    原先这件事混在 `PUT /admin/exams/{id}` 里：只要 body 里带了 `sections`，
    就会 `DELETE FROM exam_questions WHERE exam_id = ...`。一个可选字段藏着
    "整卷清空题目"的副作用，签名上完全看不出来。

    现在拆到独立端点 + 两道闸：

    1. **`expected_question_count` 必须与库里一致**，否则 `40901` 且不写库。
       它同时干两件事：防误操作，以及**乐观并发** ——
       两个人先后改同一张卷时，后者必然对不上，不会把前一个人的题默默清掉。
    2. 净减少超过阈值时其他写路径会被 `_assert_no_mass_question_loss` 拦下，
       并把调用方指到本接口 —— 所以这里**不再二次拦截**（否则提示会变成循环引用）。

    ⚠️ **题目本身不受影响**：删的只是 `exam_questions` 里"这张卷的卷面行"。
    但重建后需要重新组卷或加题把题补回来 —— 响应里的 `message` 会说明这一点。
    """
    row = await _load_exam_row(db, exam_id, for_update=True)
    if row["is_deleted"]:
        raise errors.not_found("试卷不存在", 40401)
    _ensure_subject_visible(actor, int(row["subject_id"]), "试卷所属科目")
    _assert_exam_mutable(row, action="重建卷面结构")

    current = await _count_paper_rows(db, exam_id)
    expected = payload.expected_question_count
    if expected != current:
        raise errors.conflict(
            f"卷面已变化，请刷新后重试：你读到的是 {expected} 道题，当前是 {current} 道。"
            "这一步会重建整个卷面，所以必须确认手上这份是最新的。",
            40901,
        )

    #: 计划题数合计（用于 message；实际题数由 _refresh_exam_totals 重算为 0）
    planned = sum(s.question_count for s in payload.sections)

    await db.execute(text("DELETE FROM exam_sections WHERE exam_id = :eid"), {"eid": exam_id})
    await db.execute(text("DELETE FROM exam_questions WHERE exam_id = :eid"), {"eid": exam_id})
    for idx, s in enumerate(sorted(payload.sections, key=lambda x: x.sort_no)):
        await _insert_section(db, exam_id, seq=idx + 1, section=s)
    await _refresh_exam_totals(db, exam_id)

    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="update",
        actor_id=actor.id, ip=ip,
        change_log=(
            f"重建卷面结构：{len(payload.sections)} 个分段、计划 {planned} 道题"
            + (f"，清空原有 {current} 道卷面题" if current else "")
        ),
        before={"sections": "旧分段", "questions_removed": current},
        after={
            "sections": [s.model_dump(mode="json") for s in payload.sections],
            "expected_question_count": expected,
        },
    )
    await db.commit()

    fresh = await _load_exam_row(db, exam_id)
    sections_out = await _load_sections(db, exam_id)
    msg = f"卷面结构已重建为 {len(payload.sections)} 个分段、计划 {planned} 道题。"
    if current:
        msg += (
            f"原有 {current} 道卷面题已清空（**题目本身在题库里不受影响**），"
            "请重新组卷或用「加题」补回来 —— 补之前校验会提示分段题数与计划不符。"
        )
    return ExamSectionsReplaceOut(
        exam_id=exam_id,
        removed_questions=current,
        question_count=int(fresh["question_count"] or 0),
        total_score=float(fresh["total_score"] or 0),
        sections=[_section_out(s) for s in sections_out],
        message=msg,
    )


# ============================================================ 组卷算法（纯函数，可单测）


def relaxation_steps(rule: RuleItem) -> list[tuple[str, str]]:
    """列出该规则的放宽梯度。返回 `[(阶段名, 说明), ...]`，按顺序尝试。

    第 1 步叫 `exact` 而不是"空约束"—— 即使规则什么都没限，它也是"精确匹配"这一档。
    """
    steps: list[tuple[str, str]] = [("exact", "精确匹配（题型 + 难度 + 知识点/章节 + 年份）")]
    has_hard_filter = rule.difficulty is not None
    has_soft_filter = bool(rule.kp_ids or rule.chapter_ids or rule.year)
    if has_hard_filter:
        steps.append(("relax_difficulty", "放宽难度范围（难度不再限制）"))
    if has_soft_filter:
        steps.append(("relax_scope", "放宽范围（知识点/章节/年份不再限制）"))
    return steps


def weighted_sample(
    rows: Sequence[dict[str, Any]],
    k: int,
    rng: random.Random,
    *,
    prefer_unused: bool = True,
) -> list[dict[str, Any]]:
    """按权重**无放回**抽样 `k` 条。

    权重：从未进过任何试卷（`usage_count == 0`）的题拿 `UNUSED_WEIGHT`，
    其余按 `1 / (1 + usage_count)` 递减。`prefer_unused=False` 时统一按使用次数加权。

    用 `rng` 而不是全局 `random`：传同一个种子能复现同一张卷，
    验收与排障都需要这个性质（见 `ExamComposeIn.seed`）。
    """
    pool = list(rows)
    picked: list[dict[str, Any]] = []
    while pool and len(picked) < k:
        weights: list[float] = []
        for r in pool:
            usage = int(r.get("usage_count") or 0)
            if prefer_unused and usage == 0:
                weights.append(UNUSED_WEIGHT)
            else:
                weights.append(1.0 / (1.0 + usage))
        total = sum(weights)
        if total <= 0:
            # 理论上不可达（权重恒 > 0）；真出现就退化成顺序取，别死循环。
            picked.extend(pool[: k - len(picked)])
            break
        x = rng.random() * total
        acc = 0.0
        choice = len(pool) - 1
        for i, w in enumerate(weights):
            acc += w
            if x <= acc:
                choice = i
                break
        picked.append(pool.pop(choice))
    return picked


def build_shortfall(
    rule: RuleItem, rule_index: int, got: int, step_counts: dict[str, int]
) -> Shortfall:
    """把"没凑够"这件事说清楚：差多少、放宽到哪一步、每步有多少候选。"""
    missing = max(0, rule.count - got)
    detail = "，".join(f"{name}={cnt}" for name, cnt in step_counts.items())
    return Shortfall(
        rule=rule,
        rule_label=rule.label,
        rule_index=rule_index,
        question_type=rule.type,
        need=rule.count,
        got=got,
        missing=missing,
        reason=(
            f"该规则需要 {rule.count} 道，实际只有 {got} 道，缺 {missing} 道。"
            f"已逐级放宽（{detail}）后仍不足。"
            "系统**不会用其它题目顶替**，请补充题库、放宽规则或手工加题。"
        ),
    )


# ============================================================ 组卷


_CANDIDATE_SQL = """
SELECT q.id, q.type, q.difficulty, q.chapter_id, q.knowledge_point_id, q.exam_year,
       q.version, COALESCE(u.cnt, 0) AS usage_count
FROM questions q
LEFT JOIN (
    SELECT question_id, count(*) AS cnt FROM exam_questions GROUP BY question_id
) u ON u.question_id = q.id
WHERE q.is_deleted = false
  AND q.status = 'published'
  AND q.parent_id IS NULL          -- 案例小问不作为独立抽题单位
  AND q.subject_id = :subject_id
  AND q.type = :qtype
"""


async def _candidates(
    db: AsyncSession,
    *,
    subject_id: int,
    rule: RuleItem,
    stage: str,
    excluded: Sequence[int],
) -> list[dict[str, Any]]:
    where = [_CANDIDATE_SQL]
    params: dict[str, Any] = {"subject_id": subject_id, "qtype": rule.type}

    if stage == "exact":
        if rule.difficulty is not None:
            where.append(" AND q.difficulty BETWEEN :dlo AND :dhi")
            params["dlo"], params["dhi"] = rule.difficulty
        if rule.kp_ids:
            where.append(" AND q.knowledge_point_id = ANY(CAST(:kp_ids AS bigint[]))")
            params["kp_ids"] = sorted(rule.kp_ids)
        if rule.chapter_ids:
            where.append(" AND q.chapter_id = ANY(CAST(:chapter_ids AS bigint[]))")
            params["chapter_ids"] = sorted(rule.chapter_ids)
        if rule.year is not None:
            where.append(" AND q.exam_year = :year")
            params["year"] = rule.year
    elif stage == "relax_difficulty":
        # 保留范围类约束，只放开难度
        if rule.kp_ids:
            where.append(" AND q.knowledge_point_id = ANY(CAST(:kp_ids AS bigint[]))")
            params["kp_ids"] = sorted(rule.kp_ids)
        if rule.chapter_ids:
            where.append(" AND q.chapter_id = ANY(CAST(:chapter_ids AS bigint[]))")
            params["chapter_ids"] = sorted(rule.chapter_ids)
        if rule.year is not None:
            where.append(" AND q.exam_year = :year")
            params["year"] = rule.year
    # stage == "relax_scope"：只留 type + subject

    if excluded:
        where.append(" AND q.id <> ALL(CAST(:excluded AS bigint[]))")
        params["excluded"] = sorted(int(x) for x in excluded)

    sql = "".join(where) + " ORDER BY q.id"
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


async def _resolve_rules(
    db: AsyncSession, *, exam_row: Any, payload: ExamComposeIn
) -> list[RuleItem]:
    """把"从哪来规则"这件事收口到一处：内联 > 规则表 > 试卷已有分段。"""
    if payload.rules:
        return list(payload.rules)

    if payload.rule_id is not None:
        rule_row = await _load_rule_row(db, payload.rule_id)
        if rule_row["status"] != "on":
            raise errors.bad_request("该组卷规则已停用（status=off），请先启用或改用内联规则", 40001)
        raw = rule_row["rules"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        return [RuleItem.model_validate(x) for x in (raw or [])]

    # 回退：用试卷自己的分段推导（每段一条规则，难度/知识点不限）
    sections = await _load_sections(db, int(exam_row["id"]))
    if not sections:
        raise errors.bad_request(
            "既没有传 rules/rule_id，试卷本身也没有分段，无法推导组卷规则", 40001
        )
    return [
        RuleItem(
            type=s["question_type"],
            count=int(s["question_count"]) or 1,
            score=float(s["score_per"]),
        )
        for s in sections
    ]


async def _draw_rule(
    db: AsyncSession,
    *,
    subject_id: int,
    rule: RuleItem,
    rng: random.Random,
    excluded: set[int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """**单条规则**的抽题：沿放宽阶梯逐档找，够数就停。

    返回 `(抽到的行, 每一档的候选数)`。

    ## 为什么抽成一个函数

    这个循环原本只存在于 `compose_exam` 里。Pass 2b 要加"保存规则前试算
    这条规则能抽到多少题"（dry-run），那份逻辑**必须和真正组卷完全一致** ——
    否则试算说"能抽满"、真组卷却报缺口，用户会彻底不信这个预览。

    两份实现漂移是迟早的事（改一处漏一处），所以抽出来给两边共用。
    **预览的 `need/got/missing` 与组卷的 `shortfalls` 因此天然同源。**

    `excluded` 是**跨规则**的去重集合（同一道题不能进两次卷面），
    所以调用方要自己维护并传进来。
    """
    step_counts: dict[str, int] = {}
    picked_rows: list[dict[str, Any]] = []
    for stage, _desc in relaxation_steps(rule):
        rows = await _candidates(
            db, subject_id=subject_id, rule=rule, stage=stage, excluded=list(excluded)
        )
        step_counts[stage] = len(rows)
        if len(rows) >= rule.count:
            return weighted_sample(rows, rule.count, rng, prefer_unused=rule.prefer_unused), step_counts
        # 这一档不够：先记下它作为当前最好的候选，继续放宽
        picked_rows = rows

    # 放宽到底还是不够 —— **绝不静默凑数**，抽到多少就是多少
    got = min(len(picked_rows), rule.count)
    return weighted_sample(picked_rows, got, rng, prefer_unused=rule.prefer_unused), step_counts


async def compose_exam(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    payload: ExamComposeIn,
    ip: str | None = None,
) -> ExamComposeOut:
    started = time.perf_counter()
    exam = await _load_exam_row(db, exam_id, for_update=True)
    if exam["is_deleted"]:
        raise errors.not_found("试卷不存在", 40401)
    _ensure_subject_visible(actor, int(exam["subject_id"]), "试卷所属科目")
    _assert_exam_mutable(exam, action="重新组卷")

    subject_id = payload.subject_id or int(exam["subject_id"])
    if payload.subject_id is not None:
        _ensure_subject_visible(actor, subject_id, "组卷科目")

    rules = await _resolve_rules(db, exam_row=exam, payload=payload)
    if not any(r.type in OBJECTIVE_TYPES or r.type in ("case", "fill", "essay") for r in rules):
        raise errors.bad_request("没有可用的抽题规则", 40001)

    rng = random.Random(payload.seed) if payload.seed is not None else random.Random()

    #: 兜底守卫的基准：**动手之前**数一次卷面题数（坑 42）。
    #: `replace=true` 会先清空再重建 —— 调用方的语义是"重新抽题"，
    #: 未必意识到这会把手上已有的题删掉，所以这里要能从结果上拦住。
    rows_before = await _count_paper_rows(db, exam_id)

    if payload.replace:
        await db.execute(text("DELETE FROM exam_questions WHERE exam_id = :eid"), {"eid": exam_id})

    # (question_id, rule, section_id) —— section_id 在组卷时就定下来，落库不再二次查表
    chosen: list[tuple[int, RuleItem, int | None]] = []
    shortfalls: list[Shortfall] = []
    picked_ids: set[int] = set()

    if payload.apply_sections:
        await db.execute(text("DELETE FROM exam_sections WHERE exam_id = :eid"), {"eid": exam_id})

    # `apply_sections=False` 时把抽到的题塞进**已有的**分段：按题型找还有余量的段。
    # 找不到匹配题型的段就留 `None` —— 校验会如实报"分段没填满"，
    # 而不是偷偷新建一个段把数字做得好看。
    section_pool: dict[str, list[tuple[int, int]]] = {}  # 题型 -> [(section_id, 剩余容量)]
    if not payload.apply_sections:
        for s in await _load_sections(db, exam_id):
            section_pool.setdefault(s["question_type"], []).append(
                (int(s["id"]), int(s["question_count"]))
            )

    def _reserve_section(qtype: str) -> int | None:
        for i, (sid, left) in enumerate(section_pool.get(qtype, [])):
            if left > 0:
                section_pool[qtype][i] = (sid, left - 1)
                return sid
        return None

    for idx, rule in enumerate(rules):
        picked_rows, step_counts = await _draw_rule(
            db, subject_id=subject_id, rule=rule, rng=rng, excluded=picked_ids
        )
        got = len(picked_rows)
        if got < rule.count:
            shortfalls.append(build_shortfall(rule, idx, got, step_counts))

        section_id: int | None = None
        if payload.apply_sections:
            section_id = await _insert_section(
                db, exam_id,
                seq=idx + 1,
                section=ExamSectionIn(
                    name=TYPE_LABELS.get(rule.type, rule.type),
                    question_type=rule.type,
                    question_count=rule.count,          # ★ 计划题数（用于校验是否填满）
                    score_per=rule.score,
                    sort_no=idx,
                ),
            )

        for r in picked_rows:
            picked_ids.add(int(r["id"]))
            chosen.append(
                (
                    int(r["id"]),
                    rule,
                    section_id
                    if section_id is not None
                    else _reserve_section(rule.type),
                )
            )

    # ---- 落库 ----
    seq = 0
    for question_id, rule, sec_id in chosen:
        seq += 1
        await db.execute(
            text(
                "INSERT INTO exam_questions (id, exam_id, section_id, question_id, seq, score) "
                "VALUES (:id, :eid, :sid, :qid, :seq, :score)"
            ),
            {
                "id": next_id(), "eid": exam_id, "sid": sec_id,
                "qid": question_id, "seq": seq, "score": rule.score,
            },
        )

    await _refresh_exam_totals(db, exam_id)

    # 重新组卷 = 旧的版本锁定作废（卷面都换了，锁定就没意义了）。
    # 两处都要清：独立列置 NULL + JSONB 置空（deprecated 双写，见 publish_exam）。
    # 只在卷子不是 published 时才走到这里，所以不存在"抹掉已发布卷的锁定"的风险。
    await db.execute(
        text("UPDATE exam_questions SET locked_version = NULL WHERE exam_id = :eid"),
        {"eid": exam_id},
    )

    shortfall_dump = [s.model_dump(mode="json") for s in shortfalls]
    new_rc = _merge_rule_config(
        _rule_config_of(exam),
        shortfalls=shortfall_dump,
        compose={
            "at": _now().isoformat(),
            "seed": payload.seed,
            "requested": sum(r.count for r in rules),
            "filled": len(chosen),
            "rules": len(rules),
        },
        # JSONB 侧同步清空（deprecated 双写；独立列已在上面 UPDATE 置 NULL）
        question_locks={},
        locked_at=None,
    )
    await db.execute(
        text("UPDATE exams SET rule_config = CAST(:rc AS jsonb), updated_at = now() WHERE id = :eid"),
        {"rc": _json(new_rc), "eid": exam_id},
    )
    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="update",
        actor_id=actor.id, ip=ip,
        change_log=f"自动组卷：写入 {len(chosen)} 题，缺口 {len(shortfalls)} 条",
        before=None,
        after={"filled": len(chosen), "requested": sum(r.count for r in rules),
               "shortfalls": shortfall_dump},
    )

    # ---- 兜底：这次组卷把卷面题数砍掉太多？（坑 42）----
    # 放在 commit **之前**：一旦拦下，交易整体回滚，一行都没写。
    await _assert_no_mass_question_loss(
        db, exam_id, before=rows_before, action="这次组卷"
    )

    await db.commit()

    sections = await _load_sections(db, exam_id)
    total_score = float(sum(float(c[1].score) for c in chosen))
    if shortfalls:
        msg = (
            f"组卷完成：写入 {len(chosen)} 题，总分 {total_score:g}。"
            f"⚠️ 有 {len(shortfalls)} 条规则未凑够——"
            + "；".join(f"{s.rule_label} 需 {s.need} 只拿到 {s.got}" for s in shortfalls)
            + "。系统未用其它题目顶替，请补充题库或调整规则。"
        )
    else:
        msg = f"组卷完成：写入 {len(chosen)} 题，总分 {total_score:g}，无缺口。"

    return ExamComposeOut(
        exam_id=exam_id,
        status=exam["status"],
        question_count=len(chosen),
        total_score=round(total_score, 2),
        duration_min=int(exam["duration_min"]),
        sections=[_section_out(s) for s in sections],
        shortfalls=shortfalls,
        message=msg,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _section_out(row: Any) -> ExamSectionOut:
    return ExamSectionOut(
        id=int(row["id"]), seq=int(row["seq"]), name=row["name"],
        question_type=row["question_type"], question_count=int(row["question_count"]),
        score_per=float(row["score_per"]), section_score=float(row["section_score"]),
        sort_no=int(row["sort_no"]), actual_count=int(row["actual_count"]),
        actual_score=float(row["actual_score"]),
    )


async def _refresh_exam_totals(db: AsyncSession, exam_id: int) -> None:
    """把 `exams` 的题量/总分/难度/是否含主观题按**实际卷面**重算。

    注意 `total_score` 用实际题目的分值之和，不用分段计划值 ——
    卷面缺题时，实际总分必须如实反映，否则就成了"账面上 100 分、实际 92 分"。
    """
    row = (
        await db.execute(
            text(
                "SELECT count(*) AS cnt, COALESCE(sum(eq.score), 0) AS score, "
                "       COALESCE(avg(q.difficulty), 0) AS diff, "
                "       bool_or(q.type NOT IN ('single','multiple','judge')) AS subj "
                "FROM exam_questions eq JOIN questions q ON q.id = eq.question_id "
                "WHERE eq.exam_id = :eid"
            ),
            {"eid": exam_id},
        )
    ).mappings().first()
    await db.execute(
        text(
            "UPDATE exams SET question_count = :cnt, total_score = :score, "
            "  difficulty = :diff, has_subjective = :subj, updated_at = now() WHERE id = :eid"
        ),
        {
            "cnt": int(row["cnt"] or 0), "score": round(float(row["score"] or 0), 2),
            "diff": round(float(row["diff"] or 0), 1), "subj": bool(row["subj"]),
            "eid": exam_id,
        },
    )


# ============================================================ 规则试算（dry-run，不写库）


async def preview_paper_rule(
    db: AsyncSession,
    *,
    viewer: ScopeViewer,
    payload: PaperRulePreviewIn,
) -> PaperRulePreviewOut:
    """**试算**一组规则能抽到什么程度。**纯读，一行都不写。**

    Pass 2b 的规则编辑页要在保存前给出"这条规则现在能抽到多少题"，
    以及"题库不足"的预警。没有它，用户只能
    **保存 → 建卷 → 组卷 → 发现抽不满 → 回来改**，一轮好几次往返且每次都真实落库。

    ## 三条刻意的设计

    1. **判据与组卷完全同源**：共用 `_draw_rule`。试算说"能抽满"而真组卷报缺口，
       是这类预览最致命的失败 —— 用户会彻底不信它。所以宁可不做，也不另写一份。
    2. **数据范围照常收口**：走 `_ensure_subject_visible`。试算会**回传题目明细**，
       不校验就等于开了一个"绕过数据范围的读题口子"。
    3. **回传 `stage_counts`（放宽阶梯每档的候选数）**：
       只说"缺 73 道"用户不知道该往哪儿使劲；给出"限定题型+难度+知识点时只有 3 道，
       放宽到只限题型也只有 12 道"，他立刻知道该补题库还是松条件。
    """
    subject_id = payload.subject_id
    _ensure_subject_visible(viewer, subject_id, "试算科目")
    # 与建规则同一道校验：科目必须存在且启用。
    # 少了它，用一个不存在的 subject_id 试算会得到"缺口 200 道"这种**误导性结论** ——
    # 用户以为自己规则写错了，其实是科目根本不存在（或已停用）。
    await _assert_subject_usable(db, subject_id)

    started = _now()
    rng = random.Random(payload.seed) if payload.seed is not None else random.Random()

    items: list[RulePreviewItem] = []
    shortfalls: list[Shortfall] = []
    picked_ids: set[int] = set()
    #: 先攒 (规则下标, 规则, 候选行)，题干**最后一次性查**（见函数末尾的说明）
    sampled: list[tuple[int, RuleItem, dict[str, Any]]] = []

    for idx, rule in enumerate(payload.rules):
        rows, step_counts = await _draw_rule(
            db, subject_id=subject_id, rule=rule, rng=rng, excluded=picked_ids
        )
        got = len(rows)
        for r in rows:
            picked_ids.add(int(r["id"]))

        # 与组卷共用同一个 shortfall 构造器 —— 文案、字段含义都保持一致
        if got < rule.count:
            shortfalls.append(build_shortfall(rule, idx, got, step_counts))

        items.append(
            RulePreviewItem(
                rule_index=idx,
                rule_label=rule.label,
                question_type=rule.type,
                need=rule.count,
                got=got,
                missing=max(0, rule.count - got),
                score=rule.score,
                stage_counts=step_counts,
            )
        )

        if payload.include_sample:
            for r in rows:
                if len(sampled) >= payload.sample_limit:
                    break
                sampled.append((idx, rule, r))

    # ---- 样例题干：一次性批量查 ----
    # ⚠️ 刻意**不**把 `q.stem` 塞进 `_CANDIDATE_SQL`：组卷路径也要用那份 SQL，
    # 而候选集可能有几百行，为了预览多搬几百个题干的正文（每条都近千字）不值得。
    # 样例最多 50 条，单独查一次最省。
    stems: dict[int, str] = {}
    if sampled:
        ids = [int(r["id"]) for _, _, r in sampled]
        rows = (
            await db.execute(
                text("SELECT id, stem FROM questions WHERE id = ANY(CAST(:ids AS bigint[]))"),
                {"ids": ids},
            )
        ).mappings().all()
        stems = {int(r["id"]): (r["stem"] or "") for r in rows}

    sample = [
        PreviewQuestionItem(
            question_id=r["id"],
            question_type=r["type"],
            stem_preview=_stem_preview(stems.get(int(r["id"]))),
            difficulty=r.get("difficulty"),
            chapter_id=r.get("chapter_id"),
            score=rule.score,
            rule_index=idx,
            rule_label=rule.label,
        )
        for idx, rule, r in sampled
    ]

    total_need = sum(i.need for i in items)
    total_got = sum(i.got for i in items)
    total_missing = sum(i.missing for i in items)
    total_score = sum(i.got * i.score for i in items)
    planned_score = sum(i.need * i.score for i in items)
    ok = total_missing == 0

    elapsed = int((_now() - started).total_seconds() * 1000)
    if ok:
        message = (
            f"试算通过：{len(items)} 条规则共需 {total_need} 道题，题库都能满足，"
            f"预计 {total_got} 题 / {total_score:g} 分。"
        )
    else:
        worst = max(items, key=lambda i: i.missing)
        message = (
            f"⚠️ 当前题库不足：共缺 {total_missing} 道题"
            f"（缺口最大的是「{worst.rule_label}」，缺 {worst.missing} 道）。"
            f"仍可保存这条规则，但用它组卷会出现同样的缺口 —— "
            f"系统**不会用其它题目顶替**。建议先补题库，或放宽该规则的筛选条件。"
        )

    return PaperRulePreviewOut(
        ok=ok,
        subject_id=subject_id,
        total_need=total_need,
        total_got=total_got,
        total_missing=total_missing,
        total_score=round(total_score, 2),
        planned_score=round(planned_score, 2),
        items=items,
        shortfalls=shortfalls,
        sample=sample,
        duration_ms=elapsed,
        message=message,
    )


# ============================================================ 破坏性变更兜底（坑 42）


async def _count_paper_rows(db: AsyncSession, exam_id: int) -> int:
    """当前**卷面**有多少道题（`exam_questions` 行数，不是题目总数）。"""
    return int(
        await db.scalar(
            text("SELECT count(*) FROM exam_questions WHERE exam_id = :eid"), {"eid": exam_id}
        )
        or 0
    )


async def _assert_no_mass_question_loss(
    db: AsyncSession,
    exam_id: int,
    *,
    before: int,
    ack: bool = False,
    action: str = "本次操作",
) -> None:
    """**diff 兜底**：写操作导致卷面题数净减少超过阈值就拒绝。

    ## 为什么需要它（而不是只靠"拆接口"）

    拆接口解决了"调用方**故意**改卷面结构"这条路，但防不住**副作用**：
    `POST /auto-compose` 传 `replace=true` 时也会先清空卷面再重建 ——
    那是个"重新抽题"的语义，调用方未必意识到它会删掉手里的题。

    **约定（"前端别乱传"）防不住副作用，结构防护才防得住。**
    所以这里不看调用方"想要什么"，只看**实际发生的结果**：
    操作前后各数一次卷面行，净减少超阈值就拦。

    ## 为什么在 commit 之前调用

    交易里数、交易里判、交易里拦 —— 抛异常 → `get_db` 回滚 → **一行都没写**。
    如果放到 commit 之后才数，就只能"删了再报错"，那是不可逆的。

    ## `ack` 的语义

    只有 `PUT /admin/exams/{id}/sections` 会传 `ack=True` ——
    它要求调用方先给出 `expected_question_count`，那本身就是**显式确认**。
    兜底提示把用户指到这个接口，所以它自己不能再被拦（否则提示循环）。

    ## 阈值

    来自 `app_configs.exam.mass_question_loss_threshold`（默认 10）。
    读不到就用默认值继续保护，**不因为配置缺失而放行**。
    """
    if ack:
        return
    after = await _count_paper_rows(db, exam_id)
    loss = before - after
    if loss <= 0:
        return
    threshold = await config_service.mass_question_loss_threshold(db)
    if loss > threshold:
        raise errors.conflict(
            f"{action}会删掉 {loss} 道卷面题（超过阈值 {threshold}），已在写入前拦下。"
            f"结构性重建请改用 PUT /admin/exams/{exam_id}/sections，"
            f"并传 expected_question_count={before} 显式确认。"
            "（阈值来自配置 exam.mass_question_loss_threshold，可按需调整。）",
            40901,
        )


# ============================================================ 卷面校验


async def validate_exam(db: AsyncSession, *, exam_id: int, viewer: ScopeViewer) -> ExamValidateOut:
    """卷面校验。

    分两级：
    - **error** → 阻止发布（题数为 0、分段没填满、题量与总分对不上、含未发布/已删除的题）
    - **warning** → 允许发布但提示（有组卷缺口、未设及格线、题目版本漂移）

    "分段没填满"刻意算 **error** 而不是 warning：分段就是卷面的契约，
    计划 60 道只放了 52 道就发出去，考生拿到的卷子和教研以为的不是同一张。
    要发布就先把分段数字改成实际值（那等于明确认可"这张卷就是 52 道"）。
    """
    row = await _load_exam_row(db, exam_id)
    # ⚠️ **已经归档的卷也要能校验** —— 本函数是只读的，而且 `get_exam_detail` 会在内部
    # 调它来内联校验结果。这里若对 `is_deleted` 抛 404，详情页就会**在上层已经放行之后**
    # 又被这一层拦死（实测踩到：外层 remove 了 404，归档卷详情仍然 404）。
    # 真正要拒绝归档卷的是**写操作**：compose / update / publish，它们各自有检查。
    _ensure_subject_visible(viewer, int(row["subject_id"]), "试卷所属科目")

    errors_out: list[ExamValidateIssue] = []
    warnings: list[ExamValidateIssue] = []
    rc = _rule_config_of(row)

    sections = await _load_sections(db, exam_id)
    qrows = await _load_exam_questions(db, exam_id)

    total_q = len(qrows)
    total_score = round(sum(float(r["score"]) for r in qrows), 2)

    if total_q == 0:
        errors_out.append(
            ExamValidateIssue(level="error", code="NO_QUESTIONS",
                              message="卷面一道题都没有。请先自动组卷或手工加题。")
        )

    for s in sections:
        planned = int(s["question_count"])
        actual = int(s["actual_count"])
        if planned != actual:
            errors_out.append(
                ExamValidateIssue(
                    level="error", code="SECTION_NOT_FILLED",
                    message=(
                        f"分段「{s['name']}」计划 {planned} 道，实际 {actual} 道，"
                        f"差 {abs(planned - actual)} 道。请补齐题目，或把分段题数改成 {actual}。"
                    ),
                )
            )
        expect = _section_score(planned, float(s["score_per"]))
        if abs(expect - float(s["section_score"])) > 0.01:
            errors_out.append(
                ExamValidateIssue(
                    level="error", code="SECTION_SCORE_MISMATCH",
                    message=f"分段「{s['name']}」分值 {s['section_score']} ≠ 题数×每题分 {expect}。",
                )
            )

    if abs(float(row["total_score"] or 0) - total_score) > 0.01:
        errors_out.append(
            ExamValidateIssue(
                level="error", code="TOTAL_SCORE_MISMATCH",
                message=f"试卷登记总分 {row['total_score']} 与实际卷面题分之和 {total_score} 不一致。",
            )
        )
    if int(row["question_count"] or 0) != total_q:
        errors_out.append(
            ExamValidateIssue(
                level="error", code="TOTAL_COUNT_MISMATCH",
                message=f"试卷登记题量 {row['question_count']} 与实际 {total_q} 不一致。",
            )
        )

    dup = [r["question_id"] for r in qrows]
    if len(dup) != len(set(dup)):
        errors_out.append(
            ExamValidateIssue(level="error", code="DUPLICATE_QUESTION",
                              message="同一道题在卷面出现了多次。")
        )

    not_published = [r for r in qrows if r["q_status"] != "published"]
    if not_published:
        errors_out.append(
            ExamValidateIssue(
                level="error", code="QUESTION_NOT_PUBLISHED",
                message=f"有 {len(not_published)} 道题不是「已发布」状态（如草稿）。"
                        "试卷只能使用已发布的题目。",
            )
        )
    deleted = [r for r in qrows if r["is_deleted"]]
    if deleted:
        errors_out.append(
            ExamValidateIssue(
                level="error", code="QUESTION_DELETED",
                message=f"有 {len(deleted)} 道题已被归档（软删除），需要替换。",
            )
        )

    # ---- warning 级 ----
    sf = rc.get("shortfalls") or []
    if sf:
        warnings.append(
            ExamValidateIssue(
                level="warning", code="COMPOSE_SHORTFALL",
                message=f"上次组卷有 {len(sf)} 条规则没凑够题，卷面存在明确缺口（未自动顶替）。",
            )
        )
    if float(row["pass_score"] or 0) <= 0:
        warnings.append(
            ExamValidateIssue(level="warning", code="NO_PASS_SCORE",
                              message="未设置及格线（pass_score=0）。")
        )
    locks = _question_locks(qrows, rc)
    drift = [
        r for r in qrows
        if str(int(r["question_id"])) in locks
        and int(locks[str(int(r["question_id"]))]) != int(r["current_version"])
    ]
    if drift:
        warnings.append(
            ExamValidateIssue(
                level="warning", code="VERSION_DRIFT",
                message=f"有 {len(drift)} 道题在发布后被修改过。试卷仍按**锁定版本**作答与展示"
                        "（不影响已出成绩），如需同步最新内容请重新组卷。",
            )
        )

    return ExamValidateOut(
        exam_id=exam_id,
        ok=not errors_out,
        errors=errors_out,
        warnings=warnings,
        question_count=total_q,
        total_score=total_score,
        checked_at=_now(),
    )


# ============================================================ 发布（版本锁定）


async def publish_exam(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    payload: ExamPublishIn,
    ip: str | None = None,
) -> ExamPublishOut:
    """发布试卷：**先把每道题当前版本快照下来**，再改状态。

    顺序很重要 —— 先写锁再改 status，都在同一个事务里；
    若校验不通过则整体不动（`errors` 直接 40901 抛出，不 commit）。
    """
    row = await _load_exam_row(db, exam_id, for_update=True)
    if row["is_deleted"]:
        raise errors.not_found("试卷不存在", 40401)
    _ensure_subject_visible(actor, int(row["subject_id"]), "试卷所属科目")

    if row["status"] == "published":
        raise errors.conflict("试卷已经是发布状态，无需重复发布。", 40901)
    if row["status"] in ("archived",):
        raise errors.conflict("已归档的试卷不能发布。", 40901)

    # ★ 强制走校验：不通过不允许发布
    check = await validate_exam(db, exam_id=exam_id, viewer=actor)
    if not check.ok:
        detail = "；".join(f"[{e.code}] {e.message}" for e in check.errors)
        raise errors.conflict(f"卷面校验未通过，不能发布：{detail}", 40901)

    qrows = await _load_exam_questions(db, exam_id)
    locks = {str(int(r["question_id"])): int(r["current_version"]) for r in qrows}

    # ★ 主存储：独立列 `exam_questions.locked_version`。
    # 一条 UPDATE 把每道题的**当前**版本写进卷面行 —— 比逐行 UPDATE 少 n 次往返，
    # 且天然原子（同一个事务）。
    await db.execute(
        text(
            "UPDATE exam_questions eq SET locked_version = q.version "
            "FROM questions q "
            "WHERE q.id = eq.question_id AND eq.exam_id = :eid"
        ),
        {"eid": exam_id},
    )

    # ⚠️ JSONB **双写**（deprecated，下批清理）：
    # 保留一次回滚余地 —— 若代码回退到 Batch 7 Pass 1 那版，它只读
    # rule_config.question_locks；不双写就会表现为"锁定凭空丢了"。
    new_rc = _merge_rule_config(
        _rule_config_of(row),
        question_locks=locks,
        locked_at=_now().isoformat(),
        allow_edit_after_publish=payload.allow_edit_after_publish,
    )
    await db.execute(
        text(
            "UPDATE exams SET status = 'published', published_at = now(), "
            "  rule_config = CAST(:rc AS jsonb), updated_at = now() WHERE id = :eid"
        ),
        {"rc": _json(new_rc), "eid": exam_id},
    )
    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="publish",
        actor_id=actor.id, ip=ip,
        change_log=f"发布试卷，锁定 {len(locks)} 道题的版本",
        before={"status": row["status"]}, after={"status": "published", "question_locks": locks},
    )
    await db.commit()

    return ExamPublishOut(
        exam_id=exam_id,
        status="published",
        published_at=_now(),
        question_count=int(row["question_count"] or 0),
        total_score=float(row["total_score"] or 0),
        locked_versions=len(locks),
        message=(
            f"已发布，{len(locks)} 道题都已锁定当前版本。"
            "之后即使题目被修改，本卷仍按锁定版本展示与判分。"
        ),
    )


# ============================================================ 归档（软删除）


async def soft_delete_exam(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    reason: str | None = None,
    ip: str | None = None,
) -> ExamSoftDeleteOut:
    """软删除试卷（归档）。刻意与 Batch 4 的题目软删除保持同一套语义：

    | 行为 | 说明 |
    |---|---|
    | 置 `exams.is_deleted = true` | 只动这一列 |
    | 题目 | **不动**。题目是题库的资产，卷子只是"引用"了它们 |
    | 卷面 | **不删**。`exam_questions` 保留，重新启用后卷面还在 |
    | 列表 | 默认过滤掉；`include_deleted=true` 带出来 |
    | 详情 | **仍可打开**（显示"已归档"），不是 404 |
    | 留痕 | 写 `content_change_logs`（action=delete） |

    已归档的再归档 → `40001`（不静默成功）。
    """
    row = await _load_exam_row(db, exam_id, for_update=True)
    _ensure_subject_visible(actor, int(row["subject_id"]), "试卷所属科目")
    if row["is_deleted"]:
        raise errors.bad_request("试卷已归档，无需重复删除", 40001)

    await db.execute(
        text("UPDATE exams SET is_deleted = true, updated_at = now() WHERE id = :eid"),
        {"eid": exam_id},
    )
    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="delete",
        actor_id=actor.id, ip=ip,
        change_log=f"归档试卷「{row['title']}」" + (f"：{reason}" if reason else ""),
        before={"is_deleted": False, "status": row["status"]},
        after={"is_deleted": True, "status": row["status"]},
    )
    await db.commit()

    is_published = row["status"] == "published"
    return ExamSoftDeleteOut(
        id=exam_id,
        title=row["title"],
        previous_status=row["status"],
        is_deleted=True,
        question_count=int(row["question_count"] or 0),
        message=(
            "试卷已归档：列表默认不再显示（打开「显示已归档」可以看到），详情仍可打开。"
            "题目与卷面数据都保留，不受影响。"
            + ("⚠️ 这张卷原本是**已发布**状态，归档后不应再用于考试。" if is_published else "")
        ),
    )


async def _assert_exam_relations_ok(db: AsyncSession, row: Any) -> None:
    """恢复前校验试卷的关联数据是否还站得住。

    两条检查，都对应"恢复成一张能用的卷"这个真实前提：

    1. **科目仍在启用状态**（`subjects.status='on'`）—— 科目停用后这张卷
       在 C 端不可能被正常取到，恢复它只会让它在列表里挂着点不开。
    2. **已发布的卷，卷面题目不能被归档** —— 这是最要紧的一条：
       `exam_questions` 引用题目，而题目可以被单独软删除。若一份**已发布**的卷
       恢复回来时卷面缺题，考生看到的卷子就是残缺的，而校验器会一直报
       `QUESTION_DELETED`。草稿态的卷不受此限（还在编，缺题很正常）。

    **不允许静默恢复到一个不成立的状态上** —— 与题目恢复是同一条原则。
    """
    problems: list[str] = []

    sub = (
        await db.execute(
            text("SELECT name, status FROM subjects WHERE id = :sid"),
            {"sid": int(row["subject_id"])},
        )
    ).mappings().first()
    if sub is None:
        problems.append("试卷所属科目已不存在")
    elif sub["status"] != "on":
        problems.append(f"试卷所属科目「{sub['name']}」已停用")

    if row["status"] == "published":
        n = await db.scalar(
            text(
                "SELECT count(*) FROM exam_questions eq "
                "JOIN questions q ON q.id = eq.question_id "
                "WHERE eq.exam_id = :eid AND q.is_deleted = true"
            ),
            {"eid": int(row["id"])},
        )
        if n:
            problems.append(
                f"这是一份**已发布**的卷，但卷面有 {int(n)} 道题已被归档"
                "（考生会看到残缺的卷面）"
            )

    if problems:
        raise errors.conflict(
            "试卷关联的数据已失效，拒绝恢复："
            + "；".join(problems)
            + "。请先恢复对应科目 / 卷面题目，或把试卷改回草稿并重新组卷后再恢复。",
            40901,
        )


async def restore_exam(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    ip: str | None = None,
) -> ExamRestoreOut:
    """恢复被归档的试卷（与 `soft_delete_exam` 对称）。

    - **幂等**：试卷本来就没归档 → `code=0` + `already_active=true`，无写入。
    - **恢复前校验关联数据**（见 `_assert_exam_relations_ok`）。
    - 恢复的是"这些卷"，**不动任何题目**（归档时也没动过）。
    - 写 `content_change_logs`（action=restore，diff 为 `{is_deleted: true → false}`）。
    """
    row = await _load_exam_row(db, exam_id, for_update=True)
    _ensure_subject_visible(actor, int(row["subject_id"]), "试卷所属科目")

    if not row["is_deleted"]:
        return ExamRestoreOut(
            id=exam_id, title=row["title"], is_deleted=False, status=row["status"],
            already_active=True,
            message="试卷当前未被归档，无需恢复。",
        )

    await _assert_exam_relations_ok(db, row)

    await db.execute(
        text("UPDATE exams SET is_deleted = false, updated_at = now() WHERE id = :eid"),
        {"eid": exam_id},
    )
    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="restore",
        actor_id=actor.id, ip=ip,
        change_log=f"恢复试卷「{row['title']}」",
        before={"is_deleted": True, "status": row["status"]},
        after={"is_deleted": False, "status": row["status"]},
    )
    await db.commit()

    return ExamRestoreOut(
        id=exam_id, title=row["title"], is_deleted=False, status=row["status"],
        message=(
            f"试卷已恢复，状态仍是「{STATUS_LABELS.get(row['status'], row['status'])}」，"
            "现在会重新出现在默认列表里。"
        ),
    )


# ============================================================ 手动加题 / 移题


def _assert_exam_mutable(row: Any, *, action: str) -> None:
    """写操作前的统一准入：**已发布的卷面是冻结的**。

    收口到一个函数，是因为这判断原本散在 compose / update 里各写一份 ——
    "同一份判断散落多处，改一处漏三处"正是坑 38（外层放行、内层拦死）的成因。
    `update_exam` 只在**改卷面结构**时用它，改标题/简介这类展示字段不受限。
    """
    if row["status"] == "published" and not _rule_config_of(row).get("allow_edit_after_publish"):
        raise errors.conflict(
            f"试卷已发布，卷面已冻结，不能{action}。"
            "请先把试卷下线到草稿态，或在发布时勾选「允许发布后编辑」。",
            40901,
        )


async def add_exam_questions(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    payload: ExamAddQuestionsIn,
    ip: str | None = None,
) -> ExamAddQuestionsOut:
    """往试卷里手动加题。

    逐条判断、逐条给理由 —— 与导入管道同一个哲学：
    **批量操作不因为其中一条有问题就整批失败，但绝不静默丢弃**，
    每一条没加进去的都要能在 `skipped[].reason` 里查到原因。

    六道闸（全部进 `skipped` 而不是整批报错）：

    | 判据 | reason |
    |---|---|
    | 题目不存在 | 题目不存在 |
    | 已归档 | 题目已归档（软删除） |
    | 非已发布 | 题目不是已发布状态 |
    | **科目不一致** | 题目不属于本试卷的科目 |
    | 已在卷面 | 该题已在卷面中 |
    | 找不到同题型分段 | 卷面没有「X 题」分段，请先加一个 |

    **科目一致**这条别省：不加就会出现"经济卷里塞进一道法规题"，
    而校验器只看得懂题型和分值，**看不出科目串了**。
    """
    exam = await _load_exam_row(db, exam_id, for_update=True)
    if exam["is_deleted"]:
        raise errors.not_found("试卷不存在", 40401)
    _ensure_subject_visible(actor, int(exam["subject_id"]), "试卷所属科目")
    _assert_exam_mutable(exam, action="加题/移题")

    sections = await _load_sections(db, exam_id)
    section_ids = {int(s["id"]) for s in sections}
    if payload.section_id is not None and payload.section_id not in section_ids:
        raise errors.bad_request("指定的分段不属于这份试卷", 40001)

    # 题型 -> 该题型的分段 id（按 sort_no 顺序，取第一个）
    by_type: dict[str, int] = {}
    for s in sections:
        by_type.setdefault(s["question_type"], int(s["id"]))
    score_per = {int(s["id"]): float(s["score_per"]) for s in sections}

    # 去重保序：同一个 id 传两次只算一次
    wanted = list(dict.fromkeys(int(q) for q in payload.question_ids))

    rows = (
        await db.execute(
            text(
                "SELECT id, subject_id, type, status, is_deleted FROM questions "
                "WHERE id = ANY(CAST(:ids AS bigint[]))"
            ),
            {"ids": wanted},
        )
    ).mappings().all()
    info = {int(r["id"]): r for r in rows}

    existing = {
        int(r["question_id"])
        for r in (
            await db.execute(
                text("SELECT question_id FROM exam_questions WHERE exam_id = :eid"),
                {"eid": exam_id},
            )
        ).mappings().all()
    }
    seq = int(
        await db.scalar(
            text("SELECT COALESCE(max(seq), 0) FROM exam_questions WHERE exam_id = :eid"),
            {"eid": exam_id},
        )
        or 0
    )

    skipped: list[SkippedQuestion] = []
    to_add: list[tuple[int, int, float]] = []  # (question_id, section_id, score)
    for qid in wanted:
        r = info.get(qid)
        if r is None:
            skipped.append(SkippedQuestion(question_id=qid, reason="题目不存在"))
            continue
        if r["is_deleted"]:
            skipped.append(SkippedQuestion(question_id=qid, reason="题目已归档（软删除）"))
            continue
        if r["status"] != "published":
            skipped.append(
                SkippedQuestion(
                    question_id=qid,
                    reason=f"题目不是已发布状态（当前 {r['status']}）",
                )
            )
            continue
        if int(r["subject_id"]) != int(exam["subject_id"]):
            skipped.append(
                SkippedQuestion(question_id=qid, reason="题目不属于本试卷的科目")
            )
            continue
        if qid in existing:
            skipped.append(SkippedQuestion(question_id=qid, reason="该题已在卷面中"))
            continue

        sid = payload.section_id or by_type.get(r["type"])
        if sid is None:
            label = TYPE_LABELS.get(r["type"], r["type"])
            skipped.append(
                SkippedQuestion(
                    question_id=qid,
                    reason=f"卷面没有「{label}」分段，请先加一个该题型的分段",
                )
            )
            continue

        existing.add(qid)
        to_add.append((qid, int(sid), score_per.get(int(sid), 1.0)))

    for qid, sid, score in to_add:
        seq += 1
        await db.execute(
            text(
                "INSERT INTO exam_questions (id, exam_id, section_id, question_id, seq, score) "
                "VALUES (:id, :eid, :sid, :qid, :seq, :score)"
            ),
            {"id": next_id(), "eid": exam_id, "sid": sid, "qid": qid, "seq": seq, "score": score},
        )

    await _refresh_exam_totals(db, exam_id)
    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="update",
        actor_id=actor.id, ip=ip,
        change_log=f"手动加题：成功 {len(to_add)} 道，跳过 {len(skipped)} 道",
        before=None,
        after={
            "added": len(to_add),
            "question_ids": [q for q, _, _ in to_add],
            "skipped": [s.model_dump(mode="json") for s in skipped],
        },
    )
    await db.commit()

    fresh = await _load_exam_row(db, exam_id)
    sections_out = await _load_sections(db, exam_id)
    if skipped:
        msg = (
            f"已加 {len(to_add)} 道题，跳过 {len(skipped)} 道（原因见 skipped）。"
            f"卷面现有 {int(fresh['question_count'] or 0)} 题。"
        )
    else:
        msg = f"已加 {len(to_add)} 道题，卷面现有 {int(fresh['question_count'] or 0)} 题。"

    return ExamAddQuestionsOut(
        exam_id=exam_id,
        added=len(to_add),
        skipped=skipped,
        question_count=int(fresh["question_count"] or 0),
        total_score=float(fresh["total_score"] or 0),
        sections=[_section_out(s) for s in sections_out],
        message=msg,
    )


async def remove_exam_question(
    db: AsyncSession,
    *,
    actor: Actor,
    actor_name: str,
    exam_id: int,
    exam_question_id: int,
    ip: str | None = None,
) -> ExamRemoveQuestionOut:
    """从试卷里移走一道题（删除卷面行，**不动题目本身**）。

    注意移到这一步之后，分段的**计划题数不会自动变小** ——
    于是 `validate` 会报 `SECTION_NOT_FILLED`（计划 60、实际 59）。
    这是**有意**的：分段是卷面的契约，改动它应当是一个显式动作
    （要么补一道题，要么把分段数字改成实际值）。
    """
    exam = await _load_exam_row(db, exam_id, for_update=True)
    if exam["is_deleted"]:
        raise errors.not_found("试卷不存在", 40401)
    _ensure_subject_visible(actor, int(exam["subject_id"]), "试卷所属科目")
    _assert_exam_mutable(exam, action="加题/移题")

    row = (
        await db.execute(
            text(
                "SELECT id, question_id FROM exam_questions WHERE id = :eqid AND exam_id = :eid"
            ),
            {"eqid": exam_question_id, "eid": exam_id},
        )
    ).mappings().first()
    if row is None:
        raise errors.not_found("卷面题目不存在（可能已被移走）", 40401)

    #: 兜底守卫的基准（坑 42）。单次移题只能减 1 行，默认阈值 10 下必然放行 ——
    #: 挂上它只是为了"任何写接口都过同一道闸"，而不是指望它在这里拦住什么。
    rows_before = await _count_paper_rows(db, exam_id)

    await db.execute(
        text("DELETE FROM exam_questions WHERE id = :eqid"), {"eqid": exam_question_id}
    )
    await _refresh_exam_totals(db, exam_id)
    await _write_change_log(
        db, entity_type="exam", entity_id=exam_id, action="update",
        actor_id=actor.id, ip=ip,
        change_log=f"移出题目 {row['question_id']}",
        before={"exam_question_id": exam_question_id, "question_id": int(row["question_id"])},
        after=None,
    )
    await _assert_no_mass_question_loss(db, exam_id, before=rows_before, action="移出题目")
    await db.commit()

    fresh = await _load_exam_row(db, exam_id)
    sections_out = await _load_sections(db, exam_id)
    unfinished = [s for s in sections_out if int(s["actual_count"]) != int(s["question_count"])]
    msg = (
        f"已移出该题，卷面现有 {int(fresh['question_count'] or 0)} 题。"
        + (
            f"⚠️ 有 {len(unfinished)} 个分段的题数与计划不符，发布前会被校验拦住。"
            if unfinished
            else ""
        )
    )
    return ExamRemoveQuestionOut(
        exam_id=exam_id,
        exam_question_id=exam_question_id,
        question_id=int(row["question_id"]),
        question_count=int(fresh["question_count"] or 0),
        total_score=float(fresh["total_score"] or 0),
        sections=[_section_out(s) for s in sections_out],
        message=msg,
    )


# ============================================================ 变更日志


async def _write_change_log(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    actor_id: int | None,
    ip: str | None = None,
    change_log: str = "",
    before: Any = None,
    after: Any = None,
) -> None:
    """写 `content_change_logs`。diff 固定 `{before, after}` 形状（与题库一致）。

    注意 `operator_ip` 是 INET 列：必须经 `to_inet()` 转成 `ipaddress` 对象，
    直接塞字符串 asyncpg 会报类型不匹配（坑 6 / 坑 13）。
    """
    await db.execute(
        text(
            "INSERT INTO content_change_logs "
            "(id, entity_type, entity_id, action, batch_id, diff, change_log, operator_id, operator_ip) "
            "VALUES (:id, :etype, :eid, :action, NULL, CAST(:diff AS jsonb), :cl, :op, :ip)"
        ),
        {
            "id": next_id(), "etype": entity_type, "eid": entity_id, "action": action,
            "diff": _json({"before": before, "after": after}),
            "cl": (change_log or "")[:500], "op": actor_id, "ip": to_inet(ip),
        },
    )


__all__ = [
    "STATUS_LABELS",
    "TYPE_LABELS",
    "UNUSED_WEIGHT",
    "add_exam_questions",
    "build_shortfall",
    "compose_exam",
    "create_exam",
    "create_paper_rule",
    "delete_paper_rule",
    "get_exam_detail",
    "list_exams",
    "list_paper_rules",
    "publish_exam",
    "preview_paper_rule",
    "relaxation_steps",
    "remove_exam_question",
    "replace_exam_sections",
    "restore_exam",
    "soft_delete_exam",
    "update_exam",
    "update_paper_rule",
    "validate_exam",
    "weighted_sample",
]
