#!/usr/bin/env python
"""个人 PWA 的 E2E 走查器（B-2 步骤 3c）。**场景清单见 `drivers` 注册表**（别在这里列一遍 —— 会过期）。

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
   ★★ 3b **库里的 id 必须全是字符串**（不变量 14 的**行为判据**）：静态门禁只能证明
     "边界代码走了 `asId`"，证明不了"写进库的**真的**归一了"。这一条是实测。
4. **刷新后仍在**（IndexedDB 持久化，不是内存态）。
5. **换题库的二次确认：取消 ⇒ 数据不动**，并**对照**"同一个包"走的是另一条分支
   （只验一头的话，"永远弹 replace"的实现也能过）。

## 三个数（走查固定要报）
`--max-questions` 题的导入耗时 / 整个场景耗时 / 有没有新的技术栈坑（写在末尾的"现场"段）。

## 边界（本步**不做**）
- CI 集成（走查**不进 CI**，与 C 端一致）；
- 场景清单由 `drivers` 注册表 + `--scenario` choices + `run-local-pipeline.py` 的第二份 choices
  **三处**共同定义 —— 漏一处会被**不变量 11** 当场抓住。
  ★ 走查器会改 `apps/web` 吗？**不会** —— 但 `apps/web` 与 `apps/pwa` 共用的页面
    （例如答题页加 `data-option`）是**两边一起改、保持 `same`** 的（见 `COPIED-FROM-WEB.md`）。

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
  const chs = await all(db, 'chapters');
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
  // ★★ 库里的 id **必须全是字符串**（不变量 14 的**行为判据**）。
  //   静态门禁只能证明"边界代码走了 asId"；只有这里能证明**写进库的东西真的归一了**。
  //   判据形状与 `api.ts::normalizeIds` 一致：键名是 `id` 或以 `_id` 结尾。
  const badIds = [];
  const scan = (store, rows) => {
    for (const r of rows) {
      for (const [k, v] of Object.entries(r)) {
        if (k !== 'id' && !k.endsWith('_id')) continue;
        if (v !== null && typeof v !== 'string') badIds.push(store + '.' + k + ' 是 ' + typeof v);
      }
    }
  };
  scan('subjects', subs); scan('chapters', chs); scan('questions', qs);
  db.close();
  return { total: qs.length, subjects: subs.length, options, by_subject, by_type, noCode,
           badIds: badIds.slice(0, 6), badIdCount: badIds.length };
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


async def _try_wait(b: Any, expr: str, timeout: float) -> bool:
    """探测式等待：超时**返回 False**（`wait_for` 抛的是 `TimeoutError`），不中断场景。

    ★ 用于"这条分支**本来就可能不成立**"的场合（例：这门科目的章节恰好都 0 题）。
      用它而不是 try/except 包一整段，是为了让"没等到"这件事**只影响这一个判断**。
    """
    try:
        await b.wait_for(expr, timeout=timeout, label="(probe)")
        return True
    except TimeoutError:
        return False


async def _import_bank_from_setup(
    b: Any, base: str, small: Path, small_meta: dict[str, Any], timeout: int
) -> float:
    """从首页走**真 UI** 导入小包（前提：当前**没有**题库）。返回导入耗时（秒）。

    ★ 只写一处：**每个场景都要题库**。在 6 个 driver 里各抄一遍的话，
      导入 UI 一改就有 6 处要跟着改（「同一个事实两种写法」那一族）。
    """
    await b.click('[data-entry="setup"]')
    await b.wait_for("location.pathname === '/setup'", timeout=40, label="进入 /setup")
    await b.wait_for(
        """document.querySelector('[data-bank-state="none"]')""", timeout=40, label="/setup 空态"
    )
    t0 = time.time()
    await b.set_file_input('[data-setup-file="1"]', small)
    await b.wait_for(
        """document.querySelector('[data-setup-done="1"]')""",
        timeout=timeout,
        label=f"导入 {small_meta['totals']['questions']} 题完成",
    )
    sec = time.time() - t0
    err = await b.eval(
        """(() => { const e = document.querySelector('[data-setup-error="1"]');
                   return e ? e.innerText : ''; })()"""
    )
    need(not err, f"导入期间页面报了错：{err}")
    return sec


async def _ensure_bank(
    b: Any, base: str, small: Path, small_meta: dict[str, Any], timeout: int
) -> float:
    """场景**共用前置**：库里有题就跳过，否则走真 UI 导入。返回导入耗时（跳过 ⇒ 0.0）。

    ★ `cdp_browser` 每次都用**全新临时 profile** ⇒ 正常都走导入那一路；
      "已有就跳过"是为了 `--url <已在跑的服务>` 复用服务时**不白导一遍**（那会把耗时数搞脏）。
    """
    await b.goto_ready(base + "/")
    if await b.eval("""!!document.querySelector('[data-bank="ready"]')"""):
        say("  （复用已有题库 —— 跳过导入）")
        return 0.0
    return await _import_bank_from_setup(b, base, small, small_meta, timeout)


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

        # ---- ② 走真 UI 导入（**与其它场景共用同一段前置**，见 `_import_bank_from_setup`）----
        import_sec = await _import_bank_from_setup(b, base, small, small_meta, timeout)
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
        # ★★ ③b：库里的 id 必须是**字符串**（不变量 14 的**行为判据**）。
        #   静态门禁只能证明"边界代码走了 `asId`"，证明不了"写进库的**真的**归一了"。
        need(
            snap["badIdCount"] == 0,
            "库里有**非字符串**的 id —— 跨边界的 id 必须在导入时归一"
            "（「同一个字段两种写法」那一族；症状是「某个列表是空的」，**不报错**）：\n  - "
            + "\n  - ".join(snap["badIds"]),
        )
        say("  ✅ ③b 库里的 id **全是字符串**（归一在导入边界真的生效了 —— 实测，不是声称）")
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


# --------------------------------------------------------------- 场景：marks

#: 练习页的两个列表**没有 `data-*`**（科目名 / 章节名来自数据，不是硬编码 UI 文案），
#: 所以按**结构**定位：`main` 里的第 1 / 第 2 个 `<section>`。
#: `:not([disabled])` 顺带把"0 题的章节"排除 —— 页面正是用 `disabled` 表达那件事。
_SUBJ = "main section:nth-of-type(1) ul li button:not([disabled])"
_CHAP = "main section:nth-of-type(2) ul li button:not([disabled])"


def _catalog(bank: Path) -> dict[str, dict[str, Any]]:
    """题号（字符串）→ 题目。读的是**小包文件**，不是页面也不是库 —— 外部真相。"""
    pkg = json.loads(bank.read_text(encoding="utf-8"))
    return {str(q["id"]): q for q in pkg["questions"]}


async def _enter_session(b: Any, base: str) -> tuple[str, str]:
    """练习页 → 第一门**有题**的科目 → 它的第一个有题章节 → 答题页。

    返回 `(当前题号, 路径)` —— 路径留给"刷新后仍在"那一步复用。
    ★ 科目按钮没有 `data-*` ⇒ 只能按结构点第 i 个；但**不能假定第 1 门科目就有题**
      （小包按科目分层抽样，各科题量不匀），所以逐门试到第一个点得动的章节为止。
    """
    await b.goto_ready(base + "/practice")
    await b.wait_for(f"""document.querySelectorAll('{_SUBJ}').length > 0""", timeout=40, label="科目列表")
    n = await b.eval(f"""document.querySelectorAll('{_SUBJ}').length""")
    need(isinstance(n, int) and n > 0, f"科目列表是空的（n={n!r}）—— 题库里没有科目？")
    for i in range(n):
        await b.click(_SUBJ, nth=i)
        # 等这门科目的章节区渲染出来；`busy` 期间按钮全是 disabled ⇒ 一并等掉
        await _try_wait(b, f"""document.querySelectorAll('{_CHAP}').length > 0""", 10)
        if await b.eval(f"""document.querySelectorAll('{_CHAP}').length > 0"""):
            break
    else:
        raise Failure(
            f"{n} 门科目**都没有可以点的章节**（每章 0 题？）—— 小包的分层抽样出问题了？"
        )
    await b.click(_CHAP, nth=0)
    await b.wait_for(
        """location.pathname.startsWith('/practice/session/')""", timeout=40, label="进入答题页"
    )
    await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="答题页就绪")
    qid = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
    need(isinstance(qid, str) and qid, f"读不到当前题号：{qid!r}")
    return qid, await b.eval("location.pathname")


async def _flag(b: Any, attr: str) -> Any:
    """读 `data-flag-mark` / `data-flag-fav`：`'0'` / `'1'` / `None`（元素不在）。"""
    return await b.eval(
        f"""(() => {{ const e = document.querySelector('[data-flag-{attr}]');
                     return e ? e.getAttribute('data-flag-{attr}') : null; }})()"""
    )


async def _wait_flag(b: Any, attr: str, want: str) -> None:
    await b.wait_for(
        f"""(() => {{ const e = document.querySelector('[data-flag-{attr}]');
                     return !!e && e.getAttribute('data-flag-{attr}') === '{want}'; }})()""",
        timeout=30,
        label=f"`data-flag-{attr}` 翻成 {want}",
    )


#: 收藏页就绪判据：`[data-collect-empty]` **或** `[data-collect-list]` 出现
#: —— 两者都只在 `data !== null` 之后渲染（等页签的 `aria-pressed` 是不够的）。
_COLLECT_READY = (
    "document.querySelector('[data-collect-empty]') || document.querySelector('[data-collect-list]')"
)

_COLLECT_READ = """(() => ({
  total: (document.querySelector('[data-collect-total]') || {}).innerText || '',
  rows: Array.from(document.querySelectorAll('[data-collect-row]'))
          .map((e) => e.getAttribute('data-collect-row')),
  facets: Array.from(document.querySelectorAll('[data-facet]'))
          .map((e) => e.getAttribute('data-facet')),
}))()"""


async def _collection(b: Any, base: str, kind: str, facet: str | None = None) -> dict[str, Any]:
    """收藏页 → 切到 `kind` 页签 →（可选）点科目分面 → 读回**已稳定**的列表。

    ★ 为什么要"稳定"而不是读一次就走：切页签 / 切分面之后列表是**异步换源**的
      （`setKind`/`pickSubject` 之后 effect 里再取一次），读到一半拿到的是**上一份**。
      固定 `sleep` 是坏判据（时长靠猜、换台机器就不准），所以用**收敛判据**：
      **连续两次读到的内容一致**才算数。
    """
    await b.goto_ready(base + "/me/favorites")
    await b.wait_for(_COLLECT_READY, timeout=40, label="收藏页首屏列表")
    await b.click(f'[data-kind-tab="{kind}"]')
    await b.wait_for(
        f"""(() => {{ const t = document.querySelector('[data-kind-tab][aria-pressed="true"]');
                      return !!t && t.getAttribute('data-kind-tab') === '{kind}'; }})()""",
        timeout=30,
        label=f"切到「{kind}」页签",
    )
    if facet is not None:
        await b.click(f'[data-facet="{facet}"]')
        await b.wait_for(
            f"""document.querySelector('[data-facet="{facet}"][aria-pressed="true"]') !== null""",
            timeout=30,
            label=f"分面 {facet} 选中",
        )
    prev: Any = None
    for _ in range(25):
        await b.wait_for(_COLLECT_READY, timeout=20, label=f"「{kind}」列表")
        got = await b.eval(_COLLECT_READ)
        need(isinstance(got, dict), f"读收藏列表没拿到对象：{got!r}")
        if prev is not None and got == prev:
            return got
        prev = got
        await asyncio.sleep(0.25)
    raise Failure(f"「{kind}」列表**一直没稳定**（读了 25 次还在变）：{prev!r}")


#: 标记 / 收藏**只差几个字面量**（按钮属性 / 页签 kind / 对照页签）—— 所以共用一份实现。
#: ★ 不各写一遍：它们是**同一条流程**（按钮 → 刷新 → 列表 → 筛选 → 取消 → 对照），
#:   写两份的话改一处必漏另一处（「同一个事实两种写法」那一族）。
_FLAG_VARIANTS: dict[str, dict[str, str]] = {
    "marks": {
        "attr": "mark",
        "kind": "mark",
        "word": "标记",
        "other_kind": "favorite",
        "other_word": "收藏",
    },
    "fav": {
        "attr": "fav",
        "kind": "favorite",
        "word": "收藏",
        "other_kind": "mark",
        "other_word": "标记",
    },
}


async def _flag_scenario(
    base: str, small: Path, small_meta: dict[str, Any], timeout: int, name: str
) -> dict[str, float]:
    """标记 / 收藏的**同一条流程**（变体见 `_FLAG_VARIANTS`）。

    ★ 判据顺序就是用户定的"**先断未变化态，再断变化**"：
      "本来就有"和"真的记了账"长得一模一样 ⇒ 变化**之前**必须先断一次。
    """
    v = _FLAG_VARIANTS[name]
    attr, kind, word = v["attr"], v["kind"], v["word"]
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()
    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        import_sec = await _ensure_bank(b, base, small, small_meta, timeout)
        catalog = _catalog(small)

        # ---- ① 可证伪锚（变化之前）----
        before = await _collection(b, base, kind)
        need(
            before["rows"] == [],
            f"还没{word}过，「{word}」列表却有 {len(before['rows'])} 行：{before['rows']}",
        )
        need(
            "0" in before["total"],
            f"空列表时总数文案不对（期望「共 0 道{word}」）：{before['total'].strip()!r}",
        )
        say(f"  ✅ ①a 锚：{word}列表本来是空的（{before['total'].strip()}）")

        qid, path = await _enter_session(b, base)
        need(qid in catalog, f"当前题号 {qid} 不在小包里 —— 库里的题和小包对不上？")
        need(
            await _flag(b, attr) == "0",
            f"刚进来的题（{qid}）的「{word}」按钮不是 0 —— 锚不成立，后面的「变 1」就不说明问题",
        )
        say(f"  ✅ ①b 锚：题目 {qid} 的「{word}」按钮本来是 0")

        # ---- ② 点 ----
        await b.click(f"[data-flag-{attr}]")
        await _wait_flag(b, attr, "1")
        say(f"  ✅ ② 点「{word}」⇒ 按钮翻成 1")

        # ---- ③ 刷新后仍在（读的是 IndexedDB，不是内存态）----
        await b.goto_ready(base + path)
        await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="刷新后答题页")
        again = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(
            again == qid,
            f"刷新后回到的是**另一道题**（{again} ≠ {qid}）—— 本步的前提没了，不能算通过",
        )
        need(await _flag(b, attr) == "1", f"刷新后{word}没了 —— 没落到 IndexedDB")
        say("  ✅ ③ 刷新后仍在（IndexedDB 持久化）")

        # ---- ④ 列表能筛出 ----
        after = await _collection(b, base, kind)
        need(
            after["rows"] == [qid],
            f"「{word}」列表应该有且仅有 {qid}：{after['rows']}"
            f"（总数文案 {after['total'].strip()!r}）",
        )
        sid = str(catalog[qid]["subject_id"])
        need(sid in after["facets"], f"分面里没有这道题的科目 {sid}：{after['facets']}")
        filtered = await _collection(b, base, kind, facet=sid)
        need(filtered["rows"] == [qid], f"按科目 {sid} 筛完不该变：{filtered['rows']}")
        unfiltered = await _collection(b, base, kind, facet="all")
        need(unfiltered["rows"] == [qid], f"点回「全部」不该变：{unfiltered['rows']}")
        say(f"  ✅ ④ 列表能筛出（共 1 道；科目 {sid} 与「全部」筛出来的都是它 {qid}）")

        # ---- ⑤ 对照：**另一个**页签仍是 0 ----
        # ★ 少了这一条，"两个页签其实读的是同一份数据"的实现也能过 ④
        #   （那正是"二元判据只验一头"的老毛病）。
        other = await _collection(b, base, v["other_kind"])
        need(
            other["rows"] == [],
            f"只打了**{word}**，「{v['other_word']}」列表却有 {len(other['rows'])} 行："
            f"{other['rows']} —— 两个页签切的是同一份数据？",
        )
        say(f"  ✅ ⑤ 对照：「{v['other_word']}」列表仍是 0 ⇒ 两个页签真的在切**不同的题源**")

        # ---- ⑥ 取消 ⇒ 行从列表消失、总数回 0（**写与取消是一对**）----
        # ★ 约定 T 的同族（**写设门、取消不设门**）：取消这条路径必须自己能被验到 ——
        #   只验"能记上"的话，一个"记得上但撤不掉"的实现也能过。
        back = await _collection(b, base, kind)  # ⑤ 把页签切走了，先切回来
        need(back["rows"] == [qid], f"切回「{word}」页签后行不见了：{back['rows']}")
        await b.click(f'[data-collect-remove="{qid}"]')
        need(
            await _try_wait(
                b, """document.querySelectorAll('[data-collect-row]').length === 0""", 30
            ),
            f"点了「取消{word}」，那一行**还在**列表里",
        )
        gone = await _collection(b, base, kind)
        need(gone["rows"] == [], f"取消之后列表还有 {gone['rows']}")
        need("0" in gone["total"], f"取消之后总数文案不是 0：{gone['total'].strip()!r}")
        say(f"  ✅ ⑥ 取消{word} ⇒ 行消失、总数回「共 0 道{word}」")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


async def run_marks(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`marks` 场景：标记一道题（单功能闭环；实现见 `_flag_scenario`）。"""
    return await _flag_scenario(base, small, small_meta, timeout, "marks")


