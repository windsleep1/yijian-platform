"""认证会话的剩余契约：密码锁定 / 刷新过期 / 刷新时已注销 / 登出缺会话标识。

## 为什么单开一个文件

`auth_service` 最后 8 条缺口（逐条清单见 `docs/18`）分属三条流：

    · 密码登录锁定 —— `auth_service.py:263,264`（锁定期快速拒绝）
      + `274,275,276`（达阈值建锁、清失败计数）
    · 刷新令牌   —— `374`（会话在**库里**已过期）+ `378`（账号已注销）
    · 登出       —— `416`（没有会话标识）

与 ⑤a 同族（认证业务逻辑），所以一起做完 —— 之后 `auth_service` 清零。

## 写法：每条固定一个**契约**，不是"覆盖某一行"

    ① 连续失败达阈值 → 建锁；**锁定期间密码正确也拒绝**。
       ← 顺序本身是契约：锁检查在密码校验**之前**（`login_by_password:261-264`）。
         反过来等于"先花 ~250ms 做一次 bcrypt 再说不行"—— 既浪费，也是攻击面。
    ② 会话在库里过期（`user_sessions.expires_at`）→ 40101，**即使 JWT 自己没过期**。
       ← 契约是"**服务端能主动作废**"，不是"JWT 自带 exp"的重复；
         少了它，风控/管理员就没有任何"立刻踢下线"的手段。
    ③ 刷新时账号已注销（`users.is_deleted=true`）→ 40104。
    ④ 带**没有 `sid` 声明**的 access token 调登出 → 40001。
       ← 附**正向对照**（同一 token + `all_devices=true` 必须成功）：
         否则那个 40001 可能只是"token 压根不被接受"—— 那是假绿。

## 为什么登录请求各带一个独立假 IP

`/auth/sms/send`（IP 20 次/60s + 每日 20 次）、`/auth/register`（IP 10 次/60s）、
`/auth/login/password`（IP 20 次/60s）**都按 IP 限流**。共用 `127.0.0.1` 会互相挤额度，
而**每日**限额打满后表现为 `42902 请明天再试` —— 那是测试基建问题，排查时极易误判
（`conftest.fresh_user` 的注释里记着这个坑，注册一律走它）。

⚠️ 文件序：`test_auth_session.py` 排在 `test_rate_limit.py` **之前**（拼音序），
不会把上传额度提前打满。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from app.core.config import settings

from .conftest import API, TEST_PASSWORD, auth, body, fresh_user, sql_exec

LOGIN = f"{API}/auth/login/password"
REFRESH = f"{API}/auth/refresh"
LOGOUT = f"{API}/auth/logout"

# TEST-NET-1（文档专用段）：与 conftest 的 203.0.x.y / test_admin_v5 的 203.0.113.x /
# test_sms_login 的 198.51.100.x 都不重叠
_IP_POOL = iter(f"192.0.2.{i}" for i in range(11, 120))


def _ip() -> dict[str, str]:
    """登录接口按 IP 限流 —— 每条用例一个独立桶，不与别人挤。"""
    return {"X-Forwarded-For": next(_IP_POOL)}


def _login(client: httpx.Client, phone: str, password: str, h: dict[str, str]) -> dict:
    return body(client.post(LOGIN, json={"phone": phone, "password": password}, headers=h))


def test_lockout_after_max_failures_and_correct_password_still_denied(
    client: httpx.Client,
) -> None:
    """① 达阈值建锁，且**锁定期间密码正确也被拒**（覆盖 263/264/274/275/276）。"""
    data = fresh_user(client, nickname="锁定用例")
    phone, h = data["phone"], _ip()

    # 前 N-1 次：普通"手机号或密码不正确"
    for i in range(settings.login_max_failures - 1):
        b = _login(client, phone, "Wrong0rd123", h)
        assert b["code"] == 40103, f"第 {i + 1} 次失败应为 40103，实际 {b}"

    # 第 N 次：达阈值 → 建锁 + 清失败计数 → 42903
    b = _login(client, phone, "Wrong0rd123", h)
    assert b["code"] == 42903, b
    assert "锁定" in b["message"], b

    # ★ 契约：锁定期间**密码正确也拒绝**（锁检查排在密码校验之前），并给出剩余秒数
    b2 = _login(client, phone, TEST_PASSWORD, h)
    assert b2["code"] == 42903, f"锁定期间密码正确也必须拒绝，实际 {b2}"
    assert "秒后再试" in b2["message"], b2


def test_refresh_rejected_when_session_expired_in_db(client: httpx.Client) -> None:
    """② 会话在库里已过期 → 40101（覆盖 374）。JWT 本身仍在有效期内。"""
    data = fresh_user(client, nickname="刷新过期用例")
    refresh_token = data["refresh_token"]

    # 只改**库里的会话**；JWT 的 exp（30 天）没动 ——
    # 这样命中的是 `refresh_tokens:374`，而不是 decode 阶段的 exp 校验。
    changed = sql_exec(
        "UPDATE user_sessions SET expires_at = now() - interval '1 hour' WHERE refresh_token = $1",
        hashlib.sha256(refresh_token.encode("utf-8")).hexdigest(),
    )
    assert changed == "UPDATE 1", f"应当正好改到 1 行（会话确实入库），实际 {changed!r}"

    b = body(client.post(REFRESH, json={"refresh_token": refresh_token}))
    assert b["code"] == 40101, b
    assert "已过期" in b["message"], b


def test_refresh_rejected_when_account_soft_deleted(client: httpx.Client) -> None:
    """③ 刷新时账号已注销 → 40104（覆盖 378）。"""
    data = fresh_user(client, nickname="已注销用例")
    uid = int(data["user"]["id"])
    try:
        changed = sql_exec("UPDATE users SET is_deleted = true WHERE id = $1", uid)
        assert changed == "UPDATE 1", f"应当正好改到 1 行，实际 {changed!r}"

        b = body(client.post(REFRESH, json={"refresh_token": data["refresh_token"]}))
        assert b["code"] == 40104, b
        assert "已注销" in b["message"], b
    finally:
        # 本用例改过库 → `finally` 里**直接还原，不判断前面成没成**：
        # 中途断言失败也不能留下 is_deleted=true 的孤儿用户。
        sql_exec("UPDATE users SET is_deleted = false WHERE id = $1", uid)


def test_logout_without_session_id_returns_40001(client: httpx.Client) -> None:
    """④ access token 没有 `sid` 时，单设备登出必须 40001（覆盖 416）。"""
    data = fresh_user(client, nickname="无sid登出用例")
    token = _access_token_without_sid(data["user"]["id"])

    # 正向对照先行：同一个 token 在 `all_devices=true` 下**必须成功** ——
    # 否则下面的 40001 可能只是"token 压根不被接受"（假绿）。
    ctrl = body(client.post(f"{LOGOUT}?all_devices=true", headers=auth(token)))
    assert ctrl["code"] == 0, f"all_devices=true 不需要 sid，应当成功：{ctrl}"

    b = body(client.post(LOGOUT, headers=auth(token)))
    assert b["code"] == 40001, b
    assert "会话标识" in b["message"], b


def _access_token_without_sid(user_id: str) -> str:
    """手搓一个**没有 `sid` 声明**的 access token（其余字段与 `security._encode` 一致）。

    为什么需要它：`create_access_token` 的签名强制 `session_id: int`，所以**当前代码里
    不存在**"没有 sid 的 access token"。但 `deps.current_user:107` 写的是
    `int(payload["sid"]) if payload.get("sid") else None` —— **明确容忍**无 sid 的令牌，
    于是 `logout` 必须自己处理这种情况（否则 `UserSession.id == None` 会**静默撤不掉
    任何会话**：既不报错、也不生效）。这条用例固定的就是那个分支的契约。

    ⚠️ 手搓而不是调 `app.core.security._encode`：绑到私有函数上，重命名就会误红。
    """
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "typ": "access",
            "roles": [],
            "iss": settings.jwt_issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
