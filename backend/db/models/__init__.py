# -*- coding: utf-8 -*-
"""
统一导出 AgentDeck 全部 21 张规范表 ORM 模型：
1. projects
2. mcp_project_tokens
3. tasks
4. task_dependencies
5. task_revisions
6. steps
7. step_revisions
8. runtime_providers
9. machines
10. runtime_instances
11. sessions
12. external_session_refs
13. executions
14. execution_attempts
15. decisions
16. artifacts
17. events
18. device_sessions
19. push_subscriptions
20. command_idempotency
21. table_column_comments
"""

from __future__ import annotations

from backend.db.models.artifact import ArtifactModel
from backend.db.models.decision import DecisionModel
from backend.db.models.event import EventModel
from backend.db.models.execution import ExecutionAttemptModel, ExecutionModel
from backend.db.models.planning import (
    StepModel,
    StepRevisionModel,
    TaskDependencyModel,
    TaskModel,
    TaskRevisionModel,
)
from backend.db.models.project import McpProjectTokenModel, ProjectModel
from backend.db.models.runtime import (
    MachineModel,
    RuntimeInstanceModel,
    RuntimeProviderModel,
)
from backend.db.models.session import ExternalSessionRefModel, SessionModel
from backend.db.models.system import (
    CommandIdempotencyModel,
    DeviceSessionModel,
    PushSubscriptionModel,
    TableColumnCommentModel,
)

ALL_CANONICAL_TABLE_NAMES = (
    "projects",
    "mcp_project_tokens",
    "tasks",
    "task_dependencies",
    "task_revisions",
    "steps",
    "step_revisions",
    "runtime_providers",
    "machines",
    "runtime_instances",
    "sessions",
    "external_session_refs",
    "executions",
    "execution_attempts",
    "decisions",
    "artifacts",
    "events",
    "device_sessions",
    "push_subscriptions",
    "command_idempotency",
    "table_column_comments",
)

__all__ = [
    "ALL_CANONICAL_TABLE_NAMES",
    "ProjectModel",
    "McpProjectTokenModel",
    "TaskModel",
    "TaskDependencyModel",
    "TaskRevisionModel",
    "StepModel",
    "StepRevisionModel",
    "RuntimeProviderModel",
    "MachineModel",
    "RuntimeInstanceModel",
    "SessionModel",
    "ExternalSessionRefModel",
    "ExecutionModel",
    "ExecutionAttemptModel",
    "DecisionModel",
    "ArtifactModel",
    "EventModel",
    "DeviceSessionModel",
    "PushSubscriptionModel",
    "CommandIdempotencyModel",
    "TableColumnCommentModel",
]

