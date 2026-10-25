# -*- coding: utf-8 -*-
"""
多 Agent 子进程流式执行与输出行解析管道。
负责子进程拉起、stdio 异步交互、高危工具调用拦截与 WebSocket 实时广播。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    ExecutionMode,
    classify_tool_risk,
    extract_tool_summary_and_diff,
)
from backend.core.database import PROJECT_ROOT, MetaDatabase, utc_now_iso
from backend.core.logger import log_structured_error, logger
from backend.engines.approval import PendingApproval
from backend.engines.process_utils import TurnContext, drain_subprocess_stderr


def get_system_proxy_env() -> dict[str, str]:
    """若当前系统未显式配置 HTTP_PROXY，自动尝试读取 Windows 注册表代理设置。"""
    env = os.environ.copy()
    if env.get("HTTP_PROXY") or env.get("http_proxy"):
        return env
    if sys.platform == "win32":
        try:
            import winreg

            reg = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
            key = winreg.OpenKey(reg, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            if server:
                proxy_url = f"http://{server}" if not str(server).startswith("http") else str(server)
                env["HTTP_PROXY"] = proxy_url
                env["HTTPS_PROXY"] = proxy_url
                env["http_proxy"] = proxy_url
                env["https_proxy"] = proxy_url
        except Exception:
            pass
    return env


class StreamRunner:
    """管理 Agent CLI 子进程生命周期与流式事件解析管道。"""

    def __init__(
        self,
        adapters: dict[str, BaseAgentAdapter],
        active_processes: dict[str, asyncio.subprocess.Process],
        process_last_active: dict[str, float],
        active_direct_tasks: dict[str, asyncio.Task[None]],
        process_semaphore: asyncio.Semaphore,
        broadcast_event_fn: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]],
        request_approval_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
        abort_session_fn: Callable[..., Coroutine[Any, Any, None]],
        meta_db: Optional[MetaDatabase] = None,
        normalize_mode_fn: Optional[Callable[[str, Optional[str]], str]] = None,
        resolve_default_model_fn: Optional[Callable[[str], str]] = None,
        bump_version_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        self.adapters = adapters
        self.active_processes = active_processes
        self.process_last_active = process_last_active
        self.active_direct_tasks = active_direct_tasks
        self.process_semaphore = process_semaphore
        self.broadcast_event = broadcast_event_fn
        self.request_mobile_tool_approval = request_approval_fn
        self.abort_session = abort_session_fn
        self.meta_db = meta_db
        self.normalize_mode_fn = normalize_mode_fn
        self.resolve_default_model_fn = resolve_default_model_fn
        self.bump_version_fn = bump_version_fn

    def build_cli_command(
        self,
        agent_type: str,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """多态委托给对应 Agent 适配器组装命令行参数。"""
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

    async def dispatch_prompt(
        self,
        unified_id: str,
        prompt: str,
        meta_db: Optional[MetaDatabase] = None,
        project_path: Optional[str] = None,
        execution_mode: Optional[str] = None,
        model_name: Optional[str] = None,
        auto_fork_ide: bool = False,
        approved_tools: Optional[list[str]] = None,
        normalize_mode_fn: Optional[Callable[[str, Optional[str]], str]] = None,
        resolve_default_model_fn: Optional[Callable[[str], str]] = None,
        bump_version_fn: Optional[Callable[[], None]] = None,
        allow_real_process_in_test: bool = False,
    ) -> dict[str, Any]:
        target_db = meta_db or self.meta_db
        target_norm = normalize_mode_fn or self.normalize_mode_fn
        target_res = resolve_default_model_fn or self.resolve_default_model_fn
        target_bump = bump_version_fn or self.bump_version_fn
        if ":" not in unified_id:
            raise ValueError(f"无效的 unified_id: {unified_id}")
        agent_type, raw_id = unified_id.split(":", 1)
        adapter = self.adapters.get(agent_type)
        if not adapter:
            raise ValueError(f"未知的 Agent 类型: {agent_type}")

        actual_unified_id, actual_raw_id, actual_agent_type = unified_id, raw_id, agent_type
        if target_db and hasattr(target_db, "resolve_unified_id"):
            actual_unified_id = target_db.resolve_unified_id(unified_id)
            if ":" in actual_unified_id:
                actual_agent_type, parsed_raw = actual_unified_id.split(":", 1)
                ov = target_db.get_session_overrides().get(actual_unified_id) or target_db.get_session_overrides().get(unified_id, {})
                actual_raw_id = ov.get("raw_session_id") or parsed_raw
                adapter = self.adapters.get(actual_agent_type) or adapter

        norm_mode = target_norm(actual_agent_type, execution_mode) if target_norm else "auto"
        workdir = project_path or str(PROJECT_ROOT)
        def_model = target_res(actual_agent_type) if target_res else ""
        chosen_model = model_name or def_model

        target_db.upsert_session_meta(
            unified_id=actual_unified_id,
            agent_type=actual_agent_type,
            raw_session_id=actual_raw_id,
            project_path=workdir,
            execution_mode=norm_mode,
            model_name=chosen_model,
        )
        if target_bump:
            target_bump()

        # 1. 优先尝试原生直发通道（如 Antigravity LS Connect-RPC 或 OpenCode HTTP Bridge）
        if actual_agent_type == "agy_ide" and hasattr(adapter, "send_prompt_via_ls"):
            try:
                ls_model = chosen_model if (chosen_model and chosen_model != "default") else ""
                ok = adapter.send_prompt_via_ls(
                    raw_session_id=actual_raw_id,
                    prompt=prompt,
                    model_id=ls_model,
                )
                if ok:
                    if actual_unified_id in self.active_direct_tasks:
                        old_task = self.active_direct_tasks[actual_unified_id]
                        if not old_task.done():
                            old_task.cancel()
                    self.active_direct_tasks[actual_unified_id] = asyncio.create_task(
                        self.watch_direct_turn_progress(
                            actual_unified_id, actual_agent_type, actual_raw_id
                        )
                    )
                    return {
                        "ok": True,
                        "unified_id": actual_unified_id,
                        "via_direct_ls": True,
                        "status": "sent_to_language_server",
                    }
                else:
                    return {
                        "ok": False,
                        "unified_id": actual_unified_id,
                        "error": "Antigravity IDE 实例未响应或未持有该会话，请确保桌面端当前已打开该会话。",
                    }
            except Exception as exc:
                return {
                    "ok": False,
                    "unified_id": actual_unified_id,
                    "error": f"连接 Antigravity IDE 异常: {exc}",
                }

        if hasattr(adapter, "try_direct_dispatch"):
            try:
                direct_res = adapter.try_direct_dispatch(
                    raw_session_id=actual_raw_id,
                    prompt=prompt,
                    model_name=chosen_model,
                    workdir=workdir,
                )
                if direct_res:
                    if actual_unified_id in self.active_direct_tasks:
                        old_task = self.active_direct_tasks[actual_unified_id]
                        if not old_task.done():
                            old_task.cancel()
                    self.active_direct_tasks[actual_unified_id] = asyncio.create_task(
                        self.watch_direct_turn_progress(
                            actual_unified_id, actual_agent_type, actual_raw_id
                        )
                    )
                    return {
                        "ok": True,
                        "unified_id": actual_unified_id,
                        "direct_dispatch": direct_res,
                        "status": "sent_via_direct_service",
                    }
            except Exception as exc:
                logger.debug(f"通过原生直连通道派发异常: {exc}")

        ctx = TurnContext(
            unified_id=actual_unified_id,
            agent_type=actual_agent_type,
            raw_session_id=actual_raw_id,
            prompt=prompt,
            workdir=workdir,
            execution_mode=norm_mode,
            model_name=chosen_model,
            approved_tools=approved_tools,
        )
        asyncio.create_task(
            self.run_agent_turn_stream(
                ctx=ctx,
                allow_real_process_in_test=allow_real_process_in_test,
            )
        )
        ret: dict[str, Any] = {"ok": True, "unified_id": actual_unified_id, "status": "stream_started"}
        if actual_unified_id != unified_id:
            ret["forked_from"] = unified_id
        return ret

    async def watch_direct_turn_progress(
        self, unified_id: str, agent_type: str, raw_session_id: str
    ) -> None:
        """为通过原生直连通道派发的任务提供精确的进度流与完成感知。"""
        adapter = self.adapters.get(agent_type)
        if not adapter:
            return
        try:
            initial_all = adapter.get_session_messages(raw_session_id)
            last_step = initial_all[-1].step_index if initial_all else 0
            initial_count = len(initial_all)
            has_seen_active = False
            consecutive_inactive = 0

            # 轮询上限：最长支持 15 分钟 (600 * 1.5s = 900s) 的复杂开发任务
            for _ in range(600):
                await asyncio.sleep(1.5)
                if hasattr(adapter, "invalidate_cache"):
                    adapter.invalidate_cache()

                # 1. 优先使用 adapter 原生精准活跃状态探测
                is_active = False
                if hasattr(adapter, "is_external_session_active"):
                    try:
                        is_active = bool(adapter.is_external_session_active(raw_session_id))
                    except Exception:
                        is_active = False

                if is_active:
                    has_seen_active = True
                    consecutive_inactive = 0
                else:
                    if has_seen_active:
                        consecutive_inactive += 1

                # 2. 检查是否有新步骤或消息更新
                curr_all = adapter.get_session_messages(raw_session_id)
                curr_step = curr_all[-1].step_index if curr_all else 0
                if curr_step > last_step or len(curr_all) > initial_count:
                    if curr_step > last_step:
                        last_step = curr_step
                    await self.broadcast_event(
                        "STREAM_DELTA",
                        {"unified_id": unified_id, "delta_type": "step_updated", "step_index": curr_step},
                    )

                # 3. 退出判定：
                # 情况 A：若曾明确探测到活跃态，当前已连续 2 次（3s）转入非活跃态 -> 确认任务执行完毕！
                if has_seen_active and consecutive_inactive >= 2:
                    break

                # 情况 B：若未感知到活跃标记（如轮询间隔穿透），但检测到新步骤且已连续 12s 稳定无变动
                if not is_active and (curr_step > (initial_all[-1].step_index if initial_all else 0) or len(curr_all) > initial_count):
                    consecutive_inactive += 1
                    if consecutive_inactive >= 8:  # 8 次 * 1.5s = 12s 静默
                        break

            # 最终完成时，强制刷新一次缓存并广播完成
            if hasattr(adapter, "invalidate_cache"):
                adapter.invalidate_cache()
            await self.broadcast_event(
                "SESSION_TURN_FINISHED",
                {"unified_id": unified_id, "via_direct_dispatch": True},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_structured_error(
                problem=f"原生直连通道会话增量同步异常 ({unified_id})",
                cause=str(exc),
                fix="系统已自动回退到常规文件变动轮询机制。",
                exc=exc,
            )
        finally:
            curr_task = asyncio.current_task()
            if self.active_direct_tasks.get(unified_id) is curr_task:
                self.active_direct_tasks.pop(unified_id, None)

    async def run_agent_turn_stream(
        self,
        ctx: TurnContext,
        allow_real_process_in_test: bool = False,
        cmd_builder: Optional[Callable[..., list[str]]] = None,
    ) -> None:
        """在后台拉起对应 Agent 的流式子进程。"""
        unified_id = ctx.unified_id
        if "PYTEST_CURRENT_TEST" in os.environ and not allow_real_process_in_test:
            await self.broadcast_event("SESSION_TURN_FINISHED", {"unified_id": unified_id, "mock_completed": True})
            return

        if unified_id in self.active_processes:
            await self.abort_session(unified_id, reason="启动新一轮对话替换旧进程")

        builder = cmd_builder or self.build_cli_command
        cmd = builder(
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

        proc: Optional[asyncio.subprocess.Process] = None
        stderr_task: Optional[asyncio.Task[None]] = None
        async with self.process_semaphore:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=ctx.workdir,
                    env=get_system_proxy_env(),
                )
                self.active_processes[unified_id] = proc
                self.process_last_active[unified_id] = time.time()
                stderr_task = asyncio.create_task(
                    drain_subprocess_stderr(proc, f"{ctx.agent_type}:{ctx.raw_session_id[:8]}")
                )

                assert proc.stdout is not None
                while True:
                    raw_line = await proc.stdout.readline()
                    if not raw_line:
                        break
                    self.process_last_active[unified_id] = time.time()
                    line_str = raw_line.decode("utf-8", errors="replace").strip()
                    if line_str:
                        await self._handle_stream_line(ctx=ctx, line_str=line_str, proc=proc)

                await proc.wait()
                if stderr_task and not stderr_task.done():
                    try:
                        await asyncio.wait_for(stderr_task, timeout=1.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        stderr_task.cancel()

                if proc.returncode and proc.returncode != 0:
                    err_msg = f"Agent 进程异常崩溃退出 (Exit Code: {proc.returncode})"
                    logger.error(f"{err_msg} [{ctx.unified_id}]")
                    await self.broadcast_event(
                        "SESSION_CRASHED",
                        {
                            "unified_id": unified_id,
                            "agent_type": ctx.agent_type,
                            "exit_code": proc.returncode,
                            "error": err_msg,
                        },
                    )
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
                await self.broadcast_event("SESSION_ERROR", {"unified_id": unified_id, "error": diag})
            finally:
                current_uid = ctx.unified_id
                if proc is not None:
                    if self.active_processes.get(unified_id) is proc:
                        self.active_processes.pop(unified_id, None)
                        self.process_last_active.pop(unified_id, None)
                    if self.active_processes.get(current_uid) is proc:
                        self.active_processes.pop(current_uid, None)
                        self.process_last_active.pop(current_uid, None)
                await self.broadcast_event("SESSION_TURN_FINISHED", {"unified_id": current_uid, "original_unified_id": unified_id})

    async def _handle_stream_line(
        self,
        ctx: TurnContext,
        line_str: str,
        proc: asyncio.subprocess.Process,
    ) -> None:
        """解析单行 stream-json 或 CLI 流式输出，并在 ask 模式下拦截需审批的工具调用。"""
        unified_id = ctx.unified_id
        try:
            event = json.loads(line_str)
        except json.JSONDecodeError:
            await self.broadcast_event(
                "STREAM_DELTA",
                {"unified_id": unified_id, "delta_type": "text", "text": line_str},
            )
            return

        if not isinstance(event, dict):
            return

        ev_type = str(event.get("type") or event.get("event") or "")

        # Antigravity CLI 首轮动态回填真实 conversation_id
        if ctx.agent_type == "agy_cli":
            real_cid = (
                event.get("conversation_id")
                or (event.get("step_update") or {}).get("conversation_id")
                or (event.get("result") or {}).get("conversation_id")
                or (event.get("init") or {}).get("conversation_id")
            )
            if real_cid and real_cid != ctx.raw_session_id:
                old_unified_id = ctx.unified_id
                new_unified_id = f"{ctx.agent_type}:{real_cid}"
                ctx.raw_session_id = real_cid
                ctx.unified_id = new_unified_id

                if old_unified_id in self.active_processes:
                    self.active_processes[new_unified_id] = self.active_processes[old_unified_id]
                if old_unified_id in self.process_last_active:
                    self.process_last_active[new_unified_id] = self.process_last_active[old_unified_id]

                if self.meta_db:
                    if hasattr(self.meta_db, "remap_session_unified_id"):
                        try:
                            self.meta_db.remap_session_unified_id(old_unified_id, new_unified_id, real_cid)
                        except Exception as db_exc:
                            logger.debug("动态回填真实会话ID异常: %s", db_exc)
                    elif hasattr(self.meta_db, "update_raw_session_id"):
                        try:
                            self.meta_db.update_raw_session_id(old_unified_id, real_cid)
                        except Exception as db_exc:
                            logger.debug("动态回填真实会话ID异常: %s", db_exc)

                await self.broadcast_event(
                    "SESSION_REMAPPED",
                    {
                        "old_unified_id": old_unified_id,
                        "new_unified_id": new_unified_id,
                        "raw_session_id": real_cid,
                    },
                )

        await self.broadcast_event(
            "STREAM_JSON_FRAME",
            {"unified_id": unified_id, "agent_type": ctx.agent_type, "frame": event},
        )

        adapter = self.adapters.get(ctx.agent_type)
        tool_blocks = adapter.extract_stream_tool_blocks(event) if adapter else []
        if ctx.execution_mode == ExecutionMode.ASK.value and tool_blocks:
            await self._intercept_tool_approvals(ctx, tool_blocks, proc, ev_type)

    async def _intercept_tool_approvals(
        self,
        ctx: TurnContext,
        tool_blocks: list[dict[str, Any]],
        proc: asyncio.subprocess.Process,
        ev_type: str,
    ) -> None:
        """在 Ask 模式下逐一比对工具危险等级，阻断危险操作并向前端请求授权。"""
        for tb in tool_blocks:
            t_name = str(tb.get("name") or tb.get("tool_name") or "Tool")
            t_id = str(tb.get("id") or f"tc_{int(time.time() * 1000)}")
            t_input = tb.get("input") or tb.get("arguments") or tb.get("args") or {}
            summary, diff_txt = extract_tool_summary_and_diff(t_name, t_input)
            risk = classify_tool_risk(t_name, summary)
            is_readonly = any(k in t_name.lower() for k in ("read", "glob", "grep", "list", "search", "view"))

            if not is_readonly:
                approval = PendingApproval(
                    approval_id=f"apr_{uuid.uuid4().hex[:10]}",
                    unified_id=ctx.unified_id,
                    agent_type=ctx.agent_type,
                    raw_session_id=ctx.raw_session_id,
                    tool_call_id=t_id,
                    tool_name=t_name,
                    args_summary=summary,
                    diff_text=diff_txt,
                    risk_level=risk,
                    prompt_context=ctx.prompt,
                    project_path=ctx.workdir,
                    created_at=utc_now_iso(),
                )
                approval_res = await self.request_mobile_tool_approval(approval, proc=proc)
                if approval_res.get("decision") == "denied" or ctx.unified_id not in self.active_processes:
                    break
            elif proc and proc.stdin and not proc.stdin.is_closing() and ev_type in ("permission_request", "control_request"):
                try:
                    allow_frame = json.dumps({"type": "control_response", "tool_use_id": t_id, "behavior": "allow"})
                    proc.stdin.write((allow_frame + "\n").encode("utf-8"))
                    await proc.stdin.drain()
                except Exception as exc:
                    logger.debug(f"向子进程写入放行响应异常: {exc}")
