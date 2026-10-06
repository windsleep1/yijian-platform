# 32 · 个人 PWA 方案（**B：C 端裁剪，不是从零搭脚手架**）

> 用户口径（2026-10-06）：方向 **B → C**；B 的定位是「**C 端裁剪**」——
> UI / 页面结构 / 状态管理 / 路由**直接搬**，**唯一要重写的是数据层**（换成 IndexedDB）。
> **本文零实现。** 所有"事实"都是实读出来的，出处附在每条后面。

---

## 0. 一句话

在 `apps/pwa/` **新起一个独立应用**，把 `apps/web` 的界面**复制**过来，
把 `lib/api.ts` 那一层换成 **IndexedDB**，**零后端、零登录、零网络**；
题库由用户在**首次启动时导入**（一个文件 + 对账）。

---

## 1. 侦察结果（实测）

| 项 | 实测 | 出处 |
|---|---|---|
| C 端框架 | **Next.js 14 App Router**；依赖**只有** `next` / `react` / `react-dom` | `apps/web/package.json` |
| 状态管理 | **手写 React**（`useState` / `useCallback` / `useEffect`），**无** zustand / redux | 全仓 grep |
| 数据层 | 只有 4 个文件：`lib/api.ts`（`request()` 走 fetch）、`lib/auth-store.ts`、`lib/json-bigint.ts`、`lib/types.ts` | `apps/web/src/lib/` |
| 页面 | **12 个 `page.tsx`** | `find apps/web/src/app -name page.tsx` |
| ★ PWA 基建**已经做完** | `public/sw.js`（手写）、`public/offline.html`、`public/icons/`、`src/app/manifest.ts`、`src/app/sw-register.tsx` | `docs/28` |
| 题库 | `data/seed/questions.json` = **扁平数组 6000 题 / 13.57 MB**；单题含 `answer`（**已归一** `{'value':['D']}`）、`options` 内联、subject/chapter/kp/type/difficulty | 实读 |
| 题库元数据 | `data/seed/import_manifest.json`：`totals{6000 题 / 20156 选项 / 52 知识点}`、`by_subject`(6 科)、`by_type`(5 类)、`by_difficulty`、**`fingerprint`** | 实读 |
| ★★ **`data/` 不在 git 里** | `git ls-files data/` → 空（被 `.gitignore` 排除） | 实读 |
| 判分语义 | `apps/api/app/schemas/answer.py`：`judge_bool` / `canonical_doc` / `normalize_tokens` / `check_doc`；`practice_service`：`GRADABLE_TYPES` / `PARTIAL_CREDIT_RATIO` | 实读 |
| 判断题**有两套答案写法** | `{"value":[true]}`（管理端建）vs `["A"]/["B"]`（种子生成器）—— 由 `_judge_bool()` 在判分与出参处**归一** | **BL-20** |

### 1.1 ★★ 一条必须先纠正的假设

> 用户设想：「**从 GitHub 拉 `questions.json`**」。

**走不通**：`data/` **不在 git 里**（`docs/27` §1.5 记过同族事故 —— Render 容器里没有 `data/`，
所以 `seed-questions` 在容器里**永远 skip**）。
⇒ 题库进 PWA 只有两条路：**用户手动上传**（本机 `data/seed/questions.json`），
或**先把它托管到某个可下载的地方**（新增一条发布链）。
本文按**前一条**设计（零新增基础设施），并把"托管下载"记成可选增强。

---

## 2. 用户确认的 6 件事（照单执行）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 目录结构 | **`apps/pwa` 独立**（不条件编译 —— 会让两边都复杂；且 `apps/web` 有自己的 SW/middleware 契约） |
| 2 | UI 复用 | **复制**（不抽包 —— 抽包要动 C 端，风险大、收益低） |
| 3 | 题库来源 | **首次启动导入**（选文件；见 §4） |
| 4 | 持久化 | **只用 IndexedDB**（不上 OPFS） |
| 5 | 砍掉 | **登录 / 注册 / 引导 / 我的-账号**；保留 **答题 / 错题本 / 收藏 / 标记 / 笔记 / 报告** |
| 6 | 数据备份 | **导出 / 导入 JSON，放「我的 → 设置」** —— ★ 硬需求（IndexedDB 会被清缓存） |

### 2.1 ★★ 复制带来的**新风险**（必须有对策）

复制之后，**C 端与 PWA 的同一份 UI 变成两份**：在 C 端修了 bug、PWA 侧**没修**，
而且**不报错**（正是「同一个事实两种写法」那一族）。

