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
  /**
   * 知识点（Batch 7 Pass 2 补）。组卷「加题」要按知识点挑题，
   * 而且**筛完得能看出这道题挂在哪个知识点上**，否则筛选形同虚设。
   */
  knowledge_point_id: Id | null;
  knowledge_point_name: string | null;
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

/** 知识点下拉项（Batch 7 Pass 2 补，供组卷「加题」面板按知识点筛题）。 */
export type KnowledgePointItem = {
  id: Id;
  subject_id: Id;
  chapter_id: Id;
  chapter_name: string | null;
  code: string;
  name: string;
  /** 1 低 / 2 中 / 3 高频 */
  importance: number;
  /** 该知识点下的题目数（实时统计，不含软删除） */
  question_count: number;
};

export type KnowledgePointListOut = {
  items: KnowledgePointItem[];
};

export type ListKnowledgePointsQuery = {
  subject_id?: Id;
  chapter_id?: Id;
  keyword?: string;
};

export type ListQuestionsQuery = {
  page: number;
  page_size: number;
  /** 用字符串传（ID 是雪花 ID 的字符串形式），后端按 int 解析 */
  subject_id?: string;
  chapter_id?: string;
  /** Batch 7 Pass 2 补：组卷「加题」按知识点挑题 */
  knowledge_point_id?: string;
  type?: QType;
  difficulty?: number;
  status?: QStatus;
  keyword?: string;
  /** 「显示已归档」开关：true 才把软删除的题带出来 */
  include_deleted?: boolean;
  order_by?: "updated_at" | "created_at" | "difficulty" | "id";
  order?: "asc" | "desc";
};

// ---------------------------------------------------------------- 题库导入（Batch 5 后端 / Batch 6 前端）

/**
 * 批次状态。**与后端 `ImportStatus` 枚举逐一对应**（`app/schemas/admin_import.py`）。
 *
 * ⚠️ 注意这里**没有** "validated" 这个值 —— 校验完成的批次也是 `done`。
 * 「待执行」与「已执行」靠 `can_execute`（服务端按 `content_change_logs` 判定）区分，
 * 前端不要自己编一个状态出来，否则状态流转图会画出后端永远给不出的节点。
 */
export type ImportStatus =
  | "pending"
  | "parsing"
  | "validating"
  | "importing"
  | "done"
  | "failed"
  | "rolled_back";

/** 命中同内容题时的策略：跳过（insert）或更新（upsert）。 */
export type ImportMode = "insert" | "upsert";
export type ImportFileType = "csv" | "json";

/** 单行单字段的错误。`row_no` 是**文件里的数据行号**（从 1 起，不含表头）。 */
export type RowError = {
  row_no: number;
  field: string;
  message: string;
};

export type ErrorReport = {
  /** 完整计数（`errors` 可能被截断到 200 条） */
  total_errors: number;
  truncated: boolean;
  errors: RowError[];
};

/** 逐行结果里的 `action`：insert / update / duplicate / skip / error。 */
export type ImportRowAction = "insert" | "update" | "duplicate" | "skip" | "error";

export type ImportRowPreview = {
  row_no: number;
  action: string;
  message: string | null;
  question_id: Id | null;
};

/** 批次状态 + 统计。列表 / 详情 / 校验 / 执行 / 发布 / 回滚**共用**这个形状。 */
export type ImportBatch = {
  id: Id;
  batch_no: string;
  file_name: string;
  file_type: string;
  file_hash: string;
  file_size: number | null;
  subject_id: Id | null;
  subject_code: string | null;
  subject_name: string | null;
  source_type: SourceType;
  license_note: string | null;
  mode: string;
  status: ImportStatus;
  auto_publish: boolean;

  total_rows: number;
  success_rows: number;
  failed_rows: number;
  duplicate_rows: number;
  updated_rows: number;

  error_report: ErrorReport;

  operator_id: Id | null;
  operator_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  rollback_at: string | null;
  rollback_by: Id | null;
  created_at: string | null;

  /** 服务端算好的按钮可用性 —— **与路由权限门严格一致**，前端不要自己再推一遍 */
  can_execute: boolean;
  can_publish: boolean;
  can_rollback: boolean;
};

