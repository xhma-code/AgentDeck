import { apiFetch } from "./api.js";
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

  const sess =
    (state.allSessions || []).find((s) => s.unified_id === state.selectedSessionId) ||
    (state.sessions || []).find((s) => s.unified_id === state.selectedSessionId);
  if (!sess || !sess.project_path) {
    btn.classList.add("hidden");
    btn.classList.remove("flex");
    return;
  }

  try {
    const data = await apiFetch(`/api/v1/sessions/${encodeURIComponent(sess.unified_id)}/modified-files`);
    if (data && data.is_git_repo) {
      btn.classList.remove("hidden");
      btn.classList.add("flex");
      state.currentGitStatus = data;

      const isMobile = window.innerWidth < 640;
      const allUncommitted = data.summary?.uncommitted_count ?? 0;
      const allCommitted = data.summary?.committed_count ?? 0;
      const sessionUncommitted = data.summary?.session_uncommitted_count ?? 0;
      const sessionCommitted = data.summary?.session_committed_count ?? 0;
      const sessionTotal = data.summary?.session_total_modified ?? 0;

      if (isMobile) {
        // 手机端：右上角 Git 上的数字显示只统计本会话的
        if (sessionUncommitted > 0) {
          badge.textContent = String(sessionUncommitted);
          badge.className = "px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-amber-500/25 text-amber-300 border border-amber-500/40 animate-pulse";
          btn.title = `Git (本会话): ${sessionUncommitted} 个未提交变更${sessionCommitted > 0 ? `，${sessionCommitted} 个已提交` : ""}`;
        } else if (sessionTotal > 0) {
          badge.textContent = String(sessionTotal);
          badge.className = "px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30";
          btn.title = `Git (本会话): 全部已提交 (${sessionTotal} 个文件)`;
        } else {
          badge.textContent = "0";
          badge.className = "px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-zinc-800 text-zinc-400 border border-zinc-700";
          btn.title = "Git (本会话): 未修改文件";
        }
      } else {
        // 网页端：Git 数字统计所有的会话 / 工作区全量未提交
        if (allUncommitted > 0) {
          badge.textContent = String(allUncommitted);
          badge.className = "px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-amber-500/25 text-amber-300 border border-amber-500/40 animate-pulse";
          btn.title = `Git (全量): ${allUncommitted} 个未提交变更${allCommitted > 0 ? `，${allCommitted} 个已提交` : ""}`;
        } else if (allCommitted > 0) {
          badge.textContent = String(allCommitted);
          badge.className = "px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30";
          btn.title = `Git (全量): 全部已提交 (${allCommitted} 个文件)`;
        } else {
          badge.textContent = "0";
          badge.className = "px-1.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-zinc-800 text-zinc-400 border border-zinc-700";
          btn.title = "Git: 工作区干净";
        }
      }
    } else {
      btn.classList.add("hidden");
      btn.classList.remove("flex");
    }
  } catch (_) {
    try {
      const fb = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(sess.project_path)}`);
      if (fb && fb.is_git_repo) {
        btn.classList.remove("hidden");
        btn.classList.add("flex");
        state.currentGitStatus = fb;
        const count = fb.changed_count || 0;
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
    } catch (e) {
      btn.classList.add("hidden");
      btn.classList.remove("flex");
    }
  }
}

// 监听窗口尺寸变化，自动同步桌面端(全量)与手机端(本会话)统计数字
if (typeof window !== "undefined") {
  let _resizeTimer = null;
  window.addEventListener("resize", () => {
    if (_resizeTimer) clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => {
      if (state.currentGitStatus && state.selectedSessionId) {
        updateGitStatusBadge();
      }
    }, 200);
  });
}

export function renderGitStatusBody(data) {
  const bodyEl = document.getElementById("doc-preview-body");
  if (!bodyEl) return;

  if (!data || (!data.is_git_repo && data.files?.length === 0)) {
    bodyEl.innerHTML = `<div class="p-6 text-center text-zinc-500 text-xs">当前工作区非 Git 仓库</div>`;
    return;
  }

  const isMobile = window.innerWidth < 640;
  const curSess =
    (state.allSessions || []).find((s) => s.unified_id === state.selectedSessionId) ||
    (state.sessions || []).find((s) => s.unified_id === state.selectedSessionId);
  const defaultProjectName = data.project_name || (data.project_path ? getBasename(data.project_path) : "") || curSess?.project_name || "工作区";
  const defaultSessionTitle = data.session_title || curSess?.title || "当前会话";

  // 手机端：仅显示当前会话更改的文件；电脑端：显示所有变更文件
  const rawFiles = data.files || [];
  const allFiles = isMobile
    ? rawFiles.filter((f) => Boolean(f.from_session))
    : rawFiles;

  const uncommittedFiles = allFiles.filter((f) => f.status === "uncommitted" || f.status === "M" || f.status === "A" || f.status === "D" || f.status === "U");
  const committedFiles = allFiles.filter((f) => f.status === "committed");

  const filter = state.gitFilesFilter || "all";
  let displayFiles = allFiles;
  if (filter === "uncommitted") displayFiles = uncommittedFiles;
  else if (filter === "committed") displayFiles = committedFiles;

  const tabBtnClass = (active) =>
    `px-2.5 sm:px-3 py-1 rounded-lg text-xs font-medium transition cursor-pointer select-none ${
      active
        ? "bg-zinc-800 text-sky-300 shadow-sm border border-zinc-700"
        : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900"
    }`;

  const listHtml =
    displayFiles.length === 0
      ? `<div class="p-5 text-center text-zinc-400 text-xs">
          ${isMobile ? "当前会话暂无文件变更" : "✓ 此筛选条件下无文件变更"}
        </div>`
      : displayFiles
          .map((f) => {
            const isExp = !!state.gitExpandedDiffs[f.path];
            const isUncommitted = f.status === "uncommitted" || f.status === "M" || f.status === "A" || f.status === "D" || f.status === "U";
            const gitCode = f.git_status || (f.status !== "uncommitted" && f.status !== "committed" ? f.status : "M");
            
            // 提交状态徽标（放置在最后面）
            const badgeHtml = isUncommitted
              ? `<span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 shrink-0">未提交 ${escapeHtml(gitCode)}</span>`
              : `<span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shrink-0">✓ 已提交</span>`;

            const adds = isUncommitted ? (f.git_additions || f.additions || f.session_additions || 0) : (f.session_additions || 0);
            const dels = isUncommitted ? (f.git_deletions || f.deletions || f.session_deletions || 0) : (f.session_deletions || 0);
            const fileName = f.filename || getBasename(f.path);
            const itemProject = f.project_name || defaultProjectName;
            const itemSession = f.session_title || (f.from_session ? defaultSessionTitle : "工作区变更");

            return `
              <div class="rounded-xl bg-zinc-900/70 border ${isUncommitted ? "border-amber-500/30 bg-amber-950/10" : "border-zinc-800"} py-1.5 px-2.5 sm:py-2.5 sm:px-3 text-xs space-y-1 transition">
                <div onclick="window.appActions.toggleGitFileDiff('${escapeJsSingleQuote(f.path)}')"
                  class="cursor-pointer flex items-center justify-between gap-1.5 sm:gap-2 select-none hover:text-sky-300 transition">
                  <!-- 左侧最前面：电脑端显示项目与会话名，手机端紧凑展示文件图标与文件名 -->
                  <div class="flex items-center gap-1.5 sm:gap-2 min-w-0 flex-1">
                    <svg class="w-3.5 h-3.5 text-zinc-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1 sm:gap-1.5 flex-wrap min-w-0">
                        <!-- 电脑端展示项目名称与会话名称，手机端不展示 -->
                        <span class="hidden sm:inline-flex px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-zinc-800 text-zinc-300 border border-zinc-700 shrink-0" title="项目名称">
                          ${escapeHtml(itemProject)}
                        </span>
                        <span class="hidden sm:inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-sky-500/15 text-sky-300 border border-sky-500/30 truncate max-w-[130px] sm:max-w-[200px] shrink-0" title="会话名称: ${escapeHtml(itemSession)}">
                          ${escapeHtml(itemSession)}
                        </span>
                        <span class="font-mono text-xs text-zinc-100 font-semibold truncate" title="${escapeHtml(f.path)}">
                          ${escapeHtml(fileName)}
                        </span>
                      </div>
                      <div class="font-mono text-[10px] text-zinc-500 truncate mt-0.5" title="${escapeHtml(f.path)}">
                        ${escapeHtml(f.path)}
                      </div>
                    </div>
                  </div>
                  <!-- 右侧最后面：增删统计 + 提交状态移动到最后面 + 折叠箭头 -->
                  <div class="flex items-center gap-1.5 sm:gap-2 shrink-0">
                    <div class="flex items-center gap-1 font-mono text-[10px]">
                      ${adds > 0 ? `<span class="text-emerald-400">+${adds}</span>` : ""}
                      ${dels > 0 ? `<span class="text-rose-400">-${dels}</span>` : ""}
                    </div>
                    ${badgeHtml}
                    <span class="text-zinc-500 text-[10px] ml-0.5">${isExp ? "▲" : "▼"}</span>
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
      <!-- 汇总看板：最前面显示 项目名称 会话名称，提交状态 移动到最后面 -->
      <div class="p-2.5 sm:p-3 rounded-xl bg-zinc-950 border border-zinc-800 text-xs space-y-2">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <!-- 最前面：项目名称与会话名称（电脑端显示，手机端隐藏） -->
          <div class="flex items-center gap-1.5 min-w-0 flex-1 flex-wrap">
            <span class="hidden sm:inline-flex px-2 py-0.5 rounded-lg bg-zinc-900 border border-zinc-800 text-zinc-200 font-mono font-medium items-center gap-1 shrink-0" title="项目名称">
              <svg class="w-3.5 h-3.5 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
              <span>${escapeHtml(defaultProjectName)}</span>
            </span>
            <span class="hidden sm:inline-flex px-2 py-0.5 rounded-lg bg-zinc-900/80 border border-zinc-800 text-sky-300 font-medium max-w-[200px] sm:max-w-xs truncate shrink-0" title="当前会话">
              💬 ${escapeHtml(defaultSessionTitle)}
            </span>
            <span class="px-2 py-0.5 rounded-lg bg-sky-500/10 text-sky-300 border border-sky-500/25 font-mono text-[11px] shrink-0">
              🌿 ${escapeHtml(data.branch || "HEAD")}
            </span>
          </div>

          <!-- 最后面：提交状态汇总与全量 Diff 按钮 -->
          <div class="flex items-center gap-2 shrink-0">
            <div class="text-zinc-400 font-mono text-[11px] flex items-center gap-1.5">
              <span>共 ${allFiles.length} 个文件</span>
              <span>·</span>
              ${
                uncommittedFiles.length > 0
                  ? `<span class="text-amber-400 font-semibold">${uncommittedFiles.length} 未提交</span>`
                  : `<span class="text-emerald-400 font-medium">✓ 全已提交</span>`
              }
            </div>
            ${
              allFiles.length > 0
                ? `<button onclick="window.appActions.viewFullGitDiff()"
                    class="px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-sky-300 text-xs transition active:scale-95 shrink-0"
                    title="查看汇总修改 Diff">
                    查看汇总 Diff
                  </button>`
                : ""
            }
          </div>
        </div>
      </div>

      <!-- 筛选 Tab（全部 / 未提交 / 已提交） -->
      <div class="flex items-center gap-1 bg-zinc-950/80 p-1 rounded-xl border border-zinc-800/80 w-fit">
        <button onclick="window.appActions.setGitFilesFilter('all')" class="${tabBtnClass(filter === "all")}">
          全部 (${allFiles.length})
        </button>
        <button onclick="window.appActions.setGitFilesFilter('uncommitted')" class="${tabBtnClass(filter === "uncommitted")}">
          未提交 (${uncommittedFiles.length})
        </button>
        ${
          committedFiles.length > 0
            ? `<button onclick="window.appActions.setGitFilesFilter('committed')" class="${tabBtnClass(filter === "committed")}">
                已提交 (${committedFiles.length})
              </button>`
            : ""
        }
      </div>

      <!-- 文件列表：手机端仅显示本会话文件并紧凑压缩卡片宽度，网页端全量展示 -->
      <div class="space-y-1 sm:space-y-1.5">${listHtml}</div>
    </div>
  `;
}


