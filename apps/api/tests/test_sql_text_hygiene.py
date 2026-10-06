"""SQL 卫生：`text()` 里的**注释**不许提到"不存在的绑定参数"。

## 这条判据是拿 50001 换来的（坑 103）

SQLAlchemy 的 `text()` **不剥离 SQL 注释** —— 它在**整段字符串**里扫 `:name`，
所以下面写在 `--` 注释里的东西**照样**会被登记成一个绑定参数：

    text("SELECT a FROM t WHERE id = :sid  -- 行内列，不是 :uid")
    #  → _bindparams = ['sid', 'uid']  ← 多出来一个

执行时只传 `{"sid": ...}` ⇒ **缺参数报错 ⇒ 50001**。
★ 而它的**症状指向完全错的方向**：这条 SQL 看起来完全正常，报错也只是"服务内部错误"；
  实测里我先怀疑建表顺序、再怀疑绑定参数个数，最后才想到注释。

## 为什么不做成"剥掉注释再扫"的运行时改写

那样会改变 `text()` 的既有行为（它按**原样**建绑定参数），修在一个地方、漏在别处。
⇒ 改成**门禁**：写的时候就不允许，而不是运行时替它擦屁股
（同族：坑 100「别靠记得改 N 处，写成机械检查」）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

#: `:name` —— SQLAlchemy 就是这么认绑定参数的（后面不接冒号）。
_COLON_NAME = re.compile(r":([A-Za-z_]\w*)")


def _offenders(path: Path) -> list[tuple[int, list[str]]]:
    src = path.read_text(encoding="utf-8")
    out: list[tuple[int, list[str]]] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "text"):
            continue
        seg = ast.get_source_segment(src, node)
        if not seg:  # pragma: no cover —— 只在源码不可定位时发生（例如 exec 出来的 AST）
            continue
        in_comment: set[str] = set()
        in_code: set[str] = set()
        for line in seg.splitlines():
            # 行内 `--` 之后都算注释；`--` 之前算代码（SQL 字面量里不会出现 `--`）
            body, _, inline = line.partition("--")
            in_code |= set(_COLON_NAME.findall(body))
            in_comment |= set(_COLON_NAME.findall(inline))
        ghost = sorted(in_comment - in_code)
        if ghost:
            out.append((node.lineno, ghost))
    return out


def test_no_ghost_bindparam_in_sql_comments() -> None:
    """★★ `text()` 的注释里不许出现"只存在于注释里"的绑定参数（坑 103）。

    ★ **可证伪**：把一个真参数名（`sid`）写进注释**不算违规**（它就是真参数，
      多扫到一次不影响执行）—— 这条规则只管"**凭空多出来**的那个"。
      所以判据是 `注释里的名字 − 代码里的名字 ≠ ∅`。
    """
    problems: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for line, ghost in _offenders(path):
            problems.append(
                f"{path.relative_to(APP.parent)}:{line} 注释里的 {ghost} 会被当成绑定参数"
            )
    assert not problems, (
        "`text()` 不剥离注释 ⇒ 注释里的「冒号+名字」会变成真绑定参数，"
        "执行时缺参数 ⇒ **50001**，而 SQL 看起来完全正常（坑 103）。\n  " + "\n  ".join(problems)
    )


def test_the_check_itself_can_go_red(tmp_path: Path) -> None:
    """★ 判据要能被证伪（硬约定 J）：喂一段**故意写坏**的 SQL，它必须报红。

    ★ 对照两格：① 注释里有假参数 ⇒ 报；② 注释里只有真参数 ⇒ **不报**。
      只验 ① 的话，"凡注释里有冒号就报"的实现也能过 —— 那会把下一次正常的注释弄红。
    """
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from sqlalchemy import text\n"
        'Q = text("SELECT a FROM t WHERE id = :sid  -- 行内列，不是 :uid\\n")\n',
        encoding="utf-8",
    )
    assert _offenders(bad) == [(2, ["uid"])], _offenders(bad)

    ok = tmp_path / "ok.py"
    ok.write_text(
        "from sqlalchemy import text\n"
        'Q = text("SELECT a FROM t WHERE id = :sid  -- 就是 :sid 这个参数\\n")\n',
        encoding="utf-8",
    )
    assert _offenders(ok) == [], f"注释里提到**真参数**不该报红：{_offenders(ok)}"
