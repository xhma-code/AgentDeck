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
  const pill = document.getElementById("scroll-bottom-pill");
  if (!pill) return;
  const hasExpandedTurn =
    Object.values(state.expandedTurns || {}).some(Boolean) ||
    Object.values(state.turnThinkingExpanded || {}).some(Boolean) ||
    Object.values(state.expandedDetails || {}).some(Boolean) ||
    Object.values(state.expandedCodeBlocks || {}).some(Boolean) ||
    Object.values(state.turnFilterEdits || {}).some(Boolean);
  const container = document.getElementById("messages-container");
  if (!container) {
    pill.classList.add("hidden");
    return;
  }
  const distFromBottom =
    container.scrollHeight - container.scrollTop - container.clientHeight;
  const isNotAtBottom = distFromBottom > 120;
  if (isNotAtBottom) {
    pill.classList.remove("hidden");
  } else {
    pill.classList.add("hidden");
  }
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

export function updateFooterVisibility() {
  const footer = document.getElementById("chat-footer");
  if (!footer) return;
  if (state.serverOffline && (!state.messages || state.messages.length === 0)) {
    footer.classList.add("hidden");
  } else {
    footer.classList.remove("hidden");
  }
}

function renderServerOfflineView() {
  const isRetrying = state.isRetryingConnection;
  return `
    <div class="h-full flex flex-col items-center justify-center p-4 sm:p-6 text-center text-zinc-400 max-w-md mx-auto my-auto select-none animate-fade-in">
      <div class="relative w-14 h-14 rounded-2xl bg-amber-500/10 border border-amber-500/25 flex items-center justify-center text-amber-400 mb-3.5 shadow-[0_0_24px_rgba(245,158,11,0.12)]">
        <svg class="w-7 h-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
          <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
          <line x1="6" y1="6" x2="6.01" y2="6"></line>
          <line x1="6" y1="18" x2="6.01" y2="18"></line>
          <line x1="13" y1="6" x2="18" y2="6"></line>
        </svg>
        <span class="absolute -top-1 -right-1 flex h-3 w-3">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
          <span class="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></span>
        </span>
      </div>

      <div class="text-base font-bold text-zinc-100 mb-1.5 tracking-tight">未连接到 AgentDeck 服务端</div>
      <p class="text-xs text-zinc-400 max-w-xs mb-5 leading-relaxed">
        无法与电脑端后台建立连接，请确认服务已启动或检查网络环境
      </p>

      <div class="w-full p-3.5 rounded-2xl bg-zinc-950/70 border border-zinc-800/80 text-[11px] text-zinc-400 text-left space-y-2.5 mb-5 shadow-sm">
        <div class="text-[10px] font-semibold text-zinc-500 tracking-wider uppercase">排查建议 / Troubleshooting</div>
        <div class="flex items-start gap-2.5">
          <span class="text-xs shrink-0 mt-0.5">🖥️</span>
          <span class="leading-relaxed"><strong class="text-zinc-200">电脑端服务：</strong>确认宿主机已启动 AgentDeck（终端运行 <code class="text-sky-400 font-mono text-[10px] bg-zinc-900 px-1 py-0.5 rounded">agy deck</code>）</span>
        </div>
        <div class="flex items-start gap-2.5">
          <span class="text-xs shrink-0 mt-0.5">🌐</span>
          <span class="leading-relaxed"><strong class="text-zinc-200">网络与通道：</strong>确认手机与电脑在同一 Wi-Fi，或 Tailscale / ZeroTier 正常连通</span>
        </div>
        <div class="flex items-start gap-2.5">
          <span class="text-xs shrink-0 mt-0.5">⚡</span>
          <span class="leading-relaxed"><strong class="text-zinc-200">电脑休眠：</strong>宿主机处于锁屏睡眠时，后台网络连接可能被挂起</span>
        </div>
      </div>

      <div class="w-full flex flex-col gap-2.5">
        <button onclick="window.appActions.retryServerConnection()"
          ${isRetrying ? "disabled" : ""}
          class="w-full py-2.5 px-4 rounded-xl font-bold text-xs transition active:scale-95 shadow flex items-center justify-center gap-2 ${
            isRetrying
              ? "bg-sky-500/50 text-zinc-950 cursor-wait"
              : "bg-sky-500 hover:bg-sky-400 text-zinc-950 cursor-pointer"
          }">
          ${
            isRetrying
              ? `<svg class="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>正在连接服务端...</span>`
              : `<svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 0 1 15-6.7L21 8"></path><path d="M3 22v-6h6"></path><path d="M21 12a9 9 0 0 1-15 6.7L3 16"></path></svg><span>立即重试连接</span>`
          }
        </button>

        <button onclick="window.appActions.showPairingGuideModal()"
          class="w-full py-2 px-3 rounded-xl bg-zinc-900/80 hover:bg-zinc-800 border border-zinc-800 text-zinc-300 hover:text-white text-xs transition active:scale-95 flex items-center justify-center gap-1.5 cursor-pointer">
          <svg class="w-3.5 h-3.5 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>
          <span>重新配对 / 更换连接通道</span>
        </button>
      </div>

      <div class="mt-4 flex items-center gap-1.5 text-[11px] text-zinc-500">
        <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400/80 animate-ping"></span>
        <span>后台每 5 秒自动检测连通性，服务恢复时将自动载入</span>
      </div>
    </div>
  `;
}

export function renderMessagesList() {
  const container = document.getElementById("messages-container");
  if (!container) return;

  if (state.serverOffline && (!state.messages || state.messages.length === 0)) {
    updateFooterVisibility();
    container.innerHTML = renderServerOfflineView();
    return;
  }
  updateFooterVisibility();

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
      const isLastTurn = turns.indexOf(t) === turns.length - 1;
      const isCurrentRunning = isWorking && isLastTurn;
      t.isRunning = isCurrentRunning;
      const processBarHtml = renderTurnProcessBar(t, isCurrentRunning);
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
      const questionsCardHtml =
        t.questions && t.questions.length > 0 && isLastTurn
          ? renderInteractiveQuestionsCard(t, isLastTurn)
          : "";

      return `
        <article class="mb-6 max-w-full min-w-0">
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

  // 若会话正处于运行中，但后端尚未落盘第一个 assistant 步骤（如刚下发指令），立即呈现瞬时运行状态条
  let pendingAssistantHtml = "";
  if (isWorking && (!lastTurn || lastTurn.type === "user")) {
    const pendingTurn = {
      type: "assistant_turn",
      turnKey: `pending_${state.selectedSessionId}`,
      processRecords: [],
      thinkingCount: 0,
      toolCount: 0,
      editedFiles: [],
      mdFiles: [],
      hasHighRisk: false,
      isRunning: true,
      isPendingInitial: true,
      createdAt: new Date().toISOString(),
      started_at: (state.pendingTurnThinking && state.pendingTurnThinking[state.selectedSessionId]) || Date.now(),
    };
    pendingAssistantHtml = `
      <article class="mb-6">
        ${renderTurnProcessBar(pendingTurn, true)}
      </article>
    `;
  }

  container.innerHTML =
    `<div class="max-w-5xl mx-auto">` +
    olderBannerHtml +
    html +
    pendingAssistantHtml +
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
  // 若用户正在输入框聚焦输入，严禁强制滚屏打断用户视口与软键盘输入状态
  const promptInput = document.getElementById("prompt-input");
  if (promptInput && document.activeElement === promptInput) {
    return;
  }
  const targetTop = container.scrollHeight - container.clientHeight;
  if (smooth && typeof container.scrollTo === "function") {
    container.scrollTo({ top: targetTop, behavior: "smooth" });
  } else {
    container.scrollTop = container.scrollHeight;
  }
  const jumpBtn = document.getElementById("scroll-bottom-pill");
  if (jumpBtn) jumpBtn.classList.add("hidden");
}


