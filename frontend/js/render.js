/**
 * AgentDeck UI 核心视图渲染器（侧边栏、会话流、审批栏、问答卡片、Git 状态等）
 */

import {
  GIT_STATUS_BADGES,
  AGENT_META,
  MODES_BY_AGENT,
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
  formatElapsedSec,
  formatTokenCount,
  getBasename,
  saveToBoundedStore,
  gitPathToSlotId,
  icons,
} from "./utils.js";
import {
  renderMarkdownHtml,
  renderUnifiedDiffHtml,
} from "./markdown.js";
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
let qrExpireTimer = null;

export function renderOtherRunningPill() {
  const pill = document.getElementById("other-running-pill");
  const dotEl = document.getElementById("other-running-dot");
  const textEl = document.getElementById("other-running-text");
  const countEl = document.getElementById("other-running-count");
  const mobileBadge = document.getElementById("mobile-menu-badge");
  if (!pill) return;

  const attentionList = getPendingAttentionSessions();
  const count = attentionList.length;

  if (count === 0) {
    pill.classList.add("hidden");
    pill.classList.remove("flex");
    if (mobileBadge) {
      mobileBadge.classList.add("hidden");
    }
    return;
  }

  pill.classList.remove("hidden");
  pill.classList.add("flex");

  const approvalCount = attentionList.filter((s) => s.status === "waiting_approval").length;
  const unreadCount = attentionList.filter((s) => isSessionCompletedUnread(s)).length;
  const runningCount = attentionList.filter(
    (s) => isSessionStatusActive(s) || (typeof isSessionWorking === "function" && isSessionWorking(s.unified_id))
  ).length;

  let dotColorClass = "bg-emerald-400 animate-pulse";
  let pillColorClass = "border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300";
  let textDesc = `${count} 个后台运行中`;

  if (approvalCount > 0) {
    dotColorClass = "bg-amber-400 animate-ping";
    pillColorClass = "border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-amber-300";
    if (runningCount === 0 && unreadCount === 0) {
      textDesc = `${approvalCount} 个待审批任务`;
    } else {
      textDesc = `${approvalCount} 待审批 · ${runningCount + unreadCount} 活跃`;
    }
  } else if (unreadCount > 0) {
    dotColorClass = "bg-sky-400 glow-sky";
    pillColorClass = "border-sky-500/30 bg-sky-500/10 hover:bg-sky-500/20 text-sky-300";
    if (runningCount === 0) {
      textDesc = `${unreadCount} 个完成待查看`;
    } else {
      textDesc = `${unreadCount} 待查看 · ${runningCount} 运行中`;
    }
  }

  pill.className = `h-10 min-h-[40px] sm:h-8 sm:min-h-0 px-2.5 rounded-lg border text-xs flex items-center gap-1.5 active:scale-95 transition cursor-pointer select-none focus-visible:ring-1 focus-visible:ring-sky-500 focus:outline-none ${pillColorClass}`;
  if (dotEl) {
    dotEl.className = `w-2 h-2 rounded-full shrink-0 ${dotColorClass}`;
  }
  if (textEl) {
    textEl.textContent = textDesc;
  }
  if (countEl) {
    countEl.textContent = String(count);
  }

  if (mobileBadge) {
    mobileBadge.classList.remove("hidden");
    mobileBadge.className = `absolute top-1.5 right-1.5 w-2 h-2 rounded-full pointer-events-none ${dotColorClass}`;
  }
}

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
        <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse glow-emerald"></span>
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


