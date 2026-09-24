# `app/sql/stats/` —— 统计看板的 SQL 约定

> 为什么 SQL 单独放文件、而不内联进 Python（硬约束 ④）：
> **可单独 review / diff 清楚 / 非 Python 背景的人也能改**。
> 代价是失去了 IDE 的绑定参数提示 —— 所以下面这些约定必须**写下来**，
> 并且尽量做成**可 grep、可自动检查**的，而不是靠"记住它"。

---

## 1. 每个 `:param` 都必须显式 `CAST`

```sql
-- ✅ 对
WHERE (CAST(:subject_id AS bigint) IS NULL OR q.subject_id = CAST(:subject_id AS bigint))
WHERE pt.answered_at >= CAST(CAST(:date_from AS date) AS timestamp)

-- ❌ 错：类型靠 Postgres 从上下文猜
WHERE q.subject_id = :subject_id
```

**为什么**：`CAST` 是**唯一的类型声明处**。没有它，绑定参数的类型由"用在哪一列旁边"反推 ——
猜对的时候一切正常，猜错的时候报的错**和真正的问题隔着好几层**。

实测（2026-09-24，坑 58）：`CAST(:day AS date)` 会让 SQLAlchemy 把参数**推断成 `date`**，
于是**必须传 `date` 对象**；传 `"2026-09-24"` 这种字符串会得到

```
invalid input for query argument $1: 'str' object has no attribute 'toordinal'
```

这条报错里**没有一个字提到"你应该传 date 对象"**。

---

## 2. 同一个参数名，出现在多处 → 必须 `CAST` 成**同一类型**

**为什么**：一个参数名 = 一个绑定位。如果它在两处的**推断类型不一致**，Postgres 直接拒绝：

```
asyncpg.exceptions.AmbiguousParameterError: inconsistent types deduced for parameter $7
```

实测：`$7` 既当 `practice_sessions.correct`（`smallint`）又当 `score`（`numeric`）——
**在造数脚本与测试装置里各踩了一次**，说明它不是偶发，而是**缺约定的必然结果**。

```sql
-- ❌ 错：一个名字服务两种类型
VALUES (:id, :uid, 'chapter', :n, :n, :c, :c, ...)          -- :c 既当 smallint 又当 numeric

-- ✅ 对（两种改法，任选其一）
VALUES (:id, :uid, 'chapter', :n, :n, :correct, :score, ...)  -- ① 拆成两个名字
VALUES (:id, :uid, 'chapter', :n, :n, CAST(:c AS smallint), CAST(:c AS numeric), ...)
                                                              -- ② 同名但都 CAST（类型仍须一致！）
```

> ⚠️ 改法 ② **只有在两处类型本来就相同时才成立**。上例两处类型不同，
> 所以只能走 ①。**判据是"类型是否一致"，不是"有没有 CAST"。**

---

## 3. 注释里**不要**写 `:name`

```sql
-- ❌ 错（`text()` 解析的是整段字符串，注释也算）
--   参数：:dim, :subject_id，例如 CAST(:x AS interval)
```

**为什么**：SQLAlchemy 的 `text()` 会把**注释里的** `:name` 也当成绑定参数：

- 给了一个**没有值**的名字 → `InvalidRequestError: A value is required for bind parameter 'x'`
- 给了一个**恰好存在**的名字 → **静默共享同一个绑定位**（更隐蔽：不报错，但两处串了）

实测：`trends.sql` 的注释里写了 `CAST(:x AS interval)` 举例，直接让整个接口 500。
已全量清理 **24 处**。

**写法**：用 `` `name` `` 或不带冒号。

---

## 4. 每条查询都必须带 `statement_timeout`

**不在这里写**——由 Python 侧 `stats_service._run()` 统一设置（`set_config` + `is_local := true`），
**所以不要绕过 `_run()` 直接 `db.execute()`**。

**为什么**：聚合查询是"数据量一大就会拖垮 DB"的那类。没有超时，它的表现**不是报错，而是越来越慢**，
最后把连接池吸干（硬约定 N：无界等待必须加界）。

---

## 5. 比值（除法）只在 Python 侧算

SQL **只给原始计数**（`value` / `correct` / `sample`），比值一律由 `stats_service._ratio()` 计算。

**为什么**：口径必须**只有一处实现**（docs/20 §7-9）。SQL 里再算一遍，
就有两个"正确率"的定义，早晚会分叉。

> 唯一的例外：`weak_points.sql` 的 `ORDER BY` 里有除法 —— 那是**排序键**，不是返回给调用方的值。
> 加新查询时若要在 SQL 里做除法，请先问一句"它会不会作为**值**返回"。

---

## 自检（可 grep / 可自动跑）

```bash
# ① 列出所有参数名及其出现次数（看有没有"起了就没用"或"用得很散"的名字）
grep -ohE ':[a-z_]+' app/sql/stats/*.sql | sort | uniq -c | sort -rn

# ② 找出"没有 CAST 的参数用法"（应输出空）
grep -nE ':[a-z_]+' app/sql/stats/*.sql | grep -v 'CAST('

# ③ 注释里不该有 :name（应输出空）
grep -nE '^[[:space:]]*--.*[^a-zA-Z_:]:[a-z_]+' app/sql/stats/*.sql

# ④ SQL 不该被内联进 Python（`text(` 只应出现两处）
grep -c 'text(' app/services/stats_service.py
```

其中 ②③ 已经**进了测试**（`tests/test_stats_service.py::test_every_param_is_explicitly_cast`）——
不用每次手动 grep：**约定一旦能自动检查，就不再依赖"记住它"**。
