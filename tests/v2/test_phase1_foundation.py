# -*- coding: utf-8 -*-
"""
Phase 1 定向验收验证套件（仅在 tmp_path 临时隔离目录与数据库备份副本上运行，零生产数据破坏）。

覆盖范围：
1. Alembic upgrade/downgrade 往返、SQLite WAL/foreign_keys 强约束、全表全字段中文注释 100% 校验及存量旧表零损毁。
2. SQLite Backup API 在含空格路径下的一致性备份与完整性检查。
3. 核心领域不变量：
   - 无 Project 禁止创建 Session；跨 Project 引用全面拦截；
   - Task 父子树与同项目轻量依赖有向无环图 (DAG) 环路拦截；
   - Task / Step 追加式 Revision（保留历史）与下游自动标记 needs_revalidation（不自动重跑）；
   - 平台状态、Agent 自报状态、连接状态严格分离（Agent 报完成不等于平台验收完成；Remote 断开不误判失败）；
   - UnitOfWork 业务变更与 events 表同事务原子提交与异常整体回滚。
4. 安全加固项：
   - WorkspaceDocumentReader 的 Project.root_path 白名单拦截与 Windows Junction / Symlink 越界逃逸阻断；
   - MCP Token 强制绑定单一 Project、最小权限 scope、吊销失效及禁止无令牌放行；
   - Remote URL 一次性凭证脱敏拦截与 ModelEndpointDescriptor 敏感 URL/Key 拦截；
   - CORS 禁止 `*` + credentials，仅放行本机与受控专网/局域网来源，阻断恶意公网 Origin。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text

from backend.app import create_app
from backend.core.database import MetaDatabase
from backend.core.logger import PROJECT_ROOT
from backend.core.workspace import WorkspaceDocumentReader
from backend.v2.application.planning_service import PlanningService
from backend.v2.application.project_service import ProjectService
from backend.v2.application.runtime_registry_service import RuntimeRegistryService
from backend.v2.application.session_service import SessionService
from backend.v2.config import AppV2Settings
from backend.v2.db.backup import create_sqlite_online_backup
from backend.v2.db.base import collect_v2_table_column_comments
from backend.v2.db.engine import (
    create_session_factory,
    create_sqlite_v2_engine,
    inspect_sqlite_pragmas,
)
from backend.v2.db.models.event import EventModel
from backend.v2.db.models.project import McpProjectTokenModel, ProjectModel
from backend.v2.db.uow import SqlAlchemyUnitOfWork
from backend.v2.domain.enums import (
    ActorType,
    AgentReportedStatus,
    ConnectionStatus,
    EndpointKind,
    RemoteEntryKind,
    RuntimeProviderKey,
    SessionPlatformStatus,
    StepPlatformStatus,
    TaskPlatformStatus,
)
from backend.v2.domain.exceptions import (
    CrossProjectReferenceError,
    DependencyCycleError,
    InvariantViolationError,
    SecurityBoundaryError,
)
from backend.v2.domain.types import ModelEndpointDescriptor
from backend.v2.security.mcp_token_guard import (
    issue_mcp_project_token,
    revoke_mcp_project_token,
    verify_mcp_project_token,
)


def _make_alembic_config(db_path: Path) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["skip_logging_config"] = True
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.resolve().as_posix()}")
    return cfg


def _prepare_temp_project_dir(base: Path, folder_name: str) -> Path:
    proj_dir = base / folder_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "README.md").write_text(f"# {folder_name}\n", encoding="utf-8")
    return proj_dir


def test_alembic_roundtrip_comments_and_legacy_preservation(tmp_path: Path) -> None:
    """在带空格目录副本上验证 Alembic upgrade -> downgrade -> upgrade 往返及存量旧表无损。"""
    spaced_dir = tmp_path / "dir with spaces"
    spaced_dir.mkdir(parents=True, exist_ok=True)
    db_file = spaced_dir / "test_roundtrip.db"

    # 先初始化模拟存量旧库并插入旧数据
    legacy_db = MetaDatabase(db_path=db_file)
    legacy_db.upsert_session_meta(
        unified_id="opencode:legacy_orphan",
        agent_type="opencode",
        raw_session_id="legacy_orphan",
        project_path=None,  # 模拟无路径存量记录
        title="历史无路径会话",
    )

    # 验证在线热备 API 在含空格路径下工作正常
    bak_res = create_sqlite_online_backup(db_file, spaced_dir / "backups", tag="pre_mig")
    assert bak_res.integrity_status == "ok"
    assert Path(bak_res.backup_path).exists()

    # 校验全部 ORM 表与列均具备非空中文注释
    comment_rows = collect_v2_table_column_comments(validate_non_empty=True)
    assert len(comment_rows) >= 120

    alembic_cfg = _make_alembic_config(db_file)
    command.upgrade(alembic_cfg, "head")

    engine = create_sqlite_v2_engine(db_path=db_file)
    try:
        pragmas = inspect_sqlite_pragmas(engine)
        assert pragmas["journal_mode"] == "wal"
        assert pragmas["foreign_keys"] == 1

        with engine.connect() as conn:
            # 存量旧记录完好无损
            legacy_cnt = conn.execute(
                text("SELECT COUNT(1) FROM t_unified_session_meta WHERE unified_id='opencode:legacy_orphan'")
            ).scalar()
            assert legacy_cnt == 1

            # 4 大内置 RuntimeProvider 与本机 Machine 已幂等就绪
            prov_cnt = conn.execute(text("SELECT COUNT(1) FROM runtime_providers")).scalar()
            mach_cnt = conn.execute(text("SELECT COUNT(1) FROM machines WHERE id='local-machine'")).scalar()
            assert prov_cnt == 4
            assert mach_cnt == 1

            # 中文表字段注释已全部写入 t_table_column_comments
            v2_comment_cnt = conn.execute(
                text("SELECT COUNT(1) FROM t_table_column_comments WHERE table_name IN ('projects','tasks','sessions','events')")
            ).scalar()
            assert int(v2_comment_cnt or 0) > 40
    finally:
        engine.dispose()

    # 执行回滚，验证新表干净移除且旧表依然完好
    command.downgrade(alembic_cfg, "-1")
    engine_after_down = create_sqlite_v2_engine(db_path=db_file)
    try:
        with engine_after_down.connect() as conn:
            has_projects = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'")
            ).scalar()
            assert has_projects is None
            legacy_cnt_after = conn.execute(
                text("SELECT COUNT(1) FROM t_unified_session_meta WHERE unified_id='opencode:legacy_orphan'")
            ).scalar()
            assert legacy_cnt_after == 1
    finally:
        engine_after_down.dispose()

    # 再次升级确认幂等可重复
    command.upgrade(alembic_cfg, "head")


def test_core_domain_invariants_and_transactional_events(tmp_path: Path) -> None:
    """验证不启动任何 Agent Runtime 独立完成项目/任务/依赖/修订/会话管理及全部核心不变量。"""
    db_file = tmp_path / "domain_invariants.db"
    command.upgrade(_make_alembic_config(db_file), "head")

    engine = create_sqlite_v2_engine(db_path=db_file)
    sf = create_session_factory(engine)
    try:
        proj_svc = ProjectService(sf)
        plan_svc = PlanningService(sf)
        rt_svc = RuntimeRegistryService(sf)
        sess_svc = SessionService(sf)

        repo_a = _prepare_temp_project_dir(tmp_path, "RepoAlpha")
        repo_b = _prepare_temp_project_dir(tmp_path, "RepoBeta")

        proj_a = proj_svc.create_project(name="Alpha 平台重构", root_path=str(repo_a), slug="alpha")
        proj_b = proj_svc.create_project(name="Beta 独立实验", root_path=str(repo_b), slug="beta")

        # 1. 验证 UnitOfWork 同事务回滚原子性（异常时业务实体与事件均不得落盘）
        with sf() as s:
            before_evt_count = s.execute(select(func.count()).select_from(EventModel)).scalar_one()

        with pytest.raises(RuntimeError):
            with SqlAlchemyUnitOfWork(sf) as uow:
                assert uow.session is not None
                uow.session.add(
                    ProjectModel(
                        id="temp-rollback-id",
                        slug="rollback-slug",
                        name="Rollback Test",
                        description="",
                        root_path=str(tmp_path / "never_committed"),
                        status="active",
                        summary_next_action="",
                        version=1,
                        created_at="2026-10-31T00:00:00Z",
                        updated_at="2026-10-31T00:00:00Z",
                    )
                )
                uow.record_event(
                    project_id="temp-rollback-id",
                    entity_type="project",
                    entity_id="temp-rollback-id",
                    event_type="project.should_rollback",
                )
                raise RuntimeError("模拟提交前故障")

        with sf() as s:
            after_evt_count = s.execute(select(func.count()).select_from(EventModel)).scalar_one()
            assert after_evt_count == before_evt_count
            assert s.get(ProjectModel, "temp-rollback-id") is None

        # 进一步验证：即便业务表已 flush，若 events 表落盘因外键/唯一键失败，业务表也必须 100% 原子回滚
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            with SqlAlchemyUnitOfWork(sf) as uow:
                assert uow.session is not None
                uow.session.add(
                    ProjectModel(
                        id="flushed-but-rolled-back",
                        slug="flushed-rollback",
                        name="Flushed Rollback",
                        description="",
                        root_path=str(tmp_path / "flushed_never_persisted"),
                        status="active",
                        summary_next_action="",
                        version=1,
                        created_at="2026-10-31T00:00:00Z",
                        updated_at="2026-10-31T00:00:00Z",
                    )
                )
                # 故意写入不存在的 project_id 触发 events 外键失败
                uow.record_event(
                    project_id="non-existent-project-fk",
                    entity_type="project",
                    entity_id="flushed-but-rolled-back",
                    event_type="project.fk_fail",
                )
                uow.commit()

        with sf() as s:
            assert s.get(ProjectModel, "flushed-but-rolled-back") is None

        # 2. 验证任务树、跨项目引用拦截与 DAG 环路检测
        t1 = plan_svc.create_task(
            project_id=proj_a.id,
            title="构建核心领域模型",
            goal="实现 Project 与 Task 模型",
            acceptance_criteria=["具备中文注释", "同事务写事件"],
        )
        t2 = plan_svc.create_task(
            project_id=proj_a.id,
            title="构建 REST API",
            parent_task_id=t1.id,
        )
        t3 = plan_svc.create_task(project_id=proj_a.id, title="接入 MCP 服务")
        tb_other = plan_svc.create_task(project_id=proj_b.id, title="Beta 任务")

        # 跨 Project 父子挂载拦截
        with pytest.raises(CrossProjectReferenceError):
            plan_svc.create_task(
                project_id=proj_b.id,
                title="非法跨项目子任务",
                parent_task_id=t1.id,
            )

        # 跨 Project 依赖拦截
        with pytest.raises(CrossProjectReferenceError):
            plan_svc.add_task_dependency(
                project_id=proj_a.id,
                task_id=t2.id,
                depends_on_task_id=tb_other.id,
            )

        # 正常 DAG 依赖：t3 依赖 t2，t2 依赖 t1
        plan_svc.add_task_dependency(project_id=proj_a.id, task_id=t2.id, depends_on_task_id=t1.id)
        plan_svc.add_task_dependency(project_id=proj_a.id, task_id=t3.id, depends_on_task_id=t2.id)

        # 自环与有向环拦截：t1 不能反向依赖 t3
        with pytest.raises(DependencyCycleError):
            plan_svc.add_task_dependency(project_id=proj_a.id, task_id=t1.id, depends_on_task_id=t1.id)
        with pytest.raises(DependencyCycleError):
            plan_svc.add_task_dependency(project_id=proj_a.id, task_id=t1.id, depends_on_task_id=t3.id)

        # 3. 验证 Step 追加式 Revision 与下游 needs_revalidation 传播（不自动重跑）
        s1 = plan_svc.create_step(project_id=proj_a.id, task_id=t1.id, title="设计 Schema", instruction="v1 说明")
        s2 = plan_svc.create_step(project_id=proj_a.id, task_id=t1.id, title="编写迁移脚本", instruction="v1 迁移")
        s3 = plan_svc.create_step(project_id=proj_a.id, task_id=t1.id, title="验证不变量", instruction="v1 验证")

        # 修订步骤 s1 -> s2、s3 以及下游任务 t2、t3 必须自动进入 needs_revalidation，且历史 r1 保留
        s1_updated, s1_rev2, impact = plan_svc.revise_step(
            project_id=proj_a.id,
            step_id=s1.id,
            instruction="v2 增加外键与索引约束",
            change_reason="补充索引要求",
        )
        assert s1_updated.active_revision_no == 2
        assert s1_rev2.revision_no == 2
        step_revs = plan_svc.list_step_revisions(proj_a.id, s1.id)
        assert [r.revision_no for r in step_revs] == [1, 2]
        assert step_revs[0].instruction_snapshot == "v1 说明"

        assert set(impact.impacted_step_ids) == {s2.id, s3.id}
        assert set(impact.impacted_task_ids) == {t2.id, t3.id}

        steps_now = plan_svc.list_task_steps(proj_a.id, t1.id)
        assert steps_now[0].needs_revalidation == 0
        assert steps_now[1].needs_revalidation == 1
        assert steps_now[1].platform_status == StepPlatformStatus.NEEDS_REVALIDATION.value
        assert steps_now[2].needs_revalidation == 1

        tasks_now = {t.id: t for t in plan_svc.list_project_tasks(proj_a.id)}
        assert tasks_now[t2.id].needs_revalidation == 1
        assert tasks_now[t2.id].platform_status == TaskPlatformStatus.NEEDS_REVALIDATION.value
        assert tasks_now[t3.id].needs_revalidation == 1

        # 4. 验证状态双轨分离：Agent 报完成不等于平台验收完成
        t1_after_agent = plan_svc.update_task_status(
            project_id=proj_a.id,
            task_id=t1.id,
            agent_reported_status=AgentReportedStatus.COMPLETED,
            actor_type=ActorType.MCP_AGENT,
        )
        assert t1_after_agent.agent_reported_status == AgentReportedStatus.COMPLETED.value
        assert t1_after_agent.platform_status == TaskPlatformStatus.DRAFT.value

        with pytest.raises(InvariantViolationError):
            plan_svc.update_task_status(
                project_id=proj_a.id,
                task_id=t1.id,
                platform_status=TaskPlatformStatus.COMPLETED,
                actor_type=ActorType.MCP_AGENT,
            )

        # 5. 验证 Session 必属 Project、Remote URL 脱敏与连接断开不污染平台状态
        with pytest.raises(InvariantViolationError):
            sess_svc.create_session(project_id="", title="游离会话")

        with pytest.raises(CrossProjectReferenceError):
            sess_svc.create_session(project_id=proj_a.id, title="跨项目任务会话", task_id=tb_other.id)

        # ModelEndpointDescriptor 安全校验：拒绝 URL 或 API Key
        with pytest.raises(ValueError):
            ModelEndpointDescriptor(
                provider_label="https://api.domestic-llm.example.com/v1",
                model_label="claude-compatible",
                endpoint_kind=EndpointKind.COMPATIBLE_API,
            )
        with pytest.raises(ValueError):
            ModelEndpointDescriptor(
                provider_label="Domestic Provider",
                model_label="sk-1234567890abcdef12345678",
                endpoint_kind=EndpointKind.COMPATIBLE_API,
            )

        safe_desc = ModelEndpointDescriptor(
            provider_label="Domestic Anthropic-Compatible",
            model_label="claude-sonnet-compatible",
            endpoint_kind=EndpointKind.COMPATIBLE_API,
        )
        rt_inst = rt_svc.register_runtime_instance(
            provider_key=RuntimeProviderKey.CLAUDE_CODE,
            model_endpoint_descriptor=safe_desc,
        )

        sess = sess_svc.create_session(
            project_id=proj_a.id,
            task_id=t1.id,
            runtime_instance_id=rt_inst.id,
            title="Claude Code 领域重构协作会话",
        )

        # 拒绝携带一次性凭证的 Remote URL
        with pytest.raises(SecurityBoundaryError):
            sess_svc.bind_external_session_ref(
                project_id=proj_a.id,
                session_id=sess.id,
                runtime_instance_id=rt_inst.id,
                remote_entry_kind=RemoteEntryKind.CLAUDE_REMOTE_CONTROL,
                remote_entry_url="https://claude.ai/code/session_abc?token=secret_once_token",
            )

        ref = sess_svc.bind_external_session_ref(
            project_id=proj_a.id,
            session_id=sess.id,
            runtime_instance_id=rt_inst.id,
            external_session_id="ext-cc-001",
            remote_entry_kind=RemoteEntryKind.CLAUDE_REMOTE_CONTROL,
            remote_entry_url="https://claude.ai/code",
        )
        assert ref.remote_entry_url == "https://claude.ai/code"

        # Remote 连接断开不影响会话平台管理状态
        sess_disc = sess_svc.update_session_statuses(
            project_id=proj_a.id,
            session_id=sess.id,
            connection_status=ConnectionStatus.DISCONNECTED,
            expected_status_seq=sess.status_seq,
        )
        assert sess_disc.connection_status == ConnectionStatus.DISCONNECTED.value
        assert sess_disc.platform_status == SessionPlatformStatus.ACTIVE.value
    finally:
        engine.dispose()


def test_security_remediations_path_guard_mcp_token_and_cors(tmp_path: Path) -> None:
    """验证第 6 条三大安全整改：白名单与 Junction/Symlink 逃逸拦截、Project MCP Token、受控 CORS。"""
    db_file = tmp_path / "security_check.db"
    command.upgrade(_make_alembic_config(db_file), "head")

    allowed_repo = _prepare_temp_project_dir(tmp_path, "AllowedRepo")
    outside_repo = _prepare_temp_project_dir(tmp_path, "OutsideSecretRepo")
    outside_secret = outside_repo / "secret_design.md"
    outside_secret.write_text("TOP SECRET OUTSIDE PROJECT", encoding="utf-8")

    # 1. WorkspaceDocumentReader 白名单校验
    ok_doc = WorkspaceDocumentReader.read_document(
        str(allowed_repo / "README.md"),
        allowed_roots=[allowed_repo],
    )
    assert ok_doc["ok"] is True

    # 未传入白名单默认闭合拒绝
    with pytest.raises(PermissionError):
        WorkspaceDocumentReader.read_document(str(allowed_repo / "README.md"))

    # 访问白名单外部目录文件被拒绝
    with pytest.raises(PermissionError):
        WorkspaceDocumentReader.read_document(
            str(outside_secret),
            allowed_roots=[allowed_repo],
        )

    # 在 Windows 下构造指向外部目录的目录联接 (Junction) 尝试越界逃逸读取
    junction_dir = allowed_repo / "escaped_link"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction_dir), str(outside_repo)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        junction_dir.symlink_to(outside_repo, target_is_directory=True)

    with pytest.raises(PermissionError):
        WorkspaceDocumentReader.read_document(
            str(junction_dir / "secret_design.md"),
            allowed_roots=[allowed_repo],
        )

    # 2. MCP Project-scoped Token 隔离与最小权限校验
    engine = create_sqlite_v2_engine(db_path=db_file)
    sf = create_session_factory(engine)
    try:
        proj_svc = ProjectService(sf)
        p_alpha = proj_svc.create_project(name="Alpha", root_path=str(allowed_repo))
        p_beta = proj_svc.create_project(name="Beta", root_path=str(outside_repo))

        with SqlAlchemyUnitOfWork(sf) as uow:
            issued = issue_mcp_project_token(
                uow,
                project_id=p_alpha.id,
                token_name="claude-alpha-client",
                scopes=["task:read", "progress:write"],
            )

        # 数据库中绝不保存明文 Token，events 中也不得包含明文 Token
        with sf() as s:
            stored = s.get(McpProjectTokenModel, issued.token_id)
            assert stored is not None
            assert issued.raw_token not in stored.token_hash
            evts = s.execute(select(EventModel.payload_json)).scalars().all()
            for payload_str in evts:
                assert issued.raw_token not in payload_str

        # 无 Bearer 头绝不放行（无 127.0.0.1 免密）
        with pytest.raises(SecurityBoundaryError):
            with SqlAlchemyUnitOfWork(sf) as uow:
                verify_mcp_project_token(
                    uow,
                    authorization_header=None,
                    target_project_id=p_alpha.id,
                )

        # 拿 Project Alpha 的 Token 访问 Project Beta 立即阻断
        with pytest.raises(SecurityBoundaryError):
            with SqlAlchemyUnitOfWork(sf) as uow:
                verify_mcp_project_token(
                    uow,
                    authorization_header=f"Bearer {issued.raw_token}",
                    target_project_id=p_beta.id,
                )

        # 权限 scope 不足立即阻断
        with pytest.raises(SecurityBoundaryError):
            with SqlAlchemyUnitOfWork(sf) as uow:
                verify_mcp_project_token(
                    uow,
                    authorization_header=f"Bearer {issued.raw_token}",
                    target_project_id=p_alpha.id,
                    required_scope="decision:admin",
                )

        # 合法项目与合法 scope 校验通过
        with SqlAlchemyUnitOfWork(sf) as uow:
            principal = verify_mcp_project_token(
                uow,
                authorization_header=f"Bearer {issued.raw_token}",
                target_project_id=p_alpha.id,
                required_scope="task:read",
            )
            assert principal.project_id == p_alpha.id

        # 吊销后立即失效
        with SqlAlchemyUnitOfWork(sf) as uow:
            revoke_mcp_project_token(uow, project_id=p_alpha.id, token_id=issued.token_id)

        with pytest.raises(SecurityBoundaryError):
            with SqlAlchemyUnitOfWork(sf) as uow:
                verify_mcp_project_token(
                    uow,
                    authorization_header=f"Bearer {issued.raw_token}",
                    target_project_id=p_alpha.id,
                )
    finally:
        engine.dispose()

    # 3. 验证 FastAPI 应用启动、受控 CORS 与 /api/v2/health
    meta_db = MetaDatabase(db_path=db_file)
    v2_settings = AppV2Settings(data_dir=tmp_path, db_filename=db_file.name)
    app = create_app(meta_db=meta_db, v2_settings=v2_settings)

    with TestClient(app) as client:
        # 恶意公网域名预检请求：不得返回 Access-Control-Allow-Origin: * 或回显恶意域名
        evil_resp = client.options(
            "/api/v2/health",
            headers={
                "Origin": "https://evil-attacker.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert evil_resp.headers.get("access-control-allow-origin") is None

        # 受控本机开发端口与 Tailscale 专网 IP 正常通过 CORS
        local_resp = client.options(
            "/api/v2/health",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert local_resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"

        ts_resp = client.options(
            "/api/v2/health",
            headers={
                "Origin": "http://100.101.102.103:8765",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert ts_resp.headers.get("access-control-allow-origin") == "http://100.101.102.103:8765"

        # /api/v2/health 返回完整 WAL、外键与 Schema 就绪状态
        health = client.get("/api/v2/health")
        assert health.status_code == 200
        h_body = health.json()
        assert h_body["ok"] is True
        assert h_body["schema_ready"] is True
        assert h_body["sqlite"]["journal_mode"] == "wal"
        assert h_body["sqlite"]["foreign_keys"] == 1
        assert h_body["present_table_count"] == 13

        # 路由级 /api/v1/files/read 仅放行已激活 Project 根目录内文件，拦截外部或 Junction 逃逸
        r_allowed = client.get("/api/v1/files/read", params={"path": str(allowed_repo / "README.md")})
        assert r_allowed.status_code == 200
        # 归档 p_beta 后，outside_repo 不再属于活跃白名单，直接访问或经 Junction 访问均返回 403
        with sf() as s:
            pb = s.get(ProjectModel, p_beta.id)
            assert pb is not None
            pb.status = "archived"
            s.commit()

        r_outside = client.get("/api/v1/files/read", params={"path": str(outside_secret)})
        assert r_outside.status_code == 403
        r_junction = client.get("/api/v1/files/read", params={"path": str(junction_dir / "secret_design.md")})
        assert r_junction.status_code == 403
