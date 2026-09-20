"use client";

import type { ReactNode } from "react";

/**
 * 题干/解析的**只读预览**。
 *
 * ## 为什么自己写一个 30 行的解析器，而不是装 react-markdown
 *
 * 本批对编辑器的要求是「markdown / 纯文本 + HTML 预览优先，**不做完整富文本**」。
 * 为这点需求引一个 markdown 库（外加它的若干传递依赖和默认允许的原始 HTML）
 * 不划算；而且**完整 markdown 允许内联 HTML**，那就必须再配一个 sanitizer，
 * 否则就是给自己开一个 XSS 口子。
 *
 * 这里只支持一个**极小的子集**，并且**全部渲染成 React 元素**：
 *
 * | 语法 | 效果 |
 * |---|---|
 * | `**粗体**` | **粗体** |
 * | `*斜体*` / `_斜体_` | *斜体* |
 * | `` `代码` `` | `代码` |
 * | `- ` / `* ` / `+ ` 行首 | 无序列表 |
 * | `1. ` 行首 | 有序列表 |
 * | 空行 | 分段 |
 *
 * **输入里的 HTML 标签一律按纯文本原样显示**（不会被解析、更不会被执行）。
 * 这是刻意的：预览区永远是"输入什么就显示什么"，没有隐藏的解释层。
 * 需要真正富文本排版时，走下一批的专用编辑器，届时再一并引入 sanitizer。
 */

type Block =
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] };

/** 捕获组会被 `split` 一并返回，所以这个正则同时充当"切分器"和"识别器"。 */
const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_)/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return text
    .split(INLINE)
    .filter((s) => s !== "")
    .map((p, i) => {
      const key = `${keyPrefix}-${i}`;
      if (p.length > 4 && p.startsWith("**") && p.endsWith("**")) {
        return <strong key={key}>{p.slice(2, -2)}</strong>;
      }
      if (p.length > 2 && p.startsWith("`") && p.endsWith("`")) {
        return (
          <code key={key} className="yj-json rounded bg-muted px-1 py-0.5 text-[0.85em]">
            {p.slice(1, -1)}
          </code>
        );
      }
      if (
        p.length > 2 &&
        ((p.startsWith("*") && p.endsWith("*")) || (p.startsWith("_") && p.endsWith("_")))
      ) {
        return <em key={key}>{p.slice(1, -1)}</em>;
      }
      return <span key={key}>{p}</span>;
    });
}

function parseBlocks(src: string): Block[] {
  const out: Block[] = [];
  const lines = src.replace(/\r\n?/g, "\n").split("\n");

  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) {
      out.push({ kind: "p", text: para.join(" ") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(list);
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushPara();
      flushList();
      continue;
    }

    const ul = /^[-*+]\s+(.*)$/.exec(line);
    const ol = /^\d+[.)]\s+(.*)$/.exec(line);
    if (ul) {
      flushPara();
      if (!list || list.kind !== "ul") {
        flushList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(ul[1]);
      continue;
    }
    if (ol) {
      flushPara();
      if (!list || list.kind !== "ol") {
        flushList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(ol[1]);
      continue;
    }

    flushList();
    para.push(line);
  }

  flushPara();
  flushList();
  return out;
}

type Props = {
  text: string;
  className?: string;
  emptyHint?: string;
};

export function MarkdownPreview({ text, className, emptyHint = "预览会随输入实时更新。" }: Props) {
  const blocks = parseBlocks(text ?? "");

  if (!blocks.length) {
    return <p className="text-sm text-muted-foreground">{emptyHint}</p>;
  }

  return (
    <div className={className}>
      {blocks.map((b, i) => {
        const key = `b-${i}`;
        if (b.kind === "p") {
          return (
            <p key={key} className="mb-2 last:mb-0 leading-relaxed">
              {renderInline(b.text, key)}
            </p>
          );
        }
        if (b.kind === "ul") {
          return (
            <ul key={key} className="mb-2 list-disc space-y-0.5 pl-5 last:mb-0">
              {b.items.map((it, j) => (
                <li key={`${key}-${j}`}>{renderInline(it, `${key}-${j}`)}</li>
              ))}
            </ul>
          );
        }
        return (
          <ol key={key} className="mb-2 list-decimal space-y-0.5 pl-5 last:mb-0">
            {b.items.map((it, j) => (
              <li key={`${key}-${j}`}>{renderInline(it, `${key}-${j}`)}</li>
            ))}
          </ol>
        );
      })}
    </div>
  );
}
