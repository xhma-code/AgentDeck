"""
Claude Code 会话 JSONL 日志解析、文本清洗与 Token 计算引擎。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedToolCall,
    classify_tool_risk,
    count_diff_lines,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    resolve_model_context_limit,
    truncate_tool_output,
)
from backend.core.logger import log_structured_error, logger
from backend.adapters.tool_utils import build_error_alert_markdown, is_quota_or_rate_limit_error

COMMAND_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.DOTALL)
COMMAND_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.DOTALL)
TASK_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)
TASK_ID_RE = re.compile(r"<task-id>\s*(.*?)\s*</task-id>", re.DOTALL)


def clean_claude_user_text(raw: str) -> tuple[str, str]:
    """
    清洗 Claude Code 的 user 文本块，返回 `(消息类型, 清洗后文本)`。
    - `'ignore'`: 系统级 `<local-command-caveat>`, `[Request interrupted...]`，直接丢弃
    - `'task_notification'`: 后台任务通知 XML，统一折叠为工具执行过程而不是用户发言
    - `'slash_expanded'`: 斜杠命令自动展开的千行系统 Prompt 模板
    - `'user'`: 真实用户发言或斜杠指令
    """
    text = (raw or "").strip()
    if not text:
        return "ignore", ""

    if text.startswith("[Request interrupted"):
        return "ignore", ""

    if "<local-command-caveat>" in text or "<local-command-stdout>" in text:
        cleaned = re.sub(r"<local-command-caveat>.*?</local-command-caveat>", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"<local-command-stdout>.*?</local-command-stdout>", "", cleaned, flags=re.DOTALL).strip()
        if not cleaned:
            return "ignore", ""
        text = cleaned

    if "<task-notification>" in text:
        summary_m = TASK_SUMMARY_RE.search(text)
        tid_m = TASK_ID_RE.search(text)
        summary = summary_m.group(1).strip() if summary_m else "后台任务"
        tid = tid_m.group(1).strip() if tid_m else "task"
        return "task_notification", f"[{tid}] {summary}"

    if "<command-name>" in text or "<command-message>" in text:
        cmd_m = COMMAND_NAME_RE.search(text)
        args_m = COMMAND_ARGS_RE.search(text)
        cmd_name = cmd_m.group(1).strip() if cmd_m else "/command"
        if not cmd_name.startswith("/"):
            cmd_name = f"/{cmd_name}"
        cmd_args = args_m.group(1).strip() if args_m else ""
        if cmd_name in ("/clear", "/compact", "/exit", "/login", "/logout"):
            return "ignore", ""
        return "user", f"{cmd_name} {cmd_args}".strip()

    # 识别 /init 列表自动注入的系统英文 Prompt 模板
    if text.startswith("Please analyze this codebase and create a CLAUDE.md file"):
        return "slash_expanded", text

    # 剔除内嵌 <system-reminder>
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL).strip()
    if not text:
        return "ignore", ""

    return "user", text


def extract_record_plain_text(rec: dict[str, Any]) -> str:
    """从 Claude JSONL 记录中提取纯文本摘要（自动过滤系统标签与后台任务通知）。"""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return ""
    rec_type = rec.get("type")
    content = msg.get("content")
    raw_text = ""
    if isinstance(content, str):
        raw_text = content.strip()
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = str(block.get("text") or "").strip()
                if t and not t.startswith("<system-reminder>"):
                    parts.append(t)
        raw_text = " ".join(parts).strip()

    if rec_type == "user" and raw_text:
        kind, cleaned = clean_claude_user_text(raw_text)
        if kind in ("ignore", "task_notification", "slash_expanded"):
            return ""
        return cleaned
    return raw_text


def parse_claude_record_dict(
    rec: dict[str, Any],
    raw_session_id: str,
    items: list[UnifiedMessageItem],
    tool_id_map: dict[str, UnifiedToolCall],
    step_idx_container: list[int],
) -> bool:
    """根据已解析的 Claude Code 记录字典映射并更新 items 和 tool_id_map。"""
    if not isinstance(rec, dict):
        return False

    rec_type = rec.get("type")
    created_at = str(rec.get("timestamp") or "")

    # 捕获 Claude Code 顶层错误记录
    if rec_type == "error" or rec.get("error"):
        err_data = rec.get("error") or rec.get("message") or {}
        if isinstance(err_data, dict):
            err_msg = str(err_data.get("message") or err_data.get("error") or "")
        else:
            err_msg = str(err_data)
        is_quota = is_quota_or_rate_limit_error(err_msg)
        alert_md = build_error_alert_markdown("Claude Code", err_msg, is_quota=is_quota)
        step_idx_container[0] += 1
        cur_step = step_idx_container[0]
        items.append(
            UnifiedMessageItem(
                item_id=f"{raw_session_id}:step:{cur_step}",
                step_index=cur_step,
                role="assistant",
                created_at=created_at,
                content=alert_md,
                status="ERROR",
            )
        )
        return True

    if rec_type not in ("user", "assistant"):
        return False

    msg = rec.get("message")
    if not isinstance(msg, dict):
        return False

    created_at = str(rec.get("timestamp") or "")
    content = msg.get("content")

    def _append_cleaned_user_text(raw_txt: str, created_at_str: str) -> None:
        kind, cleaned = clean_claude_user_text(raw_txt)
        if kind == "ignore" or not cleaned:
            return
        if kind == "slash_expanded":
            if items and items[-1].role == "user" and items[-1].content.startswith("/"):
                return
        if kind == "task_notification":
            step_idx_container[0] += 1
            cur_step = step_idx_container[0]
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{cur_step}",
                    step_index=cur_step,
                    role="assistant",
                    created_at=created_at_str,
                    content="",
                    tool_calls=[
                        UnifiedToolCall(
                            tool_id=f"task_notif_{cur_step}",
                            name="TaskNotification",
                            args_summary=cleaned,
                            status="DONE",
                        )
                    ],
                )
            )
            return

        step_idx_container[0] += 1
        cur_step = step_idx_container[0]
        items.append(
            UnifiedMessageItem(
                item_id=f"{raw_session_id}:step:{cur_step}",
                step_index=cur_step,
                role="user",
                created_at=created_at_str,
                content=cleaned,
            )
        )

    if rec_type == "user":
        if isinstance(content, str):
            _append_cleaned_user_text(content, created_at)
        elif isinstance(content, list):
            user_texts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = str(block.get("text") or "").strip()
                    if txt and not txt.startswith("<system-reminder>"):
                        user_texts.append(txt)
                elif btype == "tool_result":
                    tid = str(block.get("tool_use_id") or "")
                    raw_res = block.get("content")
                    res_str = (
                        raw_res
                        if isinstance(raw_res, str)
                        else json.dumps(raw_res, ensure_ascii=False)
                    )
                    if tid in tool_id_map:
                        target_tc = tool_id_map[tid]
                        trunc_out, is_trunc, full_len = truncate_tool_output(
                            target_tc.name, str(res_str)
                        )
                        target_tc.output = trunc_out
                        target_tc.is_truncated = is_trunc
                        target_tc.full_output_len = full_len
            if user_texts:
                _append_cleaned_user_text("\n".join(user_texts), created_at)
        return True

    elif rec_type == "assistant":
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[UnifiedToolCall] = []
        parsed_questions: list[dict[str, Any]] = []

        if isinstance(content, str):
            text_parts.append(content.strip())
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    t = str(block.get("text") or "").strip()
                    if t:
                        text_parts.append(t)
                elif btype == "thinking":
                    th = str(block.get("thinking") or "").strip()
                    if th:
                        thinking_parts.append(th)
                elif btype == "tool_use":
                    tid = str(block.get("id") or f"tc_{step_idx_container[0]}")
                    tname = str(block.get("name") or "Tool")
                    tinput = block.get("input") or {}
                    q_items = extract_interactive_questions(tname, tinput)
                    if q_items:
                        parsed_questions.extend(q_items)
                        continue
                    summary, diff_txt = extract_tool_summary_and_diff(tname, tinput)
                    add_c, del_c = count_diff_lines(diff_txt)
                    f_path = extract_tool_file_path(tinput)
                    risk = classify_tool_risk(tname, summary)
                    tc_obj = UnifiedToolCall(
                        tool_id=tid,
                        name=tname,
                        args_summary=summary,
                        diff_text=diff_txt,
                        diff_add_count=add_c,
                        diff_del_count=del_c,
                        risk_level=risk,
                        file_path=f_path,
                    )
                    tool_calls.append(tc_obj)
                    tool_id_map[tid] = tc_obj

        if text_parts or thinking_parts or tool_calls or parsed_questions:
            content_str = "\n\n".join(text_parts)
            is_quota = is_quota_or_rate_limit_error(content_str)
            if is_quota and not content_str.startswith("> [!"):
                content_str = build_error_alert_markdown("Claude Code", content_str, is_quota=True)
            step_idx_container[0] += 1
            cur_step = step_idx_container[0]
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{cur_step}",
                    step_index=cur_step,
                    role="assistant",
                    created_at=created_at,
                    content=content_str,
                    thinking="\n\n".join(thinking_parts),
                    tool_calls=tool_calls,
                    questions=parsed_questions,
                )
            )
            return True
    return False


def parse_claude_record_line(
    line: str,
    raw_session_id: str,
    items: list[UnifiedMessageItem],
    tool_id_map: dict[str, UnifiedToolCall],
    step_idx_container: list[int],
) -> bool:
    """解析单行 Claude Code JSONL 记录并更新 items 和 tool_id_map。"""
    line = line.strip()
    if not line:
        return False
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return False
    return parse_claude_record_dict(
        rec=rec,
        raw_session_id=raw_session_id,
        items=items,
        tool_id_map=tool_id_map,
        step_idx_container=step_idx_container,
    )


def extract_claude_step_full_output(fpath: Path, raw_session_id: str, step_index: int) -> str:
    """按需读取 Claude Code 会话中指定步骤或工具的完整未截断输出。"""
    if not fpath or not fpath.exists():
        return ""
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        raw_outputs: list[str] = []
        cur_step = 0
        target_tool_ids: set[str] = set()
        for ln in lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            rec_type = rec.get("type")
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if rec_type == "user":
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            tid = str(b.get("tool_use_id") or "")
                            if tid in target_tool_ids:
                                raw_res = b.get("content")
                                raw_outputs.append(
                                    raw_res
                                    if isinstance(raw_res, str)
                                    else json.dumps(raw_res, ensure_ascii=False, indent=2)
                                )
                elif isinstance(content, str):
                    kind, cleaned = clean_claude_user_text(content)
                    if kind != "ignore" and cleaned:
                        cur_step += 1
                        if cur_step == step_index or step_index == 0:
                            raw_outputs.append(cleaned)
            elif rec_type == "assistant":
                cur_step += 1
                if (cur_step == step_index or step_index == 0) and isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            target_tool_ids.add(str(b.get("id") or ""))
                    if not target_tool_ids and cur_step == step_index:
                        return json.dumps(rec, ensure_ascii=False, indent=2)
        return "\n\n---\n\n".join(raw_outputs) if raw_outputs else (lines[0] if lines else "")
    except Exception as exc:
        log_structured_error(
            problem=f"读取 Claude Code 单步完整输出失败 ({raw_session_id[:8]}#{step_index})",
            cause=str(exc),
            fix="请确认 ~/.claude/projects 下的会话文件可读。",
            exc=exc,
        )
        return ""


def compute_claude_token_usage(fpath: Path, limit: int) -> dict[str, Any]:
    """解析 Claude Code 会话 JSONL 文件提取 token 统计。"""
    latest_input = latest_output = latest_cached = 0
    total_input = total_output = total_cache_read = 0
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            try:
                fsize = fpath.stat().st_size
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
                    usage = record.get("usage") or (record.get("message") or {}).get("usage")
                    if isinstance(usage, dict):
                        in_tok = usage.get("input_tokens") or 0
                        out_tok = usage.get("output_tokens") or 0
                        cr_tok = usage.get("cache_read_input_tokens") or 0
                        cc_tok = usage.get("cache_creation_input_tokens") or 0
                        if in_tok or out_tok or cr_tok:
                            latest_input, latest_output = in_tok, out_tok
                            latest_cached = cr_tok + cc_tok
                            total_input += in_tok + cc_tok
                            total_output += out_tok
                            total_cache_read += cr_tok
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.debug(f"读取 Claude 会话 JSONL 跳过: {exc}")

    latest_context = latest_input + latest_cached
    pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
    return {
        "context_tokens": latest_context,
        "context_limit": limit,
        "context_percent": pct,
        "total_tokens": total_input + total_output,
        "last_output_tokens": latest_output,
        "cache_read_tokens": total_cache_read,
    }
