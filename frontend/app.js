/**
 * AgentDeck 移动端与桌面端统一中控台前端核心逻辑
 * 遵循前端 UI/UX 设计规范：
 * 1. 参照 Antigravity-Deck 左侧栏设计：置顶 (Pinned) + 项目分组 (Projects) + 最近对话 (Recent Conversations)。
 * 2. 对话底部工具栏支持切换模型 (Model Selector) 与权限模式 (Mode Selector)。
 * 3. 全站文案精炼，顶部栏适配 safe-area-inset-top 防遮挡。
 */

const DEFAULT_MODELS_BY_AGENT = {
  agy_cli: [
    { id: "claude-sonnet-5-5-high", label: "Claude Sonnet 5.5 (High)", tag: "推荐" },
    { id: "claude-opus-5-5-high", label: "Claude Opus 5.5 (High)", tag: "深度" },
    { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)", tag: "旗舰" },
    { id: "gemini-4-argon-medium", label: "Gemini 4 Argon (Med)", tag: "均衡" },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash", tag: "极速" },
    { id: "gpt-oss-120b-medium", label: "GPT-OSS 120B", tag: "" },
  ],
  agy_ide: [
    { id: "claude-sonnet-5-5-high", label: "Claude Sonnet 5.5 (High)", tag: "推荐" },
    { id: "claude-opus-5-5-high", label: "Claude Opus 5.5 (High)", tag: "深度" },
    { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)", tag: "旗舰" },
    { id: "gemini-4-argon-medium", label: "Gemini 4 Argon (Med)", tag: "均衡" },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash", tag: "极速" },
  ],
  claude_code: [
    { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", tag: "默认" },
    { id: "claude-opus-4-7", label: "Claude Opus 4.7", tag: "深度" },
    { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5", tag: "极速" },
    { id: "sonnet", label: "Sonnet (Latest)", tag: "" },
    { id: "opus", label: "Opus (Latest)", tag: "" },
  ],
  opencode: [
    { id: "default", label: "OpenCode 默认", tag: "默认" },
    { id: "anthropic/claude-sonnet-4-5", label: "Claude Sonnet 4.5", tag: "" },
    { id: "google/gemini-2.5-pro", label: "Gemini 2.5 Pro", tag: "" },
    { id: "openai/gpt-5", label: "GPT-5", tag: "" },
  ],
};

const state = {
  token: localStorage.getItem("agentdeck_token") || "",
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
  expandedProjects: {}, // projectKey -> boolean
  didInitExpand: false,
  wordWrapDiff: true,
  drawerOpen: false,
  sidebarCollapsed: localStorage.getItem("agentdeck_sidebar_collapsed") === "1",
  streamingBuffer: {}, // unified_id -> string
  autoScrollLocked: false,
  expandedTurns: {}, // turnKey -> boolean
  expandedDetails: {}, // detailId -> boolean（保留已展开的思考/输出详情状态）
  lazyDetailsStore: {}, // detailId -> { type, raw, diff }
  turnsMap: {}, // turnKey -> turn object（供局部切换展开时不重绘整页）
  expandedCodeBlocks: {}, // codeId -> boolean
  expandedUserMessages: {}, // item_id -> boolean
  codeBlockStore: {}, // codeId -> raw code string
  currentDocRawContent: "",
  workflows: [],
  slashMenuOpen: false,
  denyFeedbackOpen: false,
  denyFeedbackText: "",
  questionSelections: {}, // `${turnKey}_q${qi}` -> string[]
  questionWriteIns: {}, // `${turnKey}_q${qi}` -> string
  currentGitStatus: null,
  gitDiffCache: {},
  gitExpandedDiffs: {},
  fullOutputStore: {}, // `${unifiedId}_${stepIndex}` -> full string
  persistedQuestionDecisions: (() => {
    try {
      return JSON.parse(localStorage.getItem("agentdeck_question_decisions") || "{}");
    } catch (_) {
      return {};
    }
  })(),
};

const GIT_STATUS_BADGES = {
  M: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  A: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  U: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  D: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  R: "bg-purple-500/20 text-purple-300 border-purple-500/40",
};

const AGENT_META = {
  agy_ide: {
    label: "Antigravity IDE",
    short: "IDE",
    badgeClass: "bg-purple-500/20 text-purple-300 border-purple-500/40",
  },
  agy_cli: {
    label: "Antigravity CLI",
    short: "AGY",
    badgeClass: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  },
  claude_code: {
    label: "Claude Code",
    short: "Claude",
    badgeClass: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  },
  opencode: {
    label: "OpenCode",
    short: "OC",
    badgeClass: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  },
};

let wsInstance = null;
let holdTimer = null;
let holdStartTime = 0;
let silentSessionsTimer = null;
let silentMessagesTimer = null;

function scheduleSilentSessionsRefresh(delayMs = 350) {
  if (silentSessionsTimer) clearTimeout(silentSessionsTimer);
  silentSessionsTimer = setTimeout(() => {
    silentSessionsTimer = null;
    loadSessions(true);
  }, delayMs);
}

function scheduleSilentMessagesRefresh(unifiedId, delayMs = 250) {
  if (!unifiedId) return;
  if (silentMessagesTimer) clearTimeout(silentMessagesTimer);
  silentMessagesTimer = setTimeout(() => {
    silentMessagesTimer = null;
    if (state.selectedSessionId === unifiedId) {
      loadSessionMessages(unifiedId, { silent: true });
    }
  }, delayMs);
}

// --- API 请求封装 ---
async function apiFetch(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (state.token) {
    headers["X-AgentDeck-Token"] = state.token;
  }
  const resp = await fetch(path, { ...options, headers });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const errMsg = data.detail || data.error || `请求失败 (${resp.status})`;
    showToast(errMsg, "error");
    throw new Error(errMsg);
  }
  return data;
}

// --- 1. 一次性配对码置换 ---
async function checkAndExchangePairCode() {
  const params = new URLSearchParams(window.location.search);
  const pairCode = params.get("pair_code");
  if (!pairCode) return;

  try {
    const ua = navigator.userAgent || "Mobile";
    const devName = /iPhone|iPad/i.test(ua)
      ? "iPhone"
      : /Android/i.test(ua)
      ? "Android"
      : "Web Client";

    const res = await apiFetch("/api/v1/auth/exchange", {
      method: "POST",
      body: JSON.stringify({ pair_code: pairCode, device_name: devName }),
    });
    if (res.token) {
      state.token = res.token;
      localStorage.setItem("agentdeck_token", res.token);
      showToast("扫码配对成功", "success");
    }
  } catch (err) {
    console.warn("配对码置换失败:", err);
  } finally {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

// --- 2. 震动与通知唤醒 ---
function triggerAlertNotification(title, body) {
  if ("vibrate" in navigator) {
    try {
      navigator.vibrate([200, 100, 200]);
    } catch (_) {}
  }
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      new Notification(title, { body });
    } catch (_) {}
  }
}

function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

// --- 3. WebSocket 实时流 ---
function connectWebSocket() {
  if (wsInstance) {
    try {
      wsInstance.close();
    } catch (_) {}
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = new URLSearchParams({
    since_seq: String(state.lastSeqId || 0),
    token: state.token || "",
  });
  const wsUrl = `${proto}//${window.location.host}/ws/stream?${qs.toString()}`;

  wsInstance = new WebSocket(wsUrl);

  wsInstance.onopen = () => {
    const wasReconnecting = state.wsReconnecting;
    state.wsConnected = true;
    state.wsReconnecting = false;
    renderHeaderStatus();
    if (wasReconnecting) {
      showToast("已恢复同步", "success");
      loadSessions();
      if (state.selectedSessionId) {
        loadSessionMessages(state.selectedSessionId, true);
      }
    }
  };

  wsInstance.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.seq_id && msg.seq_id > state.lastSeqId) {
        state.lastSeqId = msg.seq_id;
      }
      handleRealtimeEvent(msg);
    } catch (e) {
      console.error("WS 解析失败:", e);
    }
  };

  wsInstance.onclose = () => {
    state.wsConnected = false;
    state.wsReconnecting = true;
    renderHeaderStatus();
    setTimeout(connectWebSocket, 2200);
  };
}

function handleRealtimeEvent(envelope) {
  const { type, payload } = envelope;

  if (type === "SESSION_STATE_SNAPSHOT") {
    state.pendingApprovals = payload.pending_approvals || [];
    renderApprovalBanners();
    return;
  }

  if (type === "APPROVAL_REQUIRED") {
    const exists = state.pendingApprovals.some(
      (p) => p.unified_id === payload.unified_id
    );
    if (!exists) {
      state.pendingApprovals.push(payload);
    } else {
      state.pendingApprovals = state.pendingApprovals.map((p) =>
        p.unified_id === payload.unified_id ? payload : p
      );
    }
    triggerAlertNotification(
      "待审批工具调用",
      `[${payload.tool_name}] ${payload.args_summary}`
    );
    renderApprovalBanners();
    loadSessions();
    return;
  }

  if (type === "APPROVAL_RESOLVED") {
    state.pendingApprovals = state.pendingApprovals.filter(
      (p) => p.unified_id !== payload.unified_id
    );
    renderApprovalBanners();
    loadSessions();
    return;
  }

  if (type === "STREAM_DELTA") {
    const uid = payload.unified_id;
    state.streamingBuffer[uid] =
      (state.streamingBuffer[uid] || "") + (payload.text || "") + "\n";
    if (uid === state.selectedSessionId) {
      renderLiveStreamOverlay();
    }
    return;
  }

  if (type === "STREAM_JSON_FRAME") {
    const uid = payload.unified_id;
    const frame = payload.frame || {};
    const snippet = extractReadableSnippetFromFrame(frame);
    if (snippet) {
      state.streamingBuffer[uid] =
        (state.streamingBuffer[uid] || "") + snippet + "\n";
      if (uid === state.selectedSessionId) {
        renderLiveStreamOverlay();
      }
    }
    return;
  }

  if (
    type === "STORAGE_UPDATED" ||
    type === "SESSION_TURN_STARTED" ||
    type === "SESSION_TURN_FINISHED" ||
    type === "SESSION_ABORTED"
  ) {
    if (type === "SESSION_TURN_FINISHED" || type === "SESSION_ABORTED") {
      if (payload && payload.unified_id) {
        delete state.streamingBuffer[payload.unified_id];
      }
    }

    if (type === "STORAGE_UPDATED") {
      const affectedIds = (payload && payload.affected_session_ids) || [];
      const affectedAgents = (payload && payload.affected_agents) || [];
      for (const uid of affectedIds) {
        if (state.sessionMessagesCache[uid] && uid !== state.selectedSessionId) {
          state.sessionMessagesCache[uid].stale = true;
        }
      }
      scheduleSilentSessionsRefresh(450);

      if (state.selectedSessionId) {
        const curAgent = state.selectedSessionId.split(":")[0];
        const isCurrentSessionAffected =
          affectedIds.includes(state.selectedSessionId) ||
          (affectedIds.length === 0 &&
            (affectedAgents.length === 0 || affectedAgents.includes(curAgent)));
        if (isCurrentSessionAffected) {
          scheduleSilentMessagesRefresh(state.selectedSessionId, 280);
        }
      }
      return;
    }

    loadSessions(true);
    if (state.selectedSessionId && (!payload?.unified_id || payload.unified_id === state.selectedSessionId)) {
      loadSessionMessages(state.selectedSessionId, { silent: true });
    }
    return;
  }

  if (type === "SESSION_ERROR") {
    showToast(payload.error || "执行异常", "error");
    loadSessions();
  }
}

