# Batch 7：组卷引擎 —— 方案与验收（Pass 1 后端 + 前置修正）

> 目标：让教研能把题库变成**一份可发布的试卷**，并且这份卷子在发布之后
> **不可被后来的改题悄悄改变**。
>
> 本批拆两个 Pass：**Pass 1 后端 + 测试**（已交付验收）→ **前置修正**（§1.2，三项）→
> **Pass 2 试卷管理前端**（`/exams`、`/exams/new`、`/exams/[id]`、`/paper-rules`，待开工）。
> 本文覆盖 Pass 1 与前置修正；Pass 2 完成后在文末追加其章节。
>
> **当前状态**：后端 **41 个接口**，`run-smoke.ps1` **78 passed, 1 skipped**（无回归）。

前置：Batch 4 的题库 CRUD、Batch 5 的导入管道（题库里已有 6000 道已发布仿真题）。

---

## 1. 范围与交付物

### 1.1 接口（12 个，全部新增）

| # | 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|---|
| ① | GET | `/admin/paper-rules` | `exam:read` | 组卷规则列表（科目/状态/类型筛选） |
| ② | POST | `/admin/paper-rules` | `exam:create` | 新建规则 |
| ③ | PUT | `/admin/paper-rules/{id}` | `exam:create` | 编辑规则（部分更新） |
| ④ | DELETE | `/admin/paper-rules/{id}` | `exam:create` | 删除规则（**硬删除**，见 §3.4） |
| ⑤ | GET | `/admin/exams` | `exam:read` | 试卷列表（科目/类型/状态/关键词） |
| ⑥ | POST | `/admin/exams` | `exam:create` | 创建试卷（含卷面分段） |
| ⑦ | POST | `/admin/exams/{id}/auto-compose` | `exam:create` | **规则自动组卷** |
| ⑧ | POST | `/admin/exams/{id}/validate` | `exam:read` | **卷面校验**（只读） |
| ⑨ | POST | `/admin/exams/{id}/publish` | `exam:publish` | **发布 + 版本锁定** |
| ⑩ | GET | `/admin/exams/{id}` | `exam:read` | 试卷详情（分段 + 题目 + 锁定版本） |
| ⑪ | PUT | `/admin/exams/{id}` | `exam:create` | 编辑试卷 |
| ⑫ | DELETE | `/admin/exams/{id}` | `exam:create` | **归档（软删除）** |

> 原需求列了 9 个。多出来的 ⑤（试卷列表）、⑪ 的完整语义是 Pass 2 前端必需的
> —— 列表页要有数据来源，编辑页要有落点；⑫ 与 Batch 4 的题目软删除对称（见 §3.5）。
> 没有超出"组卷引擎"的范围。

后端接口总数：**29 → 41**。

### 1.2 前置修正（Pass 2 开工前，独立的 `chore` 提交）

Pass 1 收口时上报了三件"必须先在 Pass 2 之前解决"的事，需求方拍板后已落地：

| # | 事项 | 处理 |
|---|---|---|
| 1 | **版本锁定挤在 JSONB 里** | 加独立列 + 回填 + 代码切换（§3.3） |
| 2 | **无法表达"试卷只读"** | 给已有 `viewer` 角色补 `exam:read`，**不新建角色**（§3.6） |
| 3 | **试卷不能归档** | 新增 `DELETE /admin/exams/{id}` 软删除（§3.5） |

### 1.3 文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `apps/api/app/schemas/admin_exam.py` | 430 | 入参/出参、`RuleItem`、`Shortfall`、校验器 |
| `apps/api/app/services/exam_service.py` | 1474 | 业务与算法（**不 import fastapi**） |
| `apps/api/app/api/v1/admin_exams.py` | 400 | 11 个路由 |
| `apps/api/tests/test_admin_v7.py` | 830 | 21 条用例 |
| `apps/api/app/api/router.py` | +3 | 挂载 |

**复用既有表，零 schema 改动**：`paper_rules` / `exams` / `exam_sections` / `exam_questions`。

