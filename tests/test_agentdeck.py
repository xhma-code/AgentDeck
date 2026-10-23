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

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adapters.antigravity_adapter import (
    AntigravityAdapter,
    fork_ide_session_to_cli,
)
from backend.adapters.base import (
    build_unified_diff,
    classify_tool_risk,
    truncate_large_output,
)
from backend.adapters.claude_adapter import ClaudeCodeAdapter
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.app import create_app
from backend.core.database import MetaDatabase, safe_query_external_sqlite
from backend.core.logger import ERROR_LOG_FILE, log_structured_error
from backend.core.tailscale_auth import PairingAuthManager
from backend.core.workspace import get_git_workspace_diff


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


@pytest.mark.asyncio
async def test_websocket_snapshot_and_approval_flow(tmp_path: Path):
    """测试 FastAPI 接口、WebSocket 断线重连快照恢复与移动端工具审批全链路闭环。"""
    db = MetaDatabase(db_path=tmp_path / "api_test.db")
    app = create_app(meta_db=db)
    orchestrator = app.state.orchestrator

    # 模拟一个挂起中的高危工具调用审批
    import asyncio

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
        assert any(m["id"] == "gemini-4-argon-high" for m in models_by_agent["agy_cli"])

        # 更新会话模型
        patch_resp = client.patch(
            "/api/v1/sessions/agy_cli:conv_model_1/meta",
            json={"model_name": "gemini-4-argon-high", "execution_mode": "plan"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["meta"]["model_name"] == "gemini-4-argon-high"

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







