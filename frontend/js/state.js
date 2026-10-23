/**
 * AgentDeck 集中状态管理与未读会话时间戳调度
 */

import { DEFAULT_MODELS_BY_AGENT } from "./config.js";

export const state = {
  token: localStorage.getItem("agentdeck_token") || "",
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
  expandedTurns: {}, // turnKey -> boolean
  turnThinkingExpanded: {}, // turnKey -> boolean（用户主动展开思考时在运行中保持展开）
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
  selectedQrNet: null,
  expandHugeFullDiff: false,
};

// 暴露全局 state 引用，兼容内联模板表达式
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
}

export function markSessionCompletedUnread(unifiedId) {
  if (!unifiedId || unifiedId === state.selectedSessionId) return;
  state.unreadCompletedSessionIds.add(unifiedId);
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
    const st = String(s.status || "").toLowerCase();
    if (st === "running" || st === "desktop_active" || st === "waiting_approval") {
      continue;
    }
    const lastViewed = state.sessionLastViewedTimes[s.unified_id] || 0;
    const updatedAt = s.updated_at ? new Date(s.updated_at).getTime() : 0;
    if (updatedAt > 0 && lastViewed > 0 && updatedAt > lastViewed && now - updatedAt < 24 * 3600 * 1000) {
      state.unreadCompletedSessionIds.add(s.unified_id);
    }
  }
}
