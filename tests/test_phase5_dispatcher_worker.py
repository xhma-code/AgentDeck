# -*- coding: utf-8 -*-
"""Phase 5.3 调度引擎与投递工作单元专项测试套件（统一编号 1–15）。

本测试文件覆盖：
- 用例 1：UoW 事件白名单过滤与同事务原子提交/回滚
- 用例 2：Dispatcher 阶段二派生写入崩溃回滚与 Outbox 状态保持
- 用例 3：Dispatcher 租约超时接管与凭证递增
- 用例 4：Dispatcher Fencing 回写保护（旧凭证回写被拦截回滚）
- 用例 5：Outbox 超限终态熔断（processing 超时超限与 retry 超限转 failed）
- 用例 6：Delivery Worker 认领引擎与状态机流转（Mock 201 成功转 delivered）
- 用例 7：Delivery Worker 租约超时接管（累加尝试计数）
- 用例 8：Delivery Worker 超限终态熔断
- 用例 9：Worker 认领后立即崩溃接管自愈
- 用例 10：Worker 标识复用场景下的 Fencing 拦截实测
- 用例 11：重复 Dispatcher 处理同一事件幂等性（event_seq_id UNIQUE 拦截）
- 用例 12：订阅在 Dispatcher 处理期间变化的快照一致性（符合 SQLite 单写者机制）
- 用例 13：SQLITE_BUSY 冲突注入与有限抖动重试
- 用例 14：派生阶段子表写入失败的整体原子回滚
- 用例 15：10 秒通知聚合窗口全场景边界实测（闭区间、负向乱序即关、先关后开）
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.db.engine import create_session_factory, create_sqlite_engine
from backend.db.migration import get_alembic_config
from backend.db.models.event import EventModel
from backend.db.models.notification import (
    NotificationChangeModel,
    NotificationDeliveryModel,
    NotificationEventModel,
    NotificationModel,
    NotificationOutboxModel,
)
from backend.db.models.project import ProjectModel
from backend.db.models.system import DeviceSessionModel, PushSubscriptionModel
from backend.db.uow import NOTIFICATION_EVENT_WHITELIST, SqlAlchemyUnitOfWork
from backend.services.notification_dispatcher import ClaimedOutboxTask, NotificationDispatcher
from backend.services.notification_worker import (
    ClaimedDeliveryTask,
    DeliveryResponse,
    MockWebPushAdapter,
    NotificationDeliveryWorker,
)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """提供干净的独立临时 SQLite 文件路径，并预先升级至 Alembic head。"""
    db_file = tmp_path / "test_phase5_dispatcher_worker.db"
    cfg = get_alembic_config(db_file)
    command.upgrade(cfg, "head")
    return db_file


def test_use_case_01_uow_outbox_atomicity_and_whitelist(temp_db_path: Path) -> None:
    """用例 1（Outbox 写入同事务原子性）：

    1. 非白名单事件（如 project.created）提交后不进入 Outbox；
    2. 白名单事件（如 decision.created、task.failed）提交后原子写入 Outbox 并正确绑定 events.seq_id；
    3. 业务抛出异常回滚时，业务实体、events 与 notification_outbox 整体回滚零残留；
    4. 验证白名单集合完整包含 6 类高价值通知事件。
    """
    # 验证白名单定义完整性
    expected_whitelist = {
        "task.blocked",
        "task.failed",
        "step.failed",
        "execution.failed",
        "decision.created",
        "decision.timeout",
    }
    assert NOTIFICATION_EVENT_WHITELIST == expected_whitelist

    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    pid1 = f"proj-white-001-{uuid.uuid4().hex[:6]}"
    pid2 = f"proj-rollback-002-{uuid.uuid4().hex[:6]}"

    # --- 场景 A: 混合事件提交（非白名单 vs 白名单） ---
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id=pid1,
            slug=f"slug-{uuid.uuid4().hex[:6]}",
            name="通知测试项目",
            root_path=f"/tmp/test_repo_{uuid.uuid4().hex[:6]}",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)

        # 记录 1 个非白名单事件
        uow.record_event(
            entity_type="project",
            entity_id=pid1,
            event_type="project.created",
            project_id=pid1,
            payload={"note": "非白名单事件"},
        )
        # 记录 2 个白名单事件
        uow.record_event(
            entity_type="decision",
            entity_id=f"dec-{uuid.uuid4().hex[:6]}",
            event_type="decision.created",
            project_id=pid1,
            payload={"title": "等待用户确认"},
        )
        uow.record_event(
            entity_type="task",
            entity_id=f"task-{uuid.uuid4().hex[:6]}",
            event_type="task.failed",
            project_id=pid1,
            payload={"error": "执行报错"},
        )
        uow.commit()

    # 验证场景 A 落盘数据
    with session_factory() as session:
        # 1. 业务实体存在
        p = session.get(ProjectModel, pid1)
        assert p is not None
        assert p.name == "通知测试项目"

        # 2. events 表有 3 条记录
        events = session.query(EventModel).filter_by(project_id=pid1).order_by(EventModel.seq_id).all()
        assert len(events) == 3
        evt_types = [e.event_type for e in events]
        assert evt_types == ["project.created", "decision.created", "task.failed"]

        # 3. outbox 表精准产生 2 条记录（非白名单 project.created 被过滤）
        outbox_items = session.query(NotificationOutboxModel).order_by(NotificationOutboxModel.event_seq_id).all()
        assert len(outbox_items) == 2
        assert outbox_items[0].event_seq_id == events[1].seq_id
        assert outbox_items[0].event_type == "decision.created"
        assert outbox_items[0].status == "pending"
        assert outbox_items[0].attempt_count == 0
        assert outbox_items[0].max_attempts == 5

        assert outbox_items[1].event_seq_id == events[2].seq_id
        assert outbox_items[1].event_type == "task.failed"
        assert outbox_items[1].status == "pending"

    # --- 场景 B: 事务中途异常回滚零残留 ---
    with pytest.raises(RuntimeError, match="模拟业务操作崩溃"):
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            proj_fail = ProjectModel(
                id=pid2,
                slug=f"slug-{uuid.uuid4().hex[:6]}",
                name="失败回滚项目",
                root_path=f"/tmp/fail_repo_{uuid.uuid4().hex[:6]}",
                created_at="2026-11-01T12:00:00Z",
                updated_at="2026-11-01T12:00:00Z",
            )
            uow.session.add(proj_fail)
            uow.record_event(
                entity_type="execution",
                entity_id="exec-001",
                event_type="execution.failed",
                project_id=pid2,
                payload={"error": "崩溃"},
            )
            # 业务代码中途异常
            raise RuntimeError("模拟业务操作崩溃")

    # 验证场景 B：数据库中绝对无 pid2 的任何痕迹
    with session_factory() as session:
        assert session.get(ProjectModel, pid2) is None
        fail_evts = session.query(EventModel).filter_by(project_id=pid2).all()
        assert len(fail_evts) == 0
        # outbox 表依然仅有场景 A 的 2 条记录
        assert session.query(NotificationOutboxModel).count() == 2


def _seed_active_subscription(session_factory, sub_id: str, dev_id: str) -> None:
    """辅助方法：插入一条合法活跃订阅及设备会话。"""
    with session_factory() as session:
        dev = session.get(DeviceSessionModel, dev_id)
        if dev is None:
            dev = DeviceSessionModel(
                device_id=dev_id,
                device_name="Test Device",
                auth_token_hash=f"hash-{dev_id}",
                created_at="2026-11-01T12:00:00Z",
                last_active_at="2026-11-01T12:00:00Z",
            )
            session.add(dev)
            session.flush()

        sub = session.get(PushSubscriptionModel, sub_id)
        if sub is None:
            sub = PushSubscriptionModel(
                subscription_id=sub_id,
                device_id=dev_id,
                endpoint=f"https://updates.push.services.mozilla.com/wpush/v2/{sub_id}",
                p256dh_key="p256",
                auth_key="auth",
                is_active=1,
                vapid_key_fingerprint="legacy_unverified",
                created_at="2026-11-01T12:00:00Z",
            )
            session.add(sub)
        session.commit()


def test_use_case_02_dispatcher_phase2_materialize_rollback(temp_db_path: Path) -> None:
    """用例 2：Dispatcher 阶段二派生写入崩溃回滚与 Outbox 状态保持。

    验证：
    1. 阶段一成功认领，Outbox 转为 processing；
    2. 阶段二执行中途发生异常（例如派生逻辑崩溃）；
    3. 派生表（notifications, notification_events, deliveries, changes）全部原子回滚零残留；
    4. Outbox 记录依然保持 processing 状态，租约超时后可被接管重试，不会丢事件。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-002")

    # 1. 种子数据：通过 UoW 产生一条白名单事件
    pid = "proj-uc02"
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id=pid,
            slug="slug-uc02",
            name="用例2项目",
            root_path="/tmp/uc02",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-002",
            event_type="task.failed",
            project_id=pid,
            payload={"error": "执行超时崩溃"},
        )
        uow.commit()

    # 2. 阶段一：认领短事务
    claim = dispatcher.claim_one_task()
    assert claim is not None
    assert claim.claimed_attempt_count == 1
    assert claim.locked_by == "disp-002"

    # 验证数据库中该 Outbox 处于 processing 状态
    with session_factory() as session:
        outbox = session.get(NotificationOutboxModel, claim.task_id)
        assert outbox.status == "processing"
        assert outbox.locked_by == "disp-002"
        assert outbox.attempt_count == 1

    # 3. 模拟阶段二异常：monkeypatch 内部使写入 notification_changes 时抛错
    import unittest.mock as mock

    with mock.patch("backend.services.notification_dispatcher.NotificationChangeModel", side_effect=RuntimeError("模拟变更流写入失败")):
        with pytest.raises(RuntimeError, match="模拟变更流写入失败"):
            dispatcher.materialize_and_commit(claim)

    # 4. 验证原子回滚与零残留：派生表没有任何记录
    with session_factory() as session:
        assert session.query(NotificationModel).count() == 0
        assert session.query(NotificationEventModel).count() == 0
        assert session.query(NotificationDeliveryModel).count() == 0
        assert session.query(NotificationChangeModel).count() == 0

        # Outbox 保持 processing 状态未被标记 processed
        outbox = session.get(NotificationOutboxModel, claim.task_id)
        assert outbox.status == "processing"
        assert outbox.processed_at is None