---

## 2. 组卷算法（`docs/05 §3.2`）

### 2.1 逐级放宽，每步都留痕

每条规则独立执行，最多三步：

| 步 | 保留的约束 | 对应文档措辞 |
|---|---|---|
| 1 `exact` | 题型 + 难度 + 知识点/章节 + 年份 | "先按 (type, difficulty, kp) 精确匹配" |
| 2 `relax_difficulty` | 题型 + 知识点/章节 + 年份 | "不足则放宽难度范围" |
| 3 `relax_scope` | 只剩题型 | "仍不足则放宽知识点" |

**只在该约束真的存在时才加那一档**：规则没写 `difficulty`，就不会凭空多出一个
"放宽难度"的档位（否则 `reason` 里会出现一个 0 候选的假档位，误导排查）。
这一条由 `relaxation_steps()` 保证，并有用例
`test_relaxation_ladder_follows_docs_05` 覆盖。

### 2.2 缺口 `shortfalls`：绝不静默凑数

放宽到底仍不够，回传：

```json
{
  "rule": {"type":"case","count":100,"score":2,"difficulty":[5,5]},
  "rule_label": "case · 100 题 · 难度 5~5",
  "rule_index": 0,
  "question_type": "case",
  "need": 100, "got": 27, "missing": 73,
  "reason": "该规则需要 100 道，实际只有 27 道，缺 73 道。已逐级放宽（exact=0，relax_difficulty=27）后仍不足。系统**不会用其它题目顶替**，请补充题库、放宽规则或手工加题。"
}
```

四个必须成立的性质：

1. **`got + missing == need`** —— 数字自洽，不糊。
2. **`rule` 是原始规则对象**（不是字符串）—— 前端才能把缺口对回具体约束（照 `docs/05` 的示例形状）。
3. **写进卷面的题数 == `got`**，不是 `need`。这是"不静默凑数"的**唯一硬指标**，
   用例 `test_shortfall_is_reported_and_never_silently_filled` 直接断言库中实际挂题数。
4. **缺口持久化**到 `rule_config.shortfalls`，详情页刷新后仍能看到警告。

### 2.3 加权采样：优先"从没考过"的题

```
w = 4.0                     若 usage_count == 0（从未进过任何试卷）
w = 1 / (1 + usage_count)   否则
```

按权重**无放回**抽样。`usage_count` 来自 `exam_questions` 的实时聚合，不额外维护计数列
（避免"计数漂移"这一整类 bug）。

不带权重的话，大题库里反复抽到同一批题是**必然事件**而非偶然 —— 权重是唯一解。

`seed` 参数让"同一种子 + 同一份库 → 同一张卷"，验收与排障都需要这个性质。

### 2.4 同卷不重复

`exam_questions` 有 `uq_exam_question(exam_id, question_id)` 唯一索引兜底；
组卷时还用 `picked_ids` 做跨规则排除（同一份卷里两条"单选"规则抽出的题必须互不重叠）。

---

## 3. 卷面校验、发布、版本锁定

### 3.1 校验的两级问题

**error（阻止发布）**

| code | 含义 |
|---|---|
| `NO_QUESTIONS` | 卷面一道题都没有 |
| `SECTION_NOT_FILLED` | 分段计划题数 ≠ 实际挂题数 |
| `SECTION_SCORE_MISMATCH` | 分段分值 ≠ 计划题数 × 每题分 |
| `TOTAL_SCORE_MISMATCH` / `TOTAL_COUNT_MISMATCH` | 试卷登记的总分/题量与卷面实际不符 |
| `DUPLICATE_QUESTION` | 同一题重复出现 |
| `QUESTION_NOT_PUBLISHED` | 卷面含草稿等非"已发布"题目 |
| `QUESTION_DELETED` | 卷面含已归档题目 |

**warning（允许发布，仅提示）**：`COMPOSE_SHORTFALL`、`NO_PASS_SCORE`、`VERSION_DRIFT`

