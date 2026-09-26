#!/usr/bin/env node
/**
 * 反向检查：认证链路的实现**必须只有一份**（`packages/api-core`）。
 *
 * ## 它防的是什么
 *
 * `docs/22-C端-方案.md` §9.2 把"401 单飞刷新 / Rotation 成对存回 / 码集"三处逻辑收进了
 * `packages/api-core`。**最可能发生的漂移不是"包被改错"，而是"有人又在某个 app 里重写一遍"**
 * —— 那种漂移不会报错、不会变红，只会让两端慢慢分叉。
 * 这是它的**唯一机械信号**。
 *
 * ## 三条规则（对 `apps/<app>/src` 生效）
 *
 *   ① `4010x` 码字面量 —— 码表只能来自 `classifyAuthFailure`（连注释也不允许，见下）
 *   ② `/auth/refresh` —— 唯一允许发起刷新请求的地方是那个包
 *   ③ `inflightRefresh` —— "单飞"是包的实现细节，app 里出现即等于自己实现了一遍
 *
 * ⚠️ **为什么连注释里也不许出现码字面量**：不是洁癖 —— 带码字面量的注释就是"第二份说明"，
 * 而说明也会漂（改了码表却忘了改注释）。码表的解释写在**实现旁边**（包内），
 * app 里要引用就写模块名。
 *
 * ⚠️ **本检查依赖"当前是 0 命中"**（已实测：全部命中原本集中在 `apps/admin/src/lib/api.ts` 一处）。
 * 若将来某条规则出现**合法**命中，正解是**在规则里加白名单并写明理由**，
 * 而不是把整条规则删掉（删掉就等于把这条防线永久关闭）。
 *
 * 跑法：`node tools/local-verify/check-auth-chain.mjs`（CI 前端 job 第一步，失败即退）
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");

const RULES = [
  {
    id: "4010x 码字面量",
    re: /4010\d/,
    why: "码表的唯一来源是 @yijian/api-core 的 classifyAuthFailure",
  },
  { id: "/auth/refresh 调用", re: /auth\/refresh/, why: "刷新请求只能由 packages/api-core 发起" },
  {
    id: "inflightRefresh",
    re: /inflightRefresh/,
    why: "单飞是包的实现细节，app 里出现等于自己实现了一遍",
  },
];

const SKIP_DIRS = new Set(["node_modules", ".next", "dist", "out", ".turbo"]);

function walk(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out; // 目录不存在（例如 C 端还没建）不是错误
  }
  for (const e of entries) {
    const p = join(dir, e.name);
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      out.push(...walk(p));
    } else if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(e.name)) {
      out.push(p);
    }
  }
  return out;
}

function appSrcDirs() {
  const appsDir = join(REPO_ROOT, "apps");
  let names;
  try {
    names = readdirSync(appsDir, { withFileTypes: true });
  } catch {
    return [];
  }
  return names
    .filter((e) => e.isDirectory())
    .map((e) => join(appsDir, e.name, "src"))
    .filter((p) => {
      try {
        return statSync(p).isDirectory();
      } catch {
        return false;
      }
    });
}

const violations = [];
let scanned = 0;

for (const srcDir of appSrcDirs()) {
  for (const file of walk(srcDir)) {
    scanned += 1;
    const lines = readFileSync(file, "utf8").split(/\r?\n/);
    lines.forEach((line, i) => {
      for (const rule of RULES) {
        if (rule.re.test(line)) {
          violations.push({
            file: relative(REPO_ROOT, file).split(sep).join("/"),
            line: i + 1,
            rule: rule.id,
            why: rule.why,
            text: line.trim().slice(0, 120),
          });
        }
      }
    });
  }
}

console.log(`[check-auth-chain] 扫描文件数 = ${scanned}（apps/*/src）`);
if (scanned === 0) {
  // 扫了 0 个文件 ≠ 通过 —— 那是"量错了对象"（硬约定 J），必须显式失败
  console.error("✗ 一个文件都没扫到：目录结构变了？本检查会静默变成假绿，故直接失败。");
  process.exit(1);
}

if (violations.length > 0) {
  console.error(`\n✗ 认证链路出现了第二份实现（${violations.length} 处）：\n`);
  for (const v of violations) {
    console.error(`  ${v.file}:${v.line}  [${v.rule}]  ${v.why}`);
    console.error(`      ${v.text}`);
  }
  console.error(
    "\n正解：把逻辑放回 packages/api-core（或从那里 import），" +
      "不要在 app 里再写一份 —— 两端分叉不会报错，只会让用户莫名被踢。",
  );
  process.exit(1);
}

console.log(`✓ 认证链路单一真相：${RULES.map((r) => r.id).join(" / ")} 在 apps/*/src 下 0 命中`);
