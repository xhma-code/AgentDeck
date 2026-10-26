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
    deleted_ids = meta_db.get_deleted_session_ids()
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

    # 同源 Antigravity 会话跨端去重（避免 Fork-to-CLI 后列表中同时出现原会话与镜像会话）
    deduped_sessions: list[UnifiedSession] = []
    antigravity_by_raw: dict[str, list[UnifiedSession]] = {}
    for sess in all_sessions:
        if sess.agent_type in ("agy_ide", "agy_cli"):
            antigravity_by_raw.setdefault(sess.raw_session_id, []).append(sess)
        else:
            deduped_sessions.append(sess)

    for raw_id, group in antigravity_by_raw.items():
        if len(group) == 1:
            deduped_sessions.append(group[0])
        else:
            # 优先选择拥有有效业务标题（非 "会话 xxx"）的会话
            with_meaningful_title = [
                s for s in group
                if not (s.title.startswith("会话 ") and s.title[3:].strip() in s.raw_session_id)
            ]
            if len(with_meaningful_title) == 1:
                deduped_sessions.append(with_meaningful_title[0])
            else:
                best = max(group, key=lambda s: str(s.updated_at or ""))
                deduped_sessions.append(best)
    all_sessions = deduped_sessions

    # 性能优化：按 Agent 类型单次预热可用模型 ID 集合，杜绝循环内重复计算
    valid_ids_by_agent: dict[str, set[str]] = {}
    for a_type, adp in adapters.items():
        if hasattr(adp, "get_available_models_with_quota"):
            try:
                avail = adp.get_available_models_with_quota()
                valid_ids_by_agent[a_type] = {m["id"].lower() for m in avail}
            except Exception:
                pass

    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for sess in all_sessions:
        if sess.unified_id in deleted_ids:
            continue
        seen_ids.add(sess.unified_id)
        ov = overrides.get(sess.unified_id)
        if ov:
            sess.is_pinned = int(ov.get("is_pinned") or 0)
            sess.execution_mode = str(ov.get("execution_mode") or ExecutionMode.AUTO.value)
            if ov.get("model_name"):
                sess.model_name = str(ov["model_name"])
            if ov.get("title"):
                sess.title = str(ov["title"])
            if ov.get("project_path") and not sess.project_path:
                sess.project_path = str(ov["project_path"])
            sess.parent_unified_id = ov.get("parent_unified_id")
            if sess.parent_unified_id:
                sess.is_subagent = True
            if ov.get("last_viewed_at"):
                sess.last_viewed_at = str(ov["last_viewed_at"])

        if not sess.model_name:
            sess.model_name = resolve_default_model_fn(sess.agent_type)

        # 校验模型是否在当前 Agent 的可用模型中有效，若失效自动校准为当前电脑端生效的默认模型
        valid_ids = valid_ids_by_agent.get(sess.agent_type)
        if valid_ids and sess.model_name and sess.model_name.lower() not in valid_ids:
            sess.model_name = resolve_default_model_fn(sess.agent_type)

        is_proj, norm_path, proj_name = resolve_proj(sess.project_path)
        sess.project_path = norm_path
        sess.is_project = is_proj
        sess.project_name = proj_name

        if sess.unified_id in pending_approvals:
            sess.status = "waiting_approval"
        elif is_session_active_fn(sess.unified_id):
            sess.status = "running"

        d = dict(sess.to_dict())
        # S1：携带数据库真相状态（creating/live/degraded/dead），前端由此驱动徽标
        d["lifecycle"] = str((ov or {}).get("status") or "live")
        merged.append(d)

    seen_agy_raw_ids: set[str] = {
        s.raw_session_id for s in all_sessions if s.agent_type in ("agy_ide", "agy_cli")
    }

    for uid, ov in overrides.items():
        if uid in deleted_ids or int(ov.get("is_deleted") or 0) == 1:
            continue
        if ov.get("parent_unified_id"):
            continue
        atype = str(ov.get("agent_type") or "claude_code")
        raw_id = str(ov.get("raw_session_id") or "")
        if atype in ("agy_ide", "agy_cli") and raw_id in seen_agy_raw_ids:
            continue
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
                if sess.is_subagent or sess.parent_unified_id:
                    continue
                sess.is_pinned = int(ov.get("is_pinned") or 0)
                sess.execution_mode = str(ov.get("execution_mode") or ExecutionMode.AUTO.value)
                if ov.get("model_name"):
                    sess.model_name = str(ov["model_name"])
                if ov.get("title") and str(ov["title"]).strip():
                    sess.title = str(ov["title"]).strip()
                if ov.get("project_path") and not sess.project_path:
                    sess.project_path = str(ov["project_path"])
                if ov.get("parent_unified_id"):
                    sess.parent_unified_id = ov.get("parent_unified_id")
                    sess.is_subagent = True
                if ov.get("last_viewed_at"):
                    sess.last_viewed_at = str(ov["last_viewed_at"])

                if not sess.model_name:
                    sess.model_name = resolve_default_model_fn(atype)

                # 校验模型有效性：若旧记录中的模型已不可用，纠偏为当前电脑端生效的默认模型
                if adapter_obj and hasattr(adapter_obj, "get_available_models_with_quota"):
                    try:
                        avail = adapter_obj.get_available_models_with_quota()
                        valid_ids = {m["id"].lower() for m in avail}
                        if valid_ids and sess.model_name.lower() not in valid_ids:
                            sess.model_name = resolve_default_model_fn(atype)
                    except Exception:
                        pass

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
            chosen_mid = str(ov.get("model_name") or "")
            if adapter_obj and hasattr(adapter_obj, "get_available_models_with_quota"):
                try:
                    avail = adapter_obj.get_available_models_with_quota()
                    valid_ids = {m["id"].lower() for m in avail}
                    if not chosen_mid or (valid_ids and chosen_mid.lower() not in valid_ids):
                        chosen_mid = default_mid
                except Exception:
                    chosen_mid = default_mid or chosen_mid
            else:
                chosen_mid = chosen_mid or default_mid

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
                    execution_mode=str(ov.get("execution_mode") or ExecutionMode.AUTO.value),
                    model_name=chosen_mid,
                    is_project=is_proj,
                    project_name=proj_name,
                    parent_unified_id=ov.get("parent_unified_id"),
                    is_subagent=bool(ov.get("parent_unified_id")),
                    last_viewed_at=ov.get("last_viewed_at"),
                ).to_dict()
            )
            merged[-1]["lifecycle"] = str(ov.get("status") or "live")

    def _session_sort_key(x: dict[str, Any]) -> tuple:
        pinned = int(x.get("is_pinned") or 0)
        st = str(x.get("status") or "").lower()
        status_weight = 2 if st == "waiting_approval" else (1 if st in ("running", "desktop_active") else 0)
        updated_at = str(x.get("updated_at") or "")
        return (pinned, status_weight, updated_at)

    merged.sort(key=_session_sort_key, reverse=True)
    return merged