function extractReadableSnippetFromFrame(frame) {
  if (!frame || typeof frame !== "object") return "";
  if (frame.type === "assistant" && frame.message && Array.isArray(frame.message.content)) {
    return frame.message.content
      .map((c) => {
        if (c.type === "text") return c.text;
        if (c.type === "thinking") return `💭 ${c.thinking}`;
        if (c.type === "tool_use") return `🔧 ${c.name}`;
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (frame.content) return String(frame.content);
  return "";
}

// --- 4. 数据加载 ---
async function loadSystemStatus() {
  try {
    const data = await apiFetch("/api/v1/system/status");
    state.systemStatus = data;
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
    renderApprovalBanners();
    renderQrModalContent();
    renderCurrentSessionHeader();
    updateGitStatusBadge();
  } catch (e) {
    console.error("获取系统状态失败:", e);
  }
}

async function loadWorkspaces() {
  try {
    const data = await apiFetch("/api/v1/workspaces");
    state.workspaces = data.workspaces || [];
  } catch (_) {}
}

function computeSessionsSig(sessions, approvals) {
  const sPart = (sessions || [])
    .map(
      (s) =>
        `${s.unified_id}:${s.updated_at}:${s.status}:${s.is_pinned ? 1 : 0}:${s.title}:${s.execution_mode}:${s.model_name}`
    )
    .join("|");
  const aPart = (approvals || []).map((a) => a.request_id).join(",");
  return `${sPart}#${aPart}`;
}

async function loadSessions(silent = false) {
  try {
    // 始终拉取全量会话（limit=60），前端切换 Agent 标签时 0ms 纯内存过滤
    const data = await apiFetch(`/api/v1/sessions?agent_type=all&limit=60`);
    const incomingSessions = data.sessions || [];
    const incomingApprovals = data.pending_approvals || state.pendingApprovals || [];
    const newSig = computeSessionsSig(incomingSessions, incomingApprovals);

    state.allSessions = incomingSessions;
    state.sessions = incomingSessions;
    if (data.pending_approvals) {
      state.pendingApprovals = data.pending_approvals;
    }

    if (!state.selectedSessionId && state.allSessions.length > 0) {
      state.lastSessionsSig = newSig;
      selectSession(state.allSessions[0].unified_id);
      return;
    }

    if (!silent || newSig !== state.lastSessionsSig) {
      state.lastSessionsSig = newSig;
      renderSessionList();
      renderCurrentSessionHeader();
      renderApprovalBanners();
    }
  } catch (e) {
    console.error("加载会话失败:", e);
  }
}

function updateSelectedSessionHighlight(prevId, nextId) {
  if (prevId) {
    document
      .querySelectorAll(`[data-session-item="${CSS.escape(prevId)}"]`)
      .forEach((el) => {
        el.className =
          "group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 bg-slate-900/45 border-transparent hover:bg-slate-900 hover:border-slate-800 text-slate-300";
      });
  }
  if (nextId) {
    document
      .querySelectorAll(`[data-session-item="${CSS.escape(nextId)}"]`)
      .forEach((el) => {
        el.className =
          "group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 bg-slate-800/95 border-sky-500/50 text-slate-100";
      });
  }
}

async function selectSession(unifiedId) {
  const prevId = state.selectedSessionId;
  const wasMobileDrawerOpen = state.drawerOpen && window.innerWidth < 768;
  state.selectedSessionId = unifiedId;
  state.drawerOpen = false;
  state.autoScrollLocked = false;

  // 自动展开当前选中会话所在的项目分组
  const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
  let needFullSidebarRender = false;
  if (sess && sess.project_path) {
    const pKey = getProjectInfo(sess.project_path).key;
    if (pKey && !state.expandedProjects[pKey]) {
      state.expandedProjects[pKey] = true;
      needFullSidebarRender = true;
    }
  }

  // 1. 立即触发抽屉关闭动画与轻量高亮切换，避免同步重排卡住移动端滑出动画
  updateDrawerVisibility();
  if (needFullSidebarRender) {
    renderSessionList();
  } else {
    updateSelectedSessionHighlight(prevId, unifiedId);
  }
  renderCurrentSessionHeader();
  renderApprovalBanners();
  updateGitStatusBadge();

  // 2. 检查前端内存缓存（Stale-While-Revalidate 秒开）
  const cached = state.sessionMessagesCache[unifiedId];
  const mountCachedOrLoad = async () => {
    if (state.selectedSessionId !== unifiedId) return;
    if (cached && Array.isArray(cached.messages) && cached.messages.length > 0) {
      state.messages = cached.messages;
      state.hasMoreMessages = Boolean(cached.hasMore);
      state.totalMessagesCount = cached.totalCount || cached.messages.length;
      state.loadingMessages = false;
      renderMessagesList();
      // 后台静默同步最新消息
      loadSessionMessages(unifiedId, { silent: true });
    } else {
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

function computeMessagesSig(messages, hasMore, totalCount) {
  if (!messages || messages.length === 0) return `0:${hasMore}:${totalCount}`;
  const first = messages[0];
  const last = messages[messages.length - 1];
  const lastTcLen = Array.isArray(last.tool_calls) ? last.tool_calls.length : 0;
  return `${messages.length}:${first.step_index}:${last.step_index}:${(last.content || "").length}:${(last.thinking || "").length}:${lastTcLen}:${hasMore}:${totalCount}`;
}

async function loadSessionMessages(unifiedId, options = false) {
  const silent = typeof options === "boolean" ? options : Boolean(options?.silent);
  const limit = (typeof options === "object" && options?.limit) || 30;

  if (!silent) {
    state.loadingMessages = true;
    renderMessagesList();
  }
  try {
    const data = await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(unifiedId)}/messages?limit=${limit}`
    );
    const incoming = data.messages || [];
    const hasMore = Boolean(data.has_more);
    const totalCount = data.total_count || incoming.length;

    // 写入内存缓存
    const prevCache = state.sessionMessagesCache[unifiedId];
    let mergedMessages = incoming;
    let mergedHasMore = hasMore;
    // 若用户此前已向上加载过更早消息，静默刷新时保留头部已加载的历史步骤
    if (
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

    const newSig = computeMessagesSig(mergedMessages, mergedHasMore, totalCount);
    const oldSig = prevCache ? prevCache.sig : "";

    // 恢复用户此前点击「加载完整输出」拉取的未截断文本，防止静默刷新回退
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
      sig: newSig,
      updatedAt: Date.now(),
    };

    if (state.selectedSessionId === unifiedId) {
      const wasLoading = state.loadingMessages;
      state.messages = mergedMessages;
      state.hasMoreMessages = mergedHasMore;
      state.totalMessagesCount = totalCount;
      if (data.model_name) {
        const sess = state.allSessions.find((s) => s.unified_id === unifiedId);
        if (sess) sess.model_name = data.model_name;
      }
      state.loadingMessages = false;
      if (!silent || wasLoading || newSig !== oldSig) {
        renderMessagesList();
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

async function loadOlderMessages() {
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

    // 保持视觉阅读锚点不跳动
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

// --- 5. 核心操作：发送消息、切换模型/模式、中断、审批 ---
async function handleSendPrompt(customPrompt = null) {
  const inputEl = document.getElementById("prompt-input");
  const text = (customPrompt !== null ? customPrompt : inputEl.value).trim();
  if (!text) return;

  if (!state.selectedSessionId) {
    openNewSessionModal(text);
    return;
  }

  requestNotificationPermission();
  if (customPrompt === null) {
    inputEl.value = "";
    inputEl.style.height = "auto";
  }

  const modelSelect = document.getElementById("model-select");
  const modeSelect = document.getElementById("execution-mode-select");
  const chosenModel = modelSelect ? modelSelect.value : "";
  const chosenMode = modeSelect ? modeSelect.value : "ask";

  state.messages.push({
    item_id: `optimistic_${Date.now()}`,
    step_index: state.messages.length + 1,
    role: "user",
    created_at: new Date().toISOString(),
    content: text,
    thinking: "",
    tool_calls: [],
    status: "RUNNING",
  });
  state.autoScrollLocked = false;
  renderMessagesList();

  try {
    const res = await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/send`,
      {
        method: "POST",
        body: JSON.stringify({
          prompt: text,
          model_name: chosenModel || undefined,
          execution_mode: chosenMode || undefined,
          auto_fork_ide: true,
        }),
      }
    );

    if (res.forked_from && res.unified_id && res.unified_id !== state.selectedSessionId) {
      showToast("已克隆至 AGY CLI 继续", "info");
      state.selectedSessionId = res.unified_id;
    }
    await loadSessions();
    await loadSessionMessages(state.selectedSessionId, true);
  } catch (err) {
    console.error("发送失败:", err);
  }
}

async function handleAbortCurrentSession() {
  if (!state.selectedSessionId) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/abort`,
      { method: "POST" }
    );
    showToast("已中断进程", "info");
    await loadSessions();
  } catch (err) {
    console.error("中断会话失败:", err);
  }
}

async function handleModeChange(newMode) {
  if (!state.selectedSessionId) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/meta`,
      {
        method: "PATCH",
        body: JSON.stringify({ execution_mode: newMode }),
      }
    );
    const modeLabels = { ask: "审批", plan: "规划", accept_edits: "自动改", auto: "全自动" };
    showToast(`模式: ${modeLabels[newMode] || newMode}`, "success");
    await loadSessions();
  } catch (err) {
    console.error("更新权限模式失败:", err);
  }
}

async function handleModelChange(newModel) {
  if (!state.selectedSessionId || !newModel) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/meta`,
      {
        method: "PATCH",
        body: JSON.stringify({ model_name: newModel }),
      }
    );
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (sess) sess.model_name = newModel;
    showToast(`模型: ${newModel}`, "success");
  } catch (err) {
    console.error("切换模型失败:", err);
  }
}

async function handleTogglePin(unifiedId, currentPinned, ev) {
  if (ev) ev.stopPropagation();
  try {
    await apiFetch(`/api/v1/sessions/${encodeURIComponent(unifiedId)}/meta`, {
      method: "PATCH",
      body: JSON.stringify({ is_pinned: currentPinned ? 0 : 1 }),
    });
    await loadSessions();
  } catch (err) {
    console.error("切换置顶状态失败:", err);
  }
}

async function submitApprovalDecision(unifiedId, decision, feedback = "") {
  try {
    const payload = { decision };
    if (feedback && feedback.trim()) {
      payload.feedback = feedback.trim();
    }
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(unifiedId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
    state.denyFeedbackOpen = false;
    state.denyFeedbackText = "";
    const labelMap = {
      approved: "已允许执行",
      approve_session_edits: "本会话已免审",
      denied: feedback ? "已拒绝并发送指导意见" : "已拒绝执行",
    };
    showToast(labelMap[decision] || "已提交", "success");
    await loadSessions();
  } catch (err) {
    console.error("提交审批决定失败:", err);
  }
}

// --- 高危命令长按 0.8s 确认 ---
function startHoldToApprove(unifiedId, btnEl) {
  const fillEl = btnEl.querySelector(".hold-progress-fill");
  holdStartTime = performance.now();
  const duration = 800;

  if (holdTimer) cancelAnimationFrame(holdTimer);

  function tick(now) {
    const elapsed = now - holdStartTime;
    const pct = Math.min(100, (elapsed / duration) * 100);
    if (fillEl) fillEl.style.width = `${pct}%`;

    if (pct >= 100) {
      if (fillEl) fillEl.style.width = "0%";
      if ("vibrate" in navigator) {
        try {
          navigator.vibrate(80);
        } catch (_) {}
      }
      submitApprovalDecision(unifiedId, "approved");
      return;
    }
    holdTimer = requestAnimationFrame(tick);
  }

  holdTimer = requestAnimationFrame(tick);
}

function cancelHoldToApprove(btnEl) {
  if (holdTimer) {
    cancelAnimationFrame(holdTimer);
    holdTimer = null;
  }
  const fillEl = btnEl ? btnEl.querySelector(".hold-progress-fill") : null;
  if (fillEl) fillEl.style.width = "0%";
}

// --- 6. 工具与渲染函数 ---
function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatRelativeTime(isoStr) {
  if (!isoStr) return "";
  const dt = new Date(isoStr);
  if (isNaN(dt.getTime())) return isoStr.slice(5, 16);
  const diffSec = Math.floor((Date.now() - dt.getTime()) / 1000);
  if (diffSec < 60) return "刚刚";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
  return `${Math.floor(diffSec / 86400)}d`;
}

function getProjectInfo(rawPath) {
  const clean = String(rawPath || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (!clean) {
    return { key: "", name: "未分类", path: "" };
  }
  const parts = clean.split("/").filter(Boolean);
  const name = parts[parts.length - 1] || clean;
  return { key: clean.toLowerCase(), name, path: clean };
}

function renderHeaderStatus() {
  const netBadge = document.getElementById("network-status-badge");
  const reconnectBanner = document.getElementById("reconnect-banner");
  if (!netBadge) return;

  if (state.wsConnected) {
    const tsOnline = state.systemStatus?.tailscale?.is_online;
    netBadge.innerHTML = `<span class="w-2 h-2 rounded-full ${
      tsOnline ? "bg-emerald-400" : "bg-sky-400"
    }"></span>`;
    if (reconnectBanner) reconnectBanner.classList.add("hidden");
  } else {
    netBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>`;
    if (reconnectBanner) reconnectBanner.classList.remove("hidden");
  }
}

function renderCompactSessionItem(s, showProjectTag = true) {
  const meta = AGENT_META[s.agent_type] || AGENT_META.claude_code;
  const isSelected = s.unified_id === state.selectedSessionId;

  let dotCls = "bg-slate-600";
  if (s.status === "waiting_approval") {
    dotCls = "bg-amber-400 animate-ping";
  } else if (s.status === "running") {
    dotCls = "bg-emerald-400 animate-pulse";
  } else if (s.status === "desktop_active") {
    dotCls = "bg-purple-400 animate-pulse";
  }

  return `
    <div data-session-item="${escapeHtml(s.unified_id)}" onclick="window.appActions.selectSession('${escapeHtml(s.unified_id)}')"
      class="group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 ${
        isSelected
          ? "bg-slate-800/95 border-sky-500/50 text-slate-100"
          : "bg-slate-900/45 border-transparent hover:bg-slate-900 hover:border-slate-800 text-slate-300"
      }">
      <div class="flex items-center gap-1.5 min-w-0 flex-1">
        <span class="w-1.5 h-1.5 rounded-full shrink-0 ${dotCls}"></span>
        <span class="px-1 py-0.5 text-[9px] font-semibold rounded border shrink-0 ${meta.badgeClass}">
          ${meta.short}
        </span>
        <span class="text-xs truncate font-medium">${escapeHtml(s.title)}</span>
      </div>
      <div class="flex items-center gap-1 shrink-0">
        ${
          showProjectTag && s.is_project && s.project_name
            ? `<span class="text-[9px] text-slate-500 font-mono max-w-[68px] truncate hidden sm:inline">${escapeHtml(
                s.project_name
              )}</span>`
            : ""
        }
        <span class="text-[10px] text-slate-500 tabular-nums">${formatRelativeTime(s.updated_at)}</span>
        <button onclick="window.appActions.togglePin('${escapeHtml(s.unified_id)}', ${s.is_pinned ? 1 : 0}, event)"
          class="p-0.5 rounded hover:bg-slate-700/60 text-[11px] ${
            s.is_pinned ? "text-amber-400" : "text-slate-600 opacity-0 group-hover:opacity-100"
          }" title="置顶">
          ★
        </button>
      </div>
    </div>
  `;
}

/**
 * 参照 Antigravity-Deck (app-sidebar.tsx & project-group.tsx) 及 /grill-me 共识：
 * 左侧边栏分为：
 * 1. ★ 置顶 (Pinned)
 * 2. 📁 项目 (Projects) —— 仅收录含 .git/pyproject.toml/pom.xml 等工程特征或手动添加的真正项目，支持「＋ 新建」与「× 移出项目」
 * 3. 💬 临时与最近对话 (Conversations) —— 收纳 Home/System32/Downloads 等散落临时会话与最近对话，自动隐藏无意义噪音目录名
 */
function renderSessionList() {
  const container = document.getElementById("session-list-container");
  if (!container) return;

  const q = state.searchQuery.trim().toLowerCase();
  const sourceList = state.allSessions.length > 0 ? state.allSessions : state.sessions;
  const filtered = sourceList.filter((s) => {
    if (state.activeFilter !== "all" && s.agent_type !== state.activeFilter) {
      return false;
    }
    if (!q) return true;
    return (
      (s.title || "").toLowerCase().includes(q) ||
      (s.preview || "").toLowerCase().includes(q) ||
      (s.project_name || "").toLowerCase().includes(q)
    );
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="p-6 text-center text-slate-500 text-xs">
        <p class="mb-2">无匹配对话</p>
        <button onclick="window.appActions.openNewSessionModal()"
          class="px-2.5 py-1 rounded-lg bg-sky-500/20 text-sky-300 border border-sky-500/30 text-xs">
          + 新建对话
        </button>
      </div>
    `;
    return;
  }

  // 1. 置顶对话
  const pinnedList = filtered.filter((s) => s.is_pinned);

  // 2. 按真正工程项目 (is_project === true) 聚合
  const projectMap = new Map();
  for (const ws of state.workspaces) {
    const info = getProjectInfo(ws.workspace_path);
    if (info.key && !projectMap.has(info.key)) {
      projectMap.set(info.key, {
        key: info.key,
        name: ws.workspace_name || info.name,
        path: info.path,
        sessions: [],
      });
    }
  }
  for (const s of filtered) {
    if (!s.is_project) continue;
    const info = getProjectInfo(s.project_path);
    if (!info.key) continue;
    if (!projectMap.has(info.key)) {
      projectMap.set(info.key, {
        key: info.key,
        name: s.project_name || info.name,
        path: info.path,
        sessions: [],
      });
    }
    projectMap.get(info.key).sessions.push(s);
  }

  const projects = Array.from(projectMap.values()).filter(
    (p) => p.sessions.length > 0 || !q
  );

  // 首次加载默认展开第一个有对话的项目
  if (!state.didInitExpand && projects.length > 0) {
    const firstWithConvs = projects.find((p) => p.sessions.length > 0) || projects[0];
    state.didInitExpand = true;
    state.expandedProjects[firstWithConvs.key] = true;
  }

  // 3. 临时与最近对话（优先展示非项目的散落临时对话 + 最近活跃对话）
  const looseList = filtered.filter((s) => !s.is_project);
  const recentDisplayList = (looseList.length > 0 ? looseList : filtered).slice(0, 15);

  let html = "";

  // --- 区块 1：置顶 ---
  if (pinnedList.length > 0) {
    html += `
      <div>
        <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
          <span>★ 置顶</span>
          <span>${pinnedList.length}</span>
        </div>
        <div class="space-y-0.5">
          ${pinnedList.map((s) => renderCompactSessionItem(s, true)).join("")}
        </div>
      </div>
    `;
  }

  // --- 区块 2：项目 (Projects) ---
  html += `
    <div>
      <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
        <span>📁 项目</span>
        <button onclick="window.appActions.openNewSessionModal()" class="text-slate-400 hover:text-sky-400 text-xs" title="添加项目或新建对话">＋</button>
      </div>
      <div class="space-y-1">
        ${
          projects.length === 0
            ? `<div class="px-2 py-2 text-[11px] text-slate-600">暂无工程项目</div>`
            : projects
                .map((proj) => {
                  const isOpen = !!state.expandedProjects[proj.key] || !!q;
                  const visibleConvs = proj.sessions.slice(0, 8);
                  return `
                    <div class="group/proj rounded-lg bg-slate-900/35 border border-slate-900">
                      <div onclick="window.appActions.toggleProject('${escapeHtml(proj.key)}')"
                        class="cursor-pointer px-2 py-1.5 flex items-center justify-between gap-1.5 rounded-lg hover:bg-slate-900 text-xs text-slate-200">
                        <div class="flex items-center gap-1.5 min-w-0 flex-1">
                          <span class="text-[10px] text-slate-500 transition-transform ${
                            isOpen ? "rotate-90" : ""
                          }">▸</span>
                          <span class="text-sky-400/90 shrink-0">📂</span>
                          <span class="truncate font-medium">${escapeHtml(proj.name)}</span>
                        </div>
                        <div class="flex items-center gap-1 shrink-0">
                          <button onclick="window.appActions.newProjectSession('${escapeHtml(
                            proj.path
                          )}', event)"
                            class="w-4 h-4 rounded hover:bg-slate-800 text-slate-400 hover:text-sky-400 flex items-center justify-center text-xs"
                            title="在此项目新建对话">＋</button>
                          <button onclick="window.appActions.hideProject('${escapeHtml(
                            proj.path
                          )}', event)"
                            class="w-4 h-4 rounded hover:bg-rose-500/20 text-slate-500 hover:text-rose-400 flex items-center justify-center text-[11px]"
                            title="从项目列表移出 (不删对话)">×</button>
                          <span class="text-[10px] text-slate-500 tabular-nums min-w-[14px] text-right">${
                            proj.sessions.length
                          }</span>
                        </div>
                      </div>
                      ${
                        isOpen
                          ? `<div class="pl-3 pr-1 pb-1 space-y-0.5 border-l border-slate-800/80 ml-3 mb-1">
                              ${
                                visibleConvs.length === 0
                                  ? `<div class="px-2 py-1 text-[10px] text-slate-600 italic">无对话</div>`
                                  : visibleConvs
                                      .map((c) => renderCompactSessionItem(c, false))
                                      .join("")
                              }
                            </div>`
                          : ""
                      }
                    </div>
                  `;
                })
                .join("")
        }
      </div>
    </div>
  `;

  // --- 区块 3：临时与最近对话 (Conversations) ---
  html += `
    <div>
      <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500 flex items-center justify-between">
        <span>💬 临时与最近对话</span>
        <span>${recentDisplayList.length}</span>
      </div>
      <div class="space-y-0.5">
        ${recentDisplayList.map((s) => renderCompactSessionItem(s, true)).join("")}
      </div>
    </div>
  `;

  container.innerHTML = html;
}

