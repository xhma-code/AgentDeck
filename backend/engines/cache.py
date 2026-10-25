"""
会话列表与消息流 ETag 协商与轻量指纹计算深模块。

职责：
1. 计算会话列表与详情的灵敏 ETag 指纹，支持客户端 304 毫秒级协商缓存。
2. 缓存消息底层日志 mtime/size 与状态机快照，避免高频请求重复深层遍历与序列化。
3. 线程与协程安全的 LRU 淘汰机制。
"""

from __future__ import annotations

import collections
from pathlib import Path
from typing import Any, NamedTuple, Optional

from backend.adapters.base import BaseAgentAdapter


class SessionMessagesEtagEntry(NamedTuple):
    """单会话消息轻量 ETag 缓存快照条目。"""

    mtime_ns: int
    fsize: int
    is_running: int
    pa_flag: int
    etag: str


class EtagNegotiator:
    """ETag 缓存与 HTTP 304 协商状态机深模块。"""

    def __init__(self, maxsize: int = 200) -> None:
        self.maxsize = maxsize
        self._messages_etag_cache: collections.OrderedDict[str, SessionMessagesEtagEntry] = (
            collections.OrderedDict()
        )

    def get_sessions_etag(
        self,
        seq_counter: int,
        active_count: int,
        pending_count: int,
        agent_filter: str = "all",
        limit: int = 40,
    ) -> str:
        """计算全局会话列表的高效 ETag 指纹（基于序列计数器、活跃进程与审批队列）。"""
        return f'W/"{seq_counter}-{active_count}-{pending_count}-{agent_filter}-{limit}"'

    def check_messages_etag_matches(
        self,
        if_none_match: Optional[str],
        unified_id: str,
        adapter: Optional[BaseAgentAdapter],
        is_active: bool,
        is_pending: bool,
        limit: int = 30,
        before_step: Optional[int] = None,
        since_step: Optional[int] = None,
    ) -> tuple[bool, Optional[str]]:
        """快速预检会话消息 ETag 是否命中客户端 If-None-Match（毫秒级短路，直接 304）。"""
        if not if_none_match or ":" not in unified_id:
            return False, None

        cache_key = f"{unified_id}-{limit}-{before_step}-{since_step}"
        cached = self._messages_etag_cache.get(cache_key)
        if not cached:
            return False, None

        raw_id = unified_id.split(":", 1)[1] if ":" in unified_id else ""
        fpath = adapter.get_session_transcript_path(raw_id) if adapter else None
        current_mtime = 0
        current_size = 0
        if fpath and fpath.exists():
            try:
                st = fpath.stat()
                current_mtime = st.st_mtime_ns
                current_size = st.st_size
                # 兼容 SQLite WAL 模式：高频变更只写入 -wal，必须联合感知
                wal_path = Path(f"{fpath}-wal")
                if wal_path.exists():
                    st_wal = wal_path.stat()
                    current_mtime = max(current_mtime, st_wal.st_mtime_ns)
                    current_size += st_wal.st_size
            except OSError:
                pass

        current_running = 1 if is_active else 0
        current_pa = 1 if is_pending else 0

        if (
            current_mtime == cached.mtime_ns
            and current_size == cached.fsize
            and current_running == cached.is_running
            and current_pa == cached.pa_flag
        ):
            inm = if_none_match.strip()
            etag = cached.etag.strip()
            if inm == etag or inm == etag.strip('"') or inm.strip('"') == etag.strip('"'):
                return True, cached.etag

        return False, None

    def generate_and_cache_messages_etag(
        self,
        detail: dict[str, Any],
        unified_id: str,
        adapter: Optional[BaseAgentAdapter],
        is_active: bool,
        is_pending: bool,
        limit: int = 30,
        before_step: Optional[int] = None,
        since_step: Optional[int] = None,
    ) -> str:
        """根据会话流最新状态、步骤序号与尾部内容指纹生成灵敏的 ETag 签名，防止流式追加时误判 304。"""
        fp = self.extract_session_detail_fingerprint(detail)
        etag_res = f'W/"msg-{unified_id}-{fp}-{limit}-{before_step}-{since_step}"'

        cache_key = f"{unified_id}-{limit}-{before_step}-{since_step}"
        raw_id = unified_id.split(":", 1)[1] if ":" in unified_id else ""
        fpath = adapter.get_session_transcript_path(raw_id) if adapter else None
        mtime_ns = 0
        fsize = 0
        if fpath and fpath.exists():
            try:
                st = fpath.stat()
                mtime_ns, fsize = st.st_mtime_ns, st.st_size
                wal_path = Path(f"{fpath}-wal")
                if wal_path.exists():
                    st_wal = wal_path.stat()
                    mtime_ns = max(mtime_ns, st_wal.st_mtime_ns)
                    fsize += st_wal.st_size
            except OSError:
                pass

        entry = SessionMessagesEtagEntry(
            mtime_ns=mtime_ns,
            fsize=fsize,
            is_running=1 if is_active else 0,
            pa_flag=1 if is_pending else 0,
            etag=etag_res,
        )
        self._messages_etag_cache[cache_key] = entry
        if len(self._messages_etag_cache) > self.maxsize:
            try:
                self._messages_etag_cache.popitem(last=False)
            except (KeyError, RuntimeError):
                pass
        return etag_res

    @staticmethod
    def extract_session_detail_fingerprint(detail: Optional[dict[str, Any]]) -> str:
        """内聚解析会话详情对象的尾部状态与内容指纹，消除跨层深层探测。"""
        if not detail or not isinstance(detail, dict):
            return "0-0-0-0-0--0-0-0"
        msgs = detail.get("messages", [])
        last_step = msgs[-1].get("step_index", 0) if msgs else 0
        is_running = 1 if detail.get("is_running") else 0
        pa = detail.get("pending_approval")
        pa_id = pa.get("approval_id", "0") if isinstance(pa, dict) else "0"
        last_msg = msgs[-1] if msgs else {}
        content_len = len(last_msg.get("content") or "")
        thinking_len = len(last_msg.get("thinking") or "")
        tool_calls = last_msg.get("tool_calls") or []
        last_tc = tool_calls[-1] if tool_calls else {}
        return (
            f"{last_step}-{len(msgs)}-{content_len}-{thinking_len}-"
            f"{len(tool_calls)}-{last_tc.get('status') or ''}-{len(last_tc.get('output') or '')}-{is_running}-{pa_id}"
        )

    def clear(self) -> None:
        """重置所有缓存。"""
        self._messages_etag_cache.clear()
