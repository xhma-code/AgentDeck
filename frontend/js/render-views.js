import { apiFetch } from "./api.js";
/**
 * AgentDeck 控制面板、Git 审查模态框、二维码与状态胶囊渲染器
 */

import {
  MODES_BY_AGENT,
  DEFAULT_MODELS_BY_AGENT,
} from "./config.js";
import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  icons,
  formatTokenCount,
} from "./utils.js";
import {
  isSessionWorking,
  getAgentDisplayName,
} from "./data.js";

let qrExpireTimer = null;
let liveStreamRaf = null;

export {
  renderModelOptionsForAgent,
  renderModeOptionsForAgent,
  renderModelPicker,
} from "./render-model-picker.js";
import {
  renderModelOptionsForAgent,
  renderModeOptionsForAgent,
  renderModelPicker,
} from "./render-model-picker.js";

export function updatePromptInputPlaceholder(sess) {
  const inputEl = document.getElementById("prompt-input");
  const overlayEl = document.getElementById("prompt-input-placeholder");
  if (!inputEl) return;
  if (!sess) {
    if (overlayEl) {
      overlayEl.innerHTML = `<span>发消息...</span>`;
    }
    inputEl.placeholder = " ";
    return;
  }
  const agentName = getAgentDisplayName(sess.agent_type);
  if (overlayEl) {
    overlayEl.innerHTML = `
      <span>发消息给&nbsp;</span><span class="text-[10px] sm:text-xs text-zinc-400 font-mono">${escapeHtml(agentName)}</span><span>...</span>
    `;
  }
  inputEl.placeholder = `发消息给 ${agentName}...`;
}

export function renderCurrentSessionHeader() {
  const titleEl = document.getElementById("current-session-title");
  const subtitleEl = document.getElementById("current-session-subtitle");
  const modeSelect = document.getElementById("execution-mode-select");
  const headerLogo = document.getElementById("header-logo-icon");

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess) {
    if (titleEl) {
      titleEl.innerHTML = `<span class="text-xs sm:text-sm font-medium text-zinc-400 truncate">未选择对话</span>`;
    }
    if (subtitleEl) subtitleEl.textContent = "";
    if (headerLogo) headerLogo.classList.remove("has-session-hidden-mobile");
    updatePromptInputPlaceholder(null);
    renderModelPicker();
    updatePromptActionBtnState();
    return;
  }

  // 手机端已选择具体会话后，隐藏顶栏会话名称前的系统钻石图标以释放横向排版空间
  if (headerLogo) {
    headerLogo.classList.add("has-session-hidden-mobile");
  }

  const meta = AGENT_META[sess.agent_type] || AGENT_META.claude_code;

  // 获取项目名称（非工程会话回退至会话 ID 短码）
  const rawProjectName =
    sess.is_project && sess.project_name
      ? sess.project_name
      : (sess.project_name || (sess.project_path ? getProjectInfo(sess.project_path).name : ""));
  const projectName = String(rawProjectName || "").trim();

  // 桌面网页端项目展示（提升对比度与悬停反馈）
  const desktopProjectHtml = projectName
    ? `<span class="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-zinc-100 font-medium font-mono shrink-0 max-w-[200px] truncate transition-colors" title="${escapeHtml(projectName)}">
        <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path></svg>
        <span class="truncate">${escapeHtml(projectName)}</span>
      </span>`
    : `<span class="inline-flex items-center text-xs text-zinc-400 font-mono shrink-0" title="${escapeHtml(sess.raw_session_id || "")}">#${escapeHtml((sess.raw_session_id || "").slice(0, 8))}</span>`;

  // 手机移动端项目展示（会话名称 · 项目名称）
  const mobileProjectHtml = projectName
    ? `<span class="text-zinc-500 text-[11px] shrink-0 select-none">·</span>
       <span class="text-[11px] text-zinc-400 font-mono shrink-0 max-w-[100px] truncate" title="${escapeHtml(projectName)}">${escapeHtml(projectName)}</span>`
    : (sess.raw_session_id
        ? `<span class="text-zinc-500 text-[11px] shrink-0 select-none">·</span>
           <span class="text-[11px] text-zinc-500 font-mono shrink-0">#${escapeHtml((sess.raw_session_id || "").slice(0, 8))}</span>`
        : "");

  // 网页端：项目名称 · 会话名称
  // 手机端：只显示 会话名称 · 项目名称
  if (titleEl) {
    titleEl.innerHTML = `
      <!-- 网页端（sm及以上显示）：项目名称 · 会话名称 -->
      <div class="hidden sm:flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
        ${desktopProjectHtml}
        <span class="text-zinc-500 text-xs shrink-0 select-none">·</span>
        <span class="truncate font-medium text-sm text-zinc-100 min-w-0" title="${escapeHtml(cleanFriendlyTitle(sess.title))}">${escapeHtml(cleanFriendlyTitle(sess.title))}</span>
      </div>

      <!-- 手机端（sm以下显示）：会话名称 · 项目名称 -->
      <div class="flex sm:hidden items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
        <span class="truncate font-medium text-xs text-zinc-100 min-w-0 shrink" title="${escapeHtml(cleanFriendlyTitle(sess.title))}">${escapeHtml(cleanFriendlyTitle(sess.title))}</span>
        ${mobileProjectHtml}
      </div>
    `;
  }
  if (subtitleEl) {
    subtitleEl.textContent = "";
  }
  updatePromptInputPlaceholder(sess);
  if (modeSelect) {
    const activeMode = renderModeOptionsForAgent(modeSelect, sess.agent_type, sess.execution_mode || "ask", true);
    // 若原存储的模式不受该 Agent 支持且发生了自动回退，静默同步更新后端元数据持久化
    if (sess.execution_mode && sess.execution_mode !== activeMode) {
      sess.execution_mode = activeMode;
      apiFetch(`/api/v1/sessions/${encodeURIComponent(sess.unified_id)}/meta`, {
        method: "PATCH",
        body: JSON.stringify({ execution_mode: activeMode }),
      }).catch((err) => console.warn("静默同步纠偏模式失败:", err));
    }
  }
  renderModelPicker();
  updatePromptActionBtnState();
  renderOtherRunningPill();
}

