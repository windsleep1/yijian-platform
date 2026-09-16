# Batch 5 · 题库批量导入管道 —— 方案与验收

> 状态：**已完成**（Pass 0 = 雪花 ID 收口；Pass 1 = 导入管道）
> 上游文档：`docs/07-题库合规与导入规范.md`（§4 导入规范是本文的规范来源）
> 前序文档：`docs/09-Batch3-管理后台v0.1-方案与骨架.md`、`docs/10-Batch4-题库CRUD-方案与验收.md`
> 本文讲 Batch 5 增量；响应信封、`BigIntStr`、权限门禁等已定约定不再重复。

---

## 0. 一句话结论

题库从"一道一道录"变成**批量灌**：一条七步管道（上传 → 试算 → 查看 → 执行 → 发布 →
回滚 + 批次列表），**整批一个事务**、**含错默认不写库**、**同一批不可重复执行**、
**回滚能退回到导入前的题量**。实测 6000 道仿真题 `success=6000 / failed=0`，
单批含错文件精确报出"第 57 行 answer 不在选项里"且**一行都不写**。

验收过程中实测 + 自审共抓出 **5 个真实缺陷**（见 §5），均已修复并留了回归手段。

---

## 一、范围

| 层 | 内容 |
|---|---|
| 后端 | 7 个接口：上传 / 校验 / 详情 / 执行 / 发布 / 回滚 / 批次列表 |
| 后端 | 4 张表参与：`import_batches`、`import_items`，落库时写 `questions` + `question_options` + `question_versions` + `content_change_logs` |
| 后端 | 新模块：`app/api/v1/admin_imports.py`、`app/services/import_service.py`、`app/schemas/admin_import.py` |
| 验证 | `apps/api/tests/test_admin_v5.py`（13 条，其中 1 条大文件用例按环境变量门控） |
| 验证 | 3 个本地探针 + 1 个格式转换器（见 §7） |
| 样例 | `docs/samples/batch5-wrong-file.csv` + `docs/samples/batch5-error-report.json` |

**不在本批**：xlsx 解析、前端导入页、Meilisearch 推送、定时同步外部题库。
（xlsx 被**显式拒绝**并给出"另存为 CSV UTF-8"的可执行建议，而不是静默当 CSV 读 —— 见 §3.3。）

---

## 二、六条验收标准（需求 → 实现 → 证据）

| # | 验收标准 | 实现要点 | 证据 |
|---|---|---|---|
| ① | **错误文件 → 精确到行 + 列 + 原因，且不写入** | `validate` 是 dry-run（0 写库），逐行落 `import_items`；`error_report.errors[]` 给 `{row_no, field, message}`；`execute` 默认**严格**：只要有 1 行没过就整体拒绝 | `test_wrong_file_reports_row_and_field_and_writes_nothing`；样例 `docs/samples/batch5-error-report.json`（第 57 行 `answer`、第 100 行 `chapter_code`） |
| ② | **同一份文件导 10 次，题量不变** | `content_hash` + `uq_questions_hash` 偏唯一索引；insert 模式命中即标 `duplicate` 跳过 | `test_idempotent_import_ten_times`（导 10 次后题量 = 基线 + 1） |
| ③ | **全链路导入 → 回滚，题量回到导入前** | 回滚按 `content_change_logs.batch_id` 反查本批的 `create`/`update`，逐条还原 | `test_full_pipeline_execute_publish_rollback`、`test_rollback_restores_upserted_questions` |
| ④ | **中途异常 → 整批回滚，不留半截数据** | `execute` 整批一个事务；任何一行抛错 → 整个事务 rollback | `test_mid_import_failure_rolls_back_everything`（见 §5 坑 27 的制造手法） |
| ⑤ | **6000 道仿真题真灌进库，success=6000 / failed=0** | upsert 模式命中已有 6000 题，全部更新而非重复插入 | `test_seed_bank_full_6000_upsert`（`YIJIAN_BIG_IMPORT=1` 门控）；实测明细见 §4.2 |
| ⑥ | **researcher 只能导入自己专业的题** | `scope_subject_ids()` 单一事实源；批次科目与逐行 `subject_code` 两道闸 | `test_researcher_data_scope` + `probe-researcher-scope.py`（**16/16 PASS**，见 §4.4） |

---

## 三、后端设计

