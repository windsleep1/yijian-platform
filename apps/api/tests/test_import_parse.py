"""导入管道 · 文件解析容错（⑤c-1 / `docs/19` §3.1）。

**为什么只测 `parse_file` 这一个入口**（`docs/19` §7 的判据）：
`_decode_text` / `_parse_csv` / `_parse_json` 是**私有实现** —— 直接测它们会在
"重命名 / 内联 / 拆分"时变红，那是"重构实现但不改契约"的假红。
它们的每个分支都由本文件的用例**间接覆盖**，破坏任一条契约用例都会红。

`parse_file(content, file_type) -> list[dict]` 的契约：
  - 合法 csv / json → 行字典列表
  - 违反任一条 → `BizError(40001)`，消息里说清原因

覆盖的缺口行（`docs/19` §3.1 全部 15 条）：
  111 / 119 / 126 / 127 / 128 / 135 / 148 / 149 / 151 / 152 / 153 / 154 / 156 / 158 / 161
"""

import json

import pytest

from app.core.errors import BizError
from app.services.import_service import MAX_FILE_BYTES, parse_file

CSV_OK = "subject_code,type,stem,answer,analysis,difficulty\nSW,single,题干内容, A,解析内容够长,3\n"


# ============================================================ 规模与类型


def test_oversize_file_is_rejected() -> None:
    """超过 20MB 直接拒绝 —— 不是"截断"，是明确报错（覆盖 111）。"""
    content = b"a" * (MAX_FILE_BYTES + 1)
    with pytest.raises(BizError) as ei:
        parse_file(content, "csv")
    assert ei.value.code == 40001
    assert "过大" in ei.value.message


def test_unknown_file_type_is_rejected() -> None:
    """只认 csv / json —— 别的类型不该被"猜着解析"（覆盖 119）。"""
    with pytest.raises(BizError) as ei:
        parse_file(b"a,b\n1,2\n", "xlsx")
    assert ei.value.code == 40001
    assert "不支持的文件类型" in ei.value.message


# ============================================================ 编码


def test_gbk_csv_falls_back_and_decodes() -> None:
    """Excel 存 CSV 常是 GBK —— 要能回退解码，而不是报"文件编码无法识别"（覆盖 126 / 127）。

    注意两点：
    ① 内容必须是**非 ASCII**，否则 UTF-8 就能解，回退分支根本不走；
    ② 表头仍要**完整**，否则会在解码成功之后被"缺必需列"拦下（那是另一条契约）。
    """
    body = (
        "subject_code,type,stem,answer,analysis,difficulty\n"
        "SW,single,中文题干内容,A,解析内容够长,3\n"
    ).encode("gb18030")
    rows = parse_file(body, "csv")
    assert rows[0]["stem"] == "中文题干内容"


def test_undecodable_bytes_are_rejected() -> None:
    """三种编码都解不动时明确报错，而不是塞进一个乱码字符串（覆盖 128）。

    用 `0xFF`：它在 UTF-8 里是非法首字节，在 gb18030 里也**不在合法首字节范围
    （0x81–0xFE）**，所以三种编码都会失败 —— 这正是这条分支要的场景。
    """
    with pytest.raises(BizError) as ei:
        parse_file(b"\xff\xff\xff\xff", "csv")
    assert ei.value.code == 40001
    assert "编码无法识别" in ei.value.message


# ============================================================ CSV 结构


def test_empty_csv_is_rejected() -> None:
    """空文件连表头都没有 → 明确拒绝（覆盖 135）。

    这条与 `create_batch` 的"文件里没有数据行"是**两个不同的错误**：
    空文件在这里就被拦下（无表头），"只有表头没有数据行"才走到那一条。
    """
    with pytest.raises(BizError) as ei:
        parse_file(b"", "csv")
    assert ei.value.code == 40001
    assert "表头" in ei.value.message


def test_csv_missing_required_column_is_rejected() -> None:
    """必需列缺失要说清缺了哪几列（对照：这条一直是覆盖的，留作正向对照）。"""
    with pytest.raises(BizError) as ei:
        parse_file("subject_code,type\nSW,single\n".encode(), "csv")
    assert "缺少必需列" in ei.value.message


# ============================================================ JSON 形态


def test_broken_json_is_rejected_with_reason() -> None:
    """JSON 语法错误要带上原因与行号，不能只说"解析失败"（覆盖 148 / 149）。"""
    with pytest.raises(BizError) as ei:
        parse_file(b'{"questions": [', "json")
    assert ei.value.code == 40001
    assert "JSON 解析失败" in ei.value.message


def test_json_wrapped_in_questions_key_is_unwrapped() -> None:
    """`{"questions": [...]}` 要能被拆出来 —— docstring 承诺支持的形态（覆盖 151–154）。

    ⚠️ 在此之前**从未被验证过**：现有用例只上传过**裸数组**。
    而带包装的形态是导出工具的常见输出。
    """
    body = json.dumps({"questions": [{"subject_code": "SW"}, {"subject_code": "SW"}]}).encode()
    rows = parse_file(body, "json")
    assert rows == [{"subject_code": "SW"}, {"subject_code": "SW"}]


@pytest.mark.parametrize("key", ["items", "rows", "data"])
def test_json_other_wrapper_keys_are_also_accepted(key: str) -> None:
    """四个包装键是并列的（`questions` / `items` / `rows` / `data`），都要认。"""
    body = json.dumps({key: [{"subject_code": "SW"}]}).encode()
    assert parse_file(body, "json") == [{"subject_code": "SW"}]


def test_json_dict_without_wrapper_key_becomes_single_row() -> None:
    """顶层是 dict 但**没有**已知包装键 → 当成"一行"（覆盖 156）。

    这是刻意的宽容：单题导出成对象是合理的，不该逼用户去套一层数组。
    """
    body = json.dumps({"subject_code": "SW", "stem": "题干"}).encode()
    rows = parse_file(body, "json")
    assert rows == [{"subject_code": "SW", "stem": "题干"}]


def test_json_scalar_toplevel_is_rejected() -> None:
    """顶层是字符串/数字 → 拒绝（覆盖 158）——"猜不出它想表达什么行"。"""
    with pytest.raises(BizError) as ei:
        parse_file(b'"just a string"', "json")
    assert ei.value.code == 40001
    assert "顶层必须是数组" in ei.value.message


def test_json_array_of_scalars_is_rejected() -> None:
    """数组元素不是对象 → 拒绝（覆盖 161），而不是让它在后面几百行处才炸。"""
    with pytest.raises(BizError) as ei:
        parse_file(b"[1, 2]", "json")
    assert ei.value.code == 40001
    assert "每一项都必须是对象" in ei.value.message


# ============================================================ 正向对照


def test_valid_csv_and_json_both_parse() -> None:
    """正向对照：合法输入要能正常解析。

    没有它，上面那些"报错了"可能是**因为通道整个坏了**（假绿的反面）——
    ⑤a 的教训：每个"拒绝"都要配一条"接受"。
    """
    csv_rows = parse_file(CSV_OK.encode(), "csv")
    assert len(csv_rows) == 1
    assert csv_rows[0]["subject_code"] == "SW"

    json_rows = parse_file(json.dumps([{"subject_code": "SW"}]).encode(), "json")
    assert json_rows == [{"subject_code": "SW"}]
