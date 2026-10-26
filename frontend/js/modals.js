/**
 * AgentDeck 抽屉手势、滚动守护与模态框生命周期控制
 */

import { state } from "./state.js";
import { showToast, apiFetch } from "./api.js";
import {
  loadSessions,
  loadWorkspaces,
  selectSession,
  getProjectInfo,
  getAggregatedProjects,
} from "./data.js";
import { DEFAULT_MODELS_BY_AGENT, MODES_BY_AGENT } from "./config.js";
import { escapeHtml, escapeJsSingleQuote } from "./utils.js";
import { renderSessionList } from "./render-sidebar.js";
import { renderModelOptionsForAgent, renderModeOptionsForAgent } from "./render-views.js";
import { renderInteractiveDecisionModalHtml } from "./render-modal-views.js";
import { dispatchCommand } from "./command-queue.js";

export function bindSessionListScrollGuardian() {
  const container = document.getElementById("session-list-container");
  if (!container || container._guardianBound) return;
  container._guardianBound = true;
  let scrollTimeout = null;
  const onUserActivity = () => {
    state.isUserInteractingWithList = true;
    if (scrollTimeout) clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      state.isUserInteractingWithList = false;
    }, 1200);
  };
  container.addEventListener("scroll", onUserActivity, { passive: true });
  container.addEventListener("touchstart", onUserActivity, { passive: true });
}


export function updateDrawerVisibility() {
  const drawer = document.getElementById("sidebar-drawer");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (!drawer || !backdrop) return;
  if (state.drawerOpen) {
    drawer.classList.remove("-translate-x-full");
    backdrop.classList.remove("hidden");
  } else {
    drawer.classList.add("-translate-x-full");
    backdrop.classList.add("hidden");
  }

  // 通过 body 类名与纯 CSS 媒体查询解耦桌面端展开按钮的显示状态
  document.body.classList.toggle("sidebar-is-collapsed", !!state.sidebarCollapsed);
}

// 移动端向右滑动展开菜单手势支持（边缘手势 Edge Swipe，避免与正文长按选词拖动游标复制冲突）
export function initDrawerSwipeGestures() {
  const EDGE_SWIPE_MAX_X = 32; // 仅允许屏幕最左侧 32px 区域发起右滑展开抽屉（移动端标准边缘手势规范）
  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let isScrollingVertical = false;
  let isGestureLocked = false;
  let isIgnoredTarget = false;

  const hasActiveTextSelection = () => {
    const sel = window.getSelection();
    if (!sel) return false;
    if (!sel.isCollapsed) return true;
    return Boolean(sel.toString() && sel.toString().trim().length > 0);
  };

  const isModalOpen = () => {
    const modalIds = [
      "new-session-modal",
      "qr-modal",
      "doc-preview-modal",
      "git-status-modal",
      "token-usage-modal",
      "rename-modal",
    ];
    for (const id of modalIds) {
      const el = document.getElementById(id);
      if (el && !el.classList.contains("hidden")) return true;
    }
    return false;
  };

  const isHorizontalScrollable = (el) => {
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (
        tag === "input" ||
        tag === "textarea" ||
        tag === "select" ||
        cur.isContentEditable
      ) {
        return true;
      }
      if (
        cur.classList?.contains("overflow-x-auto") ||
        cur.classList?.contains("unified-diff-box") ||
        cur.classList?.contains("no-scrollbar")
      ) {
        if (cur.scrollWidth > cur.clientWidth + 5) {
          return true;
        }
      }
      const style = window.getComputedStyle(cur);
      if (
        (style.overflowX === "auto" || style.overflowX === "scroll") &&
        cur.scrollWidth > cur.clientWidth + 5
      ) {
        return true;
      }
      cur = cur.parentElement;
    }
    return false;
  };

  window.addEventListener(
    "touchstart",
    (e) => {
      if (window.innerWidth >= 768 || e.touches.length !== 1) {
        isIgnoredTarget = true;
        return;
      }

      if (isModalOpen()) {
        isIgnoredTarget = true;
        return;
      }

      const touch = e.touches[0];
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
      touchStartTime = Date.now();
      isScrollingVertical = false;
      isGestureLocked = false;

      // 1. 如果抽屉处于关闭状态，且触摸起始点不在屏幕最左侧边缘（> 32px），或者当前已有活动选区，
      //    直接忽略手势，避免干扰会话内的文本长按、拖动游标选词与复制操作
      if (!state.drawerOpen && (touchStartX > EDGE_SWIPE_MAX_X || hasActiveTextSelection())) {
        isIgnoredTarget = true;
        return;
      }

      if (isHorizontalScrollable(e.target)) {
        isIgnoredTarget = true;
      } else {
        isIgnoredTarget = false;
      }
    },
    { passive: true }
  );

  window.addEventListener(
    "touchmove",
    (e) => {
      if (isIgnoredTarget || e.touches.length !== 1) return;

      const touch = e.touches[0];
      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;

      if (!isGestureLocked) {
        const absX = Math.abs(deltaX);
        const absY = Math.abs(deltaY);
        if (absX > 8 || absY > 8) {
          if (absY >= absX) {
            isScrollingVertical = true;
          } else {
            isScrollingVertical = false;
          }
          isGestureLocked = true;
        }
      }
    },
    { passive: true }
  );

  window.addEventListener(
    "touchend",
    (e) => {
      if (isIgnoredTarget || isScrollingVertical) return;

      // 2. 抬手时若检测到页面存在选中的文本（用户刚完成游标划词），严禁误触发抽屉
      if (hasActiveTextSelection()) return;

      const touch = e.changedTouches[0];
      if (!touch) return;

      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;
      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);
      const deltaTime = Date.now() - touchStartTime;

      if (absX <= absY * 1.2) return;

      const isQuickSwipe = deltaTime < 350 && absX >= 40;
      const isLongSwipe = absX >= 75;

      if (!isQuickSwipe && !isLongSwipe) return;

      if (!state.drawerOpen && deltaX > 0) {
        // 双重保险：仅允许来自屏幕左侧边缘的发起
        if (touchStartX <= EDGE_SWIPE_MAX_X) {
          window.appActions.toggleDrawer(true);
        }
      } else if (state.drawerOpen && deltaX < 0) {
        window.appActions.toggleDrawer(false);
      }
    },
    { passive: true }
  );

  // 绑定全屏阅读/Git变更弹窗右滑返回手势（与底层侧边栏抽屉互斥隔离）
  initDocPreviewSwipeBack();
}

