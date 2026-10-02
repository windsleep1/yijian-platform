"""演示账号（只读）—— 用**行为**证明「能看、不能改」。

## 为什么值得单独钉一份

README 的「Live Demo」段把这个账号**公示**给任何人（面试官、评审）。
它一旦能写，就等于把整个后台交给了陌生人。所以两头都要有判据：

- **读**：viewer 的四个权限（`user:read` / `system:audit` / `stats:read` / `exam:read`）
  覆盖的接口必须 **200 且真的有数据**（"能看列表"）；
- **读不着**：它**没有**的权限（如 `question:read`）必须 **40301**
  —— 否则"只读"就退化成"什么都能读"，那是另一种越权；
- **写**：逐个打写接口必须 **40301**。其中**最关键的一条是"给自己提权"**
  （`PUT /admin/users/{自己}/roles`）—— 演示账号最危险的用法就是拿它把自己升级成管理员。

## ★★ 判据：**"只读"要用行为证，不能用权限名证**

按名字猜（"带 read 就是只读"）等于把 viewer 的权限组成**又抄了一份** ——
而唯一真相在 `db/schema.sql` §12.2。抄一份的下场是**漂移**（改了种子忘了改判据），
而不是报错。这里的每一条都是"发一个真请求、看它真返回什么"。

（同族：坑 83「题干不是身份」、坑 94「页面存在 ≠ 用户到得了」。）
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import API, assign_roles, body, fresh_user, relogin


@pytest.fixture(scope="module")
def viewer(client: httpx.Client, admin_h: dict[str, str]) -> dict:
    """造一个**只读演示账号**：注册 → 授 `viewer` → **重新登录**拿管理端会话。

    ⚠️ `relogin(platform="pc")` 不是多余的：**身份在会话建立时确定**（`docs/22` §6.5.6），
    授角色之后注册那个 `h5` token **拿不到管理端会话** ⇒ 直接用它打 `/admin/*`
    会得到 `40306`（会话墙），而不是我们想验的 `40301`（权限）。
    """
    u = fresh_user(client, nickname="演示账号（只读）")
    g = assign_roles(client, admin_h, u["user"]["id"], ["viewer"])
    assert g["code"] == 0, g
    assert sorted(g["data"]["granted_permissions"]) == [
        "exam:read",
        "stats:read",
        "system:audit",
        "user:read",
    ], g["data"]
    return {"user": u, "h": relogin(client, u, platform="pc"), "id": u["user"]["id"]}


def _code(r: httpx.Response) -> int:
    return int(body(r)["code"])


# ---------------------------------------------------------------- 读：能看


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("/admin/users", "user:read"),
        ("/admin/roles", "user:read"),
        ("/admin/permissions", "user:read"),
        ("/admin/exams", "exam:read"),
        ("/admin/audit-logs", "system:audit"),
        ("/admin/stats/overview", "stats:read"),
    ],
)
def test_viewer_can_read_what_its_permissions_cover(
    client: httpx.Client, viewer: dict, path: str, why: str
) -> None:
    """`viewer` 的四个读权限覆盖的接口都要 **200 且有数据**。

    ⚠️ "200" 不够 —— 一个返回空列表的 200 也能骗过这条。所以要**同时**断言
    "拿得到东西"（`payload` 非空 / `total > 0`）。演示的时候页面是空的，
    和"接口坏了"在观众眼里长得一样。
    """
    r = client.get(f"{API}{path}", headers=viewer["h"])
    assert r.status_code == 200, f"{path} 应当可读（{why}）：{r.text[:200]}"
    payload = body(r)["data"]
    assert payload not in (None, [], {}), f"{path} 返回了空数据 —— 演示页会是白板"


def test_viewer_still_cannot_read_what_it_lacks(client: httpx.Client, viewer: dict) -> None:
    """★ 成对验的另一头：**没有**的权限必须被挡住。

    只验"能读"会漏掉"它其实什么都能读"的实现（那也是一种越权）。
    `question:read` 是 viewer **没有**的（题库属于出题/审核岗）。
    """
    r = client.get(f"{API}/admin/questions", headers=viewer["h"])
    assert r.status_code == 403, f"viewer 不该读得到题库：{r.text[:200]}"
    assert _code(r) == 40301


# ---------------------------------------------------------------- 写：不能改

#: 逐个写接口。**每一条都是"真发一个请求"**，不看权限名。
#: `why` 写的是它需要的权限码 —— 故意的：它让失败信息能直接说出"缺什么"。
WRITE_PROBES: list[tuple[str, str, dict | None, str]] = [
    ("DELETE", "/admin/exams/1", None, "exam:create"),
    ("POST", "/admin/exams/1/publish", None, "exam:publish"),
    ("POST", "/admin/questions/1/submit", None, "question:update"),
    (
        "POST",
        "/admin/imports/00000000-0000-0000-0000-000000000000/execute",
        None,
        "question:import",
    ),
    ("PATCH", "/admin/users/1/status", {"status": "disabled"}, "user:manage"),
]


@pytest.mark.parametrize(("method", "path", "payload", "why"), WRITE_PROBES)
def test_viewer_writes_are_refused(
    client: httpx.Client, viewer: dict, method: str, path: str, payload: dict | None, why: str
) -> None:
    """任何写操作都必须 **403 + 40301**，且**不能真的改到东西**。"""
    r = client.request(method, f"{API}{path}", headers=viewer["h"], json=payload)
    assert r.status_code == 403, f"{method} {path} 竟然不是 403（需要 {why}）：{r.text[:200]}"
    assert _code(r) == 40301, f"{method} {path} 的码不是 40301：{r.text[:200]}"


def test_viewer_cannot_escalate_its_own_privileges(
    client: httpx.Client, viewer: dict, admin_h: dict[str, str]
) -> None:
    """★★ **最重要的一条**：拿演示账号把自己升级成管理员，必须被拒。

    这是"只读演示账号"最危险的用法 —— 而它只需要一个 `PUT`。
    断言之后**再确认一次它仍然是 viewer**（拒绝必须零副作用，硬约定 C 的同族：
    "拒绝时要说清哪一条" + "幂等判据 = 目标状态已达成"）。
    """
    r = client.put(
        f"{API}/admin/users/{viewer['id']}/roles",
        headers=viewer["h"],
        json={"role_codes": ["super_admin", "admin"]},
    )
    assert r.status_code == 403, f"提权竟然没被拒：{r.text[:200]}"
    assert _code(r) == 40301

    # ★ 零副作用：用超管**重新读一次**这个账号的角色，必须仍然是 viewer
    got = client.get(f"{API}/admin/users/{viewer['id']}", headers=admin_h)
    assert got.status_code == 200, got.text[:200]
    roles = body(got)["data"]["roles"]
    assert roles == ["viewer"], f"提权被拒了，但角色被改动了：{roles}"


def test_viewer_session_is_a_real_admin_session(client: httpx.Client, viewer: dict) -> None:
    """反证锚：**先证明这个账号本来就能进后台**。

    ★ 没有这一格，"403" 有一万种解释（会话墙 40306、token 过期 40105、角色不在白名单…）——
    那样上面那些 `40301` 的断言就可能是在证明**另一件事**（硬约定：E2E「可证伪锚」）。
    """
    me = client.get(f"{API}/auth/me", headers=viewer["h"])
    assert me.status_code == 200, me.text[:200]
    assert body(me)["data"]["roles"] == ["viewer"], body(me)["data"]
