"""
工作区目录探测、Git 状态只读巡检与项目快捷工作流发现模块。

职责划分：
1. 识别真正工程项目特征（`.git`, `pyproject.toml`, `package.json` 等）与缓存过滤系统临时目录。
2. 轻量只读查询指定工作区的 Git 分支、未提交变更文件列表与 Unified Diff。
3. 发现项目根目录下的自定义工作流（`.agent/workflows/*.md`, `.claude/commands/*.md`）与高频快捷动作。
"""

from __future__ import annotations

import difflib
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from backend.core.logger import logger

# 目录工程特征磁盘探测缓存：norm_key -> (expire_ts, has_marker, real_folder_name)
_DIR_MARKER_CACHE: dict[str, tuple[float, bool, str]] = {}

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
                old_lines: list[str] = []
                new_lines = raw_content.splitlines(keepends=True)
                diff_gen = difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{clean_rel}",
                    tofile=f"b/{clean_rel}",
                    n=3,
                )
                diff_text = f"diff --git a/{clean_rel} b/{clean_rel}\nnew file mode 100644\n" + "".join(diff_gen)

        # 若未指定单文件（查看汇总 diff），为未跟踪新文件追加新建 diff 预览
        max_chars = 48000
        if not clean_rel and len(diff_text) < max_chars:
            try:
                st_proc = subprocess.run(
                    ["git", "-C", str(repo_dir), "status", "--porcelain", "-uall"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3.0,
                )
                for line in (st_proc.stdout or "").splitlines():
                    if line.startswith("?? "):
                        u_path = line[3:].strip().strip('"').replace("\\", "/")
                        u_abs = (repo_dir / u_path).resolve()
                        if u_abs.is_file() and u_abs.is_relative_to(repo_dir) and u_abs.stat().st_size <= 64 * 1024:
                            content = u_abs.read_text(encoding="utf-8", errors="replace")
                            lines = content.splitlines(keepends=True)
                            gen = difflib.unified_diff(
                                [],
                                lines,
                                fromfile=f"a/{u_path}",
                                tofile=f"b/{u_path}",
                                n=3,
                            )
                            u_diff = f"\ndiff --git a/{u_path} b/{u_path}\nnew file mode 100644\n" + "".join(gen)
                            diff_text = (diff_text + u_diff).strip()
                            if len(diff_text) >= max_chars:
                                break
            except Exception as exc:
                logger.debug(f"提取未跟踪文件 diff 预览跳过: {exc}")
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
