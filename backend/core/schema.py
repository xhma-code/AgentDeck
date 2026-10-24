# -*- coding: utf-8 -*-
"""
AgentDeck 本地元数据表 DDL 与数据字典中文注释定义。
"""

from __future__ import annotations

# 包含完整中文注释的数据库建表语句
SCHEMA_DDL = """
-- 表名：t_device_session（移动端配对设备与鉴权会话表）
CREATE TABLE IF NOT EXISTS t_device_session (
    device_id TEXT PRIMARY KEY,          -- 设备唯一标识（UUID）
    device_name TEXT NOT NULL,           -- 手机设备名称（如 iPhone / Android User-Agent 摘要）
    auth_token_hash TEXT NOT NULL,       -- 鉴权访问令牌 SHA-256 哈希值
    tailscale_ip TEXT,                   -- 最近连接的 Tailscale 虚拟局域网 IP
    created_at TEXT NOT NULL,            -- 配对创建时间（ISO 8601）
    last_active_at TEXT NOT NULL,        -- 最后活跃时间（ISO 8601）
    is_revoked INTEGER DEFAULT 0         -- 是否已吊销（0：有效，1：已吊销）
);

-- 表名：t_unified_session_meta（多 Agent 会话统一索引与扩展元数据表）
CREATE TABLE IF NOT EXISTS t_unified_session_meta (
    unified_id TEXT PRIMARY KEY,         -- 统一会话主键（格式：{agent_type}:{raw_session_id}）
    agent_type TEXT NOT NULL,            -- Agent 类型（agy_ide / agy_cli / claude_code / opencode）
    raw_session_id TEXT NOT NULL,        -- 原生会话 ID
    parent_unified_id TEXT,              -- 克隆来源会话主键（用于 IDE 会话 Fork 到 CLI 后的溯源）
    project_path TEXT,                   -- 关联的工作区目录绝对路径
    title TEXT,                          -- 会话标题摘要（自动提取首条提问或原生标题）
    is_pinned INTEGER DEFAULT 0,         -- 手机端是否置顶（0：否，1：是）
    execution_mode TEXT DEFAULT 'ask',   -- 手机端接管执行策略（plan / ask / accept_edits / auto）
    model_name TEXT DEFAULT '',          -- 会话当前选定的模型名称（如 claude-sonnet-5-5-high / gemini-4-argon-high）
    last_viewed_at TEXT,                 -- 多端已查看时间戳（ISO 8601，用于跨端消除待查看蓝点）
    updated_at TEXT NOT NULL             -- 最近消息更新时间（ISO 8601）
);

-- 表名：t_tool_approval_log（移动端工具调用审批审计日志表）
CREATE TABLE IF NOT EXISTS t_tool_approval_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, -- 审计日志自增主键
    unified_id TEXT NOT NULL,                 -- 关联的统一会话主键
    tool_call_id TEXT NOT NULL,               -- 工具调用唯一 ID
    tool_name TEXT NOT NULL,                  -- 工具名称（如 Bash / run_command / Edit）
    tool_args_summary TEXT,                   -- 工具参数摘要（如执行的命令行或修改的文件路径）
    risk_level TEXT DEFAULT 'normal',         -- 风险等级（normal：普通，high：高危需长按确认）
    decision TEXT NOT NULL,                   -- 审批决定（approved：允许，denied：拒绝）
    decided_at TEXT NOT NULL                  -- 审批操作时间（ISO 8601）
);

-- 表名：t_workspace_bookmark（移动端新建会话常用工作区收藏表）
CREATE TABLE IF NOT EXISTS t_workspace_bookmark (
    workspace_id INTEGER PRIMARY KEY AUTOINCREMENT, -- 工作区收藏自增主键
    workspace_name TEXT NOT NULL,                   -- 工作区显示名称（如 AgentDeck）
    workspace_path TEXT NOT NULL UNIQUE,            -- 工作区本地绝对路径
    default_agent TEXT DEFAULT 'claude_code',       -- 默认启动的 Agent 类型
    is_hidden INTEGER DEFAULT 0,                    -- 是否已从项目列表手动移出隐藏（0：展示，1：隐藏）
    last_used_at TEXT NOT NULL                      -- 最近使用时间（ISO 8601）
);

-- 表名：t_table_column_comments（数据字典中文注释元数据注册表）
CREATE TABLE IF NOT EXISTS t_table_column_comments (
    table_name TEXT NOT NULL,            -- 数据表英文名称
    table_comment TEXT NOT NULL,         -- 数据表中文注释说明
    column_name TEXT NOT NULL,           -- 字段英文名称
    column_comment TEXT NOT NULL,        -- 字段中文注释说明
    PRIMARY KEY (table_name, column_name)
);
"""

# 显式注册表与字段中文注释元数据（便于自省与审计）
TABLE_COLUMN_COMMENTS: list[tuple[str, str, str, str]] = [
    ("t_device_session", "移动端配对设备与鉴权会话表", "device_id", "设备唯一标识（UUID）"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "device_name", "手机设备名称"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "auth_token_hash", "鉴权访问令牌 SHA-256 哈希值"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "tailscale_ip", "最近连接的 Tailscale 虚拟局域网 IP"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "created_at", "配对创建时间（ISO 8601）"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "last_active_at", "最后活跃时间（ISO 8601）"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "is_revoked", "是否已吊销（0：有效，1：已吊销）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "unified_id", "统一会话主键（{agent_type}:{raw_session_id}）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "agent_type", "Agent 类型（agy_ide/agy_cli/claude_code/opencode）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "raw_session_id", "原生会话 ID"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "parent_unified_id", "克隆来源会话主键"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "project_path", "关联的工作区目录绝对路径"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "title", "会话标题摘要"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "is_pinned", "手机端是否置顶（0：否，1：是）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "execution_mode", "手机端接管执行策略（plan/ask/accept_edits/auto）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "model_name", "会话当前选定的模型名称"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "last_viewed_at", "多端已查看时间戳（ISO 8601）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "updated_at", "最近消息更新时间（ISO 8601）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "log_id", "审计日志自增主键"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "unified_id", "关联的统一会话主键"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_call_id", "工具调用唯一 ID"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_name", "工具名称"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_args_summary", "工具参数摘要"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "risk_level", "风险等级（normal：普通，high：高危）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "decision", "审批决定（approved：允许，denied：拒绝）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "decided_at", "审批操作时间（ISO 8601）"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_id", "工作区收藏自增主键"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_name", "工作区显示名称"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_path", "工作区本地绝对路径"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "default_agent", "默认启动的 Agent 类型"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "is_hidden", "是否已从项目列表手动移出隐藏（0：展示，1：隐藏）"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "last_used_at", "最近使用时间（ISO 8601）"),
]
