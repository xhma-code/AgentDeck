"""
Antigravity IDE (`~/.gemini/antigravity`) 与 Antigravity CLI (`~/.gemini/antigravity-cli`) 同构适配器。

核心能力：
1. 复用同一套 `AntigravityStorageAdapter` 同时读取桌面版 IDE 与 CLI 的 `conversation_summaries.db` 及 `transcript.jsonl`。
2. 遵循 `/autoplan` 防双写脑裂规范：当 `agy_ide` 会话在桌面端活跃时标记 `desktop_locked=True`；
   支持在手机端发送消息时将 IDE 会话安全克隆至 `agy_cli` (`fork_ide_session_to_cli`) 继续执行。
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    classify_tool_risk,
    decode_workspace_uri,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    truncate_large_output,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error, logger

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)

# 已知的工具执行结果 step_type 集合（在 transcript.jsonl 中它们的 source 也是 MODEL，必须挂载到工具卡片而非渲染成聊天气泡）
AGY_TOOL_OUTPUT_TYPES = {
    "GENERIC",
    "RUN_COMMAND",
    "VIEW_FILE",
    "CODE_ACTION",
    "LIST_DIRECTORY",
    "SEARCH_WEB",
    "GREP_SEARCH",
    "MCP_TOOL",
    "BROWSER_ACTION",
    "READ_FILE",
    "EDIT_FILE",
    "WRITE_FILE",
}


def clean_agy_user_content(raw_text: str) -> str:
    """剥离 Antigravity transcript.jsonl 中包裹用户输入的外层 <USER_REQUEST> 与系统元数据标签，并清理 TUI 框线冗余空格。"""
    if not raw_text:
        return ""
    match = USER_REQUEST_RE.search(raw_text)
    if match:
        cleaned = match.group(1).strip()
        if cleaned:
            cleaned = re.sub(r"[ \t]{6,}", "  ", cleaned)
            return cleaned
    # 若无标签或标签内为空（如点击了 Proceed 审批），提取可读部分
    stripped = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw_text, flags=re.DOTALL)
    stripped = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<EPHEMERAL_MESSAGE>.*?</EPHEMERAL_MESSAGE>", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<system-reminder>.*?</system-reminder>", "", stripped, flags=re.DOTALL)
    stripped = stripped.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
    stripped = re.sub(r"[ \t]{6,}", "  ", stripped)
    return stripped or "（已确认执行 / Proceed）"


class AntigravityAdapter(BaseAgentAdapter):
    """通用 Antigravity 存储与会话适配器（通过传入不同 root_dir 分别服务 IDE 与 CLI）。"""

    def __init__(
        self,
        agent_type: str = "agy_ide",
        agent_label: str = "Antigravity IDE",
        root_dir: Optional[Path] = None,
    ) -> None:
        self.agent_type = agent_type
        self.agent_label = agent_label
        default_dir = (
            "~/.gemini/antigravity"
            if agent_type == "agy_ide"
            else "~/.gemini/antigravity-cli"
        )
        self.root_dir = root_dir or Path(os.path.expanduser(default_dir))
        self.summary_db_path = self.root_dir / "conversation_summaries.db"
        self.brain_dir = self.root_dir / "brain"
        self.conversations_dir = self.root_dir / "conversations"
        self.cli_bin = (
            shutil.which("agy")
            or os.path.expanduser(r"~\AppData\Local\agy\bin\agy.exe")
        )
        self.ls_bridge = AntigravityLanguageServerBridge()
        # (last_scan_ts, dict[cid, UnifiedSession])
        self._brain_scan_cache: tuple[float, dict[str, UnifiedSession]] = (0.0, {})
        # limit -> (db_sig, dict[cid, UnifiedSession])
        self._summary_db_cache: dict[int, tuple[tuple[int, int, int, int], dict[str, UnifiedSession]]] = {}
        # raw_session_id -> (mtime_ns, file_size, list[UnifiedMessageItem])
        self._messages_cache: dict[str, tuple[int, int, list[UnifiedMessageItem]]] = {}

    def _get_summary_db_sig(self) -> tuple[int, int, int, int]:
        m1, s1, m2, s2 = 0, 0, 0, 0
        try:
            if self.summary_db_path.exists():
                st1 = self.summary_db_path.stat()
                m1, s1 = st1.st_mtime_ns, st1.st_size
            wal_p = Path(f"{self.summary_db_path}-wal")
            if wal_p.exists():
                st2 = wal_p.stat()
                m2, s2 = st2.st_mtime_ns, st2.st_size
        except OSError:
            pass
        return (m1, s1, m2, s2)

    def is_available(self) -> tuple[bool, str]:
        if self.summary_db_path.exists() or self.brain_dir.exists():
            sessions = self.list_sessions(limit=200)
            mode_note = " (只读镜像 + 安全克隆接力)" if self.agent_type == "agy_ide" else " (stream-json 双向管道)"
            return True, f"已挂载 {self.root_dir.name}: 发现 {len(sessions)} 个会话{mode_note}"
        return False, f"未找到数据目录 {self.root_dir}"

    def get_watch_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.root_dir.exists():
            paths.append(self.root_dir)
        if self.brain_dir.exists():
            paths.append(self.brain_dir)
        return paths

    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """从 conversation_summaries.db 只读查询会话列表，并补充扫描 brain 目录（带 WAL 签名与目录扫描缓存）。"""
        sessions_map: dict[str, UnifiedSession] = {}

        if self.summary_db_path.exists():
            sig = self._get_summary_db_sig()
            cached_db = self._summary_db_cache.get(limit)
            if cached_db and cached_db[0] == sig:
                sessions_map = {k: copy.copy(v) for k, v in cached_db[1].items()}
            else:
                sql = """
                    SELECT conversation_id, title, preview, step_count,
                           last_modified_time, workspace_uris, status, not_fully_idle
                    FROM conversation_summaries
                    WHERE killed = 0
                    ORDER BY last_modified_time DESC
                    LIMIT ?
                """
                rows = safe_query_external_sqlite(self.summary_db_path, sql, (limit,))
                for r in rows:
                    cid = r.get("conversation_id") or ""
                    if not cid:
                        continue
                    not_idle = bool(r.get("not_fully_idle"))
                    raw_status = str(r.get("status") or "")
                    if self.agent_type == "agy_ide" and not_idle:
                        status = "desktop_active"
                    elif not_idle or "RUNNING" in raw_status.upper():
                        status = "running"
                    else:
                        status = "idle"

                    raw_title = re.sub(r"\s+", " ", (r.get("title") or "").strip())
                    raw_preview = re.sub(r"\s+", " ", (r.get("preview") or "").strip()).strip("│ ")

                    # 过滤无标题且无预览的内部 Subagent 会话（首条消息为 SYSTEM_MESSAGE 而非用户输入）
                    if not raw_title and not raw_preview:
                        tp = self.brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
                        if tp.exists():
                            try:
                                with open(tp, "r", encoding="utf-8", errors="replace") as tf:
                                    first_ln = tf.readline().strip()
                                if first_ln and '"SYSTEM_MESSAGE"' in first_ln:
                                    continue
                            except OSError:
                                pass

                    title = raw_title or (raw_preview[:48] if raw_preview else f"会话 {cid[:8]}")
                    preview = raw_preview or title
                    project_path = decode_workspace_uri(str(r.get("workspace_uris") or ""))
                    updated_at = str(r.get("last_modified_time") or utc_now_iso()).replace(" ", "T")

                    sessions_map[cid] = UnifiedSession(
                        unified_id=f"{self.agent_type}:{cid}",
                        agent_type=self.agent_type,
                        agent_label=self.agent_label,
                        raw_session_id=cid,
                        title=title,
                        preview=preview[:160],
                        project_path=project_path,
                        status=status,
                        step_count=int(r.get("step_count") or 0),
                        updated_at=updated_at,
                        desktop_locked=(self.agent_type == "agy_ide" and not_idle),
                    )
                self._summary_db_cache[limit] = (
                    sig,
                    {k: copy.copy(v) for k, v in sessions_map.items()},
                )

        # 补充扫描 brain 目录中存在 transcript.jsonl 但尚未写入 summary 表的活跃会话（带 2s 内存缓存防高频 I/O）
        if self.brain_dir.exists():
            now_mono = time.monotonic()
            cached_ts, cached_brain_map = self._brain_scan_cache
            if now_mono - cached_ts > 2.0:
                new_brain_map: dict[str, UnifiedSession] = {}
                try:
                    for conv_dir in self.brain_dir.iterdir():
                        if not conv_dir.is_dir():
                            continue
                        cid = conv_dir.name
                        transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
                        if transcript.exists():
                            fallback_title = f"活跃会话 {cid[:8]}"
                            try:
                                with open(transcript, "r", encoding="utf-8", errors="replace") as tf:
                                    first_ln = tf.readline().strip()
                                if first_ln:
                                    if '"SYSTEM_MESSAGE"' in first_ln:
                                        continue
                                    first_obj = json.loads(first_ln)
                                    if first_obj.get("type") == "USER_INPUT" and first_obj.get("content"):
                                        cleaned_first = re.sub(
                                            r"\s+", " ", clean_agy_user_content(str(first_obj["content"]))
                                        ).strip()
                                        if cleaned_first:
                                            fallback_title = cleaned_first[:48]
                            except Exception:
                                pass

                            stat = transcript.stat()
                            from datetime import datetime, timezone

                            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                            new_brain_map[cid] = UnifiedSession(
                                unified_id=f"{self.agent_type}:{cid}",
                                agent_type=self.agent_type,
                                agent_label=self.agent_label,
                                raw_session_id=cid,
                                title=fallback_title,
                                preview=fallback_title,
                                project_path="",
                                status="idle",
                                step_count=0,
                                updated_at=mtime_iso,
                            )
                except Exception as exc:
                    log_structured_error(
                        problem=f"扫描 {self.agent_label} brain 目录异常",
                        cause=str(exc),
                        fix="请检查 ~/.gemini 目录读取权限。",
                        exc=exc,
                    )
                self._brain_scan_cache = (now_mono, new_brain_map)
                cached_brain_map = new_brain_map

            for cid, b_sess in cached_brain_map.items():
                if cid not in sessions_map:
                    sessions_map[cid] = copy.copy(b_sess)

        sorted_list = sorted(sessions_map.values(), key=lambda s: s.updated_at, reverse=True)
        return sorted_list[:limit]

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """解析 brain/<id>/.system_generated/logs/transcript.jsonl 为结构化对话卡片流（带 mtime_ns 内存缓存）。"""
        transcript_path = (
            self.brain_dir / raw_session_id / ".system_generated" / "logs" / "transcript.jsonl"
        )
        if not transcript_path.exists():
            return []

        try:
            st = transcript_path.stat()
            mtime_ns, fsize = st.st_mtime_ns, st.st_size
        except OSError:
            mtime_ns, fsize = 0, 0

        cached = self._messages_cache.get(raw_session_id)
        if cached and cached[0] == mtime_ns and cached[1] == fsize:
            return cached[2][-limit:] if limit > 0 else list(cached[2])

        items: list[UnifiedMessageItem] = []
        # 记录最近一次 assistant 消息以便将紧随其后的 TOOL_RESPONSE 输出挂载到对应工具卡片上
        last_assistant_item: Optional[UnifiedMessageItem] = None

        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                raw_lines = f.readlines()
        except Exception as exc:
            log_structured_error(
                problem=f"读取 {self.agent_label} 会话日志失败 ({raw_session_id[:8]})",
                cause=str(exc),
                fix="系统将在下次文件刷新时自动重试。",
                exc=exc,
            )
            return []

        for idx, line in enumerate(raw_lines):
            line = line.strip()
            if not line:
                continue
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                # 遵循 MalformedJsonlLineError 规范：忽略正在刷盘的末尾半截 JSON 行
                continue

            step_index = int(step.get("step_index", idx))
            step_type = str(step.get("type") or "")
            source = str(step.get("source") or "")
            created_at = str(step.get("created_at") or "")
            status = str(step.get("status") or "DONE")
            content_raw = str(step.get("content") or "")
            thinking_raw = str(step.get("thinking") or "").strip()
            raw_tool_calls = step.get("tool_calls") or []

            if step_type == "USER_INPUT" or source == "USER_EXPLICIT":
                clean_text = clean_agy_user_content(content_raw)
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:step:{step_index}",
                        step_index=step_index,
                        role="user",
                        created_at=created_at,
                        content=clean_text,
                        status=status,
                    )
                )
            elif step_type == "ASK_QUESTION":
                # 用户在交互式提问模态框中选择的答案，提取 A1/A2 行展示为用户回答气泡
                ans_lines = [
                    ln.strip()
                    for ln in content_raw.splitlines()
                    if ln.strip() and not ln.startswith(("Created At:", "Completed At:"))
                ]
                ans_text = "\n".join(ans_lines).strip()
                if ans_text:
                    items.append(
                        UnifiedMessageItem(
                            item_id=f"{raw_session_id}:step:{step_index}",
                            step_index=step_index,
                            role="user",
                            created_at=created_at,
                            content=ans_text,
                            status=status,
                        )
                    )
            elif step_type in AGY_TOOL_OUTPUT_TYPES or "TOOL" in step_type:
                # 将工具执行输出回填到上一条 Assistant 消息的对应工具调用卡片上，严禁作为普通聊天气泡刷屏
                if content_raw and last_assistant_item and last_assistant_item.tool_calls:
                    trunc_out, is_trunc, full_len = truncate_large_output(content_raw)
                    placed = False
                    for tc in last_assistant_item.tool_calls:
                        if not tc.output:
                            tc.output = trunc_out
                            tc.is_truncated = is_trunc
                            tc.full_output_len = full_len
                            placed = True
                            break
                    if not placed:
                        last_tc = last_assistant_item.tool_calls[-1]
                        combined = f"{last_tc.output}\n---\n{trunc_out}" if last_tc.output else trunc_out
                        c_out, c_trunc, c_len = truncate_large_output(combined)
                        last_tc.output = c_out
                        last_tc.is_truncated = c_trunc or is_trunc
                        last_tc.full_output_len = last_tc.full_output_len + full_len
            elif step_type == "PLANNER_RESPONSE" or (
                source == "MODEL" and (content_raw or thinking_raw or raw_tool_calls)
            ):
                parsed_tools: list[UnifiedToolCall] = []
                extracted_question_md: list[str] = []
                parsed_questions: list[dict[str, Any]] = []
                for tc_idx, tc in enumerate(raw_tool_calls):
                    if not isinstance(tc, dict):
                        continue
                    t_name = str(tc.get("name") or tc.get("tool_name") or "tool")
                    t_args = tc.get("arguments") or tc.get("args") or tc.get("input") or {}
                    if t_name == "ask_question" and isinstance(t_args, dict):
                        q_items = extract_interactive_questions(t_name, t_args)
                        parsed_questions.extend(q_items)
                        first_q = q_items[0]["question"] if q_items else "交互式提问"
                        # 记录为正规工具调用，避免收纳栏工具计数为 0
                        parsed_tools.append(
                            UnifiedToolCall(
                                tool_id=str(tc.get("id") or f"tc_{step_index}_{tc_idx}"),
                                name=t_name,
                                args_summary=first_q[:120],
                                status=status,
                            )
                        )
                        continue
                    args_summary, diff_text = extract_tool_summary_and_diff(t_name, t_args)
                    f_path = extract_tool_file_path(t_args)
                    risk = classify_tool_risk(t_name, args_summary)
                    parsed_tools.append(
                        UnifiedToolCall(
                            tool_id=str(tc.get("id") or f"tc_{step_index}_{tc_idx}"),
                            name=t_name,
                            args_summary=args_summary,
                            diff_text=diff_text,
                            risk_level=risk,
                            status=status,
                            file_path=f_path,
                        )
                    )

                final_content = content_raw.strip()
                # 仅在既无正文且无结构化 questions 交互卡片时才回退拼接 Markdown，避免同一问题重复展示双份
                if not final_content and not parsed_questions and extracted_question_md:
                    final_content = "\n\n".join(extracted_question_md)

                msg_item = UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{step_index}",
                    step_index=step_index,
                    role="assistant",
                    created_at=created_at,
                    content=final_content,
                    thinking=thinking_raw,
                    tool_calls=parsed_tools,
                    questions=parsed_questions,
                    status=status,
                )
                items.append(msg_item)
                if parsed_tools:
                    last_assistant_item = msg_item

        self._messages_cache[raw_session_id] = (mtime_ns, fsize, items)
        return items[-limit:] if limit > 0 else items

    def send_prompt_via_ls(
        self, raw_session_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """封装下沉：尝试通过桌面端 Language Server Connect-RPC 直连下发指令。"""
        return self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_id)

    def cancel_via_ls(self, raw_session_id: str) -> bool:
        """封装下沉：尝试通过桌面端 Language Server Connect-RPC 取消会话。"""
        return self.ls_bridge.cancel_cascade(raw_session_id)

    def get_session_messages_page(
        self,
        raw_session_id: str,
        limit: int = 30,
        before_step: Optional[int] = None,
    ) -> tuple[list[UnifiedMessageItem], int, bool]:
        """
        利用内存态缓存结构直接进行增量/历史切片，大幅降低轮询时的 transcript.jsonl 重复解析开销。
        """
        all_items = self.get_session_messages(raw_session_id, limit=0)
        total_count = len(all_items)
        if before_step is not None:
            filtered = [it for it in all_items if it.step_index < before_step]
        else:
            filtered = all_items
        page_items = filtered[-limit:] if limit > 0 else filtered
        has_more = len(filtered) > len(page_items)
        return page_items, total_count, has_more

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:

        """按需分页/全量读取被 4KB 截断的单步原始输出。"""
        transcript_path = (
            self.brain_dir / raw_session_id / ".system_generated" / "logs" / "transcript.jsonl"
        )
        if not transcript_path.exists():
            return ""
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if int(data.get("step_index", idx)) == step_index:
                            return json.dumps(data, ensure_ascii=False, indent=2)
                    except Exception:
                        continue
        except Exception as exc:
            log_structured_error(
                problem="读取单步完整输出失败",
                cause=str(exc),
                fix="请确认 transcript.jsonl 文件存在。",
                exc=exc,
            )
        return ""

    def send_ide_message(
        self, raw_session_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """封装调用内部 language server bridge 发送 IDE 级消息（消除 Feature Envy）。"""
        return self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_id)

    def cancel_ide_session(self, raw_session_id: str) -> bool:
        """封装调用内部 language server bridge 取消 IDE 级会话执行（消除 Feature Envy）。"""
        return self.ls_bridge.cancel_cascade(raw_session_id)

    def try_direct_dispatch(
        self,
        raw_session_id: str,
        prompt: str,
        model_name: str = "",
        workdir: str = "",
    ) -> Optional[dict[str, Any]]:
        """若为 Antigravity IDE 且桌面端 LS 在线，通过 Connect-RPC 直发指令，避免克隆开销。"""
        if self.agent_type == "agy_ide":
            if self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_name):
                return {"via_ls_rpc": True}
        return None

    def cancel_session(self, raw_session_id: str) -> bool:
        """若为 Antigravity IDE 会话，通过 Connect-RPC 取消正在运行的 Cascade 任务。"""
        if self.agent_type == "agy_ide":
            try:
                return self.ls_bridge.cancel_cascade(raw_session_id)
            except Exception:
                return False
        return False

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """为 Antigravity CLI 组装流式命令行。"""
        clean_model = (model_name or "").strip()
        if clean_model == "default":
            clean_model = ""
        bin_path = str(self.cli_bin or "agy")
        cmd = [
            bin_path,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--conversation",
            raw_session_id,
        ]
        if clean_model:
            cmd.extend(["--model", clean_model])
        if execution_mode == "plan":
            cmd.extend(["--mode", "plan"])
        elif execution_mode == "accept_edits":
            cmd.extend(["--mode", "accept-edits"])
        elif execution_mode == "auto":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd.extend(["--mode", "accept-edits"])
        return cmd

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """从 Antigravity CLI stream-json 帧中提取待审批工具调用。"""
        tool_blocks: list[dict[str, Any]] = []
        raw_calls = event.get("tool_calls")
        if not raw_calls and isinstance(event.get("step"), dict):
            raw_calls = event["step"].get("tool_calls")
        if isinstance(raw_calls, list):
            for tc in raw_calls:
                if isinstance(tc, dict):
                    tool_blocks.append(
                        {
                            "id": str(tc.get("id") or tc.get("tool_id") or ""),
                            "name": str(tc.get("name") or tc.get("tool_name") or ""),
                            "input": tc.get("args") or tc.get("arguments") or tc.get("input") or {},
                        }
                    )

        # 兼容标准 tool_use 块与权限请求帧
        msg = event.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_blocks.append(b)
        ev_type = str(event.get("type") or "")
        if ev_type in ("permission_request", "control_request", "tool_call"):
            tool_blocks.append(event)
        return tool_blocks



def fork_ide_session_to_cli(
    raw_session_id: str,
    ide_root: Optional[Path] = None,
    cli_root: Optional[Path] = None,
) -> tuple[bool, str]:
    """
    安全克隆接力（Fork-to-CLI）：
    将 `Antigravity IDE` (`~/.gemini/antigravity`) 中的指定会话文件（`conversations/<id>.db`、
    `brain/<id>` 及 `conversation_summaries` 索引行）单向安全同步至 `Antigravity CLI` (`~/.gemini/antigravity-cli`)。

    这样既解决了 `agy.exe` 无 `--data-dir` 参数无法直接找到 IDE 会话的问题，
    又彻底杜绝了外部 CLI 进程直接改写桌面端 IDE 正在占用的 SQLite 数据库引发的双写脑裂！
    """
    src_root = ide_root or Path(os.path.expanduser("~/.gemini/antigravity"))
    dst_root = cli_root or Path(os.path.expanduser("~/.gemini/antigravity-cli"))

    try:
        (dst_root / "conversations").mkdir(parents=True, exist_ok=True)
        (dst_root / "brain").mkdir(parents=True, exist_ok=True)

        # 1. 拷贝 conversations/<id>.db
        src_conv_db = src_root / "conversations" / f"{raw_session_id}.db"
        dst_conv_db = dst_root / "conversations" / f"{raw_session_id}.db"
        if src_conv_db.exists():
            shutil.copy2(src_conv_db, dst_conv_db)

        # 2. 同步 brain/<id> 目录
        src_brain = src_root / "brain" / raw_session_id
        dst_brain = dst_root / "brain" / raw_session_id
        if src_brain.exists():
            shutil.copytree(src_brain, dst_brain, dirs_exist_ok=True)

        # 3. 同步 conversation_summaries.db 中的元数据行
        src_sum_db = src_root / "conversation_summaries.db"
        dst_sum_db = dst_root / "conversation_summaries.db"
        if src_sum_db.exists() and dst_sum_db.exists():
            rows = safe_query_external_sqlite(
                src_sum_db,
                "SELECT * FROM conversation_summaries WHERE conversation_id = ?",
                (raw_session_id,),
            )
            if rows:
                row = rows[0]
                cols = list(row.keys())
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join([f"`{c}`" for c in cols])
                conn = sqlite3.connect(str(dst_sum_db), timeout=3.0)
                try:
                    conn.execute("PRAGMA busy_timeout = 3000;")
                    conn.execute(
                        f"INSERT OR REPLACE INTO conversation_summaries ({col_names}) VALUES ({placeholders})",
                        tuple(row[c] for c in cols),
                    )
                    conn.commit()
                finally:
                    conn.close()

        logger.info(f"已将 Antigravity IDE 会话 {raw_session_id[:8]} 安全克隆同步至 AGY CLI 环境")
        return True, f"agy_cli:{raw_session_id}"
    except Exception as exc:
        msg = log_structured_error(
            problem=f"克隆 Antigravity IDE 会话 ({raw_session_id[:8]}) 至 CLI 失败",
            cause=str(exc),
            fix="请确认 ~/.gemini/antigravity-cli 目录可写且磁盘空间充足。",
            exc=exc,
        )
        return False, msg


CSRF_TOKEN_RE = re.compile(r"--csrf_token[\s=]+([0-9a-fA-F-]{8,})")
LS_SERVICE_PREFIX = "/exa.language_server_pb.LanguageServerService"


def encode_connect_envelope(body: dict[str, Any]) -> bytes:
    """编码 Connect-RPC 5 字节流式协议帧 (`[0x00][uint32_be_len][json_bytes]`)。"""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return b"\x00" + len(raw).to_bytes(4, "big") + raw



class AntigravityLanguageServerBridge:
    """
    借鉴 `Antigravity-Deck` (`src/detector.js` & `src/ls-stream.js`) 的轻量级 Hub Language Server 桥接器：
    1. 自动嗅探本机运行中的 `language_server` 进程 `--csrf_token` 与本地监听端口（带 15s 内存缓存）。
    2. 支持通过 Connect-RPC 协议直接向桌面端活跃的 Antigravity IDE 会话发送指令与取消任务，
       实现手机与电脑桌面 IDE 窗口 100% 同步（当桌面端 IDE 未开启时自动回退至 `fork_ide_session_to_cli`）。
    """

    def __init__(self, cache_ttl_sec: float = 15.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self._cached_hub: Optional[dict[str, Any]] = None
        self._last_detect_ts: float = 0.0

    def get_cached_hub(self) -> Optional[dict[str, Any]]:
        """非阻塞获取已缓存的 Hub LS 连接信息。"""
        return self._cached_hub

    def detect_hub(self, force: bool = False) -> Optional[dict[str, Any]]:
        """探测本机运行中的 Antigravity Hub Language Server 端口与 CSRF Token。"""
        now = time.monotonic()
        if not force and self._last_detect_ts > 0 and (now - self._last_detect_ts) < self.cache_ttl_sec:
            return self._cached_hub

        self._last_detect_ts = now
        candidates = self._scan_ls_processes()
        if not candidates:
            self._cached_hub = None
            return None

        for pid, csrf_token in candidates:
            ports = self._find_listening_ports(pid)
            for port in ports:
                scheme = self._probe_connect_port(port, csrf_token)
                if scheme:
                    self._cached_hub = {
                        "pid": pid,
                        "port": port,
                        "csrf_token": csrf_token,
                        "scheme": scheme,
                    }
                    return self._cached_hub

        self._cached_hub = None
        return None

    @staticmethod
    def _scan_ls_processes() -> list[tuple[int, str]]:
        """扫描进程表提取 (pid, csrf_token)（兼容全系 Windows 自带的 powershell.exe 与 pwsh）。"""
        results: list[tuple[int, str]] = []
        try:
            if sys.platform == "win32":
                ps_bin = (
                    shutil.which("powershell.exe")
                    or os.path.expandvars(r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
                    or shutil.which("pwsh")
                    or "powershell"
                )
                ps_cmd = (
                    "Get-CimInstance Win32_Process -Filter \"Name LIKE '%language_server%'\" "
                    "| Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
                )
                proc = subprocess.run(
                    [ps_bin, "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2.5,
                )
                out = (proc.stdout or "").strip()
                if out:
                    data = json.loads(out)
                    rows = data if isinstance(data, list) else [data]
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        pid = int(r.get("ProcessId") or 0)
                        cmdline = str(r.get("CommandLine") or "")
                        m = CSRF_TOKEN_RE.search(cmdline)
                        if pid > 0 and m:
                            results.append((pid, m.group(1)))
            else:
                proc = subprocess.run(
                    ["ps", "-eo", "pid,args"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2.0,
                )
                for line in (proc.stdout or "").splitlines():
                    if "language_server" not in line and "csrf_token" not in line:
                        continue
                    m = CSRF_TOKEN_RE.search(line)
                    parts = line.strip().split(None, 1)
                    if m and parts and parts[0].isdigit():
                        results.append((int(parts[0]), m.group(1)))
        except Exception as exc:
            logger.debug(f"扫描 language_server 进程信息已跳过: {exc}")
        return results

    @staticmethod
    def _find_listening_ports(pid: int) -> list[int]:
        """查找指定 PID 在本机监听的 TCP 端口列表。"""
        ports: list[int] = []
        try:
            if sys.platform == "win32":
                proc = subprocess.run(
                    ["netstat", "-ano", "-p", "TCP"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2.0,
                )
                pid_str = str(pid)
                for line in (proc.stdout or "").splitlines():
                    if "LISTENING" not in line:
                        continue
                    cols = line.split()
                    if len(cols) >= 5 and cols[-1] == pid_str:
                        local_addr = cols[1]
                        if ":" in local_addr:
                            port_str = local_addr.rsplit(":", 1)[-1]
                            if port_str.isdigit():
                                port_int = int(port_str)
                                if port_int not in ports:
                                    ports.append(port_int)
        except Exception as exc:
            logger.debug(f"查询 PID={pid} 端口失败: {exc}")
        return ports[:6]

    def _probe_connect_port(self, port: int, csrf_token: str) -> Optional[str]:
        """探测指定端口是否响应 Connect-RPC `GetUserStatus`，返回 `'https'` 或 `'http'`。"""
        for scheme in ("https", "http"):
            ok, _ = self._raw_post(
                scheme=scheme,
                port=port,
                csrf_token=csrf_token,
                method="GetUserStatus",
                body={},
                timeout=0.8,
            )
            if ok:
                return scheme
        return None

    @staticmethod
    def _raw_post(
        scheme: str,
        port: int,
        csrf_token: str,
        method: str,
        body: dict[str, Any],
        timeout: float = 2.0,
    ) -> tuple[bool, Any]:
        url = f"{scheme}://127.0.0.1:{port}{LS_SERVICE_PREFIX}/{method}"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "X-Codeium-Csrf-Token": csrf_token,
            },
        )
        ctx = ssl._create_unverified_context() if scheme == "https" else None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 300:
                    raw_resp = resp.read(4096)
                    return True, raw_resp
        except Exception as exc:
            logger.debug(f"Connect-RPC POST {method} 探测失败 ({url}): {exc}")
        return False, None

    def send_cascade_message(
        self, cascade_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """
        通过 Connect-RPC 直接向运行中的 Antigravity IDE 会话发送用户指令。
        成功返回 True；若桌面端 IDE 未开启或会话不受该 LS 管辖则返回 False（上层自动回退 CLI）。
        """
        hub = self.detect_hub()
        if not hub:
            return False

        payload: dict[str, Any] = {
            "cascadeId": cascade_id,
            "items": [{"text": prompt}],
        }
        if model_id and model_id != "default":
            payload["cascadeConfig"] = {
                "plannerConfig": {
                    "requestedModel": {"modelOrAlias": model_id},
                }
            }

        ok, _ = self._raw_post(
            scheme=hub["scheme"],
            port=hub["port"],
            csrf_token=hub["csrf_token"],
            method="SendUserCascadeMessage",
            body=payload,
            timeout=2.5,
        )
        if ok:
            logger.info(
                f"已通过 Language Server Connect-RPC (port={hub['port']}) 直接向 IDE 会话 {cascade_id[:8]} 发送指令"
            )
            return True
        log_structured_error(
            problem=f"通过 Connect-RPC 向 IDE 发送指令失败 (ID={cascade_id[:8]})",
            cause="Language Server 无响应或未在监听",
            fix="系统将自动回退至 Fork-to-CLI 安全克隆接力执行。",
        )
        return False

    def cancel_cascade(self, cascade_id: str) -> bool:
        """通过 Connect-RPC 取消运行中的 Antigravity IDE 会话。"""
        hub = self.detect_hub()
        if not hub:
            return False
        ok, _ = self._raw_post(
            scheme=hub["scheme"],
            port=hub["port"],
            csrf_token=hub["csrf_token"],
            method="CancelCascadeInvocation",
            body={"cascadeId": cascade_id},
            timeout=2.0,
        )
        if not ok:
            log_structured_error(
                problem=f"通过 Connect-RPC 取消 IDE 会话失败 (ID={cascade_id[:8]})",
                cause="Language Server 无响应或已离线",
                fix="系统将通过本地强杀进程树兜底处理。",
            )
        return ok
