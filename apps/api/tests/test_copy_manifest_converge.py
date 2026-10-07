"""复制品清单的 `converge` 字段：**只许「永久」或 `BL-<n>`**。

## 它拦的是什么（用户 2026-10-07 定）
`apps/pwa/COPIED-FROM-WEB.md` 的每一条 fork / only 理由都带一个 `converge` 字段
（"这个分叉会不会收敛"）。现在**全部**写「永久」—— 但**没有任何东西拦**"当 X 时可以消除"
那种写法。而那种写法按硬约定 P 就是一条**待办**：必须有编号（`docs/21` 的 BL-XX）
+ 触发条件 + 检查点。⇒ 不拦它，它就退化成**伪装成理由的逃避**。

## 为什么是"值域检查"而不是"分析代码"
值域检查是机械的、**不会误报**；"读代码判断这个 fork 将来会不会收敛"会误报 ——
而**会误报的检查只能做诊断、不能做门禁**（项目口径）。

★ 判据在生成脚本里（`gen-copied-manifest.py::check_converge_values`，生成时即失败）；
  本测试负责**证明它能被证伪**（硬约定 J）：喂一个坏的 `converge`，它必须红。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
GEN = REPO / "tools" / "pwa" / "gen-copied-manifest.py"


def _load() -> ModuleType:
    """按**路径**加载（文件名带连字符，`import` 语句用不了）。"""
    spec = importlib.util.spec_from_file_location("gen_copied_manifest", GEN)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_converge_values_in_the_real_tables_are_allowed() -> None:
    """仓库里**真实**的 FORK / ONLY 表必须全过（否则随便一次生成就红了）。"""
    _load().check_converge_values()


def test_the_check_itself_can_go_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """★★ 硬约定 J：这道守卫必须**能被证伪** —— 四格都要验，否则它不成立。

    ★ 只验 ① 的话，"凡 `converge` 就报红"的实现也能过（那会把「永久」弄红，
      逼所有人只写「永久」= 把待办藏起来）。
    ★ 只验 FORK 的话，**ONLY 那一半从来没被验过**（同族：二元对立的判据必须成对验）。
    """
    mod = _load()

    # ① 条件式收敛（= 一条没有编号的待办）⇒ 必须红，且报错里要给出路（BL 编号）
    monkeypatch.setattr(mod, "FORK", {"src/x.ts": ("理由", "判据", "当 C 端抽了共享包时")})
    monkeypatch.setattr(mod, "ONLY", {})
    with pytest.raises(SystemExit) as ei:
        mod.check_converge_values()
    assert "BL-" in str(ei.value), f"报错里没告诉人怎么写才合法：{ei.value}"

    # ② 登记了编号 ⇒ 绿（否则这道门禁会逼所有人只写「永久」）
    monkeypatch.setattr(mod, "FORK", {"src/x.ts": ("理由", "判据", "BL-42")})
    mod.check_converge_values()

    # ③ **ONLY 那一半也要查** —— 只查 FORK 的话这里会漏
    monkeypatch.setattr(mod, "FORK", {})
    monkeypatch.setattr(mod, "ONLY", {"src/y.ts": ("理由", "以后再说")})
    with pytest.raises(SystemExit):
        mod.check_converge_values()

    # ④ 对照：ONLY 写「永久」必须绿（证明 ③ 红是因为**值不对**，不是因为"凡 ONLY 就红"）
    monkeypatch.setattr(mod, "ONLY", {"src/y.ts": ("理由", "永久")})
    mod.check_converge_values()
