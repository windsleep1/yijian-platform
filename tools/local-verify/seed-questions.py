#!/usr/bin/env python
"""题库种子：生成 + 灌库 —— **CI 与 `run-smoke.ps1` 共用同一套逻辑**。

    python tools/local-verify/seed-questions.py --pg-port 55432 --db yijian --db-user yijian
    python tools/local-verify/seed-questions.py --pg-port 5432            # CI：psql 在 PATH 上

## 为什么单独抽一个脚本，而不是在两处各写两行命令

**坑 51**：`run-smoke.ps1` 原先**没有灌题库这一步**，本地一直绿只是因为开发机的库
早先被手工灌过种子题；搬到全新库上立刻 **32 failed**（`test_admin_v7.py` 的组卷 /
加题 / 知识点下拉无题可抽）。

修它的时候，**最容易犯的错是在 CI 与本地各写一套** —— 那两套迟早漂，
于是又回到"本地绿、CI 红"的口径分歧。所以生成与灌库**只有这一份实现**，两边都调它。

## ⚠️ 为什么这个脚本的**打印全部是 ASCII**（注释和文档照旧中文）

实测（2026-09-20）：Windows 下本脚本的中文输出在 `run-smoke.ps1` 的日志里是乱码，
形如 `[seed] 鐏屽叆 yijian@...` —— UTF-8 的字节被按代码页 936 解码。

**原因是链路上三方对编码的假设不一致**：Python 子进程按 UTF-8 写（本机系统 ACP 是
UTF-8），`run-smoke.ps1` 自己按 936 写，而**再外面一层调用 `run-smoke.ps1` 的宿主**
（这里是 WorkBuddy 的工具进程）按 936 解码。最外层那个我改不了。

试过的错路：把子进程和 PS 都改成 UTF-8（`PYTHONIOENCODING` +
`[Console]::OutputEncoding`）—— 结果**更糟**：连 PowerShell 自己的中文也一起变乱码，
因为最外层仍按 936 解码。

**结论：脚本要跨宿主打印，就别依赖任何一方的默认代码页 —— 用 ASCII 输出。**
中文留在注释/文档里（那些是按 UTF-8 读源码，与 console 编码无关）。

## 为什么不用 `asyncpg` 直接跑 SQL 文件（明明它已经是依赖）

`data/seed/questions.sql` 是 **12 MB** 的裸 SQL。用 psql 的 `-f` 是**流式**读的，
实测灌完约 5s；而把它整个读进内存交给 asyncpg 的 `execute()`（多语句走简单查询协议）
要一次性持有 12 MB 字符串，且失败时的报错定位差得多。既然两个环境都有 psql
（CI 用 ubuntu 预装的 `postgresql-client`；本机用 `...\\pg16\\Library\\bin\\psql.exe`，
`run-smoke.ps1` 已经知道这个路径），就用它。`--psql` 允许显式指定二进制。

## 幂等

`questions.sql` 自带 `ON CONFLICT`，**无条件重跑**即可（约 5s）。
本脚本**刻意不做"库里已有 6000 题就跳过"的快捷判断** —— 那正是坑 51 的成因：
"环境恰好脏"时跳过 → 行为与干净环境不同 → 又变成假绿。
要么自己造出来，要么别装作验过（硬约定 H）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 仓库根 = tools/local-verify/../../  （本文件在 tools/local-verify/）
REPO = Path(__file__).resolve().parents[2]


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    """跑一条命令；非 0 退出直接抛，并把输出一起带出去（便于 CI 里定位）。"""
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        print(f"[seed] command failed (exit={proc.returncode}): {' '.join(cmd)}", file=sys.stderr)
        if proc.stdout.strip():
            print(f"[seed] ---- stdout ----\n{proc.stdout.strip()}", file=sys.stderr)
        if proc.stderr.strip():
            print(f"[seed] ---- stderr ----\n{proc.stderr.strip()}", file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def scalar(psql: list[str], dsn_args: list[str], sql: str) -> str:
    """`psql -tAc` 取一个标量。"""
    return run(psql + dsn_args + ["-tAc", sql]).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="seed the question bank (generate + load)")
    ap.add_argument("--pg-host", default=os.environ.get("PGHOST", "127.0.0.1"))
    ap.add_argument("--pg-port", default=os.environ.get("PGPORT", "55432"))
    ap.add_argument("--db", default="yijian")
    ap.add_argument("--db-user", default="yijian")
    ap.add_argument("--psql", default="psql", help="path to psql binary (default: from PATH)")
    ap.add_argument("--count", type=int, default=6000, help="target question count")
    # ⚠️ 默认**必须**含 json，不能写成只出 sql（2026-09-22 改，坑 55）：
    #    `apps/api/tests/test_admin_v5.py` 的「验收⑤ 无损证明」把
    #    `data/seed/questions.json` 当输入。此前这里写死 `--format sql`，
    #    而 `data/` 是 gitignored —— CI 全新环境**没有**该文件 → 那条用例
    #    **静默 skip**；本地只因为有一份 9-15 的**遗留物**才跑得起来。
    #    同一个用例在两个环境行为不同，而且失败方向是"看不见"（skip 不是 fail）。
    ap.add_argument(
        "--format",
        default="sql,json",
        help="输出格式（逗号分隔，透传给 db/seed/gen_seed_questions.py）。"
        "默认 sql,json —— json 是导入管道幂等性用例的输入，**不能省**",
    )
    ap.add_argument("--skip-generate", action="store_true",
                    help="reuse an existing data/seed/questions.sql (debug only, not for acceptance)")
    args = ap.parse_args()

    out_dir = REPO / "data" / "seed"
    sql_file = out_dir / "questions.sql"

    # psql 的参数形式：多一个 argv 元素比拼字符串安全（Windows 下路径带空格也能过）
    psql = [args.psql]
    dsn_args = ["-h", args.pg_host, "-p", str(args.pg_port), "-U", args.db_user, "-d", args.db]
    # 客户端编码统一 UTF8：题库里有中文，Windows 控制台默认 GBK 会让 psql 报编码错
    env = {**os.environ, "PGCLIENTENCODING": "UTF8"}

    # ---- 1. 生成（自包含、离线、seed 固定 → 可复现）----
    if args.skip_generate and sql_file.exists():
        print(f"[seed] reuse {sql_file.relative_to(REPO)} (--skip-generate)")
    else:
        print(f"[seed] generating question bank (count={args.count}) ...")
        gen = REPO / "db" / "seed" / "gen_seed_questions.py"
        if not gen.exists():
            print(f"[seed] generator not found: {gen}", file=sys.stderr)
            return 1
        out = run(
            [sys.executable, str(gen), "--format", args.format,
             "--out", str(out_dir), "--count", str(args.count)],
            cwd=gen.parent, env=env,
        )
        # 生成器自己打印的统计是中文 → 会乱码；这里只从 manifest 读数字，用 ASCII 输出。
        # （manifest 是 JSON，键是 ASCII，值里的中文不打印。）
        import json  # noqa: PLC0415  —— 只在这里用一次，放顶部没必要

        manifest = out_dir / "import_manifest.json"
        if manifest.exists():
            try:
                totals = json.loads(manifest.read_text(encoding="utf-8"))["totals"]
                print(f"[seed]   generated: questions={totals.get('questions')} "
                      f"options={totals.get('options')}")
            except Exception:  # noqa: BLE001 —— 只是摘要，读不到不影响主流程
                pass

    if not sql_file.exists():
        print(f"[seed] generation produced nothing at {sql_file}", file=sys.stderr)
        return 1
    size_mb = sql_file.stat().st_size / 1024 / 1024
    print(f"[seed] {sql_file.relative_to(REPO)}  {size_mb:.1f} MB")

    # ---- 2. 灌库（幂等 ON CONFLICT；无条件重跑，不做"脏库就跳过"）----
    print(f"[seed] loading into {args.db}@{args.pg_host}:{args.pg_port} ...")
    run(psql + dsn_args + ["-v", "ON_ERROR_STOP=1", "-q", "-f", str(sql_file)], env=env)

    # ---- 3. 自查（灌了没生效必须立刻炸，别等到 pytest 才 32 条红）----
    q = int(scalar(psql, dsn_args, "SELECT count(*) FROM questions"))
    kp = int(scalar(psql, dsn_args, "SELECT count(*) FROM knowledge_points"))
    print(f"[seed] verify: questions={q}  knowledge_points={kp}")
    if q < args.count:
        print(f"[seed] questions={q} < target {args.count}: seed not fully loaded",
              file=sys.stderr)
        return 1
    if kp <= 0:
        # 知识点为空时，`/admin/chapters/knowledge-points` 与组卷的知识点筛选全都会失败
        print("[seed] knowledge_points is empty: seed not fully loaded", file=sys.stderr)
        return 1
    print("[seed] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
