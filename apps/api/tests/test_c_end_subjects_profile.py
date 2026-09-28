"""C 端 · 科目列表 + 引导资料（P2a 的后端支撑）。

## 这一批只做两个接口（`docs/24` §3.2 的第 1 与第 3 个）

    GET  /subjects            引导页「选专业」的数据源
    PUT  /users/me/profile    写引导结果（专业 + 考试年份）

## 写法：每条固定一个**契约**，不是"覆盖某一行"

    ① 两个接口都**要登录** —— 它们是 C 端接口，走 P0 的会话墙，没有"公开"特例。
    ② `/subjects` 的 `id` 必须是**字符串**（雪花 ID 超 JS 安全整数）——
       这不是"格式好看"，是**前端 `JSON.parse` 会静默舍入**（`schemas/types.py` 抬头有实测）。
    ③ **引导完成的判据在服务端**：`professional` + `exam_year` 齐了才写 `onboarded_at`；
       客户端没有"宣布我完成了"的入口。
    ④ ★ **幂等**：完成之后再改年份，`onboarded_at` **不变**（硬约定 C：
       幂等分支**不动任何字段**）。这条最容易写成"每次 PUT 都刷时间戳" ——
       那样"完成时间"就成了"最后一次改资料的时间"，语义全错。
    ⑤ **`null` 不等于"清空"**：C 端表单 `JSON.stringify(state)` 时未填字段天然是 `null`，
       若把 `null` 当清空，用户没动过的资料会被**误清空**。
       ⚠️ 这一条配了**正向对照**（先设 A、再用 `null` 覆盖 → A 还在），
       否则"值没变"也可能只是"整个请求被忽略了"（假绿）。

## 为什么用 `fresh_user` 而不是 `register`（见 conftest 的注释）

`register()` 走 `127.0.0.1` 的**共享短信桶**，余量已经很紧；`fresh_user` 给每个账号
分一个假 IP，各自一个桶。
"""

from __future__ import annotations

import httpx

from .conftest import API, auth, body, fresh_user, sql_exec

#: 种子里的一级建造师科目数（`db/schema.sql` 的 `INSERT INTO subjects`）：
#: **3 门公共课**（`1001-1003`，`professional` 为 NULL）+ **10 门专业课**（`2001-2010`）。
#: ⚠️ 我第一版写的是"9 门专业课" —— **数错了**（`2001..2010` 是 10 个）。
#:   而这种"数错"不会报错，只会让断言对着一个错数字变绿/变红，所以写在常量里别散在断言中。
N_PUBLIC = 3
N_PROFESSIONAL = 10


def test_subjects_requires_login(client: httpx.Client) -> None:
    """无 token ⇒ 401 / **`40100`**（"请先登录"）。C 端接口**没有公开特例**。

    ⚠️ `40100` 与 `40101` 别混：`40100` = **压根没带 token**；
      `40101` = 带了但认证失败（过期/签名不对）；`40105` = 会话已被撤销。
      我第一版断言的是 `(40101, 40102)` —— **猜的**，实测是 `40100`。
    """
    r = client.get(f"{API}/subjects")
    assert r.status_code == 401, f"应 401，实际 {r.status_code}: {r.text[:160]}"
    assert body(r)["code"] == 40100, body(r)


def test_profile_requires_login(client: httpx.Client) -> None:
    """无 token ⇒ 401（写路径同样不许匿名）。"""
    r = client.put(f"{API}/users/me/profile", json={"professional": "jz"})
    assert r.status_code == 401, f"应 401，实际 {r.status_code}: {r.text[:160]}"


