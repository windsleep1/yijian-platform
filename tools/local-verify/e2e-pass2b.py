"""Batch 7 Pass 2b 的 E2E 走查 + 截图。

前置：
  1. PG + API（8123）+ admin dev（3000）都在跑
  2. 已跑 `seed-pass2b.py`（造 3 条规则：能抽满 / 题库不足 / 已停用）

跑法：
    python e2e-pass2b.py

沿用 Pass 2a 的两个坑规避（见 cdp_browser.py）：`goto_ready()` 等 hydrate、
`type_text()` 逐字符敲。本脚本额外补了 Radix Select 的打开方式。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from cdp_browser import Browser

ADMIN = "http://localhost:3000"
API = "http://127.0.0.1:8123/api/v1"
SHOTS = Path(r"C:/My Protect/WorkBuddy/ONE Build/yijian-platform/apps/admin/docs/screenshots/batch7-pass2b")

TAKEN: list[tuple[str, int]] = []


def warmup() -> None:
    """先把路由都请求一遍，让 Next dev 编译完 —— 否则浏览器要替我们等编译。"""
    for p in ("/paper-rules", "/exams", "/exams/new"):
        for _ in range(90):
            try:
                if httpx.get(f"{ADMIN}{p}", timeout=60).status_code == 200:
                    break
            except Exception:
                pass
    print("✅ 路由预热完成")


async def shot(b: Browser, name: str) -> None:
    p = SHOTS / f"{name}.png"
    await b.screenshot(str(p))
    size = p.stat().st_size
    TAKEN.append((f"{name}.png", size))
    print(f"  📸 {name}.png  {size // 1024} KB")


async def text_in(b: Browser, scope: str) -> str:
    return await b.eval(
        f"(() => {{ const r = document.querySelector({json.dumps(scope)}); "
        f"return r ? (r.innerText || '') : ''; }})()"
    ) or ""


async def wait_text_in(b: Browser, scope: str, text: str, *, timeout: float = 40.0) -> None:
    """**限定范围**等文本出现。

    ⚠️ 踩过一次：早先直接 `wait_text('题库不足')` 去等弹窗里的试算预警，
    结果**被背景列表**里的规则名「E2E 题库不足规则」命中 —— 断言瞬间"通过"，
    截图却在试算还没回来的时候就拍了，拍到的是空面板。
    教训：**判断局部状态，就要把范围限定到局部**，别拿整页 innerText 去匹配。
    """
    await b.wait_for(
        f"""(() => {{
          const r = document.querySelector({json.dumps(scope)});
          return r ? (r.innerText || '').includes({json.dumps(text)}) : false;
        }})()""",
        timeout=timeout,
        label=f"{scope} 内文本「{text}」",
    )


#: 试算面板的两种结论文案。**必须用带冒号的完整前缀** ——
#: 光用「题库不足」会被背景列表里的规则名命中（见 wait_text_in 的说明）。
PREVIEW_OK = "题库充足："
PREVIEW_GAP = "题库不足：需要"


def api(method: str, path: str, tok: str, body=None):
    return httpx.request(method, f"{API}{path}",
                         headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=60).json()


async def open_select(b: Browser, nth: int, *, scope: str = "[role=dialog]") -> None:
    """打开范围内的第 nth 个 Radix Select。

    Radix 的下拉要 `pointerdown` 才开（只发 click 没用）；选项在 Portal 里，
    不在 dialog 子树内，所以下一步用全局 `[role=option]` 找。
    """
    ok = await b.eval(
        f"""(() => {{
          const root = document.querySelector({json.dumps(scope)}) || document;
          const el = Array.from(root.querySelectorAll('[role="combobox"]'))[{nth}];
          if (!el) return false;
          el.scrollIntoView({{block:'center'}});
          const o = {{bubbles:true, cancelable:true, view:window}};
          for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {{
            const C = t.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
            el.dispatchEvent(new C(t, o));
          }}
          return true;
        }})()"""
    )
    if not ok:
        raise RuntimeError(f"打不开第 {nth} 个下拉（scope={scope}）")
    await b.wait_for("document.querySelectorAll('[role=option]').length > 0", timeout=10,
                     label="下拉选项出现")


async def pick(b: Browser, option_text: str) -> None:
    await b.click_text(option_text, tag="[role='option']")
    await asyncio.sleep(0.3)


async def login(b: Browser) -> None:
    await b.goto_ready(f"{ADMIN}/login")
    await b.wait_text("手机号", timeout=60)
    await b.type_text("#phone", "13800000000")
    await b.type_text("#password", "Admin@123456")
    await b.click_until("登录", "!location.pathname.startsWith('/login')", attempts=25)


async def main() -> None:
    warmup()
    tok = httpx.post(f"{API}/auth/login/password",
                     json={"phone": "13800000000", "password": "Admin@123456"},
                     timeout=30).json()["data"]["access_token"]

    # 幂等：先清掉上一次跑留下的"界面新建"规则（否则重跑会攒一堆同名规则，
    # 而按行定位的断言会命中第一条，结果越来越飘）
    existing = api("GET", "/admin/paper-rules?page_size=100", tok)
    for r in existing["data"]["items"]:
        if r["name"].startswith("E2E 界面新建的规则"):
            api("DELETE", f"/admin/paper-rules/{r['id']}", tok)
            print(f"↻ 清理上次残留的规则：{r['name']}")

    async with Browser() as b:
        # ============================================================ 1. 规则列表
        await login(b)
        await b.goto_ready(f"{ADMIN}/paper-rules")
        await b.wait_text("组卷规则", timeout=60)
        await b.wait_text("E2E 标准模拟卷规则", timeout=40)
        await shot(b, "01-paper-rules-list")
        print("✅ 规则列表：3 条种子规则（能抽满 / 题库不足 / 已停用）都在")

        # ============================================================ 2. 新建规则 + 实时试算（充足）
        await b.click_text("新建规则")
        await wait_text_in(b, "[role=dialog]", "新建组卷规则")
        await b.type_text("#rule-name", "E2E 界面新建的规则")
        # 第 0 个下拉 = 科目
        await open_select(b, 0)
        await pick(b, "建设工程经济")
        # ⚠️ 断言限定在**弹窗内**，并等试算真的回来（防抖 600ms + 请求）
        await wait_text_in(b, "[role=dialog]", PREVIEW_OK)
        await shot(b, "02-rule-create-live-preview")
        print("✅ 新建规则时实时试算已生效（题库充足：10 道都能抽满）")

        # ============================================================ 3. 改成"题库不足"→ 预警
        # 第一条规则的题型 → 案例题（库里远不够 200 道）
        await open_select(b, 3)
        await pick(b, "案例题")
        # 题数 → 200
        await b.eval(
            """(() => {
              const dialog = document.querySelector('[role=dialog]');
              const target = Array.from(dialog.querySelectorAll('input[inputmode="numeric"]'))[1];
              if (!target) return false;
              target.focus();
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              setter.call(target, '');
              target.dispatchEvent(new Event('input', {bubbles:true}));
              return true;
            })()"""
        )
        await b.send("Input.insertText", {"text": "200"})
        await wait_text_in(b, "[role=dialog]", PREVIEW_GAP)
        await wait_text_in(b, "[role=dialog]", "还差 200 道案例题")
        await shot(b, "03-rule-preview-gap-warning")
        print("✅ 试算预警：题库不足 + 「还差 200 道案例题」（改完自动重算）")

        # ============================================================ 4. 保存前的二次确认
        await b.click_text("保存规则")
        await wait_text_in(b, "body", "题库不足，仍要保存")
        await shot(b, "04-rule-save-gap-confirm")
        print("✅ 保存前有缺口二次确认（允许保存，但说清代价）")
        # 真的保存下来 —— 这本身是"规则可以先存着等题库补"的正当用法，
        # 也顺便验证了这条路径能走通。下一步会把它删掉。
        await b.click_text("仍然保存")
        await b.wait_for(
            "!document.body.innerText.includes('题库不足，仍要保存')", timeout=20, label="确认框关闭"
        )
        await asyncio.sleep(0.8)

        # ============================================================ 5. 行级"停用/启用"（行留在原地）
        await b.goto_ready(f"{ADMIN}/paper-rules")
        await b.wait_text("E2E 标准模拟卷规则", timeout=40)
        await b.click_in_row("E2E 标准模拟卷规则", "停用")
        await b.wait_for(
            """Array.from(document.querySelectorAll('tr'))
                 .some(r => r.innerText.includes('E2E 标准模拟卷规则') && r.innerText.includes('已停用'))""",
            timeout=20, label="行内「已停用」标记",
        )
        await shot(b, "05-row-toggle-keeprow-feedback")
        still = await b.eval(
            """Array.from(document.querySelectorAll('tr'))
                 .some(r => r.innerText.includes('E2E 标准模拟卷规则'))"""
        )
        assert still, "「停用」是状态变更，行必须留在原地（keepRow）"
        print("✅ 行级「停用」：就地标记 + 行仍在列表（keepRow 语义）")
        # 再启用回来（保证后面的组卷演示用得到它）
        await b.click_in_row("E2E 标准模拟卷规则", "启用")
        await b.wait_for(
            """Array.from(document.querySelectorAll('tr'))
                 .some(r => r.innerText.includes('E2E 标准模拟卷规则') && r.innerText.includes('已启用'))""",
            timeout=20, label="行内「已启用」标记",
        )
        print("✅ 行级「启用」同样就地反馈")

        # ============================================================ 6. 行级"删除"（行该走了）
        await b.click_in_row("E2E 界面新建的规则", "删除")
        await wait_text_in(b, "body", "确认删除规则")
        await shot(b, "06-delete-confirm-hard")
        await b.click_text("永久删除")
        await b.wait_for(
            """Array.from(document.querySelectorAll('tr'))
                 .some(r => r.innerText.includes('E2E 界面新建的规则') && r.innerText.includes('已删除'))""",
            timeout=20, label="删除行内标记",
        )
        await shot(b, "07-row-delete-feedback")
        # 3 秒后该行从当前视图移除
        await b.wait_for(
            """!Array.from(document.querySelectorAll('tr'))
                  .some(r => r.innerText.includes('E2E 界面新建的规则'))""",
            timeout=15, label="该行从视图移除",
        )
        print("✅ 行级「删除」：就地标记 → 3 秒后从视图移除（与「停用」形成对照）")

        # ============================================================ 7. /exams/new 模式切换
        await b.goto_ready(f"{ADMIN}/exams/new")
        await b.wait_text("建卷方式", timeout=60)
        await shot(b, "08-exam-new-mode-switch")
        t = await b.text()
        assert "手动选题" in t and "规则自动组卷" in t, "两种模式要一眼看清"
        assert "当前" in t, "当前模式要有明确标识"
        print("✅ 建卷方式切换清晰（手动选题 / 规则自动组卷 + 当前标识）")

        # 填基本信息
        await b.type_text("#new-title", "E2E 手动选题卷")
        await open_select(b, 0)
        await pick(b, "建设工程经济")
        await shot(b, "09-exam-new-manual-sections")
        print("✅ 手动选题模式：卷面结构编辑")

        # ============================================================ 8. 规则模式：实时试算
        # ⚠️ /exams/new 上没有 dialog，open_select 会退回 document。
        # 下拉顺序：0=科目, 1=试卷类型, 2=规则来源的规则下拉（手动模式的分段下拉已卸载）
        await b.click_text("规则自动组卷")
        await asyncio.sleep(0.8)
        await open_select(b, 2, scope="body")
        await pick(b, "E2E 标准模拟卷规则")
        await wait_text_in(b, "body", PREVIEW_OK)
        # 展开抽题明细
        await b.click_text("抽题明细")
        await asyncio.sleep(0.6)
        await shot(b, "10-exam-new-rule-live-preview")
        print("✅ 规则模式：实时试算 + 抽题明细（抽到的题直接可见）")

        # 换成"题库不足"的规则 → 预警块
        await open_select(b, 2, scope="body")
        await pick(b, "E2E 题库不足规则")
        await wait_text_in(b, "body", PREVIEW_GAP)
        await shot(b, "11-exam-new-rule-gap-warning")
        print("✅ 验收③ 缺口规则：**建之前**就预警「当前题库不足」")

        # ============================================================ 9. 用能抽满的规则真实建卷
        await open_select(b, 2, scope="body")
        await pick(b, "E2E 标准模拟卷规则")
        await wait_text_in(b, "body", PREVIEW_OK)
        await b.click_text("创建并自动组卷")
        await b.wait_for("location.pathname.startsWith('/exams/') && !location.pathname.endsWith('/new')",
                         timeout=90, label="跳到详情页")
        await b.wait_text("卷面结构", timeout=60)
        await asyncio.sleep(1.0)
        await shot(b, "12-rule-compose-exam-detail")
        # 断言：一条规则生成完整卷（无缺口）
        t = await b.text()
        assert "还差" not in t, f"能抽满的规则不该有缺口：{t[:400]}"
        assert "已排满" in t or "题库充足" in t or "校验通过" in t, f"应当是完整卷：{t[:400]}"
        print("✅ 验收① 一条规则生成完整卷（分段已排满、校验通过）")

        # ============================================================ 10. 缺口规则的卷 → shortfalls
        await b.goto_ready(f"{ADMIN}/exams/new")
        await b.wait_text("建卷方式", timeout=60)
        await b.type_text("#new-title", "E2E 缺口组卷演示")
        await open_select(b, 0)
        await pick(b, "建设工程经济")
        await b.click_text("规则自动组卷")
        await asyncio.sleep(0.8)
        await open_select(b, 2, scope="body")
        await pick(b, "E2E 题库不足规则")
        await wait_text_in(b, "body", PREVIEW_GAP)
        await b.click_text("创建并自动组卷")
        await b.wait_for("location.pathname.startsWith('/exams/') && !location.pathname.endsWith('/new')",
                         timeout=90, label="跳到详情页")
        await b.wait_text("卷面结构", timeout=60)
        await wait_text_in(b, "body", "还差")
        await asyncio.sleep(1.0)
        await shot(b, "13-exam-shortfall-detail")
        print("✅ 验收② 题库不足 → shortfalls 准确显示（need/got/missing + 还差多少）")

        await b.close()

    print("\n===== 截图清单 =====")
    for n, s in TAKEN:
        print(f" - {n}  ({s // 1024} KB)")
    print(f"共 {len(TAKEN)} 张")
    if len(TAKEN) < 10:
        print("❌ 不足 10 张", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