**`SECTION_NOT_FILLED` 刻意算 error 而不是 warning。** 分段就是卷面的契约：
"计划 60 道只放了 52 道"就发出去，考生拿到的卷子和教研以为的不是同一张。
要发这种卷，就把分段题数改成 52 —— 那等于**明确认可**"这张卷就是 52 道"。
这跟 Batch 5「有错行默认整批不写、要跳行必须显式开 `allow_partial`」是同一种思路：
**默认站在严格一侧，宽松必须显式。**

### 3.2 发布 = 先校验、再锁版本、后改状态

```
publish:
  ├─ 状态检查（已发布 / 已归档 → 40901）
  ├─ ★ 强制 validate，有 error → 40901，整批不落库
  ├─ 把每道题的 questions.version 写进 rule_config.question_locks
  └─ status='published', published_at=now()
```

顺序重要：校验不通过时**一行都不改**（不 commit，直接抛 `40901`）。
用例 `test_publish_blocked_when_validation_fails` 断言失败后 `status` 仍是 `draft`、
`published_at` 仍为 `null`。

### 3.3 版本锁定：落在独立列 `exam_questions.locked_version`

**主存储是独立列。** 发布时一条 UPDATE 把每道题的当前版本写进卷面行：

```sql
UPDATE exam_questions eq SET locked_version = q.version
FROM questions q
WHERE q.id = eq.question_id AND eq.exam_id = :eid
```

读取时 `_question_locks()` **优先读列**；该列为 NULL 才回退到 JSONB：

```python
locked = r.get("locked_version")          # 列
if locked is None:
    locked = fallback.get(str(qid))       # rule_config.question_locks（deprecated）
```

回退分支不是冗余 —— 它覆盖"**迁移已执行但代码先上线**"的时间窗，
以及未来可能出现的未回填历史行。**有了它，删掉 JSONB 才不会有行为变更。**

**关键：锁定不是"记个数字"，而是真的用锁定版本。**
`get_exam_detail` 发现 `locked_version != current_version` 时，
会去 `question_versions` 取**当时那一版的题干快照**来展示：

```python
if drift:
    snap = snapshots.get((qid, locked))
    if snap and snap.get("stem"):
        stem = snap["stem"]        # ★ 用锁定版本的题干，不是被改过的当前版本
```

用例 `test_publish_locks_version_and_later_edit_does_not_change_paper` 端到端验证：
发布 → 改题（v1→v2）→ 已发布试卷仍显示**原始题干**、
`locked_version=1` / `current_version=2` / `version_drift=true`。

#### ⚠️ 一段被推翻的取舍（留痕）

Pass 1 最初的实现把锁定挤在 `exams.rule_config.question_locks`（JSONB）里，
理由是当时约定"不改 schema"。这个约束**事后被判定为过度约束** ——
版本锁定是试卷的核心语义，不该寄居在一个用来描述"答题行为"（随机顺序 / 单题限时）的字段里。

当时的自审已经把代价列清楚了（它们正是后来要求改的理由）：

| 问题 | 说明 |
|---|---|
| 语义被撑宽 | `rule_config` 同时装了"答题行为 + 版本锁定 + 组卷缺口"三件事 |
| 读-改-写 | 组卷写 `shortfalls`、发布写 `question_locks`，必须读-改-写才不会互相覆盖（`_merge_rule_config`） |
| 查询/索引差 | JSONB 里存映射，没法给"某道题被哪些卷锁定"建索引 |
| 规模上限 | 100 道题的映射没问题；单卷上千题时 JSONB 会变重 |

**现状（已修正）**：

| 项 | 状态 |
|---|---|
| `exam_questions.locked_version` | ✅ 已加（`db/migrations/20260917-01-*.sql`，含回填） |
| 读路径 | ✅ 优先列，JSONB 仅作回退 |
| 写路径 | ⚠️ **双写**（列 + JSONB）—— 保留一次回滚余地 |
| `rule_config.question_locks` | ⚠️ **deprecated**，标记保留，**下一批清理** |
| 详情页取 `question_versions` 快照 | ✅ 保留（那是真需要的，与存储位置无关） |

