# Batch 7：组卷引擎 —— 方案与验收（Pass 1 后端 + 前置修正）

> 目标：让教研能把题库变成**一份可发布的试卷**，并且这份卷子在发布之后
> **不可被后来的改题悄悄改变**。
>
> 本批拆两个 Pass：**Pass 1 后端 + 测试**（已交付验收）→ **前置修正**（§1.2，三项）→
> **Pass 2 试卷管理前端**（`/exams`、`/exams/new`、`/exams/[id]`、`/paper-rules`，待开工）。
> 本文覆盖 Pass 1 与前置修正；Pass 2 完成后在文末追加其章节。
>
> **当前状态**：后端 **46 个接口**，`run-smoke.ps1` **92 passed, 1 skipped**（无回归）。

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
| ⑬ | POST | `/admin/exams/{id}/restore` | `exam:publish` | **恢复（解除归档）** |
| ⑭ | POST | `/admin/questions/{id}/restore` | `question:delete` | **恢复题目**（与⑫对称，在题库路由下） |
| ⑮ | POST | `/admin/exams/{id}/questions` | `exam:create` | **手动加题**（Pass 2 前置缺口，§3.9） |
| ⑯ | DELETE | `/admin/exams/{id}/questions/{eq_id}` | `exam:create` | **移出一题**（同上） |
| ⑰ | PUT | `/admin/exams/{id}/sections` | `exam:create` | **重建卷面结构**（唯一入口，需 `expected_question_count`，§3.10） |

⚠️ **`PUT /admin/exams/{id}` 现在只接受元数据**，传 `sections` 会 `40001`（§3.10）。

另外 `GET /admin/questions` 补了 `knowledge_point_id` 筛选参数（原只能按章节挑题），
并在列表项里带出 `knowledge_point_id` / `knowledge_point_name`。

> 原需求列了 9 个。多出来的 ⑤（试卷列表）、⑪ 的完整语义是 Pass 2 前端必需的
> —— 列表页要有数据来源，编辑页要有落点；⑫⑬⑭ 是与 Batch 4 题目软删除对称的
> **归档 / 恢复**闭环（§3.5、§3.8）；⑮⑯ 是 Pass 2 手动选题必需的后端缺口（§3.9）。
> 没有超出"组卷引擎"的范围。

后端接口总数：**29 → 45**（Batch 7 贡献 16 个）。

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

**恢复接口见 §3.8**（`POST /admin/exams/{id}/restore`）—— 归档与恢复构成闭环。

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

### 3.8 恢复接口（解除归档）—— 与归档/删除对称

| 接口 | 权限 | 说明 |
|---|---|---|
| `POST /admin/exams/{id}/restore` | `exam:publish` | 恢复试卷 |
| `POST /admin/questions/{id}/restore` | `question:delete` | 恢复题目 |

**都不新增权限码**，但两侧刻意选了不同的既有权限：

| 动作 | 权限 | 为什么 |
|---|---|---|
| 归档试卷 | `exam:create` | "把卷收起来"是组卷者的日常操作 |
| **恢复试卷** | **`exam:publish`** | 一份已发布的卷恢复后**立刻重新对外可见**，分量更接近发布 |
| 删除题目 | `question:delete` | （Batch 4 既有） |
| **恢复题目** | **`question:delete`** | 恢复与删除是**同一个权责**，能归档的人才能解除归档 |

三条共同性质：

**① 幂等** —— 对象本来就没被删除时返回 `code=0` + `already_active=true`，
**不报错也不产生写入**。"目标状态已达成"不是失败；否则前端重试、
或批量恢复里混进一道没删的题，都得专门写容错。注意幂等分支**不会**让 `version` 再 +1。

**② 恢复前校验关联数据有效性** —— 这是本组接口最要紧的部分：

