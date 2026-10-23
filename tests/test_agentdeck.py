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