> **为什么下批才删 JSONB**：代码与迁移不一定同时上线。若立刻停写 JSONB，
> 一旦需要回滚到 Pass 1 那版代码，锁定就会"凭空消失"（旧代码只读 JSONB）。
> 一个版本的双写成本极低，换的是"回滚不丢数据"。
>
> 清理时要做的事：删 `_merge_rule_config(question_locks=...)` 的写入、
> 删 `_question_locks()` 的回退分支、把 `rule_config.question_locks` 从存量行里移除。

用例 `test_locked_version_is_written_to_column_and_mirrored_to_jsonb` 把两件事都钉住了：
列被填上且等于题目 version、JSONB 的键与值与列**逐条一致**、重新组卷会把列清空。

### 3.5 试卷归档（软删除）

`DELETE /admin/exams/{id}` —— 与 Batch 4 的题目软删除**同一套语义**：

| 行为 | 说明 |
|---|---|
| 置 `exams.is_deleted = true` | 只动这一列 |
| 题目 | **不动**。题目是题库的资产，卷子只是"引用"了它们 |
| 卷面 | **不删**。`exam_questions` 保留（版本锁定也保留），重新启用后卷面还在 |
| 列表 | 默认过滤掉；`include_deleted=true` 带出来 |
| 详情 | **仍可打开**（带 `is_deleted=true`），不是 404 —— 归档的卷要能被查看与审计 |
| 按钮 | `can_edit` / `can_compose` / `can_publish` / `can_delete` 全为 false |
| 留痕 | 写 `content_change_logs`（action=delete，before/after 含 `is_deleted` 变化） |
| 重复归档 | `40001`（不静默成功） |

**权限复用 `exam:create`** 而不是新增 `exam:delete`：权限码是跨前后端的契约，
加一条要同时改种子与前端常量；而"能建卷的人能归档自己的卷"是合理的权责边界。
若将来需要"只读 + 不能删"的划分，再拆。

⚠️ **没有"取消归档"接口**（本批不做，见 §7 遗留事项）。

### 3.6 `viewer` 只读岗补 `exam:read`（不新建角色）

`viewer` 的定位是**通用只读岗**：哪个模块需要演示"能看不能改"，
就往它身上加那个模块的 `:read`。本批加 `exam:read`，于是：

| 能做 | 不能做 |
|---|---|
| 看试卷列表（`GET /admin/exams`） | 建卷 / 改卷（`exam:create` → `40301`） |
| 看试卷详情、卷面分段与题目 | 自动组卷（`exam:create` → `40301`） |
| 跑卷面校验（`GET` 语义，只读） | **发布**（`exam:publish` → `40301`） |
| 看组卷规则列表 | 归档试卷（`exam:create` → `40301`） |

**为什么必须这么做**：`exam` 模块的四条权限（read/create/publish/grade）是
**按模块整包**发给角色的 —— `super_admin` / `admin` / `researcher` / `teacher`
四个角色拿到的 exam 权限**完全一样**。所以「能看试卷、但发布按钮置灰」
这个状态**没有任何内置角色能复现**（同 Batch 3 的 `viewer` 缺口、见坑 36）。

403 响应里会点明缺哪个权限（`没有操作权限（需要：exam:publish）`），
前端 tooltip 直接用这句，不用自己拼文案。

用例 `test_viewer_can_read_but_not_publish` 覆盖全部读写分支，
并断言 `viewer` 的 `/auth/me` 里**有 `exam:read`、没有 `exam:publish` / `exam:create`**。

### 3.7 `paper_rules` 的删除是**硬删除**

`paper_rules` 没有 `is_deleted` 列（见 `B端联调坑.md` 坑 34），也不被 `exams` 外键引用 ——
删规则**不影响已组好的卷**（试卷各自持有自己的题目与分段）。
想保留规则但不再使用，请改 `status=off`：组卷时会明确拒绝停用规则
（`test_disabled_rule_is_refused_at_compose_time`），而不是让它在暗处继续生效。

