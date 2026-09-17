# Batch 6：前端导入向导 —— 方案与验收

> 目标：把 Batch 5 已经跑通的导入管道，包成一套**教研自己能用**的 B 端界面。
> Batch 5 的后端是给脚本调的（`tools/local-verify/import-sim-bank.py`），
> 本批要回答的问题只有一个：**一个不写代码的教研，能不能自己把一批题导进去、发现错、改完再导、导错了还能收回？**

前置：Batch 5 已交付 7 个 `/admin/imports/*` 接口（见 `docs/11-Batch5-导入管道-方案与验收.md`）。
本批**新增第 8 个接口** `GET /admin/imports/{id}/changes`（批次变更日志），
其余 7 个**零改动**（仅 `admin_imports.py` 追加路由，不动既有 handler 语义）。

---

## 1. 范围与交付物

### 1.1 页面（4 个）

| 路由 | 定位 | 需要的权限 |
|---|---|---|
| `/imports` | 批次列表：历史 + 状态 + 回滚入口 | `question:read` |
| `/imports/new` | 三步向导：上传 → 校验 → 预览确认 → 执行 → 发布 | `question:import`（上传/校验/执行）、`question:publish`（发布） |
| `/imports/[id]` | 批次详情：状态 + 统计 + 逐行结果 + 变更日志 + 回滚 | `question:read`；回滚另需 `question:rollback` |
| 错误报告下载 | 从**校验结果页**和**批次详情页**两处都能下 CSV | 同上（纯客户端 Blob，不额外要权限） |

导航入口在 `MODULE_ENTRIES` 里，权限用 `question:read`（列表/详情是只读接口）。
放在「题库管理」之后 —— 顺序即优先级，正常落地页仍是题库列表。

### 1.2 复用组件（6 个）

| 组件 | 行数 | 承担 |
|---|---|---|
| `StepWizard.tsx` | 132 | 三步条：当前高亮、已完成打勾、**不可跳步** |
| `ErrorReportTable.tsx` | 179 | 错误表：按 `row_no` 排序、点行展开字段级详情、下载 CSV |
| `ImportStatCards.tsx` | 51 | 新增/更新/跳过/未通过 四张卡片 |
| `ImportStatusFlow.tsx` | 118 | 状态流转可视化 |
| `RollbackDialog.tsx` | 188 | 回滚二次确认（含失败降级 + 重试） |
| `InlineError.tsx` | 99 | 内联失败态：说清 + 重试按钮 + trace_id |

### 1.3 其他

| 文件 | 说明 |
|---|---|
| `src/hooks/useImports.ts` (182) | 7 个 query/mutation：list / detail / changes / upload / validate / execute / publish / rollback |
| `src/lib/import.ts` (397) | 状态标签、状态流推导、CSV 构造、下载、上传前置校验 |
| `apps/api/app/api/v1/admin_imports.py` | +38 行：`GET /{batch_id}/changes` |
| `apps/api/app/schemas/admin_import.py` | + `ImportChangeOut` |
| `apps/api/app/services/import_service.py` | + `list_batch_changes()` |
| `apps/api/tests/test_admin_v5.py` | + `/changes` 用例（53 → 54 passed） |
| `tools/local-verify/import-sim-bank.py` | + `--out`：只产出严格 24 列模板 CSV，供浏览器手工走查 |
| `tools/local-verify/ab-capture-download.js` | 测试基建：拦截 `URL.createObjectURL`，抓下载内容（见 §7.2） |
| `docs/samples/batch6-questions.csv` | 好文件：12 行 × 24 列，single/multiple 混排 |
| `docs/samples/batch6-questions-2.csv` | 第二份好文件（内容不同），用于重复导入验证 |
| `docs/samples/batch6-error-report.csv` | 错误报告样例（从 UI 下载的真实产物） |

**不做**：组卷引擎、记忆曲线、考试模块、C 端任何页面。

---

## 2. 七项 B 端交互：需求 → 实现 → 证据

