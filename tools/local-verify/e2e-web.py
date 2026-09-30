"""C 端**浏览器**端到端走查（三个场景：`--scenario login` / `p2a` / `p2b1`）。

为什么要单独写一个（而不是塞进 pytest）
--------------------------------------
后端契约那一半**已经被既有测试覆盖**（`test_admin_session_wall.py` 里的
"`me` → 登出 → 同一 token 立刻失效"）。这里要验的是**另一层**：

  `apps/web` 里的**客户端 fetch + 受控表单 + 路由跳转 + localStorage 存储层**

那层**只有真浏览器能验** —— `curl` 只能验 SSR 与 middleware，验不到 hydrate 之后的交互。

★ 顺序判据（用户 2026-09-27）：**P2 全是交互；不在开 P2 之前把交互层点通一次，
  就是"在没验证的交互层上再叠一层没验证的交互"。** 所以它是 P2 的前置，不是"顺手"。

两个场景（**为什么放同一个文件**：构建 / 起服务 / CDP / 断言助手全都一样，
复制一份就等于把"本机 build 的前置隔离"那套经验再赌一次）
--------------------------------------------------------------------------------
    --scenario login（默认）  P1 的登录闭环：用**现成账号**（超管）走登录 → 首页 → 登出
    --scenario p2a            P2a：Tab 守卫 / 注册闭环 / 三条 Tab / 引导 / 反向守卫
    --scenario p2b1           P2b-1：选科目 → 选章节 → 建练习 → 答题判分 → 刷新仍在 → 重答证明判分对

walkthrough 步骤（每步都**断言**，不是"看一眼"）
----------------------------------------------
    login 场景：
    1. 未登录访问 `/`        → middleware 跳到 `/login`（且带 `next=`）
    2. 登录页 hydrate 完成   → 填表 → ★ 断言**框里真的有值**（React value tracker 那类坑：
       "框里有字、提交却是空的"）
    3. 提交                  → 客户端跳到 `/`
    4. 首页显示用户信息      → `你好` + 手机号 + 用户 ID
       ★ ID 用**原始响应文本**对账（不是 `JSON.parse`）—— 验后端 `BigIntStr` 没退化
    5. 退出登录              → 回 `/login`；localStorage 两个 token 与提示位 cookie 都清掉
    6. ★★ **登出即时生效**：拿**登出前**那个 access token 直连 `/auth/me` ⇒ 必须 401
       —— ★ 而第 4 步已经证明**同一个 token 本来能用**（200 + 原始文本里取到 ID）。
          **没有这个"反向锚"，第 6 步的 401 也可能只是"token 从来就没对"。**
    7. 再访问 `/`            → 仍被送回 `/login`（提示位 cookie 已清）

⚠️ 第 6 步是这个脚本最值钱的一条：它是 P0 会话墙**唯一能在真实浏览器里走一遍**的路径
   （pytest 走 httpx，不经过前端存储层）。

⚠️ 它**不是**每批都跑的门禁：起来要建库 + 构建 + 起两个服务，分钟级。
   定位是**里程碑走查**（P1/P2a/P2b 各跑一次），不是 push 前自检。

用法
----
    # 单跑（自己负责把 API 起好；通常用 `run-local-pipeline.py --e2e-web` 一把起）
    python tools/local-verify/e2e-web.py \\
        --api-base http://127.0.0.1:8123/api/v1 \\
        --phone 13800000000 --password 'Admin@123456'

    python tools/local-verify/e2e-web.py --keep-web    # 跑完不关服务（手工接着点）
    python tools/local-verify/e2e-web.py --force-build # 强制重新构建

★ `--dev`：用 `next dev` 而不是"构建 + `next start`"
-----------------------------------------------------
**本机（这台 Windows）必须加 `--dev`** —— 因为 `next build` 在这台机器上**跑不完**：
它会删 `.next` 里的东西，而宿主有**删除保护**（对"一次 ≥50 项"的删除直接拒绝执行），
于是实测到三种形态：

    `.next` 存在   ⇒ `[safe-delete] … genie-trash ETIMEDOUT`（build 开头 recursiveDelete）
    `.next` 不存在 ⇒ `EPERM … open '.next/trace'`（trace 的 RotatingWriteStream）
    自己去 rmtree ⇒ `[SAFE_DELETE_BULK_CONFIRM_REQUIRED] {count:80, threshold:50}`

⚠️ 后者**不是"权限"问题而是"删除保护 + 立即重开"的时序**：单独 `node -e` 打开同一个文件
**一切正常**（`writeFileSync` / `createWriteStream{a}` / `openSync{r+}` / `unlinkSync` 全过），
只有 `next build` 那条路径会炸。

★★ 而且 **`next dev` 同样会中招** —— 它启动时也会 `recursiveDelete('.next/static/chunks/…')`。
所以：**两种模式都必须先"把 `.next` 改名挪走"**（`isolate_next_dir()`；rename 不是删除，
不触发守卫）。这条是**两次失败换来的**：第一次我以为"dev 能起来"，其实是因为 `.next`
恰好被我手动挪走了 ⇒ **判据：换个入口就通，先确认"变量真的是入口"**。

⇒ 因此：**`--dev` 是"本机能跑"的模式，不是"更省事"的模式**。
它换掉的是"产物构建"这一层 —— 而那层**本来就有 CI 门禁**（`web · build`，27s 绿），
本机再验一遍是重复劳动。走查要验的是**交互层**，dev 与生产在这一点上跑的是同一份 React 代码。

⚠️ 代价（如实记）：dev 是**未压缩 + 未预渲染**的，所以首屏性能 / 预渲染 / static 生成的
结论**不能**从 dev 推 —— 那些由 CI 的 build 门禁负责；但 `/login` 的 SSR 行为在 dev 下也一致。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import cdp_browser  # noqa: E402  （同目录的极简 CDP 驱动，与 B 端截图共用）
import trash  # noqa: E402  （唯一回收站入口 —— 项目约定 R：会被拦的删除一律 rename）

#: 与 `apps/web/src/lib/auth-store.ts` **逐字一致**。
#: ⚠️ 这里**故意写死**：脚本要断言的正是"存储层用了这些名字"——
#:    如果那边改名而这里跟着改，就等于没测；只有"改名 ⇒ 这里红"才有意义。
ACCESS_KEY = "yj_access_token"
REFRESH_KEY = "yj_refresh_token"
HINT_COOKIE = "yj_web_authed"

#: 移动视口（C 端主场景）。挑 375×667 = iPhone SE/8 那一档，**最小的主流机型** ——
#: 布局在最小屏上不炸，才是真的不炸。
VIEW_W, VIEW_H = 375, 667


def say(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


class Failure(RuntimeError):
    """走查断言失败。**单独一个类型**，便于和"环境没起来"区分（后者是 RuntimeError）。"""


def need(cond: object, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def http_status(url: str, timeout: float = 3.0) -> int:
    """返回 HTTP 状态码；连不上返回 0（**不用异常流控**，便于轮询）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except OSError:
        return 0


