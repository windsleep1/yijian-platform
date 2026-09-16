/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 后端默认在 8000（docker compose）。这里不做 rewrite 代理：
  // 前端直连 API 更容易看出真实的 CORS / 401 / trace_id 行为，
  // 生产则由网关同域转发。
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
