"""
统一响应体、trace_id 中间件、分页模型。

响应格式（全站一致）：
    {
      "code": 0,                 # 0 = 成功，非 0 见 errors.py 的错误码分段
      "message": "ok",
      "data": {...} | null,
      "trace_id": "01HQ...",     # 与响应头 X-Request-ID 一致，便于排障
      "server_time": 1789000000000
    }

server_time 每个响应都带：考试倒计时以它为准，防止用户改本地时间作弊。
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from typing import Any, Generic, TypeVar

from fastapi import Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

T = TypeVar("T")

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str:
    return _trace_id.get()


def now_ms() -> int:
    return int(time.time() * 1000)


class Envelope(BaseModel, Generic[T]):
    """统一响应信封。路由用 response_model=Envelope[XxxOut] 声明，Swagger 即可看到完整结构。"""

    code: int = 0
    message: str = "ok"
    data: T | None = None
    trace_id: str = ""
    server_time: int = 0


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    has_more: bool


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应。返回值直接交给 FastAPI，由 response_model 做校验与裁剪。"""
    return {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
        "server_time": now_ms(),
    }


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
        "server_time": now_ms(),
    }


def paginate(items: list[Any], *, page: int, page_size: int, total: int) -> dict[str, Any]:
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
    }


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求分配 trace_id，写入 contextvar 与响应头。"""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Request-ID") or new_trace_id()
        token = _trace_id.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            _trace_id.reset(token)
        response.headers["X-Request-ID"] = trace_id
        return response
