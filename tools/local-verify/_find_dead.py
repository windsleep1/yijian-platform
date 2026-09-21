# -*- coding: utf-8 -*-
"""B 类（死代码）探测：一个函数"死"了，判据是**没有任何地方引用它**。

这不该靠人眼看。做法是把 `app/` 下所有文件的 AST 过一遍：
  · 收集**定义**（函数 / 方法）
  · 收集**引用**（`Name` 与 `Attribute` 的出现）
然后比对。分开统计 `app/` 内与 `tests/` 内的引用数 —— 因为有两种不同情况：
  · 两边都为 0        → 真死代码（B 类）
  · 只有 tests 引用   → 测试专用 helper（不是死代码，但也不该算进产品覆盖率口径）

排除项（这些"0 引用"是正常的）：
  · FastAPI 路由处理器（由装饰器接线，不靠名字引用）
  · dunder / pydantic 钩子
  · 名字以 `test_` 开头（测试文件里的）
"""
from __future__ import annotations

import ast
import io
import os
from collections import defaultdict

REPO = r"C:/My Protect/WorkBuddy/ONE Build/yijian-platform"
APP = os.path.join(REPO, "apps", "api", "app")
TESTS = os.path.join(REPO, "apps", "api", "tests")

FRAMEWORK_HOOKS = {
    "__call__", "__enter__", "__exit__", "__repr__", "__str__", "__eq__",
    "__hash__", "__iter__", "__len__", "__getitem__", "__setitem__",
    "model_config", "model_post_init",
    # Starlette 的 BaseHTTPMiddleware 通过 `self.dispatch` 动态调用，不靠名字引用
    "dispatch",
}
ROUTE_DECORATORS = (".get(", ".post(", ".put(", ".patch(", ".delete(", "route(")
# 这些装饰器把函数"接线"给框架：pydantic 校验器、异常处理器、中间件、property…
# 它们的共同点是**调用方按名字/属性取用，不出现普通的引用表达式**，
# 所以"没有任何引用"对它们不成立。
WIRING_DECORATORS = (
    "validator", "model_validator", "field_validator", "root_validator",
    "computed_field", "property", "staticmethod", "classmethod", "abstractmethod",
    "exception_handler", "middleware", "on_event", "lru_cache",
    "contextmanager", "asynccontextmanager", "singledispatch", "overload",
)


def pyfiles(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".pytest_cache"}]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def parse(path: str) -> ast.Module | None:
    try:
        return ast.parse(io.open(path, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError):
        return None


def scan(root: str):
    """返回 (定义列表[(name,path,lineno)], 引用计数, 路由处理器位置集合, 装饰器接线位置集合)。"""
    defs: list[tuple[str, str, int]] = []
    refs: dict[str, int] = defaultdict(int)
    routes: set[tuple[str, int]] = set()
    wired: set[tuple[str, int]] = set()
    for path in pyfiles(root):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append((node.name, path, node.lineno))
                for dec in node.decorator_list:
                    text = ast.unparse(dec)
                    if any(k in text for k in ROUTE_DECORATORS):
                        routes.add((path, node.lineno))
                    if any(k in text for k in WIRING_DECORATORS):
                        wired.add((path, node.lineno))
            elif isinstance(node, ast.Name):
                refs[node.id] += 1
            elif isinstance(node, ast.Attribute):
                refs[node.attr] += 1
    return defs, refs, routes, wired


def def_counts(defs) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for name, _p, _l in defs:
        c[name] += 1
    return c


def main() -> int:
    app_defs, app_refs, routes, wired = scan(APP)
    test_defs, test_refs, _, _ = scan(TESTS)

    dead, only_tests = [], []
    for name, path, lineno in app_defs:
        if name.startswith("__") or name in FRAMEWORK_HOOKS or name.startswith("test_"):
            continue
        if (path, lineno) in routes or (path, lineno) in wired:
            continue
        # ⚠️ **不要减"定义次数"**：`def foo` 里的 foo 是普通字符串字段，
        #    不是 AST 的 Name 节点 —— 它**根本不会进 refs**。
        #    一开始我按"定义也算一次绑定"去减，结果把唯一的调用点减掉了，
        #    满屏都是"死代码"（踩过一次，见坑 54 的附带教训）。
        n_app = app_refs.get(name, 0)
        n_test = test_refs.get(name, 0)
        rel = os.path.relpath(path, os.path.join(REPO, "apps", "api")).replace("\\", "/")
        if n_app == 0 and n_test == 0:
            dead.append((rel, lineno, name))
        elif n_app == 0 and n_test > 0:
            only_tests.append((rel, lineno, name, n_test))

    print(f"app/ 定义总数: {len(app_defs)}   （其中路由处理器 {len(routes)} 个已排除）")
    print()
    print("=" * 72)
    print(f"B 类候选（app/ 与 tests/ 都无引用）= {len(dead)}")
    print("=" * 72)
    for rel, ln, name in sorted(dead):
        print(f"  {rel}:{ln}  {name}")
    print()
    print("=" * 72)
    print(f"仅 tests 引用（测试专用 helper，非死代码）= {len(only_tests)}")
    print("=" * 72)
    for rel, ln, name, n in sorted(only_tests):
        print(f"  {rel}:{ln}  {name}   (tests 引用 {n} 次)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
