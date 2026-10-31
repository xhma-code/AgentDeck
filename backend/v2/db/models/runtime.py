# -*- coding: utf-8 -*-
"""
Runtime 注册域 ORM 模型：RuntimeProvider、Machine、RuntimeInstance（对齐 v1.3 第 9-10 节）。
注意：RuntimeProvider 表示 Agent 产品而非大模型服务商；严禁保存 API Key 或 CCSwitch 配置。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.v2.db.base import V2Base


class RuntimeProviderModel(V2Base):
    """Agent Runtime 产品定义表（如 claude_code / codex / opencode / antigravity）。"""

    __tablename__ = "runtime_providers"
    __table_args__ = {"comment": "Agent Runtime 产品定义与默认能力基线表"}

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="Runtime 产品记录唯一标识"
    )
    provider_key: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
        comment="产品标准唯一键（claude_code/codex/opencode/antigravity）",
    )
    display_name: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="Agent 产品显示名称"
    )
    integration_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="默认集成形态（remote_only/mcp_client/http_api/sdk/cli）",
    )
    documentation_url: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="官方集成参考文档链接"
    )
    default_capabilities_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        comment="默认能力声明矩阵（CapabilityManifest 列表 JSON，未知项标 unverified）",
    )
    enabled: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="是否启用（0：禁用，1：启用）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="记录创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="记录最后更新时间（UTC ISO 8601）"
    )


class MachineModel(V2Base):
    """宿主机器登记表。"""

    __tablename__ = "machines"
    __table_args__ = {"comment": "Agent 运行宿主机器登记与在线状态表"}

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="宿主机器唯一标识（UUID 或 local-machine）"
    )
    display_name: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="机器显示名称"
    )
    os: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="操作系统类型（windows/macos/linux）"
    )
    hostname_alias: Mapped[str] = mapped_column(
        String(120), nullable=False, default="", comment="主机名或网络友好别名"
    )
    connection_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="local",
        comment="连接通道类型（local/tailscale/lan/vendor_remote/other）",
    )
    health_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="机器健康状态（online/offline/degraded/unknown）",
    )
    last_seen_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="最近一次探测在线时间（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="机器登记创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="机器信息最后更新时间（UTC ISO 8601）"
    )


class RuntimeInstanceModel(V2Base):
    """具体宿主机器上的 Runtime 实例与能力声明表。"""

    __tablename__ = "runtime_instances"
    __table_args__ = (
        Index("ix_runtime_instances_provider_machine", "provider_id", "machine_id"),
        {"comment": "Runtime 运行实例、L0-L3 控制等级与能力验证清单表"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="Runtime 实例唯一标识（UUID）"
    )
    provider_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runtime_providers.id", ondelete="RESTRICT"),
        nullable=False,
        comment="关联的 RuntimeProvider 主键",
    )
    machine_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("machines.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属宿主机器主键",
    )
    display_name: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="Runtime 实例显示名称"
    )
    runtime_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unknown", comment="Runtime 版本号（未知标 unknown）"
    )
    connector_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="none", comment="平台 Connector 版本标识"
    )
    control_level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="L0", comment="已验证最高控制等级（L0/L1/L2/L3）"
    )
    health_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="实例健康状态（online/offline/degraded/unknown）",
    )
    connection_profile_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="非敏感连接配置摘要（JSON，严禁存密钥）"
    )
    capability_manifest_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", comment="实例级已验证能力清单（CapabilityManifest 数组 JSON）"
    )
    model_endpoint_descriptor_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="可选非敏感模型环境说明（ModelEndpointDescriptor JSON，严禁存 Base URL 或 API Key）",
    )
    last_seen_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="最近活跃或健康检查时间（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="实例登记创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="实例最后更新时间（UTC ISO 8601）"
    )