export function renderHeaderStatus() {
  renderSidebarStatus();
  const netBadge = document.getElementById("network-status-badge");
  const reconnectBanner = document.getElementById("reconnect-banner");
  if (!netBadge) return;

  if (state.wsConnected) {
    const tsOnline = !!state.systemStatus?.tailscale?.is_online;
    const ztOnline = !!state.systemStatus?.tailscale?.zerotier_ip;
    const vpnOnline = tsOnline || ztOnline;
    netBadge.innerHTML = `<span class="w-2 h-2 rounded-full ${
      vpnOnline ? "bg-emerald-400" : "bg-sky-400"
    }"></span>`;
    if (reconnectBanner) reconnectBanner.classList.add("hidden");
  } else {
    netBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>`;
    if (reconnectBanner) reconnectBanner.classList.remove("hidden");
  }
}

export function renderSidebarAwakeLock() {
  const dot = document.getElementById("sidebar-awake-dot");
  const text = document.getElementById("sidebar-awake-text");
  if (!dot || !text) return;
  const active = !!state.systemStatus?.awake_lock_active;
  if (active) {
    dot.className = "w-1.5 h-1.5 rounded-full bg-emerald-400";
    text.textContent = "防休眠: 开";
    text.className = "text-emerald-300 font-medium";
  } else {
    dot.className = "w-1.5 h-1.5 rounded-full bg-zinc-600";
    text.textContent = "防休眠: 关";
    text.className = "text-zinc-400";
  }
}

export function renderAgentsHealthModal() {
  const body = document.getElementById("agents-health-modal-body");
  if (!body) return;
  const list = state.systemStatus?.agents_health || [];
  body.innerHTML = list
    .map(
      (a) => `
        <div class="p-2.5 rounded-xl bg-zinc-950/80 border border-zinc-800 flex items-start justify-between gap-2">
          <div class="space-y-0.5 min-w-0">
            <div class="text-xs font-semibold flex items-center gap-1.5 ${
              a.ready ? "text-emerald-300" : "text-zinc-400"
            }">
              <span class="w-1.5 h-1.5 rounded-full ${a.ready ? "bg-emerald-400" : "bg-zinc-600"}"></span>
              <span>${escapeHtml(a.agent_label)}</span>
            </div>
            <div class="text-[10px] text-zinc-500 leading-relaxed truncate">${escapeHtml(a.description || "")}</div>
          </div>
          <span class="text-[10px] px-1.5 py-0.5 rounded font-mono shrink-0 ${
            a.ready ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-zinc-800 text-zinc-500"
          }">

            ${a.ready ? "就绪" : "未挂载"}
          </span>
        </div>
      `
    )
    .join("");
}

export function renderCompactSessionItem(s, showProjectTag = true) {
  const meta = AGENT_META[s.agent_type] || AGENT_META.claude_code;
  const isSelected = s.unified_id === state.selectedSessionId;
  const isUnread = isSessionCompletedUnread(s);

  // 运行/等待审批/活跃/完成未看状态判定：优先在右侧显示对应状态徽标
  const isWorking = typeof isSessionWorking === "function" ? isSessionWorking(s.unified_id) : false;
  let statusDotHtml = null;
  if (s.status === "waiting_approval") {
    statusDotHtml = `
      <span class="relative flex h-2 w-2" title="等待审批" aria-label="等待审批">
        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
        <span class="relative inline-flex rounded-full h-2 w-2 bg-amber-400"></span>
      </span>
    `;
  } else if (s.status === "running" || isWorking) {
    statusDotHtml = `
      <span class="relative flex h-2 w-2" title="运行中" aria-label="运行中">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse glow-emerald"></span>
      </span>
    `;
  } else if (isUnread) {
    statusDotHtml = `
      <span class="relative flex h-2 w-2 items-center justify-center" title="运行完成（未查看）" aria-label="未读完成">
        <span class="w-2 h-2 rounded-full bg-sky-400 glow-sky"></span>
      </span>
    `;
  } else if (s.status === "desktop_active") {
    statusDotHtml = `
      <span class="relative flex h-2 w-2" title="桌面端活跃" aria-label="桌面端活跃">
        <span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse glow-cyan"></span>
      </span>
    `;
  }

  // 前缀微标：若在置顶等区域且属于工程项目，开头直接展示项目名称微标（不显示 agent 类型），节省手机窄屏空间；临时会话保持显示 agent 类型
  const hasProjectBadge = showProjectTag && Boolean(s.is_project && s.project_name);
  const leadingBadgeHtml = hasProjectBadge
    ? `<span class="px-1.5 py-0.5 text-[9px] font-mono font-medium text-sky-300 bg-sky-950/40 rounded border border-sky-500/30 shrink-0 max-w-[84px] truncate" title="${escapeHtml(
        s.project_name
      )}">${escapeHtml(s.project_name)}</span>`
    : `<span class="px-1 py-0.5 text-[9px] font-semibold rounded border shrink-0 ${meta.badgeClass}">
        ${meta.short}
      </span>`;

  return `
    <div data-session-item="${escapeHtml(s.unified_id)}" onclick="window.appActions.selectSession('${escapeHtml(s.unified_id)}')"
      class="group cursor-pointer px-2.5 py-2 rounded-lg border transition-all flex items-center justify-between gap-2 ${
        isSelected
          ? "bg-zinc-800/95 border-sky-500/50 text-zinc-100"
          : isUnread
          ? "bg-sky-950/20 border-sky-500/30 hover:bg-sky-950/30 text-zinc-200"
          : "bg-zinc-900/45 border-transparent hover:bg-zinc-900 hover:border-zinc-800 text-zinc-300"
      }">
      <div class="flex items-center gap-1.5 min-w-0 flex-1">
        ${leadingBadgeHtml}
        <span class="text-xs truncate font-medium ${isUnread ? "text-sky-200" : ""}">${escapeHtml(cleanFriendlyTitle(s.title))}</span>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        ${
          statusDotHtml
            ? `<span class="flex items-center justify-center min-w-[24px] py-0.5">${statusDotHtml}</span>`
            : `<span class="text-[10px] text-zinc-500 tabular-nums">${formatRelativeTime(s.updated_at)}</span>`
        }
        <button onclick="window.appActions.togglePin('${escapeHtml(s.unified_id)}', ${s.is_pinned ? 1 : 0}, event)"
          class="p-1 rounded hover:bg-zinc-800 text-xs transition ${
            s.is_pinned ? "text-amber-400 opacity-100" : "text-zinc-500 opacity-50 hover:opacity-100 md:opacity-0 md:group-hover:opacity-100"
          }" title="置顶" aria-label="置顶">
          ★
        </button>
      </div>
    </div>
  `;
}

export function sortSessions(list) {
  if (!Array.isArray(list)) return [];
  return [...list].sort((a, b) => {
    const swA = getSessionPriorityWeight(a);
    const swB = getSessionPriorityWeight(b);
    if (swA !== swB) return swB - swA;

    const pinA = Number(a.is_pinned || 0);
    const pinB = Number(b.is_pinned || 0);
    if (pinA !== pinB) return pinB - pinA;

    const ta = new Date(a.updated_at || 0).getTime();
    const tb = new Date(b.updated_at || 0).getTime();
    return tb - ta;
  });
}

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

  // 校验当前选中的模式是否受该 Agent 支持；若不支持，安全回退至 ask（审批）
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
  const activeId = sess?.model_name || (defaultModelItem ? defaultModelItem.id : "");
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
              <!-- 左侧：选中圆点 + 模型名称 + 标签 + 推荐星 -->
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

              <!-- 右侧：模型右侧直接显示进度条与百分比数值 -->
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


export function updatePromptInputPlaceholder(sess) {
  const inputEl = document.getElementById("prompt-input");
  const overlayEl = document.getElementById("prompt-input-placeholder");
  if (!inputEl) return;
  if (!sess) {
    if (overlayEl) {
      overlayEl.innerHTML = `<span>发消息...</span>`;
    }
    inputEl.placeholder = " ";
    return;
  }
  const agentName = getAgentDisplayName(sess.agent_type);
  if (overlayEl) {
    overlayEl.innerHTML = `
      <span>发消息给&nbsp;</span><span class="text-[10px] sm:text-xs text-zinc-400 font-mono">${escapeHtml(agentName)}</span><span>...</span>
    `;
  }
  inputEl.placeholder = `发消息给 ${agentName}...`;
}