/**
 * 手机端 Git 变更与文档弹窗向右滑动返回手势支持
 * 保证右滑平滑退出回到会话页面，绝不与底层侧边栏滑动重叠
 */
export function initDocPreviewSwipeBack() {
  const modal = document.getElementById("doc-preview-modal");
  if (!modal || modal._swipeBackBound) return;
  modal._swipeBackBound = true;

  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let isScrollingVertical = false;
  let isGestureLocked = false;
  let isIgnoredTarget = false;

  const isHorizontalScrollable = (el) => {
    let cur = el;
    while (cur && cur !== modal) {
      if (
        cur.classList?.contains("overflow-x-auto") ||
        cur.classList?.contains("unified-diff-box") ||
        cur.classList?.contains("no-scrollbar")
      ) {
        if (cur.scrollWidth > cur.clientWidth + 5) {
          return true;
        }
      }
      cur = cur.parentElement;
    }
    return false;
  };

  modal.addEventListener(
    "touchstart",
    (e) => {
      if (window.innerWidth >= 768 || e.touches.length !== 1) {
        isIgnoredTarget = true;
        return;
      }
      if (modal.classList.contains("hidden")) {
        isIgnoredTarget = true;
        return;
      }

      const touch = e.touches[0];
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
      touchStartTime = Date.now();
      isScrollingVertical = false;
      isGestureLocked = false;

      // 如果手指落在横向滚动的 diff 区内部且不在最左边缘（> 48px），忽略返回手势保证横划代码体验
      if (touchStartX > 48 && isHorizontalScrollable(e.target)) {
        isIgnoredTarget = true;
      } else {
        isIgnoredTarget = false;
      }
    },
    { passive: true }
  );

  modal.addEventListener(
    "touchmove",
    (e) => {
      if (isIgnoredTarget || e.touches.length !== 1) return;

      const touch = e.touches[0];
      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;

      if (!isGestureLocked) {
        const absX = Math.abs(deltaX);
        const absY = Math.abs(deltaY);
        if (absX > 8 || absY > 8) {
          isScrollingVertical = absY >= absX;
          isGestureLocked = true;
        }
      }
    },
    { passive: true }
  );

  modal.addEventListener(
    "touchend",
    (e) => {
      if (isIgnoredTarget || isScrollingVertical) return;
      const touch = e.changedTouches[0];
      if (!touch) return;

      const deltaX = touch.clientX - touchStartX;
      const deltaY = touch.clientY - touchStartY;
      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);
      const deltaTime = Date.now() - touchStartTime;

      if (absX <= absY * 1.2) return;

      const isQuickSwipe = deltaTime < 350 && absX >= 40;
      const isLongSwipe = absX >= 75;

      // 向右滑动触发返回上一页（支持多级视图逐级返回）
      if (deltaX > 0 && (isQuickSwipe || isLongSwipe)) {
        if (window.appActions && typeof window.appActions.navigateBackDocModal === "function") {
          window.appActions.navigateBackDocModal();
        } else if (window.appActions && typeof window.appActions.closeDocModal === "function") {
          window.appActions.closeDocModal();
        } else {
          modal.classList.add("hidden");
        }
      }
    },
    { passive: true }
  );
}


export const NEW_SESSION_AGENTS = [
  { value: "agy_cli", label: "Antigravity (AGY)", dotColor: "bg-purple-400" },
  { value: "claude_code", label: "Claude Code (CC)", dotColor: "bg-amber-400" },
  { value: "opencode", label: "OpenCode (OC)", dotColor: "bg-sky-400" },
];

export function closeNewSessionDropdowns() {
  const dropdownPairs = [
    ["new-session-agent-dropdown", "new-session-agent-arrow"],
    ["new-session-mode-dropdown", "new-session-mode-arrow"],
    ["new-session-model-dropdown", "new-session-model-arrow"],
    ["new-session-workspace-dropdown", "new-session-workspace-arrow"],
  ];
  for (const [ddId, arrowId] of dropdownPairs) {
    const dd = document.getElementById(ddId);
    const arrow = document.getElementById(arrowId);
    if (dd) dd.classList.add("hidden");
    if (arrow) arrow.classList.remove("rotate-180");
  }
}

