"""不变量清单 —— **能机械检查的那部分**（`docs/24` §8 的 5 条 + `docs/25` 的第 6 条）。

第 6 条（2026-09-29 加）：**宿主删除保护** —— 会被拦的删除一律 `rename` 到 `.trash/`；
未登记的"直接删"必须变红。判据来源 = 项目约定 **R**，方案 `docs/25-宿主删除保护.md`。

为什么要有它（用户 2026-09-27）：
    不变量**不是"开工前确认一次"**，而是"**每次 push 后跑一次**" ——
    P0→P3 每推一次都可能把它们破掉。写成可执行文件才能每批收尾自动跑。

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
import re
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


def main() -> int:
    print("=== 不变量自检（`docs/24` §8 + `docs/25` 第 6 条 + 本批 7/8；每批收尾跑一次）===")
    check_gates()
    check_coverage_ratchet()
    check_ephemeral_db()
    check_admin_isolation()
    check_docs()
    check_delete_guard()
    check_write_paths_commit()
    check_answer_single_source()
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