| # | 需求 | 实现 | 证据 |
|---|---|---|---|
| 1 | 步骤条：当前高亮 / 已完成打勾 / **不可跳步** | `StepWizard.tsx`：`onStepClick` 只在 `index <= current` 时生效；未校验时「预览确认」是 `disabled`，不是"点了报错" | `02-upload.png`、`03-validation-dryrun.png` |
| 2 | 错误表：按 `row_no` 排序 / 点行看字段级详情 / 下载 CSV（列 `row_no,field,message`） | `ErrorReportTable.tsx`：`[...errors].sort((a,b)=>a.row_no-b.row_no)`；展开行显示 `field + message`；下载走 `errorsToCsv()` | `07-validation-errors.png`、`08-error-detail.png`、`09-error-csv-downloaded.png` |
| 3 | 校验页明确提示"本次为 dry-run，未写库" | 校验结果页顶部常驻提示条 + 按钮文案「上传并校验（不写库）」 | `03-validation-dryrun.png` |
| 4 | 预览页给**具体数字**，不能只写"通过" | `ImportStatCards` 四张卡 + 一句自然语言结论："本次执行将向题库写入 **N** 道题，另有 **M** 道因重复保持不动。" | `04-preview.png`、`16-duplicate-preview.png` |
| 5 | 回滚二次确认：写"将软删除 N 道题" + 要求输入批次号 | `RollbackDialog.tsx`：`软删除 = success_rows - updated_rows`（**不能直接用 success_rows**，见 §7.1）；输入框 `#rb-batchno` 必须与 `batch_no` 全等，"确认回滚"才解禁 | `17-rollback-confirm.png`、`18-rollback-typed.png` |
| 6 | 失败态降级：上传断网 / 执行 500 / 回滚失败 各有提示 + 重试 | 上传：`validateLocalFile()` 前端先拦 + 网络错误进 `InlineError`；执行：`InlineError` + 重试；回滚：**失败信息画在弹窗内部**（弹窗保持打开，便于重试） | `InlineError.tsx`、`RollbackDialog.tsx` |
| 7 | 状态流转可视化：`pending → validated → importing → done → rolled_back` | `importStatusFlow()` 输出 5 个 `FlowStep`，每步带 `stage`（spec 名）与 `hint`（后端枚举）。`ImportStatusFlow` 把 `hint` 以等宽小字显示在后端标签下 —— 一个组件同时说清"业务语义"和"库里的值" | `10-imports-list-statuses.png`、`19-rolled-back.png` |

### 2.1 关于交互 7 的一个设计取舍

spec 里写的是 `validated` 和 `published`，但**后端枚举没有这两个值**：

| spec 里的名字 | 后端真实值 | 为什么 |
|---|---|---|
| `pending`（建批次） | `pending` | 一致 |
| `validated`（校验完成） | `done`（且 `success_rows+updated_rows == 0`、`can_execute == true`） | 坑 26：不给 `status` 加枚举值，改用"是否已执行"派生 |
| `importing` | `importing` | 一致（执行中的瞬时态） |
| `done`（已入库） | `done`（且已执行） | `done` 同时表示"校验完"和"执行完"，靠 `content_change_logs.batch_id` 区分 |
| `rolled_back` | `rolled_back` | 一致 |
| `published` | **不单独建状态**，发布是 `questions.status: draft → published` 的动作，批次自身仍停在 `done` | 批次是"导入"的单位，发布是"题目"的状态 |

处理方式：**不新增后端枚举，也不在前端编造**。`FlowStep` 同时带两套名字，
UI 主标签用 spec 的业务名（教研看得懂），下面小字用后端真实值（后端排查对得上）。
这是坑 26 的直接延续 —— 详见 `B端联调坑.md` 坑 26。

---

## 3. 错误报告 CSV 的实现

**纯客户端**（`errorsToCsv()` + `downloadTextFile()`），不占一个后端接口。理由：
错误报告在 `GET /admin/imports/{id}` 的响应里已经全量下发，再为它开一个下载接口，
等于把同一份数据分两条路维护。

关键细节（`src/lib/import.ts`）：

| 细节 | 做法 | 为什么 |
|---|---|---|
| 列头 | 恰好 `row_no,field,message` | spec 硬要求；少了 `row_no` 就没法对着文件改 |
| BOM | 文本以 `\ufeff` 开头 | 不加 BOM，Excel 双击打开中文是乱码 |
| 行尾 | `\r\n` | Excel 对 LF-only 的 CSV 兼容性一般 |
| 转义 | 含 `,` `"` CR LF 的单元格加引号并把 `"` 写成 `""` | 错误信息里逗号极常见（`答案 D 不在选项中（本题选项：A、B、C）`），不转义整表错位 |
| 排序 | 按 `row_no` 升序 | 用户拿着 CSV 是去改文件的，必须按行号走 |
| 截断 | 若后端截断了错误列表，**在文件末尾追加一行说明** | 否则教研拿到 200 行会以为"只有 200 个错" |
| 文件名 | `错误报告-{batch_no}.csv`，非法字符替换为 `_` | 多批对比时能区分 |

