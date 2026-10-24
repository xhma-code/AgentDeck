/**
 * AgentDeck 会话管理、项目管理、三点操作菜单与设备配对动作控制器
 */

import { state, clearAllUnreadCompletedSessions } from "./state.js";
import { DEFAULT_PROJECT_SESSION_LIMIT } from "./config.js";
import { apiFetch, showToast } from "./api.js";
import {
  loadSessions,
  loadWorkspaces,
  selectSession,
  pickDefaultSession,
  loadSystemStatus,
  getAllAttentionSessions,
} from "./data.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderMessagesList,
  renderQrModalContent,
  startQrExpireCountdown,
  renderOtherRunningPill,
  renderSidebarAwakeLock,
  renderAgentFilterTrigger,
  toggleAgentFilterMenu,
  closeAgentFilterMenu,
} from "./render.js";
import { openNewSessionModal } from "./modals.js";
import {
  renderModelOptionsForAgent,
  renderModeOptionsForAgent,
} from "./render-views.js";
import { handleTogglePin } from "./actions-approval.js";
import { dispatchCommand } from "./command-queue.js";

let sessionContextTargetId = null;
let sessionLongPressTimer = null;
let sessionLongPressStartX = 0;
let sessionLongPressStartY = 0;
let sessionLongPressMoved = false;

function disableTextSelection() {
  try {
    document.documentElement.style.webkitUserSelect = "none";
    document.documentElement.style.userSelect = "none";
    document.body.style.webkitUserSelect = "none";
    document.body.style.userSelect = "none";
    document.body.style.webkitTouchCallout = "none";
  } catch (e) {}
}

function enableTextSelection() {
  try {
    document.documentElement.style.webkitUserSelect = "";
    document.documentElement.style.userSelect = "";
    document.body.style.webkitUserSelect = "";
    document.body.style.userSelect = "";
    document.body.style.webkitTouchCallout = "";
  } catch (e) {}
}

export function toggleSessionMenu(ev) {
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
    if (text) text.textContent = isPinned ? "取消置顶" : "置顶";
    menu.classList.remove("hidden");
  } else {
    menu.classList.add("hidden");
  }
}

export function closeSessionContextMenu() {
  const menu = document.getElementById("session-context-menu");
  if (menu) menu.classList.add("hidden");
  enableTextSelection();
}

export function openSessionContextMenu(unifiedId, x, y, ev) {
  try {
    if (ev) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  } catch (e) {}
  disableTextSelection();
  sessionContextTargetId = unifiedId;
  const menu = document.getElementById("session-context-menu");
  if (!menu) return;

  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const target = allList.find((s) => s.unified_id === unifiedId);
  const isPinned = target ? !!target.is_pinned : false;
  const icon = document.getElementById("session-context-menu-pin-icon");
  const text = document.getElementById("session-context-menu-pin-text");
  if (icon) icon.className = isPinned ? "text-amber-400 text-xs" : "text-zinc-400 text-xs";
  if (text) text.textContent = isPinned ? "取消置顶" : "置顶";

  const menuWidth = 112;
  const menuHeight = 110;
  let left = x;
  let top = y;
  const padding = 8;
  if (left + menuWidth > window.innerWidth - padding) {
    left = window.innerWidth - menuWidth - padding;
  }
  if (top + menuHeight > window.innerHeight - padding) {
    top = window.innerHeight - menuHeight - padding;
  }
  if (left < padding) left = padding;
  if (top < padding) top = padding;

  menu.style.left = left + "px";
  menu.style.top = top + "px";
  menu.classList.remove("hidden");
}

export async function contextTogglePin() {
  closeSessionContextMenu();
  if (!sessionContextTargetId) return;
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const target = allList.find((s) => s.unified_id === sessionContextTargetId);
  if (!target) return;
  try {
    await handleTogglePin(target.unified_id, !!target.is_pinned);
  } catch (err) {
    console.error("切换置顶状态失败:", err);
    showToast("置顶操作失败", "error");
  }
}

