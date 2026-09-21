# -*- coding: utf-8 -*-
"""覆盖率缺口诊断 —— 把未覆盖行**按函数分组**列出来，带源码上下文，供人工分类。

为什么要它：`coverage report -m` 只给一串行号，看不出"这条缺失是什么性质"。
补测批要做的第一件事是把缺口分成三类：
    A. 该测的   —— 错误分支 / 边界处理 / 权限拒绝 / 幂等路径
    B. 该删的   —— 防御性代码（永不可能触发）/ 死代码
    C. 该忽略的 —— 合理 `pragma: no cover` 场景（如 TYPE_CHECKING）
分类必须看着源码做，所以这里把每个缺失区域的**源码原文**打印出来。

用法：
    python tools/local-verify/cov-gaps.py                      # 全部文件，总览 + services 明细
    python tools/local-verify/cov-gaps.py --layer services     # 只看某一层
    python tools/local-verify/cov-gaps.py --data-file .coverage
    python tools/local-verify/cov-gaps.py --out gaps.txt       # 明细写文件（推荐，量大）

设计上的两个讲究：
  · **按"连续缺失区域"而不是按行**展示 —— 一段 30 行的缺失通常是同一个函数的整块，
    逐行列出会淹掉真正的结构信息。
  · **标出所在函数名** —— 缺失属于哪个函数，直接决定了它该测还是该删。
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPS_API = os.path.join(REPO, "apps", "api")

LAYERS = ("api", "core", "db", "schemas", "services", "(top)")


def layer_of(rel: str) -> str:
    """把 app/xxx/yyy.py 归到某一层。"""
    parts = rel.split("/")
    if len(parts) == 2:  # app/cli.py, app/main.py
        return "(top)"
    return parts[1] if parts[1] in LAYERS else parts[1]


def func_map(src: str) -> list[tuple[int, int, str]]:
    """返回 [(起始行, 结束行, 限定函数名)]，用于判断缺失行落在哪个函数里。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                start = child.lineno
                end = max(
                    [n.lineno for n in ast.walk(child) if hasattr(n, "lineno")]
                    or [start]
                )
                out.append((start, end, name))
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def enclosing(funcs: list[tuple[int, int, str]], ln: int) -> str:
    best = None
    for start, end, name in funcs:
        if start <= ln <= end and (best is None or start > best[0]):
            best = (start, name)
    return best[1] if best else "<module>"


def regions(missing: list[int]) -> list[tuple[int, int]]:
    """把稀疏的缺失行号合并成连续区域（允许 1 行空隙，让 if/else 块不被打散）。"""
    out: list[list[int]] = []
    for ln in sorted(missing):
        if out and ln - out[-1][1] <= 2:
            out[-1][1] = ln
        else:
            out.append([ln, ln])
    return [(a, b) for a, b in out]


