-- 统计看板 · 数据体检（docs/20 §7 判据 12）
--
-- 参数：无
--
-- ## ★ 为什么需要它
-- 「正确率」的分子与分母来自**同一张表的两列**（answered_at / is_correct），
-- schema 允许它们**不一致**：
--     answered_at IS NOT NULL AND is_correct IS NULL   → 答了没判分
--     is_correct  IS NOT NULL AND answered_at IS NULL  → 判了没有作答时间
-- 第一种会让正确率**静默偏低**（算进分母、不算进分子），而**一条错都不会报**。
-- 所以它是**数据缺陷**，必须以"能被断言为 0"的形式暴露出来，而不是靠人看数字猜。
--
-- 返回恰好 1 行、两列，都是"应为 0"的计数。

SELECT
    count(*) FILTER (WHERE pi.answered_at IS NOT NULL AND pi.is_correct IS NULL) AS ungraded_rows,
    count(*) FILTER (WHERE pi.is_correct IS NOT NULL AND pi.answered_at IS NULL) AS graded_no_time_rows
FROM practice_items pi
