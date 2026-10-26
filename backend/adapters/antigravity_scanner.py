"""
Antigravity 会话列表扫描、模型配额合并与消息文件读取引擎。
"""

from __future__ import annotations

import json
import os
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
    read_incremental_jsonl_stream,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error, logger

_logged_agy_error_sids: set[str] = set()


def _transcript_path(brain_dir: Path, cid: str) -> Path:
    """brain 目录下某会话的 transcript.jsonl 标准路径。"""
    return brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"


def _subagent_row_is_active(sub_row: dict, brain_dir: Path) -> bool:
    """判定单个子会话行是否处于真实活跃态（SQLite 字段 + transcript 尾部双重校验）。"""
    s_not_idle = bool(sub_row.get("not_fully_idle"))
    s_raw = str(sub_row.get("status") or "")
    if not (s_not_idle or "RUNNING" in s_raw.upper()):
        return False
    s_ctarget = str(sub_row.get("conversation_id") or "")
    s_tp = _transcript_path(brain_dir, s_ctarget)
    s_active = True
    if s_tp.is_file():
        try:
            s_active, _ = inspect_transcript_tail_status(s_tp)
        except OSError:
            s_active = True
    return s_active


def _raw_agy_status(agent_type: str, not_idle: bool, raw_status: str) -> str:
    """SQLite 表内字段 → 候选运行状态。"""
    if agent_type == "agy_ide" and not_idle:
        return "desktop_active"
    if not_idle or "RUNNING" in raw_status.upper():
        return "running"
    return "idle"


def _calibrate_agy_status(
    agent_type: str,
    not_idle: bool,
    raw_status: str,
    brain_dir: Path,
    cid: str,
    has_active_sub: bool,
) -> str:
    """状态权威性校准：若会话 transcript 明确已结束，绝不被滞后的 SQLite 表误判为活跃；
    但若名下存在活跃子进程，父会话必须保持活跃态。"""
    status = _raw_agy_status(agent_type, not_idle, raw_status)
    if status in ("desktop_active", "running"):
        tp = _transcript_path(brain_dir, cid)
        if tp.is_file():
            try:
                is_active, s_status = inspect_transcript_tail_status(tp)
                if not is_active:
                    if s_status == "error":
                        status = "error"
                        if cid not in _logged_agy_error_sids:
                            _logged_agy_error_sids.add(cid)
                            logger.warning(f"Antigravity 会话检测到异常中断/额度耗尽: sid={cid[:8]}")
                    else:
                        status = "desktop_active" if has_active_sub else "idle"
                elif agent_type != "agy_ide":
                    status = s_status
            except OSError:
                pass
    elif has_active_sub:
        status = "desktop_active" if agent_type == "agy_ide" else "running"
    return status