def tag(lines: list[str]) -> str:
    """按源码特征给一个粗标签，帮助分类（不是判定，最终仍由人看）。"""
    body = "\n".join(lines)
    if re.search(r"\braise\b", body):
        if re.search(r"except\b", body):
            return "错误分支(raise in except)"
        return "错误分支/校验(raise)"
    if re.search(r"\bexcept\b", body):
        return "异常处理"
    if re.search(r"\b(return None|continue|pass)\b", body):
        return "防御/早退"
    if re.search(r"\bif\b", body):
        return "边界分支(if)"
    if re.search(r"\bawait\b", body):
        return "异步调用未走到"
    return "普通路径"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-file", default=os.path.join(REPO, ".coverage"))
    ap.add_argument("--layer", default=None, help="只看某一层（services/api/core/db/schemas/(top)）")
    ap.add_argument("--out", default=None, help="明细写到此文件（否则 stdout）")
    ap.add_argument("--max-lines", type=int, default=8, help="每个区域最多打印几行源码")
    args = ap.parse_args()

    import coverage

    if not os.path.exists(args.data_file):
        print(f"数据文件不存在：{args.data_file}", file=sys.stderr)
        return 2
    cov = coverage.Coverage(data_file=args.data_file, config_file=os.path.join(REPO, ".coveragerc"))
    cov.load()
    data = cov.get_data()

    per_file: list[dict] = []
    for f in sorted(data.measured_files()):
        rel = os.path.relpath(f.replace("\\", "/"), APPS_API).replace("\\", "/")
        if not rel.startswith("app/"):
            continue
        _fn, stat, _excl, missing, _fmt = cov.analysis2(f)
        per_file.append({"abs": f, "rel": rel, "stat": len(stat), "missing": sorted(missing),
                         "layer": layer_of(rel)})

    # ---- 总览（按层）----
    by_layer: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for e in per_file:
        by_layer[e["layer"]][0] += e["stat"]
        by_layer[e["layer"]][1] += len(e["missing"])
    tot_s = sum(v[0] for v in by_layer.values())
    tot_m = sum(v[1] for v in by_layer.values())

    buf = io.StringIO()

    def out(s: str = "") -> None:
        buf.write(s + "\n")

    out("=" * 78)
    out("覆盖率缺口诊断")
    out(f"  数据文件: {args.data_file}")
    out(f"  文件数: {len(per_file)}   语句: {tot_s}   未覆盖: {tot_m}   "
        f"覆盖率: {100 * (tot_s - tot_m) / tot_s:.2f}%")
    out("=" * 78)
    out()
    out(f"{'层':<10} {'文件':>4} {'语句':>6} {'未覆盖':>7} {'覆盖率':>8}   {'缺失占比':>8}")
    out("-" * 78)
    for layer in sorted(by_layer, key=lambda k: -by_layer[k][1]):
        s, m = by_layer[layer]
        nfiles = sum(1 for e in per_file if e["layer"] == layer)
        share = f"{100.0 * m / tot_m:.1f}%" if tot_m else "-"
        out(f"{layer:<10} {nfiles:>4} {s:>6} {m:>7} {100 * (s - m) / s:>7.1f}%   {share:>8}")
    out("-" * 78)
    out(f"{'TOTAL':<10} {len(per_file):>4} {tot_s:>6} {tot_m:>7} "
        f"{100 * (tot_s - tot_m) / tot_s:>7.1f}%")
    out()

    # ---- 明细 ----
    show = [e for e in per_file if args.layer is None or e["layer"] == args.layer]
    show.sort(key=lambda e: -len(e["missing"]))

    for e in show:
        if not e["missing"]:
            continue
        src = io.open(e["abs"], encoding="utf-8").read().split("\n")
        funcs = func_map("\n".join(src))
        miss_pct = 100.0 * len(e["missing"]) / e["stat"] if e["stat"] else 0
        out("#" * 78)
        out(f"# {e['rel']}   {e['stat']} 语句 / {len(e['missing'])} 未覆盖 "
            f"({miss_pct:.0f}% 缺失)")
        out("#" * 78)
        for a, b in regions(e["missing"]):
            seg = [ln for ln in e["missing"] if a <= ln <= b]
            fn = enclosing(funcs, a)
            lines = [src[i - 1].rstrip() for i in range(a, min(b, a + args.max_lines - 1) + 1)]
            out(f"\n  [{a:>4}-{b:<4}] {len(seg):>3} 条 | {fn}")
            out(f"             标签: {tag(lines)}")
            for i in range(a, min(b, a + args.max_lines - 1) + 1):
                out(f"      {i:>4}: {src[i - 1].rstrip()}")
            if b - a + 1 > args.max_lines:
                out(f"      ...  （区域还有 {b - a + 1 - args.max_lines} 行）")
        out()

    text = buf.getvalue()
    if args.out:
        io.open(args.out, "w", encoding="utf-8").write(text)
        # 摘要仍打到 stdout（明细在文件里）
        print(text.split("########")[0])
        print(f"明细已写入: {args.out}  ({len(text.encode('utf-8'))} bytes)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
