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

export function cleanFriendlyTitle(title) {
  if (!title) return "新对话";
  return cleanFriendlyText(title).slice(0, 50);
}

export function isSessionStatusActive(s) {
  if (!s || !s.status) return false;
  const st = String(s.status).toLowerCase();
  return st === "running" || st === "desktop_active";
}

export function isSessionWorking(unifiedId) {
  if (!unifiedId) return false;
  const sess = (state.allSessions || state.sessions || []).find((s) => s.unified_id === unifiedId);
  const st = sess ? String(sess.status || "").toLowerCase() : "";
  const isSessRunning = st === "running" || st === "desktop_active";
  const hasThinking = Boolean(state.pendingTurnThinking?.[unifiedId]);
  const hasDelta = Boolean(state.streamingBuffer?.[unifiedId]?.length > 0);
  const hasOpt = Boolean(state.optimisticMessages?.[unifiedId]?.length > 0);
  return isSessRunning || hasThinking || hasDelta || hasOpt;
}

export const SESSION_PRIORITY = Object.freeze({
  WAITING_APPROVAL: 3,
  COMPLETED_UNREAD: 2,
  ACTIVE: 1,
  DEFAULT: 0,
});

export function getSessionPriorityWeight(s) {
  if (!s) return SESSION_PRIORITY.DEFAULT;
  const st = String(s.status || "").toLowerCase();
  if (st === "waiting_approval") return SESSION_PRIORITY.WAITING_APPROVAL;
  if (isSessionCompletedUnread(s)) return SESSION_PRIORITY.COMPLETED_UNREAD;
  if (st === "running" || st === "desktop_active" || isSessionWorking(s.unified_id)) {
    return SESSION_PRIORITY.ACTIVE;
  }
  return SESSION_PRIORITY.DEFAULT;
}

export function isSessionAttentionNeeded(s) {
  return Boolean(s) && getSessionPriorityWeight(s) > SESSION_PRIORITY.DEFAULT;
}

export function getAllAttentionSessions() {
  const all = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const list = all.filter((s) => isSessionAttentionNeeded(s));

  return list.sort((a, b) => {
    const wa = getSessionPriorityWeight(a);
    const wb = getSessionPriorityWeight(b);
    if (wa !== wb) return wb - wa;
    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  });
}

export function getPendingAttentionSessions() {
  return getAllAttentionSessions().filter((s) => s.unified_id !== state.selectedSessionId);
}

export function getLatestSession(list) {
  if (!list || list.length === 0) return null;
  if (list.length === 1) return list[0];
  return [...list].sort((a, b) => {
    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  })[0];
}

/**
 * 首次进入或重置时的首选会话决策权重（遵循 Spec）：
 * 1. 运行中/进行中活跃会话最高优先 (30)
 * 2. 待审批任务次之 (20)
 * 3. 收藏/置顶会话 (10)
 * 4. 普通历史会话 (0)
 */
export function getSessionDefaultEntryWeight(s) {
  if (!s) return 0;
  if (isSessionStatusActive(s) || isSessionWorking(s.unified_id)) return 30;
  if (String(s.status || "").toLowerCase() === "waiting_approval") return 20;
  if (s.is_pinned) return 10;
  return 0;
}

export function pickDefaultSession(sessionList) {
  if (!Array.isArray(sessionList) || sessionList.length === 0) {
    return null;
  }
  const sorted = [...sessionList].sort((a, b) => {
    const wa = getSessionDefaultEntryWeight(a);
    const wb = getSessionDefaultEntryWeight(b);
    if (wa !== wb) return wb - wa;
    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  });
  return sorted[0];
}

