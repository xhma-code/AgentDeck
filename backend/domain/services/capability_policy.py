# -*- coding: utf-8 -*-
"""
Runtime 控制等级（L0-L3）与能力验证证据（CapabilityManifest）领域守卫。

严格对齐 AgentDeck v1.3 能力分级定义：
- L0：官方 Remote 入口（remote_entry）
- L1：Runtime 注册、状态与能力信息管理（runtime_registry_and_status / mcp_collab）
- L2：程序化控制（programmatic_control；SDK / HTTP API / App Server Stdio 是实现 L2 的技术途径，不等于 L3）
- L3：审批与恢复能力（approval_and_resume；在 L2 基础上具备经验证的审批拦截与会话恢复能力）

核心铁律：
1. 官方 Remote 只代表 L0 远程入口，绝不能作为 L2 或 L3 的依据。
2. 任何 L2（程序化控制）或 L3（审批与恢复）能力标为 supported 时，必须提供真实验证证据
   （evidence 属于 integration_test / local_test / spike_verified）、非空 verified_at 以及具体 runtime_version。
3. 严禁把“接入了 SDK”直接等同于 L3；声明 L3 必须同时具备已验证的 L2 程序化控制能力与 L3 审批与恢复能力。
"""

from __future__ import annotations

from typing import Sequence

from backend.domain.enums import ControlLevel, EvidenceSource, SupportStatus
from backend.domain.exceptions import InvariantViolationError
from backend.domain.types import CapabilityManifestItem

L0_CAPABILITY_KEYS = {"remote_entry"}
L1_CAPABILITY_KEYS = {"runtime_registry_and_status"}
MCP_COLLAB_CAPABILITY_KEYS = {"mcp_client_collab", "mcp_collab"}
L2_PROGRAMMATIC_CAPABILITY_KEYS = {
    "programmatic_control",
    "sdk_programmatic_control",
    "http_api_control",
    "app_server_stdio",
}
L3_APPROVAL_RESUME_CAPABILITY_KEYS = {
    "approval_and_resume",
}

VERIFIED_EVIDENCE_SOURCES = {
    EvidenceSource.INTEGRATION_TEST,
    EvidenceSource.LOCAL_TEST,
    EvidenceSource.SPIKE_VERIFIED,
}

CONTROL_LEVEL_DEFINITIONS: dict[str, str] = {
    ControlLevel.L0.value: "官方 Remote 入口",
    ControlLevel.L1.value: "Runtime 注册、状态与能力信息管理",
    ControlLevel.L2.value: "程序化控制",
    ControlLevel.L3.value: "审批与恢复能力",
}


def _is_capability_truly_verified(item: CapabilityManifestItem, instance_runtime_version: str) -> bool:
    """检查单项能力是否具备完整的真实验证证据。"""
    if item.support_status != SupportStatus.SUPPORTED:
        return False
    if item.evidence not in VERIFIED_EVIDENCE_SOURCES:
        return False
    if not (item.verified_at or "").strip():
        return False
    eff_version = (item.runtime_version or "").strip()
    if not eff_version or eff_version.lower() == "unknown":
        eff_version = (instance_runtime_version or "").strip()
    if not eff_version or eff_version.lower() == "unknown":
        return False
    return True


