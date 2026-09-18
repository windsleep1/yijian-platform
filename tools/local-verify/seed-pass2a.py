"""Pass 2a E2E 数据准备：造几张处于不同状态的卷 + 一个 viewer 账号。

跑法（需要 API 在 8123）：
    python seed-pass2a.py
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

    # ---------------------------------------------------------------- 1) 完整卷（可发布）
    exam = must(
        call("POST", "/admin/exams", tok, {
            "subject_id": SUBJECT_ID,
            "title": f"E2E 完整模考卷 {tag}",
            "type": "mock",
            "exam_year": 2026,
            "paper_no": f"A-{tag}",
            "duration_min": 180,
            "pass_score": 78,
            "is_free": True,
            "sections": [
                {"name": "一、单项选择题", "question_type": "single", "question_count": 20, "score_per": 1, "sort_no": 0},
                {"name": "二、多项选择题", "question_type": "multiple", "question_count": 10, "score_per": 2, "sort_no": 1},
                {"name": "三、判断题", "question_type": "judge", "question_count": 10, "score_per": 1, "sort_no": 2},
            ],
        }),
        "建完整卷",
    )
    out["full"] = exam["id"]

    # 自动组卷（用内联规则；case 题库里不足，故意留着不给它）
    comp = must(
        call("POST", f"/admin/exams/{exam['id']}/auto-compose", tok, {
            "rules": [
                {"type": "single", "count": 20, "score": 1},
                {"type": "multiple", "count": 10, "score": 2},
                {"type": "judge", "count": 10, "score": 1},
            ],
            "seed": 20260918,
        }),
        "组卷完整卷",
    )
    print(f"完整卷组卷：{comp['question_count']} 题 / {comp['total_score']} 分，缺口 {len(comp['shortfalls'])} 条")

    # ---------------------------------------------------------------- 2) 缺口卷（要 100 道案例题）
    gap = must(
        call("POST", "/admin/exams", tok, {
            "subject_id": SUBJECT_ID,
            "title": f"E2E 题库不足卷 {tag}",
            "type": "custom",
            "duration_min": 120,
            "pass_score": 0,
            "is_free": False,
            "sections": [
                {"name": "一、案例题", "question_type": "case", "question_count": 100, "score_per": 2, "sort_no": 0},
                {"name": "二、单项选择题", "question_type": "single", "question_count": 30, "score_per": 1, "sort_no": 1},
            ],
        }),
        "建缺口卷",
    )
    out["gap"] = gap["id"]
    comp2 = must(
        call("POST", f"/admin/exams/{gap['id']}/auto-compose", tok, {
            "rules": [
                {"type": "case", "count": 100, "score": 2},
                {"type": "single", "count": 30, "score": 1},
            ],
            "seed": 7,
        }),
        "组卷缺口卷",
    )
    print(f"缺口卷组卷：{comp2['question_count']} 题，缺口 {len(comp2['shortfalls'])} 条")

    # ---------------------------------------------------------------- 3) 已发布 + 有版本漂移的卷
    pub = must(
        call("POST", "/admin/exams", tok, {
            "subject_id": SUBJECT_ID,
            "title": f"E2E 已发布卷（含版本漂移）{tag}",
            "type": "real",
            "exam_year": 2025,
            "duration_min": 180,
            "pass_score": 60,
            "is_free": False,
            "sections": [
                {"name": "一、判断题", "question_type": "judge", "question_count": 5, "score_per": 1, "sort_no": 0},
            ],
        }),
        "建已发布卷",
    )
    out["published"] = pub["id"]
    must(call("POST", f"/admin/exams/{pub['id']}/auto-compose", tok,
              {"rules": [{"type": "judge", "count": 5, "score": 1}], "seed": 42}), "组卷已发布卷")
    must(call("POST", f"/admin/exams/{pub['id']}/publish", tok, {"allow_edit_after_publish": False}), "发布")

    # 发布后改一道题 → 制造 version_drift
    detail = must(call("GET", f"/admin/exams/{pub['id']}", tok), "取已发布卷详情")
    first_q = detail["sections"][0]["questions"][0]["question_id"]
    q = must(call("GET", f"/admin/questions/{first_q}", tok), "取题目详情")
    must(
        call("PUT", f"/admin/questions/{first_q}", tok, {
            "version": q["version"],
            "stem": (q["stem"] + "（E2E：发布后被修改过）")[:400],
        }),
        "改题制造漂移",
    )
    print(f"已发布卷 {pub['id']} 已制造版本漂移（题 {first_q}）")

    # ---------------------------------------------------------------- 4) 已归档的卷
    arch = must(
        call("POST", "/admin/exams", tok, {
            "subject_id": SUBJECT_ID,
            "title": f"E2E 待恢复卷 {tag}",
            "type": "chapter_test",
            "duration_min": 60,
            "pass_score": 20,
            "is_free": True,
            "sections": [
                {"name": "一、单项选择题", "question_type": "single", "question_count": 10, "score_per": 1, "sort_no": 0},
            ],
        }),
        "建归档用卷",
    )
    out["archived"] = arch["id"]
    must(call("POST", f"/admin/exams/{arch['id']}/auto-compose", tok,
              {"rules": [{"type": "single", "count": 10, "score": 1}], "seed": 99}), "组卷归档用卷")
    must(call("POST", f"/admin/exams/{arch['id']}/publish", tok, {"allow_edit_after_publish": False}), "发布归档用卷")
    must(call("DELETE", f"/admin/exams/{arch['id']}?reason=E2E%20%E5%BD%92%E6%A1%A3", tok), "归档")
    print(f"已归档卷 {arch['id']}")

    # ---------------------------------------------------------------- 5) viewer 账号
    # 注册要过短信验证码；SMS_PROVIDER=mock 时 dev_code 会直接回在响应里。
    phone = "13900000077"
    code = must(call("POST", "/auth/sms/send", body={"phone": phone, "scene": "register"}),
                "发验证码").get("dev_code")
    if not code:
        raise SystemExit("❌ dev_code 为空：请确认 SMS_PROVIDER=mock 且 APP_ENV != prod")
    reg = call("POST", "/auth/register", body={
        "phone": phone, "code": code, "password": "Viewer@123456", "nickname": "E2E 只读岗",
    })
    if reg.get("code") == 0:
        uid = reg["data"]["user"]["id"]
    else:
        raise SystemExit(f"❌ 注册 viewer 失败：{reg}")
    must(call("PUT", f"/admin/users/{uid}/roles", tok, {"role_codes": ["viewer"], "scope_type": "global"}),
         "授予 viewer")
    print(f"viewer 账号：{phone} / Viewer@123456（uid={uid}）")

    print("\n===== E2E 数据就绪 =====")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
