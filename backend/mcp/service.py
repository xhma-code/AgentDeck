# -*- coding: utf-8 -*-
"""
MCP 8 大核心协作工具业务编排服务（McpCollaborationService）。

核心原则（Phase 4 审阅定稿）：
1. 服务端从 VerifiedMcpPrincipal 强制绑定唯一 project_id，绝不信任 Agent 传入的 project_id。
2. 所有写操作通过 McpIdempotencyService 在单一 SqlAlchemyUnitOfWork 事务中原子完成：
   幂等占位 -> 领域实体更新 -> events 审计写入 -> 响应快照保存 -> COMMIT。
3. 严格执行核心不变量：
   - task_report_progress / step_update 汇报 completed 仅修改 agent_reported_status，绝不修改 platform_status；
   - step_update 强制校验 expected_revision_no 与 needs_revalidation；
   - task_get 认领执行多 Session 原子竞争保护；
   - checkpoint_request 对 risk_gate（任意风险等级）与 high/critical 风险强制 manual_only；
   - artifact_register 拦截 Windows 盘符、UNC、'..'、符号链接逃逸与超限 metadata。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.application.artifact_service import (
    MAX_ARTIFACT_METADATA_BYTES,
    MAX_ARTIFACT_SUMMARY_CHARS,
)
from backend.db.base import utc_now_iso
from backend.db.models.artifact import ArtifactModel
from backend.db.models.decision import DecisionModel
from backend.db.models.execution import ExecutionModel
from backend.db.models.planning import (
    StepModel,
    StepRevisionModel,
    TaskDependencyModel,
    TaskModel,
    TaskRevisionModel,
)
from backend.db.models.project import ProjectModel
from backend.db.models.session import SessionModel
from backend.db.uow import SqlAlchemyUnitOfWork, _sanitize_event_payload
from backend.domain.enums import (
    ActorType,
    DecisionRiskLevel,
    DecisionStatus,
    DecisionTimeoutPolicy,
    DecisionType,
    SessionPlatformStatus,
    TaskPlatformStatus,
    TaskPriority,
)
from backend.domain.exceptions import (
    CrossProjectReferenceError,
    EntityNotFoundError,
    InvariantViolationError,
    SecurityBoundaryError,
    VersionConflictError,
)
from backend.domain.services.decision_policy import (
    HIGH_RISK_LEVELS,
    validate_decision_creation_policy,
)
from backend.mcp.auth import TOOL_REQUIRED_SCOPES
from backend.mcp.idempotency import McpIdempotencyService
from backend.mcp.schemas import (
    ArtifactRegisterArgs,
    CheckpointRequestArgs,
    DecisionGetArgs,
    ProjectContextGetArgs,
    StepUpdateArgs,
    TaskGetArgs,
    TaskReportProgressArgs,
    TaskRevisionGetArgs,
)
from backend.security.mcp_token_guard import VerifiedMcpPrincipal
from backend.security.path_guard import validate_artifact_relative_path

PRIORITY_RANK: dict[str, int] = {
    TaskPriority.CRITICAL.value: 0,
    TaskPriority.HIGH.value: 1,
    TaskPriority.MEDIUM.value: 2,
    TaskPriority.LOW.value: 3,
}


class McpCollaborationService:
    """Agent -> AgentDeck MCP 协作业务编排服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._idempotency = McpIdempotencyService(session_factory)

    @staticmethod
    def _enforce_scope_and_project(
        principal: VerifiedMcpPrincipal,
        *,
        tool_name: str,
        claimed_project_id: Optional[str] = None,
        extra_scope: Optional[str] = None,
    ) -> str:
        """校验最小权限 Scope 与项目隔离，返回服务端受控的 project_id。"""
        req_scope = TOOL_REQUIRED_SCOPES.get(tool_name)
        scope_aliases: dict[str, set[str]] = {
            "context:read": {"context:read", "project:read"},
            "decision:write": {"decision:write", "checkpoint:request"},
        }
        if req_scope:
            acceptable = scope_aliases.get(req_scope, {req_scope})
            if not (acceptable & principal.scopes):
                raise SecurityBoundaryError(
                    problem="MCP 访问被拒绝（超出令牌最小权限范围）",
                    cause=f"工具 '{tool_name}' 需要权限 '{req_scope}'，当前令牌仅拥有 {sorted(principal.scopes)}",
                    fix=f"请使用授予了 '{req_scope}' 权限范围的项目 MCP Token。",
                    context={
                        "tool": tool_name,
                        "required_scope": req_scope,
                        "granted_scopes": sorted(principal.scopes),
                        "mcp_error_code": "MCP_SCOPE_FORBIDDEN",
                    },
                )
        if extra_scope and extra_scope not in principal.scopes:
            raise SecurityBoundaryError(
                problem="MCP 访问被拒绝（超出令牌最小权限范围）",
                cause=f"当前附加写操作需要权限 '{extra_scope}'，当前令牌仅拥有 {sorted(principal.scopes)}",
                fix=f"请使用授予了 '{extra_scope}' 权限范围的项目 MCP Token。",
                context={
                    "tool": tool_name,
                    "required_scope": extra_scope,
                    "granted_scopes": sorted(principal.scopes),
                    "mcp_error_code": "MCP_SCOPE_FORBIDDEN",
                },
            )

        clean_claimed = (claimed_project_id or "").strip()
        if clean_claimed and clean_claimed != principal.project_id:
            raise CrossProjectReferenceError(
                problem="拦截跨项目越权 MCP 访问",
                cause=f"令牌绑定项目 ({principal.project_id}) 与请求传入的 project_id ({clean_claimed}) 不一致",
                fix="服务端始终以 MCP Token 绑定的唯一项目为准，禁止跨项目访问。",
                context={
                    "bound_project_id": principal.project_id,
                    "claimed_project_id": clean_claimed,
                    "mcp_error_code": "MCP_CROSS_PROJECT_DENIED",
                },
            )
        return principal.project_id

    # ==================== 1. project_context_get ====================
    def project_context_get(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = ProjectContextGetArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="project_context_get",
            claimed_project_id=args.project_id,
        )

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="绑定项目不存在",
                    cause=f"未找到 project_id={project_id}",
                    fix="请确认项目未被删除。",
                )

            task_stmt = select(TaskModel).where(TaskModel.project_id == project_id)
            if not args.include_completed_tasks:
                task_stmt = task_stmt.where(
                    TaskModel.platform_status != TaskPlatformStatus.COMPLETED.value,
                    TaskModel.platform_status != TaskPlatformStatus.CANCELLED.value,
                )
            task_stmt = task_stmt.order_by(TaskModel.sort_order.asc(), TaskModel.created_at.asc()).limit(
                args.max_tasks
            )
            tasks = list(uow.session.execute(task_stmt).scalars().all())

            pending_decisions = list(
                uow.session.execute(
                    select(DecisionModel)
                    .where(
                        DecisionModel.project_id == project_id,
                        DecisionModel.status == DecisionStatus.PENDING.value,
                    )
                    .order_by(DecisionModel.created_at.asc())
                )
                .scalars()
                .all()
            )

            active_sessions = list(
                uow.session.execute(
                    select(SessionModel)
                    .where(
                        SessionModel.project_id == project_id,
                        SessionModel.platform_status.in_(
                            [
                                SessionPlatformStatus.ACTIVE.value,
                                SessionPlatformStatus.WAITING_INTERACTION.value,
                            ]
                        ),
                    )
                    .order_by(SessionModel.updated_at.desc())
                )
                .scalars()
                .all()
            )

            return (
                {
                    "project": {
                        "id": proj.id,
                        "slug": proj.slug,
                        "name": proj.name,
                        "description": proj.description,
                        "root_path": proj.root_path,
                        "status": proj.status,
                        "summary_next_action": proj.summary_next_action,
                        "version": proj.version,
                    },
                    "tasks": [
                        {
                            "id": t.id,
                            "parent_task_id": t.parent_task_id,
                            "title": t.title,
                            "goal": t.goal,
                            "platform_status": t.platform_status,
                            "agent_reported_status": t.agent_reported_status,
                            "priority": t.priority,
                            "active_revision_no": t.active_revision_no,
                            "needs_revalidation": bool(t.needs_revalidation),
                            "revalidation_reason": t.revalidation_reason,
                        }
                        for t in tasks
                    ],
                    "pending_decisions": [
                        {
                            "id": d.id,
                            "title": d.title,
                            "decision_type": d.decision_type,
                            "risk_level": d.risk_level,
                            "task_id": d.task_id,
                            "step_id": d.step_id,
                            "session_id": d.session_id,
                        }
                        for d in pending_decisions
                    ],
                    "active_sessions": [
                        {
                            "id": s.id,
                            "title": s.title,
                            "task_id": s.task_id,
                            "platform_status": s.platform_status,
                            "connection_status": s.connection_status,
                        }
                        for s in active_sessions
                    ],
                },
                False,
            )

    # ==================== 2. task_get ====================
    def task_get(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = TaskGetArgs.model_validate(raw_args)
        is_claiming = bool((args.claim_for_session_id or "").strip())
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="task_get",
            claimed_project_id=args.project_id,
            extra_scope="progress:write" if is_claiming else None,
        )

        if not is_claiming:
            with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                assert uow.session is not None
                task = self._select_or_get_task_in_uow(
                    uow, project_id, args.task_id, exclude_occupied=args.claim_next_ready
                )
                if task is None:
                    return {"task": None, "claimed": False, "claimed_by_session_id": None}, False
                return self._build_task_detail_payload(uow, project_id, task, claimed_session_id=None), False

        # 认领模式：若未显式传入 idempotency_key，按 (task_id or "auto", session_id) 生成确定性认领幂等键
        sess_id_clean = (args.claim_for_session_id or "").strip()
        idem_key = (args.idempotency_key or "").strip() or f"claim:{args.task_id or 'auto'}:{sess_id_clean}"

        def _claim_op(uow: SqlAlchemyUnitOfWork) -> tuple[str, str, dict[str, Any]]:
            assert uow.session is not None
            sess = uow.session.get(SessionModel, sess_id_clean)
            if sess is None:
                raise EntityNotFoundError(
                    problem="认领任务关联的会话不存在",
                    cause=f"未找到 session_id='{sess_id_clean}'",
                    fix="请先登记有效的项目协同会话。",
                )
            if sess.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目使能会话认领任务",
                    cause=f"会话 {sess_id_clean} 不属于当前项目 {project_id}",
                    fix="请使用归属同一项目的 Session ID。",
                )

            task = self._select_or_get_task_in_uow(
                uow,
                project_id,
                args.task_id,
                exclude_occupied=(args.claim_next_ready and not (args.task_id or "").strip()),
                claiming_session_id=sess.id,
            )
            if task is None:
                return (
                    "session",
                    sess.id,
                    {
                        "task": None,
                        "claimed": False,
                        "claimed_by_session_id": None,
                    },
                )

            if task.needs_revalidation:
                raise InvariantViolationError(
                    problem="该任务处于上游修订待人工重验状态，暂不允许认领执行",
                    cause=f"任务 '{task.title}' needs_revalidation=1 ({task.revalidation_reason})",
                    fix="请等待用户在平台确认重验通过后再认领该任务。",
                    context={"task_id": task.id, "mcp_error_code": "MCP_STATE_NOT_ALLOWED"},
                )

            # 检查是否已被同项目下另一个活跃/等待交互的 Session 抢占认领
            competing_sess = uow.session.execute(
                select(SessionModel).where(
                    SessionModel.project_id == project_id,
                    SessionModel.task_id == task.id,
                    SessionModel.id != sess.id,
                    SessionModel.platform_status.in_(
                        [
                            SessionPlatformStatus.ACTIVE.value,
                            SessionPlatformStatus.WAITING_INTERACTION.value,
                        ]
                    ),
                )
            ).scalar_one_or_none()
            if competing_sess is not None:
                raise VersionConflictError(
                    problem="该任务已被另一个活跃协同会话抢占认领",
                    cause=f"任务 '{task.id}' 当前已绑定至活跃会话 '{competing_sess.id}' ({competing_sess.title})",
                    fix="请选取其他待执行任务，或先结束原占用会话。",
                    context={
                        "task_id": task.id,
                        "occupied_by_session_id": competing_sess.id,
                        "mcp_error_code": "MCP_VERSION_CONFLICT",
                    },
                )

            now = utc_now_iso()
            # 原子条件更新防止高并发同时修改同一 Task（保持 platform_status 不被 Agent 擅自篡改，仅更新 agent_reported_status）
            cas_res = uow.session.execute(
                update(TaskModel)
                .where(
                    TaskModel.id == task.id,
                    TaskModel.project_id == project_id,
                    TaskModel.version == task.version,
                )
                .values(
                    agent_reported_status="in_progress",
                    version=task.version + 1,
                    updated_at=now,
                )
            )
            if cas_res.rowcount != 1:
                raise VersionConflictError(
                    problem="任务认领并发冲突，任务状态已被其他操作修改",
                    cause=f"任务 '{task.id}' 的版本号 {task.version} 已失效",
                    fix="请重新读取任务状态后再试。",
                    context={"task_id": task.id, "mcp_error_code": "MCP_VERSION_CONFLICT"},
                )

            uow.session.refresh(task)
            sess.task_id = task.id
            sess.last_heartbeat_at = now
            sess.updated_at = now

            uow.record_event(
                project_id=project_id,
                entity_type="task",
                entity_id=task.id,
                event_type="task.claimed_by_agent",
                actor_type=ActorType.MCP_AGENT,
                actor_id=principal.token_name,
                payload={
                    "task_id": task.id,
                    "session_id": sess.id,
                    "platform_status": task.platform_status,
                    "agent_reported_status": task.agent_reported_status,
                },
            )
            payload = self._build_task_detail_payload(uow, project_id, task, claimed_session_id=sess.id)
            return "task", task.id, payload

        return self._idempotency.execute_idempotent(
            project_id=project_id,
            tool_name="task_get",
            idempotency_key=idem_key,
            payload=raw_args,
            operation=_claim_op,
        )

    def _select_or_get_task_in_uow(
        self,
        uow: SqlAlchemyUnitOfWork,
        project_id: str,
        task_id: Optional[str],
        *,
        exclude_occupied: bool = False,
        claiming_session_id: Optional[str] = None,
    ) -> Optional[TaskModel]:
        assert uow.session is not None
        clean_tid = (task_id or "").strip()
        if clean_tid:
            task = uow.session.get(TaskModel, clean_tid)
            if task is None:
                raise EntityNotFoundError(
                    problem="目标任务不存在",
                    cause=f"未找到任务 ID: {clean_tid}",
                    fix="请核对 task_id 是否正确。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="拦截跨项目访问任务",
                    cause=f"任务 {clean_tid} 不归属于当前项目 {project_id}",
                    fix="每个项目令牌只能读取所属项目的任务。",
                )
            return task

        # 未指定 task_id 时：自动挑选该项目下前置依赖已满足、非待重验、优先级最高的可执行任务
        all_tasks = list(
            uow.session.execute(
                select(TaskModel).where(TaskModel.project_id == project_id)
            )
            .scalars()
            .all()
        )
        if not all_tasks:
            if exclude_occupied:
                return None
            raise EntityNotFoundError(
                problem="当前项目下尚无任何任务",
                cause=f"项目 {project_id} 的任务列表为空",
                fix="请先在 AgentDeck 中为该项目创建研发任务。",
            )

        occupied_task_ids: set[str] = set()
        if exclude_occupied:
            occ_stmt = select(SessionModel).where(
                SessionModel.project_id == project_id,
                SessionModel.task_id.is_not(None),
                SessionModel.platform_status.in_(
                    [
                        SessionPlatformStatus.ACTIVE.value,
                        SessionPlatformStatus.WAITING_INTERACTION.value,
                    ]
                ),
            )
            if claiming_session_id:
                occ_stmt = occ_stmt.where(SessionModel.id != claiming_session_id)
            for s_row in uow.session.execute(occ_stmt).scalars().all():
                if s_row.task_id:
                    occupied_task_ids.add(s_row.task_id)

        task_map = {t.id: t for t in all_tasks}
        deps = list(
            uow.session.execute(
                select(TaskDependencyModel).where(TaskDependencyModel.project_id == project_id)
            )
            .scalars()
            .all()
        )
        prereq_map: dict[str, list[str]] = {}
        for d in deps:
            prereq_map.setdefault(d.task_id, []).append(d.depends_on_task_id)

        actionable: list[TaskModel] = []
        for t in all_tasks:
            if t.id in occupied_task_ids:
                continue
            if t.platform_status not in (
                TaskPlatformStatus.READY.value,
                TaskPlatformStatus.IN_PROGRESS.value,
                TaskPlatformStatus.DRAFT.value,
            ):
                continue
            if t.needs_revalidation:
                continue
            prereqs = prereq_map.get(t.id, [])
            if any(
                task_map.get(pid) is None
                or task_map[pid].platform_status != TaskPlatformStatus.COMPLETED.value
                for pid in prereqs
            ):
                continue
            actionable.append(t)

        if not actionable:
            if exclude_occupied:
                return None
            raise EntityNotFoundError(
                problem="当前项目下没有前置条件已满足的可执行任务",
                cause="所有未完成任务均处于阻塞、待重验或前置依赖未完成状态",
                fix="请先完成前置依赖任务，或在平台人工解除阻塞/重验状态。",
            )

        actionable.sort(
            key=lambda x: (
                0 if x.platform_status == TaskPlatformStatus.IN_PROGRESS.value else 1,
                PRIORITY_RANK.get(x.priority, 9),
                x.sort_order,
                x.created_at,
            )
        )
        return actionable[0]

    @staticmethod
    def _build_task_detail_payload(
        uow: SqlAlchemyUnitOfWork,
        project_id: str,
        task: TaskModel,
        *,
        claimed_session_id: Optional[str],
    ) -> dict[str, Any]:
        assert uow.session is not None
        active_rev = uow.session.execute(
            select(TaskRevisionModel).where(
                TaskRevisionModel.project_id == project_id,
                TaskRevisionModel.task_id == task.id,
                TaskRevisionModel.revision_no == task.active_revision_no,
            )
        ).scalar_one_or_none()

        steps = list(
            uow.session.execute(
                select(StepModel)
                .where(StepModel.project_id == project_id, StepModel.task_id == task.id)
                .order_by(StepModel.seq_order.asc())
            )
            .scalars()
            .all()
        )

        deps = list(
            uow.session.execute(
                select(TaskDependencyModel).where(
                    TaskDependencyModel.project_id == project_id,
                    TaskDependencyModel.task_id == task.id,
                )
            )
            .scalars()
            .all()
        )

        return {
            "claimed": bool(claimed_session_id),
            "claimed_by_session_id": claimed_session_id,
            "task": {
                "id": task.id,
                "project_id": task.project_id,
                "parent_task_id": task.parent_task_id,
                "title": task.title,
                "goal": task.goal,
                "acceptance_criteria": json.loads(task.acceptance_criteria_json or "[]"),
                "platform_status": task.platform_status,
                "agent_reported_status": task.agent_reported_status,
                "priority": task.priority,
                "active_revision_no": task.active_revision_no,
                "needs_revalidation": bool(task.needs_revalidation),
                "revalidation_reason": task.revalidation_reason,
                "version": task.version,
                "claimed_session_id": claimed_session_id,
            },
            "active_revision": (
                {
                    "revision_no": active_rev.revision_no,
                    "title_snapshot": active_rev.title_snapshot,
                    "goal_snapshot": active_rev.goal_snapshot,
                    "acceptance_criteria": json.loads(active_rev.acceptance_criteria_json or "[]"),
                    "change_reason": active_rev.change_reason,
                    "created_at": active_rev.created_at,
                }
                if active_rev
                else None
            ),
            "steps": [
                {
                    "id": s.id,
                    "seq_order": s.seq_order,
                    "title": s.title,
                    "instruction": s.instruction,
                    "platform_status": s.platform_status,
                    "agent_reported_status": s.agent_reported_status,
                    "active_revision_no": s.active_revision_no,
                    "needs_revalidation": bool(s.needs_revalidation),
                    "revalidation_reason": s.revalidation_reason,
                    "version": s.version,
                }
                for s in steps
            ],
            "dependencies": [
                {
                    "id": d.id,
                    "depends_on_task_id": d.depends_on_task_id,
                    "dependency_kind": d.dependency_kind,
                }
                for d in deps
            ],
        }

    # ==================== 3. task_revision_get ====================
    def task_revision_get(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = TaskRevisionGetArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="task_revision_get",
            claimed_project_id=args.project_id,
        )

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            task = uow.session.get(TaskModel, args.task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="目标任务不存在",
                    cause=f"未找到 task_id={args.task_id}",
                    fix="请核对任务 ID。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="拦截跨项目读取任务修订历史",
                    cause=f"任务 {args.task_id} 不归属于当前项目 {project_id}",
                    fix="禁止跨项目访问任务修订记录。",
                )

            revs = list(
                uow.session.execute(
                    select(TaskRevisionModel)
                    .where(
                        TaskRevisionModel.project_id == project_id,
                        TaskRevisionModel.task_id == task.id,
                    )
                    .order_by(TaskRevisionModel.revision_no.asc())
                )
                .scalars()
                .all()
            )
            target_no = args.revision_no or task.active_revision_no
            selected = next((r for r in revs if r.revision_no == target_no), None)
            if selected is None:
                raise EntityNotFoundError(
                    problem="指定任务修订版本不存在",
                    cause=f"任务 {task.id} 未找到修订号 r{target_no}",
                    fix=f"当前最新修订号为 r{task.active_revision_no}。",
                )

            step_revs = list(
                uow.session.execute(
                    select(StepRevisionModel)
                    .where(
                        StepRevisionModel.project_id == project_id,
                        StepRevisionModel.task_id == task.id,
                    )
                    .order_by(StepRevisionModel.step_id.asc(), StepRevisionModel.revision_no.asc())
                )
                .scalars()
                .all()
            )
            rev_obj = {
                "id": selected.id,
                "revision_no": selected.revision_no,
                "title_snapshot": selected.title_snapshot,
                "goal_snapshot": selected.goal_snapshot,
                "acceptance_criteria": json.loads(
                    selected.acceptance_criteria_json or "[]"
                ),
                "change_reason": selected.change_reason,
                "changed_by": selected.changed_by,
                "created_at": selected.created_at,
            }
            return (
                {
                    "task_id": task.id,
                    "active_revision_no": task.active_revision_no,
                    "needs_revalidation": bool(task.needs_revalidation),
                    "revalidation_reason": task.revalidation_reason,
                    "revision": rev_obj,
                    "task_revision": rev_obj,
                    "step_revisions": [
                        {
                            "id": sr.id,
                            "step_id": sr.step_id,
                            "revision_no": sr.revision_no,
                            "title_snapshot": sr.title_snapshot,
                            "instruction_snapshot": sr.instruction_snapshot,
                            "change_reason": sr.change_reason,
                            "changed_by": sr.changed_by,
                            "created_at": sr.created_at,
                        }
                        for sr in step_revs
                    ],
                    "history_summary": [
                        {
                            "revision_no": r.revision_no,
                            "change_reason": r.change_reason,
                            "changed_by": r.changed_by,
                            "created_at": r.created_at,
                        }
                        for r in revs
                    ],
                },
                False,
            )

    # ==================== 4. task_report_progress ====================
    def task_report_progress(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = TaskReportProgressArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="task_report_progress",
            claimed_project_id=args.project_id,
        )

        def _report_op(uow: SqlAlchemyUnitOfWork) -> tuple[str, str, dict[str, Any]]:
            assert uow.session is not None
            task = uow.session.get(TaskModel, args.task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="目标任务不存在",
                    cause=f"未找到 task_id={args.task_id}",
                    fix="请核对任务 ID。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="拦截跨项目汇报任务进度",
                    cause=f"任务 {args.task_id} 不归属于当前项目 {project_id}",
                    fix="禁止跨项目汇报进度。",
                )

            now = utc_now_iso()
            if args.session_id:
                sess = uow.session.get(SessionModel, args.session_id)
                if sess is None or sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="进度汇报关联的会话不存在或不属于当前项目",
                        cause=f"session_id={args.session_id} 与项目 {project_id} 不匹配",
                        fix="请核对协同会话 ID。",
                    )
                sess.last_heartbeat_at = now
                sess.updated_at = now

            # 核心不变量 #7：只更新 agent_reported_status，绝不自动把 platform_status 改为 completed
            prev_platform_status = task.platform_status
            task.agent_reported_status = args.agent_reported_status
            task.version += 1
            task.updated_at = now

            evt = uow.record_event(
                project_id=project_id,
                entity_type="task",
                entity_id=task.id,
                event_type="task.progress_reported",
                actor_type=ActorType.MCP_AGENT,
                actor_id=principal.token_name,
                payload={
                    "task_id": task.id,
                    "session_id": args.session_id,
                    "agent_reported_status": task.agent_reported_status,
                    "platform_status": task.platform_status,
                    "summary": args.summary.strip(),
                    "percent_complete": args.percent_complete,
                },
            )
            return (
                "task",
                task.id,
                {
                    "task_id": task.id,
                    "platform_status": task.platform_status,
                    "agent_reported_status": task.agent_reported_status,
                    "platform_status_unchanged": (task.platform_status == prev_platform_status),
                    "version": task.version,
                    "event_id": evt.event_id,
                    "task": {
                        "id": task.id,
                        "project_id": task.project_id,
                        "title": task.title,
                        "platform_status": task.platform_status,
                        "agent_reported_status": task.agent_reported_status,
                        "version": task.version,
                    },
                    "note": "Agent 进度汇报已记录；平台验收状态 (platform_status) 保持由人工控制。",
                },
            )

        return self._idempotency.execute_idempotent(
            project_id=project_id,
            tool_name="task_report_progress",
            idempotency_key=args.idempotency_key,
            payload=raw_args,
            operation=_report_op,
        )

    # ==================== 5. step_update ====================
    def step_update(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = StepUpdateArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="step_update",
            claimed_project_id=args.project_id,
        )

        def _step_op(uow: SqlAlchemyUnitOfWork) -> tuple[str, str, dict[str, Any]]:
            assert uow.session is not None
            step = uow.session.get(StepModel, args.step_id)
            if step is None:
                raise EntityNotFoundError(
                    problem="目标步骤不存在",
                    cause=f"未找到 step_id={args.step_id}",
                    fix="请核对步骤 ID。",
                )
            if step.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="拦截跨项目更新步骤状态",
                    cause=f"步骤 {args.step_id} 不归属于当前项目 {project_id}",
                    fix="禁止跨项目修改步骤。",
                )

            if step.active_revision_no != args.expected_revision_no:
                raise VersionConflictError(
                    problem="步骤指令已被用户修订（修订版本号冲突）",
                    cause=(
                        f"Agent 期望的修订号 expected_revision_no={args.expected_revision_no}，"
                        f"但当前步骤最新修订号为 active_revision_no={step.active_revision_no}"
                    ),
                    fix="请先调用 task_get 重新读取最新步骤修订指令后再执行。",
                    context={
                        "step_id": step.id,
                        "expected_revision_no": args.expected_revision_no,
                        "current_revision_no": step.active_revision_no,
                        "mcp_error_code": "MCP_VERSION_CONFLICT",
                    },
                )

            if args.expected_version is not None and step.version != args.expected_version:
                raise VersionConflictError(
                    problem="步骤状态版本冲突",
                    cause=f"期望版本 expected_version={args.expected_version}，当前版本为 {step.version}",
                    fix="请重新读取最新步骤状态后重试。",
                    context={
                        "step_id": step.id,
                        "expected_version": args.expected_version,
                        "current_version": step.version,
                        "mcp_error_code": "MCP_VERSION_CONFLICT",
                    },
                )

            if step.needs_revalidation:
                raise VersionConflictError(
                    problem="该步骤受上游修订影响处于待人工重验状态，禁止直接更新",
                    cause=f"step.needs_revalidation=1 ({step.revalidation_reason})",
                    fix="请先由用户在平台确认重验或调用 task_revision_get 检查上游变更。",
                    context={"step_id": step.id, "mcp_error_code": "MCP_VERSION_CONFLICT"},
                )

            now = utc_now_iso()
            if args.session_id:
                sess = uow.session.get(SessionModel, args.session_id)
                if sess is None or sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="步骤更新关联的会话不存在或不属于当前项目",
                        cause=f"session_id={args.session_id} 与项目 {project_id} 不匹配",
                        fix="请核对会话 ID。",
                    )
                sess.last_heartbeat_at = now
                sess.updated_at = now

            step.agent_reported_status = args.agent_reported_status
            step.version += 1
            step.updated_at = now

            evt = uow.record_event(
                project_id=project_id,
                entity_type="step",
                entity_id=step.id,
                event_type="step.agent_updated",
                actor_type=ActorType.MCP_AGENT,
                actor_id=principal.token_name,
                payload={
                    "step_id": step.id,
                    "task_id": step.task_id,
                    "session_id": args.session_id,
                    "agent_reported_status": step.agent_reported_status,
                    "platform_status": step.platform_status,
                    "active_revision_no": step.active_revision_no,
                    "execution_note": args.execution_note.strip(),
                },
            )
            return (
                "step",
                step.id,
                {
                    "step_id": step.id,
                    "task_id": step.task_id,
                    "platform_status": step.platform_status,
                    "agent_reported_status": step.agent_reported_status,
                    "active_revision_no": step.active_revision_no,
                    "version": step.version,
                    "event_id": evt.event_id,
                    "step": {
                        "id": step.id,
                        "task_id": step.task_id,
                        "title": step.title,
                        "status": step.agent_reported_status,
                        "agent_reported_status": step.agent_reported_status,
                        "platform_status": step.platform_status,
                        "active_revision_no": step.active_revision_no,
                        "version": step.version,
                    },
                },
            )

        return self._idempotency.execute_idempotent(
            project_id=project_id,
            tool_name="step_update",
            idempotency_key=args.idempotency_key,
            payload=raw_args,
            operation=_step_op,
        )

    # ==================== 6. checkpoint_request ====================
    def checkpoint_request(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = CheckpointRequestArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="checkpoint_request",
            claimed_project_id=args.project_id,
        )

        # 补充安全要求 1：risk_gate（无论 risk_level 如何）以及 high/critical 风险一律强制 manual_only
        is_mandatory_manual = (
            args.decision_type == DecisionType.RISK_GATE
            or args.risk_level.value in HIGH_RISK_LEVELS
        )
        if args.timeout_policy is None:
            eff_policy = (
                DecisionTimeoutPolicy.MANUAL_ONLY
                if is_mandatory_manual
                else DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED
                if (args.recommended_option_id or any(o.is_recommended for o in args.options))
                else DecisionTimeoutPolicy.MANUAL_ONLY
            )
        else:
            eff_policy = args.timeout_policy

        resolved_rec_id = validate_decision_creation_policy(
            decision_type=args.decision_type,
            risk_level=args.risk_level,
            timeout_policy=eff_policy,
            timeout_sec=args.timeout_sec,
            options=args.options,
            recommended_option_id=args.recommended_option_id,
        )

        def _checkpoint_op(uow: SqlAlchemyUnitOfWork) -> tuple[str, str, dict[str, Any]]:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="创建决策关联的项目不存在",
                    cause=f"未找到 project_id={project_id}",
                    fix="请确认项目已创建。",
                )

            if args.task_id is not None:
                task = uow.session.get(TaskModel, args.task_id)
                if task is None or task.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="决策关联的任务不存在或不属于当前项目",
                        cause=f"task_id={args.task_id} 与 project_id={project_id} 不匹配",
                        fix="请核对任务与项目归属。",
                    )
                task.agent_reported_status = "blocked_by_decision"
                task.updated_at = utc_now_iso()

            if args.step_id is not None:
                step = uow.session.get(StepModel, args.step_id)
                if step is None or step.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="决策关联的步骤不存在或不属于当前项目",
                        cause=f"step_id={args.step_id} 与 project_id={project_id} 不匹配",
                        fix="请核对步骤与项目归属。",
                    )
                step.agent_reported_status = "blocked_by_decision"
                step.updated_at = utc_now_iso()

            if args.session_id is not None:
                sess = uow.session.get(SessionModel, args.session_id)
                if sess is None or sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="决策关联的会话不存在或不属于当前项目",
                        cause=f"session_id={args.session_id} 与 project_id={project_id} 不匹配",
                        fix="请核对会话与项目归属。",
                    )
                sess.platform_status = SessionPlatformStatus.WAITING_INTERACTION.value
                sess.last_heartbeat_at = utc_now_iso()
                sess.updated_at = utc_now_iso()

            now = utc_now_iso()
            dec_id = str(uuid.uuid4())
            options_json = json.dumps(
                [opt.model_dump() for opt in args.options], ensure_ascii=False
            )
            dec = DecisionModel(
                id=dec_id,
                project_id=project_id,
                task_id=args.task_id,
                step_id=args.step_id,
                session_id=args.session_id,
                execution_id=None,
                decision_type=args.decision_type.value,
                risk_level=args.risk_level.value,
                title=args.title.strip()[:240],
                context_summary=args.context_summary.strip(),
                options_json=options_json,
                recommended_option_id=resolved_rec_id,
                allow_custom_input=1 if args.allow_custom_input else 0,
                timeout_policy=eff_policy.value,
                timeout_sec=args.timeout_sec,
                status=DecisionStatus.PENDING.value,
                chosen_option_id=None,
                custom_note="",
                resolved_by=None,
                version=1,
                created_at=now,
                resolved_at=None,
                updated_at=now,
            )
            uow.session.add(dec)
            uow.record_event(
                project_id=project_id,
                entity_type="decision",
                entity_id=dec.id,
                event_type="decision.created",
                actor_type=ActorType.MCP_AGENT,
                actor_id=principal.token_name,
                payload={
                    "title": dec.title,
                    "decision_type": dec.decision_type,
                    "risk_level": dec.risk_level,
                    "timeout_policy": dec.timeout_policy,
                    "timeout_sec": dec.timeout_sec,
                    "recommended_option_id": dec.recommended_option_id,
                    "session_id": args.session_id,
                    "task_id": args.task_id,
                },
            )
            dec_obj = {
                "id": dec.id,
                "decision_id": dec.id,
                "project_id": dec.project_id,
                "title": dec.title,
                "decision_type": dec.decision_type,
                "risk_level": dec.risk_level,
                "status": dec.status,
                "timeout_policy": dec.timeout_policy,
                "timeout_sec": dec.timeout_sec,
                "recommended_option_id": dec.recommended_option_id,
                "created_at": dec.created_at,
            }
            return (
                "decision",
                dec.id,
                {
                    **dec_obj,
                    "decision": dec_obj,
                },
            )

        return self._idempotency.execute_idempotent(
            project_id=project_id,
            tool_name="checkpoint_request",
            idempotency_key=args.idempotency_key,
            payload=raw_args,
            operation=_checkpoint_op,
        )

    # ==================== 7. decision_get ====================
    def decision_get(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = DecisionGetArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="decision_get",
            claimed_project_id=args.project_id,
        )

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            dec = uow.session.get(DecisionModel, args.decision_id)
            if dec is None:
                raise EntityNotFoundError(
                    problem="目标决策记录不存在",
                    cause=f"未找到 decision_id={args.decision_id}",
                    fix="请核对 decision_id 是否正确。",
                )
            if dec.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="拦截跨项目查询决策结果",
                    cause=f"决策 {args.decision_id} 不归属于当前项目 {project_id}",
                    fix="禁止跨项目访问决策记录。",
                )

            return (
                {
                    "is_resolved": dec.status == DecisionStatus.RESOLVED.value,
                    "decision": {
                        "id": dec.id,
                        "project_id": dec.project_id,
                        "task_id": dec.task_id,
                        "step_id": dec.step_id,
                        "session_id": dec.session_id,
                        "title": dec.title,
                        "decision_type": dec.decision_type,
                        "risk_level": dec.risk_level,
                        "status": dec.status,
                        "timeout_policy": dec.timeout_policy,
                        "recommended_option_id": dec.recommended_option_id,
                        "chosen_option_id": dec.chosen_option_id,
                        "custom_note": dec.custom_note,
                        "resolved_by": dec.resolved_by,
                        "created_at": dec.created_at,
                        "resolved_at": dec.resolved_at,
                    },
                },
                False,
            )

    # ==================== 8. artifact_register ====================
    def artifact_register(
        self,
        principal: VerifiedMcpPrincipal,
        raw_args: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        args = ArtifactRegisterArgs.model_validate(raw_args)
        project_id = self._enforce_scope_and_project(
            principal,
            tool_name="artifact_register",
            claimed_project_id=args.project_id,
        )

        def _artifact_op(uow: SqlAlchemyUnitOfWork) -> tuple[str, str, dict[str, Any]]:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="登记产物关联的项目不存在",
                    cause=f"未找到 project_id={project_id}",
                    fix="请确认项目已创建。",
                )

            clean_rel_path = validate_artifact_relative_path(proj.root_path, args.relative_path)

            clean_summary = (args.summary_content or "").strip()
            if len(clean_summary) > MAX_ARTIFACT_SUMMARY_CHARS:
                raise InvariantViolationError(
                    problem="产物摘要内容超出最大长度限制",
                    cause=f"summary_content 长度为 {len(clean_summary)}，超过上限 {MAX_ARTIFACT_SUMMARY_CHARS}",
                    fix="请精简产物摘要内容后重新登记。",
                    context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
                )

            raw_meta_bytes = len(
                json.dumps(args.metadata or {}, ensure_ascii=False).encode("utf-8")
            )
            if raw_meta_bytes > MAX_ARTIFACT_METADATA_BYTES:
                raise InvariantViolationError(
                    problem="产物 metadata 超出最大字节限制",
                    cause=f"metadata 序列化后 ({raw_meta_bytes} 字节) 超过 {MAX_ARTIFACT_METADATA_BYTES} 字节上限",
                    fix="请仅在 metadata 中保留轻量结构化元数据。",
                    context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
                )
            sanitized_meta_json = _sanitize_event_payload(args.metadata or {})

            task_rev_no: Optional[int] = None
            if args.task_id is not None:
                task = uow.session.get(TaskModel, args.task_id)
                if task is None or task.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="产物关联的任务不存在或不属于当前项目",
                        cause=f"task_id={args.task_id} 与 project_id={project_id} 不匹配",
                        fix="请核对任务与项目归属。",
                    )
                task_rev_no = task.active_revision_no

            if args.session_id is not None:
                sess = uow.session.get(SessionModel, args.session_id)
                if sess is None or sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="产物关联的会话不存在或不属于当前项目",
                        cause=f"session_id={args.session_id} 与 project_id={project_id} 不匹配",
                        fix="请核对会话与项目归属。",
                    )
                sess.last_heartbeat_at = utc_now_iso()

            if args.execution_id is not None:
                execution = uow.session.get(ExecutionModel, args.execution_id)
                if execution is None or execution.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="产物关联的执行记录不存在或不属于当前项目",
                        cause=f"execution_id={args.execution_id} 与 project_id={project_id} 不匹配",
                        fix="请核对执行记录与项目归属。",
                    )

            checksum = hashlib.sha256(clean_summary.encode("utf-8")).hexdigest()
            now = utc_now_iso()
            artifact = ArtifactModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=args.task_id,
                session_id=args.session_id,
                execution_id=args.execution_id,
                artifact_type=args.artifact_type.value,
                title=args.title.strip()[:240],
                relative_path=clean_rel_path,
                summary_content=clean_summary,
                checksum_sha256=checksum,
                source_task_revision_no=task_rev_no,
                metadata_json=sanitized_meta_json,
                created_by=ActorType.MCP_AGENT.value,
                created_at=now,
            )
            uow.session.add(artifact)
            uow.record_event(
                project_id=project_id,
                entity_type="artifact",
                entity_id=artifact.id,
                event_type="artifact.created",
                actor_type=ActorType.MCP_AGENT,
                actor_id=principal.token_name,
                payload={
                    "title": artifact.title,
                    "artifact_type": artifact.artifact_type,
                    "relative_path": artifact.relative_path,
                    "task_id": args.task_id,
                    "session_id": args.session_id,
                    "source_task_revision_no": task_rev_no,
                },
            )
            art_obj = {
                "id": artifact.id,
                "artifact_id": artifact.id,
                "project_id": artifact.project_id,
                "task_id": artifact.task_id,
                "session_id": artifact.session_id,
                "title": artifact.title,
                "artifact_type": artifact.artifact_type,
                "relative_path": artifact.relative_path,
                "checksum_sha256": artifact.checksum_sha256,
                "source_task_revision_no": artifact.source_task_revision_no,
                "created_at": artifact.created_at,
            }
            return (
                "artifact",
                artifact.id,
                {
                    **art_obj,
                    "artifact": art_obj,
                },
            )

        return self._idempotency.execute_idempotent(
            project_id=project_id,
            tool_name="artifact_register",
            idempotency_key=args.idempotency_key,
            payload=raw_args,
            operation=_artifact_op,
        )