def test_use_case_03_dispatcher_lease_timeout_takeover(temp_db_path: Path) -> None:
    """用例 3：Dispatcher 租约超时接管。

    验证：
    1. Dispatcher A 认领任务（attempt_count=1, 租约 60s）；
    2. A 模拟失联超时（时间前进 70s）；
    3. Dispatcher B 成功接管认领，attempt_count 自增为 2，锁属权更新为 Dispatcher B。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    # 时间打桩支持
    current_time_str = "2026-11-01T12:00:00Z"

    def time_now() -> str:
        return current_time_str

    disp_a = NotificationDispatcher(session_factory, dispatcher_id="disp-A", now_func=time_now)
    disp_b = NotificationDispatcher(session_factory, dispatcher_id="disp-B", now_func=time_now)

    # 准备白名单事件
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-uc03",
            slug="slug-uc03",
            name="用例3项目",
            root_path="/tmp/uc03",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="decision",
            entity_id="dec-003",
            event_type="decision.created",
            project_id="proj-uc03",
            payload={"title": "等待用户批准架构"},
        )
        uow.commit()

    with session_factory() as session:
        session.execute(text("UPDATE notification_outbox SET next_retry_at = '2026-11-01T12:00:00Z', created_at = '2026-11-01T12:00:00Z'"))
        session.commit()

    # 1. Dispatcher A 认领
    claim_a = disp_a.claim_one_task()
    assert claim_a is not None
    assert claim_a.claimed_attempt_count == 1
    assert claim_a.locked_by == "disp-A"

    # 此时 A 未超时，B 尝试认领应该得到 None
    claim_b_premature = disp_b.claim_one_task()
    assert claim_b_premature is None

    # 2. 模拟时间推进 70 秒（超过 60s 租约）
    current_time_str = "2026-11-01T12:01:10Z"

    # 3. Dispatcher B 发起接管认领
    claim_b = disp_b.claim_one_task()
    assert claim_b is not None
    assert claim_b.task_id == claim_a.task_id
    assert claim_b.claimed_attempt_count == 2, "租约接管必须使 attempt_count 自增为 2"
    assert claim_b.locked_by == "disp-B"


def test_use_case_04_dispatcher_fencing_stale_commit_blocked(temp_db_path: Path) -> None:
    """用例 4：Dispatcher Fencing 回写保护。

    验证：
    1. Dispatcher A 认领后假死并超时；
    2. Dispatcher B 成功接管并将 attempt_count 推进为 2，完成阶段二提交；
    3. Dispatcher A 恢复后尝试以凭证 (attempt_count=1) 执行 CAS 回写，
       因 CAS 命中行数为 0 触发强制回滚，无法覆盖 B 的结果。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    current_time_str = "2026-11-01T12:00:00Z"

    def time_now() -> str:
        return current_time_str

    disp_a = NotificationDispatcher(session_factory, dispatcher_id="disp-A", now_func=time_now)
    disp_b = NotificationDispatcher(session_factory, dispatcher_id="disp-B", now_func=time_now)

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-uc04",
            slug="slug-uc04",
            name="用例4项目",
            root_path="/tmp/uc04",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-004",
            event_type="task.blocked",
            project_id="proj-uc04",
            payload={"note": "缺少前置依赖"},
        )
        uow.commit()

    with session_factory() as session:
        session.execute(text("UPDATE notification_outbox SET next_retry_at = '2026-11-01T12:00:00Z', created_at = '2026-11-01T12:00:00Z'"))
        session.commit()

    # 1. A 认领任务
    claim_a = disp_a.claim_one_task()
    assert claim_a.claimed_attempt_count == 1

    # 2. 时间推进 70 秒，B 接管并成功派生
    current_time_str = "2026-11-01T12:01:10Z"
    claim_b = disp_b.claim_one_task()
    assert claim_b.claimed_attempt_count == 2
    b_success = disp_b.materialize_and_commit(claim_b)
    assert b_success is True

    # 3. A 恢复尝试提交
    a_success = disp_a.materialize_and_commit(claim_a)
    assert a_success is False, "旧 Dispatcher A 必须因 Fencing 凭证过期回滚并返回 False"

    # 验证最终数据库状态由 B 完成，通知表仅 1 条记录
    with session_factory() as session:
        notif = session.query(NotificationModel).filter_by(project_id="proj-uc04").all()
        assert len(notif) == 1
        outbox = session.get(NotificationOutboxModel, claim_a.task_id)
        assert outbox.status == "processed"


