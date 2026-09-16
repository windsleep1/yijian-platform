"""
Batch 3 管理后台接口验收（需要 API 已启动 + 已跑 `python -m app.cli seed-admin` 与 `seed-rbac`）。

本文件覆盖 Batch 3 新增的 4 个接口：

    GET /admin/users/{user_id}    用户详情（脱敏 / 明文 / 404 语义 / 最近审计）
    GET /admin/roles              角色列表（下拉 + 分配角色弹窗的数据源）
    GET /admin/permissions        权限树
    GET /admin/audit-logs         审计日志（筛选 + 分页 + diff 数据）

外加三条**跨接口契约**，它们才是 B 端最容易翻车的地方：

    A. 所有 ID 字段必须是 JSON **字符串** —— 雪花 ID 18~19 位，超出 JS 安全整数范围
       (2^53-1 = 9007199254740991)，`JSON.parse` 会静默抹平尾数，拿去拼 URL 必然 404。
    B. `viewer` 角色（只读审计岗）必须能看用户/审计，但**不能**改角色 ——
       这是「有 user:read、没有 user:manage → 按钮 disabled」唯一可复现的账号。
    C. 脱敏必须由**服务端**决定：`phone` 永远脱敏，`phone_full` 只给有 `user:export` 的人。
       前端把字符盖住不算数 —— 值仍躺在响应体里，F12 一览无余。
"""

from __future__ import annotations

import httpx

from .conftest import (
    API,
    JS_MAX_SAFE_INT,
    assign_roles,
    auth,
    body,
    register,
)

VIEWER_PERMS = {"user:read", "system:audit", "stats:read"}


# ================================================================ A. ID 字符串契约

def test_ids_are_json_strings(client: httpx.Client, admin_h: dict[str, str]) -> None:
    b = body(client.get(f"{API}/admin/users", headers=admin_h, params={"page": 1, "page_size": 50}))
    assert b["code"] == 0, b
    items = b["data"]["items"]
    assert items, "用户列表为空，无法验证 ID 序列化"

    for it in items:
        assert isinstance(it["id"], str), (
            f"id 必须是字符串，实际 {type(it['id']).__name__}: {it['id']}。"
            "Pydantic 层要用 app.schemas.types.BigIntStr"
        )

    # 关键：证明「必须字符串化」不是多虑 —— 样本里确实有超出 JS 安全范围的 ID。
    # 若这条断言挂了，说明 ID 生成器或样本变了，本测试的保护价值也就没了。
    over = [it["id"] for it in items if int(it["id"]) > JS_MAX_SAFE_INT]
    assert over, f"样本里没有超过 JS 安全整数（{JS_MAX_SAFE_INT}）的雪花 ID，断言失去意义"

    # /auth/me 与用户详情同样必须是字符串
    me = body(client.get(f"{API}/auth/me", headers=admin_h))
    assert isinstance(me["data"]["id"], str)
    detail = body(client.get(f"{API}/admin/users/{me['data']['id']}", headers=admin_h))
    assert isinstance(detail["data"]["id"], str)


# ================================================================ 用户详情

def test_user_detail_masking(client: httpx.Client, admin_h: dict[str, str]) -> None:
    stu = register(client, nickname="详情用例")
    uid = stu["user"]["id"]
    phone = stu["phone"]

    b = body(client.get(f"{API}/admin/users/{uid}", headers=admin_h))
    assert b["code"] == 0, b
    d = b["data"]

    assert d["id"] == uid
    # 脱敏永远在；明文只在有 user:export 时给（超管有全部权限）
    assert d["phone"] != phone and "****" in d["phone"], d["phone"]
    assert d["phone_full"] == phone, "超管拥有 user:export，应返回明文手机号"
    assert d["can_manage_roles"] is True
    assert d["roles"] == ["student"]
    assert d["register_source"] in ("h5", "api", "cli") or d["register_source"] is not None
    assert isinstance(d["recent_audits"], list)

    # 列表接口仍然只给脱敏值（不因为"详情给明文"就放宽列表）
    lst = body(client.get(f"{API}/admin/users", headers=admin_h, params={"keyword": phone}))
    assert lst["data"]["total"] >= 1
    assert "phone_full" not in lst["data"]["items"][0]
    assert "****" in lst["data"]["items"][0]["phone"]


