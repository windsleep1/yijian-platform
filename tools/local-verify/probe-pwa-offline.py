"""验 C 端 PWA 的**两个核心承诺**：① 静态文件未登录也能取到；② **断网打开不白屏**。

## 为什么单独一个探针（而不是塞进 E2E）

`e2e-web.py` 走的是"登录 → 刷题"那条业务主线，它**验不到 SW** ——
因为 `sw-register.tsx` 只 `NODE_ENV === "production"` 才注册，而本机只能跑 `--dev`
（判据见 `docs/28` §3.5：那是"已有 E2E 不回归"的**结构性**保证）。
⇒ SW 的行为必须**单独验**，否则"离线不白屏"就只剩真机一条路 ——
而**只能真机验 = 等于没验过**（不可反复跑、不能加断言、无法回归）。

## 它验什么（每一步都是一个断言，任何一步失败就红）

  ① **静态可达性**（HTTP 层，**不带任何 cookie**）：`/sw.js`、`/manifest.webmanifest`、
     `/offline.html` 必须 **直接 200**。⚠️ 关键是**不跟随重定向**：
     中间件把它们 302 到 `/login` 时，跟随重定向会得到"200 + 登录页 HTML"，
     看起来像成功（`curl` 默认跟随也一样）。
     ⇒ 这一步直接验 `docs/28` §1.1 那个"**登录状态下看不出来**"的缺陷。
  ② **SW 真的注册上了**：断言 `navigator.serviceWorker.controller` 非空 ——
     这是后面的**可证伪锚**：没有它，③ 的"离线页出现了"有一万种解释
     （比如页面根本就是缓存的旧 HTML）。
  ③ **离线页真的被缓存了**：`caches.match('/offline.html')` 命中。
  ④ **断网后导航 ⇒ 出离线页**（不是白屏、不是浏览器错误页、不是登录页）。
  ⑤ **离线页是自包含的**：断网下**内联样式仍然生效**（背景色读得出来）。
     不验这一步的话，"不白屏"可能只是"一张没样式的白页"。

## 跑法

    PYTHONUTF8=1 python tools/local-verify/probe-pwa-offline.py            # 自己起 dev server
    PYTHONUTF8=1 python tools/local-verify/probe-pwa-offline.py --url http://127.0.0.1:3001

⚠️ 本机只能 `--dev`（`next build` 被宿主删除保护拦，见 `e2e-web.py` 抬头）。
⚠️ 起服务与用它**必须在同一次调用里**（前台结束会回收进程树）。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
WEB_DIR = REPO / "apps" / "web"

#: 断网后应当看到的字。**取 `.html` 里的 `<h1>` 原文**，不另起一处 ——
#: 改文案时这里会红，从而**逼迫**同步（"文案与判据绑在同一处真相"）。
OFFLINE_MARK = "当前离线"


def say(msg: str) -> None:
    print(msg, flush=True)


def _load(mod_name: str, path: Path) -> Any:
    """按**路径**导入（这两个文件名带连字符，`import` 语句用不了）。"""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Failure(AssertionError):
    pass


def need(cond: object, msg: str) -> None:
    if not cond:
        raise Failure(msg)


# --------------------------------------------------------------------- ① HTTP 层


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """**不跟随重定向** —— 这一步的全部意义就在这里。

    ⚠️ 跟随重定向会把"302 到登录页"变成"200 + 登录页 HTML"，
    于是"静态文件可达吗"这个问题被答成"可达"（**假绿**）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _strip_html_comments(text: str) -> str:
    """抹掉 `<!-- … -->` 再判断。

    ★★ 为什么这一步**不能省**（2026-10-02 同一批次里两头都撞到）：
      离线页的注释里写着"必须自包含、**不引 `/_next/*`**"—— 于是
      "body 里不得出现 `/_next/`"这条断言**被它自己的说明文字弄红**（假红）。
      而同一批次的 `manifest.ts` 里，注释里的 `display: "standalone"` 让
      "必须有 standalone"那条断言**假绿**。
      ⇒ 判据：**凡"文件里必须有 / 不得有 X"的检查，先剥注释** ——
        注释是写给人的，不是产物的一部分；它同时能造成假绿和假红。
      ⚠️ 边界：这只处理 HTML 注释。字符串字面量里的路径（真会被 fetch 的）**不抹** ——
        那才是这条断言真正要抓的东西。
    """
    import re

    return re.sub(r"<!--.*?-->", " ", text, flags=re.S)


