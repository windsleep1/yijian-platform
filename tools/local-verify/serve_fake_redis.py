"""起一个真的 uvicorn 服务：真实 PostgreSQL + fakeredis 顶替 Redis。

只用于本地验证 —— Redis 被替换的仅是「命令行为」（incr/expire/set nx/ttl/del/get），
其余（HTTP 层、依赖注入、服务层、ORM、原生 SQL、事务）全部走真实代码。

用法：
    python serve_fake_redis.py [--pg-port 55432] [--api-port 8123] [--host 127.0.0.1]
                               [--log-file <path>]

为什么日志写文件而不是 stdout：
    run-smoke.ps1 用 ProcessStartInfo + UseShellExecute=True 启动本进程，让它「完全脱离」
    调用方的 stdio。否则这个长生命周期子进程会一直占着调用方的 stdout 管道句柄，
    调用方要等到本进程退出才读得到 EOF（表现为卡死）。既然脱离了 stdio，日志就自己落盘。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# 仓库根 = tools/local-verify/../../
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps" / "api"))


def main() -> None:
    ap = argparse.ArgumentParser(description="本地验证用 API 服务（真 PG + fakeredis）")
    ap.add_argument("--pg-port", default=os.environ.get("PG_PORT", "55432"))
    ap.add_argument("--api-port", type=int, default=int(os.environ.get("API_PORT", "8123")))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument(
        "--log-file",
        default=os.environ.get("UVICORN_LOG_FILE")
        or str(Path(tempfile.gettempdir()) / "yijian-api.log"),
    )
    ap.add_argument("--admin-phone", default=os.environ.get("ADMIN_INIT_PHONE", "13800000000"))
    ap.add_argument(
        "--admin-password", default=os.environ.get("ADMIN_INIT_PASSWORD", "Admin@123456")
    )
    args = ap.parse_args()

    # 这些必须在 import app.* 之前设置好。
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://yijian@127.0.0.1:{args.pg_port}/yijian"
    )
    os.environ["APP_ENV"] = os.environ.get("APP_ENV", "local")
    os.environ.setdefault("JWT_SECRET", "local-verify-secret-not-for-production")
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
    os.environ.setdefault("SMS_PROVIDER", "mock")
    os.environ["ADMIN_INIT_PHONE"] = args.admin_phone
    os.environ["ADMIN_INIT_PASSWORD"] = args.admin_password

    try:
        import fakeredis.aioredis
    except ImportError:
        sys.exit("缺少 fakeredis，请先执行： pip install fakeredis")

    import app.db.base as base

    # 关键：直接替换 Redis 单例。get_redis() 会返回它，无需改动应用代码。
    base._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    import uvicorn  # noqa: E402
    from app.main import app  # noqa: E402

    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": args.log_file,
                "mode": "w",
                "encoding": "utf-8",
                "formatter": "default",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "app": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["file"], "level": "INFO"},
    }

    uvicorn.run(app, host=args.host, port=args.api_port, log_config=log_config)


if __name__ == "__main__":
    main()
