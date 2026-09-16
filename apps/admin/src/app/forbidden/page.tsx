import type { Metadata } from "next";

import { ForbiddenState } from "@/components/ForbiddenState";

export const metadata: Metadata = { title: "无访问权限 · 一建通后台" };

/**
 * 403 落地页。
 *
 * 存在的理由：没有它，权限不足就只能"白屏"或"回首页"。
 * B 端必须有一个能明确说明"你缺什么权限、该找谁"的页面 ——
 * 这样员工不会来问开发，直接找管理员即可。
 */
export default function ForbiddenPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-lg rounded-lg border bg-card">
        <ForbiddenState />
      </div>
    </div>
  );
}
