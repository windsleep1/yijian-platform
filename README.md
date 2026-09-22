# 一建通 · 一级建造师学习备考平台

[![CI](https://github.com/windsleep1/yijian-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/windsleep1/yijian-platform/actions/workflows/ci.yml)

> 面向一级建造师考生的 **学习 / 刷题 / 模拟考试 / 进度管理** 一体化平台。
> 移动端优先（H5 + PWA），PC Web 作为管理端与深度阅读端。

---

## 快速开始：5 分钟本地跑起来

> 前置：Docker Desktop（含 Compose v2）+ Node 18+。**无需任何真实密钥** —— 本地直接用模板里的默认值即可跑通。

```bash
# ① 起后端：postgres + redis + api（约 2 分钟）
cd yijian-platform/deploy
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/api/v1/health          # data.status=ok，postgres/redis 均 up
# Swagger 文档：http://localhost:8000/docs

# ② 起管理后台：Next.js 14（约 2 分钟）
cd ../apps/admin
cp .env.local.example .env.local
npm install
npm run dev                                       # 打开 http://localhost:3000
```

登录用超管账号 **`13800000000` / `Admin@123456`**（api 容器首次启动时自动创建），
登录后默认落在**题库列表**页。更完整的验收路径见下方「三、3 分钟跑通后端」与「四、建库 + 导入种子题库」。

---

## 一、项目当前状态

| 批次 | 内容 | 状态 |
|---|---|---|
| **Batch 1** | 产品方案、信息架构、数据库设计（含可执行 DDL）、API 清单、技术选型、部署方案、题库合规规范 | ✅ 已交付 |
| **Batch 2** | 后端认证 + RBAC + 分层骨架（FastAPI + PostgreSQL + Redis），`docker compose up` 一键起 | ✅ 已交付 |
| **Batch 3** | 管理后台 v0.1：Next.js 14 前端骨架 + 用户管理 / 审计日志（`apps/admin`） | ✅ 已交付 |
| **Batch 4** | **题库 CRUD**：后端 7 个接口 + 前端 3 个页面（列表 / 新建 / 详情编辑）+ 版本历史 | ✅ 已交付 |
| **Batch 5** | **题库批量导入管道**：后端 7 个接口（上传 / 校验 / 执行 / 发布 / 回滚）+ 雪花 ID 精度修复 | ✅ 已交付 |
| **Batch 6** | **导入向导**：前端 4 页（批次列表 / 三步向导 / 批次详情 / 错误报告 CSV）+ 批次变更日志接口 | ✅ 已交付 |
| **Batch 7** | **组卷引擎**：组卷规则 CRUD + 自动组卷 / 卷面校验 / 发布（版本锁定）+ 试卷管理前端（列表 / 详情 / 新建 / 规则管理 / 实时试算 / 下线） | ✅ 已交付 |
| **Batch 7+** | **状态机补齐**：试卷下线 + 账号停用/启用 + 删 `exams.archived` + **数据范围收口**（题目八条入口） | ✅ 已交付 |

> **交付序号与最初规划不同。** 原「Batch 5」把导入流水线、组卷引擎、记忆曲线、小程序端、
> 支付、数据看板、压测打包在一批里；实际按依赖顺序拆成了
> **Batch 5（导入管道）→ Batch 6（导入向导）→ Batch 7（组卷引擎）**。
> 记忆曲线、小程序端、支付、数据看板仍未启动。
>
> 当前后端共 **50 个接口**，pytest 全量 **126 passed, 1 skipped**。

**每一批都可以独立执行、独立验收。** Batch 2 起，后端本身就是可运行服务：
`cd deploy && cp .env.example .env && docker compose up -d --build` → `http://localhost:8000/docs` 即可点开 Swagger 联调。

- Batch 3 验收：`docs/09-Batch3-管理后台v0.1-方案与骨架.md`
- Batch 4 验收：`docs/10-Batch4-题库CRUD-方案与验收.md`（含 13 张端到端截图与实测缺陷记录）
- Batch 5 验收：`docs/11-Batch5-导入管道-方案与验收.md`
- Batch 6 验收：`docs/12-Batch6-导入向导-方案与验收.md`（含 18 张截图与实测缺陷记录）
- Batch 7 验收：`docs/13-Batch7-组卷引擎-方案与验收.md`（组卷规则 / 试卷 CRUD / 实时试算）

Batch 7 之后转为**整改批**（不带新功能，专治已有账）：

- 「有状态没入口」审计：`docs/14-状态机审计.md`（29 张表逐个审，P0/P1/P2 分级 + 可复用 5 步方法）
- 审计链立项：`docs/15-审计链补齐.md`（审核要记「人 + 时间 + 理由」）
- 数据范围收口：`docs/16-数据范围收口.md`（题目八条入口统一走唯一判据）
- 门禁整顿：`docs/17-门禁整顿.md`（假门禁 / ruff / CI / 覆盖率 / 全仓格式化）

### 界面速览（管理后台 · Batch 3 – 6）

| 题库列表（搜索 / 筛选 / 分页） | 新建题目（实时校验，未通过不给提交） |
|:---:|:---:|
| ![题库列表](apps/admin/docs/screenshots/batch4/01-questions-list.png) | ![新建题目](apps/admin/docs/screenshots/batch4/09-question-new-form.png) |
| **科目 → 章节联动筛选** | **批量软删除（二次确认）** |
| ![科目章节联动](apps/admin/docs/screenshots/batch4/02-filter-subject-chapter.png) | ![批量删除确认](apps/admin/docs/screenshots/batch4/07-batch-delete-confirm.png) |
| **题目版本历史（只读快照）** | **导入向导：步骤条 + 具体数字** |
| ![版本历史抽屉](apps/admin/docs/screenshots/batch4/13-version-history-drawer.png) | ![导入预览](apps/admin/docs/screenshots/batch6/04-preview.png) |
| **回滚二次确认（复述"软删除 N 道" + 手输批次号）** | **更多截图** |
| ![回滚确认](apps/admin/docs/screenshots/batch6/17-rollback-confirm.png) | Batch 4 共 13 张 / Batch 6 共 18 张，见 `apps/admin/docs/screenshots/` |

---

## 二、目录结构

```
yijian-platform/
├─ README.md                          # 本文件：总览 + 快速开始
├─ LICENSE                            # MIT 许可
├─ .gitignore
├─ docs/
│   ├─ 01-产品方案.md                  # 定位、角色、功能矩阵、MVP 边界、商业化
│   ├─ 02-信息架构与页面清单.md         # IA 树、页面清单、移动端导航、路由映射
│   ├─ 03-数据库设计.md                # 设计原则、ER 概览、表清单、关键表详解
│   ├─ 04-API接口清单.md               # 统一规范 + 全量 REST 端点
│   ├─ 05-技术选型与系统架构.md         # 技术栈决策表、分层架构、非功能设计
│   ├─ 06-部署运维与安全方案.md         # Docker Compose、Nginx、备份、监控、安全
│   ├─ 07-题库合规与导入规范.md         # 合规来源矩阵、导入/审核/版本/回滚流程
│   ├─ 08-Batch2-验收报告.md           # Batch 2 交付范围、实测结果、验收命令
│   ├─ 09-Batch3-管理后台v0.1-方案与骨架.md
│   ├─ 10-Batch4-题库CRUD-方案与验收.md # Batch 4 方案、6 个 B 端特征、验收证据与实测缺陷
│   ├─ 11-Batch5-导入管道-方案与验收.md # Batch 5 导入管道、data scope、雪花 ID 修复
│   ├─ 12-Batch6-导入向导-方案与验收.md # Batch 6 导入向导、七项交互、18 张截图
│   ├─ 13-Batch7-组卷引擎-方案与验收.md # Batch 7 组卷引擎（接口 / 算法 / 版本锁定 / 实时试算 / 实测缺陷）
│   ├─ 14-状态机审计.md                # 29 张 status 表逐个审计（P0/P1/P2）+ 可复用的 5 步方法
│   ├─ 15-审计链补齐.md                # 立项：审核要记「人 + 时间 + 理由」（与 C 端/支付批一起排）
│   ├─ 16-数据范围收口.md              # 题目八条入口统一判据 + 变异验证证据
│   ├─ 17-门禁整顿.md                  # 假门禁 / 覆盖率门槛 / 全仓格式化（本批验收）
│   └─ samples/                        # 验收用样例文件（错误报告 / 走查 CSV）
├─ apps/
│   ├─ api/                           # FastAPI 后端（Batch 2 起，逐批扩充）
│   │   ├─ Dockerfile                 # python:3.12-slim，非 root 运行
│   │   ├─ docker-entrypoint.sh       # 等依赖 → 建表 → 建超管 → 启动
│   │   ├─ requirements.txt
│   │   ├─ tests/                     # pytest（126 passed, 1 skipped）：smoke + v3 + v4 + v5 + v7 + idgen
│   │   └─ app/
│   │       ├─ main.py                # 应用入口（中间件 / 异常处理 / 路由挂载）
│   │       ├─ cli.py                 # wait-db / wait-redis / init-db / seed-admin / seed-questions
│   │       ├─ core/                  # 配置、安全、依赖、异常、统一响应、雪花 ID、数据范围
│   │       ├─ db/                    # 引擎与会话、Redis 单例、ORM 模型（仅认证/RBAC/审计 8 表）
│   │       ├─ schemas/               # 入参/出参模型（BigIntStr 统一 ID 序列化）
│   │       ├─ services/              # 业务逻辑（auth / rbac / sms / user / audit / question / import / exam）
│   │       └─ api/v1/                # health / auth / admin_users / admin_rbac / admin_audit
│   │                                 #   / admin_chapters / admin_questions / admin_imports
│   │                                 #   / admin_exams（共 50 个接口）
│   └─ admin/                         # 管理后台（Next.js 14，Batch 3–7）
│       ├─ .eslintrc.cjs              # ESLint 配置：@typescript-eslint 基础集 + react-hooks
│       │                             #   （规则暂全为 warn，第二步提为 error）
│       ├─ src/app/(console)/         # questions（列表/new/[id]）/ imports（列表/new/[id]）
│       │                             #   / users / audit-logs
│       ├─ src/components/            # QuestionForm、QuestionVersionDrawer、DataTable、
│       │                             #   StepWizard、ErrorReportTable、RollbackDialog、MarkdownPreview…
│       ├─ src/hooks/                 # useQuestions、useImports、useTableState、useAuth
│       ├─ src/lib/                   # api 客户端、types、permission（MODULE_ENTRIES）、
│       │                             #   question / import 领域逻辑
│       └─ docs/
│           ├─ B端联调坑.md            # 56 条实战坑（现象 → 根因 → 解法 → 落点）
│           ├─ screenshots/batch4/     # Batch 4 端到端截图 13 张
│           ├─ screenshots/batch6/     # Batch 6 端到端截图 18 张
│           ├─ screenshots/batch7-pass2a/        # /exams 列表 + 详情编辑 + 归档恢复（14 张）
│           ├─ screenshots/batch7-pass2b/        # /paper-rules + /exams/new 实时试算（13 张）
│           └─ screenshots/batch7-state-machine/ # 试卷下线 + 账号停用（4 张）
├─ .github/workflows/ci.yml           # CI 门禁：tsc --noEmit + pytest + ruff check
├─ ruff.toml                          # ruff 配置（只开默认集 E4/E7/E9/F，不含风格规则）
├─ tools/local-verify/                # 本地联调脚本：起服务 / 冒烟 / 回归探针 / 导入转换器
├─ db/
│   ├─ schema.sql                     # 可直接执行的 PostgreSQL 建表脚本（64 表 + 3 视图 + 102 索引 + 基础数据）
│   └─ seed/
│       ├─ gen_seed_questions.py      # 原创题库生成器（合规、可复现）
│       └─ validate_seed.py           # 种子题库质检工具
├─ deploy/
│   ├─ docker-compose.yml             # 默认起 postgres + redis + api；meili/minio 走 extras profile
│   └─ .env.example                   # 环境变量模板
└─ data/seed/                         # 生成产物（.gitignore 已忽略）
    ├─ questions.json                 # 6000 题，字段完整
    ├─ questions.sql                  # 26,208 条 INSERT，13.1 MB，幂等
    ├─ questions.csv                  # Excel 友好
    └─ import_manifest.json           # 批次清单（分布 + 校验和）
```

---

## 三、3 分钟跑通后端（Batch 2 主验收路径）

> 前置：本机已装 Docker Desktop / Docker Engine + Compose v2。

```bash
cd yijian-platform/deploy

# 1) 准备环境变量（必须改掉几个 CHANGE_ME 密钥）
cp .env.example .env

# 2) 一键起 postgres + redis + api
docker compose up -d --build

# 3) 看启动日志：应依次出现 等待DB → 等待Redis → 建表 → 超管就绪 → Uvicorn running
docker compose logs -f api

# 4) 健康检查（postgres/redis 都应为 up）
curl http://localhost:8000/api/v1/health
```

打开 **http://localhost:8000/docs** 就是可点的 Swagger。

### 冒烟测试

```bash
cd ../apps/api
pip install -r requirements.txt
ADMIN_INIT_PHONE=13800000000 ADMIN_INIT_PASSWORD=Admin@123456 pytest tests -v
# 全量 126 passed, 1 skipped（1 skipped 是 6000 行导入用例，需环境变量显式开启）
```

`test_smoke.py` 覆盖认证主链路：注册 → `/me` → 密码登录 → 刷新令牌轮换（旧 token 立即失效）→ 登出 →
学员访问管理端 403 → 超管改角色 → **权限缓存立即失效** → 短信限流。
`test_admin_v3/v4/v5.py` + `test_idgen.py` 分别覆盖管理后台、题库 CRUD、导入管道与雪花 ID 精度。

### 手动点两个接口看看

```bash
# 发验证码（SMS_PROVIDER=mock，非生产环境会在响应里回显 dev_code，方便离线联调）
curl -s -X POST http://localhost:8000/api/v1/auth/sms/send \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001","scene":"register"}'

# 用上一步拿到的 dev_code 注册，直接返回令牌对 + 用户信息
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000001","code":"<dev_code>","password":"Passw0rd123"}'
```

> 这段演示的是 **Batch 2 当时的 10 个接口**（`/health` + `/auth` 7 个 + `/admin/users` 2 个）——
> 认证与 RBAC 的最小完整闭环，详见 `docs/08-Batch2-验收报告.md`。
> 后续批次在此基础上加了题库 CRUD（Batch 4）、章节树、导入管道（Batch 5）、变更日志（Batch 6）、
> 组卷引擎（Batch 7），**当前共 50 个接口**。

### 手动新建一道题（Batch 4 题库接口）

想快速验证题库链路（或者只是想要一道自己的测试题），按下面这条走一遍即可。
以下用**本地联调端口 8123**；走 `docker compose` 的话换成 `8000`。

```bash
BASE=http://localhost:8123/api/v1

# 1) 拿超管 token（超管由 python -m app.cli seed-admin 创建）
TOKEN=$(curl -s -X POST $BASE/auth/login/password \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","password":"Admin@123456"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["data"]["access_token"])')

# 2) 看一眼科目树，挑一个 subject_id（本地种子数据是 1001 这类小数字）
curl -s "$BASE/admin/chapters/tree" -H "Authorization: Bearer $TOKEN" | head -c 400

# 3) 新建一道单选题
#    ⚠️ 两个容易踩的点：
#      · subject_id 传**字符串**（ID 一律不要 Number()，见坑文档 21）
#      · 答案由 options[].is_correct 推导，不要另外传 answer 字段
curl -s -X POST $BASE/admin/questions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "subject_id": "1001",
    "type": "single",
    "stem": "【手测】关于施工现场安全管理，下列说法正确的是？",
    "difficulty": 3,
    "status": "draft",
    "source_type": "self",
    "options": [
      {"label": "A", "content": "先交底后作业，并留存书面签字记录", "is_correct": true},
      {"label": "B", "content": "班组口头交代即可，无需留痕",       "is_correct": false},
      {"label": "C", "content": "由班组长自行决定是否交底",         "is_correct": false},
      {"label": "D", "content": "交底记录可在竣工后补签",           "is_correct": false}
    ],
    "analysis": "安全技术交底必须书面留痕并经双方签字。"
  }'
```

返回的 `data.id` 就是新题 ID，回读详情：

```bash
curl -s "$BASE/admin/questions/<新题ID>" -H "Authorization: Bearer $TOKEN"
```

**更省事的办法**：直接开后台点出来 —— `http://localhost:3000/questions/new`
（登录 `13800000000 / Admin@123456`）。表单会实时告诉你还差什么：
底部提示"✓ 校验通过，可以保存"才能提交；填不完整会明确写出是哪一项不合格。

> **想验证批量删除？** 在本页搜索框输入关键词定位到自己造的题 → 勾选 → 顶部出现
> 「批量删除」→ 二次确认。删除是**软删除**，勾上「显示已归档」还能查到。
> e2e 跑完想恢复数据集：`python tools/local-verify/restore-e2e-softdeleted.py --restore`。

---

## 四、建库 + 导入种子题库（Batch 1 产物）

### 1. 生成种子题库（无需任何依赖，纯标准库）

```bash
cd yijian-platform

# 默认生成 6000 道原创题（3 门公共课 + 建筑/市政/机电 3 个专业）
python db/seed/gen_seed_questions.py --count 6000 --out data/seed

# 只生成经济与法规
python db/seed/gen_seed_questions.py --count 2000 --subjects JGJJ,FAGUI

# 质量检查（校验答案一致性、去重有效性、合规字段）
python db/seed/validate_seed.py data/seed/questions.json
```

### 2. 导数据

```bash
cd deploy

# 建表：postgres 数据卷为空时会自动执行 schema.sql；
#        卷已存在则手动执行下面这条（幂等）
docker compose exec -T postgres psql -U yijian -d yijian < ../db/schema.sql

# 导入题库（也可以用 API 容器的 CLI：docker compose exec api python -m app.cli seed-questions）
docker compose exec -T postgres psql -U yijian -d yijian < ../data/seed/questions.sql
```

### 3. 验证

```bash
docker compose exec -T postgres psql -U yijian -d yijian -c "
  SELECT s.short_name, q.type, count(*)
  FROM questions q JOIN subjects s ON s.id = q.subject_id
  WHERE q.source_type = 'self'
  GROUP BY 1, 2 ORDER BY 1, 2;"
```

### 4. 已实测的产物指标

| 指标 | 实测值 |
|---|---|
| 题目总数 | **6000** |
| 选项总数 | 20,156 |
| 知识点数 | 52 |
| 生成耗时 | 约 0.3 秒 |
| 题型分布 | 单选 3049 / 多选 1501 / 判断 978 / 案例 120 / 案例小题 352 |
| 难度分布 | 1⭐ 480 / 2⭐ 2058 / 3⭐ 2231 / 4⭐ 1067 / 5⭐ 164 |
| 质检结果 | 全部 11 项检查通过，0 错误 0 警告 |
| schema.sql | 64 表 + 3 视图 + 102 索引，1508 行 |

---

## 五、核心设计决策（一句话版）

| 决策点 | 结论 | 理由 |
|---|---|---|
| 前后端分离 | Next.js + FastAPI | 前端做 SEO/PWA，后端做重计算与题库管道 |
| 移动端 | Next.js 响应式 + PWA，**不做原生 APP（一期）** | 一建考生多为在职工程人，扫码即用转化率远高于装 APP |
| 数据库 | PostgreSQL 单库起步，按 `subject_id` 逻辑分片 | 一建题量 1~10 万级，PG 完全够用，避免过早引入分库 |
| 题库检索 | Meilisearch（一期）→ Elasticsearch（可选） | 中文分词开箱即用，资源占用低，单机 200MB 起步 |
| 缓存 | Redis（Session / 限流 / 排行榜 / 热点题目 / 短信验证码） | 一栈多用 |
| 主键 | 雪花 ID（BIGINT）而非自增 | 便于将来分库与数据迁移 |
| 软删除 | `is_deleted` 标记 + 归档任务 | 题库是资产，误删不可接受 |
| 题目版本 | 一题多版本快照 + 变更日志 | 已考过的卷不能因为改题而失真 |

---

## 六、合规声明

本项目**不内置任何受版权保护的真题原文**。

- `db/seed/gen_seed_questions.py` 产出的是**基于考纲知识点的原创仿真题**，用于技术验证与冷启动兜底。
- 生产题库必须通过 `docs/07-题库合规与导入规范.md` 定义的合规通道进入：
  **自有教研产出 / 已授权采购 / 公开可用内容 / 用户合法导入**。
- 任何网络采集行为必须遵守 `robots.txt`、目标站点服务条款与《著作权法》，禁止抓取受版权保护内容。

---

## 七、门禁（CI 与本地检查）

| 检查 | 命令 | 现在 | 说明 |
|---|---|---|---|
| 前端类型 | `npm run typecheck`（`tsc --noEmit`） | **拦截** | — |
| 前端 lint | `npm run lint`（eslint） | **拦截** | 2026-09-20 存量 14 条清零 → 提级为 error → 上 CI |
| 前端格式 | `npm run format:check`（prettier） | **拦截** | 配置 `apps/admin/prettier.config.mjs`，`printWidth 100` |
| 后端用例 | `pytest tests`（真 PostgreSQL） | **拦截** | 126 passed, 1 skipped |
| 后端静态检查 | `ruff check apps/api` | **拦截** | 2026-09-20 存量 19 条清零后转的 |
| 后端格式 | `ruff format --check apps/api` | **拦截** | 配置 `ruff.toml`，`line-length 100` |
| 后端覆盖率 | `coverage report` | **拦截**（门槛 65%） | 2026-09-20 起；依据见 `.coveragerc` |

CI 配置：`.github/workflows/ci.yml` —— push `main` 与 PR 触发，两个 job（后端 / 前端）。

### 格式化的两个决定（都不是"默认值"）

**① 行宽取 100，不是 prettier 默认的 80 / ruff 默认的 88。**
依据是**实测现有代码**，不是偏好：

```
apps/admin/src  17537 行 → p95=79  p99=100  超过 80 列 4.7%  超过 100 列 0.9%
apps/api        17623 行 → p95=83  p99=97   超过 88 列 2.9%  超过 100 列 0.5%
```

取 100 意味着格式化只需要碰 0.5%~0.9% 的行。**格式化的第一原则是"别让 diff 里混进样式噪音"** ——
这个 commit 的 diff 本来就有几千行，再叠上"我喜欢的风格"就彻底读不出真变化了。
两边同为 100，读代码时也不用在两种宽度之间切换。

**② Markdown 与 `docs/` 被 `.prettierignore` 排除。**
那些是**手写文档**：表格靠手工对齐、列表按语义断行、代码块里的输出是原样抄的。
格式化它们**不带来任何工程收益**（没有编译器/运行时读它），
只会产出几百行无信息量的 diff。prettier 只管**代码与配置**（ts/tsx/js/mjs/json/css）。

### 顺手：`.gitattributes`（`* text=auto eol=lf`）

本机 `core.autocrlf=true`，于是"工作区 CRLF、index LF"——格式化器和 diff 工具在这种
混合状态下容易把「改了一行」看成「整个文件都变了」。写死 `eol=lf` 让三处
（index / 工作区 / CI）统一。加之前核对过：**index 里 208 个文本文件本来就全是 LF**，
所以这个文件没有产生任何 renormalize 差异（`git add --renormalize .` 后 `git status` 无新增）。

### 覆盖率：三个进程各采一份再合并，门槛 85%

用例是**通过 HTTP** 打到独立 uvicorn 进程的（`tests/conftest.py` 里就是个普通
`httpx.Client`，base_url 来自 `AI_BASE`）。所以在 pytest 进程里跑 `pytest --cov=app`
只量得到测试自己导入的几个纯函数模块，数字低得没有意义。

⚠️ 但**由此推出"干脆整份丢掉"是错的**（2026-09-22 更正，硬约定 L）：正确做法是
**业务代码在哪个进程跑，就在哪个进程插桩，最后合并**。三份都要：

```
.coverage.api     API 进程      serve_fake_redis.py --coverage --cov-data-file
.coverage.cli     CLI 进程      seed-rbac / seed-admin（同一份，用 --append）
.coverage.tests   pytest 进程   单元测试里直接调用 app 代码的那部分
.coverage         combine 之上出的报告用文件
```

只采 API 一份时，`cli.py` 会**整文件报 0%**（它明明执行过）—— 那是测量边界造的假账。
`run-smoke.ps1` 与 CI 用**同一套参数、同一个脚本**，合并前逐份检查「存在且非空」，
缺一份就硬失败（**不静默合并**）；合并结果还会打 `sha256` 指纹，
两次 run 对指纹就是一次确定性的 dry run。

⚠️ **另一个必需的配置：`[run] concurrency = greenlet`**。本项目 DB 走 SQLAlchemy async，
每个 DB 调用都经 `greenlet_spawn()` 切一次 greenlet；coverage 默认 `concurrency = thread`
不感知切栈 → **await 之后同一帧的行全部丢失**，而且**还会把没执行的分支记成执行**
（两个方向都错）。实测加这一行前后：未覆盖 **1476 → 579**（差额 897 条是测量假象）。
排错顺序见坑 54。

`--shutdown-file` 同样是必需的：收尾若用 `Stop-Process -Force` / `kill`，进程直接消失、
`atexit` 不跑，**覆盖率数据一个字都写不出来**。改成"放哨兵文件 → 进程自己收尾写盘"。

**当前数字（2026-09-22）**：

```
               语句    未覆盖    覆盖率
本地           4299      462     89.25%
CI             4299      460     89.30%
services       2282      301     86.81%（本地口径）
门槛           fail_under = 85（**临时值**，见下）
```

⚠️ **两地残余差异只剩 2 行**（在 `cli.py`，**方向是 CI 覆盖更多**）：`_wait_db` 的重试
循环在 CI 里多跑了一轮（它的 PG 是 service container、稍慢）—— **时序差异，不是缺陷**。
（2026-09-22 之前那个 `86.7%` 是"只采 API 进程一份"的旧口径，已废。修完 `questions.json`
那一笔后 CI 从 **473 → 460**，少的 13 行正好是坑 55 里那条被静默 skip 的用例。）

⚠️ **`services` 的缺口不是"未来批次还没写的代码"**（这个描述一开始写错了）：
它是**已经写完、正在跑的代码**里没被测试打到的部分 —— 错误分支、边界处理、辅助方法。
**区别很重要**：它意味着**补测是现在就能做的事**，不需要等任何未来批次。
（历史数字 `49.6%` / `1153 条` 是**采集缺 `greenlet` 时的假账**，已作废。）

#### 门槛与抬升的**触发条件**（可判定，不靠"记得回来改"）

```
当前：fail_under = 85      （2026-09-22 裁定，临时值：贴 89.25% − 4.25）
下一档触发：实测总覆盖率 ≥ 90%
动作：把 fail_under 改成「实测值 − 1」，并重写本段
```

> 为什么必须写成"条件 + 动作"而不是"目标 85%"：后者依赖某个人的记忆，而
> **没有任何交付流程会因为"当时说过达到后要改"而停下来** —— 结局就是门槛
> 从"棘轮"退化成一个数字。写成可判定条件后，收尾动作里的
> 「跑一次 coverage report 看当前数字」就能直接对照执行
> （见 `apps/admin/docs/B端联调坑.md` 末尾「一批交付收尾的固定动作」§5）。

越过 90% 之后同样按这个套路：门槛提到「实测值 − 1」，并重新写一条"下一档 + 触发条件"。

### 两道跑的是**同一条链路**

| 步骤 | CI | 本地 `run-smoke.ps1` |
|---|---|---|
| 建表 + 迁移 | `psql -f db/schema.sql` + `db/migrations/*.sql` | 同 |
| 种子（RBAC + 超管） | `python -m app.cli seed-rbac / seed-admin` | 同 |
| **题库种子** | `python tools/local-verify/seed-questions.py` | **同一个脚本** |
| 起 API | `serve_fake_redis.py` | 同（含 `--coverage`） |
| 跑用例 | `pytest tests` | 同 |
| **覆盖率门禁** | `coverage report`（门槛读 `.coveragerc`） | **同**（同一份门槛） |

> 「题库种子」这一步**两边调的是同一个脚本**，覆盖率门槛**两边读同一个 `.coveragerc`**
> —— 不是各写一套。修坑 51 时最容易犯的错就是 CI 与本地各写一套，那两套迟早漂，
> 于是又回到"本地绿、CI 红"的口径分歧（正是坑 51 的形态）。

```bash
# 本地复刻 CI 的后端那一条（从零库也能全绿）
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1
# 只想跑用例、不要覆盖率门禁：
powershell -ExecutionPolicy Bypass -File tools/local-verify/run-smoke.ps1 -NoCoverage
```

### 「门禁整顿第一步」的完成标准（已完成）

**三条同时成立**才算完成：

1. 本地 `run-smoke.ps1` **从零库可复现全绿**
2. `ruff check` 是**拦截式**
3. CI 每次 push 都跑**"全新环境"路径**

> 2026-09-20 状态：**①②③ 全部满足**。

### 第二步：拆两个 commit

**Commit A「逻辑改动」**（已完成）

- eslint 14 条存量清理（10 未使用变量 / 3 hooks 依赖 / 1 多余转义）→ warn 提成 error → 挂上 CI
- `app/cli.py` 控制台输出改纯 ASCII（硬约定 I：跨宿主打印别赌默认代码页）
- 覆盖率门禁（API 进程采集 + `.coveragerc` 单一门槛来源）

**Commit B「纯格式」**（已完成）

- `prettier`（前端）+ `ruff format`（后端）全仓格式化 —— **90 文件 / +3638 −1631**
- CI 加 `npm run format:check` + `ruff format --check`（均拦截式）
- `.gitattributes`（`* text=auto eol=lf`）防"整个文件都变了"的假 diff
- 语义等价性验证：**52 个 Python 文件格式化前后 AST 完全一致**；前端 `next build` 通过
- （可选）`git pre-commit` hook —— **本批未做**，理由见 `docs/17`

> **为什么要拆**：A 是逻辑改动、B 是风格改动。混在一起时 diff 里
> **分不出"哪些是逻辑变化、哪些只是换了排版"**，评审时只能整体信任或整体怀疑。

### 一条贯穿始终的判据（硬约定 H）

**验收脚本本身也要被验收：把 `node_modules` / 数据库 / 缓存全删掉，它还能不能绿？**

`npm run lint` 曾经是假门禁（无配置 → 交互式提问卡死，跑得动只因从来没真跑过），
`run-smoke.ps1` 曾经是假验收（不灌题库，靠开发机脏库才绿）。
两者都是"给的是通过、而不是失败"，而且只在"环境恰好脏"时成立。

> **推论（新建"一键验收"脚本时先问）**：**它依赖哪些不在版本控制里的东西？**
> `data/`、`.env`、手工灌的数据、本机已装的全局包 —— 任何一项都会让它变成假绿。

---

## 八、下一步

**Batch 1 ~ 7 全部完成**（认证/RBAC → 管理后台 → 题库 CRUD → 导入管道 → 导入向导 →
组卷引擎（后端 + 前端）→ 状态机补齐），另有**数据范围收口**。往下可以接着推：

- **① 数据范围收口（已完成）** → 题目**八条入口**（列表 / 详情 / 新建 / 编辑 / 删除 /
  批量删除 / 恢复 / 两个下拉）统一走 `question_service.scope_subject_ids()` 这一份唯一来源；
  顺手消掉 `exam_service` 里的第二份副本。判据是「**凡是按 id 寻址的入口都要自己再拦一次**」
  —— 列表过滤防的是"翻到"，防不了"猜到"（`B端联调坑.md` 坑 48 / 49）。
- **② 门禁整顿（下一步）** → `npm run lint` 目前是**假门禁**（脚本在、配置不在，坑 44）；
  补 ESLint / Prettier 配置与覆盖率阈值。
- **③ 审计链补齐** → `docs/15-审计链补齐.md` 立项：`reviewed_by` / `audited_by` /
  `reviewer_id` 三处引用数全为 0 —— 状态能改，但"谁审的、什么时候审的"从没被记录。
- **记忆曲线** → 学员练习调度算法（依赖答题记录，需 C 端先落地）
- **记忆曲线** → 学员练习调度算法（依赖答题记录，需 C 端先落地）
- **移动端骨架** → 学员侧页面：首页倒计时、章节练习、答题卡、错题本、模拟考试、成绩报告
- **「调整方案」** → 告诉我哪里要改（比如要换 Spring Boot / 要加直播 / 要做多租户加盟商）

> ⚠️ **批量导入落地前必读**：导入会在同一毫秒连出多个雪花 ID，
> 正是坑文档第 21 条那颗雷的触发条件。**Batch 5 Pass 0 已修复**（生成端唯一 + 传输端全程字符串），
> 回归手段见 `tools/local-verify/probe-snowflake-batch.py` 与 `apps/api/tests/test_idgen.py`（15 条）。
> 后续任何"批量生成 ID"或"把 ID 拼进请求体"的新代码，都要再跑一次这两道防线。
>
> ⚠️ **组卷前必读**：版本锁定用**独立列** `exam_questions.locked_version`
> （`db/migrations/20260917-01-*.sql` 加的，含从旧 JSONB 的回填）。
> `rule_config.question_locks` 已 **deprecated**（仍双写 + 读时回退，下一批清理）。
> 另注意 `subjects` / `paper_rules` 等表**没有 `is_deleted`**，写校验前先看 `db/schema.sql`。
>
> ⚠️ **改了 `db/schema.sql` 别忘了写迁移**：`run-smoke.ps1` 只在**首次建库**时载入
> schema.sql，库已存在就整段跳过 —— 新结构只在新库存在（`B端联调坑.md` 坑 39）。
> 迁移放 `db/migrations/`，脚本自身必须幂等。
>
> 其余已知遗留项见 `docs/10-Batch4-题库CRUD-方案与验收.md` §7、
> `docs/12-Batch6-导入向导-方案与验收.md` §8、`docs/13-Batch7-组卷引擎-方案与验收.md` §7。
