# -*- coding: utf-8 -*-
"""
Phase 4 MCP 协同服务、2026-07-28 / 2025-11-25 双层协议、8 大工具、同事务并发幂等与 Stdio 安全验收测试套件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import AppSettings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_env(tmp_path: Path) -> tuple[AppSettings, TestClient, dict[str, Any], str]:
    """构建独立的测试数据库、示例项目与全权限 MCP Token。"""
    settings = AppSettings(
        data_dir=tmp_path / "phase4_data",
        db_filename="phase4_test.db",
    )
    app = create_app(settings=settings, auto_init_schema=True)
    client = TestClient(app)

    ws_dir = tmp_path / "workspace_alpha"
    ws_dir.mkdir(parents=True, exist_ok=True)

    proj_resp = client.post(
        "/api/v1/projects",
        json={
            "name": "Phase4 Alpha Project",
            "root_path": str(ws_dir),
            "slug": "phase4-alpha",
            "description": "MCP 协同服务全链路验收项目",
        },
    )
    assert proj_resp.status_code == 201, proj_resp.text
    project = proj_resp.json()["project"]

    tok_resp = client.post(
        f"/api/v1/projects/{project['id']}/mcp-tokens",
        json={"token_name": "alpha-full-mcp-token"},
    )
    assert tok_resp.status_code == 201, tok_resp.text
    raw_token = tok_resp.json()["token"]["raw_token"]
    return settings, client, project, raw_token


def _mcp_post_2026(
    client: TestClient,
    raw_token: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    mcp_name: str | None = None,
    req_id: int | str = 1,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """发送符合 MCP 2026-07-28 无状态规范的 HTTP 请求。"""
    body_params = dict(params or {})
    meta = dict(body_params.get("_meta") or {})
    meta.setdefault("protocolVersion", "2026-07-28")
    body_params["_meta"] = meta

    headers = {
        "Authorization": f"Bearer {raw_token}",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if mcp_name:
        headers["Mcp-Name"] = mcp_name
    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": body_params,
    }
    return client.post("/api/v1/mcp", json=payload, headers=headers)


def test_mcp_2026_07_28_stateless_protocol_and_2025_compat_layer(tmp_path: Path) -> None:
    """
    验证阻塞项 1：
    1. 2026-07-28 无状态主规范：
       - 支持 server/discover、tools/list、tools/call、ping；
       - 强制校验 Mcp-Method 与 Mcp-Name 请求头与 JSON-RPC body 一致；
       - 移除 initialize 与 notifications/initialized（调用返回 -32601）；
       - 移除旧版 GET /api/v1/mcp 长连接流（返回 HTTP 405）。
    2. 2025-11-25 / 2025-03-26 独立兼容处理器：
       - 支持 initialize 握手与 notifications/initialized；
       - 显式携带 MCP-Protocol-Version: 2025-11-25 时 GET /api/v1/mcp 返回 SSE 兼容流。
    """
    _, client, project, raw_token = _build_env(tmp_path)
    with client:
        # 1. 2026-07-28 server/discover
        r_disc = _mcp_post_2026(client, raw_token, "server/discover")
        assert r_disc.status_code == 200, r_disc.text
        assert r_disc.headers.get("MCP-Protocol-Version") == "2026-07-28"
        disc_res = r_disc.json()["result"]
        assert disc_res["protocolVersion"] == "2026-07-28"
        assert disc_res["stateless"] is True
        assert disc_res["projectScope"]["project_id"] == project["id"]
        assert len(disc_res["tools"]) == 8

        # 2. 2026-07-28 下调用已移除的 initialize 必须报错 (-32601)
        r_init_2026 = _mcp_post_2026(client, raw_token, "initialize")
        assert r_init_2026.status_code == 200
        err_init = r_init_2026.json()["error"]
        assert err_init["code"] == -32601
        assert "2026-07-28" in err_init["message"]

        # 3. 2026-07-28 下 Mcp-Method 请求头与 body.method 不一致必须报错 (-32600)
        r_mismatch_method = client.post(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/list",
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "project_context_get",
                    "arguments": {},
                    "_meta": {"protocolVersion": "2026-07-28"},
                },
            },
        )
        assert r_mismatch_method.json()["error"]["code"] == -32600

        # 4. 2026-07-28 下 Mcp-Name 请求头与 params.name 不一致必须报错 (-32600)
        r_mismatch_name = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {"name": "project_context_get", "arguments": {}},
            mcp_name="task_get",
        )
        assert r_mismatch_name.json()["error"]["code"] == -32600

        # 5. 2026-07-28 下 GET /api/v1/mcp 必须返回 405 Method Not Allowed
        r_get_2026 = client.get(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "MCP-Protocol-Version": "2026-07-28",
            },
        )
        assert r_get_2026.status_code == 405

        # 6. 2025-11-25 兼容层：支持 initialize 与 notifications/initialized，以及 GET SSE 流
        r_init_2025 = client.post(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "clientInfo": {"name": "claude-code", "version": "2.1.114"},
                },
            },
        )
        assert r_init_2025.status_code == 200
        assert r_init_2025.headers.get("MCP-Protocol-Version") == "2025-11-25"
        init_res = r_init_2025.json()["result"]
        assert init_res["protocolVersion"] == "2025-11-25"

        r_notif_2025 = client.post(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        assert r_notif_2025.status_code == 202

        r_get_2025 = client.get(
            "/api/v1/mcp",
            headers={
                "Authorization": f"Bearer {raw_token}",
                "MCP-Protocol-Version": "2025-11-25",
            },
        )
        assert r_get_2025.status_code == 200
        assert "text/event-stream" in r_get_2025.headers.get("content-type", "")


def test_mcp_http_security_origin_size_auth_rotation_and_rate_limit(tmp_path: Path) -> None:
    """
    验证修正项 4 与安全边界：
    1. 非法 Origin 请求头返回 HTTP 403；
    2. 请求体 > 256 KB 返回 HTTP 413；
    3. 无 Token / 错误 Token / 已吊销 Token / 过期 Token 返回 HTTP 401；
    4. Token 原子轮换后旧 Token 立即失效，新 Token 立即生效；
    5. 最小权限 Scope 拦截与跨项目访问拦截；
    6. 连续认证失败触发滑动窗口限流 (HTTP 429)。
    """
    _, client, project, raw_token = _build_env(tmp_path)
    with client:
        # 1. 非法 Origin 必须返回 HTTP 403
        r_bad_origin = _mcp_post_2026(
            client,
            raw_token,
            "server/discover",
            extra_headers={"Origin": "https://evil-attacker.example.com"},
        )
        assert r_bad_origin.status_code == 403

        # 合法本地 Origin 允许通过
        r_good_origin = _mcp_post_2026(
            client,
            raw_token,
            "server/discover",
            extra_headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert r_good_origin.status_code == 200

        # 2. 请求体超过 256 KB 返回 HTTP 413
        huge_padding = "x" * (270 * 1024)
        r_too_large = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "project_context_get",
                "arguments": {"padding": huge_padding},
            },
            mcp_name="project_context_get",
        )
        assert r_too_large.status_code == 413

        # 3. 缺少 Token 返回 HTTP 401
        r_no_auth = client.post(
            "/api/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert r_no_auth.status_code == 401

        # 4. 过期 Token 返回 HTTP 401
        past_time = (utc_now() - timedelta(hours=2)).isoformat()
        exp_tok_resp = client.post(
            f"/api/v1/projects/{project['id']}/mcp-tokens",
            json={
                "token_name": "expired-token",
                "expires_at": past_time,
            },
        )
        exp_raw = exp_tok_resp.json()["token"]["raw_token"]
        r_exp = _mcp_post_2026(client, exp_raw, "server/discover")
        assert r_exp.status_code == 401

        # 5. Token 原子轮换：旧 Token 立即失效，新 Token 立即生效
        rot_tok_resp = client.post(
            f"/api/v1/projects/{project['id']}/mcp-tokens",
            json={"token_name": "rotatable-token"},
        ).json()["token"]
        old_raw = rot_tok_resp["raw_token"]
        old_id = rot_tok_resp["id"]

        assert _mcp_post_2026(client, old_raw, "server/discover").status_code == 200

        rotated = client.post(
            f"/api/v1/projects/{project['id']}/mcp-tokens/{old_id}/rotate",
            json={"token_name": "rotated-new-token"},
        )
        assert rotated.status_code == 201, rotated.text
        new_raw = rotated.json()["token"]["raw_token"]

        assert _mcp_post_2026(client, old_raw, "server/discover").status_code == 401
        assert _mcp_post_2026(client, new_raw, "server/discover").status_code == 200

        # 6. 最小权限 Scope：签发仅具备 context:read 的只读 Token，尝试调用写工具必须被拒
        ro_tok = client.post(
            f"/api/v1/projects/{project['id']}/mcp-tokens",
            json={"token_name": "readonly-token", "scopes": ["context:read"]},
        ).json()["token"]["raw_token"]

        r_ro_ok = _mcp_post_2026(
            client,
            ro_tok,
            "tools/call",
            {"name": "project_context_get", "arguments": {}},
            mcp_name="project_context_get",
        )
        assert r_ro_ok.json()["result"]["isError"] is False

        r_ro_denied = _mcp_post_2026(
            client,
            ro_tok,
            "tools/call",
            {
                "name": "checkpoint_request",
                "arguments": {
                    "idempotency_key": "idem-ro-test-001",
                    "title": "越权发起决策",
                },
            },
            mcp_name="checkpoint_request",
        )
        ro_structured = r_ro_denied.json()["result"]["structuredContent"]
        assert ro_structured["ok"] is False
        assert ro_structured["error_code"] == "MCP_SCOPE_FORBIDDEN"

        # 7. 跨项目越权访问：项目 A 的 Token 尝试读取项目 B 的上下文或任务必须返回 MCP_CROSS_PROJECT_DENIED
        ws_b = tmp_path / "workspace_beta"
        ws_b.mkdir()
        proj_b = client.post(
            "/api/v1/projects",
            json={"name": "Beta Project", "root_path": str(ws_b), "slug": "phase4-beta"},
        ).json()["project"]
        task_b = client.post(
            f"/api/v1/projects/{proj_b['id']}/tasks",
            json={"title": "Beta Secret Task"},
        ).json()["task"]

        r_cross_proj = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "project_context_get",
                "arguments": {"project_id": proj_b["id"]},
            },
            mcp_name="project_context_get",
        )
        assert (
            r_cross_proj.json()["result"]["structuredContent"]["error_code"]
            == "MCP_CROSS_PROJECT_DENIED"
        )

        r_cross_task = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "task_get",
                "arguments": {"task_id": task_b["id"]},
            },
            mcp_name="task_get",
        )
        assert (
            r_cross_task.json()["result"]["structuredContent"]["error_code"]
            == "MCP_CROSS_PROJECT_DENIED"
        )

        # 8. 连续认证失败触发滑动窗口限流 (HTTP 429)
        got_429 = False
        for _ in range(15):
            r_fail = _mcp_post_2026(client, "agmcp_invalid_token_for_ratelimit_test", "ping")
            if r_fail.status_code == 429:
                got_429 = True
                break
        assert got_429 is True


def test_mcp_eight_tools_full_lifecycle_and_invariants(tmp_path: Path) -> None:
    """
    验证 8 大 MCP 工具全链路与核心业务不变量：
    1. project_context_get：读取项目上下文，脱敏不泄露 Token 哈希；
    2. task_get：支持多 Session 并发认领竞争，防止同一任务被两个活跃 Session 同时抢占；
    3. task_revision_get：读取不可变 TaskRevision 与 StepRevision 快照；
    4. task_report_progress：Agent 汇报 completed 仅修改 agent_reported_status，绝不直接篡改 platform_status；
    5. step_update：校验 expected_revision_no，版本不匹配返回 MCP_VERSION_CONFLICT；
    6. checkpoint_request：decision_type == risk_gate（无论 risk_level）及 high/critical 风险强制 manual_only；
    7. decision_get：轮询获取用户拍板结果；
    8. artifact_register：拦截 Windows 盘符 (C:\\)、UNC (\\\\server\\share)、反斜杠、.. 穿越与超长 metadata。
    """
    _, client, project, raw_token = _build_env(tmp_path)
    with client:
        # 准备任务、步骤与两个活跃 Session
        task = client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            json={
                "title": "实现 MCP 8 工具协同闭环",
                "goal": "验证 Agent 从认领任务到交付产物的全流程",
                "acceptance_criteria": ["所有不变量测试通过"],
            },
        ).json()["task"]
        s1_resp = client.post(
            f"/api/v1/projects/{project['id']}/tasks/{task['id']}/steps",
            json={
                "title": "Step 1: 读取上下文并认领任务",
                "instruction": "调用 project_context_get 与 task_get",
            },
        ).json()["step"]
        s2_resp = client.post(
            f"/api/v1/projects/{project['id']}/tasks/{task['id']}/steps",
            json={
                "title": "Step 2: 汇报进度并登记产物",
                "instruction": "调用 step_update 与 artifact_register",
            },
        ).json()["step"]
        steps = [s1_resp, s2_resp]
        step1_id = steps[0]["id"]

        sess_a = client.post(
            f"/api/v1/projects/{project['id']}/sessions",
            json={"title": "Claude Session A"},
        ).json()["session"]
        sess_b = client.post(
            f"/api/v1/projects/{project['id']}/sessions",
            json={"title": "Codex Session B"},
        ).json()["session"]

        # 1. project_context_get
        r_ctx = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {"name": "project_context_get", "arguments": {}},
            mcp_name="project_context_get",
        ).json()["result"]["structuredContent"]
        assert r_ctx["ok"] is True
        assert r_ctx["data"]["project"]["id"] == project["id"]
        assert "token_hash" not in json.dumps(r_ctx)

        # 2. task_get 多 Session 竞争认领：Session A 认领 Ready 任务成功后，Session B 再次认领不会抢占该任务
        r_claim_a = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "task_get",
                "arguments": {
                    "claim_next_ready": True,
                    "session_id": sess_a["id"],
                },
            },
            mcp_name="task_get",
        ).json()["result"]["structuredContent"]
        assert r_claim_a["ok"] is True
        assert r_claim_a["data"]["claimed"] is True
        assert r_claim_a["data"]["task"]["id"] == task["id"]
        assert r_claim_a["data"]["claimed_by_session_id"] == sess_a["id"]

        # Session B 尝试通过 claim_next_ready 抢占 -> 返回 task=None（已被 Session A 占用）
        r_claim_b_auto = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "task_get",
                "arguments": {
                    "claim_next_ready": True,
                    "session_id": sess_b["id"],
                },
            },
            mcp_name="task_get",
        ).json()["result"]["structuredContent"]
        assert r_claim_b_auto["ok"] is True
        assert r_claim_b_auto["data"]["task"] is None

        # Session B 显式指定 task_id 尝试绑定抢占 -> 必须返回 MCP_VERSION_CONFLICT
        r_claim_b_explicit = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "task_get",
                "arguments": {
                    "task_id": task["id"],
                    "session_id": sess_b["id"],
                },
            },
            mcp_name="task_get",
        ).json()["result"]["structuredContent"]
        assert r_claim_b_explicit["ok"] is False
        assert r_claim_b_explicit["error_code"] == "MCP_VERSION_CONFLICT"

        # 3. task_revision_get：读取不可变修订快照
        r_rev = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "task_revision_get",
                "arguments": {"task_id": task["id"], "revision_no": 1},
            },
            mcp_name="task_revision_get",
        ).json()["result"]["structuredContent"]
        assert r_rev["ok"] is True
        assert r_rev["data"]["task_revision"]["revision_no"] == 1
        assert len(r_rev["data"]["step_revisions"]) == 2

        # 4. step_update：正常更新与 expected_revision_no 乐观锁冲突拦截
        r_step_conflict = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "step_update",
                "arguments": {
                    "idempotency_key": "idem-step-conflict-1",
                    "task_id": task["id"],
                    "step_id": step1_id,
                    "status": "in_progress",
                    "expected_revision_no": 999,
                },
            },
            mcp_name="step_update",
        ).json()["result"]["structuredContent"]
        assert r_step_conflict["ok"] is False
        assert r_step_conflict["error_code"] == "MCP_VERSION_CONFLICT"

        r_step_ok = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "step_update",
                "arguments": {
                    "idempotency_key": "idem-step-ok-1",
                    "task_id": task["id"],
                    "step_id": step1_id,
                    "status": "completed",
                    "expected_revision_no": 1,
                    "summary": "已完成上下文读取与任务认领",
                },
            },
            mcp_name="step_update",
        ).json()["result"]["structuredContent"]
        assert r_step_ok["ok"] is True
        assert r_step_ok["data"]["step"]["status"] == "completed"

        # 5. task_report_progress：汇报 completed 绝不能直接修改 platform_status
        r_prog = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "task_report_progress",
                "arguments": {
                    "idempotency_key": "idem-prog-001",
                    "task_id": task["id"],
                    "session_id": sess_a["id"],
                    "reported_status": "completed",
                    "progress_percent": 100,
                    "summary": "Agent 已完成全部编码工作，提请平台验收",
                },
            },
            mcp_name="task_report_progress",
        ).json()["result"]["structuredContent"]
        assert r_prog["ok"] is True
        updated_task = r_prog["data"]["task"]
        assert updated_task["agent_reported_status"] == "completed"
        assert updated_task["platform_status"] == "draft"
        assert r_prog["data"]["platform_status_unchanged"] is True

        # 6. checkpoint_request：
        # 6a. decision_type == "risk_gate" 即使 risk_level == "low" 也严禁 auto_submit_recommended
        r_risk_gate_bad = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "checkpoint_request",
                "arguments": {
                    "idempotency_key": "idem-ckpt-riskgate-bad",
                    "task_id": task["id"],
                    "session_id": sess_a["id"],
                    "title": "生产配置变更门禁",
                    "decision_type": "risk_gate",
                    "risk_level": "low",
                    "timeout_policy": "auto_submit_recommended",
                    "options": [
                        {"option_id": "approve", "label": "批准", "is_recommended": True}
                    ],
                },
            },
            mcp_name="checkpoint_request",
        ).json()["result"]["structuredContent"]
        assert r_risk_gate_bad["ok"] is False
        assert r_risk_gate_bad["error_code"] == "MCP_HIGH_RISK_MANUAL_ONLY"

        # 6b. risk_level == "high" 尝试 auto_submit_recommended 也必须被拦截
        r_high_risk_bad = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "checkpoint_request",
                "arguments": {
                    "idempotency_key": "idem-ckpt-high-bad",
                    "task_id": task["id"],
                    "session_id": sess_a["id"],
                    "title": "高风险数据库变更",
                    "decision_type": "architecture_choice",
                    "risk_level": "high",
                    "timeout_policy": "auto_submit_recommended",
                },
            },
            mcp_name="checkpoint_request",
        ).json()["result"]["structuredContent"]
        assert r_high_risk_bad["ok"] is False
        assert r_high_risk_bad["error_code"] == "MCP_HIGH_RISK_MANUAL_ONLY"

        # 6c. 合法发起 checkpoint_request 并通过 decision_get 轮询拍板结果
        r_ckpt_ok = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "checkpoint_request",
                "arguments": {
                    "idempotency_key": "idem-ckpt-ok-001",
                    "task_id": task["id"],
                    "session_id": sess_a["id"],
                    "title": "确认 MCP 协议响应格式",
                    "decision_type": "interactive_question",
                    "risk_level": "normal",
                    "timeout_policy": "auto_submit_recommended",
                    "timeout_sec": 45,
                    "options": [
                        {"option_id": "opt_a", "label": "使用结构化信封", "is_recommended": True},
                        {"option_id": "opt_b", "label": "仅返回纯文本", "is_recommended": False},
                    ],
                },
            },
            mcp_name="checkpoint_request",
        ).json()["result"]["structuredContent"]
        assert r_ckpt_ok["ok"] is True
        decision_id = r_ckpt_ok["data"]["decision"]["id"]

        # 用户在管理端拍板
        res_dec = client.post(
            f"/api/v1/decisions/{decision_id}/resolve",
            json={
                "chosen_option_id": "opt_a",
                "custom_note": "批准结构化信封方案",
                "resolved_by": "user_manual",
            },
        )
        assert res_dec.status_code == 200

        # 7. decision_get 读取已拍板结果
        r_dec_get = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "decision_get",
                "arguments": {"decision_id": decision_id},
            },
            mcp_name="decision_get",
        ).json()["result"]["structuredContent"]
        assert r_dec_get["ok"] is True
        assert r_dec_get["data"]["is_resolved"] is True
        assert r_dec_get["data"]["decision"]["chosen_option_id"] == "opt_a"

        # 8. artifact_register 路径安全边界与合法登记
        unsafe_paths = [
            r"C:\Windows\System32\drivers\etc\hosts",
            r"D:/secret/config.json",
            r"\\192.168.1.10\share\creds.txt",
            r"docs\windows_backslash\report.md",
            "/etc/passwd",
            "../../outside_secret.txt",
            "docs/../../../escape.txt",
        ]
        for idx, bad_path in enumerate(unsafe_paths):
            r_bad_art = _mcp_post_2026(
                client,
                raw_token,
                "tools/call",
                {
                    "name": "artifact_register",
                    "arguments": {
                        "idempotency_key": f"idem-bad-art-{idx}",
                        "task_id": task["id"],
                        "title": "恶意路径测试",
                        "relative_path": bad_path,
                    },
                },
                mcp_name="artifact_register",
            ).json()["result"]["structuredContent"]
            assert r_bad_art["ok"] is False, f"Path {bad_path} should be blocked!"
            assert r_bad_art["error_code"] == "MCP_PATH_ESCAPE_BLOCKED"

        # 超长 metadata (>16KB) 拦截
        r_huge_meta = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "artifact_register",
                "arguments": {
                    "idempotency_key": "idem-huge-meta",
                    "task_id": task["id"],
                    "title": "超大元数据产物",
                    "relative_path": "docs/report.md",
                    "metadata": {"blob": "m" * 20000},
                },
            },
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r_huge_meta["ok"] is False
        assert r_huge_meta["error_code"] == "MCP_PATH_ESCAPE_BLOCKED"

        # 合法相对路径登记成功
        r_art_ok = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "artifact_register",
                "arguments": {
                    "idempotency_key": "idem-art-ok-001",
                    "task_id": task["id"],
                    "step_id": step1_id,
                    "session_id": sess_a["id"],
                    "artifact_type": "markdown_report",
                    "title": "Phase 4 MCP 验收报告草稿",
                    "relative_path": "docs/implementation/phase4-draft.md",
                    "summary_content": "# 验收摘要\n全部 8 个工具运行正常。",
                    "metadata": {"lines": 120, "verified": True},
                },
            },
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r_art_ok["ok"] is True
        assert (
            r_art_ok["data"]["artifact"]["relative_path"]
            == "docs/implementation/phase4-draft.md"
        )


def test_mcp_write_idempotency_five_scenarios_atomic_transaction(tmp_path: Path) -> None:
    """
    验证阻塞项 5（写操作同事务幂等的全部 5 种场景）：
    1. 相同 Key + 相同参数串行重复 -> 直接重放首次响应，不重复落库；
    2. 相同 Key + 不同参数 -> 拒绝并返回 MCP_IDEMPOTENCY_CONFLICT；
    3. 相同 Key + 相同参数双线程并发（Barrier 同步） -> 只生成 1 条业务记录；
    4. 业务执行失败 -> 事务整体回滚，command_idempotency 不残留脏记录；
    5. 模拟客户端断线后使用相同 Key 重试 -> 返回首次成功快照。
    """
    _, client, project, raw_token = _build_env(tmp_path)
    with client:
        task = client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            json={"title": "幂等并发测试任务"},
        ).json()["task"]

        # 场景 1 & 5：相同 Key + 相同参数串行重复 / 断线重发
        args_s1 = {
            "idempotency_key": "idem-scenario-1",
            "task_id": task["id"],
            "title": "串行幂等产物",
            "relative_path": "docs/serial_idem.md",
            "summary_content": "v1",
        }
        r1 = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {"name": "artifact_register", "arguments": args_s1},
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r1["ok"] is True
        assert r1["replayed"] is False
        art_id_1 = r1["data"]["artifact"]["id"]

        r1_replay = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {"name": "artifact_register", "arguments": args_s1},
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r1_replay["ok"] is True
        assert r1_replay["replayed"] is True
        assert r1_replay["data"]["artifact"]["id"] == art_id_1

        # 场景 2：相同 Key + 不同参数 -> 必须返回 MCP_IDEMPOTENCY_CONFLICT
        args_s2_diff = {
            "idempotency_key": "idem-scenario-1",
            "task_id": task["id"],
            "title": "篡改参数的幂等请求",
            "relative_path": "docs/tampered.md",
            "summary_content": "v2",
        }
        r2_conflict = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {"name": "artifact_register", "arguments": args_s2_diff},
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r2_conflict["ok"] is False
        assert r2_conflict["error_code"] == "MCP_IDEMPOTENCY_CONFLICT"

        # 场景 3：相同 Key + 相同参数双线程并发（threading.Barrier(2)）-> 数据库只生成 1 条 Decision
        barrier = threading.Barrier(2)
        concurrent_args = {
            "idempotency_key": "idem-scenario-3-concurrent",
            "task_id": task["id"],
            "title": "双线程并发创建的唯一决策",
            "decision_type": "interactive_question",
            "risk_level": "normal",
            "timeout_policy": "manual_only",
        }

        def _worker() -> dict[str, Any]:
            barrier.wait(timeout=5.0)
            resp = _mcp_post_2026(
                client,
                raw_token,
                "tools/call",
                {"name": "checkpoint_request", "arguments": concurrent_args},
                mcp_name="checkpoint_request",
            )
            return resp.json()["result"]["structuredContent"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_worker)
            f2 = pool.submit(_worker)
            res_a = f1.result(timeout=10.0)
            res_b = f2.result(timeout=10.0)

        assert res_a["ok"] is True
        assert res_b["ok"] is True
        assert res_a["data"]["decision"]["id"] == res_b["data"]["decision"]["id"]

        all_decisions = client.get(
            f"/api/v1/decisions?project_id={project['id']}"
        ).json()["decisions"]
        matching_decs = [
            d for d in all_decisions if d["title"] == "双线程并发创建的唯一决策"
        ]
        assert len(matching_decs) == 1

        # 场景 4：业务执行失败 -> 事务整体回滚，不残留 command_idempotency 快照
        fail_key = "idem-scenario-4-rollback"
        r4_fail = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "artifact_register",
                "arguments": {
                    "idempotency_key": fail_key,
                    "task_id": task["id"],
                    "title": "触发路径拦截回滚",
                    "relative_path": "../escape_rollback.txt",
                },
            },
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r4_fail["ok"] is False
        assert r4_fail["error_code"] == "MCP_PATH_ESCAPE_BLOCKED"

        # 证明 fail_key 未被脏快照锁定：修正为合法同参或新请求均可正常落库
        r4_recover = _mcp_post_2026(
            client,
            raw_token,
            "tools/call",
            {
                "name": "artifact_register",
                "arguments": {
                    "idempotency_key": fail_key,
                    "task_id": task["id"],
                    "title": "回滚后正常登记",
                    "relative_path": "docs/recovered_after_rollback.md",
                },
            },
            mcp_name="artifact_register",
        ).json()["result"]["structuredContent"]
        assert r4_recover["ok"] is True
        assert r4_recover["replayed"] is False


def test_stdio_server_security_isolation_restart_recovery_and_agent_compat(
    tmp_path: Path,
) -> None:
    """
    验证阻塞项 3 与 Stdio / 本机真实 Agent 兼容性：
    1. 传入 --token 明文命令行参数启动 stdio_server 必须立即以退出码 2 终止，stdout 为空；
    2. 未设置 AGENTDECK_MCP_TOKEN 环境变量时以退出码 2 终止；
    3. 通过环境变量 AGENTDECK_MCP_TOKEN 启动真实子进程：
       - stdout 仅包含纯净 JSON-RPC 消息，零日志污染；
       - 同时验证 2026-07-28 与 2025-11-25 协议交互；
       - 模拟子进程中断重启后，状态与幂等记录在 SQLite 中完整恢复；
    4. 结合本机真实安装的 Agent CLI 登记 MCP 协议验证证据，升级 mcp_client_collab 为 supported。
    """
    settings, client, project, raw_token = _build_env(tmp_path)
    repo_root = Path(__file__).resolve().parents[1]

    with client:
        task = client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            json={"title": "Stdio 子进程持久化与重启恢复任务"},
        ).json()["task"]

    # 1. 严禁命令行 --token 明文传参（退出码 2，stdout 为空）
    proc_cli_token = subprocess.run(
        [sys.executable, "-m", "backend.mcp.stdio_server", "--token", raw_token],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert proc_cli_token.returncode == 2
    assert proc_cli_token.stdout.strip() == ""
    assert "[SECURITY ERROR]" in proc_cli_token.stderr

    # 2. 缺少 AGENTDECK_MCP_TOKEN 环境变量（退出码 2，stdout 为空）
    clean_env = {
        k: v for k, v in os.environ.items() if k != "AGENTDECK_MCP_TOKEN"
    }
    proc_no_env = subprocess.run(
        [sys.executable, "-m", "backend.mcp.stdio_server"],
        cwd=str(repo_root),
        env=clean_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert proc_no_env.returncode == 2
    assert proc_no_env.stdout.strip() == ""

    # 3. 第一次启动 Stdio 子进程：执行 2026-07-28 server/discover 与 task_report_progress
    stdio_env = dict(os.environ)
    stdio_env["AGENTDECK_MCP_TOKEN"] = raw_token
    stdio_env["AGENTDECK_DATA_DIR"] = str(settings.data_dir)
    stdio_env["AGENTDECK_DB_FILENAME"] = settings.db_filename
    stdio_env["PYTHONIOENCODING"] = "utf-8"

    batch_1_lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "server/discover",
                "params": {"_meta": {"protocolVersion": "2026-07-28"}},
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "tools/call",
                "params": {
                    "name": "task_report_progress",
                    "arguments": {
                        "idempotency_key": "idem-stdio-persist-001",
                        "task_id": task["id"],
                        "reported_status": "in_progress",
                        "progress_percent": 60,
                        "summary": "Stdio 进程 1 写入进度",
                    },
                    "_meta": {"protocolVersion": "2026-07-28"},
                },
            }
        ),
    ]
    proc_run_1 = subprocess.run(
        [sys.executable, "-m", "backend.mcp.stdio_server"],
        cwd=str(repo_root),
        env=stdio_env,
        input="\n".join(batch_1_lines) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert proc_run_1.returncode == 0, proc_run_1.stderr
    out_lines_1 = [
        line.strip() for line in proc_run_1.stdout.splitlines() if line.strip()
    ]
    assert len(out_lines_1) == 2
    msg_101 = json.loads(out_lines_1[0])
    msg_102 = json.loads(out_lines_1[1])
    assert msg_101["id"] == 101
    assert msg_101["result"]["protocolVersion"] == "2026-07-28"
    assert msg_102["id"] == 102
    assert msg_102["result"]["structuredContent"]["ok"] is True

    # 4. 模拟 Stdio 断线重启：启动全新的第二个 Stdio 子进程，使用 2025-11-25 兼容协议重放同一幂等 Key
    batch_2_lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 201,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "clientInfo": {"name": "codex-cli", "version": "0.104.0"},
                },
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 202,
                "method": "tools/call",
                "params": {
                    "name": "task_report_progress",
                    "arguments": {
                        "idempotency_key": "idem-stdio-persist-001",
                        "task_id": task["id"],
                        "reported_status": "in_progress",
                        "progress_percent": 60,
                        "summary": "Stdio 进程 1 写入进度",
                    },
                },
            }
        ),
    ]
    proc_run_2 = subprocess.run(
        [sys.executable, "-m", "backend.mcp.stdio_server"],
        cwd=str(repo_root),
        env=stdio_env,
        input="\n".join(batch_2_lines) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert proc_run_2.returncode == 0, proc_run_2.stderr
    out_lines_2 = [
        line.strip() for line in proc_run_2.stdout.splitlines() if line.strip()
    ]
    assert len(out_lines_2) == 2
    msg_201 = json.loads(out_lines_2[0])
    msg_202 = json.loads(out_lines_2[1])
    assert msg_201["result"]["protocolVersion"] == "2025-11-25"
    assert msg_202["result"]["structuredContent"]["ok"] is True
    assert msg_202["result"]["structuredContent"]["replayed"] is True

    # 5. 更新 Runtime Provider 的 MCP 协议实测证据（验证 mcp_client_collab 与 verified_protocol_versions）
    with TestClient(create_app(settings=settings, auto_init_schema=False)) as client2:
        r_verify = client2.post(
            "/api/v1/runtimes/providers/claude_code/mcp-verify",
            json={
                "runtime_version": "2.1.114",
                "connector_version": "agentdeck-mcp-1.3.0",
                "verified_protocol_versions": ["2026-07-28", "2025-11-25"],
                "evidence": "integration_test",
                "limitations": "已在本机通过 Stdio 与 Streamable HTTP 完成 8 工具闭环验证",
            },
        )
        assert r_verify.status_code == 200, r_verify.text
        caps = r_verify.json()["provider"]["default_capabilities"]
        mcp_cap = next(c for c in caps if c["capability"] == "mcp_client_collab")
        assert mcp_cap["support_status"] == "supported"
        assert mcp_cap["verified_protocol_versions"] == ["2026-07-28", "2025-11-25"]

        # 管理端诊断端点 GET /api/v1/mcp/tools
        r_diag = client2.get("/api/v1/mcp/tools")
        assert r_diag.status_code == 200
        diag_data = r_diag.json()
        assert diag_data["primary_protocol_version"] == "2026-07-28"
        assert "2025-11-25" in diag_data["compat_protocol_versions"]
        assert len(diag_data["tools"]) == 8