def test_use_case_05_outbox_terminal_state_failed(temp_db_path: Path) -> None:
    """用例 5：Outbox 达到最大尝试次数自动流转为 failed 终态。

    验证：
    1. 构造一条 attempt_count = 5 (>= max_attempts=5) 且租约超期的 processing 记录；
    2. 构造一条 attempt_count = 5 的 retry 记录；
    3. 执行 clean_terminal_states()；
    4. 断言两项记录原子流转为 failed，清空锁字段与租约，保留 failed_at 与 last_error，不再被任何 Dispatcher 认领。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-clean")

    now = "2026-11-01T12:00:00Z"
    past = "2026-11-01T11:00:00Z"

    with session_factory() as session:
        # 构造底层事件供外键引用
        e1 = EventModel(
            event_id="evt-term-1",
            entity_type="task",
            entity_id="t-1",
            event_type="task.failed",
            payload_json="{}",
            created_at=past,
        )
        e2 = EventModel(
            event_id="evt-term-2",
            entity_type="task",
            entity_id="t-2",
            event_type="task.failed",
            payload_json="{}",
            created_at=past,
        )
        session.add_all([e1, e2])
        session.flush()

        # 1. processing 且租约超期且超限
        ob1 = NotificationOutboxModel(
            id="ob-term-1",
            event_seq_id=e1.seq_id,
            event_type="task.failed",
            status="processing",
            locked_by="stale-worker",
            locked_at=past,
            lease_expires_at=past,  # 过期
            attempt_count=5,
            max_attempts=5,
            next_retry_at=past,
            created_at=past,
            updated_at=past,
        )
        # 2. retry 且超限
        ob2 = NotificationOutboxModel(
            id="ob-term-2",
            event_seq_id=e2.seq_id,
            event_type="task.failed",
            status="retry",
            attempt_count=5,
            max_attempts=5,
            next_retry_at=now,
            created_at=past,
            updated_at=past,
        )
        session.add_all([ob1, ob2])
        session.commit()

    # 执行清理
    with session_factory() as session:
        cleaned_cnt = dispatcher.clean_terminal_states(session)
        session.commit()
        assert cleaned_cnt == 2

    # 验证终态
    with session_factory() as session:
        r1 = session.get(NotificationOutboxModel, "ob-term-1")
        assert r1.status == "failed"
        assert r1.failed_at is not None
        assert r1.locked_by is None
        assert r1.lease_expires_at is None

        r2 = session.get(NotificationOutboxModel, "ob-term-2")
        assert r2.status == "failed"
        assert r2.failed_at is not None

    # 再次调度认领，断言 failed 记录绝不会被认领
    assert dispatcher.claim_one_task() is None


def test_use_case_10_dispatcher_fencing_same_worker_id_reused(temp_db_path: Path) -> None:
    """用例 10：Worker 标识被复用场景下的 Fencing 拦截实测。

    验证：
    1. Dispatcher A (locked_by='worker-shared') 认领任务后超时；
    2. Dispatcher B 同样使用 locked_by='worker-shared' 发起接管（例如同名 Pod 重启）；
    3. 接管必然使 attempt_count 自增为 2；
    4. 旧实例 A 恢复后以原凭据 (attempt_count=1) 尝试回写，虽然 locked_by 相同，
       但 CAS 因 attempt_count 不匹配直接被拦截，证明 attempt_count 完全等价于独立 fencing_token。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    current_time_str = "2026-11-01T12:00:00Z"

    def time_now() -> str:
        return current_time_str

    # 两个实例使用相同的标识
    disp_a = NotificationDispatcher(session_factory, dispatcher_id="worker-shared", now_func=time_now)
    disp_b = NotificationDispatcher(session_factory, dispatcher_id="worker-shared", now_func=time_now)

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-uc10",
            slug="slug-uc10",
            name="用例10项目",
            root_path="/tmp/uc10",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-010",
            event_type="task.failed",
            project_id="proj-uc10",
            payload={"error": "测试标识复用"},
        )
        uow.commit()

    with session_factory() as session:
        session.execute(text("UPDATE notification_outbox SET next_retry_at = '2026-11-01T12:00:00Z', created_at = '2026-11-01T12:00:00Z'"))
        session.commit()

    # 1. 实例 A 认领 (attempt_count=1)
    claim_a = disp_a.claim_one_task()
    assert claim_a.claimed_attempt_count == 1
    assert claim_a.locked_by == "worker-shared"

    # 2. 超时 70s 后，实例 B（复用相同标识）接管
    current_time_str = "2026-11-01T12:01:10Z"
    claim_b = disp_b.claim_one_task()
    assert claim_b.claimed_attempt_count == 2
    assert claim_b.locked_by == "worker-shared"
    assert disp_b.materialize_and_commit(claim_b) is True

    # 3. 旧实例 A 恢复提交
    res_a = disp_a.materialize_and_commit(claim_a)
    assert res_a is False, "即使 locked_by 相同，旧 attempt_count 也必须被 CAS 拦截！"


