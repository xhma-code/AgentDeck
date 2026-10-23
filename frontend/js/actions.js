/**
 * AgentDeck 用户动作与命令执行中枢（绑定 window.appActions）
 */

import { state, markSessionViewed } from "./state.js";
import {
  apiFetch,
  showToast,
  closePairingGuideModal,
} from "./api.js";
import {
  connectWebSocket,
  scheduleSilentMessagesRefresh,
  scheduleSilentSessionsRefresh,
} from "./network.js";
import {
  selectSession,
  loadSessions,
  loadSessionMessages,
  loadSystemStatus,
  loadWorkspaces,
  loadOlderMessages,
  getAllAttentionSessions,
  getLatestSession,
  isSessionStatusActive,
  checkFrontendVersion,
  handleStatusBtnClick,
  cleanFriendlyTitle,
} from "./data.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderApprovalBanners,
  renderOtherRunningPill,
  renderModelPicker,
  renderMessagesList,
  renderLiveStreamOverlay,
  renderTokenUsage,
  renderGitStatusBody,
  renderSlashMenu,
  renderQrModalContent,
  scrollToBottom,
  updateGitStatusBadge,
} from "./render.js";
import {
  openNewSessionModal,
  confirmCreateNewSession,
  updateDrawerVisibility,
} from "./modals.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  copyTextToClipboard,
  applyButtonCheckmark,
  saveToBoundedStore,
  gitPathToSlotId,
  cleanFriendlyText,
} from "./utils.js";

let holdTimer = null;
let holdStartTime = 0;

    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "重试加载更早消息";
    }
  }
}

// --- 5. 核心操作：发送消息、切换模型/模式、中断、审批 ---
export async function handleSendPrompt(customPrompt = null) {
  const inputEl = document.getElementById("prompt-input");
  const text = (customPrompt !== null ? customPrompt : inputEl.value).trim();
  if (!text) return;

  if (!state.selectedSessionId) {
    openNewSessionModal(text);
    return;
  }

  requestNotificationPermission();
  if (customPrompt === null) {
    inputEl.value = "";
    inputEl.style.height = "auto";
  }
  updatePromptActionBtnState();

  const modelSelect = document.getElementById("model-select");
  const modeSelect = document.getElementById("execution-mode-select");
  const chosenModel = modelSelect ? modelSelect.value : "";
  const chosenMode = modeSelect ? modeSelect.value : "ask";

  const optMsg = {
    item_id: `optimistic_${Date.now()}`,
    step_index: state.messages.length + 1,
    role: "user",
    created_at: new Date().toISOString(),
    content: text,
    thinking: "",
    tool_calls: [],
    status: "RUNNING",
  };

  if (!state.optimisticMessages[state.selectedSessionId]) {
    state.optimisticMessages[state.selectedSessionId] = [];
  }
  state.optimisticMessages[state.selectedSessionId].push(optMsg);

  state.messages.push(optMsg);
  state.pendingTurnThinking[state.selectedSessionId] = Date.now();
  state.autoScrollLocked = false;

  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (curSess) {
    curSess.status = "running";
    curSess.preview = text;
    renderSessionList();
    renderCurrentSessionHeader();
  }

  renderMessagesList();
  renderLiveStreamOverlay();

  try {
    const res = await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/send`,
      {
        method: "POST",
        body: JSON.stringify({
          prompt: text,
          model_name: chosenModel || undefined,
          execution_mode: chosenMode || undefined,
          auto_fork_ide: true,
        }),
      }
    );

    if (res.forked_from && res.unified_id && res.unified_id !== state.selectedSessionId) {
      showToast("已克隆至 AGY CLI 继续", "info");
      const oldUid = state.selectedSessionId;
      state.selectedSessionId = res.unified_id;
      if (state.optimisticMessages[oldUid]) {
        state.optimisticMessages[res.unified_id] = state.optimisticMessages[oldUid];
        delete state.optimisticMessages[oldUid];
      }
      if (state.pendingTurnThinking[oldUid]) {
        state.pendingTurnThinking[res.unified_id] = state.pendingTurnThinking[oldUid];
        delete state.pendingTurnThinking[oldUid];
      }
    }
    await loadSessions();
    await loadSessionMessages(state.selectedSessionId, true);
  } catch (err) {
    console.error("发送失败:", err);
  }
}

export async function handleAbortCurrentSession() {
  if (!state.selectedSessionId) return;
  try {
    delete state.pendingTurnThinking[state.selectedSessionId];
    delete state.streamingBuffer[state.selectedSessionId];
    if (state.optimisticMessages[state.selectedSessionId]) {
      delete state.optimisticMessages[state.selectedSessionId];
    }
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (curSess && curSess.status === "running") {
      curSess.status = "idle";
    }
    updatePromptActionBtnState();
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/abort`,
      { method: "POST" }
    );
    showToast("已中断进程", "info");
    await loadSessions();
    renderCurrentSessionHeader();
    renderLiveStreamOverlay();
  } catch (err) {
    console.error("中断会话失败:", err);
  }
}

export async function handleModeChange(newMode) {
  if (!state.selectedSessionId) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/meta`,
      {
        method: "PATCH",
        body: JSON.stringify({ execution_mode: newMode }),
      }
    );
    const modeLabels = { ask: "审批", plan: "规划", accept_edits: "自动改", auto: "全自动" };
    showToast(`模式: ${modeLabels[newMode] || newMode}`, "success");
    await loadSessions();
  } catch (err) {
    console.error("更新权限模式失败:", err);
  }
}

export async function handleModelChange(newModel) {
  if (!state.selectedSessionId || !newModel) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/meta`,
      {
        method: "PATCH",
        body: JSON.stringify({ model_name: newModel }),
      }
    );
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (sess) sess.model_name = newModel;
    showToast(`模型: ${newModel}`, "success");
  } catch (err) {
    console.error("切换模型失败:", err);
  }
}

