-- ============================================================================
-- 20260918-01  试卷破坏性变更的兜底阈值
-- ============================================================================
--
-- 背景（坑 42）：`PUT /admin/exams/{id}` 原先接受 `sections`，而它一旦被传入就会
-- `DELETE FROM exam_questions WHERE exam_id = ...` —— **整卷题目清空**。
-- 一个"看起来很无害"的可选字段携带删除语义，靠"前端别乱传"这种约定是防不住的。
--
-- 结构防护分两层（本迁移是第一层的数据基础）：
--   1. **拆接口**：`sections` 从元数据接口剥离，单独走
--      `PUT /admin/exams/{id}/sections`，且必须显式传 `expected_question_count`。
--   2. **diff 兜底**：任何写接口若导致卷面题数**净减少超过阈值**，
--      拒绝并提示改用 /sections 显式确认。阈值就是本配置项。
--
-- 为什么放 app_configs 而不是写死在代码里：阈值是**运营口径**，
-- 题库规模变了要能调，不该为了改一个数字发一次版。
--
-- 幂等：`ON CONFLICT DO NOTHING` 同时覆盖主键冲突与 config_key 唯一冲突 ——
-- 新库（schema.sql 已插入同一 key）跑本脚本时是 no-op。
-- ============================================================================

INSERT INTO app_configs (id, config_key, config_value, group_name, description, is_public)
VALUES (
    11,
    'exam.mass_question_loss_threshold',
    '10'::jsonb,
    'exam',
    '单次写操作允许净减少的卷面题数上限；超过则拒绝，要求改用 PUT /admin/exams/{id}/sections 显式确认',
    false
)
ON CONFLICT DO NOTHING;

-- 自检：确认配置项存在且可被解析为整数
DO $$
DECLARE
    v jsonb;
BEGIN
    SELECT config_value INTO v FROM app_configs WHERE config_key = 'exam.mass_question_loss_threshold';
    IF v IS NULL THEN
        RAISE EXCEPTION '迁移失败：exam.mass_question_loss_threshold 未写入';
    END IF;
    IF (v #>> '{}')::int < 0 THEN
        RAISE EXCEPTION '迁移失败：阈值必须 >= 0，实际 %', v;
    END IF;
    RAISE NOTICE '迁移自检通过：exam.mass_question_loss_threshold = %', v #>> '{}';
END $$;
