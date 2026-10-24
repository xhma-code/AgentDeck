/**
 * AgentDeck 本地离线可靠命令队列管理器 (IndexedDB + WebSocket 全双工通道)
 * 
 * 核心机制：
 * 1. 手机端所有操作（置顶、重命名、发送、中断、新建、删除）先入库 IndexedDB（状态为 pending）。
 * 2. 若 WebSocket 连接正常，立即封装带 client_msg_id 的帧发出。
 * 3. 若断网或切后台，待网络恢复与 WS 重新握手后，自动按时间序回放队列中所有 pending 任务。
 * 4. 收到服务端的 COMMAND_ACK 回执后，从 IndexedDB 中安全出队，杜绝丢包。
 */

import { state } from "./state.js";
import { showToast } from "./api.js";

const DB_NAME = "agentdeck_offline_db";
const DB_VERSION = 1;
const STORE_NAME = "commands";

let dbPromise = null;
const pendingCallbacks = new Map(); // client_msg_id -> { resolve, reject, timer }

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (!("indexedDB" in window)) {
      console.warn("[CommandQueue] 当前浏览器不支持 IndexedDB，降级为纯内存处理");
      resolve(null);
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (ev) => {
      const db = ev.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "client_msg_id" });
        store.createIndex("created_at", "created_at", { unique: false });
        store.createIndex("status", "status", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => {
      console.error("[CommandQueue] 打开 IndexedDB 失败:", req.error);
      resolve(null);
    };
  });
  return dbPromise;
}

export async function putCommand(cmd) {
  const db = await openDB();
  if (!db) return;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      store.put(cmd);
      tx.oncomplete = () => {
        updateQueueBadge();
        resolve();
      };
      tx.onerror = () => resolve();
    } catch (_) {
      resolve();
    }
  });
}

export async function removeCommand(clientMsgId) {
  const db = await openDB();
  if (!db) return;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      store.delete(clientMsgId);
      tx.oncomplete = () => {
        updateQueueBadge();
        resolve();
      };
      tx.onerror = () => resolve();
    } catch (_) {
      resolve();
    }
  });
}

export async function getAllPendingCommands() {
  const db = await openDB();
  if (!db) return [];
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readonly");
      const store = tx.objectStore(STORE_NAME);
      const req = store.getAll();
      req.onsuccess = () => {
        const list = req.result || [];
        list.sort((a, b) => (a.created_at || 0) - (b.created_at || 0));
        resolve(list);
      };
      req.onerror = () => resolve([]);
    } catch (_) {
      resolve([]);
    }
  });
}

export async function updateQueueBadge() {
  const pending = await getAllPendingCommands();
  state.pendingCommandCount = pending.length;

  const badgeEl = document.getElementById("offline-queue-badge");
  if (badgeEl) {
    if (pending.length > 0 && !state.wsConnected) {
      badgeEl.textContent = `离线排队 (${pending.length})`;
      badgeEl.classList.remove("hidden");
    } else {
      badgeEl.classList.add("hidden");
    }
  }
}

let localCommandCounter = 0;

/**
 * 调度派发命令
 */
export async function dispatchCommand({ command_type, session_id, payload = {}, wsInstance = null }) {
  localCommandCounter = (localCommandCounter + 1) % 100000;
  const client_msg_id = `cmsg_${Date.now()}_${localCommandCounter}_${Math.random().toString(36).slice(2, 7)}`;
  const cmd = {
    action: "execute_command",
    client_msg_id,
    command_type: command_type.toUpperCase(),
    session_id: session_id || "",
    payload,
    client_timestamp: Date.now(),
    status: "pending",
    created_at: Date.now(),
  };

  // 1. 持久化至本地 IndexedDB 保证断网不丢
  await putCommand(cmd);

  return new Promise((resolve, reject) => {
    // 2. 注册等待 ACK 回执
    const timer = setTimeout(() => {
      // 弱网超时后不立即报错破坏离线队列，仅给提示并挂起等候重连回放
      if (pendingCallbacks.has(client_msg_id)) {
        pendingCallbacks.delete(client_msg_id);
        console.warn(`[CommandQueue] 指令 ${client_msg_id} 等待 ACK 超时，保留在离线队列等候重连回放`);
        resolve({ action: "command_ack", status: "queued_offline", client_msg_id });
      }
    }, 9000);

    pendingCallbacks.set(client_msg_id, { resolve, reject, timer });

    // 3. 尝试通过当前活动的 WebSocket 即时发送
    const activeWs = wsInstance || window._agentDeckActiveWs;
    if (activeWs && activeWs.readyState === WebSocket.OPEN) {
      try {
        activeWs.send(JSON.stringify(cmd));
        console.log(`[CommandQueue] 实时上报指令: ${cmd.command_type} (${client_msg_id})`);
      } catch (err) {
        console.warn(`[CommandQueue] 发送失败，留存本地队列等待重试:`, err);
      }
    } else {
      console.log(`[CommandQueue] 当前处于离线/重连状态，指令已加入本地队列: ${cmd.command_type}`);
      updateQueueBadge();
    }
  });
}

/**
 * 处理服务端回传的 COMMAND_ACK
 */
export async function handleCommandAck(ack) {
  const client_msg_id = ack.client_msg_id;
  if (!client_msg_id) return;

  console.log(`[CommandQueue] 收到命令 ACK: ${client_msg_id} (${ack.status})`);
  // 从本地 IndexedDB 移除已成功完成或明确错误的指令
  await removeCommand(client_msg_id);

  if (pendingCallbacks.has(client_msg_id)) {
    const cb = pendingCallbacks.get(client_msg_id);
    clearTimeout(cb.timer);
    pendingCallbacks.delete(client_msg_id);

    if (ack.status === "error") {
      cb.reject(new Error(ack.error || "指令执行失败"));
    } else {
      cb.resolve(ack);
    }
  } else if (ack.status === "error") {
    showToast(`离线操作同步失败: ${ack.error || "未知原因"}`, "error");
  }
}

/**
 * 网络恢复或 WebSocket 重连成功后，自动按序回放离线队列
 */
export async function flushPendingCommands(ws) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const pending = await getAllPendingCommands();
  if (pending.length === 0) return;

  console.log(`[CommandQueue] 检测到网络已恢复，准备回放 ${pending.length} 条待发指令...`);
  showToast(`正在同步 ${pending.length} 条离线操作...`, "info");

  for (const cmd of pending) {
    try {
      ws.send(JSON.stringify(cmd));
      // 稍微错开 20ms 防止洪水击穿
      await new Promise((r) => setTimeout(r, 20));
    } catch (e) {
      console.warn(`[CommandQueue] 回放指令异常 (${cmd.client_msg_id}):`, e);
      break;
    }
  }
  updateQueueBadge();
}