def test_subjects_list_shape(client: httpx.Client) -> None:
    """科目列表：种子全量、两类都在、**id 是字符串**、按 `sort_no` 升序。"""
    u = fresh_user(client, nickname="科目形态用例")
    b = body(client.get(f"{API}/subjects", headers=auth(u["access_token"])))
    assert b["code"] == 0, b
    items = b["data"]
    assert len(items) == N_PUBLIC + N_PROFESSIONAL, (
        f"种子应为 {N_PUBLIC} 公共课 + {N_PROFESSIONAL} 专业课，"
        f"实际 {len(items)}：{[s['code'] for s in items]}"
    )

    pub = [s for s in items if s["category"] == "public"]
    pro = [s for s in items if s["category"] == "professional"]
    assert len(pub) == N_PUBLIC and len(pro) == N_PROFESSIONAL, (len(pub), len(pro))
    # 公共课没有 professional；专业课都有，而且互不相同
    assert all(s["professional"] is None for s in pub)
    profs = [s["professional"] for s in pro]
    assert all(isinstance(p, str) and p for p in profs) and len(set(profs)) == N_PROFESSIONAL, profs

    # ★ 契约②：id 必须是**数字字符串**（不是 int）—— int 会被前端静默舍入
    for s in items:
        assert isinstance(s["id"], str), f"科目 id 必须是字符串，实际 {type(s['id'])}: {s['id']}"
        assert s["id"].isdigit(), s["id"]

    # sort_no 升序（前端不做二次排序，顺序就是后端的顺序）
    assert [s["sort_no"] for s in items] == sorted(s["sort_no"] for s in items)


def test_subjects_filter_professional(client: httpx.Client) -> None:
    """`category=professional` 只返专业课 —— 引导页「选专业」用的就是这个。"""
    u = fresh_user(client, nickname="科目筛选用例")
    h = auth(u["access_token"])
    b = body(client.get(f"{API}/subjects", params={"category": "professional"}, headers=h))
    assert b["code"] == 0, b
    assert len(b["data"]) == N_PROFESSIONAL
    assert {s["category"] for s in b["data"]} == {"professional"}

    b2 = body(client.get(f"{API}/subjects", params={"category": "public"}, headers=h))
    assert len(b2["data"]) == N_PUBLIC
    # 非法 category 由 FastAPI 的 Literal 校验挡住。
    # ⚠️ 是 **422**（不是 400）：本仓的 `RequestValidationError` 处理器**保留 422**，
    #    只把信封里的 code 统一成 40001（`core/errors.py:105`）。
    #    我第一版断言 400 —— 又一处"凭印象写的码"。
    r = client.get(f"{API}/subjects", params={"category": "nope"}, headers=h)
    assert r.status_code == 422, r.status_code
    assert body(r)["code"] == 40001, body(r)


def test_onboarding_flow_and_idempotency(client: httpx.Client) -> None:
    """★ 主用例：分两步完成引导；再改一次**不动** `onboarded_at`。"""
    u = fresh_user(client, nickname="引导用例")
    h = auth(u["access_token"])

    # ⚠️ 注册**已经建了资料行**（`auth_service.py:214` / `cli.py:278` 两条建号路径都建）
    #    ⇒ 这里不是 `None`，而是**默认值 + `onboarded_at=None`**。
    #    我第一版断言 `profile is None` —— 又一处凭印象（建号时会建什么，该去读代码）。
    me0 = body(client.get(f"{API}/auth/me", headers=h))["data"]
    assert me0["profile"] is not None, "注册应当已经建好资料行（默认值）"
    assert me0["profile"]["onboarded_at"] is None, "新账号当然还没完成引导"
    assert me0["profile"]["professional"] is None

    # 第一步：只选专业 ⇒ **还不算完成**
    b1 = body(client.put(f"{API}/users/me/profile", headers=h, json={"professional": "jz"}))
    assert b1["code"] == 0, b1
    assert b1["data"]["professional"] == "jz", b1["data"]
    assert b1["data"]["onboarded_at"] is None, "只选专业不该算完成引导"

    # 第二步：选年份 ⇒ 完成
    b2 = body(client.put(f"{API}/users/me/profile", headers=h, json={"exam_year": 2027}))
    assert b2["data"]["exam_year"] == 2027, b2["data"]
    assert b2["data"]["onboarded_at"] is not None, "专业 + 年份都齐了，应当写 onboarded_at"
    first_onboarded_at = b2["data"]["onboarded_at"]

    # ★ 契约④：再改一次年份 —— 字段更新，但 onboarded_at **一动不动**
    b3 = body(client.put(f"{API}/users/me/profile", headers=h, json={"exam_year": 2028}))
    assert b3["data"]["exam_year"] == 2028, b3["data"]
    assert b3["data"]["onboarded_at"] == first_onboarded_at, (
        "幂等分支不该改 onboarded_at —— 否则'完成时间'会变成'最后改资料的时间'"
    )

    # ★ 契约⑤ 的正向对照：没传的字段**保持原值**（不是被清空）
    assert b3["data"]["professional"] == "jz", "没传 professional，它必须还在"

    # 读路径走 /auth/me（写走 PUT，读走 me —— 单一真相）
    me = body(client.get(f"{API}/auth/me", headers=h))["data"]
    assert me["profile"]["professional"] == "jz", me["profile"]
    assert me["profile"]["exam_year"] == 2028, me["profile"]
    assert me["profile"]["onboarded_at"] == first_onboarded_at, me["profile"]


