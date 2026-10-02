"""对 `check-invariants.py` 做**变异验证**：打掉被测对象，检查必须变红。

判据（硬约定 J）：一个"检查"如果打掉它要检查的东西还是绿的，它就不是检查。
★ **逐条**变异，每条对应一处判据，**都必须被捕获**；还原用**文件备份**（不用 `git checkout`）。
⚠️ 数量**刻意不写在这里**（写死的数字一定会过期）：以 `MUTANTS` 的实际条数为准，
   输出的末行会报"捕获 N / 应用 M / 未应用 K"。

    python tools/local-verify/mutate-invariants.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "tools" / "local-verify" / "check-invariants.py"

#: 备份落点：**仓库外**（见下面 `bak =` 处的理由）。
_BAK_DIR = Path(tempfile.gettempdir()) / "yijian-mutantbak"

#: `old` / `new` 为 `str` 时按 UTF-8 文本替换；为 `bytes` 时按字节替换（用于 PNG 这类二进制）。
MUTANTS: list[tuple[str, Path, str | bytes, str | bytes]] = [
    (
        "[1] 门禁：把 `pytest` 那道步骤改名",
        REPO / ".github" / "workflows" / "ci.yml",
        "- name: pytest",
        "- name: pytest_RENAMED_BY_MUTANT",
    ),
    (
        "[1] 反向：加一道**没登记**的「拦截式」门禁",
        REPO / ".github" / "workflows" / "ci.yml",
        "      - name: 不变量自检（拦截式）\n        run: bash tools/check-invariants.sh",
        "      - name: 不变量自检（拦截式）\n        run: bash tools/check-invariants.sh\n\n"
        "      - name: 假门禁（拦截式）\n        run: true",
    ),
    (
        "[2] 棘轮：把 fail_under 从 94.40 调低到 94.00",
        REPO / ".coveragerc",
        "fail_under = 94.40",
        "fail_under = 94.00",
    ),
    (
        "[3] 一次性库：把 setdefault 改回强制赋值",
        REPO / "tools" / "local-verify" / "serve_fake_redis.py",
        'os.environ.setdefault(\n        "DATABASE_URL"',
        'os.environ["DATABASE_URL"] = (\n        "DATABASE_URL"',
    ),
    (
        # ★ 不变量 6（2026-09-29 加）：打掉"删除保护"的判据 —— 往一个**被扫到的**文件里
        #   塞一行**未登记**的 `shutil.rmtree`。检查必须变红。
        #   ⚠️ 选 `smoke-db.py` 是因为它**不参与**检查器自身的加载（改坏了也不影响这次运行），
        #      且 `from __future__ import annotations` 在它里面唯一。
        "[6] 删除保护：往被扫到的文件里塞一行未登记的 shutil.rmtree",
        REPO / "tools" / "local-verify" / "smoke-db.py",
        "from __future__ import annotations",
        "from __future__ import annotations\n\n"
        "def _mutant_cleanup(p):  # 变异体：故意不做任何登记\n"
        # trash-ok: 这是**变异体源码的字符串字面量**（要被注入到别的文件里），不是本文件的删除动作
        "    shutil.rmtree(p)",
    ),
    (
        # ★ 不变量 7（2026-09-29 加）：把 `create_session` 的 `await db.commit()` 删掉 ——
        #   这正是 P2b-1 真实踩过的 bug（`get_db` 不自动提交）。**必须变红。**
        #   这条尤其重要：它要能抓到"SQL 装在**模块级常量**里"的写法
        #   （函数体里只有 `db.execute(_INSERT_SESSION, …)`），否则看着在跑、实际抓不到。
        "[7] 写路径提交：删掉 `practice_service.create_session` 的 `await db.commit()`",
        REPO / "apps" / "api" / "app" / "services" / "practice_service.py",
        "    await db.commit()\n    return sid",
        "    return sid",
    ),
    (
        # ★ 不变量 8（2026-09-29 加）：让导入管道**自己拼** `{"value": ["A"]}`（绕过规范形式）。
        #   必须变红 —— 否则"答案只有一个构造入口"就是句空话。
        "[8] 答案单一入口：让 `_build_answer` 绕过 `canonical_doc` 自己拼字典",
        REPO / "apps" / "api" / "app" / "services" / "import_service.py",
        '        return canonical_doc("judge", [answer_raw.strip().upper()])',
        '        return {"value": [answer_raw.strip().upper()]}',
    ),
    # ---- 不变量 9 / 10（2026-10-02 加，PWA 批次）----
    (
        # ★ 不变量 10-a：把 manifest 的 `display` 从 `standalone` 改掉 ——
        #   判据是"装到桌面后**没有地址栏**"，而它唯一的来源就是这一行。
        #   改错的后果在**本机看不出来**（浏览器里照样能开，只是装出来像书签）。
        "[10] PWA manifest：`display` 从 standalone 改成 minimal-ui",
        REPO / "apps" / "web" / "src" / "app" / "manifest.ts",
        'display: "standalone",',
        'display: "minimal-ui",',
    ),
    (
        # ★ 不变量 10-a（负向那一头）：把 512 图标从 manifest 里拿掉。
        #   缺它时 **Chrome 不显示「安装」入口**，而页面看起来完全正常 ——
        #   这正是"必须写进门禁"的典型：症状是"少一个按钮"，没人会为此建 bug。
        "[10] PWA manifest：删掉 512 图标那条",
        REPO / "apps" / "web" / "src" / "app" / "manifest.ts",
        '      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },\n',
        "",
    ),
    (
        # ★ 不变量 10-b：**让文件名撒谎** —— 把 `icon-192.png` 的 IHDR 宽高字节改成 512。
        #   这是"本机没有浏览器读数 ⇒ 只能真机才发现"那一类，**只能靠读字节挡**。
        #   ⚠️ 改完 IHDR 的 CRC 会失效，但本检查只读前 24 字节（不验 CRC），
        #      CI 里也没有别的东西解析这张图 ⇒ 作为变异体足够，且**随后立刻还原**。
        "[10] PWA 图标：把 icon-192.png 的 IHDR 宽高字节改成 512（文件名撒谎）",
        REPO / "apps" / "web" / "public" / "icons" / "icon-192.png",
        b"\x00\x00\x00\x0dIHDR\x00\x00\x00\xc0\x00\x00\x00\xc0",
        b"\x00\x00\x00\x0dIHDR\x00\x00\x02\x00\x00\x00\x02\x00",
    ),
    (
        # ★ 不变量 10-c（负向）：往 SW 的缓存白名单里塞一个**接口路径**。
        #   后果是"悄悄缓存了不该缓存的响应"，**不报错、也扫不出来** ——
        #   只有这条判据能挡（它就是为这个而写的）。
        "[10] SW 白名单：往里塞一个接口路径（默认拒绝被破坏）",
        REPO / "apps" / "web" / "public" / "sw.js",
        'const CACHE_PREFIXES = ["/_next/static/", "/icons/"];',
        'const CACHE_PREFIXES = ["/_next/static/", "/icons/", "/api/"];',
    ),
    (
        # ★ 不变量 10-c（正向）：把静态资源前缀拿掉 —— 那 SW 就不缓存任何 JS/CSS 了，
        #   "静态资源可用缓存"这个承诺作废（而离线页仍然能开 ⇒ **看起来一切正常**）。
        "[10] SW 白名单：拿掉 `/_next/static/`",
        REPO / "apps" / "web" / "public" / "sw.js",
        'const CACHE_PREFIXES = ["/_next/static/", "/icons/"];',
        'const CACHE_PREFIXES = ["/icons/"];',
    ),
    (
        # ★ 不变量 9 的**连带**（2026-10-02 加）：`/offline.html` 忘了登记成公开路径。
        #   它同时会被不变量 10-d 抓到（同一件事的两道闸），这里验的是 10-d 那一头。
        #   ⚠️ 为什么这条特别值得有变异体：这个缺陷**在已登录状态下完全看不出来**
        #      （有 cookie 就放行）⇒ 人肉测试会假绿，只有门禁能挡。
        "[10] 未登录可达：把 `/offline.html` 从 `PUBLIC_PREFIXES` 里删掉",
        REPO / "apps" / "web" / "src" / "middleware.ts",
        '"/offline.html"];',
        '"];',
    ),
]


def run_check() -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, str(CHECK)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode, r.stdout


def main() -> int:
    rc0, out0 = run_check()
    print(f"=== 基线：rc={rc0}（期望 0）===")
    if rc0 != 0:
        print(out0[-2000:])
        print("!! 基线不是绿的 —— 先修基线，变异验证没有意义")
        return 2

    caught = applied = 0
    for label, path, old, new in MUTANTS:
        # ★★ 备份放在**仓库外**（`%TEMP%`），不是 `path.with_suffix(".mutantbak")`。
        #   2026-09-29 实测：一次运行被中断（SIGTERM）⇒ 仓库里留下了一个
        #   `import_service.py.mutantbak`，它**不是**版本控制里的东西，会以"意外的未跟踪文件"
        #   出现在 `git status` 里（收尾纪律 §7 专门盯着这一类）。
        #   ⇒ 判据：**临时文件不该落在工作树里** —— 崩了也要能自证现场干净。
        bak = _BAK_DIR / f"{path.name}.bak"
        _BAK_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bak)
        try:
            # ★ 支持**二进制**变异（2026-10-02 加）：`old` 是 `bytes` 时按字节替换。
            #   为什么需要这一档：不变量 10-b 的判据是"图标的**真实像素**要和文件名一致"，
            #   而它的变异体只能是"把一张图的 IHDR 宽高改掉" —— 那是**字节**操作，
            #   走不了 `read_text`（PNG 不是合法 UTF-8）。
            #   加这一档，比"这条判据没有变异体"好得多：**没有变异体的判据等于没有判据**（硬约定 J）。
            is_bytes = isinstance(old, bytes)
            s = path.read_bytes() if is_bytes else path.read_text(encoding="utf-8")
            if s.count(old) != 1:
                # ★ "锚点不匹配"必须**单独计成"未应用"**，不能算"存活"（硬约定 J）
                print(f"\n{label}\n  [未应用] 锚点匹配 {s.count(old)} 次（应为 1）")
                continue
            patched = s.replace(old, new, 1)
            if is_bytes:
                path.write_bytes(patched)
            else:
                path.write_text(patched, encoding="utf-8", newline="")
            applied += 1
            rc, out = run_check()
            fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
            if rc != 0:
                caught += 1
                print(f"\n{label}\n  rc={rc} ✅ **变红**（检查有效）")
            else:
                print(f"\n{label}\n  rc={rc} ❌ **存活**（检查没抓到它）")
            for f in fails:
                print(f"    {f}")
        finally:
            shutil.copy2(bak, path)
            # trash-ok: 单文件（变异前的备份，在 %TEMP% 下），非递归
            bak.unlink(missing_ok=True)

    rc1, _ = run_check()
    print(f"\n=== 还原后复跑：rc={rc1}（期望 0）===")
    print(f"=== 捕获 {caught} / 应用 {applied} / 未应用 {len(MUTANTS) - applied} ===")
    return 0 if (caught == applied and rc1 == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
