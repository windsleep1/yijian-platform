#!/usr/bin/env python
"""判分对拍（**方案 §8 判据 1**）：Python 的 `grade()` vs 个人 PWA 的 `grade.mjs`。

## 它拦的是什么
PWA 的判分是**第二份实现**（C 端在 Python、离线端在 JS）。第二份实现最典型的失效方式是
**悄悄分叉** —— 两侧都"看着对"，而某些题在 PWA 里永远判错（比如"部分分"或多选题的边界），
**不报错**。⇒ 唯一能拦住它的手段是**逐条对拍**。

## 怎么跑
    python tools/pwa/parity.py            # 需要 data/seed/pwa-bank.json（先跑 export-bank.py）
    python tools/pwa/parity.py --quick    # 只抽 300 题（内循环用；全量约 1.6 万条向量）

## 两侧各自跑的是什么
| 侧 | 实现 | 输入 |
|---|---|---|
| Python | `app.services.practice_service.grade()`（**语义的唯一真相**） | 同一批向量 |
| PWA | `apps/pwa/src/lib/grade.mjs`（**哑比较**） | 同一批向量 + 包里的 `meta.grading_rules` |

## 三条断言（缺一条这个对拍就没有意义）
1. **结论与得分逐条相同**（`is_correct` + `ratio`）；
2. **用户答案的归一逐条相同**（该收的收、该拒的拒 —— 两侧的"拒绝"要落在同一批输入上）；
3. ★★ **反向对照必须"红"**：喂一条**旧写法**的判断题（正确答案 `["A"]`）——
   Python 判**对**、PWA 判**错**。两者**必须不同**。
   ★ 这条是"导出时归一"**在承重**的唯一证据：没有它，把归一那道闸拆掉，
     上面①②照样全绿（向量本来就是照着"已归一"造的）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BANK = REPO / "data" / "seed" / "pwa-bank.json"
NODE_HELPER = REPO / "tools" / "pwa" / "_parity.mjs"


def find_node() -> str:
    """找 node。**找不到就大声跳过**（不是"跑过了"）—— 与 preflight 同一条纪律。"""
    exe = shutil.which("node")
    if exe:
        return exe
    managed = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    for cand in sorted(managed.glob("*/node.exe"), reverse=True):
        return str(cand)
    raise SystemExit(
        "✗ 找不到 node。判分对拍需要它来跑 PWA 侧的 grade.mjs。\n"
        "  ⇒ 这不是「对拍通过」，是「没跑成」。请装 node 或设 PATH。"
    )


def load_backend() -> tuple[Any, Any, Any, float]:
    """取后端那几样：`grade` / `normalize_user_value` / `BizError` / `PARTIAL_CREDIT_RATIO`。"""
    sys.path.insert(0, str(REPO / "apps" / "api"))
    try:
        from app.core.errors import BizError
        from app.services.practice_service import (
            PARTIAL_CREDIT_RATIO,
            grade,
            normalize_user_value,
        )
    except ModuleNotFoundError as e:  # 最常见：用错了 python（venv 才有 fastapi）
        raise SystemExit(
            f"✗ import 后端失败：{e}\n"
            "  ⇒ 用 **venv 的 python** 跑本脚本（后端依赖 fastapi / sqlalchemy）。"
        ) from None
    return grade, normalize_user_value, BizError, float(PARTIAL_CREDIT_RATIO)


def wrong_answer(qtype: str, correct: list[Any]) -> list[Any]:
    """造一个**一定错**的答案。"""
    if qtype == "judge":
        first = correct[0] if correct else True
        return [not bool(first)]
    if qtype == "single":
        # 换一个标号（A→B）；库里题库的单选题至少两个选项
        return ["B" if str(correct[0]).upper() != "B" else "A"]
    # 多选题：空集 —— 一定不是"全对"，也不满足"非空真子集"⇒ 不给部分分。
    # 想造"半对"请用 half_answer()（它要求题面允许部分分）。
    return []


def half_answer(qtype: str, correct: list[Any]) -> list[Any] | None:
    """造一个**真子集**（只在多选题、且正确答案 ≥2 个时有意义）。"""
    if qtype != "multiple" or len(correct) < 2:
        return None
    return correct[: len(correct) - 1]


def build_vectors(bank: dict[str, Any], quick: bool) -> tuple[list, list]:
    gradable = set(bank["meta"]["grading_rules"]["gradable_types"])
    pool = [q for q in bank["questions"] if q["type"] in gradable]
    if quick:
        step = max(1, len(pool) // 300)
        pool = pool[::step]

    vectors: list[list[Any]] = []
    for q in pool:
        qtype = q["type"]
        correct = list(q["answer"]["value"])
        partial = bool(q["answer"].get("partial_credit"))
        # ① 全对
        vectors.append([qtype, correct, correct, partial])
        # ② 全错
        vectors.append([qtype, correct, wrong_answer(qtype, correct), partial])
        # ③ 半对（只有多选题造得出来；其余跳过 —— 宁可少一条，也不造假的"半对"）
        half = half_answer(qtype, correct)
        if half is not None:
            vectors.append([qtype, correct, half, partial])
    return vectors, pool


#: ★ **两侧必须一致**的归一输入。混进了"该拒"的 6 条 ——
#:   只验"该收的收"的话，一个"原样放行一切"的实现也能全绿。
NORMALIZE_AGREE: list[list[Any]] = [
    ["judge", [True]],
    ["judge", [False]],
    ["judge", [True, False]],
    ["judge", []],
    ["single", ["B"]],
    ["single", [" b "]],
    ["single", ["A", "B"]],
    ["single", [1]],
    ["multiple", ["C", "A"]],
    ["multiple", ["a", "c"]],
    ["multiple", ["A", "A"]],
    ["multiple", [1, 2]],
]

#: ★★ **有意不一致**的输入 —— 不是"漏了"，是**设计决定的**，所以这里**断言它们必须不同**。
#:
#: 后端 `normalize_user_value` **宽容**判断题的标号写法（`"A"` / `"对"`），
#: 理由是它的抬头写的那句：「库里有种子那一套写法，**测试与他人脚本很可能照着库里的样子发**」。
#:
#: 个人 PWA **不宽容**，而且这是有意的：
#:   · 它读的是 `pwa-bank.json` —— 那份包的答案**在导出时已经归一**（`export-bank.py`
#:     用 `answer.check_doc()` 把关，没归干净就不导出）⇒ **不存在旧写法的来源**；
#:   · 用户答案的唯一生产者是**界面**，而界面发的是布尔（`[true]` / `[false]`）；
#:   · 要宽容就得在 JS 里放一张 `JUDGE_TRUE_TOKENS` 表 —— 那是**第二份语义**，
#:     正是方案 §3.1 明令禁止的东西（"PWA 的判分函数不许出现字面量规则"）。
#:
#: ⇒ 结论：**宁可在这里少宽容一点，也不要在 JS 里多一份 token 表**。
#: ★ 这条断言的作用是**防回潮**：哪天有人"顺手"给 JS 补上宽容，这里立刻红 ——
#:   而红的时候他会读到上面这段理由。
NORMALIZE_DECLARED_DIFF: list[list[Any]] = [
    ["judge", ["A"]],
    ["judge", ["对"]],
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="判分对拍：Python vs PWA 的 grade.mjs")
    ap.add_argument("--quick", action="store_true", help="只抽 300 题（内循环用）")
    ap.add_argument("--keep", action="store_true", help="保留中间的向量/结果文件（排障用）")
    args = ap.parse_args(argv)

    if not BANK.exists():
        raise SystemExit(
            f"✗ 缺 {BANK}\n  ⇒ 先跑：python tools/pwa/export-bank.py（且要用 venv 的 python）"
        )
    bank = json.loads(BANK.read_text(encoding="utf-8"))
    grade, normalize_user_value, biz_error, ratio_const = load_backend()

    rules = bank["meta"]["grading_rules"]
    # ★ 包里的规则必须与后端常量**同值** —— 否则对拍会用两套规则"各自自洽地全绿"，
    #   而那正是对拍最该拦住的东西（口径漂了却看不出来）。
    if abs(float(rules["partial_credit_ratio"]) - ratio_const) > 1e-12:
        raise SystemExit(
            f"✗ 包里的 partial_credit_ratio={rules['partial_credit_ratio']} "
            f"≠ 后端常量 {ratio_const} ⇒ 包是旧的，重新导出。"
        )

    vectors, pool = build_vectors(bank, args.quick)
    print(f"[1/4] 造向量：{len(pool)} 题 → {len(vectors)} 条（全对 / 全错 / 半对）")

    # ---- Python 侧 ----
    py_results: list[list[Any]] = []
    for v in vectors:
        qtype, correct, user, partial = v
        ok, ratio = grade(qtype, correct, user, partial)
        py_results.append([ok, ratio])

    norm_cases = NORMALIZE_AGREE + NORMALIZE_DECLARED_DIFF
    py_norm: list[list[Any]] = []
    for qtype, raw in norm_cases:
        try:
            py_norm.append(["ok", normalize_user_value(qtype, raw)])
        except biz_error as e:
            py_norm.append(["err", e.code])
        except Exception as e:  # noqa: BLE001 —— 其它异常也算"拒了"，但要能被看见
            py_norm.append(["err", f"{type(e).__name__}"])
    print(f"[2/4] Python 侧跑完（{len(py_results)} 条结论 + {len(py_norm)} 条归一）")

    # ---- 送进 node ----
    with tempfile.TemporaryDirectory() as td:
        vec_path = Path(td) / "vectors.json"
        out_path = Path(td) / "out.json"
        vec_path.write_text(
            json.dumps(
                {"rules": rules, "vectors": vectors, "normalizeCases": norm_cases},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        node = find_node()
        proc = subprocess.run(
            [node, str(NODE_HELPER), str(vec_path), str(out_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            return 2
        js = json.loads(out_path.read_text(encoding="utf-8"))
        if args.keep:
            (Path(tempfile.gettempdir()) / "pwa-parity-vectors.json").write_text(
                vec_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
    print(f"[3/4] node 侧跑完（{node}）")

    # ---- 比对 ----
    diffs = [
        (i, vectors[i], py_results[i], js["results"][i])
        for i in range(len(py_results))
        if py_results[i] != js["results"][i]
    ]
    n_agree = len(NORMALIZE_AGREE)
    norm_diffs = [
        (norm_cases[i], py_norm[i], js["normalize"][i])
        for i in range(n_agree)
        if py_norm[i] != js["normalize"][i]
    ]
    # ★ 声明过的那几条**必须不同**（见 NORMALIZE_DECLARED_DIFF 的理由）
    declared_same = [
        (norm_cases[i], py_norm[i], js["normalize"][i])
        for i in range(n_agree, len(norm_cases))
        if py_norm[i] == js["normalize"][i]
    ]

    print("[4/4] 比对")
    ok = True
    if diffs:
        ok = False
        print(f"  ✗ 判分结论有 {len(diffs)} 条不一致（前 5 条）：")
        for i, v, p, j in diffs[:5]:
            print(f"      #{i} {v[0]} correct={v[1]} user={v[2]} partial={v[3]}")
            print(f"         Python={p}  PWA={j}")
    else:
        print(f"  ✓ 判分结论与得分**逐条相同**（{len(py_results)} 条）")

    if norm_diffs:
        ok = False
        print(f"  ✗ 归一有 {len(norm_diffs)} 条不一致：")
        for case, p, j in norm_diffs:
            print(f"      {case}  Python={p}  PWA={j}")
    else:
        print(f"  ✓ 用户答案的归一并列相同（{n_agree} 条，其中 6 条是「该拒」的）")

    if declared_same:
        ok = False
        print(f"  ✗ 有 {len(declared_same)} 条**声明过要不同**的归一却相同了 —— 说明有人给 PWA 补了")
        print("     token 宽容（§3.1 禁止的第二份语义），或者把声明的前提改掉了：")
        for case, p, j in declared_same:
            print(f"      {case}  Python={p}  PWA={j}")
    else:
        print(
            f"  ✓ 声明的 {len(NORMALIZE_DECLARED_DIFF)} 条差异仍在"
            "（判断题标号写法：后端宽容 / PWA 只认布尔 —— 理由见脚本里的常量注释）"
        )

    # ---- ★★ 反向对照：这条"应该红"的用例是这个脚本的价值所在 ----
    qtype, correct, user = "judge", ["A"], [True]
    py_ok, py_ratio = grade(qtype, correct, user, False)
    js_ok, js_ratio = js["legacy"]
    print("")
    print("  反向对照（旧写法 [`A`] vs 用户 `[true]`）：")
    print(f"    Python（有 token 映射）= {py_ok} / {py_ratio}")
    print(f"    PWA  （哑比较）        = {js_ok} / {js_ratio}")
    if (py_ok, py_ratio) == (js_ok, js_ratio):
        ok = False
        print("    ✗ 两侧**相同**了 —— 这说明 PWA 侧多了一张 token 表（§3.1 禁止），")
        print("      或者两边的「归一前提」被改掉了。对拍失去意义，必须查清。")
    else:
        print("    ✓ 两侧**不同**（Python 判对、PWA 判错）—— 归一这一步确实在承重。")

    print("")
    print("✅ 判分对拍通过" if ok else "❌ 判分对拍**没通过**")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
