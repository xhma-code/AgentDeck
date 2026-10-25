/**
 * AgentDeck 弹窗（文档预览、全屏Diff、Git差异、Token水位、Agent健康度）交互动作集
 */

import { state } from "./state.js";
import { apiFetch, showToast, closePairingGuideModal as baseClosePairingGuide } from "./api.js";
import {
  escapeHtml,
  getBasename,
  gitPathToSlotId,
  copyTextToClipboard,
  applyButtonCheckmark,
} from "./utils.js";
import { renderMarkdownHtml } from "./markdown.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";
import { renderGitStatusBody, updateGitStatusBadge } from "./render-modal-views.js";
export { updateGitStatusBadge };
import { renderTokenUsage, stopQrExpireCountdown } from "./render-system-views.js";
import { renderAgentsHealthModal } from "./render-status-badges.js";
import { loadSystemStatus, loadWorkspaces, loadSessions } from "./data.js";
import { connectWebSocket } from "./network.js";

export function closePairingGuideModal() {
  baseClosePairingGuide();
}

export async function submitPairCodeManual(code) {
  code = (code || "").trim();
  if (!code) {
    showToast("请输入有效配对码", "error");
    return;
  }
  const ua = navigator.userAgent || "Mobile";
  const devName = /iPhone|iPad/i.test(ua) ? "iPhone" : /Android/i.test(ua) ? "Android" : "Web Client";
  try {
    const res = await apiFetch("/api/v1/auth/exchange", {
      method: "POST",
      body: JSON.stringify({ pair_code: code, device_name: devName }),
    });
    if (res.token) {
      setAuthToken(res.token);
      showToast("设备配对成功！", "success");
      baseClosePairingGuide();
      const pwaUrl = new URL(window.location.href);
      pwaUrl.searchParams.delete("pair_code");
      pwaUrl.searchParams.set("token", res.token);
      window.history.replaceState({}, document.title, pwaUrl.pathname + pwaUrl.search + pwaUrl.hash);
      await Promise.all([loadSystemStatus(), loadWorkspaces(), loadSessions()]);
      connectWebSocket();
    }
  } catch (e) {
    showToast(e.message || "配对失败", "error");
  }
}

export function closeQrModal() {
  state.pairedSuccessNotice = null;
  state.selectedQrNet = null;
  state.isRefreshingQr = false;
  stopQrExpireCountdown();
  const m = document.getElementById("qr-modal");
  if (m) m.classList.add("hidden");
}

export async function copyQrUrl(url) {
  if (!url) return;
  const ok = await copyTextToClipboard(url);
  if (ok) {
    showToast("直连地址已复制", "success");
  } else {
    showToast("复制失败，请手动选择复制", "error");
  }
}

export async function openFilePreview(rawPath) {
  if (!rawPath) return;
  const modal = document.getElementById("doc-preview-modal");
  const titleEl = document.getElementById("doc-preview-title");
  const pathEl = document.getElementById("doc-preview-path");
  const bodyEl = document.getElementById("doc-preview-body");
  if (!modal || !bodyEl) return;

  const copyBtn = document.getElementById("doc-preview-copy-btn");
  if (copyBtn) copyBtn.classList.remove("hidden");

  modal.classList.remove("hidden");
  if (titleEl) titleEl.textContent = `📄 ${getBasename(rawPath)}`;
  if (pathEl) pathEl.textContent = rawPath;
  bodyEl.innerHTML = `
    <div class="space-y-3 py-4">
      <div class="skeleton-block h-8 w-1/2"></div>
      <div class="skeleton-block h-32 w-full"></div>
      <div class="skeleton-block h-24 w-5/6"></div>
    </div>
  `;

  try {
    const data = await apiFetch(`/api/v1/files/read?path=${encodeURIComponent(rawPath)}`);
    state.currentDocRawContent = data.content || "";
    if (titleEl) titleEl.textContent = `📄 ${data.filename || getBasename(rawPath)}`;
    if (pathEl) pathEl.textContent = data.path || rawPath;

    if (data.is_markdown) {
      bodyEl.innerHTML = renderMarkdownHtml(data.content, "doc_modal");
    } else {
      const lang = (data.ext || "").replace(/^\./, "") || "text";
      bodyEl.innerHTML = renderMarkdownHtml(
        "```" + lang + "\n" + (data.content || "") + "\n```",
        "doc_modal_code"
      );
    }
  } catch (err) {
    bodyEl.innerHTML = `
      <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
        读取文件失败：${escapeHtml(err.message || String(err))}
      </div>
    `;
  }
}

