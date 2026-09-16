#!/usr/bin/env python
"""一次性清理：把探针在"修复前"误写入的那批（含错文件却执行成功）回滚掉。

用法：python tools/local-verify/cleanup-probe-batch.py <batch_id> [...]
"""
from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("AI_BASE", "http://127.0.0.1:8123") + "/api/v1"


def main() -> int:
    ids = sys.argv[1:]
    if not ids:
        print("用法: cleanup-probe-batch.py <batch_id> [...]")
        return 2
    with httpx.Client(timeout=60) as c:
        b = c.post(f"{BASE}/auth/login/password", json={
            "phone": os.environ.get("ADMIN_INIT_PHONE", "13800000000"),
            "password": os.environ.get("ADMIN_INIT_PASSWORD", "Admin@123456"),
        }).json()
        H = {"Authorization": f"Bearer {b['data']['access_token']}"}
        for bid in ids:
            r = c.post(f"{BASE}/admin/imports/{bid}/rollback", headers=H,
                       json={"reason": "清理探针误写批次"}).json()
            print(f"batch {bid}: code={r.get('code')} msg={r.get('message')} data={r.get('data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
