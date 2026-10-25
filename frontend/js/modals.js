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
} from "./data.js";
import { escapeHtml } from "./utils.js";
import { renderSessionList } from "./render-sidebar.js";
import { renderModelOptionsForAgent, renderModeOptionsForAgent } from "./render-views.js";
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
}


export function closeNewSessionModal() {
  const modal = document.getElementById("new-session-modal");
  if (modal) modal.classList.add("hidden");
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

  // 汇总已有项目路径供下拉选择
  const pathMap = new Map();
  for (const w of state.workspaces) {
    if (w.workspace_path) pathMap.set(w.workspace_path, w.workspace_name);
  }
  for (const s of state.sessions) {
    if (!s.is_project) continue;
    const info = getProjectInfo(s.project_path);
    if (info.path && !pathMap.has(info.path)) {
      pathMap.set(info.path, s.project_name || info.name);
    }
  }

  if (wsSelect) {
    wsSelect.innerHTML = Array.from(pathMap.entries())
      .map(
        ([path, name]) =>
          `<option value="${escapeHtml(path)}" ${
            path === prefillProjectPath ? "selected" : ""
          }>${escapeHtml(name)} (${escapeHtml(path)})</option>`
      )
      .join("");
  }

  const customContainer = document.getElementById("new-session-custom-path-container");
  const toggleBtn = document.getElementById("toggle-custom-path-btn");
  const hasCustomPrefill = Boolean(prefillProjectPath && !pathMap.has(prefillProjectPath));

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
