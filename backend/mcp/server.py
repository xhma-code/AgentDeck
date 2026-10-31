# -*- coding: utf-8 -*-
"""
AgentDeck MCP Server 核心与多版本协议分发器（backend/mcp/server.py）。

严格落实 Phase 4 审阅要求：
1. 集成官方 MCP Python SDK v2 (mcp / mcp_types)，复用官方标准类型（Tool / CallToolResult / TextContent）与 Server 实例。
2. 按协议版本严格分流，绝不在同一处理器内混用新旧两套协议行为：
   - Stateless2026ProtocolAdapter（目标主规范 2026-07-28）：
     * 无状态请求模型，移除 initialize / notifications/initialized 与旧版协议级 Session；
     * 支持 server/discover、tools/list、tools/call、ping；
     * 校验 HTTP Mcp-Method 与 Mcp-Name 请求头，解析每个请求的 _meta 自描述版本信息。
   - Legacy2025CompatProtocolAdapter（兼容规范 2025-11-25 / 2025-03-26）：
     * 仅供尚未升级到 2026-07-28 的旧版客户端使用，支持传统 initialize 握手与 notifications/initialized。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import mcp_types
from mcp.server.lowlevel import Server as OfficialLowLevelMcpServer
from mcp.server.mcpserver import MCPServer as OfficialMcpServerV2
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from backend.core.logger import log_structured_error
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import ActorType
from backend.domain.exceptions import DomainError, SecurityBoundaryError
from backend.mcp.auth import (
    MCP_MAX_CONCURRENT_CALLS,
    MCP_TOOL_TIMEOUT_SEC,
    McpAuthGuard,
)
from backend.mcp.schemas import (
    MCP_PROTOCOL_VERSION_2025_03,
    MCP_PROTOCOL_VERSION_2025_11,
    MCP_PROTOCOL_VERSION_2026,
    SUPPORTED_MCP_PROTOCOL_VERSIONS,
    build_mcp_error_envelope,
    build_mcp_success_envelope,
)
from backend.mcp.service import McpCollaborationService
from backend.mcp.tools import (
    MCP_TOOLS_BY_NAME,
    dispatch_mcp_tool_call,
    list_mcp_tool_dicts,
)
from backend.security.mcp_token_guard import VerifiedMcpPrincipal

SERVER_NAME = "agentdeck-mcp"
SERVER_VERSION = "1.3.0"


def build_official_sdk_tool_models() -> list[mcp_types.Tool]:
    """使用官方 mcp_types.Tool 构造 8 个标准工具模型。"""
    return [mcp_types.Tool.model_validate(d) for d in list_mcp_tool_dicts()]


def create_official_sdk_server() -> OfficialLowLevelMcpServer:
    """创建官方 MCP Python SDK v2 Low-Level Server 实例并绑定工具元数据。"""

    async def _handle_list_tools(
        _ctx: Any,
        _params: Optional[mcp_types.PaginatedRequestParams] = None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(tools=build_official_sdk_tool_models())

    return OfficialLowLevelMcpServer(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=_handle_list_tools,
    )


def create_official_mcp_server_v2() -> OfficialMcpServerV2:
    """创建官方 MCP Python SDK v2 高阶 MCPServer 实例。"""
    return OfficialMcpServerV2(SERVER_NAME)



def _jsonrpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


def _jsonrpc_error(
    req_id: Any,
    code: int,
    message: str,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": err,
    }


def _extract_protocol_version_from_meta(payload: dict[str, Any]) -> Optional[str]:
    """从请求顶层或 params._meta 提取自描述协议版本。"""
    top_meta = payload.get("_meta")
    if isinstance(top_meta, dict) and top_meta.get("protocolVersion"):
        return str(top_meta["protocolVersion"]).strip()
    params = payload.get("params")
    if isinstance(params, dict):
        p_meta = params.get("_meta")
        if isinstance(p_meta, dict) and p_meta.get("protocolVersion"):
            return str(p_meta["protocolVersion"]).strip()
        if params.get("protocolVersion"):
            return str(params["protocolVersion"]).strip()
    return None


class _BaseToolExecutionMixin:
    """提供给新旧两个协议适配器的受控工具调用执行基类。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        collab_service: McpCollaborationService,
    ) -> None:
        self._session_factory = session_factory
        self._collab = collab_service

    def _execute_tool_call(
        self,
        *,
        principal: VerifiedMcpPrincipal,
        params: dict[str, Any],
        req_id: Any,
        protocol_version: str,
        http_mcp_name_header: Optional[str] = None,
    ) -> dict[str, Any]:
        tool_name = str(params.get("name") or "").strip()
        if not tool_name or tool_name not in MCP_TOOLS_BY_NAME:
            return _jsonrpc_error(
                req_id,
                -32602,
                f"未知的 MCP 工具名称: '{tool_name}'",
                {"available_tools": sorted(MCP_TOOLS_BY_NAME.keys())},
            )

        if http_mcp_name_header is not None:
            clean_hdr_name = http_mcp_name_header.strip()
            if clean_hdr_name and clean_hdr_name != tool_name:
                return _jsonrpc_error(
                    req_id,
                    -32600,
                    f"HTTP 请求头 Mcp-Name ('{clean_hdr_name}') 与 JSON-RPC 工具名 ('{tool_name}') 不一致",
                    {"mcp_error_code": "MCP_HEADER_MISMATCH"},
                )

        raw_arguments = params.get("arguments") or {}
        if not isinstance(raw_arguments, dict):
            return _jsonrpc_error(
                req_id,
                -32602,
                "tools/call 的 arguments 必须为 JSON 对象",
            )

        try:
            data, is_replay = dispatch_mcp_tool_call(
                self._collab,
                principal,
                tool_name,
                raw_arguments,
            )
            envelope = build_mcp_success_envelope(
                tool_name,
                data,
                is_idempotent_replay=is_replay,
                protocol_version=protocol_version,
            )

            # 记录只读工具调用审计事件（写工具已在各自同事务内记录业务事件，此处补充统一工具调用痕迹，重放时不重复记录）
            if not is_replay:
                with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                    uow.record_event(
                        project_id=principal.project_id,
                        entity_type="mcp",
                        entity_id=principal.token_id,
                        event_type="mcp.tool_called",
                        actor_type=ActorType.MCP_AGENT,
                        actor_id=principal.token_name,
                        payload={
                            "tool": tool_name,
                            "protocol_version": protocol_version,
                            "is_idempotent_replay": False,
                        },
                    )
                    uow.commit()

            call_result = mcp_types.CallToolResult(
                content=[
                    mcp_types.TextContent(
                        type="text",
                        text=json.dumps(envelope, ensure_ascii=False),
                    )
                ],
                structured_content=envelope,
                is_error=False,
            )
            res_dict = call_result.model_dump(by_alias=True, exclude_none=True)
            res_dict["_meta"] = {"protocolVersion": protocol_version}
            return _jsonrpc_result(req_id, res_dict)

        except (DomainError, ValidationError) as exc:
            err_envelope = build_mcp_error_envelope(
                tool_name,
                exc,
                protocol_version=protocol_version,
            )
            log_structured_error(
                problem=f"MCP 工具 '{tool_name}' 业务或安全规则拦截",
                cause=str(exc),
                fix="已向 Agent 返回结构化错误说明。",
                context={"project_id": principal.project_id, "tool": tool_name},
            )
            call_result = mcp_types.CallToolResult(
                content=[
                    mcp_types.TextContent(
                        type="text",
                        text=json.dumps(err_envelope, ensure_ascii=False),
                    )
                ],
                structured_content=err_envelope,
                is_error=True,
            )
            res_dict = call_result.model_dump(by_alias=True, exclude_none=True)
            res_dict["_meta"] = {"protocolVersion": protocol_version}
            return _jsonrpc_result(req_id, res_dict)