export function renderCurrentSessionHeader() {
  const titleEl = document.getElementById("current-session-title");
  const subtitleEl = document.getElementById("current-session-subtitle");
  const modeSelect = document.getElementById("execution-mode-select");
  const headerLogo = document.getElementById("header-logo-icon");

  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  if (!sess) {
    if (titleEl) {
      titleEl.innerHTML = `<span class="text-xs sm:text-sm font-medium text-zinc-400 truncate">未选择对话</span>`;
    }
    if (subtitleEl) subtitleEl.textContent = "";
    if (headerLogo) headerLogo.classList.remove("has-session-hidden-mobile");
    updatePromptInputPlaceholder(null);
    renderModelPicker();
    updatePromptActionBtnState();
    return;
  }

  // 手机端已选择具体会话后，隐藏顶栏会话名称前的系统钻石图标以释放横向排版空间
  if (headerLogo) {
    headerLogo.classList.add("has-session-hidden-mobile");
  }

  const meta = AGENT_META[sess.agent_type] || AGENT_META.claude_code;

  // 获取项目名称（非工程会话回退至会话 ID 短码）
  const rawProjectName =
    sess.is_project && sess.project_name
      ? sess.project_name
      : (sess.project_name || (sess.project_path ? getProjectInfo(sess.project_path).name : ""));
  const projectName = String(rawProjectName || "").trim();

  // 桌面网页端项目展示（提升对比度与悬停反馈）
  const desktopProjectHtml = projectName
    ? `<span class="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-zinc-100 font-medium font-mono shrink-0 max-w-[200px] truncate transition-colors" title="${escapeHtml(projectName)}">
        <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path></svg>
        <span class="truncate">${escapeHtml(projectName)}</span>
      </span>`
    : `<span class="inline-flex items-center text-xs text-zinc-400 font-mono shrink-0" title="${escapeHtml(sess.raw_session_id || "")}">#${escapeHtml((sess.raw_session_id || "").slice(0, 8))}</span>`;

  // 手机移动端项目展示（会话名称 · 项目名称）
  const mobileProjectHtml = projectName
    ? `<span class="text-zinc-500 text-[11px] shrink-0 select-none">·</span>
       <span class="text-[11px] text-zinc-400 font-mono shrink-0 max-w-[100px] truncate" title="${escapeHtml(projectName)}">${escapeHtml(projectName)}</span>`
    : (sess.raw_session_id
        ? `<span class="text-zinc-500 text-[11px] shrink-0 select-none">·</span>
           <span class="text-[11px] text-zinc-500 font-mono shrink-0">#${escapeHtml((sess.raw_session_id || "").slice(0, 8))}</span>`
        : "");

  // 网页端：项目名称 · 会话名称
  // 手机端：只显示 会话名称 · 项目名称
  if (titleEl) {
    titleEl.innerHTML = `
      <!-- 网页端（sm及以上显示）：项目名称 · 会话名称 -->
      <div class="hidden sm:flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
        ${desktopProjectHtml}
        <span class="text-zinc-500 text-xs shrink-0 select-none">·</span>
        <span class="truncate font-medium text-sm text-zinc-100 min-w-0" title="${escapeHtml(cleanFriendlyTitle(sess.title))}">${escapeHtml(cleanFriendlyTitle(sess.title))}</span>
      </div>

      <!-- 手机端（sm以下显示）：会话名称 · 项目名称 -->
      <div class="flex sm:hidden items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
        <span class="truncate font-medium text-xs text-zinc-100 min-w-0 shrink" title="${escapeHtml(cleanFriendlyTitle(sess.title))}">${escapeHtml(cleanFriendlyTitle(sess.title))}</span>
        ${mobileProjectHtml}
      </div>
    `;
  }
  if (subtitleEl) {
    subtitleEl.textContent = "";
  }
  updatePromptInputPlaceholder(sess);
  if (modeSelect) {
    const activeMode = renderModeOptionsForAgent(modeSelect, sess.agent_type, sess.execution_mode || "ask", true);
    // 若原存储的模式不受该 Agent 支持且发生了自动回退，静默同步更新后端元数据持久化
    if (sess.execution_mode && sess.execution_mode !== activeMode) {
      sess.execution_mode = activeMode;
      apiFetch(`/api/v1/sessions/${encodeURIComponent(sess.unified_id)}/meta`, {
        method: "PATCH",
        body: JSON.stringify({ execution_mode: activeMode }),
      }).catch((err) => console.warn("静默同步纠偏模式失败:", err));
    }
  }
  renderModelPicker();
  updatePromptActionBtnState();

  return `<div class="md-prose">${out.join("")}</div>`;
}

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

  }, duration);
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


 * 2. 主界面仅直接展示用户的提问与 Agent 该轮最终完整的核心回复正文。
 * 3. 自动识别本轮新建/更新或引用的 .md 文档（如 implementation_plan.md 等），生成「📄 点击阅读文档」入口卡片。
 */
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

