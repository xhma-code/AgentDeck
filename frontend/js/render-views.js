/**
 * AgentDeck 控制面板、Git 审查模态框、二维码与状态胶囊渲染器
 */

import {
  GIT_STATUS_BADGES,
  MODES_BY_AGENT,
  DEFAULT_MODELS_BY_AGENT,
} from "./config.js";
import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  formatTokenCount,
  formatElapsedSec,
  gitPathToSlotId,
  icons,
} from "./utils.js";
import {
  renderUnifiedDiffHtml,
} from "./markdown.js";
import {
  isSessionWorking,
  getAgentDisplayName,
} from "./data.js";

let qrExpireTimer = null;

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


export function renderApprovalBanners() {
  const topBanner = document.getElementById("global-approval-banner");
  const bottomBar = document.getElementById("bottom-approval-bar");
  if (!topBanner || !bottomBar) return;

  const list = state.pendingApprovals || [];
  if (list.length === 0) {
    topBanner.classList.add("hidden");
    bottomBar.classList.add("hidden");
    if (window.updateBottomControlHeight) window.updateBottomControlHeight();
    return;
  }

  topBanner.classList.remove("hidden");
  topBanner.innerHTML = `
    <div class="flex items-center justify-between gap-2 px-3 py-1 bg-amber-500/20 border-b border-amber-500/40 text-[11px] text-amber-200">
      <div class="flex items-center gap-1.5 truncate">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping shrink-0"></span>
        <span class="font-semibold">${list.length} 项待审批</span>
        <span class="text-amber-300/80 truncate">(${escapeHtml(list[0].tool_name)})</span>
      </div>
      <button onclick="window.appActions.selectSession('${escapeHtml(list[0].unified_id)}')"
        class="px-2 py-0.5 rounded bg-amber-400 text-zinc-950 font-semibold shrink-0">
        处理
      </button>
    </div>
  `;

  const activeApproval =
    list.find((p) => p.unified_id === state.selectedSessionId) || list[0];
  const isHighRisk = activeApproval.risk_level === "high";

  bottomBar.classList.remove("hidden");
  bottomBar.innerHTML = `
    <div class="p-2.5 bg-zinc-900/98 border-t-2 ${
      isHighRisk ? "border-rose-500" : "border-amber-500"
    } shadow-2xl">
      <div class="max-w-5xl mx-auto">
        <div class="flex items-center justify-between gap-2 mb-1">
        <div class="flex items-center gap-1.5">
          <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${
            isHighRisk
              ? "bg-rose-500/25 text-rose-300 border border-rose-500/40"
              : "bg-amber-500/25 text-amber-300 border border-amber-500/40"
          }">
            ${isHighRisk ? "⚠️ 高危拦截" : "🛡️ 权限审批"}
          </span>
          <span class="font-mono text-xs font-semibold text-zinc-200">${escapeHtml(
            activeApproval.tool_name
          )}</span>
        </div>
      </div>

      <div class="font-mono text-[11px] text-zinc-200 bg-black/60 border border-zinc-800 rounded-lg px-2 py-1 mb-1.5 break-all max-h-20 overflow-y-auto">
        ${escapeHtml(activeApproval.args_summary)}
      </div>

      ${activeApproval.diff_text ? renderUnifiedDiffHtml(activeApproval.diff_text, activeApproval.args_summary) : ""}

      ${
        state.denyFeedbackOpen
          ? `
            <div class="mt-2 p-2 rounded-xl bg-zinc-950 border border-rose-500/40 space-y-2">
              <div class="text-[11px] text-rose-300 font-semibold flex items-center justify-between">
                <span>✕ 拒绝原因与指导意见 (将传给 Agent 调整方案)</span>
                <button type="button" onclick="window.appActions.toggleDenyFeedback(false)" class="text-zinc-400 hover:text-white px-1">收起</button>
              </div>
              <textarea id="deny-feedback-input" rows="2"
                placeholder="告诉 Agent 为什么拒绝或应该改成什么方案（例如：不要删这个函数，改成可选参数）..."
                class="w-full px-2.5 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-rose-400 resize-none"></textarea>
              <div class="flex items-center justify-end gap-2">
                <button type="button"
                  onclick="window.appActions.submitApproval('${escapeHtml(
                    activeApproval.unified_id
                  )}', 'denied')"
                  class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs">
                  直接拒绝
                </button>
                <button type="button"
                  onclick="window.appActions.submitDenyWithFeedback('${escapeHtml(
                    activeApproval.unified_id
                  )}')"
                  class="px-3 py-1 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs shadow active:scale-95 transition">
                  ✓ 拒绝并发送指导
                </button>
              </div>
            </div>
          `
          : `
            <div class="grid grid-cols-12 gap-2 mt-2">
              <button onclick="window.appActions.toggleDenyFeedback(true)"
                class="col-span-4 min-h-[42px] rounded-xl bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 font-semibold text-xs active:scale-95 transition flex items-center justify-center gap-1">
                <span>✕ 拒绝</span>
                <span class="text-[10px] text-rose-400/80">✎</span>
              </button>

              ${
                isHighRisk
                  ? `
                    <button
                      onpointerdown="window.appActions.startHold('${escapeHtml(
                        activeApproval.unified_id
                      )}', this)"
                      onpointerup="window.appActions.cancelHold(this)"
                      onpointerleave="window.appActions.cancelHold(this)"
                      onpointercancel="window.appActions.cancelHold(this)"
                      class="hold-approve-btn col-span-8 min-h-[42px] rounded-xl bg-amber-500 text-zinc-950 font-bold text-xs shadow-lg">
                      <div class="hold-progress-fill"></div>
                      <span class="relative z-10">⚡ 长按 0.8s 允许</span>
                    </button>
                  `
                  : `
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approve_session_edits')"
                      class="col-span-3 min-h-[42px] rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 font-medium text-[11px] active:scale-95 transition">
                      免审
                    </button>
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approved')"
                      class="col-span-5 min-h-[42px] rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-xs shadow-lg active:scale-95 transition">
                      ✓ 允许
                    </button>
                  `
              }
            </div>
          `
      }
      </div>
    </div>
  `;
  if (window.updateBottomControlHeight) window.updateBottomControlHeight();
}


