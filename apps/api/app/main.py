"""
一建通 · 后端 API 入口。

分层约定（务必遵守，否则骨架会烂掉）：
    api/       只做参数校验、权限声明、调用 service、组装响应。不写业务逻辑，不直接查库。
    services/  纯业务逻辑，可脱离 HTTP 单测。覆盖率要求最高的层。
    db/models  只描述数据结构，不写业务方法。
    core/      配置、安全、异常、依赖、响应封装等横切能力。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.response import TraceIdMiddleware
from app.db.base import close_redis, engine

logging.basicConfig(
    level=logging.DEBUG if settings.app_debug else logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
)
logger = logging.getLogger("app")

DESCRIPTION = """
### 一建通 · 一级建造师学习备考平台 后端 API

**当前版本（Batch 7）交付范围**：Batch 2（认证 + RBAC + 分层骨架）
+ Batch 3（审计日志 / 角色列表 / 权限树 / 用户详情）
+ Batch 4（题库 CRUD / 章节树）
+ Batch 5（题库批量导入管道：上传 / 校验 / 执行 / 发布 / 回滚）
+ Batch 6（批次变更日志）
+ Batch 7（组卷规则 CRUD / 试卷 CRUD / 归档 + 恢复 / 手动加题移题 / 自动组卷 / 卷面校验 / 发布锁定
/ 规则试算 / 试卷下线），共 **50 个接口**。
+ 之后：**状态机补齐**（`unpublish` + 账号停用/启用）与 **数据范围收口**
（题目八条入口统一走 `scope_subject_ids`，见 `docs/14` / `docs/16`）。

**统一响应体**：`{ code, message, data, trace_id, server_time }`，`code=0` 表示成功。

**鉴权**：除 `/health`、`/auth/sms/send`、`/auth/register`、`/auth/login/*` 外，
所有接口需要请求头 `Authorization: Bearer <access_token>`。

**错误码分段**：40001 参数 / 40101 认证 / 40301 权限 / 40401 不存在 / 40901 冲突 / 42901 限流 / 50001 服务端。

**ID 类型**：主键是雪花 ID（BIGINT，18~19 位），**超出 JS 安全整数范围**，
因此所有 ID 字段在 JSON 中一律序列化为**字符串**（如 `"375228939615866880"`）。
前端请按 string 处理，不要 `Number()` 转换。
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("启动 %s v%s (env=%s)", settings.app_name, settings.app_version, settings.app_env)
    if settings.sms_provider == "mock" and settings.is_prod:
        logger.warning("生产环境正在使用 MOCK 短信通道，验证码不会真正下发！")
    yield
    logger.info("关闭中：释放数据库与 Redis 连接")
    await engine.dispose()
    await close_redis()


app = FastAPI(
    title=f"{settings.app_name} API",
    version=settings.app_version,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(TraceIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"]
    if not settings.is_prod
    else [
        o.strip() for o in __import__("os").environ.get("CORS_ORIGINS", "").split(",") if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

register_exception_handlers(app)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/docs")
