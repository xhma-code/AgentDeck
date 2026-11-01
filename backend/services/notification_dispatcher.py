# -*- coding: utf-8 -*-
"""Notification Dispatcher 调度器服务。

核心职责：
1. 阶段一（短事务认领）：以显式 BEGIN IMMEDIATE 认领 Outbox 记录，递增 attempt_count（作为 Fencing generation），
   设置 60s 租约后立即提交释放 SQLite 排他写锁；
2. 阶段二（派生写入与 CAS 回写）：以独立 BEGIN IMMEDIATE 事务校验 Fencing 凭据，
   执行 10 秒通知聚合窗口判断（闭区间 [0, 10s]、负向乱序独立即关、超时先关后开），
   派生写入 notifications、notification_events、基于活跃订阅快照写入 notification_deliveries、
   写入 notification_changes 变更流，最后执行带 lease_expires_at > :now 的 CAS 回写，
   断言影响行数 == 1，否则强制整事务回滚，实现零副作用；
3. 超限终态收敛：将 attempt_count >= max_attempts 且租约过期的任务收敛为 failed；
4. 并发与重试：遵循 SQLite 单写者机制，支持带 Full Jitter 的有限重试（最多 4 次尝试，最大锁等待 60s）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import random
import time
from typing import Any, Callable, Optional
import uuid

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.core.logger import log_structured_error
from backend.db.base import utc_now_iso
from backend.db.models.event import EventModel
from backend.db.models.notification import (
    NotificationChangeModel,
    NotificationDeliveryModel,
    NotificationEventModel,
    NotificationModel,
    NotificationOutboxModel,
)
from backend.db.models.system import PushSubscriptionModel


@dataclass(frozen=True)
class ClaimedOutboxTask:
    """认领成功的 Outbox 任务凭证。"""

    task_id: str
    event_seq_id: int
    event_type: str
    payload: dict[str, Any]
    claimed_attempt_count: int
    locked_by: str
    claimed_at: str


def _parse_iso_to_ts(iso_str: str) -> float:
    """将 UTC ISO 8601 字符串转换为 UNIX 时间戳（秒）。"""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.timestamp()


def _format_now_offset_iso(offset_sec: int = 0) -> str:
    """生成带秒数偏移的 UTC ISO 8601 时间戳。"""
    now = datetime.now(timezone.utc)
    if offset_sec:
        from datetime import timedelta
        now += timedelta(seconds=offset_sec)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


class NotificationDispatcher:
    """业务事件到通知中心的可靠派发调度器。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        dispatcher_id: Optional[str] = None,
        now_func: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._session_factory = session_factory
        self.dispatcher_id = dispatcher_id or f"disp-{uuid.uuid4().hex[:8]}"
        self._now_func = now_func

    def _get_now_offset_iso(self, offset_sec: int) -> str:
        """基于当前调度时钟计算带秒数偏移的时间戳。"""
        now_str = self._now_func()
        ts = _parse_iso_to_ts(now_str)
        dt = datetime.fromtimestamp(ts + offset_sec, timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # -------------------------------------------------------------------------
    # 终态收敛与超时清理
    # -------------------------------------------------------------------------
    def clean_terminal_states(self, session: Session) -> int:
        """收敛超限终态记录（防止 processing 且租约过期超限或 retry 超限记录永久卡死）。"""
        now = self._now_func()
        stmt = (
            sa.update(NotificationOutboxModel)
            .where(
                sa.or_(
                    sa.and_(
                        NotificationOutboxModel.status == "processing",
                        NotificationOutboxModel.lease_expires_at < now,
                        NotificationOutboxModel.attempt_count >= NotificationOutboxModel.max_attempts,
                    ),
                    sa.and_(
                        NotificationOutboxModel.status == "retry",
                        NotificationOutboxModel.attempt_count >= NotificationOutboxModel.max_attempts,
                    ),
                )
            )
            .values(
                status="failed",
                failed_at=now,
                last_error=sa.func.coalesce(
                    NotificationOutboxModel.last_error,
                    "Maximum attempts reached with expired lease or retry limit",
                ),
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        result = session.execute(stmt)
        return int(result.rowcount)

    # -------------------------------------------------------------------------
    # 阶段一：认领短事务
    # -------------------------------------------------------------------------
    def claim_one_task(self) -> Optional[ClaimedOutboxTask]:
        """短事务模式认领一条就绪的 Outbox 记录并立即提交释放排他写锁。"""
        now = self._now_func()
        lease_until = self._get_now_offset_iso(60)  # Dispatcher 统一 60s 租约

        with self._session_factory() as session:
            try:
                # 显式 BEGIN IMMEDIATE 起步，获取排他写锁
                session.execute(text("BEGIN IMMEDIATE;"))

                # 1. 执行超限终态收敛清理
                self.clean_terminal_states(session)

                # 2. 扫描一条可认领任务：
                # (pending 未处理) OR (retry 就绪且未超限) OR (processing 租约过期且未超限接管)
                candidate = (
                    session.query(NotificationOutboxModel)
                    .filter(
                        NotificationOutboxModel.attempt_count < NotificationOutboxModel.max_attempts,
                        sa.or_(
                            sa.and_(
                                NotificationOutboxModel.status == "pending",
                                NotificationOutboxModel.next_retry_at <= now,
                            ),
                            sa.and_(
                                NotificationOutboxModel.status == "retry",
                                NotificationOutboxModel.next_retry_at <= now,
                            ),
                            sa.and_(
                                NotificationOutboxModel.status == "processing",
                                NotificationOutboxModel.lease_expires_at < now,
                            ),
                        ),
                    )
                    .order_by(NotificationOutboxModel.created_at.asc(), NotificationOutboxModel.id.asc())
                    .first()
                )

                if candidate is None:
                    session.commit()
                    return None

                # 3. 执行原子认领与 Fencing generation 递增（attempt_count = attempt_count + 1）
                target_id = candidate.id
                raw_payload = candidate.payload_json
                event_seq_id = candidate.event_seq_id
                event_type = candidate.event_type

                update_stmt = (
                    sa.update(NotificationOutboxModel)
                    .where(NotificationOutboxModel.id == target_id)
                    .values(
                        status="processing",
                        locked_by=self.dispatcher_id,
                        locked_at=now,
                        lease_expires_at=lease_until,
                        attempt_count=NotificationOutboxModel.attempt_count + 1,
                        updated_at=now,
                    )
                )
                session.execute(update_stmt)

                # 查询更新后的 attempt_count 作为本次 Claim 凭据
                new_attempt_count = session.query(NotificationOutboxModel.attempt_count).filter_by(id=target_id).scalar()

                session.commit()  # 提交事务，释放写锁！

                parsed_payload: dict[str, Any] = {}
                try:
                    parsed_payload = json.loads(raw_payload) if raw_payload else {}
                except Exception:
                    parsed_payload = {}

                return ClaimedOutboxTask(
                    task_id=target_id,
                    event_seq_id=event_seq_id,
                    event_type=event_type,
                    payload=parsed_payload,
                    claimed_attempt_count=int(new_attempt_count or 1),
                    locked_by=self.dispatcher_id,
                    claimed_at=now,
                )
            except Exception as exc:
                session.rollback()
                log_structured_error(
                    problem="Dispatcher 阶段一认领任务异常",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="事务已整体回滚并释放写锁；调度器将在下一周期重试。",
                    exc=exc,
                )
                raise

    # -------------------------------------------------------------------------
    # 阶段二：独立派生写入与 CAS 回写事务
    # -------------------------------------------------------------------------
    def materialize_and_commit(self, claim: ClaimedOutboxTask) -> bool:
        """独立事务内执行聚合判断、派生表写入与 CAS 回写。若失去执行权则整体回滚并返回 False。"""
        now = self._now_func()

        with self._session_factory() as session:
            try:
                # 显式 BEGIN IMMEDIATE 开启独立写事务
                session.execute(text("BEGIN IMMEDIATE;"))
                # 获得写锁后即刻刷新当前绝对时间，杜绝等待写锁阶段产生的时钟漂移
                now = self._now_func()

                # 1. 前置 Fencing 与租约有效性校验
                outbox_check = (
                    session.query(NotificationOutboxModel)
                    .filter(
                        NotificationOutboxModel.id == claim.task_id,
                        NotificationOutboxModel.status == "processing",
                        NotificationOutboxModel.locked_by == self.dispatcher_id,
                        NotificationOutboxModel.attempt_count == claim.claimed_attempt_count,
                        NotificationOutboxModel.lease_expires_at > now,
                    )
                    .first()
                )
                if outbox_check is None:
                    # 失去执行权（被接管或已过期），强制回滚零副作用
                    session.rollback()
                    return False

                # 2. 查询原始事件信息，获取 project_id、created_at 与 entity 信息
                raw_event = session.query(EventModel).filter_by(seq_id=claim.event_seq_id).first()
                event_created_at = raw_event.created_at if raw_event else now
                project_id = raw_event.project_id if raw_event else None
                entity_type = raw_event.entity_type if raw_event else "system"
                entity_id = raw_event.entity_id if raw_event else claim.task_id

                # 3. 语义映射：推导分类、优先级、标题与聚合键
                category = "system_alert"
                priority = "normal"
                payload_status = str(
                    claim.payload.get("platform_status")
                    or claim.payload.get("agent_reported_status")
                    or ""
                ).lower()

                if claim.event_type.startswith("decision."):
                    category = "decision_required"
                    priority = "high"
                elif claim.event_type == "task.blocked" or payload_status == "blocked":
                    category = "task_blocked"
                    priority = "high"
                elif "failed" in claim.event_type or payload_status == "failed":
                    category = "execution_failed"
                    priority = "urgent"
                elif "completed" in claim.event_type or payload_status == "completed":
                    category = "task_completed"
                    priority = "normal"

                aggregation_key = f"{category}:{entity_type}:{entity_id}"
                title = claim.payload.get("title") or f"事件通知：{claim.event_type}"
                body = claim.payload.get("note") or claim.payload.get("error") or f"业务事件已生成：{claim.event_type}"
                link_url = f"/projects/{project_id}" if project_id else "/"

                # 4. 10 秒通知聚合窗口判断（严格按照修正 P53-05 闭区间与时序）
                target_notif_id: str
                is_change_created = False

                # 查询当前聚合键是否存在开放窗口
                open_notif_query = session.query(NotificationModel).filter(
                    NotificationModel.aggregation_key == aggregation_key,
                    NotificationModel.is_window_open == 1,
                )
                if project_id is not None:
                    open_notif_query = open_notif_query.filter(NotificationModel.project_id == project_id)
                else:
                    open_notif_query = open_notif_query.filter(NotificationModel.project_id.is_(None))

                open_notif = open_notif_query.first()

                if open_notif is not None:
                    # 计算事件时间差 delta_t = T_event - window_started_at
                    event_ts = _parse_iso_to_ts(event_created_at)
                    window_ts = _parse_iso_to_ts(open_notif.window_started_at)
                    delta_t = event_ts - window_ts

                    if 0.0 <= delta_t <= 10.0 and open_notif.aggregated_count < 20 and open_notif.is_read == 0:
                        # 4A. 落在闭区间 [0, 10s] 且未读未饱和：合流聚合
                        open_notif.aggregated_count += 1
                        open_notif.body = f"{open_notif.body}\n[{event_created_at}] {body}"
                        open_notif.updated_at = now
                        target_notif_id = open_notif.id
                        is_change_created = False
                    elif delta_t < 0.0:
                        # 4B. 负向乱序迟到事件：独立建表且即刻关闭，保证时间轴单向演进
                        target_notif_id = str(uuid.uuid4())
                        new_late_notif = NotificationModel(
                            id=target_notif_id,
                            project_id=project_id,
                            category=category,
                            priority=priority,
                            title=f"[历史补发] {title}",
                            body=body,
                            link_url=link_url,
                            target_entity_type=entity_type,
                            target_entity_id=entity_id,
                            aggregation_key=aggregation_key,
                            aggregated_count=1,
                            window_started_at=event_created_at,
                            is_window_open=0,  # 即建即关，终态不开放
                            is_read=0,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(new_late_notif)
                        session.flush()
                        is_change_created = True
                    else:
                        # 4C. 超出 10s 或条数满 20：先关闭旧窗口，再创建新通知（严密时序防唯一键冲突）
                        open_notif.is_window_open = 0
                        open_notif.updated_at = now
                        session.flush()  # 刷盘使旧窗口释放 uq_notifications_open_agg 占用

                        target_notif_id = str(uuid.uuid4())
                        new_notif = NotificationModel(
                            id=target_notif_id,
                            project_id=project_id,
                            category=category,
                            priority=priority,
                            title=title,
                            body=body,
                            link_url=link_url,
                            target_entity_type=entity_type,
                            target_entity_id=entity_id,
                            aggregation_key=aggregation_key,
                            aggregated_count=1,
                            window_started_at=event_created_at,
                            is_window_open=1,
                            is_read=0,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(new_notif)
                        session.flush()
                        is_change_created = True
                else:
                    # 4D. 不存在开放窗口：直接创建新开放通知
                    target_notif_id = str(uuid.uuid4())
                    new_notif = NotificationModel(
                        id=target_notif_id,
                        project_id=project_id,
                        category=category,
                        priority=priority,
                        title=title,
                        body=body,
                        link_url=link_url,
                        target_entity_type=entity_type,
                        target_entity_id=entity_id,
                        aggregation_key=aggregation_key,
                        aggregated_count=1,
                        window_started_at=event_created_at,
                        is_window_open=1,
                        is_read=0,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(new_notif)
                    session.flush()
                    is_change_created = True

                # 5. 写入 notification_events 关联溯源表（受 event_seq_id UNIQUE 防重保护）
                notif_event = NotificationEventModel(
                    id=str(uuid.uuid4()),
                    notification_id=target_notif_id,
                    event_seq_id=claim.event_seq_id,
                    created_at=now,
                )
                session.add(notif_event)
                session.flush()

                # 6. 仅在创建新通知时批量生成 notification_deliveries（聚合更新时沿用首发通知的既有 Deliveries，避免触发 uq_notif_deliveries_notif_sub 唯一冲突）
                if is_change_created:
                    active_subs = (
                        session.query(PushSubscriptionModel.subscription_id, PushSubscriptionModel.device_id)
                        .filter(PushSubscriptionModel.is_active == 1)
                        .all()
                    )
                    for sub_id, dev_id in active_subs:
                        delivery = NotificationDeliveryModel(
                            id=str(uuid.uuid4()),
                            notification_id=target_notif_id,
                            subscription_id=sub_id,
                            device_id=dev_id,
                            status="pending",
                            attempt_count=0,
                            max_attempts=4,
                            next_retry_at=now,
                            created_at=now,
                            updated_at=now,
                            )
                        session.add(delivery)
                    session.flush()

                # 7. 写入 notification_changes 变更流（供 SSE 消费）
                change_record = NotificationChangeModel(
                    project_id=project_id,
                    change_type="created" if is_change_created else "updated",
                    notification_id=target_notif_id,
                    payload_json=json.dumps({"title": title, "category": category}, ensure_ascii=False),
                    created_at=now,
                )
                session.add(change_record)
                session.flush()

                # 8. 后置 Fencing CAS 回写：重新获取此时绝对时间，断言身份、generation 与租约有效性
                commit_now = self._now_func()
                cas_stmt = (
                    sa.update(NotificationOutboxModel)
                    .where(
                        NotificationOutboxModel.id == claim.task_id,
                        NotificationOutboxModel.status == "processing",
                        NotificationOutboxModel.locked_by == self.dispatcher_id,
                        NotificationOutboxModel.attempt_count == claim.claimed_attempt_count,
                        NotificationOutboxModel.lease_expires_at > commit_now,
                    )
                    .values(
                        status="processed",
                        processed_at=commit_now,
                        locked_by=None,
                        locked_at=None,
                        lease_expires_at=None,
                        updated_at=commit_now,
                    )
                )
                cas_res = session.execute(cas_stmt)

                if cas_res.rowcount != 1:
                    # 失去执行权，强制整事务回滚，派生表零写入零残留
                    session.rollback()
                    return False

                # 提交阶段二事务，持久化所有派生结果
                session.commit()
                return True
            except Exception as exc:
                session.rollback()
                log_structured_error(
                    problem="Dispatcher 阶段二派生写入异常回滚",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="事务已强制整体回滚；Outbox 保持未完成状态以便重试。",
                    exc=exc,
                )
                raise

    # -------------------------------------------------------------------------
    # 调度驱动方法（支持单步与批量）
    # -------------------------------------------------------------------------
    def dispatch_one_task(self) -> bool:
        """执行单条任务调度（阶段一短事务认领 -> 阶段二独立派生提交）。"""
        claim = self.claim_one_task()
        if claim is None:
            return False
        return self.materialize_and_commit(claim)

    def dispatch_batch(self, max_tasks: int = 10) -> int:
        """批量调度处理就绪任务，返回成功处理的条数。"""
        processed_count = 0
        for _ in range(max_tasks):
            if not self.dispatch_one_task():
                break
            processed_count += 1
        return processed_count

    def dispatch_with_retry(self, max_tasks: int = 10) -> int:
        """带 Full Jitter 重试的调度循环（初次执行 + 最多 3 次重试，最大锁等待预算 60s）。"""
        attempts = 0
        max_attempts = 4  # 初次 + 3 次重试

        while attempts < max_attempts:
            attempts += 1
            try:
                return self.dispatch_batch(max_tasks)
            except Exception as exc:
                # 捕获 SQLite 锁争用异常
                err_str = str(exc).lower()
                is_busy = "database is locked" in err_str or "busy" in err_str
                if is_busy and attempts < max_attempts:
                    # 严格 Full Jitter 退避: delay = random(0, min(cap, base * 2^n))
                    n = attempts - 1
                    cap = 0.3
                    base = 0.05
                    delay = random.uniform(0.0, min(cap, base * (2**n)))
                    time.sleep(delay)
                    continue
                # 超出重试次数或非锁错误
                log_structured_error(
                    problem="Dispatcher 批处理重试超限或发生不可恢复异常",
                    cause=f"Attempts={attempts}/{max_attempts}, Error: {exc}",
                    fix="调度器安全退出当前周期，等待外部守护周期自愈。",
                    exc=exc,
                )
                return 0
        return 0
