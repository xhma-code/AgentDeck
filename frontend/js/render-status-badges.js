/**
 * AgentDeck 顶栏连接指示、侧边栏在线徽标与防休眠状态呈现
 */

import { state } from "./state.js";
import { renderSidebarStatus } from "./render-sidebar.js";
import { escapeHtml } from "./utils.js";

export function renderHeaderStatus() {
  renderSidebarStatus();
  const netBadge = document.getElementById("network-status-badge");
  const healthDot = document.getElementById("header-health-dot");
  const reconnectBanner = document.getElementById("reconnect-banner");

  if (state.wsConnected) {
    const tsOnline = !!state.systemStatus?.tailscale?.is_online;
    const ztOnline = !!state.systemStatus?.tailscale?.zerotier_ip;
    const vpnOnline = tsOnline || ztOnline;
    if (netBadge) {
      netBadge.innerHTML = `<span class="w-2 h-2 rounded-full ${
        vpnOnline ? "bg-emerald-400" : "bg-sky-400"
      }"></span>`;
    }
    if (healthDot) {
      healthDot.className = "w-1.5 h-1.5 rounded-full bg-emerald-400";
    }
    if (reconnectBanner) reconnectBanner.classList.add("hidden");
  } else if (state.wsReconnecting) {
    if (netBadge) {
      netBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>`;
    }
    if (healthDot) {
      healthDot.className = "w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping";
    }
    if (reconnectBanner) reconnectBanner.classList.remove("hidden");
  } else {
    if (netBadge) {
      netBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-zinc-600"></span>`;
    }
    if (healthDot) {
      healthDot.className = "w-1.5 h-1.5 rounded-full bg-zinc-500";
    }
    if (reconnectBanner) reconnectBanner.classList.remove("hidden");
  }
}

export function renderSidebarAwakeLock() {
  const btn = document.getElementById("sidebar-awake-btn");
  const icon = document.getElementById("sidebar-awake-icon");
  if (!btn) return;
  const active = !!state.systemStatus?.awake_lock_active;
  if (active) {
    btn.title = "PC 防休眠锁已开启（点击关闭）";
    if (icon) icon.setAttribute("class", "w-4 h-4 text-emerald-400 transition-colors drop-shadow-[0_0_8px_rgba(52,211,153,0.5)]");
  } else {
    btn.title = "PC 防休眠锁已关闭（点击开启）";
    if (icon) icon.setAttribute("class", "w-4 h-4 text-zinc-500 transition-colors");
  }
}

export function renderSidebarHealthStatus() {
  // 体检功能已融合至 Agent 下拉菜单，此处保留空函数以兼容调用
}

export function renderAgentsHealthModal() {
  // 体检弹窗已移除
}

export { renderCompactSessionItem, sortSessions } from "./render-session-card.js";
