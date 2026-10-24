# -*- coding: utf-8 -*-
"""
Antigravity 会话尾部活跃态探测与临时探针会话判定模块。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

RECENT_ACTIVE_WINDOW_SECONDS: float = 60.0
EXTENDED_ACTIVE_WINDOW_SECONDS: float = 300.0
SHORT_IDLE_GRACE_SECONDS: float = 15.0


def inspect_transcript_tail_status(tp: Path) -> tuple[bool, str]:
    """极速从 transcript.jsonl 末尾探测会话真实活跃态与状态机（耗时 < 1ms）。"""
    if not tp.is_file():
        return False, "idle"
    try:
        st = tp.stat()
        file_mtime = st.st_mtime
        age = time.time() - file_mtime
    except OSError:
        return False, "idle"

    if age > EXTENDED_ACTIVE_WINDOW_SECONDS:
        return False, "idle"

    tail_bytes = b""
    try:
        with open(tp, "rb") as f:
            if st.st_size > 8192:
                f.seek(-8192, os.SEEK_END)
            tail_bytes = f.read()
    except OSError:
        return age < 45.0, "running" if age < 45.0 else "idle"

    lines = [ln.strip() for ln in tail_bytes.split(b"\n") if ln.strip()]
    if not lines:
        return age < RECENT_ACTIVE_WINDOW_SECONDS, "running" if age < RECENT_ACTIVE_WINDOW_SECONDS else "idle"

    last_step: Optional[dict[str, Any]] = None
    has_incomplete_tail = False
    for raw_ln in reversed(lines):
        try:
            obj = json.loads(raw_ln.decode("utf-8", errors="replace"))
            if isinstance(obj, dict):
                last_step = obj
                break
        except Exception:
            has_incomplete_tail = True

    if has_incomplete_tail and age < RECENT_ACTIVE_WINDOW_SECONDS:
        return True, "running"

    if not last_step:
        return age < RECENT_ACTIVE_WINDOW_SECONDS, "running" if age < RECENT_ACTIVE_WINDOW_SECONDS else "idle"

    step_type = str(last_step.get("type") or "")
    source = str(last_step.get("source") or "")
    status = str(last_step.get("status") or "").upper()
    tool_calls = last_step.get("tool_calls") or []
    content = str(last_step.get("content") or "").strip()

    is_user_waiting = step_type == "USER_INPUT" or source == "USER_EXPLICIT"
    has_active_tools = any(
        isinstance(tc, dict) and str(tc.get("status") or "").upper() in ("RUNNING", "PENDING")
        for tc in tool_calls
    )
    is_running_step = status in ("RUNNING", "PENDING") or has_active_tools

    # 1. 用户刚发指令提问，等待模型思考/回复中：保持 running
    if is_user_waiting and age < EXTENDED_ACTIVE_WINDOW_SECONDS:
        return True, "running"

    # 2. 存在正在执行或挂起的工具调用 / 当前步骤状态明确为 RUNNING/PENDING：保持 running
    if is_running_step and age < EXTENDED_ACTIVE_WINDOW_SECONDS:
        return True, "running"

    # 3. 终态判定：最后一步是 PLANNER_RESPONSE，无任何工具调用，且给出了对用户的文本回复，且已完成
    # 这代表 Agent 本轮任务的所有规划与工具执行均已结束，已输出最终总结，立即 0 延迟转为 idle，消除虚假延迟
    is_terminal_response = (
        step_type == "PLANNER_RESPONSE"
        and not tool_calls
        and len(content) > 0
        and status == "DONE"
    )
    if is_terminal_response:
        return False, "idle"

    # 4. 中间过渡态：最后一步是工具输出（GENERIC）或发起工具调用的 PLANNER_RESPONSE
    # 此时 Agent 正在多步规划中（工具刚返回结果，模型紧接着要读取并生成下一步），在活跃窗口内稳定保持 running，严禁在中间间隙跳变为 idle
    is_tool_intermediate_step = (
        step_type == "GENERIC"
        or (step_type == "PLANNER_RESPONSE" and bool(tool_calls))
    )
    if is_tool_intermediate_step and age < SHORT_IDLE_GRACE_SECONDS:
        return True, "running"

    # 5. 兜底状态：如果已明确完成且超过短过渡期，平滑退火为 idle
    if status == "DONE":
        return False, "idle"

    return age < 15.0, "running" if age < 15.0 else "idle"


def is_temporary_probe_session(title: str, preview: str, project_path: str = "") -> bool:
    """判定是否为探针测试或临时无效会话。"""
    comb = f"{title} {preview} {project_path}".lower()
    probe_keywords = (
        "flag.txt", "marker.txt", "reading flag file", "read flag file",
        "cmem/flag", "pytest-of-", "test_meta.db",
    )
    if any(k in comb for k in probe_keywords):
        return True

    t_clean = (title or "").strip()
    p_clean = (preview or "").strip()
    is_empty_or_id = (not t_clean) or t_clean.startswith("会话 ") or t_clean == "未命名会话"
    if is_empty_or_id:
        if (not p_clean) or p_clean.startswith("会话 ") or p_clean in ("暂无内容", "来自本地日志"):
            return True
        if p_clean.startswith("[已拒绝工具:"):
            return True
    return False