// 模态框多级视图导航历史栈（支持汇总Diff、全屏Diff等多层视图逐级返回）
let modalViewStack = [];

export function clearModalViewStack() {
  modalViewStack = [];
}

export function closeDocModal() {
  modalViewStack = [];
  const modal = document.getElementById("doc-preview-modal");
  if (modal) modal.classList.add("hidden");
  const copyBtn = document.getElementById("doc-preview-copy-btn");
  if (copyBtn) {
    copyBtn.classList.add("hidden");
    copyBtn.classList.remove("flex");
  }
  document.body.style.overflow = "";
}

export function navigateBackDocModal() {
  if (modalViewStack.length > 0) {
    const prev = modalViewStack.pop();
    if (prev && prev.type === "git_status") {
      // 逐级返回 Git 状态列表页面
      const copyBtn = document.getElementById("doc-preview-copy-btn");
      if (copyBtn) {
        copyBtn.classList.add("hidden");
        copyBtn.classList.remove("flex");
      }
      const titleEl = document.getElementById("doc-preview-title");
      if (titleEl) titleEl.textContent = `🌿 Git 变更与提交状态`;
      const sess =
        (state.allSessions || []).find((s) => s.unified_id === state.selectedSessionId) ||
        (state.sessions || []).find((s) => s.unified_id === state.selectedSessionId);
      const pathEl = document.getElementById("doc-preview-path");
      if (pathEl) pathEl.textContent = sess?.project_path || "";
      if (state.currentGitStatus) {
        renderGitStatusBody(state.currentGitStatus);
      } else {
        openGitStatusModal();
      }
      return;
    }
  }
  // 历史栈已空，直接退出模态框回到会话页面
  closeDocModal();
}

