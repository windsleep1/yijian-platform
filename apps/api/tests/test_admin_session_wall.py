"""`docs/22` §6.5 管理端会话墙 —— **专用用例**（P0 的一部分）。

这是 §6.5 落地时必须一起交的测试（§6.5.5 列的"新增用例"）。四条里三条在这里：

| # | 用例 | 钉住什么 |
|---|---|---|
| 1 | `test_c_end_session_with_admin_permission_is_still_blocked` | ★ **威胁用例** —— §6.5 要堵的那个洞 |
| 2 | `test_logout_takes_effect_immediately_on_c_end` | 会话墙的**白拿收益**（BL-13）：登出即时生效 |
| 3 | `test_admin_platform_session_is_granted_to_whitelisted_role` | 白名单的**正向**（墙不是"一律拒绝"） |

（第 4 条"变异：打掉会话墙 → 第 1 条必须变红"不在这里 —— 它要**打掉产品代码再跑**，
形态属于 `tools/local-verify/mutate-*.py` 那一族，记在 `docs/24` §9 的后续项。）

会话墙的两层分工（`docs/24` §9.3）：

    `40306` 会话墙 —— "**你不是管理端会话**"（`user_sessions.platform != 'pc'`）
    `40301` 权限墙 —— "**你是管理端会话，但缺这条权限**"
"""

from __future__ import annotations

import httpx

from .conftest import API, LOGIN_PATH, TEST_PASSWORD, assign_roles, auth, body, fresh_user, relogin


def test_c_end_session_with_admin_permission_is_still_blocked(
    client: httpx.Client, admin_h: dict[str, str]
) -> None:
    """★★ **威胁用例**：**C 端会话 + 拿到了某个 admin 权限码** ⇒ 仍然读不了后台。

    这就是 §6.5 的原始判据（保留原话）：
    *"把一个纯 C 端账号授上 `question:read`，它还能不能读后台？"* ——
    **在这道墙之前，答案是"能"**（因为老实现只看权限码，不看会话从哪来）。

    判据分两步，**缺一不可**（否则这个测试会变成"因为没权限所以被拒"，测了个寂寞）：
      ① **先证明权限真的有了** —— 授权响应 + `/auth/me` 都显示 `user:read`；
      ② **再证明仍然被拒** —— 同一个 `h5` token 访问 `/admin/users` → **`40306`**。

    ⚠️ ① 是**可证伪的锚**：如果哪天有人把会话墙删了，② 会变成 `0` ⇒ 本用例变红。
    没有 ① 的话，"变红/变绿"都可能只是权限配置变了，**说不清是不是墙在起作用**。
    """
    u = fresh_user(client, nickname="C端会话+后台权限")

    # 授一个**确实带 `user:read`** 的角色（viewer：user:read + system:audit + stats:read + exam:read）
    g = assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    assert g["code"] == 0, g
    assert "user:read" in g["data"]["granted_permissions"], g["data"]

    # ① 权限确实生效（**同一个 h5 token** 就能看到 —— 权限缓存已在分配时失效）
    h5 = auth(u["access_token"])
    me = body(client.get(f"{API}/auth/me", headers=h5))
    assert me["code"] == 0, me
    assert "user:read" in me["data"]["permissions"], me["data"]
    assert "viewer" in me["data"]["roles"], me["data"]

    # ② 但 `/admin/*` 仍然进不去 —— **不是权限不足，是根本没有管理端会话**
    denied = body(client.get(f"{API}/admin/users", headers=h5))
    assert denied["code"] == 40306, denied
    assert denied["code"] != 40301, "这里必须是**会话墙**的码，不是权限墙的码"

    # ③ 对照组：同一个账号**重新登录并申报 pc** ⇒ 同一个接口立刻能读
    #    （证明这道墙是"按会话”，不是"按人” —— 否则 ② 可能只是这个账号被判了黑名单）
    pc = relogin(client, u)
    assert body(client.get(f"{API}/admin/users", headers=pc))["code"] == 0


