# -*- coding: utf-8 -*-
"""
多 Agent 会话聚合器与消息流详情装配器。
负责跨 Agent 会话发现、状态覆盖、优先级排序、分页消息拼接与 Token 水位关联。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    ExecutionMode,
    SessionTokenUsage,
    UnifiedSession,
)
from backend.core.database import PROJECT_ROOT, MetaDatabase, utc_now_iso
from backend.core.logger import log_structured_error, logger
from backend.core.workspace import evaluate_project_dir
from backend.engines.approval import PendingApproval


def aggregate_all_sessions(
    adapters: dict[str, BaseAgentAdapter],
    meta_db: MetaDatabase,
    pending_approvals: dict[str, PendingApproval],
    is_session_active_fn: Callable[[str], bool],
    resolve_default_model_fn: Callable[[str], str],
    agent_filter: Optional[str] = None,
    limit_per_agent: int = 40,
) -> list[dict[str, Any]]:
    """聚合 4 大 Agent 的会话列表，合并本地置顶/执行模式与实时进程状态。"""
    overrides = meta_db.get_session_overrides()
    all_ws = meta_db.list_workspaces(include_hidden=True)
    bookmarked_keys: set[str] = set()
    hidden_keys: set[str] = set()
    ws_custom_names: dict[str, str] = {}
    for w in all_ws:
        w_path = str(w.get("workspace_path") or "").strip().replace("\\", "/").rstrip("/")
        if not w_path:
            continue
        w_key = w_path.lower()
        if int(w.get("is_hidden") or 0) == 1:
            hidden_keys.add(w_key)
        else:
            bookmarked_keys.add(w_key)
            if w.get("workspace_name"):
                ws_custom_names[w_key] = str(w["workspace_name"])

    proj_eval_cache: dict[str, tuple[bool, str, str]] = {}

    def resolve_proj(raw_p: str) -> tuple[bool, str, str]:
        if raw_p not in proj_eval_cache:
            is_p, norm_p, p_name = evaluate_project_dir(raw_p, bookmarked_keys, hidden_keys)
            if is_p and norm_p.lower() in ws_custom_names:
                p_name = ws_custom_names[norm_p.lower()]
            proj_eval_cache[raw_p] = (is_p, norm_p, p_name)
        return proj_eval_cache[raw_p]

    all_sessions: list[UnifiedSession] = []

    target_adapters = [
        (key, adapter)
        for key, adapter in adapters.items()
        if not (agent_filter and agent_filter != "all" and agent_filter != key)
    ]

    def _fetch_adapter_sessions(item: tuple[str, BaseAgentAdapter]) -> list[UnifiedSession]:
        _, adp = item
        try:
            return adp.list_sessions(limit=limit_per_agent)
        except Exception as exc:
            log_structured_error(
                problem=f"聚合 {adp.agent_label} 会话列表异常",
                cause=str(exc),
                fix="已跳过该适配器本次查询，不影响其他 Agent 会话展示。",
                exc=exc,
            )
            return []

    if len(target_adapters) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(len(target_adapters), 4)) as executor:
            for results in executor.map(_fetch_adapter_sessions, target_adapters):
                all_sessions.extend(results)
    elif len(target_adapters) == 1:
        all_sessions.extend(_fetch_adapter_sessions(target_adapters[0]))

    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for sess in all_sessions:
        seen_ids.add(sess.unified_id)
        ov = overrides.get(sess.unified_id)
        if ov:
            sess.is_pinned = int(ov.get("is_pinned") or 0)
            sess.execution_mode = str(ov.get("execution_mode") or ExecutionMode.ASK.value)
            if ov.get("model_name"):
                sess.model_name = str(ov["model_name"])
            if ov.get("title"):
                sess.title = str(ov["title"])
            if ov.get("project_path") and not sess.project_path:
                sess.project_path = str(ov["project_path"])
            sess.parent_unified_id = ov.get("parent_unified_id")
            if sess.parent_unified_id:
                sess.is_subagent = True

        if not sess.model_name:
            sess.model_name = resolve_default_model_fn(sess.agent_type)

        is_proj, norm_path, proj_name = resolve_proj(sess.project_path)
        sess.project_path = norm_path
        sess.is_project = is_proj
        sess.project_name = proj_name

        if sess.unified_id in pending_approvals:
            sess.status = "waiting_approval"
        elif is_session_active_fn(sess.unified_id):
            sess.status = "running"

        merged.append(sess.to_dict())

    for uid, ov in overrides.items():
        if uid not in seen_ids:
            atype = str(ov.get("agent_type") or "claude_code")
            if agent_filter and agent_filter != "all" and agent_filter != atype:
                continue
            adapter_obj = adapters.get(atype)
            label = adapter_obj.agent_label if adapter_obj else atype
            raw_id = str(ov.get("raw_session_id") or "")

            sess_obj: Optional[UnifiedSession] = None
            if adapter_obj and raw_id:
                try:
                    sess_obj = adapter_obj.get_session(raw_id)
                except Exception as exc:
                    logger.debug(f"尝试按 ID 检索会话 {uid} 异常: {exc}")

            if sess_obj:
                sess = sess_obj
                sess.is_pinned = int(ov.get("is_pinned") or 0)
                sess.execution_mode = str(ov.get("execution_mode") or ExecutionMode.ASK.value)
                if ov.get("model_name"):
                    sess.model_name = str(ov["model_name"])
                if ov.get("title") and str(ov["title"]).strip():
                    sess.title = str(ov["title"]).strip()
                if ov.get("project_path") and not sess.project_path:
                    sess.project_path = str(ov["project_path"])
                if ov.get("parent_unified_id"):
                    sess.parent_unified_id = ov.get("parent_unified_id")
                    sess.is_subagent = True

                is_proj, norm_path, proj_name = resolve_proj(sess.project_path)
                sess.project_path = norm_path
                sess.is_project = is_proj
                sess.project_name = proj_name

                if sess.unified_id in pending_approvals:
                    sess.status = "waiting_approval"
                elif is_session_active_fn(sess.unified_id):
                    sess.status = "running"

                merged.append(sess.to_dict())
                continue

            status = (
                "waiting_approval"
                if uid in pending_approvals
                else ("running" if is_session_active_fn(uid) else "idle")
            )

            default_mid = resolve_default_model_fn(atype)
            raw_p = str(ov.get("project_path") or str(PROJECT_ROOT))
            is_proj, norm_path, proj_name = resolve_proj(raw_p)
            merged.append(
                UnifiedSession(
                    unified_id=uid,
                    agent_type=atype,
                    agent_label=label,
                    raw_session_id=raw_id,
                    title=str(ov.get("title") or "新建会话"),
                    preview="等待发送首条指令...",
                    project_path=norm_path,
                    status=status,
                    step_count=0,
                    updated_at=str(ov.get("updated_at") or utc_now_iso()),
                    is_pinned=int(ov.get("is_pinned") or 0),
                    execution_mode=str(ov.get("execution_mode") or ExecutionMode.ASK.value),
                    model_name=str(ov.get("model_name") or default_mid),
                    is_project=is_proj,
                    project_name=proj_name,
                    parent_unified_id=ov.get("parent_unified_id"),
                    is_subagent=bool(ov.get("parent_unified_id")),
                ).to_dict()
            )

    def _session_sort_key(x: dict[str, Any]) -> tuple:
        pinned = int(x.get("is_pinned") or 0)
        st = str(x.get("status") or "").lower()
        status_weight = 2 if st == "waiting_approval" else (1 if st in ("running", "desktop_active") else 0)
        updated_at = str(x.get("updated_at") or "")
        return (pinned, status_weight, updated_at)

    merged.sort(key=_session_sort_key, reverse=True)
    return merged


def fetch_session_detail(
    unified_id: str,
    adapters: dict[str, BaseAgentAdapter],
    meta_db: MetaDatabase,
    pending_approvals: dict[str, PendingApproval],
    is_session_active_fn: Callable[[str], bool],
    resolve_default_model_fn: Callable[[str], str],
    limit: int = 30,
    before_step: Optional[int] = None,
    since_step: Optional[int] = None,
) -> dict[str, Any]:
    """获取指定统一会话的结构化消息流及当前状态。"""
    if ":" not in unified_id:
        return {
            "unified_id": unified_id,
            "messages": [],
            "total_count": 0,
            "has_more": False,
            "pending_approval": None,
        }
    agent_type, raw_id = unified_id.split(":", 1)
    adapter = adapters.get(agent_type)
    messages: list[dict[str, Any]] = []
    total_count = 0
    has_more = False
    if adapter:
        items, total_count, has_more = adapter.get_session_messages_page(
            raw_id, limit=limit, before_step=before_step, since_step=since_step
        )
        if items:
            last_idx = len(items) - 1
            messages = [
                it.to_dict(compact_thinking=(idx < last_idx))
                for idx, it in enumerate(items)
            ]

    pa = pending_approvals.get(unified_id)
    overrides = meta_db.get_session_overrides().get(unified_id, {})
    session_actual_model = ""
    if adapter and hasattr(adapter, "get_session_model"):
        try:
            session_actual_model = adapter.get_session_model(raw_id) or ""
        except Exception as exc:
            logger.debug(f"获取会话 {raw_id[:8]} 真实模型异常: {exc}")
    default_mid = resolve_default_model_fn(agent_type)
    chosen_model = overrides.get("model_name") or session_actual_model or default_mid
    token_usage = (
        adapter.get_session_token_usage(raw_id, model_name=chosen_model)
        if adapter
        else SessionTokenUsage(context_limit=200_000).to_dict()
    )

    is_active = is_session_active_fn(unified_id)
    if not is_active and adapter:
        try:
            if hasattr(adapter, "is_external_session_active"):
                is_active = adapter.is_external_session_active(raw_id)
            else:
                for s in adapter.list_sessions(limit=50):
                    if s.raw_session_id == raw_id:
                        if s.status in ("running", "desktop_active"):
                            is_active = True
                        break
        except Exception as exc:
            logger.error("检查外部会话活跃态失败: 会话ID=%s, 异常=%s", raw_id[:8], exc)
    if not is_active and messages:
        last_msg = messages[-1]
        if last_msg.get("status") == "RUNNING":
            is_active = True
        elif isinstance(last_msg.get("tool_calls"), list):
            if any(
                tc.get("status") in ("RUNNING", "PENDING")
                for tc in last_msg["tool_calls"]
                if isinstance(tc, dict)
            ):
                is_active = True

    return {
        "unified_id": unified_id,
        "agent_type": agent_type,
        "raw_session_id": raw_id,
        "execution_mode": overrides.get("execution_mode", ExecutionMode.ASK.value),
        "model_name": chosen_model,
        "is_pinned": overrides.get("is_pinned", 0),
        "is_running": is_active,
        "pending_approval": pa.to_dict() if pa else None,
        "token_usage": token_usage,
        "total_count": total_count,
        "has_more": has_more,
        "messages": messages,
    }


def fetch_full_step_output(
    unified_id: str,
    step_index: int,
    adapters: dict[str, BaseAgentAdapter],
) -> str:
    """按需拉取被截断的单步完整输出。"""
    if ":" not in unified_id:
        return ""
    agent_type, raw_id = unified_id.split(":", 1)
    adapter = adapters.get(agent_type)
    if adapter and hasattr(adapter, "get_full_step_output"):
        return adapter.get_full_step_output(raw_id, step_index)
    return ""


def build_startup_health_report(adapters: dict[str, BaseAgentAdapter]) -> list[dict[str, Any]]:
    """生成所有已注册 Agent 适配器的启动连通性与健康状态报告列表。"""
    report: list[dict[str, Any]] = []
    for agent_key, adapter in adapters.items():
        avail, msg = adapter.is_available()
        report.append({
            "agent_type": agent_key,
            "agent_label": adapter.agent_label,
            "ready": avail,
            "description": msg,
        })
    return report


def build_system_overview(
    adapters: dict[str, BaseAgentAdapter],
    active_processes: dict[str, Any],
    active_direct_tasks: dict[str, Any],
    pending_approvals: dict[str, Any],
    health_list: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """聚合系统当前运行中会话总数、待审批任务及探针报告。"""
    health = health_list if health_list is not None else build_startup_health_report(adapters)
    agents_dict = {
        item["agent_type"]: {
            "label": item["agent_label"],
            "available": item["ready"],
            "diagnostic": item["description"],
        }
        for item in health
    }
    all_active: set[str] = set(active_processes.keys()) | set(active_direct_tasks.keys())
    for _, adp in adapters.items():
        if hasattr(adp, "get_active_session_ids"):
            try:
                all_active.update(adp.get_active_session_ids())
            except Exception:
                pass
    return {
        "agents": agents_dict,
        "running_sessions_count": len(all_active),
        "pending_approvals_count": len(pending_approvals),
    }


