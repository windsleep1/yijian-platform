"use client";

import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileSpreadsheet,
  Info,
  Loader2,
  Rocket,
  UploadCloud,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { ErrorReportTable } from "@/components/ErrorReportTable";
import { ForbiddenState } from "@/components/ForbiddenState";
import { ImportStatCards } from "@/components/ImportStatCards";
import { InlineError } from "@/components/InlineError";
import { PageHeader } from "@/components/PageHeader";
import { StepWizard, type WizardStep } from "@/components/StepWizard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useExecuteImport,
  usePublishImport,
  useUploadImport,
  useValidateImport,
} from "@/hooks/useImports";
import { useChapterTree } from "@/hooks/useQuestions";
import { useAuth } from "@/lib/auth-context";
import { formatDateMinute } from "@/lib/format";
import { formatBytes, IMPORT_SOURCE_TYPES, validateLocalFile } from "@/lib/import";
import { P } from "@/lib/permission";
import { sourceTypeLabel } from "@/lib/question";
import type { ImportBatchDetail, ImportExecuteOut, ImportMode, SourceType } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEPS: WizardStep[] = [
  { key: "upload", title: "上传文件", description: "拖拽 CSV / JSON，选科目与来源" },
  { key: "validate", title: "校验结果", description: "逐行试算，不写库" },
  { key: "preview", title: "预览确认", description: "确认数量后执行与发布" },
];

/** Radix Select 不接受空字符串 value，用哨兵表示「不指定科目」。 */
const ANY_SUBJECT = "__any__";

/** `.csv` / `.json` 之外的一律不收（xlsx 会给出"另存为 CSV"的具体建议）。 */
const ACCEPT = ".csv,.json,text/csv,application/json";

/**
 * 题库批量导入向导（三步）。
 *
 * ## 三步各自在做什么（这是本页最需要说清的事）
 *
 *   ① 上传        POST /upload    → 建批次。**一道题都不写**。
 *   ② 校验结果    POST /validate  → dry-run 逐行试算，把每行判成
 *                                  新增 / 更新 / 跳过 / 错误。**仍然一道题都不写**。
 *   ③ 预览确认    POST /execute   → 真正写库（整批一个事务）。
 *                 POST /publish   → 把本批草稿推向线上题库。
 *
 * 第 ② 步的"未写库"必须在页面上**明说**（`dry-run` 横幅）。教研看到
 * "成功 6000 行"时的第一反应是"已经进库了吗" —— 如果界面不回答这个问题，
 * 他就会去题库列表里找，然后发现没有，然后不敢点第 ③ 步。
 *
 * ## 三个容易写错的地方
 *
 *  1. **上传后立刻校验，id 不能走 hook 参数。** `setBatchId(up.id)` 之后
 *     state 还没更新，绑定式 hook 会拿到空 id 打到 `/admin/imports//validate`。
 *     所以 `useValidateImport()` 把 id 作为**变量**传入（见 hooks/useImports.ts）。
 *  2. **"含错就不能执行"是后端默认行为，前端要把这件事讲出来。**
 *     `failed_rows > 0` 时直接点执行会吃 `40901`。这里提前把选择摆出来：
 *     要么回去改文件，要么显式勾选"仅导入通过的行"（`allow_partial`）。
 *  3. **执行与发布是两件事。** 执行完题是 `draft`，发布才上线 ——
 *     中间这一步就是给教研抽样验收的（docs/07 §4.3 ⑤）。
 */