def test_null_does_not_clear_existing_value(client: httpx.Client) -> None:
    """★ 显式传 `null` **也是"没传"**（C 端表单未填字段天然是 null）。

    ⚠️ 必须有正向对照：先确认"设进去的值真的在"（上一段断言），
    否则"传 null 之后值还在"也可能只是"那次请求整个没生效"。
    """
    u = fresh_user(client, nickname="null 语义用例")
    h = auth(u["access_token"])

    b1 = body(client.put(f"{API}/users/me/profile", headers=h, json={"professional": "sz"}))
    assert b1["data"]["professional"] == "sz"

    b2 = body(client.put(f"{API}/users/me/profile", headers=h, json={"professional": None}))
    assert b2["code"] == 0, b2
    assert b2["data"]["professional"] == "sz", (
        "传 null 被当成了'清空' —— C 端表单会把未填字段序列化成 null，"
        "这个语义会把用户没动过的资料误清空"
    )


def test_profile_validation(client: httpx.Client) -> None:
    """越界 / 形状不对 ⇒ HTTP **422** + 信封 `40001`（不是 500，也不是静默接受）。"""
    u = fresh_user(client, nickname="引导校验用例")
    h = auth(u["access_token"])

    # 年份越界（1900 会让前端算出几十年的倒计时）
    r = client.put(f"{API}/users/me/profile", headers=h, json={"exam_year": 1900})
    assert r.status_code == 422, r.status_code
    assert body(r)["code"] == 40001, body(r)

    # target_subjects 用**数字**（会被静默舍入的那种形状）⇒ 拒
    r2 = client.put(f"{API}/users/me/profile", headers=h, json={"target_subjects": [2001]})
    assert r2.status_code == 422, r2.status_code
    assert body(r2)["code"] == 40001, body(r2)

    # 合法形状：数字**字符串** ⇒ 通过，并存成 JSON 数组
    b = body(client.put(f"{API}/users/me/profile", headers=h, json={"target_subjects": ["2001"]}))
    assert b["code"] == 0, b
    assert b["data"]["target_subjects"] == ["2001"], b["data"]


def test_profile_upsert_when_row_missing(client: httpx.Client) -> None:
    """资料行**缺失**时 PUT 要**自动补上**，而不是 500。

    ⚠️ 为什么要专门测这条：所有建号路径**今天**都会建资料行
    （`auth_service.py:214` / `cli.py:278`）⇒ `profile_service` 里那个
    `if row is None` 分支**正常流程永远走不到**。
    而"走不到"有两种处置：删掉它（然后在真的缺行时炸成 500），或者**把条件造出来**测它。
    ⇒ 选后者：`PUT` 的语义本来就是 **upsert**（引导页是"补资料"的地方，
      最不该因为"缺一行"而 500）。这里直接删掉那行，制造真实条件。
    """
    u = fresh_user(client, nickname="资料行缺失用例")
    h = auth(u["access_token"])
    uid = u["user"]["id"]

    sql_exec("DELETE FROM user_profiles WHERE user_id = $1", int(uid))

    b = body(
        client.put(
            f"{API}/users/me/profile",
            headers=h,
            json={"professional": "jd", "exam_year": 2027},
        )
    )
    assert b["code"] == 0, b
    assert b["data"]["professional"] == "jd"
    assert b["data"]["onboarded_at"] is not None, "两个字段都齐了 ⇒ 应当写 onboarded_at"
