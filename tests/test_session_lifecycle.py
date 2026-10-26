# -*- coding: utf-8 -*-
"""
会话真相状态机测试套件 (S1/S2/C1/C3 — 计划 2026-10-26 session-creation-architecture)

覆盖：
- T1-1 创建降级：bridge 断连 → degraded 状态落库 + WS SESSION_STATUS_CHANGED 广播
- T1-2 幂等持久化重启：done → 回放 ACK；error → 允许重放（不阻塞）
- T1-3 并发串行：send_prompt_to_session 会话锁串行（旁路 PYTEST mock 短路）
- T1-4 长尾空闲回收：连续 2 个 poll 周期无输出 + 空闲 ≥ 阈值
- T1-5 remap 状态迁移：raw_id 回填时 lifecycle 随行迁移
"""

from __future__ import annotations

import asyncio
import json
import pytest
import time
from pathlib import Path
from typing import Any

from backend.core.database import MetaDatabase
from backend.engines.command_bus import CommandBus
from backend.engines.process_pool import AgentOrchestrator
from backend.engines.process_utils import TurnContext
from backend.adapters.opencode_adapter import OpenCodeAdapter


@pytest.fixture
def core_db(tmp_path: Path) -> MetaDatabase:
    return MetaDatabase(db_path=tmp_path / "lifecycle.db")


@pytest.fixture
def orch(core_db: MetaDatabase) -> AgentOrchestrator:
    o = AgentOrchestrator(meta_db=core_db)
    o._allow_real_process_in_test = False
    return o


async def collect_events(o: AgentOrchestrator) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def sub(envelope: dict[str, Any]) -> None:
        events.append(envelope)

    o.register_ws_subscriber(sub)
    return events


# ---------------------------------------------------------------------------
# T1-1 创建降级
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_1_opencode_create_failure_marks_degraded_and_broadcasts(core_db, orch) -> None:
    events = await collect_events(orch)
    oc = orch.adapters["opencode"]
    # 模拟 opencode server 未运行（HTTP bridge 断连）
    oc.http_bridge.is_server_running = lambda: False
    oc.http_bridge.create_session = lambda title="", directory="": (None, False)

    before = time.time()
    unified_id, meta = await orch.create_session(
        agent_type="opencode", project_path=str(Path.cwd()), title="降级测试", initial_prompt=""
    )
    elapsed = time.time() - before

    # ① REST 快返回（<500ms）
    assert elapsed < 0.5, f"创建耗时 {elapsed:.3f}s 超出快返回红线"
    # ② 底层创建失败 → degraded 落库（不得静默假成功）
    assert db_status(core_db, unified_id) == "degraded"
    assert meta.get("status") == "degraded"
    # ③ WS 广播 SESSION_STATUS_CHANGED（degraded）
    await asyncio.sleep(0.05)
    status_events = [e for e in events if e["type"] == "SESSION_STATUS_CHANGED"]
    assert any(
        e["payload"]["unified_id"] == unified_id and e["payload"]["status"] == "degraded"
        for e in status_events
    ), status_events
    # ④ 不进追赶缓冲（E9）：快照携带 status，缓冲区无状态事件
    snap = orch.get_state_snapshot()
    assert snap["session_statuses"].get(unified_id, {}).get("status") == "degraded"
    assert all(e["type"] != "SESSION_STATUS_CHANGED" for e in snap["missed_events"])


@pytest.mark.asyncio
async def test_t1_1b_opencode_create_success_is_real_id(core_db, orch) -> None:
    oc = orch.adapters["opencode"]
    oc.http_bridge.is_server_running = lambda: True
    oc.http_bridge.create_session = lambda title="", directory="": ("ses_real_1234567890abcdef", True)

    unified_id, _meta = await orch.create_session(
        agent_type="opencode", project_path=str(Path.cwd()), title="", initial_prompt=""
    )
    assert unified_id == "opencode:ses_real_1234567890abcdef"
    assert db_status(core_db, unified_id) == "live"


def db_status(db: MetaDatabase, uid: str) -> str:
    return db.get_session_status(uid)


