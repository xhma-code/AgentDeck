# -*- coding: utf-8 -*-
"""
Runtime 注册中心应用服务（RuntimeRegistryService）。
管理 RuntimeProvider、Machine 与 RuntimeInstance 的最小登记与诚实能力矩阵声明（对齐 v1.3 第 5-10 节）。
"""

from __future__ import annotations

import json
import platform
import socket
import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import utc_now_iso
from backend.db.models.runtime import (
    MachineModel,
    RuntimeInstanceModel,
    RuntimeProviderModel,
)
from backend.db.uow import SqlAlchemyUnitOfWork
from backend.domain.enums import (
    ActorType,
    ControlLevel,
    EvidenceSource,
    IntegrationType,
    MachineConnectionKind,
    MachineOS,
    RuntimeHealthStatus,
    RuntimeProviderKey,
    SupportStatus,
)
from backend.domain.exceptions import EntityNotFoundError
from backend.domain.services.capability_policy import (
    validate_runtime_control_and_capabilities,
)
from backend.domain.types import CapabilityManifestItem, ModelEndpointDescriptor

LOCAL_MACHINE_ID = "local-machine"


def _detect_local_machine_os() -> MachineOS:
    sys_name = platform.system().lower()
    if "windows" in sys_name:
        return MachineOS.WINDOWS
    if "darwin" in sys_name:
        return MachineOS.MACOS
    return MachineOS.LINUX