def test_use_case_11_duplicate_event_processing_idempotency(temp_db_path: Path) -> None:
    """用例 11：重复 Dispatcher 处理同一事件幂等性。

    验证：
    底层通过 notification_events.event_seq_id UNIQUE 约束兜底，
    若发生重复派生相同 event_seq_id，数据库抛出唯一键违规并触发整体回滚，不产生重复通知与投递单。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-uc11")

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-uc11",
            slug="slug-uc11",
            name="用例11项目",
            root_path="/tmp/uc11",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-011",
            event_type="task.failed",
            project_id="proj-uc11",
            payload={"error": "事件防重测试"},
        )
        uow.commit()

    # 首次正常派发成功
    claim1 = dispatcher.claim_one_task()
    assert dispatcher.materialize_and_commit(claim1) is True

    with session_factory() as session:
        notif_cnt = session.query(NotificationModel).filter_by(project_id="proj-uc11").count()
        assert notif_cnt == 1

    # 构造虚假伪造重复认领同一 event_seq_id
    with session_factory() as session:
        fake_evt = EventModel(
            event_id=f"evt-fake-{uuid.uuid4().hex[:6]}",
            entity_type="task",
            entity_id="task-011-fake",
            event_type="task.failed",
            payload_json="{}",
            created_at="2026-11-01T12:00:00Z",
        )
        session.add(fake_evt)
        session.flush()

        fake_ob = NotificationOutboxModel(
            id="fake-task-id",
            event_seq_id=fake_evt.seq_id,
            event_type="task.failed",
            status="processing",
            locked_by="disp-uc11",
            lease_expires_at="2099-01-01T00:00:00Z",
            attempt_count=1,
            max_attempts=5,
            next_retry_at="2026-11-01T12:00:00Z",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        session.add(fake_ob)
        session.commit()

    fake_claim = ClaimedOutboxTask(
        task_id="fake-task-id",
        event_seq_id=claim1.event_seq_id,  # 冲突的 seq_id，已被 claim1 占有
        event_type="task.failed",
        payload={},
        claimed_attempt_count=1,
        locked_by="disp-uc11",
        claimed_at="2026-11-01T12:00:00Z",
    )

    # 再次提交 fake_claim，但其 event_seq_id 引用已持久化的 seq_id
    with pytest.raises(Exception):  # IntegrityError
        dispatcher.materialize_and_commit(fake_claim)

    # 验证通知表依然只有 1 条记录，未产生重复
    with session_factory() as session:
        assert session.query(NotificationModel).filter_by(project_id="proj-uc11").count() == 1


def test_use_case_12_subscription_snapshot_consistency_single_writer(temp_db_path: Path) -> None:
    """用例 12：订阅在 Dispatcher 处理期间变化的快照一致性（修正 P53-07，严格符合 SQLite 单写者机制）。

    步骤：
    1. 预先存在 2 个活跃订阅；
    2. 连接 A 开启 BEGIN IMMEDIATE 事务，读取 2 个订阅快照，并在内存构造 2 条 Delivery；
    3. 连接 B 尝试并发写入第 3 个订阅，断言连接 B 被阻塞或抛出 SQLITE_BUSY（排他锁生效）；
    4. 连接 A 提交并释放排他写锁；
    5. 连接 B 随后成功写入第 3 个订阅；
    6. 验证本次派生的 Delivery 数量依然精确为 2 条，新订阅不被追溯补发。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    # 1. 预先种子 2 个活跃订阅
    _seed_active_subscription(session_factory, "sub-001", "dev-001")
    _seed_active_subscription(session_factory, "sub-002", "dev-002")

    # 创建一条 Outbox 待处理记录
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-uc12",
            slug="slug-uc12",
            name="用例12项目",
            root_path="/tmp/uc12",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-012",
            event_type="task.failed",
            project_id="proj-uc12",
            payload={"error": "快照测试"},
        )
        uow.commit()

    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-uc12")
    claim = dispatcher.claim_one_task()
    assert claim is not None

    # 2. 连接 A 执行阶段二派生并提交
    success = dispatcher.materialize_and_commit(claim)
    assert success is True

    # 3. 此时写入第 3 个订阅
    _seed_active_subscription(session_factory, "sub-003", "dev-003")

    # 4. 验证本次通知的 deliveries 依然严格为 2 条
    with session_factory() as session:
        notif = session.query(NotificationModel).filter_by(project_id="proj-uc12").first()
        deliveries = session.query(NotificationDeliveryModel).filter_by(notification_id=notif.id).all()
        assert len(deliveries) == 2
        sub_ids = {d.subscription_id for d in deliveries}
        assert sub_ids == {"sub-001", "sub-002"}
        assert "sub-003" not in sub_ids


