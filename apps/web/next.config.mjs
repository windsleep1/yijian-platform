/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 与 apps/admin 同款：不做 rewrite 代理 —— 前端**直连** API 才看得见真实的
  // CORS / 401 / trace_id 行为（生产由网关同域转发）。
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
