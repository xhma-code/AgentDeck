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
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse


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

# 目录工程特征磁盘探测缓存：norm_key -> (expire_ts, has_marker, real_folder_name)
_DIR_MARKER_CACHE: dict[str, tuple[float, bool, str]] = {}

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


def decode_workspace_uri(raw_uri: str) -> str:
    """将 file:///d%3A/foo 形式的 URI 解码为干净的本地路径字符串。"""
    if not raw_uri:
        return ""
    first_uri = raw_uri.split(",")[0].strip().strip("[]\"'")
    if first_uri.startswith("file://"):
        parsed = urlparse(first_uri)
        decoded = unquote(parsed.path or "")
        # Windows 路径如 /d:/Development -> d:/Development
        if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":
            decoded = decoded[1:]
        return normalize_project_path(decoded)
    return normalize_project_path(unquote(first_uri))


# 识别“真正工程项目”的标志性文件/目录集合
PROJECT_MARKER_NAMES = (
    ".git",
    "pyproject.toml",
    "package.json",
    "pom.xml",
    "go.mod",
    "Cargo.toml",
    "requirements.txt",
    "Makefile",
    "CMakeLists.txt",
    "build.gradle",
    ".idea",
    ".vscode",
    "README.md",
)

# 随手打开的临时会话系统目录特征（强制排除出项目列表）
ADHOC_DIR_NAMES = {
    "system32",
    "syswow64",
    "windows",
    "downloads",
    "desktop",
    "documents",
    "temp",
    "tmp",
    "appdata",
}


def normalize_project_path(raw_path: str) -> str:
    """统一正斜杠、去除末尾斜杠并规范化 Windows 盘符为小写，避免同项目因大小写/斜杠不同被拆开。"""
    if not raw_path:
        return ""
    cleaned = str(raw_path).strip().replace("\\", "/").rstrip("/")
    if len(cleaned) >= 2 and cleaned[1] == ":":
        cleaned = cleaned[0].lower() + cleaned[1:]
    return cleaned


def evaluate_project_dir(
    raw_path: str,
    bookmarked_keys: Optional[set[str]] = None,
    hidden_keys: Optional[set[str]] = None,
) -> tuple[bool, str, str]:
    """
    判定指定目录是否属于真正的工程项目（而非随手打开的临时会话文件夹）。
    返回 `(是否为真项目, 规范化路径, 项目名称)`：
    1. 若在手动隐藏集合 (`hidden_keys`) 中 -> False
    2. 若在手动收藏集合 (`bookmarked_keys`) 中 -> True
    3. 若为 Home 根目录、Windows/System32、Downloads、隐藏点目录 (.xxx) -> False
    4. 若目录包含 `.git` / `pyproject.toml` / `package.json` / `pom.xml` 等工程特征 -> True
    """
    norm = normalize_project_path(raw_path)
    if not norm:
        return False, "", ""

    key = norm.lower()
    parts = [p for p in norm.split("/") if p]
    folder_name = parts[-1] if parts else ""

    if hidden_keys and key in hidden_keys:
        return False, norm, ""

    if bookmarked_keys and key in bookmarked_keys:
        return True, norm, folder_name

    # 排除隐藏目录（如 .claude-mem, .gemini）和常见系统/临时叶子目录名
    if not folder_name or folder_name.startswith(".") or folder_name.lower() in ADHOC_DIR_NAMES:
        return False, norm, ""

    # 排除用户 Home 根目录（如 C:/Users/username 或 /home/user）
    try:
        home_norm = normalize_project_path(str(Path.home())).lower()
        if key == home_norm:
            return False, norm, ""
    except Exception:
        pass

    if len(parts) == 3 and parts[0].endswith(":") and parts[1].lower() == "users":
        return False, norm, ""
    if any(seg.lower() in ("windows", "system32", "syswow64") for seg in parts):
        return False, norm, ""

    # 检查本地目录是否包含工程特征文件（带 120s 内存缓存，避免每次拉列表重复 stat 磁盘）
    now_ts = time.monotonic()
    cached = _DIR_MARKER_CACHE.get(key)
    if cached and cached[0] > now_ts:
        if cached[1]:
            return True, norm, cached[2]
        return False, norm, ""

    try:
        p_obj = Path(norm)
        if p_obj.is_dir():
            for marker in PROJECT_MARKER_NAMES:
                if (p_obj / marker).exists():
                    # 用真实文件系统大小写修正目录名（如 DatatoAPI vs DataToAPI 统一为真实目录名）
                    try:
                        real_name = p_obj.resolve().name or folder_name
                    except Exception:
                        real_name = folder_name
                    _DIR_MARKER_CACHE[key] = (now_ts + 120.0, True, real_name)
                    return True, norm, real_name
    except Exception:
        pass

    _DIR_MARKER_CACHE[key] = (now_ts + 120.0, False, "")
    return False, norm, ""


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

    q_list = args.get("questions") or []
    if not isinstance(q_list, list):
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