function renderModelOptionsForAgent(selectEl, agentType, selectedModelId = "") {
  if (!selectEl) return;
  const models =
    state.modelsByAgent[agentType] ||
    DEFAULT_MODELS_BY_AGENT[agentType] ||
    DEFAULT_MODELS_BY_AGENT.agy_cli;

  const activeId = selectedModelId || (models[0] ? models[0].id : "");
  selectEl.innerHTML = models
    .map((m) => {
      const tagSuffix = m.tag ? ` · ${m.tag}` : "";
      const sel = m.id === activeId ? "selected" : "";
      return `<option value="${escapeHtml(m.id)}" ${sel}>${escapeHtml(
        m.label + tagSuffix
      )}</option>`;
    })
    .join("");
}

function renderCurrentSessionHeader() {
  const titleEl = document.getElementById("current-session-title");
  const subtitleEl = document.getElementById("current-session-subtitle");
  const modeSelect = document.getElementById("execution-mode-select");
  const modelSelect = document.getElementById("model-select");
  const abortBtn = document.getElementById("abort-session-btn");
  const splitBrainBanner = document.getElementById("split-brain-banner");

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess) {
    if (titleEl) titleEl.textContent = "选择或新建对话";
    if (subtitleEl) subtitleEl.textContent = "AGY · Claude · OpenCode";
    if (abortBtn) abortBtn.classList.add("hidden");
    if (splitBrainBanner) splitBrainBanner.classList.add("hidden");
    renderModelOptionsForAgent(modelSelect, "agy_cli", "");
    return;
  }

  const meta = AGENT_META[sess.agent_type] || AGENT_META.claude_code;
  const proj = getProjectInfo(sess.project_path);

  if (titleEl) {
    titleEl.innerHTML = `
      <span class="px-1.5 py-0.5 text-[10px] font-semibold rounded border shrink-0 ${meta.badgeClass}">
        ${meta.short}
      </span>
      <span class="truncate">${escapeHtml(sess.title)}</span>
    `;
  }
  if (subtitleEl) {
    subtitleEl.textContent =
      sess.is_project && sess.project_name
        ? `📁 ${sess.project_name}`
        : `临时对话 · #${sess.raw_session_id.slice(0, 8)}`;
  }
  if (modeSelect) {
    modeSelect.value = sess.execution_mode || "ask";
  }
  if (modelSelect) {
    renderModelOptionsForAgent(modelSelect, sess.agent_type, sess.model_name);
  }
  if (abortBtn) {
    if (sess.status === "running" || sess.status === "waiting_approval") {
      abortBtn.classList.remove("hidden");
    } else {
      abortBtn.classList.add("hidden");
    }
  }
  if (splitBrainBanner) {
    if (sess.agent_type === "agy_ide") {
      splitBrainBanner.classList.remove("hidden");
    } else {
      splitBrainBanner.classList.add("hidden");
    }
  }
}

function escapeJsSingleQuote(str) {
  return String(str || "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/\r/g, "")
    .replace(/\n/g, "\\n");
}

function getBasename(rawPath) {
  const clean = String(rawPath || "")
    .split("#")[0]
    .split("?")[0]
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
  const parts = clean.split("/").filter(Boolean);
  return parts[parts.length - 1] || clean || "文档";
}

function isLocalPreviewableLink(href) {
  const h = String(href || "").trim();
  if (!h) return false;
  if (h.startsWith("file://")) return true;
  if (/^[a-zA-Z]:[\\/]/.test(h)) return true;
  if (/\.(md|markdown|mdx|py|js|ts|tsx|json|yaml|yml|toml|html|css|sh|sql|txt)(#L\d+.*)?$/i.test(h) && !/^https?:\/\//i.test(h)) {
    return true;
  }
  return false;
}

/**
 * 轻量级零依赖代码语法着色（防 XSS：先 escapeHtml，再对单行注释/字符串/关键字/数字着色）
 */
function highlightCodeHtml(rawCode) {
  const escaped = escapeHtml(rawCode);
  const tokens = [];
  const putToken = (htmlSnippet) => {
    const id = `\x00TOK_${tokens.length}\x00`;
    tokens.push(htmlSnippet);
    return id;
  };

  // 1. 提取注释与字符串防止内部被关键字正则误匹配
  let work = escaped.replace(
    /(\/\/[^\n]*|#[^\n]*|&quot;(?:\\.|[^&])*?&quot;|'(?:\\.|[^'\n])*')/g,
    (m) => {
      if (m.startsWith("//") || m.startsWith("#")) {
        return putToken(`<span class="tok-cmt">${m}</span>`);
      }
      return putToken(`<span class="tok-str">${m}</span>`);
    }
  );

  // 2. 常见编程语言关键字
  work = work.replace(
    /\b(def|class|return|import|from|as|if|elif|else|for|while|try|except|finally|with|async|await|True|False|None|function|const|let|var|export|default|interface|type|struct|func|package|select|where|insert|update|delete)\b/g,
    '<span class="tok-kw">$1</span>'
  );

  // 3. 数字常量
  work = work.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');

  // 4. 还原字符串与注释 Token
  return work.replace(/\x00TOK_(\d+)\x00/g, (_, idx) => tokens[Number(idx)] || "");
}

/**
 * 行内 Markdown 渲染：支持行内代码 `code`、本地文件预览胶囊 `[label](file://...)`（兼容含空格路径与反引号包裹符号名）、外部链接、加粗、斜体、删除线
 */
function renderInlineMarkdown(rawLine) {
  let text = String(rawLine || "");

  // 1. 先抽离 Markdown 链接 [label](href)，兼容 file:///d:/Development Files/... 等含空格路径与 [`sym`](file://...)
  const links = [];
  text = text.replace(
    /\[([^\]]+)\]\((file:\/\/\/[^\n)]+|https?:\/\/[^\s)]+|conversation:\/\/[^\s)]+|[^)\n]+)\)/g,
    (_, label, rawHref) => {
      const idx = links.length;
      const href = rawHref.trim();
      const cleanLabel = label.replace(/`/g, "").trim() || getBasename(href);
      if (isLocalPreviewableLink(href)) {
        links.push(
          `<button type="button" onclick="window.appActions.openFilePreview('${escapeJsSingleQuote(
            href
          )}')" class="md-file-chip" title="点击在手机端全屏阅读 ${escapeHtml(href)}">📄 ${escapeHtml(
            cleanLabel
          )}</button>`
        );
      } else if (href.startsWith("conversation://")) {
        links.push(
          `<span class="md-inline-code" title="${escapeHtml(href)}">💬 ${escapeHtml(cleanLabel)}</span>`
        );
      } else if (/^https?:\/\//i.test(href)) {
        links.push(
          `<a href="${escapeHtml(
            href
          )}" target="_blank" rel="noopener noreferrer" class="text-sky-400 underline underline-offset-2 hover:text-sky-300">${escapeHtml(
            cleanLabel
          )} ↗</a>`
        );
      } else {
        links.push(`<span class="text-sky-300">${escapeHtml(cleanLabel)}</span>`);
      }
      return `\x00MDLINK_${idx}\x00`;
    }
  );

  // 2. 抽离行内代码 `...`
  const inlineCodes = [];
  text = text.replace(/`([^`\n]+)`/g, (_, codeContent) => {
    const idx = inlineCodes.length;
    inlineCodes.push(`<code class="md-inline-code">${escapeHtml(codeContent)}</code>`);
    return `\x00INLCODE_${idx}\x00`;
  });

  // 3. 转义剩余普通文本
  text = escapeHtml(text);

  // 4. 加粗 **...**、斜体 *...*、删除线 ~~...~~
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/~~([^~]+)~~/g, '<del class="text-slate-500">$1</del>');
  text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");

  // 5. 还原行内代码与链接
  text = text.replace(/\x00INLCODE_(\d+)\x00/g, (_, i) => inlineCodes[Number(i)] || "");
  text = text.replace(/\x00MDLINK_(\d+)\x00/g, (_, i) => links[Number(i)] || "");
  return text;
}

