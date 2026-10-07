#!/usr/bin/env python
"""个人 PWA 的 E2E 走查器（B-2 步骤 3b）。**目前只有 `setup` 一个场景。**

## 与 `e2e-web.py` 的关系
同一个底座（`tools/local-verify/cdp_browser.py`、移动视口 375×667），**互不影响**：
`e2e-web.py` 走 C 端"登录 → 刷题"，本脚本走 PWA"导入题库 → 答题"。
★ 底座改动（例如 3a 加的 `set_file_input`）两者**都要回归**，清单由 grep 决定、不靠记忆。

## 就绪判据 —— "这是不是我起的那个"
⚠️ **"端口开着"不是就绪**：它只说明"有个东西在监听"，可能是上一次没停干净的孤儿进程、
也可能是别的 app。所以本脚本的就绪判据是**三件一起**：

  ① 我起的那个进程**还活着**（`proc.poll() is None`）—— 它挂了就没资格说"就绪"；
  ② 端口上返回的页面里**认得到本应用的标记**（`APP_MARK`，取自 `layout.tsx` 的 title）
     —— 这一条才回答"是不是我的"；
  ③ 它**没有偷偷换端口**（`next dev` 发现端口被占会自己 +1 换个端口跑，
     此时我去敲原端口拿到的是**别人的** 200 —— 那是假绿）。

## 判据（`setup` 场景，缺一条就红）
1. ★ **可证伪锚**：先断言首页 `[data-bank="none"]`（"还没有题库"），
   且 `[data-bank="ready"]` **不在**。没有这个锚，"导入后有 200 题"与
   "本来就有 200 题"长得一模一样。
2. **走真 UI 导入**：`set_file_input` 选小包 → 等 `[data-setup-done="1"]`。
3. ★★ **对账**：直接读 **IndexedDB** 的 `questions` / `subjects`，
   与**小包文件里的 meta** 逐项比 —— 题量 / 选项数 / `by_subject` / `by_type`。
   ★ 为什么必须读库而不看页面文案：页面文案是**产物**，库里的行才是**事实**；
     而且 `by_type` **应用自己不对账**（`api.ts::reconcile` 只比科目）—— 这里是唯一的网。
4. **刷新后仍在**（IndexedDB 持久化，不是内存态）。
5. **换题库的二次确认：取消 ⇒ 数据不动**，并**对照**"同一个包"走的是另一条分支
   （只验一头的话，"永远弹 replace"的实现也能过）。

## 三个数（走查固定要报）
`--max-questions` 题的导入耗时 / 整个场景耗时 / 有没有新的技术栈坑（写在末尾的"现场"段）。

## 边界（本步**不做**）
- 其余 5 个场景（`wrong` / `marks` / `fav` / `notes` / `loop`）—— 先验证底座；
- CI 集成与 `run-local-pipeline.py --e2e-pwa`、不变量 11 的双走查器推广 —— 另一步；
- 不碰 `apps/web`。

## 跑法（**必须用 venv 的 python**：小包生成要 import 后端常量）
    PYTHONUTF8=1 <venv>/python tools/pwa/e2e-pwa.py
    PYTHONUTF8=1 <venv>/python tools/pwa/e2e-pwa.py --url http://127.0.0.1:3002   # 复用已起的服务
    PYTHONUTF8=1 <venv>/python tools/pwa/e2e-pwa.py --bank data/seed/pwa-bank-small.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PWA_DIR = REPO / "apps" / "pwa"
EXPORT_BANK = REPO / "tools" / "pwa" / "export-bank.py"
CDP_BROWSER = REPO / "tools" / "local-verify" / "cdp_browser.py"
E2E_WEB = REPO / "tools" / "local-verify" / "e2e-web.py"

#: 认"这是我起的那个应用"的标记 —— 取自 `apps/pwa/src/app/layout.tsx` 的 `title`。
#: ★ 它是**应用身份**，不是"端口活着"。改标题时这里会红，从而逼你同步（判据与真相绑在一处）。
APP_MARK = "一建通 · 离线版"


class Failure(AssertionError):
    pass


def say(msg: str) -> None:
    print(msg, flush=True)


def need(cond: object, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _load(name: str, path: Path) -> Any:
    """按**路径**导入（这些文件名带连字符，`import` 语句用不了）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    """自己占一个空闲端口再放掉 —— 比"默认 3002 然后祈祷"少一类**假绿**。

    ⚠️ 仍有 TOCTOU 窗口，所以它只是**降低**碰撞概率；`start_pwa` 的三条判据才是主防线。
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str, timeout: float = 10.0) -> tuple[int, str]:
    """返回 (状态码, 正文)。**不跟随重定向**（PWA 没有重定向，跟了会掩盖问题）。"""
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 —— 只连本机
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def find_node() -> str:
    """找 node。★ **不写死本机路径**：从 `Path.home()` 起手，版本目录用 glob 取最新的。

    写死绝对路径的代价（2026-10-07 同族清理）：换台机器 / 在 CI 上就是"找不到"，
    而那个症状看起来像**工具坏了**，不像**配置写死了**（同族：`gen-copied-manifest.py`
    写死 REPO 把后端 CI 打红了一个多星期）。
    """
    managed = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    for cand in sorted(managed.glob("*/node.exe"), reverse=True):
        return str(cand)
    raise SystemExit("✗ 找不到 node.exe —— 装 node，或放进 ~/.workbuddy/binaries/node/versions/")


# --------------------------------------------------------------------- 小包

def make_small_pack(out: Path, max_q: int) -> dict[str, Any]:
    """跑 `export-bank.py --max-questions N` 生成走查用的小包，返回它（含 `meta`）。

    ★ 必须用 **venv 的 python**（`sys.executable`）：`export-bank.py` 要 import 后端常量
      （评分规则），拿系统 python 跑会 `ModuleNotFoundError`。
    ★ 小包是**分层抽样**的（科目×题型×难度 轮转，缺一即失败）—— 所以它对账得动
      整个题库的维度，不是"前 200 题"。
    """
    say(f"$ export-bank.py --max-questions {max_q} --out {out.name}")
    r = subprocess.run(
        [sys.executable, str(EXPORT_BANK), "--max-questions", str(max_q), "--out", str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
    )
    if r.returncode != 0:
        raise Failure(
            f"生成小包失败（rc={r.returncode}）—— 用 venv 的 python 跑本脚本了吗？\n"
            f"{r.stdout[-1000:]}\n{r.stderr[-500:]}"
        )
    for line in (r.stdout or "").splitlines():
        if "分层抽样" in line:
            say(f"  {line.strip()}")
    return json.loads(out.read_text(encoding="utf-8"))


def make_replaced_pack(src: dict[str, Any], out: Path) -> dict[str, Any]:
    """同一份包，**只把指纹改掉** —— 用来走到「换题库」的破坏性确认分支。

    ★ 为什么改指纹就是"换题库"的**最小充分**刺激：页面正是**用指纹**区分
      "同一个包（reimport）"与"换题库（replace）"（见 `setup/page.tsx::onPick`）。
      所以别的字段一个都不用动 —— 动了反而引入无关变量。
    ★ 这一份**不会被真的导入**（我们只走"取消"那条路），所以它不需要数据自洽。
    """
    pkg = json.loads(json.dumps(src))
    pkg["meta"]["bank_version"] = "feedface" * 8
    out.write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
    return pkg


# --------------------------------------------------------------------- 起服务

def start_pwa(port: int, node: str, timeout: int) -> tuple[subprocess.Popen, Path]:
    """起 `next dev`，**就绪判据见模块抬头（三条一起）**。返回 (进程, 日志路径)。"""
    next_bin = PWA_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    need(next_bin.is_file(), f"找不到 {next_bin} —— 先在 apps/pwa 跑 `npm ci`")

    # ★ `.next` 必须先**改名挪走**：`next dev` 启动时会批量删 `.next/static/chunks/…`，
    #   而宿主的删除保护对"一次 ≥50 项"直接拒绝 ⇒ 服务在编译前就挂（e2e-web.py 抬头有详述）。
    e2e = _load("e2e_web", E2E_WEB)
    e2e.isolate_next_dir(PWA_DIR)

    log = Path(tempfile.gettempdir()) / "e2e-pwa.log"
    fh = log.open("w", encoding="utf-8")  # noqa: SIM115 —— 交给子进程持有，收尾时关闭
    env = dict(os.environ)
    env["NODE_ENV"] = "development"
    proc = subprocess.Popen(
        [node, str(next_bin), "dev", "-p", str(port)],
        cwd=str(PWA_DIR),
        env=env,
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    say(f"$ next dev -p {port}（日志 {log}）")

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    last = "（还没连上）"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise Failure(f"next dev 提前退出（rc={proc.returncode}）—— 见 {log}")
        try:
            status, body = _http_get(base + "/", timeout=10)
        except OSError as e:  # dev 首编译期间连接会被拒：正常，继续等
            last = f"{type(e).__name__}"
            time.sleep(1)
            continue
        if status == 200 and APP_MARK in body:
            # ③ 没有偷偷换端口：next 在目标端口被占时会自己 +1
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            need(
                "is in use, trying" not in text,
                f"`next dev` 发现 :{port} 被占、**换到别的端口去了** —— "
                f"我刚拿到的那个 200 不是我的进程给的。见 {log}",
            )
            say(f"next dev 就绪（{base}，端口上认到「{APP_MARK}」）")
            return proc, log
        last = f"HTTP {status}（没认到应用标记）"
        time.sleep(1)
    raise Failure(f"next dev 未在 {timeout}s 内就绪（最后一次：{last}）—— 见 {log}")


# --------------------------------------------------------------------- 浏览器层

#: 直接读 **IndexedDB**（不经页面文案）。DB 名与 store 名的真相来源是
#: `apps/pwa/src/lib/db.ts`（`DB_NAME` / `STORES`）—— 名字写错会**当场抛**
#: （`transaction()` 找不到 store），不会静默返回空。
_IDB_JS = """(async () => {
  const open = () => new Promise((res, rej) => {
    const r = indexedDB.open('yijian-pwa');
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error || new Error('open failed'));
  });
  const all = (db, store) => new Promise((res, rej) => {
    const r = db.transaction(store, 'readonly').objectStore(store).getAll();
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error || new Error('getAll failed: ' + store));
  });
  const db = await open();
  const qs = await all(db, 'questions');
  const subs = await all(db, 'subjects');
  const codeById = {};
  for (const s of subs) codeById[s.id] = s.code;
  const by_subject = {}, by_type = {};
  let options = 0, noCode = 0;
  for (const q of qs) {
    const code = codeById[q.subject_id];
    if (code === undefined) noCode += 1;
    by_subject[code] = (by_subject[code] || 0) + 1;
    by_type[q.type] = (by_type[q.type] || 0) + 1;
    options += (q.options || []).length;
  }
  db.close();
  return { total: qs.length, subjects: subs.length, options, by_subject, by_type, noCode };
})()"""


async def _idb(b: Any) -> dict[str, Any]:
    got = await b.eval(_IDB_JS)
    need(isinstance(got, dict), f"读 IndexedDB 没拿到对象：{got!r}")
    return got


def _diff(want: dict[str, Any], got: dict[str, Any], label: str) -> list[str]:
    """两个 dict 的**双向**差异（只比 want 的键的话，"多出来的"会漏掉）。"""
    out = []
    for k in sorted(set(want) | set(got)):
        if want.get(k) != got.get(k):
            out.append(f"{label}[{k}] 包={want.get(k)} 库={got.get(k)}")
    return out


async def run_setup(base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int) -> dict[str, float]:
    """`setup` 场景。返回耗时数据。"""
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()

    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        # ---- ① 可证伪锚：先证明"本来是空的" ----
        # 这一步成立的前提：`cdp_browser` 每次都用**全新的临时 profile** ⇒ IndexedDB 是空的。
        await b.goto_ready(base + "/")
        await b.wait_for(
            """document.querySelector('[data-bank="none"]')""", timeout=40, label="首页「还没有题库」"
        )
        need(
            not await b.eval("""!!document.querySelector('[data-bank="ready"]')"""),
            "空态下不该同时出现 `[data-bank=\"ready\"]` —— 页面结构变了？",
        )
        say("  ✅ ① 可证伪锚：首页显示「还没有题库」（且 ready 区块不在）")

        # ---- ② 走真 UI 导入 ----
        await b.click('[data-entry="setup"]')
        await b.wait_for("location.pathname === '/setup'", timeout=40, label="进入 /setup")
        await b.wait_for(
            """document.querySelector('[data-bank-state="none"]')""", timeout=40, label="/setup 空态"
        )
        t_imp0 = time.time()
        await b.set_file_input('[data-setup-file="1"]', small)
        await b.wait_for(
            """document.querySelector('[data-setup-done="1"]')""",
            timeout=timeout,
            label=f"导入 {small_meta['totals']['questions']} 题完成",
        )
        import_sec = time.time() - t_imp0
        err = await b.eval("""(() => { const e = document.querySelector('[data-setup-error="1"]');
                                      return e ? e.innerText : ''; })()""")
        need(not err, f"导入期间页面报了错：{err}")
        say(f"  ✅ ② 走真 UI 导入完成（{import_sec:.1f}s）")

        # ---- ③ 对账：库里的行 vs 小包文件里的 meta ----
        snap = await _idb(b)
        meta = small_meta
        totals = meta["totals"]
        problems: list[str] = []
        if snap["total"] != totals["questions"]:
            problems.append(f"题量 库={snap['total']} 包={totals['questions']}")
        if snap["options"] != totals["options"]:
            problems.append(f"选项 库={snap['options']} 包={totals['options']}")
        if snap["noCode"]:
            problems.append(f"有 {snap['noCode']} 道题的 subject_id 找不到对应科目（科目表缺行？）")
        problems += _diff(meta["by_subject"], snap["by_subject"], "by_subject")
        problems += _diff(meta["by_type"], snap["by_type"], "by_type")
        need(
            not problems,
            "导入对账没通过（**库里的行**与**小包 meta** 逐项比）：\n  - " + "\n  - ".join(problems),
        )
        say(
            f"  ✅ ③ 对账通过：{snap['total']} 题 / {snap['options']} 选项 / "
            f"{len(snap['by_subject'])} 科目 / {len(snap['by_type'])} 题型 —— **逐项等于小包 meta**"
        )
        say(f"       （by_type={snap['by_type']} —— 这一项**应用自己不对账**，只有本脚本在看）")

        # 页面上那三个数也得对（文案是产物；对不上说明展示层漏了）
        done_text = await b.eval("""document.querySelector('[data-setup-done="1"]').innerText""")
        for piece in (
            f"{totals['questions']} 题",
            f"{totals['options']} 个选项",
            f"指纹 {meta['bank_version'][:8]}",
        ):
            need(piece in done_text, f"导入完成区块里没有「{piece}」（实际：{done_text!r}）")

        # ---- ④ 刷新后仍在 ----
        await b.goto_ready(base + "/")
        await b.wait_for(
            """document.querySelector('[data-bank="ready"]')""", timeout=40, label="刷新后首页 ready"
        )
        home_text = await b.eval("""document.querySelector('[data-bank="ready"]').innerText""")
        need(
            f"{totals['questions']} 题" in home_text,
            f"刷新后首页题量不对（期望 {totals['questions']} 题）：{home_text!r}",
        )
        say(f"  ✅ ④ 刷新后仍在（首页显示 {totals['questions']} 题 —— IndexedDB 持久化了）")

        # ---- ⑤ 换题库的二次确认：取消不删数据（**成对**验两条分支）----
        await b.goto_ready(base + "/setup")
        await b.wait_for(
            """document.querySelector('[data-bank-state="ready"]')""", timeout=40, label="/setup 已有题库"
        )
        # (a) 不同指纹 ⇒ 破坏性的 replace 分支
        await b.set_file_input('[data-setup-file="1"]', other)
        await b.wait_for(
            """document.querySelector('[data-setup-confirm="replace"]')""",
            timeout=30,
            label="换题库确认框",
        )
        await b.click('[data-confirm="cancel"]')
        await b.wait_for(
            """!document.querySelector('[data-setup-confirm="replace"]')""", timeout=20, label="确认框关闭"
        )
        after = await _idb(b)
        need(
            after["total"] == snap["total"] and after["options"] == snap["options"],
            f"**取消之后数据变了**：题量 {snap['total']}→{after['total']}、"
            f"选项 {snap['options']}→{after['options']}（确认框上的取消必须是零副作用）",
        )
        say(f"  ✅ ⑤a 换题库确认框出现 → 点「取消」⇒ 数据一条没少（{after['total']} 题仍在）")

        # (b) **同一个包** ⇒ 走的是另一条分支（reimport）。只验 (a) 的话，
        #     "永远弹 replace"的实现也能过 —— 那正是这半个断言要拦的。
        await b.set_file_input('[data-setup-file="1"]', small)
        await b.wait_for(
            """document.querySelector('[data-setup-confirm="reimport"]')""",
            timeout=30,
            label="同包确认框（reimport）",
        )
        need(
            not await b.eval("""!!document.querySelector('[data-setup-confirm="replace"]')"""),
            "同一个包却弹出了**破坏性**的 replace 确认框 —— 指纹判据失效了",
        )
        await b.click('[data-confirm="cancel"]')
        after2 = await _idb(b)
        need(after2["total"] == snap["total"], "再取消一次，数据又变了")
        say("  ✅ ⑤b 对照：同一个包走的是 reimport 分支（不是 replace）⇒ 指纹判据真的在承重")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


#: ★ 场景注册表（不变量 11 的**第三处**清单）—— 名字 → 协程。
#: 值统一签名 `(base, bank, bank_meta, other, timeout) -> dict[str, float]`（返回各段耗时），
#: 于是**加场景不用改调度代码**（用户 2026-10-07："先推广不变量 11，再加 5 个场景"）。
#: ★ 名字必须是纯小写字母数字 —— 门禁的 `_e2e_names()` 只认那个形状。
drivers = {
    "setup": run_setup,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="e2e-pwa", description=__doc__.split("\n")[0])
    ap.add_argument(
        "--scenario",
        default="setup",
        # ⚠️ 这是场景白名单的**第一处**（另两处：`run-local-pipeline.py` 的
        #    `--e2e-pwa-scenario`、下面那个 `drivers` 注册表）。
        #    ★ 别靠"记得改"：**不变量 11** 会把三处对账，不一致就红
        #      （`tools/local-verify/check-invariants.py::check_e2e_scenarios`
        #       现在**遍历走查器清单**，e2e-web 与本脚本各自三处）。
        choices=["setup"],
        help="走查场景：setup（导入题库 + 对账 + 换题库二次确认）",
    )
    ap.add_argument("--url", help="已在跑的前端地址（给了就不自己起服务）")
    ap.add_argument("--port", type=int, default=0, help="0 = 自己挑一个空闲端口")
    ap.add_argument("--bank", help="直接用这份小包（省去每次生成）")
    ap.add_argument("--max-questions", type=int, default=200, help="小包题量（默认 200）")
    ap.add_argument("--boot-timeout", type=int, default=120, help="next dev 冷启动上限（秒）")
    ap.add_argument("--import-timeout", type=int, default=180, help="导入 + 对账的上限（秒）")
    args = ap.parse_args(argv)

    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="e2e-pwa-") as td:
            tdp = Path(td)
            if args.bank:
                small = Path(args.bank).resolve()
                need(small.is_file(), f"--bank 指向的文件不存在：{small}")
                small_meta = json.loads(small.read_text(encoding="utf-8"))["meta"]
                say(f"用现成小包 {small}（{small_meta['totals']['questions']} 题）")
            else:
                small = tdp / "pwa-bank-small.json"
                small_meta = make_small_pack(small, args.max_questions)["meta"]
            other = tdp / "pwa-bank-other.json"
            make_replaced_pack(json.loads(small.read_text(encoding="utf-8")), other)

            if args.url:
                base = args.url.rstrip("/")
                boot_sec = 0.0
                say(f"复用已在跑的服务：{base}")
            else:
                port = args.port or _free_port()
                base = f"http://127.0.0.1:{port}"
                t_boot = time.time()
                proc, _ = start_pwa(port, find_node(), args.boot_timeout)
                boot_sec = time.time() - t_boot

            say(f"=== {args.scenario} 场景 ===")
            perf = asyncio.run(
                drivers[args.scenario](base, small, small_meta, other, args.import_timeout)
            )

        say("")
        say(f"==> ✅ {args.scenario} 场景通过")
        say("")
        say("## 耗时")
        if "import_sec" in perf:
            say(f"  · 小包 {small_meta['totals']['questions']} 题导入：{perf['import_sec']:.1f}s")
        say(f"  · {args.scenario} 场景总耗时：{perf['scenario_sec']:.1f}s")
        say(f"  · next dev 冷启动：{boot_sec:.1f}s（不含在场景耗时里）")
        return 0
    except Failure as e:
        say("")
        say(f"==> ❌ 失败：{e}")
        return 1
    finally:
        if proc is not None:
            # 起/停必须在**同一次调用**里（前台结束会回收进程树）。
            try:
                proc.terminate()
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            say("[e2e-pwa] dev server 已停")


if __name__ == "__main__":
    sys.exit(main())
