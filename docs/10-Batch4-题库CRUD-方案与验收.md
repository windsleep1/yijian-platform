# Batch 4 · 题库 CRUD —— 方案与验收

> 状态：**已完成**
> 交付方式：Pass 1（后端 7 个接口 + 用例）与 Pass 2（前端 3 个页面 + E2E + 文档）
> 合并为**一次** Batch 4 提交。
>
> 上游文档：`docs/09-Batch3-管理后台v0.1-方案与骨架.md`
> 本文只讲 Batch 4 增量；Batch 3 已定的约定（响应信封、`BigIntStr`、权限门禁、
> 表格状态入 URL 等）不再重复。

---

## 0. 一句话结论

题库从"只能读列表"变成**可增删改查**：后端 7 个接口 + 前端 3 个页面，
支持草稿/发布状态流转、乐观锁并发控制、版本快照与变更日志、软删除与批量软删除。
验收过程中实测出 3 个真实缺陷（见 §6），均已修复并留回归手段。

---

## 一、范围

| 层 | 内容 |
|---|---|
| 后端 | 7 个接口：列表 / 详情 / 新建 / 编辑 / 单删 / 批删 / 章节树 |
| 后端 | 3 张表参与：`questions`、`question_options`、`question_versions` + `content_change_logs` |
| 前端 | 3 个页面：`/questions`（列表）、`/questions/new`（新建）、`/questions/[id]`（详情+编辑） |
| 前端 | 4 个复用件：`QuestionForm`、`QuestionVersionDrawer`、`MarkdownPreview`、`useQuestions` |
| 验证 | `apps/api/tests/test_admin_v4.py`（12 条）+ 浏览器端到端走查（13 张截图） |

**不在本批**：批量导入、题目组卷、回滚历史版本、富文本编辑器（本批用
"Markdown/纯文本 + 右侧实时预览"，见 §4）。

---

## 二、B 端 6 个特征（需求 → 实现 → 证据）

| # | 需求 | 实现要点 | 证据 |
|---|---|---|---|
| ① | **答案互斥** | 单选题标第 2 个正确项时**自动取消**前一个；多选题要求 ≥2 个正确；答案由 `options[].is_correct` 反推，不单独传 | 截图 12；`validateDraft`；后端 `test_create_validation_and_compliance_rules` |
| ② | **保存自动 +1 版本 + 写变更日志 + 只读版本历史** | 每次保存 `version+1`、写 `question_versions` 快照与 `content_change_logs`；抽屉**明确声明不提供回滚** | 截图 13；`test_edit_optimistic_lock_and_history`、`test_change_logs_and_version_snapshots_in_db` |
| ③ | **批量删除栏仅选中 ≥1 时出现；二次确认复述"将软删除 N 道题"** | 选中态驱动操作栏渲染；确认弹窗复述数量 + 逐条 ID | 截图 06、07（文案："将软删除 2 道题（仅标记删除位，不改动题干与选项数据）"） |
| ④ | **列表默认 `is_deleted=false`；顶部"显示已归档"开关** | `include_deleted` 作为**非筛选键**存在 URL 里，切筛选/翻页不丢；开启后行上打"已归档"徽标 + 黄色警示条 | 截图 01、05；`useTableState` 的 `nonFilterKeys` |
| ⑤ | **章节下拉联动所选科目** | 章节选项来自 `/admin/chapters/tree`，按 `subject_id` 过滤；未选科目时章节禁用 | 截图 02；URL 里可见 `subject_id=1003&chapter_id=1304` |
| ⑥ | **富文本先用 Markdown/纯文本 + HTML 预览** | 题干与解析都是"左输入 / 右实时预览"；`MarkdownPreview` 把 Markdown 渲染为 React 元素（不走 `dangerouslySetInnerHTML`） | 截图 10、11 |

---

## 三、后端设计