### 3.1 接口清单（7 个）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `POST` | `/admin/imports/upload` | `question:import` | 建批次。**不写任何题**，只落 `import_batches(status=pending)` |
| `POST` | `/admin/imports/{id}/validate` | `question:import` | 逐行试算（dry-run，0 写库），逐行落 `import_items` |
| `GET` | `/admin/imports/{id}` | `question:read` | 状态 + 统计 + 错误报告 + 逐行结果（分页） |
| `POST` | `/admin/imports/{id}/execute` | `question:import` | 整批一个事务执行；可选 `allow_partial` / `publish` |
| `POST` | `/admin/imports/{id}/publish` | `question:publish` | 本批题 `draft → published` |
| `POST` | `/admin/imports/{id}/rollback` | `question:rollback` | 整批回滚 |
| `GET` | `/admin/imports` | `question:read` | 批次列表（按创建时间倒序，可按 `status` 过滤） |

**权限为什么这么分**：上传/校验/执行是"批量导入"这一件事的三个阶段，共用
`question:import`；发布与回滚是**权责不同**的独立动作，各自独立权限
（`admin` 角色刻意**没有** `question:rollback`，见 `db/schema.sql` §12）。

**`GET /admin/imports` 注册在 `/{batch_id}` 之前** —— 否则路径参数会把 `upload`
当成一个 batch_id 吃掉。

### 3.2 导入模板：24 列

`docs/07 §4.2` 定义了 19 行字段，其中 `option_a … option_f` 一行展开为 6 个物理列，
落到导入模板就是 **24 列**：

```
subject_code, chapter_code, kp_code, type, stem,
option_a, option_b, option_c, option_d, option_e, option_f,
answer, answer_points, analysis, score, difficulty, exam_year,
source_type, source_name, source_license, tags,
case_group_id, material, media_urls
```

`answer_points` 用 `评分点1|2分;;评分点2|3分` 的文本格式，落库成 jsonb 的评分点数组。

### 3.3 几个刻意的设计

- **`validate` 同步返回，不异步化**。docs/07 §4.3 画的是
  `pending → parsing → validating` 异步状态机，但 6000 行实测 **< 1s**；
  为此引入轮询/SSE 的复杂度当前不划算。这是**与文档的已知偏差**，在接口
  docstring 里写明了。
- **`execute` 默认严格**（含错就不写）。docs/07 §4.4 的示例注释是"只导 insert，
  跳过错行"，但验收标准 ① 明确要求"不写入"。**两处冲突时选了更严的一侧**：
  默认整批成功或整批失败；确需只导通过行时显式传 `allow_partial=true`
  —— 能力保留了，但不会"默认静默半灌"。
- **同一批只能执行一次**。判断依据不是状态字段，而是
  `content_change_logs WHERE batch_id=ANY(...)`（见 §5 坑 26）。
- **upsert 命中老题时不覆盖 `status`**。把已发布的题因"内容被重导"打回草稿
  是运维事故。
- **原始文件落盘**。`_FILE_STORE` 曾是个从未被写入的内存字典（坑 25），
  现改为 `%TEMP%/yijian-import-files/<batch_id>.<type>` 的**原子写**
  （先写 `.part` 再 `os.replace`），`execute` 时按需重读全文。
  内存里只留前 200 字符的瘦身行，避免把 20MB 文件挂在进程里。
- **xlsx 显式拒绝**。docs/07 §4.1 把 xlsx 列为"推荐"，但本批不做 xlsx 解析
  （需要 openpyxl + 富文本受限规则）。拒绝时给出可执行替代方案，
  而不是按扩展名猜格式静默当 CSV 读。

---

## 四、验收结果

### 4.1 后端用例

```
$ powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepRunning
======================== 53 passed, 1 skipped in 9.85s ========================
[local-verify] 全部通过。
```

`test_admin_v5.py` 13 条（12 条常规 + 1 条 6000 行门控）：

| 用例 | 覆盖 |
|---|---|
| `test_upload_rejects_unsupported_and_malformed_files` | xlsx 拒绝、空文件、非 csv/json |
| `test_wrong_file_reports_row_and_field_and_writes_nothing` | **①** |
| `test_idempotent_import_ten_times` | **②** |
| `test_full_pipeline_execute_publish_rollback` | **③** |
| `test_rollback_restores_upserted_questions` | ③（upsert 路径） |
| `test_mid_import_failure_rolls_back_everything` | **④** |
| `test_execute_twice_and_rollback_twice_are_rejected` | 幂等边界 |
| `test_researcher_data_scope` | **⑥** |
| `test_permission_wall` | 无角色账号全接口 403 |
| `test_list_and_detail_contract` | 信封形状 + **ID 必须是字符串**（守住坑 1/21） |
| `test_status_filter` | 批次列表状态过滤 |
| `test_seed_bank_mapping_matches_existing_rows` | 300 行种子映射自检（全部 duplicate） |
| `test_seed_bank_full_6000_upsert` | **⑤**（`YIJIAN_BIG_IMPORT=1` 时启用） |