export function contextRenameSession() {
  closeSessionContextMenu();
  if (!sessionContextTargetId) return;
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const target = allList.find((s) => s.unified_id === sessionContextTargetId);
  if (!target) return;
  const modal = document.getElementById("rename-session-modal");
  const input = document.getElementById("rename-session-input");
  if (modal && input) {
    input.value = target.title || "";
    modal.classList.remove("hidden");
    setTimeout(() => {
      input.focus();
      input.select();
    }, 50);
  } else {
    const newTitle = window.prompt("重命名：", target.title);
    if (newTitle && newTitle.trim()) {
      confirmRenameSessionWithTitleForId(target.unified_id, newTitle.trim());
    }
  }
}

export async function confirmRenameSessionWithTitleForId(unifiedId, trimmed) {
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const target = allList.find((s) => s.unified_id === unifiedId);
  if (!target || trimmed === target.title) return;
  const prevTitle = target.title;
  try {
    target.title = trimmed;
    if (unifiedId === state.selectedSessionId) {
      renderCurrentSessionHeader();
    }
    renderSessionList();
    await dispatchCommand({
      command_type: "RENAME_SESSION",
      session_id: target.unified_id,
      payload: { title: trimmed },
    });
    showToast("已重命名", "success");
  } catch (err) {
    if (target) target.title = prevTitle;
    renderCurrentSessionHeader();
    renderSessionList();
    showToast(`重命名失败: ${err.message}`, "error");
    loadSessions();
  }
}

export async function contextDeleteSession() {
  closeSessionContextMenu();
  if (!sessionContextTargetId) return;
  await deleteSession(sessionContextTargetId);
}

export function handleSessionTouchStart(ev, unifiedId) {
  if (ev.touches.length !== 1) return;
  const touch = ev.touches[0];
  sessionLongPressStartX = touch.clientX;
  sessionLongPressStartY = touch.clientY;
  sessionLongPressMoved = false;
  if (sessionLongPressTimer) {
    clearTimeout(sessionLongPressTimer);
    sessionLongPressTimer = null;
  }
  try {
    ev.stopPropagation();
  } catch (e) {}
  disableTextSelection();
  sessionLongPressTimer = setTimeout(() => {
    if (sessionLongPressMoved) return;
    const menu = document.getElementById("session-context-menu");
    if (menu && !menu.classList.contains("hidden")) {
      closeSessionContextMenu();
      return;
    }
    try {
      if (ev.cancelable) {
        ev.preventDefault();
      }
      ev.stopPropagation();
    } catch (e) {}
    openSessionContextMenu(unifiedId, sessionLongPressStartX, sessionLongPressStartY, ev);
    if (navigator.vibrate) {
      try { navigator.vibrate(15); } catch (e) {}
    }
  }, 800);
}

export function handleSessionTouchMove(ev) {
  if (ev.touches.length !== 1) return;
  const touch = ev.touches[0];
  const dx = Math.abs(touch.clientX - sessionLongPressStartX);
  const dy = Math.abs(touch.clientY - sessionLongPressStartY);
  if (dx > 16 || dy > 16) {
    sessionLongPressMoved = true;
    if (sessionLongPressTimer) {
      clearTimeout(sessionLongPressTimer);
      sessionLongPressTimer = null;
    }
  }
}

export function handleSessionTouchEnd(ev) {
  if (sessionLongPressTimer) {
    clearTimeout(sessionLongPressTimer);
    sessionLongPressTimer = null;
  }
  sessionLongPressMoved = false;
  enableTextSelection();
}

export function handleSessionTouchCancel(ev) {
  if (sessionLongPressTimer) {
    clearTimeout(sessionLongPressTimer);
    sessionLongPressTimer = null;
  }
  sessionLongPressMoved = false;
  enableTextSelection();
}

export async function toggleCurrentPin() {
  const menu = document.getElementById("session-action-menu");
  if (menu) menu.classList.add("hidden");
  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!curSess) return;
  await handleTogglePin(curSess.unified_id, !!curSess.is_pinned);
}

