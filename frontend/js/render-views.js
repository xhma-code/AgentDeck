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
} from "./utils.js";
import {
  isSessionWorking,
  getAgentDisplayName,
} from "./data.js";

let qrExpireTimer = null;
let liveStreamRaf = null;

export function renderModelOptionsForAgent(selectEl, agentType, selectedModelId = "") {
  if (!selectEl) return;
  const list = state.modelsByAgent[agentType];
  const models =
    Array.isArray(list) && list.length > 0
      ? list
      : DEFAULT_MODELS_BY_AGENT[agentType] || DEFAULT_MODELS_BY_AGENT.agy_cli;

  const defaultModelItem = models.find((m) => m.is_default) || models[0];
  const activeId = selectedModelId || (defaultModelItem ? defaultModelItem.id : "");
  if (selectEl.tagName === "SELECT") {
    const usage = state.currentTokenUsage;
    const currentTokens = usage ? usage.context_tokens || 0 : 0;
    selectEl.innerHTML = models
      .map((m) => {
        const tagSuffix = m.tag ? ` · ${m.tag}` : "";
        const limit = getModelContextLimit(m.id);
        let quotaPart = "";
        if (currentTokens > 0) {
          const pct = Math.round((currentTokens / limit) * 1000) / 10;
          quotaPart = ` [水位 ${pct}% · ${formatTokenCount(currentTokens)}/${formatTokenCount(limit)}]`;
        } else {
          quotaPart = ` [容量 ${formatTokenCount(limit)}]`;
        }
        const sel = m.id === activeId ? "selected" : "";
        return `<option value="${escapeHtml(m.id)}" ${sel}>${escapeHtml(
          m.label + tagSuffix + quotaPart
        )}</option>`;
      })
      .join("");
  } else {
    selectEl.value = activeId;
  }
  renderModelPicker();
}

export function renderModeOptionsForAgent(selectEl, agentType, selectedMode = "ask", isShort = false) {
  if (!selectEl) return "ask";
  const normalizedType = agentType === "claude" ? "claude_code" : (agentType || "agy_cli");
  const list = MODES_BY_AGENT[normalizedType] || MODES_BY_AGENT.agy_cli;

  // 校验当前选中的模式是否受该 Agent 支持；若不支持，安全回退至 ask（审批）
  const isSupported = list.some((m) => m.value === selectedMode);
  const activeMode = isSupported ? selectedMode : (list[0]?.value || "ask");

  selectEl.innerHTML = list
    .map((m) => {
      const text = isShort ? m.label : m.fullLabel;
      const sel = m.value === activeMode ? "selected" : "";
      return `<option value="${escapeHtml(m.value)}" ${sel}>${escapeHtml(text)}</option>`;
    })
    .join("");

  selectEl.value = activeMode;
  return activeMode;
}

export function renderModelPicker() {
  const btn = document.getElementById("model-picker-btn");
  const labelEl = document.getElementById("model-picker-label");
  const popover = document.getElementById("model-picker-popover");
  const hiddenInput = document.getElementById("model-select");
  if (!btn || !labelEl) return;

  const sess =
    state.sessions.find((s) => s.unified_id === state.selectedSessionId) ||
    state.allSessions.find((s) => s.unified_id === state.selectedSessionId);
  const agentType = sess?.agent_type || "agy_cli";
  const list = state.modelsByAgent[agentType];
  const models =
    Array.isArray(list) && list.length > 0
      ? list
      : DEFAULT_MODELS_BY_AGENT[agentType] || DEFAULT_MODELS_BY_AGENT.agy_cli;

  const defaultModelItem = models.find((m) => m.is_default) || models[0];
  const activeId = sess?.model_name || (defaultModelItem ? defaultModelItem.id : "");
  if (hiddenInput) hiddenInput.value = activeId;

  const activeModel = models.find((m) => m.id === activeId) || defaultModelItem;
  if (activeModel) {
    labelEl.textContent = activeModel.label;
  }

  if (!popover) return;

  popover.innerHTML = `
    <div class="px-3 py-2 text-[10px] text-zinc-400 font-semibold uppercase tracking-wider border-b border-zinc-800 flex items-center justify-between sticky top-0 z-10 select-none" style="background-color: #09090b !important;">
      <span class="flex items-center gap-1.5 font-medium text-zinc-200">
        <span class="text-sky-400">🧠</span>
        <span>可用模型 (${models.length})</span>
      </span>
      <span class="text-[10px] text-zinc-400 font-mono">余量</span>
    </div>
    <div class="p-1 space-y-0.5" style="background-color: #09090b !important;">
      ${models
        .map((m) => {
          const isSelected = m.id === activeId;
          const quotaVal = typeof m.quota === "number" ? m.quota : 1.0;
          const pct = Math.round(quotaVal * 100);

          let barColorCls = "bg-emerald-400";
          let textColorCls = "text-emerald-400";
          if (pct <= 20) {
            barColorCls = "bg-rose-500";
            textColorCls = "text-rose-400";
          } else if (pct <= 50) {
            barColorCls = "bg-amber-400";
            textColorCls = "text-amber-400";
          }

          return `
            <div onclick="window.appActions.selectModel('${escapeJsSingleQuote(m.id)}', event)"
              class="w-full flex items-center justify-between px-2.5 py-2 text-xs rounded-lg cursor-pointer transition select-none ${
                isSelected
                  ? "bg-zinc-800 border border-zinc-700/80 text-white"
                  : "hover:bg-zinc-900 border border-transparent text-zinc-300"
              }">
              <!-- 左侧：选中圆点 + 模型名称 + 标签 + 推荐星 -->
              <div class="flex items-center gap-2 min-w-0 flex-1 pr-2">
                <div class="w-1.5 h-1.5 rounded-full shrink-0 ${
                  isSelected ? "bg-sky-400 ring-2 ring-sky-400/40" : "bg-zinc-700"
                }"></div>
                <span class="truncate font-medium text-zinc-100 ${isSelected ? "text-white font-semibold" : ""}">${escapeHtml(m.label)}</span>
                ${
                  m.tag
                    ? `<span class="text-[9px] px-1 py-0.5 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30 shrink-0">${escapeHtml(
                        m.tag
                      )}</span>`
                    : ""
                }
                ${
                  m.is_default
                    ? `<span class="text-[9px] px-1 py-0.5 rounded font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0" title="电脑端当前默认模型">默认</span>`
                    : ""
                }
              </div>

              <!-- 右侧：模型右侧直接显示进度条与百分比数值 -->
              <div class="flex items-center gap-1.5 shrink-0">
                <div class="w-10 sm:w-12 h-1.5 rounded-full bg-zinc-800 overflow-hidden shrink-0">
                  <div class="h-full rounded-full transition-all duration-300 ${barColorCls}" style="width: ${pct}%"></div>
                </div>
                <span class="text-[11px] font-mono font-medium ${textColorCls} w-8 text-right shrink-0">${pct}%</span>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

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

  // Agent 工作中，在对话区域显示 Working... 与实时生成流（RAF 帧对齐渲染）
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