export function renderStickyPromptContent(userElsOverride = null) {
  const container = document.getElementById("messages-container");
  const contentEl = document.getElementById("sticky-prompt-content");
  const badgeEl = document.getElementById("sticky-prompt-badge");
  const stickyBar = document.getElementById("sticky-user-prompt-bar");
  const rowEl = document.getElementById("sticky-prompt-row");
  const jumpBtn = document.getElementById("sticky-prompt-jump-btn");
  if (!container || !contentEl) return;

  const userEls = userElsOverride || Array.from(container.querySelectorAll("[data-user-msg-id]"));
  if (userEls.length === 0) return;

  const total = userEls.length;
  let idx = typeof state.stickyPromptActiveIndex === "number" ? state.stickyPromptActiveIndex : 0;
  if (idx < 0) idx = 0;
  if (idx >= total) idx = total - 1;
  state.stickyPromptActiveIndex = idx;

  const activeEl = userEls[idx];
  const rawText = activeEl.getAttribute("data-user-msg-text") || "";
  const cleanText = rawText.trim();

  // 1. 文本内容与展开/单行样式（点击文字直接切换收起/展开全部）
  contentEl.textContent = cleanText;
  const isExpanded = state.stickyPromptExpanded !== false;

  if (isExpanded) {
    // 展开状态：支持多行展示与滚动，外层顶部对齐
    contentEl.className =
      "text-xs text-zinc-200 font-medium leading-relaxed max-h-36 sm:max-h-48 overflow-y-auto whitespace-pre-wrap break-words subtle-scrollbar hover:text-sky-200 transition-colors cursor-pointer select-text flex-1";
    contentEl.title = "点击收起为单行";
    if (rowEl) {
      rowEl.className = "max-w-5xl mx-auto flex items-start justify-between gap-2.5 min-h-[26px]";
    }
    if (jumpBtn) {
      jumpBtn.classList.add("mt-0.5");
    }
  } else {
    // 收起状态：单行截断，文本与右侧按钮严格垂直居中（items-center + leading-normal）
    contentEl.className =
      "text-xs text-zinc-200 font-medium truncate whitespace-nowrap overflow-hidden leading-normal hover:text-sky-200 transition-colors cursor-pointer select-text flex-1";
    contentEl.title = "点击展开显示全部指令内容";
    if (rowEl) {
      rowEl.className = "max-w-5xl mx-auto flex items-center justify-between gap-2.5 min-h-[26px]";
    }
    if (jumpBtn) {
      jumpBtn.classList.remove("mt-0.5");
    }
  }

  // 2. 右侧按钮内部的数字序号：与向上箭头合并一体（例如 ↑ 1/2）
  if (badgeEl) {
    badgeEl.textContent = `${idx + 1}/${total}`;
  }
  if (jumpBtn) {
    jumpBtn.title = `第 ${idx + 1}/${total} 轮提问，点击返回原位置`;
  }

  if (stickyBar) {
    stickyBar.dataset.targetId = activeEl.getAttribute("data-user-msg-id") || "";
  }
}