export function stopQrExpireCountdown() {
  if (qrExpireTimer) {
    clearTimeout(qrExpireTimer);
    qrExpireTimer = null;
  }
}

export function startQrExpireCountdown() {
  if (qrExpireTimer) {
    clearTimeout(qrExpireTimer);
    qrExpireTimer = null;
  }
  const expAtSec = Number(state.systemStatus?.pair_code_expires_at || 0);
  const nowSec = Date.now() / 1000;
  if (expAtSec > 0 && expAtSec <= nowSec) {
    state.qrExpired = true;
    renderQrModalContent();
    return;
  }
  state.qrExpired = false;
  state.qrGeneratedAt = Date.now();
  const remainMs = expAtSec > 0 ? Math.max(1000, Math.floor((expAtSec - nowSec) * 1000)) : 600 * 1000;
  qrExpireTimer = setTimeout(() => {
    state.qrExpired = true;
    renderQrModalContent();
  }, remainMs);
}

export function renderQrModalContent() {
  const box = document.getElementById("qr-modal-body");
  if (!box || !state.systemStatus) return;

  // 1. 若刚刚接收到设备配对成功的实时广播，展示精致的双向握手成功卡片
  if (state.pairedSuccessNotice) {
    const dev = state.pairedSuccessNotice.device_name || "移动设备";
    const ip = state.pairedSuccessNotice.client_ip || "";
    box.innerHTML = `
      <div class="py-6 px-4 flex flex-col items-center justify-center text-center space-y-3.5 animate-fadeIn">
        <div class="w-14 h-14 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shadow-[0_0_24px_rgba(16,185,129,0.25)]">
          <svg class="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="space-y-1">
          <div class="text-base font-bold text-zinc-100">手机已成功连接！</div>
          <div class="text-xs text-zinc-400 font-mono flex items-center justify-center gap-1.5">
            <span>${escapeHtml(dev)}</span>
            ${ip ? `<span class="text-zinc-600">·</span><span>${escapeHtml(ip)}</span>` : ""}
          </div>
        </div>
        <div class="pt-2 text-[11px] text-zinc-500 flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
          <span>窗口即将自动关闭...</span>
        </div>
      </div>
    `;
    return;
  }

  const { pair_url, lan_pair_url, zt_pair_url, channels } = state.systemStatus;
  const orderWeight = { wifi: 1, tailscale: 2, zerotier: 3 };
  const channelList = (channels || []).slice().sort((a, b) => (orderWeight[a.type] || 9) - (orderWeight[b.type] || 9));

  if (!state.selectedQrNet) {
    // 默认绝对优先展示 Tailscale（支持随时随地全网随身漫游），其次 ZeroTier，严禁在有 VPN 时优先展示 Wi-Fi 直连
    const hasTs = channelList.some((c) => c.type === "tailscale");
    const hasZt = channelList.some((c) => c.type === "zerotier");
    if (hasTs) {
      state.selectedQrNet = "tailscale";
    } else if (hasZt) {
      state.selectedQrNet = "zerotier";
    } else {
      state.selectedQrNet = channelList[0]?.type || "wifi";
    }
  }

  const curChannel = channelList.find((c) => c.type === state.selectedQrNet) || channelList[0] || {};
  const activeUrl = curChannel.pair_url || curChannel.url || pair_url || "";
  const tokenParam = encodeURIComponent(state.token || "");
  const qrImgUrl = `/api/v1/auth/qr.svg?net=${encodeURIComponent(state.selectedQrNet)}&token=${tokenParam}&_t=${state.qrGeneratedAt || 0}`;

  const channelTips = {
    tailscale: "全网随身漫游 · 手机开启 Tailscale 随时秒连",
    zerotier: "虚拟隧道直连 · 手机开启 ZeroTier 隧道直连",
    wifi: "局域网免密直连 · 仅限当前同一 Wi-Fi 连接",
  };

  const channelIcons = {
    tailscale: `<svg class="w-3.5 h-3.5 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>`,
    zerotier: `<svg class="w-3.5 h-3.5 text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>`,
    wifi: `<svg class="w-3.5 h-3.5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"></path><path d="M1.42 9a16 16 0 0 1 21.16 0"></path><path d="M8.53 16.11a6 6 0 0 1 6.95 0"></path><line x1="12" y1="20" x2="12.01" y2="20"></line></svg>`,
  };

  const gridColsClass =
    channelList.length === 1
      ? "grid-cols-1"
      : channelList.length === 2
      ? "grid-cols-2"
      : "grid-cols-3";

  box.innerHTML = `
    <div class="space-y-3">
      <!-- 极简通道切换胶囊 (Tailscale 默认居中) -->
      <div class="grid ${gridColsClass} gap-1.5 w-full">
        ${channelList
          .map((c) => {
            const isSelected = c.type === state.selectedQrNet;
            return `
              <button type="button" onclick="window.appActions.switchQrChannel('${c.type}')"
                class="py-1.5 px-2 rounded-xl text-[11px] font-medium transition cursor-pointer select-none flex items-center justify-center gap-1.5 active:scale-95 ${
                  isSelected
                    ? "bg-sky-500/20 text-sky-300 border border-sky-500/50 shadow-[0_0_12px_rgba(56,189,248,0.15)] font-semibold"
                    : "bg-zinc-950/70 hover:bg-zinc-800/80 text-zinc-400 hover:text-zinc-200 border border-zinc-800"
                }">
                ${channelIcons[c.type] || ""}
                <span>${escapeHtml(c.label)}</span>
              </button>
            `;
          })
          .join("")}
      </div>

      <!-- 二维码专属卡片 (带 10 分钟过期遮罩防护) -->
      <div class="relative flex flex-col items-center bg-zinc-950/90 border border-zinc-800/80 rounded-2xl p-4 shadow-xl">
        <div class="relative bg-white p-3 rounded-2xl shadow-md">
          <img src="${qrImgUrl}" alt="AgentDeck Pair QR" class="w-44 h-44 block select-none" loading="eager" />
          
          ${
            state.qrExpired
              ? `
                <div class="absolute inset-0 bg-zinc-950/90 backdrop-blur-xs rounded-2xl flex flex-col items-center justify-center p-3 text-center space-y-2.5 animate-fadeIn">
                  <div class="w-8 h-8 rounded-full bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                  </div>
                  <div class="text-[11px] text-zinc-300 font-medium">二维码已过期 (10分钟时限)</div>
                  <button onclick="window.appActions.refreshPairCodeAndQr()"
                    class="px-3 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs shadow-md transition active:scale-95 cursor-pointer flex items-center gap-1.5">
                    <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                    <span>点击立即刷新</span>
                  </button>
                </div>
              `
              : ""
          }
        </div>

        <!-- 单行不换行主直连地址 -->
        <div class="w-full mt-3 px-2.5 py-1 rounded-xl bg-zinc-900 border border-zinc-800 flex items-center justify-between gap-1.5">
          <div class="text-[11px] font-mono text-sky-400 whitespace-nowrap overflow-hidden select-all flex-1 py-0.5 tracking-tight" title="${escapeHtml(activeUrl)}">
            ${escapeHtml(activeUrl)}
          </div>
          <button onclick="window.appActions.copyQrUrl('${escapeJsSingleQuote(activeUrl)}')"
            class="text-[11px] text-zinc-300 hover:text-white px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 shrink-0 transition active:scale-95 cursor-pointer font-medium"
            title="复制直连链接">复制</button>
        </div>

        <!-- 精炼通道场景提示 -->
        <div class="w-full mt-2.5 pt-2.5 border-t border-zinc-800/80 text-[11px] text-center text-zinc-400 flex items-center justify-center gap-1.5">
          <span class="w-1.5 h-1.5 rounded-full ${
            curChannel.type === "wifi" ? "bg-emerald-400" : curChannel.type === "tailscale" ? "bg-sky-400" : "bg-amber-400"
          }"></span>
          <span>${channelTips[state.selectedQrNet] || "扫码或复制链接在手机中打开"}</span>
        </div>
      </div>
    </div>
  `;
}


