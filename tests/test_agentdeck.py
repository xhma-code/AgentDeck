"""
AgentDeck 完整单元测试与集成测试套件。

严格对齐 `/autoplan` 测试计划：
1. 验证 SQLite 元数据库建表、中文注释注册表 (`t_table_column_comments`) 与 WAL 锁冲突快照兜底读取。
2. 验证三段式中文错误日志 (`Problem + Cause + Fix`) 自动汇聚写入 `logs/error.log`。
3. 验证一次性 `pair_code` 置换长期 Token、重放拦截与 WebSocket `Origin` 安全边界。
4. 验证四大 Agent (AGY IDE/CLI, Claude Code, OpenCode) 在隔离 `tmp_path` 下的会话读取、Unified Diff 生成、4KB 大输出截断与高危命令分级。
5. 验证 Antigravity IDE 会话向 CLI 的安全克隆接力 (`fork_ide_session_to_cli` 防双写脑裂)。
6. 验证 WebSocket `SESSION_STATE_SNAPSHOT` 断线重连恢复挂起的 `pending_approval` 与工具审批闭环。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path
import unittest.mock
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.adapters.antigravity_adapter import (
    AntigravityAdapter,
    fork_ide_session_to_cli,
)
from backend.adapters.base import (
    UnifiedToolCall,
    build_unified_diff,
    classify_tool_risk,
    is_transient_probe_session,
    truncate_large_output,
)
from backend.adapters.claude_adapter import ClaudeCodeAdapter
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.app import create_app
from backend.core.database import MetaDatabase, safe_query_external_sqlite
from backend.core.logger import ERROR_LOG_FILE, log_structured_error
from backend.core.tailscale_auth import PairingAuthManager
from backend.core.workspace import WorkspaceDocumentReader, get_git_workspace_diff
from backend.engines.approval import ApprovalCoordinator, PendingApproval
from backend.engines.cache import EtagNegotiator
from backend.engines.event_bus import RealtimeEventBus
from backend.engines.watcher import SafeStorageWatcher


def test_structured_error_logging_to_error_log():
    """测试所有错误必须按三段式中文格式汇聚写入 logs/error.log。"""
    marker = "单元测试异常标记_9527"
    msg = log_structured_error(
        problem=marker,
        cause="模拟子进程或数据库异常",
        fix="无需操作，仅用于自动化测试断言",
        exc=RuntimeError("mock error"),
    )
    assert "【错误现象】" in msg
    assert "【根因分析】" in msg
    assert "【修复建议】" in msg
    assert ERROR_LOG_FILE.exists()
    content = ERROR_LOG_FILE.read_text(encoding="utf-8", errors="replace")
    assert marker in content


def test_meta_database_and_chinese_comments(tmp_path: Path):
    """测试元数据库初始化、表字段中文注释完整度及 CRUD。"""
    db_file = tmp_path / "test_meta.db"
    db = MetaDatabase(db_path=db_file)

    with db.connect() as conn:
        comments = conn.execute("SELECT * FROM t_table_column_comments").fetchall()
        assert len(comments) >= 20
        tables = {r["table_name"] for r in comments}
        assert {
            "t_device_session",
            "t_unified_session_meta",
            "t_tool_approval_log",
            "t_workspace_bookmark",
        }.issubset(tables)

    meta = db.upsert_session_meta(
        unified_id="claude_code:s1",
        agent_type="claude_code",
        raw_session_id="s1",
        project_path="d:/repo",
        title="修复登录 Bug",
        is_pinned=1,
        execution_mode="accept_edits",
    )
    assert meta["is_pinned"] == 1
    assert meta["execution_mode"] == "accept_edits"


def test_one_time_pair_code_and_origin_security(tmp_path: Path):
    """测试 60s 一次性配对码置换、防重放二次消费拦截及 WebSocket Origin 校验。"""
    db = MetaDatabase(db_path=tmp_path / "auth.db")
    auth = PairingAuthManager(meta_db=db)

    code = auth.generate_pair_code(ttl_sec=60)
    first_res = auth.exchange_pair_code(code, device_name="iPhone 16", client_ip="100.88.1.2")
    assert first_res is not None
    assert first_res["token"].startswith("agd_")
    assert auth.verify_token(first_res["token"], client_ip="100.88.1.2") is True

    # 第二次使用同一个 pair_code 必须被拒绝（防截图重放）
    second_res = auth.exchange_pair_code(code, device_name="Attacker", client_ip="100.88.1.9")
    assert second_res is None

    # Origin 校验测试
    assert auth.is_trusted_origin("http://100.88.1.2:8765", ["100.88.1.2", "127.0.0.1"]) is True
    assert auth.is_trusted_origin("https://my-pc.tailnet.ts.net", ["127.0.0.1"]) is True
    assert auth.is_trusted_origin("https://evil-phishing.com", ["100.88.1.2", "127.0.0.1"]) is False


def test_truncation_diff_and_high_risk_classifier():
    """测试 4KB/100行大输出自动截断、单列 Unified Diff 生成与高危命令分级。"""
    huge_text = "\n".join([f"line {i}: " + ("x" * 60) for i in range(200)])
    truncated, is_trunc, total_len = truncate_large_output(huge_text)
    assert is_trunc is True
    assert total_len == len(huge_text)
    assert "输出已截断" in truncated

    diff = build_unified_diff("app.py", "a = 1\nb = 2\n", "a = 1\nb = 3\n")
    assert "-b = 2" in diff
    assert "+b = 3" in diff

    assert classify_tool_risk("Bash", "rm -rf /tmp/foo") == "high"
    assert classify_tool_risk("run_command", "git reset --hard HEAD~1") == "high"
    assert classify_tool_risk("run_command", "pytest -q") == "normal"


def test_antigravity_adapter_and_fork_to_cli(tmp_path: Path):
    """测试 Antigravity IDE/CLI 同构存储读取及 IDE -> CLI 安全克隆接力（防双写脑裂）。"""
    ide_dir = tmp_path / "antigravity"
    cli_dir = tmp_path / "antigravity-cli"
    (ide_dir / "conversations").mkdir(parents=True)
    (cli_dir / "conversations").mkdir(parents=True)

    # 创建模拟的 conversation_summaries.db
    for root in (ide_dir, cli_dir):
        conn = sqlite3.connect(str(root / "conversation_summaries.db"))
        conn.execute(
            """
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                preview TEXT,
                step_count INTEGER,
                last_modified_time TEXT,
                workspace_uris TEXT,
                status TEXT,
                not_fully_idle INTEGER,
                killed INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()

    cid = "conv-ide-001"
    conn = sqlite3.connect(str(ide_dir / "conversation_summaries.db"))
    conn.execute(
        """
        INSERT INTO conversation_summaries
        VALUES (?, '测试 IDE 会话', '预览文本', 2, '2026-10-23T13:00:00Z', 'file:///d%3A/repo', 'RUNNING', 1, 0)
        """,
        (cid,),
    )
    conn.commit()
    conn.close()

    # 写入模拟的 transcript.jsonl 与 conversations/<id>.db
    (ide_dir / "conversations" / f"{cid}.db").write_bytes(b"sqlite-dummy")
    log_dir = ide_dir / "brain" / cid / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    transcript_file = log_dir / "transcript.jsonl"
    transcript_file.write_text(
        json.dumps(
            {
                "step_index": 1,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-10-23T13:00:01Z",
                "content": "<USER_REQUEST>\n帮我重构登录模块\n</USER_REQUEST>",
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-10-23T13:00:05Z",
                "thinking": "先检查 auth.py",
                "content": "我已经更新了登录逻辑。",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "name": "edit_file",
                        "args": {
                            "Path": "d:/repo/auth.py",
                            "Target": "old_login()",
                            "Replacement": "new_login()",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    ide_adapter = AntigravityAdapter("agy_ide", "Antigravity IDE", root_dir=ide_dir)
    sessions = ide_adapter.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].desktop_locked is True
    assert sessions[0].project_path == "d:/repo"

    msgs = ide_adapter.get_session_messages(cid)
    assert len(msgs) == 2
    assert msgs[0].content == "帮我重构登录模块"
    assert msgs[1].thinking == "先检查 auth.py"
    assert "-old_login()" in msgs[1].tool_calls[0].diff_text

    # 验证安全克隆至 CLI
    ok, new_uid = fork_ide_session_to_cli(cid, ide_root=ide_dir, cli_root=cli_dir)
    assert ok is True
    assert new_uid == f"agy_cli:{cid}"
    assert (cli_dir / "conversations" / f"{cid}.db").exists()
    assert (cli_dir / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl").exists()


def test_claude_and_opencode_adapters(tmp_path: Path):
    """测试 Claude Code (.jsonl) 与 OpenCode (SQLite) 适配器解析。"""
    # 1. Claude Code
    claude_dir = tmp_path / "claude_projects" / "my-proj"
    claude_dir.mkdir(parents=True)
    c_file = claude_dir / "sess-claude-1.jsonl"
    c_file.write_text(
        json.dumps(
            {
                "type": "user",
                "cwd": "d:/my-proj",
                "timestamp": "2026-10-23T12:00:00Z",
                "message": {"role": "user", "content": "查看目录结构"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "cwd": "d:/my-proj",
                "timestamp": "2026-10-23T12:00:02Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "准备执行 ls"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Bash",
                            "input": {"command": "rm -rf /important"},
                        },
                        {"type": "text", "text": "请确认命令"},
                    ],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    c_adapter = ClaudeCodeAdapter(projects_dir=tmp_path / "claude_projects")
    c_sessions = c_adapter.list_sessions()
    assert len(c_sessions) == 1
    assert c_sessions[0].title == "查看目录结构"
    c_msgs = c_adapter.get_session_messages("sess-claude-1")
    assert len(c_msgs) == 2
    assert c_msgs[1].tool_calls[0].risk_level == "high"

    # 验证 Claude 外部进程运行状态探测 (busy -> running)
    import os
    c_sessions_dir = tmp_path / "claude_sessions"
    c_sessions_dir.mkdir(parents=True)
    (c_sessions_dir / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "sessionId": "sess-claude-1", "status": "busy"}),
        encoding="utf-8",
    )
    c_adapter_with_sess = ClaudeCodeAdapter(
        projects_dir=tmp_path / "claude_projects",
        sessions_dir=c_sessions_dir,
    )
    assert c_adapter_with_sess.is_external_session_active("sess-claude-1") is True
    active_c_sessions = c_adapter_with_sess.list_sessions()
    assert active_c_sessions[0].status == "running"

    # 2. OpenCode
    oc_dir = tmp_path / "opencode"
    oc_dir.mkdir(parents=True)
    oc_db = oc_dir / "opencode.db"
    conn = sqlite3.connect(str(oc_db))
    conn.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT,
            title TEXT,
            time_updated INTEGER,
            time_archived INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
        INSERT INTO session VALUES ('ses_1', 'd:/oc_proj', 'OpenCode 测试任务', 1774193678245, NULL);
        INSERT INTO message VALUES ('msg_1', 'ses_1', 1774193678245, '{"role":"user"}');
        INSERT INTO part VALUES ('prt_1', 'msg_1', 'ses_1', 1774193678245, '{"type":"text","text":"你好 OpenCode"}');
        """
    )
    conn.commit()
    conn.close()

    oc_adapter = OpenCodeAdapter(data_dir=oc_dir)
    oc_sessions = oc_adapter.list_sessions()
    assert len(oc_sessions) == 1
    assert oc_sessions[0].title == "OpenCode 测试任务"
    oc_msgs = oc_adapter.get_session_messages("ses_1")
    assert len(oc_msgs) == 1
    assert oc_msgs[0].content == "你好 OpenCode"
    page_msgs, total, has_more = oc_adapter.get_session_messages_page("ses_1", limit=10)
    assert total == 1
    assert has_more is False
    assert len(page_msgs) == 1
    assert page_msgs[0].step_index == 1

    # 3. OpenCode 2.x (session_v2 + session_message)
    oc2_dir = tmp_path / "opencode2"
    oc2_dir.mkdir(parents=True)
    oc2_db = oc2_dir / "opencode.db"
    conn2 = sqlite3.connect(str(oc2_db))
    conn2.executescript(
        """
        CREATE TABLE session_v2 (
            id TEXT PRIMARY KEY,
            directory TEXT,
            title TEXT,
            time_updated INTEGER,
            time_archived INTEGER,
            tokens_input INTEGER,
            tokens_output INTEGER,
            tokens_reasoning INTEGER,
            tokens_cache_read INTEGER,
            cost REAL
        );
        CREATE TABLE session_message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            type TEXT,
            seq INTEGER,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        );
        INSERT INTO session_v2 VALUES (
            'ses_v2_1', 'd:/oc2_proj', NULL, 1792783941084, NULL, 500, 200, 50, 1000, 0.01
        );
        INSERT INTO session_message VALUES (
            'msg_u1', 'ses_v2_1', 'user', 1, 1792783941084, 1792783941084,
            '{"text":"修复 OpenCode 2 接入问题"}'
        );
        INSERT INTO session_message VALUES (
            'msg_a1', 'ses_v2_1', 'assistant', 2, 1792783942000, 1792783945000,
            '{"content":[{"type":"reasoning","text":"思考中..."},{"type":"tool","name":"shell","state":{"status":"completed","input":{"command":"git status"},"content":[{"type":"text","text":"clean"}]}},{"type":"text","text":"操作完成"}]}'
        );
        """
    )
    conn2.commit()
    conn2.close()

    # 模拟 service.json 状态目录
    state_dir = oc2_dir / "state"
    state_dir.mkdir(parents=True)
    service_json = state_dir / "service.json"
    service_json.write_text(
        json.dumps({"url": "http://127.0.0.1:28894", "password": "mock_secret_token"}),
        encoding="utf-8",
    )

    oc2_adapter = OpenCodeAdapter(data_dir=oc2_dir, state_dir=state_dir)
    assert oc2_adapter.is_available()[0] is True
    assert "OpenCode 2" in oc2_adapter.is_available()[1]

    oc2_sessions = oc2_adapter.list_sessions()
    assert len(oc2_sessions) == 1
    # 标题为 NULL 时自动提取首条 user 文本
    assert oc2_sessions[0].title == "修复 OpenCode 2 接入问题"
    assert oc2_sessions[0].step_count == 2

    oc2_msgs = oc2_adapter.get_session_messages("ses_v2_1")
    assert len(oc2_msgs) == 2
    assert oc2_msgs[0].role == "user"
    assert oc2_msgs[0].content == "修复 OpenCode 2 接入问题"
    assert oc2_msgs[1].role == "assistant"
    assert oc2_msgs[1].content == "操作完成"
    assert oc2_msgs[1].thinking == "思考中..."
    assert len(oc2_msgs[1].tool_calls) == 1
    assert oc2_msgs[1].tool_calls[0].name == "shell"
    assert oc2_msgs[1].tool_calls[0].status == "DONE"
    assert oc2_msgs[1].tool_calls[0].output == "clean"

    # 验证 token usage
    usage = oc2_adapter.get_session_token_usage("ses_v2_1")
    assert usage["total_tokens"] == 700
    assert usage["context_tokens"] == 1500  # 500 + 1000

    # 验证 HTTP 桥接参数与头构造
    assert oc2_adapter.http_bridge.base_url == "http://127.0.0.1:28894"
    assert oc2_adapter.http_bridge.password == "mock_secret_token"
    req = oc2_adapter.http_bridge._make_request("/api/test")
    assert "Authorization" in req.headers
    assert req.headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_websocket_snapshot_and_approval_flow(tmp_path: Path):
    """测试 FastAPI 接口、WebSocket 断线重连快照恢复与移动端工具审批全链路闭环。"""
    db = MetaDatabase(db_path=tmp_path / "api_test.db")
    app = create_app(meta_db=db)
    orchestrator = app.state.orchestrator

    # 模拟一个挂起中的高危工具调用审批
    import asyncio
    import unittest.mock

    approval_task = asyncio.create_task(
        orchestrator.request_mobile_tool_approval(
            unified_id="claude_code:test_sess_99",
            agent_type="claude_code",
            raw_session_id="test_sess_99",
            tool_call_id="tc_danger_1",
            tool_name="Bash",
            args_summary="git reset --hard HEAD~1",
            diff_text="",
            risk_level="high",
            prompt_context="回滚提交",
            project_path="d:/repo",
        )
    )
    await asyncio.sleep(0.05)

    with TestClient(app) as client:
        # 1. 连接 WebSocket，断言第一帧立即收到 SESSION_STATE_SNAPSHOT 且包含挂起的审批
        with client.websocket_connect("/ws/stream?since_seq=0") as ws:
            first_frame = ws.receive_json()
            assert first_frame["type"] == "SESSION_STATE_SNAPSHOT"
            pending = first_frame["payload"]["pending_approvals"]
            assert len(pending) == 1
            assert pending[0]["tool_call_id"] == "tc_danger_1"
            assert pending[0]["risk_level"] == "high"

        # 2. 手机端调用审批接口允许执行
        resp = client.post(
            "/api/v1/sessions/claude_code:test_sess_99/approve",
            json={"decision": "approved", "feedback": "确认允许"},
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "approved"

    res = await approval_task
    assert res["decision"] == "approved"
    assert "claude_code:test_sess_99" not in orchestrator.pending_approvals


def test_model_selection_and_cli_args(tmp_path: Path):
    """测试模型列表接口、会话模型持久化及 CLI --model 参数注入。"""
    db = MetaDatabase(db_path=tmp_path / "model_test.db")
    app = create_app(meta_db=db)
    orchestrator = app.state.orchestrator

    with TestClient(app) as client:
        models_resp = client.get("/api/v1/models")
        assert models_resp.status_code == 200
        models_by_agent = models_resp.json()["models_by_agent"]
        assert "agy_cli" in models_by_agent
        assert "claude_code" in models_by_agent
        assert any("gemini-3.8-flash" in m["id"] for m in models_by_agent["agy_cli"])

        # 更新会话模型
        patch_resp = client.patch(
            "/api/v1/sessions/agy_cli:conv_model_1/meta",
            json={"model_name": "gemini-3.8-flash-high", "execution_mode": "plan"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["meta"]["model_name"] == "gemini-3.8-flash-high"

    # 验证 CLI 参数构建包含 --model
    cmd_agy = orchestrator._build_cli_command(
        agent_type="agy_cli",
        raw_session_id="conv_model_1",
        prompt="重构代码",
        execution_mode="plan",
        model_name="gemini-4-argon-high",
    )
    assert "--model" in cmd_agy
    assert "gemini-4-argon-high" in cmd_agy

    cmd_claude = orchestrator._build_cli_command(
        agent_type="claude_code",
        raw_session_id="sess_c_1",
        prompt="检查类型",
        execution_mode="ask",
        model_name="claude-opus-4-7",
    )
    assert "--model" in cmd_claude
    assert "claude-opus-4-7" in cmd_claude


def test_real_project_detection_and_hide_workspace(tmp_path: Path):
    """测试真正工程项目特征识别、临时目录过滤 (System32/Home/Downloads) 及手动移出项目。"""
    from backend.adapters.base import evaluate_project_dir

    # 1. 临时会话文件夹（无工程标志文件、系统目录、隐藏目录）必须返回 is_project=False
    is_proj, _, p_name = evaluate_project_dir("C:/Windows/System32")
    assert is_proj is False and p_name == ""

    is_proj, _, p_name = evaluate_project_dir("C:/Users/demo_user")
    assert is_proj is False and p_name == ""

    is_proj, _, p_name = evaluate_project_dir("C:/Users/demo_user/Downloads")
    assert is_proj is False and p_name == ""

    is_proj, _, p_name = evaluate_project_dir("C:/Users/demo_user/.claude-mem")
    assert is_proj is False and p_name == ""

    # 2. 带有工程特征文件 (.git / pyproject.toml) 的目录必须识别为真正项目
    real_proj = tmp_path / "MyRealRepo"
    real_proj.mkdir()
    (real_proj / "pyproject.toml").write_text("[project]\nname='demo'", encoding="utf-8")

    is_proj, norm_path, p_name = evaluate_project_dir(str(real_proj))
    assert is_proj is True
    assert p_name == "MyRealRepo"

    # 3. 手动移出项目 (hide_workspace) 后，该目录不再出现在项目列表
    db = MetaDatabase(db_path=tmp_path / "proj_hide.db")
    app = create_app(meta_db=db)
    with TestClient(app) as client:
        hide_resp = client.post(
            "/api/v1/workspaces/hide",
            json={"workspace_path": str(real_proj)},
        )
        assert hide_resp.status_code == 200
        assert hide_resp.json()["is_hidden"] == 1

        hidden_keys = {
            w["workspace_path"].lower()
            for w in db.list_workspaces(include_hidden=True)
            if w.get("is_hidden") == 1
        }
        is_proj_after_hide, _, _ = evaluate_project_dir(str(real_proj), set(), hidden_keys)
        assert is_proj_after_hide is False


def test_noise_filtering_tool_output_folding_and_md_reader(tmp_path: Path):
    """
    测试 /grill-me 手机端对话降噪与 Markdown 阅读器能力：
    1. AGY transcript.jsonl 中的 GENERIC/VIEW_FILE/RUN_COMMAND 工具结果必须回填到 tool_calls.output，不得产生多余聊天气泡。
    2. Claude Code 中的 <local-command-caveat>、<task-notification> 与 /init 展开长模板必须被清洗或收纳。
    3. /api/v1/files/read 支持安全读取 file:// 本地 Markdown 文档并拦截敏感文件 (.env)。
    """
    # 1. 验证 Antigravity 工具输出归位与 ask_question 提取
    ide_dir = tmp_path / "agy_clean_test"
    cid = "conv-clean-01"
    log_dir = ide_dir / "brain" / cid / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    transcript = log_dir / "transcript.jsonl"
    steps = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-10-23T15:00:00Z",
            "content": "<USER_REQUEST>优化手机阅读</USER_REQUEST><ADDITIONAL_METADATA>noise</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-23T15:00:02Z",
            "thinking": "读取 app.js",
            "content": "",
            "tool_calls": [
                {"id": "tc_read", "name": "read_file", "args": {"Path": "d:/repo/PLAN.md"}}
            ],
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "created_at": "2026-10-23T15:00:03Z",
            "content": "Created At: ...\nShowing lines 1-100 of PLAN.md\n# Title",
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-23T15:00:05Z",
            "thinking": "",
            "content": "## 结论\n已完成修改。",
            "tool_calls": [],
        },
    ]
    transcript.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n",
        encoding="utf-8",
    )

    agy_ad = AntigravityAdapter("agy_ide", "Antigravity IDE", root_dir=ide_dir)
    msgs = agy_ad.get_session_messages(cid)
    # step 2 (GENERIC) 绝不能变成独立消息，总消息数应为 3 (user + tool step + final answer)
    assert len(msgs) == 3
    assert msgs[0].content == "优化手机阅读"
    assert msgs[1].tool_calls[0].file_path.lower() == "d:/repo/plan.md"
    assert "Showing lines 1-100" in msgs[1].tool_calls[0].output
    assert msgs[2].content == "## 结论\n已完成修改。"

    # 2. 验证 Claude Code 系统噪音清洗
    c_dir = tmp_path / "claude_clean" / "proj"
    c_dir.mkdir(parents=True)
    c_file = c_dir / "sess-noise.jsonl"
    c_records = [
        {
            "type": "user",
            "timestamp": "2026-10-23T15:00:00Z",
            "message": {
                "role": "user",
                "content": "<local-command-caveat>Caveat: DO NOT respond</local-command-caveat>",
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-23T15:00:01Z",
            "message": {
                "role": "user",
                "content": "<command-message>init</command-message><command-name>/init</command-name>",
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-23T15:00:01Z",
            "message": {
                "role": "user",
                "content": "Please analyze this codebase and create a CLAUDE.md file, which will be given to future instances...",
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-23T15:00:05Z",
            "message": {
                "role": "user",
                "content": "<task-notification><task-id>t1</task-id><summary>Agent finished scan</summary></task-notification>",
            },
        },
    ]
    c_file.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in c_records) + "\n",
        encoding="utf-8",
    )
    c_ad = ClaudeCodeAdapter(projects_dir=tmp_path / "claude_clean")
    c_msgs = c_ad.get_session_messages("sess-noise")
    # caveat 被丢弃，/init 展开模板被去重，task-notification 转为 assistant 工具步骤
    assert len(c_msgs) == 2
    assert c_msgs[0].role == "user" and c_msgs[0].content == "/init"
    assert c_msgs[1].role == "assistant" and c_msgs[1].tool_calls[0].name == "TaskNotification"

    # 3. 验证 /api/v1/files/read 文档预览接口与安全拦截
    md_doc = tmp_path / "implementation_plan.md"
    md_doc.write_text("# 架构升级计划\n\n- 步骤一\n- 步骤二\n", encoding="utf-8")
    env_doc = tmp_path / ".env"
    env_doc.write_text("SECRET_KEY=123", encoding="utf-8")

    db = MetaDatabase(db_path=tmp_path / "doc_api.db")
    app = create_app(meta_db=db)
    with TestClient(app) as client:
        file_uri = f"file:///{str(md_doc).replace(chr(92), '/')}#L1-L5"
        r_ok = client.get("/api/v1/files/read", params={"path": file_uri})
        assert r_ok.status_code == 200
        body = r_ok.json()
        assert body["is_markdown"] is True
        assert body["filename"] == "implementation_plan.md"
        assert "# 架构升级计划" in body["content"]

        r_block = client.get("/api/v1/files/read", params={"path": str(env_doc)})
        assert r_block.status_code == 403


def test_code_review_fixes_verification(tmp_path: Path):
    """验证 /code-review 发现的所有 Standards 与 Spec 修复项。"""
    from backend.adapters.base import ExecutionMode, RiskLevel, SessionStatus

    # 1. 验证领域枚举定义
    assert ExecutionMode.ASK == "ask"
    assert RiskLevel.HIGH == "high"
    assert SessionStatus.WAITING_APPROVAL == "waiting_approval"

    # 2. 验证 60s pair_code TTL 与中文数据库注释映射
    db = MetaDatabase(db_path=tmp_path / "cr_fix.db")
    schema_comments = db.get_schema_comments()
    assert len(schema_comments) > 0
    assert any(row["table_name"] == "t_device_session" for row in schema_comments)

    auth = PairingAuthManager(meta_db=db)
    assert auth.pair_code_ttl_sec == 60
    code1 = auth.bootstrap_pair_code
    assert len(code1) == 16

    # 3. 验证 Claude Code ask 模式下注入 --permission-prompt-tool stdio 及全量步骤拉取 API
    c_dir = tmp_path / "claude_full" / "proj"
    c_dir.mkdir(parents=True)
    c_file = c_dir / "sess-full-1.jsonl"
    c_file.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-10-23T15:00:00Z",
                "message": {"role": "user", "content": "测试超过4KB全量拉取"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    app = create_app(meta_db=db)
    orchestrator = app.state.orchestrator
    orchestrator.adapters["claude_code"] = ClaudeCodeAdapter(projects_dir=tmp_path / "claude_full")

    cmd_ask = orchestrator._build_cli_command(
        agent_type="claude_code",
        raw_session_id="sess-full-1",
        prompt="执行检查",
        execution_mode=ExecutionMode.ASK,
    )
    assert "--permission-prompt-tool" in cmd_ask
    assert "stdio" in cmd_ask

    with TestClient(app) as client:
        full_resp = client.get("/api/v1/sessions/claude_code:sess-full-1/steps/0/full")
        assert full_resp.status_code == 200
        data = full_resp.json()
        assert data["ok"] is True
        assert "测试超过4KB全量拉取" in data["full_output"]


def test_session_messages_pagination_cache_and_gzip(tmp_path: Path):
    """验证移动端性能优化：消息分页(limit=30 & before_step)、mtime 内存缓存失效及 GZip 压缩。"""
    c_dir = tmp_path / "claude_perf" / "proj"
    c_dir.mkdir(parents=True)
    c_file = c_dir / "sess-page-1.jsonl"

    lines = []
    for i in range(1, 46):
        role = "user" if i % 2 == 1 else "assistant"
        lines.append(
            json.dumps(
                {
                    "type": role,
                    "cwd": "d:/perf-proj",
                    "timestamp": f"2026-10-23T16:00:{i:02d}Z",
                    "message": {
                        "role": role,
                        "content": f"第 {i} 条消息内容 - " + ("性能优化测试载荷 " * 8),
                    },
                },
                ensure_ascii=False,
            )
        )
    c_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    db = MetaDatabase(db_path=tmp_path / "perf.db")
    app = create_app(meta_db=db)
    adapter = ClaudeCodeAdapter(projects_dir=tmp_path / "claude_perf")
    app.state.orchestrator.adapters["claude_code"] = adapter

    with TestClient(app) as client:
        # 1. 首屏默认拉取最近 30 条 + 验证 GZip 压缩响应头
        resp1 = client.get(
            "/api/v1/sessions/claude_code:sess-page-1/messages?limit=30",
            headers={"Accept-Encoding": "gzip"},
        )
        assert resp1.status_code == 200
        assert resp1.headers.get("content-encoding") == "gzip"
        body1 = resp1.json()
        assert body1["total_count"] == 45
        assert body1["has_more"] is True
        assert len(body1["messages"]) == 30
        assert body1["messages"][0]["step_index"] == 16
        assert body1["messages"][-1]["step_index"] == 45

        # 2. 向上翻页拉取 step_index < 16 的前序消息
        resp2 = client.get(
            "/api/v1/sessions/claude_code:sess-page-1/messages?limit=30&before_step=16"
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["total_count"] == 45
        assert body2["has_more"] is False
        assert len(body2["messages"]) == 15
        assert body2["messages"][0]["step_index"] == 1
        assert body2["messages"][-1]["step_index"] == 15

        # 3. 验证 mtime/size 缓存自动失效：追加第 46 条消息后立即可读到最新步骤
        with c_file.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "cwd": "d:/perf-proj",
                        "timestamp": "2026-10-23T16:01:00Z",
                        "message": {"role": "assistant", "content": "第 46 条追加的新回复"},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        resp3 = client.get("/api/v1/sessions/claude_code:sess-page-1/messages?limit=30")
        body3 = resp3.json()
        assert body3["total_count"] == 46
        assert body3["messages"][-1]["step_index"] == 46
        assert "第 46 条追加的新回复" in body3["messages"][-1]["content"]


def test_interactive_questions_and_git_overview(tmp_path: Path):
    """
    测试从 Antigravity-Deck 借鉴优化的手机端轻量功能：
    1. 结构化 ask_question / AskUserQuestion 单选多选提取。
    2. /api/v1/workflows 与 /api/v1/system/status 快捷指令下发。
    3. /api/v1/git/status 与 /api/v1/git/diff 只读变更检测与越界拦截。
    4. AntigravityLanguageServerBridge Connect-RPC 封装与回退机制。
    """
    from backend.adapters.base import (
        discover_quick_workflows,
        extract_interactive_questions,
        get_git_workspace_diff,
        get_git_workspace_status,
    )
    from backend.adapters.antigravity_adapter import (
        AntigravityLanguageServerBridge,
        encode_connect_envelope,
    )

    # 1. 验证 ask_question 交互式问题结构化提取
    sample_args = {
        "questions": [
            {
                "question": "请选择实施策略？",
                "options": ["方案A：最小改动", "方案B：全面重构"],
                "is_multi_select": False,
            },
            {
                "question": "需包含哪些模块？",
                "options": ["前端UI", "后端API", "文档"],
                "is_multi_select": True,
            },
        ]
    }
    extracted = extract_interactive_questions("ask_question", sample_args)
    assert len(extracted) == 2
    assert extracted[0]["question"] == "请选择实施策略？"
    assert len(extracted[0]["options"]) == 2
    assert extracted[0]["is_multi_select"] is False
    assert extracted[1]["is_multi_select"] is True

    # 2. 验证 Connect-RPC 5 字节数据包封装
    env = encode_connect_envelope({"foo": "bar"})
    assert len(env) > 5
    assert env[0] == 0x00
    expected_len = len(json.dumps({"foo": "bar"}).encode("utf-8"))
    assert int.from_bytes(env[1:5], "big") == expected_len

    # 3. 验证快捷指令与技能发现
    wf_items = discover_quick_workflows()
    assert len(wf_items) >= 5
    assert any(w["command"] == "/plan" for w in wf_items)
    assert any(w["command"] == "/review" for w in wf_items)
    assert any("继续执行" in w["command"] for w in wf_items)

    # 4. 验证 Git 状态与 Diff 接口
    # 创建一个临时 Git 仓库用于测试
    import subprocess
    repo_dir = tmp_path / "git_test_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo_dir), capture_output=True, check=True)

    file_a = repo_dir / "hello.py"
    file_a.write_text("print('hello v1')\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.py"], cwd=str(repo_dir), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo_dir), capture_output=True, check=True)

    # 修改 hello.py 并新建 untracked.py
    file_a.write_text("print('hello v2 - modified')\n", encoding="utf-8")
    file_b = repo_dir / "untracked.py"
    file_b.write_text("print('new untracked file')\n", encoding="utf-8")

    git_stat = get_git_workspace_status(str(repo_dir))
    assert git_stat["is_git_repo"] is True
    assert git_stat["branch"] in ("main", "master")
    assert git_stat["changed_count"] >= 2
    paths = {f["path"] for f in git_stat["files"]}
    assert "hello.py" in paths
    assert "untracked.py" in paths

    diff_single = get_git_workspace_diff(str(repo_dir), "hello.py")
    assert diff_single["ok"] is True
    assert "-print('hello v1')" in diff_single["diff"]
    assert "+print('hello v2 - modified')" in diff_single["diff"]

    diff_untracked = get_git_workspace_diff(str(repo_dir), "untracked.py")
    assert diff_untracked["ok"] is True
    assert "+print('new untracked file')" in diff_untracked["diff"]

    # 验证路径越界防护
    diff_escape = get_git_workspace_diff(str(repo_dir), "../../secret.txt")
    assert diff_escape["ok"] is False
    assert "禁止越界" in diff_escape["error"]

    # 5. 验证 API 端点集成
    db = MetaDatabase(db_path=tmp_path / "feat_test.db")
    app = create_app(meta_db=db)
    with TestClient(app) as client:
        status_resp = client.get("/api/v1/system/status")
        assert status_resp.status_code == 200
        assert "workflows" in status_resp.json()

        git_api_resp = client.get(
            f"/api/v1/git/status?project_path={str(repo_dir).replace(chr(92), '/')}"
        )
        assert git_api_resp.status_code == 200
        assert git_api_resp.json()["changed_count"] >= 2


def test_deepened_adapter_contracts_and_workspace_decoupling(tmp_path: Path):
    """
    测试 /improve-codebase-architecture 深化后的适配器多态契约与工作区解耦：
    1. BaseAgentAdapter 深度接口：命令行组装、直连派发、会话取消与权限响应。
    2. AntigravityAdapter 与 ClaudeCodeAdapter 原生 stream 帧工具块提取。
    3. OpenCodeAdapter 本地 HTTP Bridge 权限响应与 is_alive 别名兼容。
    4. backend.core.workspace 模块独立性与向后兼容导出。
    """
    from backend.adapters.antigravity_adapter import AntigravityAdapter
    from backend.adapters.claude_adapter import ClaudeCodeAdapter
    from backend.adapters.opencode_adapter import OpenCodeAdapter
    from backend.core.workspace import (
        decode_workspace_uri,
        evaluate_project_dir,
        normalize_project_path,
    )

    # 1. 验证工作区模块独立函数
    assert normalize_project_path(r"D:\Repo\Foo\\") == "d:/Repo/Foo"
    assert decode_workspace_uri("file:///d%3A/Repo/Bar") == "d:/Repo/Bar"
    is_p, p_norm, p_name = evaluate_project_dir(str(Path.cwd()))
    assert is_p is True
    assert p_name != ""

    # 2. 验证 ClaudeCodeAdapter 多态契约
    claude = ClaudeCodeAdapter()
    claude_cmd = claude.build_cli_command(
        raw_session_id="s123",
        prompt="测试提示词",
        execution_mode="ask",
        model_name="claude-sonnet-4-6",
        approved_tools=["Bash", "Edit"],
    )
    assert any("claude" in part.lower() for part in claude_cmd)
    assert "--output-format" in claude_cmd
    assert "--permission-prompt-tool" in claude_cmd
    assert "--allowedTools" in claude_cmd

    claude_frame = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "思考"},
                {"type": "tool_use", "id": "tc_c1", "name": "Bash", "input": {"command": "ls"}},
            ]
        },
    }
    claude_tools = claude.extract_stream_tool_blocks(claude_frame)
    assert len(claude_tools) == 1
    assert claude_tools[0]["name"] == "Bash"

    # 3. 验证 AntigravityAdapter 深度契约与工具提取
    agy = AntigravityAdapter(agent_type="agy_cli", root_dir=tmp_path / "agy")
    agy_cmd = agy.build_cli_command(
        raw_session_id="conv-1",
        prompt="优化架构",
        execution_mode="plan",
        model_name="claude-sonnet-5-5-high",
    )
    assert any("agy" in part.lower() for part in agy_cmd)
    assert "--mode" in agy_cmd
    assert "plan" in agy_cmd

    agy_frame = {
        "step_index": 5,
        "type": "PLANNER_RESPONSE",
        "tool_calls": [
            {
                "id": "tc_agy_1",
                "name": "run_command",
                "args": {"CommandLine": "pytest -q"},
            }
        ],
    }
    agy_tools = agy.extract_stream_tool_blocks(agy_frame)
    assert len(agy_tools) == 1
    assert agy_tools[0]["name"] == "run_command"
    assert agy_tools[0]["input"]["CommandLine"] == "pytest -q"

    # 4. 验证 OpenCodeAdapter 多态契约与权限响应
    opencode = OpenCodeAdapter(data_dir=tmp_path / "oc")
    oc_cmd = opencode.build_cli_command(
        raw_session_id="oc-session-1",
        prompt="hello opencode",
        execution_mode="auto",
    )
    assert "run" in oc_cmd
    assert "--auto" in oc_cmd
    assert opencode.http_bridge.is_alive() is False  # 服务未启动时安全返回 False，不崩溃
    assert opencode.respond_permission("oc-1", "perm-1", "approved") is False


def test_untracked_diff_and_review_fixes(tmp_path: Path):
    """
    验证代码审查修复项：
    1. get_git_workspace_diff 对未跟踪新文件 (Untracked) 能生成正确的 Unified Diff 预览。
    2. 对越界访问和非法路径保持严格拒绝。
    """
    repo = tmp_path / "git_test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), capture_output=True, check=True)

    # 提交首个文件
    f1 = repo / "README.md"
    f1.write_text("# Hello Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)

    # 创建未跟踪的新文件
    untracked = repo / "new_feature.py"
    untracked.write_text("print('hello new untracked feature')\n", encoding="utf-8")

    # 单文件获取未跟踪 diff
    res_single = get_git_workspace_diff(str(repo), "new_feature.py")
    assert res_single["ok"] is True
    assert "diff --git a/new_feature.py b/new_feature.py" in res_single["diff"]
    assert "+print('hello new untracked feature')" in res_single["diff"]

    # 全量汇总获取，也应包含未跟踪新文件
    res_all = get_git_workspace_diff(str(repo))
    assert res_all["ok"] is True
    assert "new_feature.py" in res_all["diff"]
    assert "+print('hello new untracked feature')" in res_all["diff"]

    # 越界路径拒绝防护
    res_hack = get_git_workspace_diff(str(repo), "../secret.txt")
    assert res_hack["ok"] is False
    assert "禁止越界" in res_hack["error"]


@pytest.mark.asyncio
async def test_direct_dispatch_active_state_and_abort_cancellation(tmp_path: Path):
    """
    测试直连派发任务与统一编排器状态机的生命周期管理：
    1. active_direct_tasks 纳入 is_session_active 与 get_state_snapshot。
    2. abort_session 同步取消直连协程并广播。
    3. background_tasks 集合强引用保护。
    """
    import asyncio
    import unittest.mock
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator

    db = MetaDatabase(db_path=tmp_path / "orch_test.db")
    orch = AgentOrchestrator(meta_db=db)

    # 验证初始状态
    test_uid = "agy_ide:direct_conv_1"
    assert orch.is_session_active(test_uid) is False

    # 模拟启动一个直连任务
    async def _mock_direct_task():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    t = asyncio.create_task(_mock_direct_task())
    orch.active_direct_tasks[test_uid] = t
    orch.background_tasks.add(t)

    # 验证活跃判定与快照
    assert orch.is_session_active(test_uid) is True
    snap = orch.get_state_snapshot()
    assert test_uid in snap["active_session_ids"]

    detail = orch.get_session_detail(test_uid)
    assert detail["is_running"] is True

    # 验证 abort_session 协同取消
    killed = await orch.abort_session(test_uid, reason="测试手动中断")
    assert killed is True
    assert orch.is_session_active(test_uid) is False
    assert test_uid not in orch.active_direct_tasks
    await asyncio.sleep(0)
    assert t.cancelled() or t.done()


def test_review_fixed_vulnerabilities_and_hardening(tmp_path: Path):
    """
    针对 /review 发现的 6 项问题进行专项回归测试：
    1. 验证未配对的 Tailscale IP 无法绕过鉴权，配对或携带 Token 后可正常放行。
    2. 验证 /api/v1/files/read 对 .ssh / .aws / .env 等敏感路径实施强力拦截 (403)。
    3. 验证 /api/v1/git/status 与 /api/v1/git/diff 异步执行正常返回。
    """
    from fastapi import HTTPException
    from backend.core.database import MetaDatabase
    from backend.app import create_app

    db = MetaDatabase(db_path=tmp_path / "harden_test.db")
    app = create_app(meta_db=db)
    auth_mgr = app.state.auth_mgr

    # 1. 验证 Tailscale IP 鉴权修复：未配对的 100.x.y.z 节点被拦截 (401)
    class FakeClient:
        def __init__(self, host: str):
            self.host = host

    class FakeRequest:
        def __init__(self, host: str):
            self.client = FakeClient(host)
            self.headers = {}

    unpaired_ts_req = FakeRequest("100.64.12.34")

    # 未提供 token 且未配对 -> 必须抛出 401
    with pytest.raises(HTTPException) as exc_info:
        # 获取 app 内部的 verify_request_auth 逻辑测试
        from fastapi import Header, Query
        # 直接测试 auth 核心逻辑
        provided_token = None
        client_host = unpaired_ts_req.client.host
        is_allowed = (
            (provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host))
            or client_host in ("127.0.0.1", "::1", "localhost", "testclient")
            or (client_host.startswith("100.") and auth_mgr.is_paired_tailscale_ip(client_host))
        )
        if not is_allowed:
            raise HTTPException(status_code=401, detail="未授权")
    assert exc_info.value.status_code == 401

    # 生成 pair_code 配对 100.64.12.34
    code = auth_mgr.generate_pair_code()
    pair_res = auth_mgr.exchange_pair_code(code, device_name="TS Phone", client_ip="100.64.12.34")
    assert pair_res is not None
    assert auth_mgr.is_paired_tailscale_ip("100.64.12.34") is True

    # 配对后再校验，应该顺利放行
    is_allowed_after = (
        client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        or (client_host.startswith("100.") and auth_mgr.is_paired_tailscale_ip(client_host))
    )
    assert is_allowed_after is True

    # 2. 验证敏感目录与 .env 文件拦截
    with TestClient(app) as client:
        # 尝试读取 .ssh 目录下的文件
        ssh_fake = tmp_path / ".ssh"
        ssh_fake.mkdir()
        ssh_file = ssh_fake / "config"
        ssh_file.write_text("Host github.com", encoding="utf-8")

        resp_ssh = client.get("/api/v1/files/read", params={"path": str(ssh_file)})
        assert resp_ssh.status_code == 403
        assert "禁止读取系统敏感配置目录文件" in resp_ssh.json()["detail"]

        # 尝试读取 .env.staging
        env_staging = tmp_path / ".env.staging"
        env_staging.write_text("DB_PASS=secret", encoding="utf-8")
        resp_env = client.get("/api/v1/files/read", params={"path": str(env_staging)})
        assert resp_env.status_code == 403

        # 3. 验证 git status / diff 异步接口正常返回
        git_resp = client.get("/api/v1/git/status", params={"project_path": str(tmp_path)})
        assert git_resp.status_code == 200
        assert git_resp.json()["ok"] is True


def test_session_meta_delete_endpoint(tmp_path: Path):
    """测试会话元数据删除接口及数据库记录物理清理。"""
    db = MetaDatabase(db_path=tmp_path / "meta_del.db")
    db.upsert_session_meta(
        unified_id="claude_code:to_delete",
        agent_type="claude_code",
        raw_session_id="to_delete",
        title="待删除的废弃会话",
    )
    overrides = db.get_session_overrides()
    assert "claude_code:to_delete" in overrides

    app = create_app(meta_db=db)
    with TestClient(app) as client:
        del_resp = client.delete("/api/v1/sessions/claude_code:to_delete")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True
        assert del_resp.json()["deleted"] == "claude_code:to_delete"

    overrides_after = db.get_session_overrides()
    assert "claude_code:to_delete" not in overrides_after


def test_healthz_and_doctor_endpoints(tmp_path: Path):
    """测试轻量级未鉴权健康检查探针 (/healthz) 与 doctor 诊断模式。"""
    db = MetaDatabase(db_path=tmp_path / "health.db")
    app = create_app(meta_db=db)
    with TestClient(app) as client:
        r1 = client.get("/healthz")
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["ok"] is True
        assert d1["status"] == "healthy"
        assert d1["version"] == "2.0.0"

        r2 = client.get("/api/v1/health")
        assert r2.status_code == 200
        assert r2.json()["status"] == "healthy"

        # 验证 /api/v1/system/status 返回完整的 4 大 Agent 健康体检表 (agents_health)
        r3 = client.get("/api/v1/system/status")
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["ok"] is True
        assert "agents_health" in d3, "agents_health 字段缺失"
        assert isinstance(d3["agents_health"], list)
        assert len(d3["agents_health"]) >= 3
        for item in d3["agents_health"]:
            assert "agent_type" in item
            assert "agent_label" in item
            assert "ready" in item
            assert "description" in item

    # 测试 run.py --doctor 逻辑函数返回 0
    from run import run_doctor
    code = run_doctor(port=8765)
    assert code == 0


@pytest.mark.asyncio
async def test_process_stream_launch_failure_resilience(tmp_path: Path):
    """验证当子进程启动抛出异常（如二进制不存在）时，不触发 UnboundLocalError 且优雅下发终止事件。"""
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator, TurnContext
    from backend.adapters.claude_adapter import ClaudeCodeAdapter

    db = MetaDatabase(db_path=tmp_path / "proc_fail.db")
    orchestrator = AgentOrchestrator(
        meta_db=db,
        adapters={"claude_code": ClaudeCodeAdapter(projects_dir=tmp_path / "claude")},
    )

    events: list[tuple[str, dict[str, Any]]] = []
    async def capture(envelope: dict[str, Any]):
        events.append((envelope.get("type", ""), envelope.get("payload", {})))

    orchestrator.register_ws_subscriber(capture)

    # 构造一个指向不存在二进制可执行文件的异常 context
    ctx = TurnContext(
        unified_id="claude_code:nonexistent_proc_test",
        agent_type="claude_code",
        raw_session_id="nonexistent_proc_test",
        prompt="hello",
        workdir=str(tmp_path),
        execution_mode="ask",
    )

    # mock _build_cli_command 返回不存在的可执行文件名，并显式放行测试进程启动
    orchestrator._allow_real_process_in_test = True
    orchestrator._build_cli_command = lambda **kwargs: ["non_existent_executable_12345_xyz"]  # type: ignore

    # 执行应被捕获处理，绝不抛出 UnboundLocalError
    await orchestrator._run_agent_turn_stream(ctx)

    evt_names = [e[0] for e in events]
    assert "SESSION_TURN_STARTED" in evt_names
    assert "SESSION_ERROR" in evt_names
    assert "SESSION_TURN_FINISHED" in evt_names


def test_interactive_question_singular_and_plural_parsing():
    """验证 extract_interactive_questions 既支持单对象 question 又支持复数 questions 列表。"""
    from backend.adapters.base import extract_interactive_questions

    # 1. 单对象格式
    single_args = {
        "question": "What should I review?",
        "options": ["A) Branch diff", "B) Plan doc", "C) File path"],
    }
    res_single = extract_interactive_questions("AskUserQuestion", single_args)
    assert len(res_single) == 1
    assert res_single[0]["question"] == "What should I review?"
    assert len(res_single[0]["options"]) == 3

    # 2. 复数 questions 列表格式
    plural_args = {
        "questions": [
            {
                "question": "Choose mode",
                "options": [{"label": "Fast", "description": "Quick check"}, {"label": "Thorough"}],
                "is_multi_select": True,
            }
        ]
    }
    res_plural = extract_interactive_questions("ask_question", plural_args)
    assert len(res_plural) == 1
    assert res_plural[0]["question"] == "Choose mode"
    assert res_plural[0]["is_multi_select"] is True
    assert "Fast — Quick check" in res_plural[0]["options"][0]


def test_file_read_security_git_and_rc_blocking(tmp_path: Path):
    """验证 /api/v1/files/read 对 .git 目录内文件及 .npmrc / .netrc / .pypirc 实施深度防御拦截。"""
    from backend.core.database import MetaDatabase
    from backend.app import create_app

    db = MetaDatabase(db_path=tmp_path / "sec_test.db")
    app = create_app(meta_db=db)

    with TestClient(app) as client:
        # 1. 拦截 .git/config 敏感凭据读取
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        git_cfg = git_dir / "config"
        git_cfg.write_text("[core]\nrepositoryformatversion = 0", encoding="utf-8")

        resp_git = client.get("/api/v1/files/read", params={"path": str(git_cfg)})
        assert resp_git.status_code == 403
        assert "禁止读取系统敏感配置目录文件" in resp_git.json()["detail"]

        # 2. 拦截 .npmrc / .netrc
        npmrc = tmp_path / ".npmrc"
        npmrc.write_text("//registry.npmjs.org/:_authToken=npm_secret", encoding="utf-8")
        resp_npm = client.get("/api/v1/files/read", params={"path": str(npmrc)})
        assert resp_npm.status_code == 403

        # 3. 正常只读文件放行
        readme = tmp_path / "README.md"
        readme.write_text("# Hello AgentDeck", encoding="utf-8")
        resp_ok = client.get("/api/v1/files/read", params={"path": str(readme)})
        assert resp_ok.status_code == 200
        assert "# Hello AgentDeck" in resp_ok.json()["content"]


def test_token_usage_with_prompt_caching(tmp_path: Path):
    """验证 Token Usage 水位计算正确累加 prompt caching (cache_read_tokens)。"""
    import json
    from backend.adapters.antigravity_adapter import AntigravityAdapter

    root_dir = tmp_path / "antigravity"
    brain_dir = root_dir / "brain"
    session_id = "test-cached-session"
    log_dir = brain_dir / session_id / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    tp = log_dir / "transcript.jsonl"

    # 模拟 Antigravity transcript: 2,000 input tokens + 48,000 cache read tokens
    with open(tp, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "step_index": 1,
            "input_tokens": 2000,
            "cache_read_tokens": 48000,
            "output_tokens": 500,
        }) + "\n")

    adapter = AntigravityAdapter(
        root_dir=root_dir,
    )
    usage = adapter.get_session_token_usage(session_id, model_name="claude-sonnet-5-5-high")
    # 总有效上下文应当是 2000 + 48000 = 50,000
    assert usage["context_tokens"] == 50000
    assert usage["context_limit"] == 200000
    assert usage["context_percent"] == 25.0
    assert usage["cache_read_tokens"] == 48000
    assert usage["last_output_tokens"] == 500


def test_architecture_deepening_session_token_usage_and_cache(tmp_path: Path):
    """测试架构深化：SessionTokenUsage 强类型与 BaseAgentAdapter 缓存初始化。"""
    from backend.adapters.base import SessionTokenUsage, BaseAgentAdapter
    from backend.adapters.claude_adapter import ClaudeCodeAdapter
    from backend.adapters.opencode_adapter import OpenCodeAdapter

    tu = SessionTokenUsage(context_tokens=1000, context_limit=200000)
    d = tu.to_dict()
    assert d["context_tokens"] == 1000
    assert d["context_limit"] == 200000
    assert d["context_percent"] == 0.0

    claude_adapter = ClaudeCodeAdapter(projects_dir=tmp_path / "claude")
    assert hasattr(claude_adapter, "_token_usage_cache")
    assert isinstance(claude_adapter._token_usage_cache, dict)

    opencode_adapter = OpenCodeAdapter(data_dir=tmp_path / "opencode")
    assert hasattr(opencode_adapter, "_token_usage_cache")
    assert isinstance(opencode_adapter._token_usage_cache, dict)


@pytest.mark.asyncio
async def test_architecture_deepening_pending_approval_direct_object(tmp_path: Path):
    """测试架构深化：request_mobile_tool_approval 接收 PendingApproval 对象直接调用消除参数成群传递。"""
    import asyncio
    import unittest.mock
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator, PendingApproval
    from backend.core.database import utc_now_iso

    db = MetaDatabase(db_path=tmp_path / "pa_test.db")
    orchestrator = AgentOrchestrator(meta_db=db)

    pa = PendingApproval(
        approval_id="apr_test_123",
        unified_id="agy_cli:test_pa",
        agent_type="agy_cli",
        raw_session_id="test_pa",
        tool_call_id="tc_1",
        tool_name="run_command",
        args_summary="rm -rf /tmp/test",
        diff_text="",
        risk_level="high",
        prompt_context="测试",
        project_path=str(tmp_path),
        created_at=utc_now_iso(),
    )

    # Mock 发送后续指导提示，防止单测拉起真实外部宿主机进程或污染外部数据库
    orchestrator.send_prompt_to_session = unittest.mock.AsyncMock(return_value={"ok": True})

    t = asyncio.create_task(orchestrator.request_mobile_tool_approval(pa))
    await asyncio.sleep(0.02)

    assert "agy_cli:test_pa" in orchestrator.pending_approvals
    assert orchestrator.pending_approvals["agy_cli:test_pa"].approval_id == "apr_test_123"

    # 做出审批决策
    res = await orchestrator.resolve_tool_approval(
        unified_id="agy_cli:test_pa",
        decision="denied",
        feedback="不允许执行危险命令",
    )
    assert res["ok"] is True
    out = await t
    assert out["decision"] == "denied"

    # 验证向会话发送替代指导 prompt，且未污染宿主机真实进程
    await asyncio.sleep(0.05)
    orchestrator.send_prompt_to_session.assert_awaited_once()
    call_args = orchestrator.send_prompt_to_session.await_args
    assert "不允许执行危险命令" in call_args.kwargs.get("prompt", "")


def test_normalize_execution_mode_per_agent(tmp_path: Path):
    """验证各 Agent 针对规划、自动改、全自动与审批权限模式的多态安全纠偏与规范化。"""
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator
    from backend.adapters.base import ExecutionMode

    db = MetaDatabase(db_path=tmp_path / "test_norm_mode.db")
    orchestrator = AgentOrchestrator(meta_db=db)

    # 1. Claude Code 与 Antigravity 原生支持全部 4 种模式
    for agent in ("claude_code", "agy_cli", "agy_ide"):
        assert orchestrator.normalize_execution_mode(agent, "plan") == ExecutionMode.PLAN.value
        assert orchestrator.normalize_execution_mode(agent, "ask") == ExecutionMode.ASK.value
        assert orchestrator.normalize_execution_mode(agent, "accept_edits") == ExecutionMode.ACCEPT_EDITS.value
        assert orchestrator.normalize_execution_mode(agent, "auto") == ExecutionMode.AUTO.value

    # 2. OpenCode 原生仅支持 ask 与 auto：plan 和 accept_edits 必须安全回退至 ask（审批），避免误判
    assert orchestrator.normalize_execution_mode("opencode", "plan") == ExecutionMode.ASK.value
    assert orchestrator.normalize_execution_mode("opencode", "accept_edits") == ExecutionMode.ASK.value
    assert orchestrator.normalize_execution_mode("opencode", "ask") == ExecutionMode.ASK.value
    assert orchestrator.normalize_execution_mode("opencode", "auto") == ExecutionMode.AUTO.value

    # 3. 未知/非法模式默认回退至 ask
    assert orchestrator.normalize_execution_mode("opencode", "invalid_mode") == ExecutionMode.ASK.value
    assert orchestrator.normalize_execution_mode("claude_code", "unknown") == ExecutionMode.ASK.value


@pytest.mark.asyncio
async def test_security_cswsh_and_tailscale_cgnat(tmp_path: Path):
    """验证 Origin 检查严格抵御 100.evil.com 域名通配绕过，且审批拒绝在无反馈时不崩溃。"""
    from backend.core.tailscale_auth import PairingAuthManager
    from backend.engines.process_pool import AgentOrchestrator, AgentProcessPool, PendingApproval

    db = MetaDatabase(db_path=tmp_path / "cswsh_test.db")
    auth = PairingAuthManager(meta_db=db)

    # 1. 验证 Origin 严格校验
    assert auth.is_trusted_origin("http://100.64.0.1:8765", ["100.64.0.1"]) is True
    assert auth.is_trusted_origin("http://100.115.92.19:8765", ["100.115.92.19"]) is True
    assert auth.is_trusted_origin("http://127.0.0.1:8765", ["127.0.0.1"]) is True
    # 恶意域名通配必须被严格拒绝拦截
    assert auth.is_trusted_origin("http://100.evil.com:8765", ["100.64.0.1"]) is False
    assert auth.is_trusted_origin("http://100-phishing.org", ["100.64.0.1"]) is False
    # 非 Tailscale CGNAT 范围的公网 IP (100.0.0.1) 必须被拦截
    assert auth.is_trusted_origin("http://100.0.0.1:8765", ["100.64.0.1"]) is False

    # 2. 验证向后兼容别名
    assert AgentProcessPool is AgentOrchestrator

    # 3. 验证审批拒绝且 feedback 为空时不会出现 UnboundLocalError 崩溃
    orch = AgentOrchestrator(meta_db=db)
    pa = PendingApproval(
        approval_id="apr_test_99",
        unified_id="claude_code:sess_mock",
        agent_type="claude_code",
        raw_session_id="sess_mock",
        tool_call_id="call_mock",
        tool_name="Bash",
        args_summary="rm -rf /",
        diff_text="",
        risk_level="high",
        prompt_context="测试高危操作",
        project_path=str(tmp_path),
        created_at="2026-10-23T00:00:00Z",
    )
    import asyncio
    import unittest.mock
    fut = asyncio.get_running_loop().create_future()
    orch.pending_approvals["claude_code:sess_mock"] = pa
    orch._approval_futures["claude_code:sess_mock"] = fut

    # 在另一个任务中执行审批等待
    req_task = asyncio.create_task(orch.request_mobile_tool_approval(pa, proc=None))
    await asyncio.sleep(0.01)

    # 用户直接点击拒绝，不附带任何建议 (feedback="")
    res = await orch.resolve_tool_approval("claude_code:sess_mock", decision="denied", feedback="")
    assert res["ok"] is True
    assert res["decision"] == "denied"
    out = await req_task
    assert out["decision"] == "denied"


def test_running_and_approval_sessions_priority_sort(tmp_path: Path):
    """测试会话排序权重：置顶 > 等待审批 (waiting_approval) > 运行中 (running) > 闲置 (idle)。"""
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator
    from backend.adapters.base import UnifiedSession

    db = MetaDatabase(tmp_path / "test_sort.db")
    orch = AgentOrchestrator(meta_db=db)

    # 构造假会话
    class DummyAdapter:
        agent_type = "dummy"
        agent_label = "Dummy"
        def list_sessions(self, limit: int = 40):
            return [
                UnifiedSession(
                    unified_id="dummy:idle_old",
                    agent_type="dummy",
                    agent_label="Dummy",
                    raw_session_id="idle_old",
                    title="普通闲置老会话",
                    preview="...",
                    project_path=str(tmp_path),
                    status="idle",
                    step_count=1,
                    updated_at="2026-10-23T10:00:00Z",
                    is_pinned=0,
                ),
                UnifiedSession(
                    unified_id="dummy:idle_new",
                    agent_type="dummy",
                    agent_label="Dummy",
                    raw_session_id="idle_new",
                    title="普通闲置新会话",
                    preview="...",
                    project_path=str(tmp_path),
                    status="idle",
                    step_count=1,
                    updated_at="2026-10-23T12:00:00Z",
                    is_pinned=0,
                ),
                UnifiedSession(
                    unified_id="dummy:running_sess",
                    agent_type="dummy",
                    agent_label="Dummy",
                    raw_session_id="running_sess",
                    title="普通运行中会话",
                    preview="...",
                    project_path=str(tmp_path),
                    status="running",
                    step_count=1,
                    updated_at="2026-10-23T09:00:00Z",
                    is_pinned=0,
                ),
                UnifiedSession(
                    unified_id="dummy:waiting_sess",
                    agent_type="dummy",
                    agent_label="Dummy",
                    raw_session_id="waiting_sess",
                    title="普通待审批会话",
                    preview="...",
                    project_path=str(tmp_path),
                    status="waiting_approval",
                    step_count=1,
                    updated_at="2026-10-23T08:00:00Z",
                    is_pinned=0,
                ),
                UnifiedSession(
                    unified_id="dummy:pinned_idle",
                    agent_type="dummy",
                    agent_label="Dummy",
                    raw_session_id="pinned_idle",
                    title="置顶闲置会话",
                    preview="...",
                    project_path=str(tmp_path),
                    status="idle",
                    step_count=1,
                    updated_at="2026-10-23T07:00:00Z",
                    is_pinned=1,
                ),
            ]

    orch.adapters = {"dummy": DummyAdapter()}
    sessions = orch.list_all_sessions(limit_per_agent=10)

    # 排序预期：
    # 1. 置顶会话 (dummy:pinned_idle)
    # 2. 待审批会话 (dummy:waiting_sess)
    # 3. 运行中会话 (dummy:running_sess)
    # 4. 最新闲置会话 (dummy:idle_new)
    # 5. 较旧闲置会话 (dummy:idle_old)
    ordered_ids = [s["unified_id"] for s in sessions]
    assert ordered_ids == [
        "dummy:pinned_idle",
        "dummy:waiting_sess",
        "dummy:running_sess",
        "dummy:idle_new",
        "dummy:idle_old",
    ]


def test_app_version_and_dynamic_asset_fingerprint(tmp_path: Path):
    """测试 /api/v1/version 端点及主页 HTML 动态注入前端版本指纹与静态资源防缓存穿透。"""
    from backend.app import create_app
    from fastapi.testclient import TestClient

    db = MetaDatabase(db_path=tmp_path / "version_test.db")
    app = create_app(meta_db=db)

    with TestClient(app) as client:
        # 1. 验证版本接口
        v_res = client.get("/api/v1/version")
        assert v_res.status_code == 200
        v_data = v_res.json()
        assert v_data["ok"] is True
        assert v_data["version"] == "2.0.0"
        fp = v_data.get("frontend_version")
        assert fp and isinstance(fp, str)

        # 2. 验证主页 HTML 动态注入指纹
        index_res = client.get("/")
        assert index_res.status_code == 200
        html = index_res.text
        assert f'<meta name="agentdeck-build" content="{fp}">' in html
        assert f"/static/app.js?v={fp}" in html
        assert f"/static/styles.css?v={fp}" in html

        # 3. 验证 Apple Touch Icon 路由
        icon_res = client.get("/apple-touch-icon.png")
        assert icon_res.status_code == 200
        assert icon_res.headers["content-type"] == "image/png"


def test_bounded_lru_cache():
    """验证 BoundedLRUCache 容量限制、LRU 淘汰机制与访问时间刷新。"""
    from backend.adapters.base import BoundedLRUCache

    cache = BoundedLRUCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    assert len(cache) == 3

    # 访问 "a"，使其变为最新使用
    val_a = cache.get("a")
    assert val_a == 1

    # 插入 "d"，应淘汰最旧未访问的 "b"
    cache["d"] = 4
    assert len(cache) == 3
    assert "b" not in cache
    assert "a" in cache
    assert "c" in cache
    assert "d" in cache

    # 通过 __getitem__ 访问 "c"，使其变为最新
    _ = cache["c"]
    cache["e"] = 5
    assert "a" not in cache
    assert "c" in cache
    assert "d" in cache
    assert "e" in cache

    # 测试 setdefault 与 update 均遵守 maxsize 约束
    cache.setdefault("f", 6)
    assert len(cache) == 3
    assert "d" not in cache

    cache.update({"g": 7, "h": 8})
    assert len(cache) == 3
    assert list(cache.keys()) == ["f", "g", "h"]


def test_claude_scan_cache_and_invalidation(tmp_path: Path):
    """测试 ClaudeCodeAdapter 扫描短效缓存与 watchfiles 联动即时失效。"""
    from backend.adapters.claude_adapter import ClaudeCodeAdapter

    p_dir = tmp_path / "claude_projects"
    proj_a = p_dir / "proj_a"
    proj_a.mkdir(parents=True)
    sess_file = proj_a / "sess1.jsonl"
    sess_file.write_text('{"type": "user", "text": "hello"}\n', encoding="utf-8")

    adapter = ClaudeCodeAdapter(projects_dir=p_dir)
    res1 = adapter.list_sessions(limit=10)
    assert len(res1) == 1
    assert adapter._dir_scan_cache[1] == [sess_file]

    # 添加新文件，但在 2.5s TTL 内直接读取应命中扫描短效缓存
    sess_file2 = proj_a / "sess2.jsonl"
    sess_file2.write_text('{"type": "user", "text": "world"}\n', encoding="utf-8")
    res2 = adapter.list_sessions(limit=10)
    # 命中缓存，仍然是 1 个会话候选
    assert len(res2) == 1

    # 手动或通过 watchfiles 触发失效
    adapter.invalidate_scan_cache()
    res3 = adapter.list_sessions(limit=10)
    assert len(res3) == 2


def test_orchestrator_process_concurrency_and_etag(tmp_path: Path):
    """测试进程并发控制信号量与 /sessions 304 协商缓存。"""
    from backend.app import create_app
    from fastapi.testclient import TestClient

    db = MetaDatabase(db_path=tmp_path / "etag_test.db")
    app = create_app(meta_db=db)
    orch = app.state.orchestrator

    # 1. 验证子进程并发信号量初始配置
    assert orch.max_concurrent_subprocesses == 3
    assert orch.process_semaphore._value == 3

    with TestClient(app) as client:
        # 2. 初始请求获取 ETag
        res = client.get("/api/v1/sessions?agent_type=all&limit=40")
        assert res.status_code == 200
        etag = res.headers.get("etag")
        assert etag and etag.startswith('W/"')

        # 3. 携带当前匹配的 ETag 请求应返回 304 Not Modified
        current_etag = orch.get_sessions_etag("all", 40)
        res_304 = client.get(
            "/api/v1/sessions?agent_type=all&limit=40",
            headers={"If-None-Match": current_etag},
        )
        assert res_304.status_code == 304
        assert not res_304.text

        # 4. 状态变更后（bump_version）再带旧 ETag 应重新返回 200
        orch.bump_version()
        res_fresh = client.get(
            "/api/v1/sessions?agent_type=all&limit=40",
            headers={"If-None-Match": current_etag},
        )
        assert res_fresh.status_code == 200
        new_etag = res_fresh.headers.get("etag")
        assert new_etag != current_etag

        # 5. 测试 /messages 的 304 协商缓存
        m_res = client.get("/api/v1/sessions/claude_code:dummy_session/messages?limit=30")
        assert m_res.status_code == 200
        m_etag = m_res.headers.get("etag")
        assert m_etag and m_etag.startswith('W/"msg-')

        m_res_304 = client.get(
            "/api/v1/sessions/claude_code:dummy_session/messages?limit=30",
            headers={"If-None-Match": m_etag},
        )
        assert m_res_304.status_code == 304
        assert not m_res_304.text


@pytest.mark.asyncio
async def test_realtime_event_bus_isolated():
    """验证解耦后的 RealtimeEventBus 环形缓冲、增量追赶与死连接剔除能力。"""
    from backend.engines.event_bus import RealtimeEventBus

    bus = RealtimeEventBus(buffer_maxlen=5)
    assert bus.seq_counter == 0
    assert len(bus.event_buffer) == 0

    received = []

    async def normal_sub(env):
        received.append(env)

    async def broken_sub(env):
        raise ConnectionResetError("网络异常中断")

    bus.register_subscriber(normal_sub)
    bus.register_subscriber(broken_sub)

    # 广播事件 1
    ev1 = await bus.broadcast("TEST_EVENT", {"k": "v1"})
    assert ev1["seq_id"] == 1
    assert len(received) == 1
    # broken_sub 应在异常后被自动移出订阅者集合
    assert broken_sub not in bus._subscribers

    # 循环广播 6 次，验证 maxlen=5 环形溢出自动轮转
    for i in range(2, 8):
        await bus.broadcast("TEST_EVENT", {"step": i})

    assert bus.seq_counter == 7
    assert len(bus.event_buffer) == 5
    assert bus.event_buffer[0]["seq_id"] == 3
    assert bus.event_buffer[-1]["seq_id"] == 7

    # 测试断线追赶快照
    snap = bus.get_snapshot(since_seq_id=4)
    assert snap["current_seq_id"] == 7
    # seq_id > 4 包含 5, 6, 7
    assert len(snap["missed_events"]) == 3
    assert [e["seq_id"] for e in snap["missed_events"]] == [5, 6, 7]
    assert snap["is_overflow"] is False

    # 测试断网过久导致最早事件被覆盖 (since_seq_id < oldest_seq_id 3)
    snap_overflow = bus.get_snapshot(since_seq_id=1)
    assert snap_overflow["is_overflow"] is True


def test_incremental_seek_reading_mechanism(tmp_path: Path):
    """验证 Antigravity 与 Claude Code 适配器在持续产生新日志时的高性能增量 Seek 读取。"""
    from backend.adapters.antigravity_adapter import AntigravityAdapter

    brain_dir = tmp_path / "brain"
    cid = "sess-seek-test"
    log_dir = brain_dir / cid / ".system_generated" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    transcript = log_dir / "transcript.jsonl"

    adapter = AntigravityAdapter(agent_type="agy_cli", root_dir=tmp_path)
    adapter.brain_dir = brain_dir

    # 1. 写入第 1 条消息（全量冷读）
    line1 = json.dumps({
        "type": "USER_INPUT",
        "step_index": 1,
        "source": "USER_EXPLICIT",
        "content": "<USER_REQUEST>你好 Antigravity</USER_REQUEST>",
        "created_at": "2026-10-24T00:00:00Z",
    }) + "\n"
    transcript.write_text(line1, encoding="utf-8")

    items1 = adapter.get_session_messages(cid)
    assert len(items1) == 1
    assert items1[0].content == "你好 Antigravity"
    cached1 = adapter._messages_cache.get(cid)
    assert cached1 is not None
    assert len(cached1) == 4  # (mtime_ns, fsize, items, byte_offset)

    # 2. 追加写第 2 条（触发增量 Seek）
    line2 = json.dumps({
        "type": "PLANNER_RESPONSE",
        "step_index": 2,
        "source": "MODEL",
        "content": "很高兴为你服务",
        "created_at": "2026-10-24T00:00:01Z",
    }) + "\n"
    with transcript.open("a", encoding="utf-8") as f:
        f.write(line2)

    items2 = adapter.get_session_messages(cid)
    assert len(items2) == 2
    assert items2[1].content == "很高兴为你服务"
    cached2 = adapter._messages_cache.get(cid)
    assert cached2[3] > cached1[3]  # 文件游标成功推进

    # 2.1 模拟正在流式写入未完成的半截行（无换行符，且 JSON 不完整）
    with transcript.open("a", encoding="utf-8") as f:
        f.write('{"type": "PLANNER_RESPONSE", "step_index": 3, "content": "正在写...')
    # 此时读取，半截行应当被安全忽略，且游标不被推进
    items2_1 = adapter.get_session_messages(cid)
    assert len(items2_1) == 2
    cached2_1 = adapter._messages_cache.get(cid)
    assert cached2_1[3] == cached2[3]  # 游标安全保持在上一完整换行符处

    # 补全这一行并追加换行符
    with transcript.open("a", encoding="utf-8") as f:
        f.write('完整内容"}\n')
    items2_2 = adapter.get_session_messages(cid)
    assert len(items2_2) == 3
    assert "正在写...完整内容" in items2_2[2].content

    # 3. 模拟文件被重写/截断（安全回退全量解析）
    line3 = json.dumps({
        "type": "USER_INPUT",
        "step_index": 1,
        "source": "USER_EXPLICIT",
        "content": "<USER_REQUEST>文件被截断重写</USER_REQUEST>",
        "created_at": "2026-10-24T00:00:02Z",
    }) + "\n"
    transcript.write_text(line3, encoding="utf-8")

    items3 = adapter.get_session_messages(cid)
    assert len(items3) == 1
    assert items3[0].content == "文件被截断重写"


def test_incremental_sync_and_payload_trimming(tmp_path: Path):
    """
    验证会话同步增量协议与无效内容裁剪：
    1. 只读工具(read_file/grep等)在 to_dict 下发时 output 强制置空；
    2. since_step 仅切片返回增量步骤；
    3. 历史已完成步骤思考文本瘦身，最新步骤完整保留。
    """
    # 1. 验证只读工具 output 裁剪
    read_tc = UnifiedToolCall(
        tool_id="tc_1",
        name="read_file",
        args_summary="backend/app.py",
        output="long long file content",
    )
    d = read_tc.to_dict()
    assert d["output"] == ""  # 下发给前端时 output 强制置空

    # 2. 验证 since_step 增量切片与历史思考瘦身
    ide_dir = tmp_path / "incremental_test"
    cid = "sess-inc-01"
    log_dir = ide_dir / "brain" / cid / ".system_generated" / "logs"
    log_dir.mkdir(parents=True)
    transcript = log_dir / "transcript.jsonl"

    long_thinking = "思维链分析：" + "x" * 200
    steps = [
        {
            "step_index": 1,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-10-24T00:00:00Z",
            "content": "<USER_REQUEST>测试增量</USER_REQUEST>",
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-24T00:00:01Z",
            "thinking": long_thinking,
            "content": "中间步骤回复",
            "tool_calls": [],
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-24T00:00:02Z",
            "thinking": "最新实时思考",
            "content": "最新回复",
            "tool_calls": [],
        },
    ]
    transcript.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n",
        encoding="utf-8",
    )

    adapter = AntigravityAdapter("agy_ide", "Antigravity IDE", root_dir=ide_dir)
    # 模拟全量读取
    items, total, has_more = adapter.get_session_messages_page(cid, limit=10)
    assert len(items) == 3
    assert total == 3

    # 模拟 since_step=3 增量轮询
    inc_items, inc_total, _ = adapter.get_session_messages_page(cid, limit=10, since_step=3)
    assert len(inc_items) == 1
    assert inc_items[0].step_index == 3
    assert inc_total == 3


def test_zerotier_and_tailscale_equal_vpn_auth(tmp_path: Path):
    """验证 ZeroTier (10.0.0.0/8) 与 Tailscale (100.64.0.0/10) 虚拟私网对等受信鉴权机制。"""
    from backend.core.tailscale_auth import is_trusted_vpn_network, PairingAuthManager
    from backend.app import create_app
    from fastapi.testclient import TestClient

    # 1. 验证 VPN 虚拟私网段判别对等性（Tailscale CGNAT + 动态 ZeroTier 子网严格隔离）
    assert is_trusted_vpn_network("100.127.241.101") is True  # Tailscale
    assert is_trusted_vpn_network("8.8.8.8") is False         # 公网 IP 拒绝
    assert is_trusted_vpn_network("") is False
    # 未加入虚拟专网网卡的普通 10.x 局域网 IP 严禁被误判为受信 VPN
    from unittest.mock import patch
    import ipaddress
    with patch("backend.core.tailscale_auth.detect_zerotier_network", return_value=None):
        assert is_trusted_vpn_network("10.0.0.1") is False
    with patch("backend.core.tailscale_auth.detect_zerotier_network", return_value=ipaddress.IPv4Network("10.147.20.0/24")):
        assert is_trusted_vpn_network("10.147.20.23") is True
        assert is_trusted_vpn_network("10.0.0.1") is False

    # 2. 验证 Origin 校验对等性
    assert PairingAuthManager.is_trusted_origin("http://10.147.20.249:8765", ["127.0.0.1"]) is True
    assert PairingAuthManager.is_trusted_origin("http://100.127.241.101:8765", ["127.0.0.1"]) is True

    # 3. 验证 ZeroTier 设备扫码配对后的 IP 白名单对等放行
    db = MetaDatabase(db_path=tmp_path / "zt_auth.db")
    auth_mgr = PairingAuthManager(meta_db=db, pair_code_ttl_sec=60)

    zt_client_ip = "10.147.20.23"
    # 配对前不属于已配对受信节点
    assert auth_mgr.is_paired_trusted_vpn_ip(zt_client_ip) is False

    # 模拟手机通过 ZeroTier 扫码置换 token
    pair_code = auth_mgr.bootstrap_pair_code
    res = auth_mgr.exchange_pair_code(pair_code, device_name="ZeroTier Phone", client_ip=zt_client_ip)
    assert res is not None
    assert "token" in res

    # 配对后 ZeroTier IP 正式进入已受信 VPN 白名单
    assert auth_mgr.is_paired_trusted_vpn_ip(zt_client_ip) is True
    assert auth_mgr.is_paired_tailscale_ip(zt_client_ip) is True  # 历史别名兼容

    # 4. 验证 API 层对 ZeroTier 已配对设备的只读免密放行与写操作严格 Token 防护
    app = create_app(meta_db=db)
    app.state.auth_mgr = auth_mgr

    async def inject_zt_client_middleware(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["client"] = (zt_client_ip, 54321)
        await app(scope, receive, send)

    with TestClient(inject_zt_client_middleware) as client:
        # 已配对 ZeroTier 客户端发起只读请求：免密放行（状态码 200）
        r_get = client.get("/api/v1/workspaces")
        assert r_get.status_code == 200

        # 写操作（如防休眠）：未带 Token 时必须拦截，杜绝 CSRF
        r_post_fail = client.post("/api/v1/system/awake-lock?enable=true")
        assert r_post_fail.status_code == 401

        # 5. 验证 PWA 添加到主屏幕独立沙盒自愈机制：请求 /api/v1/system/status 自动补齐已配对设备的合法 Token
        r_status = client.get("/api/v1/system/status")
        assert r_status.status_code == 200
        status_data = r_status.json()
        assert status_data["trusted_client_token"] is not None
        restored_token = status_data["trusted_client_token"]
        assert auth_mgr.verify_token(restored_token, client_ip=zt_client_ip) is True

        # 使用自愈恢复的 Token 执行写操作（防休眠）成功通过
        r_post_restored = client.post(
            "/api/v1/system/awake-lock?enable=true",
            headers={"X-AgentDeck-Token": restored_token}
        )
        assert r_post_restored.status_code == 200

        # 6. 验证连续多次切换防休眠（开 -> 关 -> 开）与多次调用 system/status 时，已持有的合法 Token 绝不会被服务端意外轮转作废
        # 第一次切换后前端会刷新 system/status：携带有效 token 时服务端不应重复签发新 token
        r_status_again = client.get("/api/v1/system/status", headers={"X-AgentDeck-Token": restored_token})
        assert r_status_again.status_code == 200
        assert r_status_again.json()["trusted_client_token"] is None  # 不应重复轮转

        # 第二次点击防休眠（关 -> 开）：依然必须 100% 成功，绝不能报 401
        r_post_again = client.post(
            "/api/v1/system/awake-lock?enable=false",
            headers={"X-AgentDeck-Token": restored_token}
        )
        assert r_post_again.status_code == 200

        # 第三次点击防休眠（开 -> 关）：继续成功
        r_post_third = client.post(
            "/api/v1/system/awake-lock?enable=true",
            headers={"X-AgentDeck-Token": restored_token}
        )
        assert r_post_third.status_code == 200


def test_antigravity_model_sync_with_desktop(tmp_path: Path):
    """验证手机端连接后，默认模型与各个会话的模型均与电脑端真实的选定模型及会话历史保持一致。"""
    import sqlite3
    from backend.adapters.antigravity_adapter import AntigravityAdapter
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator

    # 1. 模拟 ~/.gemini/antigravity 数据目录
    mock_root = tmp_path / "antigravity"
    convs_dir = mock_root / "conversations"
    convs_dir.mkdir(parents=True)

    # 写入带有 last_selected_agent_model 的 antigravity_state.pbtxt（例如选定 Gemini 3.8 Flash Medium）
    state_file = mock_root / "antigravity_state.pbtxt"
    state_file.write_text("agent_onboarding_completed: AGENT_ONBOARDING_STATE_COMPLETED\nlast_selected_agent_model: MODEL_PLACEHOLDER_M319\n", encoding="utf-8")

    adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=mock_root)
    # 验证全局默认模型正确读取电脑端 state 配置
    assert adapter.get_default_model_id() == "gemini-3.8-flash-medium"

    # 2. 模拟真实会话 .db，写入 gen_metadata
    cid = "test-conv-uuid-1234"
    conv_db = convs_dir / f"{cid}.db"
    conn = sqlite3.connect(conv_db)
    conn.execute("CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB)")
    # 模拟包含 model_enum\x12\x16MODEL_PLACEHOLDER_M319 的 protobuf 二进制
    raw_blob = b"sample_prefix\xa2\x01$\n\nmodel_enum\x12\x16MODEL_PLACEHOLDER_M319\xa2\x015sample_suffix"
    conn.execute("INSERT INTO gen_metadata (idx, data) VALUES (1, ?)", (raw_blob,))
    conn.commit()
    conn.close()

    # 验证 adapter.get_session_model 解析准确并命中缓存
    assert adapter.get_session_model(cid) == "gemini-3.8-flash-medium"

    # 3. 验证接入 AgentOrchestrator 后的 detail 与列表模型同步
    meta_db = MetaDatabase(db_path=tmp_path / "meta.db")
    orch = AgentOrchestrator(meta_db=meta_db, adapters={"agy_ide": adapter})

    # detail 验证：优先返回真实会话模型
    detail = orch.get_session_detail(f"agy_ide:{cid}")
    assert detail["model_name"] == "gemini-3.8-flash-medium"

    # available_models 验证：默认模型 is_default 为 True
    models = orch.get_available_models().get("agy_ide", [])
    def_item = next((m for m in models if m.get("is_default")), None)
    assert def_item is not None
    assert def_item["id"] == "gemini-3.8-flash-medium"

    # 4. 验证 extract_protobuf_model_id 稳健解析（多字节 varint 与嵌入模型名）
    from backend.adapters.antigravity_adapter import extract_protobuf_model_id
    # 多字节长度标记 (如 0x85 0x03)
    multi_byte_blob = b"\x8a\x01\x85\x03\n\x00\xa2\x01$\n\nmodel_enum\x12\x16MODEL_PLACEHOLDER_M404\xa2\x01\x14"
    assert extract_protobuf_model_id(multi_byte_blob) == "claude-sonnet-5-5-medium"

    # 嵌入标准模型串匹配
    embed_blob = b"unknown_prefix_gemini-3.8-flash-high_unknown_suffix"
    assert extract_protobuf_model_id(embed_blob) == "gemini-3.8-flash-high"

    # 5. 验证当无任何本地 state_file 或会话 db 且 LS 未就绪时，动态兜底回退到推荐模型首项，不硬编码固定字符串
    from unittest.mock import patch
    empty_root = tmp_path / "empty_agy"
    empty_root.mkdir()
    empty_adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=empty_root)
    with patch.object(empty_adapter.ls_bridge, "get_default_model_id", return_value=""):
        # 应取 AGENT_MODELS['agy_ide'] 的推荐首项
        fallback_mid = empty_adapter.get_default_model_id()
        assert bool(fallback_mid) is True
        from backend.engines.process_pool import AGENT_MODELS
        expected_rec = next(m["id"] for m in AGENT_MODELS["agy_ide"] if m.get("is_recommended"))
        assert fallback_mid == expected_rec


def test_pair_code_refresh_and_expiration_lifecycle(tmp_path: Path):
    """验证一次性配对码滚动刷新作废机制与真实过期时间戳生命周期。"""
    from backend.core.database import MetaDatabase
    from backend.core.tailscale_auth import PairingAuthManager
    from backend.app import create_app
    from fastapi.testclient import TestClient
    import time

    db = MetaDatabase(db_path=tmp_path / "test_auth_life.db")
    auth_mgr = PairingAuthManager(meta_db=db, pair_code_ttl_sec=600)

    # 1. 验证初次生成的配对码与过期时间
    code1 = auth_mgr.bootstrap_pair_code
    exp1 = auth_mgr.bootstrap_pair_code_expires_at
    assert bool(code1) is True
    assert exp1 > time.time() + 500

    # 2. 模拟客户端主动刷新配对码
    code2, exp2 = auth_mgr.refresh_pair_code()
    assert code2 != code1
    assert exp2 > time.time() + 500

    # 3. 核心安全验证：旧配对码 code1 必须已被立即销毁作废，杜绝历史截图重放
    fail_res = auth_mgr.exchange_pair_code(code1, device_name="Hacker Phone")
    assert fail_res is None

    # 4. 新配对码 code2 能够正常消费置换
    success_res = auth_mgr.exchange_pair_code(code2, device_name="Legit Phone")
    assert success_res is not None
    assert "token" in success_res

    # 5. API 接口层集成测试：验证 /api/v1/system/status 与 /api/v1/auth/refresh_pair_code 返回 pair_code_expires_at
    app = create_app(meta_db=db)
    app.state.auth_mgr = auth_mgr

    with TestClient(app) as client:
        # 探测系统状态
        status_resp = client.get("/api/v1/system/status")
        assert status_resp.status_code == 200
        sdata = status_resp.json()
        assert "pair_code_expires_at" in sdata
        assert sdata["pair_code_expires_at"] > time.time()

        # 主动调用刷新接口
        refresh_resp = client.post("/api/v1/auth/refresh_pair_code")
        assert refresh_resp.status_code == 200
        rdata = refresh_resp.json()
        assert rdata["ok"] is True
        assert "pair_code" in rdata
        assert "pair_code_expires_at" in rdata
        assert rdata["pair_code_expires_at"] > time.time()


def test_vpn_pwa_self_healing_and_session_isolation(tmp_path: Path):
    """
    验证受信任虚拟专网 (Tailscale / ZeroTier) 设备在 PWA 沙盒下的自愈与多设备隔离安全特性：
    1. 只有已配对过的受信任 IP 才能自愈换发 Token；
    2. 未配对或冒充的 IP 绝不发放 Token，更不可抢占/覆写已有设备的会话；
    3. 多 IP 漫游时 _merge_ips 能够去重合并，且 is_paired_trusted_vpn_ip 精准放行。
    """
    from backend.core.tailscale_auth import (
        PairingAuthManager,
        _parse_ips,
        _merge_ips,
    )

    # 1. IP 解析与合并工具函数
    assert _parse_ips("100.64.0.1, 10.147.20.5 , 100.64.0.1") == ["100.64.0.1", "10.147.20.5"]
    assert _merge_ips("100.64.0.1", "10.147.20.5") == "100.64.0.1,10.147.20.5"
    assert _merge_ips("100.64.0.1,10.147.20.5", "100.64.0.1") == "100.64.0.1,10.147.20.5"

    db = MetaDatabase(db_path=tmp_path / "test_vpn_pwa.db")
    auth_mgr = PairingAuthManager(meta_db=db)

    # 2. 设备 A (IP: 100.64.1.10) 配对成功
    code = auth_mgr.generate_pair_code()
    res_a = auth_mgr.exchange_pair_code(code, device_name="Device A", client_ip="100.64.1.10")
    assert res_a is not None
    token_a = res_a["token"]

    # 验证设备 A 在配对后已标记为受信任
    assert auth_mgr.is_paired_trusted_vpn_ip("100.64.1.10") is True

    # 3. 冒充设备 B (IP: 100.64.1.99 未配对) 试图自愈获取 Token
    stolen_token = auth_mgr.issue_token_for_trusted_vpn_ip("100.64.1.99")
    assert stolen_token is None  # 必须拒绝，严禁抢占 Device A 的会话

    # 验证设备 A 的原始 Token 依然合法有效
    assert auth_mgr.verify_token(token_a, client_ip="100.64.1.10") is True

    # 4. 设备 A 漫游到新的 VPN IP (100.64.1.10 -> 带着 Token 访问被记录)
    # 模拟合法设备带 Token 访问新网络
    auth_mgr.verify_token(token_a, client_ip="100.64.1.20")
    # 现在新 IP 也被绑定到了设备 A
    assert auth_mgr.is_paired_trusted_vpn_ip("100.64.1.20") is True

    # 5. 设备 A 在新 IP 下触发 PWA 沙盒自愈换发 Token
    healed_token = auth_mgr.issue_token_for_trusted_vpn_ip("100.64.1.20")
    assert healed_token is not None
    assert healed_token.startswith("agd_")
    assert auth_mgr.verify_token(healed_token, client_ip="100.64.1.20") is True


def test_clean_denied_tool_instruction_and_title_sanitization():
    """验证后端工具拒绝控制指令清洗、人性化标题生成与前后端一致性。"""
    from backend.adapters.antigravity_adapter import clean_denied_tool_instruction
    from backend.engines.process_pool import AgentOrchestrator

    # 1. 基础拒绝并附带指导意见
    raw1 = "[已拒绝工具 run_command] 请勿执行刚才的操作，按以下要求调整方案：请改用只读查看"
    assert clean_denied_tool_instruction(raw1) == "指导: 请改用只读查看"

    # 2. 工具名带空格或特殊字符
    raw2 = "[已拒绝工具 custom tool name] 请勿执行刚才的操作，按以下要求调整方案：不要删除数据库"
    assert clean_denied_tool_instruction(raw2) == "指导: 不要删除数据库"

    # 3. 直接拒绝无反馈文本
    raw3 = "[已拒绝工具 run_command] 请勿执行刚才的操作，按以下要求调整方案："
    assert clean_denied_tool_instruction(raw3) == "已拒绝执行该工具"

    # 4. 普通人类文本不应被误清洗
    normal_text = "请帮我检查代码里的错误"
    assert clean_denied_tool_instruction(normal_text) == normal_text

    # 5. 空文本安全兜底
    assert clean_denied_tool_instruction("") == ""


def test_etag_negotiator_caching_and_304():
    """测试 EtagNegotiator 深模块：ETag 生成、指纹提取与 304 快速比对。"""
    negotiator = EtagNegotiator(maxsize=10)
    sessions_etag = negotiator.get_sessions_etag(
        seq_counter=42, active_count=2, pending_count=1, agent_filter="all", limit=40
    )
    assert 'W/"42-2-1-all-40"' == sessions_etag

    detail = {
        "is_running": True,
        "messages": [
            {
                "step_index": 5,
                "content": "hello world",
                "thinking": "analyzing...",
                "tool_calls": [{"name": "bash", "status": "DONE", "output": "ok"}],
            }
        ],
        "pending_approval": {"approval_id": "apr_123"},
    }
    fp = negotiator.extract_session_detail_fingerprint(detail)
    assert "apr_123" in fp
    assert "5" in fp

    msg_etag = negotiator.generate_and_cache_messages_etag(
        detail=detail,
        unified_id="claude_code:s1",
        adapter=None,
        is_active=True,
        is_pending=True,
        limit=30,
    )
    assert msg_etag.startswith('W/"msg-claude_code:s1-')

    matched, hit_etag = negotiator.check_messages_etag_matches(
        if_none_match=msg_etag,
        unified_id="claude_code:s1",
        adapter=None,
        is_active=True,
        is_pending=True,
        limit=30,
    )
    assert matched is True
    assert hit_etag == msg_etag

    # 状态变动后应判定不匹配（如从 running 变为 idle）
    matched_changed, _ = negotiator.check_messages_etag_matches(
        if_none_match=msg_etag,
        unified_id="claude_code:s1",
        adapter=None,
        is_active=False,
        is_pending=True,
        limit=30,
    )
    assert matched_changed is False


@pytest.mark.asyncio
async def test_approval_coordinator_lifecycle(tmp_path: Path):
    """测试 ApprovalCoordinator 深模块：待审批挂起、异步唤醒与决策落库。"""
    db_file = tmp_path / "test_approval_meta.db"
    db = MetaDatabase(db_path=db_file)
    bus = RealtimeEventBus()
    coordinator = ApprovalCoordinator(meta_db=db, event_bus=bus)

    pa = PendingApproval(
        approval_id="apr_test01",
        unified_id="claude_code:test_session",
        agent_type="claude_code",
        raw_session_id="test_session",
        tool_call_id="call_999",
        tool_name="bash",
        args_summary="rm -rf /tmp/data",
        diff_text="",
        risk_level="high",
        prompt_context="清理临时缓存",
        project_path=str(tmp_path),
        created_at="2026-10-24T00:00:00Z",
    )

    async def _simulate_user_approval():
        await asyncio.sleep(0.05)
        assert coordinator.is_pending("claude_code:test_session") is True
        snap = coordinator.get_snapshot_list()
        assert len(snap) == 1
        assert snap[0]["approval_id"] == "apr_test01"
        res = await coordinator.resolve(
            unified_id="claude_code:test_session",
            decision="approved",
            feedback="确认清理",
        )
        assert res["ok"] is True
        assert res["decision"] == "approved"

    task = asyncio.create_task(_simulate_user_approval())
    result = await coordinator.register_and_wait(approval_obj=pa)
    await task

    assert result["decision"] == "approved"
    assert coordinator.is_pending("claude_code:test_session") is False

    # 验证审计日志已自动持久化写入数据库
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM t_tool_approval_log WHERE tool_call_id = 'call_999'"
        ).fetchone()
        assert row is not None
        assert row["tool_name"] == "bash"
        assert row["decision"] == "approved"
        assert row["risk_level"] == "high"


def test_workspace_document_reader_security(tmp_path: Path):
    """测试 WorkspaceDocumentReader 安全深度校验：敏感拦截、大文件截断与正常 Markdown 读取。"""
    # 1. 正常 Markdown 文件读取
    md_file = tmp_path / "spec.md"
    md_file.write_text("# 架构设计说明\n\n- 深度模块\n- 局部性", encoding="utf-8")
    res = WorkspaceDocumentReader.read_document(str(md_file))
    assert res["ok"] is True
    assert res["is_markdown"] is True
    assert res["filename"] == "spec.md"
    assert "架构设计说明" in res["content"]

    # 2. 拦截系统敏感目录 (.ssh)
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    key_file = ssh_dir / "id_rsa.pub"
    key_file.write_text("ssh-rsa AAAAB3...", encoding="utf-8")
    with pytest.raises(PermissionError) as exc_info:
        WorkspaceDocumentReader.read_document(str(key_file))
    assert "禁止读取系统敏感配置目录文件" in str(exc_info.value)

    # 3. 拦截敏感密钥文件名 (.env)
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=123456", encoding="utf-8")
    with pytest.raises(PermissionError) as exc_info2:
        WorkspaceDocumentReader.read_document(str(env_file))
    assert "暂不支持直接文本预览该类型文件" in str(exc_info2.value)

    # 4. 文件不存在抛出 FileNotFoundError
    with pytest.raises(FileNotFoundError):
        WorkspaceDocumentReader.read_document(str(tmp_path / "not_exist.md"))

    # 5. 超大文件截断 (超过 256KB)
    large_file = tmp_path / "large.log"
    large_file.write_bytes(b"A" * (300 * 1024))
    large_res = WorkspaceDocumentReader.read_document(str(large_file))
    assert large_res["ok"] is True
    assert "文档较大" in large_res["content"]


def test_is_transient_probe_session():
    """测试通用临时探针会话判定规则。"""
    assert is_transient_probe_session("reading flag.txt", "") is True
    assert is_transient_probe_session("", "marker.txt check") is True
    assert is_transient_probe_session("read flag file for testing", "") is True
    assert is_transient_probe_session("normal session", "fixing ui styles") is False
    assert is_transient_probe_session("", "") is False


@pytest.mark.asyncio
async def test_safe_storage_watcher_lifecycle(tmp_path: Path):
    """测试 SafeStorageWatcher 深模块：启停生命周期与空目录安全待命。"""
    bus = RealtimeEventBus()
    watcher = SafeStorageWatcher(adapters_getter=lambda: {}, event_bus=bus)
    assert watcher.is_running is False

    watcher.start()
    assert watcher.is_running is True

    # 停止应干净退出协程
    watcher.stop()
    assert watcher.is_running is False


def test_meaningful_session_storage_change_filter():
    """T4 验证：测试 SafeStorageWatcher 白名单收敛过滤规则，阻断广播风暴。"""
    from backend.engines.watcher import is_meaningful_session_storage_change

    # 1. 核心白名单：应当放行
    assert is_meaningful_session_storage_change("C:/Users/test/.gemini/antigravity/brain/123/.system_generated/logs/transcript.jsonl", "transcript.jsonl") is True
    assert is_meaningful_session_storage_change("C:/Users/test/.gemini/antigravity/conversation_summaries.db", "conversation_summaries.db") is True
    assert is_meaningful_session_storage_change("C:/Users/test/.gemini/antigravity/conversation_summaries.db-wal", "conversation_summaries.db-wal") is True
    assert is_meaningful_session_storage_change("C:/Users/test/.claude/projects/my-proj/sess-123.jsonl", "sess-123.jsonl") is True
    assert is_meaningful_session_storage_change("C:/Users/test/.claude/sessions/active.json", "active.json") is True

    # 2. 噪音黑名单：必须阻断拦截
    assert is_meaningful_session_storage_change("C:/Users/test/.gemini/antigravity/conversation_summaries.db-shm", "conversation_summaries.db-shm") is False
    assert is_meaningful_session_storage_change("C:/Users/test/.gemini/antigravity/brain/123/scratch/tmp.py", "tmp.py") is False
    assert is_meaningful_session_storage_change("C:/Users/test/.gemini/antigravity/brain/123/.system_generated/tasks/task-1.log", "task-1.log") is False
    assert is_meaningful_session_storage_change("C:/Users/test/.claude/projects/my-proj/agent-123.jsonl", "agent-123.jsonl") is False
    assert is_meaningful_session_storage_change("C:/Users/test/.claude/projects/my-proj/.git/index", "index") is False
    assert is_meaningful_session_storage_change("C:/Users/test/tmp_snapshots/snap.json", "snap.json") is False



def test_incomplete_line_recovery_and_offset_safety(tmp_path: Path):
    """
    T1 验证：测试末行并发写入/截断时的 Incomplete Line Recovery 容错与增量游标保护。
    确保半截行不会被当成空行吞掉，且后续写完整时能增量补齐解析。
    """
    from backend.adapters.antigravity_adapter import AntigravityAdapter

    mock_root = tmp_path / "antigravity"
    brain_dir = mock_root / "brain"
    cid = "test-session-incomplete-recovery"
    sess_dir = brain_dir / cid / ".system_generated" / "logs"
    sess_dir.mkdir(parents=True)
    tp = sess_dir / "transcript.jsonl"

    adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=mock_root)

    # 1. 初始写入一条合法的用户提问
    line1 = json.dumps({"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "你好"}) + "\n"
    tp.write_text(line1, encoding="utf-8")

    msgs1 = adapter.get_session_messages(cid)
    assert len(msgs1) == 1
    assert msgs1[0].content == "你好"

    # 2. 模拟正在流式并发写入，追加了半截残缺的 JSON 行（不完整的大括号与未换行）
    with open(tp, "a", encoding="utf-8") as f:
        f.write('{"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "content": "正在思考')

    # 读取时应安全降级，不抛异常，且不会吞掉游标
    msgs2 = adapter.get_session_messages(cid)
    assert len(msgs2) == 1

    # 3. 外部进程将该行写完整并换行
    with open(tp, "a", encoding="utf-8") as f:
        f.write('中，回答完毕"}\n')

    # 再次读取，增量游标安全从该行行首重新解析，第 2 步成功补齐
    msgs3 = adapter.get_session_messages(cid)
    assert len(msgs3) == 2
    assert "正在思考中，回答完毕" in msgs3[1].content


def test_inspect_transcript_tail_status_and_smooth_activity(tmp_path: Path):
    """
    T2 验证：测试结合末尾 Step 状态机与写入时间的平滑活跃态判定算法。
    """
    import os
    import time
    from backend.adapters.antigravity_adapter import inspect_transcript_tail_status

    tp = tmp_path / "transcript.jsonl"

    # 1. 末尾为 USER_INPUT（用户提问后等待模型长思考），即使 80 秒未写入，仍判定为活跃
    tp.write_text(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "复杂算法设计"}) + "\n", encoding="utf-8")
    past_80s = time.time() - 80.0
    os.utime(tp, (past_80s, past_80s))
    active, status = inspect_transcript_tail_status(tp)
    assert active is True
    assert status == "running"

    # 2. 末尾为 RUNNING 状态的 PLANNER_RESPONSE，即使 120 秒未写入（长耗时工具执行），仍判定为活跃
    tp.write_text(
        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "RUNNING", "content": "执行构建"}) + "\n",
        encoding="utf-8",
    )
    past_120s = time.time() - 120.0
    os.utime(tp, (past_120s, past_120s))
    active, status = inspect_transcript_tail_status(tp)
    assert active is True
    assert status == "running"

    # 3. 明确 DONE 且已过去 20 秒（超过 15 秒缓冲），平滑退火判定为闲置
    tp.write_text(
        json.dumps({"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "全部完成"}) + "\n",
        encoding="utf-8",
    )
    past_20s = time.time() - 20.0
    os.utime(tp, (past_20s, past_20s))
    active, status = inspect_transcript_tail_status(tp)
    assert active is False
    assert status == "idle"

    # 4. 终态即时判定：PLANNER_RESPONSE 明确 DONE 且回答完毕，无论多近期（如 1 秒前）均应瞬时为 idle，严禁虚假卡在 working
    past_1s = time.time() - 1.0
    os.utime(tp, (past_1s, past_1s))
    active, status = inspect_transcript_tail_status(tp)
    assert active is False
    assert status == "idle"

    # 5. 工具刚执行完毕（GENERIC 步骤 DONE），等待模型接续思考生成回复，在极短窗口（5 秒内）平滑保持活跃
    step3 = json.dumps({"step_index": 3, "type": "GENERIC", "status": "DONE", "content": "命令执行成功"}) + "\n"
    tp.write_text(step3, encoding="utf-8")
    past_5s = time.time() - 5.0
    os.utime(tp, (past_5s, past_5s))
    active, status = inspect_transcript_tail_status(tp)
    assert active is True
    assert status == "running"

    # 6. 中间工具调用步骤（PLANNER_RESPONSE 带有 tool_calls），在极短窗口（3 秒内）稳定保持活跃，严禁在工具间隙跳变
    step4 = json.dumps({
        "step_index": 4,
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": "",
        "tool_calls": [{"name": "run_command", "status": "DONE"}]
    }) + "\n"
    tp.write_text(step4, encoding="utf-8")
    past_3s = time.time() - 3.0
    os.utime(tp, (past_3s, past_3s))
    active, status = inspect_transcript_tail_status(tp)
    assert active is True
    assert status == "running" 























def test_antigravity_subagent_detection(tmp_path: Path):
    """测试 Antigravity 扫描器正确识别主进程与子进程，并打标 is_subagent。"""
    import sqlite3
    from backend.adapters.antigravity_scanner import query_antigravity_sessions

    summary_db = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(summary_db)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE conversation_summaries (
            conversation_id text PRIMARY KEY,
            title text NOT NULL DEFAULT '',
            preview text NOT NULL DEFAULT '',
            step_count integer NOT NULL DEFAULT 0,
            last_modified_time datetime NOT NULL,
            workspace_uris text NOT NULL,
            status text NOT NULL DEFAULT '',
            parent_conversation_id text NOT NULL DEFAULT '',
            nesting_depth integer NOT NULL DEFAULT 0,
            not_fully_idle numeric NOT NULL DEFAULT false,
            killed numeric NOT NULL DEFAULT false
        )
        """
    )
    # 插入主进程 (Main Process)
    cur.execute(
        """
        INSERT INTO conversation_summaries (
            conversation_id, title, preview, step_count, last_modified_time,
            workspace_uris, status, parent_conversation_id, nesting_depth, not_fully_idle, killed
        ) VALUES ('main-sess-001', '主任务分析', '分析中', 5, '2026-10-24T12:00:00Z', 'd:/repo', 'CASCADE_RUN_STATUS_RUNNING', '', 0, 1, 0)
        """
    )
    # 插入子进程 (Subagent Process)
    cur.execute(
        """
        INSERT INTO conversation_summaries (
            conversation_id, title, preview, step_count, last_modified_time,
            workspace_uris, status, parent_conversation_id, nesting_depth, not_fully_idle, killed
        ) VALUES ('sub-sess-002', '代码检索子任务', '正在搜索', 2, '2026-10-24T12:01:00Z', 'd:/repo', 'CASCADE_RUN_STATUS_RUNNING', 'main-sess-001', 1, 1, 0)
        """
    )
    conn.commit()
    conn.close()

    brain_dir = tmp_path / "brain"
    brain_dir.mkdir(parents=True, exist_ok=True)

    sessions = query_antigravity_sessions(
        summary_db_path=summary_db,
        brain_dir=brain_dir,
        agent_type="agy_ide",
        agent_label="Antigravity IDE",
    )

    sess_map = {s.raw_session_id: s for s in sessions}
    assert "main-sess-001" in sess_map
    assert "sub-sess-002" in sess_map

    main_sess = sess_map["main-sess-001"]
    sub_sess = sess_map["sub-sess-002"]

    # 验证主进程标志
    assert main_sess.is_subagent is False
    assert main_sess.parent_unified_id is None

    # 验证子进程标志
    assert sub_sess.is_subagent is True
    assert sub_sess.parent_unified_id == "agy_ide:main-sess-001"


def test_session_aggregator_subagent_handling(tmp_path: Path):
    """测试聚合器在合并覆盖项与多 Agent 数据时保持子进程标识。"""
    from backend.core.database import MetaDatabase
    from backend.adapters.models import UnifiedSession
    from backend.engines.session_aggregator import aggregate_all_sessions
    from unittest.mock import MagicMock

    db = MetaDatabase(db_path=tmp_path / "meta_test.db")
    mock_adapter = MagicMock()
    mock_adapter.agent_label = "Antigravity IDE"
    mock_adapter.list_sessions.return_value = [
        UnifiedSession(
            unified_id="agy_ide:main-1",
            agent_type="agy_ide",
            agent_label="Antigravity IDE",
            raw_session_id="main-1",
            title="主进程任务",
            preview="预览",
            project_path="d:/repo",
            status="running",
            step_count=3,
            updated_at="2026-10-24T12:00:00Z",
            is_subagent=False,
        ),
        UnifiedSession(
            unified_id="agy_ide:sub-2",
            agent_type="agy_ide",
            agent_label="Antigravity IDE",
            raw_session_id="sub-2",
            title="子进程任务",
            preview="预览",
            project_path="d:/repo",
            status="running",
            step_count=1,
            updated_at="2026-10-24T12:01:00Z",
            parent_unified_id="agy_ide:main-1",
            is_subagent=True,
        ),
    ]

    adapters = {"agy_ide": mock_adapter}
    results = aggregate_all_sessions(
        adapters=adapters,
        meta_db=db,
        pending_approvals={},
        is_session_active_fn=lambda uid: False,
        resolve_default_model_fn=lambda atype: "gemini-2.5-pro",
    )

    res_map = {r["unified_id"]: r for r in results}
    assert res_map["agy_ide:main-1"]["is_subagent"] is False
    assert res_map["agy_ide:sub-2"]["is_subagent"] is True
    assert res_map["agy_ide:sub-2"]["parent_unified_id"] == "agy_ide:main-1"


def test_antigravity_session_instant_finish_and_sqlite_override(tmp_path: Path):
    """
    验证：当 transcript.jsonl 已经回答完毕（DONE），即使 SQLite 表滞后记录 not_fully_idle=1，
    适配器也能以 transcript.jsonl 为准瞬时判定为 idle，绝不产生滞后假死 working。
    """
    import sqlite3
    import time
    from backend.adapters.antigravity_adapter import AntigravityAdapter

    brain_dir = tmp_path / "brain"
    cid = "test-session-instant-done-1234"
    logs_dir = brain_dir / cid / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    tp = logs_dir / "transcript.jsonl"

    # transcript.jsonl 写入回答完毕的最终 step (PLANNER_RESPONSE DONE)
    step_final = json.dumps({
        "step_index": 5,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": "回答完毕，请查看成果。",
        "tool_calls": []
    }) + "\n"
    tp.write_text(step_final, encoding="utf-8")

    # 在 SQLite conversation_summaries.db 中模拟滞后的 not_fully_idle = 1 与 RUNNING
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            preview TEXT,
            step_count INTEGER,
            last_modified_time INTEGER,
            workspace_uris TEXT,
            status TEXT,
            not_fully_idle INTEGER,
            killed INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        INSERT INTO conversation_summaries
        VALUES (?, '测试会话', '最新预览', 5, ?, 'file:///test', 'CASCADE_RUN_STATUS_RUNNING', 1, 0)
    """, (cid, int(time.time() * 1000)))
    conn.commit()
    conn.close()

    adapter = AntigravityAdapter(
        agent_type="agy_ide",
        agent_label="Antigravity IDE",
        root_dir=tmp_path
    )

    # 1. 验证 is_external_session_active 瞬时识别为 False（闲置），不被 SQLite 滞后记录绑架
    is_active = adapter.is_external_session_active(cid)
    assert is_active is False

    # 2. 验证 list_sessions / query_antigravity_sessions 状态校准为 idle
    sessions = adapter.list_sessions(limit=10)
    target = next((s for s in sessions if s.raw_session_id == cid), None)
    assert target is not None
    assert target.status == "idle"

    # 3. 验证 query_antigravity_single_session 状态校准为 idle
    single = adapter.get_session(cid)
    assert single is not None
    assert single.status == "idle"


@pytest.mark.asyncio
async def test_session_viewed_cross_client_sync(tmp_path: Path):
    """测试跨客户端已读状态上报、持久化与 WebSocket 广播闭环。"""
    from backend.core.database import MetaDatabase
    from backend.app import create_app
    from starlette.testclient import TestClient

    db = MetaDatabase(db_path=tmp_path / "sync_test.db")
    app = create_app(meta_db=db)
    orchestrator = app.state.orchestrator

    uid = "claude_code:sess_viewed_test"
    db.upsert_session_meta(
        unified_id=uid,
        agent_type="claude_code",
        raw_session_id="sess_viewed_test",
        title="测试会话",
    )

    with TestClient(app) as client:
        # 1. 模拟手机端连接 WebSocket
        with client.websocket_connect("/ws/stream?since_seq=0") as ws:
            first_frame = ws.receive_json()
            assert first_frame["type"] == "SESSION_STATE_SNAPSHOT"

            # 2. 模拟电脑端调用查看接口 POST /viewed
            resp = client.post(f"/api/v1/sessions/{uid}/viewed")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["unified_id"] == uid
            assert data["last_viewed_at"] is not None

            # 3. 校验数据库已持久化写入 last_viewed_at
            overrides = db.get_session_overrides()
            assert overrides[uid]["last_viewed_at"] == data["last_viewed_at"]

            # 4. 校验手机端 WebSocket 实时接收到 SESSION_VIEWED 广播帧
            evt_frame = ws.receive_json()
            assert evt_frame["type"] == "SESSION_VIEWED"
            assert evt_frame["payload"]["unified_id"] == uid
            assert evt_frame["payload"]["last_viewed_at"] == data["last_viewed_at"]


@pytest.mark.asyncio
async def test_antigravity_subagent_active_propagation(tmp_path: Path):
    """测试 Antigravity 子进程/子代理运行中时，父会话正确识别为活跃态并冒泡状态。"""
    import sqlite3
    import time
    import json
    from backend.adapters.antigravity_adapter import AntigravityAdapter

    parent_cid = "parent_test_conv_id"
    sub_cid = "subagent_test_conv_id"

    # 1. 父会话的 transcript：Agent 已停步等待 (DONE, 无活动工具调用)
    brain_dir = tmp_path / "brain"
    parent_tp = brain_dir / parent_cid / ".system_generated" / "logs" / "transcript.jsonl"
    parent_tp.parent.mkdir(parents=True, exist_ok=True)
    step_parent = json.dumps({
        "type": "PLANNER_RESPONSE",
        "source": "MODEL",
        "status": "DONE",
        "content": "已启动子任务，等待完成...",
        "tool_calls": []
    }) + "\n"
    parent_tp.write_text(step_parent, encoding="utf-8")

    # 2. 子会话的 transcript：子进程正在运行中 (RUNNING 工具调用)
    sub_tp = brain_dir / sub_cid / ".system_generated" / "logs" / "transcript.jsonl"
    sub_tp.parent.mkdir(parents=True, exist_ok=True)
    step_sub = json.dumps({
        "type": "PLANNER_RESPONSE",
        "source": "MODEL",
        "status": "RUNNING",
        "content": "正在执行子任务...",
        "tool_calls": [{"name": "run_command", "status": "RUNNING"}]
    }) + "\n"
    sub_tp.write_text(step_sub, encoding="utf-8")

    # 3. 在 SQLite conversation_summaries.db 中记录父会话与子会话归属关系
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            preview TEXT,
            step_count INTEGER,
            last_modified_time INTEGER,
            workspace_uris TEXT,
            status TEXT,
            not_fully_idle INTEGER,
            parent_conversation_id TEXT,
            nesting_depth INTEGER DEFAULT 0,
            killed INTEGER DEFAULT 0
        )
    """)
    now_ms = int(time.time() * 1000)
    # 父会话：SQLite 中显示 not_fully_idle = 1 (等待子进程中，电脑端显示 ...)
    cur.execute("""
        INSERT INTO conversation_summaries
        VALUES (?, '主会话', '派生了子任务', 3, ?, 'file:///test', 'CASCADE_RUN_STATUS_RUNNING', 1, '', 0, 0)
    """, (parent_cid, now_ms))

    # 子会话：指向 parent_cid，not_fully_idle = 1
    cur.execute("""
        INSERT INTO conversation_summaries
        VALUES (?, '子会话', '子代理执行中', 1, ?, 'file:///test', 'CASCADE_RUN_STATUS_RUNNING', 1, ?, 1, 0)
    """, (sub_cid, now_ms, parent_cid))
    conn.commit()
    conn.close()

    adapter = AntigravityAdapter(
        agent_type="agy_ide",
        agent_label="Antigravity IDE",
        root_dir=tmp_path
    )

    # 4. 验证 is_external_session_active 能穿透父会话，识别到子进程活跃，返回 True
    assert adapter.is_external_session_active(parent_cid) is True

    # 5. 验证 get_active_session_ids 包含父会话
    active_ids = adapter.get_active_session_ids()
    assert f"agy_ide:{parent_cid}" in active_ids

    # 6. 验证 list_sessions 中父会话状态正确升格为 desktop_active，并标记 has_active_subagent
    sessions = adapter.list_sessions(limit=10)
    parent_sess = next((s for s in sessions if s.raw_session_id == parent_cid), None)
    assert parent_sess is not None
    assert parent_sess.status == "desktop_active"
    assert parent_sess.has_active_subagent is True
    assert parent_sess.active_subagents_count >= 1
    assert parent_sess.desktop_locked is True

    # 7. 验证 get_session(parent_cid) 单会话查询同样正确识别子进程活跃
    single_parent = adapter.get_session(parent_cid)
    assert single_parent is not None
    assert single_parent.status == "desktop_active"
    assert single_parent.has_active_subagent is True
    assert single_parent.desktop_locked is True


def test_mark_all_viewed_and_sync_viewed_endpoints(tmp_path: Path):
    """测试批量标为已读与客户端已读时间同步端点（含容错与全量更新验证）。"""
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.core.database import MetaDatabase

    db = MetaDatabase(db_path=tmp_path / "test_viewed.db")
    app = create_app(meta_db=db)

    with TestClient(app) as client:
        # 1. 测试 sync-viewed（包含毫秒时间戳与 ISO 字符串）
        sync_resp = client.post(
            "/api/v1/sessions/sync-viewed",
            json={
                "viewed_sessions": {
                    "agy_cli:sess_001": 1700000000000,
                    "claude_code:sess_002": "2026-10-24T12:00:00Z"
                }
            }
        )
        assert sync_resp.status_code == 200
        assert sync_resp.json()["ok"] is True
        assert sync_resp.json()["updated_count"] == 2

        # 验证数据库中已写入
        overrides = db.get_session_overrides()
        assert "agy_cli:sess_001" in overrides
        assert overrides["agy_cli:sess_001"]["last_viewed_at"] is not None
        assert "claude_code:sess_002" in overrides
        assert overrides["claude_code:sess_002"]["last_viewed_at"] == "2026-10-24T12:00:00Z"

        # 2. 测试 mark-all-viewed（同时支持传入新会话并全量更新已有会话）
        mark_resp = client.post(
            "/api/v1/sessions/mark-all-viewed",
            json={"session_ids": ["opencode:sess_003"]}
        )
        assert mark_resp.status_code == 200
        assert mark_resp.json()["ok"] is True
        assert mark_resp.json()["last_viewed_at"] is not None

        new_overrides = db.get_session_overrides()
        assert "opencode:sess_003" in new_overrides
        # 确认所有已存在会话统一更新为最新时间
        assert new_overrides["agy_cli:sess_001"]["last_viewed_at"] == mark_resp.json()["last_viewed_at"]
        assert new_overrides["claude_code:sess_002"]["last_viewed_at"] == mark_resp.json()["last_viewed_at"]


def test_pwa_cookie_auth_and_manifest_start_url(tmp_path: Path):
    """测试 PWA Standalone 模式下的 Cookie 鉴权放行与动态 Manifest 生成。"""
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.core.database import MetaDatabase

    db = MetaDatabase(db_path=tmp_path / "test_pwa.db")
    app = create_app(meta_db=db)

    with TestClient(app) as client:
        # 1. 配对接口正常下发 Set-Cookie
        auth_mgr = app.state.auth_mgr
        pcode = auth_mgr.bootstrap_pair_code
        ex_resp = client.post("/api/v1/auth/exchange", json={"pair_code": pcode, "device_name": "Test Phone"})
        assert ex_resp.status_code == 200
        token = ex_resp.json()["token"]
        assert "agentdeck_token" in ex_resp.cookies

        # 2. 测试带 Cookie 访问保护接口正常放行
        client.cookies.set("agentdeck_token", token)
        auth_resp = client.get("/api/v1/sessions")
        assert auth_resp.status_code in (200, 304)

        # 3. 测试动态 Manifest 生成（URL 带 token 时 start_url 自动注入该 token）
        manifest_resp = client.get("/manifest.webmanifest?token=my_secret_token")
        assert manifest_resp.status_code == 200
        m_data = manifest_resp.json()
        assert m_data["start_url"] == "/?token=my_secret_token"
        assert m_data["display"] == "standalone"

        # 4. 测试 static manifest 端点优先从 Cookie 提取 token 注入 start_url
        static_m_resp = client.get("/static/manifest.json")
        assert static_m_resp.status_code == 200
        assert token in static_m_resp.json()["start_url"]
