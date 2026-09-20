"use client";

import { AlertTriangle, ArrowLeft, FileX, History, Loader2, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ForbiddenState } from "@/components/ForbiddenState";
import { MarkdownPreview } from "@/components/MarkdownPreview";
import { PageHeader } from "@/components/PageHeader";
import { PermissionGate } from "@/components/PermissionGate";
import { QuestionForm, type QuestionFormOutput } from "@/components/QuestionForm";
import { QuestionVersionDrawer } from "@/components/QuestionVersionDrawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  useChapterTree,
  useDeleteQuestion,
  useQuestion,
  useUpdateQuestion,
} from "@/hooks/useQuestions";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDateTime } from "@/lib/format";
import { P } from "@/lib/permission";
import {
  describePatch,
  difficultyLabel,
  draftFromDetail,
  isEditableType,
  qStatusLabel,
  qStatusVariant,
  qTypeLabel,
  sourceTypeLabel,
  type QuestionDraft,
} from "@/lib/question";
import type { QuestionDetail } from "@/lib/types";

/**
 * 题目详情 + 编辑。
 *
 * ## B 端特征落点
 *
 * - **② 保存即自增版本、写变更日志**：这里只做"算 diff → 提交 → 提示改了哪些字段"。
 *   真正自增版本是后端在一个事务里做的；前端把 `diffDraft` 算出来的**变化字段**提交，
 *   所以变更日志读起来是"改了题干、解析"，而不是"全字段快照"。
 * - **② 版本历史只读**：抽屉里没有回滚按钮，见 `QuestionVersionDrawer` 的注释。
 * - **40901 版本冲突**：这不是 bug，是"另一位同事先保存了"的正常反馈。
 *   必须**单独提示 + 给出口**，而不是混在一堆通用错误 toast 里让用户反复重试。
 *
 * ## 草稿同步的一个必须做对的细节
 *
 * 草稿只在「换了一道题」或「版本号变了」时从服务端重灌。
 * 若把整个 `query.data` 对象当依赖，任何一次后台 refetch（回到窗口、缓存过期）
 * 都会生成新对象 → 把用户正在写的内容冲掉。这是最难复现也最气人的一类 bug。
 */
