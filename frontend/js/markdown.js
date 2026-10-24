/**
 * AgentDeck Markdown 渲染引擎、代码高亮与 Unified Diff 比较器
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  getBasename,
  isLocalPreviewableLink,
  saveToBoundedStore,
} from "./utils.js";

export function highlightCodeHtml(rawCode) {
  const escaped = escapeHtml(rawCode);
  const tokens = [];
  const putToken = (htmlSnippet) => {
    const id = `\x00TOK_${tokens.length}\x00`;
    tokens.push(htmlSnippet);
    return id;
  };

  // 1. 提取注释与字符串防止内部被关键字正则误匹配
  let work = escaped.replace(
    /(\/\/[^\n]*|#[^\n]*|&quot;(?:\\.|[^&])*?&quot;|'(?:\\.|[^'\n])*')/g,
    (m) => {
      if (m.startsWith("//") || m.startsWith("#")) {
        return putToken(`<span class="tok-cmt">${m}</span>`);
      }
      return putToken(`<span class="tok-str">${m}</span>`);
    }
  );

  // 2. 常见编程语言关键字
  work = work.replace(
    /\b(def|class|return|import|from|as|if|elif|else|for|while|try|except|finally|with|async|await|True|False|None|function|const|let|var|export|default|interface|type|struct|func|package|select|where|insert|update|delete)\b/g,
    '<span class="tok-kw">$1</span>'
  );

  // 3. 数字常量
  work = work.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');

  // 4. 还原字符串与注释 Token
  return work.replace(/\x00TOK_(\d+)\x00/g, (_, idx) => tokens[Number(idx)] || "");
}

/**
 * 行内 Markdown 轻量级格式化
 */
export function renderInlineMarkdown(rawLine) {
  let text = String(rawLine || "");

  // 1. 先抽离 Markdown 链接 [label](href)，兼容 file:///d:/Development Files/... 等含空格路径与 [`sym`](file://...)
  const links = [];
  text = text.replace(
    /\[([^\]]+)\]\((file:\/\/\/[^\n)]+|https?:\/\/[^\s)]+|conversation:\/\/[^\s)]+|[^)\n]+)\)/g,
    (_, label, rawHref) => {
      const idx = links.length;
      const href = rawHref.trim();
      const cleanLabel = label.replace(/`/g, "").trim() || getBasename(href);
      if (isLocalPreviewableLink(href)) {
        links.push(
          `<button type="button" onclick="window.appActions.openFilePreview('${escapeJsSingleQuote(
            href
          )}')" class="md-file-chip" title="点击在手机端全屏阅读 ${escapeHtml(href)}">📄 ${escapeHtml(
            cleanLabel
          )}</button>`
        );
      } else if (href.startsWith("conversation://")) {
        links.push(
          `<span class="md-inline-code" title="${escapeHtml(href)}">💬 ${escapeHtml(cleanLabel)}</span>`
        );
      } else if (/^https?:\/\//i.test(href)) {
        links.push(
          `<a href="${escapeHtml(
            href
          )}" target="_blank" rel="noopener noreferrer" class="text-sky-400 underline underline-offset-2 hover:text-sky-300">${escapeHtml(
            cleanLabel
          )} ↗</a>`
        );
      } else {
        links.push(`<span class="text-sky-300">${escapeHtml(cleanLabel)}</span>`);
      }
      return `\x00MDLINK_${idx}\x00`;
    }
  );

  // 2. 抽离行内代码 `...`
  const inlineCodes = [];
  text = text.replace(/`([^`\n]+)`/g, (_, codeContent) => {
    const idx = inlineCodes.length;
    inlineCodes.push(`<code class="md-inline-code">${escapeHtml(codeContent)}</code>`);
    return `\x00INLCODE_${idx}\x00`;
  });

  // 3. 转义剩余普通文本
  text = escapeHtml(text);

  // 4. 加粗 **...**、斜体 *...*、删除线 ~~...~~
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/~~([^~]+)~~/g, '<del class="text-zinc-500">$1</del>');
  text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");

  // 5. 还原行内代码与链接
  text = text.replace(/\x00INLCODE_(\d+)\x00/g, (_, i) => inlineCodes[Number(i)] || "");
  text = text.replace(/\x00MDLINK_(\d+)\x00/g, (_, i) => links[Number(i)] || "");
  return text;
}



