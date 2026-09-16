"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request } from "@/lib/api";
import type {
  AdminUserDetail,
  AdminUserItem,
  AssignRolesIn,
  AssignRolesOut,
  ListUsersQuery,
  Page,
} from "@/lib/types";

/** query key 集中定义，避免散落各处写错字符串导致缓存失效不生效。 */
export const qk = {
  users: (q: ListUsersQuery) => ["users", q] as const,
  user: (id: string) => ["user", id] as const,
  roles: (withPerms: boolean) => ["roles", withPerms] as const,
  permissions: () => ["permissions"] as const,
  auditLogs: (q: unknown) => ["audit-logs", q] as const,
};

export function useUsers(params: ListUsersQuery) {
  return useQuery({
    queryKey: qk.users(params),
    queryFn: () => request<Page<AdminUserItem>>("/admin/users", { query: params }),
    // 翻页/改筛选时保留上一页数据，避免表格闪成骨架屏。
    // 这属于"体感"优化：翻页时旧数据在位 + 轻微 loading，比整块白屏舒服得多。
    placeholderData: keepPreviousData,
  });
}

export function useUser(id: string | undefined) {
  return useQuery({
    queryKey: qk.user(id ?? ""),
    queryFn: () => request<AdminUserDetail>(`/admin/users/${id}`),
    enabled: !!id,
  });
}

/**
 * 分配角色（整体替换）。
 *
 * 成功后必须 **invalidate 三个 key**：
 *   - `user(id)`  详情页的角色/权限块变了
 *   - `users`     列表里的角色标签变了
 *   - `audit-logs` 这次操作刚写进去一条审计
 * 少做这一步的典型现象：改完角色回到列表，还是旧的标签，用户以为没保存成功。
 */
export function useAssignRoles(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AssignRolesIn) =>
      request<AssignRolesOut>(`/admin/users/${userId}/roles`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["user", userId] });
      void qc.invalidateQueries({ queryKey: ["users"] });
      void qc.invalidateQueries({ queryKey: ["audit-logs"] });
    },
  });
}
