/**
 * AgentDeck 抽屉手势、滚动守护与模态框生命周期控制
 */

import { state } from "./state.js";
import { showToast, apiFetch } from "./api.js";
import {
  loadSessions,
  selectSession,
  getProjectInfo,
} from "./data.js";

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

          <span class="w-1.5 h-1.5 rounded-full ${
            curChannel.type === "wifi" ? "bg-emerald-400" : curChannel.type === "tailscale" ? "bg-sky-400" : "bg-amber-400"
          }"></span>
          <span>${channelTips[state.selectedQrNet] || "扫码或复制链接在手机中打开"}</span>
        </div>
      </div>
    </div>
  `;
}

// --- 7. 模态框与抽屉控制 ---
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

// 移动端向右滑动展开菜单手势支持
export function initDrawerSwipeGestures() {
  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let isScrollingVertical = false;
  let isGestureLocked = false;
  let isIgnoredTarget = false;

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
        window.appActions.toggleDrawer(true);
      } else if (state.drawerOpen && deltaX < 0) {
        window.appActions.toggleDrawer(false);
      }
    },
    { passive: true }
  );
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

  if (customPathInput) {
    customPathInput.value = prefillProjectPath && !pathMap.has(prefillProjectPath) ? prefillProjectPath : "";
  }

  const agType = agentSelect ? agentSelect.value : "agy_cli";
  renderModelOptionsForAgent(modelSelect, agType, "");
  const newModeSelect = document.getElementById("new-session-mode");
  renderModeOptionsForAgent(newModeSelect, agType, "ask", false);

  if (promptInput) {
    promptInput.value = prefillPrompt || "";
  }
  modal.classList.remove("hidden");
}

export async function confirmCreateNewSession() {
  const agentType = document.getElementById("new-session-agent").value;
  const modelName = document.getElementById("new-session-model").value;
  const customPath = document.getElementById("new-session-custom-path").value.trim();
  const selectedPath = document.getElementById("new-session-workspace").value;
  const mode = document.getElementById("new-session-mode").value;
  const initialPrompt = document.getElementById("new-session-prompt").value.trim();

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

  const res = await apiFetch("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      agent_type: agentType,
      project_path: finalPath,
      execution_mode: mode,
      model_name: modelName,
      initial_prompt: initialPrompt,
    }),
  });

  document.getElementById("new-session-modal").classList.add("hidden");
  document.getElementById("new-session-prompt").value = "";
  if (res.unified_id) {
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