def test_user_detail_404_is_real_404(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """不存在的 id 必须 404 + 40401，**不能**返回 200 + 空对象。

    返回 `{}` 是 B 端经典坑：前端画出一屏空白，用户以为系统坏了，
    也不会有任何报错进监控。
    """
    r = client.get(f"{API}/admin/users/999999999999999999", headers=admin_h)
    assert r.status_code == 404, r.status_code
    b = body(r)
    assert b["code"] == 40401, b
    assert b["data"] is None, b


# ================================================================ B. viewer 只读流程

def test_viewer_read_only_flow(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """超管把新账号设成 viewer，然后验证「能看不能改」。"""
    stu = register(client, nickname="只读审计员")
    uid = stu["user"]["id"]
    h = auth(stu["access_token"])

    # 变成 viewer 之前：连用户列表都进不去
    assert body(client.get(f"{API}/admin/users", headers=h))["code"] == 40301

    # 超管授予 viewer
    g = assign_roles(client, admin_h, uid, ["viewer"])
    assert g["code"] == 0, g
    assert [x["code"] for x in g["data"]["roles"]] == ["viewer"]
    assert set(g["data"]["granted_permissions"]) == VIEWER_PERMS, g["data"]["granted_permissions"]

    # 权限缓存已在分配时失效，同一个 token 立刻生效
    assert body(client.get(f"{API}/admin/users", headers=h))["code"] == 0
    assert body(client.get(f"{API}/admin/audit-logs", headers=h))["code"] == 0
    # 用户页的两个下拉数据源都走 user:read，必须也能拉到（否则页面残废）
    assert body(client.get(f"{API}/admin/roles", headers=h))["code"] == 0
    assert body(client.get(f"{API}/admin/permissions", headers=h))["code"] == 0

    # —— 但改角色必须被拦（这就是「按钮 disabled」对应的后端那道墙）——
    denied = body(
        client.put(f"{API}/admin/users/{uid}/roles", headers=h, json={"role_codes": ["admin"]})
    )
    assert denied["code"] == 40301, denied

    # 详情里 can_manage_roles=false、phone_full=null → 前端据此置灰按钮、隐藏明文
    d = body(client.get(f"{API}/admin/users/{uid}", headers=h))
    assert d["code"] == 0, d
    assert d["data"]["can_manage_roles"] is False
    assert d["data"]["phone_full"] is None, "viewer 没有 user:export，绝不能拿到明文"
    assert "****" in d["data"]["phone"]

    me = body(client.get(f"{API}/auth/me", headers=h))
    assert "viewer" in me["data"]["roles"]
    assert "user:manage" not in me["data"]["permissions"]
    assert "user:export" not in me["data"]["permissions"]


# ================================================================ /admin/roles

def test_roles_contract(client: httpx.Client, admin_h: dict[str, str]) -> None:
    b = body(client.get(f"{API}/admin/roles", headers=admin_h))
    assert b["code"] == 0, b
    roles = {r["code"]: r for r in b["data"]}

    for code, r in roles.items():
        assert isinstance(r["id"], str), f"{code}.id 必须是字符串"
        assert isinstance(r["is_assignable"], bool), code
        assert isinstance(r["permissions"], list), code

    assert "viewer" in roles, (
        "种子缺少 viewer 角色：请在 db/schema.sql §12.1 补上，再执行 "
        "`python -m app.cli seed-rbac`（run-smoke.ps1 已自动包含该步骤）"
    )
    assert set(roles["viewer"]["permissions"]) == VIEWER_PERMS
    assert roles["viewer"]["is_assignable"] is True

    # super_admin 不可分配 —— 前端应直接置灰，而不是等提交后吃 40003
    assert roles["super_admin"]["is_assignable"] is False
    assert len(roles["super_admin"]["permissions"]) >= 24, "超管应拥有全部权限"
    assert roles["admin"]["is_assignable"] is True

    # include_permissions=false 时不带权限明细（少一次 JOIN）
    b2 = body(
        client.get(f"{API}/admin/roles", headers=admin_h, params={"include_permissions": "false"})
    )
    assert b2["code"] == 0
    assert b2["data"] and all(r["permissions"] == [] for r in b2["data"]), b2["data"][:1]


# ================================================================ /admin/permissions

def test_permission_tree(client: httpx.Client, admin_h: dict[str, str]) -> None:
    b = body(client.get(f"{API}/admin/permissions", headers=admin_h))
    assert b["code"] == 0, b
    d = b["data"]
    assert d["total"] >= 24, f"权限条数异常：{d['total']}"

    tree = d["tree"]
    assert tree, "权限树为空"
    mods = {n["code"]: n for n in tree}
    assert {"question", "user", "system"} <= set(mods)
    assert mods["user"]["type"] == "module"

    user_codes = {c["code"] for c in mods["user"]["children"]}
    assert {"user:read", "user:manage", "user:export"} <= user_codes, user_codes
    for c in mods["user"]["children"]:
        assert isinstance(c["key"], str) and c["key"].startswith("p:")

    # 模块顺序由 MODULE_ORDER 决定，不靠 sort_no（sort_no 是模块内序号）
    order = [n["code"] for n in tree]
    assert order.index("question") < order.index("user") < order.index("system"), order

    # 单模块过滤
    b2 = body(client.get(f"{API}/admin/permissions", headers=admin_h, params={"module": "user"}))
    assert [n["code"] for n in b2["data"]["tree"]] == ["user"]
    assert b2["data"]["total"] == 3


# ================================================================ /admin/audit-logs

def test_audit_logs_filter_and_diff(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """自己先造一条可预期的审计记录，再验筛选 / 分页 / diff 数据。

    刻意不依赖别的用例产生的日志 —— 测试之间不该有隐式顺序依赖。
    """
    stu = register(client, nickname="审计用例")
    uid = stu["user"]["id"]
    assert assign_roles(client, admin_h, uid, ["researcher"])["code"] == 0

    # 列表
    b = body(client.get(f"{API}/admin/audit-logs", headers=admin_h, params={"page": 1, "page_size": 20}))
    assert b["code"] == 0, b
    assert b["data"]["total"] >= 1
    assert isinstance(b["data"]["items"][0]["id"], str), "审计 id 也必须是字符串"

    # 按 action 过滤
    b2 = body(
        client.get(
            f"{API}/admin/audit-logs", headers=admin_h, params={"action": "user.assign_roles"}
        )
    )
    assert b2["data"]["items"], "没有 user.assign_roles 记录"
    assert all(i["action"] == "user.assign_roles" for i in b2["data"]["items"])

    # 按对象过滤
    b3 = body(
        client.get(
            f"{API}/admin/audit-logs",
            headers=admin_h,
            params={"entity_type": "user", "entity_id": uid},
        )
    )
    assert b3["data"]["total"] >= 1, b3["data"]
    mine = b3["data"]["items"]
    assert all(i["entity_type"] == "user" and i["entity_id"] == uid for i in mine)

    top = mine[0]
    assert top["action"] == "user.assign_roles"
    assert isinstance(top["entity_id"], str), "entity_id 必须是字符串"
    assert top["actor_id"] is not None and isinstance(top["actor_id"], str)
    assert top["success"] is True

    # 抽屉 diff 视图的数据源：before/after 都必须有，且角色确实变了
    assert top["before_data"] is not None and top["after_data"] is not None
    assert [r["code"] for r in top["before_data"]["roles"]] == ["student"]
    assert [r["code"] for r in top["after_data"]["roles"]] == ["researcher"]

    # 排序方向
    asc = body(
        client.get(
            f"{API}/admin/audit-logs", headers=admin_h, params={"order": "asc", "page_size": 5}
        )
    )
    ts = [i["created_at"] for i in asc["data"]["items"]]
    assert ts == sorted(ts), ts

    # 分页字段完整
    page = asc["data"]
    assert {"items", "page", "page_size", "total", "has_more"} <= set(page)

    # start > end → 40001（半开区间语义，顺手验参数校验）
    bad = body(
        client.get(
            f"{API}/admin/audit-logs",
            headers=admin_h,
            params={"start": "2026-01-02T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
        )
    )
    assert bad["code"] == 40001, bad

    # 没有 system:audit → 403
    s = register(client, nickname="无审计权限")
    assert (
        body(client.get(f"{API}/admin/audit-logs", headers=auth(s["access_token"])))["code"] == 40301
    )


def test_user_detail_recent_audits(client: httpx.Client, admin_h: dict[str, str]) -> None:
    """详情里带回「与这个用户相关」的最近审计 —— 作为操作人 **或** 被操作对象。"""
    stu = register(client, nickname="审计关联")
    uid = stu["user"]["id"]
    assert assign_roles(client, admin_h, uid, ["teacher"])["code"] == 0

    d = body(client.get(f"{API}/admin/users/{uid}", headers=admin_h))["data"]
    assert d["roles"] == ["teacher"], d["roles"]
    assert len(d["recent_audits"]) <= 10, "只取最近 10 条"

    acts = [x["action"] for x in d["recent_audits"]]
    assert "user.assign_roles" in acts, acts
    related = [x for x in d["recent_audits"] if x["action"] == "user.assign_roles"]
    assert all(x["entity_type"] == "user" and x["entity_id"] == uid for x in related)
    assert all(isinstance(x["id"], str) for x in d["recent_audits"])
