/**
 * AgentDeck 集中状态管理与未读会话时间戳调度
 */

import { DEFAULT_MODELS_BY_AGENT } from "./config.js";
import { isSessionStatusActive } from "./session-helper.js";

export function getCookie(name) {
  try {
    const match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[2]) : "";
  } catch (_) {
    return "";
  }
}

export const state = {
  // PWA 独立沙盒双重容灾：若 localStorage 为空，自动从浏览器共享 Cookie 恢复 Token 并写回本地
  token: (() => {
    let t = "";
    try {
      t = localStorage.getItem("agentdeck_token") || "";
    } catch (_) {}
    if (!t) {
      t = getCookie("agentdeck_token");
      if (t) {
        try {
          localStorage.setItem("agentdeck_token", t);
        } catch (_) {}
      }
    }
    return t;
  })(),
  activeBaseUrl: "", // 动态信道热切换基地址（支持 In-App 无刷新平滑漫游）
  wsConnected: false,
  wsReconnecting: false,
  lastSeqId: 0,
  systemStatus: null,
  modelsByAgent: DEFAULT_MODELS_BY_AGENT,
  allSessions: [], // 全量会话内存列表（支持切换 Agent 标签 0ms 内存过滤）
  sessions: [],
  lastSessionsSig: "",
  activeFilter: "all",
  searchQuery: "",
  remoteSearchMatches: new Map(), // 服务端全文检索命中缓存 (unified_id -> { type, snippet, session })
  selectedSessionId: null,
  messages: [],
  hasMoreMessages: false,
  totalMessagesCount: 0,
  loadingMessages: false,
  loadingOlderMessages: false,
  sessionMessagesCache: {}, // unifiedId -> { messages, hasMore, totalCount, modelName, updatedAt }
  pendingApprovals: [],
  workspaces: [],
  isAppInitialized: false, // 首次冷启动标志锁（防止后台刷新篡改用户主动选中的会话）
  expandedProjects: {}, // projectKey -> boolean
  projectSessionLimits: {}, // projectKey -> number (当前展开可见上限，默认 8)
  retainedActiveSessionIds: new Set(), // 当前查看会话运行完成后的平稳暂留集合（切换离开时才归位）
  collapsedSections: (() => {
    try {
      return JSON.parse(localStorage.getItem("agentdeck_sections_collapsed") || "{}");
    } catch (e) {
      return {};
    }
  })(),
  didInitExpand: false,
  wordWrapDiff: true,
  drawerOpen: false,
  sidebarCollapsed: localStorage.getItem("agentdeck_sidebar_collapsed") === "1",
  isUserInteractingWithList: false, // 视觉稳定性标志：用户滑动或聚焦列表时锁定重排防跳动
  streamingBuffer: {}, // unified_id -> string
  pendingTurnThinking: {}, // unified_id -> timestamp (ms)
  optimisticMessages: {}, // unified_id -> array of unconfirmed user message objects
  autoScrollLocked: false,
  stickyPromptExpanded: true, // 悬浮指令默认展示全部内容
  stickyPromptActiveIndex: 0, // 当前悬浮展示的提问下标
  expandedTurns: {}, // turnKey -> boolean
  turnThinkingExpanded: {}, // turnKey -> boolean（用户主动展开思考时在运行中保持展开）
  turnFilterEdits: {}, // turnKey -> boolean（为 true 时仅显示产生改动的步骤）
  expandedDetails: {}, // detailId -> boolean（保留已展开的思考/输出详情状态）
  lazyDetailsStore: {}, // detailId -> { type, raw, diff }
  turnsMap: {}, // turnKey -> turn object（供局部切换展开时不重绘整页）
  expandedCodeBlocks: {}, // codeId -> boolean
  expandedUserMessages: {}, // item_id -> boolean
  codeBlockStore: {}, // codeId -> raw code string
  currentDocRawContent: "",
  workflows: [],
  slashMenuOpen: false,
  slashHighlightIndex: 0,
  filteredSlashCommands: [],
  denyFeedbackOpen: false,
  denyFeedbackText: "",
  questionSelections: {}, // `${turnKey}_q${qi}` -> string[]
  questionWriteIns: {}, // `${turnKey}_q${qi}` -> string
  currentGitStatus: null,
  gitDiffCache: {},
  gitExpandedDiffs: {},
  currentTokenUsage: null,
  fullOutputStore: {}, // `${unifiedId}_${stepIndex}` -> full string
  persistedQuestionDecisions: (() => {
    try {
      return JSON.parse(localStorage.getItem("agentdeck_question_decisions") || "{}");
    } catch (_) {
      return {};
    }
  })(),
  frontendBuild: "",
  hasUpdate: false,
  remoteBuild: "",
  lastStatusClickTime: 0,
  unreadCompletedSessionIds: new Set(),
  sessionLastViewedTimes: (() => {
    try {
      return JSON.parse(localStorage.getItem("agentdeck_last_viewed_sessions") || "{}") || {};
    } catch (_) {
      return {};
    }
  })(),
};


