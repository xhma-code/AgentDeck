# -*- coding: utf-8 -*-
"""
OpenCode 会话与消息元数据查询扫描器。
负责从本地 SQLite 数据库中检索会话列表、统计指标与分页消息元数据。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from backend.adapters.models import (
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    resolve_model_context_limit,
)
from backend.adapters.opencode_parser import (
    ms_to_iso,
    parse_messages_from_rows,
    parse_session_message_rows,
)
from backend.core.database import safe_query_external_sqlite
from backend.core.logger import log_structured_error, logger


def query_all_messages_meta(
    db_path: Path,
    raw_session_id: str,
    check_has_table: Callable[[str], bool],
) -> tuple[bool, list[dict[str, Any]]]:
    """优先拉取 session_message 表，不存在则回退至 message 表。返回 (is_v2, rows)。"""
    if check_has_table("session_message"):
        try:
            sm_rows = safe_query_external_sqlite(
                db_path,
                """
                SELECT id, type, seq, time_created, time_updated, data
                FROM session_message
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (raw_session_id,),
            )
            if sm_rows:
                return True, sm_rows
        except (sqlite3.Error, OSError) as exc:
            logger.debug(f"尝试读取 OpenCode 2 session_message 失败，准备回退: {exc}")

    try:
        m_rows = safe_query_external_sqlite(
            db_path,
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created ASC",
            (raw_session_id,),
        )
        return False, m_rows
    except (sqlite3.Error, OSError) as exc:
        log_structured_error(
            problem=f"查询 OpenCode 消息失败: {raw_session_id[:8]}",
            cause=str(exc),
            fix="请检查 opencode.db 数据库文件权限或完整性。",
            exc=exc,
        )
        return False, []


