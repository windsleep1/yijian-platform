"""学员资料 / 引导（C 端第一批的第 3 个接口）。

    PUT /users/me/profile

★ **"引导完成"的判据（刻意的设计选择）**：

    引导 = **选专业 + 选考试年份**（`docs/24` §1 的 P2a 范围）。
    所以：**当 `professional` 与 `exam_year` 都有值、且 `onboarded_at` 还是 NULL 时，
    才写 `onboarded_at`。**

    为什么不用一个显式的 `onboarded: true` 入参？——那样客户端就能"宣布自己完成了"，
    而服务器没有任何依据。用"两个必填项齐了"当判据，**服务端自己可判定**，
    而且它天然幂等：第二次改年份不会把 `onboarded_at` 刷新成新时间
    （硬约定 C：**幂等分支不动任何字段** —— 这里"不动"的就是 `onboarded_at`）。

★ **`null` 的语义**：见 `schemas/c_end.py::ProfileUpdateIn` 的抬头 ——
    未传 / 传 `null` 都 = **保持原值**。理由：C 端表单直接 `JSON.stringify(state)` 时
    未填字段天然是 `null`，若把 `null` 当"清空"，用户没动过的资料会被**误清空**。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserProfile
from app.schemas.auth import ProfileOut
from app.schemas.c_end import ProfileUpdateIn


async def update_profile(db: AsyncSession, *, user_id: int, payload: ProfileUpdateIn) -> ProfileOut:
    row = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    # 首次调用时资料行可能还不存在（注册只建 users 行）⇒ 用 upsert 的"创建"那一半。
    if row is None:
        row = UserProfile(user_id=user_id)
        db.add(row)

    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:  # 见抬头：null = 没传，不是"清空"
            continue
        setattr(row, field, value)

    if row.professional and row.exam_year and row.onboarded_at is None:
        row.onboarded_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(row)
    return ProfileOut.model_validate(row)


__all__ = ["update_profile"]