def _agy_row_to_unified_session(
    r: dict,
    *,
    agent_type: str,
    agent_label: str,
    brain_dir: Path,
    now_iso: str,
    has_parent_col: bool,
    has_depth_col: bool,
    not_idle: bool,
    has_active_sub: bool,
    active_sub_count: int,
    probe_filter: bool = True,
) -> Optional[UnifiedSession]:
    """单条 conversation_summaries 行 → UnifiedSession（列表与单会话查询共享同一份映射知识）。"""
    cid = str(r.get("conversation_id") or "")
    if not cid:
        return None

    status = _calibrate_agy_status(
        agent_type=agent_type,
        not_idle=not_idle,
        raw_status=str(r.get("status") or ""),
        brain_dir=brain_dir,
        cid=cid,
        has_active_sub=has_active_sub,
    )

    title = (r.get("title") or "").strip()
    preview = (r.get("preview") or "").strip()
    if not title and preview:
        clean_p = preview.split("\n")[0].strip()
        clean_p = clean_p.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
        if clean_p:
            title = clean_p[:40]
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

    if probe_filter and is_temporary_probe_session(title=title, preview=preview, project_path=p_path):
        return None

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
        desktop_locked=(agent_type == "agy_ide" and (not_idle or has_active_sub)),
        parent_unified_id=parent_uid,
        is_subagent=is_sub,
        has_active_subagent=has_active_sub,
        active_subagents_count=active_sub_count,
    )


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

        # 性能极致优化：单次批量预查所有已记录的子进程归属与活跃状态
        all_known_subagent_ids: set[str] = set()
        active_subagents_by_parent: dict[str, int] = {}
        if has_parent_col:
            try:
                where_clause = "WHERE (parent_conversation_id IS NOT NULL AND parent_conversation_id != '')"
                if has_depth_col:
                    where_clause += " OR nesting_depth > 0"
                sub_rows = safe_query_external_sqlite(
                    summary_db_path,
                    f"SELECT conversation_id, parent_conversation_id, status, not_fully_idle FROM conversation_summaries {where_clause}",
                    (),
                )
                for sr in sub_rows:
                    s_cid = str(sr.get("conversation_id") or "")
                    p_cid = str(sr.get("parent_conversation_id") or "")
                    if s_cid:
                        all_known_subagent_ids.add(s_cid)
                        known_parent_map[s_cid] = p_cid
                        if p_cid and _subagent_row_is_active(sr, brain_dir):
                            active_subagents_by_parent[p_cid] = active_subagents_by_parent.get(p_cid, 0) + 1
            except Exception as e:
                logger.debug(f"读取 Antigravity 子会话映射关系忽略容错: {e}")

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

            # 内存秒级映射名下活跃子进程数量（0ms 无磁盘开销）
            active_sub_count = active_subagents_by_parent.get(cid, 0)
            has_active_sub = active_sub_count > 0

            sess = _agy_row_to_unified_session(
                r,
                agent_type=agent_type,
                agent_label=agent_label,
                brain_dir=brain_dir,
                now_iso=now_iso,
                has_parent_col=has_parent_col,
                has_depth_col=has_depth_col,
                not_idle=bool(r.get("not_fully_idle")),
                has_active_sub=has_active_sub,
                active_sub_count=active_sub_count,
            )
            if sess is None:
                continue

            # probe 会话已在映射器内被过滤，此处再登记父子关系（与旧行为一致：过滤后才入册）
            parent_cid = str(r.get("parent_conversation_id") or "").strip() if has_parent_col else ""
            if parent_cid:
                known_parent_map[cid] = parent_cid
            sessions_map[cid] = sess

    # 2. 补漏 brain 目录
    if brain_dir.is_dir():
        try:
            for b_item in brain_dir.iterdir():
                if not b_item.is_dir() or b_item.name in sessions_map:
                    continue
                cid = b_item.name
                if cid in all_known_subagent_ids or cid in known_parent_map:
                    continue
                tp = b_item / ".system_generated" / "logs" / "transcript.jsonl"
                if not tp.is_file():
                    continue
                # 检查第一行：若是子进程系统指令直接跳过
                try:
                    with open(tp, "r", encoding="utf-8") as f_chk:
                        chk_l = f_chk.readline()
                        if chk_l:
                            chk_it = json.loads(chk_l)
                            if chk_it.get("source") == "SYSTEM" or chk_it.get("type") == "SYSTEM_MESSAGE" or "not actually sent by the user" in str(chk_it.get("content", "")):
                                continue
                except Exception:
                    pass
                try:
                    st = tp.stat()
                    up_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                    is_active, s_status = inspect_transcript_tail_status(tp)
                    status = s_status if (is_active or s_status == "error") else "idle"
                except OSError:
                    up_iso = now_iso
                    status = "idle"

                cid = b_item.name
                p_cid = known_parent_map.get(cid)
                is_sub = cid in known_parent_map
                parent_uid = f"{agent_type}:{p_cid}" if p_cid else None

                fallback_title = ""
                fallback_preview = "来自本地日志"
                if tp.is_file():
                    try:
                        with open(tp, "r", encoding="utf-8") as f_tp:
                            for _ in range(5):
                                line = f_tp.readline()
                                if not line:
                                    break
                                try:
                                    item = json.loads(line)
                                    if item.get("type") == "USER_INPUT" and item.get("content"):
                                        c_text = str(item["content"]).replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip().split("\n")[0]
                                        if c_text:
                                            fallback_title = c_text[:40]
                                            fallback_preview = c_text
                                            break
                                except Exception:
                                    pass
                    except Exception:
                        pass

                sessions_map[cid] = UnifiedSession(
                    unified_id=f"{agent_type}:{cid}",
                    agent_type=agent_type,
                    agent_label=agent_label,
                    raw_session_id=cid,
                    title=fallback_title or f"会话 {cid[:8]}",
                    preview=fallback_preview,
                    project_path="",
                    status=status,
                    step_count=1,
                    updated_at=up_iso,
                    parent_unified_id=parent_uid,
                    is_subagent=is_sub,
                )
        except OSError:
            pass

    # 3. 子进程/子代理活跃状态向上冒泡 (Subagent Active Propagation)
    # 只要存在活跃的子代理（来自已识别的会话列表或全局活跃映射），父会话必定属于活跃任务（对应电脑端 "..." 运行状态）
    for s in list(sessions_map.values()):
        if not s.is_subagent or not s.parent_unified_id:
            continue
        if s.status in ("running", "desktop_active"):
            p_cid = s.parent_unified_id.split(":", 1)[1] if ":" in s.parent_unified_id else s.parent_unified_id
            parent_sess = sessions_map.get(p_cid)
            if parent_sess:
                parent_sess.has_active_subagent = True
                parent_sess.active_subagents_count += 1
                parent_sess.desktop_locked = True
                if parent_sess.status == "idle":
                    parent_sess.status = "desktop_active" if agent_type == "agy_ide" else "running"

    for p_cid, count in active_subagents_by_parent.items():
        parent_sess = sessions_map.get(p_cid)
        if parent_sess:
            parent_sess.has_active_subagent = True
            parent_sess.active_subagents_count = max(parent_sess.active_subagents_count, count)
            parent_sess.desktop_locked = True
            if parent_sess.status == "idle":
                parent_sess.status = "desktop_active" if agent_type == "agy_ide" else "running"

    sorted_list = sorted(sessions_map.values(), key=lambda s: s.updated_at, reverse=True)
    return sorted_list[:limit]


