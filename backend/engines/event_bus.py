"""
AgentDeck 实时事件总线与断线重连环形缓冲区模块。

职责：
1. 维护递增序列号 (seq_id) 与 200 条事件环形缓冲区 (Ring Buffer)。
2. 管理手机端 WebSocket 异步订阅者集合，提供并发安全广播与死亡连接自动剔除。
3. 生成断线重连状态机快照 (SESSION_STATE_SNAPSHOT)，确保移动端断线切回 0 丢失。
"""

from __future__ import annotations

import asyncio
import collections
from typing import Any, Callable, Coroutine, Optional

from backend.core.database import utc_now_iso


class RealtimeEventBus:
    """统一多路复用实时事件广播与重连追赶总线。"""

    def __init__(self, buffer_maxlen: int = 200) -> None:
        self._seq_counter: int = 0
        self._event_buffer: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=buffer_maxlen
        )
        self._subscribers: set[
            Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
        ] = set()

    @property
    def seq_counter(self) -> int:
        return self._seq_counter

    @property
    def event_buffer(self) -> collections.deque[dict[str, Any]]:
        return self._event_buffer

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def bump_seq(self) -> int:
        """显式自增全局事件序号并返回最新版本号。"""
        self._seq_counter += 1
        return self._seq_counter

    def register_subscriber(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        """注册一个 WebSocket 推送回调。"""
        self._subscribers.add(callback)

    def unregister_subscriber(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        """注销 WebSocket 推送回调。"""
        self._subscribers.discard(callback)

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向所有已连接的订阅者安全广播统一封包事件，并计入重连追赶缓冲区。"""
        self._seq_counter += 1
        envelope = {
            "seq_id": self._seq_counter,
            "type": event_type,
            "timestamp": utc_now_iso(),
            "payload": payload,
        }
        self._event_buffer.append(envelope)

        if not self._subscribers:
            return envelope

        subscribers = list(self._subscribers)

        async def _safe_send(
            cb: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
        ) -> Optional[Any]:
            try:
                await asyncio.wait_for(cb(envelope), timeout=1.5)
                return None
            except Exception:
                return cb

        results = await asyncio.gather(
            *[_safe_send(cb) for cb in subscribers], return_exceptions=False
        )
        for dead_cb in results:
            if dead_cb:
                self._subscribers.discard(dead_cb)

        return envelope

    def get_snapshot(
        self,
        since_seq_id: int = 0,
        active_session_ids: Optional[list[str]] = None,
        pending_approvals: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """获取重连或首屏所需的状态快照（支持检测环形缓冲区是否溢出覆盖）。"""
        is_overflow = False
        if since_seq_id > 0 and self._event_buffer:
            oldest_seq = int(self._event_buffer[0].get("seq_id", 0))
            if since_seq_id < oldest_seq:
                is_overflow = True

        missed_events = [
            ev for ev in self._event_buffer if int(ev.get("seq_id", 0)) > since_seq_id
        ]
        return {
            "current_seq_id": self._seq_counter,
            "active_session_ids": active_session_ids or [],
            "pending_approvals": pending_approvals or [],
            "missed_events": missed_events[-50:] if since_seq_id > 0 else [],
            "is_overflow": is_overflow,
        }
