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
  renderSlashMenu,
  highlightSlashItem,
  scrollToBottom,
} from "./render.js";
import {
  openNewSessionModal,
  confirmCreateNewSession,
  updateDrawerVisibility,
} from "./modals.js";
import {
  escapeHtml,
  copyTextToClipboard,
  applyButtonCheckmark,
} from "./utils.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";
import { renderTurnProcessBar } from "./render-step-cards.js";

// 子动作控制器导入
import {
  handleSendPrompt,
  handleAbortCurrentSession,
  handleModeChange,
  handleModelChange,
} from "./actions-chat.js";
import {
  submitApprovalDecision,
  startHoldToApprove,
  cancelHoldToApprove,
  handleTogglePin,
  questionActions,
} from "./actions-approval.js";
import { modalActions } from "./actions-modals.js";

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
  // 混入弹窗与问答动作集
  ...modalActions,
  ...questionActions,

  handleStatusBtnClick,
  selectSession,
  setFilter(filter) {
    state.activeFilter = filter;
    document.querySelectorAll("[data-agent-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-agent-tab");
      if (tab === filter) {
        btn.className = "px-2.5 py-1.5 sm:py-1 rounded-lg bg-sky-500 text-zinc-950 font-semibold text-[11px] shrink-0 transition active:scale-95";
      } else {
        btn.className = "px-2.5 py-1.5 sm:py-1 rounded-lg bg-zinc-900 text-zinc-300 hover:bg-zinc-800 text-[11px] shrink-0 transition active:scale-95";
      }
    });
    renderSessionList();
  },
  toggleProjectCollapse(projectKey) {
    state.expandedProjects[projectKey] = !state.expandedProjects[projectKey];
    renderSessionList();
  },
  async toggleProjectHide(projectPath, currentHidden, ev) {
    if (ev) ev.stopPropagation();
    try {
      await apiFetch("/api/v1/workspaces/hide", {
        method: "POST",
        body: JSON.stringify({
          workspace_path: projectPath,
          is_hidden: currentHidden ? 0 : 1,
        }),
      });
      await loadSessions();
      renderSessionList();
    } catch (err) {
      showToast(`操作失败: ${err.message}`, "error");
    }
  },
  async deleteSession(unifiedId, ev) {
    if (ev) ev.stopPropagation();
    const curSess = state.sessions.find((s) => s.unified_id === unifiedId);
    if (!curSess) return;
    if (!window.confirm(`确定要从列表中移除对话「${curSess.title}」吗？\n（仅隐藏记录，不会删除磁盘代码）`)) {
      return;
    }
    try {
      await apiFetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}`, { method: "DELETE" });
      showToast("已移除对话", "success");
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
    state.autoScrollLocked = true;
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
  async copyCodeBlock(codeId, btnEl) {
    const pre = document.getElementById(`code-pre-${codeId}`);
    if (!pre) return;
    const text = pre.innerText || pre.textContent || "";
    const ok = await copyTextToClipboard(text);
    if (ok) {
      if (btnEl) applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
      showToast("代码已复制", "success");
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
      if (btnEl) applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
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
      renderMessagesList();
    } catch (_) {
      if (btnEl) btnEl.textContent = "重试加载";
    }
  },
  insertSlashCommand(cmd) {
    const inputEl = document.getElementById("prompt-input");
    if (!inputEl) return;
    inputEl.value = `${cmd} `;
    inputEl.focus();
    state.slashMenuOpen = false;
    renderSlashMenu();
  },
  executeSlashDirectly(cmd) {
    state.slashMenuOpen = false;
    renderSlashMenu();
    handleSendPrompt(cmd);
  },
};