# ---------------------------------------------------------------------------
# T1-2 幂等持久化（重启后语义）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_2_idempotency_survives_restart(core_db, orch) -> None:
    uid = "agy_cli:idem_restart"
    core_db.upsert_session_meta(unified_id=uid, agent_type="agy_cli", raw_session_id="idem_restart")

    bus1 = orch.command_bus
    ok_env = {
        "action": "execute_command", "client_msg_id": "cmd_ok_1",
        "command_type": "PIN_SESSION", "session_id": uid, "payload": {"is_pinned": True},
    }
    err_env = {
        "action": "execute_command", "client_msg_id": "cmd_err_1",
        "command_type": "RENAME_SESSION", "session_id": uid, "payload": {"title": "   "},
    }

    ack1 = await bus1.execute_command(ok_env)
    assert ack1["status"] == "success"
    ack2 = await bus1.execute_command(err_env)
    assert ack2["status"] == "error"
    rec = core_db.get_command_record("cmd_ok_1")
    assert rec and rec["status"] == "done"
    assert core_db.get_command_record("cmd_err_1")["status"] == "error"

    # 模拟重启：全新的 CommandBus + 空内存表，共享同一 DB
    bus2 = CommandBus(orchestrator=orch)
    assert not bus2._processed_commands

    # done 态 → 重放 ACK（拒绝重复执行）
    ack_replay = await bus2.execute_command(ok_env)
    assert ack_replay["status"] == "success"
    assert ack_replay.get("is_duplicate") is True

    # error 态 → 放行重放（D16：2am 静默故障防线）
    ack_err2 = await CommandBus(orchestrator=orch).execute_command(err_env)
    assert ack_err2["status"] == "error"
    assert ack_err2.get("is_duplicate") is not True


# ---------------------------------------------------------------------------
# T1-3 并发串行（C1 会话锁）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_3_send_prompt_serialized_by_session_lock(orch) -> None:
    uid = "agy_cli:concurrent_lock"
    orch.meta_db.upsert_session_meta(unified_id=uid, agent_type="agy_cli", raw_session_id="concurrent_lock")

    order: list[tuple[str, float]] = []

    async def fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        uid_k = kwargs["unified_id"]
        order.append(("start", time.perf_counter(), uid_k))
        await asyncio.sleep(0.05)
        order.append(("end", time.perf_counter(), uid_k))
        return {"ok": True, "unified_id": uid_k}

    orch._stream_runner.dispatch_prompt = fake_dispatch  # type: ignore[method-assign]

    # REST + WS 双路并发（锁必须完全串行，锁内不得让出）
    r1, r2 = await asyncio.gather(
        orch.send_prompt_to_session(unified_id=uid, prompt="A"),
        orch.send_prompt_to_session(unified_id=uid, prompt="B"),
    )
    assert r1["ok"] and r2["ok"]
    starts = [x for x in order if x[0] == "start"]
    ends = [x for x in order if x[0] == "end"]
    assert len(starts) == 2 and len(ends) == 2
    # 第一个 end 必须早于第二个 start（严格串行）
    assert ends[0][1] <= starts[1][1], f"并发未串行化: {order}"


# ---------------------------------------------------------------------------
# T1-4 长尾空闲回收（C3）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_4_idle_ttl_longtail_reclaim(orch) -> None:
    uid = "agy_cli:idle_ttl"
    orch._idle_poll_seconds = 0.05
    orch._idle_min_seconds = 0.1
    fake_proc = object()
    orch.active_processes[uid] = fake_proc
    orch.process_last_active[uid] = time.time() - (orch._idle_min_seconds + 10)

    aborted: list[str] = []
    orig_abort = orch.abort_session

    async def spy_abort(u: str, reason: str = "", **kw: Any) -> bool:
        aborted.append(u)
        # 模拟真 abort_session 的回收副作用
        orch.active_processes.pop(u, None)
        orch.process_last_active.pop(u, None)
        return True

    orch.abort_session = spy_abort  # type: ignore[method-assign]
    task = asyncio.create_task(orch._idle_process_ttl_loop())
    try:
        # 2 个 poll 周期后（连发比阈值）触发回收
        await asyncio.sleep(0.6)
        assert aborted == [uid], aborted
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# T1-5 remap 状态迁移（E5）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t1_5_remap_migrates_lifecycle(orch, core_db) -> None:
    uid = "agy_cli:tmp_remap"
    core_db.upsert_session_meta(unified_id=uid, agent_type="agy_cli", raw_session_id="tmp_remap")
    core_db.set_session_status(uid, "degraded", reason="backend_create_failed")

    ctx = TurnContext(
        unified_id=uid, agent_type="agy_cli", raw_session_id="tmp_remap",
        prompt="p", workdir=str(Path.cwd()), execution_mode="auto", model_name="",
    )
    line = json.dumps({"conversation_id": "real_remap_123"})
    await orch._stream_runner._handle_stream_line(ctx=ctx, line_str=line, proc=None)

    target = "agy_cli:real_remap_123"
    assert core_db.resolve_unified_id(uid) == target
    assert db_status(core_db, target) == "degraded", "remap 必须迁移 lifecycle 状态"
    assert ctx.unified_id == target
