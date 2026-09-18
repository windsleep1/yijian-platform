"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request } from "@/lib/api";
import type {
  ExamAddQuestionsIn,
  ExamAddQuestionsOut,
  ExamComposeIn,
  ExamComposeOut,
  ExamCreateIn,
  ExamDetail,
  ExamListItem,
  ExamPublishIn,
  ExamPublishOut,
  ExamRemoveQuestionOut,
  ExamRestoreOut,
  ExamSectionsReplaceIn,
  ExamSectionsReplaceOut,
  ExamSoftDeleteOut,
  ExamUpdateIn,
  ExamValidateOut,
  ListExamsQuery,
  ListPaperRulesQuery,
  Page,
  PaperRuleOut,
} from "@/lib/types";

/**
 * 试卷 / 组卷的 query key 集中在这里。理由同 `useQuestions`：
 * 散着写字符串早晚出现 `["exam", id]` 与 `["exams", id]` 这种一字之差，
 * 表现是"改完不刷新"，且不报错。
 */
export const examKeys = {
  exams: (q: ListExamsQuery) => ["exams", q] as const,
  exam: (id: string) => ["exam", id] as const,
  paperRules: (q: ListPaperRulesQuery) => ["paper-rules", q] as const,
};

// ---------------------------------------------------------------- 查询

export function useExams(params: ListExamsQuery) {
  return useQuery({
    queryKey: examKeys.exams(params),
    queryFn: () => request<Page<ExamListItem>>("/admin/exams", { query: params }),
    placeholderData: keepPreviousData,
  });
}

export function useExam(id: string | undefined) {
  return useQuery({
    queryKey: examKeys.exam(id ?? ""),
    queryFn: () => request<ExamDetail>(`/admin/exams/${id}`),
    enabled: !!id,
  });
}

export function usePaperRules(params: ListPaperRulesQuery) {
  return useQuery({
    queryKey: examKeys.paperRules(params),
    queryFn: () => request<Page<PaperRuleOut>>("/admin/paper-rules", { query: params }),
    placeholderData: keepPreviousData,
  });
}

/**
 * 写操作后要失效的 key。
 *
 * ⚠️ **刻意不失效 `["exams"]` 列表** —— 归档 / 恢复走的是"就地反馈"模式
 * （停留当前视图 + 行标记 + 延时移除，见 `useRowActionFeedback`）。
 * 一旦在这里把列表失效掉，表格会立刻重新拉取：`include_deleted=true` 时
 * 刚恢复的那条本来就**还在**结果集里（它只是不再是"已归档"而已），
 * 于是刚标记完的行会**立刻原地复活**，反馈全被冲掉。
 *
 * 需要刷新列表时由页面在合适的时机显式调用（翻页、改筛选、手动刷新）。
 */
function invalidateAfterWrite(
  qc: ReturnType<typeof useQueryClient>,
  id?: string,
  opts: { list?: boolean } = {},
) {
  if (opts.list) void qc.invalidateQueries({ queryKey: ["exams"] });
  void qc.invalidateQueries({ queryKey: ["audit-logs"] });
  if (id) void qc.invalidateQueries({ queryKey: ["exam", id] });
}

// ---------------------------------------------------------------- 试卷 CRUD

export function useCreateExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExamCreateIn) =>
      request<ExamDetail>("/admin/exams", { method: "POST", body: payload }),
    // 新建会改变列表，这里就该失效（没有"就地反馈"的问题）
    onSuccess: (created) => invalidateAfterWrite(qc, created.id, { list: true }),
  });
}

export function useUpdateExam(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExamUpdateIn) =>
      request<ExamDetail>(`/admin/exams/${id}`, { method: "PUT", body: payload }),
    onSuccess: () => invalidateAfterWrite(qc, id, { list: true }),
  });
}

