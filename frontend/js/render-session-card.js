import {
  getPendingAttentionSessions,
  isSessionStatusActive,
} from "./session-helper.js";
/**
 * AgentDeck 侧边栏单条会话卡片与排序器
 */

import { AGENT_META } from "./config.js";
import { state, isSessionCompletedUnread } from "./state.js";
import {
  escapeHtml,
  cleanFriendlyText,
  formatRelativeTime,
} from "./utils.js";
import {
  cleanFriendlyTitle,
  isSessionWorking,
  getSessionPriorityWeight,
  getProjectInfo,
} from "./session-helper.js";

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

  // 前缀微标呈现规则：
  // 1. 若为工程项目会话且处于置顶等显示区域：开头只显示项目名称微标，不显示 agent 类型，节省手机窄屏单行宽度；
  // 2. 临时会话：保持不变，显示 agent 类型微标；
  // 3. 项目内部展开会话：因外层已有项目大标题，内部仅显示 agent 类型微标。
  const projectName = s.project_name || (s.project_path ? getProjectInfo(s.project_path)?.name : "");
  const isProjectSession = Boolean(s.is_project && projectName && projectName !== "未分类");
  const showProjectBadge = showProjectTag && isProjectSession;

  const leadingBadgeHtml = showProjectBadge
    ? `<span class="px-1.5 py-0.5 text-[9px] font-mono font-medium text-sky-300 bg-sky-950/40 rounded border border-sky-500/30 shrink-0 max-w-[84px] truncate" title="${escapeHtml(
        projectName
      )}">${escapeHtml(projectName)}</span>`
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