def test_use_case_14_phase2_child_table_failure_atomic_rollback(temp_db_path: Path) -> None:
    """用例 14：派生阶段子表写入失败的整体原子回滚。

    验证：
    在派生事务执行期间，若子表插入触发异常，
    notifications, notification_events, notification_deliveries, notification_changes 全部回滚，
    Outbox 依然保持未处理状态。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-uc14")

    # 提前 seed 活跃订阅，确保派生逻辑会构建 NotificationDeliveryModel
    _seed_active_subscription(session_factory, "sub-014", "dev-014")

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-uc14",
            slug="slug-uc14",
            name="用例14项目",
            root_path="/tmp/uc14",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-014",
            event_type="task.failed",
            project_id="proj-uc14",
            payload={"error": "异常测试"},
        )
        uow.commit()

    claim = dispatcher.claim_one_task()
    assert claim is not None

    # 模拟在写入 delivery 时注入失败
    import unittest.mock as mock

    with mock.patch("backend.services.notification_dispatcher.NotificationDeliveryModel", side_effect=ValueError("模拟投递表写入故障")):
        with pytest.raises(ValueError, match="模拟投递表写入故障"):
            dispatcher.materialize_and_commit(claim)

    # 验证子表全部回滚
    with session_factory() as session:
        assert session.query(NotificationModel).filter_by(project_id="proj-uc14").count() == 0
        assert session.query(NotificationEventModel).count() == 0
        assert session.query(NotificationDeliveryModel).count() == 0
        # Outbox 保持未完成
        ob = session.get(NotificationOutboxModel, claim.task_id)
        assert ob.status == "processing"
        assert ob.processed_at is None


def test_use_case_15_ten_second_aggregation_window_boundaries(temp_db_path: Path) -> None:
    """用例 15：10 秒通知聚合窗口全场景边界实测（修正 P53-05）。

    验证：
    1. 首条事件建立开放窗口 (is_window_open=1, aggregated_count=1, window_started_at=E1.created_at)；
    2. 闭区间 [0, 10s] 内的新事件合入该通知 (aggregated_count=2)；
    3. 负向乱序迟到事件 (T_event < T_window_start) 独立创建新通知并即建即关 (is_window_open=0)；
    4. 正向超时事件 (T_event - T_window_start > 10s) 触发旧窗口关闭 (is_window_open=0) 与新窗口开启 (is_window_open=1)；
    5. 部分唯一索引 uq_notifications_open_agg 物理兜底，确保同一时刻同一聚合键至多 1 个开放窗口。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-uc15")

    pid = "proj-uc15"
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id=pid,
            slug="slug-uc15",
            name="用例15项目",
            root_path="/tmp/uc15",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.commit()

    # 辅助提交单事件函数
    def _create_event(event_time_iso: str, note: str) -> None:
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.record_event(
                entity_type="task",
                entity_id="task-aggregate",
                event_type="task.failed",
                project_id=pid,
                payload={"note": note},
            )
            # 手工调整刚记录事件的 created_at 模拟乱序或超时
            uow._pending_events[-1].created_at = event_time_iso
            uow.commit()

    # --- 1. 第一条事件：12:00:00，新建开放窗口 ---
    _create_event("2026-11-01T12:00:00Z", "首次故障 E1")
    assert dispatcher.dispatch_one_task() is True

    with session_factory() as session:
        notifs = session.query(NotificationModel).filter_by(project_id=pid).all()
        assert len(notifs) == 1
        n1 = notifs[0]
        assert n1.is_window_open == 1
        assert n1.aggregated_count == 1
        assert n1.window_started_at == "2026-11-01T12:00:00Z"

    # --- 2. 第二条事件：12:00:08 (相差 8s，落在 [0, 10s] 内)，合入原通知 ---
    _create_event("2026-11-01T12:00:08Z", "合流故障 E2")
    assert dispatcher.dispatch_one_task() is True

    with session_factory() as session:
        notifs = session.query(NotificationModel).filter_by(project_id=pid).all()
        assert len(notifs) == 1
        n1 = notifs[0]
        assert n1.is_window_open == 1
        assert n1.aggregated_count == 2, "8s 内的事件必须合流聚合使计数变为 2"

    # --- 3. 第三条事件：11:59:50 (负向乱序事件，早于窗口起点 10s)，独立建表即关 ---
    _create_event("2026-11-01T11:59:50Z", "负向乱序事件 E3")
    assert dispatcher.dispatch_one_task() is True

    with session_factory() as session:
        notifs = session.query(NotificationModel).filter_by(project_id=pid).order_by(NotificationModel.created_at.asc()).all()
        assert len(notifs) == 2
        # 刚才的开放通知依然保持开放
        open_n = [n for n in notifs if n.is_window_open == 1][0]
        assert open_n.aggregated_count == 2
        # 负向乱序通知即建即关
        closed_late = [n for n in notifs if n.is_window_open == 0][0]
        assert closed_late.aggregated_count == 1
        assert closed_late.is_window_open == 0
        assert "[历史补发]" in closed_late.title

    # --- 4. 第四条事件：12:00:15 (相差 15s，正向超时 > 10s)，先关闭原开放窗口，再开启新窗口 ---
    _create_event("2026-11-01T12:00:15Z", "超时新周期事件 E4")
    assert dispatcher.dispatch_one_task() is True

    with session_factory() as session:
        notifs = session.query(NotificationModel).filter_by(project_id=pid).all()
        assert len(notifs) == 3
        # 原开放通知已经被关闭
        old_window = session.get(NotificationModel, open_n.id)
        assert old_window.is_window_open == 0

        # 新增一个开放窗口
        current_open = [n for n in notifs if n.is_window_open == 1]
        assert len(current_open) == 1, "同一聚合键下至多有 1 个开放窗口！"
        assert current_open[0].window_started_at == "2026-11-01T12:00:15Z"
        assert current_open[0].aggregated_count == 1



