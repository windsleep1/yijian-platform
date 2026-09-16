"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { qk } from "@/hooks/useUsers";
import { request } from "@/lib/api";
import type { AuditLogItem, ListAuditLogsQuery, Page } from "@/lib/types";

/** 审计日志列表。需要 `system:audit`。 */
export function useAuditLogs(params: ListAuditLogsQuery) {
  return useQuery({
    queryKey: qk.auditLogs(params),
    queryFn: () => request<Page<AuditLogItem>>("/admin/audit-logs", { query: params }),
    placeholderData: keepPreviousData,
  });
}