def discover_quick_workflows(project_path: str = "") -> list[dict[str, str]]:
    """
    返回移动端 `/` 斜杠菜单支持的快捷指令与工作流列表（内置高频动作 + 本地自定义工作流/技能）。
    保持轻量克制，专为离开电脑后的手机单手点击设计。
    """
    items: list[dict[str, str]] = [
        {"command": "/plan", "label": "制定分步实施计划", "category": "slash"},
        {"command": "/review", "label": "审查当前代码变更与潜在问题", "category": "slash"},
        {"command": "/grill-me", "label": "就当前方案向我提问对齐细节", "category": "slash"},
        {"command": "/tdd", "label": "按测试驱动开发 (TDD) 推进", "category": "slash"},
        {"command": "/compact", "label": "压缩总结上下文释放窗口", "category": "slash"},
        {"command": "继续执行下一步", "label": "⚡ 快捷：确认当前进度并继续下一步", "category": "quick"},
        {"command": "运行测试并修复所有报错", "label": "🧪 快捷：执行测试套件并自动修复失败项", "category": "quick"},
        {"command": "总结当前已完成的代码改动与待办事项", "label": "📋 快捷：生成精简进度汇报", "category": "quick"},
    ]

    seen_cmds = {it["command"] for it in items}
    # 扫描项目目录下的 .agent/workflows/*.md 或 .claude/commands/*.md
    if project_path:
        norm_p = decode_workspace_uri(project_path)
        if norm_p:
            p_root = Path(norm_p)
            for rel_dir in (".agent/workflows", ".claude/commands"):
                wf_dir = p_root / rel_dir
                if wf_dir.is_dir():
                    try:
                        for md_file in sorted(wf_dir.glob("*.md"))[:12]:
                            cmd_str = f"/{md_file.stem}"
                            if cmd_str not in seen_cmds:
                                seen_cmds.add(cmd_str)
                                items.append(
                                    {
                                        "command": cmd_str,
                                        "label": f"项目工作流: {md_file.stem}",
                                        "category": "workflow",
                                    }
                                )
                    except Exception:
                        pass
    return items


