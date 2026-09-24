-- 统计看板 · 薄弱知识点（docs/20 §2.5）
--
-- 参数（全部绑定）：`date_from`, `date_to`, `subject_id`, `min_sample`, `limit`
--
-- ## ★ 为什么必须有样本门槛
--   不设门槛时，"错了 1 题"的知识点正确率 = 0%，会**霸榜** ——
--   排行榜会变成"最冷门的知识点"，而不是"最该改的知识点"。
--   默认 min_sample = 20（docs/20 §2.5）。
--
-- ## ★ 为什么按样本量降序做第二排序键
--   正确率相同时，样本多的更可信。只有第一排序键（正确率升序）会让同分项顺序不稳定，
--   翻两次页可能看到不同的榜（对"教研照着改题"是坏事）。
--
-- ## 只返回原始计数（sample / correct），正确率由 service 算
--   —— 与其它端点一致（docs/20 §7-9）。

SELECT
    CAST(kp.id AS text)                       AS kp_id,
    kp.name                                   AS name,
    s.name                                    AS subject,
    count(*)                                  AS sample,
    count(*) FILTER (WHERE pi.is_correct)      AS correct
FROM practice_items pi
JOIN questions q ON q.id = pi.question_id
JOIN knowledge_points kp ON kp.id = q.knowledge_point_id
JOIN subjects s ON s.id = kp.subject_id
WHERE pi.answered_at IS NOT NULL
  AND (pi.answered_at AT TIME ZONE 'Asia/Shanghai') >= CAST(:date_from AS date)
  AND (pi.answered_at AT TIME ZONE 'Asia/Shanghai') <  CAST(:date_to AS date) + interval '1 day'
  AND (CAST(:subject_id AS bigint) IS NULL OR q.subject_id = CAST(:subject_id AS bigint))
  AND kp.is_deleted = false
GROUP BY kp.id, kp.name, s.name
HAVING count(*) >= CAST(:min_sample AS integer)
ORDER BY
    count(*) FILTER (WHERE pi.is_correct) / CAST(count(*) AS double precision) ASC,
    count(*) DESC,
    kp.id ASC
LIMIT CAST(:limit AS integer)
