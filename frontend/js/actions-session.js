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
let renamingSessionId = null;
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

  menu.classList.remove("hidden");
  const menuWidth = menu.offsetWidth || 112;
  const menuHeight = menu.offsetHeight || 110;
  const padding = 8;

  // 获取被长按会话卡片的 DOM 节点，使弹框固定靠右侧对齐
  let itemEl = null;
  if (ev && ev.target && ev.target.closest) {
    itemEl = ev.target.closest("[data-session-item]");
  }
  if (!itemEl && unifiedId) {
    const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(unifiedId) : unifiedId.replace(/["\\]/g, "\\$&");
    itemEl = document.querySelector(`[data-session-item="${escaped}"]`);
  }

  let left, top;
  if (itemEl) {
    const rect = itemEl.getBoundingClientRect();
    // 固定在会话卡片靠右侧（留出边距）
    left = rect.right - menuWidth - padding;
    top = rect.top + 4;
  } else {
    // 兜底：若未直接命中卡片则固定在抽屉或屏幕靠右侧
    const drawer = document.getElementById("sidebar-drawer");
    if (drawer) {
      const dRect = drawer.getBoundingClientRect();
      left = dRect.right - menuWidth - padding;
    } else {
      left = window.innerWidth - menuWidth - padding;
    }
    top = (y || 100) - 10;
  }

  // 视口安全边界检测
  if (left + menuWidth > window.innerWidth - padding) {
    left = window.innerWidth - menuWidth - padding;
  }
  if (left < padding) left = padding;

  if (top + menuHeight > window.innerHeight - padding) {
    top = window.innerHeight - menuHeight - padding;
  }
  if (top < padding) top = padding;

  menu.style.left = left + "px";
  menu.style.top = top + "px";
}

export async function contextTogglePin() {
  const targetId = sessionContextTargetId;
  closeSessionContextMenu();
  if (!targetId) return;
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const target = allList.find((s) => s.unified_id === targetId);
  if (!target) return;
  try {
    await handleTogglePin(target.unified_id, !!target.is_pinned);
  } catch (err) {
    console.error("切换置顶状态失败:", err);
    showToast("置顶操作失败", "error");
  }
}

export function contextRenameSession() {
  const targetId = sessionContextTargetId;
  closeSessionContextMenu();
  if (!targetId) return;
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const target = allList.find((s) => s.unified_id === targetId);
  if (!target) return;
  renamingSessionId = target.unified_id;
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
  const targetId = sessionContextTargetId;
  closeSessionContextMenu();
  if (!targetId) return;
  await deleteSession(targetId);
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
  renamingSessionId = curSess.unified_id;
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
      confirmRenameSessionWithTitleForId(curSess.unified_id, newTitle.trim());
    }
  }
}

export function closeRenameModal() {
  const modal = document.getElementById("rename-session-modal");
  if (modal) modal.classList.add("hidden");
  renamingSessionId = null;
}

