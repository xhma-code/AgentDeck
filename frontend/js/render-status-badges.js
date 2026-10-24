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

export { renderCompactSessionItem, sortSessions } from "./render-session-card.js";
import { renderCompactSessionItem, sortSessions } from "./render-session-card.js";
