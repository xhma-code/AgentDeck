/**
 * AgentDeck 数据加载、会话调度与版本感知
 */

import {
  MODEL_CONTEXT_LIMITS,
  AGENT_META,
} from "./config.js";
import {
  state,
  markSessionViewed,
  isSessionCompletedUnread,
  syncUnreadSessions,
} from "./state.js";
import {
  cleanFriendlyText,
  getSessionIdFromHash,
  updateSessionHash,
  computeSessionsSig,
  computeMessagesSig,
} from "./utils.js";
import { apiFetch, showToast, closePairingGuideModal } from "./api.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderApprovalBanners,
  renderOtherRunningPill,
  renderHeaderStatus,
  renderSidebarStatus,
  renderSidebarAwakeLock,
  renderQrModalContent,
  renderTokenUsage,
  renderMessagesList,
  renderLiveStreamOverlay,
  renderModelOptionsForAgent,
  updateGitStatusBadge,
} from "./render.js";
import { updateDrawerVisibility } from "./modals.js";
import { scheduleDeferredSessionListRender } from "./network.js";

import {
  cleanFriendlyTitle,
  isSessionStatusActive,
  SESSION_PRIORITY,
  getSessionPriorityWeight,
  isSessionAttentionNeeded,
  getAllAttentionSessions,
  getPendingAttentionSessions,
  getLatestSession,
  pickDefaultSession,
  getProjectInfo,
  getModelContextLimit,
  getAgentDisplayName,
  isSessionWorking,
} from "./session-helper.js";

export {
  cleanFriendlyTitle,
  isSessionStatusActive,
  SESSION_PRIORITY,
  getSessionPriorityWeight,
  isSessionAttentionNeeded,
  getAllAttentionSessions,
  getPendingAttentionSessions,
  getLatestSession,
  pickDefaultSession,
  getProjectInfo,
  getModelContextLimit,
  getAgentDisplayName,
  isSessionWorking,
};

export async function loadSystemStatus() {
  try {
    const data = await apiFetch("/api/v1/system/status");
    state.systemStatus = data;
    // PWA 主屏幕独立沙盒无感自愈：若服务端基于受信任已配对 VPN IP 下发了有效 token，立即补齐并入库
    if (data.trusted_client_token) {
      state.token = data.trusted_client_token;
      try {
        localStorage.setItem("agentdeck_token", data.trusted_client_token);
      } catch (_) {}
      closePairingGuideModal();
      console.info("[AgentDeck] PWA 独立沙盒已通过已配对 VPN IP 静默同步授权 Token");
    }
    if (Array.isArray(data.channels) && data.channels.length > 0) {
      try {
        localStorage.setItem("agentdeck_channels", JSON.stringify(data.channels));
      } catch (_) {}
    }
    if (data.models_by_agent) {
      state.modelsByAgent = data.models_by_agent;
    }
    if (data.workflows) {
      state.workflows = data.workflows;
    }
    if (data.snapshot && data.snapshot.pending_approvals) {
      state.pendingApprovals = data.snapshot.pending_approvals;
    }
    renderHeaderStatus();
    renderSidebarAwakeLock();
    renderApprovalBanners();
    renderQrModalContent();
    renderCurrentSessionHeader();
    updateGitStatusBadge();
  } catch (e) {
    console.error("获取系统状态失败:", e);
  }
}

export async function loadWorkspaces() {
  try {
    const data = await apiFetch("/api/v1/workspaces");
    state.workspaces = data.workspaces || [];
  } catch (_) {}
}

