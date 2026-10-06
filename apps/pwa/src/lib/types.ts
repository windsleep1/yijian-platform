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

/* ============================================================ P2b-1 · 刷题 */

/**
 * `GET /subjects/{id}/chapters` 的一项（`schemas/c_end.py` 的 `ChapterOut`）。
 *
 * ★ `question_count` / `my_answered` / `my_correct` 都是**含子章节**的实时值 ——
 *   后端**没有**读 `chapters.question_count` 那个冗余列（它写着"定时刷新"、实际没人刷）。
 *   所以这三个数字可以放心显示，不需要前端再汇总。
 */
export type Chapter = {
  id: string;
  parent_id: string | null;
  code: string;
  name: string;
  level: number;
  outline_ref: string | null;
  weight: number;
  sort_no: number;
  question_count: number;
  my_answered: number;
  my_correct: number;
};

/** 题目的一个选项。**不含 `is_correct`** —— 后端不给答案（给了就等于把答案发到浏览器）。 */
export type QuestionOption = {
  label: string;
  content: string;
  content_html: string | null;
};

/** 一次练习里的一道题（`schemas/c_end.py` 的 `SessionItemOut`）。 */
export type SessionItem = {
  item_id: string;
  seq: number;
  question_id: string;
  /** `single` 单选 / `multiple` 多选 / `judge` 判断。 */
  type: string;
  stem: string;
  stem_html: string | null;
  options: QuestionOption[];
  my_value: unknown[] | null;
  is_correct: boolean | null;
  score: number | null;
  answered: boolean;
  /**
   * ★ 用户级（**题目级**）状态：来自 `question_marks` / `favorites`。
   * ⚠️ **不是** `practice_items.marked`（那张表有 `session_id` ⇒ 是「这次练习的卷面标记」，
   *    且全仓没有任何接口读写它）⇒ 别在这里换成 `practice_items` 的语义。
   */
  marked: boolean;
  favorited: boolean;
  /** ⚠️ **未作答时恒为 null**（后端逐题判断可见性）。不要在前端"兜底"成 `{}`。 */
  answer: { value: unknown[] } | null;
  analysis: string | null;
  analysis_html: string | null;
  /** ★ 这道题下我写了几条笔记（`notes` 未软删）—— 答题页「✎ 笔记（N）」的徽标用它。 */
  note_count: number;
};

/** 结果页的一个知识点条（`schemas/c_end.py` 的 `KpStatOut`）。 */
export type KpStat = {
  knowledge_point_id: string | null;
  name: string;
  total: number;
  correct: number;
  /** ⚠️ 可空 —— 后端「零分母返 null」。前端必须显示「—」而不是「0%」。 */
  accuracy: number | null;
};

/**
 * `GET /practice/sessions/{id}/report`（`schemas/c_end.py` 的 `SessionReportOut`）。
 *
 * ★ 与 `PracticeSession` 的关键区别：**不返 `items`** —— 报告要的是「这次练得怎么样」（聚合），
 *   不是「每道题的解析」（那是 `PracticeSession` 的事）。
 */
export type SessionReport = {
  id: string;
  mode: string;
  /** `doing` / `finished`。★ 报告在 `doing` 时也看得到（读路径不设更严的准入）。 */
  status: string;
  title: string;
  subject_id: string | null;
  subject_name: string | null;
  chapter_id: string | null;
  chapter_name: string | null;
  total: number;
  answered: number;
  correct: number;
  score: number;
  /** ⚠️ **可空**：一道题都没答时后端返 null（0/0 不是 0%）。 */
  accuracy: number | null;
  duration_sec: number;
  started_at: string | null;
  finished_at: string | null;
  /** 按正确率**升序**（最弱的在前）—— 后端已排好，前端不要重排。 */
  by_kp: KpStat[];
};

/** 错题本的科目分面（**带条数**，且是**当前筛选口径下**的分布）。 */
export type WrongSubject = {
  subject_id: string;
  name: string;
  count: number;
};

/** 错题本的一行（`schemas/c_end.py` 的 `WrongItemOut`）。**不含选项与正确答案**。 */
export type WrongItem = {
  question_id: string;
  subject_id: string | null;
  subject_name: string | null;
  chapter_name: string | null;
  type: string;
  stem: string;
  /** 错了几次（每次都 +1）。 */
  wrong_count: number;
  /** **重练**答对过几次（`mode='wrong'` 里答对才 +1；章节练习里答对**不算**）。 */
  retry_correct: number;
  mastered_level: number;
  last_wrong_at: string | null;
  /** ★ 我标记过它吗（`question_marks`）—— 错题本「已标记」筛选用它。 */
  marked: boolean;
  /**
   * ★★ 约定 T：题目下架后这一行**仍然在**（列表不再过滤），这里为 `false`。
   * 前端据此标「题目已下架」并**禁用重练** —— 重练要建 session，而建 session 的题源
   * **会**过滤掉下架的题 ⇒ 点了只会拿到一个 `40401`。
   */
  question_available: boolean;
};

/** 收藏 / 标记列表的一行（`schemas/c_end.py` 的 `CollectionItemOut`）。
 *  ★ 形状与 `WrongItem` 刻意一致 ⇒ 列表页那一套（分面 / 空态 / 分页）**直接复用**。 */
