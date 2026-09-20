"""
端到端冒烟测试：Batch 2 认证链路 + RBAC（需要 API 已启动）。

    cd deploy && docker compose up -d --build     # 有 Docker
    powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1   # 无 Docker

    cd apps/api && AI_BASE=http://localhost:8000 pytest tests -v

共享装置（client / body / rand_phone / send_code / admin_token）在 tests/conftest.py；
Batch 3 管理后台的验收用例在 tests/test_admin_v3.py。

覆盖：
    1. 健康检查
    2. 验证码 → 注册 → /me
    3. 密码登录 → 刷新（Rotation）→ 登出
    4. RBAC：普通学员访问管理端接口必须 403
    5. RBAC：超管访问管理端 → 改角色 → 该用户权限立即变化
    6. 权限缓存失效验证（改完角色立刻生效，不需要等缓存过期）
    7. 限流：验证码接口连发第二次必须 429
"""

from __future__ import annotations

import httpx

from .conftest import (
    API,
    TEST_PASSWORD,
    admin_token,
    auth,
    body,
    rand_phone,
    send_code,
)


# ---------------------------------------------------------------- 1. 健康


def test_health(client: httpx.Client) -> None:
    b = body(client.get(f"{API}/health"))
    assert b["code"] == 0
    assert b["data"]["dependencies"]["postgres"] == "up"
    assert b["data"]["dependencies"]["redis"] == "up"


# ---------------------------------------------------------------- 2. 注册


def test_register_and_me(client: httpx.Client) -> None:
    phone = rand_phone()
    code = send_code(client, phone, "register")

    r = client.post(
        f"{API}/auth/register",
        json={"phone": phone, "code": code, "password": TEST_PASSWORD, "nickname": "冒烟测试"},
    )
    b = body(r)
    assert b["code"] == 0, b
    data = b["data"]
    assert data["access_token"] and data["refresh_token"]
    assert "student" in data["user"]["roles"], data["user"]["roles"]
    assert data["user"]["profile"]["exam_level"] == "yijian"

    token = data["access_token"]
    b2 = body(client.get(f"{API}/auth/me", headers=auth(token)))
    assert b2["data"]["roles"] == data["user"]["roles"]
    assert "手机号:13800000000" not in str(b2["data"])  # 不应泄露明文手机号给他人

    # 重复注册必须冲突
    r3 = client.post(
        f"{API}/auth/register",
        json={"phone": phone, "code": code, "password": TEST_PASSWORD},
    )
    assert body(r3)["code"] in (40010, 40901), body(r3)


# ---------------------------------------------------------------- 3. 登录链路


def test_password_login_refresh_logout(client: httpx.Client) -> None:
    phone = rand_phone()
    code = send_code(client, phone, "register")
    client.post(
        f"{API}/auth/register",
        json={"phone": phone, "code": code, "password": TEST_PASSWORD},
    )

    r = client.post(f"{API}/auth/login/password", json={"phone": phone, "password": TEST_PASSWORD})
    b = body(r)
    assert b["code"] == 0, b
    old_refresh = b["data"]["refresh_token"]

    # 密码错误
    r_bad = client.post(f"{API}/auth/login/password", json={"phone": phone, "password": "wrong123"})
    assert body(r_bad)["code"] == 40103

    # 刷新 → 拿到新的一对令牌
    r2 = client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    b2 = body(r2)
    assert b2["code"] == 0, b2
    new_refresh = b2["data"]["refresh_token"]
    assert new_refresh != old_refresh

    # Rotation：旧 refresh token 不可再用
    r3 = client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert body(r3)["code"] == 40105, body(r3)

    # 登出后新 refresh token 也失效
    access = b2["data"]["access_token"]
    r4 = client.post(f"{API}/auth/logout", headers=auth(access))
    assert body(r4)["code"] == 0
    r5 = client.post(f"{API}/auth/refresh", json={"refresh_token": new_refresh})
    assert body(r5)["code"] == 40105, body(r5)


def test_unauthorized_access(client: httpx.Client) -> None:
    assert body(client.get(f"{API}/auth/me"))["code"] == 40100
    assert body(client.get(f"{API}/admin/users"))["code"] == 40100
    assert (
        body(client.get(f"{API}/auth/me", headers={"Authorization": "Bearer not-a-token"}))["code"]
        == 40102
    )


# ---------------------------------------------------------------- 4/5/6. RBAC


def test_rbac_flow(client: httpx.Client) -> None:
    admin_h = auth(admin_token(client))

    # 管理端列表：超管可访问
    r = client.get(f"{API}/admin/users", headers=admin_h, params={"page": 1, "page_size": 5})
    b = body(r)
    assert b["code"] == 0, b
    assert b["data"]["total"] >= 1
    assert "****" in b["data"]["items"][0]["phone"], "手机号应脱敏"

    # 4) 普通学员访问管理端 → 403
    phone = rand_phone()
    code = send_code(client, phone, "register")
    reg = body(
        client.post(
            f"{API}/auth/register",
            json={"phone": phone, "code": code, "password": TEST_PASSWORD},
        )
    )
    student_token = reg["data"]["access_token"]
    student_id = reg["data"]["user"]["id"]
    student_h = auth(student_token)

    denied = body(client.get(f"{API}/admin/users", headers=student_h))
    assert denied["code"] == 40301, denied

    denied2 = body(
        client.put(
            f"{API}/admin/users/{student_id}/roles",
            headers=student_h,
            json={"role_codes": ["admin"]},
        )
    )
    assert denied2["code"] == 40301, denied2

    # 5) 超管给学员授予 researcher
    granted = body(
        client.put(
            f"{API}/admin/users/{student_id}/roles",
            headers=admin_h,
            json={"role_codes": ["researcher"], "scope_type": "professional", "scope_id": 7},
        )
    )
    assert granted["code"] == 0, granted
    assert [x["code"] for x in granted["data"]["roles"]] == ["researcher"]
    assert "question:create" in granted["data"]["granted_permissions"]

    # 6) 缓存立即失效：同一 token 立刻就能看到新角色
    me = body(client.get(f"{API}/auth/me", headers=student_h))
    assert "researcher" in me["data"]["roles"]
    assert me["data"]["scopes"][0]["scope_type"] == "professional"

    # 不可分配的角色必须被拒
    bad = body(
        client.put(
            f"{API}/admin/users/{student_id}/roles",
            headers=admin_h,
            json={"role_codes": ["super_admin"]},
        )
    )
    assert bad["code"] == 40003, bad

    # 非 global scope 缺 scope_id 必须被拒
    bad2 = body(
        client.put(
            f"{API}/admin/users/{student_id}/roles",
            headers=admin_h,
            json={"role_codes": ["teacher"], "scope_type": "subject"},
        )
    )
    assert bad2["code"] == 40001, bad2


# ---------------------------------------------------------------- 7. 限流


def test_rate_limit_on_sms(client: httpx.Client) -> None:
    phone = rand_phone()
    r1 = body(client.post(f"{API}/auth/sms/send", json={"phone": phone, "scene": "login"}))
    assert r1["code"] == 0
    r2 = body(client.post(f"{API}/auth/sms/send", json={"phone": phone, "scene": "login"}))
    assert r2["code"] == 42902, r2