export function renderTokenUsage() {
  const pill = document.getElementById("token-usage-pill");
  if (!pill) return;
  const usage = state.currentTokenUsage;
  if (!usage || (!usage.context_tokens && !usage.total_tokens)) {
    pill.classList.add("hidden");
    pill.classList.remove("flex");
    return;
  }

  pill.classList.remove("hidden");
  pill.classList.add("flex");

  const ctx = usage.context_tokens || 0;
  const limit = usage.context_limit || 200000;
  const pct = typeof usage.context_percent === "number" ? usage.context_percent : Math.round((ctx / limit) * 1000) / 10;

  const txtEl = document.getElementById("token-usage-text");
  const pctEl = document.getElementById("token-usage-pct");
  const dotEl = document.getElementById("token-usage-dot");

  if (txtEl) txtEl.textContent = `${formatTokenCount(ctx)} / ${formatTokenCount(limit)}`;
  if (pctEl) {
    pctEl.textContent = `${pct}%`;
    if (pct < 60) {
      pctEl.className = "text-[9px] px-1 py-0.5 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-sky-400";
    } else if (pct < 85) {
      pctEl.className = "text-[9px] px-1 py-0.5 rounded font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-amber-400";
    } else {
      pctEl.className = "text-[9px] px-1 py-0.5 rounded font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/40 animate-pulse";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-rose-500 animate-ping";
    }
  }
}


