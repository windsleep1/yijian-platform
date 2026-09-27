import type { Config } from "tailwindcss";

/**
 * C 端的 Tailwind 配置 —— **与 `apps/admin` 刻意分开**（`docs/24` §2 末行）。
 *
 * 理由（用户 2026-09-26 定的口径）：**B 端密度 vs C 端 44px，约束是反的** ——
 * admin 追求"一屏塞下最多信息"，C 端追求"手指点得准"。
 * 共用一套间距/字号刻度会让两边互相拉扯，所以**只共用取舍思路，不共用文件**。
 *
 * ⚠️ 本文件**不做** `darkMode`：C 端首批没有深色模式需求，
 * 留着它就会有人开始写 `dark:` 类而没有对应的验收（"配了但没验"）。
 * 要做就单独一批，连同真机验证一起。
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        /** 语义色走 CSS 变量（`globals.css`），换主题不用改 class */
        ink: "hsl(var(--ink))",
        sub: "hsl(var(--sub))",
        line: "hsl(var(--line))",
        brand: {
          DEFAULT: "hsl(var(--brand))",
          fg: "hsl(var(--brand-fg))",
        },
        danger: "hsl(var(--danger))",
      },
      /** C 端专用刻度：**可点区域 ≥ 44px**（iOS HIG）/ 48dp（Material）。
       *  写成 token 而不是散在各页的 `h-12` —— 散着写就没法保证一致。 */
      minHeight: { touch: "44px" },
      minWidth: { touch: "44px" },
    },
  },
  plugins: [],
};

export default config;
