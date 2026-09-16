"use client";

import { useQuery } from "@tanstack/react-query";

import { qk } from "@/hooks/useUsers";
import { request } from "@/lib/api";
import type { PermissionTreeOut, RoleItem } from "@/lib/types";

/**
 * 角色列表（下拉 / 分配角色弹窗的数据源）。
 *
 * 需要 `user:read`。角色数据基本不变，`staleTime` 给长一点没问题；
 * 但 `invalidates` 依然可以随时强刷（例如管理员刚改了角色权限）。
 */
export function useRoles(includePermissions = true) {
  return useQuery({
    queryKey: qk.roles(includePermissions),
    queryFn: () =>
      request<RoleItem[]>("/admin/roles", { query: { include_permissions: includePermissions } }),
    staleTime: 5 * 60_000,
  });
}

/** 权限树。`module` 可只取一个模块。 */
export function usePermissionTree(module?: string) {
  return useQuery({
    queryKey: [...qk.permissions(), module ?? "all"],
    queryFn: () => request<PermissionTreeOut>("/admin/permissions", { query: { module } }),
    staleTime: 5 * 60_000,
  });
}
