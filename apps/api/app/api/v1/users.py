"""C 端 · 当前用户（引导 / 资料）。

    PUT /users/me/profile

★ 与 `docs/24` §3.3 的关系：那里砍掉了 `GET /users/me/profile` ——
  因为 `GET /auth/me` **已经返 `profile`**（`schemas/auth.py` 的 `MeOut.profile`），
  再开一个读接口就是**同一份数据的第二个真相**。
  ⇒ 所以这里**只有 PUT**：写走这里，读走 `/auth/me`。

⚠️ 路径里带 `/me` 而不是 `/{user_id}`：C 端只能改自己的资料。
   带 id 的形式必须再做一次"这个 id 是不是你"的判断 —— 而那种判断
   **一旦漏掉就是越权改别人资料**（硬约定 G：按 id 寻址的入口都要自己再拦一次）。
   索性不给这个入口。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUserDep, DbSession
from app.core.response import Envelope, ok
from app.schemas.auth import ProfileOut
from app.schemas.c_end import ProfileUpdateIn
from app.services import profile_service

router = APIRouter(prefix="/users", tags=["C 端 · 用户"])


@router.put(
    "/me/profile",
    response_model=Envelope[ProfileOut],
    summary="更新我的资料（引导页用）",
    description=(
        "需要登录。**只写这次真的传了的字段**；没传或传 `null` 都保持原值。\n\n"
        "**引导完成**的判据：`professional` 与 `exam_year` 都有值时，服务端写 `onboarded_at`\n"
        "（只在它还是 `null` 时写 —— 之后改年份不会刷新这个时间戳）。\n\n"
        "响应体是**更新后的完整资料**，前端不必再打一次 `/auth/me`。"
    ),
)
async def put_my_profile(payload: ProfileUpdateIn, db: DbSession, me: CurrentUserDep) -> dict:
    profile = await profile_service.update_profile(db, user_id=me.user.id, payload=payload)
    return ok(profile.model_dump(), message="已保存")
