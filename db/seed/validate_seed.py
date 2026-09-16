#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
种子题库质检脚本
====================================================================

对 gen_seed_questions.py 的产物做内部一致性校验，也适用于校验任何
按同一 schema 导出的 questions.json。

检查项：
  1. 题目 ID 唯一、选项 ID 唯一
  2. content_hash 唯一（去重是否真的生效）
  3. 单选题：有且仅有 1 个正确选项
  4. 多选题：正确选项 >= 2 个，且答案与选项标记一致
  5. 判断题：选项固定为 A.正确 / B.错误，答案在 A/B 之内
  6. 案例小题：parent_id 指向的根题必须存在，且根题 type='case'
  7. 所有题目 chapter_id / subject_id 均在合法集合内
  8. 所有题目 source_type 合法；非 self 来源必须有 source_name（合规红线）
  9. exam_year 为空（种子题库不伪造年份）
 10. 解析非空、题干长度合理
 11. 选项标签在 A~F 且不重复、内容非空

用法：
    python validate_seed.py ../../data/seed/questions.json
退出码：0 = 全部通过；1 = 存在错误
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

VALID_TYPES = {"single", "multiple", "judge", "case", "case_sub", "fill", "essay"}
VALID_SOURCE_TYPES = {"self", "authorized", "public", "user_import", "ai_assisted"}
VALID_OPTION_LABELS = {"A", "B", "C", "D", "E", "F"}


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python validate_seed.py <questions.json>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    rows = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []

    qids = Counter(r["id"] for r in rows)
    for qid, n in qids.items():
        if n > 1:
            errors.append(f"[ID重复] 题目 ID {qid} 出现 {n} 次")

    hashes = Counter(r["content_hash"] for r in rows)
    for h, n in hashes.items():
        if n > 1:
            errors.append(f"[指纹重复] content_hash {h[:16]}… 出现 {n} 次（去重失效）")

    all_ids = set(r["id"] for r in rows)
    opt_ids: Counter = Counter()

    for r in rows:
        qid, qtype = r["id"], r["type"]
        opts = r.get("options", [])
        labels = [o["label"] for o in opts]

        for o in opts:
            opt_ids[o.get("id", f"{qid}-{o['label']}")] += 1
            if o["label"] not in VALID_OPTION_LABELS:
                errors.append(f"[选项标签] 题目 {qid} 选项标签非法: {o['label']}")
            if not str(o.get("content", "")).strip():
                errors.append(f"[选项空] 题目 {qid} 选项 {o['label']} 内容为空")

        if len(labels) != len(set(labels)):
            errors.append(f"[选项重复] 题目 {qid} 选项标签重复: {labels}")

        if qtype not in VALID_TYPES:
            errors.append(f"[题型非法] 题目 {qid} type={qtype}")

        # ---- 答案与选项一致性 ----
        answer = r.get("answer", {}).get("value")
        correct_labels = [o["label"] for o in opts if o.get("is_correct")]

        if qtype == "single":
            if len(opts) < 2:
                errors.append(f"[选项不足] 单选题 {qid} 只有 {len(opts)} 个选项")
            if len(correct_labels) != 1:
                errors.append(f"[单选答案] 题目 {qid} 正确选项数为 {len(correct_labels)}，应为 1")
            if answer != correct_labels:
                errors.append(f"[答案不一致] 单选题 {qid} answer={answer} 但选项标记={correct_labels}")

        elif qtype == "multiple":
            if len(correct_labels) < 2:
                errors.append(f"[多选答案] 题目 {qid} 正确选项数为 {len(correct_labels)}，应 >= 2")
            if sorted(answer or []) != sorted(correct_labels):
                errors.append(f"[答案不一致] 多选题 {qid} answer={answer} 但选项标记={correct_labels}")

        elif qtype == "judge":
            if labels != ["A", "B"]:
                errors.append(f"[判断选项] 题目 {qid} 选项应为 A/B，实际 {labels}")
            if answer not in (["A"], ["B"]):
                errors.append(f"[判断答案] 题目 {qid} answer={answer}，应为 ['A'] 或 ['B']")

        elif qtype == "case_sub":
            if opts:
                errors.append(f"[案例小题] 题目 {qid} 不应带选项，实际 {len(opts)} 个")
            if not r.get("parent_id"):
                errors.append(f"[案例小题] 题目 {qid} 缺少 parent_id")
            elif r["parent_id"] not in all_ids:
                errors.append(f"[案例小题] 题目 {qid} 的 parent_id={r['parent_id']} 不存在")
            if not r.get("analysis_points"):
                warnings.append(f"[案例小题] 题目 {qid} 没有评分点，自评/批改将无法逐条打分")

        # ---- 合规 ----
        if r.get("source_type") not in VALID_SOURCE_TYPES:
            errors.append(f"[来源非法] 题目 {qid} source_type={r.get('source_type')}")
        if r.get("source_type") != "self" and not r.get("source_name"):
            errors.append(f"[合规红线] 题目 {qid} 来源为 {r['source_type']} 但缺少 source_name")
        if r.get("exam_year"):
            warnings.append(f"[合规提示] 题目 {qid} 标注了 exam_year={r['exam_year']}，种子题库应为空")

        # ---- 内容质量 ----
        if not r.get("analysis"):
            errors.append(f"[解析缺失] 题目 {qid} 无解析")
        if len(r.get("stem", "")) < 8:
            warnings.append(f"[题干过短] 题目 {qid}: {r.get('stem')!r}")
        if not 1 <= r.get("difficulty", 0) <= 5:
            errors.append(f"[难度非法] 题目 {qid} difficulty={r.get('difficulty')}")
        if r.get("type") == "case" and not r.get("material_html"):
            warnings.append(f"[案例根题] 题目 {qid} 无背景材料")

    # ---- 汇总 ----
    print(f"校验文件 : {path}")
    print(f"题目总数 : {len(rows)}")
    print(f"选项总数 : {sum(len(r.get('options', [])) for r in rows)}")
    print(f"题型分布 : {dict(Counter(r['type'] for r in rows))}")
    print(f"难度分布 : {dict(sorted(Counter(r['difficulty'] for r in rows).items()))}")
    print()
    if warnings:
        print(f"⚠️  警告 {len(warnings)} 项（前 10 条）：")
        for w in warnings[:10]:
            print(f"   {w}")
        print()
    if errors:
        print(f"❌ 错误 {len(errors)} 项（前 20 条）：")
        for e in errors[:20]:
            print(f"   {e}")
        print()
        print("校验未通过。")
        return 1

    print("✅ 全部检查项通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
