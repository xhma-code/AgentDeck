# -*- coding: utf-8 -*-
"""
多路复用 WebSocket 端点模块。
包含 Origin 来源校验、断线重连快照恢复与增量事件实时广播。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.core.logger import log_structured_error

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/stream")
async def websocket_stream(
    websocket: WebSocket,
    since_seq: int = Query(default=0),
    token: Optional[str] = Query(default=None),
) -> None:
    auth_mgr = websocket.app.state.auth_mgr
    ts_status = websocket.app.state.ts_status
    orchestrator = websocket.app.state.orchestrator

    origin = websocket.headers.get("origin")
    if not auth_mgr.is_trusted_origin(origin, ts_status.bind_hosts):
        log_structured_error(
            problem="拒绝可疑的跨站 WebSocket 握手请求",
            cause=f"Origin 头不匹配受信 Tailscale/本地主机: {origin}",
            fix="请直接通过本机或 Tailscale 局域网地址访问。",
        )
        await websocket.close(code=1008)
        return

    client_host = (websocket.client.host if websocket.client else "") or ""
    bind_hosts = getattr(ts_status, "bind_hosts", []) or []
    cookie_token = websocket.cookies.get("agentdeck_token")
    effective_token = token or cookie_token
    is_trusted_net = (
        client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        or (client_host in bind_hosts)
        or auth_mgr.verify_token(effective_token, client_ip=client_host)
        or auth_mgr.is_paired_trusted_vpn_ip(client_host)
    )
    if not is_trusted_net:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    snapshot = orchestrator.get_state_snapshot(since_seq_id=since_seq)
    await websocket.send_json(
        {
            "seq_id": snapshot["current_seq_id"],
            "type": "SESSION_STATE_SNAPSHOT",
            "payload": snapshot,
        }
    )

    async def push_to_client(envelope: dict[str, Any]) -> None:
        await websocket.send_json(envelope)

    orchestrator.register_ws_subscriber(push_to_client)
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            if action == "ping":
                await websocket.send_json({"type": "PONG", "seq_id": orchestrator._seq_counter})
            elif action == "execute_command":
                ack = await orchestrator.execute_command(data)
                ack["seq_id"] = orchestrator.event_bus.seq_counter
                await websocket.send_json(ack)
            elif action == "sync":
                last_seq = int(data.get("since_seq") or 0)
                snap = orchestrator.get_state_snapshot(since_seq_id=last_seq)
                await websocket.send_json(
                    {
                        "seq_id": snap["current_seq_id"],
                        "type": "SESSION_STATE_SNAPSHOT",
                        "payload": snap,
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_structured_error(
            problem="WebSocket 实时流会话异常断开",
            cause=str(exc),
            fix="客户端将自动触发指数退避重连。",
            exc=exc,
        )
    finally:
        orchestrator.unregister_ws_subscriber(push_to_client)
