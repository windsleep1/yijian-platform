/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 与 apps/admin 同款：不做 rewrite 代理 —— 前端**直连** API 才看得见真实的
  // CORS / 401 / trace_id 行为（生产由网关同域转发）。
  eslint: { ignoreDuringBuilds: true },

  /**
   * `/sw.js` 必须**每次在线取**，不能被 CDN / 浏览器缓存按住。
   *
   * 为什么值得专门加一条：SW 是一个**会长期驻留在用户设备上**的脚本，
   * 它的"更新"完全依赖浏览器去取新版本。如果这个文件被缓存住，症状是
   * **"我修好了、也部署了，但老用户跑的还是旧的那份"** —— 而且
   * 界面一切正常、控制台也不报错（我们刚在 `docs/27` 栽过同族的坑：
   * 折行的连接串让两条路解出两个主机，症状看着像 DNS 抽风）。
   *
   * ★ 为什么不靠"平台默认值"：Chrome 68+ 起**默认**已绕过 HTTP 缓存去取 SW 脚本
   *   （**这条我没有在本机验证过**，是文档里的行为），而 `public/` 下静态文件的响应头
   *   由**托管平台**决定（Vercel 的默认值我同样没有证据）。
   *   **不赌一个我验不了的默认行为** —— 显式写死，两种假设下都对。
   */
  async headers() {
    return [
      {
        source: "/sw.js",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