export function renderTurnProcessBar(turn) {
  const {
    turnKey,
    processRecords,
    thinkingCount,
    toolCount,
    editedFiles,
    hasHighRisk,
  } = turn;

  if (!processRecords || processRecords.length === 0) return "";
  state.turnsMap[turnKey] = turn;

  const isExpanded = !!state.expandedTurns[turnKey] || !!state.turnThinkingExpanded[turnKey];
  const totalActions =
    toolCount + thinkingCount + processRecords.filter((r) => r.intermediateNote).length;

  // 构建折叠栏上的精炼徽章
  const badges = [];
  if (hasHighRisk) {
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/35 text-[10px] font-semibold shrink-0 flex items-center gap-1">
        <svg class="w-3 h-3 text-rose-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>高危</span>
      </span>`
    );
  }
  if (editedFiles.length > 0) {
    const fileHint =
      editedFiles.length === 1
        ? `改动 ${editedFiles[0]}`
        : `改动 ${editedFiles.length} 文件`;
    badges.push(
      `<span class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[10px] font-medium shrink-0 flex items-center gap-1">
        <svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
        <span class="truncate max-w-[105px] sm:max-w-[180px]">${escapeHtml(fileHint)}</span>
      </span>`
    );
  }
  if (toolCount > 0) {
    badges.push(
      `<span class="hidden sm:inline-flex text-[11px] text-zinc-400 items-center gap-1 shrink-0">
        <svg class="w-3 h-3 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
        <span>${toolCount} 次调用</span>
      </span>`
    );
  }
  if (thinkingCount > 0) {
    badges.push(
      `<span class="hidden sm:inline-flex text-[11px] text-zinc-400 items-center gap-1 shrink-0">
        <svg class="w-3 h-3 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
        <span>${thinkingCount} 次思考</span>
      </span>`
    );
  }

  const detailsHtml = isExpanded
    ? `<div class="mt-2 pt-2 border-t border-zinc-800/80 space-y-2">
        ${processRecords
          .map((rec) => {
            const thinkDetailId = `${turnKey}_think_${rec.step_index}`;
            const isThinkExplicitlySet = typeof state.expandedDetails[thinkDetailId] === "boolean";
            const isThinkOpen = isThinkExplicitlySet
              ? Boolean(state.expandedDetails[thinkDetailId])
              : Boolean(state.turnThinkingExpanded[turnKey]);
            if (rec.thinking) {
              saveToBoundedStore("lazyDetailsStore", thinkDetailId, {
                type: "thinking",
                raw: rec.thinking,
              });
            }
            const displayThinkLen =
              rec.thinking_len || (rec.thinking ? rec.thinking.length : 0);
            const thinkHtml = rec.thinking
              ? `
                <details data-detail-id="${escapeHtml(thinkDetailId)}" ${
                  isThinkOpen ? "open" : ""
                } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                  thinkDetailId
                )}', '${escapeJsSingleQuote(turnKey)}')" class="rounded-lg bg-zinc-950/80 border border-zinc-800/80 px-2.5 py-1.5 text-[11px] text-zinc-400">
                  <summary onclick="window.appActions.onThinkSummaryClick(event, '${escapeJsSingleQuote(
                    turnKey
                  )}', '${escapeJsSingleQuote(thinkDetailId)}')" class="cursor-pointer select-none font-medium text-zinc-300 min-h-[40px] sm:min-h-[34px] flex items-center justify-between px-0.5">
                    <span class="flex items-center gap-1.5 min-w-0">
                      <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a8 8 0 0 0-8 8c0 3.2 1.9 6 4.7 7.3V19a2 2 0 0 0 2 2h2.6a2 2 0 0 0 2-2v-1.7c2.8-1.3 4.7-4.1 4.7-7.3a8 8 0 0 0-8-8z"/><path d="M10 22h4"/></svg>
                      <span class="truncate">思考过程 (${displayThinkLen}字)</span>
                    </span>
                    <button type="button" onclick="window.appActions.copyThinkingText('${escapeJsSingleQuote(
                      thinkDetailId
                    )}', this, event)" class="min-h-[40px] sm:min-h-[34px] min-w-[50px] px-2.5 py-1 rounded-md text-[11px] text-zinc-400 hover:text-sky-300 hover:bg-zinc-800/80 transition active:scale-95 flex items-center justify-center gap-1" title="复制思考内容">
                      <svg class="w-3 h-3 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                      <span>复制</span>
                    </button>
                  </summary>
                  <div data-lazy-slot class="mt-1.5 pt-1.5 border-t border-zinc-800/70 whitespace-pre-wrap leading-relaxed text-zinc-400 font-mono text-[11px] max-h-52 overflow-y-auto">${
                    isThinkOpen ? escapeHtml(rec.thinking) : ""
                  }</div>
                </details>
              `
              : "";

            const noteHtml = rec.intermediateNote
              ? `
                <div class="px-2.5 py-1.5 rounded-lg bg-zinc-900/70 border border-zinc-800/60 text-xs text-zinc-300">
                  <span class="text-[10px] text-sky-400 font-semibold mr-1 inline-flex items-center gap-0.5">
                    <svg class="w-3 h-3 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <span>过程说明:</span>
                  </span>
                  ${escapeHtml(rec.intermediateNote)}
                </div>
              `
              : "";

            const allTools = rec.tool_calls || [];
            const readTools = [];
            const actionTools = [];
            allTools.forEach((tc) => {
              if (isReadOnlyTool(tc)) {
                readTools.push(tc);
              } else {
                actionTools.push(tc);
              }
            });

            // 1. 只读探索类工具（如 read_file、grep）：紧凑微型胶囊流，不占纵向屏幕
            const readChipsHtml =
              readTools.length > 0
                ? `
                  <div class="read-tool-chips-row">
                    <span class="text-[10px] text-zinc-500 font-medium mr-1 select-none flex items-center gap-1 shrink-0">
                      <svg class="w-3 h-3 text-zinc-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>
                      查阅 (${readTools.length}):
                    </span>
                    ${readTools
                      .map((tc) => {
                        const target = getBasename(tc.file_path || tc.args_summary) || tc.args_summary;
                        return `
                          <span class="read-tool-chip" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                            <svg class="w-3 h-3 text-zinc-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                            <span class="truncate max-w-[150px]">${escapeHtml(target)}</span>
                          </span>
                        `;
                      })
                      .join("")}
                  </div>
                `
                : "";

            // 2. 变更类（edit_file/write_file）与执行类工具：高亮呈现
            const actionToolsHtml = actionTools
              .map((tc, tcIdx) => {
                const isHigh = tc.risk_level === "high";
                const isEditTool = !!tc.diff_text || /edit|write|replace|create/i.test(tc.name || "");
                const diffDetailId = `${turnKey}_diff_${rec.step_index}_${tcIdx}`;
                // 用户要求：思考过程里的 edit_file 默认不展开
                const isDiffOpen = Boolean(state.expandedDetails[diffDetailId]);
                if (tc.diff_text) {
                  saveToBoundedStore("lazyDetailsStore", diffDetailId, {
                    type: "diff",
                    raw: tc.diff_text,
                    filePath: tc.file_path || tc.args_summary || "",
                    addCount: tc.diff_add_count,
                    delCount: tc.diff_del_count,
                  });
                }

                const outDetailId = `${turnKey}_out_${rec.step_index}_${tcIdx}`;
                const isOutOpen = !!state.expandedDetails[outDetailId];
                if (tc.output) {
                  saveToBoundedStore("lazyDetailsStore", outDetailId, {
                    type: "output",
                    raw: tc.output,
                  });
                }

                const cardClass = isEditTool
                  ? "edit-tool-card"
                  : `rounded-lg bg-zinc-950/90 border ${
                      isHigh ? "border-rose-500/45" : "border-zinc-800/90"
                    } p-2 text-xs`;

                const badgeBg = isEditTool
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "bg-zinc-800 text-sky-300";
                const badgeSvg = isEditTool
                  ? `<svg class="w-3 h-3 text-emerald-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`
                  : `<svg class="w-3 h-3 text-sky-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`;
                const targetText = isEditTool
                  ? getBasename(tc.file_path || tc.args_summary) || tc.args_summary
                  : tc.args_summary;

                return `
                  <div class="${cardClass}">
                    <div class="flex items-center justify-between gap-2">
                      <div class="flex items-center gap-1.5 min-w-0">
                        <span class="px-1.5 py-0.5 rounded ${badgeBg} font-mono font-semibold text-[10px] flex items-center gap-1 shrink-0">
                          ${badgeSvg}
                          <span>${escapeHtml(tc.name)}</span>
                        </span>
                        <span class="font-mono text-[11px] ${
                          isEditTool ? "text-zinc-100 font-semibold" : "text-zinc-300"
                        } truncate" title="${escapeHtml(tc.file_path || tc.args_summary)}">
                          ${escapeHtml(targetText)}
                        </span>
                      </div>
                      <span class="text-[9px] text-zinc-500 font-mono shrink-0">${escapeHtml(
                        tc.status || "DONE"
                      )}</span>
                    </div>
                    ${
                      tc.diff_text
                        ? `
                          <details data-detail-id="${escapeHtml(diffDetailId)}" ${
                            isDiffOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            diffDetailId
                          )}')" class="mt-1.5 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer select-none font-medium flex items-center justify-between text-emerald-300/90 hover:text-emerald-200 min-h-[40px] sm:min-h-[34px] py-1 px-1 rounded hover:bg-emerald-500/10 transition">
                              <span class="flex items-center gap-1.5 min-w-0">
                                <svg class="w-3.5 h-3.5 text-emerald-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                <span class="truncate">代码变更 Diff</span>
                              </span>
                              <div class="flex items-center gap-1.5 font-mono text-[9px] shrink-0">
                                ${
                                  typeof tc.diff_add_count === "number"
                                    ? `<span class="diff-stat-badge diff-stat-add">+${tc.diff_add_count}</span>`
                                    : ""
                                }
                                ${
                                  typeof tc.diff_del_count === "number"
                                    ? `<span class="diff-stat-badge diff-stat-del">-${tc.diff_del_count}</span>`
                                    : ""
                                }
                                <span class="text-zinc-500 font-sans ml-0.5">
                                  <span class="toggle-arrow-open">收起 ▴</span>
                                  <span class="toggle-arrow-closed">展开 ▾</span>
                                </span>
                              </div>
                            </summary>
                            <div data-lazy-slot class="mt-1">${
                              isDiffOpen
                                ? renderUnifiedDiffHtml(tc.diff_text, tc.file_path || tc.args_summary, {
                                    addCount: tc.diff_add_count,
                                    delCount: tc.diff_del_count,
                                    diffId: diffDetailId,
                                  })
                                : ""
                            }</div>
                          </details>
                        `
                        : ""
                    }
                    ${
                      tc.output
                        ? `
                          <details data-detail-id="${escapeHtml(outDetailId)}" ${
                            isOutOpen ? "open" : ""
                          } ontoggle="window.appActions.onDetailToggle(this, '${escapeJsSingleQuote(
                            outDetailId
                          )}')" class="mt-1.5 text-[10px] text-zinc-400">
                            <summary class="cursor-pointer text-zinc-400 hover:text-zinc-200 select-none min-h-[40px] sm:min-h-[34px] flex items-center justify-between py-1 px-1 rounded hover:bg-zinc-800/40 transition">
                              <span class="flex items-center gap-1.5 min-w-0">
                                <svg class="w-3.5 h-3.5 text-zinc-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                                <span class="truncate">查看执行输出 ${
                                  tc.is_truncated ? `(${tc.full_output_len}字·已截断)` : ""
                                }</span>
                              </span>
                              ${
                                tc.is_truncated
                                  ? `<button onclick="window.appActions.loadFullOutput('${escapeHtml(
                                      state.selectedSessionId
                                    )}', ${rec.step_index}, this, event)" class="min-h-[40px] sm:min-h-[28px] px-2.5 py-1 rounded-md bg-sky-500/20 text-sky-300 border border-sky-500/30 hover:bg-sky-500/30 text-[10px] shrink-0">加载完整输出</button>`
                                  : ""
                              }
                            </summary>
                            <pre data-lazy-slot class="mt-1 p-2 rounded bg-black/70 overflow-x-auto font-mono text-[10px] text-zinc-300 whitespace-pre-wrap break-all max-h-44 overflow-y-auto">${
                              isOutOpen ? escapeHtml(tc.output) : ""
                            }</pre>
                          </details>
                        `
                        : ""
                    }
                  </div>
                `;
              })
              .join("");

            return `<div class="space-y-1.5">${thinkHtml}${noteHtml}${readChipsHtml}${actionToolsHtml}</div>`;
          })
          .join("")}
        <div class="pt-2 text-center border-t border-zinc-800/60">
          <button type="button" onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
            class="min-h-[40px] px-3.5 py-2 rounded-lg bg-zinc-950/80 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-400 hover:text-sky-300 font-medium transition active:scale-95 inline-flex items-center gap-1">
            <svg class="w-3.5 h-3.5 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 15l-6-6-6 6"/></svg>
            <span>收起已展开步骤</span>
          </button>
        </div>
      </div>`
    : "";

  return `
    <div id="turn-process-wrap-${escapeHtml(turnKey)}" class="mb-2 rounded-xl bg-zinc-900/50 hover:bg-zinc-900/70 border border-zinc-800/80 px-2.5 sm:px-3 py-1 transition-colors">
      <div onclick="window.appActions.toggleTurnSteps('${escapeJsSingleQuote(turnKey)}')"
        class="cursor-pointer select-none min-h-[40px] flex items-center justify-between gap-1.5 sm:gap-2 active:opacity-75">
        <div class="flex items-center gap-1.5 min-w-0 flex-1 overflow-hidden">
          <span class="text-xs font-semibold text-zinc-200 flex items-center gap-1.5 shrink-0">
            <svg class="w-3.5 h-3.5 text-sky-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            <span class="whitespace-nowrap">${totalActions} 步操作</span>
          </span>
          <div class="flex items-center gap-1 min-w-0 overflow-hidden">
            ${badges.join("")}
          </div>
        </div>
        <div class="text-[11px] text-sky-400 hover:text-sky-300 font-medium shrink-0 px-2 py-1 -mr-1 rounded-md hover:bg-zinc-800/50 transition flex items-center gap-0.5">
          <span>${isExpanded ? "收起 ▴" : "展开 ▾"}</span>
        </div>
      </div>
      ${detailsHtml}
    </div>
  `;
}

export function renderQuestionOptionsHtml(turnKey, qi, q) {
  const qKey = `${turnKey}_q${qi}`;
  const selectedOpts = state.questionSelections[qKey] || [];
  const isMulti = !!q.is_multi_select;
  return (q.options || [])
    .map((opt) => {
      const isSelected = selectedOpts.includes(opt);
      return `
        <button type="button"
          onclick="window.appActions.toggleQuestionOption('${escapeJsSingleQuote(
            turnKey
          )}', ${qi}, '${escapeJsSingleQuote(opt)}', ${isMulti})"
          class="text-left text-xs px-2.5 py-1.5 rounded-lg border transition-all active:scale-95 flex items-center gap-1.5 ${
            isSelected
              ? "bg-sky-500 text-zinc-950 font-bold border-sky-400 shadow-sm"
              : "bg-zinc-900/80 hover:bg-zinc-800 text-zinc-200 border-zinc-700/80"
          }">
          <span class="text-[10px] ${isSelected ? "text-zinc-950" : "text-sky-400"}">${
        isSelected ? (isMulti ? "☑" : "●") : isMulti ? "☐" : "○"
      }</span>
          <span class="break-words">${escapeHtml(opt)}</span>
        </button>
      `;
    })
    .join("");
}

export function renderInteractiveQuestionsCard(turn, isLatestTurn) {
  const questions = turn.questions || [];
  if (questions.length === 0) return "";
  const turnKey = turn.turnKey;

  // 历史轮次：转为轻量折叠卡片，展示已确认决议与选项，防止侵占手机长流
  if (!isLatestTurn) {
    const decision = state.persistedQuestionDecisions?.[turnKey];
    const summaryTitle = decision
      ? `✓ 已决策：${escapeHtml(decision.answerText.slice(0, 32))}${
          decision.answerText.length > 32 ? "..." : ""
        }`
      : `✓ 已完成的交互问答 (共 ${questions.length} 项)`;

    return `
      <details class="mt-2 rounded-xl bg-zinc-950/70 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-400">
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
    <div class="mt-2.5 rounded-xl bg-zinc-900/90 border border-sky-500/40 p-3 shadow-lg space-y-2">
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

