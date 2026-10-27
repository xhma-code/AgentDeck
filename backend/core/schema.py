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
    execution_mode TEXT DEFAULT 'auto',   -- 手机端接管执行策略（auto / accept_edits / plan / ask）
    model_name TEXT DEFAULT '',          -- 会话当前选定的模型名称（如 claude-sonnet-5-5-high / gemini-4-argon-high）
    last_viewed_at TEXT,                 -- 多端已查看时间戳（ISO 8601，用于跨端消除待查看蓝点）
    is_deleted INTEGER DEFAULT 0,        -- 是否已在网页端/手机端删除（0：正常，1：已删除）
    status TEXT DEFAULT 'live',          -- 会话真相状态（creating/live/degraded/dead）
    status_seq INTEGER DEFAULT 0,        -- 状态单调递增序号（CAS 护栏，防陈旧异步写覆盖新状态）
    updated_at TEXT NOT NULL             -- 最近消息更新时间（ISO 8601）
);

-- 表名：t_command_idempotency（命令幂等处理记录持久化表）
CREATE TABLE IF NOT EXISTS t_command_idempotency (
    command_id TEXT PRIMARY KEY,         -- 幂等键（客户端生成的唯一命令 ID）
    unified_id TEXT,                     -- 关联的统一会话主键
    status TEXT DEFAULT 'done',          -- 处理终态（done：已成功，error：失败可重放）
    payload_summary TEXT,                -- 命令载荷摘要（白名单字段+截断，不存 prompt 全文）
    created_at INTEGER NOT NULL,         -- 处理时间（Unix epoch 秒，用于过期清理）
    updated_at INTEGER NOT NULL          -- 最后更新时间（Unix epoch 秒）
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

-- 表名：t_session_alias（移动端临时会话到真实底层会话重定向别名表）
CREATE TABLE IF NOT EXISTS t_session_alias (
    alias_unified_id TEXT PRIMARY KEY,   -- 别名会话主键（如首轮临时生成的 agy_cli:uuid）
    target_unified_id TEXT NOT NULL,     -- 映射的真实会话主键（如 CLI 动态回填后的 agy_cli:real_cid）
    created_at TEXT NOT NULL             -- 别名建立时间（ISO 8601）
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

-- 表名：t_push_subscription（iOS PWA 原生 Web Push 订阅凭据表）
CREATE TABLE IF NOT EXISTS t_push_subscription (
    subscription_id TEXT PRIMARY KEY,    -- 订阅唯一标识（SHA-256(endpoint)）
    device_id TEXT NOT NULL,             -- 关联的配对设备唯一标识
    endpoint TEXT NOT NULL,              -- APNs 目标网关推送 URL
    p256dh_key TEXT NOT NULL,            -- 客户端加密公钥（Base64 URL Safe）
    auth_key TEXT NOT NULL,              -- 客户端鉴权密钥（Base64 URL Safe）
    user_agent TEXT,                     -- 客户端浏览器 User-Agent
    created_at TEXT NOT NULL,            -- 订阅创建时间（ISO 8601）
    last_pushed_at TEXT,                 -- 最近一次成功推送时间（ISO 8601）
    is_active INTEGER DEFAULT 1          -- 是否有效（0：已失效，1：有效）
);

-- 表名：t_schema_fingerprint（底层 Agent 结构指纹基准表）
CREATE TABLE IF NOT EXISTS t_schema_fingerprint (
    agent_type TEXT NOT NULL,            -- Agent 类型（agy_ide / claude_code / opencode）
    source_target TEXT NOT NULL,         -- 目标对象名（表名或数据文件类型）
    expected_hash TEXT NOT NULL,         -- 预期结构 SHA-256 哈希值
    last_verified_at TEXT NOT NULL,      -- 最后校验时间（ISO 8601）
    status TEXT DEFAULT 'valid',         -- 校验状态（valid：吻合，drifted：发生漂移）
    PRIMARY KEY (agent_type, source_target)
);

-- 表名：t_schema_drift_log（协议与结构漂移观测流水表）
CREATE TABLE IF NOT EXISTS t_schema_drift_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, -- 审计自增主键
    agent_type TEXT NOT NULL,                 -- Agent 类型
    drift_type TEXT NOT NULL,                 -- 漂移类型（sqlite_schema / jsonl_field / stdio_event）
    details_json TEXT NOT NULL,               -- 变更详情或未知字段摘要（JSON 字符串）
    sample_snippet TEXT,                      -- 截获的原始数据样本切片（已脱敏）
    observed_at TEXT NOT NULL                 -- 首次观测时间（ISO 8601）
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
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "is_deleted", "是否已在网页端/手机端删除（0：正常展示，1：已删除隐藏）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "status", "会话真相状态（creating/live/degraded/dead）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "status_seq", "状态单调递增序号（CAS 护栏，防陈旧异步写覆盖新状态）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "updated_at", "最近消息更新时间（ISO 8601）"),
    ("t_command_idempotency", "命令幂等处理记录持久化表", "command_id", "幂等键（客户端生成的唯一命令 ID）"),
    ("t_command_idempotency", "命令幂等处理记录持久化表", "unified_id", "关联的统一会话主键"),
    ("t_command_idempotency", "命令幂等处理记录持久化表", "status", "处理终态（done：已成功，error：失败可重放）"),
    ("t_command_idempotency", "命令幂等处理记录持久化表", "payload_summary", "命令载荷摘要（白名单字段+截断）"),
    ("t_command_idempotency", "命令幂等处理记录持久化表", "created_at", "处理时间（Unix epoch 秒）"),
    ("t_command_idempotency", "命令幂等处理记录持久化表", "updated_at", "最后更新时间（Unix epoch 秒）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "log_id", "审计日志自增主键"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "unified_id", "关联的统一会话主键"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_call_id", "工具调用唯一 ID"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_name", "工具名称"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_args_summary", "工具参数摘要"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "risk_level", "风险等级（normal：普通，high：高危）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "decision", "审批决定（approved：允许，denied：拒绝）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "decided_at", "审批操作时间（ISO 8601）"),
    ("t_session_alias", "移动端临时会话到真实底层会话重定向别名表", "alias_unified_id", "别名会话主键"),
    ("t_session_alias", "移动端临时会话到真实底层会话重定向别名表", "target_unified_id", "映射的真实会话主键"),
    ("t_session_alias", "移动端临时会话到真实底层会话重定向别名表", "created_at", "别名建立时间（ISO 8601）"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_id", "工作区收藏自增主键"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_name", "工作区显示名称"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_path", "工作区本地绝对路径"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "default_agent", "默认启动的 Agent 类型"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "is_hidden", "是否已从项目列表手动移出隐藏（0：展示，1：隐藏）"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "last_used_at", "最近使用时间（ISO 8601）"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "subscription_id", "订阅唯一标识（SHA-256(endpoint)）"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "device_id", "关联的配对设备唯一标识"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "endpoint", "APNs 目标网关推送 URL"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "p256dh_key", "客户端加密公钥"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "auth_key", "客户端鉴权密钥"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "user_agent", "客户端浏览器 User-Agent"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "created_at", "订阅创建时间（ISO 8601）"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "last_pushed_at", "最近一次成功推送时间"),
    ("t_push_subscription", "iOS PWA 原生 Web Push 订阅凭据表", "is_active", "是否有效（0：已失效，1：有效）"),
    ("t_schema_fingerprint", "底层 Agent 结构指纹基准表", "agent_type", "Agent 类型"),
    ("t_schema_fingerprint", "底层 Agent 结构指纹基准表", "source_target", "目标对象名"),
    ("t_schema_fingerprint", "底层 Agent 结构指纹基准表", "expected_hash", "预期结构 SHA-256 哈希值"),
    ("t_schema_fingerprint", "底层 Agent 结构指纹基准表", "last_verified_at", "最后校验时间（ISO 8601）"),
    ("t_schema_fingerprint", "底层 Agent 结构指纹基准表", "status", "校验状态（valid/drifted）"),
    ("t_schema_drift_log", "协议与结构漂移观测流水表", "log_id", "审计自增主键"),
    ("t_schema_drift_log", "协议与结构漂移观测流水表", "agent_type", "Agent 类型"),
    ("t_schema_drift_log", "协议与结构漂移观测流水表", "drift_type", "漂移类型"),
    ("t_schema_drift_log", "协议与结构漂移观测流水表", "details_json", "变更详情或未知字段摘要"),
    ("t_schema_drift_log", "协议与结构漂移观测流水表", "sample_snippet", "截获的原始数据样本切片"),
    ("t_schema_drift_log", "协议与结构漂移观测流水表", "observed_at", "首次观测时间（ISO 8601）"),
]
