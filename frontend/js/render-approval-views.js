/**
 * AgentDeck 审批决策横幅与操作交互视图
 */

import { state } from "./state.js";
import { escapeHtml, escapeJsSingleQuote, icons } from "./utils.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";

export function renderApprovalBanners() {
  const topBanner = document.getElementById("global-approval-banner");
  const bottomBar = document.getElementById("bottom-approval-bar");
  if (!topBanner || !bottomBar) return;

  const list = state.pendingApprovals || [];
  if (list.length === 0) {
    topBanner.classList.add("hidden");
    bottomBar.classList.add("hidden");
    if (window.updateBottomControlHeight) window.updateBottomControlHeight();
    return;
  }

  topBanner.classList.remove("hidden");
  topBanner.innerHTML = `
    <div class="flex items-center justify-between gap-2 px-3 py-1 bg-amber-500/20 border-b border-amber-500/40 text-[11px] text-amber-200">
      <div class="flex items-center gap-1.5 truncate">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping shrink-0"></span>
        <span class="font-semibold">${list.length} 项待审批</span>
        <span class="text-amber-300/80 truncate">(${escapeHtml(list[0].tool_name)})</span>
      </div>
      <button onclick="window.appActions.selectSession('${escapeHtml(list[0].unified_id)}')"
        class="px-2 py-0.5 rounded bg-amber-400 text-zinc-950 font-semibold shrink-0">
        处理
      </button>
    </div>
  `;

  const activeApproval =
    list.find((p) => p.unified_id === state.selectedSessionId) || list[0];
  const isHighRisk = activeApproval.risk_level === "high";

  bottomBar.classList.remove("hidden");
  bottomBar.innerHTML = `
    <div class="p-2.5 bg-zinc-900/98 border-t-2 ${
      isHighRisk ? "border-rose-500" : "border-amber-500"
    } shadow-2xl">
      <div class="max-w-5xl mx-auto">
        <div class="flex items-center justify-between gap-2 mb-1">
        <div class="flex items-center gap-1.5">
          <span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${
            isHighRisk
              ? "bg-rose-500/25 text-rose-300 border border-rose-500/40"
              : "bg-amber-500/25 text-amber-300 border border-amber-500/40"
          }">
            ${isHighRisk ? "⚠️ 高危拦截" : "🛡️ 权限审批"}
          </span>
          <span class="font-mono text-xs font-semibold text-zinc-200">${escapeHtml(
            activeApproval.tool_name
          )}</span>
        </div>
      </div>

      <div class="font-mono text-[11px] text-zinc-200 bg-black/60 border border-zinc-800 rounded-lg px-2 py-1 mb-1.5 break-all max-h-20 overflow-y-auto">
        ${escapeHtml(activeApproval.args_summary)}
      </div>

      ${activeApproval.diff_text ? renderUnifiedDiffHtml(activeApproval.diff_text, activeApproval.args_summary) : ""}

      ${
        state.denyFeedbackOpen
          ? `
            <div class="mt-2 p-2 rounded-xl bg-zinc-950 border border-rose-500/40 space-y-2">
              <div class="text-[11px] text-rose-300 font-semibold flex items-center justify-between">
                <span>✕ 拒绝原因与指导意见 (将传给 Agent 调整方案)</span>
                <button type="button" onclick="window.appActions.toggleDenyFeedback(false)" class="text-zinc-400 hover:text-white px-1">收起</button>
              </div>
              <textarea id="deny-feedback-input" rows="2"
                placeholder="告诉 Agent 为什么拒绝或应该改成什么方案（例如：不要删这个函数，改成可选参数）..."
                class="w-full px-2.5 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-rose-400 resize-none"></textarea>
              <div class="flex items-center justify-end gap-2">
                <button type="button"
                  onclick="window.appActions.submitApproval('${escapeHtml(
                    activeApproval.unified_id
                  )}', 'denied')"
                  class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs">
                  直接拒绝
                </button>
                <button type="button"
                  onclick="window.appActions.submitDenyWithFeedback('${escapeHtml(
                    activeApproval.unified_id
                  )}')"
                  class="px-3 py-1 rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs shadow active:scale-95 transition">
                  ✓ 拒绝并发送指导
                </button>
              </div>
            </div>
          `
          : `
            <div class="grid grid-cols-12 gap-2 mt-2">
              <button onclick="window.appActions.toggleDenyFeedback(true)"
                class="col-span-4 min-h-[42px] rounded-xl bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 font-semibold text-xs active:scale-95 transition flex items-center justify-center gap-1">
                <span>✕ 拒绝</span>
                <span class="text-[10px] text-rose-400/80">✎</span>
              </button>

              ${
                isHighRisk
                  ? `
                    <button
                      onpointerdown="window.appActions.startHold('${escapeHtml(
                        activeApproval.unified_id
                      )}', this)"
                      onpointerup="window.appActions.cancelHold(this)"
                      onpointerleave="window.appActions.cancelHold(this)"
                      onpointercancel="window.appActions.cancelHold(this)"
                      class="hold-approve-btn col-span-8 min-h-[42px] rounded-xl bg-amber-500 text-zinc-950 font-bold text-xs shadow-lg">
                      <div class="hold-progress-fill"></div>
                      <span class="relative z-10">⚡ 长按 0.8s 允许</span>
                    </button>
                  `
                  : `
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approve_session_edits')"
                      class="col-span-3 min-h-[42px] rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-200 font-medium text-[11px] active:scale-95 transition">
                      免审
                    </button>
                    <button onclick="window.appActions.submitApproval('${escapeHtml(
                      activeApproval.unified_id
                    )}', 'approved')"
                      class="col-span-5 min-h-[42px] rounded-xl bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-bold text-xs shadow-lg active:scale-95 transition">
                      ✓ 允许
                    </button>
                  `
              }
            </div>
          `
      }
      </div>
    </div>
  `;
  if (window.updateBottomControlHeight) window.updateBottomControlHeight();
}
