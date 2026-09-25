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
  /** 批量导入（上传 / 校验 / 执行是同一件事的三个阶段，共用这一个权限） */
  questionImport: "question:import",
  /** 把草稿推向线上题库（权责与"导入"不同） */
  questionPublish: "question:publish",
  /** 整批回滚（`admin` 角色刻意没有这个权限，只有 super_admin / researcher 有） */
  questionRollback: "question:rollback",

  // ---- Batch 7 组卷 ----
  /** 看试卷列表 / 详情 / 校验结果 */
  examRead: "exam:read",
  /**
   * 建卷 / 改卷 / 组卷 / **加题移题** / **归档**。
   *
   * ⚠️ 归档复用 `exam:create` 而不是单独的 `exam:delete`：权限码是跨前后端契约，
   * 加一条要同时改种子与前端常量；而"能建卷的人能归档自己的卷"是合理的权责边界。
   */
  examCreate: "exam:create",
  /**
   * 发布。
   *
   * ⚠️ **恢复（解除归档）也用它**，不是 `exam:create`：一份**已发布**的卷恢复后
   * 立刻重新对外可见，这个动作的分量更接近发布。
   */
  examPublish: "exam:publish",
  /** 阅卷（本批未开接口，先把权限码占位，避免以后各处手写字符串） */
  examGrade: "exam:grade",
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
 * ⚠️ **顺序 = 默认优先级，但不等于"所有角色都该落这里"**：将来要按角色区分落地页时，
 * 正解是 **per-role 覆盖**（`roles` 挂 `landing_module`，或在本文件做一张覆盖表），
 * **不要**继续调这张表 —— 调它会影响**所有**角色。
 *
 * ## 📌 待办 BL-04：显式 per-role 落地页配置（**未做**，有触发条件）
 *
 * > 当**为了某个角色的落地页去改 `MODULE_ENTRIES` 的顺序**时，
 * > 改做**显式 per-role 落地页配置**，而不是继续靠"权限过滤后第一个命中"。
 *
 * **为什么是这个触发条件**（而不是"角色数 ≥ 3"）：角色数**早就 ≥ 3**（种子里有 7 个），
 * 拿它当触发条件 = 条件**当场就已满足**，等于"现在做"或"永远挂着一个假条件"。
 * 真正说明"这张表已经扛不住"的信号是：**又有人想调它的顺序** ——
 * 上一次这么调的是 Batch 8（`operator` 的落地页被顺带改成了 `/dashboard`，见下方注释）。
 * 出现第二次，就说明这张表在同时承担"菜单顺序"和"登录跳哪"两件事，该拆了。
 *
 * 权威记录：`docs/21-待办清单.md` 的 **BL-04**（编号 / 触发条件 / 检查点）。
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
  // Batch 7：试卷管理紧挨着题库（两侧都是"内容"模块），但排在题库之后 ——
  // 顺序即优先级，教研登录后的落地页仍然应当是题库列表。
  { perm: P.examRead, href: "/exams", label: "试卷管理" },
  // Batch 8：**统计看板**。放**第三位**（用户 2026-09-24 裁定）—— 不放第一位。
  //
  //   这张表的顺序**就是登录后的落地页优先级**（见上面的说明），而落地页应当是
  //   **最高频动作**：对管理员/教研来说每天做的是用户管理、题库管理、导入；
  //   统计是"**看一眼大盘**"，不是每天第一件事。插到第一位，等于用一个"一天看一次"的
  //   页面，盖掉一个"一天用十次"的页面。
  //
  //   ⚠️ **一个已知并接受的副作用**：`operator`（运营）的落地页从 `/users`
  //   变成了 `/dashboard` —— 它在第 1、2 位（题库 / 试卷）都没有权限，第 3 位正好命中。
  //   这个方向是对的（运营岗第一件事确实是看大盘），但它**确实是行为变化**，故记在此处。
  //   将来要做更细的控制，正解是 **per-role 覆盖**（见下），而不是继续调这里的顺序。
  { perm: P.statsRead, href: "/dashboard", label: "统计看板" },
  // 组卷规则是"试卷的模板"，紧跟试卷管理（入口权限同一个 `exam:read`）
  { perm: P.examRead, href: "/paper-rules", label: "组卷规则" },
  // Batch 6：导入向导。入口权限仍是 `question:read`（列表/详情是只读接口），
  // 放在 /questions 之后 —— 顺序即优先级，扫码一样的落地页应当还是题库列表。
  { perm: P.questionRead, href: "/imports", label: "题库导入" },
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