| 对象 | 拒绝条件 | 不加这条会怎样 |
|---|---|---|
| 题目 | `chapter_id` 指向的章节已删/不存在 | 题目挂到**不存在的章节**上，按章节筛选时既不属于任何章节、又占着列表位置 |
| 题目 | `knowledge_point_id` 已删/不存在 | 同上，且知识点的**父章节**也要一并检查（`knowledge_points.chapter_id` 是必填） |
| 试卷 | 所属科目已停用（`status != 'on'`） | 卷子在列表里挂着但 C 端取不到、点不开 |
| 试卷 | **已发布**的卷且卷面有题被归档 | 考生看到**残缺卷面**，且校验器会一直报 `QUESTION_DELETED` |

一律 `40901` + 说清**是哪一条**不满足 + 给出可执行的下一步
（"请先恢复对应的章节/知识点" / "或把试卷改回草稿并重新组卷"）。
**草稿态的试卷不受"缺题"限制** —— 还在编，缺题很正常。

> 为什么必须自己查：`questions.chapter_id` / `knowledge_point_id` 是**弱引用**
> （只有 `REFERENCES`，没写 `ON DELETE` 行为），而章节/知识点用的是**软删除** ——
> 数据库层面**根本不会拦**。所以"关联还在不在"只能由业务代码负责。
>
> 原则与"缺口不静默补题"同源：**不允许静默恢复到一个不成立的状态上。**

**③ 留痕** —— 写 `content_change_logs`，`action=restore`，
`diff` 为 `{before: {is_deleted: true}, after: {is_deleted: false}}`
（与 delete 的 diff 形状一致，前端审计抽屉能直接渲染）。题目侧额外写 `audit_logs`。

**版本号**：题目恢复 `version + 1`。恢复是一次内容变更，与删除对称；
否则会出现"删除再恢复后版本号回到旧值"的诡异现象（乐观锁会错判）。

### 3.9 手动加题 / 移题（Pass 2 前置缺口）

**这是 spec 里"手动选题 / 加题 / 移题"必需、但原后端没有的能力。**
Batch 7 Pass 1 只做了"自动组卷"，卷面的题**只能**由组卷产生 ——
一旦组卷有缺口，教研没有任何手段补一道题进去。

| 接口 | 权限 | 说明 |
|---|---|---|
| `POST /admin/exams/{id}/questions` | `exam:create` | 批量加题（≤200，去重保序） |
| `DELETE /admin/exams/{id}/questions/{eq_id}` | `exam:create` | 移出一题（删**卷面行**，不动题目） |

**逐条判断、逐条给理由** —— 与导入管道同一个哲学：批量操作不因为其中一条有问题
就整批失败，但**绝不静默丢弃**。六道闸全部进 `skipped[].reason`：

| 判据 | reason |
|---|---|
| 题目不存在 | 题目不存在 |
| 已归档 | 题目已归档（软删除） |
| 非「已发布」 | 题目不是已发布状态（当前 draft） |
| **科目不一致** | 题目不属于本试卷的科目 |
| 已在卷面 | 该题已在卷面中 |
| 找不到同题型分段 | 卷面没有「判断题」分段，请先加一个该题型的分段 |

> **「科目一致」这条别省**：不加就会出现"经济卷里塞进一道法规题"，
> 而校验器只看得懂题型与分值，**看不出科目串了**。

**`section_id` 不传时按题目题型自动挂到同题型的分段**；传了则校验它属于本卷（否则 `40001`）。

**加题不受分段计划题数限制**：加超了 `validate` 会以 `SECTION_NOT_FILLED` 报出来，
由教研决定是补题、还是把分段数字改成实际值（那等于明确认可新的卷面构成）。

**移题之后分段的计划题数不会自动变小** → `validate` 会报 `SECTION_NOT_FILLED`。
这是**有意**的：分段是卷面的契约，改动它应当是一个**显式动作**。
响应 `message` 里会带上这句提示，前端直接展示即可。

**卷面冻结**：已发布（且未开「允许发布后编辑」）的卷**不能加题也不能移题** → `40901`。
这条判断收口在 `_assert_exam_mutable()` 一处 —— 原本 compose / update 各写一份，
"同一份判断散落多处、改一处漏三处"正是坑 38 的成因。

