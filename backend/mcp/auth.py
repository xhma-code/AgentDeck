# -*- coding: utf-8 -*-
"""
MCP 认证、细粒度 Scope 最小权限、Origin 校验与失败限流守卫（backend/mcp/auth.py）。

安全边界（落实 Phase 4 审阅要求）：
1. 零信任本机访问：MCP 端点无论来自 127.0.0.1 还是 Stdio，一律要求提供有效 Project-scoped Token (agmcp_...)。
2. 服务端解析 project_id：绝不信任客户端传入的 project_id，任何跨项目访问触发 MCP_CROSS_PROJECT_DENIED。
3. Origin 校验：HTTP 请求若携带 Origin 头，仅允许本机受控来源（127.0.0.1 / localhost / [::1]），非法 Origin 返回 HTTP 403。
4. 请求体积限制：单次 HTTP MCP 请求体不超过 256 KB（超限返回 HTTP 413）。
5. 认证失败滑动窗口限流：同一来源连续认证失败超阈值返回 HTTP 429。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session, sessionmaker

from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import ActorType
from backend.domain.exceptions import SecurityBoundaryError
from backend.security.mcp_token_guard import (
    VerifiedMcpPrincipal,
    authenticate_mcp_token,
)

MAX_MCP_HTTP_BODY_BYTES = 256 * 1024  # 256 KB
MCP_TOOL_TIMEOUT_SEC = 15.0
MCP_MAX_CONCURRENT_CALLS = 32

ALL_MCP_SCOPES: tuple[str, ...] = (
    "context:read",
    "task:read",
    "progress:write",
    "decision:write",
    "decision:read",
    "artifact:write",
)

TOOL_REQUIRED_SCOPES: dict[str, str] = {
    "project_context_get": "context:read",
    "task_get": "task:read",
    "task_revision_get": "task:read",
    "task_report_progress": "progress:write",
    "step_update": "progress:write",
    "checkpoint_request": "decision:write",
    "decision_get": "decision:read",
    "artifact_register": "artifact:write",
}

ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "testserver"}


def validate_mcp_http_origin(origin_header: Optional[str]) -> None:
    """
    校验 MCP HTTP 请求的 Origin 头（遵循 MCP Streamable HTTP 安全规范）：
    - 未携带 Origin 头（如 CLI / Server-to-Server HTTP 客户端）允许继续进入 Token 鉴权；
    - 携带 Origin 头时，必须为受信任的本机 Origin，否则立即拒绝并返回 HTTP 403。
    """
    raw_origin = (origin_header or "").strip()
    if not raw_origin:
        return

    parsed = urlparse(raw_origin)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in ALLOWED_ORIGIN_HOSTS:
        raise SecurityBoundaryError(
            problem="MCP HTTP 请求被拒绝（非法 Origin 来源）",
            cause=f"Origin '{raw_origin}' 不在受信任的本机来源白名单内",
            fix="请仅从本机受信任环境（127.0.0.1 / localhost）发起 MCP HTTP 请求。",
            context={"origin": raw_origin, "http_status": 403, "mcp_error_code": "MCP_ORIGIN_FORBIDDEN"},
        )


class McpAuthRateLimiter:
    """基于滑动时间窗口的 MCP 认证失败限流器（线程安全）。"""

    def __init__(self, *, max_failures: int = 15, window_sec: float = 60.0) -> None:
        self._max_failures = max_failures
        self._window_sec = window_sec
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def check_allowed(self, client_key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._failures.get(client_key)
            if not q:
                return
            while q and (now - q[0]) > self._window_sec:
                q.popleft()
            if len(q) >= self._max_failures:
                raise SecurityBoundaryError(
                    problem="MCP 认证失败次数过多，已触发临时限流保护",
                    cause=f"客户端 '{client_key}' 在 {int(self._window_sec)} 秒内连续认证失败达到 {len(q)} 次",
                    fix="请核对 MCP Project Token 是否正确，稍后再试。",
                    context={
                        "client_key": client_key,
                        "http_status": 429,
                        "mcp_error_code": "MCP_RATE_LIMITED",
                    },
                )

    def record_failure(self, client_key: str) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._failures.setdefault(client_key, deque())
            while q and (now - q[0]) > self._window_sec:
                q.popleft()
            q.append(now)
            return len(q)

    def reset(self, client_key: str) -> None:
        with self._lock:
            self._failures.pop(client_key, None)


class McpAuthGuard:
    """MCP 令牌认证与作用域校验门面。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        rate_limiter: Optional[McpAuthRateLimiter] = None,
    ) -> None:
        self._session_factory = session_factory
        self.rate_limiter = rate_limiter or McpAuthRateLimiter()

    def verify_principal(
        self,
        *,
        raw_token_or_header: Optional[str],
        client_key: str = "stdio",
        claimed_project_id: Optional[str] = None,
        required_scope: Optional[str] = None,
    ) -> VerifiedMcpPrincipal:
        """校验限流状态、认证 MCP Token 并检查项目隔离与最小权限范围。"""
        self.rate_limiter.check_allowed(client_key)
        try:
            with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                principal = authenticate_mcp_token(
                    uow,
                    raw_token_or_header=raw_token_or_header,
                    claimed_project_id=claimed_project_id,
                    required_scope=required_scope,
                )
            return principal
        except SecurityBoundaryError as exc:
            err_code = (exc.context or {}).get("mcp_error_code", "")
            if err_code == "MCP_UNAUTHORIZED":
                fail_count = self.rate_limiter.record_failure(client_key)
                if fail_count >= self.rate_limiter._max_failures:
                    with SqlAlchemyUnitOfWork(self._session_factory) as uow:
                        uow.record_event(
                            entity_type="security",
                            entity_id=client_key[:64],
                            event_type="security.mcp_auth_rate_limited",
                            actor_type=ActorType.SYSTEM,
                            payload={"client_key": client_key, "failures": fail_count},
                        )
                        uow.commit()
            raise

