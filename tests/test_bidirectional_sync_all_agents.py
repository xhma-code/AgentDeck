import os
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.adapters.antigravity_adapter import AntigravityAdapter
from backend.adapters.antigravity_ls_bridge import AntigravityLanguageServerBridge
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.adapters.opencode_bridge import OpenCodeHttpBridge
from backend.adapters.claude_adapter import ClaudeCodeAdapter
from backend.adapters.claude_scanner import parse_claude_session_file


def test_antigravity_full_bidirectional_sync(tmp_path: Path):
    """测试 Antigravity (IDE/CLI) 发送、中断、重命名双向同步功能。"""
    db_path = tmp_path / "conversation_summaries.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            preview TEXT,
            raw_summary BLOB
        )
        """
    )
    raw_proto = b"\x0a\x08old_title\x10\x01"
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
        ("agy_sess_01", "Old Title", "Old Preview", raw_proto),
    )
    conn.commit()
    conn.close()

    adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=tmp_path)
    # mock ls_bridge
    adapter.ls_bridge = MagicMock(spec=AntigravityLanguageServerBridge)
    adapter.ls_bridge.update_conversation_title.return_value = True
    adapter.ls_bridge.send_cascade_message.return_value = True
    adapter.ls_bridge.cancel_cascade.return_value = True

    # 1. 测试重命名：LS RPC + SQLite 与 Protobuf 同步
    ok_rename = adapter.rename_session("agy_sess_01", "Antigravity 新名称")
    assert ok_rename is True
    adapter.ls_bridge.update_conversation_title.assert_called_with("agy_sess_01", "Antigravity 新名称")

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT title, preview, raw_summary FROM conversation_summaries WHERE conversation_id='agy_sess_01'").fetchone()
    conn.close()
    assert row[0] == "Antigravity 新名称"
    assert row[1] == "Antigravity 新名称"

    # 2. 测试发送指令：通过 Connect-RPC 直连下发
    ok_send = adapter.send_prompt_via_ls("agy_sess_01", "请审查代码", model_id="gemini-2.5-pro")
    assert ok_send is True
    adapter.ls_bridge.send_cascade_message.assert_called_with("agy_sess_01", "请审查代码", "gemini-2.5-pro")

    # 3. 测试中断会话：通过 Connect-RPC 取消
    ok_cancel = adapter.cancel_session("agy_sess_01")
    assert ok_cancel is True
    adapter.ls_bridge.cancel_cascade.assert_called_with("agy_sess_01")


def test_opencode_full_bidirectional_sync(tmp_path: Path):
    """测试 OpenCode 发送、中断、重命名与审批全套官方双向同步。"""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO session VALUES ('oc_sess_01', 'OpenCode 旧名称')")
    conn.execute("INSERT INTO session_v2 VALUES ('oc_sess_01', 'OpenCode 旧名称 V2')")
    conn.commit()
    conn.close()

    adapter = OpenCodeAdapter(data_dir=tmp_path)
    adapter.http_bridge = MagicMock(spec=OpenCodeHttpBridge)
    adapter.http_bridge.is_server_running.return_value = True
    adapter.http_bridge.rename_session.return_value = True
    adapter.http_bridge.send_message.return_value = {"ok": True, "turn_id": "turn_123"}
    adapter.http_bridge.abort_session.return_value = True
    adapter.http_bridge.respond_permission.return_value = True

    # 1. 测试重命名：官方 HTTP Bridge 广播 + SQLite 库表双同步
    ok_rename = adapter.rename_session("oc_sess_01", "OpenCode 新标题")
    assert ok_rename is True
    adapter.http_bridge.rename_session.assert_called_with("oc_sess_01", "OpenCode 新标题")

    conn = sqlite3.connect(str(db_path))
    r1 = conn.execute("SELECT title FROM session WHERE id='oc_sess_01'").fetchone()[0]
    r2 = conn.execute("SELECT title FROM session_v2 WHERE id='oc_sess_01'").fetchone()[0]
    conn.close()
    assert r1 == "OpenCode 新标题"
    assert r2 == "OpenCode 新标题"

    # 2. 测试发送命令：官方 HTTP 直连分发
    dispatch_res = adapter.try_direct_dispatch("oc_sess_01", "修复 Bug", model_name="claude-3-7-sonnet")
    assert dispatch_res is not None
    assert dispatch_res["via_opencode_http"] is True
    adapter.http_bridge.send_message.assert_called_with(
        session_id="oc_sess_01", prompt="修复 Bug", model_name="claude-3-7-sonnet"
    )

    # 3. 测试中断任务：官方 HTTP POST /session/:id/abort
    ok_abort = adapter.cancel_session("oc_sess_01")
    assert ok_abort is True
    adapter.http_bridge.abort_session.assert_called_with("oc_sess_01")

    # 4. 测试权限审批：官方 HTTP POST reply
    ok_perm = adapter.respond_permission("oc_sess_01", "perm_999", decision="approved")
    assert ok_perm is True
    adapter.http_bridge.respond_permission.assert_called_with(
        session_id="oc_sess_01", permission_id="perm_999", allow=True
    )


def test_claude_full_bidirectional_sync(tmp_path: Path):
    """测试 Claude Code 重命名、中断与流式参数组装双向同步。"""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True)
    sess_file = projects_dir / "claude_sess_01.jsonl"
    sess_file.write_text('{"type": "user", "message": {"content": "初始提问"}}\n', encoding="utf-8")

    adapter = ClaudeCodeAdapter(projects_dir=projects_dir)

    # 1. 测试重命名：伴生元数据持久化与扫描器优先读取
    ok_rename = adapter.rename_session("claude_sess_01", "Claude 官方重命名测试")
    assert ok_rename is True

    meta_file = projects_dir / "claude_sess_01.meta.json"
    assert meta_file.exists()
    doc = json.loads(meta_file.read_text(encoding="utf-8"))
    assert doc["title"] == "Claude 官方重命名测试"

    sess_obj = parse_claude_session_file(sess_file, sess_file.stat().st_mtime, {})
    assert sess_obj is not None
    assert sess_obj.title == "Claude 官方重命名测试"

    # 2. 测试中断会话：调用官方 claude stop
    with patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value.returncode = 0
        ok_stop = adapter.cancel_session("claude_sess_01")
        assert ok_stop is True
        mock_subproc.assert_called_once()

    # 3. 测试命令组装：--resume 与 --permission-prompt-tool stdio
    cmd = adapter.build_cli_command(
        raw_session_id="claude_sess_01",
        prompt="测试提示词",
        execution_mode="ask",
        model_name="claude-3-7-sonnet",
    )
    assert "--resume" in cmd
    assert "claude_sess_01" in cmd
    assert "--permission-prompt-tool" in cmd
    assert "stdio" in cmd
    assert "--model" in cmd
    assert "claude-3-7-sonnet" in cmd
