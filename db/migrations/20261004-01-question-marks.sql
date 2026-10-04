-- ============================================================================
-- 20261004-01  新增 `question_marks`：**用户 × 题目**级的「标记」（C 端批次 2）
-- ============================================================================
--
-- 背景：这个能力在库里**已有两处列**，但**粒度都不对** ——
--
--     practice_items.marked        ← 该表有 session_id ⇒ 是"**这次练习的卷面标记**"
--     exam_attempt_items.marked    ← 同上，考场内的
--   （★ 不写行号：加一张表就会漂 —— 拿列名去 grep 这两位。）
--
-- 危害不是"不够整洁"，而是**同一个事实会有两种说法**：
--     在练习 A 里标记第 3 题 ⇒ 打开练习 B 的**同一道题**，答题卡显示**未标记**（那是另一个 item）；
--     而"错题本筛已标记"只能问"**曾经**标过"（EXISTS）⇒ 两处对同一道题给出**相反**的答案。
-- C 端批次 2 要的判据（错题本可筛 + **跨会话一致**）**强制**题目级语义。
--
-- 为什么**不是**给 `user_question_state` 加一列：
--   那张表的"**有行**"本身就是抽题判据 —— `practice_service._PICK_SQL` 里
--   `ORDER BY (uqs.id IS NOT NULL), q.id`（**未做过的优先**）。
--   为了标记一道**还没答过**的题就得插一行 ⇒ 它立刻被当成"做过的" ⇒ **抽题顺序变了**，
--   而且**不报错**（E2E `p2b1` 依赖"未做过的优先"这条确定性）。
--   ⇒ 宁可新开一张语义单一的表。
--
-- 为什么**不做**成 `favorites` 的一行：`favorites` 的唯一索引是
--   `(user_id, target_type, target_id)` ⇒ 合表就**无法同时对同一道题既收藏又标记**
--   （要么改唯一键、要么把 `folder` 硬塞成状态位 —— 而 `folder` 是分组字段）。
--
-- ⚠️ 三点刻意的选择：
--   1. **不删** `practice_items.marked` / `exam_attempt_items.marked` ——
--      删列是破坏性迁移，不值得冒这个险；它们在"**卷面内**标记"这个语义上将来可能还有用。
--      但**必须写明唯一真相**（已在 `db/schema.sql` 的列注释里标注：
--      **用户级"标记"一律走 `question_marks`**）。
--   2. `subject_id` **冗余存一份**：列表要"按科目筛选"（与错题本同形状），
--      冗余一列换掉每次 join `questions` —— 这个站的数据量下值得。
--   3. **题目软删时标记保留**：`question_id` 只 `REFERENCES questions`；
--      "查列表时过滤掉不可见的题"放在**查询侧**（与错题本同一口径），
--      而不是靠删标记 —— 题目恢复后标记应当还在。
--
-- 幂等：全部 `IF NOT EXISTS` ⇒ **重复执行安全**（`tools/deploy/apply-schema.py` 会重放）
--   ★ 这也是给"往已部署库里补这一条"留的后路：`psql <url> -f 本文件` 直接跑即可
--     （而 `apply-schema.py --force-migrations` 会把 5 条**旧的**一起重放，那几条不保证幂等）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS question_marks (
  id          BIGINT PRIMARY KEY,
  user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id BIGINT      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  subject_id  BIGINT      NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_question_marks ON question_marks(user_id, question_id);
CREATE INDEX IF NOT EXISTS idx_question_marks_user ON question_marks(user_id, created_at DESC);
