import { AGENT_META } from "./config.js";
/**
 * AgentDeck 会话消息增量拉取、分页加载与内存缓存就地合并器
 */

import { state } from "./state.js";
import { computeMessagesSig } from "./utils.js";
import { apiFetch, showToast } from "./api.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderMessagesList,
  renderLiveStreamOverlay,
  renderModelOptionsForAgent,
} from "./render.js";
import { isSessionWorking } from "./session-helper.js";

const inflightMessagesPromises = new Map();

export async function loadSessionMessages(unifiedId, options = false) {
  if (!unifiedId) return;
  const silent = typeof options === "boolean" ? options : Boolean(options?.silent);
  const reqKey = `${unifiedId}_${silent}`;
  if (inflightMessagesPromises.has(reqKey)) {
    return inflightMessagesPromises.get(reqKey);
  }
  const taskPromise = (async () => {
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
    if (data && data.unified_id && data.unified_id !== unifiedId) {
      const realUid = data.unified_id;
      if (state.selectedSessionId === unifiedId) {
        state.selectedSessionId = realUid;
      }
      if (state.optimisticMessages[unifiedId]) {
        state.optimisticMessages[realUid] = state.optimisticMessages[unifiedId];
        delete state.optimisticMessages[unifiedId];
      }
      if (state.pendingTurnThinking[unifiedId]) {
        state.pendingTurnThinking[realUid] = state.pendingTurnThinking[unifiedId];
        delete state.pendingTurnThinking[unifiedId];
      }
      unifiedId = realUid;
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
      // 检查最新消息列表中是否仍有工具处于执行或等待状态，防止在多步长任务的间隙被误杀
      let hasRunningStep = false;
      if (incoming && incoming.length > 0) {
        const lastMsg = incoming[incoming.length - 1];
        if (lastMsg && (lastMsg.status === "RUNNING" || lastMsg.status === "PENDING")) {
          hasRunningStep = true;
        } else if (lastMsg && Array.isArray(lastMsg.tool_calls)) {
          hasRunningStep = lastMsg.tool_calls.some(
            (tc) => tc && (tc.status === "RUNNING" || tc.status === "PENDING")
          );
        }
      }
      if (!hasRunningStep) {
        if (optList.length === 0) {
          delete state.pendingTurnThinking[unifiedId];
          delete state.streamingBuffer[unifiedId];
        }

        // 单向降级链平滑流转：运行中任务结束后，后台会话降级为「已完成·未读」，当前查看会话降级为「平稳暂留」
        if (unifiedId === state.selectedSessionId) {
          if (state.retainedActiveSessionIds) {
            state.retainedActiveSessionIds.add(unifiedId);
          }
        } else {
          if (state.unreadCompletedSessionIds) {
            state.unreadCompletedSessionIds.add(unifiedId);
          }
        }

        let statusChanged = false;
        const targetSessions = [
          ...(state.allSessions || []),
          ...(state.sessions || []),
        ];
        for (const s of targetSessions) {
          if (s && s.unified_id === unifiedId && (s.status === "running" || s.status === "desktop_active")) {
            s.status = "idle";
            statusChanged = true;
          }
        }
        if (statusChanged) {
          renderSessionList();
        }
        if (unifiedId === state.selectedSessionId) {
          renderLiveStreamOverlay();
        }
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
        if (!found && (!incoming.length || data.is_running)) {
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

      // 检测当前会话最后一条消息是否为额度耗尽警告，进行即时轻提醒（支持四大 Agent）
      const lastMsg = mergedMessages[mergedMessages.length - 1];
      const curSess = (state.allSessions || state.sessions || []).find((s) => s.unified_id === unifiedId);
      const agentType = curSess?.agent_type || (unifiedId || "").split(":")[0];
      const agentLabel = AGENT_META[agentType]?.label || "Agent";

      const isQuotaAlert =
        lastMsg &&
        typeof lastMsg.content === "string" &&
        (lastMsg.content.includes("模型使用额度已耗尽") ||
          lastMsg.content.includes("Quota Exceeded") ||
          lastMsg.content.includes("Free usage limit reached"));

      if (isQuotaAlert) {
        if (!window._notifiedQuotaSessionIds) window._notifiedQuotaSessionIds = new Set();
        if (!window._notifiedQuotaSessionIds.has(unifiedId)) {
          window._notifiedQuotaSessionIds.add(unifiedId);
          showToast(`${agentLabel} 模型额度已耗尽，会话已中止`, "error");
        }
      }
    }
  } catch (e) {
    if (state.selectedSessionId === unifiedId) {
      state.loadingMessages = false;
      renderMessagesList();
    }
  }
  })();

  inflightMessagesPromises.set(reqKey, taskPromise);
  try {
    return await taskPromise;
  } finally {
    inflightMessagesPromises.delete(reqKey);
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
  // 记录视口当前顶部第一条可见消息卡片作为物理锚点，杜绝内容插入时的跳动
  const anchorEl = container ? container.querySelector("[data-user-msg-id], article") : null;
  const anchorId = anchorEl ? (anchorEl.getAttribute("data-user-msg-id") || anchorEl.getAttribute("data-turn-key") || "") : "";
  const anchorTopBefore = anchorEl ? anchorEl.getBoundingClientRect().top : 0;

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

    // 双重 RAF 物理锚定：确保更早历史消息插入并重排后，用户视线焦点元素在视口中绝对静止
    if (container && anchorEl) {
      const targetAnchor = anchorId ? container.querySelector(`[data-user-msg-id="${anchorId}"], [data-turn-key="${anchorId}"]`) : null;
      const elToTrack = targetAnchor || anchorEl;

      const adjustScroll = () => {
        if (!container || !elToTrack) return;
        const curTop = elToTrack.getBoundingClientRect().top;
        const delta = curTop - anchorTopBefore;
        if (Math.abs(delta) > 0.5) {
          container.scrollTop += delta;
        }
      };

      adjustScroll();
      requestAnimationFrame(() => {
        adjustScroll();
        requestAnimationFrame(adjustScroll);
      });
    }
  } catch (e) {
    state.loadingOlderMessages = false;
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.textContent = "重试加载更早消息";
    }
  }
}
