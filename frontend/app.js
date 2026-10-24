/**
 * AgentDeck Web & Mobile 中控台核心前端入口 (ES Modules 入口)
 */

import { state } from "./js/state.js";
import { checkAndExchangePairCode, apiFetch } from "./js/api.js";
import {
  connectWebSocket,
  triggerAutoRoaming,
  lastPongReceived,
  wsInstance,
} from "./js/network.js";
import {
  loadSystemStatus,
  loadWorkspaces,
  loadSessions,
  selectSession,
  checkFrontendVersion,
  isSessionWorking,
  initEarlyCachedSessions,
} from "./js/data.js";
import { loadSessionMessages } from "./js/data-messages.js";
import {
  renderSidebarStatus,
  renderSessionList,
  renderSidebarSkeleton,
  renderCurrentSessionHeader,
  renderMessagesList,
  renderLiveStreamOverlay,
  highlightSlashItem,
  updateCollapseStepsPillVisibility,
  updateStickyPromptVisibility,
} from "./js/render.js";
import {
  updateDrawerVisibility,
  initDrawerSwipeGestures,
  bindSessionListScrollGuardian,
} from "./js/modals.js";
import { getSessionIdFromHash } from "./js/utils.js";

// 确保用户交互动作集注册到 window.appActions
import "./js/actions.js";

