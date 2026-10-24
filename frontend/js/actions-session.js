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
} from "./render.js";
import { openNewSessionModal } from "./modals.js";
import {
  renderModelOptionsForAgent,
  renderModeOptionsForAgent,
} from "./render-views.js";
import { handleTogglePin } from "./actions-approval.js";
import { dispatchCommand } from "./command-queue.js";

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
  try {
    await apiFetch("/api/v1/auth/refresh_pair_code", { method: "POST" });
    await loadSystemStatus();
    startQrExpireCountdown();
    showToast("配对码已刷新", "success");
  } catch (e) {
    showToast("刷新失败: " + e.message, "error");
  }
}

export function switchQrChannel(type) {
  state.selectedQrNet = type;
  renderQrModalContent();
}

export async function toggleAwakeLockFromSidebar() {
  const nextState = !state.systemStatus?.awake_lock_active;
  try {
    await apiFetch(`/api/v1/system/awake-lock?enable=${nextState}`, { method: "POST" });
    await loadSystemStatus();
    showToast(nextState ? "PC 防休眠锁已开启" : "PC 防休眠锁已关闭", "info");
  } catch (e) {
    showToast("切换防休眠失败: " + e.message, "error");
  }
}

export function setFilter(filter) {
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
  const curSess = state.sessions.find((s) => s.unified_id === unifiedId);
  if (!curSess) return;
  if (!window.confirm(`确定从列表中移除会话“${curSess.title}”？
仅移除本地记录，不删除源文件。`)) {
    return;
  }
  try {
    await apiFetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}`, { method: "DELETE" });
    showToast("已从列表移除", "success");
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
}
