/**
 * 与后端契约一一对应的 TS 类型。
 *
 * 约定（**不要在本文件之外手写接口字段**）：
 *  1. 字段名保持后端的 `snake_case`。前端不做 camelCase 转换 ——
 *     多一层映射就多一处能写错的地方，而类型系统在边界上骗不了人。
 *  2. **所有 ID 都是 `string`。** 后端 Pydantic 层用 `BigIntStr` 序列化过了。
 *     写成 `number` 会在 `JSON.parse` 阶段就丢精度（见 json-bigint.ts）。
 *  3. 时间字段是 ISO 8601 字符串（UTC），展示时统一按 Asia/Shanghai 渲染（见 format.ts）。
 */

// ---------------------------------------------------------------- 通用信封

export type Envelope<T> = {
  code: number;
  message: string;
  data: T;
  trace_id: string;
  server_time: number;
};

export type Page<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
};

/** 雪花 ID 的字符串形式，如 "375228939615866880"。 */
export type Id = string;

// ---------------------------------------------------------------- 认证

export type ScopeOut = {
  role_code: string;
  scope_type: string;
  scope_id: Id | null;
};

export type ProfileOut = {
  exam_level: string;
  professional: string | null;
  exam_year: number | null;
  province: string | null;
  target_subjects: unknown[];
  target_score: number | null;
  daily_goal_min: number;
  onboarded_at: string | null;
};

/** GET /auth/me 与登录响应里的 user。 */
export type MeOut = {
  id: Id;
  phone: string | null;
  nickname: string;
  avatar_url: string | null;
  status: string;
  roles: string[];
  permissions: string[];
  scopes: ScopeOut[];
  profile: ProfileOut | null;
  last_login_at: string | null;
  created_at: string | null;
};

export type TokenPairOut = {
  token_type: string;
  access_token: string;
  refresh_token: string;
  expires_in: number;
  user: MeOut;
};

export type LoginIn = {
  phone: string;
  password: string;
  device_id?: string;
  platform?: string;
  app_version?: string;
};

// ---------------------------------------------------------------- 用户

export type AdminUserItem = {
  id: Id;
  /** 已脱敏，如 138****8888 */
  phone: string | null;
  nickname: string;
  status: string;
  roles: string[];
  register_source: string | null;
  login_count: number;
  last_login_at: string | null;
  created_at: string | null;
};

export type UserScopeBrief = {
  role_code: string;
  scope_type: string;
  scope_id: Id | null;
};

export type AdminUserDetail = {
  id: Id;
  /** 永远脱敏 */
  phone: string | null;
  /** 仅当调用者拥有 user:export 时才有值，否则为 null */
  phone_full: string | null;
  email: string | null;
  username: string | null;
  real_name: string | null;
  nickname: string;
  avatar_url: string | null;
  status: string;
  remark: string | null;
  register_source: string | null;
  register_ip: string | null;
  last_login_ip: string | null;
  last_login_at: string | null;
  login_count: number;
  created_at: string | null;
  profile: Record<string, unknown> | null;
  roles: string[];
  scopes: UserScopeBrief[];
  /** 调用者是否有 user:manage —— 前端据此决定「分配角色」按钮可用性 */
  can_manage_roles: boolean;
  recent_audits: AuditLogItem[];
};

export type ScopeType = "global" | "subject" | "professional" | "course";

export type AssignRolesIn = {
  role_codes: string[];
  scope_type?: ScopeType;
  scope_id?: number | null;
  expires_at?: string | null;
};

export type RoleBrief = {
  id: Id;
  code: string;
  name: string;
  scope_type: string;
  scope_id: Id | null;
};

export type AssignRolesOut = {
  user_id: Id;
  roles: RoleBrief[];
  granted_permissions: string[];
};

// ---------------------------------------------------------------- 角色 / 权限

export type RoleItem = {
  id: Id;
  code: string;
  name: string;
  description: string | null;
  is_system: boolean;
  sort_no: number;
  /** false（如 super_admin）前端应直接置灰并说明原因，而不是等提交后吃 40003 */
  is_assignable: boolean;
  permissions: string[];
};

export type PermissionNode = {
  /** 树节点唯一键：`m:<module>` 或 `p:<id>` */
  key: string;
  code: string;
  name: string;
  type: string;
  module: string;
  sort_no: number;
  children: PermissionNode[];
};

export type PermissionTreeOut = {
  total: number;
  tree: PermissionNode[];
};

