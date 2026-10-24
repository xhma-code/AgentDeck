# -*- coding: utf-8 -*-
"""
AgentDeck 进程管理工具与单轮执行上下文定义。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from backend.core.logger import log_structured_error, logger


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


async def drain_subprocess_stderr(proc: asyncio.subprocess.Process, log_tag: str) -> None:
    """异步排空并记录子进程 stderr，避免管道缓冲区塞满导致进程挂起。"""
    if not proc.stderr:
        return
    try:
        while True:
            err_line = await proc.stderr.readline()
            if not err_line:
                break
            txt = err_line.decode("utf-8", errors="replace").strip()
            if txt:
                logger.debug(f"[{log_tag} stderr] {txt}")
    except Exception as exc:
        logger.debug(f"[{log_tag} stderr] 读取异常: {exc}")


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

