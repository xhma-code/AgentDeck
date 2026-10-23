"""
实时存储与会话日志文件监控深模块 (SafeStorageWatcher)。

职责：
1. 聚合四大 Agent 适配器的日志/数据库监控目录（watch paths）。
2. 使用 Rust notify 内核的 `watchfiles.awatch` 高效异步监听变更。
3. 智能解析受影响的 Agent 类型与会话 unified_id，多态重置适配器短效扫描缓存。
4. 向 RealtimeEventBus 广播 `STORAGE_UPDATED` 事件，驱动前端 0 延迟增量刷新。
5. 包含完善的异常捕获与指数退避自愈重启机制。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional

import watchfiles

from backend.adapters.base import BaseAgentAdapter
from backend.core.logger import log_structured_error, logger
from backend.engines.event_bus import RealtimeEventBus


def is_meaningful_session_storage_change(p_str: str, p_name: str) -> bool:
    """
    白名单谓词：收敛过滤无效临时文件与高频共享内存变动，阻断广播风暴。
    仅当变动命中核心会话存储（如 transcript.jsonl, summaries.db, *.jsonl）时放行。
    """
    lower_name = p_name.lower()
    lower_str = p_str.lower()

    # 1. 严格黑名单排除（临时快照、SQLite 共享内存、锁文件、版本控制与 Python 缓存）
    if any(
        noise in lower_str
        for noise in (
            "tmp_snapshots",
            ".db-shm",
            ".git",
            "__pycache__",
            ".pytest_cache",
            "/scratch/",
            "\\.scratch\\",
        )
    ):
        return False

    if lower_name.endswith((".tmp", ".lock", ".swp", ".bak")):
        return False

    # 2. Antigravity 核心会话载体白名单
    if "antigravity" in lower_str:
        if lower_name == "transcript.jsonl":
            return True
        if "conversation_summaries.db" in lower_name:
            return True
        if lower_name.endswith(".db") and "/conversations/" in lower_str:
            return True
        return False

    # 3. Claude Code 核心会话载体白名单
    if ".claude" in lower_str:
        if lower_name.endswith(".jsonl") and not lower_name.startswith("agent-"):
            return True
        if lower_name.endswith(".json") and "/sessions/" in lower_str:
            return True
        return False

    # 4. OpenCode 核心会话载体白名单
    if "opencode" in lower_str:
        if lower_name.endswith((".json", ".jsonl", ".sqlite", ".db")):
            return True
        return False

    return False


class SafeStorageWatcher:
    """实时本地文件系统变动监听与多态缓存失效深模块。"""

    def __init__(
        self,
        adapters_getter: Callable[[], dict[str, BaseAgentAdapter]],
        event_bus: RealtimeEventBus,
        debounce_ms: int = 600,
        step_ms: int = 250,
    ) -> None:
        self._adapters_getter = adapters_getter
        self.event_bus = event_bus
        self.debounce_ms = debounce_ms
        self.step_ms = step_ms
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def is_running(self) -> bool:
        """检查监听后台协程是否处于活跃运行态。"""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """启动后台监听协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._watch_loop())

    def stop(self) -> None:
        """停止后台监听协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _watch_loop(self) -> None:
        """核心监听与自愈循环。"""
        adapters = self._adapters_getter()
        watch_dirs: list[Path] = []
        for adapter in adapters.values():
            for p in adapter.get_watch_paths():
                if p.exists() and p not in watch_dirs:
                    watch_dirs.append(p)

        if not watch_dirs:
            logger.info("未发现需要监听的 Agent 存储目录，SafeStorageWatcher 待命。")
            return

        logger.info(f"已启动 SafeStorageWatcher 实时文件监听，监控目录数: {len(watch_dirs)}")
        retry_delay = 2.0

        while True:
            try:
                async for changes in watchfiles.awatch(
                    *watch_dirs, debounce=self.debounce_ms, step=self.step_ms
                ):
                    changed_files: list[str] = []
                    affected_agents: set[str] = set()
                    affected_session_ids: set[str] = set()

                    for _, raw_p in changes:
                        p_str = str(raw_p).replace("\\", "/")
                        p_obj = Path(raw_p)

                        # 使用白名单收敛过滤，阻断临时文件、SQLite 共享内存与无关日志导致的广播风暴
                        if not is_meaningful_session_storage_change(p_str, p_obj.name):
                            continue

                        changed_files.append(p_obj.name)

                        lower_p = p_str.lower()
                        if "antigravity-cli" in lower_p:
                            affected_agents.add("agy_cli")
                            if "/brain/" in lower_p:
                                parts = p_str.split("/")
                                for i, seg in enumerate(parts):
                                    if seg.lower() == "brain" and i + 1 < len(parts):
                                        affected_session_ids.add(f"agy_cli:{parts[i + 1]}")
                                        break
                        elif "antigravity" in lower_p:
                            affected_agents.add("agy_ide")
                            if "/brain/" in lower_p:
                                parts = p_str.split("/")
                                for i, seg in enumerate(parts):
                                    if seg.lower() == "brain" and i + 1 < len(parts):
                                        affected_session_ids.add(f"agy_ide:{parts[i + 1]}")
                                        break
                        elif ".claude" in lower_p:
                            affected_agents.add("claude_code")
                            if p_obj.suffix.lower() == ".jsonl" and not p_obj.name.startswith("agent-"):
                                affected_session_ids.add(f"claude_code:{p_obj.stem}")
                        elif "opencode" in lower_p:
                            affected_agents.add("opencode")

                    if not changed_files:
                        continue

                    current_adapters = self._adapters_getter()
                    for ag_key in affected_agents:
                        ad = current_adapters.get(ag_key)
                        if ad:
                            ad.invalidate_cache()

                    await self.event_bus.broadcast(
                        "STORAGE_UPDATED",
                        {
                            "changed_files": changed_files[:10],
                            "affected_agents": list(affected_agents),
                            "affected_session_ids": list(affected_session_ids)[:20],
                        },
                    )
                    retry_delay = 2.0
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log_structured_error(
                    problem="实时文件监听器异常中断，正在尝试自愈重启",
                    cause=str(exc),
                    fix=f"{retry_delay}秒后自动重新连接文件系统监听器。",
                    exc=exc,
                )
                await asyncio.sleep(retry_delay)