---

## 4. 数据范围

复用 `question_service.scope_subject_ids()` —— **同一个事实源**，不在组卷里另写一套过滤。

| 位置 | 收口方式 |
|---|---|
| 规则列表 / 试卷列表 | SQL 加 `subject_id = ANY(:scope_subject_ids)` |
| 建规则 / 建试卷 | `_ensure_subject_visible()` → 越权 `40301` |
| 组卷（可指定其它科目） | 同上 |
| 读/改/发布某张卷 | 先 `_ensure_subject_visible`，越权 `40301` |

用例 `test_data_scope_researcher_only_own_subject` 覆盖三道闸：
建别科目的卷被拒、换科目组卷被拒、**规则列表本身就看不到别科目的规则**（不只是按钮置灰）。

---

## 5. 验收结果

### 5.1 验收① 一条规则生成完整卷，题型数量/分值/总分匹配

`test_compose_full_paper_matches_rule_and_sections`：
规则 `{single, 30 题, 每题 2 分, 难度 2~3}` →

- `question_count=30`、`total_score=60.0`、`shortfalls=[]`
- 分段自动重建：计划 30 道 / 每题 2 分 / 分段分 60.0 / **实际挂题 30 道**
- 详情逐题分值全为 2、无重复题
- 校验 `ok=true`、`errors=[]`

另有 `test_compose_two_same_type_rules_do_not_overlap`：两条"单选 25 道"规则
抽出的 50 道题**互不重叠**。

### 5.2 验收② 题库不足 → shortfalls 准确回传，不静默补题

`test_shortfall_is_reported_and_never_silently_filled`：
规则 `{case, 100 题, 每题 2 分, 难度 5}`（建筑实务的 `case` 题总数只有 27 道）：

| 断言 | 结果 |
|---|---|
| 恰好 1 条 shortfall | ✅ |
| `need=100` | ✅ |
| `got` == 库中 `case` 题实际总数（27） | ✅ |
| `missing=73`，且 `got+missing==need` | ✅ |
| `rule` 是原始规则对象、`reason` 含"不会用其它题目顶替" | ✅ |
| **卷面写入题数 == `got`（27），不是 100** | ✅ ← 核心 |
| 库中实际挂题数 == 27 | ✅ |
| 缺口持久化，详情页仍能看到 | ✅ |
| 该卷校验 `ok=false`、`SECTION_NOT_FILLED` + `COMPOSE_SHORTFALL` | ✅ |

### 5.3 验收③ 发布 → 改题 → 已发布试卷仍显示锁定版本

`test_publish_locks_version_and_later_edit_does_not_change_paper`：

用 `exam_year=2026` 造一道"独家"题（种子里所有题的 `exam_year` 都是 NULL，
所以 `{single, 1 题, year=2026}` 只会命中它，卷面构成完全可控）：

1. 组卷 → 卷面恰好是这道题，`locked_version=null`（未发布）
2. 发布 → `locked_versions=1`，`question_locks={qid: 1}`
3. **改题**：题干改成"改过的题干"，`version 1 → 2`
4. 重新打开已发布试卷：
   - `locked_version=1`、`current_version=2`、`version_drift=true`
   - **`stem_preview` 含"原始题干"、不含"改过的题干"** ← 真的用了锁定版本
5. 校验：`ok=true`，但带 `VERSION_DRIFT` warning

### 5.4 验收④ 用 6000 道仿真题库组一套完整模考卷

`test_compose_full_mock_paper_from_seed_bank`（科目：建设工程经济）：

| 分段 | 计划 | 实际 | 每题分 | 分段分 |
|---|---|---|---|---|
| 单项选择题 | 60 | 60 | 1 | 60 |
| 多项选择题 | 20 | 20 | 2 | 40 |
| 判断题 | 20 | 20 | 1 | 20 |
| **合计** | **100** | **100** | | **120** |