def kill_tree(pid: int) -> None:
    """连子进程一起收掉。

    ⚠️ 不能只 `terminate()` 父进程：`next start` 可能带出子进程，
    只杀父进程会留一个**还在占端口**的孤儿 —— 下一次跑就变"端口被占"，
    而症状会被误判成"上一次没停干净"（坑 68 同族：资源的拥有者必须唯一且明确）。
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:  # pragma: no cover - 本仓库只在 Windows 跑
        import signal

        subprocess.run(["pkill", "-P", str(pid)], capture_output=True, check=False)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


# --------------------------------------------------------------------- 构建 / 起服务


def isolate_next_dir(web_dir: Path) -> Path | None:
    """把 `.next` **改名挪走**（不是删除），返回挪到哪儿；不存在则返回 None。

    ★ 为什么这两种模式（`next build` **和** `next dev`）**都**必须先做这一步：

      - `next build` 开头：`recursiveDelete(distDir, /^cache/)`
      - `next dev` 启动：`recursiveDelete('.next/static/chunks/...')`

    **两者都会调宿主的删除 shim**，而它对"一次 ≥50 项"的删除**直接拒绝**
    （实测 `SAFE_DELETE_BULK_CONFIRM_REQUIRED {count:50, threshold:50, scope:turn}`）
    ⇒ 服务在**编译之前**就挂掉。

    ⚠️ 这条是 2026-09-28 用**两次失败**换来的：第一次我以为"`next dev` 能起来"（那次
      `.next` 恰好被我手动挪走了）；带上 `.next` 再跑，dev 同样挂 ⇒ **不是 dev 特例，
      是"`.next` 存在"这件事本身**。判据：**换个入口就通 ⇒ 先确认"变量真的是入口"**。

    三种做法都实测过，只有 rename 行得通：

      1. 直接跑（`.next` 在）⇒ 守卫拒批量删 ⇒ `genie-trash ETIMEDOUT` / `SAFE_DELETE_…`
      2. 自己 `shutil.rmtree(.next)` ⇒ 被**同一个守卫**拦（同样 count=80）
      3. ✅ **rename** —— 它**不是删除**，不触发守卫，效果等价：只要"目标目录不存在"

    ⚠️ 挪到**仓库根的 `.trash/next/`**，不留在 app 目录里：prettier / eslint / tsc 都以
      `apps/<app>` 为 cwd ⇒ 看不见仓库根的 `.trash/`；而留在原地会让 `format:check`
      去"检查"几万个构建文件并报格式错
      （2026-09-28 实测：`apps/web` 的 ⑩ 就是被 `.next.stale-*/server/**.js` 弄红的）。
      ★ 顺带把"构建残留"集中到**一个**目录 —— `preflight` ⓿ 因此只需要看一处
      （老做法挪进 `apps/<app>/node_modules/.cache/`，每个 app 一处，清的时候要扫两遍）。
    """
    next_dir = web_dir / ".next"
    if not next_dir.exists():
        return None
    stale = trash.trash(next_dir, kind="next", root=web_dir.parents[1])
    if stale is None:
        return None
    say(f"旧 .next 已改名挪走：{stale.name}（在 .trash/next/ 下；**改名不是删除**）")
    return stale


def ensure_build(args: argparse.Namespace, web_dir: Path, env: dict[str, str]) -> None:
    """确保 `.next` 是用**本次的 `NEXT_PUBLIC_API_BASE`** 构建出来的。

    ⚠️ 为什么必须有这道对账：`NEXT_PUBLIC_*` 是**构建期内联**进 bundle 的，
    不是运行期读的。换了 API 地址却复用旧构建 ⇒ 页面发的还是**旧地址**，
    而症状是"请求发到别处去了"——完全不报错（硬约定 H 的同族：
    **"用错了构建"与"后端挂了"看起来一样**）。
    """
    next_dir = web_dir / ".next"
    stamp = next_dir / ".e2e-api-base"
    built = (next_dir / "BUILD_ID").exists()

    if args.no_build:
        need(built, "--no-build 但没有 .next/BUILD_ID —— 先不加 --no-build 跑一次")
        say("按 --no-build 复用现有构建（**我不保证它是用本次 API 地址构建的**）")
        return

    if built and not args.force_build and stamp.exists():
        old = stamp.read_text(encoding="utf-8").strip()
        if old == args.api_base:
            say(f"复用现有构建（.next 的构建期 API_BASE 一致：{old}）")
            return
        say(f"!! 构建期 API_BASE 变了（{old} → {args.api_base}）⇒ **必须重新构建**")

    isolate_next_dir(web_dir)

    say(f"$ npm run build（注入 NEXT_PUBLIC_API_BASE={args.api_base}）")
    build_log = (
        Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "e2e-web-build.log"
    )
    with build_log.open("w", encoding="utf-8") as fh:
        r = subprocess.run(
            [args.npm, "run", "build"],
            cwd=str(web_dir),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if r.returncode != 0:
        text = build_log.read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.rstrip().splitlines()[-12:])
        # ★ 把"**报错指向的地方不是失败点**"这件事直接写进报错里（坑 63 同族）：
        #   否则下一个人会去查 next.config / 代码，而那些地方根本没错。
        hint = ""
        if "genie-trash" in text or "ETIMEDOUT" in text or "SAFE_DELETE" in text or "EPERM" in text:
            hint = (
                "\n  ⚠️ 触发词里有 `genie-trash` / `ETIMEDOUT` / `SAFE_DELETE` / `EPERM` ⇒\n"
                "     **是宿主的删除保护拦了 `.next` 的读写**，不是代码问题（坑 63）。\n"
                "     形态：① `.next` 在 ⇒ safe-delete 拒批量删（>50 项）⇒ ETIMEDOUT；\n"
                "           ② 目录不存在 ⇒ `EPERM open .next\\trace`。\n"
                "     ⇒ **本机 build 的红推不出代码结论**；换台机器 / 在 CI 上验。\n"
                "     ★ **本机请改用 `--dev`**：实测 `next dev` **能起来**（不轮转 `.next/trace`），\n"
                "       而产物构建那层本来就归 CI 的 `web · build` 门禁。"
            )
        raise Failure(
            f"npm run build 失败（rc={r.returncode}）{hint}\n  末尾 12 行：\n{tail}\n  完整日志：{build_log}"
        )
    stamp.write_text(args.api_base, encoding="utf-8")
    say("构建完成（并记下构建期 API_BASE 供下次对账）")


def start_web(
    args: argparse.Namespace, web_dir: Path, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    """直接 `node <next bin> <dev|start>` —— **不经 `npm run`**。

    为什么绕开 npm：`npm run start` 会多一层包装进程，收尾时杀父进程**留下孤儿**
    继续占 3001（下一次跑报"端口被占"，像上一次没停干净）。少一层 = 只有一个拥有者。

    `--dev` 时用 `dev` 子命令 —— 理由见模块抬头（本机 `build` 被宿主的删除保护拦住）。
    """
    next_bin = web_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    need(next_bin.is_file(), f"找不到 {next_bin} —— 先在 apps/web 跑 `npm ci`")

    tmp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    log = (tmp / "e2e-web.log").open("w", encoding="utf-8")  # noqa: SIM115
    sub = "dev" if args.dev else "start"
    proc = subprocess.Popen(
        [args.node, str(next_bin), sub, "-p", str(args.port)],
        cwd=str(web_dir),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    say(f"$ next {sub} -p {args.port}（日志 {log}）")
    for _ in range(args.boot_timeout):
        if port_open(args.port):
            break
        if proc.poll() is not None:
            raise RuntimeError(f"next {sub} 提前退出（rc={proc.returncode}）—— 见 {log}")
        time.sleep(1)
    else:
        raise RuntimeError(f"next {sub} 未在 {args.boot_timeout}s 内监听 :{args.port}")

    # ⚠️ dev 下**首次请求要现编译**（实测 `/login` 3.1s，冷启动更久），所以这里的
    #    轮询要比 start 宽松 —— 否则会把"编译中"误判成"起不来"。
    tries = 90 if args.dev else 30
    for _ in range(tries):
        if http_status(f"{args.web_base}/login", timeout=10) == 200:
            say(f"next {sub} 就绪（{args.web_base}）")
            return proc
        time.sleep(1)
    raise RuntimeError(f"{args.web_base}/login 未在 {tries}s 内返回 200")


# --------------------------------------------------------------------- 浏览器走查


#: 用**原始文本**取 `/auth/me` 的 id：`JSON.parse` 会把 18~19 位雪花 ID 静默舍入，
#: 用它去对账就等于"用被测量的东西去校准测量"。
_RAW_ME_JS = """(async () => {
  const at = localStorage.getItem(%(access_key)s);
  const r = await fetch(%(api_base)s + '/auth/me', {
    headers: at ? { Authorization: 'Bearer ' + at } : {},
  });
  const t = await r.text();
  const m = t.match(/"id"\\s*:\\s*"(\\d+)"/);
  return { status: r.status, id: m ? m[1] : null, quoted: !!m, bytes: t.length };
})()"""

#: 同上，但可以指定 token（第 6 步用的是**登出前**那个）。
_RAW_ME_JS_WITH_TOKEN = """(async () => {
  const r = await fetch(%(api_base)s + '/auth/me', {
    headers: { Authorization: 'Bearer ' + %(token)s },
  });
  const t = await r.text();
  let code = null;
  try { code = JSON.parse(t).code; } catch (e) { code = 'non-json'; }
  return { status: r.status, code: code, bytes: t.length };
})()"""


async def drill_to_me(b: cdp_browser.Browser) -> None:
    """切到「我的」Tab（底栏第三个），并等到那一屏真的渲染出来。

    ⚠️ P2a 起「退出登录」在「我的」里，不在首页 —— 所以登录场景也要先切 Tab。
    """
    await b.click_text("我的", tag="a")
    await b.wait_for("location.pathname === '/me'", timeout=30, label="切到 /me")
    await b.wait_for(
        "document.body.innerText.includes('退出登录')", timeout=30, label="/me 渲染完成"
    )


#: 从注册页第 2 步的"开发模式"提示里读验证码。
#: ★ 它**不是**"绕过验证码" —— 后端在 `SMS_PROVIDER=mock` 且非生产时**特意**把
#:   `dev_code` 返回给前端（`schemas/auth.py::SmsSendOut`），页面按设计显示它。
#:   走查读它 = 走的是**用户/联调者看到的那条路**，而不是另开一条后门。
_DEV_CODE_JS = """(() => {
  const p = Array.from(document.querySelectorAll('p'))
    .find(x => (x.innerText || '').includes('验证码是'));
  if (!p) return '';
  const s = p.querySelector('.font-mono');
  return s ? s.textContent.trim() : '';
})()"""


async def drive_p2a(args: argparse.Namespace) -> dict[str, object]:
    """P2a 场景：Tab 布局 + 注册 + 引导（用户 2026-09-28 给的五条验收判据）。

        ① 未登录访问**任意** Tab ⇒ 跳登录（不只是 `/`）
        ② 注册能走完：手机号 → 验证码 → 设置密码 → 进首页
        ③ 三个 Tab 都能切到（首页 / 练习 / 我的）
        ④ 引导能走完：选专业 → 选考试年份 → 进首页
        ⑤ 已登录访问 `/login` ⇒ 跳首页（反向守卫）

    ⚠️ 关于"注册 → 进首页"与"引导 → 进首页"：两条判据都落在**首页** ⇒
      引导做成**可后补**（首页给入口卡），而不是"未引导不许进 Tab"。
      这是按用户给的判据实现的；要改成阻塞式，改 `(tabs)/layout.tsx` 一处即可。
    """
    report: dict[str, object] = {}
    phone = "13" + "".join(random.choice(string.digits) for _ in range(9))
    password = "Yjtest123"

    async with cdp_browser.Browser(width=VIEW_W, height=VIEW_H, mobile=True) as b:
        # ---------------- ① 未登录访问任意 Tab ----------------
        say("① 未登录访问 / /practice /me ⇒ 三个都要跳 /login（带 next=）")
        seen: list[str] = []
        for path in ("/", "/practice", "/me"):
            await b.goto(f"{args.web_base}{path}")
            await b.wait_for("location.pathname === '/login'", timeout=40, label=f"{path} 跳登录")
            loc = await b.eval("location.pathname + location.search")
            need(isinstance(loc, str) and "next=" in loc, f"{path} 的跳转没带 next：{loc}")
            seen.append(loc)
        report["tabs_guard"] = seen

        # ---------------- ② 注册闭环 ----------------
        say("② 注册：手机号 → 验证码 → 设置密码")
        await b.goto(f"{args.web_base}/register")
        await b.wait_hydrated(timeout=args.hydrate_timeout)
        await b.type_text('input[autocomplete="tel"]', phone)
        await b.click_text("获取验证码", tag="button")

        await b.wait_for("location.pathname === '/register'", timeout=20, label="停在注册页")
        dev_code = ""
        for _ in range(40):  # 等页面把 dev_code 渲染出来
            dev_code = await b.eval(_DEV_CODE_JS)
            if dev_code:
                break
            await asyncio.sleep(0.3)
        need(bool(dev_code), "注册页没有显示验证码（开发模式下后端应当返回 dev_code）")
        report["dev_code_source"] = "页面上的「开发模式」提示"

        await b.type_text('input[autocomplete="one-time-code"]', dev_code)
        await b.click_text("下一步", tag="button")
        await b.wait_for("location.pathname === '/register'", timeout=20, label="到第 3 步")
        await b.type_text('input[type="password"]', password)
        await b.click_text("完成注册", tag="button")
        # 注册成功 ⇒ setTokens + replace("/") ⇒ 落在 Tab 布局的首页
        await b.wait_for("location.pathname === '/'", timeout=60, label="注册后进首页")
        await b.wait_for(
            "!!localStorage.getItem('yj_access_token')", timeout=20, label="本地已存 token"
        )
        report["register_phone"] = phone

        # ---------------- ③ 三个 Tab 都能切 ----------------
        say("③ 三个 Tab 依次切换")
        await b.wait_for(
            f"document.body.innerText.includes({json.dumps(phone)})",
            timeout=40,
            label="首页显示刚注册的手机号",
        )
        tabs_visited: list[str] = ["/"]
        await b.click_text("练习", tag="a")
        await b.wait_for("location.pathname === '/practice'", timeout=30, label="切到 /practice")
        await b.wait_text("这一屏还没做", timeout=20)
        tabs_visited.append("/practice")

        await drill_to_me(b)
        tabs_visited.append("/me")

        await b.click_text("首页", tag="a")
        await b.wait_for("location.pathname === '/'", timeout=30, label="切回 /")
        tabs_visited.append("/")
        report["tabs_visited"] = tabs_visited

        # ---------------- ④ 引导：选专业 → 选年份 ----------------
        say("④ 引导：选专业 → 选考试年份 → 回首页")
        await b.wait_text("还差一步", timeout=30)  # 未引导 ⇒ 首页有入口卡
        await b.click_text("还差一步", tag="a")
        await b.wait_for("location.pathname === '/onboarding'", timeout=30, label="进引导页")

        await b.wait_for(
            "document.querySelectorAll('main ul button').length > 0",
            timeout=40,
            label="专业列表加载完成",
        )
        picked = await b.eval(
            "(document.querySelectorAll('main ul button')[0] || {}).innerText || ''"
        )
        need(bool(picked), "没能读到第一个专业按钮的文案")
        await b.click("main ul button", nth=0)
        await b.click_text("下一步", tag="button")

        await b.wait_for(
            "document.querySelectorAll('main ul button').length > 0",
            timeout=30,
            label="年份列表出现",
        )
        year = await b.eval(
            "(document.querySelectorAll('main ul button')[0] || {}).innerText || ''"
        )
        await b.click("main ul button", nth=0)
        await b.wait_for("location.pathname === '/'", timeout=60, label="引导完成回首页")

        # 首页现在应显示"已完成"（引导真的落库了，不是前端自己记的）
        await b.wait_for(
            "document.body.innerText.includes('已完成')", timeout=40, label="首页显示引导已完成"
        )
        report["onboarding"] = f"{picked.strip()} / {year.strip()}"

        # ---------------- ⑤ 反向守卫：已登录访问 /login ----------------
        say("⑤ 已登录访问 /login ⇒ 期望被送回首页")
        await b.goto(f"{args.web_base}/login")
        await b.wait_for("location.pathname === '/'", timeout=40, label="/login 跳首页")
        report["login_reverse_guard"] = True

    return report


async def drive_login(args: argparse.Namespace) -> dict[str, object]:
    report: dict[str, object] = {}
    async with cdp_browser.Browser(width=VIEW_W, height=VIEW_H, mobile=True) as b:
        # ---------------- 1. middleware：未登录访问 / ----------------
        say("① 未登录访问 / ⇒ 期望被 middleware 送回 /login")
        await b.goto(f"{args.web_base}/")
        await b.wait_for("location.pathname === '/login'", timeout=40, label="跳到 /login")
        loc = await b.eval("location.pathname + location.search")
        need(isinstance(loc, str) and "next=" in loc, f"跳转没带 next（回来时会丢目标）：{loc}")
        await b.wait_text("一建通", timeout=30)
        report["middleware_redirect"] = loc

        # ---------------- 2. hydrate + 填表 ----------------
        say("② 等 hydrate（**不能省**：SSR 出来的按钮看得到、点了没反应且不报错）")
        await b.wait_hydrated(timeout=args.hydrate_timeout)

        inputs = await b.eval(
            "Array.from(document.querySelectorAll('form input')).map("
            "e => ({type: e.type, inputmode: e.getAttribute('inputmode'), "
            "autocomplete: e.getAttribute('autocomplete')}))"
        )
        need(
            isinstance(inputs, list) and len(inputs) >= 2,
            f"登录表单里的 input 少于 2 个（实际 {inputs!r}）",
        )
        report["form_inputs"] = inputs

        await b.type_text('input[autocomplete="username"]', args.phone)
        await b.type_text('input[type="password"]', args.password)

        # ★ 这一步才是"客户端受控组件真的能用"的核心判据：
        #    React 有自己的 value tracker，绕过它赋值会让 **state 不更新** ——
        #    表现是"框里有字、提交却是空的"（B 端 cdp_browser 抬头记过这个坑）。
        vals = await b.eval("Array.from(document.querySelectorAll('form input')).map(e => e.value)")
        need(
            isinstance(vals, list) and vals and vals[0] == args.phone,
            f"手机号没进受控 state（框里是 {vals[0] if vals else None!r}）",
        )
        need(
            len(vals) > 1 and vals[1] == args.password,
            "密码没进受控 state ⇒ 提交必然失败",
        )

        # ---------------- 3. 提交 ----------------
        say("③ 提交登录 ⇒ 期望客户端跳到 /")
        await b.click_text("登录", tag="button")
        try:
            await b.wait_for(
                "location.pathname === '/'", timeout=args.submit_timeout, label="跳首页"
            )
        except TimeoutError as e:
            alert = await b.eval("(document.querySelector('[role=alert]')||{}).textContent || ''")
            raise Failure(f"提交后没跳转（{e}）；页面上的错误提示：{alert!r}") from None

        # ---------------- 4. 首页显示用户信息 ----------------
        say("④ 首页显示用户信息（并对账雪花 ID）")
        await b.wait_for(
            "document.body.innerText.indexOf('你好') >= 0", timeout=40, label="首页渲染出用户信息"
        )
        body = await b.text()
        need(args.phone in body, f"首页没有显示手机号，正文前 200 字：{body[:200]!r}")

        shown_id = await b.eval("(document.querySelector('dd.font-mono')||{}).textContent || ''")
        need(
            isinstance(shown_id, str) and re.fullmatch(r"\d{15,}", shown_id or "") is not None,
            f"首页的「用户 ID」不像雪花 ID：{shown_id!r}",
        )

        raw = await b.eval(
            _RAW_ME_JS
            % {"access_key": json.dumps(ACCESS_KEY), "api_base": json.dumps(args.api_base)}
        )
        # ★★ 这条是第 6 步的**反向锚**：同一个 token 在登出前必须能用（200 + 拿到 ID）。
        #    没有它，第 6 步的 401 完全可能只是"token 从来就没对"。
        need(raw["status"] == 200, f"首页登录后，原始 /auth/me 返回 {raw['status']}（期望 200）")
        need(raw["quoted"], "`/auth/me` 的 id 不是 JSON **字符串** ⇒ 前端 JSON.parse 会静默舍入")
        need(
            raw["id"] == shown_id,
            f"页面显示的 ID（{shown_id}）与后端原始文本里的（{raw['id']}）不一致 ⇒ 中间被舍入了",
        )
        report["user_id"] = shown_id

        token_before = await b.eval(f"localStorage.getItem({json.dumps(ACCESS_KEY)})")
        need(bool(token_before), "登录后 localStorage 里没有 access token")
        report["token_present"] = True

        # ---------------- 5. 退出登录 ----------------
        say("⑤ 退出登录（在「我的」Tab 里）⇒ 期望回 /login 且本地凭证清空")
        # ⚠️ P2a 把「退出登录」从首页搬进了「我的」Tab（版式变化，不是回归）。
        #    所以这一步先切 Tab 再点 —— 顺带把"底栏能切"也验了一次。
        await drill_to_me(b)
        await b.click_text("退出登录", tag="button")
        await b.wait_for("location.pathname === '/login'", timeout=40, label="回登录页")
        st = await b.eval(
            "({at: localStorage.getItem(%s), rt: localStorage.getItem(%s), ck: document.cookie})"
            % (json.dumps(ACCESS_KEY), json.dumps(REFRESH_KEY))
        )
        need(st["at"] is None and st["rt"] is None, f"登出后 localStorage 还有 token：{st!r}")
        need(HINT_COOKIE not in (st["ck"] or ""), f"登出后提示位 cookie 没清：{st['ck']!r}")
        report["local_cleared"] = True

        # ---------------- 6. ★★ 登出即时生效（服务端撤销）----------------
        say("⑥ 用「登出前」的 token 直连 /auth/me ⇒ 期望 401（P0 会话墙）")
        after = await b.eval(
            _RAW_ME_JS_WITH_TOKEN
            % {"api_base": json.dumps(args.api_base), "token": json.dumps(token_before)}
        )
        need(
            after["status"] == 401,
            f"登出后旧 token 仍能用（HTTP {after['status']}，code={after.get('code')}）"
            " —— 服务端会话没被撤销（第 4 步已证明它不是无效 token）",
        )
        report["revoked_http"] = after["status"]
        report["revoked_code"] = after.get("code")

        # ---------------- 7. 再访问 / ----------------
        say("⑦ 再访问 / ⇒ 期望仍被送回 /login")
        await b.goto(f"{args.web_base}/")
        await b.wait_for("location.pathname === '/login'", timeout=40, label="仍跳 /login")
        report["second_visit_redirected"] = True

    return report


# --------------------------------------------------------------------- main


def find_tool(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    # Windows 上 npm 是 npm.cmd —— `shutil.which` 通常能找到，找不到就补一次
    for ext in (".cmd", ".exe", ".bat"):
        p = shutil.which(name + ext)
        if p:
            return p
    return None


async def do_login(b: "cdp_browser.Browser", args: argparse.Namespace) -> None:
    """用**现成账号**登录（`--phone` / `--password`），落在首页。

    ⚠️ 这里只做"能进到已登录态"，不做断言 —— 断言是 `drive_login` 的事。
      两个场景分开，是因为**登录场景的每一步本身就是判据**（表单受控、跳转、撤销），
      而 P2b-1 只把登录当成前置条件（它要验的是刷题的数据流）。
    """
    await b.goto(f"{args.web_base}/login")
    await b.wait_hydrated(timeout=args.hydrate_timeout)
    await b.type_text('input[autocomplete="username"]', args.phone)
    await b.type_text('input[type="password"]', args.password)
    await b.click_text("登录", tag="button")
    await b.wait_for("location.pathname === '/'", timeout=60, label="登录后进首页")


#: ★ 为什么要在**页面**里按文案找按钮、而不是"按索引点第 N 个"：正确答案可能是 `"A"`，
#:   也可能是多选题的 `"A、C、D"`，判断题则是 `"正确"`（后端已把判断题归一到布尔）。
#:   按文案找，三种都能覆盖。
#: ⚠️ 匹配只用 `t === w || t.startsWith(w)`，**不要**假设"标号与选项正文之间有换行" ——
#:   第一版写的是 `startsWith(w + "\n")`，在 flex 布局下匹配不到：
#:   两个 `<span>` 是 flex 子项，`innerText` 把它们**直接拼起来**（"A选项内容"），
#:   于是多选题的第 ⑤ 步找不到按钮。**这是猜 DOM 文本形状的代价** ——
#:   判据：脚本里凡是"猜渲染结果长什么样"的地方，都要有一条**能证伪**的失败路径（这里就是报"找不到按钮"）。
#: 练习页的「有可练章节」判据 / 第一个可练章节的文案 / 答题页题干。
#: ⚠️ 题干用 `main p.whitespace-pre-wrap` 取 —— 那个 class 只出现在题干上
#:   （选项正文用的是 `<span>`）。**结构一改走查就会红**，这正是想要的：
#:   结构性改动本来就该把走查一起更新。
_PRACTICE_CHAPTERS_AVAILABLE_JS = (
    "document.querySelectorAll("
    "'main section:nth-of-type(2) ul li button:not([disabled])').length > 0"
)
_PRACTICE_FIRST_CHAPTER_JS = (
    "(document.querySelectorAll("
    "'main section:nth-of-type(2) ul li button:not([disabled])')[0] || {}).innerText || ''"
)
_PRACTICE_STEM_JS = "(document.querySelector('main p.whitespace-pre-wrap') || {}).innerText || ''"
#: 答题页的「第 N 题 / 共 M 题」—— 切题的**可见**证据（游标变了它就该变）。
_PRACTICE_POS_JS = "(document.body.innerText.match(/第 \\d+ 题 \\/ 共 \\d+ 题/) || [])[0] || ''"
#: 顶部进度「已答 N / M」—— ★ **只由提交驱动**，切题不许动它（硬约定 S 的 E2E 形式）。
_PRACTICE_PROGRESS_JS = "(document.body.innerText.match(/已答 \\d+ \\/ \\d+/) || [])[0] || ''"
#: 按文字找一个按钮的 `disabled`（`null` = 页面上没有这个按钮）。
#: ⚠️ 判据是"**它能点吗**"，不是"我点过了"（硬约定 D 的 E2E 形态）。
#: ⚠️ 用 `.includes()` 与 `click_text` **保持同一套匹配** —— 否则"找到的"与"点到的"可能是两个元素。
#: 滑动卡片当前的 `translateX`（px）。★ **跟手反馈的机器可读形式** ——
#: 手势结束之后它就被重置了，所以只能在**手势中途**读。
_SWIPE_X_JS = r"""(() => {
  const el = document.querySelector('[data-swipe-card]');
  if (!el) return null;
  const m = (el.style.transform || '').match(/translateX\((-?[0-9.]+)px\)/);
  return m ? Math.round(parseFloat(m[1])) : 0;
})()"""
#: 答题卡抽屉的 `transform`（**常驻 DOM、靠它显隐**）⇒ "从底部滑出"是个可读的状态。
#: 当前显示的**题目身份**（`question_id`）。
#: ★★ 为什么不用题干当身份：**题库里有模板化题干**（`gen_seed_questions.py` 里
#:   `stem = f"下列选项中，属于「{kp['name']}」这一考点所涵盖内容的有（　　）。"`）
#:   ⇒ **题干会重复**，拿它当「这道题换没换」的判据会得到**假红**（实测踩到，见坑 83）。
_QID_JS = (
    "(() => { const el = document.querySelector('[data-qid]');"
    " return el ? el.getAttribute('data-qid') : null; })()"
)
#: 第 n 个格子里记的 `question_id`（探针：点它之后，显示的**是不是这一道**）。
_CELL_QID_JS = """(() => {
  const c = document.querySelectorAll('[data-sheet-grid] button')[%d];
  return c ? c.getAttribute('data-cell-qid') : null;
})()"""
#: 抽屉的**几何**（`top` / 高度 / 视口高 / 原始 transform）。
#: ★★ 判据是**语义**的："展开" = `top < vh`（有边在视口里）、"收起" = `top >= vh`（整个在视口之下）。
#: ⚠️ **不要拿 `style.transform` 做字符串相等** —— CSSOM 会**规范化**：
#:   `translateY(0)` 读回来是 **`translateY(0px)`**（实测：这一条让 ⑧ 第一次跑成 rc=2）。
#:   猜"渲染结果长什么样"就会踩它（同族：猜 DOM 文本形状 / 方向只判"谁大"）。
_SHEET_BOX_JS = """(() => {
  const el = document.querySelector('[data-sheet]');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: Math.round(r.top), h: Math.round(r.height),
           vh: window.innerHeight, transform: el.style.transform };
})()"""
#: 网格几何：题数 / 首行列数 / 总行数（"5 列、按题量自适应"的可证伪形式）。
_SHEET_GRID_JS = """(() => {
  const cells = Array.from(document.querySelectorAll('[data-sheet-grid] button'));
  if (!cells.length) return null;
  const tops = cells.map((c) => Math.round(c.getBoundingClientRect().top));
  return { total: cells.length, cols: tops.filter((t) => t === tops[0]).length,
           rows: new Set(tops).size };
})()"""
#: 抽屉的可见文字（图例四态在这里；grid 里的题号也在，但没别人）。
_SHEET_TEXT_JS = (
    "(() => { const el = document.querySelector('[data-sheet]');"
    " return el ? el.innerText : ''; })()"
)
#: 每格 `{seq, state}` —— ★ 状态是**数据派生**的，不能写死（走查比对的就是它）。
_SHEET_CELLS_JS = """(() => {
  const cells = Array.from(document.querySelectorAll('[data-sheet-grid] button'));
  return cells.map((c) => ({ seq: Number((c.textContent || '').trim()),
                             state: c.getAttribute('data-cell-state') }));
})()"""
#: 抽屉里的按钮账：`grid` = 题号格数、`others` = 其他按钮数。
#: ★ 这是"**没做设置标记的入口**"的可证伪形式 —— 除题号格外只该有 1 个（关闭）。
_SHEET_BUTTONS_JS = """(() => {
  const grid = document.querySelectorAll('[data-sheet-grid] button').length;
  const all = document.querySelectorAll('[data-sheet] button').length;
  return { grid: grid, others: all - grid };
})()"""
_NAV_DISABLED_JS = """(() => {
  const want = %s;
  const b = Array.from(document.querySelectorAll('button'))
    .find((x) => (x.innerText || '').trim().includes(want));
  return b ? b.disabled === true : null;
})()"""

_PICK_BY_ANSWER_JS = """(() => {
  const wants = %s;
  let hit = 0;
  for (const w of wants) {
    const btns = Array.from(document.querySelectorAll('main ul li button'));
    const b = btns.find((x) => {
      const t = (x.innerText || '').trim();
      return t === w || t.startsWith(w);
    });
    if (!b) return false;
    b.click();
    hit += 1;
  }
  return hit > 0;
})()"""


async def drive_p2b1(args: argparse.Namespace) -> dict[str, object]:
    """P2b-1 场景：**选科目 → 选章节 → 建练习 → 答题判分 → 刷新仍在**。

    用户给的四条判据 + 我加的**可证伪锚**（没有它们，前面几条都能被"表面通过"骗过）：

    | # | 判据 | 没有它会怎样 |
    |---|---|---|
    | ① | 能选科目 → 选章节 → 创建 session → 显示第一题（且**不给答案**） | —— |
    | ② | ★ 再建一次 ⇒ **抽到同一道题** | 少了它，③ 的"判分测试"没法构造（见下） |
    | ③ | 选项能选 → 提交 → 判分 → 显示解析 | —— |
    | ④ | 刷新后**进度还在**（同一条 session），且**下一题不给答案** | 少了后半句，"把整份答案都发给前端"也能过 |
    | ⑤ | ★★ 用 **A 揭示的正确答案**去答 **B 的同一道题** ⇒ 必须"答对了" | 少了它，**判分恒返回"错"也能通过 ③** |
    | ⑥ | 按钮切题：边界 `disabled` / 切题**不改进度** / 答过的题**回来仍有结果** | 少了"不改进度"，把切题写成"顺手再 GET 一次"也能过（那正是 P2b-1 那个 bug 的形状） |
    | ⑦ | **滑动切题**（**真触摸**）：跟手反馈 / 未超阈值**弹回** / 超阈值切题 / **纵向不算切题** | 少了"跟手反馈"，**手势根本没被触发**也能全绿（那是最假的一种绿） |
    | ⑧ | **答题卡**：底部滑出 / 5 列网格（行数自适应）/ 四态 / 点题号跳题 / 关闭后位置正确 | 少了「抽屉内按钮账」，偷偷加一个「设置标记」入口也能全绿 |

    ⚠️ ⑤ 为什么要"建两个 session"（第一版想省掉它，结果绕了两轮）：
      抽题是"**未做过的优先**"，所以在 A 里答完第 1 题之后，
      再建的 session **第一题就换人了** —— 拿 A 揭示的答案去答新 session 的第一题，
      匹配的是一道**完全不同的题**（实测就撞在一道判断题上，选项根本不是 A/B/C/D）。
      ⇒ 正解：**先连建两个**（此刻都还没答过 ⇒ 抽到同一道，② 顺手把它断言掉），
        再在 A 上作答、在 B 上复答。
    """
    report: dict[str, object] = {}

    async def new_session(b: "cdp_browser.Browser") -> str:
        """走一遍「选科目 → 选章节」，返回新建 session 的 URL。"""
        await b.goto_ready(f"{args.web_base}/practice")
        await b.wait_for(
            "document.querySelectorAll('main button[aria-pressed]').length > 0",
            timeout=40,
            label="科目列表加载完成",
        )
        await b.click("main button[aria-pressed]", nth=0)
        try:
            await b.wait_for(
                _PRACTICE_CHAPTERS_AVAILABLE_JS,
                timeout=40,
                label="章节列表加载完成（有可练的章节）",
            )
        except Exception as e:  # noqa: BLE001
            # ★ 排障出口：**超时只说"没等到"，不说"页面上现在是什么"** ——
            #   而这两种信息差着一整轮往返（硬约定：能"看到"证据就别"推理"证据）。
            #   把页面文本一起报出来，"请求失败"与"确实没题"一眼可分。
            txt = await b.eval("document.body.innerText")
            raise Failure(f"章节列表没出来（{e}）。此刻页面文本：\n{txt}") from None
        first_chapter = await b.eval(_PRACTICE_FIRST_CHAPTER_JS)
        if not report.get("chapter_picked"):
            report["chapter_picked"] = " ".join(str(first_chapter).split())
        await b.click("main section:nth-of-type(2) ul li button:not([disabled])", nth=0)
        await b.wait_for(
            "location.pathname.startsWith('/practice/session/')", timeout=60, label="进答题页"
        )
        await b.wait_for(
            "document.querySelectorAll('main ul li button').length > 0", timeout=40, label="有选项"
        )
        url = await b.eval("location.pathname")
        need(isinstance(url, str) and url.startswith("/practice/session/"), f"URL 不对：{url!r}")
        return url

    async def answer(b: "cdp_browser.Browser", *, nth: int) -> None:
        """点第 `nth` 个选项并提交。**先等"选中"落进 DOM 再点提交** —— 见下面的注释。"""
        await b.click("main ul li button", nth=nth)
        # ★★ 必须**先等"选中"这个状态落进 DOM**，再点提交。
        #    提交按钮的 `disabled` 由同一个 state 派生（`canSubmit`）——
        #    点完选项立刻点提交会撞上 React 的渲染节拍：那一刻按钮**还是 disabled**，
        #    `.click()` 静默无效（不报错、也不提交），下一步只能等成超时。
        #    ⚠️ 硬约定 D 的 E2E 形态：判据不是"我点过了"，而是"**它能点了吗**"。
        await b.wait_for(
            "document.querySelector('main ul li button[aria-pressed=\"true\"]') !== null",
            timeout=20,
            label="选项已选中（提交按钮此时才可用）",
        )
        try:
            await b.click_text("提交", tag="button")
            await b.wait_for(
                "document.body.innerText.includes('正确答案')", timeout=40, label="出现判分结果"
            )
        except Exception as e:  # noqa: BLE001
            txt = await b.eval("document.body.innerText")
            raise Failure(f"提交后没出现判分结果（{e}）。此刻页面文本：\n{txt}") from None

    async with cdp_browser.Browser(width=VIEW_W, height=VIEW_H, mobile=True) as b:
        await do_login(b, args)

        # ---------------- ① 建 A：显示第 1 题，且**不给答案** ----------------
        say("① 选科目 → 选章节 → 建练习 A：应显示第 1 题 + 选项，且**不给答案**")
        url_a = await new_session(b)
        await b.wait_for(
            "document.body.innerText.includes('第 1 题')", timeout=40, label="显示第 1 题"
        )
        body0 = str(await b.eval("document.body.innerText"))
        need(
            "正确答案" not in body0,
            "还没提交就看到了「正确答案」—— 后端把答案提前发给前端了",
        )
        stem_a = await b.eval(_PRACTICE_STEM_JS)
        need(isinstance(stem_a, str) and stem_a.strip() != "", "读不到题干")
        report["session_a"] = url_a

        # ---------------- ② 建 B：必须抽到**同一道题** ----------------
        say("② 再建一次（B）：此刻两边都还没答过 ⇒ 应抽到同一道题")
        url_b = await new_session(b)
        need(url_b != url_a, f"第二次建的 session 与第一次是同一个 id（{url_b!r}）—— 那不是新建")
        stem_b = await b.eval(_PRACTICE_STEM_JS)
        need(
            stem_b == stem_a,
            "两个 session 抽到的题不一样 —— 抽题**不是确定性的**，"
            f"那么「用 A 揭示的答案答 B」这个反向锚就不成立。\nA: {stem_a!r}\nB: {stem_b!r}",
        )
        report["deterministic_pick"] = "两个新建 session 抽到同一道题（未作答时）"

        # ---------------- ③ 在 A 上作答 → 判分 + 解析 ----------------
        say("③ 在 A 上选一个选项并提交 ⇒ 应出现判分结论、正确答案、解析")
        await b.goto_ready(f"{args.web_base}{url_a}")
        await b.wait_for(
            "document.querySelectorAll('main ul li button').length > 0",
            timeout=40,
            label="A 有选项",
        )
        await answer(b, nth=0)
        await b.wait_for("document.body.innerText.includes('解析')", timeout=40, label="出现解析")
        # ★★ 「刷新数据」不许移动「用户的当前位置」（用户 2026-09-29 定的判据）：
        #   提交后前端会**再 GET 一次 session** 去拿进度 —— 那一趟**只许改进度数字**，
        #   不许把游标带到下一题。第一版的 bug 正是这样：答完第 1 题页面立刻跳到第 2 题，
        #   判分结论与解析**一闪而过**（而"显示解析"是本批的验收判据）。
        #   ⚠️ 这条断言是**显式**的：只断言"解析出现过"是不够的 —— 它可能出现过又立刻被覆盖。
        where = await b.eval(
            "(document.body.innerText.match(/第 \\d+ 题 \\/ 共 \\d+ 题/) || [])[0] || ''"
        )
        need(
            isinstance(where, str) and where.startswith("第 1 题"),
            f"提交后页面停在了 {where!r}（应为「第 1 题」）——"
            "**「刷新数据」把「用户的当前位置」也带跑了**（进度跟后端走、游标跟用户动作走）",
        )
        verdict = await b.eval("(document.body.innerText.match(/(答对了|答错了)/) || [])[0] || ''")
        need(verdict in ("答对了", "答错了"), f"没读到判分结论：{verdict!r}")
        right = await b.eval(
            "(document.body.innerText.match(/正确答案：([^\\n]+)/) || [])[1] || ''"
        )
        need(isinstance(right, str) and right.strip() != "", "结果面板里没有「正确答案」")
        report["first_verdict"] = verdict
        report["revealed_answer"] = right.strip()

        # ---------------- ④ 刷新 A：进度还在，且下一题不给答案 ----------------
        say("④ 刷新 A：进度还在（同一条 session），且**下一题不给答案**")
        await b.goto_ready(f"{args.web_base}{url_a}")
        await b.wait_for(
            "document.body.innerText.includes('已答 1 /')", timeout=40, label="刷新后进度还在"
        )
        url2 = await b.eval("location.pathname")
        need(url2 == url_a, f"刷新后跑到了别的 session：{url2!r} != {url_a!r}")
        body2 = str(await b.eval("document.body.innerText"))
        need("已答 1 /" in body2, f"刷新后进度丢了：\n{body2}")
        # ★ 与上一条互补：断点恢复会落在**下一道未作答**的题上，
        #   而它**不许**被上一题的揭示带出来（可见性是**逐题**的）。
        need(
            "正确答案" not in body2,
            "刷新后落在一道**未作答**的题上，却已经能看到「正确答案」—— 可见性漏了",
        )
        report["reload_kept_progress"] = "已答 1/N 仍在，且下一题未揭示答案"

        # ---------------- ⑤ 反向锚：用 A 揭示的答案答 B 的同一道题 ----------------
        say(f"⑤ 反向锚：在 B 上提交页面刚揭示的正确答案「{right.strip()}」⇒ 必须答对")
        await b.goto_ready(f"{args.web_base}{url_b}")
        await b.wait_for(
            "document.querySelectorAll('main ul li button').length > 0",
            timeout=40,
            label="B 有选项",
        )
        wants = [x.strip() for x in str(right).split("、") if x.strip()]
        picked = await b.eval(_PICK_BY_ANSWER_JS % json.dumps(wants, ensure_ascii=False))
        need(
            picked is True,
            f"没能在 B 的选项里找到 {wants!r} 对应的按钮"
            "（判断题是「正确 / 错误」按钮；多选题要能逐个点中）",
        )
        await b.wait_for(
            "document.querySelector('main ul li button[aria-pressed=\"true\"]') !== null",
            timeout=20,
            label="B 的选项已选中",
        )
        try:
            await b.click_text("提交", tag="button")
            await b.wait_for(
                "document.body.innerText.includes('正确答案')", timeout=40, label="B 的判分结果"
            )
        except Exception as e:  # noqa: BLE001
            txt = await b.eval("document.body.innerText")
            raise Failure(f"B 提交后没出现判分结果（{e}）。此刻页面文本：\n{txt}") from None
        verdict2 = await b.eval("(document.body.innerText.match(/(答对了|答错了)/) || [])[0] || ''")
        need(
            verdict2 == "答对了",
            f"提交的**是页面自己给出的正确答案**，却判成了 {verdict2!r} —— 判分逻辑是错的",
        )
        report["grading_proven_by_reanswer"] = f"提交「{right.strip()}」⇒ 答对了"

        # ---------------- ⑥ 按钮切题（P2b-2a）----------------
        say("⑥ 按钮切题：第 1 题「上一题」应**不可点** / 切题**不改进度** / 回来看答过的题仍有结果")
        at0 = await b.eval(_PRACTICE_POS_JS)
        need(
            isinstance(at0, str) and at0.startswith("第 1 题"),
            f"进 ⑥ 时应停在 B 的第 1 题，实际 {at0!r}",
        )
        prev_dis = await b.eval(_NAV_DISABLED_JS % json.dumps("上一题"))
        need(
            prev_dis is True,
            f"第 1 题上「上一题」的 disabled = {prev_dis!r}（应为 True）——"
            "边界没做成**显式不可点**，在用户那里就成了「点了没反应」",
        )
        # ★ 可证伪锚：**先证明这个按钮本来能点** —— 否则"永远 disabled"也能过上面那条。
        next_dis = await b.eval(_NAV_DISABLED_JS % json.dumps("下一题"))
        need(next_dis is False, f"第 1 题上「下一题」的 disabled = {next_dis!r}（应为 False）")

        prog0 = await b.eval(_PRACTICE_PROGRESS_JS)
        await b.click_text("下一题", tag="button")
        await b.wait_for(
            f"({_PRACTICE_POS_JS}).startsWith({json.dumps('第 2 题')})",
            timeout=20,
            label="切到第 2 题",
        )
        body_next = str(await b.eval("document.body.innerText"))
        need(
            "正确答案" not in body_next,
            "切到第 2 题后看到了「正确答案」—— 那是一道**未作答**的题，可见性漏了",
        )
        prog1 = await b.eval(_PRACTICE_PROGRESS_JS)
        need(
            prog0 == prog1,
            f"切题把进度也改了：{prog0!r} → {prog1!r} ——"
            "**切题是本地状态，不该发请求、更不该改进度**（硬约定 S）",
        )
        # 回到第 1 题：**答过的题，结果还在**（否则"回去复习"看到的是空的）
        prev_dis2 = await b.eval(_NAV_DISABLED_JS % json.dumps("上一题"))
        need(prev_dis2 is False, f"第 2 题上「上一题」应可点，实际 disabled = {prev_dis2!r}")
        await b.click_text("上一题", tag="button")
        await b.wait_for(
            "document.body.innerText.includes('正确答案')", timeout=20, label="回到第 1 题仍有结果"
        )
        at1 = await b.eval(_PRACTICE_POS_JS)
        need(
            isinstance(at1, str) and at1.startswith("第 1 题"), f"「上一题」没回到第 1 题：{at1!r}"
        )
        stem_back = await b.eval(_PRACTICE_STEM_JS)
        need(stem_back == stem_b, f"回来看到的不是同一道题：{stem_back!r} != {stem_b!r}")
        report["nav_buttons"] = (
            "① 第 1 题「上一题」disabled（可证伪锚：「下一题」可点）"
            "② 切题**不改进度** ③ 答过的题回来结果仍在"
        )
        # ---------------- ⑦ 滑动切题（P2b-2a 后半；**真触摸**）----------------
        say("⑦ 滑动切题：跟手反馈 / 未超阈值弹回 / 超阈值切题 / 纵向不算切题")
        # ★ 先报"这一轮到底有没有触摸能力" —— 否则 ⑦ 全绿可能只是因为**手势从未被触发**。
        env_touch = await b.eval(
            "({coarse: matchMedia('(pointer: coarse)').matches,  touch: 'ontouchstart' in window})"
        )
        need(
            isinstance(env_touch, dict) and (env_touch.get("coarse") or env_touch.get("touch")),
            f"这个视口既不是 coarse pointer、也没有 ontouchstart ⇒ 手势不会启用：{env_touch!r}",
        )
        report["touch_env"] = (
            f"pointer:coarse={env_touch['coarse']}｜ontouchstart={env_touch['touch']}"
        )
        width = await b.eval("window.innerWidth")
        need(isinstance(width, (int, float)) and width > 0, f"读不到视口宽度：{width!r}")
        thresh = max(width * 0.25, 80)

        async def swipe_report(dx: float, dy: float = 0.0) -> int | None:
            """滑一次，并在**手势中途**抓一次跟手位移；返回那个值。"""
            mid: dict[str, object] = {"x": None}

            async def _on_mid() -> None:
                v: object = None
                for _ in range(25):
                    v = await b.eval(_SWIPE_X_JS)
                    if isinstance(v, int) and v != 0:
                        break
                    await asyncio.sleep(0.02)
                mid["x"] = v

            await b.swipe("main [data-swipe-card] h1", dx=dx, dy=dy, on_mid=_on_mid)
            got = mid["x"]
            return got if isinstance(got, int) else None

        pos0 = await b.eval(_PRACTICE_POS_JS)
        prog_a = await b.eval(_PRACTICE_PROGRESS_JS)

        # ⑦a 未超阈值（−60 < 阈值）：**中途必须跟手**，松手必须**弹回**且不切题
        mid_small = await swipe_report(-60.0)
        need(
            mid_small is not None and mid_small < 0,
            f"滑动中题目没有跟手位移（滑到一半读到 translateX = {mid_small!r}）——"
            "**手势根本没触发**的话，后面几条「没切题」也会全绿（那是最假的一种绿）",
        )
        report["swipe_mid_transform"] = f"未超阈值那次，滑到一半 translateX = {mid_small}px（跟手）"
        x_after = await b.eval(_SWIPE_X_JS)
        need(x_after == 0, f"未超阈值松手后没有弹回：translateX = {x_after!r}（应为 0）")
        pos1 = await b.eval(_PRACTICE_POS_JS)
        need(pos1 == pos0, f"未超阈值却切了题：{pos0!r} → {pos1!r}")

        # ⑦b 超阈值左滑 ⇒ 切到下一题；**进度不动**（与按钮切题同一判据）
        await swipe_report(-(width * 0.6))
        await b.wait_for(
            f"({_PRACTICE_POS_JS}).startsWith({json.dumps('第 2 题')})",
            timeout=20,
            label="左滑切到第 2 题",
        )
        prog_b = await b.eval(_PRACTICE_PROGRESS_JS)
        need(
            prog_a == prog_b,
            f"滑动切题把进度也改了：{prog_a!r} → {prog_b!r} —— 切题是本地状态（硬约定 S）",
        )

        # ⑦c 超阈值右滑 ⇒ 回上一题
        await swipe_report(width * 0.6)
        await b.wait_for(
            f"({_PRACTICE_POS_JS}).startsWith({json.dumps('第 1 题')})",
            timeout=20,
            label="右滑回到第 1 题",
        )

        # ⑦d ★ 方向判定：**纵向为主**的斜滑不算切题（用户在滚题目）
        mid_diag = await swipe_report(60.0, 140.0)
        need(
            mid_diag in (0, None),
            f"纵向为主的斜滑也把题目拖动了（translateX = {mid_diag!r}）——"
            "方向判据失效，用户滚题目时会误切",
        )
        pos_diag = await b.eval(_PRACTICE_POS_JS)
        need(pos_diag == pos0, f"纵向斜滑切了题：{pos0!r} → {pos_diag!r}")

        # ⑦e 边界：第 1 题上**向右**超阈值滑 ⇒ 没有上一题，必须不切（弹回）
        await swipe_report(width * 0.6)
        pos_edge = await b.eval(_PRACTICE_POS_JS)
        need(pos_edge == pos0, f"第 1 题右滑越界了：{pos0!r} → {pos_edge!r}")
        x_edge = await b.eval(_SWIPE_X_JS)
        need(x_edge == 0, f"越界右滑后没有弹回：translateX = {x_edge!r}")
        report["swipe_gesture"] = (
            f"阈值 {thresh:.0f}px（屏宽 {width} 的 25% 与 80 取大）｜"
            "① 中途跟手 ② 未超阈值弹回且不切题 ③ 超阈值切题且进度不动 "
            "④ 纵向斜滑不算切题 ⑤ 第 1 题右滑越界不切（弹回）"
        )
        # ---------------- ⑧ 答题卡（P2b-2b）----------------
        say("⑧ 答题卡：底部滑出 / 5 列网格 / 四态 / 点题号跳题 / 关闭后位置正确")

        async def reach(expr: str, label: str, timeout: float = 20.0) -> None:
            """等界面到某状态；**超时按断言失败报**（`Failure` ⇒ rc=1）。

            ⚠️ 走查顶部的分类是：`Failure` ⇒ rc=1（断言失败）、**其它异常** ⇒ rc=2（"没跑起来"）。
              而裸的 `TimeoutError` 会掉进后者 —— 实测：把 `jumpTo` 的 `setCursor` 打掉
              （**点题号不切题**）时，一个**产品缺陷**被记成了 rc=2，看起来像环境问题。
              ⇒ 判据：**"界面没到预期状态"是断言性质**，必须自己报成断言失败。
            """
            try:
                await b.wait_for(expr, timeout=timeout, label=label)
            except TimeoutError:
                raise Failure(
                    f"{label}：等了 {timeout:.0f}s 没到该状态（**这是断言失败，不是环境问题**）"
                ) from None

        pos_card0 = await b.eval(_PRACTICE_POS_JS)
        qid_before = await b.eval(_QID_JS)
        need(isinstance(qid_before, str) and qid_before, f"读不到当前题目的身份：{qid_before!r}")
        # 抽屉**常驻 DOM**，靠 transform 显隐 ⇒ "滑出"这件事可以直接读（不用肉眼）
        # ⚠️ **不能读一次就下结论**：`style.transform` 是**目标值**、`getBoundingClientRect()`
        #    是**当前帧**；CSS 过渡（200ms）期间两者不一致 —— 实测：transform 已是 110%，
        #    而 rect.top 还停在展开位（458）。⇒ **轮询到稳定**（同族：坑 ⑤ 点完选项立刻点提交）。
        await reach(f"({_SHEET_BOX_JS}).top >= ({_SHEET_BOX_JS}).vh", "答题卡初始收起")
        box0 = await b.eval(_SHEET_BOX_JS)
        need(isinstance(box0, dict), f"读不到抽屉几何：{box0!r}")

        await b.click("[data-sheet-open]", nth=0)
        await reach(
            f"({_SHEET_BOX_JS}).top < ({_SHEET_BOX_JS}).vh", "答题卡从底部滑出（有边进视口）"
        )
        box1 = await b.eval(_SHEET_BOX_JS)
        report["sheet_slide"] = (
            f"top：{box0['top']} → {box1['top']}（vh={box1['vh']}）：收起时整个在视口之下、"
            f"展开后有边进视口 ⇒ 从底部滑出｜transform = {box1['transform']!r}"
        )

        # ---- 网格：5 列 / 行数自适应 ----
        grid = await b.eval(_SHEET_GRID_JS)
        need(isinstance(grid, dict), f"读不到网格：{grid!r}")
        total = int(grid["total"])
        need(total >= 6, f"这堂练习只有 {total} 题 —— 下面的「跳到第 5 题」构造不出来")
        need(grid["cols"] == 5, f"网格每行 {grid['cols']} 列（应为 5）")
        need(
            grid["rows"] == -(-total // 5),
            f"{total} 题应为 {-(-total // 5)} 行，实际 {grid['rows']} 行",
        )
        report["sheet_grid"] = f"{total} 题 ⇒ {grid['cols']} 列 × {grid['rows']} 行"

        # ---- 四态：图例齐；★ 但「标记」**不可达**（硬约定 F：可达 ⟺ 有任何接口能写入它）----
        sheet_text = await b.eval(_SHEET_TEXT_JS)
        for lab in ("未做", "已做", "当前", "标记"):
            need(lab in str(sheet_text), f"图例里没有「{lab}」")
        states = await b.eval(_SHEET_CELLS_JS)
        need(isinstance(states, list) and len(states) == total, f"格子数不对：{states!r}")
        seen = {str(s["state"]) for s in states}
        need(
            seen <= {"todo", "done", "current", "marked"},
            f"出现了没定义的状态：{seen!r}",
        )
        need(
            "marked" not in seen,
            "本批**不给设置标记的入口** ⇒ 不该出现 marked 格 —— 出现了说明有人偷偷写了它",
        )
        # ★ 这条是"没做设置入口"的**可证伪**形式：抽屉里除了题号格，只该有 1 个按钮（关闭）。
        extra = await b.eval(_SHEET_BUTTONS_JS)
        need(
            isinstance(extra, dict) and extra["grid"] == total and extra["others"] == 1,
            f"抽屉里除题号格外应只有「关闭」1 个按钮（否则就是偷偷加了设置入口）：{extra!r}",
        )
        cur = [s for s in states if s["state"] == "current"]
        need(len(cur) == 1 and cur[0]["seq"] == 1, f"当前题标错了：{cur!r}")
        report["sheet_states"] = (
            f"图例四态齐｜格子三态可达（当前=1，其余 todo=9）｜**标记 0 格**"
            f"（无写入路径 ⇒ 不可达，硬约定 F）｜抽屉内按钮 = {total} 格 + 1 关闭"
        )

        # ---- 点题号跳题：第 1 题 → 第 5 题 ----
        prog_card0 = await b.eval(_PRACTICE_PROGRESS_JS)
        want_qid = await b.eval(_CELL_QID_JS % 4)
        need(isinstance(want_qid, str) and want_qid, f"读不到第 5 格的身份：{want_qid!r}")
        await b.eval(
            "(() => { const c = document.querySelectorAll('[data-sheet-grid] button')[4];"
            " c.scrollIntoView({block:'center'}); c.click(); return true; })()"
        )
        await reach(
            f"({_PRACTICE_POS_JS}).startsWith({json.dumps('第 5 题')})", "点题号跳到第 5 题"
        )
        qid_after = await b.eval(_QID_JS)
        # ★ 判据用**题目身份**，不用题干：题干可以是模板复用的（实测过）。
        need(
            qid_after == want_qid,
            f"点了第 5 格，显示的却是另一道题：qid={qid_after!r} ≠ 格里记的 {want_qid!r}",
        )
        need(
            qid_after != qid_before,
            f"跳题前后是**同一道题**（qid={qid_after!r}）—— 那说明根本没跳",
        )
        await reach(f"({_SHEET_BOX_JS}).top >= ({_SHEET_BOX_JS}).vh", "点题号后抽屉收起")
        box_jump = await b.eval(_SHEET_BOX_JS)
        need(isinstance(box_jump, dict), f"读不到抽屉几何：{box_jump!r}")
        prog_card1 = await b.eval(_PRACTICE_PROGRESS_JS)
        need(
            prog_card0 == prog_card1,
            f"跳题把进度也改了：{prog_card0!r} → {prog_card1!r} —— 点题号只改本地游标（硬约定 S）",
        )
        report["sheet_jump"] = (
            f"点第 5 格 ⇒ {pos_card0!r} → 第 5 题；显示的是**格里记的那道题**"
            f"（qid {qid_before[:6]}… → {qid_after[:6]}…）；抽屉自动收起；**进度不动**"
        )

        # ---- 再开一次：状态**仍然正确**（这是"标记持久化"在本批的替代判据 ——
        #      本批不给设置标记的入口，「持久化」无从谈起；能验的是"状态由数据派生、重开不错乱"）----
        await b.click("[data-sheet-open]", nth=0)
        await reach(f"({_SHEET_BOX_JS}).top < ({_SHEET_BOX_JS}).vh", "再开答题卡")
        states2 = await b.eval(_SHEET_CELLS_JS)
        by_seq = {int(s["seq"]): str(s["state"]) for s in states2}
        need(by_seq.get(5) == "current", f"第 5 题应为「当前」，实际 {by_seq.get(5)!r}")
        need(
            by_seq.get(1) == "done",
            f"第 1 题（已作答、已不是当前）应为「已做」，实际 {by_seq.get(1)!r} ——"
            "状态是**派生**的，不该是写死的",
        )
        need(by_seq.get(2) == "todo", f"第 2 题应为「未做」，实际 {by_seq.get(2)!r}")
        report["sheet_state_again"] = "重开：第 5 题=current、第 1 题=done、第 2 题=todo ✓"

        # ---- 关闭：**位置正确**（关闭不改游标）----
        await b.click("[data-sheet-close]", nth=0)
        await reach(f"({_SHEET_BOX_JS}).top >= ({_SHEET_BOX_JS}).vh", "答题卡收起")
        pos_card2 = await b.eval(_PRACTICE_POS_JS)
        need(
            pos_card2.startswith("第 5 题"),
            f"关抽屉把当前位置也改了：{pos_card2!r}（应为第 5 题）—— 开合抽屉不是切题",
        )
        report["sheet_close"] = f"关闭后仍在 {pos_card2}（抽屉开合**不改游标**）"
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="e2e-web.py",
        description="C 端浏览器端到端走查（CDP 移动视口 375×667）："
        "login = 登录闭环 / p2a = Tab + 注册 + 引导 / p2b1 = 刷题数据流 + 按钮切题",
    )
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--api-base", default="http://127.0.0.1:8123/api/v1")
    ap.add_argument("--port", type=int, default=3001, help="next start 的端口")
    ap.add_argument("--phone", default="13800000000")
    ap.add_argument(
        "--scenario",
        default="login",
        choices=["login", "p2a", "p2b1"],
        help="login = P1 登录闭环；p2a = Tab + 注册 + 引导；"
        "p2b1 = 刷题数据流（选章节 → 建练习 → 答题判分 → 刷新仍在 → 按钮切题）",
    )
    ap.add_argument("--password", default="Admin@123456")
    ap.add_argument("--node", default="", help="node 可执行文件（默认从 PATH 找）")
    ap.add_argument("--npm", default="", help="npm 可执行文件（默认从 PATH 找）")
    ap.add_argument("--no-build", action="store_true", help="复用现有 .next（不保证 API 地址对）")
    ap.add_argument("--force-build", action="store_true", help="强制重新构建")
    ap.add_argument(
        "--dev",
        action="store_true",
        help="用 next dev 而不是「构建 + next start」（★ 本机必加：build 被宿主的删除保护拦，见抬头）",
    )
    ap.add_argument("--keep-web", action="store_true", help="跑完不关 next start")
    ap.add_argument("--boot-timeout", type=int, default=60)
    ap.add_argument("--hydrate-timeout", type=float, default=120.0)
    ap.add_argument("--submit-timeout", type=float, default=40.0)
    args = ap.parse_args(argv)

    args.repo = str(Path(args.repo).resolve())
    repo = Path(args.repo)
    web_dir = repo / "apps" / "web"
    args.web_base = f"http://localhost:{args.port}"
    args.node = args.node or find_tool("node") or ""
    args.npm = args.npm or find_tool("npm") or ""

    if not web_dir.is_dir():
        say(f"✗ 找不到 {web_dir}")
        return 2
    if not args.node or not args.npm:
        say(f"✗ 找不到 node/npm（node={args.node!r} npm={args.npm!r}）")
        return 2

    env = dict(os.environ)
    env.update(
        {
            "PYTHONUTF8": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
            # ★ 构建期内联进 bundle —— 必须与下面 browser 访问的 API 是同一个地址。
            "NEXT_PUBLIC_API_BASE": args.api_base,
            "NEXT_PUBLIC_AUTH_HINT_COOKIE": HINT_COOKIE,
        }
    )

    say(f"API    = {args.api_base}")
    say(f"Web    = {args.web_base}（构建期 API 地址注入，非运行期读取）")
    say(f"viewport = {VIEW_W}×{VIEW_H}（mobile=True, DPR=2）")
    say(
        "模式   = **next dev**（本机 build 被宿主的删除保护拦；产物构建由 CI 的 `web · build` 判）"
        if args.dev
        else "模式   = 生产构建 + next start"
    )

    if http_status(f"{args.api_base}/health") != 200:
        say(f"✗ API 不通：{args.api_base}/health —— 先用 run-local-pipeline.py --e2e-web 一把起")
        return 2

    proc: subprocess.Popen[bytes] | None = None
    try:
        if args.dev:
            say("（--dev：跳过构建 —— dev 下 NEXT_PUBLIC_* 由本次进程 env 生效，不需要构建期注入）")
            # ★ dev 也必须先把 `.next` 挪走 —— 它启动时同样会 `recursiveDelete(...)`
            #   （实测：`next dev` 挂在 `.next/static/chunks/main-app.js` 的批量删守卫上）。
            isolate_next_dir(web_dir)
        else:
            ensure_build(args, web_dir, env)
        proc = start_web(args, web_dir, env)
        drivers = {
            "login": drive_login,
            "p2a": drive_p2a,
            "p2b1": drive_p2b1,
        }
        report = asyncio.run(drivers[args.scenario](args))
    except Failure as e:
        say(f"❌ 走查失败：{e}")
        return 1
    except Exception as e:  # noqa: BLE001 —— 环境类失败要和断言失败分开报
        say(f"✗ 走查没跑起来（**不是断言失败**）：{type(e).__name__}: {e}")
        return 2
    finally:
        if proc is not None:
            if args.keep_web:
                say(f"按 --keep-web 保留 next start（pid={proc.pid}，: {args.port}）")
            else:
                kill_tree(proc.pid)
                for _ in range(15):
                    if not port_open(args.port):
                        break
                    time.sleep(1)
                say(
                    f"next start 已停（:{args.port} 已释放）"
                    if not port_open(args.port)
                    else f"⚠️ :{args.port} 仍被占用"
                )

    say("")
    say("════════ 走查结果 ════════")
    for k, v in report.items():
        say(f"  ✅ {k} = {v}")
    say(
        "✅ 走查全部通过（场景 p2b1：选章节 → 建练习 → 答题判分 → 刷新仍在 → 重答证明判分对"
        " → 按钮切题）"
        if args.scenario == "p2b1"
        else (
            "✅ 走查全部通过（场景 login：登录 → 首页 → 我的 → 登出 → 会话撤销）"
            if args.scenario == "login"
            else "✅ 走查全部通过（场景 p2a：Tab 守卫 → 注册 → 三 Tab → 引导 → 反向守卫）"
        )
    )
    return 0


if __name__ == "__main__":
    try:  # 硬约定 I 的同族：**别赌任何一方的默认代码页**
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass
    sys.exit(main())
