/**
 * AgentDeck 工具调用中间步骤折叠卡片与交互式问答卡片渲染引擎
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  extractToolDuration,
  formatDuration,
  getBasename,
  icons,
} from "./utils.js";
import { renderMarkdownHtml } from "./markdown.js";
import { isSessionWorking } from "./session-helper.js";

function hasMeaningfulOutput(output) {
  if (!output) return false;
  const stripped = String(output)
    .replace(/Created At:\s*[^\r\n]+/gi, "")
    .replace(/Completed At:\s*[^\r\n]+/gi, "")
    .replace(/Command exited with code 0\.?/gi, "")
    .trim();
  return stripped.length > 0;
}

export function getTurnRunningStartTime(turn, sessionId) {
  let startMs = null;
  if (sessionId && state.pendingTurnThinking && state.pendingTurnThinking[sessionId]) {
    startMs = state.pendingTurnThinking[sessionId];
  }
  if (!startMs && turn) {
    const rawCandidates = [
      turn.started_at,
      turn.start_at,
      turn.startTime,
      turn.start_time,
      turn.processRecords && turn.processRecords[0] && turn.processRecords[0].created_at,
      turn.primarySteps && turn.primarySteps[0] && turn.primarySteps[0].created_at,
      turn.createdAt,
      turn.created_at,
    ];
    for (const raw of rawCandidates) {
      if (!raw) continue;
      if (typeof raw === "number") {
        startMs = raw < 1000000000000 ? raw * 1000 : raw;
        break;
      }
      if (typeof raw === "string") {
        const parsed = new Date(raw).getTime();
        if (!isNaN(parsed) && parsed > 0) {
          startMs = parsed;
          break;
        }
      }
    }
  }
  return startMs;
}

export function updateRunningTurnDuration() {
  const el = document.getElementById("running-turn-duration");
  if (!el || !el.dataset.startMs) return;
  const startMs = Number(el.dataset.startMs);
  if (!startMs || isNaN(startMs) || startMs <= 0) return;
  const elapsedMs = Math.max(0, Date.now() - startMs);
  const formatted = elapsedMs < 1000 ? `${(elapsedMs / 1000).toFixed(1)}s` : formatDuration(elapsedMs);
  if (formatted && el.textContent !== formatted) {
    el.textContent = formatted;
  }
}

export function renderTurnProcessBar(turn, isRunningOverride) {
  const {
    turnKey,
    processRecords,
    thinkingCount,
    toolCount,
    editedFiles,
    hasHighRisk,
    durationStr,
  } = turn;

  const isSessionRunning = Boolean(
    state.selectedSessionId && isSessionWorking(state.selectedSessionId)
  );
  const isRunning =
    typeof isRunningOverride === "boolean"
      ? isRunningOverride
      : Boolean(turn.isRunning && isSessionRunning);
  turn.isRunning = isRunning;

  if ((!processRecords || processRecords.length === 0) && !isRunning) return "";
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
  const isEditsOnly = Boolean(state.turnFilterEdits[turnKey]);
  if (editedFiles.length > 0) {
    const fileHint =
      editedFiles.length === 1
        ? `改动 ${editedFiles[0]}`
        : `改动 ${editedFiles.length} 文件`;
    badges.push(
      `<button type="button" onclick="window.appActions.toggleTurnEditsOnly('${escapeJsSingleQuote(turnKey)}', event)"
        class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] leading-tight font-medium shrink-0 transition active:scale-95 cursor-pointer border ${
          isEditsOnly
            ? "bg-emerald-500/25 text-emerald-200 border-emerald-400 ring-1 ring-emerald-500/30"
            : "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/25"
        }" title="点击仅显示改动步骤">
        <svg class="w-2.5 h-2.5 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span class="truncate max-w-[105px] sm:max-w-[180px]">${escapeHtml(fileHint)}</span>
      </button>`
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

  const recordsToRender = isEditsOnly
    ? processRecords.filter((rec) =>
        (rec.tool_calls || []).some((tc) =>
          Boolean(tc.diff_text || /edit|write|replace|create/i.test(tc.name || ""))
        )
      )
    : processRecords;

  const filterNoticeHtml = isEditsOnly
    ? `<div class="px-2 py-1 rounded-md bg-emerald-950/40 border border-emerald-500/25 flex items-center justify-between text-[11px] text-emerald-300">
        <span class="flex items-center gap-1.5 font-medium">
          <svg class="w-3.5 h-3.5 text-emerald-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
          <span>仅显示代码改动步骤 (${recordsToRender.length} 项)</span>
        </span>
        <button type="button" onclick="window.appActions.toggleTurnEditsOnly('${escapeJsSingleQuote(turnKey)}', event)"
          class="text-[10px] text-emerald-400 hover:text-emerald-200 underline font-normal">
          显示全部步骤
        </button>
      </div>`
    : "";

  const detailsHtml = isExpanded
    ? `<div class="mt-2 pt-2 border-t border-zinc-800/80 space-y-2">
        ${filterNoticeHtml}
        ${recordsToRender
          .map((rec) => {

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
              if (isEditsOnly) {
                if (tc.diff_text || /edit|write|replace|create/i.test(tc.name || "")) {
                  actionTools.push(tc);
                }
              } else if (isReadOnlyTool(tc)) {
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
                  : `rounded-md bg-zinc-950/50 border ${
                      isHigh ? "border-rose-500/45" : "border-zinc-800/60"
                    } px-2 py-1 text-xs hover:border-zinc-700/60 transition`;

                const badgeBg = isEditTool
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-zinc-800 text-sky-300";
                const badgeSvg = isEditTool
                  ? `<svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`
                  : `<svg class="w-3 h-3 text-sky-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`;
                const targetText = isEditTool
                  ? getBasename(tc.file_path || tc.args_summary) || tc.args_summary
                  : tc.args_summary;
                const durStr = extractToolDuration(tc);

                return `
                  <div class="${cardClass}">
                    <div class="flex items-center justify-between gap-2">
                      <div class="flex items-center gap-1.5 min-w-0 flex-1 overflow-x-auto no-scrollbar py-0.5">
                        <span class="px-1.5 py-0.5 rounded ${badgeBg} font-mono font-semibold text-[10px] flex items-center gap-1 shrink-0">
                          ${badgeSvg}
                          <span>${escapeHtml(tc.name)}</span>
                        </span>
                        <span class="font-mono text-[11px] ${
                          isEditTool ? "text-zinc-100 font-semibold" : "text-zinc-300"
                        } whitespace-nowrap" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                          ${escapeHtml(targetText)}
                        </span>
                      </div>
                      <div class="flex items-center gap-1.5 shrink-0">
                        ${
                          durStr
                            ? `<span class="text-[10px] text-zinc-400 font-mono">${escapeHtml(
                                durStr
                              )}</span>`
                            : ""
                        }
                        <span class="text-[9px] ${
                          tc.status === "ERROR" || tc.status === "FAILED" || /Command exited with code [1-9]/.test(tc.output || "")
                            ? "text-rose-400 font-bold"
                            : "text-zinc-500"
                        } font-mono">${escapeHtml(
                          (tc.status === "ERROR" || tc.status === "FAILED" || /Command exited with code [1-9]/.test(tc.output || ""))
                            ? "失败"
                            : (tc.status || "DONE")
                        )}</span>
                      </div>
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
                      (() => {
                        const hasMeaningful = hasMeaningfulOutput(tc.output);
                        const isFail =
                          tc.status === "ERROR" ||
                          tc.status === "FAILED" ||
                          /Command exited with code [1-9]/.test(tc.output || "") ||
                          /\b(Error|Exception|Failed|Traceback)\b/i.test(tc.output || "");
                        if (!hasMeaningful && !isFail) return "";
                        return `
                          <details data-detail-id="${escapeHtml(outDetailId)}" ${
                            isOutOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            outDetailId
                          )}')" class="mt-1 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer ${
                              isFail ? "text-rose-300 hover:text-rose-200" : "text-zinc-400 hover:text-zinc-200"
                            } select-none min-h-[26px] flex items-center justify-between py-0.5 px-1 rounded hover:bg-zinc-800/40 transition">
                              <span class="flex items-center gap-1 min-w-0">
                                <svg class="w-3 h-3 ${isFail ? "text-rose-400" : "text-zinc-500"} shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                                <span class="truncate text-[10px]">${isFail ? "查看错误输出" : "输出"} ${
                                  tc.is_truncated ? `(${tc.full_output_len}字·已截断)` : ""
                                }</span>
                              </span>
                              ${
                                tc.is_truncated
                                  ? `<button onclick="window.appActions.loadFullOutput('${escapeHtml(
                                      state.selectedSessionId
                                    )}', ${rec.step_index}, this, event)" class="h-5 px-1.5 rounded leading-none bg-sky-500/15 text-sky-300 hover:bg-sky-500/25 border border-sky-500/30 text-[10px] sm:text-[9px] shrink-0 transition active:scale-95 flex items-center justify-center cursor-pointer touch-manipulation">加载完整输出</button>`
                                  : ""
                              }
                            </summary>
                            <pre data-lazy-slot class="mt-1 p-2 rounded bg-black/70 overflow-x-auto font-mono text-[10px] ${
                              isFail ? "text-rose-300 border border-rose-500/30" : "text-zinc-300"
                            } whitespace-pre-wrap break-all max-h-44 overflow-y-auto">${
                              isOutOpen ? escapeHtml(tc.output) : ""
                            }</pre>
                          </details>
                        `;
                      })()
                    }
                  </div>
                `;
              })
              .join("");

            return `<div class="space-y-1.5">${noteHtml}${readChipsHtml}${actionToolsHtml}</div>`;
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

  let displayDuration = durationStr;
  let runningStartMs = null;

  if (isRunning) {
    runningStartMs = getTurnRunningStartTime(turn, state.selectedSessionId);
    if (runningStartMs) {
      const elapsedMs = Math.max(0, Date.now() - runningStartMs);
      displayDuration = elapsedMs < 1000 ? `${(elapsedMs / 1000).toFixed(1)}s` : formatDuration(elapsedMs);
    }
  }

  const labelText = totalActions > 0 ? `${totalActions} 个执行步骤` : "正在思考与执行...";

  return `
    <div id="turn-process-wrap-${escapeHtml(turnKey)}" class="mb-2 rounded-xl bg-zinc-900/50 hover:bg-zinc-900/70 border border-zinc-800/80 px-3 sm:px-3.5 py-1 transition-colors">
      <div role="button" tabindex="0" aria-expanded="${isExpanded ? "true" : "false"}"
        onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
        onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}');}"
        class="cursor-pointer select-none min-h-[34px] flex items-center justify-between gap-1.5 sm:gap-2 active:opacity-75 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sky-500/50 rounded-lg px-0.5">
        <div class="flex items-center gap-2 min-w-0 flex-1 overflow-hidden">
          <span class="text-xs font-semibold text-zinc-200 flex items-center gap-1.5 shrink-0">
            ${
              isRunning
                ? `<span class="relative inline-flex items-center justify-center shrink-0 w-3.5 h-3.5 mr-0.5 overflow-visible" title="执行中">
                    <svg class="lightning-wave-a absolute inset-0 w-3.5 h-3.5 text-sky-400 fill-sky-400" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                    <svg class="lightning-wave-b absolute inset-0 w-3.5 h-3.5 text-sky-400 fill-sky-400" viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                    <svg class="lightning-glow-center relative w-3.5 h-3.5 text-sky-400 fill-sky-400" viewBox="0 0 24 24" stroke="currentColor" stroke-width="0.5"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                  </span>`
                : `<svg class="w-3.5 h-3.5 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`
            }
            <span class="whitespace-nowrap">${labelText}</span>
          </span>
          <div class="flex items-center gap-1 min-w-0 overflow-hidden">
            ${badges.join("")}
          </div>
          ${
            isRunning && runningStartMs
              ? `<span class="text-zinc-500 font-normal shrink-0">·</span><span id="running-turn-duration" data-start-ms="${runningStartMs}" class="text-[11px] font-mono text-sky-400 font-medium shrink-0">${escapeHtml(
                  displayDuration || "1s"
                )}</span>`
              : displayDuration
              ? `<span class="text-zinc-500 font-normal shrink-0">·</span><span class="text-[11px] font-mono text-zinc-400 font-normal shrink-0">${escapeHtml(
                  displayDuration
                )}</span>`
              : ""
          }
        </div>
        <div class="text-[11px] text-sky-400 hover:text-sky-300 font-medium shrink-0 px-2 py-1 -mr-1 rounded-md hover:bg-zinc-800/50 transition flex items-center gap-0.5">
          <span>${isExpanded ? "收起 ▴" : "展开 ▾"}</span>
        </div>
      </div>
      ${detailsHtml}
    </div>
  `;
}

export {
  renderQuestionOptionsHtml,
  renderInteractiveQuestionsCard,
} from "./render-interactive-questions.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";
import { isReadOnlyTool } from "./render-messages.js";
import { saveToBoundedStore } from "./utils.js";

export function renderMdDocQuickCards(mdFiles) {
  if (!mdFiles || mdFiles.length === 0) return "";
  return `
    <div class="mt-2.5 flex flex-wrap gap-1.5 max-w-full">
      ${mdFiles
        .map(
          (doc) => `
            <button type="button"
              onclick="window.appActions.openFilePreview('${escapeJsSingleQuote(doc.path)}')"
              class="flex items-start gap-2 px-3 py-1.5 rounded-xl bg-sky-500/12 hover:bg-sky-500/20 border border-sky-500/35 text-left transition active:scale-95 max-w-full">
              <span class="text-base shrink-0 mt-0.5">📄</span>
              <div class="min-w-0 flex-1">
                <div class="text-xs font-semibold text-sky-300 break-all whitespace-normal leading-snug">${escapeHtml(doc.name)}</div>
                <div class="text-[10px] text-zinc-400 mt-0.5">点击全屏阅读 Markdown 文档</div>
              </div>
            </button>
          `
        )
        .join("")}
    </div>
  `;
}
