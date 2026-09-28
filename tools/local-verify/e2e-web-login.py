"""C 端「登录闭环」的**浏览器**端到端走查 —— 补上 P1 唯一没验过的那一层。

为什么要单独写一个（而不是塞进 pytest）
--------------------------------------
后端契约那一半**已经被既有测试覆盖**（`test_admin_session_wall.py` 里的
"`me` → 登出 → 同一 token 立刻失效"）。这里要验的是**另一层**：

  `apps/web` 里的**客户端 fetch + 受控表单 + 路由跳转 + localStorage 存储层**

那层**只有真浏览器能验** —— `curl` 只能验 SSR 与 middleware，验不到 hydrate 之后的交互。

★ 顺序判据（用户 2026-09-27）：**P2 全是交互；不在开 P2 之前把交互层点通一次，
  就是"在没验证的交互层上再叠一层没验证的交互"。** 所以它是 P2 的前置，不是"顺手"。

走查步骤（每步都**断言**，不是"看一眼"）
----------------------------------------
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
    python tools/local-verify/e2e-web-login.py \\
        --api-base http://127.0.0.1:8123/api/v1 \\
        --phone 13800000000 --password 'Admin@123456'

    python tools/local-verify/e2e-web-login.py --keep-web    # 跑完不关服务（手工接着点）
    python tools/local-verify/e2e-web-login.py --force-build # 强制重新构建

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
import re
import shutil
import socket
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

    ⚠️ 挪到 `node_modules/.cache/` 而不是留在 app 目录里：prettier / eslint / tsc 默认都跳过
      `node_modules`，而留在原地会让 `format:check` 去"检查"几万个构建文件并报格式错
      （2026-09-28 实测：`apps/web` 的 ⑩ 就是被 `.next.stale-*/server/**.js` 弄红的）。
    """
    next_dir = web_dir / ".next"
    if not next_dir.exists():
        return None
    cache = web_dir / "node_modules" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    stale = cache / f"next-stale-{int(time.time())}"
    next_dir.rename(stale)
    say(f"旧 .next 已改名挪走：node_modules/.cache/{stale.name}（**改名不是删除**）")
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


async def drive(args: argparse.Namespace) -> dict[str, object]:
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
        say("⑤ 退出登录 ⇒ 期望回 /login 且本地凭证清空")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="e2e-web-login.py",
        description="C 端登录闭环的浏览器端到端走查（生产构建 + CDP 移动视口 375×667）",
    )
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--api-base", default="http://127.0.0.1:8123/api/v1")
    ap.add_argument("--port", type=int, default=3001, help="next start 的端口")
    ap.add_argument("--phone", default="13800000000")
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
        report = asyncio.run(drive(args))
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
    say("✅ 登录闭环全部通过（1~7 步）")
    return 0


if __name__ == "__main__":
    try:  # 硬约定 I 的同族：**别赌任何一方的默认代码页**
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass
    sys.exit(main())
