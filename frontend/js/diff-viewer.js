/**
 * AgentDeck Unified Diff 代码对比渲染引擎
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  getBasename,
  saveToBoundedStore,
} from "./utils.js";

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
