/**
 * AgentDeck 侧边栏与会话列表视图渲染器
 */

import {
  AGENT_META,
} from "./config.js";
import {
  state,
  isSessionCompletedUnread,
} from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  cleanFriendlyText,
  formatRelativeTime,
  icons,
} from "./utils.js";
import {
  isSessionStatusActive,
  isSessionWorking,
  getProjectInfo,
  getPendingAttentionSessions,
  getAllAttentionSessions,
  getSessionPriorityWeight,
  getAgentDisplayName,
} from "./data.js";

let cachedAgentsHealth = null;

export function renderSidebarStatus() {
  const btn = document.getElementById("sidebar-status-btn");
  const headerIndicator = document.getElementById("header-status-indicator");

  if (state.hasUpdate) {
    // 发现前端代码更新：亮蓝呼吸脉冲 + 向上箭头图标（手机端专属更新入口）
    if (btn) {
      btn.className =
        "flex md:hidden h-8 px-2.5 rounded-lg bg-sky-500/15 hover:bg-sky-500/25 border border-sky-400/50 text-sky-200 items-center gap-1.5 text-xs transition active:scale-95 shadow-[0_0_12px_rgba(56,189,248,0.25)] animate-pulse cursor-pointer focus-visible:ring-1 focus-visible:ring-sky-500 focus:outline-none";
      btn.title = "发现新版本前端代码，点击立即载入更新";
      btn.innerHTML = `
        <svg class="w-3.5 h-3.5 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="19" x2="12" y2="5"></line>
          <polyline points="5 12 12 5 19 12"></polyline>
        </svg>
        <span class="text-[11px] font-semibold text-sky-300">更新</span>
      `;
    }
    if (headerIndicator) {
      headerIndicator.classList.remove("hidden");
      headerIndicator.className =
        "shrink-0 h-6 px-1.5 rounded-md bg-sky-500/20 hover:bg-sky-500/30 border border-sky-400/50 text-sky-300 flex items-center gap-1 text-[11px] transition active:scale-95 shadow-sm animate-pulse cursor-pointer focus-visible:ring-1 focus-visible:ring-sky-500 focus:outline-none";
      headerIndicator.title = "发现前端代码更新，点击立即载入最新代码";
      headerIndicator.innerHTML = `
        <svg class="w-3 h-3 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="19" x2="12" y2="5"></line>
          <polyline points="5 12 12 5 19 12"></polyline>
        </svg>
        <span class="font-semibold text-[10px]">更新</span>
      `;
    }
    return;
  }

  // 常规连接状态：仅手机端保留圆圈图标以供状态显示与更新，电脑网页端隐藏（与右上角扫码前圆点去重）
  if (btn) {
    btn.className =
      "flex md:hidden h-8 w-8 rounded-lg bg-transparent hover:bg-zinc-900/60 text-zinc-300 items-center justify-center text-xs transition active:scale-95 cursor-pointer focus-visible:ring-1 focus-visible:ring-sky-500 focus:outline-none";

    if (state.wsConnected) {
      btn.title = "WebSocket 实时连接正常（双击可强制重载）";
      btn.innerHTML = `
        <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_8px_rgba(52,211,153,0.7)]"></span>
      `;
    } else if (state.wsReconnecting) {
      btn.title = "正在重新连接 WebSocket 服务...";
      btn.innerHTML = `
        <span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping"></span>
      `;
    } else {
      btn.title = "与服务端的实时连接断开（点击尝试重新载入）";
      btn.innerHTML = `
        <span class="w-2.5 h-2.5 rounded-full bg-zinc-600"></span>
      `;
    }
  }

  if (headerIndicator) {
    if (state.wsConnected) {
      headerIndicator.classList.add("hidden");
    } else if (state.wsReconnecting) {
      headerIndicator.classList.remove("hidden");
      headerIndicator.className =
        "shrink-0 h-5 px-1 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 flex items-center gap-1 text-[10px] cursor-pointer";
      headerIndicator.title = "重连中... 点击尝试重新载入";
      headerIndicator.innerHTML = `
        <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping"></span>
        <span>重连中</span>
      `;
    } else {
      headerIndicator.classList.remove("hidden");
      headerIndicator.className =
        "shrink-0 h-5 px-1 rounded bg-zinc-800 border border-zinc-700 text-zinc-400 flex items-center gap-1 text-[10px] cursor-pointer";
      headerIndicator.title = "已离线，点击重新载入";
      headerIndicator.innerHTML = `
        <span class="w-1.5 h-1.5 rounded-full bg-zinc-500"></span>
        <span>离线</span>
      `;
    }
  }
}