def _default_provider_seeds() -> list[dict[str, object]]:
    """
    构造 4 大官方 Runtime 的初始基线定义（对齐 v1.3 L0-L3 分级与 MCP 协作协议解耦）：
    - L0：remote_entry（官方 Remote 入口）
    - L1：runtime_registry_and_status（Runtime 注册、状态与能力清单管理）
    - MCP：mcp_client_collab（Agent -> AgentDeck 协作协议与工具接口，独立记录已验证的 MCP 协议版本）
    - L2：programmatic_control（程序化控制；SDK/HTTP API/Stdio 是技术途径，不等于 L3）
    - L3：approval_and_resume（审批与恢复能力）
    严格遵守诚实声明规则：未在用户当前环境实测通过的能力一律标为 unverified！
    """
    return [
        {
            "id": "provider-claude-code",
            "provider_key": RuntimeProviderKey.CLAUDE_CODE.value,
            "display_name": "Claude Code",
            "integration_type": IntegrationType.MCP_CLIENT.value,
            "documentation_url": "https://docs.anthropic.com/en/docs/claude-code",
            "capabilities": [
                CapabilityManifestItem(
                    capability="remote_entry",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L0 官方 Remote 入口：依赖用户账号套餐及当前模型服务组合是否支持 Claude Remote Control",
                ),
                CapabilityManifestItem(
                    capability="runtime_registry_and_status",
                    support_status=SupportStatus.SUPPORTED,
                    evidence=EvidenceSource.INTEGRATION_TEST,
                    runtime_version="1.x",
                    connector_version="phase3-registry",
                    verified_at="2026-10-31T00:00:00Z",
                    limitations="L1 支持登记实例、宿主机器、外部会话映射与独立连接状态",
                ),
                CapabilityManifestItem(
                    capability="mcp_client_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="MCP 主动协同协议（Agent -> AgentDeck）：需按实际客户端测试结果记录已验证的 MCP 协议版本",
                ),
                CapabilityManifestItem(
                    capability="programmatic_control",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L2 程序化控制（SDK 途径）：待 Phase 6 在 Windows 及国内兼容模型环境下完成 Spike 验证",
                ),
                CapabilityManifestItem(
                    capability="approval_and_resume",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L3 审批与恢复能力：未经真实实测验证前严禁标为 supported",
                ),
            ],
        },
        {
            "id": "provider-codex",
            "provider_key": RuntimeProviderKey.CODEX.value,
            "display_name": "OpenAI Codex",
            "integration_type": IntegrationType.SDK.value,
            "documentation_url": "https://platform.openai.com/docs",
            "capabilities": [
                CapabilityManifestItem(
                    capability="remote_entry",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L0 官方 Remote 入口：优先复用官方 ChatGPT Mobile Remote Connections",
                ),
                CapabilityManifestItem(
                    capability="runtime_registry_and_status",
                    support_status=SupportStatus.SUPPORTED,
                    evidence=EvidenceSource.INTEGRATION_TEST,
                    runtime_version="1.x",
                    connector_version="phase3-registry",
                    verified_at="2026-10-31T00:00:00Z",
                    limitations="L1 支持登记实例、宿主机器、外部会话映射与独立连接状态",
                ),
                CapabilityManifestItem(
                    capability="mcp_client_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="MCP 主动协同协议（Agent -> AgentDeck）：需按实际客户端测试结果记录已验证的 MCP 协议版本",
                ),
                CapabilityManifestItem(
                    capability="programmatic_control",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L2 程序化控制（App Server Stdio / SDK 途径）：待 Phase 6 本地验证",
                ),
                CapabilityManifestItem(
                    capability="approval_and_resume",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L3 审批与恢复能力：待后续实测验证",
                ),
            ],
        },
        {
            "id": "provider-opencode",
            "provider_key": RuntimeProviderKey.OPENCODE.value,
            "display_name": "OpenCode",
            "integration_type": IntegrationType.HTTP_API.value,
            "documentation_url": "https://opencode.ai/docs",
            "capabilities": [
                CapabilityManifestItem(
                    capability="remote_entry",
                    support_status=SupportStatus.SUPPORTED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L0 官方 Remote 入口：通过官方 OpenCode Web / Serve 入口访问，禁止直读内部 opencode.db",
                ),
                CapabilityManifestItem(
                    capability="runtime_registry_and_status",
                    support_status=SupportStatus.SUPPORTED,
                    evidence=EvidenceSource.INTEGRATION_TEST,
                    runtime_version="1.x",
                    connector_version="phase3-registry",
                    verified_at="2026-10-31T00:00:00Z",
                    limitations="L1 支持登记实例、宿主机器、外部会话映射与独立连接状态",
                ),
                CapabilityManifestItem(
                    capability="mcp_client_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="MCP 主动协同协议（Agent -> AgentDeck）：需按实际客户端测试结果记录已验证的 MCP 协议版本",
                ),
                CapabilityManifestItem(
                    capability="programmatic_control",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L2 程序化控制（公开 HTTP API 途径）：待 Phase 6 固定版本完成契约验证",
                ),
                CapabilityManifestItem(
                    capability="approval_and_resume",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L3 审批与恢复能力：待后续实测验证",
                ),
            ],
        },
        {
            "id": "provider-antigravity",
            "provider_key": RuntimeProviderKey.ANTIGRAVITY.value,
            "display_name": "Google Antigravity",
            "integration_type": IntegrationType.MCP_CLIENT.value,
            "documentation_url": "https://idx.dev",
            "capabilities": [
                CapabilityManifestItem(
                    capability="remote_entry",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L0 官方 Remote 入口：仅限官方公开入口，严禁使用私有 Language Server RPC",
                ),
                CapabilityManifestItem(
                    capability="runtime_registry_and_status",
                    support_status=SupportStatus.SUPPORTED,
                    evidence=EvidenceSource.INTEGRATION_TEST,
                    runtime_version="2.0",
                    connector_version="phase3-registry",
                    verified_at="2026-10-31T00:00:00Z",
                    limitations="L1 支持登记实例、宿主机器、外部会话映射与独立连接状态",
                ),
                CapabilityManifestItem(
                    capability="mcp_client_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="MCP 主动协同协议（Agent -> AgentDeck）：需按实际客户端测试结果记录已验证的 MCP 协议版本",
                ),
                CapabilityManifestItem(
                    capability="programmatic_control",
                    support_status=SupportStatus.UNSUPPORTED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L2 程序化控制：当前无公开稳定第三方本地会话控制契约，保持 L0/L1 轻量协同",
                ),
                CapabilityManifestItem(
                    capability="approval_and_resume",
                    support_status=SupportStatus.UNSUPPORTED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="L3 审批与恢复能力：无公开第三方审批拦截契约",
                ),
            ],
        },
    ]


