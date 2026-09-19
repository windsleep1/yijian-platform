-- ============================================================================
-- 20260919-02  dictionaries.status 补 CHECK 约束
-- ============================================================================
--
-- 背景（docs/14-状态机审计.md P2）：`dictionaries.status` 是**全库唯一一张
-- 有 status 列却没有 CHECK 约束的表**（只有 `DEFAULT 'on'`）。
-- 其余 28 张都有 `CHECK (status IN (...))`。
--
-- 为什么值得顺手修：没有约束意味着**任何字符串都能写进去** ——
-- 将来某个接口拼错一个值（比如写成了 `enabled`），数据库不会拦，
-- 而所有读路径都按 `status='on'` 过滤 → 那条字典**静默消失**，没有任何报错。
-- 「能被写坏而无人发现」比「写不进去」糟糕得多。
--
-- 取值来源：与 `subjects` / `banners` / `paper_rules` 等其它"启用位"表保持一致，
-- 统一用 `on` / `off`（**不要**引入 `active` —— 全库有 20 多张表用 on/off，
-- 新造一个同义词只会让下一个写代码的人选错）。
--
-- 现状核实（本迁移执行前实测）：SELECT DISTINCT status FROM dictionaries;  →  只有 'on'
--
-- 幂等：DROP CONSTRAINT IF EXISTS + ADD。
-- 回滚：DROP CONSTRAINT dictionaries_status_check。
-- ============================================================================

-- 1) 防御性清场：把任何不在允许集合里的值归一到 'on'
--    （本机实测只有 'on'，这一步是 no-op；留着是为了别的环境不炸）
UPDATE dictionaries SET status = 'on' WHERE status NOT IN ('on', 'off');

-- 2) 补约束
ALTER TABLE dictionaries DROP CONSTRAINT IF EXISTS dictionaries_status_check;
ALTER TABLE dictionaries ADD CONSTRAINT dictionaries_status_check
    CHECK (status IN ('on', 'off'));

-- 3) 自检
DO $$
DECLARE
    v_def text;
    v_bad int;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def
      FROM pg_constraint
     WHERE conrelid = 'dictionaries'::regclass AND conname = 'dictionaries_status_check';

    IF v_def IS NULL THEN
        RAISE EXCEPTION '迁移失败：dictionaries_status_check 未创建';
    END IF;
    IF v_def NOT LIKE '%on%' OR v_def NOT LIKE '%off%' THEN
        RAISE EXCEPTION '迁移失败：约束取值不对 —— %', v_def;
    END IF;

    SELECT count(*) INTO v_bad FROM dictionaries WHERE status NOT IN ('on', 'off');
    IF v_bad <> 0 THEN
        RAISE EXCEPTION '迁移失败：仍有 % 行 status 不在 on/off 内', v_bad;
    END IF;

    RAISE NOTICE '迁移自检通过：dictionaries.status 已约束为 on/off';
END $$;
