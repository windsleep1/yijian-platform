"""C 端 · 刷题（P2b-1）：选章节 → 建 session → 取 session → 提交判分。

    POST /practice/sessions                 建一次练习（章节 / 错题重练）
    GET  /practice/sessions/{id}            取练习（`doing` = 断点恢复；`finished` = 全可见）
    POST /practice/sessions/{id}/answer     提交一道题并判分
    POST /practice/sessions/{id}/finish     交卷（#7，P2c-1）
    GET  /practice/sessions/{id}/report     报告：总分 / 正确率 / 用时 / 知识点分布（P2c-1）
    GET  /practice/wrong-questions          错题本列表（按科目 / **只筛已标记**）（P2c-2 / P2c-4）
    PUT|DELETE /practice/favorites/{qid}    收藏 / 取消（幂等）（P2c-4）
    PUT|DELETE /practice/marks/{qid}        标记 / 取消（幂等）（P2c-4）
    GET  /practice/favorites                收藏 / 标记 列表（`kind` 切题源）（P2c-4）
    GET  /practice/wrong-questions/{qid}    错题详情（含正确答案与解析）（P2c-2）

⚠️ **本批只做"数据流跑通"**（用户 2026-09-29 的原话）：先证明后端到前端的完整往返，
   再堆交互。**切题 / 答题卡 / 长按标记 / 交卷（#7）** 都留给 P2b-2。

⚠️ 这三个接口**都是"按 id 寻址"** ⇒ 每一个都要**自己再拦一次归属**
   （硬约定 G：列表过滤防"翻到"、防不了"猜到"）。做法统一：
   所有 SQL 都带 `user_id = :me`，查不到就是 404 —— **不是 403**，
   403 会变成一个"这个 id 存在但你没权限"的探测口。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Path, Query

from app.core.deps import CurrentUserDep, DbSession
from app.core.response import Envelope, ok
from app.schemas.c_end import (
    NoteCreateIn,
    NoteListOfQuestionOut,
    NoteListOut,
    NoteOut,
    NoteUpdateIn,
    AnswerIn,
    CollectionListOut,
    FlagOut,
    AnswerResultOut,
    SessionCreateIn,
    SessionOut,
    SessionReportOut,
    WrongDetailOut,
    WrongListOut,
)
from app.services import practice_service

router = APIRouter(prefix="/practice", tags=["C 端 · 刷题"])


@router.post(
    "/sessions",
    response_model=Envelope[dict],
    summary="创建练习（章节练习 / 错题重练）",
    description=(
        "从科目 / 章节里抽题建一次练习。需要登录。\n\n"
        "- `mode='chapter'`（默认）：从科目 / 章节抽题，`subject_id` 必填；\n"
        "- `mode='wrong'`（**错题重练**）：从**我自己的错题本**抽题；`question_ids` 给具体题\n"
        "  （重练这一题 / 这一组），不给则按「最近错的在前」抽；`subject_id` 只当筛选\n"
        "  （不给 = 全部科目 —— 跨科目重练 ⇒ 会话的 `subject_id` 为 NULL）；\n"
        "- 抽题**顺序是确定性的**（未做过的优先，其次按 id）—— 为了让「数据流跑通」可复现、可断言；\n"
        "  随机 / 按掌握度抽题属 P2b-2；\n"
        "- 只抽**客观题**（单选 / 多选 / 判断）；\n"
        "- 该范围下没有题 ⇒ `40401`（**不建空 session**：空 session 在前端看起来和「后端挂了」一样）。"
    ),
)
async def create_session(body: SessionCreateIn, db: DbSession, me: CurrentUserDep) -> dict:
    sid = await practice_service.create_session(
        db,
        user_id=me.id,
        mode=body.mode,
        subject_id=(int(body.subject_id) if body.subject_id else None),
        chapter_id=(int(body.chapter_id) if body.chapter_id else None),
        question_ids=([int(x) for x in body.question_ids] if body.question_ids else None),
        count=body.count,
    )
    return ok({"id": str(sid)})


@router.get(
    "/sessions/{session_id}",
    response_model=Envelope[SessionOut],
    summary="取练习详情（断点恢复 / 报告）",
    description=(
        "**一个 resource、两种状态**：\n\n"
        "- `doing`：断点恢复。**未作答的题不返 `answer` / `analysis`**；\n"
        "  `current_item_id` = 第一道还没答的题（刷新后回到原处就靠它）；\n"
        "- `finished`：报告（卷面全可见）。\n\n"
        "可见性判断在 service 里**只有一处**（`_may_reveal`）—— 避免「列表不漏、详情漏」。"
    ),
)
async def get_session(
    db: DbSession,
    me: CurrentUserDep,
    session_id: int = Path(description="练习 id"),
) -> dict:
    data = await practice_service.get_session(db, user_id=me.id, session_id=session_id)
    return ok(data.model_dump())


@router.get(
    "/wrong-questions",
    response_model=Envelope[WrongListOut],
    summary="错题本列表（可按科目筛选）",
    description=(
        "按 `last_wrong_at` **倒序**（最近错的在前）。需要登录。\n\n"
        "- 只返 `is_removed = false` 的（软删除的行不出现）；\n"
        "- `subject_id` 可选，用于**按科目筛选**；\n"
        "- `subjects` 是**当前筛选口径下**的科目分面（带条数）—— "
        "让前端不必调 `/subjects` 拿到 6 个「点了没反应」的 chip；\n"
        "- 分页 `page`（从 1 起）/ `page_size`（默认 20、上限 50）。"
    ),
)
async def list_wrong_questions(
    db: DbSession,
    me: CurrentUserDep,
    subject_id: int | None = Query(None, description="按科目筛选（不传 = 全部）"),
    marked_only: bool = Query(False, description="只列**我标记过的**（P2c-4）；分面仍为全量"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    data = await practice_service.list_wrong(
        db,
        user_id=me.id,
        subject_id=subject_id,
        marked_only=marked_only,
        page=page,
        page_size=page_size,
    )
    return ok(data.model_dump())


@router.get(
    "/wrong-questions/{question_id}",
    response_model=Envelope[WrongDetailOut],
    summary="错题详情（含正确答案与解析）",
    description=(
        "★ **含正确答案** —— 与「未作答的题不返 `answer`」不矛盾：\n"
        "  那条防的是「**没答就看到答案**」；而错题本的前提是「**你已经答错了**」。\n"
        "  两条规则的目标一致：**答案只在「你已经和这道题交过手」之后才给**。\n\n"
        "⚠️ 因此这里有一道门：**必须真的错过**（`wrong_questions` 里有行）。\n"
        "  没有 ⇒ `40401`（**不是 403**）—— 否则这就是一个\n"
        "  「用 `question_id` 遍历题库拿答案的后门」，比不返答案更糟。"
    ),
)
async def get_wrong_question(
    db: DbSession,
    me: CurrentUserDep,
    question_id: int = Path(description="题目 id"),
) -> dict:
    data = await practice_service.get_wrong_detail(db, user_id=me.id, question_id=question_id)
    return ok(data.model_dump())


@router.post(
    "/sessions/{session_id}/finish",
    response_model=Envelope[SessionReportOut],
    summary="交卷（结束这次练习）并返回报告",
    description=(
        "把这次练习收尾：写 `status='finished'` / `finished_at` / `duration_sec`，并返回报告。\n\n"
        '- **幂等**：已经结束 ⇒ **零写入**，返回同一份报告（判据 = "目标状态已达成"）；\n'
        "- **用时由服务端算**（`now() - started_at`），不接受前端传 —— 可伪造 + 时区 + 时钟不准；\n"
        "- **允许交白卷**（一道没答 ⇒ `accuracy = null`）。\n\n"
        "★ 为什么它和报告是两个接口：交卷是**写**（有副作用、有幂等语义），报告是**读**。\n"
        '把它们合成一个 `POST` 会让"只想看看统计"也产生一次写。'
    ),
)
async def finish_session(
    db: DbSession,
    me: CurrentUserDep,
    session_id: int = Path(description="练习 id"),
) -> dict:
    data = await practice_service.finish_session(db, user_id=me.id, session_id=session_id)
    return ok(data.model_dump())


@router.get(
    "/sessions/{session_id}/report",
    response_model=Envelope[SessionReportOut],
    summary="取练习报告（总分 / 正确率 / 用时 / 知识点分布）",
    description=(
        "结果页要的四个数一次给全。**不返逐题解析**（那是 `GET /practice/sessions/{id}`）。\n\n"
        "- ⚠️ **零分母返 `null`**：一道题都没答时 `accuracy = null`，"
        "前端要显示「—」而不是「0%」（0% 会让用户以为自己全错了）；\n"
        '- `by_kp` 按正确率**升序**（**最弱的在前**）—— 结果页的用处是"知道该补哪儿"；\n'
        "- ★ **不要求 `status='finished'`**：读路径不夹带比读接口更严的准入（`doing` 时也能看统计）。"
    ),
)
async def get_report(
    db: DbSession,
    me: CurrentUserDep,
    session_id: int = Path(description="练习 id"),
) -> dict:
    data = await practice_service.get_report(db, user_id=me.id, session_id=session_id)
    return ok(data.model_dump())


@router.post(
    "/sessions/{session_id}/answer",
    response_model=Envelope[AnswerResultOut],
    summary="提交一道题（判分 + 返回解析）",
    description=(
        "幂等：**这道题已经答过** ⇒ 返回既有结果、**零写入**（`idempotent=true`）。\n\n"
        "提交体：\n"
        '- 单选 / 多选：`value` = 标号数组（`["B"]` / `["A","C"]`）；\n'
        "- 判断：`value` = `[true]` / `[false]`。\n\n"
        "判分：单选 / 判断全对全分；多选全对 1.0、**真子集且无错选**且题目允许部分分 ⇒ 0.5。\n"
        "会话已结束（`status != 'doing'`）时拒绝写入（`40901`）。"
    ),
)
async def submit_answer(
    body: AnswerIn,
    db: DbSession,
    me: CurrentUserDep,
    session_id: int = Path(description="练习 id"),
) -> dict:
    data = await practice_service.submit_answer(
        db,
        user_id=me.id,
        session_id=session_id,
        item_id=int(body.item_id),
        value=body.value,
    )
    return ok(data.model_dump())


# ============================================================ 收藏 / 标记（P2c-4）


async def _flag(db: DbSession, user_id: int, question_id: int, *, kind: str, on: bool) -> dict:
    """四个写端点**共用的那一跳**（只有 HTTP 方法 / 路径不同，逻辑一模一样）。

    ★ 为什么不写成一个通用端点（`PUT /flags/{qid}` 带 `kind` 入参）：
      两个概念**是两行数据**、可以同时存在，各自的"置 / 清"语义一眼可读；
      合成一个只会让"我到底改了什么"需要先看请求体。
    """
    data = await practice_service.set_flag(
        db, user_id=user_id, question_id=question_id, kind=kind, on=on
    )
    return ok(
        FlagOut(
            question_id=str(question_id), marked=data["marked"], favorited=data["favorited"]
        ).model_dump()
    )


@router.put(
    "/favorites/{question_id}",
    response_model=Envelope[FlagOut],
    summary="收藏一道题（**幂等**）",
    description=(
        "置上收藏。**幂等判据 =「目标状态已达成」**（硬约定 C）：已经收藏过 ⇒ "
        "`ON CONFLICT DO NOTHING`，**净零变更**（连 `created_at` 都不动）。\n\n"
        "- 题目必须 **`published` 且未软删**，否则 `40401`（**不是 403**）——\n"
        "  否则会留下指向「用户看不到的题」的脏收藏，而它在列表里表现为**空题干**；\n"
        "- 返回**两个**状态（`marked` / `favorited`）：它们各是一行数据，可以同时存在。"
    ),
)
async def favorite_question(
    db: DbSession, me: CurrentUserDep, question_id: int = Path(description="题目 id")
) -> dict:
    return await _flag(db, me.id, question_id, kind="favorite", on=True)


@router.delete(
    "/favorites/{question_id}",
    response_model=Envelope[FlagOut],
    summary="取消收藏（**幂等**）",
    description=(
        "清掉收藏。**没收藏过也返成功**（`DELETE` 影响 0 行 = 目标状态已达成）。\n\n"
        "★★ **取消不要求题目可见**（约定 T）：题目下架后你**仍然能撤掉它** ——\n"
        "否则那条记录就**卡死在你的列表里**（既看不见、又删不掉）。"
    ),
)
async def unfavorite_question(
    db: DbSession, me: CurrentUserDep, question_id: int = Path(description="题目 id")
) -> dict:
    return await _flag(db, me.id, question_id, kind="favorite", on=False)


@router.put(
    "/marks/{question_id}",
    response_model=Envelope[FlagOut],
    summary="标记一道题（**幂等**）",
    description=(
        "置上标记。语义与收藏**同一层**（用户 × 题目），但**是另一行数据** ——\n"
        "两者可以同时存在，所以 `favorites` 与 `question_marks` **不合表**\n"
        "（`favorites` 的唯一索引是 `(user_id, target_type, target_id)`，合表撑不住）。\n\n"
        "★ 用户级「标记」的唯一真相是 `question_marks`；\n"
        "`practice_items.marked` / `exam_attempt_items.marked` 是**卷面内**标记、**未接线**。"
    ),
)
async def mark_question(
    db: DbSession, me: CurrentUserDep, question_id: int = Path(description="题目 id")
) -> dict:
    return await _flag(db, me.id, question_id, kind="mark", on=True)


@router.delete(
    "/marks/{question_id}",
    response_model=Envelope[FlagOut],
    summary="取消标记（**幂等**）",
    description=(
        "清掉标记。**没标记过也返成功**（`DELETE` 影响 0 行 = 目标状态已达成）。\n\n"
        "★★ **取消不要求题目可见**（约定 T）：题目下架后你**仍然能撤掉它**。"
    ),
)
async def unmark_question(
    db: DbSession, me: CurrentUserDep, question_id: int = Path(description="题目 id")
) -> dict:
    return await _flag(db, me.id, question_id, kind="mark", on=False)


@router.get(
    "/favorites",
    response_model=Envelope[CollectionListOut],
    summary="收藏 / 标记 列表（`kind` 切题源）",
    description=(
        "两个列表**同形状**，只有题源不同：\n\n"
        "- `kind=favorite`（默认）= 我收藏的（`favorites`，`target_type='question'`）；\n"
        "- `kind=mark` = 我标记的（`question_marks`）。\n\n"
        "★ **为什么「标记」也要有列表**：标记可以打在**从没错过**的题上，"
        "那种题**不在错题本里** ⇒ 只做「错题本筛已标记」的话，用户会问"
        "「**我标的题去哪看**」。\n"
        "- 按 `collected_at` 倒序（最近放的在前）；\n"
        "- `subject_id` 可选；`subjects` 分面**恒为全量**（与错题本同一条判据）；\n"
        "- ★★ **约定 T**：题目下架后，我收藏/标记过的那道题**仍然列在这里**，\n"
        "  只是 `question_available=false`（前端标「题目已下架」）——\n"
        "  **平台可以下架题目，但不能让用户写下的东西消失**。"
    ),
)
async def list_collections(
    db: DbSession,
    me: CurrentUserDep,
    kind: Literal["favorite", "mark"] = Query("favorite", description="favorite=收藏 / mark=标记"),
    subject_id: int | None = Query(None, description="按科目筛选（不传 = 全部）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    data = await practice_service.list_collections(
        db,
        user_id=me.id,
        kind=kind,
        subject_id=subject_id,
        page=page,
        page_size=page_size,
    )
    return ok(data.model_dump())


# ==================================================== 笔记（P2c-5）


@router.get(
    "/questions/{question_id}/notes",
    response_model=Envelope[NoteListOfQuestionOut],
    summary="这道题下我的笔记（不分页）",
    description=(
        "★★ **不因题目下架而隐藏**（约定 T）：题目下架后这里**照样返回**，\n"
        "每条带 `question_available=false`。\n\n"
        "- **题目 id 不存在 ⇒ 空列表**（不报 404 —— 读路径不夹带比读更严的准入）；\n"
        "- **不分页**：面板里就那么几条，分页只会多一次往返；\n"
        "- 按 `created_at` 升序（先写的在前，像一条时间线）。"
    ),
)
async def list_question_notes(
    db: DbSession, me: CurrentUserDep, question_id: int = Path(description="题目 id")
) -> dict:
    notes = await practice_service.list_notes_of_question(
        db, user_id=me.id, question_id=question_id
    )
    return ok(NoteListOfQuestionOut(items=notes).model_dump())


@router.post(
    "/questions/{question_id}/notes",
    response_model=Envelope[NoteOut],
    summary="写一条笔记",
    description=(
        "在这道题下写一条笔记。★ **一题可以有多条** —— `notes` 表本来就没有唯一索引，\n"
        "而且 `position_sec`（视频时间戳笔记）天然是多条；「编辑」作用在**单条**上，\n"
        "所以不存在「我改的是不是我刚写的那条」这种问题。\n\n"
        "- 题目必须 **`published` 且未软删**，否则 `40401`（**不是 403**）；\n"
        "- `content` `strip()` 后非空、≤ 2000 字，否则 **422**（空笔记会渲染成一张空白卡片）；\n"
        "- `updated_at` 由触发器维护，**不接受传入**。"
    ),
)
async def create_note(
    body: NoteCreateIn,
    db: DbSession,
    me: CurrentUserDep,
    question_id: int = Path(description="题目 id"),
) -> dict:
    note = await practice_service.create_note(
        db, user_id=me.id, question_id=question_id, content=body.content
    )
    return ok(note.model_dump())


@router.put(
    "/notes/{note_id}",
    response_model=Envelope[NoteOut],
    summary="改一条笔记",
    description=(
        "改正文。**别人的 / 不存在的 / 已经删掉的 ⇒ 一律 `40401`**（同一句话、同一个响应）\n"
        "—— 区分开就等于告诉调用方「这条存在，只是不是你的」。\n\n"
        "★ **不要求题目可见**（约定 T）：这道题下架了，我写下的笔记我照样能改。"
    ),
)
async def update_note(
    body: NoteUpdateIn,
    db: DbSession,
    me: CurrentUserDep,
    note_id: int = Path(description="笔记 id"),
) -> dict:
    note = await practice_service.update_note(
        db, user_id=me.id, note_id=note_id, content=body.content
    )
    return ok(note.model_dump())


@router.delete(
    "/notes/{note_id}",
    response_model=Envelope[dict],
    summary="删一条笔记（**软删 · 幂等**）",
    description=(
        "软删（`notes.is_deleted = true`，**行还在**）。\n\n"
        "- **幂等**：已经删掉的再删**净零变更**、仍返成功（判据 =「目标状态已达成」）；\n"
        "- 别人的 / 不存在的 ⇒ `40401`（**不是 403**）；\n"
        "- ★ **不要求题目可见**（约定 T）。"
    ),
)
async def delete_note(
    db: DbSession, me: CurrentUserDep, note_id: int = Path(description="笔记 id")
) -> dict:
    await practice_service.delete_note(db, user_id=me.id, note_id=note_id)
    return ok({"note_id": str(note_id)})


@router.get(
    "/notes",
    response_model=Envelope[NoteListOut],
    summary="我的笔记列表（分页 + 科目筛选）",
    description=(
        "跨题的「我的笔记」。\n\n"
        "- 按 `created_at` 倒序（最近写的在前）；\n"
        "- `subject_id` 可选；`subjects` 分面**恒为全量**（与错题本 / 收藏同一条判据）；\n"
        "- ★★ **约定 T**：题目下架后那条笔记**仍然列在这里**（`question_available=false`）；\n"
        "- ★ 每条都带 `stem` —— 列表的用处是**认出那是哪道题**。"
    ),
)
async def list_notes(
    db: DbSession,
    me: CurrentUserDep,
    subject_id: int | None = Query(None, description="按科目筛选（不传 = 全部）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    data = await practice_service.list_notes(
        db, user_id=me.id, subject_id=subject_id, page=page, page_size=page_size
    )
    return ok(data.model_dump())
