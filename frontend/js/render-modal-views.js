/**
 * AgentDeck 快捷斜杠指令菜单与 Git 状态弹窗视图
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  getBasename,
  gitPathToSlotId,
} from "./utils.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";
import { GIT_STATUS_BADGES } from "./config.js";

export function highlightSlashItem(idx) {
  state.slashHighlightIndex = idx;
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const items = popover.querySelectorAll("[data-slash-idx]");
  items.forEach((el) => {
    const i = parseInt(el.getAttribute("data-slash-idx"), 10);
    if (i === idx) {
      el.classList.add("bg-zinc-800", "border-zinc-700/80");
      el.classList.remove("hover:bg-zinc-900", "border-transparent");
      el.scrollIntoView({ block: "nearest" });
    } else {
      el.classList.remove("bg-zinc-800", "border-zinc-700/80");
      el.classList.add("hover:bg-zinc-900", "border-transparent");
    }
  });
}
window.highlightSlashItem = highlightSlashItem;

export function renderSlashMenu(filterQuery = "") {
  const popover = document.getElementById("slash-popover");
  if (!popover) return;
  const list = state.workflows || [];
  const q = filterQuery.trim().replace(/^\//, "").toLowerCase();

  const filtered = list.filter((item) => {
    if (!q) return true;
    const cmdClean = item.command.replace(/^\//, "").toLowerCase();
    const lblClean = (item.label || "").toLowerCase();
    return cmdClean.includes(q) || lblClean.includes(q);
  });

  state.filteredSlashCommands = filtered;
  if (state.slashHighlightIndex >= filtered.length) {
    state.slashHighlightIndex = 0;
  }

  if (!state.slashMenuOpen && !filterQuery.startsWith("/")) {
    popover.classList.add("hidden");
    return;
  }

  if (filtered.length === 0) {
    popover.innerHTML = `
      <div class="px-3 py-2.5 text-xs text-zinc-500 flex items-center justify-between">
        <span>无匹配技能或指令</span>
        <span class="text-[10px] text-zinc-600 font-mono">共 ${list.length} 个全部可用</span>
      </div>`;
    popover.classList.remove("hidden");
    return;
  }

  popover.innerHTML = `
    <div class="px-2.5 py-1 text-[11px] text-zinc-400 font-medium border-b border-zinc-800/80 flex items-center justify-between sticky top-0 bg-zinc-950/95 backdrop-blur z-10 select-none">
      <span class="flex items-center gap-1.5 font-medium text-zinc-300">
        <span class="text-sky-400">⚡</span>
        <span>可用技能与指令 (Skills & Commands)</span>
      </span>
      <span class="text-[10px] text-zinc-500 font-mono">${filtered.length} / ${list.length}</span>
    </div>
    <div class="p-1 space-y-0.5">
      ${filtered
        .map((item, idx) => {
          const isQuick = item.category === "quick";
          const isSkill = item.category === "skill" || item.category === "builtin" || item.category === "project";
          
          let badgeCls = "bg-sky-500/15 text-sky-300 border-sky-500/30";
          let badgeText = "指令";
          if (isQuick) {
            badgeCls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            badgeText = "快捷";
          } else if (item.category === "builtin") {
            badgeCls = "bg-cyan-500/15 text-cyan-300 border-cyan-500/30";
            badgeText = "内置";
          } else if (item.category === "project") {
            badgeCls = "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
            badgeText = "项目";
          } else if (isSkill) {
            badgeCls = "bg-purple-500/15 text-purple-300 border-purple-500/30";
            badgeText = "技能";
          }

          const isHigh = idx === state.slashHighlightIndex;
          return `
            <div id="slash-item-${idx}"
              data-slash-idx="${idx}"
              onclick="window.appActions.selectSlashCommand('${escapeJsSingleQuote(item.command)}')"
              onmouseenter="window.highlightSlashItem(${idx})"
              class="cursor-pointer px-2 py-1.5 rounded-lg flex items-center justify-between gap-2 text-xs transition border ${
                isHigh ? "bg-zinc-800 border-zinc-700/80" : "hover:bg-zinc-900 border-transparent"
              }">
              <div class="flex items-center gap-2 min-w-0">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold shrink-0 border ${badgeCls}">${escapeHtml(
            item.command
          )}</span>
                <span class="text-zinc-200 truncate text-[11px] sm:text-xs">${escapeHtml(item.label)}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <span class="text-[9px] text-zinc-500 font-medium px-1 py-0.5 rounded bg-zinc-900 border border-zinc-800">${badgeText}</span>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
  popover.classList.remove("hidden");
}


window.highlightSlashItem = highlightSlashItem;

export async function updateGitStatusBadge() {
  const btn = document.getElementById("git-status-btn");
  const badge = document.getElementById("git-changed-badge");
  if (!btn || !badge) return;

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess || !sess.project_path) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
    return;
  }

  try {
    const data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(sess.project_path)}`);
    if (data.is_git_repo) {
      btn.classList.remove("hidden");
      btn.classList.add("flex");
      const count = data.changed_count || 0;
      badge.textContent = String(count);
      badge.className = `px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold ${
        count > 0
          ? "bg-amber-500/25 text-amber-300 border border-amber-500/40 animate-pulse"
          : "bg-zinc-800 text-zinc-400 border border-zinc-700"
      }`;
    } else {
      btn.classList.add("hidden");
      btn.classList.remove("flex");
    }
  } catch (_) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
  }
}

export function renderGitStatusBody(data) {
  const bodyEl = document.getElementById("doc-preview-body");
  if (!bodyEl) return;

  if (!data.is_git_repo) {
    bodyEl.innerHTML = `<div class="p-6 text-center text-zinc-500 text-xs">当前工作区非 Git 仓库</div>`;
    return;
  }

  const files = data.files || [];

  const listHtml =
    files.length === 0
      ? `<div class="p-6 text-center text-emerald-400 text-xs">✓ 工作区很干净，无未提交修改</div>`
      : files
          .map((f) => {
            const isExp = !!state.gitExpandedDiffs[f.path];
            const badgeCls = GIT_STATUS_BADGES[f.status] || "bg-zinc-800 text-zinc-300 border-zinc-700";
            return `
              <div class="rounded-xl bg-zinc-900/70 border border-zinc-800 p-2.5 text-xs space-y-1.5">
                <div onclick="window.appActions.toggleGitFileDiff('${escapeJsSingleQuote(
                  f.path
                )}')"
                  class="cursor-pointer flex items-center justify-between gap-2 select-none hover:text-sky-300 transition">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="w-5 h-5 rounded flex items-center justify-center font-bold font-mono text-[10px] border ${badgeCls}">
                      ${escapeHtml(f.status)}
                    </span>
                    <span class="font-mono text-xs text-zinc-200 truncate">${escapeHtml(
                      f.path
                    )}</span>
                  </div>
                  <div class="flex items-center gap-1.5 shrink-0 font-mono text-[10px]">
                    ${f.additions > 0 ? `<span class="text-emerald-400">+${f.additions}</span>` : ""}
                    ${f.deletions > 0 ? `<span class="text-rose-400">-${f.deletions}</span>` : ""}
                    <span class="text-zinc-500">${isExp ? "▲" : "▼"}</span>
                  </div>
                </div>
                <div id="${gitPathToSlotId(f.path)}">
                  ${
                    isExp && state.gitDiffCache[f.path]
                      ? renderUnifiedDiffHtml(state.gitDiffCache[f.path], f.path)
                      : ""
                  }
                </div>
              </div>
            `;
          })
          .join("");

  bodyEl.innerHTML = `
    <div class="space-y-3 pb-6">
      <div class="flex items-center justify-between p-3 rounded-xl bg-zinc-950 border border-zinc-800 text-xs">
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded-lg bg-sky-500/15 text-sky-300 border border-sky-500/30 font-mono font-semibold">
            🌿 ${escapeHtml(data.branch || "HEAD")}
          </span>
          <span class="text-zinc-400">${data.changed_count} 个未提交变更文件</span>
        </div>
        ${
          files.length > 0
            ? `<button onclick="window.appActions.viewFullGitDiff()"
                class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-sky-300 text-xs transition">
                查看汇总 Diff
              </button>`
            : ""
        }
      </div>
      <div class="space-y-1.5">${listHtml}</div>
    </div>
  `;
}
