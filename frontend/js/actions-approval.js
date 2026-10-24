/**
 * AgentDeck 审批流（放行/拒绝/免审/长按确认）与交互式问答提交动作集
 */

import { state } from "./state.js";
import { apiFetch, showToast } from "./api.js";
import { loadSessions } from "./data.js";
import { renderInteractiveQuestionsCard } from "./render-interactive-questions.js";
import { renderApprovalBanners } from "./render-approval-views.js";
import { handleSendPrompt } from "./actions-chat.js";
import { dispatchCommand } from "./command-queue.js";

let holdTimer = null;
let holdStartTime = 0;

export async function submitApprovalDecision(unifiedId, decision, feedback = "") {
  try {
    const payload = { decision };
    if (feedback && feedback.trim()) {
      payload.feedback = feedback.trim();
    }
    await apiFetch(
      `/api/v1/sessions/${encodeURIComponent(unifiedId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    );
    state.denyFeedbackOpen = false;
    state.denyFeedbackText = "";
    const labelMap = {
      approved: "已允许执行",
      approve_session_edits: "本会话已免审",
      denied: feedback ? "已拒绝并发送指导意见" : "已拒绝执行",
    };
    showToast(labelMap[decision] || "已提交", "success");
    await loadSessions();
  } catch (err) {
    console.error("提交审批决定失败:", err);
  }
}

export function startHoldToApprove(unifiedId, btnEl) {
  const fillEl = btnEl ? btnEl.querySelector(".hold-progress-fill") : null;
  holdStartTime = performance.now();
  const duration = 800;

  if (holdTimer) cancelAnimationFrame(holdTimer);

  function tick(now) {
    const elapsed = now - holdStartTime;
    const pct = Math.min(100, (elapsed / duration) * 100);
    if (fillEl) fillEl.style.width = `${pct}%`;

    if (pct >= 100) {
      if (fillEl) fillEl.style.width = "0%";
      if ("vibrate" in navigator) {
        try {
          navigator.vibrate(80);
        } catch (_) {}
      }
      submitApprovalDecision(unifiedId, "approved");
      return;
    }
    holdTimer = requestAnimationFrame(tick);
  }

  holdTimer = requestAnimationFrame(tick);
}

export function cancelHoldToApprove(btnEl) {
  if (holdTimer) {
    cancelAnimationFrame(holdTimer);
    holdTimer = null;
  }
  const fillEl = btnEl ? btnEl.querySelector(".hold-progress-fill") : null;
  if (fillEl) fillEl.style.width = "0%";
}

export async function handleTogglePin(unifiedId, currentPinned, ev) {
  if (ev) ev.stopPropagation();
  try {
    const nextPinned = currentPinned ? 0 : 1;
    const sess = (state.allSessions || state.sessions).find((s) => s.unified_id === unifiedId);
    if (sess) sess.is_pinned = nextPinned;
    import("./render.js").then((m) => m.renderSessionList()).catch(() => {});

    await dispatchCommand({
      command_type: "PIN_SESSION",
      session_id: unifiedId,
      payload: { is_pinned: !!nextPinned },
    });
    await loadSessions();
  } catch (err) {
    console.error("切换置顶状态失败:", err);
  }
}

export function toggleQuestionOption(turnKey, qi, opt, isMulti) {
  const qKey = `${turnKey}_q${qi}`;
  if (!state.questionSelections[qKey]) {
    state.questionSelections[qKey] = [];
  }
  const cur = state.questionSelections[qKey];
  if (isMulti) {
    if (cur.includes(opt)) {
      state.questionSelections[qKey] = cur.filter((x) => x !== opt);
    } else {
      state.questionSelections[qKey] = [...cur, opt];
    }
  } else {
    state.questionSelections[qKey] = [opt];
  }

  const cardEl = document.getElementById(`interactive-questions-card-${turnKey}`);
  const turn = state.turnsMap[turnKey];
  if (cardEl && turn) {
    cardEl.outerHTML = renderInteractiveQuestionsCard(turn, true);
  }
}

export function onQuestionWriteInInput(turnKey, qi, inputEl) {
  const qKey = `${turnKey}_q${qi}`;
  state.questionWriteIns[qKey] = inputEl.value;
}

export async function submitQuestionAnswers(turnKey) {
  const turn = state.turnsMap[turnKey];
  if (!turn || !turn.questions || turn.questions.length === 0) return;

  const parts = [];
  turn.questions.forEach((q, qi) => {
    const qKey = `${turnKey}_q${qi}`;
    const chosen = state.questionSelections[qKey] || [];
    const writeIn = (state.questionWriteIns[qKey] || "").trim();

    const combined = [...chosen];
    if (writeIn) {
      combined.push(`补充: ${writeIn}`);
    }

    if (combined.length > 0) {
      if (turn.questions.length === 1) {
        parts.push(combined.join("; "));
      } else {
        parts.push(`问题「${q.question}」选择: ${combined.join("; ")}`);
      }
    }
  });

  if (parts.length === 0) {
    showToast("请至少选择一项或输入补充回答", "info");
    return;
  }

  const finalAnswer = parts.join("\n");
  if (!state.persistedQuestionDecisions) {
    state.persistedQuestionDecisions = {};
  }
  state.persistedQuestionDecisions[turnKey] = {
    answeredAt: Date.now(),
    answerText: finalAnswer,
  };

  const cardEl = document.getElementById(`interactive-questions-card-${turnKey}`);
  if (cardEl) {
    cardEl.outerHTML = renderInteractiveQuestionsCard(turn, false);
  }

  await handleSendPrompt(finalAnswer);
}

export async function skipQuestionAnswers(turnKey) {
  const turn = state.turnsMap[turnKey];
  if (!turn || !turn.questions || turn.questions.length === 0) return;

  const parts = [];
  turn.questions.forEach((q) => {
    const firstOpt = q.options && q.options.length > 0 ? q.options[0] : "";
    if (firstOpt) {
      parts.push(firstOpt);
    }
  });

  const fallbackAns = parts.length > 0 ? parts.join("; ") : "按默认方案执行";
  if (!state.persistedQuestionDecisions) {
    state.persistedQuestionDecisions = {};
  }
  state.persistedQuestionDecisions[turnKey] = {
    answeredAt: Date.now(),
    answerText: fallbackAns,
  };

  const cardEl = document.getElementById(`interactive-questions-card-${turnKey}`);
  if (cardEl) {
    cardEl.outerHTML = renderInteractiveQuestionsCard(turn, false);
  }

  await handleSendPrompt(fallbackAns);
}

export const questionActions = {
  toggleQuestionOption,
  onQuestionWriteInInput,
  submitQuestionAnswers,
  skipQuestionAnswers,
};

export function toggleDenyFeedback(open) {
  state.denyFeedbackOpen = open !== undefined ? Boolean(open) : !state.denyFeedbackOpen;
  renderApprovalBanners();
  if (state.denyFeedbackOpen) {
    setTimeout(() => {
      const inp = document.getElementById("deny-feedback-input");
      if (inp) inp.focus();
    }, 50);
  }
}

export function submitDenyWithFeedback(unifiedId) {
  const inp = document.getElementById("deny-feedback-input");
  const feedback = inp ? inp.value.trim() : "";
  submitApprovalDecision(unifiedId, "denied", feedback);
}

export function setQuestionWriteIn(turnKey, qi, val) {
  const qKey = `${turnKey}_q${qi}`;
  state.questionWriteIns[qKey] = val;
}
