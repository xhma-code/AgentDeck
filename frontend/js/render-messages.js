/**
 * AgentDeck 消息长流、Turn 聚合与交互问答卡片渲染器
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  saveToBoundedStore,
  icons,
} from "./utils.js";
import {
  renderMarkdownHtml,
  renderUnifiedDiffHtml,
} from "./markdown.js";

export function isReadOnlyTool(tc) {
  if (tc && typeof tc.is_read_only === "boolean") return tc.is_read_only;
  const name = String((tc && tc.name) || tc || "").toLowerCase().replace(/-/g, "_");
  return (
    name.includes("read_file") ||
    name.includes("view_file") ||
    name.includes("grep") ||
    name.includes("glob") ||
    name.includes("search_web") ||
    name.includes("file_search")
  );
}


export function updateCollapseStepsPillVisibility() {
  const pill = document.getElementById("collapse-steps-pill");
  if (!pill) return;
  const hasExpandedTurn =
    Object.values(state.expandedTurns || {}).some(Boolean) ||
    Object.values(state.turnThinkingExpanded || {}).some(Boolean);
  const container = document.getElementById("messages-container");
  const isScrolledDown = container ? container.scrollTop > 80 : false;
  pill.classList.toggle("hidden", !(hasExpandedTurn && isScrolledDown));
}


export function groupMessagesIntoTurns(messages) {
  const turns = [];
  let i = 0;

  while (i < messages.length) {
    const m = messages[i];
    if (m.role === "user") {
      turns.push({ type: "user", item: m });
      i++;
      continue;
    }

    // 收集紧随其后的连续 assistant 步骤，合并为一个回答轮次 (Assistant Turn)
    const assistantSteps = [];
    while (i < messages.length && messages[i].role !== "user") {
      assistantSteps.push(messages[i]);
      i++;
    }

    const firstStep = assistantSteps[0];
    const lastStep = assistantSteps[assistantSteps.length - 1];
    const turnKey = `${state.selectedSessionId || "sess"}_turn_${firstStep.step_index}`;

    // 找到最后一个包含非空 content 的步骤下标
    let lastContentIdx = -1;
    for (let idx = assistantSteps.length - 1; idx >= 0; idx--) {
      if ((assistantSteps[idx].content || "").trim()) {
        lastContentIdx = idx;
        break;
      }
    }

    // 划分：主回复正文 vs 收纳进折叠栏的中间过程步骤
    const primarySteps = [];
    const processRecords = [];
    const turnQuestions = [];
    let thinkingCount = 0;
    let toolCount = 0;
    let hasHighRisk = false;
    const editedFilesSet = new Set();
    const mdFilesMap = new Map(); // path -> basename

    const recordMdCandidate = (rawPath) => {
      if (!rawPath) return;
      const clean = String(rawPath).split("#")[0].split("?")[0].trim();
      if (/\.(md|markdown|mdx)$/i.test(clean)) {
        const base = getBasename(clean);
        if (base.toLowerCase() !== "skill.md") {
          mdFilesMap.set(clean, base);
        }
      }
    };

    assistantSteps.forEach((step, idx) => {
      if (Array.isArray(step.questions) && step.questions.length > 0) {
        turnQuestions.push(...step.questions);
      }
      const hasThinking = !!(step.thinking && step.thinking.trim());
      const tools = Array.isArray(step.tool_calls) ? step.tool_calls : [];
      const hasTools = tools.length > 0;
      const text = (step.content || "").trim();

      if (hasThinking) thinkingCount++;
      if (hasTools) {
        toolCount += tools.length;
        for (const tc of tools) {
          if (tc.risk_level === "high") hasHighRisk = true;
          const fPath = tc.file_path || "";
          if (tc.diff_text || /edit|write|replace|create/i.test(tc.name || "")) {
            const targetName = getBasename(fPath || tc.args_summary);
            if (targetName && targetName.length < 60) {
              editedFilesSet.add(targetName);
            }
          }
          recordMdCandidate(fPath);
          if (/\.(md|markdown|mdx)$/i.test(tc.args_summary || "")) {
            recordMdCandidate(tc.args_summary);
          }
        }
      }

      // 扫描正文中引用的 file:///.../*.md 链接（兼容含空格路径）
      if (text) {
        const linkMatches = text.matchAll(/\[[^\]]*\]\((file:\/\/\/[^\n)]+\.md(?:#[^\n)]*)?)\)/gi);
        for (const lm of linkMatches) {
          recordMdCandidate(lm[1]);
        }
      }

      // 判断该 step 的文本是最终主回复还是调用工具前的简短过渡说明：
      // 1. 最后一条非空文本必定作为主回复展示；
      // 2. 若前面某一步本身已是完整 Markdown 报告（>=160字或含代码块/标题/表格，且未同步捆绑工具调用），也保留在主界面，防止末尾一句短补充把主报告挤进折叠栏！
      const isSubstantialReport =
        text.length >= 160 || /```|^#{1,4}\s|\n\|.*\|.*\n/m.test(text);
      const isPrimaryText =
        text && (idx === lastContentIdx || (isSubstantialReport && !hasTools));
      const isIntermediateNote = text && !isPrimaryText;

      if (hasThinking || hasTools || isIntermediateNote) {
        processRecords.push({
          step_index: step.step_index,
          thinking: hasThinking ? step.thinking.trim() : "",
          thinking_len: step.thinking_len || (hasThinking ? step.thinking.trim().length : 0),
          intermediateNote: isIntermediateNote ? text : "",
          tool_calls: tools,
        });
      }

      if (isPrimaryText) {
        primarySteps.push({
          step_index: step.step_index,
          content: text,
          created_at: step.created_at,
        });
      }
    });

    turns.push({
      type: "assistant_turn",
      turnKey,
      processRecords,
      primarySteps,
      questions: turnQuestions,
      thinkingCount,
      toolCount,
      editedFiles: Array.from(editedFilesSet),
      mdFiles: Array.from(mdFilesMap.entries()).map(([path, name]) => ({ path, name })),
      hasHighRisk,
      createdAt: lastStep.created_at || firstStep.created_at,
      firstStepIndex: firstStep.step_index,
      lastStepIndex: lastStep.step_index,
    });
  }

  return turns;
}



export function renderTurnProcessBar(turn) {
  const {
    turnKey,
    processRecords,
    thinkingCount,
    toolCount,
    editedFiles,
    hasHighRisk,
  } = turn;

  if (!processRecords || processRecords.length === 0) return "";
  state.turnsMap[turnKey] = turn;

  const isExpanded = !!state.expandedTurns[turnKey] || !!state.turnThinkingExpanded[turnKey];
  const totalActions =
    toolCount + thinkingCount + processRecords.filter((r) => r.intermediateNote).length;

  // 构建折叠栏上的精炼徽章
  const badges = [];
  if (hasHighRisk) {
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/35 text-[10px] font-semibold shrink-0 flex items-center gap-1">
        <svg class="w-3 h-3 text-rose-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>高危</span>
      </span>`
    );
  }
  if (editedFiles.length > 0) {
    const fileHint =
      editedFiles.length === 1
        ? `改动 ${editedFiles[0]}`
        : `改动 ${editedFiles.length} 文件`;
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[10px] font-medium shrink-0 flex items-center gap-1">
        <svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span class="truncate max-w-[105px] sm:max-w-[180px]">${escapeHtml(fileHint)}</span>
      </span>`
    );
  }
  if (toolCount > 0) {
    badges.push(
      `<span class="hidden sm:inline-flex text-[11px] text-zinc-400 items-center gap-1 shrink-0">
        <svg class="w-3 h-3 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
        <span>${toolCount} 次调用</span>
      </span>`
    );
  }
  if (thinkingCount > 0) {
    badges.push(
      `<span class="hidden sm:inline-flex text-[11px] text-zinc-400 items-center gap-1 shrink-0">
        <svg class="w-3 h-3 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>${thinkingCount} 次思考</span>
      </span>`
    );
  }

  const detailsHtml = isExpanded
    ? `<div class="mt-2 pt-2 border-t border-zinc-800/80 space-y-2">
        ${processRecords
          .map((rec) => {
            const thinkDetailId = `${turnKey}_think_${rec.step_index}`;
            const isThinkExplicitlySet = typeof state.expandedDetails[thinkDetailId] === "boolean";
            const isThinkOpen = isThinkExplicitlySet
              ? Boolean(state.expandedDetails[thinkDetailId])
              : Boolean(state.turnThinkingExpanded[turnKey]);
            if (rec.thinking) {
              saveToBoundedStore("lazyDetailsStore", thinkDetailId, {
                type: "thinking",
                raw: rec.thinking,
              });
            }
            const displayThinkLen =
              rec.thinking_len || (rec.thinking ? rec.thinking.length : 0);
            const thinkHtml = rec.thinking
              ? `
                <details data-detail-id="${escapeHtml(thinkDetailId)}" ${
                  isThinkOpen ? "open" : ""
                } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                  thinkDetailId
                )}', '${escapeJsSingleQuote(turnKey)}')" class="rounded-lg bg-zinc-950/80 border border-zinc-800/80 px-2.5 py-1.5 text-[11px] text-zinc-400">
                  <summary onclick="window.appActions.onThinkSummaryClick(event, '${escapeJsSingleQuote(
                    turnKey
                  )}', '${escapeJsSingleQuote(thinkDetailId)}')" class="cursor-pointer select-none font-medium text-zinc-300 min-h-[34px] flex items-center justify-between px-0.5">
                    <span class="flex items-center gap-1.5 min-w-0">
                      <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a8 8 0 0 0-8 8c0 3.2 1.9 6 4.7 7.3V19a2 2 0 0 0 2 2h2.6a2 2 0 0 0 2-2v-1.7c2.8-1.3 4.7-4.1 4.7-7.3a8 8 0 0 0-8-8z"/><path d="M10 22h4"/></svg>
                      <span class="truncate">思考过程 (${displayThinkLen}字)</span>
                    </span>
                    <button type="button" onclick="window.appActions.copyThinkingText('${escapeJsSingleQuote(
                      thinkDetailId
                    )}', this, event)" class="min-h-[36px] min-w-[50px] px-2.5 py-1 rounded-md text-[11px] text-zinc-400 hover:text-sky-300 hover:bg-zinc-800/80 transition active:scale-95 flex items-center justify-center gap-1" title="复制思考内容">
                      <svg class="w-3 h-3 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                      <span>复制</span>
                    </button>
                  </summary>
                  <div data-lazy-slot class="mt-1.5 pt-1.5 border-t border-zinc-800/70 whitespace-pre-wrap leading-relaxed text-zinc-400 font-mono text-[11px] max-h-52 overflow-y-auto">${
                    isThinkOpen ? escapeHtml(rec.thinking) : ""
                  }</div>
                </details>
              `
              : "";

            const noteHtml = rec.intermediateNote
              ? `
                <div class="px-2.5 py-1.5 rounded-lg bg-zinc-900/70 border border-zinc-800/60 text-xs text-zinc-300">
                  <span class="text-[10px] text-sky-400 font-semibold mr-1 inline-flex items-center gap-0.5">
                    <svg class="w-3 h-3 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <span>过程说明:</span>
                  </span>
                  ${escapeHtml(rec.intermediateNote)}
                </div>
              `
              : "";

            const allTools = rec.tool_calls || [];
            const readTools = [];
            const actionTools = [];
            allTools.forEach((tc) => {
              if (isReadOnlyTool(tc)) {
                readTools.push(tc);
              } else {
                actionTools.push(tc);
              }
            });

            // 1. 只读探索类工具（如 read_file、grep）：紧凑微型胶囊流，不占纵向屏幕
            const readChipsHtml =
              readTools.length > 0
                ? `
                  <div class="read-tool-chips-row">
                    <span class="text-[10px] text-zinc-500 font-medium mr-1 select-none flex items-center gap-1 shrink-0">
                      <svg class="w-3 h-3 text-zinc-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>
                      查阅 (${readTools.length}):
                    </span>
                    ${readTools
                      .map((tc) => {
                        const target = getBasename(tc.file_path || tc.args_summary) || tc.args_summary;
                        return `
                          <span class="read-tool-chip" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                            <svg class="w-3 h-3 text-zinc-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                            <span class="truncate max-w-[150px]">${escapeHtml(target)}</span>
                          </span>
                        `;
                      })
                      .join("")}
                  </div>
                `
                : "";

            // 2. 变更类（edit_file/write_file）与执行类工具：高亮呈现
            const actionToolsHtml = actionTools
              .map((tc, tcIdx) => {
                const isHigh = tc.risk_level === "high";
                const isEditTool = !!tc.diff_text || /edit|write|replace|create/i.test(tc.name || "");
                const diffDetailId = `${turnKey}_diff_${rec.step_index}_${tcIdx}`;
                // 用户要求：思考过程里的 edit_file 默认不展开
                const isDiffOpen = Boolean(state.expandedDetails[diffDetailId]);
                if (tc.diff_text) {
                  saveToBoundedStore("lazyDetailsStore", diffDetailId, {
                    type: "diff",
                    raw: tc.diff_text,
                    filePath: tc.file_path || tc.args_summary || "",
                    addCount: tc.diff_add_count,
                    delCount: tc.diff_del_count,
                  });
                }

                const outDetailId = `${turnKey}_out_${rec.step_index}_${tcIdx}`;
                const isOutOpen = !!state.expandedDetails[outDetailId];
                if (tc.output) {
                  saveToBoundedStore("lazyDetailsStore", outDetailId, {
                    type: "output",
                    raw: tc.output,
                  });
                }

                const cardClass = isEditTool
                  ? "edit-tool-card"
                  : `rounded-lg bg-zinc-950/90 border ${
                      isHigh ? "border-rose-500/45" : "border-zinc-800/90"
                    } p-2 text-xs`;

                const badgeBg = isEditTool
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-zinc-800 text-sky-300";
                const badgeSvg = isEditTool
                  ? `<svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`
                  : `<svg class="w-3 h-3 text-sky-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`;
                const targetText = isEditTool
                  ? getBasename(tc.file_path || tc.args_summary) || tc.args_summary
                  : tc.args_summary;

                return `
                  <div class="${cardClass}">
                    <div class="flex items-center justify-between gap-2">
                      <div class="flex items-center gap-1.5 min-w-0">
                        <span class="px-1.5 py-0.5 rounded ${badgeBg} font-mono font-semibold text-[10px] flex items-center gap-1 shrink-0">
                          ${badgeSvg}
                          <span>${escapeHtml(tc.name)}</span>
                        </span>
                        <span class="font-mono text-[11px] ${
                          isEditTool ? "text-zinc-100 font-semibold" : "text-zinc-300"
                        } truncate" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                          ${escapeHtml(targetText)}
                        </span>
                      </div>
                      <span class="text-[9px] text-zinc-500 font-mono shrink-0">${escapeHtml(
                        tc.status || "DONE"
                      )}</span>
                    </div>
                    ${
                      tc.diff_text
                        ? `
                          <details data-detail-id="${escapeHtml(diffDetailId)}" ${
                            isDiffOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            diffDetailId
                          )}')" class="mt-1.5 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer select-none font-medium flex items-center justify-between text-emerald-300/90 hover:text-emerald-200 min-h-[34px] py-1 px-1 rounded hover:bg-emerald-500/10 transition">
                              <span class="flex items-center gap-1.5 min-w-0">
                                <svg class="w-3.5 h-3.5 text-emerald-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                <span class="truncate">代码变更 Diff</span>
                              </span>
                              <div class="flex items-center gap-1.5 font-mono text-[9px] shrink-0">
                                ${
                                  typeof tc.diff_add_count === "number"
                                    ? `<span class="diff-stat-badge diff-stat-add">+${tc.diff_add_count}</span>`
                                    : ""
                                }
                                ${
                                  typeof tc.diff_del_count === "number"
                                    ? `<span class="diff-stat-badge diff-stat-del">-${tc.diff_del_count}</span>`
                                    : ""
                                }
                                <span class="text-zinc-500 font-sans ml-0.5">
                                  <span class="toggle-arrow-open">收起 ▴</span>
                                  <span class="toggle-arrow-closed">展开 ▾</span>
                                </span>
                              </div>
                            </summary>
                            <div data-lazy-slot class="mt-1">${
                              isDiffOpen
                                ? renderUnifiedDiffHtml(tc.diff_text, tc.file_path || tc.args_summary, {
                                    addCount: tc.diff_add_count,
                                    delCount: tc.diff_del_count,
                                    diffId: diffDetailId,
                                  })
                                : ""
                            }</div>
                          </details>
                        `
                        : ""
                    }
                    ${
                      tc.output
                        ? `
                          <details data-detail-id="${escapeHtml(outDetailId)}" ${
                            isOutOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            outDetailId
                          )}')" class="mt-1.5 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer text-zinc-400 hover:text-zinc-200 select-none min-h-[34px] flex items-center justify-between py-1 px-1 rounded hover:bg-zinc-800/40 transition">
                              <span class="flex items-center gap-1.5 min-w-0">
                                <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                                <span class="truncate">查看执行输出 ${
                                  tc.is_truncated ? `(${tc.full_output_len}字·已截断)` : ""
                                }</span>
                              </span>
                              ${
                                tc.is_truncated
                                  ? `<button onclick="window.appActions.loadFullOutput('${escapeHtml(
                                      state.selectedSessionId
                                    )}', ${rec.step_index}, this, event)" class="min-h-[34px] sm:min-h-[28px] px-2.5 py-1 rounded-md bg-sky-500/20 text-sky-300 border border-sky-500/30 hover:bg-sky-500/30 text-[10px] shrink-0">加载完整输出</button>`
                                  : ""
                              }
                            </summary>
                            <pre data-lazy-slot class="mt-1 p-2 rounded bg-black/70 overflow-x-auto font-mono text-[10px] text-zinc-300 whitespace-pre-wrap break-all max-h-44 overflow-y-auto">${
                              isOutOpen ? escapeHtml(tc.output) : ""
                            }</pre>
                          </details>
                        `
                        : ""
                    }
                  </div>
                `;
              })
              .join("");

            return `<div class="space-y-1.5">${thinkHtml}${noteHtml}${readChipsHtml}${actionToolsHtml}</div>`;
          })
          .join("")}
        <div class="pt-2 text-center border-t border-zinc-800/60">
          <button type="button" onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
            class="min-h-[34px] px-3.5 py-1.5 rounded-lg bg-zinc-950/80 hover:bg-zinc-800 border border-zinc-800 text-[11px] text-zinc-400 hover:text-sky-300 font-medium transition active:scale-95 inline-flex items-center gap-1">
            <svg class="w-3.5 h-3.5 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 15l-6-6-6 6"/></svg>
            <span>收起已展开步骤</span>
          </button>
        </div>
      </div>`
    : "";

  return `
    <div id="turn-process-wrap-${escapeHtml(turnKey)}" class="mb-2 rounded-xl bg-zinc-900/50 hover:bg-zinc-900/70 border border-zinc-800/80 px-2.5 sm:px-3 py-1 transition-colors">
      <div onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
        class="cursor-pointer select-none min-h-[34px] flex items-center justify-between gap-1.5 sm:gap-2 active:opacity-75">
        <div class="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
          <span class="text-xs font-semibold text-zinc-200 flex items-center gap-1.5 shrink-0">
            <svg class="w-3.5 h-3.5 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            <span class="whitespace-nowrap">${totalActions} 步操作</span>
          </span>
          <div class="flex items-center gap-1 min-w-0 overflow-hidden">
            ${badges.join("")}
          </div>
        </div>
        <div class="text-[11px] text-sky-400 hover:text-sky-300 font-medium shrink-0 px-2 py-1 -mr-1 rounded-md hover:bg-zinc-800/50 transition flex items-center gap-0.5">
          <span>${isExpanded ? "收起 ▴" : "展开 ▾"}</span>
        </div>
      </div>
      ${detailsHtml}
    </div>
  `;
}


export function renderQuestionOptionsHtml(turnKey, qi, q) {
  const qKey = `${turnKey}_q${qi}`;
  const selectedOpts = state.questionSelections[qKey] || [];
  const isMulti = !!q.is_multi_select;
  return (q.options || [])
    .map((opt) => {
      const isSelected = selectedOpts.includes(opt);
      return `
        <button type="button"
          onclick="window.appActions.toggleQuestionOption('${escapeJsSingleQuote(
            turnKey
          )}', ${qi}, '${escapeJsSingleQuote(opt)}', ${isMulti})"
          class="text-left text-xs px-2.5 py-1.5 rounded-lg border transition-all active:scale-95 flex items-center gap-1.5 ${
            isSelected
              ? "bg-sky-500 text-zinc-950 font-bold border-sky-400 shadow-sm"
              : "bg-zinc-900/80 hover:bg-zinc-800 text-zinc-200 border-zinc-700/80"
          }">
          <span class="text-[10px] ${isSelected ? "text-zinc-950" : "text-sky-400"}">${
        isSelected ? (isMulti ? "☑" : "●") : isMulti ? "☐" : "○"
      }</span>
          <span class="break-words">${escapeHtml(opt)}</span>
        </button>
      `;
    })
    .join("");
}


export function renderInteractiveQuestionsCard(turn, isLatestTurn) {
  const questions = turn.questions || [];
  if (questions.length === 0) return "";
  const turnKey = turn.turnKey;

  // 历史轮次：转为轻量折叠卡片，展示已确认决议与选项，防止侵占手机长流
  if (!isLatestTurn) {
    const decision = state.persistedQuestionDecisions?.[turnKey];
    const summaryTitle = decision
      ? `✓ 已决策：${escapeHtml(decision.answerText.slice(0, 32))}${
          decision.answerText.length > 32 ? "..." : ""
        }`
      : `✓ 已完成的交互问答 (共 ${questions.length} 项)`;

    return `
      <details class="mt-2 rounded-xl bg-zinc-950/70 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
        <summary class="cursor-pointer select-none font-medium text-zinc-300 hover:text-sky-300 flex items-center justify-between">
          <span class="${decision ? "text-sky-300 font-semibold" : ""}">${summaryTitle}</span>
          <span class="text-[10px] text-zinc-500">查看明细 ▾</span>
        </summary>
        <div class="mt-2 pt-2 border-t border-zinc-800/70 space-y-2">
          ${
            decision
              ? `<div class="p-2 rounded-lg bg-sky-500/10 border border-sky-500/30 text-sky-200 text-xs">
                  <span class="font-semibold text-sky-400">答复记录：</span>${escapeHtml(
                    decision.answerText
                  )}
                </div>`
              : ""
          }
          ${questions
            .map(
              (q, qi) => `
                <div>
                  <div class="font-semibold text-zinc-200">Q${qi + 1}: ${escapeHtml(q.question)}</div>
                  ${
                    q.options && q.options.length > 0
                      ? `<div class="mt-1 pl-3 text-[11px] text-zinc-400 space-y-0.5">
                          ${q.options.map((opt) => `<div>• ${escapeHtml(opt)}</div>`).join("")}
                        </div>`
                      : ""
                  }
                </div>
              `
            )
            .join("")}
        </div>
      </details>
    `;
  }

  const questionsHtml = questions
    .map((q, qi) => {
      const qKey = `${turnKey}_q${qi}`;
      const writeInVal = state.questionWriteIns[qKey] || "";
      const isMulti = !!q.is_multi_select;
      const optsHtml = renderQuestionOptionsHtml(turnKey, qi, q);

      return `
        <div class="space-y-1.5 pt-1.5 first:pt-0">
          <div class="text-xs font-semibold text-zinc-100 flex items-start gap-1.5">
            <span class="text-sky-400 shrink-0">Q${qi + 1}:</span>
            <span class="whitespace-pre-wrap">${escapeHtml(q.question)}</span>
            ${
              isMulti
                ? `<span class="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[9px] shrink-0">多选</span>`
                : ""
            }
          </div>
          ${optsHtml ? `<div id="q-opts-${escapeHtml(qKey)}" class="flex flex-wrap gap-1.5 pl-4">${optsHtml}</div>` : ""}
          <div class="pl-4">
            <input type="text"
              value="${escapeHtml(writeInVal)}"
              placeholder="${q.options && q.options.length > 0 ? "或在此补充自定义回答..." : "输入您的回答..."}"
              oninput="window.appActions.setQuestionWriteIn('${escapeJsSingleQuote(
                turnKey
              )}', ${qi}, this.value)"
              class="w-full px-2.5 py-1 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-sky-500" />
          </div>
        </div>
      `;
    })
    .join('<div class="border-t border-zinc-800/60 my-2"></div>');

  return `
    <div class="mt-2.5 rounded-xl bg-zinc-900/90 border border-sky-500/40 p-3 shadow-lg space-y-2">
      <div class="flex items-center justify-between text-xs text-sky-400 font-semibold border-b border-zinc-800 pb-1.5">
        <span class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-sky-400 animate-ping"></span>
          <span>❓ Agent 正在向您提问确认</span>
        </span>
        <span class="text-[10px] text-zinc-400">单手点选即答</span>
      </div>
      <div class="space-y-2">${questionsHtml}</div>
      <div class="flex items-center justify-end gap-2 pt-1 border-t border-zinc-800/80">
        <button type="button"
          onclick="window.appActions.skipQuestionAnswers('${escapeJsSingleQuote(turnKey)}')"
          class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs transition">
          按推荐执行
        </button>
        <button type="button"
          onclick="window.appActions.submitQuestionAnswers('${escapeJsSingleQuote(turnKey)}')"
          class="px-3.5 py-1 rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs shadow transition active:scale-95 flex items-center gap-1">
          <span>✓ 发送回答</span>
        </button>
      </div>
    </div>
  `;
}


export function renderMdDocQuickCards(mdFiles) {
  if (!mdFiles || mdFiles.length === 0) return "";
  return `
    <div class="mt-2.5 flex flex-wrap gap-1.5">
      ${mdFiles
        .map(
          (doc) => `
            <button type="button"
              onclick="window.appActions.openFilePreview('${escapeJsSingleQuote(doc.path)}')"
              class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-sky-500/12 hover:bg-sky-500/20 border border-sky-500/35 text-left transition active:scale-95">
              <span class="text-base">📄</span>
              <div class="min-w-0">
                <div class="text-xs font-semibold text-sky-300 truncate">${escapeHtml(doc.name)}</div>
                <div class="text-[10px] text-zinc-400">点击全屏阅读 Markdown 文档</div>
              </div>
            </button>
          `
        )
        .join("")}
    </div>
  `;
}


export function renderMessagesList() {
  const container = document.getElementById("messages-container");
  if (!container) return;

  if (state.loadingMessages) {
    container.innerHTML = `
      <div class="space-y-3 p-3">
        <div class="skeleton-block h-14 w-2/3 ml-auto"></div>
        <div class="skeleton-block h-24 w-11/12"></div>
        <div class="skeleton-block h-16 w-4/5"></div>
      </div>
    `;
    return;
  }

  if (!state.selectedSessionId) {
    container.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center p-6 text-center text-zinc-400">
        <div class="w-12 h-12 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center text-sky-400 mb-3 shadow-sm">
          <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
        </div>
        <div class="text-sm font-semibold text-zinc-100 mb-1">选择对话或直接发消息</div>
        <p class="text-xs text-zinc-500 max-w-xs mb-4">
          多 Agent 移动控制台 · Antigravity / Claude Code / OpenCode
        </p>
        <button onclick="window.appActions.openNewSessionModal()"
          class="px-4 py-2 rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs transition active:scale-95 shadow">
          + 新建对话
        </button>
      </div>
    `;
    return;
  }

  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  const isWorking = isSessionWorking(state.selectedSessionId);

  if (state.messages.length === 0) {
    if (!isWorking) {
      const pPath = curSess?.project_path || "未绑定目录";
      const agentLabel = (AGENT_META[curSess?.agent_type] || AGENT_META.claude_code).label;
      container.innerHTML = `
        <div class="h-full flex flex-col items-center justify-center p-6 text-center text-zinc-400 max-w-md mx-auto">
          <div class="w-11 h-11 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center text-sky-400 mb-2.5 shadow-sm">
            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
          </div>
          <div class="text-sm font-semibold text-zinc-100 mb-1">${escapeHtml(curSess?.title || "新会话")}</div>
          <div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-zinc-900 border border-zinc-800 text-[11px] font-mono text-zinc-400 mb-4 max-w-full truncate">
            <span class="text-sky-400 font-sans font-medium">${escapeHtml(agentLabel)}</span>
            <span class="text-zinc-600">·</span>
            <span class="truncate">${escapeHtml(pPath)}</span>
          </div>
          <div class="w-full space-y-2 text-left">
            <div class="text-[11px] font-medium text-zinc-500 px-1">快速开始 / Starter Prompts：</div>
            <button onclick="window.appActions.fillPromptAndSend('查看当前代码仓库的 Git 状态与最近修改差异')"
              class="w-full p-2.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-200 transition text-left flex items-center justify-between group active:scale-[0.99]">
              <span>🔍 查看当前 Git 改动与状态差异</span>
              <span class="text-sky-400 text-xs group-hover:translate-x-0.5 transition-transform">→</span>
            </button>
            <button onclick="window.appActions.fillPromptAndSend('帮我审查当前项目的代码架构并指出潜在漏洞与优化点')"
              class="w-full p-2.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-200 transition text-left flex items-center justify-between group active:scale-[0.99]">
              <span>📋 审查项目代码架构并指出缺陷</span>
              <span class="text-sky-400 text-xs group-hover:translate-x-0.5 transition-transform">→</span>
            </button>
            <button onclick="window.appActions.fillPromptAndSend('梳理当前项目的开发计划与待办实施清单')"
              class="w-full p-2.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-200 transition text-left flex items-center justify-between group active:scale-[0.99]">
              <span>⚡ 梳理接下来待办任务与规划 (/plan)</span>
              <span class="text-sky-400 text-xs group-hover:translate-x-0.5 transition-transform">→</span>
            </button>
          </div>
        </div>
      `;
      return;
    }
  }

  const turns = groupMessagesIntoTurns(state.messages);
  const lastTurn = turns[turns.length - 1];
  const thinkingStart = state.pendingTurnThinking ? state.pendingTurnThinking[state.selectedSessionId] : null;
  if (thinkingStart && lastTurn && (lastTurn.type === "agent" || lastTurn.type === "assistant")) {
    const turnTime = new Date(lastTurn.createdAt || 0).getTime();
    const lastStep = lastTurn.primarySteps ? lastTurn.primarySteps[lastTurn.primarySteps.length - 1] : null;
    const isLastStepRunning = lastStep && lastStep.status === "RUNNING";
    if (turnTime >= thinkingStart - 1000 && !isLastStepRunning && !isSessionWorking(state.selectedSessionId)) {
      delete state.pendingTurnThinking[state.selectedSessionId];
    }
  }
  state.turnsMap = {};

  const olderBannerHtml = state.hasMoreMessages
    ? `
      <div class="mb-4 text-center">
        <button id="load-older-messages-btn" type="button" onclick="window.appActions.loadOlderMessages()"
          class="px-3.5 py-1.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-700/80 text-xs text-sky-300 font-medium transition active:scale-95">
          ↑ 加载更早消息 (已显示 ${state.messages.length}/${state.totalMessagesCount || "?"} 条)
        </button>
      </div>
    `
    : "";

  const html = turns
    .map((t) => {
      if (t.type === "user") {
        const m = t.item;
        const rawUserText = String(m.content || "").trim();
        const userLines = rawUserText.split("\n");
        const isLongUser = userLines.length > 8 || rawUserText.length > 360;
        const isUserExpanded = !!state.expandedUserMessages[m.item_id];
        const collapsedUserText = userLines.slice(0, 6).join("\n").slice(0, 280) + "...";
        const displayUserText =
          isLongUser && !isUserExpanded ? collapsedUserText : rawUserText;

        return `
          <div class="flex justify-end mb-4">
            <div class="max-w-[88%] sm:max-w-[76%] rounded-2xl rounded-tr-sm bg-zinc-900 border border-zinc-800 px-3.5 py-2.5 text-zinc-100 shadow-sm">
              <div id="user-msg-text-${escapeHtml(
                m.item_id
              )}" class="text-xs sm:text-[13px] leading-relaxed whitespace-pre-wrap break-words">${escapeHtml(
          displayUserText
        )}</div>
              ${
                isLongUser
                  ? `<button id="user-msg-btn-${escapeHtml(
                      m.item_id
                    )}" type="button" onclick="window.appActions.toggleUserMessage('${escapeJsSingleQuote(
                      m.item_id
                    )}')" class="mt-1 text-[11px] text-sky-400 font-medium hover:underline">
                      ${isUserExpanded ? "收起 ▴" : `展开全部 (${rawUserText.length}字) ▾`}
                    </button>`
                  : ""
              }
              <div class="text-[10px] text-zinc-500 text-right mt-1">${formatRelativeTime(
                m.created_at
              )}</div>
            </div>
          </div>
        `;
      }

      // Assistant Turn（沉浸式全宽文档流版式，无突兀分割线）
      const processBarHtml = renderTurnProcessBar(t);
      const mdBodyHtml = t.primarySteps
        .map((ps) => renderMarkdownHtml(ps.content, `${t.turnKey}_s${ps.step_index}`))
        .join('<div class="my-3"></div>');

      // 若本轮仅有中间过程说明而尚无最终正文，展示过程说明
      let fallbackStatusHtml = "";
      if (!mdBodyHtml && t.processRecords.length > 0) {
        const lastRec = t.processRecords[t.processRecords.length - 1];
        if (lastRec.intermediateNote) {
          fallbackStatusHtml = renderMarkdownHtml(lastRec.intermediateNote, `${t.turnKey}_note`);
        }
      }

      const mdCardsHtml = renderMdDocQuickCards(t.mdFiles);
      const isLastTurn = turns.indexOf(t) === turns.length - 1;
      const questionsCardHtml =
        t.questions && t.questions.length > 0 && isLastTurn
          ? renderInteractiveQuestionsCard(t, isLastTurn)
          : "";

      return `
        <article class="mb-6">
          ${processBarHtml}
          ${
            mdBodyHtml || fallbackStatusHtml
              ? `<div class="px-1 sm:px-2">${mdBodyHtml || fallbackStatusHtml}</div>`
              : ""
          }
          ${questionsCardHtml ? `<div class="px-1 sm:px-2">${questionsCardHtml}</div>` : ""}
          ${mdCardsHtml ? `<div class="px-1 sm:px-2">${mdCardsHtml}</div>` : ""}
          <div class="px-1 sm:px-2 mt-1.5 text-[10px] text-zinc-600 text-right">
            <span>${formatRelativeTime(t.createdAt)}</span>
          </div>
        </article>
      `;
    })
    .join("");

  container.innerHTML =
    `<div class="max-w-5xl mx-auto">` +
    olderBannerHtml +
    html +
    `<div id="live-stream-overlay-slot"></div><div id="messages-bottom-anchor"></div></div>`;
  renderLiveStreamOverlay();

  if (!state.autoScrollLocked) {
    scrollToBottom();
  }
  updateCollapseStepsPillVisibility();
}


export function scrollToBottom() {
  const container = document.getElementById("messages-container");
  if (!container) return;
  container.scrollTop = container.scrollHeight;
  const jumpBtn = document.getElementById("scroll-bottom-pill");
  if (jumpBtn) jumpBtn.classList.add("hidden");
}

