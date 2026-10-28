# -*- coding: utf-8 -*-
"""
OpenCode 消息解析与工具调用提取器。
负责解析 OpenCode 1.x (message + part) 与 2.x (session_message) 单步详情与工具调用数据。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.adapters.models import (
    UnifiedMessageItem,
    UnifiedToolCall,
)
from backend.adapters.tool_utils import (
    classify_tool_risk,
    count_diff_lines,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    truncate_large_output,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import logger


def ms_to_iso(ts_ms: Any) -> str:
    """将 OpenCode SQLite 中的毫秒级时间戳转换为 ISO 8601 字符串。"""
    try:
        val = float(ts_ms)
        if val > 1e11:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return utc_now_iso()


def format_opencode_error_alert(err_data: Any, outcome: str = "failed") -> str:
    """提取并格式化 OpenCode 异常/额度耗尽错误为标准 GitHub 风格警示 Markdown。"""
    err_dict = err_data if isinstance(err_data, dict) else {}
    err_type = str(err_dict.get("type") or "")
    err_msg = str(err_dict.get("message") or "")
    status_code = err_dict.get("status")

    # 识别免费额度耗尽与速率限制 (429 / provider.free-limit / FreeUsageLimitError 等)
    is_quota_exceeded = (
        "free-limit" in err_type.lower()
        or "freeusagelimit" in str(err_dict).lower()
        or status_code == 429
        or "limit reached" in err_msg.lower()
        or "quota" in err_msg.lower()
    )

    if is_quota_exceeded:
        body = err_msg or "Free usage limit reached. 当前模型的免费使用额度已达上限，会话已被中止。"
        return (
            "> [!WARNING]\n"
            "> **OpenCode 模型使用额度已耗尽 (Free usage limit reached / 429)**\n>\n"
            f"> {body}\n>\n"
            "> 💡 **解决建议**：当前为 OpenCode 自身免费配额耗尽（与流程中提及的 Codex/Claude 无关）。请在 OpenCode 中切换其他可用模型，或配置自定义 Provider / API Key 继续使用。"
        )

    # 识别上游流异常中断（如 without finish_reason，常由上下文溢出或供应商断流引起）
    if "without finish_reason" in err_msg.lower() or "invalid-output" in err_type.lower():
        body = err_msg or "OpenAI Chat stream ended without finish_reason"
        return (
            "> [!WARNING]\n"
            "> **OpenCode 会话数据流异常中断 (Stream Interrupted)**\n>\n"
            f"> {body}\n>\n"
            "> 💡 **根因与建议**：上游模型服务未完成正常结束标识即断开（通常为上下文超限、额度/并发瞬时受限或网络断流）。建议重试或更换模型继续会话。"
        )

    # 其他执行异常（超时、中断、认证失败等）
    detail = err_msg or (f"执行状态: {outcome}" if outcome else "未知异常中断")
    return (
        "> [!CAUTION]\n"
        "> **OpenCode 会话异常中断**\n>\n"
        f"> {detail}"
    )


def build_opencode_unified_tool_call(
    tool_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    raw_output: str,
    call_status: str = "DONE",
) -> UnifiedToolCall:
    """统一提取并构造 OpenCode 的标准化工具调用对象，复用于 1.x 与 2.x 双协议。"""
    summary, diff_txt = extract_tool_summary_and_diff(tool_name, tool_input)
    add_c, del_c = count_diff_lines(diff_txt)
    f_path = extract_tool_file_path(tool_input)
    trunc_out, is_trunc, full_len = truncate_large_output(str(raw_output))
    risk = classify_tool_risk(tool_name, summary)
    return UnifiedToolCall(
        tool_id=tool_id,
        name=tool_name,
        args_summary=summary,
        diff_text=diff_txt,
        diff_add_count=add_c,
        diff_del_count=del_c,
        output=trunc_out,
        is_truncated=is_trunc,
        full_output_len=full_len,
        risk_level=risk,
        file_path=f_path,
        status=call_status,
    )


def parse_session_message_rows(
    raw_session_id: str,
    sm_rows: list[dict[str, Any]],
    step_index_map: dict[str, int],
) -> list[UnifiedMessageItem]:
    """解析 OpenCode 2.x 的 session_message 单表行数据（包含 reasoning、tool 与 text）。"""
    items: list[UnifiedMessageItem] = []
    for r in sm_rows:
        mid = str(r.get("id") or "")
        idx = step_index_map.get(mid, 1)
        mtype = str(r.get("type") or "")
        created_at = ms_to_iso(r.get("time_created"))

        try:
            data = json.loads(str(r.get("data") or "{}"))
        except json.JSONDecodeError:
            data = {}

        if mtype == "user":
            raw_text = str(data.get("text") or "").strip()
            cleaned_user = re.sub(r"<system-reminder>.*?</system-reminder>", "", raw_text, flags=re.DOTALL).strip()
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:msg:{mid}",
                    step_index=idx,
                    role="user",
                    created_at=created_at,
                    content=cleaned_user or raw_text,
                )
            )
        elif mtype == "assistant":
            contents = data.get("content") or []
            text_chunks: list[str] = []
            thinking_chunks: list[str] = []
            tool_calls: list[UnifiedToolCall] = []
            parsed_questions: list[dict[str, Any]] = []

            for block in contents:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    t = str(block.get("text") or "").strip()
                    if t:
                        text_chunks.append(t)
                elif btype in ("reasoning", "thinking"):
                    th = str(block.get("text") or block.get("reasoning") or "").strip()
                    if th:
                        thinking_chunks.append(th)
                elif btype in ("tool", "tool_use"):
                    tname = str(block.get("name") or block.get("tool") or "tool")
                    tool_id = str(block.get("id") or f"oc_{mid}_{idx}")
                    state = block.get("state") if isinstance(block.get("state"), dict) else {}
                    tinput = state.get("input") or block.get("input") or {}
                    state_content = state.get("content") or []

                    out_pieces: list[str] = []
                    if isinstance(state_content, list):
                        for sc in state_content:
                            if isinstance(sc, dict) and sc.get("text"):
                                out_pieces.append(str(sc["text"]))
                    elif isinstance(state_content, str):
                        out_pieces.append(state_content)
                    if state.get("error"):
                        out_pieces.append(str(state["error"]))

                    raw_output = "\n".join(out_pieces)
                    st_val = str(state.get("status") or "")
                    call_status = "DONE" if st_val == "completed" else ("FAILED" if st_val == "error" else "RUNNING")
                    tool_calls.append(
                        build_opencode_unified_tool_call(
                            tool_id=tool_id,
                            tool_name=tname,
                            tool_input=tinput,
                            raw_output=raw_output,
                            call_status=call_status,
                        )
                    )
                    q_items = extract_interactive_questions(tname, tinput)
                    if q_items:
                        parsed_questions.extend(q_items)

            content_str = "\n\n".join(text_chunks).strip()
            thinking_str = "\n\n".join(thinking_chunks).strip()
            if not content_str and not thinking_str and not tool_calls and data.get("error"):
                content_str = format_opencode_error_alert(data.get("error"))

            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:msg:{mid}",
                    step_index=idx,
                    role="assistant",
                    created_at=created_at,
                    content=content_str,
                    thinking=thinking_str,
                    tool_calls=tool_calls,
                    questions=parsed_questions,
                )
            )
        elif mtype == "idle":
            outcome = str(data.get("outcome") or "")
            err_data = data.get("error")
            if outcome == "failed" or err_data:
                alert_text = format_opencode_error_alert(err_data, outcome)
                if items and items[-1].role == "assistant":
                    if not items[-1].content and not items[-1].tool_calls:
                        items[-1].content = alert_text
                        continue
                    if "模型使用额度已耗尽" in items[-1].content or "OpenCode 会话异常中断" in items[-1].content:
                        # 前序步骤已包含同源中断警告，避免重复追加冗余卡片
                        continue
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:msg:{mid}",
                        step_index=idx,
                        role="assistant",
                        created_at=created_at,
                        content=alert_text,
                    )
                )

    return items


def parse_messages_from_rows(
    db_path: Path,
    raw_session_id: str,
    msg_rows: list[dict[str, Any]],
    step_index_map: dict[str, int],
) -> list[UnifiedMessageItem]:
    """解析 OpenCode 1.x 的 message + part 双表行数据。"""
    if not msg_rows:
        return []
    msg_ids = [str(r.get("id") or "") for r in msg_rows if r.get("id")]
    if not msg_ids:
        return []
    placeholders = ",".join(["?"] * len(msg_ids))
    part_rows = safe_query_external_sqlite(
        db_path,
        f"""
        SELECT id, message_id, time_created, data
        FROM part
        WHERE session_id = ? AND message_id IN ({placeholders})
        ORDER BY time_created ASC
        """,
        (raw_session_id, *msg_ids),
    )

    parts_by_msg: dict[str, list[dict[str, Any]]] = {}
    for pr in part_rows:
        mid = str(pr.get("message_id") or "")
        try:
            pdata = json.loads(str(pr.get("data") or "{}"))
        except json.JSONDecodeError:
            continue
        pdata["_part_id"] = pr.get("id")
        parts_by_msg.setdefault(mid, []).append(pdata)

    items: list[UnifiedMessageItem] = []
    for mr in msg_rows:
        mid = str(mr.get("id") or "")
        idx = step_index_map.get(mid, 1)
        try:
            mdata = json.loads(str(mr.get("data") or "{}"))
        except json.JSONDecodeError:
            mdata = {}

        role = str(mdata.get("role") or "assistant")
        created_at = ms_to_iso(mr.get("time_created"))
        msg_parts = parts_by_msg.get(mid, [])

        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls: list[UnifiedToolCall] = []
        parsed_questions: list[dict[str, Any]] = []

        for p in msg_parts:
            ptype = str(p.get("type") or "")
            if ptype == "text":
                t = str(p.get("text") or "").strip()
                if t:
                    text_chunks.append(t)
            elif ptype in ("reasoning", "thinking"):
                th = str(p.get("text") or p.get("reasoning") or "").strip()
                if th:
                    thinking_chunks.append(th)
            elif ptype in ("tool", "tool-invocation", "tool_use"):
                tname = str(p.get("tool") or p.get("name") or "tool")
                state = p.get("state") if isinstance(p.get("state"), dict) else p
                tinput = state.get("input") or p.get("input") or {}
                toutput = state.get("output") or p.get("output") or ""
                tool_calls.append(
                    build_opencode_unified_tool_call(
                        tool_id=str(p.get("callID") or p.get("_part_id") or f"oc_tc_{idx}"),
                        tool_name=tname,
                        tool_input=tinput,
                        raw_output=str(toutput),
                    )
                )
                q_items = extract_interactive_questions(tname, tinput)
                if q_items:
                    parsed_questions.extend(q_items)

        if text_chunks or thinking_chunks or tool_calls:
            joined_content = "\n\n".join(text_chunks).strip()
            actual_role = "user" if role == "user" else "assistant"

            if actual_role == "user" and "<system-reminder>" in joined_content:
                cleaned_user = re.sub(
                    r"<system-reminder>.*?</system-reminder>", "", joined_content, flags=re.DOTALL
                ).strip()
                if not cleaned_user and joined_content.startswith("<system-reminder>"):
                    items.append(
                        UnifiedMessageItem(
                            item_id=f"{raw_session_id}:msg:{mid}",
                            step_index=idx,
                            role="assistant",
                            created_at=created_at,
                            content="",
                            tool_calls=[
                                UnifiedToolCall(
                                    tool_id=f"oc_sys_{idx}",
                                    name="TaskNotification",
                                    args_summary="后台子任务已完成",
                                    output=truncate_large_output(joined_content)[0],
                                    status="DONE",
                                )
                            ],
                        )
                    )
                    continue
                joined_content = cleaned_user

            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:msg:{mid}",
                    step_index=idx,
                    role=actual_role,
                    created_at=created_at,
                    content=joined_content,
                    thinking="\n\n".join(thinking_chunks),
                    tool_calls=tool_calls,
                    questions=parsed_questions,
                )
            )

    return items


def extract_opencode_full_step_output(
    db_path: Path,
    raw_session_id: str,
    step_index: int,
    all_meta: list[dict[str, Any]],
    is_v2: bool,
) -> str:
    """按需读取 OpenCode 指定消息步骤的完整未截断工具输出。"""
    if step_index < 1 or step_index > len(all_meta):
        return ""

    target_row = all_meta[step_index - 1]
    target_mid = str(target_row.get("id") or "")

    if is_v2:
        try:
            data = json.loads(str(target_row.get("data") or "{}"))
            contents = data.get("content") or []
            full_chunks: list[str] = []
            for block in contents:
                if isinstance(block, dict) and block.get("type") in ("tool", "tool_use"):
                    state = block.get("state") or {}
                    state_content = state.get("content") or []
                    if isinstance(state_content, list):
                        for sc in state_content:
                            if isinstance(sc, dict) and sc.get("text"):
                                full_chunks.append(str(sc["text"]))
                    elif isinstance(state_content, str):
                        full_chunks.append(state_content)
                    if state.get("error"):
                        full_chunks.append(str(state["error"]))
            return "\n\n".join(full_chunks)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as parse_err:
            logger.error("解析 OpenCode 2 消息工具内容失败: 消息ID=%s, 异常=%s", target_mid, parse_err)
            return ""

    part_rows = safe_query_external_sqlite(
        db_path,
        "SELECT data FROM part WHERE session_id = ? AND message_id = ? ORDER BY time_created ASC",
        (raw_session_id, target_mid),
    )
    full_chunks = []
    for pr in part_rows:
        raw_data = str(pr.get("data") or "")
        try:
            p = json.loads(raw_data)
            state = p.get("state") if isinstance(p.get("state"), dict) else p
            out = state.get("output") or p.get("output") or p.get("text") or ""
            if out:
                full_chunks.append(str(out))
        except json.JSONDecodeError:
            full_chunks.append(raw_data)
    return "\n\n".join(full_chunks)