`shortfalls=[]` → 校验 `ok=true` → 发布 → `locked_versions=100`。
用例运行时把这张表打印出来当验收证据。

### 5.5 验收⑤ 数据范围

见 §4。三闸全过，且 `researcher` 在自己的科目范围内走完 建卷→组卷→发布 全链路。

### 5.6 验收⑥ `run-smoke.ps1` 全绿，Batch 2–6 不回归

```
78 passed, 1 skipped
```

| 测试文件 | 用例数 | 归属 |
|---|---|---|
| `test_admin_v3.py` | 8 | Batch 3 |
| `test_admin_v4.py` | 12 | Batch 4 |
| `test_admin_v5.py` | 14 | Batch 5 / 6 |
| `test_admin_v7.py` | **24** | **Batch 7（Pass 1 21 条 + 前置修正 3 条）** |
| `test_idgen.py` | 15 | Batch 5 |
| `test_smoke.py` | 6 | Batch 2 |

上一批是 `54 passed, 1 skipped`；本批 **+24**，无回归。
（`1 skipped` 是 Batch 5 就有的 6000 行环境变量门控用例。）

> ⚠️ **加 `viewer` 的 `exam:read` 会牵动 Batch 3 的用例** ——
> `test_admin_v3.py` 里的 `VIEWER_PERMS` 常量断言了 viewer 的**完整权限集合**，
> 所以补权限时它必然失败。这是**故意的**：权限集合是跨批次契约，
> 改它必须是有意识的决定，而不是悄悄过去。已在常量上写明"补只读场景就往这里加"。

### 5.7 用例与验收标准的对照

| 验收标准 | 用例 |
|---|---|
| ① 一条规则生成完整卷，题型/分值/总分匹配 | `test_compose_full_paper_matches_rule_and_sections` |
| ② viewer 能看列表/详情，发布按钮置灰 | `test_viewer_can_read_but_not_publish` |
| ③ 题库不足 → shortfalls 准确，不静默补题 | `test_shortfall_is_reported_and_never_silently_filled` |
| ④ 发布 → 改题 → 已发布卷仍显示锁定版本 | `test_publish_locks_version_and_later_edit_does_not_change_paper` |
| ⑤ 归档 → 默认列表看不到 → 开开关能看到 | `test_soft_delete_hides_from_default_list_but_keeps_detail` |
| ⑥ 6000 题库组完整模考卷 | `test_compose_full_mock_paper_from_seed_bank` |
| ⑦ 数据范围（教研只能组自己科目的卷） | `test_data_scope_researcher_only_own_subject` |
| 发布前强制校验 | `test_publish_blocked_when_validation_fails` |
| 锁定落独立列 + JSONB 双写一致 | `test_locked_version_is_written_to_column_and_mirrored_to_jsonb` |

其余为算法纯函数（6 条）与 CRUD/状态/权限边界。

---

## 6. 实测缺陷与修复

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 1 | `POST /admin/exams` 与 `POST /admin/paper-rules` 稳定 **50001** | 想当然写了 `SELECT 1 FROM subjects WHERE id=:sid AND is_deleted=false`，但 **`subjects` 没有 `is_deleted` 列**（它用 `status IN ('on','off')`） | 抽出 `_assert_subject_usable()`，改用 `status='on'`，并把这条事实写进 docstring（坑 34） |
| 2 | `GET /admin/exams/{id}` 等接口 **50001**，报 `NoSuchColumnError: Could not locate column in row for column 'is_deleted'` | `_EXAM_SELECT` 的列清单漏了 `e.is_deleted`，而代码里照常 `row["is_deleted"]` | 补进 select；并把"看有没有 `[SQL: ...]`"这条判据写进坑 35 |
| 3 | 权限用例断言 `researcher` 不能发布，**实际发布成功** | 权限种子按**模块整包**发放，`exam` 四条权限同时给了 super_admin/admin/researcher/teacher | 用例改成如实断言现状（researcher 可发布），缺口记入 §7（坑 36） |
| 4 | 组卷写 `shortfalls` 与发布写 `question_locks` 会互相覆盖 | 两者共用 `rule_config` 这一个 JSONB | `_merge_rule_config()` 强制读-改-写（§3.3 的代价之一）；锁定后来已搬出 JSONB |

