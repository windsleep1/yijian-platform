#!/usr/bin/env python
"""Acceptance-criterion 6 probe: a subject-scoped researcher can only import
questions that belong to their own professional subject.

This deliberately drives the *real* seeded accounts written by
`tools/local-verify/seed-demo-users.ps1` (not synthetic throw-away users), so that
running the seed script + this probe is a complete, reproducible demonstration of
criterion 6:

    13900000002 / Researcher@123456   role=researcher, scope_type=subject, scope_id=2007 (SW-SZ 市政)

Two gates must hold:
  gate 1  batch-level: uploading a batch pinned to subject_id=2001 (SW-JZ 建筑)
          must be refused (40301) before any file is parsed.
  gate 2  row-level:   a file mixing their own subject with a foreign subject must
          report the foreign row as an error on field `subject_code`, while the
          own row still validates and imports. Rollback restores the baseline.

Usage:
    python tools/local-verify/probe-researcher-scope.py

Requires the API up on :8123 with the seed accounts present.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import uuid

import httpx

BASE = os.environ.get("AI_BASE", "http://localhost:8123") + "/api/v1"

ADMIN = ("13800000000", "Admin@123456")
RESEARCHER = ("13900000002", "Researcher@123456")

SZ_SUBJECT_ID = 2007          # 市政实务  (the researcher's own subject)
SZ_CHAPTER = "SZ-01"
JZ_SUBJECT_ID = 2001          # 建筑实务  (foreign, used as the out-of-scope control)
JZ_CHAPTER = "JZ-01"

COLUMNS = (
    "subject_code", "chapter_code", "kp_code", "type", "stem",
    "option_a", "option_b", "option_c", "option_d", "option_e", "option_f",
    "answer", "answer_points", "analysis", "score", "difficulty", "exam_year",
    "source_type", "source_name", "source_license", "tags",
    "case_group_id", "material", "media_urls",
)

_ok = 0
_bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ok, _bad
    if cond:
        _ok += 1
        print(f"  PASS  {label}")
    else:
        _bad += 1
        print(f"  FAIL  {label}  {detail}")


def login(client: httpx.Client, phone: str, password: str) -> str:
    r = client.post(f"{BASE}/auth/login/password", json={"phone": phone, "password": password})
    b = r.json()
    if b.get("code") != 0:
        print(f"FATAL: login failed for {phone}: {b}")
        sys.exit(2)
    return b["data"]["access_token"]


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in COLUMNS})
    return buf.getvalue().encode("utf-8-sig")


def row(*, subject: str, chapter: str, tag: str) -> dict:
    return {
        "subject_code": subject, "chapter_code": chapter, "kp_code": "",
        "type": "single",
        "stem": f"[probe-scope] {tag} 关于施工管理与验收，下列说法正确的是？",
        "option_a": f"A-{tag} 未经验收即投入使用",
        "option_b": f"B-{tag} 先验收合格再进入下道工序",
        "option_c": f"C-{tag} 口头交底代替书面交底",
        "answer": "B",
        "analysis": f"解析 {tag}：验收合格是进入下道工序的前置条件，故选 B。",
        "score": "1", "difficulty": "3",
        "source_type": "self", "tags": "probe|scope",
    }


def upload(client, h, content: bytes, *, name: str, subject_id: int | None = None) -> dict:
    data = {"mode": "insert", "source_type": "self"}
    if subject_id is not None:
        data["subject_id"] = str(subject_id)
    return client.post(
        f"{BASE}/admin/imports/upload", headers=h,
        files={"file": (name, content, "text/csv")}, data=data,
    ).json()


def count(client, h) -> int:
    b = client.get(f"{BASE}/admin/questions?page=1&page_size=1", headers=h).json()
    return int(b["data"]["total"])


def main() -> int:
    c = httpx.Client(timeout=60.0)
    admin_h = headers(login(c, *ADMIN))
    res_h = headers(login(c, *RESEARCHER))
    print(f"base={BASE}")
    print(f"researcher scope = subject:{SZ_SUBJECT_ID} (SW-SZ)\n")

    baseline = count(c, admin_h)
    print(f"baseline question count = {baseline}\n")

    dirty: list[int] = []
    try:
        # ---------------------------------------------------------- gate 1
        print("gate 1 - batch pinned to a foreign subject (subject_id=2001)")
        up = upload(c, res_h, csv_bytes([row(subject="SW-JZ", chapter=JZ_CHAPTER, tag="g1")]),
                    name="g1.csv", subject_id=JZ_SUBJECT_ID)
        check("refused with 40301", up.get("code") == 40301, str(up))
        check("message mentions data scope", "数据范围" in (up.get("message") or ""), str(up.get("message")))
        check("no batch row created / no write", count(c, admin_h) == baseline)

        # ---------------------------------------------------------- gate 2
        print("\ngate 2 - file mixing own subject with a foreign subject")
        mixed = csv_bytes([
            row(subject="SW-SZ", chapter=SZ_CHAPTER, tag="own-" + uuid.uuid4().hex[:6]),
            row(subject="SW-JZ", chapter=JZ_CHAPTER, tag="other-" + uuid.uuid4().hex[:6]),
        ])
        up = upload(c, res_h, mixed, name="mixed.csv")
        check("upload accepted (0)", up.get("code") == 0, str(up))
        if up.get("code") != 0:
            return finish(baseline, c, admin_h, dirty)
        bid = int(up["data"]["id"])
        dirty.append(bid)

        v = c.post(f"{BASE}/admin/imports/{bid}/validate", headers=res_h).json()
        check("validate ok (0)", v.get("code") == 0, str(v))
        d = v.get("data", {})
        check("failed_rows == 1", d.get("failed_rows") == 1, str(d.get("failed_rows")))
        check("success_rows == 1", d.get("success_rows") == 1, str(d.get("success_rows")))
        errs = (d.get("error_report") or {}).get("errors") or []
        err = errs[0] if errs else {}
        check("error points at field=subject_code", err.get("field") == "subject_code", str(err))
        check("error mentions data scope", "数据范围" in (err.get("message") or ""), str(err.get("message")))

        # strict execute should refuse while any row failed
        ex_strict = c.post(f"{BASE}/admin/imports/{bid}/execute", headers=res_h, json={}).json()
        check("strict execute refused (40901)", ex_strict.get("code") == 40901, str(ex_strict))
        check("still nothing written", count(c, admin_h) == baseline)

        # opt-in partial execute imports the own-subject row only
        ex = c.post(f"{BASE}/admin/imports/{bid}/execute", headers=res_h,
                    json={"allow_partial": True}).json()
        check("partial execute ok (0)", ex.get("code") == 0, str(ex))
        check("imported exactly 1 row", (ex.get("data") or {}).get("success_rows") == 1, str(ex.get("data")))
        check("count +1", count(c, admin_h) == baseline + 1)

        # ---------------------------------------------------------- rollback
        print("\nrollback restores the baseline")
        rb = c.post(f"{BASE}/admin/imports/{bid}/rollback", headers=res_h, json={}).json()
        check("rollback ok (0)", rb.get("code") == 0, str(rb))
        dirty.remove(bid)
        check("count back to baseline", count(c, admin_h) == baseline)

    finally:
        for bid in dirty:
            try:
                c.post(f"{BASE}/admin/imports/{bid}/rollback", headers=admin_h, json={})
            except Exception:
                pass
        c.close()

    return finish(baseline, None, None, [])


def finish(baseline, c, admin_h, dirty) -> int:
    print(f"\n{'=' * 56}")
    print(f"PASS {_ok} / FAIL {_bad}")
    if _bad:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS - researcher is confined to their own professional subject")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
