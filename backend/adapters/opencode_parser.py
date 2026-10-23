"""
OpenCode SQLite 会话数据与部件行解析器。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.adapters.models import UnifiedMessageItem, UnifiedSession, UnifiedToolCall
from backend.adapters.tool_utils import (
    build_unified_diff,
    classify_tool_risk,
    count_diff_lines,
    is_transient_probe_session,
    truncate_large_output,
    truncate_tool_output,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.workspace import decode_workspace_uri, evaluate_project_dir


def ms_to_iso(ts_ms: Any) -> str:
    """将 OpenCode SQLite 中的毫秒级时间戳转换为 ISO 8601 字符串。"""
    try:
        val = float(ts_ms)
        if val > 1e11:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return utc_now_iso()


def build_opencode_unified_tool_call(
    tool_id: str,
    tool_name: str,
    tool_input: Any,
    raw_output: str,
    call_status: str = "DONE",
) -> UnifiedToolCall:
    """标准化构建 OpenCode 工具调用结构卡片。"""
    if isinstance(tool_input, dict):
        args_text = (
            tool_input.get("command")
            or tool_input.get("cmd")
            or tool_input.get("path")
            or tool_input.get("file_path")
            or tool_input.get("query")
            or json.dumps(tool_input, ensure_ascii=False)
        )
    else:
        args_text = str(tool_input or "")

    diff_text = ""
    f_path = ""
    if isinstance(tool_input, dict):
        f_path = str(tool_input.get("path") or tool_input.get("file_path") or "")
        old_c = str(tool_input.get("old_string") or tool_input.get("oldString") or "")
        new_c = str(tool_input.get("new_string") or tool_input.get("newString") or tool_input.get("content") or "")
        if f_path and (old_c or new_c):
            diff_text = build_unified_diff(f_path, old_c, new_c)

    add_c, del_c = count_diff_lines(diff_text)
    risk = classify_tool_risk(tool_name, args_text)
    truncated_out, is_trunc, full_len = truncate_tool_output(tool_name, raw_output)

    return UnifiedToolCall(
        tool_id=tool_id,
        name=tool_name,
        args_summary=args_text[:200],
        diff_text=diff_text,
        diff_add_count=add_c,
        diff_del_count=del_c,
        output=truncated_out,
        is_truncated=is_trunc,
        full_output_len=full_len,
        risk_level=risk,
        status=call_status,
        file_path=f_path,
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

            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:msg:{mid}",
                    step_index=idx,
                    role="assistant",
                    created_at=created_at,
                    content="\n\n".join(text_chunks).strip(),
                    thinking="\n\n".join(thinking_chunks).strip(),
                    tool_calls=tool_calls,
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
                    thinking="\n\n".join(thinking_chunks).strip(),
                    tool_calls=tool_calls,
                )
            )

    return items


def extract_opencode_token_usage(
    db_path: Path,
    raw_session_id: str,
    limit: int,
    has_session_v2: bool,
) -> dict[str, Any]:
    """从 session_v2 或 message 表解析提取 Token 水位与总用量。"""
    if has_session_v2:
        v2_rows = safe_query_external_sqlite(
            db_path,
            """
            SELECT tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, cost
            FROM session_v2 WHERE id = ?
            """,
            (raw_session_id,),
        )
        if v2_rows:
            r = v2_rows[0]
            in_tok = int(r.get("tokens_input") or 0)
            out_tok = int(r.get("tokens_output") or 0)
            cr_tok = int(r.get("tokens_cache_read") or 0)
            if in_tok > 0 or out_tok > 0 or cr_tok > 0:
                latest_context = in_tok + cr_tok
                pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
                return {
                    "context_tokens": latest_context,
                    "context_limit": limit,
                    "context_percent": pct,
                    "total_tokens": in_tok + out_tok,
                    "last_output_tokens": out_tok,
                    "cache_read_tokens": cr_tok,
                }

    rows = safe_query_external_sqlite(
        db_path,
        "SELECT data FROM message WHERE session_id = ? ORDER BY time_created ASC",
        (raw_session_id,),
    )
    latest_context = 0
    latest_output = 0
    total_tokens = 0
    cache_read = 0

    for r in rows:
        raw_data = r.get("data")
        if not raw_data:
            continue
        try:
            d = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            tok = d.get("tokens")
            if isinstance(tok, dict):
                in_tok = tok.get("input") or 0
                out_tok = tok.get("output") or 0
                tot = tok.get("total") or (in_tok + out_tok)
                cr = (tok.get("cache") or {}).get("read") or 0
                if tot > 0 or in_tok > 0:
                    latest_context = in_tok + cr if (in_tok + cr) > 0 else tot
                    latest_output = out_tok
                    total_tokens += tot
                    cache_read += cr
        except (json.JSONDecodeError, TypeError, KeyError):
            continue

    pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
    return {
        "context_tokens": latest_context,
        "context_limit": limit,
        "context_percent": pct,
        "total_tokens": total_tokens,
        "last_output_tokens": latest_output,
        "cache_read_tokens": cache_read,
    }


def query_opencode_sessions(
    db_path: Path,
    has_v2: bool,
    active_map: dict[str, str],
    limit: int,
    agent_type: str = "opencode",
    agent_label: str = "OpenCode",
) -> list[UnifiedSession]:
    """从 opencode.db 查询并组装统一会话列表（自适应 2.x 与 1.x）。"""
    sessions: list[UnifiedSession] = []
    if has_v2:
        sql = """
        SELECT s.*,
               (SELECT COUNT(1) FROM session_message m WHERE m.session_id = s.id) AS msg_count
        FROM session_v2 s
        WHERE s.time_archived IS NULL
        ORDER BY s.time_updated DESC
        LIMIT ?
        """
        rows = safe_query_external_sqlite(db_path, sql, (limit,))
        for r in rows:
            raw_id = str(r["id"])
            raw_title = (r.get("title") or "").strip()
            if not raw_title or raw_title.startswith("New session"):
                first_user_msg = safe_query_external_sqlite(
                    db_path,
                    "SELECT data FROM session_message WHERE session_id = ? AND type = 'user' ORDER BY seq ASC LIMIT 1",
                    (raw_id,),
                )
                if first_user_msg:
                    try:
                        m_obj = json.loads(str(first_user_msg[0].get("data") or "{}"))
                        txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", str(m_obj.get("text") or ""), flags=re.DOTALL)
                        txt = re.sub(r"\s+", " ", txt).strip()
                        if txt:
                            raw_title = txt[:48]
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
            title = raw_title or f"会话 {raw_id[:8]}"
            raw_dir = str(r.get("directory") or "")
            directory = decode_workspace_uri(raw_dir)
            updated_at = ms_to_iso(r.get("time_updated") or r.get("time_created"))

            status = "idle"
            if raw_id in active_map:
                act_type = active_map[raw_id]
                status = "waiting_approval" if "prompt" in act_type else "running"

            is_proj, norm_p, p_name = evaluate_project_dir(directory)
            sessions.append(
                UnifiedSession(
                    unified_id=f"{agent_type}:{raw_id}",
                    agent_type=agent_type,
                    agent_label=agent_label,
                    raw_session_id=raw_id,
                    title=title,
                    preview=title,
                    project_path=directory,
                    status=status,
                    step_count=int(r.get("msg_count") or 1),
                    updated_at=updated_at,
                    is_project=is_proj,
                    project_name=p_name,
                )
            )
        return sessions

    # 回退 OpenCode 1 session 表
    sql = """
    SELECT s.*,
           (SELECT COUNT(1) FROM message m WHERE m.session_id = s.id) AS msg_count
    FROM session s
    WHERE s.time_archived IS NULL
    ORDER BY s.time_updated DESC
    LIMIT ?
    """
    rows = safe_query_external_sqlite(db_path, sql, (limit,))
    for r in rows:
        raw_id = str(r["id"])
        title = (r.get("title") or "").strip()
        raw_dir = str(r.get("directory") or "")
        directory = decode_workspace_uri(raw_dir)
        updated_at = ms_to_iso(r.get("time_updated") or r.get("time_created"))
        msg_count = int(r.get("msg_count") or 0)

        if not title:
            first_part = safe_query_external_sqlite(
                db_path,
                "SELECT data FROM part WHERE session_id = ? ORDER BY time_created ASC LIMIT 1",
                (raw_id,),
            )
            if first_part:
                try:
                    p_data = json.loads(str(first_part[0].get("data") or "{}"))
                    title = str(p_data.get("text") or "").strip()[:80]
                except json.JSONDecodeError:
                    pass
            if not title:
                title = f"会话 {raw_id[:8]}"

        if is_transient_probe_session(title=title):
            continue

        status = "idle"
        if raw_id in active_map:
            act_type = active_map[raw_id]
            status = "waiting_approval" if "prompt" in act_type else "running"

        sessions.append(
            UnifiedSession(
                unified_id=f"{agent_type}:{raw_id}",
                agent_type=agent_type,
                agent_label=agent_label,
                raw_session_id=raw_id,
                title=title,
                preview=title,
                project_path=directory,
                status=status,
                step_count=msg_count,
                updated_at=updated_at,
            )
        )
    return sessions


