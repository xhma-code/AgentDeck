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
import { renderModelPicker, renderModePicker } from "./render-views.js";
import { selectSession } from "./data-session-selection.js";
import { FORK_TO_CLI_FALLBACK_NOTICE } from "./config.js";
import { dispatchCommand } from "./command-queue.js";

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
    inputEl.style.height = "40px";
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
    const res = await dispatchCommand({
      command_type: "SEND_PROMPT",
      session_id: state.selectedSessionId,
      payload: {
        prompt: text,
        model_name: chosenModel || undefined,
        execution_mode: chosenMode || undefined,
      },
    });

    const data = (res && res.result) ? res.result : (res || {});
    if (data && data.error) {
      showToast(data.error, "error");
      return;
    }
    // 桌面端 IDE 不可达：后端已透明克隆至 AGY CLI 接力执行，切换到新会话跟踪进度
    if (data && data.forked_to_cli && data.unified_id && data.unified_id !== state.selectedSessionId) {
      // WS 广播（SESSION_FORKED）先于本响应到达时已弹过提示，避免双 toast
      if (data.unified_id !== state.lastForkNoticeId) {
        state.lastForkNoticeId = data.unified_id;
        showToast(data.notice || FORK_TO_CLI_FALLBACK_NOTICE, "info");
      }
      try {
        await loadSessions(true);
        await selectSession(data.unified_id);
      } catch (navErr) {
        console.warn("切换到克隆会话失败:", navErr);
        showToast("已改用 AGY 命令行继续跑，请手动打开新会话", "info");
      }
      return;
    }
    // 始终在原会话执行，保持当前选中会话不变
    if (data && (data.status === "sent_to_language_server" || data.status === "sent_via_direct_service")) {
      showToast("已成功下发至桌面 Agent 执行", "info");
    }
    await loadSessions();
    await loadSessionMessages(state.selectedSessionId, true);
  } catch (err) {
    console.error("发送失败:", err);
    showToast(`发送失败: ${err.message}`, "error");
  }
}

export async function handleAbortCurrentSession() {
  if (!state.selectedSessionId) return;
  try {
    const res = await dispatchCommand({
      command_type: "ABORT_SESSION",
      session_id: state.selectedSessionId,
      payload: { reason: "手机端手动中断" },
    });
    const result = res && res.result ? res.result : {};
    if (result.message) {
      showToast(result.message, "info");
    } else {
      showToast("已发送中断信号", "info");
    }
    delete state.pendingTurnThinking[state.selectedSessionId];
    if (state.optimisticMessages[state.selectedSessionId]) {
      state.optimisticMessages[state.selectedSessionId] = [];
    }
    await loadSessions();
    await loadSessionMessages(state.selectedSessionId, true);
  } catch (e) {
    showToast(`中断失败: ${e.message}`, "error");
  }
}