_DETAIL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DETAIL_CACHE_TTL = 2.0


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
    import time

    actual_unified_id = meta_db.resolve_unified_id(unified_id) if hasattr(meta_db, "resolve_unified_id") else unified_id
    cache_key = f"{actual_unified_id}|{limit}|{before_step}|{since_step}"
    is_active_now_check = is_session_active_fn(unified_id) or is_session_active_fn(actual_unified_id)
    pa_now = pending_approvals.get(unified_id) or pending_approvals.get(actual_unified_id)
    if not is_active_now_check and before_step is None and since_step is None and pa_now is None:
        cached = _DETAIL_CACHE.get(cache_key)
        if cached:
            ts, val = cached
            if time.monotonic() - ts < _DETAIL_CACHE_TTL:
                return val

    agent_type, raw_id = actual_unified_id.split(":", 1)
    overrides = meta_db.get_session_overrides().get(actual_unified_id) or meta_db.get_session_overrides().get(unified_id, {})
    actual_raw_id = overrides.get("raw_session_id") or raw_id
    adapter = adapters.get(agent_type)
    messages: list[dict[str, Any]] = []
    total_count = 0
    has_more = False
    if adapter:
        items, total_count, has_more = adapter.get_session_messages_page(
            actual_raw_id, limit=limit, before_step=before_step, since_step=since_step
        )
        if items:
            last_idx = len(items) - 1
            messages = [
                it.to_dict(compact_thinking=(idx < last_idx))
                for idx, it in enumerate(items)
            ]

    pa = pa_now
    deleted_ids = meta_db.get_deleted_session_ids()
    default_mid = resolve_default_model_fn(agent_type)

    from concurrent.futures import ThreadPoolExecutor

    session_actual_model_res = [""]
    ext_active_res = [False]

    def _get_model():
        if adapter and hasattr(adapter, "get_session_model"):
            try:
                return adapter.get_session_model(actual_raw_id) or ""
            except Exception as exc:
                logger.debug(f"获取会话 {actual_raw_id[:8]} 真实模型异常: {exc}")
        return ""

    def _check_external_active():
        if is_active_now_check and adapter:
            return True
        if not adapter:
            return False
        try:
            if hasattr(adapter, "is_external_session_active"):
                return bool(adapter.is_external_session_active(actual_raw_id))
            for s in adapter.list_sessions(limit=50):
                if s.raw_session_id == actual_raw_id:
                    if s.status in ("running", "desktop_active"):
                        return True
                    break
        except Exception as exc:
            logger.error("检查外部会话活跃态失败: 会话ID=%s, 异常=%s", raw_id[:8], exc)
        return False

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_model = ex.submit(_get_model)
        f_ext = ex.submit(_check_external_active)
        session_actual_model_res[0] = f_model.result()
        ext_active_res[0] = f_ext.result()

    session_actual_model = session_actual_model_res[0]
    chosen_model = overrides.get("model_name") or session_actual_model or default_mid

    if adapter and hasattr(adapter, "get_available_models_with_quota"):
        try:
            avail = adapter.get_available_models_with_quota()
            valid_ids = {m["id"].lower() for m in avail}
            if valid_ids and chosen_model.lower() not in valid_ids:
                chosen_model = default_mid
        except Exception:
            pass

    token_usage = (
        adapter.get_session_token_usage(actual_raw_id, model_name=chosen_model)
        if adapter
        else SessionTokenUsage(context_limit=200_000).to_dict()
    )

    is_active = is_active_now_check or ext_active_res[0]
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

    pending_form = None
    if adapter and hasattr(adapter, "fetch_pending_forms"):
        try:
            p_forms = adapter.fetch_pending_forms(actual_raw_id)
            if p_forms:
                pending_form = p_forms[0]
        except Exception as exc:
            logger.debug(f"获取待决议表单异常: {exc}")

    if not pending_form and messages:
        last_msg = messages[-1]
        last_tcs = last_msg.get("tool_calls") or []
        has_running_question = any(
            tc.get("name") in ("ask_question", "askuserquestion", "question", "form")
            and tc.get("status") in ("RUNNING", "PENDING")
            for tc in last_tcs
            if isinstance(tc, dict)
        )
        if (has_running_question or last_msg.get("status") == "RUNNING") and last_msg.get("questions"):
            pending_form = {
                "id": f"syn_{last_msg.get('item_id', 'q')}",
                "sessionID": actual_raw_id,
                "title": "交互式决策问答",
                "fields": [
                    {
                        "key": q.get("key") or f"q{qi}",
                        "title": q.get("header") or f"问题 {qi+1}",
                        "description": q.get("question") or "",
                        "options": [
                            {"value": opt.split(" — ")[0].strip(), "label": opt}
                            for opt in q.get("options", [])
                        ],
                        "is_multi_select": q.get("is_multi_select", False),
                    }
                    for qi, q in enumerate(last_msg["questions"])
                ],
            }

    if pending_form:
        is_active = True

    result = {
        "unified_id": actual_unified_id,
        "original_unified_id": unified_id,
        "agent_type": agent_type,
        "raw_session_id": actual_raw_id,
        "execution_mode": overrides.get("execution_mode", ExecutionMode.AUTO.value),
        "model_name": chosen_model,
        "is_pinned": overrides.get("is_pinned", 0),
        "is_running": is_active,
        # S1 真相状态透出（degraded 时前端展示横幅与重试入口）
        "lifecycle": str(overrides.get("status") or "live"),
        "pending_approval": pa.to_dict() if pa else None,
        "pending_form": pending_form,
        "is_waiting_interaction": bool(pending_form),
        "token_usage": token_usage,
        "total_count": total_count,
        "has_more": has_more,
        "messages": messages,
    }
    if not is_active and before_step is None and since_step is None and pa is None and not pending_form:
        _DETAIL_CACHE[cache_key] = (time.monotonic(), result)
        if len(_DETAIL_CACHE) > 50:
            try:
                _DETAIL_CACHE.pop(next(iter(_DETAIL_CACHE)))
            except Exception:
                pass
    return result


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