class Stateless2026ProtocolAdapter(_BaseToolExecutionMixin):
    """
    MCP 2026-07-28 官方无状态协议适配器：
    - 移除 initialize 与 notifications/initialized；
    - 无协议级 Session，每个请求通过 _meta 自描述；
    - 支持 server/discover、tools/list、tools/call、ping；
    - HTTP 下校验 Mcp-Method 与 Mcp-Name 请求头。
    """

    PROTOCOL_VERSION = MCP_PROTOCOL_VERSION_2026

    def handle_request(
        self,
        *,
        principal: VerifiedMcpPrincipal,
        payload: dict[str, Any],
        http_mcp_method_header: Optional[str] = None,
        http_mcp_name_header: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        req_id = payload.get("id")
        method = str(payload.get("method") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        if http_mcp_method_header is not None:
            clean_hdr_method = http_mcp_method_header.strip()
            if clean_hdr_method and clean_hdr_method != method:
                return _jsonrpc_error(
                    req_id,
                    -32600,
                    f"HTTP 请求头 Mcp-Method ('{clean_hdr_method}') 与 JSON-RPC method ('{method}') 不一致",
                    {"mcp_error_code": "MCP_HEADER_MISMATCH"},
                )

        if method in ("initialize", "notifications/initialized"):
            return _jsonrpc_error(
                req_id,
                -32601,
                f"MCP 2026-07-28 无状态规范已移除 '{method}' 握手流程；请直接发起无状态请求或使用 server/discover。",
                {"protocolVersion": self.PROTOCOL_VERSION},
            )

        if method == "ping":
            return _jsonrpc_result(
                req_id,
                {"_meta": {"protocolVersion": self.PROTOCOL_VERSION}},
            )

        if method == "server/discover":
            tools_models = [
                t.model_dump(by_alias=True, exclude_none=True)
                for t in build_official_sdk_tool_models()
            ]
            return _jsonrpc_result(
                req_id,
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "stateless": True,
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "stateless": True,
                    },
                    "projectScope": {
                        "project_id": principal.project_id,
                        "token_name": principal.token_name,
                        "granted_scopes": sorted(principal.scopes),
                    },
                    "tools": tools_models,
                    "_meta": {"protocolVersion": self.PROTOCOL_VERSION},
                },
            )

        if method == "tools/list":
            tools_models = [
                t.model_dump(by_alias=True, exclude_none=True)
                for t in build_official_sdk_tool_models()
            ]
            return _jsonrpc_result(
                req_id,
                {
                    "tools": tools_models,
                    "_meta": {"protocolVersion": self.PROTOCOL_VERSION},
                },
            )

        if method == "tools/call":
            return self._execute_tool_call(
                principal=principal,
                params=params,
                req_id=req_id,
                protocol_version=self.PROTOCOL_VERSION,
                http_mcp_name_header=http_mcp_name_header,
            )

        return _jsonrpc_error(
            req_id,
            -32601,
            f"MCP 2026-07-28 未知方法: '{method}'",
            {"protocolVersion": self.PROTOCOL_VERSION},
        )