window.state = state;

export function saveLastViewedTimes() {
  try {
    localStorage.setItem(
      "agentdeck_last_viewed_sessions",
      JSON.stringify(state.sessionLastViewedTimes || {})
    );
  } catch (e) {
    console.warn("保存会话查看时间失败:", e);
  }
}

export function markSessionViewed(unifiedId) {
  if (!unifiedId) return;
  state.sessionLastViewedTimes[unifiedId] = Date.now();
  state.unreadCompletedSessionIds.delete(unifiedId);
  saveLastViewedTimes();

  // 跨端同步：静默上报服务端已读状态，实时广播给其他客户端（如电脑查看后手机消红）
  try {
    if (typeof fetch === "function") {
      const token = localStorage.getItem("agentdeck_auth_token") || "";
      fetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}/viewed`, {
        method: "POST",
        headers: token ? { "Authorization": `Bearer ${token}` } : {},
      }).catch(() => {});
    }
  } catch (_) {}
}

export function markSessionCompletedUnread(unifiedId) {
  if (!unifiedId) return;
  if (unifiedId === state.selectedSessionId) {
    // 当前查看会话运行完成后平稳暂留在置顶与活动区，切换离开时再平滑归位
    state.retainedActiveSessionIds.add(unifiedId);
    return;
  }
  state.unreadCompletedSessionIds.add(unifiedId);
}

export function retainCurrentSessionActive(unifiedId) {
  if (!unifiedId) return;
  state.retainedActiveSessionIds.add(unifiedId);
}

export function releaseRetainedSession(unifiedId) {
  if (!unifiedId) return;
  state.retainedActiveSessionIds.delete(unifiedId);
}

export function isSessionRetainedActive(s) {
  const uid = typeof s === "string" ? s : s?.unified_id;
  if (!uid) return false;
  return state.retainedActiveSessionIds.has(uid);
}

export function isSessionCompletedUnread(s) {
  if (!s || !s.unified_id) return false;
  if (s.unified_id === state.selectedSessionId) return false;
  return state.unreadCompletedSessionIds.has(s.unified_id);
}

export function syncUnreadSessions(sessionList) {
  if (!Array.isArray(sessionList)) return;
  const now = Date.now();
  for (const s of sessionList) {
    if (!s || !s.unified_id) continue;
    if (s.unified_id === state.selectedSessionId) {
      state.unreadCompletedSessionIds.delete(s.unified_id);
      continue;
    }
    if (isSessionStatusActive(s) || s.status === "waiting_approval") {
      continue;
    }
    const localViewed = state.sessionLastViewedTimes[s.unified_id] || 0;
    const remoteViewed = s.last_viewed_at ? new Date(s.last_viewed_at).getTime() : 0;
    const lastViewed = Math.max(localViewed, isNaN(remoteViewed) ? 0 : remoteViewed);
    const updatedAt = s.updated_at ? new Date(s.updated_at).getTime() : 0;

    // 严密判定：
    // 1. 若存在有效查看记录 (lastViewed > 0)，且最新完成时间晚于查看时间（在24小时内），判定为有新内容未查看；若已查看则移除
    // 2. 若从未记录过查看时间 (lastViewed === 0)：保留运行时动态完成事件登记的待查看状态，不主动将历史存量会话误判为未读
    if (lastViewed > 0 && updatedAt > 0) {
      if (lastViewed >= updatedAt) {
        state.unreadCompletedSessionIds.delete(s.unified_id);
      } else if (updatedAt > lastViewed && (now - updatedAt < 24 * 3600 * 1000)) {
        state.unreadCompletedSessionIds.add(s.unified_id);
      } else {
        state.unreadCompletedSessionIds.delete(s.unified_id);
      }
    }
  }
}

export function clearAllUnreadCompletedSessions(viewedAtIso) {
  const viewedTs = viewedAtIso ? new Date(viewedAtIso).getTime() : Date.now();
  state.unreadCompletedSessionIds.clear();
  const allList = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  for (const s of allList) {
    if (s && s.unified_id) {
      state.sessionLastViewedTimes[s.unified_id] = viewedTs;
      if (viewedAtIso) {
        s.last_viewed_at = viewedAtIso;
      }
    }
  }
  saveLastViewedTimes();
}