配套的 `GET /admin/questions` 增加了 `knowledge_point_id` 筛选，
并在列表项带出 `knowledge_point_id` / `knowledge_point_name`：
加题面板要按知识点挑题（章节比知识点粗得多，挑不细），
而**筛完也得能看出这道题挂在哪个知识点上**，否则筛选形同虚设。

---

## 10. Pass 2a：试卷列表 + 详情编辑（已交付）

### 10.1 交付物

| 文件 | 说明 |
|---|---|
| `app/(console)/exams/page.tsx` | 试卷列表（筛选 + include_deleted 开关 + 归档/恢复） |
| `app/(console)/exams/[id]/page.tsx` | 详情/编辑（卷面结构 + 加题/移题 + 发布 + 归档/恢复） |
| `components/AddQuestionsDialog.tsx` | 手动加题面板（三步：挑选 → 预览确认 → 结果回执） |
| `components/ValidateReport.tsx` | 结构化校验报告（分段缺口 + error/warning + **每条带下一步**） |
| `components/ExamBasicInfoDialog.tsx` | 基本信息编辑（**只发非 sections 字段**） |
| `components/ExamSectionsDialog.tsx` | 卷面结构编辑（**会清题，需显式勾选确认**） |
| `hooks/useRowActionFeedback.ts` | **就地反馈**（三态 + 延时移除 + toast 出口 + 重试） |
| `components/RowActionMarker.tsx` | 行内三态标记 + 整行淡出样式 |
| `hooks/useExams.ts` | 试卷/规则的 query key 与全部写操作 |
| `lib/exam.ts` | 枚举中文、**分段缺口的人话**、校验码 → 「下一步」映射 |

**配套的后端小改**（45 → 46 接口）：`GET /admin/chapters/knowledge-points` ——
「加题」面板要按知识点筛题，而在它之前**没有任何接口能列出知识点**，
前端只能从题目里反推（下拉里只会出现"当前页见过的"知识点）。
与章节树同一角色（科目域下拉数据源），所以放在 `/admin/chapters` 下、用 `question:read`。

### 10.2 就地反馈（用户指定的交互模式）

操作成功后**不打断当前视图**，三段式：

1. **停留在当前视图**（仍停在「显示已归档」下，方便连续恢复）
2. 目标行**就地标记「已恢复」**并淡出，**3 秒后从列表移除**
3. 同时弹 toast **「已恢复「卷名」· [查看默认列表]」**，可跳到默认列表

抽成 `useRowActionFeedback` + `RowActionMarker`，2b 与后续批次直接用。

**为什么必须自己管状态、而不是 `invalidateQueries`**（坑 43）：
这类开关是**放宽范围**——`include_deleted=true` 返回的是"所有的卷"，
不是"仅已归档的"。所以恢复之后那一条**本来就还在结果集里**，
一旦重新拉取，"我把它标记成已完成"的本地意图就被服务端的"它还在"冲掉了。
所以行级操作**不失效列表**，用本地 `dismissed` 集合表达视图意图，
并在**翻页/改筛选**时清掉（回到服务端真相）—— 表外会写明
"本次已就地处理 N 条并从当前视图隐藏"。

### 10.3 加题面板（"把数据加到另一处"的模式）

与 `AssignRolesDialog`（给用户分配角色）同构：**挑 → 预览 → 确认 → 回执**。

- **已在卷面的题显示「已在卷面」且禁选**；非「已发布」的题也禁选并标出原因。
- **筛选结果里显示知识点名**（章节名在题库列表里就有了，挑题时真正关心的是知识点那一层）。
- **选完预览「将加入 N 道题」**，并**提前算出每道题会落到哪个分段**；
  题型在卷面没有对应分段的，在预览页就标出来"提交后会被跳过"
  —— 后端本来就会逐条回 `skipped[].reason`，但**让用户在提交之前看到**比事后解释好得多。
- 结果页回执 `added` 与 `skipped`（带原因）。

### 10.4 校验报告：结构化 + 每条给下一步

三件事都做了（缺一条这报告就等于没做）：

