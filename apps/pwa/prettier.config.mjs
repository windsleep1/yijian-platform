/**
 * Prettier 配置 —— 门禁整顿第二步（Commit B）引入。
 *
 * ## 为什么用 `.mjs` 而不是 `.prettierrc.json`
 *
 * 每一项都要解释"为什么是这个值"，JSON 写不了注释。与本仓 `apps/admin/.eslintrc.cjs`
 * 的做法一致（那边也是靠注释说明取舍）。
 *
 * ## 取值原则：**贴着现有代码，而不是贴着自己的偏好**
 *
 * 格式化的第一原则是"**别让 diff 里混进样式噪音**" —— 这个 commit 的 diff
 * 本来就有几千行，如果再把"我喜欢的风格"叠上去，评审时根本读不出哪些是真变化。
 * 所以每个值都是从现有代码**实测反推**出来的。
 */

/** @type {import("prettier").Config} */
export default {
  /**
   * 100，而不是 prettier 默认的 80 —— 依据是实测（apps/admin/src，17537 行）：
   *
   *     p95 = 79    p99 = 100    max = 357
   *     超过 80 列：825 行（4.7%）    超过 100 列：164 行（0.9%）
   *
   * 取 100 只需要碰 0.9% 的行，取 80 要多动 4.7%。后端 `ruff format` 同样取 100
   * （依据：Python p99=97），**两边口径一致**，免得读代码时在两种宽度间切换。
   */
  printWidth: 100,

  /** 2 空格缩进 —— 现有代码就是这个，且是 JS/TS 生态的绝对主流。 */
  tabWidth: 2,
  useTabs: false,

  /** 分号：现有代码全带，保留。 */
  semi: true,

  /** 双引号：现有代码全是双引号（`import x from "…"`），保留。 */
  singleQuote: false,

  /** 对象 key 只在必要时加引号 —— 与手写风格一致，少一层视觉噪音。 */
  quoteProps: "as-needed",

  /**
   * 多行结构的尾随逗号 —— 现有代码一直是这么写的（`[{...}, {...},]`）。
   * 另一个好处：往列表末尾追加一项时，diff 只有一行而不是两行。
   */
  trailingComma: "all",

  /** 单参箭头函数也带括号：`(e) => …` 而不是 `e => …`。现有代码统一带括号。 */
  arrowParens: "always",

  /** `{ a, b }` 花括号内留空格。现有代码如此。 */
  bracketSpacing: true,

  /**
   * ⚠️ 与仓库根 `.gitattributes` 的 `* text=auto eol=lf` **必须一致**。
   *
   * 本机 `core.autocrlf=true`，若不显式指定，prettier 的 `"auto"` 会沿用"当前文件
   * 已有的行尾"——于是一批文件是 LF、另一批是 CRLF，**再配合 git 的归一化，
   * diff 里会冒出整文件都在变的假象**。写死 `lf` 之后三处一致。
   */
  endOfLine: "lf",
};