export default function QuestionDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const router = useRouter();
  const { hasPermission, isLoading: authLoading } = useAuth();

  const query = useQuestion(id);
  const tree = useChapterTree();
  const update = useUpdateQuestion(id ?? "");
  const remove = useDeleteQuestion();

  const [draft, setDraft] = useState<QuestionDraft | null>(null);
  const [conflict, setConflict] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const dataId = query.data?.id;
  const dataVersion = query.data?.version;

  useEffect(() => {
    const d = query.data;
    if (d) setDraft(draftFromDetail(d));
    // 依赖只放 id / version：用整个 data 对象会导致后台 refetch 冲掉正在编辑的草稿
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataId, dataVersion]);

  if (!authLoading && !hasPermission(P.questionRead)) {
    return <ForbiddenState need={P.questionRead} />;
  }

  // ---------------------------------------------------------------- 加载 / 错误
  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 py-20 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载题目详情…
      </div>
    );
  }

  if (query.error || !query.data) {
    const err = query.error;
    const notFound = err instanceof ApiError && err.code === 40401;
    return (
      <div className="mx-auto max-w-lg py-20 text-center">
        <div className="mb-4 inline-flex rounded-full bg-muted p-4">
          <FileX className="h-7 w-7 text-muted-foreground" />
        </div>
        <h2 className="text-base font-semibold">{notFound ? "题目不存在" : "加载题目失败"}</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {notFound
            ? `ID「${id}」对应的题目不存在。它可能已被彻底删除，请确认链接是否正确。`
            : (err as Error | null)?.message ?? "未知错误"}
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link href="/questions">
              <ArrowLeft className="h-3.5 w-3.5" />
              返回题库
            </Link>
          </Button>
          {!notFound ? (
            <Button size="sm" onClick={() => void query.refetch()}>
              重试
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  const q = query.data;
  const editable = q.editable && isEditableType(q.type);

  // ---------------------------------------------------------------- 保存
  const handleSubmit = (out: QuestionFormOutput) => {
    if (out.kind !== "edit") return;
    const patch = out.patch;
    if (!patch) {
      toast.info("没有检测到任何改动", { description: "改点内容再保存，这次不会产生新版本。" });
      return;
    }
    setConflict(false);
    update.mutate(patch, {
      onSuccess: (updated) => {
        toast.success(`已保存，当前版本 v${updated.version}`, {
          description: `本次改动：${describePatch(patch)}`,
        });
      },
      onError: (err) => {
        // 40901：乐观锁失败 —— 页面另外给一块常驻提示 + 刷新出口
        if (err instanceof ApiError && err.code === 40901) setConflict(true);
      },
    });
  };

  const handleDelete = async () => {
    if (!id) return;
    try {
      await remove.mutateAsync({ id, reason: "后台手动删除" });
      toast.success("题目已软删除", {
        description: "可在题库页打开「显示已归档」查看，数据未被物理删除。",
      });
      setDeleteOpen(false);
      router.replace("/questions");
    } catch {
      // 统一错误 toast 已由 MutationCache 处理；保持弹窗打开便于重试
    }
  };

  return (
    <>
      <PageHeader
        title={editable ? "编辑题目" : "题目详情"}
        description={
          <span className="flex flex-wrap items-center gap-2 text-xs">
            <span className="yj-json">ID {q.id}</span>
            <Badge variant="outline" className="text-[10px]">
              {qTypeLabel(q.type)}
            </Badge>
            <Badge variant={qStatusVariant(q.status)} className="text-[10px]">
              {qStatusLabel(q.status)}
            </Badge>
            <Badge variant="secondary" className="yj-json text-[10px]">
              v{q.version}
            </Badge>
            {q.is_deleted ? (
              <Badge variant="destructive" className="text-[10px]">
                已归档
              </Badge>
            ) : null}
            <span className="text-muted-foreground">
              {q.subject_name ?? `#${q.subject_id}`} / {q.chapter_name ?? "未指定章节"}
            </span>
          </span>
        }
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link href="/questions">
                <ArrowLeft className="h-3.5 w-3.5" />
                返回题库
              </Link>
            </Button>

            <Button variant="outline" size="sm" onClick={() => setVersionOpen(true)}>
              <History className="h-3.5 w-3.5" />
              版本历史
              <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-[10px]">
                {q.versions.length}
              </Badge>
            </Button>

            <PermissionGate
              code={P.questionDelete}
              reason="删除会把题目从所有试卷与练习中下线，属于高权限动作。"
            >
              <Button
                variant="outline"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => setDeleteOpen(true)}
              >
                <Trash2 className="h-3.5 w-3.5" />
                删除
              </Button>
            </PermissionGate>
          </>
        }
      />

      {/* ---------------- 版本冲突（40901）常驻提示 ---------------- */}
      {conflict ? (
        <div className="mb-4 flex flex-wrap items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-amber-900">保存被拒绝：题目已被他人修改</p>
            <p className="mt-1 text-xs leading-relaxed text-amber-900/80">
              服务器上的版本已经前进到 v{q.version}，你手里的是旧版本。
              <strong>你的编辑还在页面上</strong>，不会自动丢失 —— 建议先把要保留的内容复制出去，
              再点"重新加载最新版本"在此基础上重做改动。
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => {
              setConflict(false);
              void query.refetch();
            }}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新加载最新版本
          </Button>
        </div>
      ) : null}

      {/* ---------------- 不可编辑题型：只读展示 ---------------- */}
      {!editable ? (
        <>
          <p className="mb-4 flex gap-2 rounded-lg border bg-muted/40 p-3 text-xs leading-relaxed text-muted-foreground">
            <FileX className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              题型「{qTypeLabel(q.type)}」本批<strong>只能查看</strong>
              ：它涉及背景材料、子问切分与评分点， 编辑能力属于后续批次。你仍然可以查看版本历史。
            </span>
          </p>
          <ReadOnlyView q={q} />
        </>
      ) : (
        /* ---------------- 可编辑：表单 ---------------- */
        <QuestionForm
          mode="edit"
          draft={draft ?? draftFromDetail(q)}
          onDraftChange={setDraft}
          tree={tree.data}
          treeLoading={tree.isLoading}
          original={q}
          submitting={update.isPending}
          onSubmit={handleSubmit}
          onCancel={() => router.push("/questions")}
          submitLabel={`保存（将生成 v${q.version + 1}）`}
        />
      )}

      {/* ---------------- 元信息 ---------------- */}
      <section className="mt-6 rounded-lg border bg-card p-4">
        <h2 className="mb-3 text-sm font-semibold">元信息</h2>
        <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <Meta label="默认分值">{q.score_default}</Meta>
          <Meta label="难度">{difficultyLabel(q.difficulty)}</Meta>
          <Meta label="年份">{q.exam_year ?? "—"}</Meta>
          <Meta label="标签">{q.tags.length ? q.tags.join("、") : "—"}</Meta>
          <Meta label="来源类型">{sourceTypeLabel(q.source_type)}</Meta>
          <Meta label="来源名称">{q.source_name ?? "—"}</Meta>
          <Meta label="授权凭证">{q.source_license ?? "—"}</Meta>
          <Meta label="版权方">{q.copyright_holder ?? "—"}</Meta>
          <Meta label="创建人">{q.created_by_name ?? "—"}</Meta>
          <Meta label="最后修改人">{q.updated_by_name ?? "—"}</Meta>
          <Meta label="创建时间">{formatDateTime(q.created_at)}</Meta>
          <Meta label="最后更新时间">{formatDateTime(q.updated_at)}</Meta>
          <Meta label="发布时间">{q.published_at ? formatDateTime(q.published_at) : "未发布"}</Meta>
          <Meta label="内容指纹">
            <span className="yj-json text-xs">{q.content_hash ?? "—"}</span>
          </Meta>
          <Meta label="我的权限" className="sm:col-span-2">
            <span className="flex flex-wrap gap-1.5">
              <Badge variant={q.can_edit ? "success" : "secondary"} className="text-[10px]">
                {q.can_edit ? "可编辑（question:update）" : "只读（缺 question:update）"}
              </Badge>
              <Badge variant={q.can_delete ? "success" : "secondary"} className="text-[10px]">
                {q.can_delete ? "可删除（question:delete）" : "不可删除（缺 question:delete）"}
              </Badge>
            </span>
          </Meta>
        </dl>
        {hasPermission(P.questionUpdate) && query.isFetching ? (
          <>
            <Separator className="my-3" />
            <p className="text-[11px] text-muted-foreground">正在从服务端同步最新数据…</p>
          </>
        ) : null}
      </section>

      <QuestionVersionDrawer
        open={versionOpen}
        onOpenChange={setVersionOpen}
        versions={q.versions}
        currentVersion={q.version}
      />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="确认删除这道题？"
        destructive
        loading={remove.isPending}
        confirmText="软删除"
        description={
          <span className="space-y-2 text-sm">
            <span className="block">
              将<strong className="text-destructive">软删除</strong>题目
              <code className="yj-json mx-1 rounded bg-muted px-1.5 py-0.5 text-xs">{q.id}</code>
              （v{q.version}）。
            </span>
            <span className="block text-muted-foreground">
              删除后它不再出现在默认题库列表中，也不会再被组卷引用；
              数据本身保留，可在题库页打开「显示已归档」查到。本次操作会写入审计日志。
            </span>
          </span>
        }
        onConfirm={handleDelete}
      />
    </>
  );
}

