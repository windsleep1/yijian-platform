/**
 * 大整数 ID 的解析安全带 —— **与 `apps/admin/src/lib/json-bigint.ts` 同源**。
 *
 * ⚠️ 为什么复制而不是抽进 `packages/api-core`：`docs/24` §2 已裁定 ——
 * 共享包只装"**两端必须逐字一致**"的三件事（码集 / 分类 / 单飞刷新）。
 * 本文件是**防御垫**，两端可以各自演进（例如 C 端将来要处理 `BigInt` 反序列化差异）。
 * 判据：**只要没有"改一边必须改另一边"的机制性理由，就不进包** ——
 * 否则包会变成"什么都往里塞"，最后没人敢改。
 *
 * ⚠️ 定位：**防御垫，非主路径。** 主路径是后端把 ID 序列化成字符串（`BigIntStr`）。
 * 它存在的唯一意义是：万一哪天有人新加了接口忘了套 `BigIntStr`，
 * 避免又出现"id 尾数悄悄变了 → 拼 URL 404"这种静默故障。
 *
 * ## 为什么必须先处理文本，而不能 parse 之后再转换
 *
 * 精度丢失发生在 `JSON.parse` **内部**，事后再遍历对象毫无意义：
 *
 *     JSON.parse('{"id":375228939615866880}')   // → { id: 375228939615866900 }
 *     String(parsed.id)                         // → "375228939615866900"  ← 已经错了
 *
 * 所以**绝不能**写"解析出 number → 转 BigInt → 再转字符串"的还原逻辑：
 * 得到的仍是错的数字，而且给调用方一种"已修好"的错觉，比不做更危险。
 * 唯一正确的时机是 parse **之前** —— 给裸数字字面量补引号。
 */

/**
 * 值位置的裸长整数：前面是 `:` / `,` / `[`，后面是 `,` / `}` / `]` 或空白。
 *
 * ⚠️ 字符类里的 `[` 是字面量**不需要转义**（末尾的 `\]` 才必须转义，否则会闭合字符类）。
 */
const BARE_BIGINT = /([:,[])\s*(\d{16,})(?=\s*[,}\]])/g;

export function parseJsonSafe<T>(text: string): T {
  return JSON.parse(text.replace(BARE_BIGINT, '$1"$2"')) as T;
}

/** 只用于**开发期告警**，不参与业务逻辑。 */
export function looksLikeUnsafeId(value: unknown): boolean {
  return typeof value === "number" && Number.isSafeInteger(value) === false;
}