1. **分段缺口说人话并整段标红**：不是给 `-8`，而是
   **「案例分析题 还差 100 道案例题（计划 100 道，实际 0 道）」**，整段红底。
2. **每条都有「下一步」**：`lib/exam.ts` 的 `VALIDATE_GUIDANCE` 把 11 个校验码
   映射成"是什么 + 该怎么办"。只说问题不说办法，用户看完仍然卡在原地。
3. **分清必须修 / 只是提醒**：error 不修发不了；warning（如 `VERSION_DRIFT`）
   本来就不需要处理。混在一起列会让人以为每条都得改，反而不敢发布。

### 10.5 锁定版本的展示

| 情况 | 展示 |
|---|---|
| 未发布 | 「未锁定版本」 |
| 已锁定且未漂移 | 「锁定 v1」 |
| **已锁定但漂移** | **「已锁定至 v1（当前 v2）」** + hover 说明 |

hover 里**刻意不摆"差异"**：`stem_preview` 来自**锁定版本**，
当前版本的题干这个接口拿不到 —— **没有真数据就不画假 diff**，
只写清"哪里能看到当前版本"。真正的逐字对比需要"版本对比"接口，本批没做（§7 遗留）。

### 10.6 三条界面级的保护

- **发布按钮的禁用原因是可见的**：缺权限显示缺哪个权限码，校验不过显示"未通过几条"，
  而不是一个点不动的灰按钮。
- **编辑基本信息只发非 `sections` 字段**。`PUT` 一旦收到 `sections` 会
  `DELETE FROM exam_questions`（**整卷题目清空**）。"分段是骨架、重建必重排题"
  这个语义不改，但绝不能被"我只想改个时长"顺手触发（坑 42）。
- **改卷面结构要三重确认**：说出"将清空现有 N 道题"→ **必须勾选「我明白」**（否则确认键禁用）
  → 已发布时入口直接禁用。

### 10.7 验收对照

| 验收标准 | 结果 | 证据 |
|---|---|---|
| ② viewer 能看不能发/归档/恢复 | ✅ | `12`/`13`/`14`；按钮 `disabled=true`，tooltip 写明缺 `exam:create` / `exam:publish` |
| ③ 题库不足 → shortfalls 准确显示 | ✅ | `07`/`08`：`需要 100 · 抽到 0 · 缺 100`，摘要「共缺 1 道；缺口最大的是…」 |
| ④ 发布卷 → 改题 → 仍显示锁定版本 | ✅ | `09`：「已锁定至 v1（当前 v2）」+ hover 说明 |
| ⑤ 归档 → 默认看不到 → 开关打开 → 恢复 → 就地标记 → 3 秒消失 + toast | ✅ | `02`→`03`→`04`→`05`→`06` 五连 |
| 加题：已加过禁选 / 显示知识点 / 预览确认 | ✅ | `10`/`11` |
| validate 失败结构化 + 每条下一步 | ✅ | `07` |
| 截图 ≥10 张 | ✅ | **14 张**（`apps/admin/docs/screenshots/batch7-pass2a/`，md5 无重复） |
| `run-smoke.ps1` 全绿、Batch 2–6 无回归 | ✅ | 见 §5.6 |

E2E 脚本：`tools/local-verify/seed-pass2a.py`（造 4 种状态的卷 + viewer 账号）
与 `tools/local-verify/e2e-pass2a.py`（走查 + 断言 + 截图），
驱动是 `tools/local-verify/cdp_browser.py`（CDP，无第三方浏览器依赖）。

---

## 11. Pass 2b：待做

`/exams/new`（手动选题 / 规则自动组卷）+ `/paper-rules`（规则列表 + 编辑）。
就地反馈组件与加题面板都可直接复用 —— 见 §9.1 的拆分理由。

