# -*- coding: utf-8 -*-
"""
AgentDeck CommandBus 与 WebSocket 全双工命令总线测试套件。
覆盖：指令分发、幂等防重、IDE优雅拦截、WebSocket 端到端握手与多端广播同步。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from backend.core.database import MetaDatabase
from backend.engines.command_bus import CommandBus
from backend.engines.process_pool import AgentOrchestrator


@pytest.fixture
def temp_meta_db(tmp_path: Path) -> MetaDatabase:
    db_path = tmp_path / "test_cmd_agentdeck.db"
    return MetaDatabase(db_path=db_path)


@pytest.fixture
def mock_orchestrator(temp_meta_db: MetaDatabase) -> AgentOrchestrator:
    orch = AgentOrchestrator(meta_db=temp_meta_db)
    # 模拟适配器以防真实拉起外部进程
    orch._allow_real_process_in_test = False
    return orch


@pytest.mark.asyncio
async def test_command_bus_pin_and_rename(mock_orchestrator: AgentOrchestrator, temp_meta_db: MetaDatabase) -> None:
    """测试置顶与重命名指令的执行与广播。"""
    cmd_bus = CommandBus(orchestrator=mock_orchestrator)

    # 1. 初始化一个会话元数据
    unified_id = "agy_cli:test_session_1"
    temp_meta_db.upsert_session_meta(
        unified_id=unified_id,
        agent_type="agy_cli",
        raw_session_id="test_session_1",
        title="初始标题",
        is_pinned=0,
    )

    # 2. 发送置顶命令
    pin_envelope = {
        "action": "execute_command",
        "client_msg_id": "cmsg_pin_001",
        "command_type": "PIN_SESSION",
        "session_id": unified_id,
        "payload": {"is_pinned": True},
    }
    ack = await cmd_bus.execute_command(pin_envelope)
    assert ack["status"] == "success"
    assert ack["client_msg_id"] == "cmsg_pin_001"
    assert ack["result"]["is_pinned"] is True

    # 验证数据库落库
    meta = temp_meta_db.get_session_overrides().get(unified_id)
    assert meta is not None
    assert meta["is_pinned"] == 1

    # 3. 发送重命名命令
    rename_envelope = {
        "action": "execute_command",
        "client_msg_id": "cmsg_rename_001",
        "command_type": "RENAME_SESSION",
        "session_id": unified_id,
        "payload": {"title": "全新标题_2026"},
    }
    ack_rename = await cmd_bus.execute_command(rename_envelope)
    assert ack_rename["status"] == "success"
    assert ack_rename["result"]["title"] == "全新标题_2026"

    # 验证数据库更新
    meta_renamed = temp_meta_db.get_session_overrides().get(unified_id)
    assert meta_renamed is not None
    assert meta_renamed["title"] == "全新标题_2026"


@pytest.mark.asyncio
async def test_command_bus_idempotency(mock_orchestrator: AgentOrchestrator, temp_meta_db: MetaDatabase) -> None:
    """测试弱网/断网重连时的 UUID 幂等防重机制。"""
    cmd_bus = CommandBus(orchestrator=mock_orchestrator)
    unified_id = "agy_cli:test_session_idemp"
    temp_meta_db.upsert_session_meta(
        unified_id=unified_id,
        agent_type="agy_cli",
        raw_session_id="test_session_idemp",
        title="测试标题",
    )

    envelope = {
        "action": "execute_command",
        "client_msg_id": "cmsg_dup_test_999",
        "command_type": "RENAME_SESSION",
        "session_id": unified_id,
        "payload": {"title": "重名修改一次"},
    }

    # 首次执行
    ack1 = await cmd_bus.execute_command(envelope)
    assert ack1["status"] == "success"
    assert not ack1.get("is_duplicate")

    # 第二次带相同 client_msg_id 重传（模拟断线重发）
    ack2 = await cmd_bus.execute_command(envelope)
    assert ack2["status"] == "success"
    assert ack2.get("is_duplicate") is True
    assert ack2["client_msg_id"] == "cmsg_dup_test_999"


@pytest.mark.asyncio
async def test_command_bus_create_and_delete_session(mock_orchestrator: AgentOrchestrator, temp_meta_db: MetaDatabase) -> None:
    """测试通过命令总线新建与删除会话。"""
    cmd_bus = CommandBus(orchestrator=mock_orchestrator)

    # 1. 新建会话
    create_envelope = {
        "action": "execute_command",
        "client_msg_id": "cmsg_create_001",
        "command_type": "CREATE_SESSION",
        "session_id": "",
        "payload": {
            "agent_type": "claude_code",
            "title": "新建 Claude 会话",
            "project_path": "/test/project",
        },
    }
    ack_create = await cmd_bus.execute_command(create_envelope)
    assert ack_create["status"] == "success"
    new_uid = ack_create["result"]["unified_id"]
    assert new_uid.startswith("claude_code:")

    # 验证落库
    meta = temp_meta_db.get_session_overrides().get(new_uid)
    assert meta is not None
    assert meta["title"] == "新建 Claude 会话"

    # 2. 删除会话
    del_envelope = {
        "action": "execute_command",
        "client_msg_id": "cmsg_del_001",
        "command_type": "DELETE_SESSION",
        "session_id": new_uid,
        "payload": {},
    }
    ack_del = await cmd_bus.execute_command(del_envelope)
    assert ack_del["status"] == "success"
    assert ack_del["result"]["deleted"] is True

    # 验证元数据已删除
    assert temp_meta_db.get_session_overrides().get(new_uid) is None


@pytest.mark.asyncio
async def test_command_bus_abort_ide_graceful_notice(mock_orchestrator: AgentOrchestrator) -> None:
    """测试对桌面 IDE 活跃会话的中断保护与优雅提示。"""
    cmd_bus = CommandBus(orchestrator=mock_orchestrator)
    ide_uid = "agy_ide:desktop_active_session"

    # 模拟该 IDE 会话在桌面端处于活跃中
    mock_orchestrator.is_session_active = MagicMock(return_value=True)

    abort_envelope = {
        "action": "execute_command",
        "client_msg_id": "cmsg_abort_ide_01",
        "command_type": "ABORT_SESSION",
        "session_id": ide_uid,
        "payload": {"reason": "手机端手动中断"},
    }

    ack = await cmd_bus.execute_command(abort_envelope)
    assert ack["status"] == "success"
    assert ack["result"]["status"] == "ide_active_manual_stop_required"
    assert "无法远程强杀" in ack["result"]["message"]


def test_fork_ide_session_to_cli_polymorphic(tmp_path: Path, temp_meta_db: MetaDatabase) -> None:
    """测试 Fork-to-CLI 的多态返回结果（同时支持 dict 访问与 tuple 解构，且元数据完整迁移）。"""
    from backend.adapters.antigravity_fork import fork_ide_session_to_cli

    ide_dir = tmp_path / "ide"
    cli_dir = tmp_path / "cli"
    (ide_dir / "conversations").mkdir(parents=True)
    raw_id = "test_fork_uuid_123"
    (ide_dir / "conversations" / f"{raw_id}.db").write_text("dummy sqlite content")

    # 预设原 IDE 会话元数据
    temp_meta_db.upsert_session_meta(
        unified_id=f"agy_ide:{raw_id}",
        agent_type="agy_ide",
        raw_session_id=raw_id,
        title="原始 IDE 会话标题",
        project_path="/workspace/demo",
    )

    # 1. 字典风格调用并访问
    res = fork_ide_session_to_cli(
        ide_raw_session_id=raw_id,
        ide_root=ide_dir,
        cli_root=cli_dir,
        meta_db=temp_meta_db,
    )
    assert res["ok"] is True
    assert res["cli_unified_id"] == f"agy_cli:{raw_id}"
    assert res.get("forked_from") == f"agy_ide:{raw_id}"

    # 2. 元组风格解构
    ok, target_id = res
    assert ok is True
    assert target_id == f"agy_cli:{raw_id}"

    # 3. 验证元数据自动继承
    cli_meta = temp_meta_db.get_session_overrides().get(f"agy_cli:{raw_id}")
    assert cli_meta is not None
    assert cli_meta["title"] == "原始 IDE 会话标题"
    assert cli_meta["parent_unified_id"] == f"agy_ide:{raw_id}"


@pytest.mark.asyncio
async def test_command_bus_unknown_type_and_validation(mock_orchestrator: AgentOrchestrator) -> None:
    """测试非法指令类型与缺失 client_msg_id 的优雅防御。"""
    cmd_bus = CommandBus(orchestrator=mock_orchestrator)

    # 1. 缺失 client_msg_id
    ack1 = await cmd_bus.execute_command({"command_type": "PIN_SESSION"})
    assert ack1["status"] == "error"
    assert "缺少 client_msg_id" in ack1["error"]

    # 2. 未知的 command_type
    ack2 = await cmd_bus.execute_command({
        "action": "execute_command",
        "client_msg_id": "cmsg_invalid_type",
        "command_type": "UNKNOWN_ACTION_XYZ",
    })
    assert ack2["status"] == "error"
    assert "不支持的指令类型" in ack2["error"]


@pytest.mark.asyncio
async def test_command_bus_ttl_cleanup(mock_orchestrator: AgentOrchestrator) -> None:
    """测试命令总线防重历史 TTL 过期清理。"""
    # 模拟超短 TTL (0.05 秒)
    cmd_bus = CommandBus(orchestrator=mock_orchestrator, ttl_seconds=0.05)
    env = {
        "action": "execute_command",
        "client_msg_id": "cmsg_expire_test",
        "command_type": "DELETE_SESSION",
        "session_id": "agy_cli:test_dummy",
    }
    ack1 = await cmd_bus.execute_command(env)
    assert ack1["status"] == "success"
    assert "cmsg_expire_test" in cmd_bus._processed_commands

    # 等待 TTL 过期
    await asyncio.sleep(0.08)
    cmd_bus._cleanup_expired_records()
    assert "cmsg_expire_test" not in cmd_bus._processed_commands


@pytest.mark.asyncio
async def test_websocket_command_ack_and_broadcast_integration(mock_orchestrator: AgentOrchestrator) -> None:
    """测试 WebSocket 订阅端点接收 execute_command 并广播 SESSION_UPDATED 的端到端逻辑。"""
    broadcasted_events: list[dict[str, Any]] = []

    async def mock_subscriber(envelope: dict[str, Any]) -> None:
        broadcasted_events.append(envelope)

    mock_orchestrator.register_ws_subscriber(mock_subscriber)
    try:
        cmd_bus = mock_orchestrator.command_bus
        env = {
            "action": "execute_command",
            "client_msg_id": "cmsg_ws_test_42",
            "command_type": "PIN_SESSION",
            "session_id": "agy_cli:ws_session_42",
            "payload": {"is_pinned": True},
        }
        ack = await cmd_bus.execute_command(env)
        assert ack["status"] == "success"
        assert ack["client_msg_id"] == "cmsg_ws_test_42"

        # 验证广播被触发
        assert len(broadcasted_events) >= 1
        last_ev = broadcasted_events[-1]
        assert last_ev["type"] == "SESSION_UPDATED"
        assert last_ev["payload"]["unified_id"] == "agy_cli:ws_session_42"
        assert last_ev["payload"]["is_pinned"] is True
    finally:
        mock_orchestrator.unregister_ws_subscriber(mock_subscriber)


@pytest.mark.asyncio
async def test_rename_session_bidirectional_sync_to_native_adapter(tmp_path: Path, temp_meta_db: MetaDatabase) -> None:
    """测试重命名会话不仅更新 meta_db，还双向同步回写至对应 Agent (如 Antigravity) 原生数据库。"""
    import sqlite3
    from backend.adapters.antigravity_adapter import AntigravityAdapter

    agy_dir = tmp_path / "antigravity"
    agy_dir.mkdir(parents=True)
    sum_db = agy_dir / "conversation_summaries.db"

    # 初始化原生 conversation_summaries.db
    conn = sqlite3.connect(str(sum_db))
    conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT, preview TEXT);")
    raw_id = "test_sync_title_uuid_888"
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?);",
        (raw_id, "Old English Title", "Old English Title"),
    )
    conn.commit()
    conn.close()

    adapter = AntigravityAdapter(agent_type="agy_ide", agent_label="Antigravity IDE", root_dir=agy_dir)
    orch = AgentOrchestrator(meta_db=temp_meta_db, adapters={"agy_ide": adapter})

    # 通过 CommandBus 执行重命名
    cmd_bus = orch.command_bus
    env = {
        "action": "execute_command",
        "client_msg_id": "cmsg_rename_sync_888",
        "command_type": "RENAME_SESSION",
        "session_id": f"agy_ide:{raw_id}",
        "payload": {"title": "双向同步新中文名"},
    }
    ack = await cmd_bus.execute_command(env)
    assert ack["status"] == "success"
    assert ack["result"]["title"] == "双向同步新中文名"

    # 1. 验证 meta_db 包含新标题
    meta = temp_meta_db.get_session_overrides().get(f"agy_ide:{raw_id}")
    assert meta is not None
    assert meta["title"] == "双向同步新中文名"

    # 2. 验证原生 conversation_summaries.db 已被双向回写
    conn = sqlite3.connect(str(sum_db))
    row = conn.execute("SELECT title, preview FROM conversation_summaries WHERE conversation_id = ?", (raw_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "双向同步新中文名"
    assert row[1] == "双向同步新中文名"