class Legacy2025CompatProtocolAdapter(_BaseToolExecutionMixin):
    """
    MCP 2025-11-25 / 2025-03-26 旧客户端兼容协议适配器：
    - 支持 initialize 与 notifications/initialized 传统握手；
    - 支持 ping、tools/list、tools/call。
    """

    def handle_request(
        self,
        *,
        principal: VerifiedMcpPrincipal,
        payload: dict[str, Any],
        negotiated_version: str = MCP_PROTOCOL_VERSION_2025_11,
    ) -> Optional[dict[str, Any]]:
        req_id = payload.get("id")
        method = str(payload.get("method") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        eff_ver = (
            negotiated_version
            if negotiated_version in (MCP_PROTOCOL_VERSION_2025_11, MCP_PROTOCOL_VERSION_2025_03)
            else MCP_PROTOCOL_VERSION_2025_11
        )

        if method == "notifications/initialized" or method.startswith("notifications/"):
            return None

        if method == "initialize":
            client_ver = str(params.get("protocolVersion") or "").strip()
            chosen_ver = (
                client_ver
                if client_ver in (MCP_PROTOCOL_VERSION_2025_11, MCP_PROTOCOL_VERSION_2025_03)
                else MCP_PROTOCOL_VERSION_2025_11
            )
            return _jsonrpc_result(
                req_id,
                {
                    "protocolVersion": chosen_ver,
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                    "instructions": (
                        f"已连接 AgentDeck MCP Server（绑定项目 ID: {principal.project_id}）。"
                        "请遵守平台验收状态与 Agent 汇报状态分离原则。"
                    ),
                },
            )

        if method == "ping":
            return _jsonrpc_result(req_id, {})

        if method == "tools/list":
            tools_models = [
                t.model_dump(by_alias=True, exclude_none=True)
                for t in build_official_sdk_tool_models()
            ]
            return _jsonrpc_result(req_id, {"tools": tools_models})

        if method == "tools/call":
            return self._execute_tool_call(
                principal=principal,
                params=params,
                req_id=req_id,
                protocol_version=eff_ver,
                http_mcp_name_header=None,
            )

        return _jsonrpc_error(
            req_id,
            -32601,
            f"MCP Legacy ({eff_ver}) 未知方法: '{method}'",
        )


class VersionedMcpServerDispatcher:
    """
    多版本 MCP 协议路由器：
    - 识别 2026-07-28（默认新规范）与 2025-11-25 / 2025-03-26（旧客户端兼容），严格分流至对应 Adapter。
    - 内置并发信号量与单次工具调用超时保护。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.auth_guard = McpAuthGuard(session_factory)
        self.collab_service = McpCollaborationService(session_factory)
        self.official_sdk_server = create_official_sdk_server()
        self.stateless_2026 = Stateless2026ProtocolAdapter(session_factory, self.collab_service)
        self.legacy_2025 = Legacy2025CompatProtocolAdapter(session_factory, self.collab_service)
        self._semaphore = asyncio.Semaphore(MCP_MAX_CONCURRENT_CALLS)

    @staticmethod
    def resolve_target_protocol_version(
        payload: dict[str, Any],
        *,
        http_protocol_header: Optional[str] = None,
        http_mcp_method_header: Optional[str] = None,
        sticky_version: Optional[str] = None,
    ) -> str:
        """
        判定请求所属的 MCP 协议版本：
        1. 若请求体 _meta.protocolVersion 显式指定版本，优先采用；
        2. 若携带 HTTP MCP-Protocol-Version 头，采用该头指定版本；
        3. 若携带 2026-07-28 特有的 Mcp-Method 请求头或调用 server/discover，路由至 2026-07-28；
        4. 若调用旧版特有的 initialize / notifications/initialized，路由至 2025-11-25 兼容层；
        5. 若 Stdio 连接此前已完成旧版 initialize 握手（sticky_version），沿用该连接协商版本；
        6. 默认采用最新官方规范 2026-07-28。
        """
        meta_ver = _extract_protocol_version_from_meta(payload)
        if meta_ver in SUPPORTED_MCP_PROTOCOL_VERSIONS:
            return meta_ver

        hdr_ver = (http_protocol_header or "").strip()
        if hdr_ver in SUPPORTED_MCP_PROTOCOL_VERSIONS:
            return hdr_ver

        method = str(payload.get("method") or "").strip()
        if method == "server/discover" or (http_mcp_method_header or "").strip():
            return MCP_PROTOCOL_VERSION_2026

        if method in ("initialize", "notifications/initialized"):
            if meta_ver == MCP_PROTOCOL_VERSION_2026 or hdr_ver == MCP_PROTOCOL_VERSION_2026:
                return MCP_PROTOCOL_VERSION_2026
            return MCP_PROTOCOL_VERSION_2025_11

        if sticky_version in SUPPORTED_MCP_PROTOCOL_VERSIONS:
            return sticky_version

        return MCP_PROTOCOL_VERSION_2026

    def dispatch_sync(
        self,
        *,
        principal: VerifiedMcpPrincipal,
        payload: dict[str, Any],
        http_protocol_header: Optional[str] = None,
        http_mcp_method_header: Optional[str] = None,
        http_mcp_name_header: Optional[str] = None,
        sticky_version: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], str]:
        """同步分发单条 JSON-RPC 消息，返回 (响应字典或 None, 实际使用的协议版本号)。"""
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return (
                _jsonrpc_error(
                    payload.get("id") if isinstance(payload, dict) else None,
                    -32600,
                    "无效的 JSON-RPC 2.0 请求结构（必须包含 jsonrpc='2.0'）",
                ),
                MCP_PROTOCOL_VERSION_2026,
            )

        target_ver = self.resolve_target_protocol_version(
            payload,
            http_protocol_header=http_protocol_header,
            http_mcp_method_header=http_mcp_method_header,
            sticky_version=sticky_version,
        )

        if target_ver == MCP_PROTOCOL_VERSION_2026:
            resp = self.stateless_2026.handle_request(
                principal=principal,
                payload=payload,
                http_mcp_method_header=http_mcp_method_header,
                http_mcp_name_header=http_mcp_name_header,
            )
            return resp, MCP_PROTOCOL_VERSION_2026

        resp = self.legacy_2025.handle_request(
            principal=principal,
            payload=payload,
            negotiated_version=target_ver,
        )
        return resp, target_ver

    async def dispatch_async(
        self,
        *,
        principal: VerifiedMcpPrincipal,
        payload: dict[str, Any],
        http_protocol_header: Optional[str] = None,
        http_mcp_method_header: Optional[str] = None,
        http_mcp_name_header: Optional[str] = None,
        sticky_version: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], str]:
        """在异步并发信号量与超时保护下分发执行 MCP 请求。"""
        async with self._semaphore:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.dispatch_sync,
                    principal=principal,
                    payload=payload,
                    http_protocol_header=http_protocol_header,
                    http_mcp_method_header=http_mcp_method_header,
                    http_mcp_name_header=http_mcp_name_header,
                    sticky_version=sticky_version,
                ),
                timeout=MCP_TOOL_TIMEOUT_SEC,
            )