def resolve_agy_default_model(
    ls_bridge: Any,
    root_dir: Path,
    summary_db_path: Path,
    agent_type: str = "agy_ide",
) -> str:
    """多层级自适应推断默认模型 ID。"""
    # 1. 优先读取状态配置文件中的显式选定模型
    state_file = root_dir / "antigravity_state.pbtxt"
    if not state_file.exists() and agent_type == "agy_cli":
        global_state = Path(os.path.expanduser("~/.gemini/antigravity/antigravity_state.pbtxt"))
        if global_state.exists():
            state_file = global_state

    parsed_model = parse_state_selected_model(state_file)
    if parsed_model:
        # 若 LS 在线，校验该选定模型是否在当前账号实际授权列表中；若已被当前账号淘汰/禁用则跳过自动回退
        if hasattr(ls_bridge, "get_cascade_model_configs"):
            try:
                configs = ls_bridge.get_cascade_model_configs()
                if configs:
                    from backend.adapters.antigravity_ls_bridge import LS_MODEL_ID_MAP
                    valid_ids = set()
                    for c in configs:
                        raw_id = str(c.get("modelId") or "")
                        lbl = str(c.get("label") or "")
                        mid = LS_MODEL_ID_MAP.get(raw_id.lower(), raw_id).lower()
                        valid_ids.add(mid)
                        valid_ids.add(raw_id.lower())
                        valid_ids.add(lbl.lower())
                    if parsed_model.lower() in valid_ids:
                        return parsed_model
                else:
                    return parsed_model
            except Exception:
                return parsed_model
        else:
            return parsed_model

    # 2. 实时直连电脑端 Language Server 获取当前账号默认模型
    try:
        ls_def = ls_bridge.get_default_model_id()
        if ls_def:
            return ls_def
    except Exception:
        pass

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

    from backend.engines.process_pool import AGENT_MODELS
    base_models = AGENT_MODELS.get(agent_type, [])
    rec_model = next((m["id"] for m in base_models if m.get("is_recommended")), None)
    return rec_model or (base_models[0]["id"] if base_models else "gemini-3.8-flash-high")


