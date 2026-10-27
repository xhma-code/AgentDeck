"""
工具审批状态机与协同器深模块 (ApprovalCoordinator)。

职责：
1. 维护内存态 `pending_approvals` 队列与 `_approval_futures` 异步挂起池。
2. 协调跨设备工具权限决策：下发广播、阻塞等待移动端响应、超时处理与审计日志持久化。
3. 提供统一的状态机快照，支持移动端断线切回 0 丢失与幂等性保障。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Coroutine, Optional

from backend.adapters.base import BaseAgentAdapter, ExecutionMode
from backend.core.database import MetaDatabase, utc_now_iso
from backend.core.logger import log_structured_error, logger
from backend.engines.event_bus import RealtimeEventBus


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


class ApprovalCoordinator:
    """
    工具审批状态机协调总管。
    将繁琐的审批挂起、Future 阻塞等待、stdin 协商、审计落库与事件广播封装为内聚的深模块。
    """

    def __init__(
        self,
        meta_db: MetaDatabase,
        event_bus: RealtimeEventBus,
        adapters_getter: Optional[Callable[[], dict[str, BaseAgentAdapter]]] = None,
    ) -> None:
        self.meta_db = meta_db
        self.event_bus = event_bus
        self._adapters_getter = adapters_getter or (lambda: {})
        self.pending_approvals: dict[str, PendingApproval] = {}
        self._approval_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def get_pending(self, unified_id: str) -> Optional[PendingApproval]:
        """获取指定会话当前挂起的审批请求（若有）。"""
        return self.pending_approvals.get(unified_id)

    def is_pending(self, unified_id: str) -> bool:
        """检查指定会话是否存在挂起的审批请求。"""
        return unified_id in self.pending_approvals

    def get_snapshot_list(self) -> list[dict[str, Any]]:
        """获取用于断线重连或全局状态快照的待审批列表。"""
        return [pa.to_dict() for pa in self.pending_approvals.values()]

    def cancel_session_approval(self, unified_id: str) -> None:
        """当会话中断、退出或重置时，清理挂起的审批与 Future。"""
        self.pending_approvals.pop(unified_id, None)
        fut = self._approval_futures.pop(unified_id, None)
        if fut and not fut.done():
            fut.cancel()

    async def register_and_wait(
        self,
        approval_obj: Optional[PendingApproval] = None,
        *,
        unified_id: str = "",
        agent_type: str = "",
        raw_session_id: str = "",
        tool_call_id: str = "",
        tool_name: str = "",
        args_summary: str = "",
        diff_text: str = "",
        risk_level: str = "normal",
        prompt_context: str = "",
        project_path: str = "",
        proc: Optional[asyncio.subprocess.Process] = None,
        on_cleanup_proc: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None,
        on_followup_prompt: Optional[Callable[[str, str, str], Coroutine[Any, Any, None]]] = None,
    ) -> dict[str, Any]:
        """
        登记新的待审批工具调用，广播 WAITING_APPROVAL 事件，并阻塞等待手机端决策返回。
        处理用户批准、拒绝时的 stdin 帧回写、进程安全清理以及替代方案后续指令派发。
        """
        if approval_obj is not None:
            approval = approval_obj
            unified_id = approval.unified_id
            agent_type = approval.agent_type
            raw_session_id = approval.raw_session_id
            tool_call_id = approval.tool_call_id
            tool_name = approval.tool_name
            project_path = approval.project_path
        else:
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

        await self.event_bus.broadcast(
            "APPROVAL_REQUIRED",
            approval.to_dict(),
        )

        try:
            from backend.core.push_service import get_push_service
            push_title = f"⚠️ 高危工具审批: {approval.tool_name}" if approval.risk_level == "high" else f"工具审批: {approval.tool_name}"
            push_body = approval.args_summary[:120] or "需要手机端确认放行"
            get_push_service().send_notification(
                title=push_title,
                body=push_body,
                url=f"/?session={approval.unified_id}&focus_tool={approval.tool_call_id}",
                high_priority=(approval.risk_level == "high"),
                tag=f"apr-{approval.unified_id}",
            )
        except Exception as push_err:
            logger.debug("触发 Web Push 异常: %s", push_err)

        try:
            result = await fut
            decision = str(result.get("decision") or "denied")
            feedback_msg = str(result.get("feedback") or "").strip()

            if decision == "denied":
                stdin_notified = False
                if proc and proc.stdin and not proc.stdin.is_closing():
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
                        stdin_notified = True
                    except Exception as exc:
                        log_structured_error(
                            problem=f"向子进程 stdin 发送拒绝帧失败 ({unified_id})",
                            cause=str(exc),
                            fix="子进程标准输入可能已提前关闭，系统将直接终止子进程树。",
                            exc=exc,
                        )

                if not stdin_notified:
                    if on_cleanup_proc:
                        await on_cleanup_proc(unified_id)

                    if feedback_msg and on_followup_prompt:
                        followup = f"[已拒绝工具 {tool_name}] 请勿执行刚才的操作，按以下要求调整方案：{feedback_msg}"
                        res = on_followup_prompt(unified_id, followup, project_path)
                        if asyncio.iscoroutine(res):
                            await res


            elif decision == "approved" and proc and proc.stdin:
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

    async def resolve(
        self,
        unified_id: str,
        decision: str,  # "approved" | "denied" | "approve_session_edits"
        feedback: str = "",
    ) -> dict[str, Any]:
        """处理手机端用户点击「允许」或「拒绝」工具调用的决策，记录审计日志并唤醒阻塞协程。"""
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

        adapters = self._adapters_getter()
        adapter = adapters.get(pa.agent_type)
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
        await self.event_bus.broadcast(
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
