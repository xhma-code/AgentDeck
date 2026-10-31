# -*- coding: utf-8 -*-
"""
SQLAlchemy Unit of Work（工作单元）。

核心职责：
1. 保证所有领域实体变更与 `events` 审计事件写入处于**同一个 SQLite 数据库事务**中原子提交。
2. 提交前先将业务实体 flush 至当前未提交事务（满足 SQLite PRAGMA foreign_keys=ON 顺序要求），
   随后在同一事务内写入全部挂起领域事件并执行单次原子 commit()。
3. 任何步骤抛错均触发完整事务回滚（业务表与 events 表同生共死），并将异常统一汇聚写入 `logs/error.log`。
"""

from __future__ import annotations

import json
import uuid
from types import TracebackType
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy.orm import Session, sessionmaker

from backend.core.logger import log_structured_error
from backend.v2.db.base import utc_now_iso
from backend.v2.domain.enums import ActorType
from backend.v2.domain.exceptions import DomainError
from backend.v2.domain.types import SECRET_VALUE_PATTERN, SENSITIVE_QUERY_KEYS

if TYPE_CHECKING:
    from backend.v2.db.models.event import EventModel


def _sanitize_event_payload(payload: Optional[dict[str, Any]]) -> str:
    """
    序列化事件载荷并执行脱敏守卫（v1.3 第 13.11 节：不把敏感 Token/Secret 放入 Event）。
    """
    if not payload:
        return "{}"

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            cleaned_dict: dict[str, Any] = {}
            for k, v in obj.items():
                k_str = str(k)
                if k_str.lower() in SENSITIVE_QUERY_KEYS:
                    cleaned_dict[k_str] = "[REDACTED]"
                else:
                    cleaned_dict[k_str] = _clean(v)
            return cleaned_dict
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        if isinstance(obj, str):
            if SECRET_VALUE_PATTERN.search(obj):
                return SECRET_VALUE_PATTERN.sub("[REDACTED]", obj)
            return obj
        return obj

    return json.dumps(_clean(payload), ensure_ascii=False, separators=(",", ":"))


class SqlAlchemyUnitOfWork:
    """同事务工作单元（业务表 + events 表原子提交）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Optional[Session] = None
        self._pending_events: list["EventModel"] = []

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self._pending_events = []
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self._pending_events.clear()
        if self.session is None:
            return
        try:
            if exc_type is not None:
                self.session.rollback()
                if not isinstance(exc_val, DomainError) and exc_val is not None:
                    log_structured_error(
                        problem="数据库工作单元事务回滚",
                        cause=f"{type(exc_val).__name__}: {exc_val}",
                        fix="系统已自动回滚当前事务；请查看 logs/error.log 堆栈排查数据约束或代码异常。",
                        exc=exc_val,
                    )
        finally:
            self.session.close()
            self.session = None

    def record_event(
        self,
        *,
        entity_type: str,
        entity_id: str,
        event_type: str,
        project_id: Optional[str] = None,
        actor_type: ActorType | str = ActorType.USER,
        actor_id: str = "",
        correlation_id: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> "EventModel":
        """在当前工作单元登记一条待同事务落盘的领域事件。"""
        if self.session is None:
            raise RuntimeError("UnitOfWork 尚未进入上下文，无法记录领域事件")

        from backend.v2.db.models.event import EventModel

        evt = EventModel(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            actor_type=str(actor_type.value if isinstance(actor_type, ActorType) else actor_type),
            actor_id=actor_id[:120],
            correlation_id=correlation_id[:120],
            payload_json=_sanitize_event_payload(payload),
            created_at=utc_now_iso(),
        )
        self._pending_events.append(evt)
        return evt

    def commit(self) -> None:
        """在单一 SQLite 事务内先后刷入业务实体与领域事件并原子提交。"""
        if self.session is None:
            raise RuntimeError("UnitOfWork 尚未进入上下文，无法提交事务")
        try:
            # 1. 在同一事务内先刷入业务表变更，确保新建的 Project 等父键已对当前事务可见
            self.session.flush()
            # 2. 在同一事务内刷入全部关联领域事件
            if self._pending_events:
                self.session.add_all(self._pending_events)
                self._pending_events.clear()
                self.session.flush()
            # 3. 原子提交整个 SQLite 事务
            self.session.commit()
        except Exception as exc:
            self._pending_events.clear()
            self.session.rollback()
            if not isinstance(exc, DomainError):
                log_structured_error(
                    problem="数据库事务提交失败",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="事务已整体回滚；请检查唯一键、外键约束及 SQLite 锁状态。",
                    exc=exc,
                )
            raise
