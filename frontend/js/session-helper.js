/**
 * AgentDeck 会话计算、排序决策、工程路径解析与模型辅助工具
 */

import { MODEL_CONTEXT_LIMITS, AGENT_META } from "./config.js";
import { state, isSessionCompletedUnread } from "./state.js";
import { cleanFriendlyText } from "./utils.js";

export function cleanFriendlyTitle(title) {
  if (!title) return "新对话";
  return cleanFriendlyText(title).slice(0, 50);
}

export function isSessionStatusActive(s) {
  if (!s || !s.status) return false;
  const st = String(s.status).toLowerCase();
  return st === "running" || st === "desktop_active";
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
  if (st === "running" || st === "desktop_active" || (typeof isSessionWorking === "function" && isSessionWorking(s.unified_id))) {
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

export function pickDefaultSession(sessionList) {
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

export function isSessionWorking(unifiedId) {
  if (!unifiedId) return false;
  if (state.pendingTurnThinking && state.pendingTurnThinking[unifiedId]) {
    return true;
  }
  if (state.streamingBuffer && state.streamingBuffer[unifiedId]) {
    return true;
  }
  const optList = state.optimisticMessages ? state.optimisticMessages[unifiedId] : null;
  if (optList && optList.length > 0) {
    return true;
  }
  const sess = state.sessions.find((s) => s.unified_id === unifiedId);
  if (sess && (sess.status === "running" || sess.status === "desktop_active")) {
    return true;
  }
  const allSess = state.allSessions.find((s) => s.unified_id === unifiedId);
  if (allSess && (allSess.status === "running" || allSess.status === "desktop_active")) {
    return true;
  }
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
