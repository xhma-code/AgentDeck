/**
 * AgentDeck 二维码配对弹窗与 Token 水位统计视图
 */

import { state } from "./state.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  formatTokenCount,
  copyTextToClipboard,
} from "./utils.js";
import { getBasename } from "./utils.js";
import { getModelContextLimit } from "./session-helper.js";

let qrExpireTimer = null;

export function stopQrExpireCountdown() {
  if (qrExpireTimer) {
    clearTimeout(qrExpireTimer);
    qrExpireTimer = null;
  }
}

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
          <span class="w-1.5 h-1.5 rounded-full ${
            curChannel.type === "wifi" ? "bg-emerald-400" : curChannel.type === "tailscale" ? "bg-sky-400" : "bg-amber-400"
          }"></span>
          <span>${channelTips[state.selectedQrNet] || "扫码或复制链接在手机中打开"}</span>
        </div>
      </div>
    </div>
  `;
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
