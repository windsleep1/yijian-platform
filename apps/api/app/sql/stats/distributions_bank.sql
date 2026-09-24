-- 统计看板 · 分布 · **题库结构**（真数据，docs/20 §2.3 的 bank 视图）
--
-- 参数（全部绑定）：`dim`, `subject_id`
--   `dim` ∈ subject | professional | difficulty | type
--
-- ## ★ 这是**真数据**，不是造数
--   源表 `questions` / `subjects` **现在就有真实数据**（题库已灌 6000+ 题），
--   所以它的 `data_origin = 'real'`（由 service 按 view 固定给出，见 docs/20 §3.2）。
--   同一屏上还有 `practice` 视图（造数）——**两者必须在图上分开标注**。
--
-- ## ★ 口径：`is_deleted = false` **且** `status = 'published'`
--   实测（2026-09-24，本机库）：published ∧ ¬deleted = 6078；而 published 一共 6213 ——
--   差的 135 条是"**已发布但已删除**"。只按 status 过滤会把这 135 条算进"题库有多少题"。
--   "题库结构"问的是**上线可用的题**，所以两个条件都要。
--
-- ## ★ professional 的展示名
--   `subjects.professional` 是短码（jz / mhjc / sz …），当前**没有** dict_type='professional'
--   的字典（实测只有 question_type / difficulty / source_type / wrong_reason 四种）。
--   所以标签优先取字典，取不到退化为**该专业课自己的 name**（人事上"专业课名"就是那个方向的名字），
--   再取不到才显短码。没有硬编码任何码表 —— 码表该由字典表提供，不该由 SQL 编。

WITH dl AS (
    SELECT
        q.subject_id    AS subject_id,
        s.name          AS subject_name,
        s.professional  AS prof_code,
        q.difficulty    AS difficulty,
        q.type          AS qtype
    FROM questions q
    JOIN subjects s ON s.id = q.subject_id
    WHERE q.is_deleted = false
      AND q.status = 'published'
      AND (CAST(:subject_id AS bigint) IS NULL OR q.subject_id = CAST(:subject_id AS bigint))
)
SELECT
    CASE CAST(:dim AS text)
        WHEN 'subject'      THEN CAST(dl.subject_id AS text)
        WHEN 'professional' THEN COALESCE(dl.prof_code, '__none__')
        WHEN 'difficulty'   THEN CAST(dl.difficulty AS text)
        ELSE dl.qtype
    END AS key,
    min(
        CASE CAST(:dim AS text)
            WHEN 'subject'      THEN dl.subject_name
            WHEN 'professional' THEN COALESCE(dp.dict_label, dl.subject_name, '未分类')
            WHEN 'difficulty'   THEN COALESCE(dd.dict_label, CAST(dl.difficulty AS text))
            ELSE COALESCE(dt.dict_label, dl.qtype)
        END
    ) AS label,
    count(*) AS value,
    NULL     AS correct
FROM dl
LEFT JOIN dictionaries dp ON dp.dict_type = 'professional'  AND dp.dict_key = dl.prof_code
LEFT JOIN dictionaries dd ON dd.dict_type = 'difficulty'    AND dd.dict_key = CAST(dl.difficulty AS text)
LEFT JOIN dictionaries dt ON dt.dict_type = 'question_type' AND dt.dict_key = dl.qtype
GROUP BY 1
ORDER BY value DESC, key
