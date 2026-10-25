/**
 * AgentDeck 会话选中、高亮切换与项目分组自展开
 */

import { state, markSessionViewed } from "./state.js";
import { updateSessionHash } from "./utils.js";
import { apiFetch } from "./api.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderApprovalBanners,
  renderOtherRunningPill,
  renderMessagesList,
  renderLiveStreamOverlay,
  updateGitStatusBadge,
  renderTokenUsage,
  renderModelOptionsForAgent,
} from "./render.js";
import { updateDrawerVisibility } from "./modals.js";
import { getProjectInfo } from "./session-helper.js";
import { loadSessionMessages } from "./data-messages.js";

export function updateSelectedSessionHighlight(prevId, nextId) {
  if (prevId) {
    document
      .querySelectorAll(`[data-session-item="${CSS.escape(prevId)}"]`)
      .forEach((el) => {
        el.className =
          "group cursor-pointer px-2.5 py-2 rounded-lg border transition-colors duration-150 flex items-center justify-between gap-2 bg-zinc-900/45 border-transparent hover:bg-zinc-900 hover:border-zinc-800 text-zinc-300";
      });
  }
  if (nextId) {
    document
      .querySelectorAll(`[data-session-item="${CSS.escape(nextId)}"]`)
      .forEach((el) => {
        el.className =
          "group cursor-pointer px-2.5 py-2 rounded-lg border transition-colors duration-150 flex items-center justify-between gap-2 bg-zinc-800/95 border-sky-500/50 text-zinc-100";
      });
  }
}

export async function selectSession(unifiedId) {
  const prevId = state.selectedSessionId;
  const wasMobileDrawerOpen = state.drawerOpen && window.innerWidth < 768;
  const wasUnread = state.unreadCompletedSessionIds.has(unifiedId);
  markSessionViewed(unifiedId);

  // 释放上一个会话的完成暂留锁定：上一个会话仅在用户主动切换离开时平滑归位到原项目
  let wasRetained = false;
  if (prevId && prevId !== unifiedId) {
    if (state.retainedActiveSessionIds && state.retainedActiveSessionIds.has(prevId)) {
      state.retainedActiveSessionIds.delete(prevId);
      wasRetained = true;
    }
  }

  state.selectedSessionId = unifiedId;
  try {
    localStorage.setItem("agentdeck_last_selected_session", unifiedId);
  } catch (_) {}
  if (prevId !== unifiedId) {
    state.codeBlockStore = {};
    state.expandedCodeBlocks = {};
  }
  updateSessionHash(unifiedId);
  state.drawerOpen = false;
  state.autoScrollLocked = false;

  // 自动展开当前选中会话所在的项目分组
  const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
  const prevSess = prevId ? state.allSessions.find((s) => s.unified_id === prevId) : null;
  let needFullSidebarRender = wasUnread || wasRetained;
  if (sess && sess.project_path) {
    const pKey = getProjectInfo(sess.project_path).key;
    if (pKey && !state.expandedProjects[pKey]) {
      state.expandedProjects[pKey] = true;
      needFullSidebarRender = true;
    }
  }

  updateDrawerVisibility();
  const sessionListContainer = document.getElementById("session-list-container");
  const isListEmpty = !sessionListContainer || sessionListContainer.children.length === 0;
  if (isListEmpty) {
    needFullSidebarRender = true;
  }
  // 收紧：同一已展开项目分组内切换且无未读/保留态时，仅更新高亮，避免全量重渲染
  if (!needFullSidebarRender && prevSess && sess && prevSess !== sess) {
    const prevPKey = prevSess.project_path ? getProjectInfo(prevSess.project_path).key : "";
    const currPKey = sess.project_path ? getProjectInfo(sess.project_path).key : "";
    if (prevPKey && currPKey && prevPKey === currPKey && state.expandedProjects[currPKey]) {
      needFullSidebarRender = false;
    }
  }
  if (needFullSidebarRender) {
    renderSessionList();
  } else {
    updateSelectedSessionHighlight(prevId, unifiedId);
  }

  // 消息优先：先更新头部骨架并准备消息区，非关键 UI 延迟渲染
  renderCurrentSessionHeader();

  const runDeferredUi = () => {
    renderApprovalBanners();
    renderOtherRunningPill();
    updateGitStatusBadge();
    renderLiveStreamOverlay();
  };
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(runDeferredUi);
  } else {
    setTimeout(runDeferredUi, 0);
  }

  // 非关键：延迟加载 workflows，避免阻塞首屏消息
  if (sess && sess.project_path) {
    const loadWorkflows = () => {
      apiFetch(`/api/v1/workflows?project_path=${encodeURIComponent(sess.project_path)}`)
        .then((res) => {
          if (res && res.ok && Array.isArray(res.workflows)) {
            state.workflows = res.workflows;
          }
        })
        .catch(() => {});
    };
    if (typeof requestIdleCallback === "function") {
      requestIdleCallback(loadWorkflows, { timeout: 1500 });
    } else if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => setTimeout(loadWorkflows, 0));
    } else {
      setTimeout(loadWorkflows, 0);
    }
  }

  const mountCachedOrLoad = async () => {
    const cached = state.sessionMessagesCache[unifiedId];
    if (state.optimisticMessages[unifiedId] && state.optimisticMessages[unifiedId].length > 0) {
      state.messages = [...state.optimisticMessages[unifiedId]];
      state.hasMoreMessages = false;
      state.totalMessagesCount = state.messages.length;
      state.currentTokenUsage = null;
      state.loadingMessages = false;
      renderMessagesList();
      renderLiveStreamOverlay();
      loadSessionMessages(unifiedId, { silent: true });
      return;
    }

    if (cached && Array.isArray(cached.messages) && cached.messages.length > 0) {
      state.messages = cached.messages;
      state.hasMoreMessages = Boolean(cached.hasMore);
      state.totalMessagesCount = cached.totalCount || cached.messages.length;
      state.currentTokenUsage = cached.tokenUsage || null;
      const modelSelect = document.getElementById("model-select");
      if (modelSelect && sess) {
        renderModelOptionsForAgent(modelSelect, sess.agent_type, sess.model_name || cached.modelName);
      }
      state.loadingMessages = false;
      renderMessagesList();
      loadSessionMessages(unifiedId, { silent: true });
    } else {
      state.currentTokenUsage = null;
      renderTokenUsage();
      state.loadingMessages = true;
      renderMessagesList();
      await loadSessionMessages(unifiedId, { silent: false, limit: 20 });
    }
  };

  if (wasMobileDrawerOpen) {
    setTimeout(mountCachedOrLoad, 210);
  } else {
    await mountCachedOrLoad();
  }
}
