"""
Claude Code (`~/.claude/projects/<slug>/<session_id>.jsonl`) 适配器。

核心能力：
1. 扫描 `~/.claude/projects` 下所有项目目录的 `.jsonl` 会话文件。
2. 解析 `user` 与 `assistant` 消息（含 `thinking` 块、`tool_use` 块、`tool_result` 块及 `Edit` 单列 Unified Diff）。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timezone
import time
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    BoundedLRUCache,
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    classify_tool_risk,
    count_diff_lines,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    resolve_model_context_limit,
    truncate_large_output,
    truncate_tool_output,
)
from backend.core.logger import logger, log_structured_error

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

    def __init__(
        self,
        projects_dir: Optional[Path] = None,
        sessions_dir: Optional[Path] = None,
    ) -> None:
        super().__init__()
        default_projects = Path(os.path.expanduser("~/.claude/projects"))
        default_sessions = Path(os.path.expanduser("~/.claude/sessions"))
        self.projects_dir = projects_dir or default_projects
        if sessions_dir:
            self.sessions_dir = sessions_dir
        elif projects_dir and projects_dir != default_projects:
            # 隔离测试环境自动绑定独立目录，杜绝读取全局会话文件
            self.sessions_dir = projects_dir.parent / "sessions"
        else:
            self.sessions_dir = default_sessions

        self.cli_bin = (
            shutil.which("claude")
            or os.path.expanduser(r"~\.local\bin\claude.exe")
        )
        # fpath_str -> (mtime_ns, file_size, UnifiedSession)
        self._session_meta_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        # raw_session_id -> Path
        self._session_file_map: BoundedLRUCache = BoundedLRUCache(maxsize=200)
        # raw_session_id -> (mtime_ns, file_size, list[UnifiedMessageItem])
        self._messages_cache: BoundedLRUCache = BoundedLRUCache(maxsize=40)
        # cache_key -> (mtime_ns, token_usage_dict)
        self._token_usage_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        # 扫描文件候选路径短效缓存 (monotonic_ts, list[Path])，防高频 rglob 磁盘遍历风暴
        self._dir_scan_cache: tuple[float, list[Path]] = (0.0, [])

    def get_active_sessions(self) -> dict[str, str]:
        """获取当前由外部运行的 Claude Code 进程活跃状态字典（读取 ~/.claude/sessions/*.json）。"""
        active_map: dict[str, str] = {}
        if not self.sessions_dir.exists():
            return active_map

        import psutil
        try:
            for p in self.sessions_dir.glob("*.json"):
                try:
                    raw = p.read_text(encoding="utf-8", errors="replace")
                    doc = json.loads(raw)
                    pid = doc.get("pid")
                    sid = str(doc.get("sessionId") or "")
                    if not sid:
                        continue
                    # 校验进程存活
                    if pid and not psutil.pid_exists(pid):
                        continue
                    raw_st = str(doc.get("status") or "").lower()
                    if raw_st in ("busy", "running"):
                        active_map[sid] = "running"
                    elif raw_st == "idle":
                        active_map[sid] = "desktop_active"
                except Exception:
                    continue
        except Exception as exc:
            logger.debug(f"扫描 Claude 活跃状态会话异常: {exc}")
        return active_map

    def is_external_session_active(self, raw_session_id: str) -> bool:
        """轻量探测指定 Claude Code 会话是否处于外部运行/活跃态。"""
        active_map = self.get_active_sessions()
        st = active_map.get(raw_session_id)
        if st in ("running", "desktop_active"):
            return True

        # 双保险：检查会话文件修改时间（最近 20 秒内在持续写入）
        fpath = self._find_session_file(raw_session_id)
        if fpath and fpath.exists():
            try:
                mtime = fpath.stat().st_mtime
                if time.time() - mtime < 20:
                    return True
            except OSError:
                pass
        return False

    def invalidate_cache(self) -> None:
        """重置目录扫描短效缓存（多态实现 BaseAgentAdapter 契约）。"""
        self._dir_scan_cache = (0.0, [])

    def invalidate_scan_cache(self) -> None:
        self.invalidate_cache()

    def is_available(self) -> tuple[bool, str]:
        has_bin = Path(str(self.cli_bin)).exists() if self.cli_bin else False
        has_dir = self.projects_dir.exists()
        if has_bin or has_dir:
            cnt = len(self._cached_sessions) if hasattr(self, "_cached_sessions") and self._cached_sessions else 0
            if cnt == 0 and has_dir:
                try:
                    cnt = sum(1 for p in self.projects_dir.glob("*/*.jsonl"))
                except OSError as err:
                    logger.debug("统计 Claude projects 目录会话文件失败: %s", err)
                    cnt = 0
            return (
                True,
                f"Claude Code 已就绪: 发现 {cnt} 个会话 (stream-json + stdio 逐条权限拦截)",
            )
        return False, "未检测到 claude.exe 或 ~/.claude/projects 目录"

    def get_watch_paths(self) -> list[Path]:
        paths = []
        if self.projects_dir.exists():
            paths.append(self.projects_dir)
        if self.sessions_dir.exists():
            paths.append(self.sessions_dir)
        return paths

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
        """扫描 ~/.claude/projects 下所有 .jsonl 文件并提取会话元数据（带 mtime_ns 内存缓存与短效扫描缓存）。"""
        if not self.projects_dir.exists():
            return []

        now_mono = time.monotonic()
        if now_mono - self._dir_scan_cache[0] < 2.5 and self._dir_scan_cache[1]:
            candidate_paths = self._dir_scan_cache[1]
        else:
            candidate_paths = []
            try:
                for fpath in self.projects_dir.rglob("*.jsonl"):
                    if not fpath.name.startswith("agent-"):
                        candidate_paths.append(fpath)
                self._dir_scan_cache = (now_mono, candidate_paths)
            except Exception as exc:
                log_structured_error(
                    problem="扫描 Claude Code 会话目录失败",
                    cause=str(exc),
                    fix="请检查 ~/.claude/projects 目录权限。",
                    exc=exc,
                )
                return []

        files: list[tuple[float, int, int, Path]] = []
        for fpath in candidate_paths:
            try:
                st = fpath.stat()
                self._session_file_map[fpath.stem] = fpath
                files.append((st.st_mtime, st.st_mtime_ns, st.st_size, fpath))
            except OSError:
                continue

        files.sort(key=lambda x: x[0], reverse=True)
        sessions: list[UnifiedSession] = []
        active_map = self.get_active_sessions()
        now_epoch = time.time()

        for mtime, mtime_ns, fsize, fpath in files[:limit]:
            f_key = str(fpath)
            cached = self._session_meta_cache.get(f_key)
            if cached and cached[0] == mtime_ns and cached[1] == fsize:
                s_copy = copy.copy(cached[2])
                sid_cached = s_copy.raw_session_id
                if sid_cached in active_map:
                    s_copy.status = active_map[sid_cached]
                elif s_copy.status in ("running", "desktop_active") and sid_cached not in active_map:
                    s_copy.status = "idle"
                sessions.append(s_copy)
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

            status = active_map.get(sid, "idle")
            if status == "idle" and (now_epoch - mtime < 15):
                status = "running"

            sess_obj = UnifiedSession(
                unified_id=f"{self.agent_type}:{sid}",
                agent_type=self.agent_type,
                agent_label=self.agent_label,
                raw_session_id=sid,
                title=title,
                preview=preview,
                project_path=cwd or fpath.parent.name,
                status=status,
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

    def _parse_claude_record_line(
        self,
        line: str,
        raw_session_id: str,
        items: list[UnifiedMessageItem],
        tool_id_map: dict[str, UnifiedToolCall],
        step_idx_container: list[int],
    ) -> bool:
        """解析单行 Claude Code JSONL 记录并更新 items 和 tool_id_map。"""
        line = line.strip()
        if not line:
            return False
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return False

        rec_type = rec.get("type")
        if rec_type not in ("user", "assistant"):
            return False

        msg = rec.get("message")
        if not isinstance(msg, dict):
            return False

        created_at = str(rec.get("timestamp") or "")
        content = msg.get("content")

        def _append_cleaned_user_text(raw_txt: str, created_at_str: str) -> None:
            kind, cleaned = clean_claude_user_text(raw_txt)
            if kind == "ignore" or not cleaned:
                return
            if kind == "slash_expanded":
                if items and items[-1].role == "user" and items[-1].content.startswith("/"):
                    return
            if kind == "task_notification":
                step_idx_container[0] += 1
                cur_step = step_idx_container[0]
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:step:{cur_step}",
                        step_index=cur_step,
                        role="assistant",
                        created_at=created_at_str,
                        content="",
                        tool_calls=[
                            UnifiedToolCall(
                                tool_id=f"task_notif_{cur_step}",
                                name="TaskNotification",
                                args_summary=cleaned,
                                status="DONE",
                            )
                        ],
                    )
                )
                return

            step_idx_container[0] += 1
            cur_step = step_idx_container[0]
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{cur_step}",
                    step_index=cur_step,
                    role="user",
                    created_at=created_at_str,
                    content=cleaned,
                )
            )

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
                            target_tc = tool_id_map[tid]
                            trunc_out, is_trunc, full_len = truncate_tool_output(
                                target_tc.name, str(res_str)
                            )
                            target_tc.output = trunc_out
                            target_tc.is_truncated = is_trunc
                            target_tc.full_output_len = full_len
                if user_texts:
                    _append_cleaned_user_text("\n".join(user_texts), created_at)
            return True

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
                        tid = str(block.get("id") or f"tc_{step_idx_container[0]}")
                        tname = str(block.get("name") or "Tool")
                        tinput = block.get("input") or {}
                        q_items = extract_interactive_questions(tname, tinput)
                        if q_items:
                            parsed_questions.extend(q_items)
                            continue
                        summary, diff_txt = extract_tool_summary_and_diff(tname, tinput)
                        add_c, del_c = count_diff_lines(diff_txt)
                        f_path = extract_tool_file_path(tinput)
                        risk = classify_tool_risk(tname, summary)
                        tc_obj = UnifiedToolCall(
                            tool_id=tid,
                            name=tname,
                            args_summary=summary,
                            diff_text=diff_txt,
                            diff_add_count=add_c,
                            diff_del_count=del_c,
                            risk_level=risk,
                            file_path=f_path,
                        )
                        tool_calls.append(tc_obj)
                        tool_id_map[tid] = tc_obj

            if text_parts or thinking_parts or tool_calls or parsed_questions:
                step_idx_container[0] += 1
                cur_step = step_idx_container[0]
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:step:{cur_step}",
                        step_index=cur_step,
                        role="assistant",
                        created_at=created_at,
                        content="\n\n".join(text_parts),
                        thinking="\n\n".join(thinking_parts),
                        tool_calls=tool_calls,
                        questions=parsed_questions,
                    )
                )
                return True
        return False

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """解析指定 Claude Code 会话的 JSONL 为统一消息流（带 mtime_ns 与文件游标增量缓存）。"""
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

        # 增量 Seek 读取判定：文件发生追加写且先前已有合法读取 offset
        if (
            cached
            and len(cached) >= 6
            and fsize > cached[1]
            and cached[3] > 0
        ):
            try:
                items = list(cached[2])
                step_idx_container = [int(cached[4])]
                tool_id_map = dict(cached[5])
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(cached[3])
                    inc_lines = f.readlines()
                    new_offset = f.tell()

                if inc_lines and not inc_lines[-1].endswith(("\n", "\r")):
                    incomplete_len = len(inc_lines[-1].encode("utf-8", errors="replace"))
                    inc_lines = inc_lines[:-1]
                    new_offset = max(cached[3], new_offset - incomplete_len)

                for ln in inc_lines:
                    self._parse_claude_record_line(
                        line=ln,
                        raw_session_id=raw_session_id,
                        items=items,
                        tool_id_map=tool_id_map,
                        step_idx_container=step_idx_container,
                    )

                self._messages_cache[raw_session_id] = (
                    mtime_ns,
                    fsize,
                    items,
                    new_offset,
                    step_idx_container[0],
                    tool_id_map,
                )
                return items[-limit:] if limit > 0 else items
            except Exception as inc_exc:
                logger.debug(f"增量读取 Claude Code 会话 {raw_session_id[:8]} 异常，回退全量解析: {inc_exc}")

        # 全量冷读取
        items: list[UnifiedMessageItem] = []
        tool_id_map: dict[str, UnifiedToolCall] = {}
        step_idx_container = [0]
        byte_offset = 0

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                byte_offset = f.tell()
            if lines and not lines[-1].endswith(("\n", "\r")):
                incomplete_len = len(lines[-1].encode("utf-8", errors="replace"))
                lines = lines[:-1]
                byte_offset = max(0, byte_offset - incomplete_len)
        except Exception as exc:
            log_structured_error(
                problem=f"读取 Claude Code 会话文件失败 ({raw_session_id[:8]})",
                cause=str(exc),
                fix="请确认 ~/.claude/projects 下的会话文件未被独占锁定。",
                exc=exc,
            )
            return []

        for line in lines:
            self._parse_claude_record_line(
                line=line,
                raw_session_id=raw_session_id,
                items=items,
                tool_id_map=tool_id_map,
                step_idx_container=step_idx_container,
            )

        self._messages_cache[raw_session_id] = (
            mtime_ns,
            fsize,
            items,
            byte_offset,
            step_idx_container[0],
            tool_id_map,
        )
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
        if sys.platform == "win32" and bin_path.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", bin_path]
        else:
            cmd = [bin_path]
        cmd.extend([
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ])
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

    def get_session_token_usage(
        self, raw_session_id: str, model_name: str = ""
    ) -> dict[str, Any]:
        """解析 Claude Code 会话 JSONL 文件提取 token 统计（带 mtime 内存缓存）。"""
        limit = resolve_model_context_limit(model_name)
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return SessionTokenUsage(context_limit=limit).to_dict()

        cache_key = str(fpath)
        try:
            mtime = fpath.stat().st_mtime_ns
        except OSError as err:
            logger.debug("获取 Claude 会话文件 mtime 失败: %s", err)
            mtime = 0

        cached = self._token_usage_cache.get(cache_key)
        if cached and cached[0] == mtime:
            res = dict(cached[1])
            res["context_limit"] = limit
            res["context_percent"] = (
                round((res["context_tokens"] / limit) * 100, 1) if limit > 0 else 0.0
            )
            return res

        latest_input = 0
        latest_output = 0
        latest_cached = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                try:
                    fsize = fpath.stat().st_size
                    if fsize > 512 * 1024:
                        f.seek(fsize - 256 * 1024)
                        f.readline()
                except OSError:
                    pass

                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        usage = record.get("usage") or (record.get("message") or {}).get("usage")
                        if isinstance(usage, dict):
                            in_tok = usage.get("input_tokens") or 0
                            out_tok = usage.get("output_tokens") or 0
                            cr_tok = usage.get("cache_read_input_tokens") or 0
                            cc_tok = usage.get("cache_creation_input_tokens") or 0
                            if in_tok or out_tok or cr_tok:
                                latest_input = in_tok
                                latest_output = out_tok
                                latest_cached = cr_tok + cc_tok
                                total_input += in_tok + cc_tok
                                total_output += out_tok
                                total_cache_read += cr_tok
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.debug(f"读取 Claude 会话 JSONL 跳过: {exc}")

        latest_context = latest_input + latest_cached
        pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
        data = {
            "context_tokens": latest_context,
            "context_limit": limit,
            "context_percent": pct,
            "total_tokens": total_input + total_output,
            "last_output_tokens": latest_output,
            "cache_read_tokens": total_cache_read,
        }
        self._token_usage_cache[cache_key] = (mtime, data)
        return data

