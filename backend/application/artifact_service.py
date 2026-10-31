# -*- coding: utf-8 -*-
"""
任务产物归档应用服务（ArtifactService）。
管理测试报告、计划摘要、文件变更清单与结构化总结的写入及查询。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.artifact import ArtifactModel
from backend.db.models.execution import ExecutionModel
from backend.db.models.planning import TaskModel
from backend.db.models.project import ProjectModel
from backend.db.models.session import SessionModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import ActorType, ArtifactType
from backend.domain.exceptions import CrossProjectReferenceError, EntityNotFoundError


class ArtifactService:
    """结构化执行产物登记与查询服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_artifact(
        self,
        *,
        project_id: str,
        title: str,
        artifact_type: ArtifactType = ArtifactType.MARKDOWN_NOTE,
        summary_content: str = "",
        relative_path: str = "",
        metadata: Optional[dict[str, Any]] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        actor_type: ActorType = ActorType.MCP_AGENT,
    ) -> ArtifactModel:
        """创建归属明确 Project 的结构化产物并记录事件。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="创建产物关联的项目不存在",
                    cause=f"未找到 project_id: {project_id}",
                    fix="请确认项目已创建。",
                )

            task_rev_no: Optional[int] = None
            if task_id is not None:
                task = uow.session.get(TaskModel, task_id)
                if task is None or task.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="产物关联的任务不存在或不属于当前项目",
                        cause=f"task_id={task_id} 与 project_id={project_id} 不匹配",
                        fix="请核对任务与项目归属。",
                    )
                task_rev_no = task.active_revision_no

            if session_id is not None:
                sess = uow.session.get(SessionModel, session_id)
                if sess is None or sess.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="产物关联的会话不存在或不属于当前项目",
                        cause=f"session_id={session_id} 与 project_id={project_id} 不匹配",
                        fix="请核对会话与项目归属。",
                    )

            if execution_id is not None:
                execution = uow.session.get(ExecutionModel, execution_id)
                if execution is None or execution.project_id != project_id:
                    raise CrossProjectReferenceError(
                        problem="产物关联的执行记录不存在或不属于当前项目",
                        cause=f"execution_id={execution_id} 与 project_id={project_id} 不匹配",
                        fix="请核对执行记录与项目归属。",
                    )

            clean_summary = (summary_content or "").strip()
            checksum = (
                hashlib.sha256(clean_summary.encode("utf-8")).hexdigest()
                if clean_summary
                else ""
            )

            now = utc_now_iso()
            artifact = ArtifactModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                task_id=task_id,
                session_id=session_id,
                execution_id=execution_id,
                artifact_type=artifact_type.value,
                title=(title or "执行产物").strip()[:240],
                relative_path=(relative_path or "").strip(),
                summary_content=clean_summary,
                checksum_sha256=checksum,
                source_task_revision_no=task_rev_no,
                metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
                created_by=actor_type.value,
                created_at=now,
            )
            uow.session.add(artifact)
            uow.record_event(
                project_id=project_id,
                entity_type="artifact",
                entity_id=artifact.id,
                event_type="artifact.created",
                actor_type=actor_type,
                payload={
                    "title": artifact.title,
                    "artifact_type": artifact.artifact_type,
                    "task_id": task_id,
                    "session_id": session_id,
                },
            )
            uow.commit()
            return artifact

    def list_artifacts(
        self,
        *,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[ArtifactModel]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = select(ArtifactModel)
            if project_id:
                stmt = stmt.where(ArtifactModel.project_id == project_id)
            if task_id:
                stmt = stmt.where(ArtifactModel.task_id == task_id)
            if session_id:
                stmt = stmt.where(ArtifactModel.session_id == session_id)
            stmt = stmt.order_by(ArtifactModel.created_at.desc()).limit(limit)
            return list(uow.session.execute(stmt).scalars().all())
