/**
 * AgentDeck 移动端与桌面端统一中控台前端核心逻辑
 * 遵循前端 UI/UX 设计规范：
 * 1. 参照 Antigravity-Deck 左侧栏设计：置顶 (Pinned) + 项目分组 (Projects) + 最近对话 (Recent Conversations)。
 * 2. 对话底部工具栏支持切换模型 (Model Selector) 与权限模式 (Mode Selector)。
 * 3. 全站文案精炼，顶部栏适配 safe-area-inset-top 防遮挡。
 */

const DEFAULT_MODELS_BY_AGENT = {
  agy_cli: [
    { id: "gemini-4-argon-medium", label: "Gemini 4 Argon (Medium)", tag: "New", is_recommended: true },
    { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)", tag: "New", is_recommended: true },
    { id: "gemini-4-argon-low", label: "Gemini 4 Argon (Low)", tag: "New", is_recommended: true },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (High)", tag: "极速", is_recommended: true },
    { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)", tag: "均衡", is_recommended: true },
    { id: "gemini-3.8-flash-low", label: "Gemini 3.8 Flash (Low)", tag: "超快", is_recommended: true },
    { id: "claude-opus-5-5-low", label: "Claude Opus 5.5 (Low)", tag: "New", is_recommended: true },
    { id: "claude-opus-5-5-medium", label: "Claude Opus 5.5 (Medium)", tag: "New", is_recommended: true },
    { id: "claude-opus-5-5-high", label: "Claude Opus 5.5 (High)", tag: "深度", is_recommended: true },
    { id: "claude-sonnet-5-5-low", label: "Claude Sonnet 5.5 (Low)", tag: "New", is_recommended: true },
    { id: "claude-sonnet-5-5-medium", label: "Claude Sonnet 5.5 (Medium)", tag: "New", is_recommended: true },
    { id: "claude-sonnet-5-5-high", label: "Claude Sonnet 5.5 (High)", tag: "推荐", is_recommended: true },
    { id: "gpt-oss-120b-medium", label: "GPT-OSS 120B (Medium)", tag: "Notice", is_recommended: true },
  ],
  agy_ide: [
    { id: "gemini-4-argon-medium", label: "Gemini 4 Argon (Medium)", tag: "New", is_recommended: true },
    { id: "gemini-4-argon-high", label: "Gemini 4 Argon (High)", tag: "New", is_recommended: true },
    { id: "gemini-4-argon-low", label: "Gemini 4 Argon (Low)", tag: "New", is_recommended: true },
    { id: "gemini-3.8-flash-high", label: "Gemini 3.8 Flash (High)", tag: "极速", is_recommended: true },
    { id: "gemini-3.8-flash-medium", label: "Gemini 3.8 Flash (Medium)", tag: "均衡", is_recommended: true },
    { id: "gemini-3.8-flash-low", label: "Gemini 3.8 Flash (Low)", tag: "超快", is_recommended: true },
    { id: "claude-opus-5-5-low", label: "Claude Opus 5.5 (Low)", tag: "New", is_recommended: true },
    { id: "claude-opus-5-5-medium", label: "Claude Opus 5.5 (Medium)", tag: "New", is_recommended: true },
    { id: "claude-opus-5-5-high", label: "Claude Opus 5.5 (High)", tag: "深度", is_recommended: true },
    { id: "claude-sonnet-5-5-low", label: "Claude Sonnet 5.5 (Low)", tag: "New", is_recommended: true },
    { id: "claude-sonnet-5-5-medium", label: "Claude Sonnet 5.5 (Medium)", tag: "New", is_recommended: true },
    { id: "claude-sonnet-5-5-high", label: "Claude Sonnet 5.5 (High)", tag: "推荐", is_recommended: true },
    { id: "gpt-oss-120b-medium", label: "GPT-OSS 120B (Medium)", tag: "Notice", is_recommended: true },
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

// 通用四项执行策略列表
const ALL_EXECUTION_MODES = [
  { value: "ask", label: "🛡️ 审批", fullLabel: "🛡️ 审批 (Ask)" },
  { value: "plan", label: "📋 规划", fullLabel: "📋 规划 (Plan)" },
  { value: "accept_edits", label: "✏️ 自动改", fullLabel: "✏️ 自动改 (Edit)" },
  { value: "auto", label: "⚡ 全自动", fullLabel: "⚡ 全自动 (Auto)" },
];

// 各 Agent 原生真实支持的执行/权限策略白名单矩阵（避免不支持模式引发心智模型混淆与越权误判）
const MODES_BY_AGENT = {
  agy_cli: ALL_EXECUTION_MODES,
  claude: ALL_EXECUTION_MODES,
  claude_code: ALL_EXECUTION_MODES,
  opencode: [
    { value: "ask", label: "🛡️ 审批", fullLabel: "🛡️ 审批 (Ask)" },
    { value: "auto", label: "⚡ 全自动", fullLabel: "⚡ 全自动 (Auto)" },
  ],
  agy_ide: [
    { value: "ask", label: "🛡️ 审批", fullLabel: "🛡️ 审批 (克隆接管)" },
    { value: "plan", label: "📋 规划", fullLabel: "📋 规划 (克隆接管)" },
    { value: "accept_edits", label: "✏️ 自动改", fullLabel: "✏️ 自动改 (克隆接管)" },
    { value: "auto", label: "⚡ 全自动", fullLabel: "⚡ 全自动 (克隆接管)" },
  ],
};

const state = {
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
    label: "Antigravity",
    short: "Antigravity",
    badgeClass: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  },
  agy_cli: {
    label: "Antigravity CLI",
    short: "AGY",
    badgeClass: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  },
  claude_code: {
    label: "Claude Code",
    short: "Claude",
    badgeClass: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  },
  opencode: {
    label: "OpenCode",
    short: "OC",
    badgeClass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  },
};

let wsInstance = null;
let wsHeartbeatTimer = null;
let wsPongTimeoutTimer = null;
let lastPongReceived = Date.now();
let lastWsSyncToastTime = 0;

function clearWsHeartbeat() {
  if (wsHeartbeatTimer) {
    clearInterval(wsHeartbeatTimer);
    wsHeartbeatTimer = null;
  }
  if (wsPongTimeoutTimer) {
    clearTimeout(wsPongTimeoutTimer);
    wsPongTimeoutTimer = null;
  }
}

function startWsHeartbeat() {
  clearWsHeartbeat();
  lastPongReceived = Date.now();
  wsHeartbeatTimer = setInterval(() => {
    if (!wsInstance || wsInstance.readyState !== WebSocket.OPEN) {
      return;
    }
    try {
      wsInstance.send(JSON.stringify({ action: "ping" }));
      if (wsPongTimeoutTimer) clearTimeout(wsPongTimeoutTimer);
      wsPongTimeoutTimer = setTimeout(() => {
        const elapsed = Date.now() - lastPongReceived;
        if (elapsed >= 18000 && wsInstance) {
          console.warn(`[AgentDeck WS] 心跳 Pong 超时 (${elapsed}ms)，判定网络半开假死，触发强制重连`);
          try {
            wsInstance.close();
          } catch (_) {}
        }
      }, 8000);
    } catch (e) {
      console.warn("[AgentDeck WS] 发送 Ping 心跳异常:", e);
    }
  }, 15000);
}
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
  const fullUrl = (state.activeBaseUrl && !path.startsWith("http")) ? `${state.activeBaseUrl}${path}` : path;
  const resp = await fetch(fullUrl, { ...options, headers });
  if (resp.status === 304) {
    return { not_modified: true, _etag: resp.headers.get("etag") };
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      // 401 智能自愈拦截重试：若尚未重试过且非鉴权/状态接口，尝试静默自愈恢复 Token 一次
      if (!options._retried && !path.includes("/auth/") && !path.includes("/system/status")) {
        try {
          const statusResp = await fetch(
            (state.activeBaseUrl && !path.startsWith("http")) ? `${state.activeBaseUrl}/api/v1/system/status` : "/api/v1/system/status",
            { headers: { "Content-Type": "application/json" } }
          );
          if (statusResp.ok) {
            const sData = await statusResp.json().catch(() => ({}));
            if (sData && sData.trusted_client_token) {
              state.token = sData.trusted_client_token;
              try {
                localStorage.setItem("agentdeck_token", sData.trusted_client_token);
              } catch (_) {}
              closePairingGuideModal();
              console.info("[AgentDeck] 401 拦截触发静默自愈成功，自动重试原业务请求");
              return await apiFetch(path, { ...options, _retried: true });
            }
          }
        } catch (_) {}
      }
      showPairingGuideModal();
      throw new Error("请完成设备安全配对");
    }
    const errMsg = data.detail || data.error || `请求失败 (${resp.status})`;
    showToast(errMsg, "error");
    throw new Error(errMsg);
  }
  if (resp.headers.get("etag")) {
    data._etag = resp.headers.get("etag");
  }
  return data;
}

function showPairingGuideModal(msg) {
  const modal = document.getElementById("pairing-guide-modal");
  if (!modal) return;
  const msgEl = document.getElementById("pairing-guide-msg");
  if (msgEl && msg) msgEl.textContent = msg;
  const input = document.getElementById("manual-pair-code-input");
  if (input && !input.value) {
    try {
      const pCode = new URLSearchParams(window.location.search).get("pair_code");
      if (pCode) input.value = pCode.trim();
    } catch (_) {}
  }
  modal.classList.remove("hidden");
}

function closePairingGuideModal() {
  const modal = document.getElementById("pairing-guide-modal");
  if (modal) modal.classList.add("hidden");
}

function isLocalHostClient() {
  const h = window.location.hostname;
  return h === "127.0.0.1" || h === "localhost" || h === "::1";
}

// --- 1. 一次性配对码与 Token 参数置换 ---
async function checkAndExchangePairCode() {
  const params = new URLSearchParams(window.location.search);

  // 1. 优先读取跨通道或书签携带的有效 Token
  const urlToken = params.get("token");
  if (urlToken) {
    state.token = urlToken;
    try {
      localStorage.setItem("agentdeck_token", urlToken);
    } catch (_) {}
    closePairingGuideModal();
    return;
  }

  // 2. 携带了一次性配对码进行鉴权置换
  const pairCode = params.get("pair_code");
  if (!pairCode) {
    // 首次进入不盲目弹窗拦截，允许本机或已受信网络静默进入
    return;
  }

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
      showToast("设备配对成功！", "success");
      closePairingGuideModal();
      // 将 pair_code 置换为长期 token 保存在地址栏，确保随后添加到主屏幕时 PWA 独立沙盒原生携带该授权 token
      const pwaUrl = new URL(window.location.href);
      pwaUrl.searchParams.delete("pair_code");
      pwaUrl.searchParams.set("token", res.token);
      window.history.replaceState({}, document.title, pwaUrl.pathname + pwaUrl.search + pwaUrl.hash);
    }
  } catch (err) {
    console.warn("配对码置换失败:", err);
    if (!state.token) {
      showPairingGuideModal("该配对码已失效或已过期。请在下方输入电脑上显示的当前配对码。");
    }
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

// --- 自动网络通道探活与跨网络自愈漫游调度器 ---
let isRoamingCheckBusy = false;
let wsDisconnectStreak = 0;

function showRoamingBanner(text, isPermanent = false) {
  const banner = document.getElementById("roaming-banner");
  const bannerText = document.getElementById("roaming-banner-text");
  if (!banner || !bannerText) return;
  bannerText.textContent = text;
  banner.classList.remove("hidden");
  if (!isPermanent) {
    setTimeout(() => {
      banner.classList.add("hidden");
    }, 4500);
  }
}

function hideRoamingBanner() {
  const banner = document.getElementById("roaming-banner");
  if (banner) banner.classList.add("hidden");
}

async function probeChannelHealth(targetBaseUrl, timeoutMs = 1800) {
  if (!targetBaseUrl) return false;
  const cleanUrl = targetBaseUrl.replace(/\/+$/, "");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${cleanUrl}/healthz?_probe=${Date.now()}`, {
      method: "GET",
      signal: ctrl.signal,
      cache: "no-store",
      mode: "cors",
    });
    clearTimeout(timer);
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      return data.ok === true || res.status === 200;
    }
    return false;
  } catch (_) {
    clearTimeout(timer);
    return false;
  }
}

async function triggerAutoRoaming(triggerSource = "network") {
  if (isRoamingCheckBusy) return;
  isRoamingCheckBusy = true;

  try {
    // 1. 先探测当前所在 Origin 是否畅通（放宽至 3000ms，兼容 ZeroTier / Tailscale 虚拟私网抖动）
    const currentOrigin = window.location.origin;
    const currentAlive = await probeChannelHealth(currentOrigin, 3000);
    if (currentAlive) {
      // 当前 Origin 响应正常，通道畅通，重置断线计数
      wsDisconnectStreak = 0;
      hideRoamingBanner();
      return;
    }

    // 2. 当前 Origin 不通，读取已持久化的聚合通道列表
    let cachedChannels = [];
    try {
      cachedChannels = JSON.parse(localStorage.getItem("agentdeck_channels") || "[]");
    } catch (_) {
      cachedChannels = [];
    }

    // 过滤出除当前 Origin 之外的其他通道
    const candidates = (cachedChannels || []).filter((c) => {
      if (!c.url) return false;
      try {
        const u = new URL(c.url);
        return u.origin !== currentOrigin;
      } catch (_) {
        return false;
      }
    });

    if (candidates.length === 0) {
      console.warn("未发现可漫游的备用网络通道");
      showRoamingBanner("网络通道不可达，请检查 VPN 网络连接", false);
      return;
    }

    showRoamingBanner("检测到网络通道变动，正在自动漫游探活...", true);

    // 3. 并发探测所有候选通道，取最先响应成功者
    const probePromises = candidates.map(async (c) => {
      const ok = await probeChannelHealth(c.url, 3500);
      if (ok) return c;
      throw new Error("channel offline");
    });

    let winner = null;
    try {
      winner = await Promise.any(probePromises);
    } catch (_) {
      showRoamingBanner("所有通道暂不可达，请检查 VPN 网络连接", false);
      return;
    }

    if (winner && winner.url) {
      showRoamingBanner(`已切换至更优通道：${winner.label}（无感热切中）`, true);
      try {
        const targetUrlObj = new URL(winner.url);
        if (targetUrlObj.host !== window.location.host) {
          state.activeBaseUrl = winner.url.replace(/\/+$/, "");
        } else {
          state.activeBaseUrl = "";
        }
        wsDisconnectStreak = 0;
        connectWebSocket();
        await Promise.all([loadSystemStatus(), loadSessions(true)]);
        setTimeout(hideRoamingBanner, 1600);
        return;
      } catch (hotSwapErr) {
        console.warn("无感热切换失败，降级为页面重定向:", hotSwapErr);
        const token = state.token || localStorage.getItem("agentdeck_token") || "";
        const target = new URL(winner.url);
        if (token) {
          target.searchParams.set("token", token);
        }
        target.hash = window.location.hash || "";
        setTimeout(() => {
          window.location.replace(target.toString());
        }, 200);
      }
    }
  } catch (err) {
    console.error("自动漫游探活异常:", err);
  } finally {
    isRoamingCheckBusy = false;
  }
}

// --- 3. WebSocket 实时流 ---
function connectWebSocket() {
  clearWsHeartbeat();
  if (wsInstance) {
    try {
      wsInstance.onclose = null;
      wsInstance.onerror = null;
      wsInstance.onmessage = null;
      wsInstance.onopen = null;
      wsInstance.close();
    } catch (_) {}
    wsInstance = null;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const qs = new URLSearchParams({
    since_seq: String(state.lastSeqId || 0),
    token: state.token || "",
  });
  let host = window.location.host;
  if (state.activeBaseUrl) {
    try {
      host = new URL(state.activeBaseUrl).host;
    } catch (_) {}
  }
  const wsUrl = `${proto}//${host}/ws/stream?${qs.toString()}`;

  wsInstance = new WebSocket(wsUrl);

  wsInstance.onopen = () => {
    wsDisconnectStreak = 0;
    hideRoamingBanner();
    startWsHeartbeat();
    const wasReconnecting = state.wsReconnecting;
    state.wsConnected = true;
    state.wsReconnecting = false;
    renderHeaderStatus();
    if (wasReconnecting) {
      const now = Date.now();
      if (now - lastWsSyncToastTime > 8000) {
        lastWsSyncToastTime = now;
        showToast("已恢复同步", "success");
      }
      loadSessions(true);
      if (state.selectedSessionId) {
        loadSessionMessages(state.selectedSessionId, true);
      }
    }
  };

  wsInstance.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "PONG") {
        lastPongReceived = Date.now();
        if (wsPongTimeoutTimer) {
          clearTimeout(wsPongTimeoutTimer);
          wsPongTimeoutTimer = null;
        }
        return;
      }
      if (msg.seq_id && msg.seq_id > state.lastSeqId) {
        state.lastSeqId = msg.seq_id;
      }
      handleRealtimeEvent(msg);
    } catch (e) {
      console.error("WS 解析失败:", e);
    }
  };

  wsInstance.onclose = (ev) => {
    clearWsHeartbeat();
    state.wsConnected = false;
    state.wsReconnecting = true;
    renderHeaderStatus();
    if (ev && ev.code === 1008) {
      // 鉴权未通过被服务端拒绝，先尝试通过受信虚拟私网静默自愈一次，避免冷启动瞬态误报
      loadSystemStatus().then(() => {
        if (state.token) {
          console.info("[AgentDeck WS] 握手 1008 自愈成功，正在使用新 Token 重连...");
          setTimeout(connectWebSocket, 800);
        } else {
          showPairingGuideModal();
        }
      }).catch(() => {
        showPairingGuideModal();
      });
      return;
    }
    wsDisconnectStreak++;
    if (wsDisconnectStreak >= 2) {
      triggerAutoRoaming("websocket_disconnect");
    }
    setTimeout(connectWebSocket, 2200);
  };
}

