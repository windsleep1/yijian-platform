-- 统计看板 · 趋势（docs/20 §2.2）
--
-- 参数（全部绑定）：
--   `gran`       'day' | 'week' | 'month'   —— 直接当 date_trunc 的第一个参数（PG 里它是 text 形参）
--   `date_from`  `date_to`   'YYYY-MM-DD'（上海日界，闭区间，按**日**过滤明细）
--   `axis_from`  `axis_to`   'YYYY-MM-DD'（已按 gran 对齐：week → 周一，month → 1 号）
--
-- ⚠️ 轴的步长**不传参**，而是按 ``gran`` 在 SQL 里 CASE 出来 ——
--    `interval` 只能靠 `CAST(`x` AS interval)` 绑定，而 SQLAlchemy 会据此把参数
--    推断成 `Interval` 类型，要求传 `timedelta`；可 `timedelta` **表达不了"1 个日历月"**
--    （30 天会逐月漂移）。写成 CASE 既绕开类型推断，又保证月桶对齐到自然月。
--   `subject_id` bigint 或 NULL
--
-- ## ★ 为什么要 generate_series 补空桶
-- 某天没人答题 → GROUP BY 根本**不返回那一行** → 折线图把两天连成直线，
-- 视觉上是"平稳"，实际是"断档"。所以先造完整轴，再 LEFT JOIN。
--   计数型补 0；**比值型与中位数保持 NULL**（前端断线）——
--   补 0 会画出"正确率暴跌到 0"，那是**假象**（docs/20 §2.2）。
--
-- ## ★ 为什么一次返回全部 5 个 metric
-- metric 由 service 从结果里挑一条，而不是拼不同的 SQL：
-- ① 避免动态 SQL（也就没有拼接）；② 五个数来自**同一次快照**，不会互相打架。
--
-- ## ★ subject_id 只作用于"练习/模考"类曲线
-- `new_users`（users 表）没有科目属性，传 subject_id 时它**不受影响** ——
-- 这是有意的：给"新增用户"按科目过滤在语义上不成立，硬过滤会得出一个没有意义的数。

WITH params AS (
    SELECT
        CAST(:date_from AS date)      AS d_from,
        CAST(:date_to   AS date)      AS d_to,
        CAST(CAST(:axis_from AS date) AS timestamp) AS a_from,
        CAST(CAST(:axis_to   AS date) AS timestamp) AS a_to,
        CASE CAST(:gran AS text)
            WHEN 'day'  THEN interval '1 day'
            WHEN 'week' THEN interval '1 week'
            ELSE             interval '1 month'
        END AS a_step
),
axis AS (
    SELECT generate_series(p.a_from, p.a_to, p.a_step) AS bucket FROM params p
),
pi AS (
    SELECT
        date_trunc(CAST(:gran AS text), (pi.answered_at AT TIME ZONE 'Asia/Shanghai')) AS bucket,
        count(*)                                                                      AS answers,
        count(*) FILTER (WHERE pi.is_correct)                                         AS correct,
        count(DISTINCT pi.user_id)                                                    AS active_users,
        -- 可选曲线（不是卡片指标）：**中位数**而不是均值 —— time_ms 是客户端自报、
        -- 右偏严重，均值会被挂机样本拖走。空桶保持 NULL。
        percentile_cont(0.5) WITHIN GROUP (ORDER BY pi.time_ms)                       AS median_time_ms
    FROM practice_items pi
    JOIN questions q ON q.id = pi.question_id
    CROSS JOIN params p
    WHERE pi.answered_at IS NOT NULL
      AND (pi.answered_at AT TIME ZONE 'Asia/Shanghai') >= CAST(p.d_from AS timestamp)
      AND (pi.answered_at AT TIME ZONE 'Asia/Shanghai') <  CAST(p.d_to AS timestamp) + interval '1 day'
      AND (CAST(:subject_id AS bigint) IS NULL OR q.subject_id = CAST(:subject_id AS bigint))
    GROUP BY 1
),
nu AS (
    SELECT
        date_trunc(CAST(:gran AS text), (u.created_at AT TIME ZONE 'Asia/Shanghai')) AS bucket,
        count(*) AS new_users
    FROM users u
    CROSS JOIN params p
    WHERE NOT u.is_deleted
      AND (u.created_at AT TIME ZONE 'Asia/Shanghai') >= CAST(p.d_from AS timestamp)
      AND (u.created_at AT TIME ZONE 'Asia/Shanghai') <  CAST(p.d_to AS timestamp) + interval '1 day'
    GROUP BY 1
),
ea AS (
    SELECT
        date_trunc(CAST(:gran AS text), (ea.submitted_at AT TIME ZONE 'Asia/Shanghai')) AS bucket,
        count(*) AS exam_submits
    FROM exam_attempts ea
    CROSS JOIN params p
    WHERE ea.submitted_at IS NOT NULL
      AND (ea.submitted_at AT TIME ZONE 'Asia/Shanghai') >= CAST(p.d_from AS timestamp)
      AND (ea.submitted_at AT TIME ZONE 'Asia/Shanghai') <  CAST(p.d_to AS timestamp) + interval '1 day'
      AND (CAST(:subject_id AS bigint) IS NULL OR ea.subject_id = CAST(:subject_id AS bigint))
    GROUP BY 1
)
SELECT
    to_char(a.bucket, 'YYYY-MM-DD')                 AS bucket,
    COALESCE(pi.answers, 0)                         AS answers,
    COALESCE(pi.correct, 0)                         AS correct,
    COALESCE(pi.active_users, 0)                    AS active_users,
    COALESCE(nu.new_users, 0)                       AS new_users,
    COALESCE(ea.exam_submits, 0)                    AS exam_submits,
    pi.median_time_ms                               AS median_time_ms
FROM axis a
LEFT JOIN pi ON pi.bucket = a.bucket
LEFT JOIN nu ON nu.bucket = a.bucket
LEFT JOIN ea ON ea.bucket = a.bucket
ORDER BY a.bucket
