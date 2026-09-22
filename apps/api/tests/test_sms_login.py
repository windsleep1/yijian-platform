"""⑤a（HTTP 半）：短信验证码登录 `/auth/login/sms` —— **反例优先**。

## 为什么单开一个文件

`docs/18-覆盖率缺口诊断.md` 里 `login_by_sms` 是**整条链路零覆盖**
（服务端 9 条 + 路由 2 条：`auth_service.py:298-336`、`api/v1/auth.py:105,108`）。
它是**会自动建号的真实入口**（`docs/04` §2.1：「验证码登录（未注册则自动注册）」——
降低工地扫码的门槛），任何测试用户都能打到，却一直没有用例碰过它。

## 写法：构造反例，不是把正常路径再写一遍

4 条反例各断言一个**负面契约**：

    ① 错的码不通过 —— 且**码不被消耗**（随后用正确码仍能登录）
    ② 没发过码的验证码不通过（40010）—— 不能凭 6 位数字撞进去
    ③ 码**一次性消费**（登录成功后再用同一个码 → 40010）—— 否则码可重放
    ④ 码**与手机号绑定**（A 收到的码不能登录 B → 40010）

外加 1 条**对照**（正常登录 → 自动建号 → 令牌真能用）。
没有对照，上面那些"红"也可能只是"整条链路坏了"，判别力会丢。

## ⚠️ 一条可测性约束（实测确认，写在这里免得下一个人踩）

`/auth/sms/send` 对**同一手机号**有 **60 秒冷却**（`settings.sms_interval_seconds`，
实现是 `redis.set(cooldown_key, "1", ex=..., nx=True)`），所以
**"同一手机号发两次码"在单次测试运行里做不到**。于是这两类场景**不在本文件**：

    · 「已注册用户再次短信登录 → 直接登录（不是新建账号）」
    · 「账号被锁定 / 已注销 → 拒绝登录」

它们都需要**第二次发码**，改由进程内服务层用例覆盖（`tests/test_sms_inproc.py` ——
那里测试自己持有 fakeredis，不受冷却约束）。

## 为什么每个用例用不同的假 IP

`/auth/sms/send` 与 `/auth/login/sms` 都是**按 IP 限流**（20 次/60s）。
共用 `127.0.0.1` 的额度会和别的用例互相挤（v5 里踩过：注册得多了，把排在后面的用例
挤成 `42901`）。这里给每个用例一个独立的 TEST-NET-2 地址 —— 既走真实限流链路，
又不抢别人的额度。
"""

from __future__ import annotations

import httpx

from .conftest import API, auth, body, rand_phone

_IP_POOL = iter(f"198.51.100.{i}" for i in range(11, 250))
_WRONG_CODE = "000000"


def _h() -> dict[str, str]:
    return {"X-Forwarded-For": next(_IP_POOL)}


def _send(client: httpx.Client, phone: str, scene: str, h: dict[str, str]) -> str:
    b = body(client.post(f"{API}/auth/sms/send", json={"phone": phone, "scene": scene}, headers=h))
    assert b["code"] == 0, b
    code = b["data"].get("dev_code")
    assert code, "dev_code 为空：请确认 SMS_PROVIDER=mock 且 APP_ENV != prod"
    return code


def _login(client: httpx.Client, phone: str, code: str, h: dict[str, str]) -> dict:
    return body(
        client.post(f"{API}/auth/login/sms", json={"phone": phone, "code": code}, headers=h)
    )


# ---------------------------------------------------------------- 对照


def test_sms_login_happy_path_auto_registers(client: httpx.Client) -> None:
    """**对照**：未注册手机号短信登录 → 自动建号 + 签发令牌 + 令牌真的能用。

    它不是"正常路径再写一遍"：它是上面 4 条反例的**判别基线** ——
    没有它，"40011 / 40010" 也可能来自"整条链路本来就不通"。
    """
    h = _h()
    phone = rand_phone()
    code = _send(client, phone, "login", h)

    b = _login(client, phone, code, h)
    assert b["code"] == 0, b
    data = b["data"]
    assert data["access_token"] and data["refresh_token"]

    # 自动建号：这个手机号此前**没有任何注册动作**，但令牌已经能拿到本人
    me = body(client.get(f"{API}/auth/me", headers=auth(data["access_token"])))
    assert me["code"] == 0, me
    assert me["data"]["id"] == data["user"]["id"]
    assert me["data"]["id"].isdigit(), "雪花 ID 必须序列化成字符串（超 JS 安全整数）"

    # ⚠️ 一条**容易被误读的契约**（写清楚，免得下一个人照着错的理解改）：
    #    `/auth/me` 对**本人**返回**明文**手机号 —— 脱敏只发生在**给别人看的列表**
    #    （管理端用户列表）。所以这里断言 `== phone`，而不是"不含明文"。
    #    （`test_smoke` 里那条"不应泄露明文手机号"说的是**别人**的号码不该出现，
    #      我第一版就是把它读成"永不返回明文"，被这条用例当场纠正。）
    #    为什么值得固定成契约：以后要不要对本人也脱敏是**产品决定**，
    #    固定住它，改的人就必须显式面对这条断言，而不是悄悄改掉。
    assert me["data"]["phone"] == phone


# ---------------------------------------------------------------- 4 条反例


def test_wrong_code_is_rejected_and_not_consumed(client: httpx.Client) -> None:
    """反例①：错的码 → `40011`，**且码不被消耗**。

    后半句才是真正的契约：如果校验失败也把码删掉，用户手滑输错一次就得
    重新等满 60 秒冷却。这里刻意**不重新发码**（也发不了），用同一个码再登一次。
    """
    h = _h()
    phone = rand_phone()
    code = _send(client, phone, "login", h)

    bad = _login(client, phone, _WRONG_CODE, h)
    assert bad["code"] == 40011, bad

    good = _login(client, phone, code, h)
    assert good["code"] == 0, good


def test_code_that_was_never_sent_is_rejected(client: httpx.Client) -> None:
    """反例②：从没发过码的手机号 → `40010`（验证码已过期）。"""
    h = _h()
    b = _login(client, rand_phone(), _WRONG_CODE, h)
    assert b["code"] == 40010, b


def test_code_is_single_use(client: httpx.Client) -> None:
    """反例③：码**一次性消费** —— 登录成功后再用同一个码 → `40010`。

    没有这条，"同一个码可以无限换令牌"（可重放）就没人拦。
    """
    h = _h()
    phone = rand_phone()
    code = _send(client, phone, "login", h)

    first = _login(client, phone, code, h)
    assert first["code"] == 0, first

    replay = _login(client, phone, code, h)
    assert replay["code"] == 40010, replay


def test_code_is_bound_to_phone(client: httpx.Client) -> None:
    """反例④：A 收到的码**不能**用来登录 B（码按 `scene+phone` 存）。"""
    h = _h()
    phone_a, phone_b = rand_phone(), rand_phone()
    code_a = _send(client, phone_a, "login", h)

    b = _login(client, phone_b, code_a, h)
    assert b["code"] == 40010, b