/**
 * 完整 GFM Markdown 文档流渲染引擎：
 * - 支持标题、段落、多级列表、任务复选框
 * - 支持 GFM 表格（自动外包横向滚动容器 .md-table-wrap）
 * - 支持 GitHub Alerts (> [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING] / [!CAUTION])
 * - 支持围栏代码块（带语言头、一键复制、超过 15 行自动折叠与展开按钮）
 */
function renderMarkdownHtml(rawMd, scopeKey = "md") {
  if (!rawMd) return "";
  let text = String(rawMd).replace(/\r\n/g, "\n").trim();
  if (!text) return "";

  const codeBlocks = [];
  const storeCodeBlock = (lang, codeContent) => {
    const idx = codeBlocks.length;
    const codeId = `${scopeKey}_cb_${idx}`;
    const cleanCode = String(codeContent || "").replace(/\n$/, "");
    state.codeBlockStore[codeId] = cleanCode;

    const lines = cleanCode.split("\n");
    const lineCount = lines.length;
    const isLong = lineCount > 15;
    const isExpanded = !!state.expandedCodeBlocks[codeId];
    const shouldCollapse = isLong && !isExpanded;
    const displayLang = (lang || "text").trim().toLowerCase() || "code";

    const cardHtml = `
      <div class="md-code-card">
        <div class="md-code-header">
          <div class="flex items-center gap-2">
            <span class="text-sky-400 font-semibold uppercase">${escapeHtml(displayLang)}</span>
            <span class="text-[10px] text-slate-500">${lineCount} 行</span>
          </div>
          <button type="button" onclick="window.appActions.copyCodeBlock('${escapeJsSingleQuote(
            codeId
          )}', this)"
            class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] transition active:scale-95">
            📋 复制
          </button>
        </div>
        <div id="code-body-${escapeHtml(codeId)}" class="md-code-body ${
      shouldCollapse ? "collapsed" : ""
    }">${highlightCodeHtml(cleanCode)}${
      shouldCollapse ? `<div class="md-code-fade"></div>` : ""
    }</div>
        ${
          isLong
            ? `<button id="code-btn-${escapeHtml(codeId)}" type="button" data-line-count="${lineCount}" onclick="window.appActions.toggleCodeBlock('${escapeJsSingleQuote(
                codeId
              )}')"
                class="w-full py-1.5 bg-slate-900/95 hover:bg-slate-800/90 border-t border-slate-800/80 text-sky-400 text-[11px] font-medium text-center transition">
                ${isExpanded ? "⬆ 收起代码" : `⬇ 展开全部代码 (共 ${lineCount} 行)`}
              </button>`
            : ""
        }
      </div>
    `;
    codeBlocks.push(cardHtml);
    return `\n\x00CODEBLOCK_${idx}\x00\n`;
  };

  // 提取 4 反引号代码块与 3 反引号代码块（兼容行首缩进、特殊语言标识及无语言标识）
  text = text.replace(/^[ \t]*````([^\n]*)\n([\s\S]*?)^[ \t]*````[ \t]*$/gm, (_, lang, code) =>
    storeCodeBlock(lang, code)
  );
  text = text.replace(/^[ \t]*```([^\n]*)\n([\s\S]*?)^[ \t]*```[ \t]*$/gm, (_, lang, code) =>
    storeCodeBlock(lang, code)
  );

  const lines = text.split("\n");
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i++;
      continue;
    }

    // 1. 代码块占位符
    const cbMatch = trimmed.match(/^\x00CODEBLOCK_(\d+)\x00$/);
    if (cbMatch) {
      out.push(codeBlocks[Number(cbMatch[1])] || "");
      i++;
      continue;
    }

    // 2. 水平分割线
    if (/^(?:---|\*\*\*|___)\s*$/.test(trimmed)) {
      out.push("<hr />");
      i++;
      continue;
    }

    // 3. 标题 H1 ~ H6
    const hMatch = line.match(/^\s*(#{1,6})\s+(.+)$/);
    if (hMatch) {
      const level = Math.min(4, hMatch[1].length);
      out.push(`<h${level}>${renderInlineMarkdown(hMatch[2].trim())}</h${level}>`);
      i++;
      continue;
    }

    // 4. 引用块与 GitHub Alert (> [!NOTE] 等)
    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      const firstLine = (quoteLines[0] || "").trim();
      const alertMatch = firstLine.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
      if (alertMatch) {
        const kind = alertMatch[1].toLowerCase();
        const inlineRest = alertMatch[2].trim();
        const alertMeta = {
          note: { icon: "ℹ️", label: "说明" },
          tip: { icon: "💡", label: "提示" },
          important: { icon: "🟣", label: "重要" },
          warning: { icon: "⚠️", label: "警告" },
          caution: { icon: "🚨", label: "注意" },
        }[kind] || { icon: "ℹ️", label: "提示" };

        const bodyLines = inlineRest ? [inlineRest, ...quoteLines.slice(1)] : quoteLines.slice(1);
        const bodyHtml = bodyLines
          .filter((l) => l.trim())
          .map((l) => `<div>${renderInlineMarkdown(l)}</div>`)
          .join("");
        out.push(`
          <div class="md-alert md-alert-${kind}">
            <div class="md-alert-title"><span>${alertMeta.icon}</span><span>${alertMeta.label}</span></div>
            <div class="space-y-1 text-slate-200">${bodyHtml}</div>
          </div>
        `);
      } else {
        const qBody = quoteLines
          .map((l) => (l.trim() ? renderInlineMarkdown(l) : "<br />"))
          .join("<br />");
        out.push(`<blockquote class="md-blockquote">${qBody}</blockquote>`);
      }
      continue;
    }

    // 5. GFM 表格 (| col1 | col2 | 且下一行为 | --- | --- |)
    if (
      trimmed.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1].trim()) &&
      lines[i + 1].includes("-")
    ) {
      const parseTableRow = (rStr) =>
        rStr
          .trim()
          .replace(/^\|/, "")
          .replace(/\|$/, "")
          .split("|")
          .map((c) => c.trim());

      const headers = parseTableRow(lines[i]);
      i += 2; // 跳过表头与分隔线
      const rows = [];
      while (i < lines.length && lines[i].trim().includes("|") && lines[i].trim() !== "") {
        rows.push(parseTableRow(lines[i]));
        i++;
      }

      const thHtml = headers.map((h) => `<th>${renderInlineMarkdown(h)}</th>`).join("");
      const trsHtml = rows
        .map(
          (r) =>
            `<tr>${headers
              .map((_, colIdx) => `<td>${renderInlineMarkdown(r[colIdx] || "")}</td>`)
              .join("")}</tr>`
        )
        .join("");

      out.push(`
        <div class="md-table-wrap">
          <table class="md-table">
            <thead><tr>${thHtml}</tr></thead>
            <tbody>${trsHtml}</tbody>
          </table>
        </div>
      `);
      continue;
    }

    // 6. 无序或有序列表（支持多级缩进子列表，防止子列表冲乱外层有序列表序号）
    if (/^\s*(?:[-*+]|\d+\.)\s+/.test(line)) {
      const isOrdered = /^\s*\d+\.\s+/.test(line);
      const tag = isOrdered ? "ol" : "ul";
      const itemsHtml = [];

      while (i < lines.length && /^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i])) {
        const curLine = lines[i];
        const indentLen = (curLine.match(/^\s*/) || [""])[0].length;
        const rawItem = curLine.replace(/^\s*(?:[-*+]|\d+\.)\s+/, "");
        const taskMatch = rawItem.match(/^\[([ xX])\]\s+(.*)$/);
        if (taskMatch) {
          const checked = taskMatch[1].toLowerCase() === "x";
          itemsHtml.push(
            `<li class="list-none -ml-4 flex items-start gap-1.5"><span>${
              checked ? "☑️" : "⬜"
            }</span><span class="${checked ? "line-through text-slate-400" : ""}">${renderInlineMarkdown(
              taskMatch[2]
            )}</span></li>`
          );
        } else if (indentLen >= 2) {
          itemsHtml.push(
            `<li class="list-none ml-3 flex items-start gap-1.5 text-slate-300"><span class="text-sky-400/80 select-none">•</span><span class="flex-1">${renderInlineMarkdown(
              rawItem
            )}</span></li>`
          );
        } else {
          itemsHtml.push(`<li>${renderInlineMarkdown(rawItem)}</li>`);
        }
        i++;
      }
      out.push(`<${tag}>${itemsHtml.join("")}</${tag}>`);
      continue;
    }

    // 7. 普通段落（合并连续普通文本行）
    const paraLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^\x00CODEBLOCK_\d+\x00$/.test(lines[i].trim()) &&
      !/^(?:---|\*\*\*|___)\s*$/.test(lines[i].trim()) &&
      !/^\s*#{1,6}\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*(?:[-*+]|\d+\.)\s+/.test(lines[i]) &&
      !(
        lines[i].trim().includes("|") &&
        i + 1 < lines.length &&
        /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1].trim()) &&
        lines[i + 1].includes("-")
      )
    ) {
      paraLines.push(renderInlineMarkdown(lines[i]));
      i++;
    }
    if (paraLines.length > 0) {
      out.push(`<p>${paraLines.join("<br />")}</p>`);
    }
  }

  return `<div class="md-prose">${out.join("")}</div>`;
}

function renderUnifiedDiffHtml(diffText) {
  if (!diffText) return "";
  const lines = diffText.split("\n");
  const renderedLines = lines
    .map((line) => {
      let cls = "diff-line text-slate-300";
      if (line.startsWith("+++") || line.startsWith("---")) {
        cls = "diff-line text-slate-400 font-semibold";
      } else if (line.startsWith("@@")) {
        cls = "diff-line diff-line-hunk";
      } else if (line.startsWith("+")) {
        cls = "diff-line diff-line-add";
      } else if (line.startsWith("-")) {
        cls = "diff-line diff-line-del";
      }
      return `<div class="${cls}">${escapeHtml(line)}</div>`;
    })
    .join("");

  return `
    <div class="mt-1.5">
      <div class="flex items-center justify-between text-[10px] text-slate-400 mb-1">
        <span>📄 代码变更 Diff</span>
        <button type="button" onclick="window.appActions.toggleWordWrap()" class="text-sky-400 hover:underline">
          ${state.wordWrapDiff ? "横向滚动" : "自动换行"}
        </button>
      </div>
      <div class="unified-diff-box ${state.wordWrapDiff ? "word-wrap" : ""} max-h-60 overflow-y-auto">
        ${renderedLines}
      </div>
    </div>
  `;
}

/**
 * 将平铺的 UnifiedMessageItem[] 按轮次（Turn）聚合：
 * 遵循 /grill-me 共识：
 * 1. 同一轮问答内，把中间的思考 (Thinking)、所有工具调用 (含 Diff) 以及调用工具前的过渡短句统一收纳进顶部单个可折叠的「⚡ 执行过程」进度栏。
 * 2. 主界面仅直接展示用户的提问与 Agent 该轮最终完整的核心回复正文。
 * 3. 自动识别本轮新建/更新或引用的 .md 文档（如 implementation_plan.md 等），生成「📄 点击阅读文档」入口卡片。
 */