⇒ 对策（**机械的，不靠记得**）：
1. `apps/pwa/COPIED-FROM-WEB.md` 列出**每一个复制品的来源路径**（新文件必须登记）；
2. 加一条**门禁**：清单里的每个来源文件与目标文件**要么逐字节相同、要么在清单里注明"有意分叉 + 理由"**
   —— 逐字节相同做不到（数据层必然分叉），所以判据取"**登记 + 有意分叉要写理由**"，
   并且**清单必须覆盖所有复制品**（新复制一个文件不登记 ⇒ 红）。

> ★ 这条不是"洁癖"：`apps/pwa` 与 `apps/web` 会**长期共存**，而"下游复制品悄悄落后"
> 是本项目已经踩过两遍的形态（`practice_items.marked` 的语义漂、批次 2 的 B16 判据只覆盖一半）。

---

## 3. 数据层迁移策略（**本方案的核心**）

### 3.1 一句话：**判分语义只留一处，PWA 是"哑比较"**

从 API 换到 IndexedDB，最大风险不是"存哪里"，而是 **判分/统计的语义出现第二份实现**。
`answer.py` 里那些函数（尤其 `judge_bool` 要处理**两套判断题写法**，见 BL-20）
**不能抄一份到 TS** —— 抄了就是两个可变物，迟早分叉，而症状是"某些判断题在 PWA 里永远判错"。

⇒ 设计：

| 层 | 归属 | 内容 |
|---|---|---|
| **语义**（唯一真相） | **Python 侧** | `canonical_doc` / `judge_bool` / `PARTIAL_CREDIT_RATIO` / `GRADABLE_TYPES` |
| **固化** | 导出时**跑一次** | 把每题 `answer` 归一到唯一写法；把评分规则写进包的 `meta.grading_rules` |
| **执行**（哑） | PWA 侧 | 只做「**集合相等**」+ 读 `meta.grading_rules`（不自带任何阈值/特例） |

⇒ PWA 侧**没有独立语义**，因为它没有可"决定"的东西：判断题写法已在导出时归一，
部分分阈值来自包。（这条判据值得写进代码注释：**PWA 的判分函数不许出现字面量规则**。）

### 3.2 导出包（新增一个脚本，不新增基础设施）

`tools/pwa/export-bank.py`（在 Python 侧跑一次）：

```jsonc
{
  "meta": {
    "schema_version": 1,            // PWA 据此拒绝不认识的包
    "bank_version": "<fingerprint>",// 来自 import_manifest.json
    "exported_at": "...",
    "totals": {"questions": 6000, "options": 20156, "knowledge_points": 52},
    "by_subject": {...}, "by_type": {...}, "by_difficulty": {...},
    // ★ 下面两个值**实测**自 `practice_service.py`（`:109` / `:48`）——
    //   导出时写进包是**把唯一真相随包下发**，不是在这里再定义一遍。
    "grading_rules": {"partial_credit_ratio": 0.5, "gradable_types": ["single","multiple","judge"]}
  },
  "questions": [ /* 归一后的题；去掉 PWA 用不到的字段（material_html/parent_id/root_id/…） */ ]
}
```

★ 用户**只需要上传这一个文件**（不用同时管 `questions.json` 与 `import_manifest.json`）。
★ 代价：多一步"跑脚本"。**收益**：评分规则与答案写法**只有一处定义**。
★ 可选增强（本文不做，记 BL）：把包托管到 `apps/web/public/` 之类，让 PWA 直接下载 —— 
   但那是"给公开仓库塞 13 MB 二进制"，要单独评估。

### 3.3 IndexedDB 形状（表 → object store）

| 原表 | store | keyPath | 需要的 index |
|---|---|---|---|
| `questions`（+ `question_options` 内联） | `questions` | `id` | `subject_id` / `chapter_id` / `type` |
| `subjects` / `chapters` / `knowledge_points` | 同名 | `id` | `subject_id`（chapters） |
| `wrong_questions` | `wrong` | `question_id` | `subject_id` + `last_wrong_at` |
| `favorites` / `question_marks` | `favorites` / `marks` | `question_id` | `created_at` |
| `notes` | `notes` | 自增 `id` | `question_id` / `subject_id` / `created_at` |
| `practice_sessions` | `sessions` | `id` | `started_at` |
| `practice_items` | `items` | `[session_id, seq]` | `session_id` |
| （新） | `meta` | `key` | —— |

