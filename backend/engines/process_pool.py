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
import os
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
    get_session_modified_files_summary,
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
        # C5 逃生舱：允许环境变量放宽子进程并发上限（仅可大于默认值方向由使用者自担）
        try:
            env_max = int(os.environ.get("AGENTDECK_MAX_SUBPROCESSES", "") or 0)
        except ValueError:
            env_max = 0
        self.max_concurrent_subprocesses = max(max_concurrent_subprocesses, env_max)
        self.process_semaphore = asyncio.Semaphore(self.max_concurrent_subprocesses)
        # C1 会话锁：send_prompt_to_session 唯一下沉点（REST 与 WS 两路自然串行）
        self._send_locks: dict[str, asyncio.Lock] = {}
        try:
            self._idle_poll_seconds = float(os.environ.get("AGENTDECK_IDLE_POLL_SECONDS", "") or 30.0)
        except ValueError:
            self._idle_poll_seconds = 30.0
        try:
            self._idle_min_seconds = float(os.environ.get("AGENTDECK_IDLE_MIN_SECONDS", "") or 300.0)
        except ValueError:
            self._idle_min_seconds = 300.0

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
            max_concurrent_fn=lambda: self.max_concurrent_subprocesses,
        )
        self._idle_ttl_task: Optional[asyncio.Task[None]] = None
        self._cache_store: dict[str, tuple[float, Any]] = {}
        # S1 状态机：注入状态变更广播回调（DB 写库后同步回调，异步调度在 orchestrator 侧）
        self.meta_db.set_status_broadcast_callback(self._schedule_status_broadcast)
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

    def _schedule_status_broadcast(
        self, unified_id: str, old_status: str, new_status: str, reason: str, seq: int
    ) -> None:
        """S1 状态变更广播：调度异步 WS 推送（不进 200 条追赶缓冲，快照本身携带 status）。"""
        if old_status == new_status:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self.event_bus.broadcast(
                "SESSION_STATUS_CHANGED",
                {
                    "unified_id": unified_id,
                    "old_status": old_status,
                    "status": new_status,
                    "reason": reason,
                    "status_seq": seq,
                },
                track_in_buffer=False,
                send_timeout=0.3,
            )
        )

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
        if m not in supported:
            logger.warning(f"【权限模式安全纠偏】Agent '{target_type}' 不支持模式 '{m}'，已自动安全回退为审批模式 (ask)")
            return ExecutionMode.ASK.value
        return m

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

    async def broadcast_event(
        self, event_type: str, payload: dict[str, Any], **kwargs: Any
    ) -> None:
        await self.event_bus.broadcast(event_type, payload, **kwargs)

    def get_state_snapshot(self, since_seq_id: int = 0) -> dict[str, Any]:
        try:
            session_statuses = self.meta_db.get_active_session_status_ids()
        except Exception:
            session_statuses = {}
        return self.event_bus.get_snapshot(
            since_seq_id=since_seq_id,
            active_session_ids=list(self.active_processes.keys()) + list(self.active_direct_tasks.keys()),
            pending_approvals=self.approval_coordinator.get_snapshot_list(),
            session_statuses=session_statuses,
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
        # 网页端新建会话：状态感知自适应路由（IDE 活跃优先通过 Connect-RPC 原生注入，离线平滑转 CLI）
        if agent_type in ("antigravity", "agy_ide", "agy_cli"):
            ide_adapter = self.adapters.get("agy_ide")
            has_ls = bool(
                ide_adapter
                and hasattr(ide_adapter, "ls_bridge")
                and ide_adapter.ls_bridge._scan_ls_processes()
            )
            real_agent = "agy_ide" if has_ls else "agy_cli"
        else:
            real_agent = agent_type

        workdir = project_path.strip() or str(PROJECT_ROOT)
        final_title = title.strip() or (initial_prompt.strip()[:50] if initial_prompt else "新建会话")

        raw_id = ""
        adapter_raw_id = ""
        adapter = self.adapters.get(real_agent)
        if adapter and hasattr(adapter, "create_session"):
            try:
                raw_id = adapter.create_session(title=final_title, project_path=workdir) or ""
                adapter_raw_id = raw_id
            except Exception as c_exc:
                logger.debug(f"适配器多态创建会话异常: {c_exc}")

        # IDE 在线但原生注入失败：平滑降级为 CLI 独立创建，杜绝幻影会话
        if not raw_id and real_agent == "agy_ide":
            logger.warning("IDE 会话原生创建失败，自动降级为 AGY CLI 独立创建")
            real_agent = "agy_cli"
            adapter = self.adapters.get(real_agent)
            if adapter and hasattr(adapter, "create_session"):
                try:
                    raw_id = adapter.create_session(title=final_title, project_path=workdir) or ""
                    adapter_raw_id = raw_id
                except Exception as c_exc:
                    logger.debug(f"降级 CLI 创建会话异常: {c_exc}")

        created_ok = bool(adapter_raw_id)
        if not raw_id:
            # S1/E1：底层创建失败不再静默打假 ID —— 仍需 id 落库，但会话标记 degraded 并通知用户
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

        # S1 状态机初始态：底层创建成功 → creating（等首轮派发确认）或 live（无首轮任务）
        #                  底层创建失败 → degraded（CLI 直连可用）
        prompt_text = initial_prompt.strip()
        if not prompt_text:
            init_status, init_reason = (
                ("live", "created") if created_ok else ("degraded", "backend_create_failed")
            )
            status_res = self.meta_db.set_session_status(unified_id, init_status, reason=init_reason)
            meta.update(self.meta_db.get_session_overrides().get(unified_id) or {})
            return unified_id, meta

        status_res = self.meta_db.set_session_status(
            unified_id, "creating", reason="first_prompt_pending"
        )
        meta.update(self.meta_db.get_session_overrides().get(unified_id) or {})

        # 首轮 prompt 改为后台异步派发：
        # 同步链路（StartCascade→改题→LS 直发）在 IDE 忙碌时常超 8s，手机端 apiFetch
        # 默认 8s 超时中断请求后，派发未落地 → 会话“建了但不跑”。后台化后创建响应
        # 立即返回，派发失败也不再静默。
        async def _dispatch_first_prompt() -> None:
            try:
                send_res = await self.send_prompt_to_session(
                    unified_id=unified_id,
                    prompt=prompt_text,
                    project_path=workdir,
                    execution_mode=norm_mode,
                    model_name=model_name.strip() if model_name else None,
                    auto_fork_ide=True,
                )
                if not send_res.get("ok", False):
                    log_structured_error(
                        problem=f"新建会话首轮指令派发失败 ({unified_id})",
                        cause=str(send_res.get("error") or "直发通道返回失败"),
                        fix="可在该会话中重新发送首轮指令，或检查 Antigravity 桌面端是否在线。",
                    )
                    self.meta_db.set_session_status(
                        unified_id, "degraded", reason="first_prompt_failed"
                    )
                else:
                    # S1：首轮派发成功 → live；底层降级创建的会话保持 degraded
                    self.meta_db.set_session_status(
                        unified_id,
                        "live" if created_ok else "degraded",
                        reason="first_prompt_done",
                    )
            except Exception as exc:
                log_structured_error(
                    problem=f"新建会话首轮指令派发异常 ({unified_id})",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="请确认 IDE/CLI 通道可用后在会话中重试发送。",
                    exc=exc,
                )
                self.meta_db.set_session_status(
                    unified_id, "degraded", reason="first_prompt_failed"
                )

        fp_task = asyncio.create_task(_dispatch_first_prompt())
        self.background_tasks.add(fp_task)
        fp_task.add_done_callback(self.background_tasks.discard)

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

    def get_session_modified_files(self, unified_id: str) -> dict[str, Any]:
        return get_session_modified_files_summary(
            unified_id=unified_id,
            adapters=self.adapters,
            meta_db=self.meta_db,
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
        """C3 空闲回收长尾兜底（watcher 退出失败路径）：

        空闲信号 = 连续 2 个 poll 周期无新输出 且 空闲时长 ≥ AGENTDECK_IDLE_MIN_SECONDS
        （默认 5min）。绝不用裸 10min TTL 误杀直连轮询的合法长任务。
        """
        idle_strikes: dict[str, int] = {}
        while True:
            try:
                await asyncio.sleep(self._idle_poll_seconds)
                now = time.time()
                for uid, last_time in list(self.process_last_active.items()):
                    if uid in self.active_direct_tasks and not self.active_direct_tasks[uid].done():
                        continue
                    idle = now - last_time
                    if idle <= self._idle_min_seconds:
                        idle_strikes.pop(uid, None)
                        continue
                    strikes = idle_strikes.get(uid, 0) + 1
                    idle_strikes[uid] = strikes
                    if strikes >= 2:
                        logger.info(
                            f"子进程连续 {strikes} 个周期无输出且空闲 {idle:.0f}s，长尾回收: {uid}"
                        )
                        await self.abort_session(uid, reason="长尾空闲回收（watcher 未退出兜底）")
                        idle_strikes.pop(uid, None)
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
            # E7：kill 后 reap 子进程，避免 Windows 上的僵尸句柄与残留输出缓冲
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception as exc:
                logger.warning(f"等待子进程退出超时 ({unified_id}): {exc}")

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

        target_uid = (
            self.meta_db.resolve_unified_id(unified_id)
            if hasattr(self.meta_db, "resolve_unified_id")
            else unified_id
        )
        agent_type, raw_id = target_uid.split(":", 1)

        # 1. 若当前会话活跃执行中，先安全中止
        for uid in set([unified_id, target_uid]):
            if self.is_session_active(uid):
                try:
                    await self.abort_session(uid, reason="会话已被用户删除")
                except Exception as abort_exc:
                    logger.warning(f"中止待删除会话 {uid} 异常: {abort_exc}")

        # 2. 委托对应适配器执行底层物理清理（文件与库表）
        adapter = self.adapters.get(agent_type)
        if adapter and hasattr(adapter, "delete_session"):
            try:
                adapter.delete_session(raw_id)
            except Exception as adp_exc:
                logger.warning(f"适配器物理删除会话 {target_uid} 异常: {adp_exc}")

        # 3. 内部数据库写入墓碑软删除标记，彻底隔绝重新抓取
        self.meta_db.mark_session_deleted(unified_id)
        if target_uid != unified_id:
            self.meta_db.mark_session_deleted(target_uid)

        # 4. 递增状态版本号并失效适配器短效缓存
        self.bump_version()
        if adapter and hasattr(adapter, "invalidate_cache"):
            try:
                adapter.invalidate_cache()
            except Exception:
                pass

        # 5. 全网广播 SESSION_DELETED 事件
        await self.broadcast_event(
            "SESSION_DELETED",
            {"unified_id": unified_id, "actual_unified_id": target_uid},
        )
        return True

    async def send_prompt_to_session(
        self, unified_id: str, prompt: str, project_path: Optional[str] = None,
        execution_mode: Optional[str] = None, model_name: Optional[str] = None,
        auto_fork_ide: bool = True, approved_tools: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        # C1：会话锁下沉到唯一入口 —— REST 与 WS 两路并发发送在此自然串行，
        # 不再依赖 CommandBus 单路锁（绕过总线的派发同样受保护）。
        lock = self._send_locks.setdefault(unified_id, asyncio.Lock())
        async with lock:
            res = await self._stream_runner.dispatch_prompt(
                unified_id=unified_id, prompt=prompt, project_path=project_path,
                execution_mode=execution_mode, model_name=model_name, auto_fork_ide=auto_fork_ide,
                approved_tools=approved_tools, allow_real_process_in_test=getattr(self, "_allow_real_process_in_test", False),
            )
        # S1：仅 creating → live；degraded 会话保持 degraded（D10：CLI 直连真实可用，
        # 用户重试按钮显式恢复后才转 live）
        if res.get("ok", False):
            cur_status = self.meta_db.get_session_status(unified_id)
            if cur_status == "creating":
                self.meta_db.set_session_status(unified_id, "live", reason="prompt_accepted")
        return res

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

    async def reply_interactive_form(
        self,
        unified_id: str,
        form_id: str,
        answer: dict[str, Any],
        source: str = "user",
    ) -> dict[str, Any]:
        """向会话交付交互决策表单答复。"""
        agent_type = unified_id.split(":", 1)[0] if ":" in unified_id else ""
        raw_session_id = unified_id.split(":", 1)[1] if ":" in unified_id else unified_id
        adapter = self.adapters.get(agent_type)
        if not adapter:
            raise HTTPException(status_code=400, detail=f"未找到适配器: {agent_type}")

        # 清洗 answer 中的选项值：若包含 " — " 则截取实际 value 传给底层引擎
        clean_answer = {}
        for k, v in answer.items():
            if isinstance(v, str) and " — " in v:
                clean_answer[k] = v.split(" — ")[0].strip()
            elif isinstance(v, list):
                clean_answer[k] = [
                    (item.split(" — ")[0].strip() if isinstance(item, str) and " — " in item else item)
                    for item in v
                ]
            else:
                clean_answer[k] = v

        success = False
        if hasattr(adapter, "reply_form") and not form_id.startswith("syn_"):
            success = adapter.reply_form(raw_session_id, form_id, clean_answer)

        # 若是合成表单 (syn_) 或 Antigravity / Claude Code，通过上下文 Prompt 交付
        if not success and (form_id.startswith("syn_") or agent_type in ("agy_ide", "agy_cli", "claude_code")):
            lines = []
            for k, v in answer.items():
                val_str = ", ".join(v) if isinstance(v, list) else str(v)
                lines.append(f"{val_str}")
            prompt_text = "\n".join(lines) if lines else "确认"
            res = await self.send_prompt_to_session(unified_id=unified_id, prompt=prompt_text)
            success = res.get("ok", False)

        if success:
            logger.info(f"交互式决策已生效 ({source}): unified_id={unified_id} form_id={form_id}")
            self.broadcast_event(
                "FORM_ANSWERED",
                {
                    "unified_id": unified_id,
                    "form_id": form_id,
                    "answer": answer,
                    "source": source,
                },
            )
            self.bump_version()
            return {"ok": True, "unified_id": unified_id, "form_id": form_id}

        return {"ok": False, "error": "交付表单答复失败，请检查 Agent 服务是否在线"}

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