async def run_fav(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`fav` 场景：收藏一道题（与 `marks` 同流程、不同题源）。"""
    return await _flag_scenario(base, small, small_meta, timeout, "fav")


# --------------------------------------------------------------- 场景：notes

_NOTE1 = "走查写的第 1 版文案"
_NOTE2 = "改过之后的第 2 版文案"
_NOTE3 = "在列表页改的第 3 版文案"


async def _open_panel(b: Any) -> None:
    """展开答题页的笔记面板（已展开就什么都不做）。"""
    if await b.eval("!!document.querySelector('[data-note-input]')"):
        return
    await b.click("[data-note-open]")
    await b.wait_for("!!document.querySelector('[data-note-input]')", timeout=30, label="笔记面板展开")


async def _retype(b: Any, selector: str, text: str) -> None:
    """把输入框**清空**再敲入 —— 清空走 **Ctrl+A**（真键盘），不碰 React 的 value tracker。

    ⚠️ `type_text` 只负责"敲"，它**不清空**：直接往上敲会得到"旧文案 + 新文案"，
       而那恰好让"编辑"与"追加"看起来一样 —— 正是本场景要分开的两件事。
    """
    await b.eval(f"document.querySelectorAll({json.dumps(selector)})[0].focus()")
    for kind in ("keyDown", "keyUp"):
        await b.send(
            "Input.dispatchKeyEvent",
            {"type": kind, "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65},
        )
    await b.type_text(selector, text)


async def _panel_state(b: Any) -> dict[str, Any]:
    """读**答题页的笔记面板**（要求已展开）：徽标 / 条 id / 各自文案 / 空态在不在。"""
    got = await b.eval(
        """(() => {
             const ids = Array.from(document.querySelectorAll('[data-note-id]'))
                            .map((e) => e.getAttribute('data-note-id'));
             const contents = {};
             for (const id of ids) {
               const c = document.querySelector('[data-note-content="' + id + '"]');
               contents[id] = c ? c.innerText : null;
             }
             const badge = document.querySelector('[data-note-open]');
             return {
               badge: badge ? badge.getAttribute('data-note-count') : null,
               ids: ids,
               contents: contents,
               emptyNone: !!document.querySelector('[data-note-empty="none"]'),
             };
           })()"""
    )
    need(isinstance(got, dict), f"读笔记面板没拿到对象：{got!r}")
    return got


async def _list_state(b: Any, base: str) -> dict[str, Any]:
    """打开 `/me/notes`，等列表落地，读回总数文案 / 行 id / 空态取值。"""
    await b.goto_ready(base + "/me/notes")
    await b.wait_for(
        "document.querySelector('[data-note-empty]') || document.querySelector('[data-note-row]')",
        timeout=40,
        label="笔记列表落地",
    )
    got = await b.eval(
        """(() => {
             const e = document.querySelector('[data-note-empty]');
             return {
               total: (document.querySelector('[data-note-total]') || {}).innerText || '',
               rows: Array.from(document.querySelectorAll('[data-note-row]'))
                       .map((x) => x.getAttribute('data-note-row')),
               empty: e ? e.getAttribute('data-note-empty') : null,
             };
           })()"""
    )
    need(isinstance(got, dict), f"读笔记列表没拿到对象：{got!r}")
    return got


async def _back_to_session(b: Any, base: str, path: str, qid: str) -> None:
    """回到**同一场**练习的同一道题 —— 每一步都自己确立前提，不依赖上一步留下的位置。"""
    await b.goto_ready(base + path)
    await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="回到答题页")
    got = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
    need(got == qid, f"回到的题不对（{got} ≠ {qid}）—— 后面的判据会失去前提，不能算通过")


async def _idb_all(b: Any, store: str) -> list[Any]:
    """读某个 store 的**全部行**（原样）。用来证明"软删 ≠ 物理删"。"""
    got = await b.eval(
        """(async () => {
             const open = () => new Promise((res, rej) => {
               const r = indexedDB.open('yijian-pwa');
               r.onsuccess = () => res(r.result);
               r.onerror = () => rej(r.error || new Error('open failed'));
             });
             const db = await open();
             const rows = await new Promise((res, rej) => {
               const r = db.transaction('STORE', 'readonly').objectStore('STORE').getAll();
               r.onsuccess = () => res(r.result);
               r.onerror = () => rej(r.error || new Error('getAll failed'));
             });
             db.close();
             return rows;
           })()""".replace("STORE", store)
    )
    need(isinstance(got, list), f"读 store `{store}` 没拿到数组：{got!r}")
    return got


async def run_notes(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`notes` 场景：**新增 / 编辑 / 软删** 三条路径 + 软删的**多条读路径**。

    ★ 用户 2026-10-07 点的三条：
      ① **编辑之后条数仍为 1** —— 拦"编辑其实插了一条"（有些实现会"先删后插"，
         那样条数变 2，**但不报错**）；
      ② 软删要**多条读路径**都看不到 —— 只验"列表里没了"不够，"删了还在别处"
         是最常见的不报错形态；
      ③ "新增"与"编辑"是**两条路径**，分别验。

    ★ 本场景**不覆盖**"报告页的笔记计数"：它**不存在**（`report/page.tsx` 里 `note` 零命中）
      ⇒ 塞一条断言进来只会是一条**假装查过**的判据。范围里说清"没有"，不算查过。
    ★ 读路径 5/5（备份）的判据刻意取"**恢复之后仍然看不见**"而不是"文件里没有那一行"：
      导出取的是 `idbAll`（**含 tombstone**），两种口径在"恢复后看不见"上等价，
      而前者与实现选择无关。文件里到底有没有，走查**如实打印**出来备查。
    """
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()
    dl = Path(tempfile.mkdtemp(prefix="e2e-pwa-dl-"))
    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        import_sec = await _ensure_bank(b, base, small, small_meta, timeout)
        catalog = _catalog(small)
        qid, path = await _enter_session(b, base)
        need(qid in catalog, f"当前题号 {qid} 不在小包里 —— 库里的题和小包对不上？")

        # ---- ① 可证伪锚（变化之前）：徽标 0 / 面板空 / 列表空 ----
        await _open_panel(b)
        st = await _panel_state(b)
        need(st["badge"] == "0", f"还没写过笔记，徽标却是 {st['badge']!r}")
        need(st["ids"] == [] and st["emptyNone"], f"面板里本该什么都没有：{st}")
        first = await _list_state(b, base)
        need(first["rows"] == [] and "0" in first["total"], f"还没写过笔记，列表却是：{first}")
        say(f"  ✅ ① 锚（变化之前）：徽标 0 ｜ 面板「这道题还没有笔记」｜ 列表「{first['total'].strip()}」")

        # ---- ② 新增（**POST** 路径）----
        await _back_to_session(b, base, path, qid)
        await _open_panel(b)
        await b.type_text("[data-note-input]", _NOTE1)
        await b.wait_for(
            """!document.querySelector('[data-note-submit]').disabled""", timeout=20, label="保存按钮可点"
        )
        await b.click("[data-note-submit]")
        await b.wait_for(
            "document.querySelectorAll('[data-note-id]').length === 1", timeout=30, label="面板里出现 1 条"
        )
        st = await _panel_state(b)
        nid = st["ids"][0]
        need(st["badge"] == "1", f"写完一条徽标应是 1：{st['badge']!r}")
        need(not st["emptyNone"], "刚写完一条，面板却仍显示「这道题还没有笔记」")
        need((st["contents"].get(nid) or "").strip() == _NOTE1, f"面板文案不对：{st['contents']}")
        say(f"  ✅ ② 新增（POST）：面板 1 条 ｜ 徽标 1 ｜ note id = {nid}")

        # ---- ③ 编辑（**PUT** 路径；★ 条数必须仍是 1、id 必须不变）----
        await b.click(f'[data-note-edit="{nid}"]')
        await b.wait_for("!!document.querySelector('[data-note-edit-input]')", timeout=20, label="编辑框")
        await _retype(b, "[data-note-edit-input]", _NOTE2)
        await b.click("[data-note-edit-save]")
        # ★ 等的是"**保存这件事结束了**"这个中性信号（编辑框收起），**不是**"我期望的值出现"：
        #   后者会把"值不对"变成"等待超时"，而超时**指不到真凶**（2026-10-07 变异实测踩到）。
        await b.wait_for(
            """!document.querySelector('[data-note-edit-input]')""", timeout=30, label="编辑框收起"
        )
        st2 = await _panel_state(b)
        # ★★ **先看库**：UI 是**产物**，库里的行才是**事实**。
        #    实测（2026-10-07 变异"编辑插一条新的"）：**面板会被替换成 1 条**（旧那条
        #    被内存态盖住）⇒ 只断言 UI 条数**看不出来**；而库里已经是 2 行。
        #    ⇒ 这条放在最前面：它比任何 UI 判据都硬，且消息里带两个 id，能直接定位。
        rows_now = await _idb_all(b, "notes")
        need(
            len(rows_now) == 1,
            f"★ 编辑之后库里变成了 {len(rows_now)} 行 —— 编辑**不该**新增一行"
            f"（UI 上可能只显示 1 条，看不见这件事）：{[r.get('id') for r in rows_now]}",
        )
        need(
            len(st2["ids"]) == 1,
            f"★ 编辑之后条数变成了 {len(st2['ids'])} —— 编辑**不该**插一条（{st2['ids']}）",
        )
        need(st2["ids"] == [nid], f"编辑换了 id（{st2['ids']} ≠ [{nid}]）—— 编辑应是**原地改**")
        need(st2["badge"] == "1", f"编辑之后徽标应是 1：{st2['badge']!r}")
        need((st2["contents"].get(nid) or "").strip() == _NOTE2, f"面板文案不是新值：{st2['contents']}")
        say(f"  ✅ ③ 编辑（PUT，答题页面板）：条数**仍是 1** ｜ id 不变（{nid}）｜ **库里也是 1 行**")

        # ---- ④ 列表对账 ----
        lst = await _list_state(b, base)
        need(lst["rows"] == [nid], f"「我的笔记」应有且仅有 {nid}：{lst['rows']}")
        need("1" in lst["total"], f"总数文案不对（期望「共 1 条笔记」）：{lst['total'].strip()!r}")
        row_txt = await b.eval(f"""(document.querySelector('[data-note-row="{nid}"]') || {{}}).innerText || ''""")
        need(_NOTE2 in row_txt, f"列表行里的文案不是新值：{row_txt!r}")
        say(f"  ✅ ④ 列表对账：逐项一致（{lst['total'].strip()}，行内是新文案）")

        # ---- ④b **另一条编辑入口**：列表页上的「编辑」（同一个接口，**另一段实现**）----
        # ★ 用户点的第 ③ 条：两个入口要**分别**验。这一页的 `save()` 是"就地替换那一行、
        #   不重新取整页" —— 与答题页面板那条不是同一段代码，只验一条会漏另一条。
        await b.click(f'[data-note-edit="{nid}"]')
        await b.wait_for(
            """!!document.querySelector('[data-note-edit-input]')""", timeout=20, label="列表页编辑框"
        )
        await _retype(b, "[data-note-edit-input]", _NOTE3)
        await b.click(f'[data-note-edit-save="{nid}"]')
        await b.wait_for(
            """!document.querySelector('[data-note-edit-input]')""", timeout=30, label="列表页编辑框收起"
        )
        lst_b = await _list_state(b, base)
        need(lst_b["rows"] == [nid], f"★ 列表页编辑之后行变了：{lst_b['rows']}")
        need("1" in lst_b["total"], f"★ 列表页编辑之后总数变了：{lst_b['total'].strip()!r}")
        row_txt3 = await b.eval(
            f"""(document.querySelector('[data-note-row="{nid}"]') || {{}}).innerText || ''"""
        )
        need(_NOTE3 in row_txt3, f"列表页编辑之后行内不是新文案：{row_txt3!r}")
        say("  ✅ ④b 另一条入口（/me/notes 的「编辑」）：条数仍 1、id 不变、行内是新文案")

        # ---- ⑤ 刷新后仍在（IndexedDB 持久化）----
        await _back_to_session(b, base, path, qid)
        await _open_panel(b)
        st3 = await _panel_state(b)
        need(st3["badge"] == "1", f"刷新后徽标不是 1：{st3['badge']!r}")
        need(st3["ids"] == [nid], f"刷新后笔记变了：{st3['ids']}")
        need((st3["contents"].get(nid) or "").strip() == _NOTE3, "刷新后文案不是列表页改的那一版")
        say("  ✅ ⑤ 刷新后仍在（IndexedDB 持久化，内容是列表页改的那一版）")

        # ---- ⑥ 软删：**多条读路径**逐一确认看不见 ----
        lst = await _list_state(b, base)
        await b.click(f'[data-note-del="{nid}"]')
        await b.wait_for(
            "document.querySelectorAll('[data-note-row]').length === 0",
            timeout=30,
            label="列表里那行消失",
        )
        lst2 = await _list_state(b, base)
        need(lst2["rows"] == [], f"删完列表还有：{lst2['rows']}")
        need(
            lst2["empty"] == "no-data",
            f"空态该是 `no-data`（「还没写过」），不是 `filtered`：{lst2['empty']!r}",
        )
        need("0" in lst2["total"], f"总数文案不是 0：{lst2['total'].strip()!r}")
        say(f"  ✅ ⑥a 读路径 1/5（/me/notes 列表）：行没了、空态 no-data、{lst2['total'].strip()}")

        await _back_to_session(b, base, path, qid)
        await _open_panel(b)
        st4 = await _panel_state(b)
        need(st4["badge"] == "0", f"★ 读路径 2/5（答题页**徽标**）：删完之后仍是 {st4['badge']!r}")
        need(
            st4["ids"] == [] and st4["emptyNone"],
            f"★ 读路径 3/5（答题页**面板**）：删完还看得到：{st4}",
        )
        say("  ✅ ⑥b 读路径 2/5 + 3/5（答题页的徽标 + 面板）：都看不到它")

        rows = await _idb_all(b, "notes")
        need(len(rows) == 1, f"软删之后库里应该**留着那一行**（tombstone），实际 {len(rows)} 行：{rows}")
        need(rows[0].get("deleted_at"), f"那一行没有 `deleted_at` —— 那就不是软删了：{rows[0]}")
        say("  ✅ ⑥c 读路径 4/5（库里的行）：tombstone 仍在（`deleted_at` 已置）")
        say("       ★ 报告页那条（5 条里的第 4 条）**不存在** —— 不写断言，也不算查过")

        # ★★ 正对照：**先证明"恢复"这件事真的发生了**，否则"恢复后笔记看不见"可以
        #    是"恢复根本没生效"—— 那是**空绿**（少一个"本来应该回来"的东西当参照）。
        await _back_to_session(b, base, path, qid)
        await b.click("[data-flag-mark]")
        await _wait_flag(b, "mark", "1")
        say("  · 正对照已布置：另外打了一个**标记** —— 恢复之后它**必须回来**")

        # ---- ⑦ 读路径 5/5（备份）：导出 → **恢复** ⇒ 仍然看不见 ----
        await b.goto_ready(base + "/me/backup")
        # ★ 下载拦截：底座现成的 `send()` 就够（只有一个调用方 ⇒ **不为它加底座方法**，
        #   加了就是"将来可能用"）。机制本身已由独立探针验证过。
        await b.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dl)})
        await b.click("[data-export]")
        await b.wait_for("""!!document.querySelector('[data-exported]')""", timeout=40, label="导出完成")
        f: Path | None = None
        for _ in range(40):
            hits = list(dl.glob("*.json"))
            if hits:
                f = hits[0]
                break
            await asyncio.sleep(0.25)
        need(f is not None, f"没等到导出的备份文件（目录 {dl}）")
        bundle = json.loads(f.read_text(encoding="utf-8"))
        raw_notes = bundle.get("data", {}).get("notes", [])
        dead = [r for r in raw_notes if r.get("deleted_at")]
        say(f"  · 实测：备份文件 {f.name} 里 notes 共 {len(raw_notes)} 行，其中**已软删** {len(dead)} 行")

        await b.set_file_input("[data-import-file]", f)
        await b.wait_for("""!!document.querySelector('[data-confirm-block]')""", timeout=40, label="导入确认块")
        need(
            not await b.eval("""!!document.querySelector('[data-bank-mismatch]')"""),
            "同一份题库导出的备份却提示「题库不匹配」—— 指纹判据坏了",
        )
        await b.click("[data-confirm-import]")
        await b.wait_for("""!!document.querySelector('[data-imported]')""", timeout=timeout, label="恢复完成")

        # (a) 正对照：标记**回来了** ⇒ "恢复"确实生效
        back = await _collection(b, base, "mark")
        need(
            back["rows"] == [qid],
            f"★ 正对照失败：恢复之后标记**没回来**（{back['rows']}）"
            "⇒ 「恢复」这件事没生效，下面那条判据是**空的**",
        )
        say("  ✅ ⑦a 正对照：恢复之后**标记回来了** ⇒ 恢复真的生效了")
        # (b) 负判据：已软删的笔记**没有复活**
        lst3 = await _list_state(b, base)
        need(lst3["rows"] == [], f"★ 恢复之后已软删的笔记**复活了**：{lst3['rows']}")
        need("0" in lst3["total"], f"恢复后总数不是 0：{lst3['total'].strip()!r}")
        say("  ✅ ⑦b 读路径 5/5（备份）：导出 → 恢复 ⇒ 已软删的**仍然看不见**（没有复活）")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