function handleRealtimeEvent(envelope) {
  const { type, payload } = envelope;

  // 手机端扫码配对成功广播：电脑端扫码弹窗瞬间响应双向握手并优雅自动关闭
  if (type === "DEVICE_PAIRED") {
    state.pairedSuccessNotice = payload;
    const qrModal = document.getElementById("qr-modal");
    if (qrModal && !qrModal.classList.contains("hidden")) {
      renderQrModalContent();
      setTimeout(() => {
        state.pairedSuccessNotice = null;
        window.appActions.closeQrModal();
      }, 1800);
    }
    showToast(`${payload.device_name || "移动设备"} 配对成功！`, "success");
    triggerAlertNotification("设备配对成功", `${payload.device_name || "移动设备"} 已安全接入中控台`);
    return;
  }

  if (type === "SESSION_STATE_SNAPSHOT") {
    state.pendingApprovals = payload.pending_approvals || [];
    renderApprovalBanners();
    if (payload.is_overflow) {
      console.info("[AgentDeck] 检测到事件环形缓冲区溢出 (is_overflow=true)，触发全量数据补偿同步...");
      loadSessions(true);
      if (state.selectedSessionId) {
        loadSessionMessages(state.selectedSessionId, { silent: true });
      }
    }
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

  if (type === "SESSION_TURN_STARTED") {
    const uid = payload && payload.unified_id;
    if (uid) {
      if (!state.pendingTurnThinking[uid]) {
        state.pendingTurnThinking[uid] = Date.now();
      }
      const sess = state.sessions.find((s) => s.unified_id === uid);
      if (sess) {
        sess.status = "running";
        renderSessionList();
      }
      if (uid === state.selectedSessionId) {
        renderCurrentSessionHeader();
        renderLiveStreamOverlay();
      }
    }
    loadSessions(true);
    return;
  }

  if (type === "SESSION_TURN_FINISHED" || type === "SESSION_ABORTED") {
    const uid = payload && payload.unified_id;
    if (uid) {
      delete state.pendingTurnThinking[uid];
      delete state.streamingBuffer[uid];
      if (state.optimisticMessages[uid]) {
        delete state.optimisticMessages[uid];
      }
      const sess = state.sessions.find((s) => s.unified_id === uid);
      if (sess && sess.status === "running") {
        sess.status = "idle";
        renderSessionList();
      }
      if (uid === state.selectedSessionId) {
        renderCurrentSessionHeader();
        renderLiveStreamOverlay();
      }
    }
    loadSessions(true);
    if (state.selectedSessionId && (!uid || uid === state.selectedSessionId)) {
      loadSessionMessages(state.selectedSessionId, { silent: true });
    }
    return;
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

  if (type === "SESSION_ERROR") {
    const uid = payload && payload.unified_id;
    if (uid) {
      delete state.pendingTurnThinking[uid];
      delete state.streamingBuffer[uid];
      if (state.optimisticMessages[uid]) {
        delete state.optimisticMessages[uid];
      }
      if (uid === state.selectedSessionId) {
        renderLiveStreamOverlay();
      }
    }
    showToast(payload.error || "执行异常", "error");
    loadSessions();
    return;
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

function getSessionIdFromHash() {
  const hash = window.location.hash || "";
  const match = hash.match(/(?:^|[#&])session=([^&]+)/);
  if (match && match[1]) {
    try {
      return decodeURIComponent(match[1]);
    } catch {
      return match[1];
    }
  }
  return null;
}

function updateSessionHash(unifiedId) {
  if (!unifiedId) return;
  const targetHash = `session=${encodeURIComponent(unifiedId)}`;
  const currentHash = window.location.hash.replace(/^#/, "");
  if (currentHash !== targetHash) {
    history.replaceState(null, "", `#${targetHash}`);
  }
}

function isSessionStatusActive(s) {
  if (!s || !s.status) return false;
  const st = String(s.status).toLowerCase();
  return st === "running" || st === "desktop_active";
}

function renderOtherRunningPill() {
  const pill = document.getElementById("other-running-pill");
  const textEl = document.getElementById("other-running-text");
  if (!pill) return;

  const otherRunning = (state.allSessions || []).filter(
    (s) =>
      s.unified_id !== state.selectedSessionId &&
      (isSessionStatusActive(s) || s.status === "waiting_approval")
  );

  if (otherRunning.length === 0) {
    pill.classList.add("hidden");
    pill.classList.remove("flex", "sm:flex");
    return;
  }

  // 手机端始终保持隐藏（避免显示无文字绿色圆圈并挤压顶部标题栏），仅在 sm 屏幕及以上显示
  pill.classList.add("hidden", "sm:flex");
  pill.classList.remove("flex");
  const count = otherRunning.length;
  const hasApproval = otherRunning.some((s) => s.status === "waiting_approval");
  if (textEl) {
    textEl.textContent = hasApproval
      ? `${count} 个待审批任务`
      : `${count} 个后台运行中`;
  }
}

function getLatestSession(list) {
  if (!list || list.length === 0) return null;
  if (list.length === 1) return list[0];
  return [...list].sort((a, b) => {
    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  })[0];
}

/**
 * 首次进入页面或失去当前选中会话时的首选会话决策：
 * 1. 优先进入运行中 / 进行中的会话（覆盖 running 与 desktop_active，其次 waiting_approval）
 * 2. 若无进行中的会话，则进入收藏（置顶）的会话
 * 3. 兜底进入列表第 1 个会话
 */
function pickDefaultSession(sessionList) {
  if (!Array.isArray(sessionList) || sessionList.length === 0) {
    return null;
  }
  const runningSessions = sessionList.filter((s) => isSessionStatusActive(s));
  if (runningSessions.length > 0) {
    return getLatestSession(runningSessions);
  }
  const waitingSessions = sessionList.filter(
    (s) => String(s.status || "").toLowerCase() === "waiting_approval"
  );
  if (waitingSessions.length > 0) {
    return getLatestSession(waitingSessions);
  }
  const pinnedSessions = sessionList.filter((s) => Boolean(s.is_pinned));
  if (pinnedSessions.length > 0) {
    return getLatestSession(pinnedSessions);
  }
  return sessionList[0];
}

async function loadSessions(silent = false) {
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

    if (!silent || newSig !== state.lastSessionsSig || needsInitialSelect) {
      state.lastSessionsSig = newSig;
      // 手机端抽屉展开浏览时，禁止销毁重构侧边栏 DOM，避免打断用户的滑动操作与项目折叠状态
      if (!isMobileDrawerActive) {
        renderSessionList();
      }
      renderCurrentSessionHeader();
      renderApprovalBanners();
      renderOtherRunningPill();
    }

    if (needsInitialSelect && initialSessionId) {
      selectSession(initialSessionId);
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

async function selectSession(unifiedId) {
  const prevId = state.selectedSessionId;
  const wasMobileDrawerOpen = state.drawerOpen && window.innerWidth < 768;
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
  const sessionListContainer = document.getElementById("session-list-container");
  const isListEmpty = !sessionListContainer || sessionListContainer.children.length === 0;
  if (needFullSidebarRender || isListEmpty) {
    renderSessionList();
  } else {
    updateSelectedSessionHighlight(prevId, unifiedId);
  }
  renderCurrentSessionHeader();
  renderApprovalBanners();
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
    const headers = {};
    const prevCache = state.sessionMessagesCache[unifiedId];
    let sinceStep = null;
    // 静默轮询时启用增量拉取：若本地已有该会话消息，则只拉取最后一步起的变化 (since_step)，大幅节省带宽与反序列化损耗
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

    // 检查后端返回的活跃执行态并联动思考计时器
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

    // 写入内存缓存与就地增量合并
    let mergedMessages = incoming;
    let mergedHasMore = hasMore;
    if (sinceStep !== null && prevCache && Array.isArray(prevCache.messages)) {
      if (typeof totalCount === "number" && totalCount < prevCache.messages.length) {
        // 服务端消息总数缩水，说明会话被截断或重新初始化，丢弃陈旧缓存直接使用 incoming
        mergedMessages = incoming;
      } else if (incoming.length === 0) {
        // 增量拉取无新消息，安全保留现有完整消息列表
        mergedMessages = prevCache.messages;
        mergedHasMore = prevCache.hasMore;
      } else {
        // 基于 step_index 原子去重合并，防止网络抖动导致的丢步或重复
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

    // 检查并合并尚未被后端日志落盘返回的本地乐观用户消息，保证用户发送后绝对不闪退
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
  updatePromptActionBtnState();

  const modelSelect = document.getElementById("model-select");
  const modeSelect = document.getElementById("execution-mode-select");
  const chosenModel = modelSelect ? modelSelect.value : "";
  const chosenMode = modeSelect ? modeSelect.value : "ask";

  const optMsg = {
    item_id: `optimistic_${Date.now()}`,
    step_index: state.messages.length + 1,
    role: "user",
    created_at: new Date().toISOString(),
    content: text,
    thinking: "",
    tool_calls: [],
    status: "RUNNING",
  };

  if (!state.optimisticMessages[state.selectedSessionId]) {
    state.optimisticMessages[state.selectedSessionId] = [];
  }
  state.optimisticMessages[state.selectedSessionId].push(optMsg);

  state.messages.push(optMsg);
  state.pendingTurnThinking[state.selectedSessionId] = Date.now();
  state.autoScrollLocked = false;

  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (curSess) {
    curSess.status = "running";
    curSess.preview = text;
    renderSessionList();
    renderCurrentSessionHeader();
  }

  renderMessagesList();
  renderLiveStreamOverlay();

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
      const oldUid = state.selectedSessionId;
      state.selectedSessionId = res.unified_id;
      if (state.optimisticMessages[oldUid]) {
        state.optimisticMessages[res.unified_id] = state.optimisticMessages[oldUid];
        delete state.optimisticMessages[oldUid];
      }
      if (state.pendingTurnThinking[oldUid]) {
        state.pendingTurnThinking[res.unified_id] = state.pendingTurnThinking[oldUid];
        delete state.pendingTurnThinking[oldUid];
      }
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
    delete state.pendingTurnThinking[state.selectedSessionId];
    delete state.streamingBuffer[state.selectedSessionId];
    if (state.optimisticMessages[state.selectedSessionId]) {
      delete state.optimisticMessages[state.selectedSessionId];
    }
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (curSess && curSess.status === "running") {
      curSess.status = "idle";
    }
    updatePromptActionBtnState();
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/abort`,
      { method: "POST" }
    );
    showToast("已中断进程", "info");
    await loadSessions();
    renderCurrentSessionHeader();
    renderLiveStreamOverlay();
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
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function cleanFriendlyTitle(title) {
  if (!title) return "";
  const str = String(title).trim();
  const m = str.match(/^\[已拒绝工具\s+[^\]]+\]\s*(?:请勿执行刚才的操作，按以下要求调整方案：)?\s*(.*)$/);
  if (m) {
    const feedback = m[1] ? m[1].trim() : "";
    return feedback ? `指导: ${feedback}` : "已拒绝工具执行";
  }
  return str;
}
const cleanFriendlyText = cleanFriendlyTitle;

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

function renderSidebarStatus() {
  const btn = document.getElementById("sidebar-status-btn");
  const headerIndicator = document.getElementById("header-status-indicator");

  if (state.hasUpdate) {
    // 发现前端代码更新：亮蓝呼吸脉冲 + 向上箭头图标
    if (btn) {
      btn.className =
        "h-8 px-2.5 rounded-lg bg-sky-500/15 hover:bg-sky-500/25 border border-sky-400/50 text-sky-200 flex items-center gap-1.5 text-xs transition active:scale-95 shadow-[0_0_12px_rgba(56,189,248,0.25)] animate-pulse cursor-pointer";
      btn.title = "发现新版本前端代码，点击立即载入更新";
      btn.innerHTML = `
        <svg class="w-3.5 h-3.5 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="19" x2="12" y2="5"></line>
          <polyline points="5 12 12 5 19 12"></polyline>
        </svg>
        <span class="text-[11px] font-semibold text-sky-300">更新</span>
      `;
    }
    if (headerIndicator) {
      headerIndicator.classList.remove("hidden");
      headerIndicator.className =
        "shrink-0 h-6 px-1.5 rounded-md bg-sky-500/20 hover:bg-sky-500/30 border border-sky-400/50 text-sky-300 flex items-center gap-1 text-[11px] transition active:scale-95 shadow-sm animate-pulse cursor-pointer";
      headerIndicator.title = "发现前端代码更新，点击立即载入最新代码";
      headerIndicator.innerHTML = `
        <svg class="w-3 h-3 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="19" x2="12" y2="5"></line>
          <polyline points="5 12 12 5 19 12"></polyline>
        </svg>
        <span class="font-semibold text-[10px]">更新</span>
      `;
    }
    return;
  }

  // 常规连接状态：手机端日常仅保留呼吸圆点（无背景与外边框），电脑端保留胶囊样式
  if (btn) {
    btn.className =
      "h-8 w-8 sm:w-auto p-0 sm:px-2 rounded-lg bg-transparent hover:bg-zinc-900/60 sm:bg-zinc-900 sm:hover:bg-zinc-800 border-0 sm:border sm:border-zinc-800/80 text-zinc-300 flex items-center justify-center sm:justify-start gap-0 sm:gap-1.5 text-xs transition active:scale-95 cursor-pointer";

    if (state.wsConnected) {
      btn.title = "WebSocket 实时连接正常（双击可强制重载）";
      btn.innerHTML = `
        <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(52,211,153,0.7)]"></span>
        <span class="text-[11px] font-mono text-zinc-400 hidden sm:inline">已连接</span>
      `;
    } else if (state.wsReconnecting) {
      btn.title = "正在重新连接 WebSocket 服务...";
      btn.innerHTML = `
        <span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping"></span>
        <span class="text-[11px] font-mono text-amber-400 hidden sm:inline">连接中</span>
      `;
    } else {
      btn.title = "与服务端的实时连接断开（点击尝试重新载入）";
      btn.innerHTML = `
        <span class="w-2.5 h-2.5 rounded-full bg-zinc-600"></span>
        <span class="text-[11px] font-mono text-zinc-500 hidden sm:inline">离线</span>
      `;
    }
  }

  if (headerIndicator) {
    if (state.wsConnected) {
      headerIndicator.classList.add("hidden");
    } else if (state.wsReconnecting) {
      headerIndicator.classList.remove("hidden");
      headerIndicator.className =
        "shrink-0 h-5 px-1 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 flex items-center gap-1 text-[10px] cursor-pointer";
      headerIndicator.title = "重连中... 点击尝试重新载入";
      headerIndicator.innerHTML = `
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping"></span>
        <span>重连中</span>
      `;
    } else {
      headerIndicator.classList.remove("hidden");
      headerIndicator.className =
        "shrink-0 h-5 px-1 rounded bg-zinc-800 border border-zinc-700 text-zinc-400 flex items-center gap-1 text-[10px] cursor-pointer";
      headerIndicator.title = "已离线，点击重新载入";
      headerIndicator.innerHTML = `
        <span class="w-1.5 h-1.5 rounded-full bg-zinc-500"></span>
        <span>离线</span>
      `;
    }
  }
}

async function checkFrontendVersion() {
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

function handleStatusBtnClick() {
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

function renderHeaderStatus() {
  renderSidebarStatus();
  const netBadge = document.getElementById("network-status-badge");
  const reconnectBanner = document.getElementById("reconnect-banner");
  if (!netBadge) return;

  if (state.wsConnected) {
    const tsOnline = !!state.systemStatus?.tailscale?.is_online;
    const ztOnline = !!state.systemStatus?.tailscale?.zerotier_ip;
    const vpnOnline = tsOnline || ztOnline;
    netBadge.innerHTML = `<span class="w-2 h-2 rounded-full ${
      vpnOnline ? "bg-emerald-400" : "bg-sky-400"
    }"></span>`;
    if (reconnectBanner) reconnectBanner.classList.add("hidden");
  } else {
    netBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>`;
    if (reconnectBanner) reconnectBanner.classList.remove("hidden");
  }
}

function renderSidebarAwakeLock() {
  const dot = document.getElementById("sidebar-awake-dot");
  const text = document.getElementById("sidebar-awake-text");
  if (!dot || !text) return;
  const active = !!state.systemStatus?.awake_lock_active;
  if (active) {
    dot.className = "w-1.5 h-1.5 rounded-full bg-emerald-400";
    text.textContent = "防休眠: 开";
    text.className = "text-emerald-300 font-medium";
  } else {
    dot.className = "w-1.5 h-1.5 rounded-full bg-zinc-600";
    text.textContent = "防休眠: 关";
    text.className = "text-zinc-400";
  }
}

function renderAgentsHealthModal() {
  const body = document.getElementById("agents-health-modal-body");
  if (!body) return;
  const list = state.systemStatus?.agents_health || [];
  body.innerHTML = list
    .map(
      (a) => `
        <div class="p-2.5 rounded-xl bg-zinc-950/80 border border-zinc-800 flex items-start justify-between gap-2">
          <div class="space-y-0.5 min-w-0">
            <div class="text-xs font-semibold flex items-center gap-1.5 ${
              a.ready ? "text-emerald-300" : "text-zinc-400"
            }">
              <span class="w-1.5 h-1.5 rounded-full ${a.ready ? "bg-emerald-400" : "bg-zinc-600"}"></span>
              <span>${escapeHtml(a.agent_label)}</span>
            </div>
            <div class="text-[10px] text-zinc-500 leading-relaxed truncate">${escapeHtml(a.description || "")}</div>
          </div>
          <span class="text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
            a.ready ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-zinc-800 text-zinc-500"
          }">
            ${a.ready ? "就绪" : "未挂载"}
          </span>
        </div>
      `
    )
    .join("");
}

function renderCompactSessionItem(s, showProjectTag = true) {
  const meta = AGENT_META[s.agent_type] || AGENT_META.claude_code;
  const isSelected = s.unified_id === state.selectedSessionId;

  // 运行/等待审批/活跃状态判定：优先在右侧原本显示时间的区域显示对应呼吸灯；未运行时显示更新时间
  const isWorking = typeof isSessionWorking === "function" ? isSessionWorking(s.unified_id) : false;
  let statusDotHtml = null;
  if (s.status === "waiting_approval") {
    statusDotHtml = `
      <span class="relative flex h-2 w-2" title="等待审批">
        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
        <span class="relative inline-flex rounded-full h-2 w-2 bg-amber-400"></span>
      </span>
    `;
  } else if (s.status === "running" || isWorking) {
    statusDotHtml = `
      <span class="relative flex h-2 w-2" title="运行中">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(52,211,153,0.8)]"></span>
      </span>
    `;
  } else if (s.status === "desktop_active") {
    statusDotHtml = `
      <span class="relative flex h-2 w-2" title="桌面端活跃">
        <span class="w-2 h-2 rounded-full bg-purple-400 animate-pulse shadow-[0_0_8px_rgba(192,132,252,0.8)]"></span>
      </span>
    `;
  }

  return `
    <div data-session-item="${escapeHtml(s.unified_id)}" onclick="window.appActions.selectSession('${escapeHtml(s.unified_id)}')"
      class="group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 ${
        isSelected
          ? "bg-zinc-800/95 border-sky-500/50 text-zinc-100"
          : "bg-zinc-900/45 border-transparent hover:bg-zinc-900 hover:border-zinc-800 text-zinc-300"
      }">
      <div class="flex items-center gap-1.5 min-w-0 flex-1">
        <span class="px-1 py-0.5 text-[9px] font-semibold rounded border shrink-0 ${meta.badgeClass}">
          ${meta.short}
        </span>
        <span class="text-xs truncate font-medium">${escapeHtml(cleanFriendlyTitle(s.title))}</span>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        ${
          showProjectTag && s.is_project && s.project_name
            ? `<span class="text-[9px] text-zinc-500 font-mono max-w-[68px] truncate hidden sm:inline">${escapeHtml(
                s.project_name
              )}</span>`
            : ""
        }
        ${
          statusDotHtml
            ? `<span class="flex items-center justify-center min-w-[28px] py-0.5">${statusDotHtml}</span>`
            : `<span class="text-[10px] text-zinc-500 tabular-nums">${formatRelativeTime(s.updated_at)}</span>`
        }
        <button onclick="window.appActions.togglePin('${escapeHtml(s.unified_id)}', ${s.is_pinned ? 1 : 0}, event)"
          class="p-1 rounded hover:bg-zinc-800 text-xs transition ${
            s.is_pinned ? "text-amber-400 opacity-100" : "text-zinc-500 opacity-50 hover:opacity-100 md:opacity-0 md:group-hover:opacity-100"
          }" title="置顶" aria-label="置顶">
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
function sortSessions(list) {
  if (!Array.isArray(list)) return [];
  return [...list].sort((a, b) => {
    const pinA = Number(a.is_pinned || 0);
    const pinB = Number(b.is_pinned || 0);
    if (pinA !== pinB) return pinB - pinA;

    const getStatusWeight = (s) => {
      const st = String(s?.status || "").toLowerCase();
      if (st === "waiting_approval") return 2;
      if (st === "running" || st === "desktop_active") return 1;
      return 0;
    };
    const swA = getStatusWeight(a);
    const swB = getStatusWeight(b);
    if (swA !== swB) return swB - swA;

    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  });
}

function renderSessionList() {
  const container = document.getElementById("session-list-container");
  if (!container) return;

  const q = state.searchQuery.trim().toLowerCase();
  const rawList = state.allSessions.length > 0 ? state.allSessions : state.sessions;
  const sourceList = sortSessions(rawList);
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
      <div class="p-6 text-center text-zinc-500 text-xs">
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

  // 首次加载优先自动展开包含运行中会话或当前选中会话的项目
  if (!state.didInitExpand && projects.length > 0) {
    state.didInitExpand = true;
    let expandedAny = false;
    for (const p of projects) {
      const hasActiveOrSelected = p.sessions.some(
        (s) =>
          isSessionStatusActive(s) ||
          s.status === "waiting_approval" ||
          s.unified_id === state.selectedSessionId
      );
      if (hasActiveOrSelected) {
        state.expandedProjects[p.key] = true;
        expandedAny = true;
      }
    }
    if (!expandedAny) {
      const firstWithConvs = projects.find((p) => p.sessions.length > 0) || projects[0];
      state.expandedProjects[firstWithConvs.key] = true;
    }
  }

  // 3. 临时与最近对话（优先展示非项目的散落临时对话 + 最近活跃对话）
  const looseList = filtered.filter((s) => !s.is_project);
  const recentDisplayList = (looseList.length > 0 ? looseList : filtered).slice(0, 15);

  let html = "";

  // --- 区块 1：置顶 ---
  if (pinnedList.length > 0) {
    html += `
      <div>
        <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 flex items-center justify-between">
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
  const isProjectsCollapsed = !q && !!state.collapsedSections?.projects;
  html += `
    <div>
      <div onclick="window.appActions.toggleSection('projects')"
        class="cursor-pointer select-none px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 hover:text-zinc-300 flex items-center justify-between transition-colors"
        title="点击收纳/展开 项目">
        <div class="flex items-center gap-1.5">
          <span>📁 项目</span>
          <span class="text-[10px] text-zinc-600 font-normal font-mono">(${projects.length})</span>
        </div>
        <div class="flex items-center gap-1" onclick="event.stopPropagation()">
          <button onclick="window.appActions.openNewSessionModal()" class="text-zinc-400 hover:text-sky-400 text-xs px-1" title="添加项目或新建对话">＋</button>
        </div>
      </div>
      ${
        !isProjectsCollapsed
          ? `<div class="space-y-1">
              ${
                projects.length === 0
                  ? `<div class="px-2 py-2 text-[11px] text-zinc-600">暂无工程项目</div>`
                  : projects
                      .map((proj) => {
                        const isOpen = !!state.expandedProjects[proj.key] || !!q;
                        const visibleConvs = proj.sessions.slice(0, 8);
                        return `
                          <div class="group/proj rounded-lg bg-zinc-900/35 border border-zinc-900">
                            <div onclick="window.appActions.toggleProject('${escapeHtml(proj.key)}')"
                              class="cursor-pointer px-2 py-1.5 flex items-center justify-between gap-1.5 rounded-lg hover:bg-zinc-900 text-xs text-zinc-200">
                              <div class="flex items-center gap-1.5 min-w-0 flex-1">
                                <span class="text-sky-400/90 shrink-0">📂</span>
                                <span class="truncate font-medium">${escapeHtml(proj.name)}</span>
                              </div>
                              <div class="flex items-center gap-1 shrink-0">
                                <button onclick="window.appActions.newProjectSession('${escapeHtml(
                                  proj.path
                                )}', event)"
                                  class="w-4 h-4 rounded hover:bg-zinc-800 text-zinc-400 hover:text-sky-400 flex items-center justify-center text-xs"
                                  title="在此项目新建对话">＋</button>
                                <button onclick="window.appActions.hideProject('${escapeHtml(
                                  proj.path
                                )}', event, '${escapeJsSingleQuote(proj.name)}')"
                                  class="w-4 h-4 rounded hover:bg-rose-500/20 text-zinc-500 hover:text-rose-400 flex items-center justify-center text-[11px]"
                                  title="从项目列表移出 (不删对话)">×</button>
                                <span class="text-[10px] text-zinc-500 tabular-nums min-w-[14px] text-right">${
                                  proj.sessions.length
                                }</span>
                              </div>
                            </div>
                            ${
                              isOpen
                                ? `<div class="pl-3 pr-1 pb-1 space-y-0.5 border-l border-zinc-800/80 ml-3 mb-1">
                                    ${
                                      visibleConvs.length === 0
                                        ? `<div class="px-2 py-1 text-[10px] text-zinc-600 italic">无对话</div>`
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
            </div>`
          : ""
      }
    </div>
  `;

  // --- 区块 3：临时会话 ---
  const isConversationsCollapsed = !q && !!state.collapsedSections?.conversations;
  html += `
    <div>
      <div onclick="window.appActions.toggleSection('conversations')"
        class="cursor-pointer select-none px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 hover:text-zinc-300 flex items-center justify-between transition-colors"
        title="点击收纳/展开 临时会话">
        <div class="flex items-center gap-1.5">
          <span>💬 临时会话</span>
        </div>
        <span class="text-[10px] text-zinc-500 tabular-nums">${recentDisplayList.length}</span>
      </div>
      ${
        !isConversationsCollapsed
          ? `<div class="space-y-0.5">
              ${recentDisplayList.map((s) => renderCompactSessionItem(s, true)).join("")}
            </div>`
          : ""
      }
    </div>
  `;

  container.innerHTML = html;
}

const MODEL_CONTEXT_LIMITS = {
  "claude-sonnet-5-5-high": 200000,
  "claude-opus-5-5-high": 200000,
  "claude-sonnet-5-5-medium": 200000,
  "claude-sonnet-4-6": 200000,
  "claude-opus-4-7": 200000,
  "claude-haiku-4-5-20251001": 200000,
  "gemini-4-argon-high": 1000000,
  "gemini-4-argon-medium": 1000000,
  "gemini-3.8-flash-high": 1000000,
  "gemini-2.5-pro": 1000000,
  "gemini-2.5-flash": 1000000,
  "gpt-oss-120b-medium": 128000,
  "gpt-4o": 128000,
  "gpt-4o-mini": 128000,
};

function getModelContextLimit(modelId) {
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

function renderModelOptionsForAgent(selectEl, agentType, selectedModelId = "") {
  if (!selectEl) return;
  const list = state.modelsByAgent[agentType];
  const models =
    Array.isArray(list) && list.length > 0
      ? list
      : DEFAULT_MODELS_BY_AGENT[agentType] || DEFAULT_MODELS_BY_AGENT.agy_cli;

  const defaultModelItem = models.find((m) => m.is_default) || models[0];
  const activeId = selectedModelId || (defaultModelItem ? defaultModelItem.id : "");
  if (selectEl.tagName === "SELECT") {
    const usage = state.currentTokenUsage;
    const currentTokens = usage ? usage.context_tokens || 0 : 0;
    selectEl.innerHTML = models
      .map((m) => {
        const tagSuffix = m.tag ? ` · ${m.tag}` : "";
        const limit = getModelContextLimit(m.id);
        let quotaPart = "";
        if (currentTokens > 0) {
          const pct = Math.round((currentTokens / limit) * 1000) / 10;
          quotaPart = ` [水位 ${pct}% · ${formatTokenCount(currentTokens)}/${formatTokenCount(limit)}]`;
        } else {
          quotaPart = ` [容量 ${formatTokenCount(limit)}]`;
        }
        const sel = m.id === activeId ? "selected" : "";
        return `<option value="${escapeHtml(m.id)}" ${sel}>${escapeHtml(
          m.label + tagSuffix + quotaPart
        )}</option>`;
      })
      .join("");
  } else {
    selectEl.value = activeId;
  }
  renderModelPicker();
}

function renderModeOptionsForAgent(selectEl, agentType, selectedMode = "ask", isShort = false) {
  if (!selectEl) return "ask";
  const normalizedType = agentType === "claude" ? "claude_code" : (agentType || "agy_cli");
  const list = MODES_BY_AGENT[normalizedType] || MODES_BY_AGENT.agy_cli;

  // 校验当前选中的模式是否受该 Agent 支持；若不支持，安全回退至 ask（审批）
  const isSupported = list.some((m) => m.value === selectedMode);
  const activeMode = isSupported ? selectedMode : (list[0]?.value || "ask");

  selectEl.innerHTML = list
    .map((m) => {
      const text = isShort ? m.label : m.fullLabel;
      const sel = m.value === activeMode ? "selected" : "";
      return `<option value="${escapeHtml(m.value)}" ${sel}>${escapeHtml(text)}</option>`;
    })
    .join("");

  selectEl.value = activeMode;
  return activeMode;
}

function renderModelPicker() {
  const btn = document.getElementById("model-picker-btn");
  const labelEl = document.getElementById("model-picker-label");
  const popover = document.getElementById("model-picker-popover");
  const hiddenInput = document.getElementById("model-select");
  if (!btn || !labelEl) return;

  const sess =
    state.sessions.find((s) => s.unified_id === state.selectedSessionId) ||
    state.allSessions.find((s) => s.unified_id === state.selectedSessionId);
  const agentType = sess?.agent_type || "agy_cli";
  const list = state.modelsByAgent[agentType];
  const models =
    Array.isArray(list) && list.length > 0
      ? list
      : DEFAULT_MODELS_BY_AGENT[agentType] || DEFAULT_MODELS_BY_AGENT.agy_cli;

  const defaultModelItem = models.find((m) => m.is_default) || models[0];
  const activeId = sess?.model_name || (defaultModelItem ? defaultModelItem.id : "");
  if (hiddenInput) hiddenInput.value = activeId;

  const activeModel = models.find((m) => m.id === activeId) || defaultModelItem;
  if (activeModel) {
    labelEl.textContent = activeModel.label;
  }

  if (!popover) return;

  popover.innerHTML = `
    <div class="px-3 py-2 text-[10px] text-zinc-400 font-semibold uppercase tracking-wider border-b border-zinc-800 flex items-center justify-between sticky top-0 z-10 select-none" style="background-color: #09090b !important;">
      <span class="flex items-center gap-1.5 font-medium text-zinc-200">
        <span class="text-sky-400">🧠</span>
        <span>可用模型 (${models.length})</span>
      </span>
      <span class="text-[10px] text-zinc-400 font-mono">余量</span>
    </div>
    <div class="p-1 space-y-0.5" style="background-color: #09090b !important;">
      ${models
        .map((m) => {
          const isSelected = m.id === activeId;
          const quotaVal = typeof m.quota === "number" ? m.quota : 1.0;
          const pct = Math.round(quotaVal * 100);

          let barColorCls = "bg-emerald-400";
          let textColorCls = "text-emerald-400";
          if (pct <= 20) {
            barColorCls = "bg-rose-500";
            textColorCls = "text-rose-400";
          } else if (pct <= 50) {
            barColorCls = "bg-amber-400";
            textColorCls = "text-amber-400";
          }

          return `
            <div onclick="window.appActions.selectModel('${escapeJsSingleQuote(m.id)}', event)"
              class="w-full flex items-center justify-between px-2.5 py-2 text-xs rounded-lg cursor-pointer transition select-none ${
                isSelected
                  ? "bg-zinc-800 border border-zinc-700/80 text-white"
                  : "hover:bg-zinc-900 border border-transparent text-zinc-300"
              }">
              <!-- 左侧：选中圆点 + 模型名称 + 标签 + 推荐星 -->
              <div class="flex items-center gap-2 min-w-0 flex-1 pr-2">
                <div class="w-1.5 h-1.5 rounded-full shrink-0 ${
                  isSelected ? "bg-sky-400 ring-2 ring-sky-400/40" : "bg-zinc-700"
                }"></div>
                <span class="truncate font-medium text-zinc-100 ${isSelected ? "text-white font-semibold" : ""}">${escapeHtml(m.label)}</span>
                ${
                  m.tag
                    ? `<span class="text-[9px] px-1 py-0.2 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30 shrink-0">${escapeHtml(
                        m.tag
                      )}</span>`
                    : ""
                }
                ${
                  m.is_default
                    ? `<span class="text-[9px] px-1 py-0.2 rounded font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0" title="电脑端当前默认模型">默认</span>`
                    : ""
                }
              </div>

              <!-- 右侧：模型右侧直接显示进度条与百分比数值 -->
              <div class="flex items-center gap-1.5 shrink-0">
                <div class="w-10 sm:w-12 h-1.5 rounded-full bg-zinc-800 overflow-hidden shrink-0">
                  <div class="h-full rounded-full transition-all duration-300 ${barColorCls}" style="width: ${pct}%"></div>
                </div>
                <span class="text-[11px] font-mono font-medium ${textColorCls} w-8 text-right shrink-0">${pct}%</span>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function getAgentDisplayName(agentType) {
  const nameMap = {
    claude_code: "Claude",
    agy_ide: "Antigravity",
    agy_cli: "AGY",
    opencode: "OpenCode",
  };
  return nameMap[agentType] || AGENT_META[agentType]?.short || AGENT_META[agentType]?.label || "Agent";
}

function updatePromptInputPlaceholder(sess) {
  const inputEl = document.getElementById("prompt-input");
  const overlayEl = document.getElementById("prompt-input-placeholder");
  if (!inputEl) return;
  if (!sess) {
    if (overlayEl) {
      overlayEl.innerHTML = `<span>发消息...</span>`;
    }
    inputEl.placeholder = " ";
    return;
  }
  const agentName = getAgentDisplayName(sess.agent_type);
  if (overlayEl) {
    overlayEl.innerHTML = `
      <span>发消息给&nbsp;</span><span class="text-[10px] sm:text-xs text-zinc-400 font-mono">${escapeHtml(agentName)}</span><span>...</span>
    `;
  }
  inputEl.placeholder = `发消息给 ${agentName}...`;
}

function renderCurrentSessionHeader() {
  const titleEl = document.getElementById("current-session-title");
  const subtitleEl = document.getElementById("current-session-subtitle");
  const modeSelect = document.getElementById("execution-mode-select");
  const headerLogo = document.getElementById("header-logo-icon");

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess) {
    if (titleEl) {
      titleEl.innerHTML = `<span class="text-xs sm:text-sm font-medium text-zinc-400 truncate">未选择对话</span>`;
    }
    if (subtitleEl) subtitleEl.textContent = "";
    if (headerLogo) headerLogo.classList.remove("has-session-hidden-mobile");
    updatePromptInputPlaceholder(null);
    renderModelPicker();
    updatePromptActionBtnState();
    return;
  }

  // 手机端已选择具体会话后，隐藏顶栏会话名称前的系统钻石图标以释放横向排版空间
  if (headerLogo) {
    headerLogo.classList.add("has-session-hidden-mobile");
  }

  const meta = AGENT_META[sess.agent_type] || AGENT_META.claude_code;

  // 获取项目名称（非工程会话回退至会话 ID 短码）
  const rawProjectName =
    sess.is_project && sess.project_name
      ? sess.project_name
      : (sess.project_name || (sess.project_path ? getProjectInfo(sess.project_path).name : ""));
  const projectName = String(rawProjectName || "").trim();

  // 桌面网页端项目展示（提升对比度与悬停反馈）
  const desktopProjectHtml = projectName
    ? `<span class="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-zinc-100 font-medium font-mono shrink-0 max-w-[200px] truncate transition-colors" title="${escapeHtml(projectName)}">
        <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path></svg>
        <span class="truncate">${escapeHtml(projectName)}</span>
      </span>`
    : `<span class="inline-flex items-center text-xs text-zinc-400 font-mono shrink-0" title="${escapeHtml(sess.raw_session_id || "")}">#${escapeHtml((sess.raw_session_id || "").slice(0, 8))}</span>`;

  // 手机移动端项目展示（会话名称 · 项目名称）
  const mobileProjectHtml = projectName
    ? `<span class="text-zinc-500 text-[11px] shrink-0 select-none">·</span>
       <span class="text-[11px] text-zinc-400 font-mono shrink-0 max-w-[100px] truncate" title="${escapeHtml(projectName)}">${escapeHtml(projectName)}</span>`
    : (sess.raw_session_id
        ? `<span class="text-zinc-500 text-[11px] shrink-0 select-none">·</span>
           <span class="text-[11px] text-zinc-500 font-mono shrink-0">#${escapeHtml((sess.raw_session_id || "").slice(0, 8))}</span>`
        : "");

  // 网页端：项目名称 · 会话名称
  // 手机端：只显示 会话名称 · 项目名称
  if (titleEl) {
    titleEl.innerHTML = `
      <!-- 网页端（sm及以上显示）：项目名称 · 会话名称 -->
      <div class="hidden sm:flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
        ${desktopProjectHtml}
        <span class="text-zinc-500 text-xs shrink-0 select-none">·</span>
        <span class="truncate font-medium text-sm text-zinc-100 min-w-0" title="${escapeHtml(cleanFriendlyTitle(sess.title))}">${escapeHtml(cleanFriendlyTitle(sess.title))}</span>
      </div>

      <!-- 手机端（sm以下显示）：会话名称 · 项目名称 -->
      <div class="flex sm:hidden items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
        <span class="truncate font-medium text-xs text-zinc-100 min-w-0 shrink" title="${escapeHtml(cleanFriendlyTitle(sess.title))}">${escapeHtml(cleanFriendlyTitle(sess.title))}</span>
        ${mobileProjectHtml}
      </div>
    `;
  }
  if (subtitleEl) {
    subtitleEl.textContent = "";
  }
  updatePromptInputPlaceholder(sess);
  if (modeSelect) {
    const activeMode = renderModeOptionsForAgent(modeSelect, sess.agent_type, sess.execution_mode || "ask", true);
    // 若原存储的模式不受该 Agent 支持且发生了自动回退，静默同步更新后端元数据持久化
    if (sess.execution_mode && sess.execution_mode !== activeMode) {
      sess.execution_mode = activeMode;
      apiFetch(`/api/v1/sessions/${encodeURIComponent(sess.unified_id)}/meta`, {
        method: "PATCH",
        body: JSON.stringify({ execution_mode: activeMode }),
      }).catch((err) => console.warn("静默同步纠偏模式失败:", err));
    }
  }
  renderModelPicker();
  updatePromptActionBtnState();
  renderOtherRunningPill();
}

function escapeJsSingleQuote(str) {
  return String(str || "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/"/g, "&quot;")
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
  text = text.replace(/~~([^~]+)~~/g, '<del class="text-zinc-500">$1</del>');
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
            <span class="text-[10px] text-zinc-500">${lineCount} 行</span>
          </div>
          <button type="button" onclick="window.appActions.copyCodeBlock('${escapeJsSingleQuote(
            codeId
          )}', this)"
            class="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-[11px] transition active:scale-95">
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
                class="w-full py-1.5 bg-zinc-900/95 hover:bg-zinc-800/90 border-t border-zinc-800/80 text-sky-400 text-[11px] font-medium text-center transition">
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
            <div class="space-y-1 text-zinc-200">${bodyHtml}</div>
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
            }</span><span class="${checked ? "line-through text-zinc-400" : ""}">${renderInlineMarkdown(
              taskMatch[2]
            )}</span></li>`
          );
        } else if (indentLen >= 2) {
          itemsHtml.push(
            `<li class="list-none ml-3 flex items-start gap-1.5 text-zinc-300"><span class="text-sky-400/80 select-none">•</span><span class="flex-1">${renderInlineMarkdown(
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

function isReadOnlyTool(tc) {
  if (tc && typeof tc.is_read_only === "boolean") return tc.is_read_only;
  const name = String((tc && tc.name) || tc || "").toLowerCase().replace(/-/g, "_");
  return (
    name.includes("read_file") ||
    name.includes("view_file") ||
    name.includes("grep") ||
    name.includes("glob") ||
    name.includes("search_web") ||
    name.includes("file_search")
  );
}

async function copyTextToClipboard(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {}
  }
  try {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.left = "-9999px";
    textArea.style.top = "-9999px";
    textArea.style.opacity = "0";
    textArea.setAttribute("readonly", "");
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    textArea.setSelectionRange(0, text.length);
    const successful = document.execCommand("copy");
    document.body.removeChild(textArea);
    if (successful) return true;
  } catch (_) {}
  return false;
}

function saveToBoundedStore(storeName, key, value, maxItems = 150) {
  if (!state[storeName]) {
    state[storeName] = {};
  }
  const store = state[storeName];
  store[key] = value;
  const keys = Object.keys(store);
  if (keys.length > maxItems) {
    const pruneCount = Math.floor(maxItems / 4);
    for (let i = 0; i < pruneCount; i++) {
      delete store[keys[i]];
    }
  }
}

function applyButtonCheckmark(btnEl, options = {}) {
  if (!btnEl) return;
  const label = options.label || "已复制";
  const iconSize = options.iconSize || "w-3.5 h-3.5";
  const highlightBorder = !!options.highlightBorder;
  const duration = options.duration || 1600;

  if (btnEl._copyResetTimer) {
    clearTimeout(btnEl._copyResetTimer);
  } else {
    btnEl._oldCopyHtml = btnEl.innerHTML;
  }

  btnEl.innerHTML = `
    <svg class="${iconSize} text-emerald-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
    <span class="text-emerald-300 font-medium">${escapeHtml(label)}</span>
  `;

  if (highlightBorder) {
    btnEl.classList.add("border-emerald-500/40", "bg-emerald-500/10");
  }

  btnEl._copyResetTimer = setTimeout(() => {
    if (btnEl._oldCopyHtml !== undefined) {
      btnEl.innerHTML = btnEl._oldCopyHtml;
      delete btnEl._oldCopyHtml;
    }
    if (highlightBorder) {
      btnEl.classList.remove("border-emerald-500/40", "bg-emerald-500/10");
    }
    delete btnEl._copyResetTimer;
  }, duration);
}

function updateCollapseStepsPillVisibility() {
  const pill = document.getElementById("collapse-steps-pill");
  if (!pill) return;
  const hasExpandedTurn =
    Object.values(state.expandedTurns || {}).some(Boolean) ||
    Object.values(state.turnThinkingExpanded || {}).some(Boolean);
  const container = document.getElementById("messages-container");
  const isScrolledDown = container ? container.scrollTop > 80 : false;
  pill.classList.toggle("hidden", !(hasExpandedTurn && isScrolledDown));
}

function renderUnifiedDiffHtml(diffText, filePath = "", options = {}) {
  if (!diffText) return "";
  const lines = diffText.split("\n");
  let addCount = 0;
  let delCount = 0;

  const renderedLines = lines
    .map((line) => {
      let cls = "diff-line text-zinc-300";
      if (line.startsWith("+++") || line.startsWith("---")) {
        cls = "diff-line text-zinc-400 font-semibold";
      } else if (line.startsWith("@@")) {
        cls = "diff-line diff-line-hunk";
      } else if (line.startsWith("+")) {
        cls = "diff-line diff-line-add";
        addCount++;
      } else if (line.startsWith("-")) {
        cls = "diff-line diff-line-del";
        delCount++;
      }
      return `<div class="${cls}">${escapeHtml(line)}</div>`;
    })
    .join("");

  const fileName = filePath ? getBasename(filePath) : "";
  const isModal = !!options.isModal;
  const finalAddCount = typeof options.addCount === "number" ? options.addCount : addCount;
  const finalDelCount = typeof options.delCount === "number" ? options.delCount : delCount;

  // 避免在 HTML 属性中拼接大字符串导致单引号/特殊字符截断失效：统一注册到带容量上限的内存存储
  const diffId = options.diffId || `diff_${Math.random().toString(36).slice(2, 9)}`;
  saveToBoundedStore("diffStore", diffId, {
    diffText,
    filePath,
    addCount: finalAddCount,
    delCount: finalDelCount,
  });

  return `
    <div class="unified-diff-card">
      <div class="unified-diff-header">
        <div class="flex items-center gap-1.5 min-w-0">
          <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <span class="font-mono font-medium text-zinc-200 truncate text-[11px]" title="${escapeHtml(filePath)}">
            ${escapeHtml(fileName || "代码变更")}
          </span>
          <div class="flex items-center gap-1 font-mono shrink-0 ml-1">
            <span class="diff-stat-badge diff-stat-add">+${finalAddCount}</span>
            <span class="diff-stat-badge diff-stat-del">-${finalDelCount}</span>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button type="button" data-action="toggle-word-wrap" onclick="window.appActions.toggleWordWrap()" class="text-zinc-400 hover:text-sky-300 text-[10px] transition">
            ${state.wordWrapDiff ? "横向滚动" : "自动换行"}
          </button>
          ${
            !isModal
              ? `<button type="button" onclick="window.appActions.openDiffFullscreenById('${escapeJsSingleQuote(diffId)}', event)" class="px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-sky-300 text-[10px] flex items-center gap-0.5 transition active:scale-95" title="全屏查看 Diff">
                  <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
                  <span>全屏</span>
                </button>`
              : ""
          }
        </div>
      </div>
      <div class="unified-diff-box ${state.wordWrapDiff ? "word-wrap" : ""} ${
        !isModal ? "max-h-64" : ""
      } overflow-y-auto">
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
          thinking_len: step.thinking_len || (hasThinking ? step.thinking.trim().length : 0),
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

  const isExpanded = !!state.expandedTurns[turnKey] || !!state.turnThinkingExpanded[turnKey];
  const totalActions =
    toolCount + thinkingCount + processRecords.filter((r) => r.intermediateNote).length;

  // 构建折叠栏上的精炼徽章
  const badges = [];
  if (editedFiles.length > 0) {
    const fileHint =
      editedFiles.length === 1
        ? `修改 ${editedFiles[0]}`
        : `修改 ${editedFiles.length} 个文件`;
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[10px] font-medium shrink-0 flex items-center gap-1">
        <svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span>${escapeHtml(fileHint)}</span>
      </span>`
    );
  }
  if (hasHighRisk) {
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/35 text-[10px] font-semibold shrink-0 flex items-center gap-1">
        <svg class="w-3 h-3 text-rose-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>含高危操作</span>
      </span>`
    );
  }
  if (toolCount > 0) {
    badges.push(
      `<span class="text-[11px] text-zinc-400 flex items-center gap-1 shrink-0">
        <svg class="w-3 h-3 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
        <span>${toolCount} 次调用</span>
      </span>`
    );
  }
  if (thinkingCount > 0) {
    badges.push(
      `<span class="text-[11px] text-zinc-400 flex items-center gap-1 shrink-0">
        <svg class="w-3 h-3 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>${thinkingCount} 次思考</span>
      </span>`
    );
  }

  const detailsHtml = isExpanded
    ? `<div class="mt-2 pt-2 border-t border-zinc-800/80 space-y-2">
        ${processRecords
          .map((rec) => {
            const thinkDetailId = `${turnKey}_think_${rec.step_index}`;
            const isThinkExplicitlySet = typeof state.expandedDetails[thinkDetailId] === "boolean";
            const isThinkOpen = isThinkExplicitlySet
              ? Boolean(state.expandedDetails[thinkDetailId])
              : Boolean(state.turnThinkingExpanded[turnKey]);
            if (rec.thinking) {
              saveToBoundedStore("lazyDetailsStore", thinkDetailId, {
                type: "thinking",
                raw: rec.thinking,
              });
            }
            const displayThinkLen =
              rec.thinking_len || (rec.thinking ? rec.thinking.length : 0);
            const thinkHtml = rec.thinking
              ? `
                <details data-detail-id="${escapeHtml(thinkDetailId)}" ${
                  isThinkOpen ? "open" : ""
                } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                  thinkDetailId
                )}', '${escapeJsSingleQuote(turnKey)}')" class="rounded-lg bg-zinc-950/80 border border-zinc-800/80 px-2.5 py-1.5 text-[11px] text-zinc-400">
                  <summary onclick="window.appActions.onThinkSummaryClick(event, '${escapeJsSingleQuote(
                    turnKey
                  )}', '${escapeJsSingleQuote(thinkDetailId)}')" class="cursor-pointer select-none font-medium text-zinc-300 flex items-center justify-between">
                    <span class="flex items-center gap-1.5 min-w-0">
                      <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a8 8 0 0 0-8 8c0 3.2 1.9 6 4.7 7.3V19a2 2 0 0 0 2 2h2.6a2 2 0 0 0 2-2v-1.7c2.8-1.3 4.7-4.1 4.7-7.3a8 8 0 0 0-8-8z"/><path d="M10 22h4"/></svg>
                      <span class="truncate">思考过程 (${displayThinkLen}字)</span>
                    </span>
                    <button type="button" onclick="window.appActions.copyThinkingText('${escapeJsSingleQuote(
                      thinkDetailId
                    )}', this, event)" class="px-1.5 py-0.5 rounded text-[10px] text-zinc-400 hover:text-sky-300 hover:bg-zinc-800/80 transition active:scale-95 flex items-center gap-1" title="复制思考内容">
                      <svg class="w-3 h-3 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                      <span>复制</span>
                    </button>
                  </summary>
                  <div data-lazy-slot class="mt-1.5 pt-1.5 border-t border-zinc-800/70 whitespace-pre-wrap leading-relaxed text-zinc-400 font-mono text-[11px] max-h-52 overflow-y-auto">${
                    isThinkOpen ? escapeHtml(rec.thinking) : ""
                  }</div>
                </details>
              `
              : "";

            const noteHtml = rec.intermediateNote
              ? `
                <div class="px-2.5 py-1.5 rounded-lg bg-zinc-900/70 border border-zinc-800/60 text-xs text-zinc-300">
                  <span class="text-[10px] text-sky-400 font-semibold mr-1 inline-flex items-center gap-0.5">
                    <svg class="w-3 h-3 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <span>过程说明:</span>
                  </span>
                  ${escapeHtml(rec.intermediateNote)}
                </div>
              `
              : "";

            const allTools = rec.tool_calls || [];
            const readTools = [];
            const actionTools = [];
            allTools.forEach((tc) => {
              if (isReadOnlyTool(tc)) {
                readTools.push(tc);
              } else {
                actionTools.push(tc);
              }
            });

            // 1. 只读探索类工具（如 read_file、grep）：紧凑微型胶囊流，不占纵向屏幕
            const readChipsHtml =
              readTools.length > 0
                ? `
                  <div class="read-tool-chips-row">
                    <span class="text-[10px] text-zinc-500 font-medium mr-1 select-none flex items-center gap-1 shrink-0">
                      <svg class="w-3 h-3 text-zinc-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>
                      查阅 (${readTools.length}):
                    </span>
                    ${readTools
                      .map((tc) => {
                        const target = getBasename(tc.file_path || tc.args_summary) || tc.args_summary;
                        return `
                          <span class="read-tool-chip" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                            <svg class="w-3 h-3 text-zinc-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                            <span class="truncate max-w-[150px]">${escapeHtml(target)}</span>
                          </span>
                        `;
                      })
                      .join("")}
                  </div>
                `
                : "";

            // 2. 变更类（edit_file/write_file）与执行类工具：高亮呈现
            const actionToolsHtml = actionTools
              .map((tc, tcIdx) => {
                const isHigh = tc.risk_level === "high";
                const isEditTool = !!tc.diff_text || /edit|write|replace|create/i.test(tc.name || "");
                const diffDetailId = `${turnKey}_diff_${rec.step_index}_${tcIdx}`;
                // 用户要求：思考过程里的 edit_file 默认不展开
                const isDiffOpen = Boolean(state.expandedDetails[diffDetailId]);
                if (tc.diff_text) {
                  saveToBoundedStore("lazyDetailsStore", diffDetailId, {
                    type: "diff",
                    raw: tc.diff_text,
                    filePath: tc.file_path || tc.args_summary || "",
                    addCount: tc.diff_add_count,
                    delCount: tc.diff_del_count,
                  });
                }

                const outDetailId = `${turnKey}_out_${rec.step_index}_${tcIdx}`;
                const isOutOpen = !!state.expandedDetails[outDetailId];
                if (tc.output) {
                  saveToBoundedStore("lazyDetailsStore", outDetailId, {
                    type: "output",
                    raw: tc.output,
                  });
                }

                const cardClass = isEditTool
                  ? "edit-tool-card"
                  : `rounded-lg bg-zinc-950/90 border ${
                      isHigh ? "border-rose-500/45" : "border-zinc-800/90"
                    } p-2 text-xs`;

                const badgeBg = isEditTool
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-zinc-800 text-sky-300";
                const badgeSvg = isEditTool
                  ? `<svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`
                  : `<svg class="w-3 h-3 text-sky-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`;
                const targetText = isEditTool
                  ? getBasename(tc.file_path || tc.args_summary) || tc.args_summary
                  : tc.args_summary;

                return `
                  <div class="${cardClass}">
                    <div class="flex items-center justify-between gap-2">
                      <div class="flex items-center gap-1.5 min-w-0">
                        <span class="px-1.5 py-0.5 rounded ${badgeBg} font-mono font-semibold text-[10px] flex items-center gap-1 shrink-0">
                          ${badgeSvg}
                          <span>${escapeHtml(tc.name)}</span>
                        </span>
                        <span class="font-mono text-[11px] ${
                          isEditTool ? "text-zinc-100 font-semibold" : "text-zinc-300"
                        } truncate" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                          ${escapeHtml(targetText)}
                        </span>
                      </div>
                      <span class="text-[9px] text-zinc-500 font-mono shrink-0">${escapeHtml(
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
                          )}')" class="mt-1.5 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer select-none font-medium flex items-center justify-between text-emerald-300/90 hover:text-emerald-200 py-1 px-1 rounded hover:bg-emerald-500/10 transition">
                              <span class="flex items-center gap-1.5 min-w-0">
                                <svg class="w-3.5 h-3.5 text-emerald-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                <span class="truncate">代码变更 Diff</span>
                              </span>
                              <div class="flex items-center gap-1.5 font-mono text-[9px] shrink-0">
                                ${
                                  typeof tc.diff_add_count === "number"
                                    ? `<span class="diff-stat-badge diff-stat-add">+${tc.diff_add_count}</span>`
                                    : ""
                                }
                                ${
                                  typeof tc.diff_del_count === "number"
                                    ? `<span class="diff-stat-badge diff-stat-del">-${tc.diff_del_count}</span>`
                                    : ""
                                }
                                <span class="text-zinc-500 font-sans ml-0.5">
                                  <span class="toggle-arrow-open">收起 ▴</span>
                                  <span class="toggle-arrow-closed">展开 ▾</span>
                                </span>
                              </div>
                            </summary>
                            <div data-lazy-slot class="mt-1">${
                              isDiffOpen
                                ? renderUnifiedDiffHtml(tc.diff_text, tc.file_path || tc.args_summary, {
                                    addCount: tc.diff_add_count,
                                    delCount: tc.diff_del_count,
                                    diffId: diffDetailId,
                                  })
                                : ""
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
                          )}')" class="mt-1.5 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer text-zinc-400 hover:text-zinc-200 select-none flex items-center justify-between">
                              <span class="flex items-center gap-1.5 min-w-0">
                                <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                                <span class="truncate">查看执行输出 ${
                                  tc.is_truncated ? `(${tc.full_output_len}字·已截断)` : ""
                                }</span>
                              </span>
                              ${
                                tc.is_truncated
                                  ? `<button onclick="window.appActions.loadFullOutput('${escapeHtml(
                                      state.selectedSessionId
                                    )}', ${rec.step_index}, this, event)" class="px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/30 hover:bg-sky-500/30 text-[9px] shrink-0">加载完整输出</button>`
                                  : ""
                              }
                            </summary>
                            <pre data-lazy-slot class="mt-1 p-2 rounded bg-black/70 overflow-x-auto font-mono text-[10px] text-zinc-300 whitespace-pre-wrap break-all max-h-44 overflow-y-auto">${
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

            return `<div class="space-y-1.5">${thinkHtml}${noteHtml}${readChipsHtml}${actionToolsHtml}</div>`;
          })
          .join("")}
        <div class="pt-2 text-center border-t border-zinc-800/60">
          <button type="button" onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
            class="px-3 py-1 rounded-lg bg-zinc-950/80 hover:bg-zinc-800 border border-zinc-800 text-[11px] text-zinc-400 hover:text-sky-300 font-medium transition active:scale-95 inline-flex items-center gap-1">
            <svg class="w-3.5 h-3.5 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 15l-6-6-6 6"/></svg>
            <span>收起已展开步骤</span>
          </button>
        </div>
      </div>`
    : "";

  return `
    <div id="turn-process-wrap-${escapeHtml(turnKey)}" class="mb-2.5 rounded-xl bg-zinc-900/65 border border-zinc-800/85 px-3 py-2">
      <div onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
        class="cursor-pointer select-none flex items-center justify-between gap-2">
        <div class="flex items-center gap-1.5 flex-wrap min-w-0">
          <span class="text-xs font-semibold text-zinc-200 flex items-center gap-1.5 shrink-0">
            <svg class="w-3.5 h-3.5 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
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
              ? "bg-sky-500 text-zinc-950 font-bold border-sky-400 shadow-sm"
              : "bg-zinc-900/80 hover:bg-zinc-800 text-zinc-200 border-zinc-700/80"
          }">
          <span class="text-[10px] ${isSelected ? "text-zinc-950" : "text-sky-400"}">${
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
      <details class="mt-2 rounded-xl bg-zinc-950/70 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
        <summary class="cursor-pointer select-none font-medium text-zinc-300 hover:text-sky-300 flex items-center justify-between">
          <span class="${decision ? "text-sky-300 font-semibold" : ""}">${summaryTitle}</span>
          <span class="text-[10px] text-zinc-500">查看明细 ▾</span>
        </summary>
        <div class="mt-2 pt-2 border-t border-zinc-800/70 space-y-2">
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
                  <div class="font-semibold text-zinc-200">Q${qi + 1}: ${escapeHtml(q.question)}</div>
                  ${
                    q.options && q.options.length > 0
                      ? `<div class="mt-1 pl-3 text-[11px] text-zinc-400 space-y-0.5">
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
          <div class="text-xs font-semibold text-zinc-100 flex items-start gap-1.5">
            <span class="text-sky-400 shrink-0">Q${qi + 1}:</span>
            <span class="whitespace-pre-wrap">${escapeHtml(q.question)}</span>
            ${
              isMulti
                ? `<span class="px-1 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[9px] shrink-0">多选</span>`
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
              class="w-full px-2.5 py-1 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-sky-500" />
          </div>
        </div>
      `;
    })
    .join('<div class="border-t border-zinc-800/60 my-2"></div>');

  return `
    <div class="mt-2.5 rounded-xl bg-zinc-900/90 border border-sky-500/40 p-3 shadow-lg space-y-2">
      <div class="flex items-center justify-between text-xs text-sky-400 font-semibold border-b border-zinc-800 pb-1.5">
        <span class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-sky-400 animate-ping"></span>
          <span>❓ Agent 正在向您提问确认</span>
        </span>
        <span class="text-[10px] text-zinc-400">单手点选即答</span>
      </div>
      <div class="space-y-2">${questionsHtml}</div>
      <div class="flex items-center justify-end gap-2 pt-1 border-t border-zinc-800/80">
        <button type="button"
          onclick="window.appActions.skipQuestionAnswers('${escapeJsSingleQuote(turnKey)}')"
          class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs transition">
          按推荐执行
        </button>
        <button type="button"
          onclick="window.appActions.submitQuestionAnswers('${escapeJsSingleQuote(turnKey)}')"
          class="px-3.5 py-1 rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs shadow transition active:scale-95 flex items-center gap-1">
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
                <div class="text-[10px] text-zinc-400">点击全屏阅读 Markdown 文档</div>
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

  if (!state.selectedSessionId) {
    container.innerHTML = `
      <div class="h-full flex flex-col items-center justify-center p-6 text-center text-zinc-400">
        <div class="w-12 h-12 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center text-sky-400 mb-3 shadow-sm">
          <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"></path></svg>
        </div>
        <div class="text-sm font-semibold text-zinc-100 mb-1">选择对话或直接发消息</div>
        <p class="text-xs text-zinc-500 max-w-xs mb-4">
          多 Agent 移动控制台 · Antigravity / Claude Code / OpenCode
        </p>
        <button onclick="window.appActions.openNewSessionModal()"
          class="px-4 py-2 rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs transition active:scale-95 shadow">
          + 新建对话
        </button>
      </div>
    `;
    return;
  }

  const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  const isWorking = isSessionWorking(state.selectedSessionId);

  if (state.messages.length === 0) {
    if (!isWorking) {
      const pPath = curSess?.project_path || "未绑定目录";
      const agentLabel = (AGENT_META[curSess?.agent_type] || AGENT_META.claude_code).label;
      container.innerHTML = `
        <div class="h-full flex flex-col items-center justify-center p-6 text-center text-zinc-400 max-w-md mx-auto">
          <div class="w-11 h-11 rounded-2xl bg-zinc-900 border border-zinc-800 flex items-center justify-center text-sky-400 mb-2.5 shadow-sm">
            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
          </div>
          <div class="text-sm font-semibold text-zinc-100 mb-1">${escapeHtml(curSess?.title || "新会话")}</div>
          <div class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-zinc-900 border border-zinc-800 text-[11px] font-mono text-zinc-400 mb-4 max-w-full truncate">
            <span class="text-sky-400 font-sans font-medium">${escapeHtml(agentLabel)}</span>
            <span class="text-zinc-600">·</span>
            <span class="truncate">${escapeHtml(pPath)}</span>
          </div>
          <div class="w-full space-y-2 text-left">
            <div class="text-[11px] font-medium text-zinc-500 px-1">快速开始 / Starter Prompts：</div>
            <button onclick="window.appActions.fillPromptAndSend('查看当前代码仓库的 Git 状态与最近修改差异')"
              class="w-full p-2.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-200 transition text-left flex items-center justify-between group active:scale-[0.99]">
              <span>🔍 查看当前 Git 改动与状态差异</span>
              <span class="text-sky-400 text-xs group-hover:translate-x-0.5 transition-transform">→</span>
            </button>
            <button onclick="window.appActions.fillPromptAndSend('帮我审查当前项目的代码架构并指出潜在漏洞与优化点')"
              class="w-full p-2.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-200 transition text-left flex items-center justify-between group active:scale-[0.99]">
              <span>📋 审查项目代码架构并指出缺陷</span>
              <span class="text-sky-400 text-xs group-hover:translate-x-0.5 transition-transform">→</span>
            </button>
            <button onclick="window.appActions.fillPromptAndSend('梳理当前项目的开发计划与待办实施清单')"
              class="w-full p-2.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-200 transition text-left flex items-center justify-between group active:scale-[0.99]">
              <span>⚡ 梳理接下来待办任务与规划 (/plan)</span>
              <span class="text-sky-400 text-xs group-hover:translate-x-0.5 transition-transform">→</span>
            </button>
          </div>
        </div>
      `;
      return;
    }
  }

  const turns = groupMessagesIntoTurns(state.messages);
  const lastTurn = turns[turns.length - 1];
  const thinkingStart = state.pendingTurnThinking ? state.pendingTurnThinking[state.selectedSessionId] : null;
  if (thinkingStart && lastTurn && (lastTurn.type === "agent" || lastTurn.type === "assistant")) {
    const turnTime = new Date(lastTurn.createdAt || 0).getTime();
    const lastStep = lastTurn.primarySteps ? lastTurn.primarySteps[lastTurn.primarySteps.length - 1] : null;
    const isLastStepRunning = lastStep && lastStep.status === "RUNNING";
    if (turnTime >= thinkingStart - 1000 && !isLastStepRunning && !isSessionWorking(state.selectedSessionId)) {
      delete state.pendingTurnThinking[state.selectedSessionId];
    }
  }
  state.turnsMap = {};

  const olderBannerHtml = state.hasMoreMessages
    ? `
      <div class="mb-4 text-center">
        <button id="load-older-messages-btn" type="button" onclick="window.appActions.loadOlderMessages()"
          class="px-3.5 py-1.5 rounded-xl bg-zinc-900/90 hover:bg-zinc-800 border border-zinc-700/80 text-xs text-sky-300 font-medium transition active:scale-95">
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
            <div class="max-w-[88%] sm:max-w-[76%] rounded-2xl rounded-tr-sm bg-zinc-900 border border-zinc-800 px-3.5 py-2.5 text-zinc-100 shadow-sm">
              <div id="user-msg-text-${escapeHtml(
                m.item_id
              )}" class="text-xs sm:text-[13px] leading-relaxed whitespace-pre-wrap break-words">${escapeHtml(
          displayUserText
        )}</div>
              ${
                isLongUser
                  ? `<button id="user-msg-btn-${escapeHtml(
                      m.item_id
                    )}" type="button" onclick="window.appActions.toggleUserMessage('${escapeJsSingleQuote(
                      m.item_id
                    )}')" class="mt-1 text-[11px] text-sky-400 font-medium hover:underline">
                      ${isUserExpanded ? "收起 ▴" : `展开全部 (${rawUserText.length}字) ▾`}
                    </button>`
                  : ""
              }
              <div class="text-[10px] text-zinc-500 text-right mt-1">${formatRelativeTime(
                m.created_at
              )}</div>
            </div>
          </div>
        `;
      }

      // Assistant Turn（沉浸式全宽文档流版式，无突兀分割线）
      const processBarHtml = renderTurnProcessBar(t);
      const mdBodyHtml = t.primarySteps
        .map((ps) => renderMarkdownHtml(ps.content, `${t.turnKey}_s${ps.step_index}`))
        .join('<div class="my-3"></div>');

      // 若本轮仅有中间过程说明而尚无最终正文，展示过程说明
      let fallbackStatusHtml = "";
      if (!mdBodyHtml && t.processRecords.length > 0) {
        const lastRec = t.processRecords[t.processRecords.length - 1];
        if (lastRec.intermediateNote) {
          fallbackStatusHtml = renderMarkdownHtml(lastRec.intermediateNote, `${t.turnKey}_note`);
        }
      }

      const mdCardsHtml = renderMdDocQuickCards(t.mdFiles);
      const isLastTurn = turns.indexOf(t) === turns.length - 1;
      const questionsCardHtml =
        t.questions && t.questions.length > 0 && isLastTurn
          ? renderInteractiveQuestionsCard(t, isLastTurn)
          : "";

      return `
        <article class="mb-6">
          ${processBarHtml}
          ${
            mdBodyHtml || fallbackStatusHtml
              ? `<div class="px-1 sm:px-2">${mdBodyHtml || fallbackStatusHtml}</div>`
              : ""
          }
          ${questionsCardHtml ? `<div class="px-1 sm:px-2">${questionsCardHtml}</div>` : ""}
          ${mdCardsHtml ? `<div class="px-1 sm:px-2">${mdCardsHtml}</div>` : ""}
          <div class="px-1 sm:px-2 mt-1.5 text-[10px] text-zinc-600 text-right">
            <span>${formatRelativeTime(t.createdAt)}</span>
          </div>
        </article>
      `;
    })
    .join("");

  container.innerHTML =
    `<div class="max-w-5xl mx-auto">` +
    olderBannerHtml +
    html +
    `<div id="live-stream-overlay-slot"></div><div id="messages-bottom-anchor"></div></div>`;
  renderLiveStreamOverlay();

  if (!state.autoScrollLocked) {
    scrollToBottom();
  }
  updateCollapseStepsPillVisibility();
}

function isSessionWorking(unifiedId) {
  if (!unifiedId) return false;
  // 1. 内存中记录了正在等待回复的时间戳
  if (state.pendingTurnThinking && state.pendingTurnThinking[unifiedId]) {
    return true;
  }
  // 2. 正在接收流式缓冲区输出
  if (state.streamingBuffer && state.streamingBuffer[unifiedId]) {
    return true;
  }
  // 3. 本地存在未确认的乐观消息
  const optList = state.optimisticMessages ? state.optimisticMessages[unifiedId] : null;
  if (optList && optList.length > 0) {
    return true;
  }
  // 4. 会话列表中的状态为 running 或 desktop_active（覆盖桌面端 Antigravity IDE 与 CLI 活跃任务）
  const sess = state.sessions.find((s) => s.unified_id === unifiedId);
  if (sess && (sess.status === "running" || sess.status === "desktop_active")) {
    return true;
  }
  const allSess = state.allSessions.find((s) => s.unified_id === unifiedId);
  if (allSess && (allSess.status === "running" || allSess.status === "desktop_active")) {
    return true;
  }
  // 5. 若当前选中的正是该会话，检查最新一条消息或其步骤是否为 RUNNING
  if (state.selectedSessionId === unifiedId && state.messages && state.messages.length > 0) {
    const lastMsg = state.messages[state.messages.length - 1];
    if (lastMsg && lastMsg.status === "RUNNING") {
      return true;
    }
    if (lastMsg && Array.isArray(lastMsg.tool_calls)) {
      const hasRunningTool = lastMsg.tool_calls.some(
        (tc) => tc && (tc.status === "RUNNING" || tc.status === "PENDING")
      );
      if (hasRunningTool) return true;
    }
  }
  return false;
}

function formatElapsedSec(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

function updatePromptActionBtnState() {
  const btn = document.getElementById("prompt-action-btn");
  const btnText = document.getElementById("prompt-action-btn-text");
  const inputEl = document.getElementById("prompt-input");
  const overlayEl = document.getElementById("prompt-input-placeholder");
  if (!btn) return;

  const isWorking = isSessionWorking(state.selectedSessionId);
  const hasText = Boolean(inputEl && inputEl.value.trim().length > 0);

  if (overlayEl) {
    if (hasText) {
      overlayEl.classList.add("!hidden");
    } else {
      overlayEl.classList.remove("!hidden");
    }
  }

  // 运行中的会话：如果无输入内容显示「中断」，如果有输入内容显示「发送」（跟客户端交互完全一致）
  if (isWorking && !hasText) {
    btn.dataset.action = "abort";
    btn.className =
      "shrink-0 h-10 px-3.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs sm:text-sm shadow active:scale-95 transition flex items-center justify-center m-0";
    if (btnText) {
      btnText.textContent = "中断";
    }
    btn.setAttribute("title", "中断当前执行 (SIGINT)");
  } else {
    btn.dataset.action = "send";
    btn.className =
      "shrink-0 h-10 px-3.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs sm:text-sm shadow active:scale-95 transition flex items-center justify-center m-0";
    if (btnText) {
      btnText.textContent = "发送";
    }
    btn.setAttribute("title", "发送指令");
  }
}

function handlePromptAction() {
  const btn = document.getElementById("prompt-action-btn");
  const action = btn?.dataset?.action || "send";
  if (action === "abort") {
    handleAbortCurrentSession();
  } else {
    handleSendPrompt(null);
  }
}

let liveStreamRaf = null;

function renderLiveStreamOverlay() {
  if (liveStreamRaf) return;
  liveStreamRaf = requestAnimationFrame(() => {
    liveStreamRaf = null;
    _doRenderLiveStreamOverlay();
  });
}

function _doRenderLiveStreamOverlay() {
  const slot = document.getElementById("live-stream-overlay-slot");
  updatePromptActionBtnState();
  if (!slot || !state.selectedSessionId) return;

  const isWorking = isSessionWorking(state.selectedSessionId);
  const liveText = state.streamingBuffer[state.selectedSessionId];

  if (!isWorking && !liveText) {
    if (slot.innerHTML !== "") slot.innerHTML = "";
    return;
  }

  // Agent 工作中，在对话区域显示 Working... 与实时生成流（RAF 帧对齐渲染）
  const streamHtml = liveText
    ? `<div class="mt-2 text-xs text-zinc-200 border-t border-zinc-800/60 pt-2">${renderMarkdownHtml(
        liveText.slice(-3000),
        "live_stream"
      )}</div>`
    : "";

  slot.innerHTML = `
    <div class="mb-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-300 shadow-sm transition-all">
      <div class="flex items-center gap-2 font-mono text-[11px] text-sky-400">
        <span class="relative flex h-2 w-2">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-sky-400"></span>
        </span>
        <span class="tracking-wider font-semibold">Working...</span>
      </div>
      ${streamHtml}
    </div>
  `;

  if (!state.autoScrollLocked) {
    scrollToBottom();
  }
  updateCollapseStepsPillVisibility();
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
        class="px-2 py-0.5 rounded bg-amber-400 text-zinc-950 font-semibold shrink-0">
        处理
      </button>
    </div>
  `;

  const activeApproval =
    list.find((p) => p.unified_id === state.selectedSessionId) || list[0];
  const isHighRisk = activeApproval.risk_level === "high";

  bottomBar.classList.remove("hidden");
  bottomBar.innerHTML = `
    <div class="p-2.5 bg-zinc-900/98 border-t-2 ${
      isHighRisk ? "border-rose-500" : "border-amber-500"
    } shadow-2xl">
      <div class="max-w-5xl mx-auto">
        <div class="flex items-center justify-between gap-2 mb-1">
        <div class="flex items-center gap-1.5">
          <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${
            isHighRisk
              ? "bg-rose-500/25 text-rose-300 border border-rose-500/40"
              : "bg-amber-500/25 text-amber-300 border border-amber-500/40"
          }">
            ${isHighRisk ? "⚠️ 高危拦截" : "🛡️ 权限审批"}
          </span>
          <span class="font-mono text-xs font-semibold text-zinc-200">${escapeHtml(
            activeApproval.tool_name
          )}</span>
        </div>
      </div>

      <div class="font-mono text-[11px] text-zinc-200 bg-black/60 border border-zinc-800 rounded-lg px-2 py-1 mb-1.5 break-all max-h-20 overflow-y-auto">
        ${escapeHtml(activeApproval.args_summary)}
      </div>

      ${activeApproval.diff_text ? renderUnifiedDiffHtml(activeApproval.diff_text, activeApproval.args_summary) : ""}

      ${
        state.denyFeedbackOpen
          ? `
            <div class="mt-2 p-2 rounded-xl bg-zinc-950 border border-rose-500/40 space-y-2">
              <div class="text-[11px] text-rose-300 font-semibold flex items-center justify-between">
                <span>✕ 拒绝原因与指导意见 (将传给 Agent 调整方案)</span>
                <button type="button" onclick="window.appActions.toggleDenyFeedback(false)" class="text-zinc-400 hover:text-white px-1">收起</button>
              </div>
              <textarea id="deny-feedback-input" rows="2"
                placeholder="告诉 Agent 为什么拒绝或应该改成什么方案（例如：不要删这个函数，改成可选参数）..."
                class="w-full px-2.5 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-rose-400 resize-none"></textarea>
              <div class="flex items-center justify-end gap-2">
                <button type="button"
                  onclick="window.appActions.submitApproval('${escapeHtml(
                    activeApproval.unified_id
                  )}', 'denied')"
                  class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs">
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
                      class="hold-approve-btn col-span-8 min-h-[42px] rounded-xl bg-amber-500 text-zinc-950 font-bold text-xs shadow-lg">
                      <div class="hold-progress-fill"></div>
                      <span class="relative z-10">⚡ 长按 0.8s 允许</span>
                    </button>
                  `
                  : `
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approve_session_edits')"
                      class="col-span-3 min-h-[42px] rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 font-medium text-[11px] active:scale-95 transition">
                      免审
                    </button>
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approved')"
                      class="col-span-5 min-h-[42px] rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-xs shadow-lg active:scale-95 transition">
                      ✓ 允许
                    </button>
                  `
              }
            </div>
          `
      }
      </div>
    </div>
  `;
}


let qrExpireTimer = null;

function startQrExpireCountdown() {
  if (qrExpireTimer) {
    clearTimeout(qrExpireTimer);
    qrExpireTimer = null;
  }
  const expAtSec = Number(state.systemStatus?.pair_code_expires_at || 0);
  const nowSec = Date.now() / 1000;
  if (expAtSec > 0 && expAtSec <= nowSec) {
    state.qrExpired = true;
    renderQrModalContent();
    return;
  }
  state.qrExpired = false;
  state.qrGeneratedAt = Date.now();
  const remainMs = expAtSec > 0 ? Math.max(1000, Math.floor((expAtSec - nowSec) * 1000)) : 600 * 1000;
  qrExpireTimer = setTimeout(() => {
    state.qrExpired = true;
    renderQrModalContent();
  }, remainMs);
}

function renderQrModalContent() {
  const box = document.getElementById("qr-modal-body");
  if (!box || !state.systemStatus) return;

  // 1. 若刚刚接收到设备配对成功的实时广播，展示精致的双向握手成功卡片
  if (state.pairedSuccessNotice) {
    const dev = state.pairedSuccessNotice.device_name || "移动设备";
    const ip = state.pairedSuccessNotice.client_ip || "";
    box.innerHTML = `
      <div class="py-6 px-4 flex flex-col items-center justify-center text-center space-y-3.5 animate-fadeIn">
        <div class="w-14 h-14 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shadow-[0_0_24px_rgba(16,185,129,0.25)]">
          <svg class="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="space-y-1">
          <div class="text-base font-bold text-zinc-100">手机已成功连接！</div>
          <div class="text-xs text-zinc-400 font-mono flex items-center justify-center gap-1.5">
            <span>${escapeHtml(dev)}</span>
            ${ip ? `<span class="text-zinc-600">·</span><span>${escapeHtml(ip)}</span>` : ""}
          </div>
        </div>
        <div class="pt-2 text-[11px] text-zinc-500 flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
          <span>窗口即将自动关闭...</span>
        </div>
      </div>
    `;
    return;
  }

  const { pair_url, lan_pair_url, zt_pair_url, channels } = state.systemStatus;
  const orderWeight = { tailscale: 1, zerotier: 2, wifi: 3 };
  const channelList = (channels || []).slice().sort((a, b) => (orderWeight[a.type] || 9) - (orderWeight[b.type] || 9));

  if (!state.selectedQrNet) {
    // 默认绝对优先展示 Tailscale（支持随时随地全网随身漫游），其次 ZeroTier，严禁在有 VPN 时优先展示 Wi-Fi 直连
    const hasTs = channelList.some((c) => c.type === "tailscale");
    const hasZt = channelList.some((c) => c.type === "zerotier");
    if (hasTs) {
      state.selectedQrNet = "tailscale";
    } else if (hasZt) {
      state.selectedQrNet = "zerotier";
    } else {
      state.selectedQrNet = channelList[0]?.type || "wifi";
    }
  }

  const curChannel = channelList.find((c) => c.type === state.selectedQrNet) || channelList[0] || {};
  const activeUrl = curChannel.pair_url || curChannel.url || pair_url || "";
  const tokenParam = encodeURIComponent(state.token || "");
  const qrImgUrl = `/api/v1/auth/qr.svg?net=${encodeURIComponent(state.selectedQrNet)}&token=${tokenParam}&_t=${state.qrGeneratedAt || 0}`;

  const channelTips = {
    tailscale: "全网随身漫游 · 手机开启 Tailscale 随时秒连",
    zerotier: "虚拟隧道直连 · 手机开启 ZeroTier 隧道直连",
    wifi: "局域网免密直连 · 仅限当前同一 Wi-Fi 连接",
  };

  const channelIcons = {
    tailscale: `<svg class="w-3.5 h-3.5 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>`,
    zerotier: `<svg class="w-3.5 h-3.5 text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>`,
    wifi: `<svg class="w-3.5 h-3.5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"></path><path d="M1.42 9a16 16 0 0 1 21.16 0"></path><path d="M8.53 16.11a6 6 0 0 1 6.95 0"></path><line x1="12" y1="20" x2="12.01" y2="20"></line></svg>`,
  };

  box.innerHTML = `
    <div class="space-y-3">
      <!-- 极简三通道切换胶囊 (精细化矢量排版) -->
      <div class="grid grid-cols-3 gap-1.5 w-full">
        ${channelList
          .map((c) => {
            const isSelected = c.type === state.selectedQrNet;
            return `
              <button type="button" onclick="window.appActions.switchQrChannel('${c.type}')"
                class="py-1.5 px-2 rounded-xl text-[11px] font-medium transition cursor-pointer select-none flex items-center justify-center gap-1.5 active:scale-95 ${
                  isSelected
                    ? "bg-sky-500/20 text-sky-300 border border-sky-500/50 shadow-[0_0_12px_rgba(56,189,248,0.15)] font-semibold"
                    : "bg-zinc-950/70 hover:bg-zinc-800/80 text-zinc-400 hover:text-zinc-200 border border-zinc-800"
                }">
                ${channelIcons[c.type] || ""}
                <span>${escapeHtml(c.label)}</span>
              </button>
            `;
          })
          .join("")}
      </div>

      <!-- 二维码专属卡片 (带 10 分钟过期遮罩防护) -->
      <div class="relative flex flex-col items-center bg-zinc-950/90 border border-zinc-800/80 rounded-2xl p-4 shadow-xl">
        <div class="relative bg-white p-3 rounded-2xl shadow-md">
          <img src="${qrImgUrl}" alt="AgentDeck Pair QR" class="w-44 h-44 block select-none" loading="eager" />
          
          ${
            state.qrExpired
              ? `
                <div class="absolute inset-0 bg-zinc-950/90 backdrop-blur-xs rounded-2xl flex flex-col items-center justify-center p-3 text-center space-y-2.5 animate-fadeIn">
                  <div class="w-8 h-8 rounded-full bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                  </div>
                  <div class="text-[11px] text-zinc-300 font-medium">二维码已过期 (10分钟时限)</div>
                  <button onclick="window.appActions.refreshPairCodeAndQr()"
                    class="px-3 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs shadow-md transition active:scale-95 cursor-pointer flex items-center gap-1.5">
                    <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                    <span>点击立即刷新</span>
                  </button>
                </div>
              `
              : ""
          }
        </div>

        <!-- 单行不换行主直连地址 -->
        <div class="w-full mt-3 px-2.5 py-1 rounded-xl bg-zinc-900 border border-zinc-800 flex items-center justify-between gap-1.5">
          <div class="text-[11px] font-mono text-sky-400 whitespace-nowrap overflow-hidden select-all flex-1 py-0.5 tracking-tight" title="${escapeHtml(activeUrl)}">
            ${escapeHtml(activeUrl)}
          </div>
          <button onclick="window.appActions.copyQrUrl('${escapeJsSingleQuote(activeUrl)}')"
            class="text-[11px] text-zinc-300 hover:text-white px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 shrink-0 transition active:scale-95 cursor-pointer font-medium"
            title="复制直连链接">复制</button>
        </div>

        <!-- 精炼通道场景提示 -->
        <div class="w-full mt-2.5 pt-2.5 border-t border-zinc-800/80 text-[11px] text-center text-zinc-400 flex items-center justify-center gap-1.5">
          <span class="w-1.5 h-1.5 rounded-full ${
            curChannel.type === "wifi" ? "bg-emerald-400" : curChannel.type === "tailscale" ? "bg-sky-400" : "bg-amber-400"
          }"></span>
          <span>${channelTips[state.selectedQrNet] || "扫码或复制链接在手机中打开"}</span>
        </div>
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

// 移动端向右滑动展开菜单手势支持
function initDrawerSwipeGestures() {
  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let isScrollingVertical = false;
  let isGestureLocked = false;
  let isIgnoredTarget = false;

  const isModalOpen = () => {
    const modalIds = [
      "new-session-modal",
      "qr-modal",
      "git-status-modal",
      "token-usage-modal",
      "rename-modal",
    ];
    for (const id of modalIds) {
      const el = document.getElementById(id);
      if (el && !el.classList.contains("hidden")) return true;
    }
    return false;
  };

  const isHorizontalScrollable = (el) => {
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (
        tag === "input" ||
        tag === "textarea" ||
        tag === "select" ||
        cur.isContentEditable
      ) {
        return true;
      }
      if (
        cur.classList?.contains("overflow-x-auto") ||
        cur.classList?.contains("unified-diff-box") ||
        cur.classList?.contains("no-scrollbar")
      ) {
        if (cur.scrollWidth > cur.clientWidth + 5) {
          return true;
        }
      }
      const style = window.getComputedStyle(cur);
      if (
        (style.overflowX === "auto" || style.overflowX === "scroll") &&
        cur.scrollWidth > cur.clientWidth + 5
      ) {
        return true;
      }
      cur = cur.parentElement;
    }
    return false;
  };

  window.addEventListener(
    "touchstart",
    (e) => {
      if (window.innerWidth >= 768 || e.touches.length !== 1) {
        isIgnoredTarget = true;
        return;
      }

      if (isModalOpen()) {
        isIgnoredTarget = true;
        return;
      }

      const touch = e.touches[0];
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
      touchStartTime = Date.now();
      isScrollingVertical = false;
      isGestureLocked = false;

      if (isHorizontalScrollable(e.target)) {
        isIgnoredTarget = true;
      } else {
        isIgnoredTarget = false;
      }
    },
    { passive: true }
  );

  window.addEventListener(
    "touchmove",
    (e) => {
      if (isIgnoredTarget || e.touches.length !== 1) return;

      const touch = e.touches[0];
      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;

      if (!isGestureLocked) {
        const absX = Math.abs(deltaX);
        const absY = Math.abs(deltaY);
        if (absX > 8 || absY > 8) {
          if (absY >= absX) {
            isScrollingVertical = true;
          } else {
            isScrollingVertical = false;
          }
          isGestureLocked = true;
        }
      }
    },
    { passive: true }
  );

  window.addEventListener(
    "touchend",
    (e) => {
      if (isIgnoredTarget || isScrollingVertical) return;

      const touch = e.changedTouches[0];
      if (!touch) return;

      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;
      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);
      const deltaTime = Date.now() - touchStartTime;

      if (absX <= absY * 1.2) return;

      const isQuickSwipe = deltaTime < 350 && absX >= 40;
      const isLongSwipe = absX >= 75;

      if (!isQuickSwipe && !isLongSwipe) return;

      if (!state.drawerOpen && deltaX > 0) {
        window.appActions.toggleDrawer(true);
      } else if (state.drawerOpen && deltaX < 0) {
        window.appActions.toggleDrawer(false);
      }
    },
    { passive: true }
  );
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
  const newModeSelect = document.getElementById("new-session-mode");
  renderModeOptionsForAgent(newModeSelect, agType, "ask", false);

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
  if (res.unified_id) {
    if (initialPrompt) {
      if (!state.optimisticMessages[res.unified_id]) {
        state.optimisticMessages[res.unified_id] = [];
      }
      state.optimisticMessages[res.unified_id].push({
        item_id: `optimistic_${Date.now()}`,
        step_index: 1,
        role: "user",
        created_at: new Date().toISOString(),
        content: initialPrompt,
        thinking: "",
        tool_calls: [],
        status: "RUNNING",
      });
      state.pendingTurnThinking[res.unified_id] = Date.now();
    }
    await loadSessions();
    await selectSession(res.unified_id);
  } else {
    await loadSessions();
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
      : "bg-zinc-900/95 border-sky-500/50 text-sky-200";
  el.className = `px-3 py-2 rounded-xl border shadow-xl text-xs font-medium transition-all ${colorCls}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 3000);
}