def merge_agy_models_catalog(
    ls_bridge: Any,
    default_mid: str,
    agent_type: str = "agy_ide",
) -> list[dict[str, Any]]:
    """获取带实时配额的可用模型列表。

    第一性原理与真源对齐：
    1. 若已连接电脑端 Language Server，以当前账号实际授权返回且未被 disabled 的模型清单为唯一权威来源；
    2. 绝不向列表中额外混入当前账号根本不可用或已被 disabled 的静态硬编码模型；
    3. 列表顺序严格遵循电脑端官方 clientModelSorts 推荐排序。
    """
    configs = ls_bridge.get_cascade_model_configs()
    if configs:
        from backend.adapters.antigravity_ls_bridge import LS_MODEL_ID_MAP
        import re

        sort_labels = getattr(ls_bridge, "_cached_sort_labels", [])
        sort_map = {lbl.lower(): idx for idx, lbl in enumerate(sort_labels)} if sort_labels else {}

        items: list[dict[str, Any]] = []
        for c in configs:
            raw_id = str(c.get("modelId") or "")
            lbl = str(c.get("label") or "")
            mapped_id = LS_MODEL_ID_MAP.get(raw_id.lower(), raw_id)
            if not mapped_id or mapped_id.startswith("MODEL_PLACEHOLDER_"):
                clean_lbl = re.sub(r"[^\w\s-]", "", lbl.lower())
                norm_id = re.sub(r"[-\s]+", "-", clean_lbl).strip("-")
            else:
                norm_id = mapped_id.lower().replace("_", "-")

            is_def = bool(
                norm_id == default_mid
                or raw_id.lower() == default_mid.lower()
                or mapped_id.lower() == default_mid.lower()
            )

            items.append({
                "id": norm_id,
                "label": lbl,
                "tag": str(c.get("tag") or ""),
                "quota": c.get("quota"),
                "reset_time": c.get("reset_time"),
                "is_recommended": bool(c.get("is_recommended")),
                "is_default": is_def,
            })

        # 依据电脑端官方排序，并确保当前默认模型置顶
        if sort_map:
            items.sort(key=lambda m: (0 if m.get("is_default") else 1, sort_map.get(m["label"].lower(), 999)))
        else:
            items.sort(key=lambda m: (0 if m.get("is_default") else 1))

        if items and not any(m.get("is_default") for m in items):
            items[0]["is_default"] = True
        return items

    # 离线兜底：仅当未连接 LS 时使用基础默认清单
    from backend.engines.process_pool import AGENT_MODELS
    base_models = AGENT_MODELS.get(agent_type, [])
    fallback: list[dict[str, Any]] = []
    for bm in base_models:
        item = dict(bm)
        item["quota"] = None
        if item["id"] == default_mid:
            item["is_default"] = True
        fallback.append(item)
    if fallback and not any(m.get("is_default") for m in fallback):
        fallback[0]["is_default"] = True
    return fallback

from backend.adapters.antigravity_fork import fork_ide_session_to_cli


def read_antigravity_messages(
    transcript_path: Path,
    raw_session_id: str,
    agent_label: str,
    messages_cache: BoundedLRUCache,
    limit: int = 120,
) -> list[UnifiedMessageItem]:
    """增量/全量解析 transcript.jsonl 并映射追加结构化卡片流（共享读取骨架 + Antigravity 解析钩子）。"""

    def restore_state(items: list[UnifiedMessageItem], _tail: tuple) -> list:
        # 每次都从 items 尾部重推导：parse_agy_step_dict 会回写 container[0]，容器必须为 list
        last_item = items[-1] if items else None
        return [last_item if (last_item is not None and last_item.role == "assistant") else None]

    def parse_record(step_obj: dict, items: list[UnifiedMessageItem], line_index: int, state: list) -> None:
        parse_agy_step_dict(
            step=step_obj,
            raw_session_id=raw_session_id,
            fallback_idx=line_index,
            items=items,
            last_assistant_container=state,
        )

    return read_incremental_jsonl_stream(
        fpath=transcript_path,
        cache_key=raw_session_id,
        messages_cache=messages_cache,
        family_label=agent_label,
        parse_record=parse_record,
        restore_state=restore_state,
        limit=limit,
        failure_hint="系统将在下次文件刷新时自动重试。",
    )


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

            # 检查名下是否有处于活跃态的子进程
            has_active_sub = False
            active_sub_count = 0
            if has_parent_col:
                try:
                    sub_rows = safe_query_external_sqlite(
                        summary_db_path,
                        "SELECT conversation_id, status, not_fully_idle FROM conversation_summaries WHERE parent_conversation_id = ? AND killed = 0",
                        (cid,),
                    )
                    for sr in sub_rows:
                        s_cid = str(sr.get("conversation_id") or "")
                        if s_cid and _subagent_row_is_active(sr, brain_dir):
                            has_active_sub = True
                            active_sub_count += 1
                except Exception as e:
                    logger.debug(f"单会话检查子进程活跃容错: {e}")

            sess = _agy_row_to_unified_session(
                r,
                agent_type=agent_type,
                agent_label=agent_label,
                brain_dir=brain_dir,
                now_iso=now_iso,
                has_parent_col=has_parent_col,
                has_depth_col=has_depth_col,
                not_idle=not_idle,
                has_active_sub=has_active_sub,
                active_sub_count=active_sub_count,
                probe_filter=False,
            )
            if sess is not None:
                return sess

    # 查 brain 目录
    tp = _transcript_path(brain_dir, cid)
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