**实测产物**（`docs/samples/batch6-error-report.csv`，155 字节）：

```
BOM: True | bytes: 155 | CRLF: True
row_no,field,message
57,answer,答案 D 不在选项中（本题选项：A、B、C）
100,chapter_code,章节编码不存在：SW-SZ 下没有 SZ-99
```

精确到**行 + 字段 + 原因**。BOM `EF BB BF` 在字节层面确认存在（见 §7.2 的抓取方式）。

---

## 4. 新增接口：`GET /admin/imports/{id}/changes`

批次统计只说得出"新增 3 / 更新 2"，说不出**动了哪几道题、谁动的、什么时候**。
批次详情页的「变更日志」需要后者。

```
GET /api/v1/admin/imports/{batch_id}/changes?page=1&page_size=50
权限：question:read
```

- `counts` 是**全量**按 `action` 汇总（`create` / `update` / `publish` / `rollback`），**不随分页变化** —— 否则前端分页翻到第 2 页，汇总数就变了。
- 题干预览由后端截断（`left(stem, 120)`），6000 条的批次也不会把响应撑爆。
- 批次不存在 → `40401`，**不是空列表**。「什么都没改」和「批次不存在」是两回事，接口层就该分开。
- 数据源是 `content_change_logs.batch_id` —— 与 Batch 5 判定"同批不可重复执行"用的是同一列，**一个事实源**。

---

## 5. 验收结果

### 5.1 验收 1：好文件走完整流程（校验 → 执行 → 发布）

用 `tools/local-verify/import-sim-bank.py --out` 从 Batch 1 的 6000 道仿真题里
切出 12 行严格 24 列模板（`docs/samples/batch6-questions.csv`，single/multiple 混排）。

```
上传 → 校验（dry-run：新增 12 / 更新 0 / 跳过 0 / 未通过 0）
     → 预览（写入 12）
     → 执行（status=done，12 写入，0 失败，0.10s）
     → 发布
```

| 检查点 | 结果 |
|---|---|
| 校验页显示 dry-run 提示 | ✅ `03-validation-dryrun.png` |
| 预览页给具体数字（非"通过"） | ✅ `04-preview.png` |
| 执行成功 | ✅ `05-executing.png` / `06-executed.png` |
| 发布后 `status: draft → published` | ✅ `14-wizard-published.png` |
| **库中 published 计数 6012 → 6024**（+12） | ✅ API 核验 |

### 5.2 验收 2：故意写错的文件 → 错误报告精确到行 + 字段，CSV 可下载可打开

用 Batch 5 的 `docs/samples/batch5-wrong-file.csv`（100 行，第 57 / 100 行有错）。

| 检查点 | 结果 |
|---|---|
| 错误表按 `row_no` 排序 | ✅ `07-validation-errors.png`（第 57 行、第 100 行） |
| 点行展开字段级详情 | ✅ `08-error-detail.png` |
| CSV 列头恰好 `row_no,field,message` | ✅ 字节级核验（§3） |
| CSV 带 BOM，Excel 打开不乱码 | ✅ `EF BB BF` 在字节层面确认 |
| CSV 从**两处**都能下 | ✅ 校验结果页 + 批次详情页（同一 `ErrorReportTable`） |

### 5.3 验收 3：同一份文件导两次 → 第二次全跳过、库中行数不变

用 `docs/samples/batch6-questions-2.csv`（已导过一次并发布），再导一次：

| 检查点 | 结果 |
|---|---|
| 校验/预览页显示全跳过 | ✅ **新增 0 / 更新 0 / 跳过 12 / 未通过 0**，文案"本次执行将向题库写入 **0** 道题，另有 12 道因重复保持不动" |
| 库中行数不变 | ✅ published 保持 **6024**（未因重复导入变化） |

证据：`15-duplicate-validate.png`、`16-duplicate-preview.png`。

> 依赖 `content_hash` 归一化：Batch 5 保证同一份题无论从 CSV 还是 JSON 进，
> 算出的 hash 一致（`docs/11` 有详述），所以"内容完全相同"能被精确识别。