// --- 暴露给模板的事件动作集 ---
window.appActions = {
  closePairingGuideModal() {
    closePairingGuideModal();
  },
  async submitPairCodeManual(code) {
    code = (code || "").trim();
    if (!code) {
      showToast("请输入有效配对码", "error");
      return;
    }
    const ua = navigator.userAgent || "Mobile";
    const devName = /iPhone|iPad/i.test(ua) ? "iPhone" : /Android/i.test(ua) ? "Android" : "Web Client";
    try {
      const res = await apiFetch("/api/v1/auth/exchange", {
        method: "POST",
        body: JSON.stringify({ pair_code: code, device_name: devName }),
      });
      if (res.token) {
        state.token = res.token;
        localStorage.setItem("agentdeck_token", res.token);
        showToast("设备配对成功！", "success");
        closePairingGuideModal();
        const pwaUrl = new URL(window.location.href);
        pwaUrl.searchParams.delete("pair_code");
        pwaUrl.searchParams.set("token", res.token);
        window.history.replaceState({}, document.title, pwaUrl.pathname + pwaUrl.search + pwaUrl.hash);
        await Promise.all([loadSystemStatus(), loadWorkspaces(), loadSessions()]);
        connectWebSocket();
      }
    } catch (e) {
      showToast("配对失败: " + (e.message || "配对码无效或已过期"), "error");
    }
  },
  switchQrChannel(type) {
    state.selectedQrNet = type;
    renderQrModalContent();
  },
  switchQrNetMode(mode) {
    state.qrNetMode = mode;
    const modes = ["lan", "zerotier", "tailscale"];
    let switched = false;
    for (const m of modes) {
      const panel = document.getElementById(`qr-panel-${m}`);
      const tab = document.getElementById(`qr-tab-${m}`);
      const isActive = m === mode;
      if (panel) {
        panel.classList.toggle("hidden", !isActive);
        panel.classList.toggle("block", isActive);
        switched = true;
      }
      if (tab) {
        if (isActive) {
          tab.className = "flex-1 py-1.5 px-2 rounded-lg font-medium transition cursor-pointer select-none active:scale-95 bg-sky-500/20 text-sky-300 border border-sky-500/40 shadow-xs";
        } else {
          tab.className = "flex-1 py-1.5 px-2 rounded-lg font-medium transition cursor-pointer select-none active:scale-95 text-zinc-400 hover:text-zinc-200";
        }
      }
    }
    if (!switched) {
      renderQrModalContent();
    }
  },
  handleStatusBtnClick,
  selectSession,
  jumpToRunningSession() {
    const otherRunning = (state.allSessions || []).filter(
      (s) =>
        s.unified_id !== state.selectedSessionId &&
        (isSessionStatusActive(s) || s.status === "waiting_approval")
    );
    if (otherRunning.length > 0) {
      const target = getLatestSession(otherRunning);
      if (target) {
        selectSession(target.unified_id);
      }
    }
  },
  togglePin: handleTogglePin,
  toggleProject(projectKey) {
    state.expandedProjects[projectKey] = !state.expandedProjects[projectKey];
    renderSessionList();
  },
  toggleSection(sectionKey) {
    if (!state.collapsedSections) state.collapsedSections = {};
    state.collapsedSections[sectionKey] = !state.collapsedSections[sectionKey];
    try {
      localStorage.setItem("agentdeck_sections_collapsed", JSON.stringify(state.collapsedSections));
    } catch (e) {}
    renderSessionList();
  },
  newProjectSession(projectPath, ev) {
    if (ev) ev.stopPropagation();
    openNewSessionModal("", projectPath);
  },
  async hideProject(projectPath, ev, projectName = "") {
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
  },
  onNewSessionAgentChange(agentType) {
    const modelSelect = document.getElementById("new-session-model");
    renderModelOptionsForAgent(modelSelect, agentType, "");
    const modeSelect = document.getElementById("new-session-mode");
    renderModeOptionsForAgent(modeSelect, agentType, modeSelect ? modeSelect.value : "ask", false);
  },
  setFilter(f) {
    state.activeFilter = f;
    document.querySelectorAll("[data-agent-tab]").forEach((btn) => {
      if (btn.getAttribute("data-agent-tab") === f) {
        btn.className =
          "px-2.5 py-1.5 sm:py-1 rounded-lg bg-sky-500 text-zinc-950 font-semibold text-[11px] shrink-0 transition active:scale-95";
      } else {
        btn.className =
          "px-2.5 py-1.5 sm:py-1 rounded-lg bg-zinc-900 text-zinc-300 hover:bg-zinc-800 text-[11px] shrink-0 transition active:scale-95";
      }
    });
    // 0ms 纯前端内存过滤，不发同步阻塞请求、不跳走当前正在查看的对话
    renderSessionList();
    // 后台静默同步最新列表
    loadSessions(true);
  },
  fillPromptAndSend(text) {
    const input = document.getElementById("prompt-input");
    if (input) input.value = text;
    handleSendPrompt(text);
  },
  toggleSessionMenu(ev) {
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
      if (text) text.textContent = isPinned ? "取消置顶" : "置顶此对话";
      menu.classList.remove("hidden");
    } else {
      menu.classList.add("hidden");
    }
  },
  async toggleCurrentPin() {
    const menu = document.getElementById("session-action-menu");
    if (menu) menu.classList.add("hidden");
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (!curSess) return;
    await handleTogglePin(curSess.unified_id, !!curSess.is_pinned);
  },
  async renameCurrentSession() {
    const menu = document.getElementById("session-action-menu");
    if (menu) menu.classList.add("hidden");
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (!curSess) return;
    const newTitle = window.prompt("重命名当前对话：", curSess.title);
    if (!newTitle || !newTitle.trim() || newTitle.trim() === curSess.title) return;
    try {
      await apiFetch(`/api/v1/sessions/${encodeURIComponent(curSess.unified_id)}/meta`, {
        method: "PATCH",
        body: JSON.stringify({ title: newTitle.trim() }),
      });
      curSess.title = newTitle.trim();
      renderCurrentSessionHeader();
      renderSessionList();
      showToast("会话已重命名", "success");
    } catch (err) {
      showToast(`重命名失败: ${err.message}`, "error");
    }
  },
  async deleteCurrentSession() {
    const menu = document.getElementById("session-action-menu");
    if (menu) menu.classList.add("hidden");
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (!curSess) return;
    if (!window.confirm(`确定删除会话「${curSess.title}」？此操作不可逆。`)) return;
    try {
      await apiFetch(`/api/v1/sessions/${encodeURIComponent(curSess.unified_id)}`, {
        method: "DELETE",
      });
      showToast("会话已删除", "success");
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
    // 静默在后台拉取最新配对状态，零阻塞 UI 弹出
    loadSystemStatus().then(() => {
      renderQrModalContent();
    }).catch((e) => {
      console.warn("[AgentDeck] 后台更新配对状态提示:", e);
    });
  },
  closeQrModal() {
    state.pairedSuccessNotice = null;
    if (qrExpireTimer) {
      clearTimeout(qrExpireTimer);
      qrExpireTimer = null;
    }
    const m = document.getElementById("qr-modal");
    if (m) m.classList.add("hidden");
  },
  async copyQrUrl(url) {
    if (!url) return;
    const ok = await copyTextToClipboard(url);
    if (ok) {
      showToast("直连地址已复制", "success");
    } else {
      showToast("复制失败，请手动选择复制", "error");
    }
  },
  async refreshPairCodeAndQr() {
    try {
      await apiFetch("/api/v1/auth/refresh_pair_code", { method: "POST" });
      await loadSystemStatus();
      startQrExpireCountdown();
      showToast("配对码已刷新", "success");
    } catch (e) {
      showToast("刷新失败: " + e.message, "error");
    }
  },
  async toggleAwakeLockFromSidebar() {
    const nextState = !state.systemStatus?.awake_lock_active;
    try {
      await apiFetch(`/api/v1/system/awake-lock?enable=${nextState}`, { method: "POST" });
      await loadSystemStatus();
      showToast(nextState ? "PC 防休眠锁已开启" : "PC 防休眠锁已关闭", "info");
    } catch (e) {
      showToast("切换防休眠失败: " + e.message, "error");
    }
  },
  openAgentsHealthModal() {
    renderAgentsHealthModal();
    const m = document.getElementById("agents-health-modal");
    if (m) m.classList.remove("hidden");
  },
  closeAgentsHealthModal() {
    const m = document.getElementById("agents-health-modal");
    if (m) m.classList.add("hidden");
  },
  openNewSessionModal: (prefill = "") => openNewSessionModal(prefill, ""),
  closeNewSessionModal() {
    document.getElementById("new-session-modal").classList.add("hidden");
  },
  confirmCreateNewSession,
  handlePromptAction,
  sendPrompt: () => handleSendPrompt(null),
  abortSession: handleAbortCurrentSession,
  changeMode: (el) => handleModeChange(el.value),
  changeModel: (el) => handleModelChange(el.value),
  submitApproval: submitApprovalDecision,
  startHold: startHoldToApprove,
  cancelHold: cancelHoldToApprove,
  toggleWordWrap() {
    state.wordWrapDiff = !state.wordWrapDiff;
    state.autoScrollLocked = true;

    // 1. 实时就地同步页面及模态框中的所有 .unified-diff-box，实现全屏零卡顿即时换行
    const boxes = document.querySelectorAll(".unified-diff-box");
    boxes.forEach((box) => {
      if (state.wordWrapDiff) {
        box.classList.add("word-wrap");
      } else {
        box.classList.remove("word-wrap");
      }
    });

    // 2. 实时更新所有换行切换按钮文本
    const toggleBtns = document.querySelectorAll("[data-action='toggle-word-wrap']");
    toggleBtns.forEach((btn) => {
      btn.textContent = state.wordWrapDiff ? "横向滚动" : "自动换行";
    });

    // 3. 模态框未打开时同步重绘消息列表与审批流
    const modal = document.getElementById("doc-preview-modal");
    const isModalOpen = modal && !modal.classList.contains("hidden");
    if (!isModalOpen) {
      const container = document.getElementById("messages-container");
      const prevTop = container ? container.scrollTop : 0;
      renderMessagesList();
      renderApprovalBanners();
      if (container) container.scrollTop = prevTop;
    }
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
    const ok = await copyTextToClipboard(raw);
    if (ok) {
      if (btnEl) {
        applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
      } else {
        showToast("已复制代码", "success");
      }
    } else {
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
    document.body.style.overflow = "";
  },
  openDiffFullscreen(diffText, filePath = "", options = {}) {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    state.currentDocRawContent = diffText || "";
    const bName = filePath ? getBasename(filePath) : "代码变更 Diff";
    if (titleEl) titleEl.textContent = `变更 Diff · ${bName}`;
    if (pathEl) pathEl.textContent = filePath || "";

    bodyEl.innerHTML = renderUnifiedDiffHtml(diffText, filePath, {
      isModal: true,
      diffId: options.diffId,
      addCount: options.addCount,
      delCount: options.delCount,
    });
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  },
  openDiffFullscreenById(diffId, e) {
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    const entry =
      (state.lazyDetailsStore && state.lazyDetailsStore[diffId]) ||
      (state.diffStore && state.diffStore[diffId]);
    if (!entry) return;
    const diffText = entry.raw || entry.diffText || "";
    const filePath = entry.filePath || "";
    window.appActions.openDiffFullscreen(diffText, filePath, {
      diffId,
      addCount: entry.addCount,
      delCount: entry.delCount,
    });
  },
  async copyCurrentDocContent(btnEl) {
    if (!state.currentDocRawContent) {
      showToast("无可复制内容", "info");
      return;
    }
    const targetBtn = btnEl || document.getElementById("doc-preview-copy-btn");
    const ok = await copyTextToClipboard(state.currentDocRawContent);
    if (ok) {
      if (targetBtn) {
        applyButtonCheckmark(targetBtn, { highlightBorder: true });
      }
      showToast("已复制全文", "success");
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
      if (btnEl) {
        applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
      }
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
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
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
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
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
          <div class="flex items-center justify-between pb-2 border-b border-zinc-800">
            <span class="text-xs text-zinc-400">全局未提交修改 Diff ${
              res.is_truncated ? "（已截断展示前 48KB）" : ""
            }</span>
            <button onclick="window.appActions.openGitStatusModal()" class="text-xs text-sky-400 hover:underline">返回文件列表</button>
          </div>
          ${renderUnifiedDiffHtml(displayDiff || "无未提交代码改动")}
          ${
            isHuge && !state.expandHugeFullDiff
              ? `<div class="p-3 text-center bg-zinc-900/60 rounded-xl border border-zinc-800 text-xs text-zinc-400">
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
  openTokenUsageModal() {
    const modal = document.getElementById("token-usage-modal");
    if (!modal) return;
    const usage = state.currentTokenUsage || {};
    const ctx = usage.context_tokens || 0;
    const limit = usage.context_limit || 200000;
    const pct = typeof usage.context_percent === "number" ? usage.context_percent : Math.round((ctx / limit) * 1000) / 10;

    const pctEl = document.getElementById("modal-token-pct");
    const barEl = document.getElementById("modal-token-bar");
    const ctxEl = document.getElementById("modal-token-context");
    const limitEl = document.getElementById("modal-token-limit");
    const inEl = document.getElementById("modal-token-input");
    const outEl = document.getElementById("modal-token-output");
    const totEl = document.getElementById("modal-token-total");
    const cacheEl = document.getElementById("modal-token-cache");
    const tipEl = document.getElementById("modal-token-tip");

    if (pctEl) pctEl.textContent = `${pct}%`;
    if (barEl) {
      barEl.style.width = `${Math.min(pct, 100)}%`;
      barEl.className = pct >= 85 ? "h-full bg-rose-500 rounded-full" : pct >= 60 ? "h-full bg-amber-500 rounded-full" : "h-full bg-sky-500 rounded-full";
    }
    if (ctxEl) ctxEl.textContent = `${ctx.toLocaleString()} tokens`;
    if (limitEl) limitEl.textContent = `上限 ${limit.toLocaleString()} (${formatTokenCount(limit)})`;
    if (inEl) inEl.textContent = `${ctx.toLocaleString()}`;
    if (outEl) outEl.textContent = `${(usage.last_output_tokens || 0).toLocaleString()}`;
    if (totEl) totEl.textContent = `${(usage.total_tokens || 0).toLocaleString()}`;
    if (cacheEl) cacheEl.textContent = `${formatTokenCount(usage.cache_read_tokens || 0)}`;

    if (tipEl) {
      if (pct < 60) {
        tipEl.innerHTML = `<span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1"></span> <span class="text-zinc-200 font-medium">上下文健康</span>：模型注意力聚焦，多轮长代码与工具调用表现最佳。`;
      } else if (pct < 85) {
        tipEl.innerHTML = `<span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 mr-1"></span> <span class="text-amber-300 font-medium">上下文适中</span>：已产生较多上下文信息。若要开展全新大任务，可适时考虑开启新会话或输入 <span class="font-mono text-sky-400">/compact</span> 压缩。`;
      } else {
        tipEl.innerHTML = `<span class="inline-block w-1.5 h-1.5 rounded-full bg-rose-400 mr-1"></span> <span class="text-rose-300 font-medium">临近窗口上限</span>：剩余容量不足 15%！建议输入 <span class="font-mono text-sky-400">/compact</span> 总结上下文释放空间，或新建对话继续。`;
      }
    }

    modal.classList.remove("hidden");
  },
  closeTokenUsageModal() {
    const modal = document.getElementById("token-usage-modal");
    if (modal) modal.classList.add("hidden");
  },
  toggleModelPicker(e, forceOpen) {
    if (e) {
      if (typeof e.stopPropagation === "function") e.stopPropagation();
      if (typeof e.preventDefault === "function") e.preventDefault();
    }
    const popover = document.getElementById("model-picker-popover");
    const btn = document.getElementById("model-picker-btn");
    const arrow = document.getElementById("model-picker-arrow");
    if (!popover || !btn) return;
    const isHidden = popover.classList.contains("hidden");
    const open = forceOpen !== undefined ? Boolean(forceOpen) : isHidden;
    if (open) {
      renderModelPicker();
      const rect = btn.getBoundingClientRect();
      const popoverWidth = Math.min(340, window.innerWidth - 16);
      popover.style.width = `${popoverWidth}px`;
      popover.style.backgroundColor = "#09090b";
      // fixed 定位向上弹出：bottom 距离视口底部
      popover.style.bottom = `${Math.max(10, window.innerHeight - rect.top + 6)}px`;
      // 左侧对齐，并保持在屏幕左右视口安全边距内
      const maxLeft = window.innerWidth - popoverWidth - 8;
      const left = Math.max(8, Math.min(rect.left, maxLeft));
      popover.style.left = `${left}px`;

      popover.classList.remove("hidden");
      btn.classList.add("border-sky-500/50", "bg-zinc-800");
      if (arrow) arrow.classList.add("rotate-180");
    } else {
      popover.classList.add("hidden");
      btn.classList.remove("border-sky-500/50", "bg-zinc-800");
      if (arrow) arrow.classList.remove("rotate-180");
    }
  },
  async selectModel(modelId, e) {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
    const popover = document.getElementById("model-picker-popover");
    const btn = document.getElementById("model-picker-btn");
    const arrow = document.getElementById("model-picker-arrow");
    if (popover) popover.classList.add("hidden");
    if (btn) btn.classList.remove("border-sky-500/50", "bg-zinc-800");
    if (arrow) arrow.classList.remove("rotate-180");

    const hiddenInput = document.getElementById("model-select");
    if (hiddenInput) hiddenInput.value = modelId;

    const sess =
      state.sessions.find((s) => s.unified_id === state.selectedSessionId) ||
      state.allSessions.find((s) => s.unified_id === state.selectedSessionId);
    if (sess) {
      sess.model_name = modelId;
    }
    renderModelPicker();

    if (!sess) return;
    try {
      await apiFetch(`/api/v1/sessions/${encodeURIComponent(sess.unified_id)}/meta`, {
        method: "PATCH",
        body: JSON.stringify({ model_name: modelId }),
      });
      showToast(`已切换模型为: ${modelId}`, "success");
    } catch (_) {
      showToast("切换模型失败", "error");
    }
  },
};

function formatTokenCount(n) {
  const num = Number(n) || 0;
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  }
  return String(num);
}

