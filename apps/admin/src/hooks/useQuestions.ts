"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request } from "@/lib/api";
import type {
  ChapterTreeOut,
  ListQuestionsQuery,
  Page,
  QuestionBatchDeleteIn,
  QuestionBatchDeleteOut,
  QuestionCreateIn,
  QuestionDeleteOut,
  QuestionDetail,
  QuestionListItem,
  QuestionUpdateIn,
} from "@/lib/types";

/**
 * 题库相关的 query key 集中在这里。
 *
 * 散在组件里写字符串字面量，早晚会出现
 * `["question", id]` 与 `["questions", id]` 这种一字之差 ——
 * 表现是"改完不刷新"，而且不报错，极难查。
 */
export const questionKeys = {
  questions: (q: ListQuestionsQuery) => ["questions", q] as const,
  question: (id: string) => ["question", id] as const,
  chapterTree: (subjectId: string) => ["chapter-tree", subjectId || "all"] as const,
};

/** 题目列表。翻页/改筛选时保留上一页数据，避免表格闪白。 */
export function useQuestions(params: ListQuestionsQuery) {
  return useQuery({
    queryKey: questionKeys.questions(params),
    queryFn: () => request<Page<QuestionListItem>>("/admin/questions", { query: params }),
    placeholderData: keepPreviousData,
  });
}

export function useQuestion(id: string | undefined) {
  return useQuery({
    queryKey: questionKeys.question(id ?? ""),
    queryFn: () => request<QuestionDetail>(`/admin/questions/${id}`),
    enabled: !!id,
  });
}

/**
 * 章节树。
 *
 * 不传 `subjectId` → 后端返回全部科目各一组（"科目"下拉 + 新增页的默认联动都靠它）。
 * 传了就只返回那一组（选中科目后刷新章节下拉）。
 */
export function useChapterTree(subjectId = "") {
  return useQuery({
    queryKey: questionKeys.chapterTree(subjectId),
    queryFn: () =>
      request<ChapterTreeOut>("/admin/chapters/tree", {
        query: subjectId ? { subject_id: subjectId } : undefined,
      }),
    // 科目/章节是低频变更的基础数据，比默认 staleTime 存久一点
    staleTime: 5 * 60_000,
  });
}

/**
 * 写操作成功后要失效的 key。
 *
 * **必须带上 `chapter-tree`**：章节树的 `question_count` 是按题目数实时统计的，
 * 新建/删除题目会改变它。漏掉这一步的现象是"刚加的题，章节下拉里的计数还是旧的"。
 */
function invalidateAfterWrite(qc: ReturnType<typeof useQueryClient>, id?: string) {
  void qc.invalidateQueries({ queryKey: ["questions"] });
  void qc.invalidateQueries({ queryKey: ["chapter-tree"] });
  void qc.invalidateQueries({ queryKey: ["audit-logs"] });
  if (id) void qc.invalidateQueries({ queryKey: ["question", id] });
}

/** 新建。后端直接返回新的 `QuestionDetail`（含 v1 快照），调用方可直接跳详情页。 */
export function useCreateQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: QuestionCreateIn) =>
      request<QuestionDetail>("/admin/questions", { method: "POST", body: payload }),
    onSuccess: (created) => invalidateAfterWrite(qc, created.id),
  });
}

/**
 * 编辑。
 *
 * 入参只带**变化的字段 + version**（由 `diffDraft` 算出来）。
 * `40901` 表示版本冲突 —— 那不是 bug，是"有人先改过"的正常反馈，
 * 页面要把这个情况说清楚并提示刷新（见 [id] 页的冲突分支）。
 */
export function useUpdateQuestion(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: QuestionUpdateIn) =>
      request<QuestionDetail>(`/admin/questions/${id}`, { method: "PUT", body: payload }),
    onSuccess: (updated) => invalidateAfterWrite(qc, updated.id),
  });
}

export function useDeleteQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      request<QuestionDeleteOut>(`/admin/questions/${id}`, {
        method: "DELETE",
        query: { reason },
      }),
    onSuccess: (_res, vars) => invalidateAfterWrite(qc, vars.id),
  });
}

export function useBatchDeleteQuestions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: QuestionBatchDeleteIn) =>
      request<QuestionBatchDeleteOut>("/admin/questions/batch-delete", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => invalidateAfterWrite(qc),
  });
}
