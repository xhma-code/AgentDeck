"""
Claude Code 本地 projects 目录扫描、进程状态探查与会话定位。
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from backend.adapters.base import (
    BoundedLRUCache,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    decode_workspace_uri,
    evaluate_project_dir,
    is_transient_probe_session,
)
from backend.adapters.claude_parser import extract_record_plain_text, parse_claude_record_dict
from backend.core.database import utc_now_iso
from backend.core.logger import log_structured_error, logger
from backend.adapters.tool_utils import is_quota_or_rate_limit_error

_logged_cc_error_sids: set[str] = set()


def detect_active_claude_sessions(sessions_dir: Path) -> dict[str, str]:
    """获取当前由外部运行的 Claude Code 进程活跃状态字典（读取 sessions/*.json）。"""
    active_map: dict[str, str] = {}
    if not sessions_dir.exists():
        return active_map

    try:
        for p in sessions_dir.glob("*.json"):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
                doc = json.loads(raw)
                pid = doc.get("pid")
                sid = str(doc.get("sessionId") or "")
                if not sid:
                    continue
                if psutil and pid and not psutil.pid_exists(pid):
                    continue
                raw_st = str(doc.get("status") or "").lower()
                if raw_st in ("busy", "running"):
                    active_map[sid] = "running"
                elif raw_st == "idle":
                    active_map[sid] = "desktop_active"
            except Exception:
                continue
    except Exception as exc:
        logger.debug(f"扫描 Claude 活跃状态会话异常: {exc}")
    return active_map


def find_claude_session_file(
    projects_dir: Path,
    session_file_map: BoundedLRUCache,
    raw_session_id: str,
) -> Optional[Path]:
    """快速定位指定 session_id 对应的 `.jsonl` 文件。"""
    cached_path = session_file_map.get(raw_session_id)
    if cached_path and cached_path.exists():
        return cached_path
    if not projects_dir.exists():
        return None
    for p in projects_dir.rglob(f"{raw_session_id}.jsonl"):
        session_file_map[raw_session_id] = p
        return p
    return None


def parse_claude_session_file(
    fpath: Path,
    mtime: float,
    active_map: dict[str, str],
    agent_type: str = "claude_code",
    agent_label: str = "Claude Code",
) -> Optional[UnifiedSession]:
    """解析单个 Claude Code .jsonl 会话文件。"""
    sid = fpath.stem
    title = ""
    preview = ""
    cwd = ""
    step_count = 0
    last_ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    last_is_error = False

    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type")
                if not cwd and rec.get("cwd"):
                    cwd = str(rec.get("cwd"))
                if rec.get("timestamp"):
                    last_ts = str(rec.get("timestamp"))

                if rec_type == "error" or rec.get("error"):
                    last_is_error = True
                elif rec_type in ("user", "assistant"):
                    last_is_error = False
                    step_count += 1
                    text = re.sub(r"\s+", " ", extract_record_plain_text(rec)).strip()
                    if text:
                        if rec_type == "user" and not title:
                            title = text[:60]
                        preview = text[:160]
                        if rec_type == "assistant" and is_quota_or_rate_limit_error(text):
                            last_is_error = True
    except Exception as exc:
        log_structured_error(
            problem=f"解析 Claude Code 会话摘要异常 ({fpath.name})",
            cause=str(exc),
            fix="已跳过该损坏文件，不影响其他会话加载。",
        )
        return None

    meta_file = fpath.parent / f"{sid}.meta.json"
    if meta_file.exists():
        try:
            m_doc = json.loads(meta_file.read_text(encoding="utf-8"))
            if m_doc.get("title"):
                title = str(m_doc["title"]).strip()
        except Exception:
            pass

    if is_transient_probe_session(title=title, preview=preview):
        return None

    st_str = active_map.get(sid, "")
    if not st_str:
        if last_is_error:
            st_str = "error"
            if sid not in _logged_cc_error_sids:
                _logged_cc_error_sids.add(sid)
                logger.warning(f"Claude Code 会话检测到异常中断/额度耗尽: sid={sid[:8]}")
        else:
            st_str = "idle"
    p_path = decode_workspace_uri(cwd)
    is_proj, norm_p, p_name = evaluate_project_dir(p_path)

    return UnifiedSession(
        unified_id=f"{agent_type}:{sid}",
        agent_type=agent_type,
        agent_label=agent_label,
        raw_session_id=sid,
        title=title or f"会话 {sid[:8]}",
        preview=preview or title or "暂无内容",
        project_path=p_path,
        status=st_str,
        step_count=max(1, step_count),
        updated_at=last_ts,
        is_project=is_proj,
        project_name=p_name,
    )


def read_claude_session_messages(
    fpath: Path,
    raw_session_id: str,
    messages_cache: BoundedLRUCache,
    limit: int = 120,
) -> list[UnifiedMessageItem]:
    """增量/全量解析 Claude Code 的 JSONL 文件映射为卡片流。"""
    try:
        st = fpath.stat()
        mtime_ns = st.st_mtime_ns
        fsize = st.st_size
    except OSError:
        return []

    cached = messages_cache.get(raw_session_id)
    if cached and cached[0] == mtime_ns and cached[1] == fsize:
        items: list[UnifiedMessageItem] = cached[2]
        return items[-limit:] if limit > 0 else items

    if cached and cached[1] <= fsize:
        try:
            items = list(cached[2])
            tool_id_map = dict(cached[4])
            step_idx_box = [cached[5]]
            with open(fpath, "rb") as f:
                f.seek(cached[3])
                inc_data = f.read()

            inc_lines = inc_data.splitlines(keepends=True)
            consumed_bytes = cached[3]
            for idx, b_line in enumerate(inc_lines):
                u_line = b_line.decode("utf-8", errors="replace").strip()
                if not u_line:
                    consumed_bytes += len(b_line)
                    continue
                try:
                    rec_obj = json.loads(u_line)
                except json.JSONDecodeError:
                    if idx == len(inc_lines) - 1:
                        break
                    consumed_bytes += len(b_line)
                    continue

                consumed_bytes += len(b_line)
                parse_claude_record_dict(
                    rec=rec_obj,
                    raw_session_id=raw_session_id,
                    items=items,
                    tool_id_map=tool_id_map,
                    step_idx_container=step_idx_box,
                )

            messages_cache[raw_session_id] = (
                mtime_ns,
                fsize,
                items,
                consumed_bytes,
                tool_id_map,
                step_idx_box[0],
            )
            return items[-limit:] if limit > 0 else items
        except Exception as inc_exc:
            logger.debug(f"增量读取 Claude 异常，回退全量: {inc_exc}")

    # 全量读取
    items = []
    tool_id_map: dict[str, UnifiedToolCall] = {}
    step_idx_box = [0]
    try:
        with open(fpath, "rb") as f:
            all_raw = f.read()
        raw_b_lines = all_raw.splitlines(keepends=True)
        if raw_b_lines and not raw_b_lines[-1].endswith((b"\n", b"\r")):
            raw_b_lines = raw_b_lines[:-1]
    except Exception as exc:
        log_structured_error(
            problem=f"读取 Claude Code 会话日志失败 ({raw_session_id[:8]})",
            cause=str(exc),
            fix="请确认会话文件是否被独占或具有读取权限。",
            exc=exc,
        )
        return []

    consumed_bytes = 0
    for idx, b_line in enumerate(raw_b_lines):
        u_line = b_line.decode("utf-8", errors="replace").strip()
        if not u_line:
            consumed_bytes += len(b_line)
            continue
        try:
            rec_obj = json.loads(u_line)
        except json.JSONDecodeError:
            if idx == len(raw_b_lines) - 1:
                break
            consumed_bytes += len(b_line)
            continue

        consumed_bytes += len(b_line)
        parse_claude_record_dict(
            rec=rec_obj,
            raw_session_id=raw_session_id,
            items=items,
            tool_id_map=tool_id_map,
            step_idx_container=step_idx_box,
        )

    messages_cache[raw_session_id] = (
        mtime_ns,
        fsize,
        items,
        consumed_bytes,
        tool_id_map,
        step_idx_box[0],
    )
    return items[-limit:] if limit > 0 else items
