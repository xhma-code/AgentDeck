/**
 * AgentDeck 对话输入、提示词发送、执行模式切换与任务中止控制器
 */

import { state } from "./state.js";
import { apiFetch, showToast } from "./api.js";
import {
  requestNotificationPermission,
  copyTextToClipboard,
  applyButtonCheckmark,
} from "./utils.js";
import { loadSessions, loadSessionMessages } from "./data.js";
import {
  renderSessionList,
  renderCurrentSessionHeader,
  renderMessagesList,
  renderLiveStreamOverlay,
  updatePromptActionBtnState,
  renderSlashMenu,
} from "./render.js";
import { openNewSessionModal } from "./modals.js";
import { renderModelPicker } from "./render-views.js";

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

export async function copyCodeBlock(codeId, btnEl) {
  let text = (state.codeBlockStore && state.codeBlockStore[codeId]) || "";
  if (!text) {
    const el = document.getElementById(`code-body-${codeId}`) || document.getElementById(`code-pre-${codeId}`);
    if (el) {
      const clone = el.cloneNode(true);
      const fade = clone.querySelector(".md-code-fade");
      if (fade) fade.remove();
      text = clone.innerText || clone.textContent || "";
    }
  }
  if (!text) {
    showToast("无可复制内容", "info");
    return;
  }
  const ok = await copyTextToClipboard(text);
  if (ok) {
    if (btnEl) applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
    showToast("代码已复制", "success");
  } else {
    showToast("复制失败，请手动选择复制", "error");
  }
}

export async function copyThinkingText(detailId, btnEl, e) {
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
    if (btnEl) applyButtonCheckmark(btnEl, { iconSize: "w-3 h-3" });
    showToast("已复制思考过程", "success");
  } else {
    showToast("复制失败，请手动选择复制", "error");
  }
}

export async function loadFullOutput(unifiedId, stepIndex, btnEl, ev) {
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
    renderMessagesList();
  } catch (_) {
    if (btnEl) btnEl.textContent = "重试加载";
  }
}

export function insertSlashCommand(cmd) {
  const inputEl = document.getElementById("prompt-input");
  if (!inputEl) return;
  inputEl.value = `${cmd} `;
  inputEl.focus();
  state.slashMenuOpen = false;
  renderSlashMenu();
}

export function executeSlashDirectly(cmd) {
  state.slashMenuOpen = false;
  renderSlashMenu();
  handleSendPrompt(cmd);
}


export function handlePromptAction() {
  const btn = document.getElementById("prompt-action-btn");
  const action = btn?.dataset?.action || "send";
  if (action === "abort") {
    handleAbortCurrentSession();
  } else {
    handleSendPrompt(null);
  }
}

export function fillPromptAndSend(text) {
  const input = document.getElementById("prompt-input");
  if (input) input.value = text;
  handleSendPrompt(text);
}

export function selectSlashCommand(cmd) {
  const input = document.getElementById("prompt-input");
  if (!input) return;
  state.slashMenuOpen = false;
  const popover = document.getElementById("slash-popover");
  if (popover) popover.classList.add("hidden");

  if (cmd.startsWith("/")) {
    input.value = `${cmd} `;
    input.focus();
  } else {
    input.value = cmd;
    handleSendPrompt(cmd);
  }
}

export function toggleModelPicker(e, forceOpen) {
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
    popover.style.bottom = `${Math.max(10, window.innerHeight - rect.top + 6)}px`;
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
}

export async function selectModel(modelId, e) {
  if (e && typeof e.stopPropagation === "function") e.stopPropagation();
  const popover = document.getElementById("model-picker-popover");
  if (popover) popover.classList.add("hidden");
  const btn = document.getElementById("model-picker-btn");
  const arrow = document.getElementById("model-picker-arrow");
  if (btn) btn.classList.remove("border-sky-500/50", "bg-zinc-800");
  if (arrow) arrow.classList.remove("rotate-180");

  const hiddenSelect = document.getElementById("model-select");
  if (hiddenSelect) {
    hiddenSelect.value = modelId;
  }
  await handleModelChange(modelId);
}
