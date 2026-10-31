# -*- coding: utf-8 -*-
"""
FastAPI 依赖注入、客户端鉴权与 ORM 实体序列化工具。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import HTTPException, Request

from backend.db.models.artifact import ArtifactModel
from backend.db.models.decision import DecisionModel
from backend.db.models.event import EventModel
from backend.db.models.execution import ExecutionAttemptModel, ExecutionModel
from backend.db.models.planning import (
    StepModel,
    StepRevisionModel,
    TaskDependencyModel,
    TaskModel,
    TaskRevisionModel,
)
from backend.db.models.project import McpProjectTokenModel, ProjectModel
from backend.db.models.runtime import (
    MachineModel,
    RuntimeInstanceModel,
    RuntimeProviderModel,
)
from backend.db.models.session import ExternalSessionRefModel, SessionModel
from backend.security.auth_guard import DevicePairingAuthManager


def extract_bearer_token(request: Request) -> Optional[str]:
    """从 Authorization Header、X-AgentDeck-Token 或 Cookie 提取设备访问令牌。"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    custom_token = request.headers.get("X-AgentDeck-Token", "")
    if custom_token:
        return custom_token.strip()
    cookie_token = request.cookies.get("agentdeck_token", "")
    if cookie_token:
        return cookie_token.strip()
    return None


def verify_http_client(request: Request) -> str:
    """
    校验 Web 管理端 HTTP 请求访问权限：
    1. 本机回环地址 (`127.0.0.1` / `::1` / `localhost` / TestClient) 默认免 Token 放行（仅限 Web UI，MCP 独立守卫不走此函数）；
    2. 远端设备需携带有效设备 Token 或来自已配对未吊销的 Tailscale/ZeroTier 虚拟专网 IP。
    """
    client_host = request.client.host if request.client else "127.0.0.1"
    if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return client_host

    auth_mgr: Optional[DevicePairingAuthManager] = getattr(request.app.state, "auth_mgr", None)
    if auth_mgr is None:
        return client_host

    token = extract_bearer_token(request)
    if auth_mgr.verify_token(token, client_ip=client_host):
        return client_host

    if auth_mgr.is_paired_trusted_vpn_ip(client_host):
        return client_host

    raise HTTPException(
        status_code=401,
        detail="未授权的访问请求，请在电脑终端扫描一次性配对二维码",
    )


# -------------------- ORM -> Dict 序列化函数 --------------------

def serialize_project(p: ProjectModel) -> dict[str, Any]:
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "description": p.description,
        "root_path": p.root_path,
        "git_remote": p.git_remote,
        "status": p.status,
        "summary_next_action": p.summary_next_action,
        "version": p.version,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def serialize_mcp_token(t: McpProjectTokenModel) -> dict[str, Any]:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "token_name": t.token_name,
        "token_prefix": t.token_prefix,
        "scopes": json.loads(t.scopes_json or "[]"),
        "is_revoked": bool(t.is_revoked),
        "expires_at": t.expires_at,
        "last_used_at": t.last_used_at,
        "created_at": t.created_at,
    }


def serialize_task(t: TaskModel) -> dict[str, Any]:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "parent_task_id": t.parent_task_id,
        "title": t.title,
        "goal": t.goal,
        "acceptance_criteria": json.loads(t.acceptance_criteria_json or "[]"),
        "platform_status": t.platform_status,
        "agent_reported_status": t.agent_reported_status,
        "priority": t.priority,
        "sort_order": t.sort_order,
        "active_revision_no": t.active_revision_no,
        "needs_revalidation": bool(t.needs_revalidation),
        "revalidation_reason": t.revalidation_reason,
        "version": t.version,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def serialize_task_revision(r: TaskRevisionModel) -> dict[str, Any]:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "task_id": r.task_id,
        "revision_no": r.revision_no,
        "title_snapshot": r.title_snapshot,
        "goal_snapshot": r.goal_snapshot,
        "acceptance_criteria": json.loads(r.acceptance_criteria_json or "[]"),
        "change_reason": r.change_reason,
        "changed_by": r.changed_by,
        "created_at": r.created_at,
    }


def serialize_dependency(d: TaskDependencyModel) -> dict[str, Any]:
    return {
        "id": d.id,
        "project_id": d.project_id,
        "task_id": d.task_id,
        "depends_on_task_id": d.depends_on_task_id,
        "dependency_kind": d.dependency_kind,
        "created_at": d.created_at,
    }


def serialize_step(s: StepModel) -> dict[str, Any]:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "task_id": s.task_id,
        "seq_order": s.seq_order,
        "title": s.title,
        "instruction": s.instruction,
        "platform_status": s.platform_status,
        "agent_reported_status": s.agent_reported_status,
        "active_revision_no": s.active_revision_no,
        "needs_revalidation": bool(s.needs_revalidation),
        "revalidation_reason": s.revalidation_reason,
        "version": s.version,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def serialize_step_revision(r: StepRevisionModel) -> dict[str, Any]:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "task_id": r.task_id,
        "step_id": r.step_id,
        "revision_no": r.revision_no,
        "title_snapshot": r.title_snapshot,
        "instruction_snapshot": r.instruction_snapshot,
        "change_reason": r.change_reason,
        "changed_by": r.changed_by,
        "created_at": r.created_at,
    }