export function getProjectInfo(rawPath) {
  const clean = String(rawPath || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (!clean) {
    return { key: "", name: "未分类", path: "" };
  }
  const parts = clean.split("/").filter(Boolean);
  const name = parts[parts.length - 1] || clean;
  return { key: clean.toLowerCase(), name, path: clean };
}

export function getModelContextLimit(modelId) {
  if (!modelId) return 200000;
  const m = String(modelId).toLowerCase();
  if (MODEL_CONTEXT_LIMITS[m]) return MODEL_CONTEXT_LIMITS[m];
  for (const [k, v] of Object.entries(MODEL_CONTEXT_LIMITS)) {
    if (m.includes(k) || k.includes(m)) return v;
  }
  if (m.includes("gemini")) return 1000000;
  if (m.includes("claude")) return 200000;
  return 200000;
}

export function getAgentDisplayName(agentType) {
  const nameMap = {
    claude_code: "Claude",
    agy_ide: "Antigravity",
    agy_cli: "AGY",
    opencode: "OpenCode",
  };
  return nameMap[agentType] || AGENT_META[agentType]?.short || AGENT_META[agentType]?.label || "Agent";
}

export async function loadSystemStatus() {
  try {
    const data = await apiFetch("/api/v1/system/status");
    state.systemStatus = data;
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
      const hashId = getSessionIdFromHash();
      const hashSess = hashId ? state.allSessions.find((s) => s.unified_id === hashId) : null;

      if (hashSess) {
        targetSession = hashSess;
      } else if (state.allSessions.length > 0) {
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

  const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
  let needFullSidebarRender = wasUnread;
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

  if (sess && sess.project_path) {
    apiFetch(`/api/v1/workflows?project_path=${encodeURIComponent(sess.project_path)}`)
      .then((res) => {
        if (res && res.ok && Array.isArray(res.workflows)) {
          state.workflows = res.workflows;
        }
      })
      .catch(() => {});
  }

  const cached = state.sessionMessagesCache[unifiedId];
  const mountCachedOrLoad = async () => {
    if (state.selectedSessionId !== unifiedId) return;

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
      loadSessionMessages(unifiedId, { silent: true });
    } else {
      state.currentTokenUsage = null;
      renderTokenUsage();
      await loadSessionMessages(unifiedId, { silent: false });
    }
  };

  if (wasMobileDrawerOpen) {
    setTimeout(mountCachedOrLoad, 210);
  } else {
    await mountCachedOrLoad();
  }
}

export async function loadSessionMessages(unifiedId, options = false) {
  const silent = typeof options === "boolean" ? options : Boolean(options?.silent);
  const limit = (typeof options === "object" && options?.limit) || 30;

  if (!silent) {
    state.loadingMessages = true;
    renderMessagesList();
  }
  try {
    const headers = {};
    const prevCache = state.sessionMessagesCache[unifiedId];
    let sinceStep = null;
    if (silent && prevCache && Array.isArray(prevCache.messages) && prevCache.messages.length > 0) {
      const lastLocalMsg = prevCache.messages[prevCache.messages.length - 1];
      if (typeof lastLocalMsg.step_index === "number") {
        sinceStep = lastLocalMsg.step_index;
      }
    }

    const lastETag = state.sessionMessagesETag?.[unifiedId];
    if (silent && lastETag) {
      headers["If-None-Match"] = lastETag;
    }

    let fetchUrl = `/api/v1/sessions/${encodeURIComponent(unifiedId)}/messages?limit=${limit}`;
    if (sinceStep !== null) {
      fetchUrl += `&since_step=${sinceStep}`;
    }

    const data = await apiFetch(fetchUrl, { headers });
    if (data && data.not_modified) {
      return;
    }
    if (data && data._etag) {
      state.sessionMessagesETag = state.sessionMessagesETag || {};
      state.sessionMessagesETag[unifiedId] = data._etag;
    }
    const incoming = data.messages || [];
    const hasMore = Boolean(data.has_more);
    const totalCount = data.total_count || incoming.length;

    if (data.is_running) {
      if (!state.pendingTurnThinking[unifiedId]) {
        state.pendingTurnThinking[unifiedId] = Date.now();
      }
      const sess = state.sessions.find((s) => s.unified_id === unifiedId);
      if (sess && sess.status !== "running") {
        sess.status = "running";
        renderSessionList();
      }
    } else {
      const optList = state.optimisticMessages[unifiedId] || [];
      if (optList.length === 0) {
        delete state.pendingTurnThinking[unifiedId];
      }
    }

    let mergedMessages = incoming;
    let mergedHasMore = hasMore;
    if (sinceStep !== null && prevCache && Array.isArray(prevCache.messages)) {
      if (typeof totalCount === "number" && totalCount < prevCache.messages.length) {
        mergedMessages = incoming;
      } else if (incoming.length === 0) {
        mergedMessages = prevCache.messages;
        mergedHasMore = prevCache.hasMore;
      } else {
        const msgMap = new Map();
        for (const m of prevCache.messages) {
          if (m && m.step_index !== undefined) {
            msgMap.set(m.step_index, m);
          }
        }
        for (const m of incoming) {
          if (m && m.step_index !== undefined) {
            msgMap.set(m.step_index, m);
          }
        }
        mergedMessages = Array.from(msgMap.values()).sort(
          (a, b) => (a.step_index || 0) - (b.step_index || 0)
        );
        mergedHasMore = prevCache.hasMore;
      }
    } else if (
      silent &&
      prevCache &&
      Array.isArray(prevCache.messages) &&
      prevCache.messages.length > incoming.length &&
      incoming.length > 0
    ) {
      const firstNewStep = incoming[0].step_index;
      const olderLoaded = prevCache.messages.filter((m) => m.step_index < firstNewStep);
      if (olderLoaded.length > 0) {
        mergedMessages = [...olderLoaded, ...incoming];
        mergedHasMore = prevCache.hasMore;
      }
    }

    const optList = state.optimisticMessages[unifiedId] || [];
    if (optList.length > 0) {
      const stillPending = [];
      for (const opt of optList) {
        const found = incoming.some(
          (inc) =>
            inc.role === "user" &&
            String(inc.content || "").trim() === String(opt.content || "").trim()
        );
        if (!found) {
          stillPending.push(opt);
        }
      }
      state.optimisticMessages[unifiedId] = stillPending;
      if (stillPending.length > 0) {
        mergedMessages = [...mergedMessages, ...stillPending];
      }
    }

    const newSig = computeMessagesSig(mergedMessages, mergedHasMore, totalCount);
    const oldSig = prevCache ? prevCache.sig : "";

    mergedMessages.forEach((msg) => {
      const full = state.fullOutputStore[`${unifiedId}_${msg.step_index}`];
      if (full && Array.isArray(msg.tool_calls)) {
        msg.tool_calls.forEach((tc) => {
          tc.output = full;
          tc.is_truncated = false;
        });
      }
    });

    state.sessionMessagesCache[unifiedId] = {
      messages: mergedMessages,
      hasMore: mergedHasMore,
      totalCount,
      modelName: data.model_name || "",
      tokenUsage: data.token_usage || null,
      sig: newSig,
      updatedAt: Date.now(),
    };

    if (state.selectedSessionId === unifiedId) {
      const wasLoading = state.loadingMessages;
      state.messages = mergedMessages;
      state.hasMoreMessages = mergedHasMore;
      state.totalMessagesCount = totalCount;
      if (data.token_usage) {
        state.currentTokenUsage = data.token_usage;
        const modelSelect = document.getElementById("model-select");
        const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
        if (modelSelect && sess) {
          renderModelOptionsForAgent(modelSelect, sess.agent_type, sess.model_name || data.model_name);
        }
      }
      if (data.model_name) {
        const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
        if (sess) sess.model_name = data.model_name;
      }
      state.loadingMessages = false;
      if (!silent || wasLoading || newSig !== oldSig) {
        renderMessagesList();
        renderCurrentSessionHeader();
      } else {
        renderLiveStreamOverlay();
        renderCurrentSessionHeader();
      }
    }
  } catch (e) {
    if (state.selectedSessionId === unifiedId) {
      state.loadingMessages = false;
      renderMessagesList();
    }
  }
}

export async function loadOlderMessages() {
  const unifiedId = state.selectedSessionId;
  if (!unifiedId || state.loadingOlderMessages || !state.hasMoreMessages) return;
  const firstMsg = state.messages[0];
  if (!firstMsg || !firstMsg.step_index) return;

  state.loadingOlderMessages = true;
  const btnEl = document.getElementById("load-older-messages-btn");
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.textContent = "⏳ 正在加载更早记录...";
  }

  const container = document.getElementById("messages-container");
  const prevScrollHeight = container ? container.scrollHeight : 0;
  const prevScrollTop = container ? container.scrollTop : 0;

  try {
    const data = await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(unifiedId)}/messages?limit=30&before_step=${firstMsg.step_index}`
    );
    if (state.selectedSessionId !== unifiedId) return;
    const older = data.messages || [];
    const existingIds = new Set(state.messages.map((m) => m.step_index));
    const dedupOlder = older.filter((m) => !existingIds.has(m.step_index));

    state.messages = [...dedupOlder, ...state.messages];
    state.hasMoreMessages = Boolean(data.has_more);
    state.totalMessagesCount = data.total_count || state.totalMessagesCount;
    state.loadingOlderMessages = false;

    const sig = computeMessagesSig(state.messages, state.hasMoreMessages, state.totalMessagesCount);
    state.sessionMessagesCache[unifiedId] = {
      messages: state.messages,
      hasMore: state.hasMoreMessages,
      totalCount: state.totalMessagesCount,
      sig,
      updatedAt: Date.now(),
    };

    state.autoScrollLocked = true;
    renderMessagesList();

    if (container) {
      const newScrollHeight = container.scrollHeight;
      container.scrollTop = prevScrollTop + (newScrollHeight - prevScrollHeight);
    }
  } catch (e) {
    state.loadingOlderMessages = false;
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "重试加载更早消息";
    }
  }
}

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
