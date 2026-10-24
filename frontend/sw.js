// AgentDeck PWA Service Worker — 离线 App Shell 缓存与跨网络自愈支持
const CACHE_NAME = "agentdeck-shell-20261024_dom_guardian_v1";

const APP_SHELL = [
  "/",
  "/index.html",
  "/static/app.js",
  "/static/styles.css",
  "/static/tailwind.min.js",
  "/static/manifest.json",
  "/apple-touch-icon.png",
  "/favicon.ico"
];

// 安装时预缓存基础核心外壳
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(APP_SHELL).catch(() => {});
    }).then(() => self.skipWaiting())
  );
});

// 激活时清理旧缓存并立即接管
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((k) => {
          if (k !== CACHE_NAME) {
            return caches.delete(k);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// 请求拦截策略：
// 1. API 路由、WebSocket、健康检查均纯走网络，不作任何缓存
// 2. 带 pair_code / token 查询参数的导航请求不缓存（鉴权一次性凭证，缓存会导致重放）
// 3. 静态页面与脚本资源：Network First（优先网络以保证最新，网络不通时退回缓存离线秒开）
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 动态数据探针与接口绝对不走 ServiceWorker 缓存
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/ws/") ||
    url.pathname.startsWith("/healthz") ||
    req.method !== "GET"
  ) {
    return;
  }

  // 静态 App Shell 采用 Network First with Cache Fallback
  event.respondWith(
    fetch(req)
      .then((networkRes) => {
        if (networkRes && networkRes.status === 200) {
          // 带鉴权参数的导航请求不写入缓存，防止旧凭证污染离线缓存
          const hasSensitiveParams =
            url.searchParams.has("pair_code") ||
            url.searchParams.has("token");
          if (!hasSensitiveParams) {
            const clone = networkRes.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(req, clone);
            });
          }
        }
        return networkRes;
      })
      .catch(() => {
        // 网络超时/不可达时，从缓存中返回（降级使用无参数的干净缓存版本）
        return caches.match(req).then((cachedRes) => {
          if (cachedRes) return cachedRes;
          if (req.mode === "navigate") {
            return caches.match("/") || caches.match("/index.html");
          }
          return new Response("Offline", { status: 503, statusText: "Offline" });
        });
      })
  );
});