用例设计原则与 Batch 4 一致：**每个用例自己造数据**，写进库的在结束前都回滚掉，
用例之间不相互污染。

### 4.2 6000 道仿真题真灌进库（验收 ⑤）

```
total=6000  success=6000  failed=0  updated=6000
```

导入后直查 PG 复核：

| 检查项 | 结果 |
|---|---|
| `GET /admin/questions` 的 total | **不变** —— 6000 题全部命中 upsert，无一新增 |
| `max(version)` | 3（被 upsert 顶了一轮，符合预期） |
| `content_change_logs` 本批行数 | 6000 |
| `kp_id IS NULL` 的题数 | 157 —— 与种子本身一致（这 157 道原本就没有知识点），**没有被洗成 NULL** |
| 抽检 `700000001` | 章节/知识点/answer 完整，`version=2`，hash 对得上 |
| 重复题 | 0 |

> 之所以是 **upsert 而不是 insert**：这 6000 道题在 Batch 1 就已经灌进库了。
> 验收 ⑤ 要的是"6000 道真灌进去且成功 6000"，upsert 命中 6000 = update 6000，
> 正是这条标准要证明的路径。

### 4.3 错误报告样例（验收 ①）

文件：`docs/samples/batch5-wrong-file.csv`（100 行，第 57、100 行有问题）
报告：`docs/samples/batch5-error-report.json`

```json
"error_report": {
  "total_errors": 2,
  "truncated": false,
  "errors": [
    { "row_no": 57,  "field": "answer",       "message": "答案 D 不在选项中（本题选项：A、B、C）" },
    { "row_no": 100, "field": "chapter_code", "message": "章节编码不存在：SW-SZ 下没有 SZ-99" }
  ]
}
```

`row_no` 是**文件里的数据行号（从 1 起，不含表头）**，教研据此在 Excel 里直接跳行；
`field` 精确到列名。错误最多回传 200 条，`total_errors` 是完整计数。

### 4.4 researcher 数据范围（验收 ⑥）

```
$ python tools/local-verify/probe-researcher-scope.py
researcher scope = subject:2007 (SW-SZ)
gate 1 - batch pinned to a foreign subject (subject_id=2001)
  PASS  refused with 40301
  PASS  message mentions data scope
  PASS  no batch row created / no write
gate 2 - file mixing own subject with a foreign subject
  PASS  failed_rows == 1
  PASS  error points at field=subject_code
  PASS  strict execute refused (40901)
  PASS  partial execute ok (0) / imported exactly 1 row / count +1
rollback restores the baseline
  PASS  count back to baseline
========================================================
PASS 16 / FAIL 0
```

两道闸：**批次级**（把批次钉在别的科目上 → 建批次就 403）和
**行级**（文件里混入别专业的行 → 该行报 `subject_code` 超出数据范围，
本专业的行照常通过）。用的是 `seed-demo-users.ps1` 落地的**真实账号**
（`13900000002 / Researcher@123456`），不是用例里临时造的。

---

## 五、验收过程中实测 / 自审抓出的 5 个缺陷

### 缺陷 1：含错的文件照样写库（★ 实测）

`validate` 报 2 行错，`execute` 却**写了 98 行**。这是 `docs/07 §4.4`
（"跳过错行"）与验收标准 ①（"不写入"）的正面冲突。
**已修**：`execute` 默认严格（`failed_rows > 0` 直接 `40901`），
`allow_partial=true` 保留 docs/07 的能力。详见坑文档 **第 24 条**。

### 缺陷 2：上传的原文从没落盘（★ 自审发现）

`_FILE_STORE: dict[int, bytes]` 声明了、也读了，但 `create_batch`
**从来没写过** → `execute` 永远报"原文件已不可用"。
**已修**：磁盘落地 + 原子写；`execute` 前还会检查文件在不在。
详见坑文档 **第 25 条**。

### 缺陷 3：`execute` 允许覆盖 `mode`，且 `done` 分不清"待执行/已执行"（★ 自审发现）

`execute` 能改 `mode`，导致"校验按 insert、执行按 upsert"的分裂；
同时 `done` 这个状态既表示"校验完成待执行"又表示"已执行完成"。
**已修**：去掉 `mode` 覆盖；用 `content_change_logs` 反查该批是否已执行
（**避免往 `status` 的 CHECK 里加新枚举值**，那样要改 schema）。
详见坑文档 **第 26 条**。

