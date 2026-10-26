# -*- coding: utf-8 -*-
"""
AgentDeck 全双工命令总线 (Command Bus) 与幂等防重调度器。

核心职责：
1. 统一接收手机端与桌面端通过 WebSocket 发送的结构化命令帧 (`action: "execute_command"`)。
2. 维护带 TTL 淘汰的客户端消息幂等表 (`client_msg_id`)，防弱网断线重发导致的重复执行。
3. 调度底层 Orchestrator、MetaDatabase 与 Agent 适配器，支持透明 Fork-to-CLI 与安全中断。
4. 立即向客户端返回高可靠 ACK 确认帧，并通过 EventBus 广播全端秒级同步。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from backend.core.logger import log_structured_error, logger


class CommandBus:
    """全双工指令调度中枢与幂等防重引擎。"""

    def __init__(self, orchestrator: Any, ttl_seconds: float = 3600.0) -> None:
        self.orchestrator = orchestrator
        self.ttl_seconds = ttl_seconds
        # 记录已处理的命令：client_msg_id -> {"status": str, "result": Any, "error": str, "ts": float}
        self._processed_commands: dict[str, dict[str, Any]] = {}
        # 会话级执行锁：防止同会话并发重复派发相同指令
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _cleanup_expired_records(self) -> None:
        """淘汰超时的历史命令幂等记录，防止内存泄漏。"""
        now = time.monotonic()
        expired_keys = [
            k for k, v in self._processed_commands.items()
            if now - v.get("ts", now) > self.ttl_seconds
        ]
        for k in expired_keys:
            self._processed_commands.pop(k, None)

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def execute_command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """
        处理单条命令帧，并返回结构化 ACK 回执。
        帧规范：
        {
            "action": "execute_command",
            "client_msg_id": "cmsg_xxx",
            "command_type": "PIN_SESSION" | "RENAME_SESSION" | "SEND_PROMPT" | "ABORT_SESSION" | "CREATE_SESSION" | "DELETE_SESSION" | "APPROVE_TOOL",
            "session_id": "agy_cli:xxx",
            "payload": { ... },
            "client_timestamp": 1729770000000
        }
        """
        self._cleanup_expired_records()

        client_msg_id = str(envelope.get("client_msg_id") or "").strip()
        command_type = str(envelope.get("command_type") or "").strip().upper()
        session_id = str(envelope.get("session_id") or "").strip()
        payload = envelope.get("payload") or {}

        if not client_msg_id:
            return {
                "action": "command_ack",
                "type": "COMMAND_ACK",
                "client_msg_id": "",
                "command_type": command_type,
                "status": "error",
                "error": "缺少 client_msg_id",
            }

        # 1. 幂等性命中检查：成功(in_progress/success)直接回放缓存 ACK；error 态允许重放（D16）
        if client_msg_id in self._processed_commands:
            cached = self._processed_commands[client_msg_id]
            if cached.get("status") == "error":
                last_err = str(cached.get("error") or "")
                self._processed_commands.pop(client_msg_id, None)
                logger.warning(f"[CommandBus] error 态放行重放: {client_msg_id} (上次错误: {last_err[:120]})")
            else:
                logger.info(f"[CommandBus] 命中幂等防重缓存: {client_msg_id} ({command_type})")
                return self._build_duplicate_ack(client_msg_id, command_type, cached)

        # 标记为处理中
        self._processed_commands[client_msg_id] = {
            "status": "in_progress",
            "result": None,
            "error": None,
            "ts": time.monotonic(),
        }

        # S2：内存未命中时查持久化幂等表（重启恢复）；done → 直接回放 ACK，error → 允许重放
        persisted = self._get_persisted_record(client_msg_id)
        if persisted:
            if persisted.get("status") == "done":
                logger.info(f"[CommandBus] 命中持久化幂等记录(重启后重放 ACK): {client_msg_id}")
                return self._build_duplicate_ack(client_msg_id, command_type, {
                    "status": "success", "result": persisted, "error": None,
                })
            # error 态：不阻塞重放（2am 静默故障防线，D16）—— 走全新执行

        payload_summary = self._build_payload_summary(command_type, session_id, payload)
        meta_db = getattr(self.orchestrator, "meta_db", None)
        if meta_db is not None and hasattr(meta_db, "record_command"):
            try:
                meta_db.record_command(client_msg_id, session_id, "in_progress", payload_summary)
            except Exception:
                pass

        try:
            handler = getattr(self, f"_handle_{command_type.lower()}", None)
            if not handler:
                raise ValueError(f"不支持的指令类型: {command_type}")

            result = await handler(session_id=session_id, payload=payload, client_msg_id=client_msg_id)

            self._processed_commands[client_msg_id] = {
                "status": "success",
                "result": result,
                "error": None,
                "ts": time.monotonic(),
            }
            self._persist_command(meta_db, client_msg_id, session_id, "done", payload_summary)

            return {
                "action": "command_ack",
                "type": "COMMAND_ACK",
                "client_msg_id": client_msg_id,
                "command_type": command_type,
                "status": "success",
                "result": result,
                "error": None,
            }
        except Exception as exc:
            err_msg = str(exc)
            log_structured_error(
                problem=f"指令执行失败 [{command_type}] ({client_msg_id})",
                cause=err_msg,
                fix="检查指令载荷格式、会话状态或进程执行上下文。",
                exc=exc,
            )
            self._processed_commands[client_msg_id] = {
                "status": "error",
                "result": None,
                "error": err_msg,
                "ts": time.monotonic(),
            }
            self._persist_command(meta_db, client_msg_id, session_id, "error", payload_summary)
            return {
                "action": "command_ack",
                "type": "COMMAND_ACK",
                "client_msg_id": client_msg_id,
                "command_type": command_type,
                "status": "error",
                "result": None,
                "error": err_msg,
            }

    # -------------------------------------------------------------------------
    # S2 幂等持久化辅助
    # -------------------------------------------------------------------------

    def _build_duplicate_ack(self, client_msg_id: str, command_type: str, cached: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "command_ack",
            "type": "COMMAND_ACK",
            "client_msg_id": client_msg_id,
            "command_type": command_type,
            "status": cached.get("status", "success") if cached.get("status") != "in_progress" else "in_progress",
            "result": cached.get("result"),
            "error": cached.get("error"),
            "is_duplicate": True,
        }

    def _get_persisted_record(self, client_msg_id: str) -> Optional[dict[str, Any]]:
        meta_db = getattr(self.orchestrator, "meta_db", None)
        if meta_db is None or not hasattr(meta_db, "get_command_record"):
            return None
        try:
            return meta_db.get_command_record(client_msg_id)
        except Exception:
            return None

    def _persist_command(
        self, meta_db: Any, command_id: str, unified_id: str, status: str, payload_summary: str
    ) -> None:
        if meta_db is None or not hasattr(meta_db, "record_command"):
            return
        try:
            meta_db.record_command(command_id, unified_id, status, payload_summary)
        except Exception:
            pass

    @staticmethod
    def _build_payload_summary(command_type: str, session_id: str, payload: dict[str, Any]) -> str:
        """白名单字段 + 截断摘要（严禁写入 prompt 全文，防元数据库膨胀与内容泄漏）。"""
        try:
            import json as _json
            summary: dict[str, Any] = {"command_type": command_type}
            if session_id:
                summary["session_id"] = session_id
            for key in ("agent_type", "project_path", "title", "is_pinned", "decision"):
                if key in payload and payload.get(key) is not None:
                    summary[key] = str(payload.get(key))[:80]
            prompt = str(payload.get("prompt") or "")
            if prompt:
                summary["prompt_len"] = len(prompt)
            return _json.dumps(summary, ensure_ascii=False)
        except Exception:
            return f"{command_type}@{session_id}"

    # -------------------------------------------------------------------------
    # 具体指令业务处理器
    # -------------------------------------------------------------------------

    async def _handle_pin_session(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """置顶或取消置顶会话。"""
        if not session_id or ":" not in session_id:
            raise ValueError(f"无效的 session_id: {session_id}")
        is_pinned = 1 if payload.get("is_pinned", True) else 0
        agent_type, raw_id = session_id.split(":", 1)

        init_title = None
        init_path = None
        existing_meta = self.orchestrator.meta_db.get_session_overrides().get(session_id)
        if not existing_meta and agent_type in self.orchestrator.adapters:
            try:
                base_sess = self.orchestrator.adapters[agent_type].get_session(raw_id)
                if base_sess:
                    init_title = getattr(base_sess, "title", None)
                    init_path = getattr(base_sess, "project_path", None)
            except Exception:
                pass

        meta = self.orchestrator.meta_db.upsert_session_meta(
            unified_id=session_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            title=init_title,
            project_path=init_path,
            is_pinned=is_pinned,
        )
        self.orchestrator.bump_version()

        # 广播全端秒级同步
        await self.orchestrator.broadcast_event(
            "SESSION_UPDATED",
            {
                "unified_id": session_id,
                "meta": meta,
                "action": "pin",
                "is_pinned": bool(is_pinned),
            },
        )
        return {"unified_id": session_id, "is_pinned": bool(is_pinned)}

    async def _handle_rename_session(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """重命名会话标题。"""
        if not session_id or ":" not in session_id:
            raise ValueError(f"无效的 session_id: {session_id}")
        new_title = str(payload.get("title") or "").strip()
        if not new_title:
            raise ValueError("会话新标题不能为空")

        agent_type, raw_id = session_id.split(":", 1)
        init_path = None
        existing_meta = self.orchestrator.meta_db.get_session_overrides().get(session_id)
        if not existing_meta and agent_type in self.orchestrator.adapters:
            try:
                base_sess = self.orchestrator.adapters[agent_type].get_session(raw_id)
                if base_sess:
                    init_path = getattr(base_sess, "project_path", None)
            except Exception:
                pass

        meta = self.orchestrator.meta_db.upsert_session_meta(
            unified_id=session_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            project_path=init_path,
            title=new_title,
        )
        self.orchestrator.bump_version()

        # 双向同步回写至对应 Agent 原生存储/数据库
        adapter = self.orchestrator.adapters.get(agent_type)
        if adapter and hasattr(adapter, "rename_session"):
            try:
                adapter.rename_session(raw_id, new_title)
            except Exception as sync_exc:
                logger.warning(f"向 Agent {agent_type} 回写原生会话名称失败: {sync_exc}")

        await self.orchestrator.broadcast_event(
            "SESSION_UPDATED",
            {
                "unified_id": session_id,
                "meta": meta,
                "action": "rename",
                "title": new_title,
            },
        )
        return {"unified_id": session_id, "title": new_title}

    async def _handle_create_session(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """在手机端新建一个 Agent 会话。"""
        agent_type = str(payload.get("agent_type") or "agy_cli").strip()
        project_path = str(payload.get("project_path") or "").strip()
        title = str(payload.get("title") or "").strip()
        initial_prompt = str(payload.get("initial_prompt") or "").strip()
        model_name = payload.get("model_name")
        execution_mode = payload.get("execution_mode")

        unified_id, meta = await self.orchestrator.create_session(
            agent_type=agent_type,
            project_path=project_path,
            title=title,
            initial_prompt=initial_prompt,
            model_name=model_name,
            execution_mode=execution_mode,
        )

        await self.orchestrator.broadcast_event(
            "SESSION_CREATED",
            {
                "unified_id": unified_id,
                "meta": meta,
            },
        )
        return {"unified_id": unified_id, "meta": meta}

    async def _handle_delete_session(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """删除会话：委托 Orchestrator 深模块一站式彻底物理删除并写入墓碑。"""
        target_id = session_id or str(payload.get("unified_id") or "").strip()
        if not target_id:
            raise ValueError("缺少要删除的 session_id")

        await self.orchestrator.delete_session(target_id)
        return {"unified_id": target_id, "deleted": True}

    async def _handle_send_prompt(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """向会话发送提示词指令（包含 IDE 会话透明 Fork-to-CLI 与子进程拉起）。"""
        if not session_id or ":" not in session_id:
            raise ValueError(f"无效的 session_id: {session_id}")
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("发送指令内容不能为空")

        project_path = payload.get("project_path")
        execution_mode = payload.get("execution_mode")
        model_name = payload.get("model_name")
        auto_fork_ide = bool(payload.get("auto_fork_ide", True))

        lock = self._get_session_lock(session_id)
        async with lock:
            res = await self.orchestrator.send_prompt_to_session(
                unified_id=session_id,
                prompt=prompt,
                project_path=project_path,
                execution_mode=execution_mode,
                model_name=model_name,
                auto_fork_ide=auto_fork_ide,
            )
            return res

    async def _handle_abort_session(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """中止会话执行（优雅拦截与递归杀树）。"""
        if not session_id or ":" not in session_id:
            raise ValueError(f"无效的 session_id: {session_id}")

        reason = str(payload.get("reason") or "手机端手动中断").strip()
        agent_type, _ = session_id.split(":", 1)

        # 检查是否为桌面 IDE 活跃会话
        is_active = self.orchestrator.is_session_active(session_id)
        if agent_type == "agy_ide" and is_active and session_id not in self.orchestrator.active_direct_tasks:
            # 优雅拦截提示
            return {
                "unified_id": session_id,
                "status": "ide_active_manual_stop_required",
                "message": "该任务正在桌面 IDE 中执行，无法远程强杀，请在电脑端停止。",
            }

        await self.orchestrator.abort_session(session_id, reason=reason)
        return {"unified_id": session_id, "status": "aborted", "reason": reason}

    async def _handle_tool_approval(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        return await self._handle_approve_tool(session_id=session_id, payload=payload, **_)

    async def _handle_approve_tool(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """审批工具调用。"""
        if not session_id:
            raise ValueError("缺少 session_id")
        decision = str(payload.get("decision") or "").strip()
        feedback = str(payload.get("feedback") or "").strip()
        return await self.orchestrator.resolve_tool_approval(
            unified_id=session_id,
            decision=decision,
            feedback=feedback,
        )

    async def _handle_switch_model(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """模型热切换与配置同步（适用于所有 Agent）。"""
        if not session_id or ":" not in session_id:
            raise ValueError(f"无效的 session_id: {session_id}")
        new_model = str(payload.get("model_name") or "").strip()
        if not new_model:
            raise ValueError("新模型名称不能为空")

        agent_type, raw_id = session_id.split(":", 1)
        meta = self.orchestrator.meta_db.upsert_session_meta(
            unified_id=session_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            model_name=new_model,
        )
        self.orchestrator.bump_version()

        await self.orchestrator.broadcast_event(
            "SESSION_CONFIG_CHANGED",
            {
                "unified_id": session_id,
                "model_name": new_model,
                "action": "switch_model",
            },
        )
        return {"unified_id": session_id, "model_name": new_model}

    async def _handle_submit_interactive_answer(self, session_id: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        """交互式多选问答/确认反向应答回填给底层 Agent（适用于所有 Agent）。"""
        if not session_id or ":" not in session_id:
            raise ValueError(f"无效的 session_id: {session_id}")
        answers = payload.get("answers") or payload.get("answer") or ""
        agent_type, raw_id = session_id.split(":", 1)

        # 1. 尝试向正在运行的子进程 stdin 管道或 HTTP bridge / LS 注入答案
        delivered = False
        active_proc = getattr(self.orchestrator, "active_processes", {}).get(session_id)
        if active_proc and active_proc.stdin and not active_proc.stdin.is_closing():
            try:
                line = (json.dumps(answers, ensure_ascii=False) if isinstance(answers, (dict, list)) else str(answers)) + "\n"
                active_proc.stdin.write(line.encode("utf-8"))
                await active_proc.stdin.drain()
                delivered = True
            except Exception as pipe_exc:
                logger.warning(f"向子进程 stdin 写入交互答案失败: {pipe_exc}")

        # 2. 如果是 Antigravity IDE 会话且进程不处于 CLI stdin，通过 LS bridge 下发应答文本
        if not delivered and agent_type == "agy_ide":
            adapter = self.orchestrator.adapters.get(agent_type)
            if adapter and hasattr(adapter, "send_prompt_via_ls"):
                ans_text = str(answers) if not isinstance(answers, list) else ", ".join(str(a) for a in answers)
                delivered = adapter.send_prompt_via_ls(raw_id, ans_text)

        await self.orchestrator.broadcast_event(
            "INTERACTIVE_ANSWER_SUBMITTED",
            {
                "unified_id": session_id,
                "answers": answers,
                "delivered": delivered,
            },
        )
        return {"unified_id": session_id, "delivered": delivered}