### 3.1 接口清单（7 个）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/admin/questions` | `question:read` | 列表：科目/章节/题型/难度/状态/关键词 + 排序 + 分页 + `include_deleted` |
| `POST` | `/admin/questions` | `question:create` | 新建，返回完整 `QuestionDetail` |
| `GET` | `/admin/questions/{id}` | `question:read` | 详情（含选项、答案、解析、版本） |
| `PUT` | `/admin/questions/{id}` | `question:update` | 编辑（必须带 `version`，乐观锁） |
| `DELETE` | `/admin/questions/{id}` | `question:delete` | 单条软删除 |
| `POST` | `/admin/questions/batch-delete` | `question:delete` | 批量软删除（单次 ≤200） |
| `GET` | `/admin/chapters/tree` | `question:read` | 科目 → 章节树（供筛选联动） |

**为什么写操作返回完整详情而不是 204**：B 端页面写操作后要**就地更新**；
返回 204 就得整页刷新，会丢掉筛选条件与页内滚动位置。见 `admin_questions.py` 模块注释。

### 3.2 几个刻意的设计

- **答案不单独传**。`answer.value` 由 `options[].is_correct` 推导。
  两份数据必然不一致，索性只留一份。
- **`content_hash` 去重**。同题干+同选项即视为重复题，新建时直接返回冲突而不是默默入库。
- **乐观锁**。`PUT` 必须带 `version`，不匹配返回 `40901`；
  前端弹**常驻横幅**（不是一闪而过的 toast），并提供"重新加载最新版本"，避免用户白填一遍。
- **批量删除整批共用一个 `batch_id`**，便于追溯"谁在什么时候批量删了什么"。
- **`skipped` 不报错**。批量操作不该因其中一条有问题就整批失败；
  但这正是坑 21 的温床 —— 详见 §6。

---

## 四、前端设计

### 4.1 页面与复用件

```
src/app/(console)/questions/page.tsx        列表：筛选 + 排序 + 批量操作栏
src/app/(console)/questions/new/page.tsx    新建
src/app/(console)/questions/[id]/page.tsx   详情 = 编辑器 + 只读历史入口
src/components/QuestionForm.tsx             新建/编辑共用表单（含实时校验与预览）
src/components/QuestionVersionDrawer.tsx    只读版本历史抽屉
src/components/MarkdownPreview.tsx          Markdown → React 安全渲染
src/hooks/useQuestions.ts                   TanStack Query hooks + key 工厂
src/lib/question.ts                         纯函数领域逻辑（可单测）
```

`src/lib/question.ts` 把领域规则与 React 解耦：`validateDraft`、`toggleCorrect`、
`relabel`、`diffDraft`、`draftToCreate`、`draftFromDetail`。
好处是规则可以被"读一遍就对"，而不是散落在 JSX 的 `onChange` 里。

### 4.2 两个必须说清的实现细节

**（a）草稿初始化只用 `[id, version]` 做依赖，不用整个 `data` 对象**

```ts
useEffect(() => { /* 用 data 重建草稿 */ }, [dataId, dataVersion]);  // ← 不是 [data]
```

TanStack Query 在窗口重新聚焦时会后台 refetch。如果把整个 `data` 当依赖，
refetch 一回来就会**把用户正在填的内容冲掉**。用 `[id, version]` 做键，
只有"换了题"或"版本真的变了"才重建草稿。

**（b）"显示已归档"必须作为 `nonFilterKeys`**

`useTableState` 里区分"筛选键"和"非筛选键"：

- 点「清空筛选」时，筛选键回默认值，但 `include_deleted` / `order_by` / `order` **要保留**；
- 否则用户开了"显示已归档"再清筛选，开关会被悄悄关掉，看到的列表和预期不符。

---

## 五、验收结果

### 5.1 后端

```
$ AI_BASE=http://localhost:8123 \
  DATABASE_URL=postgresql+asyncpg://yijian@127.0.0.1:55432/yijian \
  python -m pytest -q
26 passed in 6.80s
```

`test_admin_v4.py` 覆盖 12 条：内容去重与答案推导、静态守卫（新增模块无未定义全局）、
章节树契约、列表筛选与排序、新建后回读、**校验与合规红线**、重复内容冲突、
**乐观锁与历史**、单条软删除、批量删除、**变更日志与版本快照落库**、权限墙。