export function renderMdDocQuickCards(mdFiles) {
  if (!mdFiles || mdFiles.length === 0) return "";
  return `
    <div class="mt-2.5 flex flex-wrap gap-1.5">
      ${mdFiles
        .map(
          (doc) => `
            <button type="button"
              onclick="window.appActions.openFilePreview('${escapeJsSingleQuote(doc.path)}')"
              class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-sky-500/12 hover:bg-sky-500/20 border border-sky-500/35 text-left transition active:scale-95">
              <span class="text-base">📄</span>
              <div class="min-w-0">
                <div class="text-xs font-semibold text-sky-300 truncate">${escapeHtml(doc.name)}</div>
                <div class="text-[10px] text-zinc-400">点击全屏阅读 Markdown 文档</div>
              </div>
            </button>
          `
        )
        .join("")}
    </div>
  `;
}

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

  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

export function updatePromptActionBtnState() {
  const btn = document.getElementById("prompt-action-btn");
  const btnText = document.getElementById("prompt-action-btn-text");
  const inputEl = document.getElementById("prompt-input");
  const overlayEl = document.getElementById("prompt-input-placeholder");
  if (!btn) return;

  const isWorking = isSessionWorking(state.selectedSessionId);
  const hasText = Boolean(inputEl && inputEl.value.trim().length > 0);

  if (overlayEl) {
    if (hasText) {
      overlayEl.classList.add("!hidden");
    } else {
      overlayEl.classList.remove("!hidden");
    }
  }

  // 运行中的会话：如果无输入内容显示「中断」，如果有输入内容显示「发送」（跟客户端交互完全一致）
  if (isWorking && !hasText) {
    btn.dataset.action = "abort";
    btn.className =
      "shrink-0 h-10 px-3.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs sm:text-sm shadow active:scale-95 transition flex items-center justify-center m-0";
    if (btnText) {
      btnText.textContent = "中断";
    }
    btn.setAttribute("title", "中断当前执行 (SIGINT)");
  } else {
    btn.dataset.action = "send";
    btn.className =
      "shrink-0 h-10 px-3.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs sm:text-sm shadow active:scale-95 transition flex items-center justify-center m-0";
    if (btnText) {
      btnText.textContent = "发送";
    }
    btn.setAttribute("title", "发送指令");
  }
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

let liveStreamRaf = null;

export function renderLiveStreamOverlay() {
  if (liveStreamRaf) return;
  liveStreamRaf = requestAnimationFrame(() => {
    liveStreamRaf = null;
    _doRenderLiveStreamOverlay();
  });
}

export function _doRenderLiveStreamOverlay() {
  const slot = document.getElementById("live-stream-overlay-slot");
  updatePromptActionBtnState();
  if (!slot || !state.selectedSessionId) return;

  const isWorking = isSessionWorking(state.selectedSessionId);
  const liveText = state.streamingBuffer[state.selectedSessionId];

  if (!isWorking && !liveText) {
    if (slot.innerHTML !== "") slot.innerHTML = "";
    return;
  }

  // Agent 工作中，在对话区域显示 Working... 与实时生成流（RAF 帧对齐渲染）
  const streamHtml = liveText
    ? `<div class="mt-2 text-xs text-zinc-200 border-t border-zinc-800/60 pt-2">${renderMarkdownHtml(
        liveText.slice(-3000),
        "live_stream"
      )}</div>`
    : "";

  slot.innerHTML = `
    <div class="mb-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 px-3 py-2 text-xs text-zinc-300 shadow-sm transition-all">
      <div class="flex items-center gap-2 font-mono text-[11px] text-sky-400">
        <span class="relative flex h-2 w-2">
          <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75"></span>
          <span class="relative inline-flex rounded-full h-2 w-2 bg-sky-400"></span>
        </span>
        <span class="tracking-wider font-semibold">Working...</span>
      </div>
      ${streamHtml}
    </div>
  `;

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

export function renderApprovalBanners() {
  const topBanner = document.getElementById("global-approval-banner");
  const bottomBar = document.getElementById("bottom-approval-bar");
  if (!topBanner || !bottomBar) return;

  const list = state.pendingApprovals || [];
  if (list.length === 0) {
    topBanner.classList.add("hidden");
    bottomBar.classList.add("hidden");
    if (window.appActions?.updateBottomControlHeight) {
      window.appActions.updateBottomControlHeight();
    } else if (window.updateBottomControlHeight) {
      window.updateBottomControlHeight();
    }
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
  if (window.appActions?.updateBottomControlHeight) {
    window.appActions.updateBottomControlHeight();
  } else if (window.updateBottomControlHeight) {
    window.updateBottomControlHeight();
  }
}


let qrExpireTimer = null;

export function startQrExpireCountdown() {
  if (qrExpireTimer) {
    clearTimeout(qrExpireTimer);
    qrExpireTimer = null;
  }
  const expAtSec = Number(state.systemStatus?.pair_code_expires_at || 0);
  const nowSec = Date.now() / 1000;
  if (expAtSec > 0 && expAtSec <= nowSec) {
    state.qrExpired = true;
    renderQrModalContent();
    return;
  }
  state.qrExpired = false;
  state.qrGeneratedAt = Date.now();
  const remainMs = expAtSec > 0 ? Math.max(1000, Math.floor((expAtSec - nowSec) * 1000)) : 600 * 1000;
  qrExpireTimer = setTimeout(() => {
    state.qrExpired = true;
    renderQrModalContent();
  }, remainMs);
}

export function renderQrModalContent() {
  const box = document.getElementById("qr-modal-body");
  if (!box || !state.systemStatus) return;

  // 1. 若刚刚接收到设备配对成功的实时广播，展示精致的双向握手成功卡片
  if (state.pairedSuccessNotice) {
    const dev = state.pairedSuccessNotice.device_name || "移动设备";
    const ip = state.pairedSuccessNotice.client_ip || "";
    box.innerHTML = `
      <div class="py-6 px-4 flex flex-col items-center justify-center text-center space-y-3.5 animate-fadeIn">
        <div class="w-14 h-14 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shadow-[0_0_24px_rgba(16,185,129,0.25)]">
          <svg class="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
        <div class="space-y-1">
          <div class="text-base font-bold text-zinc-100">手机已成功连接！</div>
          <div class="text-xs text-zinc-400 font-mono flex items-center justify-center gap-1.5">
            <span>${escapeHtml(dev)}</span>
            ${ip ? `<span class="text-zinc-600">·</span><span>${escapeHtml(ip)}</span>` : ""}
          </div>
        </div>
        <div class="pt-2 text-[11px] text-zinc-500 flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
          <span>窗口即将自动关闭...</span>
        </div>
      </div>
    `;
    return;
  }

  const { pair_url, lan_pair_url, zt_pair_url, channels } = state.systemStatus;
  const orderWeight = { wifi: 1, tailscale: 2, zerotier: 3 };
  const channelList = (channels || []).slice().sort((a, b) => (orderWeight[a.type] || 9) - (orderWeight[b.type] || 9));

  if (!state.selectedQrNet) {
    // 默认绝对优先展示 Tailscale（支持随时随地全网随身漫游），其次 ZeroTier，严禁在有 VPN 时优先展示 Wi-Fi 直连
    const hasTs = channelList.some((c) => c.type === "tailscale");
    const hasZt = channelList.some((c) => c.type === "zerotier");
    if (hasTs) {
      state.selectedQrNet = "tailscale";
    } else if (hasZt) {
      state.selectedQrNet = "zerotier";
    } else {
      state.selectedQrNet = channelList[0]?.type || "wifi";
    }
  }

  const curChannel = channelList.find((c) => c.type === state.selectedQrNet) || channelList[0] || {};
  const activeUrl = curChannel.pair_url || curChannel.url || pair_url || "";
  const tokenParam = encodeURIComponent(state.token || "");
  const qrImgUrl = `/api/v1/auth/qr.svg?net=${encodeURIComponent(state.selectedQrNet)}&token=${tokenParam}&_t=${state.qrGeneratedAt || 0}`;

  const channelTips = {
    tailscale: "全网随身漫游 · 手机开启 Tailscale 随时秒连",
    zerotier: "虚拟隧道直连 · 手机开启 ZeroTier 隧道直连",
    wifi: "局域网免密直连 · 仅限当前同一 Wi-Fi 连接",
  };

  const channelIcons = {
    tailscale: `<svg class="w-3.5 h-3.5 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>`,
    zerotier: `<svg class="w-3.5 h-3.5 text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>`,
    wifi: `<svg class="w-3.5 h-3.5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"></path><path d="M1.42 9a16 16 0 0 1 21.16 0"></path><path d="M8.53 16.11a6 6 0 0 1 6.95 0"></path><line x1="12" y1="20" x2="12.01" y2="20"></line></svg>`,
  };

  const gridColsClass =
    channelList.length === 1
      ? "grid-cols-1"
      : channelList.length === 2
      ? "grid-cols-2"
      : "grid-cols-3";

  box.innerHTML = `
    <div class="space-y-3">
      <!-- 极简通道切换胶囊 (Tailscale 默认居中) -->
      <div class="grid ${gridColsClass} gap-1.5 w-full">
        ${channelList
          .map((c) => {
            const isSelected = c.type === state.selectedQrNet;
            return `
              <button type="button" onclick="window.appActions.switchQrChannel('${c.type}')"
                class="py-1.5 px-2 rounded-xl text-[11px] font-medium transition cursor-pointer select-none flex items-center justify-center gap-1.5 active:scale-95 ${
                  isSelected
                    ? "bg-sky-500/20 text-sky-300 border border-sky-500/50 shadow-[0_0_12px_rgba(56,189,248,0.15)] font-semibold"
                    : "bg-zinc-950/70 hover:bg-zinc-800/80 text-zinc-400 hover:text-zinc-200 border border-zinc-800"
                }">
                ${channelIcons[c.type] || ""}
                <span>${escapeHtml(c.label)}</span>
              </button>
            `;
          })
          .join("")}
      </div>

      <!-- 二维码专属卡片 (带 10 分钟过期遮罩防护) -->
      <div class="relative flex flex-col items-center bg-zinc-950/90 border border-zinc-800/80 rounded-2xl p-4 shadow-xl">
        <div class="relative bg-white p-3 rounded-2xl shadow-md">
          <img src="${qrImgUrl}" alt="AgentDeck Pair QR" class="w-44 h-44 block select-none" loading="eager" />
          
          ${
            state.qrExpired
              ? `
                <div class="absolute inset-0 bg-zinc-950/90 backdrop-blur-xs rounded-2xl flex flex-col items-center justify-center p-3 text-center space-y-2.5 animate-fadeIn">
                  <div class="w-8 h-8 rounded-full bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                  </div>
                  <div class="text-[11px] text-zinc-300 font-medium">二维码已过期 (10分钟时限)</div>
                  <button onclick="window.appActions.refreshPairCodeAndQr()"
                    class="px-3 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 font-bold text-xs shadow-md transition active:scale-95 cursor-pointer flex items-center gap-1.5">
                    <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                    <span>点击立即刷新</span>
                  </button>
                </div>
              `
              : ""
          }
        </div>

        <!-- 单行不换行主直连地址 -->
        <div class="w-full mt-3 px-2.5 py-1 rounded-xl bg-zinc-900 border border-zinc-800 flex items-center justify-between gap-1.5">
          <div class="text-[11px] font-mono text-sky-400 whitespace-nowrap overflow-hidden select-all flex-1 py-0.5 tracking-tight" title="${escapeHtml(activeUrl)}">
            ${escapeHtml(activeUrl)}
          </div>
          <button onclick="window.appActions.copyQrUrl('${escapeJsSingleQuote(activeUrl)}')"
            class="text-[11px] text-zinc-300 hover:text-white px-2.5 py-1 rounded-lg bg-zinc-800 hover:bg-zinc-700 shrink-0 transition active:scale-95 cursor-pointer font-medium"
            title="复制直连链接">复制</button>
        </div>

        <!-- 精炼通道场景提示 -->
        <div class="w-full mt-2.5 pt-2.5 border-t border-zinc-800/80 text-[11px] text-center text-zinc-400 flex items-center justify-center gap-1.5">

export function formatTokenCount(n) {
  const num = Number(n) || 0;
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  }
  return String(num);
}

export function renderTokenUsage() {
  const pill = document.getElementById("token-usage-pill");
  if (!pill) return;
  const usage = state.currentTokenUsage;
  if (!usage || (!usage.context_tokens && !usage.total_tokens)) {
    pill.classList.add("hidden");
    pill.classList.remove("flex");
    return;
  }

  pill.classList.remove("hidden");
  pill.classList.add("flex");

  const ctx = usage.context_tokens || 0;
  const limit = usage.context_limit || 200000;
  const pct = typeof usage.context_percent === "number" ? usage.context_percent : Math.round((ctx / limit) * 1000) / 10;

  const txtEl = document.getElementById("token-usage-text");
  const pctEl = document.getElementById("token-usage-pct");
  const dotEl = document.getElementById("token-usage-dot");

  if (txtEl) txtEl.textContent = `${formatTokenCount(ctx)} / ${formatTokenCount(limit)}`;
  if (pctEl) {
    pctEl.textContent = `${pct}%`;
    if (pct < 60) {
      pctEl.className = "text-[9px] px-1 py-0.5 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30";

      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-sky-400";
    } else if (pct < 85) {
      pctEl.className = "text-[9px] px-1 py-0.5 rounded font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-amber-400";
    } else {
      pctEl.className = "text-[9px] px-1 py-0.5 rounded font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/40 animate-pulse";
      if (dotEl) dotEl.className = "w-1.5 h-1.5 rounded-full bg-rose-500 animate-ping";
    }
  }
}

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


export function gitPathToSlotId(relPath) {
  let hash = 0;
  const str = String(relPath || "");
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return "git_diff_slot_" + (hash >>> 0).toString(16);
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

window.highlightSlashItem = highlightSlashItem;
