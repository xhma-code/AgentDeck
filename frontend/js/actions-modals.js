/**
 * AgentDeck 弹窗（文档预览、全屏Diff、Git差异、Token水位、Agent健康度）交互动作集
 */

import { state } from "./state.js";
import { apiFetch, showToast, closePairingGuideModal } from "./api.js";
import {
  escapeHtml,
  escapeJsSingleQuote,
  getBasename,
  gitPathToSlotId,
  copyTextToClipboard,
  applyButtonCheckmark,
} from "./utils.js";
import { renderMarkdownHtml } from "./markdown.js";
import { renderUnifiedDiffHtml } from "./diff-viewer.js";
import { renderGitStatusBody } from "./render-modal-views.js";
import { renderTokenUsage, stopQrExpireCountdown } from "./render-system-views.js";
import { renderAgentsHealthModal } from "./render-status-badges.js";
import { loadSystemStatus, loadWorkspaces, loadSessions } from "./data.js";
import { connectWebSocket } from "./network.js";

export const modalActions = {
  closePairingGuideModal() {
    closePairingGuideModal();
  },
  async submitPairCodeManual(code) {
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
        state.token = res.token;
        localStorage.setItem("agentdeck_token", res.token);
        showToast("设备配对成功！", "success");
        closePairingGuideModal();
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
  },
  closeQrModal() {
    state.pairedSuccessNotice = null;
    stopQrExpireCountdown();
    const m = document.getElementById("qr-modal");
    if (m) m.classList.add("hidden");
  },
  async copyQrUrl(url) {
    if (!url) return;
    const ok = await copyTextToClipboard(url);
    if (ok) {
      showToast("直连地址已复制", "success");
    } else {
      showToast("复制失败，请手动选择复制", "error");
    }
  },
  async openFilePreview(rawPath) {
    if (!rawPath) return;
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

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
  },
  closeDocModal() {
    const modal = document.getElementById("doc-preview-modal");
    if (modal) modal.classList.add("hidden");
    document.body.style.overflow = "";
  },
  openDiffFullscreen(diffText, filePath = "", options = {}) {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

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
  },
  openDiffFullscreenById(diffId, e) {
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
    modalActions.openDiffFullscreen(diffText, filePath, {
      diffId,
      addCount: entry.addCount,
      delCount: entry.delCount,
    });
  },
  async copyCurrentDocContent(btnEl) {
    if (!state.currentDocRawContent) {
      showToast("无可复制内容", "info");
      return;
    }
    const ok = await copyTextToClipboard(state.currentDocRawContent);
    if (ok) {
      applyButtonCheckmark(btnEl, { label: "已复制全篇" });
      showToast("文档内容已复制", "success");
    } else {
      showToast("复制失败，请手动选择复制", "error");
    }
  },
  openTokenUsageModal() {
    const modal = document.getElementById("token-usage-modal");
    if (!modal) return;
    renderTokenUsage();
    modal.classList.remove("hidden");
  },
  closeTokenUsageModal() {
    const modal = document.getElementById("token-usage-modal");
    if (modal) modal.classList.add("hidden");
  },
  openAgentsHealthModal() {
    const modal = document.getElementById("agents-health-modal");
    if (!modal) return;
    renderAgentsHealthModal();
    modal.classList.remove("hidden");
  },
  closeAgentsHealthModal() {
    const modal = document.getElementById("agents-health-modal");
    if (modal) modal.classList.add("hidden");
  },
  async openGitStatusModal() {
    const modal = document.getElementById("doc-preview-modal");
    const titleEl = document.getElementById("doc-preview-title");
    const pathEl = document.getElementById("doc-preview-path");
    const bodyEl = document.getElementById("doc-preview-body");
    if (!modal || !bodyEl) return;

    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    const pPath = sess?.project_path || "";
    if (!pPath) {
      showToast("当前会话无关联工程目录", "info");
      return;
    }

    modal.classList.remove("hidden");
    if (titleEl) titleEl.textContent = `🌿 Git 工作区变更`;
    if (pathEl) pathEl.textContent = `${pPath}`;
    bodyEl.innerHTML = `
      <div class="space-y-3 py-4">
        <div class="skeleton-block h-8 w-1/2"></div>
        <div class="skeleton-block h-32 w-full"></div>
      </div>
    `;

    try {
      const data = await apiFetch(`/api/v1/git/status?project_path=${encodeURIComponent(pPath)}`);
      state.currentGitStatus = data;
      renderGitStatusBody(data);
    } catch (err) {
      bodyEl.innerHTML = `
        <div class="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200">
          获取 Git 状态失败：${escapeHtml(err.message || String(err))}
        </div>
      `;
    }
  },
  async toggleGitFileDiff(relPath) {
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

    container.innerHTML = `<div class="p-2 text-xs text-sky-400">正在加载 Diff...</div>`;
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    try {
      const res = await apiFetch(
        `/api/v1/git/diff?project_path=${encodeURIComponent(
          sess?.project_path || ""
        )}&file_path=${encodeURIComponent(relPath)}`
      );
      state.gitDiffCache[relPath] = res.diff || "无文本变更";
      container.innerHTML = renderUnifiedDiffHtml(state.gitDiffCache[relPath], relPath);
    } catch (err) {
      container.innerHTML = `<div class="p-2 text-xs text-rose-400">加载 Diff 失败</div>`;
    }
  },
  async viewFullGitDiff() {
    const sess = state.sessions.find((s) => s.unified_id === state.selectedSessionId);
    const bodyEl = document.getElementById("doc-preview-body");
    if (!bodyEl || !sess?.project_path) return;
    bodyEl.innerHTML = `<div class="p-4 text-xs text-sky-400">正在生成全量 Unified Diff...</div>`;
    try {
      const res = await apiFetch(
        `/api/v1/git/diff?project_path=${encodeURIComponent(sess.project_path)}`
      );
      state.currentDocRawContent = res.diff || "";
      const rawDiff = res.diff || "";
      const lines = rawDiff.split("\n");
      const isHuge = lines.length > 400;
      const displayDiff = isHuge && !state.expandHugeFullDiff ? lines.slice(0, 400).join("\n") : rawDiff;

      bodyEl.innerHTML = `
        <div class="space-y-2">
          <div class="flex items-center justify-between pb-2 border-b border-zinc-800">
            <span class="text-xs text-zinc-400">全局未提交修改 Diff ${
              res.is_truncated ? "（已截断展示前 48KB）" : ""
            }</span>
            <button onclick="window.appActions.openGitStatusModal()" class="text-xs text-sky-400 hover:underline">返回文件列表</button>
          </div>
          ${renderUnifiedDiffHtml(displayDiff || "无未提交代码改动")}
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
      bodyEl.innerHTML = `<div class="p-4 text-xs text-rose-400">加载失败</div>`;
    }
  },
};