export function openDiffFullscreen(diffText, filePath = "", options = {}) {
  const modal = document.getElementById("doc-preview-modal");
  const titleEl = document.getElementById("doc-preview-title");
  const pathEl = document.getElementById("doc-preview-path");
  const bodyEl = document.getElementById("doc-preview-body");
  if (!modal || !bodyEl) return;

  if (modal.classList.contains("hidden")) {
    modalViewStack = [];
  } else {
    modalViewStack.push({ type: "git_status" });
  }

  state.currentDocRawContent = diffText || "";
  const bName = filePath ? getBasename(filePath) : "代码变更 Diff";
  if (titleEl) titleEl.textContent = `变更 Diff · ${bName}`;
  if (pathEl) pathEl.textContent = filePath || "";

  bodyEl.innerHTML = renderUnifiedDiffHtml(diffText, filePath, {
    isModal: true,
    diffId: options.diffId,
    addCount: options.addCount,
    delCount: options.delCount,
  });
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

export function openDiffFullscreenById(diffId, e) {
  if (e) {
    e.stopPropagation();
    e.preventDefault();
  }
  const entry =
    (state.lazyDetailsStore && state.lazyDetailsStore[diffId]) ||
    (state.diffStore && state.diffStore[diffId]);
  if (!entry) return;
  const diffText = entry.raw || entry.diffText || "";
  const filePath = entry.filePath || "";
  openDiffFullscreen(diffText, filePath, {
    diffId,
    addCount: entry.addCount,
    delCount: entry.delCount,
  });
}

export async function copyCurrentDocContent(btnEl) {
  if (!state.currentDocRawContent) {
    showToast("无可复制内容", "info");
    return;
  }
  const ok = await copyTextToClipboard(state.currentDocRawContent);
  if (ok) {
    if (btnEl) applyButtonCheckmark(btnEl, { iconSize: "w-3.5 h-3.5" });
    showToast("文档内容已复制", "success");
  } else {
    showToast("复制失败，请手动选择复制", "error");
  }
}

export function openTokenUsageModal() {
  const modal = document.getElementById("token-usage-modal");
  if (!modal) return;
  renderTokenUsage();
  modal.classList.remove("hidden");
}

export function closeTokenUsageModal() {
  const modal = document.getElementById("token-usage-modal");
  if (modal) modal.classList.add("hidden");
}

export async function openAgentsHealthModal() {
  // Agent 体检功能已融合至顶部 Agent 下拉菜单中，不再使用独立弹窗
}

export function closeAgentsHealthModal() {}

export async function refreshAgentsHealth() {}

export async function openGitStatusModal() {
  modalViewStack = [];
  const modal = document.getElementById("doc-preview-modal");
  const titleEl = document.getElementById("doc-preview-title");
  const pathEl = document.getElementById("doc-preview-path");
  const bodyEl = document.getElementById("doc-preview-body");
  if (!modal || !bodyEl) return;

  const sess =
    (state.allSessions || []).find((s) => s.unified_id === state.selectedSessionId) ||
    (state.sessions || []).find((s) => s.unified_id === state.selectedSessionId);
  const pPath = sess?.project_path || "";
  if (!pPath) {
    showToast("当前会话无关联工程目录", "info");
    return;
  }

  const copyBtn = document.getElementById("doc-preview-copy-btn");
  if (copyBtn) copyBtn.classList.add("hidden");

  modal.classList.remove("hidden");
  if (titleEl) titleEl.textContent = `🌿 Git 变更与提交状态`;
  if (pathEl) pathEl.textContent = `${pPath}`;
  bodyEl.innerHTML = `
    <div class="space-y-3 py-4">
      <div class="skeleton-block h-8 w-1/2"></div>
      <div class="skeleton-block h-32 w-full"></div>
    </div>
  `;

  try {
    let data = null;
    if (sess?.unified_id) {
      try {
        data = await apiFetch(`/api/v1/sessions/${encodeURIComponent(sess.unified_id)}/modified-files`);
      } catch (_) {}
    }
    if (!data || !data.ok) {
      data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(pPath)}`);
    }
    state.currentGitStatus = data;
    renderGitStatusBody(data);
  } catch (err) {
    bodyEl.innerHTML = `
      <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
        获取 Git 状态失败：${escapeHtml(err.message || String(err))}
      </div>
    `;
  }
}

export function setGitFilesFilter(filter) {
  state.gitFilesFilter = filter;
  if (state.currentGitStatus) {
    renderGitStatusBody(state.currentGitStatus);
  }
}

export async function toggleGitFileDiff(relPath) {
  const isExp = !state.gitExpandedDiffs[relPath];
  state.gitExpandedDiffs[relPath] = isExp;
  const container = document.getElementById(gitPathToSlotId(relPath));
  if (!container) return;
  if (!isExp) {
    container.innerHTML = "";
    return;
  }

  if (state.gitDiffCache[relPath]) {
    container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
    return;
  }

  const fObj = state.currentGitStatus?.files?.find((f) => f.path === relPath);
  const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
  const pPath = state.currentGitStatus?.project_path || sess?.project_path || "";

  container.innerHTML = `<div class="p-2 text-xs text-sky-400">正在加载 Diff...</div>`;
  try {
    const res = await apiFetch(
      `/api/v1/git/diff?project_path=${encodeURIComponent(pPath)}&file_path=${encodeURIComponent(relPath)}`
    );
    if (res?.diff) {
      state.gitDiffCache[relPath] = res.diff;
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
      return;
    }
  } catch (_) {}

  if (fObj?.diff_preview) {
    state.gitDiffCache[relPath] = fObj.diff_preview;
    container.innerHTML = renderUnifiedDiffHtml(fObj.diff_preview, relPath);
    return;
  }

  container.innerHTML = `<div class="p-2 text-xs text-zinc-500">该文件变更已被提交或工作区无文本差异</div>`;
}

export async function viewFullGitDiff() {
  const sess =
    (state.allSessions || []).find((s) => s.unified_id === state.selectedSessionId) ||
    (state.sessions || []).find((s) => s.unified_id === state.selectedSessionId);
  const bodyEl = document.getElementById("doc-preview-body");
  const titleEl = document.getElementById("doc-preview-title");
  if (!bodyEl || !sess?.project_path) {
    showToast("当前会话无关联工程目录", "info");
    return;
  }

  // 压入上一级视图（Git 变更与提交状态列表），实现逐级返回
  modalViewStack.push({ type: "git_status" });

  if (titleEl) titleEl.textContent = `🌿 汇总 Diff`;
  bodyEl.innerHTML = `<div class="p-4 text-xs text-sky-400">正在生成汇总 Diff...</div>`;
  try {
    const res = await apiFetch(
      `/api/v1/git/diff?project_path=${encodeURIComponent(sess.project_path)}`
    );
    let rawDiff = res.diff || "";
    // 若工作区未提交 diff 为空，但当前有修改记录，汇总会话中的 diff 预览
    if (!rawDiff && state.currentGitStatus?.files) {
      const parts = [];
      for (const f of state.currentGitStatus.files) {
        if (f.diff_preview) {
          parts.push(`diff --git a/${f.path} b/${f.path}\n--- a/${f.path}\n+++ b/${f.path}\n${f.diff_preview}`);
        }
      }
      if (parts.length > 0) {
        rawDiff = parts.join("\n\n");
      }
    }

    state.currentDocRawContent = rawDiff;
    const copyBtn = document.getElementById("doc-preview-copy-btn");
    if (copyBtn && rawDiff) {
      copyBtn.classList.remove("hidden");
      copyBtn.classList.add("flex");
    }

    const lines = rawDiff ? rawDiff.split("\n") : [];
    const isHuge = lines.length > 400;
    const displayDiff = isHuge && !state.expandHugeFullDiff ? lines.slice(0, 400).join("\n") : rawDiff;

    bodyEl.innerHTML = `
      <div class="space-y-2">
        <div class="flex items-center justify-between pb-2 border-b border-white/[0.06]">
          <span class="text-xs text-zinc-400">汇总 Diff ${
            res.is_truncated ? "（已截断展示前 48KB）" : ""
          }</span>
          <button onclick="window.appActions.navigateBackDocModal()" class="text-xs text-sky-400 hover:underline">返回文件列表</button>
        </div>
        ${renderUnifiedDiffHtml(displayDiff || "无代码修改差异")}
        ${
          isHuge && !state.expandHugeFullDiff
            ? `<div class="p-3 text-center bg-zinc-900/60 rounded-xl border border-zinc-800 text-xs text-zinc-400">
                已折叠剩余 ${lines.length - 400} 行以保证移动端流畅度。
                <button onclick="state.expandHugeFullDiff = true; window.appActions.viewFullGitDiff()" class="text-sky-400 underline font-medium ml-1">
                  仍要展示全部
                </button>
              </div>`
            : ""
        }
      </div>
    `;
  } catch (e) {
    bodyEl.innerHTML = `<div class="p-4 text-xs text-rose-400">加载汇总 Diff 失败</div>`;
  }
}


export const modalActions = {
  closePairingGuideModal,
  submitPairCodeManual,
  closeQrModal,
  copyQrUrl,
  openFilePreview,
  closeDocModal,
  openDiffFullscreen,
  openDiffFullscreenById,
  copyCurrentDocContent,
  openTokenUsageModal,
  closeTokenUsageModal,
  openAgentsHealthModal,
  closeAgentsHealthModal,
  refreshAgentsHealth,
  openGitStatusModal,
  setGitFilesFilter,
  toggleGitFileDiff,
  viewFullGitDiff,
  updateGitStatusBadge,
  navigateBackDocModal,
};
