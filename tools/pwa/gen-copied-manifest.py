"""生成 `apps/pwa/COPIED-FROM-WEB.md`（复制品清单 + 可判定理由）。

★ 为什么用脚本生成而不是手写：清单要**逐条**包含"目标 / 来源 / 关系 / 理由 / 判据"，
  手写 26 行必然漂（漏一行 = 门禁的覆盖面悄悄缺一块）。脚本从**磁盘上真实存在的文件**
  列清单 ⇒ "复制了但忘了登记"这件事从根上不可能发生（门禁再做一次独立核对）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path("C:/My Protect/WorkBuddy/ONE Build/yijian-platform")
WEB = REPO / "apps" / "web"
PWA = REPO / "apps" / "pwa"

#: 有意分叉 / 只在 PWA 存在的文件：**每条都要有可判定的理由**。
#: ★ 理由的写法规矩（用户 2026-10-06 定）：**指名一个具体的依赖或数据差异**，
#:   且**能被一条命令验证**。禁止「有意简化 / 暂时这样 / 后续再改」——
#:   那些能套在任何一个文件上，等于没写。
FORK: dict[str, tuple[str, str, str]] = {
    "src/lib/api.ts": (
        "数据源不同：C 端是 HTTP（`fetch` 到 `/api/v1`）、带 token 与 401 单飞刷新；"
        "这里读 **IndexedDB**，且没有任何认证链路",
        "本文件里 `fetch(` 出现 0 次（有的话说明 HTTP 那一半没删干净）",
        "永久",
    ),
    "public/sw.js": (
        "缓存策略不同：C 端的数据在网络上 ⇒ **不缓存页面 HTML**（缓存只会制造陈旧页面）；"
        "本应用的数据本来就在本机 ⇒ 必须缓存**应用壳**（否则离线只剩浏览器错误页）",
        "两个文件的 `fetch` 处理分支不同（本文件是 `cache-first` + 后台更新）",
        "永久",
    ),
    "src/app/layout.tsx": (
        "站点标题/描述不同（`一建通 · 离线版`），且不再需要与登录态相关的元信息",
        "`grep -c '离线版' apps/pwa/src/app/layout.tsx` → 1",
        "永久",
    ),
    "src/app/(tabs)/layout.tsx": (
        "C 端有两层登录守卫（`middleware.ts` + `hasToken()`）与 3 个 Tab；"
        "本应用没有登录这个状态，只有 2 个 Tab",
        "`apps/pwa/src/middleware.ts` 不存在（test ! -f）",
        "永久",
    ),
    "src/app/(tabs)/page.tsx": (
        "C 端首页读 `/auth/me` 的 profile、并按 `onboarded_at` 决定是否送去引导；"
        "本应用没有账号与引导 ⇒ 首页改问**题库状态**（有没有导入过）",
        "`grep -c 'auth/me' apps/pwa/src/app/(tabs)/page.tsx` → 0",
        "永久",
    ),
    "src/app/(tabs)/practice/page.tsx": (
        "少一个「错题本」入口：那一行在 C 端链到 `/practice/wrong`，"
        "而这条路由在 `apps/pwa/src/app` 下**不存在**（该页属 B-2）"
        "—— 渲染一个指向不存在路由的入口，点开是 404，比没有入口更糟",
        "`apps/pwa/src/app/practice/wrong` 目录不存在（该路由尚未建）",
        "B-2 建好错题本页后，本文件将**逐字节相同**",
    ),
    "package.json": (
        "包名 / 端口 / 描述不同（3002），且**不消费** `packages/api-core`"
        "（那个包是认证链路的唯一实现，本应用没有认证链路）",
        "`grep -c 'api-core' apps/pwa/package.json` → 0",
        "永久",
    ),
    "tsconfig.json": (
        "没有 `@yijian/api-core` 的路径映射（理由同上），因此也不需要 "
        "`allowImportingTsExtensions`",
        "`grep -c 'api-core' apps/pwa/tsconfig.json` → 0",
        "永久",
    ),
}

#: 只在 PWA 存在、C 端没有对应物（或对应物完全不同）的文件。
ONLY: dict[str, tuple[str, str]] = {
    "src/lib/db.ts": (
        "IndexedDB 的 schema 与读写助手 —— C 端没有数据层（它在服务端），无处可抄",
        "永久",
    ),
    "src/lib/grade.mjs": (
        "判分的**哑比较**实现。★ 它不是 C 端 `grade()` 的复制品，而是"
        "「包已归一」前提下的等价实现 —— 等价性由 `tools/pwa/parity.py` 逐条对拍守住",
        "永久",
    ),
    "src/app/setup/page.tsx": (
        "导入题库 + 换题库的二次确认 —— C 端的题库在服务端，没有「导入」这件事",
        "永久",
    ),
    "src/favicon.ico": ("Next 默认图标占位（若存在）", "永久"),
}

#: 不该进清单的东西。
#: ★ `package-lock.json` **由 npm 生成**，不是"从 C 端复制过来的" ——
#:   它对每个 app 本来就**必须不同**（各装各的依赖树），拿它与 C 端逐字节比是没意义的。
#:   ⚠️ 但**它要提交**（CI 的 `npm ci` 依赖它）—— 不登记 ≠ 不提交，这两件事别混。
#:   （实测：第一次生成清单时 npm install 还没跑完，`package-lock.json` 还不存在；
#:     之后它一出现，门禁的"覆盖"那条立刻红 —— **那正是它该有的行为**。）
SKIP = {
    "next-env.d.ts",
    "tsconfig.tsbuildinfo",
    "package-lock.json",
    ".next",
    "node_modules",
    "COPIED-FROM-WEB.md",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def walk(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel == s or rel.startswith(s + "/") for s in SKIP):
            continue
        out.append(rel)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="生成 / 校验 apps/pwa/COPIED-FROM-WEB.md")
    ap.add_argument(
        "--check",
        action="store_true",
        help="只校验（不写盘）：磁盘上的文件必须与本脚本现在会生成的内容逐字节一致",
    )
    args = ap.parse_args(argv)

    pwa_files = walk(PWA)
    web_files = set(walk(WEB))

    same: list[dict] = []
    fork: list[dict] = []
    only: list[dict] = []

    for rel in pwa_files:
        if rel in ONLY:
            reason, converge = ONLY[rel]
            only.append({"to": rel, "why": reason, "converge": converge})
            continue
        if rel not in web_files:
            # 既不在 ONLY 里、C 端也没有 ⇒ 漏登记
            only.append(
                {
                    "to": rel,
                    "why": "（★ 未登记 —— 请在生成脚本的 ONLY 表里补一条理由）",
                    "converge": "?",
                }
            )
            continue
        if rel in FORK:
            reason, check, converge = FORK[rel]
            fork.append(
                {"to": rel, "from": rel, "reason": reason, "check": check, "converge": converge}
            )
        else:
            a, b = sha(WEB / rel), sha(PWA / rel)
            same.append({"to": rel, "from": rel, "sha256": b[:16], "identical": a == b})

    # 只登记了 ONLY 却真的在 web 有同名文件 ⇒ 分类写错了
    for rel in list(ONLY):
        if rel in web_files and (PWA / rel).exists():
            raise SystemExit(f"✗ {rel} 在 apps/web 里也有 —— 它该进 FORK 表，不是 ONLY")

    payload = {
        "generated_by": "tools/pwa/gen-copied-manifest.py",
        "copied_identical": same,
        "copied_fork": fork,
        "pwa_only": only,
    }

    lines: list[str] = []
    lines.append("# PWA 相对 C 端的**复制品清单**（门禁 `不变量 12` 读它）")
    lines.append("")
    lines.append(
        "`apps/pwa` 的 UI 是**从 `apps/web` 复制的** —— 这是用户 2026-10-06 定的方案"
        "（不抽共享包：抽包要动 C 端，风险大、收益低）。"
    )
    lines.append("")
    lines.append(
        "**复制带来的新风险**：C 端与 PWA 的同一份 UI 变成两份 ⇒ 在 C 端修了 bug、"
        "PWA 侧没修，而且**不报错**（「同一个事实两种写法」那一族）。"
    )
    lines.append("")
    lines.append("⇒ 对策就是**本清单 + 一条门禁**（`tools/local-verify/check-invariants.py`）：")
    lines.append("")
    lines.append("1. 清单**必须覆盖**每一个「两个 app 都有」的文件（漏登记 ⇒ 门禁红）；")
    lines.append("2. 标 `same` 的**必须逐字节相同**（下面给了 sha256 前 16 位）；")
    lines.append("3. 标 `fork` 的**必须写理由**，而理由是**可判定的** ——")
    lines.append("   要指名一个具体依赖或数据差异，并给出一条能验它的命令。")
    lines.append("")
    lines.append("   ★ 反面写法（门禁会直接判红）：`有意简化` / `暂时这样` / `后续再改` / `先复制过来`")
    lines.append("     —— **它们能套在任何一个文件上**，等于没有理由（与「以后 / 时机未到」是同一族）。")
    lines.append("")
    lines.append("4. `pwa_only` 是「只在 PWA 存在」的文件：它同样要写理由，且门禁会核对")
    lines.append("   **C 端确实没有同名文件**（防止把「其实是复制品」的东西挂到这一类里逃避比对）。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"## 1. 逐字节相同（{len(same)} 个）—— C 端一改，这里就红，提醒你同步"
    )
    lines.append("")
    lines.append("| 文件（`apps/pwa/` 与 `apps/web/` 下同路径） | sha256[:16] |")
    lines.append("|---|---|")
    for e in same:
        mark = "" if e["identical"] else " ⚠️ **当前不一致**"
        lines.append(f"| `{e['to']}`{mark} | `{e['sha256']}` |")
    lines.append("")
    lines.append(f"## 2. 有意分叉（{len(fork)} 个）—— 每个理由都要能验")
    lines.append("")
    for e in fork:
        lines.append(f"### `{e['to']}`")
        lines.append("")
        lines.append(f"- **理由**：{e['reason']}")
        lines.append(f"- **判据（怎么验）**：{e['check']}")
        lines.append(f"- **是否收敛**：{e['converge']}")
        lines.append("")
    lines.append(f"## 3. 只在 PWA 存在（{len(only)} 个）")
    lines.append("")
    lines.append("| 文件 | 为什么 C 端没有对应物 | 是否收敛 |")
    lines.append("|---|---|---|")
    for e in only:
        lines.append(f"| `{e['to']}` | {e['why']} | {e['converge']} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. C 端有、PWA **刻意没有**的文件（不算分叉，因为是「没复制」）")
    lines.append("")
    lines.append("| C 端文件 | 为什么 PWA 没有 |")
    lines.append("|---|---|")
    lines.append(
        "| `src/middleware.ts` | 没有登录 ⇒ 没有「未登录跳转」这件事。留着它就是**永不触发的守卫** |"
    )
    lines.append("| `src/lib/auth-store.ts` | 同上（它存 token） |")
    lines.append("| `src/lib/json-bigint.ts` | 它是为了**雪花 ID 不被 `JSON.parse` 舍入**；本应用没有服务端 ID |")
    lines.append("| `src/app/login` `register` `onboarding` | 本版**砍掉**（零后端的代价，用户已确认） |")
    lines.append("| `src/app/(tabs)/me/**` | 账号相关的 Tab；设置（导出/导入）在 B-3 加 |")
    lines.append("| `src/app/practice/wrong/**` | 属 **B-2**（错题本 + 收藏 + 标记 + 笔记） |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. 机器可读部分（门禁读这个代码块，不要手改格式）")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")

    out = PWA / "COPIED-FROM-WEB.md"
    rendered = "\n".join(lines) + "\n"

    if args.check:
        # ★★ **为什么必须有 `--check`**（2026-10-06 变异验证抓到的真缺口）：
        #   清单里同一个事实写了**两遍** —— 人读的表格 + 机器读的 JSON 块。
        #   只比 JSON 的话，手改**表格**不会红（两侧可以各说各话）；
        #   而"手改 JSON"又能绕过理由检查。⇒ 唯一干净的解法是**让它是生成的**：
        #   磁盘上的内容必须与脚本此刻会产出的一致。这样"改理由"只能改**脚本里的表**，
        #   也就是**只有一处**（与不变量 8 的'一个字段只能有一种写法'同族）。
        if not out.is_file():
            print(f"✗ {out} 不存在 —— 跑一次生成（不带 --check）")
            return 1
        on_disk = out.read_text(encoding="utf-8")
        if on_disk != rendered:
            print("✗ 清单**不是最新的**：磁盘内容 ≠ 本脚本现在会生成的内容。")
            print("  ⇒ 说明有人手工改过它（表格或 JSON 块），或者改了本脚本的表却没重新生成。")
            print("  ⇒ 理由只能写在**本脚本的表**里 —— 那是它的唯一真相来源。")
            print("  修法：`python tools/pwa/gen-copied-manifest.py`（不带 --check）再提交。")
            d = [
                f"      - {a[:70]}"
                for a, b in zip(on_disk.splitlines(), rendered.splitlines())
                if a != b
            ][:3]
            print("\n".join(d) if d else "      （差异在行数，不是内容）")
            return 1
        print(f"✓ {out} 是最新的（与生成脚本一致，共 {len(rendered.splitlines())} 行）")
        return 0

    out.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"✓ {out}")
    print(f"  same={len(same)}  fork={len(fork)}  only={len(only)}")
    miss = [e["to"] for e in only if "未登记" in e["why"]]
    if miss:
        print("  ⚠️ 有文件既不在 FORK 也不在 ONLY：", miss)
        return 1
    return 0


if __name__ == "__main__":
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