export function renameCurrentSession() {
  const menu = document.getElementById("session-action-menu");
  if (menu) menu.classList.add("hidden");
  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!curSess) return;
  const modal = document.getElementById("rename-session-modal");
  const input = document.getElementById("rename-session-input");
  if (modal && input) {
    input.value = curSess.title || "";
    modal.classList.remove("hidden");
    setTimeout(() => {
      input.focus();
      input.select();
    }, 50);
  } else {
    const newTitle = window.prompt("重命名：", curSess.title);
    if (newTitle && newTitle.trim()) {
      confirmRenameSessionWithTitle(newTitle.trim());
    }
  }
}

export function closeRenameModal() {
  const modal = document.getElementById("rename-session-modal");
  if (modal) modal.classList.add("hidden");
}

export async function confirmRenameSession() {
  const input = document.getElementById("rename-session-input");
  const newTitle = input ? input.value.trim() : "";
  closeRenameModal();
  if (!newTitle) return;
  await confirmRenameSessionWithTitle(newTitle);
}

export async function confirmRenameSessionWithTitle(trimmed) {
  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!curSess || trimmed === curSess.title) return;
  try {
    curSess.title = trimmed;
    renderCurrentSessionHeader();
    renderSessionList();
    await dispatchCommand({
      command_type: "RENAME_SESSION",
      session_id: curSess.unified_id,
      payload: { title: trimmed },
    });
    showToast("已重命名", "success");
  } catch (err) {
    showToast(`重命名失败: ${err.message}`, "error");
    loadSessions();
  }
}

export async function deleteCurrentSession() {
  const menu = document.getElementById("session-action-menu");
  if (menu) menu.classList.add("hidden");
  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!curSess) return;
  if (!window.confirm(`确定删除「${curSess.title}」？此操作不可逆。`)) return;
  try {
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
    await dispatchCommand({
      command_type: "DELETE_SESSION",
      session_id: curSess.unified_id,
      payload: {},
    });
    showToast("已删除", "success");
  } catch (err) {
    showToast(`删除失败: ${err.message}`, "error");
    loadSessions();
  }
}

export function relayToNewAgentSession() {
  const menu = document.getElementById("session-action-menu");
  if (menu) menu.classList.add("hidden");
  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!curSess) return;

  const lastMsg = state.messages.length > 0 ? state.messages[state.messages.length - 1] : null;
  let prefill = "";
  if (lastMsg && lastMsg.content) {
    const rawExcerpt = lastMsg.content.slice(0, 180).replace(/\s+/g, " ");
    prefill = `[接续自 ${curSess.agent_type || "前序会话"}] 关于「${curSess.title}」的后续推进：\n`;
  }
  openNewSessionModal(prefill, curSess.project_path || "");
}

export function jumpToRunningSession() {
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
}

export async function markAllSessionsViewed(ev) {
  if (ev) ev.stopPropagation();
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const allIds = allList.map((s) => s.unified_id).filter(Boolean);
  clearAllUnreadCompletedSessions(new Date().toISOString());
  renderSessionList();
  renderOtherRunningPill();
  showToast("已将全部会话标为已读", "info");

  try {
    await apiFetch("/api/v1/sessions/mark-all-viewed", {
      method: "POST",
      body: JSON.stringify({ session_ids: allIds.length > 0 ? allIds : undefined }),
    });
  } catch (err) {
    console.warn("上报全部已读失败:", err);
  }
}

export function toggleProject(projectKey) {
  state.expandedProjects[projectKey] = !state.expandedProjects[projectKey];
  renderSessionList();
}

function ensureProjectSessionLimits() {
  if (!state.projectSessionLimits) state.projectSessionLimits = {};
  return state.projectSessionLimits;
}

export function expandProjectSessions(projectKey, step = DEFAULT_PROJECT_SESSION_LIMIT, ev) {
  if (ev) ev.stopPropagation();
  const limits = ensureProjectSessionLimits();
  const current = limits[projectKey] || DEFAULT_PROJECT_SESSION_LIMIT;
  limits[projectKey] = current + step;
  renderSessionList();
}

