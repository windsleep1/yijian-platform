"""科目（**科目域**的公共数据）—— C 端第一批的第 1 个接口。

    GET /subjects?exam_level=yijian&category=professional

数据源是 `subjects` 表，**没有 ORM 模型**（`app/db/models.py` 里从来没有它，
管理端也没用过）—— 所以这里直接写 SQL，并把"为什么"记下来：
为一次只读查询加一个 ORM 类是**多一处需要同步的东西**（列名改了要改两处），
而这张表目前**只有一个读法**。等出现第二个消费方（按 id 取单个科目 / 写科目）
再补模型；在那之前保持"就这一条 SQL"。

★ 为什么"选专业"不另开一个 `/professions` 接口：
    `subjects.professional`（`jz` / `sz` …）+ 专业课自己的名字**已经构成完整信息**
    （种子里每门实务课都带 `professional` 码与中文名）。
    前端从 `category='professional'` 里挑一个，就是"选专业"这件事本身。
    为此新造一张「专业字典」表 = **凭空发明数据**，而且立刻要维护它的种子、状态位、排序。
    ⇒ 引导页显示专业课的 `name` / `short_name`，落库存 `professional` 码。
"""

from __future__ import annotations

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.c_end import SubjectOut

#: ⚠️ `status='on'` 必须过滤 —— 下线科目出现在引导页里，用户选完就无题可做。
#:
#: ⚠️⚠️ `:category` 一定要**声明类型**（下面的 `bindparams`）：
#:   写成裸 `text(...)` 时，`category=None` 会让 asyncpg 报
#:   `AmbiguousParameterError: could not determine data type of parameter $2`
#:   —— 因为 `:category IS NULL` 里的参数是**无类型 NULL**，PG 推断不出来。
#:   2026-09-28 实测踩到（表现为稳定的 `50001`，而 SQL 本身看着完全正常）。
_SELECT = """
    SELECT id, code, name, short_name, exam_level, category, professional,
           full_score, pass_score, duration_min, color, sort_no
      FROM subjects
     WHERE status = 'on'
       AND exam_level = :exam_level
       AND (:category IS NULL OR category = :category)
     ORDER BY sort_no, id
"""
_STMT = text(_SELECT).bindparams(
    bindparam("exam_level", type_=String), bindparam("category", type_=String)
)


async def list_subjects(
    db: AsyncSession, *, exam_level: str = "yijian", category: str | None = None
) -> list[SubjectOut]:
    rows = await db.execute(_STMT, {"exam_level": exam_level, "category": category})
    return [SubjectOut(**dict(row)) for row in rows.mappings().all()]


__all__ = ["list_subjects"]