### 缺陷 4：校验拦得住业务规则，拦不住**列宽**（★ 实测）

列宽校验发生在 PG 层，`validate` 看不到 → 于是"列宽超限"成了构造
"④ 中途异常"最方便的手法：让 `source_name` 超过 `VARCHAR(160)`，
`validate` 全绿、`execute` 在第 N 行炸 → 验证整批回滚。
**这是有用的手法而非缺陷**，但必须写下来，否则下次会以为是校验漏了。
详见坑文档 **第 27 条**。

### 缺陷 5：Batch 1 的导出格式 ≠ Batch 5 的导入模板（★ 实测）

直接把 `data/seed/questions.json` 喂给导入接口，会把 6000 道题的章节**洗成 NULL**
—— 导出侧用 `chapter_id` 数字，导入模板要 `chapter_code` 编码。
**已修**：写了转换器 `tools/local-verify/import-sim-bank.py`（从库里取编码映射，
顺带对齐 `content_hash`，并修正 `answer_points` 整数分值与 `partial_credit` 的规范化）。
详见坑文档 **第 28 条**。

---

## 六、遗留事项（明确不做，留痕）

| # | 事项 | 说明 |
|---|---|---|
| 1 | xlsx 解析 | 本批显式拒绝。要做需要 openpyxl + 富文本受限规则（docs/07 §4.1 的"推荐格式"） |
| 2 | 前端导入页 | 本批只交付后端管道。B 端的"上传 → 看报告 → 确认 → 发布"三屏未做 |
| 3 | Meilisearch 推送 | docs/07 §4.3 第 ④ 步要求异步推送，当前没接搜索引擎 |
| 4 | `import_batches` 无清理策略 | 批次与 `import_items` 会持续增长；建议后续加"保留 N 天"的清理任务 |
| 5 | 单题回滚 | docs/07 §6.2 的"单题回滚到历史版本"仍属后续批次（Batch 4 已声明不做） |
| 6 | 大文件全量读内存 | `execute` 会重读整个文件；20MB 上限下可接受，真要更大的文件需改流式 |
| 7 | `python-multipart` 依赖 | 为 `UploadFile` 新增，已写入 `apps/api/requirements.txt`；老环境需 `pip install -r` 一次 |

---

## 七、复现步骤

```bash
# 1) 一键起依赖 + 跑全部用例（PG + fakeredis 版 API，API 在 :8123）
#    跑之前确认 8123 空闲：脚本会硬失败，不会"假绿"（坑 17）
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepRunning

# 2) 数据范围验收⑥：先落账号，再跑探针
powershell -ExecutionPolicy Bypass -File tools/local-verify/seed-demo-users.ps1
python tools/local-verify/probe-researcher-scope.py

# 3) 手工走一遍 ①②③④（会自建脏数据并在结束时回滚干净）
python tools/local-verify/probe-import-pipeline.py
#    → 产出 docs/samples/batch5-wrong-file.csv + batch5-error-report.json

# 4) 6000 道仿真题全量 upsert（验收⑤，约 30s+）
python tools/local-verify/import-sim-bank.py --mode upsert --publish

# 5) 只跑导入那一组用例
cd apps/api
AI_BASE=http://localhost:8123 \
DATABASE_URL=postgresql+asyncpg://yijian@127.0.0.1:55432/yijian \
YIJIAN_BIG_IMPORT=1 python -m pytest tests/test_admin_v5.py -v
```

### 账号

| 角色 | 手机号 | 密码 | 数据范围 |
|---|---|---|---|
| 超级管理员 | `13800000000` | `Admin@123456` | global |
| 只读观察员 | `13900000001` | `Viewer@123456` | global |
| 教研（市政） | `13900000002` | `Researcher@123456` | `subject:2007`（SW-SZ） |

---

## 八、本地验收工具一览

| 文件 | 作用 |
|---|---|
| `tools/local-verify/probe-import-pipeline.py` | 手工走 ①②③④，自建脏数据、自清理，产出错误报告样例 |
| `tools/local-verify/probe-researcher-scope.py` | 验收⑥，对真实种子账号打两道闸（16 项断言） |
| `tools/local-verify/import-sim-bank.py` | 把 `data/seed/questions.json` 转成 24 列导入模板 |
| `tools/local-verify/cleanup-probe-batch.py` | 一次性回滚助手（探针中途挂掉后清场） |
