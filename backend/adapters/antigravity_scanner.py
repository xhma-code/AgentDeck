"""
Antigravity 会话列表扫描、模型配额合并与消息文件读取引擎。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.adapters.antigravity_ls_bridge import LS_MODEL_ID_MAP
from backend.adapters.antigravity_parser import (
    inspect_transcript_tail_status,
    is_temporary_probe_session,
    parse_agy_step_dict,
    parse_state_selected_model,
)
from backend.adapters.base import (
    BoundedLRUCache,
    UnifiedMessageItem,
    UnifiedSession,
    decode_workspace_uri,
    evaluate_project_dir,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error, logger


def query_session_model(
    summary_db_path: Path,
    root_dir: Path,
    raw_session_id: str,
    agent_type: str = "agy_ide",
) -> Optional[str]:
    """从 conversations/<id>.db 读取该会话最近使用的真实模型 ID。"""
    conv_dir = root_dir / "conversations"
    db_path = conv_dir / f"{raw_session_id}.db"
    if not db_path.exists():
        if agent_type == "agy_cli":
            ide_db = Path(os.path.expanduser(f"~/.gemini/antigravity/conversations/{raw_session_id}.db"))
            if ide_db.exists():
                db_path = ide_db
            else:
                return None
        else:
            return None

    try:
        from backend.adapters.antigravity_parser import extract_protobuf_model_id

        sql = "SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 1"
        rows = safe_query_external_sqlite(db_path, sql, ())
        if rows and rows[0].get("data"):
            data = rows[0]["data"]
            if isinstance(data, bytes):
                return extract_protobuf_model_id(data)
    except Exception as exc:
        logger.debug(f"读取会话 {raw_session_id[:8]} 模型异常: {exc}")

    return None


def query_antigravity_sessions(
    summary_db_path: Path,
    brain_dir: Path,
    agent_type: str,
    agent_label: str,
    limit: int = 50,
) -> list[UnifiedSession]:
    """合并 conversation_summaries.db 与 brain 目录日志，构建会话摘要。"""
    sessions_map: dict[str, UnifiedSession] = {}
    now_iso = utc_now_iso()

    # 1. 扫描 conversation_summaries.db
    known_parent_map: dict[str, str] = {}
    if summary_db_path.exists():
        cols_info = safe_query_external_sqlite(summary_db_path, "PRAGMA table_info(conversation_summaries)", ())
        existing_cols = {str(c.get("name") or "") for c in cols_info}
        has_parent_col = "parent_conversation_id" in existing_cols
        has_depth_col = "nesting_depth" in existing_cols

        extra_cols = []
        if has_parent_col:
            extra_cols.append("parent_conversation_id")
        if has_depth_col:
            extra_cols.append("nesting_depth")
        extra_select = (", " + ", ".join(extra_cols)) if extra_cols else ""

        sql = f"""
            SELECT conversation_id, title, preview, step_count,
                   last_modified_time, workspace_uris, status, not_fully_idle
                   {extra_select}
            FROM conversation_summaries
            WHERE killed = 0
            ORDER BY last_modified_time DESC
            LIMIT ?
        """
        rows = safe_query_external_sqlite(summary_db_path, sql, (limit,))
        for r in rows:
            cid = str(r.get("conversation_id") or "")
            if not cid:
                continue
            not_idle = bool(r.get("not_fully_idle"))
            raw_status = str(r.get("status") or "")
            if agent_type == "agy_ide" and not_idle:
                status = "desktop_active"
            elif not_idle or "RUNNING" in raw_status.upper():
                status = "running"
            else:
                status = "idle"

            title = (r.get("title") or "").strip()
            preview = (r.get("preview") or "").strip()
            raw_paths = r.get("workspace_uris") or r.get("workspace_paths") or ""
            p_path = decode_workspace_uri(str(raw_paths))
            raw_up = r.get("last_modified_time") or r.get("updated_at")
            if isinstance(raw_up, (int, float)):
                val = float(raw_up)
                if val > 1e11:
                    val /= 1000.0
                up_iso = datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
            else:
                up_iso = str(raw_up or now_iso).replace(" ", "T")

            if is_temporary_probe_session(title=title, preview=preview, project_path=p_path):
                continue

            parent_cid = str(r.get("parent_conversation_id") or "").strip()
            nesting_depth = int(r.get("nesting_depth") or 0)
            is_sub = bool(parent_cid) or nesting_depth > 0
            parent_uid = f"{agent_type}:{parent_cid}" if parent_cid else None
            if parent_cid:
                known_parent_map[cid] = parent_cid

            is_proj, norm_p, p_name = evaluate_project_dir(p_path)
            sessions_map[cid] = UnifiedSession(
                unified_id=f"{agent_type}:{cid}",
                agent_type=agent_type,
                agent_label=agent_label,
                raw_session_id=cid,
                title=title or f"会话 {cid[:8]}",
                preview=preview or title or "暂无内容",
                project_path=p_path,
                status=status,
                step_count=int(r.get("step_count") or 1),
                updated_at=up_iso,
                is_project=is_proj,
                project_name=p_name,
                desktop_locked=(agent_type == "agy_ide" and not_idle),
                parent_unified_id=parent_uid,
                is_subagent=is_sub,
            )

        # 预查所有已记录的子进程归属，以供 brain 目录补漏判断
        if has_parent_col:
            try:
                where_clause = "WHERE (parent_conversation_id IS NOT NULL AND parent_conversation_id != '')"
                if has_depth_col:
                    where_clause += " OR nesting_depth > 0"
                sub_rows = safe_query_external_sqlite(
                    summary_db_path,
                    f"SELECT conversation_id, parent_conversation_id FROM conversation_summaries {where_clause}",
                    (),
                )
                for sr in sub_rows:
                    s_cid = str(sr.get("conversation_id") or "")
                    p_cid = str(sr.get("parent_conversation_id") or "")
                    if s_cid:
                        known_parent_map[s_cid] = p_cid
            except Exception as e:
                logger.debug(f"读取 Antigravity 子会话映射关系忽略容错: {e}")

    # 2. 补漏 brain 目录
    if brain_dir.is_dir():
        try:
            for b_item in brain_dir.iterdir():
                if not b_item.is_dir() or b_item.name in sessions_map:
                    continue
                tp = b_item / ".system_generated" / "logs" / "transcript.jsonl"
                if not tp.is_file():
                    continue
                try:
                    st = tp.stat()
                    up_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                    is_active, s_status = inspect_transcript_tail_status(tp)
                    status = s_status if is_active else "idle"
                except OSError:
                    up_iso = now_iso
                    status = "idle"

                cid = b_item.name
                p_cid = known_parent_map.get(cid)
                is_sub = cid in known_parent_map
                parent_uid = f"{agent_type}:{p_cid}" if p_cid else None
                sessions_map[cid] = UnifiedSession(
                    unified_id=f"{agent_type}:{cid}",
                    agent_type=agent_type,
                    agent_label=agent_label,
                    raw_session_id=cid,
                    title=f"会话 {cid[:8]}",
                    preview="来自本地日志",
                    project_path="",
                    status=status,
                    step_count=1,
                    updated_at=up_iso,
                    parent_unified_id=parent_uid,
                    is_subagent=is_sub,
                )
        except OSError:
            pass

    sorted_list = sorted(sessions_map.values(), key=lambda s: s.updated_at, reverse=True)
    return sorted_list[:limit]


def resolve_agy_default_model(
    ls_bridge: Any,
    root_dir: Path,
    summary_db_path: Path,
    agent_type: str = "agy_ide",
) -> str:
    """多层级自适应推断默认模型 ID。"""
    state_file = root_dir / "antigravity_state.pbtxt"
    if not state_file.exists() and agent_type == "agy_cli":
        ide_state = Path(os.path.expanduser("~/.gemini/antigravity/antigravity_state.pbtxt"))
        if ide_state.exists():
            state_file = ide_state

    parsed_model = parse_state_selected_model(state_file)
    if parsed_model:
        return parsed_model

    conv_dir = root_dir / "conversations"
    if conv_dir.exists():
        try:
            db_files = sorted(conv_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            for dbf in db_files[:3]:
                m = query_session_model(summary_db_path, root_dir, dbf.stem)
                if m:
                    return m
        except Exception:
            pass

    ls_def = ls_bridge.get_default_model_id()
    if ls_def:
        return ls_def

    from backend.engines.process_pool import AGENT_MODELS
    base_models = AGENT_MODELS.get(agent_type, [])
    rec_model = next((m["id"] for m in base_models if m.get("is_recommended")), None)
    return rec_model or (base_models[0]["id"] if base_models else "gemini-3.8-flash-medium")


def merge_agy_models_catalog(
    ls_bridge: Any,
    default_mid: str,
    agent_type: str = "agy_ide",
) -> list[dict[str, Any]]:
    """获取带实时配额的可用模型列表。"""
    from backend.engines.process_pool import AGENT_MODELS

    configs = ls_bridge.get_cascade_model_configs()
    q_map: dict[str, dict[str, Any]] = {}
    for c in configs:
        if c.get("modelId"):
            q_map[c["modelId"].lower()] = c
        if c.get("label"):
            q_map[c["label"].lower()] = c

    base_models = AGENT_MODELS.get(agent_type, [])
    merged: list[dict[str, Any]] = []
    for bm in base_models:
        item = dict(bm)
        matched = q_map.get(bm["id"].lower()) or q_map.get(bm["label"].lower())
        if matched:
            item["quota"] = matched.get("quota", 1.0)
            item["reset_time"] = matched.get("reset_time")
            if matched.get("tag") and not item.get("tag"):
                item["tag"] = matched["tag"]
            if matched.get("is_recommended"):
                item["is_recommended"] = True
        else:
            item["quota"] = 1.0
        if item["id"] == default_mid:
            item["is_default"] = True
        merged.append(item)

    sort_labels = getattr(ls_bridge, "_cached_sort_labels", [])
    if sort_labels:
        label_index = {lbl.lower(): i for i, lbl in enumerate(sort_labels)}
        merged.sort(key=lambda m: (0 if m.get("is_default") else 1, label_index.get(m["label"].lower(), 999)))
    return merged


from backend.adapters.antigravity_fork import fork_ide_session_to_cli


def read_antigravity_messages(
    transcript_path: Path,
    raw_session_id: str,
    agent_label: str,
    messages_cache: BoundedLRUCache,
    limit: int = 120,
) -> list[UnifiedMessageItem]:
    """增量/全量解析 transcript.jsonl 并映射追加结构化卡片流。"""
    try:
        st = transcript_path.stat()
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
            last_assistant_box: list[Optional[UnifiedMessageItem]] = [
                items[-1] if (items and items[-1].role == "assistant") else None
            ]
            with open(transcript_path, "rb") as f:
                f.seek(cached[3])
                inc_data = f.read()

            inc_lines = inc_data.splitlines(keepends=True)
            start_idx = len(items)
            consumed_bytes = cached[3]
            for idx_offset, b_line in enumerate(inc_lines):
                u_line = b_line.decode("utf-8", errors="replace").strip()
                if not u_line:
                    consumed_bytes += len(b_line)
                    continue

                try:
                    step_obj = json.loads(u_line)
                except json.JSONDecodeError:
                    if idx_offset == len(inc_lines) - 1:
                        break
                    consumed_bytes += len(b_line)
                    continue

                consumed_bytes += len(b_line)
                parse_agy_step_dict(
                    step=step_obj,
                    raw_session_id=raw_session_id,
                    fallback_idx=start_idx + idx_offset,
                    items=items,
                    last_assistant_container=last_assistant_box,
                )

            messages_cache[raw_session_id] = (mtime_ns, fsize, items, consumed_bytes)
            return items[-limit:] if limit > 0 else items
        except Exception as inc_exc:
            logger.debug(f"增量读取 Antigravity 异常，回退全量: {inc_exc}")

    # 全量读取
    items = []
    last_assistant_box = [None]
    try:
        with open(transcript_path, "rb") as f:
            all_raw = f.read()
        raw_b_lines = all_raw.splitlines(keepends=True)
        if raw_b_lines and not raw_b_lines[-1].endswith((b"\n", b"\r")):
            raw_b_lines = raw_b_lines[:-1]
    except Exception as exc:
        log_structured_error(
            problem=f"读取 {agent_label} 会话日志失败 ({raw_session_id[:8]})",
            cause=str(exc),
            fix="系统将在下次文件刷新时自动重试。",
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
            step_obj = json.loads(u_line)
        except json.JSONDecodeError:
            if idx == len(raw_b_lines) - 1:
                break
            consumed_bytes += len(b_line)
            continue

        consumed_bytes += len(b_line)
        parse_agy_step_dict(
            step=step_obj,
            raw_session_id=raw_session_id,
            fallback_idx=idx,
            items=items,
            last_assistant_container=last_assistant_box,
        )

    messages_cache[raw_session_id] = (mtime_ns, fsize, items, consumed_bytes)
    return items[-limit:] if limit > 0 else items


def query_antigravity_single_session(
    summary_db_path: Path,
    brain_dir: Path,
    agent_type: str,
    agent_label: str,
    raw_session_id: str,
) -> Optional[UnifiedSession]:
    """按会话 ID 单独查询单个 Antigravity 会话元数据。"""
    cid = str(raw_session_id).strip()
    if not cid:
        return None

    now_iso = utc_now_iso()
    if summary_db_path.exists():
        cols_info = safe_query_external_sqlite(summary_db_path, "PRAGMA table_info(conversation_summaries)", ())
        existing_cols = {str(c.get("name") or "") for c in cols_info}
        has_parent_col = "parent_conversation_id" in existing_cols
        has_depth_col = "nesting_depth" in existing_cols

        extra_cols = []
        if has_parent_col:
            extra_cols.append("parent_conversation_id")
        if has_depth_col:
            extra_cols.append("nesting_depth")
        extra_select = (", " + ", ".join(extra_cols)) if extra_cols else ""

        sql = f"""
            SELECT conversation_id, title, preview, step_count,
                   last_modified_time, workspace_uris, status, not_fully_idle
                   {extra_select}
            FROM conversation_summaries
            WHERE conversation_id = ?
            LIMIT 1
        """
        rows = safe_query_external_sqlite(summary_db_path, sql, (cid,))
        if rows:
            r = rows[0]
            not_idle = bool(r.get("not_fully_idle"))
            raw_status = str(r.get("status") or "")
            if agent_type == "agy_ide" and not_idle:
                status = "desktop_active"
            elif not_idle or "RUNNING" in raw_status.upper():
                status = "running"
            else:
                status = "idle"

            title = (r.get("title") or "").strip()
            preview = (r.get("preview") or "").strip()
            raw_paths = r.get("workspace_uris") or r.get("workspace_paths") or ""
            p_path = decode_workspace_uri(str(raw_paths))
            raw_up = r.get("last_modified_time") or r.get("updated_at")
            if isinstance(raw_up, (int, float)):
                val = float(raw_up)
                if val > 1e11:
                    val /= 1000.0
                up_iso = datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
            else:
                up_iso = str(raw_up or now_iso).replace(" ", "T")

            parent_cid = str(r.get("parent_conversation_id") or "").strip() if has_parent_col else ""
            nesting_depth = int(r.get("nesting_depth") or 0) if has_depth_col else 0
            is_sub = bool(parent_cid) or nesting_depth > 0
            parent_uid = f"{agent_type}:{parent_cid}" if parent_cid else None

            is_proj, norm_p, p_name = evaluate_project_dir(p_path)
            return UnifiedSession(
                unified_id=f"{agent_type}:{cid}",
                agent_type=agent_type,
                agent_label=agent_label,
                raw_session_id=cid,
                title=title or f"会话 {cid[:8]}",
                preview=preview or title or "暂无内容",
                project_path=p_path,
                status=status,
                step_count=int(r.get("step_count") or 1),
                updated_at=up_iso,
                is_project=is_proj,
                project_name=p_name,
                desktop_locked=(agent_type == "agy_ide" and not_idle),
                parent_unified_id=parent_uid,
                is_subagent=is_sub,
            )

    # 查 brain 目录
    tp = brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
    if tp.is_file():
        try:
            st = tp.stat()
            up_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
            is_active, s_status = inspect_transcript_tail_status(tp)
            status = s_status if is_active else "idle"
        except OSError:
            up_iso = now_iso
            status = "idle"

        return UnifiedSession(
            unified_id=f"{agent_type}:{cid}",
            agent_type=agent_type,
            agent_label=agent_label,
            raw_session_id=cid,
            title=f"会话 {cid[:8]}",
            preview="来自本地日志",
            project_path="",
            status=status,
            step_count=1,
            updated_at=up_iso,
        )

    return None