export async function loadSessions(silent = false) {
  try {
    const headers = {};
    if (state.sessionsETag) {
      headers["If-None-Match"] = state.sessionsETag;
    }
    // 始终拉取全量会话（limit=60），前端切换 Agent 标签时 0ms 纯内存过滤（支持 ETag 304 协商缓存）
    const data = await apiFetch(`/api/v1/sessions?agent_type=all&limit=60`, { headers });
    if (data && data.not_modified) {
      return;
    }
    if (data && data._etag) {
      state.sessionsETag = data._etag;
    }
    const incomingSessions = data.sessions || [];
    for (const s of incomingSessions) {
      if (s) {
        s.title = cleanFriendlyTitle(s.title);
        s.preview = cleanFriendlyText(s.preview);
      }
    }
    const incomingApprovals = data.pending_approvals || state.pendingApprovals || [];
    const newSig = computeSessionsSig(incomingSessions, incomingApprovals);

    // 若当前已选中的会话不在新返回的 top60 内（如深度历史会话），保活其引用，防止因分页截断导致当前会话被误判失效
    if (
      state.selectedSessionId &&
      !incomingSessions.some((s) => s.unified_id === state.selectedSessionId)
    ) {
      const prevSess =
        state.allSessions.find((s) => s.unified_id === state.selectedSessionId) ||
        state.sessions.find((s) => s.unified_id === state.selectedSessionId);
      if (prevSess) {
        incomingSessions.push(prevSess);
      }
    }

    state.allSessions = incomingSessions;
    state.sessions = incomingSessions;
    if (data.pending_approvals) {
      state.pendingApprovals = data.pending_approvals;
    }

    const isFirstMount = !state.isAppInitialized;
    let targetSession = null;
    let needsInitialSelect = false;

    if (isFirstMount) {
      // 仅在首屏冷启动挂载（Initial Mount）时执行一次首选会话决策，后续严禁任何后台事件抢占会话
      const hashId = getSessionIdFromHash();
      const hashSess = hashId ? state.allSessions.find((s) => s.unified_id === hashId) : null;

      if (hashSess) {
        // 场景 A：URL Hash 显式指定，优先恢复（保持操作与阅读连续性）
        targetSession = hashSess;
      } else if (state.allSessions.length > 0) {
        // 场景 B：纯冷启动无 Hash：优先运行中（running / desktop_active） > 收藏置顶 > 最近活跃
        targetSession = pickDefaultSession(state.allSessions);
      }

      if (targetSession) {
        needsInitialSelect = true;
        state.selectedSessionId = targetSession.unified_id;
        if (targetSession.project_path) {
          const pKey = getProjectInfo(targetSession.project_path)?.key;
          if (pKey) {
            state.expandedProjects[pKey] = true;
          }
        }
      }

      // 首屏自动展开包含运行中 / 进行中任务的项目分组
      for (const s of state.allSessions) {
        if ((isSessionStatusActive(s) || s.status === "waiting_approval") && s.project_path) {
          const pKey = getProjectInfo(s.project_path)?.key;
          if (pKey) {
            state.expandedProjects[pKey] = true;
          }
        }
      }

      state.isAppInitialized = true;
    }

    const initialSessionId = targetSession ? targetSession.unified_id : null;
    const isMobileDrawerActive = state.drawerOpen && window.innerWidth < 768;

    syncUnreadSessions(state.allSessions);

    if (!silent || newSig !== state.lastSessionsSig || needsInitialSelect) {
      state.lastSessionsSig = newSig;
      // 手机端抽屉展开浏览或用户正在滚动列表/搜索输入时，静默更新不销毁重构侧边栏 DOM，避免打断用户的滑动操作与视口跳动
      const isUserBusyWithList =
        state.isUserInteractingWithList ||
        (document.activeElement && document.activeElement.id === "search-input");
      if (!isMobileDrawerActive) {
        if (silent && isUserBusyWithList) {
          scheduleDeferredSessionListRender();
        } else {
          renderSessionList();
        }
      }
      if (!needsInitialSelect) {
        renderCurrentSessionHeader();
        renderApprovalBanners();
        renderOtherRunningPill();
      }
    }

    if (needsInitialSelect && initialSessionId) {
      selectSession(initialSessionId);
    }
  } catch (e) {
    console.error("加载会话失败:", e);
  }
}

export function updateSelectedSessionHighlight(prevId, nextId) {
  if (prevId) {
    document
      .querySelectorAll(`[data-session-item="${CSS.escape(prevId)}"]`)
      .forEach((el) => {
        el.className =
          "group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 bg-zinc-900/45 border-transparent hover:bg-zinc-900 hover:border-zinc-800 text-zinc-300";
      });
  }
  if (nextId) {
    document
      .querySelectorAll(`[data-session-item="${CSS.escape(nextId)}"]`)
      .forEach((el) => {
        el.className =
          "group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 bg-zinc-800/95 border-sky-500/50 text-zinc-100";
      });
  }
}

