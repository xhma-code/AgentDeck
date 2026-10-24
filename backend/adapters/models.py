"""
多 Agent 统一会话模型、消息卡片、枚举与 Token 水位定义。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Optional


class ExecutionMode(StrEnum):
    """会话工具执行权限策略枚举。"""

    PLAN = "plan"
    ASK = "ask"
    ACCEPT_EDITS = "accept_edits"
    AUTO = "auto"


class RiskLevel(StrEnum):
    """工具调用风险等级枚举。"""

    NORMAL = "normal"
    HIGH = "high"


class SessionStatus(StrEnum):
    """统一会话运行状态枚举。"""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DESKTOP_ACTIVE = "desktop_active"
    ERROR = "error"


@dataclass
class UnifiedToolCall:
    """统一工具调用卡片结构。"""

    tool_id: str
    name: str
    args_summary: str
    diff_text: str = ""
    diff_add_count: int = 0
    diff_del_count: int = 0
    output: str = ""
    is_truncated: bool = False
    full_output_len: int = 0
    risk_level: str = "normal"
    status: str = "DONE"
    file_path: str = ""
    duration_str: str = ""

    def to_dict(self) -> dict[str, Any]:
        from backend.adapters.tool_utils import is_read_only_tool

        d = asdict(self)
        d["is_read_only"] = is_read_only_tool(self.name)
        if d["is_read_only"]:
            d["output"] = ""
        return d


@dataclass
class UnifiedMessageItem:
    """统一对话流单步消息卡片结构。"""

    item_id: str
    step_index: int
    role: str  # "user" | "assistant" | "system"
    created_at: str
    content: str = ""
    thinking: str = ""
    tool_calls: list[UnifiedToolCall] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    status: str = "DONE"
    thinking_len: int = 0

    def to_dict(self, compact_thinking: bool = False) -> dict[str, Any]:
        d = asdict(self)
        d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.thinking:
            d["thinking_len"] = len(self.thinking)
            if compact_thinking and len(self.thinking) > 120:
                d["thinking"] = (
                    self.thinking[:80] + f" ... (共{len(self.thinking)}字)"
                )
        return d


@dataclass
class UnifiedSession:
    """四大 Agent 统一会话摘要模型。"""

    unified_id: str
    agent_type: str  # "agy_ide" | "agy_cli" | "claude_code" | "opencode"
    agent_label: str
    raw_session_id: str
    title: str
    preview: str
    project_path: str
    status: str  # "idle" | "running" | "waiting_approval" | "desktop_active" | "error"
    step_count: int
    updated_at: str
    is_pinned: int = 0
    execution_mode: str = "ask"
    model_name: str = ""
    is_project: bool = False
    project_name: str = ""
    desktop_locked: bool = False
    parent_unified_id: Optional[str] = None
    is_subagent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionTokenUsage:
    """统一会话 Token 用量与上下文水位快照。"""

    context_tokens: int = 0
    context_limit: int = 200_000
    context_percent: float = 0.0
    total_tokens: int = 0
    last_output_tokens: int = 0
    cache_read_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # Claude 模型族 (默认 200k)
    "claude-sonnet-5-5-high": 200_000,
    "claude-sonnet-5-5-medium": 200_000,
    "claude-sonnet-5-5-low": 200_000,
    "claude-opus-5-5-high": 200_000,
    "claude-opus-5-5-medium": 200_000,
    "claude-opus-5-5-low": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    # Gemini 模型族 (默认 1M)
    "gemini-4-argon-high": 1_000_000,
    "gemini-4-argon-medium": 1_000_000,
    "gemini-4-argon-low": 1_000_000,
    "gemini-3.8-flash-high": 1_000_000,
    "gemini-3.8-flash-medium": 1_000_000,
    "gemini-3.8-flash-low": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    # OpenAI / OpenCode 模型族 (默认 128k - 200k)
    "gpt-oss-120b-medium": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o1": 200_000,
    "o3-mini": 200_000,
}
DEFAULT_MODEL_CONTEXT_LIMIT = 200_000


def resolve_model_context_limit(model_name: str = "") -> int:
    """根据模型名称智能推断上下文窗口限制，默认兜底 200k。"""
    if not model_name:
        return DEFAULT_MODEL_CONTEXT_LIMIT
    m_clean = model_name.strip().lower()
    if m_clean in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[m_clean]
    for k, v in MODEL_CONTEXT_LIMITS.items():
        if k in m_clean or m_clean in k:
            return v
    if "gemini" in m_clean:
        return 1_000_000
    if "claude" in m_clean:
        return 200_000
    return DEFAULT_MODEL_CONTEXT_LIMIT
