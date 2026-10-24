# -*- coding: utf-8 -*-
"""
Antigravity 模型标识反序列化与电脑端状态文件模型解析器。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from backend.adapters.antigravity_ls_bridge import LS_MODEL_ID_MAP
from backend.core.logger import logger


def extract_protobuf_model_id(data: bytes) -> Optional[str]:
    """从二进制 Protobuf 报文中提取模型 ID。"""
    if not data or not isinstance(data, bytes):
        return None

    pattern = rb"model_enum[\x00-\x20\x80-\xff]+([A-Za-z0-9_]{4,40})"
    for m in re.finditer(pattern, data):
        raw_token = m.group(1).decode("ascii", errors="ignore").lower()
        if raw_token in LS_MODEL_ID_MAP:
            return LS_MODEL_ID_MAP[raw_token]
        if raw_token.startswith("model_placeholder_"):
            return LS_MODEL_ID_MAP.get(raw_token, raw_token)

    lower_data = data.lower()
    for mid in sorted(LS_MODEL_ID_MAP.values(), key=len, reverse=True):
        if mid.encode("ascii").lower() in lower_data:
            return mid

    fb_pat = rb"(?:^|[^a-zA-Z0-9.-])(gemini-[a-zA-Z0-9.-]+|claude-[a-zA-Z0-9.-]+|gpt-[a-zA-Z0-9.-]+)(?:[^a-zA-Z0-9.-]|$)"
    m_fb = re.search(fb_pat, data)
    if m_fb:
        return m_fb.group(1).decode("ascii", errors="ignore").lower()
    return None


def parse_state_selected_model(state_file: Path) -> Optional[str]:
    """从电脑端 antigravity_state.pbtxt 中提取用户最后选定的真实模型 ID。"""
    if not state_file.exists():
        return None
    try:
        content = state_file.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"last_selected_agent_model:\s*([A-Za-z0-9_]+)", content)
        if m:
            raw_id = m.group(1).lower()
            return LS_MODEL_ID_MAP.get(raw_id, raw_id)
        m2 = re.search(r'selected_model_id\s*:\s*"([^"]+)"', content)
        if m2:
            raw_id = m2.group(1).strip().lower()
            return LS_MODEL_ID_MAP.get(raw_id, raw_id)
    except Exception as exc:
        logger.debug(f"读取 state.pbtxt 选定模型失败: {exc}")
    return None
