# -*- coding: utf-8 -*-
"""
项目管理应用服务（ProjectService）。
负责 Project 注册（含本地工作区路径安全校验）、状态管理、乐观锁更新与项目级 MCP Token 生命周期管理。
"""

from __future__ import annotations

import re
import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.project import McpProjectTokenModel, ProjectModel
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import ActorType, ProjectStatus
from backend.domain.exceptions import (
    EntityNotFoundError,
    InvariantViolationError,
    VersionConflictError,
)
from backend.security.mcp_token_guard import (
    IssuedMcpToken,
    issue_mcp_project_token,
    revoke_mcp_project_token,
    rotate_mcp_project_token,
)
from backend.security.path_guard import validate_project_root_dir


def _slugify_project_name(raw_slug_or_name: str) -> str:
    """生成规范的小写连字符项目短标识（支持中文项目名回退短随机后缀）。"""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", (raw_slug_or_name or "").strip().lower()).strip("-_")
    if not cleaned:
        cleaned = f"proj-{uuid.uuid4().hex[:8]}"
    return cleaned[:80]


class ProjectService:
    """研发项目生命周期管理服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_project(
        self,
        *,
        name: str,
        root_path: str,
        slug: Optional[str] = None,
        description: str = "",
        git_remote: Optional[str] = None,
        summary_next_action: str = "",
    ) -> ProjectModel:
        """创建新项目，强制校验本地 root_path 合法性并在同一事务内写入创建事件。"""
        clean_name = (name or "").strip()
        if not clean_name:
            raise InvariantViolationError(
                problem="项目名称不能为空",
                cause="传入的 name 字段为空白字符串",
                fix="请提供清晰的项目显示名称。",
            )

        norm_root = validate_project_root_dir(root_path)
        candidate_slug = _slugify_project_name(slug or clean_name)

        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            dup_root = uow.session.execute(
                select(ProjectModel).where(ProjectModel.root_path == norm_root)
            ).scalar_one_or_none()
            if dup_root is not None:
                raise InvariantViolationError(
                    problem="该本地工作区目录已注册为项目",
                    cause=f"路径 '{norm_root}' 已绑定至项目 '{dup_root.name}' ({dup_root.slug})",
                    fix="请直接使用已有项目，或指定其他代码仓库目录。",
                    context={"existing_project_id": dup_root.id, "root_path": norm_root},
                )

            dup_slug = uow.session.execute(
                select(ProjectModel).where(ProjectModel.slug == candidate_slug)
            ).scalar_one_or_none()
            if dup_slug is not None:
                if slug:
                    raise InvariantViolationError(
                        problem="项目短代号 (slug) 已被占用",
                        cause=f"slug '{candidate_slug}' 已存在",
                        fix="请更换唯一的项目短代号。",
                        context={"slug": candidate_slug},
                    )
                candidate_slug = f"{candidate_slug[:70]}-{uuid.uuid4().hex[:6]}"

            now = utc_now_iso()
            proj = ProjectModel(
                id=str(uuid.uuid4()),
                slug=candidate_slug,
                name=clean_name[:160],
                description=(description or "").strip(),
                root_path=norm_root,
                git_remote=(git_remote or "").strip() or None,
                status=ProjectStatus.ACTIVE.value,
                summary_next_action=(summary_next_action or "").strip(),
                version=1,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(proj)
            uow.record_event(
                project_id=proj.id,
                entity_type="project",
                entity_id=proj.id,
                event_type="project.created",
                actor_type=ActorType.USER,
                payload={
                    "slug": proj.slug,
                    "name": proj.name,
                    "root_path": proj.root_path,
                    "status": proj.status,
                },
            )
            uow.commit()
            return proj

    def get_project(self, project_id: str) -> ProjectModel:
        """按 ID 查询单个项目，不存在则抛出 EntityNotFoundError。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="目标项目不存在",
                    cause=f"未找到 ID 为 '{project_id}' 的项目",
                    fix="请核对项目 ID 是否正确。",
                    context={"project_id": project_id},
                )
            return proj

    def list_projects(self, status: Optional[ProjectStatus | str] = None) -> list[ProjectModel]:
        """按状态筛选并按最近更新时间倒序返回项目列表。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = select(ProjectModel)
            if status is not None:
                status_val = status.value if isinstance(status, ProjectStatus) else str(status)
                stmt = stmt.where(ProjectModel.status == status_val)
            stmt = stmt.order_by(ProjectModel.updated_at.desc())
            return list(uow.session.execute(stmt).scalars().all())

    def update_project(
        self,
        project_id: str,
        *,
        expected_version: Optional[int] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[ProjectStatus] = None,
        summary_next_action: Optional[str] = None,
    ) -> ProjectModel:
        """更新项目信息与状态（支持乐观锁校验与事件同事务提交）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            proj = uow.session.get(ProjectModel, project_id)
            if proj is None:
                raise EntityNotFoundError(
                    problem="目标项目不存在",
                    cause=f"未找到 ID 为 '{project_id}' 的项目",
                    fix="请核对项目 ID 是否正确。",
                    context={"project_id": project_id},
                )

            if expected_version is not None and proj.version != expected_version:
                raise VersionConflictError(
                    problem="项目信息已被其他操作更新（版本冲突）",
                    cause=f"期望版本 {expected_version}，当前数据库版本 {proj.version}",
                    fix="请刷新最新项目状态后重试。",
                    context={"project_id": project_id, "current_version": proj.version},
                )

            changed_fields: dict[str, str] = {}
            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise InvariantViolationError(
                        problem="项目名称不能为空",
                        cause="更新时传入了空白名称",
                        fix="请提供非空项目名称。",
                    )
                proj.name = clean_name[:160]
                changed_fields["name"] = proj.name

            if description is not None:
                proj.description = description.strip()
                changed_fields["description"] = "updated"

            if status is not None:
                proj.status = status.value
                changed_fields["status"] = proj.status

            if summary_next_action is not None:
                proj.summary_next_action = summary_next_action.strip()
                changed_fields["summary_next_action"] = proj.summary_next_action

            proj.version += 1
            proj.updated_at = utc_now_iso()

            uow.record_event(
                project_id=proj.id,
                entity_type="project",
                entity_id=proj.id,
                event_type="project.updated",
                actor_type=ActorType.USER,
                payload={"version": proj.version, "changes": changed_fields},
            )
            uow.commit()
            return proj

    def issue_mcp_token(
        self,
        *,
        project_id: str,
        token_name: str,
        scopes: Sequence[str],
        expires_at: Optional[str] = None,
    ) -> IssuedMcpToken:
        """为项目签发专属 MCP 令牌。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            return issue_mcp_project_token(
                uow,
                project_id=project_id,
                token_name=token_name,
                scopes=scopes,
                expires_at=expires_at,
            )

    def revoke_mcp_token(self, *, project_id: str, token_id: str) -> None:
        """吊销项目下的 MCP 令牌。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            revoke_mcp_project_token(uow, project_id=project_id, token_id=token_id)

    def rotate_mcp_token(
        self,
        *,
        project_id: str,
        token_id: str,
        expires_at: Optional[str] = None,
    ) -> IssuedMcpToken:
        """同事务原子轮换项目下的 MCP 令牌（吊销旧令牌并继承原权限范围签发新令牌）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            return rotate_mcp_project_token(
                uow,
                project_id=project_id,
                token_id=token_id,
                expires_at=expires_at,
            )

    def list_mcp_tokens(self, project_id: str) -> list[McpProjectTokenModel]:
        """查询项目下全部 MCP 令牌元数据（不含明文）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = (
                select(McpProjectTokenModel)
                .where(McpProjectTokenModel.project_id == project_id)
                .order_by(McpProjectTokenModel.created_at.desc())
            )
            return list(uow.session.execute(stmt).scalars().all())

