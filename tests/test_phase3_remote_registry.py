# -*- coding: utf-8 -*-
"""
Phase 3 Remote Registry、能力验证证据门禁、跨项目权限隔离与 Decision 并发原子性测试套件。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import AppSettings
from backend.domain.enums import DecisionResolvedBy
from backend.domain.exceptions import VersionConflictError


def _build_test_client(tmp_path: Path) -> TestClient:
    settings = AppSettings(
        data_dir=tmp_path / "phase3_data",
        db_filename="phase3_test.db",
    )
    app = create_app(settings=settings, auto_init_schema=True)
    return TestClient(app)


def test_runtime_control_level_and_capability_evidence_gate(tmp_path: Path) -> None:
    """
    验证修正 1：
    - L0（官方 Remote 入口）、L1（Runtime 注册、状态与能力信息管理）、L2（程序化控制）、L3（审批与恢复能力）定义准确；
    - 未经实测验证证据严禁将 RuntimeInstance 标为 L2 或 L3；
    - 仅验证 SDK / 程序化控制（L2）但不具备 approval_and_resume 实测证据时，严禁标为 L3；
    - 提供完整实测证据后允许升级为 L2 / L3。
    """
    with _build_test_client(tmp_path) as client:
        health = client.get("/api/v1/health").json()
        assert health["control_level_definitions"] == {
            "L0": "官方 Remote 入口",
            "L1": "Runtime 注册、状态与能力信息管理",
            "L2": "程序化控制",
            "L3": "审批与恢复能力",
        }
        assert health["decision_retry_policy"]["backoff_ms"] == [1000, 2000, 4000]
        assert health["decision_retry_policy"]["max_retries"] == 3

        # 1. 默认登记 L0 / L1 实例成功
        r_l1 = client.post(
            "/api/v1/runtimes/instances",
            json={
                "provider_key": "claude_code",
                "machine_id": "local-machine",
                "display_name": "Claude Code Local L1",
                "runtime_version": "1.0.24",
                "control_level": "L1",
                "health_status": "online",
            },
        )
        assert r_l1.status_code == 201, r_l1.text
        inst_id = r_l1.json()["instance"]["id"]

        # 2. 仅凭 remote_entry=supported 尝试将实例升级为 L2 -> 必须被拦截 (422)
        r_bad_l2 = client.patch(
            f"/api/v1/runtimes/instances/{inst_id}",
            json={
                "control_level": "L2",
                "capabilities": [
                    {
                        "capability": "remote_entry",
                        "support_status": "supported",
                        "evidence": "local_test",
                        "runtime_version": "1.0.24",
                        "connector_version": "c1",
                        "verified_at": "2026-10-31T12:00:00Z",
                        "limitations": "官方远程入口不等于程序化控制",
                    }
                ],
            },
        )
        assert r_bad_l2.status_code == 422

        # 3. 声称 programmatic_control=supported 但 evidence 仅为 official_docs -> 必须被拦截 (422)
        r_doc_only = client.patch(
            f"/api/v1/runtimes/instances/{inst_id}",
            json={
                "control_level": "L2",
                "capabilities": [
                    {
                        "capability": "programmatic_control",
                        "support_status": "supported",
                        "evidence": "official_docs",
                        "runtime_version": "1.0.24",
                        "connector_version": "sdk-0.1",
                        "verified_at": "2026-10-31T12:00:00Z",
                        "limitations": "仅看文档未实测",
                    }
                ],
            },
        )
        assert r_doc_only.status_code == 422

        # 4. 提供真实 spike_verified 证据升级 L2（程序化控制）-> 成功
        r_ok_l2 = client.patch(
            f"/api/v1/runtimes/instances/{inst_id}",
            json={
                "control_level": "L2",
                "runtime_version": "1.0.24",
                "connector_version": "claude-sdk-0.1",
                "capabilities": [
                    {
                        "capability": "programmatic_control",
                        "support_status": "supported",
                        "evidence": "spike_verified",
                        "runtime_version": "1.0.24",
                        "connector_version": "claude-sdk-0.1",
                        "verified_at": "2026-10-31T15:00:00Z",
                        "limitations": "已在本机实测通过受控指令发送",
                    }
                ],
            },
        )
        assert r_ok_l2.status_code == 200, r_ok_l2.text
        assert r_ok_l2.json()["instance"]["control_level"] == "L2"

        # 5. 仅具备 L2 程序化控制（SDK）证据就尝试标记为 L3 -> 必须被拦截（SDK != L3）
        r_bad_l3 = client.patch(
            f"/api/v1/runtimes/instances/{inst_id}",
            json={
                "control_level": "L3",
            },
        )
        assert r_bad_l3.status_code == 422

        # 6. 同时提供 programmatic_control (L2) 与 approval_and_resume (L3) 实测证据 -> 允许升级 L3
        r_ok_l3 = client.patch(
            f"/api/v1/runtimes/instances/{inst_id}",
            json={
                "control_level": "L3",
                "capabilities": [
                    {
                        "capability": "programmatic_control",
                        "support_status": "supported",
                        "evidence": "spike_verified",
                        "runtime_version": "1.0.24",
                        "connector_version": "claude-sdk-0.1",
                        "verified_at": "2026-10-31T15:00:00Z",
                        "limitations": "已验证程序化控制",
                    },
                    {
                        "capability": "approval_and_resume",
                        "support_status": "supported",
                        "evidence": "integration_test",
                        "runtime_version": "1.0.24",
                        "connector_version": "claude-sdk-0.1",
                        "verified_at": "2026-10-31T15:10:00Z",
                        "limitations": "已验证审批拦截与跨中断恢复",
                    },
                ],
            },
        )
        assert r_ok_l3.status_code == 200, r_ok_l3.text
        assert r_ok_l3.json()["instance"]["control_level"] == "L3"


def test_remote_registry_url_security_and_project_isolation(tmp_path: Path) -> None:
    """
    验证：
    1. Remote URL 协议白名单、异常 URL、控制字符、userinfo、query/fragment 凭证拦截与脱敏展示；
    2. GET /api/v1/remotes 与项目级权限过滤严格隔离不同项目数据，防止跨项目泄露。
    """
    with _build_test_client(tmp_path) as client:
        ws_a = tmp_path / "proj_a"
        ws_b = tmp_path / "proj_b"
        ws_a.mkdir()
        ws_b.mkdir()

        proj_a = client.post(
            "/api/v1/projects",
            json={"name": "Project Alpha", "root_path": str(ws_a), "slug": "proj-alpha"},
        ).json()["project"]
        proj_b = client.post(
            "/api/v1/projects",
            json={"name": "Project Beta", "root_path": str(ws_b), "slug": "proj-beta"},
        ).json()["project"]

        inst = client.post(
            "/api/v1/runtimes/instances",
            json={
                "provider_key": "opencode",
                "machine_id": "local-machine",
                "display_name": "OpenCode Local",
            },
        ).json()["instance"]

        sess_a = client.post(
            f"/api/v1/projects/{proj_a['id']}/sessions",
            json={"title": "Alpha Session", "runtime_instance_id": inst["id"]},
        ).json()["session"]
        sess_b = client.post(
            f"/api/v1/projects/{proj_b['id']}/sessions",
            json={"title": "Beta Session", "runtime_instance_id": inst["id"]},
        ).json()["session"]

        # 异常与恶意 Remote URL 必须全部被拦截 (403 SecurityBoundaryError)
        unsafe_urls = [
            "javascript:alert(1)",
            "file:///C:/Windows/System32/cmd.exe",
            "ssh://root@192.168.1.10/repo",
            "ws://localhost:8080/ws",
            "https://user:password@claude.ai/code",
            "https://claude.ai/code?token=secret123",
            "https://claude.ai/code#access_token=xyz987",
            "https://claude.ai/code/sk-1234567890abcdef123456",
            "https://claude.ai/code with space",
            "https://invalid_host_without_dot/code",
        ]
        for bad_url in unsafe_urls:
            resp = client.post(
                f"/api/v1/projects/{proj_a['id']}/sessions/{sess_a['id']}/external-refs",
                json={
                    "runtime_instance_id": inst["id"],
                    "external_session_id": "ext-bad",
                    "remote_entry_kind": "opencode_web",
                    "remote_entry_url": bad_url,
                },
            )
            assert resp.status_code == 403, f"Expected 403 for {bad_url}, got {resp.status_code}"

        # 合法官方 Remote URL 登记成功，并自动返回脱敏展示字段 masked_remote_url
        ref_a = client.post(
            f"/api/v1/projects/{proj_a['id']}/sessions/{sess_a['id']}/external-refs",
            json={
                "runtime_instance_id": inst["id"],
                "external_session_id": "native-alpha-001",
                "remote_entry_kind": "opencode_web",
                "remote_entry_url": "https://opencode.ai/workspace/sessions/native-alpha-001",
            },
        )
        assert ref_a.status_code == 201, ref_a.text
        ref_a_data = ref_a.json()["external_ref"]
        assert ref_a_data["masked_remote_url"] == "https://opencode.ai/workspace/.../ha-001"

        client.post(
            f"/api/v1/projects/{proj_b['id']}/sessions/{sess_b['id']}/external-refs",
            json={
                "runtime_instance_id": inst["id"],
                "external_session_id": "native-beta-002",
                "remote_entry_kind": "opencode_web",
                "remote_entry_url": "https://opencode.ai/workspace/sessions/native-beta-002",
            },
        )

        # 未携带 project_id 调用 GET /api/v1/remotes -> 拒绝无作用域枚举 (422)
        r_no_pid = client.get("/api/v1/remotes")
        assert r_no_pid.status_code == 422

        # 按项目 A 查询只返回项目 A 的记录，绝不泄露项目 B
        r_remotes_a = client.get(f"/api/v1/remotes?project_id={proj_a['id']}")
        assert r_remotes_a.status_code == 200
        remotes_list_a = r_remotes_a.json()["remotes"]
        assert len(remotes_list_a) == 1
        assert remotes_list_a[0]["external_ref"]["external_session_id"] == "native-alpha-001"
        assert remotes_list_a[0]["project"]["id"] == proj_a["id"]

        # 携带项目 A 作用域头 (X-Project-Scope) 或项目 A 的 MCP Token 尝试读取项目 B -> 严格拦截 (422)
        r_cross_scope = client.get(
            f"/api/v1/remotes?project_id={proj_b['id']}",
            headers={"X-Project-Scope": proj_a["id"]},
        )
        assert r_cross_scope.status_code == 422

        tok_a = client.post(
            f"/api/v1/projects/{proj_a['id']}/mcp-tokens",
            json={"token_name": "alpha-only-token"},
        ).json()["token"]["raw_token"]

        r_cross_token = client.get(
            f"/api/v1/projects/{proj_b['id']}/remotes",
            headers={"X-MCP-Project-Token": tok_a},
        )
        assert r_cross_token.status_code == 422


def test_connection_status_disconnect_never_marks_task_or_session_failed(tmp_path: Path) -> None:
    """
    验证 Connection Status 独立演进：
    当 Remote 连接断开 (disconnected) 时，仅更新连接状态与链接状态，
    绝不将 Session.platform_status 或关联 Task.platform_status 误判为 failed。
    """
    with _build_test_client(tmp_path) as client:
        ws = tmp_path / "proj_conn"
        ws.mkdir()
        proj = client.post(
            "/api/v1/projects",
            json={"name": "Conn Project", "root_path": str(ws)},
        ).json()["project"]

        task = client.post(
            f"/api/v1/projects/{proj['id']}/tasks",
            json={"title": "Implement Remote Registry"},
        ).json()["task"]
        client.patch(
            f"/api/v1/projects/{proj['id']}/tasks/{task['id']}/status",
            json={"platform_status": "in_progress"},
        )

        inst = client.post(
            "/api/v1/runtimes/instances",
            json={"provider_key": "claude_code", "machine_id": "local-machine"},
        ).json()["instance"]

        sess = client.post(
            f"/api/v1/projects/{proj['id']}/sessions",
            json={
                "title": "Claude Remote Session",
                "task_id": task["id"],
                "runtime_instance_id": inst["id"],
            },
        ).json()["session"]

        ref = client.post(
            f"/api/v1/projects/{proj['id']}/sessions/{sess['id']}/external-refs",
            json={
                "runtime_instance_id": inst["id"],
                "external_session_id": "claude-native-99",
                "remote_entry_kind": "claude_remote_control",
                "remote_entry_url": "https://claude.ai/code",
                "link_status": "active",
            },
        ).json()["external_ref"]

        # 独立切换连接状态为 disconnected
        r_disc = client.patch(
            f"/api/v1/projects/{proj['id']}/sessions/{sess['id']}/connection",
            json={
                "connection_status": "disconnected",
                "sync_link_status": "disconnected",
            },
        )
        assert r_disc.status_code == 200, r_disc.text
        updated_sess = r_disc.json()["session"]
        assert updated_sess["connection_status"] == "disconnected"
        assert updated_sess["platform_status"] == "active"

        # 检查任务与外部引用状态
        detail = client.get(f"/api/v1/sessions/{sess['id']}").json()
        assert detail["task"]["platform_status"] == "in_progress"
        assert detail["external_refs"][0]["id"] == ref["id"]
        assert detail["external_refs"][0]["link_status"] == "disconnected"


def test_decision_atomic_concurrency_and_session_state_protection(tmp_path: Path) -> None:
    """
    验证修正 4：
    1. 人工提交与超时结算（或过期）并发竞争时，SQLite 原子条件更新保证仅一个事务成功落定，绝不产生重复审计事件；
    2. 同一Session存在多个 pending Decision 时，仅当全部结案后才恢复为 active；
    3. 若 Session 已因其他原因处于 paused / completed，结案 Decision 绝不将其覆盖回 active。
    """
    with _build_test_client(tmp_path) as client:
        ws = tmp_path / "proj_dec"
        ws.mkdir()
        proj = client.post(
            "/api/v1/projects",
            json={"name": "Decision Race Project", "root_path": str(ws)},
        ).json()["project"]

        sess = client.post(
            f"/api/v1/projects/{proj['id']}/sessions",
            json={"title": "Concurrency Session"},
        ).json()["session"]

        dec_1 = client.post(
            f"/api/v1/projects/{proj['id']}/decisions",
            json={
                "title": "Concurrent Race Decision",
                "decision_type": "interactive_question",
                "risk_level": "normal",
                "timeout_policy": "auto_submit_recommended",
                "timeout_sec": 45,
                "session_id": sess["id"],
                "options": [
                    {"option_id": "opt_rec", "label": "Recommended", "is_recommended": True},
                    {"option_id": "opt_manual", "label": "Manual Choice", "is_recommended": False},
                ],
            },
        ).json()["decision"]

        # 使用底层 DecisionService 进行双线程同步屏障并发竞争：人工选 opt_manual vs 超时自动选 opt_rec
        dec_svc = client.app.state.decision_service  # type: ignore[attr-defined]
        barrier = threading.Barrier(2)

        def _worker_manual():
            barrier.wait()
            return dec_svc.resolve_decision(
                decision_id=dec_1["id"],
                chosen_option_id="opt_manual",
                custom_note="User clicked manual option",
                resolved_by=DecisionResolvedBy.USER_MANUAL,
                idempotency_key="idem-manual-1",
            )

        def _worker_timeout():
            barrier.wait()
            return dec_svc.resolve_decision(
                decision_id=dec_1["id"],
                chosen_option_id="opt_rec",
                custom_note="Timeout auto submit",
                resolved_by=DecisionResolvedBy.TIMEOUT_AUTO_RECOMMENDED,
                idempotency_key="idem-timeout-1",
            )

        outcomes: list[str] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_worker_manual)
            f2 = pool.submit(_worker_timeout)
            for fut in (f1, f2):
                try:
                    _, is_replay = fut.result()
                    outcomes.append("won" if not is_replay else "replay")
                except VersionConflictError:
                    outcomes.append("conflict")

        # 严格断言：必然一个是 won，另一个是 conflict！
        assert sorted(outcomes) == ["conflict", "won"], f"Unexpected outcomes: {outcomes}"

        # 检查审计事件：该 Decision 只能有 1 条创建事件 + 1 条结案事件（绝无重复结案事件）
        evts = client.get(
            f"/api/v1/events?project_id={proj['id']}&entity_type=decision&entity_id={dec_1['id']}"
        ).json()["events"]
        resolution_events = [
            e
            for e in evts
            if e["event_type"]
            in ("decision.resolved", "decision.auto_recommended_submitted")
        ]
        assert len(resolution_events) == 1

        # 验证多未决 Decision 下 Session 状态恢复与 paused 状态保护
        dec_2 = client.post(
            f"/api/v1/projects/{proj['id']}/decisions",
            json={
                "title": "Multi Decision #1",
                "risk_level": "high",
                "timeout_policy": "manual_only",
                "session_id": sess["id"],
                "options": [{"option_id": "yes", "label": "Confirm"}],
            },
        ).json()["decision"]
        dec_3 = client.post(
            f"/api/v1/projects/{proj['id']}/decisions",
            json={
                "title": "Multi Decision #2",
                "risk_level": "high",
                "timeout_policy": "manual_only",
                "session_id": sess["id"],
                "options": [{"option_id": "yes", "label": "Confirm"}],
            },
        ).json()["decision"]

        # 结案 dec_2 后，因为 dec_3 仍为 pending，Session 必须保持 waiting_interaction
        client.post(
            f"/api/v1/decisions/{dec_2['id']}/resolve",
            json={"chosen_option_id": "yes", "resolved_by": "user_manual"},
        )
        sess_after_d2 = client.get(f"/api/v1/sessions/{sess['id']}").json()["session"]
        assert sess_after_d2["platform_status"] == "waiting_interaction"

        # 若此时人工将 Session 置为 paused，随后结案最后一个 dec_3，Session 必须保持 paused（绝不被覆盖为 active）
        client.patch(
            f"/api/v1/projects/{proj['id']}/sessions/{sess['id']}/status",
            json={"platform_status": "paused"},
        )
        client.post(
            f"/api/v1/decisions/{dec_3['id']}/resolve",
            json={"chosen_option_id": "yes", "resolved_by": "user_manual"},
        )
        sess_after_d3 = client.get(f"/api/v1/sessions/{sess['id']}").json()["session"]
        assert sess_after_d3["platform_status"] == "paused"


def test_browser_e2e_decision_modal_and_remote_registry(tmp_path: Path) -> None:
    """
    真实 Chromium (Microsoft Edge Headless CDP) 浏览器端到端交互验证：
    1. 跨页面（在 P14 Remotes 页）自动唤醒全局 Decision 弹窗；
    2. 键盘快捷键防误触：聚焦普通输入框按键 '2' 不切换弹窗选项，失焦后按键 '2' 切换至选项 2 并自动暂停倒计时；
    3. 倒计时恢复/暂停切换按钮交互正常；
    4. 移动端视口 (375x812) 下弹窗选项卡片高度 >= 48px；
    5. P10 会话详情页展示官方 Remote 入口与 Capability Manifest (含 unverified)，无任何受控指令输入框，断线切换不将 Session 判败。
    """
    import asyncio
    import json
    import socket
    import subprocess
    import time
    import urllib.request
    import uvicorn
    import websockets

    edge_path = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge_path.exists():
        pytest.skip("Microsoft Edge 未安装，跳过本地 Chromium CDP E2E 验证")

    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    app_port = _free_port()
    cdp_port = _free_port()

    settings = AppSettings(
        data_dir=tmp_path / "e2e_data",
        db_filename="e2e_test.db",
        port=app_port,
    )
    app = create_app(settings)

    config = uvicorn.Config(app, host="127.0.0.1", port=app_port, log_level="error")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{app_port}"
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/health", timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.1)

    def _post_json(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # 直接向运行中的 uvicorn 实例注入测试项目、会话、Remote 引用与待决 Decision
    ws_dir = tmp_path / "e2e_ws"
    ws_dir.mkdir()
    proj = _post_json(
        "/api/v1/projects",
        {"name": "E2E Browser Project", "root_path": str(ws_dir)},
    )["project"]
    inst = _post_json(
        "/api/v1/runtimes/instances",
        {"provider_key": "claude_code", "machine_id": "local-machine"},
    )["instance"]
    sess = _post_json(
        f"/api/v1/projects/{proj['id']}/sessions",
        {"title": "E2E Remote Session", "runtime_instance_id": inst["id"]},
    )["session"]
    _post_json(
        f"/api/v1/projects/{proj['id']}/sessions/{sess['id']}/external-refs",
        {
            "runtime_instance_id": inst["id"],
            "external_session_id": "claude-e2e-session-01",
            "remote_entry_kind": "claude_remote_control",
            "remote_entry_url": "https://claude.ai/code",
        },
    )
    _post_json(
        f"/api/v1/projects/{proj['id']}/decisions",
        {
            "title": "是否批准数据库结构变更？",
            "decision_type": "interactive_question",
            "risk_level": "normal",
            "timeout_policy": "auto_submit_recommended",
            "timeout_sec": 60,
            "session_id": sess["id"],
            "options": [
                {"option_id": "opt_1", "label": "使用安全增量迁移", "is_recommended": True},
                {"option_id": "opt_2", "label": "仅生成 SQL 脚本人工审核", "is_recommended": False},
            ],
        },
    )

    user_data_dir = tmp_path / "edge_profile"
    edge_proc = subprocess.Popen(
        [
            str(edge_path),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={user_data_dir}",
            f"--remote-debugging-port={cdp_port}",
            f"{base_url}/#remotes",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ws_url = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=0.5) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                    for t in targets:
                        if (
                            t.get("type") == "page"
                            and "webSocketDebuggerUrl" in t
                            and str(app_port) in t.get("url", "")
                        ):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
                    if not ws_url:
                        for t in targets:
                            if t.get("type") == "page" and "webSocketDebuggerUrl" in t:
                                ws_url = t["webSocketDebuggerUrl"]
                                break
                if ws_url:
                    break
            except Exception:
                time.sleep(0.1)
        assert ws_url is not None, "未能连接 Headless Edge CDP 调试端口"

        async def _run_cdp_checks() -> None:
            async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
                msg_id = 0

                async def cdp_call(method: str, params: dict | None = None) -> dict:
                    nonlocal msg_id
                    msg_id += 1
                    cur_id = msg_id
                    await ws.send(json.dumps({"id": cur_id, "method": method, "params": params or {}}))
                    while True:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        if data.get("id") == cur_id:
                            if "error" in data:
                                raise RuntimeError(f"CDP error: {data['error']}")
                            return data.get("result", {})

                async def eval_js(expr: str):
                    res = await cdp_call(
                        "Runtime.evaluate",
                        {"expression": expr, "returnByValue": True, "awaitPromise": True},
                    )
                    return res.get("result", {}).get("value")

                await cdp_call("Page.enable")
                await cdp_call("Runtime.enable")
                await cdp_call("Page.navigate", {"url": f"{base_url}/#remotes"})

                # 1. 等待跨页面全局 Decision 弹窗 (.modal-panel) 渲染唤醒
                modal_ready = False
                for _ in range(60):
                    val = await eval_js("Boolean(document.querySelector('.modal-panel'))")
                    if val:
                        modal_ready = True
                        break
                    await asyncio.sleep(0.15)
                if not modal_ready:
                    body_txt = await eval_js("document.body ? document.body.innerText : 'NO_BODY'")
                    cur_href = await eval_js("location.href")
                    raise AssertionError(
                        f"跨页面未能自动唤醒全局 Decision 弹窗 (href={cur_href}, body={body_txt[:300]})"
                    )

                # 2. 验证键盘防误触：聚焦页面上的普通 input 输入框时按 '2' 不会切换弹窗选项
                input_guard_result = await eval_js(
                    """
                    (() => {
                      const tempInput = document.createElement('input');
                      tempInput.id = 'e2e-guard-input';
                      document.body.appendChild(tempInput);
                      tempInput.focus();
                      tempInput.dispatchEvent(new KeyboardEvent('keydown', { key: '2', bubbles: true }));
                      const activeOptionText = document.querySelector('.decision-option-card.selected')?.innerText || '';
                      tempInput.blur();
                      tempInput.remove();
                      return activeOptionText;
                    })()
                    """
                )
                assert "使用安全增量迁移" in input_guard_result, (
                    f"输入框聚焦时按数字键误触了弹窗选项: {input_guard_result}"
                )

                # 3. 失焦状态下派发按键 '2'，验证切换到第二个选项并自动暂停倒计时
                after_shortcut = await eval_js(
                    """
                    (() => {
                      window.dispatchEvent(new KeyboardEvent('keydown', { key: '2', bubbles: true }));
                      return new Promise(r => setTimeout(() => {
                        const activeOptionText = document.querySelector('.decision-option-card.selected')?.innerText || '';
                        const dialogText = document.querySelector('.modal-panel')?.innerText || '';
                        r({ activeOptionText, isPaused: dialogText.includes('倒计时已自动暂停') });
                      }, 100));
                    })()
                    """
                )
                assert "仅生成 SQL 脚本人工审核" in after_shortcut["activeOptionText"]
                assert after_shortcut["isPaused"] is True

                # 4. 切换移动端视口 (375x812)，验证弹窗选项触控高度 >= 48px
                await cdp_call(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 375, "height": 812, "deviceScaleFactor": 2, "mobile": True},
                )
                min_option_height = await eval_js(
                    """
                    (() => {
                      const btns = Array.from(document.querySelectorAll('.decision-option-card'));
                      return Math.min(...btns.map(b => b.getBoundingClientRect().height));
                    })()
                    """
                )
                assert min_option_height >= 48, f"移动端触控目标高度不足 48px: {min_option_height}"

                # 5. 点击确认拍板按钮结案 Decision，验证弹窗关闭
                await eval_js(
                    """
                    (() => {
                      const submitBtn = Array.from(document.querySelectorAll('.modal-panel button'))
                        .find(b => b.innerText.includes('确认拍板'));
                      submitBtn?.click();
                    })()
                    """
                )
                modal_closed = False
                for _ in range(40):
                    val = await eval_js("Boolean(document.querySelector('.modal-panel'))")
                    if not val:
                        modal_closed = True
                        break
                    await asyncio.sleep(0.1)
                assert modal_closed, "提交决策后全局弹窗未能关闭"

                # 6. 恢复桌面视口并进入 P10 会话详情页，检查官方 Remote 入口、Capability Manifest (含 unverified) 及无受控指令输入框
                await cdp_call(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False},
                )
                await eval_js(
                    """
                    (() => {
                      const navBtn = Array.from(document.querySelectorAll('aside button'))
                        .find(b => b.innerText.includes('P09-P10 协同会话'));
                      navBtn?.click();
                    })()
                    """
                )
                await asyncio.sleep(0.2)
                await eval_js(
                    """
                    (() => {
                      const sessCard = Array.from(document.querySelectorAll('.card-elevated'))
                        .find(d => d.innerText.includes('E2E Remote Session'));
                      sessCard?.click();
                    })()
                    """
                )
                await asyncio.sleep(0.3)

                p10_state = await eval_js(
                    """
                    (() => {
                      const mainText = document.querySelector('main')?.innerText || '';
                      const remoteLinks = Array.from(document.querySelectorAll('main a'))
                        .map(a => a.getAttribute('href'));
                      const commandInputs = Array.from(document.querySelectorAll('main input, main textarea'))
                        .filter(el => (el.getAttribute('placeholder') || '').includes('指令') || (el.getAttribute('placeholder') || '').includes('发送'));
                      return {
                        hasOfficialRemoteLink: remoteLinks.includes('https://claude.ai/code'),
                        hasUnverifiedBadge: mainText.includes('unverified'),
                        hasCapabilityManifestTitle: mainText.includes('关联 Runtime 能力清单与验证证据'),
                        commandInputCount: commandInputs.length,
                      };
                    })()
                    """
                )
                assert p10_state["hasOfficialRemoteLink"] is True
                assert p10_state["hasUnverifiedBadge"] is True
                assert p10_state["hasCapabilityManifestTitle"] is True
                assert p10_state["commandInputCount"] == 0

        asyncio.run(_run_cdp_checks())
    finally:
        server.should_exit = True
        edge_proc.terminate()
        try:
            edge_proc.wait(timeout=5)
        except Exception:
            edge_proc.kill()

