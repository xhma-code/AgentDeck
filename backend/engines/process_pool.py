# -*- coding: utf-8 -*-
"""
多 Agent 双向流式执行池 (`AgentOrchestrator`)、工具审批状态机与文件变动实时广播中枢。

遵循架构规范：
1. 维护内存态 `pending_approvals` 快照与 200 条事件环形缓冲区（递增 `seq_id`），支持断线重连即时恢复。
2. Windows 子进程销毁强制使用 `taskkill /F /T /PID <pid>` 递归清理整棵进程树，并设 10 分钟空闲 TTL。
3. 对 `Antigravity IDE` 会话实施防双写脑裂保护，支持一键安全克隆至 `agy_cli` 接力执行。
"""

from __future__ import annotations
from backend.adapters.tool_utils import extract_snippet

import asyncio
import uuid
import collections
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from backend.adapters.antigravity_adapter import AntigravityAdapter
from backend.adapters.base import BaseAgentAdapter, ExecutionMode
from backend.adapters.claude_adapter import ClaudeCodeAdapter
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.core.database import MetaDatabase
from backend.core.logger import PROJECT_ROOT, log_structured_error, logger
from backend.engines.approval import ApprovalCoordinator, PendingApproval
from backend.engines.cache import EtagNegotiator, SessionMessagesEtagEntry
from backend.engines.command_bus import CommandBus
from backend.engines.event_bus import RealtimeEventBus
from backend.engines.model_catalog import (
    AGENT_MODELS,
    fetch_available_models,
    resolve_default_model,
)
from backend.engines.process_utils import TurnContext, kill_windows_process_tree
from backend.engines.session_aggregator import (
    aggregate_all_sessions,
    build_startup_health_report,
    build_system_overview,
    fetch_full_step_output,
    fetch_session_detail,
)
from backend.engines.stream_runner import StreamRunner
from backend.engines.watcher import SafeStorageWatcher

__all__ = [
    "AGENT_MODELS",
    "TurnContext",
    "kill_windows_process_tree",
    "AgentOrchestrator",
    "AgentProcessPool",
]

