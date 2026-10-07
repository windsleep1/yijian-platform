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


async def run_marks(
    base: str, small: Path, small_meta: dict[str, Any], other: Path, timeout: int
) -> dict[str, float]:
    """`marks` 场景：标记一道题 ⇒ 按钮翻 1 ⇒ 刷新仍在 ⇒ 列表能筛出（**并对照收藏仍是 0**）。

    ★ 判据的顺序就是用户定的"**先断未变化态，再断变化**"：
      "标记本来就有"和"真的记了账"长得一模一样 ⇒ 变化**之前**必须先断一次。
    """
    cdp = _load("cdp_browser", CDP_BROWSER)
    t0 = time.time()
    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        import_sec = await _ensure_bank(b, base, small, small_meta, timeout)
        catalog = _catalog(small)

        # ---- ① 可证伪锚（变化之前）----
        before = await _collection(b, base, "mark")
        need(
            before["rows"] == [],
            f"还没标记过，「标记」列表却有 {len(before['rows'])} 行：{before['rows']}",
        )
        need(
            "0" in before["total"],
            f"空列表时总数文案不对（期望「共 0 道标记」）：{before['total'].strip()!r}",
        )
        say(f"  ✅ ①a 锚：标记列表本来是空的（{before['total'].strip()}）")

        qid, path = await _enter_session(b, base)
        need(qid in catalog, f"当前题号 {qid} 不在小包里 —— 库里的题和小包对不上？")
        need(
            await _flag(b, "mark") == "0",
            f"刚进来的题（{qid}）标记按钮不是 0 —— 锚不成立，后面的「变 1」就不说明问题",
        )
        say(f"  ✅ ①b 锚：题目 {qid} 的标记按钮本来是 0")

        # ---- ② 点标记 ----
        await b.click("[data-flag-mark]")
        await _wait_flag(b, "mark", "1")
        say("  ✅ ② 点「标记」⇒ 按钮翻成 1")

        # ---- ③ 刷新后仍在（读的是 IndexedDB，不是内存态）----
        await b.goto_ready(base + path)
        await b.wait_for("""!!document.querySelector('[data-qid]')""", timeout=40, label="刷新后答题页")
        again = await b.eval("""document.querySelector('[data-qid]').getAttribute('data-qid')""")
        need(
            again == qid,
            f"刷新后回到的是**另一道题**（{again} ≠ {qid}）—— 本步的前提没了，不能算通过",
        )
        need(await _flag(b, "mark") == "1", "刷新后标记没了 —— 没落到 IndexedDB")
        say("  ✅ ③ 刷新后仍在（IndexedDB 持久化）")

        # ---- ④ 列表能筛出 ----
        after = await _collection(b, base, "mark")
        need(
            after["rows"] == [qid],
            f"「标记」列表应该有且仅有 {qid}：{after['rows']}（总数文案 {after['total'].strip()!r}）",
        )
        sid = str(catalog[qid]["subject_id"])
        need(sid in after["facets"], f"分面里没有这道题的科目 {sid}：{after['facets']}")
        filtered = await _collection(b, base, "mark", facet=sid)
        need(filtered["rows"] == [qid], f"按科目 {sid} 筛完不该变：{filtered['rows']}")
        unfiltered = await _collection(b, base, "mark", facet="all")
        need(unfiltered["rows"] == [qid], f"点回「全部」不该变：{unfiltered['rows']}")
        say(f"  ✅ ④ 列表能筛出（共 1 道；科目 {sid} 与「全部」筛出来的都是它 {qid}）")

        # ---- ⑤ 对照：收藏列表仍是 0 ----
        # ★ 少了这一条，"两个页签其实读的是同一份数据"的实现也能过 ④
        #   （那正是"二元判据只验一头"的老毛病）。
        fav = await _collection(b, base, "favorite")
        need(
            fav["rows"] == [],
            f"只打了**标记**，「收藏」列表却有 {len(fav['rows'])} 行：{fav['rows']} "
            "—— 两个页签切的是同一份数据？",
        )
        say("  ✅ ⑤ 对照：收藏列表仍是 0 ⇒ 两个页签真的在切**不同的题源**")

    return {"import_sec": import_sec, "scenario_sec": time.time() - t0}


#: ★ 场景注册表（不变量 11 的**第三处**清单）—— 名字 → 协程。
#: 值统一签名 `(base, bank, bank_meta, other, timeout) -> dict[str, float]`（返回各段耗时），
#: 于是**加场景不用改调度代码**（用户 2026-10-07："先推广不变量 11，再加 5 个场景"）。
#: ★ 名字必须是纯小写字母数字 —— 门禁的 `_e2e_names()` 只认那个形状。
drivers = {
    "setup": run_setup,
    "marks": run_marks,
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
        choices=["setup", "marks"],
        help="走查场景：setup（导入题库 + 对账 + 换题库二次确认）/ marks（标记 + 列表筛选）",
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