export type ImportBatchDetail = ImportBatch & {
  rows: ImportRowPreview[];
  row_page: number;
  row_page_size: number;
  row_total: number;
};

export type ImportUploadOut = {
  id: Id;
  batch_no: string;
  file_name: string;
  file_type: string;
  total_rows: number;
  status: string;
  message: string;
};

export type ImportExecuteOut = {
  id: Id;
  status: string;
  total_rows: number;
  success_rows: number;
  failed_rows: number;
  duplicate_rows: number;
  updated_rows: number;
  duration_ms: number;
};

export type ImportRollbackOut = {
  id: Id;
  status: string;
  rolled_back_questions: number;
  rolled_back_updates: number;
  missing: Id[];
};

/** 批次变更日志的一条（`content_change_logs`）。 */
export type ImportChangeItem = {
  id: Id;
  entity_type: string;
  entity_id: Id;
  action: string;
  change_log: string | null;
  /** 题干预览（后端已截断到 120 字） */
  question_stem: string | null;
  operator_id: Id | null;
  operator_name: string | null;
  created_at: string | null;
};

export type ImportChangeOut = {
  id: Id;
  batch_no: string;
  /** 全量条数（不随分页变化） */
  total: number;
  /** 全量按 action 汇总：`{create: 3, rollback: 3}` */
  counts: Record<string, number>;
  page: number;
  page_size: number;
  has_more: boolean;
  items: ImportChangeItem[];
};

export type ListImportsQuery = {
  page: number;
  page_size: number;
  status?: ImportStatus;
};

// ---------------------------------------------------------------- 组卷 / 试卷（Batch 7）

/** `exams.type`。与后端 `ExamType` 字面量一一对应。 */
export type ExamType = "real" | "mock" | "chapter_test" | "sprint" | "daily" | "custom";

/** `exams.status`。注意 `archived` 是**状态**，与 `is_deleted`（归档位）不是一回事。 */
export type ExamStatus = "draft" | "reviewing" | "published" | "off" | "archived";

/** `paper_rules.strategy`。本批后端只实现了 `random` 的语义，其余是占位。 */
export type RuleStrategy = "random" | "weak_first" | "coverage" | "history_similar";

export type RuleStatus = "on" | "off";

/** 组卷规则里的一条抽题约束。 */
export type RuleItem = {
  type: QType;
  count: number;
  score: number;
  /** `[min, max]` 难度区间，闭区间。 */
  difficulty: number[] | null;
  kp_ids: Id[];
  chapter_ids: Id[];
  year: number | null;
  prefer_unused: boolean;
};

/** 组卷缺口。**绝不静默补题**，缺多少如实报出来。 */
export type Shortfall = {
  rule: RuleItem;
  /** 规则的中文摘要，前端直接展示，不用自己拼 */
  rule_label: string;
  /** 该规则在 `rules` 数组里的下标（从 0 起） */
  rule_index: number;
  question_type: QType;
  need: number;
  got: number;
  missing: number;
  reason: string;
};

export type PaperRuleOut = {
  id: Id;
  name: string;
  subject_id: Id;
  subject_name: string | null;
  type: ExamType;
  duration_min: number;
  rules: RuleItem[];
  strategy: RuleStrategy;
  status: RuleStatus;
  planned_count: number;
  planned_score: number;
  created_by: Id | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  can_delete: boolean;
};

export type ListPaperRulesQuery = {
  page: number;
  page_size: number;
  subject_id?: Id;
  status?: RuleStatus;
  type?: ExamType;
};

