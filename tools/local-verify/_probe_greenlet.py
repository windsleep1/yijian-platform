# -*- coding: utf-8 -*-
"""判别性实验：覆盖率丢事件是不是 greenlet 栈切换造成的？

用法（分两个进程跑，避免 asyncio loop / 连接池串味）：
    python _probe_greenlet.py thread
    python _probe_greenlet.py greenlet

已知事实：
    · 裸 asyncpg 跑同样的查询 —— **不丢**             （之前测过）
    · 走 SQLAlchemy async（= asyncpg + greenlet 桥）—— **丢**
    · 三种追踪内核（默认 / pytrace / ctrace）结果**完全一致**

推理：三种内核都丢 → 不是"哪个内核"的问题，是"追踪机制与运行机制的交互"。
      SQLAlchemy async 的每个 DB 调用都经由 `greenlet_spawn()` 切一次 greenlet，
      而 coverage 的默认 `concurrency = thread` **不感知 greenlet 切换** ——
      切回来之后，追踪器手里那个"当前帧"已经指错了，于是**该帧后续的 line 事件全部丢失**。

      这正好解释了症状的形状：
        · await 那一行**有**记录（切换前）
        · 它之后同一个函数里的行**全没有**（切回来后追踪失效）
        · 别的帧（模块级、别的函数）**正常**（它们各自是新帧）

本实验：**同进程**（不经过 uvicorn，排除运行方式）跑同样的流程，
        只改一件事：`concurrency` 是默认的 thread 还是 greenlet。

判据用三处"必然执行"的行：
    app/api/v1/auth.py:76           注册路由的 return（用例断言了它）
    app/core/deps.py:117            current_user 里的取权限（每个鉴权请求必跑）
    app/services/auth_service.py    register 函数体（注册必跑）
"""
import asyncio
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "thread"
# 每次跑用新号码：注册是**写库**的，重复号码会 409（上一轮崩溃正是踩了这个）
PHONE = "139" + str(int(__import__("time").time() * 1000))[-8:]

REPO = r"C:/My Protect/WorkBuddy/ONE Build/yijian-platform"
sys.path.insert(0, os.path.join(REPO, "apps", "api"))
os.chdir(os.path.join(REPO, "apps", "api"))

os.environ["DATABASE_URL"] = "postgresql+asyncpg://yijian@127.0.0.1:55432/yijian"
os.environ["APP_ENV"] = "local"
os.environ["JWT_SECRET"] = "local-verify-secret-not-for-production"
os.environ["SMS_PROVIDER"] = "mock"

import coverage  # noqa: E402

CFG = os.path.join(REPO, ".coveragerc")
DATA = os.path.join(REPO, f".coverage.r-{MODE}")
CONC = None if MODE == "thread" else ["greenlet"]

if os.path.exists(DATA):
    os.remove(DATA)

kw = {"source": ["app"], "data_file": DATA, "config_file": CFG}
if CONC is not None:
    kw["concurrency"] = CONC
cov = coverage.Coverage(**kw)
cov.start()

import fakeredis.aioredis  # noqa: E402

import app.db.base as base  # noqa: E402

base._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

import httpx  # noqa: E402

from app.main import app  # noqa: E402


async def flow() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/v1/auth/sms/send",
                         json={"phone": PHONE, "scene": "register"},
                         headers={"X-Forwarded-For": "203.0.113.9"})
        code = r.json()["data"]["dev_code"]
        r = await c.post("/api/v1/auth/register",
                         json={"phone": PHONE, "code": code,
                               "password": "Passw0rd123", "nickname": f"probe-{MODE}"},
                         headers={"X-Forwarded-For": "203.0.113.9"})
        body = r.json()
        print(f"  [{MODE}] 注册 {r.status_code} code={body.get('code')}")
        if body.get("code") == 0:
            tok = body["data"]["access_token"]
        else:
            # 号码被占用则回退登录（同样会过 current_user / auth_service）
            r = await c.post("/api/v1/auth/login/password",
                             json={"phone": PHONE, "password": "Passw0rd123"},
                             headers={"X-Forwarded-For": "203.0.113.9"})
            body = r.json()
            print(f"  [{MODE}] 回退登录 {r.status_code} code={body.get('code')}")
            tok = body["data"]["access_token"]
        r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
        print(f"  [{MODE}] /auth/me {r.status_code} code={r.json().get('code')}")


asyncio.run(flow())

cov.stop()
cov.save()

data = cov.get_data()


def lines(rel: str):
    for f in data.measured_files():
        if f.replace("\\", "/").endswith(rel):
            return set(data.lines(f) or [])
    return set()


deps = sorted(x for x in lines("app/core/deps.py") if 85 <= x <= 130)
auth = lines("app/api/v1/auth.py")
svc = lines("app/services/auth_service.py")
auth_svc = lines("app/api/v1/auth.py")

print()
print(f"  == MODE={MODE} (concurrency={CONC or '默认 thread'})  号码={PHONE} ==")
print(f"     auth.py:76  (注册路由 return)  = {76 in auth}")
print(f"     deps.py:117 (取权限)           = {117 in deps}")
print(f"     auth_service.py 覆盖行数       = {len(svc)}")
print(f"     deps.py 85-130 覆盖行          = {deps}")
print(f"     auth.py 覆盖行数               = {len(auth_svc)}")
print(f"RESULT mode={MODE} auth76={76 in auth} deps117={117 in deps} "
      f"svc={len(svc)} authlines={len(auth_svc)}")
