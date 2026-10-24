/**
 * AgentDeck 会话计算、排序决策、工程路径解析与模型辅助工具
 */

import { MODEL_CONTEXT_LIMITS, AGENT_META } from "./config.js";
import { state, isSessionCompletedUnread } from "./state.js";
import { cleanFriendlyText, extractSearchSnippet } from "./utils.js";

export function cleanFriendlyTitle(title) {
  if (!title) return "新对话";
  return cleanFriendlyText(title).slice(0, 50);
}

export function isSessionStatusActive(s) {
  if (!s || !s.status) return false;
  const st = String(s.status).toLowerCase();
  return st === "running" || st === "desktop_active" || Boolean(s.has_active_subagent);
}

export const SESSION_PRIORITY = Object.freeze({
  WAITING_APPROVAL: 3,
  COMPLETED_UNREAD: 2,
  ACTIVE: 1,
  RETAINED: 1, // 当前查看会话运行完成后的平稳暂留
  DEFAULT: 0,
});

export function getSessionPriorityWeight(s) {
  if (!s) return SESSION_PRIORITY.DEFAULT;
  const st = String(s.status || "").toLowerCase();
  if (st === "waiting_approval") return SESSION_PRIORITY.WAITING_APPROVAL;
  if (isSessionCompletedUnread(s)) return SESSION_PRIORITY.COMPLETED_UNREAD;
  if (
    st === "running" ||
    st === "desktop_active" ||
    Boolean(s.has_active_subagent) ||
    (typeof isSessionWorking === "function" && isSessionWorking(s.unified_id))
  ) {
    return SESSION_PRIORITY.ACTIVE;
  }
  if (state.retainedActiveSessionIds && state.retainedActiveSessionIds.has(s.unified_id)) {
    return SESSION_PRIORITY.RETAINED;
  }
  return SESSION_PRIORITY.DEFAULT;
}

export function isSessionAttentionNeeded(s) {
  return Boolean(s) && getSessionPriorityWeight(s) > SESSION_PRIORITY.DEFAULT;
}

export function isSubagentSession(s) {
  if (!s) return false;
  if (s.is_subagent) return true;
  if (s.parent_unified_id) return true;
  const title = String(s.title || "").toLowerCase();
  if (title.includes("(subagent)") || title.includes("subagent)")) return true;
  if (title.startsWith("subagent ") || title === "subagent") return true;
  return false;
}

