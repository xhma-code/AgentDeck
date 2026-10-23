"""
多 Agent 统一适配器基类、标准消息模型、Unified Diff 生成器与高危命令分级器。

遵循 `/autoplan` 规范：
1. 单条工具输出超过 4KB 或 100 行时自动截断（`is_truncated=True`），防止手机浏览器 DOM 崩溃。
2. 编辑文件类工具自动生成窄屏友好的单列 Unified Diff。
3. 自动识别破坏性命令并标记 `risk_level="high"`（手机端触发长按 0.8s 确认）。
"""

from __future__ import annotations

import difflib
import json
import re
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

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



class ExecutionMode(StrEnum):
    """会话工具执行权限策略枚举（消除基本类型偏执）。"""

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

# 单条工具输出截断阈值（4KB 或 100 行）
MAX_TOOL_OUTPUT_BYTES = 4096
MAX_TOOL_OUTPUT_LINES = 100

# 高危破坏性操作正则特征（触发手机端长按 0.8s 防误触确认）
HIGH_RISK_PATTERNS = re.compile(
    r"(\brm\s+-rf?\b|\bdel\s+/[fsq]|\bRemove-Item\b.*-Recurse|"
    r"\bgit\s+reset\s+--hard\b|\bgit\s+push\b.*--force|\bgit\s+clean\s+-fd|"
    r"\bdrop\s+table\b|\btruncate\s+table\b|\bformat\s+[a-zA-Z]:|"
    r"\bStop-Process\b|\btaskkill\b)",
    re.IGNORECASE,
)

READ_ONLY_TOOL_NAMES = {
    "read_file",
    "view_file",
    "read_symbol",
    "codebase_search",
    "grep",
    "glob",
    "file_search",
    "list_dir",
    "list_directory",
    "search_web",
    "web_search",
    "find_by_name",
}


def is_read_only_tool(tool_name: str) -> bool:
    """判断是否为只读或检索类工具（其长文本输出对手机端无意义，且拖慢同步）。"""
    name = (tool_name or "").lower().replace("-", "_")
    if name in READ_ONLY_TOOL_NAMES:
        return True
    return any(
        k in name
        for k in (
            "read_file",
            "view_file",
            "codebase_search",
            "grep",
            "glob",
            "file_search",
            "search_web",
            "list_dir",
        )
    )


def count_diff_lines(diff_text: str) -> tuple[int, int]:
    """计算 Unified Diff 的新增行数和删除行数 (+N, -M)。"""
    if not diff_text:
        return 0, 0
    add_count = 0
    del_count = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            add_count += 1
        elif line.startswith("-") and not line.startswith("---"):
            del_count += 1
    return add_count, del_count


def classify_tool_risk(tool_name: str, args_text: str) -> str:
    """评估工具调用的风险等级（normal：单点确认，high：需长按 0.8s 确认）。"""
    lower_name = (tool_name or "").lower()
    if any(k in lower_name for k in ("bash", "command", "shell", "exec", "terminal")):
        if HIGH_RISK_PATTERNS.search(args_text or ""):
            return "high"
    if "delete" in lower_name or "remove" in lower_name:
        return "high"
    return "normal"


def truncate_large_output(
    text: str,
    max_bytes: int = MAX_TOOL_OUTPUT_BYTES,
    max_lines: int = MAX_TOOL_OUTPUT_LINES,
) -> tuple[str, bool, int]:
    """
    对超大工具输出或日志进行安全截断，返回 `(截断后文本, 是否发生截断, 原始总字符数)`。
    """
    if not text:
        return "", False, 0
    total_len = len(text)
    lines = text.splitlines()
    is_truncated = False

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        is_truncated = True

    joined = "\n".join(lines)
    encoded = joined.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        joined = encoded[:max_bytes].decode("utf-8", errors="ignore")
        is_truncated = True

    if is_truncated:
        joined += f"\n\n... [输出已截断，原始长度 {total_len} 字符 / 共 {len(text.splitlines())} 行，点击可展开全部]"
    return joined, is_truncated, total_len


def truncate_tool_output(
    tool_name: str,
    raw_output: str,
    max_bytes: int = MAX_TOOL_OUTPUT_BYTES,
    max_lines: int = MAX_TOOL_OUTPUT_LINES,
) -> tuple[str, bool, int]:
    """根据工具是否为只读探查类做智能截断：只读类截断为轻量预览(≤200字节/2行)，常规工具截断为 4KB。"""
    if is_read_only_tool(tool_name):
        return truncate_large_output(raw_output, max_bytes=200, max_lines=2)
    return truncate_large_output(raw_output, max_bytes=max_bytes, max_lines=max_lines)


def build_unified_diff(
    file_path: str,
    old_text: str,
    new_text: str,
    context_lines: int = 3,
) -> str:
    """
    生成适合 390px 手机窄屏阅读的单列 Unified Diff（默认仅保留变更前后 3 行上下文）。
    """
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    basename = Path(file_path).name if file_path else "file"
    diff_gen = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{basename}",
        tofile=f"b/{basename}",
        n=context_lines,
    )
    diff_str = "".join(diff_gen)
    if not diff_str and new_text:
        # 新建文件场景
        preview_lines = new_text.splitlines()[:40]
        diff_str = f"--- /dev/null\n+++ b/{basename}\n@@ -0,0 +1,{len(preview_lines)} @@\n" + "\n".join(
            f"+{line}" for line in preview_lines
        )
    return diff_str[:8000]