class AgentOrchestrator:
    """多 Agent 统一调度器、进程执行池与事件中枢。"""

    def __init__(
        self,
        meta_db: MetaDatabase,
        antigravity_ide_dir: Optional[Path] = None,
        antigravity_cli_dir: Optional[Path] = None,
        claude_projects_dir: Optional[Path] = None,
        claude_sessions_dir: Optional[Path] = None,
        opencode_data_dir: Optional[Path] = None,
        adapters: Optional[dict[str, BaseAgentAdapter]] = None,
        max_concurrent_subprocesses: int = 3,
    ) -> None:
        self.meta_db = meta_db
        self.adapters: dict[str, BaseAgentAdapter] = {
            "agy_ide": AntigravityAdapter(agent_type="agy_ide", agent_label="Antigravity IDE", root_dir=antigravity_ide_dir),
            "agy_cli": AntigravityAdapter(agent_type="agy_cli", agent_label="Antigravity CLI", root_dir=antigravity_cli_dir),
            "claude_code": ClaudeCodeAdapter(projects_dir=claude_projects_dir, sessions_dir=claude_sessions_dir),
            "opencode": OpenCodeAdapter(data_dir=opencode_data_dir),
        }
        if adapters:
            self.adapters.update(adapters)
        self.active_processes: dict[str, asyncio.subprocess.Process] = {}
        self.process_last_active: dict[str, float] = {}
        self.active_direct_tasks: dict[str, asyncio.Task[None]] = {}
        self.background_tasks: set[asyncio.Task[Any]] = set()
        self.max_concurrent_subprocesses = max_concurrent_subprocesses
        self.process_semaphore = asyncio.Semaphore(self.max_concurrent_subprocesses)

        self.event_bus = RealtimeEventBus(buffer_maxlen=200)
        self.approval_coordinator = ApprovalCoordinator(
            meta_db=meta_db, event_bus=self.event_bus, adapters_getter=lambda: self.adapters
        )
        self._etag_negotiator = EtagNegotiator(maxsize=256)
        self.storage_watcher = SafeStorageWatcher(
            adapters_getter=lambda: self.adapters, event_bus=self.event_bus, debounce_ms=600, step_ms=250
        )
        self._stream_runner = StreamRunner(
            adapters=self.adapters,
            active_processes=self.active_processes,
            process_last_active=self.process_last_active,
            active_direct_tasks=self.active_direct_tasks,
            process_semaphore=self.process_semaphore,
            broadcast_event_fn=self.broadcast_event,
            request_approval_fn=self.request_mobile_tool_approval,
            abort_session_fn=self.abort_session,
            meta_db=self.meta_db,
            normalize_mode_fn=self.normalize_execution_mode,
            resolve_default_model_fn=self._resolve_default_model,
            bump_version_fn=self.bump_version,
        )
        self._idle_ttl_task: Optional[asyncio.Task[None]] = None
        self._cache_store: dict[str, tuple[float, Any]] = {}
        self.command_bus = CommandBus(orchestrator=self)

    @property
    def pending_approvals(self) -> dict[str, PendingApproval]:
        return self.approval_coordinator.pending_approvals

    @property
    def _approval_futures(self) -> dict[str, asyncio.Future[dict[str, Any]]]:
        return self.approval_coordinator._approval_futures

    @property
    def _messages_etag_cache(self) -> dict[str, SessionMessagesEtagEntry]:
        return self._etag_negotiator._messages_etag_cache

    @property
    def _watcher_task(self) -> Optional[asyncio.Task[None]]:
        return self.storage_watcher._task

    @property
    def _seq_counter(self) -> int:
        return self.event_bus.seq_counter

    @property
    def _event_buffer(self) -> collections.deque[dict[str, Any]]:
        return self.event_bus.event_buffer

    @property
    def _ws_subscribers(self) -> set[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]]:
        return self.event_bus._subscribers

    def bump_version(self) -> None:
        self.event_bus.bump_seq()

    def get_sessions_etag(self, agent_filter: str = "all", limit: int = 40) -> str:
        active_count = len(self.active_processes) + len(self.active_direct_tasks)
        return self._etag_negotiator.get_sessions_etag(
            seq_counter=self._seq_counter, active_count=active_count,
            pending_count=len(self.pending_approvals), agent_filter=agent_filter, limit=limit,
        )

    def check_session_messages_etag_matches(self, if_none_match: Optional[str], unified_id: str, limit: int = 30, before_step: Optional[int] = None, since_step: Optional[int] = None) -> tuple[bool, Optional[str]]:
        agent_type = unified_id.split(":", 1)[0] if ":" in unified_id else ""
        return self._etag_negotiator.check_messages_etag_matches(if_none_match=if_none_match, unified_id=unified_id, adapter=self.adapters.get(agent_type), is_active=self.is_session_active(unified_id), is_pending=unified_id in self.pending_approvals, limit=limit, before_step=before_step, since_step=since_step)

    def get_session_messages_etag(self, detail: dict[str, Any], unified_id: str, limit: int = 30, before_step: Optional[int] = None, since_step: Optional[int] = None) -> str:
        agent_type = unified_id.split(":", 1)[0] if ":" in unified_id else ""
        return self._etag_negotiator.generate_and_cache_messages_etag(detail=detail, unified_id=unified_id, adapter=self.adapters.get(agent_type), is_active=self.is_session_active(unified_id), is_pending=unified_id in self.pending_approvals, limit=limit, before_step=before_step, since_step=since_step)

    def _extract_session_detail_fingerprint(self, detail: dict[str, Any]) -> str:
        return self._etag_negotiator.extract_session_detail_fingerprint(detail)

    def normalize_execution_mode(self, agent_type: str, mode: Optional[str]) -> str:
        m = (mode or "").strip().lower()
        target_type = "claude_code" if agent_type == "claude" else agent_type
        adapter = self.adapters.get(target_type)
        supported = (
            adapter.supported_modes
            if adapter
            else {ExecutionMode.AUTO.value, ExecutionMode.ACCEPT_EDITS.value, ExecutionMode.PLAN.value, ExecutionMode.ASK.value}
        )
        return m if m in supported else ExecutionMode.AUTO.value

    def is_session_active(self, unified_id: str) -> bool:
        if unified_id in self.active_processes:
            return True
        if unified_id in self.active_direct_tasks:
            return not self.active_direct_tasks[unified_id].done()
        return False

    def register_ws_subscriber(self, send_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        self.event_bus.register_subscriber(send_fn)

    def unregister_ws_subscriber(self, send_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        self.event_bus.unregister_subscriber(send_fn)

    async def execute_command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """统一通过 CommandBus 处理带幂等防重的全双工命令帧。"""
        return await self.command_bus.execute_command(envelope)

    async def broadcast_event(self, event_type: str, payload: dict[str, Any]) -> None:
        await self.event_bus.broadcast(event_type, payload)

    def get_state_snapshot(self, since_seq_id: int = 0) -> dict[str, Any]:
        return self.event_bus.get_snapshot(
            since_seq_id=since_seq_id,
            active_session_ids=list(self.active_processes.keys()) + list(self.active_direct_tasks.keys()),
            pending_approvals=self.approval_coordinator.get_snapshot_list(),
        )

    def _get_cached_or_compute(self, cache_key: str, ttl_seconds: float, compute_fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        if cache_key in self._cache_store:
            ts, val = self._cache_store[cache_key]
            if now - ts < ttl_seconds:
                return val
        val = compute_fn()
        self._cache_store[cache_key] = (now, val)
        return val

    def get_startup_health_report(self, max_age: float = 30.0) -> list[dict[str, Any]]:
        return self._get_cached_or_compute(
            "health_report", max_age, lambda: build_startup_health_report(self.adapters)
        )

    def get_available_models(self, max_age: float = 15.0) -> dict[str, list[dict[str, Any]]]:
        return self._get_cached_or_compute(
            "models_catalog", max_age, lambda: fetch_available_models(self.adapters)
        )

    def _resolve_default_model(self, agent_type: str) -> str:
        return resolve_default_model(self.adapters, agent_type)

    async def create_session(
        self,
        agent_type: str,
        project_path: str = "",
        title: str = "",
        initial_prompt: str = "",
        model_name: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> tuple[str, dict[str, Any]]:
        """深模块：一站式创建并初始化新会话（状态感知路由：IDE 活跃走 IDE，未打开平滑切 CLI）。"""
        # 网页端新建会话：Antigravity 统一创建由 CLI 驱动的独立会话
        # （IDE 桌面端只支持接管/直连桌面端自身已创建的活动会话，不支持通过 RPC 凭空注入未初始化的会话）
        if agent_type in ("antigravity", "agy_ide", "agy_cli"):
            real_agent = "agy_cli"
        else:
            real_agent = agent_type

        workdir = project_path.strip() or str(PROJECT_ROOT)
        final_title = title.strip() or (initial_prompt.strip()[:50] if initial_prompt else "新建会话")

        raw_id = ""
        adapter = self.adapters.get(real_agent)
        if adapter and hasattr(adapter, "create_session"):
            try:
                raw_id = adapter.create_session(title=final_title, project_path=workdir) or ""
            except Exception as c_exc:
                logger.debug(f"适配器多态创建会话异常: {c_exc}")

        if not raw_id:
            raw_id = f"ses_{uuid.uuid4().hex[:20]}" if real_agent == "opencode" else str(uuid.uuid4())

        unified_id = f"{real_agent}:{raw_id}"
        norm_mode = self.normalize_execution_mode(real_agent, execution_mode)

        meta = self.meta_db.upsert_session_meta(
            unified_id=unified_id,
            agent_type=real_agent,
            raw_session_id=raw_id,
            project_path=workdir,
            title=final_title,
            execution_mode=norm_mode,
            model_name=model_name.strip() if model_name else None,
        )
        self.bump_version()

        if initial_prompt.strip():
            await self.send_prompt_to_session(
                unified_id=unified_id,
                prompt=initial_prompt.strip(),
                project_path=workdir,
                execution_mode=norm_mode,
                model_name=model_name.strip() if model_name else None,
            )

        return unified_id, meta

    def list_all_sessions(self, agent_filter: Optional[str] = None, limit_per_agent: int = 40) -> list[dict[str, Any]]:
        return aggregate_all_sessions(
            adapters=self.adapters, meta_db=self.meta_db, pending_approvals=self.pending_approvals,
            is_session_active_fn=self.is_session_active, resolve_default_model_fn=self._resolve_default_model,
            agent_filter=agent_filter, limit_per_agent=limit_per_agent,
        )

    def get_session_detail(
        self, unified_id: str, limit: int = 30, before_step: Optional[int] = None, since_step: Optional[int] = None
    ) -> dict[str, Any]:
        return fetch_session_detail(
            unified_id=unified_id, adapters=self.adapters, meta_db=self.meta_db, pending_approvals=self.pending_approvals,
            is_session_active_fn=self.is_session_active, resolve_default_model_fn=self._resolve_default_model,
            limit=limit, before_step=before_step, since_step=since_step,
        )

    def get_full_step_output(self, unified_id: str, step_index: int) -> str:
        return fetch_full_step_output(unified_id, step_index, self.adapters)

    async def get_system_overview(self) -> dict[str, Any]:
        return build_system_overview(
            adapters=self.adapters, active_processes=self.active_processes,
            active_direct_tasks=self.active_direct_tasks, pending_approvals=self.pending_approvals,
            health_list=self.get_startup_health_report(),
        )

    async def start_background_services(self) -> None:
        res = self.storage_watcher.start()
        if asyncio.iscoroutine(res):
            await res
        if self._idle_ttl_task is None or self._idle_ttl_task.done():
            self._idle_ttl_task = asyncio.create_task(self._idle_process_ttl_loop())
        logger.info("AgentOrchestrator 后台服务与存储监控已启动")

    async def stop_background_services(self) -> None:
        res = self.storage_watcher.stop()
        if asyncio.iscoroutine(res):
            await res
        if self._idle_ttl_task and not self._idle_ttl_task.done():
            self._idle_ttl_task.cancel()
        for uid in list(self.active_processes.keys()):
            await self.abort_session(uid, reason="中控台停机关闭")
        for t in list(self.active_direct_tasks.values()):
            if not t.done():
                t.cancel()
        self.active_direct_tasks.clear()
        logger.info("AgentOrchestrator 后台服务已安全停止")

    async def _idle_process_ttl_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30.0)
                now = time.time()
                for uid, last_time in list(self.process_last_active.items()):
                    if now - last_time > 600.0:
                        logger.info(f"子进程空闲超过 10 分钟 TTL，自动回收: {uid}")
                        await self.abort_session(uid, reason="子进程空闲超时自动回收 (10min TTL)")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug(f"空闲 TTL 检查轮询异常: {exc}")

    def cancel_session_approval(self, unified_id: str) -> None:
        self.approval_coordinator.cancel_session_approval(unified_id)

    async def abort_session(self, unified_id: str, reason: str = "手动中止") -> bool:
        d_task = self.active_direct_tasks.pop(unified_id, None)
        if d_task and not d_task.done():
            d_task.cancel()
        proc = self.active_processes.pop(unified_id, None)
        self.process_last_active.pop(unified_id, None)
        self.cancel_session_approval(unified_id)

        killed = False
        if proc and proc.pid:
            killed = kill_windows_process_tree(proc.pid)

        if ":" in unified_id:
            atype, raw_cid = unified_id.split(":", 1)
            adapter = self.adapters.get(atype)
            if adapter and hasattr(adapter, "cancel_session"):
                try:
                    killed = adapter.cancel_session(raw_cid) or killed
                except Exception:
                    pass

        await self.broadcast_event(
            "SESSION_ABORTED",
            {"unified_id": unified_id, "reason": reason, "killed": killed},
        )
        self.bump_version()
        return True

    async def delete_session(self, unified_id: str) -> bool:
        """深模块彻底删除会话：终止活跃任务、调用适配器物理删除、写入墓碑并广播。"""
        if not unified_id or ":" not in unified_id:
            return False

        agent_type, raw_id = unified_id.split(":", 1)

        # 1. 若当前会话活跃执行中，先安全中止
        if self.is_session_active(unified_id):
            try:
                await self.abort_session(unified_id, reason="会话已被用户删除")
            except Exception as abort_exc:
                logger.warning(f"中止待删除会话 {unified_id} 异常: {abort_exc}")

        # 2. 委托对应适配器执行底层物理清理（文件与库表）
        adapter = self.adapters.get(agent_type)
        if adapter and hasattr(adapter, "delete_session"):
            try:
                adapter.delete_session(raw_id)
            except Exception as adp_exc:
                logger.warning(f"适配器物理删除会话 {unified_id} 异常: {adp_exc}")

        # 3. 内部数据库写入墓碑软删除标记，彻底隔绝重新抓取
        self.meta_db.mark_session_deleted(unified_id)

        # 4. 递增状态版本号并失效适配器短效缓存
        self.bump_version()
        if adapter and hasattr(adapter, "invalidate_cache"):
            try:
                adapter.invalidate_cache()
            except Exception:
                pass

        # 5. 全网广播 SESSION_DELETED 事件
        await self.broadcast_event("SESSION_DELETED", {"unified_id": unified_id})
        return True

    async def send_prompt_to_session(
        self, unified_id: str, prompt: str, project_path: Optional[str] = None,
        execution_mode: Optional[str] = None, model_name: Optional[str] = None,
        auto_fork_ide: bool = True, approved_tools: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return await self._stream_runner.dispatch_prompt(
            unified_id=unified_id, prompt=prompt, project_path=project_path,
            execution_mode=execution_mode, model_name=model_name, auto_fork_ide=auto_fork_ide,
            approved_tools=approved_tools, allow_real_process_in_test=getattr(self, "_allow_real_process_in_test", False),
        )

    def _build_cli_command(self, agent_type: str, raw_session_id: str, prompt: str, execution_mode: str, model_name: str = "", approved_tools: Optional[list[str]] = None) -> list[str]:
        return self._stream_runner.build_cli_command(agent_type, raw_session_id, prompt, execution_mode, model_name, approved_tools)

    async def _handle_stream_line(self, ctx: TurnContext, line_str: str, proc: asyncio.subprocess.Process) -> None:
        await self._stream_runner._handle_stream_line(ctx=ctx, line_str=line_str, proc=proc)

    async def _watch_direct_turn_progress(self, uid: str, atype: str, sid: str) -> None:
        await self._stream_runner.watch_direct_turn_progress(uid, atype, sid)

    async def _run_agent_turn_stream(self, ctx: TurnContext) -> None:
        await self._stream_runner.run_agent_turn_stream(ctx=ctx, allow_real_process_in_test=getattr(self, "_allow_real_process_in_test", False), cmd_builder=self._build_cli_command)

    async def _cleanup_proc_for_approval(self, proc: Optional[asyncio.subprocess.Process], uid: str) -> None:
        if proc and proc.pid:
            kill_windows_process_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception as exc:
                log_structured_error(problem=f"等待旧子进程退出超时 ({uid})", cause=str(exc), fix="强制移除并继续。", exc=exc)
        self.active_processes.pop(uid, None)
        self.process_last_active.pop(uid, None)

    async def _dispatch_followup_for_approval(self, uid: str, followup: str, p_path: str) -> None:
        async def _send_task() -> None:
            try:
                await self.send_prompt_to_session(unified_id=uid, prompt=followup, project_path=p_path)
            except Exception as exc:
                log_structured_error(problem=f"拒绝工具后发送替代方案失败 ({uid})", cause=str(exc), fix="检查状态重新发送。", exc=exc)
        f_task = asyncio.create_task(_send_task())
        self.background_tasks.add(f_task)
        f_task.add_done_callback(self.background_tasks.discard)

    async def request_mobile_tool_approval(self, approval_obj: Optional[PendingApproval] = None, *, unified_id: str = "", agent_type: str = "", raw_session_id: str = "", tool_call_id: str = "", tool_name: str = "", args_summary: str = "", diff_text: str = "", risk_level: str = "normal", prompt_context: str = "", project_path: str = "", proc: Optional[asyncio.subprocess.Process] = None) -> dict[str, Any]:
        return await self.approval_coordinator.register_and_wait(approval_obj=approval_obj, unified_id=unified_id, agent_type=agent_type, raw_session_id=raw_session_id, tool_call_id=tool_call_id, tool_name=tool_name, args_summary=args_summary, diff_text=diff_text, risk_level=risk_level, prompt_context=prompt_context, project_path=project_path, proc=proc, on_cleanup_proc=lambda uid: self._cleanup_proc_for_approval(proc, uid), on_followup_prompt=lambda uid, fol, pp: self._dispatch_followup_for_approval(uid, fol, pp))

    async def resolve_tool_approval(self, unified_id: str, decision: str, feedback: str = "") -> dict[str, Any]:
        return await self.approval_coordinator.resolve(unified_id=unified_id, decision=decision, feedback=feedback)

    def search_sessions(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """全量跨 Agent 会话内容与元数据高速穿透检索。"""
        q = (query or "").strip()
        if not q:
            return []

        all_sessions = self.list_all_sessions(limit_per_agent=100)
        sess_map = {s["unified_id"]: s for s in all_sessions}

        results: list[dict[str, Any]] = []
        matched_uids: set[str] = set()
        q_lower = q.lower()

        # 1. 优先匹配已有聚合会话的标题、摘要与项目名称
        for s in all_sessions:
            uid = s["unified_id"]
            title = str(s.get("title") or "")
            preview = str(s.get("preview") or "")
            proj = str(s.get("project_name") or "")

            if q_lower in title.lower():
                matched_uids.add(uid)
                results.append({
                    "unified_id": uid,
                    "type": "title",
                    "snippet": extract_snippet(title, q),
                    "session": s,
                })
            elif q_lower in preview.lower():
                matched_uids.add(uid)
                results.append({
                    "unified_id": uid,
                    "type": "preview",
                    "snippet": extract_snippet(preview, q),
                    "session": s,
                })
            elif q_lower in proj.lower():
                matched_uids.add(uid)
                results.append({
                    "unified_id": uid,
                    "type": "project",
                    "snippet": extract_snippet(proj, q),
                    "session": s,
                })

        # 2. 深入各个 Agent 底层存储扫描会话内容
        for atype, adapter in self.adapters.items():
            if len(results) >= limit:
                break
            try:
                sub_matches = adapter.search_sessions_content(q, limit=limit)
                for item in sub_matches:
                    raw_id = item["raw_session_id"]
                    uid = f"{atype}:{raw_id}"
                    if uid in matched_uids:
                        continue
                    matched_uids.add(uid)

                    s_obj = sess_map.get(uid)
                    if not s_obj and hasattr(adapter, "get_session"):
                        single_sess = adapter.get_session(raw_id)
                        if single_sess:
                            s_obj = single_sess.to_dict()

                    results.append({
                        "unified_id": uid,
                        "type": item["type"],
                        "snippet": item["snippet"],
                        "session": s_obj,
                    })
                    if len(results) >= limit:
                        break
            except Exception as exc:
                logger.debug(f"Agent {atype} 搜索异常: {exc}")

        return results

AgentProcessPool = AgentOrchestrator
