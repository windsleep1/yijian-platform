/**
 * C 端用到的接口类型。
 *
 * ⚠️ **手写而非从后端生成**：与 `apps/admin/src/lib/types.ts` 的取舍一致 ——
 * 后端是 Pydantic、前端手写 TS，两者靠**契约测试**（`apps/api/tests/`）而不是代码生成来对齐。
 * 走代码生成要引入一整套工具链，而本项目「契约守卫」已经在后端侧钉着（硬约定：**契约守卫** > 覆盖率）。
 *
 * P1 只覆盖**登录闭环**用到的三个形状。后面的页面按需往里加，
 * **不要**为了"看起来完整"预先声明还没接口的字段（那会让 `unknown` 变 `never` 之外看不出问题）。
 */

/** 统一信封 —— 所有接口的外层形状（后端 `app/core/response.py`）。 */
export type Envelope<T> = {
  code: number;
  message: string;
  data: T;
  /** 业务错误时后端会带；网关层错误没有，只能读响应头 `X-Request-ID`。 */
  trace_id?: string;
  server_time?: number;
};

/** `GET /auth/me` 里嵌的 `profile`（`schemas/auth.py` 的 `ProfileOut`）。 */
export type Profile = {
  exam_level: string;
  professional: string | null;
  exam_year: number | null;
  province: string | null;
  target_subjects: unknown[];
  target_score: number | null;
  daily_goal_min: number;
  /** 为 null = **还没做引导** ⇒ 首页据此决定要不要把人送去 `/onboarding`（P2）。 */
  onboarded_at: string | null;
};

/**
 * `GET /auth/me` 的返回（`schemas/auth.py` 的 `MeOut`）。
 *
 * ★ `id` 是 **string**：后端用 `BigIntStr` 序列化雪花 ID（18~19 位，超出 JS 安全整数）。
 *   前端**任何地方都不许对它 `Number()`** —— 见 `json-bigint.ts` 抬头那段"为什么先处理文本"。
 */
export type Me = {
  id: string;
  phone: string | null;
  nickname: string;
  avatar_url: string | null;
  status: string;
  roles: string[];
  permissions: string[];
  scopes: unknown[];
  profile: Profile | null;
  last_login_at: string | null;
  created_at: string | null;
};

/** 登录 / 注册 / 刷新 的返回（`schemas/auth.py` 的 `TokenPairOut`）。 */
export type TokenPair = {
  token_type: string;
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: Me;
};

/**
 * `GET /subjects` 的一项（`schemas/c_end.py` 的 `SubjectOut`）。
 *
 * ★ `id` 同样是 **string**（雪花 ID）—— 它会被当作 `target_subjects` 写回去，
 *   所以**必须**原样传递，任何 `Number()` 都会让它变成一个不存在的科目。
 */
export type Subject = {
  id: string;
  code: string;
  name: string;
  short_name: string | null;
  exam_level: string;
  /** `public` 公共课 / `professional` 专业课。引导页的"选专业"筛的是后者。 */
  category: string;
  /** 专业课的所属专业码（`jz`/`sz`…）；公共课为 `null`。它就是引导页要存的值。 */
  professional: string | null;
  full_score: number;
  pass_score: number;
  duration_min: number;
  color: string | null;
  sort_no: number;
};

/** `PUT /users/me/profile` 的请求体（`schemas/c_end.py` 的 `ProfileUpdateIn`）。 */
export type ProfileUpdate = {
  exam_level?: "yijian" | "erjian";
  professional?: string;
  exam_year?: number;
  /** ⚠️ 科目 ID 用**字符串**：后端会拒数字（雪花 ID 会被 JSON 静默舍入）。 */
  target_subjects?: string[];
  target_score?: number;
  daily_goal_min?: number;
};
