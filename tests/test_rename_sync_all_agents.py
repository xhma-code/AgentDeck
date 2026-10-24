import os
import sqlite3
import json
import pytest
from pathlib import Path
from backend.adapters.antigravity_adapter import AntigravityAdapter
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.adapters.claude_adapter import ClaudeCodeAdapter


def test_antigravity_rename_sync(tmp_path: Path):
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
    # 构造含 protobuf field 1 的二进制数据
    # field 1: \n\x06old_t
    raw = b"\x0a\x06old_t\x10\x01\x1a\x02ok"
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
        ("sess-1", "Old Title", "Old Preview", raw),
    )
    conn.commit()
    conn.close()

    adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=tmp_path)
    ok = adapter.rename_session("sess-1", "New Unified Title")
    assert ok is True

    # 验证数据库回写与 protobuf 字段更新
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT title, preview, raw_summary FROM conversation_summaries WHERE conversation_id='sess-1'").fetchone()
    conn.close()
    assert row[0] == "New Unified Title"
    assert row[1] == "New Unified Title"
    new_raw = row[2]
    assert new_raw.startswith(b"\x0a\x11New Unified Title")


def test_opencode_rename_sync(tmp_path: Path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO session VALUES ('oc-1', 'Old OpenCode')")
    conn.execute("INSERT INTO session_v2 VALUES ('oc-1', 'Old OpenCode V2')")
    conn.commit()
    conn.close()

    adapter = OpenCodeAdapter(data_dir=tmp_path)
    ok = adapter.rename_session("oc-1", "Renamed OpenCode")
    assert ok is True

    conn = sqlite3.connect(str(db_path))
    r1 = conn.execute("SELECT title FROM session WHERE id='oc-1'").fetchone()[0]
    r2 = conn.execute("SELECT title FROM session_v2 WHERE id='oc-1'").fetchone()[0]
    conn.close()
    assert r1 == "Renamed OpenCode"
    assert r2 == "Renamed OpenCode"


def test_claude_rename_sync(tmp_path: Path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True)
    session_file = projects_dir / "claude-1.jsonl"
    session_file.write_text('{"type": "user", "message": {"content": "hello"}}\n', encoding="utf-8")

    adapter = ClaudeCodeAdapter(projects_dir=projects_dir)
    ok = adapter.rename_session("claude-1", "Renamed Claude")
    assert ok is True

    meta_file = projects_dir / "claude-1.meta.json"
    assert meta_file.exists()
    doc = json.loads(meta_file.read_text(encoding="utf-8"))
    assert doc["title"] == "Renamed Claude"
