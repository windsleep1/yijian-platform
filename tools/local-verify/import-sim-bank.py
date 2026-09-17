#!/usr/bin/env python
"""把 Batch 1 的 6000 道仿真题真灌进库（Batch 5 验收标准 ⑤）。

    python tools/local-verify/import-sim-bank.py                 # upsert（默认，成功数=6000）
    python tools/local-verify/import-sim-bank.py --mode insert   # 幂等验证（重复数=6000）
    python tools/local-verify/import-sim-bank.py --dry-run       # 只上传+校验，不写库
    python tools/local-verify/import-sim-bank.py --publish       # 执行后再发布

## 为什么需要一个转换器，不能直接把 data/seed/questions.csv 喂进去

`data/seed/questions.csv` 是 Batch 1 的**导出**格式，不是 Batch 5 的**导入模板**，差在三处：

| 差异 | seed 导出 | 导入模板（docs/07 §4.2） | 直接喂进去的后果 |
|---|---|---|---|
| 章节/知识点 | `chapter_id` / `knowledge_point_id`（数字 ID） | `chapter_code` / `kp_code`（业务编码） | 两列都不认识 → **章节/知识点全丢**（NULL） |
| 评分点 | `analysis_points` 无该列 | `answer_points`（`文本|分值;;…`） | 352 道案例小问的评分点丢失 |
| 案例分组 | 只有 `parent_id` | `case_group_id` | 小问找不到大题，校验直接报错 |

upsert 模式下"丢章节/知识点"= **把库里 6000 道题的章节关系洗成 NULL**，属于数据事故。
所以这里从 `questions.json`（信息最全）重新生成一份**严格符合 24 列导入模板**的文件。

## 输出与"规范化"说明

生成的 JSON 用 upsert 灌库后，除下面三处**可解释的规范化**外，逐字段与 seed 一致：

1. `answer`：`multiple` 题补上 `partial_credit: true` —— 对齐 `docs/03` §5.1 的权威定义
   （seed 漏了这个键；`single`/`judge`/`case`/`case_sub` 与 seed 完全一致）。
2. `analysis_points`：整分值写成 `2` 而不是 `2.0`（jsonb 里两者不同值）。
3. `version +1`、追加 `question_versions` 快照与 `content_change_logs` —— 这是导入的
   正常副作用（回滚依据），`status` 不动（upsert 不覆盖老题状态）。

`content_hash` 与 seed **6000/6000 完全一致**（由 API 侧归一化算法重算校验），
所以 upsert 会逐条命中已有题目，而不是插出 6000 条重复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SEED_JSON = REPO / "data" / "seed" / "questions.json"

# 导入模板列（与 apps/api/app/schemas/admin_import.py::IMPORT_COLUMNS 一致）
COLUMNS: tuple[str, ...] = (
    "subject_code", "chapter_code", "kp_code", "type", "stem",
    "option_a", "option_b", "option_c", "option_d", "option_e", "option_f",
    "answer", "answer_points", "analysis", "score", "difficulty", "exam_year",
    "source_type", "source_name", "source_license", "tags",
    "case_group_id", "material", "media_urls",
)


# ================================================================ 转换

def _answer_text(item: dict[str, Any]) -> str:
    """把 seed 的 answer JSONB 还原成导入模板里的**文本**表示。"""
    qtype = item["type"]
    value = (item.get("answer") or {}).get("value")
    if qtype == "single":
        return "".join(value or [])
    if qtype == "multiple":
        # 必须用分隔符：`ACD` 会被当成一个标号，导致"答案不在选项中"
        return "|".join(value or [])
    if qtype == "judge":
        return (value or [""])[0]
    if qtype == "case":
        return value or ""
    # case_sub / fill / essay：单条文本
    return "".join(value or []) if isinstance(value, list) else (value or "")


def _points_text(item: dict[str, Any]) -> str:
    """`[{key,text,score}]` -> `文本|分值;;文本|分值`（与 parse_answer_points 互逆）。"""
    parts = []
    for p in item.get("analysis_points") or []:
        parts.append(f"{p.get('text', '')}|{p.get('score', 0)}")
    return ";;".join(parts)


def _case_group(item: dict[str, Any]) -> str:
    """把 seed 的 parent_id 关系翻译成导入模板的 case_group_id。"""
    if item["type"] == "case":
        return f"CASE-{item['id']}"
    if item["type"] == "case_sub":
        return f"CASE-{item.get('parent_id')}" if item.get("parent_id") else ""
    return ""


def to_import_row(
    item: dict[str, Any],
    chapter_code_by_id: dict[int, str],
    kp_code_by_id: dict[int, str],
) -> dict[str, str]:
    """seed 题目 -> 导入模板的一行（24 列，值一律转字符串）。"""
    row: dict[str, str] = {c: "" for c in COLUMNS}
    row["subject_code"] = item.get("subject_code") or ""
    cid, kid = item.get("chapter_id"), item.get("knowledge_point_id")
    row["chapter_code"] = chapter_code_by_id.get(int(cid)) if cid else ""
    row["kp_code"] = kp_code_by_id.get(int(kid)) if kid else ""
    row["type"] = item["type"]
    row["stem"] = item["stem"]
    for opt in sorted(item.get("options") or [], key=lambda o: o.get("sort_no", 0)):
        col = f"option_{str(opt['label']).lower()}"
        if col in row:
            row[col] = opt.get("content") or ""
    row["answer"] = _answer_text(item)
    row["answer_points"] = _points_text(item)
    row["analysis"] = item.get("analysis") or ""
    row["score"] = str(item.get("score_default") or 1)
    row["difficulty"] = str(item.get("difficulty") or 3)
    row["exam_year"] = str(item["exam_year"]) if item.get("exam_year") else ""
    row["source_type"] = item.get("source_type") or "self"
    row["source_name"] = item.get("source_name") or ""
    row["source_license"] = item.get("source_license") or ""
    row["tags"] = "|".join(item.get("tags") or [])
    row["case_group_id"] = _case_group(item)
    row["material"] = item.get("material_html") or ""
    row["media_urls"] = ""
    return row


def build_rows(
    items: list[dict[str, Any]],
    chapter_code_by_id: dict[int, str],
    kp_code_by_id: dict[int, str],
) -> list[dict[str, str]]:
    return [to_import_row(i, chapter_code_by_id, kp_code_by_id) for i in items]


async def load_code_maps(dsn: str) -> tuple[dict[int, str], dict[int, str]]:
    """从库里取 章节ID->code、知识点ID->code 两张映射表。

    用库而不是 schema.sql：知识点种子不在 schema.sql 里，
    而且"编码是否存在"的判据本来就该以库为准（导入校验也是查库）。
    """
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT id, code FROM chapters WHERE is_deleted = false")
        chapter = {int(r["id"]): r["code"] for r in rows}
        rows = await conn.fetch("SELECT id, code FROM knowledge_points WHERE is_deleted = false")
        kp = {int(r["id"]): r["code"] for r in rows}
        return chapter, kp
    finally:
        await conn.close()


# ================================================================ API 调用

def _api(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def login(client, base: str, phone: str, password: str) -> str:
    r = client.post(_api(base, "/auth/login/password"), json={"phone": phone, "password": password})
    b = r.json()
    if b.get("code") != 0:
        raise SystemExit(f"[import-sim-bank] 超管登录失败：{b.get('code')} {b.get('message')}")
    return b["data"]["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser(description="把 Batch 1 的 6000 道仿真题经 Batch 5 导入管道真灌进库")
    ap.add_argument("--base", default=os.environ.get("AI_BASE", "http://127.0.0.1:8123") + "/api/v1")
    ap.add_argument("--phone", default=os.environ.get("ADMIN_INIT_PHONE", "13800000000"))
    ap.add_argument("--password", default=os.environ.get("ADMIN_INIT_PASSWORD", "Admin@123456"))
    ap.add_argument("--dsn", default=(os.environ.get("DATABASE_URL") or "").replace(
        "postgresql+asyncpg://", "postgresql://"))
    ap.add_argument("--mode", choices=("upsert", "insert"), default="upsert")
    ap.add_argument("--subject-id", type=int, default=None, help="批次归属科目（默认随文件）")
    ap.add_argument("--dry-run", action="store_true", help="只上传+校验，不写库")
    ap.add_argument("--publish", action="store_true", help="执行后再发布")
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 道（调试用，0=全部）")
    ap.add_argument(
        "--out",
        default=None,
        help="只把生成的严格 24 列模板写成 CSV（UTF-8 with BOM）后退出，不上传。"
        "用于产出给浏览器手工走查用的文件。",
    )
    args = ap.parse_args()

    if not args.dsn:
        print("[import-sim-bank] 缺少 DATABASE_URL（或 --dsn），无法取章节/知识点编码表", file=sys.stderr)
        return 2

    items = json.loads(SEED_JSON.read_text(encoding="utf-8"))
    if args.limit:
        items = items[: args.limit]
    chapter_map, kp_map = asyncio.run(load_code_maps(args.dsn))
    rows = build_rows(items, chapter_map, kp_map)

    missing_chapter = sum(1 for r in rows if r["chapter_code"] == "")
    missing_kp = sum(1 for r in rows if r["kp_code"] == "")
    print(f"[import-sim-bank] 读入 {len(items)} 道题；未映射到 chapter_code 的 {missing_chapter} 行，"
          f"kp_code 空的 {missing_kp} 行")

    # ---- 只产出文件（给浏览器手工走查用），不上传 ----
    if args.out:
        import csv as _csv

        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig = UTF-8 with BOM：Excel 直接双击打开中文不乱码，
        # 也正是导入接口要能吃下的那种"Excel 另存为 CSV"的形态。
        with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(COLUMNS))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"[import-sim-bank] 已写出 CSV：{out_path}（{len(rows)} 行 × {len(COLUMNS)} 列，UTF-8 with BOM）")
        return 0

    try:
        import httpx  # noqa: F401
    except ImportError:
        print("[import-sim-bank] 缺少 httpx：pip install httpx", file=sys.stderr)
        return 2

    payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    print(f"[import-sim-bank] 生成导入文件 {len(payload) / 1048576:.1f}MB（JSON，24 列）")

    import httpx

    with httpx.Client(timeout=180) as client:
        token = login(client, args.base, args.phone, args.password)
        headers = {"Authorization": f"Bearer {token}"}

        t0 = time.perf_counter()
        r = client.post(
            _api(args.base, "/admin/imports/upload"),
            headers=headers,
            files={"file": ("sim-bank-6000.json", payload, "application/json")},
            data={
                "mode": args.mode,
                **({"subject_id": str(args.subject_id)} if args.subject_id else {}),
            },
        )
        b = r.json()
        if b.get("code") != 0:
            print(f"[import-sim-bank] 上传失败：{b.get('code')} {b.get('message')}")
            return 1
        batch_id = b["data"]["id"]
        print(f"[import-sim-bank] 上传 OK  batch_no={b['data']['batch_no']}  id={batch_id}  "
              f"total_rows={b['data']['total_rows']}")

        r = client.post(_api(args.base, f"/admin/imports/{batch_id}/validate"), headers=headers)
        b = r.json()
        if b.get("code") != 0:
            print(f"[import-sim-bank] 校验失败：{b.get('code')} {b.get('message')}")
            return 1
        d = b["data"]
        print(f"[import-sim-bank] 校验 OK  total={d['total_rows']} success={d['success_rows']} "
              f"failed={d['failed_rows']} duplicate={d['duplicate_rows']} "
              f"updated={d['updated_rows']}  用时={time.perf_counter() - t0:.1f}s")
        errs = d["error_report"]["errors"]
        if errs:
            print(f"[import-sim-bank] 前 {min(len(errs), 10)} 条错误：")
            for e in errs[:10]:
                print(f"    row {e['row_no']}  [{e['field']}] {e['message']}")
            return 1

        if args.dry_run:
            print("[import-sim-bank] --dry-run：到此为止，未写入任何题目")
            return 0

        r = client.post(
            _api(args.base, f"/admin/imports/{batch_id}/execute"),
            headers=headers,
            json={"publish": bool(args.publish)},
        )
        b = r.json()
        if b.get("code") != 0:
            print(f"[import-sim-bank] 执行失败：{b.get('code')} {b.get('message')}")
            return 1
        e = b["data"]
        print(f"[import-sim-bank] 执行 OK  status={e['status']} success={e['success_rows']} "
              f"updated={e['updated_rows']} 用时={time.perf_counter() - t0:.1f}s")

        r = client.get(_api(args.base, f"/admin/imports/{batch_id}"), headers=headers)
        d = r.json()["data"]
        print("\n================ 验收证据（GET /admin/imports/{id}）================")
        print(f"  batch_no      = {d['batch_no']}")
        print(f"  status        = {d['status']}")
        print(f"  total_rows    = {d['total_rows']}")
        print(f"  success_rows  = {d['success_rows']}")
        print(f"  failed_rows   = {d['failed_rows']}")
        print(f"  duplicate_rows= {d['duplicate_rows']}")
        print(f"  updated_rows  = {d['updated_rows']}")
        print(f"  mode          = {d['mode']}")
        print("===================================================================\n")

        total, success, failed = d["total_rows"], d["success_rows"], d["failed_rows"]
        if args.mode == "upsert":
            ok = (total == len(items) and success == len(items) and failed == 0)
        else:
            ok = (total == len(items) and failed == 0)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