export function handleNewSessionModalClick(e) {
  if (
    !e.target.closest("#new-session-agent-btn") &&
    !e.target.closest("#new-session-agent-dropdown") &&
    !e.target.closest("#new-session-mode-btn") &&
    !e.target.closest("#new-session-mode-dropdown") &&
    !e.target.closest("#new-session-model-btn") &&
    !e.target.closest("#new-session-model-dropdown") &&
    !e.target.closest("#new-session-workspace-btn") &&
    !e.target.closest("#new-session-workspace-dropdown")
  ) {
    closeNewSessionDropdowns();
  }
}

export function toggleNewSessionAgentDropdown(e) {
  if (e) e.stopPropagation();
  const dd = document.getElementById("new-session-agent-dropdown");
  const arrow = document.getElementById("new-session-agent-arrow");
  if (!dd) return;
  const isHidden = dd.classList.contains("hidden");
  closeNewSessionDropdowns();
  if (isHidden) {
    dd.classList.remove("hidden");
    if (arrow) arrow.classList.add("rotate-180");
  }
}

export function selectNewSessionAgent(agentType, e) {
  if (e) e.stopPropagation();
  renderNewSessionAgentUI(agentType);
  closeNewSessionDropdowns();
  if (window.appActions && typeof window.appActions.onNewSessionAgentChange === "function") {
    window.appActions.onNewSessionAgentChange(agentType);
  }
}

export function renderNewSessionAgentUI(activeAgent = "agy_cli") {
  const hiddenInput = document.getElementById("new-session-agent");
  const labelEl = document.getElementById("new-session-agent-label");
  const dotEl = document.getElementById("new-session-agent-dot");
  const dropdown = document.getElementById("new-session-agent-dropdown");

  const cur = NEW_SESSION_AGENTS.find((a) => a.value === activeAgent) || NEW_SESSION_AGENTS[0];
  if (hiddenInput) hiddenInput.value = cur.value;
  if (labelEl) labelEl.textContent = cur.label;
  if (dotEl) {
    dotEl.className = `w-1.5 h-1.5 rounded-full shrink-0 ${cur.dotColor}`;
  }

  if (dropdown) {
    dropdown.innerHTML = NEW_SESSION_AGENTS.map((a) => {
      const isSel = a.value === cur.value;
      return `
        <div onclick="window.appActions.selectNewSessionAgent('${escapeJsSingleQuote(a.value)}', event)"
          class="w-full flex items-center justify-between px-2.5 py-1.5 text-xs rounded-lg cursor-pointer transition select-none ${
            isSel ? "bg-zinc-800 text-white font-medium" : "hover:bg-zinc-900 text-zinc-300"
          }">
          <div class="flex items-center gap-1.5 min-w-0 flex-1 pr-1">
            <span class="w-1.5 h-1.5 rounded-full shrink-0 ${a.dotColor}"></span>
            <span class="truncate ${isSel ? "text-white font-semibold" : "text-zinc-200"}">${escapeHtml(a.label)}</span>
          </div>
          ${isSel ? `<span class="text-sky-400 text-xs font-bold shrink-0">✓</span>` : ""}
        </div>
      `;
    }).join("");
  }
}

export function toggleNewSessionModeDropdown(e) {
  if (e) e.stopPropagation();
  const dd = document.getElementById("new-session-mode-dropdown");
  const arrow = document.getElementById("new-session-mode-arrow");
  if (!dd) return;
  const isHidden = dd.classList.contains("hidden");
  closeNewSessionDropdowns();
  if (isHidden) {
    dd.classList.remove("hidden");
    if (arrow) arrow.classList.add("rotate-180");
  }
}

export function selectNewSessionMode(modeValue, e) {
  if (e) e.stopPropagation();
  const agentInput = document.getElementById("new-session-agent");
  const agentType = agentInput ? agentInput.value : "agy_cli";
  renderNewSessionModeUI(agentType, modeValue);
  closeNewSessionDropdowns();
}

export function renderNewSessionModeUI(agentType = "agy_cli", selectedMode = "auto") {
  const hiddenInput = document.getElementById("new-session-mode");
  const labelEl = document.getElementById("new-session-mode-label");
  const dropdown = document.getElementById("new-session-mode-dropdown");

  const normalizedType =
    agentType === "claude"
      ? "claude_code"
      : agentType === "antigravity" || agentType === "agy_ide"
      ? "agy_cli"
      : agentType || "agy_cli";
  const list = MODES_BY_AGENT[normalizedType] || MODES_BY_AGENT.agy_cli;
  const isSupported = list.some((m) => m.value === selectedMode);
  const activeMode = isSupported ? selectedMode : list[0]?.value || "auto";

  if (hiddenInput) hiddenInput.value = activeMode;
  const curItem = list.find((m) => m.value === activeMode) || list[0];
  if (labelEl && curItem) {
    labelEl.textContent = curItem.label;
  }

  if (dropdown) {
    dropdown.innerHTML = list.map((m) => {
      const isSel = m.value === activeMode;
      return `
        <div onclick="window.appActions.selectNewSessionMode('${escapeJsSingleQuote(m.value)}', event)"
          class="w-full flex items-center justify-between px-2.5 py-1.5 text-xs rounded-lg cursor-pointer transition select-none ${
            isSel ? "bg-zinc-800 text-white font-medium" : "hover:bg-zinc-900 text-zinc-300"
          }">
          <div class="flex items-center gap-1.5 min-w-0 flex-1 pr-1">
            <span class="truncate ${isSel ? "text-white font-semibold" : "text-zinc-200"}">${escapeHtml(m.label)}</span>
          </div>
          ${isSel ? `<span class="text-sky-400 text-xs font-bold shrink-0">✓</span>` : ""}
        </div>
      `;
    }).join("");
  }
  return activeMode;
}