export function highlightSlashItem(idx) {
  state.slashHighlightIndex = idx;
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const items = popover.querySelectorAll("[data-slash-idx]");
  items.forEach((el) => {
    const i = parseInt(el.getAttribute("data-slash-idx"), 10);
    if (i === idx) {
      el.classList.add("bg-zinc-800", "border-zinc-700/80");
      el.classList.remove("hover:bg-zinc-900", "border-transparent");
      el.scrollIntoView({ block: "nearest" });
    } else {
      el.classList.remove("bg-zinc-800", "border-zinc-700/80");
      el.classList.add("hover:bg-zinc-900", "border-transparent");
    }
  });
}
window.highlightSlashItem = highlightSlashItem;

export function renderSlashMenu(filterQuery = "") {
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const list = state.workflows || [];
  const q = filterQuery.trim().replace(/^\//, "").toLowerCase();

  const filtered = list.filter((item) => {
    if (!q) return true;
    const cmdClean = item.command.replace(/^\//, "").toLowerCase();
    const lblClean = (item.label || "").toLowerCase();
    return cmdClean.includes(q) || lblClean.includes(q);
  });

  state.filteredSlashCommands = filtered;
  if (state.slashHighlightIndex >= filtered.length) {
    state.slashHighlightIndex = 0;
  }

  if (!state.slashMenuOpen && !filterQuery.startsWith("/")) {
    popover.classList.add("hidden");
    return;
  }

  if (filtered.length === 0) {
    popover.innerHTML = `
      <div class="px-3 py-2.5 text-xs text-zinc-500 flex items-center justify-between">
        <span>无匹配技能或指令</span>
        <span class="text-[10px] text-zinc-600 font-mono">共 ${list.length} 个全部可用</span>
      </div>`;
    popover.classList.remove("hidden");
    return;
  }

  popover.innerHTML = `
    <div class="px-2.5 py-1 text-[11px] text-zinc-400 font-medium border-b border-zinc-800/80 flex items-center justify-between sticky top-0 bg-zinc-950/95 backdrop-blur z-10 select-none">
      <span class="flex items-center gap-1.5 font-medium text-zinc-300">
        <span class="text-sky-400">⚡</span>
        <span>可用技能与指令 (Skills & Commands)</span>
      </span>
      <span class="text-[10px] text-zinc-500 font-mono">${filtered.length} / ${list.length}</span>
    </div>
    <div class="p-1 space-y-0.5">
      ${filtered
        .map((item, idx) => {
          const isQuick = item.category === "quick";
          const isSkill = item.category === "skill" || item.category === "builtin" || item.category === "project";
          
          let badgeCls = "bg-sky-500/15 text-sky-300 border-sky-500/30";
          let badgeText = "指令";
          if (isQuick) {
            badgeCls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            badgeText = "快捷";
          } else if (item.category === "builtin") {
            badgeCls = "bg-cyan-500/15 text-cyan-300 border-cyan-500/30";
            badgeText = "内置";
          } else if (item.category === "project") {
            badgeCls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            badgeText = "项目";
          } else if (isSkill) {
            badgeCls = "bg-purple-500/15 text-purple-300 border-purple-500/30";
            badgeText = "技能";
          }

          const isHigh = idx === state.slashHighlightIndex;
          return `
            <div id="slash-item-${idx}"
              data-slash-idx="${idx}"
              onclick="window.appActions.selectSlashCommand('${escapeJsSingleQuote(item.command)}')"
              onmouseenter="window.highlightSlashItem(${idx})"
              class="cursor-pointer px-2 py-1.5 rounded-lg flex items-center justify-between gap-2 text-xs transition border ${
                isHigh ? "bg-zinc-800 border-zinc-700/80" : "hover:bg-zinc-900 border-transparent"
              }">
              <div class="flex items-center gap-2 min-w-0">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold shrink-0 border ${badgeCls}">${escapeHtml(
            item.command
          )}</span>
                <span class="text-zinc-200 truncate text-[11px] sm:text-xs">${escapeHtml(item.label)}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <span class="text-[9px] text-zinc-500 font-medium px-1 py-0.5 rounded bg-zinc-900 border border-zinc-800">${badgeText}</span>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
  popover.classList.remove("hidden");
}


