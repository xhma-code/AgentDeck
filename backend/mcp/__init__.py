# -*- coding: utf-8 -*-
"""
AgentDeck MCP 协同服务包（基于官方 MCP Python SDK v2，支持 2026-07-28 主协议与 2025-11-25 兼容层）。
"""

from backend.mcp.auth import ALL_MCP_SCOPES, TOOL_REQUIRED_SCOPES, McpAuthGuard
from backend.mcp.idempotency import McpIdempotencyService
from backend.mcp.server import VersionedMcpServerDispatcher
from backend.mcp.service import McpCollaborationService
from backend.mcp.tools import MCP_TOOL_DEFINITIONS, list_mcp_tool_dicts

__all__ = [
    "ALL_MCP_SCOPES",
    "MCP_TOOL_DEFINITIONS",
    "McpAuthGuard",
    "McpCollaborationService",
    "McpIdempotencyService",
    "TOOL_REQUIRED_SCOPES",
    "VersionedMcpServerDispatcher",
    "list_mcp_tool_dicts",
]

