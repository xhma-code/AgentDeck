/**
 * AgentDeck 会话真相状态渲染模块 (S1 状态机前端单渲染点)
 *
 * 状态来源：后端 t_unified_session_meta.status 列，经以下三条通道到达前端：
 * 1. 会话列表/详情 API 的 `lifecycle` 字段（REST 聚合透出）；
 * 2. WS `SESSION_STATUS_CHANGED` 事件（瞬态广播，不进追赶缓冲）；
 * 3. WS `SESSION_STATE_SNAPSHOT.payload.session_statuses`（重连全量对账）。
 *
 * UI 映射（设计评审 Pass 2 七态四格表，DESIGN.md token 对齐）：
 * - creating : 灰骨架「启动中」(sky 脉冲) —— prompt 气泡正常显示，可发消息
 * - live     : 无徽标（正常态）
 * - degraded : amber「直连」徽标 + header 横幅（底层创建失败，本轮走 CLI 直连，可重试）
 * - dead     : rose「已结束」徽标（列表收进归档组，输入禁用）
 * - queued   : zinc「排队中」徽标（由 SESSION_QUEUED 瞬态事件驱动）
 *
 * 安全约定：所有用户侧/后端侧字段一律通过 createElement + textContent 写入，
 * 严禁 innerHTML 拼接未转义内容（防 XSS，工程评审 D17）。
 */

export const SESSION_LIFECYCLE = {
  creating: { label: "启动中", token: "sky", tip: "Agent 正在启动…" },
  live: null,
  degraded: { label: "直连", token: "amber", tip: "底层服务未响应，已自动改用备用方式连接" },
  dead: { label: "已结束", token: "rose", tip: "会话已结束" },
  queued: { label: "排队中", token: "zinc", tip: "达到同时运行上限，本会话将自动开始" },
};

/** 读取会话对象当前真相状态（缺省 live） */
export function getLifecycleStatus(s) {
  if (!s) return "live";
  const st = String(s.lifecycle || "").toLowerCase();
  return Object.prototype.hasOwnProperty.call(SESSION_LIFECYCLE, st) ? st : "live";
}

/**
 * 渲染真相状态徽标。非创建/降级/结束/排队态返回 null（live 不渲染徽标，保持列表安静）。
 * 返回: HTMLElement | null
 */
export function renderLifecycleBadge(s) {
  const st = getLifecycleStatus(s);
  const meta = SESSION_LIFECYCLE[st];
  if (!meta) return null;

  const badge = document.createElement("span");
  badge.className =
    `inline-flex items-center gap-1 px-1 py-0.5 rounded border text-[11px] font-mono leading-none shrink-0 ` +
    `min-h-[20px] md:min-w-0`;
  // ≥40px 触控热区由父级会话卡片承担；徽标自身保持微标签规格（mono 11px，DESIGN.md §3）

  if (st === "creating") {
    badge.className +=
      " bg-sky-500/10 border-sky-400/30 text-sky-300";
    const dot = document.createElement("span");
    dot.className = "w-1 h-1 rounded-full bg-sky-400 animate-pulse";
    const txt = document.createElement("span");
    txt.textContent = meta.label;
    badge.append(dot, txt);
  } else {
    const colorMap = {
      amber: " bg-amber-500/10 border-amber-400/30 text-amber-300",
      rose: " bg-rose-500/10 border-rose-400/30 text-rose-300",
      zinc: " bg-zinc-500/10 border-zinc-400/30 text-zinc-300",
    };
    badge.className += colorMap[meta.token] || colorMap.zinc;
    badge.textContent = meta.label;
  }
  badge.setAttribute("title", meta.tip);
  badge.setAttribute("aria-label", meta.tip);
  return badge;
}

/**
 * 渲染 degraded / dead 会话 header 横幅。live/creating 返回 null。
 * degraded 文案（DX D19 三要素：发生什么/为何/如何恢复）：
 *   「OpenCode 服务未响应，已自动改用备用方式连接。会话可正常使用，点击重试恢复完整功能。」
 * dead：reason 枚举 (crash/timeout/killed) 随 payload.reason 携带。
 */
export function renderLifecycleBanner(s, onRetry) {
  const st = getLifecycleStatus(s);
  if (st !== "degraded" && st !== "dead") return null;

  const banner = document.createElement("div");
  banner.className =
    "flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] leading-snug border mb-2";
  banner.setAttribute("role", "status");
  banner.setAttribute("aria-live", "polite");

  if (st === "degraded") {
    banner.className += " bg-amber-500/10 border-amber-400/30 text-amber-300";
    const msg = document.createElement("span");
    msg.className = "flex-1";
    msg.textContent =
      "OpenCode 服务未响应，已自动改用备用方式连接。会话可正常使用，点击重试恢复完整功能。";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "px-2.5 py-1.5 rounded border border-amber-400/40 bg-amber-500/15 text-amber-200 hover:bg-amber-500/25 active:scale-95 transition text-[11px] font-mono shrink-0";
    btn.textContent = "重试";
    btn.setAttribute("aria-label", "重试恢复底层服务连接");
    const minTouch = btn; // 移动端热区补强
    minTouch.style.minHeight = "40px";
    minTouch.style.minWidth = "40px";
    if (typeof onRetry === "function") btn.onclick = onRetry;
    banner.append(msg, btn);
  } else {
    banner.className += " bg-rose-500/10 border-rose-400/30 text-rose-300";
    const reasonText = (() => {
      const r = String((s && s.reason) || "").toLowerCase();
      if (r.includes("crash")) return "底层进程异常崩溃";
      if (r.includes("timeout")) return "空闲超长未响应";
      if (r.includes("kill")) return "被手动终止";
      return "";
    })();
    const msg = document.createElement("span");
    msg.className = "flex-1";
    msg.textContent =
      "会话已结束" + (reasonText ? `（${reasonText}）` : "") + "。可重新发送新指令唤醒或创建新会话。";
    banner.appendChild(msg);
  }
  return banner;
}
