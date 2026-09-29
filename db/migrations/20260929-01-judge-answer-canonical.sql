-- ============================================================================
-- 20260929-01  判断题的 `answer` 归一到**规范形式**（`{"value": [true|false]}`）
-- ============================================================================
--
-- 背景（坑 76 / BL-20）——**同一个库里判断题有两套写法**，而且都叫 `type='judge'`：
--
--     管理端（question_service.derive_answer）  → {"value": [true]}
--     种子生成器 + 导入管道                      → {"value": ["A"]}   （A = 表述正确）
--
-- 危害不是"不够整洁"，而是**每个读取方只能靠类型强制去猜**：
--     bool("A") == True  ⇒  **答错的判断题会被判成对**（不报错、还给满分）。
-- C 端判分第一版就是这么写的（2026-09-29 实证）。
--
-- 规范形式的定义在 `apps/api/app/schemas/answer.py`（**唯一入口**）：
--   judge → `{"value": [true|false]}`；single/multiple → `{"value": ["A","C"]}`；
--   case → `{"value": "<文本>"}`；case_sub/fill/essay → `{"value": ["<文本>"]}`。
--
-- 为什么 `judge` 取布尔而不是 `"A"/"B"`：布尔**自描述**。
-- 取 `["A"]` 的话，"A 表示正确"这条语义**不在数据里**（种子的判断题甚至没有 options），
-- 每个读取方都得自带一张映射表 —— 那正是这次事故的形状。
--
-- ⚠️ 三点刻意的选择：
--   1. **只动 `questions.answer`，不动 `question_versions.snapshot`** ——
--      快照是**历史记录**（"当时是什么"），改它等于篡改审计链。
--      已确认全仓**没有**"回滚到某个版本"的写路径，所以旧快照不会被写回 `questions`。
--   2. **幂等**：`WHERE` 只命中 `answer->'value'->>0` ∈ {A,B} 的行；跑第二遍是 0 行。
--   3. **不猜第三种写法**：只认 `A` / `B`（生成器与导入管道只产出这两个）。
--      真出现别的写法时**不静默放过** —— 由数据级用例
--      `apps/api/tests/test_answer_format.py` 报出来（它扫全库）。
--
-- 回填方向（A → true）的依据**不是我猜的**：`import_service` 在建判断题选项时
-- 写的就是 `{"label": "A", "content": "正确"}` / `{"label": "B", "content": "错误"}`。

UPDATE questions
   SET answer = jsonb_build_object('value', jsonb_build_array(upper(answer->'value'->>0) = 'A')),
       updated_at = now()
 WHERE type = 'judge'
   AND jsonb_typeof(answer -> 'value') = 'array'
   AND upper(answer -> 'value' ->> 0) IN ('A', 'B');
