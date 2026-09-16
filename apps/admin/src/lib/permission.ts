/**
 * 权限码集中管理 + 判定纯函数。
 *
 * 为什么不在各处手写字符串：权限码是**跨前后端的契约**，
 * 写错一个字符（`user:manage` → `user:manager`）不会报错，
 * 只会"按钮永远置灰"或"接口永远 403"，很难查。集中定义 + TS 字面量类型能挡住。
 */

export const P = {
  userRead: "user:read",
  userManage: "user:manage",
  userExport: "user:export",
  systemAudit: "system:audit",
  systemRole: "system:role",
  systemConfig: "system:config",
  statsRead: "stats:read",
  questionRead: "question:read",
  questionCreate: "question:create",
  questionUpdate: "question:update",
  questionDelete: "question:delete",
} as const;

export type PermissionCode = (typeof P)[keyof typeof P];

/** 是否拥有某个权限。`perms` 为空/未加载时一律 false（宁可不显示，也不能误放）。 */
export function can(perms: string[] | undefined | null, code: string): boolean {
  return !!perms?.includes(code);
}

/** 命中任一即可。 */
export function canAny(perms: string[] | undefined | null, codes: readonly string[]): boolean {
  if (!perms?.length) return false;
  return codes.some((c) => perms.includes(c));
}

/** 角色 code → 展示名。后端也有中文名，但菜单/徽章需要前端能独立渲染。 */
export const ROLE_LABELS: Record<string, string> = {
  super_admin: "超级管理员",
  admin: "管理员",
  researcher: "教研",
  teacher: "教师",
  operator: "运营",
  student: "学员",
  viewer: "只读审计员",
};

export function roleLabel(code: string): string {
  return ROLE_LABELS[code] ?? code;
}

/** 状态码 → 展示（用户 status 字段）。 */
export const STATUS_LABELS: Record<string, string> = {
  active: "正常",
  disabled: "已禁用",
  locked: "已锁定",
  deleted: "已注销",
};

export function statusLabel(code: string): string {
  return STATUS_LABELS[code] ?? code;
}

// ---------------------------------------------------------------- 后台入口 / 落地页

/**
 * 各后台模块的「入口权限」。
 *
 * **顺序即优先级**，同时也是登录后的默认落地页顺序 —— 两者共用一份定义，
 * 是为了避免"菜单第一项"和"登录跳哪"各写一遍、改一处漏一处。
 *
 * ## 为什么必须有这份表（Batch 4 实际踩到）
 *
 * Batch 3 只有 `user:read` / `system:audit` 两种入口，于是"已登录能进后台"
 * 被硬编码成了 `["/users"]` 这个默认落地页。Batch 4 加了题库模块之后：
 *
 *   - `researcher`（教研）有 `question:read`，但**没有** `user:read` / `system:audit`；
 *   - 它是题库模块真正的使用者，却在登录时被判为"没有任何后台权限"，
 *     或在跳转 `/users` 后立刻吃到 403 —— **有权的人进不来**。
 *
 * 所以"能进后台"与"该落在哪一页"必须由**权限**推导，不能写死路径。
 */
export const MODULE_ENTRIES = [
  { perm: P.questionRead, href: "/questions", label: "题库管理" },
  { perm: P.userRead, href: "/users", label: "用户管理" },
  { perm: P.systemAudit, href: "/audit-logs", label: "审计日志" },
] as const;

/** 拥有任一模块入口权限即可进入控制台（否则登录页应给出明确提示）。 */
export function canEnterConsole(perms: string[] | undefined | null): boolean {
  return MODULE_ENTRIES.some((m) => can(perms, m.perm));
}

/**
 * 按权限挑一个真正进得去的落地页；一个模块都无权限时返回 `null`。
 *
 * 返回 `null` 是**有意义的返回值**，调用方必须处理（渲染 403 页），
 * 而不是兜底成 `/users` —— 那正是本 Bug 的成因。
 */
export function landingPath(perms: string[] | undefined | null): string | null {
  return MODULE_ENTRIES.find((m) => can(perms, m.perm))?.href ?? null;
}