export default function NewImportPage() {
  const { hasPermission, isLoading: authLoading } = useAuth();

  const [step, setStep] = useState(0);
  // "不可跳步"靠 maxReached：只有到达过的步骤可点回去
  const [maxReached, setMaxReached] = useState(0);

  // -- 第 ① 步 --
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [subjectId, setSubjectId] = useState("");
  const [sourceType, setSourceType] = useState<SourceType>("self");
  const [mode, setMode] = useState<ImportMode>("insert");
  const [licenseNote, setLicenseNote] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // -- 第 ② / ③ 步 --
  const [batchId, setBatchId] = useState<string | null>(null);
  const [validation, setValidation] = useState<ImportBatchDetail | null>(null);
  const [allowPartial, setAllowPartial] = useState(false);
  const [executed, setExecuted] = useState<ImportExecuteOut | null>(null);
  const [publishedAt, setPublishedAt] = useState<string | null>(null);

  const tree = useChapterTree();
  const groups = tree.data?.items ?? [];

  const upload = useUploadImport();
  const validate = useValidateImport();
  const execute = useExecuteImport();
  const publish = usePublishImport();

  if (!authLoading && !hasPermission(P.questionImport)) {
    return <ForbiddenState need={P.questionImport} />;
  }

  const mayPublish = hasPermission(P.questionPublish);

  const busyUploading = upload.isPending || validate.isPending;

  const pickFile = (f: File | null | undefined) => {
    if (!f) return;
    const err = validateLocalFile({ name: f.name, size: f.size });
    setLocalError(err);
    setFile(err ? null : f);
  };

  /** ① → ②：上传建批次，紧接着校验（dry-run）。 */
  const handleUploadAndValidate = async () => {
    if (!file || busyUploading) return;
    setLocalError(null);
    setValidation(null);
    setExecuted(null);
    setPublishedAt(null);
    setAllowPartial(false);
    try {
      const up = await upload.mutateAsync({
        file,
        // ⚠️ 不指定科目时**不要**塞空字符串——后端会把它当成一个科目 ID 去解析
        subject_id: subjectId || undefined,
        source_type: sourceType,
        mode,
        license_note: licenseNote.trim() || undefined,
      });
      setBatchId(up.id);
      const v = await validate.mutateAsync(up.id);
      setValidation(v);
      setStep(1);
      setMaxReached((m) => Math.max(m, 1));
      toast.success(`校验完成：共 ${v.total_rows} 行`, {
        description:
          v.failed_rows > 0
            ? `${v.failed_rows} 行未通过，请查看错误报告（本次未写库）。`
            : "全部通过，可以进入预览确认（本次未写库）。",
      });
    } catch {
      // 具体原因由下面的 InlineError 常驻展示（toast 会消失、且分不清是哪一步）
    }
  };

  /**
   * 只重试校验（不重新上传）。
   *
   * 上传成功后校验失败时，**批次已经建好了**。此时如果重试走
   * `handleUploadAndValidate`，会再建一个新批次 —— 旧批次就成了永久挂在
   * 列表里的垃圾（还会占一个 batch_no）。所以这两件事必须能分开重试。
   */
  const handleValidateOnly = async () => {
    if (!batchId || validate.isPending) return;
    try {
      const v = await validate.mutateAsync(batchId);
      setValidation(v);
      setStep(1);
      setMaxReached((m) => Math.max(m, 1));
    } catch {
      /* 见 InlineError */
    }
  };

  /** ③：执行导入（整批一个事务）。 */
  const handleExecute = async () => {
    if (!batchId || execute.isPending) return;
    try {
      const ex = await execute.mutateAsync({ id: batchId, allow_partial: allowPartial });
      setExecuted(ex);
      toast.success(`导入完成：新增/更新 ${ex.success_rows} 题`, {
        description: `耗时 ${(ex.duration_ms / 1000).toFixed(2)}s。题目当前是草稿状态，发布后才会进入线上题库。`,
      });
    } catch {
      /* 见 InlineError */
    }
  };

  /** ③：发布本批题目。 */
  const handlePublish = async () => {
    if (!batchId || publish.isPending) return;
    try {
      const b = await publish.mutateAsync({ id: batchId });
      setPublishedAt(b.finished_at ?? new Date().toISOString());
      toast.success("已发布到线上题库", {
        description: `批次 ${b.batch_no} 的题目状态已从草稿变为已发布。`,
      });
    } catch {
      /* 见 InlineError */
    }
  };

  const resetToPickFile = () => {
    setStep(0);
    setFile(null);
    setBatchId(null);
    setValidation(null);
    setExecuted(null);
    setPublishedAt(null);
    setAllowPartial(false);
    upload.reset();
    validate.reset();
    execute.reset();
    publish.reset();
  };

  return (
    <>
      <PageHeader
        title="批量导入题库"
        description="三步走：上传文件 → 校验（不写库）→ 预览确认后执行。执行是整批一个事务：要么全部进入，要么一条不进。"
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href="/imports">批次历史</Link>
          </Button>
        }
      />

      <StepWizard
        steps={STEPS}
        current={step}
        maxReached={maxReached}
        onStepClick={(i) => setStep(i)}
        className="mb-5"
      />

      {/* ============================================ ① 上传 */}
      {step === 0 ? (
        <div className="space-y-4">
          <div className="space-y-4 rounded-lg border bg-card p-4">
            {/* ---- 拖拽上传区 ---- */}
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={(e) => {
                // 只在真正离开这个容器时收掉高亮：指针移到子元素上也会触发 dragleave
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                pickFile(e.dataTransfer.files?.[0]);
              }}
              className={cn(
                "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
                dragging ? "border-primary bg-primary/5" : "border-border bg-muted/20",
              )}
              data-testid="import-dropzone"
            >
              <UploadCloud
                className={cn("h-8 w-8", dragging ? "text-primary" : "text-muted-foreground")}
              />
              <p className="text-sm font-medium">
                {dragging ? "松手即可上传" : "把 CSV / JSON 文件拖到这里"}
              </p>
              <p className="text-xs text-muted-foreground">
                或
                <button
                  type="button"
                  className="mx-1 font-medium text-primary underline underline-offset-2"
                  onClick={() => inputRef.current?.click()}
                >
                  选择文件
                </button>
                。支持 UTF-8 with BOM 的 CSV（Excel「另存为 CSV UTF-8」直接可用）。
                {/* 说明为什么不让传 xlsx —— 需求 docs/07 §4.1 把 xlsx 列为推荐，
                    但本批后端显式拒绝。这句话就是那块"认知差"的补丁。 */}
                本批不支持 .xlsx，请先另存为 CSV。
              </p>
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPT}
                className="hidden"
                onChange={(e) => {
                  pickFile(e.target.files?.[0]);
                  // 清空 value：否则连续选同一个文件不会再触发 onChange
                  e.target.value = "";
                }}
              />

              {file ? (
                <div className="mt-2 flex items-center gap-2 rounded-md border bg-background px-3 py-1.5 text-xs">
                  <FileSpreadsheet className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-medium">{file.name}</span>
                  <span className="yj-json text-muted-foreground">{formatBytes(file.size)}</span>
                  <button
                    type="button"
                    onClick={() => setFile(null)}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label="移除已选文件"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : null}
            </div>

            {/* ---- 元信息 ---- */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1.5">
                <Label className="text-xs">批次科目</Label>
                <Select
                  value={subjectId || ANY_SUBJECT}
                  onValueChange={(v) => setSubjectId(v === ANY_SUBJECT ? "" : v)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={tree.isLoading ? "加载中…" : "不指定"} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY_SUBJECT}>不指定（按文件里的 subject_code）</SelectItem>
                    {groups.map((g) => (
                      <SelectItem key={g.subject.id} value={g.subject.id}>
                        {g.subject.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[10px] leading-snug text-muted-foreground">
                  指定后会与你的数据范围取交集：教研只能导入自己专业的题。
                </p>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs">来源类型</Label>
                <Select value={sourceType} onValueChange={(v) => setSourceType(v as SourceType)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {IMPORT_SOURCE_TYPES.map((s) => (
                      <SelectItem key={s} value={s}>
                        {sourceTypeLabel(s)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[10px] leading-snug text-muted-foreground">
                  行内未填时用这个兜底。非「自有原创」必须填来源名称（合规红线）。
                </p>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs">重复题处理</Label>
                <Select value={mode} onValueChange={(v) => setMode(v as ImportMode)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="insert">insert · 跳过重复（推荐）</SelectItem>
                    <SelectItem value="upsert">upsert · 更新已存在的题</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[10px] leading-snug text-muted-foreground">
                  同一批只能执行一次；选错要换模式请重新上传。
                </p>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs">授权 / 来源说明（选填）</Label>
                <Input
                  value={licenseNote}
                  onChange={(e) => setLicenseNote(e.target.value)}
                  placeholder="例如：HT-2026-0312 授权采购"
                  maxLength={500}
                />
                <p className="text-[10px] leading-snug text-muted-foreground">
                  会写进批次留痕，便于日后追溯来源。
                </p>
              </div>
            </div>
          </div>

          {localError ? (
            <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {localError}
            </div>
          ) : null}

          {upload.isError ? (
            <InlineError
              title="上传"
              error={upload.error}
              onRetry={() => void handleUploadAndValidate()}
              retrying={busyUploading}
              hint="上传只是建批次，不会写入任何题目，重试是安全的。"
            />
          ) : null}

          {validate.isError ? (
            <InlineError
              title="校验"
              error={validate.error}
              onRetry={() => void handleValidateOnly()}
              retrying={validate.isPending}
              hint="批次已经建好了，重试只重新校验同一个批次，不会再建一个新批次。"
            />
          ) : null}

          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {batchId ? (
                <>
                  已建批次 <span className="yj-json">{batchId}</span>
                </>
              ) : (
                "上传后系统会立刻逐行校验（不写库），你可以在下一步看到完整报告。"
              )}
            </p>
            <Button
              onClick={() => void handleUploadAndValidate()}
              disabled={!file || busyUploading}
            >
              {busyUploading ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  上传并校验中…
                </>
              ) : (
                <>
                  上传并校验
                  <ArrowRight className="h-3.5 w-3.5" />
                </>
              )}
            </Button>
          </div>
        </div>
      ) : null}

      {/* ============================================ ② 校验结果 */}
      {step === 1 && validation ? (
        <div className="space-y-4">
          {/* dry-run 横幅：本页最容易被误解的一句话 */}
          <div className="flex items-start gap-2.5 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-sky-700" />
            <div className="text-xs leading-relaxed text-sky-900">
              <p className="text-sm font-medium">
                本次为 dry-run：逐行校验已完成，<strong>没有写入任何题目</strong>。
              </p>
              <p className="mt-1">
                下面的数字是"如果现在执行会发生什么"的试算结果。确认无误后进入第 ③ 步执行；
                如果错误行比较多，建议回第 ① 步改文件后重新上传（比一行行改库快得多）。
              </p>
            </div>
          </div>

          <BatchMeta
            batchNo={validation.batch_no}
            fileName={validation.file_name}
            fileSize={validation.file_size}
            subjectName={validation.subject_name}
            mode={validation.mode}
            sourceType={validation.source_type}
            createdAt={validation.created_at}
          />

          <ImportStatCards batch={validation} />

          <ErrorReportTable
            errors={validation.error_report.errors}
            totalErrors={validation.error_report.total_errors}
            truncated={validation.error_report.truncated}
            batchNo={validation.batch_no}
          />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button variant="outline" onClick={resetToPickFile}>
              <ArrowLeft className="h-3.5 w-3.5" />
              重新选择文件
            </Button>
            <Button
              onClick={() => {
                setStep(2);
                setMaxReached((m) => Math.max(m, 2));
              }}
              disabled={validation.total_rows === 0}
            >
              下一步：预览确认
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      ) : null}

      {/* ============================================ ③ 预览确认 */}
      {step === 2 && validation ? (
        <div className="space-y-4">
          <BatchMeta
            batchNo={validation.batch_no}
            fileName={validation.file_name}
            fileSize={validation.file_size}
            subjectName={validation.subject_name}
            mode={validation.mode}
            sourceType={validation.source_type}
            createdAt={validation.created_at}
          />

          {/* 预览：给具体数字，不是"通过" */}
          <div className="rounded-lg border bg-card p-4">
            <h2 className="mb-3 text-sm font-medium">执行后会发生什么</h2>
            {(() => {
              const inserts = validation.success_rows;
              const updates = validation.updated_rows;
              const skipped = validation.duplicate_rows;
              const failed = validation.failed_rows;
              const willWrite = inserts + updates;
              return (
                <div className="space-y-3">
                  <ul className="space-y-1.5 text-sm">
                    <li className="flex flex-wrap items-center gap-2">
                      <Badge variant="success" className="w-[76px] justify-center">
                        新增 {inserts}
                      </Badge>
                      <span className="text-muted-foreground">
                        道新题将写入题库，状态为<strong className="text-foreground">草稿</strong>
                      </span>
                    </li>
                    <li className="flex flex-wrap items-center gap-2">
                      <Badge variant="info" className="w-[76px] justify-center">
                        更新 {updates}
                      </Badge>
                      <span className="text-muted-foreground">
                        道已存在的题会被覆盖为文件里的内容（版本 +1，原状态保留）
                      </span>
                    </li>
                    <li className="flex flex-wrap items-center gap-2">
                      <Badge variant="secondary" className="w-[76px] justify-center">
                        跳过 {skipped}
                      </Badge>
                      <span className="text-muted-foreground">
                        道与库里已有题内容完全相同，不会改动
                      </span>
                    </li>
                    <li className="flex flex-wrap items-center gap-2">
                      <Badge
                        variant={failed > 0 ? "destructive" : "outline"}
                        className="w-[76px] justify-center"
                      >
                        未通过 {failed}
                      </Badge>
                      <span className="text-muted-foreground">
                        {failed > 0
                          ? "行校验失败，默认不会被写入（见下方选择）"
                          : "行（没有错误行）"}
                      </span>
                    </li>
                  </ul>

                  <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs">
                    本次执行将向题库写入 <strong className="yj-json text-sm">{willWrite}</strong>{" "}
                    道题
                    {skipped > 0 ? <>，另有 {skipped} 道因重复保持不动</> : null}。 整批在
                    <strong>一个事务</strong>里完成：中途任何异常都会整体回滚， 不会留下半截数据。
                  </div>
                </div>
              );
            })()}
          </div>

          {/* 含错时的显式选择：把后端的 40901 提前变成用户能理解的两个选项 */}
          {validation.failed_rows > 0 ? (
            <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-4">
              <p className="text-sm font-medium text-amber-900">
                这批有 {validation.failed_rows} 行未通过校验
              </p>
              <p className="text-xs leading-relaxed text-amber-900">
                按「整批成功或整批失败」的约定，<strong>未经确认不会写入任何一行</strong>。
                你可以回到第 ② 步下载错误报告、改好文件重新上传；
                或者勾选下面这一项，只导入通过校验的行。
              </p>
              <label className="flex cursor-pointer items-start gap-2 text-xs text-amber-900">
                <Checkbox
                  checked={allowPartial}
                  onCheckedChange={(v) => setAllowPartial(v === true)}
                  className="mt-0.5"
                />
                <span>
                  仅导入通过校验的 {validation.total_rows - validation.failed_rows} 行 （跳过{" "}
                  {validation.failed_rows} 个错误行）
                  <span className="mt-0.5 block text-[11px] text-amber-800">
                    跳过错误行会让这一批的数据与文件不完全一致，请确认你清楚少了哪几行。
                  </span>
                </span>
              </label>
            </div>
          ) : null}

          {execute.isError ? (
            <InlineError
              title="执行导入"
              error={execute.error}
              onRetry={() => void handleExecute()}
              retrying={execute.isPending}
              hint={
                "执行是整批事务：失败时库里一条都没写进去，直接重试是安全的。" +
                "若提示「该批次已经执行过导入」，请到批次详情查看，不要重复导入。"
              }
            />
          ) : null}

          {publish.isError ? (
            <InlineError
              title="发布"
              error={publish.error}
              onRetry={() => void handlePublish()}
              retrying={publish.isPending}
              hint="题目已经入库为草稿，只是发布这一步失败了。重试即可，不会重复写入题目。"
            />
          ) : null}

          {/* ---- 执行中 ---- */}
          {execute.isPending ? (
            <div className="flex items-start gap-3 rounded-lg border border-primary/40 bg-primary/5 p-4">
              <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-primary" />
              <div className="text-xs leading-relaxed">
                <p className="text-sm font-medium text-foreground">正在导入…</p>
                <p className="mt-1 text-muted-foreground">
                  整批 {validation.total_rows} 行在一个事务内写入， 请
                  <strong className="text-foreground">不要关闭页面或刷新</strong>。
                  期间系统会写题目、写版本快照、写变更日志，所以大文件需要几十秒。
                </p>
              </div>
            </div>
          ) : null}

          {/* ---- 执行结果 ---- */}
          {executed ? (
            <div className="space-y-3 rounded-lg border border-emerald-200 bg-emerald-50/60 p-4">
              <p className="flex items-center gap-2 text-sm font-medium text-emerald-900">
                <CheckCircle2 className="h-4 w-4" />
                执行完成：写入 {executed.success_rows} 道题
                <span className="yj-json text-xs font-normal text-emerald-800">
                  耗时 {(executed.duration_ms / 1000).toFixed(2)}s
                </span>
              </p>
              <ul className="grid gap-1 text-xs text-emerald-900 sm:grid-cols-2">
                <li>· 新增 {Math.max(0, executed.success_rows - executed.updated_rows)} 道</li>
                <li>· 更新 {executed.updated_rows} 道</li>
                <li>· 跳过（重复）{executed.duplicate_rows} 道</li>
                <li>· 未通过 {executed.failed_rows} 行</li>
              </ul>
              <p className="text-xs leading-relaxed text-emerald-900">
                题目当前是<strong>草稿</strong>状态，不会出现在线上题库。确认抽样验收后再发布。
              </p>
            </div>
          ) : null}

          {publishedAt ? (
            <div className="rounded-lg border border-primary/40 bg-primary/5 p-4 text-xs leading-relaxed">
              <p className="text-sm font-medium">已发布到线上题库</p>
              <p className="mt-1 text-muted-foreground">
                批次 {validation.batch_no} 的题目状态已从草稿变为已发布。
                题库列表（默认不含草稿）现在可以看到它们了。
              </p>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                <ArrowLeft className="h-3.5 w-3.5" />
                返回校验结果
              </Button>
              {batchId ? (
                <Button variant="ghost" asChild>
                  <Link href={`/imports/${batchId}`}>查看批次详情</Link>
                </Button>
              ) : null}
            </div>

            <div className="flex gap-2">
              {!executed ? (
                <Button
                  onClick={() => void handleExecute()}
                  disabled={execute.isPending || (validation.failed_rows > 0 && !allowPartial)}
                  title={
                    validation.failed_rows > 0 && !allowPartial
                      ? "本批存在校验失败行。请先返回修正文件，或勾选「仅导入通过校验的行」。"
                      : undefined
                  }
                >
                  {execute.isPending ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      执行中…
                    </>
                  ) : (
                    <>
                      <Rocket className="h-3.5 w-3.5" />
                      执行导入
                    </>
                  )}
                </Button>
              ) : !publishedAt ? (
                <Button
                  onClick={() => void handlePublish()}
                  disabled={publish.isPending || !mayPublish}
                >
                  {publish.isPending ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      发布中…
                    </>
                  ) : (
                    "发布到线上题库"
                  )}
                </Button>
              ) : (
                <Button onClick={resetToPickFile}>再导一批</Button>
              )}
            </div>
          </div>

          {!mayPublish && executed && !publishedAt ? (
            <p className="text-xs text-muted-foreground">
              你的账号没有 <code className="yj-json">question:publish</code> 权限，
              题目已入库为草稿，需要由有发布权限的同事完成发布。
            </p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/** 批次元信息一行（第 ② / ③ 步共用）。 */
function BatchMeta({
  batchNo,
  fileName,
  fileSize,
  subjectName,
  mode,
  sourceType,
  createdAt,
}: {
  batchNo: string;
  fileName: string;
  fileSize: number | null;
  subjectName: string | null;
  mode: string;
  sourceType: string;
  createdAt: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-lg border bg-card px-4 py-3 text-xs">
      <span className="flex items-center gap-1.5">
        <span className="text-muted-foreground">批次</span>
        <code className="yj-json font-medium">{batchNo}</code>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="text-muted-foreground">文件</span>
        <span className="font-medium">{fileName}</span>
        <span className="yj-json text-muted-foreground">{formatBytes(fileSize)}</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="text-muted-foreground">科目</span>
        <span>{subjectName ?? "未指定"}</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="text-muted-foreground">模式</span>
        <code className="yj-json">{mode}</code>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="text-muted-foreground">来源</span>
        <span>{sourceTypeLabel(sourceType)}</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="text-muted-foreground">创建于</span>
        <span className="yj-json">{createdAt ? formatDateMinute(createdAt) : "—"}</span>
      </span>
    </div>
  );
}
