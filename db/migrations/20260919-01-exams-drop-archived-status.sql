-- ============================================================================
-- 20260919-01  删除 exams.status 的 'archived'（状态机三态简化）
-- ============================================================================
--
-- 背景（docs/14-状态机审计.md）：`archived` 是 exams 状态里**唯一一个从未被写入过**的值 ——
-- 全仓 grep 写入点 = 0，`can_compose` / `can_publish` / `can_unpublish` 三个标志位
-- 没有一个提到它，只有 `publish_exam` 里一句防御性判断（"已归档的试卷不能发布"）。
--
-- 为什么**删**而不是**补入口**（对比 `users.disabled` 为什么是补入口）：
--
--   `users.disabled`（保留并补入口）      vs   `exams.archived`（删除）
--   ─────────────────────────────────────    ──────────────────────────────────
--   含义独立：登录拦截已实现，是真实运维需求   语义重复：is_deleted 已完整承担"归档"
--   读路径是**功能**                        读路径只是**一句防御**（且 is_deleted 的
--   缺的是入口                               检查在它之前已经生效）
--
-- **判据：语义重复的一律删；含义独立、将来明确要用的才叫"预留"。**
-- `exams.reviewing`（送审）属于后者 —— 含义独立、审核流程是明确的未来批次，保留并标预留。
--
-- 现状核实（本迁移执行前实测）：
--   SELECT status, count(*) FROM exams GROUP BY status;  →  draft=20, published=4
--   即 **0 行** status='archived'，所以清存量那一步是 no-op（保留它以防御意外）。
--
-- 幂等：先 DROP CONSTRAINT IF EXISTS 再 ADD，重复执行结果一致。
-- 回滚：把 'archived' 加回 CHECK 即可（一行）。
-- ============================================================================

-- 1) 防御性清场：万一某个环境有存量（本机实测 0 行）
UPDATE exams SET status = 'off' WHERE status = 'archived';

-- 2) 重建 CHECK 约束（去掉 archived）
ALTER TABLE exams DROP CONSTRAINT IF EXISTS exams_status_check;
ALTER TABLE exams ADD CONSTRAINT exams_status_check
    CHECK (status IN ('draft', 'reviewing', 'published', 'off'));

-- 3) 自检：约束里不该再有 archived；也不该有任何 archived 存量
DO $$
DECLARE
    v_def   text;
    v_count int;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def
      FROM pg_constraint
     WHERE conrelid = 'exams'::regclass AND conname = 'exams_status_check';

    IF v_def IS NULL THEN
        RAISE EXCEPTION '迁移失败：exams_status_check 不存在';
    END IF;
    IF v_def LIKE '%archived%' THEN
        RAISE EXCEPTION '迁移失败：CHECK 里仍有 archived —— %', v_def;
    END IF;
    -- 四个目标值都必须在
    IF v_def NOT LIKE '%draft%' OR v_def NOT LIKE '%reviewing%'
       OR v_def NOT LIKE '%published%' OR v_def NOT LIKE '%off%' THEN
        RAISE EXCEPTION '迁移失败：CHECK 缺少预期取值 —— %', v_def;
    END IF;

    SELECT count(*) INTO v_count FROM exams WHERE status = 'archived';
    IF v_count <> 0 THEN
        RAISE EXCEPTION '迁移失败：仍有 % 行 status=archived', v_count;
    END IF;

    RAISE NOTICE '迁移自检通过：exams.status 允许 draft/reviewing/published/off（archived 已移除）';
END $$;
