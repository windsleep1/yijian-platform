-- 统计看板 · 漏斗（docs/20 §2.4）
--
-- 参数（全部绑定）：
--   `cohort_from`  'YYYY-MM-DD' 或 NULL（NULL = 全部时间）
--   `cohort_to`    'YYYY-MM-DD'
--
-- ## ★ 必须是「同一批人」的留存式漏斗
--   不是"三天的独立日活相减"。三段都作用在**同一个用户集合**（按注册日切的队列）上：
--     ① 注册      = 队列内 created_at 落在该区间的人
--     ② 首次答题  = 其中 min(answered_at) 存在的人
--     ③ 付费      = 其中存在 status='paid' 订单的人
--   所以这个查询里三段是**同一张 base 表的 LEFT JOIN**，不是三个独立 count()。
--
-- ## ★ ② 的判据是 practice_items.answered_at 的 min，不是"有 practice_sessions"
--   开了会话没答题是极常见的行为；用"有会话"会让第一段转化率**虚高**。
--
-- ## 返回值恰好 1 行（三段的绝对人数）；转化率由 service 算
--   （pp / 百分比都是"比值"，全仓只在 `_ratio()` 里做）

WITH cohort AS (
    SELECT
        u.id AS user_id
    FROM users u
    WHERE NOT u.is_deleted
      AND (CAST(:cohort_from AS date) IS NULL
           OR (u.created_at AT TIME ZONE 'Asia/Shanghai') >= CAST(:cohort_from AS date))
      AND (u.created_at AT TIME ZONE 'Asia/Shanghai') <  CAST(:cohort_to AS date) + interval '1 day'
),
first_answer AS (
    SELECT pi.user_id
    FROM practice_items pi
    WHERE pi.answered_at IS NOT NULL
      AND EXISTS (SELECT 1 FROM cohort c WHERE c.user_id = pi.user_id)
    GROUP BY pi.user_id
),
paid AS (
    SELECT DISTINCT o.user_id
    FROM orders o
    WHERE o.status = 'paid'
      AND o.is_deleted = false
      AND EXISTS (SELECT 1 FROM cohort c WHERE c.user_id = o.user_id)
)
SELECT
    count(*)                                                              AS registered,
    count(fa.user_id)                                                     AS first_answered,
    count(pd.user_id)                                                     AS paid
FROM cohort c
LEFT JOIN first_answer fa ON fa.user_id = c.user_id
LEFT JOIN paid pd ON pd.user_id = c.user_id
