/**
 * AgentDeck API 核心封装、鉴权自愈与配对引导
 */

import { state } from "./state.js";

export function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  const colorCls =
    type === "error"
      ? "bg-rose-950/95 border-rose-500/60 text-rose-200"
      : type === "success"
      ? "bg-emerald-950/95 border-emerald-500/60 text-emerald-200"
      : "bg-zinc-900/95 border-sky-500/50 text-sky-200";
  el.className = `px-3 py-2 rounded-xl border shadow-xl text-xs font-medium transition-all ${colorCls}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 3000);
}


export function showPairingGuideModal(msg) {
  const modal = document.getElementById("pairing-guide-modal");
  if (!modal) return;
  const msgEl = document.getElementById("pairing-guide-msg");
  if (msgEl && msg) msgEl.textContent = msg;
  const input = document.getElementById("manual-pair-code-input");
  if (input && !input.value) {
    try {
      const pCode = new URLSearchParams(window.location.search).get("pair_code");
      if (pCode) input.value = pCode.trim();
    } catch (_) {}
  }
  modal.classList.remove("hidden");
}

export function closePairingGuideModal() {
  const modal = document.getElementById("pairing-guide-modal");
  if (modal) modal.classList.add("hidden");
}


export async function apiFetch(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (state.token) {
    headers["X-AgentDeck-Token"] = state.token;
  }
  const fullUrl = (state.activeBaseUrl && !path.startsWith("http")) ? `${state.activeBaseUrl}${path}` : path;
  const resp = await fetch(fullUrl, { ...options, headers });
  if (resp.status === 304) {
    return { not_modified: true, _etag: resp.headers.get("etag") };
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    if (resp.status === 401 || resp.status === 403) {
      // 401 智能自愈拦截重试：若尚未重试过且非鉴权/状态接口，尝试静默自愈恢复 Token 一次
      if (!options._retried && !path.includes("/auth/") && !path.includes("/system/status")) {
        try {
          const statusResp = await fetch(
            (state.activeBaseUrl && !path.startsWith("http")) ? `${state.activeBaseUrl}/api/v1/system/status` : "/api/v1/system/status",
            { headers: { "Content-Type": "application/json" } }
          );
          if (statusResp.ok) {
            const sData = await statusResp.json().catch(() => ({}));
            if (sData && sData.trusted_client_token) {
              state.token = sData.trusted_client_token;
              try {
                localStorage.setItem("agentdeck_token", sData.trusted_client_token);
              } catch (_) {}
              closePairingGuideModal();
              console.info("[AgentDeck] 401 拦截触发静默自愈成功，自动重试原业务请求");
              return await apiFetch(path, { ...options, _retried: true });
            }
          }
        } catch (_) {}
      }
      showPairingGuideModal();
      throw new Error("请完成设备安全配对");
    }
    const errMsg = data.detail || data.error || `请求失败 (${resp.status})`;
    showToast(errMsg, "error");
    throw new Error(errMsg);
  }
  if (resp.headers.get("etag")) {
    data._etag = resp.headers.get("etag");
  }
  return data;
}


export async function checkAndExchangePairCode() {
  const params = new URLSearchParams(window.location.search);

  // 1. 优先读取跨通道或书签携带的有效 Token
  const urlToken = params.get("token");
  if (urlToken) {
    state.token = urlToken;
    try {
      localStorage.setItem("agentdeck_token", urlToken);
    } catch (_) {}
    closePairingGuideModal();
    return;
  }

  // 2. 携带了一次性配对码进行鉴权置换
  const pairCode = params.get("pair_code");
  if (!pairCode) {
    // 首次进入不盲目弹窗拦截，允许本机或已受信网络静默进入
    return;
  }

  try {
    const ua = navigator.userAgent || "Mobile";
    const devName = /iPhone|iPad/i.test(ua)
      ? "iPhone"
      : /Android/i.test(ua)
      ? "Android"
      : "Web Client";

    const res = await apiFetch("/api/v1/auth/exchange", {
      method: "POST",
      body: JSON.stringify({ pair_code: pairCode, device_name: devName }),
    });
    if (res.token) {
      state.token = res.token;
      localStorage.setItem("agentdeck_token", res.token);
      showToast("设备配对成功！", "success");
      closePairingGuideModal();
      // 将 pair_code 置换为长期 token 保存在地址栏，确保随后添加到主屏幕时 PWA 独立沙盒原生携带该授权 token
      const pwaUrl = new URL(window.location.href);
      pwaUrl.searchParams.delete("pair_code");
      pwaUrl.searchParams.set("token", res.token);
      window.history.replaceState({}, document.title, pwaUrl.pathname + pwaUrl.search + pwaUrl.hash);
    }
  } catch (err) {
    console.warn("配对码置换失败:", err);
    if (!state.token) {
      showPairingGuideModal("该配对码已失效或已过期。请在下方输入电脑上显示的当前配对码。");
    }
  }
}