### 6.1 前置修正（Pass 2 开工前）新踩的两条

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| 5 | 归档试卷后 `GET /admin/exams/{id}` **仍 404**，但直连库能查到行 | `get_exam_detail` 内部会调 `validate_exam()` 来内联校验结果，而 `validate_exam` **自己也有一份** `if row["is_deleted"]: raise not_found` —— 只删了外层那份 | 只读的 `validate_exam` 不再对归档抛 404（写操作 compose/update/publish 继续拒绝）。见坑 38 |
| 6 | 只改 `db/schema.sql` 加列/补权限，**老库完全不生效** | `run-smoke.ps1` 的建表步骤是条件执行的：`to_regclass('public.users')` 非空就**整段跳过** schema.sql | 新增 `db/migrations/` + `run-smoke.ps1` 3.5 步按序执行迁移（脚本自身幂等）。见坑 39 |

> 第 6 条是**流程性**的坑，代价最高：它在"新库验收全绿"与"老库/生产报错"之间埋了一条缝。
> 修完后专门造了一行"迁移前"数据验证回填逻辑（JSONB 有锁、列是 NULL → 跑迁移 → 列被填对 →
> 再跑一次确认幂等），而不是只看"跑迁移没报错"。

---

## 7. 遗留事项

| # | 事项 | 说明 |
|---|---|---|
| 1 | **`rule_config.question_locks` 待清理** | 已 deprecated（读写都走独立列，JSONB 仅回退 + 双写）。**下一批删**：停掉双写、删回退分支、清存量行里的键（§3.3） |
| 2 | **试卷没有"取消归档"接口** | `DELETE` 只能归档；恢复目前要手动改库。对称的做法是加 `POST /admin/exams/{id}/restore`（题目侧也没有，可一起做） |
| 3 | 试卷没有"物理删除" | 只有软删除。这是**故意的**（有作答记录的卷不能真删），但与题目的处理一致 |
| 4 | `rule_config` 的"答题行为"字段未开放 | schema 里注明它用于"随机顺序 / 单题限时 / 是否可回看"，本批没有开对应的入参（`ExamCreateIn` 未接 `rule_config`），需要时再加 |
| 5 | 组卷不处理案例题**分组** | `case_sub` 被明确拒绝独立抽题；抽 `case` 时只抽到大题本身，**没有把它的子问一起带进卷**。真正支持案例题需要"父题 + 子问"整体入卷 |
| 6 | 加权采样只按"使用次数" | schema 里还有 `question_stats`（正确率/区分度/平均耗时），`strategy` 也已预留 `weak_first` / `coverage` / `history_similar` 三个取值，但本批只实现 `random` 语义 |
| 7 | 组卷是**同步**的 | 6000 题库里 100 道题的组卷耗时在百毫秒级；若将来单卷上千题或候选池到几十万，需要改成异步任务 |
| 8 | 试卷 `difficulty` 用简单平均 | `avg(questions.difficulty)`，没有按分值加权 |

---

## 8. 复现步骤

```bash
# 0) 起本地 PG（注意：必须让 postgres 常驻，见 B端联调坑.md 坑 37）
powershell -ExecutionPolicy Bypass -File tools/local-verify/start-pg.ps1

# 0.5) 老库要先应用迁移（run-smoke.ps1 已自动做；手工起环境时要自己跑）
#      迁移是幂等的，可以无条件重跑
PGCLIENTENCODING=UTF8 psql -h 127.0.0.1 -p 55432 -U yijian -d yijian \
  -v ON_ERROR_STOP=1 -f db/migrations/20260917-01-locked-version-and-viewer-exam-read.sql

# 1) 起 API（真 PG + fakeredis）
cd tools/local-verify
DATABASE_URL="postgresql+asyncpg://yijian@127.0.0.1:55432/yijian" \
JWT_SECRET="local-verify-secret-not-for-production" SMS_PROVIDER=mock \
python serve_fake_redis.py --pg-port 55432 --api-port 8123 &

# 2) 只跑本批用例
cd ../../apps/api
AI_BASE=http://127.0.0.1:8123 \
DATABASE_URL="postgresql+asyncpg://yijian@127.0.0.1:55432/yijian" \
python -m pytest tests/test_admin_v7.py -v

# 3) 全量验收（会自己起停 PG 与 API）
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
```

