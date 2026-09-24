"""
业务异常与全局异常处理。

错误码分段（与 docs/04-API接口清单.md 一致）：
    0        成功
    40001+   参数错误
    40101+   认证失败
    40301+   权限不足
    40401+   资源不存在
    40901+   业务冲突
    42901+   限流
    50001+   服务端错误（50003 = 依赖服务不可用 / 50004 = 查询超时，两者都是 HTTP 503）
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import fail

logger = logging.getLogger("app.error")


class BizError(Exception):
    """业务异常。抛出后由全局处理器转成统一响应体。"""

    def __init__(self, code: int, message: str, http_status: int = 400) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------- 常用错误构造快捷方式 ----------------


def bad_request(message: str, code: int = 40001) -> BizError:
    return BizError(code, message, 400)


def unauthorized(message: str = "登录已过期，请重新登录", code: int = 40101) -> BizError:
    return BizError(code, message, 401)


def forbidden(message: str = "没有操作权限", code: int = 40301) -> BizError:
    return BizError(code, message, 403)


def not_found(message: str = "资源不存在", code: int = 40401) -> BizError:
    return BizError(code, message, 404)


def conflict(message: str, code: int = 40901) -> BizError:
    return BizError(code, message, 409)


def too_many(message: str = "请求过于频繁，请稍后再试", code: int = 42901) -> BizError:
    return BizError(code, message, 429)


def unavailable(message: str = "依赖服务暂时不可用，请稍后重试", code: int = 50003) -> BizError:
    """依赖（Redis/数据库/三方）不可用。返回 503，便于编排/网关做重试与熔断。"""
    return BizError(code, message, 503)


def query_timeout(
    message: str = "统计查询超时，请缩小时间范围或增加筛选条件", code: int = 50004
) -> BizError:
    """聚合查询超过 `statement_timeout`。

    ★ 与 `bad_request`（参数错）区分开：**参数没问题，是这次的数据量太大** ——
    用户缩小范围后**重试就能成功**。所以是 503（可重试），不是 400。

    ★ 它存在的意义是**把无界等待变成有界失败**（硬约定 N）：没有它，一个大时间范围的
    聚合查询会一直占着连接，直到把连接池吸干 —— 而且**不会报错**，只会"越来越慢"。
    """
    return BizError(code, message, 503)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=fail(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 把 Pydantic 的报错压成一句人话，前端好展示
        details = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
            details.append(f"{loc}: {err.get('msg', '')}".strip(": "))
        message = "参数校验失败：" + "；".join(details[:5]) if details else "参数校验失败"
        return JSONResponse(
            status_code=422,
            content=fail(40001, message, data={"errors": details}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code_map = {401: 40101, 403: 40301, 404: 40401, 405: 40001, 429: 42901}
        code = code_map.get(exc.status_code, 50001 if exc.status_code >= 500 else 40001)
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(status_code=exc.status_code, content=fail(code, message))

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常 path=%s", request.url.path)
        return JSONResponse(status_code=500, content=fail(50001, "服务内部错误，请稍后重试"))