function Meta({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="mb-0.5 text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all">{children}</dd>
    </div>
  );
}

/** 案例题 / 主观题的只读展示（本批不可编辑）。 */
function ReadOnlyView({ q }: { q: QuestionDetail }) {
  const options = [...q.options].sort((a, b) => a.sort_no - b.sort_no);
  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-4">
        <h2 className="mb-2 text-sm font-semibold">题干</h2>
        <MarkdownPreview text={q.stem} emptyHint="（空题干）" />
      </section>

      {options.length ? (
        <section className="rounded-lg border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold">选项 / 参考答案</h2>
          <ul className="space-y-1.5">
            {options.map((o) => (
              <li key={o.id} className="flex flex-wrap items-start gap-2 text-sm">
                <span className="yj-json w-5 shrink-0 font-semibold">{o.label}</span>
                <span className="min-w-0 flex-1">{o.content}</span>
                {o.is_correct ? (
                  <Badge variant="success" className="shrink-0 text-[10px]">
                    正确
                  </Badge>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {q.answer?.value?.length ? (
        <section className="rounded-lg border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold">答案</h2>
          <span className="yj-json text-sm">{q.answer.value.map(String).join("、")}</span>
        </section>
      ) : null}

      {q.analysis ? (
        <section className="rounded-lg border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold">解析</h2>
          <MarkdownPreview text={q.analysis} emptyHint="（空）" />
        </section>
      ) : null}
    </div>
  );
}
