# -*- coding: utf-8 -*-
"""
Clean-Slate Phase 2 `/api/v1/*` 全套 REST API 端到端契约测试套件。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import AppSettings


def test_phase2_rest_api_full_lifecycle(tmp_path: Path) -> None:
    """端到端验证 `/api/v1/*` 项目、任务、步骤、修订、会话、执行、交互式 Decision 及事件流闭环。"""
    data_dir = tmp_path / "api_data"
    repo_dir = tmp_path / "Workspaces" / "AgentDeckDemo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "README.md").write_text("# AgentDeck Demo Repo\n", encoding="utf-8")

    settings = AppSettings(data_dir=data_dir, db_filename="api_test.db")
    app = create_app(settings=settings, auto_init_schema=True)

    with TestClient(app) as client:
        # 1. Health & Overview
        r_health = client.get("/api/v1/health")
        assert r_health.status_code == 200
        assert r_health.json()["canonical_table_count"] == 21

        # 2. 创建项目
        r_proj = client.post(
            "/api/v1/projects",
            json={
                "name": "AgentDeck 核心重构",
                "root_path": str(repo_dir),
                "description": "面向多项目的 AI 研发异步编排驾驶舱",
                "summary_next_action": "完成 Phase 2 前后端闭环",
            },
        )
        assert r_proj.status_code == 201, r_proj.text
        proj = r_proj.json()["project"]
        pid = proj["id"]

        # 3. 签发项目级 MCP Token
        r_tok = client.post(
            f"/api/v1/projects/{pid}/mcp-tokens",
            json={
                "token_name": "claude-code-local",
                "scopes": ["project:read", "task:read", "checkpoint:request"],
            },
        )
        assert r_tok.status_code == 201
        assert r_tok.json()["token"]["raw_token"].startswith("agmcp_")

        # 4. 创建父子任务、步骤与依赖
        r_t1 = client.post(
            f"/api/v1/projects/{pid}/tasks",
            json={
                "title": "搭建 SQLAlchemy 2.x 领域层",
                "goal": "实现 21 张规范表与工作单元",
                "acceptance_criteria": ["WAL 开启", "外键开启"],
                "priority": "high",
            },
        )
        assert r_t1.status_code == 201
        t1_id = r_t1.json()["task"]["id"]

        r_t2 = client.post(
            f"/api/v1/projects/{pid}/tasks",
            json={
                "title": "实现 React 项目管理前端",
                "goal": "对接 /api/v1 接口",
                "priority": "medium",
            },
        )
        assert r_t2.status_code == 201
        t2_id = r_t2.json()["task"]["id"]

        r_dep = client.post(
            f"/api/v1/projects/{pid}/tasks/{t2_id}/dependencies",
            json={"depends_on_task_id": t1_id},
        )
        assert r_dep.status_code == 201

        r_s1 = client.post(
            f"/api/v1/projects/{pid}/tasks/{t1_id}/steps",
            json={"title": "定义 ORM 模型", "instruction": "全表全字段中文注释"},
        )
        assert r_s1.status_code == 201
        s1_id = r_s1.json()["step"]["id"]

        # 5. 将下游任务 t2 置为 in_progress，再修订上游任务 t1 -> 验证 t2 自动标为 needs_revalidation
        client.patch(
            f"/api/v1/projects/{pid}/tasks/{t2_id}/status",
            json={"platform_status": "in_progress"},
        )
        r_rev = client.post(
            f"/api/v1/projects/{pid}/tasks/{t1_id}/revisions",
            json={
                "title": "搭建 SQLAlchemy 2.x 领域层（含 Decision 幂等表）",
                "goal": "升级至 21 张表",
                "change_reason": "Clean-Slate 全量收敛",
            },
        )
        assert r_rev.status_code == 200
        assert r_rev.json()["revision"]["revision_no"] == 2
        assert t2_id in r_rev.json()["revalidation_impact"]["impacted_task_ids"]

        # 6. 登记 RuntimeInstance 与 Session + ExternalSessionRef
        r_inst = client.post(
            "/api/v1/runtimes/instances",
            json={
                "provider_key": "claude_code",
                "display_name": "Claude Code @ Local PC",
                "control_level": "L1",
                "health_status": "online",
                "model_endpoint_descriptor": {
                    "provider_label": "GLM Coding Plan",
                    "model_label": "GLM-4.6",
                    "endpoint_kind": "compatible_api",
                },
            },
        )
        assert r_inst.status_code == 201
        inst_id = r_inst.json()["instance"]["id"]

        r_sess = client.post(
            f"/api/v1/projects/{pid}/sessions",
            json={
                "title": "Claude Code 本地 MCP 协同会话",
                "task_id": t1_id,
                "runtime_instance_id": inst_id,
                "source_channel": "mcp",
            },
        )
        assert r_sess.status_code == 201
        sess_id = r_sess.json()["session"]["id"]

        r_ext = client.post(
            f"/api/v1/projects/{pid}/sessions/{sess_id}/external-refs",
            json={
                "runtime_instance_id": inst_id,
                "external_session_id": "cc-sess-001",
                "remote_entry_kind": "claude_remote_control",
                "remote_entry_url": "https://claude.ai/code",
                "link_status": "active",
            },
        )
        assert r_ext.status_code == 201

        # 7. 创建交互式 Decision -> 验证 waiting_interaction 状态与 45s 推荐选项幂等提交
        r_dec = client.post(
            f"/api/v1/projects/{pid}/decisions",
            json={
                "title": "确认前端路由组织方式",
                "context_summary": "是否在单页工作台内支持项目切换与全局待决策抽屉",
                "decision_type": "interactive_question",
                "risk_level": "normal",
                "timeout_policy": "auto_submit_recommended",
                "timeout_sec": 45,
                "task_id": t1_id,
                "step_id": s1_id,
                "session_id": sess_id,
                "options": [
                    {
                        "option_id": "opt_1",
                        "label": "方案 1：左侧导航 + 顶部项目切换器 + 全局决策弹窗（推荐）",
                        "description": "兼顾桌面端多栏效率与手机端底部导航",
                        "is_recommended": True,
                    },
                    {
                        "option_id": "opt_2",
                        "label": "方案 2：仅保留单列表视图",
                        "description": "信息密度较低",
                        "is_recommended": False,
                    },
                ],
            },
        )
        assert r_dec.status_code == 201, r_dec.text
        dec_id = r_dec.json()["decision"]["id"]

        # 检查 overview 中 pending_decisions 计数为 1，且会话状态为 waiting_interaction
        r_ov = client.get("/api/v1/overview")
        assert r_ov.status_code == 200
        assert r_ov.json()["counts"]["pending_decisions"] == 1

        r_sess_detail = client.get(f"/api/v1/sessions/{sess_id}")
        assert r_sess_detail.status_code == 200
        assert r_sess_detail.json()["session"]["platform_status"] == "waiting_interaction"

        # 携带 Idempotency-Key 提交决策答案
        r_resolve1 = client.post(
            f"/api/v1/decisions/{dec_id}/resolve",
            headers={"Idempotency-Key": "ui-submit-001"},
            json={
                "chosen_option_id": "opt_1",
                "custom_note": "采用推荐的多栏+全局弹窗方案",
                "resolved_by": "user_manual",
            },
        )
        assert r_resolve1.status_code == 200
        assert r_resolve1.json()["is_idempotent_replay"] is False
        assert r_resolve1.json()["decision"]["status"] == "resolved"

        # 重复提交相同 Idempotency-Key -> 幂等重放成功
        r_resolve2 = client.post(
            f"/api/v1/decisions/{dec_id}/resolve",
            headers={"Idempotency-Key": "ui-submit-001"},
            json={
                "chosen_option_id": "opt_1",
                "custom_note": "采用推荐的多栏+全局弹窗方案",
                "resolved_by": "user_manual",
            },
        )
        assert r_resolve2.status_code == 200
        assert r_resolve2.json()["is_idempotent_replay"] is True

        # 提交冲突选项 -> 返回 409 Conflict
        r_conflict = client.post(
            f"/api/v1/decisions/{dec_id}/resolve",
            headers={"Idempotency-Key": "ui-submit-002"},
            json={"chosen_option_id": "opt_2"},
        )
        assert r_conflict.status_code == 409

        # 8. 受控文档读取与在线备份
        r_doc = client.get(
            "/api/v1/workspace/doc",
            params={"path": str(repo_dir / "README.md"), "project_id": pid},
        )
        assert r_doc.status_code == 200
        assert "AgentDeck Demo Repo" in r_doc.json()["content"]

        r_bak = client.post("/api/v1/system/backup", params={"label": "api_e2e"})
        assert r_bak.status_code == 200
        assert Path(r_bak.json()["backup_path"]).exists()

        # 9. 事件流查询
        r_evts = client.get("/api/v1/events", params={"project_id": pid})
        assert r_evts.status_code == 200
        evt_types = {e["event_type"] for e in r_evts.json()["events"]}
        assert "project.created" in evt_types
        assert "task.revision_created" in evt_types
        assert "decision.created" in evt_types
        assert "decision.resolved" in evt_types

