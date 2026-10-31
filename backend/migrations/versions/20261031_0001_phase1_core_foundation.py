# -*- coding: utf-8 -*-
"""Clean-Slate 最终重构 21 张规范表全量基线迁移脚本

Revision ID: 20261031_0001
Revises: None
Create Date: 2026-10-31 22:15:00
"""

from __future__ import annotations

import json
import platform
import socket
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from backend.application.runtime_registry_service import LOCAL_MACHINE_ID, _default_provider_seeds
from backend.db.base import sync_comments_to_connection, utc_now_iso

revision: str = "20261031_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CLEAN_SLATE_21_TABLES_DROP_ORDER = [
    "table_column_comments",
    "command_idempotency",
    "push_subscriptions",
    "device_sessions",
    "events",
    "artifacts",
    "decisions",
    "execution_attempts",
    "executions",
    "external_session_refs",
    "sessions",
    "runtime_instances",
    "machines",
    "runtime_providers",
    "step_revisions",
    "steps",
    "task_revisions",
    "task_dependencies",
    "tasks",
    "mcp_project_tokens",
    "projects",
]


def _detect_os_str() -> str:
    sys_name = platform.system().lower()
    if "windows" in sys_name:
        return "windows"
    if "darwin" in sys_name:
        return "macos"
    return "linux"


