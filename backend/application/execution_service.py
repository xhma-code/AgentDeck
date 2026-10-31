# -*- coding: utf-8 -*-
"""
执行活动与单次运行尝试应用服务（ExecutionService）。

核心领域不变量实现：
1. 严格区分 Execution（业务级执行单，绑定锁定的 TaskRevision / StepRevision 版本号）与 ExecutionAttempt（第 N 次具体尝试）。
2. 强制同 Project 关联校验。
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.execution import ExecutionAttemptModel, ExecutionModel
from backend.db.models.planning import StepModel, TaskModel
from backend.db.models.project import ProjectModel
from backend.db.models.runtime import RuntimeInstanceModel
from backend.db.models.session import SessionModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import (
    ActorType,
    AgentReportedStatus,
    AttemptStatus,
    ConnectionStatus,
    ExecutionPlatformStatus,
    RuntimeHealthStatus,
)
from backend.domain.exceptions import (
    CrossProjectReferenceError,
    EntityNotFoundError,
)
from backend.domain.types import ModelEndpointDescriptor


class ExecutionService:
    """管理业务级 Execution 与具体运行尝试 ExecutionAttempt。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_execution(
        self,
        *,
        project_id: str,
        task_id: str,
        step_id: Optional[str] = None,
        session_id: Optional[str] = None,
        runtime_instance_id: Optional[str] = None,
        summary_note: str = "",
        model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = None,
        actor_type: ActorType = ActorType.USER,
    ) -> tuple[ExecutionModel, ExecutionAttemptModel]:
        """创建 Execution 并自动启动第 1 次 ExecutionAttempt，锁定当前 Task/Step 的 active_revision_no。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="关联项目不存在",
                    cause=f"未找到 project_id: {project_id}",
                    fix="请确认项目存在。",
                )

            task = uow.session.get(TaskModel, task_id)
            if task is None:
                raise EntityNotFoundError(
                    problem="关联任务不存在",
                    cause=f"未找到 task_id: {task_id}",
                    fix="请确认任务存在。",
                )
            if task.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目创建执行记录",
                    cause=f"任务 {task_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )

            step_rev_no: Optional[int] = None
            if step_id is not None:
                step = uow.session.get(StepModel, step_id)
                if step is None:
                    raise EntityNotFoundError(
                        problem="关联步骤不存在",
                        cause=f"未找到 step_id: {step_id}",
                        fix="请确认步骤存在。",
                    )
                if step.project_id != project_id or step.task_id != task_id:
                    raise CrossProjectReferenceError(
                        problem="步骤与任务或项目归属不一致",
                        cause=f"步骤 {step_id} 不属于项目 {project_id} 的任务 {task_id}",
                        fix="请选择同一任务下的步骤。",
                    )
                step_rev_no = step.active_revision_no

            if session_id is not None:
                sess = uow.session.get(SessionModel, session_id)
                if sess is None:
                    raise EntityNotFoundError(
                        problem="关联会话不存在",
                        cause=f"未找到 session_id: {session_id}",
                        fix="请确认会话存在。",
                    )
                if sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="禁止跨项目绑定执行会话",
                        cause=f"会话 {session_id} 不属于项目 {project_id}",
                        fix="请选择同项目下的协同会话。",
                    )

            if runtime_instance_id is not None:
                rt = uow.session.get(RuntimeInstanceModel, runtime_instance_id)
                if rt is None:
                    raise EntityNotFoundError(
                        problem="关联 Runtime 实例不存在",
                        cause=f"未找到 runtime_instance_id: {runtime_instance_id}",
                        fix="请先注册对应的 Runtime 实例。",
                    )

            now = utc_now_iso()
            exec_id = str(uuid.uuid4())
            execution = ExecutionModel(
                id=exec_id,
                project_id=project_id,
                task_id=task_id,
                step_id=step_id,
                session_id=session_id,
                task_revision_no=task.active_revision_no,
                step_revision_no=step_rev_no,
                platform_status=ExecutionPlatformStatus.RUNNING.value,
                agent_reported_status=AgentReportedStatus.RUNNING.value,
                runtime_status=RuntimeHealthStatus.ONLINE.value,
                connection_status=ConnectionStatus.CONNECTED.value,
                summary_note=(summary_note or "").strip(),
                started_at=now,
                finished_at=None,
                created_at=now,
                updated_at=now,
            )
            endpoint_json = (
                json.dumps(model_endpoint_descriptor.model_dump(), ensure_ascii=False)
                if model_endpoint_descriptor is not None
                else None
            )
            attempt = ExecutionAttemptModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                execution_id=exec_id,
                attempt_no=1,
                runtime_instance_id=runtime_instance_id,
                status=AttemptStatus.RUNNING.value,
                exit_code=None,
                error_summary="",
                model_endpoint_descriptor_json=endpoint_json,
                started_at=now,
                ended_at=None,
            )
            uow.session.add(execution)
            uow.session.flush()
            uow.session.add(attempt)
            uow.record_event(
                project_id=project_id,
                entity_type="execution",
                entity_id=exec_id,
                event_type="execution.started",
                actor_type=actor_type,
                payload={
                    "task_id": task_id,
                    "task_revision_no": task.active_revision_no,
                    "step_id": step_id,
                    "step_revision_no": step_rev_no,
                    "attempt_id": attempt.id,
                    "attempt_no": 1,
                },
            )
            uow.commit()
            return execution, attempt

    def start_new_attempt(
        self,
        *,
        project_id: str,
        execution_id: str,
        runtime_instance_id: Optional[str] = None,
        model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = None,
        actor_type: ActorType = ActorType.USER,
    ) -> ExecutionAttemptModel:
        """在同一 Execution 下追加第 N+1 次运行尝试（不覆盖前序 Attempt 记录）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            execution = uow.session.get(ExecutionModel, execution_id)
            if execution is None:
                raise EntityNotFoundError(
                    problem="目标执行活动不存在",
                    cause=f"未找到 execution_id: {execution_id}",
                    fix="请确认执行 ID 正确。",
                )
            if execution.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目追加执行尝试",
                    cause=f"执行 {execution_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )

            max_no = uow.session.execute(
                select(func.max(ExecutionAttemptModel.attempt_no)).where(
                    ExecutionAttemptModel.execution_id == execution_id
                )
            ).scalar()
            next_no = int(max_no or 0) + 1
            now = utc_now_iso()

            execution.platform_status = ExecutionPlatformStatus.RUNNING.value
            execution.updated_at = now

            endpoint_json = (
                json.dumps(model_endpoint_descriptor.model_dump(), ensure_ascii=False)
                if model_endpoint_descriptor is not None
                else None
            )
            attempt = ExecutionAttemptModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                execution_id=execution_id,
                attempt_no=next_no,
                runtime_instance_id=runtime_instance_id,
                status=AttemptStatus.RUNNING.value,
                exit_code=None,
                error_summary="",
                model_endpoint_descriptor_json=endpoint_json,
                started_at=now,
                ended_at=None,
            )
            uow.session.add(attempt)
            uow.record_event(
                project_id=project_id,
                entity_type="execution",
                entity_id=execution_id,
                event_type="execution.attempt_started",
                actor_type=actor_type,
                payload={"attempt_id": attempt.id, "attempt_no": next_no},
            )
            uow.commit()
            return attempt

    def list_project_executions(self, project_id: str) -> list[ExecutionModel]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(ExecutionModel)
                .where(ExecutionModel.project_id == project_id)
                .order_by(ExecutionModel.created_at.desc())
            )
            return list(uow.session.execute(stmt).scalars().all())

    def list_execution_attempts(self, execution_id: str) -> list[ExecutionAttemptModel]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(ExecutionAttemptModel)
                .where(ExecutionAttemptModel.execution_id == execution_id)
                .order_by(ExecutionAttemptModel.attempt_no.asc())
            )
            return list(uow.session.execute(stmt).scalars().all())
