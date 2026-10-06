"""不变量清单 —— **能机械检查的那部分**（`docs/24` §8 的 5 条 + `docs/25` 的第 6 条）。

第 6 条（2026-09-29 加）：**宿主删除保护** —— 会被拦的删除一律 `rename` 到 `.trash/`；
未登记的"直接删"必须变红。判据来源 = 项目约定 **R**，方案 `docs/25-宿主删除保护.md`。

第 9 条（2026-10-01 加）：**公开路由必须有站内入口** —— `middleware.ts::PUBLIC_PREFIXES`
里声明的每个公开路由，都要有人能**点**进去；否则它只能手敲 URL 才到得了。
（真缺陷：`/register` 页做出来了，登录页却按 P1 的计划**刻意没放入口**，
而那个 TODO 的触发条件"P2 加 `/register` 时"早就成立了 —— 见坑 94。）

第 10 条（2026-10-02 加）：**C 端 PWA 契约** —— manifest 必须 `standalone` + 192/512，
图标的**真实像素**必须与文件名一致（读 PNG 的 IHDR，不信文件名），
SW 的缓存白名单必须含静态资源、**不得含接口**，且 `sw.js` / `manifest.webmanifest` /
`offline.html` 三个必须**未登录也取得到**（否则装不上，而登录状态下看不出来）。
方案 = `docs/28-PWA收尾-方案.md`。

第 11 条（2026-10-03 加）：**E2E 场景清单三处必须一致** —— `e2e-web.py` 的
`--scenario` choices / 管道的 `--e2e-scenario` choices / `e2e-web.py` 的
`drivers` 注册表，必须**同一个集合**；`SCENARIOS_NEEDING_QUESTIONS` 是它的子集。
为什么要有它：这条清单**同一个事实写了三份**，而漏改的症状**不像配置错**
（`invalid choice` + 退出码 2 + 1 秒结束，看着像走查没跑起来）——
实测已经踩过两次（p2b1 / p2c3）。同族：坑 93（一个串被两个解析器读）。

为什么要有它（用户 2026-09-27）：
    不变量**不是"开工前确认一次"**，而是"**每次 push 后跑一次**" ——
    P0→P3 每推一次都可能把它们破掉。写成可执行文件才能每批收尾自动跑。

第 12 条（2026-10-06 加）：**离线 PWA 的复制品清单 + 三条结构契约**。
`apps/pwa` 的 UI 是**复制** `apps/web` 的（用户定的方案：不抽共享包）。
复制之后"同一份 UI 两份" ⇒ 在 C 端修了 bug、PWA 没修，而且**不报错**
（「同一个事实两种写法」那一族）。对策 = `apps/pwa/COPIED-FROM-WEB.md` 的机器可读块：
  ① 清单**必须覆盖**每一个"两个 app 都有"的文件（漏登记 ⇒ 红）；
  ② 标 `same` 的**必须逐字节相同**；
  ③ 标 `fork` 的**理由必须可判定** —— 要指名一个具体依赖或数据差异，且能被一条命令验证。
     ★ 反面写法（直接判红）：`有意简化` / `暂时这样` / `后续再改` / `先复制过来`
       —— **它们能套在任何一个文件上**，等于没有理由。
另有三条结构契约（都是"声明式的、不报错"那类）：
  a. `apps/pwa/src/lib/api.ts` 里**不许出现 `fetch(`** —— 它是"数据源换成 IndexedDB"的判据；
  b. `apps/pwa/public/sw.js` 的预缓存清单里**不许出现数据文件**（题库包 / questions）
     —— 否则 SW 与 IndexedDB 各存一份，换题库后两份不一致（症状：导入成功但题目还是旧的）；
  c. `apps/pwa` 的**判分不许自带字面量规则**（只许从包的 `meta.grading_rules` 读）。
方案 = `docs/32-个人PWA-方案.md`（§2.1 复制品门禁 / §2.2 SW 与 IndexedDB 的分工）。

判据来源：`docs/24-C端首批-范围冻结.md` §8；第 6 条 → `docs/25-宿主删除保护.md`。

设计约束
--------
- **纯标准库、只读仓库内文件** ⇒ 在干净环境（CI）也能跑（硬约定 H）。
- ⚠️ **刻意不 import yaml**：解析 `ci.yml` 只需要"步骤名在不在 + 有没有
  `continue-on-error`"，文本级足够；而 PyYAML 是**间接依赖**（靠 `uvicorn[standard]` 带进来），
  把它变成硬依赖 = 给这个脚本加一个"本地装了、CI 装不到"的失败模式。
- ⚠️ **剥注释再查**：`ci.yml` 的注释里大量出现 `continue-on-error` ——
  直接字符串搜索会把注释读成配置（**假红**）。
- **查不了的项就明说"查不了"**，不要假装检查过了（第 5 条只有一半能机械化）。

CLI
---
    python tools/local-verify/check-invariants.py            # 人类可读
    python tools/check-invariants.sh                          # 同一件事的薄包装
"""

from __future__ import annotations

import ast
import json
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: ★ 覆盖率门槛的**棘轮基线**。`fail_under` 只许 ≥ 它。
#: 抬门槛时必须**同时改这里** —— 这个"两处一起改"的动作本身就是棘轮的痕迹：
#: 想偷偷把门槛调低，就得先改一个名叫"基线"的常量（而不是改一个不起眼的数字）。
FAIL_UNDER_BASELINE = 94.40

#: 门禁清单（**18 道**）。名字逐字取自 `.github/workflows/ci.yml` 的 `name:`。
#: ⚠️ 历史口径说的"7 道"是 2026-09 早期的：后来加了诊断自检 / 反向检查 / 共享包单测 /
#: `format:check:shared` / `build` / **不变量自检** ⇒ 13 道；
#: 2026-09-27（P1）加 `apps/web` 的 5 道 ⇒ **18 道**。
#: ★ 为什么 C 端要**单独 5 道**而不是"复用 admin 的"：每个 app 有自己的
#:   `package.json`/lock/node_modules（不做 npm workspaces）⇒ 装依赖本来就分两次。
REQUIRED_GATES: list[tuple[str, str]] = [
    ("后端", "CI 诊断段自检（拦截式）"),
    ("后端", "不变量自检（拦截式）"),
    ("后端", "ruff check（拦截式，全仓）"),
    ("后端", "ruff format --check（拦截式）"),
    ("后端", "pytest"),
    ("后端", "覆盖率门禁（合并三份 → 报告 → 指纹）"),
    ("前端", "反向检查 · 认证链路单一真相（拦截式）"),
    ("前端", "共享包单测 · packages/api-core（拦截式）"),
    ("前端", "npm run lint（拦截式）"),
    ("前端", "npm run format:check（拦截式）"),
    ("前端", "npm run format:check:shared（拦截式）"),
    ("前端", "tsc --noEmit"),
    ("前端", "npm run build（拦截式，可编译性门禁）"),
    # ---- C 端（apps/web）—— 名字带 `web · ` 前缀，与 admin 那 5 道区分 ----
    ("前端 · C 端", "web · npm run lint（拦截式）"),
    ("前端 · C 端", "web · npm run format:check（拦截式）"),
    ("前端 · C 端", "web · npm run format:check:shared（拦截式）"),
    ("前端 · C 端", "web · tsc --noEmit（拦截式）"),
    ("前端 · C 端", "web · npm run build（拦截式，可编译性门禁）"),
]

_results: list[tuple[bool, str]] = []

#: `/admin/*` 的**接口路径字面量**：`/admin/` 前面**紧跟引号或反引号**。
#:
#: ⚠️ 为什么不写成裸的 `"/admin/" in line`（**第一版就是这么写的**）：
#:    那样会把 `apps/admin/src/lib/api.ts` 这类**路径引用**也报成违规 ——
#:    P1 给 `apps/web` 写"与 admin 同源"的注释时，**一次报出 4 处假红**。
#:    假红的代价不是"漏报"，而是**被人当成噪音**：一条会误报的守卫，
#:    下一步就是被人加豁免、或者干脆删掉（硬约定 J：**假红与假绿同族**）。
#:
#: ★ 收窄后**故意保留"注释里也算"**：带接口路径的注释是"第二份说明"，
#:   与 `check-auth-chain.mjs` 的理由一致（改了接口却忘了改注释 = 静默漂移）。
#:   判据：它匹配的是 `"/admin/…` 这种**字面量形态**；而 `apps/admin/…` 是路径，不是。
ADMIN_API_LITERAL = re.compile(r"""["'`]/admin/""")


