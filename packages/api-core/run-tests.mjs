#!/usr/bin/env node
/**
 * `packages/api-core` 的测试入口 —— 比 `node --test` 多**一道"到底跑了没有"的守卫**。
 *
 * ## 为什么不能直接 `node --test`
 *
 * `node --test`（和 pytest 一样）在**全部 skip** 时退出码也是 0。
 * 只看退出码的调用方会把"一条都没跑"读成"全过"。本项目为此交过学费：
 * 一个 harness 因为漏了一个环境变量 → 12 条全 skip → 报"基线全绿"，
 * 并连带产出 4 个**假的"变异存活"**（`docs/22` §9.2.2 第 3 级的来历）。
 *
 * ⇒ 判据写死在这里：**`pass >= 1` 且 `skipped == 0` 且 `fail == 0`**，
 * 三者缺一都非 0 退出。本地与 CI 跑的是**同一条命令**（`npm test`），
 * 免得又出现"本地绿、CI 红"那种两个口径（坑 51 的形态）。
 *
 * ## ⚠️ 为什么用异步 `spawn` 而不是 `spawnSync`
 *
 * 本机（Windows + 沙箱）用 `spawnSync(process.execPath, …)` 会直接
 * `Error: spawnSync …node.exe EBUSY` —— **一次都跑不起来**。
 * 异步 `spawn` 正常。⇒ 这不是"CI 才会遇到"的问题，是"本地会假红"的问题：
 * 用 `spawnSync` 的话，本地永远报"没跑成"，而 CI 是绿的（两个口径，正是要避免的）。
 *
 * ⚠️ 解析的是 TAP 汇总行（`# pass N` / `# fail N` / `# skipped N`）。
 * 如果 Node 改了输出格式，本脚本会**找不到汇总行 → 直接失败**（宁可报"没跑成"，
 * 也不要把"读不到数字"当成"通过"）。
 */

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const pkgDir = dirname(fileURLToPath(import.meta.url));
const spec = join("test", "auth-chain.test.ts");

/** 跑子进程并收集全部输出（含退出码）。 */
function runNode(args) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, { cwd: pkgDir });
    let out = "";
    child.stdout.on("data", (d) => {
      out += d;
    });
    child.stderr.on("data", (d) => {
      out += d;
    });
    child.on("error", (e) => resolve({ code: null, out: `${out}\n[spawn error] ${String(e)}` }));
    child.on("close", (code) => resolve({ code, out }));
  });
}

const { code, out } = await runNode(["--test", spec]);
process.stdout.write(out);

const num = (key) => {
  const m = out.match(new RegExp(`^# ${key} (\\d+)$`, "m"));
  return m ? Number(m[1]) : null;
};

const pass = num("pass");
const fail = num("fail");
const skipped = num("skipped");

if (pass === null || fail === null || skipped === null) {
  console.error("\n✗ 读不到 TAP 汇总行（pass/fail/skipped）—— 没跑成 ≠ 通过，拒绝当成成功。");
  if (code !== 0) console.error(`  （子进程退出码 ${code}）`);
  process.exit(2);
}

console.log(`\n[api-core] pass=${pass} fail=${fail} skipped=${skipped}`);

if (pass < 1) {
  console.error("✗ pass = 0：一条用例都没跑（文件没匹配上？）—— 这不是通过。");
  process.exit(1);
}
if (skipped !== 0) {
  console.error(`✗ skipped = ${skipped}：有用例被静默跳过 —— 换个干净环境它还会 skip 吗？`);
  process.exit(1);
}
if (fail !== 0) {
  console.error(`✗ fail = ${fail}`);
  process.exit(1);
}

console.log("✓ 用例真的跑了，且全过");