export async function handleTogglePin(unifiedId, currentPinned, ev) {
  if (ev) ev.stopPropagation();
  try {
    await apiFetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}/meta`, {
      method: "PATCH",
      body: JSON.stringify({ is_pinned: currentPinned ? 0 : 1 }),
    });
    await loadSessions();
  } catch (err) {
    console.error("切换置顶状态失败:", err);
  }
}

export async function submitApprovalDecision(unifiedId, decision, feedback = "") {
  try {
    const payload = { decision };
    if (feedback && feedback.trim()) {
      payload.feedback = feedback.trim();
    }
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(unifiedId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
    state.denyFeedbackOpen = false;
    state.denyFeedbackText = "";
    const labelMap = {
      approved: "已允许执行",
      approve_session_edits: "本会话已免审",
      denied: feedback ? "已拒绝并发送指导意见" : "已拒绝执行",
    };
    showToast(labelMap[decision] || "已提交", "success");
    await loadSessions();
  } catch (err) {
    console.error("提交审批决定失败:", err);
  }
}

// --- 高危命令长按 0.8s 确认 ---
export function startHoldToApprove(unifiedId, btnEl) {
  const fillEl = btnEl.querySelector(".hold-progress-fill");
  holdStartTime = performance.now();
  const duration = 800;

  if (holdTimer) cancelAnimationFrame(holdTimer);

  function tick(now) {
    const elapsed = now - holdStartTime;
    const pct = Math.min(100, (elapsed / duration) * 100);
    if (fillEl) fillEl.style.width = `${pct}%`;

    if (pct >= 100) {
      if (fillEl) fillEl.style.width = "0%";
      if ("vibrate" in navigator) {
        try {
          navigator.vibrate(80);
        } catch (_) {}
      }
      submitApprovalDecision(unifiedId, "approved");
      return;
    }
    holdTimer = requestAnimationFrame(tick);
  }

  holdTimer = requestAnimationFrame(tick);
}

export function cancelHoldToApprove(btnEl) {

      : type === "success"
      ? "bg-emerald-950/95 border-emerald-500/60 text-emerald-200"
      : "bg-zinc-900/95 border-sky-500/50 text-sky-200";
  el.className = `px-3 py-2 rounded-xl border shadow-xl text-xs font-medium transition-all ${colorCls}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 3000);
}