export function toggleNewSessionModelDropdown(e) {
  if (e) e.stopPropagation();
  const dd = document.getElementById("new-session-model-dropdown");
  const arrow = document.getElementById("new-session-model-arrow");
  if (!dd) return;
  const isHidden = dd.classList.contains("hidden");
  closeNewSessionDropdowns();
  if (isHidden) {
    dd.classList.remove("hidden");
    if (arrow) arrow.classList.add("rotate-180");
  }
}

export function selectNewSessionModel(modelId, e) {
  if (e) e.stopPropagation();
  const input = document.getElementById("new-session-model");
  if (input) input.value = modelId;
  const agentSelect = document.getElementById("new-session-agent");
  const agType = agentSelect ? agentSelect.value : "agy_cli";
  try {
    localStorage.setItem("agentdeck_preferred_model_" + agType, modelId);
  } catch (_) {}
  renderNewSessionModelUI(agType, modelId);
  closeNewSessionDropdowns();
}

export function renderNewSessionModelUI(agentType, selectedModelId = "") {
  const normAgent = (agentType === "antigravity" || agentType === "agy_ide") ? "agy_cli" : agentType;
  const list = state.modelsByAgent[agentType] || state.modelsByAgent[normAgent];
  const models =
    Array.isArray(list) && list.length > 0
      ? list
      : DEFAULT_MODELS_BY_AGENT[agentType] || DEFAULT_MODELS_BY_AGENT.agy_cli;

  let userPrefItem = null;
  try {
    const userPrefModel = localStorage.getItem("agentdeck_preferred_model_" + agentType);
    if (userPrefModel) userPrefItem = models.find((m) => m.id === userPrefModel);
  } catch (_) {}
  const defaultModelItem = userPrefItem || models.find((m) => m.is_default) || models[0];
  const activeId = selectedModelId || (defaultModelItem ? defaultModelItem.id : "");

  const hiddenInput = document.getElementById("new-session-model");
  if (hiddenInput) hiddenInput.value = activeId;

  const labelEl = document.getElementById("new-session-model-label");
  const badgeEl = document.getElementById("new-session-model-badge");
  const dropdown = document.getElementById("new-session-model-dropdown");

  const currentModel = models.find((m) => m.id === activeId) || defaultModelItem;
  if (labelEl && currentModel) {
    labelEl.textContent = currentModel.label;
  }
  if (badgeEl && currentModel) {
    const hasQuota = typeof currentModel.quota === "number" && !isNaN(currentModel.quota);
    const pct = hasQuota ? Math.round(currentModel.quota * 100) : null;
    if (currentModel.tag) {
      badgeEl.textContent = currentModel.tag;
      badgeEl.classList.remove("hidden");
    } else if (hasQuota) {
      badgeEl.textContent = `${pct}%`;
      badgeEl.classList.remove("hidden");
    } else {
      badgeEl.classList.add("hidden");
    }
  }

  if (dropdown) {
    dropdown.innerHTML = models
      .map((m) => {
        const isSel = m.id === activeId;
        const hasQuota = typeof m.quota === "number" && !isNaN(m.quota);
        const pct = hasQuota ? Math.round(m.quota * 100) : null;
        let quotaText = hasQuota ? `${pct}%` : "";
        return `
          <div onclick="window.appActions.selectNewSessionModel('${escapeJsSingleQuote(m.id)}', event)"
            class="w-full flex items-center justify-between px-2.5 py-1.5 text-xs rounded-lg cursor-pointer transition select-none ${
              isSel
                ? "bg-zinc-800 text-white font-medium"
                : "hover:bg-zinc-900 text-zinc-300"
            }">
            <div class="flex items-center gap-1.5 min-w-0 flex-1 pr-2">
              <span class="w-1.5 h-1.5 rounded-full shrink-0 ${isSel ? "bg-sky-400 ring-2 ring-sky-400/40" : "bg-zinc-700"}"></span>
              <span class="truncate ${isSel ? "text-white font-semibold" : "text-zinc-200"}">${escapeHtml(m.label)}</span>
            </div>
            <div class="flex items-center gap-1 shrink-0">
              ${m.tag ? `<span class="text-[9px] px-1 py-0.2 rounded font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/30 shrink-0">${escapeHtml(m.tag)}</span>` : ""}
              ${m.is_default ? `<span class="text-[9px] px-1 py-0.2 rounded font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shrink-0">默认</span>` : ""}
              ${quotaText ? `<span class="text-[10px] font-mono text-zinc-400 shrink-0">${quotaText}</span>` : ""}
            </div>
          </div>
        `;
      })
      .join("");
  }
}

export function toggleNewSessionWorkspaceDropdown(e) {
  if (e) e.stopPropagation();
  const dd = document.getElementById("new-session-workspace-dropdown");
  const arrow = document.getElementById("new-session-workspace-arrow");
  if (!dd) return;
  const isHidden = dd.classList.contains("hidden");
  closeNewSessionDropdowns();
  if (isHidden) {
    dd.classList.remove("hidden");
    if (arrow) arrow.classList.add("rotate-180");
  }
}

