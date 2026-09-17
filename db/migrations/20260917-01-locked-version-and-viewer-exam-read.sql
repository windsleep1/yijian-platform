-- =====================================================================
-- Batch 7 前置修正
--   1) exam_questions 增加 locked_version 列并回填
--   2) viewer 角色补 exam:read（不新建角色）
--
-- 幂等：可重复执行（ADD COLUMN IF NOT EXISTS / ON CONFLICT DO NOTHING）。
--
-- 背景
-- ----
-- Batch 7 Pass 1 的版本锁定暂存在 `exams.rule_config.question_locks`（JSONB）。
-- 事后判定"不改 schema"是**过度约束**：版本锁定是试卷的核心语义，不该挤在
-- 一个描述"答题行为"的 JSONB 里。本迁移只**加列 + 回填**，
-- **不删除** `rule_config.question_locks`（留一次回滚余地，下一批再清理）。
--
-- 为什么必须单独有一个迁移文件（而不是只改 db/schema.sql）
-- --------------------------------------------------------
-- `run-smoke.ps1` 只在**首次建库**（`to_regclass('public.users')` 为空）时载入
-- schema.sql；库已存在时**整段跳过**。所以对已有数据的库，改 schema.sql 是**不生效**的。
--
-- 注意：`?` 是 jsonb 的"键存在"操作符，会和参数占位符混淆 ——
-- 这里统一用 `jsonb_exists()` 函数形式，便于任何工具直接执行。
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------- 1. 加列

ALTER TABLE exam_questions
  ADD COLUMN IF NOT EXISTS locked_version INTEGER;

COMMENT ON COLUMN exam_questions.locked_version IS
  '发布试卷时锁定的题目版本。已发布/已考过的卷面按此版本作答与展示；未发布为 NULL；重新组卷清空。（Batch 7）';

-- ---------------------------------------------------------------- 2. 回填

-- 把 exams.rule_config.question_locks 的 {question_id: version} 铺回每一行。
-- 只填 locked_version IS NULL 的行，重复执行不会覆盖已有值。
UPDATE exam_questions eq
SET locked_version = (e.rule_config -> 'question_locks' ->> eq.question_id::text)::int
FROM exams e
WHERE e.id = eq.exam_id
  AND eq.locked_version IS NULL
  AND jsonb_exists(e.rule_config, 'question_locks')
  AND jsonb_exists(e.rule_config -> 'question_locks', eq.question_id::text)
  AND (e.rule_config -> 'question_locks' ->> eq.question_id::text) ~ '^[0-9]+$';

-- ---------------------------------------------------------------- 3. viewer + exam:read

-- 不新建角色：viewer 定位是"通用只读岗"，补一个新的只读场景就往这里加。
-- 有了这条，才能复现「能看试卷列表/详情，但『发布』按钮置灰」这个 B 端状态。
INSERT INTO role_permissions (role_id, permission_id)
SELECT 7, id FROM permissions WHERE code = 'exam:read'
ON CONFLICT DO NOTHING;

COMMIT;

-- ---------------------------------------------------------------- 自检

-- 期望：locked_version 列存在；viewer 有 exam:read 但没有 exam:publish
--   SELECT count(*) FROM exam_questions WHERE locked_version IS NOT NULL;
--   SELECT r.code, p.code FROM role_permissions rp
--     JOIN roles r ON r.id = rp.role_id JOIN permissions p ON p.id = rp.permission_id
--    WHERE r.code = 'viewer' AND p.module = 'exam';
