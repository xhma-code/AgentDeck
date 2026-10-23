"""
多 Agent 双向流式执行池 (`StreamJsonProcessPool`)、工具审批状态机与文件变动实时广播中枢。

遵循 `/autoplan` Phase 3 Eng 规范：
1. 维护内存态 `pending_approvals` 快照与 200 条事件环形缓冲区（递增 `seq_id`），支持断线重连即时恢复。
2. Windows 子进程销毁强制使用 `taskkill /F /T /PID <pid>` 递归清理整棵进程树，并设 10 分钟空闲 TTL。
3. 对 `Antigravity IDE` 会话实施防双写脑裂保护，支持一键安全克隆至 `agy_cli` 接力执行。
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import watchfiles

from backend.adapters.antigravity_adapter import (
    AntigravityAdapter,
    fork_ide_session_to_cli,
)
from backend.adapters.base import (
    BaseAgentAdapter,
    ExecutionMode,
    UnifiedMessageItem,
    UnifiedSession,
    classify_tool_risk,
    evaluate_project_dir,
    extract_tool_summary_and_diff,
)
from backend.adapters.claude_adapter import ClaudeCodeAdapter
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.core.database import PROJECT_ROOT, MetaDatabase, utc_now_iso
from backend.core.logger import log_structured_error, logger

# 四大 Agent 可选模型目录（实测对齐本机 agy models 与 Claude Code / OpenCode 模型列表）
AGENT_MODELS: dict[str, list[dict[str, Any]]] = {
    "agy_cli": [
        {"id": "claude-sonnet-5-5-high", "label": "Claude Sonnet 5.5 (High)", "tag": "推荐"},
        {"id": "claude-opus-5-5-high", "label": "Claude Opus 5.5 (High)", "tag": "深度"},
        {"id": "gemini-4-argon-high", "label": "Gemini 4 Argon (High)", "tag": "旗舰"},
        {"id": "gemini-4-argon-medium", "label": "Gemini 4 Argon (Medium)", "tag": "均衡"},
        {"id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)", "tag": "极速"},
        {"id": "claude-sonnet-5-5-medium", "label": "Claude Sonnet 5.5 (Med)", "tag": ""},
        {"id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Med)", "tag": ""},
    ],
    "agy_ide": [
        {"id": "claude-sonnet-5-5-high", "label": "Claude Sonnet 5.5 (High)", "tag": "推荐"},
        {"id": "claude-opus-5-5-high", "label": "Claude Opus 5.5 (High)", "tag": "深度"},
        {"id": "gemini-4-argon-high", "label": "Gemini 4 Argon (High)", "tag": "旗舰"},
        {"id": "gemini-4-argon-medium", "label": "Gemini 4 Argon (Medium)", "tag": "均衡"},
        {"id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)", "tag": "极速"},
    ],
    "claude_code": [
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "tag": "默认"},
        {"id": "claude-opus-4-7", "label": "Claude Opus 4.7", "tag": "深度"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "tag": "极速"},
        {"id": "sonnet", "label": "Sonnet (Latest)", "tag": ""},
        {"id": "opus", "label": "Opus (Latest)", "tag": ""},
    ],
    "opencode": [
        {"id": "default", "label": "OpenCode 默认模型", "tag": "默认"},
        {"id": "anthropic/claude-sonnet-4-5", "label": "Claude Sonnet 4.5", "tag": ""},
        {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro", "tag": ""},
        {"id": "openai/gpt-5", "label": "GPT-5", "tag": ""},
    ],
}


@dataclass
class TurnContext:
    """单轮流式执行上下文，消除多方法间 7 个参数成群传递（Data Clumps）。"""

    unified_id: str
    agent_type: str
    raw_session_id: str
    prompt: str
    workdir: str
    execution_mode: str
    model_name: str = ""
    approved_tools: Optional[list[str]] = None


@dataclass
class PendingApproval:
    """挂起等待手机端用户确认的工具审批请求快照。"""

    approval_id: str
    unified_id: str
    agent_type: str
    raw_session_id: str
    tool_call_id: str
    tool_name: str
    args_summary: str
    diff_text: str
    risk_level: str  # "normal" | "high"
    prompt_context: str
    project_path: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def kill_windows_process_tree(pid: Optional[int]) -> bool:
    """
    在 Windows 下使用 `taskkill /F /T /PID` 递归强杀整棵子孙进程树（含派生的 pwsh.exe / node.exe）。
    """
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=4.0,
            )
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
        logger.info(f"已递归清理子进程树 (PID={pid})")
        return True
    except Exception as exc:
        log_structured_error(
            problem=f"清理子进程树失败 (PID={pid})",
            cause=str(exc),
            fix="进程可能已提前自行退出，无需人工干预。",
            exc=exc,
        )
        return False


class AgentOrchestrator:
    """
    四大 Agent 统一编排器与 `StreamJsonProcessPool` 进程池总管。
    负责：
    1. 聚合 4 大适配器的会话列表与历史消息。
    2. 维护活跃子进程、工具审批挂起队列与断线重连环形缓冲区（200 条事件）。
    3. 监听本地文件变动并向所有已连接的手机 WebSocket 广播增量更新。
    """

    def __init__(self, meta_db: MetaDatabase) -> None:
        self.meta_db = meta_db
        self.adapters: dict[str, BaseAgentAdapter] = {
            "agy_ide": AntigravityAdapter(
                agent_type="agy_ide",
                agent_label="Antigravity IDE",
            ),
            "agy_cli": AntigravityAdapter(
                agent_type="agy_cli",
                agent_label="Antigravity CLI",
            ),
            "claude_code": ClaudeCodeAdapter(),
            "opencode": OpenCodeAdapter(),
        }

        # unified_id -> asyncio.subprocess.Process
        self.active_processes: dict[str, asyncio.subprocess.Process] = {}
        # unified_id -> last_activity_epoch (用于 10 分钟空闲 TTL 回收)
        self.process_last_active: dict[str, float] = {}
        # unified_id -> PendingApproval
        self.pending_approvals: dict[str, PendingApproval] = {}
        # unified_id -> asyncio.Event + decision result (用于长驻管道内唤醒等待中的审批)
        self._approval_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

        # 断线重连事件环形缓冲区（保存最近 200 条广播事件，带递增 seq_id）
        self._seq_counter: int = 0
        self._event_buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=200)

        # 正在执行的后台协程强引用集合（防止被 Python GC 意外回收）
        self.background_tasks: set[asyncio.Task[Any]] = set()
        # unified_id -> asyncio.Task (针对原生直连派发任务的进度同步与取消控制)
        self.active_direct_tasks: dict[str, asyncio.Task[None]] = {}

        # WebSocket 广播回调集合
        self._ws_subscribers: set[Callable[[dict[str, Any]], Coroutine[Any, Any, None]]] = set()
        self._watcher_task: Optional[asyncio.Task[None]] = None
        self._ttl_task: Optional[asyncio.Task[None]] = None

    def is_session_active(self, unified_id: str) -> bool:
        """判定指定统一会话当前是否处于活跃执行态（覆盖子进程树与直连任务）。"""
        return unified_id in self.active_processes or unified_id in self.active_direct_tasks


    def register_ws_subscriber(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        self._ws_subscribers.add(callback)

    def unregister_ws_subscriber(
        self, callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        self._ws_subscribers.discard(callback)

    async def broadcast_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向所有已连接的手机端广播统一事件，并写入 200 条环形缓冲区供重连追赶。"""
        self._seq_counter += 1
        envelope = {
            "seq_id": self._seq_counter,
            "type": event_type,
            "timestamp": utc_now_iso(),
            "payload": payload,
        }
        self._event_buffer.append(envelope)

        dead_callbacks = []
        for cb in list(self._ws_subscribers):
            try:
                await cb(envelope)
            except Exception:
                dead_callbacks.append(cb)
        for cb in dead_callbacks:
            self._ws_subscribers.discard(cb)

        return envelope

    def get_state_snapshot(self, since_seq_id: int = 0) -> dict[str, Any]:
        """
        生成 `SESSION_STATE_SNAPSHOT` 状态机快照（用于手机端首次连接或切后台断线重连时 0 延迟恢复）。
        """
        missed_events = [
            ev for ev in self._event_buffer if int(ev.get("seq_id", 0)) > since_seq_id
        ]
        active_ids = list(set(self.active_processes.keys()) | set(self.active_direct_tasks.keys()))
        return {
            "current_seq_id": self._seq_counter,
            "active_session_ids": active_ids,
            "pending_approvals": [pa.to_dict() for pa in self.pending_approvals.values()],
            "missed_events": missed_events[-50:] if since_seq_id > 0 else [],
        }


    def get_startup_health_report(self) -> list[dict[str, Any]]:
        """生成启动时的 4 大 Agent 状态体检表（Startup Health Check Table）。"""
        report: list[dict[str, Any]] = []
        for key, adapter in self.adapters.items():
            ok, desc = adapter.is_available()
            report.append(
                {
                    "agent_type": key,
                    "agent_label": adapter.agent_label,
                    "ready": ok,
                    "description": desc,
                }
            )
        return report

    def get_available_models(self) -> dict[str, list[dict[str, Any]]]:
        """返回各 Agent 支持的模型列表供前端选择器展示。"""
        return AGENT_MODELS

    def list_all_sessions(
        self, agent_filter: Optional[str] = None, limit_per_agent: int = 40
    ) -> list[dict[str, Any]]:
        """聚合 4 大 Agent 的会话列表，合并本地置顶/执行模式与实时进程状态，并区分真正工程项目与临时目录。"""
        overrides = self.meta_db.get_session_overrides()
        all_ws = self.meta_db.list_workspaces(include_hidden=True)
        bookmarked_keys: set[str] = set()
        hidden_keys: set[str] = set()
        ws_custom_names: dict[str, str] = {}
        for w in all_ws:
            w_path = str(w.get("workspace_path") or "").strip().replace("\\", "/").rstrip("/")
            if not w_path:
                continue
            w_key = w_path.lower()
            if int(w.get("is_hidden") or 0) == 1:
                hidden_keys.add(w_key)
            else:
                bookmarked_keys.add(w_key)
                if w.get("workspace_name"):
                    ws_custom_names[w_key] = str(w["workspace_name"])

        proj_eval_cache: dict[str, tuple[bool, str, str]] = {}

        def resolve_proj(raw_p: str) -> tuple[bool, str, str]:
            if raw_p not in proj_eval_cache:
                is_p, norm_p, p_name = evaluate_project_dir(raw_p, bookmarked_keys, hidden_keys)
                if is_p and norm_p.lower() in ws_custom_names:
                    p_name = ws_custom_names[norm_p.lower()]
                proj_eval_cache[raw_p] = (is_p, norm_p, p_name)
            return proj_eval_cache[raw_p]

        all_sessions: list[UnifiedSession] = []

        for key, adapter in self.adapters.items():
            if agent_filter and agent_filter != "all" and agent_filter != key:
                continue
            try:
                adapter_sessions = adapter.list_sessions(limit=limit_per_agent)
                all_sessions.extend(adapter_sessions)
            except Exception as exc:
                log_structured_error(
                    problem=f"聚合 {adapter.agent_label} 会话列表异常",
                    cause=str(exc),
                    fix="已跳过该适配器本次查询，不影响其他 Agent 会话展示。",
                    exc=exc,
                )

        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for sess in all_sessions:
            seen_ids.add(sess.unified_id)
            ov = overrides.get(sess.unified_id)
            if ov:
                sess.is_pinned = int(ov.get("is_pinned") or 0)
                sess.execution_mode = str(ov.get("execution_mode") or ExecutionMode.ASK.value)
                sess.model_name = str(ov.get("model_name") or "")
                if ov.get("title"):
                    sess.title = str(ov["title"])
                if ov.get("project_path") and not sess.project_path:
                    sess.project_path = str(ov["project_path"])
                sess.parent_unified_id = ov.get("parent_unified_id")

            if not sess.model_name:
                models = AGENT_MODELS.get(sess.agent_type, [])
                sess.model_name = models[0]["id"] if models else ""

            is_proj, norm_path, proj_name = resolve_proj(sess.project_path)
            sess.project_path = norm_path
            sess.is_project = is_proj
            sess.project_name = proj_name

            # 实时覆盖运行态与待审批态
            if sess.unified_id in self.pending_approvals:
                sess.status = "waiting_approval"
            elif self.is_session_active(sess.unified_id):
                sess.status = "running"


            merged.append(sess.to_dict())

        # 补充在 AgentDeck 中新建但尚未产生底层日志文件的会话
        for uid, ov in overrides.items():
            if uid not in seen_ids:
                atype = str(ov.get("agent_type") or "claude_code")
                if agent_filter and agent_filter != "all" and agent_filter != atype:
                    continue
                adapter_obj = self.adapters.get(atype)
                label = adapter_obj.agent_label if adapter_obj else atype
                status = (
                    "waiting_approval"
                    if uid in self.pending_approvals
                    else ("running" if self.is_session_active(uid) else "idle")
                )

                default_models = AGENT_MODELS.get(atype, [])
                default_mid = default_models[0]["id"] if default_models else ""
                raw_p = str(ov.get("project_path") or str(PROJECT_ROOT))
                is_proj, norm_path, proj_name = resolve_proj(raw_p)
                merged.append(
                    UnifiedSession(
                        unified_id=uid,
                        agent_type=atype,
                        agent_label=label,
                        raw_session_id=str(ov.get("raw_session_id") or ""),
                        title=str(ov.get("title") or "新建会话"),
                        preview="等待发送首条指令...",
                        project_path=norm_path,
                        status=status,
                        step_count=0,
                        updated_at=str(ov.get("updated_at") or utc_now_iso()),
                        is_pinned=int(ov.get("is_pinned") or 0),
                        execution_mode=str(ov.get("execution_mode") or ExecutionMode.ASK.value),
                        model_name=str(ov.get("model_name") or default_mid),
                        is_project=is_proj,
                        project_name=proj_name,
                        parent_unified_id=ov.get("parent_unified_id"),
                    ).to_dict()
                )

        merged.sort(
            key=lambda x: (int(x.get("is_pinned") or 0), str(x.get("updated_at") or "")),
            reverse=True,
        )
        return merged

    def get_session_detail(
        self,
        unified_id: str,
        limit: int = 30,
        before_step: Optional[int] = None,
    ) -> dict[str, Any]:
        """获取指定统一会话的结构化消息流（支持首屏分页与向上加载历史）及当前状态。"""
        if ":" not in unified_id:
            return {
                "unified_id": unified_id,
                "messages": [],
                "total_count": 0,
                "has_more": False,
                "pending_approval": None,
            }
        agent_type, raw_id = unified_id.split(":", 1)
        adapter = self.adapters.get(agent_type)
        messages: list[dict[str, Any]] = []
        total_count = 0
        has_more = False
        if adapter:
            items, total_count, has_more = adapter.get_session_messages_page(
                raw_id, limit=limit, before_step=before_step
            )
            messages = [it.to_dict() for it in items]

        pa = self.pending_approvals.get(unified_id)
        overrides = self.meta_db.get_session_overrides().get(unified_id, {})
        default_models = AGENT_MODELS.get(agent_type, [])
        default_mid = default_models[0]["id"] if default_models else ""
        return {
            "unified_id": unified_id,
            "agent_type": agent_type,
            "raw_session_id": raw_id,
            "execution_mode": overrides.get("execution_mode", ExecutionMode.ASK.value),
            "model_name": overrides.get("model_name") or default_mid,
            "is_pinned": overrides.get("is_pinned", 0),
            "is_running": self.is_session_active(unified_id),
            "pending_approval": pa.to_dict() if pa else None,
            "total_count": total_count,
            "has_more": has_more,
            "messages": messages,
        }

    def get_full_step_output(self, unified_id: str, step_index: int) -> str:
        """按需拉取被 4KB 截断的单步完整输出。"""
        if ":" not in unified_id:
            return ""
        agent_type, raw_id = unified_id.split(":", 1)
        adapter = self.adapters.get(agent_type)
        if not adapter:
            return ""
        return adapter.get_full_step_output(raw_id, step_index)

    async def start_background_services(self) -> None:
        """启动实时文件变更监听任务与 10 分钟空闲子进程 TTL 回收器。"""
        if self._watcher_task is None:
            self._watcher_task = asyncio.create_task(self._storage_watch_loop())
        if self._ttl_task is None:
            self._ttl_task = asyncio.create_task(self._idle_process_ttl_loop())

    async def stop_background_services(self) -> None:
        """停止后台监听并递归清理所有活跃子进程树与直连任务。"""
        for task in (self._watcher_task, self._ttl_task):
            if task:
                task.cancel()
        for uid in list(self.active_direct_tasks.keys()):
            d_task = self.active_direct_tasks.pop(uid, None)
            if d_task and not d_task.done():
                d_task.cancel()
        for b_task in list(self.background_tasks):
            if not b_task.done():
                b_task.cancel()
        self.background_tasks.clear()
        for uid in list(self.active_processes.keys()):
            await self.abort_session(uid, reason="服务关闭清理")


    async def _idle_process_ttl_loop(self) -> None:
        """每 60 秒检查一次活跃子进程，超过 10 分钟（600 秒）无活动自动回收释放内存。"""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for uid, last_ts in list(self.process_last_active.items()):
                if now - last_ts > 600 and uid not in self.pending_approvals:
                    logger.info(f"会话 {uid} 已空闲满 10 分钟，触发 TTL 自动回收进程树")
                    await self.abort_session(uid, reason="10分钟空闲TTL自动回收")

    async def _storage_watch_loop(self) -> None:
        """使用 watchfiles (Rust notify 内核) 监听 4 大 Agent 的会话文件变动并实时推送。"""
        watch_dirs: list[Path] = []
        for adapter in self.adapters.values():
            for p in adapter.get_watch_paths():
                if p.exists() and p not in watch_dirs:
                    watch_dirs.append(p)

        if not watch_dirs:
            return

        logger.info(f"已启动 SafeStorageWatcher 实时文件监听，监控目录数: {len(watch_dirs)}")
        try:
            async for changes in watchfiles.awatch(*watch_dirs, debounce=600, step=250):
                changed_files: list[str] = []
                affected_agents: set[str] = set()
                affected_session_ids: set[str] = set()

                for _, raw_p in changes:
                    p_str = str(raw_p).replace("\\", "/")
                    if "tmp_snapshots" in p_str:
                        continue
                    p_obj = Path(raw_p)
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

                await self.broadcast_event(
                    "STORAGE_UPDATED",
                    {
                        "changed_files": changed_files[:10],
                        "affected_agents": list(affected_agents),
                        "affected_session_ids": list(affected_session_ids)[:20],
                    },
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log_structured_error(
                problem="实时文件监听器异常中断",
                cause=str(exc),
                fix="系统将在下次请求时依靠轮询兜底。",
                exc=exc,
            )

    async def abort_session(self, unified_id: str, reason: str = "用户手动点击立即中断") -> bool:
        """
        紧急刹车（Hard Stop / SIGINT）：
        立即使用 `taskkill /F /T /PID` 强杀指定会话的子进程树，并取消挂起的审批阻塞。
        """
        # 1. 取消正在执行的原生直连追踪协程
        d_task = self.active_direct_tasks.pop(unified_id, None)
        if d_task and not d_task.done():
            d_task.cancel()

        proc = self.active_processes.pop(unified_id, None)
        self.process_last_active.pop(unified_id, None)
        self.pending_approvals.pop(unified_id, None)


        fut = self._approval_futures.pop(unified_id, None)
        if fut and not fut.done():
            fut.set_result({"decision": "denied", "reason": reason})

        killed = False
        if proc and proc.pid:
            killed = kill_windows_process_tree(proc.pid)

        # 多态通知对应适配器尝试直连取消
        if ":" in unified_id:
            atype, raw_cid = unified_id.split(":", 1)
            adapter = self.adapters.get(atype)
            if adapter:
                try:
                    killed = adapter.cancel_session(raw_cid) or killed
                except Exception as exc:
                    log_structured_error(
                        problem=f"适配器直连取消会话失败 ({unified_id})",
                        cause=str(exc),
                        fix="系统已通过强杀本地进程树兜底。",
                        exc=exc,
                    )


        await self.broadcast_event(
            "SESSION_ABORTED",
            {
                "unified_id": unified_id,
                "reason": reason,
                "killed": killed,
            },
        )
        return True

    async def send_prompt_to_session(
        self,
        unified_id: str,
        prompt: str,
        project_path: Optional[str] = None,
        execution_mode: Optional[str] = None,
        model_name: Optional[str] = None,
        auto_fork_ide: bool = True,
    ) -> dict[str, Any]:
        """
        向指定会话发送指令并流式驱动 Agent 执行。
        若目标为 `agy_ide` 会话，遵循 `/autoplan` 防双写脑裂规范，自动克隆至 `agy_cli` 执行。
        """
        if ":" not in unified_id:
            raise ValueError(f"无效的会话 ID: {unified_id}")

        agent_type, raw_session_id = unified_id.split(":", 1)

        # 提前解析当前会话选定模型与模式，以便 LS Connect-RPC 直连或 CLI 接力使用
        overrides = self.meta_db.get_session_overrides().get(unified_id, {})
        mode = execution_mode or overrides.get("execution_mode") or ExecutionMode.ASK.value
        default_models = AGENT_MODELS.get(agent_type, [])
        default_mid = default_models[0]["id"] if default_models else ""
        chosen_model = model_name or overrides.get("model_name") or default_mid

        target_adapter = self.adapters.get(agent_type)
        if not target_adapter:
            raise ValueError(f"不支持的 Agent 类型: {agent_type}")

        forked_from: Optional[str] = None
        # 1. 优先尝试适配器原生直连派发（如 Antigravity IDE LS-RPC 0 脑裂同步）
        dispatch_res = target_adapter.try_direct_dispatch(
            raw_session_id=raw_session_id,
            prompt=prompt,
            model_name=chosen_model,
            workdir=project_path or "",
        )
        if dispatch_res:
            workdir = project_path or overrides.get("project_path") or str(PROJECT_ROOT)
            self.meta_db.upsert_session_meta(
                unified_id=unified_id,
                agent_type=agent_type,
                raw_session_id=raw_session_id,
                project_path=workdir,
                title=overrides.get("title") or prompt[:60],
                execution_mode=mode,
                model_name=chosen_model,
            )
            await self.broadcast_event(
                "SESSION_TURN_STARTED",
                {
                    "unified_id": unified_id,
                    "agent_type": agent_type,
                    "prompt": prompt,
                    "execution_mode": mode,
                    **dispatch_res,
                },
            )
            # 若该会话已有直连任务在跑，先安全取消旧任务
            old_dt = self.active_direct_tasks.pop(unified_id, None)
            if old_dt and not old_dt.done():
                old_dt.cancel()

            # 启动针对原生直连通道的增量同步协程并纳入强引用与活跃态管理
            dt_task = asyncio.create_task(
                self._watch_direct_turn_progress(unified_id, agent_type, raw_session_id)
            )
            self.active_direct_tasks[unified_id] = dt_task
            self.background_tasks.add(dt_task)
            dt_task.add_done_callback(lambda t: self.active_direct_tasks.pop(unified_id, None))
            dt_task.add_done_callback(self.background_tasks.discard)

            return {
                "ok": True,
                "unified_id": unified_id,
                "forked_from": None,
                "via_ls_rpc": dispatch_res.get("via_ls_rpc", True),
                "execution_mode": mode,
                "model_name": chosen_model,
                "status": "running",
            }


        # 2. 若目标为 agy_ide 但直连未开启，执行安全克隆至 agy_cli (Fork-to-CLI)
        if agent_type == "agy_ide":
            if not auto_fork_ide:
                diag = log_structured_error(
                    problem=f"拒绝直接向桌面端 Antigravity IDE 会话 ({raw_session_id[:8]}) 原地写库",
                    cause="桌面端 language_server.exe 持有内存态锁，双进程同时写同一 SQLite 会引发脑裂损坏",
                    fix="请启用「安全克隆至后台 CLI 继续 (Fork-to-CLI)」或打开桌面端 IDE。",
                )
                return {"ok": False, "error": diag, "code": "SPLIT_BRAIN_PROTECTED"}

            ok, new_uid_or_err = fork_ide_session_to_cli(raw_session_id)
            if not ok:
                return {"ok": False, "error": new_uid_or_err, "code": "FORK_FAILED"}
            forked_from = unified_id
            unified_id = new_uid_or_err
            agent_type = "agy_cli"
            overrides = self.meta_db.get_session_overrides().get(unified_id, overrides)


        # 2. 更新会话元数据与执行策略
        workdir = project_path or overrides.get("project_path") or str(PROJECT_ROOT)
        if not Path(workdir).exists():
            workdir = str(PROJECT_ROOT)

        self.meta_db.upsert_session_meta(
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_session_id,
            project_path=workdir,
            title=overrides.get("title") or prompt[:60],
            execution_mode=mode,
            model_name=chosen_model,
            parent_unified_id=forked_from,
        )

        # 3. 启动后台流式进程
        turn_ctx = TurnContext(
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_session_id,
            prompt=prompt,
            workdir=workdir,
            execution_mode=mode,
            model_name=chosen_model,
        )
        asyncio.create_task(self._run_agent_turn_stream(turn_ctx))

        return {
            "ok": True,
            "unified_id": unified_id,
            "forked_from": forked_from,
            "execution_mode": mode,
            "model_name": chosen_model,
            "status": "running",
        }

    async def _watch_direct_turn_progress(
        self, unified_id: str, agent_type: str, raw_session_id: str
    ) -> None:
        """为通过原生直连通道（如 LS Connect-RPC）派发的任务提供进度与完成状态同步。"""
        adapter = self.adapters.get(agent_type)
        if not adapter:
            return
        try:
            initial_items, _, _ = adapter.get_session_messages_page(raw_session_id, limit=5)
            last_step = initial_items[-1].step_index if initial_items else 0

            # 最多跟踪 120 秒，每 1.5 秒检查一次是否有新增消息产生
            idle_count = 0
            for _ in range(80):
                await asyncio.sleep(1.5)
                curr_items, _, _ = adapter.get_session_messages_page(raw_session_id, limit=5)
                curr_step = curr_items[-1].step_index if curr_items else 0
                if curr_step > last_step:
                    last_step = curr_step
                    idle_count = 0
                    await self.broadcast_event(
                        "STREAM_DELTA",
                        {
                            "unified_id": unified_id,
                            "delta_type": "step_updated",
                            "step_index": curr_step,
                        },
                    )
                else:
                    idle_count += 1
                    # 若已有新 step 且连续 4 次无变化，判定该 turn 已生成完毕
                    if last_step > (initial_items[-1].step_index if initial_items else 0) and idle_count >= 4:
                        break

            await self.broadcast_event(
                "SESSION_TURN_FINISHED",
                {"unified_id": unified_id, "via_direct_dispatch": True},
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log_structured_error(
                problem=f"原生直连通道会话增量同步异常 ({unified_id})",
                cause=str(exc),
                fix="系统已自动回退到常规文件变动轮询机制。",
                exc=exc,
            )
        finally:
            self.active_direct_tasks.pop(unified_id, None)


    async def _run_agent_turn_stream(
        self,
        ctx: TurnContext,
    ) -> None:
        """
        在后台拉起对应 Agent 的流式子进程（`stream-json` 或 `opencode run`），
        实时解析思考增量、工具调用、权限审批请求并通过 WebSocket 推送到手机端。
        """
        unified_id = ctx.unified_id
        # 若该会话已有旧进程在跑，先安全清理
        if unified_id in self.active_processes:
            await self.abort_session(unified_id, reason="启动新一轮对话替换旧进程")

        cmd = self._build_cli_command(
            agent_type=ctx.agent_type,
            raw_session_id=ctx.raw_session_id,
            prompt=ctx.prompt,
            execution_mode=ctx.execution_mode,
            model_name=ctx.model_name,
            approved_tools=ctx.approved_tools,
        )

        await self.broadcast_event(
            "SESSION_TURN_STARTED",
            {
                "unified_id": unified_id,
                "agent_type": ctx.agent_type,
                "prompt": ctx.prompt,
                "execution_mode": ctx.execution_mode,
            },
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=ctx.workdir,
            )
            self.active_processes[unified_id] = proc
            self.process_last_active[unified_id] = time.time()

            assert proc.stdout is not None
            while True:
                raw_line = await proc.stdout.readline()
                if not raw_line:
                    break
                self.process_last_active[unified_id] = time.time()
                line_str = raw_line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                await self._handle_stream_line(
                    ctx=ctx,
                    line_str=line_str,
                    proc=proc,
                )

            await proc.wait()
        except asyncio.CancelledError:
            if unified_id in self.active_processes:
                await self.abort_session(unified_id, reason="异步任务取消")
            return
        except Exception as exc:
            diag = log_structured_error(
                problem=f"执行 {ctx.agent_type} 流式任务异常 ({ctx.raw_session_id[:8]})",
                cause=str(exc),
                fix="请检查对应 Agent CLI 是否在 PATH 中且登录凭证有效。",
                exc=exc,
                context={"cmd": cmd[:4], "workdir": ctx.workdir},
            )
            await self.broadcast_event(
                "SESSION_ERROR",
                {"unified_id": unified_id, "error": diag},
            )
        finally:
            if self.active_processes.get(unified_id) is proc:
                self.active_processes.pop(unified_id, None)
                self.process_last_active.pop(unified_id, None)
                await self.broadcast_event(
                    "SESSION_TURN_FINISHED",
                    {"unified_id": unified_id},
                )

    def _build_cli_command(
        self,
        agent_type: str,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """多态委托给对应 Agent 适配器组装命令行参数（消除类型级联判断）。"""
        adapter = self.adapters.get(agent_type)
        if not adapter:
            raise ValueError(f"不支持的 Agent 类型: {agent_type}")
        return adapter.build_cli_command(
            raw_session_id=raw_session_id,
            prompt=prompt,
            execution_mode=execution_mode,
            model_name=model_name,
            approved_tools=approved_tools,
        )


    async def _handle_stream_line(
        self,
        ctx: TurnContext,
        line_str: str,
        proc: asyncio.subprocess.Process,
    ) -> None:
        """
        解析单行 `stream-json` 或 CLI 流式输出，并在 `ask` 模式下拦截需审批的工具调用！
        """
        unified_id = ctx.unified_id
        agent_type = ctx.agent_type
        raw_session_id = ctx.raw_session_id
        prompt = ctx.prompt
        workdir = ctx.workdir
        execution_mode = ctx.execution_mode

        try:
            event = json.loads(line_str)
        except json.JSONDecodeError:
            # 普通文本流（如 OpenCode CLI 直接输出文本行）
            await self.broadcast_event(
                "STREAM_DELTA",
                {
                    "unified_id": unified_id,
                    "delta_type": "text",
                    "text": line_str,
                },
            )
            return

        if not isinstance(event, dict):
            return

        ev_type = str(event.get("type") or "")

        # 实时广播流式事件给手机端展示
        await self.broadcast_event(
            "STREAM_JSON_FRAME",
            {
                "unified_id": unified_id,
                "agent_type": agent_type,
                "frame": event,
            },
        )

        # 检查是否触发了工具调用或权限请求（多态委托对应适配器解析，覆盖各 Agent 原生流协议）
        adapter = self.adapters.get(agent_type)
        tool_blocks: list[dict[str, Any]] = (
            adapter.extract_stream_tool_blocks(event) if adapter else []
        )


        if execution_mode == ExecutionMode.ASK.value and tool_blocks:
            for tb in tool_blocks:
                t_name = str(tb.get("name") or tb.get("tool_name") or "Tool")
                t_id = str(tb.get("id") or f"tc_{int(time.time() * 1000)}")
                t_input = tb.get("input") or tb.get("arguments") or tb.get("args") or {}
                summary, diff_txt = extract_tool_summary_and_diff(t_name, t_input)
                risk = classify_tool_risk(t_name, summary)

                # 只读工具（如 Read / Glob / Grep / read_file）免打扰直接放行；写入与命令执行类挂起等待手机端审批
                is_readonly = any(
                    k in t_name.lower()
                    for k in ("read", "glob", "grep", "list", "search", "view")
                )
                if not is_readonly:
                    await self.request_mobile_tool_approval(
                        unified_id=unified_id,
                        agent_type=agent_type,
                        raw_session_id=raw_session_id,
                        tool_call_id=t_id,
                        tool_name=t_name,
                        args_summary=summary,
                        diff_text=diff_txt,
                        risk_level=risk,
                        prompt_context=prompt,
                        project_path=workdir,
                        proc=proc,
                    )

    async def request_mobile_tool_approval(
        self,
        unified_id: str,
        agent_type: str,
        raw_session_id: str,
        tool_call_id: str,
        tool_name: str,
        args_summary: str,
        diff_text: str,
        risk_level: str,
        prompt_context: str,
        project_path: str,
        proc: Optional[asyncio.subprocess.Process] = None,
    ) -> dict[str, Any]:
        """
        挂起当前工具调用，向手机端推送 `WAITING_APPROVAL` 弹窗与震动唤醒信号，
        并阻塞等待手机端返回 `approved` 或 `denied`！
        """
        approval = PendingApproval(
            approval_id=f"apr_{uuid.uuid4().hex[:10]}",
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_summary=args_summary,
            diff_text=diff_text,
            risk_level=risk_level,
            prompt_context=prompt_context,
            project_path=project_path,
            created_at=utc_now_iso(),
        )
        self.pending_approvals[unified_id] = approval

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._approval_futures[unified_id] = fut

        await self.broadcast_event(
            "APPROVAL_REQUIRED",
            approval.to_dict(),
        )

        try:
            result = await fut
            decision = str(result.get("decision") or "denied")
            feedback_msg = str(result.get("feedback") or "").strip()
            if decision == "denied":
                if proc and proc.stdin:
                    try:
                        deny_frame = json.dumps(
                            {
                                "type": "control_response",
                                "tool_use_id": tool_call_id,
                                "behavior": "deny",
                                "message": feedback_msg or "用户拒绝执行该操作",
                            },
                            ensure_ascii=False,
                        )
                        proc.stdin.write((deny_frame + "\n").encode("utf-8"))
                        await proc.stdin.drain()
                    except Exception as exc:
                        log_structured_error(
                            problem=f"向子进程 stdin 发送拒绝帧失败 ({unified_id})",
                            cause=str(exc),
                            fix="子进程标准输入可能已提前关闭，系统将直接终止子进程树。",
                            exc=exc,
                        )
                if proc and proc.pid:
                    kill_windows_process_tree(proc.pid)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except Exception as exc:
                        log_structured_error(
                            problem=f"等待旧子进程退出超时 ({unified_id})",
                            cause=str(exc),
                            fix="系统将强制移除进程并继续执行。",
                            exc=exc,
                        )
                self.active_processes.pop(unified_id, None)
                self.process_last_active.pop(unified_id, None)

                # 用户在拒绝时若填写了替代指导意见，自动将指导意见发给会话继续执行
                if feedback_msg:
                    followup = f"[已拒绝工具 {tool_name}] 请勿执行刚才的操作，按以下要求调整方案：{feedback_msg}"

                    async def _send_followup_prompt() -> None:
                        try:
                            await self.send_prompt_to_session(
                                unified_id=unified_id,
                                prompt=followup,
                                project_path=project_path,
                            )
                        except Exception as exc:
                            log_structured_error(
                                problem=f"拒绝工具后发送替代方案失败 ({unified_id})",
                                cause=str(exc),
                                fix="请检查该会话进程状态或手动在输入框重新发送。",
                                exc=exc,
                            )

                    f_task = asyncio.create_task(_send_followup_prompt())
                    self.background_tasks.add(f_task)
                    f_task.add_done_callback(self.background_tasks.discard)
            elif decision == "approved" and proc and proc.stdin:
                # 尝试向支持 stdio 审批帧的进程 stdin 写回确认
                try:
                    resp_frame = json.dumps(
                        {
                            "type": "control_response",
                            "tool_use_id": tool_call_id,
                            "behavior": "allow",
                        }
                    )
                    proc.stdin.write((resp_frame + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                except Exception as exc:
                    log_structured_error(
                        problem=f"向子进程 stdin 写回权限审批帧失败 ({unified_id})",
                        cause=str(exc),
                        fix="子进程管道可能已关闭，系统已记录审计状态。",
                        exc=exc,
                    )
            return result
        finally:
            self.pending_approvals.pop(unified_id, None)
            self._approval_futures.pop(unified_id, None)

    async def resolve_tool_approval(
        self,
        unified_id: str,
        decision: str,  # "approved" | "denied" | "approve_session_edits"
        feedback: str = "",
    ) -> dict[str, Any]:
        """处理手机端用户点击「允许」或「拒绝」工具调用的决策，并记录审计日志。"""
        pa = self.pending_approvals.get(unified_id)
        if not pa:
            return {"ok": False, "message": "该审批请求已处理或已过期"}

        normalized_decision = "approved" if decision.startswith("approve") else "denied"
        self.meta_db.record_approval_log(
            unified_id=unified_id,
            tool_call_id=pa.tool_call_id,
            tool_name=pa.tool_name,
            tool_args_summary=pa.args_summary,
            risk_level=pa.risk_level,
            decision=normalized_decision,
        )

        if decision == "approve_session_edits":
            self.meta_db.upsert_session_meta(
                unified_id=unified_id,
                agent_type=pa.agent_type,
                raw_session_id=pa.raw_session_id,
                execution_mode=ExecutionMode.ACCEPT_EDITS.value,
            )

        # 多态通知适配器响应原生权限通道（如 OpenCode HTTP Bridge）
        adapter = self.adapters.get(pa.agent_type)
        if adapter:
            adapter.respond_permission(
                raw_session_id=pa.raw_session_id,
                tool_call_id=pa.tool_call_id,
                decision=normalized_decision,
            )


        fut = self._approval_futures.get(unified_id)
        if fut and not fut.done():
            fut.set_result({"decision": normalized_decision, "feedback": feedback})

        self.pending_approvals.pop(unified_id, None)
        await self.broadcast_event(
            "APPROVAL_RESOLVED",
            {
                "unified_id": unified_id,
                "approval_id": pa.approval_id,
                "tool_call_id": pa.tool_call_id,
                "decision": normalized_decision,
                "feedback": feedback,
            },
        )
        return {
            "ok": True,
            "unified_id": unified_id,
            "decision": normalized_decision,
            "feedback": feedback.strip(),
        }