function renderTokenUsage() {
  const pill = document.getElementById("token-usage-pill");
  if (!pill) return;
  const usage = state.currentTokenUsage;
  if (!usage || (!usage.context_tokens && !usage.total_tokens)) {
    pill.classList.add("hidden");
    pill.classList.remove("flex");
    return;
  }

  pill.classList.remove("hidden");
  pill.classList.add("flex");

  const ctx = usage.context_tokens || 0;
  const limit = usage.context_limit || 200000;
  const pct = typeof usage.context_percent === "number" ? usage.context_percent : Math.round((ctx / limit) * 1000) / 10;

  const txtEl = document.getElementById("token-usage-text");
  const pctEl = document.getElementById("token-usage-pct");
  const dotEl = document.getElementById("token-usage-dot");

  if (txtEl) txtEl.textContent = `${formatTokenCount(ctx)} / ${formatTokenCount(limit)}`;
  if (pctEl) {
    pctEl.textContent = `${pct}%`;
    if (pct < 60) {
      pctEl.className = "text-[9px] px-1 py-0.2 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-sky-400";
    } else if (pct < 85) {
      pctEl.className = "text-[9px] px-1 py-0.2 rounded font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-amber-400";
    } else {
      pctEl.className = "text-[9px] px-1 py-0.2 rounded font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/40 animate-pulse";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-rose-500 animate-ping";
    }
  }
}