### 5.2 前端

```
$ npx tsc --noEmit     # exit 0
$ npx next build       # exit 0
Route (app)                    Size     First Load JS
├ ○ /questions                7.27 kB   176 kB
├ ƒ /questions/[id]           8.24 kB   181 kB
└ ○ /questions/new            3.07 kB   169 kB
```

### 5.3 浏览器端到端走查（13 张截图）

截图目录：`apps/admin/docs/screenshots/batch4/`

| 文件 | 验证点 |
|---|---|
| `01-questions-list.png` | 列表默认态：筛选区 + 表格（题干/科目章节/答案/难度/状态/版本/更新时间） |
| `02-filter-subject-chapter.png` | 科目 → 章节联动 |
| `03-filter-advanced.png` | 题型 + 状态组合筛选 |
| `04-list-filtered-empty.png` | 空态区分"筛出来没结果" |
| `05-show-archived-toggle.png` | "显示已归档"开启：已归档徽标 + 黄色警示条 |
| `06-batch-select-bar.png` | 勾选 2 条后出现批量操作栏 |
| `07-batch-delete-confirm.png` | 二次确认复述"将软删除 2 道题" + 逐条 ID |
| `08-batch-delete-done.png` | 删除成功 toast：「已软删除 2 道题｜可在「显示已归档」中查看」 |
| `09-question-new-form.png` | 新建页 + 科目下拉（13 个科目） |
| `10-question-new-filled.png` | 填好后：「已标 1 个正确答案 · 选项 4/8」「校验通过，可以保存」 |
| `11-question-created.png` | 创建成功 → 落到详情页（v1），标注"保存后版本号会 +1" |
| `12-single-option-exclusive.png` | **特征①**：点 A 再点 B，A 自动取消 |
| `13-version-history-drawer.png` | **特征②**：只读版本历史，v2 当前 / v1 完整快照 + 明确声明不回滚 |

### 5.4 关键动作的实测证据（不只靠截图）

- **批量删除真的删掉了**：页面点完后直查接口，两道题 `is_deleted=true, version=2`。
- **新建真的落库正确**：`answer.value = ["A"]`，4 个选项齐全，`version=1`。
- **版本真的 +1 且写了日志**：抽屉里 v2 = "编辑题目：stem"，v1 = "创建题目 v1"，
  操作人与时间齐全。
- **单选互斥真的生效**：点 A → 选中数 1；点 B → 选中数仍为 1，且选中索引变成 B。

---

## 六、验收过程中实测出的 3 个缺陷

> 这三个都是 `tsc` 与 `next build` **永远发现不了**的 —— 它们要在真实浏览器 + 真实后端
> 上才暴露。这正是端到端走查不可省的理由。

### 缺陷 1：教研角色进不了后台（权限落地页写死）

`researcher` 有 `question:read`（题库的主要用户），但登录门禁与 4 处入口都把落地页
写死成 `/users`，导致该角色被踢到 403。
**已修**：收敛出 `MODULE_ENTRIES` 单一事实源，所有入口由权限推导落地页。

详见 `apps/admin/docs/B端联调坑.md` **第 20 条**。

### 缺陷 2：雪花 ID 在**请求方向**同样丢精度

旧代码 `ids: selected.map(Number)`。`Number()` 发生在 `JSON.stringify` **之前**，
请求体里带的已是舍入后的错值 → 后端 `code=0, deleted=0, skipped=[...]`，**不报错**。

这条比坑 1 更阴：**它不是必然触发，而是取值相关**。
雪花布局 `ts<<22 | worker<<12 | seq`、`worker_id=1`，低 22 位 = `4096 | seq`；
当前量级 ULP 恰好为 64，于是 `seq=0`（每毫秒第一个）**无损**、
`seq=1..63`（同一毫秒内第 2~64 个）**失真且塌缩到同一个值**。
手点新建总落在不同毫秒的 `seq=0`，所以一直没暴露 ——
但批量导入一旦落地就会当场翻车。

