"""起一个真的 uvicorn 服务：真实 PostgreSQL + fakeredis 顶替 Redis。

只用于本地验证 —— Redis 被替换的仅是「命令行为」（incr/expire/set nx/ttl/del/get），
其余（HTTP 层、依赖注入、服务层、ORM、原生 SQL、事务）全部走真实代码。

用法：
    python serve_fake_redis.py [--pg-port 55432] [--api-port 8123] [--host 127.0.0.1]
                               [--log-file <path>]
                               [--coverage] [--cov-data-file <path>] [--shutdown-file <path>]

为什么日志写文件而不是 stdout：
    run-smoke.ps1 用 ProcessStartInfo + UseShellExecute=True 启动本进程，让它「完全脱离」
    调用方的 stdio。否则这个长生命周期子进程会一直占着调用方的 stdout 管道句柄，
    调用方要等到本进程退出才读得到 EOF（表现为卡死）。既然脱离了 stdio，日志就自己落盘。

## 覆盖率为什么要在这里采集（而不是在 pytest 进程里）

用例是**通过 HTTP** 打到这个进程的（`conftest.py` 里就是个普通 `httpx.Client`，
base_url 来自 `AI_BASE`）。所以业务代码**全部执行在这个进程里** ——
在 pytest 进程里跑 `--cov=app` 只能量到测试自己导入的那几个纯函数模块，
数字会低得离谱，拿它当门槛基线就是"用错的尺子量"。

## 为什么需要 `--shutdown-file`（哨兵文件）

`run-smoke.ps1` 收尾用的是 `Stop-Process -Force`，那是**硬杀**：
进程直接消失，`atexit` 不跑，coverage 的数据**一个字都写不出来**。
所以改成"外部放一个哨兵文件 → 本进程自己收尾"：main 线程里 `server.run()` 返回后
正常走到 `cov.stop()/cov.save()`。**不用信号**：Windows 上给别的进程发不了 SIGTERM，
而 `signal` 处理器只在 main 线程生效，加进去反而多一层平台差异。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
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
    # ---- 覆盖率（门禁整顿第二步引入）----
    ap.add_argument(
        "--coverage",
        action="store_true",
        help="在本进程里采集 app.* 的覆盖率（数据写 --cov-data-file）",
    )
    ap.add_argument(
        "--cov-data-file",
        default=str(REPO / ".coverage"),
        help="覆盖率数据文件（默认 <repo>/.coverage，gitignored）",
    )
    ap.add_argument(
        "--shutdown-file",
        default=None,
        help="哨兵文件：该文件出现时本进程优雅退出（用于让 coverage 数据落盘）",
    )
    args = ap.parse_args()

    # 覆盖率**必须在 import app.* 之前起**，否则模块级代码不会被记到。
    # cwd 切到 `apps/api` 是为了让 `source=["app"]` 能被解析成那个包
    # （coverage 的相对 source 是按**进程 cwd** 找的）。
    cov = None
    if args.coverage:
        import coverage

        os.chdir(REPO / "apps" / "api")
        cov = coverage.Coverage(
            source=["app"],
            data_file=args.cov_data_file,
            config_file=str(REPO / ".coveragerc"),
        )
        cov.start()

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

    # 用 Server 对象而不是 `uvicorn.run()`：需要拿到 `should_exit` 才能被哨兵文件关掉。
    server = uvicorn.Server(
        uvicorn.Config(app, host=args.host, port=args.api_port, log_config=log_config)
    )

    if args.shutdown_file:
        sentinel = Path(args.shutdown_file)
        # 起来之前先清掉可能残留的旧哨兵，否则会刚起就退。
        sentinel.unlink(missing_ok=True)

        def _watch_sentinel() -> None:
            while not sentinel.exists():
                time.sleep(0.4)
            server.should_exit = True

        threading.Thread(target=_watch_sentinel, daemon=True).start()

    started = time.monotonic()
    try:
        server.run()
    finally:
        if cov is not None:
            cov.stop()
            cov.save()
            # 这条是给"日志即证据"用的：不记一笔的话，外部只能靠数据文件是否存在来猜
            # 到底是"正常收尾写盘了"还是"被硬杀了"。走 logger 而不是 print ——
            # 本进程的 stdout 是脱离的（UseShellExecute=True），print 没人看得到。
            import logging

            logging.getLogger("app").info(
                "[cov] data saved to %s (uptime %.0fs)",
                args.cov_data_file,
                time.monotonic() - started,
            )


if __name__ == "__main__":
    main()
