/**
 * AgentDeck 对话输入、提示词发送、执行模式切换与任务中止控制器
 */

import { state } from "./state.js";
import { apiFetch, showToast } from "./api.js";
import { requestNotificationPermission } from "./utils.js";
import { loadSessions, loadSessionMessages } from "./data.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderMessagesList,
  renderLiveStreamOverlay,
  updatePromptActionBtnState,
} from "./render.js";
import { openNewSessionModal } from "./modals.js";

export async function handleSendPrompt(customPrompt = null) {
  const inputEl = document.getElementById("prompt-input");
  const text = (customPrompt !== null ? customPrompt : (inputEl ? inputEl.value : "")).trim();
  if (!text) return;

  if (!state.selectedSessionId) {
    openNewSessionModal(text);
    return;
  }

  requestNotificationPermission();
  if (customPrompt === null && inputEl) {
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

export async function handleAbortCurrentSession() {
  if (!state.selectedSessionId) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/abort`,
      { method: "POST" }
    );
    showToast("已发送中断信号", "info");
    delete state.pendingTurnThinking[state.selectedSessionId];
    if (state.optimisticMessages[state.selectedSessionId]) {
      state.optimisticMessages[state.selectedSessionId] = [];
    }
    await loadSessions();
    await loadSessionMessages(state.selectedSessionId, true);
  } catch (e) {
    showToast("中断失败", "error");
  }
}

export async function handleModeChange(newMode) {
  if (!state.selectedSessionId) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/mode`,
      {
        method: "POST",
        body: JSON.stringify({ execution_mode: newMode }),
      }
    );
    const s = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (s) s.execution_mode = newMode;
    showToast(`模式已切换: ${newMode}`, "success");
  } catch (e) {
    showToast("模式切换失败", "error");
  }
}

export async function handleModelChange(newModel) {
  if (!state.selectedSessionId) return;
  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/model`,
      {
        method: "POST",
        body: JSON.stringify({ model_name: newModel }),
      }
    );
    const s = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (s) s.model_name = newModel;
    showToast("模型已切换", "success");
  } catch (e) {
    showToast("模型切换失败", "error");
  }
}
