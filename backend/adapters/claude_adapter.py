"""
Claude Code (`~/.claude/projects/<slug>/<session_id>.jsonl`) 适配器。

核心能力：
1. 扫描 `~/.claude/projects` 下所有项目目录的 `.jsonl` 会话文件。
2. 解析 `user` 与 `assistant` 消息（含 `thinking` 块、`tool_use` 块、`tool_result` 块及 `Edit` 单列 Unified Diff）。
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    classify_tool_risk,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    truncate_large_output,
)
from backend.core.logger import log_structured_error

COMMAND_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.DOTALL)
COMMAND_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.DOTALL)
TASK_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)
TASK_ID_RE = re.compile(r"<task-id>\s*(.*?)\s*</task-id>", re.DOTALL)


def clean_claude_user_text(raw: str) -> tuple[str, str]:
    """
    清洗 Claude Code 的 user 文本块，返回 `(消息类型, 清洗后文本)`：
    - `'ignore'`: 系统噪音（如 `<local-command-caveat>`, `[Request interrupted...]`），直接丢弃
    - `'task_notification'`: 后台任务完成通知 XML，归入上一轮工具执行过程而非用户气泡
    - `'slash_expanded'`: 斜杠命令自动展开的千字系统 Prompt 模板
    - `'user'`: 人类真实输入的提问或简短斜杠指令
    """
    text = (raw or "").strip()
    if not text:
        return "ignore", ""

    if text.startswith("[Request interrupted"):
        return "ignore", ""

    if "<local-command-caveat>" in text or "<local-command-stdout>" in text:
        cleaned = re.sub(r"<local-command-caveat>.*?</local-command-caveat>", "", text, flags=re.DOTALL)
        cleaned = re.sub(r"<local-command-stdout>.*?</local-command-stdout>", "", cleaned, flags=re.DOTALL).strip()
        if not cleaned:
            return "ignore", ""
        text = cleaned

    if "<task-notification>" in text:
        summary_m = TASK_SUMMARY_RE.search(text)
        tid_m = TASK_ID_RE.search(text)
        summary = summary_m.group(1).strip() if summary_m else "后台子任务已完成"
        tid = tid_m.group(1).strip() if tid_m else "task"
        return "task_notification", f"[{tid}] {summary}"

    if "<command-name>" in text or "<command-message>" in text:
        cmd_m = COMMAND_NAME_RE.search(text)
        args_m = COMMAND_ARGS_RE.search(text)
        cmd_name = cmd_m.group(1).strip() if cmd_m else "/command"
        if not cmd_name.startswith("/"):
            cmd_name = f"/{cmd_name}"
        cmd_args = args_m.group(1).strip() if args_m else ""
        if cmd_name in ("/clear", "/compact", "/exit", "/login", "/logout"):
            return "ignore", ""
        return "user", f"{cmd_name} {cmd_args}".strip()

    # 识别 /init 等斜杠命令自动注入的系统级英文 Prompt 模板
    if text.startswith("Please analyze this codebase and create a CLAUDE.md file"):
        return "slash_expanded", text

    # 剥离内嵌的 <system-reminder>
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL).strip()
    if not text:
        return "ignore", ""

    return "user", text


class ClaudeCodeAdapter(BaseAgentAdapter):
    """Claude Code 本地会话存储与流式协议适配器。"""

    agent_type = "claude_code"
    agent_label = "Claude Code"

    def __init__(self, projects_dir: Optional[Path] = None) -> None:
        self.projects_dir = projects_dir or Path(os.path.expanduser("~/.claude/projects"))
        self.cli_bin = (
            shutil.which("claude")
            or os.path.expanduser(r"~\.local\bin\claude.exe")
        )
        # fpath_str -> (mtime_ns, file_size, UnifiedSession)
        self._session_meta_cache: dict[str, tuple[int, int, UnifiedSession]] = {}
        # raw_session_id -> Path
        self._session_file_map: dict[str, Path] = {}
        # raw_session_id -> (mtime_ns, file_size, list[UnifiedMessageItem])
        self._messages_cache: dict[str, tuple[int, int, list[UnifiedMessageItem]]] = {}

    def is_available(self) -> tuple[bool, str]:
        has_bin = Path(str(self.cli_bin)).exists() if self.cli_bin else False
        sessions = self.list_sessions(limit=200) if self.projects_dir.exists() else []
        if has_bin or self.projects_dir.exists():
            return (
                True,
                f"Claude Code 已就绪: 发现 {len(sessions)} 个会话 (stream-json + stdio 逐条权限拦截)",
            )
        return False, "未检测到 claude.exe 或 ~/.claude/projects 目录"

    def get_watch_paths(self) -> list[Path]:
        return [self.projects_dir] if self.projects_dir.exists() else []

    def _find_session_file(self, raw_session_id: str) -> Optional[Path]:
        """在 ~/.claude/projects/*/<raw_session_id>.jsonl 中定位会话文件（优先命中内存路径索引）。"""
        cached_path = self._session_file_map.get(raw_session_id)
        if cached_path and cached_path.exists():
            return cached_path
        if not self.projects_dir.exists():
            return None
        for p in self.projects_dir.rglob(f"{raw_session_id}.jsonl"):
            self._session_file_map[raw_session_id] = p
            return p
        return None

    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """扫描 ~/.claude/projects 下所有 .jsonl 文件并提取会话元数据（带 mtime_ns 内存缓存）。"""
        if not self.projects_dir.exists():
            return []

        files: list[tuple[float, int, int, Path]] = []
        try:
            for fpath in self.projects_dir.rglob("*.jsonl"):
                if fpath.name.startswith("agent-"):
                    continue
                try:
                    st = fpath.stat()
                    self._session_file_map[fpath.stem] = fpath
                    files.append((st.st_mtime, st.st_mtime_ns, st.st_size, fpath))
                except OSError:
                    continue
        except Exception as exc:
            log_structured_error(
                problem="扫描 Claude Code 会话目录失败",
                cause=str(exc),
                fix="请检查 ~/.claude/projects 目录权限。",
                exc=exc,
            )
            return []

        files.sort(key=lambda x: x[0], reverse=True)
        sessions: list[UnifiedSession] = []

        for mtime, mtime_ns, fsize, fpath in files[:limit]:
            f_key = str(fpath)
            cached = self._session_meta_cache.get(f_key)
            if cached and cached[0] == mtime_ns and cached[1] == fsize:
                sessions.append(copy.copy(cached[2]))
                continue

            sid = fpath.stem
            title = ""
            preview = ""
            cwd = ""
            step_count = 0
            last_ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        rec_type = rec.get("type")
                        if not cwd and rec.get("cwd"):
                            cwd = str(rec.get("cwd"))
                        if rec.get("timestamp"):
                            last_ts = str(rec.get("timestamp"))

                        if rec_type in ("user", "assistant"):
                            step_count += 1
                            text = re.sub(r"\s+", " ", self._extract_record_plain_text(rec)).strip()
                            if text:
                                if rec_type == "user" and not title:
                                    title = text[:60]
                                preview = text[:160]
            except Exception as exc:
                log_structured_error(
                    problem=f"解析 Claude Code 会话摘要异常 ({fpath.name})",
                    cause=str(exc),
                    fix="已跳过该损坏文件，不影响其他会话加载。",
                    exc=exc,
                )
                continue

            if step_count == 0:
                continue

            if not title:
                title = preview[:60] if preview else f"Claude 会话 ({fpath.parent.name[:24]})"
            if not preview:
                preview = title

            sess_obj = UnifiedSession(
                unified_id=f"{self.agent_type}:{sid}",
                agent_type=self.agent_type,
                agent_label=self.agent_label,
                raw_session_id=sid,
                title=title,
                preview=preview,
                project_path=cwd or fpath.parent.name,
                status="idle",
                step_count=step_count,
                updated_at=last_ts,
            )
            self._session_meta_cache[f_key] = (mtime_ns, fsize, sess_obj)
            sessions.append(copy.copy(sess_obj))

        return sessions

    @staticmethod
    def _extract_record_plain_text(rec: dict[str, Any]) -> str:
        """从 Claude JSONL 记录中提取纯文本摘要（自动过滤系统标签与后台任务通知）。"""
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return ""
        rec_type = rec.get("type")
        content = msg.get("content")
        raw_text = ""
        if isinstance(content, str):
            raw_text = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = str(block.get("text") or "").strip()
                    if t and not t.startswith("<system-reminder>"):
                        parts.append(t)
            raw_text = " ".join(parts).strip()

        if rec_type == "user" and raw_text:
            kind, cleaned = clean_claude_user_text(raw_text)
            if kind in ("ignore", "task_notification", "slash_expanded"):
                return ""
            return cleaned
        return raw_text

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """解析指定 Claude Code 会话的 JSONL 为统一消息流（带 mtime_ns 内存缓存）。"""
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return []

        try:
            st = fpath.stat()
            mtime_ns, fsize = st.st_mtime_ns, st.st_size
        except OSError:
            mtime_ns, fsize = 0, 0

        cached = self._messages_cache.get(raw_session_id)
        if cached and cached[0] == mtime_ns and cached[1] == fsize:
            return cached[2][-limit:] if limit > 0 else list(cached[2])

        items: list[UnifiedMessageItem] = []
        tool_id_map: dict[str, UnifiedToolCall] = {}

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as exc:
            log_structured_error(
                problem=f"读取 Claude Code 会话文件失败 ({raw_session_id[:8]})",
                cause=str(exc),
                fix="请确认 ~/.claude/projects 下的会话文件未被独占锁定。",
                exc=exc,
            )
            return []

        def _append_cleaned_user_text(raw_txt: str, created_at_str: str) -> None:
            nonlocal step_idx
            kind, cleaned = clean_claude_user_text(raw_txt)
            if kind == "ignore" or not cleaned:
                return
            if kind == "slash_expanded":
                # 若上一条已经是斜杠命令（如 /init），则不再把展开的千字系统模板重复渲染为用户气泡
                if items and items[-1].role == "user" and items[-1].content.startswith("/"):
                    return
            if kind == "task_notification":
                # 后台子任务通知归入 Assistant 工具步骤，避免伪装成用户提问打断对话流
                step_idx += 1
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:step:{step_idx}",
                        step_index=step_idx,
                        role="assistant",
                        created_at=created_at_str,
                        content="",
                        tool_calls=[
                            UnifiedToolCall(
                                tool_id=f"task_notif_{step_idx}",
                                name="TaskNotification",
                                args_summary=cleaned,
                                status="DONE",
                            )
                        ],
                    )
                )
                return

            step_idx += 1
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{step_idx}",
                    step_index=step_idx,
                    role="user",
                    created_at=created_at_str,
                    content=cleaned,
                )
            )

        step_idx = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = rec.get("type")
            if rec_type not in ("user", "assistant"):
                continue

            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue

            created_at = str(rec.get("timestamp") or "")
            content = msg.get("content")

            if rec_type == "user":
                if isinstance(content, str):
                    _append_cleaned_user_text(content, created_at)
                elif isinstance(content, list):
                    user_texts: list[str] = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            txt = str(block.get("text") or "").strip()
                            if txt and not txt.startswith("<system-reminder>"):
                                user_texts.append(txt)
                        elif btype == "tool_result":
                            tid = str(block.get("tool_use_id") or "")
                            raw_res = block.get("content")
                            res_str = (
                                raw_res
                                if isinstance(raw_res, str)
                                else json.dumps(raw_res, ensure_ascii=False)
                            )
                            if tid in tool_id_map:
                                trunc_out, is_trunc, full_len = truncate_large_output(str(res_str))
                                tool_id_map[tid].output = trunc_out
                                tool_id_map[tid].is_truncated = is_trunc
                                tool_id_map[tid].full_output_len = full_len
                    if user_texts:
                        _append_cleaned_user_text("\n".join(user_texts), created_at)

            elif rec_type == "assistant":
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                tool_calls: list[UnifiedToolCall] = []
                parsed_questions: list[dict[str, Any]] = []

                if isinstance(content, str):
                    text_parts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            t = str(block.get("text") or "").strip()
                            if t:
                                text_parts.append(t)
                        elif btype == "thinking":
                            th = str(block.get("thinking") or "").strip()
                            if th:
                                thinking_parts.append(th)
                        elif btype == "tool_use":
                            tid = str(block.get("id") or f"tc_{step_idx}")
                            tname = str(block.get("name") or "Tool")
                            tinput = block.get("input") or {}
                            q_items = extract_interactive_questions(tname, tinput)
                            if q_items:
                                parsed_questions.extend(q_items)
                                continue
                            summary, diff_txt = extract_tool_summary_and_diff(tname, tinput)
                            f_path = extract_tool_file_path(tinput)
                            risk = classify_tool_risk(tname, summary)
                            tc_obj = UnifiedToolCall(
                                tool_id=tid,
                                name=tname,
                                args_summary=summary,
                                diff_text=diff_txt,
                                risk_level=risk,
                                file_path=f_path,
                            )
                            tool_calls.append(tc_obj)
                            tool_id_map[tid] = tc_obj

                if text_parts or thinking_parts or tool_calls or parsed_questions:
                    step_idx += 1
                    items.append(
                        UnifiedMessageItem(
                            item_id=f"{raw_session_id}:step:{step_idx}",
                            step_index=step_idx,
                            role="assistant",
                            created_at=created_at,
                            content="\n\n".join(text_parts),
                            thinking="\n\n".join(thinking_parts),
                            tool_calls=tool_calls,
                            questions=parsed_questions,
                        )
                    )

        self._messages_cache[raw_session_id] = (mtime_ns, fsize, items)
        return items[-limit:] if limit > 0 else items

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需读取 Claude Code 会话中指定步骤或工具的完整未截断输出。"""
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return ""
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            raw_outputs: list[str] = []
            cur_step = 0
            target_tool_ids: set[str] = set()
            for ln in lines:
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                rec_type = rec.get("type")
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if rec_type == "user":
                    if isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_result":
                                tid = str(b.get("tool_use_id") or "")
                                if tid in target_tool_ids:
                                    raw_res = b.get("content")
                                    raw_outputs.append(
                                        raw_res
                                        if isinstance(raw_res, str)
                                        else json.dumps(raw_res, ensure_ascii=False, indent=2)
                                    )
                    elif isinstance(content, str):
                        kind, cleaned = clean_claude_user_text(content)
                        if kind != "ignore" and cleaned:
                            cur_step += 1
                            if cur_step == step_index or step_index == 0:
                                raw_outputs.append(cleaned)
                elif rec_type == "assistant":
                    cur_step += 1
                    if cur_step == step_index and isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_use":
                                target_tool_ids.add(str(b.get("id") or ""))
                        if not target_tool_ids:
                            return json.dumps(rec, ensure_ascii=False, indent=2)
            return "\n\n---\n\n".join(raw_outputs) if raw_outputs else (lines[0] if lines else "")
        except Exception as exc:
            log_structured_error(
                problem=f"读取 Claude Code 单步完整输出失败 ({raw_session_id[:8]}#{step_index})",
                cause=str(exc),
                fix="请确认 ~/.claude/projects 下的会话文件可读。",
                exc=exc,
            )
            return ""

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """为 Claude Code 组装流式命令行（支持 plan / accept_edits / auto / stdio 逐条拦截）。"""
        clean_model = (model_name or "").strip()
        if clean_model == "default":
            clean_model = ""
        bin_path = str(self.cli_bin or "claude")
        cmd = [
            bin_path,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if clean_model:
            cmd.extend(["--model", clean_model])
        if self._find_session_file(raw_session_id):
            cmd.extend(["--resume", raw_session_id])
        else:
            cmd.extend(["--session-id", raw_session_id])

        if execution_mode == "plan":
            cmd.extend(["--permission-mode", "plan"])
        elif execution_mode == "accept_edits":
            cmd.extend(["--permission-mode", "acceptEdits"])
        elif execution_mode == "auto":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd.extend(["--permission-prompt-tool", "stdio"])
        if approved_tools:
            cmd.extend(["--allowedTools", ",".join(approved_tools)])
        return cmd

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """从 Claude Code stream-json 帧中提取待审批工具调用。"""
        tool_blocks: list[dict[str, Any]] = []
        msg = event.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_blocks.append(b)
        ev_type = str(event.get("type") or "")
        if ev_type in ("permission_request", "control_request", "tool_call"):
            tool_blocks.append(event)
        return tool_blocks