手工造一张卷（超管 `13800000000 / Admin@123456`）：

```bash
BASE=http://localhost:8123/api/v1
ADMIN=$(curl -s -X POST $BASE/auth/login/password -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","password":"Admin@123456"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
H="Authorization: Bearer $ADMIN"

# 建卷
EXAM=$(curl -s -X POST $BASE/admin/exams -H "$H" -H 'Content-Type: application/json' \
  -d '{"subject_id":1001,"title":"验收模考","duration_min":120,"pass_score":60}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['id'])")

# 组卷（单选 60 + 多选 20 + 判断 20）
curl -s -X POST $BASE/admin/exams/$EXAM/auto-compose -H "$H" -H 'Content-Type: application/json' \
  -d '{"seed":20260917,"rules":[
        {"type":"single","count":60,"score":1,"difficulty":[2,3]},
        {"type":"multiple","count":20,"score":2,"difficulty":[3,4]},
        {"type":"judge","count":20,"score":1,"difficulty":[1,2]}]}' | python -m json.tool

curl -s -X POST $BASE/admin/exams/$EXAM/validate -H "$H" | python -m json.tool
curl -s -X POST $BASE/admin/exams/$EXAM/publish  -H "$H" -H 'Content-Type: application/json' -d '{}' | python -m json.tool
```

---

## 9. Pass 2 待办（前端，待确认后开工）

四个页面：`/exams`（列表 + 类型/科目/状态筛选 + **include_deleted 开关**）、
`/exams/new`（手动选题 / 规则自动组卷）、
`/exams/[id]`（卷面结构预览 + 加题/移题 + 发布 + **归档**）、`/paper-rules`（规则管理）。

关键交互（依赖后端已经就位的能力）：

| 交互 | 后端已支持 |
|---|---|
| 自动组卷实时展示抽题结果 + **缺口警告（need/got/missing）** | `ExamComposeOut.shortfalls` + `message` |
| 卷面结构可视化（按 section 分段：题型/数量/分值） | `ExamDetail.sections[]`（含 `actual_count` / `actual_score`） |
| 加题时按章节/知识点/难度筛选 | 复用 Batch 4 的 `/admin/questions` 筛选 |
| 发布前强制走 validate，不通过不允许发布 | 后端已强制；前端需先展示 `validation` 再放开按钮 |
| 已发布试卷显示"锁定版本 vX"；与当前版本不同时显示 **"已锁定至 vX（当前 vY）"** + hover 差异摘要 | `ExamQuestionItem.locked_version` / `current_version` / `version_drift`（且 `stem_preview` 已是锁定版题干） |
| **归档**按钮 + 默认列表过滤 + "显示已归档"开关 | `DELETE /admin/exams/{id}`、`ExamListItem.is_deleted`、`can_delete` |
| **viewer 登录时"发布"按钮置灰 + tooltip** | `viewer` 已补 `exam:read`；发布返回 `40301`，消息含 `exam:publish`（tooltip 直接用） |

> ✅ 原「Pass 2 若要演示发布按钮置灰，需先加角色」的问题已解决 ——
> `viewer` 补了 `exam:read` 就够（§3.6），**不需要新建角色**。
>
> ⚠️ Pass 2 只剩一个后端侧的空缺：**没有"取消归档"接口**。
> 前端若要做"恢复"按钮，需要先补 `POST /admin/exams/{id}/restore`（§7 第 2 条）。