export function selectNewSessionWorkspace(path, name, e) {
  if (e) e.stopPropagation();
  const input = document.getElementById("new-session-workspace");
  if (input) input.value = path;
  const nameEl = document.getElementById("new-session-workspace-name");
  const subEl = document.getElementById("new-session-workspace-sub");
  if (nameEl) nameEl.textContent = name;
  if (subEl) subEl.textContent = path;
  closeNewSessionDropdowns();
}

export function renderNewSessionWorkspaceUI(prefillProjectPath = "") {
  const hiddenInput = document.getElementById("new-session-workspace");
  const nameEl = document.getElementById("new-session-workspace-name");
  const subEl = document.getElementById("new-session-workspace-sub");
  const dropdown = document.getElementById("new-session-workspace-dropdown");

  // 1. 使用已去重且严格按活跃度降序排列的项目列表
  const projects = getAggregatedProjects(state.sessions, state.workspaces);
  const validProjects = projects.filter((p) => !p.isHidden || p.sessions.length > 0);

  let selectedProj = null;
  if (prefillProjectPath) {
    const prefillKey = getProjectInfo(prefillProjectPath).key;
    selectedProj = validProjects.find((p) => p.key === prefillKey);
  }
  if (!selectedProj && state.selectedSessionId) {
    const curSess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    if (curSess && curSess.project_path) {
      const curKey = getProjectInfo(curSess.project_path).key;
      selectedProj = validProjects.find((p) => p.key === curKey);
    }
  }
  if (!selectedProj && validProjects.length > 0) {
    selectedProj = validProjects[0]; // 默认选中最活跃项目
  }

  const activePath = selectedProj ? selectedProj.path : (prefillProjectPath || "");
  const activeName = selectedProj ? selectedProj.name : (getProjectInfo(prefillProjectPath).name || "项目目录");

  if (hiddenInput) hiddenInput.value = activePath;
  if (nameEl) nameEl.textContent = activeName;
  if (subEl) subEl.textContent = activePath;

  if (dropdown) {
    if (validProjects.length === 0) {
      dropdown.innerHTML = `<div class="p-2 text-center text-xs text-zinc-500">暂无项目目录</div>`;
      return;
    }

    dropdown.innerHTML = validProjects
      .map((proj) => {
        const isSel = proj.path === activePath || proj.key === getProjectInfo(activePath).key;
        return `
          <div onclick="window.appActions.selectNewSessionWorkspace('${escapeJsSingleQuote(proj.path)}', '${escapeJsSingleQuote(proj.name)}', event)"
            class="w-full px-2.5 py-1.5 rounded-lg cursor-pointer transition select-none flex flex-col gap-0.5 ${
              isSel
                ? "bg-zinc-800 border border-zinc-700/80"
                : "hover:bg-zinc-900 border border-transparent"
            }">
            <div class="flex items-center justify-between min-w-0">
              <div class="flex items-center gap-1.5 min-w-0 flex-1 pr-1">
                <span class="text-xs shrink-0">📂</span>
                <span class="text-xs font-semibold truncate ${isSel ? "text-white" : "text-zinc-200"}">${escapeHtml(proj.name)}</span>
                ${proj.hasActiveSession ? `<span class="text-[9px] px-1 py-0.2 rounded font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shrink-0">⚡ 运行中</span>` : ""}
              </div>
              <div class="flex items-center gap-1.5 shrink-0">
                ${proj.sessions.length > 0 ? `<span class="text-[10px] text-zinc-500 font-mono">${proj.sessions.length}个对话</span>` : ""}
                ${isSel ? `<span class="text-sky-400 text-xs font-bold shrink-0">✓</span>` : ""}
              </div>
            </div>
            <div class="text-[10px] text-zinc-500 font-mono truncate pl-4 text-left block w-full">${escapeHtml(proj.path)}</div>
          </div>
        `;
      })
      .join("");
  }
}

export function closeNewSessionModal() {
  const modal = document.getElementById("new-session-modal");
  if (modal) modal.classList.add("hidden");
  closeNewSessionDropdowns();
}

export function toggleCustomPathInput() {
  const container = document.getElementById("new-session-custom-path-container");
  const btn = document.getElementById("toggle-custom-path-btn");
  if (!container) return;
  const isHidden = container.classList.contains("hidden");
  if (isHidden) {
    container.classList.remove("hidden");
    if (btn) btn.textContent = "- 使用项目列表";
    const input = document.getElementById("new-session-custom-path");
    if (input) input.focus();
  } else {
    container.classList.add("hidden");
    if (btn) btn.textContent = "+ 输入其它路径";
  }
}

export function bindNewSessionModalKeyboard() {
  const modal = document.getElementById("new-session-modal");
  if (!modal || modal._keyboardBound) return;
  modal._keyboardBound = true;

  modal.addEventListener("keydown", (e) => {
    if (modal.classList.contains("hidden")) return;

    if (e.key === "Escape") {
      e.preventDefault();
      closeNewSessionModal();
      return;
    }

    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      confirmCreateNewSession();
    }
  });
}