function highlightSlashItem(idx) {
  state.slashHighlightIndex = idx;
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const items = popover.querySelectorAll("[data-slash-idx]");
  items.forEach((el) => {
    const i = parseInt(el.getAttribute("data-slash-idx"), 10);
    if (i === idx) {
      el.classList.add("bg-zinc-800", "border-zinc-700/80");
      el.classList.remove("hover:bg-zinc-900", "border-transparent");
      el.scrollIntoView({ block: "nearest" });
    } else {
      el.classList.remove("bg-zinc-800", "border-zinc-700/80");
      el.classList.add("hover:bg-zinc-900", "border-transparent");
    }
  });
}
window.highlightSlashItem = highlightSlashItem;

function renderSlashMenu(filterQuery = "") {
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const list = state.workflows || [];
  const q = filterQuery.trim().replace(/^\//, "").toLowerCase();

  const filtered = list.filter((item) => {
    if (!q) return true;
    const cmdClean = item.command.replace(/^\//, "").toLowerCase();
    const lblClean = (item.label || "").toLowerCase();
    return cmdClean.includes(q) || lblClean.includes(q);
  });

  state.filteredSlashCommands = filtered;
  if (state.slashHighlightIndex >= filtered.length) {
    state.slashHighlightIndex = 0;
  }

  if (!state.slashMenuOpen && !filterQuery.startsWith("/")) {
    popover.classList.add("hidden");
    return;
  }

  if (filtered.length === 0) {
    popover.innerHTML = `
      <div class="px-3 py-2.5 text-xs text-zinc-500 flex items-center justify-between">
        <span>无匹配技能或指令</span>
        <span class="text-[10px] text-zinc-600 font-mono">共 ${list.length} 个全部可用</span>
      </div>`;
    popover.classList.remove("hidden");
    return;
  }

  popover.innerHTML = `
    <div class="px-2.5 py-1 text-[11px] text-zinc-400 font-medium border-b border-zinc-800/80 flex items-center justify-between sticky top-0 bg-zinc-950/95 backdrop-blur z-10 select-none">
      <span class="flex items-center gap-1.5 font-medium text-zinc-300">
        <span class="text-sky-400">⚡</span>
        <span>可用技能与指令 (Skills & Commands)</span>
      </span>
      <span class="text-[10px] text-zinc-500 font-mono">${filtered.length} / ${list.length}</span>
    </div>
    <div class="p-1 space-y-0.5">
      ${filtered
        .map((item, idx) => {
          const isQuick = item.category === "quick";
          const isSkill = item.category === "skill" || item.category === "builtin" || item.category === "project";
          
          let badgeCls = "bg-sky-500/15 text-sky-300 border-sky-500/30";
          let badgeText = "指令";
          if (isQuick) {
            badgeCls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            badgeText = "快捷";
          } else if (item.category === "builtin") {
            badgeCls = "bg-cyan-500/15 text-cyan-300 border-cyan-500/30";
            badgeText = "内置";
          } else if (item.category === "project") {
            badgeCls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            badgeText = "项目";
          } else if (isSkill) {
            badgeCls = "bg-purple-500/15 text-purple-300 border-purple-500/30";
            badgeText = "技能";
          }

          const isHigh = idx === state.slashHighlightIndex;
          return `
            <div id="slash-item-${idx}"
              data-slash-idx="${idx}"
              onclick="window.appActions.selectSlashCommand('${escapeJsSingleQuote(item.command)}')"
              onmouseenter="window.highlightSlashItem(${idx})"
              class="cursor-pointer px-2 py-1.5 rounded-lg flex items-center justify-between gap-2 text-xs transition border ${
                isHigh ? "bg-zinc-800 border-zinc-700/80" : "hover:bg-zinc-900 border-transparent"
              }">
              <div class="flex items-center gap-2 min-w-0">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold shrink-0 border ${badgeCls}">${escapeHtml(
            item.command
          )}</span>
                <span class="text-zinc-200 truncate text-[11px] sm:text-xs">${escapeHtml(item.label)}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <span class="text-[9px] text-zinc-500 font-medium px-1 py-0.5 rounded bg-zinc-900 border border-zinc-800">${badgeText}</span>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
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
          : "bg-zinc-800 text-zinc-400 border border-zinc-700"
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
    bodyEl.innerHTML = `<div class="p-6 text-center text-zinc-500 text-xs">当前工作区非 Git 仓库</div>`;
    return;
  }

  const files = data.files || [];

  const listHtml =
    files.length === 0
      ? `<div class="p-6 text-center text-emerald-400 text-xs">✓ 工作区很干净，无未提交修改</div>`
      : files
          .map((f) => {
            const isExp = !!state.gitExpandedDiffs[f.path];
            const badgeCls = GIT_STATUS_BADGES[f.status] || "bg-zinc-800 text-zinc-300 border-zinc-700";
            return `
              <div class="rounded-xl bg-zinc-900/70 border border-zinc-800 p-2.5 text-xs space-y-1.5">
                <div onclick="window.appActions.toggleGitFileDiff('${escapeJsSingleQuote(
                  f.path
                )}')"
                  class="cursor-pointer flex items-center justify-between gap-2 select-none hover:text-sky-300 transition">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="w-5 h-5 rounded flex items-center justify-center font-bold font-mono text-[10px] border ${badgeCls}">
                      ${escapeHtml(f.status)}
                    </span>
                    <span class="font-mono text-xs text-zinc-200 truncate">${escapeHtml(
                      f.path
                    )}</span>
                  </div>
                  <div class="flex items-center gap-1.5 shrink-0 font-mono text-[10px]">
                    ${f.additions > 0 ? `<span class="text-emerald-400">+${f.additions}</span>` : ""}
                    ${f.deletions > 0 ? `<span class="text-rose-400">-${f.deletions}</span>` : ""}
                    <span class="text-zinc-500">${isExp ? "▲" : "▼"}</span>
                  </div>
                </div>
                <div id="${gitPathToSlotId(f.path)}">
                  ${
                    isExp && state.gitDiffCache[f.path]
                      ? renderUnifiedDiffHtml(state.gitDiffCache[f.path], f.path)
                      : ""
                  }
                </div>
              </div>
            `;
          })
          .join("");

  bodyEl.innerHTML = `
    <div class="space-y-3 pb-6">
      <div class="flex items-center justify-between p-3 rounded-xl bg-zinc-950 border border-zinc-800 text-xs">
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded-lg bg-sky-500/15 text-sky-300 border border-sky-500/30 font-mono font-semibold">
            🌿 ${escapeHtml(data.branch || "HEAD")}
          </span>
          <span class="text-zinc-400">${data.changed_count} 个未提交变更文件</span>
        </div>
        ${
          files.length > 0
            ? `<button onclick="window.appActions.viewFullGitDiff()"
                class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-sky-300 text-xs transition">
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
  // 读取服务端注入的前端基准版本指纹
  const metaBuild = document.querySelector('meta[name="agentdeck-build"]')?.content;
  if (metaBuild) state.frontendBuild = metaBuild;
  renderSidebarStatus();

  updateDrawerVisibility();
  initDrawerSwipeGestures();
  await checkAndExchangePairCode();
  try {
    await Promise.all([loadSystemStatus(), loadWorkspaces(), loadSessions()]);
  } catch (err) {
    console.warn("冷启动连接当前通道失败，尝试漫游探测可用通道...", err);
    triggerAutoRoaming("initial_boot_fail");
  }
  connectWebSocket();
  checkFrontendVersion();

  // 手机锁屏唤醒或切回主屏幕应用时立即检测一次版本与通道连通性
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      checkFrontendVersion();
      const isStale = Date.now() - lastPongReceived > 20000;
      if (!state.wsConnected || isStale || !wsInstance || wsInstance.readyState !== WebSocket.OPEN) {
        console.info("[AgentDeck] 移动端切回前台发现连接断开或心跳逾期，立即加速重连与漫游...");
        connectWebSocket();
        triggerAutoRoaming("visibility_resume");
      }
    }
  });

  // 手机网络环境变更监听（Wi-Fi 断开/连接、蜂窝网切换等）
  window.addEventListener("online", () => {
    triggerAutoRoaming("network_online");
  });
  // 保持周期性轻量巡检
  setInterval(checkFrontendVersion, 25000);

  window.addEventListener("hashchange", () => {
    const hashId = getSessionIdFromHash();
    if (hashId && hashId !== state.selectedSessionId) {
      if (state.allSessions.some((s) => s.unified_id === hashId)) {
        selectSession(hashId);
      }
    }
  });

  setInterval(() => {
    if (state.selectedSessionId) {
      if (isSessionWorking(state.selectedSessionId)) {
        renderLiveStreamOverlay();
      }
    }
  }, 1000);

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
      const popover = document.getElementById("slash-popover");
      const isPopoverVisible = state.slashMenuOpen && popover && !popover.classList.contains("hidden");
      const filtered = state.filteredSlashCommands || [];

      // 当弹出菜单展示时支持键盘 ↑ ↓ 导航与 Enter / Tab 快速补全选中
      if (isPopoverVisible && filtered.length > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          const nextIdx = (state.slashHighlightIndex + 1) % filtered.length;
          highlightSlashItem(nextIdx);
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          const prevIdx = (state.slashHighlightIndex - 1 + filtered.length) % filtered.length;
          highlightSlashItem(prevIdx);
          return;
        }
        if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey) {
          e.preventDefault();
          const chosen = filtered[state.slashHighlightIndex] || filtered[0];
          if (chosen) {
            window.appActions.selectSlashCommand(chosen.command);
          }
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          state.slashMenuOpen = false;
          popover.classList.add("hidden");
          return;
        }
      }

      if (e.key === "Enter" && !e.shiftKey && window.innerWidth >= 768) {
        e.preventDefault();
        const btn = document.getElementById("prompt-action-btn");
        if (btn?.dataset?.action === "abort" && !promptInput.value.trim()) {
          // 当前为中断态且无内容输入时，按 Enter 不误触发中断
          return;
        }
        handleSendPrompt(null);
      }
    });

    promptInput.addEventListener("input", (e) => {
      updatePromptActionBtnState();
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
    const modelPopover = document.getElementById("model-picker-popover");
    const modelBtn = document.getElementById("model-picker-btn");
    const modelArrow = document.getElementById("model-picker-arrow");
    if (modelPopover && !modelPopover.classList.contains("hidden")) {
      if (!modelPopover.contains(e.target) && !modelBtn?.contains(e.target)) {
        modelPopover.classList.add("hidden");
        if (modelBtn) modelBtn.classList.remove("border-sky-500/50", "bg-zinc-800");
        if (modelArrow) modelArrow.classList.remove("rotate-180");
      }
    }

    const popover = document.getElementById("slash-popover");
    const input = document.getElementById("prompt-input");
    if (popover && input && !popover.contains(e.target) && e.target !== input) {
      state.slashMenuOpen = false;
      popover.classList.add("hidden");
    }

    const sessionMenu = document.getElementById("session-action-menu");
    const sessionMenuBtn = document.getElementById("session-options-menu-btn");
    if (sessionMenu && !sessionMenu.classList.contains("hidden")) {
      if (!sessionMenu.contains(e.target) && (!sessionMenuBtn || !sessionMenuBtn.contains(e.target))) {
        sessionMenu.classList.add("hidden");
      }
    }
  });

  // 全局键盘 Escape 键支持一键关闭任何打开的模态框
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const modals = [
        "qr-modal",
        "agents-health-modal",
        "pairing-guide-modal",
        "new-session-modal",
        "git-status-modal",
        "token-usage-modal",
        "doc-preview-modal",
      ];
      for (const mId of modals) {
        const el = document.getElementById(mId);
        if (el && !el.classList.contains("hidden")) {
          el.classList.add("hidden");
          if (mId === "doc-preview-modal") {
            document.body.style.overflow = "";
          }
          if (mId === "qr-modal" && qrExpireTimer) {
            clearTimeout(qrExpireTimer);
            qrExpireTimer = null;
          }
        }
      }
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
      updateCollapseStepsPillVisibility();
    });
  }
});
