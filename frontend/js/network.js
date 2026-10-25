/**
 * AgentDeck WebSocket 实时连接、断线重连、跨网络通道自愈漫游调度器
 */

import { state, markSessionCompletedUnread, saveLastViewedTimes, clearAllUnreadCompletedSessions } from "./state.js";
import { triggerAlertNotification } from "./utils.js";
import { showToast, showPairingGuideModal } from "./api.js";
import {
  loadSessions,
  loadSessionMessages,
  loadSystemStatus,
  pickDefaultSession,
  selectSession,
} from "./data.js";
import {
  renderApprovalBanners,
  renderSessionList,
  renderHeaderStatus,
  renderCurrentSessionHeader,
  renderLiveStreamOverlay,
  renderOtherRunningPill,
  renderQrModalContent,
  renderMessagesList,
} from "./render.js";

import {
  handleCommandAck,
  flushPendingCommands,
  updateQueueBadge,
} from "./command-queue.js";

export let wsInstance = null;
export let wsHeartbeatTimer = null;
export let wsPongTimeoutTimer = null;
export let lastPongReceived = Date.now();
export let lastWsSyncToastTime = 0;
let wsDisconnectStreak = 0;
let silentSessionsTimer = null;
let silentMessagesTimer = null;
let deferredListRenderTimer = null;

import {
  showRoamingBanner,
  hideRoamingBanner,
  probeChannelHealth,
  triggerAutoRoaming as rawTriggerAutoRoaming,
  retryServerConnection,
} from "./network-roaming.js";

export { showRoamingBanner, hideRoamingBanner, probeChannelHealth, retryServerConnection };

export function triggerAutoRoaming(triggerSource = "network") {
  return rawTriggerAutoRoaming(triggerSource, () => {
    wsDisconnectStreak = 0;
    connectWebSocket();
  });
}

export function clearWsHeartbeat() {
  if (wsHeartbeatTimer) {
    clearInterval(wsHeartbeatTimer);
    wsHeartbeatTimer = null;
  }
  if (wsPongTimeoutTimer) {
    clearTimeout(wsPongTimeoutTimer);
    wsPongTimeoutTimer = null;
  }
}