# --------------------------------------------------------------- 场景：wrong

#: 错题本就绪判据：`[data-wrong-empty]` **或** `[data-wrong-list]` 出现
#: —— 两者都只在 `data !== null` 之后渲染（等"加载中"消失是不够的）。
_WRONG_READY = (
    "document.querySelector('[data-wrong-empty]') || document.querySelector('[data-wrong-list]')"
)

_WRONG_READ = """(() => ({
  total: (document.querySelector('[data-wrong-total]') || {}).innerText || '',
  items: Array.from(document.querySelectorAll('[data-wrong-item]'))
          .map((e) => e.getAttribute('data-wrong-item')),
  empty: (() => { const e = document.querySelector('[data-wrong-empty]');
                  return e ? e.getAttribute('data-wrong-empty') : null; })(),
}))()"""


async def _list_state_wrong(b: Any, base: str) -> dict[str, Any]:
    """打开错题本、等列表落地，读回总数文案 / 条目 qid 列表 / 空态取值。"""
    await b.goto_ready(base + "/practice/wrong")
    await b.wait_for(_WRONG_READY, timeout=40, label="错题本列表落地")
    got = await b.eval(_WRONG_READ)
    need(isinstance(got, dict), f"读错题本没拿到对象：{got!r}")
    return got


def _page_subject_order(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """页面上的**科目顺序** —— 必须**复刻** `practice/page.tsx` 的排序：
    先「公共课」再「专业课」，各自按 `(sort_no, id)`。
    ★ 不能想当然用 `pack["subjects"]` 的原始顺序：页面是分两组渲染的。
    """
    subs = list(pack["subjects"])
    key = lambda s: (s.get("sort_no") or 0, s.get("id"))  # noqa: E731
    pub = sorted([s for s in subs if s.get("category") == "public"], key=key)
    pro = sorted([s for s in subs if s.get("category") != "public"], key=key)
    return pub + pro


def _chapters_of(pack: dict[str, Any], subject_id: Any) -> list[dict[str, Any]]:
    """页面上这门科目的**章节顺序**（按 `(sort_no, id)`）—— 与 `listChapters` 的排序一致。"""
    chs = [c for c in pack["chapters"] if str(c.get("subject_id")) == str(subject_id)]
    return sorted(chs, key=lambda c: (c.get("sort_no") or 0, c.get("id")))


def _small_chapter(pack: dict[str, Any], limit: int) -> tuple[int, int] | None:
    """找一个「**可判分题数 ≤ limit**」的章节 ⇒ `(科目序号, 章节序号)`（都是页面顺序）。

    ★ 为什么要它（2026-10-07 用户追问后**重新分析**，先前的结论是错的）：
      `createSession` **不接受**"章节会话 + 指定题集" —— `mode` 只有 `chapter` / `wrong`，
      且 `question_ids` **只在 `mode='wrong'` 下有意义**（与后端 `SessionCreateIn._check`
      同口径，属**设计上不支持**，不是漏了）。
      ⚠️ 但"让会话里**含有**某一道指定的题"**不需要**那个能力：当这一章的**可判分题数
      ≤ 单次抽题上限**（页面写死 `SESSION_SIZE = 10`）时，**整章都会进会话** ⇒ 指定题自然在里面。
      实测（200 题小包）：6 个科目里有多章满足（例：经济第 2 章 = 8 题、法规第 1 章 = 5 题）。
    """
    gradable = {"single", "multiple", "judge"}
    for si, s in enumerate(_page_subject_order(pack)):
        for ci, c in enumerate(_chapters_of(pack, s["id"])):
            n = sum(
                1
                for q in pack["questions"]
                if str(q.get("chapter_id")) == str(c["id"]) and q.get("type") in gradable
            )
            if 1 <= n <= limit:
                return si, ci
    return None


async def _enter_session_at(b: Any, base: str, si: int, ci: int) -> str:
    """按**序号**进指定科目的指定章节（序号 = `_page_subject_order` / `_chapters_of` 的顺序）。"""
    await b.goto_ready(base + "/practice")
    await b.wait_for(
        f"""document.querySelectorAll('{_SUBJ}').length > {si}""", timeout=40, label="科目列表"
    )
    await b.click(_SUBJ, nth=si)
    await b.wait_for(
        f"""document.querySelectorAll('{_CHAP}').length > {ci}""",
        timeout=40,
        label=f"第 {si + 1} 门科目的第 {ci + 1} 个有题章节",
    )
    await b.click(_CHAP, nth=ci)
    await b.wait_for(
        """location.pathname.startsWith('/practice/session/')""", timeout=40, label="进入答题页"
    )
    await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="答题页就绪")
    return await b.eval("location.pathname")


