#!/usr/bin/env python
"""Batch 5 导入管道 · 手工验收探针（同时产出「错误报告样例」）。

跑之前 API 必须是活的（真 PG + fakeredis，:8123）：

    python tools/local-verify/probe-import-pipeline.py

覆盖验收标准 ①②③④，每一步都打印实际响应：

  ① 错误文件：100 行，第 57 行 answer=D 但选项只有 A/B/C，第 100 行 chapter_code 不存在
     → 断言**逐行**返回 {row_no, field, message}；断言默认**一行都不写**；
       再断言 allow_partial=true 时才写入 98 行（并可整批回滚回基线）
  ② 幂等：同一份文件连导 10 次，只有第 1 次写入，总题量不变
  ③ 全链路：upload → validate → execute → publish → rollback，题量回到基线
  ④ 事务/回滚留痕：重复回滚被拒；软删除仍可查

**自清理**：探针跑完会把自己写进库的批次全部回滚，保持题库基线不变
（验收用例不该污染被测数据）。退出码非 0 表示验收失败。
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "docs" / "samples"

BASE = os.environ.get("AI_BASE", "http://127.0.0.1:8123") + "/api/v1"
PHONE = os.environ.get("ADMIN_INIT_PHONE", "13800000000")
PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD", "Admin@123456")

COLUMNS = (
    "subject_code", "chapter_code", "kp_code", "type", "stem",
    "option_a", "option_b", "option_c", "option_d", "option_e", "option_f",
    "answer", "answer_points", "analysis", "score", "difficulty", "exam_year",
    "source_type", "source_name", "source_license", "tags",
    "case_group_id", "material", "media_urls",
)

SUBJECT = "SW-SZ"        # 市政实务（2007）
CHAPTER = "SZ-01"        # 该科目下真实存在的章节编码

failures: list[str] = []
dirty: list[int] = []    # 写进过库、需要回滚清理的批次


def check(cond: bool, label: str) -> bool:
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        failures.append(label)
    return cond


def csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in COLUMNS})
    return buf.getvalue().encode("utf-8-sig")


def good_row(i) -> dict:
    return {
        "subject_code": SUBJECT, "chapter_code": CHAPTER, "kp_code": "",
        "type": "single",
        "stem": f"【探针】第 {i} 题：关于市政公用工程施工管理，下列说法正确的是？",
        "option_a": f"A-{i} 未经验收即投入使用",
        "option_b": f"B-{i} 先验收合格再进入下道工序",
        "option_c": f"C-{i} 口头交底代替书面交底",
        "answer": "B",
        "analysis": f"第 {i} 题解析：验收合格是进入下道工序的前置条件，故选 B。",
        "score": "1", "difficulty": "3",
        "source_type": "self", "tags": "探针|单选题",
    }


def call(client, method, path, **kw):
    r = client.request(method, f"{BASE}{path}", **kw)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:400]}


def count_questions(client, headers) -> int:
    """当前题库题目总数（不含软删除）—— 判断"有没有真的写入"的硬指标。"""
    st, b = call(client, "GET", "/admin/questions?page=1&page_size=1", headers=headers)
    return int(b["data"]["total"])


def upload_validate(client, H, name: str, content: bytes, mode: str = "insert"):
    st, b = call(client, "POST", "/admin/imports/upload", headers=H,
                 files={"file": (name, content, "text/csv")},
                 data={"mode": mode, "source_type": "self"})
    bid = int(b["data"]["id"])
    st, b = call(client, "POST", f"/admin/imports/{bid}/validate", headers=H)
    return bid, b["data"]


def main() -> int:
    with httpx.Client(timeout=120) as client:
        st, b = call(client, "POST", "/auth/login/password",
                     json={"phone": PHONE, "password": PASSWORD})
        if b.get("code") != 0:
            print(f"超管登录失败：{st} {b}")
            return 2
        H = {"Authorization": f"Bearer {b['data']['access_token']}"}
        print(f"登录 OK（超管 {PHONE}）")
        baseline = count_questions(client, H)
        print(f"题库基线：{baseline} 道（未删除）\n")

        try:
            # ============================================= ① 错误文件
            print("=" * 72)
            print("【验收①】错误文件：第 57 行 answer 不在选项中 / 第 100 行 chapter_code 不存在")
            print("=" * 72)
            rows = [good_row(i) for i in range(1, 101)]
            rows[56].update({"option_d": "", "answer": "D"})   # 第 57 行
            rows[99]["chapter_code"] = "SZ-99"                 # 第 100 行

            wrong = csv_bytes(rows)
            SAMPLES.mkdir(parents=True, exist_ok=True)
            (SAMPLES / "batch5-wrong-file.csv").write_bytes(wrong)

            bid, d = upload_validate(client, H, "wrong-100.csv", wrong)
            errs = d["error_report"]["errors"]
            (SAMPLES / "batch5-error-report.json").write_text(
                json.dumps(
                    {"_note": "Batch 5 验收标准 ① 的错误报告样例；对应文件 batch5-wrong-file.csv",
                     "_request": f"POST /api/v1/admin/imports/{bid}/validate",
                     "batch_id": str(bid), "response": {
                         "code": 0, "message": "校验完成，未写入任何题目", "data": d}},
                    ensure_ascii=False, indent=2), encoding="utf-8")

            print(f"校验结果 total={d['total_rows']} success={d['success_rows']} "
                  f"failed={d['failed_rows']} total_errors={d['error_report']['total_errors']}")
            for e in errs:
                print(f"    row_no={e['row_no']:<4} field={e['field']:<12} {e['message']}")

            by_row = {e["row_no"]: e for e in errs}
            check(57 in by_row and by_row[57]["field"] == "answer",
                  "第 57 行报错且 field=answer")
            check(100 in by_row and by_row[100]["field"] == "chapter_code",
                  "第 100 行报错且 field=chapter_code")
            check(d["failed_rows"] == 2, f"失败行数 = 2（实际 {d['failed_rows']}）")
            check(count_questions(client, H) == baseline,
                  "校验是 dry-run：题目总数未变（一行都没写）")

            # 默认严格：含错批次整体拒绝
            st, b = call(client, "POST", f"/admin/imports/{bid}/execute", headers=H, json={})
            check(b.get("code") == 40901, f"含错批次默认拒绝执行（code={b.get('code')}）")
            check("未通过校验" in (b.get("message") or ""), "拒绝原因说明了失败行数")
            check(count_questions(client, H) == baseline, "题目总数仍不变（未写入）")

            # 显式 opt-in 才允许"跳过错行"
            st, b = call(client, "POST", f"/admin/imports/{bid}/execute", headers=H,
                         json={"allow_partial": True})
            check(b.get("code") == 0 and b["data"]["success_rows"] == 98,
                  f"allow_partial=true 只导入通过的 98 行（success={b.get('data',{}).get('success_rows')}）")
            dirty.append(bid)
            check(count_questions(client, H) == baseline + 98, "部分导入写入 98 行")
            st, b = call(client, "POST", f"/admin/imports/{bid}/rollback", headers=H,
                         json={"reason": "探针清理-错误文件批次"})
            check(b.get("data", {}).get("rolled_back_questions") == 98, "回滚掉这 98 行")
            check(count_questions(client, H) == baseline, "回滚后回到基线")

            # ============================================= ③ 全链路
            print("\n" + "=" * 72)
            print("【验收③】正确文件全链路：upload → validate → execute → publish → rollback")
            print("=" * 72)
            stamp = str(int(time.time() * 1000) % 10**9)
            good = csv_bytes([good_row(f"{stamp}-{i}") for i in range(1, 5)])
            bid_good, d = upload_validate(client, H, "good-4.csv", good)
            check(d["failed_rows"] == 0 and d["success_rows"] == 4,
                  f"4 行全部通过校验（success={d['success_rows']} failed={d['failed_rows']}）")

            st, b = call(client, "POST", f"/admin/imports/{bid_good}/execute", headers=H, json={})
            check(b["data"]["status"] == "done" and b["data"]["success_rows"] == 4,
                  f"执行成功 success={b['data']['success_rows']}")
            after = count_questions(client, H)
            check(after == baseline + 4, f"题目总数 +4（{baseline} → {after}）")

            st, b = call(client, "GET", f"/admin/imports/{bid_good}", headers=H)
            qids = [r["question_id"] for r in b["data"]["rows"] if r["question_id"]]
            check(len(qids) == 4, "批次详情带回 4 个 question_id")
            st, b = call(client, "GET", f"/admin/questions/{qids[0]}", headers=H)
            check(b["data"]["status"] == "draft", "导入的题初始 status=draft")

            st, b = call(client, "POST", f"/admin/imports/{bid_good}/publish", headers=H, json={})
            check(b.get("code") == 0, "发布接口返回成功")
            st, b = call(client, "GET", f"/admin/questions/{qids[0]}", headers=H)
            check(b["data"]["status"] == "published", "发布后 status=published")

            # 重复执行被拒（同一批只允许执行一次）
            st, b = call(client, "POST", f"/admin/imports/{bid_good}/execute", headers=H, json={})
            check(b.get("code") == 40901, "重复执行被拒（40901）")

            st, b = call(client, "POST", f"/admin/imports/{bid_good}/rollback", headers=H,
                         json={"reason": "探针验收回滚"})
            check(b["data"]["rolled_back_questions"] == 4, "回滚 4 道")
            check(count_questions(client, H) == baseline, f"回滚后回到基线 {baseline}")
            st, b = call(client, "POST", f"/admin/imports/{bid_good}/rollback", headers=H, json={})
            check(b.get("code") == 40901, "重复回滚被拒（40901）")
            st, b = call(client, "GET", f"/admin/questions/{qids[0]}", headers=H)
            check(b.get("code") == 0 and b["data"]["is_deleted"] is True,
                  "回滚是软删除：题目仍可查到且 is_deleted=true")

            # ============================================= ② 幂等 ×10
            print("\n" + "=" * 72)
            print("【验收②】同一份正确文件导入 10 次 → 题量不变")
            print("=" * 72)
            stamp = str(int(time.time() * 1000) % 10**9)
            same = csv_bytes([good_row(f"idem-{stamp}-{i}") for i in range(1, 4)])
            results = []
            for n in range(1, 11):
                bid_n, _ = upload_validate(client, H, "same-3.csv", same)
                st, b = call(client, "POST", f"/admin/imports/{bid_n}/execute", headers=H, json={})
                results.append((b["data"]["success_rows"], b["data"]["duplicate_rows"]))
                dirty.append(bid_n)
                print(f"    第 {n:>2} 次：success={results[-1][0]} duplicate={results[-1][1]}")
            after = count_questions(client, H)
            check(results[0][0] == 3, "第 1 次真正写入 3 题")
            check(all(r[0] == 0 for r in results[1:]), "第 2~10 次 success 均为 0")
            check(all(r[1] == 3 for r in results[1:]), "第 2~10 次 duplicate 均为 3")
            check(after == baseline + 3, f"总题量只增加 3（{baseline} → {after}）")
        finally:
            # 自清理：把探针写进过库的批次全部回滚
            print("\n" + "-" * 72)
            print("自清理：回滚探针写过的批次")
            for bid in dirty:
                st, b = call(client, "GET", f"/admin/imports/{bid}", headers=H)
                if b.get("data", {}).get("status") == "done" and \
                        int(b["data"].get("success_rows") or 0) > 0:
                    call(client, "POST", f"/admin/imports/{bid}/rollback", headers=H,
                         json={"reason": "探针自清理"})
            final = count_questions(client, H)
            print(f"  清理后题库题量：{final}（基线 {baseline}）")
            if final != baseline:
                failures.append(f"自清理后题量与基线不一致（{final} != {baseline}）")

    print("\n" + "=" * 72)
    if failures:
        print(f"结果：{len(failures)} 项未通过")
        for f in failures:
            print("  - " + f)
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
