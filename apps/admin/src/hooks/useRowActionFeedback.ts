"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";

/**
 * 行级异步操作的**就地反馈**。
 *
 * ## 为什么要有这个东西
 *
 * B 端最常见的行级操作（恢复 / 发布 / 归档 / 删除 / 启用）有一个共同的体验难题：
 * 操作成功后如果**立刻重新拉列表**，那一行会"唰"地消失 ——
 * 用户看到的是"我点了一下，东西没了"，既没确认到结果，也来不及看清发生了什么；
 * 如果**什么都不做**，用户又不知道到底成没成。
 *
 * 用户指定的模式（本项目约定）是三段式：
 *
 *   1. **停留在当前视图** —— 不跳转、不切筛选。在"显示已归档"下连续恢复几条时，
 *      视图一变就得重新开开关，连续操作直接断掉。
 *   2. **目标行就地标记**（淡出 / 打勾），**延时后从列表移除**。
 *      先给"成了"的正反馈，再让它走 —— 顺序反了就成了"神秘消失"。
 *   3. **同时给出口**：toast 里带 [查看]，一键跳到"该去的地方"（如默认列表）。
 *
 * ## 为什么不是 TanStack mutation 的 `onSuccess` 里 `invalidateQueries`
 *
 * 失效列表会**立刻重新拉取**。而归档/恢复这类开关式列表（`include_deleted=true`）
 * 里，"已恢复"的那条**本来还在结果集里**（它只是不再是"已归档"而已）——
 * 于是刚标记完的行会原地复活，标记和移除全被冲掉。
 * 所以这里**刻意绕过缓存失效**，由本 hook 用本地状态表达"这一行完成了"，
 * 等用户翻页/改筛选/手动刷新时再回到服务端真相。
 *
 * ## 三态 + 重试
 *
 * `pending`（转圈，禁掉重复点击）/ `done`（打勾 + 淡出）/ `error`（行内原因 + 重试键）。
 * **只有幂等接口才该给重试**（这是 `retryable` 参数存在的理由）：
 * 一个非幂等的写，重试可能造成第二次副作用。
 */
export type RowFeedbackPhase = "pending" | "done" | "error";

export type RowFeedback = {
  phase: RowFeedbackPhase;
  /** 行内文案，如「已恢复」「处理中…」 */
  label: string;
  /** `phase=error` 时的原因，直接来自后端 message（含可执行的下一步） */
  error?: string;
  /** `phase=error` 且允许重试时才有 */
  retry?: () => void;
};

type RunArgs<T> = {
  /** 行标识（用它来定位"这一行"） */
  id: string;
  /** 进行中文案，默认「处理中…」 */
  pendingLabel?: string;
  /** 成功后的行内标记，如「已恢复」 */
  doneLabel: string;
  /** 真正调后端。抛错即视为失败（`ApiError.message` 会作为行内原因） */
  action: () => Promise<T>;
  /**
   * 成功后的 toast。返回一个**纯描述**，由 hook 负责聚合与渲染 ——
   * 这样"已恢复 N 道"的计数才有唯一的地方去累加。
   */
  successToast: (result: T) => {
    title: string;
    description?: string;
    /** 出口：[查看] 按钮 */
    actionLabel?: string;
    onAction?: () => void;
  };
  /**
   * 失败时是否给"重试"。
   * **只有幂等的操作才该传 true** —— 非幂等写重试可能二次副作用。
   */
  retryable?: boolean;
  /** 失败时行内文案前缀 */
  errorTitle?: string;
  /**
   * `true` = **这一行留在原地**，只是状态变了（启用/停用、发布下线这类开关式操作）。
   *
   * 默认 `false`：标记完了就延时移除（归档/恢复/删除这类"这一行该走了"的操作）。
   *
   * 为什么必须有这个开关：行级异步操作其实有**两种结局** ——
   * "这条记录从当前视图消失"和"这条记录还在、但换了状态"。
   * 早期只有前者，遇到"停用一条规则"就会**把还在列表里的行错误地移除**，
   * 用户以为被删了。
   */
  keepRow?: boolean;
};

export type UseRowActionFeedbackOptions = {
  /** `done` → 从列表移除的延时，默认 3000ms（用户约定的"3 秒后消失"） */
  removeDelayMs?: number;
};

/** 同一个 `taskKey` 的成功在 N 毫秒内累加计数，避免连续操作弹一串 toast。 */
const TOAST_BATCH_WINDOW_MS = 6000;

