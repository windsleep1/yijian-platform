"use client";

import {
  Archive,
  ArchiveRestore,
  CheckCircle2,
  ChevronRight,
  Eye,
  FileText,
  Info,
  Lock,
  Pencil,
  Plus,
  Send,
  ShieldCheck,
  Trash2,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AddQuestionsDialog } from "@/components/AddQuestionsDialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ExamBasicInfoDialog } from "@/components/ExamBasicInfoDialog";
import { ExamSectionsDialog } from "@/components/ExamSectionsDialog";
import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { RowActionMarker } from "@/components/RowActionMarker";
import { ValidateReport } from "@/components/ValidateReport";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DataTableSkeleton } from "@/components/DataTableSkeleton";
import {
  useArchiveExam,
  useExam,
  usePublishExam,
  useRemoveExamQuestion,
  useRestoreExam,
  useValidateExam,
} from "@/hooks/useExams";
import { useRowActionFeedback } from "@/hooks/useRowActionFeedback";
import { useAuth } from "@/lib/auth-context";
import {
  EXAM_STATUS_LABELS,
  examStatusLabel,
  examStatusVariant,
  examTypeLabel,
  sectionGap,
  shortfallSummary,
} from "@/lib/exam";
import { formatDateMinute } from "@/lib/format";
import { P } from "@/lib/permission";
import { difficultyLabel, qTypeLabel } from "@/lib/question";
import type { ExamDetail, ExamQuestionItem, ExamValidateOut } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * 试卷详情 / 编辑。
 *
 * 页面结构对应"一条从校验到发布的路径"，顺序即操作顺序：
 *
 *   基本信息 → （缺口警告）→ 卷面结构 → 校验报告 → 发布
 *
 * 几个刻意的设计：
 *
 * - **缺口与校验报告都放在卷面上面**，而不是折叠起来。卷面 60 道题的列表很长，
 *   把"这张卷还不能发"的信息放在下面等于没有。
 * - **发布按钮的禁用原因是可见的**（缺哪个权限 / 校验没过几条），
 *   而不是一个点不动的灰按钮 —— 见 `docs/B端联调坑.md` 第 30 条。
 * - **锁定版本不是只显示 `locked_version` 数字**：只有它和当前版本不同时
 *   才显示"已锁定至 vX（当前 vY）"，并给出**为什么不用管**的解释
 *   —— 否则教研看到"版本漂移"会以为卷子坏了。
 */
