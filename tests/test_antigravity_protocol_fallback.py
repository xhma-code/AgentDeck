# -*- coding: utf-8 -*-
"""
Antigravity LS Connect-RPC 协议契约与降级链路测试。

覆盖：
1. StartCascade source 枚举候选按序回退（LS 版本漂移防护契约）。
2. StartCascade 2xx 后回读校验拒绝幻影会话 / 校验不可用时信任 2xx。
3. agy_ide 注入失败返回 None（无幻影）与 agy_cli 跳过 LS 注入。
4. Orchestrator 层 IDE 创建失败平滑降级 CLI 创建。
5. Fork-to-CLI 对幻影源会话如实失败、真实源正常克隆。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.adapters.antigravity_adapter import AntigravityAdapter
from backend.adapters.antigravity_fork import fork_ide_session_to_cli
from backend.adapters.antigravity_ls_bridge import (
    CASCADE_SOURCE_CANDIDATES,
    AntigravityLanguageServerBridge,
)

PROJECT = r"D:\UnitTest\demo"


def _make_bridge_stub(
    scan=(1, "token"),
    raw_post_results=None,
    exists_result=None,
):
    """构造可注入的 ls_bridge 桩：raw_post_results 为按调用顺序弹出的 (ok, raw) 列表。"""
    calls: list[dict] = []
    results = list(raw_post_results or [])

    def _raw_post(scheme, port, csrf_token, method, body, timeout=2.0):
        calls.append({"method": method, "body": body})
        if results:
            return results.pop(0)
        return True, json.dumps({"cascadeId": body.get("cascadeId", "")}).encode()

    def _cascade_exists(cid, instances=None):
        return exists_result

    stub = SimpleNamespace(
        _scan_ls_processes=lambda: [scan] if scan else [],
        _find_listening_ports=lambda pid: [5000],
        _probe_connect_port=lambda port, token: "http",
        _raw_post=_raw_post,
        cascade_exists=_cascade_exists,
        start_cascade=lambda cid, title="", project_path="": True,
        update_conversation_title=lambda cid, title: True,
        delete_cascade=lambda cid: True,
    )
    stub.calls = calls
    return stub


# ---------------------------------------------------------------------------
# 1. StartCascade source 枚举契约与回退
# ---------------------------------------------------------------------------


def test_source_candidates_contract():
    """契约锚点：候选列表首位必须是当前版本实测合法值，且保留旧版兼容回退。"""
    assert CASCADE_SOURCE_CANDIDATES[0] == "CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT"
    assert "CORTEX_TRAJECTORY_SOURCE_VSCODE" in CASCADE_SOURCE_CANDIDATES
    assert len(set(CASCADE_SOURCE_CANDIDATES)) == len(CASCADE_SOURCE_CANDIDATES)


def test_start_cascade_falls_back_on_invalid_source():
    """首个 source 被 LS 拒绝（如枚举改名）时按序回退到下一候选并成功。"""
    b = AntigravityLanguageServerBridge()
    b._scan_ls_processes = lambda: [(1, "token")]  # type: ignore[assignment]
    b._find_listening_ports = lambda pid: [5000]  # type: ignore[assignment]
    b._probe_connect_port = lambda port, token: "http"  # type: ignore[assignment]
    calls: list[dict] = []

    def fake_raw_post(scheme, port, csrf_token, method, body, timeout=2.0):
        calls.append({"method": method, "body": body})
        if len(calls) == 1:
            return False, b'{"code":"invalid_argument","message":"CortexTrajectorySource is unspecified"}'
        return True, json.dumps({"cascadeId": body.get("cascadeId")}).encode()

    b._raw_post = fake_raw_post  # type: ignore[assignment]
    b.cascade_exists = lambda cid, instances=None: True  # type: ignore[assignment]

    assert b.start_cascade("cid-1234", title="t", project_path=PROJECT) is True
    start_calls = [c for c in calls if c["method"] == "StartCascade"]
    assert len(start_calls) == 2
    assert start_calls[0]["body"]["source"] == CASCADE_SOURCE_CANDIDATES[0]
    assert start_calls[1]["body"]["source"] == CASCADE_SOURCE_CANDIDATES[1]


def test_start_cascade_verification_rejects_phantom():
    """StartCascade 返回 2xx 但回读索引无该会话：视为假成功，最终如实返回 False。"""
    b = AntigravityLanguageServerBridge()
    b._scan_ls_processes = lambda: [(1, "token")]  # type: ignore[assignment]
    b._find_listening_ports = lambda pid: [5000]  # type: ignore[assignment]
    b._probe_connect_port = lambda port, token: "http"  # type: ignore[assignment]
    b._raw_post = lambda *a, **k: (True, b'{"cascadeId":"x"}')  # type: ignore[assignment]
    b.cascade_exists = lambda cid, instances=None: False  # type: ignore[assignment]

    assert b.start_cascade("phantom-cid", title="t", project_path=PROJECT) is False


def test_start_cascade_trusts_2xx_when_verification_unavailable():
    """回读校验通道自身故障（None）时不阻塞正常流程，信任 2xx 结果。"""
    b = AntigravityLanguageServerBridge()
    b._scan_ls_processes = lambda: [(1, "token")]  # type: ignore[assignment]
    b._find_listening_ports = lambda pid: [5000]  # type: ignore[assignment]
    b._probe_connect_port = lambda port, token: "http"  # type: ignore[assignment]
    b._raw_post = lambda *a, **k: (True, b'{"cascadeId":"x"}')  # type: ignore[assignment]
    b.cascade_exists = lambda cid, instances=None: None  # type: ignore[assignment]

    assert b.start_cascade("cid-ok", title="", project_path=PROJECT) is True


# ---------------------------------------------------------------------------
# 2. create_session 多态行为（无幻影 / CLI 不注入）
# ---------------------------------------------------------------------------


def test_create_session_agy_ide_returns_none_when_injection_fails(tmp_path: Path):
    """IDE 在线但注入失败：返回 None 交由上层降级，绝不返回幻影 ID。"""
    adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=tmp_path / "ide")
    adapter.ls_bridge = _make_bridge_stub(exists_result=None)
    # 注入失败：start_cascade 返回 False
    adapter.ls_bridge.start_cascade = lambda cid, title="", project_path="": False

    assert adapter.create_session(title="t", project_path=str(tmp_path)) is None


def test_create_session_agy_cli_skips_ls_injection(tmp_path: Path):
    """CLI 类型会话即使检测到 LS 进程也不得注入 IDE。"""
    adapter = AntigravityAdapter(agent_type="agy_cli", root_dir=tmp_path / "cli")
    injected = {"called": False}

    def _boom(*a, **k):
        injected["called"] = True
        return True

    adapter.ls_bridge = _make_bridge_stub(exists_result=None)
    adapter.ls_bridge.start_cascade = _boom

    cid = adapter.create_session(title="t", project_path=str(tmp_path))
    assert cid and len(cid) == 36
    assert injected["called"] is False


def test_create_session_agy_ide_happy_path(tmp_path: Path):
    """IDE 在线且注入成功：返回 cid 并回写工作区。"""
    adapter = AntigravityAdapter(agent_type="agy_ide", root_dir=tmp_path / "ide")
    adapter.ls_bridge = _make_bridge_stub(exists_result=True)
    cid = adapter.create_session(title="t", project_path=str(tmp_path))
    assert cid and len(cid) == 36


# ---------------------------------------------------------------------------
# 3. Fork-to-CLI 幻影防御
# ---------------------------------------------------------------------------


def test_fork_rejects_phantom_session(tmp_path: Path):
    """源 IDE 会话无任何存储痕迹：克隆如实失败并给出明确原因。"""
    ide_root = tmp_path / "ide"
    cli_root = tmp_path / "cli"
    cli_root.mkdir(parents=True)
    (cli_root / "conversation_summaries.db").touch()

    res = fork_ide_session_to_cli("phantom-id", ide_root=ide_root, cli_root=cli_root)
    assert res["ok"] is False
    assert "不存在" in str(res.get("message") or "")


def test_fork_succeeds_for_real_source(tmp_path: Path):
    """源会话存在（有 conversations db）：正常克隆成功。"""
    ide_root = tmp_path / "ide"
    cli_root = tmp_path / "cli"
    (ide_root / "conversations").mkdir(parents=True)
    cli_root.mkdir(parents=True)
    (cli_root / "conversation_summaries.db").touch()
    (ide_root / "conversations" / "real-id.db").write_bytes(b"x")

    res = fork_ide_session_to_cli("real-id", ide_root=ide_root, cli_root=cli_root)
    assert res["ok"] is True
    assert res["cli_unified_id"] == "agy_cli:real-id"
    assert (cli_root / "conversations" / "real-id.db").exists()


# ---------------------------------------------------------------------------
# 4. Orchestrator 层降级路由
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_create_session_falls_back_to_cli(tmp_path: Path):
    """IDE 注入失败返回 None 时，Orchestrator 自动降级为 agy_cli 创建。"""
    from backend.core.database import MetaDatabase
    from backend.engines.process_pool import AgentOrchestrator

    all_modes = {"ask", "plan", "accept_edits", "auto"}
    ide_adapter = SimpleNamespace(
        agent_label="Antigravity IDE",
        supported_modes=all_modes,
        create_session=lambda title="", project_path="": None,
    )
    cli_adapter = SimpleNamespace(
        agent_label="Antigravity CLI",
        supported_modes=all_modes,
        create_session=lambda title="", project_path="": "cli-cid-0001",
    )
    db = MetaDatabase(db_path=tmp_path / "meta.db")
    orch = AgentOrchestrator(meta_db=db, adapters={"agy_ide": ide_adapter, "agy_cli": cli_adapter})

    unified_id, meta = await orch.create_session(
        agent_type="agy_ide", project_path=str(tmp_path), title="降级测试"
    )
    assert unified_id.startswith("agy_cli:")
    assert meta["agent_type"] == "agy_cli"