export function collapseProjectSessions(projectKey, ev) {
  if (ev) ev.stopPropagation();
  const limits = ensureProjectSessionLimits();
  limits[projectKey] = DEFAULT_PROJECT_SESSION_LIMIT;
  renderSessionList();
}

// 兼容别名
export const resetProjectSessions = collapseProjectSessions;

export function toggleSection(sectionKey) {
  if (!state.collapsedSections) state.collapsedSections = {};
  state.collapsedSections[sectionKey] = !state.collapsedSections[sectionKey];
  try {
    localStorage.setItem("agentdeck_sections_collapsed", JSON.stringify(state.collapsedSections));
  } catch (e) {}
  renderSessionList();
}

export function newProjectSession(projectPath, ev) {
  if (ev) ev.stopPropagation();
  openNewSessionModal("", projectPath);
}

export async function hideProject(projectPath, ev, projectName = "") {
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
}

export function onNewSessionAgentChange(agentType) {
  const modelSelect = document.getElementById("new-session-model");
  renderModelOptionsForAgent(modelSelect, agentType, "");
  const modeSelect = document.getElementById("new-session-mode");
  renderModeOptionsForAgent(modeSelect, agentType, modeSelect ? modeSelect.value : "ask", false);
}

export async function refreshPairCodeAndQr() {
  if (state.isRefreshingQr) return;
  state.isRefreshingQr = true;
  renderQrModalContent();
  try {
    await apiFetch("/api/v1/auth/refresh_pair_code", { method: "POST" });
    await loadSystemStatus();
    startQrExpireCountdown();
    renderQrModalContent();
    showToast("配对码已刷新", "success");
  } catch (e) {
    showToast("刷新失败: " + e.message, "error");
  } finally {
    state.isRefreshingQr = false;
    renderQrModalContent();
  }
}

export function switchQrChannel(type) {
  state.selectedQrNet = type;
  renderQrModalContent();
}

export { toggleAgentFilterMenu, closeAgentFilterMenu };

export function selectAgentFilter(filter, ev) {
  if (ev) ev.stopPropagation();
  setFilter(filter);
}

export async function toggleAwakeLockFromSidebar() {
  const nextState = !state.systemStatus?.awake_lock_active;
  if (!state.systemStatus) state.systemStatus = {};
  state.systemStatus.awake_lock_active = nextState;
  renderSidebarAwakeLock();

  try {
    await apiFetch(`/api/v1/system/awake-lock?enable=${nextState}`, { method: "POST" });
    await loadSystemStatus();
    showToast(nextState ? "PC 防休眠锁已开启" : "PC 防休眠锁已关闭", "info");
  } catch (e) {
    state.systemStatus.awake_lock_active = !nextState;
    renderSidebarAwakeLock();
    showToast("切换防休眠失败: " + e.message, "error");
  }
}

export function setFilter(filter) {
  state.activeFilter = filter;
  closeAgentFilterMenu();
  renderAgentFilterTrigger();
  renderSessionList();
}

export function toggleProjectCollapse(projectKey) {
  state.expandedProjects[projectKey] = !state.expandedProjects[projectKey];
  renderSessionList();
}

export async function toggleProjectHide(projectPath, currentHidden, ev) {
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
}

