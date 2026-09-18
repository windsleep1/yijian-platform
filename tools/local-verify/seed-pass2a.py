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
    #
    # ⚠️ 这里**必须自己造一道题**来制造漂移，绝不能改种子题库里的题。
    # 踩过一次：早先的写法是"从卷面里挑第一道题，把题干加个后缀" ——
    # 看着无害，但导入管道的去重是**内容指纹**（`questions.content_hash`，
    # 见 question_service.content_hash），改题干会让那道题**不再等于种子里那一行**。
    # 后果是 `test_admin_v5.py::test_seed_bank_mapping_matches_existing_rows` 失败：
    # 它断言"重新导入 300 行种子应当全是 duplicate"，而污染后的那行会被当成**新题**插入。
    #
    # 教训：**验收脚本不要改共享的基础数据**。要造特殊状态就自己造一条。
    drift_stem = f"【E2E 漂移演示 {tag}】下列关于施工组织设计的说法，正确的是？"
    own_q = must(
        call("POST", "/admin/questions", tok, {
            "subject_id": SUBJECT_ID, "type": "judge", "stem": drift_stem,
            "analysis": "E2E 专用题（可安全删除）", "difficulty": 3,
            "score_default": 1, "status": "published", "source_type": "self",
        }),
        "造漂移用题",
    )
    own_qid = own_q["id"]
    print(f"已造 E2E 专用题 {own_qid}（用于演示版本漂移，不动种子题库）")

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
                {"name": "一、判断题", "question_type": "judge",
                 "question_count": 1, "score_per": 1, "sort_no": 0},
            ],
        }),
        "建已发布卷",
    )
    out["published"] = pub["id"]
    must(call("POST", f"/admin/exams/{pub['id']}/questions", tok, {"question_ids": [own_qid]}),
         "把漂移题加进卷面")
    must(call("POST", f"/admin/exams/{pub['id']}/publish", tok, {"allow_edit_after_publish": False}), "发布")

    # 发布后改这道**自己的**题 → 制造 version_drift（只影响这一条，不碰种子数据）
    q = must(call("GET", f"/admin/questions/{own_qid}", tok), "取题目详情")
    must(
        call("PUT", f"/admin/questions/{own_qid}", tok, {
            "version": q["version"],
            "stem": drift_stem.rstrip("？") + "（发布后被改过）？",
        }),
        "改题制造漂移",
    )
    print(f"已发布卷 {pub['id']} 已制造版本漂移（E2E 专用题 {own_qid}）")

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
