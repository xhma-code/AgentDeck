# -*- coding: utf-8 -*-
"""
多 Agent 模型目录与默认配置表。
严格对齐本机 agy models 官方完整清单与 Claude Code / OpenCode 模型选型。
"""

from __future__ import annotations

from typing import Any

from backend.core.logger import logger

DEFAULT_AGY_MODELS: list[dict[str, Any]] = [
    {"id": "gemini-4-argon-medium", "label": "Gemini 4 Argon (Medium)", "tag": "New", "is_recommended": True},
    {"id": "gemini-4-argon-high", "label": "Gemini 4 Argon (High)", "tag": "New", "is_recommended": True},
    {"id": "gemini-4-argon-low", "label": "Gemini 4 Argon (Low)", "tag": "New", "is_recommended": True},
    {"id": "gemini-3.8-flash-medium", "label": "Gemini 3.8 Flash (Medium)", "tag": "均衡", "is_recommended": True},
    {"id": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)", "tag": "极速", "is_recommended": False},
    {"id": "gemini-3.8-flash-low", "label": "Gemini 3.8 Flash (Low)", "tag": "超快", "is_recommended": True},
    {"id": "claude-opus-5-5-low", "label": "Claude Opus 5.5 (Low)", "tag": "New", "is_recommended": True},
    {"id": "claude-opus-5-5-medium", "label": "Claude Opus 5.5 (Medium)", "tag": "New", "is_recommended": True},
    {"id": "claude-opus-5-5-high", "label": "Claude Opus 5.5 (High)", "tag": "深度", "is_recommended": True},
    {"id": "claude-sonnet-5-5-low", "label": "Claude Sonnet 5.5 (Low)", "tag": "New", "is_recommended": True},
    {"id": "claude-sonnet-5-5-medium", "label": "Claude Sonnet 5.5 (Medium)", "tag": "New", "is_recommended": True},
    {"id": "claude-sonnet-5-5-high", "label": "Claude Sonnet 5.5 (High)", "tag": "推荐", "is_recommended": True},
    {"id": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)", "tag": "Notice", "is_recommended": True},
]

AGENT_MODELS: dict[str, list[dict[str, Any]]] = {
    "agy_cli": [dict(m) for m in DEFAULT_AGY_MODELS],
    "agy_ide": [dict(m) for m in DEFAULT_AGY_MODELS],
    "claude_code": [
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "tag": "默认"},
        {"id": "claude-opus-4-7", "label": "Claude Opus 4.7", "tag": "深度"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "tag": "极速"},
        {"id": "sonnet", "label": "Sonnet (Latest)", "tag": ""},
        {"id": "opus", "label": "Opus (Latest)", "tag": ""},
    ],
    "opencode": [
        {"id": "opencode/big-pickle", "label": "Big Pickle", "tag": "默认", "is_recommended": True, "quota": 1.0},
        {"id": "opencode/deepseek-v4.1-flash-free", "label": "DeepSeek V4.1 Flash Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/glm-5.3-flash-free", "label": "GLM-5.3-Flash Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/nemotron-3.5-super-vl-free", "label": "Nemotron 3.5 Super VL Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/flint-40b-free", "label": "Flint 40B Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/beam-free", "label": "Beam Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/mimo-v2.6-flash-free", "label": "MiMo-V2.6-Flash Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/nemotron-3.5-lightning-free", "label": "Nemotron 3.5 Lightning Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "opencode/nemotron-3-ultra-free", "label": "Nemotron 3 Ultra Free", "tag": "免费", "is_recommended": False, "quota": 1.0},
        {"id": "anthropic/claude-sonnet-4-5", "label": "Claude Sonnet 4.5", "tag": ""},
        {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro", "tag": ""},
        {"id": "openai/gpt-5", "label": "GPT-5", "tag": ""},
    ],
}


def fetch_available_models(adapters: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """查询并合并各 Agent 适配器动态探测到的可用模型列表（优先带配额与默认标记）。"""
    res: dict[str, list[dict[str, Any]]] = {}
    for k, adapter in adapters.items():
        if hasattr(adapter, "get_available_models_with_quota"):
            try:
                res[k] = adapter.get_available_models_with_quota()
                continue
            except Exception as exc:
                logger.debug(f"获取 {k} 实时带配额模型异常: {exc}")
        elif hasattr(adapter, "get_available_models"):
            try:
                res[k] = adapter.get_available_models()
                continue
            except Exception as exc:
                logger.debug(f"动态获取 {k} 模型列表异常: {exc}")
        res[k] = list(AGENT_MODELS.get(k, []))
    return res



def resolve_default_model(adapters: dict[str, Any], agent_type: str) -> str:
    """解析指定 Agent 类型的默认推荐模型 ID。"""
    adapter = adapters.get(agent_type)
    if adapter and hasattr(adapter, "get_default_model_id"):
        try:
            def_mid = adapter.get_default_model_id()
            if def_mid:
                return def_mid
        except Exception as exc:
            logger.debug(f"解析 {agent_type} 默认模型异常: {exc}")
    models = AGENT_MODELS.get(agent_type, [])
    return models[0]["id"] if models else ""
