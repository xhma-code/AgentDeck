/**
 * AgentDeck 跨网络通道自愈、漫游与健康探测调度器
 */

import { state } from "./state.js";
import { loadSessions, loadSystemStatus } from "./data.js";

let isRoamingCheckBusy = false;

export function showRoamingBanner(text, isPermanent = false) {
  const banner = document.getElementById("roaming-banner");
  const bannerText = document.getElementById("roaming-banner-text");
  if (!banner || !bannerText) return;
  bannerText.textContent = text;
  banner.classList.remove("hidden");
  if (!isPermanent) {
    setTimeout(() => {
      banner.classList.add("hidden");
    }, 4500);
  }
}

export function hideRoamingBanner() {
  const banner = document.getElementById("roaming-banner");
  if (banner) banner.classList.add("hidden");
}

export async function probeChannelHealth(targetBaseUrl, timeoutMs = 1800) {
  if (!targetBaseUrl) return false;
  const cleanUrl = targetBaseUrl.replace(/\/+$/, "");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${cleanUrl}/healthz?_probe=${Date.now()}`, {
      method: "GET",
      signal: ctrl.signal,
      cache: "no-store",
      mode: "cors",
    });
    clearTimeout(timer);
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      return data.ok === true || res.status === 200;
    }
    return false;
  } catch (_) {
    clearTimeout(timer);
    return false;
  }
}

export async function triggerAutoRoaming(triggerSource = "network", onHotSwapSuccess = null) {
  if (isRoamingCheckBusy) return;
  isRoamingCheckBusy = true;

  try {
    // 1. 先探测当前所在 Origin 是否畅通（放宽至 3000ms，兼容虚拟私网抖动）
    const currentOrigin = window.location.origin;
    const currentAlive = await probeChannelHealth(currentOrigin, 3000);
    if (currentAlive) {
      hideRoamingBanner();
      return;
    }

    // 2. 当前 Origin 不通，读取已持久化的聚合通道列表
    let cachedChannels = [];
    try {
      cachedChannels = JSON.parse(localStorage.getItem("agentdeck_channels") || "[]");
    } catch (_) {
      cachedChannels = [];
    }

    // 过滤出除当前 Origin 之外的其他通道
    const candidates = (cachedChannels || []).filter((c) => {
      if (!c.url) return false;
      try {
        const u = new URL(c.url);
        return u.origin !== currentOrigin;
      } catch (_) {
        return false;
      }
    });

    if (candidates.length === 0) {
      console.warn("未发现可漫游的备用网络通道");
      showRoamingBanner("网络通道不可达，请检查 VPN 网络连接", false);
      return;
    }

    showRoamingBanner("检测到网络通道变动，正在自动漫游探活...", true);

    // 3. 并发探测所有候选通道，取最先响应成功者
    const probePromises = candidates.map(async (c) => {
      const ok = await probeChannelHealth(c.url, 3500);
      if (ok) return c;
      throw new Error("channel offline");
    });

    let winner = null;
    try {
      winner = await Promise.any(probePromises);
    } catch (_) {
      showRoamingBanner("所有通道暂不可达，请检查 VPN 网络连接", false);
      return;
    }

    if (winner && winner.url) {
      showRoamingBanner(`已切换至更优通道：${winner.label}（无感热切中）`, true);
      try {
        const targetUrlObj = new URL(winner.url);
        if (targetUrlObj.host !== window.location.host) {
          state.activeBaseUrl = winner.url.replace(/\/+$/, "");
        } else {
          state.activeBaseUrl = "";
        }
        if (typeof onHotSwapSuccess === "function") {
          onHotSwapSuccess();
        }
        await Promise.all([loadSystemStatus(), loadSessions(true)]);
        setTimeout(hideRoamingBanner, 1600);
        return;
      } catch (hotSwapErr) {
        console.warn("无感热切换失败，降级为页面重定向:", hotSwapErr);
        const token = state.token || localStorage.getItem("agentdeck_token") || "";
        const target = new URL(winner.url);
        if (token) {
          target.searchParams.set("token", token);
        }
        target.hash = window.location.hash || "";
        setTimeout(() => {
          window.location.replace(target.toString());
        }, 200);
      }
    }
  } catch (err) {
    console.error("自动漫游探活异常:", err);
  } finally {
    isRoamingCheckBusy = false;
  }
}
