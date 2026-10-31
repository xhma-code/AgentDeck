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
    构造 4 大官方 Runtime 的初始基线定义。
    严格遵守 v1.3 诚实声明规则：未在用户当前环境实测通过的 L2/L3 能力一律标为 unverified！
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
                    limitations="依赖用户账号套餐及当前模型服务组合是否支持官方 Remote Control",
                ),
                CapabilityManifestItem(
                    capability="mcp_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 4 AgentDeck MCP Server 实测验证",
                ),
                CapabilityManifestItem(
                    capability="sdk_programmatic_control",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 6 在 Windows 及国内兼容模型环境下完成 SDK Spike 验证",
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
                    limitations="优先复用官方 ChatGPT Mobile Remote Connections",
                ),
                CapabilityManifestItem(
                    capability="mcp_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 4 实测验证",
                ),
                CapabilityManifestItem(
                    capability="app_server_stdio",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 6 验证 Codex Python SDK / App Server 本地 stdio",
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
                    limitations="通过官方 OpenCode Web / Serve 入口访问，禁止直读内部 opencode.db",
                ),
                CapabilityManifestItem(
                    capability="mcp_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 4 实测验证",
                ),
                CapabilityManifestItem(
                    capability="http_api_control",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 6 固定版本完成公开 HTTP API 契约验证",
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
                    limitations="仅限官方公开入口，严禁使用私有 Language Server RPC",
                ),
                CapabilityManifestItem(
                    capability="mcp_collab",
                    support_status=SupportStatus.UNVERIFIED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="待 Phase 4 实测验证",
                ),
                CapabilityManifestItem(
                    capability="programmatic_control",
                    support_status=SupportStatus.UNSUPPORTED,
                    evidence=EvidenceSource.OFFICIAL_DOCS,
                    limitations="当前无公开稳定第三方本地会话控制契约，保持 L0/L1 轻量协同",
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
                existing = uow.session.execute(
                    select(RuntimeProviderModel).where(RuntimeProviderModel.provider_key == pkey)
                ).scalar_one_or_none()
                if existing is None:
                    caps = [c.model_dump() for c in seed["capabilities"]]  # type: ignore[union-attr]
                    uow.session.add(
                        RuntimeProviderModel(
                            id=str(seed["id"]),
                            provider_key=pkey,
                            display_name=str(seed["display_name"]),
                            integration_type=str(seed["integration_type"]),
                            documentation_url=str(seed["documentation_url"]),
                            default_capabilities_json=json.dumps(caps, ensure_ascii=False),
                            enabled=1,
                            created_at=now,
                            updated_at=now,
                        )
                    )

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

            if capabilities is not None:
                caps_json = json.dumps([c.model_dump() for c in capabilities], ensure_ascii=False)
            else:
                caps_json = provider.default_capabilities_json

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
                runtime_version=(runtime_version or "unknown").strip()[:64] or "unknown",
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

