# -*- coding: utf-8 -*-
"""
AgentDeck `/api/v1/*` 统一 REST API 路由。
覆盖系统健康与备份、设备扫码鉴权、Web Push、项目与 MCP 令牌、任务与追加式修订、
Runtime 注册中心、协同会话与官方 Remote 入口、执行与尝试、交互式 Decision 及事件流。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from backend.api.deps import (
    extract_bearer_token,
    serialize_artifact,
    serialize_attempt,
    serialize_decision,
    serialize_dependency,
    serialize_event,
    serialize_execution,
    serialize_external_ref,
    serialize_machine,
    serialize_mcp_token,
    serialize_project,
    serialize_provider,
    serialize_runtime_instance,
    serialize_session,
    serialize_step,
    serialize_step_revision,
    serialize_task,
    serialize_task_revision,
    verify_http_client,
)
from backend.api.schemas import (
    ArtifactCreateRequest,
    DecisionCreateRequest,
    DecisionResolveRequest,
    ExecutionAttemptStartRequest,
    ExecutionCreateRequest,
    ExternalSessionRefBindRequest,
    MachineRegisterRequest,
    McpTokenIssueRequest,
    PairExchangeRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    PushSubscribeRequest,
    RuntimeInstanceRegisterRequest,
    SessionCreateRequest,
    SessionStatusUpdateRequest,
    StepCreateRequest,
    StepReviseRequest,
    StepStatusUpdateRequest,
    TaskCreateRequest,
    TaskDependencyCreateRequest,
    TaskReviseRequest,
    TaskStatusUpdateRequest,
)
from backend.application.artifact_service import ArtifactService
from backend.application.decision_service import DecisionService
from backend.application.event_service import EventService
from backend.application.execution_service import ExecutionService
from backend.application.planning_service import PlanningService
from backend.application.project_service import ProjectService
from backend.application.runtime_registry_service import RuntimeRegistryService
from backend.application.session_service import SessionService
from backend.core.network import (
    detect_tailscale_status,
    is_trusted_vpn_network,
)
from backend.core.workspace import (
    WorkspaceDocumentReader,
    get_git_workspace_diff,
    get_git_workspace_status,
)
from backend.db.backup import create_online_sqlite_backup
from backend.db.models import ALL_CANONICAL_TABLE_NAMES
from backend.domain.enums import DecisionStatus

api_v1_router = APIRouter(prefix="/api/v1")


def _services(request: Request):
    return request.app.state


# -------------------- 1. Health, Overview, Backup & Workspace --------------------

@api_v1_router.get("/health")
async def health_check(request: Request) -> dict[str, Any]:
    """系统健康探针与架构基线信息。"""
    st = _services(request)
    projects = st.project_service.list_projects()
    pending_decisions = st.decision_service.list_decisions(status=DecisionStatus.PENDING.value, limit=50)
    return {
        "ok": True,
        "status": "healthy",
        "architecture_version": "1.3.0-clean-slate",
        "canonical_table_count": len(ALL_CANONICAL_TABLE_NAMES),
        "project_count": len(projects),
        "pending_decision_count": len(pending_decisions),
    }


@api_v1_router.get("/overview", dependencies=[Depends(verify_http_client)])
async def get_work_overview(request: Request) -> dict[str, Any]:
    """P01 工作总览聚合接口：返回活跃项目、全局待确认决策、活跃协同会话与最近事件流。"""
    st = _services(request)
    projects = st.project_service.list_projects()
    pending_decisions = st.decision_service.list_decisions(status=DecisionStatus.PENDING.value, limit=50)
    sessions = st.session_service.list_all_sessions(limit=25)
    recent_events = st.event_service.list_events(limit=30)
    providers = st.runtime_registry_service.list_providers()
    instances = st.runtime_registry_service.list_runtime_instances()

    project_Map = {p.id: p.name for p in projects}
    enriched_decisions = []
    for d in pending_decisions:
        item = serialize_decision(d)
        item["project_name"] = project_Map.get(d.project_id, "")
        enriched_decisions.append(item)

    enriched_sessions = []
    for s in sessions:
        item = serialize_session(s)
        item["project_name"] = project_Map.get(s.project_id, "")
        enriched_sessions.append(item)

    return {
        "ok": True,
        "counts": {
            "total_projects": len(projects),
            "active_projects": sum(1 for p in projects if p.status == "active"),
            "pending_decisions": len(pending_decisions),
            "active_sessions": sum(
                1 for s in sessions if s.platform_status in ("active", "waiting_interaction")
            ),
            "runtime_instances": len(instances),
        },
        "projects": [serialize_project(p) for p in projects],
        "pending_decisions": enriched_decisions,
        "sessions": enriched_sessions,
        "recent_events": [serialize_event(e) for e in recent_events],
        "runtime_providers": [serialize_provider(rp) for rp in providers],
    }


@api_v1_router.post("/system/backup", dependencies=[Depends(verify_http_client)])
async def trigger_online_backup(request: Request, label: str = Query(default="manual")) -> dict[str, Any]:
    """执行 SQLite 在线热备份并校验完整性。"""
    st = _services(request)
    db_path = st.settings.resolved_db_path
    backup_path = create_online_sqlite_backup(db_path, label=label)
    return {
        "ok": True,
        "backup_path": str(backup_path),
        "filename": backup_path.name,
    }


@api_v1_router.get("/workspace/doc", dependencies=[Depends(verify_http_client)])
async def read_workspace_document(
    request: Request,
    path: str = Query(..., description="目标文件绝对路径"),
    project_id: Optional[str] = Query(default=None, description="可选限定所属项目 ID"),
) -> dict[str, Any]:
    """受控工作区文档读取（强制校验已注册 Project.root_path 白名单与防 symlink/junction 逃逸）。"""
    st = _services(request)
    if project_id:
        proj = st.project_service.get_project(project_id)
        allowed_roots = [proj.root_path]
    else:
        allowed_roots = [p.root_path for p in st.project_service.list_projects()]

    try:
        return WorkspaceDocumentReader.read_document(path, allowed_roots=allowed_roots)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_v1_router.get("/workspace/git", dependencies=[Depends(verify_http_client)])
async def inspect_project_git(
    request: Request,
    project_id: str = Query(..., description="已注册项目 ID"),
    file_path: Optional[str] = Query(default=None, description="可选查看单文件 diff"),
) -> dict[str, Any]:
    """只读查询已注册项目的 Git 分支、变更文件与 Diff。"""
    st = _services(request)
    proj = st.project_service.get_project(project_id)
    status_info = get_git_workspace_status(proj.root_path)
    diff_info = get_git_workspace_diff(proj.root_path, file_path=file_path) if file_path else None
    return {
        "ok": True,
        "project_id": proj.id,
        "root_path": proj.root_path,
        "git_status": status_info,
        "git_diff": diff_info,
    }


# -------------------- 2. Auth & Web Push --------------------

@api_v1_router.get("/auth/status")
async def get_auth_status(request: Request) -> dict[str, Any]:
    """查询当前客户端鉴权状态、虚拟专网信息与本机扫码配对码。"""
    st = _services(request)
    auth_mgr = st.auth_mgr
    client_host = request.client.host if request.client else "127.0.0.1"
    is_local = client_host in ("127.0.0.1", "::1", "localhost", "testclient")

    token = extract_bearer_token(request)
    token_valid = auth_mgr.verify_token(token, client_ip=client_host)
    vpn_paired = auth_mgr.is_paired_trusted_vpn_ip(client_host)

    auto_issued_token: Optional[str] = None
    if not is_local and not token_valid and vpn_paired:
        auto_issued_token = auth_mgr.issue_token_for_trusted_vpn_ip(client_host)
        if auto_issued_token:
            token_valid = True

    ts = getattr(st, "ts_status", None) or detect_tailscale_status()
    port = st.settings.port

    preferred_ip = ts.tailscale_ip or ts.zerotier_ip or ts.lan_ip or "127.0.0.1"
    pair_code = auth_mgr.bootstrap_pair_code if is_local else ""
    pair_url = f"http://{preferred_ip}:{port}/?pair_code={pair_code}" if pair_code else ""

    return {
        "ok": True,
        "authenticated": bool(is_local or token_valid or vpn_paired),
        "is_localhost": is_local,
        "client_ip": client_host,
        "is_trusted_vpn": is_trusted_vpn_network(client_host),
        "auto_issued_token": auto_issued_token,
        "network": {
            "tailscale_ip": ts.tailscale_ip,
            "tailscale_hostname": ts.hostname,
            "zerotier_ip": ts.zerotier_ip,
            "lan_ip": ts.lan_ip,
            "preferred_ip": preferred_ip,
            "port": port,
        },
        "pairing": {
            "pair_code": pair_code,
            "pair_url": pair_url,
            "expires_at": auth_mgr.bootstrap_pair_code_expires_at if is_local else 0,
        },
        "devices": auth_mgr.list_devices() if (is_local or token_valid) else [],
    }


@api_v1_router.post("/auth/pair")
async def exchange_pair_code(request: Request, body: PairExchangeRequest) -> dict[str, Any]:
    """使用 60 秒一次性配对码置换长期设备 Token。"""
    st = _services(request)
    client_host = request.client.host if request.client else ""
    result = st.auth_mgr.exchange_pair_code(
        pair_code=body.pair_code.strip(),
        device_name=body.device_name.strip() or "Mobile Browser",
        client_ip=client_host,
    )
    if not result:
        raise HTTPException(status_code=401, detail="配对码无效或已过期，请在电脑端刷新二维码重试")
    return {"ok": True, **result}


@api_v1_router.post("/auth/pair-code/refresh", dependencies=[Depends(verify_http_client)])
async def refresh_pair_code(request: Request) -> dict[str, Any]:
    """主动滚动刷新一次性配对码。"""
    st = _services(request)
    code, exp = st.auth_mgr.refresh_pair_code()
    return {"ok": True, "pair_code": code, "expires_at": exp}


@api_v1_router.delete("/auth/devices/{device_id}", dependencies=[Depends(verify_http_client)])
async def revoke_paired_device(request: Request, device_id: str) -> dict[str, Any]:
    """吊销已配对设备。"""
    st = _services(request)
    ok = st.auth_mgr.revoke_device(device_id)
    if not ok:
        raise HTTPException(status_code=404, detail="未找到指定设备记录")
    return {"ok": True, "device_id": device_id}


@api_v1_router.get("/push/vapid-public-key")
async def get_vapid_public_key(request: Request) -> dict[str, Any]:
    st = _services(request)
    return {"ok": True, "public_key": st.push_service.get_public_key()}


@api_v1_router.post("/push/subscribe", dependencies=[Depends(verify_http_client)])
async def subscribe_web_push(request: Request, body: PushSubscribeRequest) -> dict[str, Any]:
    st = _services(request)
    res = st.push_service.subscribe(
        device_id=body.device_id,
        subscription_info=body.subscription,
        user_agent=body.user_agent or request.headers.get("User-Agent", ""),
    )
    return res


# -------------------- 3. Projects & MCP Tokens --------------------

@api_v1_router.get("/projects", dependencies=[Depends(verify_http_client)])
async def list_projects(
    request: Request,
    status: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    st = _services(request)
    svc: ProjectService = st.project_service
    projects = svc.list_projects(status=status)

    # 附带每个项目的任务统计与待决策数，支撑 P04 项目列表卡片展示
    items = []
    for p in projects:
        tasks = st.planning_service.list_project_tasks(p.id)
        pending_decs = st.decision_service.list_decisions(
            project_id=p.id, status=DecisionStatus.PENDING.value
        )
        sessions = st.session_service.list_project_sessions(p.id)
        data = serialize_project(p)
        data["stats"] = {
            "total_tasks": len(tasks),
            "completed_tasks": sum(1 for t in tasks if t.platform_status == "completed"),
            "in_progress_tasks": sum(
                1 for t in tasks if t.platform_status in ("in_progress", "waiting_interaction")
            ),
            "needs_revalidation_tasks": sum(1 for t in tasks if t.needs_revalidation),
            "pending_decisions": len(pending_decs),
            "session_count": len(sessions),
        }
        items.append(data)

    return {"ok": True, "projects": items}


@api_v1_router.post("/projects", dependencies=[Depends(verify_http_client)], status_code=201)
async def create_project(request: Request, body: ProjectCreateRequest) -> dict[str, Any]:
    st = _services(request)
    proj = st.project_service.create_project(
        name=body.name,
        root_path=body.root_path,
        slug=body.slug,
        description=body.description,
        git_remote=body.git_remote,
        summary_next_action=body.summary_next_action,
    )
    return {"ok": True, "project": serialize_project(proj)}


@api_v1_router.get("/projects/{project_id}", dependencies=[Depends(verify_http_client)])
async def get_project_detail(request: Request, project_id: str) -> dict[str, Any]:
    """P06 项目概览聚合详情：包含项目信息、任务列表、步骤、依赖边、协同会话、待确认决策与最新事件。"""
    st = _services(request)
    proj = st.project_service.get_project(project_id)
    tasks = st.planning_service.list_project_tasks(project_id)
    steps = st.planning_service.list_project_steps(project_id)
    deps = st.planning_service.list_project_dependencies(project_id)
    sessions = st.session_service.list_project_sessions(project_id)
    decisions = st.decision_service.list_decisions(project_id=project_id, limit=50)
    artifacts = st.artifact_service.list_artifacts(project_id=project_id, limit=50)
    events = st.event_service.list_events(project_id=project_id, limit=40)
    tokens = st.project_service.list_mcp_tokens(project_id)

    return {
        "ok": True,
        "project": serialize_project(proj),
        "tasks": [serialize_task(t) for t in tasks],
        "steps": [serialize_step(s) for s in steps],
        "dependencies": [serialize_dependency(d) for d in deps],
        "sessions": [serialize_session(s) for s in sessions],
        "decisions": [serialize_decision(d) for d in decisions],
        "artifacts": [serialize_artifact(a) for a in artifacts],
        "events": [serialize_event(e) for e in events],
        "mcp_tokens": [serialize_mcp_token(t) for t in tokens],
    }


@api_v1_router.patch("/projects/{project_id}", dependencies=[Depends(verify_http_client)])
async def update_project(
    request: Request, project_id: str, body: ProjectUpdateRequest
) -> dict[str, Any]:
    st = _services(request)
    proj = st.project_service.update_project(
        project_id,
        expected_version=body.expected_version,
        name=body.name,
        description=body.description,
        status=body.status,
        summary_next_action=body.summary_next_action,
    )
    return {"ok": True, "project": serialize_project(proj)}


@api_v1_router.get("/projects/{project_id}/mcp-tokens", dependencies=[Depends(verify_http_client)])
async def list_project_mcp_tokens(request: Request, project_id: str) -> dict[str, Any]:
    st = _services(request)
    st.project_service.get_project(project_id)
    tokens = st.project_service.list_mcp_tokens(project_id)
    return {"ok": True, "mcp_tokens": [serialize_mcp_token(t) for t in tokens]}


@api_v1_router.post(
    "/projects/{project_id}/mcp-tokens",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def issue_project_mcp_token(
    request: Request, project_id: str, body: McpTokenIssueRequest
) -> dict[str, Any]:
    st = _services(request)
    issued = st.project_service.issue_mcp_token(
        project_id=project_id,
        token_name=body.token_name,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    return {
        "ok": True,
        "token": {
            "token_id": issued.token_id,
            "project_id": issued.project_id,
            "token_name": issued.token_name,
            "token_prefix": issued.token_prefix,
            "raw_token": issued.raw_token,
            "scopes": issued.scopes,
            "expires_at": issued.expires_at,
            "created_at": issued.created_at,
        },
    }


@api_v1_router.delete(
    "/projects/{project_id}/mcp-tokens/{token_id}",
    dependencies=[Depends(verify_http_client)],
)
async def revoke_project_mcp_token(
    request: Request, project_id: str, token_id: str
) -> dict[str, Any]:
    st = _services(request)
    st.project_service.revoke_mcp_token(project_id=project_id, token_id=token_id)
    return {"ok": True, "token_id": token_id}


# -------------------- 4. Tasks, Steps, Dependencies & Revisions --------------------

@api_v1_router.get("/projects/{project_id}/tasks", dependencies=[Depends(verify_http_client)])
async def list_project_tasks(request: Request, project_id: str) -> dict[str, Any]:
    st = _services(request)
    st.project_service.get_project(project_id)
    tasks = st.planning_service.list_project_tasks(project_id)
    steps = st.planning_service.list_project_steps(project_id)
    deps = st.planning_service.list_project_dependencies(project_id)
    return {
        "ok": True,
        "tasks": [serialize_task(t) for t in tasks],
        "steps": [serialize_step(s) for s in steps],
        "dependencies": [serialize_dependency(d) for d in deps],
    }


@api_v1_router.post(
    "/projects/{project_id}/tasks",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def create_task(
    request: Request, project_id: str, body: TaskCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    task = st.planning_service.create_task(
        project_id=project_id,
        title=body.title,
        goal=body.goal,
        acceptance_criteria=body.acceptance_criteria,
        parent_task_id=body.parent_task_id,
        priority=body.priority,
        sort_order=body.sort_order,
    )
    return {"ok": True, "task": serialize_task(task)}


@api_v1_router.get(
    "/projects/{project_id}/tasks/{task_id}",
    dependencies=[Depends(verify_http_client)],
)
async def get_task_detail(
    request: Request, project_id: str, task_id: str
) -> dict[str, Any]:
    """P08 任务详情与修订页聚合接口：返回任务、步骤及各步骤修订历史、任务修订历史、关联决策与产物。"""
    st = _services(request)
    plan_svc: PlanningService = st.planning_service
    task = plan_svc.get_task(project_id, task_id)
    revisions = plan_svc.list_task_revisions(project_id, task_id)
    steps = plan_svc.list_task_steps(project_id, task_id)
    step_revisions_map: dict[str, list[dict[str, Any]]] = {}
    for s in steps:
        s_revs = plan_svc.list_step_revisions(project_id, s.id)
        step_revisions_map[s.id] = [serialize_step_revision(r) for r in s_revs]

    deps = [
        serialize_dependency(d)
        for d in plan_svc.list_project_dependencies(project_id)
        if d.task_id == task_id or d.depends_on_task_id == task_id
    ]
    decisions = st.decision_service.list_decisions(project_id=project_id, task_id=task_id)
    artifacts = st.artifact_service.list_artifacts(project_id=project_id, task_id=task_id)
    events = st.event_service.list_events(project_id=project_id, entity_type="task", entity_id=task_id, limit=30)

    return {
        "ok": True,
        "task": serialize_task(task),
        "revisions": [serialize_task_revision(r) for r in revisions],
        "steps": [serialize_step(s) for s in steps],
        "step_revisions": step_revisions_map,
        "dependencies": deps,
        "decisions": [serialize_decision(d) for d in decisions],
        "artifacts": [serialize_artifact(a) for a in artifacts],
        "events": [serialize_event(e) for e in events],
    }


@api_v1_router.post(
    "/projects/{project_id}/tasks/{task_id}/revisions",
    dependencies=[Depends(verify_http_client)],
)
async def revise_task(
    request: Request, project_id: str, task_id: str, body: TaskReviseRequest
) -> dict[str, Any]:
    st = _services(request)
    task, rev, impact = st.planning_service.revise_task(
        project_id=project_id,
        task_id=task_id,
        title=body.title,
        goal=body.goal,
        acceptance_criteria=body.acceptance_criteria,
        change_reason=body.change_reason,
        expected_version=body.expected_version,
    )
    return {
        "ok": True,
        "task": serialize_task(task),
        "revision": serialize_task_revision(rev),
        "revalidation_impact": {
            "impacted_step_ids": impact.impacted_step_ids,
            "impacted_task_ids": impact.impacted_task_ids,
            "reason": impact.reason,
        },
    }


@api_v1_router.patch(
    "/projects/{project_id}/tasks/{task_id}/status",
    dependencies=[Depends(verify_http_client)],
)
async def update_task_status(
    request: Request, project_id: str, task_id: str, body: TaskStatusUpdateRequest
) -> dict[str, Any]:
    st = _services(request)
    task = st.planning_service.update_task_status(
        project_id=project_id,
        task_id=task_id,
        platform_status=body.platform_status,
        agent_reported_status=body.agent_reported_status,
        clear_revalidation=body.clear_revalidation,
    )
    return {"ok": True, "task": serialize_task(task)}


@api_v1_router.post(
    "/projects/{project_id}/tasks/{task_id}/dependencies",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def add_task_dependency(
    request: Request, project_id: str, task_id: str, body: TaskDependencyCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    dep = st.planning_service.add_task_dependency(
        project_id=project_id,
        task_id=task_id,
        depends_on_task_id=body.depends_on_task_id,
        dependency_kind=body.dependency_kind,
    )
    return {"ok": True, "dependency": serialize_dependency(dep)}


@api_v1_router.get(
    "/projects/{project_id}/dependencies",
    dependencies=[Depends(verify_http_client)],
)
async def list_project_dependencies(request: Request, project_id: str) -> dict[str, Any]:
    st = _services(request)
    deps = st.planning_service.list_project_dependencies(project_id)
    return {"ok": True, "dependencies": [serialize_dependency(d) for d in deps]}


@api_v1_router.post(
    "/projects/{project_id}/tasks/{task_id}/steps",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def create_step(
    request: Request, project_id: str, task_id: str, body: StepCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    step = st.planning_service.create_step(
        project_id=project_id,
        task_id=task_id,
        title=body.title,
        instruction=body.instruction,
        seq_order=body.seq_order,
    )
    return {"ok": True, "step": serialize_step(step)}


@api_v1_router.post(
    "/projects/{project_id}/steps/{step_id}/revisions",
    dependencies=[Depends(verify_http_client)],
)
async def revise_step(
    request: Request, project_id: str, step_id: str, body: StepReviseRequest
) -> dict[str, Any]:
    st = _services(request)
    step, rev, impact = st.planning_service.revise_step(
        project_id=project_id,
        step_id=step_id,
        title=body.title,
        instruction=body.instruction,
        change_reason=body.change_reason,
        expected_version=body.expected_version,
    )
    return {
        "ok": True,
        "step": serialize_step(step),
        "revision": serialize_step_revision(rev),
        "revalidation_impact": {
            "impacted_step_ids": impact.impacted_step_ids,
            "impacted_task_ids": impact.impacted_task_ids,
            "reason": impact.reason,
        },
    }


@api_v1_router.patch(
    "/projects/{project_id}/steps/{step_id}/status",
    dependencies=[Depends(verify_http_client)],
)
async def update_step_status(
    request: Request, project_id: str, step_id: str, body: StepStatusUpdateRequest
) -> dict[str, Any]:
    st = _services(request)
    step = st.planning_service.update_step_status(
        project_id=project_id,
        step_id=step_id,
        platform_status=body.platform_status,
        agent_reported_status=body.agent_reported_status,
        clear_revalidation=body.clear_revalidation,
    )
    return {"ok": True, "step": serialize_step(step)}


# -------------------- 5. Runtime Registry & Machines --------------------

@api_v1_router.get("/runtimes/providers", dependencies=[Depends(verify_http_client)])
async def list_runtime_providers(request: Request) -> dict[str, Any]:
    st = _services(request)
    rt_svc: RuntimeRegistryService = st.runtime_registry_service
    providers = rt_svc.list_providers()
    return {"ok": True, "providers": [serialize_provider(p) for p in providers]}


@api_v1_router.get("/machines", dependencies=[Depends(verify_http_client)])
async def list_machines(request: Request) -> dict[str, Any]:
    st = _services(request)
    machines = st.runtime_registry_service.list_machines()
    return {"ok": True, "machines": [serialize_machine(m) for m in machines]}


@api_v1_router.post("/machines", dependencies=[Depends(verify_http_client)], status_code=201)
async def register_machine(
    request: Request, body: MachineRegisterRequest
) -> dict[str, Any]:
    st = _services(request)
    m = st.runtime_registry_service.register_machine(
        display_name=body.display_name,
        os_type=body.os_type,
        hostname_alias=body.hostname_alias,
        connection_kind=body.connection_kind,
        health_status=body.health_status,
    )
    return {"ok": True, "machine": serialize_machine(m)}


@api_v1_router.get("/runtimes/instances", dependencies=[Depends(verify_http_client)])
async def list_runtime_instances(request: Request) -> dict[str, Any]:
    st = _services(request)
    instances = st.runtime_registry_service.list_runtime_instances()
    providers = {p.id: serialize_provider(p) for p in st.runtime_registry_service.list_providers()}
    machines = {m.id: serialize_machine(m) for m in st.runtime_registry_service.list_machines()}
    items = []
    for inst in instances:
        data = serialize_runtime_instance(inst)
        data["provider"] = providers.get(inst.provider_id)
        data["machine"] = machines.get(inst.machine_id)
        items.append(data)
    return {"ok": True, "instances": items}


@api_v1_router.post(
    "/runtimes/instances",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def register_runtime_instance(
    request: Request, body: RuntimeInstanceRegisterRequest
) -> dict[str, Any]:
    st = _services(request)
    inst = st.runtime_registry_service.register_runtime_instance(
        provider_key=body.provider_key,
        machine_id=body.machine_id,
        display_name=body.display_name,
        runtime_version=body.runtime_version,
        connector_version=body.connector_version,
        control_level=body.control_level,
        health_status=body.health_status,
        capabilities=body.capabilities,
        model_endpoint_descriptor=body.model_endpoint_descriptor,
    )
    return {"ok": True, "instance": serialize_runtime_instance(inst)}


# -------------------- 6. Sessions & External Session Refs (P09/P10) --------------------

@api_v1_router.get("/sessions", dependencies=[Depends(verify_http_client)])
async def list_sessions(
    request: Request,
    project_id: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    st = _services(request)
    sess_svc: SessionService = st.session_service
    if project_id:
        sessions = sess_svc.list_project_sessions(project_id)
    else:
        sessions = sess_svc.list_all_sessions(limit=100)

    projects = {p.id: p.name for p in st.project_service.list_projects()}
    items = []
    for s in sessions:
        data = serialize_session(s)
        data["project_name"] = projects.get(s.project_id, "")
        refs = sess_svc.list_external_session_refs(s.id)
        data["external_refs"] = [serialize_external_ref(r) for r in refs]
        items.append(data)
    return {"ok": True, "sessions": items}


@api_v1_router.post(
    "/projects/{project_id}/sessions",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def create_session(
    request: Request, project_id: str, body: SessionCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    sess = st.session_service.create_session(
        project_id=project_id,
        title=body.title,
        task_id=body.task_id,
        runtime_instance_id=body.runtime_instance_id,
        source_channel=body.source_channel,
    )
    return {"ok": True, "session": serialize_session(sess)}


@api_v1_router.get("/sessions/{session_id}", dependencies=[Depends(verify_http_client)])
async def get_session_detail(request: Request, session_id: str) -> dict[str, Any]:
    """
    P10 会话详情页（v1.3 收敛版）：
    仅返回编排上下文（Project/Task/Steps）、Runtime/Machine/ExternalSessionRef（官方 Remote 入口）、
    关联 Decision 与 Artifact、以及 MCP/系统回写进度时间线。不复刻 Agent 原生聊天界面。
    """
    st = _services(request)
    sess = st.session_service.get_session(session_id)
    proj = st.project_service.get_project(sess.project_id)
    task = (
        st.planning_service.get_task(sess.project_id, sess.task_id)
        if sess.task_id
        else None
    )
    steps = (
        st.planning_service.list_task_steps(sess.project_id, sess.task_id)
        if sess.task_id
        else []
    )
    refs = st.session_service.list_external_session_refs(sess.id)
    decisions = st.decision_service.list_decisions(
        project_id=sess.project_id, session_id=sess.id
    )
    artifacts = st.artifact_service.list_artifacts(
        project_id=sess.project_id, session_id=sess.id
    )
    events = st.event_service.list_events(project_id=sess.project_id, limit=40)

    runtime_info = None
    if sess.runtime_instance_id:
        for inst in st.runtime_registry_service.list_runtime_instances():
            if inst.id == sess.runtime_instance_id:
                runtime_info = serialize_runtime_instance(inst)
                break

    return {
        "ok": True,
        "session": serialize_session(sess),
        "project": serialize_project(proj),
        "task": serialize_task(task) if task else None,
        "steps": [serialize_step(s) for s in steps],
        "runtime_instance": runtime_info,
        "external_refs": [serialize_external_ref(r) for r in refs],
        "decisions": [serialize_decision(d) for d in decisions],
        "artifacts": [serialize_artifact(a) for a in artifacts],
        "timeline_events": [serialize_event(e) for e in events],
    }


@api_v1_router.patch(
    "/projects/{project_id}/sessions/{session_id}/status",
    dependencies=[Depends(verify_http_client)],
)
async def update_session_status(
    request: Request,
    project_id: str,
    session_id: str,
    body: SessionStatusUpdateRequest,
) -> dict[str, Any]:
    st = _services(request)
    sess = st.session_service.update_session_statuses(
        project_id=project_id,
        session_id=session_id,
        platform_status=body.platform_status,
        agent_reported_status=body.agent_reported_status,
        runtime_status=body.runtime_status,
        connection_status=body.connection_status,
        expected_status_seq=body.expected_status_seq,
    )
    return {"ok": True, "session": serialize_session(sess)}


@api_v1_router.post(
    "/projects/{project_id}/sessions/{session_id}/external-refs",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def bind_external_session_ref(
    request: Request,
    project_id: str,
    session_id: str,
    body: ExternalSessionRefBindRequest,
) -> dict[str, Any]:
    st = _services(request)
    ref = st.session_service.bind_external_session_ref(
        project_id=project_id,
        session_id=session_id,
        runtime_instance_id=body.runtime_instance_id,
        external_session_id=body.external_session_id,
        remote_entry_kind=body.remote_entry_kind,
        remote_entry_url=body.remote_entry_url,
        link_status=body.link_status,
    )
    return {"ok": True, "external_ref": serialize_external_ref(ref)}


# -------------------- 7. Executions & Attempts --------------------

@api_v1_router.get(
    "/projects/{project_id}/executions",
    dependencies=[Depends(verify_http_client)],
)
async def list_project_executions(request: Request, project_id: str) -> dict[str, Any]:
    st = _services(request)
    exec_svc: ExecutionService = st.execution_service
    executions = exec_svc.list_project_executions(project_id)
    return {"ok": True, "executions": [serialize_execution(ex) for ex in executions]}


@api_v1_router.post(
    "/projects/{project_id}/executions",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def create_execution(
    request: Request, project_id: str, body: ExecutionCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    execution, attempt = st.execution_service.create_execution(
        project_id=project_id,
        task_id=body.task_id,
        step_id=body.step_id,
        session_id=body.session_id,
        runtime_instance_id=body.runtime_instance_id,
        summary_note=body.summary_note,
        model_endpoint_descriptor=body.model_endpoint_descriptor,
    )
    return {
        "ok": True,
        "execution": serialize_execution(execution),
        "attempt": serialize_attempt(attempt),
    }


@api_v1_router.post(
    "/projects/{project_id}/executions/{execution_id}/attempts",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def start_execution_attempt(
    request: Request,
    project_id: str,
    execution_id: str,
    body: ExecutionAttemptStartRequest,
) -> dict[str, Any]:
    st = _services(request)
    attempt = st.execution_service.start_new_attempt(
        project_id=project_id,
        execution_id=execution_id,
        runtime_instance_id=body.runtime_instance_id,
        model_endpoint_descriptor=body.model_endpoint_descriptor,
    )
    return {"ok": True, "attempt": serialize_attempt(attempt)}


@api_v1_router.get(
    "/executions/{execution_id}/attempts",
    dependencies=[Depends(verify_http_client)],
)
async def list_execution_attempts(request: Request, execution_id: str) -> dict[str, Any]:
    st = _services(request)
    attempts = st.execution_service.list_execution_attempts(execution_id)
    return {"ok": True, "attempts": [serialize_attempt(at) for at in attempts]}


# -------------------- 8. Interactive Decisions (P02/P03 & Global Modal) --------------------

@api_v1_router.get("/decisions", dependencies=[Depends(verify_http_client)])
async def list_decisions(
    request: Request,
    project_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict[str, Any]:
    st = _services(request)
    dec_svc: DecisionService = st.decision_service
    decisions = dec_svc.list_decisions(
        project_id=project_id,
        session_id=session_id,
        task_id=task_id,
        status=status,
        limit=limit,
    )
    projects = {p.id: p.name for p in st.project_service.list_projects()}
    items = []
    for d in decisions:
        data = serialize_decision(d)
        data["project_name"] = projects.get(d.project_id, "")
        items.append(data)
    return {"ok": True, "decisions": items}


@api_v1_router.post(
    "/projects/{project_id}/decisions",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def create_decision(
    request: Request, project_id: str, body: DecisionCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    decision = st.decision_service.create_decision(
        project_id=project_id,
        title=body.title,
        context_summary=body.context_summary,
        decision_type=body.decision_type,
        risk_level=body.risk_level,
        options=body.options,
        recommended_option_id=body.recommended_option_id,
        allow_custom_input=body.allow_custom_input,
        timeout_policy=body.timeout_policy,
        timeout_sec=body.timeout_sec,
        task_id=body.task_id,
        step_id=body.step_id,
        session_id=body.session_id,
        execution_id=body.execution_id,
    )
    return {"ok": True, "decision": serialize_decision(decision)}


@api_v1_router.get("/decisions/{decision_id}", dependencies=[Depends(verify_http_client)])
async def get_decision_detail(request: Request, decision_id: str) -> dict[str, Any]:
    st = _services(request)
    decision = st.decision_service.get_decision(decision_id)
    proj = st.project_service.get_project(decision.project_id)
    data = serialize_decision(decision)
    data["project_name"] = proj.name
    return {"ok": True, "decision": data}


@api_v1_router.post(
    "/decisions/{decision_id}/resolve",
    dependencies=[Depends(verify_http_client)],
)
async def resolve_decision(
    request: Request,
    decision_id: str,
    body: DecisionResolveRequest,
    idempotency_key_header: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """
    拍板提交交互式决策答案：
    - 支持 `Idempotency-Key` 请求头或 `idempotency_key` 请求体字段；
    - 支持人工确认 (`user_manual`) 与合规的 45 秒超时自动推荐提交 (`timeout_auto_recommended`)；
    - 高风险决策严禁超时自动提交。
    """
    st = _services(request)
    effective_idem_key = (idempotency_key_header or body.idempotency_key or "").strip() or None
    decision, is_replay = st.decision_service.resolve_decision(
        decision_id=decision_id,
        chosen_option_id=body.chosen_option_id,
        custom_note=body.custom_note,
        resolved_by=body.resolved_by,
        idempotency_key=effective_idem_key,
    )
    return {
        "ok": True,
        "is_idempotent_replay": is_replay,
        "decision": serialize_decision(decision),
    }


# -------------------- 9. Artifacts & Events --------------------

@api_v1_router.get("/artifacts", dependencies=[Depends(verify_http_client)])
async def list_artifacts(
    request: Request,
    project_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict[str, Any]:
    st = _services(request)
    art_svc: ArtifactService = st.artifact_service
    artifacts = art_svc.list_artifacts(
        project_id=project_id,
        task_id=task_id,
        session_id=session_id,
        limit=limit,
    )
    return {"ok": True, "artifacts": [serialize_artifact(a) for a in artifacts]}


@api_v1_router.post(
    "/projects/{project_id}/artifacts",
    dependencies=[Depends(verify_http_client)],
    status_code=201,
)
async def create_artifact(
    request: Request, project_id: str, body: ArtifactCreateRequest
) -> dict[str, Any]:
    st = _services(request)
    artifact = st.artifact_service.create_artifact(
        project_id=project_id,
        title=body.title,
        artifact_type=body.artifact_type,
        summary_content=body.summary_content,
        relative_path=body.relative_path,
        metadata=body.metadata,
        task_id=body.task_id,
        session_id=body.session_id,
        execution_id=body.execution_id,
    )
    return {"ok": True, "artifact": serialize_artifact(artifact)}


@api_v1_router.get("/events", dependencies=[Depends(verify_http_client)])
async def list_events(
    request: Request,
    project_id: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
    after_seq_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    st = _services(request)
    evt_svc: EventService = st.event_service
    events = evt_svc.list_events(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        after_seq_id=after_seq_id,
        limit=limit,
    )
    return {"ok": True, "events": [serialize_event(e) for e in events]}