export default function ExamDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const router = useRouter();
  const { hasPermission, isLoading: authLoading } = useAuth();

  const query = useExam(id);
  const validate = useValidateExam(id ?? "");
  const publish = usePublishExam(id ?? "");
  const archive = useArchiveExam();
  const restore = useRestoreExam();
  const removeQuestion = useRemoveExamQuestion(id ?? "");
  const fb = useRowActionFeedback();

  const [addOpen, setAddOpen] = useState(false);
  const [basicOpen, setBasicOpen] = useState(false);
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [allowEdit, setAllowEdit] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<{ eq: ExamQuestionItem; title: string } | null>(null);

  const exam = query.data;

  // 校验结果：优先用本次点出来的，其次用详情里内联的那份
  const validation = validate.data ?? exam?.validation ?? null;

  /**
   * 进页面自动跑一次校验。
   *
   * 让"能不能发布"在用户动手之前就是已知的 —— 而不是点了发布才发现过不去。
   * 依赖用 `[id]`：详情 refetch 不该反复触发（那是无谓的请求）。
   */
  useEffect(() => {
    if (!id) return;
    validate.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (authLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <DataTableSkeleton rows={6} cols={4} />
      </div>
    );
  }
  if (!hasPermission(P.examRead)) return <ForbiddenState need={P.examRead} />;

  if (query.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <DataTableSkeleton rows={6} cols={4} />
      </div>
    );
  }
  if (query.error) {
    return (
      <div className="rounded-lg border bg-card">
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  if (!exam) return null;

  return (
    <ExamDetailBody
      exam={exam}
      validation={validation}
      validating={validate.isPending}
      onValidate={() => validate.mutate()}
      publish={publish}
      archive={archive}
      restore={restore}
      removeQuestion={removeQuestion}
      fb={fb}
      addOpen={addOpen}
      setAddOpen={setAddOpen}
      basicOpen={basicOpen}
      setBasicOpen={setBasicOpen}
      sectionsOpen={sectionsOpen}
      setSectionsOpen={setSectionsOpen}
      publishOpen={publishOpen}
      setPublishOpen={setPublishOpen}
      allowEdit={allowEdit}
      setAllowEdit={setAllowEdit}
      archiveOpen={archiveOpen}
      setArchiveOpen={setArchiveOpen}
      restoreOpen={restoreOpen}
      setRestoreOpen={setRestoreOpen}
      removeTarget={removeTarget}
      setRemoveTarget={setRemoveTarget}
      router={router}
      hasPermission={hasPermission}
      refresh={() => void query.refetch()}
    />
  );
}

type BodyProps = {
  exam: ExamDetail;
  validation: ExamValidateOut | null;
  validating: boolean;
  onValidate: () => void;
  publish: ReturnType<typeof usePublishExam>;
  archive: ReturnType<typeof useArchiveExam>;
  restore: ReturnType<typeof useRestoreExam>;
  removeQuestion: ReturnType<typeof useRemoveExamQuestion>;
  fb: ReturnType<typeof useRowActionFeedback>;
  addOpen: boolean;
  setAddOpen: (v: boolean) => void;
  basicOpen: boolean;
  setBasicOpen: (v: boolean) => void;
  sectionsOpen: boolean;
  setSectionsOpen: (v: boolean) => void;
  publishOpen: boolean;
  setPublishOpen: (v: boolean) => void;
  allowEdit: boolean;
  setAllowEdit: (v: boolean) => void;
  archiveOpen: boolean;
  setArchiveOpen: (v: boolean) => void;
  restoreOpen: boolean;
  setRestoreOpen: (v: boolean) => void;
  removeTarget: { eq: ExamQuestionItem; title: string } | null;
  setRemoveTarget: (v: { eq: ExamQuestionItem; title: string } | null) => void;
  router: ReturnType<typeof useRouter>;
  hasPermission: (code: string) => boolean;
  refresh: () => void;
};

function ExamDetailBody(props: BodyProps) {
  const { exam, validation, validating, onValidate, publish, archive, restore, removeQuestion, fb } = props;
  const { hasPermission } = props;

  const canCreate = hasPermission(P.examCreate);
  const canPublish = hasPermission(P.examPublish);
  const frozen = exam.status === "published" && !exam.can_edit;

  const errors = validation?.errors.length ?? 0;
  const warnings = validation?.warnings.length ?? 0;
  const publishBlocked = errors > 0;

  /** 立即就地刷新的两个动作（组卷/加题/移题走的是正常失效，不在这里） */
  const refreshBoth = () => {
    props.refresh();
    onValidate();
  };

  return (
    <>
      <PageHeader
        title={exam.title}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge variant={examStatusVariant(exam.status)}>{examStatusLabel(exam.status)}</Badge>
            <span>{examTypeLabel(exam.type)}</span>
            <span>·</span>
            <span>{exam.subject_name ?? `#${exam.subject_id}`}</span>
            <span>·</span>
            <span className="yj-json">
              {exam.question_count} 题 / {exam.total_score} 分 / {exam.duration_min} 分钟
            </span>
            {exam.is_deleted ? (
              <Badge variant="destructive" className="gap-1">
                <Archive className="h-3 w-3" />
                已归档
              </Badge>
            ) : null}
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/exams">
                <Undo2 className="h-3.5 w-3.5" />
                返回列表
              </Link>
            </Button>

            {/* 校验：只读，viewer 也能点（页面上的"校验"是 exam:read 语义） */}
            <Button variant="outline" size="sm" disabled={validating} onClick={onValidate}>
              <ShieldCheck className="h-3.5 w-3.5" />
              {validating ? "校验中…" : "校验卷面"}
            </Button>

            {/* 恢复：只在已归档时出现 */}
            {exam.is_deleted ? (
              <Gate
                allowed={canPublish}
                need={P.examPublish}
                hint="已发布的卷恢复后立刻重新对外可见，所以按发布对待"
              >
                <Button variant="default" size="sm" disabled={!canPublish} onClick={() => props.setRestoreOpen(true)}>
                  <ArchiveRestore className="h-3.5 w-3.5" />
                  恢复
                </Button>
              </Gate>
            ) : (
              <>
                <Gate allowed={canCreate} need={P.examCreate} hint="加题 / 改卷面结构都要它">
                  <Button variant="outline" size="sm" disabled={!canCreate || frozen} onClick={() => props.setAddOpen(true)}>
                    <Plus className="h-3.5 w-3.5" />
                    加题
                  </Button>
                </Gate>

                {/* 发布：禁用原因必须可见 */}
                <Gate
                  allowed={canPublish}
                  need={P.examPublish}
                  hint="发布需要 exam:publish 权限"
                >
                  <Button
                    size="sm"
                    disabled={!canPublish || frozen || publishBlocked}
                    onClick={() => props.setPublishOpen(true)}
                    title={
                      !canPublish
                        ? `需要 ${P.examPublish} 权限`
                        : frozen
                          ? "该卷已发布"
                          : publishBlocked
                            ? `校验未通过（${errors} 个必须修的问题），修好才能发布`
                            : undefined
                    }
                  >
                    <Send className="h-3.5 w-3.5" />
                    发布
                  </Button>
                </Gate>

                <Gate allowed={canCreate} need={P.examCreate} hint="归档复用 exam:create">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground hover:text-destructive"
                    disabled={!canCreate}
                    onClick={() => props.setArchiveOpen(true)}
                  >
                    <Archive className="h-3.5 w-3.5" />
                    归档
                  </Button>
                </Gate>
              </>
            )}
          </div>
        }
      />

      {exam.is_deleted ? (
        <div className="mb-4 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <p className="font-medium text-destructive">这份试卷已归档，不在默认列表里显示。</p>
          <p className="mt-1 text-muted-foreground">
            题目与卷面数据都保留。点右上角「恢复」可以让它重新出现在默认列表。
          </p>
        </div>
      ) : null}

      {/* ---------------- ① 基本信息 ---------------- */}
      <section className="mb-4 rounded-lg border bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <FileText className="h-4 w-4 text-muted-foreground" />
            基本信息
          </h2>
          <Gate allowed={canCreate} need={P.examCreate} hint="编辑基本信息需要 exam:create">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2"
              disabled={!canCreate || exam.is_deleted}
              onClick={() => props.setBasicOpen(true)}
            >
              <Pencil className="h-3.5 w-3.5" />
              编辑
            </Button>
          </Gate>
        </div>

        <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <Field label="卷号" value={exam.paper_no ?? "—"} mono />
          <Field label="考试年份" value={exam.exam_year ? `${exam.exam_year} 年` : "—"} />
          <Field label="及格分" value={exam.pass_score ? String(exam.pass_score) : "未设置"} />
          <Field label="是否免费" value={exam.is_free ? "免费" : "需购买"} />
          <Field label="创建人" value={exam.created_by_name ?? "—"} />
          <Field label="创建时间" value={formatDateMinute(exam.created_at)} mono />
          <Field label="更新时间" value={formatDateMinute(exam.updated_at)} mono />
          <Field
            label="发布时间"
            value={exam.published_at ? formatDateMinute(exam.published_at) : "未发布"}
            mono
          />
        </div>
      </section>

      {/* ---------------- ② 上次组卷的缺口（绝不静默补题） ---------------- */}
      {exam.shortfalls.length > 0 ? (
        <section className="mb-4 overflow-hidden rounded-lg border border-amber-300">
          <div className="bg-amber-50 px-4 py-2">
            <p className="text-xs font-medium text-amber-800">
              上次组卷存在缺口（系统**不会自动凑数**，缺口如实报出来）
            </p>
            <p className="mt-0.5 text-xs text-amber-800">{shortfallSummary(exam.shortfalls)}</p>
          </div>
          <ul className="divide-y">
            {exam.shortfalls.map((s) => (
              <li key={`${s.rule_index}-${s.question_type}`} className="flex flex-wrap items-center gap-x-3 px-4 py-2 text-xs">
                <span className="font-medium">{s.rule_label}</span>
                <span className="text-muted-foreground">{s.reason}</span>
                <span className="yj-json ml-auto whitespace-nowrap text-muted-foreground">
                  需要 <strong className="text-foreground">{s.need}</strong> · 抽到{" "}
                  <strong className="text-foreground">{s.got}</strong> · 缺{" "}
                  <strong className="text-destructive">{s.missing}</strong>
                </span>
              </li>
            ))}
          </ul>
          <p className="border-t bg-muted/40 px-4 py-2 text-[11px] text-muted-foreground">
            补齐方式：放宽该规则的难度 / 年份 / 知识点条件后重新组卷，或直接用「加题」手动补。
          </p>
        </section>
      ) : null}

      {/* ---------------- ③ 校验报告（结构化，每条带下一步） ---------------- */}
      <section className="mb-4">
        <h2 className="mb-2 flex items-center gap-2 text-sm font-medium">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          卷面校验
          {warnings > 0 ? (
            <span className="text-xs font-normal text-amber-700">{warnings} 条提醒</span>
          ) : null}
        </h2>
        <ValidateReport validation={validation} sections={exam.sections} />
      </section>

      {/* ---------------- ④ 卷面结构 ---------------- */}
      <section className="mb-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
            卷面结构
            <span className="text-xs font-normal text-muted-foreground">
              {exam.sections.length} 个分段 · {exam.question_count} 题
            </span>
          </h2>
          <Gate allowed={canCreate} need={P.examCreate} hint="改卷面结构需要 exam:create">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2"
              disabled={!canCreate || frozen || exam.is_deleted}
              onClick={() => props.setSectionsOpen(true)}
              title={frozen ? "已发布的卷卷面结构不可改" : "会清空卷面题目，需二次确认"}
            >
              <Pencil className="h-3.5 w-3.5" />
              编辑卷面结构
            </Button>
          </Gate>
        </div>

        {exam.sections.length === 0 ? (
          <div className="rounded-lg border bg-card">
            <EmptyState
              title="还没有分段"
              description="分段声明了「哪个题型、多少道、每题多少分」，是卷面的骨架。先编辑卷面结构加一个分段，再往里加题。"
            />
          </div>
        ) : (
          <div className="space-y-3">
            {exam.sections.map((s) => {
              const gap = sectionGap(s);
              const bad = gap.state !== "ok";
              return (
                <div
                  key={s.id}
                  className={cn(
                    "overflow-hidden rounded-lg border",
                    // 用户要求：计划 vs 实际对不上时**整段标红**
                    bad && "border-destructive/50",
                  )}
                >
                  <div
                    className={cn(
                      "flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2",
                      bad ? "bg-destructive/10" : "bg-muted/40",
                    )}
                  >
                    <span className={cn("text-sm font-medium", bad && "text-destructive")}>
                      {s.name}
                    </span>
                    <Badge variant="outline" className="text-[10px]">
                      {qTypeLabel(s.question_type)}
                    </Badge>
                    <span
                      className={cn("text-xs", bad ? "font-medium text-destructive" : "text-muted-foreground")}
                    >
                      {/* 一句人话，不是干巴巴的 -8 */}
                      {gap.text}
                    </span>
                    <span className="yj-json ml-auto whitespace-nowrap text-xs text-muted-foreground">
                      每题 {s.score_per} 分 · 本段 {s.actual_score} 分
                    </span>
                  </div>

                  {s.questions.length === 0 ? (
                    <p className="px-4 py-3 text-xs text-muted-foreground">
                      这个分段还没有题。点右上角「加题」从题库里挑，
                      {(exam.shortfalls.length > 0) && "或先解决上面的组卷缺口。"}
                    </p>
                  ) : (
                    <ul className="divide-y">
                      {s.questions.map((q) => (
                        <QuestionRow
                          key={q.id}
                          q={q}
                          feedback={fb.feedbackOf(q.id)}
                          canEdit={canCreate && !frozen && !exam.is_deleted}
                          onRemove={() => props.setRemoveTarget({ eq: q, title: s.name })}
                        />
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ---------------- 弹窗们 ---------------- */}
      <AddQuestionsDialog
        open={props.addOpen}
        onOpenChange={props.setAddOpen}
        exam={exam}
        onAdded={() => {
          // 加题改变卷面 → 关掉面板后刷新详情并重跑校验
          refreshBoth();
        }}
      />

      <ExamBasicInfoDialog open={props.basicOpen} onOpenChange={props.setBasicOpen} exam={exam} />

      <ExamSectionsDialog open={props.sectionsOpen} onOpenChange={props.setSectionsOpen} exam={exam} />

      {/* ---- 发布确认 ---- */}
      <ConfirmDialog
        open={props.publishOpen}
        onOpenChange={(o) => {
          props.setPublishOpen(o);
          if (!o) props.setAllowEdit(false);
        }}
        title="确认发布这份试卷？"
        loading={publish.isPending}
        confirmText="发布"
        description={
          <div className="space-y-3 text-sm">
            <p>
              将发布 <strong>{exam.title}</strong>：卷面{" "}
              <strong>{exam.question_count}</strong> 道题、合计{" "}
              <strong>{exam.total_score}</strong> 分。
            </p>
            <p className="text-xs text-muted-foreground">
              发布时会给卷面每一道题<strong>锁定当前版本</strong>
              （写入 `exam_questions.locked_version`）。
              之后即使有人在题库里改了这些题，考生看到的仍是发布时那一版。
            </p>
            {warnings > 0 ? (
              <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-800">
                本次校验有 {warnings} 条提醒（不影响发布），可在上方「卷面校验」里查看。
              </p>
            ) : null}
            <label className="flex items-start gap-2 text-xs">
              <Checkbox
                checked={props.allowEdit}
                onCheckedChange={(v) => props.setAllowEdit(v === true)}
                className="mt-0.5"
              />
              <span>
                允许发布后编辑。
                <span className="text-muted-foreground">
                  {" "}
                  勾选后这张卷可以在发布状态下继续改卷面 —— 修改会影响正在进行的考试，
                  通常不建议。
                </span>
              </span>
            </label>
          </div>
        }
        onConfirm={async () => {
          const res = await publish.mutateAsync({ allow_edit_after_publish: props.allowEdit });
          props.setPublishOpen(false);
          props.setAllowEdit(false);
          await fb.run({
            id: exam.id,
            doneLabel: "已发布",
            action: async () => res,
            errorTitle: "发布失败",
            successToast: () => ({
              title: `已发布「${exam.title}」`,
              description: `已锁定 ${res.locked_versions} 道题的版本。`,
              actionLabel: "查看列表",
              onAction: () => props.router.push("/exams"),
            }),
          });
          refreshBoth();
        }}
      />

      {/* ---- 归档确认 ---- */}
      <ConfirmDialog
        open={props.archiveOpen}
        onOpenChange={props.setArchiveOpen}
        title={`确认归档「${exam.title}」？`}
        destructive
        loading={archive.isPending}
        confirmText="归档这份试卷"
        description={
          <div className="space-y-2 text-sm">
            <p>将把这份试卷归档（软删除），它不再出现在默认列表里。</p>
            {exam.status === "published" ? (
              <p className="text-amber-700">
                ⚠️ 这份卷当前是<strong>已发布</strong>状态 —— 归档后不应再用于考试。
              </p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              题目与卷面数据<strong>都会保留</strong>，本页仍可打开；
              在列表页打开「显示已归档的试卷」就能找到并恢复。
            </p>
          </div>
        }
        onConfirm={async () => {
          props.setArchiveOpen(false);
          const res = await archive.mutateAsync({ id: exam.id, reason: "详情页归档" });
          await fb.run({
            id: exam.id,
            doneLabel: "已归档",
            action: async () => res,
            retryable: true,
            errorTitle: "归档失败",
            successToast: () => ({
              title: `已归档「${exam.title}」`,
              description: "题目与卷面都保留，可随时恢复。",
              actionLabel: "查看已归档",
              onAction: () => props.router.push("/exams?include_deleted=true"),
            }),
          });
          props.refresh();
        }}
      />

      {/* ---- 恢复确认 ---- */}
      <ConfirmDialog
        open={props.restoreOpen}
        onOpenChange={props.setRestoreOpen}
        title={`确认恢复「${exam.title}」？`}
        loading={restore.isPending}
        confirmText="恢复这份试卷"
        description={
          <div className="space-y-2 text-sm">
            <p>
              将把这份试卷从归档状态恢复，它立刻重新出现在默认列表里。
            </p>
            <p className="text-xs text-muted-foreground">
              恢复<strong>不会改变试卷状态</strong>（仍是「
              {EXAM_STATUS_LABELS[exam.status]}」）
              ，也不会改动任何题目。
            </p>
          </div>
        }
        onConfirm={async () => {
          props.setRestoreOpen(false);
          const res = await restore.mutateAsync({ id: exam.id });
          await fb.run({
            id: exam.id,
            doneLabel: "已恢复",
            action: async () => res,
            retryable: true,
            errorTitle: "恢复失败",
            successToast: (r) => ({
              title: `已恢复「${exam.title}」`,
              description: r.already_active ? "这份卷本来就没有被归档。" : r.message,
              actionLabel: "查看默认列表",
              onAction: () => props.router.push("/exams"),
            }),
          });
          props.refresh();
        }}
      />

      {/* ---- 移题确认 ---- */}
      <ConfirmDialog
        open={!!props.removeTarget}
        onOpenChange={(o) => !o && props.setRemoveTarget(null)}
        title="确认把这道题移出卷面？"
        destructive
        loading={
          props.removeTarget ? fb.feedbackOf(props.removeTarget.eq.id)?.phase === "pending" : false
        }
        confirmText="移出卷面"
        description={
          <div className="space-y-2 text-sm">
            {props.removeTarget ? (
              <>
                <p className="line-clamp-3 text-muted-foreground">
                  {props.removeTarget.eq.stem_preview || `#${props.removeTarget.eq.question_id}`}
                </p>
                <p>
                  将把这道题从<strong>「{props.removeTarget.title}」</strong>分段移出卷面。
                  <strong>题目本身在题库里不受影响。</strong>
                </p>
                <p className="text-xs text-amber-700">
                  ⚠️ 移出后该分段的计划题数<strong>不会自动变小</strong>
                  （计划 {props.removeTarget.eq.score} 分不变），
                  校验会提示分段的题数与计划不符 —— 由你决定补一道题，
                  还是把计划题数改成实际值。
                </p>
              </>
            ) : null}
          </div>
        }
        onConfirm={async () => {
          const target = props.removeTarget;
          if (!target) return;
          props.setRemoveTarget(null);
          await fb.run({
            id: target.eq.id,
            doneLabel: "已移出",
            action: () => removeQuestion.mutateAsync({ examQuestionId: target.eq.id }),
            // 移题不幂等（再移一次是 40401），所以不给重试键
            retryable: false,
            errorTitle: "移出失败",
            successToast: () => ({
              title: "已移出卷面",
              description: "题目仍在题库中，可以随时重新加回来。",
            }),
          });
          refreshBoth();
        }}
      />
    </>
  );
}

// ================================================================ 小件

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("truncate", mono && "yj-json")} title={value}>
        {value}
      </div>
    </div>
  );
}

/**
 * 没有权限时的"禁用 + 说明"包装。
 *
 * 两件事一起做：`disabled` 让点击无效，`Tooltip` 说清楚**缺哪个权限、找谁开通**。
 * ⚠️ 必须用 `<span>` 兜住 —— disabled 的元素不派发鼠标事件，
 * 直接给 Button 套 TooltipTrigger 是收不到 hover 的（坑 30）。
 */
function Gate({
  allowed,
  need,
  hint,
  children,
}: {
  allowed: boolean;
  need: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{children}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        {allowed ? (
          hint
        ) : (
          <>
            需要权限 <code className="yj-json">{need}</code>。请联系系统管理员开通。
          </>
        )}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * 卷面里的一行题。
 *
 * 锁定版本的展示规则（本页最需要想清楚的地方）：
 *
 * | 情况 | 展示 |
 * |---|---|
 * | 未发布（`locked_version` 为 null） | 「未锁定」 |
 * | 已锁定且未漂移 | 「锁定 v1」+ 锁图标 |
 * | **已锁定但漂移** | 「**已锁定至 v1（当前 v2）**」+ hover 说明 |
 *
 * 漂移的那一版**刻意不摆出"差异"**：题干预览 `stem_preview` 来自
 * **锁定版本**（后端就是这么返回的），当前版本的题干在这个接口里拿不到 ——
 * 没有真数据就不画假 diff，只把"哪里能看到当前版本"指出来。
 * 真正的逐字对比需要一个"版本对比"接口，本批没有做（记在 docs/13 §7）。
 */
function QuestionRow({
  q,
  feedback,
  canEdit,
  onRemove,
}: {
  q: ExamQuestionItem;
  feedback: ReturnType<ReturnType<typeof useRowActionFeedback>["feedbackOf"]>;
  canEdit: boolean;
  onRemove: () => void;
}) {
  const drift = q.version_drift && q.locked_version != null;
  return (
    <li className="flex items-start gap-3 px-4 py-2">
      <span className="yj-json mt-0.5 w-8 shrink-0 text-xs text-muted-foreground">{q.seq}</span>
      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 text-sm leading-relaxed">{q.stem_preview || "（无题干预览）"}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
          <Badge variant="outline" className="text-[10px]">
            {qTypeLabel(q.question_type)}
          </Badge>
          {q.difficulty != null ? (
            <span className="text-muted-foreground">{difficultyLabel(q.difficulty)}</span>
          ) : null}
          <span className="yj-json text-muted-foreground">{q.score} 分</span>

          {drift ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="warning" className="cursor-help gap-1 text-[10px]">
                  <Lock className="h-3 w-3" />
                  已锁定至 v{q.locked_version}（当前 v{q.current_version}）
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm space-y-1.5">
                <p className="text-xs">
                  这道题在<strong>发布之后被改过</strong>：考生作答与展示用的是锁定版
                  <code className="yj-json mx-1">v{q.locked_version}</code>，
                  题库里现在已是
                  <code className="yj-json mx-1">v{q.current_version}</code>。
                </p>
                <p className="text-xs text-muted-foreground">
                  上面显示的就是<strong>锁定版本的题干</strong>，所以不需要处理。
                  想看当前版本请到题库详情页。
                </p>
                <Link
                  href={`/questions/${q.question_id}`}
                  className="inline-block text-xs text-primary hover:underline"
                >
                  查看该题的当前版本 →
                </Link>
              </TooltipContent>
            </Tooltip>
          ) : q.locked_version != null ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="success" className="cursor-help gap-1 text-[10px]">
                  <Lock className="h-3 w-3" />
                  锁定 v{q.locked_version}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs text-xs">
                发布时锁定的版本，与题库当前版本一致。
              </TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="cursor-help text-muted-foreground">未锁定版本</span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs text-xs">
                这份试卷还没有发布 —— 发布时才会把每道题的当前版本锁进卷面。
              </TooltipContent>
            </Tooltip>
          )}

          <RowActionMarker feedback={feedback} />
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button variant="ghost" size="sm" className="h-7 px-2" asChild>
          <Link href={`/questions/${q.question_id}`}>
            <Eye className="h-3.5 w-3.5" />
            题目
          </Link>
        </Button>
        <Gate allowed={canEdit} need={P.examCreate} hint="已发布的卷不能移题">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-muted-foreground hover:text-destructive"
            disabled={!canEdit}
            onClick={onRemove}
          >
            <Trash2 className="h-3.5 w-3.5" />
            移出
          </Button>
        </Gate>
      </div>
    </li>
  );
}
