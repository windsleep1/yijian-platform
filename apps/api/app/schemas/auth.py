"""认证相关请求/响应模型。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.types import BigIntStr, BigIntStrOpt

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
SCENE = Literal["register", "login", "reset"]


def _validate_phone(v: str) -> str:
    v = (v or "").strip()
    if not PHONE_RE.match(v):
        raise ValueError("手机号格式不正确")
    return v


class DeviceIn(BaseModel):
    device_id: str | None = Field(None, max_length=128)
    device_name: str | None = Field(None, max_length=128)
    platform: str | None = Field(None, max_length=24, description="h5 / pc / mini / app")
    # 与 user_sessions.app_version VARCHAR(24) 对齐；auth_service 三个入口都会读取
    app_version: str | None = Field(None, max_length=24, description="客户端版本号，如 1.0.0")


class SmsSendIn(BaseModel):
    phone: str = Field(..., examples=["13800000001"])
    scene: SCENE = Field("login", description="register 注册 / login 登录 / reset 重置密码")

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _validate_phone(v)


class SmsSendOut(BaseModel):
    expires_in: int = Field(..., description="验证码有效期（秒）")
    dev_code: str | None = Field(
        None, description="仅当 SMS_PROVIDER=mock 且非生产环境时返回，便于联调"
    )


class RegisterIn(DeviceIn):
    phone: str
    code: str = Field(..., min_length=4, max_length=8)
    password: str = Field(..., min_length=8, max_length=64)
    nickname: str | None = Field(None, max_length=32)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _validate_phone(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("密码需同时包含字母和数字")
        return v


class PasswordLoginIn(DeviceIn):
    phone: str
    password: str = Field(..., min_length=1, max_length=64)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _validate_phone(v)


class SmsLoginIn(DeviceIn):
    phone: str
    code: str = Field(..., min_length=4, max_length=8)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _validate_phone(v)


class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class ProfileOut(BaseModel):
    exam_level: str = "yijian"
    professional: str | None = None
    exam_year: int | None = None
    province: str | None = None
    target_subjects: list = Field(default_factory=list)
    target_score: int | None = None
    daily_goal_min: int = 30
    onboarded_at: datetime | None = None

    model_config = {"from_attributes": True}


class ScopeOut(BaseModel):
    role_code: str
    scope_type: str
    scope_id: BigIntStrOpt = None

    model_config = {"from_attributes": True}


class MeOut(BaseModel):
    id: BigIntStr
    phone: str | None = None
    nickname: str = ""
    avatar_url: str | None = None
    status: str = "active"
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    scopes: list[ScopeOut] = Field(default_factory=list)
    profile: ProfileOut | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TokenPairOut(BaseModel):
    token_type: str = "Bearer"
    access_token: str
    refresh_token: str
    expires_in: int = Field(..., description="access_token 剩余有效期（秒）")
    user: MeOut


class LogoutOut(BaseModel):
    revoked_sessions: int