// ---------------------------------------------------------------- 审计

export type AuditLogItem = {
  id: Id;
  actor_id: Id | null;
  actor_name: string | null;
  action: string;
  module: string;
  entity_type: string | null;
  entity_id: Id | null;
  method: string | null;
  path: string | null;
  ip: string | null;
  success: boolean;
  error_msg: string | null;
  before_data: unknown | null;
  after_data: unknown | null;
  created_at: string | null;
};

// ---------------------------------------------------------------- 查询参数

export type ListUsersQuery = {
  page: number;
  page_size: number;
  keyword?: string;
  status?: string;
};

export type ListAuditLogsQuery = {
  page: number;
  page_size: number;
  actor_id?: string;
  action?: string;
  entity_type?: string;
  entity_id?: string;
  success?: boolean;
  start?: string;
  end?: string;
  order?: "asc" | "desc";
};

// ---------------------------------------------------------------- 题库（Batch 4）

/** 全部题型。**读接口会返回全部 7 种**，写接口只接受前 3 种。 */
export type QType = "single" | "multiple" | "judge" | "case" | "case_sub" | "fill" | "essay";

/**
 * 本批可编辑的题型。
 * 案例题（case/case_sub）与主观题（fill/essay）**能看能筛不能改** ——
 * 涉及背景材料、子问切分、评分点，是另一个量级的工作量。
 */
export type EditableQType = "single" | "multiple" | "judge";

export type QStatus = "draft" | "reviewing" | "published" | "rejected" | "archived";

export type SourceType = "self" | "authorized" | "public" | "user_import" | "ai_assisted";

export type QuestionOptionIn = {
  label: string;
  content: string;
  content_html?: string | null;
  /** 答案由本字段推导 —— 后端没有"单独传答案"的入口 */
  is_correct: boolean;
};

export type QuestionCreateIn = {
  /**
   * 科目 / 章节 ID。
   *
   * 同样用**字符串**承载（见 `QuestionBatchDeleteIn` 的说明）：虽然后端声明为 `int`，
   * 但 Pydantic 会把数字字符串松散转成 Python int，所以传字符串既精确又不改后端契约。
   * 种子数据里科目/章节是 1001 / 1101 这类小整数，用 number 目前也不会出错 ——
   * 但"ID 一律字符串"是这项目的铁律，留一个例外就是给下一个人挖坑。
   */
  subject_id: string;
  chapter_id?: string | null;
  type: EditableQType;
  stem: string;
  stem_html?: string | null;
  /** 判断题必填；其余题型必须为空 */
  judge_answer?: boolean | null;
  analysis?: string | null;
  analysis_html?: string | null;
  difficulty: number;
  score_default: number;
  status: QStatus;
  exam_year?: number | null;
  keywords?: string | null;
  tags: string[];
  source_type: SourceType;
  source_name?: string | null;
  source_license?: string | null;
  copyright_holder?: string | null;
  options: QuestionOptionIn[];
};

/**
 * 编辑入参。
 *
 * **只回传变了的字段**（`patch` 语义）—— 后端对 `None/undefined` 一律解释为"沿用库里现值"。
 * 好处有二：一是不会把没动过的字段写进 `content_change_logs` 的 diff 里（diff 保持可读），
 * 二是两个教研同时改同一道题、改的是不同字段时，不会互相覆盖对方的改动。
 */
export type QuestionUpdateIn = Partial<QuestionCreateIn> & { version: number };

/**
 * 批量删除入参。
 *
 * ⚠️ **`ids` 必须是字符串，不能是 `number`。**
 *
 * 题目 ID 是雪花 ID（18~19 位），超出 JS 安全整数范围。后端虽然声明成 `list[int]`，
 * 但 Pydantic 会把**数字字符串**松散地转成 Python int（Python 的 int 是任意精度），
 * 所以传 `"375273861765140480"` 是**精确**的；传 `375273861765140480` 这个
 * JS number 则在 `JSON.stringify` 之前就已经被舍入成 `375273861765140500`，
 * 到后端变成一个查不到的 ID —— 接口返回 `code:0, deleted:0, skipped:[...]`，
 * **不报错、静默失败**（Batch 4 验收时实测踩到，见 `B端联调坑.md` 第 21 条）。
 *
 * 结论：ID 在这个项目里**永远是字符串**，从响应体到请求体一路如此，不做数字转换。
 */
export type QuestionBatchDeleteIn = { ids: string[]; reason?: string | null };