### 5.4 验收 4：回滚 → 二次确认 → 归档 → `rolled_back`

对 5.1 里发布的那一批（`IMP-20260917084515-39457`，12 道题）执行整批回滚。

| 检查点 | 结果 |
|---|---|
| 回滚入口受 `can_rollback` 门控 | ✅ 只在该批可回滚时出现 |
| 二次确认弹窗写"将软删除 12 道题" | ✅ `17-rollback-confirm.png` |
| 要求手输批次号，输错不解禁 | ✅ `18-rollback-typed.png`（输入框 `#rb-batchno`） |
| 回滚后状态 `rolled_back`，`can_rollback: false` | ✅ `19-rolled-back.png` |
| **库中该批 12 道题归档**（published 6024 → 6012） | ✅ API 核验 |
| 变更日志三方齐全 | ✅ `{create: 12, update: 0, publish: 12, rollback: 12}` |

### 5.5 验收 5：截图存档

`apps/admin/docs/screenshots/batch6/`，共 **18 张**。spec 要求的 8 张对应关系：

| spec 要求 | 文件 |
|---|---|
| 批次列表 | `01-imports-list.png`、`10-imports-list-statuses.png` |
| 上传页 | `02-upload.png` |
| 校验结果页 | `03-validation-dryrun.png`（好文件）、`07-validation-errors.png`（有错） |
| 错误报告详情 | `08-error-detail.png` |
| 预览页 | `04-preview.png`、`16-duplicate-preview.png`（全跳过） |
| 执行中 | `05-executing.png` |
| 批次详情 | `11-batch-detail.png` |
| 回滚确认弹窗 | `17-rollback-confirm.png`、`18-rollback-typed.png` |

额外的证据图：`06-executed.png`（执行完成）、`09-error-csv-downloaded.png`（CSV 已下载）、
`12-batch-changes.png`（变更日志）、`14-wizard-published.png`（已发布）、
`15-duplicate-validate.png`（重复导入校验）、`19-rolled-back.png`（已回滚）。

> 截图视口 1600×1100，用 `--full` 抓整页，高度按内容自适应（最长 4111px）。

### 5.6 验收 6：`run-smoke.ps1` 全绿，Batch 2/3/4/5 不回归

```
54 passed, 1 skipped in 10.20s
```

Batch 5 是 `53 passed, 1 skipped`；本批 +1（`/changes` 用例），**无回归**。
`1 skipped` 是 Batch 5 就有的 6000 行环境变量门控用例（需显式开启才跑）。

### 5.7 验收 7：文档

- 本文（`docs/12-Batch6-导入向导-方案与验收.md`）
- `apps/admin/docs/B端联调坑.md` 增补坑 29–33
- `apps/admin/README.md` §3 增补「如何验证导入流程」，并同步版本号/页面表/目录树

### 5.8 静态门禁

```
npm run typecheck   # tsc --noEmit → exit 0
npm run build       # Next.js 生产构建 → exit 0，3 条新路由全部生成
```

---

## 6. 实测缺陷与修复

| # | 缺陷 | 处理 |
|---|---|---|
| 1 | 批次列表页回滚失败只有 `catch {}` + toast，**弹窗里没有任何失败信息**，用户既不知道成没成、也找不到重试入口 | `RollbackDialog` 新增 `error` prop，在弹窗内部画 `InlineError`（含"整批一个事务、失败即一道题都没动"的说明 + 重试）；两个调用页把 `rollback.error` 传进去 |
| 2 | `ImportStatusFlow` 的步骤标签与后端枚举混着写，教研看不出"校验完了"到底是哪个状态 | `FlowStep` 增加 `stage`（spec 业务名）与 `hint`（后端值）两个字段，UI 同时显示两套名字（§2.1） |
| 3 | `P.questionRollback` 定义了但**从没被用过**，回滚入口只看了 `can_rollback` | 回滚入口改为 `can_rollback && can(perms, P.questionRollback)` 双闸；死码清除 |
| 4 | 截图目录里混进过 4 张**登录页**截图（上一轮走查中断的产物），文件名却写着 `01-imports-list.png` | 全部删除重抓；并在提交前用 md5 去重，删掉一张与自己高度截图逐字节相同的 `13-published.png` |
| 5 | 仓库根目录有个 27KB 的残留文件 `ProtectWorkBuddyONE`（PNG） | 确认是 agent-browser 路径被空格截断产生的垃圾，删除；**它一度进过 `git status`，差点被提交** |

