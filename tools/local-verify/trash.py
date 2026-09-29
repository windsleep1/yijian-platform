"""唯一回收站入口 —— **应用不删，只 rename**（硬约定 R，方案 `docs/25`）。

## 为什么要有这个模块，而不是各处自己写 `p.rename(...)`

1. **一个名字只有一个来源**：`.trash/<kind>/` 这套布局被 5 个脚本共用。各写一份必然漂移，
   而漂移的后果是**某个角落里的旧产物没有任何清理机制能看到**（它不占门禁、只占磁盘，
   所以不会报错 —— 只会慢慢把盘填满）。
2. **它让"正确做法"比"直接删"更省事**。硬约定 R 的物理约束（不变量 6）**只拦错的做法**，
   那是下限；**让正解更好走**才是上限 —— 一个函数调用换掉一行 `rmtree` 就是上限。

## 机制（一句话）

    trash(path, kind="coverage")     # → <repo>/.trash/coverage/<name>[.N]

`rename` 是**元数据操作**：不触发宿主的删除保护、瞬时完成、同盘无数据拷贝。
真正的"删"由**宿主 / 人**去做（`preflight.sh` 只**报告**，见 R 里那条区分）。

## CLI

    python tools/local-verify/trash.py --report          # 各 kind 的条目数与占用
    python tools/local-verify/trash.py --report --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: 回收站目录名。**只有这一处写它** —— 其它脚本一律 import 这个常量。
TRASH_DIRNAME = ".trash"

#: 允许的 kind（= `.trash/` 下的子目录）。故意**做成白名单**：
#: 随手传一个新 kind 会在回收站里长出一个"没人知道该不该清"的目录。
KINDS: tuple[str, ...] = ("next", "coverage", "chrome", "misc")

TRASH_OK_MARK = "trash-ok:"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def trash_dir(kind: str, root: Path | None = None) -> Path:
    if kind not in KINDS:
        raise ValueError(f"未知的 kind = {kind!r}，允许的：{KINDS}")
    return (root or repo_root()) / TRASH_DIRNAME / kind


def trash(path: Path | str, kind: str = "misc", root: Path | None = None) -> Path | None:
    """把 `path` **改名挪进**回收站，返回落点；`path` 不存在则返回 None。

    ⚠️ **绝不删除**。挪不动（跨盘 / 被占用）时**抛 `OSError`** ——
    不"退化成删除"，也不静默吞掉：调用方要能看见"这次没挪成"。
    那些"清理失败不该影响主流程"的调用点，请自己 `try/except` 并**说明**为什么可以忽略。
    """
    p = Path(path)
    if not p.exists():
        return None
    target_dir = trash_dir(kind, root)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / p.name
    n = 1
    while target.exists():  # 同名的历史残留：加后缀，**不覆盖**
        target = target_dir / f"{p.name}.{n}"
        n += 1
    p.rename(target)
    return target


def _tree_size(p: Path) -> tuple[int, int]:
    """(条目数, 字节数)；目录递归，文件算 1 项。"""
    if p.is_file():
        try:
            return 1, p.stat().st_size
        except OSError:
            return 1, 0
    items = 0
    size = 0
    for f in p.rglob("*"):
        if f.is_file():
            items += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return items, size


def report(root: Path | None = None) -> dict[str, dict[str, int | bool]]:
    """各 kind 的 `{entries, files, bytes}`。条目 = `.trash/<kind>/` 下的一级子项。"""
    base = (root or repo_root()) / TRASH_DIRNAME
    out: dict[str, dict[str, int | bool]] = {}
    for kind in KINDS:
        d = base / kind
        if not d.is_dir():
            continue
        files = 0
        size = 0
        entries = 0
        for entry in d.iterdir():
            entries += 1
            n, s = _tree_size(entry)
            files += n
            size += s
        out[kind] = {"entries": entries, "files": files, "bytes": size, "is_dir": True}
    return out


def human(n: int) -> str:
    unit = "B"
    for u in ("KB", "MB", "GB"):
        if n < 1024:
            break
        n //= 1024
        unit = u
    return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("## CLI")[0])
    ap.add_argument("--report", action="store_true", help="报 .trash/ 的占用")
    ap.add_argument("--json", action="store_true", help="机器可读")
    args = ap.parse_args(argv)

    if not args.report:
        ap.print_help()
        return 2

    data = report()
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return 0

    root = repo_root() / TRASH_DIRNAME
    if not data:
        print(f"[trash] {TRASH_DIRNAME}/ 是空的（没有待回收的东西）")
        return 0
    total_b = total_e = 0
    for kind, v in data.items():
        total_b += int(v["bytes"])
        total_e += int(v["entries"])
        print(
            f"[trash] {TRASH_DIRNAME}/{kind}/：{v['entries']} 个条目 / "
            f"{v['files']} 个文件 / {human(int(v['bytes']))}"
        )
    print(f"[trash] 合计 {total_e} 个条目 / {human(total_b)} —— 位置：{root}")
    print(
        "[trash] ⚠️ 本工具**不删**（宿主的删除保护会拦批量删除，硬约定 R）。"
        "要真回收，在你自己的终端里跑：\n"
        f"        rm -rf '{root}'"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