class RuntimeRegistryService:
    """Runtime 产品、宿主机器与运行实例登记服务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def ensure_seed_data(self) -> None:
        """幂等初始化内置 4 大 RuntimeProvider 与当前本机 Machine 记录。"""
        now = utc_now_iso()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            for seed in _default_provider_seeds():
                pkey = str(seed["provider_key"])
                caps = [c.model_dump() for c in seed["capabilities"]]  # type: ignore[union-attr]
                caps_json = json.dumps(caps, ensure_ascii=False)
                existing = uow.session.execute(
                    select(RuntimeProviderModel).where(RuntimeProviderModel.provider_key == pkey)
                ).scalar_one_or_none()
                if existing is None:
                    uow.session.add(
                        RuntimeProviderModel(
                            id=str(seed["id"]),
                            provider_key=pkey,
                            display_name=str(seed["display_name"]),
                            integration_type=str(seed["integration_type"]),
                            documentation_url=str(seed["documentation_url"]),
                            default_capabilities_json=caps_json,
                            enabled=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                else:
                    existing.default_capabilities_json = caps_json
                    existing.updated_at = now

            local_machine = uow.session.get(MachineModel, LOCAL_MACHINE_ID)
            if local_machine is None:
                hostname = socket.gethostname() or "localhost"
                uow.session.add(
                    MachineModel(
                        id=LOCAL_MACHINE_ID,
                        display_name=f"Local Host ({hostname})",
                        os=_detect_local_machine_os().value,
                        hostname_alias=hostname[:120],
                        connection_kind=MachineConnectionKind.LOCAL.value,
                        health_status=RuntimeHealthStatus.ONLINE.value,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            uow.commit()

    def register_machine(
        self,
        *,
        display_name: str,
        os_type: MachineOS,
        hostname_alias: str = "",
        connection_kind: MachineConnectionKind = MachineConnectionKind.LOCAL,
        health_status: RuntimeHealthStatus = RuntimeHealthStatus.UNKNOWN,
    ) -> MachineModel:
        """登记一台新的宿主机器并记录审计事件。"""
        now = utc_now_iso()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            machine = MachineModel(
                id=str(uuid.uuid4()),
                display_name=(display_name or "Host").strip()[:120],
                os=os_type.value,
                hostname_alias=(hostname_alias or "").strip()[:120],
                connection_kind=connection_kind.value,
                health_status=health_status.value,
                last_seen_at=now if health_status == RuntimeHealthStatus.ONLINE else None,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(machine)
            uow.record_event(
                entity_type="machine",
                entity_id=machine.id,
                event_type="machine.registered",
                actor_type=ActorType.USER,
                payload={
                    "display_name": machine.display_name,
                    "os": machine.os,
                    "connection_kind": machine.connection_kind,
                },
            )
            uow.commit()
            return machine

    def register_runtime_instance(
        self,
        *,
        provider_key: RuntimeProviderKey,
        machine_id: str = LOCAL_MACHINE_ID,
        display_name: Optional[str] = None,
        runtime_version: str = "unknown",
        connector_version: str = "none",
        control_level: ControlLevel = ControlLevel.L0,
        health_status: RuntimeHealthStatus = RuntimeHealthStatus.UNKNOWN,
        capabilities: Optional[Sequence[CapabilityManifestItem]] = None,
        model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = None,
    ) -> RuntimeInstanceModel:
        """登记具体宿主机器上的 Runtime 实例。"""
        now = utc_now_iso()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            provider = uow.session.execute(
                select(RuntimeProviderModel).where(
                    RuntimeProviderModel.provider_key == provider_key.value
                )
            ).scalar_one_or_none()
            if provider is None:
                raise EntityNotFoundError(
                    problem="指定的 RuntimeProvider 不存在",
                    cause=f"未找到 provider_key={provider_key.value}",
                    fix="请先执行 RuntimeProvider 种子初始化。",
                )

            machine = uow.session.get(MachineModel, machine_id)
            if machine is None:
                raise EntityNotFoundError(
                    problem="指定的宿主机器不存在",
                    cause=f"未找到 machine_id={machine_id}",
                    fix="请先登记目标机器或使用默认 local-machine。",
                )

            clean_ver = (runtime_version or "unknown").strip()[:64] or "unknown"
            if capabilities is not None:
                raw_caps = list(capabilities)
            else:
                raw_caps = [
                    CapabilityManifestItem.model_validate(item)
                    for item in json.loads(provider.default_capabilities_json or "[]")
                ]

            validated_caps = validate_runtime_control_and_capabilities(
                control_level=control_level,
                capabilities=raw_caps,
                runtime_version=clean_ver,
            )
            caps_json = json.dumps([c.model_dump() for c in validated_caps], ensure_ascii=False)

            endpoint_json = (
                json.dumps(model_endpoint_descriptor.model_dump(), ensure_ascii=False)
                if model_endpoint_descriptor is not None
                else None
            )

            inst = RuntimeInstanceModel(
                id=str(uuid.uuid4()),
                provider_id=provider.id,
                machine_id=machine.id,
                display_name=(display_name or f"{provider.display_name} @ {machine.display_name}").strip()[:120],
                runtime_version=clean_ver,
                connector_version=(connector_version or "none").strip()[:64] or "none",
                control_level=control_level.value,
                health_status=health_status.value,
                connection_profile_json="{}",
                capability_manifest_json=caps_json,
                model_endpoint_descriptor_json=endpoint_json,
                last_seen_at=now if health_status == RuntimeHealthStatus.ONLINE else None,
                created_at=now,
                updated_at=now,
            )
            uow.session.add(inst)
            uow.record_event(
                entity_type="runtime_instance",
                entity_id=inst.id,
                event_type="runtime_instance.registered",
                actor_type=ActorType.USER,
                payload={
                    "provider_key": provider.provider_key,
                    "machine_id": machine.id,
                    "control_level": inst.control_level,
                    "runtime_version": inst.runtime_version,
                },
            )
            uow.commit()
            return inst

    def update_machine(
        self,
        machine_id: str,
        *,
        display_name: Optional[str] = None,
        hostname_alias: Optional[str] = None,
        connection_kind: Optional[MachineConnectionKind] = None,
        health_status: Optional[RuntimeHealthStatus] = None,
    ) -> MachineModel:
        """更新宿主机器显示名、别名、连接方式或健康状态。"""
        now = utc_now_iso()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            machine = uow.session.get(MachineModel, machine_id)
            if machine is None:
                raise EntityNotFoundError(
                    problem="指定的宿主机器不存在",
                    cause=f"未找到 machine_id={machine_id}",
                    fix="请确认机器 ID 正确。",
                )
            if display_name is not None and display_name.strip():
                machine.display_name = display_name.strip()[:120]
            if hostname_alias is not None:
                machine.hostname_alias = hostname_alias.strip()[:120]
            if connection_kind is not None:
                machine.connection_kind = connection_kind.value
            if health_status is not None:
                machine.health_status = health_status.value
                if health_status == RuntimeHealthStatus.ONLINE:
                    machine.last_seen_at = now
            machine.updated_at = now

            uow.record_event(
                entity_type="machine",
                entity_id=machine.id,
                event_type="machine.updated",
                actor_type=ActorType.USER,
                payload={
                    "display_name": machine.display_name,
                    "connection_kind": machine.connection_kind,
                    "health_status": machine.health_status,
                },
            )
            uow.commit()
            return machine

    def get_runtime_instance(self, instance_id: str) -> RuntimeInstanceModel:
        """获取单个 Runtime 实例详情。"""
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            inst = uow.session.get(RuntimeInstanceModel, instance_id)
            if inst is None:
                raise EntityNotFoundError(
                    problem="目标 Runtime 实例不存在",
                    cause=f"未找到 runtime_instance_id={instance_id}",
                    fix="请核对 Runtime 实例 ID。",
                )
            return inst

    def update_runtime_instance(
        self,
        instance_id: str,
        *,
        display_name: Optional[str] = None,
        runtime_version: Optional[str] = None,
        connector_version: Optional[str] = None,
        control_level: Optional[ControlLevel] = None,
        health_status: Optional[RuntimeHealthStatus] = None,
        capabilities: Optional[Sequence[CapabilityManifestItem]] = None,
        model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = None,
    ) -> RuntimeInstanceModel:
        """
        更新 Runtime 实例状态、版本与能力验证证据。
        任何 L2（程序化控制）/ L3（审批与恢复能力）等级与能力声明变更均强制经过 capability_policy 校验。
        """
        now = utc_now_iso()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            inst = uow.session.get(RuntimeInstanceModel, instance_id)
            if inst is None:
                raise EntityNotFoundError(
                    problem="目标 Runtime 实例不存在",
                    cause=f"未找到 runtime_instance_id={instance_id}",
                    fix="请核对 Runtime 实例 ID。",
                )

            eff_version = (
                (runtime_version.strip()[:64] or "unknown")
                if runtime_version is not None
                else inst.runtime_version
            )
            eff_level = (
                control_level
                if control_level is not None
                else ControlLevel(inst.control_level)
            )
            if capabilities is not None:
                raw_caps = list(capabilities)
            else:
                raw_caps = [
                    CapabilityManifestItem.model_validate(item)
                    for item in json.loads(inst.capability_manifest_json or "[]")
                ]

            validated_caps = validate_runtime_control_and_capabilities(
                control_level=eff_level,
                capabilities=raw_caps,
                runtime_version=eff_version,
            )

            if display_name is not None and display_name.strip():
                inst.display_name = display_name.strip()[:120]
            inst.runtime_version = eff_version
            if connector_version is not None:
                inst.connector_version = connector_version.strip()[:64] or "none"
            inst.control_level = eff_level.value
            if health_status is not None:
                inst.health_status = health_status.value
                if health_status == RuntimeHealthStatus.ONLINE:
                    inst.last_seen_at = now
            inst.capability_manifest_json = json.dumps(
                [c.model_dump() for c in validated_caps], ensure_ascii=False
            )
            if model_endpoint_descriptor is not None:
                inst.model_endpoint_descriptor_json = json.dumps(
                    model_endpoint_descriptor.model_dump(), ensure_ascii=False
                )
            inst.updated_at = now

            uow.record_event(
                entity_type="runtime_instance",
                entity_id=inst.id,
                event_type="runtime_instance.updated",
                actor_type=ActorType.USER,
                payload={
                    "control_level": inst.control_level,
                    "health_status": inst.health_status,
                    "runtime_version": inst.runtime_version,
                    "connector_version": inst.connector_version,
                },
            )
            uow.commit()
            return inst

    def list_providers(self) -> list[RuntimeProviderModel]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            return list(
                uow.session.execute(
                    select(RuntimeProviderModel).order_by(RuntimeProviderModel.provider_key.asc())
                )
                .scalars()
                .all()
            )

    def list_machines(self) -> list[MachineModel]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            return list(
                uow.session.execute(select(MachineModel).order_by(MachineModel.created_at.asc()))
                .scalars()
                .all()
            )

    def list_runtime_instances(self) -> list[RuntimeInstanceModel]:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            return list(
                uow.session.execute(
                    select(RuntimeInstanceModel).order_by(RuntimeInstanceModel.created_at.desc())
                )
                .scalars()
                .all()
            )

    def record_provider_mcp_verification(
        self,
        *,
        provider_key: str,
        runtime_version: str,
        verified_protocol_versions: Sequence[str],
        evidence: EvidenceSource = EvidenceSource.INTEGRATION_TEST,
        limitations: str = "MCP 主动协同协议已通过真实验证",
    ) -> RuntimeProviderModel:
        """记录指定 RuntimeProvider 经实测通过的 MCP 协作协议版本与证据。"""
        clean_protocols = [str(v).strip() for v in verified_protocol_versions if str(v).strip()]
        now = utc_now_iso()
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.session is not None
            provider = uow.session.execute(
                select(RuntimeProviderModel).where(RuntimeProviderModel.provider_key == provider_key)
            ).scalar_one_or_none()
            if provider is None:
                raise EntityNotFoundError(
                    problem="未找到目标 RuntimeProvider",
                    cause=f"provider_key='{provider_key}' 不存在",
                    fix="请确认 provider_key 是否为内置官方 Provider。",
                )

            raw_caps = [
                CapabilityManifestItem.model_validate(item)
                for item in json.loads(provider.default_capabilities_json or "[]")
            ]
            updated_caps: list[CapabilityManifestItem] = []
            found = False
            for c in raw_caps:
                cap_name = "mcp_client_collab" if c.capability == "mcp_collab" else c.capability
                if cap_name == "mcp_client_collab":
                    found = True
                    updated_caps.append(
                        CapabilityManifestItem(
                            capability="mcp_client_collab",
                            support_status=SupportStatus.SUPPORTED,
                            evidence=evidence,
                            runtime_version=runtime_version.strip() or "1.x",
                            connector_version="phase4-mcp-v2",
                            verified_at=now,
                            verified_protocol_versions=clean_protocols,
                            limitations=limitations,
                        )
                    )
                else:
                    updated_caps.append(c)

            if not found:
                updated_caps.append(
                    CapabilityManifestItem(
                        capability="mcp_client_collab",
                        support_status=SupportStatus.SUPPORTED,
                        evidence=evidence,
                        runtime_version=runtime_version.strip() or "1.x",
                        connector_version="phase4-mcp-v2",
                        verified_at=now,
                        verified_protocol_versions=clean_protocols,
                        limitations=limitations,
                    )
                )

            validated = validate_runtime_control_and_capabilities(
                control_level=ControlLevel.L1,
                capabilities=updated_caps,
                runtime_version=runtime_version,
            )
            provider.default_capabilities_json = json.dumps(
                [c.model_dump() for c in validated], ensure_ascii=False
            )
            provider.updated_at = now
            uow.record_event(
                entity_type="runtime_provider",
                entity_id=provider.id,
                event_type="runtime_provider.mcp_verified",
                actor_type=ActorType.SYSTEM,
                payload={
                    "provider_key": provider.provider_key,
                    "runtime_version": runtime_version,
                    "verified_protocol_versions": clean_protocols,
                },
            )
            uow.commit()
            return provider


