"""Pass 2b E2E 数据准备：造两条组卷规则（一条能抽满、一条题库不足）。

⚠️ 只造**规则**，不碰共享的种子题库（坑 45）。

跑法（需要 API 在 8123）：
    python seed-pass2b.py
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

API = "http://127.0.0.1:8123/api/v1"
SUBJECT_ID = 1001  # 建筑工程


def call(method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def must(resp, what: str):
    if resp.get("code") != 0:
        raise SystemExit(f"❌ {what} 失败：{resp}")
    return resp["data"]


def main() -> None:
    tok = must(
        call("POST", "/auth/login/password", body={"phone": "13800000000", "password": "Admin@123456"}),
        "登录",
    )["access_token"]

    tag = uuid.uuid4().hex[:4].upper()
    out: dict[str, str] = {}

    # ---------------------------------------------------------------- 1) 能抽满的规则
    ok_rule = must(
        call("POST", "/admin/paper-rules", tok, {
            "name": f"E2E 标准模拟卷规则 {tag}",
            "subject_id": SUBJECT_ID,
            "type": "mock",
            "duration_min": 180,
            "strategy": "random",
            "rules": [
                {"type": "single", "count": 15, "score": 1},
                {"type": "multiple", "count": 8, "score": 2},
                {"type": "judge", "count": 8, "score": 1},
            ],
        }),
        "建可抽满的规则",
    )
    out["ok_rule"] = ok_rule["id"]
    print(f"✅ 可抽满的规则：{ok_rule['name']}（计划 {ok_rule['planned_count']} 题 / "
          f"{ok_rule['planned_score']} 分）")

    # 用试算确认它真的抽得满（如果不满，说明种子库有问题，早点炸出来）
    pv = must(
        call("POST", "/admin/paper-rules/preview", tok, {
            "subject_id": SUBJECT_ID, "rules": ok_rule["rules"], "seed": 20260101,
        }),
        "试算可抽满规则",
    )
    print(f"   试算：need={pv['total_need']} got={pv['total_got']} missing={pv['total_missing']} "
          f"ok={pv['ok']}")
    if not pv["ok"]:
        raise SystemExit("❌ 这条规则本应能抽满，实际抽不满 —— 先检查种子题库是否完整")

    # ---------------------------------------------------------------- 2) 题库不足的规则（暂停用）
    gap_rule = must(
        call("POST", "/admin/paper-rules", tok, {
            "name": f"E2E 题库不足规则 {tag}",
            "subject_id": SUBJECT_ID,
            "type": "custom",
            "duration_min": 120,
            "strategy": "random",
            "rules": [
                {"type": "case", "count": 200, "score": 2},
                {"type": "single", "count": 10, "score": 1},
            ],
        }),
        "建题库不足的规则",
    )
    out["gap_rule"] = gap_rule["id"]
    print(f"⚠️ 题库不足的规则：{gap_rule['name']}")

    pv2 = must(
        call("POST", "/admin/paper-rules/preview", tok, {
            "subject_id": SUBJECT_ID, "rules": gap_rule["rules"], "seed": 20260101,
        }),
        "试算题库不足规则",
    )
    print(f"   试算：need={pv2['total_need']} got={pv2['total_got']} "
          f"missing={pv2['total_missing']} ok={pv2['ok']}")

    # ---------------------------------------------------------------- 3) 一条停用的规则（演示启用/停用）
    off_rule = must(
        call("POST", "/admin/paper-rules", tok, {
            "name": f"E2E 已停用规则 {tag}",
            "subject_id": SUBJECT_ID,
            "type": "daily",
            "duration_min": 30,
            "strategy": "random",
            "rules": [{"type": "judge", "count": 10, "score": 1}],
        }),
        "建规则（待停用）",
    )
    must(call("PUT", f"/admin/paper-rules/{off_rule['id']}", tok, {"status": "off"}), "停用规则")
    out["off_rule"] = off_rule["id"]
    print(f"⏸ 已停用的规则：{off_rule['name']}")

    print("\n===== Pass 2b E2E 数据就绪 =====")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n（本轮 tag = {tag}，UI 上按这个后缀找）")


if __name__ == "__main__":
    main()