def ok(label: str) -> None:
    _results.append((True, label))
    print(f"  [ok] {label}")


def bad(label: str) -> None:
    _results.append((False, label))
    print(f"  [FAIL] {label}")


def strip_comments(text: str) -> str:
    """去掉整行注释 —— 只用于"查配置"的场合，保住行号无关的语义。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def read(rel: str) -> str | None:
    p = REPO / rel
    if not p.is_file():
        bad(f"找不到 {rel}")
        return None
    return p.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 1
def check_gates() -> None:
    # ⚠️ 条数**从清单推导**，不写死 —— 写死的话"加门禁忘了改数字"会让输出自相矛盾，
    #    而矛盾的数字比没有数字更容易误导（2026-09-27：13 → 18 时就差一点漏改）。
    print(f"[1] 门禁完整性（{len(REQUIRED_GATES)} 道，全拦截式）—— 不变量 1")
    ci = read(".github/workflows/ci.yml")
    if ci is None:
        return
    body = strip_comments(ci)
    for job, name in REQUIRED_GATES:
        # ⚠️ 步骤名在 YAML 里写作 `- name: X` —— 正则**必须容忍那个 `- `**。
        #    2026-09-27 第一版写成 `^\s*name:`，于是 12 道门禁**全部**被报成"不见了"：
        #    这就是"**判据写糙给假红**"（硬约定 J 的反面）—— 它本会变成一个**恒红**的脚本，
        #    比没有脚本更糟（人会去查不存在的问题，然后不再信任它）。
        hits = len(re.findall(rf"^\s*(?:-\s*)?name:\s*{re.escape(name)}\s*$", body, re.MULTILINE))
        if hits == 1:
            ok(f"{job} · {name}")
        elif hits == 0:
            bad(f"{job} · {name} —— **这道门禁不见了**")
        else:
            bad(f"{job} · {name} —— 出现 {hits} 次（步骤名重复，门禁会被读成两个）")
    # 反向：ci.yml 里**所有**带"拦截式"的步骤都必须登记在上面 —— 否则"加了新门禁却忘了登记"
    # 会让这份清单慢慢**过期**，而过期清单比没有清单更糟（它给人"检查过了"的错觉）。
    # ⚠️ `pytest` / `tsc --noEmit` 的名字里没有"拦截式"，属**白名单式**登记 ⇒ 不参与反向检查。
    marked = {
        m.strip()
        for m in re.findall(r"^\s*-\s*name:\s*(.+?)\s*$", body, re.MULTILINE)
        if "拦截式" in m
    }
    unregistered = sorted(marked - {name for _, name in REQUIRED_GATES})
    if unregistered:
        bad(f"这些「拦截式」门禁**没登记**进 REQUIRED_GATES：{unregistered}")
    else:
        ok(f"ci.yml 里所有「拦截式」步骤（{len(marked)} 个）都已登记")
    # 拦截式：任何一步都不许挂 continue-on-error
    offenders = [
        ln.strip()
        for ln in body.splitlines()
        if re.search(r"continue-on-error:\s*(true|\$\{\{\s*true\s*\}\})", ln, re.IGNORECASE)
    ]
    if offenders:
        bad(f"有步骤挂 continue-on-error（门禁会变成'上报'而不是'拦截'）：{offenders}")
    else:
        ok(f"{len(REQUIRED_GATES)} 道全部是**拦截式**（没有任何 continue-on-error）")


# ------------------------------------------------------------------ 2
def check_coverage_ratchet() -> None:
    print("[2] 覆盖率门槛棘轮 —— 不变量 2")
    rc = read(".coveragerc")
    if rc is None:
        return
    m = re.search(r"^fail_under\s*=\s*([\d.]+)\s*$", rc, re.MULTILINE)
    if not m:
        bad(".coveragerc 里找不到 fail_under")
        return
    got = float(m.group(1))
    if got >= FAIL_UNDER_BASELINE:
        ok(f"fail_under = {got} ≥ 棘轮基线 {FAIL_UNDER_BASELINE}")
    else:
        bad(f"fail_under = {got} **低于**棘轮基线 {FAIL_UNDER_BASELINE} —— 门槛被调低了")
    # greenlet：不配这一行覆盖率是**错的**（不是偏低），踩过（.coveragerc 顶部有长注释）
    if re.search(r"^concurrency\s*=\s*greenlet\s*$", rc, re.MULTILINE):
        ok("`.coveragerc` 配了 `concurrency = greenlet`")
    else:
        bad("`.coveragerc` 缺 `concurrency = greenlet` ⇒ 覆盖率数字**是错的**（丢 await 之后的行）")
    # CI 装不到的依赖 = 本地绿、CI 红
    req = read("apps/api/requirements.txt")
    if req is not None:
        for pkg in ("ruff", "fakeredis", "coverage", "pytest-timeout"):
            if re.search(rf"^{pkg}\b", req, re.MULTILINE):
                ok(f"`requirements.txt` 含 `{pkg}`")
            else:
                bad(f"`requirements.txt` **缺** `{pkg}` ⇒ CI 上装不到（本地'碰巧装了'不算）")


# ------------------------------------------------------------------ 3
def check_ephemeral_db() -> None:
    print("[3] 本地验证默认跑一次性库（BL-17）—— 不变量 3")
    pipe = read("tools/local-verify/run-local-pipeline.py")
    if pipe is not None:
        # `--db` 的默认值必须是空串 = 自动生成一次性库
        m = re.search(r'add_argument\(\s*"--db"\s*,\s*default\s*=\s*"([^"]*)"', pipe)
        default_db = m.group(1) if m else None
        if default_db == "":
            ok("`run-local-pipeline.py` 的 `--db` 默认为空 ⇒ 默认跑**一次性库**")
        else:
            bad(f"`run-local-pipeline.py` 的 `--db` 默认值 = {default_db!r} ⇒ 默认不再是干净基线")
    # serve_fake_redis 不许**强制**覆盖 DATABASE_URL（坑 71）
    api = read("tools/local-verify/serve_fake_redis.py")
    if api is not None:
        body = strip_comments(api)
        if re.search(r'os\.environ\[\s*"DATABASE_URL"\s*\]\s*=', body):
            bad(
                "`serve_fake_redis.py` 又**强制赋值** `DATABASE_URL` 了 ⇒ API 会被指向写死的库（坑 71）"
            )
        elif "os.environ.setdefault(" in body and '"DATABASE_URL"' in body:
            ok("`serve_fake_redis.py` 用 `setdefault` ⇒ 尊重调用方给的库")
        else:
            bad("`serve_fake_redis.py` 里找不到 DATABASE_URL 的 setdefault")


# ------------------------------------------------------------------ 4
def check_admin_isolation() -> None:
    print("[4] `/admin/*` 隔离（C 端不许复用管理端接口）—— 不变量 4")
    web_src = REPO / "apps" / "web" / "src"
    if not web_src.is_dir():
        print("  [--] `apps/web` 还没建（P1 才建）⇒ 跳过扫描，不是通过")
    else:
        offenders = []
        for f in web_src.rglob("*"):
            if not f.is_file() or f.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
                continue
            for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if ADMIN_API_LITERAL.search(ln):
                    offenders.append(f"{f.relative_to(REPO)}:{i}")
        if offenders:
            bad(f"C 端代码里出现 `/admin/` **接口路径字面量**：{offenders[:5]}")
        else:
            ok("`apps/web/src` 里没有 `/admin/` 接口路径字面量")
    ci = read(".github/workflows/ci.yml")
    if ci is not None and "check-auth-chain.mjs" in strip_comments(ci):
        ok("CI 里有反向检查 `check-auth-chain.mjs`（会覆盖新 app）")
    else:
        bad("CI 里找不到 `check-auth-chain.mjs` ⇒ 新 app 没人扫")


# ------------------------------------------------------------------ 5
def check_docs() -> None:
    print("[5] 文档声称 —— 不变量 5（**只有一半能机械化**）")
    if (REPO / "docs" / "23-文档与现实对照清单.md").is_file():
        ok("`docs/23-文档与现实对照清单.md` 在（清单本身没丢）")
    else:
        bad("`docs/23-文档与现实对照清单.md` 不见了")
    if (REPO / "docs" / "24-C端首批-范围冻结.md").is_file():
        ok("`docs/24-C端首批-范围冻结.md` 在")
    else:
        bad("`docs/24-C端首批-范围冻结.md` 不见了")


# ------------------------------------------------------------------ 6
#: 只扫**代码**。`.md` 不扫 —— `docs/` 里有大量 `rm -rf` 的**示例**，
#: 把它们算成违规就是"判据写糙给假红"，而假红的下场是被加豁免或干脆删掉（硬约定 J）。
GUARD_SUFFIXES: dict[str, str] = {
    ".py": "py",
    ".sh": "sh",
    ".bash": "sh",
    ".ps1": "ps1",
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "js",
    ".tsx": "js",
}
#: 无后缀但**必须显式扫到** —— 否则"没扫"是一个**静默漏洞**：没人会问"为什么它不在扫的范围里"。
GUARD_EXTRA_NAMES: dict[str, str] = {"Dockerfile": "sh"}
#: 扫的时候整目录跳过（构建产物 / 依赖 / 回收站本身）。
GUARD_SKIP_PARTS = frozenset(
    {"node_modules", "__pycache__", ".git", ".trash", ".ruff_cache", ".pytest_cache", "htmlcov"}
)

#: 删除 token —— **按语言分开**。`rm -rf` 出现在 `.py` 的字符串里不是删除；
#: `unlink(` 出现在 `.sh` 里也不是。（合成一张表会有大量假红。）
DELETE_TOKENS: dict[str, tuple[str, ...]] = {
    # trash-ok: 判据的定义处（token 表）—— 这些串是"要找什么"，不是"要删什么"
    "py": ("shutil.rmtree(", "os.remove(", "os.unlink(", "os.rmdir(", ".unlink(", ".rmdir("),
    "sh": ("rm -rf", "rm -fr", "rm -r ", "rm -f ", "rmdir "),
    "ps1": ("Remove-Item",),
    "js": (
        "fs.rm(",
        "fs.rmSync(",
        # trash-ok: 同上（token 表）
        "fs.unlink(",
        "fs.unlinkSync(",
        # trash-ok: 同上（token 表）
        "fs.rmdir(",
        "fs.rmdirSync(",
        "recursiveDelete(",
        "rimraf(",
    ),
}

#: **按路径整体豁免**。理由必须写在**这里**（一处可审完），不散在代码注释里。
PATH_EXEMPT: dict[str, str] = {
    "apps/api/Dockerfile": "在**容器镜像内**执行（`RUN` 层），不碰宿主文件系统 —— 宿主的删除保护管不到它",
}

#: 已经"改对了"的地方没得可查，但**登记过的豁免**要能被一眼数出来（否则豁免会悄悄变多）。
TRASH_OK = "trash-ok:"


def _strip_py(text: str) -> str:
    """抹掉 Python 的 `#` 注释与三引号块，**保留换行**（行号因此仍然对齐）。

    ⚠️ 必须抹：`preflight.sh` 的 ⓿ 段里就有一条**打印给用户看的** `rm -rf .trash`
    手工命令（不是它自己执行的删除）。不抹 ⇒ 它变成一个恒红的假红。
    ⚠️ 只做**词法级**处理（不建 AST）：三引号块与行尾注释靠状态机扫掉就够了，
    而这一层的目的只是"别把注释里的 SQL 当成真的 SQL"（不变量 6/8 用它）。
    （不是"不能用 ast" —— 本文件在**不变量 7** 里确实 import 了 `ast` 做结构分析；
     两处的需求不同：那边要看"哪个函数调了哪个常量"，词法级看不出来。
     `ast` 是标准库，与"零第三方依赖"不冲突。）
    """
    res: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            if text.startswith(quote, i):
                res.append(" " * 3)
                i += 3
                quote = None
                continue
            res.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            quote = text[i : i + 3]
            res.append(" " * 3)
            i += 3
            continue
        if ch == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            res.append(" " * (j - i))
            i = j
            continue
        res.append(ch)
        i += 1
    return "".join(res)


def _strip_eol(text: str, markers: tuple[str, ...]) -> str:
    """抹掉行尾注释（shell / PS / JS），保留换行与行内缩进。"""
    out: list[str] = []
    for ln in text.split("\n"):
        cut = len(ln)
        for m in markers:
            k = ln.find(m)
            if k >= 0:
                cut = min(cut, k)
        out.append(ln[:cut] + " " * (len(ln) - cut))
    return "\n".join(out)


def _guard_files():
    """产出 `(相对路径, 语言)`；只含**代码**。"""
    roots = [REPO / d for d in ("tools", "apps", "packages", "db", "deploy")]
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(REPO)
            if any(p in GUARD_SKIP_PARTS or p.startswith(".next") for p in rel.parts):
                continue
            if f.name in GUARD_EXTRA_NAMES:
                yield str(rel).replace("\\", "/"), GUARD_EXTRA_NAMES[f.name]
            elif f.suffix in GUARD_SUFFIXES:
                yield str(rel).replace("\\", "/"), GUARD_SUFFIXES[f.suffix]


def _strip(text: str, lang: str) -> str:
    if lang == "py":
        return _strip_py(text)
    if lang in ("sh", "ps1"):
        return _strip_eol(text, ("#",))
    return _strip_eol(text, ("//",))  # js


def _is_marker_line(line: str) -> bool:
    """这一行是不是"**只有注释、且写着 `trash-ok:`**"？—— 登记可以写在删除的**上一行**。

    为什么允许上一行：代码行常常已经贴着 100 列上限，再往后接一句理由会**为了写登记而破格式**
    （而格式红线本身是另一道门禁）。允许"上一行独立注释"之后，两条约束不打架。
    ⚠️ 只认**独立注释行**：`x = 1  # trash-ok` 这种带代码的不算 —— 否则
    `# trash-ok` 会被一个"顺手的说明"满足掉，登记就退化成噪音。
    """
    s = line.strip()
    return (
        (s.startswith("#") or s.startswith("//") or s.startswith("/*"))
        and TRASH_OK in s
        and len(s) < 160
    )


def check_delete_guard() -> None:
    print("[6] 宿主删除保护（R：会被拦的删除一律改 rename）—— 不变量 6")
    scanned = 0
    offenders: list[str] = []
    registered: list[str] = []
    for rel, lang in _guard_files():
        if rel in PATH_EXEMPT:
            continue
        scanned += 1
        try:
            raw = (REPO / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            bad(f"{rel} 读不了 —— 「读不了」不能算通过")
            continue
        stripped = _strip(raw, lang).split("\n")
        raw_lines = raw.split("\n")
        for i, (code, orig) in enumerate(zip(stripped, raw_lines), 1):
            hit = next((t for t in DELETE_TOKENS[lang] if t in code), None)
            if hit is None:
                continue
            if TRASH_OK in orig or _is_marker_line(raw_lines[i - 2] if i >= 2 else ""):
                registered.append(f"{rel}:{i}")
                continue
            offenders.append(f"{rel}:{i}  「{hit}」  {orig.strip()[:58]}")
    for rel, why in PATH_EXEMPT.items():
        if (REPO / rel).is_file():
            print(f"  [--] 整路径豁免：{rel} —— {why}")
    if offenders:
        bad(f"{len(offenders)} 处**未登记**的删除 —— 宿主的删除保护会拦（或至少是隐患）")
        for o in offenders[:10]:
            print(f"        {o}")
        if len(offenders) > 10:
            print(f"        …还有 {len(offenders) - 10} 处")
        print(
            "      ⇒ 两条出路：① **改 rename**（`from trash import trash` 一行搞定）；"
            f"② 确实是**单文件 + 非递归** ⇒ 在**同一行尾部**或**上一行独立注释**里写 `{TRASH_OK} <理由>`。"
        )
    else:
        ok(f"扫描 {scanned} 个代码文件 —— 没有未登记的删除（已登记豁免 {len(registered)} 处）")
        if registered:
            print(
                f"        （豁免清单：{'、'.join(registered[:6])}{'…' if len(registered) > 6 else ''}）"
            )

    # 回收站本身：它在不在版本控制里、入口是不是唯一
    if (REPO / "tools" / "local-verify" / "trash.py").is_file():
        ok("唯一回收站入口 `tools/local-verify/trash.py` 在")
    else:
        bad("`tools/local-verify/trash.py` 不见了 ⇒ `.trash/` 的布局会退化成各写一份")
    gi = read(".gitignore")
    if gi is not None:
        if re.search(r"^\.trash/?\s*$", gi, re.MULTILINE):
            ok("`.gitignore` 含 `.trash/`")
        else:
            bad("`.gitignore` 缺 `.trash/` ⇒ 回收站里的东西会进版本库")


# ------------------------------------------------------------------ 7
#: 写库的语句长什么样（只认**字面量里的 SQL**）。用 `\b` 卡词边界：
#: `UPDATE ` 与 `INSERT INTO` / `DELETE FROM` 是三种形态，别的词（如 `UPDATE` 出现在
#: 描述文本里）不该被算进来 —— 但那属于"字面量不是 SQL"的情况，见下面的 `text(...)` 约束。
_WRITE_SQL = re.compile(r"\b(INSERT\s+INTO|UPDATE\s|DELETE\s+FROM)\b", re.I)

#: 扫哪些目录。**只扫 service 层**：路由层不该直接写库（那是分层的意义），
#: 而 `db/seed` / `tools/` 不在请求事务里，规则不同（它们自己管连接）。
_WRITE_SCAN_ROOT = "apps/api/app/services"

#: 扫到的"写库函数"个数的**下限**。低于它 ⇒ 认为解析器坏了，而不是"代码很干净"。
#: ★ 这是本文件里第三次用"下限"这个手法（前两次在删除保护与答案格式）：
#:   **"零发现"最容易被读成"通过"**，而它更可能是"我根本没扫到"（硬约定 H）。
MIN_WRITE_FUNCS = 25


def _text_literal_is_write(node: ast.AST) -> bool:
    """这个表达式是不是 `text("...写语句...")`（允许外面套 `.bindparams(...)`）？"""
    while isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "text":
            if node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    return bool(_WRITE_SQL.search(a.value))
                if isinstance(a, ast.JoinedStr):  # f-string
                    parts = [
                        v.value
                        for v in a.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    ]
                    return bool(_WRITE_SQL.search("".join(parts)))
                return False
            return False
        return False
    return False


def _module_write_names(tree: ast.Module) -> set[str]:
    """模块级常量里那些"装着写语句的 `text(...)`" —— 例如 `_INSERT_SESSION = text("INSERT ...")`。

    ★★ 为什么必须有这一步（不是锦上添花）：`practice_service` 把SQL 放在**模块级常量**里，
      函数体里只写 `db.execute(_INSERT_SESSION, {...})`。
      只扫函数体的话，那个**真的漏了 `commit` 的函数会被整个跳过** ——
      也就是说，这条不变量**本来该抓到 P2b-1 那个 bug，却抓不到**。
      （判据同硬约定 J：判据本身要先证明"它在该红的场景真的红过"。）
    """
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None or not _text_literal_is_write(value):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _calls_db_execute_with(node: ast.AST, names: set[str]) -> bool:
    """函数体里有没有 `….execute(<写常量>, …)`。"""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr in ("execute", "execute_dml")):
            continue
        if not n.args:
            continue
        a = n.args[0]
        if isinstance(a, ast.Name) and a.id in names:
            return True
    return False


def _has_commit(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "commit"
        ):
            return True
    return False


def check_write_paths_commit() -> None:
    print("[7] 写路径必须提交（`get_db` 不自动 commit）—— 不变量 7")
    root = REPO / _WRITE_SCAN_ROOT
    offenders: list[str] = []
    public_writes: list[str] = []
    private_writes = 0
    files = 0
    for f in sorted(root.glob("*.py")):
        files += 1
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad(f"{_WRITE_SCAN_ROOT}/{f.name} 语法错误：{e}")
            continue
        consts = _module_write_names(tree)
        for node in tree.body:
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            writes = any(_text_literal_is_write(x) for x in ast.walk(node)) or (
                _calls_db_execute_with(node, consts)
            )
            if not writes:
                continue
            if node.name.startswith("_"):
                # 私有 helper **允许**不提交 —— 它由调用方在同一事务里提交（本仓一直如此，
                # 且 `import_service._write_rows` 的 docstring 明写了这条约定）。
                private_writes += 1
                continue
            public_writes.append(f"{f.name}::{node.name}")
            if not _has_commit(node):
                offenders.append(f"{_WRITE_SCAN_ROOT}/{f.name}::{node.name}")
    total = len(public_writes)
    if total < MIN_WRITE_FUNCS:
        bad(
            f"只扫到 {total} 个「写库的公开 service 函数」（下限 {MIN_WRITE_FUNCS}）"
            "—— 先确认解析器/路径没写错，再把下限调下来（**别直接调低**）"
        )
        return
    if offenders:
        bad(f"{len(offenders)} 个**写着库却看不到 `commit()`** 的公开 service 函数：")
        for o in offenders:
            print(f"        {o}")
        print(
            "      ⇒ `app/db/base.py::get_db` **不自动提交**（只在异常时 rollback）。\n"
            "         漏了它的症状极有欺骗性：**200 + code=0 + 返回合法 id，数据却被回滚**\n"
            "         ⇒ 紧接着按 id 查就是 404。**判据：写路径「成功了但查不到」，先查 commit 再查 SQL。**"
        )
        return
    ok(
        f"{total} 个写库的公开 service 函数**都含 `commit()`**"
        f"（另有 {private_writes} 个私有 helper 由调用方提交，允许没有）｜扫了 {files} 个 service 文件"
    )


# ------------------------------------------------------------------ 8
#: `{"value"` 这个字面量**只允许出现在这些文件里**（`answer` 的构造入口）。
#: 理由必须写在这里（一处可审完），只有"确实是另一个用途"才登记。
ANSWER_LITERAL_ALLOW = {
    "apps/api/app/schemas/answer.py": "**规范形式的定义处** —— 唯一允许拼 `answer` 的地方",
    "apps/api/app/services/practice_service.py": "出参侧（`public_answer` 把答案发给前端），不是写库",
}
MIN_ANSWER_SCAN_FILES = 40

#: 判据只认**字典字面量构造**：`{"value": …}`（允许空格/引号变体）。
#:
#: ⚠️ 第一版写的是"这一行里出现 `"value"` 就算" —— 一跑就报 **12 处**，而其中绝大多数是**读取**
#:   （`doc.get("value")` / `answer->'value'` / 另一张表的 `value` 列）。
#:   那正是"判据写糙给假红"（硬约定 J 的反面）：**一条会误报的守卫，下一步就是被人加豁免、
#:   或者干脆删掉**。收窄到"`{` 紧接 `"value"`"之后，误报消失，剩下的就都是真构造。
#: （本检查是**逐行**跑的，所以 `{` 在上一行的多行字面量不会被算进来 —— 那正是收窄能成立的原因。）
_ANSWER_CONSTRUCT = re.compile(r"""\{\s*["']value["']\s*:""")


def check_answer_single_source() -> None:
    print("[8] 答案只有一个构造入口（规范形式）—— 不变量 8")
    seen = 0
    offenders: list[str] = []
    for rel, lang in _guard_files():
        if not rel.endswith(".py"):
            continue
        if not (rel.startswith("apps/api/app/") or rel.startswith("db/seed/")):
            continue
        seen += 1
        raw = (REPO / rel).read_text(encoding="utf-8")
        if rel in ANSWER_LITERAL_ALLOW:
            continue
        for i, code in enumerate(_strip(raw, lang).split("\n"), 1):
            if _ANSWER_CONSTRUCT.search(code):
                offenders.append(f"{rel}:{i}  {code.strip()[:60]}")
    for rel, why in ANSWER_LITERAL_ALLOW.items():
        if (REPO / rel).is_file():
            print(f"  [--] 允许：{rel} —— {why}")
    if seen < MIN_ANSWER_SCAN_FILES:
        bad(f"只扫到 {seen} 个文件（下限 {MIN_ANSWER_SCAN_FILES}）—— 路径写错了吗")
        return
    if offenders:
        bad(f'{len(offenders)} 处**自己拼 `{{"value": …}}`**，绕过规范形式：')
        for o in offenders[:10]:
            print(f"        {o}")
        print(
            "      ⇒ 用 `app.schemas.answer.canonical_doc(qtype, tokens, partial_credit=…)` 一行搞定；\n"
            "         确实另有用途就登记进 `ANSWER_LITERAL_ALLOW`（带理由）。\n"
            "         **两种格式都在跑 ⇒ 一定有读取方在处理类型强制**（坑 76）。"
        )
        return
    ok(f"扫 {seen} 个文件 —— 没有绕过 `canonical_doc()` 的 `answer` 构造")


# ------------------------------------------------------------------ 9

#: `apps/*/src/middleware.ts` 里公开路由的声明处。
_PUBLIC_PREFIXES_RE = re.compile(r"const\s+PUBLIC_PREFIXES\s*=\s*\[(?P<body>[^\]]*)\]")

#: 「站内入口」的判据：**别处有人引用过这个路径字面量**（引号包着的 `/xxx`）。
#:
#: ★★ 为什么绑在**字面量**上、而不是"文本里出现过这个路径"（2026-10-01 真缺陷的形状）：
#:    登录页里有一段注释写着 "P2 加 `/register` 时，把入口补在这里。"，
#:    而 `/register` 页**早就做出来了、却没有任何入口** —— 只能手敲 URL 才能到，
#:    用户按 `docs/27` §3.5 走"点**注册**"时看到的是一扇**没有门的墙**。
#:    按"出现过"来扫，**那段注释会让它假绿** —— 假绿比漏报更坏（硬约定 J）。
#:
#: ★★ 又为什么是**弱判据**（只看"有人引用"，不看"引用得对不对"）：
#:    第一版要求匹配 `href="/x"` / `router.push("/x")` 这种**入口写法**，
#:    当场在 `admin /forbidden` 上**假红** —— 它是被
#:    `login/page.tsx` 里 `router.replace(sp.get("next") || … || "/forbidden")`
#:    引用的，**目标是算出来的**，任何"写法级"正则都看不见它。
#:    ⇒ 判据：**宁可弱，不许假红**。一条会误报的守卫，下一步就是被人加豁免或删掉
#:      （同族：`ADMIN_API_LITERAL` 收窄的理由）。
#:    ⚠️ 边界（如实写明）：它保证"**有人引用**该路径"，**不**保证"点得到"。
#:      ⇒ 它仍然能抓住本次这类缺陷（"页做出来了、但**没有任何地方**引用它"）。
_QUOTED_PATH_RE = re.compile(r"""["'`](/[^"'`]*)["'`]""")

#: 扫到的文件数下限。**"扫了 0 个文件"≠"通过"** —— 那是"量错了对象"，必须显式失败。
MIN_WEB_SCAN_FILES = 5


def _strip_js_comments(text: str) -> str:
    """先抹 `/* … */` 块注释，再抹 `//` 行注释（保留换行，行号仍对齐）。

    ⚠️ 为什么要抹**块**注释：JSX 里的注释写作 `{/* … */}`，而 `_strip()` 那套
    只处理 `//` —— 登录页那段"把入口补在这里"的注释**正好是 `{/* */}`**，
    不抹它，本检查就白写了（那段注释里就有 `` `/register` ``）。
    ⚠️ 边界：块注释**中间的行**不含 `/*`，抹不掉；但那些行也不会是唯一引用。
    """
    text = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text, flags=re.S)
    return _strip_eol(text, ("//",))


def check_public_route_entrypoints() -> None:
    print("[9] 公开路由必须在**别处被引用**（否则只能手敲 URL 才到得了）—— 不变量 9")

    targets: list[tuple[str, list[str]]] = []
    for app_dir in sorted(p for p in (REPO / "apps").iterdir() if p.is_dir()):
        rel = f"apps/{app_dir.name}/src/middleware.ts"
        if not (REPO / rel).is_file():
            continue
        m = _PUBLIC_PREFIXES_RE.search((REPO / rel).read_text(encoding="utf-8"))
        if m is None:
            bad(f"{rel} 里读不到 `PUBLIC_PREFIXES` —— 本检查会静默变成假绿，故直接失败")
            continue
        targets.append((app_dir.name, _QUOTED_PATH_RE.findall(m.group("body"))))

    if not targets:
        bad("`apps/*/src/middleware.ts` 一个都没读到 —— 目录结构变了？")
        return

    scanned = 0
    owners: dict[str, set[str]] = {}  # 路径字面量 -> 出现过它的文件
    for rel, lang in _guard_files():
        if lang != "js" or not (rel.startswith("apps/") and "/src/" in rel):
            continue
        # ⚠️ `middleware.ts` **自己不算入口** —— 它是公开路由的**声明处**，
        #    把它算进去 ⇒ 每个公开路由都"有引用" ⇒ 这条检查恒绿。
        if rel.endswith("/middleware.ts"):
            continue
        scanned += 1
        code = _strip_js_comments((REPO / rel).read_text(encoding="utf-8"))
        for mm in _QUOTED_PATH_RE.finditer(code):
            owners.setdefault(mm.group(1), set()).add(rel)

    if scanned < MIN_WEB_SCAN_FILES:
        bad(f"只扫到 {scanned} 个前端文件（下限 {MIN_WEB_SCAN_FILES}）—— 路径写错了吗")
        return

    missing: list[str] = []
    skipped_static: list[str] = []
    for app, prefixes in targets:
        for prefix in prefixes:
            # ★★ 静态文件**不是页面**，不参与这条检查（2026-10-02 加）。
            #
            # 为什么必须排除：PWA 把 `/sw.js`、`/manifest.webmanifest`、`/offline.html`
            # 加进了 `PUBLIC_PREFIXES`（它们**必须**能被未登录访客取到，见 `docs/28` §1.1），
            # 可它们的引用天然落在**本检查扫不到的地方**：
            #   · `/manifest.webmanifest` 由 Next 从 `src/app/manifest.ts` 产出，没人"引用"它；
            #   · `/offline.html` 被 `public/sw.js` 引用 —— 而这里只扫 `apps/*/src/**`；
            #   · `/sw.js` 恰好被 `sw-register.tsx` 的 `register("/sw.js")` 引用，所以它"过"了。
            # ⇒ 不排除就会**假红**，而假红的下场是被人加豁免或干脆删掉这条检查（硬约定 J）。
            #
            # 判据：**带扩展名 = 静态文件**（App Router 的页面路径一律不含 `.`）。
            # 所以这条排除**不会**放过"新加了页面却忘了加入口"——页面路径没有点。
            if "." in prefix:
                skipped_static.append(f"{app} {prefix}")
                continue
            # 引用必须来自**别的目录**：`src/app/register/page.tsx` 里引用自己不算入口
            own_dir = f"apps/{app}/src/app{prefix}"
            hit = {
                rel
                for path, rels in owners.items()
                if (path == prefix or path.startswith(prefix + "/"))
                for rel in rels
                if not rel.startswith(own_dir)
            }
            if not hit:
                missing.append(f"{app} {prefix}")

    if missing:
        bad(f"{len(missing)} 个公开路由**没有任何地方引用**：{' / '.join(missing)}")
        print(
            "      ⇒ 加一个跳转入口（`<Link href=\"/xxx\">` 或 `router.push(\"/xxx\")`）。\n"
            "         公开路由的声明处 = `middleware.ts::PUBLIC_PREFIXES`（那本身就是提醒：\n"
            "         新加公开页时**必须同时加入口**，否则它只能手敲 URL 才能到 ——\n"
            "         而『手敲得到』在验收时**看不出来**（照文档点不到，只会以为是自己点错了）。"
        )
        return

    total = sum(len(p) for _, p in targets) - len(skipped_static)
    note = f"（另跳过 {len(skipped_static)} 个静态文件：{' / '.join(skipped_static)}）" if skipped_static else ""
    ok(f"扫 {scanned} 个前端文件 —— {total} 个公开**页面**都有人在别处引用{note}")


# ------------------------------------------------------------------ 10

#: C 端 PWA 契约（2026-10-02 加，`docs/28`）。
#:
#: ★ 这一组**只做静态断言**（读文件字节），**不依赖浏览器** —— 所以它能在 CI、在任何
#:   干净环境跑（硬约定 H）。浏览器那一层（真的能装、断网真的不白屏）由
#:   `tools/local-verify/probe-pwa-offline.py` 负责，两者**不能互相代替**：
#:     · 这组管"**产物对不对**"（尺寸、字段、白名单、可达性）；
#:     · 探针管"**行为对不对**"（注册成功、断网渲染出离线页）。
#:   而它们各自都会漏：只跑门禁 ⇒ "文件都在，就是装不上"；只跑探针 ⇒
#:   探针在 dev 下能过，线上却因为**没加 PUBLIC_PREFIXES** 而失败。
#:
#: ★ 为什么值得写成门禁（每条都对应一个"**只有真机/线上才发现**"的失败模式）：
#:   a) 缺 `display: standalone` ⇒ 装到桌面仍带地址栏（看起来"像网页书签"）；
#:   b) 图标尺寸与文件名不符 ⇒ 启动图/桌面图标糊掉或直接被拒（**本机没浏览器读数**）；
#:   c) 白名单里混进接口路径 ⇒ 悄悄缓存了不该缓存的响应（**不报错**）；
#:   d) 三个静态文件没进 `PUBLIC_PREFIXES` ⇒ 未登录访客拿不到它们
#:      （**登录状态下完全看不出来** ⇒ 最容易在"我自测是好的"里漏掉）。
_PWA_APP = "apps/web"
#: 文件名 -> 期望边长。**文件名是可以撒谎的**，所以下面要读 IHDR 核对。
_PWA_ICONS: dict[str, int] = {
    "icon-192.png": 192,
    "icon-512.png": 512,
    "maskable-512.png": 512,
    "apple-touch-icon.png": 180,
}
#: 这三个**必须**能被未登录访客取到（判据见 C 端 `middleware.ts` 顶部的注释）。
_PWA_MUST_BE_PUBLIC = ["/sw.js", "/manifest.webmanifest", "/offline.html"]


def _png_size(path: Path) -> tuple[int, int] | None:
    """读 PNG 的 **IHDR** 拿真实宽高（第 16~24 字节）。读不出返回 `None`。

    ⚠️ 为什么不看文件名、也不看 `<img>` 上的 `width`：**它们在撒谎时不会报错**。
       "icon-192.png 里放一张 512 的图"在浏览器里表现为"图标有点糊"或者
       "启动画面被裁"，且**本机没有浏览器可以读数** ⇒ 只信字节。
    """
    try:
        with path.open("rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return int(w), int(h)


def check_pwa_contract() -> None:
    print("[10] C 端 PWA 契约（manifest / 图标真尺寸 / SW 白名单 / 静态文件未登录可达）—— 不变量 10")

    # ---- a) manifest：standalone + 192/512(any) + maskable ----
    mrel = f"{_PWA_APP}/src/app/manifest.ts"
    if not (REPO / mrel).is_file():
        bad(f"缺 `{mrel}` —— 没有 manifest ⇒ `display: standalone` 没有来源，装不到桌面")
    else:
        # ★★ **必须先剥注释**（2026-10-02 实测踩到）：第一版直接搜原文，
        #    而 `manifest.ts` 的注释里就写着 `display: "standalone"` 这句判据说明
        #    ⇒ 把 `display` 改成别的值之后，检查**依然是绿的**（假绿）。
        #    变异套件当场抓到它（"存活 2 条"）—— 这说明**注释能满足任何字符串判据**。
        #    ⇒ 判据：凡"某字段值必须是什么"的检查，**先剥注释再搜**。
        src = _strip_js_comments((REPO / mrel).read_text(encoding="utf-8"))

        # 按**条目**断言，而不是"文件里出现过这个字符串"：
        # `sizes` 与 `purpose` 在每条 icon 里各出现一次（prettier 也保持这个顺序），
        # 所以按出现顺序配对即可。这样"删掉 `any` 的 512、只留 maskable 的"能被抓到 ——
        # 而字符串级判断会以为"512 还在"（maskable 那条也是 512×512）。
        sizes = re.findall(r'sizes:\s*"(\d+x\d+)"', src)
        purposes = re.findall(r'purpose:\s*"([a-z]+)"', src)
        problems: list[str] = []
        if len(sizes) != len(purposes):
            # 数不齐 ⇒ 本检查会静默变成假绿，直接失败（"查不了"不能当"通过"）
            problems.append(f"icons 里 `sizes` {len(sizes)} 条、`purpose` {len(purposes)} 条，数不齐")
        else:
            pairs = sorted(set(zip(sizes, purposes)))
            if ('192x192', 'any') not in pairs:
                problems.append('缺 192×192 的 `purpose: "any"`')
            if ('512x512', 'any') not in pairs:
                problems.append('缺 512×512 的 `purpose: "any"`（**缺它时 Chrome 不显示「安装」**）')
            if not any(p == 'maskable' for _, p in pairs):
                problems.append('缺 `purpose: "maskable"`（边缘会被 Android 裁掉）')
        if '"standalone"' not in src:
            problems.append('没有 `display: "standalone"`（判据「打开后无地址栏」靠它）')

        if problems:
            bad(f"`{mrel}`：" + "；".join(problems))
        else:
            ok(f"`{mrel}` —— standalone + 192/512(any) + maskable 都在：{sorted(set(zip(sizes, purposes)))}")

    # ---- b) 图标真尺寸（读字节）----
    missing_icons = [n for n in _PWA_ICONS if not (REPO / _PWA_APP / "public/icons" / n).is_file()]
    wrong: list[str] = []
    for name, want in _PWA_ICONS.items():
        if name in missing_icons:
            continue
        got = _png_size(REPO / _PWA_APP / "public/icons" / name)
        if got != (want, want):
            wrong.append(f"{name} 实际 {got}（期望 {want}×{want}）")
    if missing_icons:
        bad(
            f"缺图标 {' / '.join(missing_icons)} —— 跑 `tools/local-verify/gen-pwa-icons.py` 生成"
            "（PNG 与脚本一起提交，换 logo 时可重跑）"
        )
    elif wrong:
        bad("图标**尺寸与文件名不符**：" + "；".join(wrong))
    else:
        ok(f"{len(_PWA_ICONS)} 张图标尺寸与文件名一致（按 PNG 的 IHDR 实读，不信文件名）")

    # ---- c) SW 白名单：含静态资源、不含接口 ----
    srel = f"{_PWA_APP}/public/sw.js"
    if not (REPO / srel).is_file():
        bad(f"缺 `{srel}` —— 没有 SW 就没有缓存与离线兜底")
    else:
        # ★ 同样**先剥注释**：`sw.js` 的注释里就整段解释着这个白名单
        #   （包括 `/_next/static/` 这几个字）—— 不剥注释，改坏了也会被注释救回来。
        ssrc = _strip_js_comments((REPO / srel).read_text(encoding="utf-8"))
        m = re.search(r"const\s+CACHE_PREFIXES\s*=\s*\[(?P<body>[^\]]*)\]", ssrc)
        if m is None:
            bad(f"`{srel}` 里读不到 `CACHE_PREFIXES` —— 本检查会静默变成假绿，故直接失败")
        else:
            entries = _QUOTED_PATH_RE.findall(m.group("body"))
            api = [e for e in entries if "api" in e.lower()]
            if not entries:
                bad(f"`{srel}::CACHE_PREFIXES` 是空的 —— SW 连它承诺的静态资源都不会缓存")
            elif api:
                bad(
                    f"`{srel}::CACHE_PREFIXES` 里有接口路径 {api} —— "
                    "**接口响应一律不进缓存**（它与『离线答题』是两件事，属 BL-21）"
                )
            elif "/_next/static/" not in entries:
                # ⚠️ 比较串**必须带前导斜杠**：`_QUOTED_PATH_RE` 只匹配以 `/` 开头的字面量，
                #    所以 `entries` 里存的是 `"/_next/static/"`。
                #    第一版写成 `"_next/static/"`（少一个斜杠）⇒ **判据恒红**
                #    （2026-10-02 实测：同一批里"应该红"的场景先红了这条，才发现是比错了）。
                bad(f"`{srel}::CACHE_PREFIXES` 里没有 `/_next/static/` —— JS/CSS/字体不会被缓存")
            else:
                ok(f"`{srel}` 白名单 = {entries}（有静态资源、**不含接口**）")

    # ---- d) 三个静态文件必须**未登录**也能访问 ----
    rel_mw = f"{_PWA_APP}/src/middleware.ts"
    mw = REPO / rel_mw
    prefixes: list[str] | None = None
    if mw.is_file():
        # ★ 同上：先剥注释（`middleware.ts` 顶上那段注解里就列着这三个路径）
        mm = _PUBLIC_PREFIXES_RE.search(_strip_js_comments(mw.read_text(encoding="utf-8")))
        prefixes = None if mm is None else _QUOTED_PATH_RE.findall(mm.group("body"))

    # ★ 这里刻意**不用**"改成"的措辞：它验的是"两个文件里同一个东西一致"，
    #   而按坑 97 的判据，我们只声明**它必须在这里**（唯一真相来源 = middleware），
    #   不声明"和别处保持一致"。
    if prefixes is None:
        bad(f"`{rel_mw}` 里读不到 `PUBLIC_PREFIXES` —— 无法验证『未登录可达』，故直接失败")
    else:
        miss = [p for p in _PWA_MUST_BE_PUBLIC if p not in prefixes]
        if miss:
            bad(
                f"`PUBLIC_PREFIXES` 里缺 {miss} —— 未登录访客访问它们会被 302 到 `/login`："
                "SW 拿到 HTML ⇒ **注册失败**；manifest 取不到 ⇒ **装不上**；"
                "离线页被抓成**登录页** ⇒ 断网看到登录表单。"
                "★ 这条**在已登录状态下完全看不出来**（有 cookie 就放行）"
            )
        else:
            ok(f"`PUBLIC_PREFIXES` 含 {_PWA_MUST_BE_PUBLIC}（未登录也能取到）")


#: ★ E2E 场景清单同时出现在**三处**（见不变量 11 抬头）—— 各抓一份来对账。
_E2E_CHOICES_RE = re.compile(r"choices=\[([^\]]*)\]")
_E2E_NEED_Q_RE = re.compile(r"SCENARIOS_NEEDING_QUESTIONS\s*=\s*frozenset\(([^)]*)\)")


def _e2e_names(body: str) -> set[str]:
    """从 `"a", "b"` / `"a": drive_a,` 这类文本里取场景名。

    ★ **不把引号写进正则**：源码里单/双引号都用过，而 `["\']` 那种字符类
      写在 Python 源码里会**提前结束字符串**（这一版第一稿就栽在这）。
      做法 = 用 `chr(34)` / `chr(39)` 当分隔符切分，正则只认"纯小写字母数字"。
    """
    out: set[str] = set()
    for ch in (chr(34), chr(39)):
        for chunk in body.split(ch):
            t = chunk.strip().strip(",").strip(": ").strip()
            if re.fullmatch(r"[a-z0-9]+", t):
                out.add(t)
    return out


def _e2e_registry(text: str) -> set[str]:
    """从 `drivers = { ... }` 里取场景名（那是**第三处**清单）。"""
    i = text.index("drivers = {")
    j = text.index("}", i)
    return _e2e_names(text[i:j])


def check_copy_manifest() -> None:
    print("[12] 离线 PWA 的复制品清单 + 三条结构契约（不变量 12）")

    import hashlib

    offline = "apps/pwa"
    manifest_rel = f"{offline}/COPIED-FROM-WEB.md"
    path = REPO / manifest_rel
    if not path.is_file():
        bad(f"缺 `{manifest_rel}` —— 没有它，`{offline}` 的复制品没有任何覆盖检查")
        return

    blocks = re.findall(r"```json\s*\n(.*?)```", path.read_text(encoding="utf-8"), flags=re.S)
    if not blocks:
        bad(f"`{manifest_rel}` 里没有 ```json 代码块 —— 门禁读不到清单，会静默变成假绿")
        return
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as e:
        bad(f"`{manifest_rel}` 的 json 块解析失败：{e}")
        return

    same = data.get("copied_identical", [])
    fork = data.get("copied_fork", [])
    only = data.get("pwa_only", [])

    # ---- ② 标 same 的必须**逐字节相同** ----
    drifted: list[str] = []
    missing: list[str] = []
    for e in same:
        a = REPO / "apps" / "web" / e["from"]
        b = REPO / offline / e["to"]
        if not a.is_file():
            missing.append(f"{e['from']}（C 端源文件已不在）")
            continue
        if not b.is_file():
            missing.append(f"{e['to']}（PWA 侧文件已不在）")
            continue
        if hashlib.sha256(a.read_bytes()).digest() != hashlib.sha256(b.read_bytes()).digest():
            drifted.append(f"{e['to']}")
    if missing:
        bad(f"清单里登记为逐字节相同的文件不见了：{missing}")
    elif drifted:
        bad(
            f"这些文件登记为**逐字节相同**、实际已经分叉：{drifted}。"
            "⇒ 要么把 C 端的改动同步过去，要么把它改登记为 fork 并写清可判定的理由"
        )
    else:
        ok(f"逐字节相同的 {len(same)} 个文件**确实一致**（sha256 实算，不信清单里的值）")

    # ---- ③ fork 的理由必须可判定 ----
    forbidden = (
        "有意简化",
        "暂时这样",
        "暂时先",
        "后续再改",
        "以后再改",
        "先复制过来",
        "先这样",
        "待改",
    )
    bad_reason: list[str] = []
    for e in fork:
        reason = str(e.get("reason", "")).strip()
        check = str(e.get("check", "")).strip()
        if not reason or not check:
            bad_reason.append(f"{e['to']}（缺理由或缺判据）")
            continue
        hit = [w for w in forbidden if w in reason]
        if hit:
            bad_reason.append(f"{e['to']}（理由里有 {hit} —— 那种理由能套在任何一个文件上）")
    for e in only:
        if not str(e.get("why", "")).strip():
            bad_reason.append(f"{e['to']}（pwa_only 也要写理由）")
    if bad_reason:
        bad(f"分叉理由不合格：{bad_reason}")
    else:
        ok(f"{len(fork)} 个分叉的理由都指名了具体差异，且都给了可验判据；{len(only)} 个 pwa_only 也有理由")

    # ---- ① 覆盖：两个 app 都有的文件，**一条都不许漏登记** ----
    #: ★ 与 `tools/pwa/gen-copied-manifest.py` 的 SKIP **必须同集**（锁文件由 npm 生成、
    #:   各 app 本来就不同，逐字节比它没有意义）。这**不是**"两处必须一致"的约定 ——
    #:   它是同一份判断第二次被用到，所以两处都写清理由；谁改都得两边看。
    skip = {"next-env.d.ts", "tsconfig.tsbuildinfo", "package-lock.json", "COPIED-FROM-WEB.md"}
    registered = {e["to"] for e in same} | {e["to"] for e in fork}

    def walk(root: str) -> set[str]:
        out: set[str] = set()
        base = REPO / root
        if not base.is_dir():
            return out
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(base).as_posix()
            if rel in skip or rel.startswith(".next") or "node_modules" in rel:
                continue
            out.add(rel)
        return out

    both = (walk("apps/web") & walk(offline)) - {e["from"] for e in same} - registered
    if both:
        bad(
            f"这些文件在 `apps/web` 与 `{offline}` 下**同名共存**，却没登记在清单里：{sorted(both)}。"
            "★ 这正是「复制了但忘了登记」的形状 —— 它会让『C 端改了、PWA 没改』永远不被发现"
        )
    else:
        ok("两个 app 共有的文件**全部已登记**（没有「复制了没登记」的漏网）")

    # ---- a) api.ts 不许出现 fetch( ----
    api_rel = f"{offline}/src/lib/api.ts"
    api_src = REPO / api_rel
    if not api_src.is_file():
        bad(f"缺 `{api_rel}` —— 离线端的数据层，无处可查")
    else:
        # ★ 先剥注释：本文件抬头就整段解释着"为什么不用 fetch"（不剥就是假绿）
        stripped = _strip_js_comments(api_src.read_text(encoding="utf-8"))
        if "fetch(" in stripped:
            bad(
                f"`{api_rel}` 里去掉了注释仍能找到 `fetch(` —— "
                "离线端的**唯一**数据源应当是 IndexedDB（方案 §2.2）"
            )
        else:
            ok(f"`{api_rel}` 里没有 `fetch(` —— 数据源确实换成了 IndexedDB")

    # ---- b) sw.js 的预缓存清单不许含数据文件 ----
    sw_rel = f"{offline}/public/sw.js"
    sw_src = REPO / sw_rel
    if not sw_src.is_file():
        bad(f"缺 `{sw_rel}`")
    else:
        s = _strip_js_comments(sw_src.read_text(encoding="utf-8"))
        entries = re.findall(r"""["'`](/[^"'`]*)["'`]""", s)
        data_like = [e for e in entries if re.search(r"bank|questions|\.sql", e, flags=re.I)]
        if data_like:
            bad(
                f"`{sw_rel}` 里出现了数据文件 {data_like} —— SW 与 IndexedDB 会**各存一份**，"
                "换题库后两份不一致（症状：导入成功但题目还是旧的，**不报错**）"
            )
        else:
            ok(f"`{sw_rel}` 只缓存应用壳（{len(entries)} 条路径，无任何数据文件）")

    # ---- c) 判分不许自带字面量规则 ----
    grade_rel = f"{offline}/src/lib/grade.mjs"
    grade_src = REPO / grade_rel
    if not grade_src.is_file():
        bad(f"缺 `{grade_rel}` —— 判分的哑比较实现")
    else:
        g = _strip_js_comments(grade_src.read_text(encoding="utf-8"))
        if "partial_credit_ratio" not in g:
            bad(f"`{grade_rel}` 里读不到 `partial_credit_ratio` —— 阈值没有随包下发？")
        elif re.search(r"\b0\.[0-9]+\b", g):
            hit = re.findall(r"\b0\.[0-9]+\b", g)
            bad(
                f"`{grade_rel}` 里出现了字面量比例 {hit} —— 判分必须**从包的 meta 读**规则，"
                "不许自带（方案 §3.1：PWA 侧没有可决定的东西）"
            )
        else:
            ok(f"`{grade_rel}` 从包的 `meta.grading_rules` 读规则，没有任何字面量阈值")

    # ---- d) 清单必须**是生成的**（不是手改的）----
    #
    # ★★ 这条是变异验证抓出来的真缺口（2026-10-06）：清单里同一个事实写了**两遍**
    #    —— 人读的表格 + 机器读的 JSON 块。只比 JSON 的话，手改**表格**不会红
    #    （两侧各说各话）；而手改 JSON 又能绕过"理由必须可判定"。
    #    ⇒ 唯一干净的解法是让它是**生成的**：磁盘内容必须 == 生成脚本此刻的输出。
    check_py = REPO / "tools" / "pwa" / "gen-copied-manifest.py"
    if not check_py.is_file():
        bad(f"缺 `{check_py.relative_to(REPO).as_posix()}` —— 无法判断清单是不是手改的")
    else:
        import subprocess

        p = subprocess.run(
            [sys.executable, str(check_py), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if p.returncode == 0:
            ok("清单**是最新的**（与生成脚本逐字节一致 ⇒ 理由只有一处，手改会被抓到）")
        else:
            bad(f"{manifest_rel} 不是最新的：{p.stdout.strip() or p.stderr.strip()}")


def check_e2e_scenarios() -> None:
    print("[11] E2E 场景清单**三处必须一致**（不变量 11）—— 加场景漏一处，会静默或直接跑不起来")

    web_rel = "tools/local-verify/e2e-web.py"
    pipe_rel = "tools/local-verify/run-local-pipeline.py"
    web = (REPO / web_rel).read_text(encoding="utf-8")
    pipe = (REPO / pipe_rel).read_text(encoding="utf-8")

    m_web = _E2E_CHOICES_RE.search(web)
    m_pipe = _E2E_CHOICES_RE.search(pipe)
    m_need = _E2E_NEED_Q_RE.search(pipe)
    if m_web is None or m_pipe is None or m_need is None:
        bad("读不到场景清单（三处之一）—— 本检查会**静默变成假绿**，故直接失败")
        return
    try:
        registry = _e2e_registry(web)
    except ValueError:
        bad(f"`{web_rel}` 里找不到 `drivers = {{` —— 注册表被改名或删掉了")
        return

    want = _e2e_names(m_web.group(1))
    if not want:
        bad(f"`{web_rel}` 的 `--scenario` choices 解析出了**空集合** —— 判据形状变了")
        return

    problems: list[str] = []
    diff_pipe = _e2e_names(m_pipe.group(1)) ^ want
    if diff_pipe:
        problems.append(
            f"`{pipe_rel}` 的 `--e2e-scenario` choices 与 `{web_rel}` 不同：{sorted(diff_pipe)}"
        )
    diff_reg = registry ^ want
    if diff_reg:
        problems.append(f"`drivers` 注册表与 choices 不同：{sorted(diff_reg)}")
    extra_need = _e2e_names(m_need.group(1)) - want
    if extra_need:
        problems.append(f"`SCENARIOS_NEEDING_QUESTIONS` 里有 choices 之外的场景：{sorted(extra_need)}")

    if problems:
        bad("；".join(problems))
        print(
            "      ⇒ 加一个 E2E 场景要同时改 **3 处**（+ 可选第 4 处）：\n"
            f"        ① `{web_rel}` 的 `--scenario` choices\n"
            f"        ② `{pipe_rel}` 的 `--e2e-scenario` choices\n"
            f"        ③ `{web_rel}` 的 `drivers` 注册表\n"
            "        ④ `SCENARIOS_NEEDING_QUESTIONS`（只加「需要灌题库」的场景）\n"
            "      ★ 漏 ② 的症状：`invalid choice: '<场景>'` + **退出码 2、1 秒结束** ——\n"
            "        看上去像「走查没跑起来」，**不像配置错**（实测踩过两次：p2b1 / p2c3）。"
        )
        return

    need_n = len(_e2e_names(m_need.group(1)))
    ok(f"{len(want)} 个场景**三处一致**（{'/'.join(sorted(want))}）；其中 {need_n} 个要灌题库")


def main() -> int:
    print(
        "=== 不变量自检（`docs/24` §8 + `docs/25` 第 6 条 + 本批 7/8/9/10/11；"
        "每批收尾跑一次）==="
    )
    check_gates()
    check_coverage_ratchet()
    check_ephemeral_db()
    check_admin_isolation()
    check_docs()
    check_delete_guard()
    check_write_paths_commit()
    check_answer_single_source()
    check_public_route_entrypoints()
    check_pwa_contract()
    check_copy_manifest()
    check_e2e_scenarios()
    passed = sum(1 for good, _ in _results if good)
    failed = [label for good, label in _results if not good]
    print()
    print("--- ⚠️ 人工检查项（脚本**查不了**，别把它当成已检查）---")
    print("  · `docs/23` 的清单每批收尾扫一遍（新增的'声称'要补进去）")
    print("  · `docs/` 里凡'声称'的话，是否都能被**一条命令**验证")
    print()
    if failed:
        print(f"==> {passed} 项通过 / **{len(failed)} 项失败**")
        for label in failed:
            print(f"    ✗ {label}")
        return 1
    print(f"==> {passed} 项通过 / 0 项失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