> Pass 2a **刻意没做**的一件事：**"下线（已发布 → 草稿）"没有接口**。
> `ExamDetail.can_unpublish` 字段一直是 `true`，但后端没有对应的写接口
> （只有 `archived` 这个**状态**，与"下线到草稿"不是一回事）。
> 于是"已发布的卷要改卷面"目前只能改库 —— 见 §7 遗留事项。
> 这属于**状态机枚举**的问题，与「跨批次契约审计」一起做更合适，故本批不动。

### 3.10 破坏性变更的**结构防护**（坑 42 的正式修法）

> **约定防不住副作用，结构防护才防得住。**
> 上一版的对策是"前端提交前构造 payload，绝不回传读到的对象" —— 那是**约定**。
> 一个人忘了，就再删一次整卷。所以这一节把它改成**结构上不可能**。

#### ① 拆接口：`sections` 从元数据更新接口剥离

| 接口 | 接受什么 |
|---|---|
| `PUT /admin/exams/{id}` | **只有元数据**（标题/类型/年份/卷号/时长/及格线/简介/是否免费） |
| `PUT /admin/exams/{id}/sections` | **只有卷面结构**，且必须传 `expected_question_count` |

元数据接口上：

- `model_config = ConfigDict(extra="forbid")` —— 任何未知字段一律拒绝
  （顺带防住将来新增的错字段，比如 `subjects_id`）；
- 传 `sections` 会命中 `_reject_sections`，返回 **`40001`** 且**不写库**，
  错误信息带**可执行的下一步**：告诉你该用哪个接口、要传什么参数。

> 为什么保留一条"专门拒绝 `sections`"的分支，而不是让它走通用的
> "Extra inputs are not permitted"：调用方需要知道**往哪走**。
> 这一坑最缺的就是这个 —— 报错只说"不支持"，用户还是不知道正确做法。

#### ② 显式确认：`expected_question_count`

`sections` 接口要求传**调用方读到的当前卷面题数**（详情里的 `question_count`）：

- 不传 → `40001`（必填）；
- 与库里不一致 → **`40901` + 「卷面已变化，请刷新后重试」且不写库**。

它同时是两件事：

1. **防误操作** —— 你确认的是"我要重建的是**这一份**卷面"；
2. **乐观并发** —— 两人同时改一张卷，后提交的必然对不上，
   **不会把前一个人刚加的题默默清掉**。前端拿到 `40901` 后给了
   「刷新并重填」的出口（而不是让用户对着同一堵墙反复点）。

#### ③ diff 兜底：净减少超阈值就拒绝

拆接口只解决了"调用方**故意**改卷面结构"这条路，防不住**副作用**：
`POST /auto-compose` 传 `replace=true` 时也会先清空卷面再重建 ——
它的语义是"重新抽题"，调用方未必意识到这会删掉手里已有的题。

所以兜底**不看调用方想要什么，只看实际发生的结果**：

```
操作前数一次 exam_questions → 操作后数一次 → 净减少 > 阈值 就拦
```

- 阈值来自 `app_configs.exam.mass_question_loss_threshold`（默认 10，
  种子 id=11；老库由 `db/migrations/20260918-01-*.sql` 补）；
- **在 `commit` 之前判定** —— 抛异常 → `get_db` 回滚 → **一行都没写**。
  放到 commit 之后就只能"删了再报错"，那是不可逆的；
- 拒绝时把调用方指到 `/sections` 并**直接给出该传的参数值**
  （`expected_question_count=<操作前的题数>`）；
- 覆盖 `auto-compose`、`移题` 等所有会减少卷面行的写路径，
  **`/sections` 自己除外** —— 兜底提示就是把人指到它，它再拦一次就成了循环引用。

**配置读不到就用默认值继续保护，绝不因为配置缺失而放行。**
配置值写坏（如 `"not-a-number"`）时同样回退默认值 —— 守卫的职责是"保护"，
不该自己变成故障源。

#### ④ 前端对应

