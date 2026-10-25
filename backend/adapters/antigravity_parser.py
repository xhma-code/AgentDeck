"""
Antigravity transcript.jsonl 会话日志解析、步骤映射、模型提取与克隆接力。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from backend.adapters.antigravity_ls_bridge import LS_MODEL_ID_MAP
from backend.adapters.antigravity_model_resolver import (
    extract_protobuf_model_id,
    parse_state_selected_model,
)
from backend.adapters.antigravity_tail_inspector import (
    EXTENDED_ACTIVE_WINDOW_SECONDS,
    RECENT_ACTIVE_WINDOW_SECONDS,
    SHORT_IDLE_GRACE_SECONDS,
    inspect_transcript_tail_status,
    is_temporary_probe_session,
)
from backend.adapters.models import (
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedToolCall,
)
from backend.adapters.tool_utils import (
    build_error_alert_markdown,
    is_quota_or_rate_limit_error,
    classify_tool_risk,
    count_diff_lines,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    is_read_only_tool,
    truncate_tool_output,
    extract_duration_from_output,
)
from backend.core.database import utc_now_iso
from backend.core.logger import log_structured_error, logger

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>", re.IGNORECASE)


def clean_agy_user_content(raw_text: str) -> str:
    """提取 Antigravity 用户输入，剥离系统提示词包装。"""
    if not raw_text:
        return ""
    m = USER_REQUEST_RE.search(raw_text)
    if m:
        extracted = m.group(1).strip()
        if extracted:
            return extracted

    cleaned = USER_REQUEST_RE.sub("", raw_text)
    cleaned = re.sub(r"<context>[\s\S]*?</context>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<guidelines>[\s\S]*?</guidelines>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or raw_text.strip()


def clean_denied_tool_instruction(text: str) -> str:
    """清洗内部拼装标记的工具拒绝文本，转化为对话指导语。"""
    if not text:
        return ""
    m = re.match(
        r"^\[已拒绝(?:工具)?\s+[^\]]+\]\s*(?:请勿执行刚才的操作，按以下要求调整方案[：:]|请执行刚才的操作，要求[：:])?\s*(.*)$",
        text.strip(),
        re.DOTALL,
    )
    if m:
        feedback = m.group(1).strip()
        return f"指导: {feedback}" if feedback else "已拒绝执行该工具"
    return text


AGY_TOOL_OUTPUT_TYPES = {
    "GENERIC",
    "RUN_COMMAND",
    "VIEW_FILE",
    "CODE_ACTION",
    "LIST_DIRECTORY",
    "SEARCH_WEB",
    "GREP_SEARCH",
    "MCP_TOOL",
    "BROWSER_ACTION",
    "READ_FILE",
    "EDIT_FILE",
    "WRITE_FILE",
}


def parse_agy_step_dict(
    step: dict[str, Any],
    raw_session_id: str,
    fallback_idx: int,
    items: list[UnifiedMessageItem],
    last_assistant_container: list[Optional[UnifiedMessageItem]],
) -> bool:
    """将 step 字典映射并追加到结构化消息列表中。"""
    if not isinstance(step, dict):
        return False

    step_index = int(step.get("step_index", fallback_idx))
    step_type = str(step.get("type") or "")
    source = str(step.get("source") or "")
    created_at = str(step.get("created_at") or utc_now_iso())
    status = str(step.get("status") or "DONE")
    content_raw = str(step.get("content") or "")
    thinking_raw = str(step.get("thinking") or "").strip()
    raw_tool_calls = step.get("tool_calls") or []

    if step_type == "USER_INPUT" or source == "USER_EXPLICIT":
        clean_text = clean_agy_user_content(content_raw)
        items.append(
            UnifiedMessageItem(
                item_id=f"{raw_session_id}:step:{step_index}",
                step_index=step_index,
                role="user",
                created_at=created_at,
                content=clean_text,
                status=status,
            )
        )
        return True
    elif step_type == "ASK_QUESTION":
        ans_lines = [
            ln.strip()
            for ln in content_raw.splitlines()
            if ln.strip() and not ln.startswith(("Created At:", "Completed At:"))
        ]
        ans_text = "\n".join(ans_lines).strip()
        if ans_text:
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{step_index}",
                    step_index=step_index,
                    role="user",
                    created_at=created_at,
                    content=ans_text,
                    status=status,
                )
            )
        return True
    elif step_type in AGY_TOOL_OUTPUT_TYPES or "TOOL" in step_type:
        last_assistant_item = last_assistant_container[0]
        if content_raw and last_assistant_item and last_assistant_item.tool_calls:
            placed = False
            for tc in last_assistant_item.tool_calls:
                if not tc.output:
                    trunc_out, is_trunc, full_len = truncate_tool_output(tc.name, content_raw)
                    tc.output = trunc_out
                    tc.is_truncated = is_trunc
                    tc.full_output_len = full_len
                    dur = extract_duration_from_output(content_raw)
                    if dur:
                        tc.duration_str = dur
                    placed = True
                    break
            if not placed:
                last_tc = last_assistant_item.tool_calls[-1]
                if is_read_only_tool(last_tc.name):
                    trunc_out, is_trunc, full_len = truncate_tool_output(last_tc.name, content_raw)
                    last_tc.output = trunc_out
                    last_tc.full_output_len += full_len
                else:
                    combined = f"{last_tc.output}\n---\n{content_raw}" if last_tc.output else content_raw
                    c_out, c_trunc, c_len = truncate_tool_output(last_tc.name, combined)
                    last_tc.output = c_out
                    last_tc.is_truncated = c_trunc
                    last_tc.full_output_len = last_tc.full_output_len + c_len
        return True
    elif step_type == "PLANNER_RESPONSE" or (
        source == "MODEL" and (content_raw or thinking_raw or raw_tool_calls)
    ):
        parsed_tools: list[UnifiedToolCall] = []
        parsed_questions: list[dict[str, Any]] = []
        for tc_idx, tc in enumerate(raw_tool_calls):
            if not isinstance(tc, dict):
                continue
            t_name = str(tc.get("name") or tc.get("tool_name") or "tool")
            t_args = tc.get("arguments") or tc.get("args") or tc.get("input") or {}
            if t_name == "ask_question" and isinstance(t_args, dict):
                q_items = extract_interactive_questions(t_name, t_args)
                parsed_questions.extend(q_items)
                first_q = q_items[0]["question"] if q_items else "交互式提问"
                parsed_tools.append(
                    UnifiedToolCall(
                        tool_id=str(tc.get("id") or f"tc_{step_index}_{tc_idx}"),
                        name=t_name,
                        args_summary=first_q[:120],
                        status=status,
                    )
                )
                continue
            args_summary, diff_text = extract_tool_summary_and_diff(t_name, t_args)
            add_c, del_c = count_diff_lines(diff_text)
            f_path = extract_tool_file_path(t_args)
            risk = classify_tool_risk(t_name, args_summary)
            parsed_tools.append(
                UnifiedToolCall(
                    tool_id=str(tc.get("id") or f"tc_{step_index}_{tc_idx}"),
                    name=t_name,
                    args_summary=args_summary,
                    diff_text=diff_text,
                    diff_add_count=add_c,
                    diff_del_count=del_c,
                    risk_level=risk,
                    status=status,
                    file_path=f_path,
                )
            )

        final_content = clean_denied_tool_instruction(content_raw.strip())
        is_quota = is_quota_or_rate_limit_error(final_content)
        if is_quota or status == "ERROR":
            if not final_content.startswith("> [!"):
                final_content = build_error_alert_markdown("Antigravity", final_content, is_quota=is_quota)
        msg_item = UnifiedMessageItem(
            item_id=f"{raw_session_id}:step:{step_index}",
            step_index=step_index,
            role="assistant",
            created_at=created_at,
            content=final_content,
            thinking=thinking_raw,
            tool_calls=parsed_tools,
            questions=parsed_questions,
            status=status,
        )
        items.append(msg_item)
        if parsed_tools:
            last_assistant_container[0] = msg_item
        return True
    return False


def parse_agy_step_line(
    line: str,
    raw_session_id: str,
    fallback_idx: int,
    items: list[UnifiedMessageItem],
    last_assistant_container: list[Optional[UnifiedMessageItem]],
) -> bool:
    """单行 JSON 解析辅助函数。"""
    line = line.strip()
    if not line:
        return False
    try:
        step = json.loads(line)
    except json.JSONDecodeError:
        return False
    parse_agy_step_dict(
        step=step,
        raw_session_id=raw_session_id,
        fallback_idx=fallback_idx,
        items=items,
        last_assistant_container=last_assistant_container,
    )
    return True


def extract_agy_step_full_output(brain_dir: Path, raw_session_id: str, step_index: int) -> str:
    """按需从底层 transcript.jsonl 读取指定单步完整原始输出。"""
    transcript_path = brain_dir / raw_session_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not transcript_path.exists():
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                except json.JSONDecodeError:
                    continue

                curr_idx = step.get("step_index", idx)
                if curr_idx == step_index:
                    for tc in step.get("tool_calls", []):
                        out = tc.get("output") or tc.get("result")
                        if out:
                            return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2)
                    c = step.get("content")
                    if c:
                        return str(c)
                    th = step.get("thinking")
                    if th:
                        return str(th)
                    return json.dumps(step, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_structured_error(
            problem=f"读取 Antigravity 完整单步输出失败 ({raw_session_id[:8]}#{step_index})",
            cause=str(exc),
            fix="请确认日志文件未被底层独占且路径存在。",
            exc=exc,
        )
    return ""


def compute_agy_token_usage(transcript_path: Path, limit: int) -> dict[str, Any]:
    """从 transcript.jsonl 扫描提取会话 Token 统计水位。"""
    latest_input = 0
    latest_output = 0
    latest_cache_read = 0
    total_input = 0
    total_output = 0
    total_cache_read = 0

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            try:
                fsize = transcript_path.stat().st_size
                if fsize > 512 * 1024:
                    f.seek(fsize - 256 * 1024)
                    f.readline()
            except OSError:
                pass

            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    in_tokens = record.get("input_tokens") or 0
                    out_tokens = record.get("output_tokens") or 0
                    cr_tokens = record.get("cache_read_tokens") or record.get("cache_read_input_tokens") or 0
                    if not (in_tokens or out_tokens or cr_tokens):
                        for k in ("token_usage", "usage", "tokens", "metadata"):
                            sub = record.get(k)
                            if isinstance(sub, dict):
                                in_tokens = sub.get("input_tokens") or sub.get("prompt_tokens") or 0
                                out_tokens = sub.get("output_tokens") or sub.get("completion_tokens") or 0
                                cr_tokens = sub.get("cached_tokens") or sub.get("cache_read_input_tokens") or 0
                                break
                    if in_tokens or out_tokens or cr_tokens:
                        latest_input = in_tokens
                        latest_output = out_tokens
                        latest_cache_read = cr_tokens
                        total_input += in_tokens
                        total_output += out_tokens
                        total_cache_read += cr_tokens
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.debug(f"读取 Antigravity 会话 transcript 异常: {exc}")

    latest_context = latest_input + latest_cache_read
    pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
    return {
        "context_tokens": latest_context,
        "context_limit": limit,
        "context_percent": pct,
        "total_tokens": total_input + total_output,
        "last_output_tokens": latest_output,
        "cache_read_tokens": total_cache_read,
    }


# 保持历史导出兼容
from backend.adapters.antigravity_fork import fork_ide_session_to_cli
