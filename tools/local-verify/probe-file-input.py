#!/usr/bin/env python
"""最小验证：`cdp_browser.Browser.set_file_input()` 能不能真的把文件"选"进页面。

## 为什么要单独一个探针（而不是直接写进 E2E 场景）
出问题时必须能分清「**上传原语错了**」还是「**被测场景错了**」。这个探针只依赖
Chrome + `cdp_browser`，**不碰 PWA、不碰后端** ⇒ 它红了就是原语的锅（B-2 步骤 3a 的前置）。

## 判据（**成对** —— 只验一条的话，另一种半成品实现也能绿）
1. 页面**读得到**这个文件（`input.files[0].name` == 文件名）；
2. 页面**收到了 `change` 事件**（真人选完文件时浏览器会派发；React 的 `onChange`
   靠它 —— 一个"塞了文件但不触发回调"的实现会通过第 1 条、栽在第 2 条）。

用法：`python tools/local-verify/probe-file-input.py`   （不需要 venv：只用 cdp_browser）
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_browser import Browser  # noqa: E402  （同目录的极简 CDP 驱动）

#: 一个**没有 React** 的纯静态页 —— 故意如此：本探针验的是原语，不是框架。
PAGE = """<!doctype html><meta charset="utf-8"><title>file probe</title>
<input type="file" id="f" accept=".json">
<div id="out">(等待)</div>
<script>
  window.__changes = 0;
  document.getElementById('f').addEventListener('change', (ev) => {
    window.__changes += 1;
    const el = ev.target;
    document.getElementById('out').textContent =
      (el.files && el.files[0]) ? el.files[0].name : '(空)';
  });
</script>
"""


async def main() -> int:
    td = Path(tempfile.mkdtemp(prefix="probe-file-input-"))
    page = td / "page.html"
    payload = td / "payload.json"
    page.write_text(PAGE, encoding="utf-8")
    payload.write_text('{"probe": true}', encoding="utf-8")

    async with Browser(width=375, height=667, mobile=True) as b:
        # ⚠️ `hydrate=False` **必须**：这页没有 React，等 hydrate 会必然超时（90s 白等）。
        await b.goto_ready(page.as_uri(), hydrate=False)
        await b.set_file_input("#f", payload)
        name = await b.eval(
            "(() => { const el = document.getElementById('f');"
            " return (el.files && el.files[0]) ? el.files[0].name : null; })()"
        )
        changes = await b.eval("window.__changes || 0")
        shown = await b.eval("document.getElementById('out').textContent")

    ok = True
    if name != payload.name:
        print(f"✗ 页面读到的文件名 = {name!r}，期望 {payload.name!r}")
        ok = False
    else:
        print(f"✓ 页面读得到文件：{name}（页面自渲染的文本 = {shown!r}）")
    if not changes:
        print("✗ 没收到 change 事件 —— 页面不会知道用户选了文件（React 的 onChange 收不到）")
        ok = False
    else:
        print(f"✓ change 事件派发了 {changes} 次")

    print("✅ set_file_input 可用" if ok else "❌ set_file_input **不可用**")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