★ 三条**红线**（都是项目已有约定，别在 PWA 里丢掉）：
1. **日界一律 `Asia/Shanghai`** —— "今天答了几题 / 连续天数"必须按这个时区算，
   否则跨零点或用户改时区就会错（**不给解释**）。
2. **零分母返 `null`**，不是 `0`（正确率那类除法）。
3. **软删要过滤** —— `notes.is_deleted`、`wrong.is_removed` 在**每条读路径**上都要带；
   漏一处 ⇒ "删了还在"，**不报错**。

★ **ID**：PWA 单机 ⇒ 自增 / `crypto.randomUUID()` 即可；
但**题目 id 沿用包里的字符串**（保持与 C 端一致，将来"导出到 C 端"才可能）。

### 3.4 报告：**明确是"简化版"**，并写下差异

报告（正确率 / 按知识点 / 按科目）在 C 端是**后端算的**。PWA 侧必须自己算 ⇒ 又一处潜在分叉。
⇒ 本方案的选择：**报告只做能一一对应的那几项**（答题数 / 正确数 / 正确率 / 按科目分布），
并在页脚写一行「口径与 App 版一致；不含 X/Y」—— **不装作一模一样**。
★ 若将来要对齐，做法同 §3.1：把口径下沉进 `meta`，而不是各写一份。

---

## 4. 首次启动引导（用户怎么把题库放进去）

```
/setup  →  ① 选文件（<input type="file" accept=".json,.gz">）
        →  ② 读 → 校验 meta.schema_version
        →  ③ **分批写** IndexedDB（每 500 题一个事务）+ 进度条 + 可中断续传
        →  ④ **对账**（见下）→  ⑤ 写 meta.bank_version → 进主界面
```

**对账（必做，且要能证伪）**：用包里的 `meta` 逐项比 ——
`题目数 == 6000`、`选项数 == 20156`、`by_subject` / `by_type` 分布**逐项相等**。
不一致 ⇒ **不进主界面**，并显示"哪一项差多少"（照做的提示，不是"导入失败"四个字）。
★ 为什么必须对账：**半份题库**的症状是"某些科目题很少 / 某些题搜不到"，
**不会报错** —— 而对账能把它变成一条明确的红。

**三条实现约束**：
1. **导入必须可重入**：中途关页面 ⇒ 下次进来要么"继续"要么"重头"，**不许留下半份数据**
   （判据：写数据前先写 `meta.importing=true`，全部完成后才清；启动时见到 `importing=true`
   就**先清空那批再重来**）。
2. **大文件不许卡死 UI**：13.6 MB 一次性 `JSON.parse` 会冻结几秒 ⇒ 用 Worker 或分片解析。
3. **空态要能进**：没题库也能打开 / 安装（`/setup` 是首页），否则"装不上、也就没有 PWA"。

**用户侧的取包步骤**（写进 `docs/`，与 `docs/27` 同风格）：
```
python tools/pwa/export-bank.py            # → data/seed/pwa-bank.json
# 把 pwa-bank.json 传到手机（AirDrop / 微信文件传输 / U 盘皆可）
# PWA 首次打开 → 「导入题库」 → 选这个文件
```
★ **"传到手机"这一步无法自动化** —— 如实写进步骤，不要假装有一个"一键"。

---

## 5. BL-21 的处理（用户口径：**已闭合，不是触发**）

- BL-21 原文的主触发是「开始做个人 PWA 时」——那时**标记 / 收藏 / 笔记**还没实现。
- **但批次 2/3 已经在 C 端把这三件做完了** ⇒ 它们**不需要在 PWA 里重做**，
  PWA 只是把 `apps/web` 的对应页面**复制**过来 + 换数据层。
- ⇒ 状态从「⚪ 未到」改为「✅ **已闭合（通过复用 C 端实现）**」，
  并在行内写清：**它不是被 PWA 触发的，是被 C 端批次做掉的**。

---

## 6. 砍掉什么（逐项说清代价）

| 砍掉 | 代价（用户可感的） |
|---|---|
| **登录 / 注册** | 单机单用户；换手机 = 数据要自己搬（靠 §7 的导出/导入） |
| **引导（onboarding）** | 报考科目 / 考试年份那些个性化设置没了 ⇒ 有它才能做的"倒计时 / 个性化推荐"也没有 |
| **我的-账号相关**（改密码 / 退出 / 消息中心） | 只剩"设置（导出/导入）+ 关于" |
| **多设备同步** | 明确不做 —— 这正是"零成本"的代价，写在首页的"关于"里 |