function groupMessagesIntoTurns(messages) {
  const turns = [];
  let i = 0;

  while (i < messages.length) {
    const m = messages[i];
    if (m.role === "user") {
      turns.push({ type: "user", item: m });
      i++;
      continue;
    }

    // 收集紧随其后的连续 assistant 步骤，合并为一个回答轮次 (Assistant Turn)
    const assistantSteps = [];
    while (i < messages.length && messages[i].role !== "user") {
      assistantSteps.push(messages[i]);
      i++;
    }

    const firstStep = assistantSteps[0];
    const lastStep = assistantSteps[assistantSteps.length - 1];
    const turnKey = `${state.selectedSessionId || "sess"}_turn_${firstStep.step_index}`;

    // 找到最后一个包含非空 content 的步骤下标
    let lastContentIdx = -1;
    for (let idx = assistantSteps.length - 1; idx >= 0; idx--) {
      if ((assistantSteps[idx].content || "").trim()) {
        lastContentIdx = idx;
        break;
      }
    }

    // 划分：主回复正文 vs 收纳进折叠栏的中间过程步骤
    const primarySteps = [];
    const processRecords = [];
    const turnQuestions = [];
    let thinkingCount = 0;
    let toolCount = 0;
    let hasHighRisk = false;
    const editedFilesSet = new Set();
    const mdFilesMap = new Map(); // path -> basename

    const recordMdCandidate = (rawPath) => {
      if (!rawPath) return;
      const clean = String(rawPath).split("#")[0].split("?")[0].trim();
      if (/\.(md|markdown|mdx)$/i.test(clean)) {
        const base = getBasename(clean);
        if (base.toLowerCase() !== "skill.md") {
          mdFilesMap.set(clean, base);
        }
      }
    };

    assistantSteps.forEach((step, idx) => {
      if (Array.isArray(step.questions) && step.questions.length > 0) {
        turnQuestions.push(...step.questions);
      }
      const hasThinking = !!(step.thinking && step.thinking.trim());
      const tools = Array.isArray(step.tool_calls) ? step.tool_calls : [];
      const hasTools = tools.length > 0;
      const text = (step.content || "").trim();

      if (hasThinking) thinkingCount++;
      if (hasTools) {
        toolCount += tools.length;
        for (const tc of tools) {
          if (tc.risk_level === "high") hasHighRisk = true;
          const fPath = tc.file_path || "";
          if (tc.diff_text || /edit|write|replace|create/i.test(tc.name || "")) {
            const targetName = getBasename(fPath || tc.args_summary);
            if (targetName && targetName.length < 60) {
              editedFilesSet.add(targetName);
            }
          }
          recordMdCandidate(fPath);
          if (/\.(md|markdown|mdx)$/i.test(tc.args_summary || "")) {
            recordMdCandidate(tc.args_summary);
          }
        }
      }

      // 扫描正文中引用的 file:///.../*.md 链接（兼容含空格路径）
      if (text) {
        const linkMatches = text.matchAll(/\[[^\]]*\]\((file:\/\/\/[^\n)]+\.md(?:#[^\n)]*)?)\)/gi);
        for (const lm of linkMatches) {
          recordMdCandidate(lm[1]);
        }
      }

      // 判断该 step 的文本是最终主回复还是调用工具前的简短过渡说明：
      // 1. 最后一条非空文本必定作为主回复展示；
      // 2. 若前面某一步本身已是完整 Markdown 报告（>=160字或含代码块/标题/表格，且未同步捆绑工具调用），也保留在主界面，防止末尾一句短补充把主报告挤进折叠栏！
      const isSubstantialReport =
        text.length >= 160 || /```|^#{1,4}\s|\n\|.*\|.*\n/m.test(text);
      const isPrimaryText =
        text && (idx === lastContentIdx || (isSubstantialReport && !hasTools));
      const isIntermediateNote = text && !isPrimaryText;

      if (hasThinking || hasTools || isIntermediateNote) {
        processRecords.push({
          step_index: step.step_index,
          thinking: hasThinking ? step.thinking.trim() : "",
          intermediateNote: isIntermediateNote ? text : "",
          tool_calls: tools,
        });
      }

      if (isPrimaryText) {
        primarySteps.push({
          step_index: step.step_index,
          content: text,
          created_at: step.created_at,
        });
      }
    });

    turns.push({
      type: "assistant_turn",
      turnKey,
      processRecords,
      primarySteps,
      questions: turnQuestions,
      thinkingCount,
      toolCount,
      editedFiles: Array.from(editedFilesSet),
      mdFiles: Array.from(mdFilesMap.entries()).map(([path, name]) => ({ path, name })),
      hasHighRisk,
      createdAt: lastStep.created_at || firstStep.created_at,
      firstStepIndex: firstStep.step_index,
      lastStepIndex: lastStep.step_index,
    });
  }

  return turns;
}

function renderTurnProcessBar(turn) {
  const {
    turnKey,
    processRecords,
    thinkingCount,
    toolCount,
    editedFiles,
    hasHighRisk,
  } = turn;

  if (!processRecords || processRecords.length === 0) return "";
  state.turnsMap[turnKey] = turn;

  const isExpanded = !!state.expandedTurns[turnKey];
  const totalActions =
    toolCount + thinkingCount + processRecords.filter((r) => r.intermediateNote).length;

  // 构建折叠栏上的精炼徽章
  const badges = [];
  if (editedFiles.length > 0) {
    const fileHint =
      editedFiles.length === 1
        ? `✏️ 修改 ${editedFiles[0]}`
        : `✏️ 修改 ${editedFiles.length} 个文件`;
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[10px] font-medium shrink-0">${escapeHtml(
        fileHint
      )}</span>`
    );
  }
  if (hasHighRisk) {
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/35 text-[10px] font-semibold shrink-0">⚠️ 含高危操作</span>`
    );
  }
  if (toolCount > 0) {
    badges.push(`<span class="text-[11px] text-slate-400">🔧 ${toolCount} 次调用</span>`);
  }
  if (thinkingCount > 0) {
    badges.push(`<span class="text-[11px] text-slate-400">💭 ${thinkingCount} 次思考</span>`);
  }

  const detailsHtml = isExpanded
    ? `<div class="mt-2 pt-2 border-t border-slate-800/80 space-y-2">
        ${processRecords
          .map((rec) => {
            const thinkDetailId = `${turnKey}_think_${rec.step_index}`;
            const isThinkOpen = !!state.expandedDetails[thinkDetailId];
            if (rec.thinking) {
              state.lazyDetailsStore[thinkDetailId] = {
                type: "thinking",
                raw: rec.thinking,
              };
            }
            const thinkHtml = rec.thinking
              ? `
                <details data-detail-id="${escapeHtml(thinkDetailId)}" ${
                  isThinkOpen ? "open" : ""
                } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                  thinkDetailId
                )}')" class="rounded-lg bg-slate-950/80 border border-slate-800/80 px-2.5 py-1.5 text-[11px] text-slate-400">
                  <summary class="cursor-pointer select-none font-medium text-slate-300">
                    💭 思考过程 (${rec.thinking.length}字)
                  </summary>
                  <div data-lazy-slot class="mt-1.5 pt-1.5 border-t border-slate-800/70 whitespace-pre-wrap leading-relaxed text-slate-400 font-mono text-[11px] max-h-52 overflow-y-auto">${
                    isThinkOpen ? escapeHtml(rec.thinking) : ""
                  }</div>
                </details>
              `
              : "";

            const noteHtml = rec.intermediateNote
              ? `
                <div class="px-2.5 py-1.5 rounded-lg bg-slate-900/70 border border-slate-800/60 text-xs text-slate-300">
                  <span class="text-[10px] text-sky-400 font-semibold mr-1">💬 过程说明:</span>
                  ${escapeHtml(rec.intermediateNote)}
                </div>
              `
              : "";

            const toolsHtml = (rec.tool_calls || [])
              .map((tc, tcIdx) => {
                const isHigh = tc.risk_level === "high";
                const diffDetailId = `${turnKey}_diff_${rec.step_index}_${tcIdx}`;
                const isDiffOpen = !!state.expandedDetails[diffDetailId];
                if (tc.diff_text) {
                  state.lazyDetailsStore[diffDetailId] = {
                    type: "diff",
                    raw: tc.diff_text,
                  };
                }

                const outDetailId = `${turnKey}_out_${rec.step_index}_${tcIdx}`;
                const isOutOpen = !!state.expandedDetails[outDetailId];
                if (tc.output) {
                  state.lazyDetailsStore[outDetailId] = {
                    type: "output",
                    raw: tc.output,
                  };
                }

                const diffLineCount = tc.diff_text ? tc.diff_text.split("\n").length : 0;

                return `
                  <div class="rounded-lg bg-slate-950/90 border ${
                    isHigh ? "border-rose-500/45" : "border-slate-800/90"
                  } p-2 text-xs">
                    <div class="flex items-center justify-between gap-2">
                      <div class="flex items-center gap-1.5 min-w-0">
                        <span class="px-1.5 py-0.5 rounded bg-slate-800 text-sky-300 font-mono font-semibold text-[10px] shrink-0">
                          🔧 ${escapeHtml(tc.name)}
                        </span>
                        <span class="font-mono text-[11px] text-slate-300 truncate">
                          ${escapeHtml(tc.args_summary)}
                        </span>
                      </div>
                      <span class="text-[9px] text-slate-500 font-mono shrink-0">${escapeHtml(
                        tc.status || "DONE"
                      )}</span>
                    </div>
                    ${
                      tc.diff_text
                        ? `
                          <details data-detail-id="${escapeHtml(diffDetailId)}" ${
                            isDiffOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            diffDetailId
                          )}')" class="mt-1.5 text-[10px] text-slate-400">
                            <summary class="cursor-pointer text-emerald-300/90 hover:text-emerald-200 select-none font-medium">
                              📝 查看代码变更 Diff (${diffLineCount}行)
                            </summary>
                            <div data-lazy-slot>${
                              isDiffOpen ? renderUnifiedDiffHtml(tc.diff_text) : ""
                            }</div>
                          </details>
                        `
                        : ""
                    }
                    ${
                      tc.output
                        ? `
                          <details data-detail-id="${escapeHtml(outDetailId)}" ${
                            isOutOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            outDetailId
                          )}')" class="mt-1.5 text-[10px] text-slate-400">
                            <summary class="cursor-pointer text-slate-400 hover:text-slate-200 select-none flex items-center justify-between">
                              <span>📤 查看执行输出 ${
                                tc.is_truncated ? `(${tc.full_output_len}字·已截断)` : ""
                              }</span>
                              ${
                                tc.is_truncated
                                  ? `<button onclick="window.appActions.loadFullOutput('${escapeHtml(
                                      state.selectedSessionId
                                    )}', ${rec.step_index}, this, event)" class="px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30 hover:bg-sky-500/30 text-[9px]">加载完整输出</button>`
                                  : ""
                              }
                            </summary>
                            <pre data-lazy-slot class="mt-1 p-2 rounded bg-black/70 overflow-x-auto font-mono text-[10px] text-slate-300 whitespace-pre-wrap break-all max-h-44 overflow-y-auto">${
                              isOutOpen ? escapeHtml(tc.output) : ""
                            }</pre>
                          </details>
                        `
                        : ""
                    }
                  </div>
                `;
              })
              .join("");

            return `<div class="space-y-1.5">${thinkHtml}${noteHtml}${toolsHtml}</div>`;
          })
          .join("")}
      </div>`
    : "";

  return `
    <div id="turn-process-wrap-${escapeHtml(turnKey)}" class="mb-2.5 rounded-xl bg-slate-900/65 border border-slate-800/85 px-3 py-2">
      <div onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
        class="cursor-pointer select-none flex items-center justify-between gap-2">
        <div class="flex items-center gap-1.5 flex-wrap min-w-0">
          <span class="text-xs font-semibold text-slate-200 flex items-center gap-1 shrink-0">
            <span class="text-sky-400">⚡</span>
            <span>已收纳 ${totalActions} 个执行步骤</span>
          </span>
          ${badges.join("")}
        </div>
        <span class="text-[11px] text-sky-400 font-medium shrink-0">
          ${isExpanded ? "收起 ▴" : "展开 ▾"}
        </span>
      </div>
      ${detailsHtml}
    </div>
  `;
}

function renderQuestionOptionsHtml(turnKey, qi, q) {
  const qKey = `${turnKey}_q${qi}`;
  const selectedOpts = state.questionSelections[qKey] || [];
  const isMulti = !!q.is_multi_select;
  return (q.options || [])
    .map((opt) => {
      const isSelected = selectedOpts.includes(opt);
      return `
        <button type="button"
          onclick="window.appActions.toggleQuestionOption('${escapeJsSingleQuote(
            turnKey
          )}', ${qi}, '${escapeJsSingleQuote(opt)}', ${isMulti})"
          class="text-left text-xs px-2.5 py-1.5 rounded-lg border transition-all active:scale-95 flex items-center gap-1.5 ${
            isSelected
              ? "bg-sky-500 text-slate-950 font-bold border-sky-400 shadow-sm"
              : "bg-slate-900/80 hover:bg-slate-800 text-slate-200 border-slate-700/80"
          }">
          <span class="text-[10px] ${isSelected ? "text-slate-950" : "text-sky-400"}">${
        isSelected ? (isMulti ? "☑" : "●") : isMulti ? "☐" : "○"
      }</span>
          <span class="break-words">${escapeHtml(opt)}</span>
        </button>
      `;
    })
    .join("");
}

function renderInteractiveQuestionsCard(turn, isLatestTurn) {
  const questions = turn.questions || [];
  if (questions.length === 0) return "";
  const turnKey = turn.turnKey;

  // 历史轮次：转为轻量折叠卡片，展示已确认决议与选项，防止侵占手机长流
  if (!isLatestTurn) {
    const decision = state.persistedQuestionDecisions?.[turnKey];
    const summaryTitle = decision
      ? `✓ 已决策：${escapeHtml(decision.answerText.slice(0, 32))}${
          decision.answerText.length > 32 ? "..." : ""
        }`
      : `✓ 已完成的交互问答 (共 ${questions.length} 项)`;

    return `
      <details class="mt-2 rounded-xl bg-slate-950/70 border border-slate-800/80 px-3 py-2 text-xs text-slate-400">
        <summary class="cursor-pointer select-none font-medium text-slate-300 hover:text-sky-300 flex items-center justify-between">
          <span class="${decision ? "text-sky-300 font-semibold" : ""}">${summaryTitle}</span>
          <span class="text-[10px] text-slate-500">查看明细 ▾</span>
        </summary>
        <div class="mt-2 pt-2 border-t border-slate-800/70 space-y-2">
          ${
            decision
              ? `<div class="p-2 rounded-lg bg-sky-500/10 border border-sky-500/30 text-sky-200 text-xs">
                  <span class="font-semibold text-sky-400">答复记录：</span>${escapeHtml(
                    decision.answerText
                  )}
                </div>`
              : ""
          }
          ${questions
            .map(
              (q, qi) => `
                <div>
                  <div class="font-semibold text-slate-200">Q${qi + 1}: ${escapeHtml(q.question)}</div>
                  ${
                    q.options && q.options.length > 0
                      ? `<div class="mt-1 pl-3 text-[11px] text-slate-400 space-y-0.5">
                          ${q.options.map((opt) => `<div>• ${escapeHtml(opt)}</div>`).join("")}
                        </div>`
                      : ""
                  }
                </div>
              `
            )
            .join("")}
        </div>
      </details>
    `;
  }

  const questionsHtml = questions
    .map((q, qi) => {
      const qKey = `${turnKey}_q${qi}`;
      const writeInVal = state.questionWriteIns[qKey] || "";
      const isMulti = !!q.is_multi_select;
      const optsHtml = renderQuestionOptionsHtml(turnKey, qi, q);

      return `
        <div class="space-y-1.5 pt-1.5 first:pt-0">
          <div class="text-xs font-semibold text-slate-100 flex items-start gap-1.5">
            <span class="text-sky-400 shrink-0">Q${qi + 1}:</span>
            <span class="whitespace-pre-wrap">${escapeHtml(q.question)}</span>
            ${
              isMulti
                ? `<span class="px-1 py-0.5 rounded bg-slate-800 text-slate-400 text-[9px] shrink-0">多选</span>`
                : ""
            }
          </div>
          ${optsHtml ? `<div id="q-opts-${escapeHtml(qKey)}" class="flex flex-wrap gap-1.5 pl-4">${optsHtml}</div>` : ""}
          <div class="pl-4">
            <input type="text"
              value="${escapeHtml(writeInVal)}"
              placeholder="${q.options && q.options.length > 0 ? "或在此补充自定义回答..." : "输入您的回答..."}"
              oninput="window.appActions.setQuestionWriteIn('${escapeJsSingleQuote(
                turnKey
              )}', ${qi}, this.value)"
              class="w-full px-2.5 py-1 rounded-lg bg-slate-950 border border-slate-800 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-sky-500" />
          </div>
        </div>
      `;
    })
    .join('<div class="border-t border-slate-800/60 my-2"></div>');

  return `
    <div class="mt-2.5 rounded-xl bg-slate-900/90 border border-sky-500/40 p-3 shadow-lg space-y-2">
      <div class="flex items-center justify-between text-xs text-sky-400 font-semibold border-b border-slate-800 pb-1.5">
        <span class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-sky-400 animate-ping"></span>
          <span>❓ Agent 正在向您提问确认</span>
        </span>
        <span class="text-[10px] text-slate-400">单手点选即答</span>
      </div>
      <div class="space-y-2">${questionsHtml}</div>
      <div class="flex items-center justify-end gap-2 pt-1 border-t border-slate-800/80">
        <button type="button"
          onclick="window.appActions.skipQuestionAnswers('${escapeJsSingleQuote(turnKey)}')"
          class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition">
          按推荐执行
        </button>
        <button type="button"
          onclick="window.appActions.submitQuestionAnswers('${escapeJsSingleQuote(turnKey)}')"
          class="px-3.5 py-1 rounded-lg bg-sky-500 hover:bg-sky-400 text-slate-950 font-bold text-xs shadow transition active:scale-95 flex items-center gap-1">
          <span>✓ 发送回答</span>
        </button>
      </div>
    </div>
  `;
}

function renderMdDocQuickCards(mdFiles) {
  if (!mdFiles || mdFiles.length === 0) return "";
  return `
    <div class="mt-2.5 flex flex-wrap gap-1.5">
      ${mdFiles
        .map(
          (doc) => `
            <button type="button"
              onclick="window.appActions.openFilePreview('${escapeJsSingleQuote(doc.path)}')"
              class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-sky-500/12 hover:bg-sky-500/20 border border-sky-500/35 text-left transition active:scale-95">
              <span class="text-base">📄</span>
              <div class="min-w-0">
                <div class="text-xs font-semibold text-sky-300 truncate">${escapeHtml(doc.name)}</div>
                <div class="text-[10px] text-slate-400">点击全屏阅读 Markdown 文档</div>
              </div>
            </button>
          `
        )
        .join("")}
    </div>
  `;
}

function renderMessagesList() {
  const container = document.getElementById("messages-container");
  if (!container) return;

  if (state.loadingMessages) {
    container.innerHTML = `
      <div class="space-y-3 p-3">
        <div class="skeleton-block h-14 w-2/3 ml-auto"></div>
        <div class="skeleton-block h-24 w-11/12"></div>
        <div class="skeleton-block h-16 w-4/5"></div>
      </div>
    `;
    return;
  }

  if (!state.selectedSessionId || state.messages.length === 0) {
    container.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center p-6 text-center text-slate-400">
        <div class="w-10 h-10 rounded-xl bg-slate-800/80 border border-slate-700/60 flex items-center justify-center text-xl mb-2.5">
          ⚡
        </div>
        <div class="text-sm font-medium text-slate-200 mb-1">选择对话或直接发消息</div>
        <p class="text-xs text-slate-500 max-w-xs mb-3">
          支持 Antigravity IDE / CLI · Claude Code · OpenCode
        </p>
        <button onclick="window.appActions.openNewSessionModal()"
          class="px-3.5 py-1.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-slate-950 font-semibold text-xs transition">
          + 新建对话
        </button>
      </div>
    `;
    return;
  }

  const turns = groupMessagesIntoTurns(state.messages);
  state.turnsMap = {};

  const olderBannerHtml = state.hasMoreMessages
    ? `
      <div class="mb-4 text-center">
        <button id="load-older-messages-btn" type="button" onclick="window.appActions.loadOlderMessages()"
          class="px-3.5 py-1.5 rounded-xl bg-slate-900/90 hover:bg-slate-800 border border-slate-700/80 text-xs text-sky-300 font-medium transition active:scale-95">
          ↑ 加载更早消息 (已显示 ${state.messages.length}/${state.totalMessagesCount || "?"} 条)
        </button>
      </div>
    `
    : "";

  const html = turns
    .map((t) => {
      if (t.type === "user") {
        const m = t.item;
        const rawUserText = String(m.content || "").trim();
        const userLines = rawUserText.split("\n");
        const isLongUser = userLines.length > 8 || rawUserText.length > 360;
        const isUserExpanded = !!state.expandedUserMessages[m.item_id];
        const collapsedUserText = userLines.slice(0, 6).join("\n").slice(0, 280) + "...";
        const displayUserText =
          isLongUser && !isUserExpanded ? collapsedUserText : rawUserText;

        return `
          <div class="flex justify-end mb-4">
            <div class="max-w-[88%] sm:max-w-[76%] rounded-2xl rounded-tr-sm bg-sky-500/20 border border-sky-500/35 px-3.5 py-2.5 text-slate-100 shadow-sm">
              <div id="user-msg-text-${escapeHtml(
                m.item_id
              )}" class="text-[14px] leading-relaxed whitespace-pre-wrap break-words">${escapeHtml(
          displayUserText
        )}</div>
              ${
                isLongUser
                  ? `<button id="user-msg-btn-${escapeHtml(
                      m.item_id
                    )}" type="button" onclick="window.appActions.toggleUserMessage('${escapeJsSingleQuote(
                      m.item_id
                    )}')" class="mt-1 text-[11px] text-sky-300 font-medium hover:underline">
                      ${isUserExpanded ? "收起 ▴" : `展开全部 (${rawUserText.length}字) ▾`}
                    </button>`
                  : ""
              }
              <div class="text-[10px] text-sky-300/60 text-right mt-1">${formatRelativeTime(
                m.created_at
              )}</div>
            </div>
          </div>
        `;
      }

      // Assistant Turn（沉浸式全宽文档流版式）
      const processBarHtml = renderTurnProcessBar(t);
      const mdBodyHtml = t.primarySteps
        .map((ps) => renderMarkdownHtml(ps.content, `${t.turnKey}_s${ps.step_index}`))
        .join('<hr class="my-3 border-slate-800/80" />');

      // 若本轮仅有工具/思考过程而尚无最终文字输出，展示最后一步简短状态说明，杜绝空白
      let fallbackStatusHtml = "";
      if (!mdBodyHtml && t.processRecords.length > 0) {
        const lastRec = t.processRecords[t.processRecords.length - 1];
        if (lastRec.intermediateNote) {
          fallbackStatusHtml = renderMarkdownHtml(lastRec.intermediateNote, `${t.turnKey}_note`);
        } else if (lastRec.tool_calls && lastRec.tool_calls.length > 0) {
          const lastTc = lastRec.tool_calls[lastRec.tool_calls.length - 1];
          fallbackStatusHtml = `
            <div class="text-xs text-slate-400 italic">
              已完成工具调用 <code class="md-inline-code">${escapeHtml(lastTc.name)}</code>，点击上方折叠栏可查看详情。
            </div>
          `;
        }
      }

      const mdCardsHtml = renderMdDocQuickCards(t.mdFiles);
      const isLastTurn = turns.indexOf(t) === turns.length - 1;
      const questionsCardHtml =
        t.questions && t.questions.length > 0 && isLastTurn
          ? renderInteractiveQuestionsCard(t, isLastTurn)
          : "";

      return `
        <article class="mb-5 pb-4 border-b border-slate-800/60 last:border-b-0">
          ${processBarHtml}
          ${
            mdBodyHtml || fallbackStatusHtml
              ? `<div class="px-1 sm:px-2">${mdBodyHtml || fallbackStatusHtml}</div>`
              : ""
          }
          ${questionsCardHtml ? `<div class="px-1 sm:px-2">${questionsCardHtml}</div>` : ""}
          ${mdCardsHtml ? `<div class="px-1 sm:px-2">${mdCardsHtml}</div>` : ""}
          <div class="px-1 sm:px-2 mt-2 text-[10px] text-slate-500 flex items-center justify-between">
            <span>步骤 #${t.firstStepIndex}${
        t.lastStepIndex > t.firstStepIndex ? `~${t.lastStepIndex}` : ""
      }</span>
            <span>${formatRelativeTime(t.createdAt)}</span>
          </div>
        </article>
      `;
    })
    .join("");

  container.innerHTML =
    `<div class="max-w-3xl mx-auto">` +
    olderBannerHtml +
    html +
    `<div id="live-stream-overlay-slot"></div><div id="messages-bottom-anchor"></div></div>`;
  renderLiveStreamOverlay();

  if (!state.autoScrollLocked) {
    scrollToBottom();
  }
}