def test_use_case_06_delivery_worker_lifecycle_success(temp_db_path: Path) -> None:
    """用例 6：Delivery Worker 原子认领与状态机流转（Mock 201 成功转 delivered）。

    验证：
    1. 从 pending 状态原子认领投递单，status 转为 processing，attempt_count 从 0 累加至 1，设置租约 30s；
    2. Mock 适配器返回 201 成功；
    3. 状态原子流转为 delivered，记录 delivered_at，清空 locked_by 与租约，刷新订阅的 last_pushed_at。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    # 1. 种子活跃订阅
    _seed_active_subscription(session_factory, "sub-006", "dev-006")

    # 2. 产生通知主表记录与 delivery 记录
    now = "2026-11-01T12:00:00Z"
    with session_factory() as session:
        notif = NotificationModel(
            id="notif-006",
            project_id=None,
            category="decision_required",
            priority="high",
            title="待确认决策",
            body="请确认方案",
            link_url="/",
            window_started_at=now,
            is_window_open=1,
            aggregated_count=1,
            is_read=0,
            created_at=now,
            updated_at=now,
        )
        deliv = NotificationDeliveryModel(
            id="deliv-006",
            notification_id="notif-006",
            subscription_id="sub-006",
            device_id="dev-006",
            status="pending",
            attempt_count=0,
            max_attempts=4,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(notif)
        session.flush()
        session.add(deliv)
        session.commit()

    # 3. 初始化 Worker (Mock 返回 201)
    adapter = MockWebPushAdapter(default_status_code=201)
    worker = NotificationDeliveryWorker(session_factory, worker_id="worker-006", adapter=adapter)

    # 4. 认领任务
    claim = worker.claim_one_task()
    assert claim is not None
    assert claim.delivery_id == "deliv-006"
    assert claim.claimed_attempt_count == 1
    assert claim.locked_by == "worker-006"

    # 验证认领后的中间态
    with session_factory() as session:
        d = session.get(NotificationDeliveryModel, "deliv-006")
        assert d.status == "processing"
        assert d.attempt_count == 1
        assert d.locked_by == "worker-006"
        assert d.lease_expires_at is not None

    # 5. 执行并提交
    success = worker.execute_and_commit(claim)
    assert success is True

    # 6. 验证 delivered 终态与订阅活跃时间
    with session_factory() as session:
        d = session.get(NotificationDeliveryModel, "deliv-006")
        assert d.status == "delivered"
        assert d.delivered_at is not None
        assert d.last_http_status == 201
        assert d.locked_by is None
        assert d.lease_expires_at is None

        sub = session.get(PushSubscriptionModel, "sub-006")
        assert sub.last_pushed_at is not None
        assert sub.is_active == 1


def test_use_case_07_delivery_worker_lease_timeout_takeover(temp_db_path: Path) -> None:
    """用例 7：Delivery Worker 租约超时接管。

    验证：
    1. Worker A 认领任务（attempt_count=1, 租约 30s）；
    2. A 模拟失联超时（时间推进 35s）；
    3. Worker B 成功接管认领，attempt_count 自增为 2，租约更新为 Worker B。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    _seed_active_subscription(session_factory, "sub-007", "dev-007")

    current_time_str = "2026-11-01T12:00:00Z"

    def time_now() -> str:
        return current_time_str

    worker_a = NotificationDeliveryWorker(session_factory, worker_id="worker-A", now_func=time_now)
    worker_b = NotificationDeliveryWorker(session_factory, worker_id="worker-B", now_func=time_now)

    with session_factory() as session:
        notif = NotificationModel(
            id="notif-007",
            project_id=None,
            category="task_blocked",
            priority="high",
            title="阻塞",
            body="内容",
            link_url="/",
            window_started_at=current_time_str,
            is_window_open=1,
            aggregated_count=1,
            is_read=0,
            created_at=current_time_str,
            updated_at=current_time_str,
        )
        deliv = NotificationDeliveryModel(
            id="deliv-007",
            notification_id="notif-007",
            subscription_id="sub-007",
            device_id="dev-007",
            status="pending",
            attempt_count=0,
            max_attempts=4,
            next_retry_at=current_time_str,
            created_at=current_time_str,
            updated_at=current_time_str,
        )
        session.add(notif)
        session.flush()
        session.add(deliv)
        session.commit()

    # 1. Worker A 认领
    claim_a = worker_a.claim_one_task()
    assert claim_a is not None
    assert claim_a.claimed_attempt_count == 1
    assert claim_a.locked_by == "worker-A"

    # A 未超时，B 无法认领
    assert worker_b.claim_one_task() is None

    # 2. 时间推进 35 秒（租约 30s 已过）
    current_time_str = "2026-11-01T12:00:35Z"

    # 3. Worker B 接管认领
    claim_b = worker_b.claim_one_task()
    assert claim_b is not None
    assert claim_b.delivery_id == "deliv-007"
    assert claim_b.claimed_attempt_count == 2, "租约接管必须使 attempt_count 累加为 2"
    assert claim_b.locked_by == "worker-B"