// --- 暴露给模板的事件动作集 ---
window.appActions = {
  closePairingGuideModal() {
    closePairingGuideModal();
  },
  async submitPairCodeManual(code) {
    code = (code || "").trim();
    if (!code) {
      showToast("请输入有效配对码", "error");
      return;
    }
    const ua = navigator.userAgent || "Mobile";
    const devName = /iPhone|iPad/i.test(ua) ? "iPhone" : /Android/i.test(ua) ? "Android" : "Web Client";
    try {
      const res = await apiFetch("/api/v1/auth/exchange", {
        method: "POST",
        body: JSON.stringify({ pair_code: code, device_name: devName }),
      });
      if (res.token) {
        state.token = res.token;
        localStorage.setItem("agentdeck_token", res.token);
        showToast("设备配对成功！", "success");
        closePairingGuideModal();
        const pwaUrl = new URL(window.location.href);
        pwaUrl.searchParams.delete("pair_code");
        pwaUrl.searchParams.set("token", res.token);
        window.history.replaceState({}, document.title, pwaUrl.pathname + pwaUrl.search + pwaUrl.hash);
        await Promise.all([loadSystemStatus(), loadWorkspaces(), loadSessions()]);
        connectWebSocket();
      }
    } catch (e) {
      showToast("配对失败: " + (e.message || "配对码无效或已过期"), "error");
    }
  },
  switchQrChannel(type) {
    state.selectedQrNet = type;
    renderQrModalContent();
  },
  switchQrNetMode(mode) {
    state.qrNetMode = mode;
    const modes = ["lan", "zerotier", "tailscale"];
    let switched = false;
    for (const m of modes) {
      const panel = document.getElementById(`qr-panel-${m}`);
      const tab = document.getElementById(`qr-tab-${m}`);
      const isActive = m === mode;
      if (panel) {
        panel.classList.toggle("hidden", !isActive);
        panel.classList.toggle("block", isActive);
        switched = true;
      }
      if (tab) {
        if (isActive) {
          tab.className = "flex-1 py-1.5 px-2 rounded-lg font-medium transition cursor-pointer select-none active:scale-95 bg-sky-500/20 text-sky-300 border border-sky-500/40 shadow-xs";
        } else {
          tab.className = "flex-1 py-1.5 px-2 rounded-lg font-medium transition cursor-pointer select-none active:scale-95 text-zinc-400 hover:text-zinc-200";
        }
      }
    }
    if (!switched) {
      renderQrModalContent();
    }
  },
  handleStatusBtnClick,
  selectSession,
  jumpToRunningSession() {
    const list = getAllAttentionSessions();
    if (!list || list.length === 0) return;
    const curIdx = list.findIndex((s) => s.unified_id === state.selectedSessionId);
    let nextIdx = 0;
    if (curIdx >= 0) {
      nextIdx = (curIdx + 1) % list.length;
    }
    const target = list[nextIdx] || list[0];
    if (target && target.unified_id) {
      selectSession(target.unified_id);
    }
  },
  togglePin: handleTogglePin,
  toggleProject(projectKey) {
    state.expandedProjects[projectKey] = !state.expandedProjects[projectKey];
    renderSessionList();
  },
  toggleSection(sectionKey) {
    if (!state.collapsedSections) state.collapsedSections = {};
    state.collapsedSections[sectionKey] = !state.collapsedSections[sectionKey];
    try {
      localStorage.setItem("agentdeck_sections_collapsed", JSON.stringify(state.collapsedSections));
    } catch (e) {}
    renderSessionList();
  },
  newProjectSession(projectPath, ev) {
    if (ev) ev.stopPropagation();
    openNewSessionModal("", projectPath);
  },
  async hideProject(projectPath, ev, projectName = "") {
    if (ev) ev.stopPropagation();
    const nameHint = projectName ? `“${projectName}”` : "该项目";
    const confirmed = window.confirm(
      `确定要从左侧项目列表移出 ${nameHint} 吗？\n\n提示：仅从项目列表隐藏，不会删除任何代码文件与历史对话记录。`
    );
    if (!confirmed) return;
    try {
      await apiFetch("/api/v1/workspaces/hide", {
        method: "POST",
        body: JSON.stringify({ workspace_path: projectPath }),
      });
      showToast(`已从项目列表移出 ${nameHint}`, "info");
      await Promise.all([loadWorkspaces(), loadSessions()]);
    } catch (err) {
      console.error("从项目列表移出失败:", err);
    }
  },
  onNewSessionAgentChange(agentType) {
    const modelSelect = document.getElementById("new-session-model");
    renderModelOptionsForAgent(modelSelect, agentType, "");
    const modeSelect = document.getElementById("new-session-mode");
    renderModeOptionsForAgent(modeSelect, agentType, modeSelect ? modeSelect.value : "ask", false);
  },
  setFilter(f) {
    state.activeFilter = f;
    document.querySelectorAll("[data-agent-tab]").forEach((btn) => {
      if (btn.getAttribute("data-agent-tab") === f) {
        btn.className =
          "px-2.5 py-1.5 sm:py-1 rounded-lg bg-sky-500 text-zinc-950 font-semibold text-[11px] shrink-0 transition active:scale-95";
      } else {
        btn.className =
          "px-2.5 py-1.5 sm:py-1 rounded-lg bg-zinc-900 text-zinc-300 hover:bg-zinc-800 text-[11px] shrink-0 transition active:scale-95";
      }
    });
    // 0ms 纯前端内存过滤
    renderSessionList();
    // 若当前选中的会话与新选定的 Agent Tab 互斥，自动顺滑切换到该 Agent 标签下的首选会话，避免中间对话区内容滞留
    if (f !== "all" && state.selectedSessionId) {
      const curAgent = state.selectedSessionId.split(":")[0];
      if (curAgent !== f) {
        const rawList = state.allSessions.length > 0 ? state.allSessions : state.sessions;
        const matchSessions = rawList.filter((s) => s.agent_type === f);
        const nextSess = pickDefaultSession(matchSessions);
        if (nextSess) {
          selectSession(nextSess.unified_id);
        }
      }
    }
    // 后台静默同步最新列表
    loadSessions(true);
  },
  fillPromptAndSend(text) {
    const input = document.getElementById("prompt-input");
    if (input) input.value = text;
    handleSendPrompt(text);
  },
  toggleSessionMenu(ev) {
    if (ev) ev.stopPropagation();
    const menu = document.getElementById("session-action-menu");
    if (!menu) return;
    const isHidden = menu.classList.contains("hidden");
    if (isHidden) {
      const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
      const isPinned = curSess ? !!curSess.is_pinned : false;
      const icon = document.getElementById("session-menu-pin-icon");
      const text = document.getElementById("session-menu-pin-text");
      if (icon) icon.className = isPinned ? "text-amber-400 text-xs" : "text-zinc-400 text-xs";
      if (text) text.textContent = isPinned ? "取消置顶" : "置顶此对话";
      menu.classList.remove("hidden");
    } else {
      menu.classList.add("hidden");
    }
  },
  async toggleCurrentPin() {
    const menu = document.getElementById("session-action-menu");
    if (menu) menu.classList.add("hidden");
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (!curSess) return;
    await handleTogglePin(curSess.unified_id, !!curSess.is_pinned);
  },
  async renameCurrentSession() {
    const menu = document.getElementById("session-action-menu");
    if (menu) menu.classList.add("hidden");
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (!curSess) return;
    const newTitle = window.prompt("重命名当前对话：", curSess.title);
    if (!newTitle || !newTitle.trim() || newTitle.trim() === curSess.title) return;
    try {
      await apiFetch(`/api/v1/sessions/${encodeURIComponent(curSess.unified_id)}/meta`, {
        method: "PATCH",
        body: JSON.stringify({ title: newTitle.trim() }),
      });
      curSess.title = newTitle.trim();
      renderCurrentSessionHeader();
      renderSessionList();
      showToast("会话已重命名", "success");
    } catch (err) {
      showToast(`重命名失败: ${err.message}`, "error");
    }
  },
  async deleteCurrentSession() {
    const menu = document.getElementById("session-action-menu");
    if (menu) menu.classList.add("hidden");
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (!curSess) return;
    if (!window.confirm(`确定删除会话「${curSess.title}」？此操作不可逆。`)) return;
    try {
      await apiFetch(`/api/v1/sessions/${encodeURIComponent(curSess.unified_id)}`, {
        method: "DELETE",
      });
      showToast("会话已删除", "success");
      state.sessions = state.sessions.filter((s) => s.unified_id !== curSess.unified_id);
      state.allSessions = state.allSessions.filter((s) => s.unified_id !== curSess.unified_id);
      const nextSess = pickDefaultSession(state.sessions);
      state.selectedSessionId = nextSess?.unified_id || null;
      renderSessionList();
      renderCurrentSessionHeader();
      if (state.selectedSessionId) {
        selectSession(state.selectedSessionId);
      } else {
        history.replaceState(null, "", window.location.pathname + window.location.search);
        state.messages = [];
        renderMessagesList();
      }
    } catch (err) {
      showToast(`删除失败: ${err.message}`, "error");
    }
  },
  loadOlderMessages,
  onDetailToggle(detailsEl, detailId, turnKey) {
    if (!detailsEl || !detailId) return;
    const isOpen = detailsEl.open;
    state.expandedDetails[detailId] = isOpen;
    if (turnKey) {
      state.turnThinkingExpanded[turnKey] = isOpen;
      if (isOpen) {
        state.expandedTurns[turnKey] = true;
      }
    }
    if (!isOpen) return;
    const slot = detailsEl.querySelector("[data-lazy-slot]");
    if (!slot || slot.innerHTML.trim() !== "") return;
    const entry = state.lazyDetailsStore[detailId];
    if (!entry) return;
    if (entry.type === "diff") {
      slot.innerHTML = renderUnifiedDiffHtml(entry.raw, entry.filePath || "", {
        diffId: detailId,
        addCount: entry.addCount,
        delCount: entry.delCount,
      });
    } else {
      slot.textContent = entry.raw || "";
    }
  },
  onThinkSummaryClick(e, turnKey, detailId) {
    const details = e.currentTarget?.closest("details");
    if (!details) return;
    const willOpen = !details.open;
    state.expandedDetails[detailId] = willOpen;
    state.turnThinkingExpanded[turnKey] = willOpen;
    if (willOpen) {
      state.expandedTurns[turnKey] = true;
    }
  },
  toggleDrawer(open) {
    const nextOpen = open !== undefined ? open : !state.drawerOpen;
    state.drawerOpen = nextOpen;
    updateDrawerVisibility();
    if (nextOpen) {
      renderSessionList();
    }
  },
  toggleSidebar(collapsed) {
    state.sidebarCollapsed =
      collapsed !== undefined ? Boolean(collapsed) : !state.sidebarCollapsed;
    localStorage.setItem(
      "agentdeck_sidebar_collapsed",
      state.sidebarCollapsed ? "1" : "0"
    );
    updateDrawerVisibility();
  },
  async openQrModal() {
    startQrExpireCountdown();
    const m = document.getElementById("qr-modal");
    if (m) m.classList.remove("hidden");
    if (state.systemStatus) {
      renderQrModalContent();
    } else {
      const box = document.getElementById("qr-modal-body");
      if (box) {
        box.innerHTML = `
          <div class="py-12 flex flex-col items-center justify-center space-y-3">
            <div class="w-8 h-8 rounded-full border-2 border-sky-400 border-t-transparent animate-spin"></div>
            <div class="text-xs text-zinc-400">正在生成配对信息...</div>
          </div>
        `;
      }
    }
    // 静默在后台拉取最新配对状态，零阻塞 UI 弹出
    loadSystemStatus().then(() => {
      renderQrModalContent();
    }).catch((e) => {
      console.warn("[AgentDeck] 后台更新配对状态提示:", e);
    });
  },
  closeQrModal() {
    state.pairedSuccessNotice = null;
    if (qrExpireTimer) {
      clearTimeout(qrExpireTimer);
      qrExpireTimer = null;
    }
    const m = document.getElementById("qr-modal");
    if (m) m.classList.add("hidden");
  },
  async copyQrUrl(url) {
    if (!url) return;
    const ok = await copyTextToClipboard(url);
    if (ok) {
      showToast("直连地址已复制", "success");
    } else {
      showToast("复制失败，请手动选择复制", "error");
    }
  },
  async refreshPairCodeAndQr() {
    try {
      await apiFetch("/api/v1/auth/refresh_pair_code", { method: "POST" });
      await loadSystemStatus();
      startQrExpireCountdown();
      showToast("配对码已刷新", "success");
    } catch (e) {
      showToast("刷新失败: " + e.message, "error");
    }
  },
  async toggleAwakeLockFromSidebar() {
    const nextState = !state.systemStatus?.awake_lock_active;
    try {
      await apiFetch(`/api/v1/system/awake-lock?enable=${nextState}`, { method: "POST" });
      await loadSystemStatus();
      showToast(nextState ? "PC 防休眠锁已开启" : "PC 防休眠锁已关闭", "info");
    } catch (e) {
      showToast("切换防休眠失败: " + e.message, "error");
    }
  },
  openAgentsHealthModal() {
    renderAgentsHealthModal();
    const m = document.getElementById("agents-health-modal");
    if (m) m.classList.remove("hidden");
  },
  closeAgentsHealthModal() {
    const m = document.getElementById("agents-health-modal");
    if (m) m.classList.add("hidden");
  },
  openNewSessionModal: (prefill = "") => openNewSessionModal(prefill, ""),
  closeNewSessionModal() {
    document.getElementById("new-session-modal").classList.add("hidden");
  },
  confirmCreateNewSession,
  handlePromptAction,
  sendPrompt: () => handleSendPrompt(null),
  abortSession: handleAbortCurrentSession,
  changeMode: (el) => handleModeChange(el.value),
  changeModel: (el) => handleModelChange(el.value),
  submitApproval: submitApprovalDecision,
  startHold: startHoldToApprove,
  cancelHold: cancelHoldToApprove,
  toggleWordWrap() {
    state.wordWrapDiff = !state.wordWrapDiff;
    state.autoScrollLocked = true;

    // 1. 实时就地同步页面及模态框中的所有 .unified-diff-box，实现全屏零卡顿即时换行
    const boxes = document.querySelectorAll(".unified-diff-box");
    boxes.forEach((box) => {
      if (state.wordWrapDiff) {
        box.classList.add("word-wrap");
      } else {
        box.classList.remove("word-wrap");
      }
    });

    // 2. 实时更新所有换行切换按钮文本
    const toggleBtns = document.querySelectorAll("[data-action='toggle-word-wrap']");
    toggleBtns.forEach((btn) => {
      btn.textContent = state.wordWrapDiff ? "横向滚动" : "自动换行";
    });

    // 3. 模态框未打开时同步重绘消息列表与审批流
    const modal = document.getElementById("doc-preview-modal");
    const isModalOpen = modal && !modal.classList.contains("hidden");
    if (!isModalOpen) {
      const container = document.getElementById("messages-container");
      const prevTop = container ? container.scrollTop : 0;
      renderMessagesList();
      renderApprovalBanners();
      if (container) container.scrollTop = prevTop;
    }
  },
  toggleTurnSteps(turnKey) {
    const isCurrentlyExpanded = !!state.expandedTurns[turnKey] || !!state.turnThinkingExpanded[turnKey];
    const nextVal = !isCurrentlyExpanded;
    state.expandedTurns[turnKey] = nextVal;
    if (!nextVal) {
      state.turnThinkingExpanded[turnKey] = false;
    }
    state.autoScrollLocked = true;
    const turn = state.turnsMap[turnKey];
    const wrapEl = document.getElementById(`turn-process-wrap-${turnKey}`);
    if (turn && wrapEl) {
      wrapEl.outerHTML = renderTurnProcessBar(turn);
      return;
    }
    const container = document.getElementById("messages-container");
    const prevTop = container ? container.scrollTop : 0;
    renderMessagesList();
    if (container) container.scrollTop = prevTop;
  },
  toggleCodeBlock(codeId) {
    const isExpanded = !state.expandedCodeBlocks[codeId];
    state.expandedCodeBlocks[codeId] = isExpanded;
    state.autoScrollLocked = true;

    const bodyEl = document.getElementById(`code-body-${codeId}`);
    const btnEl = document.getElementById(`code-btn-${codeId}`);
    if (bodyEl && btnEl) {
      const lineCount = btnEl.getAttribute("data-line-count") || "";
      if (isExpanded) {
        bodyEl.classList.remove("collapsed");
        const fade = bodyEl.querySelector(".md-code-fade");
        if (fade) fade.remove();
        btnEl.textContent = "⬆ 收起代码";
      } else {
        bodyEl.classList.add("collapsed");
        if (!bodyEl.querySelector(".md-code-fade")) {
          bodyEl.insertAdjacentHTML("beforeend", '<div class="md-code-fade"></div>');
        }
        btnEl.textContent = `⬇ 展开全部代码 (共 ${lineCount} 行)`;
      }
      return;
    }

    if (String(codeId).startsWith("doc_modal")) {
      const modalBody = document.getElementById("doc-preview-body");
      if (modalBody && state.currentDocRawContent) {
        const prevDocScroll = modalBody.scrollTop;
        modalBody.innerHTML = renderMarkdownHtml(state.currentDocRawContent, "doc_modal");
        modalBody.scrollTop = prevDocScroll;
      }
      return;
    }
    const container = document.getElementById("messages-container");
    const prevTop = container ? container.scrollTop : 0;
    renderMessagesList();
    if (container) container.scrollTop = prevTop;
  },
  toggleUserMessage(itemId) {
    const isExpanded = !state.expandedUserMessages[itemId];
    state.expandedUserMessages[itemId] = isExpanded;
    state.autoScrollLocked = true;

    const textEl = document.getElementById(`user-msg-text-${itemId}`);
    const btnEl = document.getElementById(`user-msg-btn-${itemId}`);
    const msgObj = state.messages.find((m) => m.item_id === itemId);
    if (textEl && btnEl && msgObj) {
      const rawUserText = String(msgObj.content || "").trim();
      const userLines = rawUserText.split("\n");
      const collapsedText = userLines.slice(0, 6).join("\n").slice(0, 280) + "...";
      textEl.textContent = isExpanded ? rawUserText : collapsedText;
      btnEl.textContent = isExpanded ? "收起 ▴" : `展开全部 (${rawUserText.length}字) ▾`;
      return;
    }

    const container = document.getElementById("messages-container");
    const prevTop = container ? container.scrollTop : 0;
    renderMessagesList();
    if (container) container.scrollTop = prevTop;
  },
  async copyCodeBlock(codeId, btnEl) {
    const raw = state.codeBlockStore[codeId] || "";
    const ok = await copyTextToClipboard(raw);
    if (ok) {
      if (btnEl) {
        applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
      } else {
        showToast("已复制代码", "success");
      }
    } else {
      showToast("复制失败，请长按手动选择", "error");
    }
  },
  async openFilePreview(rawPath) {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    modal.classList.remove("hidden");
    if (titleEl) titleEl.textContent = `📄 ${getBasename(rawPath)}`;
    if (pathEl) pathEl.textContent = rawPath;
    bodyEl.innerHTML = `
      <div class="space-y-3 py-4">
        <div class="skeleton-block h-8 w-1/2"></div>
        <div class="skeleton-block h-32 w-full"></div>
        <div class="skeleton-block h-24 w-5/6"></div>
      </div>
    `;

    try {
      const data = await apiFetch(`/api/v1/files/read?path=${encodeURIComponent(rawPath)}`);
      state.currentDocRawContent = data.content || "";
      if (titleEl) titleEl.textContent = `📄 ${data.filename || getBasename(rawPath)}`;
      if (pathEl) pathEl.textContent = data.path || rawPath;

      if (data.is_markdown) {
        bodyEl.innerHTML = renderMarkdownHtml(data.content, "doc_modal");
      } else {
        const lang = (data.ext || "").replace(/^\./, "") || "text";
        bodyEl.innerHTML = renderMarkdownHtml(
          "```" + lang + "\n" + (data.content || "") + "\n```",
          "doc_modal_code"
        );
      }
    } catch (err) {
      bodyEl.innerHTML = `
        <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
          读取文件失败：${escapeHtml(err.message || String(err))}
        </div>
      `;
    }
  },
  closeDocModal() {
    const modal = document.getElementById("doc-preview-modal");
    if (modal) modal.classList.add("hidden");
    document.body.style.overflow = "";
  },
  openDiffFullscreen(diffText, filePath = "", options = {}) {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    state.currentDocRawContent = diffText || "";
    const bName = filePath ? getBasename(filePath) : "代码变更 Diff";
    if (titleEl) titleEl.textContent = `变更 Diff · ${bName}`;
    if (pathEl) pathEl.textContent = filePath || "";

    bodyEl.innerHTML = renderUnifiedDiffHtml(diffText, filePath, {
      isModal: true,
      diffId: options.diffId,
      addCount: options.addCount,
      delCount: options.delCount,
    });
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  },
  openDiffFullscreenById(diffId, e) {
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    const entry =
      (state.lazyDetailsStore && state.lazyDetailsStore[diffId]) ||
      (state.diffStore && state.diffStore[diffId]);
    if (!entry) return;
    const diffText = entry.raw || entry.diffText || "";
    const filePath = entry.filePath || "";
    window.appActions.openDiffFullscreen(diffText, filePath, {
      diffId,
      addCount: entry.addCount,
      delCount: entry.delCount,
    });
  },
  async copyCurrentDocContent(btnEl) {
    if (!state.currentDocRawContent) {
      showToast("无可复制内容", "info");
      return;
    }
    const targetBtn = btnEl || document.getElementById("doc-preview-copy-btn");
    const ok = await copyTextToClipboard(state.currentDocRawContent);
    if (ok) {
      if (targetBtn) {
        applyButtonCheckmark(targetBtn, { highlightBorder: true });
      }
      showToast("已复制全文", "success");
    } else {
      showToast("复制失败，请手动选择复制", "error");
    }
  },
  async copyThinkingText(detailId, btnEl, e) {
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    const entry = state.lazyDetailsStore[detailId];
    const text = entry ? entry.raw : "";
    if (!text) {
      showToast("思考内容为空", "info");
      return;
    }
    const ok = await copyTextToClipboard(text);
    if (ok) {
      if (btnEl) {
        applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
      }
      showToast("已复制思考过程", "success");
    } else {
      showToast("复制失败，请手动选择复制", "error");
    }
  },
  collapseCurrentExpandedSteps() {
    state.expandedTurns = {};
    state.turnThinkingExpanded = {};
    const pill = document.getElementById("collapse-steps-pill");
    if (pill) pill.classList.add("hidden");
    const container = document.getElementById("messages-container");
    const prevTop = container ? container.scrollTop : 0;
    renderMessagesList();
    if (container) container.scrollTop = prevTop;
    showToast("已收起执行步骤", "info");
  },
  async toggleAwakeLock(enable) {
    await apiFetch(`/api/v1/system/awake-lock?enable=${enable}`, { method: "POST" });
    await loadSystemStatus();
  },
  scrollToBottom() {
    state.autoScrollLocked = false;
    scrollToBottom();
  },
  async loadFullOutput(unifiedId, stepIndex, btnEl, ev) {
    if (ev) ev.stopPropagation();
    if (!unifiedId) return;
    if (btnEl) btnEl.textContent = "加载中...";
    try {
      const res = await apiFetch(
        `/api/v1/sessions/${encodeURIComponent(unifiedId)}/steps/${stepIndex}/full`
      );
      if (res.full_output) {
        state.fullOutputStore[`${unifiedId}_${stepIndex}`] = res.full_output;
        const msg = state.messages.find((m) => m.step_index === stepIndex);
        if (msg && Array.isArray(msg.tool_calls)) {
          msg.tool_calls.forEach((tc) => {
            tc.output = res.full_output;
            tc.is_truncated = false;
          });
        }
      }
      const detailsEl = btnEl ? btnEl.closest("details") : null;
      const preEl = detailsEl ? detailsEl.querySelector("pre") : null;
      if (preEl && res.full_output) {
        preEl.textContent = res.full_output;
        preEl.classList.remove("max-h-44");
        preEl.classList.add("max-h-96");
        if (detailsEl) detailsEl.open = true;
      }
      if (btnEl) btnEl.remove();
    } catch (_) {
      if (btnEl) btnEl.textContent = "重试加载";
    }
  },
  toggleDenyFeedback(open) {
    state.denyFeedbackOpen = open !== undefined ? Boolean(open) : !state.denyFeedbackOpen;
    renderApprovalBanners();
    if (state.denyFeedbackOpen) {
      setTimeout(() => {
        const inp = document.getElementById("deny-feedback-input");
        if (inp) inp.focus();
      }, 50);
    }
  },
  submitDenyWithFeedback(unifiedId) {
    const inp = document.getElementById("deny-feedback-input");
    const feedback = inp ? inp.value.trim() : "";
    submitApprovalDecision(unifiedId, "denied", feedback);
  },
  toggleQuestionOption(arg1, qi, optText, isMulti) {
    const opts = typeof arg1 === "object" && arg1 !== null
      ? arg1
      : { turnKey: arg1, qi, optText, isMulti };
    const qKey = `${opts.turnKey}_q${opts.qi}`;
    const cur = state.questionSelections[qKey] || [];
    if (opts.isMulti) {
      state.questionSelections[qKey] = cur.includes(opts.optText)
        ? cur.filter((x) => x !== opts.optText)
        : [...cur, opts.optText];
    } else {
      state.questionSelections[qKey] = cur.includes(opts.optText) ? [] : [opts.optText];
    }
    state.autoScrollLocked = true;
    const turn = state.turnsMap[opts.turnKey];
    const optsEl = document.getElementById(`q-opts-${qKey}`);
    if (turn && turn.questions && turn.questions[opts.qi] && optsEl) {
      optsEl.innerHTML = renderQuestionOptionsHtml(opts.turnKey, opts.qi, turn.questions[opts.qi]);
      return;
    }
    renderMessagesList();
  },
  setQuestionWriteIn(turnKey, qi, val) {
    const qKey = `${turnKey}_q${qi}`;
    state.questionWriteIns[qKey] = val;
  },
  submitQuestionAnswers(turnKey) {
    const turn = state.turnsMap[turnKey];
    if (!turn || !turn.questions) return;
    const parts = [];
    turn.questions.forEach((q, qi) => {
      const qKey = `${turnKey}_q${qi}`;
      const selected = state.questionSelections[qKey] || [];
      const writeIn = (state.questionWriteIns[qKey] || "").trim();
      const ansParts = [...selected];
      if (writeIn) ansParts.push(writeIn);
      const finalAns = ansParts.join("；") || "按推荐方案";
      parts.push(turn.questions.length > 1 ? `关于“${q.question}”：${finalAns}` : finalAns);
    });
    const answerText = parts.join("\n") || "确认执行";
    // 持久化决策记录，方便历史折叠卡片回溯
    state.persistedQuestionDecisions[turnKey] = { answerText, parts, answeredAt: Date.now() };
    try {
      localStorage.setItem(
        "agentdeck_question_decisions",
        JSON.stringify(state.persistedQuestionDecisions)
      );
    } catch (_) {}
    handleSendPrompt(answerText);
  },
  skipQuestionAnswers(turnKey) {
    state.persistedQuestionDecisions[turnKey] = {
      answerText: "按最佳方案执行",
      parts: ["按推荐方案"],
      answeredAt: Date.now(),
    };
    try {
      localStorage.setItem(
        "agentdeck_question_decisions",
        JSON.stringify(state.persistedQuestionDecisions)
      );
    } catch (_) {}
    handleSendPrompt("按你推荐的最佳方案继续执行。");
  },
  toggleSlashMenu(forceOpen) {
    state.slashMenuOpen = forceOpen !== undefined ? Boolean(forceOpen) : !state.slashMenuOpen;
    renderSlashMenu("");
  },
  selectSlashCommand(cmd) {
    const input = document.getElementById("prompt-input");
    if (!input) return;
    state.slashMenuOpen = false;
    const popover = document.getElementById("slash-popover");
    if (popover) popover.classList.add("hidden");

    if (cmd.startsWith("/")) {
      input.value = `${cmd} `;
      input.focus();
    } else {
      // 快捷一键动作直接填入并发送
      input.value = cmd;
      handleSendPrompt(cmd);
    }
  },
  async openGitStatusModal() {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    const pPath = sess?.project_path || "";
    if (!pPath) {
      showToast("当前会话无关联工程目录", "info");
      return;
    }

    modal.classList.remove("hidden");
    if (titleEl) titleEl.textContent = `🌿 Git 工作区变更`;
    if (pathEl) pathEl.textContent = `${pPath}`;
    bodyEl.innerHTML = `
      <div class="space-y-3 py-4">
        <div class="skeleton-block h-8 w-1/2"></div>
        <div class="skeleton-block h-32 w-full"></div>
      </div>
    `;

    try {
      const data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(pPath)}`);
      state.currentGitStatus = data;
      renderGitStatusBody(data);
    } catch (err) {
      bodyEl.innerHTML = `
        <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
          获取 Git 状态失败：${escapeHtml(err.message || String(err))}
        </div>
      `;
    }
  },
  async toggleGitFileDiff(relPath) {
    const isExp = !state.gitExpandedDiffs[relPath];
    state.gitExpandedDiffs[relPath] = isExp;
    const container = document.getElementById(gitPathToSlotId(relPath));
    if (!container) return;
    if (!isExp) {
      container.innerHTML = "";
      return;
    }

    if (state.gitDiffCache[relPath]) {
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
      return;
    }

    container.innerHTML = `<div class="p-2 text-xs text-sky-400">正在加载 Diff...</div>`;
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    try {
      const res = await apiFetch(
        `/api/v1/git/diff?project_path=${encodeURIComponent(
          sess?.project_path || ""
        )}&file_path=${encodeURIComponent(relPath)}`
      );
      state.gitDiffCache[relPath] = res.diff || "无文本变更";
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
    } catch (err) {
      container.innerHTML = `<div class="p-2 text-xs text-rose-400">加载 Diff 失败</div>`;
    }
  },
  async viewFullGitDiff() {
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    const bodyEl = document.getElementById("doc-preview-body");
    if (!bodyEl || !sess?.project_path) return;
    bodyEl.innerHTML = `<div class="p-4 text-xs text-sky-400">正在生成全量 Unified Diff...</div>`;
    try {
      const res = await apiFetch(
        `/api/v1/git/diff?project_path=${encodeURIComponent(sess.project_path)}`
      );
      state.currentDocRawContent = res.diff || "";
      const rawDiff = res.diff || "";
      const lines = rawDiff.split("\n");
      const isHuge = lines.length > 400;
      const displayDiff = isHuge && !state.expandHugeFullDiff ? lines.slice(0, 400).join("\n") : rawDiff;

      bodyEl.innerHTML = `
        <div class="space-y-2">
          <div class="flex items-center justify-between pb-2 border-b border-zinc-800">
            <span class="text-xs text-zinc-400">全局未提交修改 Diff ${
              res.is_truncated ? "（已截断展示前 48KB）" : ""
            }</span>
            <button onclick="window.appActions.openGitStatusModal()" class="text-xs text-sky-400 hover:underline">返回文件列表</button>
          </div>
          ${renderUnifiedDiffHtml(displayDiff || "无未提交代码改动")}
          ${
            isHuge && !state.expandHugeFullDiff
              ? `<div class="p-3 text-center bg-zinc-900/60 rounded-xl border border-zinc-800 text-xs text-zinc-400">
                  已折叠剩余 ${lines.length - 400} 行以保证移动端流畅度。
                  <button onclick="state.expandHugeFullDiff = true; window.appActions.viewFullGitDiff()" class="text-sky-400 underline font-medium ml-1">
                    仍要展示全部
                  </button>
                </div>`
              : ""
          }
        </div>
      `;
    } catch (e) {
      bodyEl.innerHTML = `<div class="p-4 text-xs text-rose-400">加载失败</div>`;
    }
  },
  openTokenUsageModal() {
    const modal = document.getElementById("token-usage-modal");
    if (!modal) return;
    const usage = state.currentTokenUsage || {};
    const ctx = usage.context_tokens || 0;
    const limit = usage.context_limit || 200000;
    const pct = typeof usage.context_percent === "number" ? usage.context_percent : Math.round((ctx / limit) * 1000) / 10;

    const pctEl = document.getElementById("modal-token-pct");
    const barEl = document.getElementById("modal-token-bar");
    const ctxEl = document.getElementById("modal-token-context");
    const limitEl = document.getElementById("modal-token-limit");
    const inEl = document.getElementById("modal-token-input");
    const outEl = document.getElementById("modal-token-output");
    const totEl = document.getElementById("modal-token-total");
    const cacheEl = document.getElementById("modal-token-cache");
    const tipEl = document.getElementById("modal-token-tip");

    if (pctEl) pctEl.textContent = `${pct}%`;
    if (barEl) {
      barEl.style.width = `${Math.min(pct, 100)}%`;
      barEl.className = pct >= 85 ? "h-full bg-rose-500 rounded-full" : pct >= 60 ? "h-full bg-amber-500 rounded-full" : "h-full bg-sky-500 rounded-full";
    }
    if (ctxEl) ctxEl.textContent = `${ctx.toLocaleString()} tokens`;
    if (limitEl) limitEl.textContent = `上限 ${limit.toLocaleString()} (${formatTokenCount(limit)})`;
    if (inEl) inEl.textContent = `${ctx.toLocaleString()}`;
    if (outEl) outEl.textContent = `${(usage.last_output_tokens || 0).toLocaleString()}`;
    if (totEl) totEl.textContent = `${(usage.total_tokens || 0).toLocaleString()}`;
    if (cacheEl) cacheEl.textContent = `${formatTokenCount(usage.cache_read_tokens || 0)}`;

    if (tipEl) {
      if (pct < 60) {
        tipEl.innerHTML = `<span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1"></span> <span class="text-zinc-200 font-medium">上下文健康</span>：模型注意力聚焦，多轮长代码与工具调用表现最佳。`;
      } else if (pct < 85) {
        tipEl.innerHTML = `<span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 mr-1"></span> <span class="text-amber-300 font-medium">上下文适中</span>：已产生较多上下文信息。若要开展全新大任务，可适时考虑开启新会话或输入 <span class="font-mono text-sky-400">/compact</span> 压缩。`;
      } else {
        tipEl.innerHTML = `<span class="inline-block w-1.5 h-1.5 rounded-full bg-rose-400 mr-1"></span> <span class="text-rose-300 font-medium">临近窗口上限</span>：剩余容量不足 15%！建议输入 <span class="font-mono text-sky-400">/compact</span> 总结上下文释放空间，或新建对话继续。`;
      }
    }

    modal.classList.remove("hidden");
  },
  closeTokenUsageModal() {
    const modal = document.getElementById("token-usage-modal");
    if (modal) modal.classList.add("hidden");
  },
  toggleModelPicker(e, forceOpen) {
    if (e) {
      if (typeof e.stopPropagation === "function") e.stopPropagation();
      if (typeof e.preventDefault === "function") e.preventDefault();
    }
    const popover = document.getElementById("model-picker-popover");
    const btn = document.getElementById("model-picker-btn");
    const arrow = document.getElementById("model-picker-arrow");
    if (!popover || !btn) return;
    const isHidden = popover.classList.contains("hidden");
    const open = forceOpen !== undefined ? Boolean(forceOpen) : isHidden;
    if (open) {
      renderModelPicker();
      const rect = btn.getBoundingClientRect();
      const popoverWidth = Math.min(340, window.innerWidth - 16);
      popover.style.width = `${popoverWidth}px`;
      popover.style.backgroundColor = "#09090b";
      // fixed 定位向上弹出：bottom 距离视口底部
      popover.style.bottom = `${Math.max(10, window.innerHeight - rect.top + 6)}px`;
      // 左侧对齐，并保持在屏幕左右视口安全边距内
      const maxLeft = window.innerWidth - popoverWidth - 8;
      const left = Math.max(8, Math.min(rect.left, maxLeft));
      popover.style.left = `${left}px`;

      popover.classList.remove("hidden");
      btn.classList.add("border-sky-500/50", "bg-zinc-800");
      if (arrow) arrow.classList.add("rotate-180");
    } else {
      popover.classList.add("hidden");
      btn.classList.remove("border-sky-500/50", "bg-zinc-800");
      if (arrow) arrow.classList.remove("rotate-180");
    }
  },
  async selectModel(modelId, e) {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    const popover = document.getElementById("model-picker-popover");
    const btn = document.getElementById("model-picker-btn");
    const arrow = document.getElementById("model-picker-arrow");
    if (popover) popover.classList.add("hidden");
    if (btn) btn.classList.remove("border-sky-500/50", "bg-zinc-800");
    if (arrow) arrow.classList.remove("rotate-180");

    const hiddenInput = document.getElementById("model-select");
    if (hiddenInput) hiddenInput.value = modelId;

    const sess =
      state.sessions.find((s) => s.unified_id === state.selectedSessionId) ||
      state.allSessions.find((s) => s.unified_id === state.selectedSessionId);
    if (sess) {
      sess.model_name = modelId;
    }
    renderModelPicker();

    if (!sess) return;
