-- 统计看板 · 指标卡（docs/20 §2.1 定稿的 5 张）
--
-- 参数（全部**绑定**，无拼接）：
--   `day`   'YYYY-MM-DD' —— **上海日界**的那一天
--
-- ## 日界为什么不写 date_trunc('day', answered_at)
-- 列是 TIMESTAMPTZ；date_trunc 走 **UTC** 日界 → 00:00–08:00 会被算进前一天，
-- "今日"少 8 小时。这里统一用半开区间 [d0, d1)，端点由
-- `CAST(`day` AS date)::timestamp AT TIME ZONE 'Asia/Shanghai'` 算出。
--
-- ## 为什么今日与昨日在同一次扫描里算
-- 分两次查会让两个数字来自**不同时点**（中间有人答题就对不上）。
-- 一次扫描 + FILTER 保证"环比"的两端是**同一个快照**。
--
-- ## 返回
-- 恰好 1 行：5 张卡的今日/昨日原始计数 + 一个给 §7-12 用的告警计数。
-- **比值一律不在这里算** —— 全仓只有 `stats_service._ratio()` 算比值（docs/20 §7-9）。

WITH bounds AS (
    SELECT
        (CAST(CAST(:day AS date) AS timestamp) AT TIME ZONE 'Asia/Shanghai')                    AS d0,
        (CAST(CAST(:day AS date) AS timestamp) AT TIME ZONE 'Asia/Shanghai' + interval '1 day')  AS d1,
        (CAST(CAST(:day AS date) AS timestamp) AT TIME ZONE 'Asia/Shanghai' - interval '1 day')  AS y0,
        (CAST(CAST(:day AS date) AS timestamp) AT TIME ZONE 'Asia/Shanghai' - interval '7 days') AS y7
),

-- 练习明细：[y0, d1) 一次扫描出今日/昨日两套计数
pi AS (
    SELECT
        count(*) FILTER (WHERE t.at >= b.d0)                  AS answers_today,
        count(*) FILTER (WHERE t.at >= b.d0 AND t.ok)         AS correct_today,
        count(*) FILTER (WHERE t.at <  b.d0)                  AS answers_yday,
        count(*) FILTER (WHERE t.at <  b.d0 AND t.ok)         AS correct_yday,
        -- ★ §7-12：窗口内"已作答但没判分"的行。
        -- 这些行会**进正确率的分母、却不进分子**（= 被静默当成答错），
        -- 所以必须在 meta 里显式告警，而不是让它悄悄稀释正确率。
        count(*) FILTER (WHERE t.ok IS NULL)                  AS ungraded_rows
    FROM (
        SELECT pi.answered_at AS at, pi.is_correct AS ok
        FROM practice_items pi
        WHERE pi.answered_at IS NOT NULL
          AND pi.answered_at >= (SELECT y0 FROM bounds)
          AND pi.answered_at <  (SELECT d1 FROM bounds)
    ) t
    CROSS JOIN bounds b
),

-- 模考提交：[y0, d1)；`submitted_at IS NOT NULL` 才算"提交"（doing/expired 不算）
ea AS (
    SELECT
        count(*) FILTER (WHERE t.at >= b.d0) AS submits_today,
        count(*) FILTER (WHERE t.at <  b.d0) AS submits_yday
    FROM (
        SELECT ea.submitted_at AS at
        FROM exam_attempts ea
        WHERE ea.submitted_at IS NOT NULL
          AND ea.submitted_at >= (SELECT y0 FROM bounds)
          AND ea.submitted_at <  (SELECT d1 FROM bounds)
    ) t
    CROSS JOIN bounds b
),

-- DAU = **行为口径**（答题 ∪ 交卷），不是 last_login_at。
-- 用 [y0, d1) 的并集再做去重：`UNION ALL` 后 count(DISTINCT) 天然按人去重。
dau AS (
    SELECT
        count(DISTINCT t.user_id) FILTER (WHERE t.at >= b.d0) AS today,
        count(DISTINCT t.user_id) FILTER (WHERE t.at <  b.d0) AS yday
    FROM (
        SELECT pi.user_id AS user_id, pi.answered_at AS at
        FROM practice_items pi
        WHERE pi.answered_at IS NOT NULL
          AND pi.answered_at >= (SELECT y0 FROM bounds)
          AND pi.answered_at <  (SELECT d1 FROM bounds)
        UNION ALL
        SELECT ea.user_id, ea.submitted_at
        FROM exam_attempts ea
        WHERE ea.submitted_at IS NOT NULL
          AND ea.submitted_at >= (SELECT y0 FROM bounds)
          AND ea.submitted_at <  (SELECT d1 FROM bounds)
    ) t
    CROSS JOIN bounds b
),

-- 新增用户：[y0, d1) 给今日/昨日，额外给 [y7, d0) 供"近 7 日均值"的副行
nu AS (
    SELECT
        count(*) FILTER (WHERE t.at >= b.d0) AS today,
        count(*) FILTER (WHERE t.at <  b.d0) AS yday,
        count(*)                             AS prev7
    FROM (
        SELECT u.created_at AS at
        FROM users u
        WHERE NOT u.is_deleted
          AND u.created_at >= (SELECT y7 FROM bounds)
          AND u.created_at <  (SELECT d1 FROM bounds)
    ) t
    CROSS JOIN bounds b
)

SELECT
    (SELECT d0 FROM bounds)                AS window_start,
    (SELECT d1 FROM bounds)                AS window_end,
    nu.today                               AS new_users_today,
    nu.yday                                AS new_users_yday,
    nu.prev7                               AS new_users_prev7,
    dau.today                              AS dau_today,
    dau.yday                               AS dau_yday,
    pi.answers_today                       AS answers_today,
    pi.correct_today                       AS correct_today,
    pi.answers_yday                        AS answers_yday,
    pi.correct_yday                        AS correct_yday,
    pi.ungraded_rows                       AS ungraded_rows,
    ea.submits_today                       AS exam_submits_today,
    ea.submits_yday                        AS exam_submits_yday
FROM pi
CROSS JOIN ea
CROSS JOIN dau
CROSS JOIN nu
