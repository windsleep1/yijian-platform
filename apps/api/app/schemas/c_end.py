"""C 端（学员端）接口的请求 / 响应模型。

为什么单独一个文件：这一批是**普通学员**用的接口（**没有权限码，靠会话**），
与管理端 `admin*.py` 的形状、可见性规则都不同（管理端接口会返答案，C 端不能）。
放在一起迟早有人把管理端字段抄过来 —— 而 `docs/22` §5.3 已经把
"C 端**一个都不许**复用 `/admin/*`"写成红线。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.types import BigIntStr

#: 考试年份的合理区间。**不收 1900 也不是洁癖**：`exam_year` 直接参与
#: "还有几天考试"的倒计时计算，离谱的值（0 / 负数 / 2999）会让前端算出
#: 几十年或负数的倒计时 —— 当场拒掉比事后排查便宜。
EXAM_YEAR_MIN = 2000
EXAM_YEAR_MAX = 2100


class SubjectOut(BaseModel):
    """科目（公共课 + 专业课）。引导页的"选专业"就是从 `category='professional'` 里挑。"""

    id: BigIntStr
    code: str
    name: str
    short_name: str | None = None
    exam_level: str
    category: str
    #: 专业课的所属专业码（`jz` / `sz` …）；公共课为 `None`。
    professional: str | None = None
    full_score: int
    pass_score: int
    duration_min: int
    color: str | None = None
    sort_no: int

    model_config = {"from_attributes": True}


class ProfileUpdateIn(BaseModel):
    """引导 / 资料更新。**全部字段可选** —— 引导页分两步提交，不该要求一次给全。

    ⚠️ 语义（刻意的）：**只写"这次真的传了"的字段**。
      - 没传的字段 → **保持原值**（不是清空）；
      - 显式传 `null` → 也**当成没传**（见 `profile_service` 的注释与用例）。
      为什么不做"传 null 就清空"：C 端表单直接 `JSON.stringify(state)` 时，
      未填的字段天然是 `null` —— 那种语义会把用户没动过的资料**误清空**。
    """

    exam_level: Literal["yijian", "erjian"] | None = None
    professional: str | None = Field(None, max_length=24)
    exam_year: int | None = Field(None, ge=EXAM_YEAR_MIN, le=EXAM_YEAR_MAX)
    #: ⚠️ 科目 ID 用**字符串**：雪花 ID 超出 JS 安全整数，用数字会被静默舍入
    #:   （`schemas/types.py` 抬头有实测）。这里当场校验，不指望前端记得。
    target_subjects: list[str] | None = None
    target_score: int | None = Field(None, ge=0, le=1000)
    daily_goal_min: int | None = Field(None, ge=5, le=600)

    @field_validator("target_subjects")
    @classmethod
    def _ids_are_digit_strings(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if len(v) > 20:
            raise ValueError("目标科目最多 20 个")
        for item in v:
            if not item.isdigit():
                raise ValueError(f"目标科目 ID 必须是数字字符串（收到 {item!r}）")
        return v
