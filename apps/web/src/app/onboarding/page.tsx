"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ApiError, request } from "@/lib/api";
import { hasToken } from "@/lib/auth-store";
import type { Me, Profile, Subject } from "@/lib/types";

/**
 * 引导（**两步**：选专业 → 选考试年份）。
 *
 * ## ★ 为什么"选专业"是挑一门**实务课**，而不是另做一份专业字典
 *
 * `subjects.professional`（`jz` / `sz` …）+ 每门实务课自己的中文名**已经构成完整信息**
 * （种子见 `db/schema.sql` 的 `INSERT INTO subjects`）。
 * 为此新造一张「专业列表」= **凭空发明数据**，还要顺带维护它的种子、状态位、排序 ——
 * 而后端 `GET /subjects?category=professional` 已经把这些原样给了出来。
 * ⇒ 显示用 `name`（"专业工程管理与实务（建筑工程）"）+ `short_name`，**落库存 `professional` 码**。
 *
 * ## 两步之间**不提交**
 *
 * 选专业时不发请求，选完年份才 `PUT /users/me/profile` 一次。
 * 理由：后端的"引导完成"判据是**两个字段都有值**（`profile_service`），
 * 分两次发请求也一样——但那样会多一次"只选了专业"的中间态落库，
 * 而这中间态**没有任何用途**（前端自己就记着）。
 * ⇒ 少一次写、少一种状态。
 */

/** 年份候选：今年 / 明年。刻意**不用**后端拿 —— 它不依赖任何业务数据。 */
function yearOptions(): number[] {
  const y = new Date().getFullYear();
  return [y, y + 1];
}

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1);
  const [subjects, setSubjects] = useState<Subject[] | null>(null);
  // ⚠️ 存 `Subject` 整条而不是只存 `professional` 码：第二步要显示"你选的专业"，
  //    只存码就得再回数组里找一次（多一处可能找不到的查找）。
  const [picked, setPicked] = useState<Subject | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!hasToken()) {
      router.replace("/login?next=%2Fonboarding");
      return;
    }
    setError("");
    try {
      const [me, list] = await Promise.all([
        request<Me>("/auth/me"),
        request<Subject[]>("/subjects", { query: { category: "professional" } }),
      ]);
      setProfile(me.profile);
      setSubjects(list);
      // 已经选过就预选上（"我的 → 修改报考信息"进来时不该从零开始）
      if (me.profile?.professional) {
        setPicked(list.find((s) => s.professional === me.profile?.professional) ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onPickYear(year: number) {
    setBusy(true);
    setError("");
    try {
      // ★ 一次提交两个字段 ⇒ 服务端据此判定"引导完成"（写 `onboarded_at`）
      await request<Profile>("/users/me/profile", {
        method: "PUT",
        body: { professional: picked?.professional, exam_year: year },
      });
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message}（${err.code}）` : "保存失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  if (error && !subjects) {
    return (
      <main className="mx-auto max-w-md px-6 py-10">
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
        <button
          onClick={() => void load()}
          className="min-h-touch mt-4 w-full rounded-lg border border-line font-medium"
        >
          重试
        </button>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-md px-6 py-8">
      <h1 className="text-2xl font-semibold">完善报考信息</h1>
      <p className="mt-1 mb-6 text-sm text-sub">
        {step}/2 · {step === 1 ? "选专业" : "选考试年份"}
      </p>

      {step === 1 &&
        (subjects === null ? (
          <p className="py-6 text-sm text-sub">加载中…</p>
        ) : (
          <ul className="space-y-2">
            {subjects.map((s) => {
              const active = picked?.id === s.id;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => setPicked(s)}
                    aria-pressed={active}
                    className={`min-h-touch w-full rounded-lg border px-4 py-3 text-left ${
                      active ? "border-brand bg-brand/5" : "border-line"
                    }`}
                  >
                    <span className="font-medium">{s.name}</span>
                    <span className="ml-2 text-sm text-sub">{s.short_name}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        ))}

      {step === 1 && subjects !== null && (
        <button
          type="button"
          disabled={!picked}
          onClick={() => {
            setError("");
            setStep(2);
          }}
          className="min-h-touch mt-6 w-full rounded-lg bg-brand font-medium text-brand-fg disabled:opacity-60"
        >
          下一步
        </button>
      )}

      {step === 2 && (
        <>
          <p className="mb-4 rounded-lg border border-line px-4 py-3 text-sm">
            专业：<span className="font-medium">{picked?.name ?? "（未选）"}</span>
          </p>
          <ul className="space-y-2">
            {yearOptions().map((y) => (
              <li key={y}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void onPickYear(y)}
                  className={`min-h-touch w-full rounded-lg border px-4 py-3 text-left font-medium disabled:opacity-60 ${
                    profile?.exam_year === y ? "border-brand bg-brand/5" : "border-line"
                  }`}
                >
                  {y} 年
                </button>
              </li>
            ))}
          </ul>
          {error && (
            <p role="alert" className="mt-4 text-sm text-danger">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={() => setStep(1)}
            className="min-h-touch mt-4 w-full text-sm text-sub"
          >
            返回改专业
          </button>
        </>
      )}

      {/* 已完成引导的人从这里进来（"我的 → 修改报考信息"）：给一条出口，
          否则他会以为必须再选一次。 */}
      {profile?.onboarded_at && (
        <button
          type="button"
          onClick={() => router.replace("/me")}
          className="min-h-touch mt-6 w-full text-sm text-sub"
        >
          已设置过，直接返回
        </button>
      )}
    </main>
  );
}
