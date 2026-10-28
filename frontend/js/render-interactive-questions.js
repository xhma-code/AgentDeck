/**
 * AgentDeck 多选/单选交互式问答卡片渲染组件
 */

import { state } from "./state.js";
import { escapeHtml, escapeJsSingleQuote } from "./utils.js";

export function renderQuestionOptionsHtml(turnKey, qi, q) {
  const qKey = `${turnKey}_q${qi}`;
  const selectedOpts = state.questionSelections[qKey] || [];
  const isMulti = !!q.is_multi_select;
  return (q.options || [])
    .map((opt, oIdx) => {
      const label = typeof opt === "string" ? opt : (opt.label || opt.value || "");
      const value = typeof opt === "string" ? opt : (opt.value || opt.label || "");
      const isSelected = selectedOpts.includes(value) || selectedOpts.includes(label);
      return `
        <button type="button"
          data-qkey="${escapeHtml(qKey)}"
          data-oidx="${oIdx}"
          onclick="window.appActions.toggleQuestionOption('${escapeJsSingleQuote(
            turnKey
          )}', ${qi}, ${oIdx}, ${isMulti})"
          class="text-left text-xs px-2.5 py-1.5 rounded-lg border transition-all active:scale-95 flex items-center gap-1.5 cursor-pointer ${
            isSelected
              ? "bg-sky-500 text-zinc-950 font-bold border-sky-400 shadow-sm"
              : "bg-zinc-900/80 hover:bg-zinc-800 text-zinc-200 border-zinc-700/80"
          }">
          <span class="text-[10px] ${isSelected ? "text-zinc-950" : "text-sky-400"}">${
        isSelected ? (isMulti ? "☑" : "●") : isMulti ? "☐" : "○"
      }</span>
          <span class="break-words">${escapeHtml(label)}</span>
        </button>
      `;
    })
    .join("");
}


export function renderInteractiveQuestionsCard(turn, isLatestTurn) {
  const questions = turn.questions || [];
  if (questions.length === 0) return "";
  const turnKey = turn.turnKey;
  state.turnsMap[turnKey] = turn;

  // 历史轮次：转为轻量折叠卡片，展示已确认决议与选项，防止侵占手机长流
  if (!isLatestTurn) {
    const decision = state.persistedQuestionDecisions?.[turnKey];
    const summaryTitle = decision
      ? `✓ 已决策：${escapeHtml(decision.answerText.slice(0, 32))}${
          decision.answerText.length > 32 ? "..." : ""
        }`
      : `✓ 已完成的交互问答 (共 ${questions.length} 项)`;

    return `
      <details id="interactive-questions-card-${escapeHtml(turnKey)}" class="mt-2 rounded-xl bg-zinc-950/70 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
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
    <div id="interactive-questions-card-${escapeHtml(turnKey)}" class="mt-2.5 rounded-xl bg-zinc-900/90 border border-sky-500/40 p-3 shadow-lg space-y-2">
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
