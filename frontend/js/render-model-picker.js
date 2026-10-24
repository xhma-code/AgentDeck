/**
 * AgentDeck 模型选择器下拉列表与模式选择器渲染引擎
 */

import { MODES_BY_AGENT, DEFAULT_MODELS_BY_AGENT } from "./config.js";
import { state } from "./state.js";
import { escapeHtml, escapeJsSingleQuote, formatTokenCount } from "./utils.js";
import { getModelContextLimit } from "./data.js";

export function renderModelOptionsForAgent(selectEl, agentType, selectedModelId = "") {
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

export function renderModeOptionsForAgent(selectEl, agentType, selectedMode = "ask", isShort = false) {
  if (!selectEl) return "ask";
  const normalizedType = agentType === "claude" ? "claude_code" : (agentType || "agy_cli");
  const list = MODES_BY_AGENT[normalizedType] || MODES_BY_AGENT.agy_cli;

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

export function renderModelPicker() {
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
  const availableIds = new Set(models.map((m) => m.id));
  const activeId =
    sess?.model_name && availableIds.has(sess.model_name)
      ? sess.model_name
      : defaultModelItem ? defaultModelItem.id : "";
  if (sess && (!sess.model_name || !availableIds.has(sess.model_name))) {
    sess.model_name = activeId;
  }
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
              <div class="flex items-center gap-2 min-w-0 flex-1 pr-2">
                <div class="w-1.5 h-1.5 rounded-full shrink-0 ${
                  isSelected ? "bg-sky-400 ring-2 ring-sky-400/40" : "bg-zinc-700"
                }"></div>
                <span class="truncate font-medium text-zinc-100 ${isSelected ? "text-white font-semibold" : ""}">${escapeHtml(m.label)}</span>
                ${
                  m.tag
                    ? `<span class="text-[9px] px-1 py-0.5 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30 shrink-0">${escapeHtml(
                        m.tag
                      )}</span>`
                    : ""
                }
                ${
                  m.is_default
                    ? `<span class="text-[9px] px-1 py-0.5 rounded font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0" title="电脑端当前默认模型">默认</span>`
                    : ""
                }
              </div>
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