export type CollectionItem = {
  question_id: string;
  subject_id: string | null;
  subject_name: string | null;
  chapter_name: string | null;
  type: string;
  stem: string;
  stem_html: string | null;
  /** 进这个列表的时间（收藏时间 / 标记时间）—— 后端按它倒序。 */
  collected_at: string;
  marked: boolean;
  favorited: boolean;
  /** ★★ 约定 T：题目下架后这一行**仍然在**，这里为 `false`（标「题目已下架」，取消按钮仍可用）。 */
  question_available: boolean;
  /**
   * ★ 这道题在我的**错题本**里吗 —— 决定这一行**能不能链到** `/practice/wrong/{qid}`。
   * 那个页面**要求错题本里有这道题**（没有 ⇒ 404），而「收藏了但从没错过」很常见
   * ⇒ 原来每行都渲染成链接，等于一半的点开是报错页（批次 2 遗留，本批修）。
   */
  in_wrong_book: boolean;
};

/** `GET /practice/favorites?kind=`（`schemas/c_end.py` 的 `CollectionListOut`）。 */
export type CollectionList = {
  /** 回显请求的那一种（两个页签据此高亮）。 */
  kind: "favorite" | "mark";
  total: number;
  page: number;
  page_size: number;
  /** ★ 分面**恒为全量**（不随筛选收缩）—— 与错题本同一条判据。 */
  subjects: WrongSubject[];
  items: CollectionItem[];
};

/** 一条笔记（`schemas/c_end.py` 的 `NoteOut`）。 */
export type Note = {
  id: string;
  question_id: string;
  content: string;
  /** ★★ 约定 T：题目下架后这条笔记**照样给**，这里为 `false`（标「题目已下架」）。 */
  question_available: boolean;
  created_at: string;
  /** ★ 由触发器 `trg_notes_updated` 维护 —— 前端**只读**，不要本地推算。 */
  updated_at: string;
};

/** 笔记列表的一行（`NoteListItemOut`）：比 `Note` 多「这道题长什么样」。 */
export type NoteListItem = Note & {
  subject_id: string | null;
  subject_name: string | null;
  chapter_name: string | null;
  type: string | null;
  stem: string | null;
};

/** `GET /practice/notes`（`NoteListOut`）。 */
export type NoteList = {
  total: number;
  page: number;
  page_size: number;
  /** ★ 分面**恒为全量**（与错题本 / 收藏同一条判据）。 */
  subjects: WrongSubject[];
  items: NoteListItem[];
};

/** `GET /practice/questions/{qid}/notes`（`NoteListOfQuestionOut`）。**不分页**。 */
export type NoteListOfQuestion = {
  items: Note[];
};

/** 标记 / 收藏写入后的**两个**状态（`schemas/c_end.py` 的 `FlagOut`）。 */
export type FlagState = {
  question_id: string;
  marked: boolean;
  favorited: boolean;
};

/** `GET /practice/wrong-questions`（`schemas/c_end.py` 的 `WrongListOut`）。 */
export type WrongList = {
  total: number;
  page: number;
  page_size: number;
  subjects: WrongSubject[];
  items: WrongItem[];
};

/**
 * `GET /practice/wrong-questions/{qid}`（`schemas/c_end.py` 的 `WrongDetailOut`）。
 *
 * ★ 与列表的关键区别：**含正确答案与解析**。理由是"你已经和这道题交过手了"——
 *   而这个能力的**前提**是后端那道"必须真的错过"的门（没错过的题返回 404）。
 */
export type WrongDetail = {
  question_id: string;
  subject_id: string | null;
  subject_name: string | null;
  chapter_name: string | null;
  type: string;
  stem: string;
  stem_html: string | null;
  options: QuestionOption[];
  answer: { value: unknown[] };
  analysis: string | null;
  analysis_html: string | null;
  wrong_count: number;
  retry_correct: number;
  mastered_level: number;
  reason_tag: string | null;
  last_wrong_at: string | null;
};

/** `GET /practice/sessions/{id}`（`schemas/c_end.py` 的 `SessionOut`）。 */
export type PracticeSession = {
  id: string;
  mode: string;
  /** `doing` = 断点恢复；`finished` = 报告（P2b-2 才有）。 */
  status: string;
  subject_id: string | null;
  subject_name: string | null;
  chapter_id: string | null;
  chapter_name: string | null;
  title: string;
  /** 第一道**还没作答**的题；全答完 = null。"刷新后还在"就靠它回到原处。 */
  current_item_id: string | null;
  total: number;
  answered: number;
  correct: number;
  score: number;
  items: SessionItem[];
};

export type SessionProgress = {
  total: number;
  answered: number;
  correct: number;
  score: number;
};

/** `POST /practice/sessions/{id}/answer`（`schemas/c_end.py` 的 `AnswerResultOut`）。 */
export type AnswerResult = {
  item_id: string;
  is_correct: boolean;
  score: number;
  correct_answer: { value: unknown[] };
  my_value: unknown[];
  analysis: string | null;
  analysis_html: string | null;
  /** `true` = 这道题**之前已答过**，后端零写入、返回的是既有结果。 */
  idempotent: boolean;
  session: SessionProgress;
};
