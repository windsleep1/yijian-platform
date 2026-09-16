"""
pytest 共享装置：HTTP 客户端、统一响应体断言、测试账号工具。

Batch 3 起测试拆成两个文件（test_smoke.py 认证链路 / test_admin_v3.py 管理后台），
把 client 装置与几个小工具抽到这里，避免两份重复。

跑之前 API 必须是活的：

    AI_BASE=http://127.0.0.1:8123 pytest tests -v
    # 或一键： powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
"""

from __future__ import annotations

import os
import random
import string

import httpx
import pytest

BASE = os.environ.get("AI_BASE", "http://localhost:8000")
API = f"{BASE}/api/v1"

ADMIN_PHONE = os.environ.get("ADMIN_INIT_PHONE", "13800000000")
ADMIN_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD", "Admin@123456")

# 注册接口的密码策略：>=8 位且同时含字母与数字
TEST_PASSWORD = "Passw0rd123"

# JS Number.MAX_SAFE_INTEGER。雪花 ID 一定超过它 —— 这正是「ID 必须序列化成字符串」的理由。
JS_MAX_SAFE_INT = 2**53 - 1


def rand_phone() -> str:
    """生成不与现有用户冲突的测试手机号（13x 段）。"""
    return "13" + "".join(random.choice(string.digits) for _ in range(9))


def body(resp: httpx.Response) -> dict:
    """统一响应体：所有接口都必须带 code / trace_id / server_time。"""
    data = resp.json()
    assert "code" in data and "trace_id" in data and "server_time" in data, data
    return data


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def send_code(client: httpx.Client, phone: str, scene: str) -> str:
    """取验证码。依赖 SMS_PROVIDER=mock 返回 dev_code。"""
    r = client.post(f"{API}/auth/sms/send", json={"phone": phone, "scene": scene})
    b = body(r)
    assert b["code"] == 0, b
    code = b["data"].get("dev_code")
    assert code, "dev_code 为空：请确认 SMS_PROVIDER=mock 且 APP_ENV != prod"
    return code


def register(client: httpx.Client, *, nickname: str = "测试用户") -> dict:
    """注册一个新用户，返回完整 data（含 access_token / refresh_token / user）。"""
    phone = rand_phone()
    code = send_code(client, phone, "register")
    b = body(
        client.post(
            f"{API}/auth/register",
            json={"phone": phone, "code": code, "password": TEST_PASSWORD, "nickname": nickname},
        )
    )
    assert b["code"] == 0, b
    b["data"]["phone"] = phone  # 方便断言脱敏/明文
    return b["data"]


def admin_token(client: httpx.Client) -> str:
    """超管 Token。超管由 `python -m app.cli seed-admin` 创建；没跑就 skip。"""
    b = body(
        client.post(
            f"{API}/auth/login/password", json={"phone": ADMIN_PHONE, "password": ADMIN_PASSWORD}
        )
    )
    if b["code"] != 0:
        pytest.skip(f"超管登录失败（{b['code']} {b['message']}）：请先执行 python -m app.cli seed-admin")
    return b["data"]["access_token"]


def assign_roles(
    client: httpx.Client, headers: dict[str, str], user_id: str, codes: list[str], **kw
) -> dict:
    """PUT /admin/users/{id}/roles（整体替换角色）。"""
    return body(
        client.put(
            f"{API}/admin/users/{user_id}/roles",
            headers=headers,
            json={"role_codes": codes, **kw},
        )
    )


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    try:
        r = httpx.get(f"{API}/health", timeout=5)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"API 未就绪（{BASE}）：{exc}。"
            "请先 docker compose up -d --build，或运行 "
            "tools/local-verify/run-smoke.ps1（无 Docker 路径）"
        )
    with httpx.Client(timeout=15) as c:
        yield c


@pytest.fixture(scope="module")
def admin_h(client: httpx.Client) -> dict[str, str]:
    return auth(admin_token(client))
