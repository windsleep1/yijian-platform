"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request, uploadRequest } from "@/lib/api";
import type {
  ImportBatch,
  ImportBatchDetail,
  ImportChangeOut,
  ImportExecuteOut,
  ImportMode,
  ImportRollbackOut,
  ImportUploadOut,
  ListImportsQuery,
  Page,
} from "@/lib/types";

/**
 * 导入相关的 query key 集中在这里（与 `useQuestions` 同一套理由：
 * 散在组件里写字符串，早晚出现 `["import",id]` / `["imports",id]` 这种一字之差，
 * 表现是"改完不刷新"，而且不报错）。
 */
export const importKeys = {
  imports: (q: ListImportsQuery) => ["imports", q] as const,
  /** 前缀 key：`invalidateQueries({queryKey:["import"]})` 能一次带走详情与所有分页 */
  importAll: ["import"] as const,
  detail: (id: string, rowPage: number, rowPageSize: number) =>
    ["import", id, rowPage, rowPageSize] as const,
  changes: (id: string, page: number, pageSize: number) =>
    ["import-changes", id, page, pageSize] as const,
  changesAll: ["import-changes"] as const,
};

/**
 * 导入成功后要失效的 key。
 *
 * **必须带上 `questions` 与 `chapter-tree`**：导入会真的写题库，
 * 题目列表的题量与章节树的 `question_count` 都会变。
 * 漏掉的现象是"刚导完 100 道题，回题库列表还是旧数字"。
 */
function invalidateAfterImport(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ["imports"] });
  void qc.invalidateQueries({ queryKey: importKeys.importAll });
  void qc.invalidateQueries({ queryKey: importKeys.changesAll });
  void qc.invalidateQueries({ queryKey: ["questions"] });
  void qc.invalidateQueries({ queryKey: ["chapter-tree"] });
  void qc.invalidateQueries({ queryKey: ["audit-logs"] });
}

// ---------------------------------------------------------------- 读

/** 批次列表。翻页时保留上一页数据，避免表格闪白。 */
export function useImports(params: ListImportsQuery) {
  return useQuery({
    queryKey: importKeys.imports(params),
    queryFn: () => request<Page<ImportBatch>>("/admin/imports", { query: params }),
    placeholderData: keepPreviousData,
  });
}

/**
 * 批次详情。
 *
 * **处理中会自轮询**（`importing / validating / parsing` 每 2s 拉一次）：
 * 执行中的批次状态是会自己往前走的，让用户手点刷新才看到进展，是很糟的体验。
 * 到了终态（`done / failed / rolled_back`）就停 —— 别让一个已完成的页面
 * 在后台每 2 秒打一次接口。
 */
export function useImport(id: string | undefined, rowPage = 1, rowPageSize = 50) {
  return useQuery({
    queryKey: importKeys.detail(id ?? "", rowPage, rowPageSize),
    queryFn: () =>
      request<ImportBatchDetail>(`/admin/imports/${id}`, {
        query: { row_page: rowPage, row_page_size: rowPageSize },
      }),
    enabled: !!id,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "importing" || s === "validating" || s === "parsing" ? 2000 : false;
    },
  });
}

/** 批次变更日志（本批动了哪几道题、谁动的）。 */
export function useImportChanges(id: string | undefined, page = 1, pageSize = 50) {
  return useQuery({
    queryKey: importKeys.changes(id ?? "", page, pageSize),
    queryFn: () =>
      request<ImportChangeOut>(`/admin/imports/${id}/changes`, {
        query: { page, page_size: pageSize },
      }),
    enabled: !!id,
    placeholderData: keepPreviousData,
  });
}

// ---------------------------------------------------------------- 写

export type UploadImportVars = {
  file: File;
  /** ⚠️ 字符串。雪花 ID 过 `Number()` 就被舍入（见 lib/types.ts 的说明） */
  subject_id?: string;
  source_type: string;
  mode: ImportMode;
  license_note?: string;
};

/**
 * ① 上传（建批次）。**不写任何题**，只拿到 batch_id。
 *
 * 走 `uploadRequest`（multipart）而不是 `request`：
 * `Content-Type` 必须留给浏览器去补 `boundary`。
 */
export function useUploadImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: UploadImportVars) => {
      const form = new FormData();
      form.append("file", vars.file, vars.file.name);
      form.append("source_type", vars.source_type);
      form.append("mode", vars.mode);
      // 一律 String()：FormData 会把数字转成十进制字符串，但已经丢过精度的数字
      // 转出来仍是错的（375273861765140480 → 375273861765140500）。
      if (vars.subject_id) form.append("subject_id", String(vars.subject_id));
      if (vars.license_note) form.append("license_note", vars.license_note);
      return uploadRequest<ImportUploadOut>("/admin/imports/upload", form);
    },
    onSuccess: () => invalidateAfterImport(qc),
  });
}

/**
 * ② 逐行校验（dry-run，一行都不写库）。
 *
 * `id` 走**变量**而不是 hook 参数：向导页是"上传拿到 id → 立刻校验"的链式流程，
 * hook 创建时还没有 id（useState 还没更新）。绑定式 hook 在这里必然拿到空字符串，
 * 表现为"下一步点了没反应"或打到 `/admin/imports//validate`。
 */
export function useValidateImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      request<ImportBatchDetail>(`/admin/imports/${id}/validate`, { method: "POST" }),
    onSuccess: () => invalidateAfterImport(qc),
  });
}

/** ④ 执行导入（整批一个事务）。 */
export function useExecuteImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; publish?: boolean; allow_partial?: boolean }) => {
      const { id, ...body } = vars;
      return request<ImportExecuteOut>(`/admin/imports/${id}/execute`, { method: "POST", body });
    },
    onSuccess: () => invalidateAfterImport(qc),
  });
}

/** ⑤ 发布本批题目（draft → published）。 */
export function usePublishImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; include_duplicates?: boolean }) => {
      const { id, ...body } = vars;
      return request<ImportBatch>(`/admin/imports/${id}/publish`, { method: "POST", body });
    },
    onSuccess: () => invalidateAfterImport(qc),
  });
}

/** ⑥ 整批回滚（软删除本批新增 + 还原本批更新）。 */
export function useRollbackImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; reason?: string }) => {
      const { id, ...body } = vars;
      return request<ImportRollbackOut>(`/admin/imports/${id}/rollback`, { method: "POST", body });
    },
    onSuccess: () => invalidateAfterImport(qc),
  });
}