// --- 初始化入口 ---
async function initApp() {
  // 读取服务端注入的前端基准版本指纹
  const metaBuild = document.querySelector('meta[name="agentdeck-build"]')?.content;
  if (metaBuild) state.frontendBuild = metaBuild;
  renderSidebarStatus();

  updateDrawerVisibility();
  initDrawerSwipeGestures();
  bindSessionListScrollGuardian();

  // 1. SWR 极速初渲染（0ms 首屏直出）：尝试加载本地缓存的最近已知会话
  const earlySessionId = initEarlyCachedSessions();
  if (earlySessionId) {
    renderSessionList();
    renderCurrentSessionHeader();
    renderMessagesList(); // 立即渲染消息区（若有缓存则呈现消息，若无则呈现骨架屏，杜绝视口滞后）
  } else {
    renderSidebarSkeleton();
  }

  // 2. 鉴权与配对码预检
  await checkAndExchangePairCode();

  // 3. 网络请求全面并行化：将会话消息拉取与列表聚合、系统状态并行执行
  const targetInitId = typeof earlySessionId === "string" ? earlySessionId : (getSessionIdFromHash() || localStorage.getItem("agentdeck_last_selected_session"));
  const initialMessagesPromise = targetInitId ? loadSessionMessages(targetInitId) : Promise.resolve();

  try {
    await Promise.all([
      loadSystemStatus(),
      loadWorkspaces(),
      loadSessions(),
      initialMessagesPromise,
    ]);
  } catch (err) {
    console.warn("冷启动连接当前通道失败，尝试漫游探测可用通道...", err);
    triggerAutoRoaming("initial_boot_fail");
  }
  connectWebSocket();
  checkFrontendVersion();

  // 确保冷启动即使未选择任何会话或首次加载无数据，也能脱离 HTML 初始的静态骨架态
  if (!state.selectedSessionId) {
    renderCurrentSessionHeader();
    renderSessionList();
    renderMessagesList();
  }

  // 手机锁屏唤醒或切回主屏幕应用时立即检测一次版本与通道连通性
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      checkFrontendVersion();
      const isStale = Date.now() - lastPongReceived > 20000;
      if (!state.wsConnected || isStale || !wsInstance || wsInstance.readyState !== WebSocket.OPEN) {
        console.info("[AgentDeck] 移动端切回前台发现连接断开或心跳逾期，立即加速重连与漫游...");
        connectWebSocket();
        triggerAutoRoaming("visibility_resume");
      }
    }
  });

  // 手机网络环境变更监听（Wi-Fi 断开/连接、蜂窝网切换等）
  window.addEventListener("online", () => {
    triggerAutoRoaming("network_online");
  });
  // 保持周期性轻量巡检
  setInterval(checkFrontendVersion, 25000);

  window.addEventListener("hashchange", () => {
    const hashId = getSessionIdFromHash();
    if (hashId && hashId !== state.selectedSessionId) {
      if (state.allSessions.some((s) => s.unified_id === hashId)) {
        selectSession(hashId);
      }
    }
  });

  let workingPollCounter = 0;
  setInterval(() => {
    if (state.selectedSessionId) {
      if (isSessionWorking(state.selectedSessionId)) {
        renderLiveStreamOverlay();
        // 活跃执行态自愈收敛巡检：每 2 秒静默核实一次当前会话的最新完成态，消除外部会话完成后的同步滞后
        workingPollCounter++;
        if (workingPollCounter % 2 === 0) {
          loadSessionMessages(state.selectedSessionId, { silent: true });
        }
      } else {
        workingPollCounter = 0;
      }
    }
  }, 1000);

  const searchInput = document.getElementById("session-search-input");
  if (searchInput) {
    let remoteSearchDebounce = null;
    let remoteSearchController = null;

    searchInput.addEventListener("input", (e) => {
      const val = e.target.value;
      state.searchQuery = val;
      const q = val.trim();

      // 阶段 1：本地内存 0ms 瞬时过滤并渲染
      renderSessionList();

      clearTimeout(remoteSearchDebounce);
      if (remoteSearchController) {
        remoteSearchController.abort();
        remoteSearchController = null;
      }

      if (!q) {
        if (state.remoteSearchMatches && state.remoteSearchMatches.size > 0) {
          state.remoteSearchMatches.clear();
          renderSessionList();
        }
        return;
      }

      // 阶段 2：防抖 220ms 触发服务端穿透全文检索，彻底搜罗全量历史会话与未展开内容
      remoteSearchDebounce = setTimeout(async () => {
        remoteSearchController = new AbortController();
        try {
          const res = await apiFetch(`/api/v1/sessions/search?q=${encodeURIComponent(q)}&limit=60`, {
            signal: remoteSearchController.signal,
          });
          if (res && res.ok && Array.isArray(res.matches)) {
            // 确保当前搜索词未发生改变
            if (state.searchQuery.trim() === q) {
              state.remoteSearchMatches.clear();
              for (const m of res.matches) {
                if (m && m.unified_id) {
                  state.remoteSearchMatches.set(m.unified_id, {
                    type: m.type,
                    snippet: m.snippet,
                    session: m.session,
                  });
                }
              }
              renderSessionList();
            }
          }
        } catch (err) {
          if (err && err.name !== "AbortError") {
            console.debug("服务端会话搜索请求异常:", err);
          }
        }
      }, 220);
    });
  }

  const promptInput = document.getElementById("prompt-input");
  if (promptInput) {
    promptInput.addEventListener("keydown", (e) => {
      const popover = document.getElementById("slash-popover");
      const isPopoverVisible = state.slashMenuOpen && popover && !popover.classList.contains("hidden");
      const filtered = state.filteredSlashCommands || [];

      // 当弹出菜单展示时支持键盘 ↑ ↓ 导航与 Enter / Tab 快速补全选中
      if (isPopoverVisible && filtered.length > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          const nextIdx = (state.slashHighlightIndex + 1) % filtered.length;
          highlightSlashItem(nextIdx);
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          const prevIdx = (state.slashHighlightIndex - 1 + filtered.length) % filtered.length;
          highlightSlashItem(prevIdx);
          return;
        }
        if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey) {
          e.preventDefault();
          const chosen = filtered[state.slashHighlightIndex] || filtered[0];
          if (chosen) {
            window.appActions.selectSlashCommand(chosen.command);
          }
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          state.slashMenuOpen = false;
          popover.classList.add("hidden");
          return;
        }
      }

      if (e.key === "Enter" && !e.shiftKey && window.innerWidth >= 768) {
        e.preventDefault();
        const btn = document.getElementById("prompt-action-btn");
        if (btn && btn.dataset.action === "abort") {
          window.appActions.abortSession();
        } else {
          window.appActions.sendPrompt();
        }
      }
    });

    promptInput.addEventListener("input", () => {
      window.appActions.handlePromptInput();
    });
  }

  // 点击外部收起弹出菜单
  document.addEventListener("click", (e) => {
    const modelPopover = document.getElementById("model-picker-popover");
    const modelBtn = document.getElementById("model-picker-btn");
    const modelArrow = document.getElementById("model-picker-arrow");
    if (modelPopover && !modelPopover.classList.contains("hidden")) {
      if (!modelPopover.contains(e.target) && !modelBtn?.contains(e.target)) {
        modelPopover.classList.add("hidden");
        if (modelBtn) modelBtn.classList.remove("border-sky-500/50", "bg-zinc-800");
        if (modelArrow) modelArrow.classList.remove("rotate-180");
      }
    }

    const popover = document.getElementById("slash-popover");
    const input = document.getElementById("prompt-input");
    if (popover && input && !popover.contains(e.target) && e.target !== input) {
      state.slashMenuOpen = false;
      popover.classList.add("hidden");
    }

    const sessionMenu = document.getElementById("session-action-menu");
    const sessionMenuBtn = document.getElementById("session-options-menu-btn");
    if (sessionMenu && !sessionMenu.classList.contains("hidden")) {
      if (!sessionMenu.contains(e.target) && (!sessionMenuBtn || !sessionMenuBtn.contains(e.target))) {
        sessionMenu.classList.add("hidden");
      }
    }

    const agentFilterMenu = document.getElementById("sidebar-agent-filter-menu");
    const agentFilterBtn = document.getElementById("sidebar-agent-filter-btn");
    if (agentFilterMenu && !agentFilterMenu.classList.contains("hidden")) {
      if (!agentFilterMenu.contains(e.target) && !agentFilterBtn?.contains(e.target)) {
        agentFilterMenu.classList.add("hidden");
        if (agentFilterBtn) agentFilterBtn.setAttribute("aria-expanded", "false");
      }
    }
  });

  // 全局键盘 Escape 键支持一键关闭任何打开的模态框
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const agentFilterMenu = document.getElementById("sidebar-agent-filter-menu");
      const agentFilterBtn = document.getElementById("sidebar-agent-filter-btn");
      if (agentFilterMenu && !agentFilterMenu.classList.contains("hidden")) {
        agentFilterMenu.classList.add("hidden");
        if (agentFilterBtn) agentFilterBtn.setAttribute("aria-expanded", "false");
      }

      const modals = [
        "qr-modal",
        "pairing-guide-modal",
        "new-session-modal",
        "git-status-modal",
        "token-usage-modal",
        "doc-preview-modal",
      ];
      for (const mId of modals) {
        const el = document.getElementById(mId);
        if (el && !el.classList.contains("hidden")) {
          el.classList.add("hidden");
          if (mId === "doc-preview-modal") {
            document.body.style.overflow = "";
          }
        }
      }
    }
  });

  const msgContainer = document.getElementById("messages-container");
  if (msgContainer) {
    let olderPullThrottle = false;
    msgContainer.addEventListener("scroll", () => {
      const distFromBottom =
        msgContainer.scrollHeight - msgContainer.scrollTop - msgContainer.clientHeight;
      state.autoScrollLocked = distFromBottom > 120;
      const jumpBtn = document.getElementById("scroll-bottom-pill");
      if (jumpBtn) {
        jumpBtn.classList.toggle("hidden", !state.autoScrollLocked);
      }
      updateCollapseStepsPillVisibility();
      updateStickyPromptVisibility();

      // 向上翻阅历史记录：当页面向下拉动或滚动至顶部附近时，自动触发加载更早历史记录
      if (msgContainer.scrollTop <= 60 && state.hasMoreMessages && !state.loadingOlderMessages && !olderPullThrottle) {
        olderPullThrottle = true;
        window.appActions.loadOlderMessages();
        setTimeout(() => {
          olderPullThrottle = false;
        }, 1000);
      }
    }, { passive: true });
  }

  // 自适应监测底部控制舱高度（含输入框撑高、移动端软键盘、审批横幅变化），动态更新浮动药丸吸附位置
  const updateBottomControlHeight = () => {
    const footerEl = document.querySelector("footer");
    const approvalBarEl = document.getElementById("bottom-approval-bar");
    let totalBottomHeight = footerEl ? footerEl.offsetHeight : 108;
    if (approvalBarEl && !approvalBarEl.classList.contains("hidden")) {
      totalBottomHeight += approvalBarEl.offsetHeight;
    }
    // 移动端软键盘弹起高度补偿 (VisualViewport)
    if (window.visualViewport) {
      const keyboardOffset = Math.max(0, window.innerHeight - window.visualViewport.height - window.visualViewport.offsetTop);
      if (keyboardOffset > 80) {
        totalBottomHeight += keyboardOffset;
      }
    }
    document.documentElement.style.setProperty("--bottom-control-height", `${totalBottomHeight}px`);
  };
  if (window.appActions) {
    window.appActions.updateBottomControlHeight = updateBottomControlHeight;
  }
  window.updateBottomControlHeight = updateBottomControlHeight;

  const footerEl = document.querySelector("footer");
  if (footerEl && window.ResizeObserver) {
    const bottomObserver = new ResizeObserver(() => updateBottomControlHeight());
    bottomObserver.observe(footerEl);
    const approvalBarEl = document.getElementById("bottom-approval-bar");
    if (approvalBarEl) bottomObserver.observe(approvalBarEl);
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => updateBottomControlHeight());
    window.visualViewport.addEventListener("scroll", () => updateBottomControlHeight());
  }
  updateBottomControlHeight();
}

// 采用规范的 DOM 状态守护：确保 module 脚本异步解析就绪后无论 DOMContentLoaded 是否已触发均可秒级启动
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}