def probe_ls_protocol(adapter: BaseAgentAdapter) -> Optional[dict[str, Any]]:
    """Antigravity IDE Connect-RPC 协议探针：实例可达性 + 模型目录回读，失配即暴露协议漂移。"""
    bridge = getattr(adapter, "ls_bridge", None)
    if bridge is None:
        return None
    try:
        instances = list(bridge._iter_reachable_instances())
        if not instances:
            return {"reachable": False, "detail": "未检测到运行中的 Antigravity IDE（不影响 AGY CLI 使用）"}
        pid, port, scheme, token = instances[0]
        ok, _raw = bridge._raw_post(
            scheme=scheme, port=port, csrf_token=token,
            method="GetCascadeModelConfigs", body={}, timeout=3.0,
        )
        if not ok:
            return {
                "reachable": False,
                "detail": f"Connect-RPC 模型目录读取失败 (PID={pid}, Port={port})，疑似 LS 协议漂移，请检查 IDE 版本",
            }
        return {
            "reachable": True,
            "detail": f"Connect-RPC 正常 (PID={pid}, Port={port})，可达实例 {len(instances)} 个",
        }
    except Exception as exc:
        return {"reachable": False, "detail": f"协议探针异常: {exc}"}


def build_startup_health_report(adapters: dict[str, BaseAgentAdapter]) -> list[dict[str, Any]]:
    """生成所有已注册 Agent 适配器的启动连通性与健康状态报告列表。"""
    report: list[dict[str, Any]] = []
    for agent_key, adapter in adapters.items():
        avail, msg = adapter.is_available()
        entry: dict[str, Any] = {
            "agent_type": agent_key,
            "agent_label": adapter.agent_label,
            "ready": avail,
            "description": msg,
        }
        # Antigravity IDE 附加协议层探针：尽早暴露 LS 版本升级导致的 RPC 契约漂移
        if agent_key == "agy_ide" and hasattr(adapter, "ls_bridge"):
            entry["protocol"] = probe_ls_protocol(adapter)
        report.append(entry)
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
        "agents_health": health,
        "running_sessions_count": len(all_active),
        "pending_approvals_count": len(pending_approvals),
    }