function renderLiveStreamOverlay() {
  const slot = document.getElementById("live-stream-overlay-slot");
  if (!slot || !state.selectedSessionId) return;
  const liveText = state.streamingBuffer[state.selectedSessionId];
  if (!liveText) {
    slot.innerHTML = "";
    return;
  }
  slot.innerHTML = `
    <div class="mb-4 rounded-2xl bg-slate-900/95 border border-sky-500/40 px-3.5 py-2.5">
      <div class="flex items-center gap-1.5 text-sky-400 text-xs font-semibold mb-1.5">
        <span class="w-1.5 h-1.5 rounded-full bg-sky-400 animate-ping"></span>
        <span>Agent 正在输出...</span>
      </div>
      ${renderMarkdownHtml(liveText.slice(-2400), "live_stream")}
    </div>
  `;
  if (!state.autoScrollLocked) {
    scrollToBottom();
  }
}

function scrollToBottom() {
  const container = document.getElementById("messages-container");
  if (!container) return;
  container.scrollTop = container.scrollHeight;
  const jumpBtn = document.getElementById("scroll-bottom-pill");
  if (jumpBtn) jumpBtn.classList.add("hidden");
}

function renderApprovalBanners() {
  const topBanner = document.getElementById("global-approval-banner");
  const bottomBar = document.getElementById("bottom-approval-bar");
  if (!topBanner || !bottomBar) return;

  const list = state.pendingApprovals || [];
  if (list.length === 0) {
    topBanner.classList.add("hidden");
    bottomBar.classList.add("hidden");
    return;
  }

  topBanner.classList.remove("hidden");
  topBanner.innerHTML = `
    <div class="flex items-center justify-between gap-2 px-3 py-1 bg-amber-500/20 border-b border-amber-500/40 text-[11px] text-amber-200">
      <div class="flex items-center gap-1.5 truncate">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping shrink-0"></span>
        <span class="font-semibold">${list.length} 项待审批</span>
        <span class="text-amber-300/80 truncate">(${escapeHtml(list[0].tool_name)})</span>
      </div>
      <button onclick="window.appActions.selectSession('${escapeHtml(list[0].unified_id)}')"
        class="px-2 py-0.5 rounded bg-amber-400 text-slate-950 font-semibold shrink-0">
        处理
      </button>
    </div>
  `;

  const activeApproval =
    list.find((p) => p.unified_id === state.selectedSessionId) || list[0];
  const isHighRisk = activeApproval.risk_level === "high";

  bottomBar.classList.remove("hidden");
  bottomBar.innerHTML = `
    <div class="p-2.5 bg-slate-900/98 border-t-2 ${
      isHighRisk ? "border-rose-500" : "border-amber-500"
    } shadow-2xl">
      <div class="flex items-center justify-between gap-2 mb-1">
        <div class="flex items-center gap-1.5">
          <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${
            isHighRisk
              ? "bg-rose-500/25 text-rose-300 border border-rose-500/40"
              : "bg-amber-500/25 text-amber-300 border border-amber-500/40"
          }">
            ${isHighRisk ? "⚠️ 高危拦截" : "🛡️ 权限审批"}
          </span>
          <span class="font-mono text-xs font-semibold text-slate-200">${escapeHtml(
            activeApproval.tool_name
          )}</span>
        </div>
      </div>

      <div class="font-mono text-[11px] text-slate-200 bg-black/60 border border-slate-800 rounded-lg px-2 py-1 mb-1.5 break-all max-h-20 overflow-y-auto">
        ${escapeHtml(activeApproval.args_summary)}
      </div>

      ${activeApproval.diff_text ? renderUnifiedDiffHtml(activeApproval.diff_text) : ""}

      ${
        state.denyFeedbackOpen
          ? `
            <div class="mt-2 p-2 rounded-xl bg-slate-950 border border-rose-500/40 space-y-2">
              <div class="text-[11px] text-rose-300 font-semibold flex items-center justify-between">
                <span>✕ 拒绝原因与指导意见 (将传给 Agent 调整方案)</span>
                <button type="button" onclick="window.appActions.toggleDenyFeedback(false)" class="text-slate-400 hover:text-white px-1">收起</button>
              </div>
              <textarea id="deny-feedback-input" rows="2"
                placeholder="告诉 Agent 为什么拒绝或应该改成什么方案（例如：不要删这个函数，改成可选参数）..."
                class="w-full px-2.5 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-rose-400 resize-none"></textarea>
              <div class="flex items-center justify-end gap-2">
                <button type="button"
                  onclick="window.appActions.submitApproval('${escapeHtml(
                    activeApproval.unified_id
                  )}', 'denied')"
                  class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs">
                  直接拒绝
                </button>
                <button type="button"
                  onclick="window.appActions.submitDenyWithFeedback('${escapeHtml(
                    activeApproval.unified_id
                  )}')"
                  class="px-3 py-1 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs shadow active:scale-95 transition">
                  ✓ 拒绝并发送指导
                </button>
              </div>
            </div>
          `
          : `
            <div class="grid grid-cols-12 gap-2 mt-2">
              <button onclick="window.appActions.toggleDenyFeedback(true)"
                class="col-span-4 min-h-[42px] rounded-xl bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 font-semibold text-xs active:scale-95 transition flex items-center justify-center gap-1">
                <span>✕ 拒绝</span>
                <span class="text-[10px] text-rose-400/80">✎</span>
              </button>

              ${
                isHighRisk
                  ? `
                    <button
                      onpointerdown="window.appActions.startHold('${escapeHtml(
                        activeApproval.unified_id
                      )}', this)"
                      onpointerup="window.appActions.cancelHold(this)"
                      onpointerleave="window.appActions.cancelHold(this)"
                      onpointercancel="window.appActions.cancelHold(this)"
                      class="hold-approve-btn col-span-8 min-h-[42px] rounded-xl bg-amber-500 text-slate-950 font-bold text-xs shadow-lg">
                      <div class="hold-progress-fill"></div>
                      <span class="relative z-10">⚡ 长按 0.8s 允许</span>
                    </button>
                  `
                  : `
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approve_session_edits')"
                      class="col-span-3 min-h-[42px] rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-medium text-[11px] active:scale-95 transition">
                      免审
                    </button>
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approved')"
                      class="col-span-5 min-h-[42px] rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs shadow-lg active:scale-95 transition">
                      ✓ 允许
                    </button>
                  `
              }
            </div>
          `
      }
    </div>
  `;
}

