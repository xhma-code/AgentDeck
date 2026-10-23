/**
 * AgentDeck 基础工具、图标系统与辅助函数
 */

import { state } from "./state.js";

export function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function escapeJsSingleQuote(str) {
  return String(str || "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
}

export function cleanFriendlyText(text) {
  if (!text) return "";
  return String(text)
    .replace(/<thinking>[\s\S]*?<\/thinking>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/\[\/?(thought|system|internal)[^\]]*\]/gi, "")
    .replace(/^(\s*[-*+]\s*|\s*\d+\.\s*)/gm, "")
    .replace(/[`#*_~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function formatRelativeTime(dateStr) {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - d.getTime()) / 1000);
    if (diffSec < 45) return "刚刚";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
    if (diffSec < 86400 * 30) return `${Math.floor(diffSec / 86400)}d`;
    return d.toLocaleDateString(undefined, { month: "numeric", day: "numeric" });
  } catch (_) {
    return "";
  }
}

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

export function formatElapsedSec(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const remSec = s % 60;
  return m > 0 ? `${m}m ${remSec}s` : `${remSec}s`;
}

export function getBasename(pathStr) {
  if (!pathStr) return "";
  const normalized = String(pathStr).replace(/\\/g, "/");
  const parts = normalized.split("/");
  return parts[parts.length - 1] || pathStr;
}

export function isLocalPreviewableLink(url) {
  if (!url) return false;
  if (url.startsWith("file://") || url.startsWith("vscode://") || url.startsWith("cursor://")) {
    return true;
  }
  if (url.startsWith("http://") || url.startsWith("https://")) {
    try {
      const parsed = new URL(url);
      const isLocalHost = ["localhost", "127.0.0.1", "0.0.0.0"].includes(parsed.hostname);
      const isStaticPath = parsed.pathname.startsWith("/static/") || parsed.pathname.startsWith("/artifacts/");
      return isLocalHost && isStaticPath;
    } catch (_) {
      return false;
    }
  }
  return false;
}

export async function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      console.warn("navigator.clipboard.writeText 写入失败，降级 textarea:", e);
    }
  }
  const textArea = document.createElement("textarea");
  textArea.value = text;
  textArea.style.position = "fixed";
  textArea.style.left = "-999999px";
  textArea.style.top = "-999999px";
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  let success = false;
  try {
    success = document.execCommand("copy");
  } catch (err) {
    console.error("降级 copyTextToClipboard 异常:", err);
  }
  document.body.removeChild(textArea);
  return success;
}

export function applyButtonCheckmark(btn, options = {}) {
  if (!btn) return;
  const originalHtml = btn.innerHTML;
  const originalTitle = btn.getAttribute("title") || "";
  const successText = options.successText || "已复制";
  const duration = options.duration || 1800;

  btn.innerHTML = `
    <svg class="w-3.5 h-3.5 text-emerald-400 shrink-0 animate-in fade-in zoom-in-75 duration-150" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="20 6 9 17 4 12"></polyline>
    </svg>
    <span class="text-emerald-300 font-medium">${successText}</span>
  `;
  btn.classList.add("text-emerald-400");

  setTimeout(() => {
    btn.innerHTML = originalHtml;
    btn.classList.remove("text-emerald-400");
    if (originalTitle) btn.setAttribute("title", originalTitle);
  }, duration);
}

export function saveToBoundedStore(storeKey, id, data, maxSize = 250) {
  if (!state[storeKey]) state[storeKey] = {};
  state[storeKey][id] = data;
  const keys = Object.keys(state[storeKey]);
  if (keys.length > maxSize) {
    const deleteCount = keys.length - maxSize;
    for (let i = 0; i < deleteCount; i++) {
      delete state[storeKey][keys[i]];
    }
  }
}

export function triggerAlertNotification(type = "approval") {
  if ("vibrate" in navigator) {
    try {
      if (type === "approval") {
        navigator.vibrate([150, 80, 150]);
      } else if (type === "complete") {
        navigator.vibrate([200]);
      } else {
        navigator.vibrate(80);
      }
    } catch (_) {}
  }
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) {
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(type === "approval" ? 587.33 : 880, ctx.currentTime);
      gain.gain.setValueAtTime(0.04, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
      osc.start();
      osc.stop(ctx.currentTime + 0.18);
    }
  } catch (_) {}
}

export function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

export function isLocalHostClient() {
  const h = window.location.hostname;
  return h === "localhost" || h === "127.0.0.1" || h === "::1";
}

export function getSessionIdFromHash() {
  const hash = window.location.hash || "";
  const match = hash.match(/^#session=([^&]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export function updateSessionHash(unifiedId) {
  if (!unifiedId) {
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    return;
  }
  const currentHash = getSessionIdFromHash();
  if (currentHash !== unifiedId) {
    window.history.replaceState(null, "", `#session=${encodeURIComponent(unifiedId)}`);
  }
}

export function computeSessionsSig(sessionList) {
  if (!sessionList || !Array.isArray(sessionList)) return "";
  return sessionList
    .map(
      (s) =>
        `${s.unified_id}:${s.status}:${s.updated_at || ""}:${s.is_pinned ? 1 : 0}:${
          state.sessionLastViewedTimes[s.unified_id] || 0
        }`
    )
    .join("|");
}

export function computeMessagesSig(messages) {
  if (!messages || !Array.isArray(messages)) return "";
  const len = messages.length;
  if (len === 0) return "0";
  const first = messages[0];
  const last = messages[len - 1];
  return `${len}_${first.step_index || 0}_${last.step_index || 0}_${(last.content || "").length}_${
    last.thinking_len || 0
  }`;
}

export function gitPathToSlotId(filePath) {
  let h = 0;
  const s = String(filePath || "");
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return `diff-slot-${Math.abs(h)}`;
}

// 统一标准矢量图标库
export const icons = {
  highRisk: (cls = "w-3 h-3 text-rose-400 shrink-0") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  edit: (cls = "w-3 h-3 text-emerald-300 shrink-0") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`,
  tool: (cls = "w-3 h-3 text-zinc-400 shrink-0") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`,
  thinking: (cls = "w-3 h-3 text-zinc-400 shrink-0") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  brain: (cls = "w-3.5 h-3.5 text-zinc-400 shrink-0") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a8 8 0 0 0-8 8c0 3.2 1.9 6 4.7 7.3V19a2 2 0 0 0 2 2h2.6a2 2 0 0 0 2-2v-1.7c2.8-1.3 4.7-4.1 4.7-7.3a8 8 0 0 0-8-8z"/><path d="M10 22h4"/></svg>`,
  copy: (cls = "w-3 h-3 text-zinc-400") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`,
  chevronDown: (cls = "w-3.5 h-3.5 text-zinc-400") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>`,
  chevronUp: (cls = "w-3.5 h-3.5 text-zinc-400") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="18 15 12 9 6 15"/></svg>`,
  branch: (cls = "w-3 h-3 text-zinc-400") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg>`,
  search: (cls = "w-3 h-3 text-zinc-500") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>`,
  file: (cls = "w-3 h-3 text-zinc-400") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`,
  note: (cls = "w-3 h-3 text-sky-400 shrink-0") =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
};
