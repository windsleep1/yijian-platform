"""
RBAC 服务。

模型：user --(user_roles, 带 scope)--> role --(role_permissions)--> permission
权限码格式：module:action，例如 question:create / user:read / system:role

数据范围（scope）：
    global        全站
    subject       限定科目（scope_id = subjects.id）
    professional  限定专业（scope_id 暂用字符串专业码的映射值）
    course        限定课程

Batch 2 只做「权限码校验」+ 暴露 scopes；
行级数据范围过滤（按 scope 过滤查询结果）在 Batch 4 与题库模块一起落地。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import redis.asyncio as aioredis
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, not_found
from app.core.idgen import next_id
from app.db.models import Permission, Role, UserRole
from app.schemas.admin_rbac import PermissionNode, RoleItem

logger = logging.getLogger("app.rbac")

PRIV_CACHE_TTL = 300
PRIV_CACHE_KEY = "rbac:priv:{user_id}"

# 可被后台分配的角色（super_admin 只能由 CLI 授予，避免越权提权）
#
# viewer：只读管理员，给「审计岗」用。存在的另一个现实理由——
# 种子里的 6 个角色里，凡是有 user:read 的（super_admin/admin/operator）**都有 user:manage**，
# 没有 user:manage 的（researcher/teacher/student）又都进不了用户页。
# 于是「无 user:manage → 按钮 disabled」这个状态**没有任何账号能复现**。
# viewer 补上了这个缺口：有 user:read、没有 user:manage。
ASSIGNABLE_ROLES = {"admin", "researcher", "teacher", "operator", "student", "viewer"}


@dataclass
class Privileges:
    roles: list[dict] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    scopes: list[dict] = field(default_factory=list)

    @property
    def role_codes(self) -> list[str]:
        return [r["code"] for r in self.roles]

    @property
    def permission_set(self) -> set[str]:
        return set(self.permissions)

    def has(self, *codes: str) -> bool:
        """任一命中即通过（用于 require_any）。"""
        return bool(self.permission_set.intersection(codes))

    def has_all(self, *codes: str) -> bool:
        return set(codes).issubset(self.permission_set)

    def to_cache(self) -> str:
        return json.dumps(
            {"roles": self.roles, "permissions": self.permissions, "scopes": self.scopes},
            ensure_ascii=False,
        )

    @classmethod
    def from_cache(cls, raw: str) -> "Privileges":
        data = json.loads(raw)
        return cls(roles=data["roles"], permissions=data["permissions"], scopes=data["scopes"])


_ROLES_SQL = text(
    """
    SELECT r.id, r.code, r.name, ur.scope_type, ur.scope_id
    FROM user_roles ur
    JOIN roles r ON r.id = ur.role_id
    WHERE ur.user_id = :uid
      AND (ur.expires_at IS NULL OR ur.expires_at > now())
    ORDER BY r.sort_no
    """
)

_PERMS_SQL = text(
    """
    SELECT DISTINCT p.code AS code
    FROM user_roles ur
    JOIN role_permissions rp ON rp.role_id = ur.role_id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE ur.user_id = :uid
      AND (ur.expires_at IS NULL OR ur.expires_at > now())
    ORDER BY p.code
    """
)


async def _load_privileges(db: AsyncSession, user_id: int) -> Privileges:
    role_rows = (await db.execute(_ROLES_SQL, {"uid": user_id})).mappings().all()
    perm_rows = (await db.execute(_PERMS_SQL, {"uid": user_id})).scalars().all()

    roles = [
        {
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "scope_type": r["scope_type"],
            "scope_id": r["scope_id"],
        }
        for r in role_rows
    ]
    # 数据范围用于前端决定「能看到哪些科目/专业」，与权限码分开表达
    scopes = [
        {"role_code": r["code"], "scope_type": r["scope_type"], "scope_id": r["scope_id"]}
        for r in role_rows
    ]
    return Privileges(roles=roles, permissions=list(perm_rows), scopes=scopes)


async def get_privileges(
    db: AsyncSession, user_id: int, redis: aioredis.Redis | None = None
) -> Privileges:
    """读用户权限，优先走 Redis 缓存（TTL 5 分钟）。"""
    key = PRIV_CACHE_KEY.format(user_id=user_id)
    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached:
                return Privileges.from_cache(cached)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读权限缓存失败，回源数据库: %s", exc)

    priv = await _load_privileges(db, user_id)

    if redis is not None:
        try:
            await redis.set(key, priv.to_cache(), ex=PRIV_CACHE_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("写权限缓存失败: %s", exc)
    return priv


async def invalidate_privileges(redis: aioredis.Redis | None, user_id: int) -> None:
    if redis is None:
        return
    try:
        await redis.delete(PRIV_CACHE_KEY.format(user_id=user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("清权限缓存失败: %s", exc)


async def list_roles(db: AsyncSession) -> list[Role]:
    rows = await db.execute(select(Role).order_by(Role.sort_no))
    return list(rows.scalars().all())


async def get_role_by_code(db: AsyncSession, code: str) -> Role | None:
    rows = await db.execute(select(Role).where(Role.code == code))
    return rows.scalar_one_or_none()


async def assign_roles(
    db: AsyncSession,
    *,
    redis: aioredis.Redis | None,
    user_id: int,
    role_codes: list[str],
    scope_type: str = "global",
    scope_id: int | None = None,
    granted_by: int | None = None,
    expires_at=None,
) -> Privileges:
    """整体替换用户角色（不是追加）。返回替换后的权限。"""
    if scope_type != "global" and scope_id is None:
        raise bad_request("scope_type 非 global 时必须提供 scope_id", 40001)

    wanted = sorted({c.strip() for c in role_codes if c and c.strip()})
    illegal = [c for c in wanted if c not in ASSIGNABLE_ROLES]
    if illegal:
        raise bad_request(
            f"角色不可分配：{'、'.join(illegal)}（可分配：{'、'.join(sorted(ASSIGNABLE_ROLES))}）",
            40003,
        )

    role_map: dict[str, Role] = {}
    if wanted:
        rows = await db.execute(select(Role).where(Role.code.in_(wanted)))
        role_map = {r.code: r for r in rows.scalars().all()}
        missing = [c for c in wanted if c not in role_map]
        if missing:
            raise not_found(f"角色不存在：{'、'.join(missing)}", 40402)

    await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for code in wanted:
        db.add(
            UserRole(
                id=next_id(),
                user_id=user_id,
                role_id=role_map[code].id,
                scope_type=scope_type,
                scope_id=scope_id,
                granted_by=granted_by,
                expires_at=expires_at,
            )
        )
    await db.flush()
    await invalidate_privileges(redis, user_id)
    return await get_privileges(db, user_id, redis)


async def ensure_role_assigned(
    db: AsyncSession, *, user_id: int, role_code: str, role_id_cache: dict[str, int] | None = None
) -> None:
    """幂等授予单个角色（注册送 student、CLI 送 super_admin 用）。"""
    role_id = (role_id_cache or {}).get(role_code)
    if role_id is None:
        role = await get_role_by_code(db, role_code)
        if role is None:
            raise not_found(f"角色 {role_code} 不存在，请确认已执行 db/schema.sql", 40402)
        role_id = role.id
        if role_id_cache is not None:
            role_id_cache[role_code] = role_id

    exists = await db.execute(
        select(UserRole.id).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
            UserRole.scope_type == "global",
        )
    )
    if exists.scalar_one_or_none() is None:
        db.add(UserRole(id=next_id(), user_id=user_id, role_id=role_id, scope_type="global"))
        await db.flush()


# =====================================================================
# 管理端：角色与权限（Batch 3）
# =====================================================================

# 模块展示顺序与中文名。permissions.sort_no 是**模块内**序号（每个模块从 1 重来），
# 所以模块之间的顺序必须单独定义，不能靠 sort_no。
MODULE_ORDER: list[str] = [
    "question",
    "exam",
    "course",
    "user",
    "order",
    "content",
    "stats",
    "system",
]

MODULE_NAMES: dict[str, str] = {
    "question": "题库",
    "exam": "试卷",
    "course": "课程",
    "user": "用户",
    "order": "订单",
    "content": "内容",
    "stats": "统计",
    "system": "系统",
}

_MODULE_UNKNOWN_SORT = 999


async def list_roles_with_permissions(
    db: AsyncSession, *, include_permissions: bool = True
) -> list[RoleItem]:
    """角色列表 + 每个角色的权限码。

    **一次 LEFT JOIN 批量取全量**，不要写成"循环角色各查一次权限"——
    6 个角色打 7 次库，是 B 端最典型的 N+1 性能事故。

    `is_assignable` 直接把 `ASSIGNABLE_ROLES` 白名单透出去，让前端把 `super_admin`
    置灰并说明原因，而不是等用户点了提交才吃 `40003`。
    """
    rows = (
        (
            await db.execute(
                text(
                    """
                SELECT r.id, r.code, r.name, r.description, r.is_system, r.sort_no,
                       p.code AS perm_code
                FROM roles r
                LEFT JOIN role_permissions rp ON rp.role_id = r.id
                LEFT JOIN permissions p ON p.id = rp.permission_id
                ORDER BY r.sort_no, p.sort_no, p.id
                """
                )
            )
        )
        .mappings()
        .all()
    )

    merged: dict[int, RoleItem] = {}
    for row in rows:
        item = merged.get(row["id"])
        if item is None:
            item = RoleItem(
                id=row["id"],
                code=row["code"],
                name=row["name"],
                description=row["description"],
                is_system=bool(row["is_system"]),
                sort_no=row["sort_no"],
                is_assignable=row["code"] in ASSIGNABLE_ROLES,
                permissions=[],
            )
            merged[row["id"]] = item
        if include_permissions and row["perm_code"]:
            item.permissions.append(row["perm_code"])

    return list(merged.values())


async def build_permission_tree(
    db: AsyncSession, *, module: str | None = None
) -> tuple[list[PermissionNode], int]:
    """权限树。

    **一个 schema 事实要先说清**：`permissions` 表有 `parent_id`，但 `db/schema.sql`
    的种子数据里 `parent_id` **全是 NULL**，24 条权限是扁平的，
    层级靠 `module` + `code` 的 `module:action` 约定表达。

    所以策略是「**优先按 `parent_id` 递归，缺失时按 `module` 兜底**」——
    将来真接了菜单树（`type='menu'` 的节点填上 parent_id），本接口不用改。
    """
    stmt = select(Permission).order_by(Permission.module, Permission.sort_no, Permission.id)
    if module:
        stmt = stmt.where(Permission.module == module)
    perms = list((await db.execute(stmt)).scalars().all())

    if any(p.parent_id is not None for p in perms):
        return _tree_by_parent(perms)
    return _tree_by_module(perms)


def _node(p: Permission) -> PermissionNode:
    return PermissionNode(
        key=f"p:{p.id}",
        code=p.code,
        name=p.name,
        type=p.type,
        module=p.module,
        sort_no=p.sort_no,
    )


def _sort_tree(nodes: list[PermissionNode]) -> None:
    nodes.sort(key=lambda n: (n.sort_no, n.code))
    for n in nodes:
        _sort_tree(n.children)


def _tree_by_module(perms: list[Permission]) -> tuple[list[PermissionNode], int]:
    buckets: dict[str, list[Permission]] = {}
    for p in perms:
        buckets.setdefault(p.module or "other", []).append(p)

    def module_sort(m: str) -> tuple[int, str]:
        return (
            MODULE_ORDER.index(m) if m in MODULE_ORDER else _MODULE_UNKNOWN_SORT,
            m,
        )

    tree: list[PermissionNode] = []
    for m in sorted(buckets, key=module_sort):
        children = sorted((_node(p) for p in buckets[m]), key=lambda n: (n.sort_no, n.code))
        tree.append(
            PermissionNode(
                key=f"m:{m}",
                code=m,
                name=MODULE_NAMES.get(m, m),
                type="module",
                module=m,
                sort_no=module_sort(m)[0],
                children=children,
            )
        )
    return tree, len(perms)


def _tree_by_parent(perms: list[Permission]) -> tuple[list[PermissionNode], int]:
    nodes = {p.id: _node(p) for p in perms}
    roots: list[PermissionNode] = []
    for p in perms:
        node = nodes[p.id]
        parent = nodes.get(p.parent_id) if p.parent_id else None
        if parent is not None:
            parent.children.append(node)
        else:
            roots.append(node)
    _sort_tree(roots)
    return roots, len(perms)