export async function selectSession(unifiedId) {
  const prevId = state.selectedSessionId;
  const wasMobileDrawerOpen = state.drawerOpen && window.innerWidth < 768;
  const wasUnread = state.unreadCompletedSessionIds.has(unifiedId);
  markSessionViewed(unifiedId);

  state.selectedSessionId = unifiedId;
  if (prevId !== unifiedId) {
    state.codeBlockStore = {};
    state.expandedCodeBlocks = {};
  }
  updateSessionHash(unifiedId);
  state.drawerOpen = false;
  state.autoScrollLocked = false;

  // 自动展开当前选中会话所在的项目分组
  const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
  let needFullSidebarRender = wasUnread;
  if (sess && sess.project_path) {
    const pKey = getProjectInfo(sess.project_path).key;
    if (pKey && !state.expandedProjects[pKey]) {
      state.expandedProjects[pKey] = true;
      needFullSidebarRender = true;
    }
  }

  // 1. 立即触发抽屉关闭动画与轻量高亮切换，避免同步重排卡住移动端滑出动画
  updateDrawerVisibility();
  const sessionListContainer = document.getElementById("session-list-container");
  const isListEmpty = !sessionListContainer || sessionListContainer.children.length === 0;
  if (needFullSidebarRender || isListEmpty) {
    renderSessionList();
  } else {
    updateSelectedSessionHighlight(prevId, unifiedId);
  }
  renderCurrentSessionHeader();
  renderApprovalBanners();
  renderOtherRunningPill();
  updateGitStatusBadge();
  renderLiveStreamOverlay();

  // 若会话所属项目发生变化，在后台异步获取该工作区专属的 Skills 与 Workflows
  if (sess && sess.project_path) {
    apiFetch(`/api/v1/workflows?project_path=${encodeURIComponent(sess.project_path)}`)
      .then((res) => {
        if (res && res.ok && Array.isArray(res.workflows)) {
          state.workflows = res.workflows;
        }
      })
      .catch(() => {});
  }

  // 2. 检查前端内存缓存（Stale-While-Revalidate 秒开）
  const cached = state.sessionMessagesCache[unifiedId];
  const mountCachedOrLoad = async () => {
    if (state.selectedSessionId !== unifiedId) return;

    // 若当前会话存在待确认的乐观用户消息（如刚新建并提交首条指令），优先直出避免骨架屏闪烁
    const optList = state.optimisticMessages[unifiedId] || [];
    if (optList.length > 0 && (!cached || !cached.messages || cached.messages.length === 0)) {
      state.messages = [...optList];
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
      // 后台静默同步最新消息
      loadSessionMessages(unifiedId, { silent: true });
    } else {
      state.currentTokenUsage = null;
      renderTokenUsage();
      await loadSessionMessages(unifiedId, { silent: false });
    }
  };

  if (wasMobileDrawerOpen) {
    // 移动端抽屉正在收起：让出 210ms 动画时长后再挂载大 DOM，保证 200ms 滑出动画 60fps 丝滑
    setTimeout(mountCachedOrLoad, 210);
  } else {
    await mountCachedOrLoad();
  }
}

export { loadSessionMessages, loadOlderMessages } from "./data-messages.js";

export async function checkFrontendVersion() {
  try {
    const url = state.activeBaseUrl ? `${state.activeBaseUrl}/api/v1/version` : "/api/v1/version";
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    if (data.ok && data.frontend_version) {
      if (!state.frontendBuild) {
        state.frontendBuild = data.frontend_version;
      } else if (state.frontendBuild !== data.frontend_version) {
        state.hasUpdate = true;
        state.remoteBuild = data.frontend_version;
        renderSidebarStatus();
      }
    }
  } catch (_) {}
}

export function handleStatusBtnClick() {
  if (state.hasUpdate) {
    showToast("检测到前端改动，正在载入最新代码...", "info");
    setTimeout(() => {
      window.location.reload(true);
    }, 150);
    return;
  }

  const now = Date.now();
  if (now - (state.lastStatusClickTime || 0) < 2000) {
    showToast("正在强制重新载入...", "info");
    setTimeout(() => {
      window.location.reload(true);
    }, 100);
    return;
  }
  state.lastStatusClickTime = now;
  showToast(
    state.wsConnected
      ? "服务已连接 · 再次点击可强制重载"
      : "连接尚未就绪 · 再次点击可重载页面",
    "info"
  );
  checkFrontendVersion();
}