def validate_runtime_control_and_capabilities(
    *,
    control_level: ControlLevel,
    capabilities: Sequence[CapabilityManifestItem],
    runtime_version: str = "unknown",
) -> list[CapabilityManifestItem]:
    """
    按具体能力分别校验验证证据，并校验实例声明的 control_level 是否与已验证能力严格匹配。
    返回归一化版本信息后的 CapabilityManifestItem 列表。
    """
    normalized: list[CapabilityManifestItem] = []
    clean_inst_ver = (runtime_version or "unknown").strip() or "unknown"

    for item in capabilities:
        cap_key = (item.capability or "").strip()
        if cap_key == "mcp_collab":
            cap_key = "mcp_client_collab"
        eff_ver = item.runtime_version
        if (not eff_ver or eff_ver == "unknown") and clean_inst_ver != "unknown":
            eff_ver = clean_inst_ver

        updated_item = item.model_copy(update={"capability": cap_key, "runtime_version": eff_ver})

        # 若单项能力属于 MCP 主动协同协议且声称 supported，则必须具备实测证据与已验证的协议版本列表
        if cap_key in MCP_COLLAB_CAPABILITY_KEYS and updated_item.support_status == SupportStatus.SUPPORTED:
            if not _is_capability_truly_verified(updated_item, clean_inst_ver) or not updated_item.verified_protocol_versions:
                raise InvariantViolationError(
                    problem=f"MCP 协作能力 '{cap_key}' 缺少真实验证证据或协议版本记录，禁止标为 supported",
                    cause=(
                        f"capability='{cap_key}' 声明为 supported，但 evidence='{updated_item.evidence.value}'、"
                        f"verified_at='{updated_item.verified_at or ''}' 或 verified_protocol_versions={updated_item.verified_protocol_versions} 不完整"
                    ),
                    fix=(
                        "未完成真实客户端 MCP 协同验证前请保持 support_status='unverified'；"
                        "验证通过后需记录实测 evidence、verified_at、runtime_version 以及 verified_protocol_versions（如 ['2026-07-28']）。"
                    ),
                )

        # 若单项能力属于 L2 程序化控制或 L3 审批与恢复，且声称 supported，则必须具备真实验证证据
        if (
            cap_key in (L2_PROGRAMMATIC_CAPABILITY_KEYS | L3_APPROVAL_RESUME_CAPABILITY_KEYS)
            and updated_item.support_status == SupportStatus.SUPPORTED
        ):
            if not _is_capability_truly_verified(updated_item, clean_inst_ver):
                raise InvariantViolationError(
                    problem=f"高阶控制能力 '{cap_key}' 缺少真实验证证据，禁止标为 supported",
                    cause=(
                        f"capability='{cap_key}' 声明为 supported，但 evidence='{updated_item.evidence.value}'、"
                        f"verified_at='{updated_item.verified_at or ''}' 或 runtime_version='{updated_item.runtime_version}' 不满足实测证据要求"
                    ),
                    fix=(
                        "未完成真实集成/Spike 验证前请保持 support_status='unverified'；"
                        "验证通过后需提供 evidence in ('integration_test', 'local_test', 'spike_verified')、非空 verified_at 与具体 runtime_version。"
                    ),
                )

        normalized.append(updated_item)

    has_verified_l2 = any(
        c.capability in L2_PROGRAMMATIC_CAPABILITY_KEYS
        and _is_capability_truly_verified(c, clean_inst_ver)
        for c in normalized
    )
    has_verified_l3 = any(
        c.capability in L3_APPROVAL_RESUME_CAPABILITY_KEYS
        and _is_capability_truly_verified(c, clean_inst_ver)
        for c in normalized
    )

    if control_level == ControlLevel.L2 and not has_verified_l2:
        raise InvariantViolationError(
            problem="禁止未经真实验证将 Runtime 实例标记为 L2（程序化控制）",
            cause="capabilities 中不存在已通过实测验证（supported + 实测 evidence + verified_at + 具体版本号）的 L2 程序化控制能力（programmatic_control 等）",
            fix="官方 Remote 入口仅代表 L0，Runtime 注册与状态管理代表 L1；请将 control_level 设为 L0 或 L1，或补充真实的 L2 能力验证证据。",
        )

    if control_level == ControlLevel.L3:
        if not has_verified_l2 or not has_verified_l3:
            raise InvariantViolationError(
                problem="禁止未经真实验证将 Runtime 实例标记为 L3（审批与恢复能力）",
                cause=(
                    f"声明 L3 必须同时具备已验证的 L2 程序化控制能力 (当前={has_verified_l2}) "
                    f"与 L3 审批及恢复能力 approval_and_resume (当前={has_verified_l3})；接入 SDK 不等于 L3"
                ),
                fix="仅验证程序化控制请标为 L2；若需标记 L3，必须补充 approval_and_resume 的实测验证证据。",
            )

    return normalized

