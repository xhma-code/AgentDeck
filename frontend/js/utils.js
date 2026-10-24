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

export function cleanFriendlyText(text) {
  if (!text) return "";
  const str = String(text).trim();
  const m = str.match(/^\[已拒绝工具\s+[^\]]+\]\s*(?:请勿执行刚才的操作，按以下要求调整方案：)?\s*(.*)$/);
  if (m) {
    const feedback = m[1] ? m[1].trim() : "";
    return feedback ? `指导: ${feedback}` : "已拒绝执行该工具";
  }
  return str;
}
const cleanFriendlyTitle = cleanFriendlyText;

export function formatRelativeTime(isoStr) {
  if (!isoStr) return "";
  const dt = new Date(isoStr);
  if (isNaN(dt.getTime())) return isoStr.slice(5, 16);
  const diffSec = Math.floor((Date.now() - dt.getTime()) / 1000);
  if (diffSec < 60) return "刚刚";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
  return `${Math.floor(diffSec / 86400)}d`;
}


export function escapeJsSingleQuote(str) {
  return String(str || "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/"/g, "&quot;")
    .replace(/\r/g, "")
    .replace(/\n/g, "\\n");
}

export function getBasename(rawPath) {
  const clean = String(rawPath || "")
    .split("#")[0]
    .split("?")[0]
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
  const parts = clean.split("/").filter(Boolean);
  return parts[parts.length - 1] || clean || "文档";
}

export function isLocalPreviewableLink(href) {
  const h = String(href || "").trim();
  if (!h) return false;
  if (h.startsWith("file://")) return true;
  if (/^[a-zA-Z]:[\\/]/.test(h)) return true;
  if (/\.(md|markdown|mdx|py|js|ts|tsx|json|yaml|yml|toml|html|css|sh|sql|txt)(#L\d+.*)?$/i.test(h) && !/^https?:\/\//i.test(h)) {
    return true;
  }
  return false;
}



export async function copyTextToClipboard(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {}
  }
  try {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.left = "-9999px";
    textArea.style.top = "-9999px";
    textArea.style.opacity = "0";
    textArea.setAttribute("readonly", "");
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    textArea.setSelectionRange(0, text.length);
    const successful = document.execCommand("copy");
    document.body.removeChild(textArea);
    if (successful) return true;
  } catch (_) {}
  return false;
}

export function saveToBoundedStore(storeName, key, value, maxItems = 150) {
  if (!state[storeName]) {
    state[storeName] = {};
  }
  const store = state[storeName];
  store[key] = value;
  const keys = Object.keys(store);
  if (keys.length > maxItems) {
    const pruneCount = Math.floor(maxItems / 4);
    for (let i = 0; i < pruneCount; i++) {
      delete store[keys[i]];
    }
  }
}

export function applyButtonCheckmark(btnEl, options = {}) {
  if (!btnEl) return;
  const label = options.label || "已复制";
  const iconSize = options.iconSize || "w-3.5 h-3.5";
  const highlightBorder = !!options.highlightBorder;
  const duration = options.duration || 1600;

  if (btnEl._copyResetTimer) {
    clearTimeout(btnEl._copyResetTimer);
  } else {
    btnEl._oldCopyHtml = btnEl.innerHTML;
  }

  btnEl.innerHTML = `
    <svg class="${iconSize} text-emerald-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
    <span class="text-emerald-300 font-medium">${escapeHtml(label)}</span>
  `;

  if (highlightBorder) {
    btnEl.classList.add("border-emerald-500/40", "bg-emerald-500/10");
  }

  btnEl._copyResetTimer = setTimeout(() => {
    if (btnEl._oldCopyHtml !== undefined) {
      btnEl.innerHTML = btnEl._oldCopyHtml;
      delete btnEl._oldCopyHtml;
    }
    if (highlightBorder) {
      btnEl.classList.remove("border-emerald-500/40", "bg-emerald-500/10");
    }
    delete btnEl._copyResetTimer;
  }, duration);
}



export function triggerAlertNotification(title, body) {
  if ("vibrate" in navigator) {
    try {
      navigator.vibrate([200, 100, 200]);
    } catch (_) {}
  }
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      new Notification(title, { body });
    } catch (_) {}
  }
}

export function requestNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}



export function isLocalHostClient() {
  const h = window.location.hostname;
  return h === "127.0.0.1" || h === "localhost" || h === "::1";
}


export function computeSessionsSig(sessions, approvals) {
  const sPart = (sessions || [])
    .map(
      (s) =>
        `${s.unified_id}:${s.updated_at}:${s.status}:${s.is_pinned ? 1 : 0}:${s.title}:${s.execution_mode}:${s.model_name}`
    )
    .join("|");
  const aPart = (approvals || []).map((a) => a.request_id).join(",");
  return `${sPart}#${aPart}`;
}

export function getSessionIdFromHash() {
  const hash = window.location.hash || "";
  const match = hash.match(/(?:^|[#&])session=([^&]+)/);
  if (match && match[1]) {
    try {
      return decodeURIComponent(match[1]);
    } catch {
      return match[1];
    }
  }
  return null;
}

export function updateSessionHash(unifiedId) {
  if (!unifiedId) return;
  const targetHash = `session=${encodeURIComponent(unifiedId)}`;
  const currentHash = window.location.hash.replace(/^#/, "");
  if (currentHash !== targetHash) {
    history.replaceState(null, "", `#${targetHash}`);
  }
}


export function computeMessagesSig(messages, hasMore, totalCount) {
  if (!messages || messages.length === 0) return `0:${hasMore}:${totalCount}`;
  const first = messages[0];
  const last = messages[messages.length - 1];
  const lastTcLen = Array.isArray(last.tool_calls) ? last.tool_calls.length : 0;
  return `${messages.length}:${first.step_index}:${last.step_index}:${(last.content || "").length}:${(last.thinking || "").length}:${lastTcLen}:${hasMore}:${totalCount}`;
}


export function formatElapsedSec(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
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


export function gitPathToSlotId(relPath) {
  let hash = 0;
  const str = String(relPath || "");
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return "git_diff_slot_" + (hash >>> 0).toString(16);
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
