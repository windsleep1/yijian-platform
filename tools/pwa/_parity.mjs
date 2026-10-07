/* 判分对拍的 **node 侧** —— 直接 import PWA 那份 `grade.mjs`，跑同一批向量。
 *
 * ★ 为什么用 `.mjs` 跑 node（而不是先编译 TS）：
 *   多一步编译 = 多一步会失修的地方。`grade.mjs` 是纯 ESM ⇒ node 直接读，
 *   于是"本地能跑对拍"这件事不依赖任何构建链。
 *
 * 用法：node _parity.mjs <vectors.json> <out.json>
 * 输出：{ results: [[isCorrect, ratio], ...], normalize: [...], legacy: [isCorrect, ratio] }
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
/* ★ Windows 上必须转成 `file://` URL —— 直接 `import("<盘符>:\\...\\grade.mjs")`
   会报 `ERR_UNSUPPORTED_ESM_URL_SCHEME: Received protocol 'c:'`
   （ESM 加载器只认 file / data / node 三种 scheme）。
   ★ 这条**只在 Windows 上炸**，Linux/macOS 上路径本身就是 `/...` 所以看不出问题
   —— 属于"同一份代码在两个环境里表现不同"那一族，所以这里显式转，不赌平台。 */
const gradeMod = await import(
  pathToFileURL(join(here, "..", "..", "apps", "pwa", "src", "lib", "grade.mjs")).href
);
const { gradeAnswer, normalizeUserValue } = gradeMod;

const [, , vectorsPath, outPath] = process.argv;
if (!vectorsPath || !outPath) {
  console.error("用法：node _parity.mjs <vectors.json> <out.json>");
  process.exit(2);
}

const payload = JSON.parse(readFileSync(vectorsPath, "utf8"));
const { rules, vectors, normalizeCases } = payload;

const results = vectors.map((v) => {
  const [type, correct, user, partialCredit] = v.slice(0, 4);
  const r = gradeAnswer({ type, correct, user, partialCredit, rules });
  return [r.isCorrect, r.ratio];
});

/* 归一的方向也要对拍：`normalizeUserValue` 该收的收、该拒的拒（与 Python 同口径）。
   输出 "ok:<归一回的值>" 或 "err"。 */
const normalize = normalizeCases.map(([type, raw]) => {
  try {
    return ["ok", normalizeUserValue(type, raw)];
  } catch (e) {
    return ["err", e && e.code ? e.code : String(e)];
  }
});

/* ★★ **反向对照**（本文件的另一半价值）
 *
 * 上面对拍的是"**归一到规范形式之后**两侧一致"。但那句话有个前提：**包真的归一了**。
 * 如果哪天导出侧漏了归一（比如有人把 `check_doc` 那道闸去掉），上面那批向量
 * **照样全绿** —— 因为向量是照着"已归一"造的。
 *
 * ⇒ 这里再跑一条**旧写法**的判断题：正确答案 `["A"]`（种子的老格式），用户答 `[true]`。
 *   · Python 侧：`judge_bool("A") === True` ⇒ 判**对**（1.0 分）
 *   · 本模块：`{"A"}` ≠ `{true}` ⇒ 判**错**（0 分）
 *   **两者必须不同** —— 那条"应该红"的用例，才是"归一这一步在承重"的证据。
 *   哪天两边结果**相同**了，说明有人又在 JS 里加了一张 token 表
 *   （那是本方案明确禁止的：§3.1 —— PWA 不自带语义）。
 */
const legacy = gradeAnswer({
  type: "judge",
  correct: ["A"], // ← 旧写法（导出侧本该把它归一掉）
  user: [true],
  partialCredit: false,
  rules,
});

writeFileSync(
  outPath,
  JSON.stringify({ results, normalize, legacy: [legacy.isCorrect, legacy.ratio] }),
  "utf8",
);