/**
 * **重建卷面结构**（唯一入口）。
 *
 * ⚠️ 会清空这张卷现有的全部卷面题。调用方**必须**从详情里取 `question_count`
 * 作为 `expected_question_count` 传进来 —— 后端据此做乐观并发校验，
 * 对不上就 `40901`（"卷面已变化，请刷新后重试"）且不写库。
 */
export function useReplaceExamSections(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExamSectionsReplaceIn) =>
      request<ExamSectionsReplaceOut>(`/admin/exams/${id}/sections`, { method: "PUT", body: payload }),
    onSuccess: () => invalidateAfterWrite(qc, id, { list: true }),
  });
}

/** 归档（软删除）。**不失效列表**，交由 `useRowActionFeedback` 做就地反馈。 */
export function useArchiveExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      request<ExamSoftDeleteOut>(`/admin/exams/${id}`, { method: "DELETE", query: { reason } }),
    onSuccess: (_res, vars) => invalidateAfterWrite(qc, vars.id),
  });
}

/**
 * 恢复（解除归档）。**不失效列表** —— 同上。
 *
 * 幂等：本来就未归档时后端返回 `code=0` + `already_active=true`，
 * 调用方据此提示"无需恢复"而不是报错。
 */
export function useRestoreExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      request<ExamRestoreOut>(`/admin/exams/${id}/restore`, { method: "POST" }),
    onSuccess: (_res, vars) => invalidateAfterWrite(qc, vars.id),
  });
}

// ---------------------------------------------------------------- 组卷 / 校验 / 发布

export function useComposeExam(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExamComposeIn) =>
      request<ExamComposeOut>(`/admin/exams/${id}/auto-compose`, { method: "POST", body: payload }),
    // 组卷改变的是整个卷面（题数/总分/分段），不是"某一行"，
    // 所以要正常失效列表与详情 —— 这里没有就地反馈的诉求。
    onSuccess: () => invalidateAfterWrite(qc, id, { list: true }),
  });
}

/**
 * 卷面校验。**是 POST 但语义只读**（不改数据），所以作为 mutation 用：
 * 它由按钮显式触发、结果要就地展示，且不该被 React Query 的缓存策略左右。
 */
export function useValidateExam(id: string) {
  return useMutation({
    mutationFn: () => request<ExamValidateOut>(`/admin/exams/${id}/validate`, { method: "POST" }),
  });
}

export function usePublishExam(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExamPublishIn) =>
      request<ExamPublishOut>(`/admin/exams/${id}/publish`, { method: "POST", body: payload }),
    onSuccess: () => invalidateAfterWrite(qc, id, { list: true }),
  });
}

// ---------------------------------------------------------------- 加题 / 移题

export function useAddExamQuestions(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExamAddQuestionsIn) =>
      request<ExamAddQuestionsOut>(`/admin/exams/${id}/questions`, { method: "POST", body: payload }),
    onSuccess: () => invalidateAfterWrite(qc, id, { list: true }),
  });
}

export function useRemoveExamQuestion(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ examQuestionId }: { examQuestionId: string }) =>
      request<ExamRemoveQuestionOut>(`/admin/exams/${id}/questions/${examQuestionId}`, {
        method: "DELETE",
      }),
    onSuccess: () => invalidateAfterWrite(qc, id, { list: true }),
  });
}

// ---------------------------------------------------------------- 组卷规则（2b 会用，先就位）

export function useCreatePaperRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: unknown) =>
      request<PaperRuleOut>("/admin/paper-rules", { method: "POST", body: payload }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["paper-rules"] }),
  });
}

export function useUpdatePaperRule(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: unknown) =>
      request<PaperRuleOut>(`/admin/paper-rules/${id}`, { method: "PUT", body: payload }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["paper-rules"] }),
  });
}

export function useDeletePaperRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      request<{ id: string; name: string; hard_deleted: boolean; message: string }>(
        `/admin/paper-rules/${id}`,
        { method: "DELETE" },
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["paper-rules"] }),
  });
}
