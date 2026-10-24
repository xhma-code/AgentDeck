"""
工作区 Git 状态只读巡检、分支状态与 Unified Diff 提取。
"""

from __future__ import annotations

import difflib
import subprocess
import time
from pathlib import Path
from typing import Any

from backend.core.logger import log_structured_error, logger

_GIT_STATUS_CACHE: dict[str, tuple[float, tuple[int, int, int], dict[str, Any]]] = {}


def get_git_repo_sig(repo_dir: Path) -> tuple[int, int, int]:
    """获取 git 关键状态元文件 (HEAD, index 与工作区根目录) 的 mtime_ns 签名。"""
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        return (0, 0, 0)
    try:
        if git_dir.is_file():
            content = git_dir.read_text(encoding="utf-8", errors="replace").strip()
            if content.startswith("gitdir:"):
                actual_dir = (repo_dir / content.split(":", 1)[1].strip()).resolve()
                git_dir = actual_dir
        head_mtime = (git_dir / "HEAD").stat().st_mtime_ns if (git_dir / "HEAD").exists() else 0
        idx_mtime = (git_dir / "index").stat().st_mtime_ns if (git_dir / "index").exists() else 0
        repo_mtime = repo_dir.stat().st_mtime_ns
        return (head_mtime, idx_mtime, repo_mtime)
    except OSError:
        return (0, 0, 0)


def get_git_workspace_status(raw_project_path: str) -> dict[str, Any]:
    """
    轻量只读查询指定工作区的 Git 分支与未提交变更文件列表（带 0.8s 防抖签名缓存与单次 status -b 解析，严格只读）。
    """
    from backend.core.workspace import decode_workspace_uri

    norm_path = decode_workspace_uri(raw_project_path)
    if not norm_path:
        return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}

    repo_dir = Path(norm_path)
    if not repo_dir.is_dir():
        return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}

    now_mono = time.monotonic()
    sig = get_git_repo_sig(repo_dir)
    cache_key = str(repo_dir.resolve())
    cached = _GIT_STATUS_CACHE.get(cache_key)
    if cached and (now_mono - cached[0] < 0.8) and cached[1] == sig:
        return cached[2]

    try:
        status_proc = subprocess.run(
            ["git", "-C", str(repo_dir), "-c", "core.quotepath=false", "status", "-b", "--porcelain=v1", "-uall"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4.0,
        )
        if status_proc.returncode != 0:
            res = {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}
            _GIT_STATUS_CACHE[cache_key] = (now_mono, sig, res)
            return res

        status_lines = (status_proc.stdout or "").splitlines()
        branch = "HEAD"
        if status_lines and status_lines[0].startswith("## "):
            branch_info = status_lines[0][3:].strip()
            branch = branch_info.split("...", 1)[0].split(" ", 1)[0]
            status_lines = status_lines[1:]

        stat_map: dict[str, tuple[int, int]] = {}
        if status_lines:
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

            for ln in (numstat_proc.stdout or "").splitlines():
                parts = ln.split("\t")
                if len(parts) >= 3:
                    add_s, del_s, f_rel = parts[0], parts[1], parts[2].strip().strip('"')
                    add_n = int(add_s) if add_s.isdigit() else 0
                    del_n = int(del_s) if del_s.isdigit() else 0
                    stat_map[f_rel] = (add_n, del_n)

        files: list[dict[str, Any]] = []
        for raw_line in status_lines:
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

        res = {
            "ok": True,
            "is_git_repo": True,
            "project_path": norm_path,
            "branch": branch,
            "changed_count": len(files),
            "files": files[:80],
        }
        _GIT_STATUS_CACHE[cache_key] = (now_mono, sig, res)
        return res
    except Exception as exc:
        log_structured_error(
            problem=f"获取工作区 Git 状态异常 ({raw_project_path})",
            cause=str(exc),
            fix="请检查工作区目录读取权限或是否包含有效 Git 仓库元数据。",
            exc=exc,
        )
        return {"ok": True, "is_git_repo": False, "branch": "", "changed_count": 0, "files": []}


def get_git_workspace_diff(raw_project_path: str, file_path: str = "") -> dict[str, Any]:
    """
    轻量只读获取工作区单文件或全局未提交 Unified Diff（含未跟踪新文件预览，严格限制路径边界）。
    """
    from backend.core.workspace import decode_workspace_uri

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
                            u_diff = f"\ndiff --git/{u_path} b/{u_path}\nnew file mode 100644\n" + "".join(gen)
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
