/**
 * AgentDeck 消息长流、Turn 聚合与交互问答卡片渲染器
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  saveToBoundedStore,
  icons,
} from "./utils.js";
import {
  renderMarkdownHtml,
  renderUnifiedDiffHtml,
} from "./markdown.js";

export function isReadOnlyTool(tc) {
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

export function updateCollapseStepsPillVisibility() {
  const pill = document.getElementById("collapse-steps-pill");
  if (!pill) return;
  const hasExpandedTurn =
    Object.values(state.expandedTurns || {}).some(Boolean) ||
    Object.values(state.turnThinkingExpanded || {}).some(Boolean);
  const container = document.getElementById("messages-container");
  const isScrolledDown = container ? container.scrollTop > 80 : false;
  pill.classList.toggle("hidden", !(hasExpandedTurn && isScrolledDown));
}

export function groupMessagesIntoTurns(messages) {
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

export {
  renderTurnProcessBar,
  renderQuestionOptionsHtml,
  renderInteractiveQuestionsCard,
} from "./render-step-cards.js";
import {
  renderTurnProcessBar,
  renderQuestionOptionsHtml,
  renderInteractiveQuestionsCard,
} from "./render-step-cards.js";

export { renderMdDocQuickCards } from "./render-step-cards.js";
import { renderMdDocQuickCards } from "./render-step-cards.js";

export function renderMessagesList() {
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

export function scrollToBottom() {
  const container = document.getElementById("messages-container");
  if (!container) return;
  container.scrollTop = container.scrollHeight;
  const jumpBtn = document.getElementById("scroll-bottom-pill");
  if (jumpBtn) jumpBtn.classList.add("hidden");
}
