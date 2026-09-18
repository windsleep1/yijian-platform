"""极简 CDP 浏览器驱动（无第三方浏览器库，只用 `websockets` + `httpx`）。

为什么自己写：本机 `agent-browser` 的守护进程在这个 shell 环境里起不来（无输出卡住），
而 Playwright / Selenium 都没装。但 Chrome 本体已经装好了
（`agent-browser install` 下的 `~/.agent-browser/browsers/chrome-*/chrome.exe`），
直接用 **CDP**（Chrome DevTools Protocol）驱动它是最短路径。

用途：E2E 走查 + 截图（`apps/admin/docs/screenshots/`）。

设计取舍：
- **交互走 `Runtime.evaluate` 注入 JS**，而不是 CDP 的 Input 域。
  对 React 受控组件来说，JS 里"用 native setter 赋值 + 派发 input 事件"比
  模拟真实键盘输入更稳（不受焦点/滚动位置影响）；Radix 的下拉要补 pointerdown。
- **截图走 `Page.captureScreenshot`**，全页用 `captureBeyondViewport`。
- 视口固定 1440×900：截图要能进文档，尺寸得稳定。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

CHROME_CANDIDATES = [
    Path.home() / ".agent-browser" / "browsers",
]


def find_chrome() -> str:
    """在 agent-browser 的浏览器目录里找一个 chrome.exe（取版本号最大的）。"""
    for root in CHROME_CANDIDATES:
        if not root.exists():
            continue
        exes = sorted(root.glob("chrome-*/chrome.exe"))
        if exes:
            return str(exes[-1])
    # 退而求其次：系统安装的 Chrome
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).exists():
            return p
    raise RuntimeError("找不到 chrome.exe，请先运行 agent-browser install")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Browser:
    """同步外观、内部 asyncio 的极简浏览器。"""

    def __init__(self, *, width: int = 1440, height: int = 900, headless: bool = True):
        self.width, self.height, self.headless = width, height, headless
        self.port = _free_port()
        self.profile = Path(tempfile.mkdtemp(prefix="cdp-profile-"))
        self.proc: subprocess.Popen | None = None
        self.ws: Any = None
        self._id = 0

    # ---------------------------------------------------------------- 生命周期

    async def __aenter__(self) -> "Browser":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def start(self) -> None:
        chrome = find_chrome()
        args = [
            chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            f"--window-size={self.width},{self.height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--lang=zh-CN",
            "--accept-lang=zh-CN",
            "about:blank",
        ]
        if self.headless:
            args.insert(1, "--headless=new")
            args.insert(2, "--disable-gpu")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 等 devtools 端口起来
        base = f"http://127.0.0.1:{self.port}"
        target = None
        for _ in range(80):
            try:
                with httpx.Client(timeout=2) as c:
                    lst = c.get(f"{base}/json/list").json()
                for t in lst:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        target = t
                        break
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not target:
            raise RuntimeError("Chrome devtools 端口未就绪")

        self.ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False},
        )

    async def close(self) -> None:
        try:
            if self.ws is not None:
                await self.ws.close()
        except Exception:
            pass
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        shutil.rmtree(self.profile, ignore_errors=True)

    # ---------------------------------------------------------------- 原语

    async def send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=60)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 失败：{msg['error']}")
                return msg.get("result", {})

    async def goto(self, url: str) -> None:
        await self.send("Page.navigate", {"url": url})

    async def eval(self, expr: str) -> Any:
        r = await self.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
        )
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS 异常：{r['exceptionDetails'].get('text')} —— {expr[:120]}")
        return r.get("result", {}).get("value")

    async def wait_for(self, expr: str, *, timeout: float = 20.0, label: str = "") -> None:
        """轮询 JS 表达式直到为真。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if await self.eval(f"(() => !!({expr}))()"):
                    return
            except Exception:
                pass
            await asyncio.sleep(0.2)
        raise TimeoutError(f"等待超时（{label or expr}）")

    async def wait_text(self, text: str, *, timeout: float = 20.0) -> None:
        await self.wait_for(
            f"document.body && document.body.innerText.indexOf({json.dumps(text)}) >= 0",
            timeout=timeout,
            label=f"文本「{text}」",
        )

    #: React 18 会在 hydrate 过的 DOM 节点上挂一个 `__reactFiber$xxx` 属性。
    #: 这是 App Router 下**唯一可靠**的"已 hydrate"信号 ——
    #: ⚠️ 不要用 `window.__NEXT_DATA__`：那是 Pages Router 的东西，
    #: 本项目（App Router）实测为 `false`，等它必然超时（这里踩过一次）。
    HYDRATED_JS = """(() => {
      const els = Array.from(document.querySelectorAll('body *')).slice(0, 600);
      return els.some(e => Object.keys(e).some(
        k => k.startsWith('__reactFiber') || k.startsWith('__reactContainer')
      ));
    })()"""

    async def wait_hydrated(self, *, timeout: float = 90.0) -> None:
        """等 React **hydrate 完成**。

        ⚠️ 这一步不能省 —— 踩过一次：Next dev 首屏是**服务端渲染的 HTML**，
        `wait_text('手机号')` 立刻就通过了，但此时 JS bundle 还在编译/下载，
        **React 还没挂上事件监听**。此时任何 click 都是石沉大海：
        页面上能看到按钮、点了没反应、也**不报错**（因为没有监听器可报错）。
        排查时最容易误判成"选择器写错了"。
        """
        await self.wait_for(self.HYDRATED_JS, timeout=timeout, label="React hydrate")
        await asyncio.sleep(0.8)

    async def goto_ready(self, url: str, *, hydrate: bool = True) -> None:
        """导航并等到可交互。**E2E 里一律用这个**，不要直接用 `goto`。"""
        await self.goto(url)
        await self.wait_for("document.readyState === 'complete'", timeout=60, label="文档加载")
        if hydrate:
            await self.wait_hydrated()

    # ---- 交互（都用 JS 注入，对 React / Radix 都够用）----

    async def click(self, selector: str, *, nth: int = 0, timeout: float = 15.0) -> None:
        await self.wait_for(
            f"document.querySelectorAll({json.dumps(selector)}).length > {nth}",
            timeout=timeout,
            label=f"元素 {selector}[{nth}]",
        )
        ok = await self.eval(
            f"""(() => {{
              const els = document.querySelectorAll({json.dumps(selector)});
              const el = els[{nth}];
              if (!el) return false;
              el.scrollIntoView({{block:'center'}});
              const opts = {{bubbles:true, cancelable:true, view:window}};
              for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
                const C = t.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
                el.dispatchEvent(new C(t, opts));
              }}
              return true;
            }})()"""
        )
        if not ok:
            raise RuntimeError(f"点击失败：{selector}[{nth}]")
        await asyncio.sleep(0.35)

    @staticmethod
    def _click_expr(match: str) -> str:
        """生成"找到匹配元素并派发全套鼠标事件"的 JS。"""
        return f"""(() => {{
          const el = {match};
          if (!el) return false;
          el.scrollIntoView({{block:'center'}});
          const opts = {{bubbles:true, cancelable:true, view:window}};
          for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
            const C = t.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
            el.dispatchEvent(new C(t, opts));
          }}
          return true;
        }})()"""

    async def click_text(self, text: str, *, tag: str = "button, a", timeout: float = 20.0) -> None:
        """按可见文字点击（B 端按钮文案稳定，比结构选择器可靠）。"""
        await self.wait_for(
            f"""Array.from(document.querySelectorAll({json.dumps(tag)}))
                 .some(e => (e.innerText||'').trim().includes({json.dumps(text)}))""",
            timeout=timeout,
            label=f"可点击文字「{text}」",
        )
        await self.eval(
            self._click_expr(
                f"""Array.from(document.querySelectorAll({json.dumps(tag)}))
                      .find(e => (e.innerText||'').trim().includes({json.dumps(text)}))"""
            )
        )
        await asyncio.sleep(0.35)

    async def click_in_row(self, row_text: str, button_text: str) -> None:
        """在包含 `row_text` 的那一行（`<tr>` / `<li>`）里点 `button_text`。

        批量列表里的行级操作必须**按行定位** —— 按文字全局找"归档"，
        在多行列表里会点到第一行去（越点越错，而且看起来像"点了没反应"）。
        """
        expr = f"""(() => {{
          const row = Array.from(document.querySelectorAll('tr, li'))
            .find(r => (r.innerText||'').includes({json.dumps(row_text)}));
          if (!row) return false;
          const btn = Array.from(row.querySelectorAll('button, a'))
            .find(x => (x.innerText||'').trim() === {json.dumps(button_text)});
          if (!btn) return false;
          btn.scrollIntoView({{block:'center'}});
          const opts = {{bubbles:true, cancelable:true, view:window}};
          for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
            const C = t.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
            btn.dispatchEvent(new C(t, opts));
          }}
          return true;
        }})()"""
        ok = await self.eval(expr)
        if not ok:
            raise RuntimeError(f"在包含「{row_text}」的行里找不到按钮「{button_text}」")
        await asyncio.sleep(0.35)

    async def type_text(self, selector: str, text: str, *, nth: int = 0) -> None:
        """往受控输入框里**真实敲字**。

        ⚠️ 为什么不直接 `el.value = x`（或 native setter）：React 有自己的
        value tracker，绕过它赋值时组件 state **不会更新** —— 表现是
        "框里有字、提交却是空的"。这里逐个字符发 CDP 键盘事件，
        让 React 收到和真人输入一样的 `input` 事件。

        实测：`Input.insertText` 也不够（值进了 DOM，但本次 React 版本没收到 onChange），
        必须是 `keyDown(text) + keyUp` 这一对。
        """
        await self.eval(f"document.querySelectorAll({json.dumps(selector)})[{nth}].focus()")
        for ch in text:
            await self.send(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": ch, "unmodifiedText": ch, "key": ch},
            )
            await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        await asyncio.sleep(0.2)

    async def click_until(
        self,
        target: str,
        predicate_js: str,
        *,
        by: str = "text",
        attempts: int = 20,
        interval: float = 0.8,
    ) -> None:
        """点 `target` 直到 `predicate_js` 为真。

        为什么需要它：即使 `wait_hydrated()` 过了，首屏仍可能有
        延迟挂载的组件（Radix Portal、懒加载面板）。重试一次比调长 sleep 稳。
        """
        for i in range(attempts):
            try:
                if by == "text":
                    await self.click_text(target)
                else:
                    await self.click(target)
            except Exception:
                pass
            await asyncio.sleep(interval)
            if await self.eval(f"(() => !!({predicate_js}))()"):
                return
        raise TimeoutError(f"点了 {attempts} 次「{target}」仍未满足：{predicate_js}")

    async def hover(self, selector: str, *, nth: int = 0) -> None:
        """把鼠标移到元素上（CDP Input，才能触发 Radix 的 hover 状态）。"""
        box = await self.eval(
            f"""(() => {{
              const el = document.querySelectorAll({json.dumps(selector)})[{nth}];
              if (!el) return null;
              el.scrollIntoView({{block:'center'}});
              const r = el.getBoundingClientRect();
              return [r.left + r.width/2, r.top + r.height/2];
            }})()"""
        )
        if not box:
            raise RuntimeError(f"hover 目标不存在：{selector}[{nth}]")
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box[0], "y": box[1]})
        await asyncio.sleep(0.8)

    async def select_option(self, label: str) -> None:
        """点开一个 Radix Select 并选中某一项（按可见文字）。"""
        await self.eval(
            """(() => {
              const t = document.querySelector('[data-state="open"][role="listbox"]');
              return !!t;
            })()"""
        )
        await self.click_text(label, tag="[role='option']")

    # ---- 截图 ----

    async def screenshot(self, path: str, *, full: bool = False) -> None:
        params: dict[str, Any] = {"format": "png"}
        if full:
            params["captureBeyondViewport"] = True
        r = await self.send("Page.captureScreenshot", params)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.b64decode(r["data"]))

    async def text(self) -> str:
        return await self.eval("document.body ? document.body.innerText : ''") or ""