function renderQrModalContent() {
  const box = document.getElementById("qr-modal-body");
  if (!box || !state.systemStatus) return;

  const { pair_url, awake_lock_active, agents_health } = state.systemStatus;
  const qrImgUrl = `/api/v1/auth/qr.svg?t=${Date.now()}&token=${encodeURIComponent(state.token || "")}`;

  box.innerHTML = `
    <div class="space-y-3">
      <div class="flex flex-col items-center bg-slate-950/90 border border-slate-800 rounded-xl p-3">
        <div class="bg-white p-2 rounded-xl mb-2 shadow">
          <img src="${qrImgUrl}" alt="QR" class="w-40 h-40 block" />
        </div>
        <div class="text-[11px] font-mono text-sky-400 break-all text-center select-all px-1">
          ${escapeHtml(pair_url)}
        </div>
      </div>

      <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-800/60 border border-slate-700/60">
        <span class="text-xs font-medium text-slate-200">💻 PC 防休眠锁</span>
        <button onclick="window.appActions.toggleAwakeLock(${!awake_lock_active})"
          class="px-2.5 py-1 rounded-lg text-xs font-semibold ${
            awake_lock_active
              ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
              : "bg-slate-700 text-slate-300"
          }">
          ${awake_lock_active ? "已开启" : "已关闭"}
        </button>
      </div>

      <div class="space-y-1">
        ${(agents_health || [])
          .map(
            (a) => `
              <div class="flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-slate-950/70 border border-slate-800 text-xs">
                <span class="font-medium ${a.ready ? "text-emerald-300" : "text-slate-400"}">
                  ${a.ready ? "✓" : "!"} ${escapeHtml(a.agent_label)}
                </span>
                <span class="text-[10px] text-slate-400 truncate max-w-[60%]">
                  ${escapeHtml(a.description)}
                </span>
              </div>
            `
          )
          .join("")}
      </div>
    </div>
  `;
}

// --- 7. 模态框与抽屉控制 ---
function updateDrawerVisibility() {
  const drawer = document.getElementById("sidebar-drawer");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (!drawer || !backdrop) return;
  if (state.drawerOpen) {
    drawer.classList.remove("-translate-x-full");
    backdrop.classList.remove("hidden");
  } else {
    drawer.classList.add("-translate-x-full");
    backdrop.classList.add("hidden");
  }

  // 通过 body 类名与纯 CSS 媒体查询解耦桌面端展开按钮的显示状态
  document.body.classList.toggle("sidebar-is-collapsed", !!state.sidebarCollapsed);
}

function openNewSessionModal(prefillPrompt = "", prefillProjectPath = "") {
  const modal = document.getElementById("new-session-modal");
  const wsSelect = document.getElementById("new-session-workspace");
  const agentSelect = document.getElementById("new-session-agent");
  const modelSelect = document.getElementById("new-session-model");
  const customPathInput = document.getElementById("new-session-custom-path");
  const promptInput = document.getElementById("new-session-prompt");
  if (!modal) return;

  // 汇总已有项目路径供下拉选择
  const pathMap = new Map();
  for (const w of state.workspaces) {
    if (w.workspace_path) pathMap.set(w.workspace_path, w.workspace_name);
  }
  for (const s of state.sessions) {
    if (!s.is_project) continue;
    const info = getProjectInfo(s.project_path);
    if (info.path && !pathMap.has(info.path)) {
      pathMap.set(info.path, s.project_name || info.name);
    }
  }

  if (wsSelect) {
    wsSelect.innerHTML = Array.from(pathMap.entries())
      .map(
        ([path, name]) =>
          `<option value="${escapeHtml(path)}" ${
            path === prefillProjectPath ? "selected" : ""
          }>${escapeHtml(name)} (${escapeHtml(path)})</option>`
      )
      .join("");
  }

  if (customPathInput) {
    customPathInput.value = prefillProjectPath && !pathMap.has(prefillProjectPath) ? prefillProjectPath : "";
  }

  const agType = agentSelect ? agentSelect.value : "agy_cli";
  renderModelOptionsForAgent(modelSelect, agType, "");

  if (promptInput) {
    promptInput.value = prefillPrompt || "";
  }
  modal.classList.remove("hidden");
}

async function confirmCreateNewSession() {
  const agentType = document.getElementById("new-session-agent").value;
  const modelName = document.getElementById("new-session-model").value;
  const customPath = document.getElementById("new-session-custom-path").value.trim();
  const selectedPath = document.getElementById("new-session-workspace").value;
  const mode = document.getElementById("new-session-mode").value;
  const initialPrompt = document.getElementById("new-session-prompt").value.trim();

  const finalPath = customPath || selectedPath;
  if (customPath) {
    const folderName = customPath.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "Project";
    await apiFetch("/api/v1/workspaces", {
      method: "POST",
      body: JSON.stringify({
        workspace_name: folderName,
        workspace_path: customPath,
        default_agent: agentType,
      }),
    }).catch(() => {});
    await loadWorkspaces();
  }

  const res = await apiFetch("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      agent_type: agentType,
      project_path: finalPath,
      execution_mode: mode,
      model_name: modelName,
      initial_prompt: initialPrompt,
    }),
  });

  document.getElementById("new-session-modal").classList.add("hidden");
  document.getElementById("new-session-prompt").value = "";
  await loadSessions();
  if (res.unified_id) {
    await selectSession(res.unified_id);
  }
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  const colorCls =
    type === "error"
      ? "bg-rose-950/95 border-rose-500/60 text-rose-200"
      : type === "success"
      ? "bg-emerald-950/95 border-emerald-500/60 text-emerald-200"
      : "bg-slate-900/95 border-sky-500/50 text-sky-200";
  el.className = `px-3 py-2 rounded-xl border shadow-xl text-xs font-medium transition-all ${colorCls}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 3000);
}

// --- 暴露给模板的事件动作集 ---
window.appActions = {
  selectSession,
  togglePin: handleTogglePin,
  toggleProject(projectKey) {
    state.expandedProjects[projectKey] = !state.expandedProjects[projectKey];
    renderSessionList();
  },
  newProjectSession(projectPath, ev) {
    if (ev) ev.stopPropagation();
    openNewSessionModal("", projectPath);
  },
  async hideProject(projectPath, ev) {
    if (ev) ev.stopPropagation();
    try {
      await apiFetch("/api/v1/workspaces/hide", {
        method: "POST",
        body: JSON.stringify({ workspace_path: projectPath }),
      });
      showToast("已从项目列表移出", "info");
      await Promise.all([loadWorkspaces(), loadSessions()]);
    } catch (err) {
      console.error("从项目列表移出失败:", err);
    }
  },
  onNewSessionAgentChange(agentType) {
    const modelSelect = document.getElementById("new-session-model");
    renderModelOptionsForAgent(modelSelect, agentType, "");
  },
  setFilter(f) {
    state.activeFilter = f;
    document.querySelectorAll("[data-agent-tab]").forEach((btn) => {
      if (btn.getAttribute("data-agent-tab") === f) {
        btn.className =
          "px-2 py-1 rounded-md bg-sky-500 text-slate-950 font-semibold text-[11px] shrink-0 transition";
      } else {
        btn.className =
          "px-2 py-1 rounded-md bg-slate-900 text-slate-300 hover:bg-slate-800 text-[11px] shrink-0 transition";
      }
    });
    // 0ms 纯前端内存过滤，不发同步阻塞请求、不跳走当前正在查看的对话
    renderSessionList();
    // 后台静默同步最新列表
    loadSessions(true);
  },
  loadOlderMessages,
  onDetailToggle(detailsEl, detailId) {
    if (!detailsEl || !detailId) return;
    const isOpen = detailsEl.open;
    state.expandedDetails[detailId] = isOpen;
    if (!isOpen) return;
    const slot = detailsEl.querySelector("[data-lazy-slot]");
    if (!slot || slot.innerHTML.trim() !== "") return;
    const entry = state.lazyDetailsStore[detailId];
    if (!entry) return;
    if (entry.type === "diff") {
      slot.innerHTML = renderUnifiedDiffHtml(entry.raw);
    } else {
      slot.textContent = entry.raw || "";
    }
  },
  toggleDrawer(open) {
    state.drawerOpen = open !== undefined ? open : !state.drawerOpen;
    updateDrawerVisibility();
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
  openQrModal() {
    loadSystemStatus();
    document.getElementById("qr-modal").classList.remove("hidden");
  },
  closeQrModal() {
    document.getElementById("qr-modal").classList.add("hidden");
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
  toggleWordWrap() {
    const container = document.getElementById("messages-container");
    const prevTop = container ? container.scrollTop : 0;
    state.wordWrapDiff = !state.wordWrapDiff;
    state.autoScrollLocked = true;
    renderMessagesList();
    renderApprovalBanners();
    if (container) container.scrollTop = prevTop;
  },
  toggleTurnSteps(turnKey) {
    state.expandedTurns[turnKey] = !state.expandedTurns[turnKey];
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

    if (String(codeId).startsWith("doc_modal")) {
      const modalBody = document.getElementById("doc-preview-body");
      if (modalBody && state.currentDocRawContent) {
        const prevDocScroll = modalBody.scrollTop;
        modalBody.innerHTML = renderMarkdownHtml(state.currentDocRawContent, "doc_modal");
        modalBody.scrollTop = prevDocScroll;
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
    const raw = state.codeBlockStore[codeId] || "";
    try {
      await navigator.clipboard.writeText(raw);
      if (btnEl) {
        const oldText = btnEl.textContent;
        btnEl.textContent = "✓ 已复制";
        setTimeout(() => {
          btnEl.textContent = oldText;
        }, 1600);
      } else {
        showToast("已复制代码", "success");
      }
    } catch (_) {
      showToast("复制失败，请长按手动选择", "error");
    }
  },
  async openFilePreview(rawPath) {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    modal.classList.remove("hidden");
    if (titleEl) titleEl.textContent = `📄 ${getBasename(rawPath)}`;
    if (pathEl) pathEl.textContent = rawPath;
    bodyEl.innerHTML = `
      <div class="space-y-3 py-4">
        <div class="skeleton-block h-8 w-1/2"></div>
        <div class="skeleton-block h-32 w-full"></div>
        <div class="skeleton-block h-24 w-5/6"></div>
      </div>
    `;

    try {
      const data = await apiFetch(`/api/v1/files/read?path=${encodeURIComponent(rawPath)}`);
      state.currentDocRawContent = data.content || "";
      if (titleEl) titleEl.textContent = `📄 ${data.filename || getBasename(rawPath)}`;
      if (pathEl) pathEl.textContent = data.path || rawPath;

      if (data.is_markdown) {
        bodyEl.innerHTML = renderMarkdownHtml(data.content, "doc_modal");
      } else {
        const lang = (data.ext || "").replace(/^\./, "") || "text";
        bodyEl.innerHTML = renderMarkdownHtml(
          "```" + lang + "\n" + (data.content || "") + "\n```",
          "doc_modal_code"
        );
      }
    } catch (err) {
      bodyEl.innerHTML = `
        <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
          读取文件失败：${escapeHtml(err.message || String(err))}
        </div>
      `;
    }
  },
  closeDocModal() {
    const modal = document.getElementById("doc-preview-modal");
    if (modal) modal.classList.add("hidden");
  },
  async copyCurrentDocContent() {
    if (!state.currentDocRawContent) return;
    try {
      await navigator.clipboard.writeText(state.currentDocRawContent);
      showToast("已复制文档全文", "success");
    } catch (_) {
      showToast("复制失败", "error");
    }
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
      const detailsEl = btnEl ? btnEl.closest("details") : null;
      const preEl = detailsEl ? detailsEl.querySelector("pre") : null;
      if (preEl && res.full_output) {
        preEl.textContent = res.full_output;
        preEl.classList.remove("max-h-44");
        preEl.classList.add("max-h-96");
        if (detailsEl) detailsEl.open = true;
      }
      if (btnEl) btnEl.remove();
    } catch (_) {
      if (btnEl) btnEl.textContent = "重试加载";
    }
  },
  toggleDenyFeedback(open) {
    state.denyFeedbackOpen = open !== undefined ? Boolean(open) : !state.denyFeedbackOpen;
    renderApprovalBanners();
    if (state.denyFeedbackOpen) {
      setTimeout(() => {
        const inp = document.getElementById("deny-feedback-input");
        if (inp) inp.focus();
      }, 50);
    }
  },
  submitDenyWithFeedback(unifiedId) {
    const inp = document.getElementById("deny-feedback-input");
    const feedback = inp ? inp.value.trim() : "";
    submitApprovalDecision(unifiedId, "denied", feedback);
  },
  toggleQuestionOption(arg1, qi, optText, isMulti) {
    const opts = typeof arg1 === "object" && arg1 !== null
      ? arg1
      : { turnKey: arg1, qi, optText, isMulti };
    const qKey = `${opts.turnKey}_q${opts.qi}`;
    const cur = state.questionSelections[qKey] || [];
    if (opts.isMulti) {
      state.questionSelections[qKey] = cur.includes(opts.optText)
        ? cur.filter((x) => x !== opts.optText)
        : [...cur, opts.optText];
    } else {
      state.questionSelections[qKey] = cur.includes(opts.optText) ? [] : [opts.optText];
    }
    state.autoScrollLocked = true;
    const turn = state.turnsMap[opts.turnKey];
    const optsEl = document.getElementById(`q-opts-${qKey}`);
    if (turn && turn.questions && turn.questions[opts.qi] && optsEl) {
      optsEl.innerHTML = renderQuestionOptionsHtml(opts.turnKey, opts.qi, turn.questions[opts.qi]);
      return;
    }
    renderMessagesList();
  },
  setQuestionWriteIn(turnKey, qi, val) {
    const qKey = `${turnKey}_q${qi}`;
    state.questionWriteIns[qKey] = val;
  },
  submitQuestionAnswers(turnKey) {
    const turn = state.turnsMap[turnKey];
    if (!turn || !turn.questions) return;
    const parts = [];
    turn.questions.forEach((q, qi) => {
      const qKey = `${turnKey}_q${qi}`;
      const selected = state.questionSelections[qKey] || [];
      const writeIn = (state.questionWriteIns[qKey] || "").trim();
      const ansParts = [...selected];
      if (writeIn) ansParts.push(writeIn);
      const finalAns = ansParts.join("；") || "按推荐方案";
      parts.push(turn.questions.length > 1 ? `关于“${q.question}”：${finalAns}` : finalAns);
    });
    const answerText = parts.join("\n") || "确认执行";
    // 持久化决策记录，方便历史折叠卡片回溯
    state.persistedQuestionDecisions[turnKey] = { answerText, parts, answeredAt: Date.now() };
    try {
      localStorage.setItem(
        "agentdeck_question_decisions",
        JSON.stringify(state.persistedQuestionDecisions)
      );
    } catch (_) {}
    handleSendPrompt(answerText);
  },
  skipQuestionAnswers(turnKey) {
    state.persistedQuestionDecisions[turnKey] = {
      answerText: "按最佳方案执行",
      parts: ["按推荐方案"],
      answeredAt: Date.now(),
    };
    try {
      localStorage.setItem(
        "agentdeck_question_decisions",
        JSON.stringify(state.persistedQuestionDecisions)
      );
    } catch (_) {}
    handleSendPrompt("按你推荐的最佳方案继续执行。");
  },
  toggleSlashMenu(forceOpen) {
    state.slashMenuOpen = forceOpen !== undefined ? Boolean(forceOpen) : !state.slashMenuOpen;
    renderSlashMenu("");
  },
  selectSlashCommand(cmd) {
    const input = document.getElementById("prompt-input");
    if (!input) return;
    state.slashMenuOpen = false;
    const popover = document.getElementById("slash-popover");
    if (popover) popover.classList.add("hidden");

    if (cmd.startsWith("/")) {
      input.value = `${cmd} `;
      input.focus();
    } else {
      // 快捷一键动作直接填入并发送
      input.value = cmd;
      handleSendPrompt(cmd);
    }
  },
  async openGitStatusModal() {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    const pPath = sess?.project_path || "";
    if (!pPath) {
      showToast("当前会话无关联工程目录", "info");
      return;
    }

    modal.classList.remove("hidden");
    if (titleEl) titleEl.textContent = `🌿 Git 工作区变更`;
    if (pathEl) pathEl.textContent = `${pPath}`;
    bodyEl.innerHTML = `
      <div class="space-y-3 py-4">
        <div class="skeleton-block h-8 w-1/2"></div>
        <div class="skeleton-block h-32 w-full"></div>
      </div>
    `;

    try {
      const data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(pPath)}`);
      state.currentGitStatus = data;
      renderGitStatusBody(data);
    } catch (err) {
      bodyEl.innerHTML = `
        <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
          获取 Git 状态失败：${escapeHtml(err.message || String(err))}
        </div>
      `;
    }
  },
  async toggleGitFileDiff(relPath) {
    const isExp = !state.gitExpandedDiffs[relPath];
    state.gitExpandedDiffs[relPath] = isExp;
    const container = document.getElementById(gitPathToSlotId(relPath));
    if (!container) return;
    if (!isExp) {
      container.innerHTML = "";
      return;
    }

    if (state.gitDiffCache[relPath]) {
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath]);
      return;
    }

    container.innerHTML = `<div class="p-2 text-xs text-sky-400">正在加载 Diff...</div>`;
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    try {
      const res = await apiFetch(
        `/api/v1/git/diff?project_path=${encodeURIComponent(
          sess?.project_path || ""
        )}&file_path=${encodeURIComponent(relPath)}`
      );
      state.gitDiffCache[relPath] = res.diff || "无文本变更";
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath]);
    } catch (err) {
      container.innerHTML = `<div class="p-2 text-xs text-rose-400">加载 Diff 失败</div>`;
    }
  },
  async viewFullGitDiff() {
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    const bodyEl = document.getElementById("doc-preview-body");
    if (!bodyEl || !sess?.project_path) return;
    bodyEl.innerHTML = `<div class="p-4 text-xs text-sky-400">正在生成全量 Unified Diff...</div>`;
    try {
      const res = await apiFetch(
        `/api/v1/git/diff?project_path=${encodeURIComponent(sess.project_path)}`
      );
      state.currentDocRawContent = res.diff || "";
      const rawDiff = res.diff || "";
      const lines = rawDiff.split("\n");
      const isHuge = lines.length > 400;
      const displayDiff = isHuge && !state.expandHugeFullDiff ? lines.slice(0, 400).join("\n") : rawDiff;

      bodyEl.innerHTML = `
        <div class="space-y-2">
          <div class="flex items-center justify-between pb-2 border-b border-slate-800">
            <span class="text-xs text-slate-400">全局未提交修改 Diff ${
              res.is_truncated ? "（已截断展示前 48KB）" : ""
            }</span>
            <button onclick="window.appActions.openGitStatusModal()" class="text-xs text-sky-400 hover:underline">返回文件列表</button>
          </div>
          ${renderUnifiedDiffHtml(displayDiff || "无未提交代码改动")}
          ${
            isHuge && !state.expandHugeFullDiff
              ? `<div class="p-3 text-center bg-slate-900/60 rounded-xl border border-slate-800 text-xs text-slate-400">
                  已折叠剩余 ${lines.length - 400} 行以保证移动端流畅度。
                  <button onclick="state.expandHugeFullDiff = true; window.appActions.viewFullGitDiff()" class="text-sky-400 underline font-medium ml-1">
                    仍要展示全部
                  </button>
                </div>`
              : ""
          }
        </div>
      `;
    } catch (e) {
      bodyEl.innerHTML = `<div class="p-4 text-xs text-rose-400">加载失败</div>`;
    }
  },
};