def probe_http(base: str) -> list[str]:
    """未登录取三个静态文件。返回人类可读的结果行；失败直接抛 `Failure`。"""
    opener = urllib.request.build_opener(_NoRedirect)
    lines: list[str] = []

    # 期望 (路径, 该怎么判断它"是我们要的那个东西")
    checks: list[tuple[str, str]] = [
        ("/sw.js", "js"),
        ("/manifest.webmanifest", "json"),
        ("/offline.html", "offline-html"),
    ]
    for path, kind in checks:
        url = base + path
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        try:
            with opener.open(req, timeout=15) as r:
                status, body = r.status, r.read().decode("utf-8", "replace")
                ctype = r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:  # 302 会走到这里（我们不跟随）
            raise Failure(
                f"{path} ⇒ HTTP {e.code}"
                + (f"（Location: {e.headers.get('Location')}）" if e.headers else "")
                + "\n      ⇒ 它被中间件拦去登录页了。未登录访客拿不到它 ⇒ "
                "SW 注册失败 / manifest 取不到 / 离线页被抓成登录页。\n"
                "      ★ 这个缺陷**在已登录状态下看不出来**（有 cookie 就放行）——"
                "修法：把它加进 `middleware.ts::PUBLIC_PREFIXES`。"
            ) from None

        need(status == 200, f"{path} ⇒ HTTP {status}（期望 200）")

        if kind == "js":
            need(
                "javascript" in ctype.lower(),
                f"{path} 的 Content-Type 是 {ctype!r} —— 不是 JS。"
                "（典型症状：被重定向到了 HTML 页面。）",
            )
            need("addEventListener" in body, f"{path} 的内容不像 Service Worker")
            need("CACHE_PREFIXES" in body, f"{path} 里读不到白名单常量 —— 是不是拿错了文件")
            lines.append(f"  ✅ {path:24} 200 · {ctype.split(';')[0]} · {len(body)} 字节")
        elif kind == "json":
            import json

            data = json.loads(body)
            need(data.get("display") == "standalone", f"manifest.display = {data.get('display')!r}")
            icons = data.get("icons") or []
            need(
                any(i.get("sizes") == "512x512" for i in icons),
                "manifest 里没有 512 的图标（Chrome 不会显示「安装」）",
            )
            lines.append(f"  ✅ {path:24} 200 · display=standalone · {len(icons)} 个图标")
        else:
            # ★ 先剥 HTML 注释再判（见 `_strip_html_comments`：注释是写给人的，不是产物）
            html = _strip_html_comments(body)
            need(
                OFFLINE_MARK in html,
                f"{path} 的内容里没有「{OFFLINE_MARK}」—— 它不是我们的离线页（是不是登录页？）",
            )
            need(
                "/_next/" not in html,
                f"{path} 引用了 `/_next/*` —— 离线页**必须自包含**，"
                "否则断网时它的样式/脚本同样拿不到（表现为'没有样式的白屏'）。",
            )
            lines.append(f"  ✅ {path:24} 200 · 含「{OFFLINE_MARK}」· 自包含（不引 /_next/*）")

    return lines


# --------------------------------------------------------------------- ②~⑤ 浏览器层


#: CDP 的"断网"参数。`latency/throughput` 必须一起给，只给 `offline` 有些版本不认。
OFFLINE_PARAMS = {
    "offline": True,
    "latency": 0,
    "downloadThroughput": 0,
    "uploadThroughput": 0,
}


class SwSession:
    """直连 **Service Worker 的 CDP target**（它和页面是**两个** target）。

    ★★ 为什么非要有这个类（2026-10-02 实测踩到，探针第一版就是栽在这里）：
      `Network.emulateNetworkConditions` 是**按 target 生效**的。只给页面目标开断网 ⇒
      **SW 发起的 `fetch` 仍然通网** ⇒ `sw.js` 的 `navigate` 分支里
      `await fetch(req)` 会**成功返回真页面**，于是"离线兜底"分支永远不被走到。
      症状特别像"SW 写错了"（离线导航拿到的还是登录页），而真实原因是**测试条件没造对** ——
      这正是"判据要先能被证伪"要防的那类：**它测的不是它以为的东西**。
      ⇒ 判据：**要给某个上下文造条件，就得进那个上下文**（同族：硬约定 L「业务代码在哪个
        进程跑就在哪个进程插桩」）。
    """

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self._id = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=20))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise Failure(f"SW target 的 {method} 失败：{msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr: str) -> Any:
        r = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
        )
        return (r.get("result") or {}).get("value")

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass


async def attach_sw_target(browser: Any) -> SwSession:
    """找到 SW 的 target 并连上它（找不到就直接失败 —— 不能"假装断网了"）。"""
    import httpx
    import websockets

    listing = f"http://127.0.0.1:{browser.port}/json/list"
    target: dict | None = None
    for _ in range(30):
        try:
            for t in httpx.get(listing, timeout=3).json():
                if t.get("type") == "service_worker" and t.get("webSocketDebuggerUrl"):
                    target = t
                    break
        except Exception:
            pass
        if target:
            break
        await asyncio.sleep(0.5)
    need(
        target,
        "在 `/json/list` 里找不到 `type == service_worker` 的 target —— "
        "SW 没跑起来？（没有它就无法给 SW 造断网条件，本探针拒绝假装通过）",
    )
    assert target is not None
    ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024)
    return SwSession(ws)


#: 失败现场采集。**每一项都单独 try** —— 因为失败时的页面可能是
#: **浏览器的错误页**（那种上下文里没有 `caches`、甚至没有 `document.body`），
#: 而"诊断自己崩了"会把最有用的信息（**页面到底显示了什么**）盖成一段 traceback。
#: ⚠️ 第一版就是在这里崩的（`ReferenceError: caches is not defined`）——
#:    同族：硬约定 H 的「诊断还要问：它的输出我读得到吗」。
_DIAG_JS = """(async () => {
  const r = {};
  try { r.href = location.href; } catch (e) { r.href = '<' + e.message + '>'; }
  try { r.title = document.title; } catch (e) { r.title = null; }
  try { r.text = (document.body ? document.body.innerText : '').slice(0, 160); }
  catch (e) { r.text = null; }
  try { r.controller = !!navigator.serviceWorker.controller; } catch (e) { r.controller = null; }
  try { r.onLine = navigator.onLine; } catch (e) { r.onLine = null; }
  try { r.caches = (await caches.keys()).join(','); } catch (e) { r.caches = '<' + e.message + '>'; }
  return r;
})()"""


async def _diag(browser: Any) -> dict:
    try:
        got = await browser.eval(_DIAG_JS)
        return got if isinstance(got, dict) else {}
    except Exception as e:  # noqa: BLE001 —— 诊断**不准**让原错误消失
        return {"text": f"<诊断本身也失败了：{type(e).__name__}: {e}>"}


