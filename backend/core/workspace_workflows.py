"""
工作区项目级与全局 Skills / 斜杠指令发现模块。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# 快捷工作流与 Skills 缓存：norm_key -> (expire_ts, items)
_WORKFLOWS_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}


def parse_skill_meta(skill_dir: Path) -> tuple[str, str]:
    """从技能目录及其 SKILL.md 解析技能名称与描述说明。"""
    skill_file = skill_dir / "SKILL.md"
    name = skill_dir.name
    desc = ""
    if skill_file.is_file():
        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            if lines and lines[0].strip() == "---":
                capturing_desc = False
                desc_lines = []
                for line in lines[1:]:
                    s = line.strip()
                    if s == "---":
                        break
                    if s.startswith("name:"):
                        name = s.split(":", 1)[1].strip().strip("\"'")
                        capturing_desc = False
                    elif s.startswith("description:"):
                        raw_val = s.split(":", 1)[1].strip().strip("\"'")
                        desc_lines = [raw_val] if raw_val else []
                        capturing_desc = True
                    elif capturing_desc:
                        if line.startswith(" ") or line.startswith("\t"):
                            desc_lines.append(s.strip("\"'"))
                        else:
                            capturing_desc = False
                if desc_lines:
                    desc = " ".join(desc_lines).strip()
            if not desc:
                for line in lines:
                    s = line.strip()
                    if s and not s.startswith("#") and not s.startswith("---"):
                        desc = s[:120]
                        break
        except Exception:
            pass
    return name or skill_dir.name, desc


def discover_quick_workflows(project_path: str = "") -> list[dict[str, str]]:
    """
    返回电脑端与移动端 `/` 斜杠菜单支持的全部快捷指令、系统命令与已安装 Skills 列表。
    与电脑端 IDE/命令行体验一致，展示所有可用技能与自定义工作流。
    """
    now_ts = time.time()
    cache_key = project_path.strip().lower()
    cached = _WORKFLOWS_CACHE.get(cache_key)
    if cached and cached[0] > now_ts:
        return cached[1]

    items: list[dict[str, str]] = [
        {"command": "/plan", "label": "制定分步实施计划", "category": "slash"},
        {"command": "/review", "label": "审查当前代码变更与潜在问题", "category": "slash"},
        {"command": "/grill-me", "label": "就当前方案向我提问对齐细节", "category": "slash"},
        {"command": "/tdd", "label": "按测试驱动开发 (TDD) 推进", "category": "slash"},
        {"command": "/compact", "label": "压缩总结上下文释放窗口", "category": "slash"},
        {"command": "/browser", "label": "自动化浏览器测试与网页交互", "category": "slash"},
        {"command": "/goal", "label": "长程自主任务目标持续推进", "category": "slash"},
        {"command": "/learn", "label": "沉淀与持久化最佳实践经验", "category": "slash"},
        {"command": "/boost", "label": "深度思考与多视角严密规划验证", "category": "slash"},
        {"command": "继续执行下一步", "label": "⚡ 快捷：确认当前进度并继续下一步", "category": "quick"},
        {"command": "运行测试并修复所有报错", "label": "🧪 快捷：执行测试套件并自动修复失败项", "category": "quick"},
        {"command": "总结当前已完成的代码改动与待办事项", "label": "📋 快捷：生成精简进度汇报", "category": "quick"},
    ]

    seen_cmds = {it["command"] for it in items}

    # 1. 扫描电脑端已安装的全局 Skills 目录
    home = Path(os.path.expanduser("~"))
    skills_roots = [
        (home / ".gemini" / "skills", "skill"),
        (home / ".gemini" / "config" / "skills", "skill"),
        (home / ".gemini" / "antigravity" / "builtin" / "skills", "builtin"),
        (home / ".agent" / "skills", "skill"),
    ]

    # 2. 扫描工作区项目级目录
    if project_path:
        from backend.core.workspace import decode_workspace_uri

        norm_p = decode_workspace_uri(project_path)
        if norm_p:
            p_root = Path(norm_p)
            if p_root.is_dir():
                skills_roots.insert(0, (p_root / ".agent" / "skills", "project"))
                skills_roots.insert(0, (p_root / ".gemini" / "skills", "project"))

                # 扫描项目工作流
                for rel_dir in (".agent/workflows",):
                    wf_dir = p_root / rel_dir
                    if wf_dir.is_dir():
                        try:
                            for md_file in sorted(wf_dir.glob("*.md"))[:30]:
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

    # 3. 遍历各 skills 根目录提取 Skills
    for s_root, cat in skills_roots:
        if not s_root.is_dir():
            continue
        try:
            for s_dir in sorted(s_root.iterdir()):
                if not s_dir.is_dir():
                    continue
                skill_name, skill_desc = parse_skill_meta(s_dir)
                cmd_str = f"/{skill_name}"
                if cmd_str not in seen_cmds:
                    seen_cmds.add(cmd_str)
                    items.append(
                        {
                            "command": cmd_str,
                            "label": skill_desc or f"技能: {skill_name}",
                            "category": cat,
                        }
                    )
        except Exception:
            pass

    _WORKFLOWS_CACHE[cache_key] = (now_ts + 60.0, items)
    return items
