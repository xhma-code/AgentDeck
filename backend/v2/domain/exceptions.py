# -*- coding: utf-8 -*-
"""
AgentDeck v2 领域异常体系。
所有领域异常提供结构化三段式中文信息，确保统一汇聚写入 logs/error.log。
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core.logger import log_structured_error


class DomainError(Exception):
    """领域基础异常。"""

    status_code: int = 400
    error_code: str = "DOMAIN_ERROR"

    def __init__(
        self,
        problem: str,
        cause: str,
        fix: str,
        *,
        context: Optional[dict[str, Any]] = None,
        log_error: bool = True,
    ) -> None:
        self.problem = problem
        self.cause = cause
        self.fix = fix
        self.context = context or {}
        if log_error:
            self.diagnostic = log_structured_error(
                problem=problem,
                cause=cause,
                fix=fix,
                context=self.context,
            )
        else:
            self.diagnostic = f"【错误现象】{problem} | 【根因分析】{cause} | 【修复建议】{fix}"
        super().__init__(self.diagnostic)


class EntityNotFoundError(DomainError):
    """目标领域实体不存在。"""

    status_code = 404
    error_code = "ENTITY_NOT_FOUND"


class InvariantViolationError(DomainError):
    """违反核心领域不变量（如无 Project 创建 Session）。"""

    status_code = 422
    error_code = "INVARIANT_VIOLATION"


class CrossProjectReferenceError(DomainError):
    """非法的跨 Project 实体关联引用。"""

    status_code = 422
    error_code = "CROSS_PROJECT_REFERENCE"


class DependencyCycleError(DomainError):
    """任务依赖关系出现自环或有向环（违反 DAG 约束）。"""

    status_code = 422
    error_code = "DEPENDENCY_CYCLE"


class VersionConflictError(DomainError):
    """乐观锁版本冲突。"""

    status_code = 409
    error_code = "VERSION_CONFLICT"


class SecurityBoundaryError(DomainError):
    """触发安全边界拦截（路径越界、符号链接逃逸、敏感凭证泄露或 MCP 越权）。"""

    status_code = 403
    error_code = "SECURITY_BOUNDARY_VIOLATION"