export function useRowActionFeedback(opts: UseRowActionFeedbackOptions = {}) {
  const removeDelayMs = opts.removeDelayMs ?? 3000;

  const [feedback, setFeedback] = useState<Record<string, RowFeedback>>({});
  /** 已经从当前视图"移除"的行 */
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());

  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  /** toast 里的累计计数：taskKey → { count, timer } */
  const batches = useRef(new Map<string, { count: number; timer?: ReturnType<typeof setTimeout> }>());

  // 卸载时清掉所有定时器，避免"组件没了还在 setState"
  useEffect(() => {
    const t = timers.current;
    const b = batches.current;
    return () => {
      t.forEach((x) => clearTimeout(x));
      t.clear();
      b.forEach((x) => x.timer && clearTimeout(x.timer));
      b.clear();
    };
  }, []);

  const clearTimer = (id: string) => {
    const t = timers.current.get(id);
    if (t) {
      clearTimeout(t);
      timers.current.delete(id);
    }
  };

  const setOne = useCallback((id: string, next: RowFeedback) => {
    setFeedback((prev) => ({ ...prev, [id]: next }));
  }, []);

  const run = useCallback(
    async <T,>(args: RunArgs<T>): Promise<{ ok: boolean; result?: T }> => {
      const {
        id,
        pendingLabel = "处理中…",
        doneLabel,
        action,
        successToast,
        retryable = false,
        errorTitle,
        keepRow = false,
      } = args;

      clearTimer(id);
      // 重试前把它从"已移除"里放回来（否则用户点了重试却看不到那一行）
      setDismissed((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      setOne(id, { phase: "pending", label: pendingLabel });

      try {
        const result = await action();

        setOne(id, { phase: "done", label: doneLabel });

        // ---- toast：同一 taskKey 连续操作时累加计数 ----
        const spec = successToast(result);
        const taskKey = `${doneLabel}::${spec.actionLabel ?? ""}`;
        const batch = batches.current.get(taskKey) ?? { count: 0 };
        batch.count += 1;
        if (batch.timer) clearTimeout(batch.timer);
        batch.timer = setTimeout(() => batches.current.delete(taskKey), TOAST_BATCH_WINDOW_MS);
        batches.current.set(taskKey, batch);

        const n = batch.count;
        toast.success(n > 1 ? `已${doneLabel} ${n} 项` : spec.title, {
          // 用固定 id：连续操作时**原地更新**同一个 toast，而不是堆一屏
          id: `row-action-${taskKey}`,
          description: spec.description,
          duration: TOAST_BATCH_WINDOW_MS,
          action:
            spec.actionLabel && spec.onAction
              ? { label: spec.actionLabel, onClick: spec.onAction }
              : undefined,
        });

        // ---- 延时移除：先给正反馈，再让它走 ----
        // `keepRow` 时**跳过**这一步：行还在（只是状态变了），移除它是错的。
        if (!keepRow) {
          timers.current.set(
            id,
            setTimeout(() => {
              timers.current.delete(id);
              setDismissed((prev) => new Set(prev).add(id));
              setFeedback((prev) => {
                const next = { ...prev };
                delete next[id];
                return next;
              });
            }, removeDelayMs),
          );
        } else {
          // 留在原地的那些，2 秒后把行内标记收掉（"已停用"不该一直挂着），
          // 但**不隐藏行**。
          timers.current.set(
            id,
            setTimeout(() => {
              timers.current.delete(id);
              setFeedback((prev) => {
                const next = { ...prev };
                delete next[id];
                return next;
              });
            }, 2000),
          );
        }

        return { ok: true, result };
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err);
        setOne(id, {
          phase: "error",
          label: errorTitle ?? "操作失败",
          error: message,
          // 只有幂等操作才给重试键
          retry: retryable ? () => void run(args) : undefined,
        });
        // ⚠️ 刻意**不弹错误 toast**：原因就写在那一行上（含后端给的可执行建议），
        // 再弹一次只会让用户在两处读同一句话。
        return { ok: false };
      }
    },
    [removeDelayMs, setOne],
  );

  /** 筛选/翻页时调用：清掉所有本地状态，回到服务端真相。 */
  const reset = useCallback(() => {
    timers.current.forEach((t) => clearTimeout(t));
    timers.current.clear();
    batches.current.forEach((b) => b.timer && clearTimeout(b.timer));
    batches.current.clear();
    setFeedback({});
    setDismissed(new Set());
  }, []);

  return {
    /** 某一行的当前状态 */
    feedbackOf: (id: string): RowFeedback | undefined => feedback[id],
    /** 该行是否已从当前视图移除 */
    isDismissed: (id: string) => dismissed.has(id),
    /** 本次会话里已移除的行数（用于表格下方那行说明） */
    dismissedCount: dismissed.size,
    /** 有任意行在 pending —— 用于禁用"批量"类按钮，避免并发冲突 */
    hasPending: Object.values(feedback).some((f) => f.phase === "pending"),
    /** 从服务端列表里滤掉已移除的行 */
    visibleRows: <R,>(rows: R[], keyOf: (r: R) => string): R[] =>
      rows.filter((r) => !dismissed.has(keyOf(r))),
    run,
    reset,
  };
}