export function startWsHeartbeat() {
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

export function connectWebSocket() {
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
    state.serverOffline = false;
    renderHeaderStatus();
    import("./render-messages.js").then((m) => {
      m.updateFooterVisibility();
      if (!state.selectedSessionId || state.messages.length === 0) {
        m.renderMessagesList();
      }
    }).catch(() => {});
    window._agentDeckActiveWs = wsInstance;
    flushPendingCommands(wsInstance);
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
      if (msg.action === "command_ack" || msg.type === "COMMAND_ACK") {
        handleCommandAck(msg);
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
    updateQueueBadge();
    if (ev && ev.code === 1008) {
      // 鉴权未通过被服务端拒绝，先重置重连状态为未连接，避免界面假死挂起“重连中”
      state.wsReconnecting = false;
      renderHeaderStatus();
      loadSystemStatus().then(() => {
        if (state.token) {
          console.info("[AgentDeck WS] 握手 1008 自愈成功，正在使用新 Token 重连...");
          state.wsReconnecting = true;
          renderHeaderStatus();
          setTimeout(connectWebSocket, 800);
        } else {
          showPairingGuideModal();
        }
      }).catch(() => {
        showPairingGuideModal();
      });
      return;
    }
    state.wsReconnecting = true;
    renderHeaderStatus();
    wsDisconnectStreak++;
    if (wsDisconnectStreak >= 2) {
      triggerAutoRoaming("websocket_disconnect");
    }
    setTimeout(connectWebSocket, 2200);
  };
}

export function handleRealtimeEvent(envelope) {
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

  if (type === "SESSION_VIEWED") {
    const uid = payload && payload.unified_id;
    const vAt = payload && payload.last_viewed_at;
    if (uid) {
      if (vAt) {
        const vTime = new Date(vAt).getTime();
        if (!isNaN(vTime)) {
          state.sessionLastViewedTimes[uid] = Math.max(
            state.sessionLastViewedTimes[uid] || 0,
            vTime
          );
        }
      }
      state.unreadCompletedSessionIds.delete(uid);
      saveLastViewedTimes();
      renderSessionList();
      renderOtherRunningPill();
    }
    return;
  }

  if (type === "ALL_SESSIONS_VIEWED") {
    const vAt = payload && payload.last_viewed_at;
    clearAllUnreadCompletedSessions(vAt);
    renderSessionList();
    renderOtherRunningPill();
    return;
  }

  if (type === "SESSION_DELETED") {
    const deletedIds = [payload?.unified_id, payload?.actual_unified_id].filter(Boolean);
    for (const did of deletedIds) {
      delete state.pendingTurnThinking[did];
      delete state.streamingBuffer[did];
      delete state.optimisticMessages[did];
      if (state.retainedActiveSessionIds) {
        state.retainedActiveSessionIds.delete(did);
      }
      state.sessions = (state.sessions || []).filter((s) => s.unified_id !== did);
      state.allSessions = (state.allSessions || []).filter((s) => s.unified_id !== did);
    }
    if (deletedIds.includes(state.selectedSessionId)) {
      const nextSess = pickDefaultSession(state.sessions);
      state.selectedSessionId = nextSess?.unified_id || null;
      renderSessionList();
      renderCurrentSessionHeader();
      if (state.selectedSessionId) {
        selectSession(state.selectedSessionId);
      } else {
        try {
          history.replaceState(null, "", window.location.pathname + window.location.search);
        } catch (_) {}
        state.messages = [];
        renderMessagesList();
      }
    } else {
      renderSessionList();
    }
    loadSessions(true);
    return;
  }

  if (type === "SESSION_UPDATED" || type === "SESSION_CREATED") {
    if (type === "SESSION_UPDATED" && payload && payload.unified_id) {
      const uid = payload.unified_id;
      const match = (state.allSessions || state.sessions).find((s) => s.unified_id === uid);
      if (match) {
        if (payload.title) match.title = payload.title;
        if (payload.is_pinned !== undefined) match.is_pinned = payload.is_pinned ? 1 : 0;
        if (payload.meta) {
          if (payload.meta.title) match.title = payload.meta.title;
          if (payload.meta.is_pinned !== undefined) match.is_pinned = payload.meta.is_pinned ? 1 : 0;
        }
      }
      renderSessionList();
      if (uid === state.selectedSessionId) {
        renderCurrentSessionHeader();
      }
    }
    loadSessions(true);
    return;
  }

  if (type === "SESSION_REMAPPED") {
    const oldId = payload && payload.old_unified_id;
    const newId = payload && payload.new_unified_id;
    if (oldId && newId) {
      if (state.selectedSessionId === oldId) {
        state.selectedSessionId = newId;
      }
      if (state.optimisticMessages[oldId]) {
        state.optimisticMessages[newId] = state.optimisticMessages[oldId];
        delete state.optimisticMessages[oldId];
      }
      if (state.pendingTurnThinking[oldId]) {
        state.pendingTurnThinking[newId] = state.pendingTurnThinking[oldId];
        delete state.pendingTurnThinking[oldId];
      }
      if (state.streamingBuffer[oldId]) {
        state.streamingBuffer[newId] = state.streamingBuffer[oldId];
        delete state.streamingBuffer[oldId];
      }
      loadSessions(true);
      loadSessionMessages(newId, true);
    }
    return;
  }

  if (type === "SESSION_TURN_FINISHED" || type === "SESSION_ABORTED") {
    const uid = payload && payload.unified_id;
    const origUid = payload && payload.original_unified_id;
    const affected = [uid, origUid].filter(Boolean);
    for (const id of affected) {
      delete state.pendingTurnThinking[id];
      const sess = (state.allSessions || state.sessions).find((s) => s.unified_id === id);
      if (sess && sess.status === "running") {
        sess.status = "idle";
      }
      if (id !== state.selectedSessionId) {
        delete state.streamingBuffer[id];
        delete state.optimisticMessages[id];
      }
    }
    const curUid = state.selectedSessionId;
    if (affected.includes(curUid)) {
      if (state.retainedActiveSessionIds) {
        state.retainedActiveSessionIds.add(curUid);
      }
    } else if (uid) {
      markSessionCompletedUnread(uid);
    }
    renderSessionList();
    renderOtherRunningPill();
    if (affected.includes(state.selectedSessionId)) {
      renderCurrentSessionHeader();
    }
    loadSessions(true);
    if (state.selectedSessionId && (!uid || affected.includes(state.selectedSessionId))) {
      loadSessionMessages(state.selectedSessionId, { silent: false }).finally(() => {
        if (uid) {
          delete state.streamingBuffer[uid];
          if (state.optimisticMessages[uid]) {
            delete state.optimisticMessages[uid];
          }
          renderLiveStreamOverlay();
        }
      });
    } else if (uid) {
      delete state.streamingBuffer[uid];
      if (state.optimisticMessages[uid]) {
        delete state.optimisticMessages[uid];
      }
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

export function extractReadableSnippetFromFrame(frame) {
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

export function scheduleSilentSessionsRefresh(delayMs = 350) {
  if (silentSessionsTimer) clearTimeout(silentSessionsTimer);
  silentSessionsTimer = setTimeout(() => {
    silentSessionsTimer = null;
    loadSessions(true);
  }, delayMs);
}

export function scheduleSilentMessagesRefresh(unifiedId, delayMs = 250) {
  if (!unifiedId) return;
  if (silentMessagesTimer) clearTimeout(silentMessagesTimer);
  silentMessagesTimer = setTimeout(() => {
    silentMessagesTimer = null;
    if (state.selectedSessionId === unifiedId) {
      loadSessionMessages(unifiedId, { silent: true });
    }
  }, delayMs);
}

export function scheduleDeferredSessionListRender(delayMs = 1200) {
  if (deferredListRenderTimer) clearTimeout(deferredListRenderTimer);
  deferredListRenderTimer = setTimeout(() => {
    deferredListRenderTimer = null;
    if (!state.isUserInteractingWithList) {
      renderSessionList();
    }
  }, delayMs);
}