export function toggleStickyPromptExpanded(e) {
  if (e) e.stopPropagation();
  state.stickyPromptExpanded = !state.stickyPromptExpanded;
  renderStickyPromptContent();
}

export function updateStickyPromptVisibility() {
  const container = document.getElementById("messages-container");
  const stickyBar = document.getElementById("sticky-user-prompt-bar");
  const headerEl = document.querySelector("header");
  if (!container || !stickyBar) return;

  const userEls = Array.from(container.querySelectorAll("[data-user-msg-id]"));
  if (userEls.length === 0) {
    stickyBar.classList.add("hidden");
    return;
  }

  if (headerEl) {
    stickyBar.style.top = `${headerEl.offsetHeight}px`;
  }

  const containerRect = container.getBoundingClientRect();
  const topThreshold = containerRect.top + 16;

  let activeIdx = -1;
  for (let i = 0; i < userEls.length; i++) {
    const el = userEls[i];
    const rect = el.getBoundingClientRect();
    if (rect.bottom < topThreshold) {
      activeIdx = i;
    } else {
      break;
    }
  }

  // 需求 3：文字定位在最新位置也要显示最后一条指令
  const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
  if (distFromBottom <= 120 && userEls.length > 0) {
    const lastEl = userEls[userEls.length - 1];
    const lastRect = lastEl.getBoundingClientRect();
    // 只要最后一条指令的气泡没有停留在视口顶端（即已被滚上去或视口在最底部），始终悬浮显示最后一条指令
    if (lastRect.top < topThreshold || activeIdx === -1) {
      activeIdx = userEls.length - 1;
    }
  }

  if (activeIdx >= 0) {
    state.stickyPromptActiveIndex = activeIdx;
    renderStickyPromptContent(userEls);
    stickyBar.classList.remove("hidden");
  } else {
    stickyBar.classList.add("hidden");
  }
}

export function scrollToCurrentPrompt(e) {
  if (e) e.stopPropagation();
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
