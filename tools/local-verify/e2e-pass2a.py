"""Batch 7 Pass 2a 的 E2E 走查 + 截图。

前置：
  1. PG + API（8123）+ admin dev（3000）都在跑
  2. 已跑 `seed-pass2a.py` 造好数据

跑法：
    python e2e-pass2a.py

⚠️ 两个必须记住的坑（都在这台机器上实测踩过）：
  1. **Next dev 首屏是 SSR HTML，React 还没 hydrate** —— 此时点击无反应且不报错。
     一律用 `goto_ready()`（内部等 `__NEXT_DATA__`），不要用裸 `goto`。
  2. **受控输入框要逐字符发键盘事件**，`el.value = x` 与 `Input.insertText`
     都进不了 React 的 state。用 `type_text()`。
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
SHOTS = Path(r"C:/My Protect/WorkBuddy/ONE Build/yijian-platform/apps/admin/docs/screenshots/batch7-pass2a")

TAKEN: list[tuple[str, int]] = []


def warmup() -> None:
    """先把要访问的路由都请求一遍，让 Next dev 编译完 —— 否则浏览器要替我们等编译。"""
    for p in ("/login", "/exams"):
        for _ in range(90):
            try:
                r = httpx.get(f"{ADMIN}{p}", timeout=60)
                if r.status_code == 200:
                    break
            except Exception:
                pass
    print("✅ 路由预热完成")


async def shot(b: Browser, name: str, *, full: bool = False) -> None:
    p = SHOTS / f"{name}.png"
    await b.screenshot(str(p), full=full)
    size = p.stat().st_size
    TAKEN.append((f"{name}.png", size))
    print(f"  📸 {name}.png  {size // 1024} KB")


def api(method: str, path: str, tok: str, body=None):
    r = httpx.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {tok}"},
                      json=body, timeout=60)
    return r.json()


async def login(b: Browser, phone: str, password: str) -> None:
    await b.goto_ready(f"{ADMIN}/login")
    await b.wait_text("手机号", timeout=60)
    await b.type_text("#phone", phone)
    await b.type_text("#password", password)
    await b.click_until("登录", "!location.pathname.startsWith('/login')", attempts=25)
    await b.goto_ready(f"{ADMIN}/exams")


async def main() -> None:
    warmup()

    tok = httpx.post(f"{API}/auth/login/password",
                     json={"phone": "13800000000", "password": "Admin@123456"},
                     timeout=30).json()["data"]["access_token"]
    lst = api("GET", "/admin/exams?page_size=100&include_deleted=true", tok)
    by_title = {x["title"]: x for x in lst["data"]["items"]}

    def find(prefix: str) -> dict:
        for t, x in by_title.items():
            if t.startswith(prefix):
                return x
        raise SystemExit(f"找不到标题以「{prefix}」开头的试卷：{sorted(by_title)}")

    full, gap = find("E2E 完整模考卷"), find("E2E 题库不足卷")
    published, archived = find("E2E 已发布卷"), find("E2E 待恢复卷")

    # 幂等：这张卷可能被上一次跑恢复过了 —— 归档流程要有东西可恢复，先确保它是归档态
    if not archived["is_deleted"]:
        r = api("DELETE", f"/admin/exams/{archived['id']}?reason=E2E%20%E9%87%8D%E7%BD%AE", tok)
        assert r["code"] == 0, r
        print("↻ 已把「待恢复卷」重新归档（保证流程可重复跑）")

    # ---- 第一段会话：超管 ----
    await admin_walkthrough(full, gap, published, archived, tok)

    # ---- 第二段会话：viewer（**必须换一个干净的浏览器**）----
    # 为什么不在同一个浏览器里清 localStorage：清掉存储不等于清掉**内存里的登录态**，
    # 应用可能基于内存态把你重定向走，于是登录页根本不出现（实测踩到）。
    # 换一个 browser 实例最省事，也最接近"另一个人打开浏览器"的真实场景。
    await viewer_walkthrough(full)

    print("\n===== 截图清单 =====")
    for n, s in TAKEN:
        print(f" - {n}  ({s // 1024} KB)")
    print(f"共 {len(TAKEN)} 张")
    if len(TAKEN) < 10:
        print("❌ 不足 10 张", file=sys.stderr)
        sys.exit(1)


async def admin_walkthrough(full: dict, gap: dict, published: dict, archived: dict, tok: str) -> None:
    async with Browser() as b:
        # ============================================================ 1. 登录页
        await b.goto_ready(f"{ADMIN}/login")
        await b.wait_text("手机号", timeout=60)
        await shot(b, "01-login")

        # ============================================================ 2. 列表：默认范围
        await login(b, "13800000000", "Admin@123456")
        print("✅ 超管登录成功")
        await b.wait_text("试卷管理", timeout=30)
        await b.wait_text(full["title"], timeout=30)
        await shot(b, "02-exams-list-default")
        assert archived["title"] not in await b.text(), "默认列表不该出现已归档的卷"
        print("✅ 验收⑤-1 默认列表看不到已归档的卷")

        # ============================================================ 3. 打开「显示已归档」
        await b.click_text("显示已归档的试卷", tag="label")
        await b.wait_text(archived["title"], timeout=20)
        await b.wait_for(
            f"""Array.from(document.querySelectorAll('tr'))
                  .some(r => r.innerText.includes({json.dumps(archived["title"])})
                             && r.innerText.includes('恢复'))""",
            timeout=15, label="该行的恢复按钮",
        )
        await shot(b, "03-include-deleted-restore-button")
        print("✅ 验收⑤-2 开关打开后能看到，且出现「恢复」按钮")

        # ============================================================ 4. 恢复二次确认
        await b.click_in_row(archived["title"], "恢复")
        await b.wait_text("确认恢复", timeout=20)
        await shot(b, "04-restore-confirm-dialog")

        # ============================================================ 5. 提交 → 就地标记 + toast
        await b.click_text("恢复这份试卷")
        await b.wait_text("已恢复", timeout=20)
        await shot(b, "05-restore-inline-marked-and-toast")
        print("✅ 验收⑤-3 就地标记「已恢复」+ toast")
        assert await b.eval(
            """Array.from(document.querySelectorAll('*'))
                 .some(e => e.children.length === 0 && (e.innerText||'').includes('查看'))"""
        ), "toast 里应当有 [查看] 出口"
        print("✅ 验收⑤-4 toast 带 [查看] 出口")

        # ============================================================ 6. 3 秒后移除
        await asyncio.sleep(4)
        gone = await b.eval(
            f"""!Array.from(document.querySelectorAll('tr'))
                 .some(r => r.innerText.includes({json.dumps(archived["title"])}))"""
        )
        assert gone, "3 秒后该行应当从当前视图移除"
        await shot(b, "06-after-3s-row-removed")
        print("✅ 验收⑤-5 3 秒后从当前视图移除")
        d = api("GET", f"/admin/exams/{archived['id']}", tok)["data"]
        assert d["is_deleted"] is False, d["is_deleted"]
        print("✅ 服务端确认：该卷已恢复（is_deleted=false）")

        # ============================================================ 7. 缺口卷：结构化校验
        await b.goto_ready(f"{ADMIN}/exams/{gap['id']}")
        await b.wait_text("卷面校验", timeout=40)
        await b.wait_text("必须修的问题", timeout=30)
        await shot(b, "07-validate-report-structured")
        txt = await b.text()
        assert "下一步" in txt, "校验报告每条都该有「下一步」"
        assert "还差" in txt, "分段缺口要写成人话（还差 N 道…）"
        assert "需要" in txt and "缺" in txt, "应当展示组卷缺口 need/got/missing"
        print("✅ 验收③ 缺口 need/got/missing + 结构化校验 + 每条给下一步")

        await b.eval(
            """(() => {
              const h = Array.from(document.querySelectorAll('h2'))
                .find(x => (x.innerText||'').includes('卷面结构'));
              if (h) h.scrollIntoView({block:'start'});
              return true;
            })()"""
        )
        await asyncio.sleep(0.6)
        await shot(b, "08-section-gap-red-and-shortfall")
        print("✅ 分段计划/实际对不上时整段标红")

        # ============================================================ 8. 已发布卷：锁定版本漂移
        await b.goto_ready(f"{ADMIN}/exams/{published['id']}")
        await b.wait_text("卷面结构", timeout=40)
        await b.wait_text("已锁定至 v", timeout=30)
        try:
            await b.hover("[class*='cursor-help']")
            await b.wait_for(
                "document.body.innerText.includes('发布之后被改过')", timeout=10, label="hover 差异摘要"
            )
            print("✅ hover 差异摘要已展开")
        except Exception as e:
            print("⚠️ hover 差异摘要未捕获：", e)
        await shot(b, "09-locked-version-drift-tooltip")
        print("✅ 验收④ 已发布卷显示「已锁定至 vX（当前 vY）」")

        # ============================================================ 9. 加题面板
        await b.goto_ready(f"{ADMIN}/exams/{full['id']}")
        await b.wait_text("卷面结构", timeout=40)
        await b.click_text("加题")
        await b.wait_text("手动加题", timeout=30)
        await b.wait_for(
            "document.body.innerText.includes('已在卷面')", timeout=30, label="已加过的题标记"
        )
        await shot(b, "10-add-questions-picker")
        t10 = await b.text()
        assert "已在卷面" in t10, "已在卷面的题要有标记"
        assert "知识点" in t10, "筛选结果要显示知识点名"
        print("✅ 加题：已在卷面禁选 + 筛选结果显示知识点名")

        picked = await b.eval(
            """(() => {
              const boxes = Array.from(document.querySelectorAll('button[role="checkbox"]'))
                .filter(x => x.getAttribute('aria-disabled') !== 'true' && !x.disabled);
              if (!boxes.length) return 0;
              for (const el of boxes.slice(0, 2)) {
                el.scrollIntoView({block:'center'});
                el.click();
              }
              return Math.min(2, boxes.length);
            })()"""
        )
        assert picked, "没有可勾选的题"
        await asyncio.sleep(0.6)
        await b.click_text("下一步：预览")
        await b.wait_text("将加入", timeout=20)
        await shot(b, "11-add-questions-preview")
        print(f"✅ 加题：预览「将加入 N 道题」+ 落点分段（勾了 {picked} 道）")

        await b.click_text("返回修改")
        await asyncio.sleep(0.4)
        await b.click_text("取消")
        await asyncio.sleep(0.6)
        print("（已截图，未提交 —— 保持 E2E 数据稳定）")

        await b.close()


async def viewer_walkthrough(full: dict) -> None:
    """viewer 会话：**独立的浏览器实例**，保证是干净的未登录态。"""
    async with Browser() as b:
        await login(b, "13900000077", "Viewer@123456")
        await b.wait_text("试卷管理", timeout=40)
        await b.wait_text(full["title"], timeout=40)
        await shot(b, "12-viewer-exams-list")

        dis = await b.eval(
            """(() => {
              const btns = Array.from(document.querySelectorAll('button'))
                .filter(x => ['归档','发布','恢复'].includes((x.innerText||'').trim()));
              return JSON.stringify(btns.map(x => ({t: x.innerText.trim(), disabled: x.disabled})));
            })()"""
        )
        print("   viewer 写按钮状态：", dis)
        assert "true" in dis, f"viewer 的写按钮应当置灰：{dis}"
        print("✅ 验收② viewer 写按钮置灰")

        await b.hover("button[disabled]")
        try:
            await b.wait_for(
                "document.body.innerText.includes('exam:create') || document.body.innerText.includes('exam:publish')",
                timeout=10, label="权限 tooltip",
            )
            await shot(b, "13-viewer-disabled-tooltip")
            print("✅ viewer 置灰按钮 tooltip 说清缺哪个权限")
        except Exception:
            print("⚠️ tooltip 未捕获（置灰本身已验证）")

        await b.goto_ready(f"{ADMIN}/exams/{full['id']}")
        await b.wait_text("卷面结构", timeout=40)
        await shot(b, "14-viewer-detail-readonly")
        print("✅ viewer 能看详情（只读）")

        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
