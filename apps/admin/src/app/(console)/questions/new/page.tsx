"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { ForbiddenState } from "@/components/ForbiddenState";
import { PageHeader } from "@/components/PageHeader";
import { QuestionForm, type QuestionFormOutput } from "@/components/QuestionForm";
import { Button } from "@/components/ui/button";
import { useChapterTree, useCreateQuestion } from "@/hooks/useQuestions";
import { useAuth } from "@/lib/auth-context";
import { P } from "@/lib/permission";
import { emptyDraft, type QuestionDraft } from "@/lib/question";

/**
 * 新建题目。
 *
 * 与详情页共用 `QuestionForm`，差别只在"提交后干什么"：
 * 这里创建成功后**直接跳到详情页**，而不是回列表。
 *
 * 为什么不是回列表：新建完一件事通常还没完 —— 要核对渲染出来的选项、
 * 想补一句解析、要看一眼版本历史。跳详情页让"创建"和"继续完善"接得上；
 * 回列表则要多点一次才能回来核对。详情页本来就有"返回题库"的出口。
 */
export default function NewQuestionPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();
  const router = useRouter();
  const tree = useChapterTree();
  const create = useCreateQuestion();

  // 惰性初始化：emptyDraft() 内部会分配选项 key，绝不能每次渲染都调一次
  const [draft, setDraft] = useState<QuestionDraft>(() => emptyDraft());

  if (!authLoading && !hasPermission(P.questionCreate)) {
    return <ForbiddenState need={P.questionCreate} />;
  }

  const handleSubmit = (out: QuestionFormOutput) => {
    if (out.kind !== "create") return;
    create.mutate(out.payload, {
      onSuccess: (created) => {
        toast.success(`题目已创建（v${created.version}）`, {
          description: "已跳到详情页，可继续完善解析或查看版本历史。",
        });
        router.replace(`/questions/${created.id}`);
      },
    });
  };

  return (
    <>
      <PageHeader
        title="新建题目"
        description="本批支持单选 / 多选 / 判断三种题型。保存后会立即生成 v1 版本快照。"
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href="/questions">
              <ArrowLeft className="h-3.5 w-3.5" />
              返回题库
            </Link>
          </Button>
        }
      />

      <QuestionForm
        mode="create"
        draft={draft}
        onDraftChange={setDraft}
        tree={tree.data}
        treeLoading={tree.isLoading}
        submitting={create.isPending}
        onSubmit={handleSubmit}
        onCancel={() => router.push("/questions")}
        submitLabel="创建题目"
      />
    </>
  );
}