export {
  renderHeaderStatus,
  renderSidebarAwakeLock,
  renderAgentsHealthModal,
} from "./render-status-badges.js";
import {
  renderHeaderStatus,
  renderSidebarAwakeLock,
  renderAgentsHealthModal,
} from "./render-status-badges.js";

export function renderSessionList() {
  const container = document.getElementById("session-list-container");
  if (!container) return;
  const savedScrollTop = container.scrollTop;

  const q = state.searchQuery.trim().toLowerCase();
  const rawList = state.allSessions.length > 0 ? state.allSessions : state.sessions;
  const sourceList = sortSessions(rawList);
  const filtered = sourceList.filter((s) => {
    if (state.activeFilter !== "all" && s.agent_type !== state.activeFilter) {
      return false;
    }
    if (!q) return true;
    return (
      (s.title || "").toLowerCase().includes(q) ||
      (s.preview || "").toLowerCase().includes(q) ||
      (s.project_name || "").toLowerCase().includes(q)
    );
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="p-6 text-center text-zinc-500 text-xs">
        <p class="mb-2">无匹配对话</p>
        <button onclick="window.appActions.openNewSessionModal()"
          class="px-2.5 py-1 rounded-lg bg-sky-500/20 text-sky-300 border border-sky-500/30 text-xs">
          + 新建对话
        </button>
      </div>
    `;
    return;
  }

  // 1. 动态提升会话（运行中 / 待审批 / 刚完成未查看）
  const activeUnreadList = filtered.filter((s) => isSessionAttentionNeeded(s));

  activeUnreadList.sort((a, b) => {
    const wa = getSessionPriorityWeight(a);
    const wb = getSessionPriorityWeight(b);
    if (wa !== wb) return wb - wa;
    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  });

  const activeIds = new Set(activeUnreadList.map((s) => s.unified_id));

  // 常驻手动置顶（排除已经在动态列表里的会话，避免重复）
  const manualPinnedList = filtered.filter((s) => s.is_pinned && !activeIds.has(s.unified_id));
  const totalTopCount = activeUnreadList.length + manualPinnedList.length;

  // 2. 按真正工程项目 (is_project === true) 聚合
  const projectMap = new Map();
  for (const ws of state.workspaces) {
    const info = getProjectInfo(ws.workspace_path);
    if (info.key && !projectMap.has(info.key)) {
      projectMap.set(info.key, {
        key: info.key,
        name: ws.workspace_name || info.name,
        path: info.path,
        sessions: [],
      });
    }
  }
  for (const s of filtered) {
    if (!s.is_project) continue;
    const info = getProjectInfo(s.project_path);
    if (!info.key) continue;
    if (!projectMap.has(info.key)) {
      projectMap.set(info.key, {
        key: info.key,
        name: s.project_name || info.name,
        path: info.path,
        sessions: [],
      });
    }
    projectMap.get(info.key).sessions.push(s);
  }

  const projects = Array.from(projectMap.values()).filter(
    (p) => p.sessions.length > 0 || !q
  );

  // 首次加载优先自动展开包含运行中会话或当前选中会话的项目
  if (!state.didInitExpand && projects.length > 0) {
    state.didInitExpand = true;
    let expandedAny = false;
    for (const p of projects) {
      const hasActiveOrSelected = p.sessions.some(
        (s) =>
          isSessionStatusActive(s) ||
          s.status === "waiting_approval" ||
          s.unified_id === state.selectedSessionId
      );
      if (hasActiveOrSelected) {
        state.expandedProjects[p.key] = true;
        expandedAny = true;
      }
    }
    if (!expandedAny) {
      const firstWithConvs = projects.find((p) => p.sessions.length > 0) || projects[0];
      state.expandedProjects[firstWithConvs.key] = true;
    }
  }

  // 3. 临时与最近对话（优先展示非项目的散落临时对话 + 最近活跃对话）
  const looseList = filtered.filter((s) => !s.is_project);
  const recentDisplayList = (looseList.length > 0 ? looseList : filtered).slice(0, 15);

  let html = "";

  // --- 区块 1：置顶与动态活动 ---
  if (totalTopCount > 0) {
    const sectionTitle = activeUnreadList.length > 0 ? "★ 置顶与活动" : "★ 置顶";
    html += `
      <div>
        <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 flex items-center justify-between">
          <div class="flex items-center gap-1.5">
            <span>${sectionTitle}</span>
            ${
              activeUnreadList.length > 0
                ? `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>`
                : ""
            }
          </div>
          <span class="text-[10px] text-zinc-500 font-mono tabular-nums">${totalTopCount}</span>
        </div>
        <div class="space-y-0.5">
          ${activeUnreadList.map((s) => renderCompactSessionItem(s, true)).join("")}
          ${
            activeUnreadList.length > 0 && manualPinnedList.length > 0
              ? `<div class="my-1 border-t border-white/[0.06]"></div>`
              : ""
          }
          ${manualPinnedList.map((s) => renderCompactSessionItem(s, true)).join("")}
        </div>
      </div>
    `;
  }

  // --- 区块 2：项目 (Projects) ---
  const isProjectsCollapsed = !q && !!state.collapsedSections?.projects;
  html += `
    <div>
      <div onclick="window.appActions.toggleSection('projects')"
        class="cursor-pointer select-none px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 hover:text-zinc-300 flex items-center justify-between transition-colors"
        title="点击收纳/展开 项目">
        <div class="flex items-center gap-1.5">
          <span>📁 项目</span>
          <span class="text-[10px] text-zinc-600 font-normal font-mono">(${projects.length})</span>
        </div>
        <div class="flex items-center gap-1" onclick="event.stopPropagation()">
          <button onclick="window.appActions.openNewSessionModal()" class="text-zinc-400 hover:text-sky-400 text-xs px-1" title="添加项目或新建对话">＋</button>
        </div>
      </div>
      ${
        !isProjectsCollapsed
          ? `<div class="space-y-1">
              ${
                projects.length === 0
                  ? `<div class="px-2 py-2 text-[11px] text-zinc-600">暂无工程项目</div>`
                  : projects
                      .map((proj) => {
                        const isOpen = !!state.expandedProjects[proj.key] || !!q;
                        const visibleConvs = proj.sessions.slice(0, 8);
                        return `
                          <div class="group/proj rounded-lg bg-zinc-900/35 border border-zinc-900">
                            <div onclick="window.appActions.toggleProject('${escapeHtml(proj.key)}')"
                              class="cursor-pointer px-2 py-1.5 flex items-center justify-between gap-1.5 rounded-lg hover:bg-zinc-900 text-xs text-zinc-200">
                              <div class="flex items-center gap-1.5 min-w-0 flex-1">
                                <span class="text-sky-400/90 shrink-0">📂</span>
                                <span class="truncate font-medium">${escapeHtml(proj.name)}</span>
                              </div>
                              <div class="flex items-center gap-1 shrink-0">
                                <button onclick="window.appActions.newProjectSession('${escapeHtml(
                                  proj.path
                                )}', event)"
                                  class="w-4 h-4 rounded hover:bg-zinc-800 text-zinc-400 hover:text-sky-400 flex items-center justify-center text-xs"
                                  title="在此项目新建对话">＋</button>
                                <button onclick="window.appActions.hideProject('${escapeHtml(
                                  proj.path
                                )}', event, '${escapeJsSingleQuote(proj.name)}')"
                                  class="w-4 h-4 rounded hover:bg-rose-500/20 text-zinc-500 hover:text-rose-400 flex items-center justify-center text-[11px]"
                                  title="从项目列表移出 (不删对话)">×</button>
                                <span class="text-[10px] text-zinc-500 tabular-nums min-w-[14px] text-right">${
                                  proj.sessions.length
                                }</span>
                              </div>
                            </div>
                            ${
                              isOpen
                                ? `<div class="pl-3 pr-1 pb-1 space-y-0.5 border-l border-zinc-800/80 ml-3 mb-1">
                                    ${
                                      visibleConvs.length === 0
                                        ? `<div class="px-2 py-1 text-[10px] text-zinc-600 italic">无对话</div>`
                                        : visibleConvs
                                            .map((c) => renderCompactSessionItem(c, false))
                                            .join("")
                                    }
                                  </div>`
                                : ""
                            }
                          </div>
                        `;
                      })
                      .join("")
              }
            </div>`
          : ""
      }
    </div>
  `;

  // --- 区块 3：临时会话 ---
  const isConversationsCollapsed = !q && !!state.collapsedSections?.conversations;
  html += `
    <div>
      <div onclick="window.appActions.toggleSection('conversations')"
        class="cursor-pointer select-none px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 hover:text-zinc-300 flex items-center justify-between transition-colors"
        title="点击收纳/展开 临时会话">
        <div class="flex items-center gap-1.5">
          <span>💬 临时会话</span>
        </div>
        <span class="text-[10px] text-zinc-500 tabular-nums">${recentDisplayList.length}</span>
      </div>
      ${
        !isConversationsCollapsed
          ? `<div class="space-y-0.5">
              ${recentDisplayList.map((s) => renderCompactSessionItem(s, true)).join("")}
            </div>`
          : ""
      }
    </div>
  `;

  container.innerHTML = html;
  if (savedScrollTop > 0) {
    container.scrollTop = savedScrollTop;
  }
}

export { renderOtherRunningPill } from "./render-session-card.js";
