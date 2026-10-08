#!/usr/bin/env python
"""单独截一张**统计看板**（`/dashboard`）—— 不重跑整条管理端走查。

前置（与 `e2e-pass2a.py` / `e2e-pass2b.py` **同款**）：
  1. PG + API（8123）+ admin dev（3000）都在跑
  2. 已 `seed-rbac` / `seed-admin`；**想看到非 0 的数字**还要先跑 `seed-stats.py`

跑法：

    python tools/local-verify/shoot-admin-dashboard.py \\
        --admin-base http://127.0.0.1:3000 \\
        --phone <超管手机号> --password <超管密码> \\
        --out apps/admin/docs/screenshots/16-dashboard.png

## 为什么要单独一个脚本
`docs/26` §1 A.3 的 **A1（统计看板截图）** 长期缺失（77 张管理端截图里一张看板都没有），
而**为了这一张去重跑全部管理端走查不划算**（那些走查是给"功能回归"用的，不是给"补图"用的）。

## 判据（**成对**，缺一条就红）
1. **登录真的成功了**：登录后 URL **离开了 `/login`** —— 只看"点了按钮"不算（点了没反应也是那样）。
2. **截到的真是看板**：渲染文本里必须有 `统计看板` 与 `学习数据概况`
   —— 出错页 / 还在加载的骨架，两者都不含这句话 ⇒ **不许冒充**。
3. **图不是空白**：文件 ≥ `--min-bytes`（默认 20 KB）。
4. 控制台**打印页面文本前 200 字**（用户级规则：截图提交前要"抽查内容"）。

## ⚠️ 诚实标注（2026-10-08）
**本脚本在本机还没有被一次成功的运行验证过** —— 本机起不了 B 端栈（`next dev` 清不动 `.next`；
`run-smoke.ps1 -KeepRunning` 只留 PG、会停 API）。阻塞点的实测记录在 `docs/26` §1 A.3。
⇒ 用之前请先确认那两条已经解决；**别把"脚本存在"当成"图已经截到了"**。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cdp_browser  # noqa: E402


async def shoot(args: argparse.Namespace) -> int:
    out = Path(args.out)
    async with cdp_browser.Browser(mobile=False, width=1440, height=900) as b:
        await b.goto_ready(args.admin_base + "/login")
        await b.type_text("#phone", args.phone)
        await b.type_text("#password", args.password)
        await b.click_text("登录")

        # 判据 ①：登录后**离开** /login（轮询 URL，不看按钮）
        for _ in range(80):
            path = await b.eval("location.pathname")
            if isinstance(path, str) and not path.startswith("/login"):
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("登录后仍停在 /login —— 凭据不对？API / Redis 没起？")

        await b.goto_ready(args.admin_base + "/dashboard")
        await b.wait_for("document.querySelectorAll('main *').length > 20", timeout=args.timeout)
        await b.wait_text("统计看板", timeout=args.timeout)
        await asyncio.sleep(args.settle)

        # 判据 ②：渲染文本确实是看板（**不拿错误页冒充**）
        text = await b.text()
        missing = [s for s in ("统计看板", "学习数据概况") if s not in text]
        if missing:
            raise RuntimeError(f"页面文本里缺 {missing} —— 拒绝截图（出错了就是出错了）")

        out.parent.mkdir(parents=True, exist_ok=True)
        await b.screenshot(str(out), full=True)

    # 判据 ③：不是空白图
    size = out.stat().st_size
    if size < args.min_bytes:
        raise RuntimeError(f"截图只有 {size} B（< {args.min_bytes}）—— 大概率是空白页")
    print(f"✅ {out}（{size} B）")
    print(f"   页面文本抽查前 200 字：{text[:200]!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="单独截一张管理端统计看板")
    ap.add_argument("--admin-base", default="http://127.0.0.1:3000")
    ap.add_argument("--phone", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument(
        "--out", default="apps/admin/docs/screenshots/16-dashboard.png", help="输出 PNG 路径"
    )
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--settle", type=float, default=1.5, help="等图表动画落定（秒）")
    ap.add_argument("--min-bytes", type=int, default=20_000)
    return asyncio.run(shoot(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