export function renderMarkdownHtml(rawMd, scopeKey = "md") {
  if (!rawMd) return "";
  let text = String(rawMd).replace(/\r\n/g, "\n").trim();
  if (!text) return "";

  const codeBlocks = [];
  const storeCodeBlock = (lang, codeContent) => {
    const idx = codeBlocks.length;
    const codeId = `${scopeKey}_cb_${idx}`;
    const cleanCode = String(codeContent || "").replace(/\n$/, "");
    state.codeBlockStore[codeId] = cleanCode;

    const lines = cleanCode.split("\n");
    const lineCount = lines.length;
    const isLong = lineCount > 15;
    const isExpanded = !!state.expandedCodeBlocks[codeId];
    const shouldCollapse = isLong && !isExpanded;
    const displayLang = (lang || "text").trim().toLowerCase() || "code";

    const cardHtml = `
      <div class="md-code-card">
        <div class="md-code-header">
          <div class="flex items-center gap-2">
            <span class="text-sky-400 font-semibold uppercase">${escapeHtml(displayLang)}</span>
            <span class="text-[10px] text-zinc-500">${lineCount} 行</span>
          </div>
          <button type="button" onclick="window.appActions.copyCodeBlock('${escapeJsSingleQuote(
            codeId
          )}', this)"
            class="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-[11px] transition active:scale-95">
            📋 复制
          </button>
        </div>
        <div id="code-body-${escapeHtml(codeId)}" class="md-code-body ${
      shouldCollapse ? "collapsed" : ""
    }">${highlightCodeHtml(cleanCode)}${
      shouldCollapse ? `<div class="md-code-fade"></div>` : ""
    }</div>
        ${
          isLong
            ? `<button id="code-btn-${escapeHtml(codeId)}" type="button" data-line-count="${lineCount}" onclick="window.appActions.toggleCodeBlock('${escapeJsSingleQuote(
                codeId
              )}')"
                class="w-full py-1.5 bg-zinc-900/95 hover:bg-zinc-800/90 border-t border-zinc-800/80 text-sky-400 text-[11px] font-medium text-center transition">
                ${isExpanded ? "⬆ 收起代码" : `⬇ 展开全部代码 (共 ${lineCount} 行)`}
              </button>`
            : ""
        }
      </div>
    `;
    codeBlocks.push(cardHtml);
    return `\n\x00CODEBLOCK_${idx}\x00\n`;
  };

  // 提取 4 反引号代码块与 3 反引号代码块（兼容行首缩进、特殊语言标识及无语言标识）
  text = text.replace(/^[ \t]*````([^\n]*)\n([\s\S]*?)^[ \t]*````[ \t]*$/gm, (_, lang, code) =>
    storeCodeBlock(lang, code)
  );
  text = text.replace(/^[ \t]*```([^\n]*)\n([\s\S]*?)^[ \t]*```[ \t]*$/gm, (_, lang, code) =>
    storeCodeBlock(lang, code)
  );

  const lines = text.split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    // 1. 代码块占位符
    const cbMatch = trimmed.match(/^\x00CODEBLOCK_(\d+)\x00$/);
    if (cbMatch) {
      out.push(codeBlocks[Number(cbMatch[1])] || "");
      i++;
      continue;
    }

    // 2. 水平分割线
    if (/^(?:---|\*\*\*|___)\s*$/.test(trimmed)) {
      out.push("<hr />");
      i++;
      continue;
    }

    // 3. 标题 H1 ~ H6
    const hMatch = line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (hMatch) {
      const level = Math.min(4, hMatch[1].length);
      out.push(`<h${level}>${renderInlineMarkdown(hMatch[2].trim())}</h${level}>`);
      i++;
      continue;
    }

    // 4. 引用块与 GitHub Alert (> [!NOTE] 等)
    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      const firstLine = (quoteLines[0] || "").trim();
      const alertMatch = firstLine.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
      if (alertMatch) {
        const kind = alertMatch[1].toLowerCase();
        const inlineRest = alertMatch[2].trim();
        const alertMeta = {
          note: { icon: "ℹ️", label: "说明" },
          tip: { icon: "💡", label: "提示" },
          important: { icon: "🟣", label: "重要" },
          warning: { icon: "⚠️", label: "警告" },
          caution: { icon: "🚨", label: "注意" },
        }[kind] || { icon: "ℹ️", label: "提示" };

        const bodyLines = inlineRest ? [inlineRest, ...quoteLines.slice(1)] : quoteLines.slice(1);
        const bodyHtml = bodyLines
          .filter((l) => l.trim())
          .map((l) => `<div>${renderInlineMarkdown(l)}</div>`)
          .join("");
        out.push(`
          <div class="md-alert md-alert-${kind}">
            <div class="md-alert-title"><span>${alertMeta.icon}</span><span>${alertMeta.label}</span></div>
            <div class="space-y-1 text-zinc-200">${bodyHtml}</div>
          </div>
        `);
      } else {
        const qBody = quoteLines
          .map((l) => (l.trim() ? renderInlineMarkdown(l) : "<br />"))
          .join("<br />");
        out.push(`<blockquote class="md-blockquote">${qBody}</blockquote>`);
      }
      continue;
    }

    // 5. GFM 表格 (| col1 | col2 | 且下一行为 | --- | --- |)
    if (
      trimmed.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1].trim()) &&
      lines[i + 1].includes("-")
    ) {
      const parseTableRow = (rStr) =>
        rStr
          .trim()
          .replace(/^\|/, "")
          .replace(/\|$/, "")
          .split("|")
          .map((c) => c.trim());

      const headers = parseTableRow(lines[i]);
      i += 2; // 跳过表头与分隔线
      const rows = [];
      while (i < lines.length && lines[i].trim().includes("|") && lines[i].trim() !== "") {
        rows.push(parseTableRow(lines[i]));
        i++;
      }

      const thHtml = headers.map((h) => `<th>${renderInlineMarkdown(h)}</th>`).join("");
      const trsHtml = rows
        .map(
          (r) =>
            `<tr>${headers
              .map((_, colIdx) => `<td>${renderInlineMarkdown(r[colIdx] || "")}</td>`)
              .join("")}</tr>`
        )
        .join("");

      out.push(`
        <div class="md-table-wrap">
          <table class="md-table">
            <thead><tr>${thHtml}</tr></thead>
            <tbody>${trsHtml}</tbody>
          </table>
        </div>
      `);
      continue;
    }

    // 6. 无序或有序列表（支持多级缩进子列表，防止子列表冲乱外层有序列表序号）
    if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) {
      const isOrdered = /^\s*\d+\.\s+/.test(line);
      const tag = isOrdered ? "ol" : "ul";
      const itemsHtml = [];

      while (i < lines.length && /^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i])) {
        const curLine = lines[i];
        const indentLen = (curLine.match(/^\s*/) || [""])[0].length;
        const rawItem = curLine.replace(/^\s*(?:[-*+]|\d+\.)\s+/, "");
        const taskMatch = rawItem.match(/^\[([ xX])\]\s+(.*)$/);
        if (taskMatch) {
          const checked = taskMatch[1].toLowerCase() === "x";
          itemsHtml.push(
            `<li class="list-none -ml-4 flex items-start gap-1.5"><span>${
              checked ? "☑️" : "⬜"
            }</span><span class="${checked ? "line-through text-zinc-400" : ""}">${renderInlineMarkdown(
              taskMatch[2]
            )}</span></li>`
          );
        } else if (indentLen >= 2) {
          itemsHtml.push(
            `<li class="list-none ml-3 flex items-start gap-1.5 text-zinc-300"><span class="text-sky-400/80 select-none">•</span><span class="flex-1">${renderInlineMarkdown(
              rawItem
            )}</span></li>`
          );
        } else {
          itemsHtml.push(`<li>${renderInlineMarkdown(rawItem)}</li>`);
        }
        i++;
      }
      out.push(`<${tag}>${itemsHtml.join("")}</${tag}>`);
      continue;
    }

    // 7. 普通段落（合并连续普通文本行）
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^\x00CODEBLOCK_\d+\x00$/.test(lines[i].trim()) &&
      !/^(?:---|\*\*\*|___)\s*$/.test(lines[i].trim()) &&
      !/^\s*#{1,6}\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i]) &&
      !(
        lines[i].trim().includes("|") &&
        i + 1 < lines.length &&
        /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1].trim()) &&
        lines[i + 1].includes("-")
      )
    ) {
      paraLines.push(renderInlineMarkdown(lines[i]));
      i++;
    }
    if (paraLines.length > 0) {
      out.push(`<p>${paraLines.join("<br />")}</p>`);
    }
  }

  return `<div class="md-prose">${out.join("")}</div>`;
}