def upgrade() -> None:
    """一次性创建 Clean-Slate 全部 21 张规范领域表、同步中文表字段注释并写入内置 Runtime/Machine 基线种子。"""
    # 1. projects（研发项目主表）
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False, comment="项目唯一标识（UUID）"),
        sa.Column("slug", sa.String(length=80), nullable=False, comment="项目唯一短代号（URL 安全）"),
        sa.Column("name", sa.String(length=160), nullable=False, comment="项目显示名称"),
        sa.Column("description", sa.Text(), nullable=False, server_default="", comment="项目背景与目标说明"),
        sa.Column("root_path", sa.Text(), nullable=False, comment="项目本地工作区规范化绝对路径（文件读取白名单根边界）"),
        sa.Column("git_remote", sa.Text(), nullable=True, comment="关联 Git 远端仓库地址（可空）"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active", comment="项目生命周期状态（active/paused/archived）"),
        sa.Column("summary_next_action", sa.Text(), nullable=False, server_default="", comment="当前下一步关键动作摘要"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1", comment="乐观锁递增版本号"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="项目创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="项目最后更新时间（UTC ISO 8601）"),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
        sa.UniqueConstraint("root_path", name="uq_projects_root_path"),
        comment="研发项目主表（Project-first 顶层隔离边界）",
    )

    # 2. mcp_project_tokens（MCP 项目级最小权限令牌表）
    op.create_table(
        "mcp_project_tokens",
        sa.Column("id", sa.String(length=36), nullable=False, comment="令牌记录唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="强制绑定的单一项目唯一标识"),
        sa.Column("token_name", sa.String(length=120), nullable=False, comment="令牌用途显示名称（如 claude-code-local）"),
        sa.Column("token_prefix", sa.String(length=24), nullable=False, comment="令牌可识别安全前缀（不含完整明文）"),
        sa.Column("token_hash", sa.String(length=64), nullable=False, comment="访问令牌 SHA-256 哈希值（严禁存储明文）"),
        sa.Column("scopes_json", sa.Text(), nullable=False, server_default="[]", comment="授予的最小权限范围列表（JSON 数组）"),
        sa.Column("is_revoked", sa.Integer(), nullable=False, server_default="0", comment="是否已吊销（0：有效，1：已吊销）"),
        sa.Column("expires_at", sa.String(length=40), nullable=True, comment="令牌过期时间（UTC ISO 8601，可空）"),
        sa.Column("last_used_at", sa.String(length=40), nullable=True, comment="最近一次鉴权使用时间（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="令牌创建时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_mcp_project_tokens_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_project_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_mcp_project_tokens_token_hash"),
        comment="MCP 项目级最小权限令牌表（强制绑定单一 Project，仅存 SHA-256 哈希）",
    )
    op.create_index("ix_mcp_project_tokens_project_revoked", "mcp_project_tokens", ["project_id", "is_revoked"])

    # 3. tasks（项目任务主表）
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False, comment="任务唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识（强制归属）"),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True, comment="父任务唯一标识（根任务为空，必须同属一个项目）"),
        sa.Column("title", sa.String(length=240), nullable=False, comment="任务标题"),
        sa.Column("goal", sa.Text(), nullable=False, server_default="", comment="当前生效版本的任务目标详情"),
        sa.Column("acceptance_criteria_json", sa.Text(), nullable=False, server_default="[]", comment="当前生效版本的验收标准列表（JSON 数组）"),
        sa.Column("platform_status", sa.String(length=32), nullable=False, server_default="draft", comment="平台任务管理状态（draft/ready/in_progress/waiting_interaction/waiting_decision/blocked/needs_revalidation/completed/cancelled）"),
        sa.Column("agent_reported_status", sa.String(length=32), nullable=False, server_default="unknown", comment="Agent 主动汇报状态（unknown/running/waiting_interaction/waiting_input/completed/failed，不直接等同平台验收）"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="medium", comment="任务优先级（low/medium/high/critical）"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0", comment="同级任务展示排序序号"),
        sa.Column("active_revision_no", sa.Integer(), nullable=False, server_default="1", comment="当前生效的任务修订版本号"),
        sa.Column("needs_revalidation", sa.Integer(), nullable=False, server_default="0", comment="是否因上游修订待人工重验（0：否，1：是）"),
        sa.Column("revalidation_reason", sa.Text(), nullable=False, server_default="", comment="标记待重验的具体原因说明"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1", comment="乐观锁递增版本号"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="任务创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="任务最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_tasks_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.id"], name="fk_tasks_parent_task_id_tasks", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        comment="项目任务主表（支持同项目父子树层级与双轨状态管理）",
    )
    op.create_index("ix_tasks_project_parent", "tasks", ["project_id", "parent_task_id"])
    op.create_index("ix_tasks_project_status", "tasks", ["project_id", "platform_status"])

    # 4. task_dependencies（同项目任务轻量前置依赖表）
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.String(length=36), nullable=False, comment="任务依赖关系唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=False, comment="后置受依赖任务唯一标识"),
        sa.Column("depends_on_task_id", sa.String(length=36), nullable=False, comment="前置依赖任务唯一标识"),
        sa.Column("dependency_kind", sa.String(length=32), nullable=False, server_default="finish_to_start", comment="依赖语义类型（finish_to_start）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="依赖创建时间（UTC ISO 8601）"),
        sa.CheckConstraint("task_id != depends_on_task_id", name="ck_task_dependencies_no_self_dependency"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_task_dependencies_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_dependencies_task_id_tasks", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_task_id"], ["tasks.id"], name="fk_task_dependencies_depends_on_task_id_tasks", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_task_dependencies"),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
        comment="同项目任务轻量前置依赖关系表（强制同项目且有向无环）",
    )
    op.create_index("ix_task_dependencies_project", "task_dependencies", ["project_id"])
    op.create_index("ix_task_dependencies_prereq", "task_dependencies", ["depends_on_task_id"])

    # 5. task_revisions（任务追加式修订快照历史表）
    op.create_table(
        "task_revisions",
        sa.Column("id", sa.String(length=36), nullable=False, comment="任务修订记录唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=False, comment="所属任务唯一标识"),
        sa.Column("revision_no", sa.Integer(), nullable=False, comment="单调递增修订版本号（从 1 开始）"),
        sa.Column("title_snapshot", sa.String(length=240), nullable=False, comment="修订时的任务标题快照"),
        sa.Column("goal_snapshot", sa.Text(), nullable=False, server_default="", comment="修订时的任务目标快照"),
        sa.Column("acceptance_criteria_json", sa.Text(), nullable=False, server_default="[]", comment="修订时的验收标准列表快照（JSON 数组）"),
        sa.Column("change_reason", sa.Text(), nullable=False, server_default="", comment="本次修订变更原因说明"),
        sa.Column("changed_by", sa.String(length=40), nullable=False, server_default="user", comment="修订发起方（user/mcp_agent/system）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="修订记录创建时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_task_revisions_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_revisions_task_id_tasks", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_task_revisions"),
        sa.UniqueConstraint("task_id", "revision_no", name="uq_task_revisions_task_rev"),
        comment="任务追加式修订历史快照表（保留历次目标与验收标准变更）",
    )
    op.create_index("ix_task_revisions_project_task", "task_revisions", ["project_id", "task_id"])

    # 6. steps（任务拆解步骤表）
    op.create_table(
        "steps",
        sa.Column("id", sa.String(length=36), nullable=False, comment="步骤唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=False, comment="所属任务唯一标识"),
        sa.Column("seq_order", sa.Integer(), nullable=False, comment="任务内步骤执行顺序号（从 1 开始递增）"),
        sa.Column("title", sa.String(length=240), nullable=False, comment="步骤标题"),
        sa.Column("instruction", sa.Text(), nullable=False, server_default="", comment="当前生效版本的步骤具体说明"),
        sa.Column("platform_status", sa.String(length=32), nullable=False, server_default="pending", comment="平台步骤管理状态（pending/ready/in_progress/waiting_interaction/blocked/needs_revalidation/completed/skipped）"),
        sa.Column("agent_reported_status", sa.String(length=32), nullable=False, server_default="unknown", comment="Agent 主动汇报步骤状态"),
        sa.Column("active_revision_no", sa.Integer(), nullable=False, server_default="1", comment="当前生效的步骤修订版本号"),
        sa.Column("needs_revalidation", sa.Integer(), nullable=False, server_default="0", comment="是否因前置步骤或任务修订待重验（0：否，1：是）"),
        sa.Column("revalidation_reason", sa.Text(), nullable=False, server_default="", comment="步骤进入待重验的原因说明"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1", comment="乐观锁递增版本号"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="步骤创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="步骤最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_steps_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_steps_task_id_tasks", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_steps"),
        comment="任务拆解步骤表（支持追加式修订与下游待重验标记）",
    )
    op.create_index("ix_steps_task_seq", "steps", ["task_id", "seq_order"])
    op.create_index("ix_steps_project_status", "steps", ["project_id", "platform_status"])

    # 7. step_revisions（步骤追加式修订快照历史表）
    op.create_table(
        "step_revisions",
        sa.Column("id", sa.String(length=36), nullable=False, comment="步骤修订记录唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=False, comment="所属任务唯一标识"),
        sa.Column("step_id", sa.String(length=36), nullable=False, comment="所属步骤唯一标识"),
        sa.Column("revision_no", sa.Integer(), nullable=False, comment="单调递增步骤修订版本号（从 1 开始）"),
        sa.Column("title_snapshot", sa.String(length=240), nullable=False, comment="修订时的步骤标题快照"),
        sa.Column("instruction_snapshot", sa.Text(), nullable=False, server_default="", comment="修订时的步骤说明内容快照"),
        sa.Column("change_reason", sa.Text(), nullable=False, server_default="", comment="本次步骤修订原因说明"),
        sa.Column("changed_by", sa.String(length=40), nullable=False, server_default="user", comment="修订发起方（user/mcp_agent/system）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="步骤修订创建时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_step_revisions_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_step_revisions_task_id_tasks", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["steps.id"], name="fk_step_revisions_step_id_steps", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_step_revisions"),
        sa.UniqueConstraint("step_id", "revision_no", name="uq_step_revisions_step_rev"),
        comment="任务步骤追加式修订历史快照表",
    )
    op.create_index("ix_step_revisions_task_step", "step_revisions", ["task_id", "step_id"])

    # 8. runtime_providers（Agent Runtime 产品定义表）
    op.create_table(
        "runtime_providers",
        sa.Column("id", sa.String(length=36), nullable=False, comment="Runtime 产品记录唯一标识"),
        sa.Column("provider_key", sa.String(length=40), nullable=False, comment="产品标准唯一键（claude_code/codex/opencode/antigravity）"),
        sa.Column("display_name", sa.String(length=80), nullable=False, comment="Agent 产品显示名称"),
        sa.Column("integration_type", sa.String(length=32), nullable=False, comment="默认集成形态（remote_only/mcp_client/http_api/sdk/cli）"),
        sa.Column("documentation_url", sa.Text(), nullable=False, server_default="", comment="官方集成参考文档链接"),
        sa.Column("default_capabilities_json", sa.Text(), nullable=False, server_default="[]", comment="默认能力声明矩阵（CapabilityManifest 列表 JSON，未知项标 unverified）"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1", comment="是否启用（0：禁用，1：启用）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="记录创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="记录最后更新时间（UTC ISO 8601）"),
        sa.PrimaryKeyConstraint("id", name="pk_runtime_providers"),
        sa.UniqueConstraint("provider_key", name="uq_runtime_providers_provider_key"),
        comment="Agent Runtime 产品定义与默认能力基线表",
    )

    # 9. machines（宿主机器登记表）
    op.create_table(
        "machines",
        sa.Column("id", sa.String(length=36), nullable=False, comment="宿主机器唯一标识（UUID 或 local-machine）"),
        sa.Column("display_name", sa.String(length=120), nullable=False, comment="机器显示名称"),
        sa.Column("os", sa.String(length=32), nullable=False, comment="操作系统类型（windows/macos/linux）"),
        sa.Column("hostname_alias", sa.String(length=120), nullable=False, server_default="", comment="主机名或网络友好别名"),
        sa.Column("connection_kind", sa.String(length=32), nullable=False, server_default="local", comment="连接通道类型（local/tailscale/lan/vendor_remote/other）"),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown", comment="机器健康状态（online/offline/degraded/unknown）"),
        sa.Column("last_seen_at", sa.String(length=40), nullable=True, comment="最近一次探测在线时间（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="机器登记创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="机器信息最后更新时间（UTC ISO 8601）"),
        sa.PrimaryKeyConstraint("id", name="pk_machines"),
        comment="Agent 运行宿主机器登记与在线状态表",
    )

    # 10. runtime_instances（Runtime 运行实例与能力验证清单表）
    op.create_table(
        "runtime_instances",
        sa.Column("id", sa.String(length=36), nullable=False, comment="Runtime 实例唯一标识（UUID）"),
        sa.Column("provider_id", sa.String(length=36), nullable=False, comment="关联的 RuntimeProvider 主键"),
        sa.Column("machine_id", sa.String(length=36), nullable=False, comment="所属宿主机器主键"),
        sa.Column("display_name", sa.String(length=120), nullable=False, comment="Runtime 实例显示名称"),
        sa.Column("runtime_version", sa.String(length=64), nullable=False, server_default="unknown", comment="Runtime 版本号（未知标 unknown）"),
        sa.Column("connector_version", sa.String(length=64), nullable=False, server_default="none", comment="平台 Connector 版本标识"),
        sa.Column("control_level", sa.String(length=8), nullable=False, server_default="L0", comment="已验证最高控制等级（L0/L1/L2/L3）"),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default="unknown", comment="实例健康状态（online/offline/degraded/unknown）"),
        sa.Column("connection_profile_json", sa.Text(), nullable=False, server_default="{}", comment="非敏感连接配置摘要（JSON，严禁存密钥）"),
        sa.Column("capability_manifest_json", sa.Text(), nullable=False, server_default="[]", comment="实例级已验证能力清单（CapabilityManifest 数组 JSON）"),
        sa.Column("model_endpoint_descriptor_json", sa.Text(), nullable=True, comment="可选非敏感模型环境说明（ModelEndpointDescriptor JSON，严禁存 Base URL 或 API Key）"),
        sa.Column("last_seen_at", sa.String(length=40), nullable=True, comment="最近活跃或健康检查时间（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="实例登记创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="实例最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["provider_id"], ["runtime_providers.id"], name="fk_runtime_instances_provider_id_runtime_providers", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], name="fk_runtime_instances_machine_id_machines", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_runtime_instances"),
        comment="Runtime 运行实例、L0-L3 控制等级与能力验证清单表",
    )
    op.create_index("ix_runtime_instances_provider_machine", "runtime_instances", ["provider_id", "machine_id"])

    # 11. sessions（项目协同会话主表）
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False, comment="平台会话唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="强制归属的项目唯一标识（禁止游离无项目会话）"),
        sa.Column("task_id", sa.String(length=36), nullable=True, comment="当前关联的任务唯一标识（可空，非空时必须同属一个项目）"),
        sa.Column("runtime_instance_id", sa.String(length=36), nullable=True, comment="关联的 Runtime 实例唯一标识（可空）"),
        sa.Column("title", sa.String(length=240), nullable=False, comment="协同会话显示标题"),
        sa.Column("source_channel", sa.String(length=32), nullable=False, server_default="remote_link", comment="会话接入通道（remote_link/mcp/local_runtime/hosted）"),
        sa.Column("platform_status", sa.String(length=32), nullable=False, server_default="active", comment="平台会话管理状态（active/waiting_interaction/idle/paused/completed/archived）"),
        sa.Column("agent_reported_status", sa.String(length=32), nullable=False, server_default="unknown", comment="Agent 主动汇报状态（unknown/running/waiting_interaction/waiting_input/completed/failed）"),
        sa.Column("runtime_status", sa.String(length=32), nullable=False, server_default="unknown", comment="底层 Runtime 实例返回状态（online/offline/degraded/unknown）"),
        sa.Column("connection_status", sa.String(length=32), nullable=False, server_default="unknown", comment="Remote 或 Server 连接状态（connected/disconnected/unknown，断开不代表失败）"),
        sa.Column("status_seq", sa.Integer(), nullable=False, server_default="1", comment="状态单调递增序号（防止乱序旧事件覆盖新状态）"),
        sa.Column("last_activity_at", sa.String(length=40), nullable=True, comment="最近一次会话活动时间（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="会话创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="会话最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_sessions_project_id_projects", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_sessions_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instances.id"], name="fk_sessions_runtime_instance_id_runtime_instances", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        comment="项目协同会话主表（强制归属 Project，四层状态分离）",
    )
    op.create_index("ix_sessions_project_status", "sessions", ["project_id", "platform_status"])
    op.create_index("ix_sessions_task", "sessions", ["task_id"])
    op.create_index("ix_sessions_runtime_instance", "sessions", ["runtime_instance_id"])

    # 12. external_session_refs（外部 Runtime 会话引用与脱敏 Remote 入口表）
    op.create_table(
        "external_session_refs",
        sa.Column("id", sa.String(length=36), nullable=False, comment="外部会话引用唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识（强制与关联 Session 同项目）"),
        sa.Column("session_id", sa.String(length=36), nullable=False, comment="关联的平台 Session 唯一标识"),
        sa.Column("runtime_instance_id", sa.String(length=36), nullable=False, comment="关联的 Runtime 实例唯一标识"),
        sa.Column("external_session_id", sa.String(length=160), nullable=False, server_default="", comment="外部 Runtime 原生会话标识（未知允许为空）"),
        sa.Column("remote_entry_kind", sa.String(length=40), nullable=False, server_default="none", comment="官方远程入口类型（none/claude_remote_control/chatgpt_remote/opencode_web/antigravity_remote/custom_safe_link）"),
        sa.Column("remote_entry_url", sa.Text(), nullable=False, server_default="", comment="脱敏后的官方 Remote 入口地址（严禁含认证 Token 或一次性凭证）"),
        sa.Column("link_status", sa.String(length=32), nullable=False, server_default="unknown", comment="外部链接可用状态（active/stale/disconnected/unknown）"),
        sa.Column("last_activity_at", sa.String(length=40), nullable=True, comment="最近一次外部会话活动时间（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="引用登记创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="引用最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_external_session_refs_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_external_session_refs_session_id_sessions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instances.id"], name="fk_external_session_refs_runtime_instance_id_runtime_instances", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_external_session_refs"),
        comment="外部 Runtime 会话引用与脱敏官方 Remote 入口表",
    )
    op.create_index("ix_external_session_refs_project_session", "external_session_refs", ["project_id", "session_id"])
    op.create_index("ix_external_session_refs_runtime_ext", "external_session_refs", ["runtime_instance_id", "external_session_id"])

    # 13. executions（任务与步骤执行单表）
    op.create_table(
        "executions",
        sa.Column("id", sa.String(length=36), nullable=False, comment="执行单唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=False, comment="关联的任务唯一标识"),
        sa.Column("step_id", sa.String(length=36), nullable=True, comment="关联的具体步骤唯一标识（可空）"),
        sa.Column("session_id", sa.String(length=36), nullable=True, comment="关联的协同会话唯一标识（可空）"),
        sa.Column("task_revision_no", sa.Integer(), nullable=False, server_default="1", comment="执行关联的任务修订版本号快照"),
        sa.Column("step_revision_no", sa.Integer(), nullable=True, comment="执行关联的步骤修订版本号快照（可空）"),
        sa.Column("platform_status", sa.String(length=32), nullable=False, server_default="queued", comment="平台执行管理状态（queued/running/waiting_interaction/succeeded/failed/cancelled/needs_revalidation）"),
        sa.Column("agent_reported_status", sa.String(length=32), nullable=False, server_default="unknown", comment="Agent 主动汇报的执行状态"),
        sa.Column("runtime_status", sa.String(length=32), nullable=False, server_default="unknown", comment="Runtime 返回的执行状态"),
        sa.Column("connection_status", sa.String(length=32), nullable=False, server_default="unknown", comment="执行期间的远程连接状态（断开不自动判失败）"),
        sa.Column("summary_note", sa.Text(), nullable=False, server_default="", comment="执行过程或结果高层摘要说明"),
        sa.Column("started_at", sa.String(length=40), nullable=True, comment="执行开始时间（UTC ISO 8601）"),
        sa.Column("finished_at", sa.String(length=40), nullable=True, comment="执行结束时间（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="执行单创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="执行单最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_executions_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_executions_task_id_tasks", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["steps.id"], name="fk_executions_step_id_steps", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_executions_session_id_sessions", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_executions"),
        comment="任务与步骤执行单表（与 Session 及 ExecutionAttempt 三层分离）",
    )
    op.create_index("ix_executions_project_task", "executions", ["project_id", "task_id"])
    op.create_index("ix_executions_session", "executions", ["session_id"])

    # 14. execution_attempts（执行尝试记录表）
    op.create_table(
        "execution_attempts",
        sa.Column("id", sa.String(length=36), nullable=False, comment="执行尝试唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("execution_id", sa.String(length=36), nullable=False, comment="所属执行单唯一标识"),
        sa.Column("attempt_no", sa.Integer(), nullable=False, comment="单调递增尝试次数序号（从 1 开始）"),
        sa.Column("runtime_instance_id", sa.String(length=36), nullable=True, comment="本次尝试使用的 Runtime 实例唯一标识（可空）"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running", comment="本次尝试运行状态（running/waiting_interaction/succeeded/failed/cancelled/timed_out）"),
        sa.Column("exit_code", sa.Integer(), nullable=True, comment="进程或调用退出码（可空）"),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default="", comment="失败或异常高层诊断摘要（不含敏感凭证）"),
        sa.Column("model_endpoint_descriptor_json", sa.Text(), nullable=True, comment="可选非敏感模型环境说明快照（JSON）"),
        sa.Column("started_at", sa.String(length=40), nullable=False, comment="本次尝试开始时间（UTC ISO 8601）"),
        sa.Column("ended_at", sa.String(length=40), nullable=True, comment="本次尝试结束时间（UTC ISO 8601，可空）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_execution_attempts_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], name="fk_execution_attempts_execution_id_executions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["runtime_instance_id"], ["runtime_instances.id"], name="fk_execution_attempts_runtime_instance_id_runtime_instances", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_execution_attempts"),
        sa.UniqueConstraint("execution_id", "attempt_no", name="uq_execution_attempts_exec_no"),
        comment="执行尝试记录表（记录每次运行尝试的 Runtime 实例、状态与退出信息）",
    )
    op.create_index("ix_execution_attempts_project_exec", "execution_attempts", ["project_id", "execution_id"])

    # 15. decisions（持久化交互式决策与人工检查点表）
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=36), nullable=False, comment="决策唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=True, comment="关联的任务唯一标识（可空）"),
        sa.Column("step_id", sa.String(length=36), nullable=True, comment="关联的步骤唯一标识（可空）"),
        sa.Column("session_id", sa.String(length=36), nullable=True, comment="关联的协同会话唯一标识（可空）"),
        sa.Column("execution_id", sa.String(length=36), nullable=True, comment="关联的执行单唯一标识（可空）"),
        sa.Column("decision_type", sa.String(length=40), nullable=False, server_default="interactive_question", comment="决策类型（interactive_question/plan_confirm/scope_change/risk_gate/direction_choice）"),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="normal", comment="风险等级（low/normal/high/critical；高风险禁止默认超时自动提交）"),
        sa.Column("title", sa.String(length=240), nullable=False, comment="决策问题主标题"),
        sa.Column("context_summary", sa.Text(), nullable=False, server_default="", comment="决策背景、影响范围与判断依据说明"),
        sa.Column("options_json", sa.Text(), nullable=False, server_default="[]", comment="结构化候选选项列表（DecisionOptionItem 数组 JSON）"),
        sa.Column("recommended_option_id", sa.String(length=64), nullable=True, comment="推荐选项 ID（供高亮提示及合规超时自动采纳）"),
        sa.Column("allow_custom_input", sa.Integer(), nullable=False, server_default="1", comment="是否允许用户附加自定义文本说明（0：否，1：是）"),
        sa.Column("timeout_policy", sa.String(length=40), nullable=False, server_default="auto_submit_recommended", comment="超时处理策略（auto_submit_recommended/manual_only/expire_on_timeout）"),
        sa.Column("timeout_sec", sa.Integer(), nullable=False, server_default="45", comment="前端交互倒计时秒数（默认 45 秒，0 表示无倒计时）"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending", comment="决策生命周期状态（pending/resolved/expired/cancelled）"),
        sa.Column("chosen_option_id", sa.String(length=64), nullable=True, comment="最终选定的选项 ID（可空）"),
        sa.Column("custom_note", sa.Text(), nullable=False, server_default="", comment="用户拍板时填写的补充说明或超时自动提交留痕说明"),
        sa.Column("resolution_payload_json", sa.Text(), nullable=False, server_default="{}", comment="完整决策结果结构化载荷（JSON 字符串）"),
        sa.Column("resolved_by", sa.String(length=40), nullable=True, comment="决策处理来源（user_manual：人工确认，timeout_auto_recommended：超时自动采纳推荐，system：系统）"),
        sa.Column("resolution_idempotency_key", sa.String(length=120), nullable=True, comment="拍板提交幂等键（防止前端网络抖动指数退避重试产生重复冲突）"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1", comment="乐观锁递增版本号"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="决策创建时间（UTC ISO 8601）"),
        sa.Column("resolved_at", sa.String(length=40), nullable=True, comment="决策完成时间（UTC ISO 8601，可空）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="决策最后更新时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_decisions_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_decisions_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["step_id"], ["steps.id"], name="fk_decisions_step_id_steps", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_decisions_session_id_sessions", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], name="fk_decisions_execution_id_executions", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_decisions"),
        comment="持久化交互式决策与人工检查点表（支持超时策略、推荐选项与幂等拍板留痕）",
    )
    op.create_index("ix_decisions_project_status", "decisions", ["project_id", "status"])
    op.create_index("ix_decisions_session_status", "decisions", ["session_id", "status"])
    op.create_index("ix_decisions_task", "decisions", ["task_id"])

    # 16. artifacts（项目交付产物与关键变更登记表）
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False, comment="产物唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=False, comment="所属项目唯一标识"),
        sa.Column("task_id", sa.String(length=36), nullable=True, comment="关联的任务唯一标识（可空）"),
        sa.Column("session_id", sa.String(length=36), nullable=True, comment="关联的协同会话唯一标识（可空）"),
        sa.Column("execution_id", sa.String(length=36), nullable=True, comment="关联的执行单唯一标识（可空）"),
        sa.Column("artifact_type", sa.String(length=40), nullable=False, server_default="markdown_note", comment="产物类型（plan_doc/git_diff_summary/test_report/markdown_note/file_ref/external_link）"),
        sa.Column("title", sa.String(length=240), nullable=False, comment="产物显示标题"),
        sa.Column("relative_path", sa.Text(), nullable=False, server_default="", comment="相对于所属 Project.root_path 的安全相对路径（可空）"),
        sa.Column("summary_content", sa.Text(), nullable=False, server_default="", comment="产物高层摘要或 Markdown 内容"),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False, server_default="", comment="内容或文件 SHA-256 校验值（可空）"),
        sa.Column("source_task_revision_no", sa.Integer(), nullable=True, comment="产出时关联的任务修订版本号（可空）"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}", comment="扩展非敏感元数据（JSON 字符串）"),
        sa.Column("created_by", sa.String(length=40), nullable=False, server_default="user", comment="登记来源（user/mcp_agent/runtime/system）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="产物登记创建时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_artifacts_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_artifacts_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_artifacts_session_id_sessions", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], name="fk_artifacts_execution_id_executions", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        comment="项目交付产物与关键变更登记表（记录元数据、校验值与关联修订版本）",
    )
    op.create_index("ix_artifacts_project_task", "artifacts", ["project_id", "task_id"])
    op.create_index("ix_artifacts_session", "artifacts", ["session_id"])

    # 17. events（全局领域事件持久化审计流表）
    op.create_table(
        "events",
        sa.Column("seq_id", sa.Integer(), autoincrement=True, nullable=False, comment="全局严格递增事件序列号（客户端增量订阅游标）"),
        sa.Column("event_id", sa.String(length=36), nullable=False, comment="事件全局唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=True, comment="关联项目唯一标识（系统级事件可空）"),
        sa.Column("entity_type", sa.String(length=40), nullable=False, comment="变更领域实体类型（project/task/step/session/execution/decision/artifact/runtime_instance/security）"),
        sa.Column("entity_id", sa.String(length=64), nullable=False, comment="变更领域实体唯一标识"),
        sa.Column("event_type", sa.String(length=80), nullable=False, comment="标准领域事件名称（如 decision.created / task.revision_created）"),
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="user", comment="触发主体类型（user/mcp_agent/runtime/system）"),
        sa.Column("actor_id", sa.String(length=120), nullable=False, server_default="", comment="触发主体非敏感标识"),
        sa.Column("correlation_id", sa.String(length=120), nullable=False, server_default="", comment="请求或命令关联追踪 ID"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}", comment="脱敏后的事件结构化载荷（JSON 字符串，严禁写入密钥）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="事件发生时间（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_events_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("seq_id", name="pk_events"),
        sa.UniqueConstraint("event_id", name="uq_events_event_id"),
        comment="全局领域事件持久化审计流表（与业务写操作同事务提交，支持断线按 seq_id 补拉）",
    )
    op.create_index("ix_events_project_seq", "events", ["project_id", "seq_id"])
    op.create_index("ix_events_entity", "events", ["entity_type", "entity_id"])

    # 18. device_sessions（移动端与桌面端扫码配对设备及访问令牌鉴权表）
    op.create_table(
        "device_sessions",
        sa.Column("device_id", sa.String(length=36), nullable=False, comment="设备唯一标识（UUID）"),
        sa.Column("device_name", sa.String(length=120), nullable=False, comment="配对设备显示名称（如 iPhone / Desktop Browser）"),
        sa.Column("auth_token_hash", sa.String(length=64), nullable=False, comment="设备访问令牌 SHA-256 哈希值（严禁存明文）"),
        sa.Column("tailscale_ip", sa.String(length=160), nullable=True, comment="最近连接的 Tailscale/ZeroTier/局域网漫游 IP 列表"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="配对创建时间（UTC ISO 8601）"),
        sa.Column("last_active_at", sa.String(length=40), nullable=False, comment="最近活跃访问时间（UTC ISO 8601）"),
        sa.Column("is_revoked", sa.Integer(), nullable=False, server_default="0", comment="是否已吊销（0：有效，1：已吊销）"),
        sa.PrimaryKeyConstraint("device_id", name="pk_device_sessions"),
        sa.UniqueConstraint("auth_token_hash", name="uq_device_sessions_auth_token_hash"),
        comment="移动端与桌面端扫码配对设备及访问令牌鉴权表",
    )
    op.create_index("ix_device_sessions_token_hash", "device_sessions", ["auth_token_hash"])

    # 19. push_subscriptions（移动端 PWA 原生 Web Push 推送订阅凭据表）
    op.create_table(
        "push_subscriptions",
        sa.Column("subscription_id", sa.String(length=64), nullable=False, comment="订阅唯一标识（SHA-256(endpoint)）"),
        sa.Column("device_id", sa.String(length=36), nullable=False, comment="关联的配对设备唯一标识"),
        sa.Column("endpoint", sa.Text(), nullable=False, comment="Web Push 网关推送目标 URL"),
        sa.Column("p256dh_key", sa.Text(), nullable=False, comment="客户端加密公钥（Base64 URL-safe）"),
        sa.Column("auth_key", sa.Text(), nullable=False, comment="客户端鉴权密钥（Base64 URL-safe）"),
        sa.Column("user_agent", sa.Text(), nullable=True, comment="客户端浏览器 User-Agent"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="订阅创建时间（UTC ISO 8601）"),
        sa.Column("last_pushed_at", sa.String(length=40), nullable=True, comment="最近一次成功推送时间（UTC ISO 8601）"),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1", comment="是否有效（0：已失效，1：有效）"),
        sa.PrimaryKeyConstraint("subscription_id", name="pk_push_subscriptions"),
        comment="移动端 PWA 原生 Web Push 推送订阅凭据表",
    )
    op.create_index("ix_push_subscriptions_device_active", "push_subscriptions", ["device_id", "is_active"])

    # 20. command_idempotency（客户端写命令与交互式决策提交幂等键持久化表）
    op.create_table(
        "command_idempotency",
        sa.Column("idempotency_key", sa.String(length=120), nullable=False, comment="客户端生成的唯一幂等键（Idempotency-Key）"),
        sa.Column("command_type", sa.String(length=64), nullable=False, comment="命令或操作类型（如 decision.resolve / task.revise）"),
        sa.Column("target_entity_type", sa.String(length=40), nullable=False, server_default="", comment="关联实体类型"),
        sa.Column("target_entity_id", sa.String(length=64), nullable=False, server_default="", comment="关联实体主键"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="done", comment="处理终态（done：已完成，error：失败可重放）"),
        sa.Column("response_snapshot_json", sa.Text(), nullable=False, server_default="{}", comment="幂等重放时直接返回的脱敏响应快照（JSON）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="首次处理时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="最后更新时间（UTC ISO 8601）"),
        sa.PrimaryKeyConstraint("idempotency_key", name="pk_command_idempotency"),
        comment="客户端写命令与交互式决策提交幂等键持久化表",
    )
    op.create_index("ix_command_idempotency_target", "command_idempotency", ["target_entity_type", "target_entity_id"])

    # 21. table_column_comments（全库数据表名与字段中文注释元数据字典表）
    op.create_table(
        "table_column_comments",
        sa.Column("table_name", sa.String(length=80), nullable=False, comment="数据表英文名称"),
        sa.Column("table_comment", sa.Text(), nullable=False, comment="数据表中文注释说明"),
        sa.Column("column_name", sa.String(length=80), nullable=False, comment="字段英文名称"),
        sa.Column("column_comment", sa.Text(), nullable=False, comment="字段中文注释说明"),
        sa.PrimaryKeyConstraint("table_name", "column_name", name="pk_table_column_comments"),
        comment="全库数据表名与字段中文注释元数据字典表",
    )

    # 同步全部 21 张表与字段的中文注释至 table_column_comments，并写入 Runtime / Machine 基线种子
    conn = op.get_bind()
    sync_comments_to_connection(conn)

    now = utc_now_iso()
    for seed in _default_provider_seeds():
        caps = [c.model_dump() for c in seed["capabilities"]]  # type: ignore[union-attr]
        conn.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO runtime_providers
                (id, provider_key, display_name, integration_type, documentation_url, default_capabilities_json, enabled, created_at, updated_at)
                VALUES (:id, :provider_key, :display_name, :integration_type, :documentation_url, :caps, 1, :now, :now)
                """
            ),
            {
                "id": str(seed["id"]),
                "provider_key": str(seed["provider_key"]),
                "display_name": str(seed["display_name"]),
                "integration_type": str(seed["integration_type"]),
                "documentation_url": str(seed["documentation_url"]),
                "caps": json.dumps(caps, ensure_ascii=False),
                "now": now,
            },
        )

    hostname = (socket.gethostname() or "localhost")[:120]
    conn.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO machines
            (id, display_name, os, hostname_alias, connection_kind, health_status, last_seen_at, created_at, updated_at)
            VALUES (:id, :display_name, :os, :hostname, 'local', 'online', :now, :now, :now)
            """
        ),
        {
            "id": LOCAL_MACHINE_ID,
            "display_name": f"Local Host ({hostname})",
            "os": _detect_os_str(),
            "hostname": hostname,
            "now": now,
        },
    )


def downgrade() -> None:
    """按外键拓扑逆序卸载全部 21 张规范表。"""
    for t_name in CLEAN_SLATE_21_TABLES_DROP_ORDER:
        op.drop_table(t_name)