export async function deleteSession(unifiedId, ev) {
  if (ev) ev.stopPropagation();
  const curSess = state.sessions.find((s) => s.unified_id === unifiedId) || (state.allSessions || []).find((s) => s.unified_id === unifiedId);
  if (!curSess) return;
  if (!window.confirm(`确定从列表中移除会话“${curSess.title}”？
仅移除本地记录，不删除源文件。`)) {
    return;
  }
  try {
    await apiFetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}`, { method: "DELETE" });
    showToast("已从列表移除", "success");
    state.sessions = state.sessions.filter((s) => s.unified_id !== curSess.unified_id);
    state.allSessions = (state.allSessions || []).filter((s) => s.unified_id !== curSess.unified_id);
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
}

if (typeof window !== "undefined") {
  const blockSessionItemDefault = (ev) => {
    const target = ev.target;
    if (!target) return;
    const el = target.closest ? target.closest("[data-session-item]") : null;
    if (!el) return;
    try {
      ev.preventDefault();
      ev.stopPropagation();
    } catch (e) {}
    return false;
  };

  window.addEventListener("contextmenu", blockSessionItemDefault, { capture: true, passive: false });
  window.addEventListener("selectstart", blockSessionItemDefault, { capture: true, passive: false });
  window.addEventListener("dragstart", blockSessionItemDefault, { capture: true, passive: false });
  document.addEventListener("gesturestart", blockSessionItemDefault, { capture: true, passive: false });
  document.addEventListener("gesturechange", blockSessionItemDefault, { capture: true, passive: false });
  document.addEventListener("gestureend", blockSessionItemDefault, { capture: true, passive: false });

  const container = document.getElementById("session-list-container");
  let globalPressTimer = null;
  let globalPressStartX = 0;
  let globalPressStartY = 0;
  let globalPressedId = null;
  let globalMoved = false;

  const clearGlobalPress = () => {
    if (globalPressTimer) {
      clearTimeout(globalPressTimer);
      globalPressTimer = null;
    }
    globalPressedId = null;
    globalMoved = false;
    enableTextSelection();
  };

  document.addEventListener("touchstart", (ev) => {
    if (ev.touches.length !== 1) return;
    const touch = ev.touches[0];
    const target = ev.target;
    if (!target) return;
    const el = target.closest ? target.closest("[data-session-item]") : null;
    if (!el) return;
    globalPressedId = el.getAttribute("data-session-id") || el.getAttribute("data-session-item");
    if (!globalPressedId) return;
    globalPressStartX = touch.clientX;
    globalPressStartY = touch.clientY;
    globalMoved = false;
    if (globalPressTimer) {
      clearTimeout(globalPressTimer);
      globalPressTimer = null;
    }
    try {
      ev.stopPropagation();
    } catch (e) {}
    disableTextSelection();
    globalPressTimer = setTimeout(() => {
      if (globalMoved || !globalPressedId) return;
      const menu = document.getElementById("session-context-menu");
      if (menu && !menu.classList.contains("hidden")) {
        closeSessionContextMenu();
        return;
      }
      try {
        if (ev.cancelable) {
          ev.preventDefault();
        }
        ev.stopPropagation();
      } catch (e) {}
      openSessionContextMenu(globalPressedId, globalPressStartX, globalPressStartY, ev);
      if (navigator.vibrate) {
        try {
          navigator.vibrate(15);
        } catch (e) {}
      }
      globalPressedId = null;
      if (globalPressTimer) {
        clearTimeout(globalPressTimer);
        globalPressTimer = null;
      }
    }, 650);
  }, { passive: false, capture: true });

  document.addEventListener("touchmove", (ev) => {
    if (ev.touches.length !== 1) return;
    if (!globalPressedId) return;
    const touch = ev.touches[0];
    const dx = Math.abs(touch.clientX - globalPressStartX);
    const dy = Math.abs(touch.clientY - globalPressStartY);
    if (dx > 12 || dy > 12) {
      globalMoved = true;
      if (globalPressTimer) {
        clearTimeout(globalPressTimer);
        globalPressTimer = null;
      }
    }
  }, { passive: true, capture: true });

  document.addEventListener("touchend", clearGlobalPress, { passive: true, capture: true });
  document.addEventListener("touchcancel", clearGlobalPress, { passive: true, capture: true });

  document.addEventListener("click", (ev) => {
    const menu = document.getElementById("session-context-menu");
    if (!menu || menu.classList.contains("hidden")) return;
    if (menu.contains(ev.target)) return;
    closeSessionContextMenu();
  });
  document.addEventListener("touchstart", (ev) => {
    const menu = document.getElementById("session-context-menu");
    if (!menu || menu.classList.contains("hidden")) return;
    if (menu.contains(ev.target)) return;
    closeSessionContextMenu();
  }, { passive: true });
}