def _labels_right(q: dict[str, Any]) -> list[str]:
    """小包里这道题的**正确标号** —— 外部真相。

    ★ 判断题的 `answer.value` 是**布尔**（`[true]` / `[false]`），而 UI 上是**硬编码**的
      「正确 / 错误」两个按钮 ⇒ 这里做一次映射（这正是"答题页选项本来没有 hook、判断题
      更是连标号都没有"那件事的收尾）。
    """
    vals = (q.get("answer") or {}).get("value")
    if not isinstance(vals, list):
        return []
    return ["正确" if v is True else "错误" if v is False else str(v) for v in vals]


def _labels_all(q: dict[str, Any]) -> list[str]:
    """这道题在 UI 上**能点**的标号（判断题是硬编码的两个）。"""
    if q["type"] == "judge":
        return ["正确", "错误"]
    return [str(o["label"]) for o in (q.get("options") or [])]


async def _pick_and_submit(b: Any, labels: list[str]) -> None:
    """点这些标号 → 点「提交」。

    ★ 定位走 **`data-submit`**（2026-10-07 用户裁定加的语义锚点）—— 之前是按文案
      `innerText.trim() === '提交'` 找的，那属于 BL-23 那一族（挂文案会漂移）。
      锚点是**两份一起加、保持 `same`** 的惰性属性（与 `data-option` 同款）。
    """
    for lb in labels:
        await b.click(f'[data-option="{lb}"]')
    await b.wait_for(
        """(() => { const e = document.querySelector('[data-submit]'); return !!e && !e.disabled; })()""",
        timeout=30,
        label="「提交」可点",
    )
    await b.click("[data-submit]")