export type QuestionOptionOut = {
  id: Id;
  label: string;
  content: string;
  content_html: string | null;
  is_correct: boolean;
  sort_no: number;
};

export type QuestionListItem = {
  id: Id;
  subject_id: Id;
  subject_name: string | null;
  chapter_id: Id | null;
  chapter_name: string | null;
  type: QType;
  /** 已按 160 字截断（后端 STEM_PREVIEW_LEN） */
  stem: string;
  difficulty: number;
  score_default: number;
  status: QStatus;
  version: number;
  is_deleted: boolean;
  option_count: number;
  /** 列表里就能一眼看到答案，不用点进详情 */
  correct_labels: string[];
  keywords: string | null;
  created_by_name: string | null;
  updated_by_name: string | null;
  updated_at: string | null;
  created_at: string | null;
};

/** `question_versions.snapshot` 的结构（只读展示用，字段可能缺）。 */
export type QuestionSnapshot = {
  stem?: string;
  stem_html?: string | null;
  analysis?: string | null;
  analysis_html?: string | null;
  type?: QType;
  difficulty?: number;
  status?: QStatus;
  answer?: { value?: unknown[]; partial_credit?: boolean };
  options?: { label: string; content: string; is_correct: boolean; sort_no?: number }[];
  [k: string]: unknown;
};

export type QuestionVersionItem = {
  id: Id;
  version: number;
  change_log: string | null;
  operator_id: Id | null;
  operator_name: string | null;
  is_current: boolean;
  snapshot: QuestionSnapshot;
  created_at: string | null;
};

export type QuestionDetail = {
  id: Id;
  subject_id: Id;
  subject_name: string | null;
  chapter_id: Id | null;
  chapter_name: string | null;
  knowledge_point_id: Id | null;
  type: QType;
  stem: string;
  stem_html: string | null;
  analysis: string | null;
  analysis_html: string | null;
  answer: { value?: unknown[]; partial_credit?: boolean; [k: string]: unknown };
  options: QuestionOptionOut[];
  correct_labels: string[];
  difficulty: number;
  score_default: number;
  status: QStatus;
  version: number;
  is_deleted: boolean;
  exam_year: number | null;
  keywords: string | null;
  tags: string[];
  source_type: SourceType;
  source_name: string | null;
  source_license: string | null;
  copyright_holder: string | null;
  content_hash: string | null;
  created_by: Id | null;
  created_by_name: string | null;
  updated_by: Id | null;
  updated_by_name: string | null;
  published_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** 调用者是否有 question:update */
  can_edit: boolean;
  /** 调用者是否有 question:delete */
  can_delete: boolean;
  /** 该题型本批是否可编辑（案例题/主观题为 false） */
  editable: boolean;
  /** 历史版本，最新在前，最多 10 条，**只读**（本批不做回滚） */
  versions: QuestionVersionItem[];
};

export type QuestionDeleteOut = { id: Id; is_deleted: boolean; version: number };

export type QuestionBatchDeleteOut = {
  deleted: number;
  /** 不存在 / 已是删除态而被跳过的 id */
  skipped: Id[];
  batch_id: Id;
};

// ---------------------------------------------------------------- 章节树

export type SubjectBrief = {
  id: Id;
  code: string;
  name: string;
  short_name: string | null;
  professional: string | null;
};

export type ChapterNode = {
  id: Id;
  subject_id: Id;
  parent_id: Id | null;
  code: string;
  name: string;
  level: number;
  sort_no: number;
  /** 实时统计（不含软删除），不是 chapters.question_count 那个冗余列 */
  question_count: number;
  children: ChapterNode[];
};

export type SubjectChapterGroup = { subject: SubjectBrief; chapters: ChapterNode[] };

export type ChapterTreeOut = {
  /** 请求时传了 subject_id 才有值 */
  subject_id: Id | null;
  total: number;
  items: SubjectChapterGroup[];
};

export type ListQuestionsQuery = {
  page: number;
  page_size: number;
  /** 用字符串传（ID 是雪花 ID 的字符串形式），后端按 int 解析 */
  subject_id?: string;
  chapter_id?: string;
  type?: QType;
  difficulty?: number;
  status?: QStatus;
  keyword?: string;
  /** 「显示已归档」开关：true 才把软删除的题带出来 */
  include_deleted?: boolean;
  order_by?: "updated_at" | "created_at" | "difficulty" | "id";
  order?: "asc" | "desc";
};