export function openNewSessionModal(prefillPrompt = "", prefillProjectPath = "") {
  const modal = document.getElementById("new-session-modal");
  const wsSelect = document.getElementById("new-session-workspace");
  const agentSelect = document.getElementById("new-session-agent");
  const modelSelect = document.getElementById("new-session-model");
  const customPathInput = document.getElementById("new-session-custom-path");
  const promptInput = document.getElementById("new-session-prompt");
  if (!modal) return;

  // 渲染高质感、按活跃度优先的项目目录选择器（彻底消除重复项）
  renderNewSessionWorkspaceUI(prefillProjectPath);

  const projects = getAggregatedProjects(state.sessions, state.workspaces);
  const knownPathSet = new Set(projects.map((p) => p.path.toLowerCase()));
  const customContainer = document.getElementById("new-session-custom-path-container");
  const toggleBtn = document.getElementById("toggle-custom-path-btn");
  const hasCustomPrefill = Boolean(
    prefillProjectPath && !knownPathSet.has(getProjectInfo(prefillProjectPath).key)
  );

  if (customPathInput) {
    customPathInput.value = hasCustomPrefill ? prefillProjectPath : "";
  }
  if (customContainer) {
    if (hasCustomPrefill) {
      customContainer.classList.remove("hidden");
      if (toggleBtn) toggleBtn.textContent = "- 使用项目列表";
    } else {
      customContainer.classList.add("hidden");
      if (toggleBtn) toggleBtn.textContent = "+ 输入其它路径";
    }
  }

  const agType = agentSelect ? agentSelect.value : "agy_cli";
  renderNewSessionAgentUI(agType);
  renderNewSessionModeUI(agType, "auto");
  renderNewSessionModelUI(agType, "");
  renderModelOptionsForAgent(modelSelect, agType, "");
  const newModeSelect = document.getElementById("new-session-mode");
  renderModeOptionsForAgent(newModeSelect, agType, "auto", false);

  if (promptInput) {
    promptInput.value = prefillPrompt || "";
  }

  bindNewSessionModalKeyboard();
  modal.classList.remove("hidden");

  // 延时让 DOM 渲染完成自动聚焦输入框
  setTimeout(() => {
    if (promptInput) promptInput.focus();
  }, 50);
}

let isCreatingSession = false;

export async function confirmCreateNewSession() {
  if (isCreatingSession) return;

  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    showToast("当前网络已离线，请连接网络后重试", "error");
    return;
  }

  const agentSelect = document.getElementById("new-session-agent");
  const modelSelect = document.getElementById("new-session-model");
  const customPathInput = document.getElementById("new-session-custom-path");
  const wsSelect = document.getElementById("new-session-workspace");
  const modeSelect = document.getElementById("new-session-mode");
  const promptInput = document.getElementById("new-session-prompt");
  const confirmBtn = document.getElementById("new-session-confirm-btn");
  const modal = document.getElementById("new-session-modal");

  const agentType = agentSelect ? agentSelect.value : "agy_cli";
  const modelName = modelSelect ? modelSelect.value : "";
  const customPath = customPathInput ? customPathInput.value.trim() : "";
  const selectedPath = wsSelect ? wsSelect.value : "";
  const mode = modeSelect ? modeSelect.value : "auto";
  const initialPrompt = promptInput ? promptInput.value.trim() : "";

  const finalPath = customPath || selectedPath;
  if (customPath) {
    const folderName = customPath.replace(/\\/g, "/").split("/").filter(Boolean).pop() || "Project";
    await apiFetch("/api/v1/workspaces", {
      method: "POST",
      body: JSON.stringify({
        workspace_name: folderName,
        workspace_path: customPath,
        default_agent: agentType,
      }),
    }).catch(() => {});
    await loadWorkspaces();
  }

  // 1. 立即进入防重防抖加载态
  isCreatingSession = true;
  const originalBtnHtml = confirmBtn ? confirmBtn.innerHTML : "创建";
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.classList.add("opacity-75", "cursor-not-allowed");
    confirmBtn.innerHTML = `
      <span class="inline-flex items-center gap-1.5">
        <svg class="animate-spin -ml-0.5 h-3.5 w-3.5 text-zinc-950 inline" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>创建中...</span>
      </span>
    `;
  }

  let res = null;
  try {
    // 2. 直接调用轻量 RESTful API 瞬时创建（<10ms）
    res = await apiFetch("/api/v1/sessions", {
      method: "POST",
      // 创建含首轮派发（LS 注入在 IDE 忙碌时可能 >8s），8s 默认超时会误报失败
      timeout: 25000,
      body: JSON.stringify({
        agent_type: agentType,
        project_path: finalPath,
        execution_mode: mode,
        model_name: modelName,
        initial_prompt: initialPrompt,
      }),
    });
  } catch (err) {
    showToast(`创建会话失败: ${err.message}`, "error");
    return;
  } finally {
    isCreatingSession = false;
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.classList.remove("opacity-75", "cursor-not-allowed");
      confirmBtn.innerHTML = originalBtnHtml;
    }
  }

  // 3. 立即关闭弹窗并清空输入
  if (modal) modal.classList.add("hidden");
  if (promptInput) promptInput.value = "";

  if (res && res.unified_id) {
    const rawId = res.meta?.raw_session_id || res.unified_id.split(":")[1] || "";
    const pInfo = finalPath ? getProjectInfo(finalPath) : null;
    const isProj = Boolean(finalPath && pInfo?.name && pInfo.name !== "未分类");

    // 4. 乐观构建会话对象并瞬时置顶，杜绝界面等待感
    const optimisticSess = {
      unified_id: res.unified_id,
      agent_type: res.meta?.agent_type || agentType,
      raw_session_id: rawId,
      parent_unified_id: null,
      project_path: finalPath || null,
      project_name: pInfo?.name || "",
      is_project: isProj,
      title: res.meta?.title || (initialPrompt ? initialPrompt.slice(0, 40) : "新对话"),
      preview: initialPrompt || "新会话已就绪",
      is_pinned: 0,
      execution_mode: mode,
      model_name: modelName,
      status: initialPrompt ? "RUNNING" : "IDLE",
      updated_at: new Date().toISOString(),
    };

    state.allSessions = [optimisticSess, ...state.allSessions.filter((s) => s.unified_id !== res.unified_id)];
    state.sessions = [optimisticSess, ...state.sessions.filter((s) => s.unified_id !== res.unified_id)];
    renderSessionList();

    if (initialPrompt) {
      if (!state.optimisticMessages[res.unified_id]) {
        state.optimisticMessages[res.unified_id] = [];
      }
      state.optimisticMessages[res.unified_id].push({
        item_id: `optimistic_${Date.now()}`,
        step_index: 1,
        role: "user",
        created_at: new Date().toISOString(),
        content: initialPrompt,
        thinking: "",
        tool_calls: [],
        status: "RUNNING",
      });
      state.pendingTurnThinking[res.unified_id] = Date.now();
    }

    showToast("会话已创建", "success");
    await selectSession(res.unified_id);
    // 后台静默对齐全量状态
    loadSessions(true).catch(() => {});
  } else {
    await loadSessions();
  }
}