export function updatePromptActionBtnState() {
  const btn = document.getElementById("prompt-action-btn");
  const btnText = document.getElementById("prompt-action-btn-text");
  const inputEl = document.getElementById("prompt-input");
  const overlayEl = document.getElementById("prompt-input-placeholder");
  if (!btn) return;

  const isWorking = isSessionWorking(state.selectedSessionId);
  const hasText = Boolean(inputEl && inputEl.value.trim().length > 0);

  if (overlayEl) {
    if (hasText) {
      overlayEl.classList.add("!hidden");
    } else {
      overlayEl.classList.remove("!hidden");
    }
  }

  // 运行中的会话：如果无输入内容显示「中断」，如果有输入内容显示「发送」（跟客户端交互完全一致）
  if (isWorking && !hasText) {
    btn.dataset.action = "abort";
    btn.className =
      "shrink-0 h-10 px-3.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs sm:text-sm shadow active:scale-95 transition flex items-center justify-center m-0";
    if (btnText) {
      btnText.textContent = "中断";
    }
    btn.setAttribute("title", "中断当前执行 (SIGINT)");
  } else {
    btn.dataset.action = "send";
    btn.className =
      "shrink-0 h-10 px-3.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs sm:text-sm shadow active:scale-95 transition flex items-center justify-center m-0";
    if (btnText) {
      btnText.textContent = "发送";
    }
    btn.setAttribute("title", "发送指令");
  }
}

export function handlePromptAction() {
  const btn = document.getElementById("prompt-action-btn");
  const action = btn?.dataset?.action || "send";
  if (action === "abort") {
    handleAbortCurrentSession();
  } else {
    handleSendPrompt(null);
  }
}

export function renderLiveStreamOverlay() {
  if (liveStreamRaf) return;
  liveStreamRaf = requestAnimationFrame(() => {
    liveStreamRaf = null;
    _doRenderLiveStreamOverlay();
  });
}

export function _doRenderLiveStreamOverlay() {
  const slot = document.getElementById("live-stream-overlay-slot");
  updatePromptActionBtnState();
  if (!slot || !state.selectedSessionId) return;

  const isWorking = isSessionWorking(state.selectedSessionId);
  const liveText = state.streamingBuffer[state.selectedSessionId];

  if (!isWorking && !liveText) {
    if (slot.innerHTML !== "") slot.innerHTML = "";
    return;
  }

  // Agent 工作中，在对话区域显示 Working... 与实时耗时及生成流（RAF 帧对齐渲染）
  const startMs = state.pendingTurnThinking ? state.pendingTurnThinking[state.selectedSessionId] : null;
  let elapsedStr = "";
  if (startMs) {
    const elapsedSec = Math.max(1, Math.round((Date.now() - startMs) / 1000));
    elapsedStr = elapsedSec < 60 ? `${elapsedSec}s` : `${Math.floor(elapsedSec / 60)}m ${elapsedSec % 60}s`;
  }

  const streamHtml = liveText
    ? `<div class="mt-2 text-xs text-zinc-200 border-t border-zinc-800/60 pt-2">${renderMarkdownHtml(
        liveText.slice(-3000),
        "live_stream"
      )}</div>`
    : "";

  slot.innerHTML = `
    <div class="mb-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-300 shadow-sm transition-all">
      <div class="flex items-center gap-2 font-mono text-[11px] text-sky-400">
        <span class="relative flex h-2 w-2">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-sky-400"></span>
        </span>
        <span class="tracking-wider font-semibold">Working...</span>
        ${elapsedStr ? `<span class="text-zinc-600 font-normal">·</span><span class="text-sky-300/80 font-normal font-mono text-[11px]">${elapsedStr}</span>` : ""}
      </div>
      ${streamHtml}
    </div>
  `;

  if (!state.autoScrollLocked) {
    scrollToBottom();
  }
  updateCollapseStepsPillVisibility();
}

export { renderApprovalBanners } from "./render-approval-views.js";

export {
  stopQrExpireCountdown,
  startQrExpireCountdown,
  renderQrModalContent,
  renderTokenUsage,
} from "./render-system-views.js";

export {
  highlightSlashItem,
  renderSlashMenu,
  renderGitStatusBody,
} from "./render-modal-views.js";
import { handleSendPrompt, handleAbortCurrentSession } from "./actions.js";
import { AGENT_META } from "./config.js";
import { cleanFriendlyTitle, getProjectInfo, getModelContextLimit } from "./data.js";
import { renderMarkdownHtml } from "./markdown.js";
import { updateCollapseStepsPillVisibility, scrollToBottom } from "./render-messages.js";
import { renderOtherRunningPill } from "./render-session-card.js";