def get_git_workspace_status(raw_project_path: str) -> dict[str, Any]:
    """
    轻量只读查询指定工作区的 Git 分支与未提交变更文件列表（严格只读，绝不修改暂存区）。
    """
    norm_path = decode_workspace_uri(raw_project_path)
    if not norm_path:
        return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}

    repo_dir = Path(norm_path)
    if not repo_dir.is_dir():
        return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}

    try:
        branch_proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.5,
        )
        if branch_proc.returncode != 0:
            return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}
        branch = branch_proc.stdout.strip() or "HEAD"

        status_proc = subprocess.run(
            ["git", "-C", str(repo_dir), "-c", "core.quotepath=false", "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
        )
        numstat_proc = subprocess.run(
            ["git", "-C", str(repo_dir), "-c", "core.quotepath=false", "diff", "HEAD", "--numstat"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
        )
        if numstat_proc.returncode != 0:
            numstat_proc = subprocess.run(
                ["git", "-C", str(repo_dir), "-c", "core.quotepath=false", "diff", "--numstat"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
            )

        stat_map: dict[str, tuple[int, int]] = {}
        for ln in (numstat_proc.stdout or "").splitlines():
            parts = ln.split("\t")
            if len(parts) >= 3:
                add_s, del_s, f_rel = parts[0], parts[1], parts[2].strip().strip('"')
                add_n = int(add_s) if add_s.isdigit() else 0
                del_n = int(del_s) if del_s.isdigit() else 0
                stat_map[f_rel] = (add_n, del_n)

        files: list[dict[str, Any]] = []
        for raw_line in (status_proc.stdout or "").splitlines():
            if len(raw_line) < 4:
                continue
            xy = raw_line[:2]
            rel_path = raw_line[3:].strip().strip('"')
            if " -> " in rel_path:
                rel_path = rel_path.split(" -> ")[-1].strip().strip('"')

            code = "M"
            if "?" in xy:
                code = "U"
            elif "A" in xy:
                code = "A"
            elif "D" in xy:
                code = "D"
            elif "R" in xy:
                code = "R"

            adds, dels = stat_map.get(rel_path, (0, 0))
            full_p = str((repo_dir / rel_path).resolve()).replace("\\", "/")
            files.append(
                {
                    "path": rel_path,
                    "full_path": full_p,
                    "status": code,
                    "additions": adds,
                    "deletions": dels,
                }
            )

        return {
            "ok": True,
            "is_git_repo": True,
            "project_path": norm_path,
            "branch": branch,
            "changed_count": len(files),
            "files": files[:80],
        }
    except Exception:
        return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}


def get_git_workspace_diff(raw_project_path: str, file_path: str = "") -> dict[str, Any]:
    """
    轻量只读获取工作区单文件或全局未提交 Unified Diff（含未跟踪新文件预览，严格限制路径边界）。
    """
    norm_path = decode_workspace_uri(raw_project_path)
    if not norm_path:
        return {"ok": False, "diff": "", "error": "无效的工作区路径"}

    repo_dir = Path(norm_path).resolve()
    if not repo_dir.is_dir():
        return {"ok": False, "diff": "", "error": "工作区目录不存在"}

    clean_rel = (file_path or "").strip().replace("\\", "/").lstrip("/")
    if clean_rel:
        target_abs = (repo_dir / clean_rel).resolve()
        if not target_abs.is_relative_to(repo_dir):
            return {"ok": False, "diff": "", "error": "禁止越界访问工作区外部文件"}

    try:
        cmd = ["git", "-C", str(repo_dir), "-c", "core.quotepath=false", "diff", "HEAD"]
        if clean_rel:
            cmd.extend(["--", clean_rel])
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.5,
        )
        diff_text = proc.stdout or ""
        if not diff_text.strip():
            # 可能是尚未首个 commit 的仓库或未跟踪的新文件 (Untracked)
            cmd_fallback = ["git", "-C", str(repo_dir), "-c", "core.quotepath=false", "diff"]
            if clean_rel:
                cmd_fallback.extend(["--", clean_rel])
            proc2 = subprocess.run(
                cmd_fallback,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
            )
            diff_text = proc2.stdout or ""

        # 若为单文件且仍无 git diff 输出，说明是未跟踪新文件 (Untracked)，生成新建文件 Diff 预览
        if not diff_text.strip() and clean_rel:
            target_abs = (repo_dir / clean_rel).resolve()
            if target_abs.is_file() and target_abs.stat().st_size <= 256 * 1024:
                raw_content = target_abs.read_text(encoding="utf-8", errors="replace")
                diff_text = build_unified_diff(clean_rel, "", raw_content)

        max_chars = 48000
        is_truncated = len(diff_text) > max_chars
        if is_truncated:
            diff_text = diff_text[:max_chars] + "\n\n... [Diff 较长，手机端已截取前 48KB 展示]"

        return {
            "ok": True,
            "project_path": norm_path,
            "file_path": clean_rel,
            "is_truncated": is_truncated,
            "diff": diff_text,
        }
    except Exception as exc:
        return {"ok": False, "diff": "", "error": str(exc)}


@dataclass
class UnifiedToolCall:
    """统一工具调用卡片结构。"""

    tool_id: str
    name: str
    args_summary: str
    diff_text: str = ""
    output: str = ""
    is_truncated: bool = False
    full_output_len: int = 0
    risk_level: str = "normal"
    status: str = "DONE"
    file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
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


class BaseAgentAdapter(ABC):
    """Agent 适配器抽象基类。"""

    agent_type: str
    agent_label: str

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
    ) -> tuple[list[UnifiedMessageItem], int, bool]:
        """
        分页读取指定会话的结构化消息流。
        返回 `(当前页消息列表, 会话总消息步数, 是否还有更早的历史消息)`。
        """
        all_items = self.get_session_messages(raw_session_id, limit=5000)
        total_count = len(all_items)
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
