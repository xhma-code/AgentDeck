# -*- coding: utf-8 -*-
"""
领域核心值对象与安全校验器（对齐 v1.3 第 5、10、13 节及交互式 Decision 规范）。
"""

from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.domain.enums import (
    EndpointKind,
    EndpointVerifiedStatus,
    EvidenceSource,
    SupportStatus,
)
from backend.domain.exceptions import SecurityBoundaryError

SENSITIVE_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "auth",
    "auth_token",
    "api_key",
    "apikey",
    "key",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "pair_code",
    "code",
    "session_key",
    "signature",
    "sig",
}

SECRET_VALUE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{16,}|agd_[a-zA-Z0-9_-]{16,}|agmcp_[a-zA-Z0-9_-]{16,}|Bearer\s+[a-zA-Z0-9._-]{16,})",
    re.IGNORECASE,
)


class DecisionOptionItem(BaseModel):
    """交互式决策单项候选方案定义。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    option_id: str = Field(..., min_length=1, max_length=64, description="选项唯一标识（如 opt_1 / approve / reject）")
    label: str = Field(..., min_length=1, max_length=200, description="选项主标题（支持快捷键 1/2/3 映射）")
    description: str = Field(default="", max_length=600, description="选项影响与优劣说明")
    is_recommended: bool = Field(default=False, description="是否为 Agent 或系统推荐选项")


class CapabilityManifestItem(BaseModel):
    """单项 Runtime 能力诚实声明结构。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    capability: str = Field(..., min_length=1, max_length=80, description="能力标识符")
    support_status: SupportStatus = Field(default=SupportStatus.UNVERIFIED, description="支持状态")
    evidence: EvidenceSource = Field(default=EvidenceSource.OFFICIAL_DOCS, description="证据来源")
    runtime_version: str = Field(default="unknown", max_length=64, description="Runtime 版本")
    connector_version: str = Field(default="none", max_length=64, description="连接器版本")
    verified_at: Optional[str] = Field(default=None, description="验证通过时间（ISO 8601）")
    verified_protocol_versions: list[str] = Field(
        default_factory=list,
        description="已验证通过的协议版本列表（如 ['2026-07-28', '2025-11-25']）",
    )
    limitations: str = Field(default="", max_length=500, description="适用前提与能力限制说明")


