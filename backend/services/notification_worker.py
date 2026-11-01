# -*- coding: utf-8 -*-
"""Notification Delivery Worker 投递引擎服务。

核心职责：
1. 认领引擎（短事务认领）：以 BEGIN IMMEDIATE 扫描认领就绪的 notification_deliveries，
   严格按“已认领并启动的工作尝试次数”累加 attempt_count（包含外呼前崩溃，租约接管计入新尝试），
   设置 30s 租约后立即提交释放 SQLite 排他写锁；
2. 状态机流转与 Mock 适配器隔离（Phase 5.3 严格使用 Mock/Stub 适配器，绝不发起真实网络外呼）：
   - 201: delivered 终态，更新订阅活跃时间；
   - 410: gone 终态，同事务停用订阅（is_active = 0）；
   - 404: 同一 Delivery 连续第 2 次 404 视同 410 置 gone 并停用订阅；首次 404 记录并 retry；
   - 5xx / 429 / 暂时性错误: 指数退避重试（2s/8s/32s），超限转 failed 终态；
3. 全路径 Fencing CAS 回写与租约有效性：回写条件必须携带 lease_expires_at > :now 与 attempt_count，
   影响行数必须为 1，若为 0 强制整体回滚，严禁停用订阅或产生任何副作用；
4. 超限终态收敛：将 attempt_count >= 4 且租约过期的任务收敛为 failed；
5. 并发防死锁：遵循 SQLite 单写者机制，支持带 Full Jitter 的有限重试（最多 4 次尝试，最大锁等待 60s）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random
import time
from typing import Any, Callable, Optional, Protocol
import uuid

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.core.logger import log_structured_error
from backend.db.base import utc_now_iso
from backend.db.models.notification import NotificationDeliveryModel
from backend.db.models.system import PushSubscriptionModel


@dataclass(frozen=True)
class DeliveryResponse:
    """网关外呼响应（Mock 适配器）。"""

    status_code: int
    error_message: Optional[str] = None


class WebPushAdapter(Protocol):
    """Web Push 外呼适配器协议（Phase 5.3 严格为 Mock/Stub）。"""

    def send(self, delivery_id: str, endpoint: str, payload_json: str) -> DeliveryResponse:
        ...


class MockWebPushAdapter:
    """Phase 5.3 默认测试 Mock 适配器（支持可配置响应码）。"""

    def __init__(self, default_status_code: int = 201) -> None:
        self.default_status_code = default_status_code
        self.call_history: list[dict[str, Any]] = []

    def send(self, delivery_id: str, endpoint: str, payload_json: str) -> DeliveryResponse:
        self.call_history.append({"delivery_id": delivery_id, "endpoint": endpoint})
        if self.default_status_code == 201:
            return DeliveryResponse(status_code=201)
        elif self.default_status_code == 404:
            return DeliveryResponse(status_code=404, error_message="Push subscription not found")
        elif self.default_status_code == 410:
            return DeliveryResponse(status_code=410, error_message="Push subscription gone")
        elif self.default_status_code == 429:
            return DeliveryResponse(status_code=429, error_message="Rate limited")
        else:
            return DeliveryResponse(status_code=self.default_status_code, error_message="Gateway error")


@dataclass(frozen=True)
class ClaimedDeliveryTask:
    """认领成功的 Delivery 任务凭证。"""

    delivery_id: str
    notification_id: str
    subscription_id: str
    device_id: str
    claimed_attempt_count: int
    locked_by: str
    last_http_status: Optional[int]


def _parse_iso_to_ts(iso_str: str) -> float:
    """将 UTC ISO 8601 字符串转换为 UNIX 时间戳（秒）。"""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.timestamp()


class NotificationDeliveryWorker:
    """按订阅投递外呼的 Worker 工作单元服务。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        worker_id: Optional[str] = None,
        adapter: Optional[WebPushAdapter] = None,
        now_func: Callable[[], str] = utc_now_iso,
    ) -> None:
        self._session_factory = session_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.adapter = adapter or MockWebPushAdapter(default_status_code=201)
        self._now_func = now_func

    def _get_now_offset_iso(self, offset_sec: int) -> str:
        """基于当前时钟计算带秒数偏移的时间戳。"""
        now_str = self._now_func()
        ts = _parse_iso_to_ts(now_str)
        dt = datetime.fromtimestamp(ts + offset_sec, timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # -------------------------------------------------------------------------
    # 终态收敛清理
    # -------------------------------------------------------------------------
    def clean_terminal_states(self, session: Session) -> int:
        """收敛超限终态记录（防止 processing 超时超限或 retry 超限记录永久卡死）。"""
        now = self._now_func()
        stmt = (
            sa.update(NotificationDeliveryModel)
            .where(
                sa.or_(
                    sa.and_(
                        NotificationDeliveryModel.status == "processing",
                        NotificationDeliveryModel.lease_expires_at < now,
                        NotificationDeliveryModel.attempt_count >= NotificationDeliveryModel.max_attempts,
                    ),
                    sa.and_(
                        NotificationDeliveryModel.status == "retry",
                        NotificationDeliveryModel.attempt_count >= NotificationDeliveryModel.max_attempts,
                    ),
                )
            )
            .values(
                status="failed",
                failed_at=now,
                last_error=sa.func.coalesce(
                    NotificationDeliveryModel.last_error,
                    "Delivery attempts exceeded max_attempts",
                ),
                locked_by=None,
                locked_at=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        res = session.execute(stmt)
        return int(res.rowcount)

    # -------------------------------------------------------------------------
    # 阶段一：短事务原子认领
    # -------------------------------------------------------------------------
    def claim_one_task(self) -> Optional[ClaimedDeliveryTask]:
        """短事务模式认领一条就绪的 Delivery 任务，累加 attempt_count 并释放排他写锁。"""
        now = self._now_func()
        lease_until = self._get_now_offset_iso(30)  # Delivery 统一 30s 租约

        with self._session_factory() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE;"))

                # 1. 终态收敛
                self.clean_terminal_states(session)

                # 2. 扫描一条可认领任务
                candidate = (
                    session.query(NotificationDeliveryModel)
                    .filter(
                        NotificationDeliveryModel.attempt_count < NotificationDeliveryModel.max_attempts,
                        sa.or_(
                            sa.and_(
                                NotificationDeliveryModel.status == "pending",
                                NotificationDeliveryModel.next_retry_at <= now,
                            ),
                            sa.and_(
                                NotificationDeliveryModel.status == "retry",
                                NotificationDeliveryModel.next_retry_at <= now,
                            ),
                            sa.and_(
                                NotificationDeliveryModel.status == "processing",
                                NotificationDeliveryModel.lease_expires_at < now,
                            ),
                        ),
                    )
                    .order_by(NotificationDeliveryModel.created_at.asc(), NotificationDeliveryModel.id.asc())
                    .first()
                )

                if candidate is None:
                    session.commit()
                    return None

                target_id = candidate.id
                notif_id = candidate.notification_id
                sub_id = candidate.subscription_id
                dev_id = candidate.device_id
                last_http_status = candidate.last_http_status

                # 3. 认领并累加 attempt_count（Fencing generation）
                session.execute(
                    sa.update(NotificationDeliveryModel)
                    .where(NotificationDeliveryModel.id == target_id)
                    .values(
                        status="processing",
                        locked_by=self.worker_id,
                        locked_at=now,
                        lease_expires_at=lease_until,
                        attempt_count=NotificationDeliveryModel.attempt_count + 1,
                        updated_at=now,
                    )
                )
                new_attempt_count = (
                    session.query(NotificationDeliveryModel.attempt_count).filter_by(id=target_id).scalar()
                )
                session.commit()  # 提交短事务，释放写锁！

                return ClaimedDeliveryTask(
                    delivery_id=target_id,
                    notification_id=notif_id,
                    subscription_id=sub_id,
                    device_id=dev_id,
                    claimed_attempt_count=int(new_attempt_count or 1),
                    locked_by=self.worker_id,
                    last_http_status=last_http_status,
                )
            except Exception as exc:
                session.rollback()
                log_structured_error(
                    problem="Delivery Worker 阶段一认领任务异常",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="事务已回滚并释放写锁；Worker 将在下一个周期重新认领。",
                    exc=exc,
                )
                raise

    # -------------------------------------------------------------------------
    # 阶段二：Mock 外呼与带租约 CAS 回写独立事务
    # -------------------------------------------------------------------------
    def execute_and_commit(self, claim: ClaimedDeliveryTask) -> bool:
        """调用适配器模拟发送，并在独立写事务内带 lease_expires_at > :now 执行 Fencing CAS 回写。"""
        now = self._now_func()

        # 1. 模拟调用 Web Push 适配器（严格 Mock/Stub，Phase 5.3 绝不发起真实网络外呼）
        resp = self.adapter.send(
            delivery_id=claim.delivery_id,
            endpoint="https://mock.gateway/wpush",
            payload_json="{}",
        )
        status_code = resp.status_code
        error_msg = resp.error_message

        # 2. 开启独立写事务执行状态机流转与 Fencing CAS 回写
        with self._session_factory() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE;"))
                # 外呼结束且成功获得写锁后即刻刷新当前绝对时间，确保 Fencing 租约判定与时间戳精准反映锁内时钟
                now = self._now_func()

                # 判定状态机目标状态与重试参数
                target_status = "delivered"
                delivered_at = None
                failed_at = None
                next_retry = now
                deactivate_subscription = False

                if status_code == 201:
                    # 分支 A: 投递成功终态
                    target_status = "delivered"
                    delivered_at = now
                    error_msg = None
                elif status_code == 410:
                    # 分支 B: 明确失效终态，同事务停用订阅
                    target_status = "gone"
                    failed_at = now
                    deactivate_subscription = True
                elif status_code == 404:
                    # 分支 C: 404 判断（同一 Delivery 内连续第 2 次 404 视同 410）
                    if claim.last_http_status == 404:
                        target_status = "gone"
                        failed_at = now
                        deactivate_subscription = True
                        error_msg = "Consecutive 404 responses; subscription considered gone"
                    else:
                        # 首次 404：进入 retry，记录 last_http_status=404
                        if claim.claimed_attempt_count < 4:
                            target_status = "retry"
                            backoff_sec = 2 * (4 ** (claim.claimed_attempt_count - 1))
                            next_retry = self._get_now_offset_iso(backoff_sec)
                        else:
                            target_status = "failed"
                            failed_at = now
                else:
                    # 分支 D: 5xx / 429 / 暂时性网络错误
                    if claim.claimed_attempt_count < 4:
                        target_status = "retry"
                        backoff_sec = 2 * (4 ** (claim.claimed_attempt_count - 1))
                        next_retry = self._get_now_offset_iso(backoff_sec)
                    else:
                        target_status = "failed"
                        failed_at = now

                # 3. 执行严格携带 lease_expires_at > :now 的 Fencing CAS 回写
                cas_stmt = (
                    sa.update(NotificationDeliveryModel)
                    .where(
                        NotificationDeliveryModel.id == claim.delivery_id,
                        NotificationDeliveryModel.status == "processing",
                        NotificationDeliveryModel.locked_by == self.worker_id,
                        NotificationDeliveryModel.attempt_count == claim.claimed_attempt_count,
                        NotificationDeliveryModel.lease_expires_at > now,  # 租约有效期强校验
                    )
                    .values(
                        status=target_status,
                        last_http_status=status_code,
                        last_error=error_msg,
                        delivered_at=delivered_at,
                        failed_at=failed_at,
                        next_retry_at=next_retry,
                        locked_by=None,
                        locked_at=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                )
                cas_res = session.execute(cas_stmt)

                if cas_res.rowcount != 1:
                    # 失去执行权（卡顿超时失效或被接管）：强制回滚，严禁停用订阅或更新状态，零副作用！
                    session.rollback()
                    return False

                # 4. 同事务强绑定操作：仅当 CAS 确认执行权有效时才执行
                if deactivate_subscription:
                    # 停用订阅
                    session.execute(
                        sa.update(PushSubscriptionModel)
                        .where(PushSubscriptionModel.subscription_id == claim.subscription_id)
                        .values(is_active=0)
                    )
                elif target_status == "delivered":
                    # 刷新订阅最后活跃推送时间
                    session.execute(
                        sa.update(PushSubscriptionModel)
                        .where(PushSubscriptionModel.subscription_id == claim.subscription_id)
                        .values(last_pushed_at=now)
                    )

                session.commit()
                return True
            except Exception as exc:
                session.rollback()
                log_structured_error(
                    problem="Delivery Worker 阶段二回写异常",
                    cause=f"{type(exc).__name__}: {exc}",
                    fix="事务已整体回滚；Delivery 状态与订阅状态均未改变。",
                    exc=exc,
                )
                raise

    # -------------------------------------------------------------------------
    # 单步调度与重试驱动
    # -------------------------------------------------------------------------
    def process_one_task(self) -> bool:
        """执行单条投递认领与处理。"""
        claim = self.claim_one_task()
        if claim is None:
            return False
        return self.execute_and_commit(claim)

    def process_batch(self, max_tasks: int = 10) -> int:
        """批量处理就绪的投递任务。"""
        count = 0
        for _ in range(max_tasks):
            if not self.process_one_task():
                break
            count += 1
        return count

    def process_with_retry(self, max_tasks: int = 10) -> int:
        """带 Full Jitter 重试的 Worker 批处理（初次 + 最多 3 次重试，最大锁等待 60s）。"""
        attempts = 0
        max_attempts = 4

        while attempts < max_attempts:
            attempts += 1
            try:
                return self.process_batch(max_tasks)
            except Exception as exc:
                err_str = str(exc).lower()
                is_busy = "database is locked" in err_str or "busy" in err_str
                if is_busy and attempts < max_attempts:
                    n = attempts - 1
                    delay = random.uniform(0.0, min(0.3, 0.05 * (2**n)))
                    time.sleep(delay)
                    continue
                log_structured_error(
                    problem="Delivery Worker 批处理重试超限或发生不可恢复异常",
                    cause=f"Attempts={attempts}/{max_attempts}, Error: {exc}",
                    fix="Worker 安全退出当前周期，等待外部调度循环重试。",
                    exc=exc,
                )
                return 0
        return 0