def extract_tool_file_path(raw_args: Any) -> str:
    """从工具调用参数中提取规范化文件路径（供前端识别新建/修改的 .md 文档或代码文件）。"""
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except Exception:
            return ""
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        return ""

    file_path = (
        args.get("Path")
        or args.get("file_path")
        or args.get("filePath")
        or args.get("path")
        or ""
    )
    return normalize_project_path(str(file_path)) if file_path else ""


def extract_tool_summary_and_diff(tool_name: str, raw_args: Any) -> tuple[str, str]:
    """从异构工具参数中提取一行人类可读摘要与单列 Unified Diff（若是文件修改类工具）。"""
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except Exception:
            return raw_args[:160], ""
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        return str(raw_args)[:160], ""

    # 1. 命令执行类
    cmd = (
        args.get("CommandLine")
        or args.get("command")
        or args.get("cmd")
        or args.get("script")
    )
    if cmd:
        summary = str(cmd).strip().replace("\n", " ")
        return summary[:200], ""

    # 2. 文件编辑与写入类
    file_path = (
        args.get("Path")
        or args.get("file_path")
        or args.get("filePath")
        or args.get("path")
        or ""
    )
    old_str = args.get("Target") or args.get("old_string") or args.get("oldString") or ""
    new_str = (
        args.get("Replacement")
        or args.get("new_string")
        or args.get("newString")
        or args.get("Content")
        or args.get("content")
        or ""
    )

    diff_text = ""
    if file_path and (old_str or new_str):
        diff_text = build_unified_diff(str(file_path), str(old_str), str(new_str))

    if file_path:
        return str(file_path), diff_text

    # 3. 搜索或通用工具
    query = args.get("query") or args.get("pattern") or args.get("Prompt") or args.get("prompt")
    if query:
        return str(query)[:180], ""

    compact = json.dumps(args, ensure_ascii=False)
    return compact[:180], diff_text


def extract_interactive_questions(tool_name: str, raw_args: Any) -> list[dict[str, Any]]:
    """
    从 Antigravity `ask_question` 或 Claude Code `AskUserQuestion` 工具参数中
    提取结构化单选/多选问题列表，供手机端渲染可点选的交互式答题卡片。
    """
    lower_name = (tool_name or "").lower()
    if lower_name not in ("ask_question", "askuserquestion"):
        return []

    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except Exception:
            return []
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        return []

    q_list = args.get("questions")
    if q_list is None and "question" in args:
        q_list = [args]
    elif isinstance(q_list, dict):
        q_list = [q_list]
    elif not isinstance(q_list, list):
        return []

    extracted: list[dict[str, Any]] = []
    for q_obj in q_list:
        if not isinstance(q_obj, dict):
            continue
        q_text = str(q_obj.get("question") or "").strip()
        if not q_text:
            continue
        raw_opts = q_obj.get("options") or []
        clean_opts: list[str] = []
        if isinstance(raw_opts, list):
            for opt in raw_opts:
                if isinstance(opt, str) and opt.strip():
                    clean_opts.append(opt.strip())
                elif isinstance(opt, dict):
                    lbl = str(opt.get("label") or opt.get("text") or opt.get("id") or "").strip()
                    desc = str(opt.get("description") or "").strip()
                    if lbl:
                        clean_opts.append(f"{lbl} — {desc}" if desc else lbl)
        is_multi = bool(q_obj.get("is_multi_select") or q_obj.get("multiSelect"))
        extracted.append(
            {
                "question": q_text,
                "options": clean_opts,
                "is_multi_select": is_multi,
            }
        )
    return extracted




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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_read_only"] = is_read_only_tool(self.name)
        if d["is_read_only"]:
            # API 传输裁剪：只读探查类工具(read_file等)的 output 在下发给移动端时强制置空，彻底切断大段源码传输
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

    @abstractmethod
    def get_watch_paths(self) -> list[Path]:
        """返回需要被 `watchfiles` 实时监听的本地目录列表。"""

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需读取被 4KB 截断的单步完整原始输出（子类可重写以读取底层未截断数据）。"""
        return ""

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """根据 Agent 类型、选定模型与执行模式（plan/ask/accept_edits/auto）构建流式子进程执行命令。"""
        raise NotImplementedError(f"{self.agent_label} 未实现命令行组装协议")

    def try_direct_dispatch(
        self,
        raw_session_id: str,
        prompt: str,
        model_name: str = "",
        workdir: str = "",
    ) -> Optional[dict[str, Any]]:
        """
        尝试通过原生直连通道（如 LS Connect-RPC 或本地 HTTP 服务）直发指令。
        若支持且成功直发则返回状态字典；若未开启或不支持则返回 None（上层回退至 CLI 子进程）。
        """
        return None

    def cancel_session(self, raw_session_id: str) -> bool:
        """尝试通过直连协议（如 Connect-RPC 取消 Cascade）停止正在运行的任务。"""
        return False

    def respond_permission(
        self,
        raw_session_id: str,
        tool_call_id: str,
        decision: str,
    ) -> bool:
        """通过原生通信渠道（如 OpenCode HTTP API）响应待审批工具调用的允许/拒绝。"""
        return False

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """
        从单帧流式 JSON 事件中提取出该 Agent 待执行/待审批的工具调用块列表。
        子类根据各 Agent 原生流格式（Claude tool_use / AGY tool_calls / OpenCode tool）实现解析。
        """
        return []

    def get_session_token_usage(
        self, raw_session_id: str, model_name: str = ""
    ) -> dict[str, Any]:
        """提取当前会话的最新上下文 Token 水位与总用量统计。"""
        limit = resolve_model_context_limit(model_name)
        return SessionTokenUsage(context_limit=limit).to_dict()


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