class ModelEndpointDescriptor(BaseModel):
    """
    可选、非核心、非敏感的模型运行环境描述信息（v1.3 第 10 节）。
    严禁保存 Base URL、API Key、Token、账号信息或 CCSwitch 配置文件副本。
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider_label: str = Field(default="", max_length=80, description="用户填写的非敏感显示标签")
    model_label: str = Field(default="", max_length=80, description="用户填写的模型显示名称")
    endpoint_kind: EndpointKind = Field(default=EndpointKind.UNKNOWN, description="端点分类")
    configuration_owner: Literal["user_environment"] = Field(
        default="user_environment",
        description="配置所有权固定为用户本机环境，AgentDeck 不接管",
    )
    verified_status: EndpointVerifiedStatus = Field(
        default=EndpointVerifiedStatus.UNVERIFIED,
        description="在对应 Runtime 下的实测状态",
    )

    @field_validator("provider_label", "model_label")
    @classmethod
    def reject_url_or_secret_in_labels(cls, val: str) -> str:
        cleaned = (val or "").strip()
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        if lowered.startswith(("http://", "https://")) or SECRET_VALUE_PATTERN.search(cleaned):
            raise ValueError("ModelEndpointDescriptor 仅允许填写非敏感显示名称，严禁填入 Base URL 或密钥 Token")
        return cleaned


def validate_safe_remote_url(raw_url: str) -> str:
    """
    校验并清理官方 Remote 入口链接（v1.3 第 13.3 节安全边界）：
    1. 空字符串合法（表示尚未配置深链，不伪造 URL）。
    2. 严禁包含控制字符或空白字符注入。
    3. 仅允许 http / https 协议，且必须具备合法主机名与合法端口。
    4. 严禁包含用户名密码 (userinfo) 或一次性认证 Token / API Key 查询参数、路径密钥与 Fragment。
    """
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return ""

    if any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        raise SecurityBoundaryError(
            problem="Remote 入口链接包含非法空白或控制字符",
            cause="URL 中检测到空格、换行或不可见控制字符",
            fix="请提供标准格式的单行 http/https 官方 Remote 链接。",
        )

    try:
        parsed = urlparse(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise SecurityBoundaryError(
            problem="Remote 入口链接格式或端口不合法",
            cause=f"URL 解析失败: {exc}",
            fix="请检查 URL 主机名与端口号（1-65535）。",
        ) from exc

    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").strip()
    if scheme not in ("http", "https") or not hostname or "." not in hostname and hostname != "localhost":
        raise SecurityBoundaryError(
            problem="Remote 入口链接协议或主机名不合法",
            cause=f"仅支持包含有效域名或 localhost 的 http/https 链接，收到协议: '{parsed.scheme or '空'}', 主机: '{hostname or '空'}'",
            fix="请配置厂商官方标准 Remote 页面地址（禁止 javascript:/file:/ssh:/ws: 等协议）。",
        )

    if port is not None and (port < 1 or port > 65535):
        raise SecurityBoundaryError(
            problem="Remote 入口链接端口超出有效范围",
            cause=f"收到无效端口号: {port}",
            fix="请使用 1 至 65535 之间的合法端口。",
        )

    if parsed.username or parsed.password:
        raise SecurityBoundaryError(
            problem="拒绝保存包含账号密码凭证的 Remote 链接",
            cause="URL netloc 中检测到明文 userinfo 认证段",
            fix="请移除 URL 中的用户名与密码部分后再登记。",
        )

    if SECRET_VALUE_PATTERN.search(parsed.path or ""):
        raise SecurityBoundaryError(
            problem="拒绝保存路径中包含密钥特征的 Remote 链接",
            cause="URL 路径段命中敏感密钥特征规则",
            fix="请移除 URL 路径中的敏感密钥后再保存。",
        )

    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS or SECRET_VALUE_PATTERN.search(val):
            raise SecurityBoundaryError(
                problem="拒绝保存携带敏感或一次性凭证的 Remote 链接",
                cause=f"URL 查询参数 '{key}' 命中凭证脱敏拦截规则",
                fix="请仅保存不含 token / pair_code / api_key 的基础入口地址。",
            )

    if parsed.fragment:
        for key, val in parse_qsl(parsed.fragment, keep_blank_values=True):
            if key.lower() in SENSITIVE_QUERY_KEYS or SECRET_VALUE_PATTERN.search(val):
                raise SecurityBoundaryError(
                    problem="拒绝保存 Fragment 中携带敏感凭证的 Remote 链接",
                    cause=f"URL Fragment 参数 '{key}' 命中凭证脱敏拦截规则",
                    fix="请移除 URL '#' 之后的一次性认证凭证后再保存。",
                )
        if SECRET_VALUE_PATTERN.search(parsed.fragment):
            raise SecurityBoundaryError(
                problem="拒绝保存 Fragment 中疑似包含密钥的 Remote 链接",
                cause="URL Fragment 命中密钥特征规则",
                fix="请移除 URL '#' 之后的敏感令牌后再保存。",
            )

    return cleaned


def mask_remote_url_for_display(raw_url: str) -> str:
    """
    生成用于界面展示的脱敏 Remote URL 摘要（隐藏长路径尾部参数，保留协议、主机与首级入口路径）。
    """
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return ""
    try:
        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc:
            return ""
        segments = [seg for seg in (parsed.path or "").split("/") if seg]
        if not segments:
            return f"{parsed.scheme}://{parsed.netloc}/"
        if len(segments) == 1 and len(segments[0]) <= 24:
            return f"{parsed.scheme}://{parsed.netloc}/{segments[0]}"
        first_seg = segments[0][:20]
        last_tail = segments[-1][-6:] if len(segments[-1]) > 8 else segments[-1]
        return f"{parsed.scheme}://{parsed.netloc}/{first_seg}/.../{last_tail}"
    except Exception:
        return ""