/** 规则试算（dry-run，不写库）。 */
export type PaperRulePreviewIn = {
  subject_id: Id;
  rules: RuleItem[];
  strategy?: RuleStrategy;
  /** 传了就固定抽样结果（同一 seed + 同一题库 → 同一结果），方便对比调参 */
  seed?: number | null;
  include_sample?: boolean;
  sample_limit?: number;
};

export type PreviewQuestionItem = {
  question_id: Id;
  question_type: QType;
  stem_preview: string;
  difficulty: number | null;
  chapter_id: Id | null;
  score: number;
  rule_index: number;
  rule_label: string;
};

export type RulePreviewItem = {
  rule_index: number;
  rule_label: string;
  question_type: QType;
  need: number;
  got: number;
  missing: number;
  score: number;
  /** 放宽阶梯每一档的候选数 —— 用来解释"为什么抽不到" */
  stage_counts: Record<string, number>;
};

export type PaperRulePreviewOut = {
  ok: boolean;
  subject_id: Id;
  total_need: number;
  total_got: number;
  total_missing: number;
  /** 按实际抽到的题算 */
  total_score: number;
  /** 按理论抽满算（题库充足时的满分） */
  planned_score: number;
  items: RulePreviewItem[];
  shortfalls: Shortfall[];
  sample: PreviewQuestionItem[];
  duration_ms: number;
  message: string;
};

export type ExamSectionIn = {
  name: string;
  question_type: QType;
  question_count: number;
  score_per: number;
  sort_no: number;
};

export type ExamSectionOut = {
  id: Id;
  seq: number;
  name: string;
  question_type: QType;
  /** **计划**题数（卷面契约） */
  question_count: number;
  score_per: number;
  section_score: number;
  sort_no: number;
  /** **实际**入卷题数 */
  actual_count: number;
  actual_score: number;
};

export type ExamSectionDetail = ExamSectionOut & {
  questions: ExamQuestionItem[];
};

export type ExamQuestionItem = {
  /** ⚠️ `exam_questions.id`（**卷面行 id**），移题用它，**不是** `question_id` */
  id: Id;
  question_id: Id;
  section_id: Id | null;
  seq: number;
  score: number;
  question_type: QType;
  stem_preview: string;
  difficulty: number | null;
  chapter_id: Id | null;
  /** 发布时锁定的版本；未发布为 null */
  locked_version: number | null;
  current_version: number | null;
  /** 锁定版本 ≠ 当前版本（题目被改过） */
  version_drift: boolean;
};

export type ExamValidateIssue = {
  level: "error" | "warning";
  code: string;
  message: string;
};

export type ExamValidateOut = {
  exam_id: Id;
  /** 无 error 级问题才为 true；发布接口要求 true */
  ok: boolean;
  errors: ExamValidateIssue[];
  warnings: ExamValidateIssue[];
  question_count: number;
  total_score: number;
  checked_at: string;
};

export type ExamCreateIn = {
  subject_id: Id;
  title: string;
  type: ExamType;
  professional?: string | null;
  exam_year?: number | null;
  paper_no?: string | null;
  duration_min: number;
  pass_score: number;
  intro_html?: string | null;
  is_free: boolean;
  sections: ExamSectionIn[];
};

/**
 * 编辑试卷的**元数据**。
 *
 * ⚠️ **刻意不含 `sections`** —— 后端已经从 `PUT /admin/exams/{id}` 上把它剥离了：
 * 它一旦被传入就会 `DELETE FROM exam_questions`（整卷题目清空）。
 * 类型上少一个字段，就少一次"把详情对象原样回传"的机会（坑 42）。
 *
 * 改卷面结构用 `ExamSectionsReplaceIn` → `PUT /admin/exams/{id}/sections`。
 */
export type ExamUpdateIn = Partial<Omit<ExamCreateIn, "subject_id" | "sections">>;