async def probe_browser(base: str) -> int:
    """②~⑤。**边跑边打**（不攒着）—— 攒着的话，一旦中途失败，
    "前面哪几步是通过的"这个信息就全丢了，而那恰恰是定位问题最需要的东西
    （同族：硬约定 H 的「诊断还要问：它的输出我读得到吗」）。"""
    cdp = _load("cdp_browser", REPO / "tools" / "local-verify" / "cdp_browser.py")
    steps = 0

    async with cdp.Browser(mobile=True, width=375, height=667) as b:
        # --- ② 打开登录页并**手动**注册 SW（prod 才自动注册，本机是 dev）---
        await b.goto_ready(base + "/login")
        reg = await b.eval(
            """(async () => {
                 const r = await navigator.serviceWorker.register('/sw.js');
                 await navigator.serviceWorker.ready;
                 return {scope: r.scope, active: !!r.active};
               })()"""
        )
        need(reg and reg.get("active"), f"Service Worker 没有进入 active：{reg}")
        say(f"  ✅ SW 注册成功（scope = {reg['scope']}）")
        steps += 1

        # ★★ **可证伪锚**：重新加载后必须被 SW 接管。
        #    没有这一步，"离线页出现了"就无法归因 —— 也可能是浏览器自己的缓存。
        await b.goto_ready(base + "/login", hydrate=False)
        await b.wait_for("navigator.serviceWorker.controller", timeout=20, label="SW 接管页面")
        say("  ✅ 页面已被 SW 接管（`serviceWorker.controller` 非空）")
        steps += 1

        # --- ③ 离线页是否**真的**（且内容正确）在缓存里 ---
        # ⚠️ 只断言"非空"是不够的：把**登录页**缓存进 `/offline.html` 也是非空，
        #    而那正是"中间件把离线页重定向走"时的真实症状（`docs/28` §1.1）。
        cached = await b.eval(
            """(async () => {
                 const c = await caches.match('/offline.html');
                 return c ? await c.text() : '';
               })()"""
        )
        need(bool(cached), "缓存里没有 `/offline.html` —— install 阶段的预缓存没成功")
        need(
            OFFLINE_MARK in _strip_html_comments(cached),
            "缓存里的 `/offline.html` **内容不对**（不含「" + OFFLINE_MARK + "」）—— "
            "多半是它在 install 时被抓成了登录页（被重定向）。开头："
            + repr(cached[:120]),
        )
        say(f"  ✅ `/offline.html` 已在缓存里且内容正确（{len(cached)} 字节）")
        steps += 1

        # --- ④ 断网（**页面 + Service Worker 两个 target 都要**）+ 导航 ⇒ 离线页 ---
        # ★ 先把自己弄成"离线"，并且**自测**真的离线了（页面内 + SW 内各测一次）。
        #   不测的话，这一步是空转：失败时看到的只是"离线页没出现"，
        #   而原因可能是"网根本没断" —— 报错会指向错误的方向。
        await b.send("Network.enable")
        await b.send("Network.emulateNetworkConditions", OFFLINE_PARAMS)

        page_off = await b.eval(
            f"""(async () => {{
                 try {{ await fetch({base!r} + '/login', {{cache: 'no-store'}}); return 'ONLINE'; }}
                 catch (e) {{ return 'OFFLINE'; }}
               }})()"""
        )
        need(
            page_off == "OFFLINE",
            f"CDP 的断网模拟对**页面**没生效（fetch 仍然成功）⇒ 后面的断言没有意义。得到 {page_off!r}",
        )

        sw = await attach_sw_target(b)
        try:
            await sw.call("Runtime.enable")
            await sw.call("Network.enable")
            await sw.call("Network.emulateNetworkConditions", OFFLINE_PARAMS)
            # ★★ 在 **SW 的上下文里**再测一次 —— 这才是决定性的那一次：
            #    `navigate` 分支里的 `fetch(req)` 就是在这个上下文里发出的。
            sw_off = await sw.eval(
                f"""fetch({base!r} + '/login', {{cache: 'no-store'}})
                      .then(function () {{ return 'ONLINE'; }})
                      .catch(function () {{ return 'OFFLINE'; }})"""
            )
            need(
                sw_off == "OFFLINE",
                f"断网模拟对 **Service Worker 上下文**没生效（它自己的 fetch 仍然成功：{sw_off!r}）\n"
                "      ⇒ `navigate` 分支会拿到真响应 ⇒ 这条判据其实什么都没验。"
                "（CDP 的 `emulateNetworkConditions` 是**按 target** 生效的，"
                "SW 是独立 target。）",
            )
            say("  ✅ 断网已生效 —— 页面与 **Service Worker 两个 target** 都自测过（各自 fetch 都失败）")
            steps += 1

            await b.goto(base + "/practice")
            got = ""
            try:
                await b.wait_text(OFFLINE_MARK, timeout=25)
                got = OFFLINE_MARK
            except TimeoutError:
                pass

            if got != OFFLINE_MARK:
                # ★ 失败要**把现场端出来**（而不是只说"没看到"）：下面几项能直接区分
                #   "SW 没接管" / "缓存里没那一项" / "navigate 分支没兜住"。
                d = await _diag(b)
                raise Failure(
                    f"断网导航后**没有出现**「{OFFLINE_MARK}」。\n"
                    f"      落在了：{d.get('href')!r}（title={d.get('title')!r}）\n"
                    f"      页面开头：{(d.get('text') or '')!r}\n"
                    f"      现场：controller={d.get('controller')} · onLine={d.get('onLine')} · "
                    f"缓存={d.get('caches')}\n"
                    "      ⇒ 三种成因方向不同：\n"
                    "        controller=false ⇒ SW 没接管这次导航（注册/作用域问题）；\n"
                    "        缓存里没有 `/offline.html` ⇒ 预缓存丢了（看 install 分支）；\n"
                    "        两者都正常 ⇒ `navigate` 分支没兜住（看 `sw.js` 规则 ④）。"
                )
            say("  ✅ 断网导航 ⇒ 落到离线页（不是白屏、不是浏览器错误页、不是登录页）")
            steps += 1

            # --- ⑤ 自包含：内联样式在断网下仍然生效 ---
            style = await b.eval(
                """(() => {
                     const s = getComputedStyle(document.body);
                     return {bg: s.backgroundColor,
                             h1: (document.querySelector('h1')||{}).innerText || ''};
                   })()"""
            )
            need(
                style and style.get("bg") == "rgb(255, 255, 255)",
                f"离线页的背景色不是白色（得到 {style.get('bg')!r}）—— 内联样式没生效 ⇒ "
                "它其实依赖了某个网络资源（那就不是'自包含'）",
            )
            need(style.get("h1") == OFFLINE_MARK, f"离线页的 <h1> 是 {style.get('h1')!r}")
            say(f"  ✅ 离线页渲染正常（h1=「{style['h1']}」· 内联样式生效 · 不是白屏）")
            steps += 1
        finally:
            await sw.close()

        # --- 收尾：注销 SW、清缓存（**必须做**）---
        # 本机 dev 环境里留下一个 SW，会缓存旧 chunk，把后续任何 dev 调试变成
        # "我改了代码没生效"（而那种症状会指向错误的方向）。
        cleaned = await b.eval(
            """(async () => {
                 const rs = await navigator.serviceWorker.getRegistrations();
                 for (const r of rs) await r.unregister();
                 const keys = await caches.keys();
                 for (const k of keys) await caches.delete(k);
                 return {unregistered: rs.length, caches: keys.length};
               })()"""
        )
        say(
            f"  ✅ 已清理现场（注销 {cleaned['unregistered']} 个 SW · 清 {cleaned['caches']} 个缓存）"
        )
    return steps