// --- 交互式决策问答桌面级原生模态框逻辑 ---
let decisionCountdownTimer = null;
let decisionSecondsRemaining = 45;
let decisionMaxSeconds = 45;
let decisionIsPaused = false;
let currentDecisionForm = null;
let decisionSelections = {};
let decisionKeyHandlerBound = false;

function onDecisionKeyDown(e) {
  const modal = document.getElementById("interactive-decision-modal");
  if (!modal || modal.classList.contains("hidden") || !currentDecisionForm) return;

  if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey) {
    e.preventDefault();
    confirmInteractiveDecision("user");
    return;
  }
  if (e.key === "Escape") {
    pauseDecisionCountdown("已按 Escape 暂停");
    return;
  }
  if (e.key >= "1" && e.key <= "9") {
    const oIdx = parseInt(e.key, 10) - 1;
    const fields = currentDecisionForm.fields || [];
    const targetField = fields.find((f, i) => !decisionSelections[f.key || `q${i}`]?.length) || fields[0];
    if (targetField && targetField.options && targetField.options[oIdx]) {
      e.preventDefault();
      const opt = targetField.options[oIdx];
      const val = typeof opt === "string" ? opt.split(" — ")[0].trim() : (opt.value || opt.label || "");
      const fKey = targetField.key || "q0";
      const isMulti = targetField.is_multi_select || targetField.type === "multiselect";
      selectDecisionOption(fKey, val, !!isMulti);
    }
  }
}

export function openInteractiveDecisionModal(formPayload) {
  if (!formPayload) return;
  if (currentDecisionForm && currentDecisionForm.id === formPayload.id) {
    return;
  }
  if (!formPayload.unifiedId && state.selectedSessionId) {
    formPayload.unifiedId = state.selectedSessionId;
  }
  currentDecisionForm = formPayload;
  decisionSelections = {};
  decisionIsPaused = false;
  decisionSecondsRemaining = 45;

  (formPayload.fields || []).forEach((field, fIdx) => {
    const fKey = field.key || `q${fIdx}`;
    let recVal = "";
    (field.options || []).forEach((opt, oIdx) => {
      let label = typeof opt === "string" ? opt : (opt.label || opt.value || "");
      let value = typeof opt === "string" ? opt.split(" — ")[0].trim() : (opt.value || opt.label || "");
      if (label.includes("(Recommended)") || value.includes("(Recommended)")) {
        recVal = value;
      } else if (oIdx === 0 && !recVal) {
        recVal = value;
      }
    });
    if (recVal) {
      decisionSelections[fKey] = [recVal];
    }
  });

  const card = document.getElementById("interactive-decision-card");
  const modal = document.getElementById("interactive-decision-modal");
  if (card && modal) {
    card.innerHTML = renderInteractiveDecisionModalHtml(formPayload, decisionSelections);
    modal.classList.remove("hidden");
  }

  startDecisionCountdown();
  if (!decisionKeyHandlerBound) {
    document.addEventListener("keydown", onDecisionKeyDown);
    decisionKeyHandlerBound = true;
  }
}

export function closeInteractiveDecisionModal() {
  if (decisionCountdownTimer) {
    clearInterval(decisionCountdownTimer);
    decisionCountdownTimer = null;
  }
  currentDecisionForm = null;
  decisionSelections = {};
  decisionIsPaused = false;
  const modal = document.getElementById("interactive-decision-modal");
  if (modal) modal.classList.add("hidden");
}

export function selectDecisionOption(fKey, value, isMulti) {
  pauseDecisionCountdown("已暂停自动推进");
  if (!decisionSelections[fKey]) {
    decisionSelections[fKey] = [];
  }
  if (isMulti) {
    if (decisionSelections[fKey].includes(value)) {
      decisionSelections[fKey] = decisionSelections[fKey].filter((x) => x !== value);
    } else {
      decisionSelections[fKey].push(value);
    }
  } else {
    decisionSelections[fKey] = [value];
  }
  const card = document.getElementById("interactive-decision-card");
  if (card && currentDecisionForm) {
    card.innerHTML = renderInteractiveDecisionModalHtml(currentDecisionForm, decisionSelections);
    updateDecisionCountdownUI();
  }
}

