-- 统计看板 · 分布 · **作答分布**（造数，docs/20 §2.3 的 practice 视图）
--
-- 参数（全部绑定）：`dim`, `date_from`, `date_to`, `subject_id`
--
-- ## ★ 这是**造数**（data_origin = 'demo'）
--   `practice_items` 现在一行都没有（C 端未落地）。图上必须标"演示数据"。
--
-- ## ★ 与 bank 视图**不可互相替代**
--   题库里"难"的题占 20%，**不代表**用户作答里难的只占 20%（人天然回避难题）。
--   所以这张图问的是"用户练了哪些"，不是"我们有多少题"。
--
-- ## ★ professional 在这里是"**人的**专业"
--   bank 视图用的是 `subjects.professional`（**课**的专业）。
--   这里用 `user_profiles.professional`（**人**的专业）—— 运营关心"哪个方向的人练得多"。
--   两个视角名字不同、来源不同，**混用会得出互相矛盾的结论**（docs/20 §2.3）。
--
-- ## 返回 `correct` 与 `value` 两个**原始计数**，比值由 service 统一算
--   （docs/20 §7-9「口径单一」：全仓只有 `stats_service._ratio()` 会做除法）

WITH dl AS (
    SELECT
        pi.is_correct             AS ok,
        q.subject_id              AS subject_id,
        s.name                    AS subject_name,
        q.difficulty              AS difficulty,
        q.type                    AS qtype,
        up.professional           AS user_prof
    FROM practice_items pi
    JOIN questions q ON q.id = pi.question_id
    JOIN subjects s ON s.id = q.subject_id
    LEFT JOIN user_profiles up ON up.user_id = pi.user_id
    WHERE pi.answered_at IS NOT NULL
      AND (pi.answered_at AT TIME ZONE 'Asia/Shanghai') >= CAST(:date_from AS date)
      AND (pi.answered_at AT TIME ZONE 'Asia/Shanghai') <  CAST(:date_to AS date) + interval '1 day'
      AND (CAST(:subject_id AS bigint) IS NULL OR q.subject_id = CAST(:subject_id AS bigint))
)
SELECT
    CASE CAST(:dim AS text)
        WHEN 'subject'      THEN CAST(dl.subject_id AS text)
        WHEN 'professional' THEN COALESCE(dl.user_prof, '__none__')
        WHEN 'difficulty'   THEN CAST(dl.difficulty AS text)
        ELSE dl.qtype
    END AS key,
    min(
        CASE CAST(:dim AS text)
            WHEN 'subject'      THEN dl.subject_name
            WHEN 'professional' THEN COALESCE(dp.dict_label, dl.user_prof, '未分类')
            WHEN 'difficulty'   THEN COALESCE(dd.dict_label, CAST(dl.difficulty AS text))
            ELSE COALESCE(dt.dict_label, dl.qtype)
        END
    ) AS label,
    count(*)                                  AS value,
    count(*) FILTER (WHERE dl.ok)             AS correct
FROM dl
LEFT JOIN dictionaries dp ON dp.dict_type = 'professional'  AND dp.dict_key = dl.user_prof
LEFT JOIN dictionaries dd ON dd.dict_type = 'difficulty'    AND dd.dict_key = CAST(dl.difficulty AS text)
LEFT JOIN dictionaries dt ON dt.dict_type = 'question_type' AND dt.dict_key = dl.qtype
GROUP BY 1
ORDER BY value DESC, key
