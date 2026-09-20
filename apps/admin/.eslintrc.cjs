/* ESLint 配置 —— 门禁整顿**第一步**（2026-09-20，用户定）
 *
 * ## 为什么会有这个文件
 *
 * 之前 `npm run lint` 是 `next lint`，而仓库里**没有任何 eslint 配置**、
 * `eslint` 也不在 devDependencies 里。于是 `next lint` 会进入
 * 「How would you like to configure ESLint?」**交互式提问** —— 在 CI / 脚本里直接卡死。
 * 也就是说：**这是一道假门禁**（脚本在、配置不在），跑得动只是因为它从来没真跑过。
 *
 * ## 本步的范围（用户明确划定）
 *
 * - 只做「**让真门禁能跑**」，把问题清单露出来；**不修任何问题、不动业务代码**
 * - 规则集：`@typescript-eslint` 基础集 + `react-hooks`（**不引入新风格、不加 prettier**）
 * - 严重级：**全部设 warn，不设 error** —— 见下面 `asWarn()`
 *
 * ## 为什么用 `.cjs` 而不是 `.eslintrc.json`
 *
 * 需要写注释解释「为什么全是 warn」，JSON 写不了注释。eslint 对两种都支持。
 */

const js = require("@eslint/js");
const reactHooks = require("eslint-plugin-react-hooks");
const tseslint = require("@typescript-eslint/eslint-plugin");

/**
 * 把一条规则配置的严重级统一降为 `warn`（数组形态只替换第 0 项，保留选项）。
 *
 * **为什么需要它**：本步的目标是"让门禁能跑起来 + 看到清单"，而不是"让门禁挂掉"。
 * 若沿用预设默认的 `error`，`npm run lint` 退出码非 0，会被误读成
 * "门禁坏了"—— 明明存量问题还没清理。而且这一步**明确不修问题**，
 * 一个必然失败的门禁只会被绕过（`--no-verify`、直接不看）。
 *
 * **第二步（独立批：风格统一）怎么改**：把 `asWarn()` 从 `rules` 里去掉即可 ——
 * 严重级回到预设的 `error`；配合全仓格式化**单独一个 commit**。
 * 这样"降级"只存在于一处，"提级"也是一处改动，不会散落。
 */
const asWarn = (rules = {}) =>
  Object.fromEntries(
    Object.entries(rules).map(([name, value]) => {
      if (Array.isArray(value)) return [name, ["warn", ...value.slice(1)]];
      return [name, value === "error" || value === 2 ? "warn" : value];
    }),
  );

module.exports = {
  root: true,

  env: { browser: true, es2022: true, node: true },

  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },

  plugins: ["@typescript-eslint", "react-hooks"],

  /*
   * ⚠️ 这里**刻意不用 `extends`**，改为显式合并。
   *
   * `extends` 是 eslint 在内部解析的，我们的 `asWarn()` 够不到它 ——
   * 结果就只有自己写的那几条能降级，预设带来的 86 条仍是 error。
   * 显式展开后，`asWarn()` 作用在**每一条**规则上，行为可预测、可验证。
   *
   * 三份预设的规模（实测）：@eslint/js 61 条 + @typescript-eslint 23 条 + react-hooks 2 条。
   * 去重后由 eslint 自己合并（后面的覆盖前面的）。
   */
  rules: {
    ...asWarn(js.configs.recommended.rules),
    ...asWarn(tseslint.configs.recommended.rules),
    ...asWarn(reactHooks.configs.recommended.rules),
  },

  /*
   * ⚠️ 跑 `npm run lint` 时会看到一行提示：
   *     "The Next.js plugin was not detected in your ESLint configuration."
   *
   * 这是**刻意的**，不是配置漏了：加上 `eslint-config-next` 会引入一整套
   * Next 专属规则（`@next/next/*`），而用户对本步的规则集要求明确是
   * 「@typescript-eslint 基础集 + react-hooks」——**不引入新风格**。
   * 那行提示只是 Next 在提醒"我没参与 lint"，不影响检查结果（退出码 0）。
   * 第二步若决定加 Next 规则，再补 `extends: ['next']` 即可，届时该提示自动消失。
   */

  ignorePatterns: [
    "node_modules/",
    ".next/",
    "out/",
    // Next 自动生成，内容随构建变化，lint 它没有意义（官方模板也是排除的）
    "next-env.d.ts",
  ],

  /*
   * ⚠️ 以下不是"修 lint 报出的问题"，而是**基础配置本身** —— 两条都属于
   * "JS 规则用在 TS 文件上必然误报"的类别。判断依据是 @typescript-eslint
   * 官方 FAQ：**`no-undef` 在 TypeScript 项目里必须关闭**。
   */
  overrides: [
    {
      files: ["**/*.ts", "**/*.tsx"],
      rules: {
        /*
         * 实测本仓会产生 **8 条假阳性**，全部形如：
         *     const submitSearch = (e: React.FormEvent) => { ... }
         *     children: React.ReactNode;
         *     function Skeleton({ ... }: React.HTMLAttributes<HTMLDivElement>) { ... }
         *
         * 都是**类型位置**上的全局命名空间 `React` 引用 —— 文件本身**没有**
         * `import React`（走的是 React 17+ 的新 JSX 转换），`tsc --noEmit` 全绿。
         * `no-undef` 是 JS 规则，不认识"类型位置的全局命名空间"，于是误报。
         *
         * **关掉它不会失去保护**：未定义标识符由 TS 编译器自己报，
         * 而 `tsc --noEmit` 本来就是本项目的另一道门禁（CI 里与它并列跑）。
         * 留着它只会让真问题淹在噪声里 —— 一个全是假阳性的门禁等于没有门禁。
         */
        "no-undef": "off",
      },
    },
  ],
};