export function pauseDecisionCountdown(reasonText = "已暂停自动推进") {
  decisionIsPaused = true;
  if (decisionCountdownTimer) {
    clearInterval(decisionCountdownTimer);
    decisionCountdownTimer = null;
  }
  const badge = document.getElementById("decision-countdown-badge");
  const timerText = document.getElementById("decision-countdown-timer");
  const pbar = document.getElementById("decision-progress-bar");
  if (badge && timerText) {
    badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-400 font-mono text-[11px] shadow-sm";
    timerText.textContent = reasonText;
  }
  if (pbar) {
    pbar.className = "h-full bg-zinc-700 transition-all duration-300";
  }
}

function ensureDecisionTicker() {
  if (decisionCountdownTimer) return;
  decisionCountdownTimer = setInterval(() => {
    if (decisionIsPaused) return;
    decisionSecondsRemaining--;
    updateDecisionCountdownUI();
    if (decisionSecondsRemaining <= 0) {
      clearInterval(decisionCountdownTimer);
      decisionCountdownTimer = null;
      confirmInteractiveDecision("auto_timeout");
    }
  }, 1000);
}

function startDecisionCountdown(seconds = 45) {
  if (decisionCountdownTimer) {
    clearInterval(decisionCountdownTimer);
    decisionCountdownTimer = null;
  }
  decisionSecondsRemaining = seconds;
  decisionMaxSeconds = Math.max(decisionMaxSeconds, seconds);
  decisionIsPaused = false;
  updateDecisionCountdownUI();
  ensureDecisionTicker();
}

export function extendDecisionCountdown(seconds = 60) {
  if (!currentDecisionForm) return;
  decisionSecondsRemaining += seconds;
  if (decisionSecondsRemaining > decisionMaxSeconds) {
    decisionMaxSeconds = decisionSecondsRemaining;
  }
  decisionIsPaused = false;

  const badge = document.getElementById("decision-countdown-badge");
  const pbar = document.getElementById("decision-progress-bar");
  if (badge) {
    badge.className = "flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-950/80 hover:bg-amber-900/80 border border-amber-500/40 text-amber-300 font-mono text-[11px] shadow-sm cursor-pointer select-none transition active:scale-95";
  }
  if (pbar) {
    pbar.className = "h-full bg-amber-500 transition-all duration-1000 ease-linear";
  }

  ensureDecisionTicker();
  updateDecisionCountdownUI();
  showToast(`已延长决策时间 +${seconds}s`, "info");
}

function updateDecisionCountdownUI() {
  const timerText = document.getElementById("decision-countdown-timer");
  const pbar = document.getElementById("decision-progress-bar");
  if (timerText && !decisionIsPaused) {
    timerText.textContent = `${decisionSecondsRemaining}s 后自动推荐`;
  }
  if (pbar && !decisionIsPaused) {
    const base = decisionMaxSeconds > 0 ? decisionMaxSeconds : 45;
    const pct = Math.max(0, Math.min(100, (decisionSecondsRemaining / base) * 100));
    pbar.style.width = `${pct}%`;
  }
}

export async function confirmInteractiveDecision(source = "user") {
  if (!currentDecisionForm) return;
  const formId = currentDecisionForm.id;
  const sessionUnifiedId = currentDecisionForm.unifiedId || currentDecisionForm.sessionID || state.selectedSessionId;
  const answer = {};

  (currentDecisionForm.fields || []).forEach((field, fIdx) => {
    const fKey = field.key || `q${fIdx}`;
    const chosen = decisionSelections[fKey] || [];
    const isMulti = field.is_multi_select || field.type === "multiselect";
    if (isMulti) {
      answer[fKey] = chosen;
    } else {
      answer[fKey] = chosen.length > 0 ? chosen[0] : "";
    }
  });

  const submitBtn = document.getElementById("decision-submit-btn");
  const submitText = document.getElementById("decision-submit-text");
  const retryStatus = document.getElementById("decision-retry-status");
  if (submitBtn) submitBtn.disabled = true;

  const maxRetries = 3;
  let success = false;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      if (submitText) {
        submitText.textContent = attempt === 1 ? "正在同步决策..." : `重试同步中 (${attempt}/${maxRetries})...`;
      }
      if (retryStatus && attempt > 1) {
        retryStatus.textContent = `第 ${attempt}/${maxRetries} 次重试中...`;
      }
      const res = await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionUnifiedId)}/form/${formId}/reply`, {
        method: "POST",
        body: JSON.stringify({ answer, source }),
      });
      if (res && (res.ok || res.status === 409 || res.status_code === 409)) {
        success = true;
        break;
      }
    } catch (err) {
      console.warn(`[交互决策] 提交异常 (第 ${attempt} 次):`, err);
      if (err?.status === 409 || err?.message?.includes("409")) {
        success = true;
        break;
      }
      if (attempt < maxRetries) {
        const backoffMs = Math.pow(2, attempt - 1) * 1000;
        await new Promise((r) => setTimeout(r, backoffMs));
      }
    }
  }

  if (success) {
    showToast("决策已生效，会话继续推进", "success");
    closeInteractiveDecisionModal();
    if (window.appActions?.loadSessionMessages) {
      window.appActions.loadSessionMessages(sessionUnifiedId);
    }
  } else {
    if (submitBtn) submitBtn.disabled = false;
    if (submitText) submitText.textContent = "确定 (重试)";
    if (retryStatus) retryStatus.textContent = "同步失败，请检查网络后点击重试";
    showToast("决策同步失败，请点击重试", "error");
  }
}
