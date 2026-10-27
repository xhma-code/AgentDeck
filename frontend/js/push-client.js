// -*- coding: utf-8 -*-
/**
 * AgentDeck iOS PWA 原生 Web Push 客户端模块。
 * 负责 VAPID 公钥获取、PushManager 订阅注册与 iOS 添加到主屏幕引导。
 */

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export const PushClient = {
  isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  },

  isStandalone() {
    return window.navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;
  },

  isSupported() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  },

  async initPushNotifications() {
    if (!this.isSupported()) {
      return { supported: false, reason: "Browser does not support PushManager" };
    }

    // iOS 必须添加到主屏幕后才支持 Web Push
    if (this.isIOS() && !this.isStandalone()) {
      console.log("[PushClient] iOS 需先将网页添加到主屏幕方可开启原生推送");
      return { supported: false, isIOSBrowser: true };
    }

    try {
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();

      if (!sub) {
        // 请求权限
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          return { ok: false, permission };
        }

        // 获取服务端 VAPID 公钥
        const res = await fetch("/api/v1/push/vapid-public-key");
        if (!res.ok) throw new Error("获取 VAPID 公钥失败");
        const { public_key } = await res.json();

        const applicationServerKey = urlBase64ToUint8Array(public_key);
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationServerKey
        });
      }

      // 上报给后端持久化
      const token = localStorage.getItem("agentdeck_token") || "";
      await fetch("/api/v1/push/subscribe", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: jsonSafeStringify({
          subscription: sub.toJSON(),
          user_agent: navigator.userAgent
        })
      });

      console.log("[PushClient] Web Push 原生推送订阅就绪！");
      return { ok: true, subscription: sub };
    } catch (err) {
      console.warn("[PushClient] 注册 Web Push 异常:", err);
      return { ok: false, error: err.message };
    }
  }
};

function jsonSafeStringify(obj) {
  try {
    return JSON.stringify(obj);
  } catch (e) {
    return "{}";
  }
}