export async function confirmRenameSession() {
  const input = document.getElementById("rename-session-input");
  const newTitle = input ? input.value.trim() : "";
  const targetId = renamingSessionId || state.selectedSessionId;
  closeRenameModal();
  if (!newTitle || !targetId) return;
  await confirmRenameSessionWithTitleForId(targetId, newTitle);
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
  if (!state.selectedSessionId) return;
  await deleteSession(state.selectedSessionId);
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
  renderModeOptionsForAgent(modeSelect, agentType, modeSelect ? modeSelect.value : "auto", false);
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
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const curSess = allList.find((s) => s.unified_id === unifiedId);
  if (!curSess) return;
  if (!window.confirm(`确定删除「${curSess.title}」？此操作不可逆。`)) {
    return;
  }
  try {
    await apiFetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}`, { method: "DELETE" });
    showToast("已删除", "success");
    state.sessions = state.sessions.filter((s) => s.unified_id !== curSess.unified_id);
    state.allSessions = (state.allSessions || []).filter((s) => s.unified_id !== curSess.unified_id);
    if (state.selectedSessionId === curSess.unified_id) {
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
    } else {
      renderSessionList();
    }
  } catch (err) {
    showToast(`删除失败: ${err.message}`, "error");
  }
}

if (typeof window !== "undefined") {
  let longPressTimer = null;
  let startX = 0;
  let startY = 0;
  let longPressTargetId = null;
  let isMoved = false;
  let lastLongPressTime = 0;

  const cancelLongPress = () => {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
    longPressTargetId = null;
    isMoved = false;
    enableTextSelection();
  };

  // 1. 触摸长按手势识别（针对左侧抽屉菜单与会话列表项）
  document.addEventListener("touchstart", (ev) => {
    // 菜单已打开时，若点击在菜单之外，关闭菜单
    const menu = document.getElementById("session-context-menu");
    if (menu && !menu.classList.contains("hidden")) {
      if (!menu.contains(ev.target)) {
        closeSessionContextMenu();
      }
    }

    if (ev.touches.length !== 1) {
      cancelLongPress();
      return;
    }

    const touch = ev.touches[0];
    const target = ev.target;
    if (!target) return;

    // 若点击的是卡片内的独立功能按钮（如置顶星标按钮），不触发会话长按
    if (target.closest("button") || target.closest("a")) {
      cancelLongPress();
      return;
    }

    const itemEl = target.closest ? target.closest("[data-session-item]") : null;
    if (!itemEl) {
      cancelLongPress();
      return;
    }

    const unifiedId = itemEl.getAttribute("data-session-id") || itemEl.getAttribute("data-session-item");
    if (!unifiedId) {
      cancelLongPress();
      return;
    }

    startX = touch.clientX;
    startY = touch.clientY;
    longPressTargetId = unifiedId;
    isMoved = false;

    if (longPressTimer) {
      clearTimeout(longPressTimer);
    }

    disableTextSelection();

    // 500ms 长按判定时间
    longPressTimer = setTimeout(() => {
      if (isMoved || !longPressTargetId) return;

      lastLongPressTime = Date.now();
      if (navigator.vibrate) {
        try { navigator.vibrate(20); } catch (e) {}
      }

      openSessionContextMenu(longPressTargetId, startX, startY, ev);
      longPressTargetId = null;
      longPressTimer = null;
    }, 500);
  }, { passive: false, capture: true });

  // 2. 触摸移动与滚动时取消长按判定（防止误触）
  document.addEventListener("touchmove", (ev) => {
    if (!longPressTimer || ev.touches.length !== 1) return;
    const touch = ev.touches[0];
    const dx = Math.abs(touch.clientX - startX);
    const dy = Math.abs(touch.clientY - startY);
    if (dx > 12 || dy > 12) {
      isMoved = true;
      cancelLongPress();
    }
  }, { passive: true, capture: true });

  document.addEventListener("touchend", () => {
    if (longPressTimer) {
      cancelLongPress();
    }
  }, { passive: true, capture: true });

  document.addEventListener("touchcancel", cancelLongPress, { passive: true, capture: true });
  window.addEventListener("scroll", cancelLongPress, { passive: true, capture: true });

  // 3. 核心拦截：阻止长按抬手后浏览器派发的合成 click 事件穿透触发 selectSession 和关闭菜单
  window.addEventListener("click", (ev) => {
    if (Date.now() - lastLongPressTime < 450) {
      ev.preventDefault();
      ev.stopPropagation();
      ev.stopImmediatePropagation();
      return false;
    }

    // 普通点击：若菜单处于显示状态且点击在菜单外部，平滑关闭菜单
    const menu = document.getElementById("session-context-menu");
    if (menu && !menu.classList.contains("hidden")) {
      if (!menu.contains(ev.target)) {
        closeSessionContextMenu();
      }
    }
  }, { capture: true, passive: false });

  // 4. 支持右键菜单（支持桌面/鼠标或长按触发系统右键）
  window.addEventListener("contextmenu", (ev) => {
    const target = ev.target;
    if (!target) return;
    const itemEl = target.closest ? target.closest("[data-session-item]") : null;
    if (itemEl) {
      try {
        ev.preventDefault();
        ev.stopPropagation();
      } catch (e) {}
      const unifiedId = itemEl.getAttribute("data-session-id") || itemEl.getAttribute("data-session-item");
      if (unifiedId) {
        lastLongPressTime = Date.now();
        openSessionContextMenu(unifiedId, ev.clientX, ev.clientY, ev);
      }
      return false;
    }
  }, { capture: true, passive: false });
}
