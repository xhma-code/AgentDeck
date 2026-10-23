"""
多 Agent 统一适配器基类、标准消息模型、Unified Diff 生成器与高危命令分级器。

遵循规范：
1. 单条工具输出超过 4KB 或 100 行时自动截断（`is_truncated=True`），防止手机浏览器 DOM 崩溃。
2. 编辑文件类工具自动生成窄屏友好的单列 Unified Diff。
3. 自动识别破坏性命令并标记 `risk_level="high"`（手机端触发长按 0.8s 确认）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from backend.adapters.models import (
    DEFAULT_MODEL_CONTEXT_LIMIT,
    MODEL_CONTEXT_LIMITS,
    ExecutionMode,
    RiskLevel,
    SessionStatus,
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    resolve_model_context_limit,
)
from backend.adapters.tool_utils import (
    HIGH_RISK_PATTERNS,
    MAX_TOOL_OUTPUT_BYTES,
    MAX_TOOL_OUTPUT_LINES,
    READ_ONLY_TOOL_NAMES,
    build_unified_diff,
    classify_tool_risk,
    count_diff_lines,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    is_read_only_tool,
    is_transient_probe_session,
    truncate_large_output,
    truncate_tool_output,
)
from backend.core.workspace import (
    ADHOC_DIR_NAMES,
    PROJECT_MARKER_NAMES,
    decode_workspace_uri,
    discover_quick_workflows,
    evaluate_project_dir,
    get_git_workspace_diff,
    get_git_workspace_status,
    normalize_project_path,
)


class BoundedLRUCache(OrderedDict):
    """带最大容量限制的轻量 LRU 缓存，防止长时间运行内存无界膨胀。"""

    def __init__(self, maxsize: int = 64, *args: Any, **kwargs: Any) -> None:
        self.maxsize = maxsize
        super().__init__(*args, **kwargs)

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default

    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.maxsize:
            self.popitem(last=False)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        self[key] = default
        return default

    def update(self, *args: Any, **kwargs: Any) -> None:
        for k, v in dict(*args, **kwargs).items():
            self[k] = v


class BaseAgentAdapter(ABC):
    """Agent 适配器抽象基类。"""

    agent_type: str
    agent_label: str

    def __init__(self) -> None:
        self._token_usage_cache: dict[str, tuple[int, dict[str, Any]]] = {}

    def invalidate_cache(self) -> None:
        """重置适配器内部短效缓存（供外部 watchfiles 事件或手动触发时多态调用）。"""
        pass

    @property
    def supported_modes(self) -> set[str]:
        """该 Agent 原生真实支持的执行策略集合，默认支持全部四种模式。"""
        return {
            ExecutionMode.ASK.value,
            ExecutionMode.PLAN.value,
            ExecutionMode.ACCEPT_EDITS.value,
            ExecutionMode.AUTO.value,
        }

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """检查本机该 Agent 的二进制或数据目录是否可用，返回 `(是否就绪, 状态描述)`。"""

    @abstractmethod
    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """列出该 Agent 的历史与活跃会话。"""

    @abstractmethod
    def get_session_messages(
        self, raw_session_id: str, limit: int = 100
    ) -> list[UnifiedMessageItem]:
        """读取指定会话的结构化消息流。"""

    def get_session_messages_page(
        self,
        raw_session_id: str,
        limit: int = 30,
        before_step: Optional[int] = None,
        since_step: Optional[int] = None,
    ) -> tuple[list[UnifiedMessageItem], int, bool]:
        """
        分页或增量读取指定会话的结构化消息流。
        返回 `(当前页消息列表, 会话总消息步数, 是否还有更早的历史消息)`。
        """
        all_items = self.get_session_messages(raw_session_id, limit=0)
        total_count = len(all_items)
        if since_step is not None:
            # 增量更新模式：仅返回 >= since_step 的变化或新增步骤，极大节省数据传输
            page_items = [it for it in all_items if it.step_index >= since_step]
            return page_items, total_count, False

        if before_step is not None:
            filtered = [it for it in all_items if it.step_index < before_step]
        else:
            filtered = all_items
        page_items = filtered[-limit:] if limit > 0 else filtered
        has_more = len(filtered) > len(page_items)
        return page_items, total_count, has_more

    def is_external_session_active(self, raw_session_id: str) -> bool:
        """轻量探测指定会话是否处于外部运行/活跃态。"""
        return False

    def get_active_session_ids(self) -> list[str]:
        """返回当前适配器下处于外部原生运行/活跃态的 unified_id 列表。"""
        return []

    def get_session_transcript_path(self, raw_session_id: str) -> Optional[Path]:
        """返回指定会话底层日志或数据库文件的本地路径。"""
        return None

    @abstractmethod
    def get_watch_paths(self) -> list[Path]:
        """返回需要被 `watchfiles` 实时监听的本地目录列表。"""

    @classmethod
    def is_meaningful_storage_change(cls, path_str: str, filename: str) -> bool:
        """多态判定指定文件变动是否属于该适配器关心的核心会话载体。"""
        return False

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需读取被 4KB 截断的单步完整原始输出。"""
        return ""

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """根据 Agent 类型、选定模型与执行模式构建流式子进程执行命令。"""
        raise NotImplementedError(f"{self.agent_label} 未实现命令行组装协议")

    def try_direct_dispatch(
        self,
        raw_session_id: str,
        prompt: str,
        model_name: str = "",
        workdir: str = "",
    ) -> Optional[dict[str, Any]]:
        """尝试通过原生直连通道直发指令。"""
        return None

    def cancel_session(self, raw_session_id: str) -> bool:
        """尝试通过直连协议停止正在运行的任务。"""
        return False

    def respond_permission(
        self,
        raw_session_id: str,
        tool_call_id: str,
        decision: str,
    ) -> bool:
        """通过原生通信渠道响应待审批工具调用的允许/拒绝。"""
        return False

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """从单帧流式 JSON 事件中提取出该 Agent 待执行/待审批的工具调用块列表。"""
        return []

    def get_session_token_usage(
        self, raw_session_id: str, model_name: str = ""
    ) -> dict[str, Any]:
        """提取当前会话的最新上下文 Token 水位与总用量统计。"""
        limit = resolve_model_context_limit(model_name)
        return SessionTokenUsage(context_limit=limit).to_dict()
