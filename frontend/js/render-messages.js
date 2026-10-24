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

export { isReadOnlyTool, groupMessagesIntoTurns } from "./render-turn-grouping.js";
import { isReadOnlyTool, groupMessagesIntoTurns } from "./render-turn-grouping.js";

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

export {
  renderTurnProcessBar,
  renderQuestionOptionsHtml,
  renderInteractiveQuestionsCard,
} from "./render-step-cards.js";
import {
  renderTurnProcessBar,
  renderQuestionOptionsHtml,
  renderInteractiveQuestionsCard,
} from "./render-step-cards.js";

export { renderMdDocQuickCards } from "./render-step-cards.js";
import { renderMdDocQuickCards } from "./render-step-cards.js";
import { AGENT_META } from "./config.js";
import { isSessionWorking } from "./data.js";
import { renderLiveStreamOverlay } from "./render-views.js";
import { formatRelativeTime, getBasename, getDurationStrFromDates, formatDuration } from "./utils.js";

export function renderMessagesList() {
  const container = document.getElementById("messages-container");
  if (!container) return;

  if (state.loadingMessages) {
    container.innerHTML = `
      <div class="space-y-4 p-2 sm:p-4 max-w-3xl mx-auto w-full animate-pulse opacity-70" id="messages-loading-skeleton">
        <div class="flex justify-end">
          <div class="h-10 bg-zinc-800/70 rounded-2xl w-2/5"></div>
        </div>
        <div class="flex justify-start">
          <div class="h-28 bg-zinc-900/80 border border-zinc-800/50 rounded-2xl w-4/5 p-3 space-y-2">
            <div class="h-3 bg-zinc-800/90 rounded w-1/3"></div>
            <div class="h-3 bg-zinc-800/50 rounded w-5/6"></div>
            <div class="h-3 bg-zinc-800/40 rounded w-2/3"></div>
          </div>
        </div>
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
  if (thinkingStart && lastTurn && (lastTurn.type === "assistant_turn" || lastTurn.type === "agent" || lastTurn.type === "assistant")) {
    const lastStep = (lastTurn.primarySteps && lastTurn.primarySteps.length > 0)
      ? lastTurn.primarySteps[lastTurn.primarySteps.length - 1]
      : (lastTurn.processRecords && lastTurn.processRecords.length > 0 ? lastTurn.processRecords[lastTurn.processRecords.length - 1] : null);
    const isLastStepRunning = lastStep && (lastStep.status === "RUNNING" || lastStep.status === "PENDING");
    const hasRunningTools = lastStep && Array.isArray(lastStep.tool_calls) && lastStep.tool_calls.some(
      (tc) => tc && (tc.status === "RUNNING" || tc.status === "PENDING")
    );
    if (!isLastStepRunning && !hasRunningTools) {
      const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
      if (!curSess || curSess.status !== "running") {
        delete state.pendingTurnThinking[state.selectedSessionId];
      }
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
          <div data-user-msg-id="${escapeHtml(m.item_id)}" data-user-msg-text="${escapeHtml(rawUserText)}" class="flex justify-end mb-4">
            <div class="max-w-[88%] sm:max-w-[76%] rounded-2xl rounded-tr-sm bg-zinc-900 border border-zinc-800 px-3.5 py-2.5 text-zinc-100 shadow-sm transition-all duration-300">
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
  updateStickyPromptVisibility();
}

export function scrollToBottom(smooth = false) {
  const container = document.getElementById("messages-container");
  if (!container) return;
  const doScroll = () => {
    const bottomAnchor = document.getElementById("messages-bottom-anchor");
    if (bottomAnchor && typeof bottomAnchor.scrollIntoView === "function") {
      bottomAnchor.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "end" });
    } else {
      container.scrollTop = container.scrollHeight;
    }
  };
  doScroll();
  requestAnimationFrame(() => {
    doScroll();
  });
  const jumpBtn = document.getElementById("scroll-bottom-pill");
  if (jumpBtn) jumpBtn.classList.add("hidden");
}


export function updateStickyPromptVisibility() {
  const container = document.getElementById("messages-container");
  const stickyBar = document.getElementById("sticky-user-prompt-bar");
  const stickyText = document.getElementById("sticky-prompt-text");
  const headerEl = document.querySelector("header");
  if (!container || !stickyBar || !stickyText) return;

  // 若处于最底部最新消息区域且未锁定滚动（非向上翻看历史模式），不显示置顶指令栏
  const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
  if (distFromBottom <= 120 && !state.autoScrollLocked) {
    stickyBar.classList.add("hidden");
    return;
  }

  const userEls = container.querySelectorAll("[data-user-msg-id]");
  if (userEls.length === 0) {
    stickyBar.classList.add("hidden");
    return;
  }

  if (headerEl) {
    stickyBar.style.top = `${headerEl.offsetHeight}px`;
  }

  const containerRect = container.getBoundingClientRect();
  const topThreshold = containerRect.top + 16;

  let activeUserEl = null;
  for (let i = 0; i < userEls.length; i++) {
    const el = userEls[i];
    const rect = el.getBoundingClientRect();
    if (rect.bottom < topThreshold) {
      activeUserEl = el;
    } else {
      break;
    }
  }

  if (activeUserEl) {
    const text = activeUserEl.getAttribute("data-user-msg-text") || "";
    const cleanText = text.replace(/\s+/g, " ").trim();
    stickyText.textContent = cleanText;
    stickyBar.dataset.targetId = activeUserEl.getAttribute("data-user-msg-id") || "";
    stickyBar.classList.remove("hidden");
  } else {
    stickyBar.classList.add("hidden");
  }
}

export function scrollToCurrentPrompt() {
  const stickyBar = document.getElementById("sticky-user-prompt-bar");
  const targetId = stickyBar?.dataset.targetId;
  if (!targetId) return;

  const container = document.getElementById("messages-container");
  const el = container?.querySelector(`[data-user-msg-id="${targetId}"]`);
  if (!el) return;

  el.scrollIntoView({ behavior: "smooth", block: "start" });

  const bubble = el.querySelector(".bg-zinc-900");
  if (bubble) {
    bubble.classList.add("ring-2", "ring-sky-400", "shadow-[0_0_16px_rgba(56,189,248,0.4)]");
    setTimeout(() => {
      bubble.classList.remove("ring-2", "ring-sky-400", "shadow-[0_0_16px_rgba(56,189,248,0.4)]");
    }, 1500);
  }
}