export function renderUnifiedDiffHtml(diffText, filePath = "", options = {}) {
  if (!diffText) return "";
  const lines = diffText.split("\n");
  let addCount = 0;
  let delCount = 0;

  const renderedLines = lines
    .map((line) => {
      let cls = "diff-line text-zinc-300";
      if (line.startsWith("+++") || line.startsWith("---")) {
        cls = "diff-line text-zinc-400 font-semibold";
      } else if (line.startsWith("@@")) {
        cls = "diff-line diff-line-hunk";
      } else if (line.startsWith("+")) {
        cls = "diff-line diff-line-add";
        addCount++;
      } else if (line.startsWith("-")) {
        cls = "diff-line diff-line-del";
        delCount++;
      }
      return `<div class="${cls}">${escapeHtml(line)}</div>`;
    })
    .join("");

  const fileName = filePath ? getBasename(filePath) : "";
  const isModal = !!options.isModal;
  const finalAddCount = typeof options.addCount === "number" ? options.addCount : addCount;
  const finalDelCount = typeof options.delCount === "number" ? options.delCount : delCount;

  // 避免在 HTML 属性中拼接大字符串导致单引号/特殊字符截断失效：统一注册到带容量上限的内存存储
  const diffId = options.diffId || `diff_${Math.random().toString(36).slice(2, 9)}`;
  saveToBoundedStore("diffStore", diffId, {
    diffText,
    filePath,
    addCount: finalAddCount,
    delCount: finalDelCount,
  });

  return `
    <div class="unified-diff-card">
      <div class="unified-diff-header">
        <div class="flex items-center gap-1.5 min-w-0">
          <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <span class="font-mono font-medium text-zinc-200 truncate text-[11px]" title="${escapeHtml(filePath)}">
            ${escapeHtml(fileName || "代码变更")}
          </span>
          <div class="flex items-center gap-1 font-mono shrink-0 ml-1">
            <span class="diff-stat-badge diff-stat-add">+${finalAddCount}</span>
            <span class="diff-stat-badge diff-stat-del">-${finalDelCount}</span>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button type="button" data-action="toggle-word-wrap" onclick="window.appActions.toggleWordWrap()" class="text-zinc-400 hover:text-sky-300 text-[10px] transition">
            ${state.wordWrapDiff ? "横向滚动" : "自动换行"}
          </button>
          ${
            !isModal
              ? `<button type="button" onclick="window.appActions.openDiffFullscreenById('${escapeJsSingleQuote(diffId)}', event)" class="px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-sky-300 text-[10px] flex items-center gap-0.5 transition active:scale-95" title="全屏查看 Diff">
                  <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
                  <span>全屏</span>
                </button>`
              : ""
          }
        </div>
      </div>
      <div class="unified-diff-box ${state.wordWrapDiff ? "word-wrap" : ""} ${
        !isModal ? "max-h-64" : ""
      } overflow-y-auto">
        ${renderedLines}
      </div>
    </div>
  `;
}