★ 砍掉登录后，**`user_id` 整个消失** ⇒ 数据层少一层隔离（好事），
但**`lib/auth-store.ts` 与 `middleware.ts` 的 Tab 守卫要一起删** —— 不然会留下"永远不触发的守卫"
（那种东西在真机上表现为**白屏/跳错页**，而本地因为默认已登录看不出来）。

---

## 7. 数据备份（**硬需求**）

- 位置：**我的 → 设置 →「导出全部数据」/「导入数据」**。
- 导出内容：`meta`（版本 / 题库指纹）+ `sessions` / `items` / `wrong` / `favorites` /
  `marks` / `notes` **全量**。文件名带时间戳。
- ★ **判据（可证伪）**：**导出 → 清空 IndexedDB → 导入 → 逐表对账行数一致**，
  且抽 5 条比对关键字段（`wrong.wrong_count` / `retry_correct`、`notes.updated_at`）。
  **只验"导出按钮没报错"不算**（那与"导出了一个空文件"长得一样）。
- 导入要**幂等**：同一个文件导两次 ⇒ 结果一致（按 `id` 覆盖，不是追加）。
- ★ **平台事实（写在界面上提醒用户）**：**iOS Safari 对"没用过 7 天"的站点会清掉脚本可写存储**
  ⇒ ① 引导用户**添加到主屏幕**（安装态不受这条影响）；② 备份不是可选项。
  （这条属于本项目的「**错误信念**」家族：不写出来，用户会在丢数据那天才知道。）

---

## 8. 验收判据（每条都要"能红"）

| # | 判据 | 拦的是什么 |
|---|---|---|
| 1 | ★★ **判分对拍**：抽 N 题 × 每题的「全对 / 全错 / 半对」三种提交，**API 与 PWA 两侧逐条结论与得分相同** | "两套判分实现悄悄分叉"——这是本方案最大的风险 |
| 2 | ★★ **导入对账**：题量 / 选项数 / `by_subject` / `by_type` **逐项等于** `meta` | "半份题库"（不报错） |
| 3 | ★ **导入可重入**：导入中途杀掉页面 ⇒ 再进来不会出现"半份 + 半份" | 脏写 |
| 4 | ★★ **数据往返**：导出 → 清空 → 导入 → 逐表行数 + 抽 5 条字段一致 | 备份是假的 |
| 5 | ★ **日界**：把系统时区改成 `UTC` ⇒ "今天答了几题"仍按 **Asia/Shanghai** 算 | 时区红线被丢掉 |
| 6 | ★ **离线真可用**：安装后**断网**（DevTools Offline）→ 答题 / 判分 / 进错题本 / 写笔记全通 | SW 只缓存了壳、没缓存逻辑 |
| 7 | ★ **软删过滤**：删一条笔记 ⇒ 5 个读路径（本题面板 / 笔记页 / 报告 / 徽标 / 导出）**都不出现** | 漏一处不报错 |
| 8 | ★ **复制品清单覆盖**：`apps/pwa` 里每个复制品都在 `COPIED-FROM-WEB.md` 里登记 | "下游悄悄落后" |

---

## 9. 本批**不做**（写清理由）

| 不做 | 理由 |
|---|---|
| **多设备同步 / 云备份** | 与"零后端"直接冲突；要做得先回答"谁是真相来源" |
| **题库托管下载** | 要给公开仓库塞 13 MB 二进制；**先用手动上传跑通**，再评估 |
| **报告与 App 版逐位对齐** | 先把"简化版 + 写明差异"做完；对齐同 §3.1 的做法（下沉口径），单独一批 |
| **C：E2E helper 抽取** | 用户已定：**B 做完再做 C**；且 C 服务的是 `apps/admin` + `apps/web`，与 B 无关 |
| 把 PWA 的 UI 组件抽成"共享 helper" | = 抽包，风险大收益低（用户已明确否掉） |

---

## 10. 预计产出（确认后执行）

- **新应用** `apps/pwa/`：12 个页面**复制**过来、删掉登录/注册/引导/账号；`lib/api.ts` → `lib/db.ts`
- **新脚本** `tools/pwa/export-bank.py`（产出 `pwa-bank.json`）
- **新文档** `apps/pwa/COPIED-FROM-WEB.md`（复制品清单，门禁校验）
- **新门禁**：复制品清单覆盖检查（进 `check-invariants.py`，与不变量 11 同族）
- **验证**：§8 的 8 条判据 + 现有 43 项不变量（PWA 只增不改 C 端行为）
