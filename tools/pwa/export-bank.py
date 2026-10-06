#!/usr/bin/env python
"""把题库导出成**个人 PWA 的单一导入包**（`data/seed/pwa-bank.json`）。

## 为什么需要这一层（而不是让 PWA 直接读 questions.json）

`questions.json` 只有题目。PWA 还得知道**科目名 / 章节名 / 知识点名**（否则练习页只有一列
id、"按知识点"的报告只有数字）。而且两件事必须在**导出时**做掉：

1. **答案归一到唯一写法** —— 库里判断题有 `{"value": [true]}` 与 `{"value": ["A"]}` 两套
   （见 `apps/api/app/schemas/answer.py` 的抬头，那是 BL-20 的现场）。
   若把两套原样发到 PWA，PWA 侧的判分就得**自带一张 token 表** —— 那就是第二份语义。
2. **把评分规则随包下发** —— 部分分比例（`PARTIAL_CREDIT_RATIO`）与可判分题型
   （`GRADABLE_TYPES`）写进 `meta.grading_rules`，PWA 只**读**、不**定义**。

⇒ 之后 PWA 的判分就是**哑比较**（集合相等），它没有可以"决定"的东西。
   判据：`apps/pwa/src/lib/grade.mjs` 里不许出现字面量规则；本脚本用
   `answer.check_doc()` 把"没归干净"的包**挡在门外**（对拍的前提由它承重）。

## 数据来源（三处，都是仓库里的真相来源）
| 内容 | 来源 |
|---|---|
| 科目 / 章节 | `db/schema.sql` 的 `INSERT INTO subjects/chapters`（含 `path`，练习页按它筛子章节） |
| 知识点 | `data/seed/questions.sql` 里生成器写的 `INSERT INTO knowledge_points` |
| 题目 | `data/seed/questions.json`（**不是** `questions.sql` —— JSON 不用再解 SQL 字面量） |
| 元信息 | `data/seed/import_manifest.json`（题量 / 分布 / 指纹） |
| 评分规则 | **import 后端常量**（`practice_service` 是本项目里它们的唯一定义处） |

用法：
    python tools/pwa/export-bank.py                 # → data/seed/pwa-bank.json
    python tools/pwa/export-bank.py --out /tmp/x.json --max-questions 200   # 小包，供本地试
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCHEMA_SQL = REPO / "db" / "schema.sql"
Q_SQL = REPO / "data" / "seed" / "questions.sql"
Q_JSON = REPO / "data" / "seed" / "questions.json"
MANIFEST = REPO / "data" / "seed" / "import_manifest.json"

#: 与 `apps/pwa/src/lib/api.ts::BANK_SCHEMA_VERSION` **必须一致**。
#: ★ 这一对是"两个可变物"里最典型的一种 —— 所以下面 `main()` 会去**读那个文件**核对，
#:   而不是"记得改两边"（那种约定会在某次改动里悄悄失效）。
BANK_SCHEMA_VERSION = 1


# ---------------------------------------------------------------- 取后端常量（唯一真相）

def load_grading_rules() -> dict[str, Any]:
    """从后端代码里取评分规则。**抄一份常量到这里 = 制造第二份语义**，所以是 import。"""
    sys.path.insert(0, str(REPO / "apps" / "api"))
    from app.services.practice_service import GRADABLE_TYPES, PARTIAL_CREDIT_RATIO

    return {
        "partial_credit_ratio": float(PARTIAL_CREDIT_RATIO),
        "gradable_types": list(GRADABLE_TYPES),
    }


def load_answer_module():
    """`answer.py` 的抬头明写它**故意不依赖 Pydantic**，就是为了能被独立脚本 import。"""
    sys.path.insert(0, str(REPO / "apps" / "api"))
    from app.schemas import answer

    return answer


# ---------------------------------------------------------------- SQL 行解析（只解这一个形状）

def _split_sql_row(body: str) -> list[Any]:
    """把 `(1001,'JGJJ', '建设工程经济', NULL, 100)` 的**内容**切成值列表。

    ★ 只处理这一类"字面量列表"：字符串（含 SQL 转义 `''`）、NULL、数字。
      括号里没有嵌套子查询 —— 有的话这里会**报错停下**，不会静静解错。
    """
    out: list[Any] = []
    buf = ""
    quote = False
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "'":
                if i + 1 < len(body) and body[i + 1] == "'":  # '' ⇒ 一个 '
                    buf += "'"
                    i += 2
                    continue
                quote = False
            else:
                buf += ch
        elif ch == "'":
            quote = True
        elif ch == ",":
            out.append(_lit(buf))
            buf = ""
        else:
            buf += ch
        i += 1
    if quote:
        raise ValueError(f"字符串没闭合：…{body[-60:]!r}")
    out.append(_lit(buf))
    return out


def _lit(tok: str) -> Any:
    s = tok.strip()
    if s == "" or s.upper() == "NULL":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_insert(sql_text: str, table: str) -> list[dict[str, Any]]:
    """解出 `INSERT INTO <table> (cols) VALUES (...), (...);` 的所有行。"""
    m = re.search(
        rf"INSERT INTO {table}\s*\(([^)]*)\)\s*VALUES(.*?);",
        sql_text,
        flags=re.S | re.I,
    )
    if m is None:
        raise SystemExit(f"✗ {SCHEMA_SQL.name} 里找不到 `INSERT INTO {table}` —— 数据源变了？")
    cols = [c.strip() for c in m.group(1).split(",")]
    rows: list[dict[str, Any]] = []
    depth = 0
    start = -1
    body = m.group(2)
    # ★★ 必须**先砍掉 `ON CONFLICT (id) DO NOTHING`** —— 它里面有一对括号，
    #    不砍的话会被当成"多出来的一行 `[id]`"，而它的值个数与列数不等 ⇒ 报错停手。
    #    （宁可报错也不静默解错：解错了会产出一个"科目少了几个"的包，而那种包
    #     在 PWA 里表现为**某些科目没有题** —— 不报错。）
    cut = re.search(r"\bON\s+CONFLICT\b", body, flags=re.I)
    if cut is not None:
        body = body[: cut.start()]
    quote = False
    for i, ch in enumerate(body):
        if quote:
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
        elif ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                vals = _split_sql_row(body[start:i])
                if len(vals) != len(cols):
                    raise SystemExit(
                        f"✗ {table} 一行有 {len(vals)} 个值、列有 {len(cols)} 个 —— 解析不可信，停手"
                    )
                rows.append(dict(zip(cols, vals)))
    return rows


# ---------------------------------------------------------------- 知识点（单行 INSERT）

_KP_RE = re.compile(
    r"INSERT INTO knowledge_points\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*ON CONFLICT",
    re.I,
)


def parse_knowledge_points(sql_text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in sql_text.splitlines():
        m = _KP_RE.search(line)
        if m is None:
            continue
        cols = [c.strip() for c in m.group(1).split(",")]
        vals = _split_sql_row(m.group(2))
        if len(vals) != len(cols):
            raise SystemExit("✗ 知识点那一行解不出来 —— 停手，别猜")
        out.append(dict(zip(cols, vals)))
    return out


# ---------------------------------------------------------------- 题目

def build_questions(
    raw: list[dict[str, Any]], answer_mod: Any, gradable: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """题目 → 包的形状。顺带收集"答案没归干净"的题（**发现就不导出**）。"""
    bad: list[str] = []
    out: list[dict[str, Any]] = []
    for q in raw:
        qtype = str(q["type"])
        doc = q.get("answer")
        problem = answer_mod.check_doc(qtype, doc)
        if problem is not None:
            bad.append(f"id={q['id']} type={qtype}：{problem}")
            continue

        # ★ 选项：用 `options_snapshot`（**导出时它就是没有 is_correct 的那一份**）。
        #   断言而不是"相信" —— 一旦哪天快照里带上正确答案，这里立刻红。
        opts = q.get("options_snapshot") or []
        clean_opts = []
        for o in opts:
            if "is_correct" in o:
                raise SystemExit(
                    f"✗ 题目 {q['id']} 的 options_snapshot 里出现了 is_correct —— "
                    "那意味着正确答案会随包发到前端。数据源变了，请先确认再继续。"
                )
            clean_opts.append(
                {"label": o["label"], "content": o["content"], "content_html": o.get("content_html")}
            )

        out.append(
            {
                "id": q["id"],
                "subject_id": q["subject_id"],
                "chapter_id": q.get("chapter_id"),
                "knowledge_point_id": q.get("knowledge_point_id"),
                "type": qtype,
                "stem": q["stem"],
                "stem_html": q.get("stem_html"),
                "answer": doc,
                "analysis": q.get("analysis"),
                "analysis_html": q.get("analysis_html"),
                "options": clean_opts,
                "score_default": float(q.get("score_default") or 1.0),
                "difficulty": q.get("difficulty"),
                "status": q.get("status") or "published",
            }
        )
    return out, bad


# ---------------------------------------------------------------- 一致性自检

def check_schema_version_sync() -> None:
    """`BANK_SCHEMA_VERSION` 与 PWA 侧那个常量必须一致 —— **读文件核对**，不靠记得。"""
    api_ts = REPO / "apps" / "pwa" / "src" / "lib" / "api.ts"
    if not api_ts.exists():
        raise SystemExit(f"✗ 找不到 {api_ts} —— PWA 还没建？")
    m = re.search(r"BANK_SCHEMA_VERSION\s*=\s*(\d+)", api_ts.read_text(encoding="utf-8"))
    if m is None:
        raise SystemExit(f"✗ {api_ts} 里找不到 BANK_SCHEMA_VERSION")
    if int(m.group(1)) != BANK_SCHEMA_VERSION:
        raise SystemExit(
            f"✗ 版本号不一致：本脚本 = {BANK_SCHEMA_VERSION}，PWA = {m.group(1)}。"
            "改版本必须两边一起改，并且想清「老用户手里那个旧包怎么办」。"
        )
    print(f"  ✓ 包版本与 PWA 一致（schema_version={BANK_SCHEMA_VERSION}）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出一建通个人 PWA 的题库包")
    ap.add_argument("--out", default=str(REPO / "data" / "seed" / "pwa-bank.json"))
    ap.add_argument(
        "--max-questions",
        type=int,
        default=0,
        help="只取前 N 题（本地试跑用；此时对账会相应放宽 —— 生成的不是完整包）",
    )
    args = ap.parse_args(argv)

    for p in (SCHEMA_SQL, Q_SQL, Q_JSON, MANIFEST):
        if not p.exists():
            raise SystemExit(f"✗ 缺 {p}（题库包依赖它；data/ 不在版本库里，见 docs/32 §1.1）")

    print("[1/6] 读评分规则（从后端代码 import，不抄常量）")
    rules = load_grading_rules()
    print(f"  ✓ partial_credit_ratio={rules['partial_credit_ratio']} gradable={rules['gradable_types']}")

    print("[2/6] 读科目 / 章节（db/schema.sql）")
    schema_text = SCHEMA_SQL.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    want_codes = set(manifest["by_subject"])
    subjects = [s for s in parse_insert(schema_text, "subjects") if s["code"] in want_codes]
    sub_ids = {s["id"] for s in subjects}
    chapters = [c for c in parse_insert(schema_text, "chapters") if c["subject_id"] in sub_ids]
    print(f"  ✓ 科目 {len(subjects)} 个 / 章节 {len(chapters)} 个")

    print("[3/6] 读知识点（data/seed/questions.sql）")
    kps = parse_knowledge_points(Q_SQL.read_text(encoding="utf-8"))
    print(f"  ✓ 知识点 {len(kps)} 个")

    print("[4/6] 读题目并**归一答案**（任一题没归干净就不导出）")
    raw_q = json.loads(Q_JSON.read_text(encoding="utf-8"))
    if args.max_questions:
        raw_q = raw_q[: args.max_questions]
    questions, bad = build_questions(raw_q, load_answer_module(), rules["gradable_types"])
    if bad:
        print(f"  ✗ {len(bad)} 道题的答案不符合规范形式（前 5 条）：")
        for b in bad[:5]:
            print(f"      {b}")
        print("  ⇒ 不导出。库里有两套写法时，先跑迁移统一口径（BL-20），再回来导出。")
        return 2
    n_opts = sum(len(q["options"]) for q in questions)
    print(f"  ✓ 题目 {len(questions)} 道 / 选项 {n_opts} 个")

    print("[5/6] 对账（与 import_manifest.json 逐项比）")
    mismatch: list[str] = []
    if not args.max_questions:
        if len(questions) != manifest["totals"]["questions"]:
            mismatch.append(f"题量 {len(questions)} ≠ {manifest['totals']['questions']}")
        if n_opts != manifest["totals"]["options"]:
            mismatch.append(f"选项 {n_opts} ≠ {manifest['totals']['options']}")
    if len(kps) != manifest["totals"]["knowledge_points"]:
        mismatch.append(f"知识点 {len(kps)} ≠ {manifest['totals']['knowledge_points']}")
    per = {}
    code_by_id = {s["id"]: s["code"] for s in subjects}
    for q in questions:
        c = code_by_id.get(q["subject_id"])
        per[c] = per.get(c, 0) + 1
    for code, n in manifest["by_subject"].items():
        got = per.get(code, 0)
        if not args.max_questions and got != n:
            mismatch.append(f"{code} {got} ≠ {n}")
    if mismatch:
        print("  ✗ 对账不通过：")
        for m in mismatch:
            print(f"      {m}")
        return 3
    print(f"  ✓ 题量 / 选项 / 知识点 / 科目分布逐项一致（by_subject={per}）")

    print("[6/6] 写包")
    check_schema_version_sync()
    by_type: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    for q in questions:
        by_type[q["type"]] = by_type.get(q["type"], 0) + 1
        by_diff[str(q["difficulty"])] = by_diff.get(str(q["difficulty"]), 0) + 1

    pkg = {
        "meta": {
            "schema_version": BANK_SCHEMA_VERSION,
            "bank_version": manifest["fingerprint"],
            "exported_at": manifest["generated_at"],
            "bank_name": manifest["bank_name"],
            "totals": {
                "questions": len(questions),
                "options": n_opts,
                "knowledge_points": len(kps),
            },
            "by_subject": per,
            "by_type": by_type,
            "by_difficulty": by_diff,
            "grading_rules": rules,
        },
        "subjects": subjects,
        "chapters": chapters,
        "kps": kps,
        "questions": questions,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pkg, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size = out.stat().st_size
    print(f"  ✓ {out}  ({size / 1024 / 1024:.2f} MB)")
    print()
    print("下一步：把这个文件传到手机 → 打开 PWA → 「导入题库」→ 选它。")
    print("★ 「传到手机」这一步无法自动化（那台设备是离线的），如实照做即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
