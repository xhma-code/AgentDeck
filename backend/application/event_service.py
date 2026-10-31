# -*- coding: utf-8 -*-
"""
统一领域事件时间线查询服务（EventService）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models.event import EventModel
from backend.db.uow import SqlAlchemyUnitOfWork


class EventService:
    """提供只读追加式 Event 时间线的多维筛选与增量拉取。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_events(
        self,
        *,
        project_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        after_seq_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[EventModel]:
        """按项目、实体或自增序号 seq_id 查询事件时间线（默认按最新事件倒序）。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            stmt = select(EventModel)
            if project_id:
                stmt = stmt.where(EventModel.project_id == project_id)
            if entity_type:
                stmt = stmt.where(EventModel.entity_type == entity_type)
            if entity_id:
                stmt = stmt.where(EventModel.entity_id == entity_id)
            if after_seq_id is not None:
                stmt = stmt.where(EventModel.seq_id > after_seq_id).order_by(EventModel.seq_id.asc())
            else:
                stmt = stmt.order_by(EventModel.seq_id.desc())
            stmt = stmt.limit(max(1, min(limit, 500)))
            return list(uow.session.execute(stmt).scalars().all())