---

## 7. 走查基建的两个发现

### 7.1 回滚数字必须拆成"软删除"和"还原"两个数

后端 `success_rows` 是**写入成功行数**，在 upsert 模式下**包含命中已有题的更新行**。
那些题回滚时是被**还原**（版本回退），不是被删除。直接拿 `success_rows` 当"将软删除 N 道题"，
在 upsert 批次上会**夸大破坏范围**（12 道里可能 10 道只是更新，却告诉用户"要删 12 道"）。

```ts
软删除 = max(0, success_rows - updated_rows)   // 本批真正新建的题
还原   = updated_rows                          // 本批覆盖过的老题，回到导入前那一版
```

弹窗把两个数**分开说**，并注明跳过的重复题"本就不属于本批写入，不受影响"。

### 7.2 无头浏览器抓不到下载 —— 改成拦 `URL.createObjectURL`

`agent-browser` 的 `download` 命令在本环境**恒定返回 `Download was canceled`**。
隔离验证过：连一个纯 `data:text/plain;base64,...` 的 `<a download>` 都抓不到 ——
所以**不是应用的问题**，是这个环境不暴露下载事件。

排查过程中我一度怀疑是 `revokeObjectURL` 时机太早（`setTimeout(..., 0)`），
改成了 1000ms 去验证 —— **证伪后已还原**。留下的判断：
在没有新证据前，不因为一个环境限制去改产品代码。

替代方案（`tools/local-verify/ab-capture-download.js`）：用
`AGENT_BROWSER_INIT_SCRIPTS` 注入一段脚本，包住 `URL.createObjectURL`，
把传入的 Blob 的 `type` / `size` / 前 4 字节 / 全文都存到 `window.__yb*` 上，
再用 `eval` 取回来。这样验证的是**应用真实产出的字节**，比"文件是否下载到磁盘"更强：
BOM 在不在、是不是 CRLF、列头对不对，全部可断言。

---

## 8. 遗留事项

| # | 事项 | 说明 |
|---|---|---|
| 1 | 批次列表不支持按 `source_type` / 时间范围筛选 | 后端 `GET /admin/imports` 目前只支持 `status` + 分页；数据量上来再加 |
| 2 | 单文件上限 20MB 写死在前端 | `MAX_FILE_BYTES` 与后端 `MAX_FILE_BYTES` 是**两处常量**，改一处要记得另一处（未抽出共享契约） |
| 3 | 执行无进度条 | 执行是整批一个事务、后端不回流进度，前端只能显示"执行中"；6000 行实测 0.10s，暂无痛点 |
| 4 | 变更日志不可导出 | 只有分页查看；教研想留档目前得自己截图 |
| 5 | 回滚不产生"可再导入"的快捷入口 | 回滚后要重新走一遍上传；弹窗里的文案已明确说清"系统不提供再撤销回滚的入口" |

---

## 9. 复现步骤

```bash
# 0) 起环境（本机 PostgreSQL + fakeredis，API 在 8123）
cd "tools/local-verify"
python serve_fake_redis.py --pg-port 55432 --api-port 8123 --log-file /tmp/api.log &

# 1) 前端（注意：先删 .next 再起 dev，否则 build 产物会让页面不 hydrate —— 坑 17）
cd ../../apps/admin && rm -rf .next && npx next dev -p 3000

# 2) 生成走查用文件（严格 24 列模板）
cd ../..
DATABASE_URL="postgresql+asyncpg://yijian@127.0.0.1:55432/yijian" \
  python tools/local-verify/import-sim-bank.py --out docs/samples/batch6-questions.csv --limit 300

# 3) 接口层验收（全绿：54 passed, 1 skipped）
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -KeepRunning

# 4) 浏览器走查：登录 13800000000 / Admin@123456
#    /imports/new 走 ①上传 → ②校验 → ③预览 → 执行 → 发布
#    /imports/{id} 看状态流转 / 变更日志 / 回滚二次确认
```

截图基建（可选，用于验证下载产物）：

```bash
export AGENT_BROWSER_INIT_SCRIPTS="$PWD/tools/local-verify/ab-capture-download.js"
# 之后点「下载错误报告 CSV」，再：
#   eval "JSON.stringify({head:window.__ybBlobHead,text:window.__ybBlobText})"
```
