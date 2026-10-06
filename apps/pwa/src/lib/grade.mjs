/**
 * 判分 —— **哑比较**（个人 PWA 侧，`apps/pwa/src/lib/grade.mjs`）。
 *
 * ## 为什么是 `.mjs` 而不是 `.ts`
 * 判分对拍（`tools/pwa/parity.py`）要在 **node 里直接跑**这个模块，和 Python 的
 * `practice_service.grade()` 逐条比结论。写成 `.ts` 就得先 `tsc` 一遍才能被 node 读到，
 * 而"多一步编译"正是让这种对拍慢慢失修的原因（本地跑不动 ⇒ 没人跑 ⇒ 门禁变摆设）。
 * 纯 ESM + JSDoc 让 **Next 与 node 用同一份文件**。
 *
 * ## ★★ 它的唯一设计约束：**不自带任何语义**
 *
 * 「哪些 token 算对」「部分分给多少」「哪些题型可判分」——**一条都不在这里**：
 *   · 答案的写法（判断题是 `[true]` 还是 `["A"]`）由 `tools/pwa/export-bank.py`
 *     **在导出时归一**，并用 `answer.check_doc()` 把"没归干净"的包**挡在门外**；
 *   · 部分分比例与可判分题型从包的 `meta.grading_rules` **读**，不写死。
 *
 * ⇒ 本文件里**不许出现字面量规则**（阈值、token 表、题型白名单）。
 *   判据：`grep -nE '[0-9]\.[0-9]|"A"|single|multiple|judge' src/lib/grade.mjs`
 *   只应命中**类型名的分支判断**，不命中任何**规则取值**。
 *
 * ## 与 Python 的关系：**同构，不是同一份**
 * `apps/api/app/services/practice_service.py::grade()` 是语义的唯一真相；
 * 本文件是它在"包已归一"这个前提下的**等价实现**。
 * ★ 前提由**两条判据**守着（缺一条这个等价就不成立）：
 *   ① `export-bank.py` 用 `check_doc()` 逐题把关 —— 包里出现旧写法就直接拒绝导出；
 *   ② `tools/pwa/parity.py` 拿 N 题 × {全对 / 全错 / 半对} 在两侧跑，**结论与得分逐条相同**。
 *   ★★ ② 还带一个**反向对照**：喂一条**旧写法**（`["A"]`）给本模块 ⇒ 它**必须**判错
 *      （与 Python 判对**不同**）。那条"应该红"的用例才是①在承重的证据 ——
 *      没有它，"归一"这件事做没做，对拍都看不出来。
 */

/** 题型 → 该题的正确答案从哪读、用户答案怎么比。**只有分派，没有规则。** */
const JUDGE_TYPE = "judge";
const MULTI_TYPE = "multiple";

/**
 * 非判断题的标号归一 —— 与 Python `grade()` 里的 `str(x).strip().upper()` 同构。
 * ★ 判断题**不做归一**：包里已经是布尔（`[true]` / `[false]`），
 *   再"归一"就等于在这里重新实现一套 token 映射（那正是本节开头禁止的事）。
 *
 * @param {unknown} x
 * @returns {string}
 */
function normToken(x) {
  return String(x).trim().toUpperCase();
}

/**
 * 取评分规则。**读不到就抛** —— 不兜底、不默认。
 *
 * ★ 为什么不给默认值（`?? 0.5`）：那会让"包里没有规则"与"规则就是 0.5"
 *   **长得一模一样**（同族：兜底要出声）。缺规则是**包的缺陷**，必须当场可见。
 *
 * @param {{ partial_credit_ratio?: number, gradable_types?: string[] } | null | undefined} rules
 */
function readRules(rules) {
  const ratio = rules?.partial_credit_ratio;
  const gradable = rules?.gradable_types;
  if (typeof ratio !== "number") {
    throw new Error(
      "题库包缺少 meta.grading_rules.partial_credit_ratio —— 判分规则必须随包下发，本模块不提供默认值。" +
        "请用 tools/pwa/export-bank.py 重新导出。",
    );
  }
  if (!Array.isArray(gradable) || gradable.length === 0) {
    throw new Error("题库包缺少 meta.grading_rules.gradable_types —— 可判分题型必须随包下发。");
  }
  return { ratio, gradable };
}