def extract_opencode_token_usage(
    db_path: Path,
    raw_session_id: str,
    model_name: str,
    has_session_v2: bool,
) -> dict[str, Any]:
    """提取当前会话 token 统计（优先读取 OpenCode 2.x session_v2 高精度字段，回退消息聚合）。"""
    limit = resolve_model_context_limit(model_name)
    if not db_path.exists():
        return SessionTokenUsage(context_limit=limit).to_dict()

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
    limit: int,
    active_map: dict[str, str],
    agent_type: str,
    agent_label: str,
    has_v2: bool,
    has_sm: bool,
    check_has_table: Callable[[str], bool],
) -> list[UnifiedSession]:
    """从 opencode.db 读取会话列表（自适应 session_v2 与旧版 session，实时感知活跃状态）。"""
    if has_v2:
        count_sub = (
            "(SELECT COUNT(1) FROM session_message m WHERE m.session_id = s.id)"
            if has_sm
            else "0"
        )
        sql = f"""
            SELECT s.*,
                   {count_sub} AS msg_count
            FROM session_v2 s
            WHERE s.time_archived IS NULL
            ORDER BY s.time_updated DESC
            LIMIT ?
        """
    else:
        sql = """
            SELECT s.*,
                   (SELECT COUNT(1) FROM message m WHERE m.session_id = s.id) AS msg_count
            FROM session s
            WHERE s.time_archived IS NULL
            ORDER BY s.time_updated DESC
            LIMIT ?
        """

    rows = safe_query_external_sqlite(db_path, sql, (limit,))
    sessions: list[UnifiedSession] = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    for r in rows:
        sid = str(r.get("id") or "")
        if not sid:
            continue
        raw_title = re.sub(r"\s+", " ", str(r.get("title") or "").strip())
        if "subagent)" in raw_title.lower():
            continue

        if not raw_title or raw_title.startswith("New session"):
            extracted = ""
            if has_v2 and has_sm:
                first_user_msg = safe_query_external_sqlite(
                    db_path,
                    "SELECT data FROM session_message WHERE session_id = ? AND type = 'user' ORDER BY seq ASC LIMIT 1",
                    (sid,),
                )
                if first_user_msg:
                    try:
                        m_obj = json.loads(str(first_user_msg[0].get("data") or "{}"))
                        txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", str(m_obj.get("text") or ""), flags=re.DOTALL)
                        txt = re.sub(r"\s+", " ", txt).strip()
                        if txt:
                            extracted = txt[:48]
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
            elif not has_v2 and check_has_table("part"):
                first_part = safe_query_external_sqlite(
                    db_path,
                    "SELECT data FROM part WHERE session_id = ? ORDER BY time_created ASC LIMIT 3",
                    (sid,),
                )
                for fp in first_part:
                    try:
                        p_obj = json.loads(str(fp.get("data") or "{}"))
                        if p_obj.get("type") == "text" and p_obj.get("text"):
                            txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", str(p_obj["text"]), flags=re.DOTALL)
                            txt = re.sub(r"\s+", " ", txt).strip()
                            if txt:
                                extracted = txt[:48]
                                break
                    except (json.JSONDecodeError, TypeError, KeyError):
                        continue
            title = extracted or raw_title or f"OpenCode {sid[:8]}"
        else:
            title = raw_title

        directory = str(r.get("directory") or "")
        updated_at = ms_to_iso(r.get("time_updated"))
        msg_count = int(r.get("msg_count") or 0)

        if sid in active_map:
            status = "running"
        elif has_v2:
            t_updated = int(r.get("time_updated") or 0)
            t_idle = int(r.get("time_idle") or 0) if r.get("time_idle") is not None else 0
            if (t_idle == 0 or t_updated > t_idle) and (now_ms - t_updated < 45000):
                status = "running"
            else:
                status = "idle"
        else:
            status = "idle"

        parent_id = str(r.get("parent_id") or "").strip()
        is_sub = bool(parent_id) or ("subagent)" in raw_title.lower())
        parent_uid = f"{agent_type}:{parent_id}" if parent_id else None

        sessions.append(
            UnifiedSession(
                unified_id=f"{agent_type}:{sid}",
                agent_type=agent_type,
                agent_label=agent_label,
                raw_session_id=sid,
                title=title,
                preview=title,
                project_path=directory,
                status=status,
                step_count=msg_count,
                updated_at=updated_at,
                parent_unified_id=parent_uid,
                is_subagent=is_sub,
            )
        )

    return sessions


def fetch_opencode_messages_page(
    db_path: Path,
    raw_session_id: str,
    limit: int = 30,
    before_step: Optional[int] = None,
    since_step: Optional[int] = None,
    check_has_table: Optional[Callable[[str], bool]] = None,
) -> tuple[list[UnifiedMessageItem], int, bool]:
    """分页获取会话消息（自适应 OpenCode 2 与 1）。"""
    if not db_path.exists():
        return [], 0, False

    chk = check_has_table or (lambda _: True)
    is_v2, all_meta = query_all_messages_meta(db_path, raw_session_id, chk)
    if not all_meta:
        return [], 0, False

    total_count = len(all_meta)
    step_index_map = {str(r.get("id")): idx for idx, r in enumerate(all_meta, start=1)}

    if since_step is not None:
        filtered = [r for r in all_meta if step_index_map.get(str(r.get("id")), 0) >= since_step]
        if is_v2:
            page_items = parse_session_message_rows(raw_session_id, filtered, step_index_map)
        else:
            page_items = parse_messages_from_rows(db_path, raw_session_id, filtered, step_index_map)
        return page_items, total_count, False

    if before_step is not None:
        filtered = [r for r in all_meta if step_index_map.get(str(r.get("id")), 0) < before_step]
    else:
        filtered = all_meta

    page_rows = filtered[-limit:] if limit > 0 else filtered
    has_more = len(filtered) > len(page_rows)

    if is_v2:
        page_items = parse_session_message_rows(raw_session_id, page_rows, step_index_map)
    else:
        page_items = parse_messages_from_rows(db_path, raw_session_id, page_rows, step_index_map)
    return page_items, total_count, has_more