`ExamSectionsDialog` 走新接口并传 `expected_question_count`；
冲突（`40901` + 卷面已变化）时展示独立提示 + **「刷新并重填」**按钮，
而不是只弹一个会被忽略的 toast。

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
92 passed, 1 skipped
```

| 测试文件 | 用例数 | 归属 |
|---|---|---|
| `test_admin_v3.py` | 8 | Batch 3 |
| `test_admin_v4.py` | 12 | Batch 4 |
| `test_admin_v5.py` | 14 | Batch 5 / 6 |
| `test_admin_v7.py` | **38** | **Batch 7（Pass 1 21 + 前置修正 3 + 恢复 8 + 加题移题 5 + 知识点下拉 1）** |
| `test_idgen.py` | 15 | Batch 5 |
| `test_smoke.py` | 6 | Batch 2 |

上一批是 `54 passed, 1 skipped`；Batch 7 **+37**，无回归。
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
| 恢复：归档→恢复→回默认列表、幂等 | `test_exam_restore_roundtrip_and_idempotent` |
| 恢复权限（`exam:publish`） | `test_exam_restore_requires_publish_permission` |
| 恢复前校验关联数据（科目停用 / 已发布卷缺题） | `test_exam_restore_rejects_when_subject_disabled`、`..._published_with_archived_questions` |
| 题目恢复：幂等、版本 +1、权限 | `test_question_restore_roundtrip_and_idempotent`、`..._requires_delete_permission` |
| **题目恢复拒绝悬空引用**（章节/知识点已删） | `test_question_restore_rejects_when_chapter_or_kp_deleted` |
| 恢复留痕（diff `is_deleted: true→false`） | `test_restore_writes_change_log_with_is_deleted_diff` |
| 手动加题 / 移题 + 分段计数与总分重算 | `test_manual_add_and_remove_questions` |
| 加题六道闸逐条给理由、不整批失败 | `test_add_questions_skips_with_reason_not_whole_batch_failure` |
| 已发布卷面冻结（加题/移题都 40901） | `test_add_and_remove_questions_frozen_after_publish` |
| 分段归属校验 + 权限 | `test_add_questions_validates_section_and_permission` |
| 题目按**知识点**筛选 | `test_question_list_filters_by_knowledge_point` |

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
| 2 | 试卷没有"物理删除" | 只有软删除 + 恢复。这是**故意的**（有作答记录的卷不能真删），与题目处理一致 |
| 3 | **题目写路径未做数据范围校验** | `create` / `update` / `delete` / `restore` 题目都**没有**按 `user_roles.scope_type` 收口（只有列表/导入/组卷做了）。这是 Batch 4 起就存在的缺口，本次**刻意没有只给 `restore` 单独加** —— 那样会出现"能删别科目的题、却恢复不了"的更糟状态。要修就四个入口一起修 |
| 9 | **试卷没有"下线"接口**（Pass 2a 发现） | `ExamDetail.can_unpublish` 恒为 `true`，但**没有任何写接口**能把 `published` 改回 `draft`（`ExamUpdateIn` 不含 `status`）。于是"已发布的卷要改卷面"目前只能改库。这是**状态机枚举**层面的问题，与「跨批次契约审计」一起做更合适。⚠️ 在修好之前，前端**不要**渲染下线按钮 —— 那是个必然 404 的操作 |
| 10 | **没有"版本对比"接口**（Pass 2a 发现） | 详情页能显示"已锁定至 v1（当前 v2）"，但**拿不到当前版本的题干**（`stem_preview` 返回的是锁定版本），所以画不了逐字 diff。做一个 `GET /admin/questions/{id}/versions/compare?a=&b=` 才能支撑真正的差异摘要 |
| 11 | **前端 `npm run lint` 是假门禁** | `package.json` 有 `lint` 脚本，但仓库**没有 eslint 配置**，执行会卡在交互式提问上（坑 44）。当前实际生效的门禁只有 `tsc --noEmit`。启用时要把首次全量结果单独提交，免得和业务改动混在一起 |
| 12 | **前端页面级 E2E 未纳入 `run-smoke.ps1`** | 本批的走查脚本是 `tools/local-verify/e2e-pass2a.py`（CDP 驱动，含断言），但**需要额外起 admin dev（3000）**，所以没有进 `run-smoke.ps1`。要不要把它也收进冒烟，等 2b 做完一起定 |
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

## 9. Pass 2：试卷管理前端

### 9.1 拆成两个 Pass 的理由

四个页面 + 一个可复用的"就地反馈"组件 + E2E 走查与截图，工作量约为 Pass 1 的两倍。
一次做完，最费时间的浏览器走查与截图部分最容易压成"点一下截一张"的形式主义。
所以按**真实边界**拆开：

| Pass | 内容 | 一句话概括 |
|---|---|---|
| **2a** | `/exams` 列表 + `/exams/[id]` 详情编辑 + 归档/恢复 + 就地反馈组件 | **看已有的卷、管它的状态** |
| **2b** | `/exams/new`（手动选题 / 规则自动组卷）+ `/paper-rules` 规则管理 | **造新卷、配规则** |

切分的好处：**归档/恢复的就地反馈组件在 2a 落地并沉淀，2b 直接复用**；
2a 的验收标准（viewer 只读、归档→恢复→回列表、锁定版本显示）不依赖 2b 的任何东西。

前置：2a 依赖 §3.9 的手动加题/移题接口（已随本批交付）。

### 9.2 就地反馈组件（用户指定的交互模式）

操作成功后**不打断当前视图**，三段式反馈：

1. **停留在当前视图**（例如仍停在"显示已归档"下，方便连续操作）
2. 目标行**就地标记"已恢复"**（淡出 / 打勾），**3 秒后从列表移除**
3. 同时弹 toast：**「已恢复 N 道题 · [查看]」**，点 [查看] 跳默认列表

抽成组件复用（后面批量删除 / 发布也用它）。

### 9.3 页面与关键交互

四个页面：`/exams`（列表 + 类型/科目/状态筛选 + **include_deleted 开关**）、
`/exams/new`（手动选题 / 规则自动组卷）、
`/exams/[id]`（卷面结构预览 + 加题/移题 + 发布 + **归档 / 恢复**）、`/paper-rules`（规则管理）。

| 交互 | 后端已支持 |
|---|---|
| 自动组卷实时展示抽题结果 + **缺口警告（need/got/missing）** | `ExamComposeOut.shortfalls` + `message` |
| 卷面结构可视化（按 section 分段：题型/数量/分值） | `ExamDetail.sections[]`（含 `actual_count` / `actual_score`） |
| 加题时按章节/**知识点**/难度筛选 | `GET /admin/questions?chapter_id=&knowledge_point_id=&difficulty=` |
| 加题 / 移题 | `POST /admin/exams/{id}/questions`、`DELETE .../questions/{eq_id}`（§3.9） |
| 发布前强制走 validate，不通过不允许发布 | 后端已强制；前端需先展示 `validation` 再放开按钮 |
| 已发布试卷显示"锁定版本 vX"；与当前版本不同时显示 **"已锁定至 vX（当前 vY）"** + hover 差异摘要 | `ExamQuestionItem.locked_version` / `current_version` / `version_drift`（且 `stem_preview` 已是锁定版题干） |
| **归档**按钮 + 默认列表过滤 + "显示已归档"开关 | `DELETE /admin/exams/{id}`、`ExamListItem.is_deleted`、`can_delete` |
| **恢复**按钮（**只在"显示已归档"模式下出现** + 二次确认） | `POST /admin/exams/{id}/restore`（§3.8）；拒绝时 `40901` + 具体原因可直接展示 |
| **viewer 登录时"发布/归档/恢复"按钮置灰 + tooltip** | `viewer` 只有 `exam:read`；三个写接口分别返回 `40301`，消息含 `exam:publish` / `exam:create`（tooltip 直接用） |

> ✅ 原「Pass 2 若要演示发布按钮置灰，需先加角色」的问题已解决 ——
> `viewer` 补了 `exam:read` 就够（§3.6），**不需要新建角色**。
> 归档（`exam:create`）、恢复（`exam:publish`）也都在 viewer 的权限之外，
> 所以"三个写按钮全灰"是同一个账号能一次演示完的。
