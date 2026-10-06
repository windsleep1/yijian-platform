#!/usr/bin/env python
"""PWA ↔ C 端**端点/参数对账**（**诊断，不是门禁**）。

## 它回答什么

`apps/pwa` 的页面是**逐字节复制** `apps/web` 的，数据层重写成 IndexedDB。
于是每一次复制都要回答同一个问题：

    C 端页面的每个 `request(路径, 选项)`，**PWA 的 ROUTES + service 签名能不能接住？**

分两层，因为它们是**两种不同的坏法**：

| 层 | 坏法 | 症状 |
|---|---|---|
| **路径** | ROUTES 里没有这条 | 页面一进来就 `50001 本地数据层没有实现这个接口` |
| **参数** | 路径对上了，但 service **不读**某个 query / body 字段 | **不报错** —— 分页与筛选**静默失效** |

★ 第二层是真实发生过的（B-2 步骤 1）：`listCollections(kind)` 只收 `kind`、
把 `subject_id` / `page` / `page_size` **整个丢掉** ⇒ 收藏页永远返回第一页全量。
**路径级对账抓不到它。**

## ★★ 为什么它**不进 `check-invariants`**

用户口径（2026-10-06 提炼③）：**会误报的检查只能做诊断**。
本脚本是**基于正则的近似**，已知三类误报形态（都在下面 `KNOWN_*` 里显式登记）：

1. **模板串里的三元** —— `/practice/${k === "a" ? "x" : "y"}/${id}` 只能给出**候选集**，
   取不出分支（连比较对象 `"a"` 都会被当候选捞进来）；
2. **shorthand 属性** —— `{ kind }` 与 `{ kind: k }` 是同一件事，正则要专门照顾；
3. **变量拼的路径** —— `\`/practice/${path}/${qid}\`` 里 `path` 的取值在另一处赋值。

⇒ 判据：**报出来的先当线索，逐条人工判读**；`KNOWN_HAND_CHECK` 里登记过的必须
   在报告里给出结论。**它连续几批零真阳性之后，才谈"升格成门禁"。**

## 用法

    python tools/pwa/check-endpoints.py            # 全部（路径级 + 参数级）
    python tools/pwa/check-endpoints.py --paths    # 只看路径级
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: 要核对的 C 端页面 —— **这条清单是手写的**（它必须和 `apps/pwa` 实际复制了哪些页面对应）。
#: ⚠️ 复制新页面时**要来这里加一行**（否则新页面永远不被对账）。
C_PAGES = [
    "apps/web/src/app/practice/wrong/page.tsx",
    "apps/web/src/app/practice/wrong/[qid]/page.tsx",
    "apps/web/src/app/(tabs)/me/favorites/page.tsx",
    "apps/web/src/app/(tabs)/me/notes/page.tsx",
    "apps/web/src/app/practice/session/[id]/page.tsx",
    "apps/web/src/app/practice/session/[id]/report/page.tsx",
]

#: ROUTES 模式 → 实现函数（用于打印"谁来接"）。
ROUTE_TO_FN = {
    "subjects": "listSubjects",
    "subjects/:sid/chapters": "listChapters",
    "practice/sessions": "createSession",
    "practice/sessions/:sid": "getSession",
    "practice/sessions/:sid/answer": "submitAnswer",
    "practice/sessions/:sid/finish": "finishSession",
    "practice/sessions/:sid/report": "sessionReport",
    "practice/wrong-questions": "listWrong",
    "practice/wrong-questions/:qid": "wrongDetail",
    "practice/marks/:qid": "toggleFlag",
    "practice/favorites/:qid": "toggleFlag",
    "practice/favorites": "listCollections",
    "practice/questions/:qid/notes": "listNotesOfQuestion|addNote",
    "practice/notes/:nid": "editNote|removeNote",
    "practice/notes": "listNotes",
}

#: ★ **已知的、需人工判读**的调用（正则解不动，但人一秒钟能判）。
#:   判据 = 「展开后每条都在 ROUTES 里」；**不在报告里给结论就等于没核**。
KNOWN_HAND_CHECK = {
    ("apps/web/src/app/(tabs)/me/favorites/page.tsx", "DELETE", "/practice/:p/:p"): (
        "模板串 `\\`/practice/${kind === \"favorite\" ? \"favorites\" : \"marks\"}/${qid}\\`` "
        "⇒ 展开为 `/practice/favorites/:qid` 与 `/practice/marks/:qid`，**两条都在 ROUTES 里**。"
        "（候选集里的 `favorite` 是 `kind === \"favorite\"` 的**比较对象**，不是路径取值 —— 干扰项。）"
    ),
    ("apps/web/src/app/practice/session/[id]/page.tsx", "PUT", "/practice/:p/:p"): (
        "答题页的标记/收藏按钮：`\\`/practice/${path}/${current.question_id}\\`` + "
        "`method: on ? \"PUT\" : \"DELETE\"`。`path` 是 `\"marks\" | \"favorites\"`（同文件里 "
        "`const on = path === \"marks\" ? ...` 给它定了型）⇒ 展开为 `/practice/marks/:qid` 与 "
        "`/practice/favorites/:qid`，**两条都在 ROUTES 里**。"
        "★ 另有**经验证据**：B-1 已复制该页面，E2E `p2c4` 真机点过这两个按钮并验了状态持久化。"
    ),
}

#: 函数 → 它**实际读**的 query key / body 字段（从函数体里抽，不靠注释）。
IMPL_FNS = [
    "createSession", "getSession", "submitAnswer", "finishSession", "sessionReport",
    "listWrong", "wrongDetail", "listCollections", "listNotes", "listNotesOfQuestion",
    "toggleFlag", "addNote", "editNote", "removeNote",
]


# ----------------------------------------------------------------- 抽取工具
def _balanced(text: str, i: int) -> int:
    """从 `text[i]`（应为 `(`）出发，返回配平后的下标；`-1` = 没配平。"""
    depth, j, quote, esc = 0, i, None, False
    while j < len(text):
        c = text[j]
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def calls_of(src: str) -> list[str]:
    """抓出 `request(...)` / `request<T>(...)` 的整段（括号配平）。"""
    out: list[str] = []
    for m in re.finditer(r"\brequest\b\s*[(<]", src):
        i = m.end() - 1
        if src[i] == "<":
            d, j = 0, i
            while j < len(src):
                if src[j] == "<":
                    d += 1
                elif src[j] == ">":
                    d -= 1
                    if d == 0:
                        break
                j += 1
            i = j + 1
            while i < len(src) and src[i].isspace():
                i += 1
        if i >= len(src) or src[i] != "(":
            continue
        j = _balanced(src, i)
        if j > 0:
            out.append(src[m.start(): j + 1])
    return out


def first_arg(call: str) -> str:
    """第一个实参的**字符串内容** —— 按包裹它的那种引号取到闭合。

    ★ 不能用固定的双引号正则：模板串里会含 `"`（`${k === "a" ? ...}`），会被截断。
    """
    m = re.search(r"^request\s*(?:<[^>]*>)?\s*\(\s*", call, re.S)
    if not m:
        return "?"
    i = m.end()
    q = call[i] if i < len(call) else ""
    if q not in "\"'`":
        return "?"
    j = i + 1
    while j < len(call):
        if call[j] == "\\":
            j += 2
            continue
        if call[j] == q:
            break
        j += 1
    return call[i + 1: j]


def obj_branch(seg: str) -> str:
    """取 `{` 起配平的那一整段。"""
    if "{" not in seg:
        return ""
    i = seg.index("{")
    d, j, quote, esc = 0, i, None, False
    while j < len(seg):
        c = seg[j]
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "{":
            d += 1
        elif c == "}":
            d -= 1
            if d == 0:
                return seg[i: j + 1]
        j += 1
    return seg[i:]


def obj_fields(seg: str) -> set[str]:
    """`{ a, b: 1, c }` → `{a, b, c}`（**含 shorthand** —— 少抓一种就会误报）。"""
    if not seg:
        return set()
    inner = seg.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    keys = set(re.findall(r"(?:^|[{,\n])\s*([A-Za-z_$][\w$]*)\s*:", inner))
    rest = re.sub(r"[A-Za-z_$][\w$]*\s*:\s*[^,{}]*", "", inner)
    keys |= set(re.findall(r"(?:^|[{,\n])\s*([A-Za-z_$][\w$]*)\s*(?=[,}\n]|$)", rest))
    return {k for k in keys if k not in {"true", "false", "null", "undefined"}}


def analyze(call: str) -> dict:
    raw = first_arg(call)
    cands: list[str] = []
    for m in re.finditer(r"\$\{([^}]*)\}", raw):
        cands += re.findall(r"\"([^\"]+)\"", m.group(1))
    path = re.sub(r"\$\{[^}]*\}", ":p", raw)
    mm = re.search(r"\bmethod\s*:\s*([^,\n]+)", call)
    methods = ["GET"]
    if mm:
        lit = re.findall(r"\"(\w+)\"", mm.group(1))
        methods = lit if lit else ["<expr>"]
    qk: set[str] = set()
    bk: set[str] = set()
    for key, into in (("query", qk), ("body", bk)):
        m2 = re.search(rf"\b{key}\s*:\s*\{{", call)
        if m2:
            into |= obj_fields(obj_branch(call[m2.start():]))
    return {"raw": raw, "path": path, "cands": sorted(set(cands)),
            "methods": methods, "query": qk, "body": bk}


def segs(s: str) -> list[str]:
    return [x for x in s.split("/") if x]


def main() -> int:
    ap = argparse.ArgumentParser(description="PWA ↔ C 端端点/参数对账（**诊断**）")
    ap.add_argument("--paths", action="store_true", help="只做路径级")
    args = ap.parse_args()

    api = (REPO / "apps/pwa/src/lib/api.ts").read_text(encoding="utf-8")
    patterns = re.findall(r'route\("([^"]+)"', api)

    def match_route(path: str) -> list[str]:
        cs = segs(path)
        return [
            r for r in patterns
            if len(segs(r)) == len(cs)
            and all(x.startswith(":") or x == y for x, y in zip(segs(r), cs))
        ]

    impl: dict[str, dict] = {}
    if not args.paths:
        for fn in IMPL_FNS:
            m = re.search(rf"^(?:export )?async function {fn}\((.*?)\)\s*:", api, re.S | re.M)
            if not m:
                continue
            i = api.index("{", m.end())
            d, j = 0, i
            while j < len(api):
                if api[j] == "{":
                    d += 1
                elif api[j] == "}":
                    d -= 1
                    if d == 0:
                        break
                j += 1
            body = api[i:j]
            impl[fn] = {
                "sig": " ".join(m.group(1).split()),
                "query": set(re.findall(r'query\.get\("([^"]+)"\)', body)),
                "body": set(re.findall(r"body\.([a-z_]+)", body)),
            }

    print("=" * 96)
    print("路径级 + 参数级对账（**诊断** —— 报出来的先当线索，逐条人工判读）")
    print("=" * 96)

    total = 0
    problems: list[tuple[str, str, list[str]]] = []
    hand: list[tuple[str, str, str]] = []
    for rel in C_PAGES:
        p = REPO / rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        rows = [analyze(c) for c in calls_of(src)]
        if not rows:
            continue
        print(f"\n######## {rel} ########")
        for a in rows:
            total += 1
            hit = match_route(a["path"])
            mkey = (rel, a["methods"][0], a["path"])
            print(f"\n  {'/'.join(a['methods']):10} {a['path']}"
                  + (f"   [候选 {a['cands']}]" if a["cands"] else ""))
            print(f"      → ROUTES: {hit if hit else '❌ 无'}")
            if a["query"]:
                print(f"      query 传出 = {sorted(a['query'])}")
            if a["body"]:
                print(f"      body  传出 = {sorted(a['body'])}")
            if not hit:
                if mkey in KNOWN_HAND_CHECK:
                    hand.append((rel, a["path"], KNOWN_HAND_CHECK[mkey]))
                    print("      ⚠️  需**人工判读**（已登记）：")
                    print(f"          {KNOWN_HAND_CHECK[mkey]}")
                else:
                    problems.append((rel, a["path"], ["路径无 ROUTES"]))
                    print("      ✗ 路径无 ROUTES")
                continue
            if args.paths:
                continue
            fn = ROUTE_TO_FN.get(hit[0], "")
            for name in fn.split("|"):
                d = impl.get(name)
                if d and a["query"] and not a["query"] <= d["query"]:
                    miss = sorted(a["query"] - d["query"])
                    problems.append((rel, a["path"], [f"{name} 不读 query {miss}"]))
                    print(f"      ✗ {name} **不读** query {miss}")

    print()
    print("=" * 96)
    print(f"共核对 **{total}** 个调用 ｜ 真问题 **{len(problems)}** ｜ 需人工判读 **{len(hand)}**")
    for rel, path, ps in problems:
        print(f"  ✗ {rel}: {path} → {ps}")
    for rel, path, why in hand:
        print(f"  ⚠️（已判读）{rel}: {path}\n      {why}")
    print()
    if problems:
        print("⇒ 有真问题：**复制页面前先修数据层**（复制完再改要同时改两份）。")
        return 1
    print("⇒ 全部接得住（含已登记的人工判读项）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
