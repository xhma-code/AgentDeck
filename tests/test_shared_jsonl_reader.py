# -*- coding: utf-8 -*-
"""
共享 JSONL 增量读取骨架 (`backend.adapters.base.read_incremental_jsonl_stream`) 的行为 pin 测试。

覆盖 Claude 家族薄委托在三种场景下的语义稳定性：
1. 全量冷读 + 缓存元组新契约（含解析状态尾部）；
2. 追加写入走增量 seek 分支；
3. 流式半截行被安全忽略且文件游标不推进。
"""

import json
from pathlib import Path

import pytest

from backend.adapters.base import BoundedLRUCache, read_incremental_jsonl_stream
from backend.adapters.claude_scanner import read_claude_session_messages


def _claude_user_record(idx: int, text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": f"2026-10-24T00:00:0{idx}Z",
            "message": {"content": text},
        }
    ) + "\n"


@pytest.fixture()
def claude_jsonl(tmp_path: Path) -> Path:
    fpath = tmp_path / "abc123.jsonl"
    fpath.write_text(_claude_user_record(0, "第一条"), encoding="utf-8")
    return fpath


def test_claude_reader_full_read_and_cache_tuple(claude_jsonl: Path):
    cache = BoundedLRUCache(maxsize=10)
    items = read_claude_session_messages(claude_jsonl, "s1", cache, limit=50)
    assert [it.content for it in items] == ["第一条"]

    cached = cache.get("s1")
    assert cached is not None
    # 新共享契约：(mtime_ns, fsize, items, consumed_bytes, tool_id_map, step_idx)
    assert len(cached) == 6


def test_claude_reader_incremental_seek_branch(claude_jsonl: Path):
    cache = BoundedLRUCache(maxsize=10)
    assert read_claude_session_messages(claude_jsonl, "s1", cache, limit=50)

    with claude_jsonl.open("a", encoding="utf-8") as f:
        f.write(_claude_user_record(1, "第二条"))

    items = read_claude_session_messages(claude_jsonl, "s1", cache, limit=50)
    assert [it.content for it in items] == ["第一条", "第二条"]
    assert cache.get("s1")[4] == {}  # tool_id_map 状态在增量分支被正确保留


def test_claude_reader_partial_line_cursor_stability(claude_jsonl: Path):
    cache = BoundedLRUCache(maxsize=10)
    with claude_jsonl.open("a", encoding="utf-8") as f:
        f.write(_claude_user_record(1, "第二条"))
    assert len(read_claude_session_messages(claude_jsonl, "s1", cache, limit=50)) == 2

    offset_before = cache.get("s1")[3]
    with claude_jsonl.open("a", encoding="utf-8") as f:
        f.write('{"type": "user", "message')  # 半截行（无换行且 JSON 不完整）

    items = read_claude_session_messages(claude_jsonl, "s1", cache, limit=50)
    assert len(items) == 2
    assert cache.get("s1")[3] == offset_before  # 游标不因半截行推进


def test_shared_skeleton_missing_file_returns_empty():
    assert read_incremental_jsonl_stream(
        fpath=Path("Z:/__not_exist__/ghost.jsonl"),
        cache_key="ghost",
        messages_cache=BoundedLRUCache(maxsize=4),
        family_label="Ghost",
        parse_record=lambda rec, items, idx, state: None,
    ) == []