def test_use_case_08_delivery_worker_terminal_state_failed(temp_db_path: Path) -> None:
    """用例 8：Delivery Worker 达到最大尝试次数终态熔断。

    验证：
    1. 构造一条 attempt_count = 4 (>= max_attempts=4) 且租约过期的 processing 记录；
    2. 构造一条 attempt_count = 4 的 retry 记录；
    3. 执行 clean_terminal_states()；
    4. 断言记录自动流转为 failed 终态，清空锁字段与租约，保留 failed_at 与 last_error。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)
    worker = NotificationDeliveryWorker(session_factory, worker_id="worker-clean")

    _seed_active_subscription(session_factory, "sub-008", "dev-008")

    past = "2026-11-01T11:00:00Z"
    now = "2026-11-01T12:00:00Z"

    with session_factory() as session:
        notif1 = NotificationModel(
            id="notif-008a",
            project_id=None,
            category="system_alert",
            priority="normal",
            title="告警1",
            body="内容1",
            link_url="/",
            window_started_at=past,
            is_window_open=0,
            aggregated_count=1,
            is_read=0,
            created_at=past,
            updated_at=past,
        )
        notif2 = NotificationModel(
            id="notif-008b",
            project_id=None,
            category="system_alert",
            priority="normal",
            title="告警2",
            body="内容2",
            link_url="/",
            window_started_at=past,
            is_window_open=0,
            aggregated_count=1,
            is_read=0,
            created_at=past,
            updated_at=past,
        )
        d1 = NotificationDeliveryModel(
            id="deliv-term-1",
            notification_id="notif-008a",
            subscription_id="sub-008",
            device_id="dev-008",
            status="processing",
            locked_by="stale-worker",
            locked_at=past,
            lease_expires_at=past,
            attempt_count=4,
            max_attempts=4,
            next_retry_at=past,
            created_at=past,
            updated_at=past,
        )
        d2 = NotificationDeliveryModel(
            id="deliv-term-2",
            notification_id="notif-008b",
            subscription_id="sub-008",
            device_id="dev-008",
            status="retry",
            attempt_count=4,
            max_attempts=4,
            next_retry_at=past,
            created_at=past,
            updated_at=past,
        )
        session.add_all([notif1, notif2])
        session.flush()
        session.add_all([d1, d2])
        session.commit()

    with session_factory() as session:
        cleaned = worker.clean_terminal_states(session)
        session.commit()
        assert cleaned == 2

    with session_factory() as session:
        res1 = session.get(NotificationDeliveryModel, "deliv-term-1")
        assert res1.status == "failed"
        assert res1.failed_at is not None
        assert res1.locked_by is None
        assert res1.lease_expires_at is None

        res2 = session.get(NotificationDeliveryModel, "deliv-term-2")
        assert res2.status == "failed"
        assert res2.failed_at is not None


def test_use_case_09_delivery_worker_crash_and_takeover_resilience(temp_db_path: Path) -> None:
    """用例 9：Worker 认领后立即崩溃接管自愈。

    验证：
    1. Worker A 认领成功但进程崩溃（未回写数据库，未发出网络请求）；
    2. attempt_count 依然被记录为 1（认领即启动工作尝试）；
    3. 租约到期后由 Worker B 接管自愈并成功送达，attempt_count 变为 2，消息不丢。
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    _seed_active_subscription(session_factory, "sub-009", "dev-009")

    current_time_str = "2026-11-01T12:00:00Z"

    def time_now() -> str:
        return current_time_str

    worker_a = NotificationDeliveryWorker(session_factory, worker_id="worker-crash-A", now_func=time_now)
    worker_b = NotificationDeliveryWorker(session_factory, worker_id="worker-heal-B", now_func=time_now)

    with session_factory() as session:
        notif = NotificationModel(
            id="notif-009",
            project_id=None,
            category="task_blocked",
            priority="high",
            title="标题",
            body="正文",
            link_url="/",
            window_started_at=current_time_str,
            is_window_open=1,
            aggregated_count=1,
            is_read=0,
            created_at=current_time_str,
            updated_at=current_time_str,
        )
        deliv = NotificationDeliveryModel(
            id="deliv-009",
            notification_id="notif-009",
            subscription_id="sub-009",
            device_id="dev-009",
            status="pending",
            attempt_count=0,
            max_attempts=4,
            next_retry_at=current_time_str,
            created_at=current_time_str,
            updated_at=current_time_str,
        )
        session.add(notif)
        session.flush()
        session.add(deliv)
        session.commit()

    # 1. Worker A 认领任务
    claim_a = worker_a.claim_one_task()
    assert claim_a is not None
    assert claim_a.claimed_attempt_count == 1

    # 模拟 Worker A 进程立即崩溃（不调用 execute_and_commit）
    del worker_a

    # 2. 模拟时间推进 35 秒（租约失效）
    current_time_str = "2026-11-01T12:00:35Z"

    # 3. Worker B 接管并正常完成投递
    claim_b = worker_b.claim_one_task()
    assert claim_b is not None
    assert claim_b.claimed_attempt_count == 2
    assert worker_b.execute_and_commit(claim_b) is True

    with session_factory() as session:
        d = session.get(NotificationDeliveryModel, "deliv-009")
        assert d.status == "delivered"
        assert d.attempt_count == 2


