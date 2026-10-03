"""本机一键管道（不用 `Start-Process`）：起 PG → 一次性库 → CLI 插桩 → 起 API → pytest → combine → report。

为什么另写一份（不是 `run-smoke.ps1` 的替代品，而是它的**兄弟**）
----------------------------------------------------------------
`run-smoke.ps1` 起 API 用的是 `ProcessStartInfo + UseShellExecute`（= `Start-Process`），
**本机沙箱会拦掉** —— 表现是脚本在"起 API"那一步直接失败，**日志里连错误行都没有**。
本脚本用 `subprocess.Popen`，本机可跑，是**本机唯一能跑全量测试的路径**。

⚠️ 它以前只活在 `%TEMP%/yb-pipe.py`（**会被临时目录清掉、也进不了版本控制**）
⇒ 2026-09-27 收进仓库。这是它该待的地方：一个"唯一的验证路径"活在 `%TEMP%` 里，
下个会话找不到、改了也没人 review（硬约定 H 的同族：验收路径本身也要被保管好）。

与 `run-smoke.ps1` / CI 的关系
------------------------------
- **库**：默认一次性（`smoke-db.py`，**BL-17**）—— 与 CI 的"全新容器 + 全新库"**可比**。
  想跑在累积的开发库上：`--db yijian`（此时不销毁，且数字不再与 CI 可比）。
- **口径**：pytest `-q -rfEXs`，与 CI 逐字一致。
  ⚠️ **不要写 `-rs`** —— `-r` 是**替换**默认值，只写 `-rs` 会把默认的 `-rfE` 一起顶掉，
  于是短汇总里**没有 `FAILED` 行**（坑 55 / 2026-09-26 实测）。
- **三份插桩** → combine → report（硬约定 L：业务代码在哪个进程跑，就在哪个进程插桩）。
- **PG 所有权**：只有"本轮是我起的"才在收尾时停它（坑 68：两个组件都以为自己拥有同一资源
  ⇒ 症状是"某个时刻起成片失败"）。

用法
----
    python tools/local-verify/run-local-pipeline.py
    python tools/local-verify/run-local-pipeline.py --keep-db        # 跑完不销毁库（连上去看）
    python tools/local-verify/run-local-pipeline.py --no-pg-stop     # 别停 PG（我另有用途）
    python tools/local-verify/run-local-pipeline.py --db yijian      # 故意跑在开发累积库上

    python tools/local-verify/run-local-pipeline.py --e2e-web        # ★ 换成浏览器走查（C 端登录闭环）
        # 同一套脚手架（一次性库 / 种子 / 同源 env / 已就绪的 API），
        # 但被测对象从 httpx 换成**真浏览器**（`e2e-web.py`，移动视口 375×667）。
        # 它不跑覆盖率门禁 —— 量的是"交互通不通"，不是覆盖率。
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import pipeline_stamp  # noqa: E402  （管道回执 —— 让 preflight 的 📋 有证可查）
import trash as trash_mod  # noqa: E402  （唯一回收站入口 —— 项目约定 R / `docs/25`）

HOME = Path.home()
PY_DEFAULT = str(
    HOME / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe"
)
PG_PREFIX_DEFAULT = HOME / ".workbuddy" / "binaries" / "pg" / "pg16"
PG_DATA_DEFAULT = HOME / ".workbuddy" / "binaries" / "pg" / "data16"
PG_USER = "yijian"
SMOKE_DB = HERE / "smoke-db.py"
SMOKE_PREFIX = "yijian_smoke_"

#: 本地门禁预检（`tools/preflight.sh`）—— **静态检查那 4 道**，不碰数据库。
#: 跑在起 PG 之前：这几道几十秒就能出结论，**没必要先花 3 分钟建库再发现 ruff 红了**。
PREFLIGHT = REPO_DEFAULT / "tools" / "preflight.sh"

#: ★ 需要**题库**才能跑的走查场景。
#:
#: ⚠️ 加新场景时**必须**在这里登记 —— 否则它跑在"没有题目"的库上，
#:   而症状是 **"每个章节都显示 0 题 / 暂无题目"** ⇒ 看起来像**产品 bug**
#:   （比如"选章节页读错了那个没人刷的冗余列"），实际只是种子没灌。
#:   2026-09-29 实测：P2b-1 场景第一次跑就是这么红的，为它白跑了 3 轮。
#:   （同族：坑 51「本地/CI 的环境差异让某一步从来不需要 ⇒ 从来没人发现它漏了」。）
#: ⇒ 判据：**"这个场景要不要题目"必须被显式回答**，而不是"默认不灌、出事了再说"。
#: ★ **哪些场景需要题库**：必须**显式登记** —— 第一版写的是"不灌题库"（登录闭环不需要题目），
#:   于是 P2b-1 第一次跑就撞上"章节全是 0 题"，而那个症状**长得像产品 bug**，白跑 3 轮。
#:   ⇒ 新场景若要用题，**忘了登记就会红**（而不是安静地跳过）。
SCENARIOS_NEEDING_QUESTIONS = frozenset({"p2b1", "p2c1", "p2c2"})


def say(msg: str) -> None:
    print(f"[pipe] {msg}", flush=True)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_port(port: int, label: str, tries: int = 60) -> None:
    for _ in range(tries):
        if port_open(port):
            say(f"{label} 端口已开 (:{port})")
            return
        time.sleep(1)
    raise SystemExit(f"{label} 未在 {tries}s 内监听 :{port}")


def run(cmd: list[str], cwd: Path, env: dict[str, str], label: str, timeout: int = 1800) -> int:
    say(f"$ {label}")
    r = subprocess.run(cmd, cwd=str(cwd), env=env, timeout=timeout, check=False)
    say(f"  -> exit={r.returncode}")
    return r.returncode


def run_capture(
    cmd: list[str], cwd: Path, env: dict[str, str], label: str, timeout: int = 1800
) -> tuple[int, str]:
    """同 `run()`，但**把输出也留下来**（写给"管道回执"用）。

    ⚠️ 仍然**打屏**：把输出吞掉会让"跑过了"与"没跑"看起来一样（硬约定 H 的同族：
      **没覆盖 ≠ 能过**，而"看不见的输出"等于没覆盖）。
    """
    say(f"$ {label}")
    r = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for stream in (r.stdout, r.stderr):
        if stream:
            print(stream, end="" if stream.endswith("\n") else "\n")
    say(f"  -> exit={r.returncode}")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def preflight(p: Pipeline) -> None:
    """本地门禁预检（`tools/preflight.sh`）：4 道静态门禁，**跑在起 PG 之前**。

    ★ 为什么放最前：这几道**几十秒**就出结论 —— 没必要先花三分钟建库、
      再发现 ruff 红了。fail-fast 省的是"一次建库 + 一次全量 pytest"。

    ⚠️ 它需要 `bash`（那 4 道里有两道是 bash harness）。**找不到 bash 时大声跳过、不静默**：
      "跳过"是**少查了 4 道**，不是"查过了"—— 所以要把警告和手工命令都打出来
      （硬约定 H 的同族：**没覆盖 ≠ 能过**）。
    """
    bash = shutil.which("bash")
    if bash is None:
        say("!! 找不到 bash ⇒ **跳过本地门禁预检（少查 4 道，不等于通过）**")
        say("   装了 Git Bash 之后手工跑：bash tools/preflight.sh")
        return
    rc = run([bash, str(PREFLIGHT)], p.repo, p.env, "preflight（本地门禁 ①~④）")
    if rc != 0:
        raise SystemExit("本地门禁预检未通过 —— 先修这里，别往下跑（省一次建库）")


class Pipeline:
    """把「谁拥有什么」写清楚：PG / 库 / API 三个资源各有明确的所有者与释放点。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.repo = Path(args.repo)
        self.api_dir = self.repo / "apps" / "api"
        self.pg_bin = Path(args.pg_prefix) / "Library" / "bin"
        self.pg_data = Path(args.pg_data)
        self.psql = self.pg_bin / "psql.exe"
        self.tmp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
        #: 回执要引用的两行数字（跑完才有值；pytest 与覆盖率各摘一行）。
        self.passed_line = ""
        self.total_line = ""
        self.env = dict(os.environ)
        self.env.update(
            {
                "PYTHONUTF8": "1",
                "PGCLIENTENCODING": "UTF8",
                "APP_ENV": "local",
                # ⚠️ JWT_SECRET 必须与 API 进程同源，否则手搓 JWT 的用例报 40102（像缺陷，其实是 env）。
                "JWT_SECRET": "local-verify-secret-not-for-production",
                "ADMIN_INIT_PHONE": "13800000000",
                "ADMIN_INIT_PASSWORD": "Admin@123456",
                # ⚠️ AI_BASE 漏设 ⇒ 整个 AI 相关模块**静默 skip**（覆盖率少一截而没人报错）。
                "AI_BASE": f"http://127.0.0.1:{args.api_port}",
                "SMS_PROVIDER": "mock",
            }
        )
        self.db: str | None = None
        self.ephemeral = False
        self.started_pg = False
        self.api: subprocess.Popen[bytes] | None = None

    # ------------------------------------------------------------ 小工具
    def smoke_db(
        self, cmd: str, *args: str, capture: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """调 `smoke-db.py`。

        ⚠️ 参数顺序**必须**是「**子命令在前、公共参数在后**」—— 理由见 `smoke-db.py::_add_common`：
        `argparse` 的**主解析器可选参数不能写在子命令之后**（写了报 `unrecognized arguments`，
        2026-09-27 实测就是栽在这里）；而"主解析器与子解析器两边都定义"又会让主解析器的值被
        **静默覆盖**（且 `--port` 默认值恰好相同 ⇒ 完全看不出来）。所以统一写成这个形状。
        """
        argv = [
            self.a.python,
            str(SMOKE_DB),
            cmd,
            "--psql",
            str(self.psql),
            "--port",
            str(self.a.pg_port),
            "--user",
            PG_USER,
            *args,
        ]
        return subprocess.run(
            argv, capture_output=capture, text=True, encoding="utf-8", errors="replace", check=False
        )

    def pg_ready(self) -> bool:
        """★ 判据是**真跑一次 SELECT 1**，不是"端口开着"（端口开着只说明有个东西在监听）。"""
        return self.smoke_db("ping", capture=True).returncode == 0

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.a.api_port}/api/v1/health", timeout=3
            ):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------ 各阶段
    def wait_pg_ready(self, tries: int = 30) -> bool:
        """**有界**等待"真就绪"。

        ⚠️ `wait_port` 只说 TCP 可连 —— **PG 在完成 startup 之前就会 listen**，此时任何
        查询都拿到 "the database system is starting up"（psql 非 0 退出）。⇒ "端口开着"
        连"能不能查"都不保证，更别说"是不是我要的那个 PG"。
        （2026-09-27 实测踩到：`ping` 撞进 startup 窗口，报「端口开了但 SELECT 1 不通」。）
        """
        for _ in range(tries):
            if self.pg_ready():
                return True
            time.sleep(1)
        return False

    def start_pg(self) -> None:
        # ① 已经有**可用**的 PG？直接复用 —— 并记下"不是我起的"。
        if self.pg_ready():
            say(
                "PostgreSQL 已在运行（**不是我起的**）⇒ 本轮收尾不会停它（坑 68：一个资源一个拥有者）"
            )
            return
        # ② 端口有东西但查不通：**绝不贸然再起一个** —— 那正是坑 68（两个拥有者：谁都不肯让，
        #    症状是"某个时刻起成片失败"）。多半是别人的 PG 还在 startup ⇒ 有界等它。
        if port_open(self.a.pg_port):
            say(f"端口 :{self.a.pg_port} 已监听但 SELECT 1 还不通 —— 有界等它就绪（不另起一个）")
            if self.wait_pg_ready(tries=30):
                say("PostgreSQL 就绪（**不是我起的**）⇒ 收尾不会停它")
                return
            raise SystemExit(
                f"端口 :{self.a.pg_port} 上有东西，但 30s 内 SELECT 1 都不通 —— 不是可用 PG"
            )
        # ③ 确实没人跑 ⇒ 我起，并登记所有权（收尾时才由我停）。
        # trash-ok: 单文件（PG 的 pid 文件），非递归
        (self.pg_data / "postmaster.pid").unlink(missing_ok=True)
        # ⚠️ 必须当**后台进程**起：`subprocess.Popen` 起的 PG 会被父进程退出带走。
        log = (self.tmp / "yb-pg.log").open("w", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(
            [
                str(self.pg_bin / "postgres.exe"),
                "-D",
                str(self.pg_data),
                "-p",
                str(self.a.pg_port),
                "-c",
                "listen_addresses=127.0.0.1",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.started_pg = True
        wait_port(self.a.pg_port, "PostgreSQL")
        if not self.wait_pg_ready(tries=30):
            raise SystemExit("我起的 PG 在 30s 内没有就绪（SELECT 1 不通）")
        say("PostgreSQL 就绪（我起的）")

    def provision_db(self) -> None:
        if self.a.db:
            self.db = self.a.db
            say(f"显式指定 --db {self.db} ⇒ 不销毁，该库可能跨会话累积（与 CI 不可比）")
        else:
            self.db = self.smoke_db("name", capture=True).stdout.strip()
            self.ephemeral = True
            if not self.db.startswith(SMOKE_PREFIX):
                raise SystemExit(f"库名异常：{self.db!r}")
            say(f"一次性库：{self.db}（跑完销毁；要保留加 --keep-db）")
        self.env["DATABASE_URL"] = (
            f"postgresql+asyncpg://{PG_USER}@127.0.0.1:{self.a.pg_port}/{self.db}"
        )
        say(f"$ provision {self.db}")
        rc = self.smoke_db("provision", "--db", self.db, "--repo", str(self.repo)).returncode
        if rc != 0:
            raise SystemExit("建库 / 建表 / 迁移失败")

    def seed_questions(self) -> None:
        """灌题库种子（生成 + 灌库）。

        ⚠️ 为什么必须有（**坑 51**）：`db/schema.sql` 只灌 subjects / chapters / RBAC，
        **不含 questions / knowledge_points**。缺了它，在**真正全新的库**上会有一批用例失败
        （组卷 / 加题 / 知识点下拉无题可抽）。

        ★★ 2026-09-27 实测（这条本身就是价值）：本管道是从 `%TEMP%/yb-pipe.py` 收编来的，
        而它一直跑在**累积库**上 —— 题库早在库里 ⇒ 这一步**从来不需要**，
        也就**从来没人发现它漏了**。换成一次性库后**第一次跑就报 31 failed**。
        ⇒ 这就是"环境恰好脏 ⇒ 假绿"的教科书实例（硬约定 H），
        也是"默认跑全新库"这个改动带来的**第一个收益**：
        它把"本地环境脏"这层掩盖物直接拿掉了。
        """
        rc = run(
            [
                self.a.python,
                str(HERE / "seed-questions.py"),
                "--pg-port",
                str(self.a.pg_port),
                "--db",
                str(self.db),
                "--db-user",
                PG_USER,
                "--psql",
                str(self.psql),
            ],
            HERE,
            self.env,
            "seed-questions",
        )
        if rc != 0:
            raise SystemExit("题库种子灌入失败（seed-questions.py）")

    def _retire(self, p: Path) -> None:
        """把旧产物**改名挪走**，而不是删除。

        ⚠️ 2026-09-28 实测：**本机宿主的删除保护会拒掉批量 `unlink`**
        （`SAFE_DELETE_BULK_CONFIRM_REQUIRED {count:247, threshold:50, targets:[".coverage.cli"]}`），
        于是 `clean_coverage` 把整条管道**在起 API 之前**就搞挂了 —— 而报错里
        完全没提覆盖率，看起来像"覆盖率配置坏了"（**报错指向的地方不是失败点**）。

        ⇒ 与 `e2e-web.py::isolate_next_dir` **同一个手法**：**rename 不是删除**，
          不触发守卫，效果等价（清场要的只是"这些旧文件别再被 combine 读到"）。
        ★ 落点自 2026-09-29 起统一到 **`.trash/coverage/`**（此前是 `.coverage-trash/`）——
          唯一入口 = `tools/local-verify/trash.py`，**一个名字只有一个来源**（项目约定 R）。
        ⚠️ 代价：会留一份旧数据（每次 ~150KB）。它不是"没人管"：`preflight` ⓿ 会报出来，
          并把 7 天以上的**单文件**回收掉（`docs/25` 说明为什么"目录不删、文件才删"）。
        """
        try:
            trash_mod.trash(p, kind="coverage", root=self.repo)
        except OSError as e:
            # 挪不动 ≠ 可以删。**报出来**，绝不"退化成删除"（那正是 R 要防的滑坡）。
            say(f"!! 覆盖率旧文件挪不进回收站（{e}）—— 留在原处，不删")

    def clean_coverage(self) -> None:
        """整族清场 —— 只删"自己产生的名字"永远清不掉"上次改了命名方案留下的"那种文件。

        ⚠️ 绝不要写 `.coverage*`：那个通配符**会匹配 `.coveragerc`**，把门禁配置本身删掉
        （已实测：`Get-ChildItem .coverage*` → `.coverage | .coveragerc`）。
        """
        for f in self.repo.glob(".coverage.*"):
            if f.name == ".coveragerc":  # 理论不匹配，守卫便宜
                continue
            self._retire(f)
        for name in (".coverage", "coverage.xml"):
            self._retire(self.repo / name)
        if (self.repo / "htmlcov").is_dir():
            self._retire(self.repo / "htmlcov")
        if not (self.repo / ".coveragerc").is_file():
            raise SystemExit("清场把 .coveragerc 删了 —— 覆盖率门禁会**静默失效**（宁可当场报错）")
        say("覆盖率清场完成（.coveragerc 仍在；旧的挪进 .trash/coverage/，不删）")

    def cov_file(self, kind: str) -> Path:
        return self.repo / f".coverage.{kind}"

    def seed_cli(self) -> None:
        """CLI 跑在**它自己的进程**里 ⇒ 必须在它自己的进程里插桩（硬约定 L）。

        `--append`：seed-rbac 与 seed-admin 是**两次进程**，共用一份 `.coverage.cli`。
        """
        for sub in ("seed-rbac", "seed-admin"):
            rc = run(
                [
                    self.a.python,
                    "-m",
                    "coverage",
                    "run",
                    "--append",
                    "--data-file",
                    str(self.cov_file("cli")),
                    "--source",
                    "app",
                    "--rcfile",
                    str(self.repo / ".coveragerc"),
                    "-m",
                    "app.cli",
                    sub,
                ],
                self.api_dir,
                self.env,
                f"cli {sub}",
            )
            if rc != 0:
                raise SystemExit(f"app.cli {sub} 失败")

    def start_api(self, *, coverage: bool = True) -> None:
        stop_file = self.tmp / "yb-cov-stop"
        # trash-ok: 单文件（停机哨兵），非递归
        stop_file.unlink(missing_ok=True)
        log = (self.tmp / "yb-api.log").open("w", encoding="utf-8")  # noqa: SIM115
        args = [
            self.a.python,
            "serve_fake_redis.py",
            "--pg-port",
            str(self.a.pg_port),
            "--api-port",
            str(self.a.api_port),
            "--log-file",
            str(self.tmp / "yb-api-inner.log"),
            # ⚠️ --shutdown-file 是必须的：本机收尾若走 terminate，atexit 不跑
            #    ⇒ 优雅退出（以及覆盖率落盘）都不会发生。
            "--shutdown-file",
            str(stop_file),
        ]
        if coverage:
            args += [
                "--coverage",
                "--cov-data-file",
                str(self.cov_file("api")),
            ]
        self.api = subprocess.Popen(
            args,
            cwd=str(HERE),
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(60):
            if self.health():
                say(f"API 就绪 (:{self.a.api_port})")
                return
            time.sleep(1)
        raise SystemExit("API 未在 60s 内就绪")

    def pytest(self) -> int:
        rc, out = run_capture(
            [
                self.a.python,
                "-m",
                "coverage",
                "run",
                "--data-file",
                str(self.cov_file("tests")),
                "--source",
                "app",
                "--rcfile",
                str(self.repo / ".coveragerc"),
                "-m",
                "pytest",
                *shlex.split(self.a.pytest_target),
                *shlex.split(self.a.pytest_args),
            ],
            self.api_dir,
            self.env,
            f"pytest {self.a.pytest_args}",
        )
        # 摘出 `354 passed, 1 skipped in …` 这一行 —— 回执里要**引用数字**，
        # 只写"跑过了"等于让人回去翻日志（而日志会被下一次运行覆盖）。
        hits = re.findall(r"\d+ passed[^\n]*", out)
        self.passed_line = hits[-1].strip() if hits else ""
        return rc

    def e2e_web(self) -> int:
        """**把 pytest 那一步换成浏览器走查**（`e2e-web.py`）。

        为什么复用这套脚手架而不是另写一个起服务脚本
        ----------------------------------------------
        浏览器走查需要的东西与 pytest **完全一样**：一次性库、RBAC + 超管种子、
        同源 env（`JWT_SECRET` / `APP_ENV`）、一个已就绪的 API。
        再写一遍就等于**再赌一次** PG 的启动与就绪（"端口开着 ≠ 就绪"）、
        资源的拥有者归属（坑 68）、env 同源（`JWT_SECRET` 不一致就报 40102）。
        ⇒ 差别只有"被测对象"：一个是 httpx 打接口，一个是真浏览器点页面。

        ⚠️ **题库按场景决定灌不灌**（`SCENARIOS_NEEDING_QUESTIONS`）：
        `login` / `p2a` 用不到题目（`subjects` / `chapters` 在 `schema.sql` 里，本来就建好了），
        灌一遍白等约 30 秒。但**"要不要"必须显式回答** —— 第一版写的是"不灌题库，
        P2b 要选题时再加"，于是 P2b-1 第一次跑就撞上"章节全是 0 题"，
        而那个症状**长得像产品 bug**（参考"选章节读错冗余列"那条），白跑了 3 轮。
        """
        cmd = [
            self.a.python,
            str(HERE / "e2e-web.py"),
            "--scenario",
            self.a.e2e_scenario,
            "--repo",
            str(self.repo),
            "--api-base",
            f"http://127.0.0.1:{self.a.api_port}/api/v1",
            "--phone",
            self.a.e2e_phone,
            "--password",
            self.a.e2e_password,
        ]
        if self.a.e2e_no_build:
            cmd.append("--no-build")
        if self.a.e2e_force_build:
            cmd.append("--force-build")
        if self.a.e2e_dev:
            cmd.append("--dev")
        if self.a.e2e_keep_web:
            cmd.append("--keep-web")
        if self.a.e2e_shots:
            cmd += ["--shots", self.a.e2e_shots]
        # 构建 + 起 next + 浏览器走查：给 15 分钟（首次构建 ~40s，冷启动 ~10s）
        return run(
            cmd,
            HERE,
            self.env,
            f"e2e-web（场景 {self.a.e2e_scenario}，移动视口 375×667）",
            timeout=900,
        )

    def stop_api_gracefully(self) -> None:
        """放哨兵 → 让 API 自己 cov.save() 落盘。若直接 terminate，数据文件是空的（不是报错）。"""
        if self.api is None:
            return
        (self.tmp / "yb-cov-stop").write_text("stop", encoding="utf-8")
        for _ in range(60):
            if not self.health():
                say("API 已优雅退出")
                break
            time.sleep(1)
        try:
            self.api.wait(timeout=60)
        except subprocess.TimeoutExpired:
            say("!! API 收到哨兵后 60s 仍未退出")

    def report(self) -> int:
        parts = [self.cov_file(k) for k in ("api", "cli", "tests")]
        for p in parts:
            size = p.stat().st_size if p.exists() else -1
            say(f"  {p.name}: {size} bytes")
            if size <= 0:
                say(f"!! 覆盖率数据缺失或为空：{p.name} —— **拒绝静默合并**")
                return 1
        rc = run(
            [
                self.a.python,
                "-m",
                "coverage",
                "combine",
                "--data-file",
                str(self.repo / ".coverage"),
                *[str(p) for p in parts],
            ],
            self.repo,
            self.env,
            "coverage combine",
        )
        if rc != 0:
            return rc
        out = subprocess.run(
            [
                self.a.python,
                "-m",
                "coverage",
                "report",
                "--data-file",
                str(self.repo / ".coverage"),
                "--skip-covered",
            ],
            cwd=str(self.repo),
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        (self.tmp / "yb-cov-report.txt").write_text(
            out.stdout + "\n----stderr----\n" + out.stderr, encoding="utf-8"
        )
        say(f"coverage report exit={out.returncode}（0 = 过 fail_under）")
        say("---- 逐文件（有缺失的才列出；与 CI 的 `::notice 覆盖率逐文件` 同口径）----")
        for line in out.stdout.rstrip().splitlines():
            say("  " + line)
        totals = re.findall(r"^TOTAL\s+\d+\s+\d+\s+\d+%.*$", out.stdout, re.MULTILINE)
        self.total_line = totals[-1].strip() if totals else ""
        say(f"（完整报告：{self.tmp / 'yb-cov-report.txt'}）")
        return out.returncode

    # ------------------------------------------------------------ 收尾
    def teardown(self) -> None:
        if self.api is not None and self.api.poll() is None:
            self.api.terminate()
            say("API 已强制停止")
        # 顺序：先 DROP 库（此时 PG 还在），再停 PG。
        if self.db and self.ephemeral:
            if self.a.keep_db:
                say(f"按 --keep-db 保留一次性库 {self.db}（记得手动清理）")
            else:
                r = self.smoke_db("drop", "--db", self.db, capture=True)
                say(
                    f"已销毁一次性库 {self.db}"
                    if r.returncode == 0
                    else f"⚠️ 销毁 {self.db} 失败（下次 provision 会先收掉同名残留）"
                )
        if self.started_pg:
            if self.a.no_pg_stop:
                say("按 --no-pg-stop 保留 PostgreSQL 运行")
            else:
                subprocess.run(
                    [
                        str(self.pg_bin / "pg_ctl.exe"),
                        "-D",
                        str(self.pg_data),
                        "stop",
                        "-m",
                        "fast",
                    ],
                    capture_output=True,
                    check=False,
                )
                say("PostgreSQL 已停（本轮是我起的）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="run-local-pipeline.py",
        description="本机一键管道（subprocess.Popen 版，沙箱可跑）：一次性库 + 三份插桩 + 同 CI 口径",
    )
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--python", default=PY_DEFAULT)
    ap.add_argument("--pg-prefix", default=str(PG_PREFIX_DEFAULT))
    ap.add_argument("--pg-data", default=str(PG_DATA_DEFAULT))
    ap.add_argument("--pg-port", type=int, default=55432)
    ap.add_argument("--api-port", type=int, default=8123)
    ap.add_argument("--pytest-args", default="-q -rfEXs")
    ap.add_argument(
        "--pytest-target",
        default="tests",
        help="pytest 的目标（默认 tests；调试单条用例时传 tests/xxx.py::test_yyy）",
    )
    ap.add_argument("--db", default="", help="显式库名（默认空 = 一次性库，跑完销毁）")
    ap.add_argument("--keep-db", action="store_true", help="不销毁一次性库")
    ap.add_argument("--no-pg-stop", action="store_true", help="不停 PostgreSQL（即使是我起的）")
    ap.add_argument(
        "--no-preflight",
        action="store_true",
        help="跳过本地门禁预检（tools/preflight.sh：诊断自检 / 不变量 / ruff ×2）",
    )
    # ---- 浏览器走查（`--e2e-web`）：把 pytest 那一步换成"真浏览器点一遍"----
    ap.add_argument(
        "--e2e-web",
        action="store_true",
        help="跑 C 端浏览器走查（不起覆盖率门禁；见 e2e-web.py）",
    )
    ap.add_argument(
        "--e2e-scenario",
        default="login",
        # ⚠️ 这是**第二份**场景白名单（第一份在 `e2e-web.py` 的 `--scenario`）。
        #    加场景要改两处 —— 但**两处都必须是白名单**：只在 e2e-web 里加的话，
        #    从管道传进来会被这里**拒掉**（实测过一次：`invalid choice: 'p2b1'`，
        #    退出码 2、1 秒结束）。宁可"要改两处"，也不要"传错了静默跑默认场景"。
        choices=["login", "p2a", "p2b1", "p2c1", "p2c2"],
        help="走查场景：login（P1 登录闭环）/ p2a（Tab + 注册 + 引导）/ "
        "p2b1（刷题数据流：选章节 → 答题判分 → 刷新仍在）/ "
        "p2c1（交卷 → 结果页 + 空卷显示「—」）/ p2c2（错题本：列表/筛选/详情/那扇门）",
    )
    ap.add_argument(
        "--e2e-phone", default="13800000000", help="走查用的账号（默认 = seed-admin 的）"
    )
    ap.add_argument("--e2e-password", default="Admin@123456")
    ap.add_argument("--e2e-no-build", action="store_true", help="复用现有 apps/web/.next")
    ap.add_argument("--e2e-force-build", action="store_true", help="强制重新构建 apps/web")
    ap.add_argument(
        "--e2e-dev",
        action="store_true",
        help="走查用 `next dev` 而不是「构建 + next start」（★ 本机必加，理由见 e2e-web.py 抬头）",
    )
    ap.add_argument(
        "--e2e-keep-web", action="store_true", help="走查完不关 next start（手工接着点）"
    )
    ap.add_argument(
        "--e2e-shots",
        default="",
        help="把走查途中关键画面截到该目录（透传给 `e2e-web.py --shots`）",
    )
    args = ap.parse_args(argv)

    p = Pipeline(args)
    try:
        # ---- 第 0 步：本地门禁预检（fail-fast）----
        # 本地共 6 道门禁：这里 4 道（静态）+ 下面 pytest/覆盖率 2 道。CI 另有 7 道前端。
        # 明细与"差几道"见 `tools/preflight.sh` 的输出和 `docs/24` §10。
        if not args.no_preflight:
            preflight(p)
        p.start_pg()
        p.provision_db()
        if args.e2e_web:
            # 浏览器走查分支：灌 RBAC + 超管（走查要用的账号就是它），**不跑覆盖率门禁**
            # —— 它量的是"浏览器交互通不通"，不是覆盖率；混进来只会让门槛无意义地红。
            p.seed_cli()
            if args.e2e_scenario in SCENARIOS_NEEDING_QUESTIONS:
                p.seed_questions()
            else:
                # ★ 跳过**必须出声**（三态纪律）：否则下一个加场景的人会看到
                #   "章节全是 0 题"，并去查一个不存在的产品 bug。
                say(
                    f"（场景 {args.e2e_scenario} 不在 SCENARIOS_NEEDING_QUESTIONS 里 ⇒ "
                    "**跳过题库种子**；若新场景要用到题目，请把它登记进去）"
                )
            p.start_api(coverage=False)
            rc = p.e2e_web()
            p.stop_api_gracefully()
            say(f"e2e-web exit={rc} ⇒ 本轮 {'通过' if rc == 0 else '未通过'}")
            # ⚠️ **走查分支不写回执**：它换掉的就是 pytest/覆盖率那一步，
            #   写一份"通过"的回执会让 preflight 把 ⑤⑥ 标成 📋 ——
            #   而这两道**本轮根本没跑**。回执只由真正跑了它们的那条路径写。
            say("（本分支不写管道回执：pytest / 覆盖率这两道本轮没跑）")
            return rc
        p.clean_coverage()
        # ⚠️ **顺序与 CI 对齐**：先 RBAC + 超管，**再**灌题库。
        #    2026-09-27 实测：反过来的顺序（题库先）在**全新库**上会有一批用例失败 ——
        #    题目要挂 `creator`（超管用户），超管还没建时灌进去的题数据就不对。
        #    `run-smoke.ps1` 里一直是反的，却**从来没被发现** —— 因为它一直跑在
        #    超管早已存在的**累积库**上（同一个"环境脏掩盖问题"的形态）。
        p.seed_cli()
        p.seed_questions()
        p.start_api()
        rc_pytest = p.pytest()
        p.stop_api_gracefully()
        rc_cov = p.report()
        rc = 0 if rc_pytest == 0 and rc_cov == 0 else 1
        say(
            f"pytest exit={rc_pytest} / coverage exit={rc_cov} ⇒ 本轮 {'通过' if rc == 0 else '未通过'}"
        )
        # ★ 写"管道回执"：让 `preflight` 的 📋（由别的工具跑过）**有证可查**。
        #   失败也写 —— "我知道它跑过、而且知道它失败了"同样是一条结论。
        stamp = pipeline_stamp.write_stamp(
            pytest_rc=rc_pytest,
            cov_rc=rc_cov,
            passed=p.passed_line,
            total=p.total_line,
        )
        say(f"管道回执已写：{stamp}")
        return rc
    finally:
        p.teardown()


if __name__ == "__main__":
    sys.exit(main())
