# -*- coding: utf-8 -*-
"""
协同会话管理应用服务（SessionService）。

核心领域不变量实现：
1. 任何 Session 必须归属明确 Project（禁止空 project_id 游离会话）。
2. 若关联 Task，强制校验 Task 与 Session 同属一个 Project。
3. ExternalSessionRef 绑定时强制同 Project 校验，且 remote_entry_url 执行严格凭证脱敏拦截。
4. 严格分离 platform_status / agent_reported_status / runtime_status / connection_status：
   - Remote 连接断开 (connection_status=disconnected) 绝不自动将平台会话或任务标为失败；
   - Agent 汇报完成绝不自动等同平台完成。
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.v2.db.base import utc_now_iso
from backend.v2.db.models.planning import TaskModel
from backend.v2.db.models.project import ProjectModel
from backend.v2.db.models.runtime import RuntimeInstanceModel
from backend.v2.db.models.session import ExternalSessionRefModel, SessionModel
from backend.v2.db.uow import SqlAlchemyUnitOfWork
from backend.v2.domain.enums import (
    ActorType,
    AgentReportedStatus,
    ConnectionStatus,
    LinkStatus,
    RemoteEntryKind,
    RuntimeHealthStatus,
    SessionPlatformStatus,
    SessionSourceChannel,
)
from backend.v2.domain.exceptions import (
    CrossProjectReferenceError,
    EntityNotFoundError,
    InvariantViolationError,
    VersionConflictError,
)
from backend.v2.domain.types import validate_safe_remote_url


class SessionService:
    """项目协同会话与官方 Remote 引用登记服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_session(
        self,
        *,
        project_id: str,
        title: str,
        task_id: Optional[str] = None,
        runtime_instance_id: Optional[str] = None,
        source_channel: SessionSourceChannel = SessionSourceChannel.REMOTE_LINK,
        actor_type: ActorType = ActorType.USER,
    ) -> SessionModel:
        """创建归属明确 Project 的协同会话，同事务写入 session.created 事件。"""
        clean_pid = (project_id or "").strip()
        if not clean_pid:
            raise InvariantViolationError(
                problem="禁止创建无归属项目的游离会话",
                cause="project_id 为空（违反核心不变量 #1：Session 必须属于 Project）",
                fix="请先选择或创建一个目标 Project 后再登记 Session。",
            )

        clean_title = (title or "").strip()
        if not clean_title:
            raise InvariantViolationError(
                problem="会话标题不能为空",
                cause="传入了空白会话标题",
                fix="请填写可识别的协同会话标题。",
            )

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, clean_pid)
            if proj is None:
                raise EntityNotFoundError(
                    problem="会话关联的项目不存在",
                    cause=f"未找到项目 ID: {clean_pid}",
                    fix="请确认项目存在。",
                    context={"project_id": clean_pid},
                )

            if task_id is not None:
                task = uow.session.get(TaskModel, task_id)
                if task is None:
                    raise EntityNotFoundError(
                        problem="会话关联的任务不存在",
                        cause=f"未找到任务 ID: {task_id}",
                        fix="请确认任务 ID 是否正确。",
                        context={"task_id": task_id},
                    )
                if task.project_id != clean_pid:
                    raise CrossProjectReferenceError(
                        problem="禁止将会话关联到其他项目的任务",
                        cause=f"任务 {task_id} 属于项目 {task.project_id}，与会话所属项目 {clean_pid} 不一致",
                        fix="请选择同一 Project 下的任务。",
                        context={
                            "session_project_id": clean_pid,
                            "task_id": task_id,
                            "task_project_id": task.project_id,
                        },
                    )

            if runtime_instance_id is not None:
                rt = uow.session.get(RuntimeInstanceModel, runtime_instance_id)
                if rt is None:
                    raise EntityNotFoundError(
                        problem="关联的 Runtime 实例不存在",
                        cause=f"未找到 runtime_instance_id: {runtime_instance_id}",
                        fix="请先登记对应的 Runtime 实例。",
                    )

            now = utc_now_iso()
            sess = SessionModel(
                id=str(uuid.uuid4()),
                project_id=clean_pid,
                task_id=task_id,
                runtime_instance_id=runtime_instance_id,
                title=clean_title[:240],
                source_channel=source_channel.value,
                platform_status=SessionPlatformStatus.ACTIVE.value,
                agent_reported_status=AgentReportedStatus.UNKNOWN.value,
                runtime_status=RuntimeHealthStatus.UNKNOWN.value,
                connection_status=ConnectionStatus.UNKNOWN.value,
                status_seq=1,
                last_activity_at=now,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(sess)
            uow.record_event(
                project_id=clean_pid,
                entity_type="session",
                entity_id=sess.id,
                event_type="session.created",
                actor_type=actor_type,
                payload={
                    "title": sess.title,
                    "task_id": task_id,
                    "runtime_instance_id": runtime_instance_id,
                    "source_channel": sess.source_channel,
                },
            )
            uow.commit()
            return sess

    def bind_external_session_ref(
        self,
        *,
        project_id: str,
        session_id: str,
        runtime_instance_id: str,
        external_session_id: str = "",
        remote_entry_kind: RemoteEntryKind = RemoteEntryKind.NONE,
        remote_entry_url: str = "",
        link_status: LinkStatus = LinkStatus.UNKNOWN,
    ) -> ExternalSessionRefModel:
        """为会话登记外部 Runtime 原生标识与脱敏后的官方 Remote 入口链接。"""
        safe_url = validate_safe_remote_url(remote_entry_url)

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            sess = uow.session.get(SessionModel, session_id)
            if sess is None:
                raise EntityNotFoundError(
                    problem="目标协同会话不存在",
                    cause=f"未找到 session_id: {session_id}",
                    fix="请先创建平台协同会话。",
                )
            if sess.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目绑定外部会话引用",
                    cause=f"会话 {session_id} 归属项目 {sess.project_id}，与传入项目 {project_id} 不一致",
                    fix="请在会话所属项目上下文中绑定外部引用。",
                )

            rt = uow.session.get(RuntimeInstanceModel, runtime_instance_id)
            if rt is None:
                raise EntityNotFoundError(
                    problem="关联的 Runtime 实例不存在",
                    cause=f"未找到 runtime_instance_id: {runtime_instance_id}",
                    fix="请先登记有效的 Runtime 实例。",
                )

            now = utc_now_iso()
            ref = ExternalSessionRefModel(
                id=str(uuid.uuid4()),
                project_id=project_id,
                session_id=session_id,
                runtime_instance_id=runtime_instance_id,
                external_session_id=(external_session_id or "").strip()[:160],
                remote_entry_kind=remote_entry_kind.value,
                remote_entry_url=safe_url,
                link_status=link_status.value,
                last_activity_at=now,
                created_at=now,
                updated_at=now,
            )
            if sess.runtime_instance_id is None:
                sess.runtime_instance_id = runtime_instance_id
                sess.updated_at = now

            uow.session.add(ref)
            uow.record_event(
                project_id=project_id,
                entity_type="session",
                entity_id=session_id,
                event_type="session.external_ref_bound",
                actor_type=ActorType.USER,
                payload={
                    "external_ref_id": ref.id,
                    "runtime_instance_id": runtime_instance_id,
                    "remote_entry_kind": ref.remote_entry_kind,
                    "has_remote_url": bool(safe_url),
                },
            )
            uow.commit()
            return ref

    def update_session_statuses(
        self,
        *,
        project_id: str,
        session_id: str,
        platform_status: Optional[SessionPlatformStatus] = None,
        agent_reported_status: Optional[AgentReportedStatus] = None,
        runtime_status: Optional[RuntimeHealthStatus] = None,
        connection_status: Optional[ConnectionStatus] = None,
        expected_status_seq: Optional[int] = None,
        actor_type: ActorType = ActorType.SYSTEM,
    ) -> SessionModel:
        """
        按四层状态独立演进 Session 状态（单调递增 status_seq 防乱序覆盖）。
        Remote 连接断开只改变 connection_status，绝不污染 platform_status。
        """
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            sess = uow.session.get(SessionModel, session_id)
            if sess is None:
                raise EntityNotFoundError(
                    problem="目标协同会话不存在",
                    cause=f"未找到 session_id: {session_id}",
                    fix="请核对会话 ID。",
                )
            if sess.project_id != project_id:
                raise CrossProjectReferenceError(
                    problem="禁止跨项目更新会话状态",
                    cause=f"会话 {session_id} 不属于项目 {project_id}",
                    fix="请核对项目上下文。",
                )

            if expected_status_seq is not None and sess.status_seq != expected_status_seq:
                raise VersionConflictError(
                    problem="会话状态序号已发生推进（陈旧状态写入拦截）",
                    cause=f"期望 status_seq={expected_status_seq}，当前为 {sess.status_seq}",
                    fix="请拉取最新会话状态后再评估状态变更。",
                )

            if platform_status is not None:
                sess.platform_status = platform_status.value
            if agent_reported_status is not None:
                sess.agent_reported_status = agent_reported_status.value
            if runtime_status is not None:
                sess.runtime_status = runtime_status.value
            if connection_status is not None:
                sess.connection_status = connection_status.value

            now = utc_now_iso()
            sess.status_seq += 1
            sess.last_activity_at = now
            sess.updated_at = now

            uow.record_event(
                project_id=project_id,
                entity_type="session",
                entity_id=sess.id,
                event_type="session.status_updated",
                actor_type=actor_type,
                payload={
                    "status_seq": sess.status_seq,
                    "platform_status": sess.platform_status,
                    "agent_reported_status": sess.agent_reported_status,
                    "runtime_status": sess.runtime_status,
                    "connection_status": sess.connection_status,
                },
            )
            uow.commit()
            return sess

    def list_project_sessions(self, project_id: str) -> list[SessionModel]:
        """查询项目下全部协同会话（按最近活动时间倒序）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(SessionModel)
                .where(SessionModel.project_id == project_id)
                .order_by(SessionModel.updated_at.desc())
            )
            return list(uow.session.execute(stmt).scalars().all())