export async function handleModeChange(newMode) {
  if (!state.selectedSessionId) return;
  try {
    const res = await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/mode`,
      {
        method: "POST",
        body: JSON.stringify({ execution_mode: newMode }),
      }
    );
    const finalMode = (res && res.execution_mode) || newMode;
    const s =
      state.sessions.find((item) => item.unified_id === state.selectedSessionId) ||
      state.allSessions.find((item) => item.unified_id === state.selectedSessionId);
    if (s) s.execution_mode = finalMode;
    const hiddenSelect = document.getElementById("execution-mode-select");
    if (hiddenSelect) {
      hiddenSelect.value = finalMode;
    }
    renderModePicker();
    showToast(`模式已切换: ${finalMode}`, "success");
  } catch (e) {
    console.error("模式切换失败:", e);
  }
}

export async function handleModelChange(newModel) {
  if (!state.selectedSessionId) return;
  const s =
    state.sessions.find((item) => item.unified_id === state.selectedSessionId) ||
    state.allSessions.find((item) => item.unified_id === state.selectedSessionId);
  const agentType = s?.agent_type || "agy_cli";
  try {
    localStorage.setItem(`agentdeck_preferred_model_${agentType}`, newModel);
  } catch (_) {}

  try {
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(state.selectedSessionId)}/model`,
      {
        method: "POST",
        body: JSON.stringify({ model_name: newModel }),
      }
    );
    if (s) s.model_name = newModel;
    renderModelPicker();
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
  handlePromptInput();
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

let lastModelsFetchTs = 0;
async function silentRefreshModels() {
  const now = Date.now();
  if (now - lastModelsFetchTs < 10000) return;
  lastModelsFetchTs = now;
  try {
    const res = await apiFetch("/api/v1/models");
    if (res && res.ok && res.models_by_agent) {
      state.modelsByAgent = res.models_by_agent;
      const popover = document.getElementById("model-picker-popover");
      if (popover && !popover.classList.contains("hidden")) {
        renderModelPicker();
      }
    }
  } catch (err) {
    console.debug("静默预检模型列表跳过:", err);
  }
}

export function toggleModelPicker(e, forceOpen) {
  if (e) {
    if (typeof e.stopPropagation === "function") e.stopPropagation();
    if (typeof e.preventDefault === "function") e.preventDefault();
  }
  const modePopover = document.getElementById("mode-picker-popover");
  const modeBtn = document.getElementById("mode-picker-btn");
  const modeArrow = document.getElementById("mode-picker-arrow");
  if (modePopover) modePopover.classList.add("hidden");
  if (modeBtn) modeBtn.classList.remove("border-amber-500/50", "bg-zinc-800");
  if (modeArrow) modeArrow.classList.remove("rotate-180");

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
    silentRefreshModels();
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

export function toggleModePicker(e, forceOpen) {
  if (e) {
    if (typeof e.stopPropagation === "function") e.stopPropagation();
    if (typeof e.preventDefault === "function") e.preventDefault();
  }
  const modelPopover = document.getElementById("model-picker-popover");
  const modelBtn = document.getElementById("model-picker-btn");
  const modelArrow = document.getElementById("model-picker-arrow");
  if (modelPopover) modelPopover.classList.add("hidden");
  if (modelBtn) modelBtn.classList.remove("border-sky-500/50", "bg-zinc-800");
  if (modelArrow) modelArrow.classList.remove("rotate-180");

  const popover = document.getElementById("mode-picker-popover");
  const btn = document.getElementById("mode-picker-btn");
  const arrow = document.getElementById("mode-picker-arrow");
  if (!popover || !btn) return;
  const isHidden = popover.classList.contains("hidden");
  const open = forceOpen !== undefined ? Boolean(forceOpen) : isHidden;
  if (open) {
    renderModePicker();
    const rect = btn.getBoundingClientRect();
    const popoverWidth = Math.min(185, window.innerWidth - 16);
    popover.style.width = `${popoverWidth}px`;
    popover.style.backgroundColor = "#09090b";
    popover.style.bottom = `${Math.max(10, window.innerHeight - rect.top + 6)}px`;
    const maxLeft = window.innerWidth - popoverWidth - 8;
    const left = Math.max(8, Math.min(rect.left, maxLeft));
    popover.style.left = `${left}px`;

    popover.classList.remove("hidden");
    btn.classList.add("border-amber-500/50", "bg-zinc-800");
    if (arrow) arrow.classList.add("rotate-180");
  } else {
    popover.classList.add("hidden");
    btn.classList.remove("border-amber-500/50", "bg-zinc-800");
    if (arrow) arrow.classList.remove("rotate-180");
  }
}

export async function selectMode(modeValue, e) {
  if (e && typeof e.stopPropagation === "function") e.stopPropagation();
  const popover = document.getElementById("mode-picker-popover");
  if (popover) popover.classList.add("hidden");
  const btn = document.getElementById("mode-picker-btn");
  const arrow = document.getElementById("mode-picker-arrow");
  if (btn) btn.classList.remove("border-amber-500/50", "bg-zinc-800");
  if (arrow) arrow.classList.remove("rotate-180");

  const hiddenSelect = document.getElementById("execution-mode-select");
  if (hiddenSelect) {
    hiddenSelect.value = modeValue;
  }
  await handleModeChange(modeValue);
  renderModePicker();
}


export function handlePromptInput() {
  const inputEl = document.getElementById("prompt-input");
  if (!inputEl) return;

  // 1. 动态自适应调整多行文本高度（基础 42px，上限 180px 随后滚动）
  inputEl.style.height = "40px";
  const MAX_PROMPT_HEIGHT = 192; // 对齐 Tailwind max-h-48 (12rem)
  const newHeight = Math.min(Math.max(inputEl.scrollHeight, 42), MAX_PROMPT_HEIGHT);
  inputEl.style.height = `${newHeight}px`;

  // 2. 斜杠指令检查与浮层渲染
  const val = inputEl.value;
  if (val.startsWith("/")) {
    state.slashMenuOpen = true;
    renderSlashMenu(val);
  } else if (state.slashMenuOpen) {
    state.slashMenuOpen = false;
    renderSlashMenu("");
  }

  // 3. 动态刷新发送按钮可用性
  updatePromptActionBtnState();
}
