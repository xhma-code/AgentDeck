# -*- coding: utf-8 -*-
"""
Clean-Slate Phase 1 核心领域模型、21 张规范表、Alembic 迁移、交互式 Decision 策略与安全边界测试套件。
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text

from backend.application.artifact_service import ArtifactService
from backend.application.decision_service import DecisionService
from backend.application.execution_service import ExecutionService
from backend.application.planning_service import PlanningService
from backend.application.project_service import ProjectService
from backend.application.runtime_registry_service import RuntimeRegistryService
from backend.application.session_service import SessionService
from backend.core.logger import PROJECT_ROOT
from backend.db.backup import create_online_sqlite_backup
from backend.db.base import collect_table_column_comments
from backend.db.engine import (
    create_session_factory,
    create_sqlite_engine,
    inspect_sqlite_pragmas,
)
from backend.db.models import ALL_CANONICAL_TABLE_NAMES
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import (
    ActorType,
    AgentReportedStatus,
    ConnectionStatus,
    DecisionResolvedBy,
    DecisionRiskLevel,
    DecisionTimeoutPolicy,
    DecisionType,
    EndpointKind,
    ExecutionPlatformStatus,
    RemoteEntryKind,
    RuntimeProviderKey,
    SessionPlatformStatus,
    StepPlatformStatus,
    TaskPlatformStatus,
)
from backend.domain.exceptions import (
    CrossProjectReferenceError,
    DependencyCycleError,
    InvariantViolationError,
    SecurityBoundaryError,
    VersionConflictError,
)
from backend.domain.types import (
    DecisionOptionItem,
    ModelEndpointDescriptor,
    validate_safe_remote_url,
)
from backend.security.mcp_token_guard import (
    issue_mcp_project_token,
    verify_mcp_project_token,
)
from backend.security.path_guard import resolve_and_verify_path_within_roots


def _make_alembic_config(db_path: Path) -> Config:
    ini_path = PROJECT_ROOT / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    cfg.attributes["skip_logging_config"] = True
    return cfg


def test_alembic_21_canonical_tables_and_chinese_comments(tmp_path: Path) -> None:
    """验证 Alembic upgrade/downgrade/upgrade 创建全部 21 张规范表，且全表全字段带非空中文注释。"""
    assert len(ALL_CANONICAL_TABLE_NAMES) == 21

    db_path = tmp_path / "space dir" / "clean_slate_test.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    alembic_cfg = _make_alembic_config(db_path)

    # 1. Upgrade head
    command.upgrade(alembic_cfg, "head")

    engine = create_sqlite_engine(db_path=db_path)
    try:
        pragmas = inspect_sqlite_pragmas(engine)
        assert pragmas["journal_mode"] == "wal"
        assert pragmas["foreign_keys"] == 1
        assert int(pragmas["busy_timeout"]) >= 5000

        insp = inspect(engine)
        actual_tables = set(insp.get_table_names())
        for expected_t in ALL_CANONICAL_TABLE_NAMES:
            assert expected_t in actual_tables, f"缺少规范表: {expected_t}"

        # 校验 ORM 定义中全部 21 张表的每个字段均有非空中文注释并已同步至 table_column_comments
        comments = collect_table_column_comments()
        covered_tables = {t_name for t_name, _, _, _ in comments}
        assert covered_tables == set(ALL_CANONICAL_TABLE_NAMES)
        for t_name, t_comment, c_name, c_comment in comments:
            assert t_comment.strip(), f"表 {t_name} 缺少表级中文注释"
            assert c_comment.strip(), f"字段 {t_name}.{c_name} 缺少字段级中文注释"

        with engine.connect() as conn:
            comment_count = conn.execute(
                text("SELECT COUNT(*) FROM table_column_comments")
            ).scalar()
            assert comment_count == len(comments)

            provider_count = conn.execute(
                text("SELECT COUNT(*) FROM runtime_providers")
            ).scalar()
            assert provider_count == 4
    finally:
        engine.dispose()

    # 2. Downgrade base & Upgrade head 往返验证
    command.downgrade(alembic_cfg, "base")
    engine2 = create_sqlite_engine(db_path=db_path)
    try:
        remaining = set(inspect(engine2).get_table_names()) - {"alembic_version"}
        assert len(remaining) == 0
    finally:
        engine2.dispose()

    command.upgrade(alembic_cfg, "head")
    backup_file = create_online_sqlite_backup(db_path, backup_dir=tmp_path / "backups", label="unit")
    assert backup_file.exists() and backup_file.stat().st_size > 0


def test_domain_invariants_revisions_and_interactive_decisions(tmp_path: Path) -> None:
    """验证核心领域不变量、追加式修订传播、三层执行模型与交互式 Decision 超时安全/幂等机制。"""
    db_path = tmp_path / "domain_invariants.db"
    command.upgrade(_make_alembic_config(db_path), "head")

    engine = create_sqlite_engine(db_path=db_path)
    session_factory = create_session_factory(engine)

    proj_svc = ProjectService(session_factory)
    plan_svc = PlanningService(session_factory)
    rt_svc = RuntimeRegistryService(session_factory)
    sess_svc = SessionService(session_factory)
    exec_svc = ExecutionService(session_factory)
    dec_svc = DecisionService(session_factory)
    art_svc = ArtifactService(session_factory)

    repo_a = tmp_path / "Repos" / "ProjectAlpha"
    repo_b = tmp_path / "Repos" / "ProjectBeta"
    repo_a.mkdir(parents=True)
    repo_b.mkdir(parents=True)

    try:
        proj_a = proj_svc.create_project(name="Project Alpha", root_path=str(repo_a))
        proj_b = proj_svc.create_project(name="Project Beta", root_path=str(repo_b))

        # 不变量 1：禁止创建无 Project 的游离 Session，禁止跨项目绑定 Task
        with pytest.raises(InvariantViolationError):
            sess_svc.create_session(project_id="", title="Orphan")

        task_a1 = plan_svc.create_task(
            project_id=proj_a.id,
            title="设计领域模型",
            goal="完成 21 张表设计",
            acceptance_criteria=["全表全字段中文注释"],
        )
        task_a2 = plan_svc.create_task(
            project_id=proj_a.id,
            title="实现 REST API",
            parent_task_id=task_a1.id,
        )
        task_b1 = plan_svc.create_task(project_id=proj_b.id, title="Beta 任务")

        # 拦截跨项目父子任务与跨项目依赖
        with pytest.raises(CrossProjectReferenceError):
            plan_svc.create_task(
                project_id=proj_a.id, title="非法跨项子任务", parent_task_id=task_b1.id
            )
        with pytest.raises(CrossProjectReferenceError):
            plan_svc.add_task_dependency(
                project_id=proj_a.id, task_id=task_a1.id, depends_on_task_id=task_b1.id
            )

        # 不变量 2：同项目 DAG 环路拦截
        plan_svc.add_task_dependency(
            project_id=proj_a.id, task_id=task_a2.id, depends_on_task_id=task_a1.id
        )
        with pytest.raises(DependencyCycleError):
            plan_svc.add_task_dependency(
                project_id=proj_a.id, task_id=task_a1.id, depends_on_task_id=task_a2.id
            )

        # 不变量 3：追加式 Revision 与下游 needs_revalidation 传播（不覆盖旧版本，不自动重跑）
        s1 = plan_svc.create_step(project_id=proj_a.id, task_id=task_a1.id, title="步骤 1")
        s2 = plan_svc.create_step(project_id=proj_a.id, task_id=task_a1.id, title="步骤 2")

        plan_svc.update_step_status(
            project_id=proj_a.id, step_id=s2.id, platform_status=StepPlatformStatus.COMPLETED
        )
        plan_svc.update_task_status(
            project_id=proj_a.id, task_id=task_a2.id, platform_status=TaskPlatformStatus.COMPLETED
        )

        s1_updated, s1_rev2, impact = plan_svc.revise_step(
            project_id=proj_a.id,
            step_id=s1.id,
            title="步骤 1（修订版）",
            instruction="补充约束",
            change_reason="增加安全校验",
        )
        assert s1_updated.active_revision_no == 2
        assert s1_rev2.revision_no == 2
        assert s2.id in impact.impacted_step_ids
        assert task_a2.id in impact.impacted_task_ids
        assert len(plan_svc.list_step_revisions(proj_a.id, s1.id)) == 2

        # 不变量 4：Agent 自报完成绝不自动把 platform_status 置为 completed
        t_after_agent = plan_svc.update_task_status(
            project_id=proj_a.id,
            task_id=task_a1.id,
            agent_reported_status=AgentReportedStatus.COMPLETED,
            actor_type=ActorType.MCP_AGENT,
        )
        assert t_after_agent.agent_reported_status == AgentReportedStatus.COMPLETED.value
        assert t_after_agent.platform_status != TaskPlatformStatus.COMPLETED.value

        with pytest.raises(InvariantViolationError):
            plan_svc.update_task_status(
                project_id=proj_a.id,
                task_id=task_a1.id,
                platform_status=TaskPlatformStatus.COMPLETED,
                actor_type=ActorType.MCP_AGENT,
            )

        # 不变量 5：Session / Execution / ExecutionAttempt 三层分离，且 Remote 断联不自动标失败
        rt_inst = rt_svc.register_runtime_instance(
            provider_key=RuntimeProviderKey.CLAUDE_CODE,
            model_endpoint_descriptor=ModelEndpointDescriptor(
                provider_label="GLM Coding Plan",
                model_label="GLM-4.6",
                endpoint_kind=EndpointKind.COMPATIBLE_API,
            ),
        )
        sess = sess_svc.create_session(
            project_id=proj_a.id,
            task_id=task_a1.id,
            runtime_instance_id=rt_inst.id,
            title="Claude Code 架构实现会话",
        )
        sess_disconnected = sess_svc.update_session_statuses(
            project_id=proj_a.id,
            session_id=sess.id,
            connection_status=ConnectionStatus.DISCONNECTED,
        )
        assert sess_disconnected.connection_status == ConnectionStatus.DISCONNECTED.value
        assert sess_disconnected.platform_status == SessionPlatformStatus.ACTIVE.value

        execution, attempt1 = exec_svc.create_execution(
            project_id=proj_a.id,
            task_id=task_a1.id,
            step_id=s1.id,
            session_id=sess.id,
            runtime_instance_id=rt_inst.id,
        )
        attempt2 = exec_svc.start_new_attempt(
            project_id=proj_a.id,
            execution_id=execution.id,
            runtime_instance_id=rt_inst.id,
        )
        assert attempt1.attempt_no == 1
        assert attempt2.attempt_no == 2
        assert len(exec_svc.list_execution_attempts(execution.id)) == 2

        # 不变量 6：交互式 Decision —— 高风险决策严禁启用 auto_submit_recommended
        options = [
            DecisionOptionItem(
                option_id="opt_1",
                label="方案 A：重建 21 张规范表（推荐）",
                description="彻底清理历史包袱",
                is_recommended=True,
            ),
            DecisionOptionItem(
                option_id="opt_2",
                label="方案 B：保留旧表",
                description="维护成本高",
                is_recommended=False,
            ),
        ]
        with pytest.raises(InvariantViolationError):
            dec_svc.create_decision(
                project_id=proj_a.id,
                title="高风险生产库变更审批",
                decision_type=DecisionType.RISK_GATE,
                risk_level=DecisionRiskLevel.HIGH,
                timeout_policy=DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED,
                options=options,
            )

        # 创建合规普通风险 Decision -> 联动 Task/Step/Session/Execution 进入 waiting_interaction
        dec = dec_svc.create_decision(
            project_id=proj_a.id,
            task_id=task_a1.id,
            step_id=s1.id,
            session_id=sess.id,
            execution_id=execution.id,
            title="选择前端状态管理粒度",
            context_summary="需要确认全局弹窗轮询与同步方式",
            decision_type=DecisionType.DIRECTION_CHOICE,
            risk_level=DecisionRiskLevel.NORMAL,
            timeout_policy=DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED,
            timeout_sec=45,
            options=options,
        )
        assert plan_svc.get_task(proj_a.id, task_a1.id).platform_status == TaskPlatformStatus.WAITING_INTERACTION.value
        assert plan_svc.get_step(proj_a.id, s1.id).platform_status == StepPlatformStatus.WAITING_INTERACTION.value
        assert sess_svc.get_session(sess.id).platform_status == SessionPlatformStatus.WAITING_INTERACTION.value

        # 幂等拍板提交 + 重复幂等键重放 + 冲突答案拦截
        resolved_dec, is_replay_1 = dec_svc.resolve_decision(
            decision_id=dec.id,
            chosen_option_id="opt_1",
            custom_note="同意按推荐方案推进",
            resolved_by=DecisionResolvedBy.USER_MANUAL,
            idempotency_key="idem-key-001",
        )
        assert not is_replay_1
        assert resolved_dec.status == "resolved"
        assert resolved_dec.chosen_option_id == "opt_1"

        # 相同幂等键重复提交 -> 返回 is_idempotent_replay=True
        _, is_replay_2 = dec_svc.resolve_decision(
            decision_id=dec.id,
            chosen_option_id="opt_1",
            custom_note="同意按推荐方案推进",
            resolved_by=DecisionResolvedBy.USER_MANUAL,
            idempotency_key="idem-key-001",
        )
        assert is_replay_2

        # 不同选项尝试覆盖已结案决策 -> 抛出 409 VersionConflictError
        with pytest.raises(VersionConflictError):
            dec_svc.resolve_decision(
                decision_id=dec.id,
                chosen_option_id="opt_2",
                idempotency_key="idem-key-002",
            )

        # 决策结案后，Task/Step/Session 已自动从 waiting_interaction 恢复为活跃状态
        assert plan_svc.get_task(proj_a.id, task_a1.id).platform_status == TaskPlatformStatus.IN_PROGRESS.value
        assert plan_svc.get_step(proj_a.id, s1.id).platform_status == StepPlatformStatus.IN_PROGRESS.value
        assert sess_svc.get_session(sess.id).platform_status == SessionPlatformStatus.ACTIVE.value

        # 验证 Artifact 登记
        art = art_svc.create_artifact(
            project_id=proj_a.id,
            task_id=task_a1.id,
            session_id=sess.id,
            execution_id=execution.id,
            title="Phase 1 架构基线验证报告",
            summary_content="21 张规范表全部通过校验",
        )
        assert art.source_task_revision_no == 1
        assert len(art.checksum_sha256) == 64
    finally:
        engine.dispose()


def test_security_boundaries_path_mcp_and_remote_url(tmp_path: Path) -> None:
    """验证路径白名单与 symlink 逃逸拦截、Remote URL 凭证脱敏拦截、MCP 项目级 Token 作用域隔离。"""
    db_path = tmp_path / "security_test.db"
    command.upgrade(_make_alembic_config(db_path), "head")
    engine = create_sqlite_engine(db_path=db_path)
    session_factory = create_session_factory(engine)
    proj_svc = ProjectService(session_factory)

    allowed_dir = tmp_path / "Workspace" / "SafeRepo"
    outside_dir = tmp_path / "Workspace" / "SecretRepo"
    allowed_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)

    safe_file = allowed_dir / "README.md"
    safe_file.write_text("# Safe Project", encoding="utf-8")
    outside_file = outside_dir / "credentials.txt"
    outside_file.write_text("TOP_SECRET", encoding="utf-8")

    try:
        # 1. 路径守卫
        resolved = resolve_and_verify_path_within_roots(safe_file, [allowed_dir])
        assert resolved == safe_file.resolve()

        with pytest.raises(PermissionError):
            resolve_and_verify_path_within_roots(outside_file, [allowed_dir])

        with pytest.raises(PermissionError):
            resolve_and_verify_path_within_roots(safe_file, [])

        # 2. Remote URL 脱敏校验与 ModelEndpointDescriptor 禁存 URL/Key
        assert validate_safe_remote_url("https://claude.ai/code/session_123") == "https://claude.ai/code/session_123"
        with pytest.raises(SecurityBoundaryError):
            validate_safe_remote_url("https://claude.ai/code?access_token=secret123")
        with pytest.raises(ValueError):
            ModelEndpointDescriptor(provider_label="https://open.bigmodel.cn/api", model_label="GLM-4.6")
        with pytest.raises(ValueError):
            ModelEndpointDescriptor(provider_label="GLM", model_label="sk-1234567890abcdefghij")

        # 3. MCP Project-scoped Token 跨项目隔离
        p1 = proj_svc.create_project(name="P1", root_path=str(allowed_dir))
        p2 = proj_svc.create_project(name="P2", root_path=str(outside_dir))

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            issued = issue_mcp_project_token(
                uow,
                project_id=p1.id,
                token_name="claude-mcp",
                scopes=["task:read", "checkpoint:request"],
            )

        with SqlAlchemyUnitOfWork(session_factory) as uow:
            principal = verify_mcp_project_token(
                uow,
                authorization_header=f"Bearer {issued.raw_token}",
                target_project_id=p1.id,
                required_scope="task:read",
            )
            assert principal.project_id == p1.id

            # 跨项目访问 p2 立即拦截
            with pytest.raises(SecurityBoundaryError):
                verify_mcp_project_token(
                    uow,
                    authorization_header=f"Bearer {issued.raw_token}",
                    target_project_id=p2.id,
                )
    finally:
        engine.dispose()