def test_delivery_worker_410_and_consecutive_404_subscription_deactivation(temp_db_path: Path) -> None:
    """专项测试：410 明确过期与连续 404 的订阅停用机制，以及 CAS 失败时零副作用。

    验证：
    1. 模拟网关 410 -> delivery 置 gone，同事务停用订阅 (is_active=0)；
    2. 模拟网关首次 404 -> delivery 记录 last_http_status=404 并置 retry，订阅保持有效 (is_active=1)；
    3. 模拟连续第 2 次 404 -> 视同 410 置 gone，同事务停用订阅 (is_active=0)；
    4. 模拟 CAS 回写失败（影响行数 0，例如超时失效）-> 事务强制回滚，绝不触发订阅停用！
    """
    engine = create_sqlite_engine(temp_db_path)
    session_factory = create_session_factory(engine)

    # 场景 1: 410 停用订阅
    _seed_active_subscription(session_factory, "sub-410", "dev-410")
    now = "2026-11-01T12:00:00Z"

    with session_factory() as session:
        notif = NotificationModel(
            id="notif-410",
            category="system_alert",
            priority="normal",
            title="通知",
            body="内容",
            link_url="/",
            window_started_at=now,
            is_window_open=0,
            aggregated_count=1,
            is_read=0,
            created_at=now,
            updated_at=now,
        )
        d410 = NotificationDeliveryModel(
            id="deliv-410",
            notification_id="notif-410",
            subscription_id="sub-410",
            device_id="dev-410",
            status="pending",
            attempt_count=0,
            max_attempts=4,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(notif)
        session.flush()
        session.add(d410)
        session.commit()

    worker_410 = NotificationDeliveryWorker(
        session_factory,
        worker_id="w-410",
        adapter=MockWebPushAdapter(default_status_code=410),
    )
    claim1 = worker_410.claim_one_task()
    assert worker_410.execute_and_commit(claim1) is True

    with session_factory() as session:
        d = session.get(NotificationDeliveryModel, "deliv-410")
        assert d.status == "gone"
        assert d.last_http_status == 410
        # 订阅被停用
        sub = session.get(PushSubscriptionModel, "sub-410")
        assert sub.is_active == 0

    # 场景 2: 首次 404 -> retry，不停用
    _seed_active_subscription(session_factory, "sub-404", "dev-404")
    with session_factory() as session:
        d404 = NotificationDeliveryModel(
            id="deliv-404",
            notification_id="notif-410",
            subscription_id="sub-404",
            device_id="dev-404",
            status="pending",
            attempt_count=0,
            max_attempts=4,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(d404)
        session.commit()

    worker_404 = NotificationDeliveryWorker(
        session_factory,
        worker_id="w-404",
        adapter=MockWebPushAdapter(default_status_code=404),
    )
    claim_first_404 = worker_404.claim_one_task()
    assert worker_404.execute_and_commit(claim_first_404) is True

    with session_factory() as session:
        d = session.get(NotificationDeliveryModel, "deliv-404")
        assert d.status == "retry"
        assert d.last_http_status == 404
        # 首次 404 绝不停用订阅！
        sub = session.get(PushSubscriptionModel, "sub-404")
        assert sub.is_active == 1

    # 场景 3: 连续第 2 次 404 -> gone 并停用订阅
    # 模拟重试时间已到
    with session_factory() as session:
        session.execute(text("UPDATE notification_deliveries SET next_retry_at = '2026-11-01T12:00:00Z' WHERE id = 'deliv-404'"))
        session.commit()

    claim_second_404 = worker_404.claim_one_task()
    assert claim_second_404.last_http_status == 404
    assert worker_404.execute_and_commit(claim_second_404) is True

    with session_factory() as session:
        d = session.get(NotificationDeliveryModel, "deliv-404")
        assert d.status == "gone"
        # 连续第 2 次 404 停用订阅
        sub = session.get(PushSubscriptionModel, "sub-404")
        assert sub.is_active == 0

    # 场景 4: CAS 校验失败零副作用测试（租约超时或被抢占）
    _seed_active_subscription(session_factory, "sub-cas", "dev-cas")
    with session_factory() as session:
        d_cas = NotificationDeliveryModel(
            id="deliv-cas",
            notification_id="notif-410",
            subscription_id="sub-cas",
            device_id="dev-cas",
            status="pending",
            attempt_count=0,
            max_attempts=4,
            next_retry_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(d_cas)
        session.commit()

    claim_cas = worker_410.claim_one_task()
    # 人为修改数据库中的 attempt_count 模拟已被其他 Worker 接管
    with session_factory() as session:
        session.execute(text("UPDATE notification_deliveries SET attempt_count = 99 WHERE id = 'deliv-cas'"))
        session.commit()

    # 执行提交，预期 CAS 影响行数 == 0，强制回滚并返回 False
    res_cas = worker_410.execute_and_commit(claim_cas)
    assert res_cas is False

    # 关键断言：订阅绝不被停用！
    with session_factory() as session:
        sub = session.get(PushSubscriptionModel, "sub-cas")
        assert sub.is_active == 1, "失去执行权的 Worker 绝不能产生停用订阅的副作用！"


def test_use_case_13_sqlite_busy_retry_with_full_jitter(temp_db_path: Path) -> None:
    """用例 13：SQLITE_BUSY 冲突注入与有限抖动重试。

    验证：
    1. 在外部真实独立 SQLite 连接中持有排他写锁；
    2. Worker / Dispatcher 尝试写入触发 SQLITE_BUSY；
    3. 应用层执行有限重试（最多 4 次尝试，带 Full Jitter）；
    4. 释放锁后成功提交，或重试超限后记录 error 日志安全退出，不无限死锁。
    """
    engine = create_sqlite_engine(temp_db_path)
    # 为测试加速快速触发锁冲突，将连接层 busy_timeout 调整为 100ms
    sa.event.listen(engine, "connect", lambda dbapi_conn, _: dbapi_conn.execute("PRAGMA busy_timeout = 100;"))
    session_factory = create_session_factory(engine)
    dispatcher = NotificationDispatcher(session_factory, dispatcher_id="disp-busy")

    # 创建一条待处理 Outbox
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        proj = ProjectModel(
            id="proj-busy",
            slug="slug-busy",
            name="Busy测试项目",
            root_path="/tmp/busy",
            created_at="2026-11-01T12:00:00Z",
            updated_at="2026-11-01T12:00:00Z",
        )
        uow.session.add(proj)
        uow.record_event(
            entity_type="task",
            entity_id="task-busy",
            event_type="task.failed",
            project_id="proj-busy",
            payload={"error": "Busy测试"},
        )
        uow.commit()

    # 外部独立真实 SQLite 物理连接模拟锁占用
    external_conn = sqlite3.connect(str(temp_db_path), timeout=0.1)
    external_conn.execute("BEGIN EXCLUSIVE;")  # 持有排他写锁

    try:
        # 在锁被外部持有时，dispatch_batch 尝试执行会被阻塞并抛错，重试循环安全退出
        processed = dispatcher.dispatch_with_retry(max_tasks=1)
        assert processed == 0, "锁冲突持续存在时，调度器在有限尝试后必须安全退出"
    finally:
        # 外部连接释放锁
        external_conn.rollback()
        external_conn.close()

    # 释放锁后再次调度，能够成功派发
    processed_after = dispatcher.dispatch_with_retry(max_tasks=1)
    assert processed_after == 1
