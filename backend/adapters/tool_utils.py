"""
工具调用参数解析、高危操作分级、Unified Diff 生成与大文本截断实用函数。
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from backend.adapters.models import resolve_model_context_limit
from backend.core.workspace import normalize_project_path

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


def is_transient_probe_session(title: str = "", preview: str = "") -> bool:
    """
    判断会话是否为自动化测试探针或临时残留会话（如 cmem/flag.txt/marker.txt 探针测试记录）。
    统一供各大 Agent 适配器在扫描与投影时过滤，避免污染移动端用户会话列表。
    """
    check_text = (str(title or "") + " " + str(preview or "")).lower()
    return any(
        probe in check_text
        for probe in (
            "flag.txt",
            "marker.txt",
            "reading flag file",
            "read flag file",
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