export function renderInteractiveDecisionModalHtml(form, selectedMap = {}, customInputMap = {}) {
  if (!form || !form.fields || form.fields.length === 0) {
    return `<div class="p-4 text-center text-zinc-400 text-xs">无待决议内容</div>`;
  }

  const fieldsHtml = form.fields
    .map((field, fIdx) => {
      const fKey = field.key || `q${fIdx}`;
      const title = field.title || field.header || `问题 ${fIdx + 1}`;
      const desc = field.description || field.question || "";
      const isMulti = field.is_multi_select || field.type === "multiselect";
      const selected = selectedMap[fKey] || [];

      const optsHtml = (field.options || [])
        .map((opt, oIdx) => {
          let label = typeof opt === "string" ? opt : (opt.label || opt.value || "");
          let value = typeof opt === "string" ? opt.split(" — ")[0].trim() : (opt.value || opt.label || "");
          let descText = typeof opt === "object" && opt.description ? opt.description : "";
          if (typeof opt === "string" && opt.includes(" — ")) {
            descText = opt.split(" — ").slice(1).join(" — ").trim();
          }

          const isRec = label.includes("(Recommended)") || value.includes("(Recommended)");
          const isChosen = selected.includes(value);

          return `
            <div onclick="window.appActions.selectDecisionOption('${escapeJsSingleQuote(fKey)}', '${escapeJsSingleQuote(value)}', ${!!isMulti})"
              onkeydown="if(event.key===' '||event.key==='Enter'){event.preventDefault();window.appActions.selectDecisionOption('${escapeJsSingleQuote(fKey)}', '${escapeJsSingleQuote(value)}', ${!!isMulti});}"
              tabindex="0"
              role="${isMulti ? 'checkbox' : 'radio'}"
              aria-checked="${isChosen}"
              class="group relative flex items-center gap-2.5 py-1.5 px-3 rounded-lg border transition-all cursor-pointer select-none active:scale-[0.99] min-h-[36px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-1 focus-visible:ring-offset-zinc-950 ${
                isChosen
                  ? "bg-sky-500/15 border-sky-400/80 shadow-sm"
                  : "bg-zinc-900/70 hover:bg-zinc-800/80 border-zinc-800 text-zinc-300"
              }">
              <div class="shrink-0 flex items-center justify-center">
                <span class="w-3.5 h-3.5 rounded-full flex items-center justify-center text-[9px] transition ${
                  isChosen
                    ? "bg-sky-500 text-zinc-950 font-bold shadow-sm shadow-sky-500/50"
                    : "border-2 border-zinc-600 bg-zinc-800/50 text-transparent"
                }">
                  ${isMulti ? "✓" : `<span class="w-1.5 h-1.5 rounded-full ${isChosen ? "bg-zinc-950" : "bg-transparent"}"></span>`}
                </span>
              </div>
              <div class="flex-1 min-w-0">
                <div class="flex items-center justify-between gap-2 leading-tight">
                  <div class="flex items-center gap-1.5 flex-wrap min-w-0">
                    <span class="font-semibold text-xs sm:text-sm leading-tight ${isChosen ? "text-sky-200" : "text-zinc-200"}">
                      ${escapeHtml(label.replace(" (Recommended)", ""))}
                    </span>
                    ${
                      isRec
                        ? `<span class="px-1 py-0.5 rounded text-[9px] leading-none bg-sky-500/20 text-sky-400 border border-sky-500/40 font-bold shrink-0">Recommended</span>`
                        : ""
                    }
                  </div>
                  <span class="hidden sm:inline-block text-[10px] font-mono px-1 py-0.5 rounded bg-zinc-800/80 text-zinc-400 border border-zinc-700/60 shrink-0 leading-none">
                    [${oIdx + 1}]
                  </span>
                </div>
                ${
                  descText
                    ? `<div class="mt-0.5 text-[11px] leading-snug ${isChosen ? "text-sky-300/80" : "text-zinc-400"}">${escapeHtml(descText)}</div>`
                    : ""
                }
              </div>
            </div>
          `;
        })
        .join("");

      return `
        <div class="space-y-2.5 pt-2 first:pt-0">
          <div class="flex items-center gap-1.5 font-bold text-xs sm:text-sm text-zinc-100">
            <span class="w-1.5 h-3.5 rounded-full bg-sky-400 shrink-0"></span>
            <span>${escapeHtml(title)}</span>
          </div>
          ${
            desc
              ? `<div class="text-xs text-zinc-300 bg-zinc-900/50 p-2.5 rounded-xl border border-zinc-800/60 whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto">${escapeHtml(desc)}</div>`
              : ""
          }
          <div class="space-y-1">${optsHtml}</div>
        </div>
      `;
    })
    .join("");

  return `
    <div class="flex items-center justify-between pb-3 border-b border-zinc-800/80 shrink-0">
      <div class="flex items-center gap-2">
        <div class="w-7 h-7 rounded-lg bg-sky-500/10 border border-sky-500/30 flex items-center justify-center text-sky-400">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <div>
          <div id="decision-modal-title" class="font-bold text-sm text-zinc-100 flex items-center gap-1.5">
            <span>交互式决策问答</span>
          </div>
          <div class="text-[10px] text-zinc-500">Agent 正在挂起等待确认</div>
        </div>
      </div>
      <div id="decision-countdown-badge" onclick="window.appActions.extendDecisionCountdown(60)" title="点击延长决策时间 (+60s)" class="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-950/80 hover:bg-amber-900/80 border border-amber-500/40 text-amber-300 font-mono text-[11px] shadow-sm cursor-pointer select-none transition active:scale-95">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
        <span id="decision-countdown-timer">45s 后自动推荐</span>
        <span class="text-[10px] text-amber-400/80 font-normal hover:text-amber-200 ml-0.5 underline decoration-dotted">+60s</span>
      </div>
    </div>

    <!-- 顶部进度条 -->
    <div class="w-full h-1 bg-zinc-900 rounded-full overflow-hidden shrink-0">
      <div id="decision-progress-bar" class="h-full bg-amber-500 transition-all duration-1000 ease-linear" style="width: 100%"></div>
    </div>

    <!-- 中部滚动问答与选项 -->
    <div class="overflow-y-auto space-y-4 pr-1 flex-1 py-1">
      ${fieldsHtml}
    </div>

    <!-- 底部操作与提交条 -->
    <div class="pt-3 border-t border-zinc-800/80 space-y-2 shrink-0">
      <div class="flex items-center justify-between text-[11px] text-zinc-500">
        <span class="hidden sm:inline">桌面快捷键：按 1~9 选择选项，按 Enter 直接确认</span>
        <span id="decision-retry-status" class="text-amber-400 font-medium"></span>
      </div>
      <button id="decision-submit-btn" onclick="window.appActions.confirmInteractiveDecision()"
        class="w-full py-2.5 px-4 rounded-xl bg-sky-500 hover:bg-sky-400 active:scale-[0.99] text-zinc-950 font-bold text-xs sm:text-sm flex items-center justify-center gap-2 shadow-lg shadow-sky-500/20 transition cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950">
        <span id="decision-submit-text">确定</span>
      </button>
    </div>
  `;
}