/**
 * 按题型校验并归一用户答案 —— Python `normalize_user_value()` 的镜像。
 *
 * ★ 它存在的理由和 Python 侧一样：**校验与判分要分开**。判分只回答"对不对"，
 *   而"这个提交本身合不合法"（判断题发了两个值、单选题发了标号数组？）是另一件事。
 *   混在一起会把"提交格式错"误报成"答错了"，而两者对用户的含义完全不同。
 *
 * @param {string} type
 * @param {unknown[]} raw
 * @returns {unknown[]}
 */
export function normalizeUserValue(type, raw) {
  const seq = Array.isArray(raw) ? raw : [raw];
  if (type === JUDGE_TYPE) {
    if (seq.length !== 1 || typeof seq[0] !== "boolean") {
      throw new LocalBadRequest("判断题请提交 [true] 或 [false]");
    }
    return [seq[0]];
  }
  if (type === "single") {
    if (seq.length !== 1 || typeof seq[0] !== "string") {
      throw new LocalBadRequest('单选题请提交一个选项标号，例如 ["B"]');
    }
    return [seq[0].trim().toUpperCase()];
  }
  if (type === MULTI_TYPE) {
    if (!seq.every((x) => typeof x === "string")) {
      throw new LocalBadRequest('多选题请提交选项标号数组，例如 ["A","C"]');
    }
    const labels = seq.map((x) => x.trim().toUpperCase());
    if (new Set(labels).size !== labels.length) {
      throw new LocalBadRequest("多选题不要重复提交同一个标号");
    }
    return labels.slice().sort();
  }
  throw new LocalBadRequest(`这类题（${type}）暂不支持在线判分`);
}

/** 本地侧的"请求不合法"。`code` 与后端 `40001` 同值 —— 前端错误分支不用分两套。 */
export class LocalBadRequest extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = "LocalBadRequest";
    this.code = 40001;
  }
}

/**
 * 判分。返回 `{ isCorrect, ratio }` —— 与 Python `grade()` 的 `(bool, float)` 一一对应。
 *
 * ★ **两个维度必须分开**：`isCorrect` 回答"答对了吗"，`ratio` 回答"给几分"。
 *   多选题"真子集且都对"时是 `(false, 0.5)` —— **算错但给分**。
 *   合并成一个数会让"正确率"被部分分污染（半对算对？算错？两种口径都能自圆其说 ⇒ 必然漂）。
 *
 * @param {object} p
 * @param {string} p.type          题型
 * @param {unknown[]} p.correct    正确答案（**包里的规范形式**）
 * @param {unknown[]} p.user       用户提交（已过 `normalizeUserValue`）
 * @param {boolean} [p.partialCredit] 题目自己声明的"是否允许部分分"（`answer.partial_credit`）
 * @param {{ partial_credit_ratio?: number, gradable_types?: string[] }} p.rules 来自包 `meta`
 * @returns {{ isCorrect: boolean, ratio: number }}
 */
export function gradeAnswer({ type, correct, user, partialCredit = false, rules }) {
  const { ratio: partialRatio, gradable } = readRules(rules);
  if (!gradable.includes(type)) {
    // ★ 出声，不静默判错：不可判分的题**本来就不该进练习**（导出时题源已过滤），
    //   走到这里说明包或题源有缺陷，判个 0 分只会把它藏起来。
    throw new Error(`题型 ${type} 不在包声明的可判分题型里（${gradable.join("/")}）`);
  }

  const norm = type === JUDGE_TYPE ? (x) => x : normToken;
  const c = new Set((correct ?? []).map(norm));
  const u = new Set((user ?? []).map(norm));

  // 没有正确答案的题：判错比判对安全，且这种数据缺陷必须能被看见
  //（Python 侧同一处也这么写 —— 两侧对"空答案"的态度必须一致）
  if (c.size === 0) return { isCorrect: false, ratio: 0 };
  if (u.size === c.size && [...u].every((x) => c.has(x))) return { isCorrect: true, ratio: 1 };
  if (type === MULTI_TYPE && partialCredit && u.size > 0 && u.size < c.size) {
    return { isCorrect: false, ratio: partialRatio };
  }
  return { isCorrect: false, ratio: 0 };
}