/** 重建卷面结构（唯一入口）。会清空卷面题目，所以必须给 `expected_question_count`。 */
export type ExamSectionsReplaceIn = {
  sections: ExamSectionIn[];
  /** 调用方读到的**当前卷面题数**（详情里的 `question_count`）。对不上就 40901。 */
  expected_question_count: number;
};

export type ExamSectionsReplaceOut = {
  exam_id: Id;
  /** 重建前卷面有多少道题（题目本身不受影响） */
  removed_questions: number;
  question_count: number;
  total_score: number;
  sections: ExamSectionOut[];
  message: string;
};

export type ExamListItem = {
  id: Id;
  subject_id: Id;
  subject_name: string | null;
  title: string;
  type: ExamType;
  status: ExamStatus;
  exam_year: number | null;
  paper_no: string | null;
  question_count: number;
  total_score: number;
  pass_score: number;
  duration_min: number;
  difficulty: number;
  has_subjective: boolean;
  is_free: boolean;
  is_deleted: boolean;
  published_at: string | null;
  updated_at: string;
  created_by: Id | null;
  created_by_name: string | null;
};

export type ExamDetail = ExamListItem & {
  professional: string | null;
  intro_html: string | null;
  sections: ExamSectionDetail[];
  /** 上次组卷留下的缺口。空数组 = 没缺口或还没组过卷 */
  shortfalls: Shortfall[];
  validation: ExamValidateOut | null;
  created_at: string;
  can_edit: boolean;
  can_compose: boolean;
  can_publish: boolean;
  can_unpublish: boolean;
  can_delete: boolean;
};

export type ListExamsQuery = {
  page: number;
  page_size: number;
  subject_id?: Id;
  type?: ExamType;
  status?: ExamStatus;
  keyword?: string;
  /** 「显示已归档」—— 只有 true 时才传 */
  include_deleted?: boolean;
  order_by?: "updated_at" | "created_at" | "published_at" | "id";
  order?: "asc" | "desc";
};

export type ExamComposeIn = {
  rule_id?: Id | null;
  rules?: RuleItem[] | null;
  subject_id?: Id | null;
  seed?: number | null;
  replace?: boolean;
  /** 组卷后把分段数字改成实际抽到的数量 */
  apply_sections?: boolean;
};

export type ExamComposeOut = {
  exam_id: Id;
  status: ExamStatus;
  question_count: number;
  total_score: number;
  duration_min: number;
  sections: ExamSectionOut[];
  shortfalls: Shortfall[];
  message: string;
  elapsed_ms: number;
};

export type ExamPublishIn = {
  allow_edit_after_publish: boolean;
};

export type ExamPublishOut = {
  exam_id: Id;
  status: ExamStatus;
  published_at: string;
  question_count: number;
  total_score: number;
  locked_versions: number;
  message: string;
};

export type ExamSoftDeleteOut = {
  id: Id;
  title: string;
  previous_status: ExamStatus;
  is_deleted: boolean;
  question_count: number;
  message: string;
};

export type ExamRestoreOut = {
  id: Id;
  title: string;
  is_deleted: boolean;
  status: ExamStatus;
  /** 本来就是未归档状态（走了幂等分支，没有产生任何写入） */
  already_active: boolean;
  message: string;
};

export type ExamAddQuestionsIn = {
  /** ⚠️ 字符串数组，**不要** `Number()`（雪花 ID 精度，见 lib/json-bigint.ts） */
  question_ids: Id[];
  section_id?: Id | null;
};

export type SkippedQuestion = {
  question_id: Id;
  reason: string;
};

export type ExamAddQuestionsOut = {
  exam_id: Id;
  added: number;
  skipped: SkippedQuestion[];
  question_count: number;
  total_score: number;
  sections: ExamSectionOut[];
  message: string;
};

export type ExamRemoveQuestionOut = {
  exam_id: Id;
  exam_question_id: Id;
  question_id: Id;
  question_count: number;
  total_score: number;
  sections: ExamSectionOut[];
  message: string;
};