**已修**：`ids` 全链路改字符串（类型层 + 调用层），并新增请求方向的
`assertNoUnsafeIdInBody()` 体检；回归探针 `tools/local-verify/probe-id-precision.py`。

详见坑文档 **第 21 条**。

### 缺陷 3：「复制 ID」失败却照样报「已复制」

`void navigator.clipboard?.writeText(...)` + 无条件 `toast.success`。
剪贴板写入是异步且**可能被拒绝**的（实测 `NotAllowedError: Write permission denied`），
被 `void` 丢掉的拒绝变成 unhandled rejection（Next 开发覆盖层直接甩 `1 error` 红点），
而用户看到的仍是绿色「已复制」——**假成功**。

**已修**：改成 `await` + `catch` 分支，失败时报错并给出下一步。
验证：改完后 `unhandledrejection` 计数为 0，toast 变红。

详见坑文档 **第 22 条**。

---

## 七、遗留事项（明确不做，留痕）

| # | 事项 | 说明 |
|---|---|---|
| 1 | `GET /admin/chapters/tree` 暂用 `question:read` | 将来若有**课程模块**也要用章节树，应拆出 `subject:read` 或独立权限。**本批不改**，已在 `admin_chapters.py` 留 TODO |
| 2 | 版本回滚 | 抽屉里已明确说明不提供。回滚会牵动已发布试卷与作答记录，属后续批次的独立能力 |
| 3 | 富文本编辑器 | 本批 Markdown/纯文本 + 预览；真富文本需先定 XSS 白名单策略 |
| 4 | 剪贴板残留（Batch 3 代码） | `users/[id]/page.tsx`、`AuditDiffDrawer.tsx`、`providers.tsx`、`ErrorState.tsx` 同样是无条件报成功。建议后续抽 `copyText()` 统一收口 |
| 5 | `AssignRolesDialog` 的 `scope_id` | 库里是 `BigInteger`、响应侧按字符串下发，但该处手输后 `Number()` 转换。现状 `subjects.id` 是 `1003` 这类小数字，**暂未触发**；改的时候要连 `AssignRolesIn.scope_id` 一起换成 `BigIntStr` 并回归 Batch 3 用例。已留 TODO 注释 |
| 6 | `sms_daily_limit_per_ip = 20` | 用例跑两遍就会把当天额度用光（详见坑 23）。建议给测试环境调大或跳过计数 |

---

## 八、复现步骤

```bash
# 1) 起本地依赖（PG + 带 fakeredis 的 API，API 在 :8123）
pwsh tools/local-verify/serve-local.ps1 -Foreground

# 2) 后端用例（26 passed）
cd apps/api
AI_BASE=http://localhost:8123 \
DATABASE_URL=postgresql+asyncpg://yijian@127.0.0.1:55432/yijian \
python -m pytest -q

# 3) ID 精度回归探针（全部通过）
python tools/local-verify/probe-id-precision.py

# 4) 前端
cd apps/admin
npm run typecheck && npm run build
npm run dev            # http://localhost:3000

# 5) 登录：13800000000 / Admin@123456（超管，落地 /questions）
#    教研账号可验证「有 question:read 但没有 user:read」也能正常进题库

# 6) E2E 若做过软删除，跑完可以用这个恢复数据集
python tools/local-verify/restore-e2e-softdeleted.py          # 先看
python tools/local-verify/restore-e2e-softdeleted.py --restore # 再恢复
```

> ⚠️ 第 4 步跑 `next build` 会写 `.next`，**与正在运行的 `next dev` 抢同一目录**，
> 会导致 dev 服务页面**不水合**（输入框能打字但 React 状态不更新、按钮一直置灰）。
> 症状极具误导性 —— 看起来像表单 bug，其实是构建产物被覆盖。
> 跑完 build 记得重启 `next dev`。（本次验收就踩了这个，见坑文档第 17 条的同类现象。）