def test_logout_takes_effect_immediately_on_c_end(client: httpx.Client) -> None:
    """★ 会话墙的**白拿收益**（BL-13）：**登出即时生效**，C 端也一样。

    ⚠️ 在这道墙之前 `current_user` **从不读 `user_sessions` 行** ——
    它只解 token、查用户、查权限 ⇒ **登出之后 access token 仍然有效到自然过期**
    （最长一整个 access TTL）。这不是"设计如此"，是**漏了**。

    `docs/22` §6.5.4 的裁定（用户原话）：*"它和 §6.5 同族（『会话』和『token』要分别管理）。
    如果首批不做，将来补的成本是『改 C 端登录接口 + 迁移已有会话』。"*

    判据三步，**用同一个 token** 走完（不许换 token，否则测的是"新 token 能不能用"）：
      ① 登出前：`/auth/me` 通；
      ② 登出：`revoked_sessions == 1`（真的撤了，不是"接口回 0 但什么都没做"）；
      ③ 登出后：**同一个 token** → `401 / 40105`（"登录已失效，请重新登录"）。
    """
    u = fresh_user(client, nickname="登出即时生效")
    h = auth(u["access_token"])

    # ① 登出前：受保护接口能通（`/auth/me` 不是 `/admin/*`，所以 h5 会话本来就该通）
    assert body(client.get(f"{API}/auth/me", headers=h))["code"] == 0

    # ② 登出（撤销**当前会话**；`all_devices` 是 query 参数，默认 False）
    out = body(client.post(f"{API}/auth/logout", headers=h))
    assert out["code"] == 0, out
    assert out["data"]["revoked_sessions"] == 1, out["data"]

    # ③ 同一个 token 立刻失效 —— 这就是"会话墙"带来的东西
    r = client.get(f"{API}/auth/me", headers=h)
    assert r.status_code == 401, (
        f"登出后同一个 token 应立刻 401，实际 {r.status_code}: {r.text[:160]}"
    )
    b = body(r)
    assert b["code"] == 40105, b
    assert "重新登录" in b["message"], b


def test_admin_platform_session_is_granted_to_whitelisted_role(
    client: httpx.Client, admin_h: dict[str, str]
) -> None:
    """白名单的**正向**：`viewer`（管理端只读岗）**能**拿到管理端会话。

    为什么必须有这条（否则整组用例会失衡）：其余用例都在证明"**拒绝**"，
    而"一律拒绝"也能让它们全绿 —— 那是个**假绿**（墙把正常用户也挡了）。
    这条钉住"该放行的确实放行了"，两条合起来才是"**该拦的拦住、该过的通过**"。

    ⚠️ `viewer` 曾经**不在**白名单里（§6.5.2 的初版名单漏了它）——
    后果不是"权限少一点"，而是它**一个接口都读不了**（拿不到 pc 会话、h5 又被挡）⇒
    这个角色**整个作废**。所以这条用例同时是**那份名单的可证伪判据**。
    """
    u = fresh_user(client, nickname="白名单正向")
    assert assign_roles(client, admin_h, u["user"]["id"], ["viewer"])["code"] == 0

    # 用 `platform="pc"` 重新登录 ⇒ 成功（viewer 在白名单里）
    pc = relogin(client, u)

    # 会话确实是 pc 通道：能读 viewer 该读的东西
    assert body(client.get(f"{API}/admin/users", headers=pc))["code"] == 0
    assert body(client.get(f"{API}/admin/audit-logs", headers=pc))["code"] == 0

    # 而它**没有** user:manage ⇒ 写动作仍是 40301（**权限墙**，不是会话墙）
    denied = body(
        client.put(
            f"{API}/admin/users/{u['user']['id']}/roles",
            headers=pc,
            json={"role_codes": ["admin"]},
        )
    )
    assert denied["code"] == 40301, denied
    assert "user:manage" in denied["message"], denied


def test_student_pc_login_is_rejected_not_silently_downgraded(client: httpx.Client) -> None:
    """非白名单角色申报 `platform="pc"` ⇒ **拒绝登录**（`40306`），**不静默降级为 h5**。

    ★ 为什么不降级（§6.5.2 修正②）：降级之后用户看到的是"我登进来了，但什么都点不了"
    （每个 `/admin/*` 都回 `40306`）—— 那是个**查不出原因**的症状。
    宁可当场拒绝，并说清"该账号不是管理端账号"，让人知道**该从哪个入口登录**。

    ⚠️ 它与 `test_admin_stats_api.test_student_cannot_hold_admin_session` **同源**：
      那边测的是"student 拿得到/拿不到会话"（请求侧 + 登录侧一起）；
      这里单独把**登录侧的契约**钉死（消息里必须点明"管理端"，且**原 h5 会话不受影响**）。
    """
    u = fresh_user(client, nickname="非白名单申报pc")
    b = body(
        client.post(
            LOGIN_PATH,
            headers={"X-Forwarded-For": u["_ip"]},
            json={"phone": u["phone"], "password": TEST_PASSWORD, "platform": "pc"},
        )
    )
    assert b["code"] == 40306, b
    assert "管理端" in b["message"], b

    # 拒绝**不影响**已有的 C 端会话（不能因为一次失败的 pc 申请把人踢下线）
    assert body(client.get(f"{API}/auth/me", headers=auth(u["access_token"])))["code"] == 0