def get_session_modified_files_summary(
    unified_id: str,
    adapters: dict[str, BaseAgentAdapter],
    meta_db: MetaDatabase,
) -> dict[str, Any]:
    """
    聚合指定会话实际修改的所有文件列表，并比对工作区 Git 状态标记未提交与已提交。
    """
    from pathlib import Path
    from backend.adapters.tool_utils import is_read_only_tool
    from backend.core.workspace_git import get_git_workspace_status

    if ":" not in unified_id:
        return {
            "ok": False,
            "unified_id": unified_id,
            "project_path": "",
            "is_git_repo": False,
            "branch": "",
            "summary": {"total_modified": 0, "uncommitted_count": 0, "committed_count": 0},
            "files": [],
        }

    actual_unified_id = meta_db.resolve_unified_id(unified_id) if hasattr(meta_db, "resolve_unified_id") else unified_id
    agent_type, raw_id = actual_unified_id.split(":", 1)
    overrides = meta_db.get_session_overrides().get(actual_unified_id) or meta_db.get_session_overrides().get(unified_id, {})
    actual_raw_id = overrides.get("raw_session_id") or raw_id
    adapter = adapters.get(agent_type)

    project_path = overrides.get("project_path") or ""
    if not project_path and adapter:
        sess_obj = adapter.get_session(actual_raw_id)
        if sess_obj and sess_obj.project_path:
            project_path = sess_obj.project_path

    if not adapter:
        return {
            "ok": True,
            "unified_id": unified_id,
            "project_path": project_path,
            "is_git_repo": False,
            "branch": "",
            "summary": {"total_modified": 0, "uncommitted_count": 0, "committed_count": 0},
            "files": [],
        }

    items = adapter.get_session_messages(actual_raw_id, limit=0)
    write_keywords = ("edit", "write", "patch", "create", "replace", "delete", "remove", "append")

    files_map: dict[str, dict[str, Any]] = {}
    norm_project_path = project_path.replace("\\", "/").rstrip("/") if project_path else ""

    for it in items:
        step_idx = getattr(it, "step_index", 0)
        created_at = getattr(it, "created_at", "")
        for tc in getattr(it, "tool_calls", []):
            raw_path = (getattr(tc, "file_path", "") or "").strip().strip('"\'')
            if not raw_path:
                continue
            t_name = (getattr(tc, "name", "") or "").lower()
            if is_read_only_tool(t_name):
                continue

            diff_text = getattr(tc, "diff_text", "") or ""
            add_c = getattr(tc, "diff_add_count", 0) or 0
            del_c = getattr(tc, "diff_del_count", 0) or 0

            is_modifying = (
                diff_text != ""
                or add_c > 0
                or del_c > 0
                or any(k in t_name for k in write_keywords)
            )
            if not is_modifying:
                continue

            clean_p = raw_path.replace("\\", "/")
            # 过滤内部暂存目录与 scratch 临时脚本，聚焦真实业务工程文件
            if "/.gemini/antigravity/brain/" in clean_p.lower() or "/.system_generated/" in clean_p.lower():
                continue

            rel_p = clean_p
            if norm_project_path and clean_p.lower().startswith(norm_project_path.lower() + "/"):
                rel_p = clean_p[len(norm_project_path) + 1 :]
            elif not Path(clean_p).is_absolute():
                rel_p = clean_p.lstrip("./")

            key = rel_p.lower()
            if key not in files_map:
                files_map[key] = {
                    "path": rel_p,
                    "full_path": clean_p,
                    "filename": Path(rel_p).name,
                    "session_additions": add_c,
                    "session_deletions": del_c,
                    "tool_names": [getattr(tc, "name", "")],
                    "last_modified_step": step_idx,
                    "last_modified_at": created_at,
                    "diff_preview": diff_text[:2000] if diff_text else "",
                }
            else:
                entry = files_map[key]
                entry["session_additions"] += add_c
                entry["session_deletions"] += del_c
                curr_tool = getattr(tc, "name", "")
                if curr_tool and curr_tool not in entry["tool_names"]:
                    entry["tool_names"].append(curr_tool)
                if step_idx >= entry["last_modified_step"]:
                    entry["last_modified_step"] = step_idx
                    entry["last_modified_at"] = created_at
                    if diff_text:
                        entry["diff_preview"] = diff_text[:2000]

    git_status = get_git_workspace_status(project_path) if project_path else {
        "ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []
    }
    is_git_repo = bool(git_status.get("is_git_repo", False))
    branch = git_status.get("branch", "")
    git_files = git_status.get("files", [])
    git_rel_map = {f["path"].replace("\\", "/").lower(): f for f in git_files}

    result_files: list[dict[str, Any]] = []
    seen_git_keys = set()
    for key, f_obj in files_map.items():
        git_info = git_rel_map.get(key)
        if git_info:
            seen_git_keys.add(key)
            f_obj["status"] = "uncommitted"
            f_obj["git_status"] = git_info.get("status", "M")
            f_obj["git_additions"] = git_info.get("additions", 0)
            f_obj["git_deletions"] = git_info.get("deletions", 0)
            f_obj["from_session"] = True
        else:
            if is_git_repo:
                f_obj["status"] = "committed"
                f_obj["git_status"] = None
                f_obj["git_additions"] = 0
                f_obj["git_deletions"] = 0
                f_obj["from_session"] = True
            else:
                f_obj["status"] = "non_git"
                f_obj["git_status"] = None
                f_obj["git_additions"] = 0
                f_obj["git_deletions"] = 0
                f_obj["from_session"] = True
        result_files.append(f_obj)

    # 补充工作区中存在但非本会话直接编辑的未提交变更文件
    for git_f in git_files:
        k = git_f["path"].replace("\\", "/").lower()
        if k not in seen_git_keys and k not in files_map:
            result_files.append({
                "path": git_f["path"].replace("\\", "/"),
                "full_path": git_f.get("full_path", ""),
                "filename": Path(git_f["path"]).name,
                "session_additions": 0,
                "session_deletions": 0,
                "tool_names": [],
                "last_modified_step": 0,
                "last_modified_at": "",
                "diff_preview": "",
                "status": "uncommitted",
                "git_status": git_f.get("status", "M"),
                "git_additions": git_f.get("additions", 0),
                "git_deletions": git_f.get("deletions", 0),
                "from_session": False,
            })

    result_files.sort(
        key=lambda x: (
            0 if x["status"] == "uncommitted" else 1,
            -x["last_modified_step"],
        )
    )

    session_files = [f for f in result_files if f.get("from_session")]
    session_uncommitted = sum(1 for f in session_files if f["status"] == "uncommitted")
    session_committed = sum(1 for f in session_files if f["status"] == "committed")
    session_total = len(session_files)

    uncommitted_count = sum(1 for f in result_files if f["status"] == "uncommitted")
    committed_count = sum(1 for f in result_files if f["status"] == "committed")

    project_name = Path(project_path).name if project_path else ""
    session_title = overrides.get("title") or ""
    if not session_title and adapter:
        s_obj = adapter.get_session(actual_raw_id)
        if s_obj and s_obj.title:
            session_title = s_obj.title

    for f_obj in result_files:
        f_obj["project_name"] = project_name
        if f_obj.get("from_session"):
            f_obj["session_title"] = session_title
        else:
            f_obj["session_title"] = f_obj.get("session_title") or "工作区变更"

    return {
        "ok": True,
        "unified_id": unified_id,
        "project_path": project_path,
        "project_name": project_name,
        "session_title": session_title,
        "is_git_repo": is_git_repo,
        "branch": branch,
        "summary": {
            "total_modified": len(result_files),
            "uncommitted_count": uncommitted_count,
            "committed_count": committed_count,
            "session_total_modified": session_total,
            "session_uncommitted_count": session_uncommitted,
            "session_committed_count": session_committed,
        },
        "files": result_files,
    }