export function getAllAttentionSessions() {
  const all = state.allSessions && state.allSessions.length > 0 ? state.allSessions : state.sessions || [];
  const list = all.filter((s) => isSessionAttentionNeeded(s) && !isSubagentSession(s));

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
  const mainSessions = sessionList.filter((s) => !isSubagentSession(s));
  const pool = mainSessions.length > 0 ? mainSessions : sessionList;

  const runningSessions = pool.filter((s) => isSessionStatusActive(s));
  if (runningSessions.length > 0) {
    return getLatestSession(runningSessions);
  }
  const waitingSessions = pool.filter(
    (s) => String(s.status || "").toLowerCase() === "waiting_approval"
  );
  if (waitingSessions.length > 0) {
    return getLatestSession(waitingSessions);
  }
  const pinnedSessions = pool.filter((s) => Boolean(s.is_pinned));
  if (pinnedSessions.length > 0) {
    return getLatestSession(pinnedSessions);
  }
  return pool[0];
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
  if (sess && (sess.status === "running" || sess.status === "desktop_active" || Boolean(sess.has_active_subagent))) {
    return true;
  }
  const allSess = state.allSessions.find((s) => s.unified_id === unifiedId);
  if (allSess && (allSess.status === "running" || allSess.status === "desktop_active" || Boolean(allSess.has_active_subagent))) {
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

/**
 * 检索单个会话是否匹配关键词（包含标题、项目、preview 及所有已在内存中加载的会话内容）
 * 遵循原则：只检索内存中已加载/缓存的内容，不触发后端全量历史请求
 */
export function searchSessionMatch(session, query) {
  if (!query) return { matched: true, type: "all" };
  const q = String(query).trim().toLowerCase();
  if (!q) return { matched: true, type: "all" };
  if (!session) return { matched: false };

  // 1. 标题匹配
  const title = (session.title || "").toLowerCase();
  if (title.includes(q)) {
    return { matched: true, type: "title" };
  }

  // 2. 所属项目名称匹配
  const projectName = (session.project_name || "").toLowerCase();
  if (projectName.includes(q)) {
    return { matched: true, type: "project" };
  }

  // 3. 会话摘要 preview 匹配（包含最新消息内容摘要）
  const preview = session.preview || "";
  const prevLower = preview.toLowerCase();
  const prevIdx = prevLower.indexOf(q);
  if (prevIdx !== -1) {
    return {
      matched: true,
      type: "preview",
      snippet: extractSearchSnippet(preview, q, prevIdx),
    };
  }

  const uid = session.unified_id;

  // 4. 正在流式生成的文本内容检索 (streamingBuffer)
  const streaming = state.streamingBuffer?.[uid];
  if (streaming && typeof streaming === "string") {
    const sIdx = streaming.toLowerCase().indexOf(q);
    if (sIdx !== -1) {
      return {
        matched: true,
        type: "content",
        snippet: extractSearchSnippet(streaming, q, sIdx),
      };
    }
  }

  // 5. 内存已加载的对话消息流检索 (sessionMessagesCache 或当前选中的 state.messages)
  const cachedMessages = state.sessionMessagesCache?.[uid]?.messages;
  const currentMessages = state.selectedSessionId === uid ? state.messages : null;
  const messages = (currentMessages && currentMessages.length > 0) ? currentMessages : (cachedMessages || []);

  if (Array.isArray(messages) && messages.length > 0) {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (!msg) continue;

      // 5.1 对话正文 (用户提问 / 助手回复)
      if (msg.content && typeof msg.content === "string") {
        const cIdx = msg.content.toLowerCase().indexOf(q);
        if (cIdx !== -1) {
          return {
            matched: true,
            type: "content",
            role: msg.role,
            snippet: extractSearchSnippet(msg.content, q, cIdx),
          };
        }
      }

      // 5.2 思考推理过程 (thinking)
      if (msg.thinking && typeof msg.thinking === "string") {
        const tIdx = msg.thinking.toLowerCase().indexOf(q);
        if (tIdx !== -1) {
          return {
            matched: true,
            type: "thinking",
            snippet: extractSearchSnippet(msg.thinking, q, tIdx),
          };
        }
      }

      // 5.3 工具调用 (tool_calls)
      if (Array.isArray(msg.tool_calls)) {
        for (const tc of msg.tool_calls) {
          if (!tc) continue;
          if (tc.args_summary && typeof tc.args_summary === "string") {
            const aIdx = tc.args_summary.toLowerCase().indexOf(q);
            if (aIdx !== -1) {
              return {
                matched: true,
                type: "tool",
                snippet: extractSearchSnippet(tc.args_summary, q, aIdx),
              };
            }
          }
          if (tc.output && typeof tc.output === "string") {
            const oIdx = tc.output.toLowerCase().indexOf(q);
            if (oIdx !== -1) {
              return {
                matched: true,
                type: "tool",
                snippet: extractSearchSnippet(tc.output, q, oIdx),
              };
            }
          }
          if (tc.name && typeof tc.name === "string" && tc.name.toLowerCase().includes(q)) {
            return {
              matched: true,
              type: "tool",
              snippet: `工具: ${tc.name}`,
            };
          }
          if (tc.file_path && typeof tc.file_path === "string" && tc.file_path.toLowerCase().includes(q)) {
            return {
              matched: true,
              type: "tool",
              snippet: `文件: ${tc.file_path}`,
            };
          }
        }
      }

      // 5.4 完整输出缓存 (fullOutputStore)
      const full = state.fullOutputStore?.[`${uid}_${msg.step_index}`];
      if (full && typeof full === "string") {
        const fIdx = full.toLowerCase().indexOf(q);
        if (fIdx !== -1) {
          return {
            matched: true,
            type: "tool",
            snippet: extractSearchSnippet(full, q, fIdx),
          };
        }
      }
    }
  }

  // 6. 乐观未持久化消息 (optimisticMessages)
  const optMsgs = state.optimisticMessages?.[uid];
  if (Array.isArray(optMsgs)) {
    for (const om of optMsgs) {
      if (om && om.content && typeof om.content === "string") {
        const oIdx = om.content.toLowerCase().indexOf(q);
        if (oIdx !== -1) {
          return {
            matched: true,
            type: "content",
            snippet: extractSearchSnippet(om.content, q, oIdx),
          };
        }
      }
    }
  }

  return { matched: false };
}