async def _wrong_row(b: Any, qid: str) -> dict[str, Any] | None:
    """读**库里**这道题的错题行（`None` = 不在错题本里）。"""
    rows = await _idb_all(b, "wrong")
    hit = [r for r in rows if str(r.get("question_id")) == qid]
    need(len(hit) <= 1, f"`wrong` 里同一个 qid 出现 {len(hit)} 行：{hit}")
    return hit[0] if hit else None


async def run_wrong(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`wrong` 场景：**答错 → 错题本 → 重练 → 答对 → `retry_correct` +1**（核心闭环）。

    ★ 判据顺序（用户 2026-10-07 定）：**先断"本来没有"，再断"有了"**。
      ①a 错题本为空 ｜ ①b **这道题**本来不是错题（**同一个详情页 URL**：答错前显示
      "不在错题本里"、答错后有记录 —— 同一路径上的一对，不是两个不同的东西）
      ② **由小包驱动**地故意答错（读 `data-qid` → 查小包 `answer.value` → 反着点）
      ③ 错题本 1 条、**且此时没有「已重练」**
      ④ 「重练这道题」⇒ 新 session，且**只含这题**
      ⑤ 提交**正确标号** ⇒ 答对
      ⑥ 回错题本 ⇒ 出现「已重练 ✓」，**且「错 1 次」没变**
      ⑦ ★ **在库里**断言 `retry_correct == 1` 且 `wrong_count == 1` —— 不在 UI 断言
         （UI 会被缓存 / 内存态盖住：`notes` 场景刚证明过这件事）

    ★ **⑧ 是"章节练习里答对**不该**给 `retry_correct` +1"这条反向判据** ——
      2026-10-07 我先判它"拿不到"（题答错后落到"没做过的在后"的尾部），**那个判断是错的**：
      `createSession` 虽然拒绝"章节 + 指定题集"，但只要挑一个**可判分题数 ≤ 10** 的章节，
      整章都会进会话 ⇒ 指定题自然在里面（见 `_small_chapter`）。
      ⇒ **"做不到"和"这个能力没实现"是两件事** —— 前者要先问"换个构造方式行不行"。
    """
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()
    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        import_sec = await _ensure_bank(b, base, small, small_meta, timeout)
        pack = json.loads(small.read_text(encoding="utf-8"))
        catalog = _catalog(small)
        # ★★ 一开始就挑一个「可判分题数 ≤ 10」的章节 —— 这样**这道题本身就落在一个小章里**，
        #    ⑧ 重进同一个章节时，"整章都进会话"才真的成立。
        #    ⚠️ 第一版只在 ⑧ 里找小章节，结果找到的是**另一章**（qid 在第一大章里）⇒ 判据前提不成立。
        pick = _small_chapter(pack, 10)
        need(
            pick is not None,
            "小包里找不到「可判分题数 ≤ 10」的章节 —— ⑧ 的构造前提没了（换更大的小包？）",
        )
        si, ci = pick
        path = await _enter_session_at(b, base, si, ci)
        qid = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(qid in catalog, f"当前题号 {qid} 不在小包里 —— 库里的题和小包对不上？")
        q = catalog[qid]
        right, all_labels = _labels_right(q), _labels_all(q)
        need(right, f"小包里这道题（{qid}，type={q.get('type')}）没有可判分的答案：{q.get('answer')}")
        wrongs = [lb for lb in all_labels if lb not in set(right)]
        need(wrongs, f"这道题**每个**选项都是对的？{all_labels} / right={right}")

        # ---- ① 可证伪锚（变化之前）----
        lst = await _list_state_wrong(b, base)
        need(lst["items"] == [], f"还没答错过，错题本却有 {len(lst['items'])} 条：{lst['items']}")
        need("0" in lst["total"], f"总数文案不对（期望「共 0 道错题」）：{lst['total'].strip()!r}")
        await b.goto_ready(base + f"/practice/wrong/{qid}")
        await b.wait_for(
            """!!document.querySelector('[data-wrong-missing]')""",
            timeout=40,
            label="①b 详情页说「不在错题本里」",
        )
        need(
            (await _wrong_row(b, qid)) is None,
            "①b 不成立：库里已经有这道题的错题行了 —— 后面的判据都不说明问题",
        )
        say(f"  ✅ ① 锚（变化之前）：错题本空（{lst['total'].strip()}）｜ 详情页说它「不在错题本里」")
        say(f"       ★ 这道题 type={q.get('type')}，正确标号={right}，将故意点 {wrongs[0]}")

        # ---- ② 故意答错（**小包驱动**，不是"点第一个"）----
        await b.goto_ready(base + path)
        await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="回到答题页")
        got = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(got == qid, f"回到的题不对（{got} ≠ {qid}）—— 后面的前提没了")
        await _pick_and_submit(b, [wrongs[0]])
        await b.wait_text("答错了", timeout=40)
        say(f"  ✅ ② 故意答错（点了 {wrongs[0]}，来自小包的 `answer.value`）⇒ 页面判「答错了」")

        # ---- ③ 错题本出现 1 条，**且此时没有「已重练」** ----
        await b.goto_ready(base + f"/practice/wrong/{qid}")
        await b.wait_for("""!!document.querySelector('[data-wrong-record]')""", timeout=40, label="详情页有记录")
        ans_txt = await b.eval("""(document.querySelector('[data-wrong-answer]') || {}).innerText || ''""")
        for lb in right:
            need(lb in ans_txt, f"详情页揭示的正确答案里没有 {lb}（小包说它是正确的）：{ans_txt!r}")
        need(
            not await b.eval("""!!document.querySelector('[data-retried]')"""),
            "刚答错一次，详情页就显示「已重练」了 —— 重练标记不该凭空出现",
        )
        rec = await b.eval("""(document.querySelector('[data-wrong-record]') || {}).innerText || ''""")
        need("错过 1 次" in rec, f"详情页记录该是「错过 1 次」：{rec!r}")
        lst = await _list_state_wrong(b, base)
        need(lst["items"] == [qid], f"错题本应有且仅有 {qid}：{lst['items']}")
        need("1" in lst["total"], f"总数文案不对（期望「共 1 道错题」）：{lst['total'].strip()!r}")
        row_txt = await b.eval(f"""(document.querySelector('[data-wrong-row="{qid}"]') || {{}}).innerText || ''""")
        need("错 1 次" in row_txt, f"行内该显示「错 1 次」：{row_txt!r}")
        need("重练答对" not in row_txt, f"还没重练过，行内不该有「重练答对」：{row_txt!r}")
        need(
            not await b.eval("""!!document.querySelector('[data-retried]')"""),
            "错题本里已出现「已重练 ✓」—— 它只该在重练答对之后出现",
        )
        row0 = await _wrong_row(b, qid)
        need(row0 is not None, "库里没有这道题的错题行 —— UI 上有、库里没有，两边不一致")
        need(
            row0.get("wrong_count") == 1 and row0.get("retry_correct") == 0,
            f"库里的计数不对（期望 wrong_count=1 / retry_correct=0）：{row0}",
        )
        say("  ✅ ③ 错题本 1 条、库里 wrong_count=1 / retry_correct=0，且**没有**「已重练」")

        # ---- ④ 「重练这道题」⇒ 新 session，**只含这题** ----
        await b.click(f'[data-retry="{qid}"]')
        await b.wait_for(
            f"""location.pathname.startsWith('/practice/session/')
                 && location.pathname !== {json.dumps(path)}""",
            timeout=40,
            label="进入**新的**重练会话",
        )
        await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="重练答题页就绪")
        rid = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(rid == qid, f"重练会话的第一题不是 {qid}（是 {rid}）")
        cells = await b.eval(
            """Array.from(document.querySelectorAll('[data-sheet-grid] button'))
                   .map((e) => e.getAttribute('data-cell-qid'))"""
        )
        need(cells == [qid], f"★ 重练会话该**只含这题**，答题卡却是：{cells}")
        new_path = await b.eval("location.pathname")
        say(f"  ✅ ④ 重练 ⇒ 新会话（{new_path.split('/')[-1][:10]}…），答题卡只有 1 格且就是它")

        # ---- ⑤ 提交**正确标号** ⇒ 答对 ----
        await _pick_and_submit(b, right)
        await b.wait_text("答对了", timeout=40)
        say(f"  ✅ ⑤ 提交正确答案（{right}）⇒ 答对")

        # ---- ⑥ 回错题本：「已重练 ✓」出现，且「错 1 次」**没变** ----
        lst = await _list_state_wrong(b, base)
        need(lst["items"] == [qid], f"重练之后错题本条目变了：{lst['items']}")
        need(
            await b.eval(f"""!!document.querySelector('[data-retried="{qid}"]')"""),
            "重练答对了，错题本里却没出现「已重练 ✓」",
        )
        row_txt = await b.eval(f"""(document.querySelector('[data-wrong-row="{qid}"]') || {{}}).innerText || ''""")
        need("错 1 次" in row_txt, f"★ 「错 1 次」被重练覆盖掉了：{row_txt!r}（重练不该改 wrong_count）")
        need("重练答对 1 次" in row_txt, f"行内该显示「重练答对 1 次」：{row_txt!r}")
        say("  ✅ ⑥ 错题本出现「已重练 ✓」+「重练答对 1 次」，而「错 1 次」**没变**")

        # ---- ⑦ ★ 在**库里**收口（UI 会被缓存盖住）----
        # ⚠️⚠️ **本条与 ⑥ 断言的是同一事实的两层，它没有被变异证明过独立承重。**
        #    2026-10-07 试过并**构造不出**"让 ⑥ 过、⑦ 红"的变异：那需要"③ 时列表说 0、
        #    重练后说 1、**而库一直是 0**" ⇒ 要求列表投影与库状态相关，而那**正是正确实现**。
        #    ⇒ 它的价值是**抗缓存 / 抗内存态**（`notes` 场景确实发生过"UI 把库里的事实盖住"），
        #      不是"⑦ 能抓到 ⑥ 抓不到的东西"。
        #    ★ 留下这段标注的理由（用户 2026-10-07）：不标的话，将来有人会以为"⑦ 是备份"
        #      而把 ⑥ 删掉 —— 那会把两层同一事实变成一层。与「fork 理由里的收敛条件是待办」同族。
        row1 = await _wrong_row(b, qid)
        need(row1 is not None, "重练之后库里那行不见了 —— 软删语义被破坏")
        need(
            row1.get("retry_correct") == 1,
            f"★ 库里 `retry_correct` 应是 1（重练答对一次）：{row1}",
        )
        need(
            row1.get("wrong_count") == 1,
            f"★ 库里 `wrong_count` 应仍是 1（重练**不该**动它）：{row1}",
        )
        say("  ✅ ⑦ 库里收口：`retry_correct=1`、`wrong_count=1`（**不是** UI 上的数字）")

        # ---- ⑧ ★ 反向对照：**章节练习**里答对**不算**重练答对 ----
        # ★ 2026-10-07 用户追问后**重新分析**：我先前的结论是"拿不到"（理由是"题一答错就落到
        #   '没做过的在后' 的排序尾部 ⇒ 新的章节会话里不会有它"）—— ⚠️ **那个结论是错的**。
        #   `createSession` 确实**拒绝**"章节会话 + 指定题集"（设计如此，与后端同口径），
        #   但**不需要**它：这一章的可判分题数 ≤ 10 ⇒ **整章都会进会话** ⇒ qid 一定在里面。
        await _enter_session_at(b, base, si, ci)
        cells = await b.eval(
            """Array.from(document.querySelectorAll('[data-sheet-grid] button'))
                   .map((e) => e.getAttribute('data-cell-qid'))"""
        )
        need(qid in cells, f"这一章（可判分 ≤10）本该整章进会话，但 {qid} 不在：{cells}")
        await b.click("[data-sheet-grid] button", nth=cells.index(qid))
        await b.wait_for(
            f"""(() => {{ const e = document.querySelector('[data-qid]');
                          return !!e && e.getAttribute('data-qid') === {json.dumps(qid)}; }})()""",
            timeout=30,
            label="跳到那一道题",
        )
        await _pick_and_submit(b, right)
        await b.wait_text("答对了", timeout=40)
        row2 = await _wrong_row(b, qid)
        need(
            row2 is not None and row2.get("retry_correct") == 1,
            f"★ **章节练习**里答对**不该**给 `retry_correct` +1（只有重练答对才算）：{row2}",
        )
        need(row2.get("wrong_count") == 1, f"章节练习答对也不该动 `wrong_count`：{row2}")
        say("  ✅ ⑧ 反向对照：**章节练习**里答对同一道题 ⇒ `retry_correct` 仍是 1（不算重练答对）")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


# --------------------------------------------------------------- 场景：loop

_LOOP_NOTE = "loop 场景的笔记：四个态同时打在这一道题上"
_LOOP_FAKE_QID = "999999999999"


def _break_backup(bundle: dict[str, Any], out: Path, fake: str) -> None:
    """把备份里**四类记录**的题号改成 `fake` —— 构造"**题不在库里、记录还在**"这一态。

    ★ 为什么必须"造"：PWA 里让一道题消失的**唯一**途径是「换题库」，而 `importBank` 调的是
      `db.ts::wipeAll()` ⇒ **连用户数据一起清空**（界面也明写着"这会清空你的全部数据 / 清空并导入"）。
      ⇒ **"换包后记录仍在"在 PWA 里不可达**（`question_available=false` 这一态没有写入路径 ——
        硬约定 F：状态"可达" ⟺ 有任何接口能写入它）。
      可达的是"记录指向一道**库里没有**的题"，而它就是**题目下架**在本地的等价物
      ⇒ 正是**约定 T**（我的内容不随平台内容变化）在 PWA 里的落点。
    ★ 不改 `bank_version`（题库还是那一份）⇒ `importUserData` 的指纹校验照常通过。
    """
    pkg = json.loads(json.dumps(bundle))
    for store in ("wrong", "marks", "favorites", "notes"):
        for row in pkg.get("data", {}).get(store, []):
            if "question_id" in row:
                row["question_id"] = fake
    out.write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")


async def _rows_in_store(b: Any, store: str, qid: str) -> list[dict[str, Any]]:
    return [r for r in await _idb_all(b, store) if str(r.get("question_id")) == qid]


async def run_loop(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`loop` 场景：测**交互**（"同时"），不是把 5 个单场景再跑一遍。

    ★ 用户 2026-10-07 点明它测什么 —— 单场景是"一次一个用户态"，这里是**同时**：
      · 错题 + 收藏 + 标记 + 笔记**同时**打在同一道题上；
      · **四个列表**都能找到它（错题本 / 收藏 / 标记 / 笔记）；
      · 重练之后**四个 store** 的状态**同时**对（不是只看 UI —— `notes` 的教训）；
      · "题不在了、记录仍在"（约定 T）—— 用**可达**的构造（见 `_break_backup`）。
    """
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()
    dl = Path(tempfile.mkdtemp(prefix="e2e-pwa-loop-"))
    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        import_sec = await _ensure_bank(b, base, small, small_meta, timeout)
        pack = json.loads(small.read_text(encoding="utf-8"))
        catalog = _catalog(small)
        need(_LOOP_FAKE_QID not in catalog, f"假题号 {_LOOP_FAKE_QID} 竟然在包里 —— 换一个")
        pick = _small_chapter(pack, 10)
        need(pick is not None, "找不到「可判分题数 ≤ 10」的章节 —— 本场景的构造前提没了")
        si, ci = pick
        path = await _enter_session_at(b, base, si, ci)
        qid = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(qid in catalog, f"当前题号 {qid} 不在小包里")
        q = catalog[qid]
        right, all_labels = _labels_right(q), _labels_all(q)
        wrongs = [lb for lb in all_labels if lb not in set(right)]
        need(right and wrongs, f"这道题没法判分/没法答错：{q.get('type')} {q.get('answer')}")

        # ---- ① 锚：四类都还没有 ----
        first_w = await _list_state_wrong(b, base)
        first_f = await _collection(b, base, "favorite")
        first_m = await _collection(b, base, "mark")
        first_n = await _list_state(b, base)
        need(first_w["items"] == [], f"错题本本该是空的：{first_w['items']}")
        need(first_f["rows"] == [], f"收藏本该是空的：{first_f['rows']}")
        need(first_m["rows"] == [], f"标记本该是空的：{first_m['rows']}")
        need(first_n["rows"] == [], f"笔记本该是空的：{first_n['rows']}")
        say("  ✅ ① 锚（四个都空）：错题本 0 ｜ 收藏 0 ｜ 标记 0 ｜ 笔记 0")

        # ---- ② 答错 ⇒ 错题本有 ----
        await b.goto_ready(base + path)
        await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="答题页")
        await _pick_and_submit(b, [wrongs[0]])
        await b.wait_text("答错了", timeout=40)
        row0 = await _wrong_row(b, qid)
        need(
            row0 is not None and row0.get("wrong_count") == 1 and row0.get("retry_correct") == 0,
            f"答错之后库里的错题行不对：{row0}",
        )
        say(f"  ✅ ② 答错（点 {wrongs[0]}）⇒ 错题本有它（库里 wrong_count=1 / retry_correct=0）")

        # ---- ③ ★ **同时**在**同一道题**上打上 标记 + 收藏 + 笔记 ----
        await b.click("[data-flag-mark]")
        await _wait_flag(b, "mark", "1")
        await b.click("[data-flag-fav]")
        await _wait_flag(b, "fav", "1")
        await _open_panel(b)
        await b.type_text("[data-note-input]", _LOOP_NOTE)
        await b.wait_for("""!document.querySelector('[data-note-submit]').disabled""", timeout=20, label="保存可用")
        await b.click("[data-note-submit]")
        await b.wait_for(
            "document.querySelectorAll('[data-note-id]').length === 1", timeout=30, label="笔记 1 条"
        )
        st = await _panel_state(b)
        nid = st["ids"][0]
        need(
            await _flag(b, "mark") == "1" and await _flag(b, "fav") == "1" and st["badge"] == "1",
            f"三个态没**同时**在：mark/fav/badge = {await _flag(b, 'mark')}/"
            f"{await _flag(b, 'fav')}/{st['badge']}",
        )
        say(f"  ✅ ③ **同一道题**上三个态**同时**在：标记 1 ｜ 收藏 1 ｜ 笔记 1（id={nid[:12]}…）")

        # ---- ④ 重练答对 ⇒ retry_correct +1 ----
        await b.goto_ready(base + f"/practice/wrong/{qid}")
        await b.wait_for("""!!document.querySelector('[data-retry-this]')""", timeout=40, label="详情页")
        await b.click(f'[data-retry-this="{qid}"]')
        await b.wait_for(
            """location.pathname.startsWith('/practice/session/')""", timeout=40, label="重练会话"
        )
        await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="重练答题页")
        await _pick_and_submit(b, right)
        await b.wait_text("答对了", timeout=40)
        say("  ✅ ④ 重练答对 ⇒ 页面判「答对了」")

        # ---- ⑤ ★ **四个列表**都能找到它 ----
        w2 = await _list_state_wrong(b, base)
        need(w2["items"] == [qid], f"错题本找不到它：{w2['items']}")
        need(
            await b.eval(f"""!!document.querySelector('[data-retried="{qid}"]')"""),
            "错题本里没有「已重练 ✓」",
        )
        # 「只看已标记」筛选（与科目筛选正交）—— 它必须仍能筛出这道题
        await b.click("[data-marked-only]")
        await b.wait_for(
            f"""!!document.querySelector('[data-wrong-item="{qid}"]')""",
            timeout=30,
            label="只看已标记 ⇒ 仍能找到它",
        )
        say("  ✅ ⑤a 错题本：有它 + 「已重练 ✓」+ 「只看已标记」筛选后仍在")
        f2 = await _collection(b, base, "favorite")
        m2 = await _collection(b, base, "mark")
        n2 = await _list_state(b, base)
        need(f2["rows"] == [qid], f"收藏列表找不到它：{f2['rows']}")
        need(m2["rows"] == [qid], f"标记列表找不到它：{m2['rows']}")
        need(n2["rows"] == [nid], f"笔记列表找不到它：{n2['rows']}")
        say("  ✅ ⑤b 收藏 / 标记 / 笔记 三个列表都找得到它")

        # ---- ⑥ ★ 库里**四个 store** 的状态同时都对 ----
        r1 = await _wrong_row(b, qid)
        need(
            r1 is not None and r1.get("retry_correct") == 1 and r1.get("wrong_count") == 1,
            f"库里错题行不对：{r1}",
        )
        need(len(await _rows_in_store(b, "marks", qid)) == 1, "库里 marks 那行不对")
        need(len(await _rows_in_store(b, "favorites", qid)) == 1, "库里 favorites 那行不对")
        notes = await _rows_in_store(b, "notes", qid)
        need(len(notes) == 1 and not notes[0].get("deleted_at"), f"库里 notes 那行不对：{notes}")
        say("  ✅ ⑥ 库里四个 store 同时正确：wrong(retry=1/wrong=1) marks 1 favorites 1 notes 1（未删）")

        # ---- ⑦ ★ "题不在了、记录仍在"（**可达**的构造；换题库做不到，见 `_break_backup` 抬头）----
        await b.goto_ready(base + "/me/backup")
        await b.send("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(dl)})
        await b.click("[data-export]")
        await b.wait_for("""!!document.querySelector('[data-exported]')""", timeout=40, label="导出完成")
        src: Path | None = None
        for _ in range(40):
            hits = list(dl.glob("*.json"))
            if hits:
                src = hits[0]
                break
            await asyncio.sleep(0.25)
        need(src is not None, f"没等到导出的备份（{dl}）")
        broken = dl / "broken.json"
        _break_backup(json.loads(src.read_text(encoding="utf-8")), broken, _LOOP_FAKE_QID)
        await b.set_file_input("[data-import-file]", broken)
        await b.wait_for("""!!document.querySelector('[data-confirm-block]')""", timeout=40, label="确认块")
        need(
            not await b.eval("""!!document.querySelector('[data-bank-mismatch]')"""),
            "题库没换过却提示「题库不匹配」—— 指纹判据坏了",
        )
        await b.click("[data-confirm-import]")
        await b.wait_for("""!!document.querySelector('[data-imported]')""", timeout=timeout, label="恢复完成")
        w3 = await _list_state_wrong(b, base)
        need(w3["items"] == [_LOOP_FAKE_QID], f"★ 题不在了，错题本那条却没了：{w3['items']}")
        need(
            await b.eval("""!!document.querySelector('[data-question-gone]')"""),
            "★ 行还在，但没标「题目已下架」",
        )
        need(
            await b.eval(
                f"""document.querySelector('[data-wrong-item="{_LOOP_FAKE_QID}"]')
                     .getAttribute('data-question-available') === '0'"""
            ),
            "★ 题不在了，`data-question-available` 却是 1",
        )
        need(
            await b.eval(
                f"""document.querySelector('[data-retry="{_LOOP_FAKE_QID}"]').disabled === true"""
            ),
            "★ 题不在了，「重练」按钮却仍可点",
        )
        say("  ✅ ⑦a 错题本：行**还在** + 标「题目已下架」+ `question_available=0` + 重练按钮禁用")
        f3 = await _collection(b, base, "favorite")
        m3 = await _collection(b, base, "mark")
        need(f3["rows"] == [_LOOP_FAKE_QID], f"★ 收藏那条没了：{f3['rows']}")
        need(m3["rows"] == [_LOOP_FAKE_QID], f"★ 标记那条没了：{m3['rows']}")
        # ★ 收藏 / 标记**共用一套列表 UI**（只按 `kind` 切题源）⇒ 检查一次即可。
        # ⚠️ **必须在这里查**（人还在收藏页）—— 第一版把这一步放到了 `_list_state` 之后，
        #    那时人已经在**笔记页**，`[data-collect-row]` 根本不在 DOM 里 ⇒ 假红。
        #    这正是刚写进 MEMORY 的那条："每一步都要**自己确立前提**，不依赖上一步把状态留在了哪"。
        need(
            await b.eval(
                f"""(() => {{ const e = document.querySelector('[data-collect-row="{_LOOP_FAKE_QID}"]');"""
                """ return !!e && e.getAttribute('data-question-available') === '0'; })()"""
            ),
            "★ 收藏 / 标记 列表里那条没标 `data-question-available=0`",
        )
        n3 = await _list_state(b, base)
        need(len(n3["rows"]) == 1, f"★ 笔记那条没了：{n3['rows']}")
        need(
            await b.eval(
                f"""(() => {{ const e = document.querySelector('[data-note-row="{n3['rows'][0]}"]');"""
                """ return !!e && e.getAttribute('data-question-available') === '0'; })()"""
            ),
            "★ 笔记列表里那条没标 `data-question-available=0`",
        )
        say("  ✅ ⑦b 收藏 / 标记 / 笔记 三条**也还在**，且都标了「题目已下架」（约定 T）")
        say("       ⚠️ 说明：「换**题库**」做不到这一幕 —— `importBank` 调 `wipeAll()` 清**全部** store")
        say("          （界面也明写「这会清空你的全部数据」）⇒ `question_available=false` 无写入路径。")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


# ------------------------------------------------------- 场景：loopsession


async def run_loopsession(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`loopsession` 场景：**跨会话一致** —— 练习 A 标的题，练习 B 的**同一道题**也显示已标记。

    ★ 判据出处：`docs/33` §6 判据 6；`docs/34` §4.1 把它记成"漏做"，本场景补上。
      C 端对这条用的是**后端**用例（`apps/api/tests/test_c_end_flags.py::test_mark_is_cross_session…`）；
      PWA 零后端 ⇒ 只能在**浏览器层**验 —— 本场景就是它的对应物。

    ★★ **判据形状照抄 C 端那条**（这是关键，别改）：**先把两个会话都建好**，
      在**会话 B** 里断"本来是 0" → 在**会话 A** 里标记 → **回会话 B** 断"变成 1"。
      ⚠️ 只在 B 里断一次 1 是**不够**的：那时"跨会话真的通了"与"这个实现给所有题都显示已标记"
        **在单次读上长得一模一样**；只有**同一条路径上的 0 → 1** 才把这两种解释分开
        （= 用户说的"会话 B 里先断本来是 0，再断变成 1"）。

    ★ **为什么两个会话必含同一题**：挑「可判分题数 ≤ 10」的章节（页面写死 `SESSION_SIZE = 10`）
      ⇒ **整章都会进会话**（与 `wrong` ⑧ 同一个构造，不是新技巧）；且两个会话都**一题未答**
      ⇒ 抽题顺序（"没做过的在前" + 按 id）一致 ⇒ 落点是同一道题。落点对不对用
      `_back_to_session` 断言（它**自己确立前提**，失败了就明确报"前提没了"，不静默通过）。

    ★ 本场景**只验这一件事**（用户 2026-10-07："不要做成综合大场景"）。
    """
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()
    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        import_sec = await _ensure_bank(b, base, small, small_meta, timeout)
        pack = json.loads(small.read_text(encoding="utf-8"))
        catalog = _catalog(small)
        pick = _small_chapter(pack, 10)
        need(pick is not None, "找不到「可判分题数 ≤ 10」的章节 —— 本场景的构造前提没了")
        si, ci = pick

        # ---- 先建**两个**会话（顺序不能反：锚要在"标记"发生**之前**取到）----
        path_a = await _enter_session_at(b, base, si, ci)
        qid = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(qid in catalog, f"当前题号 {qid} 不在小包里 —— 库里的题和小包对不上？")
        path_b = await _enter_session_at(b, base, si, ci)
        need(
            path_b != path_a,
            f"两次进入同一章节拿到的是**同一个** session（{path_a}）—— 那不叫跨会话，判据失效",
        )
        # ★ 会话 B 的落点必须**就是** qid —— 否则"两个会话含同一题"这个前提不成立。
        await _back_to_session(b, base, path_b, qid)
        sid_a, sid_b = path_a.rsplit("/", 1)[-1][:8], path_b.rsplit("/", 1)[-1][:8]
        say(f"  · 两个会话已就位：A={sid_a}… ｜ B={sid_b}…（同一章、都含同一道题 {qid}）")

        # ---- ① ★ 可证伪锚（**在会话 B 里**、"标记"之前）：本来是 0 ----
        need(
            await _flag(b, "mark") == "0",
            f"会话 B：题目 {qid} 的「标记」按钮**本来**不是 0 —— 锚不成立，"
            "后面那条「变成 1」就不说明问题",
        )
        need(
            await _rows_in_store(b, "marks", qid) == [],
            f"会话 B：库里本来就有 {qid} 的标记行 —— 锚不成立",
        )
        say(f"  ✅ ① 锚（会话 B，变化之前）：题目 {qid} 的标记 = 0（按钮 + 库都是）")

        # ---- ② 在**会话 A** 里标记 ⇒ 1 ----
        await _back_to_session(b, base, path_a, qid)
        was = await _flag(b, "mark")
        need(was == "0", f"会话 A：标记按钮本来不是 0（{was!r}）")
        await b.click("[data-flag-mark]")
        await _wait_flag(b, "mark", "1")
        need(
            len(await _rows_in_store(b, "marks", qid)) == 1,
            "点了标记，按钮翻了 1，但**库里没有那一行**",
        )
        say(f"  ✅ ② 在**会话 A** 里标记 {qid} ⇒ 按钮 1、库里 1 行")

        # ---- ③ ★★ 核心判据：**回会话 B**，同一道题也是 1 ----
        await _back_to_session(b, base, path_b, qid)
        need(
            await _flag(b, "mark") == "1",
            f"★ **跨会话不一致**：会话 A 标记的题（{qid}），在会话 B 里显示未标记 —— "
            "标记很可能被存成了**会话级**的（`practice_items.marked` 那一族），而不是题目级",
        )
        need(
            len(await _rows_in_store(b, "marks", qid)) == 1,
            "会话 B：按钮说已标记，但**库里那行不在**（UI 与库两层不一致）",
        )
        say(f"  ✅ ③ ★ 跨会话一致：会话 A 标的 {qid}，在**另一个会话**（B={sid_b}…）里也是已标记")

        # ---- ④ 反向：在**会话 B** 取消 ⇒ 0 ----
        await b.click("[data-flag-mark]")
        await _wait_flag(b, "mark", "0")
        need(
            await _rows_in_store(b, "marks", qid) == [],
            "在会话 B 里取消了标记，**库里那行还在**",
        )
        say("  ✅ ④ 反向（在会话 B 取消）：按钮 0、库里的行也没了")

        # ---- ⑤ 反方向同样跨会话：回**会话 A**，也是 0 ----
        await _back_to_session(b, base, path_a, qid)
        need(
            await _flag(b, "mark") == "0",
            "★ 在会话 B 取消了标记，回到会话 A 却**仍显示已标记** —— 反方向也不跨会话",
        )
        say(f"  ✅ ⑤ 反向也跨会话：会话 B 的取消，在会话 A（{sid_a}…）里同样看得到（0）")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


#: ★ 场景注册表（不变量 11 的**第三处**清单）—— 名字 → 协程。
#: 值统一签名 `(base, bank, bank_meta, other, timeout) -> dict[str, float]`（返回各段耗时），
#: 于是**加场景不用改调度代码**（用户 2026-10-07："先推广不变量 11，再加 5 个场景"）。
#: ★ 名字必须是纯小写字母数字 —— 门禁的 `_e2e_names()` 只认那个形状。
drivers = {
    "setup": run_setup,
    "marks": run_marks,
    "fav": run_fav,
    "notes": run_notes,
    "wrong": run_wrong,
    "loop": run_loop,
    "loopsession": run_loopsession,
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
        choices=["setup", "marks", "fav", "notes", "wrong", "loop", "loopsession"],
        help=(
            "走查场景：setup（导入题库）/ marks / fav / notes / wrong（错题重练）/ "
            "loop（四态交叉）/ loopsession（跨会话一致）"
        ),
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
    except (Failure, TimeoutError, RuntimeError) as e:
        # ★ 底座的 `wait_for` 抛 `TimeoutError`、`eval`/`click` 抛 `RuntimeError`。
        #   只接 `Failure` 的话，**最常见的一类失败（等不到）会变成裸 traceback**：
        #   退出码是对的，但"哪一步、等的是什么"要去翻栈（2026-10-07 实测踩到）。
        say("")
        say(f"==> ❌ 失败：{type(e).__name__}: {e}")
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