def serialize_provider(p: RuntimeProviderModel) -> dict[str, Any]:
    return {
        "id": p.id,
        "provider_key": p.provider_key,
        "display_name": p.display_name,
        "integration_type": p.integration_type,
        "documentation_url": p.documentation_url,
        "default_capabilities": json.loads(p.default_capabilities_json or "[]"),
        "enabled": bool(p.enabled),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def serialize_machine(m: MachineModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "display_name": m.display_name,
        "os": m.os,
        "hostname_alias": m.hostname_alias,
        "connection_kind": m.connection_kind,
        "health_status": m.health_status,
        "last_seen_at": m.last_seen_at,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
    }


def serialize_runtime_instance(inst: RuntimeInstanceModel) -> dict[str, Any]:
    return {
        "id": inst.id,
        "provider_id": inst.provider_id,
        "machine_id": inst.machine_id,
        "display_name": inst.display_name,
        "runtime_version": inst.runtime_version,
        "connector_version": inst.connector_version,
        "control_level": inst.control_level,
        "health_status": inst.health_status,
        "connection_profile": json.loads(inst.connection_profile_json or "{}"),
        "capabilities": json.loads(inst.capability_manifest_json or "[]"),
        "model_endpoint_descriptor": (
            json.loads(inst.model_endpoint_descriptor_json)
            if inst.model_endpoint_descriptor_json
            else None
        ),
        "last_seen_at": inst.last_seen_at,
        "created_at": inst.created_at,
        "updated_at": inst.updated_at,
    }


def serialize_session(s: SessionModel) -> dict[str, Any]:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "task_id": s.task_id,
        "runtime_instance_id": s.runtime_instance_id,
        "title": s.title,
        "source_channel": s.source_channel,
        "platform_status": s.platform_status,
        "agent_reported_status": s.agent_reported_status,
        "runtime_status": s.runtime_status,
        "connection_status": s.connection_status,
        "status_seq": s.status_seq,
        "last_activity_at": s.last_activity_at,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def serialize_external_ref(ref: ExternalSessionRefModel) -> dict[str, Any]:
    return {
        "id": ref.id,
        "project_id": ref.project_id,
        "session_id": ref.session_id,
        "runtime_instance_id": ref.runtime_instance_id,
        "external_session_id": ref.external_session_id,
        "remote_entry_kind": ref.remote_entry_kind,
        "remote_entry_url": ref.remote_entry_url,
        "link_status": ref.link_status,
        "last_activity_at": ref.last_activity_at,
        "created_at": ref.created_at,
        "updated_at": ref.updated_at,
    }


def serialize_execution(ex: ExecutionModel) -> dict[str, Any]:
    return {
        "id": ex.id,
        "project_id": ex.project_id,
        "task_id": ex.task_id,
        "step_id": ex.step_id,
        "session_id": ex.session_id,
        "task_revision_no": ex.task_revision_no,
        "step_revision_no": ex.step_revision_no,
        "platform_status": ex.platform_status,
        "agent_reported_status": ex.agent_reported_status,
        "runtime_status": ex.runtime_status,
        "connection_status": ex.connection_status,
        "summary_note": ex.summary_note,
        "started_at": ex.started_at,
        "finished_at": ex.finished_at,
        "created_at": ex.created_at,
        "updated_at": ex.updated_at,
    }


def serialize_attempt(at: ExecutionAttemptModel) -> dict[str, Any]:
    return {
        "id": at.id,
        "project_id": at.project_id,
        "execution_id": at.execution_id,
        "attempt_no": at.attempt_no,
        "runtime_instance_id": at.runtime_instance_id,
        "status": at.status,
        "exit_code": at.exit_code,
        "error_summary": at.error_summary,
        "model_endpoint_descriptor": (
            json.loads(at.model_endpoint_descriptor_json)
            if at.model_endpoint_descriptor_json
            else None
        ),
        "started_at": at.started_at,
        "ended_at": at.ended_at,
    }


def serialize_decision(d: DecisionModel) -> dict[str, Any]:
    return {
        "id": d.id,
        "project_id": d.project_id,
        "task_id": d.task_id,
        "step_id": d.step_id,
        "session_id": d.session_id,
        "execution_id": d.execution_id,
        "decision_type": d.decision_type,
        "risk_level": d.risk_level,
        "title": d.title,
        "context_summary": d.context_summary,
        "options": json.loads(d.options_json or "[]"),
        "recommended_option_id": d.recommended_option_id,
        "allow_custom_input": bool(d.allow_custom_input),
        "timeout_policy": d.timeout_policy,
        "timeout_sec": d.timeout_sec,
        "status": d.status,
        "chosen_option_id": d.chosen_option_id,
        "custom_note": d.custom_note,
        "resolution_payload": json.loads(d.resolution_payload_json or "{}"),
        "resolved_by": d.resolved_by,
        "resolution_idempotency_key": d.resolution_idempotency_key,
        "version": d.version,
        "created_at": d.created_at,
        "resolved_at": d.resolved_at,
        "updated_at": d.updated_at,
    }


def serialize_artifact(a: ArtifactModel) -> dict[str, Any]:
    return {
        "id": a.id,
        "project_id": a.project_id,
        "task_id": a.task_id,
        "session_id": a.session_id,
        "execution_id": a.execution_id,
        "artifact_type": a.artifact_type,
        "title": a.title,
        "relative_path": a.relative_path,
        "summary_content": a.summary_content,
        "checksum_sha256": a.checksum_sha256,
        "source_task_revision_no": a.source_task_revision_no,
        "metadata": json.loads(a.metadata_json or "{}"),
        "created_by": a.created_by,
        "created_at": a.created_at,
    }


def serialize_event(e: EventModel) -> dict[str, Any]:
    return {
        "seq_id": e.seq_id,
        "event_id": e.event_id,
        "project_id": e.project_id,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "event_type": e.event_type,
        "actor_type": e.actor_type,
        "actor_id": e.actor_id,
        "correlation_id": e.correlation_id,
        "payload": json.loads(e.payload_json or "{}"),
        "created_at": e.created_at,
    }