window.highlightSlashItem = highlightSlashItem;

export async function updateGitStatusBadge() {
  const btn = document.getElementById("git-status-btn");
  const badge = document.getElementById("git-changed-badge");
  if (!btn || !badge) return;

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess || !sess.project_path) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
    return;
  }

  try {
    const data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(sess.project_path)}`);
    if (data.is_git_repo) {
      btn.classList.remove("hidden");
      btn.classList.add("flex");
      const count = data.changed_count || 0;
      badge.textContent = String(count);
      badge.className = `px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold ${
        count > 0
          ? "bg-amber-500/25 text-amber-300 border border-amber-500/40 animate-pulse"
          : "bg-zinc-800 text-zinc-400 border border-zinc-700"
      }`;
    } else {
      btn.classList.add("hidden");
      btn.classList.remove("flex");
    }
  } catch (_) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
  }
}

export function renderGitStatusBody(data) {
  const bodyEl = document.getElementById("doc-preview-body");
  if (!bodyEl) return;

  if (!data.is_git_repo) {
    bodyEl.innerHTML = `<div class="p-6 text-center text-zinc-500 text-xs">当前工作区非 Git 仓库</div>`;
    return;
  }

  const files = data.files || [];

  const listHtml =
    files.length === 0
      ? `<div class="p-6 text-center text-emerald-400 text-xs">✓ 工作区很干净，无未提交修改</div>`
      : files
          .map((f) => {
            const isExp = !!state.gitExpandedDiffs[f.path];
            const badgeCls = GIT_STATUS_BADGES[f.status] || "bg-zinc-800 text-zinc-300 border-zinc-700";
            return `
              <div class="rounded-xl bg-zinc-900/70 border border-zinc-800 p-2.5 text-xs space-y-1.5">
                <div onclick="window.appActions.toggleGitFileDiff('${escapeJsSingleQuote(
                  f.path
                )}')"
                  class="cursor-pointer flex items-center justify-between gap-2 select-none hover:text-sky-300 transition">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="w-5 h-5 rounded flex items-center justify-center font-bold font-mono text-[10px] border ${badgeCls}">
                      ${escapeHtml(f.status)}
                    </span>
                    <span class="font-mono text-xs text-zinc-200 truncate">${escapeHtml(
                      f.path
                    )}</span>
                  </div>
                  <div class="flex items-center gap-1.5 shrink-0 font-mono text-[10px]">
                    ${f.additions > 0 ? `<span class="text-emerald-400">+${f.additions}</span>` : ""}
                    ${f.deletions > 0 ? `<span class="text-rose-400">-${f.deletions}</span>` : ""}
                    <span class="text-zinc-500">${isExp ? "▲" : "▼"}</span>
                  </div>
                </div>
                <div id="${gitPathToSlotId(f.path)}">
                  ${
                    isExp && state.gitDiffCache[f.path]
                      ? renderUnifiedDiffHtml(state.gitDiffCache[f.path], f.path)
                      : ""
                  }
                </div>
              </div>
            `;
          })
          .join("");

  bodyEl.innerHTML = `
    <div class="space-y-3 pb-6">
      <div class="flex items-center justify-between p-3 rounded-xl bg-zinc-950 border border-zinc-800 text-xs">
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded-lg bg-sky-500/15 text-sky-300 border border-sky-500/30 font-mono font-semibold">
            🌿 ${escapeHtml(data.branch || "HEAD")}
          </span>
          <span class="text-zinc-400">${data.changed_count} 个未提交变更文件</span>
        </div>
        ${
          files.length > 0
            ? `<button onclick="window.appActions.viewFullGitDiff()"
                class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-sky-300 text-xs transition">
                查看汇总 Diff
              </button>`
            : ""
        }
      </div>
      <div class="space-y-1.5">${listHtml}</div>
    </div>
  `;
}

