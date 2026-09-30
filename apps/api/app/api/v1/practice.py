"""C 端 · 刷题（P2b-1）：选章节 → 建 session → 取 session → 提交判分。

    POST /practice/sessions                 建一次章节练习
    GET  /practice/sessions/{id}            取练习（`doing` = 断点恢复；`finished` = 全可见）
    POST /practice/sessions/{id}/answer     提交一道题并判分
    POST /practice/sessions/{id}/finish     交卷（#7，P2c-1）
    GET  /practice/sessions/{id}/report     报告：总分 / 正确率 / 用时 / 知识点分布（P2c-1）

⚠️ **本批只做"数据流跑通"**（用户 2026-09-29 的原话）：先证明后端到前端的完整往返，
   再堆交互。**切题 / 答题卡 / 长按标记 / 交卷（#7）** 都留给 P2b-2。

⚠️ 这三个接口**都是"按 id 寻址"** ⇒ 每一个都要**自己再拦一次归属**
   （硬约定 G：列表过滤防"翻到"、防不了"猜到"）。做法统一：
   所有 SQL 都带 `user_id = :me`，查不到就是 404 —— **不是 403**，
   403 会变成一个"这个 id 存在但你没权限"的探测口。
"""

from __future__ import annotations

from fastapi import APIRouter, Path

from app.core.deps import CurrentUserDep, DbSession
from app.core.response import Envelope, ok
from app.schemas.c_end import (
    AnswerIn,
    AnswerResultOut,
    SessionCreateIn,
    SessionOut,
    SessionReportOut,
)
from app.services import practice_service

router = APIRouter(prefix="/practice", tags=["C 端 · 刷题"])


@router.post(
    "/sessions",
    response_model=Envelope[dict],
    summary="创建练习（P2b-1 只支持章节练习）",
    description=(
        "从科目 / 章节里抽题建一次练习。需要登录。\n\n"
        "- P2b-1 只支持 `mode='chapter'`（`chapter_id` 必填）；\n"
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
        subject_id=int(body.subject_id),
        chapter_id=(int(body.chapter_id) if body.chapter_id else None),
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