def _fake_args(base: str, port: int, dev: bool, node: str) -> argparse.Namespace:
    return argparse.Namespace(dev=dev, node=node, port=port, boot_timeout=90, web_base=base)


def find_node() -> str:
    """★ **不写死本机路径**（2026-10-07 同族清理）：`Path.home()` 起手 + glob 取最新版本。"""
    managed = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    for cand in sorted(managed.glob("*/node.exe"), reverse=True):
        return str(cand)
    raise SystemExit("[probe] ✗ 找不到 node.exe —— 装 node，或设 PATH")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="probe-pwa-offline", description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="已在跑的前端地址（给了就不自己起服务）")
    ap.add_argument("--port", type=int, default=3001)
    ap.add_argument("--dev", action="store_true", default=True, help="（本机只能 dev）")
    args = ap.parse_args(argv)

    e2e = _load("e2e_web", REPO / "tools" / "local-verify" / "e2e-web.py")

    base = args.url or f"http://127.0.0.1:{args.port}"
    proc = None
    try:
        if not args.url:
            # 与 e2e-web.py 同款：先把 `.next` **改名挪走**（宿主删除保护会拦递归删除）。
            moved = e2e.isolate_next_dir(WEB_DIR)
            if moved:
                say(f"[probe] `.next` 已挪到 {moved.name}（不删，见项目约定 R）")
            env = dict(os.environ)
            env["NODE_ENV"] = "development"
            proc = e2e.start_web(_fake_args(base, args.port, True, find_node()), WEB_DIR, env)

        say("=== ① 静态可达性（**未登录**，不跟随重定向）===")
        for line in probe_http(base):
            say(line)

        say("=== ②~⑤ 浏览器层（真注册 SW + CDP 断网）===")
        steps = asyncio.run(probe_browser(base))
        say("")
        say(f"==> ① 的 3 项 + ②~⑤ 的 {steps} 项，全部通过 ✅")
        say("    （未登录可达 · SW 已接管 · 离线页已缓存且内容正确 · 断网出离线页 · 自包含）")
        return 0
    except Failure as e:
        say("")
        say(f"==> ❌ 失败：{e}")
        return 1
    finally:
        if proc is not None:
            # 起/停必须在**同一次调用**里：前台结束会回收进程树（本机踩过）。
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
            say("[probe] dev server 已停")


if __name__ == "__main__":
    sys.exit(main())
