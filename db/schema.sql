-- =====================================================================
--  一建通 · 一级建造师学习备考平台  数据库结构
--  PostgreSQL 14+
--  约定：
--    1. 主键统一 BIGINT，由应用层雪花算法生成（数据库不自增），便于将来分库迁移
--    2. 时间统一 TIMESTAMPTZ，默认 now()
--    3. 枚举值用 VARCHAR + CHECK 约束（比原生 ENUM 更容易演进）
--    4. 所有业务表带 is_deleted 做软删除；题库是资产，不做物理删除
--    5. updated_at 由触发器自动维护
--
--  执行：psql -U yijian -d yijian -f schema.sql
-- =====================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 模糊搜索（后台管理端用）
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- ---------------------------------------------------------------------
-- 0. 通用：更新时间触发器
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =====================================================================
-- 1. 用户与权限
-- =====================================================================

CREATE TABLE users (
  id              BIGINT PRIMARY KEY,
  phone           VARCHAR(20),
  email           VARCHAR(128),
  username        VARCHAR(64),
  password_hash   VARCHAR(128),
  nickname        VARCHAR(64)  NOT NULL DEFAULT '',
  avatar_url      VARCHAR(512),
  real_name       VARCHAR(64),
  status          VARCHAR(16)  NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','disabled','locked','deleted')),
  register_source VARCHAR(32)  NOT NULL DEFAULT 'h5',
  register_ip     INET,
  last_login_at   TIMESTAMPTZ,
  last_login_ip   INET,
  login_count     INTEGER      NOT NULL DEFAULT 0,
  remark          VARCHAR(255),
  is_deleted      BOOLEAN      NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_users_phone    ON users(phone)    WHERE phone    IS NOT NULL AND is_deleted = false;
CREATE UNIQUE INDEX uq_users_email    ON users(email)    WHERE email    IS NOT NULL AND is_deleted = false;
CREATE UNIQUE INDEX uq_users_username ON users(username) WHERE username IS NOT NULL AND is_deleted = false;
CREATE INDEX idx_users_created_at ON users(created_at DESC);
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 第三方身份（微信/小程序/Apple）。一期可空置，二期启用
CREATE TABLE user_identities (
  id           BIGINT PRIMARY KEY,
  user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider     VARCHAR(24) NOT NULL CHECK (provider IN ('phone','wechat_mp','wechat_mini','wechat_open','apple','qq')),
  open_id      VARCHAR(128),
  union_id     VARCHAR(128),
  extra        JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_identity_provider_openid ON user_identities(provider, open_id) WHERE open_id IS NOT NULL;
CREATE INDEX idx_identity_user ON user_identities(user_id);
CREATE TRIGGER trg_user_identities_updated BEFORE UPDATE ON user_identities FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 刷新令牌（JWT Refresh Token 白名单，登出/踢线即删）
CREATE TABLE user_sessions (
  id              BIGINT PRIMARY KEY,
  user_id         BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_token   VARCHAR(128) NOT NULL,
  device_id       VARCHAR(128),
  device_name     VARCHAR(128),
  platform        VARCHAR(24),
  app_version     VARCHAR(24),
  ip              INET,
  user_agent      VARCHAR(512),
  expires_at      TIMESTAMPTZ NOT NULL,
  revoked_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_session_token ON user_sessions(refresh_token);
CREATE INDEX idx_session_user ON user_sessions(user_id, expires_at DESC);

-- 学习档案（与登录账号解耦，方便一人多考期/多专业）
CREATE TABLE user_profiles (
  user_id          BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  gender           VARCHAR(8),
  province         VARCHAR(32),
  city             VARCHAR(32),
  birth_year       SMALLINT,
  education        VARCHAR(32),
  major            VARCHAR(64),
  work_years       SMALLINT,
  company          VARCHAR(128),
  exam_level       VARCHAR(16) NOT NULL DEFAULT 'yijian' CHECK (exam_level IN ('yijian','erjian')),
  professional     VARCHAR(24),
  exam_year        SMALLINT,
  target_subjects  JSONB       NOT NULL DEFAULT '[]'::jsonb,
  target_score     SMALLINT,
  daily_goal_min   SMALLINT    NOT NULL DEFAULT 30,
  study_time_slots JSONB       NOT NULL DEFAULT '[]'::jsonb,
  onboarded_at     TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_user_profiles_updated BEFORE UPDATE ON user_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 角色
CREATE TABLE roles (
  id          BIGINT PRIMARY KEY,
  code        VARCHAR(32) NOT NULL,
  name        VARCHAR(64) NOT NULL,
  description VARCHAR(255),
  is_system   BOOLEAN     NOT NULL DEFAULT false,
  sort_no     INTEGER     NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_roles_code ON roles(code);
CREATE TRIGGER trg_roles_updated BEFORE UPDATE ON roles FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 权限（菜单/按钮/接口三级）
CREATE TABLE permissions (
  id         BIGINT PRIMARY KEY,
  code       VARCHAR(96) NOT NULL,
  name       VARCHAR(96) NOT NULL,
  type       VARCHAR(16) NOT NULL DEFAULT 'api' CHECK (type IN ('menu','page','button','api','data')),
  module     VARCHAR(48) NOT NULL DEFAULT '',
  parent_id  BIGINT,
  sort_no    INTEGER     NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_permissions_code ON permissions(code);
CREATE INDEX idx_permissions_module ON permissions(module);

CREATE TABLE role_permissions (
  role_id       BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_id)
);

-- 用户角色（带数据范围）
CREATE TABLE user_roles (
  id         BIGINT PRIMARY KEY,
  user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id    BIGINT      NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  scope_type VARCHAR(24) NOT NULL DEFAULT 'global'
             CHECK (scope_type IN ('global','subject','professional','course')),
  scope_id   BIGINT,
  granted_by BIGINT,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_user_role_scope ON user_roles(user_id, role_id, scope_type, COALESCE(scope_id, 0));
CREATE INDEX idx_user_roles_user ON user_roles(user_id);


-- =====================================================================
-- 2. 商品 / 订单 / 权益
-- =====================================================================

CREATE TABLE products (
  id             BIGINT PRIMARY KEY,
  code           VARCHAR(48) NOT NULL,
  name           VARCHAR(128) NOT NULL,
  subtitle       VARCHAR(255),
  type           VARCHAR(24)  NOT NULL CHECK (type IN ('course','subject','vip','package','paper_pack','grading')),
  scope_subjects JSONB        NOT NULL DEFAULT '[]'::jsonb,   -- 覆盖科目 id 列表
  scope_courses  JSONB        NOT NULL DEFAULT '[]'::jsonb,
  original_price_cents INTEGER NOT NULL DEFAULT 0,
  price_cents    INTEGER      NOT NULL DEFAULT 0,
  duration_days  INTEGER      NOT NULL DEFAULT 0,             -- 0 = 永久
  quota          JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- {"mock_exam":-1,"grading":10}
  cover_url      VARCHAR(512),
  description    TEXT,
  is_recommended BOOLEAN      NOT NULL DEFAULT false,
  sort_no        INTEGER      NOT NULL DEFAULT 0,
  status         VARCHAR(16)  NOT NULL DEFAULT 'off' CHECK (status IN ('on','off')),
  valid_from     DATE,
  valid_until    DATE,
  is_deleted     BOOLEAN      NOT NULL DEFAULT false,
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_products_code ON products(code) WHERE is_deleted = false;
CREATE TRIGGER trg_products_updated BEFORE UPDATE ON products FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE coupons (
  id              BIGINT PRIMARY KEY,
  code            VARCHAR(32) NOT NULL,
  name            VARCHAR(96) NOT NULL,
  type            VARCHAR(16) NOT NULL CHECK (type IN ('fixed','percent')),
  value           INTEGER     NOT NULL,        -- fixed: 分；percent: 百分比(如 80 = 8折)
  min_amount_cents INTEGER    NOT NULL DEFAULT 0,
  max_discount_cents INTEGER,
  scope           JSONB       NOT NULL DEFAULT '{}'::jsonb,
  total_quota     INTEGER     NOT NULL DEFAULT 0,
  used_count      INTEGER     NOT NULL DEFAULT 0,
  per_user_limit  SMALLINT    NOT NULL DEFAULT 1,
  start_at        TIMESTAMPTZ NOT NULL,
  end_at          TIMESTAMPTZ NOT NULL,
  status          VARCHAR(16) NOT NULL DEFAULT 'on' CHECK (status IN ('on','off')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_coupons_code ON coupons(code);

CREATE TABLE orders (
  id               BIGINT PRIMARY KEY,
  order_no         VARCHAR(40) NOT NULL,
  user_id          BIGINT      NOT NULL REFERENCES users(id),
  product_id       BIGINT      NOT NULL REFERENCES products(id),
  product_snapshot JSONB       NOT NULL,       -- 下单时快照，防商品改价影响历史
  quantity         SMALLINT    NOT NULL DEFAULT 1,
  original_cents   INTEGER     NOT NULL,
  discount_cents   INTEGER     NOT NULL DEFAULT 0,
  amount_cents     INTEGER     NOT NULL,
  coupon_id        BIGINT,
  pay_channel      VARCHAR(24) CHECK (pay_channel IN ('wechat','alipay','unionpay','manual')),
  status           VARCHAR(16) NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','paid','closed','refunding','refunded','failed')),
  paid_at          TIMESTAMPTZ,
  closed_at        TIMESTAMPTZ,
  expire_at        TIMESTAMPTZ,
  client_ip        INET,
  remark           VARCHAR(255),
  is_deleted       BOOLEAN     NOT NULL DEFAULT false,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_orders_no ON orders(order_no);
CREATE INDEX idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status, created_at DESC);
CREATE TRIGGER trg_orders_updated BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE payments (
  id           BIGINT PRIMARY KEY,
  order_id     BIGINT      NOT NULL REFERENCES orders(id),
  channel      VARCHAR(24) NOT NULL,
  trade_no     VARCHAR(96),                  -- 第三方流水号
  out_trade_no VARCHAR(64) NOT NULL,
  amount_cents INTEGER     NOT NULL,
  status       VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','success','failed','refunded')),
  notify_at    TIMESTAMPTZ,
  notify_raw   JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_payments_out_trade_no ON payments(out_trade_no);
CREATE INDEX idx_payments_order ON payments(order_id);
CREATE TRIGGER trg_payments_updated BEFORE UPDATE ON payments FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 权益：决定用户能看哪些课、做哪些题。所有鉴权最终落到这张表
CREATE TABLE entitlements (
  id              BIGINT PRIMARY KEY,
  user_id         BIGINT      NOT NULL REFERENCES users(id),
  product_id      BIGINT      REFERENCES products(id),
  order_id        BIGINT      REFERENCES orders(id),
  type            VARCHAR(24) NOT NULL CHECK (type IN ('course','subject','vip','package','grading')),
  scope_subjects  JSONB       NOT NULL DEFAULT '[]'::jsonb,
  scope_courses   JSONB       NOT NULL DEFAULT '[]'::jsonb,
  quota           JSONB       NOT NULL DEFAULT '{}'::jsonb,
  quota_used      JSONB       NOT NULL DEFAULT '{}'::jsonb,
  start_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  end_at          TIMESTAMPTZ,                -- NULL = 永久
  status          VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','revoked')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_entitlements_user ON entitlements(user_id, status, end_at);
CREATE TRIGGER trg_entitlements_updated BEFORE UPDATE ON entitlements FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =====================================================================
-- 3. 科目 / 章节 / 知识点
-- =====================================================================

CREATE TABLE subjects (
  id           BIGINT PRIMARY KEY,
  code         VARCHAR(32) NOT NULL,
  name         VARCHAR(96) NOT NULL,
  short_name   VARCHAR(32),
  exam_level   VARCHAR(16) NOT NULL DEFAULT 'yijian' CHECK (exam_level IN ('yijian','erjian')),
  category     VARCHAR(16) NOT NULL CHECK (category IN ('public','professional')),
  professional VARCHAR(24),                 -- 专业课的所属专业，公共课为 NULL
  full_score   SMALLINT    NOT NULL DEFAULT 0,
  pass_score   SMALLINT    NOT NULL DEFAULT 0,
  duration_min SMALLINT    NOT NULL DEFAULT 0,
  icon         VARCHAR(64),
  color        VARCHAR(16),
  description  TEXT,
  sort_no      INTEGER     NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'on' CHECK (status IN ('on','off')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_subjects_code ON subjects(code);
CREATE TRIGGER trg_subjects_updated BEFORE UPDATE ON subjects FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE chapters (
  id           BIGINT PRIMARY KEY,
  subject_id   BIGINT      NOT NULL REFERENCES subjects(id),
  parent_id    BIGINT      REFERENCES chapters(id),
  code         VARCHAR(48) NOT NULL,
  name         VARCHAR(160) NOT NULL,
  level        SMALLINT    NOT NULL DEFAULT 1,
  path         VARCHAR(255) NOT NULL DEFAULT '',   -- 物化路径 /1/12/135/，取子树用 LIKE
  outline_ref  VARCHAR(96),                        -- 对应考纲条目号
  weight       NUMERIC(5,2) NOT NULL DEFAULT 0,    -- 章节分值权重（%）
  question_count INTEGER   NOT NULL DEFAULT 0,     -- 冗余计数，定时刷新
  sort_no      INTEGER     NOT NULL DEFAULT 0,
  is_deleted   BOOLEAN     NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_chapters_subject_code ON chapters(subject_id, code) WHERE is_deleted = false;
CREATE INDEX idx_chapters_subject ON chapters(subject_id, sort_no);
CREATE INDEX idx_chapters_path ON chapters(path varchar_pattern_ops);
CREATE TRIGGER trg_chapters_updated BEFORE UPDATE ON chapters FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE knowledge_points (
  id          BIGINT PRIMARY KEY,
  subject_id  BIGINT      NOT NULL REFERENCES subjects(id),
  chapter_id  BIGINT      NOT NULL REFERENCES chapters(id),
  parent_id   BIGINT      REFERENCES knowledge_points(id),
  code        VARCHAR(64) NOT NULL,
  name        VARCHAR(255) NOT NULL,
  level       SMALLINT    NOT NULL DEFAULT 1,
  path        VARCHAR(255) NOT NULL DEFAULT '',
  importance  SMALLINT    NOT NULL DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),  -- 1低 2中 3高频
  description TEXT,
  question_count INTEGER  NOT NULL DEFAULT 0,
  sort_no     INTEGER     NOT NULL DEFAULT 0,
  is_deleted  BOOLEAN     NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_kp_subject_code ON knowledge_points(subject_id, code) WHERE is_deleted = false;
CREATE INDEX idx_kp_chapter ON knowledge_points(chapter_id, sort_no);
CREATE TRIGGER trg_kp_updated BEFORE UPDATE ON knowledge_points FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =====================================================================
-- 4. 课程 / 课时 / 学习行为
-- =====================================================================

CREATE TABLE courses (
  id            BIGINT PRIMARY KEY,
  subject_id    BIGINT      NOT NULL REFERENCES subjects(id),
  professional  VARCHAR(24),
  title         VARCHAR(200) NOT NULL,
  subtitle      VARCHAR(255),
  cover_url     VARCHAR(512),
  teacher_id    BIGINT      REFERENCES users(id),
  teacher_name  VARCHAR(64),
  type          VARCHAR(24) NOT NULL DEFAULT 'system' CHECK (type IN ('system','sprint','special','live','free')),
  exam_year     SMALLINT,
  description   TEXT,
  outline       JSONB,
  lesson_count  INTEGER     NOT NULL DEFAULT 0,
  total_minutes INTEGER     NOT NULL DEFAULT 0,
  is_free       BOOLEAN     NOT NULL DEFAULT false,
  status        VARCHAR(16) NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','reviewing','on','off','archived')),
  published_at  TIMESTAMPTZ,
  sort_no       INTEGER     NOT NULL DEFAULT 0,
  is_deleted    BOOLEAN     NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_courses_subject ON courses(subject_id, status, sort_no);
CREATE TRIGGER trg_courses_updated BEFORE UPDATE ON courses FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE lessons (
  id            BIGINT PRIMARY KEY,
  course_id     BIGINT      NOT NULL REFERENCES courses(id),
  chapter_id    BIGINT      REFERENCES chapters(id),
  title         VARCHAR(200) NOT NULL,
  type          VARCHAR(16) NOT NULL DEFAULT 'video' CHECK (type IN ('video','audio','article','pdf','live')),
  media_url     VARCHAR(512),
  hls_url       VARCHAR(512),
  cover_url     VARCHAR(512),
  content_html  TEXT,                          -- article 类型正文
  duration_sec  INTEGER     NOT NULL DEFAULT 0,
  size_bytes    BIGINT      NOT NULL DEFAULT 0,
  is_free       BOOLEAN     NOT NULL DEFAULT false,
  allow_download BOOLEAN    NOT NULL DEFAULT false,
  attachments   JSONB       NOT NULL DEFAULT '[]'::jsonb,
  sort_no       INTEGER     NOT NULL DEFAULT 0,
  status        VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','on','off')),
  is_deleted    BOOLEAN     NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_lessons_course ON lessons(course_id, sort_no);
CREATE INDEX idx_lessons_chapter ON lessons(chapter_id);
CREATE TRIGGER trg_lessons_updated BEFORE UPDATE ON lessons FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE lesson_progress (
  id            BIGINT PRIMARY KEY,
  user_id       BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  lesson_id     BIGINT      NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  course_id     BIGINT      NOT NULL,
  progress_sec  INTEGER     NOT NULL DEFAULT 0,
  progress_pct  SMALLINT    NOT NULL DEFAULT 0,
  finished      BOOLEAN     NOT NULL DEFAULT false,
  study_seconds INTEGER     NOT NULL DEFAULT 0,
  last_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_lesson_progress ON lesson_progress(user_id, lesson_id);
CREATE INDEX idx_lesson_progress_user ON lesson_progress(user_id, last_at DESC);
CREATE TRIGGER trg_lesson_progress_updated BEFORE UPDATE ON lesson_progress FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 笔记
CREATE TABLE notes (
  id                BIGINT PRIMARY KEY,
  user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type       VARCHAR(16) NOT NULL CHECK (target_type IN ('lesson','question','knowledge_point','chapter')),
  target_id         BIGINT      NOT NULL,
  subject_id        BIGINT,
  chapter_id        BIGINT,
  content           TEXT        NOT NULL,
  position_sec      INTEGER,                   -- 视频时间戳笔记
  is_public         BOOLEAN     NOT NULL DEFAULT false,
  like_count        INTEGER     NOT NULL DEFAULT 0,
  is_deleted        BOOLEAN     NOT NULL DEFAULT false,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notes_user ON notes(user_id, created_at DESC);
CREATE INDEX idx_notes_target ON notes(target_type, target_id);
CREATE INDEX idx_notes_search ON notes USING gin(to_tsvector('simple', content));
CREATE TRIGGER trg_notes_updated BEFORE UPDATE ON notes FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 划线（讲义文本标注）
CREATE TABLE highlights (
  id           BIGINT PRIMARY KEY,
  user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  lesson_id    BIGINT      NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  start_offset INTEGER     NOT NULL,
  end_offset   INTEGER     NOT NULL,
  text         VARCHAR(1000) NOT NULL,
  color        VARCHAR(16) NOT NULL DEFAULT 'yellow',
  note         VARCHAR(500),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_highlights_user_lesson ON highlights(user_id, lesson_id);

-- 统一收藏
CREATE TABLE favorites (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_type VARCHAR(16) NOT NULL CHECK (target_type IN ('lesson','question','course','article')),
  target_id   BIGINT      NOT NULL,
  subject_id  BIGINT,
  folder      VARCHAR(64) NOT NULL DEFAULT 'default',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_favorites ON favorites(user_id, target_type, target_id);
CREATE INDEX idx_favorites_user ON favorites(user_id, created_at DESC);

-- 评论 / 答疑
CREATE TABLE comments (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT      NOT NULL REFERENCES users(id),
  target_type VARCHAR(16) NOT NULL CHECK (target_type IN ('lesson','question','article','course')),
  target_id   BIGINT      NOT NULL,
  root_id     BIGINT,
  parent_id   BIGINT,
  content     TEXT        NOT NULL,
  like_count  INTEGER     NOT NULL DEFAULT 0,
  reply_count INTEGER     NOT NULL DEFAULT 0,
  is_top      BOOLEAN     NOT NULL DEFAULT false,
  status      VARCHAR(16) NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','approved','rejected','hidden')),
  audited_by  BIGINT,
  audited_at  TIMESTAMPTZ,
  is_deleted  BOOLEAN     NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_comments_target ON comments(target_type, target_id, created_at DESC);
CREATE TRIGGER trg_comments_updated BEFORE UPDATE ON comments FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 学习计划
CREATE TABLE study_plans (
  id           BIGINT PRIMARY KEY,
  user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  subject_id   BIGINT      REFERENCES subjects(id),
  title        VARCHAR(160) NOT NULL,
  start_date   DATE        NOT NULL,
  end_date     DATE        NOT NULL,
  daily_minutes SMALLINT   NOT NULL DEFAULT 30,
  target_score SMALLINT,
  strategy     VARCHAR(24) NOT NULL DEFAULT 'balanced',   -- balanced / intensive / weak_first
  progress_pct SMALLINT    NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done','expired')),
  generated_by VARCHAR(16) NOT NULL DEFAULT 'auto',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_study_plans_user ON study_plans(user_id, status);
CREATE TRIGGER trg_study_plans_updated BEFORE UPDATE ON study_plans FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE study_plan_items (
  id           BIGINT PRIMARY KEY,
  plan_id      BIGINT      NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
  user_id      BIGINT      NOT NULL,
  plan_date    DATE        NOT NULL,
  seq          SMALLINT    NOT NULL DEFAULT 0,
  item_type    VARCHAR(16) NOT NULL CHECK (item_type IN ('lesson','question','exam','review')),
  ref_id       BIGINT,
  title        VARCHAR(200) NOT NULL,
  est_minutes  SMALLINT    NOT NULL DEFAULT 0,
  done_ratio   SMALLINT    NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done','skipped')),
  done_at      TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_plan_items ON study_plan_items(user_id, plan_date, status);


-- =====================================================================
-- 5. 题库（核心）
-- =====================================================================

CREATE TABLE questions (
  id                 BIGINT PRIMARY KEY,
  subject_id         BIGINT      NOT NULL REFERENCES subjects(id),
  chapter_id         BIGINT      REFERENCES chapters(id),
  knowledge_point_id BIGINT      REFERENCES knowledge_points(id),

  type               VARCHAR(16) NOT NULL
                     CHECK (type IN ('single','multiple','judge','case','case_sub','fill','essay')),
  stem               TEXT        NOT NULL,          -- 纯文本题干（搜索用）
  stem_html          TEXT,                          -- 富文本题干（展示用）
  stem_media         JSONB       NOT NULL DEFAULT '[]'::jsonb,

  answer             JSONB       NOT NULL,          -- 见 docs/03 第 5.1 节结构定义
  analysis           TEXT,
  analysis_html      TEXT,
  analysis_points    JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- 主观题评分点
  options_snapshot   JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- 冗余快照，供搜索/导出

  score_default      NUMERIC(6,2) NOT NULL DEFAULT 1,
  difficulty         SMALLINT    NOT NULL DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),

  exam_year          SMALLINT,
  exam_session       VARCHAR(32),                   -- 如 "2024年9月"
  paper_no           VARCHAR(32),                   -- 如 "真题卷A"

  source_type        VARCHAR(16) NOT NULL DEFAULT 'self'
                     CHECK (source_type IN ('self','authorized','public','user_import','ai_assisted')),
  source_name        VARCHAR(160),
  source_license     VARCHAR(160),                  -- 授权凭证号/许可说明
  copyright_holder   VARCHAR(160),

  tags               TEXT[]      NOT NULL DEFAULT '{}',
  keywords           VARCHAR(255),

  -- 案例题：父题 -> 小问
  parent_id          BIGINT      REFERENCES questions(id),
  root_id            BIGINT,                        -- 案例大题根 id，普通题为自身
  sort_no            SMALLINT    NOT NULL DEFAULT 0,
  material_html      TEXT,                          -- 案例背景材料（挂在根题上）

  content_hash       CHAR(64)    NOT NULL,          -- 归一化内容指纹，用于去重
  status             VARCHAR(16) NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','reviewing','published','rejected','archived')),
  version            INTEGER     NOT NULL DEFAULT 1,
  quality_flag       VARCHAR(24),                   -- ok / too_easy / too_hard / suspicious

  is_deleted         BOOLEAN     NOT NULL DEFAULT false,
  created_by         BIGINT      REFERENCES users(id),
  updated_by         BIGINT      REFERENCES users(id),
  reviewed_by        BIGINT      REFERENCES users(id),
  reviewed_at        TIMESTAMPTZ,
  published_at       TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 去重唯一约束：同一内容指纹只允许一条有效题
CREATE UNIQUE INDEX uq_questions_hash ON questions(content_hash) WHERE is_deleted = false;
CREATE INDEX idx_questions_filter  ON questions(subject_id, chapter_id, type, difficulty, status) WHERE is_deleted = false;
CREATE INDEX idx_questions_kp      ON questions(knowledge_point_id) WHERE is_deleted = false;
CREATE INDEX idx_questions_year    ON questions(subject_id, exam_year) WHERE exam_year IS NOT NULL;
CREATE INDEX idx_questions_root    ON questions(root_id);
CREATE INDEX idx_questions_status  ON questions(status, created_at DESC);
CREATE INDEX idx_questions_tags    ON questions USING gin(tags);
CREATE INDEX idx_questions_stem_trgm ON questions USING gin(stem gin_trgm_ops);
CREATE TRIGGER trg_questions_updated BEFORE UPDATE ON questions FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE question_options (
  id          BIGINT PRIMARY KEY,
  question_id BIGINT      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  label       VARCHAR(8)  NOT NULL,        -- A/B/C/D/E
  content     VARCHAR(2000) NOT NULL,
  content_html VARCHAR(4000),
  is_correct  BOOLEAN     NOT NULL DEFAULT false,
  sort_no     SMALLINT    NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_question_option_label ON question_options(question_id, label);
CREATE INDEX idx_options_question ON question_options(question_id, sort_no);

-- 题目历史版本快照（每次发布/修改留档，支持回滚与历史卷面还原）
CREATE TABLE question_versions (
  id          BIGINT PRIMARY KEY,
  question_id BIGINT      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  version     INTEGER     NOT NULL,
  snapshot    JSONB       NOT NULL,        -- 完整题目 + 选项 JSON
  change_log  VARCHAR(500),
  operator_id BIGINT      REFERENCES users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_question_versions ON question_versions(question_id, version);

-- 全量变更日志（题目/章节/试卷/课程通用）
CREATE TABLE content_change_logs (
  id           BIGINT PRIMARY KEY,
  entity_type  VARCHAR(24) NOT NULL,       -- question / chapter / exam / course / product
  entity_id    BIGINT      NOT NULL,
  action       VARCHAR(24) NOT NULL,       -- create / update / publish / archive / rollback / delete / review / submit
  batch_id     BIGINT,                     -- 若来自批量导入
  diff         JSONB,                      -- {"before":{...},"after":{...}}
  change_log   VARCHAR(500),
  operator_id  BIGINT      REFERENCES users(id),
  operator_ip  INET,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ccl_entity ON content_change_logs(entity_type, entity_id, created_at DESC);
CREATE INDEX idx_ccl_batch ON content_change_logs(batch_id);

-- 题目统计（作答数据反哺，用于难度校正与质量预警）
CREATE TABLE question_stats (
  question_id    BIGINT PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  attempt_count  INTEGER     NOT NULL DEFAULT 0,
  correct_count  INTEGER     NOT NULL DEFAULT 0,
  correct_rate   NUMERIC(5,4) NOT NULL DEFAULT 0,
  avg_time_ms    INTEGER     NOT NULL DEFAULT 0,
  favorite_count INTEGER     NOT NULL DEFAULT 0,
  wrong_count    INTEGER     NOT NULL DEFAULT 0,
  report_count   INTEGER     NOT NULL DEFAULT 0,
  discrimination NUMERIC(5,4) NOT NULL DEFAULT 0,   -- 区分度
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 题目报错/纠错反馈
CREATE TABLE question_reports (
  id          BIGINT PRIMARY KEY,
  question_id BIGINT      NOT NULL REFERENCES questions(id),
  user_id     BIGINT      REFERENCES users(id),
  reason      VARCHAR(24) NOT NULL CHECK (reason IN ('answer_wrong','stem_error','option_error','analysis_error','duplicate','copyright','other')),
  detail      VARCHAR(1000),
  images      JSONB       NOT NULL DEFAULT '[]'::jsonb,
  status      VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
  handled_by  BIGINT,
  handled_at  TIMESTAMPTZ,
  handle_note VARCHAR(500),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_qreports_status ON question_reports(status, created_at DESC);
CREATE INDEX idx_qreports_question ON question_reports(question_id);

-- 批量导入批次
CREATE TABLE import_batches (
  id             BIGINT PRIMARY KEY,
  batch_no       VARCHAR(40) NOT NULL,
  file_name      VARCHAR(255) NOT NULL,
  file_url       VARCHAR(512),
  file_hash      CHAR(64)    NOT NULL,
  file_type      VARCHAR(16) NOT NULL CHECK (file_type IN ('csv','xlsx','json')),
  subject_id     BIGINT      REFERENCES subjects(id),
  source_type    VARCHAR(16) NOT NULL DEFAULT 'user_import',
  license_note   VARCHAR(500),
  mode           VARCHAR(16) NOT NULL DEFAULT 'insert'
                 CHECK (mode IN ('insert','upsert','dry_run','incremental')),
  total_rows     INTEGER     NOT NULL DEFAULT 0,
  success_rows   INTEGER     NOT NULL DEFAULT 0,
  failed_rows    INTEGER     NOT NULL DEFAULT 0,
  duplicate_rows INTEGER     NOT NULL DEFAULT 0,
  updated_rows   INTEGER     NOT NULL DEFAULT 0,
  error_report   JSONB,
  status         VARCHAR(16) NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','parsing','validating','importing','done','failed','rolled_back')),
  auto_publish   BOOLEAN     NOT NULL DEFAULT false,
  rollback_at    TIMESTAMPTZ,
  rollback_by    BIGINT,
  operator_id    BIGINT      REFERENCES users(id),
  started_at     TIMESTAMPTZ,
  finished_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_import_batches_no ON import_batches(batch_no);
CREATE INDEX idx_import_batches_status ON import_batches(status, created_at DESC);

CREATE TABLE import_items (
  id          BIGINT PRIMARY KEY,
  batch_id    BIGINT      NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
  row_no      INTEGER     NOT NULL,
  raw         JSONB       NOT NULL,
  question_id BIGINT,
  action      VARCHAR(16) NOT NULL CHECK (action IN ('insert','update','skip','error','duplicate')),
  message     VARCHAR(500),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_import_items_batch ON import_items(batch_id, action);

-- 题库整体版本（用于整库快照与回滚基线）
CREATE TABLE bank_versions (
  id            BIGINT PRIMARY KEY,
  version_no    VARCHAR(32) NOT NULL,
  subject_id    BIGINT      REFERENCES subjects(id),
  question_count INTEGER    NOT NULL DEFAULT 0,
  snapshot_url  VARCHAR(512),
  change_log    VARCHAR(1000),
  created_by    BIGINT      REFERENCES users(id),
  is_baseline   BOOLEAN     NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_bank_versions_no ON bank_versions(version_no, COALESCE(subject_id, 0));


-- =====================================================================
-- 6. 练习 / 错题 / 掌握度
-- =====================================================================

CREATE TABLE practice_sessions (
  id           BIGINT PRIMARY KEY,
  user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode         VARCHAR(24) NOT NULL
               CHECK (mode IN ('chapter','daily','random','wrong','favorite','real','mock','custom','kp','review')),
  subject_id   BIGINT      REFERENCES subjects(id),
  chapter_id   BIGINT      REFERENCES chapters(id),
  exam_id      BIGINT,
  title        VARCHAR(200) NOT NULL DEFAULT '',
  config       JSONB       NOT NULL DEFAULT '{}'::jsonb,   -- 生成时的筛选条件，供「再练一遍」
  total        SMALLINT    NOT NULL DEFAULT 0,
  answered     SMALLINT    NOT NULL DEFAULT 0,
  correct      SMALLINT    NOT NULL DEFAULT 0,
  score        NUMERIC(7,2) NOT NULL DEFAULT 0,
  duration_sec INTEGER     NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'doing' CHECK (status IN ('doing','finished','abandoned')),
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_practice_user ON practice_sessions(user_id, created_at DESC);
CREATE INDEX idx_practice_user_mode ON practice_sessions(user_id, mode, created_at DESC);
CREATE TRIGGER trg_practice_sessions_updated BEFORE UPDATE ON practice_sessions FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE practice_items (
  id           BIGINT PRIMARY KEY,
  session_id   BIGINT      NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
  user_id      BIGINT      NOT NULL,
  question_id  BIGINT      NOT NULL REFERENCES questions(id),
  seq          SMALLINT    NOT NULL,
  user_answer  JSONB,
  is_correct   BOOLEAN,
  score        NUMERIC(6,2),
  time_ms      INTEGER     NOT NULL DEFAULT 0,
  marked       BOOLEAN     NOT NULL DEFAULT false,
  show_analysis BOOLEAN    NOT NULL DEFAULT false,
  answered_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_practice_items ON practice_items(session_id, question_id);
CREATE INDEX idx_practice_items_session ON practice_items(session_id, seq);
CREATE INDEX idx_practice_items_user_q ON practice_items(user_id, question_id);

-- 用户-题目 状态总表（含 SM-2 记忆曲线字段）
CREATE TABLE user_question_state (
  id             BIGINT PRIMARY KEY,
  user_id        BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id    BIGINT      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  subject_id     BIGINT      NOT NULL,
  status         VARCHAR(16) NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new','done','wrong','mastered')),
  correct_count  SMALLINT    NOT NULL DEFAULT 0,
  wrong_count    SMALLINT    NOT NULL DEFAULT 0,
  streak         SMALLINT    NOT NULL DEFAULT 0,      -- 连续答对次数
  last_answer    JSONB,
  last_result    BOOLEAN,
  last_at        TIMESTAMPTZ,
  mastery        NUMERIC(5,4) NOT NULL DEFAULT 0,     -- 掌握度 0~1
  -- SM-2 记忆曲线
  ease_factor    NUMERIC(4,2) NOT NULL DEFAULT 2.50,
  interval_days  INTEGER     NOT NULL DEFAULT 0,
  repetitions    SMALLINT    NOT NULL DEFAULT 0,
  next_review_at TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_uqs ON user_question_state(user_id, question_id);
CREATE INDEX idx_uqs_due ON user_question_state(user_id, next_review_at) WHERE next_review_at IS NOT NULL;
CREATE INDEX idx_uqs_wrong ON user_question_state(user_id, subject_id, status) WHERE status = 'wrong';
CREATE TRIGGER trg_uqs_updated BEFORE UPDATE ON user_question_state FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 错题本（独立表，便于人工整理与教研分析；与 uqs 通过 userId+questionId 对齐）
CREATE TABLE wrong_questions (
  id            BIGINT PRIMARY KEY,
  user_id       BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id   BIGINT      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  subject_id    BIGINT      NOT NULL,
  chapter_id    BIGINT,
  wrong_count   SMALLINT    NOT NULL DEFAULT 1,
  retry_correct SMALLINT    NOT NULL DEFAULT 0,
  mastered_level SMALLINT   NOT NULL DEFAULT 0 CHECK (mastered_level BETWEEN 0 AND 3),
  reason_tag    VARCHAR(24),        -- 概念不清/审题失误/计算错/蒙对
  last_wrong_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  next_review_at TIMESTAMPTZ,
  is_removed    BOOLEAN     NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_wrong_user_question ON wrong_questions(user_id, question_id);
CREATE INDEX idx_wrong_user ON wrong_questions(user_id, is_removed, last_wrong_at DESC);
CREATE INDEX idx_wrong_due ON wrong_questions(user_id, next_review_at) WHERE is_removed = false;
CREATE TRIGGER trg_wrong_updated BEFORE UPDATE ON wrong_questions FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- =====================================================================
-- 7. 试卷 / 模考 / 成绩
-- =====================================================================

CREATE TABLE exams (
  id             BIGINT PRIMARY KEY,
  subject_id     BIGINT      NOT NULL REFERENCES subjects(id),
  professional   VARCHAR(24),
  title          VARCHAR(200) NOT NULL,
  type           VARCHAR(24) NOT NULL
                 CHECK (type IN ('real','mock','chapter_test','sprint','daily','custom')),
  exam_year      SMALLINT,
  paper_no       VARCHAR(32),
  source_type    VARCHAR(16) NOT NULL DEFAULT 'self',
  total_score    NUMERIC(7,2) NOT NULL DEFAULT 0,
  pass_score     NUMERIC(7,2) NOT NULL DEFAULT 0,
  question_count INTEGER     NOT NULL DEFAULT 0,
  duration_min   SMALLINT    NOT NULL DEFAULT 0,
  difficulty     NUMERIC(3,1) NOT NULL DEFAULT 0,
  has_subjective BOOLEAN     NOT NULL DEFAULT false,
  intro_html     TEXT,
  rule_config    JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- 随机顺序/单题限时/是否可回看
  attempt_count  INTEGER     NOT NULL DEFAULT 0,
  avg_score      NUMERIC(7,2) NOT NULL DEFAULT 0,
  is_free        BOOLEAN     NOT NULL DEFAULT false,
  status         VARCHAR(16) NOT NULL DEFAULT 'draft'
                 -- 三态：draft → published → off →（重发）→ published，或 is_deleted 归档。
                 -- 'archived' 已于 20260919-01 移除：它与 is_deleted 语义重复且从未被写入过
                 -- （见 docs/14-状态机审计.md）。'reviewing'（送审）保留并标预留。
                 CHECK (status IN ('draft','reviewing','published','off')),
  published_at   TIMESTAMPTZ,
  is_deleted     BOOLEAN     NOT NULL DEFAULT false,
  created_by     BIGINT      REFERENCES users(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_exams_subject ON exams(subject_id, type, status);
CREATE TRIGGER trg_exams_updated BEFORE UPDATE ON exams FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 卷面结构（题型分段，含分值与数量；用于组卷与校验）
CREATE TABLE exam_sections (
  id            BIGINT PRIMARY KEY,
  exam_id       BIGINT      NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  seq           SMALLINT    NOT NULL,
  name          VARCHAR(96) NOT NULL,     -- 如「单项选择题」
  question_type VARCHAR(16) NOT NULL,
  question_count SMALLINT   NOT NULL DEFAULT 0,
  score_per     NUMERIC(6,2) NOT NULL DEFAULT 1,
  section_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  sort_no       SMALLINT    NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_exam_sections ON exam_sections(exam_id, sort_no);

CREATE TABLE exam_questions (
  id          BIGINT PRIMARY KEY,
  exam_id     BIGINT      NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  section_id  BIGINT      REFERENCES exam_sections(id) ON DELETE CASCADE,
  question_id BIGINT      NOT NULL REFERENCES questions(id),
  seq         SMALLINT    NOT NULL,
  score       NUMERIC(6,2) NOT NULL DEFAULT 1,
  -- 发布试卷时锁定的题目版本（Batch 7 补列）。
  -- 已发布/已考过的卷面必须按这一版作答与展示 ——
  -- "考生昨天考了 80 分，今天你改了答案，他的成绩就成了悬案"（docs/07 §6.4）。
  -- 未发布时为 NULL；重新组卷会清空。
  locked_version INTEGER,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_exam_question ON exam_questions(exam_id, question_id);
CREATE INDEX idx_exam_questions_seq ON exam_questions(exam_id, seq);

-- 组卷规则（定时/一键生成模拟卷）
CREATE TABLE paper_rules (
  id          BIGINT PRIMARY KEY,
  name        VARCHAR(160) NOT NULL,
  subject_id  BIGINT      NOT NULL REFERENCES subjects(id),
  type        VARCHAR(24) NOT NULL DEFAULT 'mock',
  duration_min SMALLINT   NOT NULL DEFAULT 180,
  rules       JSONB       NOT NULL,     -- [{"type":"single","count":60,"score":1,"difficulty":[2,4],"kp_ids":[...]}]
  strategy    VARCHAR(24) NOT NULL DEFAULT 'random'
              CHECK (strategy IN ('random','weak_first','coverage','history_similar')),
  status      VARCHAR(16) NOT NULL DEFAULT 'on' CHECK (status IN ('on','off')),
  created_by  BIGINT      REFERENCES users(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_paper_rules_updated BEFORE UPDATE ON paper_rules FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE exam_attempts (
  id              BIGINT PRIMARY KEY,
  exam_id         BIGINT      NOT NULL REFERENCES exams(id),
  user_id         BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  subject_id      BIGINT      NOT NULL,
  attempt_no      SMALLINT    NOT NULL DEFAULT 1,
  objective_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  subjective_score NUMERIC(7,2) NOT NULL DEFAULT 0,
  total_score     NUMERIC(7,2) NOT NULL DEFAULT 0,
  full_score      NUMERIC(7,2) NOT NULL DEFAULT 0,
  correct_count   SMALLINT    NOT NULL DEFAULT 0,
  wrong_count     SMALLINT    NOT NULL DEFAULT 0,
  unanswered_count SMALLINT   NOT NULL DEFAULT 0,
  is_pass         BOOLEAN,
  duration_sec    INTEGER     NOT NULL DEFAULT 0,
  rank_no         INTEGER,
  score_percentile NUMERIC(5,2),
  status          VARCHAR(16) NOT NULL DEFAULT 'doing'
                  CHECK (status IN ('doing','submitted','scoring','scored','expired','abandoned')),
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deadline_at     TIMESTAMPTZ,          -- 服务端权威截止时间
  submitted_at    TIMESTAMPTZ,
  scored_at       TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_attempt_no ON exam_attempts(exam_id, user_id, attempt_no);
CREATE INDEX idx_attempts_user ON exam_attempts(user_id, created_at DESC);
CREATE INDEX idx_attempts_exam ON exam_attempts(exam_id, status, total_score DESC);
CREATE TRIGGER trg_attempts_updated BEFORE UPDATE ON exam_attempts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE exam_attempt_items (
  id              BIGINT PRIMARY KEY,
  attempt_id      BIGINT      NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
  question_id     BIGINT      NOT NULL REFERENCES questions(id),
  seq             SMALLINT    NOT NULL,
  user_answer     JSONB,
  is_correct      BOOLEAN,
  auto_score      NUMERIC(6,2) NOT NULL DEFAULT 0,
  final_score     NUMERIC(6,2) NOT NULL DEFAULT 0,
  scored_by       VARCHAR(16) CHECK (scored_by IN ('auto','self','teacher')),
  reviewer_id     BIGINT      REFERENCES users(id),
  review_comment  VARCHAR(1000),
  point_hits      JSONB       NOT NULL DEFAULT '[]'::jsonb,   -- 主观题评分点命中情况
  time_ms         INTEGER     NOT NULL DEFAULT 0,
  marked          BOOLEAN     NOT NULL DEFAULT false,
  answered_at     TIMESTAMPTZ,
  reviewed_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_attempt_item ON exam_attempt_items(attempt_id, question_id);
CREATE INDEX idx_attempt_items ON exam_attempt_items(attempt_id, seq);

-- 排名快照（Redis ZSET 实时榜，此表用于沉淀历史与离线分析）
CREATE TABLE exam_rank_snapshots (
  id          BIGINT PRIMARY KEY,
  exam_id     BIGINT      NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  user_id     BIGINT      NOT NULL,
  total_score NUMERIC(7,2) NOT NULL,
  rank_no     INTEGER     NOT NULL,
  percentile  NUMERIC(5,2) NOT NULL DEFAULT 0,
  snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rank_snap ON exam_rank_snapshots(exam_id, rank_no);


-- =====================================================================
-- 8. 智能学习 / 统计分析
-- =====================================================================

CREATE TABLE user_knowledge_stats (
  id                BIGINT PRIMARY KEY,
  user_id           BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  knowledge_point_id BIGINT     NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
  subject_id        BIGINT      NOT NULL,
  chapter_id        BIGINT,
  attempt_count     INTEGER     NOT NULL DEFAULT 0,
  correct_count     INTEGER     NOT NULL DEFAULT 0,
  accuracy          NUMERIC(5,4) NOT NULL DEFAULT 0,
  mastery           NUMERIC(5,4) NOT NULL DEFAULT 0,
  avg_time_ms       INTEGER     NOT NULL DEFAULT 0,
  last_at           TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_uks ON user_knowledge_stats(user_id, knowledge_point_id);
CREATE INDEX idx_uks_weak ON user_knowledge_stats(user_id, subject_id, mastery);
CREATE TRIGGER trg_uks_updated BEFORE UPDATE ON user_knowledge_stats FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE user_subject_stats (
  user_id        BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  answered_total INTEGER     NOT NULL DEFAULT 0,
  correct_total  INTEGER     NOT NULL DEFAULT 0,
  accuracy       NUMERIC(5,4) NOT NULL DEFAULT 0,
  study_minutes  INTEGER     NOT NULL DEFAULT 0,
  lesson_done    INTEGER     NOT NULL DEFAULT 0,
  exam_count     INTEGER     NOT NULL DEFAULT 0,
  subject_json   JSONB       NOT NULL DEFAULT '{}'::jsonb,  -- {subjectId: {...}}
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE daily_study_stats (
  id             BIGINT PRIMARY KEY,
  user_id        BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stat_date      DATE        NOT NULL,
  study_minutes  INTEGER     NOT NULL DEFAULT 0,
  lesson_count   INTEGER     NOT NULL DEFAULT 0,
  question_count INTEGER     NOT NULL DEFAULT 0,
  correct_count  INTEGER     NOT NULL DEFAULT 0,
  exam_count     INTEGER     NOT NULL DEFAULT 0,
  points         INTEGER     NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_daily_stats ON daily_study_stats(user_id, stat_date);
CREATE INDEX idx_daily_stats_date ON daily_study_stats(stat_date);
CREATE TRIGGER trg_daily_stats_updated BEFORE UPDATE ON daily_study_stats FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE learning_reports (
  id           BIGINT PRIMARY KEY,
  user_id      BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_type  VARCHAR(16) NOT NULL CHECK (period_type IN ('week','month','season','custom')),
  period_start DATE        NOT NULL,
  period_end   DATE        NOT NULL,
  subject_id   BIGINT,
  data         JSONB       NOT NULL,       -- 报告全量数据（趋势、雷达、错因、建议）
  summary      VARCHAR(1000),
  share_image  VARCHAR(512),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_reports ON learning_reports(user_id, period_type, period_start, COALESCE(subject_id,0));

CREATE TABLE recommendations (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  rec_type    VARCHAR(24) NOT NULL
              CHECK (rec_type IN ('weak_practice','review_due','next_lesson','exam_suggest','kp_lesson','daily_digest')),
  subject_id  BIGINT,
  payload     JSONB       NOT NULL,
  reason      VARCHAR(255),
  score       NUMERIC(6,4) NOT NULL DEFAULT 0,
  status      VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','consumed','expired')),
  expire_at   TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_recs_user ON recommendations(user_id, status, score DESC);

CREATE TABLE user_streaks (
  user_id        BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  current_days   INTEGER     NOT NULL DEFAULT 0,
  max_days       INTEGER     NOT NULL DEFAULT 0,
  last_checkin   DATE,
  total_days     INTEGER     NOT NULL DEFAULT 0,
  total_points   INTEGER     NOT NULL DEFAULT 0,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 错因标签统计（用于薄弱点归因）
CREATE TABLE user_wrong_reasons (
  id         BIGINT PRIMARY KEY,
  user_id    BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  subject_id BIGINT      NOT NULL,
  reason_tag VARCHAR(24) NOT NULL,
  cnt        INTEGER     NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_uwr ON user_wrong_reasons(user_id, subject_id, reason_tag);


-- =====================================================================
-- 9. 资讯 / 提醒 / 运营
-- =====================================================================

CREATE TABLE announcements (
  id           BIGINT PRIMARY KEY,
  title        VARCHAR(200) NOT NULL,
  summary      VARCHAR(500),
  content_html TEXT        NOT NULL,
  type         VARCHAR(24) NOT NULL DEFAULT 'notice'
               CHECK (type IN ('notice','policy','exam','score','register','activity','maintenance')),
  subject_id   BIGINT      REFERENCES subjects(id),
  cover_url    VARCHAR(512),
  target_roles JSONB       NOT NULL DEFAULT '["all"]'::jsonb,
  target_professional VARCHAR(24),
  is_top       BOOLEAN     NOT NULL DEFAULT false,
  is_popup     BOOLEAN     NOT NULL DEFAULT false,
  view_count   INTEGER     NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','off')),
  publish_at   TIMESTAMPTZ,
  expire_at    TIMESTAMPTZ,
  created_by   BIGINT      REFERENCES users(id),
  is_deleted   BOOLEAN     NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_announcements ON announcements(status, is_top DESC, publish_at DESC);
CREATE TRIGGER trg_announcements_updated BEFORE UPDATE ON announcements FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 考试日历（倒计时状态机的数据源）
CREATE TABLE exam_calendar (
  id           BIGINT PRIMARY KEY,
  exam_level   VARCHAR(16) NOT NULL DEFAULT 'yijian',
  professional VARCHAR(24),
  exam_year    SMALLINT    NOT NULL,
  event_type   VARCHAR(24) NOT NULL
               CHECK (event_type IN ('outline','register_start','register_end','pay_end','ticket_print','exam_start','exam_end','score_release','certificate')),
  title        VARCHAR(160) NOT NULL,
  start_date   DATE        NOT NULL,
  end_date     DATE,
  province     VARCHAR(32),          -- NULL = 全国
  description  TEXT,
  source_url   VARCHAR(512),
  status       VARCHAR(16) NOT NULL DEFAULT 'published' CHECK (status IN ('draft','published','off')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_exam_calendar ON exam_calendar(exam_level, exam_year, event_type);
CREATE TRIGGER trg_exam_calendar_updated BEFORE UPDATE ON exam_calendar FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE notifications (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        VARCHAR(32) NOT NULL,
  title       VARCHAR(200) NOT NULL,
  content     VARCHAR(1000),
  channel     VARCHAR(16) NOT NULL DEFAULT 'inapp'
              CHECK (channel IN ('inapp','sms','wechat','push','email')),
  biz_type    VARCHAR(32),
  biz_id      BIGINT,
  link        VARCHAR(512),
  status      VARCHAR(16) NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','sent','failed','read')),
  sent_at     TIMESTAMPTZ,
  read_at     TIMESTAMPTZ,
  fail_reason VARCHAR(255),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notifications_user ON notifications(user_id, created_at DESC);
CREATE INDEX idx_notifications_pending ON notifications(status) WHERE status = 'pending';

-- 推送任务（运营群发）
CREATE TABLE push_tasks (
  id           BIGINT PRIMARY KEY,
  name         VARCHAR(160) NOT NULL,
  channel      VARCHAR(16) NOT NULL DEFAULT 'inapp',
  audience     JSONB       NOT NULL,       -- {"exam_year":2027,"professional":"jz","no_login_days":7}
  title        VARCHAR(200) NOT NULL,
  content      VARCHAR(1000),
  link         VARCHAR(512),
  scheduled_at TIMESTAMPTZ,
  total_count  INTEGER     NOT NULL DEFAULT 0,
  sent_count   INTEGER     NOT NULL DEFAULT 0,
  fail_count   INTEGER     NOT NULL DEFAULT 0,
  status       VARCHAR(16) NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft','scheduled','sending','done','cancelled','failed')),
  created_by   BIGINT      REFERENCES users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_push_tasks_updated BEFORE UPDATE ON push_tasks FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE banners (
  id          BIGINT PRIMARY KEY,
  title       VARCHAR(160) NOT NULL,
  image_url   VARCHAR(512) NOT NULL,
  link        VARCHAR(512),
  position    VARCHAR(32) NOT NULL DEFAULT 'home_top',
  audience    JSONB       NOT NULL DEFAULT '{}'::jsonb,
  sort_no     INTEGER     NOT NULL DEFAULT 0,
  start_at    TIMESTAMPTZ,
  end_at      TIMESTAMPTZ,
  status      VARCHAR(16) NOT NULL DEFAULT 'on' CHECK (status IN ('on','off')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_banners_updated BEFORE UPDATE ON banners FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE feedbacks (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT      REFERENCES users(id),
  type        VARCHAR(24) NOT NULL DEFAULT 'suggest',
  content     TEXT        NOT NULL,
  contact     VARCHAR(96),
  images      JSONB       NOT NULL DEFAULT '[]'::jsonb,
  status      VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','closed')),
  reply       TEXT,
  handled_by  BIGINT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_feedbacks ON feedbacks(status, created_at DESC);


-- =====================================================================
-- 10. 系统 / 文件 / 审计
-- =====================================================================

CREATE TABLE files (
  id          BIGINT PRIMARY KEY,
  owner_id    BIGINT      REFERENCES users(id),
  bucket      VARCHAR(64) NOT NULL,
  object_key  VARCHAR(512) NOT NULL,
  url         VARCHAR(512),
  file_name   VARCHAR(255),
  mime_type   VARCHAR(96),
  size_bytes  BIGINT      NOT NULL DEFAULT 0,
  hash        CHAR(64),
  biz_type    VARCHAR(32),
  is_public   BOOLEAN     NOT NULL DEFAULT false,
  ref_count   INTEGER     NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_files_key ON files(bucket, object_key);
CREATE INDEX idx_files_hash ON files(hash);

CREATE TABLE app_configs (
  id          BIGINT PRIMARY KEY,
  config_key  VARCHAR(96) NOT NULL,
  config_value JSONB     NOT NULL,
  group_name  VARCHAR(48) NOT NULL DEFAULT 'common',
  description VARCHAR(255),
  is_public   BOOLEAN     NOT NULL DEFAULT false,
  updated_by  BIGINT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_app_configs_key ON app_configs(config_key);
CREATE TRIGGER trg_app_configs_updated BEFORE UPDATE ON app_configs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE dictionaries (
  id          BIGINT PRIMARY KEY,
  dict_type   VARCHAR(48) NOT NULL,
  dict_key    VARCHAR(48) NOT NULL,
  dict_label  VARCHAR(96) NOT NULL,
  extra       JSONB       NOT NULL DEFAULT '{}'::jsonb,
  sort_no     INTEGER     NOT NULL DEFAULT 0,
  status      VARCHAR(16) NOT NULL DEFAULT 'on'
              -- 与 subjects / banners / paper_rules 等"启用位"表统一用 on/off。
              -- 补于 20260919-02：此前是全库唯一没有 CHECK 的 status 列，
              -- 写错一个值不会被拦住，而读路径全按 status='on' 过滤 → 那条字典会静默消失。
              CHECK (status IN ('on','off')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_dict ON dictionaries(dict_type, dict_key);

CREATE TABLE audit_logs (
  id          BIGINT PRIMARY KEY,
  actor_id    BIGINT      REFERENCES users(id),
  actor_name  VARCHAR(64),
  action      VARCHAR(64) NOT NULL,
  module      VARCHAR(48) NOT NULL DEFAULT '',
  entity_type VARCHAR(32),
  entity_id   BIGINT,
  before_data JSONB,
  after_data  JSONB,
  method      VARCHAR(8),
  path        VARCHAR(255),
  ip          INET,
  user_agent  VARCHAR(512),
  duration_ms INTEGER,
  success     BOOLEAN     NOT NULL DEFAULT true,
  error_msg   VARCHAR(500),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_actor ON audit_logs(actor_id, created_at DESC);
CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_logs(action, created_at DESC);

-- 短信验证码审计（验证码本体存 Redis，此表仅留痕便于风控）
CREATE TABLE sms_logs (
  id          BIGINT PRIMARY KEY,
  phone       VARCHAR(20) NOT NULL,
  scene       VARCHAR(32) NOT NULL,
  code_digest VARCHAR(64),
  provider    VARCHAR(32),
  biz_id      VARCHAR(96),
  status      VARCHAR(16) NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','failed','verified','expired')),
  ip          INET,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_sms_logs_phone ON sms_logs(phone, created_at DESC);

-- 风控告警（防刷命中记录）
CREATE TABLE risk_events (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT,
  phone       VARCHAR(20),
  event_type  VARCHAR(48) NOT NULL,    -- sms_flood / login_bruteforce / answer_flood / scrape
  severity    VARCHAR(16) NOT NULL DEFAULT 'low' CHECK (severity IN ('low','medium','high')),
  detail      JSONB,
  ip          INET,
  device_id   VARCHAR(128),
  handled     BOOLEAN     NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_events ON risk_events(event_type, created_at DESC);


-- =====================================================================
-- 11. 便捷视图
-- =====================================================================

-- 已发布题目视图（业务查询默认走这里，自动过滤软删除）
CREATE OR REPLACE VIEW v_published_questions AS
SELECT q.*
FROM questions q
WHERE q.status = 'published' AND q.is_deleted = false;

-- 章节题目数统计视图
CREATE OR REPLACE VIEW v_chapter_question_count AS
SELECT chapter_id, subject_id, type, count(*) AS cnt
FROM questions
WHERE status = 'published' AND is_deleted = false AND chapter_id IS NOT NULL
GROUP BY chapter_id, subject_id, type;

-- 用户待复习队列
CREATE OR REPLACE VIEW v_due_reviews AS
SELECT s.user_id, s.question_id, s.subject_id, s.next_review_at, s.repetitions, s.mastery
FROM user_question_state s
JOIN questions q ON q.id = s.question_id AND q.status = 'published' AND q.is_deleted = false
WHERE s.next_review_at IS NOT NULL AND s.next_review_at <= now();


-- =====================================================================
-- 12. 基础数据（幂等 UPSERT，可重复执行）
-- =====================================================================

-- >>> RBAC-SEED:START
-- ↑ 这两行是**机器标记**，`python -m app.cli seed-rbac` 靠它们精确切片重放本段。
--   作用：init-db 只在空库执行 schema.sql，已建好的库拿不到后加的角色；
--        seed-rbac 把本节（角色 + 权限 + 角色权限映射）单独重放一遍，幂等。
--   所以：新增角色改在这里即可，CLI 自动跟上；**不要改动或翻译这两行标记**，
--        也不要在别处（包括注释里）书写同样字面量，否则切片会错位。
--
-- 12.1 角色
INSERT INTO roles (id, code, name, description, is_system, sort_no) VALUES
  (1, 'super_admin', '超级管理员', '系统拥有者，拥有全部权限', true, 1),
  (2, 'admin',       '管理员',     '系统管理，含用户/订单/配置',   true, 2),
  (3, 'researcher',  '教研',       '题库与试卷管理',               true, 3),
  (4, 'teacher',     '教师',       '课程与答疑',                   true, 4),
  (5, 'operator',    '运营',       '内容运营与推送',               true, 5),
  (6, 'student',     '学员',       '前台普通用户',                 true, 6),
  (7, 'viewer',      '只读审计员', '只读管理员（审计岗）：可看用户与审计日志，不能改', true, 7)
ON CONFLICT (id) DO NOTHING;

-- 12.2 权限
INSERT INTO permissions (id, code, name, type, module, sort_no) VALUES
  (101,'question:read',   '查看题目','api','question',1),
  (102,'question:create', '新增题目','api','question',2),
  (103,'question:update', '编辑题目','api','question',3),
  (104,'question:delete', '删除题目','api','question',4),
  (105,'question:import', '批量导入','api','question',5),
  (106,'question:review', '审核题目','api','question',6),
  (107,'question:publish','发布题目','api','question',7),
  (108,'question:rollback','回滚版本','api','question',8),
  (201,'exam:read',       '查看试卷','api','exam',1),
  (202,'exam:create',     '创建试卷','api','exam',2),
  (203,'exam:publish',    '发布试卷','api','exam',3),
  (204,'exam:grade',      '人工批改','api','exam',4),
  (301,'course:read',     '查看课程','api','course',1),
  (302,'course:manage',   '管理课程','api','course',2),
  (401,'user:read',       '查看用户','api','user',1),
  (402,'user:manage',     '管理用户','api','user',2),
  (403,'user:export',     '导出用户','api','user',3),
  (501,'order:read',      '查看订单','api','order',1),
  (502,'order:refund',    '订单退款','api','order',2),
  (601,'content:manage',  '内容运营','api','content',1),
  (701,'stats:read',      '查看统计','api','stats',1),
  (801,'system:config',   '系统配置','api','system',1),
  (802,'system:role',     '角色权限','api','system',2),
  (803,'system:audit',    '审计日志','api','system',3)
ON CONFLICT (id) DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT 1, id FROM permissions
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT 3, id FROM permissions WHERE module IN ('question','exam','stats')
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT 4, id FROM permissions WHERE module IN ('course','exam','stats')
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT 5, id FROM permissions WHERE module IN ('content','stats','user')
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_id)
SELECT 2, id FROM permissions WHERE code <> 'question:rollback'
ON CONFLICT DO NOTHING;

-- viewer：**通用只读岗**。权限刻意选得最小：
--   user:read      能进用户列表/详情（但**没有** user:manage → 前端「分配角色」按钮 disabled）
--   system:audit   能看审计日志
--   stats:read     能看统计
--   exam:read      能看试卷列表/详情（Batch 7 加，**没有** exam:publish → 「发布」按钮置灰）
-- 它存在的现实理由：种子里的其他角色凡有某模块 read 权限的，**都同时拿到了该模块的写权限**
-- （权限按 module 整包发放）。于是「有 read、没有 write → 按钮置灰」这个 B 端状态
-- **没有任何账号能复现**：
--   · user 模块：有 user:read 的（super_admin/admin/operator）都有 user:manage
--   · exam 模块：super_admin/admin/researcher/teacher 四条 exam 权限**完全一样**
-- viewer 是唯一能复现"只读"的账号，让权限门控能在浏览器里被真实验证（验收标准 2 靠它）。
--
-- ⚠️ 定位是**通用只读岗**，将来补新的只读场景（比如 course:read）往这里加即可，
-- **不要再新建角色**。
INSERT INTO role_permissions (role_id, permission_id)
SELECT 7, id FROM permissions
WHERE code IN ('user:read', 'system:audit', 'stats:read', 'exam:read')
ON CONFLICT DO NOTHING;

-- <<< RBAC-SEED:END

-- 12.3 科目：3 门公共课 + 10 个实务专业
INSERT INTO subjects (id, code, name, short_name, exam_level, category, professional, full_score, pass_score, duration_min, color, sort_no) VALUES
  (1001,'JGJJ',  '建设工程经济',            '经济','yijian','public',NULL, 100, 60, 120,'#C8102E',1),
  (1002,'FAGUI', '建设工程法规及相关知识',  '法规','yijian','public',NULL, 130, 78, 180,'#1D4ED8',2),
  (1003,'XMGL',  '建设工程项目管理',        '管理','yijian','public',NULL, 130, 78, 180,'#047857',3),
  (2001,'SW-JZ',   '专业工程管理与实务（建筑工程）',        '建筑实务','yijian','professional','jz',   160, 96, 240,'#B45309',11),
  (2002,'SW-GL',   '专业工程管理与实务（公路工程）',        '公路实务','yijian','professional','gl',   160, 96, 240,'#7C3AED',12),
  (2003,'SW-TL',   '专业工程管理与实务（铁路工程）',        '铁路实务','yijian','professional','tl',   160, 96, 240,'#0F766E',13),
  (2004,'SW-MHJC', '专业工程管理与实务（民航机场工程）',    '民航实务','yijian','professional','mhjc', 160, 96, 240,'#0369A1',14),
  (2005,'SW-GKHD', '专业工程管理与实务（港口与航道工程）',  '港航实务','yijian','professional','gkhd', 160, 96, 240,'#0891B2',15),
  (2006,'SW-SHSD', '专业工程管理与实务（水利水电工程）',    '水利实务','yijian','professional','shsd', 160, 96, 240,'#15803D',16),
  (2007,'SW-SZ',   '专业工程管理与实务（市政公用工程）',    '市政实务','yijian','professional','sz',   160, 96, 240,'#BE185D',17),
  (2008,'SW-TXYG', '专业工程管理与实务（通信与广电工程）',  '通信实务','yijian','professional','txyg', 160, 96, 240,'#4338CA',18),
  (2009,'SW-KY',   '专业工程管理与实务（矿业工程）',        '矿业实务','yijian','professional','ky',   160, 96, 240,'#78350F',19),
  (2010,'SW-JD',   '专业工程管理与实务（机电工程）',        '机电实务','yijian','professional','jd',   160, 96, 240,'#1E40AF',20)
ON CONFLICT (id) DO NOTHING;

-- 12.4 章节（一级章节，示意；完整考纲章节在 Batch 2 的 seed 脚本中批量写入）
INSERT INTO chapters (id, subject_id, parent_id, code, name, level, path, outline_ref, weight, sort_no) VALUES
  (1101,1001,NULL,'JGJJ-01','第1章 工程经济',            1,'/1101/', '1Z101000', 45.0, 1),
  (1102,1001,NULL,'JGJJ-02','第2章 工程财务',            1,'/1102/', '1Z102000', 25.0, 2),
  (1103,1001,NULL,'JGJJ-03','第3章 建设工程估价',        1,'/1103/', '1Z103000', 30.0, 3),
  (1201,1002,NULL,'FG-01','第1章 建设工程基本法律知识',          1,'/1201/','1Z301000', 18.0, 1),
  (1202,1002,NULL,'FG-02','第2章 施工许可法律制度',              1,'/1202/','1Z302000',  8.0, 2),
  (1203,1002,NULL,'FG-03','第3章 建设工程发承包法律制度',        1,'/1203/','1Z303000', 15.0, 3),
  (1204,1002,NULL,'FG-04','第4章 建设工程合同和劳动合同法律制度',1,'/1204/','1Z304000', 18.0, 4),
  (1205,1002,NULL,'FG-05','第5章 施工环境保护、节约能源和文物保护法律制度',1,'/1205/','1Z305000', 6.0, 5),
  (1206,1002,NULL,'FG-06','第6章 建设工程安全生产法律制度',      1,'/1206/','1Z306000', 15.0, 6),
  (1207,1002,NULL,'FG-07','第7章 建设工程质量法律制度',          1,'/1207/','1Z307000', 12.0, 7),
  (1208,1002,NULL,'FG-08','第8章 解决建设工程纠纷法律制度',      1,'/1208/','1Z308000',  8.0, 8),
  (1301,1003,NULL,'XM-01','第1章 建设工程项目的组织与管理',      1,'/1301/','1Z201000', 28.0, 1),
  (1302,1003,NULL,'XM-02','第2章 建设工程项目成本管理',          1,'/1302/','1Z202000', 18.0, 2),
  (1303,1003,NULL,'XM-03','第3章 建设工程项目进度控制',          1,'/1303/','1Z203000', 18.0, 3),
  (1304,1003,NULL,'XM-04','第4章 建设工程项目质量控制',          1,'/1304/','1Z204000', 20.0, 4),
  (1305,1003,NULL,'XM-05','第5章 职业健康安全与环境管理',        1,'/1305/','1Z205000',  8.0, 5),
  (1306,1003,NULL,'XM-06','第6章 建设工程合同与合同管理',        1,'/1306/','1Z206000',  6.0, 6),
  (1307,1003,NULL,'XM-07','第7章 建设工程项目信息管理',          1,'/1307/','1Z207000',  2.0, 7),
  (2101,2001,NULL,'JZ-01','第1章 建筑工程技术',                  1,'/2101/','1A410000', 45.0, 1),
  (2102,2001,NULL,'JZ-02','第2章 建筑工程项目施工管理',          1,'/2102/','1A420000', 40.0, 2),
  (2103,2001,NULL,'JZ-03','第3章 建筑工程项目施工相关法规与标准',1,'/2103/','1A430000', 15.0, 3),
  (2201,2007,NULL,'SZ-01','第1章 市政公用工程技术',              1,'/2201/','1K410000', 45.0, 1),
  (2202,2007,NULL,'SZ-02','第2章 市政公用工程项目施工管理',      1,'/2202/','1K420000', 40.0, 2),
  (2203,2007,NULL,'SZ-03','第3章 市政公用工程项目施工相关法规与标准',1,'/2203/','1K430000', 15.0, 3),
  (2301,2010,NULL,'JD-01','第1章 机电工程技术',                  1,'/2301/','1H410000', 45.0, 1),
  (2302,2010,NULL,'JD-02','第2章 机电工程项目施工管理',          1,'/2302/','1H420000', 40.0, 2),
  (2303,2010,NULL,'JD-03','第3章 机电工程项目施工相关法规与标准',1,'/2303/','1H430000', 15.0, 3)
ON CONFLICT (id) DO NOTHING;

-- 12.5 考试日历（2027 考季为估算值，官方公布后运营在后台修正）
INSERT INTO exam_calendar (id, exam_level, professional, exam_year, event_type, title, start_date, end_date, description, source_url, status) VALUES
  (9001,'yijian',NULL,2026,'exam_start','2026年度一级建造师考试（第1天）','2026-09-12','2026-09-12','建设工程经济 9:00-11:00；建设工程法规及相关知识 14:00-17:00','http://www.cpta.com.cn/','published'),
  (9002,'yijian',NULL,2026,'exam_end',  '2026年度一级建造师考试（第2天）','2026-09-13','2026-09-13','建设工程项目管理 9:00-12:00；专业工程管理与实务 14:00-18:00','http://www.cpta.com.cn/','published'),
  (9003,'yijian',NULL,2026,'score_release','2026年度成绩公布（预计）','2026-12-15',NULL,'预计 2026 年 12 月中旬公布，以中国人事考试网通知为准','http://www.cpta.com.cn/','published'),
  (9101,'yijian',NULL,2027,'outline',      '2027年度考试大纲发布（预计）','2027-03-01',NULL,'参考往年节奏，以官方公告为准','http://www.cpta.com.cn/','published'),
  (9102,'yijian',NULL,2027,'register_start','2027年度报名开始（预计）','2027-06-17',NULL,'参考 2026 年节奏（6/17–7/12），各地略有差异','http://www.cpta.com.cn/','published'),
  (9103,'yijian',NULL,2027,'ticket_print','2027年度准考证打印（预计）','2027-09-03',NULL,'参考往年：考前一周开放打印','http://www.cpta.com.cn/','published'),
  (9104,'yijian',NULL,2027,'exam_start',  '2027年度一级建造师考试（第1天，预计）','2027-09-11',NULL,'预计 9 月第二个周末','http://www.cpta.com.cn/','published'),
  (9105,'yijian',NULL,2027,'exam_end',    '2027年度一级建造师考试（第2天，预计）','2027-09-12',NULL,'预计 9 月第二个周末','http://www.cpta.com.cn/','published')
ON CONFLICT (id) DO NOTHING;

-- 12.6 系统参数
INSERT INTO app_configs (id, config_key, config_value, group_name, description, is_public) VALUES
  (1,'site.name',            '"一建通"',                       'common','站点名称',true),
  (2,'exam.current_year',    '2027',                           'exam','当前备考考季',true),
  (3,'exam.cutoff_passing',  '{"JGJJ":60,"FAGUI":78,"XMGL":78,"SW":96}', 'exam','各科及格线',true),
  (4,'practice.paywall_after','10',                            'practice','免费用户可答题数（第 N 题触发付费墙）',true),
  (5,'practice.daily_count', '10',                             'practice','每日一练题量',true),
  (6,'question.answer_rule', '{"single":[1,0],"multiple":"all_or_partial","judge":[1,0]}', 'question','客观题判分规则',false),
  (7,'sms.code_ttl_sec',     '300',                            'security','短信验证码有效期（秒）',false),
  (8,'sms.daily_limit',      '10',                             'security','单手机号每日验证码上限',false),
  (9,'rate_limit.answer',    '120',                            'security','答题接口每分钟上限（次/用户）',false),
  (10,'review.sm2',          '{"initial_ease":2.5,"min_ease":1.3,"intervals":[0,1,2,4,7,15,30,60]}', 'learning','记忆曲线参数',false),
  -- 坑 42 的结构防护：单次写操作允许净减少的卷面题数上限。
  -- 老库靠 db/migrations/20260918-01-*.sql 补，这里是给新库用的。
  (11,'exam.mass_question_loss_threshold','10', 'exam','单次写操作允许净减少的卷面题数上限；超过则拒绝，要求改用 PUT /admin/exams/{id}/sections 显式确认',false)
ON CONFLICT (id) DO NOTHING;

-- 12.7 字典
INSERT INTO dictionaries (id, dict_type, dict_key, dict_label, sort_no) VALUES
  (1,'question_type','single','单选题',1),
  (2,'question_type','multiple','多选题',2),
  (3,'question_type','judge','判断题',3),
  (4,'question_type','case','案例题',4),
  (5,'question_type','case_sub','案例小题',5),
  (6,'question_type','fill','填空题',6),
  (7,'difficulty','1','易',1),
  (8,'difficulty','2','较易',2),
  (9,'difficulty','3','中等',3),
  (10,'difficulty','4','较难',4),
  (11,'difficulty','5','难',5),
  (12,'source_type','self','自有教研原创',1),
  (13,'source_type','authorized','授权采购',2),
  (14,'source_type','public','公开可用',3),
  (15,'source_type','user_import','用户导入',4),
  (16,'source_type','ai_assisted','AI辅助原创（需人工复核）',5),
  (17,'wrong_reason','concept','概念不清',1),
  (18,'wrong_reason','misread','审题失误',2),
  (19,'wrong_reason','calc','计算错误',3),
  (20,'wrong_reason','guess','蒙对',4)
ON CONFLICT (id) DO NOTHING;

-- 12.8 商品（MVP 上架，价格占位）
INSERT INTO products (id, code, name, subtitle, type, scope_subjects, original_price_cents, price_cents, duration_days, quota, is_recommended, sort_no, status) VALUES
  (5001,'VIP-SEASON-2027','2027 考季全科题库会员','全题库 + 无限模考 + 错题重练','vip','[1001,1002,1003,2001,2007,2010]', 39900, 19900, 400, '{"mock_exam":-1,"grading":0}', true, 1,'on'),
  (5002,'PKG-FULL-2027',  '2027 全科通关套餐',    '4 科课程 + 题库 + 模考 + 批改','package','[1001,1002,1003,2001,2007,2010]', 198000, 128000, 400,'{"mock_exam":-1,"grading":10}', true, 2,'on'),
  (5003,'COURSE-JGJJ-2027','建设工程经济 精讲班',  '系统精讲 + 章节练习','subject','[1001]', 59900, 29900, 400, '{"mock_exam":5}', false, 3,'on')
ON CONFLICT (id) DO NOTHING;

COMMIT;

-- =====================================================================
-- 附：可选优化（数据量增长后再启用，勿在初始化时执行）
-- =====================================================================
-- 1) practice_items / user_question_state 按 user_id 哈希分区（>5000 万行时）
--    CREATE TABLE practice_items_p ... PARTITION BY HASH (user_id);
-- 2) question_change_logs / audit_logs 按月 RANGE 分区 + 自动归档
-- 3) 题库量大后为 questions 增加 (subject_id, status) 覆盖索引，并在 Meilisearch 侧做向量/语义召回
