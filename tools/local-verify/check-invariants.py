"""`docs/24` §8 的 5 条不变量 —— **能机械检查的那部分**。

为什么要有它（用户 2026-09-27）：
    不变量**不是"开工前确认一次"**，而是"**每次 push 后跑一次**" ——
    P0→P3 每推一次都可能把它们破掉。写成可执行文件才能每批收尾自动跑。

判据来源：`docs/24-C端首批-范围冻结.md` §8。

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

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: ★ 覆盖率门槛的**棘轮基线**。`fail_under` 只许 ≥ 它。
#: 抬门槛时必须**同时改这里** —— 这个"两处一起改"的动作本身就是棘轮的痕迹：
#: 想偷偷把门槛调低，就得先改一个名叫"基线"的常量（而不是改一个不起眼的数字）。
FAIL_UNDER_BASELINE = 94.40

#: 门禁清单（13 道）。名字逐字取自 `.github/workflows/ci.yml` 的 `name:`。
#: ⚠️ 历史口径说的"7 道"是 2026-09 早期的：后来加了诊断自检 / 反向检查 / 共享包单测 /
#: `format:check:shared` / `build` / **不变量自检** ⇒ 现在是 13 道。
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
]

_results: list[tuple[bool, str]] = []


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
    print("[1] 门禁完整性（13 道，全拦截式）—— 不变量 1")
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
        ok("13 道全部是**拦截式**（没有任何 continue-on-error）")


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
                if "/admin/" in ln and not ln.lstrip().startswith("//"):
                    offenders.append(f"{f.relative_to(REPO)}:{i}")
        if offenders:
            bad(f"C 端代码里出现 `/admin/` 调用：{offenders[:5]}")
        else:
            ok("`apps/web/src` 里没有 `/admin/` 调用")
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


def main() -> int:
    print("=== 不变量自检（`docs/24` §8；用户 2026-09-27：每批收尾跑一次）===")
    check_gates()
    check_coverage_ratchet()
    check_ephemeral_db()
    check_admin_isolation()
    check_docs()
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
