/**
 * AgentDeck 用户交互动作调度总线（统一挂载 window.appActions）
 */

import { state } from "./state.js";
import { apiFetch, showToast } from "./api.js";
import {
  loadSessions,
  loadSessionMessages,
  loadOlderMessages,
  selectSession,
  pickDefaultSession,
  loadSystemStatus,
  handleStatusBtnClick,
} from "./data.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderMessagesList,
  renderQrModalContent,
  startQrExpireCountdown,
  highlightSlashItem,
  scrollToBottom,
} from "./render.js";
import {
  openNewSessionModal,
  confirmCreateNewSession,
  updateDrawerVisibility,
} from "./modals.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";
import { renderTurnProcessBar } from "./render-step-cards.js";

// 子动作控制器导入
import {
  toggleSessionMenu,
  toggleCurrentPin,
  renameCurrentSession,
  deleteCurrentSession,
  jumpToRunningSession,
  toggleProject,
  expandProjectSessions,
  collapseProjectSessions,
  resetProjectSessions,
  toggleSection,
  newProjectSession,
  hideProject,
  onNewSessionAgentChange,
  refreshPairCodeAndQr,
  switchQrChannel,
  toggleAwakeLockFromSidebar,
  setFilter,
  toggleProjectCollapse,
  toggleProjectHide,
  deleteSession,
} from "./actions-session.js";

import {
  handleSendPrompt,
  handleAbortCurrentSession,
  handleModeChange,
  handleModelChange,
  copyCodeBlock,
  copyThinkingText,
  loadFullOutput,
  insertSlashCommand,
  executeSlashDirectly,
  handlePromptAction,
  fillPromptAndSend,
  selectSlashCommand,
  toggleModelPicker,
  selectModel,
} from "./actions-chat.js";
import {
  submitApprovalDecision,
  startHoldToApprove,
  cancelHoldToApprove,
  handleTogglePin,
  toggleQuestionOption,
  onQuestionWriteInInput,
  submitQuestionAnswers,
  skipQuestionAnswers,
  toggleDenyFeedback,
  submitDenyWithFeedback,
  setQuestionWriteIn,
} from "./actions-approval.js";
import {
  closePairingGuideModal,
  submitPairCodeManual,
  closeQrModal,
  copyQrUrl,
  openFilePreview,
  closeDocModal,
  openDiffFullscreen,
  openDiffFullscreenById,
  copyCurrentDocContent,
  openTokenUsageModal,
  closeTokenUsageModal,
  openAgentsHealthModal,
  closeAgentsHealthModal,
  refreshAgentsHealth,
  openGitStatusModal,
  toggleGitFileDiff,
  viewFullGitDiff,
} from "./actions-modals.js";

export {
  handleSendPrompt,
  handleAbortCurrentSession,
  handleModeChange,
  handleModelChange,
  submitApprovalDecision,
  startHoldToApprove,
  cancelHoldToApprove,
  handleTogglePin,
};

window.appActions = {
  // 会话与三点操作菜单
  toggleSessionMenu,
  toggleCurrentPin,
  renameCurrentSession,
  deleteCurrentSession,
  jumpToRunningSession,
  toggleProject,
  expandProjectSessions,
  collapseProjectSessions,
  resetProjectSessions,
  toggleSection,
  newProjectSession,
  hideProject,
  onNewSessionAgentChange,
  refreshPairCodeAndQr,
  switchQrChannel,
  toggleAwakeLockFromSidebar,

  // 聊天与模型选择交互
  handlePromptAction,
  fillPromptAndSend,
  selectSlashCommand,
  toggleModelPicker,
  selectModel,

  // 审批与问答
  toggleDenyFeedback,
  submitDenyWithFeedback,
  setQuestionWriteIn,

  // 弹窗动作
  closePairingGuideModal,
  submitPairCodeManual,
  closeQrModal,
  copyQrUrl,
  openFilePreview,
  closeDocModal,
  openDiffFullscreen,
  openDiffFullscreenById,
  copyCurrentDocContent,
  openTokenUsageModal,
  closeTokenUsageModal,
  openAgentsHealthModal,
  closeAgentsHealthModal,
  refreshAgentsHealth,
  openGitStatusModal,
  toggleGitFileDiff,
  viewFullGitDiff,

  // 问答动作
  toggleQuestionOption,
  onQuestionWriteInInput,
  submitQuestionAnswers,
  skipQuestionAnswers,

  // 基础会话交互
  handleStatusBtnClick,
  selectSession,
  setFilter,
  toggleProjectCollapse,
  toggleProjectHide,
  deleteSession,
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
    loadSystemStatus().then(() => {
      renderQrModalContent();
    }).catch((e) => {
      console.warn("[AgentDeck] 后台更新配对状态提示:", e);
    });
  },
  openNewSessionModal: (prefill = "") => openNewSessionModal(prefill, ""),
  closeNewSessionModal() {
    document.getElementById("new-session-modal").classList.add("hidden");
  },
  confirmCreateNewSession,
  sendPrompt: () => handleSendPrompt(null),
  abortSession: handleAbortCurrentSession,
  changeMode: (el) => handleModeChange(el.value),
  changeModel: (el) => handleModelChange(el.value),
  submitApproval: submitApprovalDecision,
  startHold: startHoldToApprove,
  cancelHold: cancelHoldToApprove,
  togglePin: handleTogglePin,
  toggleWordWrap() {
    state.wordWrapDiff = !state.wordWrapDiff;
    document.querySelectorAll(".unified-diff-box").forEach((box) => {
      box.classList.toggle("word-wrap", state.wordWrapDiff);
    });
    document.querySelectorAll("[data-action=\"toggle-word-wrap\"]").forEach((btn) => {
      btn.textContent = state.wordWrapDiff ? "横向滚动" : "自动换行";
    });
  },
  toggleTurnEditsOnly(turnKey, ev) {
    if (ev) ev.stopPropagation();
    const isCurrentlyExpanded = !!state.expandedTurns[turnKey] || !!state.turnThinkingExpanded[turnKey];
    const isFilter = Boolean(state.turnFilterEdits[turnKey]);
    if (!isCurrentlyExpanded) {
      state.expandedTurns[turnKey] = true;
      state.turnFilterEdits[turnKey] = true;
    } else if (isFilter) {
      state.turnFilterEdits[turnKey] = false;
    } else {
      state.turnFilterEdits[turnKey] = true;
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
  toggleTurnSteps(turnKey) {
    const isCurrentlyExpanded = !!state.expandedTurns[turnKey] || !!state.turnThinkingExpanded[turnKey];
    const nextVal = !isCurrentlyExpanded;
    state.expandedTurns[turnKey] = nextVal;
    if (!nextVal) {
      state.turnThinkingExpanded[turnKey] = false;
      state.turnFilterEdits[turnKey] = false;
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
  copyCodeBlock,
  copyThinkingText,
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
  loadFullOutput,
  insertSlashCommand,
  executeSlashDirectly,
};
