"""
多 Agent 统一适配器抽象基类与公共工具导出。

遵循规范：
1. 单条工具输出超过 4KB 或 100 行时自动截断（`is_truncated=True`），防止手机浏览器 DOM 崩溃。
2. 编辑文件类工具自动生成窄屏友好的单列 Unified Diff。
3. 自动识别破坏性命令并标记 `risk_level="high"`（手机端触发长按 0.8s 确认）。
"""

from __future__ import annotations

import difflib
import json
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

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
from backend.core.logger import log_structured_error, logger


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


def read_incremental_jsonl_stream(
    fpath: Path,
    cache_key: str,
    messages_cache: BoundedLRUCache,
    family_label: str,
    parse_record: Callable[[dict[str, Any], list[UnifiedMessageItem], int, Any], None],
    restore_state: Optional[Callable[[list[UnifiedMessageItem], tuple], Any]] = None,
    limit: int = 120,
    failure_hint: str = "系统将在下次文件刷新时自动重试。",
) -> list[UnifiedMessageItem]:
    """
    各 Agent 家族共享的「缓存命中 → 增量 seek → 逐行解码 → 解析回调 → 回写缓存」读取骨架。

    家族差异（解析函数、跨文件解析状态）通过钩子注入：
    - `parse_record(rec, items, line_index, state)`：单行解析回调，mutating items/state；
    - `restore_state(items, tail)`：从缓存尾部元组恢复解析状态并返回新状态。
      状态类型由家族自决（可为元组或可变列表容器），骨架仅透传不持有；
      未提供时默认空状态 `()`（无跨行解析上下文的家族足够使用）。

    缓存元组形态统一为 `(mtime_ns, fsize, items, consumed_bytes, *state_tail)`。
    `line_index` 为原始文件内近似绝对行号（增量分支为 `len(items) + 相对偏移`）。
    """

    def consume_lines(lines: list[bytes], start_index: int, items: list[UnifiedMessageItem], state: Any) -> int:
        """逐行解码→全部解析，返回已消费字节数。半截末行（无换行/json 不完整）安全忽略且不推进游标。"""
        consumed = 0
        total = len(lines)
        for idx, b_line in enumerate(lines):
            u_line = b_line.decode("utf-8", errors="replace").strip()
            if not u_line:
                consumed += len(b_line)
                continue
            try:
                rec_obj = json.loads(u_line)
            except json.JSONDecodeError:
                if idx == total - 1:
                    break
                consumed += len(b_line)
                continue

            consumed += len(b_line)
            parse_record(rec_obj, items, start_index + idx, state)
        return consumed

    try:
        st = fpath.stat()
        mtime_ns = st.st_mtime_ns
        fsize = st.st_size
    except OSError:
        return []

    cached = messages_cache.get(cache_key)
    if cached and cached[0] == mtime_ns and cached[1] == fsize:
        items: list[UnifiedMessageItem] = cached[2]
        return items[-limit:] if limit > 0 else items

    if cached and cached[1] <= fsize:
        # 增量分支：从上次游标 seek 追加部分
        try:
            items = list(cached[2])
            state = restore_state(items, tuple(cached[4:])) if restore_state else ()
            with open(fpath, "rb") as f:
                f.seek(cached[3])
                inc_data = f.read()

            inc_lines = inc_data.splitlines(keepends=True)
            consumed_bytes = cached[3] + consume_lines(inc_lines, len(items), items, state)

            messages_cache[cache_key] = (mtime_ns, fsize, items, consumed_bytes, *state)
            return items[-limit:] if limit > 0 else items
        except Exception as inc_exc:
            logger.debug(f"增量读取 {family_label} 异常，回退全量: {inc_exc}")

    # 全量读取
    items = []
    state = restore_state([], ()) if restore_state else ()
    try:
        with open(fpath, "rb") as f:
            all_raw = f.read()
        raw_b_lines = all_raw.splitlines(keepends=True)
        if raw_b_lines and not raw_b_lines[-1].endswith((b"\n", b"\r")):
            raw_b_lines = raw_b_lines[:-1]
    except Exception as exc:
        log_structured_error(
            problem=f"读取 {family_label} 会话日志失败 ({cache_key[:8]})",
            cause=str(exc),
            fix=failure_hint,
            exc=exc,
        )
        return []

    consumed_bytes = consume_lines(raw_b_lines, 0, items, state)
    messages_cache[cache_key] = (mtime_ns, fsize, items, consumed_bytes, *state)
    return items[-limit:] if limit > 0 else items


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

    def get_session(self, raw_session_id: str) -> Optional[UnifiedSession]:
        """按会话 ID 单独检索单个会话元数据（用于被置顶或有元数据覆盖但超出批量扫描上限的外部会话）。"""
        return None

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

    def rename_session(self, raw_session_id: str, new_title: str) -> bool:
        """将新标题回写同步至底层 Agent 的原生存储或数据库。"""
        return False

    def delete_session(self, raw_session_id: str) -> bool:
        """尝试从原生持久化存储中物理删除该会话（包括数据库行或日志文件）。默认返回 False。"""
        return False

    def fetch_pending_forms(self, raw_session_id: str) -> list[dict[str, Any]]:
        """获取当前会话待决策的交互式表单（默认空）。"""
        return []

    def reply_form(self, raw_session_id: str, form_id: str, answer_dict: dict[str, Any]) -> bool:
        """交付交互式决策表单答复（默认不支持返回 False）。"""
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

    def search_sessions_content(
        self, query: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        """在当前 Agent 历史持久化消息中检索关键词。返回 [{'raw_session_id': ..., 'type': ..., 'snippet': ...}, ...]"""
        return []

