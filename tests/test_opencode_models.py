# -*- coding: utf-8 -*-
"""
OpenCode 动态模型获取、免费模型识别与格式化测试套件。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.adapters.models import resolve_model_context_limit
from backend.adapters.opencode_adapter import OpenCodeAdapter
from backend.adapters.opencode_bridge import (
    OpenCodeHttpBridge,
    parse_opencode_model_ref,
)
from backend.adapters.opencode_scanner import parse_opencode_model_field
from backend.engines.model_catalog import fetch_available_models


def test_parse_opencode_model_ref():
    """测试 Model.Ref 参数规范化转换。"""
    assert parse_opencode_model_ref("") == {}
    assert parse_opencode_model_ref("default") == {}
    
    # 纯模型名称
    ref1 = parse_opencode_model_ref("big-pickle")
    assert ref1 == {"providerID": "opencode", "id": "big-pickle"}
    
    # 带 provider
    ref2 = parse_opencode_model_ref("opencode/deepseek-v4.1-flash-free")
    assert ref2 == {"providerID": "opencode", "id": "deepseek-v4.1-flash-free"}
    
    # 带 variant
    ref3 = parse_opencode_model_ref("opencode/glm-5.3-flash-free#high")
    assert ref3 == {"providerID": "opencode", "id": "glm-5.3-flash-free", "variant": "high"}
    
    # 第三方 provider
    ref4 = parse_opencode_model_ref("google/gemini-2.5-pro#medium")
    assert ref4 == {"providerID": "google", "id": "gemini-2.5-pro", "variant": "medium"}


def test_parse_opencode_model_field():
    """测试 SQLite 会话表中 model 字段的格式反序列化。"""
    assert parse_opencode_model_field("") == ""
    assert parse_opencode_model_field(None) == ""
    
    # 字符串形式
    assert parse_opencode_model_field("opencode/big-pickle") == "opencode/big-pickle"
    
    # JSON 字典字符串
    json_str = '{"id":"big-pickle","providerID":"opencode"}'
    assert parse_opencode_model_field(json_str) == "opencode/big-pickle"
    
    # 真实字典
    d = {"id": "deepseek-v4.1-flash-free", "providerID": "opencode"}
    assert parse_opencode_model_field(d) == "opencode/deepseek-v4.1-flash-free"


def test_opencode_context_limit_resolution():
    """测试免费模型的上下文限制推断。"""
    assert resolve_model_context_limit("opencode/beam-free") == 1_048_576
    assert resolve_model_context_limit("opencode/nemotron-3-ultra-free") == 1_000_000
    assert resolve_model_context_limit("opencode/flint-40b-free") == 262_144
    assert resolve_model_context_limit("opencode/deepseek-v4.1-flash-free") == 200_000
    assert resolve_model_context_limit("opencode/big-pickle") == 200_000


def test_bridge_get_available_models_with_mock():
    """测试 HTTP Bridge 解析 /api/model 返回并标定免费/默认模型。"""
    bridge = OpenCodeHttpBridge()
    
    mock_data = {
        "data": [
            {
                "id": "big-pickle",
                "providerID": "opencode",
                "name": "Big Pickle",
                "enabled": True,
                "cost": [{"input": 0, "output": 0}],
                "limit": {"context": 200000},
            },
            {
                "id": "deepseek-v4.1-flash-free",
                "providerID": "opencode",
                "name": "DeepSeek V4.1 Flash Free",
                "enabled": True,
                "cost": [{"input": 0, "output": 0}],
                "limit": {"context": 200000},
            },
            {
                "id": "paid-model",
                "providerID": "other",
                "name": "Paid Model",
                "enabled": True,
                "cost": [{"input": 10, "output": 30}],
                "limit": {"context": 128000},
            },
            {
                "id": "disabled-model",
                "providerID": "opencode",
                "name": "Disabled Model",
                "enabled": False,
            },
        ]
    }
    
    with patch.object(bridge, "get_default_model_id", return_value="opencode/big-pickle"), \
         patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        
        models = bridge.get_available_models()
        assert len(models) == 3  # disabled 被过滤
        
        # 默认模型置顶且 tag 为 默认
        assert models[0]["id"] == "opencode/big-pickle"
        assert models[0]["tag"] == "默认"
        assert models[0]["is_recommended"] is True
        
        # 免费模型 tag 为 免费
        assert models[1]["id"] == "opencode/deepseek-v4.1-flash-free"
        assert models[1]["tag"] == "免费"
        assert models[1]["is_recommended"] is False
        
        # 付费模型 tag 为空
        assert models[2]["id"] == "other/paid-model"
        assert models[2]["tag"] == ""


def test_opencode_adapter_fallback_to_static_models():
    """测试当 HTTP Bridge 和 CLI 均不可用时，平滑降级到静态包含全套免费模型的目录。"""
    adapter = OpenCodeAdapter(data_dir=Path("/non_existent_opencode_dir"))
    adapter.cli_bin = None  # 禁用 CLI
    
    with patch.object(adapter.http_bridge, "is_server_running", return_value=False):
        models = adapter.get_available_models()
        assert len(models) > 0
        model_ids = [m["id"] for m in models]
        assert "opencode/big-pickle" in model_ids
        assert "opencode/deepseek-v4.1-flash-free" in model_ids
        assert "opencode/glm-5.3-flash-free" in model_ids
        
        free_models = [m for m in models if m.get("tag") == "免费"]
        assert len(free_models) >= 8


def test_fetch_available_models_engine_integration():
    """测试后端引擎层 fetch_available_models 能正确汇聚 OpenCode 免费模型。"""
    adapter = OpenCodeAdapter()
    catalog = fetch_available_models({"opencode": adapter})
    assert "opencode" in catalog
    models = catalog["opencode"]
    assert len(models) >= 9
    
    # 确认包含免费模型与默认模型
    free_models = [m for m in models if m.get("tag") == "免费"]
    assert len(free_models) >= 1
