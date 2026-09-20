"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useRef } from "react";

type Primitive = string | number | undefined | null;
export type TableDefaults = Record<string, Primitive>;

/**
 * 表格状态（页码 / 每页条数 / 筛选）与 **URL query** 双向同步。
 *
 * 为什么一定要放 URL 而不是 `useState`：
 *   1. F5 刷新后筛选和页码还在。用 useState 的话，用户筛了 5 分钟、翻到第 7 页，
 *      手一抖刷新，全没了 —— 这是后台最招人烦的体验问题之一。
 *   2. 能把"我筛出来的结果"直接发链接给同事。
 *   3. 浏览器后退键符合直觉（回到上一个筛选状态，而不是退出页面）。
 *
 * ## 用法约束
 *
 * `defaults` **必须是模块级常量对象**（引用稳定）。它参与 useMemo/useCallback 的依赖，
 * 在渲染里现写一个字面量会导致每次渲染都重建 state，进而把请求打成死循环。
 */
export function useTableState<T extends TableDefaults>(
  defaults: T,
  /**
   * **不算"筛选条件"的键**（同样必须是模块级常量数组）。
   *
   * 存在的理由：有些开关和"筛选"语义不同 —— 题库页的「显示已归档」是**放宽**范围
   * （默认本来就只看未删除的），不是缩小范围。它若被算作筛选，空态文案会错：
   * 库里一道题都没有时打开开关，页面会说"没有符合条件的题目，请清空筛选条件"，
   * 而真正的答案是"题库还是空的"。这类开关放这里，空态文案才说得准。
   */
  nonFilterKeys: readonly string[] = [],
) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();

  // 用 ref 兜住 defaults：调用方若不小心传了不稳定引用，也不会引发循环
  const defaultsRef = useRef(defaults);
  const nonFilterRef = useRef(nonFilterKeys);
  const keys = useMemo(() => Object.keys(defaultsRef.current), []);

  const state = useMemo(() => {
    const out: TableDefaults = { ...defaultsRef.current };
    for (const key of keys) {
      const raw = sp.get(key);
      if (raw === null) continue;
      out[key] = typeof defaultsRef.current[key] === "number" ? Number(raw) : raw;
    }
    return out as T;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp, keys.join(",")]);

  const push = useCallback(
    (merged: TableDefaults) => {
      const next = new URLSearchParams();
      for (const [k, v] of Object.entries(merged)) {
        if (v === undefined || v === null || v === "") continue;
        next.set(k, String(v));
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  /**
   * 更新状态。**默认会把页码重置为 1** —— 改了筛选条件还停在第 7 页，
   * 结果往往是空白页（第 7 页在新条件下根本不存在），用户会以为"筛出来没数据"。
   */
  const setState = useCallback(
    (patch: Partial<T>) => {
      const merged: TableDefaults = { ...state, ...patch } as TableDefaults;
      if (!("page" in patch)) {
        const d = defaultsRef.current.page;
        if (typeof d === "number") merged.page = d;
      }
      push(merged);
    },
    [push, state],
  );

  /** 只改页码，不动筛选。 */
  const setPage = useCallback((page: number) => push({ ...state, page }), [push, state]);

  /**
   * 清空全部筛选（保留 page/page_size 这类结构性参数的默认值）。
   *
   * **`nonFilterKeys` 里的开关会被保留**：它们本来就不是"筛选条件"，
   * 而是"放宽范围"的显示开关。用户为了看归档题特意打开「显示已归档」，
   * 再点了下"清空筛选"却把开关也关掉，是一件很恼人的事 ——
   * 与 `hasFilters` 跳过它们保持同一套语义。
   */
  const reset = useCallback(() => {
    const out: TableDefaults = {};
    for (const [k, v] of Object.entries(defaultsRef.current)) {
      if (v === undefined || v === null || v === "") continue;
      out[k] = v;
    }
    for (const k of nonFilterRef.current) {
      const cur = (state as TableDefaults)[k];
      if (cur === undefined || cur === null || cur === "") delete out[k];
      else out[k] = cur;
    }
    push(out);
  }, [push, state]);

  /** 当前是否处于"筛选过"的状态（用于空态文案与"清空筛选"入口）。 */
  const hasFilters = useMemo(() => {
    return keys.some((k) => {
      if (k === "page" || k === "page_size" || k === "order" || k === "sort") return false;
      if (nonFilterRef.current.includes(k)) return false;
      const def = defaultsRef.current[k];
      const cur = (state as TableDefaults)[k];
      return String(cur ?? "") !== String(def ?? "");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, keys.join(",")]);

  return { state, setState, setPage, reset, hasFilters };
}
