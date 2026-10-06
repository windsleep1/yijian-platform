/* ESLint 配置 —— 门禁整顿（2026-09-20，用户定）
 *
 * ## 为什么会有这个文件
 *
 * 之前 `npm run lint` 是 `next lint`，而仓库里**没有任何 eslint 配置**、
 * `eslint` 也不在 devDependencies 里。于是 `next lint` 会进入
 * 「How would you like to configure ESLint?」**交互式提问** —— 在 CI / 脚本里直接卡死。
 * 也就是说：**这是一道假门禁**（脚本在、配置不在），跑得动只是因为它从来没真跑过（坑 44）。
 *
 * ## 演进
 *
 * - **第一步（已交付）**：补齐配置让门禁真能跑。规则级**全部设 warn**，
 *   因为当时有 14 条存量未清 —— 一个必然失败的门禁只会被绕过（`--no-verify`、直接不看）。
 * - **第二步（本次）**：**存量清零 → 提级为 error**。`asWarn()` 已删除，
 *   严重级回到预设默认，仅 `react-hooks/exhaustive-deps` 需要显式提级（见下）。
 *
 * ## 为什么用 `.cjs` 而不是 `.eslintrc.json`
 *
 * 需要写注释解释「为什么是这几条、为什么这样摆」，JSON 写不了注释。
 */

const js = require("@eslint/js");
const reactHooks = require("eslint-plugin-react-hooks");
const tseslint = require("@typescript-eslint/eslint-plugin");

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
   * ⚠️ 这里**刻意不用 `extends`**，改为显式合并三份预设的 `rules`。
   *
   * 原先是配合 `asWarn()` 降级用的（`extends` 由 eslint 内部解析，`asWarn()` 够不到，
   * 结果只有自己写的那几条能降级）。现在虽然不需要降级了，**仍然保留显式展开**，
   * 理由变成"**可见性**"：预设里到底有多少条规则、哪些被覆盖，在这里一眼看得出。
   *
   * 三份预设的规模（实测）与合并结果：
   *     @eslint/js       61 条
   *     @typescript-eslint 23 条   ← 其中把 base 的 `no-unused-vars` 设为 `off`
   *     react-hooks       2 条
   *     合并后 85 条 = 82 error + 3 off（`no-unused-vars` 等由 TS 版接管）
   *
   * ⚠️ 顺序即优先级（后面的覆盖前面的）。**`@typescript-eslint` 必须在 `@eslint/js` 之后** ——
   * 它靠 `no-unused-vars: "off"` 把 base 规则关掉，换成 `@typescript-eslint/no-unused-vars`。
   * 顺序颠倒会让同一个未使用变量被报**两遍**。
   */
  rules: {
    ...js.configs.recommended.rules,
    ...tseslint.configs.recommended.rules,
    ...reactHooks.configs.recommended.rules,

    /*
     * 显式提级：`react-hooks/exhaustive-deps` 是**唯一**一条预设默认值为 `warn` 的规则
     * （插件作者认为它误报率偏高，所以保守设 warn）。其余 81 条预设默认就是 error。
     *
     * 提级的理由：本仓已经把它的 3 条存量清干净了，而且那 3 条**不是风格问题** ——
     * 都是「派生值每次渲染换新身份 → 依赖它的 `useMemo` 每帧重算」，
     * 属于**效能正确性**（memo 形同虚设）。这种问题该拦，不该只提示。
     */
    "react-hooks/exhaustive-deps": "error",
  },

  /*
   * ⚠️ 跑 `npm run lint` 时会看到一行提示：
   *     "The Next.js plugin was not detected in your ESLint configuration."
   *
   * 这是**刻意的**，不是配置漏了：加上 `eslint-config-next` 会引入一整套
   * Next 专属规则（`@next/next/*`），而用户对本步的规则集要求明确是
   * 「@typescript-eslint 基础集 + react-hooks」——**不引入新风格**。
   * 那行提示只是 Next 在提醒"我没参与 lint"，不影响检查结果（退出码 0）。
   * 若将来决定加 Next 规则，补 `extends: ['next']` 即可，届时该提示自动消失。
   */

  ignorePatterns: [
    "node_modules/",
    ".next/",
    "out/",
    // Next 自动生成，内容随构建变化，lint 它没有意义（官方模板也是排除的）
    "next-env.d.ts",
  ],

  /*
   * ⚠️ 以下不是"修 lint 报出的问题"，而是**基础配置本身** —— 属于
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