function renderSlashMenu(filterQuery = "") {
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const list = state.workflows || [];
  const q = filterQuery.trim().replace(/^\//, "").toLowerCase();

  const filtered = list.filter((item) => {
    if (!q) return true;
    return (
      item.command.toLowerCase().includes(q) ||
      (item.label || "").toLowerCase().includes(q)
    );
  });

  if (!state.slashMenuOpen && !filterQuery.startsWith("/")) {
    popover.classList.add("hidden");
    return;
  }

  if (filtered.length === 0) {
    popover.innerHTML = `<div class="px-3 py-2 text-xs text-slate-500">无匹配快捷指令</div>`;
    popover.classList.remove("hidden");
    return;
  }

  popover.innerHTML = filtered
    .map((item) => {
      const isQuick = item.category === "quick";
      return `
        <div onclick="window.appActions.selectSlashCommand('${escapeJsSingleQuote(
          item.command
        )}')"
          class="cursor-pointer px-2.5 py-1.5 rounded-lg hover:bg-slate-900 flex items-center justify-between gap-2 text-xs transition active:scale-98">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold shrink-0 ${
              isQuick
                ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
                : "bg-sky-500/15 text-sky-300 border border-sky-500/30"
            }">${escapeHtml(item.command)}</span>
            <span class="text-slate-200 truncate">${escapeHtml(item.label)}</span>
          </div>
          <span class="text-[10px] text-slate-500 shrink-0">${isQuick ? "一键发送" : "填入"}</span>
        </div>
      `;
    })
    .join("");
  popover.classList.remove("hidden");
}

async function updateGitStatusBadge() {
  const btn = document.getElementById("git-status-btn");
  const badge = document.getElementById("git-changed-badge");
  if (!btn || !badge) return;

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess || !sess.project_path) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
    return;
  }

  try {
    const data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(sess.project_path)}`);
    if (data.is_git_repo) {
      btn.classList.remove("hidden");
      btn.classList.add("flex");
      const count = data.changed_count || 0;
      badge.textContent = String(count);
      badge.className = `px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold ${
        count > 0
          ? "bg-amber-500/25 text-amber-300 border border-amber-500/40 animate-pulse"
          : "bg-slate-800 text-slate-400 border border-slate-700"
      }`;
    } else {
      btn.classList.add("hidden");
      btn.classList.remove("flex");
    }
  } catch (_) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
  }
}

function gitPathToSlotId(relPath) {
  let hash = 0;
  const str = String(relPath || "");
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return "git_diff_slot_" + (hash >>> 0).toString(16);
}

function renderGitStatusBody(data) {
  const bodyEl = document.getElementById("doc-preview-body");
  if (!bodyEl) return;

  if (!data.is_git_repo) {
    bodyEl.innerHTML = `<div class="p-6 text-center text-slate-500 text-xs">当前工作区非 Git 仓库</div>`;
    return;
  }

  const files = data.files || [];

  const listHtml =
    files.length === 0
      ? `<div class="p-6 text-center text-emerald-400 text-xs">✓ 工作区很干净，无未提交修改</div>`
      : files
          .map((f) => {
            const isExp = !!state.gitExpandedDiffs[f.path];
            const badgeCls = GIT_STATUS_BADGES[f.status] || "bg-slate-800 text-slate-300 border-slate-700";
            return `
              <div class="rounded-xl bg-slate-900/70 border border-slate-800 p-2.5 text-xs space-y-1.5">
                <div onclick="window.appActions.toggleGitFileDiff('${escapeJsSingleQuote(
                  f.path
                )}')"
                  class="cursor-pointer flex items-center justify-between gap-2 select-none hover:text-sky-300 transition">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="w-5 h-5 rounded flex items-center justify-center font-bold font-mono text-[10px] border ${badgeCls}">
                      ${escapeHtml(f.status)}
                    </span>
                    <span class="font-mono text-xs text-slate-200 truncate">${escapeHtml(
                      f.path
                    )}</span>
                  </div>
                  <div class="flex items-center gap-1.5 shrink-0 font-mono text-[10px]">
                    ${f.additions > 0 ? `<span class="text-emerald-400">+${f.additions}</span>` : ""}
                    ${f.deletions > 0 ? `<span class="text-rose-400">-${f.deletions}</span>` : ""}
                    <span class="text-slate-500">${isExp ? "▲" : "▼"}</span>
                  </div>
                </div>
                <div id="${gitPathToSlotId(f.path)}">
                  ${
                    isExp && state.gitDiffCache[f.path]
                      ? renderUnifiedDiffHtml(state.gitDiffCache[f.path])
                      : ""
                  }
                </div>
              </div>
            `;
          })
          .join("");

  bodyEl.innerHTML = `
    <div class="space-y-3 pb-6">
      <div class="flex items-center justify-between p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs">
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded-lg bg-sky-500/15 text-sky-300 border border-sky-500/30 font-mono font-semibold">
            🌿 ${escapeHtml(data.branch || "HEAD")}
          </span>
          <span class="text-slate-400">${data.changed_count} 个未提交变更文件</span>
        </div>
        ${
          files.length > 0
            ? `<button onclick="window.appActions.viewFullGitDiff()"
                class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-sky-300 text-xs transition">
                查看汇总 Diff
              </button>`
            : ""
        }
      </div>
      <div class="space-y-1.5">${listHtml}</div>
    </div>
  `;
}

// --- 初始化入口 ---
window.addEventListener("DOMContentLoaded", async () => {
  updateDrawerVisibility();
  await checkAndExchangePairCode();
  await Promise.all([loadSystemStatus(), loadWorkspaces(), loadSessions()]);
  connectWebSocket();

  const searchInput = document.getElementById("session-search-input");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value;
      renderSessionList();
    });
  }

  const promptInput = document.getElementById("prompt-input");
  if (promptInput) {
    promptInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && window.innerWidth >= 768) {
        e.preventDefault();
        handleSendPrompt(null);
      }
    });
    promptInput.addEventListener("input", (e) => {
      const val = e.target.value;
      if (val.startsWith("/")) {
        state.slashMenuOpen = true;
        renderSlashMenu(val);
      } else {
        state.slashMenuOpen = false;
        renderSlashMenu("");
      }
    });
  }

  document.addEventListener("click", (e) => {
    const popover = document.getElementById("slash-popover");
    const input = document.getElementById("prompt-input");
    if (!popover || !input) return;
    if (!popover.contains(e.target) && e.target !== input && !e.target.closest("button[onclick*='toggleSlashMenu']")) {
      state.slashMenuOpen = false;
      popover.classList.add("hidden");
    }
  });

  const msgContainer = document.getElementById("messages-container");
  if (msgContainer) {
    msgContainer.addEventListener("scroll", () => {
      const distFromBottom =
        msgContainer.scrollHeight - msgContainer.scrollTop - msgContainer.clientHeight;
      state.autoScrollLocked = distFromBottom > 120;
      const jumpBtn = document.getElementById("scroll-bottom-pill");
      if (jumpBtn) {
        jumpBtn.classList.toggle("hidden", !state.autoScrollLocked);
      }
    });
  }
});
