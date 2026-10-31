# -*- coding: utf-8 -*-
"""
领域核心值对象与安全校验器（对齐 v1.3 第 5、10、13 节）。

安全铁律：
1. ModelEndpointDescriptor 严禁携带 Base URL、API Key、Token、账号或 CCSwitch 配置副本。
2. RemoteEntryURL 严禁保存一次性认证凭证或查询参数密钥。
"""

from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.v2.domain.enums import (
    EndpointKind,
    EndpointVerifiedStatus,
    EvidenceSource,
    SupportStatus,
)
from backend.v2.domain.exceptions import SecurityBoundaryError

# URL 查询参数与片段中严禁落库的敏感凭证键名
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

# 疑似明文 API Key / Bearer Token 的特征正则
SECRET_VALUE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{16,}|agd_[a-zA-Z0-9_-]{16,}|agmcp_[a-zA-Z0-9_-]{16,}|Bearer\s+[a-zA-Z0-9._-]{16,})",
    re.IGNORECASE,
)


class CapabilityManifestItem(BaseModel):
    """单项 Runtime 能力诚实声明结构。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    capability: str = Field(..., min_length=1, max_length=80, description="能力标识符（如 remote_open / mcp_collab / session_create / prompt_send）")
    support_status: SupportStatus = Field(default=SupportStatus.UNVERIFIED, description="支持状态（未实测一律为 unverified）")
    evidence: EvidenceSource = Field(default=EvidenceSource.OFFICIAL_DOCS, description="证据来源")
    runtime_version: str = Field(default="unknown", max_length=64, description="验证对应的 Runtime 版本")
    connector_version: str = Field(default="none", max_length=64, description="连接器版本")
    verified_at: Optional[str] = Field(default=None, description="验证通过时间（ISO 8601）")
    limitations: str = Field(default="", max_length=500, description="适用前提与能力限制说明")


class ModelEndpointDescriptor(BaseModel):
    """
    可选、非核心、非敏感的模型运行环境描述信息（v1.3 第 10 节）。
    严禁保存 Base URL、API Key、Token、账号信息或 CCSwitch 配置文件副本。
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider_label: str = Field(default="", max_length=80, description="用户填写的非敏感显示标签（如 Official / Domestic-Compatible）")
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
    2. 仅允许 http / https 协议。
    3. 严禁包含用户名密码 (userinfo) 或一次性认证 Token / API Key 查询参数与 Fragment。
    """
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return ""

    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise SecurityBoundaryError(
            problem="Remote 入口链接格式不合法",
            cause=f"仅支持包含合法主机名的 http/https 链接，收到协议: '{parsed.scheme or '空'}'",
            fix="请配置厂商官方标准 Remote 页面地址（不含私有协议或本地命令行）。",
        )

    if parsed.username or parsed.password:
        raise SecurityBoundaryError(
            problem="拒绝保存包含账号密码凭证的 Remote 链接",
            cause="URL netloc 中检测到明文 userinfo 认证段",
            fix="请移除 URL 中的用户名与密码部分后再登记。",
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
